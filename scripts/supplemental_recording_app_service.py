#!/usr/bin/env python3
"""Explicit, uninstalled App-aware service driver through recording recovery.

No command selects this owner. It borrows the original idle service, runs ONE
top-level loop, and retires idle reads before native input publication. Recording
handoff is separately explicit and one-use. Independent outer supervision remains
mandatory; synthetic tests are not installed provenance or scanner acceptance.
"""

from __future__ import annotations

import os
import time
from contextlib import suppress

import supplemental_recording_app_candidate as candidates
import supplemental_recording_app_execution as execution
import supplemental_recording_app_recovery as recovery
from supplemental_handoff_host import CandidateNotice

operator = candidates.operator
inputs, launch, require = candidates.inputs, operator.launch, operator.require
begin, base = operator.begin, operator.base


class AppService:
    """One explicit reservation of an unused ORIGINAL IdleService.

    This is not a subclass accepted by direct policy. Direct native/recording
    slots stay empty, and the original run() cannot also drive this service.
    Readiness/capture uncertainty leaves the same session's clock ticks alive.
    Custody substitution still refuses; it is not an observation failure.
    """

    def __init__(self, startup, original, *, candidate_observer=None, native_observer=None):
        require(type(self) is AppService and type(original) is operator.IdleService)
        require(candidate_observer is None or callable(candidate_observer))
        require(native_observer is None or callable(native_observer))
        original._context()
        require(not original.used and not original.lock.locked())
        require(original._app_driver is None and not original.candidate_attempted)
        require(type(startup) is inputs.publication.startup.Startup)
        require(startup.original is original.original and startup.projected is original.projected)
        require(startup.clock is original.clock_witness)
        inputs._service_custody(startup, original.plan)
        self.owner, self.startup, self.original = original.owner, startup, original
        self.plan, self.projected, self.journal = (
            original.plan,
            original.projected,
            original.journal,
        )
        self.session, self.processes, self.dispatch = (
            original.session,
            original.processes,
            original.dispatch,
        )
        self.coordinator, self.inbox, self.lock = (
            original.coordinator,
            original.inbox,
            original.lock,
        )
        self._cleanup = original._cleanup
        self.candidate_observer = candidate_observer
        self.native_observer = native_observer
        self.objects = (
            startup,
            original,
            self.plan,
            self.projected,
            self.journal,
            self.session,
            self.processes,
            self.dispatch,
            self.coordinator,
            self.inbox,
            self.lock,
            self._cleanup,
            candidate_observer,
            native_observer,
        )
        self.used = self.native_attempted = False
        self.candidate_observation_attempted = False
        self.native = self._original_native = None
        self.recording_attempted = False
        self.recording = self._original_recording = None
        original._app_driver = self
        self._context()

    def _context(self, *, recovering=False):
        require(type(self) is AppService and self.owner == (os.getpid(), operator.get_ident()))
        original = self.original
        require(type(original) is operator.IdleService and original._app_driver is self)
        original._context()
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        self.startup,
                        original,
                        original.plan,
                        original.projected,
                        original.journal,
                        original.session,
                        original.processes,
                        original.dispatch,
                        original.coordinator,
                        original.inbox,
                        original.lock,
                        original._cleanup,
                        self.candidate_observer,
                        self.native_observer,
                    ),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        self.startup,
                        original,
                        self.plan,
                        self.projected,
                        self.journal,
                        self.session,
                        self.processes,
                        self.dispatch,
                        self.coordinator,
                        self.inbox,
                        self.lock,
                        self._cleanup,
                        self.candidate_observer,
                        self.native_observer,
                    ),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self.startup.original is original.original)
        require(self.startup.clock is original.clock_witness)
        require(self.startup.projected is self.projected)
        # Continuing custody, not a renewed startup offer or native health read.
        inputs._service_custody(self.startup, self.plan)
        require(original.prepared_launch is original.native is original.recording is None)
        require(not original.launch_preparation_attempted and not original.native_attempted)
        require(not original.recording_attempted)
        require(self.native is self._original_native)
        require(self.recording is self._original_recording)
        if self.used:
            require(original.used and self.lock.locked())
        if self.native is not None:
            require(type(self.native) is AppNativePhase)
            self.native._context(recovering=recovering, retired=self.recording is not None)
            if self.recording is not None:
                from supplemental_recording_app_recording import AppRecordingPhase

                require(type(self.recording) is AppRecordingPhase)
                require(self.recording.service is self)
                self.recording._context(recovering=recovering)
        else:
            require(not self.native_attempted and not recovering and self.recording is None)

    def _clock(self):
        self._context()
        return self.objects[1]._clock()

    def _observe_candidate(self):
        """One original callback before launch publication, under the inbox lock.

        Optional for existing development compositions only. An active launcher
        must explicitly select the authenticated independent custody exchange;
        an arbitrary receipt is not action authority or complete supervision.
        The service consumes the observation slot before entering here. Idle
        ownership remains live until this check returns, then is retired even
        when the acknowledgment was lost. Never repoll a retired idle reader.
        """
        self._context()
        require(self.candidate_observation_attempted and self.native is None)
        observer = self.candidate_observer
        if observer is None:
            return
        end = time.monotonic() + 2
        candidate = self.original.candidate
        candidate.recheck()
        notice = CandidateNotice(
            self.plan.sha256,
            candidate.record.generation,
            candidate.witness.identity,
            tuple(base.encode(entry) for entry in self.journal.entries),
        )
        receipt = notice.receipt
        require(observer(notice) == receipt)
        self._context()
        require(self.candidate_observer is observer and self.original.candidate is candidate)
        candidate.recheck()
        require(
            CandidateNotice(
                self.plan.sha256,
                candidate.record.generation,
                candidate.witness.identity,
                tuple(base.encode(entry) for entry in self.journal.entries),
            )
            == notice
        )
        require(notice.receipt == receipt and time.monotonic() < end)
        require(self._clock()[1] < self.plan.deadlines.ready_by)
        require(time.monotonic() < end)

    def start_recording(self):
        """Retire native cancellation BEFORE constructing the one-use AppStart.

        This is explicit authority, never an idle notice or automatic step.
        Failed/unknown begin preserves clock expiry, not pristine recovery.
        """
        from supplemental_recording_app_recording import AppRecordingPhase

        original = self.objects[1]
        try:
            self._context()
            require(self.used and not self.recording_attempted)
            recording = AppRecordingPhase(self)
            self.recording_attempted = True
            self.native.retired = True
            self.recording = self._original_recording = recording
            self.session.read = recording.read
            return recording.start()
        except BaseException as error:
            original._fail(error)

    def observe_recording(self):
        original = self.objects[1]
        try:
            self._context()
            require(self.recording is not None)
            return self.recording.observe()
        except BaseException as error:
            original._fail(error)

    def finish_recording(self):
        original = self.objects[1]
        try:
            self._context()
            require(self.recording is not None)
            return self.recording.finish()
        except BaseException as error:
            original._fail(error)

    def abandon_recording(self):
        original = self.objects[1]
        try:
            self._context()
            require(self.recording is not None)
            return self.recording.abandon()
        except BaseException as error:
            original._fail(error)

    def prepare_candidate(self, **profile):
        self._context()
        require(self.used and not self.native_attempted)
        return candidates.prepare_candidate(self.startup, self.original, **profile)

    def start_native(self, ledger, endpoint, operator_endpoint, *, specification, profile_sha256):
        """Publish and launch once, under the original idle-publication lock.

        No future poll may return to the empty-launch idle reader after entering
        this boundary, even if publication or native acquisition fails.
        """
        self._context()
        require(
            self.used and not self.native_attempted and not self.candidate_observation_attempted
        )
        candidate = self.original.candidate
        require(type(candidate) is candidates.AppIdleCandidate)
        candidate.recheck()
        with self.inbox.native_handoff():
            phase = AppNativePhase(self, ledger, endpoint, operator_endpoint)
            candidate.recheck()
            self.candidate_observation_attempted = True
            observed = False
            try:
                self._observe_candidate()
                observed = True
            except Exception:
                pass  # Retire idle below; lost evidence never permits retry.
            self._context()
            self.native_attempted = True
            self.coordinator.finished = True
            self.native = self._original_native = phase
            self.session.read = phase.read
            return phase.start(
                specification=specification,
                profile_sha256=profile_sha256,
                candidate_observed=observed,
            )

    def cancel_native(self):
        self._context()
        require(type(self.native) is AppNativePhase and self.recording is None)
        return self.native.cancel()

    def run(self, wait):
        # Retain cleanup before callbacks can replace public owner attributes.
        original, lock = self.objects[1], self.objects[10]
        close = original.close
        acquired = False
        try:
            self._context()
            require(not self.used and not self.original.used and callable(wait))
            require(lock.acquire(blocking=False))
            acquired = True
            self.used = self.original.used = True
            for _ in range(operator.IDLE_POLL_LIMIT):
                self._context()
                phase = self.native if self.native is not None else self.coordinator
                if self.recording is not None:
                    phase = self.recording
                result = phase.poll(wait)
                if result.phase in ("complete", "review"):
                    return result
                wait(0.25)
            self._context()
            return launch.bootstrap.recovery.dispatch.Result(
                self.journal.machine.state.phase, "poll_limit_unconfirmed"
            )
        except BaseException as error:
            original._fail(error)
        finally:
            if acquired:
                try:
                    close()
                finally:
                    lock.release()


