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
import sys
import time
from pathlib import Path
from threading import Lock, get_ident

PROBE_MESSAGE = "Finite recording startup observation ended; preserve this case and do not restart."

# Direct execution is only the explicitly named action-free observation mode.
# This command is not selected by existing HelperQualification or an App/service.
# Never bootstrap imports from cwd or an environment-controlled helper location.
if __name__ == "__main__":
    try:
        allowed = (
            len(sys.argv) == 4
            and sys.argv[3] == "--startup-probe"
            and sys.flags.isolated == sys.flags.dont_write_bytecode == 1
            and os.geteuid() == os.getegid() == 0
            and os.getcwd() == "/"
            and Path(__file__)
            == Path("/opt/sdsctl-recording-host/supplemental_recording_service_startup.py")
        )
    except Exception:
        allowed = False
    if not allowed:
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(64)
    sys.path.insert(0, "/opt/sdsctl-recording-host")

try:
    import supplemental_recording_service_acceptance as acceptance
    import supplemental_recording_service_declaration as declaration
except Exception:
    if __name__ == "__main__":
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise

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


def startup_probe(root, expected_sha256):
    """Finite original-owner observation, not an installed recording service.

    Unlike the older passive read probe, this explicitly publishes a new
    synthetic-or-independently-provisioned case's claim and clock-bound plan.
    It may consume one independently submitted acceptance, but performs no
    Engine request, journal, operator notice, native launch or scanner action.
    All original handles stay alive during this observation, even after
    acceptance. It retires before the original offer's at-most-15-second bound,
    reserving the last two seconds rather than starting another bounded read at
    the expiry edge. The bound is never restarted or extended.
    A separate supervisor must bound blocked kernel I/O. No signal is masked.
    Exit75 proves neither acceptance nor readiness; files remain for inspection.
    """
    with declaration.Declaration(root, expected_sha256) as original:
        owner = Startup(original)
        try:
            owner.prepare()
            # Observation may end early; it cannot claim a successful final
            # custody read at expiry. Reserve one acceptance I/O budget instead
            # of repeatedly beginning fresh reads immediately before expiry.
            end = owner.offer.deadline - acceptance.MAX_SECONDS
            for _ in range(151):
                # Expiry only ends this non-authorizing observation. It is not
                # a successful custody check or a return of accepted handles.
                if time.monotonic() >= end:
                    return 75
                if owner.accepted:
                    owner.accepted_input()
                else:
                    owner.poll()
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return 75
                time.sleep(min(0.1, remaining))
            raise UnconfirmedStartup(MESSAGE) from None
        finally:
            owner.close()


if __name__ == "__main__":
    try:
        root = Path(sys.argv[1])
        require(str(root) == sys.argv[1] and root.is_absolute() and ".." not in root.parts)
        code = startup_probe(root, sys.argv[2])
    except Exception:
        print(PROBE_MESSAGE, file=sys.stderr)
        raise SystemExit(75) from None
    raise SystemExit(code)
