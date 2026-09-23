#!/usr/bin/env python3
"""Offline exact-child return / projected host-ledger join.

This is not an installed cross-container transport or a source authenticator.
The launcher must independently qualify executable bytes, mount projection and
boot/clock domain. No operation here starts/stops a recorder or proves exit.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from threading import Lock

import supplemental_recording_binding as host
import supplemental_recording_channel as native
import supplemental_recording_checkpoints as checkpoints
import supplemental_recording_protected as protected
from supplemental_handoff_policy import checksum

MESSAGE = "Recording return binding is unconfirmed; preserve evidence and do not retry."


class UnconfirmedBridge(ValueError):
    """A lost return cannot be recovered by a second request or a disk receipt."""


def require(value):
    if not value:
        raise UnconfirmedBridge(MESSAGE)


@dataclass(frozen=True)
class Completion:
    acknowledgment: protected.Acknowledgment
    collected: protected.Collected
    native_return_sha256: str


class Bridge:
    """Consume one real Receiver against an already durable host start intent.

    Same-process qualification adapter, not permission to launch a native child
    on the host or substitute a serialized Received object for authentication.
    The host ledger is independently replayed before every return. A successful
    completion requires current host files as well as the native return. Native
    and host aliases are never opened interchangeably or freshly rebaselined.
    """

    def __init__(self, ledger, receiver):
        self.phase = "unconfirmed"
        self._lock = Lock()
        try:
            require(type(ledger) is host.Ledger and type(receiver) is native.Receiver)
            self.ledger, self.receiver = ledger, receiver
            self.binding, self.directory = ledger.binding, ledger.directory
            self.native_binding = receiver.binding
            self.parent = os.getpid()
            self.peer = (receiver.pid, receiver.start_ticks, receiver.uid, receiver.gid)
            state = self._state()
            require(state.count == 2 and state.expected is None and state.tip is None)
            self.intent_at = state.now
            require(receiver.expected is None and receiver.plan is None)
            self._channel(state, "started")
            require(self.intent_at <= time.monotonic() < state.start_by)
            self.expected, self.plan = None, None
            self.phase = "started"
        except Exception:
            raise UnconfirmedBridge(MESSAGE) from None

    def _state(self):
        require(os.getpid() == self.parent)
        require(self.ledger.binding == self.binding and self.ledger.directory == self.directory)
        state = host.load(self.directory, self.binding)
        require(state == self.ledger.state and state.generation is not None and not state.closed)
        require(state.acknowledgment is None)
        return state

    def _channel(self, state, phase):
        receiver = self.receiver
        require(receiver.fd >= 0 and receiver.owner_pid == self.parent)
        require((receiver.pid, receiver.start_ticks, receiver.uid, receiver.gid) == self.peer)
        require(receiver.phase == phase and receiver.binding == self.native_binding)
        require(
            receiver.binding
            == native.Binding(
                self.binding.projection.native,
                state.generation,
                self.binding.projection.sha256,
                self.binding.source_sha256,
                state.start_by,
                state.finish_by,
            )
        )

    def _receive(self, phase):
        require(self.phase == phase)
        self.phase = "unconfirmed"  # Consume before replay, validation or recvmsg.
        state = self._state()
        self._channel(state, phase)
        require(self.receiver.expected == self.expected and self.receiver.plan == self.plan)
        require(state.expected == self.expected)
        report = self.receiver.receive()
        require(type(report) is native.Received)
        require((report.pid, report.start_ticks, report.uid, report.gid) == self.peer)
        value, expected = native._decode(report.raw, self.native_binding, phase, self.expected)
        require(self.intent_at <= value["at"] <= report.received_at <= time.monotonic())
        return state, report, value, expected

    def started(self):
        require(self._lock.acquire(blocking=False))
        try:
            state, report, value, expected = self._receive("started")
            plan = native.owner.Plan(**value["body"]["plan"])
            require(plan.prepared_at >= self.intent_at)
            deadline = min(state.start_by, plan.start_by)
            require(time.monotonic() < deadline)
            self.ledger.started(expected, now=time.monotonic(), success_sha256=report.sha256)
            # A durable entry whose publication return was late is not a timely
            # acknowledgment. Keep it for review, without enabling completion.
            require(time.monotonic() < deadline)
            self.expected, self.plan = expected, plan
            self.phase = "completed"
            return expected
        except BaseException as error:
            self.phase = "unconfirmed"
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedBridge(MESSAGE) from None
        finally:
            self._lock.release()

    def completed(self, *, progress_directory=None):
        require(self._lock.acquire(blocking=False))
        try:
            state, report, value, expected = self._receive("completed")
            deadline = min(state.finish_by, self.plan.finish_by)
            require(time.monotonic() < deadline and value["at"] >= self.plan.stop_at)
            collector = protected.Collector(self.binding.projection.host)
            previous = None
            if state.tip is not None:
                require(progress_directory is not None)
                previous = checkpoints.load_progress(
                    progress_directory, collector, expected, expected_tip=state.tip
                )
            else:
                require(progress_directory is None)
            stopped = value["body"]["stopped"]
            acknowledgment = protected.Acknowledgment(
                expected.case,
                expected.generation,
                collector.stored.contract.sha256,
                expected.started_at,
                checksum(stopped),
                report.sha256,
            )
            collected = collector.finalized(
                expected, stopped=stopped, acknowledgment=acknowledgment, previous=previous
            )
            require(asdict(collected.artifact) == value["body"]["artifact"])
            require(time.monotonic() < deadline)
            self.ledger.completed(acknowledgment, now=time.monotonic())
            require(time.monotonic() < deadline)
            self.phase = "closed"
            return Completion(acknowledgment, collected, report.sha256)
        except BaseException as error:
            self.phase = "unconfirmed"
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedBridge(MESSAGE) from None
        finally:
            self._lock.release()


if __name__ == "__main__":
    raise SystemExit("Offline return binding only; no launch, recording or recovery is enabled.")
