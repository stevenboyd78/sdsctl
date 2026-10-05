#!/usr/bin/env python3
"""Actual private Engine returns joined to original host recording evidence.

Distinct from the same-process exact-Receiver Bridge, whose authentication
contract is unchanged. This adapter uses actual received Ready/Engine/process
binding and a single returned durable begin. Image/interpreter/source/mount and
host-policy qualification remain independent, required prerequisites. No public
service or installed host plan invokes this mechanism yet.
"""

from __future__ import annotations

import hashlib
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
            expected, plan, started = self._started_context
            require(self.expected is expected and self.plan is plan)
            require(started.count == 3 and started.expected == expected)
            deadline = min(state.finish_by, self.plan.finish_by)
            with host.protected._private_directory(self.directory, exclusive=False) as fd:
                require(host.identity(os.fstat(fd))[:6] == self.directory_identity)
                raw = host.protected.evidence.read_bytes(
                    fd, "0002.json", limit=host.MAX_BYTES, deadline=deadline
                )
                require(hashlib.sha256(raw).hexdigest() == started.sha256)
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
            # Only the actually received, durably acknowledged start anchors
            # later phase selection. No caller-selected schedule or disk receipt.
            self._started_context = (self.expected, self.plan, self.ledger.state)
            self._progress_context = None
            return expected
        except BaseException as error:
            self._fail(error)

    def read_progress(self, directory):
        """Fresh intermediate files under the original authenticated owner plan.

        First read binds an empty private checkpoint directory, before any tip
        is published. Later reads accept only the original ledger's acknowledged
        chain, preserving its previously seen prefix and directory identity.
        This method never writes a checkpoint, receives a return or infers native
        health, finalized audio, worker exit or restoration.
        """
        acquired = False
        try:
            began = time.monotonic()
            require(self._lock.acquire(blocking=False))
            acquired = True
            require(self.phase == "completed")
            expected, plan, started = self._started_context
            require(self.expected is expected and self.plan is plan)
            require(type(plan) is native.owner.Plan and started.count == 3)
            require(started.expected == expected and started.tip is None)
            end = min(
                began + 2, self.guard.finish_by, self.native_binding.finish_by, plan.finish_by
            )
            before_exits = self.guard.check()
            state = self._state()
            require(state.expected == expected and state.count >= started.count)
            with host.protected._private_directory(self.directory, exclusive=False) as fd:
                require(host.identity(os.fstat(fd))[:6] == self.directory_identity)
                raw = host.protected.evidence.read_bytes(
                    fd, "0002.json", limit=host.MAX_BYTES, deadline=end
                )
                require(hashlib.sha256(raw).hexdigest() == started.sha256)
            directory_identity = self._progress_identity(directory)
            require(directory is not None)
            previous_tip = None
            last_observed = None
            prior_context = self._progress_context
            if prior_context is None:
                require(state == started and state.tip is None)
            else:
                original_directory, original_identity, previous_tip, last_observed = prior_context
                require((directory, directory_identity) == (original_directory, original_identity))
                require(previous_tip is None or state.tip is not None)
            collector = local.protected.Collector(self.binding.projection.host)

            def progress():
                context = local.checkpoints._context(directory, collector, expected)
                with host.protected._private_directory(directory, exclusive=False) as fd:
                    require(host.identity(os.fstat(fd))[:6] == directory_identity)
                    return local.checkpoints._read(
                        fd, collector, expected, context, state.tip, end, previous_tip
                    )

            previous = progress()
            finalizing = began >= plan.stop_at
            result = collector.active(expected, finalizing=finalizing, previous=previous)
            if last_observed is not None:
                local.checkpoints._follows(last_observed, result.progress)
            require(progress() == previous)
            require(self._progress_identity(directory) == directory_identity)
            require(self.guard.check() == before_exits)
            require(self._state() == state and self.phase == "completed")
            require(self._started_context == (expected, plan, started))
            require(self.expected is expected and self.plan is plan)
            require(self._progress_context is prior_context)
            ended = time.monotonic()
            require(began <= ended < end)
            require(finalizing or ended < plan.stop_at)
            self._progress_context = (directory, directory_identity, state.tip, result.progress)
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self._lock.release()

    def completed(self, *, progress_directory=None):
        try:
            progress_identity = self._progress_identity(progress_directory)
            progress_context = self._progress_context
            acknowledged_tip = None
            if progress_context is not None:
                # An intermediate read may not be forgotten when finalizing.
                # Require its original directory and a returned durable chain
                # that reaches at least the last observation, even if that
                # observation had not been published when read_progress returned.
                began = time.monotonic()
                end = min(began + 2, self.native_binding.finish_by, self.plan.finish_by)
                directory, identity, previous_tip, observed = progress_context
                require((progress_directory, progress_identity) == (directory, identity))
                state = self._state()
                acknowledged_tip = state.tip
                require(acknowledged_tip is not None)
                collector = local.protected.Collector(self.binding.projection.host)
                context = local.checkpoints._context(directory, collector, self.expected)
                with host.protected._private_directory(directory, exclusive=False) as fd:
                    require(host.identity(os.fstat(fd))[:6] == identity)
                    previous = local.checkpoints._read(
                        fd, collector, self.expected, context, state.tip, end, previous_tip
                    )
                local.checkpoints._follows(observed, previous)
                require(self._state() == state and began <= time.monotonic() < end)
            result = super().completed(progress_directory=progress_directory)
            self.guard.check()
            state = self._state(closed=True)
            require(state.acknowledgment == result.acknowledgment)
            require(self._progress_identity(progress_directory) == progress_identity)
            require(self._progress_context is progress_context)
            require(progress_context is None or state.tip == acknowledged_tip)
            require(time.monotonic() < min(self.native_binding.finish_by, self.plan.finish_by))
            self.completion = result
            self._completion_context = (
                result,
                self.expected,
                self.plan,
                state,
                progress_directory,
                progress_identity,
            )
            return result
        except BaseException as error:
            self._fail(error)

    @staticmethod
    def _progress_identity(directory):
        if directory is None:
            return None
        with host.protected._private_directory(directory, exclusive=False) as fd:
            return host.identity(os.fstat(fd))[:6]

    def recheck_completed(self):
        """Fresh files under the original timely completion, before exit collection.

        Does not receive another frame or adopt a disk receipt, changed artifact,
        new progress tail, process exit or renewed recording budget. Requires
        this original Relay and returned completion; copied Completion values
        alone are insufficient. The retained attachment's fixed finish bound
        limits this read-only tail, and the full read must fit within two seconds.
        Independent outer supervision is still needed for blocked kernel I/O.
        """
        acquired = False
        try:
            require(self._lock.acquire(blocking=False))
            acquired = True
            require(self.phase == "closed")
            began = time.monotonic()
            end = min(began + 2, self.guard.finish_by)
            self.guard.check()
            original = self._completion_context
            result, expected, plan, state, directory, directory_identity = original
            require(self.completion is result and type(result) is local.Completion)
            require(self.expected == expected and self.plan == plan)
            require(self._state(closed=True) == state)
            require(state.expected == expected and state.acknowledgment == result.acknowledgment)
            require(result.native_return_sha256 == state.acknowledgment.completion_sha256)
            require(type(result.stopped_raw) is bytes)
            stopped = json.loads(result.stopped_raw)
            require(host.encode(stopped) == result.stopped_raw)
            require(host.checksum(stopped) == state.acknowledgment.stopped_sha256)
            require(self._progress_identity(directory) == directory_identity)
            collector = local.protected.Collector(self.binding.projection.host)
            if state.tip is None:
                require(directory is None)
            else:
                require(directory is not None)
                local.checkpoints.load_progress(
                    directory, collector, expected, expected_tip=state.tip
                )
            collected = collector.finalized(
                expected,
                stopped=stopped,
                acknowledgment=result.acknowledgment,
                previous=result.collected.progress,
            )
            # An internally valid, same-sized replacement WAV or sidecar is
            # still not the artifact bound to the actual native completion.
            require(collected == result.collected)
            require(self._progress_identity(directory) == directory_identity)
            self.guard.check()
            require(self._state(closed=True) == state)
            require(self._completion_context is original and self.completion is result)
            require(self.expected == expected and self.plan == plan)
            require(began <= time.monotonic() < end)
            return collected
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self._lock.release()


if __name__ == "__main__":
    raise SystemExit("Private relayed-return join only; no installed host plan enabled.")
