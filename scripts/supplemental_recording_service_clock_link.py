#!/usr/bin/env python3
"""Uninstalled observer-side comparison for a service-owned original clock.

The observer retains its OWN ClockWitness and a live ZeroDomain for the original
target. Both MONOTONIC and BOOTTIME offsets must already be proven zero by that
kernel witness. No Window is relabeled, no namespace is entered, and no clock
is changed. Existing strict plan checks and installed policies are unchanged.
This does not attest that a submitted service sample was produced by trusted
code; source/runtime/command/process/template provenance remain separate gates.
"""

from __future__ import annotations

import os
import time
from threading import Lock, get_ident

import supplemental_recording_host_plan as plans
import supplemental_recording_time_domain as domains

MAX_SECONDS = 1
MESSAGE = (
    "Recording startup clock comparison is unconfirmed; preserve original custody and deadlines."
)


class UnconfirmedClockLink(ValueError):
    """No comparison result implies startup, recording or recovery authority."""


def require(value):
    if not value:
        raise UnconfirmedClockLink(MESSAGE)


class ObserverClock:
    """Borrow original live observer/domain witnesses for one startup plan.

    ZeroDomain retains both namespace descriptors and the original target pidfd.
    Its local original Window must be the caller's exact retained observer clock;
    its target namespace and incarnation must match the service plan and the
    independently bound target. Every read revalidates those live witnesses.
    The returned Window stays in the OBSERVER domain. It must not be passed off
    as a service-side sample or used to reconstruct the service's ClockWitness.
    This class owns no descriptors and closes neither caller-owned witness.
    """

    def __init__(self, plan, observer_clock, zero_domain, target):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = False
        try:
            self.pin = plans.PinnedPlan(plan)
            require(type(observer_clock) is plans.clock.ClockWitness)
            require(type(zero_domain) is domains.ZeroDomain)
            require(type(target) is domains.process.ProcessIdentity)
            target.__post_init__()
            self.plan, self.observer, self.domain, self.target = (
                plan,
                observer_clock,
                zero_domain,
                target,
            )
            self.local_original = observer_clock.original
            self.objects = plan, observer_clock, zero_domain, target, self.local_original
            self.proof = zero_domain.refresh()
            require(type(self.proof) is domains.Evidence)
            self.proof_sha256 = self.proof.sha256
            self.read()
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedClockLink(MESSAGE) from None

    def _binding(self, end):
        require(not self.failed and not self.closed and time.monotonic() < end)
        require(self.owner == (os.getpid(), get_ident()))
        require(
            all(
                a is b
                for a, b in zip(
                    (self.plan, self.observer, self.domain, self.target, self.local_original),
                    self.objects,
                    strict=True,
                )
            )
        )
        self.pin.check(self.plan)
        require(time.monotonic() < self.plan.lease["ready_by"])
        require(type(self.observer) is plans.clock.ClockWitness)
        require(not self.observer.closed and not self.observer.failed)
        require(self.observer.original is self.local_original)
        require(type(self.domain) is domains.ZeroDomain)
        require(not self.domain.closed and not self.domain.failed)
        require(time.monotonic() < end)

    def _guard(self, end):
        self._binding(end)
        proof = self.domain.refresh()
        # The live witness is an I/O boundary. Check the original binding again,
        # including on the final bracket, before accepting its observation.
        self._binding(end)
        require(type(proof) is domains.Evidence and proof is self.proof)
        require(proof.sha256 == self.proof_sha256)
        require(proof.original_clock is self.local_original)
        require(proof.host_time == self.local_original.namespace)
        require(proof.native_time == self.plan.original_clock.namespace)
        require(plans._same_plan_value(proof.init, self.target))
        require(time.monotonic() < end)

    def _compare(self, observed):
        # Only called inside a bracket of actual live zero-offset proof. The
        # namespace identities are checked separately, never overwritten to
        # trick the ordinary strict Window.check_later / Plan.check_clock path.
        original = self.plan.original_clock
        require(type(observed) is plans.clock.Window)
        original.__post_init__()
        observed.__post_init__()
        require(observed.namespace == self.local_original.namespace)
        require(original.namespace == self.proof.native_time)
        require(original.boot == observed.boot == self.local_original.boot == self.plan.boot)
        require(observed.before_ns >= original.after_ns)
        require(observed.boottime_ns >= original.boottime_ns)
        # Zero namespace offsets do NOT imply BOOTTIME == MONOTONIC: both
        # domains still include the same accumulated suspend time. Preserve the
        # original measured offset interval and refuse a subsequent jump.
        require(
            max(original.offset[0], observed.offset[0])
            <= min(original.offset[1], observed.offset[1])
        )
        require(observed.boottime_ns / plans.clock.NS < self.plan.deadlines.ready_by)

    def read(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            end = time.monotonic() + MAX_SECONDS
            self._guard(end)
            before = self.observer.read()
            self._compare(before)
            self._guard(end)
            after = self.observer.read()
            before.check_later(after)
            self._compare(after)
            self._guard(end)
            require(time.monotonic() < end)
            return after
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        self.closed = True


if __name__ == "__main__":
    raise SystemExit("Read-only observer clock library only; no installed clock policy enabled.")
