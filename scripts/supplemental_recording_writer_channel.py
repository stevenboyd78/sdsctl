#!/usr/bin/env python3
"""Join descriptor intake to the ORIGINAL accepted writer Startup, offline only.

No serialized plan, replacement clock or reconstructed Startup is accepted.
The external launcher must authenticate the declaration pin, original peers,
private connection, runtime/source and termination before calling this join.
This is not input provisioning, installed qualification, Ready or App consent.
The explicit peer source comparison inventories this graph for a separate
passive preparation command. No command admits active or live App execution.
"""

from __future__ import annotations

import math
import os
import time
from contextlib import contextmanager
from threading import get_ident

import supplemental_recording_peer_bootstrap as bootstrap
import supplemental_recording_peer_connection as connections
import supplemental_recording_peer_inputs as input_files
import supplemental_recording_service_runtime_expectations as expectations
import supplemental_recording_service_startup as startup

MESSAGE = "Recording writer channel is unconfirmed; preserve the case and do not retry."


class UnconfirmedWriterChannel(ValueError):
    """A partial handoff never allows replacement ownership or action replay."""


def require(value):
    if not value:
        raise UnconfirmedWriterChannel(MESSAGE)


def receive(owner, declaration, expected_sha256, channel, local, outer, observer, *, deadline=None):
    """Borrow accepted startup inputs; return owned Channels and transport Receipt.

    One non-renewable two-second/original-ready window includes every startup
    check, transport step and final recheck. A supplied deadline only narrows it.
    The original Startup's attempt is consumed before validation or I/O, once
    exact type/thread ownership is established. No retry after partial failure.

    This step must precede journal/service assembly. It requires the accepted
    baseline-derived plan and retains its actual first continuing ClockWitness,
    not the earlier preflight witness or a new witness from equal plan bytes.
    Caller-supplied runtime declarations must match an independently authenticated
    digest; this comparison does NOT authenticate the digest's origin.

    Success does not construct/run a service or send Ready. The caller must keep
    this Startup alive and create its original Link using owner.original.plan
    and owner.clock. Close that borrower and returned Channels BEFORE Startup.
    On failure, received descriptors are retired; caller-owned socket, witnesses
    and clock are not closed here. Startup's own failed checks may invalidate
    its original inputs. Files are never removed and no journal is reopened.
    """
    return _receive(
        owner, declaration, expected_sha256, channel, local, outer, observer, deadline=deadline
    )


def receive_from_inputs(owner, inputs, connection, local, outer, observer, *, deadline=None):
    """Borrow original retained inputs/connection through the SAME writer intake.

    Inputs must borrow this Startup's exact original Declaration and Template;
    Connection must retain the exact outer witness. Its original construction
    cutoff also bounds every input read/startup check/descriptor handoff. Files
    and connection are checked before and after transport, with no replacement
    inputs, socket, witness or clock. Caller retains all borrowed owners; received
    Channels alone transfer ownership. This does not authenticate installation
    or admit App work. The fixed passive writer uses this intake through its
    immediate preparation/retirement variant.
    """
    return _receive(
        owner,
        None,
        None,
        None,
        local,
        outer,
        observer,
        deadline=deadline,
        retained=(inputs, connection),
    )


def prepare_idle_from_inputs(owner, inputs, connection, local, outer, observer, docker):
    """Prepare and immediately retire the original passive scope.

    The fixed passive writer selects this immediate variant. Its return does
    not mean an outer retained-listener delivery has completed; that outer may
    still be checking the original connection. Do not treat the command's own
    preparation completion as permission to retire another owner's borrower.
    """
    with retained_idle_from_inputs(
        owner, inputs, connection, local, outer, observer, docker
    ) as receipt:
        return receipt


def prepare_idle_until_released(owner, inputs, connection, local, outer, observer, docker):
    """Explicit passive release join; no installed command selects this variant.

    Retain the ORIGINAL bootstrap Endpoint through passive service assembly and
    the original outer's authenticated one-way retirement frame. The complete
    intake/service/release/cleanup stays within the original connection cutoff.
    No fixture pipe, new endpoint, clock or timeout can supply this release.
    It proves neither complete outer qualification nor exit/action/recovery.
    """
    with _retained_idle_from_inputs(
        owner, inputs, connection, local, outer, observer, docker, passive_retirement=True
    ) as receipt:
        return receipt


