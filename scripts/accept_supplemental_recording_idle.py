#!/usr/bin/env python3
"""Uninstalled finite PID-1 bootstrap. Never opens a scanner or starts a daemon.

A new separately qualified image/host plan must select this instead of the old
scanner-owning App entrypoint. It consumes one private lease claim and exits on
its ORIGINAL deadline or TERM/INT. It neither launches the native operator nor
reports it healthy. Host recovery and exact init-exit witnesses remain separate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import stat
import sys
import time
from pathlib import Path
from threading import Event
from uuid import UUID

DATA = Path("/data")
BOOT = Path("/proc/sys/kernel/random/boot_id")
ROOT_UID = ROOT_GID = 0
MAX_BYTES = 4096
MESSAGE = "Finite recording idle startup is unconfirmed; preserve this case and do not restart."
LEASE_EXPIRED = 75


def require(value):
    if not value:
        raise ValueError(MESSAGE)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _constant(_value):
    raise ValueError(MESSAGE)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _file(directory, name):
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
    require(before.st_uid == ROOT_UID and stat.S_IMODE(before.st_mode) == 0o600)
    require(0 < before.st_size <= MAX_BYTES)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
        require(_identity(os.fstat(fd)) == _identity(before))
        raw = os.read(fd, MAX_BYTES + 1)
        require(len(raw) == before.st_size and not os.read(fd, 1))
        require(_identity(os.fstat(fd)) == _identity(before))
        require(
            _identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == _identity(before)
        )
        return raw, _identity(before)
    finally:
        os.close(fd)


def _boot():
    with BOOT.open("rb", buffering=0) as stream:
        raw = stream.read(38)
    require(len(raw) == 37 and raw[-1:] == b"\n")
    value = UUID(raw[:-1].decode("ascii"))
    require(str(value).encode() == raw[:-1])
    return value.hex


def decode(raw, *, sha256, path, now, boot):
    """Pure closed lease parsing; neither a launch nor readiness authorization."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    require(type(sha256) is str and re.fullmatch(r"[0-9a-f]{64}", sha256))
    require(hashlib.sha256(raw).hexdigest() == sha256)
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    require(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "kind",
            "case",
            "boot",
            "clock",
            "issued_at",
            "ready_by",
            "stop_by",
        }
    )
    require(_encode(value) == raw and type(value["schema"]) is int and value["schema"] == 1)
    require(value["kind"] == "finite-recording-container-lease")
    require(value["clock"] == "CLOCK_MONOTONIC")
    require(type(value["case"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["case"]))
    require(type(boot) is str and re.fullmatch(r"[0-9a-f]{32}", boot) and value["boot"] == boot)
    require(type(path) is type(Path()))
    require(path == DATA / ("sdsctl-recording-" + value["case"]) / "idle" / "lease.json")
    for number in (now, value["issued_at"], value["ready_by"], value["stop_by"]):
        require(type(number) in (int, float) and math.isfinite(number) and number >= 0)
    require(value["issued_at"] <= now < value["ready_by"] < value["stop_by"])
    require(value["ready_by"] - value["issued_at"] <= 600)
    require(value["stop_by"] - value["issued_at"] <= 780)
    return value


def run(argv):
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1)
    require(os.getpid() == 1 and os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID)
    require(type(argv) is list and len(argv) == 4)
    require(argv[0] == "--lease" and argv[2] == "--lease-sha256")
    path, sha256 = Path(argv[1]), argv[3]
    require(path.is_absolute() and str(path) == argv[1] and ".." not in path.parts)
    require(path.name == "lease.json" and path.is_relative_to(DATA))
    # Fixed depth rules out arbitrary directory walks before opening any path.
    relative = path.relative_to(DATA)
    require(len(relative.parts) == 3 and relative.parts[1] == "idle")
    require(re.fullmatch(r"sdsctl-recording-[0-9a-f]{32}", relative.parts[0]))
    stopped = Event()
    previous = {}
    opened = []
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: stopped.set())
        root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened.append((None, None, root, None))  # Retain before a fallible stat.
        info = os.fstat(root)
        opened[-1] = (None, None, root, _identity(info)[:6])
        require(info.st_uid == 0 and info.st_mode & 0o7022 == 0)
        parent = root
        current = Path("/")
        for name in path.parent.parts[1:]:
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
            )
            opened.append((parent, name, child, None))
            info = os.fstat(child)
            opened[-1] = (parent, name, child, _identity(info)[:6])
            current /= name
            if current.is_relative_to(DATA):
                # DATA is fixed /data in the executable. Tests substitute an
                # owned deeper root, never a public argument or environment.
                require(info.st_uid == ROOT_UID and info.st_mode & 0o7022 == 0)
            parent = child
        require(stat.S_IMODE(os.fstat(parent).st_mode) == 0o700)
        require(sorted(os.listdir(parent)) == ["lease.json"])
        raw, lease_identity = _file(parent, "lease.json")
        boot = _boot()
        lease = decode(raw, sha256=sha256, path=path, now=time.monotonic(), boot=boot)

        def unchanged():
            for ancestor, name, fd, identity in opened:
                require(_identity(os.fstat(fd))[:6] == identity)
                if ancestor is not None:
                    require(
                        _identity(os.stat(name, dir_fd=ancestor, follow_symlinks=False))[:6]
                        == identity
                    )
            require(_file(parent, "lease.json") == (raw, lease_identity))
            require(_boot() == boot)

        unchanged()
        claim = _encode(
            {
                "schema": 1,
                "kind": "finite-recording-idle-claim",
                "case": lease["case"],
                "lease_sha256": sha256,
                "started_at": time.monotonic(),
            }
        )
        fd = os.open(
            "consumed.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        with os.fdopen(fd, "wb") as stream:
            require(stream.write(claim) == len(claim))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
        claim_evidence = _file(parent, "consumed.json")
        require(claim_evidence[0] == claim)
        unchanged()
        require(time.monotonic() < lease["ready_by"])
        while not stopped.is_set():
            remaining = lease["stop_by"] - time.monotonic()
            if remaining <= 0:
                return LEASE_EXPIRED
            unchanged()
            require(sorted(os.listdir(parent)) == ["consumed.json", "lease.json"])
            require(_file(parent, "consumed.json") == claim_evidence)
            stopped.wait(min(0.1, remaining))
        return 0
    finally:
        for _, _, fd, _ in reversed(opened):
            os.close(fd)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except BaseException:
        print(MESSAGE, file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
