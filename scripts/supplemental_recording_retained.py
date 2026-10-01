#!/usr/bin/env python3
"""Read-only post-begin continuity for already retained private return evidence.

No dispatch, deadline renewal, process discovery, signal, success or restoration.
An exited actor may have left a buffered return. Only its originally retained
pidfd can establish that exit; missing proc data is never sufficient by itself.
The separate receipt consumer still has to validate each timely native return.
"""

from __future__ import annotations

import os
import time
from threading import get_ident

import supplemental_recording_ready as received

engine = received.engine
dispatch = engine.dispatch
namespace = engine.namespace
MESSAGE = "Retained recording continuity is unconfirmed; preserve evidence and do not retry."
ROLES = ("init", "guardian", "native", "watchdog")


class UnconfirmedContinuity(ValueError):
    """A retained process handle is not a native success receipt."""


def require(value):
    if not value:
        raise UnconfirmedContinuity(MESSAGE)


class Retained:
    """Keep original dispatch evidence after its readiness window has ended.

    Construction is possible only after an actual Ready and one consumed begin.
    Unlike Claim.check(), these checks cannot enable create/attach/begin: they
    read the original final attach-intent chain, bounded by the already fixed
    attachment finish time. Init must stay live. Other actors must be unchanged
    and live, or proven exited through the exact handles retained at readiness.
    This class owns no descriptors; Ready remains their explicit owner.
    """

    def __init__(self, ready):
        self.ready = ready
        self.failed = False
        self.owner = os.getpid(), get_ident()
        try:
            require(type(ready) is received.Ready and ready.owner == self.owner)
            client = ready.client
            require(type(client) is engine.Client)
            require(client.attachment.reads == 1 and client.attachment.begun)
            self.client, self.channel = client, client.attachment
            self.claim, self.endpoint, self.processes = (
                client.claim,
                client.endpoint,
                ready.processes,
            )
            require(type(self.processes) is namespace.Witness)
            self.pins, self.state = self.claim.pins, self.claim.state
            self.directory, self.directory_identity = self.claim.directory, self.claim.identity
            self.initial = self.claim.witness
            require(self.state.count == 3 and self.state.phase == "attach_intent")
            self.clock = ready.clock
            self.context_raw, self.ready_raw = ready.context_raw, ready.ready_raw
            self.ready_by, self.watch_deadline = ready.ready_by, ready.watch_deadline
            self.finish_by = self.channel.finish_by
            self.handles = dict(self.processes.handles)
            self.actors = self.processes.actors
            self.host_domains = self.processes.host_user, self.processes.host_time
            self.zero_domain = self.processes.zero_domain
            self.domain_sha256 = self.processes.domain_sha256
            self.exits = frozenset()
            self.check()
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if type(self.ready) is received.Ready:
            self.ready.failed = True
            if type(self.ready.processes) is namespace.Witness:
                self.ready.processes.failed = True
            if type(self.ready.client) is engine.Client:
                self.ready.client.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedContinuity(MESSAGE) from None

    def _identities(self, end):
        witness = self.processes
        require(type(witness) is namespace.Witness and witness.owner == self.owner)
        require(not witness.closed and not witness.failed)
        require(witness.handles == self.handles and tuple(self.handles) == ROLES)
        require(witness.actors == self.actors and witness.expected_init == self.pins.init)
        require((witness.host_user, witness.host_time) == self.host_domains)
        require(
            witness.zero_domain is self.zero_domain and witness.domain_sha256 == self.domain_sha256
        )
        if self.zero_domain is not None:
            require(self.zero_domain.evidence.original_clock == self.clock)
        require(witness._host_domains() == self.host_domains)
        witness._match(self.actors)
        exited = set()
        for role, actor in zip(ROLES, self.actors, strict=True):
            require(time.monotonic() < end)
            if witness.exited(role):
                require(role != "init")
                exited.add(role)
                continue
            require(role not in self.exits)
            try:
                current = namespace.read(actor.host_pid, actor.container_id)
            except Exception:
                # A race with this exact actor's exit is allowed. A frozen,
                # unreadable or replaced live process is NOT considered exited.
                require(role != "init" and witness.exited(role))
                exited.add(role)
            else:
                require(current == actor)
                if witness.exited(role):
                    require(role != "init")
                    exited.add(role)
        require(not witness.exited("init") and witness._host_domains() == self.host_domains)
        require(time.monotonic() < end)
        return frozenset(exited)

    def check(self):
        """Return only observed exited roles; never success or recovery authority."""
        try:
            require(not self.failed and self.owner == (os.getpid(), get_ident()))
            ready, client, claim = self.ready, self.client, self.claim
            require(not ready.failed and not ready.closed and ready.owner == self.owner)
            require(ready.client is client and ready.processes is self.processes)
            require(ready.zero_domain is self.zero_domain)
            require(ready.clock == self.clock and ready.context_raw == self.context_raw)
            require(ready.ready_raw == self.ready_raw)
            require((ready.ready_by, ready.watch_deadline) == (self.ready_by, self.watch_deadline))
            require(not client.closed and client.owner == self.owner)
            require(
                client.create_attempted and client.attach_attempted and client.binding_attempted
            )
            require(client.claim is claim and client.endpoint is self.endpoint)
            require(client.attachment is self.channel)
            require(self.channel.begun and 1 <= self.channel.reads <= 4)
            require(
                not self.channel.closed or self.channel.finished and self.channel.reads == 4
            )  # Only a returned clean EOF, never a failed/closed attachment.
            require(
                (self.channel.ready_by, self.channel.finish_by) == (self.ready_by, self.finish_by)
            )
            end = min(time.monotonic() + 2, self.finish_by)
            require(time.monotonic() < end)
            self.clock.check_later(received.clock.read())
            require(not claim.poisoned and claim.owner == self.owner)
            require(claim.pins == self.pins and claim.state == self.state)
            require(claim.directory == self.directory and claim.identity == self.directory_identity)
            require(claim.witness is self.initial and self.initial.identity == self.pins.init)
            require(not self.initial.exited())
            dispatch._location(self.directory, self.pins)
            with dispatch.binding.protected._private_directory(
                self.directory, exclusive=False
            ) as fd:
                require(dispatch.binding.identity(os.fstat(fd))[:6] == self.directory_identity)
                require(dispatch._read(fd, self.pins, end) == self.state)
            self.endpoint.check()
            exits = self._identities(end)
            self.clock.check_later(received.clock.read())
            require(time.monotonic() < end)
            self.exits = exits
            return exits
        except BaseException as error:
            self._fail(error)


if __name__ == "__main__":
    raise SystemExit("Read-only retained continuity only; no installed host action enabled.")
