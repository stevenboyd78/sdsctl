#!/usr/bin/env python3
"""Uninstalled fixed-App-command bridge to one independently pinned idle lease.

The trusted host must publish a private launch receipt AFTER final-plan
acceptance. Reading the receipt does not authenticate the installed image or
authorize native/scanner/recording work. This bridge only claims its own startup
once and execs the existing finite idle program, which separately validates and
consumes the lease. No receipt is manufactured from observed lease bytes.

This is NOT in any current image/profile/qualifier. Docker's configured command
remains this bridge after exec; an explicit new qualification policy must check
both that command and the actual idle process before enabling any later action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from uuid import UUID

ENTRYPOINT = Path("/usr/local/libexec/sdsctl-recording-app-idle.py")
IDLE = "/opt/sdsctl-supplemental-recording/accept_supplemental_recording_idle.py"
PYTHON = "/usr/local/bin/python"
DATA = Path("/data")
ROOT_UID = ROOT_GID = 0
MAX_BYTES, MAX_SECONDS = 2048, 2.0
MESSAGE = "Finite App idle startup is unconfirmed; preserve this case and do not restart."
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def require(value):
    if not value:
        raise ValueError(MESSAGE)


def _case(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value))
    require(UUID(hex=value).version == 4)
    return value


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _reject(_):
    raise ValueError(MESSAGE)


def decode(raw, case):
    """Closed receipt syntax only; expected digests must come from the host."""
    _case(case)
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject)
    require(
        type(value) is dict
        and set(value) == {"schema", "kind", "case", "plan_sha256", "lease_sha256"}
    )
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(value["kind"] == "finite-recording-app-idle-launch-v1" and value["case"] == case)
    for key in ("plan_sha256", "lease_sha256"):
        require(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{64}", value[key]))
    require(_encode(value) == raw)
    return value


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


def _private_file(directory, name):
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
    require((before.st_uid, before.st_gid) == (ROOT_UID, ROOT_GID))
    require(stat.S_IMODE(before.st_mode) == 0o600)
    require(0 < before.st_size <= MAX_BYTES)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
        original = _identity(before)
        require(_identity(os.fstat(fd)) == original)
        raw = os.read(fd, MAX_BYTES + 1)
        require(len(raw) == before.st_size and not os.read(fd, 1))
        require(_identity(os.fstat(fd)) == original)
        require(_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == original)
        return raw, original
    finally:
        os.close(fd)


def _gate():
    require(sys.flags.isolated == sys.flags.dont_write_bytecode == 1)
    require(os.getpid() == 1 and os.geteuid() == os.getegid() == 0)
    require(os.getcwd() == "/" and Path(__file__) == ENTRYPOINT)


def _names(directory):
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            require(entry.name in {"launch.json", "consumed.json"} and entry.name not in names)
            names.add(entry.name)
    return names


def run(argv):
    """One receipt claim, then fixed exec; all uncertainty preserves residue."""
    _gate()
    require(type(argv) is list and len(argv) == 2 and argv[0] == "--case")
    case = _case(argv[1])
    # app-start is separate from idle, whose exact two-file inventory is unchanged.
    root = DATA / ("sdsctl-recording-" + case) / "app-start"
    opened, claim = [], -1
    end = time.monotonic() + MAX_SECONDS
    try:
        anchor = os.open("/", DIRECTORY)
        opened.append((None, None, anchor, None))
        info = os.fstat(anchor)
        require(info.st_uid == info.st_gid == 0 and info.st_mode & 0o7022 == 0)
        opened[-1] = (None, None, anchor, _identity(info)[:6])
        parent = anchor
        current = Path("/")
        for name in root.parts[1:]:
            require(time.monotonic() < end)
            child = os.open(name, DIRECTORY, dir_fd=parent)
            opened.append((parent, name, child, None))
            info = os.fstat(child)
            current /= name
            if current.is_relative_to(DATA):
                # DATA is fixed in the installed command; test substitution only.
                require((info.st_uid, info.st_gid) == (ROOT_UID, ROOT_GID))
                require(info.st_mode & 0o7022 == 0)
            opened[-1] = (parent, name, child, _identity(info)[:6])
            parent = child
        require(stat.S_IMODE(os.fstat(parent).st_mode) == 0o700)
        require(_names(parent) == {"launch.json"})
        raw, original = _private_file(parent, "launch.json")
        receipt = decode(raw, case)

        def unchanged():
            require(time.monotonic() < end)
            for ancestor, name, descriptor, identity in opened:
                require(_identity(os.fstat(descriptor))[:6] == identity)
                if ancestor is not None:
                    require(
                        _identity(os.stat(name, dir_fd=ancestor, follow_symlinks=False))[:6]
                        == identity
                    )
            require(_private_file(parent, "launch.json") == (raw, original))

        unchanged()
        claim_raw = _encode(
            {
                "schema": 1,
                "kind": "finite-recording-app-idle-consumed-v1",
                "case": case,
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "plan_sha256": receipt["plan_sha256"],
                "lease_sha256": receipt["lease_sha256"],
            }
        )
        claim = os.open(
            "consumed.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        created = _identity(os.fstat(claim))
        require(created[3:6] == (ROOT_UID, ROOT_GID, 1))
        require(stat.S_ISREG(created[2]) and stat.S_IMODE(created[2]) == 0o600)
        require(os.write(claim, claim_raw) == len(claim_raw))
        os.fsync(claim)
        os.fsync(parent)
        claim_evidence = _private_file(parent, "consumed.json")
        require(claim_evidence[0] == claim_raw)
        require(claim_evidence[1] == _identity(os.fstat(claim)))
        require(claim_evidence[1][:6] == created[:6])
        unchanged()
        require(_names(parent) == {"consumed.json", "launch.json"})
        require(_private_file(parent, "consumed.json") == claim_evidence)
        require(time.monotonic() < end)
        # No shell, PATH lookup, environment-selected executable, scanner import,
        # lease read, signal-handler replacement, retry, or successful-start receipt.
        os.execv(
            PYTHON,
            [
                PYTHON,
                "-I",
                "-B",
                IDLE,
                "--lease",
                str(DATA / ("sdsctl-recording-" + case) / "idle/lease.json"),
                "--lease-sha256",
                receipt["lease_sha256"],
            ],
        )
        require(False)  # A returned exec is never success.
    finally:
        # Each owned descriptor is attempted once. One failed close must not
        # skip the others or lead to retrying a possibly reused descriptor.
        problem, close_failed = sys.exception(), False
        owned = ([claim] if claim >= 0 else []) + [item[2] for item in reversed(opened)]
        for descriptor in owned:
            try:
                os.close(descriptor)
            except BaseException:
                close_failed = True
        if close_failed and problem is None:
            raise ValueError(MESSAGE) from None


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
    except BaseException:
        print(MESSAGE, file=sys.stderr)
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
