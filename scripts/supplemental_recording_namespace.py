#!/usr/bin/env python3
"""Read-only host /proc actor mapping for a separately qualified container.

No PID discovery by name, namespace entry, exec, signal or scanner operation.
Only the existing exact HAOS unified Docker cgroup and one nested PID namespace
are supported. The caller must retain actual live-bound pidfds and independently
check container incarnation and exec/source identity around these observations.
Parsed proc data alone is not that authentication or an exit witness.
"""

from __future__ import annotations

import os
import select
import time
from dataclasses import dataclass
from threading import get_ident

import supplemental_handoff_process as host_process

NAMESPACES = ("pid", "mnt", "net", "user", "time")
MESSAGE = "Finite recording process namespace is unconfirmed; do not dispatch or restore."


class UnconfirmedNamespace(ValueError):
    """Namespace identity and process exit are separate observations."""


def require(value):
    if not value:
        raise UnconfirmedNamespace(MESSAGE)


def _namespace(pair):
    require(type(pair) is tuple and len(pair) == 2)
    require(all(type(value) is int for value in pair) and pair[0] >= 0 and pair[1] > 0)


@dataclass(frozen=True)
class Actor:
    host_pid: int
    local_pid: int
    parent: int
    start_ticks: int
    container_id: str
    namespaces: tuple[tuple[int, int], ...]

    def __post_init__(self):
        for value in (self.host_pid, self.local_pid, self.parent, self.start_ticks):
            require(type(value) is int)
        require(1 < self.host_pid < 2**31 and 0 < self.local_pid < 2**31)
        require(0 <= self.parent < 2**31 and self.start_ticks > 0)
        host_process.digest(self.container_id)
        require(type(self.namespaces) is tuple and len(self.namespaces) == len(NAMESPACES))
        for pair in self.namespaces:
            _namespace(pair)


def decode(pid, container_id, stat, status, cgroup, namespaces):
    """Decode bounded host proc facts. Only root, unmapped two-level PIDs here."""
    try:
        identity = host_process.process_identity(pid, container_id, stat, cgroup)
        fields = stat.rpartition(") ")[2].split()
        require(fields[0] in ("R", "S", "I") and fields[1].isdigit())
        require(type(status) is str and 0 < len(status) <= 16384)
        selected = {}
        for line in status.splitlines():
            key, _, value = line.partition(":")
            if key in ("Uid", "Gid", "NSpid", "Tgid", "Pid", "PPid"):
                require(key not in selected)
                selected[key] = value.split()
        require(set(selected) == {"Uid", "Gid", "NSpid", "Tgid", "Pid", "PPid"})
        require(
            all(value.isascii() and value.isdigit() for row in selected.values() for value in row)
        )
        require(selected["Uid"] == selected["Gid"] == ["0"] * 4)
        require(selected["Tgid"] == selected["Pid"] == [str(pid)])
        require(selected["PPid"] == [fields[1]])
        require(len(selected["NSpid"]) == 2 and selected["NSpid"][0] == str(pid))
        local = int(selected["NSpid"][1])
        require(0 < local < 2**31 and 0 <= int(fields[1]) < 2**31)
        require(type(namespaces) is tuple and len(namespaces) == len(NAMESPACES))
        for pair in namespaces:
            _namespace(pair)
        return Actor(pid, local, int(fields[1]), identity.start_ticks, container_id, namespaces)
    except Exception:
        raise UnconfirmedNamespace(MESSAGE) from None


def read(pid, container_id):
    """Fixed bounded proc paths only. Caller retains pidfd before AND after."""
    try:
        require(type(pid) is int and 1 < pid < 2**31)
        host_process.digest(container_id)
        deadline = time.monotonic() + 1.0

        def raw(name, limit):
            require(time.monotonic() < deadline)
            with open(f"/proc/{pid}/{name}", "rb", buffering=0) as stream:
                value = stream.read(limit + 1)
            require(len(value) <= limit and time.monotonic() < deadline)
            return value.decode("ascii")

        namespaces = []
        for name in NAMESPACES:
            require(time.monotonic() < deadline)
            info = os.stat(f"/proc/{pid}/ns/{name}")
            namespaces.append((info.st_dev, info.st_ino))
        result = decode(
            pid,
            container_id,
            raw("stat", 4096),
            raw("status", 16384),
            raw("cgroup", 4096),
            tuple(namespaces),
        )
        require(time.monotonic() < deadline)
        return result
    except Exception:
        raise UnconfirmedNamespace(MESSAGE) from None


def match(init, guardian, native, watchdog, *, expected_init, reported, host_user, host_time):
    """Check already live-bound observations against an actual operator report.

    The init witness and independently inspected guardian host PID must be
    qualified by the host adapter; this pure mapping does not obtain them.
    Exact init PID1, distinct child IDs, shared namespaces/cgroup and unchanged
    host user/time domains are required. No PID arithmetic or substring match.
    """
    try:
        require(type(expected_init) is host_process.ProcessIdentity)
        _namespace(host_user)
        _namespace(host_time)
        actors = (init, guardian, native, watchdog)
        require(all(type(actor) is Actor for actor in actors))
        require(
            (init.host_pid, init.start_ticks, init.container_id)
            == (expected_init.pid, expected_init.start_ticks, expected_init.container_id)
        )
        require(init.local_pid == 1 and len({actor.host_pid for actor in actors}) == 4)
        require(len({actor.local_pid for actor in actors}) == 4)
        require(all(actor.local_pid > 1 for actor in actors[1:]))
        require(
            all(
                actor.container_id == init.container_id and actor.namespaces == init.namespaces
                for actor in actors
            )
        )
        require(init.namespaces[3:] == (host_user, host_time))
        require(native.parent == watchdog.parent == guardian.host_pid)
        require(guardian.parent not in {actor.host_pid for actor in actors[1:]})
        require(type(reported) is dict and set(reported) == {"guardian", "native", "watchdog"})
        for role, actor in zip(("guardian", "native", "watchdog"), actors[1:], strict=True):
            value = reported[role]
            require(type(value) is dict and set(value) == {"pid", "start_ticks", "uid", "gid"})
            require(all(type(item) is int for item in value.values()))
            require(
                value
                == {"pid": actor.local_pid, "start_ticks": actor.start_ticks, "uid": 0, "gid": 0}
            )
        return actors
    except Exception:
        raise UnconfirmedNamespace(MESSAGE) from None


