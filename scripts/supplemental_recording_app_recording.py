#!/usr/bin/env python3
"""Explicit, uninstalled App recording owner over the original service session.

App-only admissions and readers; the direct recording policy is unchanged.
Shared checkpoint/abandonment algorithms retain original ledger and exit custody.
No source profile or CLI selects this driver, and independent supervision is
still required. Unit tests do not establish installed or audible acceptance.
"""

from __future__ import annotations

import os
import time

import supplemental_recording_app_begin as app_begin
import supplemental_recording_app_finalized as finalized
import supplemental_recording_app_observation as observation
import supplemental_recording_app_recovery as recovery
import supplemental_recording_app_service as service_module

operator = service_module.operator
begin, base, launch, require = operator.begin, operator.base, operator.launch, operator.require


class AppRecordingPhase(operator.RecordingPhase):
    """Explicit one-use recording owner over the original captured native actors.

    This separate handoff retires pre-recording cancellation BEFORE constructing
    Start. Neither a returned Relay nor an EOF is recording success. Only actual
    start/completion returns, finalized files, original worker/init exits and
    the original session's recovery can establish success. No notices trigger it.

    Uncertain begin/completion is sticky and preserves original clock expiry;
    this owner never invents abandonment, retries, or falls back to pristine
    recovery. Explicit abandonment of a confirmed start is a distinct route,
    never repair for a failed finish. Independent supervision remains required.
    Explicit active observations use fresh one-use samplers;
    they are never automatic polling or cached policy/recording success.
    """

    def __init__(self, service):
        require(type(self) is AppRecordingPhase and type(service) is service_module.AppService)
        service._context()
        native = service.native
        require(type(native) is service_module.AppNativePhase and native.used and native.confirmed)
        require(not native.uncertain and not native.retired)
        require(not native.cancel_attempted and not native.recovery_attempted)
        require(native.operator is not None and not native.operator.done)
        native._ledger()
        state = native._history().state
        require(state.phase == "candidate_running" and not state.finish_requested)
        require(state.operator_exit_sha256 is None)
        self.owner = service.owner
        self.service, self.native = service, native
        self.run, self.ledger, self.operator = native.run, native.ledger, native.operator
        self.objects = service, native, self.run, self.ledger, self.operator, service.session
        self.used = self.uncertain = self.started = False
        self.finish_attempted = self.recovery_attempted = False
        self.abandon_attempted = self.abandoned = False
        self.abandoned_state = None
        self.start_attempt = self._original_start = None
        self.relay = self._original_relay = None
        self.reader = self._original_reader = None
        self.completion = self._original_completion = None
        self.active_preparation_attempted = False
        self.continuity = self.active_qualifier = self.active_host = None
        self.active_objects = None
        self.read = self._unavailable

    def _context(self, *, recovering=False):
        require(type(self) is AppRecordingPhase)
        service, native = self.service, self.native
        require(self.owner == service.owner == (os.getpid(), operator.get_ident()))
        require(service.used and service.lock.locked() and service.recording_attempted)
        require(service.recording is service._original_recording is self)
        require(native is service.native and native.retired)
        require(
            all(
                a is b
                for a, b in zip(
                    (service, native, native.run, native.ledger, native.operator, service.session),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self.run is native.run and self.ledger is native.ledger)
        require(self.operator is native.operator)
        require(self.start_attempt is self._original_start)
        if self.start_attempt is not None:
            require(type(self.start_attempt) is app_begin.AppStart)
            require(self.start_attempt.run is self.run and self.start_attempt.ledger is self.ledger)
        require(self.relay is self._original_relay)
        if self.relay is not None:
            require(type(self.relay) is begin.relayed.Relay)
            require(self.start_attempt.relay is self.relay)
        require(self.reader is self._original_reader)
        if self.reader is not None:
            if self.abandoned:
                require(type(self.reader) is begin.worker_exit.reconcile.Preserved)
                require(self.reader.operator is self.operator and self.reader.ledger is self.ledger)
                require(self.reader.journal is service.journal)
                require(not self.finish_attempted and self.completion is None)
            else:
                require(type(self.reader) is finalized.AppAuthorizedFinalized)
                require(
                    self.reader.start is self.start_attempt
                    and self.reader.operator is self.operator
                )
        require(self.completion is self._original_completion)
        if recovering:
            require(self.recovery_attempted and self.reader is not None)
        else:
            require(service.session.read is self.read and self.read == self._unavailable)

    def _prepare_active(self):
        if self.active_preparation_attempted:
            require(self.active_objects is not None)
            require(
                all(
                    a is b
                    for a, b in zip(
                        (self.continuity, self.active_qualifier, self.active_host),
                        self.active_objects,
                        strict=True,
                    )
                )
            )
            require(type(self.continuity) is launch.idle_module.PostBegin)
            require(type(self.active_qualifier) is observation.active.NativeActiveQualification)
            require(type(self.active_host) is observation.AppRetainedHost)
            require(self.continuity.idle is self.run.idle)
            require(self.continuity.guard is self.relay.guard)
            require(self.active_qualifier.prebegin is self.run.ready_qualification)
            require(self.active_qualifier.start is self.start_attempt)
            require(self.active_qualifier.continuity is self.continuity)
            require(self.active_host.start is self.start_attempt)
            require(self.active_host.continuity is self.continuity)
            return
        self.active_preparation_attempted = True
        continuity = launch.idle_module.PostBegin(self.run.idle, self.relay.guard)
        self.service._cleanup.append(continuity.close)
        self.continuity = continuity
        self.active_qualifier = observation.active.NativeActiveQualification(
            self.run.ready_qualification, self.start_attempt, continuity
        )
        host = observation.AppRetainedHost(self.start_attempt, continuity)
        self.service._cleanup.append(host.discard)
        self.active_host = host
        self.active_objects = continuity, self.active_qualifier, host

    def observe(self):
        """One explicit read, not a progress publication or automatic retry.

        Each successful call consumes a NEW ActiveSample over the ORIGINAL
        post-begin resources. The input/runtime/host/native checks keep their
        existing bounds. No result is stored for the session to reuse. A failed
        read is sticky; original clock-only expiry remains available for review.
        """
        self.service._context()
        require(self.used and self.started and not self.uncertain)
        require(
            not self.finish_attempted and not self.recovery_attempted and not self.abandon_attempted
        )
        require(self.relay.phase == "completed" and self.relay.expected is not None)
        try:
            entries = tuple(base.encode(e) for e in self.service.journal.entries)
            ledger_state = self.ledger.state
            self._prepare_active()
            sample = observation.AppActiveSample(self.active_host, self.active_qualifier)
            # Capture the original cleanup before read; replacement members
            # cannot close somebody else's probe or lose this owned handle.
            close = sample.close
            self.service._cleanup.append(close)
            try:
                observed = sample.read()
            finally:
                close()
            self.service._context()
            require(tuple(base.encode(e) for e in self.service.journal.entries) == entries)
            require(self.ledger.state is ledger_state)
            require(type(observed) is launch.bootstrap.recovery.Sample)
            boot, now = self.service._clock()
            require(observed.boot_id == boot and observed.now <= now)
            require(observed.observation.files.stage == "active")
            # Include owner-side close/context checks without refreshing the
            # earliest contributing evidence timestamp or any original lease.
            return launch.bootstrap.recovery.Sample(boot, now, observed.observation)
        except Exception:
            self.uncertain = True
        self.service._context()
        return None

    def start(self):
        self.service._context()
        require(not self.used)
        self.used = True
        try:
            start = app_begin.AppStart(self.run, self.ledger)
            self.service._cleanup.append(start.close)
            self.start_attempt = self._original_start = start
            relay = start.start_once()
            self.relay = self._original_relay = relay
            require(type(relay) is begin.relayed.Relay and start.relay is relay)
            expected = relay.started()
            require(expected is relay.expected and relay.phase == "completed")
            start.retained_history()
            self.started = True
        except Exception:
            # Start may already have committed authorization or intent. Its
            # failure can mark Launch failed; the retired native owner must not
            # disable the same independent session's clock-only expiry.
            self.uncertain = True
        self.service._context()
        return self.started and not self.uncertain

    def finish(self):
        """Receive bounded completion and publish actual exit, NOT restoration.

        One uninterrupted owner step preserves AuthorizedFinalized's exact
        journal prefix. No generic session tick is inserted between capture and
        exit publication. Existing native/attachment/exit deadlines bound I/O;
        the caller still needs independent outer supervision.
        """
        self.service._context()
        require(self.used and self.started and not self.uncertain)
        require(
            not self.finish_attempted and not self.recovery_attempted and not self.abandon_attempted
        )
        self.finish_attempted = True
        try:
            self.start_attempt.retained_history()
            progress_directory = self._retain_progress()
            completion = self.relay.completed(progress_directory=progress_directory)
            self.completion = self._original_completion = completion
            require(self.relay.phase == "closed" and self.relay.completion is completion)
            reader = finalized.AppAuthorizedFinalized(self.start_attempt, self.operator)
            self.service._cleanup.append(reader.close)
            self.reader = self._original_reader = reader
            reader.collect_exit()
            self.run.ready.close()
            receipt = self.operator.poll()
            require(type(receipt) is begin.worker_exit.reconcile.Evidence)
            require(receipt.returncode == 0)
            require(reader.publish_exit() == self.operator.result_sha256)
            require(reader.phase == "published")
        except Exception:
            self.uncertain = True
        self.service._context()
        return not self.uncertain

    def poll(self, wait):
        self.service._context()
        require(self.used and not self.recovery_attempted and callable(wait))
        result = self.service.session.poll()
        if result.phase in ("complete", "review") or self.uncertain:
            return result
        if self.reader is None and not self.abandoned:
            return result
        try:
            if self.abandoned:
                if not self._preserved_reader():
                    return result
            else:
                require(self.finish_attempted and self.reader.phase == "published")
                if not self.operator._exited("init"):
                    return result
                self.service.processes.reconcile()
                self.reader._history(time.monotonic() + 2)
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

        if self.abandoned:
            return recovery.recover_preserved(
                self.reader, self.run, self.service.session, bounded_wait
            )
        return recovery.recover_finalized(self.reader, self.service.session, bounded_wait)


if __name__ == "__main__":
    raise SystemExit("Uninstalled App recording phase only; no recording enabled.")
