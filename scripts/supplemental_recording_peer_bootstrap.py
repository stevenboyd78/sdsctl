#!/usr/bin/env python3
"""One-use original-peer descriptor delivery, not App admission or a launcher.

This separately selected uninstalled bootstrap borrows an ALREADY connected
private SEQPACKET socket. The qualified outer launcher must provision that
connection and authenticate both runtimes, inputs, original process witnesses,
independent deadlines and action gates. This library creates no listener, path,
process, clock, journal or App operation. It does not change Link, preflight
permissions or any observed source profile. No installed command selects it.
"""

from __future__ import annotations

import fcntl
import math
import os
import secrets
import select
import socket
import struct
import time
from dataclasses import asdict, dataclass
from threading import Lock, get_ident

import supplemental_recording_service_cli_channel as links

KIND = "finite-recording-peer-channel-bootstrap-v1"
MESSAGE = "Recording peer channel delivery is unconfirmed; preserve the case and do not retry."
MAX_BYTES, SECONDS, ROOT_UID = 2048, 2.0, 0


class UnconfirmedBootstrap(ValueError):
    """A transport acknowledgment never proves action scope or peer readiness."""


def require(value):
    if not value:
        raise UnconfirmedBootstrap(MESSAGE)


def _sockets(channels):
    require(type(channels) is links.Channels)
    result = []
    for endpoint, passcred in ((channels.incoming, 1), (channels.outgoing, 0)):
        links.control.returns._socket(endpoint)  # Anonymous AF_UNIX SEQPACKET only.
        require(endpoint.gettimeout() == 0.0 and not endpoint.get_inheritable())
        require(fcntl.fcntl(endpoint.fileno(), fcntl.F_GETFL) & os.O_NONBLOCK)
        require(endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == passcred)
        result.append(links._identity(endpoint.fileno()))
    require(result[0] != result[1])
    require(not select.select([channels.incoming], [], [], 0)[0])
    return result


@dataclass(frozen=True)
class Receipt:
    context_sha256: str
    offer_sha256: str


