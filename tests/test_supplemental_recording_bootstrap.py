"""Pure bootstrap policy and real journal fsync/replay; no live authority."""

import importlib.util
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_handoff as recording

NAME = "supplemental_recording_bootstrap"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(recording.old.SCRIPT).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
b, old = m.base, recording.old
BOOTSTRAP = m.Bootstrap("a" * 64, "b" * 64, "c" * 64, 120, 600)
IDLE = replace(old.CANDIDATE_RUNNING, healthy=None, recording=None)
directory = recording.directory


def prepared():
    return recording.prepare() | {"kind": "prepare_bootstrap", "bootstrap": asdict(BOOTSTRAP)}


@pytest.fixture
def journal(directory):
    with m.Journal(directory) as journal:
        journal.append(prepared())
        yield journal


def starting(journal, *, bind=True, completed=True):
    recording.bind(journal, b.NORMAL, 10)
    journal.append(recording.event("request", 11))
    assert recording.observe(journal, 12, old.NORMAL, old.CANDIDATE).slug == b.NORMAL
    recording.execution(journal, 12)
    recording.exited(journal, b.NORMAL, 13)
    assert recording.observe(journal, 13, candidate=old.CANDIDATE).slug == b.CANDIDATE
    recording.execution(journal, 14, complete=completed)
    if bind:
        recording.bind(journal, b.CANDIDATE, 15)


@pytest.fixture
def idle(journal):
    starting(journal)
    assert recording.observe(journal, 16, candidate=IDLE) is None
    assert journal.machine.state.phase == "candidate_idle"
    return journal


def authorization(now=17):
    return recording.event(
        "authorize_operator",
        now,
        generation=IDLE.generation,
        bootstrap_sha256=BOOTSTRAP.sha256,
        launch_plan_sha256="d" * 64,
        idle_evidence_sha256="e" * 64,
        observation=asdict(recording.observation(now, old.NORMAL_STOPPED, IDLE)),
    )


@pytest.fixture
def launched(idle):
    action = idle.append(authorization())
    assert type(action) is m.OperatorAction
    assert action.intent_sha256 == b.checksum(authorization())
    return idle


def ready(journal, now=19):
    return recording.event(
        "operator_ready",
        now,
        generation=IDLE.generation,
        intent_sha256=journal.machine.state.launch_intent_sha256,
        ready_evidence_sha256="f" * 64,
        received_at=now - 1,
        observation=asdict(recording.observation(now, old.NORMAL_STOPPED, old.CANDIDATE_RUNNING)),
    )


def worker_exit(journal, now):
    return recording.event(
        "operator_exited",
        now,
        generation=IDLE.generation,
        intent_sha256=journal.machine.state.launch_intent_sha256,
        exit_evidence_sha256="7" * 64,
    )


def test_actual_ready_and_health_are_separate_from_idle_and_recording_permission(launched):
    j = launched
    deadlines = (j.machine.state.deadline, j.machine.state.trial_deadline, j.machine.hard_deadline)
    assert deadlines == (120, 600, 1510)
    assert j.machine.state.authorization_generation is None
    # Even a genuinely healthy cached probe alone is not the native return.
    assert recording.observe(j, 18) is None
    assert j.machine.state.phase == "starting_operator"
    with pytest.raises(b.UnsafeHandoff):
        j.append(recording.authorization(18))
    assert j.append(ready(j)) is None
    assert j.machine.state.phase == "candidate_running"
    assert (
        j.machine.state.deadline,
        j.machine.state.trial_deadline,
        j.machine.hard_deadline,
    ) == deadlines
    assert j.machine.state.authorization_generation is None
    j.append(recording.authorization(20))
    assert j.machine.state.authorization_generation == IDLE.generation
    assert j.machine.state.recording_outcome == "unconfirmed"


