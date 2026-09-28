#!/usr/bin/env python3
"""Authenticate clock-free peer input pins before baseline collection, offline only.

The independently qualified original outer supplies the EXPECTED pin, never a
hash learned from observed peer files/argv. Original private connection/listener,
pidfd and zero-offset domain checks authenticate this exchange's local sender.
They do not authenticate the outer's installation or make it an App authority.
No existing source inventory or command selects this new preparation protocol.
The exchange begins no baseline or service. A separate prepare_writer adapter
requires the existing independently obtained one-use preflight permission before
joining retained inputs to original Startup. Neither path grants App actions.
"""

from __future__ import annotations

import os
import secrets
import select
import socket
import struct
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import get_ident

import supplemental_recording_peer_bootstrap as bootstrap
import supplemental_recording_peer_connection as connections
import supplemental_recording_peer_inputs as inputs_module
import supplemental_recording_peer_listener as listeners
import supplemental_recording_time_domain as domains

codec, links = inputs_module.codec, bootstrap.links
KIND = "finite-recording-peer-input-preparation-v1"
SCOPE = "authenticate-retained-peer-inputs-only-v1"
MAX_BYTES, ROOT_UID = 2048, 0
MESSAGE = "Recording peer preparation is unconfirmed; preserve this case and do not retry."


class UnconfirmedPreparation(ValueError):
    """Lost acknowledgment never authorizes replay or reveals private inputs."""


def require(value):
    if not value:
        raise UnconfirmedPreparation(MESSAGE)


def preparation_root(case, role):
    codec.plans.base.identifier(case, case=True)
    require(type(role) is str and role in codec.ROLES)
    return Path("/mnt/data/sdsctl-recording-preparation-" + case + "-" + role)


def baseline_root(case):
    codec.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-baseline-" + case)


def _cleanup(callbacks, problem):
    while callbacks:
        try:
            callbacks.pop()()
        except BaseException as error:
            if problem is None or not isinstance(error, Exception):
                problem = error
    if problem is not None:
        if not isinstance(problem, Exception):
            raise problem
        raise UnconfirmedPreparation(MESSAGE) from None


def _peer_guard(peer, pin):
    identity, fd, descriptor = pin
    require(peer.identity is identity and peer.fd == fd and not os.get_inheritable(fd))
    require(links._identity(fd) == descriptor and not peer.exited())
    domains.process.ProcessWitness._live_descriptor(fd, identity.pid)
    require(domains.process.read_identity(identity.pid, identity.container_id) == identity)


def _peer_close(peer, pin):
    # Retire only the captured descriptor, never a foreign replacement named by
    # a changed witness slot. An exited original still owns its retained pidfd.
    _, fd, descriptor = pin
    require(links._identity(fd) == descriptor)
    same = peer.fd == fd
    os.close(fd)
    if same:
        peer.fd = -1
    require(same)


def _same_frame(observed, expected):
    # Mapping equality would accept true/1 and 1.0/1 as the same protocol.
    require(links.base.encode(observed) == links.base.encode(expected))


