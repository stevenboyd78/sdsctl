#!/usr/bin/env python3
"""One-use supervised descriptor delivery, not a launcher or App action gate.

The caller has already qualified/provisioned BOTH peers and private connections,
captured original custody and explicitly armed peer termination. This outer-only
uninstalled join keeps the same original owners, comparisons and hard deadline.
No listener, installed command, App operation, journal or recovery is provided.
No observed source graph or passive permission format selects this module.
"""

from __future__ import annotations

import os
import select
import time
from contextlib import suppress
from dataclasses import dataclass

import supplemental_recording_peer_bootstrap as bootstrap
import supplemental_recording_peer_inputs as peer_inputs
import supplemental_recording_peer_listener as listeners
import supplemental_recording_peer_termination as termination

MESSAGE = "Recording peer delivery is unconfirmed; preserve the case and do not retry."
SECONDS = 2.0


class UnconfirmedDelivery(ValueError):
    """Partial delivery never admits work, replay, replacement or restoration."""


def require(value):
    if not value:
        raise UnconfirmedDelivery(MESSAGE)


@dataclass(frozen=True)
class Delivered:
    """Transport facts only; NOT a Ready, action grant or continuing success cache."""

    plan_sha256: str
    declaration_sha256: str
    writer: bootstrap.Receipt
    observer: bootstrap.Receipt


def _retire(channels, pins):
    if pins is None:  # Initial acquisition failed before exposing these new sockets.
        channels.close()
        return
    problem = False
    for endpoint, fd, identity in pins:
        if endpoint.fileno() == -1:
            continue
        try:
            require(endpoint.fileno() == fd and bootstrap.links._identity(fd) == identity)
            endpoint.close()
        except Exception:
            endpoint.detach()  # Never close a foreign reused descriptor through this wrapper.
            problem = True
    require(not problem)


def deliver(custody, watch, local, writer_connection, observer_connection):
    """Bind two handoffs to one original runtime pair and already armed watcher.

    One attempt on the original pair, two fresh complete paired comparisons
    (before/after delivery), one non-renewable two-second/original-ready window.
    Connections, clock, witnesses and custody are borrowed. Sender Channels and
    Endpoint namespace handles are owned and retired before returning.

    Once the exact original pair/watch binding has been validated and the slot
    consumed, ANY failure cancels that original watcher, stopping both peers.
    Invalid/unrelated input objects are never adopted or closed. Cancellation
    is the existing armed peer-stop operation, NOT App or recovery authority.
    On success the same watcher remains armed and caller-owned. The caller must
    still require independent Ready, input/outer provenance and App action scope.
    """
    return _deliver(custody, watch, local, writer_connection, observer_connection)


def deliver_retained(custody, watch, local, writer_listener, observer_listener):
    """Explicit listener-bound variant, still uninstalled and without App actions.

    Borrow two original accepted Listener owners, each bound to the exact witness
    in the original runtime pair. Their construction/accept cutoffs narrow the
    complete delivery window, including all runtime collections. Paths, sockets,
    witnesses and listener owners are rechecked between steps and at completion.
    Listener uncertainty after original custody binding cancels that same Watch;
    no replacement listener, deadline renewal or preflight permission is allowed.
    Successful handoff leaves both listeners and the Watch caller-owned.
    """
    return _deliver(
        custody, watch, local, None, None, retained=(writer_listener, observer_listener)
    )


def deliver_from_inputs(custody, watch, local, inputs, writer_listener, observer_listener):
    """Join retained inputs and listeners to the SAME already qualified pair.

    Both collectors must already use the exact Inputs-owned Expectations and
    borrowed original Template. Every guard freshly reads those original files
    under the same complete handoff cutoff. No file is adopted after observing
    the peers. Digest authentication, installation provenance, a fixed launcher
    and App authority remain external, independent prerequisites.
    """
    return _deliver(
        custody,
        watch,
        local,
        None,
        None,
        retained=(writer_listener, observer_listener),
        inputs=inputs,
        inputs_required=True,
    )


