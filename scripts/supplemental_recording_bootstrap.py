#!/usr/bin/env python3
"""Offline idle -> operator -> ready recording policy, NOT a host adapter.

Observations/digests are separately qualified inputs, not authentication here.
No container, Engine, scanner, recording or signal operations are performed.
This separate journal format cannot reinterpret older recording/idle journals.
All times here are the original host policy clock, never raw native MONOTONIC.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import supplemental_recording_handoff as recording
import supplemental_recording_recovery as recovery

base = recording.base
BOOTSTRAPPING = ("starting_candidate", "candidate_idle", "starting_operator")


@dataclass(frozen=True)
class Bootstrap:
    host_plan_sha256: str
    source_sha256: str
    idle_lease_sha256: str
    ready_by: float
    stop_by: float

    def __post_init__(self):
        for field in ("host_plan_sha256", "source_sha256", "idle_lease_sha256"):
            base.digest(getattr(self, field))
        base.clock(self.ready_by)
        base.clock(self.stop_by)
        base.require(self.ready_by < self.stop_by)

    @property
    def sha256(self):
        return base.checksum(asdict(self))


@dataclass(frozen=True)
class State(recording.State):
    launch_intent_sha256: str | None = None
    launch_plan_sha256: str | None = None
    launch_authorized_at: float = 0
    idle_evidence_sha256: str | None = None
    ready_evidence_sha256: str | None = None
    operator_exit_sha256: str | None = None


@dataclass(frozen=True)
class OperatorAction:
    """Returned only after journal fsync; replay never reissues this permission.

    A future adapter must requalify the same source/lease/init/plan and fresh
    preconditions, then use the distinct durable one-use Engine dispatch ledger.
    This is neither an exec-created result nor native-ready/recording permission.
    """

    case_id: str
    generation: str
    bootstrap_sha256: str
    launch_plan_sha256: str
    intent_sha256: str
    preconditions_sha256: str


class Machine(recording.Machine):
    def __init__(self, case_id, boot_id, now, baseline, contract, bootstrap):
        base.require(type(bootstrap) is Bootstrap)
        super().__init__(case_id, boot_id, now, baseline, contract)
        base.require(
            now < bootstrap.ready_by <= now + 600
            and bootstrap.ready_by + contract.maximum_recording_seconds + 3 <= bootstrap.stop_by
            and bootstrap.stop_by <= min(now + 780, self.hard_deadline)
        )
        self.bootstrap = bootstrap
        self.state = State(**asdict(self.state))

    def dispatch(self, phase, now, observation):
        action = super().dispatch(phase, now, observation)
        if phase == "starting_candidate":
            self.state = replace(
                self.state,
                deadline=min(self.state.deadline, self.bootstrap.ready_by),
                trial_deadline=min(self.state.trial_deadline, self.bootstrap.stop_by),
            )
        return action

    def _launch_observation(self, event, *, idle):
        obs = recording.decode_observation(event["observation"])
        self.fresh(event["now"], obs)
        state = self.state
        base.require(
            obs.normal.state == "stopped"
            and obs.normal.pin == self.baseline.normal.pin
            and obs.candidate.pin == self.baseline.candidate.pin
            and obs.candidate.state == "running"
            and obs.candidate.generation == state.candidate_generation == event["generation"]
            and self.process_bound(base.CANDIDATE, exited=False)
            and self.execution_closed("starting_candidate")
            and obs.jobs_idle
            and obs.core_running
            and obs.other_owners_stopped
            and obs.files == self.baseline.files
        )
        if idle:
            # PID-1 deliberately runs no daemon: neither bool is synthesized.
            base.require(obs.candidate.healthy is None and obs.candidate.recording is None)
        else:
            base.require(obs.candidate.healthy is True and obs.candidate.recording is False)
        return obs

    def event(self, event):
        kind = event.get("kind")
        if kind == "authorize_recording":
            base.clock(event.get("now"))
            base.require(
                self.state.ready_evidence_sha256 is not None
                and self.state.operator_exit_sha256 is None
                and event["now"] < min(self.state.deadline, self.bootstrap.ready_by)
            )
            return super().event(event)
        fields = {
            "authorize_operator": {
                "generation",
                "bootstrap_sha256",
                "launch_plan_sha256",
                "idle_evidence_sha256",
                "observation",
            },
            "operator_ready": {
                "generation",
                "intent_sha256",
                "ready_evidence_sha256",
                "received_at",
                "observation",
            },
            "operator_exited": {"generation", "intent_sha256", "exit_evidence_sha256"},
        }
        custom_finish = kind == "finish" and self.state.phase in BOOTSTRAPPING
        if kind not in fields and not custom_finish:
            return super().event(event)
        base.require(set(event) == {"kind", "now", "boot_id"} | fields.get(kind, set()))
        # Reuse the original boot, monotonic order, phase and total deadlines.
        # No bootstrap event creates a fresh phase, trial or lease deadline.
        super().event({key: event[key] for key in ("now", "boot_id")} | {"kind": "tick"})
        if self.state.phase == "review":
            return None
        state, now = self.state, event["now"]
        if custom_finish:
            base.require(not state.finish_requested)
            self.state = replace(state, finish_requested=True)
            return None
        base.digest(event["generation"])
        base.require(event["generation"] == state.candidate_generation)
        if kind == "authorize_operator":
            base.require(
                state.phase == "candidate_idle"
                and state.launch_intent_sha256 is None
                and not state.finish_requested
                and event["bootstrap_sha256"] == self.bootstrap.sha256
                and now < min(state.deadline, self.bootstrap.ready_by)
            )
            base.digest(event["launch_plan_sha256"])
            base.digest(event["idle_evidence_sha256"])
            observation = self._launch_observation(event, idle=True)
            intent = base.checksum(event)
            self.state = replace(
                state,
                phase="starting_operator",
                launch_intent_sha256=intent,
                launch_plan_sha256=event["launch_plan_sha256"],
                launch_authorized_at=now,
                idle_evidence_sha256=event["idle_evidence_sha256"],
            )
            return OperatorAction(
                self.case_id,
                state.candidate_generation,
                self.bootstrap.sha256,
                event["launch_plan_sha256"],
                intent,
                self.preconditions(observation),
            )
        base.require(
            state.launch_intent_sha256 is not None
            and event["intent_sha256"] == state.launch_intent_sha256
        )
        if kind == "operator_ready":
            base.digest(event["ready_evidence_sha256"])
            base.clock(event["received_at"])
            observation = self._launch_observation(event, idle=False)
            base.require(
                state.phase == "starting_operator"
                and state.ready_evidence_sha256 is None
                and state.operator_exit_sha256 is None
                and not state.finish_requested
                and state.launch_authorized_at <= event["received_at"] <= observation.sampled_at
                and 0 <= now - event["received_at"] <= 2
                and now < min(state.deadline, self.bootstrap.ready_by)
            )
            self.state = replace(
                state,
                phase="candidate_running",
                ready_evidence_sha256=event["ready_evidence_sha256"],
            )
        else:
            base.digest(event["exit_evidence_sha256"])
            base.require(
                state.phase in ("starting_operator", "candidate_running", "stopping_candidate")
                and state.operator_exit_sha256 is None
            )
            # Input must cover independent retained worker exits and exact exec
            # reconciliation, including failed/lost-return cases. No file result
            # or init/CLI exit can synthesize it. Qualification is outside policy.
            self.state = replace(
                state, operator_exit_sha256=event["exit_evidence_sha256"], finish_requested=True
            )
        return None

    def restore(self, now, obs):
        if self.state.launch_intent_sha256 is not None and self.state.operator_exit_sha256 is None:
            return None
        return super().restore(now, obs)

    def observe(self, now, obs):
        if self.state.phase not in BOOTSTRAPPING:
            return super().observe(now, obs)
        state, candidate = self.state, obs.candidate
        if (
            obs.normal.pin != self.baseline.normal.pin
            or candidate.pin != self.baseline.candidate.pin
        ):
            self.review("protected_identity_changed")
        elif not obs.other_owners_stopped:
            self.review("other_owner_not_confirmed_stopped")
        elif not obs.core_running:
            self.review("core_not_confirmed_running")
        elif now >= min(state.deadline, self.bootstrap.ready_by):
            self.review("phase_deadline")
        elif obs.normal.recording is True or candidate.recording is True:
            self.review("recording_not_authorized")
        elif obs.normal.state == "unknown" or candidate.state == "unknown":
            return None
        elif obs.normal.state != "stopped":
            self.review("normal_started_externally")
        elif (
            state.candidate_generation is not None
            and candidate.state == "running"
            and candidate.generation != state.candidate_generation
        ):
            self.review("candidate_generation_changed")
        elif not self.files_valid(obs) or not obs.jobs_idle:
            return None
        elif candidate.state == "stopped":
            if state.candidate_generation is not None:
                return self.restore(now, obs)
        elif state.phase in ("starting_candidate", "candidate_idle") and (
            candidate.healthy is not None or candidate.recording is not None
        ):
            self.review("idle_candidate_not_idle")
        elif state.phase == "starting_candidate":
            if state.candidate_generation is None:
                self.state = replace(state, candidate_generation=candidate.generation)
            if self.process_bound(base.CANDIDATE, exited=False) and self.execution_closed(
                "starting_candidate"
            ):
                self.state = replace(self.state, phase="candidate_idle")
        elif state.finish_requested and self.process_bound(base.CANDIDATE, exited=False):
            return self.stop_candidate(now, obs)
        # A healthy-looking observation cannot replace the actual Ready event.
        return None


class Journal(base.Journal):
    """New offline format, not installed host-plan schema 3 or launch permission."""

    schema = 3
    max_events = 64

    def apply(self, event):
        base.require(type(event) is dict)
        if self.machine is None:
            base.require(
                set(event)
                == {"kind", "case_id", "boot_id", "now", "observation", "contract", "bootstrap"}
                and event["kind"] == "prepare_bootstrap"
                and type(event["contract"]) is dict
                and set(event["contract"]) == set(recording.Contract.__dataclass_fields__)
                and type(event["bootstrap"]) is dict
                and set(event["bootstrap"]) == set(Bootstrap.__dataclass_fields__)
            )
            self.machine = Machine(
                event["case_id"],
                event["boot_id"],
                event["now"],
                recording.decode_observation(event["observation"]),
                recording.Contract(**event["contract"]),
                Bootstrap(**event["bootstrap"]),
            )
            return None
        return super().apply(event)


class Executor(recovery.dispatch.Executor):
    """Exact journal3 bridge; never reinterpret the older recording journal.

    Only fixed App start/stop actions from fresh, durable policy intents reach
    the supplied qualified dispatch. Polling never authorizes an operator or
    recording begin. Those separate events and authentication remain required.
    """

    sample_type = recovery.Sample

    def __init__(self, journal, read, send):
        base.require(type(journal) is Journal and type(journal.machine) is Machine)
        super().__init__(journal, read, send)


class RecoverySession(recovery.BaseRecoverySession):
    """Original init/CLI exit reconciliation with the explicit bootstrap policy.

    Retains the inherited independent tick, one-use dispatch and exact process
    tracking. No observer, actual Ready, operator-exit receipt, source check or
    automatic launch permission is invented by constructing this bridge.
    """

    sample_type = recovery.Sample
    executor_type = Executor


if __name__ == "__main__":
    raise SystemExit("Offline bootstrap policy only; no recording-capable host plan is enabled.")
