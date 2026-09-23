"""Real journal/dispatch bridge, explicitly synthetic host/process observations.

No Engine, container, native recorder or scanner. Actor/Ready/exit digests are
policy inputs only; their actual authentication is tested by separate joins.
"""

from dataclasses import asdict, replace

import pytest

from . import test_supplemental_recording_bootstrap as policy
from . import test_supplemental_recording_recovery as recording

m, old, b = policy.m, recording.previous, policy.b


class Host(recording.Host):
    def __init__(self):
        super().__init__()
        self.operator_ready = False

    def read(self):
        sample = super().read()
        candidate = sample.observation.candidate
        if candidate.state == "running" and not self.operator_ready:
            candidate = replace(candidate, healthy=None, recording=None)
        return replace(sample, observation=replace(sample.observation, candidate=candidate))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    host = Host()
    monkeypatch.setattr(old.r, "read_identity", host.identity)
    monkeypatch.setattr(old.r, "ProcessWitness", host.witness)
    path = tmp_path / "bootstrap-recovery"
    path.mkdir(mode=0o700)
    with m.Journal(path) as journal:
        journal.append(
            dict(
                kind="prepare_bootstrap",
                case_id=old.CASE,
                boot_id=host.boot,
                now=host.now,
                observation=asdict(host.read().observation),
                contract=asdict(host.contract),
                bootstrap=asdict(policy.BOOTSTRAP),
            )
        )
        yield host, journal


def session(host, journal):
    processes = old.r.TrackedProcesses(
        journal, host, images={b.NORMAL: old.IMAGE, b.CANDIDATE: old.IMAGE}, read_clock=host.clock
    )
    dispatch = old.h.TrackedDispatch(
        journal,
        host,
        cli_image=old.IMAGE,
        cli_generation=old.h.generation(host.container(old.h.CLI), name=old.h.CLI, image=old.IMAGE),
        now=lambda: host.now,
    )
    return m.RecoverySession(journal, processes, dispatch, host.read)


def idle(host, journal, run):
    old.request(host, journal)
    assert old.poll(host, run).outcome == "dispatch_submitted"
    assert old.poll(host, run).outcome == "dispatch_submitted"
    assert old.poll(host, run).phase == "candidate_idle"
    assert journal.machine.state.authorization_generation is None
    assert host.read().observation.candidate.healthy is None
    assert host.read().observation.candidate.recording is None


def event(host, journal, kind, **fields):
    host.now += 1
    return journal.append(dict(kind=kind, boot_id=host.boot, now=host.now, **fields))


def authorize_operator(host, journal):
    return event(
        host,
        journal,
        "authorize_operator",
        generation=journal.machine.state.candidate_generation,
        bootstrap_sha256=journal.machine.bootstrap.sha256,
        launch_plan_sha256="d" * 64,
        idle_evidence_sha256="e" * 64,
        observation=asdict(host.read().observation),
    )


def operator_ready(host, journal):
    host.operator_ready = True
    event(
        host,
        journal,
        "operator_ready",
        generation=journal.machine.state.candidate_generation,
        intent_sha256=journal.machine.state.launch_intent_sha256,
        ready_evidence_sha256="f" * 64,
        received_at=host.now,
        observation=asdict(host.read().observation),
    )


def operator_exited(host, journal):
    event(
        host,
        journal,
        "operator_exited",
        generation=journal.machine.state.candidate_generation,
        intent_sha256=journal.machine.state.launch_intent_sha256,
        exit_evidence_sha256="7" * 64,
    )


def test_truthful_idle_can_finish_and_restore_without_ever_authorizing_operator(setup):
    host, journal = setup
    run = session(host, journal)
    original = journal.machine.hard_deadline
    try:
        idle(host, journal, run)
        for _ in range(3):
            assert old.poll(host, run).phase == "candidate_idle"
        assert len(host.sent) == 2
        event(host, journal, "finish")
        assert old.poll(host, run).outcome == "dispatch_submitted"
        assert old.poll(host, run).outcome == "dispatch_submitted"
        assert old.poll(host, run).phase == "complete"
        assert journal.machine.hard_deadline == original
        assert journal.machine.state.launch_intent_sha256 is None
        assert journal.machine.state.recording_outcome == "not_attempted"
        assert len(journal.machine.state.exited_processes) == 2
        assert host.sent == [
            ("stop", b.NORMAL),
            ("start", b.CANDIDATE),
            ("stop", b.CANDIDATE),
            ("start", b.NORMAL),
        ]
    finally:
        run.close()
    assert all(witness.closed for witness in host.handles)


