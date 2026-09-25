#!/usr/bin/env python3
"""Explicit App finalized/exit evidence; uninstalled and not a recovery action.

Original successful AppStart and the independently captured Operator are both
required. The existing fourth-return/EOF/pidfd/Engine collector and journal
publication remain separate from immutable-file reads. No live Ready refresh,
new deadline, App stop/start, or fallback from uncertain recording is provided.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from threading import Lock, get_ident

import supplemental_recording_app_begin as app_begin

begin, launch, require = app_begin.begin, app_begin.launch, app_begin.require
base, binding, worker_exit = begin.base, begin.binding, begin.worker_exit


class AppAuthorizedFinalized(begin.AuthorizedFinalized):
    """Exact App policy over the unchanged finalization/exit-publication steps.

    Construction checks successful retained history while the original actors are
    still owned. Later checks retain identities and seals, not live pre-begin
    qualification: completed actors and the original Ready may then be closed.
    Failure retires only this reader, leaving independent Operator, journal, and
    process custody available. It cannot grant another recording attempt.
    """

    def __init__(self, start, operator):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = self.publish_attempted = False
        self.recovery_attempted = False
        self.phase, self.publication, self.exited = "capturing", None, None
        self._publication_return = None
        self.files = None
        try:
            began = time.monotonic()
            require(type(self) is AppAuthorizedFinalized)
            require(type(start) is app_begin.AppStart)
            require(type(operator) is worker_exit.reconcile.Operator)
            machine = start.retained_history()
            require(start.relay.phase == "closed" and not operator.publish_attempted)
            self.start, self.operator = start, operator
            self.run, self.plan, self.ready = start.run, start.plan, start.ready
            self.relay, self.ledger, self.journal = start.relay, start.ledger, start.run.journal
            require(operator.plan is self.plan and operator.clock is self.plan.original_clock)
            require(operator.pins == self.run.pins)
            self.origins, self.original = start.objects, start.original
            self.app_origins = self._app_objects()
            self.begin = start._begin_result
            self.before, self.proof = machine.state, start.proof
            self.history = tuple(base.encode(entry) for entry in self.journal.entries)
            self.fd = self.journal.fd
            self.dir_id = binding.identity(os.fstat(self.fd))[:6]
            self.files = worker_exit.Finalized(self.relay, operator)
            require(start.read_files() == self.files.completion.collected)
            self.seal = self._values()
            self.phase = "completed"
            self._context()
            self._history(min(began + 2, self.relay.guard.finish_by))
            require(began <= time.monotonic() < min(began + 2, self.relay.guard.finish_by))
        except BaseException as error:
            self._fail(error)

    def _app_objects(self):
        run = self.run
        return (
            self.start,
            run,
            self.operator,
            run.prelaunch,
            run.prelaunch.candidate,
            run.ready_qualification,
            run.app_objects,
            run.input_pins,
        )

    def _context(self):
        require(type(self) is AppAuthorizedFinalized)
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        start, run = self.start, self.run
        require(type(start) is app_begin.AppStart and type(run) is app_begin.execution.AppLaunch)
        require(all(a is b for a, b in zip(self._app_objects(), self.app_origins, strict=True)))
        require(start.original_run is run and run.begin_owner is start and run.begin_attempted)
        original, q = run.prelaunch, run.ready_qualification
        require(type(original) is app_begin.execution.inputs.NativeLaunchQualification)
        require(type(q) is app_begin.execution.readiness.NativeReadyQualification)
        require(original.candidate.native_execution_owner is run)
        require(original.candidate.native_execution_used is True)
        require(original.candidate.native_ready_owner is q)
        require(original.candidate.native_ready_used is True)
        require(run.qualify is q and q.prelaunch is original and q.ready is self.ready)
        require(run.ready is run._owned_ready and run.client is run._owned_client)
        # Do not call AppLaunch._app_context(), Ready.check_before_begin(), or
        # a full live input qualifier here: worker/init exits are legitimate.
        require(not start.failed and not start.closed and start.owner == self.owner and start.used)
        require(not run.failed and not run.closed and run.used and run.confirm_attempted)
        require(run.owner == self.owner)
        require(start.run is run and start.plan is self.plan is run.plan)
        require(self.operator.plan is self.plan and self.operator.clock is self.plan.original_clock)
        require(start.ready is self.ready is run.ready and self.ready.client is run.client)
        require(start.relay is self.relay and start.ledger is self.ledger)
        require(start.objects is self.origins and start.original is self.original)
        current = (
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
            self.ledger,
        )
        require(all(a is b for a, b in zip(current, self.origins, strict=True)))
        require(
            (
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
                self.ledger.directory,
                self.ledger.binding,
                self.ledger._directory_identity,
            )
            == self.original
        )
        require(start._begin_result is self.begin and self.begin[0] is self.relay)
        require(self.begin[1] == base.encode(start.authorization) and self.begin[2] is start.intent)
        require(start.proof == self.proof and self._values() == self.seal)
        require(run.journal is self.journal and self.journal.fd == self.fd)
        require(binding.identity(os.fstat(self.fd))[:6] == self.dir_id)
        require(type(self.files) is worker_exit.Finalized)
        require(self.files.relay is self.relay and self.files.operator is self.operator)
        self.files._context()
        require(self.publication is self._publication_return)
        if self.exited is not None:
            result, digest = self.exited
            require(self.files.used and self.relay.phase == "exited")
            require(self.files._exit_receipt[0] is result)
            require(self.files._exit_receipt[1] == digest == base.checksum(asdict(result)))


class AppFinalizedHost(begin.FinalizedHost):
    """Full ordinary host/static-file reads joined to original App finalization.

    Native flags remain unknown. This is evidence, not recovery dispatch, installed
    source qualification or normal-start health. Existing direct host and recovery
    admission gates intentionally do not accept this leaf.
    """

    def __init__(self, reader):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = False
        self.end = None
        try:
            require(type(self) is AppFinalizedHost and type(reader) is AppAuthorizedFinalized)
            self.reader = reader
            self.start, self.run, self.plan = reader.start, reader.run, reader.plan
            self.docker = self.run.read.docker
            self.origins = (reader, self.start, self.run, self.plan, self.docker, self.run.read)
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _guard(self):
        require(type(self) is AppFinalizedHost)
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        reader, run = self.reader, self.run
        require(type(reader) is AppAuthorizedFinalized and reader.phase == "published")
        require(reader.start is self.start and reader.run is run and reader.plan is self.plan)
        require(
            all(
                a is b
                for a, b in zip(
                    (reader, reader.start, reader.run, reader.plan, run.read.docker, run.read),
                    self.origins,
                    strict=True,
                )
            )
        )
        require(type(self.docker) is begin.plans.ordinary.Docker)
        require(self.docker.path == "/var/run/docker.sock")
        require(self.docker is run.qualify.docker)
        reader._context()


if __name__ == "__main__":
    raise SystemExit("Uninstalled App finalized evidence only; no recovery actions enabled.")
