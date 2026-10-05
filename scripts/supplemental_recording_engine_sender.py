#!/usr/bin/env python3
"""Explicit kernel-sender witness for a qualified Unix Engine connection.

A socket-activated listener's SO_PEERCRED may identify its creator (PID1), not
the Engine writing replies. Never treat creator lifetime as Engine lifetime.
This read-only component first sends a fixed ping and binds SCM_CREDENTIALS to
an actual retained sender pidfd. Every subsequent read must come from that same
live sender. The caller must separately qualify the fixed root-owned socket,
image/runtime and operation. Only an explicit Endpoint profile selects this;
no installed plan enables it.
"""

from __future__ import annotations

import array
import os
import select
import socket
import struct
import time
from contextlib import suppress
from threading import get_ident

ROOT_UID = ROOT_GID = 0
CREDENTIALS = struct.Struct("3i")
MAX_HEADER, MAX_READ = 8192, 65536
MESSAGE = "Engine response sender is unconfirmed; preserve the case and do not retry."
PING = (
    b"GET /v1.47/_ping HTTP/1.1\r\nHost: localhost\r\n"
    b"Connection: keep-alive\r\nContent-Length: 0\r\n\r\n"
)


class UnconfirmedSender(ValueError):
    """Creator credentials or a successful ping alone confer no App authority."""


def require(value):
    if not value:
        raise UnconfirmedSender(MESSAGE)


def ticks(pid):
    require(type(pid) is int and pid > 0)
    with open(f"/proc/{pid}/stat", "rb", buffering=0) as stream:
        raw = stream.read(4097)
    prefix, separator, tail = raw.rpartition(b") ")
    parts = tail.split()
    require(len(raw) <= 4096 and separator and prefix.startswith(str(pid).encode() + b" ("))
    require(len(parts) >= 20 and parts[0] in (b"R", b"S", b"I") and parts[19].isdigit())
    value = int(parts[19])
    require(value > 0)
    return value


def alive(fd):
    require(type(fd) is int and fd >= 0)
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    require(not poller.poll(0))


def close_rights(ancillary):
    """Close only descriptors actually delivered by the kernel, before refusal."""
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            values = array.array("i")
            values.frombytes(data[: len(data) - len(data) % values.itemsize])
            for fd in values:
                os.close(fd)


