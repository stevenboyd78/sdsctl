#!/usr/bin/env python3
"""Observer-owned native process handles; no Ready, action or exit publication.

The fixed exec's original three actors are bound through separate Engine reads,
the private dispatch chain and actual live kernel process/namespace evidence.
Reported identities are untrusted hints, not an authenticated Ready. No installed
command/source profile selects this library, and no recording begin is enabled.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from threading import Lock, get_ident

import supplemental_recording_dispatch as dispatch
import supplemental_recording_service_app_custody as apps

engine, deadlines = apps.engine, apps.deadlines
ROLES = ("init", "guardian", "native", "watchdog")
MAX_SECONDS = 2
MESSAGE = "Independent native process custody is unconfirmed; preserve the original case."


class UnconfirmedNativeCustody(ValueError):
    """These observations cannot authorize begin, cancellation or restoration."""


def require(value):
    if not value:
        raise UnconfirmedNativeCustody(MESSAGE)


@dataclass(frozen=True)
class Binding:
    execution_id: str
    generation: str
    pins_sha256: str
    dispatch_sha256: str
    engine_sha256: str
    actors: tuple[engine.namespace.Actor, ...]


@dataclass(frozen=True)
class Status:
    apps: apps.Status
    binding: Binding
    exited: frozenset[str]


class NativeCustody:
    """One observer-side capture while helper, candidate and workers are live.

    Requires the original AppCustody (normal retained before stop, candidate
    retained before launch). Original plan/projection and fixed command pins,
    three immutable dispatch intents and two exact live exec GETs must agree.
    Kernel ancestry/namespace/UID/start-time evidence independently checks every
    reported worker identity. The retained candidate pidfd is duplicated, never
    reopened by numeric PID. Capture is once-only, including partial failure.

    This does NOT receive/authenticate Ready, prove installed source/runtime,
    authorize begin, or acquire journal/dispatcher ownership. Its already
    qualified caller must retain those independent boundaries before any action.
    No alternate time-domain proof is inferred: worker mapping currently requires
    the same host user/time namespaces, as the ordinary namespace witness does.

    poll() uses original pidfds only, even after helper/Engine loss, dispatch
    cleanup or deadline expiry. Frozen workers are not exits. App init/helper
    exit is never substituted for workers; exec return status is not inferred.
    Native success and safe recovery still need the original separate evidence,
    single-writer handoff and independently bounded observer execution.
    """

    def __init__(self, custody, pins, reported):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.closed = self.failed = False
        self._retained = self._original_retained = ()
        locked = False
        try:
            end = time.monotonic() + MAX_SECONDS
            require(type(self) is NativeCustody and type(custody) is apps.AppCustody)
            require(custody.lock.acquire(blocking=False))
            locked = True
            require(custody.native_custody_attempted is False)
            custody.native_custody_attempted = True
            self.custody, self.plan = custody, custody.plan
            self._originals = custody, self.plan
            self._capture_guard(end)
            require(type(pins) is dispatch.Pins)
            pins_raw = dispatch.binding.encode(pins.payload())
            candidate = custody._retained[1][0]
            require(pins.init == candidate.process and pins.generation == candidate.generation)
            require(
                pins.host.plan_sha256 == self.plan.sha256 and pins.host.boot_id == self.plan.boot
            )
            self.plan.check_projection(pins.host.projection)
            require(pins.command.source_sha256 == self.plan.candidate_runtime.source)
            require(pins.command.plan == str(self.plan.native_root / "launch/launch.json"))
            require(pins.command.ready_by == self.plan.deadlines.ready_by)
            directory = self.plan.root / "operator-exec"
            dispatch._location(directory, pins)
            # The read lock and retained directory bracket both inspections and
            # all kernel captures. No claim is recreated and no intent appended.
            with dispatch.binding.protected._private_directory(directory, exclusive=False) as fd:
                identity = dispatch.binding.identity(os.fstat(fd))
                state = dispatch._read(fd, pins, end)
                require(type(state) is dispatch.State and state.count == 3)
                require(state.phase == "attach_intent")
                self._capture_guard(end)
                before, inspection = self._inspect(pins, state, end)
                self._capture_guard(end)
                with apps.processes.ProcessWitness(
                    candidate.process, retained_fd=custody._retained[1][1]
                ) as init:
                    witness = engine.namespace.Observation(init, inspection.pid, reported)
                    try:
                        require(tuple(witness.handles) == ROLES)
                        require(witness.actors[0].host_pid == candidate.process.pid)
                        forbidden = {
                            self.owner[0],
                            custody.watch.target.pid,
                            custody._retained[0][0].process.pid,
                        }
                        require(
                            not forbidden.intersection(actor.host_pid for actor in witness.actors)
                        )
                        for role, actor in zip(ROLES, witness.actors, strict=True):
                            self._duplicate(role, witness.handles[role], actor.host_pid)
                        after, final = self._inspect(pins, state, end)
                        require(before == after and inspection == final)
                        require(witness.refresh() == witness.actors)
                        require(dispatch._read(fd, pins, end) == state)
                        require(dispatch.binding.identity(os.fstat(fd)) == identity)
                        require(dispatch.binding.encode(pins.payload()) == pins_raw)
                        self._capture_guard(end)
                        require(
                            not any(deadlines._readable(handle) for _, handle, _ in self._retained)
                        )
                        self.binding = Binding(
                            state.execution_id,
                            candidate.generation,
                            dispatch.binding.checksum(pins.payload()),
                            state.sha256,
                            dispatch.binding.checksum(before),
                            witness.actors,
                        )
                        self._binding = self.binding
                        self._receipt = dispatch.binding.encode(asdict(self.binding))
                    finally:
                        witness.close()
            require(time.monotonic() < end)
        except BaseException as error:
            self.close()
            self._fail(error)
        finally:
            if locked:
                custody.lock.release()

    def _capture_guard(self, end):
        self.custody._capture_guard(end)
        require(self.custody.native_custody_attempted is True)
        require(len(self.custody._retained) == 2)
        require(deadlines._readable(self.custody._retained[0][1]))
        require(not deadlines._readable(self.custody._retained[1][1]))
        require(self.plan is self.custody.plan)

    def _inspect(self, pins, state, end):
        self._capture_guard(end)
        value = engine._json_request(
            self.custody.endpoint,
            "GET",
            f"/exec/{state.execution_id}/json",
            None,
            200,
            deadline=end,
        )
        inspection = dispatch.execution.inspect(
            value,
            execution_id=state.execution_id,
            container_id=pins.init.container_id,
            command=pins.command,
        )
        require(inspection.phase == "running")
        self._capture_guard(end)
        return value, inspection

    def _duplicate(self, role, source, pid):
        fd = os.dup(source)
        try:
            identity = deadlines._identity(fd)
        except BaseException:
            os.close(fd)
            raise
        self._retained += ((role, fd, identity),)
        self._original_retained = self._retained
        require(not os.get_inheritable(fd) and not deadlines._readable(fd))
        require(deadlines._fdinfo(fd).get(b"Pid") == str(pid).encode("ascii"))

    def _guard(self):
        require(type(self) is NativeCustody and not self.closed and not self.failed)
        require(self.owner == (os.getpid(), get_ident()))
        require(self.custody is self._originals[0] and self.plan is self._originals[1])
        require(self.custody.plan is self.plan and self.custody.native_custody_attempted is True)
        require(self.binding is self._binding and type(self.binding) is Binding)
        require(dispatch.binding.encode(asdict(self.binding)) == self._receipt)
        require(self._retained is self._original_retained)
        require(tuple(role for role, _, _ in self._retained) == ROLES)
        for _, fd, identity in self._retained:
            require(deadlines._identity(fd) == identity and not os.get_inheritable(fd))
        return self.custody.poll()

    def poll(self):
        acquired = False
        try:
            end = time.monotonic() + MAX_SECONDS
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._guard()
            exited = frozenset(role for role, fd, _ in self._retained if deadlines._readable(fd))
            status = self._guard()
            require(time.monotonic() < end)
            return Status(status, self.binding, exited)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedNativeCustody(MESSAGE) from None

    def close(self):
        """Release only our duplicates; borrowed App/deadline custody survives."""
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        problem = None
        for _, fd, identity in reversed(self._original_retained):
            try:
                require(deadlines._identity(fd) == identity)
                os.close(fd)
            except Exception as error:
                problem = error
        if problem is not None:
            self._fail(problem)


if __name__ == "__main__":
    raise SystemExit("Read-only native process custody; no active controller is enabled.")
