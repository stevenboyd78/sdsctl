#!/usr/bin/env python3
"""Uninstalled outer peer termination, NOT an active App launcher or recovery.

An explicit caller first captures both original qualified peers and zero-offset
domains. Arming separately requires the narrow termination scope below. A forked
kernel-handle-only watcher then stops both on original deadline, either peer's
loss, parent loss or cancellation. No Engine request, process discovery, journal
write, scanner command, native-exit claim or App restoration is provided.

The outer runtime/proc/image and fixed launch topology must be independently
qualified before installation. This module is outside all observed source sets;
no existing entrypoint, preflight permission or passive command selects it.
"""

from __future__ import annotations

import os
import select
import signal
import time
from contextlib import suppress
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from threading import current_thread, get_ident, main_thread

import qualify_supplemental_recording_peer_runtime as peers
import supplemental_recording_service_deadline as deadlines
import supplemental_recording_watchdog as cleanup

SCOPE = "terminate-original-recording-peers-only-v1"
MESSAGE = "Original recording peer termination is unconfirmed; preserve the case and do not retry."
CAPTURE_SECONDS = 2.0
RETIRE_SECONDS = 1.0


class UnconfirmedTermination(ValueError):
    """Signals and watcher returns never establish recording or App success."""


def require(value):
    if not value:
        raise UnconfirmedTermination(MESSAGE)


def _kill_all(fds):
    # Attempt BOTH independently, even if one signal fails. Never fall back to
    # kill(PID), process groups, container names, discovery or a replacement.
    okay = True
    for fd in fds:
        try:
            if not deadlines._readable(fd):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            try:
                require(deadlines._readable(fd))
            except BaseException:
                okay = False
        except BaseException:
            okay = False
    return okay


def _child(owned, targets, parent, timer, cancel, ready):
    """Fixed child: retained kernel handles only, no arbitrary code callbacks.

    Deadline has already been armed in CLOCK_BOOTTIME, so neither a suspended
    host nor a blocked/frozen writer or observer can renew it. SIGKILL is used
    directly: there is no post-deadline grace period. A kernel task stuck in
    uninterruptible I/O can still delay actual exit; signal delivery is not exit.
    """
    try:
        # Do not run an inherited Python application signal handler in this
        # kernel-only child. SIGPIPE must raise instead of bypassing cleanup.
        for signum in signal.valid_signals() - {signal.SIGKILL, signal.SIGSTOP}:
            signal.signal(signum, signal.SIG_DFL)
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_SETMASK, set())
        cleanup._close_unrelated(set(owned) | {parent, cancel, ready})
        require(not any(deadlines._readable(fd) for fd in (*targets, parent, timer, cancel)))
        require(os.write(ready, b"1") == 1)
        os.close(ready)
        poller = select.poll()
        for fd in (*targets, parent, timer, cancel):
            poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        while True:
            events = poller.poll()  # Kernel absolute timer, not a relative sleep.
            require(
                events
                and all(not flags & (select.POLLNVAL | select.POLLERR) for _, flags in events)
            )
            exited = tuple(deadlines._readable(fd) for fd in targets)
            if all(exited):
                return 0  # Both peer exits, not native exit or successful work.
            active = {fd for fd, _ in events}
            cause = (
                12
                if parent in active
                else 13
                if cancel in active
                else 10
                if timer in active
                else 11
                if any(exited)
                else None
            )
            require(cause is not None)
            return cause if _kill_all(targets) else 70
    except BaseException:
        _kill_all(targets)
        return 70


