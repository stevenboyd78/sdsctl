#!/usr/bin/env python3
"""Explicit App Ready -> original host permission -> one existing Relay begin.

Uninstalled leaf policy. The direct Start/Launch and service gates remain exact
and unchanged. Actual recording returns, full active observations, worker/init
exits and App restoration remain separate required obligations.
"""

from __future__ import annotations

import os
from contextlib import suppress
from threading import Lock, get_ident

import supplemental_recording_app_execution as execution
import supplemental_recording_host_begin as begin

launch, require = execution.launch, begin.require


class AppStart(begin.Start):
    """Borrow the original AppLaunch, never a reconstructed ready report.

    Shares Start's bounded journal/ledger/probe/Relay algorithms, not its direct
    policy constructor. The App-specific exact type, original publication and
    one-use controller joins are checked throughout the lifecycle. Failure
    explicitly closes the original AppLaunch transport while retaining pidfds;
    direct Start's exact-Launch cleanup cannot provide this for an App leaf.
    """

    def __init__(self, run, ledger):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.files_lock = Lock()
        self.used = self.failed = self.closed = False
        self.probe = self.authorization = self.intent = self.relay = None
        self._begin_result = None
        self.run = self.original_run = None
        try:
            require(type(self) is AppStart and type(run) is execution.AppLaunch)
            require(run.begin_attempted is False)
            run.begin_attempted = True
            require(run.begin_owner is None)
            run.begin_owner = self
            self.run = self.original_run = run
            require(type(ledger) is begin.binding.Ledger)
            require(run.used and run.confirm_attempted and not run.failed and not run.closed)
            require(type(run.ready) is launch.received.Ready)
            require(type(run.probe) is launch.probe_exec.Sample and run.probe.ready is run.ready)
            require(type(run.read) is launch.BootstrapHost)
            require(type(run.qualify) is execution.readiness.NativeReadyQualification)
            require(run.ready_qualification is run.qualify)
            require(run.qualify.elapsed_seconds is not None and run.qualify.consumption is not None)
            require(run.read.docker is run.qualify.docker)
            require(ledger.directory == run.plan.root / "recording-ledger")
            require(ledger.binding == run.pins.host)
            self.ledger = ledger
            self.plan, self.ready = run.plan, run.ready
            self.plan_pin = begin.plans.PinnedPlan(self.plan)
            self.objects = (
                run.plan,
                run.projected,
                run.journal,
                run.ready,
                run.read,
                run.qualify,
                run.idle,
                run.witness,
                run.client,
                run.probe,
                ledger,
            )
            self.original = (
                self.plan.raw,
                run.pins,
                run.command,
                run.launch_sha256,
                run.profile_sha256,
                self.ready.ready_raw,
                self.ready.context_raw,
                self.ready.received_at,
                self.ready.ready_by,
                self.ready.watch_deadline,
                ledger.directory,
                ledger.binding,
                ledger._directory_identity,
            )
            self.history = tuple(begin.base.encode(entry) for entry in run.journal.entries)
            self.prepared = ledger.state
            self.proof = self._ready_proof()
            self._guard()
            self._ledger()
        except BaseException as error:
            self._fail(error)

    def _identity(self):
        require(type(self) is AppStart and type(self.original_run) is execution.AppLaunch)
        require(self.run is self.original_run)
        require(self.run.begin_attempted is True and self.run.begin_owner is self)
        require(type(self.run.qualify) is execution.readiness.NativeReadyQualification)
        # This deliberately does not repoll pre-begin readiness. The inherited
        # pre-begin guard or retained-history path establishes its own phase.
        return super()._identity()

    def _fail(self, error):
        self.failed = True
        if type(self.original_run) is execution.AppLaunch:
            with suppress(Exception):
                self.original_run._fail(error)
        if not isinstance(error, Exception):
            raise error
        raise begin.UnconfirmedHostBegin(begin.MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled App begin join only; no live recording enabled.")
