"""Offline recovery faults; fake observations never contact Home Assistant."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "supplemental_handoff_policy.py"
if "supplemental_handoff_policy" not in sys.modules:
    SPEC = importlib.util.spec_from_file_location("supplemental_handoff_policy", SCRIPT)
    policy = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = policy
    SPEC.loader.exec_module(policy)
policy = sys.modules["supplemental_handoff_policy"]

CASE = "4def7cd52b514600a80db8fe022e0e2f"
BOOT = "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"
OTHER_BOOT = "bbbbbbbbbbbb4bbb8bbbbbbbbbbbbbbb"
NORMAL = policy.App("a" * 64, "running", "b" * 64, True, False)
CANDIDATE = policy.App("c" * 64, "stopped")
NORMAL_STOPPED = replace(NORMAL, state="stopped", generation=None, healthy=None, recording=None)
CANDIDATE_RUNNING = replace(
    CANDIDATE, state="running", generation="d" * 64, healthy=True, recording=False
)
RESTORED = replace(NORMAL, generation="e" * 64)


def observation(now=10, normal=NORMAL, candidate=CANDIDATE, **kwargs):
    return policy.Observation(now, normal, candidate, True, True, True, **kwargs)


def prepare():
    return {
        "kind": "prepare",
        "case_id": CASE,
        "boot_id": BOOT,
        "now": 10,
        "observation": asdict(observation()),
    }


def event(kind, now=11, boot=BOOT, obs=None):
    result = {"kind": kind, "now": now, "boot_id": boot}
    if obs is not None:
        result["observation"] = asdict(obs)
    return result


def observe(journal, now=12, normal=NORMAL, candidate=CANDIDATE, **changes):
    obs = replace(observation(now, normal, candidate), **changes)
    return journal.append(event("observe", now, obs=obs))


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "handoff"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def journal(directory):
    with policy.Journal(directory) as result:
        result.append(prepare())
        yield result


def reach_candidate(journal, *, healthy=True):
    journal.append(event("request"))
    assert observe(journal).slug == policy.NORMAL
    assert observe(journal, 13, NORMAL_STOPPED).slug == policy.CANDIDATE
    action = observe(journal, 14, NORMAL_STOPPED, replace(CANDIDATE_RUNNING, healthy=healthy))
    if healthy is False:
        assert (action.operation, action.slug) == ("stop", policy.CANDIDATE)
    else:
        assert action is None
    return journal.machine.state


def reach_restore(journal):
    reach_candidate(journal)
    journal.append(event("finish", 15))
    assert observe(journal, 16, NORMAL_STOPPED, CANDIDATE_RUNNING).operation == "stop"
    assert observe(journal, 17, NORMAL_STOPPED).operation == "start"


def test_complete_handoff_actions_are_fixed_order_and_durable(directory, journal):
    journal.append(event("request"))
    actions = [observe(journal), observe(journal, 13, NORMAL_STOPPED)]
    observe(journal, 14, NORMAL_STOPPED, CANDIDATE_RUNNING)
    deadline = journal.machine.state.trial_deadline
    assert deadline == 13 + policy.TRIAL_SECONDS
    assert observe(journal, deadline - 1, NORMAL_STOPPED, CANDIDATE_RUNNING) is None
    actions += [
        observe(journal, deadline, NORMAL_STOPPED, CANDIDATE_RUNNING),
        observe(journal, deadline + 1, NORMAL_STOPPED),
    ]
    assert observe(journal, deadline + 2, RESTORED) is None
    assert journal.machine.state.phase == "complete"
    assert [(a.operation, a.slug) for a in actions] == [
        ("stop", policy.NORMAL),
        ("start", policy.CANDIDATE),
        ("stop", policy.CANDIDATE),
        ("start", policy.NORMAL),
    ]
    for action in actions:
        assert action.case_id == CASE and len(action.preconditions_sha256) == 64
    journal.close()
    with policy.Journal(directory) as recovered:
        assert recovered.machine.state.phase == "complete"
        with pytest.raises(policy.UnsafeHandoff):
            recovered.append(event("request", deadline + 3))


@pytest.mark.parametrize(
    "stage", ["stopping_normal", "starting_candidate", "stopping_candidate", "starting_normal"]
)
def test_restart_after_each_intent_never_replays_mutation(directory, journal, stage):
    journal.append(event("request"))
    observe(journal)
    normal, candidate, now = NORMAL, CANDIDATE, 12
    if stage != "stopping_normal":
        normal, now = NORMAL_STOPPED, 13
        observe(journal, now, normal, candidate)
    if stage in ("stopping_candidate", "starting_normal"):
        candidate, now = CANDIDATE_RUNNING, 14
        observe(journal, now, normal, candidate)
        journal.append(event("finish", 15))
        now = 16
        observe(journal, now, normal, candidate)
    if stage == "starting_normal":
        candidate, now = CANDIDATE, 17
        observe(journal, now, normal, candidate)
    assert journal.machine.state.phase == stage
    deadline = journal.machine.state.deadline
    journal.close()
    with policy.Journal(directory) as recovered:
        for time in (now + 1, deadline - 1):
            assert observe(recovered, time, normal, candidate) is None
            assert recovered.machine.state.phase == stage
            assert recovered.machine.state.deadline == deadline
        assert observe(recovered, deadline, normal, candidate) is None
        assert recovered.machine.state.phase == "review"
        assert recovered.machine.state.reason == "phase_deadline"


@pytest.mark.parametrize("stage", ["prepared", "requested", "candidate_running", "starting_normal"])
def test_new_host_boot_requires_review_not_rearm(journal, stage):
    if stage == "requested":
        journal.append(event("request"))
    elif stage == "candidate_running":
        reach_candidate(journal)
    elif stage == "starting_normal":
        reach_restore(journal)
    assert journal.append(event("observe", 20, OTHER_BOOT, observation(20))) is None
    assert journal.machine.state.reason == "host_boot_changed"


def test_no_request_never_stops_normal_or_starts_candidate(journal):
    for now in (12, 20, 50, 309):
        assert observe(journal, now) is None
    assert len(journal.entries) == 1
    assert observe(journal, 310) is None
    assert journal.machine.state.phase == "review"


def test_candidate_window_persists_across_restart_and_no_operator_arm(directory, journal):
    reach_candidate(journal)
    deadline = journal.machine.state.trial_deadline
    journal.close()
    with policy.Journal(directory) as recovered:
        assert observe(recovered, deadline - 1, NORMAL_STOPPED, CANDIDATE_RUNNING) is None
        action = observe(recovered, deadline, NORMAL_STOPPED, CANDIDATE_RUNNING)
        assert action.operation == "stop" and action.slug == policy.CANDIDATE
        assert recovered.machine.state.trial_deadline == deadline


@pytest.mark.parametrize("healthy", [True, False, None])
def test_observed_candidate_crash_can_restore_only_after_confirmed_exit(journal, healthy):
    reach_candidate(journal, healthy=healthy)
    assert observe(journal, 15, NORMAL_STOPPED, replace(CANDIDATE, state="unknown")) is None
    assert observe(journal, 16, NORMAL_STOPPED, jobs_idle=False) is None
    action = observe(journal, 17, NORMAL_STOPPED)
    assert action.operation == "start" and action.slug == policy.NORMAL
    observe(journal, 18, RESTORED)
    assert journal.machine.state.phase == "complete"


def test_candidate_start_lost_before_first_observation_does_not_assume_exit(journal):
    journal.append(event("request"))
    observe(journal)
    observe(journal, 13, NORMAL_STOPPED)
    assert observe(journal, 132, NORMAL_STOPPED) is None
    assert observe(journal, 133, NORMAL_STOPPED) is None
    assert journal.machine.state.phase == "review"


def test_healthy_container_is_not_healthy_daemon_and_generation_is_bound(journal):
    reach_restore(journal)
    observe(journal, 18, replace(RESTORED, healthy=None))
    assert journal.machine.state.phase == "starting_normal"
    assert journal.machine.state.restored_generation == RESTORED.generation
    observe(journal, 19, replace(RESTORED, generation="f" * 64))
    assert journal.machine.state.reason == "restored_generation_changed"


def test_lost_start_reply_reconciles_success_without_second_start(directory, journal):
    reach_restore(journal)
    journal.close()
    with policy.Journal(directory) as recovered:
        assert observe(recovered, 18, RESTORED) is None
        assert recovered.machine.state.phase == "complete"


@pytest.mark.parametrize("phase", ["requested", "candidate_running", "starting_normal"])
@pytest.mark.parametrize(
    "fault", ["normal_pin", "candidate_pin", "other_owner", "core", "recording"]
)
def test_drift_and_external_activity_do_not_authorize_actions(journal, phase, fault):
    normal, candidate = NORMAL, CANDIDATE
    if phase == "requested":
        journal.append(event("request"))
    elif phase == "candidate_running":
        reach_candidate(journal)
        normal, candidate = NORMAL_STOPPED, CANDIDATE_RUNNING
    else:
        reach_restore(journal)
        normal = RESTORED
    changes = {}
    if fault == "normal_pin":
        normal = replace(normal, pin="f" * 64)
    elif fault == "candidate_pin":
        candidate = replace(candidate, pin="f" * 64)
    elif fault == "other_owner":
        changes["other_owners_stopped"] = False
    elif fault == "core":
        changes["core_running"] = False
    elif normal.state == "running":
        normal = replace(normal, recording=True)
    else:
        candidate = replace(candidate, recording=True)
    assert observe(journal, 20, normal, candidate, **changes) is None
    assert journal.machine.state.phase == "review"


@pytest.mark.parametrize("target", ["candidate", "normal"])
def test_external_restarts_during_trial_are_not_adopted(journal, target):
    reach_candidate(journal)
    normal, candidate = NORMAL_STOPPED, CANDIDATE_RUNNING
    if target == "normal":
        normal = RESTORED
    else:
        candidate = replace(candidate, generation="f" * 64)
    assert observe(journal, 15, normal, candidate) is None
    assert journal.machine.state.phase == "review"


def test_pending_stop_and_jobs_never_authorize_second_owner(journal):
    reach_candidate(journal)
    journal.append(event("finish", 15))
    observe(journal, 16, NORMAL_STOPPED, CANDIDATE_RUNNING)
    assert observe(journal, 17, NORMAL_STOPPED, jobs_idle=False) is None
    assert observe(journal, 18, NORMAL_STOPPED, replace(CANDIDATE, state="unknown")) is None
    assert observe(journal, 19, NORMAL_STOPPED, CANDIDATE_RUNNING) is None
    assert observe(journal, 20, NORMAL_STOPPED).slug == policy.NORMAL


@pytest.mark.parametrize("normal_state", ["running", "stopped", "unknown"])
@pytest.mark.parametrize("candidate_state", ["running", "stopped", "unknown"])
@pytest.mark.parametrize("jobs_idle", [True, False])
def test_restore_gate_all_state_combinations(journal, normal_state, candidate_state, jobs_idle):
    reach_candidate(journal)
    journal.append(event("finish", 15))
    observe(journal, 16, NORMAL_STOPPED, CANDIDATE_RUNNING)
    normal = NORMAL if normal_state == "running" else replace(NORMAL_STOPPED, state=normal_state)
    candidate = (
        CANDIDATE_RUNNING
        if candidate_state == "running"
        else replace(CANDIDATE, state=candidate_state)
    )
    action = observe(journal, 17, normal, candidate, jobs_idle=jobs_idle)
    permitted = normal_state == candidate_state == "stopped" and jobs_idle
    assert (action is not None) is permitted
    if action:
        assert (action.operation, action.slug) == ("start", policy.NORMAL)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sampled_at", float("nan")),
        ("sampled_at", float("inf")),
        ("sampled_at", True),
        ("other_owners_stopped", 1),
        ("jobs_idle", None),
        ("core_running", "true"),
    ],
)
def test_strict_observation_types(field, value):
    with pytest.raises(policy.UnsafeHandoff):
        replace(observation(), **{field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"pin": "bad"},
        {"generation": None},
        {"healthy": 1},
        {"recording": "false"},
        {"state": "stopped"},
        {"state": "invented"},
        {"generation": "x" * 64},
    ],
)
def test_strict_app_types(changes):
    with pytest.raises(policy.UnsafeHandoff):
        replace(NORMAL, **changes)


@pytest.mark.parametrize("offset", [-0.1, 2.1])
def test_stale_or_future_observation_refused_without_consuming_intent(journal, offset):
    journal.append(event("request"))
    before = journal.machine.state
    with pytest.raises(policy.UnsafeHandoff):
        journal.append(event("observe", 20, obs=observation(20 - offset)))
    assert journal.machine.state == before and len(journal.entries) == 2
    assert observe(journal, 21).operation == "stop"


@pytest.mark.parametrize("kind", ["prepare", "request", "finish"])
def test_duplicate_requests_refused_without_replacement(journal, kind):
    if kind == "request":
        journal.append(event("request"))
    elif kind == "finish":
        reach_candidate(journal)
        journal.append(event("finish", 15))
    count = len(journal.entries)
    with pytest.raises(policy.UnsafeHandoff):
        journal.append(prepare() if kind == "prepare" else event(kind, 20))
    assert len(journal.entries) == count


def test_journal_excludes_second_controller(directory, journal):
    with pytest.raises(BlockingIOError):
        policy.Journal(directory)
    assert journal.machine.state.phase == "prepared"


@pytest.mark.parametrize(
    "damage",
    [
        "truncated",
        "extra",
        "missing",
        "wrong_hash",
        "duplicate",
        "nan",
        "world_readable",
        "symlink",
        "hardlink",
        "fifo",
    ],
)
def test_damaged_evidence_is_preserved_not_reinitialized(directory, journal, damage):
    journal.close()
    first = directory / "0000.json"
    if damage == "truncated":
        first.write_bytes(b'{"schema":')
    elif damage == "extra":
        (directory / "unexpected").write_text("retain")
    elif damage == "missing":
        first.rename(directory / "0001.json")
    elif damage == "wrong_hash":
        value = json.loads(first.read_bytes())
        value["previous"] = "f" * 64
        first.write_text(json.dumps(value))
    elif damage == "duplicate":
        first.write_text('{"schema":1,"schema":1}')
    elif damage == "nan":
        first.write_text('{"schema":NaN}')
    elif damage == "world_readable":
        first.chmod(0o644)
    elif damage == "hardlink":
        os.link(first, directory.parent / "linked")
    else:
        first.rename(directory.parent / "saved")
        if damage == "symlink":
            first.symlink_to(directory.parent / "saved")
        else:
            os.mkfifo(first, 0o600)
    names = sorted(directory.iterdir())
    with pytest.raises((ValueError, OSError)):
        policy.Journal(directory)
    assert sorted(directory.iterdir()) == names


@pytest.mark.parametrize("operation", ["open", "fsync"])
def test_publication_failure_cannot_emit_action_or_retry(directory, journal, operation):
    journal.append(event("request"))
    with (
        patch.object(policy.os, operation, side_effect=OSError("injected")),
        pytest.raises(OSError),
    ):
        observe(journal)
    assert journal.fd == -1
    with pytest.raises(policy.UnsafeHandoff):
        observe(journal)
    # An fsync failure may leave a complete but unacknowledged intent. Reopening
    # reconciles it and cannot emit it again; open failure left nothing dispatched.
    with policy.Journal(directory) as recovered:
        if operation == "fsync":
            assert recovered.machine.state.phase == "stopping_normal"
            assert observe(recovered, 13) is None
        else:
            assert recovered.machine.state.phase == "requested"


def test_real_process_death_after_intent_does_not_reemit(directory, journal):
    journal.append(event("request"))
    journal.close()
    code = (
        "import os,sys; sys.path.insert(0,sys.argv[1]); "
        "from pathlib import Path; import supplemental_handoff_policy as p; "
        "j=p.Journal(Path(sys.argv[2])); "
        "e={'kind':'observe','now':12,'boot_id':sys.argv[3],"
        "'observation':p.asdict(p.Observation(12,j.machine.baseline.normal,"
        "j.machine.baseline.candidate,True,True,True))}; "
        "a=j.append(e); assert a.slug==p.NORMAL; os._exit(23)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPT.parent), str(directory), BOOT],
        check=False,
        capture_output=True,
        timeout=5,
    )
    assert completed.returncode == 23, completed.stderr
    with policy.Journal(directory) as recovered:
        assert recovered.machine.state.phase == "stopping_normal"
        assert observe(recovered, 13) is None
        # A separate observation of proven stop permits only the next action.
        action = observe(recovered, 14, NORMAL_STOPPED)
        assert (action.operation, action.slug) == ("start", policy.CANDIDATE)


def test_cli_has_no_operational_entrypoint():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], check=False, capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 1
    assert "qualified independent host adapter is still required" in result.stderr


@pytest.mark.parametrize("fault", ["rename", "mode", "extra"])
def test_journal_changed_under_live_controller_is_not_used(directory, journal, fault):
    journal.append(event("request"))
    if fault == "rename":
        directory.rename(directory.parent / "preserved")
        directory.mkdir(mode=0o700)
    elif fault == "mode":
        directory.chmod(0o755)
    else:
        (directory / "extra").write_text("preserve")
    with pytest.raises(policy.UnsafeHandoff):
        observe(journal)
    assert journal.fd == -1


@pytest.mark.parametrize("now,reason", [(310, "request_expired"), (1510, "hard_deadline")])
def test_late_request_does_not_reset_deadlines(journal, now, reason):
    assert journal.append(event("request", now)) is None
    assert journal.machine.state.reason == reason


def test_jobs_that_never_settle_end_in_review_without_another_command(journal):
    reach_candidate(journal)
    deadline = journal.machine.state.trial_deadline
    assert observe(journal, deadline, NORMAL_STOPPED, CANDIDATE_RUNNING, jobs_idle=False) is None
    assert (
        observe(
            journal,
            deadline + policy.COMMAND_SECONDS,
            NORMAL_STOPPED,
            CANDIDATE_RUNNING,
            jobs_idle=False,
        )
        is None
    )
    assert journal.machine.state.reason == "recovery_observation_deadline"


@pytest.mark.parametrize("requested", [True, False])
def test_already_stopped_baseline_is_not_adopted_for_handoff(journal, requested):
    if requested:
        journal.append(event("request"))
    assert observe(journal, 12, NORMAL_STOPPED) is None
    assert journal.machine.state.reason == "normal_stopped_externally"


@pytest.mark.parametrize("fault", ["normal_generation", "external_candidate"])
def test_pre_handoff_generation_and_owner_checks(journal, fault):
    journal.append(event("request"))
    normal, candidate = NORMAL, CANDIDATE
    if fault == "normal_generation":
        normal = RESTORED
    else:
        candidate = CANDIDATE_RUNNING
    assert observe(journal, 12, normal, candidate) is None
    assert journal.machine.state.phase == "review"


def test_old_baseline_generation_is_not_restoration_proof(journal):
    reach_restore(journal)
    assert observe(journal, 18, NORMAL) is None
    assert journal.machine.state.reason == "old_normal_generation_reappeared"


def test_candidate_reappearing_during_normal_start_requires_review(journal):
    reach_restore(journal)
    assert observe(journal, 18, NORMAL_STOPPED, CANDIDATE_RUNNING) is None
    assert journal.machine.state.reason == "candidate_reappeared"


@pytest.mark.parametrize("value", [None, "not-an-id", "../secret", "a" * 32])
def test_invalid_case_id_never_creates_evidence(directory, value):
    with policy.Journal(directory) as journal, pytest.raises(policy.UnsafeHandoff):
        journal.append({**prepare(), "case_id": value})
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("update", [{"extra": "secret"}, {"generation": None}])
def test_malformed_app_payload_is_refused(journal, update):
    journal.append(event("request"))
    obs = asdict(observation(12))
    obs["normal"].update(update)
    with pytest.raises(policy.UnsafeHandoff):
        journal.append({**event("observe", 12), "observation": obs})
    assert journal.machine.state.phase == "requested"


def test_sigkill_releases_lock_and_preserves_unexecuted_intent(directory, journal):
    journal.append(event("request"))
    journal.close()
    code = (
        "import sys,time; sys.path.insert(0,sys.argv[1]); "
        "from pathlib import Path; import supplemental_handoff_policy as p; "
        "j=p.Journal(Path(sys.argv[2])); "
        "e={'kind':'observe','now':12,'boot_id':sys.argv[3],"
        "'observation':p.asdict(p.Observation(12,j.machine.baseline.normal,"
        "j.machine.baseline.candidate,True,True,True))}; "
        "a=j.append(e); assert a.slug==p.NORMAL; print('durable',flush=True); time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(SCRIPT.parent), str(directory), BOOT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        import select

        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline().strip() == "durable"
        child.kill()
        assert child.wait(timeout=5) == -signal.SIGKILL
        with policy.Journal(directory) as recovered:
            assert observe(recovered, 13) is None
            assert recovered.machine.state.phase == "stopping_normal"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_dispatch_preconditions_ignore_only_sampling_time(journal):
    journal.append(event("request"))
    action = observe(journal)
    assert policy.preconditions(observation(13)) == action.preconditions_sha256
    assert policy.preconditions(observation(13, normal=RESTORED)) != action.preconditions_sha256
    assert (
        policy.preconditions(replace(observation(13), jobs_idle=False))
        != action.preconditions_sha256
    )


def test_confirmed_candidate_startup_failure_restores_without_waiting_for_ready(journal):
    reach_candidate(journal, healthy=False)
    assert journal.machine.state.phase == "stopping_candidate"
    assert journal.machine.state.candidate_generation == CANDIDATE_RUNNING.generation
    assert observe(journal, 15, NORMAL_STOPPED, replace(CANDIDATE_RUNNING, healthy=False)) is None
    action = observe(journal, 16, NORMAL_STOPPED)
    assert (action.operation, action.slug) == ("start", policy.NORMAL)
    assert observe(journal, 17, RESTORED) is None
    assert journal.machine.state.phase == "complete"
