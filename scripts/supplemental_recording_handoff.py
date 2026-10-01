#!/usr/bin/env python3
"""Offline recording-aware recovery policy, NOT an installed host adapter.

Qualified observations are inputs, not independently authenticated by this pure
policy. No recording start, scanner request, filesystem collection or process
signal is implemented here. Old recording-idle journals cannot open this format.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

import supplemental_handoff_policy as base


@dataclass(frozen=True)
class Contract:
    case_id: str
    baseline_sha256: str
    root_sha256: str
    audio_endpoint_sha256: str
    writer_sha256: str
    maximum_recording_seconds: int = 180

    def __post_init__(self) -> None:
        base.identifier(self.case_id, case=True)
        for key in ("baseline_sha256", "root_sha256", "audio_endpoint_sha256", "writer_sha256"):
            base.digest(getattr(self, key))
        base.require(
            type(self.maximum_recording_seconds) is int
            and 0 < self.maximum_recording_seconds <= 180
        )

    @property
    def sha256(self) -> str:
        return base.checksum(asdict(self))


@dataclass(frozen=True)
class Files:
    """Result of a separately qualified complete recording-root observation.

    Pristine means the entire original inventory still matches. Other stages
    require unchanged old files plus only the exact case/start-bound new names.
    Retained means a bounded, scoped failed/partial artifact, not ignored files.
    Finalized additionally requires verified contents AND timely native success
    acknowledgment; a valid WAV or a stopped receipt alone is insufficient.
    Unknown grants no action. Hashes bind retained evidence; they do not prove it.
    """

    contract_sha256: str
    stage: str
    evidence_sha256: str | None = None
    generation: str | None = None

    def __post_init__(self) -> None:
        base.digest(self.contract_sha256)
        base.require(
            self.stage in ("pristine", "active", "finalizing", "finalized", "retained", "unknown")
        )
        if self.stage == "unknown":
            base.require(self.evidence_sha256 is None and self.generation is None)
        else:
            base.digest(self.evidence_sha256)
            if self.stage == "pristine":
                base.require(self.generation is None)
            else:
                base.digest(self.generation)


@dataclass(frozen=True)
class Observation(base.Observation):
    files: Files

    def __post_init__(self) -> None:
        super().__post_init__()
        base.require(type(self.files) is Files)


@dataclass(frozen=True)
class State(base.State):
    authorization_generation: str | None = None
    recording_deadline: float = 0
    files_stage: str = "pristine"
    artifact_sha256: str | None = None
    preserved_sha256: str | None = None
    recording_outcome: str = "not_attempted"


def decode_observation(value: Any) -> Observation:
    base.require(type(value) is dict and set(value) == set(Observation.__dataclass_fields__))
    try:
        ordinary = base.decode_observation({k: v for k, v in value.items() if k != "files"})
        return Observation(
            **{k: getattr(ordinary, k) for k in base.Observation.__dataclass_fields__},
            files=Files(**value["files"]),
        )
    except (TypeError, KeyError):
        raise base.UnsafeHandoff("Invalid recording handoff observation.") from None


class Machine(base.Machine):
    """Finite one-recording policy; native recording and host exit stay separate."""

    def __init__(
        self, case_id: str, boot_id: str, now: float, baseline: Observation, contract: Contract
    ):
        base.require(type(contract) is Contract and contract.case_id == case_id)
        super().__init__(case_id, boot_id, now, baseline)
        base.require(
            baseline.files.contract_sha256 == contract.sha256 and baseline.files.stage == "pristine"
        )
        self.contract = contract
        self.state = State(**asdict(self.state))

    @staticmethod
    def fresh(now: float, observation: Observation) -> None:
        base.clock(now)
        base.require(type(observation) is Observation and 0 <= now - observation.sampled_at <= 2)

    decode_observation = staticmethod(decode_observation)

    @staticmethod
    def preconditions(observation: Observation) -> str:
        values = asdict(observation)
        del values["sampled_at"]
        if observation.files.stage in ("active", "finalizing"):
            # A second fully qualified monitor sample must still verify every
            # old file and the same case/writer. Its bounded growing-WAV proof
            # need not be byte-identical; requiring that could withhold the
            # one shutdown intent merely because audio continued arriving.
            del values["files"]["evidence_sha256"]
        return base.checksum(values)

    def process_bound(self, slug: str, *, exited: bool) -> bool:
        record = next((p for p in self.state.processes if p.slug == slug), None)
        return record is not None and (record.generation in self.state.exited_processes) is exited

    def execution_closed(self, phase: str) -> bool:
        execution = next((e for p, _, e in self.state.executions if p == phase), None)
        # A CLI process exit is not proof of App success. Fresh state and init
        # pidfd receipts are required separately, even if the exit code is zero.
        return execution is not None and any(
            e == execution for e, _ in self.state.completed_executions
        )

    def event(self, event: dict[str, Any]) -> base.Action | None:
        if event.get("kind") != "authorize_recording":
            action = super().event(event)
            if (
                event["kind"] == "tick"
                and self.state.phase == "candidate_running"
                and self.state.recording_deadline
                and event["now"] >= self.state.recording_deadline + base.COMMAND_SECONDS
            ):
                self.review("recording_recovery_deadline")
            return action
        base.require(
            set(event) == {"kind", "now", "boot_id", "generation", "contract_sha256", "observation"}
        )
        base.digest(event["generation"])
        base.require(event["contract_sha256"] == self.contract.sha256)
        obs = decode_observation(event["observation"])
        self.fresh(event["now"], obs)
        # Reuse the original boot, monotonic-clock and deadline guards. This
        # does not turn a tick into an action or reset any existing deadline.
        super().event({k: event[k] for k in ("now", "boot_id")} | {"kind": "tick"})
        if self.state.phase == "review":
            return None
        now = event["now"]
        base.require(
            self.state.phase == "candidate_running"
            and self.state.authorization_generation is None
            and not self.state.finish_requested
            and self.state.files_stage == "pristine"
            and self.state.candidate_generation == event["generation"]
            and self.process_bound(base.CANDIDATE, exited=False)
            and now + self.contract.maximum_recording_seconds < self.state.trial_deadline
            and obs.normal.state == "stopped"
            and obs.normal.pin == self.baseline.normal.pin
            and obs.candidate.pin == self.baseline.candidate.pin
            and obs.candidate.state == "running"
            and obs.candidate.generation == event["generation"]
            and obs.candidate.healthy is True
            and obs.candidate.recording is False
            and obs.jobs_idle
            and obs.other_owners_stopped
            and obs.core_running
            and obs.files == self.baseline.files
        )
        self.state = replace(
            self.state,
            authorization_generation=event["generation"],
            recording_deadline=now + self.contract.maximum_recording_seconds,
            recording_outcome="unconfirmed",
        )
        # This is durable permission, NOT a start acknowledgment. A future
        # qualified operator adapter must dispatch its separate one-use trigger.
        return None

    def stop_candidate(self, now: float, obs: Observation) -> base.Action:
        if self.state.authorization_generation is not None and obs.files.stage != "finalized":
            self.state = replace(self.state, recording_outcome="unconfirmed")
        return self.dispatch("stopping_candidate", now, obs)

    def restore(self, now: float, obs: Observation) -> base.Action | None:
        if not self.process_bound(base.CANDIDATE, exited=True):
            return None
        if not self.execution_closed("starting_candidate"):
            return None
        if self.state.phase == "stopping_candidate" and not self.execution_closed(
            "stopping_candidate"
        ):
            return None
        if obs.files.stage not in ("pristine", "retained", "finalized"):
            return None
        outcome = "not_attempted"
        if self.state.authorization_generation is not None:
            outcome = "verified" if obs.files.stage == "finalized" else "unconfirmed"
        self.state = replace(
            self.state, recording_outcome=outcome, preserved_sha256=obs.files.evidence_sha256
        )
        return self.dispatch("starting_normal", now, obs)

    def files_valid(self, obs: Observation) -> bool:
        files, state = obs.files, self.state
        if files.contract_sha256 != self.contract.sha256:
            self.review("recording_contract_changed")
            return False
        if files.stage == "unknown":
            return False
        if files.stage != "pristine" and (
            state.authorization_generation is None
            or files.generation != state.authorization_generation
        ):
            self.review("recording_not_authorized")
        elif files.stage == "pristine" and state.files_stage != "pristine":
            self.review("recording_evidence_disappeared")
        elif files.stage == "pristine" and files != self.baseline.files:
            self.review("pristine_recording_evidence_changed")
        elif (
            files.stage == "active" and state.files_stage in ("finalizing", "finalized", "retained")
        ) or (state.files_stage in ("finalized", "retained") and files.stage != state.files_stage):
            self.review("recording_stage_regressed")
        elif state.preserved_sha256 is not None and files.evidence_sha256 != state.preserved_sha256:
            self.review("preserved_recording_changed")
        elif files.stage == "retained" and not (
            obs.candidate.state == "stopped" and self.process_bound(base.CANDIDATE, exited=True)
        ):
            self.review("retained_writer_exit_unconfirmed")
        elif files.stage == "finalized" and (
            obs.candidate.recording is True
            or state.artifact_sha256 not in (None, files.evidence_sha256)
        ):
            self.review("final_recording_evidence_changed")
        elif obs.candidate.recording is True and files.stage not in ("active", "finalizing"):
            self.review("recording_state_inconsistent")
        else:
            self.state = replace(
                state,
                files_stage=files.stage,
                artifact_sha256=(
                    files.evidence_sha256 if files.stage == "finalized" else state.artifact_sha256
                ),
                preserved_sha256=(
                    files.evidence_sha256 if files.stage == "retained" else state.preserved_sha256
                ),
            )
            return True
        return False

    def observe(self, now: float, obs: Observation) -> base.Action | None:
        state = self.state
        normal, candidate = obs.normal, obs.candidate
        if normal.pin != self.baseline.normal.pin or candidate.pin != self.baseline.candidate.pin:
            self.review("protected_identity_changed")
        elif not obs.other_owners_stopped:
            self.review("other_owner_not_confirmed_stopped")
        elif not obs.core_running:
            self.review("core_not_confirmed_running")
        elif state.phase != "candidate_running" and now >= state.deadline:
            self.review("phase_deadline")
        elif (
            state.phase == "candidate_running"
            and now >= state.trial_deadline + base.COMMAND_SECONDS
        ):
            self.review("recovery_observation_deadline")
        elif (
            state.phase == "candidate_running"
            and state.recording_deadline
            and now >= state.recording_deadline + base.COMMAND_SECONDS
        ):
            self.review("recording_recovery_deadline")
        elif normal.recording is True:
            self.review("normal_recording_active")
        elif candidate.recording is True and state.authorization_generation != candidate.generation:
            self.review("recording_not_authorized")
        elif normal.state == "unknown" or candidate.state == "unknown":
            return None
        elif state.phase in ("prepared", "requested", "stopping_normal") and (
            normal.state == "running" and normal.generation != self.baseline.normal.generation
        ):
            self.review("normal_generation_changed")
        elif (
            state.phase in ("prepared", "requested", "stopping_normal")
            and candidate.state != "stopped"
        ):
            self.review("candidate_started_externally")
        elif (
            state.phase in ("starting_candidate", "candidate_running", "stopping_candidate")
            and normal.state != "stopped"
        ):
            self.review("normal_started_externally")
        elif state.phase == "starting_normal" and candidate.state != "stopped":
            self.review("candidate_reappeared")
        elif (
            state.candidate_generation is not None
            and candidate.state == "running"
            and candidate.generation != state.candidate_generation
        ):
            self.review("candidate_generation_changed")
        elif (
            state.restored_generation is not None
            and normal.state == "running"
            and normal.generation != state.restored_generation
        ):
            self.review("restored_generation_changed")
        elif not self.files_valid(obs) or not obs.jobs_idle:
            return None
        elif state.phase == "prepared":
            if normal.state != "running":
                self.review("normal_stopped_externally")
        elif state.phase == "requested":
            if normal.state != "running":
                self.review("normal_stopped_externally")
            elif (
                normal.healthy is True
                and normal.recording is False
                and self.process_bound(base.NORMAL, exited=False)
            ):
                return self.dispatch("stopping_normal", now, obs)
        elif state.phase == "stopping_normal":
            if (
                normal.state == "stopped"
                and self.process_bound(base.NORMAL, exited=True)
                and self.execution_closed("stopping_normal")
            ):
                return self.dispatch("starting_candidate", now, obs)
        elif state.phase == "starting_candidate":
            if candidate.state == "running":
                if state.candidate_generation is None:
                    self.state = replace(self.state, candidate_generation=candidate.generation)
                if self.process_bound(base.CANDIDATE, exited=False) and self.execution_closed(
                    "starting_candidate"
                ):
                    if candidate.healthy is False and candidate.recording is False:
                        return self.stop_candidate(now, obs)
                    if candidate.healthy is True and candidate.recording is False:
                        self.state = replace(self.state, phase="candidate_running")
            elif state.candidate_generation is not None:
                return self.restore(now, obs)
        elif state.phase == "candidate_running":
            if candidate.state == "stopped":
                return self.restore(now, obs)
            if (
                self.process_bound(base.CANDIDATE, exited=False)
                and (
                    state.finish_requested
                    or now >= state.trial_deadline
                    or candidate.healthy is False
                    or (
                        state.authorization_generation is not None
                        and now >= state.recording_deadline
                    )
                )
                and (state.authorization_generation is not None or candidate.recording is False)
            ):
                # A bounded authorized recording may need native shutdown to
                # finalize. This never claims stop acknowledgment or OS exit.
                return self.stop_candidate(now, obs)
        elif state.phase == "stopping_candidate":
            if candidate.state == "stopped":
                return self.restore(now, obs)
        elif state.phase == "starting_normal" and normal.state == "running":
            if normal.generation == self.baseline.normal.generation:
                self.review("old_normal_generation_reappeared")
            else:
                if state.restored_generation is None:
                    self.state = replace(self.state, restored_generation=normal.generation)
                if (
                    normal.healthy is True
                    and normal.recording is False
                    and self.execution_closed("starting_normal")
                ):
                    self.state = replace(self.state, phase="complete", reason="restored")
        return None


class Journal(base.Journal):
    """Separate format; reuses exclusive hash-chain/fsync/no-replay machinery.

    Schema 2 here describes journal entries, NOT installed host-plan schema 2.
    No existing live host-plan version authorizes this recording policy.
    """

    schema = 2
    max_events = 48

    def apply(self, event: dict[str, Any]) -> base.Action | None:
        base.require(type(event) is dict)
        if self.machine is None:
            base.require(
                set(event) == {"kind", "case_id", "boot_id", "now", "observation", "contract"}
                and event["kind"] == "prepare_recording"
                and type(event["contract"]) is dict
                and set(event["contract"]) == set(Contract.__dataclass_fields__)
            )
            self.machine = Machine(
                event["case_id"],
                event["boot_id"],
                event["now"],
                decode_observation(event["observation"]),
                Contract(**event["contract"]),
            )
            return None
        return super().apply(event)


if __name__ == "__main__":
    raise SystemExit("Offline recording policy only; no live host plan is enabled.")
