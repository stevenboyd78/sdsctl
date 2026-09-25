#!/usr/bin/env python3
"""Uninstalled one-use publication of an accepted plan's ORIGINAL idle inputs.

Only a successfully accepted service-prepared Startup can call this explicitly,
before passive service assembly. No App/Engine, scanner or recording command is
sent. Existing profiles/commands do not select this module. Installed provenance,
candidate absence, fixed image command, independent recovery and later actual
process/claim qualification remain separate obligations, not inferred from files.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass

import supplemental_recording_service_startup as startup

plans, files = startup.plans, startup.declaration.files
ROOT_UID = ROOT_GID = 0
MAX_SECONDS = 2.0
MESSAGE = "App idle inputs are unconfirmed; preserve this case and do not republish."


class UnconfirmedPublication(ValueError):
    """Ambiguous output is neither readiness nor permission to restart."""


def require(value):
    if not value:
        raise UnconfirmedPublication(MESSAGE)


def _secure(info):
    require(stat.S_ISDIR(info.st_mode))
    require((info.st_uid, info.st_gid) == (ROOT_UID, ROOT_GID))
    require(info.st_mode & 0o7022 == 0)


@contextmanager
def _chain(path, guard):
    """Retain every real host ancestor; no alias or permissive runtime option."""
    opened = []
    try:
        parent = os.open("/", files.DIRECTORY)
        opened.append((None, None, parent, None))
        info = os.fstat(parent)
        _secure(info)
        opened[-1] = (None, None, parent, files.identity(info)[:5])
        for name in path.parts[1:]:
            guard()
            child = os.open(name, files.DIRECTORY, dir_fd=parent)
            opened.append((parent, name, child, None))
            info = os.fstat(child)
            _secure(info)
            opened[-1] = (parent, name, child, files.identity(info)[:5])
            parent = child

        def unchanged():
            guard()
            for ancestor, name, fd, original in opened:
                require(files.identity(os.fstat(fd))[:5] == original)
                if ancestor is not None:
                    require(
                        files.identity(os.stat(name, dir_fd=ancestor, follow_symlinks=False))[:5]
                        == original
                    )

        unchanged()
        yield parent, unchanged
        unchanged()
    finally:
        _close([entry[2] for entry in reversed(opened)])


def _close(descriptors):
    problem, failed = sys.exception(), False
    for fd in descriptors:
        try:
            os.close(fd)
        except BaseException:
            failed = True
    if failed and problem is None:
        raise UnconfirmedPublication(MESSAGE) from None


def _data(plan):
    return next(layout.data for layout in plan.layouts if layout.slug == plans.base.CANDIDATE)


@dataclass(frozen=True)
class Published:
    """Original output pins only; never self-authenticating launch authority."""

    plan_sha256: str
    lease_sha256: str
    receipt_sha256: str
    root_identity: tuple
    file_identities: tuple


@dataclass(frozen=True)
class NativePublished(Published):
    """Explicit native-tree preparation; not accepted by idle-only qualifiers.

    Empty output/launch directories are pinned before PID1 or a native worker
    can run. The original projected baseline is copied, never recaptured.
    Native launch publication, generation binding and authorization are absent.
    """

    baseline_sha256: str
    directory_identities: tuple


def publish(owner):
    """Write the original lease first, then its independent launch receipt last.

    One exclusive case directory is the durable intent. Any partial directory,
    write, fsync or lost close acknowledgement remains in place. A consumed owner
    cannot try again, even when failure happened before the first filesystem
    mutation. Borrowed startup/clock/plan resources are never closed here.
    """
    return _publish(owner, native=False)


def publish_native(owner):
    """Reserve a DISTINCT closed native tree before any init/process pinning.

    The same one-attempt owner flag is consumed as idle-only publish(). Neither
    route may follow the other. No launch.json is fabricated: its generation
    can be bound only after a later independently qualified actual App start.
    Existing commands/profiles do not call this or accept its output type.
    """
    return _publish(owner, native=True)


def _publish(owner, *, native):
    locked, owned = False, []
    try:
        require(type(native) is bool)
        require(type(owner) is startup.Startup)
        require(owner.lock.acquire(blocking=False))
        locked = True
        require(owner.app_idle_publication_used is False)
        owner.app_idle_publication_used = True
        require(owner.accepted and owner._service_inputs is not None and not owner.service_used)
        owner._guard()
        original, clock, plan = owner.original, owner.clock, owner.original.recheck()
        pinned = plans.PinnedPlan(plan)
        end = min(time.monotonic() + MAX_SECONDS, plan.lease["ready_by"])

        def guard():
            require(time.monotonic() < end and not owner.service_used)
            require(owner.app_idle_publication_used is True and owner.accepted)
            require(owner.original is original and owner.clock is clock)
            require(owner._service_inputs is not None)
            owner._guard()
            require(original.recheck() is plan)
            pinned.check(plan)
            observed = clock.read()
            plan.check_clock(observed)
            require(observed.boottime_ns / plans.clock.NS < plan.deadlines.ready_by)
            require(time.monotonic() < end)

        lease = plans.base.encode(plan.lease)
        require(hashlib.sha256(lease).hexdigest() == plan.lease_sha256)
        receipt = plans.base.encode(
            dict(
                schema=1,
                kind="finite-recording-app-idle-launch-v1",
                case=plan.case,
                plan_sha256=plan.sha256,
                lease_sha256=plan.lease_sha256,
            )
        )
        require(0 < len(lease) <= 4096 and 0 < len(receipt) <= 2048)
        payloads = [("idle", "lease.json", lease, 4096)]
        empty = ("launch", "sockets", "receipts") if native else ()
        if native:
            plan.check_projection(owner.projected)
            baseline = owner.projected.native_manifest
            limit = plans.projection.recording.MAX_MANIFEST_BYTES
            require(0 < len(baseline) <= limit)
            require(hashlib.sha256(baseline).hexdigest() == plan.native_baseline_sha256)
            payloads.append(("baseline", "baseline.json", baseline, limit))
        # Publish the bridge receipt LAST, after every directory/input is ready.
        payloads.append(("app-start", "launch.json", receipt, 2048))
        directories = tuple(empty) + tuple(item[0] for item in payloads)
        name = plan.native_root.relative_to("/data").as_posix()
        require(name == "sdsctl-recording-" + plan.case)
        with _chain(_data(plan), guard) as (data, ancestry):
            _secure(os.fstat(data))
            fcntl.flock(data, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = os.fstat(data).st_nlink
            guard()
            os.mkdir(name, mode=0o700, dir_fd=data)
            os.fsync(data)
            root = os.open(name, files.DIRECTORY, dir_fd=data)
            owned.append(root)
            info = os.fstat(root)
            _secure(info)
            require(stat.S_IMODE(info.st_mode) == 0o700 and not os.listdir(root))
            root_identity = files.identity(info)[:5]
            root_links = info.st_nlink
            records, reserved = [], []
            for directory in empty:
                ancestry()
                os.mkdir(directory, mode=0o700, dir_fd=root)
                os.fsync(root)
                fd = os.open(directory, files.DIRECTORY, dir_fd=root)
                owned.append(fd)
                _secure(os.fstat(fd))
                require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700 and not os.listdir(fd))
                reserved.append((directory, fd, files.identity(os.fstat(fd))))
            for directory, filename, raw, limit in payloads:
                ancestry()
                os.mkdir(directory, mode=0o700, dir_fd=root)
                os.fsync(root)
                fd = os.open(directory, files.DIRECTORY, dir_fd=root)
                owned.append(fd)
                _secure(os.fstat(fd))
                require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700 and not os.listdir(fd))
                directory_identity = files.identity(os.fstat(fd))[:6]
                output = os.open(
                    filename,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=fd,
                )
                owned.append(output)
                require(os.write(output, raw) == len(raw))
                os.fsync(output)
                os.fsync(fd)
                stated = os.stat(filename, dir_fd=fd, follow_symlinks=False)
                require(files.identity(stated) == files.identity(os.fstat(output)))
                require(stat.S_ISREG(stated.st_mode) and stated.st_nlink == 1)
                require((stated.st_uid, stated.st_gid) == (ROOT_UID, ROOT_GID))
                require(stat.S_IMODE(stated.st_mode) == 0o600 and stated.st_size == len(raw))
                records.append(
                    (
                        directory,
                        filename,
                        fd,
                        raw,
                        files.identity(stated),
                        directory_identity,
                        limit,
                    )
                )
            for directory, filename, fd, raw, identity, directory_identity, limit in records:
                ancestry()
                require(files.identity(os.fstat(fd))[:6] == directory_identity)
                require(os.listdir(fd) == [filename])
                observed = startup.publication.protected.evidence.read_bytes(
                    fd, filename, limit=limit, deadline=end
                )
                require(observed == raw)
                require(
                    files.identity(os.stat(filename, dir_fd=fd, follow_symlinks=False)) == identity
                )
                require(
                    files.identity(os.fstat(fd))
                    == files.identity(os.stat(directory, dir_fd=root, follow_symlinks=False))
                )
            for directory, fd, identity in reserved:
                ancestry()
                require(not os.listdir(fd))
                require(files.identity(os.fstat(fd)) == identity)
                require(
                    files.identity(os.stat(directory, dir_fd=root, follow_symlinks=False))
                    == identity
                )
            require(sorted(os.listdir(root)) == sorted(directories))
            require(files.identity(os.fstat(root))[:5] == root_identity)
            require(os.fstat(root).st_nlink == root_links + len(directories))
            require(
                files.identity(os.stat(name, dir_fd=data, follow_symlinks=False))
                == files.identity(os.fstat(root))
            )
            require(os.fstat(data).st_nlink == before + 1)
            ancestry()
            arguments = (
                plan.sha256,
                plan.lease_sha256,
                hashlib.sha256(receipt).hexdigest(),
                files.identity(os.fstat(root)),
                tuple(
                    (directory + "/" + filename, identity)
                    for directory, filename, _, _, identity, _, _ in records
                ),
            )
            if native:
                identities = tuple(
                    sorted(
                        [(name, files.identity(os.fstat(fd))[:6]) for name, fd, _ in reserved]
                        + [
                            (name, files.identity(os.fstat(fd))[:6])
                            for name, _, fd, _, _, _, _ in records
                        ]
                    )
                )
                result = NativePublished(*arguments, plan.native_baseline_sha256, identities)
            else:
                result = Published(*arguments)
        handles, owned = list(reversed(owned)), []
        _close(handles)
        guard()
        return result
    except BaseException as error:
        if not isinstance(error, Exception):
            raise
        raise UnconfirmedPublication(MESSAGE) from None
    finally:
        try:
            handles, owned = list(reversed(owned)), []
            _close(handles)
        finally:
            if locked:
                owner.lock.release()