class Sender:
    """Own original creator/sender pidfds, never the caller's socket.

    PID1 is allowed only as a kernel-reported root listener creator. A distinct
    live root response writer (PID>1) is bound before any non-ping operation.
    Later connections must retain both original identities. There is no PID
    argument, socket discovery, reconnect, Engine action or signal here.
    Failures permanently consume this witness and close its owned descriptors.
    """

    def __init__(self):
        self.owner = os.getpid(), get_ident()
        self.creator = self.peer = None
        self.creator_fd = self.peer_fd = -1
        self.closed = False
        require(os.geteuid() == ROOT_UID and os.getegid() == ROOT_GID)

    def check(self):
        require(not self.closed and self.owner == (os.getpid(), get_ident()))
        for value, fd in ((self.creator, self.creator_fd), (self.peer, self.peer_fd)):
            if value is not None:
                alive(fd)
                require(ticks(value[0]) == value[1])

    def _socket(self, channel):
        self.check()
        require(type(channel) is socket.socket and channel.fileno() >= 0)
        require(channel.family == socket.AF_UNIX and not channel.get_inheritable())
        require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM)
        channel.getpeername()
        pid, uid, gid = CREDENTIALS.unpack(
            channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, CREDENTIALS.size)
        )
        require(pid > 0 and (uid, gid) == (ROOT_UID, ROOT_GID))
        if self.creator is None:
            self.creator_fd = os.pidfd_open(pid, 0)
            self.creator = pid, ticks(pid), uid, gid
        require((pid, ticks(pid), uid, gid) == self.creator)
        self.check()

    def _receive(self, channel, limit, *, learn=False):
        self._socket(channel)
        require(type(limit) is int and 0 < limit <= MAX_READ)
        require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1)
        raw, ancillary, flags, _address = channel.recvmsg(
            limit, socket.CMSG_SPACE(CREDENTIALS.size), socket.MSG_CMSG_CLOEXEC
        )
        close_rights(ancillary)
        require(flags & ~socket.MSG_CMSG_CLOEXEC == 0)
        if not raw:
            # Linux SO_PASSCRED may attach an all-zero credential to a stream
            # EOF. This is NOT a writer identity; it is empty framing only.
            require(
                not ancillary
                or ancillary
                == [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, bytes(CREDENTIALS.size))]
            )
            self.check()
            return b""  # Framing EOF only, not sender/native/init exit.
        require(len(ancillary) == 1)
        level, kind, data = ancillary[0]
        require(level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS)
        require(len(data) == CREDENTIALS.size)
        pid, uid, gid = CREDENTIALS.unpack(data)
        require(pid > 1 and (uid, gid) == (ROOT_UID, ROOT_GID))
        if self.peer is None:
            require(learn)
            self.peer_fd = os.pidfd_open(pid, 0)
            self.peer = pid, ticks(pid), uid, gid
        require((pid, ticks(pid), uid, gid) == self.peer)
        self.check()
        return raw

    def receive(self, channel, limit):
        try:
            require(self.peer is not None)
            return self._receive(channel, limit)
        except BlockingIOError:
            raise  # An ordinary nonblocking retry, not a second request.
        except BaseException as error:
            self._fail(error)

    def ping(self, channel, *, deadline):
        """Authenticate the actual writer with one fixed bounded read-only ping.

        Caller provides an already connected, qualified socket. No reconnect or
        additional request follows a failed ping. SO_PASSCRED remains enabled;
        every later read must use receive(), never silently discard credentials.
        """
        try:
            self._socket(channel)
            require(type(deadline) in (int, float))
            require(0 < deadline - time.monotonic() <= 1)
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            channel.setblocking(False)

            def wait(write=False):
                remaining = deadline - time.monotonic()
                require(remaining > 0)
                read, written, _ = select.select(
                    [] if write else [channel], [channel] if write else [], [], remaining
                )
                require(time.monotonic() < deadline and bool(written if write else read))
                self._socket(channel)

            offset = 0
            while offset < len(PING):
                wait(True)
                try:
                    count = channel.send(PING[offset:])
                except BlockingIOError:
                    continue
                require(count > 0)
                offset += count
            response, header_end = bytearray(), None
            while True:
                wait()
                try:
                    part = self._receive(channel, 4096, learn=True)
                except BlockingIOError:
                    continue
                require(part)
                response.extend(part)
                require(len(response) <= MAX_HEADER + 2)
                if header_end is None:
                    split = response.find(b"\r\n\r\n")
                    if split < 0:
                        require(len(response) <= MAX_HEADER)
                        continue
                    header_end = split + 4
                    require(header_end <= MAX_HEADER)
                    lines = bytes(response[:split]).decode("ascii").split("\r\n")
                    require(lines.pop(0) == "HTTP/1.1 200 OK")
                    headers = {}
                    for line in lines:
                        key, separator, value = line.partition(":")
                        require(separator and key and key.strip() == key)
                        require(all(c.isascii() and (c.isalnum() or c == "-") for c in key))
                        key = key.lower()
                        require(key not in headers and all(32 <= ord(c) <= 126 for c in value))
                        headers[key] = value.strip()
                    require(headers.get("content-length") == "2")
                    require(headers.get("content-type") == "text/plain; charset=utf-8")
                    require(headers.get("connection", "").lower() != "close")
                    require(
                        "transfer-encoding" not in headers and "content-encoding" not in headers
                    )
                require(len(response) <= header_end + 2)
                if len(response) == header_end + 2:
                    require(response[header_end:] == b"OK")
                    break
            require(not select.select([channel], [], [], 0)[0])
            self.check()
            require(time.monotonic() < deadline)
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedSender(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        self.closed = True
        for name in ("peer_fd", "creator_fd"):
            fd = getattr(self, name)
            setattr(self, name, -1)
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)  # Cleanup only, never liveness or exit evidence.


if __name__ == "__main__":
    raise SystemExit("Read-only kernel sender only; no installed Engine profile enabled.")