class Witness:
    """Retain actual pidfds around host/container mapping, without signals.

    init_witness must already be bound by the independent host before handoff;
    guardian_pid comes from its separately qualified exact exec inspection.
    reported is the identity part of the authenticated ready envelope. None of
    those prerequisites is established by accepting caller arguments here.
    A failed refresh prevents further live claims but keeps retained handles
    available for exact exit observation until explicitly closed.
    """

    def __init__(self, init_witness, guardian_pid, reported):
        self.owner = os.getpid(), get_ident()
        self.handles, self.actors = {}, None
        self.closed = self.failed = False
        try:
            require(type(init_witness) is host_process.ProcessWitness and not init_witness.exited())
            require(type(guardian_pid) is int and 1 < guardian_pid < 2**31)
            self.expected_init = init_witness.identity
            require(guardian_pid != self.expected_init.pid)
            require(type(reported) is dict and set(reported) == {"guardian", "native", "watchdog"})
            self.reported = {}
            for role in ("guardian", "native", "watchdog"):
                value = reported[role]
                require(type(value) is dict and set(value) == {"pid", "start_ticks", "uid", "gid"})
                require(all(type(number) is int for number in value.values()))
                self.reported[role] = dict(value)
            require(len({value["pid"] for value in self.reported.values()}) == 3)
            self.handles["init"] = os.dup(init_witness.fd)
            self.handles["guardian"] = os.pidfd_open(guardian_pid)
            deadline = time.monotonic() + 2
            cid = self.expected_init.container_id
            init, guardian = read(self.expected_init.pid, cid), read(guardian_pid, cid)
            require(guardian.local_pid == self.reported["guardian"]["pid"])
            require(guardian.start_ticks == self.reported["guardian"]["start_ticks"])
            with open(
                f"/proc/{guardian_pid}/task/{guardian_pid}/children", "rb", buffering=0
            ) as stream:
                raw = stream.read(4097)
            require(len(raw) <= 4096 and time.monotonic() < deadline)
            children = raw.split()
            require(len(children) == 2 and all(value.isdigit() for value in children))
            children = tuple(int(value) for value in children)
            require(len(set(children)) == 2 and all(1 < pid < 2**31 for pid in children))
            require(not set(children) & {init.host_pid, guardian_pid})
            # Bind both exact host PIDs before opening their proc paths. A
            # reused PID cannot retrospectively become a live exit witness.
            pending = []
            for pid in children:
                key = str(pid)
                self.handles[key] = os.pidfd_open(pid)
                pending.append(read(pid, cid))
            mapped = {item.local_pid: item for item in pending}
            require(len(mapped) == 2)
            native, watchdog = [
                mapped[self.reported[role]["pid"]] for role in ("native", "watchdog")
            ]
            for role, item in (("native", native), ("watchdog", watchdog)):
                self.handles[role] = self.handles.pop(str(item.host_pid))
            self.actors = init, guardian, native, watchdog
            self.host_user, self.host_time = self._host_domains()
            self._match(self.actors)
            require(time.monotonic() < deadline)
            self.refresh()  # Recheck every mapped actor after retaining all handles.
            require(time.monotonic() < deadline)
        except BaseException as error:
            self.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedNamespace(MESSAGE) from None

    @staticmethod
    def _host_domains():
        result = []
        for name in ("user", "time"):
            info = os.stat(f"/proc/self/ns/{name}")
            result.append((info.st_dev, info.st_ino))
        return tuple(result)

    def _match(self, actors):
        return match(
            *actors,
            expected_init=self.expected_init,
            reported=self.reported,
            host_user=self.host_user,
            host_time=self.host_time,
        )

    def refresh(self):
        try:
            require(
                not self.closed and not self.failed and self.owner == (os.getpid(), get_ident())
            )
            deadline = time.monotonic() + 2
            require(not any(self.exited(role) for role in self.handles))
            current = tuple(read(actor.host_pid, actor.container_id) for actor in self.actors)
            require(
                current == self.actors and self._host_domains() == (self.host_user, self.host_time)
            )
            self._match(current)
            require(not any(self.exited(role) for role in self.handles))
            require(time.monotonic() < deadline)
            return current
        except BaseException as error:
            self.failed = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedNamespace(MESSAGE) from None

    def exited(self, role):
        require(not self.closed and self.owner == (os.getpid(), get_ident()))
        require(type(role) is str and role in self.handles)
        poller = select.poll()
        poller.register(self.handles[role], select.POLLIN)
        events = poller.poll(0)
        if not events:
            return False
        require(len(events) == 1 and events[0][0] == self.handles[role])
        require(events[0][1] in (select.POLLIN, select.POLLIN | select.POLLHUP))
        return True

    def close(self):
        if self.closed:
            return
        require(self.owner == (os.getpid(), get_ident()))
        self.closed = True
        failed = False
        for fd in self.handles.values():
            try:
                os.close(fd)
            except OSError:
                failed = True
        self.handles.clear()
        require(not failed)


if __name__ == "__main__":
    raise SystemExit("Read-only namespace mapping only; no host action enabled.")
