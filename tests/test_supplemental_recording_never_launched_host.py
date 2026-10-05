"""Real journal/files; explicit synthetic Engine/pidfd/cache cancellation route."""

import copy
import os
from contextlib import contextmanager
from dataclasses import asdict, replace
from threading import Thread

import pytest

from . import test_supplemental_recording_transfer_host as transfer_tests

m, b, audio = transfer_tests.m, transfer_tests.b, transfer_tests.audio
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    transfer,
) = (
    transfer_tests.layout,
    transfer_tests.tree,
    transfer_tests.routing,
    transfer_tests.projection,
    transfer_tests.binding,
    transfer_tests.directory,
    transfer_tests.prepared,
    transfer_tests.joined,
    transfer_tests.before_handoff,
    transfer_tests.transfer,
)


@contextmanager
def owned_session(s, monkeypatch, create_session=None):
    """One original synthetic Engine/session, before any request or dispatch."""
    previous = audio.ot.recovery_tests
    engine = previous.Host()
    engine.containers = s.host.values
    original = engine.start_execution

    def start(eid):
        command = engine.execs[eid]["ProcessConfig"]["arguments"]
        _, operation, slug, _ = command
        stopped = copy.deepcopy(engine.containers.get("app_" + slug))
        try:
            original(eid)
        finally:
            if operation == "start":
                if slug == b.CANDIDATE:
                    transfer_tests.candidate(s)
                else:
                    normal = previous.container("app_" + b.NORMAL, 6)
                    normal.update(audio.container_network(False))
                    s.host.values["app_" + b.NORMAL] = normal
            elif slug == b.CANDIDATE:
                stopped["State"].update(
                    Status="exited",
                    Running=False,
                    Pid=0,
                    ExitCode=0,
                    FinishedAt="2026-09-22T00:01:00Z",
                )
                s.host.values["app_" + b.CANDIDATE] = stopped

    monkeypatch.setattr(previous.r, "read_identity", engine.identity)
    monkeypatch.setattr(previous.r, "ProcessWitness", engine.witness)
    monkeypatch.setattr(s.docker, "create_execution", engine.create_execution)
    monkeypatch.setattr(s.docker, "start_execution", start)
    monkeypatch.setattr(s.docker, "inspect_execution", engine.inspect_execution)

    def clock():
        value = m.plans.clock.read()
        s.plan.check_clock(value)
        return value.boot, value.boottime_ns / m.plans.clock.NS

    if create_session is None:
        processes = m.TrackedProcesses(
            s.journal,
            s.docker,
            images={b.NORMAL: s.plan.normal.image, b.CANDIDATE: s.plan.candidate.image},
            read_clock=clock,
        )
        dispatch = m.TrackedDispatch(
            s.journal,
            s.docker,
            cli_image=s.plan.cli_image,
            cli_generation=s.plan.cli_generation,
            now=lambda: clock()[1],
        )
        session = m.bootstrap.RecoverySession(s.journal, processes, dispatch, s.before.read)
    else:
        # The service factory creates the ONLY session. No discarded temporary
        # owner or reconstructed process tracker precedes the test's real join.
        session = create_session()
    try:
        s.session, s.engine = session, engine

        def cached(docker, seal, command, generation):
            assert docker is s.docker and seal is s.plan.normal
            assert command == session.read.__self__.normal_reader.command
            normal = s.host.values["app_" + b.NORMAL]
            assert generation == audio.h.generation(
                normal, name="app_" + b.NORMAL, image=seal.image
            )
            s.cached_calls.append((seal.slug, generation))
            s.after_cached()
            return m.plans.ordinary.NativeState(generation, *s.host.native_state)

        monkeypatch.setattr(m.normal_read.cached, "_read_probe", cached)
        yield s
    finally:
        session.close()
    assert all(handle.closed for handle in engine.handles)


@pytest.fixture
def cancel(transfer, monkeypatch):
    with owned_session(transfer, monkeypatch) as s:
        s.append("request")
        assert s.session.poll().phase == "stopping_normal"
        assert s.session.poll().phase == "starting_candidate"
        assert s.session.poll().phase == "candidate_idle"
        s.cancel = m.NeverLaunchedHost(s.before, s.session)
        yield s


def finish(s):
    s.append("finish")
    s.session.read = s.cancel.read


def denied(s):
    transfer_tests.pre.bootstrap.launch.denied(s.cancel.read)
    assert s.cancel.failed and not s.cancel.lock.locked()
    transfer_tests.pre.bootstrap.launch.denied(s.cancel.read)