@contextmanager
def retained_idle_from_inputs(owner, inputs, connection, local, outer, observer, docker):
    """Retain original passive custody during a caller's bounded handoff join.

    Yield ONLY the transport receipt, never a service, Link, clock or dispatcher.
    No inbox is consumed. It creates only the original preparation journal/inbox,
    wires that service's dispatcher to this exact Link.observe, then retires the
    service AFTER the caller's scope and before the Link and received copies.
    Startup, input readers, bootstrap connection and both peer witnesses remain
    caller-owned. Their own failed checks may invalidate their resources; this
    join never replaces them.

    The connection's ORIGINAL cutoff bounds the complete join, including input
    checks and assembly; it is not restarted after descriptor receipt. No plan,
    clock, Startup, observer, connection or callback may be supplied as a later
    replacement. An uncertain/partial result preserves files and consumes the
    same one intake attempt. It never retries or reopens a journal. The caller's
    body and exit checks share the ORIGINAL connection deadline; yielding does
    not grant a new window. This context does not provide the outer's completion
    protocol, authenticated admission or independent blocked-I/O termination.

    Borrowed startup, inputs, connection and original peer handles are rechecked
    AFTER final channel retirement, under the SAME cutoff. Retired channels and
    Link are not reopened. Return is only the original transport receipt. It is
    not Ready, App-action admission, recording/restore success, input provenance,
    fixed-entrypoint selection or outer/platform qualification. No existing
    command selects a continuing scope; the old command still exits immediately.
    Separate launcher/admission/termination gates remain mandatory.
    """
    with _retained_idle_from_inputs(
        owner, inputs, connection, local, outer, observer, docker
    ) as receipt:
        yield receipt


