#!/usr/bin/env python3
"""Retained exact operator exits after a lost/failed return, not success.

Capture while the actual original Ready is live, before begin. An independently
authenticated Engine endpoint and duplicated original pidfds survive closure of
the recording transport. No process discovery, signals, replay, recording-file
inference, journal promotion or App action. Installed recovery supervision and
source/protection qualification remain separate, required responsibilities.
"""

from __future__ import annotations

import hashlib
import os
import select
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from threading import get_ident

import supplemental_recording_host_plan as plans
import supplemental_recording_ready as received

engine = received.engine
dispatch = engine.dispatch
ROLES = ("init", "guardian", "native", "watchdog")
MESSAGE = "Operator exit reconciliation is unconfirmed; retain this case and do not retry."


class UnconfirmedReconciliation(ValueError):
    """A missing return, EOF, init exit or Engine result alone is insufficient."""


def require(value):
    if not value:
        raise UnconfirmedReconciliation(MESSAGE)


def _peer(endpoint):
    endpoint.check()
    if endpoint.sender is None:
        require(endpoint.peer is not None)
        return ("peer", endpoint.peer)
    require(endpoint.sender.peer is not None and endpoint.sender.creator is not None)
    return ("sender", endpoint.sender.creator, endpoint.sender.peer)


@dataclass(frozen=True)
class Evidence:
    """Exit facts only. Even returncode0 is NOT native recording completion."""

    execution_id: str
    container_id: str
    generation: str
    plan_sha256: str
    ready_sha256: str
    actors_sha256: str
    dispatch_sha256: str
    engine_sha256: str
    returncode: int
    init_exited: bool
    observed_at: float  # Original host BOOTTIME domain.

    @property
    def sha256(self):
        return dispatch.binding.checksum(asdict(self))


