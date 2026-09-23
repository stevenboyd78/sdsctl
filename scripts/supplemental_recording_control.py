#!/usr/bin/env python3
"""Private ready/begin handshake and dedicated native execution, offline only.

This is not an installed operator API or source authenticator. The fixed guardian
must authenticate the bundle/launch plan and consume a durable host intent before
begin(). It must independently terminate its exact child on a hard deadline.
Socket timeouts and parent-death signals do not replace that guardian or the host
container-init exit witness. No ordinary CLI options or public socket are used.
"""

from __future__ import annotations

import ctypes
import json
import os
import select
import signal
import socket
import struct
import time
from contextlib import suppress
from dataclasses import dataclass
from threading import Event, Thread, current_thread, main_thread

import supplemental_recording_channel as returns
import supplemental_recording_launch_plan as launch
from supplemental_handoff_policy import clock, encode

MAX_BYTES = 4096
MESSAGE = "Finite recording control is unconfirmed; preserve the case and do not retry."


class UnconfirmedControl(ValueError):
    """No recording acknowledgment, exit proof or permission for another owner."""


def require(value):
    if not value:
        raise UnconfirmedControl(MESSAGE)


@dataclass(frozen=True)
class Context:
    plan: launch.LaunchPlan
    ready_by: float

    def payload(self):
        require(type(self.plan) is launch.LaunchPlan)
        p = self.plan
        launch.protected.Collector(p.stored)  # Validate original manifest; no I/O.
        require(type(p.specification) is launch.construction.Specification)
        p.specification.__post_init__()
        clock(self.ready_by)
        require(self.ready_by > 0)
        for digest in (
            p.generation,
            p.sha256,
            p.source_sha256,
            p.projection_sha256,
            p.host_plan_sha256,
            p.profile_sha256,
        ):
            launch.protected.evidence.digest(digest)
        return {
            "launch": p.sha256,
            "source": p.source_sha256,
            "projection": p.projection_sha256,
            "host_plan": p.host_plan_sha256,
            "profile": p.profile_sha256,
            "manifest": p.stored.manifest_sha256,
            "contract": p.stored.contract.sha256,
            "generation": p.generation,
            "ready_by": self.ready_by,
        }


@dataclass(frozen=True)
class Channels:
    incoming: socket.socket
    outgoing: socket.socket

    def close(self):
        self.incoming.close()
        self.outgoing.close()


def pair():
    # Two directional pairs preserve anonymous endpoints. On Linux, sending
    # from a PASSCRED-enabled endpoint can auto-bind an abstract socket name.
    # Only receivers enable PASSCRED; no receive endpoint is ever used to send.
    reports_in, reports_out = returns.pair()
    try:
        begin_in, begin_out = returns.pair()
        return Channels(reports_in, begin_out), Channels(begin_in, reports_out)
    except BaseException:
        reports_in.close()
        reports_out.close()
        raise


