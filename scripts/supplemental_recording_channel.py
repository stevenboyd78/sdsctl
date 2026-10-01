#!/usr/bin/env python3
"""Offline private native-return channel for an exact owned Linux child.

Anonymous Unix SEQPACKET only: no listening path, public API, process spawn,
signals, scanner command or recording dispatch. Per-message kernel credentials
and a live-bound pidfd identify the sender; source authenticity still requires
the separately qualified fixed child launcher and sealed package inventory.
"""

from __future__ import annotations

import json
import os
import select
import socket
import struct
import time
from dataclasses import asdict, dataclass

import supplemental_recording_owner as owner
import supplemental_recording_protected as protected
from supplemental_handoff_policy import checksum, clock, encode

MAX_BYTES = 8192
SEND_SECONDS = 0.2
MESSAGE = "Native recording return is unconfirmed; preserve the case and do not retry."
CREDENTIALS = struct.Struct("3i")


class UnconfirmedReturn(ValueError):
    """Fixed failure, not evidence of native successful return or process exit."""


def require(value):
    if not value:
        raise UnconfirmedReturn(MESSAGE)


@dataclass(frozen=True)
class Binding:
    stored: protected.StoredBaseline
    generation: str
    projection_sha256: str
    source_sha256: str
    start_by: float
    finish_by: float

    def payload(self):
        protected.Collector(self.stored)  # Strict original manifest, no filesystem read.
        for value in (self.generation, self.projection_sha256, self.source_sha256):
            protected.evidence.digest(value)
        clock(self.start_by)
        clock(self.finish_by)
        require(0 < self.start_by < self.finish_by)
        require(self.finish_by - self.start_by < self.stored.contract.maximum_recording_seconds)
        return {
            "manifest": self.stored.manifest_sha256,
            "contract": self.stored.contract.sha256,
            "generation": self.generation,
            "projection": self.projection_sha256,
            "source": self.source_sha256,
            "start_by": self.start_by,
            "finish_by": self.finish_by,
        }


def _socket(value):
    require(type(value) is socket.socket and value.fileno() >= 0)
    require(value.family == socket.AF_UNIX)
    require(value.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_SEQPACKET)
    require(value.getsockname() in ("", b"") and value.getpeername() in ("", b""))


def pair():
    """Create two private non-inheritable endpoints BEFORE launching the child.

    Caller must close the unused endpoint in each process and pass only the
    sender to its source-pinned child. PASSCRED is enabled before any send; Unix
    socketpair SO_PEERCRED alone would identify the creating parent after fork.
    """
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        require(not left.get_inheritable() and not right.get_inheritable())
        _socket(left)
        _socket(right)
        return left, right
    except BaseException:
        left.close()
        right.close()
        raise


