#!/usr/bin/env python3
"""Offline, read-only final-artifact proof for one future finite recording case.

Not a host-handoff adapter, recorder, playback client, or operational CLI. The
existing handoff guards still require idle recordings and unchanged inventories.
Call only after a separately bound recording owner has stopped and finalized;
an apparently valid WAV cannot establish that its writer has exited.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from supplemental_handoff_files import DIRECTORY, FileEvidence, identity, inventory

MAX_SECONDS = 5.0
MAX_RECORDING_SECONDS = 180
MAX_METADATA_BYTES = 65536
MAX_WAV_BYTES = 44 + MAX_RECORDING_SECONDS * 8000 * 2
RELIABILITY = (
    "packets_lost",
    "duplicate_packets",
    "late_packets",
    "malformed_packets",
    "unexpected_source_packets",
    "ssrc_mismatch_packets",
    "timestamp_discontinuities",
    "receive_errors",
    "callback_errors",
)
SINK = (
    "bytes_submitted",
    "bytes_written",
    "bytes_dropped",
    "queued_bytes",
    "underflows",
    "overflows",
    "callback_statuses",
)
SNAPSHOT = (
    "status",
    "active",
    "recording",
    "metadata",
    "started_at",
    "stopped_at",
    "elapsed_seconds",
    "packets",
    "samples",
    "audio_duration_seconds",
    "reliability",
    "sink",
    "completed_recordings",
    "closed",
    "error",
)
BOUNDARY_FIELDS = frozenset(
    {
        "mode",
        "system",
        "department",
        "site",
        "channel",
        "frequency",
        "modulation",
        "service_type",
        "talkgroup_id",
        "unit_id",
    }
)


class UnconfirmedRecording(ValueError):
    """No path, radio identity, payload or arbitrary exception text is exposed."""


def require(value: bool) -> None:
    if not value:
        raise UnconfirmedRecording("Recording artifact evidence is unconfirmed; preserve the case.")


def number(value: Any, *, positive: bool = False) -> int:
    require(type(value) is int and (value > 0 if positive else value >= 0) and value < 2**63)
    return value


def digest(value: Any) -> None:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def timestamp(value: Any) -> datetime:
    require(type(value) is str and 1 <= len(value) <= 40)
    try:
        parsed = datetime.fromisoformat(value)
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
        return parsed.astimezone(UTC)
    except Exception:
        raise UnconfirmedRecording(
            "Recording timestamp is unconfirmed; preserve the case."
        ) from None


def template(case: str) -> str:
    require(type(case) is str and re.fullmatch(r"[0-9a-f]{32}", case) is not None)
    return "sdsctl-acceptance-" + case + "-{timestamp}.wav"


def filename(case: str, started_at: str) -> str:
    """Use the writer's required timestamp template, in its start receipt zone."""
    selected = template(case)
    timestamp(started_at)
    return selected.format(timestamp=datetime.fromisoformat(started_at).strftime("%Y%m%d-%H%M%S"))


@dataclass(frozen=True)
class RecordingBaseline:
    """Privately retained pre-start file inventory, never an exclusion list.

    Does not attest to directory topology or inode continuity between captures.
    File bytes, size, permissions and owners are compared; each capture also
    refuses unsafe links and detects mutations during its bounded observation.
    """

    case: str
    root: Path
    root_identity: tuple[int, ...]
    files: tuple[tuple[str, FileEvidence], ...]


@dataclass(frozen=True)
class RecordingExpectation:
    """Bind the one start receipt to a separately verified daemon generation."""

    case: str
    generation: str
    audio_endpoint_sha256: str
    started_at: str

    def __post_init__(self) -> None:
        template(self.case)
        digest(self.generation)
        digest(self.audio_endpoint_sha256)
        timestamp(self.started_at)


@dataclass(frozen=True)
class FinalizedRecording:
    """Content-only proof, not process exit, recovery, audibility or trial success."""

    case: str
    generation: str
    wav_sha256: str
    metadata_sha256: str
    samples: int
    packets: int
    audio_seconds: float
    old_files: int


@contextmanager
def opened_root(root: Path, *, deadline: float) -> Iterator[int]:
    """Keep no-follow ancestor descriptors through the entire observation."""
    opened: list[tuple[int, str, int, tuple[int, ...]]] = []
    anchor = -1
    try:
        require(type(root) is type(Path()) and root.is_absolute() and root != Path("/"))
        require(all(part not in (".", "..") for part in root.parts))
        require(len(root.parts) <= 32)
        anchor = os.open("/", DIRECTORY)
        parent = anchor
        for name in root.parts[1:]:
            child = os.open(name, DIRECTORY, dir_fd=parent)
            try:
                before = identity(os.fstat(child))
            except BaseException:
                os.close(child)
                raise
            opened.append((parent, name, child, before))
            parent = child
        yield parent
        for parent, name, child, before in opened:
            current = identity(os.fstat(child))
            named = identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            # Ancestor siblings may change. Selected root content must not change
            # during this final observation (it can differ from pre-start).
            width = len(before) if child == opened[-1][2] else 6
            require(current[:width] == named[:width] == before[:width])
        require(time.monotonic() <= deadline)
    finally:
        for _, _, child, _ in reversed(opened):
            os.close(child)
        if anchor >= 0:
            os.close(anchor)


