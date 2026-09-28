#!/usr/bin/env python3
"""Retained read-only peer expectations, not input provenance or a launcher.

The external owner must authenticate BOTH the original startup declaration and
this expectations digest before observing peers. No digest is learned from an
observed file or running process. Fixed files are read, never provisioned here.
This uninstalled library is outside command-selected source/permission profiles.
"""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import stat
import time
from pathlib import Path
from threading import get_ident

import supplemental_recording_service_declaration as declarations
import supplemental_recording_service_runtime_expectations as codec

files = declarations.files
NAME, SECONDS, ROOT_UID = "expectations.json", 2.0, 0
MESSAGE = "Recording peer inputs are unconfirmed; preserve the case and do not retry."


class UnconfirmedInputs(ValueError):
    """Never expose private paths, bytes, pins or underlying exception details."""


def require(value):
    if not value:
        raise UnconfirmedInputs(MESSAGE)


def inputs_root(case):
    codec.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-peer-inputs-" + case)


def _end(deadline):
    end = time.monotonic() + SECONDS
    if deadline is not None:
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        end = min(end, deadline)
    require(time.monotonic() < end)
    return end


class Inputs:
    """Retain one exact private file and borrow the original Declaration.

    Every recheck fully rereads the same inode, not a cached observation. The
    fixed sibling directory is distinct from startup and writable case roots.
    Ancestors are held without following symlinks; the leaf is exactly0700 and
    contains only the single-link0600 regular file. Original descriptors, flags,
    path bindings, bytes, digest, objects and template join must remain intact.

    Each complete read has a two-second budget; an explicit enclosing deadline
    only narrows it and also bounds nested Declaration reads. No clock origin,
    plan, process, directory, journal or App action is created. Path permissions
    do not prove installation trust; independent provenance and an outer bound
    for blocking syscalls remain mandatory. This does not defend against trusted
    root replacing the installation or arbitrary code in this same process.
    """

    def __init__(self, original, root, expected_sha256, *, deadline=None):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.closed = self.failed = False
        self.handles, self.directories = [], []
        try:
            end = _end(deadline)
            require(type(self) is Inputs and self.owner[2:] == (ROOT_UID, ROOT_UID))
            require(type(original) is declarations.Declaration and original.owner == self.owner)
            require(type(root) is type(Path()) and root.is_absolute() and root != Path("/"))
            require(root.anchor == "/" and ".." not in root.parts and len(root.parts) <= 32)
            require(len(os.fsencode(root)) <= 4096 and all(ord(c) >= 32 for c in str(root)))
            require(type(expected_sha256) is str)
            codec.plans.base.digest(expected_sha256)
            template = original.recheck(deadline=end)
            case = declarations.codec._read(template.raw)["plan"]["case"]
            require(root == inputs_root(case) and root != original.root)
            self.declaration, self.root, self.expected = original, root, expected_sha256
            self.template = template
            self.binding = original, root, expected_sha256, template
            self._state(end)
            parent = self._open("/", files.DIRECTORY)
            for name in root.parts[1:]:
                self._state(end)
                child = self._open(name, files.DIRECTORY, dir_fd=parent)
                self.directories.append((parent, name, child, files.identity(os.fstat(child))[:5]))
                parent = child
            self.path_pins = tuple(self.directories)
            self.directory = parent
            self.directory_pin = files.identity(os.fstat(parent))
            before = os.stat(NAME, dir_fd=parent, follow_symlinks=False)
            require(
                stat.S_ISREG(before.st_mode)
                and stat.S_IMODE(before.st_mode) == 0o600
                and (before.st_uid, before.st_gid) == self.owner[2:]
                and before.st_nlink == 1
                and 0 < before.st_size <= codec.MAX_BYTES
            )
            self.file_pin = files.identity(before)
            self.file = self._open(
                NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent
            )
            self.handle_pins = tuple(self.handles)
            self.raw = self._read(end)
            self.expectations = codec.load_bytes(self.raw, expected_sha256)
            self.original_expectations = self.expectations
            self.expectations.check_template(template)
            self._joined(end)
            self._paths(end)
        except BaseException as error:
            self._fail(error)

    def _open(self, *args, **kwargs):
        fd = os.open(*args, **kwargs)
        try:
            pin = files.identity(os.fstat(fd))[:5]
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except BaseException:
            os.close(fd)
            raise
        self.handles.append((fd, pin, flags))
        return fd

    def _state(self, end):
        require(type(self) is Inputs and not self.closed and not self.failed)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(time.monotonic() < end)
        original, root, expected, template = self.binding
        require(self.declaration is original and self.root is root and self.expected == expected)
        require(self.template is template and original.template is template)
        require(original.owner == self.owner and not original.closed and not original.failed)

    def _paths(self, end):
        self._state(end)
        require(tuple(self.handles) == self.handle_pins)
        require(tuple(self.directories) == self.path_pins)
        require(self.file == self.handle_pins[-1][0] and self.directory == self.path_pins[-1][2])
        for fd, pin, original_flags in self.handle_pins:
            require(files.identity(os.fstat(fd))[:5] == pin and not os.get_inheritable(fd))
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            require(flags == original_flags)
            require(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_APPEND)
        require(fcntl.fcntl(self.file, fcntl.F_GETFL) & os.O_NONBLOCK)
        for parent, name, child, pin in self.path_pins:
            self._state(end)
            require(files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:5] == pin)
            require(files.identity(os.fstat(child))[:5] == pin)
        info = os.fstat(self.directory)
        require(files.identity(info) == self.directory_pin)
        require(
            stat.S_IMODE(info.st_mode) == 0o700 and (info.st_uid, info.st_gid) == self.owner[2:]
        )
        with os.scandir(self.directory) as entries:
            entry = next(entries, None)
            require(entry is not None and entry.name == NAME and next(entries, None) is None)
        require(files.identity(os.fstat(self.file)) == self.file_pin)
        require(
            files.identity(os.stat(NAME, dir_fd=self.directory, follow_symlinks=False))
            == self.file_pin
        )
        self._state(end)

    def _read(self, end):
        self._paths(end)
        raw, size = bytearray(), self.file_pin[6]
        while len(raw) <= size:
            self._state(end)
            chunk = os.pread(self.file, min(4096, size + 1 - len(raw)), len(raw))
            if not chunk:
                break
            raw.extend(chunk)
        require(len(raw) == size)
        self._paths(end)
        return bytes(raw)

    def _joined(self, end):
        self._state(end)
        require(self.declaration.recheck(deadline=end) is self.template)
        require(type(self.expectations) is codec.Expectations)
        require(self.expectations is self.original_expectations)
        require(type(self.raw) is bytes and self.expectations.raw == self.raw)
        require(hashlib.sha256(self.raw).hexdigest() == self.expected)
        self.expectations.check_template(self.template)
        self._state(end)

    def recheck(self, *, deadline=None):
        """Return the SAME declaration value, after fresh complete original reads."""
        try:
            end = _end(deadline)
            self._joined(end)
            require(self._read(end) == self.raw)
            self._joined(end)
            return self.expectations
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        try:
            self.close()
        except BaseException as cleanup:
            if isinstance(error, Exception) and not isinstance(cleanup, Exception):
                raise cleanup
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedInputs(MESSAGE) from None

    def close(self):
        """Retire only owned originals once; never close a borrowed declaration."""
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        problem = None
        while self.handles:
            fd, pin, _ = self.handles.pop()
            try:
                current = files.identity(os.fstat(fd))
                require(current[:2] == pin[:2] and stat.S_IFMT(current[2]) == stat.S_IFMT(pin[2]))
                os.close(fd)
            except BaseException as error:
                if problem is None or not isinstance(error, Exception):
                    problem = error
        if problem is not None:
            if not isinstance(problem, Exception):
                raise problem
            raise UnconfirmedInputs(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled read-only peer inputs only; no active launch enabled.")
