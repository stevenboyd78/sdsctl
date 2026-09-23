"""Offline durable recovery model; no App, scanner or host operations."""

import importlib.util
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_policy as old

NAME = "supplemental_recording_handoff"
SPEC = importlib.util.spec_from_file_location(NAME, Path(old.SCRIPT).with_name(NAME + ".py"))
h = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = h
SPEC.loader.exec_module(h)
b = old.policy
CONTRACT = h.Contract(old.CASE, "1" * 64, "2" * 64, "3" * 64, "4" * 64)
PRISTINE = h.Files(CONTRACT.sha256, "pristine", "5" * 64)
ACTIVE = replace(old.CANDIDATE_RUNNING, recording=True)


def files(stage, proof="6" * 64, generation=old.CANDIDATE_RUNNING.generation):
    if stage == "pristine":
        return PRISTINE
    if stage == "unknown":
        return h.Files(CONTRACT.sha256, stage)
    return h.Files(CONTRACT.sha256, stage, proof, generation)


def observation(now=10, normal=old.NORMAL, candidate=old.CANDIDATE, evidence=PRISTINE, **changes):
    return replace(h.Observation(now, normal, candidate, True, True, True, evidence), **changes)


def prepare():
    return old.prepare() | {
        "kind": "prepare_recording",
        "observation": asdict(observation()),
        "contract": asdict(CONTRACT),
    }


def event(kind, now=11, **values):
    return {"kind": kind, "now": now, "boot_id": old.BOOT, **values}


def observe(
    journal,
    now,
    normal=old.NORMAL_STOPPED,
    candidate=old.CANDIDATE_RUNNING,
    evidence=PRISTINE,
    **changes,
):
    obs = observation(now, normal, candidate, evidence, **changes)
    return journal.append(event("observe", now, observation=asdict(obs)))


def bind(journal, slug, now):
    normal = slug == b.NORMAL
    record = b.ProcessRecord(
        slug,
        old.NORMAL.generation if normal else old.CANDIDATE_RUNNING.generation,
        ("8" if normal else "9") * 64,
        1234 if normal else 5678,
        100,
    )
    journal.append(event("bind_process", now, process=asdict(record)))


def exited(journal, slug, now):
    journal.append(
        event(
            "process_exited",
            now,
            generation=old.NORMAL.generation
            if slug == b.NORMAL
            else old.CANDIDATE_RUNNING.generation,
        )
    )


def execution(journal, now, *, complete=True, exit_code=0):
    value = b.checksum(journal.machine.state.phase)
    journal.append(event("bind_execution", now, container_id="a" * 64, execution_id=value))
    if complete:
        journal.append(event("execution_completed", now, execution_id=value, exit_code=exit_code))
    return value


def reach_candidate(journal):
    bind(journal, b.NORMAL, 10)
    journal.append(event("request", 11))
    assert observe(journal, 12, old.NORMAL, old.CANDIDATE).slug == b.NORMAL
    execution(journal, 12)
    exited(journal, b.NORMAL, 13)
    assert observe(journal, 13, candidate=old.CANDIDATE).slug == b.CANDIDATE
    execution(journal, 14)
    bind(journal, b.CANDIDATE, 15)
    assert observe(journal, 16) is None
    assert journal.machine.state.phase == "candidate_running"


def authorization(now=17, **changes):
    obs = observation(now, old.NORMAL_STOPPED, old.CANDIDATE_RUNNING, **changes)
    return event(
        "authorize_recording",
        now,
        generation=old.CANDIDATE_RUNNING.generation,
        contract_sha256=CONTRACT.sha256,
        observation=asdict(obs),
    )


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "recording-handoff"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def journal(directory):
    with h.Journal(directory) as journal:
        journal.append(prepare())
        yield journal


@pytest.fixture
def candidate(journal):
    reach_candidate(journal)
    return journal


@pytest.fixture
def authorized(candidate):
    assert candidate.append(authorization()) is None
    assert candidate.machine.state.recording_outcome == "unconfirmed"
    return candidate


def finish_restore(journal, now, evidence):
    execution(journal, now)
    assert (
        observe(journal, now + 1, normal=old.RESTORED, candidate=old.CANDIDATE, evidence=evidence)
        is None
    )
    assert journal.machine.state.phase == "complete"
    assert journal.machine.state.reason == "restored"