def test_explicit_finish_is_required_before_any_recovery_read(cancel):
    s = cancel
    s.host.reads.clear()
    denied(s)
    assert not s.host.reads and len(s.engine.sent) == 2


def test_passive_cancel_reader_preserves_original_session_and_all_fresh_files(cancel):
    s = cancel
    finish(s)
    original = tuple(s.journal.entries), s.plan.raw, s.plan.deadlines, s.projected.sha256
    descriptors = len(os.listdir("/proc/self/fd"))
    first, second = s.cancel.read(), s.cancel.read()
    assert first.observation.candidate.state == "running"
    assert first.observation.candidate.healthy is first.observation.candidate.recording is None
    assert first.observation.normal.state == "stopped"
    assert first.observation.files.stage == "pristine"
    assert first.observation.sampled_at < second.observation.sampled_at
    # Initial dispatch includes a separate pre-send confirmation read.
    assert len(s.cached_calls) == 2 and s.idle_reads == 0
    assert original == (tuple(s.journal.entries), s.plan.raw, s.plan.deadlines, s.projected.sha256)
    assert descriptors == len(os.listdir("/proc/self/fd"))
    assert len(s.engine.sent) == 2


def test_same_original_session_cancels_idle_and_restores_without_any_recording(cancel):
    s = cancel
    originals = s.session.processes, s.session.dispatch, s.session.executor, s.plan.deadlines
    finish(s)
    assert s.session.poll().phase == "stopping_candidate"
    assert s.session.poll().phase == "starting_normal"
    result = s.session.poll()
    assert result.phase == "complete"
    state = s.journal.machine.state
    assert state.recording_outcome == "not_attempted" and state.artifact_sha256 is None
    assert state.launch_intent_sha256 is state.authorization_generation is None
    assert state.ready_evidence_sha256 is state.operator_exit_sha256 is None
    assert state.files_stage == "pristine" and len(state.exited_processes) == 2
    assert len(state.completed_executions) == 4
    assert s.engine.sent == [
        ("stop", b.NORMAL),
        ("start", b.CANDIDATE),
        ("stop", b.CANDIDATE),
        ("start", b.NORMAL),
    ]
    assert originals == (
        s.session.processes,
        s.session.dispatch,
        s.session.executor,
        s.plan.deadlines,
    )
    assert len(s.cached_calls) == 3
    assert s.cached_calls[-1][1] == state.restored_generation != s.plan.normal_generation
    assert s.cancel.read().observation.files.stage == "pristine"


def test_one_use_continuation_reuses_and_closes_original_session(cancel):
    s = cancel
    s.append("finish")
    waits = []
    result = m.recover_never_launched(s.cancel, waits.append)
    assert result.phase == "complete" and waits == [0.25, 0.25]
    assert s.cancel.recovery_attempted and s.session.processes.closed
    assert len(s.engine.sent) == 4
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    with pytest.raises(m.UnconfirmedHostLaunch):
        m.recover_never_launched(s.cancel, waits.append)
    assert waits == [0.25, 0.25] and len(s.engine.sent) == 4


def test_continuation_without_explicit_finish_cannot_send_or_retry(cancel):
    s = cancel
    with pytest.raises(m.UnconfirmedHostLaunch):
        m.recover_never_launched(s.cancel, lambda _: pytest.fail("Must not wait"))
    assert len(s.engine.sent) == 2 and s.cancel.recovery_attempted
    assert s.cancel.failed and not s.session.processes.closed


@pytest.mark.parametrize("error", [OSError("PRIVATE"), KeyboardInterrupt(), SystemExit(75)])
def test_interrupted_continuation_closes_original_handles_without_retry(cancel, error):
    s = cancel
    s.append("finish")

    def interrupted(seconds):
        assert seconds == 0.25
        raise error

    with pytest.raises(type(error)):
        m.recover_never_launched(s.cancel, interrupted)
    assert s.session.processes.closed and s.cancel.recovery_attempted
    assert len(s.engine.sent) == 3
    assert s.journal.machine.state.phase == "stopping_candidate"
    with pytest.raises(m.UnconfirmedHostLaunch):
        m.recover_never_launched(s.cancel, interrupted)
    assert len(s.engine.sent) == 3