@contextmanager
def _retained_idle_from_inputs(
    owner, inputs, connection, local, outer, observer, docker, *, passive_retirement=False
):
    channels = link = receipt = endpoint = problem = None
    pins = ()
    try:
        # Intake owns the one-attempt check, including invalid retained inputs.
        if passive_retirement:
            channels, receipt, endpoint = _receive(
                owner,
                None,
                None,
                None,
                local,
                outer,
                observer,
                deadline=None,
                retained=(inputs, connection),
                passive_retirement=True,
            )
        else:
            channels, receipt = receive_from_inputs(
                owner, inputs, connection, local, outer, observer
            )
        pins = tuple(
            (s, s.fileno(), bootstrap.links._identity(s.fileno()))
            for s in (channels.incoming, channels.outgoing)
        )
        original, clock = owner.original, owner.clock
        origin = clock.original
        plan, template = original.plan, owner.template
        expected, cutoff = inputs.expectations, connection.deadline
        end = min(cutoff, plan.lease["ready_by"])
        observer_pin = observer.identity, observer.fd, bootstrap.links._identity(observer.fd)

        def guard(*, retired=False):
            require(time.monotonic() < end)
            require(owner.original is original and owner.clock is clock)
            require(clock.original is origin and owner.accepted)
            # idle_service already holds the ORIGINAL startup lock while
            # yielded. Recheck its custody directly, never attempt another
            # accepted_input acquisition or substitute an equal owner.
            owner._guard()
            require(owner.template is template and original.plan is plan)
            require(inputs.declaration is owner.declaration and inputs.template is template)
            require(inputs.expectations is expected and inputs.recheck(deadline=end) is expected)
            require(connection.peer is outer and connection.deadline == cutoff)
            connection.recheck()
            identity, fd, pin = observer_pin
            require(observer.identity is identity and observer.fd == fd)
            require(bootstrap.links._identity(fd) == pin and not os.get_inheritable(fd))
            bootstrap.links.processes.ProcessWitness._live_descriptor(fd, identity.pid)
            require(not observer.exited())
            require(
                bootstrap.links.processes.read_identity(identity.pid, identity.container_id)
                == identity
            )
            require(bootstrap.links.processes.read_identity(local.pid, local.container_id) == local)
            if retired:
                require(link.closed and all(s.fileno() == -1 for s, _, _ in pins))
            else:
                require(all(s.fileno() == fd for s, fd, _ in pins))
                require(all(bootstrap.links._identity(fd) == pin for _, fd, pin in pins))
            if link is not None and not retired:
                require(link.channel is channels and link.plan is plan and link.timer is clock)
                link._guard(end)
            require(time.monotonic() < end)

        guard()
        link = bootstrap.links.Link(channels, plan, clock, observer, role="writer")
        dispatch_observer = link.observe
        guard()
        with owner.idle_service(
            docker, dispatch_observer=dispatch_observer, deadline=end
        ) as service:
            guard()
            require(service.original is original and service.clock_witness is clock)
            require(service.dispatch.observe is dispatch_observer)
            require(service.dispatch._original_observe is dispatch_observer)
            require(service._dispatch_observer is dispatch_observer)
            require(not service.used and not service.dispatch.used)
            require(len(service.journal.entries) == 1 and not service.processes.witnesses)
            # Deliberately no run(), consume(), dispatch, native or recording.
            if passive_retirement:
                endpoint.receive_retirement(receipt)
                guard()
            yield receipt
            guard()
            require(not service.closed and not service.failed)
            require(service.original is original and service.clock_witness is clock)
            require(service.dispatch.observe is dispatch_observer)
            require(service.dispatch._original_observe is dispatch_observer)
            require(service._dispatch_observer is dispatch_observer)
            require(not service.used and not service.dispatch.used)
            require(len(service.journal.entries) == 1 and not service.processes.witnesses)
        require(service.closed and service.journal.fd == -1)
        guard()
    except BaseException as error:
        problem = error
    finally:
        if endpoint is not None:
            try:
                endpoint.close()
            except BaseException as error:
                if problem is None or not isinstance(error, Exception):
                    problem = error
        # All received copies retire even if Link retirement fails. Retire only
        # the originals; a foreign reused FD is detached, never closed here.
        if link is not None:
            try:
                link.close()
            except BaseException as error:
                if problem is None or not isinstance(error, Exception):
                    problem = error
        if channels is not None:
            for channel, fd, pin in pins or (
                (s, s.fileno(), None) for s in (channels.incoming, channels.outgoing)
            ):
                try:
                    if channel.fileno() == -1:
                        continue
                    require(channel.fileno() == fd)
                    require(pin is None or bootstrap.links._identity(fd) == pin)
                    channel.close()
                except BaseException as error:
                    channel.detach()
                    if problem is None or not isinstance(error, Exception):
                        problem = error
    if problem is None:
        try:
            # Closing the final received socket is still part of the original
            # attempt. Do not return a stale receipt after input/peer/startup
            # loss during retirement. Recheck borrowed originals, without
            # reopening any retired Link, channel, journal or service.
            guard(retired=True)
        except BaseException as error:
            problem = error
    if problem is not None:
        if not isinstance(problem, Exception):
            raise problem
        raise UnconfirmedWriterChannel(MESSAGE) from None