class AppNativePhase(operator.NativePhase):
    """App-only policy; share prepared-ledger and transport-cancel algorithms.

    A successful launch return is required before independent Operator capture.
    Earlier failure stays uncertain even when files remain pristine. No begin,
    retry, automatic cancellation or fabricated native exit is provided here.
    """

    def __init__(self, service, ledger, endpoint, operator_endpoint):
        require(type(self) is AppNativePhase and type(service) is AppService)
        service._context()
        require(service.used and not service.native_attempted)
        require(type(endpoint) is type(operator_endpoint) is launch.engine.Endpoint)
        require(
            endpoint is not operator_endpoint
            and not endpoint.closed
            and not operator_endpoint.closed
        )
        endpoint.check()
        operator_endpoint.check()
        require(type(ledger) is launch.binding.Ledger and not ledger._poisoned)
        self.ledger_state = ledger.state
        require(type(self.ledger_state) is launch.binding.State)
        require(
            self.ledger_state
            == launch.binding.State(1, self.ledger_state.sha256, self.ledger_state.now)
        )
        require(
            service.plan.original_clock.after_ns / operator.plans.clock.NS
            <= self.ledger_state.now
            <= time.monotonic()
        )
        self.owner, self.service = service.owner, service
        self.ledger, self.launch_endpoint, self.endpoint = ledger, endpoint, operator_endpoint
        self.ledger_identity, self.ledger_lock = ledger._directory_identity, ledger._lock
        self.candidate = service.original.candidate
        self.objects = service, ledger, endpoint, operator_endpoint, self.candidate, service.session
        self.used = self.uncertain = self.confirmed = self.retired = False
        self.cancel_attempted = self.recovery_attempted = False
        self.prelaunch = self._original_prelaunch = None
        self.run = self._original_run = None
        self.operator = self._original_operator = None
        self.reader = self._original_reader = None
        self.run_objects = self.run_binding = None
        self.read = self._unavailable
        self._ledger()

    def _ledger(self):
        """Verify the original prepared ledger BEFORE publishing native input."""
        ledger, service = self.ledger, self.service
        expected = launch.binding.Binding(
            service.projected,
            service.plan.candidate_runtime.source,
            service.plan.sha256,
            service.plan.boot,
        )
        require(type(ledger) is launch.binding.Ledger and not ledger._poisoned)
        require(ledger._lock is self.ledger_lock and ledger.state is self.ledger_state)
        require(ledger.directory == service.plan.root / "recording-ledger")
        require(ledger.binding == expected and ledger._directory_identity == self.ledger_identity)
        if self.run is not None:
            require(self.run.pins.host == expected)
        require(self.ledger_lock.acquire(blocking=False))
        try:
            end = time.monotonic() + 2
            launch.binding._location(ledger.directory, expected)
            with launch.binding.protected._private_directory(
                ledger.directory, exclusive=False
            ) as fd:
                require(launch.binding.identity(os.fstat(fd))[:6] == self.ledger_identity)
                require(launch.binding._read(fd, expected, end) == self.ledger_state)
            require(time.monotonic() < end)
        finally:
            self.ledger_lock.release()

    def _run_objects(self):
        return (*super()._run_objects(), self.run.qualify, self.run.ready_qualification)

    def _binding(self):
        run = self.run
        return (
            run.plan,
            run.projected,
            run.journal,
            run.endpoint,
            run.idle,
            run.witness,
            run.read,
            run.prelaunch,
            run.pins,
            run.command,
            run.launch_sha256,
            run.profile_sha256,
        )

    def _context(self, *, recovering=False, retired=False):
        service = self.service
        require(type(self) is AppNativePhase and type(retired) is bool)
        require(self.retired is retired)
        require(self.owner == service.owner == (os.getpid(), operator.get_ident()))
        require(service.used and service.lock.locked() and service.native_attempted)
        require(service.native is service._original_native is self and service.coordinator.finished)
        require(
            all(
                a is b
                for a, b in zip(
                    (
                        service,
                        self.ledger,
                        self.launch_endpoint,
                        self.endpoint,
                        service.original.candidate,
                        service.session,
                    ),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self.candidate is service.original.candidate)
        service.coordinator._owned_context()
        require(self.prelaunch is self._original_prelaunch and self.run is self._original_run)
        if self.prelaunch is not None:
            require(type(self.prelaunch) is inputs.NativeLaunchQualification)
            require(self.prelaunch.candidate is self.candidate.qualifier)
            require(self.prelaunch.startup is service.startup)
        if self.run is not None:
            require(type(self.run) is execution.AppLaunch)
            require(self.run.prelaunch is self.prelaunch)
            require(self.run.plan is service.plan and self.run.projected is service.projected)
            require(
                self.run.journal is service.journal and self.run.endpoint is self.launch_endpoint
            )
            require(self.run.read is self.candidate.reader and self.run.idle is self.candidate.idle)
            require(self.run.witness is self.candidate.witness)
            if self.run_binding is not None:
                require(all(a is b for a, b in zip(self._binding(), self.run_binding, strict=True)))
            if self.run_objects is not None:
                require(
                    all(a is b for a, b in zip(self._run_objects(), self.run_objects, strict=True))
                )
        require(self.operator is self._original_operator and self.reader is self._original_reader)
        if self.operator is not None:
            require(type(self.operator) is begin.worker_exit.reconcile.Operator)
            require(self.operator.plan is service.plan and self.operator.endpoint is self.endpoint)
        if self.confirmed:
            require(self.run.used and self.run.confirm_attempted and self.operator is not None)
        if retired:
            # Static custody only: authorization has changed the ledger and
            # Ready may be expired or failed. Do not disable original expiry.
            require(self.confirmed and not self.uncertain)
            require(not self.cancel_attempted and not self.recovery_attempted)
            require(self.reader is None and self.operator is not None)
            require(service.recording is not None and service.recording.native is self)
        elif recovering:
            require(self.recovery_attempted and self.reader is not None)
        else:
            require(service.session.read is self.read and self.read == self._unavailable)

    def start(self, *, specification, profile_sha256, candidate_observed):
        self.service._context()
        require(not self.used)
        self.used = True
        try:
            require(candidate_observed is True)
            self._ledger()
            prelaunch = inputs.publish_launch(
                self.service.startup,
                self.candidate.qualifier,
                specification=specification,
                profile_sha256=profile_sha256,
            )
            self.prelaunch = self._original_prelaunch = prelaunch
            run = execution.AppLaunch(
                prelaunch, self.service.journal, self.launch_endpoint, read=self.candidate.reader
            )
            self.service._cleanup.append(run.close)
            self.run = self._original_run = run
            self.run_binding = self._binding()
            self._ledger()
            run.start_confirmed()
            observed = begin.worker_exit.reconcile.Operator(
                self.service.plan, run.ready, self.endpoint
            )
            # Register the ORIGINAL cleanup before any subsequent validation.
            self.service._cleanup.append(observed.close)
            self.operator = self._original_operator = observed
            self._ledger()
            require(self._history().state.phase == "candidate_running")
        except Exception:
            self.uncertain = True
        if self.run is not None:
            self.run_objects = self._run_objects()
        self.service._context()
        self.confirmed = not self.uncertain
        return self.confirmed

    def cancel(self):
        self.service._context()
        require(self.run is not None and not self.run.failed)
        observer = self.service.native_observer
        if observer is None:
            return super().cancel()
        # Consume cancellation BEFORE the callback, so neither a reentrant
        # cancel nor a recording handoff can use this observation as authority.
        require(self.used and self.confirmed and not self.uncertain and not self.retired)
        require(not self.cancel_attempted and not self.recovery_attempted)
        self.cancel_attempted = True
        run = self.run
        try:
            notice = self._cancel_notice()
            require(run.qualify() is None)
            require(self._cancel_notice().receipt == notice.receipt)
            end = min(time.monotonic() + 2, run.ready.ready_by)
            receipt = notice.receipt
            require(observer(notice) == receipt)
            self.service._context()
            require(self.service.native_observer is observer)
            require(self._cancel_notice().receipt == receipt and notice.receipt == receipt)
            require(time.monotonic() < end)
            require(run.qualify() is None)
            require(self._cancel_notice().receipt == receipt)
            require(time.monotonic() < end)
        except BaseException as error:
            # Keep the same clock/session alive for review. Close only the
            # originally acquired transport; no retry or pristine inference.
            self.uncertain = True
            with suppress(Exception):
                run._fail(error)
            if not isinstance(error, Exception):
                raise
            return False
        confirmed = True
        try:
            run.client.close()
        except Exception:
            confirmed = False  # Original handles may still prove actual exit.
        self.service._context()
        return confirmed

    def _cancel_notice(self):
        """Original pre-cancel custody without constructing any AppStart."""
        from supplemental_recording_app_begin import native_notice

        self.service._context()
        run = self.run
        require(self.cancel_attempted and not self.uncertain and not self.retired)
        require(not run.begin_attempted and run.begin_owner is None)
        require(self.service.recording is None and not self.service.recording_attempted)
        require(not self.operator.done and not self.recovery_attempted)
        run._app_context()
        require(type(run.ready) is launch.received.Ready and run.confirm_attempted)
        require(type(run.probe) is launch.probe_exec.Sample and run.probe.ready is run.ready)
        require(type(run.qualify) is execution.readiness.NativeReadyQualification)
        run.ready.check_before_begin()
        self._ledger()
        require(self.ledger_state.now <= run.ready.received_at)
        machine = self._history()
        state = machine.state
        proof = begin.ready_proof(run)
        require(state.phase == "candidate_running" and not state.finish_requested)
        require(state.operator_exit_sha256 is None and state.ready_evidence_sha256 == proof)
        require(state.launch_intent_sha256 == run.action.intent_sha256)
        require(state.launch_plan_sha256 == run.command.plan_sha256)
        require(state.candidate_generation == run.pins.generation)
        require(machine.process_bound(base.CANDIDATE, exited=False))
        require(machine.execution_closed("starting_candidate"))
        observed = operator.plans.clock.read()
        self.service.plan.check_clock(observed)
        now = observed.boottime_ns / operator.plans.clock.NS
        require(machine.last_at <= now < min(state.deadline, self.service.plan.deadlines.ready_by))
        require(time.monotonic() < run.ready.ready_by)
        return native_notice(run, proof)

    def poll(self, wait):
        self.service._context()
        require(self.used and not self.retired and not self.recovery_attempted and callable(wait))
        result = self.service.session.poll()
        if result.phase in ("complete", "review") or self.uncertain:
            return result
        try:
            self._history()
            observed = self.operator
            require(observed is not None)
            if not observed.done:
                if observed.poll() is None:
                    return result
                observed.publish(self.service.journal)
            if not observed._exited("init"):
                return result
            self.service.processes.reconcile()
            reader = begin.worker_exit.reconcile.NeverAuthorized(
                observed, self.ledger, self.service.journal
            )
            self.service._cleanup.append(reader.close)
            self.reader = self._original_reader = reader
        except Exception:
            self.uncertain = True
            return result
        self.service._context()
        self.recovery_attempted = True

        def bounded_wait(seconds):
            self.service._context(recovering=True)
            require(seconds == 0.25)
            wait(seconds)
            self.service._context(recovering=True)

        return recovery.recover_never_authorized(
            self.reader, self.run, self.service.session, bounded_wait
        )


if __name__ == "__main__":
    raise SystemExit("Uninstalled App service driver only; no service enabled.")
