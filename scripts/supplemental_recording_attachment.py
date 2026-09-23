#!/usr/bin/env python3
"""One-use I/O on an already qualified private Docker exec connection.

This component does NOT connect, create an exec, authenticate an image/source,
authorize a begin, inspect exit or restore scanner ownership. The future host
adapter must independently qualify the socket, exec and original deadlines and
durably record its start intent BEFORE send_begin. Parsed frames are not native
acknowledgments. No installed host plan or public entry point uses this module.
"""

from __future__ import annotations

import math
import os
import re
import select
import socket
import time
from threading import get_ident

import supplemental_recording_engine_sender as senders
import supplemental_recording_exec_stream as framing

MESSAGE = "Finite recording attachment is unconfirmed; preserve the case and do not reconnect."
PHASES = ("ready", "started", "completed", "exited")


class UnconfirmedAttachment(ValueError):
    """No failure or EOF establishes exec, native or container-init exit."""


def require(value):
    if not value:
        raise UnconfirmedAttachment(MESSAGE)


class Attachment:
    """Take ownership of a connected, non-inheritable Unix stream socket.

    start sends only a fixed non-TTY exec/start upgrade request. One private
    begin frame follows one ready frame; at most four ordered operator frames
    are received. All operations are bounded by the ORIGINAL ready/final
    deadlines as well as caller step deadlines. Any failed/late/interrupted
    operation closes the socket and permanently consumes this instance.

    There is deliberately no default Docker socket, connect(), arbitrary HTTP
    method/path/body, caller argv, restart, attach-again or exit-inspection API.
    """

    def __init__(self, channel, execution_id, *, ready_by, finish_by, sender=None):
        self.owner = (os.getpid(), get_ident())
        self.channel = channel if type(channel) is socket.socket else None
        self.closed = self.started = self.begun = self.finished = False
        self.reads, self.pending = 0, []
        self.decoder = framing.Decoder()
        self.sender = sender  # Borrowed original witness; never owned or rebound here.
        try:
            require(self.channel is not None and channel.fileno() >= 0)
            require(channel.family == socket.AF_UNIX)
            require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM)
            channel.getpeername()  # Reject unconnected/listening descriptors.
            require(not channel.get_inheritable())
            self._sender_check()
            require(type(execution_id) is str and re.fullmatch(r"[0-9a-f]{64}", execution_id))
            for deadline in (ready_by, finish_by):
                require(type(deadline) in (int, float) and math.isfinite(deadline))
            now = time.monotonic()
            require(0 < ready_by - now <= 600 and ready_by < finish_by <= now + 780)
            self.ready_by, self.finish_by = ready_by, finish_by
            self.execution_id = execution_id
            channel.setblocking(False)
        except BaseException as error:
            self._fail(error)

    def _check(self, deadline, *, ready=False):
        require(not self.closed and self.owner == (os.getpid(), get_ident()))
        self._sender_check()
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        require(0 < deadline - time.monotonic() <= 780)
        require(deadline <= (self.ready_by if ready else self.finish_by))

    def _sender_check(self):
        if self.sender is not None:
            require(type(self.sender) is senders.Sender and self.sender.peer is not None)
            self.sender.check()
            require(self.channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1)

    def _receive_bytes(self):
        if self.sender is not None:
            return self.sender.receive(self.channel, framing.MAX_CHUNK)
        return self.channel.recv(framing.MAX_CHUNK)

    def _wait(self, deadline, *, writing):
        self._check(deadline)
        read, write, _ = select.select(
            [] if writing else [self.channel],
            [self.channel] if writing else [],
            [],
            max(0, deadline - time.monotonic()),
        )
        self._check(deadline)
        require(bool(write if writing else read))

    def _write(self, raw, deadline):
        offset = 0
        while offset < len(raw):
            self._wait(deadline, writing=True)
            try:
                count = self.channel.send(raw[offset:])
            except BlockingIOError:
                continue
            require(count > 0)
            offset += count
        self._check(deadline)

    def _read(self, deadline):
        while True:
            self._wait(deadline, writing=False)
            try:
                raw = self._receive_bytes()
            except BlockingIOError:
                continue
            self._check(deadline)
            if not raw:
                return False
            values = self.decoder.feed(raw)
            self._check(deadline)  # Parsing cannot extend a transport deadline.
            for value in values:
                index = self.reads + len(self.pending)
                require(index < 4 and value.get("phase") == PHASES[index])
                require(self.begun or index == 0)
                self.pending.append(value)
            return True

    def start(self, *, deadline):
        try:
            self._check(deadline, ready=True)
            require(not self.started)
            self.started = True  # Consume before any partial or lost HTTP write.
            body = b'{"Detach":false,"Tty":false}'
            request = (
                f"POST /v1.47/exec/{self.execution_id}/start HTTP/1.1\r\n"
                "Host: localhost\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode("ascii") + body
            self._write(request, deadline)
            while not self.decoder.upgraded:
                require(self._read(deadline))
            self._check(deadline, ready=True)
        except BaseException as error:
            self._fail(error)

    def receive(self, *, deadline):
        try:
            self._check(deadline, ready=self.reads == 0)
            require(self.started and self.reads < 4 and (self.reads == 0 or self.begun))
            while not self.pending:
                require(self._read(deadline))
            value = self.pending.pop(0)
            self.reads += 1
            self._check(deadline, ready=self.reads == 1)
            return value
        except BaseException as error:
            self._fail(error)

    def send_begin(self, value, *, deadline):
        try:
            self._check(deadline, ready=True)
            require(self.started and self.reads == 1 and not self.begun and not self.pending)
            self.begun = True  # No resend if the final send return is lost.
            require(type(value) is dict and value.get("phase") == "begin")
            raw = framing.wire.encode(value)
            self._write(framing.wire.HEADER.pack(len(raw)) + raw, deadline)
            self._check(deadline, ready=True)
        except BaseException as error:
            self._fail(error)

    def finish(self, *, deadline):
        """Require clean framing/EOF after four messages; NOT any process exit."""
        try:
            self._check(deadline)
            require(self.started and self.begun and self.reads == 4 and not self.pending)
            while self._read(deadline):
                require(not self.pending)
            self.decoder.finish()
            self._check(deadline)
            self.finished = True
            self.close()
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedAttachment(MESSAGE) from None

    def close(self):
        if self.closed:
            return
        require(self.owner == (os.getpid(), get_ident()))
        self.closed = True
        self.pending.clear()
        if self.channel is not None:
            self.channel.close()


class ProbeAttachment(Attachment):
    """One request/reply on an already qualified exec socket, never an operator.

    Takes socket ownership exactly like Attachment, but has a separate short
    absolute sampling deadline and no ready/begin/native-return phases. Shared
    methods provide bounded transport only. The exact Engine exec and retained
    original actors must still be independently authenticated around this I/O.
    Clean framing/EOF is not cached health, process exit or restoration authority.
    """

    def __init__(self, channel, execution_id, *, probe_by, sender=None):
        self.owner = (os.getpid(), get_ident())
        self.channel = channel if type(channel) is socket.socket else None
        self.closed = self.started = self.begun = self.finished = False
        self.reads, self.pending = 0, []
        self.decoder = framing.Decoder(message_limit=1)
        self.sender = sender
        try:
            require(self.channel is not None and channel.fileno() >= 0)
            require(channel.family == socket.AF_UNIX)
            require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM)
            channel.getpeername()
            require(not channel.get_inheritable())
            self._sender_check()
            require(type(execution_id) is str and re.fullmatch(r"[0-9a-f]{64}", execution_id))
            require(type(probe_by) in (int, float) and math.isfinite(probe_by))
            require(0 < probe_by - time.monotonic() <= 8)
            self.ready_by = self.finish_by = probe_by
            self.execution_id = execution_id
            channel.setblocking(False)
        except BaseException as error:
            self._fail(error)

    def _read(self, deadline):
        while True:
            self._wait(deadline, writing=False)
            try:
                raw = self._receive_bytes()
            except BlockingIOError:
                continue
            self._check(deadline)
            if not raw:
                return False
            values = self.decoder.feed(raw)
            self._check(deadline)
            for value in values:
                require(self.begun and self.reads + len(self.pending) == 0)
                require(value.get("kind") == "finite-recording-cached-probe-result")
                self.pending.append(value)
            return True

    def send_request(self, value, *, deadline):
        try:
            self._check(deadline)
            require(self.started and not self.begun and self.reads == 0 and not self.pending)
            require(
                not self.decoder.pending
                and not self.decoder.stdout
                and self.decoder.segments == 0
                and self.decoder.remaining is None
            )
            require(not select.select([self.channel], [], [], 0)[0])
            self.begun = True  # Even an incomplete write consumes this sample.
            require(type(value) is dict and value.get("kind") == "finite-recording-cached-probe")
            raw = framing.wire.encode(value)
            self._write(framing.wire.HEADER.pack(len(raw)) + raw, deadline)
            self._check(deadline)
        except BaseException as error:
            self._fail(error)

    def send_begin(self, value, *, deadline):
        self._fail(UnconfirmedAttachment(MESSAGE))

    def receive(self, *, deadline):
        try:
            require(self.begun and self.reads == 0)
            return super().receive(deadline=deadline)
        except BaseException as error:
            self._fail(error)

    def finish(self, *, deadline):
        try:
            self._check(deadline)
            require(self.started and self.begun and self.reads == 1 and not self.pending)
            while self._read(deadline):
                require(not self.pending)
            self.decoder.finish()
            self._check(deadline)
            self.finished = True
            self.close()
        except BaseException as error:
            self._fail(error)


