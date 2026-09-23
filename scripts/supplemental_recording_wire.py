#!/usr/bin/env python3
"""Bounded private operator pipe framing, not host or native authentication.

Only a future qualified exec attachment may carry these frames. This module has
no Docker, scanner, process-launch, acknowledgment or recovery operation. A frame
alone is never an authenticated native return. No reconnect or partial replay.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import select
import socket
import stat
import struct
import time
from threading import get_ident

MAX_BYTES = 16384
MAX_SECONDS = 780
HEADER = struct.Struct("!I")
MESSAGE = "Finite recording operator stream is unconfirmed; do not reconnect or replay."


class UnconfirmedStream(ValueError):
    """No partial frame, acknowledgment or implicit retry permission is returned."""


def require(value):
    if not value:
        raise UnconfirmedStream(MESSAGE)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _reject(value):
    raise UnconfirmedStream(MESSAGE)


def encode(value):
    try:
        require(type(value) is dict)
        raw = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
        require(0 < len(raw) <= MAX_BYTES)
        require(json.loads(raw) == value)  # No implicit tuple/key-type conversion.
        return raw
    except Exception:
        raise UnconfirmedStream(MESSAGE) from None


class Stream:
    """Own duplicate fds for two distinct pipes/Unix streams, with fixed budgets.

    The operator receives one begin frame and sends at most ready, started,
    completed and exited. The host has the opposite budget.
    A separate probe role permits exactly one request and one reply; it grants
    no operator messages or recording authority. Semantic validation and actual
    exec/native provenance are the caller's separate responsibilities.
    Failed/late I/O poisons both directions; closing restores original flags on
    the retained descriptions, then closes the duplicates (never caller's fds).
    """

    def __init__(self, incoming, outgoing, *, role):
        self.fds, self.flags = [], []
        self.poisoned, self.closed = False, False
        self.owner = (os.getpid(), get_ident())
        self.reads = self.writes = 0
        try:
            require(role in ("host", "operator", "probe"))
            require(type(incoming) is int and type(outgoing) is int and incoming != outgoing)
            require(incoming >= 0 and outgoing >= 0)
            self.read_limit, self.write_limit = {
                "host": (4, 1),
                "operator": (1, 4),
                "probe": (1, 1),
            }[role]
            identities = set()
            for index, fd in enumerate((incoming, outgoing)):
                info = os.fstat(fd)
                require(stat.S_ISFIFO(info.st_mode) or stat.S_ISSOCK(info.st_mode))
                identity = info.st_dev, info.st_ino
                require(identity not in identities)
                identities.add(identity)
                copy = os.dup(fd)
                self.fds.append(copy)
                self.flags.append(fcntl.fcntl(copy, fcntl.F_GETFL))
                mode = self.flags[-1] & os.O_ACCMODE
                require(mode != (os.O_WRONLY if index == 0 else os.O_RDONLY))
                if stat.S_ISSOCK(info.st_mode):
                    channel = socket.socket(fileno=copy)
                    try:
                        require(channel.family == socket.AF_UNIX)
                        require(
                            channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
                            == socket.SOCK_STREAM
                        )
                    finally:
                        channel.detach()
                os.set_blocking(copy, False)
        except BaseException:
            self.close()
            raise UnconfirmedStream(MESSAGE) from None

    def _check(self, deadline):
        require(not self.closed and not self.poisoned and self.owner == (os.getpid(), get_ident()))
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        require(0 < deadline - time.monotonic() <= MAX_SECONDS)

    def _wait(self, fd, deadline, *, writing):
        self._check(deadline)
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        read, write, _ = select.select(
            [] if writing else [fd], [fd] if writing else [], [], remaining
        )
        require((write if writing else read) == [fd] and time.monotonic() < deadline)

    def _read(self, size, deadline):
        raw = bytearray()
        while len(raw) < size:
            self._wait(self.fds[0], deadline, writing=False)
            try:
                chunk = os.read(self.fds[0], size - len(raw))
            except BlockingIOError:
                continue
            require(chunk)
            raw.extend(chunk)
        self._check(deadline)
        return bytes(raw)

    def receive(self, *, deadline):
        try:
            self._check(deadline)
            require(self.reads < self.read_limit)
            self.reads += 1  # Consume even an incomplete header or invalid payload.
            size = HEADER.unpack(self._read(HEADER.size, deadline))[0]
            require(0 < size <= MAX_BYTES)
            raw = self._read(size, deadline)
            value = json.loads(
                raw.decode("ascii"), object_pairs_hook=_pairs, parse_constant=_reject
            )
            require(encode(value) == raw)
            self._check(deadline)
            return value
        except BaseException:
            self.poisoned = True
            raise UnconfirmedStream(MESSAGE) from None

    def send(self, value, *, deadline):
        try:
            self._check(deadline)
            require(self.writes < self.write_limit)
            self.writes += 1
            raw = encode(value)
            raw, offset = HEADER.pack(len(raw)) + raw, 0
            while offset < len(raw):
                self._wait(self.fds[1], deadline, writing=True)
                try:
                    size = os.write(self.fds[1], raw[offset:])
                except BlockingIOError:
                    continue
                require(size > 0)
                offset += size
            self._check(deadline)
        except BaseException:
            self.poisoned = True
            raise UnconfirmedStream(MESSAGE) from None

    def close(self):
        if self.closed:
            return
        require(self.owner == (os.getpid(), get_ident()))
        self.closed = self.poisoned = True
        failed = False
        for index, fd in enumerate(self.fds):
            try:
                if index < len(self.flags):
                    fcntl.fcntl(fd, fcntl.F_SETFL, self.flags[index])
            except OSError:
                failed = True
            finally:
                try:
                    os.close(fd)
                except OSError:
                    failed = True
        self.fds.clear()
        require(not failed)


if __name__ == "__main__":
    raise SystemExit("Private framing only; no installed operator or host transport is enabled.")
