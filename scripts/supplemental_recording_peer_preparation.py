#!/usr/bin/env python3
"""Authenticate original peer inputs and prepare one passive writer, uninstalled.

The independently qualified original outer supplies the EXPECTED pin, never a
hash learned from observed peer files/argv. Original private connection/listener,
pidfd and zero-offset domain checks authenticate this exchange's local sender.
They do not authenticate the outer's installation or make it an App authority.
The separate preparation profile covers the fixed passive command; older
inventories/commands do not select it. The exchange begins no baseline or service.
A separate prepare_writer adapter
requires the existing independently obtained one-use preflight permission before
joining retained inputs to original Startup. Neither path grants App actions.
"""

from __future__ import annotations

import os
import secrets
import select
import socket
import struct
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import get_ident

ENTRYPOINT = "/opt/sdsctl-recording-host/supplemental_recording_peer_preparation.py"
MODE = "--prepare-idle-peer-writer"
MESSAGE = "Recording peer preparation is unconfirmed; preserve this case and do not retry."

if __name__ == "__main__":
    try:
        allowed = (
            len(sys.argv) == 6
            and sys.argv[5] == MODE
            and sys.flags.isolated == sys.flags.dont_write_bytecode == 1
            and os.geteuid() == os.getegid() == 0
            and os.getcwd() == "/"
            and Path(__file__) == Path(ENTRYPOINT)
        )
    except Exception:
        allowed = False
    if not allowed:
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(64)
    sys.path.insert(0, "/opt/sdsctl-recording-host")

try:
    import supplemental_recording_peer_bootstrap as bootstrap
    import supplemental_recording_peer_connection as connections
    import supplemental_recording_peer_inputs as inputs_module
    import supplemental_recording_peer_listener as listeners
    import supplemental_recording_permission_probe as preflight_channel
    import supplemental_recording_service_permission as preflight
    import supplemental_recording_service_startup as startups
    import supplemental_recording_time_domain as domains
except Exception:
    if __name__ == "__main__":
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise

codec, links = inputs_module.codec, bootstrap.links
KIND = "finite-recording-peer-input-preparation-v1"
SCOPE = "authenticate-retained-peer-inputs-only-v1"
PLAN_KIND = "finite-recording-observer-plan-delivery-v1"
PLAN_SCOPE = "retain-original-writer-plan-only-v1"
MAX_BYTES, ROOT_UID = 2048, 0
MILESTONE = "Finite peer writer prepared and retired; no App or recording action selected."


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


def observer_plan_root(case):
    codec.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-observer-plan-" + case)


def writer_case_root(case):
    codec.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-handoff-" + case)


def handoff_root(case):
    codec.plans.base.identifier(case, case=True)
    return Path("/mnt/data/sdsctl-recording-peer-handoff-" + case + "-writer")


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
    def __init__(
        self,
        declaration,
        channel_owner,
        timer,
        local,
        role,
        baseline_sha256,
        *,
        sending,
        final_plan=False,
    ):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.domain = None
        try:
            require(type(self) is _Exchange and self.owner[2:] == (ROOT_UID, ROOT_UID))
            require(type(sending) is bool)
            require(type(final_plan) is bool and (not final_plan or role == "observer"))
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
            self.final_plan = final_plan
            self.end = channel_owner.deadline
            require(time.monotonic() < self.end <= time.monotonic() + bootstrap.SECONDS)
            self.origin = timer.original
            self.template = declaration.recheck(deadline=self.end)
            template_value = codec.templates._read(self.template.raw)
            self.case = template_value["plan"]["case"]
            require(template_value["plan"]["boot"] == self.origin.boot)
            require(
                channel_owner.root
                == (
                    observer_plan_root(self.case)
                    if final_plan
                    else preparation_root(self.case, role)
                )
            )
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
                kind=PLAN_KIND if final_plan else KIND,
                scope=PLAN_SCOPE if final_plan else SCOPE,
                case=self.case,
                role=role,
                template_sha256=self.template.sha256,
                baseline_sha256=baseline_sha256,
                requester=asdict(self.remote.identity if sending else local),
                outer=asdict(local if sending else self.remote.identity),
            )
            self.context_raw = links.base.encode(self.context)
            self.values = role, baseline_sha256, sending, self.end, self.case, final_plan
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
        require(
            (self.role, self.baseline_sha256, self.sending, self.end, self.case, self.final_plan)
            == self.values
        )
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


