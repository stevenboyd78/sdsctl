#!/usr/bin/env python3
"""Uninstalled one-use plan offer over a borrowed ORIGINAL clock witness.

Only models the continuing owner's bounded wait for an independently reviewed
plan digest. No publication, file intake, journal, service, App or recording
action is implemented. The caller retains and later closes the clock witness;
this object cannot replace it or renew its original sample. This module is not
part of the qualified helper image/command allowlist.
"""

from __future__ import annotations

import os
import time
from threading import Lock, get_ident

import supplemental_recording_service_template as template_codec

plans = template_codec.plans
MAX_OFFER_SECONDS = 15
MESSAGE = "Recording startup offer is unconfirmed; preserve the original owner and do not retry."


class UnconfirmedOffer(ValueError):
    """Failure never reveals a proposed plan, pathname, or supplied digest."""


def require(value):
    if not value:
        raise UnconfirmedOffer(MESSAGE)


class Offer:
    """A borrowed-clock model, not a service entrypoint or independent approval.

    Constructor callers must authenticate the template pin and retain the actual
    clock owner for the entire later service. inspect() returns the same proposed
    immutable plan, not readiness. accept() consumes ONE externally supplied
    exact plan digest, even when it is invalid or the outcome becomes uncertain.
    Acceptance is structural only: no private-file publication/acknowledgment,
    installed qualification, operator notice or recording authority is implied.
    A new Offer object is not permission to restart the original case.
    """

    def __init__(self, template, expected_template_sha256, clock_witness):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = self.used = self.accepted = False
        try:
            require(type(template) is template_codec.Template)
            require(type(expected_template_sha256) is str)
            plans.base.digest(expected_template_sha256)
            require(template.sha256 == expected_template_sha256)
            require(type(clock_witness) is plans.clock.ClockWitness)
            self.template, self.clock_witness = template, clock_witness
            self.original_clock = clock_witness.original
            self.plan = template.preview(self.original_clock)
            self.plan_pin = plans.PinnedPlan(self.plan)
            self.originals = template, clock_witness, self.original_clock, self.plan
            self.template_raw, self.template_sha256 = template.raw, expected_template_sha256
            self.deadline = min(
                self.original_clock.after_ns / plans.clock.NS + MAX_OFFER_SECONDS,
                self.plan.lease["ready_by"],
            )
            self.original_deadline = self.deadline
            self._check()
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedOffer(MESSAGE) from None

    def _guard(self):
        require(not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident()))
        require(
            all(
                current is original
                for current, original in zip(
                    (self.template, self.clock_witness, self.original_clock, self.plan),
                    self.originals,
                    strict=True,
                )
            )
        )
        require(type(self.clock_witness) is plans.clock.ClockWitness)
        require(self.clock_witness.original is self.original_clock)
        require(self.template.raw == self.template_raw)
        require(self.template.sha256 == self.template_sha256)
        self.plan_pin.check(self.plan)
        self.template.check_plan(self.plan, self.original_clock)
        require(self.deadline == self.original_deadline)
        require(time.monotonic() < self.deadline)

    def _check(self):
        self._guard()
        observed = self.clock_witness.read()
        self.plan.check_clock(observed)
        require(observed.after_ns / plans.clock.NS < self.deadline)
        # Clock I/O is a boundary: refuse changes or concurrent poisoning that
        # occur during it, including on the final acceptance check.
        self._guard()

    def inspect(self):
        """No clock renewal or publication; return the same original proposal."""
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used)
            self._check()
            return self.plan
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def accept(self, independently_reviewed_plan_sha256):
        """One structural acceptance, not independent digest provenance or action."""
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used)
            self.used = True
            self._check()
            require(type(independently_reviewed_plan_sha256) is str)
            plans.base.digest(independently_reviewed_plan_sha256)
            require(independently_reviewed_plan_sha256 == self.plan.sha256)
            self._check()
            self.accepted = True
            return self.plan
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def close(self):
        """Close this model only; caller's ORIGINAL witness stays caller-owned."""
        try:
            require(self.owner == (os.getpid(), get_ident()))
            self.closed = True
        except BaseException as error:
            self._fail(error)


if __name__ == "__main__":
    raise SystemExit("Offline startup offer only; no publication, service or App action enabled.")
