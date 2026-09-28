#!/usr/bin/env python3
"""Exclusive pre-start publication of independently pinned peer inputs.

An operator must supply reviewed canonical bytes AND independently authenticated
digests. This module never learns expected values from installed files, peers,
Engine or a service offer. It provisions only the two fixed input directories;
it does not create a case, clock, process, listener, plan, journal or App action.
No existing command or source profile selects this uninstalled library.
"""

from __future__ import annotations

import fcntl
import math
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from threading import get_ident

import supplemental_recording_peer_inputs as inputs

declarations, codec, files = inputs.declarations, inputs.codec, inputs.files
PARENT = Path("/mnt/data")
SECONDS, ROOT_UID = 2.0, 0
MESSAGE = "Peer input publication is unconfirmed; preserve all created files and do not retry."


class UnconfirmedPublication(ValueError):
    """No private bytes, paths or underlying exception details are exposed."""


def require(value):
    if not value:
        raise UnconfirmedPublication(MESSAGE)


@dataclass(frozen=True)
class Publication:
    """Provisioning receipt only, not file custody, provenance or action authority.

    The caller must keep its original independently authenticated digests. A
    later reader must retain/recheck the files itself; this receipt cannot prove
    they stayed unchanged after return, or authenticate the supplying operator.
    """

    case: str
    template_sha256: str
    expectations_sha256: str


def publish(template_raw, template_sha256, expectations_raw, expectations_sha256, *, deadline=None):
    """Create both fixed, private inputs once; never adopt, repair or overwrite.

    Validate the COMPLETE pair before any filesystem write. Both targets and
    the writable case must be absent; mkdir/O_EXCL remain the final guards.
    Partial and complete artifacts survive every failure, including a lost
    acknowledgment. There is no rollback, automatic retry or incomplete-input
    adoption. Two directories are not an atomic transaction: no peer may start
    until the independent owner has verified this publication and its inputs.

    All operations share one two-second elapsed-time budget. Blocking kernel
    I/O still requires independently enforced outer supervision. No live clock
    origin is captured. Ancestor installation trust, caller/runtime provenance,
    entrypoint selection, platform bounds and App consent remain separate.
    """
    owned, chain, records = [], [], []
    owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
    problem = None
    try:
        began = time.monotonic()
        end = began + SECONDS
        if deadline is not None:
            require(type(deadline) in (int, float) and math.isfinite(deadline))
            end = min(end, deadline)
        require(owner[2:] == (ROOT_UID, ROOT_UID))
        require(type(template_raw) is bytes and type(expectations_raw) is bytes)
        require(type(template_sha256) is str and type(expectations_sha256) is str)
        template = declarations.codec.load_bytes(template_raw, template_sha256)
        expected = codec.load_bytes(expectations_raw, expectations_sha256)
        expected.check_template(template)
        case = declarations.codec._read(template_raw)["plan"]["case"]
        roots = declarations.declaration_root(case), inputs.inputs_root(case)
        require(roots[0] != roots[1] and all(root.parent == PARENT for root in roots))
        require(type(PARENT) is type(Path()) and PARENT.is_absolute() and Path("/") != PARENT)
        require(".." not in PARENT.parts and len(PARENT.parts) <= 32)

        def guard():
            require(owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
            require(time.monotonic() < end)
            for fd, pin, flags in owned:
                require(files.identity(os.fstat(fd))[:5] == pin)
                require(not os.get_inheritable(fd))
                require(fcntl.fcntl(fd, fcntl.F_GETFL) == flags)
            for parent, name, fd in chain:
                require(
                    files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:5]
                    == files.identity(os.fstat(fd))[:5]
                )

        def opened(name, flags, mode=0o600, *, parent=None):
            guard()
            fd = os.open(name, flags, mode, dir_fd=parent)
            try:
                pin = files.identity(os.fstat(fd))[:5]
                current_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            except BaseException:
                os.close(fd)
                raise
            owned.append((fd, pin, current_flags))
            guard()
            return fd

        parent = opened("/", files.DIRECTORY)
        for name in PARENT.parts[1:]:
            child = opened(name, files.DIRECTORY, parent=parent)
            chain.append((parent, name, child))
            parent = child
        guard()
        info = os.fstat(parent)
        require((info.st_uid, info.st_gid) == owner[2:])
        require(not stat.S_IMODE(info.st_mode) & 0o7022)
        case_root = PARENT / ("sdsctl-recording-handoff-" + case)
        for root in (*roots, case_root):
            try:
                os.stat(root.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                require(False)
        guard()
        for root, name, raw in zip(
            roots, (declarations.NAME, inputs.NAME), (template_raw, expectations_raw), strict=True
        ):
            os.mkdir(root.name, 0o700, dir_fd=parent)
            child = opened(root.name, files.DIRECTORY, parent=parent)
            chain.append((parent, root.name, child))
            # Never chmod existing paths or relax a restrictive umask. Refuse
            # and preserve the new directory if its exact private mode differs.
            require(stat.S_IMODE(os.fstat(child).st_mode) == 0o700)
            require((os.fstat(child).st_uid, os.fstat(child).st_gid) == owner[2:])
            output = opened(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                parent=child,
            )
            chain.append((child, name, output))
            require(stat.S_IMODE(os.fstat(output).st_mode) == 0o600)
            require((os.fstat(output).st_uid, os.fstat(output).st_gid) == owner[2:])
            offset = 0
            while offset < len(raw):
                guard()
                written = os.write(output, raw[offset:])
                require(type(written) is int and 0 < written <= len(raw) - offset)
                offset += written
            guard()
            os.fsync(output)
            guard()
            os.fsync(child)
            guard()
            records.append((child, name, output, files.identity(os.fstat(output)), raw))
        os.fsync(parent)
        guard()
        for child, name, output, pin, raw in records:
            require(pin[5] == 1 and pin[6] == len(raw))
            with os.scandir(child) as entries:
                entry = next(entries, None)
                require(entry is not None and entry.name == name and next(entries, None) is None)
            require(files.identity(os.fstat(output)) == pin)
            require(os.pread(output, len(raw) + 1, 0) == raw)
            require(files.identity(os.fstat(output)) == pin)
            guard()
        result = Publication(case, template_sha256, expectations_sha256)
    except BaseException as error:
        problem = error
    finally:
        # Retire originals, not a newly reused descriptor. Preserve disk state
        # even on partial write/fsync/verification/cleanup/acknowledgment failure.
        for fd, pin, _ in reversed(owned):
            try:
                current = files.identity(os.fstat(fd))
                require(current[:2] == pin[:2] and stat.S_IFMT(current[2]) == stat.S_IFMT(pin[2]))
                os.close(fd)
            except BaseException as error:
                if problem is None or not isinstance(error, Exception):
                    problem = error
    if problem is None and time.monotonic() >= end:
        problem = UnconfirmedPublication(MESSAGE)
    if problem is not None:
        if not isinstance(problem, Exception):
            raise problem
        raise UnconfirmedPublication(MESSAGE) from None
    return result


if __name__ == "__main__":
    raise SystemExit("Explicit provisioning library only; no command or active launch enabled.")