def _decode(raw, binding, phase, expected):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    value = json.loads(
        raw,
        object_pairs_hook=protected.evidence.unique,
        parse_constant=protected.evidence.reject_constant,
    )
    protected._mapping(value, {"schema", "kind", "binding", "phase", "body", "at"})
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(value["kind"] == "finite-recording-native-return" and encode(value) == raw)
    require(value["binding"] == binding.payload() and value["phase"] == phase)
    clock(value["at"])
    require(value["at"] < (binding.start_by if phase == "started" else binding.finish_by))
    body = value["body"]
    if phase == "started":
        protected._mapping(body, {"expected", "plan"})
        current = protected.evidence.RecordingExpectation(
            **protected._mapping(
                body["expected"], set(protected.evidence.RecordingExpectation.__dataclass_fields__)
            )
        )
        plan = owner.Plan(**protected._mapping(body["plan"], set(owner.Plan.__dataclass_fields__)))
        protected.Collector(binding.stored)._expected(current)
        require(current.generation == plan.generation == binding.generation)
        require(
            plan.case == current.case
            and plan.audio_endpoint_sha256 == current.audio_endpoint_sha256
        )
        require(plan.prepared_at <= value["at"] < plan.start_by <= binding.start_by)
        require(plan.finish_by <= binding.finish_by)
        return value, current
    require(phase == "completed" and type(expected) is protected.evidence.RecordingExpectation)
    protected._mapping(body, {"stopped", "artifact"})
    stopped = owner.FiniteRecordingOwner._snapshot(body["stopped"])
    name = protected.evidence.filename(expected.case, expected.started_at)
    require(
        stopped["status"] == "stopped"
        and stopped["active"] is False
        and stopped["closed"] is False
        and stopped["error"] is None
    )
    require(stopped["completed_recordings"] == 1 and stopped["started_at"] == expected.started_at)
    require(stopped["recording"] == name and stopped["metadata"] == name + ".json")
    require(
        protected.evidence.timestamp(stopped["stopped_at"])
        >= protected.evidence.timestamp(expected.started_at)
    )
    artifact = protected.evidence.FinalizedRecording(
        **protected._mapping(
            body["artifact"], set(protected.evidence.FinalizedRecording.__dataclass_fields__)
        )
    )
    require(artifact.case == expected.case and artifact.generation == binding.generation)
    for digest in (artifact.wav_sha256, artifact.metadata_sha256):
        protected.evidence.digest(digest)
    for number in (artifact.samples, artifact.packets, artifact.old_files):
        protected.evidence.number(number)
    clock(artifact.audio_seconds)
    require(
        0
        < artifact.packets
        <= artifact.samples
        <= 8000 * binding.stored.contract.maximum_recording_seconds
    )
    require(artifact.samples == stopped["samples"] and artifact.packets == stopped["packets"])
    require(artifact.audio_seconds == stopped["audio_duration_seconds"] == artifact.samples / 8000)
    require(artifact.old_files == len(binding.stored.baseline.files))
    require(stopped["elapsed_seconds"] <= binding.stored.contract.maximum_recording_seconds)
    require(
        all(
            value == (artifact.samples * 2 if key in ("bytes_written", "bytes_submitted") else 0)
            for key, value in stopped["sink"].items()
        )
    )
    return value, expected


class Sender:
    """Two bounded, non-retry publications, only from successful native call sites.

    A source-pinned launcher must call started AFTER owner.start returns, and
    completed AFTER the assembled native run returns with verified cleanup.
    This transport cannot establish those call-site facts on its own.
    """

    def __init__(self, channel, binding):
        require(type(binding) is Binding)
        _socket(channel)
        binding.payload()
        require(0 < binding.start_by - time.monotonic() <= 10)
        self.channel, self.binding = channel, binding
        self.phase, self.expected, self.pid = "started", None, os.getpid()

    def send(self, phase, body):
        try:
            require(
                self.pid == os.getpid()
                and phase == self.phase
                and phase in ("started", "completed")
            )
            self.phase = "unconfirmed"  # Consume before validation or send.
            now = time.monotonic()
            raw = encode(
                {
                    "schema": 1,
                    "kind": "finite-recording-native-return",
                    "binding": self.binding.payload(),
                    "phase": phase,
                    "body": body,
                    "at": now,
                }
            )
            _, expected = _decode(raw, self.binding, phase, self.expected)
            deadline = min(
                now + SEND_SECONDS,
                self.binding.start_by if phase == "started" else self.binding.finish_by,
            )
            require(deadline > time.monotonic())
            self.channel.settimeout(deadline - time.monotonic())
            require(self.channel.send(raw) == len(raw) and time.monotonic() < deadline)
            self.expected = expected
            self.phase = "completed" if phase == "started" else "closed"
        except Exception:
            self.phase = "unconfirmed"
            raise UnconfirmedReturn(MESSAGE) from None

    def started(self, expected, plan):
        require(
            type(expected) is protected.evidence.RecordingExpectation and type(plan) is owner.Plan
        )
        self.send("started", {"expected": asdict(expected), "plan": asdict(plan)})

    def completed(self, artifact, stopped):
        require(type(artifact) is protected.evidence.FinalizedRecording)
        self.send("completed", {"artifact": asdict(artifact), "stopped": stopped})


