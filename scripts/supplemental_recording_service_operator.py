#!/usr/bin/env python3
"""Private schema3 request/cancel publication and phase join, not an entrypoint.

Only the existing service owner may consume a fresh independently submitted
notice into its original journal. The idle coordinator may advance the ORIGINAL
session's one-use App dispatch and pristine cancellation. No native operator,
recording authorization, new session, replayed dispatch or automatic request
exists here. Installed helper/runtime and independent recovery are separate gates.
"""

from __future__ import annotations

import os
import stat
import time
from contextlib import ExitStack, contextmanager
from threading import Lock, get_ident

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
        require(session.read == transfer.read and session.consume_operator is None)
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


class IdleService:
    """Finite idle-only assembly over a caller-owned ORIGINAL preparation.

    Construction opens an Inbox but publishes nothing, observes no host and
    sends no command. The original CasePlan, projection and preparation-only
    Journal must already be independently qualified. They remain borrowed and
    open for caller review. This owner assembles exactly one transfer reader,
    process tracker, dispatcher, session and coordinator; run() consumes it once.

    Missing/refused input still expires through the original session. Exiting
    the loop closes only its original process handles and Inbox, never files,
    containers, the borrowed plan or journal. There is no native/recording route
    or automatic request/finish. A new object is NOT restart permission.

    This is not an installed entrypoint. Root/confinement, source/runtime pins,
    independent outer supervision and original custody after helper loss must
    be qualified by the eventual host launcher, not inferred from this loop.
    """

    def __init__(self, original, projected, journal, docker):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.used = self.closed = False
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

    def _context(self):
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        require(all(a is b for a, b in zip(self._objects(), self.objects, strict=True)))
        require(self.coordinator.session is self.session and self.coordinator.inbox is self.inbox)
        require(self.coordinator.transfer is self.transfer)
        require(self.original.recheck() is self.plan)

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedOperator(MESSAGE) from None

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
                result = self.coordinator.poll(wait)
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
