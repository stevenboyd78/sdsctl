#!/usr/bin/env python3
"""Fixed private Engine peer and one-case dispatch; not an installed host plan.

The sealed host must independently qualify the image, native/interpreter source,
environment, container incarnation, original plan and readiness BEFORE using
this component, and recheck them around attachment. Root-owned local socket and
kernel peer credentials authenticate transport, not those candidate properties.
No scanner ownership, recording begin, process exit or restoration is inferred.
"""

from __future__ import annotations

import json
import os
import select
import socket
import stat
import struct
import time
from contextlib import suppress
from pathlib import Path
from threading import get_ident

import supplemental_handoff_files as files
import supplemental_recording_attachment as attachment
import supplemental_recording_dispatch as dispatch
import supplemental_recording_namespace as namespace

SOCKET = Path("/run/docker.sock")
ROOT_UID = ROOT_GID = 0
MAX_HEADER, MAX_BODY = 8192, 16384
MESSAGE = "Finite recording Engine request is unconfirmed; preserve this case and do not retry."


class UnconfirmedEngine(ValueError):
    """A lost Engine return never permits another create/start or proves exit."""


def require(value):
    if not value:
        raise UnconfirmedEngine(MESSAGE)


def _alive(fd):
    require(type(fd) is int and fd >= 0)
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    require(not poll.poll(0))  # ERR/NVAL/HUP also refuse; none establishes exit.


def _ticks(pid):
    require(type(pid) is int and pid > 1)
    with open(f"/proc/{pid}/stat", "rb", buffering=0) as stream:
        raw = stream.read(4097)
    require(0 < len(raw) <= 4096)
    head, separator, tail = raw.decode("ascii").rpartition(") ")
    fields = tail.split()
    require(separator and head.startswith(str(pid) + " (") and len(fields) >= 20)
    require(fields[0] in ("R", "S", "I") and fields[19].isdigit())
    ticks = int(fields[19])
    require(ticks > 0)
    return ticks


def _deadline(deadline):
    dispatch.binding.clock(deadline)
    remaining = deadline - time.monotonic()
    require(0 < remaining <= 780)
    return remaining


class Endpoint:
    """Only /run/docker.sock in the qualified host PID/mount namespaces.

    Every connection checks the root-owned, non-world-writable socket and its
    root-owned non-writable parent, then kernel SO_PEERCRED against root. The
    first peer is retained by pidfd/start ticks; later connections must be that
    same live peer and unchanged socket. No TCP, path argument or reconnect after
    failure. Host root is trusted; this is not protection against malicious root.
    """

    def __init__(self):
        self.owner = (os.getpid(), get_ident())
        self.parent, self.peer_fd, self.peer = -1, -1, None
        self.closed = False
        try:
            require(os.geteuid() == ROOT_UID)
            self.parent = os.open(str(SOCKET.parent), files.DIRECTORY)
            info = os.fstat(self.parent)
            require(info.st_uid == ROOT_UID and info.st_mode & 0o7022 == 0)
            self.parent_identity = files.identity(info)[:6]
            info = os.stat(SOCKET.name, dir_fd=self.parent, follow_symlinks=False)
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == ROOT_UID)
            require(info.st_mode & 0o7002 == 0 and info.st_nlink == 1)
            self.socket_identity = files.identity(info)
        except BaseException as error:
            self._fail(error)

    def check(self):
        require(not self.closed and self.owner == (os.getpid(), get_ident()))
        require(files.identity(os.fstat(self.parent))[:6] == self.parent_identity)
        require(
            files.identity(os.stat(SOCKET.parent, follow_symlinks=False))[:6]
            == self.parent_identity
        )
        require(
            files.identity(os.stat(SOCKET.name, dir_fd=self.parent, follow_symlinks=False))
            == self.socket_identity
        )
        if self.peer is not None:
            _alive(self.peer_fd)
            require(_ticks(self.peer[0]) == self.peer[1])

    def connect(self, *, deadline):
        channel = None
        try:
            self.check()
            end = min(deadline, time.monotonic() + 1)
            channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            channel.set_inheritable(False)
            channel.settimeout(_deadline(end))
            channel.connect(str(SOCKET))
            _deadline(end)
            pid, uid, gid = struct.unpack(
                "3i",
                channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
            )
            require(uid == ROOT_UID and gid == ROOT_GID)
            observed = (pid, _ticks(pid), uid, gid)
            if self.peer is None:
                self.peer_fd = os.pidfd_open(pid, 0)
                self.peer = observed
            require(self.peer == observed)
            self.check()
            _deadline(end)
            channel.setblocking(False)
            return channel
        except BaseException as error:
            if channel is not None:
                channel.close()
            self._fail(error)

    def _fail(self, error):
        self.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedEngine(MESSAGE) from None

    def close(self):
        self.closed = True
        for name in ("peer_fd", "parent"):
            fd = getattr(self, name)
            setattr(self, name, -1)
            if fd >= 0:
                # Cleanup only; never reported as live or exit evidence.
                with suppress(OSError):
                    os.close(fd)