def test_healthy_cache_is_not_ready_or_recording_permission(setup):
    host, journal = setup
    run = session(host, journal)
    try:
        idle(host, journal, run)
        action = authorize_operator(host, journal)
        assert type(action) is m.OperatorAction
        host.operator_ready = True
        assert old.poll(host, run).phase == "starting_operator"
        assert len(host.sent) == 2  # This bridge never dispatches operator exec.
        with pytest.raises(b.UnsafeHandoff):
            event(
                host,
                journal,
                "authorize_recording",
                generation=journal.machine.state.candidate_generation,
                contract_sha256=host.contract.sha256,
                observation=asdict(host.read().observation),
            )
        operator_ready(host, journal)
        assert journal.machine.state.phase == "candidate_running"
        assert journal.machine.state.authorization_generation is None
    finally:
        run.close()


@pytest.mark.parametrize("ready", [False, True])
def test_init_exit_cannot_replace_launched_operator_exit(setup, ready):
    host, journal = setup
    run = session(host, journal)
    try:
        idle(host, journal, run)
        authorize_operator(host, journal)
        if ready:
            operator_ready(host, journal)
        event(host, journal, "finish")
        assert old.poll(host, run).outcome == "dispatch_submitted"
        count = len(host.sent)
        assert old.poll(host, run).phase == "stopping_candidate"
        assert len(journal.machine.state.exited_processes) == 2
        assert len(host.sent) == count and journal.machine.state.operator_exit_sha256 is None
        operator_exited(host, journal)
        assert old.poll(host, run).outcome == "dispatch_submitted"
        assert old.poll(host, run).phase == "complete"
        assert host.sent[-1] == ("start", b.NORMAL)
    finally:
        run.close()


@pytest.mark.parametrize("ending", ["finalized", "retained"])
def test_one_recording_returns_and_worker_exits_remain_separate(setup, ending):
    host, journal = setup
    run = session(host, journal)
    try:
        idle(host, journal, run)
        authorize_operator(host, journal)
        operator_ready(host, journal)
        event(
            host,
            journal,
            "authorize_recording",
            generation=journal.machine.state.candidate_generation,
            contract_sha256=host.contract.sha256,
            observation=asdict(host.read().observation),
        )
        host.recording = True
        host.set_files(journal, "active")
        assert old.poll(host, run).phase == "candidate_running"
        host.now = journal.machine.state.recording_deadline
        assert old.poll(host, run).outcome == "dispatch_submitted"
        host.recording = False
        host.set_files(journal, ending)
        assert old.poll(host, run).phase == "stopping_candidate"
        assert host.sent[-1] == ("stop", b.CANDIDATE)
        operator_exited(host, journal)
        assert old.poll(host, run).outcome == "dispatch_submitted"
        assert old.poll(host, run).phase == "complete"
        assert journal.machine.state.recording_outcome == (
            "verified" if ending == "finalized" else "unconfirmed"
        )
    finally:
        run.close()


@pytest.mark.parametrize("stage", ["idle", "launched", "ready"])
def test_original_deadline_tick_survives_unavailable_observer(setup, stage):
    host, journal = setup
    run = session(host, journal)
    try:
        idle(host, journal, run)
        if stage in ("launched", "ready"):
            authorize_operator(host, journal)
        if stage == "ready":
            operator_ready(host, journal)
        original = journal.machine.hard_deadline
        host.now = original
        run.read = lambda: pytest.fail("Expired policy must not query the host")
        assert run.poll().phase == "review"
        assert journal.machine.hard_deadline == original
        assert len(host.sent) == 2
    finally:
        run.close()


def test_lost_idle_stop_reply_reopens_without_replaying_or_renewing(setup):
    host, journal = setup
    run = session(host, journal)
    idle(host, journal, run)
    deadlines = (
        journal.machine.created_at,
        journal.machine.hard_deadline,
        journal.machine.bootstrap,
    )
    event(host, journal, "finish")
    host.start_error = RuntimeError("PRIVATE")
    assert old.poll(host, run).outcome == "dispatch_unconfirmed"
    run.processes.reconcile()  # Persist actual fixture exit before closing pidfd.
    run.close()
    path, count = journal.path, len(host.sent)
    journal.close()
    host.start_error = None
    with m.Journal(path) as reopened:
        assert (
            reopened.machine.created_at,
            reopened.machine.hard_deadline,
            reopened.machine.bootstrap,
        ) == deadlines
        run = session(host, reopened)
        try:
            assert old.poll(host, run).outcome == "dispatch_submitted"
            assert old.poll(host, run).phase == "complete"
            assert len(host.sent) == count + 1
            assert host.sent[-1] == ("start", b.NORMAL)
        finally:
            run.close()


def test_old_recording_executor_cannot_consume_bootstrap_journal(setup):
    host, journal = setup
    with pytest.raises(b.UnsafeHandoff):
        recording.r.Executor(journal, host.read, lambda *_: pytest.fail("No dispatch"))


def test_bootstrap_executor_cannot_consume_old_recording_journal(recording_setup):
    host, journal = recording_setup
    with pytest.raises(b.UnsafeHandoff):
        m.Executor(journal, host.read, lambda *_: pytest.fail("No dispatch"))


recording_setup = recording.setup