def test_success_proves_finalization_then_exact_exit_then_restoration(authorized, directory):
    j = authorized
    assert observe(j, 18, candidate=ACTIVE, evidence=files("active")) is None
    assert j.machine.state.phase == "candidate_running"
    assert observe(j, 19, candidate=ACTIVE, evidence=files("finalizing")) is None
    final = files("finalized")
    assert observe(j, 20, evidence=final) is None
    assert j.machine.state.recording_outcome == "unconfirmed"
    # Neither a good file nor stopped-looking Supervisor state proves init exit.
    assert observe(j, 21, candidate=old.CANDIDATE, evidence=final) is None
    exited(j, b.CANDIDATE, 22)
    action = observe(j, 22, candidate=old.CANDIDATE, evidence=final)
    assert (action.operation, action.slug) == ("start", b.NORMAL)
    assert j.machine.state.recording_outcome == "verified"
    finish_restore(j, 23, final)
    state = j.machine.state
    j.close()
    with h.Journal(directory) as reopened:
        assert reopened.machine.state == state
        with pytest.raises(b.UnsafeHandoff):
            reopened.append(authorization(25))


@pytest.mark.parametrize("stage", ["pristine", "retained", "finalized"])
def test_exact_exit_can_restore_without_promoting_failed_artifact(authorized, stage):
    j = authorized
    exited(j, b.CANDIDATE, 18)
    evidence = files(stage)
    assert observe(j, 19, candidate=old.CANDIDATE, evidence=evidence).slug == b.NORMAL
    expected = "verified" if stage == "finalized" else "unconfirmed"
    assert j.machine.state.recording_outcome == expected
    finish_restore(j, 20, evidence)
    assert j.machine.state.recording_outcome == expected


def test_no_authorization_no_recording_and_no_synthetic_success(candidate):
    exited(candidate, b.CANDIDATE, 18)
    assert observe(candidate, 19, candidate=old.CANDIDATE).slug == b.NORMAL
    finish_restore(candidate, 20, PRISTINE)
    assert candidate.machine.state.recording_outcome == "not_attempted"


@pytest.mark.parametrize("reason", ["deadline", "finish", "unhealthy"])
def test_active_recording_stays_truthful_during_bounded_stop(authorized, reason):
    j = authorized
    now, candidate = 18, ACTIVE
    if reason == "deadline":
        now = j.machine.state.recording_deadline
    elif reason == "finish":
        j.append(event("finish", now))
    else:
        candidate = replace(ACTIVE, healthy=False)
    action = observe(j, now, candidate=candidate, evidence=files("active"))
    assert (action.operation, action.slug) == ("stop", b.CANDIDATE)
    assert action.preconditions_sha256 == h.Machine.preconditions(
        observation(now, old.NORMAL_STOPPED, candidate, files("active"))
    )
    assert j.machine.state.recording_outcome == "unconfirmed"
    execution(j, now)
    assert observe(j, now + 1, candidate=old.CANDIDATE, evidence=files("finalizing")) is None
    exited(j, b.CANDIDATE, now + 2)
    assert observe(j, now + 2, candidate=old.CANDIDATE, evidence=files("retained")).slug == b.NORMAL
    finish_restore(j, now + 3, files("retained"))


@pytest.mark.parametrize("kind", ["tick", "observe"])
def test_unavailable_observations_do_not_extend_fixed_recording_recovery_deadline(authorized, kind):
    j = authorized
    limit = j.machine.state.recording_deadline + b.COMMAND_SECONDS
    assert observe(j, limit - 1, evidence=files("unknown")) is None
    if kind == "tick":
        j.append(event(kind, limit))
    else:
        observe(j, limit, evidence=files("unknown"))
    assert j.machine.state.phase == "review"
    assert j.machine.state.reason == "recording_recovery_deadline"