def _receive(
    owner,
    declaration,
    expected_sha256,
    channel,
    local,
    outer,
    observer,
    *,
    deadline,
    retained=None,
    passive_retirement=False,
):
    began = time.monotonic()
    endpoint = channels = result = problem = None
    pins = None
    claimed = False
    try:
        require(type(owner) is startup.Startup)
        require(owner.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(owner.peer_channel_attempted is False)
        owner.peer_channel_attempted = True
        claimed = True
        end = began + bootstrap.SECONDS
        if deadline is not None:
            require(type(deadline) in (int, float) and math.isfinite(deadline))
            end = min(end, deadline)
        if retained is not None:
            inputs, connection = retained
            require(
                type(inputs) is input_files.Inputs and type(connection) is connections.Connection
            )
            require(inputs.declaration is owner.declaration and inputs.template is owner.template)
            require(connection.peer is outer)
            end = min(end, connection.deadline)
            connection.recheck()
            declaration = inputs.recheck(deadline=end)
            expected_sha256, channel = inputs.expected, connection.channel
            connection_cutoff = connection.deadline
        require(time.monotonic() < end)
        original = owner.accepted_input()
        plan, clock, template = original.plan, owner.clock, owner.template
        baseline, projected = owner.baseline, owner.projected
        require(baseline is not None and projected is not None)
        require(owner._service_inputs is not None and not owner.service_used)
        require(not owner._service_active and not owner.app_idle_publication_used)
        end = min(end, plan.lease["ready_by"])
        require(type(declaration) is expectations.Expectations)
        require(type(expected_sha256) is str)
        startup.plans.base.digest(expected_sha256)
        require(declaration.sha256 == expected_sha256)
        origin = clock.original

        def binding():
            require(time.monotonic() < end)
            if retained is not None:
                require(inputs.declaration is owner.declaration and inputs.template is template)
                require(inputs.expectations is declaration and inputs.expected == expected_sha256)
                require(connection.channel is channel and connection.peer is outer)
                require(connection.deadline == connection_cutoff)
                require(inputs.recheck(deadline=end) is declaration)
                connection.recheck()
            require(owner.peer_channel_attempted is True)
            require(owner.original is original)
            require(owner.clock is clock and clock.original is origin)
            require(owner.template is template)
            require(owner.baseline is baseline and owner.projected is projected)
            require(not owner.service_used and not owner._service_active)
            require(not owner.app_idle_publication_used)
            if pins is not None:
                require((channels.incoming, channels.outgoing) == tuple(p[0] for p in pins))
                require(bootstrap._sockets(channels) == [p[2] for p in pins])
                require(all(s.fileno() == fd for s, fd, _ in pins))
            require(time.monotonic() < end)

        def guard():
            binding()
            require(owner.accepted_input() is original)
            require(original.plan is plan)
            require(declaration.sha256 == expected_sha256)
            declaration.check_plan(template, plan, origin)
            binding()

        guard()
        endpoint = bootstrap.Endpoint(
            channel,
            plan,
            clock,
            local,
            outer,
            observer,
            role="writer",
            mode="receive",
            declaration_sha256=expected_sha256,
            deadline=end,
            passive_retirement=passive_retirement,
        )
        guard()
        channels, receipt = endpoint.receive()
        # Pin before any subsequent callback/guard. Never close a foreign reused
        # descriptor through a stale socket wrapper if later custody fails.
        pins = tuple(
            (s, s.fileno(), bootstrap.links._identity(s.fileno()))
            for s in (channels.incoming, channels.outgoing)
        )
        guard()
        if passive_retirement:
            # Transfer only to the internal passive scope, never a public
            # receiver/caller. Keep the original namespace and transport owner.
            guard()
            result = channels, receipt, endpoint
            endpoint = None
        else:
            endpoint.close()
            endpoint = None  # Retired once, before the final bounded owner check.
            guard()
            result = channels, receipt
    except BaseException as error:
        problem = error
    finally:
        if claimed:
            # A contradictory/reset slot is failure, never permission to retry.
            owner.peer_channel_attempted = True
        if endpoint is not None:
            try:
                endpoint.close()
            except BaseException as error:
                if problem is None or not isinstance(error, Exception):
                    problem = error
        if problem is not None and channels is not None:
            for socket, fd, identity in pins or (
                (s, s.fileno(), None) for s in (channels.incoming, channels.outgoing)
            ):
                try:
                    if socket.fileno() == -1:
                        continue
                    require(socket.fileno() == fd)
                    require(identity is None or bootstrap.links._identity(fd) == identity)
                    socket.close()
                except BaseException as error:
                    socket.detach()
                    if not isinstance(error, Exception):
                        problem = error
    if problem is not None:
        if not isinstance(problem, Exception):
            raise problem
        raise UnconfirmedWriterChannel(MESSAGE) from None
    return result


if __name__ == "__main__":
    raise SystemExit("Uninstalled original writer channel join only; no active launch enabled.")
