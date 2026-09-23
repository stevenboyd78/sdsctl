#!/usr/bin/env python3
"""Bounded decoder for one upgraded, non-TTY private exec output stream.

Pure parsing, not a Docker client, socket opener or exec authenticator. It does
not create/start/attach/retry anything. The future host adapter must separately
qualify the exact exec, enforce absolute I/O deadlines and observe all exits.
Protocol reference: https://docs.docker.com/reference/api/engine/version/v1.47/
"""

from __future__ import annotations

import json
import re
import struct

import supplemental_recording_wire as wire

MAX_HEADER = 8192
MAX_CHUNK = 65536
MAX_SEGMENTS = 4096
MAX_STDOUT = 4 * (wire.HEADER.size + wire.MAX_BYTES)
MAX_INPUT = MAX_HEADER + MAX_SEGMENTS * 8 + MAX_STDOUT
MESSAGE = "Finite recording exec stream is unconfirmed; do not attach again or replay."


class UnconfirmedExecStream(ValueError):
    """No byte sequence by itself proves native acknowledgment or exec exit."""


def require(value):
    if not value:
        raise UnconfirmedExecStream(MESSAGE)


def _upgrade(raw):
    lines = raw.split(b"\r\n")
    require(0 < len(raw) <= MAX_HEADER and 4 <= len(lines) <= 33)
    require(re.fullmatch(rb"HTTP/1\.1 101 [\x20-\x7e]{1,64}", lines[0]) is not None)
    fields = {}
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        require(separator and re.fullmatch(rb"[A-Za-z0-9-]{1,64}", name) is not None)
        name = name.lower()
        require(name not in fields and all(32 <= octet <= 126 for octet in value))
        fields[name] = value.strip().lower()
    require(fields.get(b"connection") == b"upgrade" and fields.get(b"upgrade") == b"tcp")
    require(fields.get(b"content-type") == b"application/vnd.docker.multiplexed-stream")
    require(
        not {b"content-length", b"transfer-encoding", b"content-encoding", b"trailer"}
        & fields.keys()
    )


class Decoder:
    """Consume a finite HTTP upgrade followed by Docker stdout segments.

    Docker segment boundaries need not match the inner operator frame boundaries.
    stderr/stdin/system-error segments, malformed headers, partial EOF, excess
    bytes and a fifth application frame poison the entire stream. No bytes from
    stderr or an error body are exposed. finish() is framing-only, never exit proof.
    """

    def __init__(self):
        self.pending = bytearray()
        self.stdout = bytearray()
        self.upgraded = self.used = False
        self.received = self.output_bytes = self.segments = self.messages = 0
        self.remaining = None

    def feed(self, raw):
        try:
            require(not self.used and type(raw) is bytes and 0 < len(raw) <= MAX_CHUNK)
            self.received += len(raw)
            require(self.received <= MAX_INPUT)
            self.pending.extend(raw)
            if not self.upgraded:
                end = self.pending.find(b"\r\n\r\n")
                if end < 0:
                    require(len(self.pending) <= MAX_HEADER)
                    return []
                require(end + 4 <= MAX_HEADER)
                _upgrade(bytes(self.pending[:end]))
                del self.pending[: end + 4]
                self.upgraded = True
            result = []
            while self.pending or self.remaining == 0:
                if self.remaining is None:
                    if len(self.pending) < 8:
                        break
                    channel, reserved, size = struct.unpack("!B3sI", self.pending[:8])
                    del self.pending[:8]
                    self.segments += 1
                    require(
                        self.segments <= MAX_SEGMENTS and channel == 1 and reserved == b"\0" * 3
                    )
                    require(size <= MAX_STDOUT - self.output_bytes)
                    self.remaining = size
                count = min(self.remaining, len(self.pending))
                self.stdout.extend(self.pending[:count])
                del self.pending[:count]
                self.output_bytes += count
                self.remaining -= count
                if self.remaining == 0:
                    self.remaining = None
                while len(self.stdout) >= wire.HEADER.size:
                    size = wire.HEADER.unpack(self.stdout[: wire.HEADER.size])[0]
                    require(0 < size <= wire.MAX_BYTES and self.messages < 4)
                    end = wire.HEADER.size + size
                    if len(self.stdout) < end:
                        break
                    payload = bytes(self.stdout[wire.HEADER.size : end])
                    value = json.loads(
                        payload.decode("ascii"),
                        object_pairs_hook=wire._pairs,
                        parse_constant=wire._reject,
                    )
                    require(wire.encode(value) == payload)
                    del self.stdout[:end]
                    self.messages += 1
                    result.append(value)
                if self.remaining is not None:
                    break
            return result
        except BaseException as error:
            self.used = True
            self.pending.clear()
            self.stdout.clear()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedExecStream(MESSAGE) from None

    def finish(self):
        require(not self.used)
        self.used = True
        require(
            self.upgraded
            and self.messages == 4
            and self.remaining is None
            and not self.pending
            and not self.stdout
        )


if __name__ == "__main__":
    raise SystemExit("Pure private stream decoder only; no Docker or scanner operation enabled.")