class WebAttachment(Attachment):
    """One original startup request/listening reply, then a held-open channel.

    Distinct from the short passive probe and recording operator. No begin,
    keepalive command, second request, success or EOF/finish acknowledgment is
    allowed. Closing this channel asks only the already bound web process to
    end; actual process and Engine exit observations remain separate. The host
    must persist a unique web-exec intent and bind actual original Ready/PIDs
    before sending anything. This class provides bounded byte I/O only.
    """

    def __init__(self, channel, execution_id, *, ready_by, finish_by, sender=None):
        super().__init__(
            channel, execution_id, ready_by=ready_by, finish_by=finish_by, sender=sender
        )
        self.decoder = framing.Decoder(message_limit=1)

    def _read(self, deadline):
        while True:
            self._wait(deadline, writing=False)
            try:
                raw = self._receive_bytes()
            except BlockingIOError:
                continue
            self._check(deadline)
            if not raw:
                return False
            values = self.decoder.feed(raw)
            self._check(deadline)
            for value in values:
                require(self.begun and self.reads + len(self.pending) == 0)
                require(value.get("kind") == "finite-recording-web-listening")
                self.pending.append(value)
            return True

    def send_request(self, value, *, deadline):
        try:
            self._check(deadline, ready=True)
            require(self.started and not self.begun and self.reads == 0 and not self.pending)
            require(
                not self.decoder.pending
                and not self.decoder.stdout
                and self.decoder.segments == 0
                and self.decoder.remaining is None
            )
            require(not select.select([self.channel], [], [], 0)[0])
            self.begun = True
            require(type(value) is dict and value.get("kind") == "finite-recording-web-startup")
            raw = framing.wire.encode(value)
            self._write(framing.wire.HEADER.pack(len(raw)) + raw, deadline)
            self._check(deadline, ready=True)
        except BaseException as error:
            self._fail(error)

    def receive(self, *, deadline):
        try:
            require(self.begun and self.reads == 0)
            return super().receive(deadline=deadline)
        except BaseException as error:
            self._fail(error)

    def check_quiet(self, *, deadline):
        """No later output or EOF may silently become continuing web health."""
        try:
            self._check(deadline)
            require(self.started and self.begun and self.reads == 1 and not self.pending)
            require(
                not self.decoder.pending
                and not self.decoder.stdout
                and self.decoder.remaining is None
            )
            require(not select.select([self.channel], [], [], 0)[0])
            self._check(deadline)
        except BaseException as error:
            self._fail(error)

    def send_begin(self, value, *, deadline):
        self._fail(UnconfirmedAttachment(MESSAGE))

    def finish(self, *, deadline):
        self._fail(UnconfirmedAttachment(MESSAGE))


if __name__ == "__main__":
    raise SystemExit("Private attachment I/O only; no installed host action enabled.")
