#!/usr/bin/env python3
"""Private schema3 notices and original-owner phase joins, not an entrypoint.

Only the existing service owner may consume a fresh independently submitted
notice into its original journal. The idle coordinator may advance the ORIGINAL
session's one-use App dispatch and pristine cancellation. A separate explicit
native handoff retires idle ownership without granting recording authorization.
No new session, replayed dispatch or automatic request exists here. Installed
helper/runtime and independent recovery are separate gates.
"""

from __future__ import annotations

import os
import stat
import time
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from threading import Lock, get_ident

import supplemental_recording_host_begin as begin
import supplemental_recording_host_launch as launch
import supplemental_recording_service_input as intake
from supplemental_handoff_host import object_json

plans, base = intake.plans, intake.plans.base
MAX_AGE, MAX_BYTES = 30, 1024
ACTIONS = {"request": "prepared", "cancel_idle": "candidate_idle"}
IDLE_POLL_LIMIT = base.TOTAL_SECONDS * 4 + 1
MESSAGE = "Recording operator input is unconfirmed; preserve the case and do not resubmit."


class UnconfirmedOperator(ValueError):
    """Invalid input cannot authorize an action or bypass independent expiry."""


def require(value):
    if not value:
        raise UnconfirmedOperator(MESSAGE)


def notice(action, plan, preparation_sha256, issued_at):
    """Pure format builder; not publication, approval, or a journal event."""
    require(type(action) is str and action in ACTIONS and type(plan) is plans.Plan)
    base.digest(preparation_sha256)
    base.clock(issued_at)
    return {
        "schema": 1,
        "kind": "finite-recording-service-notice-v1",
        "action": action,
        "case_id": plan.case,
        "boot_id": plan.boot,
        "plan_sha256": plan.sha256,
        "preparation_sha256": preparation_sha256,
        "issued_at": issued_at,
    }


@contextmanager
def _reading_directory(path):
    """Only lock acquisition contention means 'not yet'; read errors still fail."""
    with ExitStack() as stack:
        try:
            fd = stack.enter_context(
                launch.binding.protected._private_directory(path, exclusive=False)
            )
        except launch.binding.protected.DirectoryBusy:
            yield None
        else:
            yield fd


def _names(fd):
    names = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            require(len(names) < 2 and entry.name in {a + ".json" for a in ACTIONS})
            names.add(entry.name)
    return names


