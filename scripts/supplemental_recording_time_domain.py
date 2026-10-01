#!/usr/bin/env python3
"""Read-only zero-offset Linux time-domain proof, not installed launch policy.

Some container runtimes create distinct time namespaces with zero offsets. A
matching numeric clock sample is insufficient evidence of equivalence. This
separate component retains namespace descriptors and an already bound init
pidfd, and reads both kernel offsets for the actual current/child domains.
It never enters a namespace, sets a clock, changes an offset or renews a lease.
The existing strict same-domain call sites do not select it implicitly.
"""

from __future__ import annotations

import os
import re
import select
import time
from dataclasses import asdict, dataclass
from threading import Lock, get_ident

import supplemental_handoff_process as process
import supplemental_recording_clock as clock
from supplemental_handoff_policy import checksum

MESSAGE = "Recording time-domain equivalence is unconfirmed; preserve the original deadlines."
ROOT_UID = 0
MAX_BYTES = 256


class UnconfirmedDomain(ValueError):
    """A serialized result cannot reconstruct the live process/domain witness."""


def require(value):
    if not value:
        raise UnconfirmedDomain(MESSAGE)


def zero_offsets(raw):
    """Closed bounded kernel format; no coercion of unknown/nonzero clocks."""
    try:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
        require(
            re.fullmatch(rb"monotonic[ \t]+0[ \t]+0\nboottime[ \t]+0[ \t]+0\n", raw) is not None
        )
    except Exception:
        raise UnconfirmedDomain(MESSAGE) from None


def _identity(info):
    return info.st_dev, info.st_ino


def _ns(pid, name):
    require(type(pid) is int and 0 < pid < 2**31)
    require(name in ("time", "time_for_children", "user"))
    return f"/proc/{pid}/ns/{name}"


def _offsets(pid):
    require(type(pid) is int and 0 < pid < 2**31)
    with open(f"/proc/{pid}/timens_offsets", "rb", buffering=0) as stream:
        raw = stream.read(MAX_BYTES + 1)
    zero_offsets(raw)
    return raw


@dataclass(frozen=True)
class Evidence:
    init: process.ProcessIdentity
    host_time: tuple[int, int]
    native_time: tuple[int, int]
    user: tuple[int, int]
    original_clock: clock.Window

    @property
    def sha256(self):
        return checksum({"kind": "finite-recording-zero-time-domains", **asdict(self)})


class ZeroDomain:
    """Retained current-helper/native-init equivalence, zero offsets only.

    original must have been captured in this continuing helper's namespace.
    Neither an unrelated driver sample nor a replaced namespace can be adopted.
    /proc/PID/timens_offsets describes time_for_children: require that namespace
    to equal time, before AND after reading. With a live member the kernel has
    frozen its offsets; retained descriptors prevent namespace inode recycling.

    Source, proc-mount provenance, Engine identity, native readiness, user consent
    and fixed original deadlines remain separate checks. No installed launcher
    or existing clock/namespace verifier automatically accepts this witness.
    """

    def __init__(self, original, witness):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.closed = self.failed = False
        self.handles, self.pidfd = {}, -1
        try:
            require(os.geteuid() == ROOT_UID)
            require(type(original) is clock.Window and type(witness) is process.ProcessWitness)
            require(not witness.exited())
            self.original, self.init = original, witness.identity
            require(self.init.pid != self.owner[0])
            self.pidfd = os.dup(witness.fd)
            original.check_later(clock.read())
            self._live()
            domains = []
            for pid in (self.owner[0], self.init.pid):
                fd = os.open(_ns(pid, "time"), os.O_RDONLY | os.O_CLOEXEC)
                self.handles[pid] = (fd, None)
                identity = _identity(os.fstat(fd))
                self.handles[pid] = (fd, identity)
                require(_identity(os.stat(_ns(pid, "time"))) == identity)
                require(_identity(os.stat(_ns(pid, "time_for_children"))) == identity)
                domains.append(identity)
            require(domains[0] == original.namespace)
            user = _identity(os.stat(_ns(self.owner[0], "user")))
            require(user == _identity(os.stat(_ns(self.init.pid, "user"))))
            self.evidence = Evidence(self.init, domains[0], domains[1], user, original)
            self.refresh()
        except BaseException as error:
            self.failed = True
            self.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedDomain(MESSAGE) from None

    def _live(self):
        require(self.pidfd >= 0)
        poller = select.poll()
        poller.register(self.pidfd, select.POLLIN)
        require(not poller.poll(0))
        require(process.read_identity(self.init.pid, self.init.container_id) == self.init)
        require(not poller.poll(0))

    def _domains(self):
        for pid, (fd, identity) in self.handles.items():
            require(_identity(os.fstat(fd)) == identity)
            require(_identity(os.stat(_ns(pid, "time"))) == identity)
            require(_identity(os.stat(_ns(pid, "time_for_children"))) == identity)
            require(_identity(os.stat(_ns(pid, "user"))) == self.evidence.user)

    def refresh(self):
        acquired = False
        try:
            require(
                not self.failed and not self.closed and self.owner == (os.getpid(), get_ident())
            )
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            before = clock.read()
            self.original.check_later(before)
            self._live()
            self._domains()
            first = tuple(_offsets(pid) for pid in (self.owner[0], self.init.pid))
            self._domains()
            require(first == tuple(_offsets(pid) for pid in (self.owner[0], self.init.pid)))
            self._domains()
            self._live()
            after = clock.read()
            before.check_later(after)
            self.original.check_later(after)
            require(0 <= time.monotonic() - began < 1)
            require(not self.failed and not self.closed)
            return self.evidence
        except BaseException as error:
            self.failed = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedDomain(MESSAGE) from None
        finally:
            if acquired:
                self.lock.release()

    def close(self):
        self.closed = True
        for fd, _ in self.handles.values():
            os.close(fd)
        self.handles.clear()
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1


if __name__ == "__main__":
    raise SystemExit("Read-only clock-domain evidence only; no clock or namespace was changed.")