class Custody:
    """Read-only one-use capture; owns duplicates, never borrowed witnesses.

    Construction proves both full runtime comparisons plus BOTH live domains
    inside one original two-second window. Namespace and pidfd handles and the
    absolute recovery timer are retained before loss. No new clock is created.
    Capture alone does not fork, signal, grant active action scope or run a peer.
    """

    def __init__(self, pair, clock, writer_domain, observer_domain):
        self.owner = os.getpid(), get_ident()
        self.closed = self.attempted = False
        self._owned = []
        self._resources = None
        try:
            require(type(self) is Custody and type(pair) is peers.PeerRuntimePair)
            require(pair.termination_capture_attempted is False)
            pair.termination_capture_attempted = True
            require(type(clock) is deadlines.links.plans.clock.ClockWitness)
            require(type(writer_domain) is deadlines.links.domains.ZeroDomain)
            require(type(observer_domain) is deadlines.links.domains.ZeroDomain)
            require(writer_domain is not observer_domain)
            require(deadlines.timerfd_available())
            self.pair, self.clock = pair, clock
            self.domains = writer_domain, observer_domain
            self.plan = pair.writer.plan
            self.origin = clock.original
            # The OUTER owner has its own continuing original clock, not the
            # writer's decoded Window object. Never reconstruct or relabel it.
            require(self.origin.boot == self.plan.boot)
            require(self.origin.namespace == self.plan.original_clock.namespace)
            self.pin = peers.launch.plans.PinnedPlan(self.plan)
            self.originals = pair, clock, self.domains, self.plan, self.pin, self.origin
            self.proofs = tuple(domain.evidence for domain in self.domains)
            self.proof_hashes = tuple(proof.sha256 for proof in self.proofs)
            end = min(time.monotonic() + CAPTURE_SECONDS, self.plan.lease["ready_by"])
            self._live(end)
            pair._collect_before(end)
            self._live(end)
            self.targets = tuple(self._dup(role.fd) for role in (pair.writer, pair.observer))
            self.identities = pair.writer.init, pair.observer.init
            for fd, target in zip(self.targets, self.identities, strict=True):
                require(deadlines._fdinfo(fd).get(b"Pid") == str(target.pid).encode("ascii"))
            self.host_namespace = self._dup(clock.fd)
            self.namespaces = tuple(
                self._dup(domain.handles[target.pid][0])
                for domain, target in zip(self.domains, self.identities, strict=True)
            )
            require(deadlines._identity(self.host_namespace)[:2] == self.origin.namespace)
            for fd, proof in zip(self.namespaces, self.proofs, strict=True):
                require(deadlines._identity(fd)[:2] == proof.native_time)
            self.deadline_ns = int(
                (
                    Decimal(self.plan.deadlines.recover_by) * peers.launch.plans.clock.NS
                ).to_integral_value(rounding=ROUND_FLOOR)
            )
            self.timer = self._own(
                os.timerfd_create(time.CLOCK_BOOTTIME, flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK)
            )
            require(time.clock_gettime_ns(time.CLOCK_BOOTTIME) < self.deadline_ns)
            os.timerfd_settime_ns(
                self.timer, flags=os.TFD_TIMER_ABSTIME, initial=self.deadline_ns, interval=0
            )
            self._live(end)
            self._resources = tuple(self._owned)
            self.values = (
                self.targets,
                self.identities,
                self.host_namespace,
                self.namespaces,
                self.deadline_ns,
                self.timer,
            )
            self._guard()
            require(time.monotonic() < end)
        except BaseException as error:
            self.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedTermination(MESSAGE) from None

    def _own(self, fd):
        try:
            identity = deadlines._identity(fd)
        except BaseException:
            os.close(fd)
            raise
        self._owned.append((fd, identity))
        require(not os.get_inheritable(fd))
        return fd

    def _dup(self, fd):
        return self._own(os.dup(fd))

    def _live(self, end):
        require(self.owner == (os.getpid(), get_ident()) and not self.closed)
        require(
            all(
                a is b
                for a, b in zip(
                    (self.pair, self.clock, self.domains, self.plan, self.pin, self.origin),
                    self.originals,
                    strict=True,
                )
            )
        )
        require(self.pair.termination_capture_attempted is True)
        self.pin.check(self.plan)
        self.pair._guard(end)
        require(self.clock.original is self.origin)
        self.plan.check_clock(self.clock.read())
        for role, domain, proof, digest in zip(
            (self.pair.writer, self.pair.observer),
            self.domains,
            self.proofs,
            self.proof_hashes,
            strict=True,
        ):
            require(domain.refresh() is proof and proof.sha256 == digest)
            require(proof.init is role.init and proof.original_clock is self.clock.original)
            require(proof.host_time == self.plan.original_clock.namespace)
            require(
                domain.pidfd >= 0
                and deadlines._fdinfo(domain.pidfd).get(b"Pid")
                == str(role.init.pid).encode("ascii")
            )
        self.pair._guard(end)
        require(time.monotonic() < end)

    def _guard(self):
        require(
            type(self) is Custody and self.owner == (os.getpid(), get_ident()) and not self.closed
        )
        require(self._resources is not None and tuple(self._owned) == self._resources)
        require(
            (
                self.targets,
                self.identities,
                self.host_namespace,
                self.namespaces,
                self.deadline_ns,
                self.timer,
            )
            == self.values
        )
        self.pin.check(self.plan)
        for fd, identity in self._resources:
            require(deadlines._identity(fd) == identity and not os.get_inheritable(fd))
        deadlines._timer_metadata(self.timer)
        before = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        remaining, interval = os.timerfd_gettime_ns(self.timer)
        after = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        require(before <= after and interval == 0)
        require(
            before <= self.deadline_ns - remaining <= after
            if remaining
            else after >= self.deadline_ns and deadlines._readable(self.timer)
        )
        for name in ("time", "time_for_children"):
            info = os.stat(f"/proc/self/ns/{name}")
            require((info.st_dev, info.st_ino) == self.plan.original_clock.namespace)

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        problem = False
        for fd, identity in reversed(
            self._resources if self._resources is not None else self._owned
        ):
            try:
                require(deadlines._identity(fd) == identity)
                os.close(fd)
            except Exception:
                problem = True
        require(not problem)


