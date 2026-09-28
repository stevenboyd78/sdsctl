#!/usr/bin/env python3
"""Join descriptor intake to the ORIGINAL accepted writer Startup, offline only.

No serialized plan, replacement clock or reconstructed Startup is accepted.
The external launcher must authenticate the declaration pin, original peers,
private connection, runtime/source and termination before calling this join.
This is not input provisioning, installed qualification, Ready or App consent.
No existing command or runtime qualification selects this module's source graph.
"""

from __future__ import annotations

import math
import os
import time
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
    or admit App work, and no existing command selects this variant.
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


def _receive(
    owner, declaration, expected_sha256, channel, local, outer, observer, *, deadline, retained=None
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
