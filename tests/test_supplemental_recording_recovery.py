"""Recording policy plus real tracking/dispatch bridge against a fake local host."""

import importlib.util
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_recovery as previous
from . import test_supplemental_recording_handoff as policy_tests

NAME = "supplemental_recording_recovery"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(policy_tests.h.__file__).with_name(NAME + ".py")
)
r = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = r
SPEC.loader.exec_module(r)
h, b = policy_tests.h, policy_tests.b


class Host(previous.Host):
    def __init__(self):
        super().__init__()
        self.contract = replace(policy_tests.CONTRACT, case_id=previous.CASE)
        self.pristine = h.Files(self.contract.sha256, "pristine", "5" * 64)
        self.evidence = self.pristine
        self.recording = False

    def read(self):
        ordinary = super().read()
        obs = ordinary.observation
        candidate = obs.candidate
        if candidate.state == "running":
            candidate = replace(candidate, recording=self.recording)
        return r.Sample(
            self.boot,
            self.now,
            h.Observation(
                obs.sampled_at,
                obs.normal,
                candidate,
                obs.other_owners_stopped,
                obs.jobs_idle,
                obs.core_running,
                self.evidence,
            ),
        )

    def set_files(self, journal, stage, proof="6" * 64):
        self.evidence = h.Files(
            self.contract.sha256, stage, proof, journal.machine.state.candidate_generation
        )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    host = Host()
    monkeypatch.setattr(previous.r, "read_identity", host.identity)
    monkeypatch.setattr(previous.r, "ProcessWitness", host.witness)
    path = tmp_path / "recording-recovery"
    path.mkdir(mode=0o700)
    with h.Journal(path) as journal:
        journal.append(
            {
                "kind": "prepare_recording",
                "case_id": previous.CASE,
                "boot_id": host.boot,
                "now": host.now,
                "observation": asdict(host.read().observation),
                "contract": asdict(host.contract),
            }
        )
        yield host, journal


def session(host, journal):
    processes = previous.r.TrackedProcesses(
        journal,
        host,
        images={b.NORMAL: previous.IMAGE, b.CANDIDATE: previous.IMAGE},
        read_clock=host.clock,
    )
    dispatch = previous.h.TrackedDispatch(
        journal,
        host,
        cli_image=previous.IMAGE,
        cli_generation=previous.h.generation(
            host.container(previous.h.CLI), name=previous.h.CLI, image=previous.IMAGE
        ),
        now=lambda: host.now,
    )
    return r.RecoverySession(journal, processes, dispatch, host.read)


def candidate(host, journal, run):
    previous.request(host, journal)
    assert previous.poll(host, run).outcome == "dispatch_submitted"
    assert previous.poll(host, run).outcome == "dispatch_submitted"
    assert previous.poll(host, run).phase == "candidate_running"
    host.now += 1
    journal.append(
        {
            "kind": "authorize_recording",
            "boot_id": host.boot,
            "now": host.now,
            "generation": journal.machine.state.candidate_generation,
            "contract_sha256": host.contract.sha256,
            "observation": asdict(host.read().observation),
        }
    )


@pytest.mark.parametrize("ending", ["finalized", "retained"])
@pytest.mark.parametrize("stop", ["natural", "deadline", "finish"])
def test_truthful_recording_recovery_tracks_each_cli_and_init_exit(setup, ending, stop):
    host, journal = setup
    run = session(host, journal)
    try:
        candidate(host, journal, run)
        host.recording = True
        host.set_files(journal, "active")
        assert previous.poll(host, run).phase == "candidate_running"
        assert host.read().observation.candidate.recording is True
        if stop == "natural":
            value = host.containers.pop("app_" + b.CANDIDATE)
            host.dead.add(value["Id"])
        else:
            if stop == "deadline":
                host.now = journal.machine.state.recording_deadline
            else:
                host.now += 1
                journal.append({"kind": "finish", "boot_id": host.boot, "now": host.now})
            assert previous.poll(host, run).outcome == "dispatch_submitted"
        host.recording = False
        host.set_files(journal, ending)
        assert previous.poll(host, run).outcome == "dispatch_submitted"
        assert previous.poll(host, run).phase == "complete"
        assert journal.machine.state.recording_outcome == (
            "verified" if ending == "finalized" else "unconfirmed"
        )
        assert len(journal.machine.state.exited_processes) == 2
        assert len(journal.entries) <= h.Journal.max_events
        expected = [("stop", b.NORMAL), ("start", b.CANDIDATE)]
        if stop != "natural":
            expected.append(("stop", b.CANDIDATE))
        assert host.sent == expected + [("start", b.NORMAL)]
    finally:
        run.close()
    assert all(w.closed for w in host.handles)