class Endpoint:
    """One delivery on one original authenticated socket; no reconnect/retry.

    mode is deliver for the outer owner or receive for the selected role. peer
    is the OTHER role's original witness, not the bootstrap socket's sender.
    Both original witnesses are borrowed and remain caller-owned. The common
    context pins all three process identities, the final plan, the independently
    authenticated runtime-declaration digest and the receiving role. Equal new
    owner objects, changed descriptors or lost processes are never adopted.

    Outer and both roles must currently have identical user/time namespaces,
    as required by the existing Link, not merely coincident numeric clocks.
    Original ClockWitness ownership/provenance remains the launcher's duty.
    Construction reads only; receive() owns received duplicates, deliver()
    borrows its supplied Channels. The caller retires original sender copies
    after acknowledgment and BEFORE any active action grant.
    """

    def __init__(
        self,
        channel,
        plan,
        clock,
        local,
        remote,
        peer,
        *,
        role,
        mode,
        declaration_sha256,
        deadline=None,
    ):
        began = time.monotonic()
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock = Lock()
        self.used = self.failed = self.closed = False
        self.handles = []
        try:
            require(type(self) is Endpoint and self.owner[2] == ROOT_UID)
            require(type(channel) is socket.socket)
            require(type(clock) is links.clock.ClockWitness)
            require(type(local) is links.processes.ProcessIdentity and local.pid == self.owner[0])
            require(type(remote) is type(peer) is links.processes.ProcessWitness)
            require(remote is not peer)
            require(type(role) is str and role in ("writer", "observer"))
            require(type(mode) is str and mode in ("deliver", "receive"))
            require(type(declaration_sha256) is str)
            links.base.digest(declaration_sha256)
            ids = local, remote.identity, peer.identity
            for identity in ids:
                identity.__post_init__()
            require(
                len({item.pid for item in ids}) == len({item.container_id for item in ids}) == 3
            )
            self.channel, self.plan, self.clock = channel, plan, clock
            self.local, self.remote, self.peer = local, remote, peer
            self.role, self.mode, self.declaration_sha256 = role, mode, declaration_sha256
            self.pin = links.plans.PinnedPlan(plan)
            self.origin = clock.original
            self.context = links.base.encode(
                dict(
                    schema=1,
                    kind=KIND,
                    plan_sha256=plan.sha256,
                    declaration_sha256=declaration_sha256,
                    role=role,
                    outer=asdict(local if mode == "deliver" else remote.identity),
                    recipient=asdict(remote.identity if mode == "deliver" else local),
                    peer=asdict(peer.identity),
                )
            )
            self.context_sha256 = links.base.checksum(links._decode(self.context))
            self.end = min(began + SECONDS, plan.lease["ready_by"])
            if deadline is not None:
                require(type(deadline) in (int, float) and math.isfinite(deadline))
                self.end = min(self.end, deadline)
            self.objects = channel, plan, clock, local, remote, peer, self.pin, self.origin
            self.values = self._values()
            self.sockets = channel.fileno(), links._identity(channel.fileno())
            self.targets = tuple(
                (w, w.identity, w.fd, links._identity(w.fd)) for w in (remote, peer)
            )
            for identity in ids:
                for name in ("user", "time", "time_for_children"):
                    path = f"/proc/{identity.pid}/ns/{name}"
                    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                    try:
                        fd_identity = links._identity(fd)
                    except BaseException:
                        os.close(fd)
                        raise
                    self.handles.append((path, fd, fd_identity))
            self.original_handles = tuple(self.handles)
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _values(self):
        return (
            self.role,
            self.mode,
            self.declaration_sha256,
            self.context,
            self.context_sha256,
            self.end,
        )

    def _guard(self):
        require(not self.closed and not self.failed and type(self) is Endpoint)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(time.monotonic() < self.end)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        self.channel,
                        self.plan,
                        self.clock,
                        self.local,
                        self.remote,
                        self.peer,
                        self.pin,
                        self.origin,
                    ),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self._values() == self.values)
        self.pin.check(self.plan)
        require(self.clock.original is self.origin)
        observed = self.clock.read()
        self.plan.check_clock(observed)
        require(observed.boottime_ns / links.clock.NS < self.plan.deadlines.ready_by)
        require(
            links.processes.read_identity(self.local.pid, self.local.container_id) == self.local
        )
        for witness, identity, fd, original in self.targets:
            require(witness.identity is identity and witness.fd == fd)
            require(not os.get_inheritable(fd))
            require(links._identity(fd) == original and not witness.exited())
            links.processes.ProcessWitness._live_descriptor(fd, identity.pid)
            require(links.processes.read_identity(identity.pid, identity.container_id) == identity)
        require(tuple(self.handles) == self.original_handles)
        for path, fd, identity in self.original_handles:
            require(not os.get_inheritable(fd) and links._identity(fd) == identity)
            info = os.stat(path)
            require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) == identity)
        namespaces = [item[2][:2] for item in self.original_handles]
        require(namespaces[0] == namespaces[3] == namespaces[6])
        require(
            all(
                namespaces[index] == self.plan.original_clock.namespace
                for index in (1, 2, 4, 5, 7, 8)
            )
        )
        fd, identity = self.sockets
        require(self.channel.fileno() == fd and links._identity(fd) == identity)
        require(self.channel.family == socket.AF_UNIX)
        require(self.channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_SEQPACKET)
        require(self.channel.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0)
        require(self.channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1)
        require(self.channel.gettimeout() == 0.0 and not self.channel.get_inheritable())
        require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK)
        self.channel.getpeername()
        credentials = self.channel.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, links.control.returns.CREDENTIALS.size
        )
        require(
            links.control.returns.CREDENTIALS.unpack(credentials)
            == (self.remote.identity.pid, *self.owner[2:])
        )
        require(time.monotonic() < self.end)

    def _wait(self, sending=False):
        self._guard()
        read = [self.remote.fd, self.peer.fd] + ([] if sending else [self.channel])
        readable, writable, _ = select.select(
            read, [self.channel] if sending else [], [], max(0, self.end - time.monotonic())
        )
        require(self.remote.fd not in readable and self.peer.fd not in readable)
        require(bool(writable) if sending else self.channel in readable)
        self._guard()

    def _send(self, value, fds=()):
        raw = links.base.encode(value)
        require(len(raw) <= MAX_BYTES)
        self._wait(sending=True)
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("2i", *fds))] if fds else []
        require(self.channel.sendmsg([raw], ancillary) == len(raw))
        self._guard()
        return links.base.checksum(value)

    def _receive(self, *, rights=False):
        installed = []
        try:
            self._wait()
            raw, ancillary, flags, _ = self.channel.recvmsg(
                MAX_BYTES,
                socket.CMSG_SPACE(links.control.returns.CREDENTIALS.size) + socket.CMSG_SPACE(8),
                socket.MSG_CMSG_CLOEXEC,
            )
            credentials, other = [], []
            for level, kind, data in ancillary:
                if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                    installed.extend(
                        value[0] for value in struct.iter_unpack("i", data[: len(data) // 4 * 4])
                    )
                    other.append((level, kind))
                elif (level, kind) == (socket.SOL_SOCKET, socket.SCM_CREDENTIALS):
                    credentials.append(data)
                else:
                    other.append((level, kind))
            require(flags & ~socket.MSG_CMSG_CLOEXEC == 0)
            require(
                credentials
                == [
                    links.control.returns.CREDENTIALS.pack(
                        self.remote.identity.pid, *self.owner[2:]
                    )
                ]
            )
            require(other == ([(socket.SOL_SOCKET, socket.SCM_RIGHTS)] if rights else []))
            require(len(installed) == (2 if rights else 0))
            require(all(not os.get_inheritable(fd) for fd in installed))
            value = links._decode(raw)
            require(type(value.get("schema")) is int and value["schema"] == 1)
            require(
                value.get("kind") == KIND and value.get("context_sha256") == self.context_sha256
            )
            self._guard()
            result, installed = tuple(installed), []
            return value, result
        finally:
            for fd in installed:
                os.close(fd)

    def _frame(self, phase, nonce, **fields):
        return dict(
            schema=1,
            kind=KIND,
            context_sha256=self.context_sha256,
            phase=phase,
            nonce=nonce,
            **fields,
        )

    def _enter(self, mode):
        require(self.lock.acquire(blocking=False))
        try:
            require(not self.used and self.mode == mode)
            self.used = True
            self._guard()
        except BaseException:
            self.lock.release()
            raise

    def deliver(self, channels):
        acquired = False
        try:
            self._enter("deliver")
            acquired = True
            identities = _sockets(channels)
            request, _ = self._receive()
            nonce = request.get("nonce")
            require(type(nonce) is str)
            links.base.digest(nonce)
            require(links.base.encode(request) == links.base.encode(self._frame("request", nonce)))
            require(_sockets(channels) == identities)
            offer = self._frame("endpoints", nonce, identities=[list(item) for item in identities])
            digest = self._send(offer, (channels.incoming.fileno(), channels.outgoing.fileno()))
            response, _ = self._receive()
            require(
                links.base.encode(response)
                == links.base.encode(self._frame("received", nonce, offer_sha256=digest))
            )
            require(_sockets(channels) == identities)
            require(not select.select([self.channel], [], [], 0)[0])
            self._guard()
            return Receipt(self.context_sha256, digest)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def receive(self):
        acquired = False
        owned = []
        channels = None
        try:
            self._enter("receive")
            acquired = True
            require(not select.select([self.channel], [], [], 0)[0])
            nonce = secrets.token_hex(32)
            self._send(self._frame("request", nonce))
            offer, rights = self._receive(rights=True)
            owned.extend(rights)
            incoming = socket.socket(fileno=owned[0])
            owned.pop(0)
            try:
                outgoing = socket.socket(fileno=owned[0])
                owned.pop(0)
            except BaseException:
                incoming.close()
                raise
            channels = links.Channels(incoming, outgoing)
            # Do not repair flags/options: the received handles must already
            # be exactly the independently delivered original endpoints.
            for endpoint in (incoming, outgoing):
                require(fcntl.fcntl(endpoint.fileno(), fcntl.F_GETFL) & os.O_NONBLOCK)
                endpoint.setblocking(False)  # Match Python's wrapper to existing O_NONBLOCK.
            identities = _sockets(channels)
            require(
                links.base.encode(offer)
                == links.base.encode(
                    self._frame("endpoints", nonce, identities=[list(item) for item in identities])
                )
            )
            digest = links.base.checksum(offer)
            # Refuse already queued extra work/EOF BEFORE acknowledging. A
            # lost or later contradicted acknowledgment is still not a commit.
            require(not select.select([self.channel], [], [], 0)[0])
            require(_sockets(channels) == identities)
            self._guard()
            self._send(self._frame("received", nonce, offer_sha256=digest))
            require(not select.select([self.channel], [], [], 0)[0])
            require(_sockets(channels) == identities)
            self._guard()
            result, channels = channels, None
            return result, Receipt(self.context_sha256, digest)
        except BaseException as error:
            self._fail(error)
        finally:
            if channels is not None:
                channels.close()
            for fd in owned:
                os.close(fd)
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        self.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedBootstrap(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        problem = False
        for _path, fd, identity in reversed(getattr(self, "original_handles", tuple(self.handles))):
            try:
                require(links._identity(fd) == identity)
                os.close(fd)
            except Exception:
                problem = True
        require(not problem)


if __name__ == "__main__":
    raise SystemExit("Uninstalled peer descriptor bootstrap only; no active launcher enabled.")