class Publisher:
    """One explicit notice, one publication attempt; never an automatic action.

    The caller supplies the original independently bound preparation digest and
    issue time. This does not discover approval from a file, read the host, own
    the journal, dispatch anything, or authorize native recording. The service
    must independently check the notice against its original journal and phase.

    A failed/uncertain attempt is consumed. Preserve every remaining file; do
    not recreate this object to retry. Pending files prevent later publication
    and consumption. Success removes only its own temporary hardlink after
    publishing the durable payload; the destination is never overwritten.
    """

    def __init__(self, original, action, preparation_sha256, issued_at):
        self.owner = (os.getpid(), get_ident(), os.geteuid(), os.getegid())
        self.used = self.failed = False
        self.lock = Lock()
        try:
            require(type(original) is intake.CasePlan)
            self.original, self.plan = original, original.recheck()
            self.action = action
            self.raw = base.encode(notice(action, self.plan, preparation_sha256, issued_at))
            require(len(self.raw) <= MAX_BYTES)
            self.issued_at = issued_at
            self.path = self.plan.root / "inbox"
            self.originals = (original, self.plan, action, self.raw, issued_at, self.path)
            with launch.binding.protected._private_directory(self.path, exclusive=False) as fd:
                info = os.fstat(fd)
                require(info.st_gid == os.getegid())
                self.identity = intake.files.identity(info)[:6]
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedOperator(MESSAGE) from None

    def publish(self):
        acquired = False
        output = -1
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used and not self.failed)
            self.used = True
            end = time.monotonic() + 2

            def check():
                require(not self.failed)
                require(self.owner == (os.getpid(), get_ident(), os.geteuid(), os.getegid()))
                current = (
                    self.original,
                    self.plan,
                    self.action,
                    self.raw,
                    self.issued_at,
                    self.path,
                )
                require(all(a is b for a, b in zip(current, self.originals, strict=True)))
                require(self.original.recheck() is self.plan and time.monotonic() < end)
                require(intake.files.identity(os.fstat(fd))[:6] == self.identity)
                require(
                    intake.files.identity(os.stat(self.path, follow_symlinks=False))[:6]
                    == self.identity
                )
                observed = plans.clock.read()
                self.plan.check_clock(observed)
                now = observed.boottime_ns / plans.clock.NS
                require(0 <= now - self.issued_at <= MAX_AGE and now < self.plan.deadlines.ready_by)
                require(time.monotonic() < end)
                return observed

            with launch.binding.protected._private_directory(self.path, exclusive=True) as fd:
                before = check()
                names = _names(fd)
                destination, temporary = self.action + ".json", ".pending-" + self.action
                require(destination not in names)
                output = os.open(
                    temporary,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=fd,
                )
                opened = intake.files.identity(os.fstat(output))
                require(stat.S_IMODE(opened[2]) == 0o600 and opened[3:6] == (*self.owner[2:], 1))
                require(os.write(output, self.raw) == len(self.raw))
                os.fsync(output)
                require(os.pread(output, MAX_BYTES + 1, 0) == self.raw)
                complete = intake.files.identity(os.fstat(output))
                require(complete[:6] == opened[:6] and complete[6] == len(self.raw))
                require(
                    intake.files.identity(os.stat(temporary, dir_fd=fd, follow_symlinks=False))
                    == complete
                )
                before.check_later(check())
                os.link(temporary, destination, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                linked = intake.files.identity(os.fstat(output))
                require(linked[:5] == complete[:5] and linked[5] == 2)
                require(linked[6:8] == complete[6:8])
                for name in (temporary, destination):
                    require(
                        intake.files.identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                        == linked
                    )
                before.check_later(check())
                # Remove only the checked temporary link created by this call.
                # Failures leave pending/destination files untouched for review.
                os.unlink(temporary, dir_fd=fd)
                os.fsync(fd)
                final = intake.files.identity(os.fstat(output))
                require(final[:6] == complete[:6] and final[6:8] == complete[6:8])
                require(
                    intake.files.identity(os.stat(destination, dir_fd=fd, follow_symlinks=False))
                    == final
                )
                require(_names(fd) == names | {destination})
                require(os.pread(output, MAX_BYTES + 1, 0) == self.raw)
                require(intake.files.identity(os.fstat(output)) == final)
                require(
                    intake.files.identity(os.stat(destination, dir_fd=fd, follow_symlinks=False))
                    == final
                )
                after = check()
                before.check_later(after)
                require(0 <= (after.boottime_ns - before.boottime_ns) / plans.clock.NS < 2)
            require(time.monotonic() < end)
            return base.checksum(object_json(self.raw))
        except BaseException as error:
            self._fail(error)
        finally:
            if output >= 0:
                os.close(output)
            if acquired:
                self.lock.release()


class Inbox:
    """One original input/journal/projection, with retained private inbox identity.

    A request is eligible only in prepared. Idle cancellation is eligible only
    before ANY native operator/recording authorization. Neither notice can be
    reinterpreted as approval for a different phase. Consumed files remain in
    place; the original journal is the durable one-use consumption receipt.

    The caller must continue its independent expiry/recovery loop when input is
    absent or refused. A refused Inbox is permanently unusable, and may not be
    replaced to reinterpret the same case. close() never closes caller custody.
    """

    def __init__(self, original, projected, journal):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = False
        self.fd = -1
        try:
            require(type(original) is intake.CasePlan)
            self.original, self.plan = original, original.recheck()
            self.projected, self.journal = projected, journal
            self.plan.check_projection(projected)
            self.objects = original, self.plan, projected, journal
            require(type(journal) is launch.bootstrap.Journal and journal.fd >= 0)
            self.journal_fd = journal.fd
            self.journal_identity = intake.files.identity(os.fstat(journal.fd))[:6]
            self.path = self.plan.root / "inbox"
            with launch.binding.protected._private_directory(self.path, exclusive=False) as fd:
                info = os.fstat(fd)
                require(info.st_gid == os.getegid())
                self.identity = intake.files.identity(info)[:6]
                # Retain the directory, not a dup of the flock-bearing open
                # description. A future publisher must be able to lock it
                # exclusively between consume calls while this Inbox lives.
                self.fd = os.open(".", intake.files.DIRECTORY, dir_fd=fd)
            self._guard(time.monotonic() + 2)
        except BaseException as error:
            self.close()
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedOperator(MESSAGE) from None

    def _guard(self, end):
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        require(
            all(
                current is original
                for current, original in zip(
                    (self.original, self.plan, self.projected, self.journal),
                    self.objects,
                    strict=True,
                )
            )
        )
        require(self.original.recheck() is self.plan)
        require(self.path == self.plan.root / "inbox")
        require(self.journal.fd == self.journal_fd)
        require(intake.files.identity(os.fstat(self.journal_fd))[:6] == self.journal_identity)
        require(intake.files.identity(os.fstat(self.fd))[:6] == self.identity)
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        require(time.monotonic() < end)
        return observed

    def _history(self, end):
        self._guard(end)
        return launch._journal_history(self.plan, self.projected, self.journal, end)

    @contextmanager
    def native_handoff(self):
        """Exclude idle publishers across the explicit one-way native transition.

        Unlike consume(), lock contention is NOT absence of cancellation here.
        Pending, malformed or already published cancellation refuses the start
        without consuming/removing any notice. Keep the publication lock through
        native intent, dispatch and capture; their existing deadlines and outer
        supervision still apply. This guard creates no new launch/time budget.

        Publication after this boundary is not consumption: the retired idle
        coordinator cannot reinterpret such a notice as native cancellation.
        """
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            end = time.monotonic() + 2
            self._guard(end)
            with launch.binding.protected._private_directory(self.path, exclusive=True) as fd:
                require(intake.files.identity(os.fstat(fd))[:6] == self.identity)
                state = self._history(end).state
                require(state.phase == "candidate_idle" and not state.finish_requested)
                require(state.launch_intent_sha256 is state.authorization_generation is None)
                require(state.recording_outcome == "not_attempted")
                names = _names(fd)
                require(names <= {"request.json"})
                self._guard(end)
                yield
                # No renewed evidence window or phase check after the handoff.
                # Verify the held directory and publication set are unchanged.
                require(intake.files.identity(os.fstat(fd))[:6] == self.identity)
                require(_names(fd) == names)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def consume(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            end = time.monotonic() + 2
            before = self._guard(end)
            machine = self._history(end)
            action = next((a for a, phase in ACTIONS.items() if phase == machine.state.phase), None)
            if action is None:
                return False
            event = "request" if action == "request" else "finish"
            if any(entry["event"]["kind"] == event for entry in self.journal.entries):
                return False
            state = machine.state
            require(state.launch_intent_sha256 is None and state.authorization_generation is None)
            require(state.recording_outcome == "not_attempted" and not state.finish_requested)
            preparation = base.checksum(self.journal.entries[0]["event"])
            with _reading_directory(self.path) as fd:
                if fd is None:
                    self._guard(end)
                    return False
                require(intake.files.identity(os.fstat(fd))[:6] == self.identity)
                if action + ".json" not in _names(fd):
                    return False
                name = action + ".json"
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(
                    stat.S_ISREG(info.st_mode)
                    and stat.S_IMODE(info.st_mode) == 0o600
                    and (info.st_uid, info.st_gid) == (os.geteuid(), os.getegid())
                    and info.st_nlink == 1
                )
                identity = intake.files.identity(info)
                raw = launch.binding.protected.evidence.read_bytes(
                    fd, name, limit=MAX_BYTES, deadline=end
                )
                value = object_json(raw)
                require(type(value) is dict and type(value.get("schema")) is int)
                issued = value.get("issued_at")
                expected = notice(action, self.plan, preparation, issued)
                require(value == expected and raw == base.encode(expected))
                require(
                    self._history(end) is machine and _names(fd) <= {a + ".json" for a in ACTIONS}
                )
                observed = self._guard(end)
                before.check_later(observed)
                now = observed.boottime_ns / plans.clock.NS
                require(0 <= (observed.boottime_ns - before.boottime_ns) / plans.clock.NS < 2)
                require(machine.created_at <= issued <= now and now - issued <= MAX_AGE)
                require(now < min(machine.state.deadline, self.plan.deadlines.ready_by))
                require(
                    intake.files.identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                    == identity
                )
                entries = tuple(base.encode(entry) for entry in self.journal.entries)
                submitted = {"kind": event, "boot_id": observed.boot, "now": now}
                self.journal.append(submitted)
                # If publication succeeded but its acknowledgement/check failed,
                # the durable event remains consumed; no deletion or retry.
                require(
                    intake.files.identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                    == identity
                )
                self._history(end)
                require(len(self.journal.entries) == len(entries) + 1)
                require(tuple(base.encode(entry) for entry in self.journal.entries[:-1]) == entries)
                require(self.journal.entries[-1]["event"] == submitted)
                self._guard(end)
            return True
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        self.closed = True


class IdleCoordinator:
    """Join explicit notices to ONE original pristine transfer/recovery session.

    Construct before any request or process binding. poll() borrows the original
    session and its independent clock tick, never the consume_operator callback.
    At candidate idle it retains a NeverLaunchedHost BEFORE consuming cancellation.
    An explicit durable finish routes the same session through its one-use
    pristine recovery continuation. No launch, recording, implicit cancellation,
    replacement reader/session, or automatic request is supplied here.

    Missing or refused input cannot suppress the session's independent expiry;
    a refused Inbox is never called again. A committed finish with a lost input
    acknowledgement is still authoritative only after fresh journal verification.
    Other failures are sticky and preserve caller custody/evidence. The caller
    owns final closure and an independently enforced outer deadline: this is a
    phase component, NOT an installed service or an unattended polling loop.
    """

    def __init__(self, inbox, transfer, session):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.finished = self.input_failed = False
        self.cancel = self._original_cancel = None
        try:
            require(type(inbox) is Inbox and not inbox.failed and not inbox.closed)
            require(type(transfer) is launch.TransferHost and not transfer.failed)
            require(type(session) is launch.bootstrap.RecoverySession)
            self.inbox, self.transfer, self.session = inbox, transfer, session
            self.original, self.plan = inbox.original, inbox.plan
            self.projected, self.journal = inbox.projected, inbox.journal
            self.processes, self.dispatch, self.executor = (
                session.processes,
                session.dispatch,
                session.executor,
            )
            self.objects = (
                inbox,
                transfer,
                session,
                self.original,
                self.plan,
                self.projected,
                self.journal,
                self.processes,
                self.dispatch,
                self.executor,
            )
            self.inbox_fd = inbox.fd
            self.callbacks = self.processes.read_clock, self.dispatch.now
            machine = self._history()
            require(machine.state.phase == "prepared" and len(self.journal.entries) == 1)
            require(not machine.state.processes and not machine.state.executions)
            require(not self.processes.witnesses)
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedOperator(MESSAGE) from None

    def _context(self):
        require(not self.failed and not self.finished and self.owner == (os.getpid(), get_ident()))
        self._owned_context()
        require(self.session.read == self.transfer.read)

    def _owned_context(self):
        """Original object custody only, never permission to poll the idle phase."""
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        current = (
            self.inbox,
            self.transfer,
            self.session,
            self.original,
            self.plan,
            self.projected,
            self.journal,
            self.processes,
            self.dispatch,
            self.executor,
        )
        require(all(a is b for a, b in zip(current, self.objects, strict=True)))
        require(self.cancel is self._original_cancel)
        require(self.original.recheck() is self.plan)
        inbox, transfer, session = self.inbox, self.transfer, self.session
        require(not inbox.closed and inbox.fd == self.inbox_fd)
        require(inbox.original is self.original and inbox.plan is transfer.plan is self.plan)
        require(inbox.projected is transfer.projected is self.projected)
        require(inbox.journal is transfer.journal is session.journal is self.journal)
        require(session.consume_operator is None)
        require(session.processes is self.processes and session.dispatch is self.dispatch)
        require(session.executor is self.executor and not self.processes.closed)
        require(type(self.processes) is launch.TrackedProcesses)
        require(type(self.dispatch) is launch.TrackedDispatch)
        require(type(self.executor) is launch.bootstrap.Executor)
        require(
            self.processes.journal is self.dispatch.journal is self.executor.journal is self.journal
        )
        require(self.processes.docker is self.dispatch.docker is transfer.docker)
        require(self.executor.read == session._read and self.executor.send == session._send)
        require(
            self.processes.read_clock is self.callbacks[0]
            and self.dispatch.now is self.callbacks[1]
        )
        require(
            self.processes.images
            == {
                base.NORMAL: self.plan.normal.image,
                base.CANDIDATE: self.plan.candidate.image,
            }
        )
        require(
            (self.dispatch.image, self.dispatch.generation)
            == (
                self.plan.cli_image,
                self.plan.cli_generation,
            )
        )

    def _history(self):
        self._context()
        machine = launch._journal_history(
            self.plan,
            self.projected,
            self.journal,
            time.monotonic() + 2,
        )
        state = machine.state
        require(state.phase in launch.TransferHost.PHASES | {"review"})
        require(state.launch_intent_sha256 is state.authorization_generation is None)
        require(state.ready_evidence_sha256 is state.operator_exit_sha256 is None)
        require(state.recording_outcome == "not_attempted")
        return machine

    def _retain_idle(self, machine):
        if machine.state.phase == "candidate_idle" and self.cancel is None:
            self.cancel = launch.NeverLaunchedHost(self.transfer, self.session)
            self._original_cancel = self.cancel
        if self.cancel is not None:
            require(type(self.cancel) is launch.NeverLaunchedHost)
            require(self.cancel.transfer is self.transfer and self.cancel.session is self.session)
            require(not self.cancel.recovery_attempted and not self.cancel.failed)

    def poll(self, wait):
        """One phase step; explicit cancellation runs bounded original recovery.

        wait is used only by the existing cancellation continuation, with 0.25s
        arguments and the original recovery deadline. No input is published here.
        No terminal result except complete certifies normal restoration.
        """
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(callable(wait))
            machine = self._history()
            self._retain_idle(machine)
            if not self.input_failed and machine.state.phase in ACTIONS.values():
                try:
                    self.inbox.consume()
                except UnconfirmedOperator:
                    self.input_failed = True
            machine = self._history()
            if machine.state.phase == "candidate_idle" and machine.state.finish_requested:
                require(self.cancel is not None)
                # Mark this phase owner spent before any continuation I/O.
                self.finished = True
                return launch.recover_never_launched(self.cancel, wait)
            result = self.session.poll()
            machine = self._history()
            require(result.phase == machine.state.phase)
            self._retain_idle(machine)
            if result.phase == "review":
                self.finished = True
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()


class IdleCandidate:
    """Read-only candidate resources borrowed from the STILL RUNNING idle owner.

    This is not a phase transition, native launch, successful qualification or
    recording approval. The service's one explicit preparation attempt retains
    its ORIGINAL candidate witness/session/journal and creates only Idle,
    BootstrapHost and CandidateQualification readers. No new init process is
    acquired or rebound, no Engine action is sent, and no journal event is appended.

    Construction reads actual idle/proc/clock evidence. Complete host and runtime
    qualification still belongs to the separate readers; this object cannot
    substitute for it. Any supplied ZeroDomain remains caller-owned. close()
    discards only this owner's reader and closes its duplicate idle descriptors,
    not the service's original witness, journal, plan or clock-domain handles.
    """

    def __init__(
        self,
        service,
        *,
        image_environment_sha256,
        timezone,
        hostname,
        architecture,
        runtime_workers=1,
        zero_domain=None,
    ):
        self.owner = (os.getpid(), get_ident())
        self.failed = self.closed = False
        self._cleanup = []
        try:
            require(type(service) is IdleService)
            require(service.candidate_attempted and service.candidate is None)
            self.service = service
            service._context()
            self.coordinator = service.coordinator
            machine = self._phase()
            self.witness = self.coordinator.cancel.candidate_witness
            require(type(self.witness) is launch.engine.dispatch.process.ProcessWitness)
            self.record = self.coordinator.cancel.candidate_record
            self.fd = self.witness.fd
            self.fd_identity = intake.files.identity(os.fstat(self.fd))
            self.zero_domain = zero_domain
            entries = tuple(base.encode(e) for e in service.journal.entries)
            self._custody(machine)
            self.idle = launch.idle_module.Idle(
                service.plan, self.witness, self.record.generation, zero_domain=zero_domain
            )
            self._cleanup.append(self.idle.close)
            self.reader = launch.BootstrapHost(
                service.plan, service.projected, self.idle, self.witness, service.docker
            )
            self._cleanup.append(self.reader.discard)
            self.qualifier = launch.CandidateQualification(
                service.plan,
                self.idle,
                self.witness,
                service.docker,
                image_environment_sha256=image_environment_sha256,
                timezone=timezone,
                hostname=hostname,
                architecture=architecture,
                runtime_workers=runtime_workers,
            )
            self.objects = (
                service,
                self.coordinator,
                self.witness,
                self.record,
                self.idle,
                self.reader,
                self.qualifier,
                zero_domain,
            )
            self.profile = self.qualifier._pins()
            self.recheck()
            require(tuple(base.encode(e) for e in service.journal.entries) == entries)
        except BaseException as error:
            self.close()
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedOperator(MESSAGE) from None

    def _phase(self):
        require(not self.closed and not self.failed and self.owner == (os.getpid(), get_ident()))
        service = self.service
        service._context()
        require(service.used and service.lock.locked() and service.candidate_attempted)
        require(service.coordinator is self.coordinator and not self.coordinator.input_failed)
        machine = self.coordinator._history()
        require(machine.state.phase == "candidate_idle" and not machine.state.finish_requested)
        require(self.coordinator.cancel is not None and not self.coordinator.cancel.failed)
        require(not self.coordinator.cancel.recovery_attempted)
        observed = plans.clock.read()
        service.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(
            machine.last_at <= now < min(machine.state.deadline, service.plan.deadlines.ready_by)
        )
        require(time.monotonic() < service.plan.lease["ready_by"])
        return machine

    def _custody(self, machine):
        cancel = self.coordinator.cancel
        require(cancel.candidate_witness is self.witness and cancel.candidate_record is self.record)
        require(self.service.processes.witnesses.get(base.CANDIDATE) is self.witness)
        require(machine.process_bound(base.CANDIDATE, exited=False))
        require(machine.execution_closed("starting_candidate"))
        require(machine.state.candidate_generation == self.record.generation)
        require(
            self.witness.identity
            == launch.runtime.processes.ProcessIdentity(
                self.record.pid, self.record.start_ticks, self.record.container_id
            )
        )
        require(self.witness.fd == self.fd)
        require(intake.files.identity(os.fstat(self.fd)) == self.fd_identity)
        require(not self.witness.exited())

    def recheck(self):
        """Original live custody only; None is NOT source/runtime readiness."""
        try:
            require(
                all(
                    a is b
                    for a, b in zip(
                        (
                            self.service,
                            self.coordinator,
                            self.witness,
                            self.record,
                            self.idle,
                            self.reader,
                            self.qualifier,
                            self.zero_domain,
                        ),
                        self.objects,
                        strict=True,
                    )
                )
            )
            machine = self._phase()
            self._custody(machine)
            require(not self.idle.failed and not self.idle.closed)
            require(self.idle.plan is self.service.plan and self.idle.init == self.witness.identity)
            require(self.idle.generation == self.record.generation)
            require(self.idle.zero_domain is self.zero_domain)
            for reader in (self.reader, self.qualifier):
                require(not reader.failed and reader.plan is self.service.plan)
                require(reader.idle is self.idle and reader.witness is self.witness)
                require(reader.docker is self.service.docker and reader.fd == self.fd)
            require(self.reader.projected is self.service.projected)
            require(self.reader.pending is None and self.qualifier._pins() == self.profile)
        except BaseException as error:
            self._fail(error)

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        try:
            with ExitStack() as stack:
                for callback in self._cleanup:
                    stack.callback(callback)
        except BaseException as error:
            self._fail(error)


class NativePhase:
    """One-way, pre-recording owner; never an idle fallback or a recorder.

    The original service retires its idle coordinator BEFORE native dispatch.
    A distinct endpoint captures original Ready pidfds before any cancellation.
    Launch/capture uncertainty is sticky, but does not disable the SAME session's
    clock-only expiry. No host observation is synthesized from former Ready.

    Explicit cancellation closes only the original completion transport. It
    sends no App stop, signal, recording begin or recording abandonment. Only
    actual original worker/init exits plus the distinct NeverAuthorized reader
    may continue original-session restoration. Otherwise the original deadline
    enters review. Independent outer supervision remains mandatory.
    """

    def __init__(self, service, ledger, endpoint):
        self.owner = (os.getpid(), get_ident())
        self.used = self.uncertain = self.confirmed = False
        self.cancel_attempted = self.recovery_attempted = False
        self.operator = self._original_operator = None
        self.reader = self._original_reader = None
        self.run_objects = None
        self.service, self.run = service, service.prepared_launch
        self.ledger, self.endpoint = ledger, endpoint
        require(type(service) is IdleService and type(self.run) is launch.Launch)
        require(type(endpoint) is launch.engine.Endpoint and endpoint is not self.run.endpoint)
        require(not endpoint.closed)
        endpoint.check()
        require(type(ledger) is launch.binding.Ledger and not ledger._poisoned)
        self.ledger_state = ledger.state
        require(type(self.ledger_state) is launch.binding.State)
        require(
            self.ledger_state
            == launch.binding.State(1, self.ledger_state.sha256, self.ledger_state.now)
        )
        require(
            self.run.plan.original_clock.after_ns / plans.clock.NS
            <= self.ledger_state.now
            <= time.monotonic()
        )
        self.ledger_identity = ledger._directory_identity
        self.ledger_lock = ledger._lock
        self.objects = service, self.run, ledger, endpoint, service.session
        self.read = self._unavailable
        self._ledger()

    def _ledger(self):
        """Actual prepared bytes, not an idle flag or a cached ledger count."""
        ledger, run = self.ledger, self.run
        require(type(ledger) is launch.binding.Ledger and not ledger._poisoned)
        require(ledger._lock is self.ledger_lock and ledger.state is self.ledger_state)
        require(ledger.directory == run.plan.root / "recording-ledger")
        require(ledger.binding == run.pins.host)
        require(ledger._directory_identity == self.ledger_identity)
        require(self.ledger_lock.acquire(blocking=False))
        try:
            end = time.monotonic() + 2
            launch.binding._location(ledger.directory, ledger.binding)
            with launch.binding.protected._private_directory(
                ledger.directory, exclusive=False
            ) as fd:
                require(launch.binding.identity(os.fstat(fd))[:6] == self.ledger_identity)
                require(launch.binding._read(fd, ledger.binding, end) == self.ledger_state)
            require(time.monotonic() < end)
        finally:
            self.ledger_lock.release()

    def _context(self, *, recovering=False):
        service = self.service
        require(self.owner == service.owner == (os.getpid(), get_ident()))
        require(service.used and service.lock.locked() and service.native_attempted)
        require(service.native is service._original_native is self)
        require(service.prepared_launch is self.run and service.coordinator.finished)
        require(
            all(
                a is b
                for a, b in zip(
                    (service, self.run, self.ledger, self.endpoint, service.session),
                    self.objects,
                    strict=True,
                )
            )
        )
        service.coordinator._owned_context()
        require(self.operator is self._original_operator)
        if self.run_objects is not None:
            require(all(a is b for a, b in zip(self._run_objects(), self.run_objects, strict=True)))
        if self.confirmed:
            require(self.run.used and self.run.confirm_attempted and not self.run.failed)
        if self.operator is not None:
            require(type(self.operator) is begin.worker_exit.reconcile.Operator)
            require(self.operator.plan is service.plan and self.operator.endpoint is self.endpoint)
        require(self.reader is self._original_reader)
        if recovering:
            require(self.recovery_attempted and self.reader is not None)
        else:
            require(service.session.read is self.read and self.read == self._unavailable)

    def _run_objects(self):
        run = self.run
        return run.action, run.claim, run.client, run.ready, run.probe

    def _history(self):
        service = self.service
        machine = launch._journal_history(
            service.plan, service.projected, service.journal, time.monotonic() + 2
        )
        require(machine.state.authorization_generation is None)
        require(machine.state.recording_outcome == "not_attempted")
        require(all(e["event"]["kind"] != "authorize_recording" for e in service.journal.entries))
        return machine

    def _unavailable(self):
        # Deliberately no BootstrapHost/Ready read here: native flags are unknown.
        # RecoverySession still appends its original independent clock tick.
        raise UnconfirmedOperator(MESSAGE)

    def start(self):
        self.service._context()
        require(not self.used)
        self.used = True
        try:
            self.run.start_confirmed()
            operator = begin.worker_exit.reconcile.Operator(
                self.service.plan, self.run.ready, self.endpoint
            )
            # Retain cleanup BEFORE any subsequent check can fail. Endpoint and
            # Ledger remain borrowed; only the new original-pidfd duplicates close.
            self.service._cleanup.append(operator.close)
            self.operator = self._original_operator = operator
            self._ledger()
            require(self._history().state.phase == "candidate_running")
        except Exception:
            self.uncertain = True
        self.run_objects = self._run_objects()
        self.service._context()
        self.confirmed = not self.uncertain
        return self.confirmed

    def cancel(self):
        self.service._context()
        require(self.used and self.confirmed and not self.uncertain)
        require(not self.cancel_attempted and not self.recovery_attempted)
        self.cancel_attempted = True
        self._ledger()
        state = self._history().state
        require(state.phase == "candidate_running" and not state.finish_requested)
        require(state.operator_exit_sha256 is None and not self.operator.done)
        require(not self.run.closed and self.run.client is self.run.ready.client)
        confirmed = True
        try:
            self.run.client.close()
        except Exception:
            # A lost close acknowledgement is not permission to close/send again.
            # Original independent observation may still confirm actual exit.
            confirmed = False
        self.service._context()
        return confirmed

    def poll(self, wait):
        self.service._context()
        require(self.used and not self.recovery_attempted and callable(wait))
        result = self.service.session.poll()
        if result.phase in ("complete", "review") or self.uncertain:
            return result
        try:
            self._history()
            operator = self.operator
            require(operator is not None)
            if not operator.done:
                if operator.poll() is None:
                    return result
                operator.publish(self.service.journal)
            if not operator._exited("init"):
                return result
            self.service.processes.reconcile()
            reader = begin.worker_exit.reconcile.NeverAuthorized(
                operator, self.ledger, self.service.journal
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

        return begin.recover_never_authorized(
            self.reader, self.run, self.service.session, bounded_wait
        )


class IdleService:
    """Finite idle-first assembly over a caller-owned ORIGINAL preparation.

    Construction opens an Inbox but publishes nothing, observes no host and
    sends no command. The original CasePlan, projection and preparation-only
    Journal must already be independently qualified. They remain borrowed and
    open for caller review. This owner assembles exactly one transfer reader,
    process tracker, dispatcher, session and coordinator; run() consumes it once.

    Missing/refused input still expires through the original session. Exiting
    the loop closes only its original process handles and Inbox, never files,
    containers, the borrowed plan or journal. Native execution requires a
    separate explicit one-way handoff; there is no recording route or automatic
    request/finish. A new object is NOT restart permission.

    This is not an installed entrypoint. Root/confinement, source/runtime pins,
    independent outer supervision and original custody after helper loss must
    be qualified by the eventual host launcher, not inferred from this loop.
    """

    def __init__(self, original, projected, journal, docker):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.used = self.closed = False
        self.candidate_attempted = False
        self.observation_attempted = False
        self.launch_preparation_attempted = False
        self.native_attempted = False
        self.native = self._original_native = None
        self.candidate = self._original_candidate = None
        self.prepared_launch = self._original_launch = None
        self._launch_binding = None
        self._cleanup = []
        try:
            require(type(original) is intake.CasePlan)
            self.original, self.plan = original, original.recheck()
            self.projected, self.journal, self.docker = projected, journal, docker
            self.transfer = launch.TransferHost(self.plan, projected, journal, docker)
            self.processes = launch.TrackedProcesses(
                journal,
                docker,
                images={
                    base.NORMAL: self.plan.normal.image,
                    base.CANDIDATE: self.plan.candidate.image,
                },
                read_clock=self._clock,
            )
            # Capture the ORIGINAL resources, not a later replaceable session
            # attribute. Even refusal during construction must release these.
            self._cleanup.append(self.processes.close)
            self.dispatch = launch.TrackedDispatch(
                journal,
                docker,
                cli_image=self.plan.cli_image,
                cli_generation=self.plan.cli_generation,
                now=self._now,
            )
            self.session = launch.bootstrap.RecoverySession(
                journal, self.processes, self.dispatch, self.transfer.read
            )
            self.inbox = Inbox(original, projected, journal)
            self._cleanup.append(self.inbox.close)
            self.coordinator = IdleCoordinator(self.inbox, self.transfer, self.session)
            self.objects = self._objects()
        except BaseException as error:
            self.close()
            self._fail(error)

    def _objects(self):
        return (
            self.original,
            self.plan,
            self.projected,
            self.journal,
            self.docker,
            self.transfer,
            self.processes,
            self.dispatch,
            self.session,
            self.inbox,
            self.coordinator,
        )

    def _clock(self):
        require(self.owner == (os.getpid(), get_ident()) and not self.closed and not self.failed)
        require(self.original.recheck() is self.plan)
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        return observed.boot, observed.boottime_ns / plans.clock.NS

    def _now(self):
        return self._clock()[1]

    def _context(self, *, recovering=False):
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        require(all(a is b for a, b in zip(self._objects(), self.objects, strict=True)))
        require(self.coordinator.session is self.session and self.coordinator.inbox is self.inbox)
        require(self.coordinator.transfer is self.transfer)
        require(self.original.recheck() is self.plan)
        require(self.candidate is self._original_candidate)
        require(self.native is self._original_native)
        self._launch_context(unused=self.native is None)
        if self.native is not None:
            require(type(self.native) is NativePhase and self.native.service is self)
            self.native._context(recovering=recovering)
        else:
            require(not recovering)

    def _launch_context(self, *, unused=True):
        """Only the explicit original native owner may have consumed this launch."""
        require(self.prepared_launch is self._original_launch)
        run = self.prepared_launch
        if run is None:
            require(self._launch_binding is None)
            return
        require(self.launch_preparation_attempted and type(run) is launch.Launch)
        require(run.owner == self.owner and not run.closed)
        if unused:
            require(not run.used and not run.failed and not run.confirm_attempted)
            require(run.action is run.claim is run.client is run.ready is run.probe is None)
        candidate = self._original_candidate
        require(type(candidate) is IdleCandidate and candidate.service is self)
        require(run.plan is self.plan and run.projected is self.projected)
        require(run.journal is self.journal and run.idle is candidate.idle)
        require(run.witness is candidate.witness and run.read is candidate.reader)
        require(run.qualify is candidate.qualifier)
        endpoint, pins, command, digests = self._launch_binding
        require(run.endpoint is endpoint and run.pins is pins and run.command is command)
        require((run.launch_sha256, run.profile_sha256) == digests)
        require(type(endpoint) is launch.engine.Endpoint)
        if unused:
            require(not endpoint.closed)

    def start_native(self, ledger, endpoint):
        """Explicit one-use native handoff, NOT recording authorization.

        Only the independently qualified caller inside the original running loop
        may invoke this. The passive Launch must already be prepared. The ledger
        is an original pristine writer and endpoint is a DISTINCT borrowed Engine
        observer. Old request/cancel notices cannot trigger this transition.
        A pending/busy idle inbox refuses before retirement or native dispatch;
        its original publication lock remains held through dispatch/capture.

        False means launch/capture was unconfirmed: the original session retains
        only clock expiry and review, never retries or falls back to idle. An
        interruption still closes this owner under the independent outer lease.
        """
        try:
            self._context()
            require(self.used and self.lock.locked() and not self.native_attempted)
            self.native_attempted = True
            require(type(self.candidate) is IdleCandidate)
            self.candidate.recheck()
            require(type(self.prepared_launch) is launch.Launch)
            with self.inbox.native_handoff():
                native = NativePhase(self, ledger, endpoint)
                self.candidate.recheck()
                # Retire idle ownership and its reader BEFORE the first Engine write.
                self.coordinator.finished = True
                self.native = self._original_native = native
                self.session.read = native.read
                return native.start()
        except BaseException as error:
            self._fail(error)

    def cancel_native(self):
        """Explicit completion-stream close; never an App stop or a recorder call."""
        try:
            self._context()
            require(type(self.native) is NativePhase)
            return self.native.cancel()
        except BaseException as error:
            self._fail(error)

    def prepare_launch(self, endpoint, *, launch_sha256, profile_sha256):
        """Bind one passive Launch to this STILL RUNNING original idle owner.

        Digests and the authenticated Engine endpoint come from the separately
        qualified caller, never a saved observation. Construction only verifies
        custody/journal/socket metadata; no connection, exec, intent, Ready or
        recording approval is created. The endpoint stays caller-owned because
        no Engine Client is constructed. Original Launch closure is service-owned.

        Preparation cannot dispatch the object. Without the explicit native
        handoff, a used/changed launch refuses the idle loop instead of falling
        back to pristine cancellation. Recording permission remains separate.
        """
        try:
            started = time.monotonic()
            self._context()
            require(self.used and self.lock.locked() and not self.launch_preparation_attempted)
            self.launch_preparation_attempted = True
            candidate = self.candidate
            require(type(candidate) is IdleCandidate and candidate.service is self)
            candidate.recheck()
            before = plans.clock.read()
            self.plan.check_clock(before)
            entries = tuple(base.encode(e) for e in self.journal.entries)
            run = launch.Launch(
                self.plan,
                self.projected,
                self.journal,
                candidate.idle,
                candidate.witness,
                endpoint,
                launch_sha256=launch_sha256,
                profile_sha256=profile_sha256,
                read=candidate.reader,
                qualify=candidate.qualifier,
            )
            self._cleanup.append(run.close)
            self.prepared_launch = self._original_launch = run
            self._launch_binding = (
                endpoint,
                run.pins,
                run.command,
                (launch_sha256, profile_sha256),
            )
            candidate.recheck()
            require(tuple(base.encode(e) for e in self.journal.entries) == entries)
            after = plans.clock.read()
            self.plan.check_clock(after)
            before.check_later(after)
            require(0 <= (after.boottime_ns - before.boottime_ns) / plans.clock.NS < 2)
            require(time.monotonic() < started + 2)
            return run
        except BaseException as error:
            self._fail(error)

    def prepare_candidate(
        self,
        *,
        image_environment_sha256,
        timezone,
        hostname,
        architecture,
        runtime_workers=1,
        zero_domain=None,
    ):
        """One explicit read-only preparation inside this ORIGINAL running loop.

        Never called automatically. The eventual qualified host caller may use
        this while run() retains custody, not after run() closes it. It creates
        no native/recording authority and never changes the original session's
        reader, deadline or request/cancel-only behavior. All new descriptors are
        owned by this service even if component attributes are later replaced.
        Failure consumes the attempt; do not construct a replacement owner.
        """
        try:
            self._context()
            require(self.used and self.lock.locked() and not self.candidate_attempted)
            self.candidate_attempted = True
            candidate = IdleCandidate(
                self,
                image_environment_sha256=image_environment_sha256,
                timezone=timezone,
                hostname=hostname,
                architecture=architecture,
                runtime_workers=runtime_workers,
                zero_domain=zero_domain,
            )
            self._cleanup.append(candidate.close)
            self.candidate = self._original_candidate = candidate
            return candidate
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedOperator(MESSAGE) from None

    def observe_candidate(self):
        """One explicit full read-only observation in this original idle owner.

        The fixed host read starts before source/runtime hashing, retaining its
        original start time and deadline. Both checks join within one unchanged
        two-second window, not two renewed budgets. The result is a diagnostic
        observation with unknown native health, NOT Ready, native intent or
        recording authorization. Nothing is journaled or cached for later use.

        Every attempt is consumed, including refusal. The original service must
        remain running; no replacement owner, background retry or loop-triggered
        collection is supplied. Independent outer I/O supervision is required.
        """
        discard = None
        try:
            started = time.monotonic()
            self._context()
            require(self.used and self.lock.locked() and not self.observation_attempted)
            self.observation_attempted = True
            candidate = self.candidate
            require(type(candidate) is IdleCandidate and candidate.service is self)
            candidate.recheck()
            before = plans.clock.read()
            self.plan.check_clock(before)
            began = before.boottime_ns / plans.clock.NS
            entries = tuple(base.encode(e) for e in self.journal.entries)
            reader, qualifier = candidate.reader, candidate.qualifier
            discard = reader.discard  # Original cleanup, never a replaced member.
            require(reader.prepare() is None)
            pending = reader.pending
            require(type(pending) is launch._PreparedHostRead)
            joined = []

            def observe():
                require(not joined and self.candidate is candidate)
                candidate._custody(candidate._phase())
                require(candidate.reader is reader and candidate.qualifier is qualifier)
                require(reader.pending is pending and time.monotonic() < started + 2)
                sample = reader()
                require(type(sample) is launch.bootstrap.recovery.Sample)
                joined.append(sample)
                return sample

            sample = qualifier.during(observe)
            require(len(joined) == 1 and sample is joined[0])
            candidate.recheck()
            require(tuple(base.encode(e) for e in self.journal.entries) == entries)
            after = plans.clock.read()
            self.plan.check_clock(after)
            before.check_later(after)
            now = after.boottime_ns / plans.clock.NS
            require(sample.boot_id == before.boot == after.boot == self.plan.boot)
            require(began <= sample.observation.sampled_at <= sample.now <= now)
            require(0 <= now - began < 2 and time.monotonic() < started + 2)
            observation = replace(sample.observation, sampled_at=began)
            candidate._phase().fresh(now, observation)
            require(
                observation.candidate.healthy is None and observation.candidate.recording is None
            )
            require(time.monotonic() < started + 2)
            return launch.bootstrap.recovery.Sample(sample.boot_id, now, observation)
        except BaseException as error:
            try:
                if discard is not None:
                    discard()
            finally:
                self._fail(error)

    def run(self, wait):
        """One finite loop; wait receives only the existing 0.25s interval.

        The caller must bound wait and all host I/O independently. Only a
        complete policy result certifies restoration. Review, interruption or
        the defensive poll ceiling preserve evidence, never invent recovery.
        """
        acquired = entered = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.owner == (os.getpid(), get_ident()) and not self.used)
            self.used = entered = True
            require(callable(wait))
            for _ in range(IDLE_POLL_LIMIT):
                self._context()
                result = (
                    self.coordinator.poll(wait) if self.native is None else self.native.poll(wait)
                )
                if result.phase in ("complete", "review"):
                    return result
                wait(0.25)
            self._context()
            return launch.bootstrap.recovery.dispatch.Result(
                self.journal.machine.state.phase, "poll_limit_unconfirmed"
            )
        except BaseException as error:
            self._fail(error)
        finally:
            try:
                if entered:
                    self.close()
            finally:
                if acquired:
                    self.lock.release()

    def close(self):
        """Original owner only; release owned descriptors, preserve borrowed evidence."""
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        try:
            with ExitStack() as stack:
                for callback in self._cleanup:
                    stack.callback(callback)
        except BaseException as error:
            self._fail(error)


if __name__ == "__main__":
    raise SystemExit("Private recording operator input only; no request or cancellation submitted.")
