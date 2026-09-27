#!/usr/bin/env python3
"""Explicit, uninstalled App-aware service driver through native cancellation.

No command selects this owner. It borrows the original idle service, runs ONE
top-level loop, and retires idle reads before native input publication. Recording
handoff is deliberately not supplied yet. Independent outer supervision remains
mandatory; synthetic tests are not installed provenance or scanner acceptance.
"""

from __future__ import annotations

import os
import time

import supplemental_recording_app_candidate as candidates
import supplemental_recording_app_execution as execution
import supplemental_recording_app_recovery as recovery

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

    def __init__(self, startup, original):
        require(type(self) is AppService and type(original) is operator.IdleService)
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
        )
        self.used = self.native_attempted = False
        self.native = self._original_native = None
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
        if self.used:
            require(original.used and self.lock.locked())
        if self.native is not None:
            require(type(self.native) is AppNativePhase)
            self.native._context(recovering=recovering)
        else:
            require(not self.native_attempted and not recovering)

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
        require(self.used and not self.native_attempted)
        candidate = self.original.candidate
        require(type(candidate) is candidates.AppIdleCandidate)
        candidate.recheck()
        with self.inbox.native_handoff():
            phase = AppNativePhase(self, ledger, endpoint, operator_endpoint)
            candidate.recheck()
            self.native_attempted = True
            self.coordinator.finished = True
            self.native = self._original_native = phase
            self.session.read = phase.read
            return phase.start(specification=specification, profile_sha256=profile_sha256)

    def cancel_native(self):
        self._context()
        require(type(self.native) is AppNativePhase)
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

    def _context(self, *, recovering=False):
        service = self.service
        require(type(self) is AppNativePhase and not self.retired)
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
        if recovering:
            require(self.recovery_attempted and self.reader is not None)
        else:
            require(service.session.read is self.read and self.read == self._unavailable)

    def start(self, *, specification, profile_sha256):
        self.service._context()
        require(not self.used)
        self.used = True
        try:
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
        return super().cancel()

    def poll(self, wait):
        self.service._context()
        require(self.used and not self.recovery_attempted and callable(wait))
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
    raise SystemExit("Uninstalled App native-phase driver only; no service enabled.")
