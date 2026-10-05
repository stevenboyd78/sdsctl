#!/usr/bin/env python3
"""Explicit App recording recovery in the ORIGINAL session only.

Uninstalled. This is not a service, new recovery budget, independent supervisor
or implicit failure fallback. Success, preserved and never-authorized evidence
have separate entrances. Direct-policy gates and passive commands are unchanged.
"""

from __future__ import annotations

import os
import time
from threading import Lock, get_ident

import supplemental_recording_app_failure as failure
import supplemental_recording_app_finalized as finalized

begin, launch, require = finalized.begin, finalized.launch, finalized.require
base, plans = begin.base, begin.plans


class AppRestoredHost(begin._RestoredNormal, begin.FinalizedHost):
    """Original App completion plus a new bounded normal-App status read.

    The original journal must already hold worker/init exits and normal-start
    intent. The inherited fixed reader admits only the newly observed normal
    generation. No candidate probe or previous health result is reused.
    """

    def __init__(self, reader):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.closed = False
        self.end = None
        try:
            require(type(self) is AppRestoredHost)
            require(type(reader) is finalized.AppAuthorizedFinalized)
            self.reader = reader
            self.start, self.run, self.plan = reader.start, reader.run, reader.plan
            self.docker = self.run.read.docker
            self.origins = (reader, self.start, self.run, self.plan, self.docker, self.run.read)
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _guard(self):
        require(type(self) is AppRestoredHost)
        require(not self.failed and not self.closed and self.owner == (os.getpid(), get_ident()))
        reader, run = self.reader, self.run
        require(type(reader) is finalized.AppAuthorizedFinalized and reader.phase == "published")
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
        require(type(self.docker) is plans.ordinary.Docker)
        require(self.docker.path == "/var/run/docker.sock")
        require(self.docker is run.qualify.docker)
        reader._context()


def recover_finalized(reader, session, wait):
    """Consume one original App success continuation; never retry a failed read.

    Only the existing session may dispatch candidate stop and normal restore. Its
    original process/CLI owners, journal, clock and fixed recovery limit remain in
    force. Failed observation stays unavailable while independent ticks can expire
    the same policy. This function creates no service, session or new authority.
    """
    require(type(reader) is finalized.AppAuthorizedFinalized)
    require(reader.owner == (os.getpid(), get_ident()) and not reader.recovery_attempted)
    reader.recovery_attempted = True
    reader._context()
    require(reader.phase == "published")
    return _recover_closed(reader, reader.run, session, wait)


def recover_preserved(reader, run, session, wait):
    """Original independently confirmed exits; recording remains unconfirmed.

    Requires the actual closed ledger and retained Operator, never a failed
    success reader or reconstructed scope. This entrance does not abandon a
    ledger, create a recording receipt, refresh Ready or grant another attempt.
    """
    require(type(reader) is failure.reconcile.Preserved)
    require(reader.owner == (os.getpid(), get_ident()) and not reader.recovery_attempted)
    reader.recovery_attempted = True
    reader._context()
    return _recover_closed(reader, run, session, wait)


def recover_never_authorized(reader, run, session, wait):
    """Original exited native run, prepared-only ledger and no authorization.

    Original App Ready/qualification must have been acquired before failure.
    Earlier launch failure is not inferred safe from pristine files. This never
    writes a fake recording permission or converts an attempted run to pristine.
    """
    require(type(reader) is failure.reconcile.NeverAuthorized)
    require(reader.owner == (os.getpid(), get_ident()) and not reader.recovery_attempted)
    reader.recovery_attempted = True
    reader._context()
    return _recover_closed(reader, run, session, wait)


def _recover_closed(reader, run, session, wait):
    """Shared bounded mechanism; only the three closed App evidence routes."""
    require(type(session) is launch.bootstrap.RecoverySession and callable(wait))
    require(
        type(reader)
        in (
            finalized.AppAuthorizedFinalized,
            failure.reconcile.Preserved,
            failure.reconcile.NeverAuthorized,
        )
    )
    require(type(run) is finalized.app_begin.execution.AppLaunch)
    journal, plan, docker = reader.journal, reader.plan, run.read.docker
    processes, dispatch, executor = session.processes, session.dispatch, session.executor
    require(type(processes) is begin.TrackedProcesses and type(dispatch) is begin.TrackedDispatch)
    require(type(executor) is launch.bootstrap.Executor)
    images = {base.NORMAL: plan.normal.image, base.CANDIDATE: plan.candidate.image}
    owner = reader.owner

    def context():
        require(owner == (os.getpid(), get_ident()))
        require(session.journal is journal and executor.journal is journal)
        require(session.processes is processes and session.dispatch is dispatch)
        require(session.executor is executor and session.consume_operator is None)
        require(executor.read == session._read and executor.send == session._send)
        require(processes.journal is journal and dispatch.journal is journal)
        require(processes.docker is docker and dispatch.docker is docker)
        require(not processes.closed and processes.images == images)
        require(dispatch.image == plan.cli_image and dispatch.generation == plan.cli_generation)
        require(journal is reader.journal and reader.plan is plan)
        require(run.plan is plan and run.read.docker is docker)
        if type(reader) is finalized.AppAuthorizedFinalized:
            require(reader.run is run)
        require(reader.recovery_attempted and not reader.closed)

    context()
    if type(reader) is finalized.AppAuthorizedFinalized:
        reader._history(time.monotonic() + 2)
        host = finalized.AppFinalizedHost(reader)
    else:
        reader._check(time.monotonic() + 2)
        host = (
            failure.AppNeverAuthorizedHost(reader, run)
            if type(reader) is failure.reconcile.NeverAuthorized
            else failure.AppPreservedHost(reader, run)
        )
    restored = None
    failed = False
    try:
        if type(host) is finalized.AppFinalizedHost:
            restored = AppRestoredHost(reader)
        elif type(host) is failure.AppNeverAuthorizedHost:
            restored = failure.AppNeverAuthorizedRestoredHost(reader, run)
        else:
            restored = failure.AppPreservedRestoredHost(reader, run)

        def read():
            nonlocal failed
            try:
                began = time.monotonic()
                end = began + 2
                require(not failed)
                context()
                require(session.read is read)
                machine = host._history(end)
                if machine.state.phase in ("candidate_running", "stopping_candidate") or (
                    type(host) is failure.AppNeverAuthorizedHost
                    and machine.state.phase == "starting_operator"
                ):
                    sample = host.read()
                else:
                    require(machine.state.phase == "starting_normal")
                    sample = restored.read()
                context()
                require(session.read is read and began <= time.monotonic() < end)
                return sample
            except BaseException:
                # Do not refresh or substitute this reader. Original session
                # clock-only ticks remain independent of observation success.
                failed = True
                raise

        def bounded_wait(seconds):
            context()
            require(session.read is read and seconds == 0.25)
            wait(seconds)
            context()
            require(session.read is read)

        session.read = read
        return session.run(bounded_wait)
    finally:
        host.close()
        if restored is not None:
            restored.close()


if __name__ == "__main__":
    raise SystemExit("Uninstalled original-session App recovery only; no service enabled.")
