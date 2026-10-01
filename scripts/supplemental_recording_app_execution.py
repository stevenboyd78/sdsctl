#!/usr/bin/env python3
"""Explicit, uninstalled App launch controller; no recording or service selection.

The direct Launch and Start exact-type gates remain unchanged. This separate
leaf shares the existing journal/dispatch/confirmation algorithm, but owns an
explicit one-way transition from original App inputs to original native Ready.
No passive command imports or selects this controller. Installed provenance,
independent supervision and recovery remain required integration obligations.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import asdict

import qualify_supplemental_recording_app_ready as readiness

inputs, launch = readiness.inputs, readiness.launch
require = launch.require


class AppLaunch(launch.Launch):
    """One original App publication -> durable exec intent -> qualified Ready.

    Construction consumes the original candidate's one controller slot; another
    passive wrapper cannot grant another attempt. No I/O action occurs until
    start_confirmed(). Only the combined launch path is supported: accepting
    actual Ready installs its socket-aware qualifier before the fresh complete
    source/runtime bracket and original two-second journal confirmation.

    Launch owns its acquired transport/Ready/probe handles, just as the direct
    controller does. Startup, input readers, journal, clock, idle and init
    witness remain borrowed. Failure retains process handles, not a retry.
    """

    def __init__(self, original, journal, endpoint, *, read):
        self.owner = os.getpid(), launch.get_ident()
        self.used = self.failed = self.closed = self.confirm_attempted = False
        self.probe = self.action = self.claim = self.client = self.ready = None
        self.ready_qualification = None
        self.begin_attempted, self.begin_owner = False, None
        self._owned_client = self._owned_ready = None
        self._failure_cleanup = False
        try:
            require(type(self) is AppLaunch)
            require(type(original) is inputs.NativeLaunchQualification)
            holder = original.candidate
            require(type(holder) is inputs.qualification.NativeIdleQualification)
            require(holder.native_execution_used is False)
            holder.native_execution_used = True
            require(holder.native_execution_owner is None)
            holder.native_execution_owner = self
            require(not holder.native_ready_used and holder.native_ready_owner is None)
            require(not original.failed and not original.lock.locked())
            require(original.elapsed_seconds is not None and original.consumption is not None)
            require(type(read) is launch.BootstrapHost)
            require(read.docker is original.docker)
            self.prelaunch = original
            self.plan_pin = launch.plans.PinnedPlan(original.plan)
            self.app_objects = (
                original,
                original.startup,
                holder,
                original.launch_inputs,
                original.consumption,
                journal,
                endpoint,
                read,
                original.plan,
                original.startup.projected,
                original.idle,
                original.witness,
                original.docker,
            )
            self.input_pins = original.original
            super().__init__(
                original.plan,
                original.startup.projected,
                journal,
                original.idle,
                original.witness,
                endpoint,
                launch_sha256=original.launch_inputs.sha256,
                profile_sha256=json.loads(original.launch_inputs.raw)["profile"]["sha256"],
                read=read,
                qualify=original,
            )
        except BaseException as error:
            self._fail(error)

    def _app_context(self):
        """Original identities only; never poll an expired acceptance offer."""
        require(type(self) is AppLaunch)
        require(not self.failed and not self.closed)
        require(self.owner == (os.getpid(), launch.get_ident()))
        require(self.client is self._owned_client and self.ready is self._owned_ready)
        original = self.prelaunch
        require(type(original) is inputs.NativeLaunchQualification)
        require(not original.failed and not original.lock.locked())
        require(original.candidate.native_execution_used is True)
        require(original.candidate.native_execution_owner is self)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        original,
                        original.startup,
                        original.candidate,
                        original.launch_inputs,
                        original.consumption,
                        self.journal,
                        self.endpoint,
                        self.read,
                        self.plan,
                        self.projected,
                        self.idle,
                        self.witness,
                        self.read.docker,
                    ),
                    self.app_objects,
                    strict=True,
                )
            )
        )
        self.plan_pin.check(self.plan)
        require(original._pins() == original.original == self.input_pins)
        require(self.launch_sha256 == original.launch_inputs.sha256)
        require(self.profile_sha256 == json.loads(original.launch_inputs.raw)["profile"]["sha256"])
        require(type(self.read) is launch.BootstrapHost and not self.read.failed)
        require(
            self.read.plan is self.plan
            and self.read.projected is self.projected
            and self.read.idle is self.idle
            and self.read.witness is self.witness
        )
        if self.ready_qualification is None:
            require(self.qualify is original)
            require(original.candidate.native_ready_used is False)
            require(original.candidate.native_ready_owner is None)
        else:
            q = self.ready_qualification
            require(type(q) is readiness.NativeReadyQualification)
            require(self.qualify is q and q.prelaunch is original and q.ready is self.ready)
            require(not q.failed)
            require(original.candidate.native_ready_owner is q)
            q._binding()

    def _guard(self, phase):
        self._app_context()
        return super()._guard(phase)

    def _candidate_qualifier(self):
        self._app_context()
        q = self.qualify
        require(q.plan is self.plan and q.idle is self.idle and q.witness is self.witness)
        require(q.generation == self.pins.generation and q.init == self.pins.init)
        return q

    def _start(self, *, combined):
        # Keep the direct algorithm's ordering explicit here. In particular,
        # do not teach its generic exact-type gates to accept App subclasses.
        try:
            require(combined is True and not self.used)
            self.used = True
            self._candidate_qualifier()
            evidence, sample, _, now = self._sample("candidate_idle")
            event = dict(
                kind="authorize_operator",
                boot_id=self.plan.boot,
                now=now,
                generation=self.pins.generation,
                bootstrap_sha256=self.plan.bootstrap.sha256,
                launch_plan_sha256=self.command.plan_sha256,
                idle_evidence_sha256=evidence.sha256,
                observation=asdict(sample.observation),
            )
            self.action = self.journal.append(event)
            require(type(self.action) is launch.bootstrap.OperatorAction)
            require(self.action.intent_sha256 == launch.base.checksum(event))
            _, fresh, machine, second = self._sample("starting_operator")
            require(0 <= second - now <= 2)
            require(machine.preconditions(fresh.observation) == self.action.preconditions_sha256)
            self.claim = launch.engine.dispatch.Claim(
                self.plan.root / "operator-exec", self.pins, self.witness
            )
            self.client = launch.engine.Client(self.endpoint, self.claim)
            self._owned_client = self.client
            self._qualified("starting_operator")
            self.client.create()
            self._qualified("starting_operator")
            self.client.attach(finish_by=self.plan.lease["stop_by"])
            self.ready = launch.received.Ready(
                self.client,
                profile_sha256=self.profile_sha256,
                original_clock=self.plan.original_clock,
                zero_domain=self.idle.zero_domain,
            )
            self._owned_ready = self.ready
            require(self.ready_qualification is None and self.qualify is self.prelaunch)
            self.ready_qualification = readiness.NativeReadyQualification(
                self.prelaunch, self.ready
            )
            self.qualify = self.ready_qualification
            return self._confirm_ready(combined=True)
        except BaseException as error:
            self._fail(error)

    def confirm_ready(self):
        """A split/replayed ready transition is not supported by this policy."""
        self._fail(ValueError(launch.MESSAGE))

    def _fail(self, error):
        self.failed = True
        # Nested failure wrappers must not retry an uncertain close. Use the
        # originally acquired handles even if a public binding was substituted.
        if not self._failure_cleanup:
            self._failure_cleanup = True
            objects = getattr(self, "app_objects", None)
            if objects is not None:
                collector = objects[7]
                if type(collector) is launch.BootstrapHost and collector.owner == self.owner:
                    with suppress(Exception):
                        collector.discard()
            if self._owned_ready is not None:
                self._owned_ready.failed = True
            if self._owned_client is not None:
                with suppress(Exception):
                    self._owned_client.close()
        if not isinstance(error, Exception):
            raise error
        raise launch.UnconfirmedHostLaunch(launch.MESSAGE) from None

    def close(self):
        require(self.owner == (os.getpid(), launch.get_ident()))
        if self.closed:
            return
        self.closed = True
        objects = getattr(self, "app_objects", None)
        if objects is not None:
            objects[7].discard()
        if self.probe is not None:
            self.probe.close()
        if self._owned_ready is not None:
            self._owned_ready.close()
        elif self._owned_client is not None and not self._failure_cleanup:
            self._owned_client.close()


if __name__ == "__main__":
    raise SystemExit("Uninstalled App controller only; no live launch enabled.")
