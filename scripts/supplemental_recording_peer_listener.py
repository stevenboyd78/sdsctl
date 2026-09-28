#!/usr/bin/env python3
"""One private, original-peer listener; uninstalled, never an action grant.

The outer owner separately authenticates its installation, empty private leaf,
original peer witness and inputs. This library binds one fixed socket name and
accepts once. It never creates directories, unlinks evidence, retries, discovers
replacement processes or expands an existing command/source profile.
"""

from __future__ import annotations

import fcntl
import math
import os
import select
import socket
import stat
import struct
import time
from pathlib import Path
from threading import get_ident

import supplemental_recording_peer_connection as connection

files, processes = connection.files, connection.processes
NAME, SECONDS, ROOT_UID = connection.NAME, connection.SECONDS, 0
MESSAGE = "Recording peer listener is unconfirmed; preserve the case and do not retry."


class UnconfirmedListener(ValueError):
    """Failure text contains no private pathname, peer data or exception details."""


def require(value):
    if not value:
        raise UnconfirmedListener(MESSAGE)


class Listener:
    """Own one fixed listener and accepted socket; borrow the original witness.

    An independently provisioned leaf must already exist, mode0700 and empty.
    Every ancestor is retained without following symlinks. The only new entry
    is bootstrap.sock, set to0600 through its retained O_PATH descriptor before
    listen, without changing the process umask. No pre-existing entry is adopted,
    changed or removed. Even after failure/close, the created pathname remains.

    Construction, accept and all rechecks share one original two-second-or-earlier
    cutoff. Pass that same cutoff to the subsequent Endpoint handoff and keep
    this owner until all socket borrowers finish. Kernel stalls still require
    independent outer/platform termination. Root pathname/credential evidence
    is NOT independent installation provenance, Ready, consent or App authority.
    This is not protection against another trusted root process modifying the
    private installation or against arbitrary code inside the owner itself.
    """

    def __init__(self, root, peer, *, deadline):
        began = time.monotonic()
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.closed = self.failed = self.attempted = self.accepted = False
        self.handles, self.directories, self.sockets = [], [], []
        self.listener = self.channel = None
        self.node = None
        try:
            require(type(self) is Listener and self.owner[2:] == (ROOT_UID, ROOT_UID))
            require(type(root) is type(Path()) and root.is_absolute() and root != Path("/"))
            require(root.anchor == "/" and ".." not in root.parts and len(root.parts) <= 32)
            require(len(os.fsencode(root)) <= 4096 and all(ord(c) >= 32 for c in str(root)))
            require(type(deadline) in (int, float) and math.isfinite(deadline))
            require(type(peer) is processes.ProcessWitness)
            require(type(peer.identity) is processes.ProcessIdentity)
            peer.identity.__post_init__()
            require(peer.identity.pid != self.owner[0])
            self.root, self.peer = root, peer
            self.deadline = min(began + SECONDS, deadline)
            self.inputs = root, peer, self.deadline
            self.peer_pin = peer.identity, peer.fd, connection._identity(peer.fd)
            self._context()
            parent = self._open("/", files.DIRECTORY)
            for name in root.parts[1:]:
                self._context()
                child = self._open(name, files.DIRECTORY, dir_fd=parent)
                self.directories.append((parent, name, child, connection._identity(child)))
                parent = child
            self.path_pins = tuple(self.directories)
            self.directory = parent
            self.directory_pin = files.identity(os.fstat(parent))
            self._paths(empty=True)
            self.listener = self._own(
                socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK)
            )
            self._paths(empty=True)
            self.listener.bind(f"/proc/self/fd/{parent}/{NAME}")
            # Pin the newly created inode BEFORE chmod. Never chmod through a
            # replaceable pathname or temporarily change the process-wide umask.
            self.node = self._open(NAME, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            info = os.fstat(self.node)
            require(
                stat.S_ISSOCK(info.st_mode)
                and (info.st_uid, info.st_gid) == self.owner[2:]
                and info.st_nlink == 1
            )
            require(
                files.identity(os.stat(NAME, dir_fd=parent, follow_symlinks=False))
                == files.identity(info)
            )
            self._context()
            os.chmod(f"/proc/self/fd/{self.node}", 0o600)
            info = os.fstat(self.node)
            require(stat.S_IMODE(info.st_mode) == 0o600)
            self.handles[-1] = self.node, connection._identity(self.node), self.handles[-1][2]
            self.node_pin = files.identity(info)
            # Binding is the only allowed directory change. The original leaf
            # inode/owner/mode must remain, and the final entry set is exact.
            updated = files.identity(os.fstat(parent))
            require(updated[:6] == self.directory_pin[:6])
            self.directory_pin = updated
            self._paths()
            self.handle_pins = tuple(self.handles)
            self.listener.listen(1)
            self.recheck()
        except BaseException as error:
            self._fail(error)

    def _open(self, *args, **kwargs):
        fd = os.open(*args, **kwargs)
        try:
            pin = connection._identity(fd)
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except BaseException:
            os.close(fd)
            raise
        self.handles.append((fd, pin, flags))
        return fd

    def _own(self, channel):
        try:
            # Configure only our newly created/accepted socket, then retain its
            # complete original status. Rechecks never repair changed flags.
            channel.setblocking(False)
            channel.set_inheritable(False)
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            pin = (
                channel,
                channel.fileno(),
                connection._identity(channel.fileno()),
                fcntl.fcntl(channel.fileno(), fcntl.F_GETFL),
            )
        except BaseException:
            channel.close()
            raise
        self.sockets.append(pin)
        return channel

    def _context(self):
        require(type(self) is Listener and not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        root, peer, deadline = self.inputs
        require(self.root is root and self.peer is peer and self.deadline == deadline)
        require(time.monotonic() < deadline)
        identity, fd, pin = self.peer_pin
        require(peer.identity is identity and peer.fd == fd and connection._identity(fd) == pin)
        require(not os.get_inheritable(fd))
        processes.ProcessWitness._live_descriptor(fd, identity.pid)
        require(not peer.exited())
        require(processes.read_identity(identity.pid, identity.container_id) == identity)
        require(time.monotonic() < deadline)

    def _paths(self, *, empty=False):
        self._context()
        require(tuple(self.directories) == self.path_pins)
        for fd, pin, flags in self.handles:
            require(not os.get_inheritable(fd) and connection._identity(fd) == pin)
            require(fcntl.fcntl(fd, fcntl.F_GETFL) == flags)
        for parent, name, child, pin in self.path_pins:
            require(files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:5] == pin)
            require(connection._identity(child) == pin)
        info = os.fstat(self.directory)
        require(files.identity(info) == self.directory_pin)
        require(
            stat.S_IMODE(info.st_mode) == 0o700 and (info.st_uid, info.st_gid) == self.owner[2:]
        )
        with os.scandir(self.directory) as entries:
            entry = next(entries, None)
            if empty:
                require(entry is None)
            else:
                require(entry is not None and entry.name == NAME and next(entries, None) is None)
        if not empty:
            require(files.identity(os.fstat(self.node)) == self.node_pin)
            require(
                files.identity(os.stat(NAME, dir_fd=self.directory, follow_symlinks=False))
                == self.node_pin
            )
        self._context()

    def _socket(self, index, channel, *, listening):
        original, fd, pin, flags = self.sockets[index]
        require(channel is original and type(channel) is socket.socket)
        require(channel.fileno() == fd and connection._identity(fd) == pin)
        require(not channel.get_inheritable() and channel.gettimeout() == 0.0)
        require(fcntl.fcntl(fd, fcntl.F_GETFL) == flags and flags & os.O_NONBLOCK)
        require(channel.family == socket.AF_UNIX)
        require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_SEQPACKET)
        require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == int(listening))
        require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1)

    def recheck(self):
        """Retain the same original paths/sockets/peer; returns no authority."""
        try:
            require(tuple(self.handles) == self.handle_pins)
            self._paths()
            if self.accepted:
                require(self.attempted and len(self.sockets) == 2)
                require(self.listener is self.sockets[0][0] and self.listener.fileno() == -1)
                self._socket(1, self.channel, listening=False)
                self.channel.getpeername()
                credentials = struct.Struct("3i")
                require(
                    credentials.unpack(
                        self.channel.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, credentials.size
                        )
                    )
                    == (self.peer_pin[0].pid, *self.owner[2:])
                )
                poller = select.poll()
                poller.register(
                    self.channel.fileno(), select.POLLERR | select.POLLHUP | select.POLLRDHUP
                )
                require(not poller.poll(0))
            else:
                require(len(self.sockets) == 1 and self.channel is None)
                self._socket(0, self.listener, listening=True)
            self._paths()
        except BaseException as error:
            self._fail(error)

    def accept(self):
        """One finite attempt; accepted socket stays owned here, never transferred."""
        try:
            self._context()
            require(self.attempted is False)
            self.attempted = True
            self.recheck()
            poller = select.poll()
            fd = self.listener.fileno()
            poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
            poller.register(self.peer.fd, select.POLLIN | select.POLLERR | select.POLLHUP)
            events = poller.poll(max(0, int((self.deadline - time.monotonic()) * 1000)))
            self.recheck()
            require(events == [(fd, select.POLLIN)])
            channel, _ = self.listener.accept()
            self.channel = self._own(channel)
            self._socket(0, self.listener, listening=True)
            self.listener.close()  # No further backlog admission, even on success.
            self.accepted = True
            self.recheck()
            return self.channel
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
        raise UnconfirmedListener(MESSAGE) from None

    def close(self):
        """Close owned originals once; keep all paths and the borrowed witness."""
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        problem = None
        while self.sockets:
            channel, fd, pin, _ = self.sockets.pop()
            try:
                if channel.fileno() != -1:
                    require(channel.fileno() == fd and connection._identity(fd) == pin)
                    channel.close()
            except BaseException as error:
                channel.detach()
                if problem is None or not isinstance(error, Exception):
                    problem = error
        while self.handles:
            fd, pin, _ = self.handles.pop()
            try:
                current = connection._identity(fd)
                require(current[:2] == pin[:2] and stat.S_IFMT(current[2]) == stat.S_IFMT(pin[2]))
                os.close(fd)
            except BaseException as error:
                if problem is None or not isinstance(error, Exception):
                    problem = error
        if problem is not None:
            if not isinstance(problem, Exception):
                raise problem
            raise UnconfirmedListener(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled private listener only; no active launch enabled.")