def capture_baseline(root: Path, case: str) -> RecordingBaseline:
    """Capture before any recording start; refuse an existing reserved artifact."""
    try:
        prefix = template(case).split("{timestamp}", 1)[0]
        with opened_root(root, deadline=time.monotonic() + MAX_SECONDS) as fd:
            files = inventory(root, max_file_bytes=16 * 1024 * 1024)
            require(not any(Path(key).name.startswith((prefix, "." + prefix)) for key in files))
            require(len(files) <= 4094)
            return RecordingBaseline(
                case,
                root,
                identity(os.fstat(fd))[:6],
                tuple((key, FileEvidence(**value)) for key, value in files.items()),
            )
    except Exception:
        raise UnconfirmedRecording(
            "Recording baseline is unconfirmed; preserve the case."
        ) from None


def read_bytes(directory: int, name: str, *, limit: int, deadline: float) -> bytes:
    """Read exactly one stable bounded regular file relative to the held root."""
    fd = -1
    try:
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        require(0 < before.st_size <= limit)
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory,
        )
        require(identity(os.fstat(fd)) == identity(before))
        result = bytearray()
        while True:
            require(time.monotonic() <= deadline)
            chunk = os.read(fd, min(65536, before.st_size + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
            require(len(result) <= before.st_size)
        require(len(result) == before.st_size)
        require(identity(os.fstat(fd)) == identity(before))
        require(
            identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == identity(before)
        )
        return bytes(result)
    finally:
        if fd >= 0:
            os.close(fd)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject_constant(_value: str) -> None:
    require(False)


def mapping(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    require(type(value) is dict and set(value) == set(keys))
    return value


def duration(value: Any) -> float:
    require(type(value) in (int, float) and 0 < value <= MAX_RECORDING_SECONDS)
    require(math.isfinite(value))
    return float(value)


def _validate_payloads(
    wav: bytes,
    metadata: bytes,
    stopped: dict[str, Any],
    expected: RecordingExpectation,
) -> tuple[int, int]:
    """Strict writer-format validation, not a permissive general WAV player."""
    mapping(stopped, SNAPSHOT)
    require(type(expected) is RecordingExpectation)
    require(stopped.get("status") == "stopped" and stopped.get("active") is False)
    require(type(stopped.get("closed")) is bool and stopped.get("error") is None)
    require(number(stopped.get("completed_recordings")) == 1)
    name = filename(expected.case, expected.started_at)
    require(stopped.get("recording") == name and stopped.get("metadata") == name + ".json")
    require(timestamp(stopped.get("started_at")) == timestamp(expected.started_at))
    require(timestamp(stopped.get("stopped_at")) >= timestamp(expected.started_at))
    samples, packets = (
        number(stopped.get("samples"), positive=True),
        number(stopped.get("packets"), positive=True),
    )
    require(packets <= samples and samples <= 8000 * MAX_RECORDING_SECONDS)
    require(duration(stopped.get("audio_duration_seconds")) == samples / 8000)
    duration(stopped.get("elapsed_seconds"))
    sink = mapping(stopped.get("sink"), SINK)
    for key in SINK:
        number(sink[key])
        require(sink[key] == (samples * 2 if key in ("bytes_submitted", "bytes_written") else 0))
    reliability = mapping(stopped.get("reliability"), RELIABILITY)
    for value in reliability.values():
        number(value)
    require(type(wav) is bytes and len(wav) == 44 + samples * 2 and len(wav) <= MAX_WAV_BYTES)
    require(
        struct.unpack("<4sI4s4sIHHIIHH4sI", wav[:44])
        == (
            b"RIFF",
            len(wav) - 8,
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            8000,
            16000,
            2,
            16,
            b"data",
            samples * 2,
        )
    )
    require(type(metadata) is bytes and 0 < len(metadata) <= MAX_METADATA_BYTES)
    value = mapping(
        json.loads(metadata, object_pairs_hook=unique, parse_constant=reject_constant),
        (
            "schema",
            "version",
            "recording",
            "source",
            "boundaries",
            "statistics",
            "error",
        ),
    )
    require(
        value["schema"] == "sds200.recording-metadata"
        and type(value["version"]) is int
        and value["version"] == 1
    )
    require(value["error"] is None)
    recording = mapping(
        value["recording"], ("file", "format", "sample_rate_hz", "channels", "sample_width_bytes")
    )
    require(recording["file"] == name and recording["format"] == "wav")
    require(
        all(
            type(recording[k]) is int and recording[k] == v
            for k, v in (
                ("sample_rate_hz", 8000),
                ("channels", 1),
                ("sample_width_bytes", 2),
            )
        )
    )
    source = value["source"]
    require(type(source) is dict and set(source) in ({"endpoint"}, {"endpoint", "scanner"}))
    require(type(source["endpoint"]) is str and 0 < len(source["endpoint"]) <= 4096)
    require(
        hashlib.sha256(source["endpoint"].encode()).hexdigest() == expected.audio_endpoint_sha256
    )
    if "scanner" in source:
        require(type(source["scanner"]) is str and 0 < len(source["scanner"]) <= 128)
    boundaries = mapping(value["boundaries"], ("started", "stopped"))
    for key in ("started", "stopped"):
        boundary = mapping(boundaries[key], ("at", "state"))
        require(timestamp(boundary["at"]) == timestamp(stopped[key + "_at"]))
        require(type(boundary["state"]) is dict)
        require(set(boundary["state"]) <= BOUNDARY_FIELDS)
        require(all(type(v) is str for v in boundary["state"].values()))
    statistics = mapping(
        value["statistics"],
        (
            "elapsed_seconds",
            "packets",
            "samples",
            "audio_duration_seconds",
            "reliability",
        ),
    )
    for key in ("packets", "samples"):
        require(number(statistics[key], positive=True) == stopped[key])
    for key in ("elapsed_seconds", "audio_duration_seconds"):
        require(duration(statistics[key]) == stopped[key])
    meta_reliability = mapping(statistics["reliability"], RELIABILITY)
    require(all(number(meta_reliability[k]) == reliability[k] for k in RELIABILITY))
    return samples, packets


def verify_finalized(
    baseline: RecordingBaseline,
    expected: RecordingExpectation,
    *,
    generation: str,
    stopped: dict[str, Any],
) -> FinalizedRecording:
    """Full unchanged-old inventory plus exactly one finalized WAV/JSON pair.

    generation must come from the separately authenticated stop receipt, not be
    inferred from a pathname or the WAV. This does not authenticate that receipt
    or dispatch recovery. Any uncertainty returns no proof and changes no file.
    """
    try:
        require(type(baseline) is RecordingBaseline and type(expected) is RecordingExpectation)
        require(baseline.case == expected.case and generation == expected.generation)
        require(type(baseline.files) is tuple and len(baseline.files) <= 4094)
        require(type(baseline.root_identity) is tuple and len(baseline.root_identity) == 6)
        require(all(type(v) is int and v >= 0 for v in baseline.root_identity))
        old = dict(baseline.files)
        require(
            len(old) == len(baseline.files) and all(type(v) is FileEvidence for v in old.values())
        )
        for key, value in old.items():
            require(type(key) is str and 0 < len(key) <= 4096)
            require(
                not key.startswith("/") and all(p not in ("", ".", "..") for p in key.split("/"))
            )
            require(not any(ord(c) < 32 for c in key))
            number(value.size)
            require(value.size <= 16 * 1024 * 1024)
            digest(value.sha256)
            number(value.mode)
            require(value.mode <= 0o7777)
            number(value.uid)
            number(value.gid)
        name = filename(expected.case, expected.started_at)
        require(name not in old and name + ".json" not in old)
        deadline = time.monotonic() + MAX_SECONDS
        with opened_root(baseline.root, deadline=deadline) as fd:
            require(identity(os.fstat(fd))[:6] == baseline.root_identity)
            after = inventory(baseline.root, max_file_bytes=16 * 1024 * 1024)
            require(set(after) == set(old) | {name, name + ".json"})
            require(all(FileEvidence(**after[key]) == value for key, value in old.items()))
            wav = read_bytes(fd, name, limit=MAX_WAV_BYTES, deadline=deadline)
            metadata = read_bytes(fd, name + ".json", limit=MAX_METADATA_BYTES, deadline=deadline)
            wav_sha, meta_sha = (
                hashlib.sha256(wav).hexdigest(),
                hashlib.sha256(metadata).hexdigest(),
            )
            require(
                wav_sha == after[name]["sha256"] and meta_sha == after[name + ".json"]["sha256"]
            )
            samples, packets = _validate_payloads(wav, metadata, stopped, expected)
            require(inventory(baseline.root, max_file_bytes=16 * 1024 * 1024) == after)
            proof = FinalizedRecording(
                expected.case,
                generation,
                wav_sha,
                meta_sha,
                samples,
                packets,
                samples / 8000,
                len(old),
            )
        return proof
    except Exception:
        raise UnconfirmedRecording(
            "Recording artifact evidence is unconfirmed; preserve the case."
        ) from None


if __name__ == "__main__":
    raise SystemExit(
        "Offline recording evidence component; no recording, scanner or App operation started."
    )