def _wait(channel, end, *, write=False):
    remaining = _deadline(end)
    readable, writable, _ = select.select(
        [] if write else [channel], [channel] if write else [], [], remaining
    )
    _deadline(end)
    require(bool(writable if write else readable))


def _json_request(endpoint, method, path, body, expected, *, deadline):
    """Internal fixed calls only; bounded HTTP/1.1 Content-Length JSON, no retry.

    Unknown framing, including chunked replies, closes the consumed operation.
    Installed Engine compatibility must be qualified before enabling a host plan.
    """
    end = min(deadline, time.monotonic() + 1)
    raw = b"" if body is None else dispatch.binding.encode(body)
    require(len(raw) <= MAX_BODY)
    request = (
        f"{method} /v1.47{path} HTTP/1.1\r\nHost: localhost\r\n"
        "Connection: close\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(raw)}\r\n\r\n"
    ).encode("ascii") + raw
    channel = endpoint.connect(deadline=end)
    try:
        offset = 0
        while offset < len(request):
            _wait(channel, end, write=True)
            try:
                sent = channel.send(request[offset:])
            except BlockingIOError:
                continue
            require(sent > 0)
            offset += sent
        response, size = bytearray(), None
        while True:
            _wait(channel, end)
            try:
                chunk = channel.recv(4096)
            except BlockingIOError:
                continue
            require(chunk)
            response.extend(chunk)
            require(len(response) <= MAX_HEADER + MAX_BODY)
            if size is None:
                index = response.find(b"\r\n\r\n")
                if index < 0:
                    require(len(response) <= MAX_HEADER)
                    continue
                require(index + 4 <= MAX_HEADER)
                lines = bytes(response[:index]).decode("ascii").split("\r\n")
                status = lines.pop(0).split(" ", 2)
                require(len(status) == 3 and status[:2] == ["HTTP/1.1", str(expected)])
                headers = {}
                for line in lines:
                    key, separator, value = line.partition(":")
                    require(separator and key and key.strip() == key)
                    require(all(c.isascii() and (c.isalnum() or c == "-") for c in key))
                    key = key.lower()
                    require(key not in headers and all(32 <= ord(c) <= 126 for c in value))
                    headers[key] = value.strip()
                require("transfer-encoding" not in headers and "content-encoding" not in headers)
                require(headers.get("content-type") == "application/json")
                length = headers.get("content-length", "")
                require(length.isascii() and length.isdigit() and len(length) <= 5)
                size = int(length)
                require(0 < size <= MAX_BODY)
                del response[: index + 4]
            require(len(response) <= size)
            if len(response) == size:
                break
        _deadline(end)
        endpoint.check()
        value = json.loads(
            response,
            object_pairs_hook=dispatch.binding.protected.evidence.unique,
            parse_constant=dispatch.binding.protected.evidence.reject_constant,
        )
        require(type(value) is dict)
        _deadline(end)
        return value
    finally:
        channel.close()


