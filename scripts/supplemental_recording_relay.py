#!/usr/bin/env python3
"""Actual private Engine returns joined to original host recording evidence.

Distinct from the same-process exact-Receiver Bridge, whose authentication
contract is unchanged. This adapter uses actual received Ready/Engine/process
binding and a single returned durable begin. Image/interpreter/source/mount and
host-policy qualification remain independent, required prerequisites. No public
service or installed host plan invokes this mechanism yet.
"""

from __future__ import annotations

import json
import os
import time
from threading import Lock

import supplemental_recording_begin as begin
import supplemental_recording_bridge as local
import supplemental_recording_retained as retained

host, native = local.host, local.native
MESSAGE = "Relayed recording return is unconfirmed; preserve evidence and do not retry."


class UnconfirmedRelay(ValueError):
    """Neither a successful write nor an exited process is a native return."""


def require(value):
    if not value:
        raise UnconfirmedRelay(MESSAGE)


class Relay(local.Bridge):
    """One actual begin followed by two actually received, bounded returns.

    Shares only the existing start/completion artifact-verification algorithm;
    never passes a deserialized Received to the strict same-process constructor.
    The caller owns Ready, including retained exact exit handles after failure.
    Successful completion is NOT guardian/watchdog/init exit or restoration.
    """

    def __init__(self, ledger, ready):
        self.phase = "unconfirmed"
        self._lock = Lock()
        self.ready = ready
        try:
            require(type(ledger) is host.Ledger and type(ready) is begin.received.Ready)
            self.ledger, self.binding, self.directory = ledger, ledger.binding, ledger.directory
            self.directory_identity = ledger._directory_identity
            self.parent = os.getpid()
            # This consumes actual original readiness and a returned durable
            # intent; callers cannot supply either wire bytes or a native Binding.
            self.native_binding = begin.send_once(ready, ledger)
            self.guard = retained.Retained(ready)
            self.envelope = json.loads(ready.ready_raw)
            self.peer = tuple(
                self.envelope["native"][key] for key in ("pid", "start_ticks", "uid", "gid")
            )
            self.intent_at = ledger.state.now
            self.intent_sha256 = ledger.state.sha256
            self.expected, self.plan = None, None
            state = self._state()
            require(state.count == 2 and state.expected is None and state.tip is None)
            self.phase = "started"
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.phase = "unconfirmed"
        if type(self.ready) is begin.received.Ready:
            self.ready.failed = True
            self.ready.client.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedRelay(MESSAGE) from None

    def _state(self, *, closed=False):
        require(os.getpid() == self.parent and not self.ledger._poisoned)
        require(self.ledger.binding == self.binding and self.ledger.directory == self.directory)
        require(self.ledger._directory_identity == self.directory_identity)
        host._location(self.directory, self.binding)
        # Closed history may be reconciled during the separately bounded exit
        # tail. This read does not renew either native publication deadline.
        end = min(
            time.monotonic() + 2,
            self.guard.finish_by if closed else self.native_binding.finish_by,
        )
        with host.protected._private_directory(self.directory, exclusive=False) as fd:
            require(host.identity(os.fstat(fd))[:6] == self.directory_identity)
            state = host._read(fd, self.binding, end)
            require(state == self.ledger.state and time.monotonic() < end)
        require(state.closed is closed and (state.acknowledgment is not None) is closed)
        require(state.generation == self.native_binding.generation)
        require(
            (state.start_by, state.finish_by)
            == (self.native_binding.start_by, self.native_binding.finish_by)
        )
        return state

    def _receive(self, phase):
        require(self.phase == phase)
        self.phase = "unconfirmed"  # Consume before replay, validation or read.
        self.guard.check()
        state = self._state()
        require(state.expected == self.expected)
        if phase == "started":
            require(state.count == 2 and state.sha256 == self.intent_sha256)
            deadline = state.start_by
        else:
            require(phase == "completed" and self.plan is not None)
            deadline = min(state.finish_by, self.plan.finish_by)
        channel = self.ready.client.attachment
        require(channel.reads == (1 if phase == "started" else 2))
        require(time.monotonic() < deadline)
        value = channel.receive(deadline=deadline)
        received_at = time.monotonic()
        self.guard.check()
        require(self._state() == state and time.monotonic() < deadline)
        mapping = host.protected._mapping
        mapping(
            value, {"schema", "kind", "phase", "context", "guardian", "native", "watchdog", "body"}
        )
        require(type(value["schema"]) is int and value["schema"] == 1)
        require(value["kind"] == "finite-recording-operator" and value["phase"] == phase)
        for key in ("context", "guardian", "native", "watchdog"):
            # Canonical byte equality also rejects bool/number substitutions.
            require(host.encode(value[key]) == host.encode(self.envelope[key]))
        receipt = mapping(
            mapping(value["body"], {"received"})["received"],
            {"raw", "pid", "start_ticks", "uid", "gid", "received_at"},
        )
        fields = tuple(receipt[key] for key in ("pid", "start_ticks", "uid", "gid"))
        require(all(type(item) is int for item in fields) and fields == self.peer)
        require(type(receipt["raw"]) is str)
        raw = receipt["raw"].encode("ascii")
        decoded, expected = native._decode(raw, self.native_binding, phase, self.expected)
        host.clock(receipt["received_at"])
        require(self.intent_at <= decoded["at"] <= receipt["received_at"] <= received_at < deadline)
        # Construct internally only after the actual bound transport, pinned
        # operator envelope and private native-return bytes have all checked.
        report = native.Received(raw, *fields, receipt["received_at"])
        if phase == "completed":
            self.completed_at = decoded["at"]
        return state, report, decoded, expected

    def started(self):
        try:
            expected = super().started()
            self.guard.check()
            require(self._state().expected == expected)
            require(time.monotonic() < min(self.native_binding.start_by, self.plan.start_by))
            return expected
        except BaseException as error:
            self._fail(error)

    def completed(self, *, progress_directory=None):
        try:
            result = super().completed(progress_directory=progress_directory)
            self.guard.check()
            require(self._state(closed=True).acknowledgment == result.acknowledgment)
            require(time.monotonic() < min(self.native_binding.finish_by, self.plan.finish_by))
            self.completion = result
            return result
        except BaseException as error:
            self._fail(error)


if __name__ == "__main__":
    raise SystemExit("Private relayed-return join only; no installed host plan enabled.")
