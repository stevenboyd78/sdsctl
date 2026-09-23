#!/usr/bin/env python3
"""Read-only live idle-init / original lease join; not daemon readiness.

The adapter must separately authenticate Engine generation, image, source,
interpreter, environment, mounts and all protected files around this observation.
This collector reads only the fixed host-mapped idle directory and exact retained
init process. It cannot start an operator, renew a lease, infer recording idle,
prove ownership of the scanner, or restore another App.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import stat
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from threading import Lock, get_ident

import supplemental_handoff_files as files
import supplemental_handoff_process as process
import supplemental_recording_host_plan as host_plan
import supplemental_recording_namespace as namespace

MESSAGE = "Recording idle-init evidence is unconfirmed; preserve the case and do not launch."
ROOT_UID = ROOT_GID = 0
MAX_BYTES = 4096
MAX_SECONDS = 2


class UnconfirmedIdle(ValueError):
    """Failure never becomes a positive native-health or process-exit result."""


def require(value):
    if not value:
        raise UnconfirmedIdle(MESSAGE)


@dataclass(frozen=True)
class Evidence:
    plan_sha256: str
    generation: str
    init: process.ProcessIdentity
    actor_sha256: str
    files_sha256: str
    lease_sha256: str
    claim_sha256: str
    started_at: float
    sampled_at: float

    @property
    def sha256(self):
        return host_plan.base.checksum({"kind": "finite-recording-idle-evidence", **asdict(self)})

    @property
    def native(self):
        # Claim publication and live Python PID1 are not a healthy daemon or
        # evidence that recording is inactive. Keep both facts unconfirmed.
        return host_plan.ordinary.NativeState(self.generation, None, None)


def _claim(raw, plan, now, start_ticks):
    """Decode bytes from the bounded actual file read, never a public report."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)

    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value)
            value[key] = item
        return value

    def constant(_):
        require(False)

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    require(
        type(value) is dict
        and set(value) == {"schema", "kind", "case", "lease_sha256", "started_at"}
    )
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(value["kind"] == "finite-recording-idle-claim")
    require(value["case"] == plan.case and value["lease_sha256"] == plan.lease_sha256)
    require(host_plan.base.encode(value) == raw)
    started = value["started_at"]
    host_plan.base.clock(started)
    require(plan.lease["issued_at"] <= started <= now < plan.lease["ready_by"])
    ticks = os.sysconf("SC_CLK_TCK")
    require(type(ticks) is int and ticks > 0)
    # Linux /proc starttime uses BOOTTIME, quantized to clock ticks (Linux
    # fs/proc/array.c, do_task_stat). The lease uses MONOTONIC. Compare the
    # tick's lower bound to the claim's maximum possible BOOTTIME using the
    # ORIGINAL offset interval. This bounds stale claims to kernel resolution;
    # the live pidfd/source/claim continuity checks remain independently required.
    claim_boot_ns = Decimal(started) * host_plan.clock.NS + plan.original_clock.offset[1]
    require(Decimal(start_ticks) * host_plan.clock.NS <= claim_boot_ns * ticks)
    return started


