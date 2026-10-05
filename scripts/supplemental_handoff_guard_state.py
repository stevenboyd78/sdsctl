#!/usr/bin/env python3
"""Read-only candidate guardian/readiness proof inside its PID namespace.

No arming, signals, process creation, scanner requests, repair or evidence writes.
The host has already verified the App image, package and finite wrapper bytes.
Binding uses the actual cached IPC peer, not a PID merely asserted by a report.
"""

from __future__ import annotations

import json
import os
import re
import select
import stat
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from supplemental_handoff_cached import CachedEvidence, UnconfirmedCache, process_ticks, require


def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in values:
        require(key not in result)
        result[key] = value
    return result


def reject(_: str) -> None:
    raise UnconfirmedCache()


def read_report(path: Path) -> dict[str, Any]:
    """Read one <=4KiB regular private file through a no-follow ancestor chain."""
    require(path.is_absolute() and ".." not in path.parts)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    file = -1
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.fstat(directory)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        file = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(file)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1
            and 0 < info.st_size <= 4096
        )
        raw = os.read(file, 4097)
        after = os.fstat(file)
        named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        require(len(raw) == info.st_size)
        require(
            all(
                getattr(info, key) == getattr(after, key) == getattr(named, key)
                for key in (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_uid",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            )
        )
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
        require(type(value) is dict)
        return cast(dict[str, Any], value)
    finally:
        if file >= 0:
            os.close(file)
        os.close(directory)


def parent_pid(pid: int) -> int:
    with open(f"/proc/{pid}/stat", "rb", buffering=0) as stream:
        raw = stream.read(4097)
    require(len(raw) <= 4096)
    before, delimiter, after = raw.decode("ascii").rpartition(") ")
    require(bool(delimiter) and before.startswith(str(pid) + " ("))
    fields = after.split()
    require(len(fields) >= 20 and fields[1].isascii() and fields[1].isdigit())
    return int(fields[1])


def guardian_live(evidence: CachedEvidence, *, case: str, source: str) -> bool:
    """True=current live bound guardian; False=its durable ended report; else error."""
    handles: list[int] = []
    try:
        require(type(evidence) is CachedEvidence and evidence.supplemental_advertised is True)
        require(
            type(case) is str
            and re.fullmatch(r"[0-9a-f]{32}", case) is not None
            and UUID(hex=case).version == 4
        )
        require(type(source) is str and re.fullmatch(r"[0-9a-f]{40}", source) is not None)
        root = Path("/data/sdsctl-supplemental-acceptance-" + case)
        try:
            ended = read_report(root / "guard-result.json")
        except FileNotFoundError:
            ended = None
        if ended is not None:
            require(
                ended.get("state") == "ended" and type(ended.get("child_exit_confirmed")) is bool
            )
            return False
        started = read_report(root / "guard-started.json")
        ready_path = root / "daemon-case/ready.json"
        ready = read_report(ready_path)
        require(
            started.get("state") == "guard_started" and started.get("restoration_verified") is False
        )
        require(
            type(started.get("deadline_seconds")) in (int, float)
            and started["deadline_seconds"] == 684
        )
        require(
            type(started.get("grace_seconds")) in (int, float) and started["grace_seconds"] == 3
        )
        guard = started.get("pid")
        ticks = started.get("start_ticks")
        require(type(guard) is int and guard > 1 and guard != evidence.peer_pid)
        guard = cast(int, guard)
        require(type(ticks) is str and ticks.isascii() and ticks.isdigit() and int(ticks) > 0)
        require(
            ready.get("kind") == "explicit-demand-clock-favorites-v1"
            and ready.get("state") == "waiting_for_operator"
        )
        require(
            type(ready.get("pid")) is int
            and ready["pid"] == evidence.peer_pid
            and ready.get("start_ticks") == evidence.peer_start_ticks
        )
        require(ready.get("source_revision") == source)
        require(
            type(ready.get("generation")) is str
            and re.fullmatch(r"[0-9a-f]{32}", ready["generation"]) is not None
        )
        require(ready.get("reads_require_explicit_consumer_demand") is True)
        for key, expected in (
            ("ready_timeout_seconds", 600),
            ("window_seconds", 64),
            ("max_read_attempts", 60),
        ):
            require(type(ready.get(key)) in (int, float) and ready[key] == expected)
        for pid, expected_ticks in ((guard, ticks), (evidence.peer_pid, evidence.peer_start_ticks)):
            require(process_ticks(pid) == expected_ticks)
            handles.append(os.pidfd_open(pid))
            require(process_ticks(pid) == expected_ticks)
        require(parent_pid(evidence.peer_pid) == guard)
        require(
            read_report(root / "guard-started.json") == started and read_report(ready_path) == ready
        )
        try:
            read_report(root / "guard-result.json")
        except FileNotFoundError:
            pass
        else:
            return False
        for handle in handles:
            poller = select.poll()
            poller.register(handle, select.POLLIN)
            require(not poller.poll(0))
        require(
            process_ticks(guard) == ticks
            and process_ticks(evidence.peer_pid) == evidence.peer_start_ticks
            and parent_pid(evidence.peer_pid) == guard
        )
        return True
    except Exception:
        raise UnconfirmedCache() from None
    finally:
        for handle in handles:
            os.close(handle)


if __name__ == "__main__":
    raise SystemExit("Read-only candidate guardian collector; nothing was armed or signaled.")