def send_inputs(
    inputs, listener, timer, local, *, role, baseline_sha256, counterpart, preparation=False
):
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
        codec.source_profile(declaration, peer_handoff=not preparation, preparation=preparation)
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
    if role == "observer" and preparation:
        # Retain continuity only AFTER the sender completed all retirement
        # checks. An ack or a failed send must not create this later binding.
        exchange.guard(retired=True)
        inputs._preparation_context = (
            inputs.declaration,
            timer,
            timer.original,
            local,
            listener.peer,
            counterpart,
            baseline_sha256,
            role,
            preparation,
        )
    return result


@contextmanager
def receive_inputs(
    declaration, connection, timer, local, *, role, baseline_sha256, preparation=False
):
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
        codec.source_profile(
            inputs.expectations, peer_handoff=not preparation, preparation=preparation
        )
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
        # Retain the actual local receive owners for a separately admitted
        # later plan exchange. This receipt is continuity, not action authority.
        inputs._preparation_context = (
            declaration,
            timer,
            timer.original,
            local,
            connection.peer,
            counterpart,
            baseline_sha256,
            role,
            preparation,
        )
        yield inputs, counterpart
    except BaseException as error:
        problem = error
    finally:
        _cleanup(cleanup, problem)


def _plan_input_guard(exchange, inputs, counterpart):
    """Borrow the original inputs and writer; never manufacture their provenance."""
    require(type(inputs) is inputs_module.Inputs)
    require(type(counterpart) is domains.process.ProcessWitness)
    expected = inputs.recheck(deadline=exchange.end)
    codec.source_profile(expected, preparation=True)
    require(inputs.declaration is exchange.declaration)
    pin = counterpart.identity, counterpart.fd, links._identity(counterpart.fd)
    identities = exchange.local, exchange.remote.identity, counterpart.identity
    require(len({item.pid for item in identities}) == 3)
    require(len({item.container_id for item in identities}) == 3)
    digest = inputs.expected
    received = getattr(inputs, "_preparation_context", None)
    require(type(received) is tuple and len(received) == 9)

    def guard(*, retired=False):
        exchange.guard(retired=retired)
        require(inputs.recheck(deadline=exchange.end) is expected and inputs.expected == digest)
        require(inputs._preparation_context is received)
        objects = (
            inputs.declaration,
            exchange.timer,
            exchange.origin,
            exchange.local,
            exchange.remote,
            counterpart,
        )
        require(all(a is b for a, b in zip(objects, received[:6], strict=True)))
        require(received[6:] == (exchange.baseline_sha256, "observer", True))
        _peer_guard(counterpart, pin)
        exchange.guard(retired=retired)

    guard()
    return guard


def _plan_file_guard(exchange, inputs, counterpart, original):
    intake = startups.acceptance.intake
    require(type(original) is intake.CasePlan)
    plan = original.recheck()
    require(plan.root == writer_case_root(exchange.case))
    inputs.expectations.check_plan(inputs.template, plan, plan.original_clock)
    # Structural comparison uses the writer's reported Window, never creates
    # another ClockWitness from it. Executing-writer provenance remains external.
    require(plan.original_clock.before_ns >= exchange.origin.after_ns)
    domain = domains.ZeroDomain(exchange.origin, counterpart)
    try:
        proof = domain.refresh()

        def guard():
            require(time.monotonic() < min(exchange.end, plan.lease["ready_by"]))
            require(domain.refresh() is proof)
            require(proof.original_clock is exchange.origin)
            require(proof.native_time == plan.original_clock.namespace)
            require(original.recheck() is plan)
            inputs.expectations.check_plan(inputs.template, plan, plan.original_clock)
            plan.check_clock(exchange.timer.read())
            require(time.monotonic() < min(exchange.end, plan.lease["ready_by"]))

        guard()
        return plan, domain, guard
    except BaseException:
        domain.close()
        raise