@dataclass(frozen=True)
class Outcome:
    writer: deadlines.links.domains.process.ProcessIdentity
    observer: deadlines.links.domains.process.ProcessIdentity
    deadline_ns: int
    returncode: int


class Watch:
    """Owned watcher; close cancels, never disarms or grants recovery authority.

    finish() is non-blocking and one-use. It reaps only this watcher. Callers
    must independently observe original peer AND native/App exits; these return
    codes describe reasons for stopping and never successful recording work.
    """

    def __init__(self, pid, fd, cancel, targets, identities, deadline_ns):
        self.owner = os.getpid(), get_ident()
        self.pid, self.fd, self.cancel, self.targets = pid, fd, cancel, targets
        self.identities, self.deadline_ns = identities, deadline_ns
        self.resources = tuple((f, deadlines._identity(f)) for f in (fd, cancel, *targets))
        self.original = pid, fd, cancel, targets, identities, deadline_ns
        self.closed = self.finished = False

    def _guard(self):
        require(
            type(self) is Watch and self.owner == (os.getpid(), get_ident()) and not self.closed
        )
        require(
            (self.pid, self.fd, self.cancel, self.targets, self.identities, self.deadline_ns)
            == self.original
        )
        for fd, identity in self.resources:
            require(deadlines._identity(fd) == identity and not os.get_inheritable(fd))

    def finish(self):
        try:
            self._guard()
            require(not self.finished and deadlines._readable(self.fd))
            self.finished = True
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            require(pid == self.pid)
            code = os.waitstatus_to_exitcode(status)
            require(code in (0, 10, 11, 12, 13))
            return Outcome(*self.identities, self.deadline_ns, code)
        except Exception:
            self._stop_originals()
            raise UnconfirmedTermination(MESSAGE) from None

    def _stop_originals(self):
        # Retain the original descriptor identities even if a public attribute
        # is corrupted. A foreign reused descriptor must never be signaled.
        # A missing first handle must not skip the second original peer.
        if self.owner != (os.getpid(), get_ident()):
            return
        for fd in self.original[3]:
            try:
                identity = next(
                    identity for original_fd, identity in self.resources if original_fd == fd
                )
                require(deadlines._identity(fd) == identity)
                _kill_all((fd,))
            except Exception:
                pass  # Caller reports uncertainty; no replacement is adopted.

    def _retire_after_failure(self):
        # A corrupt public attribute or lost target handle must not orphan the
        # separately owned watcher. Use only the original, still-matching fds.
        # Failure stays uncertainty even if its original child can be reaped.
        pid, fd, cancel = self.original[:3]
        identities = dict(self.resources)
        with suppress(Exception):
            require(deadlines._identity(cancel) == identities[cancel])
            os.write(cancel, b"X")
        with suppress(Exception):
            require(deadlines._identity(fd) == identities[fd])
            if not select.select([fd], [], [], RETIRE_SECONDS)[0]:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            if select.select([fd], [], [], RETIRE_SECONDS)[0] and not self.finished:
                self.finished = True
                require(os.waitpid(pid, os.WNOHANG)[0] == pid)

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        problem = False
        try:
            self._guard()
            # Any byte is cancellation. A separate parent pidfd also covers
            # parent death if an unrelated child inherited this pipe writer.
            with suppress(BrokenPipeError):
                require(os.write(self.cancel, b"X") == 1)
            if not select.select([self.fd], [], [], RETIRE_SECONDS)[0]:
                _kill_all(self.targets)
                signal.pidfd_send_signal(self.fd, signal.SIGKILL)
                require(select.select([self.fd], [], [], RETIRE_SECONDS)[0])
                problem = True
            if not self.finished:
                self.finish()
        except Exception:
            self._stop_originals()
            self._retire_after_failure()
            problem = True
        finally:
            self.closed = True
            for fd, identity in reversed(self.resources):
                try:
                    require(deadlines._identity(fd) == identity)
                    os.close(fd)
                except Exception:
                    problem = True
        require(not problem)


