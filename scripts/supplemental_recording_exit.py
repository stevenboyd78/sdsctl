#!/usr/bin/env python3
"""Separate exact-process and Engine exit checks after actual native completion.

No signal, candidate stop, container-init exit inference, file rewrite or normal
App restoration. A failed recording/transport case must use separate independent
recovery; no receipt, EOF or Engine flag substitutes for retained kernel handles.
"""

from __future__ import annotations

import select
import time
from dataclasses import dataclass

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