class Idle:
    """Retain original files and a duplicate of the already bound live pidfd.

    Construction and refresh perform actual proc/clock/filesystem reads. Parsed
    container metadata is deliberately not accepted as authentication here.
    The generation is an externally authenticated pin; the adapter must recheck
    it around use. Failed refresh poisons this collector and keeps the pidfd for
    its owner to close; neither a retry nor another current baseline is adopted.
    close() closes only this object's descriptors, never the caller's witness.
    """

    def __init__(self, plan, witness, generation):
        self.opened, self.pidfd = [], -1
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = False
        self.original_files = self.actor = None
        try:
            require(type(plan) is host_plan.Plan)
            require(host_plan.load_bytes(plan.raw, plan.sha256) == plan)
            host_plan.base.digest(generation)
            require(type(witness) is process.ProcessWitness and not witness.exited())
            require(
                process.read_identity(witness.identity.pid, witness.identity.container_id)
                == witness.identity
            )
            self.plan, self.init, self.generation = plan, witness.identity, generation
            self.pidfd = os.dup(witness.fd)
            layout = next(item for item in plan.layouts if item.slug == host_plan.base.CANDIDATE)
            self.directory = layout.data / plan.native_root.relative_to("/data") / "idle"
            self._open()
            self.initial = self.read()
        except BaseException as error:
            self.failed = True
            self.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedIdle(MESSAGE) from None

    def _open(self):
        root = os.open("/", files.DIRECTORY)
        self.opened.append((None, None, root, None))
        info = os.fstat(root)
        require(info.st_uid == ROOT_UID and info.st_gid == ROOT_GID and info.st_mode & 0o7022 == 0)
        self.opened[-1] = (None, None, root, files.identity(info)[:6])
        parent = root
        for name in self.directory.parts[1:]:
            fd = os.open(name, files.DIRECTORY, dir_fd=parent)
            self.opened.append((parent, name, fd, None))
            info = os.fstat(fd)
            require(info.st_uid == ROOT_UID and info.st_gid == ROOT_GID)
            require(info.st_mode & 0o7022 == 0)
            self.opened[-1] = (parent, name, fd, files.identity(info)[:6])
            parent = fd
        require(stat.S_IMODE(os.fstat(parent).st_mode) == 0o700)

    def _file(self, directory, name):
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
        require(before.st_uid == ROOT_UID and before.st_gid == ROOT_GID)
        require(stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= MAX_BYTES)
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory
        )
        try:
            identity = files.identity(before)
            require(files.identity(os.fstat(fd)) == identity)
            raw = os.read(fd, MAX_BYTES + 1)
            require(len(raw) == before.st_size and not os.read(fd, 1))
            require(files.identity(os.fstat(fd)) == identity)
            require(
                files.identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == identity
            )
            return raw, identity
        finally:
            os.close(fd)

    def _files(self):
        directory = self.opened[-1][2]
        before = files.identity(os.fstat(directory))
        # Bound enumeration before materializing names; never accept an extra
        # recovery/renewal file, directory, socket or private side channel here.
        names = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                require(entry.name in {"lease.json", "consumed.json"} and entry.name not in names)
                names.add(entry.name)
        require(names == {"lease.json", "consumed.json"})
        result = {name: self._file(directory, name) for name in sorted(names)}
        require(files.identity(os.fstat(directory)) == before)
        for parent, name, fd, original in self.opened:
            require(files.identity(os.fstat(fd))[:6] == original)
            if parent is not None:
                require(
                    files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:6]
                    == original
                )
        return before, result

    def _process(self):
        poller = select.poll()
        poller.register(self.pidfd, select.POLLIN)
        require(not poller.poll(0))
        require(process.read_identity(self.init.pid, self.init.container_id) == self.init)
        actor = namespace.read(self.init.pid, self.init.container_id)
        require(type(actor) is namespace.Actor and actor.local_pid == 1)
        require(
            (actor.host_pid, actor.start_ticks, actor.container_id)
            == (self.init.pid, self.init.start_ticks, self.init.container_id)
        )
        domain = os.stat("/proc/self/ns/user")
        require(
            actor.namespaces[3:]
            == ((domain.st_dev, domain.st_ino), self.plan.original_clock.namespace)
        )
        with open(f"/proc/{self.init.pid}/cmdline", "rb", buffering=0) as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(raw == b"\0".join(part.encode("ascii") for part in self.plan.idle_argv) + b"\0")
        require(not poller.poll(0))
        return actor

    def read(self):
        """Fresh original claim/process continuity; no cached positive result."""
        acquired = False
        try:
            require(
                not self.closed and not self.failed and self.owner == (os.getpid(), get_ident())
            )
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            before = host_plan.clock.read()
            self.plan.check_clock(before)
            require(before.boottime_ns / host_plan.clock.NS < self.plan.deadlines.ready_by)
            actor = self._process()
            observed = self._files()
            lease, claim = (observed[1][name][0] for name in ("lease.json", "consumed.json"))
            require(lease == host_plan.base.encode(self.plan.lease))
            started = _claim(
                claim, self.plan, before.after_ns / host_plan.clock.NS, self.init.start_ticks
            )
            require(self._process() == actor and self._files() == observed)
            require(self._process() == actor)
            after = host_plan.clock.read()
            before.check_later(after)
            self.plan.check_clock(after)
            require(after.boottime_ns / host_plan.clock.NS < self.plan.deadlines.ready_by)
            require(time.monotonic() < self.plan.lease["ready_by"])
            require(0 <= time.monotonic() - began < MAX_SECONDS)
            require(not self.failed and not self.closed)
            if self.actor is None:
                self.actor, self.original_files = actor, observed
            require(actor == self.actor and observed == self.original_files)
            return Evidence(
                self.plan.sha256,
                self.generation,
                self.init,
                host_plan.base.checksum(asdict(actor)),
                host_plan.base.checksum(
                    {
                        "directory": observed[0],
                        "files": {
                            name: {"identity": identity, "sha256": hashlib.sha256(raw).hexdigest()}
                            for name, (raw, identity) in observed[1].items()
                        },
                    }
                ),
                self.plan.lease_sha256,
                hashlib.sha256(claim).hexdigest(),
                started,
                after.boottime_ns / host_plan.clock.NS,
            )
        except BaseException as error:
            self.failed = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedIdle(MESSAGE) from None
        finally:
            if acquired:
                self.lock.release()

    def close(self):
        self.closed = True
        for _, _, fd, _ in reversed(self.opened):
            os.close(fd)
        self.opened.clear()
        if self.pidfd >= 0:
            os.close(self.pidfd)
            self.pidfd = -1


if __name__ == "__main__":
    raise SystemExit(
        "Read-only idle-init observation only; no native operator or App was launched."
    )