def test_missing_candidate_is_not_exit_even_with_final_artifact(setup):
    host, journal = setup
    run = session(host, journal)
    try:
        candidate(host, journal, run)
        host.omit_exit = True
        host.recording = True
        host.set_files(journal, "active")
        host.now = journal.machine.state.recording_deadline
        assert previous.poll(host, run).outcome == "dispatch_submitted"
        host.set_files(journal, "finalized")
        count = len(host.sent)
        assert previous.poll(host, run).phase == "stopping_candidate"
        assert len(host.sent) == count
        assert journal.machine.state.recording_outcome == "unconfirmed"
    finally:
        run.close()


def test_lost_stop_reply_and_service_restart_never_retry_mutation(setup):
    host, journal = setup
    run = session(host, journal)
    candidate(host, journal, run)
    host.recording = True
    host.set_files(journal, "active")
    host.start_error = RuntimeError("private CLI error must not escape")
    host.now = journal.machine.state.recording_deadline
    assert previous.poll(host, run).outcome == "dispatch_unconfirmed"
    # Persist exact exit before losing the pidfd. Reopening cannot manufacture it.
    run.processes.reconcile()
    run.close()
    path, count = journal.path, len(host.sent)
    journal.close()
    host.start_error = None
    host.recording = False
    with h.Journal(path) as reopened:
        host.set_files(reopened, "retained")
        run = session(host, reopened)
        try:
            assert previous.poll(host, run).outcome == "dispatch_submitted"
            assert previous.poll(host, run).phase == "complete"
            assert reopened.machine.state.recording_outcome == "unconfirmed"
            assert len(host.sent) == count + 1
        finally:
            run.close()


@pytest.mark.parametrize(
    "change", ["growth", "stage", "contract", "generation", "unknown", "jobs", "recording"]
)
def test_fresh_pre_dispatch_qualification_allows_growth_but_not_changed_authority(setup, change):
    host, journal = setup
    run = session(host, journal)
    try:
        candidate(host, journal, run)
        host.recording = True
        host.set_files(journal, "active")
        host.now = journal.machine.state.recording_deadline
        first = host.read()
        obs = first.observation
        if change == "growth":
            obs = replace(obs, files=replace(obs.files, evidence_sha256="7" * 64))
        elif change == "stage":
            obs = replace(obs, files=replace(obs.files, stage="finalizing"))
        elif change in ("contract", "generation"):
            obs = replace(
                obs,
                files=replace(
                    obs.files,
                    **{("contract_sha256" if change == "contract" else "generation"): "7" * 64},
                ),
            )
        elif change == "unknown":
            obs = replace(obs, files=h.Files(host.contract.sha256, "unknown"))
        elif change == "jobs":
            obs = replace(obs, jobs_idle=False)
        else:
            obs = replace(obs, candidate=replace(obs.candidate, recording=False))
        values, sent = iter((first, r.Sample(host.boot, host.now, obs))), []
        executor = r.Executor(journal, lambda: next(values), lambda *args: sent.append(args))
        result = executor.poll()
        assert result.outcome == ("dispatch_submitted" if change == "growth" else "intent_withheld")
        assert len(sent) == (1 if change == "growth" else 0)
        # Whether withheld or sent, no second stop intent may be produced.
        executor.read = lambda: first
        assert executor.poll().outcome == "observed"
        assert len(sent) == (1 if change == "growth" else 0)
    finally:
        run.close()


def test_recording_executor_refuses_legacy_samples_and_journals(setup, tmp_path):
    host, journal = setup
    legacy = previous.Host().read()
    executor = r.Executor(journal, lambda: legacy, lambda *_: pytest.fail("unexpected dispatch"))
    assert executor.poll().outcome == "observation_unavailable"
    path = tmp_path / "old"
    path.mkdir(mode=0o700)
    with b.Journal(path) as other:
        other.append(policy_tests.old.prepare())
        with pytest.raises(b.UnsafeHandoff):
            r.Executor(other, host.read, lambda *_: None)


def test_independent_tick_expires_without_working_observer(setup):
    host, journal = setup
    run = session(host, journal)
    try:
        candidate(host, journal, run)
        host.now = journal.machine.state.recording_deadline + b.COMMAND_SECONDS
        run.read = lambda: pytest.fail("terminal policy should not collect host state")
        result = run.poll()
        assert result.phase == "review" and result.outcome == "terminal"
        assert journal.machine.state.reason == "recording_recovery_deadline"
    finally:
        run.close()