def arm(custody, *, scope):
    """Explicit peer-stop scope only, never inferred from runtime comparison.

    Fork from a single-threaded qualified outer process BEFORE releasing the
    peers' action gate. No callbacks or arbitrary command/PID/deadline accepted.
    The scope string selects operations; it is NOT authenticated user consent.
    Once original custody is committed, arming failure stops both exact peers.
    Their original owners still retain every evidence file and reap their own
    children. This function cannot launch the peers or admit App mutations.
    """
    owned, targets = [], ()
    pid = watch_fd = None
    committed = False
    try:
        require(type(custody) is Custody)
        custody._guard()
        require(not custody.attempted)
        custody.attempted = True
        require(type(scope) is str and scope == SCOPE)
        require(current_thread() is main_thread() and len(os.listdir("/proc/self/task")) == 1)
        require(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL)
        end = min(time.monotonic() + CAPTURE_SECONDS, custody.plan.lease["ready_by"])
        custody._live(end)
        custody.pair._collect_before(end)
        custody._live(end)
        custody._guard()

        def own(fd):
            owned.append(fd)
            return fd

        targets = tuple(own(os.dup(fd)) for fd in custody.targets)
        committed = True
        for fd in targets:
            # Permission/liveness probe, not termination or a proof that a
            # later SIGKILL will produce immediate kernel exit.
            signal.pidfd_send_signal(fd, 0)
        parent = own(os.pidfd_open(os.getpid()))
        read, write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        owned.extend((read, write))
        ready, send = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        owned.extend((ready, send))
        require(time.monotonic() < end)
        pid = os.fork()
        if pid == 0:
            os._exit(
                _child(
                    tuple(fd for fd, _ in custody._resources) + targets,
                    targets,
                    parent,
                    custody.timer,
                    read,
                    send,
                )
            )
        watch_fd = own(os.pidfd_open(pid))
        os.close(send)
        owned.remove(send)
        require(
            select.select([ready, watch_fd], [], [], min(1, max(0, end - time.monotonic())))[0]
            == [ready]
        )
        require(os.read(ready, 2) == b"1")
        custody._live(end)
        custody._guard()
        require(not deadlines._readable(watch_fd) and time.monotonic() < end)
        result = Watch(pid, watch_fd, write, targets, custody.identities, custody.deadline_ns)
        for fd in (watch_fd, write, *targets):
            owned.remove(fd)
        return result
    except BaseException as error:
        if committed:
            _kill_all(targets)
        if pid is not None and pid > 0 and watch_fd is not None:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(watch_fd, signal.SIGKILL)
            if select.select([watch_fd], [], [], RETIRE_SECONDS)[0]:
                os.waitpid(pid, os.WNOHANG)
        elif pid is not None and pid > 0:
            # No pidfd was acquired for our NEW fork child. Reap that owned
            # child, never reopen its PID for signaling. Its existing original
            # target handles notice our stops; pipe EOF also cancels it.
            finish_by = time.monotonic() + RETIRE_SECONDS
            while os.waitpid(pid, os.WNOHANG)[0] == 0 and time.monotonic() < finish_by:
                select.select([], [], [], 0.01)
        if not isinstance(error, Exception):
            raise
        raise UnconfirmedTermination(MESSAGE) from None
    finally:
        for fd in reversed(owned):
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Uninstalled original-peer termination library; no active launcher enabled.")
