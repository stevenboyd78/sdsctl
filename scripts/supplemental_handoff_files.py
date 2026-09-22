#!/usr/bin/env python3
"""Bounded read-only filesystem evidence for the private handoff observer.

No App imports, repair, copying, deployment or command execution. Hashes prove
only the bytes observed: the caller must reconstruct expected source separately
and bind this evidence to independently inspected image/container identities.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_FILES = 4096
MAX_ENTRIES = 8192
MAX_DEPTH = 16
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SECONDS = 2.0
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


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


def inventory(root: Path) -> dict[str, dict[str, int | str]]:
    """Hash one explicit tree, with no symlinks, special files or hardlinks.

    Every parent is opened without following links. Both open descriptors and
    directory entries are rechecked, including after descendants are read. Any
    failure yields a fixed error, not a partial inventory. This is bounded local
    disk I/O, not a filesystem snapshot or protection against a trusted root user.
    Kernel I/O stalls still require the independent outer service deadline.
    """
    opened: list[tuple[int, str, int, tuple[int, ...]]] = []
    anchor = -1
    try:
        require(type(root) is type(Path()) and root.is_absolute())
        require(root != Path("/") and all(p not in (".", "..") for p in root.parts))
        deadline = time.monotonic() + MAX_SECONDS
        anchor = os.open("/", DIRECTORY)
        parent = anchor
        for name in root.parts[1:]:
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
            # scandir is incremental so a huge directory cannot allocate an
            # unbounded list before its entry budget is checked.
            with os.scandir(directory) as children:
                for entry in children:
                    timely()
                    entries += 1
                    require(entries <= MAX_ENTRIES)
                    name = entry.name
                    require(len(name.encode()) <= 255 and not any(ord(c) < 32 for c in name))
                    path = prefix + name
                    stated = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISDIR(stated.st_mode):
                        child = os.open(name, DIRECTORY, dir_fd=directory)
                        try:
                            require(identity(os.fstat(child)) == identity(stated))
                            visit(child, path + "/", depth + 1)
                            require(identity(os.fstat(child)) == identity(stated))
                            require(
                                identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                                == identity(stated)
                            )
                        finally:
                            os.close(child)
                    else:
                        require(stat.S_ISREG(stated.st_mode) and stated.st_nlink == 1)
                        require(len(result) < MAX_FILES and stated.st_size <= MAX_FILE_BYTES)
                        total += stated.st_size
                        require(total <= MAX_TOTAL_BYTES)
                        fd = os.open(
                            name,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                            dir_fd=directory,
                        )
                        try:
                            require(identity(os.fstat(fd)) == identity(stated))
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
                            require(identity(os.fstat(fd)) == identity(stated))
                            require(
                                identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
                                == identity(stated)
                            )
                            result[path] = asdict(
                                FileEvidence(
                                    size,
                                    hashed.hexdigest(),
                                    stat.S_IMODE(stated.st_mode),
                                    stated.st_uid,
                                    stated.st_gid,
                                )
                            )
                        finally:
                            os.close(fd)
            require(identity(os.fstat(directory)) == before)

        visit(parent, "", 0)
        timely()
        # Parent directory mtimes can change for unrelated siblings. Their
        # identity/mode/owner must remain stable; the selected root must match
        # its entire original stat (including mtime and ctime).
        for index, (parent, name, child, before) in enumerate(opened):
            current = identity(os.fstat(child))
            entry = identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            width = len(before) if index == len(opened) - 1 else 6
            require(current[:width] == entry[:width] == before[:width])
        return dict(sorted(result.items()))
    except Exception:
        raise UnconfirmedFiles("Protected filesystem evidence is unconfirmed.") from None
    finally:
        for _, _, child, _ in reversed(opened):
            os.close(child)
        if anchor >= 0:
            os.close(anchor)


if __name__ == "__main__":
    raise SystemExit("Read-only private evidence component; no handoff or deployment started.")