def _identity(pid):
    with open(f"/proc/{pid}/stat", "rb", buffering=0) as stream:
        raw = stream.read(4097)
    require(len(raw) <= 4096)
    prefix, delimiter, suffix = raw.rpartition(b") ")
    require(delimiter and prefix.startswith(str(pid).encode() + b" ("))
    values = suffix.split()
    require(len(values) >= 20 and values[0] in (b"R", b"S", b"D", b"T", b"t", b"I"))
    require(values[1].isdigit() and values[19].isdigit() and int(values[19]) > 0)
    return int(values[1]), int(values[19])


@dataclass(frozen=True)
class Received:
    raw: bytes
    pid: int
    start_ticks: int
    uid: int
    gid: int
    received_at: float

    @property
    def sha256(self):
        return checksum(asdict(self) | {"raw": self.raw.decode("ascii")})


class Receiver:
    """Bind a live owned child before accepting either per-message credential.

    The pidfd is retained, not reconstructed from a receipt. Receiving completion
    is NOT exit proof; guardian wait/reap and host container-init exit are separate.
    This object never retries a timed-out/malformed/missing native return.
    """

    def __init__(self, channel, binding, *, pid, start_ticks, uid, gid):
        self.fd = -1
        try:
            require(type(binding) is Binding)
            binding.payload()
            require(0 < binding.start_by - time.monotonic() <= 10)
            _socket(channel)
            require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1)
            for n in (pid, start_ticks, uid, gid):
                protected.evidence.number(n)
            require(pid > 1 and start_ticks > 0 and pid != os.getpid())
            self.fd = os.pidfd_open(pid)
            require(_identity(pid) == (os.getpid(), start_ticks))
            require(not select.select([self.fd], [], [], 0)[0])
            self.channel, self.binding = channel, binding
            self.pid, self.start_ticks, self.uid, self.gid = pid, start_ticks, uid, gid
            self.phase, self.expected, self.plan = "started", None, None
            self.owner_pid = os.getpid()
        except Exception:
            self.close()
            raise UnconfirmedReturn(MESSAGE) from None

    def receive(self):
        try:
            require(
                self.owner_pid == os.getpid()
                and self.fd >= 0
                and self.phase in ("started", "completed")
            )
            phase, self.phase = self.phase, "unconfirmed"
            deadline = (
                self.binding.start_by
                if phase == "started"
                else min(self.binding.finish_by, self.plan.finish_by)
            )
            require(time.monotonic() < deadline)
            self.channel.settimeout(deadline - time.monotonic())
            raw, ancillary, flags, _ = self.channel.recvmsg(
                MAX_BYTES, socket.CMSG_SPACE(CREDENTIALS.size), socket.MSG_CMSG_CLOEXEC
            )
            # recvmsg installs any supplied descriptors even if the frame will
            # be rejected. Close them before validation, including truncated
            # ancillary data; never leak a native-provided descriptor.
            for level, kind, data in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    for (descriptor,) in struct.iter_unpack("i", data[: len(data) // 4 * 4]):
                        os.close(descriptor)
            require(flags & ~socket.MSG_CMSG_CLOEXEC == 0 and len(ancillary) == 1)
            level, kind, data = ancillary[0]
            require(
                level == socket.SOL_SOCKET
                and kind == socket.SCM_CREDENTIALS
                and len(data) == CREDENTIALS.size
            )
            require(CREDENTIALS.unpack(data) == (self.pid, self.uid, self.gid))
            received = time.monotonic()
            require(received < deadline)
            value, expected = _decode(raw, self.binding, phase, self.expected)
            require(value["at"] <= received)
            if phase == "started":
                self.plan = owner.Plan(**value["body"]["plan"])
                require(received < self.plan.start_by)
            else:
                require(value["at"] >= self.plan.stop_at)
            checked = time.monotonic()
            require(received <= checked < deadline)
            require(phase != "started" or checked < self.plan.start_by)
            self.expected = expected
            self.phase = "completed" if phase == "started" else "closed"
            return Received(raw, self.pid, self.start_ticks, self.uid, self.gid, checked)
        except Exception:
            self.phase = "unconfirmed"
            raise UnconfirmedReturn(MESSAGE) from None

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


if __name__ == "__main__":
    raise SystemExit("Offline private return channel only; no child or recording is launched.")