@pytest.mark.parametrize(
    "change",
    [
        "generation",
        "contract",
        "stale",
        "jobs",
        "core",
        "owners",
        "healthy",
        "active",
        "normal_pin",
        "candidate_pin",
        "normal_running",
        "files",
    ],
)
def test_authorization_requires_fresh_complete_matching_pristine_observation(candidate, change):
    value = authorization()
    obs = value["observation"]
    if change == "generation":
        value["generation"] = "f" * 64
    elif change == "contract":
        value["contract_sha256"] = "f" * 64
    elif change == "stale":
        obs["sampled_at"] -= 3
    elif change in ("jobs", "core", "owners"):
        obs[
            {"jobs": "jobs_idle", "core": "core_running", "owners": "other_owners_stopped"}[change]
        ] = False
    elif change in ("healthy", "active"):
        obs["candidate"]["healthy" if change == "healthy" else "recording"] = change == "active"
    elif change.endswith("_pin"):
        obs[change.split("_")[0]]["pin"] = "f" * 64
    elif change == "normal_running":
        obs["normal"] = asdict(old.NORMAL)
    else:
        obs["files"] = asdict(files("unknown"))
    before = candidate.machine.state
    with pytest.raises(b.UnsafeHandoff):
        candidate.append(value)
    assert candidate.machine.state == before


def test_authorization_is_consumed_and_cannot_be_reissued_after_restart(authorized, directory):
    deadline = authorized.machine.state.recording_deadline
    authorized.close()
    with h.Journal(directory) as j:
        assert j.machine.state.recording_deadline == deadline
        with pytest.raises(b.UnsafeHandoff):
            j.append(authorization(18))
        assert j.machine.state.recording_deadline == deadline


@pytest.mark.parametrize("which", ["normal", "candidate"])
def test_unplanned_recording_is_not_tolerated(candidate, which):
    changes = (
        {"normal": replace(old.NORMAL, recording=True)}
        if which == "normal"
        else {"candidate": ACTIVE, "evidence": files("active")}
    )
    assert observe(candidate, 17, **changes) is None
    assert candidate.machine.state.phase == "review"
    assert candidate.machine.state.reason in ("normal_recording_active", "recording_not_authorized")


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("contract", "recording_contract_changed"),
        ("generation", "recording_not_authorized"),
        ("pristine", "recording_evidence_disappeared"),
        ("regression", "recording_stage_regressed"),
        ("retained_live", "retained_writer_exit_unconfirmed"),
        ("finalized_active", "final_recording_evidence_changed"),
        ("finalized_changed", "final_recording_evidence_changed"),
    ],
)
def test_file_progress_is_case_bound_and_nonregressing(authorized, fault, reason):
    j = authorized
    observe(j, 18, candidate=ACTIVE, evidence=files("active"))
    evidence, candidate = files("active"), ACTIVE
    if fault == "contract":
        evidence = replace(evidence, contract_sha256="f" * 64)
    elif fault == "generation":
        evidence = replace(evidence, generation="f" * 64)
    elif fault == "pristine":
        evidence = PRISTINE
    elif fault == "regression":
        observe(j, 19, candidate=ACTIVE, evidence=files("finalizing"))
    elif fault == "retained_live":
        evidence = files("retained")
    elif fault == "finalized_active":
        evidence = files("finalized")
    else:
        observe(j, 19, evidence=files("finalized"))
        evidence, candidate = files("finalized", "f" * 64), old.CANDIDATE_RUNNING
    observe(j, 20, candidate=candidate, evidence=evidence)
    assert j.machine.state.reason == reason


@pytest.mark.parametrize("stage", ["retained", "finalized", "pristine"])
def test_preserved_evidence_is_not_changed_during_normal_restore(authorized, stage):
    j = authorized
    exited(j, b.CANDIDATE, 18)
    evidence = files(stage)
    observe(j, 19, candidate=old.CANDIDATE, evidence=evidence)
    execution(j, 20)
    observe(
        j,
        21,
        normal=old.RESTORED,
        candidate=old.CANDIDATE,
        evidence=replace(evidence, evidence_sha256="f" * 64),
    )
    assert j.machine.state.reason == (
        "pristine_recording_evidence_changed"
        if stage == "pristine"
        else "preserved_recording_changed"
    )