def _deliver(
    custody,
    watch,
    local,
    writer_connection,
    observer_connection,
    *,
    retained=None,
    inputs=None,
    inputs_required=False,
):
    began = time.monotonic()
    accepted = False
    bundles, endpoints = [], []
    socket_pins = {}
    error = result = None
    original_pair = original_watch = None
    try:
        require(type(custody) is termination.Custody and type(watch) is termination.Watch)
        custody._guard()
        watch._guard()
        require(custody.attempted and custody.armed_watch is watch and not watch.finished)
        require(watch.identities is custody.identities and watch.deadline_ns == custody.deadline_ns)
        require(not select.select([watch.fd], [], [], 0)[0])
        require(
            type(local) is bootstrap.links.processes.ProcessIdentity and local.pid == os.getpid()
        )
        original_pair, original_watch = custody.pair, watch
        require(type(original_pair) is termination.peers.PeerRuntimePair)
        require(original_pair.channel_delivery_attempted is False)
        original_pair.channel_delivery_attempted = True
        accepted = True
        plan, clock = custody.plan, custody.clock
        writer, observer = original_pair.writer, original_pair.observer
        declaration = writer.expectations
        declared_sha = writer.expectations_sha256
        original_owners = (
            custody,
            watch,
            original_pair,
            plan,
            clock,
            writer,
            observer,
            declaration,
            writer.witness,
            observer.witness,
        )
        end = min(began + SECONDS, plan.lease["ready_by"])
        retained_pins = []
        if retained is not None:
            require(retained[0] is not retained[1])
            for listener, member in zip(retained, (writer, observer), strict=True):
                require(type(listener) is listeners.Listener)
                require(listener.peer is member.witness and listener.accepted is True)
                listener.recheck()
                retained_pins.append((listener, listener.channel, listener.peer, listener.deadline))
                end = min(end, listener.deadline)
            writer_connection, observer_connection = (item[1] for item in retained_pins)

        def guard():
            require(time.monotonic() < end)
            if inputs_required:
                require(type(inputs) is peer_inputs.Inputs)
                require(inputs.expectations is declaration)
                require(inputs.template is writer.template and inputs.template is observer.template)
                require(inputs.expected == declared_sha)
                require(inputs.recheck(deadline=end) is declaration)
            for listener, channel, peer, deadline in retained_pins:
                require(listener.channel is channel and listener.peer is peer)
                require(listener.deadline == deadline and listener.accepted is True)
                listener.recheck()
            require(
                all(
                    current is original
                    for current, original in zip(
                        (
                            custody,
                            watch,
                            custody.pair,
                            custody.plan,
                            custody.clock,
                            custody.pair.writer,
                            custody.pair.observer,
                            writer.expectations,
                            writer.witness,
                            observer.witness,
                        ),
                        original_owners,
                        strict=True,
                    )
                )
            )
            require(original_pair.channel_delivery_attempted is True)
            require(custody.armed_watch is original_watch)
            custody._guard()
            custody._live(end)
            original_watch._guard()
            require(not original_watch.finished)
            require(original_watch.identities is custody.identities)
            require(original_watch.deadline_ns == custody.deadline_ns)
            require(not select.select([original_watch.fd], [], [], 0)[0])
            require(writer.expectations_sha256 == declared_sha)
            require(time.monotonic() < end)

        guard()
        original_pair._collect_before(end)
        guard()
        require(writer_connection is not observer_connection)
        bundles.extend(bootstrap.links.pair())
        for channels in bundles:
            socket_pins[id(channels)] = tuple(
                (s, s.fileno(), bootstrap.links._identity(s.fileno()))
                for s in (channels.incoming, channels.outgoing)
            )
        receipts = []
        for role, connection, member, other, channels in (
            ("writer", writer_connection, writer, observer, bundles[0]),
            ("observer", observer_connection, observer, writer, bundles[1]),
        ):
            guard()
            endpoint = bootstrap.Endpoint(
                connection,
                plan,
                clock,
                local,
                member.witness,
                other.witness,
                role=role,
                mode="deliver",
                declaration_sha256=declared_sha,
                deadline=end,
            )
            endpoints.append(endpoint)
            receipts.append(endpoint.deliver(channels))
            endpoint.close()
            _retire(channels, socket_pins[id(channels)])
            guard()
        original_pair._collect_before(end)
        guard()
        result = Delivered(plan.sha256, declared_sha, *receipts)
    except BaseException as cause:
        error = cause
    finally:
        # Cleanup failures also fail closed. Do not skip the other owned side.
        for resource in reversed(endpoints):
            try:
                resource.close()
            except BaseException as cause:
                if error is None:
                    error = cause
        for channels in reversed(bundles):
            try:
                _retire(channels, socket_pins.get(id(channels)))
            except BaseException as cause:
                if error is None:
                    error = cause
        if error is not None and accepted:
            # Remains uncertainty; never substitute/reopen any owner.
            with suppress(BaseException):
                original_watch.close()
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedDelivery(MESSAGE) from None
    return result


if __name__ == "__main__":
    raise SystemExit("Uninstalled supervised descriptor delivery only; no active launcher enabled.")
