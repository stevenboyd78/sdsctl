#!/usr/bin/env python3
"""Finite original-owner startup custody, uninstalled and without App actions.

Connects retained declaration, original clock, exclusive plan publication and
independent acceptance. No Engine, journal, service, native worker, scanner or
recording operation is selected. Accepted bytes are NOT installed qualification
or operator approval. The caller must keep this owner alive until any later
borrowers have released its original plan and clock handles.
"""

from __future__ import annotations

import os
from threading import Lock, get_ident

import supplemental_recording_service_acceptance as acceptance
import supplemental_recording_service_declaration as declaration

publication, offers = acceptance.publication, acceptance.offers
plans = declaration.codec.plans
MESSAGE = "Recording startup is unconfirmed; preserve the case and do not restart it."


class UnconfirmedStartup(ValueError):
    """No ambiguous startup result is permission to construct another owner."""


def require(value):
    if not value:
        raise UnconfirmedStartup(MESSAGE)


class Startup:
    """One prepare/accept attempt, owning clock/models/plan but not declaration.

    Construction claims the original caller-owned Declaration in memory without
    capturing a clock or writing. prepare() explicitly captures ONE original
    clock and exclusively publishes its offer. poll() never renews the original
    offer's finite bound; acceptance returns the same retained CasePlan.

    For later separately qualified service assembly, accepted_input() rechecks
    the original accepted input and clock. IdleService may BORROW these exact
    handles. Close this startup owner only AFTER that borrower is closed. There
    is no service assembly, readiness, recording or restoration claim here.
    """

    def __init__(self, original):
        self.owner = os.getpid(), get_ident(), os.geteuid(), os.getegid()
        self.lock, self._cleanup = Lock(), []
        self.failed = self.closed = self.used = self.accepted = False
        self.clock = self.offer = self.publisher = self.original = self.reader = None
        try:
            require(type(original) is declaration.Declaration)
            require(original.owner == self.owner and original.startup_owner is None)
            self.declaration = self._declaration = original
            original.startup_owner = self
            self.template = self._template = original.recheck()
            self.expected = self._expected = original.expected
            self._input()
        except BaseException as error:
            self._fail(error)

    def _state(self):
        require(not self.failed and not self.closed)
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        require(self.declaration is self._declaration and self.template is self._template)
        require(self.expected == self._expected)
        require(self.declaration.startup_owner is self)
        require(not self.declaration.failed and not self.declaration.closed)

    def _input(self):
        self._state()
        require(self.declaration.recheck() is self.template)
        require(self.template.sha256 == self.expected)
        self._state()

    def _binding(self):
        self._state()
        require(self.used)
        require(
            all(
                a is b
                for a, b in zip(
                    (self.clock, self.offer, self.publisher, self.original, self.reader),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self.offer.clock_witness is self.clock)
        require(self.publisher.published is self.original)
        require(self.reader.publisher is self.publisher)
        require(not self.clock.closed and not self.clock.failed)
        require(not self.reader.closed and not self.reader.failed)
        require(self.reader.accepted is self.accepted)

    def _guard(self):
        self._input()
        self._binding()
        self.reader._guard(
            acceptance.time.monotonic() + acceptance.MAX_SECONDS, accepted=self.accepted
        )
        self._input()
        self.offer._check()
        self._binding()

    def prepare(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            self._input()
            require(not self.used)
            self.used = True
            self.clock = plans.clock.ClockWitness(plans.clock.read())
            self._cleanup.append(self.clock.close)
            self._input()
            self.offer = offers.Offer(self.template, self.expected, self.clock)
            self._cleanup.append(self.offer.close)
            self.publisher = publication.Publisher(self.offer)
            self.original = self.publisher.publish()
            self._cleanup.append(self.original.close)
            self.reader = acceptance.Acceptance(self.publisher)
            self._cleanup.append(self.reader.close)
            self.objects = self.clock, self.offer, self.publisher, self.original, self.reader
            self._guard()
            return self.original
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def poll(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.accepted)
            self._guard()
            result = self.reader.poll()
            if result is not None:
                self.accepted = True
                require(result is self.offer.plan and result.raw == self.original.recheck().raw)
            self._guard()
            return self.original if self.accepted else None
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def accepted_input(self):
        """Original accepted handles only; never service/action authorization."""
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.accepted)
            self._guard()
            return self.original
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()):
            try:
                self.close()
            except BaseException as cleanup:
                if isinstance(error, Exception) and not isinstance(cleanup, Exception):
                    raise cleanup
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedStartup(MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
        if self.closed:
            return
        self.closed = True
        error = None
        while self._cleanup:
            callback = self._cleanup.pop()
            try:
                callback()
            except BaseException as problem:
                if (
                    error is None
                    or isinstance(error, Exception)
                    and not isinstance(problem, Exception)
                ):
                    error = problem
        if error is not None:
            self.failed = True
            if not isinstance(error, Exception):
                raise error
            raise UnconfirmedStartup(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled startup custody library only; no service command enabled.")