def test_stop_normal_needs_live_bound_original_process(journal):
    journal.append(event("request"))
    assert observe(journal, 12, old.NORMAL, old.CANDIDATE) is None
    bind(journal, b.NORMAL, 13)
    assert observe(journal, 14, old.NORMAL, old.CANDIDATE).operation == "stop"


def test_candidate_start_requires_both_normal_exit_and_cli_exit(journal):
    j = journal
    bind(j, b.NORMAL, 10)
    j.append(event("request"))
    observe(j, 12, old.NORMAL, old.CANDIDATE)
    eid = execution(j, 13, complete=False)
    assert observe(j, 14, candidate=old.CANDIDATE) is None
    exited(j, b.NORMAL, 15)
    assert observe(j, 16, candidate=old.CANDIDATE) is None
    j.append(event("execution_completed", 17, execution_id=eid, exit_code=1))
    # Nonzero CLI exit is reconciled from actual stopped state, not retried.
    assert observe(j, 18, candidate=old.CANDIDATE).slug == b.CANDIDATE


def test_candidate_stop_cli_exit_does_not_substitute_for_init_exit(authorized):
    j = authorized
    j.append(event("finish", 18))
    observe(j, 19, candidate=ACTIVE, evidence=files("active"))
    execution(j, 20)
    assert observe(j, 21, candidate=old.CANDIDATE, evidence=files("finalized")) is None
    exited(j, b.CANDIDATE, 22)
    assert observe(j, 23, candidate=old.CANDIDATE, evidence=files("finalized")).slug == b.NORMAL


@pytest.mark.parametrize("stage", ["authorized", "stopping_candidate", "starting_normal"])
def test_reopen_never_reissues_consumed_action(authorized, directory, stage):
    j = authorized
    now, candidate, evidence = 18, old.CANDIDATE_RUNNING, PRISTINE
    if stage != "authorized":
        j.append(event("finish", 18))
        observe(j, 19, candidate=ACTIVE, evidence=files("active"))
        now, candidate, evidence = 20, ACTIVE, files("active")
    if stage == "starting_normal":
        execution(j, 20)
        exited(j, b.CANDIDATE, 21)
        observe(j, 22, candidate=old.CANDIDATE, evidence=files("retained"))
        now, candidate, evidence = 23, old.CANDIDATE, files("retained")
    state = j.machine.state
    j.close()
    with h.Journal(directory) as reopened:
        assert reopened.machine.state == state
        assert observe(reopened, now, candidate=candidate, evidence=evidence) is None
        assert reopened.machine.state.phase == state.phase
        assert reopened.machine.state.recording_deadline == state.recording_deadline


@pytest.mark.parametrize("kind", ["observe", "authorize_recording", "tick"])
def test_boot_change_never_rearms(candidate, kind):
    value = authorization() if kind == "authorize_recording" else event(kind, 17)
    if kind == "observe":
        value["observation"] = asdict(observation(17, old.NORMAL_STOPPED, old.CANDIDATE_RUNNING))
    value["boot_id"] = old.OTHER_BOOT
    assert candidate.append(value) is None
    assert candidate.machine.state.reason == "host_boot_changed"


@pytest.mark.parametrize("value", [0, -1, 181, True, 1.5, "180"])
def test_recording_limit_is_strict_and_bounded(value):
    with pytest.raises(b.UnsafeHandoff):
        replace(CONTRACT, maximum_recording_seconds=value)


@pytest.mark.parametrize(
    "stage", ["bogus", "active", "finalized", "retained", "pristine", "unknown"]
)
def test_file_evidence_decoder_rejects_missing_or_inapplicable_fields(stage):
    with pytest.raises(b.UnsafeHandoff):
        h.Files(CONTRACT.sha256, stage, None if stage != "unknown" else "f" * 64)


def test_old_and_recording_journal_formats_are_mutually_rejected(journal, directory, tmp_path):
    journal.close()
    with pytest.raises(b.UnsafeHandoff):
        b.Journal(directory)
    path = tmp_path / "old"
    path.mkdir(mode=0o700)
    with b.Journal(path) as legacy:
        legacy.append(old.prepare())
    with pytest.raises(b.UnsafeHandoff):
        h.Journal(path)


