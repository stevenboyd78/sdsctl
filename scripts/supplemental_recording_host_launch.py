#!/usr/bin/env python3
"""Schema3 bootstrap policy -> one original idle/Engine/Ready launch.

Uninstalled host adapter segment. The supplied read/qualify functions are trusted
host collectors, not network callbacks: they must independently check installed
source, runtime, environment, mounts, jobs and ownership against the original
plan. This joins their results to actual retained idle/process/Engine objects;
it does not make supplied pins self-authenticating or install independent recovery.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock, Thread, current_thread, get_ident

import supplemental_recording_host_source as helper_source
import supplemental_recording_idle_observer as idle_module
import supplemental_recording_normal_read as normal_read
import supplemental_recording_probe_exec as probe_exec
import supplemental_recording_ready as received
import supplemental_recording_runtime as runtime
import supplemental_recording_time_domain as time_domain
from supplemental_handoff_host import TrackedDispatch
from supplemental_handoff_recovery import TrackedProcesses

plans = idle_module.host_plan
bootstrap, base = plans.bootstrap, plans.base
engine = received.engine
binding, execution = engine.dispatch.binding, engine.dispatch.execution
MESSAGE = "Recording host launch is unconfirmed; preserve the case and do not retry."


class UnconfirmedHostLaunch(ValueError):
    """No uncertainty permits another policy intent, create, attach or begin."""


def require(value):
    if not value:
        raise UnconfirmedHostLaunch(MESSAGE)


def _verified_history(plan, projected, journal, end):
    """Launch/transfer history stays bounded by the original native stop time."""
    base.clock(end)
    require(time.monotonic() < end <= min(time.monotonic() + 2, plan.lease["stop_by"]))
    return _journal_history(plan, projected, journal, end)


def _journal_history(plan, projected, journal, end):
    """Fresh bytes and pure replay; caller must also enforce its phase deadline."""
    base.clock(end)
    require(time.monotonic() < end <= time.monotonic() + 2)
    require(type(journal) is bootstrap.Journal and journal.path == plan.root / "journal")
    journal.check_directory()
    machine = journal.replayed(end)
    entries = tuple(base.encode(entry) for entry in journal.entries)
    require(0 < len(journal.entries) <= journal.max_events)
    require(
        sorted(os.listdir(journal.fd)) == [journal.name(i) for i in range(len(journal.entries))]
    )
    for index, expected in enumerate(entries):
        name = journal.name(index)
        info = os.stat(name, dir_fd=journal.fd, follow_symlinks=False)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o7777 == 0o600)
        raw = binding.protected.evidence.read_bytes(
            journal.fd, name, limit=base.MAX_BYTES, deadline=end
        )
        require(raw == expected)
        require(
            binding.identity(os.stat(name, dir_fd=journal.fd, follow_symlinks=False))
            == binding.identity(info)
        )
    journal.check_directory()
    require(sorted(os.listdir(journal.fd)) == [journal.name(i) for i in range(len(entries))])
    require(tuple(base.encode(entry) for entry in journal.entries) == entries)
    require(journal.replayed(end) is machine)
    require(time.monotonic() < end)
    require(type(machine) is bootstrap.Machine)
    require(
        base.encode(journal.entries[0]["event"])
        == base.encode(plan.preparation(machine.baseline, projected))
    )
    require((machine.case_id, machine.boot_id) == (plan.case, plan.boot))
    require(machine.created_at == plan.deadlines.issued_at)
    require(machine.hard_deadline == plan.deadlines.recover_by)
    require(machine.bootstrap == plan.bootstrap and machine.contract == plan.candidate.contract)
    return machine


class PreHandoffHost:
    """One current normal-running/candidate-absent sample, before any transfer.

    Joins the complete existing host observer, original pristine recording
    manifest and a fixed cached normal-App read. Construction is passive. This
    does not create the plan's ORIGINAL baseline or replace its issued_at; that
    separately sealed preparation observation is still required by the journal.

    Only the exact original normal generation may receive the cached read. An
    existing candidate container, even exited, refuses rather than becoming a
    new case. No native candidate request, scanner command, journal/notice write,
    App start/stop, process acquisition or recovery authorization exists here.
    Expected-input provenance, actual helper/source/runtime and independent
    supervision are separate gates. Every instance is single-use, including
    failures; callers must not turn that API property into automatic retries.
    """

    def __init__(self, plan, projected, docker):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = self.used = False
        self.normal_reader = None
        try:
            self.plan_pin = plans.PinnedPlan(plan)
            plan.check_projection(projected)
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            self.plan, self.projected, self.docker = plan, projected, docker
            self.objects = plan, projected, docker
            self.original = plan.raw, projected.sha256
        except BaseException as error:
            self._fail(error)

    def _context(self):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        require(
            all(
                current is original
                for current, original in zip(
                    (self.plan, self.projected, self.docker), self.objects, strict=True
                )
            )
        )
        self.plan_pin.check(self.plan)
        self.plan.check_projection(self.projected)
        require((self.plan.raw, self.projected.sha256) == self.original)
        require(
            type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        return observed.boot, now

    def _guard(self):
        boot, now = self._context()
        require(now < self.plan.deadlines.ready_by)
        return boot, now

    def _candidate_absent(self):
        indexed = plans.ordinary.container_index(self.docker.containers())
        require("/app_" + base.CANDIDATE not in indexed)

    def _native(self, slug, generation):
        self._guard()
        require(slug == base.NORMAL and generation == self.plan.normal_generation)
        require(type(self.normal_reader) is normal_read.Sample)
        return self.normal_reader.read(slug, generation)

    def _observer(self):
        plan = self.plan
        layouts = {item.slug: item for item in plan.layouts}
        collector = plans.host.FilesCollector(
            layouts[base.NORMAL],
            layouts[base.CANDIDATE],
            plans.host.recording.Collector(self.projected.host),
            lambda: plans.host.Capture("pristine"),
        )
        supervisor = plans.ordinary.SupervisorReads(
            self.docker, image=plan.cli_image, incarnation=plan.cli_generation
        )
        return plans.host.HostObserver(
            self.docker,
            supervisor,
            seals=(plan.normal, plan.candidate),
            installed_versions=dict(plan.installed_versions),
            other_scanner_apps=frozenset(plan.other_scanner_apps),
            core_image=plan.core_image,
            core_generation=plan.core_generation,
            core_version=plan.core_version,
            read_clock=self._guard,
            collect_files=collector,
            read_native=self._native,
            network=plans.ordinary.AUDIO_NETWORK,
        )

    def read(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(not self.used)
            self.used = True
            started = time.monotonic()
            boot, began = self._guard()
            self._candidate_absent()
            self.normal_reader = normal_read.Sample(self.plan, self.docker)
            reader = self.normal_reader
            sample = self._observer().read()
            require(type(sample) is bootstrap.recovery.Sample)
            self._candidate_absent()
            end_boot, ended = self._guard()
            require(boot == end_boot == sample.boot_id)
            require(began <= sample.observation.sampled_at <= sample.now <= ended)
            require(0 <= ended - began < 2 and 0 <= time.monotonic() - started < 2)
            require(self.normal_reader is reader and reader.used and not reader.failed)
            observed = sample.observation
            require(
                observed.normal
                == base.App(
                    self.plan.normal.pin, "running", self.plan.normal_generation, True, False
                )
            )
            require(observed.candidate == base.App(self.plan.candidate.pin, "stopped"))
            require(observed.jobs_idle and observed.core_running and observed.other_owners_stopped)
            require(
                observed.files
                == bootstrap.recording.Files(
                    self.plan.candidate.contract.sha256,
                    "pristine",
                    self.plan.candidate.contract.baseline_sha256,
                )
            )
            return bootstrap.recovery.Sample(boot, ended, replace(observed, sampled_at=began))
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None


class TransferHost(PreHandoffHost):
    """Current pristine host observations during the ORIGINAL initial transfer.

    Unlike the one-use preflight, this reader supports successive observations
    while the same journal advances from prepared to candidate_idle. Each read
    uses fresh complete host/files/cache evidence and original journal bytes.
    An uncertain read permanently fails this instance; it cannot be reset.

    Candidate native health and recording state remain UNKNOWN. Only the
    original RecoverySession may bind init/CLI receipts and advance the policy;
    Idle and Launch must independently qualify the candidate before any exec.
    No notice, journal event, stop/start, scanner command or process acquisition
    is performed here. This is not a post-launch or restoration reader.
    """

    PHASES = frozenset(
        ("prepared", "requested", "stopping_normal", "starting_candidate", "candidate_idle")
    )

    def __init__(self, plan, projected, journal, docker):
        super().__init__(plan, projected, docker)
        try:
            require(type(journal) is bootstrap.Journal)
            require(journal.path == plan.root / "journal" and journal.fd >= 0)
            self.journal = self.original_journal = journal
            self.journal_fd = journal.fd
            self.journal_identity = runtime.identity(os.fstat(journal.fd))[:5]
            self.machine = None
        except BaseException as error:
            self._fail(error)

    def _guard(self):
        observed = super()._guard()
        require(self.journal is self.original_journal and type(self.journal) is bootstrap.Journal)
        require(self.journal.fd == self.journal_fd)
        require(runtime.identity(os.fstat(self.journal_fd))[:5] == self.journal_identity)
        return observed

    def _history(self, end):
        self._guard()
        machine = _verified_history(self.plan, self.projected, self.journal, end)
        state = machine.state
        require(state.phase in self.PHASES)
        require(state.launch_intent_sha256 is None and state.ready_evidence_sha256 is None)
        require(state.authorization_generation is None and state.operator_exit_sha256 is None)
        require(not state.finish_requested)
        if state.phase in ("prepared", "requested", "stopping_normal"):
            self._candidate_absent()
        return machine

    def _native(self, slug, generation):
        if slug == base.NORMAL:
            return super()._native(slug, generation)
        self._guard()
        require(slug == base.CANDIDATE and self.machine is self.journal.machine)
        require(self.machine.state.phase in ("starting_candidate", "candidate_idle"))
        recorded = self.machine.state.candidate_generation
        require(recorded is None or recorded == generation)
        # Explicitly no candidate cache/exec/claim read. This is not idle proof.
        return plans.ordinary.NativeState(generation, None, None)

    def read(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            boot, started = self._guard()
            end = min(began + 2, self.plan.lease["ready_by"])
            self.machine = machine = self._history(end)
            entries = tuple(base.encode(entry) for entry in self.journal.entries)
            self.normal_reader = normal_read.Sample(self.plan, self.docker)
            reader = self.normal_reader
            sample = self._observer().read()
            require(type(sample) is bootstrap.recovery.Sample)
            require(self._history(end) is machine)
            require(tuple(base.encode(entry) for entry in self.journal.entries) == entries)
            require(self.normal_reader is reader and not reader.failed)
            observed = sample.observation
            if observed.normal.state == "running":
                require(observed.normal.generation == self.plan.normal_generation)
            if observed.candidate.state == "running":
                require(machine.state.phase in ("starting_candidate", "candidate_idle"))
                recorded = machine.state.candidate_generation
                require(recorded is None or observed.candidate.generation == recorded)
                require(observed.candidate.healthy is None and observed.candidate.recording is None)
            require(
                observed.files
                == bootstrap.recording.Files(
                    self.plan.candidate.contract.sha256,
                    "pristine",
                    self.plan.candidate.contract.baseline_sha256,
                )
            )
            end_boot, ended = self._guard()
            require(boot == sample.boot_id == end_boot)
            require(started <= observed.sampled_at <= sample.now <= ended)
            require(0 <= ended - started < 2 and began <= time.monotonic() < end)
            return bootstrap.recovery.Sample(boot, ended, replace(observed, sampled_at=started))
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()


class NeverLaunchedHost(TransferHost):
    """Pristine cancellation observations using the ORIGINAL transfer session.

    Construct while the original candidate is idle and its init witness is
    still retained. Reads require an explicit durable finish, and refuse any
    operator or recording authorization. No fallback from a failed Launch is
    supported. This collector sends no command and never acquires a process.

    Recovery may outlast native readiness/stop, but only within the ORIGINAL
    recovery deadline and each original two-second observation window. The
    session still supplies init/CLI exits and one-use App dispatch. Installed
    source/runtime qualification and outside supervision remain separate gates.
    """

    PHASES = frozenset(("candidate_idle", "stopping_candidate", "starting_normal", "complete"))

    def __init__(self, transfer, session):
        require(type(transfer) is TransferHost and not transfer.failed)
        super().__init__(transfer.plan, transfer.projected, transfer.journal, transfer.docker)
        try:
            require(type(session) is bootstrap.RecoverySession and session.read == transfer.read)
            self.transfer, self.session = transfer, session
            self.processes, self.dispatch, self.executor = (
                session.processes,
                session.dispatch,
                session.executor,
            )
            self.origins = transfer, session, self.processes, self.dispatch, self.executor
            state = self.journal.machine.state
            require(state.phase == "candidate_idle")
            self.records = state.processes
            require(
                len(self.records) == 2
                and {r.slug for r in self.records} == {base.NORMAL, base.CANDIDATE}
            )
            self.candidate_record = next(r for r in self.records if r.slug == base.CANDIDATE)
            self.candidate_witness = self.processes.witnesses.get(base.CANDIDATE)
            require(self.candidate_witness is not None)
            self.recovery_attempted = False
            self.end = None
            self._guard()
        except BaseException as error:
            self._fail(error)

    def _guard(self):
        boot, now = PreHandoffHost._context(self)
        require(now < self.plan.deadlines.recover_by)
        require(self.journal is self.original_journal and self.journal.fd == self.journal_fd)
        require(runtime.identity(os.fstat(self.journal_fd))[:5] == self.journal_identity)
        transfer, session, processes, dispatch, executor = self.origins
        require(
            all(
                a is b
                for a, b in zip(
                    (self.transfer, self.session, self.processes, self.dispatch, self.executor),
                    self.origins,
                    strict=True,
                )
            )
        )
        require(not transfer.failed and transfer.owner == self.owner)
        require(
            all(
                a is b
                for a, b in zip(
                    (transfer.plan, transfer.projected, transfer.docker),
                    transfer.objects,
                    strict=True,
                )
            )
        )
        require(transfer.plan is self.plan and transfer.projected is self.projected)
        require(transfer.journal is self.journal and transfer.docker is self.docker)
        require(type(processes) is TrackedProcesses and type(dispatch) is TrackedDispatch)
        require(type(executor) is bootstrap.Executor and session.executor is executor)
        require(
            session.journal
            is processes.journal
            is dispatch.journal
            is executor.journal
            is self.journal
        )
        require(session.processes is processes and session.dispatch is dispatch)
        require(processes.docker is dispatch.docker is self.docker and not processes.closed)
        require(
            processes.images
            == {base.NORMAL: self.plan.normal.image, base.CANDIDATE: self.plan.candidate.image}
        )
        require(
            (dispatch.image, dispatch.generation) == (self.plan.cli_image, self.plan.cli_generation)
        )
        require(session.consume_operator is None and session.read in (transfer.read, self.read))
        require(executor.read == session._read and executor.send == session._send)
        require(self.journal.machine.state.processes == self.records)
        record = self.candidate_record
        require(
            self.candidate_witness.identity
            == runtime.processes.ProcessIdentity(
                record.pid, record.start_ticks, record.container_id
            )
        )
        if record.generation not in self.journal.machine.state.exited_processes:
            require(processes.witnesses.get(base.CANDIDATE) is self.candidate_witness)
        else:
            require(base.CANDIDATE not in processes.witnesses)
        return boot, now

    def _history(self, end):
        self._guard()
        require(self.end == end and time.monotonic() < end)
        machine = _journal_history(self.plan, self.projected, self.journal, end)
        state = machine.state
        require(state.phase in self.PHASES and state.finish_requested)
        require(state.launch_intent_sha256 is None and state.launch_plan_sha256 is None)
        require(state.idle_evidence_sha256 is None and state.ready_evidence_sha256 is None)
        require(state.authorization_generation is None and state.operator_exit_sha256 is None)
        require(state.recording_outcome == "not_attempted" and state.files_stage == "pristine")
        require(
            state.artifact_sha256 is None
            and state.candidate_generation == self.candidate_record.generation
        )
        require(machine.process_bound(base.NORMAL, exited=True))
        require(
            machine.execution_closed("stopping_normal")
            and machine.execution_closed("starting_candidate")
        )
        if state.phase in ("starting_normal", "complete"):
            require(machine.process_bound(base.CANDIDATE, exited=True))
            require(machine.execution_closed("stopping_candidate"))
        return machine

    def _candidate(self, *, exited=False):
        self._guard()
        current = self.docker.container("app_" + base.CANDIDATE)
        require(type(current) is dict and current.get("Id") == self.candidate_record.container_id)
        if exited:
            plans.ordinary.retained_exit(
                current,
                name="app_" + base.CANDIDATE,
                image=self.plan.candidate.image,
                cid=self.candidate_record.container_id,
            )
        self._guard()

    def _native(self, slug, generation):
        machine = self._history(self.end)
        if slug == base.CANDIDATE:
            require(generation == self.candidate_record.generation)
            return plans.ordinary.NativeState(generation, None, None)
        require(slug == base.NORMAL and machine.state.phase in ("starting_normal", "complete"))
        require(generation != self.plan.normal_generation)
        require(machine.state.restored_generation in (None, generation))
        self._candidate(exited=True)
        result = self.normal_reader.read(slug, generation)
        self._candidate(exited=True)
        require(self._history(self.end) is machine)
        return result

    def read(self):
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            boot, started = self._guard()
            self.end = end = began + min(2, self.plan.deadlines.recover_by - started)
            self.machine = machine = self._history(end)
            entries = tuple(base.encode(entry) for entry in self.journal.entries)
            restoring = machine.state.phase in ("starting_normal", "complete")
            self._candidate(exited=restoring)
            self.normal_reader = reader = normal_read.Sample(self.plan, self.docker)
            sample = self._observer().read()
            require(type(sample) is bootstrap.recovery.Sample)
            self._candidate(exited=restoring)
            require(self._history(end) is machine)
            require(tuple(base.encode(entry) for entry in self.journal.entries) == entries)
            require(self.normal_reader is reader and not reader.failed)
            obs = sample.observation
            require(obs.candidate.healthy is None and obs.candidate.recording is None)
            if obs.candidate.state == "running":
                require(
                    not restoring and obs.candidate.generation == self.candidate_record.generation
                )
            if obs.normal.state == "running":
                require(restoring and obs.normal.generation != self.plan.normal_generation)
                require(machine.state.restored_generation in (None, obs.normal.generation))
            require(
                obs.files
                == bootstrap.recording.Files(
                    self.plan.candidate.contract.sha256,
                    "pristine",
                    self.plan.candidate.contract.baseline_sha256,
                )
            )
            end_boot, ended = self._guard()
            require(boot == sample.boot_id == end_boot)
            require(started <= obs.sampled_at <= sample.now <= ended)
            require(0 <= ended - started < 2 and began <= time.monotonic() < end)
            return bootstrap.recovery.Sample(boot, ended, replace(obs, sampled_at=started))
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.end = None
                self.lock.release()


def recover_never_launched(reader, wait):
    """Continue one original session after explicit, pristine idle cancellation.

    No implicit finish, new session, native launch or reconstructed process
    handle. The existing session closes its owned handles when its loop exits;
    its journal and outside supervision remain the original caller's custody.
    A successful result means normal restoration, never a recording success.
    """
    require(type(reader) is NeverLaunchedHost and callable(wait))
    require(not reader.recovery_attempted and reader.owner == (os.getpid(), get_ident()))
    reader.recovery_attempted = True
    reader._guard()
    session = reader.session
    require(session.read == reader.transfer.read)
    # Validate actual explicit-finish bytes and fresh pristine input before
    # routing. This sample grants no action and is not reused by the executor.
    reader.read()
    require(reader.journal.machine.state.phase == "candidate_idle")
    session.read = reader.read

    def bounded_wait(seconds):
        reader._guard()
        require(session.read == reader.read and seconds == 0.25)
        wait(seconds)
        reader._guard()
        require(session.read == reader.read)

    return session.run(bounded_wait)


class BootstrapHost:
    """Full read-only host sample for one original idle candidate, before begin.

    Reuses the fixed Supervisor reader, complete host observer and pristine
    recording collector. It never selects a native exec/cache query: PID1 idle
    evidence leaves both health flags unknown, even after the operator starts.
    Launch joins its separately authenticated one-use probe only after this
    sample. This is not the normal-App readiness or active-recording collector.

    All images/files/options/versions/jobs/Core/network/other owners are freshly
    collected by HostObserver. Original manifests are never recaptured as a new
    baseline. Installed source/runtime qualification, original pidfd provenance,
    independent recovery and outer I/O supervision remain separate obligations.
    Construction does not dispatch or perform a host request. A failed read
    permanently consumes this instance; caller-owned handles are never closed.
    """

    def __init__(self, plan, projected, idle, witness, docker):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed = False
        self.pending = None
        try:
            self.plan_pin = plans.PinnedPlan(plan)
            plan.check_projection(projected)
            require(type(idle) is idle_module.Idle and idle.plan is plan)
            require(type(witness) is engine.dispatch.process.ProcessWitness)
            require(idle.init == witness.identity)
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            base.digest(idle.generation)
            self.plan, self.projected, self.idle = plan, projected, idle
            self.witness, self.docker = witness, docker
            self.init, self.generation = witness.identity, idle.generation
            self.fd, self.fd_identity = witness.fd, runtime.identity(os.fstat(witness.fd))
            self.original = (plan.raw, projected.sha256, self.init, self.generation)
        except BaseException as error:
            self._fail(error)

    def _clock(self):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(now < self.plan.deadlines.ready_by)
        return observed.boot, now

    def _guard(self):
        self.plan_pin.check(self.plan)
        self.plan.check_projection(self.projected)
        require((self.plan.raw, self.projected.sha256, self.init, self.generation) == self.original)
        require(type(self.idle) is idle_module.Idle and self.idle.plan is self.plan)
        require(type(self.witness) is engine.dispatch.process.ProcessWitness)
        require(self.witness.identity == self.idle.init == self.init)
        require(self.idle.generation == self.generation and self.witness.fd == self.fd)
        require(runtime.identity(os.fstat(self.fd)) == self.fd_identity)
        require(not self.witness.exited())
        require(
            engine.dispatch.process.read_identity(self.init.pid, self.init.container_id)
            == self.init
        )
        require(
            type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        return self._clock()

    @staticmethod
    def _unknown_native(slug, generation):
        require(slug in (base.NORMAL, base.CANDIDATE))
        return plans.ordinary.NativeState(generation, None, None)

    def _observer(self, read_clock):
        plan = self.plan
        layouts = {item.slug: item for item in plan.layouts}
        collector = plans.host.FilesCollector(
            layouts[base.NORMAL],
            layouts[base.CANDIDATE],
            plans.host.recording.Collector(self.projected.host),
            lambda: plans.host.Capture("pristine"),
        )
        supervisor = plans.ordinary.SupervisorReads(
            self.docker, image=plan.cli_image, incarnation=plan.cli_generation
        )
        return plans.host.HostObserver(
            self.docker,
            supervisor,
            seals=(plan.normal, plan.candidate),
            installed_versions=dict(plan.installed_versions),
            other_scanner_apps=frozenset(plan.other_scanner_apps),
            core_image=plan.core_image,
            core_generation=plan.core_generation,
            core_version=plan.core_version,
            read_clock=read_clock,
            collect_files=collector,
            read_native=self._unknown_native,
            network=plans.ordinary.AUDIO_NETWORK,
        )

    def prepare(self):
        """Start only a fixed read-only host sample, without returning evidence.

        This permits Supervisor/file I/O to overlap independent runtime hashing.
        Idle/namespace/pidfd ownership stays on the original calling thread. The
        worker never performs a native probe, journal append, App control or
        recording action. Its original start and two-second deadline are held
        across collection/join; finishing never renews freshness. A failed outer
        qualification must discard this object, even if its worker later finishes.

        A blocked kernel/Engine read still needs the independent outer process
        supervisor. discard() invalidates a pending result, not that outstanding
        read; its thread handle is retained and never supplies process-exit proof.
        """
        acquired = False
        try:
            require(not self.failed and self.owner == (os.getpid(), get_ident()))
            require(self.lock.acquire(blocking=False))
            acquired = True
            require(self.pending is None)
            started = time.monotonic()
            boot, began = self._clock()
            self._guard()
            before = self.idle.read()
            require(type(before) is idle_module.Evidence)
            pending = _PreparedHostRead(
                self, boot, began, before, min(started + 2, self.plan.lease["ready_by"])
            )
            self.pending = pending
            pending.worker.start()
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def discard(self):
        """Permanently consume pending evidence; never signal or close pidfds."""
        require(self.owner == (os.getpid(), get_ident()))
        self.failed = True
        if self.pending is not None:
            self.pending.cancelled.set()

    def __call__(self):
        acquired = False
        try:
            require(not self.failed and self.owner == (os.getpid(), get_ident()))
            require(self.lock.acquire(blocking=False))
            acquired = True
            if self.pending is not None:
                pending = self.pending
                self._guard()
                sample = pending.finish()
                result = self._complete(pending.boot, pending.began, pending.before, sample)
                require(self.pending is pending and not self.failed)
                self.pending = None
                return result
            boot, began = self._clock()
            self._guard()  # Include the first identity/manifest checks in freshness.
            before = self.idle.read()
            require(type(before) is idle_module.Evidence)
            return self._complete(boot, began, before, self._observer(self._clock).read())
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _complete(self, boot, began, before, sample):
        require(type(sample) is bootstrap.recovery.Sample)
        after = self.idle.read()
        require(type(after) is idle_module.Evidence)
        require(replace(after, sampled_at=before.sampled_at) == before)
        require(after.sampled_at >= before.sampled_at)
        end_boot, ended = self._guard()
        require(boot == end_boot == sample.boot_id and began <= sample.now <= ended)
        require(0 <= ended - began <= 2)
        require(began <= before.sampled_at <= after.sampled_at <= ended)
        plan = self.plan
        require(
            (before.plan_sha256, before.generation, before.init, before.lease_sha256)
            == (plan.sha256, self.generation, self.init, plan.lease_sha256)
        )
        observation = sample.observation
        require(observation.normal.state == "stopped")
        candidate = observation.candidate
        require(candidate.state == "running" and candidate.generation == self.generation)
        require(candidate.healthy is None and candidate.recording is None)
        require(
            observation.files
            == bootstrap.recording.Files(
                plan.candidate.contract.sha256, "pristine", plan.candidate.contract.baseline_sha256
            )
        )
        return bootstrap.recovery.Sample(
            boot, ended, replace(observation, sampled_at=min(began, observation.sampled_at))
        )

    def _fail(self, error):
        self.failed = True
        if self.pending is not None:
            self.pending.cancelled.set()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None


class _PreparedHostRead:
    """Internal, one fixed read-only worker; not a caller-supplied result API."""

    def __init__(self, host, boot, began, before, deadline):
        self.host, self.boot, self.began, self.before = host, boot, began, before
        self.deadline = deadline
        self.done, self.cancelled = Event(), Event()
        self.sample = self.error = None
        self.worker = Thread(target=self._run, name="sdsctl-bootstrap-host-read", daemon=True)
        self.original = (host, boot, began, before, deadline, self.worker)

    def _fixed(self):
        require(
            (self.host, self.boot, self.began, self.before, self.deadline, self.worker)
            == self.original
        )
        require(self.host.pending is self)

    def _clock(self):
        self._fixed()
        require(current_thread() is self.worker and os.getpid() == self.host.owner[0])
        require(not self.cancelled.is_set() and not self.host.failed)
        require(time.monotonic() < self.deadline)
        observed = plans.clock.read()
        self.host.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(
            observed.boot == self.boot and self.began <= now < self.host.plan.deadlines.ready_by
        )
        return observed.boot, now

    def _run(self):
        try:
            self._clock()
            self.sample = self.host._observer(self._clock).read()
            self._clock()
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def finish(self):
        self._fixed()
        require(self.host.owner == (os.getpid(), get_ident()))
        require(self.host.pending is self and not self.cancelled.is_set())
        require(time.monotonic() < self.deadline)
        require(self.done.wait(max(0, self.deadline - time.monotonic())))
        self.worker.join(max(0, self.deadline - time.monotonic()))
        require(not self.worker.is_alive() and time.monotonic() < self.deadline)
        require(not self.cancelled.is_set() and not self.host.failed)
        if self.error is not None:
            raise self.error
        require(type(self.sample) is bootstrap.recovery.Sample)
        return self.sample


def helper_proc_mount_profile(raw, *, mount_id, device):
    """Bounded target mountinfo policy, read through the trusted outer proc view.

    The real open proc-directory mount ID and device come from fdinfo/fstat,
    not this text. Refuse overmounted process/self/clock paths. Standard Docker
    masks of unrelated proc files are immaterial to those reads. This does not
    authenticate the caller's own proc mount or grant execution authority.
    Format: docs.kernel.org/filesystems/proc.html sections 3.5 and 3.8.
    """
    try:
        require(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024 and raw.endswith(b"\n"))
        require(type(mount_id) is int and mount_id > 0)
        require(type(device) is tuple and len(device) == 2)
        require(all(type(value) is int and value >= 0 for value in device))
        text = raw.decode("ascii")
        require("\x00" not in text and "\r" not in text and "\t" not in text)
        lines = text.splitlines()
        require(0 < len(lines) <= 4096)
        ids, proc, sys_mount = set(), None, None

        def path(value, *, target=True):
            require(re.search(r"\\(?!040|011|012|134)", value) is None)
            decoded = re.sub(r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), value)
            if target:
                require(decoded.startswith("/") and not decoded.startswith("//"))
                require(str(Path(decoded)) == decoded and ".." not in Path(decoded).parts)
            return decoded

        for line in lines:
            fields = line.split(" ")
            require(all(fields) and len(fields) >= 10 and fields.count("-") == 1)
            separator = fields.index("-")
            require(separator >= 6 and len(fields) == separator + 4)
            require(all(re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in fields[:2]))
            current_id = int(fields[0])
            require(current_id not in ids)
            ids.add(current_id)
            require(re.fullmatch(r"[0-9]{1,10}:[0-9]{1,10}", fields[2]) is not None)
            current_device = tuple(int(value) for value in fields[2].split(":"))
            # Filesystem-specific roots can be nsfs names such as mnt:[123].
            # Only mount targets are paths; selected proc roots are exact below.
            root, target = path(fields[3], target=False), path(fields[4])
            options = fields[5].split(",")
            require(len(set(options)) == len(options) and all(options))
            if target == "/proc":
                require(proc is None and root == "/")
                require(fields[separator + 1] == "proc")
                require({"nosuid", "nodev", "noexec"} <= set(options))
                require(("ro" in options) != ("rw" in options))
                require(current_id == mount_id and current_device == device)
                proc = current_id
            elif target == "/proc/sys":
                require(sys_mount is None and root == "/sys")
                require(fields[separator + 1] == "proc" and current_device == device)
                require("ro" in options and "rw" not in options)
                sys_mount = current_id
            elif target.startswith("/proc/"):
                first = target.split("/")[2]
                require(first not in ("self", "thread-self") and not first.isdigit())
                boot = "/proc/sys/kernel/random/boot_id"
                require(
                    target != boot
                    and not boot.startswith(target + "/")
                    and not target.startswith(boot + "/")
                )
            else:
                require(current_id != mount_id)
        require(proc is not None)
        return base.checksum({"kind": "finite-helper-proc-mount-view-v1", "mountinfo": text})
    except Exception:
        raise UnconfirmedHostLaunch(MESSAGE) from None


class HelperQualification:
    """Read-only startup qualification of one independently captured helper.

    Inputs come from the reviewed launcher, not the container being observed.
    The exact command and complete Engine configuration fingerprint are supplied
    separately from the plan's image/source/runtime/environment pins. The
    original live witness remains caller-owned. A replacement, late observation
    or failed read permanently consumes this instance; nothing is launched.

    This checks Engine-declared confinement, actual proc privilege state and
    the observed helper's proc mount against the trusted outer process view,
    not the outer view's provenance or the seccomp filter's contents, independent
    supervision, or the helper's own continuing clock domain. Those remain
    separate installation gates. A
    read-only Docker socket mount DOES NOT restrict Engine API authority.

    The explicit seven-argument passive probe additionally requires a live
    caller-owned ZeroDomain for this exact process and original plan clock.
    This never relaxes ordinary command/domain checks or enables a service;
    the caller must be the continuing original clock owner, not a later driver.
    """

    MAX_SECONDS = 2.0
    HELPER = Path("opt/sdsctl-recording-host")

    def __init__(
        self,
        plan,
        witness,
        docker,
        *,
        generation,
        command,
        configuration_sha256,
        image_environment_sha256,
        timezone,
        hostname,
        architecture,
        runtime_workers=1,
        zero_domain=None,
    ):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed, self.elapsed_seconds = False, None
        try:
            self.plan_pin = plans.PinnedPlan(plan)
            require(type(witness) is engine.dispatch.process.ProcessWitness)
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            for pin in (generation, configuration_sha256, image_environment_sha256):
                base.digest(pin)
            runtime._timezone_name(timezone)
            plans.text(hostname, r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?")
            require(architecture in ("amd64", "arm64"))
            require(type(runtime_workers) is int and runtime_workers in (1, 2))
            require(zero_domain is None or type(zero_domain) is time_domain.ZeroDomain)
            require(type(command) is tuple and len(command) == (6 if zero_domain is None else 7))
            require(all(type(part) is str for part in command))
            require(command[:3] == ("/usr/local/bin/python", "-I", "-B"))
            require(
                command[3]
                in {str(Path("/") / self.HELPER / name) for name in helper_source.HELPER_FILES}
                and command[4:6] == (str(plan.root), plan.sha256)
            )
            if zero_domain is not None:
                require(
                    command[3]
                    == "/opt/sdsctl-recording-host/supplemental_recording_service_input.py"
                    and command[6] == "--zero-offset-probe"
                )
            self.plan, self.witness, self.docker = plan, witness, docker
            self.zero_domain = zero_domain
            self.objects = plan, witness, docker, zero_domain
            self.init, self.fd = witness.identity, witness.fd
            self.fd_identity = runtime.identity(os.fstat(self.fd))
            self.generation, self.command = generation, command
            self.configuration_sha256 = configuration_sha256
            self.image_environment_sha256 = image_environment_sha256
            self.timezone, self.hostname, self.architecture = timezone, hostname, architecture
            self.runtime_workers = runtime_workers
            self.domain_sha256 = None if zero_domain is None else zero_domain.refresh().sha256
            self.original = self._pins()
            self._guard(min(time.monotonic() + self.MAX_SECONDS, plan.lease["ready_by"]))
        except BaseException as error:
            self._fail(error)

    def _pins(self):
        return (
            self.plan.raw,
            self.init,
            self.generation,
            self.command,
            self.configuration_sha256,
            self.image_environment_sha256,
            self.timezone,
            self.hostname,
            self.architecture,
            (type(self.runtime_workers), self.runtime_workers),
            self.domain_sha256,
        )

    def _guard(self, deadline):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        require(os.geteuid() == runtime.ROOT_UID and time.monotonic() < deadline)
        require(
            all(
                current is original
                for current, original in zip(
                    (self.plan, self.witness, self.docker, self.zero_domain),
                    self.objects,
                    strict=True,
                )
            )
        )
        self.plan_pin.check(self.plan)
        require(self._pins() == self.original)
        require(
            type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        require(self.witness.identity == self.init and self.witness.fd == self.fd)
        require(runtime.identity(os.fstat(self.fd)) == self.fd_identity)
        require(not self.witness.exited())
        require(
            engine.dispatch.process.read_identity(self.init.pid, self.init.container_id)
            == self.init
        )
        if self.zero_domain is not None:
            require(type(self.zero_domain) is time_domain.ZeroDomain)
            proof = self.zero_domain.refresh()
            require(proof.sha256 == self.domain_sha256)
            require(proof.init == self.init and proof.original_clock == self.plan.original_clock)
            require(proof.host_time == self.plan.original_clock.namespace)
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        require(observed.boottime_ns / plans.clock.NS < self.plan.deadlines.ready_by)
        require(time.monotonic() < min(deadline, self.plan.lease["ready_by"]))

    def _mounts(self, container):
        # Closed top-level routing: only the private case is declared writable.
        # /mnt/data contains the HAOS daemon root, so Engine uses rslave there;
        # all other binds remain rprivate. Source/root descriptor and complete
        # mount-table checks still gate reads. Propagation is not a recursive
        # read-only guarantee for future submounts or Engine API confinement.
        expected = {
            "/var/run/docker.sock": ("/var/run/docker.sock", False, "rprivate"),
            "/mnt/data": ("/mnt/data", False, "rslave"),
            str(self.plan.root): (str(self.plan.root), True, "rprivate"),
            "/opt/sdsctl-host-udp/udp": ("/proc/1/net/udp", False, "rprivate"),
            "/opt/sdsctl-host-udp/udp6": ("/proc/1/net/udp6", False, "rprivate"),
        }
        mounts = container.get("Mounts")
        require(type(mounts) is list and len(mounts) == len(expected))
        seen = set()
        for mount in mounts:
            require(type(mount) is dict)
            destination = mount.get("Destination")
            require(
                type(destination) is str and destination in expected and destination not in seen
            )
            seen.add(destination)
            source, writable, propagation = expected[destination]
            require(mount.get("Type") == "bind" and mount.get("Source") == source)
            require(mount.get("RW") is writable and mount.get("Propagation") == propagation)
        require(seen == set(expected))
        host = container["HostConfig"]
        requested = host.get("Mounts")
        require(type(requested) is list and len(requested) == len(expected))
        seen = set()
        for mount in requested:
            require(type(mount) is dict)
            keys = set(mount)
            if "BindOptions" in mount:
                # Admit only the explicit spelling of the required HAOS data
                # propagation. No recursive-writable or other bind options.
                require(mount.get("Target") == "/mnt/data")
                require(mount["BindOptions"] == {"Propagation": "rslave"})
                keys.remove("BindOptions")
            require(
                keys in ({"Type", "Source", "Target"}, {"Type", "Source", "Target", "ReadOnly"})
            )
            destination = mount["Target"]
            require(
                type(destination) is str and destination in expected and destination not in seen
            )
            seen.add(destination)
            source, writable, _ = expected[destination]
            require(
                mount["Type"] == "bind"
                and mount["Source"] == source
                # Engine omits the false zero value for the sole RW case mount.
                # Absence cannot qualify ANY of the required read-only mounts.
                and mount.get("ReadOnly", False) is (not writable)
            )
        require(seen == set(expected))

    def _metadata(self, deadline):
        self._guard(deadline)
        container = self.docker.container(self.init.container_id)
        require(type(container) is dict and container.get("Id") == self.init.container_id)
        name = "sdsctl-recording-handoff-" + self.plan.case
        require(
            plans.ordinary.generation(container, name=name, image=self.plan.helper.image)
            == self.generation
        )
        require(container["State"]["Pid"] == self.init.pid)
        plans.ordinary.manual_container(container)
        require(
            container.get("Path") == self.command[0]
            and container.get("Args") == list(self.command[1:])
        )
        config, host = container.get("Config"), container.get("HostConfig")
        require(type(config) is dict and type(host) is dict)
        require(config.get("User") == "0:0" and config.get("WorkingDir") == "/")
        require(config.get("Hostname") == self.hostname)
        require(config.get("Tty") is False and config.get("OpenStdin") is False)
        require(config.get("Volumes") in (None, {}))
        parts = []
        for key in ("Entrypoint", "Cmd"):
            value = config.get(key)
            require(value is None or type(value) is list)
            parts.extend(value or [])
        require(parts == list(self.command))
        require(
            runtime.helper_environment(
                config.get("Env"),
                image_environment_sha256=self.image_environment_sha256,
                timezone=self.timezone,
            )
            == self.plan.helper.environment
        )
        required = {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "AutoRemove": False,
            "NetworkMode": "none",
            "PidMode": "host",
            "CgroupnsMode": "host",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "Runtime": "runc",
            "PublishAllPorts": False,
            "NanoCpus": 1_000_000_000,
            "Memory": 512 * 1024 * 1024,
            "MemorySwap": 1024 * 1024 * 1024,
            "PidsLimit": 64,
            # HAOS/cgroup-v2 startup discards this unsupported option. The
            # launcher must pin that explicit expected startup value before
            # start, not copy the later observed setting into a new pin.
            "OomKillDisable": None,
        }
        require(
            all(
                type(host.get(key)) is type(value) and host[key] == value
                for key, value in required.items()
            )
        )
        require(host.get("CapDrop") == ["ALL"])
        added = host.get("CapAdd")
        require(
            type(added) is list
            and len(added) == 2
            and set(added) == {"CAP_DAC_READ_SEARCH", "CAP_SYS_PTRACE"}
        )
        security = host.get("SecurityOpt")
        require(security in (["no-new-privileges"], ["no-new-privileges", "label=disable"]))
        for key in (
            "Binds",
            "VolumesFrom",
            "Devices",
            "DeviceRequests",
            "DeviceCgroupRules",
            "GroupAdd",
            "ExtraHosts",
        ):
            require(host.get(key) in (None, []))
        for key in ("Tmpfs", "Sysctls", "PortBindings", "StorageOpt"):
            require(host.get(key) in (None, {}))
        self._mounts(container)
        # This exact complete configuration pin is independently provided. It
        # also covers unknown Engine settings: no observation becomes a default.
        stamp = base.checksum(
            {
                "schema": 2,
                "kind": "finite-recording-helper-configuration-v2",
                "config": config | {"Env": self.plan.helper.environment},
                "host": host,
                # Engine's observed mount list is unordered and can change
                # ordering at startup. _mounts already requires unique exact
                # targets; preserve EVERY field, not merely selected values.
                "mounts": sorted(container["Mounts"], key=lambda item: item["Destination"]),
            }
        )
        require(stamp == self.configuration_sha256)
        driver = container.get("GraphDriver")
        require(type(driver) is dict and driver.get("Name") == "overlay2")
        data = driver.get("Data")
        require(type(data) is dict and data.get("ID") == self.init.container_id)
        merged = data.get("MergedDir")
        plans.text(merged, r"/mnt/data/docker/overlay2/[a-z0-9]{1,128}/merged")
        self._guard(deadline)
        image = self.docker.image(self.plan.helper.image)
        require(image.get("Id") == self.plan.helper.image and image.get("Os") == "linux")
        require(
            image.get("Architecture") == self.architecture and type(image.get("Config")) is dict
        )
        require(runtime.environment(image["Config"].get("Env")) == self.image_environment_sha256)
        self._guard(deadline)
        return Path(merged), base.checksum(driver), config["Env"]

    def _environment(self, configured, deadline):
        self._guard(deadline)
        result = runtime.collect_helper_process_environment(
            self.witness,
            deadline=min(deadline, time.monotonic() + 1),
            configured=configured,
            configured_sha256=self.plan.helper.environment,
            image_environment_sha256=self.image_environment_sha256,
            timezone=self.timezone,
            hostname=self.hostname,
        )
        require(type(result) is runtime.ProcessEnvironment and result.process == self.init)
        self._guard(deadline)
        return result

    def _command(self, deadline):
        self._guard(deadline)
        with open(f"/proc/{self.init.pid}/cmdline", "rb", buffering=0) as stream:
            raw = stream.read(8193)
        require(len(raw) <= 8192)
        require(raw == b"\0".join(part.encode("ascii") for part in self.command) + b"\0")
        self._guard(deadline)

    def _kernel(self, deadline):
        self._guard(deadline)
        result = runtime.collect_helper_kernel(
            self.witness, deadline=min(deadline, time.monotonic() + 1)
        )
        require(type(result) is runtime.HelperKernel and result.process == self.init)
        base.digest(result.sha256)
        self._guard(deadline)
        return result

    @contextmanager
    def _proc_binding(self, deadline):
        """Retain the observed helper's proc mount through the source bracket.

        Call only inside the original root/mount-namespace binding. The outer
        launcher's proc/PID/cgroup/user view must already be trusted. No pidfd
        reacquisition, namespace entry, mount operation or process discovery.
        """
        self._guard(deadline)
        path = f"/proc/{self.init.pid}/root/proc"
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            # proc root link counts change when unrelated processes start/exit.
            # Retain stable object/security metadata, not volatile tree size.
            def identity(info):
                return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid

            initial = identity(os.fstat(fd))
            require(stat.S_ISDIR(os.fstat(fd).st_mode))

            def read(selected, limit):
                self._guard(deadline)
                with open(selected, "rb", buffering=0) as stream:
                    raw = stream.read(limit + 1)
                require(len(raw) <= limit and time.monotonic() < deadline)
                return raw

            def sample():
                self._guard(deadline)
                require(identity(os.fstat(fd)) == initial)
                require(identity(os.stat(path, follow_symlinks=False)) == initial)
                info = read(f"/proc/{os.getpid()}/fdinfo/{fd}", 4096).decode("ascii")
                ids = re.findall(r"^mnt_id:\s*([0-9]+)$", info, flags=re.MULTILINE)
                inodes = re.findall(r"^ino:\s*([0-9]+)$", info, flags=re.MULTILINE)
                require(len(ids) == len(inodes) == 1 and int(inodes[0]) == os.fstat(fd).st_ino)
                metadata = os.fstat(fd)
                stamp = helper_proc_mount_profile(
                    read(f"/proc/{self.init.pid}/mountinfo", 1024 * 1024),
                    mount_id=int(ids[0]),
                    device=(os.major(metadata.st_dev), os.minor(metadata.st_dev)),
                )
                require(os.readlink(path + "/self") == str(os.getpid()))
                for name in ("pid", "cgroup", "user"):
                    own = os.stat(f"/proc/{os.getpid()}/ns/{name}")
                    other = os.stat(f"/proc/{self.init.pid}/ns/{name}")
                    require((own.st_dev, own.st_ino) == (other.st_dev, other.st_ino))
                observed_identity = engine.dispatch.process.process_identity(
                    self.init.pid,
                    self.init.container_id,
                    read(path + f"/{self.init.pid}/stat", 4096).decode("ascii"),
                    read(path + f"/{self.init.pid}/cgroup", 4096).decode("ascii"),
                )
                require(observed_identity == self.init)
                self._guard(deadline)
                return stamp

            original = sample()

            def check():
                require(sample() == original)

            yield check
            check()
        finally:
            os.close(fd)

    @contextmanager
    def _root_binding(self, root, deadline):
        """Retain the original process root and mount namespace across hashing.

        Engine's merged path must be the SAME directory as /proc/original/root.
        Holding the mount namespace prevents inode recycling while this check
        is active. It does not authenticate proc-mount provenance or inventory
        every mount inside that namespace. No setns, chroot, signal or execution.
        """
        opened = []
        try:
            self._guard(deadline)
            proc_root = f"/proc/{self.init.pid}/root"
            namespace = f"/proc/{self.init.pid}/ns/mnt"
            for path, flags in (
                (root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW),
                # These two fixed proc magic links must be followed; arbitrary
                # caller paths are never used as their destinations.
                (proc_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC),
                (namespace, os.O_RDONLY | os.O_CLOEXEC),
            ):
                self._guard(deadline)
                fd = os.open(path, flags)
                opened.append((fd, path, None))
                info = os.fstat(fd)
                require(info.st_uid == runtime.ROOT_UID)
                require(
                    stat.S_ISDIR(info.st_mode) if path != namespace else stat.S_ISREG(info.st_mode)
                )
                opened[-1] = (fd, path, runtime.identity(info))
            require(opened[0][2] == opened[1][2])

            def check():
                self._guard(deadline)
                for fd, path, expected in opened:
                    require(runtime.identity(os.fstat(fd)) == expected)
                    require(
                        runtime.identity(os.stat(path, follow_symlinks=path != root)) == expected
                    )
                require(opened[0][2] == opened[1][2])
                self._guard(deadline)

            check()
            yield check
            check()
        finally:
            failed_close = False
            for fd, *_ in reversed(opened):
                try:
                    os.close(fd)
                except Exception:
                    failed_close = True
            require(not failed_close)

    def __call__(self):
        """No cached report or action authority; every successful call is fresh."""
        acquired = False
        self.elapsed_seconds = None
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            deadline = min(began + self.MAX_SECONDS, self.plan.lease["ready_by"])
            self._guard(deadline)
            root, driver, configured = self._metadata(deadline)
            with (
                self._root_binding(root, deadline) as check_root,
                self._proc_binding(deadline) as check_proc,
            ):
                kernel_before = self._kernel(deadline)
                self._command(deadline)
                before = self._environment(configured, deadline)
                source = helper_source.Layout(root / plans.fixed.PACKAGE, root / self.HELPER)
                check_root()
                source.verify(self.plan.helper.source)
                check_root()
                check_proc()
                runtime.Layout(root, workers=self.runtime_workers).verify_supervised(
                    self.plan.helper.interpreter, self.timezone
                )
                check_root()
                source.verify(self.plan.helper.source)
                check_root()
                check_proc()
                after = self._environment(configured, deadline)
                kernel_after = self._kernel(deadline)
                require(
                    (kernel_before.sha256, kernel_before.process)
                    == (kernel_after.sha256, kernel_after.process)
                )
                require(began <= kernel_before.observed_at <= kernel_after.observed_at < deadline)
                self._command(deadline)
                require((before.sha256, before.process) == (after.sha256, after.process))
                require(began <= before.observed_at <= after.observed_at < deadline)
                end_root, end_driver, _ = self._metadata(deadline)
                require((root, driver) == (end_root, end_driver))
            self._guard(deadline)
            self.elapsed_seconds = time.monotonic() - began
            require(0 <= self.elapsed_seconds < self.MAX_SECONDS)
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed, self.elapsed_seconds = True, None
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None


class CandidateQualification:
    """Fresh source/runtime/environment join for one retained original idle init.

    Read-only, uninstalled adapter segment. Independently reviewed image, source,
    runtime and environment pins are inputs, never learned from this observation.
    The environment pin is explicitly the supervised Config.Env profile, not the
    legacy five-key image profile. No credentials are retained in this object.

    Full host files/jobs/Core/network/other owners, helper qualification, native
    cached health and independent recovery remain separate obligations. A call
    returns None only after fresh checks; elapsed_seconds is diagnostic, never
    reusable authority. Failure permanently consumes this collector. It never
    closes caller-owned handles. Kernel/Engine stalls still require independent
    outer supervision; elapsed checks cannot interrupt blocked kernel I/O.

    A trusted assembly may explicitly select one or two runtime workers before
    construction. The original choice is retained across all checks; it never
    changes on timeout or discovers resources itself. Helper CPU/confinement
    qualification is separate, and no file or deadline changes with this choice.
    """

    CODE_ROOTS = plans.host.candidate_static.CODE_ROOTS + tuple(
        Path(name)
        for name in (
            "/etc/ssl",
            "/etc/ld.so.conf",
            "/etc/ld.so.cache",
            "/etc/ld.so.conf.d",
            "/etc/ld.so.preload",
            "/etc/localtime",
            "/etc/timezone",
        )
    )
    MAX_SECONDS = 2.0
    EVIDENCE = idle_module.Evidence

    def __init__(
        self,
        plan,
        idle,
        witness,
        docker,
        *,
        image_environment_sha256,
        timezone,
        hostname,
        architecture,
        runtime_workers=1,
    ):
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.failed, self.elapsed_seconds = False, None
        try:
            require(type(runtime_workers) is int and runtime_workers in (1, 2))
            self.plan_pin = plans.PinnedPlan(plan)
            require(type(idle) is idle_module.Idle and idle.plan == plan)
            require(type(witness) is engine.dispatch.process.ProcessWitness)
            require(idle.init == witness.identity)
            require(type(docker) is plans.ordinary.Docker and docker.path == "/var/run/docker.sock")
            base.digest(idle.generation)
            base.digest(image_environment_sha256)
            runtime._timezone_name(timezone)
            plans.text(hostname, r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}")
            require(architecture in ("amd64", "arm64"))
            self.plan, self.idle, self.witness, self.docker = plan, idle, witness, docker
            self.image_environment_sha256 = image_environment_sha256
            self.timezone, self.hostname, self.architecture = timezone, hostname, architecture
            self.runtime_workers = runtime_workers
            self.init, self.generation = witness.identity, idle.generation
            self.fd = witness.fd
            self.fd_identity = runtime.identity(os.fstat(self.fd))
            self.original = self._pins()
            self.layout = next(item for item in plan.layouts if item.slug == base.CANDIDATE)
            self._guard(min(time.monotonic() + self.MAX_SECONDS, self._bounds()[1]))
        except BaseException as error:
            self._fail(error)

    def _pins(self):
        return (
            self.plan.raw,
            self.init,
            self.generation,
            self.image_environment_sha256,
            self.timezone,
            self.hostname,
            self.architecture,
            (type(self.runtime_workers), self.runtime_workers),
        )

    def _bounds(self):
        return self.plan.deadlines.ready_by, self.plan.lease["ready_by"]

    def _evidence(self):
        return self.idle.read()

    @staticmethod
    def _original(evidence):
        return evidence

    def _guard(self, deadline):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        require(os.geteuid() == runtime.ROOT_UID and time.monotonic() < deadline)
        require(self._pins() == self.original)
        self.plan_pin.check(self.plan)
        require(
            type(self.docker) is plans.ordinary.Docker
            and self.docker.path == "/var/run/docker.sock"
        )
        require(type(self.idle) is idle_module.Idle and self.idle.plan == self.plan)
        require(type(self.witness) is engine.dispatch.process.ProcessWitness)
        require(self.idle.init == self.witness.identity == self.init)
        require(self.idle.generation == self.generation and self.witness.fd == self.fd)
        require(runtime.identity(os.fstat(self.fd)) == self.fd_identity)
        require(not self.witness.exited())
        require(
            engine.dispatch.process.read_identity(self.init.pid, self.init.container_id)
            == self.init
        )
        require(
            self.layout == next(item for item in self.plan.layouts if item.slug == base.CANDIDATE)
        )
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        policy_end, native_end = self._bounds()
        require(observed.boottime_ns / plans.clock.NS < policy_end)
        require(time.monotonic() < min(deadline, native_end))

    def _metadata(self, deadline):
        self._guard(deadline)
        plan = self.plan
        container = self.docker.container("app_" + base.CANDIDATE)
        require(container.get("Id") == self.init.container_id)
        require(
            plans.ordinary.generation(
                container, name="app_" + base.CANDIDATE, image=plan.candidate_runtime.image
            )
            == self.generation
        )
        require(container["State"]["Pid"] == self.init.pid)
        plans.ordinary.manual_container(container)
        require(
            container.get("Path") == plan.idle_argv[0]
            and container.get("Args") == list(plan.idle_argv[1:])
        )
        config = container.get("Config")
        require(type(config) is dict and config.get("User") in ("0", "0:0"))
        require(config.get("Hostname") == self.hostname and config.get("WorkingDir") in ("", "/"))
        command = []
        for key in ("Entrypoint", "Cmd"):
            parts = config.get(key)
            require(parts is None or type(parts) is list)
            command.extend(parts or [])
        require(command == list(plan.idle_argv))
        require(
            runtime.supervised_environment(
                config.get("Env"),
                image_environment_sha256=self.image_environment_sha256,
                timezone=self.timezone,
            )
            == plan.candidate_runtime.environment
        )
        merged = plans.fixed._merged_root(self.layout, container, code_roots=self.CODE_ROOTS)
        require(merged is not None)
        self._guard(deadline)
        image = self.docker.image(plan.candidate_runtime.image)
        require(image.get("Id") == plan.candidate_runtime.image and image.get("Os") == "linux")
        require(
            image.get("Architecture") == self.architecture and type(image.get("Config")) is dict
        )
        require(runtime.environment(image["Config"].get("Env")) == self.image_environment_sha256)
        # Compare all configuration/mount inputs, not changing counters or health.
        # Tokens are represented only by the separately tagged one-way digest.
        stamp = base.checksum(
            {
                "config": config | {"Env": plan.candidate_runtime.environment},
                "host": container["HostConfig"],
                "mounts": container["Mounts"],
                "driver": container["GraphDriver"],
                "generation": self.generation,
            }
        )
        self._guard(deadline)
        return merged, stamp, config["Env"]

    def __call__(self):
        """Complete fresh qualification without a contributing observation."""
        return self._collect(None)

    def during(self, observe):
        """Return one trusted read-only observation only after fresh qualification.

        Full source and runtime inventories surround the callback, which may
        read cached status/host state but must not authorize or perform writes.
        Both original process-environment reads, metadata and idle evidence also
        bracket it. Nothing is cached for a later call; failures poison this
        instance. The entire collection keeps the same two-second maximum.
        """
        try:
            require(callable(observe))
        except BaseException as error:
            self._fail(error)
        return self._collect(observe)

    def _environment(self, configured, deadline):
        self._guard(deadline)
        evidence = runtime.collect_supervised_process_environment(
            self.witness,
            deadline=min(deadline, time.monotonic() + 1),
            configured=configured,
            configured_sha256=self.plan.candidate_runtime.environment,
            image_environment_sha256=self.image_environment_sha256,
            timezone=self.timezone,
            hostname=self.hostname,
            fixed_exec=False,
        )
        require(type(evidence) is runtime.ProcessEnvironment and evidence.process == self.init)
        self._guard(deadline)
        return evidence

    def _collect(self, observe):
        acquired = False
        self.elapsed_seconds = None
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            deadline = min(began + self.MAX_SECONDS, self._bounds()[1])
            self._guard(deadline)
            before = self._evidence()
            require(type(before) is self.EVIDENCE)
            original = self._original(before)
            require(type(original) is idle_module.Evidence and original.init == self.init)
            require(
                (original.plan_sha256, original.generation, original.lease_sha256)
                == (self.plan.sha256, self.generation, self.plan.lease_sha256)
            )
            merged, stamp, configured = self._metadata(deadline)
            native = plans.host.candidate_static
            source = native.source.Layout(merged / plans.fixed.PACKAGE, merged / native.NATIVE)
            result = None
            if observe is None:
                source.verify(self.plan.candidate_runtime.source)
                self._guard(deadline)
                runtime.Layout(merged, workers=self.runtime_workers).verify_supervised(
                    self.plan.candidate_runtime.interpreter, self.timezone
                )
            else:
                environment_before = self._environment(configured, deadline)

                def guarded_observation():
                    self._guard(deadline)
                    value = observe()
                    self._guard(deadline)
                    return value

                result = source.verify_during(
                    self.plan.candidate_runtime.source,
                    lambda: runtime.Layout(
                        merged, workers=self.runtime_workers
                    ).verify_supervised_during(
                        self.plan.candidate_runtime.interpreter,
                        self.timezone,
                        guarded_observation,
                        deadline=deadline,
                    ),
                    deadline=deadline,
                )
            evidence = self._environment(configured, deadline)
            require(began <= evidence.observed_at < deadline)
            if observe is not None:
                require(
                    (evidence.sha256, evidence.process)
                    == (environment_before.sha256, environment_before.process)
                )
                require(began <= environment_before.observed_at <= evidence.observed_at)
            after_root, after_stamp, _ = self._metadata(deadline)
            require((after_root, after_stamp) == (merged, stamp))
            after = self._evidence()
            require(type(after) is self.EVIDENCE)
            # Evidence timestamps must advance; immutable process/claim/files
            # and original clock domain must not change beneath that freshness.
            require(replace(after, sampled_at=before.sampled_at) == before)
            require(after.sampled_at >= before.sampled_at)
            self._guard(deadline)
            self.elapsed_seconds = time.monotonic() - began
            require(0 <= self.elapsed_seconds < self.MAX_SECONDS)
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed, self.elapsed_seconds = True, None
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None


class RetainedQualification(CandidateQualification):
    """Same fresh source/runtime bracket, only after an actual retained begin.

    Uses PostBegin's ORIGINAL init/lease/actor continuity and already fixed stop
    bounds, never an expired Idle.read or renewed readiness. Source, environment,
    mounts, image and original process reads are identical to the bootstrap
    qualifier; nothing is cached from a previous successful call. A worker exit
    during collection refuses that sample. No health, recording completion,
    independent host recovery or permission to begin is produced by this class.
    Launch/Start continue requiring the exact pre-begin qualification type.
    """

    EVIDENCE = idle_module.Continuity

    def __init__(self, continuity, witness, docker, **profile):
        self.failed, self.elapsed_seconds = False, None
        try:
            require(type(continuity) is idle_module.PostBegin)
            require(not continuity.closed and not continuity.failed)
            self.continuity = continuity
            self.continuity_objects = (
                continuity,
                continuity.plan,
                continuity.idle,
                continuity.guard,
                continuity.ready,
            )
            self.continuity_finish = continuity.finish_by
            super().__init__(continuity.plan, continuity.idle, witness, docker, **profile)
        except BaseException as error:
            self._fail(error)

    def _bounds(self):
        continued = self.continuity
        require(type(continued) is idle_module.PostBegin)
        require(continued.plan is self.plan and continued.idle is self.idle)
        require(
            all(
                a is b
                for a, b in zip(
                    (continued, self.plan, self.idle, continued.guard, continued.ready),
                    self.continuity_objects,
                    strict=True,
                )
            )
        )
        require(continued.finish_by == self.continuity_finish == self.plan.lease["stop_by"])
        continued._guard(min(time.monotonic() + self.MAX_SECONDS, self.continuity_finish))
        return self.plan.deadlines.stop_by, self.continuity_finish

    def _evidence(self):
        return self.continuity.read()

    @staticmethod
    def _original(evidence):
        return evidence.original


class Launch:
    """One returned durable authorization, fixed exec, actual original Ready.

    Requires the already prepared journal3 in candidate_idle, with original init
    and CLI receipts. Directories must be precreated BEFORE idle PID1 starts.
    Reopening a consumed journal cannot recreate this launch. Source/protection
    qualification surrounds create, attach and Ready; full host observations
    bracket the durable intent. No recording authorization/begin, web listener,
    App command, signal, native success, process exit or restoration is implied.

    On failure, retain any acquired Ready actor handles for independent recovery.
    close() releases those handles explicitly, never certifies their exit. The
    original idle/witness/journal remain caller-owned. The Engine Client takes
    endpoint ownership when constructed, as in its existing contract.
    """

    def __init__(
        self,
        plan,
        projected,
        journal,
        idle,
        witness,
        endpoint,
        *,
        launch_sha256,
        profile_sha256,
        read,
        qualify,
    ):
        self.owner = os.getpid(), get_ident()
        self.used = self.failed = self.closed = False
        self.confirm_attempted = False
        self.probe = None
        self.action = self.claim = self.client = self.ready = None
        try:
            require(type(plan) is plans.Plan and type(journal) is bootstrap.Journal)
            require(plans.load_bytes(plan.raw, plan.sha256) == plan)
            plan.check_projection(projected)
            require(type(idle) is idle_module.Idle and idle.plan == plan)
            require(type(witness) is engine.dispatch.process.ProcessWitness)
            require(idle.init == witness.identity and type(endpoint) is engine.Endpoint)
            require(callable(read) and callable(qualify))
            base.digest(launch_sha256)
            base.digest(profile_sha256)
            self.plan, self.projected, self.journal = plan, projected, journal
            self.idle, self.witness, self.endpoint = idle, witness, endpoint
            self.read, self.qualify = read, qualify
            self.launch_sha256 = launch_sha256
            self.profile_sha256 = profile_sha256
            self.command = execution.Command(
                str(plan.native_root / "launch/launch.json"),
                launch_sha256,
                plan.candidate_runtime.source,
                plan.lease["ready_by"],
            )
            self.command.argv()
            self.pins = engine.dispatch.Pins(
                binding.Binding(projected, plan.candidate_runtime.source, plan.sha256, plan.boot),
                self.command,
                idle.generation,
                witness.identity,
            )
            self.pins.payload()
            self._guard("candidate_idle")
        except BaseException as error:
            self._fail(error)

    def _history(self):
        """Recheck actual original journal bytes, not only a cached intent."""
        return self._history_until(min(time.monotonic() + 2, self.command.ready_by))

    def _history_until(self, end):
        """Read only; the caller must independently establish its lifecycle phase.

        Bootstrap always supplies its original ready bound above. An actual
        retained begin may instead supply the original stop bound, never a new
        readiness window. This routine itself grants no launch/begin permission.
        """
        return _verified_history(self.plan, self.projected, self.journal, end)

    def _guard(self, phase):
        require(not self.closed and not self.failed and self.owner == (os.getpid(), get_ident()))
        require(
            self.idle.plan == self.plan
            and self.idle.init == self.witness.identity == self.pins.init
        )
        require(not self.witness.exited())
        require(self.pins.generation == self.idle.generation)
        require(
            self.command
            == execution.Command(
                str(self.plan.native_root / "launch/launch.json"),
                self.launch_sha256,
                self.plan.candidate_runtime.source,
                self.plan.lease["ready_by"],
            )
        )
        require(self.pins.command == self.command and self.pins.host.projection == self.projected)
        require(self.pins.host.boot_id == self.plan.boot)
        require(self.pins.host.plan_sha256 == self.plan.sha256)
        require(self.pins.host.source_sha256 == self.plan.candidate_runtime.source)
        machine = self._history()
        state = machine.state
        require(state.phase == phase and state.candidate_generation == self.pins.generation)
        require(not state.finish_requested and state.operator_exit_sha256 is None)
        require(state.authorization_generation is None and state.ready_evidence_sha256 is None)
        require(machine.process_bound(base.CANDIDATE, exited=False))
        require(machine.execution_closed("starting_candidate"))
        record = next(item for item in state.processes if item.slug == base.CANDIDATE)
        require(
            (record.container_id, record.pid, record.start_ticks)
            == (self.pins.init.container_id, self.pins.init.pid, self.pins.init.start_ticks)
        )
        if phase == "candidate_idle":
            require(state.launch_intent_sha256 is None and self.action is None)
        else:
            require(type(self.action) is bootstrap.OperatorAction)
            require(state.launch_intent_sha256 == self.action.intent_sha256)
            require(state.launch_plan_sha256 == self.command.plan_sha256)
        observed = plans.clock.read()
        self.plan.check_clock(observed)
        now = observed.boottime_ns / plans.clock.NS
        require(machine.last_at <= now < min(state.deadline, self.plan.deadlines.ready_by))
        require(time.monotonic() < self.command.ready_by)
        self.endpoint.check()
        return machine, now

    def _qualified(self, phase):
        self._guard(phase)
        # The independent installed qualifier must raise on any uncertainty.
        # A boolean/serialized report is not an alternate authority interface.
        require(self.qualify() is None)
        self._guard(phase)
        evidence = self.idle.read()
        require(type(evidence) is idle_module.Evidence)
        require(
            (evidence.plan_sha256, evidence.generation, evidence.init, evidence.lease_sha256)
            == (self.plan.sha256, self.pins.generation, self.pins.init, self.plan.lease_sha256)
        )
        self._guard(phase)
        return evidence

    def _sample(self, phase):
        evidence = self._qualified(phase)
        sample, machine, now = self._read_sample(phase)
        return evidence, sample, machine, now

    def _read_sample(self, phase):
        """Fresh full host read; qualification belongs to the enclosing path."""
        self._guard(phase)
        sample = self.read()
        require(type(sample) is bootstrap.recovery.Sample)
        machine, now = self._guard(phase)
        require(sample.boot_id == self.plan.boot and sample.now <= now)
        machine.fresh(now, sample.observation)
        # This is the truthful idle PID1 collection, not healthy/recording-idle.
        candidate = sample.observation.candidate
        require(candidate.healthy is None and candidate.recording is None)
        return sample, machine, now

    def start(self):
        """Consume a fresh policy intent before any fixed Engine write, once."""
        return self._start(combined=False)

    def start_confirmed(self):
        """Single-use combined launch/Ready collection; no recording begin.

        Requires the actual original CandidateQualification, not an arbitrary
        boolean/report or previous successful qualification. The passive probe
        is prepared immediately after Ready. Its one current status read and a
        fresh full host observation are bracketed by complete new source/runtime
        inventories. The original Ready timestamp and two-second journal bound
        remain unchanged. An actual bound BootstrapHost may prepare its fixed
        read-only I/O concurrently with those inventories, then join on this
        original thread. The separate start()/confirm_ready() path is unchanged.
        """
        return self._start(combined=True)

    def _candidate_qualifier(self):
        qualifier = self.qualify
        require(type(qualifier) is CandidateQualification)
        require(
            qualifier.plan is self.plan
            and qualifier.idle is self.idle
            and qualifier.witness is self.witness
        )
        require(qualifier.generation == self.pins.generation and qualifier.init == self.pins.init)
        if type(self.read) is BootstrapHost:
            require(
                self.read.plan is self.plan
                and self.read.idle is self.idle
                and self.read.witness is self.witness
                and self.read.projected == self.projected
            )
        return qualifier

    def _start(self, *, combined):
        try:
            require(not self.used)
            self.used = True
            if combined:
                self._candidate_qualifier()
            evidence, sample, machine, now = self._sample("candidate_idle")
            event = dict(
                kind="authorize_operator",
                boot_id=self.plan.boot,
                now=now,
                generation=self.pins.generation,
                bootstrap_sha256=self.plan.bootstrap.sha256,
                launch_plan_sha256=self.command.plan_sha256,
                idle_evidence_sha256=evidence.sha256,
                observation=asdict(sample.observation),
            )
            self.action = self.journal.append(event)
            require(type(self.action) is bootstrap.OperatorAction)
            require(self.action.intent_sha256 == base.checksum(event))
            _, fresh, machine, second = self._sample("starting_operator")
            require(0 <= second - now <= 2)
            require(machine.preconditions(fresh.observation) == self.action.preconditions_sha256)
            self.claim = engine.dispatch.Claim(
                self.plan.root / "operator-exec", self.pins, self.witness
            )
            self.client = engine.Client(self.endpoint, self.claim)
            self._qualified("starting_operator")
            self.client.create()
            self._qualified("starting_operator")
            self.client.attach(finish_by=self.plan.lease["stop_by"])
            self.ready = received.Ready(
                self.client,
                profile_sha256=self.profile_sha256,
                original_clock=self.plan.original_clock,
                zero_domain=self.idle.zero_domain,
            )
            if combined:
                return self._confirm_ready(combined=True)
            self._qualified("starting_operator")
            self.ready.check_before_begin()
            return self.ready
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
        collector = getattr(self, "read", None)
        if type(collector) is BootstrapHost and collector.owner == (os.getpid(), get_ident()):
            collector.discard()
        if self.ready is not None:
            self.ready.failed = True
        if self.client is not None:
            self.client.close()
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedHostLaunch(MESSAGE) from None

    def confirm_ready(self):
        """Join one actual passive probe and fresh host state to journal3 Ready.

        The original received timestamp is converted conservatively using the
        ORIGINAL clock interval; no later successful check refreshes its age.
        Import/Engine/probe/host delays must all fit the unchanged policy window.
        Failure consumes this attempt and closes the original transport, retaining
        probe/actor pidfds for independent reconciliation. This is not recording
        authorization and never starts the recorder or a browser listener.
        """
        return self._confirm_ready(combined=False)

    def _confirm_ready(self, *, combined):
        try:
            require(self.used and self.ready is not None and not self.confirm_attempted)
            self.confirm_attempted = True
            self.ready.check_before_begin()
            if combined:
                qualifier = self._candidate_qualifier()
                _, began = self._guard("starting_operator")
                self.probe = probe_exec.Sample(self.ready)
                require(self.probe.prepare() is None)
                if type(self.read) is BootstrapHost:
                    require(self.read.prepare() is None)

                def observe():
                    self.ready.check_before_begin()
                    sample, _, _ = self._read_sample("starting_operator")
                    native = self.probe.read()
                    self._check_native(native)
                    self.ready.check_before_begin()
                    return native, sample

                native, sample = qualifier.during(observe)
                require(self._candidate_qualifier() is qualifier)
            else:
                self._qualified("starting_operator")
                _, began = self._guard("starting_operator")
                self.probe = probe_exec.Sample(self.ready)
                native = self.probe.read()
                self._check_native(native)
                _, sample, _, _ = self._sample("starting_operator")
            self.ready.check_before_begin()
            machine, now = self._guard("starting_operator")
            # Preserve the oldest contributing observation, not the timestamp
            # of the most recent successful recheck.
            observation = replace(
                sample.observation,
                sampled_at=min(began, sample.observation.sampled_at),
                candidate=replace(
                    sample.observation.candidate, healthy=native.healthy, recording=native.recording
                ),
            )
            machine.fresh(now, observation)
            lower_ns = (
                Decimal(self.ready.received_at) * plans.clock.NS
                + self.plan.original_clock.offset[0]
            )
            received_at = math.nextafter(float(lower_ns / plans.clock.NS), -math.inf)
            proof = base.checksum(
                dict(
                    ready_sha256=hashlib.sha256(self.ready.ready_raw).hexdigest(),
                    probe_execution_id=self.probe.execution_id,
                    probe_request_sha256=self.probe.request_sha256,
                    native=asdict(native),
                    observation=asdict(observation),
                )
            )
            self.journal.append(
                dict(
                    kind="operator_ready",
                    boot_id=self.plan.boot,
                    now=now,
                    generation=self.pins.generation,
                    intent_sha256=self.action.intent_sha256,
                    ready_evidence_sha256=proof,
                    received_at=received_at,
                    observation=asdict(observation),
                )
            )
            machine = self._history()
            require(machine.state.phase == "candidate_running")
            require(machine.state.ready_evidence_sha256 == proof)
            require(machine.state.authorization_generation is None)
            self.plan.check_clock(plans.clock.read())
            self.ready.check_before_begin()
            return native
        except BaseException as error:
            self._fail(error)

    def _check_native(self, native):
        require(type(native) is plans.ordinary.NativeState)
        require(native.generation == self.pins.generation)
        require(native.healthy is True and native.recording is False)

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        collector = getattr(self, "read", None)
        if type(collector) is BootstrapHost:
            collector.discard()
        if self.probe is not None:
            self.probe.close()
        if self.ready is not None:
            self.ready.close()
        elif self.client is not None:
            self.client.close()


if __name__ == "__main__":
    raise SystemExit("Uninstalled host bootstrap join only; no live handoff enabled.")
