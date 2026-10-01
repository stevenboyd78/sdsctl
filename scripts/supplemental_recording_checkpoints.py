#!/usr/bin/env python3
"""Private bounded recording-progress persistence; no lifecycle authority.

The host must retain the returned tip in independent durable evidence before
using it after restart. Missing, additional or partial entries are uncertainty,
never permission to repair/truncate the chain or replay a recording operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import supplemental_recording_protected as protected
from supplemental_handoff_files import identity
from supplemental_handoff_policy import checksum, encode
from supplemental_recording_handoff import Files

MAX_ENTRIES = 192  # One-second observations through a <=180-second finite run.
MAX_ENTRY_BYTES = 16 * 1024
MAX_SECONDS = 5.0
require = protected.require
evidence = protected.evidence
monitor = protected.monitor


@dataclass(frozen=True)
class Tip:
    count: int
    sha256: str

    def __post_init__(self):
        evidence.number(self.count, positive=True)
        require(self.count <= MAX_ENTRIES)
        evidence.digest(self.sha256)


def _follows(previous: monitor.Observation, current: monitor.Observation) -> None:
    # Both are already strictly decoded under the same sealed contract/expectation.
    require(not (previous.stage == "finalizing" and current.stage == "recording"))
    require(
        monitor.PUBLICATIONS.index(previous.publication)
        <= monitor.PUBLICATIONS.index(current.publication)
    )
    monitor._progress(previous.wav, current.wav)
    if previous.metadata is not None:
        require(current.metadata is not None)
        monitor._progress(previous.metadata, current.metadata)
    if previous.metadata_sha256 is not None:
        require(previous.metadata_sha256 == current.metadata_sha256)
    if previous.temporary_name is not None and current.temporary_name is not None:
        require(previous.temporary_name == current.temporary_name)


def _name(index: int) -> str:
    return f"{index:04d}.json"


def _context(directory: Path, collector: protected.Collector, expected):
    require(type(collector) is protected.Collector)
    collector._expected(expected)
    protected._path(str(directory))
    root = collector.stored.baseline.root
    require(not directory.is_relative_to(root) and not root.is_relative_to(directory))
    return checksum({"contract": collector.stored.contract.sha256, "expected": asdict(expected)})


def _read(fd, collector, expected, context, tip, deadline, previous_tip=None):
    require(tip is None or type(tip) is Tip)
    count = 0 if tip is None else tip.count
    require(previous_tip is None or type(previous_tip) is Tip)
    require(previous_tip is None or previous_tip.count <= count)
    before = identity(os.fstat(fd))
    names = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            require(time.monotonic() <= deadline and len(names) < MAX_ENTRIES)
            names.add(entry.name)
    require(names == {_name(i) for i in range(count)})
    previous, digest = None, context
    for index in range(count):
        name = _name(index)
        stated = os.stat(name, dir_fd=fd, follow_symlinks=False)
        require(stated.st_uid == os.geteuid() and stat.S_IMODE(stated.st_mode) == 0o600)
        raw = evidence.read_bytes(fd, name, limit=MAX_ENTRY_BYTES, deadline=deadline)
        require(identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) == identity(stated))
        entry = json.loads(
            raw, object_pairs_hook=evidence.unique, parse_constant=evidence.reject_constant
        )
        protected._mapping(entry, {"schema", "kind", "previous", "files", "progress"})
        require(type(entry["schema"]) is int and entry["schema"] == 1)
        require(entry["kind"] == "finite-recording-progress" and encode(entry) == raw)
        require(entry["previous"] == digest)
        files = Files(**protected._mapping(entry["files"], set(Files.__dataclass_fields__)))
        current = collector.load_progress(
            encode(entry["progress"]), expected_files=files, expected=expected
        )
        if previous is not None:
            _follows(previous, current)
        previous, digest = current, hashlib.sha256(raw).hexdigest()
        if previous_tip is not None and index + 1 == previous_tip.count:
            require(digest == previous_tip.sha256)
    require(tip is None or digest == tip.sha256)
    require(identity(os.fstat(fd)) == before and time.monotonic() <= deadline)
    return previous


def load_progress(
    directory: Path,
    collector: protected.Collector,
    expected: evidence.RecordingExpectation,
    *,
    expected_tip: Tip,
    previous_tip: Tip | None = None,
) -> monitor.Observation:
    """Reload the chain, optionally pinning a previously acknowledged prefix too.

    The independent host uses previous_tip before accepting a newly returned
    tail. A rehashed replacement history cannot supersede its earlier pin.
    """
    try:
        require(type(expected_tip) is Tip)
        deadline = time.monotonic() + MAX_SECONDS
        context = _context(directory, collector, expected)
        with protected._private_directory(directory, exclusive=False) as fd:
            result = _read(fd, collector, expected, context, expected_tip, deadline, previous_tip)
        require(result is not None and time.monotonic() <= deadline)
        return result
    except Exception:
        raise protected.UnconfirmedProtection(protected.MESSAGE) from None


def append_progress(
    directory: Path,
    collector: protected.Collector,
    expected: evidence.RecordingExpectation,
    collected: protected.Collected,
    *,
    previous_tip: Tip | None,
) -> Tip:
    """Durably append one qualified observation without replacing earlier evidence.

    Passing None requires an empty directory. Publication uncertainty leaves the
    chain untouched for review; the caller must not infer a returned/pinned tip.
    This writes only evidence, never start/stop intent or an operator request.
    """
    try:
        deadline = time.monotonic() + MAX_SECONDS
        context = _context(directory, collector, expected)
        raw_progress = collector.dump_progress(collected, expected)
        with protected._private_directory(directory, exclusive=True) as fd:
            previous = _read(fd, collector, expected, context, previous_tip, deadline)
            count = 0 if previous_tip is None else previous_tip.count
            require(count < MAX_ENTRIES)
            if previous is not None:
                _follows(previous, collected.progress)
            raw = encode(
                {
                    "schema": 1,
                    "kind": "finite-recording-progress",
                    "previous": context if previous_tip is None else previous_tip.sha256,
                    "files": asdict(collected.files),
                    "progress": json.loads(raw_progress),
                }
            )
            require(len(raw) <= MAX_ENTRY_BYTES and time.monotonic() <= deadline)
            output = os.open(
                _name(count),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(output, "wb") as stream:
                require(stream.write(raw) == len(raw))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
            tip = Tip(count + 1, hashlib.sha256(raw).hexdigest())
            require(_read(fd, collector, expected, context, tip, deadline) == collected.progress)
        require(time.monotonic() <= deadline)
        return tip
    except Exception:
        raise protected.UnconfirmedProtection(protected.MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Offline progress evidence only; no live recording or replay authority.")
