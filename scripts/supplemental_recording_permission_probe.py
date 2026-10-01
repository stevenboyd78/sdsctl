#!/usr/bin/env python3
"""Distinct finite permission-protocol observation, NEVER a service command.

Reads only a pinned declaration and original process/clock/socket evidence.
Even after permission, no baseline, host cache, plan, journal, Engine, scanner,
recording or App operation is selected. Exit75 is not acceptance or readiness.
Both original peers and channel setup need independent trusted qualification.
"""

from __future__ import annotations

import errno
import os
import re
import select
import socket
import stat
import sys
import time
from pathlib import Path
from threading import get_ident

MESSAGE = "Finite recording permission observation ended; preserve this case and do not retry."
ENTRYPOINT = "/opt/sdsctl-recording-host/supplemental_recording_permission_probe.py"

if __name__ == "__main__":
    try:
        allowed = (
            len(sys.argv) == 6
            and sys.argv[5] == "--permission-probe"
            and sys.flags.isolated == sys.flags.dont_write_bytecode == 1
            and os.geteuid() == os.getegid() == 0
            and os.getcwd() == "/"
            and Path(__file__) == Path(ENTRYPOINT)
        )
    except Exception:
        allowed = False
    if not allowed:
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(64)
    sys.path.insert(0, "/opt/sdsctl-recording-host")

try:
    import supplemental_recording_service_declaration as declaration
    import supplemental_recording_service_permission as permission
except Exception:
    if __name__ == "__main__":
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise

require = permission.require
process, clock, files = permission.domains.process, permission.clock, declaration.files
NAME = "observer.sock"


def peer_root(case):
    permission.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-peer-" + case)


def identity_argument(identity):
    require(type(identity) is process.ProcessIdentity)
    return f"{identity.pid}:{identity.start_ticks}:{identity.container_id}"


def parse_identity(value):
    require(type(value) is str and len(value) <= 110)
    require(re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*:[0-9a-f]{64}", value) is not None)
    pid, ticks, container = value.split(":")
    return process.ProcessIdentity(int(pid), int(ticks), container)


def current_identity():
    # No Engine discovery, namespace path normalization or saved PID adoption.
    try:
        with open("/proc/self/cgroup", "rb", buffering=0) as stream:
            raw = stream.read(4097)
        match = re.fullmatch(rb"0::/system.slice/docker-([0-9a-f]{64})\.scope\n", raw)
        require(match is not None)
        return process.read_identity(os.getpid(), match[1].decode("ascii"))
    except Exception:
        raise permission.UnconfirmedPermission(permission.MESSAGE) from None


class PeerConnection:
    """Own one bounded connect through retained directories; never reconnect.

    The socket must already be provisioned by the trusted original observer in
    its own private directory. The target neither creates nor removes that path.
    Directory/name and original channel checks continue after connection; peer
    credentials and live pidfd/domain checks are the Permission owner's job.
    A blocked kernel operation still needs the independent outer supervisor.
    """

    def __init__(self, root, deadline):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.handles, self.channel = [], None
        self.closed = self.failed = False
        try:
            require(type(root) is type(Path()) and root.is_absolute() and root != Path("/"))
            require(".." not in root.parts and 1 < len(root.parts) <= 32)
            require(not any(ord(c) < 32 for c in str(root)))
            require(type(deadline) is float)
            require(time.monotonic() < deadline <= time.monotonic() + permission.WAIT_SECONDS)
            self.root, self.deadline = root, deadline
            self.inputs = root, deadline
            self.anchor = self._open("/", files.DIRECTORY)
            self.anchor_id = files.identity(os.fstat(self.anchor))[:5]
            parent, self.directories = self.anchor, []
            for name in root.parts[1:]:
                self._context()
                child = self._open(name, files.DIRECTORY, dir_fd=parent)
                self.directories.append((parent, name, child, files.identity(os.fstat(child))[:5]))
                parent = child
            self.directory = parent
            self.directory_id = files.identity(os.fstat(parent))
            info = os.stat(NAME, dir_fd=parent, follow_symlinks=False)
            require(
                stat.S_ISSOCK(info.st_mode)
                and stat.S_IMODE(info.st_mode) == 0o600
                and (info.st_uid, info.st_gid) == self.owner[2:]
                and info.st_nlink == 1
            )
            self.socket_id = files.identity(info)
            self._paths()
            self.channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK)
            self.original_channel = self.channel
            self.fd = self.channel.fileno()
            self.fd_id = permission._Peer._fd_identity(self.fd)
            self.channel.setblocking(False)
            self._paths()
            # Resolve the leaf through the original directory handle, not the
            # supplied pathname after a possible ancestor rename/replacement.
            result = self.channel.connect_ex(f"/proc/self/fd/{parent}/{NAME}")
            require(result in (0, errno.EINPROGRESS))
            # AF_UNIX EAGAIN can mean a full backlog with NO pending connect;
            # reject it, rather than retrying a logical connection attempt.
            end = min(deadline, time.monotonic() + permission.IO_SECONDS)
            if result:
                poller = select.poll()
                poller.register(self.fd, select.POLLOUT | select.POLLERR | select.POLLHUP)
                while True:
                    self._paths()
                    require(time.monotonic() < end)
                    if poller.poll(max(1, min(50, int((end - time.monotonic()) * 1000)))):
                        require(self.channel.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0)
                        break
            require(time.monotonic() < end)
            self.recheck()
        except BaseException as error:
            self._fail(error)

    def _open(self, *args, **kwargs):
        fd = os.open(*args, **kwargs)
        self.handles.append(fd)
        return fd

    def _context(self):
        require(not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require((self.root, self.deadline) == self.inputs and time.monotonic() < self.deadline)

    def _paths(self):
        self._context()
        require(files.identity(os.fstat(self.anchor))[:5] == self.anchor_id)
        for parent, name, child, identity in self.directories:
            require(files.identity(os.fstat(child))[:5] == identity)
            require(
                files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:5] == identity
            )
        info = os.fstat(self.directory)
        require(files.identity(info) == self.directory_id)
        require(
            stat.S_IMODE(info.st_mode) == 0o700 and (info.st_uid, info.st_gid) == self.owner[2:]
        )
        with os.scandir(self.directory) as entries:
            entry = next(entries, None)
            require(entry is not None and entry.name == NAME and next(entries, None) is None)
        require(
            files.identity(os.stat(NAME, dir_fd=self.directory, follow_symlinks=False))
            == self.socket_id
        )
        for fd in self.handles:
            require(not os.get_inheritable(fd))
        self._context()

    def recheck(self):
        try:
            self._paths()
            require(self.channel is self.original_channel and self.channel.fileno() == self.fd)
            require(permission._Peer._fd_identity(self.fd) == self.fd_id)
            require(self.channel.gettimeout() == 0.0 and not os.get_inheritable(self.fd))
            require(permission.fcntl.fcntl(self.fd, permission.fcntl.F_GETFL) & os.O_NONBLOCK)
            self.channel.getpeername()
            self._paths()
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        try:
            self.close()
        except BaseException as cleanup:
            if isinstance(error, Exception) and not isinstance(cleanup, Exception):
                raise cleanup
        if not isinstance(error, Exception):
            raise error
        raise permission.UnconfirmedPermission(permission.MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        callbacks = [lambda fd=fd: os.close(fd) for fd in self.handles]
        self.handles = []
        if self.channel is not None:
            channel, self.channel = self.channel, None
            callbacks.append(channel.close)
        _cleanup(callbacks)


def _cleanup(callbacks, problem=None):
    # Pop before calling; an uncertain close is never retried on a recycled fd.
    while callbacks:
        callback = callbacks.pop()
        try:
            callback()
        except BaseException as error:
            if (
                problem is None
                or isinstance(problem, Exception)
                and not isinstance(error, Exception)
            ):
                problem = error
    if problem is not None:
        if not isinstance(problem, Exception):
            raise problem
        raise permission.UnconfirmedPermission(permission.MESSAGE) from None


def _retain_to_cutoff(original, connection, receiver, end):
    # Stop starting bounded observations in the final I/O budget. Keep every
    # original handle owned during that passive tail, but claim no fresh read,
    # permission or service authority from sleeping to the original cutoff.
    # A read that was actually started still must complete its original guard;
    # expiry/refusal inside it is never swallowed as a normal finish.
    for _ in range(151):
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        if remaining > permission.IO_SECONDS:
            original.recheck()
            connection.recheck()
            receiver._check(end)
            receiver._quiet()
            receiver._binding(end)
        remaining = end - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.1, remaining))
    require(False)  # A stalled local clock cannot create an unbounded loop.


