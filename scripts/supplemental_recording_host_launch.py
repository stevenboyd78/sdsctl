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
import time
from dataclasses import asdict, replace
from decimal import Decimal
from threading import get_ident

import supplemental_recording_idle_observer as idle_module
import supplemental_recording_probe_exec as probe_exec
import supplemental_recording_ready as received

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
        journal, plan = self.journal, self.plan
        require(type(journal) is bootstrap.Journal and journal.path == plan.root / "journal")
        journal.check_directory()
        require(0 < len(journal.entries) <= journal.max_events)
        require(
            sorted(os.listdir(journal.fd)) == [journal.name(i) for i in range(len(journal.entries))]
        )
        end = min(time.monotonic() + 2, self.command.ready_by)
        for index, entry in enumerate(journal.entries):
            name = journal.name(index)
            info = os.stat(name, dir_fd=journal.fd, follow_symlinks=False)
            require(info.st_uid == os.geteuid() and info.st_mode & 0o7777 == 0o600)
            raw = binding.protected.evidence.read_bytes(
                journal.fd, name, limit=base.MAX_BYTES, deadline=end
            )
            require(raw == base.encode(entry))
            require(
                binding.identity(os.stat(name, dir_fd=journal.fd, follow_symlinks=False))
                == binding.identity(info)
            )
        journal.check_directory()
        require(time.monotonic() < end)
        machine = journal.machine
        require(type(machine) is bootstrap.Machine)
        require(
            base.encode(journal.entries[0]["event"])
            == base.encode(plan.preparation(machine.baseline, self.projected))
        )
        require((machine.case_id, machine.boot_id) == (plan.case, plan.boot))
        require(machine.created_at == plan.deadlines.issued_at)
        require(machine.hard_deadline == plan.deadlines.recover_by)
        require(machine.bootstrap == plan.bootstrap and machine.contract == plan.candidate.contract)
        return machine

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
        sample = self.read()
        require(type(sample) is bootstrap.recovery.Sample)
        machine, now = self._guard(phase)
        require(sample.boot_id == self.plan.boot and sample.now <= now)
        machine.fresh(now, sample.observation)
        # This is the truthful idle PID1 collection, not healthy/recording-idle.
        candidate = sample.observation.candidate
        require(candidate.healthy is None and candidate.recording is None)
        return evidence, sample, machine, now

    def start(self):
        """Consume a fresh policy intent before any fixed Engine write, once."""
        try:
            require(not self.used)
            self.used = True
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
            self._qualified("starting_operator")
            self.ready.check_before_begin()
            return self.ready
        except BaseException as error:
            self._fail(error)

    def _fail(self, error):
        self.failed = True
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
        try:
            require(self.used and self.ready is not None and not self.confirm_attempted)
            self.confirm_attempted = True
            self.ready.check_before_begin()
            self._qualified("starting_operator")
            _, began = self._guard("starting_operator")
            self.probe = probe_exec.Sample(self.ready)
            native = self.probe.read()
            require(type(native) is plans.ordinary.NativeState)
            require(native.generation == self.pins.generation)
            require(native.healthy is True and native.recording is False)
            _, sample, machine, now = self._sample("starting_operator")
            self.ready.check_before_begin()
            _, now = self._guard("starting_operator")
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

    def close(self):
        require(self.owner == (os.getpid(), get_ident()))
        if self.closed:
            return
        self.closed = True
        if self.probe is not None:
            self.probe.close()
        if self.ready is not None:
            self.ready.close()
        elif self.client is not None:
            self.client.close()


if __name__ == "__main__":
    raise SystemExit("Uninstalled host bootstrap join only; no live handoff enabled.")
