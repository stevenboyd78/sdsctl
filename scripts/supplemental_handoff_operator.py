#!/usr/bin/env python3
"""Private one-shot request/finish inbox; the service alone owns the journal.

No network listener, App command, arming action or automatic submission. A local
operator publishes a fresh case/boot/baseline-bound notice, which the already
running service may consume once. Files are retained as evidence, not reset.
"""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from supplemental_handoff_host import object_json
from supplemental_handoff_policy import Journal, checksum, clock, encode, identifier, require

MAX_AGE = 30
KINDS = ("request", "finish")
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def open_directory(path: Path) -> int:
    require(type(path) is type(Path()) and path.is_absolute() and path != Path("/"))
    require(all(part not in (".", "..") for part in path.parts))
    fd = os.open("/", FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def directory_unchanged(path: Path, fd: int) -> None:
    other = open_directory(path)
    try:
        before, after = os.fstat(fd), os.fstat(other)
        require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino))
    finally:
        os.close(other)


def names(fd: int) -> None:
    # Even an interrupted pending publication requires review; never delete it
    # or overwrite a partially confirmed operator action to make a retry work.
    with os.scandir(fd) as values:
        for index, value in enumerate(values):
            require(index < 2 and value.name in ("request.json", "finish.json"))


def notice(kind: str, case: str, boot: str, baseline: str, now: float) -> dict[str, Any]:
    from supplemental_handoff_policy import digest

    require(kind in KINDS)
    identifier(case, case=True)
    identifier(boot)
    digest(baseline)
    clock(now)
    return {
        "schema": 1,
        "kind": kind,
        "case_id": case,
        "boot_id": boot,
        "baseline": baseline,
        "issued_at": now,
    }


def publish(path: Path, value: dict[str, Any]) -> None:
    """Atomic exclusive publication; an uncertain result must not be retried."""
    require(
        type(value) is dict
        and set(value) == {"schema", "kind", "case_id", "boot_id", "baseline", "issued_at"}
    )
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(
        value
        == notice(
            value["kind"], value["case_id"], value["boot_id"], value["baseline"], value["issued_at"]
        )
    )
    raw = encode(value)
    require(len(raw) <= 1024)
    fd = open_directory(path)
    try:
        names(fd)
        destination = value["kind"] + ".json"
        require(destination not in os.listdir(fd))
        temporary = ".pending-" + uuid.uuid4().hex
        output = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        with os.fdopen(output, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        directory_unchanged(path, fd)
        os.link(temporary, destination, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
        directory_unchanged(path, fd)
    finally:
        os.close(fd)


class OperatorInbox:
    """Consume into the exclusively locked service journal, never dispatch.

    The journal's request/finish event is the durable consumption receipt. On
    restart it prevents a second consumption even if the retained notice is old.
    Invalid input cannot make the service skip its independent expiry/recovery
    loop. The caller catches an unconfirmed inbox separately from host evidence.
    """

    def __init__(self, path: Path, journal: Journal, read_clock: Callable[[], tuple[str, float]]):
        require(journal.fd >= 0 and journal.machine is not None)
        require(path != journal.path and not path.is_relative_to(journal.path))
        self.path, self.journal, self.read_clock = path, journal, read_clock
        self.fd = open_directory(path)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def consume(self) -> bool:
        require(self.fd >= 0 and self.journal.fd >= 0)
        machine = self.journal.machine
        require(machine is not None)
        assert machine is not None
        if machine.state.phase in ("complete", "review"):
            return False
        wanted = (
            "request"
            if machine.state.phase == "prepared"
            else "finish"
            if machine.state.phase == "candidate_running"
            else None
        )
        if wanted is None or any(e["event"]["kind"] == wanted for e in self.journal.entries):
            return False
        directory_unchanged(self.path, self.fd)
        names(self.fd)
        try:
            file = os.open(
                wanted + ".json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=self.fd,
            )
        except FileNotFoundError:
            return False
        try:
            before = os.fstat(file)
            require(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == os.getuid()
                and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600
                and 0 < before.st_size <= 1024
            )
            raw = os.read(file, 1025)
            after = os.fstat(file)
            named = os.stat(wanted + ".json", dir_fd=self.fd, follow_symlinks=False)
            require(len(raw) == before.st_size)
            require(
                all(
                    getattr(before, key) == getattr(after, key) == getattr(named, key)
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
        finally:
            os.close(file)
        value = object_json(raw)
        require(type(value.get("schema")) is int and value["schema"] == 1)
        issued = value.get("issued_at")
        clock(issued)
        issued = cast(float, issued)
        require(
            value
            == notice(
                wanted, machine.case_id, machine.boot_id, checksum(asdict(machine.baseline)), issued
            )
        )
        boot, now = self.read_clock()
        identifier(boot)
        clock(now)
        require(
            boot == machine.boot_id
            and now >= machine.last_at
            and now
            < min(
                machine.hard_deadline,
                machine.state.deadline if wanted == "request" else machine.state.trial_deadline,
            )
        )
        require(machine.created_at <= issued <= now and now - issued <= MAX_AGE)
        directory_unchanged(self.path, self.fd)
        self.journal.append({"kind": wanted, "boot_id": boot, "now": now})
        return True


if __name__ == "__main__":
    raise SystemExit("Private operator inbox component; nothing was submitted or started.")