def permission_probe(root, template_sha256, baseline_sha256, observer_identity):
    """Wait once and consume an EMPTY scope, then retain originals until cutoff.

    No service origin/claim/plan is produced. The supplied baseline is a digest
    only: this command never opens it or selects prepare_service(). A generic
    milestone reports local protocol consumption, NOT qualification/readiness.
    Independent outer supervision is mandatory for blocked kernel I/O.
    """
    cleanup, problem = [], None
    try:
        require(type(observer_identity) is process.ProcessIdentity)
        permission.plans.base.digest(baseline_sha256)
        original = declaration.Declaration(root, template_sha256)
        cleanup.append(original.close)
        template = original.recheck()
        target = current_identity()
        require(target.pid != observer_identity.pid)
        timer = clock.ClockWitness(clock.read())
        cleanup.append(timer.close)
        witness = process.ProcessWitness(observer_identity)
        cleanup.append(witness.close)
        domain = permission.domains.ZeroDomain(timer.original, witness)
        cleanup.append(domain.close)
        end = timer.original.after_ns / clock.NS + permission.WAIT_SECONDS - permission.IO_SECONDS
        case = declaration.codec._read(template.raw)["plan"]["case"]
        connection = PeerConnection(peer_root(case), end)
        cleanup.append(connection.close)
        receiver = permission.Permission(
            template,
            template_sha256,
            baseline_sha256,
            target,
            witness,
            domain,
            timer,
            connection.channel,
        )
        cleanup.append(receiver.close)
        original.recheck()
        connection.recheck()
        receiver.wait()
        with receiver.consume() as guard:
            original.recheck()
            connection.recheck()
            guard()
        # Written only AFTER the single scope's final guard. A lost stdout
        # write remains ambiguous. Never derive a sender acknowledgment from it.
        print("Permission probe consumed its empty scope; no service action selected.", flush=True)
        _retain_to_cutoff(original, connection, receiver, end)
    except BaseException as error:
        problem = error
    finally:
        _cleanup(cleanup, problem)
    return 75


if __name__ == "__main__":
    try:
        root = Path(sys.argv[1])
        require(str(root) == sys.argv[1] and root.is_absolute() and ".." not in root.parts)
        code = permission_probe(root, sys.argv[2], sys.argv[3], parse_identity(sys.argv[4]))
    except Exception:
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise SystemExit(code)