class _Exchange:
    def __init__(self, declaration, channel_owner, timer, local, role, baseline_sha256, *, sending):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.domain = None
        try:
            require(type(self) is _Exchange and self.owner[2:] == (ROOT_UID, ROOT_UID))
            require(type(sending) is bool)
            require(
                type(channel_owner) is (listeners.Listener if sending else connections.Connection)
            )
            require(channel_owner.owner == self.owner)
            require(channel_owner.preparation_attempted is False)
            channel_owner.preparation_attempted = True  # Consumed even if validation below fails.
            require(type(declaration) is inputs_module.declarations.Declaration)
            require(type(timer) is domains.clock.ClockWitness)
            require(type(local) is domains.process.ProcessIdentity and local.pid == self.owner[0])
            require(type(role) is str and role in codec.ROLES)
            require(type(baseline_sha256) is str)
            codec.plans.base.digest(baseline_sha256)
            self.declaration, self.connection, self.timer, self.local = (
                declaration,
                channel_owner,
                timer,
                local,
            )
            self.role, self.baseline_sha256, self.sending = role, baseline_sha256, sending
            self.end = channel_owner.deadline
            require(time.monotonic() < self.end <= time.monotonic() + bootstrap.SECONDS)
            self.origin = timer.original
            self.template = declaration.recheck(deadline=self.end)
            template_value = codec.templates._read(self.template.raw)
            self.case = template_value["plan"]["case"]
            require(template_value["plan"]["boot"] == self.origin.boot)
            require(channel_owner.root == preparation_root(self.case, role))
            channel_owner.recheck()
            if sending:
                require(channel_owner.accepted is True)
            self.channel, self.remote = channel_owner.channel, channel_owner.peer
            self.remote_pin = self.remote.identity, self.remote.fd, links._identity(self.remote.fd)
            require(self.remote.identity.pid != local.pid)
            require(self.remote.identity.container_id != local.container_id)
            self.domain = domains.ZeroDomain(self.origin, self.remote)
            self.proof = self.domain.refresh()
            self.context = dict(
                schema=1,
                kind=KIND,
                scope=SCOPE,
                case=self.case,
                role=role,
                template_sha256=self.template.sha256,
                baseline_sha256=baseline_sha256,
                requester=asdict(self.remote.identity if sending else local),
                outer=asdict(local if sending else self.remote.identity),
            )
            self.context_raw = links.base.encode(self.context)
            self.values = role, baseline_sha256, sending, self.end, self.case
            self.originals = (
                declaration,
                channel_owner,
                timer,
                local,
                self.template,
                self.channel,
                self.remote,
                self.domain,
                self.origin,
                self.proof,
            )
            self.guard()
        except BaseException as error:
            _cleanup([self.close], error)

    def guard(self, *, retired=False):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(time.monotonic() < self.end and self.connection.deadline == self.end)
        require((self.role, self.baseline_sha256, self.sending, self.end, self.case) == self.values)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        self.declaration,
                        self.connection,
                        self.timer,
                        self.local,
                        self.template,
                        self.channel,
                        self.remote,
                        self.domain,
                        self.origin,
                        self.proof,
                    ),
                    self.originals,
                    strict=True,
                )
            )
        )
        require(self.connection.preparation_attempted is True)
        require(self.connection.channel is self.channel and self.connection.peer is self.remote)
        require(self.timer.original is self.origin)
        self.timer.read()
        require(self.declaration.recheck(deadline=self.end) is self.template)
        require(links.base.encode(self.context) == self.context_raw)
        require(self.context["template_sha256"] == self.template.sha256)
        self.connection.recheck()
        _peer_guard(self.remote, self.remote_pin)
        require(
            domains.process.read_identity(self.local.pid, self.local.container_id) == self.local
        )
        if not retired:
            require(self.domain.refresh() is self.proof)
        else:
            require(self.domain.closed)
        require(time.monotonic() < self.end)

    def frame(self, phase, nonce, **fields):
        return self.context | dict(phase=phase, nonce=nonce, **fields)

    def wait(self, *, sending=False):
        self.guard()
        readable, writable, _ = select.select(
            [self.remote.fd] + ([] if sending else [self.channel]),
            [self.channel] if sending else [],
            [],
            max(0, self.end - time.monotonic()),
        )
        require(self.remote.fd not in readable)
        require(bool(writable) if sending else self.channel in readable)
        self.guard()

    def send(self, value):
        raw = links.base.encode(value)
        require(len(raw) <= MAX_BYTES)
        self.wait(sending=True)
        require(self.channel.sendmsg([raw]) == len(raw))
        self.guard()
        return links.base.checksum(value)

    def receive(self):
        received, problem = [], None
        try:
            self.wait()
            raw, ancillary, flags, _ = self.channel.recvmsg(
                MAX_BYTES,
                socket.CMSG_SPACE(links.control.returns.CREDENTIALS.size) + socket.CMSG_SPACE(8),
                socket.MSG_CMSG_CLOEXEC,
            )
            credentials, extras = [], []
            for level, kind, data in ancillary:
                if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                    received.extend(
                        v[0] for v in struct.iter_unpack("i", data[: len(data) // 4 * 4])
                    )
                    extras.append(kind)
                elif (level, kind) == (socket.SOL_SOCKET, socket.SCM_CREDENTIALS):
                    credentials.append(data)
                else:
                    extras.append(kind)
            require(flags & ~socket.MSG_CMSG_CLOEXEC == 0 and not extras)
            require(
                credentials
                == [
                    links.control.returns.CREDENTIALS.pack(
                        self.remote.identity.pid, *self.owner[2:]
                    )
                ]
            )
            value = links._decode(raw)
            self.guard()
            return value
        except BaseException as error:
            problem = error
        finally:
            # Rights are forbidden in preparation, including truncated input.
            # These are freshly received descriptors, never caller-owned ones.
            _cleanup([lambda fd=fd: os.close(fd) for fd in received], problem)

    def quiet(self):
        self.guard()
        require(not select.select([self.channel], [], [], 0)[0])

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.domain is not None and not self.domain.closed:
            self.domain.close()


def send_inputs(inputs, listener, timer, local, *, role, baseline_sha256, counterpart):
    """Original outer sends its independently pinned input digest once.

    Caller must qualify BOTH original runtimes and its own installation/platform
    before selecting this operation. This is not runtime qualification, baseline
    permission, Ready, action scope, or a grant to stop peers/recover an App.
    One original listener cutoff covers every input read, exchange and cleanup.
    All supplied owners remain caller-owned; no file, clock or process is made.
    """
    exchange, problem, result = None, None, None
    try:
        require(type(inputs) is inputs_module.Inputs)
        require(type(counterpart) is domains.process.ProcessWitness)
        exchange = _Exchange(
            inputs.declaration, listener, timer, local, role, baseline_sha256, sending=True
        )
        declaration = inputs.recheck(deadline=exchange.end)
        codec.source_profile(declaration, peer_handoff=True)
        expected = inputs.expected
        peer_pin = counterpart.identity, counterpart.fd, links._identity(counterpart.fd)
        require(len({local.pid, listener.peer.identity.pid, counterpart.identity.pid}) == 3)
        require(
            len(
                {
                    local.container_id,
                    listener.peer.identity.container_id,
                    counterpart.identity.container_id,
                }
            )
            == 3
        )

        def guard():
            exchange.guard()
            require(inputs.recheck(deadline=exchange.end) is declaration)
            require(inputs.expected == expected)
            _peer_guard(counterpart, peer_pin)
            exchange.guard()

        guard()
        request = exchange.receive()
        nonce = request.get("nonce")
        require(type(nonce) is str)
        codec.plans.base.digest(nonce)
        _same_frame(request, exchange.frame("request", nonce))
        guard()
        offer = exchange.frame(
            "inputs", nonce, expectations_sha256=expected, counterpart=asdict(counterpart.identity)
        )
        digest = exchange.send(offer)
        reply = exchange.receive()
        _same_frame(reply, exchange.frame("retained", nonce, offer_sha256=digest))
        guard()
        exchange.quiet()
        exchange.close()
        # No stale success after owned namespace retirement.
        exchange.guard(retired=True)
        require(inputs.recheck(deadline=exchange.end) is declaration)
        _peer_guard(counterpart, peer_pin)
        exchange.guard(retired=True)
        result = digest
    except BaseException as error:
        problem = error
    finally:
        _cleanup([exchange.close] if exchange is not None else [], problem)
    return result


@contextmanager
def receive_inputs(declaration, connection, timer, local, *, role, baseline_sha256):
    """Retain authentic original inputs and counterpart for the caller's lifetime.

    The expected digest is received only from the original authenticated outer,
    not supplied in this receiver's argv or learned by hashing a local file.
    The new Inputs and counterpart witness are owned HERE and borrowed by the
    caller until context exit; preserve them through the later original Startup
    and final handoff instead of reconstructing either from equal serialized data.

    Yield is not baseline/App/recording permission or sender completion. Separate
    final acceptance, runtime/outer qualification, action and recovery boundaries
    remain mandatory. The completed preparation socket cannot be reused, renewed
    or stretched across later phases. No final writer clock is created here.
    """
    cleanup, problem = [], None
    try:
        exchange = _Exchange(
            declaration, connection, timer, local, role, baseline_sha256, sending=False
        )
        cleanup.append(exchange.close)
        exchange.quiet()
        nonce = secrets.token_hex(32)
        exchange.send(exchange.frame("request", nonce))
        offer = exchange.receive()
        expected, value = offer.get("expectations_sha256"), offer.get("counterpart")
        require(type(expected) is str)
        codec.plans.base.digest(expected)
        codec.plans.mapping(value, {"pid", "start_ticks", "container_id"})
        other = domains.process.ProcessIdentity(**value)
        require(len({local.pid, connection.peer.identity.pid, other.pid}) == 3)
        require(
            len({local.container_id, connection.peer.identity.container_id, other.container_id})
            == 3
        )
        _same_frame(
            offer,
            exchange.frame(
                "inputs", nonce, expectations_sha256=expected, counterpart=asdict(other)
            ),
        )
        inputs = inputs_module.Inputs(
            declaration, inputs_module.inputs_root(exchange.case), expected, deadline=exchange.end
        )
        cleanup.append(inputs.close)
        codec.source_profile(inputs.expectations, peer_handoff=True)
        counterpart = domains.process.ProcessWitness(other)
        pin = counterpart.identity, counterpart.fd, links._identity(counterpart.fd)
        cleanup.append(lambda: _peer_close(counterpart, pin))
        exchange.guard()
        _peer_guard(counterpart, pin)
        require(inputs.recheck(deadline=exchange.end) is inputs.expectations)
        exchange.send(exchange.frame("retained", nonce, offer_sha256=links.base.checksum(offer)))
        exchange.quiet()
        _peer_guard(counterpart, pin)
        require(inputs.recheck(deadline=exchange.end) is inputs.expectations)
        exchange.guard()
        yield inputs, counterpart
    except BaseException as error:
        problem = error
    finally:
        _cleanup(cleanup, problem)


def prepare_writer(
    inputs,
    owner,
    permission,
    counterpart,
    directory,
    docker,
    *,
    original_timer,
    original_outer,
    original_connection,
):
    """Join retained inputs to ONE independently admitted original baseline read.

    Input authentication/acknowledgment is NOT this permission. The caller must
    obtain the existing exact preflight Permission separately from the original
    qualified outer and retain the SAME preparation clock and outer/counterpart
    witnesses. Its unchanged kind/scope admits ONLY the existing original
    manifest/host read and post-read Startup clock/publication, not an App action.

    Original inputs and both peers are freshly checked throughout that one
    consume scope, with the same original two-second-or-earlier cutoff. No new
    clock renews permission; no final acceptance, handoff or service runs here.
    Return is the original UNACCEPTED startup input, not Ready or command admission.
    Complete/partial files survive failure and both original owners are poisoned.
    This module remains outside every command-selected source inventory.
    """
    import supplemental_recording_permission_probe as preflight_channel
    import supplemental_recording_service_permission as preflight
    import supplemental_recording_service_startup as startups

    try:
        require(type(inputs) is inputs_module.Inputs and type(owner) is startups.Startup)
        require(type(permission) is preflight.Permission)
        require(type(counterpart) is domains.process.ProcessWitness)
        require(type(original_outer) is domains.process.ProcessWitness)
        require(type(original_timer) is domains.clock.ClockWitness)
        require(type(original_connection) is preflight_channel.PeerConnection)
        require(permission.timer is original_timer and permission.observer is original_outer)
        template, declaration = inputs.template, inputs.declaration
        require(owner.declaration is declaration and owner.template is template)
        require(permission.template is template and permission.template_sha256 == owner.expected)
        require(owner.clock is None and not owner.used and not owner.accepted)
        case = codec.templates._read(template.raw)["plan"]["case"]
        require(directory == baseline_root(case))
        require(type(directory) is type(Path()))
        require(original_connection.root == preflight_channel.peer_root(case))
        require(original_connection.channel is permission.channel)
        require(original_connection.deadline == permission.deadline)
        expected = inputs.expectations
        codec.source_profile(expected, peer_handoff=True)
        target = permission.target
        require(len({target.pid, original_outer.identity.pid, counterpart.identity.pid}) == 3)
        require(
            len(
                {
                    target.container_id,
                    original_outer.identity.container_id,
                    counterpart.identity.container_id,
                }
            )
            == 3
        )
        peers = [
            (peer, (peer.identity, peer.fd, links._identity(peer.fd)))
            for peer in (original_outer, counterpart)
        ]
        origin, baseline_pin = original_timer.original, permission.baseline_sha256
        with permission.consume():
            end = permission.consume_end

            def guard(*, retired=False):
                require(time.monotonic() < end and permission.consume_end == end)
                require(permission.timer is original_timer and original_timer.original is origin)
                require(permission.observer is original_outer and permission.target is target)
                require(original_connection.channel is permission.channel)
                require(original_connection.deadline == permission.deadline)
                original_connection.recheck()
                require(
                    permission.template is template and permission.baseline_sha256 == baseline_pin
                )
                if retired:
                    require(permission.used and permission.approved and not permission.active)
                    permission._check(end)
                    permission._quiet()
                else:
                    permission.guard()
                require(inputs.declaration is declaration and owner.declaration is declaration)
                require(inputs.template is template and owner.template is template)
                require(
                    inputs.expectations is expected and inputs.recheck(deadline=end) is expected
                )
                for peer, pin in peers:
                    _peer_guard(peer, pin)
                original_connection.recheck()
                if retired:
                    permission._check(end)
                else:
                    permission.guard()

            guard()
            original = owner._prepare(
                (None, docker, (directory, baseline_pin)), preflight_guard=guard
            )
            guard()
        # Retiring the permission scope cannot hide original input/peer drift.
        # This final read grants no new scope and stays inside its original end.
        guard(retired=True)
        require(owner.original is original and not owner.accepted)
        owner._guard()
        require(
            owner.clock is not original_timer and owner.clock.original.before_ns >= origin.after_ns
        )
        guard(retired=True)
        return original
    except BaseException as error:
        if type(permission) is preflight.Permission:
            permission.failed = True
        if type(owner) is startups.Startup:
            try:
                owner._fail(error)
            except BaseException as failure:
                error = failure
        _cleanup([], error)


if __name__ == "__main__":
    raise SystemExit("Uninstalled peer input preparation only; no active launch enabled.")