def test_failed_publication_consumes_controller_and_preserves_evidence(
    candidate, monkeypatch, directory
):
    real = os.fsync

    def failed(fd):
        real(fd)
        if fd == candidate.fd:
            raise OSError("test only")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", failed)
        with pytest.raises(OSError):
            candidate.append(authorization())
    assert candidate.fd == -1
    with h.Journal(directory) as reopened:
        assert reopened.machine.state.authorization_generation == old.CANDIDATE_RUNNING.generation
        with pytest.raises(b.UnsafeHandoff):
            reopened.append(authorization(18))


def test_entry_schema_cannot_be_downgraded_to_reuse_legacy_policy(journal, directory):
    journal.close()
    target = directory / "0000.json"
    data = json.loads(target.read_text())
    data["schema"] = 1
    target.write_text(json.dumps(data))
    for reader in (b.Journal, h.Journal):
        with pytest.raises(b.UnsafeHandoff):
            reader(directory)


@pytest.mark.parametrize(
    "fault",
    ["normal_pin", "candidate_pin", "owners", "core", "candidate_generation", "normal_reappeared"],
)
@pytest.mark.parametrize("stage", ["active", "finalizing", "finalized"])
def test_authorized_recording_never_weakens_other_host_protections(authorized, fault, stage):
    candidate = ACTIVE if stage != "finalized" else old.CANDIDATE_RUNNING
    normal = old.NORMAL_STOPPED
    changes = {}
    if fault == "normal_pin":
        normal = replace(normal, pin="f" * 64)
    elif fault == "candidate_pin":
        candidate = replace(candidate, pin="f" * 64)
    elif fault in ("owners", "core"):
        changes["other_owners_stopped" if fault == "owners" else "core_running"] = False
    elif fault == "candidate_generation":
        candidate = replace(candidate, generation="f" * 64)
    else:
        normal = old.NORMAL
    assert observe(authorized, 18, normal, candidate, files(stage), **changes) is None
    assert authorized.machine.state.phase == "review"


@pytest.mark.parametrize("stage", ["active", "finalizing", "finalized", "retained"])
def test_unknown_files_never_grant_restoration(authorized, stage):
    j = authorized
    if stage == "retained":
        exited(j, b.CANDIDATE, 18)
        # Busy jobs prevent restoration while retaining the verified scope.
        observe(j, 19, candidate=old.CANDIDATE, evidence=files(stage), jobs_idle=False)
    else:
        observe(
            j,
            18,
            candidate=ACTIVE if stage != "finalized" else old.CANDIDATE_RUNNING,
            evidence=files(stage),
        )
        exited(j, b.CANDIDATE, 19)
    assert observe(j, 20, candidate=old.CANDIDATE, evidence=files("unknown")) is None
    assert j.machine.state.phase == "candidate_running"
    assert j.machine.state.recording_outcome == "unconfirmed"


def test_late_authorization_cannot_shift_original_candidate_deadline(candidate):
    deadline = candidate.machine.state.trial_deadline
    value = authorization(deadline - CONTRACT.maximum_recording_seconds)
    with pytest.raises(b.UnsafeHandoff):
        candidate.append(value)
    assert candidate.machine.state.trial_deadline == deadline
    assert candidate.machine.state.authorization_generation is None


def test_recovery_bound_does_not_fill_journal_with_audio_growth(authorized):
    j = authorized
    observe(j, 18, candidate=ACTIVE, evidence=files("active"))
    count = len(j.entries)
    for index in range(100):
        assert (
            observe(
                j, 19 + index / 100, candidate=ACTIVE, evidence=files("active", b.checksum(index))
            )
            is None
        )
    assert len(j.entries) == count
    assert j.machine.state.recording_deadline == 17 + CONTRACT.maximum_recording_seconds


def test_retained_artifact_is_pinned_even_while_restore_waits_for_jobs(authorized):
    j = authorized
    exited(j, b.CANDIDATE, 18)
    assert (
        observe(j, 19, candidate=old.CANDIDATE, evidence=files("retained"), jobs_idle=False) is None
    )
    assert observe(j, 20, candidate=old.CANDIDATE, evidence=files("retained", "f" * 64)) is None
    assert j.machine.state.reason == "preserved_recording_changed"
