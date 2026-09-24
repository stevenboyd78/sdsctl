#!/usr/bin/env python3
"""Retained exact operator exits after a lost/failed return, not success.

Capture while the actual original Ready is live, before begin. An independently
authenticated Engine endpoint and duplicated original pidfds survive closure of
the recording transport. No process discovery, signals, replay, recording-file
inference, completion promotion or App action. The separate publish() operation
can record only this observer's actual exit evidence in the original journal3.
Installed recovery supervision and
source/protection qualification remain separate, required responsibilities.
"""

from __future__ import annotations

import hashlib
import os
import select
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
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
        self.publish_attempted = False
        self.result = self.result_sha256 = None
        self._terminal_receipt = None
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
            self.result, self.result_sha256 = result, result.sha256
            self._terminal_receipt = (result, result.sha256)
            return result
        except BaseException as error:
            self._fail(error)

    def _receipt(self):
        """Only the actual terminal return, not an equal/caller-replaced value."""
        require(self.done and self._terminal_receipt is not None)
        require(type(self.plan) is plans.Plan)
        require(plans.load_bytes(self.plan.raw, self.plan.sha256) == self.plan)
        require(self.plan.original_clock is self.clock)
        result, digest = self._terminal_receipt
        require(type(result) is Evidence and self.result is result)
        require(result.sha256 == self.result_sha256 == digest)
        require(
            (
                result.execution_id,
                result.container_id,
                result.generation,
                result.plan_sha256,
                result.ready_sha256,
                result.actors_sha256,
                result.dispatch_sha256,
            )
            == (
                self.execution_id,
                self.pins.init.container_id,
                self.pins.generation,
                self.plan.sha256,
                self.ready_sha256,
                dispatch.binding.checksum([asdict(actor) for actor in self.actors]),
                self.state.sha256,
            )
        )
        return result

    def recheck(self):
        """Fresh read-only custody checks AFTER this observer's terminal poll.

        Reuses original pidfds/dispatch/Engine identities under the original
        recovery deadline; never repolls, republishes or renews that receipt's
        observed_at. Returns the SAME historical Evidence, not a fresh file,
        completion, health or init-exit verdict. Init may exit since capture,
        but that does not rewrite the original result or authorize restoration.
        Installed supervision and full input qualification remain separate.
        """
        try:
            require(not self.closed and not self.failed)
            require(self.owner == (os.getpid(), get_ident()))
            original = self._receipt()
            began = self._clock()
            require(began >= original.observed_at)
            end = time.monotonic() + min(2, self.plan.deadlines.recover_by - began)
            require(time.monotonic() < end)
            require(_peer(self.endpoint) == self.peer)
            self._history(end)
            exited = self._actors()
            require(set(ROLES[1:]) <= exited)
            require(not original.init_exited or "init" in exited)
            raw, terminal = self._inspect(end)
            require(terminal.phase == "not_running" and terminal.returncode == original.returncode)
            require(terminal.pid in (0, self.actors[1].host_pid))
            require(dispatch.binding.checksum(raw) == original.engine_sha256)
            require(_peer(self.endpoint) == self.peer)
            self._history(end)
            require(exited <= self._actors())
            require(self._receipt() is original)
            ended = self._clock()
            require(time.monotonic() < end and 0 <= ended - began <= 2)
            return original
        except BaseException as error:
            self._fail(error)

    def _journal(self, journal, end):
        """Recheck actual original journal bytes before/after exit publication."""
        base = plans.base
        require(
            type(journal) is plans.bootstrap.Journal and journal.path == self.plan.root / "journal"
        )
        journal.check_directory()
        machine = journal.replayed(end)
        entries = tuple(base.encode(entry) for entry in journal.entries)
        require(0 < len(journal.entries) <= journal.max_events)
        require(
            sorted(os.listdir(journal.fd)) == [journal.name(i) for i in range(len(journal.entries))]
        )
        for index, expected in enumerate(entries):
            name = journal.name(index)
            before = os.stat(name, dir_fd=journal.fd, follow_symlinks=False)
            require(before.st_uid == os.geteuid() and before.st_mode & 0o7777 == 0o600)
            raw = dispatch.binding.protected.evidence.read_bytes(
                journal.fd, name, limit=base.MAX_BYTES, deadline=end
            )
            require(raw == expected)
            require(
                dispatch.binding.identity(os.stat(name, dir_fd=journal.fd, follow_symlinks=False))
                == dispatch.binding.identity(before)
            )
        journal.check_directory()
        require(sorted(os.listdir(journal.fd)) == [journal.name(i) for i in range(len(entries))])
        require(tuple(base.encode(entry) for entry in journal.entries) == entries)
        require(journal.replayed(end) is machine)
        require(type(machine) is plans.bootstrap.Machine)
        require(
            base.encode(journal.entries[0]["event"])
            == base.encode(self.plan.preparation(machine.baseline, self.pins.host.projection))
        )
        require((machine.case_id, machine.boot_id) == (self.plan.case, self.plan.boot))
        require(machine.created_at == self.plan.deadlines.issued_at)
        require(machine.hard_deadline == self.plan.deadlines.recover_by)
        require(
            machine.bootstrap == self.plan.bootstrap
            and machine.contract == self.plan.candidate.contract
        )
        require(machine.state.candidate_generation == self.pins.generation)
        require(machine.state.launch_plan_sha256 == self.pins.command.plan_sha256)
        require(machine.state.launch_intent_sha256 is not None)
        record = next(item for item in machine.state.processes if item.slug == base.CANDIDATE)
        require(
            (record.container_id, record.pid, record.start_ticks)
            == (self.pins.init.container_id, self.pins.init.pid, self.pins.init.start_ticks)
        )
        require(time.monotonic() < end)
        return machine

    def publish(self, journal):
        """Durably record ONLY the actual retained exit, once, without an action.

        No caller-supplied receipt/digest is accepted. A late/failed fsync return
        consumes this publication; the on-disk case is preserved, never retried.
        A review/expired policy cannot be revived. This marks finish_requested,
        not successful recording, init exit, restoration, or fresh authorization.
        The independent recovery session must still qualify all other gates.
        """
        try:
            require(self.owner == (os.getpid(), get_ident()))
            require(
                not self.closed and not self.failed and self.done and not self.publish_attempted
            )
            self.publish_attempted = True
            self._receipt()
            now = self._clock()
            require(0 <= now - self.result.observed_at <= 2)
            end = time.monotonic() + min(2, self.plan.deadlines.recover_by - now)
            require(_peer(self.endpoint) == self.peer)
            self._history(end)
            require(set(ROLES[1:]) <= self._actors())
            machine = self._journal(journal, end)
            before = machine.state
            require(
                before.phase in ("starting_operator", "candidate_running", "stopping_candidate")
            )
            require(before.operator_exit_sha256 is None)
            count = len(journal.entries)
            now = self._clock()
            require(0 <= now - self.result.observed_at <= 2)
            event = dict(
                kind="operator_exited",
                boot_id=self.plan.boot,
                now=now,
                generation=self.pins.generation,
                intent_sha256=before.launch_intent_sha256,
                exit_evidence_sha256=self.result_sha256,
            )
            require(journal.append(event) is None)  # No App action is returned.
            after = self._journal(journal, end).state
            require(len(journal.entries) == count + 1 and journal.entries[-1]["event"] == event)
            require(
                after
                == replace(before, operator_exit_sha256=self.result_sha256, finish_requested=True)
            )
            require(_peer(self.endpoint) == self.peer)
            require(set(ROLES[1:]) <= self._actors())
            require(self._clock() - self.result.observed_at <= 2 and time.monotonic() < end)
            return self.result_sha256
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
    raise SystemExit("Private original-exit journal join only; no restoration enabled.")
