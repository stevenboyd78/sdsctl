#!/usr/bin/env python3
"""Explicit App Ready -> original host permission -> one existing Relay begin.

Uninstalled leaf policy. The direct Start/Launch and service gates remain exact
and unchanged. Actual recording returns, full active observations, worker/init
exits and App restoration remain separate required obligations.
"""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from threading import Lock, get_ident

import supplemental_recording_app_execution as execution
import supplemental_recording_host_begin as begin

launch, require = execution.launch, begin.require
dispatch, namespace = launch.engine.dispatch, launch.engine.namespace


@dataclass(frozen=True)
class NativeNotice:
    """Pre-begin comparison facts, not independent custody or action permission.

    AppStart or AppNativePhase derives this from its original authenticated
    Ready. A receiver must reconstruct history/dispatch and capture actual actors;
    serialized hints or a matching receipt alone cannot supply that evidence.
    """

    pins: dispatch.Pins
    execution_id: str
    dispatch_sha256: str
    ready_sha256: str
    ready_proof: str
    probe_execution_id: str
    probe_request_sha256: str
    actors: tuple[namespace.Actor, ...]
    history: tuple[bytes, ...]

    def __post_init__(self):
        require(type(self.pins) is dispatch.Pins)
        self.pins.payload()
        for value in (
            self.execution_id,
            self.dispatch_sha256,
            self.ready_sha256,
            self.ready_proof,
            self.probe_execution_id,
            self.probe_request_sha256,
        ):
            begin.base.digest(value)
        require(type(self.actors) is tuple and len(self.actors) == 4)
        for actor in self.actors:
            require(type(actor) is namespace.Actor)
            actor.__post_init__()
            require(actor.container_id == self.pins.init.container_id)
        require(len({actor.host_pid for actor in self.actors}) == 4)
        require(len({actor.local_pid for actor in self.actors}) == 4)
        require(self.actors[0].host_pid == self.pins.init.pid)
        require(self.actors[0].start_ticks == self.pins.init.start_ticks)
        require(type(self.history) is tuple and 0 < len(self.history) <= 64)
        require(
            all(type(raw) is bytes and 0 < len(raw) <= begin.base.MAX_BYTES for raw in self.history)
        )

    @property
    def receipt(self):
        return begin.base.checksum(
            dict(
                pins=self.pins.payload(),
                execution_id=self.execution_id,
                dispatch_sha256=self.dispatch_sha256,
                ready_sha256=self.ready_sha256,
                ready_proof=self.ready_proof,
                probe_execution_id=self.probe_execution_id,
                probe_request_sha256=self.probe_request_sha256,
                actors=[asdict(actor) for actor in self.actors],
                history=[raw.hex() for raw in self.history],
            )
        )


def native_notice(run, proof):
    """Comparison only; callers must guard their distinct original phase first."""
    require(type(run) is execution.AppLaunch)
    require(type(run.ready.processes) is namespace.Witness)
    actors = run.ready.processes.refresh()
    state = run.client.claim.state
    require(type(state) is dispatch.State and state.count == 3)
    require(state.phase == "attach_intent")
    return NativeNotice(
        run.pins,
        state.execution_id,
        state.sha256,
        hashlib.sha256(run.ready.ready_raw).hexdigest(),
        proof,
        run.probe.execution_id,
        run.probe.request_sha256,
        actors,
        tuple(begin.base.encode(entry) for entry in run.journal.entries),
    )


class AppStart(begin.Start):
    """Borrow the original AppLaunch, never a reconstructed ready report.

    Shares Start's bounded journal/ledger/probe/Relay algorithms, not its direct
    policy constructor. The App-specific exact type, original publication and
    one-use controller joins are checked throughout the lifecycle. Failure
    explicitly closes the original AppLaunch transport while retaining pidfds;
    direct Start's exact-Launch cleanup cannot provide this for an App leaf.
    """

    def __init__(self, run, ledger, *, native_observer=None):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.files_lock = Lock()
        self.used = self.failed = self.closed = False
        self.probe = self.authorization = self.intent = self.relay = None
        self._begin_result = None
        self.run = self.original_run = None
        try:
            require(type(self) is AppStart and type(run) is execution.AppLaunch)
            require(native_observer is None or callable(native_observer))
            self.native_observer = native_observer
            self.observer_objects = (native_observer,)
            self.native_observation_attempted = False
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
        require(self.native_observer is self.observer_objects[0])
        require(self.run is self.original_run)
        require(self.run.begin_attempted is True and self.run.begin_owner is self)
        require(type(self.run.qualify) is execution.readiness.NativeReadyQualification)
        # This deliberately does not repoll pre-begin readiness. The inherited
        # pre-begin guard or retained-history path establishes its own phase.
        return super()._identity()

    def _native_notice(self):
        self._guard()
        self._ledger()
        require(self.authorization is self.intent is self.relay is None)
        require(self._ready_proof() == self.proof)
        return native_notice(self.run, self.proof)

    def _observe(self):
        """Selected pre-begin hook stays inside the fresh qualification bracket.

        Optional only for existing uninstalled compositions. The active launcher
        still must select an authenticated independent custody exchange and its
        separate action scope. No retry, budget renewal or implied permission.
        """
        observation = super()._observe()
        observer = self.native_observer
        if observer is None:
            return observation
        require(not self.native_observation_attempted)
        self.native_observation_attempted = True
        end = min(time.monotonic() + 2, self.ready.ready_by)
        notice = self._native_notice()
        receipt = notice.receipt
        require(observer(notice) == receipt)
        self._identity()
        require(self.native_observer is observer)
        require(self._native_notice().receipt == receipt and notice.receipt == receipt)
        require(time.monotonic() < end)
        return observation

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
