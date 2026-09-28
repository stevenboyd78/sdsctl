#!/usr/bin/env python3
"""Private, one-attempt CLI evidence acknowledgments, not action permission.

Two anonymous directional SEQPACKET pairs bind every message to the original
live peer's kernel credentials. Only a compact notice digest crosses the link;
the observer independently reads the retained journal and inspects Engine.
The joint read-only source inventory includes this library; no command selects
it. The qualified launcher must establish both peers, one writer, action consent and
independently bounded lifetimes. No preflight permission is consumed or reused.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import select
import socket
import struct
import time
from dataclasses import asdict
from decimal import ROUND_FLOOR, Decimal
from threading import Lock, get_ident

import supplemental_recording_control as control
import supplemental_recording_service_cli_custody as custody_module

plans, base = custody_module.plans, custody_module.base
processes, clock = custody_module.apps.processes, plans.clock
Channels = control.Channels
MAX_BYTES, MAX_EXCHANGES, EXCHANGE_SECONDS, ROOT_UID = 2048, 8, 2, 0
KIND = "finite-recording-cli-evidence-v1"
CANDIDATE_KIND = "finite-recording-candidate-custody-v1"
NATIVE_KIND = "finite-recording-native-custody-v1"
MESSAGE = "App command evidence exchange is unconfirmed; preserve the case and do not retry."


class UnconfirmedExchange(ValueError):
    """An absent acknowledgment never permits another create/start attempt."""


def require(value):
    if not value:
        raise UnconfirmedExchange(MESSAGE)


def pair():
    """Create private endpoints only, before the separately qualified launch.

    Pass only each peer's own two descriptors; close all unused copies. Only
    receive endpoints enable PASSCRED, avoiding Linux abstract auto-binding.
    The library does not spawn, listen, signal, or acquire a journal lock.
    """
    left, right = control.pair()
    try:
        for channel in (left, right):
            for endpoint in (channel.incoming, channel.outgoing):
                endpoint.setblocking(False)
        return left, right
    except BaseException:
        left.close()
        right.close()
        raise


def _identity(fd):
    value = os.fstat(fd)
    return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid


def _notice(notice):
    require(type(notice) is custody_module.platform.DispatchNotice)
    notice.__post_init__()
    # Validate the full immutable notice, including bounded history.
    return {
        "stage": notice.stage,
        "phase": notice.phase,
        "case_id": notice.case_id,
        "boot_id": notice.boot_id,
        "container_id": notice.container_id,
        "execution_id": notice.execution_id,
        "receipt": notice.receipt,
    }


def _decode(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    value = json.loads(raw, object_pairs_hook=base.unique, parse_constant=base.reject_constant)
    require(type(value) is dict and base.encode(value) == raw)
    return value


def _candidate_notice(notice):
    require(type(notice) is custody_module.platform.CandidateNotice)
    notice.__post_init__()
    return dict(
        plan_sha256=notice.plan_sha256,
        generation=notice.generation,
        process=asdict(notice.process),
        receipt=notice.receipt,
    )


def _hard_ns(plan):
    return int(
        (Decimal(plan.deadlines.recover_by) * clock.NS).to_integral_value(rounding=ROUND_FLOOR)
    )


class Link:
    """Borrow channels/clock, duplicate the ORIGINAL live peer pidfd once.

    This narrow initial implementation requires identical user/time namespaces
    for both peers, not merely coincident timestamps or inferred zero offsets.
    No process discovery or reconnection follows peer loss. A source-qualified
    launcher must retain one Link for each side of one case; constructing a new
    object is not permission to replay a lost transaction.

    Each call has one unchanged two-second budget AND the original plan cutoff.
    This bounds cooperative I/O, not a blocked observer: external execution
    supervision and exclusive recovery handoff remain mandatory.
    """

    def __init__(self, channel, plan, timer, peer, *, role):
        self.owner, self.lock = (os.getpid(), get_ident(), os.geteuid(), os.getegid()), Lock()
        self.closed = self.failed = False
        self._owned = self._original_owned = ()
        self._peer = None
        self._peer_handle = None
        self.sequence = 0
        self._sequence = 0
        self.candidate_attempted = self._candidate_attempted = False
        self.native_attempted = self._native_attempted = False
        try:
            require(type(self) is Link and self.owner[2] == ROOT_UID)
            require(role in ("writer", "observer") and type(role) is str)
            require(type(channel) is Channels and channel.incoming is not channel.outgoing)
            require(type(plan) is plans.Plan and type(timer) is clock.ClockWitness)
            # Accepted Startup retains its first ClockWitness while CasePlan
            # decodes the acknowledged bytes into an equal immutable Window.
            # Borrow that exact witness; do not construct or renew another one.
            require(plans._same_plan_value(timer.original, plan.original_clock))
            require(type(peer) is processes.ProcessWitness and peer.identity.pid != self.owner[0])
            self.channel, self.plan, self.timer, self.role = channel, plan, timer, role
            self.pin = plans.PinnedPlan(plan)
            self._clock_origin = timer.original
            self._originals = channel, plan, timer, role, self.pin, self._clock_origin
            self.plan_sha256 = plan.sha256
            self._peer = processes.ProcessWitness(peer.identity, retained_fd=peer.fd)
            self._peer_object = self._peer
            try:
                self.peer_fd_identity = _identity(self._peer.fd)
            except BaseException:
                self._peer.close()
                self._peer = self._peer_object = None
                raise
            self.peer_pin = base.encode(asdict(self._peer.identity))
            self.peer_fd = self._peer.fd
            self._peer_handle = self.peer_fd, self.peer_fd_identity
            self._sockets = tuple(
                (endpoint, endpoint.fileno(), _identity(endpoint.fileno()), passcred)
                for endpoint, passcred in ((channel.incoming, 1), (channel.outgoing, 0))
            )
            # Retain each namespace inode. Later checks cannot accept a reused
            # numeric namespace identity after a peer has left its domain.
            for pid in (self.owner[0], self._peer.identity.pid):
                for name in ("user", "time", "time_for_children"):
                    path = f"/proc/{pid}/ns/{name}"
                    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                    try:
                        identity = _identity(fd)
                    except BaseException:
                        os.close(fd)
                        raise
                    self._owned += ((path, fd, identity),)
                    self._original_owned = self._owned
            self._namespaces = self._owned
            end = time.monotonic() + EXCHANGE_SECONDS
            self._guard(end)
            require(time.clock_gettime_ns(time.CLOCK_BOOTTIME) / clock.NS < plan.deadlines.ready_by)
            self._pins = self._values()
        except BaseException as error:
            self.close()
            self._fail(error)

    def _values(self):
        return self.plan_sha256, self.peer_pin, self.peer_fd, self.peer_fd_identity, self._sockets

    def _guard(self, end):
        require(not self.closed and not self.failed and time.monotonic() < end)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(
            all(
                a is b
                for a, b in zip(
                    (self.channel, self.plan, self.timer, self.role, self.pin, self._clock_origin),
                    self._originals,
                    strict=True,
                )
            )
        )
        require(type(self.sequence) is int and self.sequence == self._sequence <= MAX_EXCHANGES)
        require(
            type(self.candidate_attempted) is bool
            and self.candidate_attempted is self._candidate_attempted
        )
        require(
            type(self.native_attempted) is bool and self.native_attempted is self._native_attempted
        )
        if hasattr(self, "_pins"):
            require(self._values() == self._pins)
        self.pin.check(self.plan)
        require(self.plan.sha256 == self.plan_sha256)
        require(self.timer.original is self._clock_origin)
        require(plans._same_plan_value(self._clock_origin, self.plan.original_clock))
        require(self._peer is self._peer_object and self._peer.fd == self.peer_fd)
        require(base.encode(asdict(self._peer.identity)) == self.peer_pin)
        require(
            _identity(self.peer_fd) == self.peer_fd_identity
            and not os.get_inheritable(self.peer_fd)
        )
        require(not self._peer.exited())
        processes.ProcessWitness._live_descriptor(self.peer_fd, self._peer.identity.pid)
        require(
            processes.read_identity(self._peer.identity.pid, self._peer.identity.container_id)
            == self._peer.identity
        )
        require(self._owned is self._original_owned and self._owned is self._namespaces)
        for path, fd, identity in self._namespaces:
            require(_identity(fd) == identity and not os.get_inheritable(fd))
            value = os.stat(path)
            require(
                (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid) == identity
            )
        ids = tuple(identity[:2] for _, _, identity in self._namespaces)
        require(
            ids[0] == ids[3]
            and ids[1] == ids[2] == ids[4] == ids[5] == self.plan.original_clock.namespace
        )
        require(
            tuple(item[0] for item in self._sockets)
            == (self.channel.incoming, self.channel.outgoing)
        )
        for endpoint, fd, identity, passcred in self._sockets:
            require(endpoint.fileno() == fd and _identity(fd) == identity)
            control.returns._socket(endpoint)
            require(not endpoint.get_inheritable() and endpoint.gettimeout() == 0.0)
            require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK)
            require(endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == passcred)
        self.plan.check_clock(self.timer.read())
        require(time.monotonic() < end)

    def _quiet(self):
        # Readability also includes peer EOF. Do not peek SCM_RIGHTS: a peek
        # with recvmsg can install caller-supplied descriptors repeatedly.
        require(not select.select([self.channel.incoming], [], [], 0)[0])

    def _wait(self, *, sending, end):
        self._guard(end)
        incoming = [self.peer_fd] if sending else [self.peer_fd, self.channel.incoming]
        outgoing = [self.channel.outgoing] if sending else []
        readable, writable, _ = select.select(
            incoming, outgoing, [], max(0, end - time.monotonic())
        )
        require(self.peer_fd not in readable)
        require(bool(writable) if sending else self.channel.incoming in readable)
        self._guard(end)

    def _send(self, value, end):
        raw = base.encode(value)
        require(0 < len(raw) <= MAX_BYTES)
        self._wait(sending=True, end=end)
        require(self.channel.outgoing.send(raw) == len(raw))
        self._guard(end)
        return raw

    def _receive(self, end):
        self._wait(sending=False, end=end)
        raw, ancillary, flags, _ = self.channel.incoming.recvmsg(
            MAX_BYTES, socket.CMSG_SPACE(control.returns.CREDENTIALS.size), socket.MSG_CMSG_CLOEXEC
        )
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                for (fd,) in struct.iter_unpack("i", data[: len(data) // 4 * 4]):
                    os.close(fd)
        require(flags & ~socket.MSG_CMSG_CLOEXEC == 0 and len(ancillary) == 1)
        level, kind, data = ancillary[0]
        require((level, kind) == (socket.SOL_SOCKET, socket.SCM_CREDENTIALS))
        require(len(data) == control.returns.CREDENTIALS.size)
        require(
            control.returns.CREDENTIALS.unpack(data) == (self._peer.identity.pid, *self.owner[2:])
        )
        value = _decode(raw)
        self._guard(end)
        self._quiet()
        return value, raw

    def _enter(self, role, *, candidate=False, native=False):
        require(self.lock.acquire(blocking=False))
        try:
            end = time.monotonic() + EXCHANGE_SECONDS
            self._guard(end)
            require(self.role == role and self.sequence < MAX_EXCHANGES)
            require(not (candidate and native))
            if native:
                require(
                    self.sequence == 4 and self.candidate_attempted and not self.native_attempted
                )
                self.native_attempted = self._native_attempted = True
            elif candidate:
                require(self.sequence == 4 and not self.candidate_attempted)
                self.candidate_attempted = self._candidate_attempted = True
            else:
                self.sequence += 1
                self._sequence = self.sequence
            return end
        except BaseException:
            self.lock.release()
            raise

    def observe(self, notice):
        """Original writer callback: request evidence, never issue any action."""
        return self._observe(notice, candidate=False)

    def observe_candidate(self, notice):
        """One separate pre-native exchange after the two initial CLI commands."""
        return self._observe(notice, candidate=True)

    def observe_native(self, notice):
        """One original-Ready comparison exchange, never a begin instruction."""
        return self._observe(notice, candidate=False, native=True)

    def _observe(self, notice, *, candidate, native=False):
        entered = False
        try:
            end = self._enter("writer", candidate=candidate, native=native)
            entered = True
            self._quiet()
            kind = NATIVE_KIND if native else CANDIDATE_KIND if candidate else KIND
            codec = (
                custody_module.native_fields
                if native
                else _candidate_notice
                if candidate
                else _notice
            )
            fields = codec(notice)
            if native:
                require(notice.pins.host.plan_sha256 == self.plan_sha256)
            elif candidate:
                require(notice.plan_sha256 == self.plan_sha256)
            else:
                require((notice.case_id, notice.boot_id) == (self.plan.case, self.plan.boot))
            issued = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            until = min(issued + EXCHANGE_SECONDS * clock.NS, _hard_ns(self.plan))
            if candidate or native:
                until = min(until, int(Decimal(self.plan.deadlines.ready_by) * clock.NS))
            require(issued < until)
            request = dict(
                schema=1,
                kind=kind,
                plan=self.plan_sha256,
                sequence=self.sequence,
                nonce=secrets.token_hex(32),
                notice=fields,
                issued_ns=issued,
                until_ns=until,
            )
            raw = self._send(request, end)
            reply, _ = self._receive(end)
            expected = dict(
                schema=1,
                kind=kind,
                plan=self.plan_sha256,
                sequence=self.sequence,
                nonce=request["nonce"],
                request_sha256=base.checksum(request),
                receipt=fields["receipt"],
            )
            require(base.encode(reply) == base.encode(expected))
            require(base.encode(request) == raw and codec(notice) == fields)
            self._guard(end)
            require(time.clock_gettime_ns(time.CLOCK_BOOTTIME) < until)
            return fields["receipt"]
        except BaseException as error:
            self._fail(error)
        finally:
            if entered:
                self.lock.release()

    def acknowledge(self, custody):
        """Observer: independently read/capture original evidence, then reply.

        Lost reply leaves the retained custody factual but the link unusable.
        This never appends a journal, transfers ownership or dispatches an exec.
        """
        return self._acknowledge(custody, candidate=False)

    def acknowledge_candidate(self, custody):
        """Capture candidate through independent original CLI/App custody.

        Uses a distinct message kind and one slot, not another CLI receipt or
        native Ready. Lost replies preserve captured handles but never retry.
        """
        return self._acknowledge(custody, candidate=True)

    def acknowledge_native(self, custody):
        """Capture original workers; the writer separately authenticates Ready."""
        return self._acknowledge(custody, candidate=False, native=True)

    def _acknowledge(self, custody, *, candidate, native=False):
        entered = False
        try:
            end = self._enter("observer", candidate=candidate, native=native)
            entered = True
            require(type(custody) is custody_module.CliCustody and custody.plan is self.plan)
            require(custody.custody.watch.target == self._peer.identity)
            request, _ = self._receive(end)
            kind = NATIVE_KIND if native else CANDIDATE_KIND if candidate else KIND
            plans.mapping(
                request,
                {"schema", "kind", "plan", "sequence", "nonce", "notice", "issued_ns", "until_ns"},
            )
            require(
                type(request["schema"]) is int
                and request["schema"] == 1
                and request["kind"] == kind
            )
            require(type(request["sequence"]) is int and request["sequence"] == self.sequence)
            require(request["plan"] == self.plan_sha256)
            base.digest(request["nonce"])
            issued, until = request["issued_ns"], request["until_ns"]
            require(
                type(issued) is int
                and type(until) is int
                and 0 < until - issued <= EXCHANGE_SECONDS * clock.NS
            )
            now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            require(issued <= now < until <= _hard_ns(self.plan))
            if candidate or native:
                require(until <= int(Decimal(self.plan.deadlines.ready_by) * clock.NS))
            end = min(end, time.monotonic() + (until - now) / clock.NS)
            keys = (
                custody_module.NATIVE_FIELDS
                if native
                else {"plan_sha256", "generation", "process", "receipt"}
                if candidate
                else {
                    "stage",
                    "phase",
                    "case_id",
                    "boot_id",
                    "container_id",
                    "execution_id",
                    "receipt",
                }
            )
            fields = plans.mapping(
                request["notice"],
                keys,
            )
            base.digest(fields["receipt"])
            # Never accept serialized journal bytes, paths, or actions from the
            # writer. These bytes come from the original independently held fd.
            status = custody._guard()
            require(
                not status.deadline.helper_exited and not status.deadline.recovery_deadline_expired
            )
            require(not custody.capture_failed and not custody.inspection_failed)
            if native:
                require(custody.observe_native(fields, deadline=end) == fields["receipt"])
            elif candidate:
                snapshot = custody._history.read(end)
                history = tuple(raw for _, raw in snapshot)
                process = plans.mapping(fields["process"], {"pid", "start_ticks", "container_id"})
                notice = custody_module.platform.CandidateNotice(
                    fields["plan_sha256"],
                    fields["generation"],
                    processes.ProcessIdentity(**process),
                    history,
                )
                require(_candidate_notice(notice) == fields)
                require(custody.observe_candidate(notice, deadline=end) == fields["receipt"])
            else:
                snapshot = custody._history.read(end)
                history = tuple(raw for _, raw in snapshot)
                notice = custody_module.platform.DispatchNotice(
                    **{key: value for key, value in fields.items() if key != "receipt"},
                    history=history,
                )
                require(_notice(notice) == fields)
                require(custody.observe(notice, deadline=end) == fields["receipt"])
            self._guard(end)
            require(time.clock_gettime_ns(time.CLOCK_BOOTTIME) < until)
            self._quiet()
            reply = dict(
                schema=1,
                kind=kind,
                plan=self.plan_sha256,
                sequence=self.sequence,
                nonce=request["nonce"],
                request_sha256=base.checksum(request),
                receipt=fields["receipt"],
            )
            self._send(reply, end)
            require(time.clock_gettime_ns(time.CLOCK_BOOTTIME) < until)
            return fields["receipt"]
        except BaseException as error:
            self._fail(error)
        finally:
            if entered:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedExchange(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        for _, fd, identity in reversed(self._original_owned):
            require(_identity(fd) == identity)
            os.close(fd)
        self._owned = self._original_owned = ()
        if self._peer_handle is not None:
            fd, identity = self._peer_handle
            require(_identity(fd) == identity)
            os.close(fd)
            self._peer_object.fd = -1
            self._peer_handle = None
