#!/usr/bin/env python3
"""Retain one private original-peer bootstrap connection, uninstalled only.

The independently qualified outer owner must already provision the listener and
authenticate the original peer witness and input pins. This module connects;
it never creates, repairs, removes or reconnects a pathname. Socket credentials
are transport evidence, not runtime qualification, input provenance or consent.
No existing command or runtime qualification selects this module's source graph.
"""

from __future__ import annotations

import errno
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

import supplemental_handoff_files as files
import supplemental_handoff_process as processes

NAME, SECONDS, ROOT_UID = "bootstrap.sock", 2.0, 0
MESSAGE = "Recording peer connection is unconfirmed; preserve the case and do not retry."


class UnconfirmedConnection(ValueError):
    """No pathname, raw peer data or underlying exception is exposed."""


def require(value):
    if not value:
        raise UnconfirmedConnection(MESSAGE)


def _identity(fd):
    return files.identity(os.fstat(fd))[:5]


class Connection:
    """Own one connection; borrow the exact independently authenticated peer.

    All directory components are retained without following symlinks. The leaf
    must remain private, unchanged and contain only the original 0600 socket.
    Parent bindings are checked without pinning unrelated sibling timestamps.
    Ancestor trust/installation provenance is the outer owner's responsibility;
    these observations are not protection against another trusted root process.

    The immutable cutoff includes construction, path checks, the single connect
    and subsequent rechecks. Pass this same deadline to the descriptor bootstrap
    and keep this owner alive until its borrowers close. No new clock is made.
    A kernel I/O stall still requires independently enforced outer termination.

    Failure closes only owned original descriptors, preserves the socket path
    and borrowed witness, and permanently invalidates this object. No retry is
    made for a full AF_UNIX backlog or an unconfirmed nonblocking connect.
    """

    def __init__(self, root, peer, *, deadline):
        began = time.monotonic()
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.closed = self.failed = False
        self.preparation_attempted = False
        self.handles, self.directories = [], []
        self.channel = self.original_channel = None
        self.channel_pin = None
        try:
            require(type(self) is Connection and self.owner[2:] == (ROOT_UID, ROOT_UID))
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
            self.peer_pin = peer.identity, peer.fd, _identity(peer.fd)
            self._context()
            self.anchor = self._open("/", files.DIRECTORY)
            parent = self.anchor
            for name in root.parts[1:]:
                self._context()
                child = self._open(name, files.DIRECTORY, dir_fd=parent)
                self.directories.append((parent, name, child, _identity(child)))
                parent = child
            self.path_pins = tuple(self.directories)
            self.handle_pins = tuple(self.handles)
            self.directory = parent
            self.directory_pin = files.identity(os.fstat(parent))
            info = os.stat(NAME, dir_fd=parent, follow_symlinks=False)
            require(
                stat.S_ISSOCK(info.st_mode)
                and stat.S_IMODE(info.st_mode) == 0o600
                and (info.st_uid, info.st_gid) == self.owner[2:]
                and info.st_nlink == 1
            )
            self.socket_pin = files.identity(info)
            self._paths()
            channel = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK)
            try:
                self.channel_pin = (
                    channel.fileno(),
                    _identity(channel.fileno()),
                    fcntl.fcntl(channel.fileno(), fcntl.F_GETFL),
                )
            except BaseException:
                channel.close()
                raise
            self.channel = self.original_channel = channel
            channel.setblocking(False)
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self._paths()
            # Never resolve the original string again after capturing the leaf.
            result = channel.connect_ex(f"/proc/self/fd/{parent}/{NAME}")
            require(result in (0, errno.EINPROGRESS))
            if result:
                poller = select.poll()
                poller.register(channel.fileno(), select.POLLOUT | select.POLLERR | select.POLLHUP)
                poller.register(peer.fd, select.POLLIN | select.POLLERR | select.POLLHUP)
                self._paths()
                events = poller.poll(max(0, int((self.deadline - time.monotonic()) * 1000)))
                require(events == [(channel.fileno(), select.POLLOUT)])
                require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0)
            self.recheck()
        except BaseException as error:
            self._fail(error)

    def _open(self, *args, **kwargs):
        fd = os.open(*args, **kwargs)
        try:
            pin = _identity(fd)
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except BaseException:
            os.close(fd)
            raise
        self.handles.append((fd, pin, flags))
        return fd

    def _context(self):
        require(type(self) is Connection and not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        root, peer, deadline = self.inputs
        require(self.root is root and self.peer is peer and self.deadline == deadline)
        require(time.monotonic() < deadline)
        identity, fd, pin = self.peer_pin
        require(peer.identity is identity and peer.fd == fd and _identity(fd) == pin)
        require(not os.get_inheritable(fd))
        processes.ProcessWitness._live_descriptor(fd, identity.pid)
        require(not peer.exited())
        require(processes.read_identity(identity.pid, identity.container_id) == identity)
        require(time.monotonic() < deadline)

    def _paths(self):
        self._context()
        require(tuple(self.directories) == self.path_pins)
        require(tuple(self.handles) == self.handle_pins)
        for fd, pin, flags in self.handles:
            require(not os.get_inheritable(fd) and _identity(fd) == pin)
            require(fcntl.fcntl(fd, fcntl.F_GETFL) == flags)
        for parent, name, child, pin in self.path_pins:
            require(_identity(child) == pin)
            require(files.identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:5] == pin)
        info = os.fstat(self.directory)
        require(files.identity(info) == self.directory_pin)
        require(
            stat.S_IMODE(info.st_mode) == 0o700 and (info.st_uid, info.st_gid) == self.owner[2:]
        )
        with os.scandir(self.directory) as entries:
            entry = next(entries, None)
            require(entry is not None and entry.name == NAME and next(entries, None) is None)
        require(
            files.identity(os.stat(NAME, dir_fd=self.directory, follow_symlinks=False))
            == self.socket_pin
        )
        self._context()

    def recheck(self):
        """Recheck retained paths, original peer and connection; return no grant."""
        try:
            self._paths()
            channel = self.original_channel
            require(self.channel is channel and type(channel) is socket.socket)
            fd, pin, flags = self.channel_pin
            require(channel.fileno() == fd and _identity(fd) == pin)
            require(not channel.get_inheritable() and channel.gettimeout() == 0.0)
            require(fcntl.fcntl(fd, fcntl.F_GETFL) == flags and flags & os.O_NONBLOCK)
            require(channel.family == socket.AF_UNIX)
            require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_SEQPACKET)
            require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0)
            require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1)
            channel.getpeername()
            credentials = struct.Struct("3i")
            require(
                credentials.unpack(
                    channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, credentials.size)
                )
                == (self.peer_pin[0].pid, *self.owner[2:])
            )
            # POLLIN may be a legitimate queued bootstrap message; do not peek,
            # consume it or its SCM_RIGHTS. A closed/error peer is never accepted.
            poller = select.poll()
            poller.register(fd, select.POLLERR | select.POLLHUP | select.POLLRDHUP)
            require(not poller.poll(0))
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
        raise UnconfirmedConnection(MESSAGE) from None

    def close(self):
        """Retire original owned handles once; never unlink or close a reused fd."""
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        problem = None
        channel, self.original_channel = self.original_channel, None
        self.channel = None
        if channel is not None:
            try:
                if channel.fileno() != -1:
                    fd, pin, _ = self.channel_pin
                    require(channel.fileno() == fd and _identity(fd) == pin)
                    channel.close()
            except BaseException as error:
                channel.detach()
                problem = error
        while self.handles:
            fd, pin, _ = self.handles.pop()
            try:
                # Changed permissions invalidate evidence, not ownership of the
                # still-original directory inode. A foreign reused fd must not
                # be closed, while chmod/chown must not leak our own handles.
                current = _identity(fd)
                require(current[:2] == pin[:2] and stat.S_IFMT(current[2]) == stat.S_IFMT(pin[2]))
                os.close(fd)
            except BaseException as error:
                if problem is None or not isinstance(error, Exception):
                    problem = error
        if problem is not None:
            if not isinstance(problem, Exception):
                raise problem
            raise UnconfirmedConnection(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled private connection only; no active launch enabled.")