def test_cancellation_before_ready_expiry_can_restore_after_ready_without_renewal(
    cancel, monkeypatch
):
    s = cancel
    original, monotonic = m.plans.clock.read, m.time.monotonic
    elapsed = [119]
    deadlines = s.plan.deadlines

    def shifted():
        value = original()
        amount = elapsed[0] * m.plans.clock.NS
        return replace(
            value,
            before_ns=value.before_ns + amount,
            after_ns=value.after_ns + amount,
            boottime_ns=value.boottime_ns + amount,
        )

    monkeypatch.setattr(m.plans.clock, "read", shifted)
    monkeypatch.setattr(m.time, "monotonic", lambda: monotonic() + elapsed[0])
    finish(s)
    assert s.session.poll().phase == "stopping_candidate"
    elapsed[0] = 121
    assert s.session.poll().phase == "starting_normal"
    assert s.session.poll().phase == "complete"
    assert s.plan.deadlines is deadlines
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert len(s.engine.sent) == 4


def test_foreign_thread_refuses_before_any_host_read(cancel):
    s = cancel
    finish(s)
    errors = []
    s.host.reads.clear()

    def read():
        try:
            s.cancel.read()
        except m.UnconfirmedHostLaunch as error:
            errors.append(str(error))

    worker = Thread(target=read)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and errors == [m.MESSAGE]
    denied(s)
    assert not s.host.reads and len(s.engine.sent) == 2


@pytest.mark.parametrize("fault", ["options", "normal_files", "candidate_files", "jobs"])
def test_fresh_protected_changes_are_forwarded_not_hidden(cancel, fault):
    s = cancel
    finish(s)
    if fault == "options":
        s.host.private_options["normal"] = "PRIVATE changed"
    elif fault == "normal_files":
        s.host.files[b.NORMAL] = replace(s.host.files[b.NORMAL], recordings="e" * 64)
    elif fault == "candidate_files":
        s.static = replace(s.static, package="f" * 64)
    else:
        s.host.jobs = False
    obs = s.cancel.read().observation
    if fault in ("options", "normal_files"):
        assert obs.normal.pin != s.plan.normal.pin
    elif fault == "candidate_files":
        assert obs.candidate.pin != s.plan.candidate.pin
    else:
        assert obs.jobs_idle is False
    assert len(s.engine.sent) == 2


@pytest.mark.parametrize("fault", ["journal", "late", "reentrant"])
def test_changes_during_restored_normal_read_are_not_published(cancel, monkeypatch, fault):
    s = cancel
    finish(s)
    s.session.poll()
    s.session.poll()

    def changed():
        if fault == "journal":
            (s.journal.path / s.journal.name(0)).chmod(0o644)
        elif fault == "late":
            monkeypatch.setattr(m.time, "monotonic", lambda: s.cancel.end + 1)
        else:
            transfer_tests.pre.bootstrap.launch.denied(s.cancel.read)

    s.after_cached = changed
    denied(s)
    assert len(s.engine.sent) == 4


@pytest.mark.parametrize("fault", ["missing_exit", "lost_stop_reply", "missing_cli_exit"])
def test_uncertain_stop_never_retries_or_invents_exit(cancel, fault):
    s = cancel
    finish(s)
    if fault == "missing_exit":
        s.engine.omit_exit = True
    elif fault == "lost_stop_reply":
        s.engine.start_error = OSError("PRIVATE_LOST_STOP_REPLY")
    result = s.session.poll()
    assert result.phase == "stopping_candidate"
    assert result.outcome == (
        "dispatch_unconfirmed" if fault == "lost_stop_reply" else "dispatch_submitted"
    )
    s.engine.start_error = None
    if fault == "missing_cli_exit":
        s.engine.execs[s.journal.machine.state.executions[-1][2]].update(
            Running=True, ExitCode=None, Pid=4321
        )
    if fault == "lost_stop_reply":
        assert s.session.poll().phase == "starting_normal"
        assert s.session.poll().phase == "complete"
        assert len(s.engine.sent) == 4
    else:
        for _ in range(2):
            assert s.session.poll().phase == "stopping_candidate"
        assert len(s.engine.sent) == 3
    assert s.engine.sent.count(("stop", b.CANDIDATE)) == 1


def test_operator_authorization_cannot_be_reinterpreted_as_never_launched(cancel):
    s = cancel
    sample = s.before.read()
    s.append(
        "authorize_operator",
        generation=s.journal.machine.state.candidate_generation,
        bootstrap_sha256=s.plan.bootstrap.sha256,
        launch_plan_sha256="d" * 64,
        idle_evidence_sha256="e" * 64,
        observation=asdict(sample.observation),
    )
    finish(s)
    s.host.reads.clear()
    denied(s)
    assert not s.host.reads and len(s.engine.sent) == 2