class Client:
    """One fixed create/attach attempt for an already consumed durable Claim.

    Constructing this client does not qualify candidate source or grant handoff.
    No installed service invokes it. Independent source/container/plan checks
    must surround this mechanism. Attachment returns raw frames, not accepted
    native receipts; the separate host ledger must precede any recording begin.
    """

    def __init__(self, endpoint, claim):
        require(type(endpoint) is Endpoint and type(claim) is dispatch.Claim)
        self.endpoint, self.claim = endpoint, claim
        self.owner = (os.getpid(), get_ident())
        self.create_attempted = self.attach_attempted = self.closed = False
        self.binding_attempted = False
        self.attachment = None

    def _check(self):
        require(not self.closed and self.owner == (os.getpid(), get_ident()))
        self.claim.check()
        self.endpoint.check()

    def create(self):
        try:
            self._check()
            require(not self.create_attempted and self.claim.state.phase == "create_intent")
            self.create_attempted = True
            pins = self.claim.pins
            value = _json_request(
                self.endpoint,
                "POST",
                f"/containers/{pins.init.container_id}/exec",
                pins.command.create_body(),
                201,
                deadline=pins.command.ready_by,
            )
            require(set(value) == {"Id"})
            dispatch.execution._digest(value["Id"])
            self.claim.created(value["Id"])  # Durable before any exec inspection.
            self._check()
            return value["Id"]
        except BaseException as error:
            self._fail(error)

    def attach(self, *, finish_by):
        try:
            self._check()
            require(self.create_attempted and not self.attach_attempted)
            require(self.claim.state.phase == "created")
            self.attach_attempted = True
            pins, execution_id = self.claim.pins, self.claim.state.execution_id
            inspected = _json_request(
                self.endpoint,
                "GET",
                f"/exec/{execution_id}/json",
                None,
                200,
                deadline=pins.command.ready_by,
            )
            self.claim.attach_intent(inspected)  # Durable BEFORE any start request.
            self._check()
            channel = self.endpoint.connect(deadline=pins.command.ready_by)
            self.attachment = attachment.Attachment(
                channel, execution_id, ready_by=pins.command.ready_by, finish_by=finish_by
            )
            self.attachment.start(deadline=pins.command.ready_by)
            self._check()
            return self.attachment
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.claim.poisoned = True
        self.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedEngine(MESSAGE) from None

    def bind_processes(self, reported, *, zero_domain=None):
        """Bind one actual ready-frame actor set to this exact Engine execution.

        The caller must validate the actual received ready envelope, original
        context and source independently; a supplied identity dictionary is not
        authorization. This method obtains both exec inspections itself through
        the retained Engine peer. PID 0, stopped/changed execs and any namespace
        uncertainty refuse binding, without polling, restarting or resending.

        On success the caller owns the returned Witness and must retain it for
        separate native/watchdog/guardian/init exit checks, then close it. Closing
        this transport never closes that independent witness or proves any exit.
        This operation sends no recording begin and does not grant one.
        """
        witness = None
        try:
            self._check()
            require(self.create_attempted and self.attach_attempted and not self.binding_attempted)
            require(self.claim.state.phase == "attach_intent")
            channel = self.attachment
            require(type(channel) is attachment.Attachment and not channel.closed)
            require(channel.started and channel.reads == 1 and not channel.begun)
            self.binding_attempted = True

            def running():
                self._check()
                pins, execution_id = self.claim.pins, self.claim.state.execution_id
                value = _json_request(
                    self.endpoint,
                    "GET",
                    f"/exec/{execution_id}/json",
                    None,
                    200,
                    deadline=pins.command.ready_by,
                )
                observed = dispatch.execution.inspect(
                    value,
                    execution_id=execution_id,
                    container_id=pins.init.container_id,
                    command=pins.command,
                )
                self._check()
                require(observed.phase == "running")
                return observed.pid

            guardian_pid = running()
            witness = namespace.Witness(
                self.claim.witness, guardian_pid, reported, zero_domain=zero_domain
            )
            require(running() == guardian_pid)
            witness.refresh()
            self._check()
            return witness
        except BaseException as error:
            if witness is not None:
                with suppress(Exception):
                    witness.close()
            self._fail(error)

    def close(self):
        self.closed = True
        try:
            if self.attachment is not None:
                self.attachment.close()
        finally:
            self.endpoint.close()


if __name__ == "__main__":
    raise SystemExit("Private finite dispatch only; no installed host plan enabled.")
