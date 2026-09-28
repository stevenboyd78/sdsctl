#!/usr/bin/env python3
"""Bounded read-only filesystem evidence for the private handoff observer.

No App imports, repair, copying, deployment or command execution. Hashes prove
only the bytes observed: the caller must reconstruct expected source separately
and bind this evidence to independently inspected image/container identities.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path

MAX_FILES = 4096
MAX_ENTRIES = 8192
MAX_DEPTH = 16
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SECONDS = 2.0
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
CONTROL_CHARACTER = re.compile(r"[\x00-\x1f]")


class UnconfirmedFiles(ValueError):
    """The private tree could not be verified; never include path/content text."""


def require(condition: bool) -> None:
    if not condition:
        raise UnconfirmedFiles("Protected filesystem evidence is unconfirmed.")


def identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@dataclass(frozen=True)
class FileEvidence:
    size: int
    sha256: str
    mode: int
    uid: int
    gid: int


def inventory(
    root: Path,
    *,
    max_file_bytes: int | None = None,
    source_directories: bool = False,
    deadline: float | None = None,
) -> dict[str, dict[str, int | str]]:
    """Hash one explicit tree, with no symlinks, special files or hardlinks.

    Every parent is opened without following links. Both open descriptors and
    directory entries are rechecked, including after descendants are read. Any
    failure yields a fixed error, not a partial inventory. This is bounded local
    disk I/O, not a filesystem snapshot or protection against a trusted root user.
    Kernel I/O stalls still require the independent outer service deadline.
    Source callers can additionally require that the selected root and every
    descendant directory have no special bits or group/other write access.
    This is checked on the same held descriptors as traversal, not a separate
    path walk. It does not change the file-only inventory schema or qualify
    external ancestor permissions, image provenance or executable ownership.
    An explicit outer deadline can narrow, never extend, the original budget.
    """
    opened: list[tuple[int, str, int, tuple[int, ...]]] = []
    anchor = -1
    try:
        # Source/profile inventories keep their original 4 MiB limit. A reviewed
        # recording root may opt into 16 MiB per file, still under the unchanged
        # 64 MiB total, entry/depth and elapsed-time bounds. No metadata-only proof.
        limit = MAX_FILE_BYTES if max_file_bytes is None else max_file_bytes
        require(type(limit) is int and 0 < limit <= 16 * 1024 * 1024)
        require(type(source_directories) is bool)
        require(type(root) is type(Path()) and root.is_absolute())
        require(root != Path("/") and all(p not in (".", "..") for p in root.parts))
        outer_bound = deadline is not None
        began = time.monotonic()
        if outer_bound:
            require(type(deadline) in (int, float) and math.isfinite(deadline))
            deadline = min(began + MAX_SECONDS, deadline)
        else:
            deadline = began + MAX_SECONDS
        require(began < deadline)
        anchor = os.open("/", DIRECTORY)
        parent = anchor
        for name in root.parts[1:]:
            if outer_bound:
                require(time.monotonic() < deadline)
            child = os.open(name, DIRECTORY, dir_fd=parent)
            try:
                initial = identity(os.fstat(child))
            except BaseException:
                os.close(child)
                raise
            opened.append((parent, name, child, initial))
            parent = child
        result: dict[str, dict[str, int | str]] = {}
        entries = total = 0

        def timely() -> None:
            require(time.monotonic() <= deadline)

        def visit(directory: int, prefix: str, depth: int) -> None:
            nonlocal entries, total
            timely()
            require(depth <= MAX_DEPTH)
            before = identity(os.fstat(directory))
            if source_directories:
                require(stat.S_IMODE(before[2]) & 0o7022 == 0)
            # scandir is incremental so a huge directory cannot allocate an
            # unbounded list before its entry budget is checked.
            with os.scandir(directory) as children:
                for entry in children:
                    timely()
                    entries += 1
                    require(entries <= MAX_ENTRIES)
                    name = entry.name
                    require(len(name.encode()) <= 255 and CONTROL_CHARACTER.search(name) is None)
                    path = prefix + name
                    stated = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    stated_identity = identity(stated)
                    if stat.S_ISDIR(stated.st_mode):
                        child = os.open(name, DIRECTORY, dir_fd=directory)
                        try:
                            require(identity(os.fstat(child)) == stated_identity)
                            visit(child, path + "/", depth + 1)
                            require(identity(os.fstat(child)) == stated_identity)
                            require(
                                identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                                == stated_identity
                            )
                        finally:
                            os.close(child)
                    else:
                        require(stat.S_ISREG(stated.st_mode) and stated.st_nlink == 1)
                        require(len(result) < MAX_FILES and stated.st_size <= limit)
                        total += stated.st_size
                        require(total <= MAX_TOTAL_BYTES)
                        fd = os.open(
                            name,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                            dir_fd=directory,
                        )
                        try:
                            require(identity(os.fstat(fd)) == stated_identity)
                            hashed = hashlib.sha256()
                            size = 0
                            while True:
                                timely()
                                chunk = os.read(fd, min(65536, stated.st_size + 1 - size))
                                if not chunk:
                                    break
                                size += len(chunk)
                                require(size <= stated.st_size)
                                hashed.update(chunk)
                            require(size == stated.st_size)
                            require(identity(os.fstat(fd)) == stated_identity)
                            require(
                                identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                                == stated_identity
                            )
                            # The inventory format contains only five scalars.
                            # Avoid constructing and recursively copying a
                            # dataclass for every file in every full snapshot.
                            # All bytes and fresh stat observations above remain.
                            result[path] = {
                                "size": size,
                                "sha256": hashed.hexdigest(),
                                "mode": stat.S_IMODE(stated.st_mode),
                                "uid": stated.st_uid,
                                "gid": stated.st_gid,
                            }
                        finally:
                            os.close(fd)
            require(identity(os.fstat(directory)) == before)

        visit(parent, "", 0)
        timely()
        # Parent directory timestamps/link counts can change for unrelated siblings. Their
        # identity/mode/owner must remain stable; the selected root must match
        # its entire original stat (including mtime and ctime).
        for index, (parent, name, child, before) in enumerate(opened):
            if outer_bound:
                require(time.monotonic() < deadline)
            current = identity(os.fstat(child))
            entry = identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            width = len(before) if index == len(opened) - 1 else 5
            require(current[:width] == entry[:width] == before[:width])
        if outer_bound:
            require(time.monotonic() < deadline)
        return dict(sorted(result.items()))
    except Exception:
        raise UnconfirmedFiles("Protected filesystem evidence is unconfirmed.") from None
    finally:
        for _, _, child, _ in reversed(opened):
            os.close(child)
        if anchor >= 0:
            os.close(anchor)


def _single_file(path: Path, mode: int) -> FileEvidence:
    """Hash exactly one existing regular file, never its siblings.

    The four accepted-profile/deployment inputs can live in different private
    directories. Opening each ancestor without following links also avoids
    importing an App to resolve paths while its container is stopped.
    """
    opened: list[tuple[int, str, int, tuple[int, ...]]] = []
    anchor = file = -1
    try:
        require(type(mode) is int and mode in (0o600, 0o555))
        require(type(path) is type(Path()) and path.is_absolute() and len(path.parts) > 2)
        require(all(part not in (".", "..") for part in path.parts))
        deadline = time.monotonic() + MAX_SECONDS
        anchor = os.open("/", DIRECTORY)
        parent = anchor
        for name in path.parts[1:-1]:
            child = os.open(name, DIRECTORY, dir_fd=parent)
            try:
                observed = identity(os.fstat(child))
            except BaseException:
                os.close(child)
                raise
            opened.append((parent, name, child, observed))
            parent = child
        stated = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        require(stat.S_ISREG(stated.st_mode) and stated.st_nlink == 1)
        require(stated.st_uid == os.geteuid() and stat.S_IMODE(stated.st_mode) == mode)
        require(0 < stated.st_size <= MAX_FILE_BYTES)
        file = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent
        )
        require(identity(os.fstat(file)) == identity(stated))
        hashed, size = hashlib.sha256(), 0
        while True:
            require(time.monotonic() <= deadline)
            chunk = os.read(file, min(65536, stated.st_size + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            require(size <= stated.st_size)
            hashed.update(chunk)
        require(size == stated.st_size and identity(os.fstat(file)) == identity(stated))
        require(
            identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == identity(stated)
        )
        for parent, name, child, before in opened:
            current = identity(os.fstat(child))
            named = identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            # This API selects one file, not its sibling directories. Preserve
            # device/inode/type/mode/owner checks on every external ancestor.
            require(current[:5] == named[:5] == before[:5])
        require(time.monotonic() <= deadline)
        return FileEvidence(size, hashed.hexdigest(), mode, stated.st_uid, stated.st_gid)
    except Exception:
        raise UnconfirmedFiles("Protected filesystem evidence is unconfirmed.") from None
    finally:
        if file >= 0:
            os.close(file)
        for _, _, child, _ in reversed(opened):
            os.close(child)
        if anchor >= 0:
            os.close(anchor)


def private_file(path: Path) -> FileEvidence:
    """One bounded 0600 private configuration/profile file."""
    return _single_file(path, 0o600)


def executable_file(path: Path) -> FileEvidence:
    """One bounded 0555 immutable candidate entry script, not a private input."""
    return _single_file(path, 0o555)


if __name__ == "__main__":
    raise SystemExit("Read-only private evidence component; no handoff or deployment started.")
