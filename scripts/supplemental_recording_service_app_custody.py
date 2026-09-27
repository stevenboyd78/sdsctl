#!/usr/bin/env python3
"""Observer-owned original App init pidfds; no dispatch or journal ownership.

Capture the normal App before stopping it, then the actual candidate before
native launch. Both reads use the observer's separately authenticated Engine
endpoint. Polling survives helper/Engine loss without reconstructing processes
from numeric PIDs. No installed command or source profile selects this library.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import asdict, dataclass
from threading import Lock, get_ident

import supplemental_handoff_host as platform
import supplemental_handoff_process as processes
import supplemental_recording_engine as engine
import supplemental_recording_service_deadline as deadlines

NORMAL, CANDIDATE = platform.NORMAL, platform.CANDIDATE
MAX_SECONDS = 2
NAMESPACES = ("pid", "pid_for_children", "cgroup", "mnt")
MESSAGE = "Independent App process custody is unconfirmed; preserve the original case."


class UnconfirmedCustody(ValueError):
    """Process exit alone is not native exit, App restoration or permission."""


def require(value):
    if not value:
        raise UnconfirmedCustody(MESSAGE)


@dataclass(frozen=True)
class Binding:
    slug: str
    generation: str
    process: processes.ProcessIdentity


@dataclass(frozen=True)
class Status:
    deadline: deadlines.Status
    normal: Binding
    normal_exited: bool
    candidate: Binding | None
    candidate_exited: bool | None  # None means NOT captured, never exited.
    capture_failed: bool


def _endpoint(endpoint):
    """Pin a previously authenticated endpoint, never learn a new peer here."""
    require(type(endpoint) is engine.Endpoint)
    endpoint.check()
    sender = endpoint.sender
    if sender is None:
        require(endpoint.peer is not None and endpoint.peer_fd >= 0)
        peer = ("peer", endpoint.peer, endpoint.peer_fd)
    else:
        require(type(sender) is engine.senders.Sender)
        require(sender.creator is not None and sender.peer is not None)
        peer = ("sender", sender, sender.creator, sender.peer, sender.creator_fd, sender.peer_fd)
    return endpoint.parent, endpoint.parent_identity, endpoint.socket_identity, peer


class AppCustody:
    """Read-only original init-process custody in the independent observer.

    The caller must independently qualify this observer, its host PID/cgroup
    view and the separate Engine endpoint before construction. Kernel credentials
    authenticate transport, not full source/runtime/configuration or action
    consent. The original DeadlineWatch is borrowed, never reset or closed.

    Construction consumes its one App-capture slot and brackets the original
    normal generation in the plan around a real live pidfd capture. A single
    capture_candidate(expected_generation) is allowed only after that retained
    normal pidfd is readable, while the helper and ORIGINAL readiness deadline
    remain live. The candidate generation is independently checked twice, never
    inferred from the helper's claim or Docker absence. No new custody can be
    acquired after helper loss. Failed candidate capture is not retryable, but
    does not discard previously retained normal evidence.

    poll() reads retained kernel handles only: no Engine requests, helper calls,
    /proc/PID reopening, journal events or signals. It remains available after
    helper/Engine loss and after the hard deadline. These are observations only;
    original init exit does NOT establish worker exit, no replacement App,
    recording completion, restoration, or ownership of the journal/dispatcher.
    A separately bounded observer and single-writer recovery are still required.
    """

    def __init__(self, watch, endpoint):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.closed = self.failed = self.capture_failed = False
        self.candidate_attempted = False
        self.native_custody_attempted = False
        self.cli_custody_attempted = False
        self._retained = ()
        self._original_retained = self._retained
        self._receipts = ()
        self._namespaces = ()
        self._original_namespaces = self._namespaces
        try:
            end = time.monotonic() + MAX_SECONDS
            require(type(self) is AppCustody and type(watch) is deadlines.DeadlineWatch)
            require(watch.poll() == deadlines.Status(False, False))
            require(watch.app_custody_attempted is False)
            watch.app_custody_attempted = True
            self.watch, self.endpoint, self.plan = watch, endpoint, watch.plan
            self._originals = watch, endpoint, self.plan
            # Hold the original namespace inodes, not just numeric identities
            # which could recycle after entering a different namespace. These
            # checks retain an already qualified view; they do not prove host
            # provenance by comparing against a potentially namespaced PID1.
            for name in NAMESPACES:
                fd = os.open(f"/proc/self/ns/{name}", os.O_RDONLY | os.O_CLOEXEC)
                try:
                    identity = deadlines._identity(fd)
                    require(stat.S_ISREG(identity[2]) and not os.get_inheritable(fd))
                except BaseException:
                    os.close(fd)
                    raise
                self._namespaces += ((name, fd, identity),)
                self._original_namespaces = self._namespaces
            require(self._namespaces[0][2] == self._namespaces[1][2])
            self.endpoint_pin = _endpoint(endpoint)
            self._endpoint_pin = self.endpoint_pin
            self._capture(NORMAL, self.plan.normal_generation, end)
            self.poll()
            require(time.monotonic() < end)
        except BaseException as error:
            self.close()
            self._fail(error)

    def _guard(self):
        require(type(self) is AppCustody and not self.closed and not self.failed)
        require(self.owner == (os.getpid(), get_ident()))
        require(
            all(
                a is b
                for a, b in zip(
                    (self.watch, self.endpoint, self.plan), self._originals, strict=True
                )
            )
        )
        require(self.watch.plan is self.plan and self.watch.app_custody_attempted is True)
        require(self.endpoint_pin is self._endpoint_pin)
        require(self._namespaces is self._original_namespaces)
        require(tuple(name for name, _, _ in self._namespaces) == NAMESPACES)
        for name, fd, identity in self._namespaces:
            require(deadlines._identity(fd) == identity and not os.get_inheritable(fd))
            observed = os.stat(f"/proc/self/ns/{name}")
            require((observed.st_dev, observed.st_ino, observed.st_mode) == identity)
        require(self._retained is self._original_retained)
        require(len(self._retained) == len(self._receipts) <= 2)
        require(
            tuple(b.slug for b, _, _ in self._retained)
            == (NORMAL, CANDIDATE)[: len(self._retained)]
        )
        for (binding, fd, identity), (original, raw) in zip(
            self._retained, self._receipts, strict=True
        ):
            require(type(binding) is Binding and binding is original)
            require(platform.encode(asdict(binding)) == raw)
            require(deadlines._identity(fd) == identity and not os.get_inheritable(fd))
        return self.watch.poll()

    def _capture_guard(self, end):
        require(time.monotonic() < end)
        status = self._guard()
        require(status == deadlines.Status(False, False))
        require(not self.capture_failed)
        require(
            time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1_000_000_000
            < self.plan.deadlines.ready_by
        )
        require(_endpoint(self.endpoint) == self.endpoint_pin)
        require(time.monotonic() < end)

    def _inspect(self, slug, expected, end):
        self._capture_guard(end)
        value = engine._json_request(
            self.endpoint, "GET", f"/containers/app_{slug}/json", None, 200, deadline=end
        )
        image = self.plan.normal.image if slug == NORMAL else self.plan.candidate.image
        require(platform.generation(value, name="app_" + slug, image=image) == expected)
        self._capture_guard(end)
        return value

    def _capture(self, slug, expected, end):
        platform.digest(expected)
        before = self._inspect(slug, expected, end)
        identity = processes.read_identity(before["State"]["Pid"], before["Id"])
        require(identity.pid not in (self.owner[0], self.watch.target.pid))
        require(identity.container_id != self.watch.target.container_id)
        require(all(identity.pid != binding.process.pid for binding, _, _ in self._retained))
        require(
            all(
                identity.container_id != binding.process.container_id
                for binding, _, _ in self._retained
            )
        )
        witness, fd, fd_identity = None, -1, None
        try:
            witness = processes.ProcessWitness(identity)
            fd = os.dup(witness.fd)
            try:
                fd_identity = deadlines._identity(fd)
            except BaseException:
                # This is the descriptor just returned by dup, not an adopted
                # external number. Even failed first metadata acquisition must
                # release it; later cleanup uses the established identity.
                os.close(fd)
                fd = -1
                raise
            require(not os.get_inheritable(fd) and not deadlines._readable(fd))
            require(deadlines._fdinfo(fd).get(b"Pid") == str(identity.pid).encode("ascii"))
            after = self._inspect(slug, expected, end)
            require(processes.read_identity(after["State"]["Pid"], after["Id"]) == identity)
            self._capture_guard(end)
            require(not witness.exited() and not deadlines._readable(fd))
            require(deadlines._fdinfo(fd).get(b"Pid") == str(identity.pid).encode("ascii"))
            binding = Binding(slug, expected, identity)
            raw = platform.encode(asdict(binding))
            require(time.monotonic() < end)
            self._retained += ((binding, fd, fd_identity),)
            self._original_retained = self._retained
            self._receipts += ((binding, raw),)
            fd = -1
            return binding
        finally:
            try:
                if witness is not None:
                    witness.close()
            finally:
                if fd >= 0:
                    require(deadlines._identity(fd) == fd_identity)
                    os.close(fd)

    def capture_candidate(self, expected_generation):
        """One pre-native capture, never a candidate start or action permit."""
        acquired = False
        try:
            end = time.monotonic() + MAX_SECONDS
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._capture_guard(end)
            require(not self.candidate_attempted and len(self._retained) == 1)
            self.candidate_attempted = True
            require(deadlines._readable(self._retained[0][1]))
            return self._capture(CANDIDATE, expected_generation, end)
        except BaseException as error:
            # Failed/uncertain acquisition must not make the original normal
            # pidfd unusable. No further capture is allowed; poll remains read-only.
            self.capture_failed = True
            self._fail(error, poison=False)
        finally:
            if acquired:
                self.lock.release()

    def poll(self):
        acquired = False
        try:
            end = time.monotonic() + MAX_SECONDS
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._guard()
            require(len(self._retained) in (1, 2))
            exited = tuple(deadlines._readable(fd) for _, fd, _ in self._retained)
            status = self._guard()
            require(time.monotonic() < end)
            return Status(
                status,
                self._retained[0][0],
                exited[0],
                self._retained[1][0] if len(exited) == 2 else None,
                exited[1] if len(exited) == 2 else None,
                self.capture_failed,
            )
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error, *, poison=True):
        if poison:
            self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedCustody(MESSAGE) from None

    def close(self):
        """Close only our original pidfds, never the borrowed watch/endpoint."""
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        problem = None
        for _, fd, identity in reversed((*self._original_namespaces, *self._original_retained)):
            try:
                require(deadlines._identity(fd) == identity)
                os.close(fd)
            except Exception as error:
                problem = error
        if problem is not None:
            self._fail(problem)


if __name__ == "__main__":
    raise SystemExit("Read-only original App process custody; no active controller is enabled.")