def send_observer_plan(inputs, original, listener, timer, local, *, baseline_sha256, counterpart):
    """Deliver an independently reviewed original writer plan pin, once.

    The qualified outer retains its CasePlan and original process/input owners.
    This is a NEW fixed private channel and original two-second bound, not reuse
    of the completed input exchange. Source/publication provenance, full final
    runtime checks, acceptance, action grants and independent termination remain
    caller prerequisites/separate phases. This sends no plan bytes or rights.
    """
    cleanup, problem, result = [], None, None
    try:
        require(type(inputs) is inputs_module.Inputs)
        exchange = _Exchange(
            inputs.declaration,
            listener,
            timer,
            local,
            "observer",
            baseline_sha256,
            sending=True,
            final_plan=True,
        )
        cleanup.append(exchange.close)
        guard = _plan_input_guard(exchange, inputs, counterpart)
        plan, domain, file_guard = _plan_file_guard(exchange, inputs, counterpart, original)
        cleanup.append(domain.close)
        fields = dict(expectations_sha256=inputs.expected, writer=asdict(counterpart.identity))
        request = exchange.receive()
        nonce = request.get("nonce")
        require(type(nonce) is str)
        codec.plans.base.digest(nonce)
        _same_frame(request, exchange.frame("request", nonce, **fields))
        guard()
        file_guard()
        result = exchange.send(exchange.frame("plan", nonce, plan_sha256=plan.sha256, **fields))
        reply = exchange.receive()
        _same_frame(reply, exchange.frame("retained", nonce, offer_sha256=result, **fields))
        guard()
        file_guard()
        exchange.quiet()
        exchange.close()
        guard(retired=True)
        file_guard()
        domain.close()
        guard(retired=True)
        require(original.recheck() is plan)
        plan.check_clock(timer.read())
        guard(retired=True)
    except BaseException as error:
        problem = error
    finally:
        _cleanup(cleanup, problem)
    return result


