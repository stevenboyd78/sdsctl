#!/usr/bin/env python3
"""Bounded recording-root collection and private baseline persistence; offline.

Reads actual files, never starts/stops a recorder or dispatches App recovery.
The host separately authenticates native acknowledgments and proves writer exit.
A private manifest or a good WAV cannot supply either of those facts.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import supplemental_recording_evidence as evidence
import supplemental_recording_monitor as monitor
from supplemental_handoff_files import DIRECTORY, MAX_TOTAL_BYTES, FileEvidence, identity
from supplemental_handoff_policy import checksum, encode, identifier
from supplemental_recording_handoff import Contract, Files

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 5.0
MESSAGE = "Recording protection is unconfirmed; preserve the case and its original deadline."


class UnconfirmedProtection(ValueError):
    """Fixed error without private paths, filenames, source identities or contents."""


def require(value: bool) -> None:
    if not value:
        raise UnconfirmedProtection(MESSAGE)


def _path(value: object) -> Path:
    require(type(value) is str and 0 < len(value) <= 4096)
    path = Path(value)
    require(path.is_absolute() and str(path) == value and path != Path("/"))
    require(".." not in path.parts and len(path.parts) <= 32)
    require(not any(ord(c) < 32 for c in value))
    return path


def _mapping(value: Any, names: set[str]) -> dict[str, Any]:
    require(type(value) is dict and set(value) == names)
    return value


def _files(values: Any, case: str) -> tuple[tuple[str, FileEvidence], ...]:
    require(type(values) is list and len(values) <= 4093)
    prefix = evidence.template(case).split("{timestamp}", 1)[0]
    result, total, previous = [], 0, ""
    for pair in values:
        require(type(pair) is list and len(pair) == 2)
        name, raw = pair
        require(type(name) is str and previous < name and len(name) <= 4096)
        parts = name.split("/")
        require(len(parts) <= 17 and not any(p in ("", ".", "..") for p in parts))
        require(all(len(p.encode()) <= 255 for p in parts))
        require(not any(ord(c) < 32 for c in name))
        require(not parts[-1].startswith((prefix, "." + prefix)))
        _mapping(raw, set(FileEvidence.__dataclass_fields__))
        for key in ("size", "mode", "uid", "gid"):
            evidence.number(raw[key])
        evidence.digest(raw["sha256"])
        require(raw["size"] <= 16 * 1024 * 1024 and raw["mode"] <= 0o7777)
        total += raw["size"]
        require(total <= MAX_TOTAL_BYTES)
        result.append((name, FileEvidence(**raw)))
        previous = name
    return tuple(result)


@dataclass(frozen=True)
class StoredBaseline:
    baseline: evidence.RecordingBaseline
    writer: monitor.Writer
    contract: Contract
    manifest_sha256: str


def _decode(raw: bytes) -> StoredBaseline:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_MANIFEST_BYTES)
    value = json.loads(
        raw, object_pairs_hook=evidence.unique, parse_constant=evidence.reject_constant
    )
    _mapping(
        value,
        {
            "schema",
            "kind",
            "case",
            "root",
            "root_identity",
            "files",
            "writer",
            "audio_endpoint_sha256",
            "maximum_recording_seconds",
        },
    )
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(value["kind"] == "finite-recording-baseline" and encode(value) == raw)
    identifier(value["case"], case=True)
    root = _path(value["root"])
    root_id = value["root_identity"]
    require(type(root_id) is list and len(root_id) == 6)
    for part in root_id:
        evidence.number(part)
    require(
        stat.S_ISDIR(root_id[2]) and root_id[2] <= 0o177777 and root_id[1] > 0 and root_id[5] > 0
    )
    files = _files(value["files"], value["case"])
    writer = monitor.Writer(**_mapping(value["writer"], set(monitor.Writer.__dataclass_fields__)))
    contract = Contract(
        value["case"],
        checksum({"root_identity": root_id, "files": value["files"]}),
        hashlib.sha256(os.fsencode(root)).hexdigest(),
        value["audio_endpoint_sha256"],
        checksum(asdict(writer)),
        value["maximum_recording_seconds"],
    )
    # Reserve all three possible new names before start, including the two-link
    # native metadata publication. Do not discover an exhausted observation
    # budget only after creating a recording that recovery cannot fully inspect.
    allowance = 44 + contract.maximum_recording_seconds * 8000 * 2 + 2 * evidence.MAX_METADATA_BYTES
    require(sum(item.size for _, item in files) + allowance <= MAX_TOTAL_BYTES)
    return StoredBaseline(
        evidence.RecordingBaseline(value["case"], root, tuple(root_id), files),
        writer,
        contract,
        hashlib.sha256(raw).hexdigest(),
    )


def manifest_bytes(
    baseline: evidence.RecordingBaseline,
    writer: monitor.Writer,
    endpoint_sha256: str,
    *,
    maximum_recording_seconds: int = 180,
) -> bytes:
    """Canonical private representation; hash/contract must be sealed separately."""
    try:
        require(type(baseline) is evidence.RecordingBaseline and type(writer) is monitor.Writer)
        require(type(baseline.root) is type(Path()))
        require(type(baseline.files) is tuple and len(baseline.files) <= 4093)
        raw = encode(
            {
                "schema": 1,
                "kind": "finite-recording-baseline",
                "case": baseline.case,
                "root": str(baseline.root),
                "root_identity": baseline.root_identity,
                "files": [(name, asdict(item)) for name, item in baseline.files],
                "writer": asdict(writer),
                "audio_endpoint_sha256": endpoint_sha256,
                "maximum_recording_seconds": maximum_recording_seconds,
            }
        )
        decoded = _decode(raw)
        require(decoded.baseline == baseline and decoded.writer == writer)
        return raw
    except Exception:
        raise UnconfirmedProtection(MESSAGE) from None


@contextmanager
def _private_directory(path: Path, *, exclusive: bool):
    """Hold every ancestor; content changes are allowed, identity changes are not."""
    opened, anchor = [], -1
    try:
        require(type(path) is type(Path()))
        _path(str(path))
        anchor = os.open("/", DIRECTORY)
        parent = anchor
        for name in path.parts[1:]:
            child = os.open(name, DIRECTORY, dir_fd=parent)
            try:
                before = identity(os.fstat(child))[:6]
            except BaseException:
                os.close(child)
                raise
            opened.append((parent, name, child, before))
            parent = child
        info = os.fstat(parent)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        fcntl.flock(parent, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield parent
        for parent, name, child, before in opened:
            # External siblings may create directories; the selected private
            # directory still retains its link-count guard as well as identity.
            width = 6 if child == opened[-1][2] else 5
            require(identity(os.fstat(child))[:width] == before[:width])
            require(
                identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:width]
                == before[:width]
            )
    finally:
        for _, _, child, _ in reversed(opened):
            os.close(child)
        if anchor >= 0:
            os.close(anchor)


def save_baseline(
    directory: Path,
    baseline: evidence.RecordingBaseline,
    writer: monitor.Writer,
    endpoint_sha256: str,
    *,
    maximum_recording_seconds: int = 180,
) -> StoredBaseline:
    """Exclusive one-file publication in an empty private directory; never retry.

    Failure leaves any partial/complete file for review. The returned manifest
    hash and contract need independent durable plan binding before live use.
    """
    try:
        deadline = time.monotonic() + MAX_SECONDS
        raw = manifest_bytes(
            baseline, writer, endpoint_sha256, maximum_recording_seconds=maximum_recording_seconds
        )
        require(
            not directory.is_relative_to(baseline.root)
            and not baseline.root.is_relative_to(directory)
        )
        require(evidence.capture_baseline(baseline.root, baseline.case) == baseline)
        with _private_directory(directory, exclusive=True) as fd:
            with os.scandir(fd) as entries:
                require(next(entries, None) is None)
            require(time.monotonic() <= deadline)
            output = os.open(
                "baseline.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(output, "wb") as stream:
                require(stream.write(raw) == len(raw))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
            require(evidence.capture_baseline(baseline.root, baseline.case) == baseline)
            require(_read_manifest(fd, deadline) == raw)
        require(time.monotonic() <= deadline)
        return _decode(raw)
    except Exception:
        raise UnconfirmedProtection(MESSAGE) from None


def _read_manifest(fd: int, deadline: float) -> bytes:
    directory_before = identity(os.fstat(fd))
    with os.scandir(fd) as entries:
        first = next(entries, None)
        require(first is not None and first.name == "baseline.json")
        require(next(entries, None) is None)
    info = os.stat("baseline.json", dir_fd=fd, follow_symlinks=False)
    require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
    raw = evidence.read_bytes(fd, "baseline.json", limit=MAX_MANIFEST_BYTES, deadline=deadline)
    require(identity(os.stat("baseline.json", dir_fd=fd, follow_symlinks=False)) == identity(info))
    require(identity(os.fstat(fd)) == directory_before)
    return raw


def load_baseline(
    directory: Path, *, expected_contract: Contract, expected_sha256: str
) -> StoredBaseline:
    """Read a previously sealed baseline even while current recording files grow."""
    try:
        require(type(expected_contract) is Contract)
        evidence.digest(expected_sha256)
        deadline = time.monotonic() + MAX_SECONDS
        with _private_directory(directory, exclusive=False) as fd:
            raw = _read_manifest(fd, deadline)
            require(hashlib.sha256(raw).hexdigest() == expected_sha256)
            stored = _decode(raw)
            require(stored.contract == expected_contract)
            require(
                not directory.is_relative_to(stored.baseline.root)
                and not stored.baseline.root.is_relative_to(directory)
            )
        require(time.monotonic() <= deadline)
        return stored
    except Exception:
        raise UnconfirmedProtection(MESSAGE) from None


@dataclass(frozen=True)
class Acknowledgment:
    """Externally authenticated native SUCCESS, never derived from a stop receipt.

    The trusted host must establish the source/process, fixed deadline, successful
    return and complete report. This value binds that separate evidence; this
    module cannot authenticate a digest or infer a lost return from disk contents.
    """

    case: str
    generation: str
    contract_sha256: str
    started_at: str
    stopped_sha256: str
    completion_sha256: str

    def __post_init__(self):
        identifier(self.case, case=True)
        for value in (
            self.generation,
            self.contract_sha256,
            self.stopped_sha256,
            self.completion_sha256,
        ):
            evidence.digest(value)
        evidence.timestamp(self.started_at)


@dataclass(frozen=True)
class Collected:
    files: Files
    progress: monitor.Observation | None = None
    artifact: evidence.FinalizedRecording | None = None


class Collector:
    """Read-only stage collector. Host policy still proves lifecycle/exit authority.

    previous progress is a trusted same-process or separately authenticated
    checkpoint, not arbitrary serialized client input. No capture deletes files,
    repairs headers, adopts a new baseline or fabricates native success.
    """

    def __init__(self, stored: StoredBaseline):
        try:
            require(type(stored) is StoredBaseline)
            raw = manifest_bytes(
                stored.baseline,
                stored.writer,
                stored.contract.audio_endpoint_sha256,
                maximum_recording_seconds=stored.contract.maximum_recording_seconds,
            )
            require(_decode(raw) == stored)
            self._stored = stored
        except Exception:
            raise UnconfirmedProtection(MESSAGE) from None

    def _expected(self, expected: evidence.RecordingExpectation):
        require(type(expected) is evidence.RecordingExpectation)
        require(expected.case == self.stored.contract.case_id)
        require(expected.audio_endpoint_sha256 == self.stored.contract.audio_endpoint_sha256)

    @property
    def stored(self) -> StoredBaseline:
        return self._stored

    def pristine(self) -> Collected:
        try:
            require(
                evidence.capture_baseline(self.stored.baseline.root, self.stored.baseline.case)
                == self.stored.baseline
            )
            return Collected(
                Files(self.stored.contract.sha256, "pristine", self.stored.contract.baseline_sha256)
            )
        except Exception:
            raise UnconfirmedProtection(MESSAGE) from None

    def active(
        self,
        expected: evidence.RecordingExpectation,
        *,
        finalizing: bool = False,
        previous: monitor.Observation | None = None,
    ) -> Collected:
        try:
            self._expected(expected)
            require(type(finalizing) is bool)
            progress = monitor.observe(
                self.stored.baseline,
                expected,
                generation=expected.generation,
                writer=self.stored.writer,
                stage="finalizing" if finalizing else "recording",
                previous=previous,
            )
            proof = checksum(
                {
                    "expected": asdict(expected),
                    "progress": asdict(progress),
                    "contract": self.stored.contract.sha256,
                }
            )
            return Collected(
                Files(
                    self.stored.contract.sha256,
                    "finalizing" if finalizing else "active",
                    proof,
                    expected.generation,
                ),
                progress,
            )
        except Exception:
            raise UnconfirmedProtection(MESSAGE) from None

    def dump_progress(self, collected: Collected, expected: evidence.RecordingExpectation) -> bytes:
        """Export only the progress whose digest is in a qualified policy sample.

        Caller must durably publish these bytes and bind that digest in its host
        journal. This function writes nothing and supplies no replay authority.
        """
        try:
            require(type(collected) is Collected and collected.artifact is None)
            require(type(collected.progress) is monitor.Observation)
            raw = encode(
                {
                    "expected": asdict(expected),
                    "progress": asdict(collected.progress),
                    "contract": self.stored.contract.sha256,
                }
            )
            require(
                self.load_progress(raw, expected_files=collected.files, expected=expected)
                == collected.progress
            )
            return raw
        except Exception:
            raise UnconfirmedProtection(MESSAGE) from None

    def load_progress(
        self, raw: bytes, *, expected_files: Files, expected: evidence.RecordingExpectation
    ) -> monitor.Observation:
        """Decode only a checkpoint pinned by independently retained host evidence."""
        try:
            self._expected(expected)
            require(type(raw) is bytes and 0 < len(raw) <= 8192)
            require(
                type(expected_files) is Files and expected_files.stage in ("active", "finalizing")
            )
            require(
                expected_files.contract_sha256 == self.stored.contract.sha256
                and expected_files.generation == expected.generation
            )
            require(hashlib.sha256(raw).hexdigest() == expected_files.evidence_sha256)
            value = json.loads(
                raw, object_pairs_hook=evidence.unique, parse_constant=evidence.reject_constant
            )
            _mapping(value, {"contract", "expected", "progress"})
            require(
                encode(value) == raw
                and value["contract"] == self.stored.contract.sha256
                and value["expected"] == asdict(expected)
            )
            item = _mapping(value["progress"], set(monitor.Observation.__dataclass_fields__))
            require(item["case"] == expected.case and item["generation"] == expected.generation)
            require(
                item["binding_sha256"]
                == checksum(
                    {
                        "expected": asdict(expected),
                        "root_identity": self.stored.baseline.root_identity,
                    }
                )
            )
            writer = monitor.Writer(
                **_mapping(item["writer"], set(monitor.Writer.__dataclass_fields__))
            )
            require(writer == self.stored.writer)
            evidence.number(item["old_files"])
            require(item["old_files"] == len(self.stored.baseline.files))
            require(
                item["stage"] == ("recording" if expected_files.stage == "active" else "finalizing")
            )
            publication = item["publication"]
            require(publication in monitor.PUBLICATIONS)
            require(item["stage"] != "recording" or publication == "none")
            wav = _growing(item["wav"], self.stored.writer, metadata=False)
            metadata = (
                None
                if item["metadata"] is None
                else _growing(item["metadata"], self.stored.writer, metadata=True)
            )
            require((metadata is None) == (publication == "none"))
            if publication in ("linked", "published"):
                evidence.digest(item["metadata_sha256"])
                require(metadata is not None and 0 < metadata.size_before == metadata.size_after)
            else:
                require(item["metadata_sha256"] is None)
            temporary = item["temporary_name"]
            if publication in ("writing", "linked"):
                name = evidence.filename(expected.case, expected.started_at)
                require(
                    type(temporary) is str
                    and re.fullmatch(
                        re.escape("." + name + ".json.") + r"[a-z0-9_]{8}\.tmp", temporary
                    )
                    is not None
                )
            else:
                require(temporary is None)
            return monitor.Observation(
                **(item | {"writer": self.stored.writer, "wav": wav, "metadata": metadata})
            )
        except Exception:
            raise UnconfirmedProtection(MESSAGE) from None

    def retained(
        self,
        expected: evidence.RecordingExpectation,
        *,
        previous: monitor.Observation | None = None,
    ) -> Collected:
        """Hash complete bounded partial files without asserting writer exit.

        Host policy accepts this stage only after its independently proven exit.
        Stable malformed/empty output and an interrupted native hardlink pair can
        be preserved; unrelated names, unsafe links and changed old files cannot.
        """
        try:
            self._expected(expected)
            deadline = time.monotonic() + MAX_SECONDS
            first = self.active(expected, finalizing=True, previous=previous)
            assert first.progress is not None
            name = evidence.filename(expected.case, expected.started_at)
            names = [name]
            if first.progress.publication in ("linked", "published"):
                names.append(name + ".json")
            if first.progress.temporary_name is not None:
                names.append(first.progress.temporary_name)
            snapshots = []
            with evidence.opened_root(self.stored.baseline.root, deadline=deadline) as fd:
                require(identity(os.fstat(fd))[:6] == self.stored.baseline.root_identity)
                for _ in range(2):
                    snapshots.append(
                        {
                            n: _stable_file(
                                fd,
                                n,
                                deadline=deadline,
                                linked=(n != name and first.progress.publication == "linked"),
                                maximum=evidence.MAX_WAV_BYTES
                                if n == name
                                else evidence.MAX_METADATA_BYTES,
                            )
                            for n in names
                        }
                    )
                second = self.active(expected, finalizing=True, previous=first.progress)
                require(second.progress == first.progress and snapshots[0] == snapshots[1])
                for n, snapshot in snapshots[0].items():
                    progress = first.progress.wav if n == name else first.progress.metadata
                    require(progress is not None and snapshot["identity"][:5] == progress.identity)
            require(time.monotonic() <= deadline)
            proof = checksum(
                {
                    "contract": self.stored.contract.sha256,
                    "expected": asdict(expected),
                    "progress": asdict(first.progress),
                    "files": snapshots[0],
                }
            )
            return Collected(
                Files(self.stored.contract.sha256, "retained", proof, expected.generation),
                first.progress,
            )
        except Exception:
            raise UnconfirmedProtection(MESSAGE) from None

    def finalized(
        self,
        expected: evidence.RecordingExpectation,
        *,
        stopped: dict[str, Any],
        acknowledgment: Acknowledgment,
        previous: monitor.Observation | None = None,
    ) -> Collected:
        """Require separate native acknowledgment plus independently checked files."""
        try:
            self._expected(expected)
            require(type(acknowledgment) is Acknowledgment)
            require(acknowledgment.contract_sha256 == self.stored.contract.sha256)
            require(
                (acknowledgment.case, acknowledgment.generation, acknowledgment.started_at)
                == (expected.case, expected.generation, expected.started_at)
            )
            require(acknowledgment.stopped_sha256 == checksum(stopped))
            deadline = time.monotonic() + MAX_SECONDS
            name = evidence.filename(expected.case, expected.started_at)
            with evidence.opened_root(self.stored.baseline.root, deadline=deadline) as fd:
                require(identity(os.fstat(fd))[:6] == self.stored.baseline.root_identity)
                first = self.active(expected, finalizing=True, previous=previous)
                artifact = evidence.verify_finalized(
                    self.stored.baseline, expected, generation=expected.generation, stopped=stopped
                )
                require(artifact.audio_seconds <= self.stored.contract.maximum_recording_seconds)
                require(
                    stopped["elapsed_seconds"] <= self.stored.contract.maximum_recording_seconds
                )
                second = self.active(expected, finalizing=True, previous=first.progress)
                require(first.progress == second.progress and time.monotonic() <= deadline)
                # A finalizing monitor permits same-size WAV/header rewrites.
                # Size/inode continuity alone cannot bless changed final bytes.
                for filename, digest, maximum in (
                    (name, artifact.wav_sha256, evidence.MAX_WAV_BYTES),
                    (name + ".json", artifact.metadata_sha256, evidence.MAX_METADATA_BYTES),
                ):
                    current = _stable_file(
                        fd, filename, deadline=deadline, linked=False, maximum=maximum
                    )
                    require(current["sha256"] == digest)
                    progress = second.progress.wav if filename == name else second.progress.metadata
                    require(progress is not None and current["identity"][:5] == progress.identity)
            require(time.monotonic() <= deadline)
            proof = checksum(
                {
                    "contract": self.stored.contract.sha256,
                    "artifact": asdict(artifact),
                    "acknowledgment": asdict(acknowledgment),
                }
            )
            return Collected(
                Files(self.stored.contract.sha256, "finalized", proof, expected.generation),
                second.progress,
                artifact,
            )
        except Exception:
            raise UnconfirmedProtection(MESSAGE) from None


def _growing(raw: Any, writer: monitor.Writer, *, metadata: bool) -> monitor.GrowingFile:
    _mapping(raw, set(monitor.GrowingFile.__dataclass_fields__))
    file_id = raw["identity"]
    require(type(file_id) is list and len(file_id) == 5)
    for value in [*file_id, raw["size_before"], raw["size_after"]]:
        evidence.number(value)
    require(
        file_id[1] > 0 and file_id[2] == stat.S_IFREG | (0o600 if metadata else writer.wav_mode)
    )
    require(file_id[3:] == [writer.uid, writer.gid])
    require(
        raw["size_before"]
        <= raw["size_after"]
        <= (evidence.MAX_METADATA_BYTES if metadata else evidence.MAX_WAV_BYTES)
    )
    return monitor.GrowingFile(tuple(file_id), raw["size_before"], raw["size_after"])


def _stable_file(
    root: int, name: str, *, deadline: float, linked: bool, maximum: int
) -> dict[str, Any]:
    """Hash one exact allowed name, including empty/partial native output."""
    fd = os.open(name, monitor.READ, dir_fd=root)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == (2 if linked else 1))
        require(0 <= before.st_size <= maximum)
        require(identity(os.stat(name, dir_fd=root, follow_symlinks=False)) == identity(before))
        digest, size = hashlib.sha256(), 0
        while True:
            require(time.monotonic() <= deadline)
            raw = os.read(fd, min(65536, before.st_size + 1 - size))
            if not raw:
                break
            size += len(raw)
            require(size <= before.st_size)
            digest.update(raw)
        require(size == before.st_size)
        require(identity(os.fstat(fd)) == identity(before))
        require(identity(os.stat(name, dir_fd=root, follow_symlinks=False)) == identity(before))
        return {"identity": identity(before), "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Offline protected-file collection only; no live host plan is enabled.")
