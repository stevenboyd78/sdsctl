#!/usr/bin/env python3
"""Retained read-only startup declaration, not an installed service entrypoint.

The independently prepared declaration lives outside the EMPTY writable case,
under the existing read-only /mnt/data view. This library creates nothing and
does not capture a clock, publish a plan, contact Engine or grant authority.
Its new input path/command/source still require separate installed qualification.
"""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import stat
import time
from pathlib import Path
from threading import Lock, get_ident

import supplemental_handoff_files as files
import supplemental_recording_service_template as codec

NAME, MAX_SECONDS = "template.json", 2
MESSAGE = "Recording startup declaration input is unconfirmed; preserve this case."


class UnconfirmedDeclaration(ValueError):
    """No private filename, payload or expected digest is exposed."""


def require(value):
    if not value:
        raise UnconfirmedDeclaration(MESSAGE)


def declaration_root(case):
    codec.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-startup-" + case)


class Declaration:
    """Borrow no handles: retain original read-only ancestors and template file.

    The caller authenticates the expected digest independently. Exact decoded
    bytes/objects and original file identities are retained across every read;
    changed or uncertain input cannot be reopened or repaired. No clock or
    deadline is present yet. Sibling startup inputs must not be put inside the
    case directory reserved for exclusive original-owner plan publication.
    """

    def __init__(self, root, expected_sha256):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock, self.handles, self.handle_pins = Lock(), [], {}
        self.failed = self.closed = False
        self.startup_owner = None
        try:
            require(type(root) is type(Path()) and root.is_absolute() and root != Path("/"))
            require(1 < len(root.parts) <= 32 and ".." not in root.parts)
            require(not any(ord(c) < 32 for c in str(root)))
            require(type(expected_sha256) is str)
            codec.plans.base.digest(expected_sha256)
            self.root, self.expected = root, expected_sha256
            self.original_inputs = root, expected_sha256
            end = time.monotonic() + MAX_SECONDS
            self.anchor = self._open("/", files.DIRECTORY)
            self.anchor_id = files.identity(os.fstat(self.anchor))[:5]
            parent, self.directories = self.anchor, []
            for name in root.parts[1:]:
                self._context(end)
                child = self._open(name, files.DIRECTORY, dir_fd=parent)
                self.directories.append((parent, name, child, files.identity(os.fstat(child))[:5]))
                parent = child
            self.directory = parent
            self.directory_id = files.identity(os.fstat(parent))
            self._directories(end)
            before = os.stat(NAME, dir_fd=parent, follow_symlinks=False)
            require(
                stat.S_ISREG(before.st_mode)
                and stat.S_IMODE(before.st_mode) == 0o600
                and (before.st_uid, before.st_gid) == self.owner[2:]
                and before.st_nlink == 1
                and 0 < before.st_size <= codec.MAX_BYTES
            )
            self.file_id = files.identity(before)
            self.file = self._open(
                NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent
            )
            raw = self._read(end)
            self.template = codec.load_bytes(raw, expected_sha256)
            self.original_template, self.raw = self.template, raw
            require(declaration_root(codec._read(raw)["plan"]["case"]) == self.root)
            self._binding(end)
            self._file(end)
            self._directories(end)
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
        self.handles.append(fd)
        self.handle_pins[fd] = pin, flags
        return fd

    def _handle(self, fd):
        pin, flags = self.handle_pins[fd]
        require(files.identity(os.fstat(fd))[:5] == pin and not os.get_inheritable(fd))
        require(fcntl.fcntl(fd, fcntl.F_GETFL) == flags)
        require(flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_APPEND)

    def _context(self, end):
        require(not self.failed and not self.closed and time.monotonic() < end)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require((self.root, self.expected) == self.original_inputs)
        require(tuple(self.handles) == tuple(self.handle_pins))

    def _directories(self, end):
        self._context(end)
        self._handle(self.anchor)
        require(files.identity(os.fstat(self.anchor))[:5] == self.anchor_id)
        for parent, name, child, identity in self.directories:
            self._context(end)
            self._handle(child)
            require(files.identity(os.fstat(child))[:5] == identity)
            require(
                files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:5] == identity
            )
        info = os.fstat(self.directory)
        require(files.identity(info) == self.directory_id)
        require(
            stat.S_IMODE(info.st_mode) == 0o700 and (info.st_uid, info.st_gid) == self.owner[2:]
        )
        with os.scandir(self.directory) as entries:
            entry = next(entries, None)
            require(entry is not None and entry.name == NAME and next(entries, None) is None)
        self._context(end)

    def _file(self, end):
        self._context(end)
        self._handle(self.file)
        require(fcntl.fcntl(self.file, fcntl.F_GETFL) & os.O_NONBLOCK)
        require(files.identity(os.fstat(self.file)) == self.file_id)
        require(
            files.identity(os.stat(NAME, dir_fd=self.directory, follow_symlinks=False))
            == self.file_id
        )

    def _read(self, end):
        self._directories(end)
        self._file(end)
        size, raw = self.file_id[6], bytearray()
        while len(raw) <= size:
            self._context(end)
            chunk = os.pread(self.file, size + 1 - len(raw), len(raw))
            if not chunk:
                break
            raw.extend(chunk)
        require(len(raw) == size)
        self._file(end)
        self._directories(end)
        return bytes(raw)

    def _binding(self, end):
        self._context(end)
        require(self.template is self.original_template and type(self.template) is codec.Template)
        # Construction validates the complete canonical template and case/root
        # relationship. Template contains only immutable bytes: rechecking those
        # exact independently pinned bytes needs no repeated plan decoding. File
        # contents, all retained descriptors and directory entries are still
        # freshly read/checked on EVERY call. Neither changed bytes nor a new
        # Template can become an accepted replacement.
        require(type(self.raw) is bytes and type(self.template.raw) is bytes)
        require(self.template.raw == self.raw)
        require(hashlib.sha256(self.raw).hexdigest() == self.expected)
        self._context(end)

    def recheck(self, *, deadline=None):
        """Read originals again; a caller deadline can only narrow this read."""
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            end = time.monotonic() + MAX_SECONDS
            if deadline is not None:
                require(type(deadline) in (int, float) and math.isfinite(deadline))
                end = min(end, deadline)
            self._binding(end)
            require(self._read(end) == self.raw)
            self._binding(end)
            return self.template
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()):
            try:
                self.close()
            except BaseException as cleanup:
                if isinstance(error, Exception) and not isinstance(cleanup, Exception):
                    raise cleanup
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedDeclaration(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        error = None
        if tuple(self.handles) != tuple(self.handle_pins):
            error = UnconfirmedDeclaration(MESSAGE)
        self.handles.clear()
        while self.handle_pins:
            # Retire each descriptor before closing it. Never retry an uncertain
            # close, or close an unrelated object reusing an original number.
            # Permission/flag drift still allows retiring an owned original.
            fd, (pin, _) = self.handle_pins.popitem()
            try:
                current = files.identity(os.fstat(fd))
                require(current[:2] == pin[:2] and stat.S_IFMT(current[2]) == stat.S_IFMT(pin[2]))
                os.close(fd)
            except BaseException as problem:
                if (
                    error is None
                    or isinstance(error, Exception)
                    and not isinstance(problem, Exception)
                ):
                    error = problem
        if error is not None:
            self.failed = True
            if not isinstance(error, Exception):
                raise error
            raise UnconfirmedDeclaration(MESSAGE) from None

    def __enter__(self):
        self.recheck()
        return self

    def __exit__(self, *_):
        self.close()


if __name__ == "__main__":
    raise SystemExit("Read-only declaration library only; no installed startup command enabled.")