class Operator:
    """Own four duplicated original pidfds; borrow a separate Engine endpoint.

    The endpoint must already be supplied by the trusted host adapter, distinct
    from the recording Client's endpoint. An actual running inspection binds it
    to the SAME Engine peer before capture returns. Once captured, Ready may be
    failed/closed without destroying these independent handles. This remains a
    same-process object, not a cross-process recovery service or fd-transfer API.

    poll() returns None for still-live workers, or one exact terminal Evidence.
    Init exit is reported separately, never substituted for worker exit. No
    native success is asserted, even for Engine0. Uncertainty poisons this object
    without releasing evidence; explicit close() only releases its own handles.
    Partial constructor failure releases only its duplicates; the original Ready
    remains caller-owned with its original handles intact.
    The caller owns the endpoint throughout, including partial capture failure.
    """

    def __init__(self, plan, ready, endpoint):
        self.owner = os.getpid(), get_ident()
        self.handles, self.identities = {}, {}
        self.closed = self.failed = self.done = False
        try:
            require(type(plan) is plans.Plan and type(ready) is received.Ready)
            require(plans.load_bytes(plan.raw, plan.sha256) == plan)
            require(type(endpoint) is engine.Endpoint and endpoint is not ready.client.endpoint)
            ready.check_before_begin()
            self.plan, self.endpoint = plan, endpoint
            self.pins, self.state = ready.client.claim.pins, ready.client.claim.state
            self.clock = plan.original_clock
            require(ready.clock == self.clock and self.pins.host.boot_id == plan.boot)
            require(self.pins.host.plan_sha256 == plan.sha256)
            plan.check_projection(self.pins.host.projection)
            require(self.pins.command.source_sha256 == plan.candidate_runtime.source)
            require(self.pins.command.plan == str(plan.native_root / "launch/launch.json"))
            require(self.pins.command.ready_by == plan.lease["ready_by"] == ready.ready_by)
            self.directory, self.directory_identity = (
                ready.client.claim.directory,
                ready.client.claim.identity,
            )
            require(self.directory == plan.root / "operator-exec")
            require(self.state.count == 3 and self.state.phase == "attach_intent")
            self.execution_id = self.state.execution_id
            self.ready_sha256 = hashlib.sha256(ready.ready_raw).hexdigest()
            self.actors = ready.processes.actors
            require(tuple(ready.processes.handles) == ROLES)
            self.host_domains = ready.processes.host_user, ready.processes.host_time
            for role in ROLES:
                fd = os.dup(ready.processes.handles[role])
                self.handles[role] = fd
                self.identities[role] = dispatch.binding.identity(os.fstat(fd))
                engine._alive(fd)
            now = self._clock()
            inspected = self._inspect(min(time.monotonic() + 2, ready.ready_by))
            require(inspected[1].phase == "running")
            require(inspected[1].pid == self.actors[1].host_pid)
            require(
                endpoint.parent_identity == ready.client.endpoint.parent_identity
                and endpoint.socket_identity == ready.client.endpoint.socket_identity
            )
            self.peer = _peer(endpoint)
            require(self.peer == _peer(ready.client.endpoint))
            ready.check_before_begin()
            require(self._actors() == frozenset())
            self._history(min(time.monotonic() + 2, ready.ready_by))
            require(self._clock() - now <= 2)
        except BaseException as error:
            with suppress(Exception):
                self.close()
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedReconciliation(MESSAGE) from None

    def _clock(self):
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        return observed.boottime_ns / plans.clock.NS

    def _history(self, end):
        dispatch._location(self.directory, self.pins)
        with dispatch.binding.protected._private_directory(self.directory, exclusive=False) as fd:
            require(dispatch.binding.identity(os.fstat(fd))[:6] == self.directory_identity)
            require(dispatch._read(fd, self.pins, end) == self.state)
        require(time.monotonic() < end)

    def _exited(self, role):
        fd = self.handles[role]
        require(dispatch.binding.identity(os.fstat(fd)) == self.identities[role])
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        events = poller.poll(0)
        if not events:
            return False
        require(len(events) == 1 and events[0][0] == fd)
        require(events[0][1] in (select.POLLIN, select.POLLIN | select.POLLHUP))
        return True

    def _actors(self):
        require(tuple(self.handles) == tuple(self.identities) == ROLES)
        require(engine.namespace.Witness._host_domains() == self.host_domains)
        exited = set()
        for role, actor in zip(ROLES, self.actors, strict=True):
            if self._exited(role):
                exited.add(role)
                continue
            try:
                current = engine.namespace.read(actor.host_pid, actor.container_id)
            except Exception:
                require(self._exited(role))  # Missing proc data alone proves nothing.
                exited.add(role)
            else:
                require(current == actor)
                if self._exited(role):
                    exited.add(role)
        require(engine.namespace.Witness._host_domains() == self.host_domains)
        return frozenset(exited)

    def _inspect(self, end):
        raw = engine._json_request(
            self.endpoint, "GET", f"/exec/{self.execution_id}/json", None, 200, deadline=end
        )
        return raw, dispatch.execution.inspect(
            raw,
            execution_id=self.execution_id,
            container_id=self.pins.init.container_id,
            command=self.pins.command,
        )

    def poll(self):
        """Bounded read-only reconciliation, never successful recording evidence."""
        try:
            require(not self.closed and not self.failed and not self.done)
            require(self.owner == (os.getpid(), get_ident()))
            began = self._clock()
            end = time.monotonic() + min(2, self.plan.deadlines.recover_by - began)
            require(time.monotonic() < end)
            require(_peer(self.endpoint) == self.peer)
            self._history(end)
            exits = self._actors()
            if not set(ROLES[1:]) <= exits:
                require(time.monotonic() < end and self._clock() - began <= 2)
                return None
            raw, terminal = self._inspect(end)
            require(terminal.phase == "not_running")
            require(terminal.pid in (0, self.actors[1].host_pid))
            require(_peer(self.endpoint) == self.peer)
            self._history(end)
            confirmed = self._actors()
            require(exits <= confirmed and set(ROLES[1:]) <= confirmed)
            observed_at = self._clock()
            require(time.monotonic() < end and observed_at - began <= 2)
            result = Evidence(
                self.execution_id,
                self.pins.init.container_id,
                self.pins.generation,
                self.plan.sha256,
                self.ready_sha256,
                dispatch.binding.checksum([asdict(actor) for actor in self.actors]),
                self.state.sha256,
                dispatch.binding.checksum(raw),
                terminal.returncode,
                "init" in confirmed,
                observed_at,
            )
            self.done = True
            return result
        except BaseException as error:
            self._fail(error)

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        failed = False
        for fd in self.handles.values():
            try:
                os.close(fd)
            except OSError:
                failed = True
        self.handles.clear()
        require(not failed)


if __name__ == "__main__":
    raise SystemExit("Read-only original exit reconciliation only; no restoration enabled.")
