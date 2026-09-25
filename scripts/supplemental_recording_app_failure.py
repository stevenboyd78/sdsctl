#!/usr/bin/env python3
"""Read-only App failure evidence after independently confirmed worker/init exits.

Uninstalled. These exact leaves retain the original App publication and acquired
Ready, but never refresh live readiness after failure. Preserved and pristine
evidence remain separate; neither authorizes a recording or App action. Failure
before acquisition/qualification of original Ready requires separate review.
"""

from __future__ import annotations

import hashlib
import json
import os
from threading import Lock, get_ident

import supplemental_recording_app_begin as app_begin

begin, execution = app_begin.begin, app_begin.execution
launch, require = app_begin.launch, begin.require
plans, reconcile = begin.plans, begin.worker_exit.reconcile


class _AppFailure:
    """Closed admission over the existing full host/file observation mechanism."""

    def __init__(self, reader, run):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = False
        self.end = None
        try:
            self.reader, self.run, self.plan = reader, run, reader.plan
            self.docker = run.read.docker
            self.origins = self._objects()
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _objects(self):
        run = self.run
        return (
            self.reader,
            run,
            run.plan,
            run.read,
            run.read.docker,
            run.qualify,
            run.pins,
            run.app_objects,
            run.input_pins,
            run.plan_pin,
            run.ready,
            run.client,
            run.ready_qualification,
            run.begin_owner,
        )

    def _guard(self):
        if type(self) in (AppPreservedHost, AppPreservedRestoredHost):
            require(type(self.reader) is reconcile.Preserved)
        else:
            require(type(self) in (AppNeverAuthorizedHost, AppNeverAuthorizedRestoredHost))
            require(type(self.reader) is reconcile.NeverAuthorized)
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        reader, run = self.reader, self.run
        require(type(run) is execution.AppLaunch)
        require(all(a is b for a, b in zip(self._objects(), self.origins, strict=True)))
        require(run.owner == self.owner and run.used and run.confirm_attempted)
        require(run.plan is reader.plan is self.plan and run.journal is reader.journal)
        require(run.pins is reader.pins and run.projected == reader.pins.host.projection)
        require(type(run.read) is launch.BootstrapHost)
        require(type(self.docker) is plans.ordinary.Docker)
        require(self.docker.path == "/var/run/docker.sock")
        original, q, ready = run.prelaunch, run.ready_qualification, run.ready
        require(type(original) is execution.inputs.NativeLaunchQualification)
        require(type(q) is execution.readiness.NativeReadyQualification)
        require(type(ready) is launch.received.Ready)
        require(run.qualify is q and q.prelaunch is original and q.ready is ready)
        require(run.ready is run._owned_ready and run.client is run._owned_client)
        require(ready.client is run.client and run.client.claim is run.claim)
        require(run.claim.pins is run.pins and self.docker is q.docker is original.docker)
        require(type(original.candidate) is execution.inputs.qualification.NativeIdleQualification)
        holder = original.candidate
        require(holder.native_execution_used is True and holder.native_execution_owner is run)
        require(holder.native_ready_used is True and holder.native_ready_owner is q)
        require(holder.native_launch_publication is original.launch_inputs)
        require(original.plan is q.plan is self.plan)
        require(original.startup is q.startup and original.startup.projected is run.projected)
        require(q.candidate is holder and q.launch_inputs is original.launch_inputs)
        require(original.idle is q.idle is run.idle)
        require(original.witness is q.witness is run.witness)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        original,
                        original.startup,
                        holder,
                        original.launch_inputs,
                        original.consumption,
                        run.journal,
                        run.endpoint,
                        run.read,
                        run.plan,
                        run.projected,
                        run.idle,
                        run.witness,
                        self.docker,
                    ),
                    run.app_objects,
                    strict=True,
                )
            )
        )
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        original,
                        original.startup,
                        holder,
                        original.launch_inputs,
                        original.consumption,
                    ),
                    q.prelaunch_objects,
                    strict=True,
                )
            )
        )
        require(
            all(
                a is b
                for a, b in zip(
                    (ready, ready.client, ready.processes, ready.clock, ready.zero_domain),
                    q.ready_objects,
                    strict=True,
                )
            )
        )
        # Only immutable pins/identities are examined here. _pins(), _binding(),
        # _app_context(), Startup/Idle and Ready checks require live actors and
        # must NOT be called on this exited branch, even if previously healthy.
        require(type(run.plan_pin) is plans.PinnedPlan)
        run.plan_pin.check(self.plan)
        require(original.original is run.input_pins is q.prelaunch_pins)
        require(original._launch_pins() == original.launch_pins)
        require(original.launch_pins == run.input_pins[-4:])
        require(run.launch_sha256 == original.launch_inputs.sha256)
        require(run.profile_sha256 == json.loads(original.launch_inputs.raw)["profile"]["sha256"])
        require(q.expected == run.pins and q.expected.payload() == q.expected_payload)
        require(run.command == run.pins.command)
        require(run.pins.command.plan_sha256 == run.launch_sha256)
        require(
            (ready.context_raw, ready.ready_raw, ready.received_at, ready.ready_by) == q.ready_pins
        )
        require(ready.context_raw == q.expected_context)
        require(
            ready.context_raw
            == begin.base.encode(
                launch.received._context(
                    run.pins,
                    run.profile_sha256,
                )
            )
        )
        require(ready.clock is self.plan.original_clock)
        require(ready.ready_by == self.plan.lease["ready_by"])
        require(reader.operator.plan is self.plan and reader.operator.clock is ready.clock)
        require(reader.operator.ready_sha256 == hashlib.sha256(ready.ready_raw).hexdigest())
        require(
            run.read.plan is self.plan
            and run.read.projected is run.projected
            and run.read.idle is run.idle
            and run.read.witness is run.witness
        )
        reader._context()


class AppPreservedHost(_AppFailure, begin.PreservedHost):
    """Original unconfirmed recording, retained files and actual exited candidate."""


class AppNeverAuthorizedHost(_AppFailure, begin.NeverAuthorizedHost):
    """Original prepared-only ledger and pristine files, never attempted recording."""


class AppPreservedRestoredHost(begin._RestoredNormal, AppPreservedHost):
    """Fresh normal health never promotes the original unconfirmed recording."""


class AppNeverAuthorizedRestoredHost(begin._RestoredNormal, AppNeverAuthorizedHost):
    """Fresh normal health keeps the recording truthfully not attempted."""


if __name__ == "__main__":
    raise SystemExit("Uninstalled App failure evidence only; no recovery actions enabled.")
