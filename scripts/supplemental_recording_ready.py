#!/usr/bin/env python3
"""Actual private ready-message / Engine / process / clock join, uninstalled.

This does not independently qualify the image, interpreter, source, profile or
host plan. The reviewed host adapter must establish those pins before attaching
and recheck them around this step. There is no recording begin, native success,
process exit, restoration, or caller-supplied ready-message entry point here.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from threading import get_ident

import supplemental_recording_clock as clock
import supplemental_recording_engine as engine

MESSAGE = "Finite recording readiness is unconfirmed; preserve this case and do not begin."
GRACE_SECONDS = 3
MAX_RETURN = 8192


class UnconfirmedReady(ValueError):
    """A parsed report alone is never a live readiness or success receipt."""


def require(value):
    if not value:
        raise UnconfirmedReady(MESSAGE)


def _context(pins, profile):
    engine.dispatch.execution._digest(profile)
    pins.payload()
    original = pins.host.projection.native
    return {
        "launch": pins.command.plan_sha256,
        "source": pins.command.source_sha256,
        "projection": pins.host.projection.sha256,
        "host_plan": pins.host.plan_sha256,
        "profile": profile,
        "manifest": original.manifest_sha256,
        "contract": original.contract.sha256,
        "generation": pins.generation,
        "ready_by": pins.command.ready_by,
    }


class Ready:
    """Consume an attached Client's actual first frame; take ownership on entry.

    The returned object's process witness is independent of transport lifetime.
    check_before_begin() may refuse later use but retains those exact pidfds for
    exit observation until close(). Constructor failure closes partial handles.
    The original host clock window and readiness deadline are never renewed.
    This class intentionally has no begin(), start(), resume() or success API.
    """

    def __init__(self, client, *, profile_sha256, original_clock, zero_domain=None):
        self.owner = os.getpid(), get_ident()
        self.client, self.processes = client, None
        self.failed = self.closed = False
        try:
            require(type(client) is engine.Client and type(original_clock) is clock.Window)
            client._check()
            pins = client.claim.pins
            require(original_clock.boot == pins.host.boot_id)
            original_clock.check_later(clock.read())
            self.clock = original_clock
            if zero_domain is not None:
                require(type(zero_domain) is engine.namespace.time_domain.ZeroDomain)
                proof = zero_domain.refresh()
                require(proof.original_clock == original_clock and proof.init == pins.init)
            self.zero_domain = zero_domain
            channel = client.attachment
            require(type(channel) is engine.attachment.Attachment and channel.reads == 0)
            require(not channel.closed and not channel.begun and channel.started)
            context = _context(pins, profile_sha256)
            self.context_raw = engine.dispatch.binding.encode(context)
            self.ready_by = pins.command.ready_by
            self.watch_deadline = (
                self.ready_by + pins.host.projection.native.contract.maximum_recording_seconds
            )
            require(self.watch_deadline + GRACE_SECONDS <= channel.finish_by)
            self.received_after = time.monotonic()
            value = channel.receive(deadline=self.ready_by)
            self.received_at = time.monotonic()
            mapping = engine.dispatch.binding.protected._mapping
            mapping(
                value,
                {"schema", "kind", "phase", "context", "guardian", "native", "watchdog", "body"},
            )
            require(type(value["schema"]) is int and value["schema"] == 1)
            require(value["kind"] == "finite-recording-operator" and value["phase"] == "ready")
            require(value["context"] == context)
            identities = {}
            for role in ("guardian", "native", "watchdog"):
                fields = {"pid", "start_ticks", "uid", "gid"}
                mapping(
                    value[role], fields | ({"deadline", "grace"} if role == "watchdog" else set())
                )
                identities[role] = {key: value[role][key] for key in fields}
            watch = value["watchdog"]
            for field in ("deadline", "grace"):
                engine.dispatch.binding.clock(watch[field])
            require(watch["deadline"] == self.watch_deadline and watch["grace"] == GRACE_SECONDS)
            received = mapping(
                mapping(value["body"], {"received"})["received"],
                {"raw", "pid", "start_ticks", "uid", "gid", "received_at"},
            )
            require(type(received["raw"]) is str)
            raw = received["raw"].encode("ascii")
            require(0 < len(raw) <= MAX_RETURN)
            decoder = engine.dispatch.binding.protected.evidence
            native = json.loads(
                raw, object_pairs_hook=decoder.unique, parse_constant=decoder.reject_constant
            )
            mapping(native, {"schema", "kind", "context", "phase", "body", "at"})
            require(type(native["schema"]) is int and native["schema"] == 1)
            require(native["kind"] == "finite-recording-control" and native["phase"] == "ready")
            require(native["context"] == context and native["body"] == {})
            require(engine.dispatch.binding.encode(native) == raw)
            for field in ("pid", "start_ticks", "uid", "gid"):
                require(
                    type(received[field]) is int and received[field] == identities["native"][field]
                )
            for number in (native["at"], received["received_at"]):
                engine.dispatch.binding.clock(number)
            require(
                client.claim.state.at
                <= native["at"]
                <= received["received_at"]
                <= self.received_at
                < self.ready_by
            )
            # Neither the outer dictionary nor the embedded return is accepted
            # as authentication until actual Engine and kernel mapping agree.
            self.processes = client.bind_processes(identities, zero_domain=zero_domain)
            require(self.processes.host_time == original_clock.namespace)
            self.ready_raw = engine.dispatch.binding.encode(value)
            self.check_before_begin()
        except BaseException as error:
            with suppress(Exception):
                self.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedReady(MESSAGE) from None

    def check_before_begin(self):
        """Recheck this one original readiness; not permission to send begin."""
        try:
            require(
                not self.closed and not self.failed and self.owner == (os.getpid(), get_ident())
            )
            require(time.monotonic() < self.ready_by)
            self.client._check()
            channel = self.client.attachment
            require(not channel.closed and channel.reads == 1 and not channel.begun)
            self.clock.check_later(clock.read())
            require(self.processes.zero_domain is self.zero_domain)
            if self.zero_domain is not None:
                require(self.zero_domain.evidence.original_clock == self.clock)
            self.processes.refresh()
            require(self.processes.host_time == self.clock.namespace)
            require(time.monotonic() < self.ready_by)
        except BaseException as error:
            self.failed = True
            if type(self.client) is engine.Client:
                self.client.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedReady(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        self.closed = True
        try:
            if self.processes is not None:
                self.processes.close()
        finally:
            if type(self.client) is engine.Client:
                self.client.close()


if __name__ == "__main__":
    raise SystemExit("Private readiness join only; no installed host plan is enabled.")