@pytest.mark.parametrize(
    "fault",
    ["old_recording", "new_recording", "journal", "candidate_generation", "candidate_absent"],
)
def test_changed_protected_state_never_qualifies_cancel(cancel, fault):
    s = cancel
    finish(s)
    if fault == "old_recording":
        (s.recording_root / s.projected.host.baseline.files[0][0]).write_bytes(b"PRIVATE")
    elif fault == "new_recording":
        (s.recording_root / "new.wav").write_bytes(b"PRIVATE")
    elif fault == "journal":
        (s.journal.path / s.journal.name(0)).chmod(0o644)
    elif fault == "candidate_generation":
        s.host.values["app_" + b.CANDIDATE]["State"]["Pid"] += 1
    else:
        s.host.values.pop("app_" + b.CANDIDATE)
    denied(s)
    assert len(s.engine.sent) == 2


@pytest.mark.parametrize(
    "fault", ["old_normal", "different_normal", "candidate_reappeared", "cache_failure"]
)
def test_restoration_uncertainty_is_not_promoted_to_success(cancel, fault, monkeypatch):
    s = cancel
    finish(s)
    assert s.session.poll().phase == "stopping_candidate"
    assert s.session.poll().phase == "starting_normal"
    if fault == "old_normal":
        value = audio.ot.recovery_tests.container("app_" + b.NORMAL, 3)
        value.update(audio.container_network(False))
        s.host.values["app_" + b.NORMAL] = value
    elif fault == "different_normal":
        s.cancel.read()
        s.append(
            "observe",
            observation=asdict(
                replace(
                    s.cancel.read().observation,
                    normal=replace(s.cancel.read().observation.normal, healthy=None),
                )
            ),
        )
        s.host.values["app_" + b.NORMAL]["State"]["Pid"] += 1
    elif fault == "candidate_reappeared":
        transfer_tests.candidate(s)
    else:

        def failed(*args):
            raise OSError("PRIVATE_CACHE_ERROR")

        monkeypatch.setattr(m.normal_read.cached, "_read_probe", failed)
    denied(s)
    assert len(s.engine.sent) == 4


@pytest.mark.parametrize("healthy,recording", [(None, None), (False, False), (True, True)])
def test_restored_native_state_remains_truthful(cancel, healthy, recording):
    s = cancel
    finish(s)
    s.session.poll()
    s.session.poll()
    s.host.native_state = healthy, recording
    sample = s.cancel.read()
    assert (sample.observation.normal.healthy, sample.observation.normal.recording) == (
        healthy,
        recording,
    )
    assert s.journal.machine.state.phase == "starting_normal"


@pytest.mark.parametrize(
    "fault", ["plan", "session", "processes", "dispatch", "executor", "witness", "closed", "owner"]
)
def test_original_inputs_and_retained_session_cannot_be_replaced(cancel, fault):
    s = cancel
    finish(s)
    if fault == "plan":
        s.cancel.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
    elif fault == "closed":
        s.session.close()
    elif fault == "owner":
        s.cancel.owner = (-1, -1)
    elif fault == "witness":
        old = s.session.processes.witnesses[b.CANDIDATE]
        s.session.processes.witnesses[b.CANDIDATE] = s.engine.witness(old.identity)
        old.close()
    else:
        setattr(s.cancel, fault, object())
    s.host.reads.clear()
    denied(s)
    assert not s.host.reads and len(s.engine.sent) == 2


def test_changed_files_during_normal_cache_read_are_not_published(cancel):
    s = cancel
    finish(s)
    s.session.poll()
    s.session.poll()
    s.after_cached = lambda: (s.recording_root / "unplanned.wav").write_bytes(b"PRIVATE")
    denied(s)


def test_unchanged_launch_history_keeps_the_stricter_original_stop_bound(cancel, monkeypatch):
    s = cancel
    future = s.plan.lease["stop_by"] + 1
    monkeypatch.setattr(m.time, "monotonic", lambda: future)
    with pytest.raises(m.UnconfirmedHostLaunch, match=m.MESSAGE):
        m._verified_history(s.plan, s.projected, s.journal, future + 1)


def test_original_recovery_deadline_is_not_renewed(cancel, monkeypatch):
    s = cancel
    finish(s)
    original = m.plans.clock.read

    def expired():
        value = original()
        shift = 1501 * m.plans.clock.NS
        return replace(
            value,
            before_ns=value.before_ns + shift,
            after_ns=value.after_ns + shift,
            boottime_ns=value.boottime_ns + shift,
        )

    monkeypatch.setattr(m.plans.clock, "read", expired)
    denied(s)
    assert len(s.engine.sent) == 2
