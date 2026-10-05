#!/usr/bin/env python3
"""Read-only observation of one finite recorder's changing files; not activated.

No root exclusion, finalization, repair, retries or App/process operations.
This validates stable old files and bounded *intermediate* new-file states, not
audio contents, writer/process identity, successful finalization or restoration.
The caller separately qualifies the start/stop intent, generation and writer IDs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import asdict, dataclass

from supplemental_handoff_files import (
    DIRECTORY,
    MAX_DEPTH,
    MAX_ENTRIES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    FileEvidence,
    identity,
)
from supplemental_recording_evidence import (
    MAX_METADATA_BYTES,
    MAX_WAV_BYTES,
    RecordingBaseline,
    RecordingExpectation,
    digest,
    filename,
    number,
    opened_root,
    template,
)

MAX_SECONDS = 2.0
MESSAGE = (
    "Recording file observation is unconfirmed; preserve the case and fixed recovery deadline."
)
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
PUBLICATIONS = ("none", "writing", "linked", "published")


class UnconfirmedObservation(ValueError):
    """An unconfirmed sample grants no file, recording, or recovery permission."""


def require(value: bool) -> None:
    if not value:
        raise UnconfirmedObservation(MESSAGE)


@dataclass(frozen=True)
class Writer:
    uid: int
    gid: int
    wav_mode: int

    def __post_init__(self) -> None:
        try:
            number(self.uid)
            number(self.gid)
            require(
                type(self.wav_mode) is int and self.wav_mode in (0o600, 0o640, 0o644, 0o660, 0o664)
            )
        except Exception:
            raise UnconfirmedObservation(MESSAGE) from None


@dataclass(frozen=True)
class GrowingFile:
    # dev, ino, mode, uid, gid; link count is checked for each permitted state.
    identity: tuple[int, ...]
    size_before: int
    size_after: int


@dataclass(frozen=True)
class Observation:
    case: str
    generation: str
    binding_sha256: str
    stage: str
    writer: Writer
    wav: GrowingFile
    publication: str
    metadata: GrowingFile | None
    temporary_name: str | None
    metadata_sha256: str | None
    old_files: int


def _baseline(
    baseline: RecordingBaseline, expected: RecordingExpectation
) -> dict[str, FileEvidence]:
    require(type(baseline) is RecordingBaseline and type(expected) is RecordingExpectation)
    require(baseline.case == expected.case)
    require(type(baseline.files) is tuple and len(baseline.files) <= MAX_FILES - 3)
    require(type(baseline.root_identity) is tuple and len(baseline.root_identity) == 6)
    require(all(type(v) is int and v >= 0 for v in baseline.root_identity))
    old = dict(baseline.files)
    require(len(old) == len(baseline.files))
    prefix = template(expected.case).split("{timestamp}", 1)[0]
    for name, item in old.items():
        require(type(name) is str and 0 < len(name) <= 4096)
        require(all(part not in ("", ".", "..") for part in name.split("/")))
        require(not any(ord(c) < 32 for c in name))
        require(not name.split("/")[-1].startswith((prefix, "." + prefix)))
        require(type(item) is FileEvidence)
        number(item.size)
        require(item.size <= 16 * 1024 * 1024)
        digest(item.sha256)
        number(item.mode)
        require(item.mode <= 0o7777)
        number(item.uid)
        number(item.gid)
    return old


def _progress(before: GrowingFile, after: GrowingFile) -> None:
    require(type(before) is GrowingFile and type(after) is GrowingFile)
    require(before.identity == after.identity and before.size_after <= after.size_before)


def observe(
    baseline: RecordingBaseline,
    expected: RecordingExpectation,
    *,
    generation: str,
    writer: Writer,
    stage: str,
    previous: Observation | None = None,
) -> Observation:
    """One bounded observation; changing directory publication can be unconfirmed.

    stage='recording' requires exactly the old files and one changing WAV.
    stage='finalizing' additionally permits one exact sidecar and/or the native
    writer's one adjacent temporary metadata file. This is not an API accepting
    arbitrary glob exclusions. previous is a trusted same-process observation,
    not externally authenticated evidence or durable restart authority.
    """
    descriptors: list[int] = []
    try:
        old = _baseline(baseline, expected)
        require(type(generation) is str and generation == expected.generation)
        require(type(writer) is Writer and stage in ("recording", "finalizing"))
        name = filename(expected.case, expected.started_at)
        sidecar = name + ".json"
        temporary = re.compile(re.escape("." + sidecar + ".") + r"[a-z0-9_]{8}\.tmp")
        binding = hashlib.sha256(
            json.dumps(
                {
                    "expected": asdict(expected),
                    "root_identity": baseline.root_identity,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        deadline = time.monotonic() + MAX_SECONDS
        found: set[str] = set()
        changing: dict[str, tuple[int, os.stat_result]] = {}
        entries = total = 0

        def timely() -> None:
            require(time.monotonic() <= deadline)

        with opened_root(baseline.root, deadline=deadline) as root:
            require(identity(os.fstat(root))[:6] == baseline.root_identity)

            def visit(directory: int, prefix: str, depth: int) -> None:
                nonlocal entries, total
                timely()
                require(depth <= MAX_DEPTH)
                initial = identity(os.fstat(directory))
                with os.scandir(directory) as children:
                    for entry in children:
                        timely()
                        entries += 1
                        require(entries <= MAX_ENTRIES)
                        require(len(entry.name.encode()) <= 255)
                        require(not any(ord(c) < 32 for c in entry.name))
                        path = prefix + entry.name
                        info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            require(
                                path not in (name, sidecar) and temporary.fullmatch(path) is None
                            )
                            child = os.open(entry.name, DIRECTORY, dir_fd=directory)
                            try:
                                require(identity(os.fstat(child)) == identity(info))
                                visit(child, path + "/", depth + 1)
                                require(identity(os.fstat(child)) == identity(info))
                                require(
                                    identity(
                                        os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                                    )
                                    == identity(info)
                                )
                            finally:
                                os.close(child)
                            continue
                        require(stat.S_ISREG(info.st_mode))
                        require(len(found) < MAX_FILES and path not in found)
                        found.add(path)
                        total += info.st_size
                        require(total <= MAX_TOTAL_BYTES)
                        fd = os.open(entry.name, READ, dir_fd=directory)
                        try:
                            require(identity(os.fstat(fd)) == identity(info))
                            if path in old:
                                wanted = old[path]
                                require(info.st_nlink == 1 and info.st_size == wanted.size)
                                require(
                                    (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
                                    == (wanted.mode, wanted.uid, wanted.gid)
                                )
                                checksum = hashlib.sha256()
                                size = 0
                                while True:
                                    timely()
                                    chunk = os.read(fd, min(65536, info.st_size + 1 - size))
                                    if not chunk:
                                        break
                                    size += len(chunk)
                                    require(size <= info.st_size)
                                    checksum.update(chunk)
                                require(
                                    size == wanted.size and checksum.hexdigest() == wanted.sha256
                                )
                                require(identity(os.fstat(fd)) == identity(info))
                                require(
                                    identity(
                                        os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                                    )
                                    == identity(info)
                                )
                            else:
                                allowed = path == name or (
                                    stage == "finalizing"
                                    and (path == sidecar or temporary.fullmatch(path) is not None)
                                )
                                require(allowed and prefix == "" and len(changing) < 3)
                                require(info.st_uid == writer.uid and info.st_gid == writer.gid)
                                require(
                                    stat.S_IMODE(info.st_mode)
                                    == (writer.wav_mode if path == name else 0o600)
                                )
                                require(
                                    0
                                    <= info.st_size
                                    <= (MAX_WAV_BYTES if path == name else MAX_METADATA_BYTES)
                                )
                                require(
                                    info.st_nlink == 1 if path == name else info.st_nlink in (1, 2)
                                )
                                changing[path] = (fd, info)
                                descriptors.append(fd)
                                fd = -1
                        finally:
                            if fd >= 0:
                                os.close(fd)
                require(identity(os.fstat(directory)) == initial)

            visit(root, "", 0)
            require(set(old) <= found and name in changing)
            temps = [key for key in changing if key not in (name, sidecar)]
            require(len(temps) <= 1)
            has_temp, has_sidecar = bool(temps), sidecar in changing
            publication = (
                "linked"
                if has_temp and has_sidecar
                else "writing"
                if has_temp
                else "published"
                if has_sidecar
                else "none"
            )
            temp = temps[0] if temps else None
            if publication == "linked":
                require(identity(changing[sidecar][1]) == identity(changing[temp][1]))
            observations: dict[str, GrowingFile] = {}
            for key, (fd, before) in changing.items():
                after = os.fstat(fd)
                named = os.stat(key, dir_fd=root, follow_symlinks=False)
                link_count = 2 if publication == "linked" and key != name else 1
                require(before.st_nlink == after.st_nlink == named.st_nlink == link_count)
                require(identity(before)[:5] == identity(after)[:5] == identity(named)[:5])
                require(
                    before.st_size
                    <= after.st_size
                    <= named.st_size
                    <= (MAX_WAV_BYTES if key == name else MAX_METADATA_BYTES)
                )
                # WAV data can grow and its header can change. Published metadata
                # no longer changes, including while two publication links exist.
                if key != name and publication in ("linked", "published"):
                    require(identity(before) == identity(after) == identity(named))
                total += named.st_size - before.st_size
                require(total <= MAX_TOTAL_BYTES)
                observations[key] = GrowingFile(identity(before)[:5], before.st_size, named.st_size)
            metadata = observations.get(sidecar) or observations.get(temp)
            metadata_sha = None
            if publication in ("linked", "published"):
                fd, info = changing[sidecar]
                payload = os.read(fd, MAX_METADATA_BYTES + 1)
                require(0 < len(payload) == info.st_size <= MAX_METADATA_BYTES)
                require(identity(os.fstat(fd)) == identity(info))
                require(
                    identity(os.stat(sidecar, dir_fd=root, follow_symlinks=False)) == identity(info)
                )
                metadata_sha = hashlib.sha256(payload).hexdigest()
            result = Observation(
                expected.case,
                generation,
                binding,
                stage,
                writer,
                observations[name],
                publication,
                metadata,
                temp,
                metadata_sha,
                len(old),
            )
            if previous is not None:
                require(type(previous) is Observation)
                require(
                    (
                        previous.case,
                        previous.generation,
                        previous.binding_sha256,
                        previous.writer,
                        previous.old_files,
                    )
                    == (result.case, result.generation, binding, writer, len(old))
                )
                require(previous.stage in ("recording", "finalizing"))
                require(not (previous.stage == "finalizing" and stage == "recording"))
                require(previous.publication in PUBLICATIONS)
                require(PUBLICATIONS.index(previous.publication) <= PUBLICATIONS.index(publication))
                _progress(previous.wav, result.wav)
                if previous.metadata is not None:
                    require(metadata is not None)
                    _progress(previous.metadata, metadata)
                if previous.metadata_sha256 is not None:
                    require(previous.metadata_sha256 == metadata_sha)
                if previous.temporary_name is not None and temp is not None:
                    require(previous.temporary_name == temp)
            timely()
        return result
    except Exception:
        raise UnconfirmedObservation(MESSAGE) from None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Read-only offline recording monitor; no recording or handoff started.")
