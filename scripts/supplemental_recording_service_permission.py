#!/usr/bin/env python3
"""One-use preflight permission over an already authenticated local peer channel.

Uninstalled library only. Construction/wait create no connection, listener,
file, service, plan, clock, or App operation. An explicit prepare_service()
adapter can consume permission for the existing baseline/startup library join.
The trusted launcher supplies the exact original observer pidfd/domain, target
identity and reviewed input pins. Socket peer credentials bind the response to
that observer's endpoint, not any same-UID writer.
Source/runtime qualification of BOTH peers and channel setup remain external.
No existing command or helper inventory selects this new library.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import select
import socket
import stat
import struct
import time
from contextlib import contextmanager
from dataclasses import asdict
from threading import Lock, get_ident

import supplemental_recording_service_template as templates
import supplemental_recording_time_domain as domains

plans, clock = templates.plans, domains.clock
ROOT_UID = 0
WAIT_SECONDS, IO_SECONDS = 15, 2
MAX_BYTES = 2048
CHALLENGE_KIND = "finite-recording-preflight-challenge-v1"
PERMISSION_KIND = "finite-recording-preflight-permission-v1"
MESSAGE = "Recording preflight permission is unconfirmed; preserve the case and do not retry."


class UnconfirmedPermission(ValueError):
    """An uncertain reply cannot authorize another attempt or disclose input."""


def require(value):
    if not value:
        raise UnconfirmedPermission(MESSAGE)


def permission_bytes(challenge_sha256):
    """Canonical response codec only, NEVER proof of review or peer identity."""
    try:
        require(type(challenge_sha256) is str)
        plans.base.digest(challenge_sha256)
        return (
            plans.base.encode(
                {"schema": 1, "kind": PERMISSION_KIND, "challenge_sha256": challenge_sha256}
            )
            + b"\n"
        )
    except Exception:
        raise UnconfirmedPermission(MESSAGE) from None


class _Peer:
    """Private symmetric live-peer binding, without protocol or authority.

    target is this local process; observer is its remote peer. The observer-side
    sender reverses those argument roles explicitly. Each endpoint owns its OWN
    preflight clock/domain proof and borrows an already authenticated socket.
    """

    def __init__(
        self,
        template,
        template_sha256,
        baseline_sha256,
        target,
        observer,
        domain,
        timer,
        channel,
        *,
        incoming=False,
    ):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock = Lock()
        self.failed = self.closed = self.requested = self.approved = self.used = self.active = False
        self.consume_end = self._consume_end = None
        try:
            require(self.owner[2] == ROOT_UID)
            require(type(template) is templates.Template)
            for value in (template_sha256, baseline_sha256):
                require(type(value) is str)
                plans.base.digest(value)
            require(template.sha256 == template_sha256)
            require(type(target) is domains.process.ProcessIdentity and target.pid == self.owner[0])
            require(type(observer) is domains.process.ProcessWitness)
            require(observer.identity.pid != self.owner[0])
            require(type(domain) is domains.ZeroDomain and type(timer) is clock.ClockWitness)
            require(type(channel) is socket.socket)
            self.template, self.target, self.observer = template, target, observer
            self.domain, self.timer, self.channel = domain, timer, channel
            self.objects = template, target, observer, domain, timer, channel
            self.template_raw, self.origin = template.raw, timer.original
            require(templates._read(self.template_raw)["plan"]["boot"] == self.origin.boot)
            self.target_pin = plans.base.encode(asdict(target))
            self.template_sha256, self.baseline_sha256 = template_sha256, baseline_sha256
            self.fd, self.peer_fd = channel.fileno(), observer.fd
            self.socket_identity = self._fd_identity(self.fd)
            self.peer_identity = self._fd_identity(self.peer_fd)
            self.peer = observer.identity
            proof = domain.refresh()
            require(proof.init == self.peer and proof.original_clock == self.origin)
            self.proof = proof
            self.domain_sha256 = proof.sha256
            self.deadline = self.origin.after_ns / clock.NS + WAIT_SECONDS
            self.wait_by = self.deadline - IO_SECONDS
            self.values = self._values()
            self._check(self.wait_by)
            if not incoming:
                self._quiet()
        except BaseException as error:
            self._fail(error)

    @staticmethod
    def _fd_identity(fd):
        info = os.fstat(fd)
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid

    def _values(self):
        return (
            self.template_raw,
            self.origin,
            self.target_pin,
            self.template_sha256,
            self.baseline_sha256,
            self.fd,
            self.peer_fd,
            self.socket_identity,
            self.peer_identity,
            self.peer,
            self.domain_sha256,
            self.deadline,
            self.wait_by,
        )

    def _binding(self, end):
        require(not self.failed and not self.closed and time.monotonic() < end <= self.deadline)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        self.template,
                        self.target,
                        self.observer,
                        self.domain,
                        self.timer,
                        self.channel,
                    ),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self._values() == self.values)
        require(plans.base.encode(asdict(self.target)) == self.target_pin)
        require(self.template.raw == self.template_raw and self.timer.original is self.origin)
        require(not self.timer.closed and not self.timer.failed)
        require(not self.domain.closed and not self.domain.failed)
        require(self.domain.evidence is self.proof and type(self.proof) is domains.Evidence)
        require(self.proof.sha256 == self.domain_sha256)
        require(self.proof.init == self.peer and self.proof.original_clock == self.origin)
        require(self.observer.identity == self.peer and self.observer.fd == self.peer_fd)
        require(
            self._fd_identity(self.peer_fd) == self.peer_identity and not self.observer.exited()
        )
        require(
            self.channel.fileno() == self.fd and self._fd_identity(self.fd) == self.socket_identity
        )
        require(stat.S_ISSOCK(self.socket_identity[2]))
        require(self.channel.family == socket.AF_UNIX and self.channel.type == socket.SOCK_STREAM)
        require(self.channel.gettimeout() == 0.0 and not os.get_inheritable(self.fd))
        require(fcntl.fcntl(self.fd, fcntl.F_GETFL) & os.O_NONBLOCK)
        require(self.channel.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0)
        self.channel.getpeername()  # Reject an unconnected socket, not just declared family/type.
        credentials = self.channel.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        require(struct.unpack("3i", credentials) == (self.peer.pid, *self.owner[2:]))
        require(
            domains.process.read_identity(self.target.pid, self.target.container_id) == self.target
        )
        require(time.monotonic() < end)

    def _check(self, end):
        self._binding(end)
        require(self.domain.refresh().sha256 == self.domain_sha256)
        self.timer.read()
        self._binding(end)  # No final-read retirement can return success.

    def _quiet(self):
        try:
            self.channel.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            return
        require(False)  # Includes peer EOF and any extra/replayed protocol bytes.

    def _poll(self, event, end):
        self._check(end)
        poller = select.poll()
        poller.register(self.fd, event | select.POLLERR | select.POLLHUP)
        poller.poll(max(1, min(50, int((end - time.monotonic()) * 1000))))
        self._check(end)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedPermission(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        self.closed = True
        if self.active:
            self.failed = True


class Permission(_Peer):
    """Borrow one original channel/clock/process/domain; grant one short scope.

    The caller must already trust the independently qualified original observer.
    This is not discovery, source qualification, or final-plan acceptance.
    SO_PEERCRED checks endpoint creation-time credentials, not current fd
    possession after delegation. The trusted peer must not delegate its endpoint.

    wait() sends one fresh nonce-bound challenge and receives one exact response.
    It reserves the original window's final two seconds for consume(). That
    context grants at most two seconds, never beyond the original clock cutoff.
    guard() is valid only inside that single scope. No subsequent service lease
    is created, and refusal/interruption leaves borrowed handles caller-owned.
    """

    def __init__(
        self, template, template_sha256, baseline_sha256, target, observer, domain, timer, channel
    ):
        # Unlike the opposite endpoint, a receiver must reject any unsolicited
        # bytes before generating its challenge. Callers cannot relax this.
        super().__init__(
            template, template_sha256, baseline_sha256, target, observer, domain, timer, channel
        )

    def wait(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.requested)
            self.requested = True
            self._check(self.wait_by)
            self._quiet()
            value = dict(
                schema=1,
                kind=CHALLENGE_KIND,
                case=templates._read(self.template_raw)["plan"]["case"],
                template_sha256=self.template_sha256,
                baseline_sha256=self.baseline_sha256,
                target=asdict(self.target),
                observer=asdict(self.peer),
                original_clock=asdict(self.origin),
                domain_sha256=self.domain_sha256,
                wait_by=self.wait_by,
                deadline=self.deadline,
                nonce=secrets.token_hex(32),
            )
            raw = plans.base.encode(value)
            require(len(raw) < MAX_BYTES)
            self.challenge_sha256 = hashlib.sha256(raw).hexdigest()
            expected = permission_bytes(self.challenge_sha256)
            data, sent = raw + b"\n", 0
            end = min(self.wait_by, time.monotonic() + IO_SECONDS)
            while sent < len(data):
                self._check(end)
                try:
                    count = self.channel.send(
                        data[sent:], socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL
                    )
                    require(count > 0)
                    sent += count
                except BlockingIOError:
                    self._poll(select.POLLOUT, end)
                self._check(end)
            received = bytearray()
            while b"\n" not in received:
                self._check(self.wait_by)
                try:
                    chunk = self.channel.recv(MAX_BYTES + 1 - len(received), socket.MSG_DONTWAIT)
                    require(bool(chunk))
                    received.extend(chunk)
                    require(len(received) <= MAX_BYTES)
                except BlockingIOError:
                    self._poll(select.POLLIN, self.wait_by)
                self._check(self.wait_by)
            require(bytes(received) == expected)
            self._quiet()
            self._check(self.wait_by)
            self.approved = True
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def guard(self):
        try:
            require(self.requested and self.approved and self.used and self.active)
            require(self.consume_end == self._consume_end and self.consume_end is not None)
            self._check(self.consume_end)
            self._quiet()
            self._binding(self.consume_end)
        except BaseException as error:
            self._fail(error)

    def prepare_service(self, owner, directory, docker):
        """Consume this permission for one original baseline/startup join.

        No wait/retry, input recapture, final acceptance or service run occurs
        here. The independently pinned baseline is loaded once, the existing
        complete host observation still runs, and only THEN is the continuing
        service clock captured. It is never this borrowed preflight clock.
        A late/lost result poisons both owners and preserves any partial case.
        A future command/source profile must explicitly qualify this adapter;
        --startup-probe and the existing source inventory do not select it.
        """
        import supplemental_recording_service_startup as startup

        try:
            require(type(owner) is startup.Startup)
            with self.consume() as guard:
                owner._input()
                require(owner.template is self.template and owner.expected == self.template_sha256)
                return owner._prepare(
                    (None, docker, (directory, self.baseline_sha256)), preflight_guard=guard
                )
        except BaseException as error:
            # Startup owns its acquired descriptors; Permission owns none of
            # its borrowed descriptors. Preserve interrupts across both cleanups.
            if type(owner) is startup.Startup:
                try:
                    owner._fail(error)
                except BaseException as cleanup:
                    error = cleanup
            self._fail(error)

    @contextmanager
    def consume(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.requested and self.approved and not self.used)
            self.used = self.active = True
            self.consume_end = self._consume_end = min(self.deadline, time.monotonic() + IO_SECONDS)
            self.guard()
            yield self.guard
            self.guard()
        except BaseException as error:
            self._fail(error)
        finally:
            self.active = False
            if acquired:
                self.lock.release()


if __name__ == "__main__":
    raise SystemExit("Uninstalled preflight permission receiver; no service or host read enabled.")