def test_good_files_init_exit_and_cli_exit_do_not_replace_worker_exit(launched):
    j = launched
    j.append(ready(j))
    j.append(recording.authorization(20))
    evidence = recording.files("finalized")
    recording.observe(j, 21, evidence=evidence)
    recording.exited(j, b.CANDIDATE, 22)
    assert recording.observe(j, 23, candidate=old.CANDIDATE, evidence=evidence) is None
    assert j.machine.state.phase == "candidate_running"
    j.append(worker_exit(j, 24))
    action = recording.observe(j, 25, candidate=old.CANDIDATE, evidence=evidence)
    assert (action.operation, action.slug) == ("start", b.NORMAL)
    assert j.machine.state.recording_outcome == "verified"
    recording.finish_restore(j, 26, evidence)


def test_failed_operator_can_close_without_claiming_ready_or_recording(launched):
    j = launched
    j.append(worker_exit(j, 18))
    assert j.machine.state.ready_evidence_sha256 is None
    with pytest.raises(b.UnsafeHandoff):
        j.append(ready(j))
    action = recording.observe(j, 19, candidate=IDLE)
    assert (action.operation, action.slug) == ("stop", b.CANDIDATE)
    recording.execution(j, 20)
    recording.exited(j, b.CANDIDATE, 21)
    assert recording.observe(j, 22, candidate=old.CANDIDATE).slug == b.NORMAL
    assert j.machine.state.recording_outcome == "not_attempted"
    recording.finish_restore(j, 23, recording.PRISTINE)


def test_unlaunched_idle_can_be_stopped_and_restored(idle):
    j = idle
    j.append(recording.event("finish", 17))
    assert recording.observe(j, 18, candidate=IDLE).operation == "stop"
    recording.execution(j, 19)
    recording.exited(j, b.CANDIDATE, 20)
    assert recording.observe(j, 21, candidate=old.CANDIDATE).operation == "start"
    recording.finish_restore(j, 22, recording.PRISTINE)


@pytest.mark.parametrize("fault", ["generation", "intent", "proof", "extra", "duplicate"])
def test_worker_exit_cannot_be_substituted_or_replayed(launched, fault):
    value = worker_exit(launched, 19)
    if fault in ("generation", "intent"):
        value["generation" if fault == "generation" else "intent_sha256"] = "9" * 64
    elif fault == "proof":
        value["exit_evidence_sha256"] = "bad"
    elif fault == "extra":
        value["extra"] = True
    else:
        launched.append(worker_exit(launched, 18))
    state = launched.machine.state
    with pytest.raises(b.UnsafeHandoff):
        launched.append(value)
    assert launched.machine.state == state


def test_worker_exit_withholds_new_recording_authorization(launched):
    launched.append(ready(launched))
    launched.append(worker_exit(launched, 20))
    with pytest.raises(b.UnsafeHandoff):
        launched.append(recording.authorization(21))
    assert launched.machine.state.authorization_generation is None


@pytest.mark.parametrize("phase", ["idle", "launched"])
@pytest.mark.parametrize(
    "fault",
    ["jobs", "unknown", "core", "owners", "normal", "generation", "pin", "recording", "files"],
)
def test_bootstrap_observations_never_promote_uncertain_state(idle, phase, fault):
    if phase == "launched":
        idle.append(authorization())
    obs = recording.observation(18, old.NORMAL_STOPPED, IDLE)
    if fault == "jobs":
        obs = replace(obs, jobs_idle=False)
    elif fault == "unknown":
        obs = replace(obs, candidate=b.App(IDLE.pin, "unknown"))
    elif fault == "core":
        obs = replace(obs, core_running=False)
    elif fault == "owners":
        obs = replace(obs, other_owners_stopped=False)
    elif fault == "normal":
        obs = replace(obs, normal=old.NORMAL)
    elif fault in ("generation", "pin", "recording"):
        obs = replace(
            obs, candidate=replace(IDLE, **{fault: True if fault == "recording" else "9" * 64})
        )
    else:
        obs = replace(obs, files=recording.files("active"))
    assert idle.append(recording.event("observe", 18, observation=asdict(obs))) is None
    expected = "candidate_idle" if phase == "idle" else "starting_operator"
    assert idle.machine.state.phase == (expected if fault in ("jobs", "unknown") else "review")
    assert idle.machine.state.ready_evidence_sha256 is None


