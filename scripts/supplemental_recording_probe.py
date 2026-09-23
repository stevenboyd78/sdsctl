#!/usr/bin/env python3
"""Passive cached recording probe bound to previously authenticated process facts.

This is an uninstalled component, NOT an authenticator for caller-supplied PIDs
or deadlines. The independent host must obtain these namespace identities and
the original deadline from its exact source-qualified operator attachment. The
installed exec wrapper and host-plan integration are separate unfinished gates.
There is no scanner request, consumer demand, stop, signal, replay or restoration.
"""

from __future__ import annotations

import math
import os
import select
import time
from dataclasses import dataclass
from pathlib import Path

import supplemental_handoff_cached as cached

MESSAGE = "Finite recording cached probe is unconfirmed; no ownership change is authorized."
MAX_SECONDS = 2.5


class UnconfirmedProbe(ValueError):
    """Unknown health is never rewritten to healthy or recording-idle."""


def require(value):
    if not value:
        raise UnconfirmedProbe(MESSAGE)


@dataclass(frozen=True)
class Process:
    pid: int
    start_ticks: int
    uid: int
    gid: int

    def validate(self):
        require(
            all(type(value) is int for value in (self.pid, self.start_ticks, self.uid, self.gid))
        )
        require(self.pid > 1 and self.start_ticks > 0 and self.uid >= 0 and self.gid >= 0)


@dataclass(frozen=True)
class Expected:
    guardian: Process
    native: Process
    watchdog: Process
    profile_sha256: str
    deadline: float

    def validate(self):
        for process in (self.guardian, self.native, self.watchdog):
            require(type(process) is Process)
            process.validate()
            require((process.uid, process.gid) == (os.geteuid(), os.getegid()))
        require(len({self.guardian.pid, self.native.pid, self.watchdog.pid, os.getpid()}) == 4)
        require(type(self.profile_sha256) is str and len(self.profile_sha256) == 64)
        require(all(char in "0123456789abcdef" for char in self.profile_sha256))
        require(type(self.deadline) in (int, float) and math.isfinite(self.deadline))
        require(0 < self.deadline - time.monotonic() <= 780)


def _identity(process):
    """Bounded local proc reads; a stopped/frozen supervisor is not healthy."""
    with open(f"/proc/{process.pid}/stat", "rb", buffering=0) as stream:
        raw = stream.read(4097)
    require(len(raw) <= 4096)
    prefix, delimiter, suffix = raw.rpartition(b") ")
    require(delimiter and prefix.startswith(str(process.pid).encode() + b" ("))
    fields = suffix.split()
    require(len(fields) >= 20 and fields[0] in (b"R", b"S", b"I"))
    require(fields[1].isdigit() and fields[19].isdigit())
    require(int(fields[19]) == process.start_ticks)
    with open(f"/proc/{process.pid}/status", "rb", buffering=0) as stream:
        raw = stream.read(16385)
    require(len(raw) <= 16384)
    for name, expected in ((b"Uid:", process.uid), (b"Gid:", process.gid)):
        matches = [line.split()[1:] for line in raw.splitlines() if line.startswith(name)]
        require(len(matches) == 1 and len(matches[0]) == 4)
        require(all(value.isdigit() and int(value) == expected for value in matches[0]))
    own, other = os.stat("/proc/self/ns/pid"), os.stat(f"/proc/{process.pid}/ns/pid")
    require((own.st_dev, own.st_ino) == (other.st_dev, other.st_ino))
    return int(fields[1])


def collect(expected, deployment, recordings, daemon_socket, *, firmware):
    """Five existing cached IPC reads with live-bound guardian/native/watch PIDs.

    Exact namespace/source/exec provenance is a caller prerequisite, not a claim
    made by a matching Process tuple. Frozen, missing, replaced or reparented
    processes, changed profile and expired original deadline all fail closed.
    The recording flag remains the actual cache value even when it is active.
    """
    handles = []
    began = time.monotonic()
    try:
        require(type(expected) is Expected)
        expected.validate()
        for path in (deployment, recordings, daemon_socket):
            require(
                type(path) is type(Path())
                and path.is_absolute()
                and path != Path("/")
                and ".." not in path.parts
            )
        for path in (deployment, daemon_socket):
            require(not path.is_relative_to(recordings) and not recordings.is_relative_to(path))
        processes = (expected.guardian, expected.native, expected.watchdog)
        for process in processes:
            handles.append(os.pidfd_open(process.pid))
        parents = [_identity(process) for process in processes]
        require(parents[1:] == [expected.guardian.pid, expected.guardian.pid])
        require(not select.select(handles, [], [], 0)[0])
        require(time.monotonic() < min(expected.deadline, began + MAX_SECONDS))
        observed = cached.collect_cached(
            deployment, recordings, daemon_socket, firmware=firmware, supplemental=True
        )
        require(type(observed) is cached.CachedEvidence)
        require(
            observed.profile_sha256 == expected.profile_sha256
            and observed.peer_pid == expected.native.pid
            and observed.peer_start_ticks == str(expected.native.start_ticks)
            and observed.supplemental_advertised is True
        )
        require(type(observed.healthy) is bool and type(observed.recording) is bool)
        require([_identity(process) for process in processes] == parents)
        require(not select.select(handles, [], [], 0)[0])
        require(time.monotonic() < min(expected.deadline, began + MAX_SECONDS))
        return observed
    except Exception:
        raise UnconfirmedProbe(MESSAGE) from None
    finally:
        for fd in handles:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Private read-only probe only; no installed executable or host plan enabled.")