class _Peer:
    def __init__(self, channel, context, *, pid, start_ticks, uid, gid, child):
        self.fd = -1
        try:
            require(type(context) is Context)
            context.payload()
            remaining = context.ready_by - time.monotonic()
            require(0 < remaining <= context.plan.specification.ready_timeout)
            require(type(channel) is Channels and channel.incoming is not channel.outgoing)
            for endpoint, passcred in ((channel.incoming, 1), (channel.outgoing, 0)):
                returns._socket(endpoint)
                require(endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == passcred)
            for number in (pid, start_ticks, uid, gid):
                launch.protected.evidence.number(number)
            require(pid > 1 and pid != os.getpid() and start_ticks > 0)
            self.channel, self.context = channel, context
            self.pid, self.start_ticks, self.uid, self.gid = pid, start_ticks, uid, gid
            self.child, self.owner = child, os.getpid()
            self.fd = os.pidfd_open(pid)
            self.live()
        except Exception:
            self.close()
            raise UnconfirmedControl(MESSAGE) from None

    def live(self):
        require(self.owner == os.getpid() and self.fd >= 0)
        require(not select.select([self.fd], [], [], 0)[0])
        parent, ticks = returns._identity(self.pid)
        require(ticks == self.start_ticks)
        require(parent == os.getpid() if self.child else self.pid == os.getppid())

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _receive(peer, phase):
    peer.live()
    deadline = peer.context.ready_by
    require(time.monotonic() < deadline)
    peer.channel.incoming.settimeout(deadline - time.monotonic())
    raw, ancillary, flags, _ = peer.channel.incoming.recvmsg(
        MAX_BYTES, socket.CMSG_SPACE(returns.CREDENTIALS.size), socket.MSG_CMSG_CLOEXEC
    )
    # Even rejected/truncated SCM_RIGHTS frames install descriptors in this
    # process. Close every delivered descriptor before examining the frame.
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            for (fd,) in struct.iter_unpack("i", data[: len(data) // 4 * 4]):
                os.close(fd)
    require(flags & ~socket.MSG_CMSG_CLOEXEC == 0 and len(ancillary) == 1)
    level, kind, data = ancillary[0]
    require(level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS)
    require(len(data) == returns.CREDENTIALS.size)
    require(returns.CREDENTIALS.unpack(data) == (peer.pid, peer.uid, peer.gid))
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    value = json.loads(
        raw,
        object_pairs_hook=launch.protected.evidence.unique,
        parse_constant=launch.protected.evidence.reject_constant,
    )
    launch.protected._mapping(value, {"schema", "kind", "context", "phase", "body", "at"})
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(value["kind"] == "finite-recording-control" and value["phase"] == phase)
    require(encode(value) == raw and value["context"] == peer.context.payload())
    clock(value["at"])
    checked = time.monotonic()
    require(value["at"] <= checked < deadline)
    peer.live()
    checked = time.monotonic()
    require(checked < deadline)
    return value, returns.Received(raw, peer.pid, peer.start_ticks, peer.uid, peer.gid, checked)


def _send(peer, phase, body, *, deadline):
    peer.live()
    now = time.monotonic()
    deadline = min(deadline, peer.context.ready_by, now + returns.SEND_SECONDS)
    raw = encode(
        {
            "schema": 1,
            "kind": "finite-recording-control",
            "context": peer.context.payload(),
            "phase": phase,
            "body": body,
            "at": now,
        }
    )
    require(len(raw) <= MAX_BYTES and time.monotonic() < deadline)
    peer.channel.outgoing.settimeout(deadline - time.monotonic())
    require(peer.channel.outgoing.send(raw) == len(raw) and time.monotonic() < deadline)
    return now


def _begin(context, body, *, ready_at, message_at, now):
    launch.protected._mapping(body, {"binding", "intent_at", "intent_sha256"})
    launch.protected.evidence.digest(body["intent_sha256"])
    clock(body["intent_at"])
    require(ready_at <= body["intent_at"] <= message_at <= now < context.ready_by)
    p = context.plan
    fields = launch.protected._mapping(
        body["binding"],
        {"manifest", "contract", "generation", "projection", "source", "start_by", "finish_by"},
    )
    binding = returns.Binding(
        p.stored,
        p.generation,
        p.projection_sha256,
        p.source_sha256,
        fields["start_by"],
        fields["finish_by"],
    )
    require(binding.payload() == fields)
    require(binding.start_by - body["intent_at"] <= 10)
    require(binding.finish_by - body["intent_at"] <= p.stored.contract.maximum_recording_seconds)
    # Preserve the assembly's full native preparation/read/finalization budget.
    # No late message may extend an intent or squeeze it into a new window.
    require(now + 3 <= binding.start_by)
    require(now + 3 + p.specification.read_window_seconds + 10 <= binding.finish_by)
    return binding


class Parent(_Peer):
    """Guardian-side one-use gate; host-intent durability is a caller prerequisite.

    An intent hash alone is not authorization. The source-pinned host relay must
    have durably recorded the exact intent before calling begin(). No serialized
    caller-provided ready/return object is accepted in place of the actual peer.
    """

    def __init__(self, channel, context, **identity):
        super().__init__(channel, context, child=True, **identity)
        self.phase, self.ready = "ready", None
        self.created = time.monotonic()

    def receive_ready(self):
        try:
            require(self.phase == "ready")
            self.phase = "unconfirmed"
            value, received = _receive(self, "ready")
            require(value["body"] == {} and self.created <= value["at"])
            self.ready, self.phase = received, "begin"
            return received
        except BaseException as error:
            self.phase = "unconfirmed"
            if isinstance(error, Exception):
                raise UnconfirmedControl(MESSAGE) from None
            raise

    def begin(self, binding, *, intent_at, intent_sha256):
        receiver = None
        try:
            require(self.phase == "begin" and type(binding) is returns.Binding)
            self.phase = "unconfirmed"  # Consume before validation, binding or send.
            now = time.monotonic()
            body = {
                "binding": binding.payload(),
                "intent_at": intent_at,
                "intent_sha256": intent_sha256,
            }
            _begin(self.context, body, ready_at=self.ready.received_at, message_at=now, now=now)
            receiver = returns.Receiver(
                self.channel.incoming,
                binding,
                pid=self.pid,
                start_ticks=self.start_ticks,
                uid=self.uid,
                gid=self.gid,
            )
            _send(self, "begin", body, deadline=binding.start_by - 3)
            self.phase = "closed"
            return receiver
        except BaseException as error:
            if receiver is not None:
                receiver.close()
            self.phase = "unconfirmed"
            if isinstance(error, Exception):
                raise UnconfirmedControl(MESSAGE) from None
            raise


def _parent_death(peer):
    """Install in the actual child/main thread before runtime construction.

    Recheck parent identity around prctl to close the exited/reparented gap.
    This does not bound a living-but-stuck guardian; an external deadline remains
    mandatory, and this function never treats the signal setting as exit proof.
    """
    require(current_thread() is main_thread())
    peer.live()
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    require(prctl(1, signal.SIGKILL, 0, 0, 0) == 0)  # PR_SET_PDEATHSIG
    actual = ctypes.c_int()
    require(prctl(2, ctypes.addressof(actual), 0, 0, 0) == 0)  # PR_GET_PDEATHSIG
    require(actual.value == signal.SIGKILL)
    peer.live()


class Child(_Peer):
    def __init__(self, channel, context, **identity):
        super().__init__(channel, context, child=False, **identity)
        self.phase = "ready"

    def request_when_ready(self, trial, done):
        from supplemental_recording_assembly import NativeRecordingAssembly

        try:
            require(self.phase == "ready")
            self.phase = "unconfirmed"
            require(type(trial) is NativeRecordingAssembly)
            p = self.context.plan
            require(trial.baseline == p.stored.baseline and trial.writer == p.stored.writer)
            require(trial.generation == p.generation and trial.journal == p.specification.receipts)
            require(trial.audio_endpoint_sha256 == p.stored.contract.audio_endpoint_sha256)
            while not trial.ready:
                self.live()
                require(time.monotonic() < self.context.ready_by and not done.wait(0.02))
            trial._bindings()
            require(trial._attempted and not trial._requested.is_set())
            require(trial.manager.snapshot().status.value == "idle")
            require(not trial.acquisition.status().armed and trial.acquisition._started)
            ready_at = _send(self, "ready", {}, deadline=self.context.ready_by)
            value, received = _receive(self, "begin")
            binding = _begin(
                self.context,
                value["body"],
                ready_at=ready_at,
                message_at=value["at"],
                now=time.monotonic(),
            )
            require(not done.is_set() and trial.ready)
            trial.request_start(returns=returns.Sender(self.channel.outgoing, binding))
            require(time.monotonic() < self.context.ready_by)
            self.phase = "closed"
        except BaseException as error:
            self.phase = "unconfirmed"
            if type(trial) is NativeRecordingAssembly:
                trial.cancel()
            if isinstance(error, Exception):
                raise UnconfirmedControl(MESSAGE) from None
            raise


def execute(channel, context, *, parent_pid, parent_start_ticks, parent_uid, parent_gid):
    """Run dedicated native construction on the main thread, in an owned child.

    Inputs must already have passed load() under externally authenticated pins.
    No entrypoint, process spawning, host relay, implicit consumer or public
    operator is installed here. Caller owns a live-bound pidfd/hard timeout.
    Closing the private channel cancels a pending request; it is never retried.
    """
    peer, worker = None, None
    done, failed = Event(), Event()
    try:
        peer = Child(
            channel,
            context,
            pid=parent_pid,
            start_ticks=parent_start_ticks,
            uid=parent_uid,
            gid=parent_gid,
        )
        _parent_death(peer)
        p = context.plan
        with launch.construction.construct(
            p.specification, p.stored, p.configuration, generation=p.generation
        ) as trial:

            def control():
                try:
                    peer.request_when_ready(trial, done)
                except BaseException:
                    failed.set()

            worker = Thread(target=control, name="finite-native-control", daemon=True)
            worker.start()
            result = trial.run()
            require(not failed.is_set() and peer.phase == "closed")
            return result
    except Exception:
        raise UnconfirmedControl(MESSAGE) from None
    finally:
        done.set()
        if type(channel) is Channels:
            for endpoint in (channel.incoming, channel.outgoing):
                with suppress(OSError):
                    endpoint.shutdown(socket.SHUT_RDWR)
        if worker is not None:
            worker.join(timeout=1)
        if peer is not None:
            peer.close()
        if type(channel) is Channels:
            channel.close()
        require(worker is None or not worker.is_alive())


if __name__ == "__main__":
    raise SystemExit("Offline private native control only; no installed launcher is enabled.")