@pytest.mark.parametrize("missing", ["pidfd", "cli"])
def test_idle_transition_needs_original_init_and_start_cli_exit(journal, missing):
    starting(journal, bind=missing != "pidfd", completed=missing != "cli")
    assert recording.observe(journal, 16, candidate=IDLE) is None
    assert journal.machine.state.phase == "starting_candidate"
    with pytest.raises(b.UnsafeHandoff):
        journal.append(authorization())


@pytest.mark.parametrize(
    "healthy,recording_flag", [(True, False), (False, False), (None, False), (None, True)]
)
def test_running_old_daemon_is_not_idle(journal, healthy, recording_flag):
    starting(journal)
    recording.observe(
        journal, 16, candidate=replace(IDLE, healthy=healthy, recording=recording_flag)
    )
    assert journal.machine.state.phase == "review"
    assert journal.machine.state.launch_intent_sha256 is None


@pytest.mark.parametrize(
    "fault",
    [
        "generation",
        "bootstrap",
        "plan",
        "idle",
        "normal",
        "candidate_pin",
        "health",
        "recording",
        "jobs",
        "core",
        "owners",
        "files",
        "stale",
        "extra",
    ],
)
def test_launch_authorization_rejects_changed_or_unknown_preconditions(idle, fault):
    value = authorization()
    obs = value["observation"]
    if fault == "generation":
        value["generation"] = "9" * 64
    elif fault == "bootstrap":
        value["bootstrap_sha256"] = "9" * 64
    elif fault == "plan":
        value["launch_plan_sha256"] = "bad"
    elif fault == "idle":
        value["idle_evidence_sha256"] = "bad"
    elif fault == "normal":
        obs["normal"] = asdict(old.NORMAL)
    elif fault == "candidate_pin":
        obs["candidate"]["pin"] = "9" * 64
    elif fault == "health":
        obs["candidate"]["healthy"] = True
    elif fault == "recording":
        obs["candidate"]["recording"] = False
    elif fault in ("jobs", "core", "owners"):
        obs[
            {"jobs": "jobs_idle", "core": "core_running", "owners": "other_owners_stopped"}[fault]
        ] = False
    elif fault == "files":
        obs["files"] = asdict(recording.files("unknown"))
    elif fault == "stale":
        obs["sampled_at"] = 14
    else:
        value["extra"] = True
    state, count = idle.machine.state, len(idle.entries)
    with pytest.raises(b.UnsafeHandoff):
        idle.append(value)
    assert idle.machine.state == state and len(idle.entries) == count


@pytest.mark.parametrize(
    "fault",
    [
        "intent",
        "generation",
        "proof",
        "preauthorization",
        "future",
        "stale_return",
        "sample_order",
        "health",
        "recording",
        "init_exit",
        "finish",
        "extra",
    ],
)
def test_ready_requires_current_actual_context_and_truthful_health(launched, fault):
    value = ready(launched)
    obs = value["observation"]
    if fault == "intent":
        value["intent_sha256"] = "9" * 64
    elif fault == "generation":
        value["generation"] = "9" * 64
    elif fault == "proof":
        value["ready_evidence_sha256"] = None
    elif fault in ("preauthorization", "future", "stale_return", "sample_order"):
        value["received_at"] = {
            "preauthorization": 16,
            "future": 20,
            "stale_return": 17,
            "sample_order": 19,
        }[fault]
        if fault == "stale_return":
            value["now"] = obs["sampled_at"] = 20
        if fault == "sample_order":
            obs["sampled_at"] = 18
    elif fault == "health":
        obs["candidate"]["healthy"] = None
    elif fault == "recording":
        obs["candidate"]["recording"] = True
    elif fault == "init_exit":
        recording.exited(launched, b.CANDIDATE, 18)
    elif fault == "finish":
        launched.append(recording.event("finish", 18))
    else:
        value["extra"] = True
    state = launched.machine.state
    with pytest.raises(b.UnsafeHandoff):
        launched.append(value)
    assert launched.machine.state == state


