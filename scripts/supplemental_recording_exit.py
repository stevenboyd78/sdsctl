#!/usr/bin/env python3
"""Separate exact-process and Engine exit checks after actual native completion.

No signal, candidate stop, container-init exit inference, file rewrite or normal
App restoration. A failed recording/transport case must use separate independent
recovery; no receipt, EOF or Engine flag substitutes for retained kernel handles.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import time
from dataclasses import asdict, dataclass
from threading import Lock, get_ident

import supplemental_recording_reconcile as reconcile
import supplemental_recording_relay as returned

engine = returned.retained.engine
MESSAGE = "Recording exit qualification is unconfirmed; retain handles for independent recovery."


class UnconfirmedExit(ValueError):
    """No successful completion or EOF grants permission to restore ownership."""


def require(value):
    if not value:
        raise UnconfirmedExit(MESSAGE)


@dataclass(frozen=True)
class Exited:
    guardian_pid: int
    native_pid: int
    watchdog_pid: int
    completion_sha256: str
    reaped_return_sha256: str
    execution_inspection_sha256: str
    observed_at: float


class Finalized:
    """Keep actual completion/files across the existing one-use exit collector.

    Capture while the original Relay and pre-begin Operator are still retained.
    collect_exit() invokes the existing fourth-return/EOF/pidfd/Engine collector;
    no constructor accepts Completion, Exited, stopped bytes or a replacement
    baseline. read() then brackets the original files with Operator.recheck().

    This is file evidence ONLY, not a full host/authorization-journal sample,
    source/runtime qualification, independent recovery service or restoration
    permission. All original live Start/Relay guards remain unchanged. No
    resources are transferred: close() only retires this reader, not its caller's
    journals, transport, process handles or separately owned Operator endpoint.
    """

    def __init__(self, relay, operator):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = self.used = False
        self._exit_receipt = None
        try:
            began = time.monotonic()
            require(type(relay) is returned.Relay and type(operator) is reconcile.Operator)
            require(not operator.closed and not operator.failed and not operator.done)
            require(operator.owner == self.owner)
            require(relay.phase == "closed")
            collected = relay.recheck_completed()
            self.relay, self.operator = relay, operator
            self.ready, self.ledger, self.guard = relay.ready, relay.ledger, relay.guard
            self.plan, self.clock = operator.plan, operator.clock
            self.context = relay._completion_context
            (
                self.completion,
                self.expected,
                self.schedule,
                self.state,
                self.directory,
                self.dir_id,
            ) = self.context
            self.binding = relay.binding
            self.location, self.location_id = relay.directory, relay.directory_identity
            self.pins, self.actors = operator.pins, operator.actors
            self.execution_id = operator.execution_id
            self.ready_sha256 = hashlib.sha256(self.ready.ready_raw).hexdigest()
            self.intent_sha256 = relay.intent_sha256
            require(collected == self.completion.collected)
            require(operator.pins == relay.guard.pins)
            require(operator.execution_id == relay.guard.state.execution_id)
            require(operator.ready_sha256 == self.ready_sha256)
            require(operator.actors == relay.guard.actors)
            require(operator.plan.sha256 == self.binding.plan_sha256)
            require(operator.pins.host == self.binding)
            require(self.ready.clock is self.clock is self.plan.original_clock)
            self.seal = self._values()
            self._context()
            require(began <= time.monotonic() < min(began + 2, self.guard.finish_by))
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedExit(MESSAGE) from None

    def _values(self):
        result = self.completion
        return returned.host.checksum(
            dict(
                completion=dict(
                    acknowledgment=asdict(result.acknowledgment),
                    collected=asdict(result.collected),
                    native_return_sha256=result.native_return_sha256,
                    stopped_sha256=hashlib.sha256(result.stopped_raw).hexdigest(),
                ),
                expected=asdict(self.expected),
                schedule=asdict(self.schedule),
                state=asdict(self.state),
                binding=self.binding.payload(),
                plan_sha256=self.plan.sha256,
                pins=self.pins.payload(),
                actors=[asdict(actor) for actor in self.actors],
                progress_directory=str(self.directory) if self.directory is not None else None,
                progress_identity=self.dir_id,
                ledger_directory=str(self.location),
                ledger_identity=self.location_id,
                intent_sha256=self.intent_sha256,
                execution_id=self.execution_id,
                ready_sha256=self.ready_sha256,
            )
        )

    def _context(self):
        require(not self.closed and not self.failed and self.owner == (os.getpid(), get_ident()))
        relay, operator = self.relay, self.operator
        require(type(relay) is returned.Relay and type(operator) is reconcile.Operator)
        require(not operator.closed and not operator.failed and operator.owner == self.owner)
        require(relay._completion_context is self.context and relay.completion is self.completion)
        require(relay.expected is self.expected and relay.plan is self.schedule)
        require(
            relay.ready is self.ready and relay.ledger is self.ledger and relay.guard is self.guard
        )
        require(not self.ready.failed and not self.ledger._poisoned)
        require(relay.binding is self.binding and self.ledger.binding == self.binding)
        require(relay.directory == self.ledger.directory == self.location)
        require(relay.directory_identity == self.ledger._directory_identity == self.location_id)
        require(relay.intent_sha256 == self.intent_sha256 and self.ledger.state == self.state)
        require(self.state.closed and self.state.preservation is None)
        require(operator.plan is self.plan and operator.clock is self.clock)
        require(reconcile.plans.load_bytes(self.plan.raw, self.plan.sha256) == self.plan)
        require(self.plan.original_clock is self.clock)
        require(operator.pins is self.pins and operator.actors is self.actors)
        require(
            operator.execution_id == self.execution_id
            and operator.ready_sha256 == self.ready_sha256
        )
        require(hashlib.sha256(self.ready.ready_raw).hexdigest() == self.ready_sha256)
        require(self._values() == self.seal)

    def _history(self, end):
        host = returned.host
        host._location(self.location, self.binding)
        with host.protected._private_directory(self.location, exclusive=False) as fd:
            require(host.identity(os.fstat(fd))[:6] == self.location_id)
            require(host._read(fd, self.binding, end) == self.state)
            intent = host.protected.evidence.read_bytes(
                fd, "0001.json", limit=host.MAX_BYTES, deadline=end
            )
            require(hashlib.sha256(intent).hexdigest() == self.intent_sha256)
        require(returned.Relay._progress_identity(self.directory) == self.dir_id)
        collector = returned.local.protected.Collector(self.binding.projection.host)
        if self.state.tip is None:
            require(self.directory is None)
        else:
            require(self.directory is not None)
            checkpoints = returned.local.checkpoints
            context = checkpoints._context(self.directory, collector, self.expected)
            with host.protected._private_directory(self.directory, exclusive=False) as fd:
                require(host.identity(os.fstat(fd))[:6] == self.dir_id)
                checkpoints._read(fd, collector, self.expected, context, self.state.tip, end)
        require(time.monotonic() < end)
        return collector

    def collect_exit(self):
        """Consume the existing collector once and privately retain its return."""
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._context()
            require(not self.used and self.relay.phase == "closed")
            self.used = True
            require(self.relay.recheck_completed() == self.completion.collected)
            result = collect(self.relay)
            require(type(result) is Exited)
            require(result.completion_sha256 == self.completion.native_return_sha256)
            require(
                (result.guardian_pid, result.native_pid, result.watchdog_pid)
                == tuple(actor.host_pid for actor in self.actors[1:])
            )
            self._context()
            self._exit_receipt = result, returned.host.checksum(asdict(result))
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def read(self):
        """Fresh immutable files, original completion and retained process custody."""
        acquired = False
        try:
            began = time.monotonic()
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._context()
            require(self.used and self._exit_receipt is not None and self.relay.phase == "exited")
            require(self.ready.closed and self.ready.client.closed and self.ready.processes.closed)
            receipt = self._exit_receipt
            result, digest = receipt
            require(type(result) is Exited and returned.host.checksum(asdict(result)) == digest)
            now = self.operator._clock()
            end = min(began + 2, time.monotonic() + self.plan.deadlines.recover_by - now)
            require(time.monotonic() < end)
            original = self.operator.recheck()
            require(original.returncode == 0)
            require(original.engine_sha256 == result.execution_inspection_sha256)
            require(result.completion_sha256 == self.completion.native_return_sha256)
            collector = self._history(end)
            stopped = json.loads(self.completion.stopped_raw)
            require(returned.host.encode(stopped) == self.completion.stopped_raw)
            require(returned.host.checksum(stopped) == self.state.acknowledgment.stopped_sha256)
            files = collector.finalized(
                self.expected,
                stopped=stopped,
                acknowledgment=self.completion.acknowledgment,
                previous=self.completion.collected.progress,
            )
            require(files == self.completion.collected)
            self._history(end)
            require(self.operator.recheck() is original)
            self._context()
            require(self._exit_receipt is receipt and self.used and self.relay.phase == "exited")
            require(self.ready.closed and self.ready.client.closed and self.ready.processes.closed)
            require(returned.host.checksum(asdict(result)) == digest)
            ended = self.operator._clock()
            require(0 <= ended - now <= 2 and began <= time.monotonic() < end)
            return files
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        self.closed = True


def collect(relay):
    """One fourth return, clean EOF, three retained exits and one fixed GET.

    Native and watchdog must already be independently exited when their reap
    report is received. Guardian exit/EOF is allowed only a three-second tail
    capped by the ORIGINAL watchdog/grace and attachment deadlines. The exact
    execution must then report successful not-running for the same command.
    The independently retained container init must remain live throughout;
    stopping it and authorizing normal-App restoration are not done here.
    """
    acquired = False
    try:
        require(type(relay) is returned.Relay)
        require(relay._lock.acquire(blocking=False))
        acquired = True
        require(relay.phase == "closed")
        relay.phase = "exit_pending"
        guard, channel = relay.guard, relay.ready.client.attachment
        require(relay._state(closed=True).acknowledgment == relay.completion.acknowledgment)
        guard.check()
        require(channel.reads == 3 and not channel.finished)
        end = min(guard.finish_by, guard.watch_deadline + returned.begin.received.GRACE_SECONDS)
        require(time.monotonic() < end)
        value = channel.receive(deadline=end)
        received_at = time.monotonic()
        end = min(end, received_at + 3)
        mapping = returned.host.protected._mapping
        mapping(
            value, {"schema", "kind", "phase", "context", "guardian", "native", "watchdog", "body"}
        )
        require(type(value["schema"]) is int and value["schema"] == 1)
        require(value["kind"] == "finite-recording-operator" and value["phase"] == "exited")
        for key in ("context", "guardian", "native", "watchdog"):
            require(returned.host.encode(value[key]) == returned.host.encode(relay.envelope[key]))
        body = mapping(value["body"], {"pid", "start_ticks", "returncode", "reaped_at", "watchdog"})
        for key in ("pid", "start_ticks", "returncode"):
            require(type(body[key]) is int)
        native, watch = relay.envelope["native"], relay.envelope["watchdog"]
        require(
            (body["pid"], body["start_ticks"], body["returncode"])
            == (native["pid"], native["start_ticks"], 0)
        )
        returned.host.clock(body["reaped_at"])
        require(relay.completed_at <= body["reaped_at"] <= received_at < end)
        expected_watch = {
            "native_pid": native["pid"],
            "native_start_ticks": native["start_ticks"],
            "watchdog_pid": watch["pid"],
            "watchdog_start_ticks": watch["start_ticks"],
            "deadline": watch["deadline"],
            "grace": watch["grace"],
            "returncode": 0,
        }
        require(returned.host.encode(body["watchdog"]) == returned.host.encode(expected_watch))
        exits = guard.check()
        require({"native", "watchdog"} <= exits)
        channel.finish(deadline=end)  # Only actual returned clean EOF qualifies.
        fd = guard.handles["guardian"]
        require(select.select([fd], [], [], max(0, end - time.monotonic()))[0] == [fd])
        require(guard.check() == frozenset({"guardian", "native", "watchdog"}))
        require(time.monotonic() < end)
        pins, execution_id = guard.pins, guard.state.execution_id
        inspected = engine._json_request(
            guard.endpoint, "GET", f"/exec/{execution_id}/json", None, 200, deadline=end
        )
        observed = engine.dispatch.execution.inspect(
            inspected,
            execution_id=execution_id,
            container_id=pins.init.container_id,
            command=pins.command,
        )
        require(observed.phase == "not_running" and observed.returncode == 0)
        require(observed.pid in (0, guard.actors[1].host_pid))
        require(guard.check() == frozenset({"guardian", "native", "watchdog"}))
        require(relay._state(closed=True).acknowledgment == relay.completion.acknowledgment)
        require(time.monotonic() < end)
        result = Exited(
            *(actor.host_pid for actor in guard.actors[1:]),
            relay.completion.native_return_sha256,
            returned.host.checksum(value),
            returned.host.checksum(inspected),
            time.monotonic(),
        )
        relay.phase = "exited"
        relay.ready.client.close()  # Handles remain owned by Ready, not this transport.
        return result
    except BaseException as error:
        if type(relay) is returned.Relay:
            relay.phase = "unconfirmed"
            relay.ready.failed = True
            relay.ready.client.close()
        if not isinstance(error, Exception):
            raise
        raise UnconfirmedExit(MESSAGE) from None
    finally:
        if acquired:
            relay._lock.release()


if __name__ == "__main__":
    raise SystemExit("Private exact-exit qualification only; no restoration enabled.")
