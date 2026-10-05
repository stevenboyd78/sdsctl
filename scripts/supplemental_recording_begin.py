#!/usr/bin/env python3
"""One private begin AFTER original durable host intent; uninstalled mechanism.

The source-qualified host policy must independently authorize that intent.
This module joins it to actual received readiness and private transport, not a
caller-supplied JSON request. Successful sending is NOT a native start return,
file-finalization result, process exit, or permission to restore another owner.
"""

from __future__ import annotations

import json
import os
import time

import supplemental_recording_binding as host
import supplemental_recording_channel as native
import supplemental_recording_ready as received

MESSAGE = "Finite recording begin is unconfirmed; preserve the intent and do not retry."


class UnconfirmedBegin(ValueError):
    """An attempted or lost write never authorizes a second begin."""


def require(value):
    if not value:
        raise UnconfirmedBegin(MESSAGE)


def _intent(ledger, ready):
    require(type(ledger) is host.Ledger and not ledger._poisoned)
    require(ledger.binding == ready.client.claim.pins.host)
    host._location(ledger.directory, ledger.binding)
    end = min(time.monotonic() + 2, ready.ready_by)
    with host.protected._private_directory(ledger.directory, exclusive=False) as fd:
        require(host.identity(os.fstat(fd))[:6] == ledger._directory_identity)
        state = host._read(fd, ledger.binding, end)
        require(state == ledger.state and time.monotonic() < end)
    require(state.count == 2 and not state.closed)
    require(state.expected is None and state.tip is None and state.acknowledgment is None)
    require(state.generation == ready.client.claim.pins.generation)
    require(ready.received_at <= state.now <= time.monotonic() < state.start_by)
    require(state.finish_by <= ready.watch_deadline)
    return state


def send_once(ready, ledger):
    """Send exactly the fixed request for the returned original intent.

    Requires a live Ready object and live, unpoisoned original Ledger. Full chain
    and original directory identity are checked before and after sending, so a
    byte-identical replacement directory cannot authorize an external write.
    No new intent/deadline is fabricated here. Failure closes transport but
    preserves ready.processes for independent exit observation; caller owns
    its eventual close. No ledger entry is promoted to started by this function.
    """
    try:
        require(type(ready) is received.Ready)
        ready.check_before_begin()
        state = _intent(ledger, ready)
        binding = native.Binding(
            ledger.binding.projection.native,
            state.generation,
            ledger.binding.projection.sha256,
            ledger.binding.source_sha256,
            state.start_by,
            state.finish_by,
        )
        request = {
            "schema": 1,
            "kind": "finite-recording-operator",
            "phase": "begin",
            "context": json.loads(ready.context_raw),
            "body": {
                "binding": binding.payload(),
                "intent_at": state.now,
                "intent_sha256": state.sha256,
            },
        }
        ready.check_before_begin()
        require(_intent(ledger, ready) == state)
        # Leave the guardian its existing preparation margin. The native
        # guardian separately enforces the complete pinned read/finalize budget.
        end = min(ready.ready_by, state.start_by - 3, time.monotonic() + 1)
        require(time.monotonic() < end)
        ready.client.attachment.send_begin(request, deadline=end)
        require(_intent(ledger, ready) == state)
        ready.client._check()
        ready.clock.check_later(received.clock.read())
        ready.processes.refresh()
        require(time.monotonic() < end)
        return binding
    except BaseException as error:
        if type(ready) is received.Ready:
            ready.failed = True
            ready.client.close()
        if not isinstance(error, Exception):
            raise
        raise UnconfirmedBegin(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Private one-use begin mechanism only; no installed host plan enabled.")