@pytest.mark.parametrize("kind", ["tick", "observe", "authorize_operator", "operator_ready"])
def test_original_ready_deadline_is_not_renewed(launched, kind):
    if kind == "tick":
        value = recording.event(kind, 120)
    elif kind == "observe":
        value = recording.event(
            kind, 120, observation=asdict(recording.observation(120, old.NORMAL_STOPPED, IDLE))
        )
    elif kind == "authorize_operator":
        value = authorization(120)
    else:
        value = ready(launched, 120)
    assert launched.append(value) is None
    assert launched.machine.state.phase == "review" and launched.machine.state.deadline == 120


def test_recording_authorization_cannot_outlive_original_begin_deadline(launched):
    launched.append(ready(launched))
    with pytest.raises(b.UnsafeHandoff):
        launched.append(recording.authorization(120))
    assert launched.machine.state.authorization_generation is None


@pytest.mark.parametrize("kind", ["authorize_operator", "operator_ready"])
def test_changed_boot_blocks_launch_or_readiness(idle, kind):
    if kind == "operator_ready":
        idle.append(authorization())
        value = ready(idle)
    else:
        value = authorization()
    value["boot_id"] = "9" * 32
    assert idle.append(value) is None
    assert idle.machine.state.phase == "review" and idle.machine.state.reason == "host_boot_changed"


def test_reopen_does_not_resend_operator_or_accept_old_journals(launched, directory):
    state = launched.machine.state
    launched.close()
    for older in (recording.h.Journal, b.Journal):
        with pytest.raises(b.UnsafeHandoff):
            older(directory)
    with m.Journal(directory) as reopened:
        assert reopened.machine.state == state
        with pytest.raises(b.UnsafeHandoff):
            reopened.append(authorization(18))
        assert reopened.append(ready(reopened)) is None
        with pytest.raises(b.UnsafeHandoff):
            reopened.append(ready(reopened, 20))


@pytest.mark.parametrize("lost", ["file_fsync", "directory_fsync"])
def test_lost_durable_launch_return_never_reissues_permission(idle, directory, monkeypatch, lost):
    actual, calls = os.fsync, []

    def fail(fd):
        calls.append(fd)
        actual(fd)
        if len(calls) == (1 if lost == "file_fsync" else 2):
            raise OSError("injected lost publication return")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(OSError):
            idle.append(authorization())
    assert idle.fd == -1
    with m.Journal(directory) as reopened:
        assert reopened.machine.state.phase == "starting_operator"
        with pytest.raises(b.UnsafeHandoff):
            reopened.append(authorization(18))


@pytest.mark.parametrize("version", [1, 2])
def test_new_journal_refuses_legacy_format(directory, version):
    cls, event = (
        (b.Journal, old.prepare()) if version == 1 else (recording.h.Journal, recording.prepare())
    )
    with cls(directory) as old_journal:
        old_journal.append(event)
    with pytest.raises(b.UnsafeHandoff):
        m.Journal(directory)
    assert json.loads((directory / "0000.json").read_text())["schema"] == version


@pytest.mark.parametrize(
    "change",
    [
        {"ready_by": True},
        {"stop_by": float("nan")},
        {"ready_by": 10},
        {"ready_by": 611},
        {"stop_by": 791},
        {"stop_by": 300},
        {"host_plan_sha256": "bad"},
    ],
)
def test_fixed_bootstrap_plan_bounds(directory, change):
    value = prepared()
    value["bootstrap"].update(change)
    with m.Journal(directory) as journal, pytest.raises(b.UnsafeHandoff):
        journal.append(value)