@contextmanager
def receive_observer_plan(inputs, connection, timer, local, *, baseline_sha256, counterpart):
    """Retain one writer CasePlan from the SAME original outer/input/peer owners.

    The pin is received over authenticated private transport, never learned by
    hashing disk or supplied as a future-plan startup argument. The caller keeps
    the original input/counterpart context alive around this one. Yield borrows
    this context's CasePlan; it is not sender completion, acceptance, Ready or
    action scope. Later phases must independently recheck all original owners.
    No Startup, writer clock, baseline read, dispatcher or App action is created.
    """
    cleanup, problem = [], None
    try:
        require(type(inputs) is inputs_module.Inputs)
        exchange = _Exchange(
            inputs.declaration,
            connection,
            timer,
            local,
            "observer",
            baseline_sha256,
            sending=False,
            final_plan=True,
        )
        cleanup.append(exchange.close)
        guard = _plan_input_guard(exchange, inputs, counterpart)
        fields = dict(expectations_sha256=inputs.expected, writer=asdict(counterpart.identity))
        exchange.quiet()
        nonce = secrets.token_hex(32)
        exchange.send(exchange.frame("request", nonce, **fields))
        offer = exchange.receive()
        digest = offer.get("plan_sha256")
        require(type(digest) is str)
        codec.plans.base.digest(digest)
        _same_frame(offer, exchange.frame("plan", nonce, plan_sha256=digest, **fields))
        guard()
        original = startups.acceptance.intake.CasePlan(writer_case_root(exchange.case), digest)
        cleanup.append(original.close)
        plan, domain, file_guard = _plan_file_guard(exchange, inputs, counterpart, original)
        cleanup.append(domain.close)
        guard()
        file_guard()
        exchange.send(
            exchange.frame("retained", nonce, offer_sha256=links.base.checksum(offer), **fields)
        )
        exchange.quiet()
        guard()
        file_guard()
        yield original
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
    preparation=False,
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
    The fixed passive command requires the separately tagged preparation profile;
    older profiles/commands do not select this module.
    """
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
        codec.source_profile(expected, peer_handoff=not preparation, preparation=preparation)
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


def _accepted_writer(owner, inputs, local, outer, counterpart, docker):
    """Separate acceptance -> original descriptor/dispatcher -> passive retirement."""
    import supplemental_recording_writer_channel as writer

    clock, original, template = owner.clock, owner.original, owner.template
    origin = clock.original
    deadline = owner.offer.deadline
    poll_by = deadline - 2 * writer.startup.acceptance.MAX_SECONDS
    peers = [
        (peer, (peer.identity, peer.fd, links._identity(peer.fd))) for peer in (outer, counterpart)
    ]

    def guard(end):
        require(time.monotonic() < end)
        require(owner.clock is clock and clock.original is origin and owner.original is original)
        require(owner.template is template and inputs.declaration is owner.declaration)
        owner._guard()
        inputs.recheck(deadline=end)
        for peer, pin in peers:
            _peer_guard(peer, pin)
        require(time.monotonic() < end)

    for _ in range(151):
        guard(poll_by)
        accepted = owner.poll()
        guard(poll_by)
        if accepted is not None:
            require(accepted is original and owner.accepted)
            break
        time.sleep(min(0.1, max(0, poll_by - time.monotonic())))
    else:
        require(False)
    connection = None
    try:
        connection = connections.Connection(
            handoff_root(original.plan.case),
            outer,
            deadline=min(time.monotonic() + 2, original.plan.lease["ready_by"], deadline),
        )
        end = connection.deadline
        guard(end)
        writer.prepare_idle_from_inputs(
            owner, inputs, connection, local, outer, counterpart, docker
        )
        guard(end)
    finally:
        if connection is not None:
            connection.close()
    guard(end)  # Retiring the original connection cannot conceal stale success.
    return end


def prepare_idle_writer(case, template_sha256, baseline_sha256, outer_identity):
    """Fixed passive flow; no expectations pin/future plan/PID is learned from argv.

    The independently provisioned outer owns the private preparation, permission
    and final handoff listeners, supplies
    the authenticated Inputs pin/counterpart, issues separate preflight permission
    and arranges independent final acceptance/descriptor handoff. This command
    neither launches/qualifies that outer nor provides an App action grant. Its
    installation and blocked-I/O termination require independent qualification.
    Files and one-attempt evidence survive every exit, including status 75.
    """
    cleanup, problem, end, owner = [], None, None, None
    try:
        codec.plans.base.identifier(case, case=True)
        for digest in (template_sha256, baseline_sha256):
            codec.plans.base.digest(digest)
        require(type(outer_identity) is domains.process.ProcessIdentity)
        declaration = inputs_module.declarations.Declaration(
            inputs_module.declarations.declaration_root(case), template_sha256
        )
        cleanup.append(declaration.close)
        template = declaration.recheck()
        require(codec.templates._read(template.raw)["plan"]["case"] == case)
        local = preflight_channel.current_identity()
        timer = domains.clock.ClockWitness(domains.clock.read())
        cleanup.append(timer.close)
        outer = domains.process.ProcessWitness(outer_identity)
        cleanup.append(outer.close)
        connection = connections.Connection(
            preparation_root(case, "writer"), outer, deadline=time.monotonic() + 2
        )
        cleanup.append(connection.close)
        with receive_inputs(
            declaration,
            connection,
            timer,
            local,
            role="writer",
            baseline_sha256=baseline_sha256,
            preparation=True,
        ) as (inputs, counterpart):
            phase, failure = [], None
            try:
                domain = domains.ZeroDomain(timer.original, outer)
                phase.append(domain.close)
                peer = preflight_channel.PeerConnection(
                    preflight_channel.peer_root(case),
                    timer.original.after_ns / domains.clock.NS + preflight.WAIT_SECONDS,
                )
                phase.append(peer.close)
                permission = preflight.Permission(
                    template,
                    template_sha256,
                    baseline_sha256,
                    local,
                    outer,
                    domain,
                    timer,
                    peer.channel,
                )
                phase.append(permission.close)
                peer.recheck()
                permission.wait()
                peer.recheck()
                # Retain the completed input socket passively until the outer's
                # distinct permission phase. Closing just after our ack could
                # race its final retirement checks. Never recheck/renew the old
                # expired input exchange, and retire it before baseline I/O.
                connection.close()
                owner = startups.Startup(declaration)
                phase.append(owner.close)
                docker = startups.plans.ordinary.Docker()
                prepare_writer(
                    inputs,
                    owner,
                    permission,
                    counterpart,
                    baseline_root(case),
                    docker,
                    original_timer=timer,
                    original_outer=outer,
                    original_connection=peer,
                    preparation=True,
                )
                end = _accepted_writer(owner, inputs, local, outer, counterpart, docker)
            except BaseException as error:
                failure = error
            finally:
                _cleanup(phase, failure)
    except BaseException as error:
        if type(owner) is startups.Startup:
            owner.failed = True
        problem = error
    finally:
        try:
            _cleanup(cleanup, problem)
        except BaseException:
            if type(owner) is startups.Startup:
                owner.failed = True
            raise
    try:
        require(end is not None and time.monotonic() < end)
        print(MILESTONE, flush=True)
        require(time.monotonic() < end)
    except BaseException as error:
        if type(owner) is startups.Startup:
            owner.failed = True
        _cleanup([], error)
    return 75  # Never a recording, restoration, Ready or installed-qualification receipt.


if __name__ == "__main__":
    try:
        import supplemental_recording_permission_probe as command_peer

        result = prepare_idle_writer(
            sys.argv[1], sys.argv[2], sys.argv[3], command_peer.parse_identity(sys.argv[4])
        )
    except Exception:
        print(MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise SystemExit(result)
