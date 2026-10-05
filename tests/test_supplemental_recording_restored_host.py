"""Real original authorization, replay and normal reader; synthetic host/IPC.

No installed source/runtime, actual restoration, scanner or listening claim.
The existing full observer and fixed probe have separate transport tests.
"""

from contextlib import suppress
from dataclasses import asdict, replace
from threading import Thread

import pytest

from . import test_supplemental_handoff_host as dispatch_tests
from . import test_supplemental_handoff_recovery as recovery_tests
from . import test_supplemental_recording_finalized_host as finalized

m, authority = finalized.m, finalized.authority
layout, tree, routing, projection, binding, directory, prepared, setup, joined, begun, completed = (
    finalized.layout,
    finalized.tree,
    finalized.routing,
    finalized.projection,
    finalized.binding,
    finalized.directory,
    finalized.prepared,
    finalized.setup,
    finalized.joined,
    finalized.begun,
    finalized.completed,
)
host = finalized.host


def event(c, kind, **fields):
    return c.s.journal.append(
        dict(kind=kind, now=c.operator._clock(), boot_id=c.s.plan.boot, **fields)
    )


def observation(c, normal=None):
    return m.launch.bootstrap.recording.Observation(
        c.operator._clock(),
        normal or m.base.App(c.s.plan.normal.pin, "stopped"),
        m.base.App(c.s.plan.candidate.pin, "stopped"),
        True,
        True,
        True,
        c.collected.files,
    )


@pytest.fixture
def restoring(host, monkeypatch):
    c = host
    # Explicit synthetic independent init receipt; real durable policy transition.
    event(c, "process_exited", generation=c.s.run.pins.generation)
    action = event(c, "observe", observation=asdict(observation(c)))
    assert (action.operation, action.slug) == ("start", m.base.NORMAL)
    assert c.s.journal.machine.state.phase == "starting_normal"
    c.host = m.RestoredHost(c.reader)
    c.normal = m.base.App(c.s.plan.normal.pin, "running", "e" * 64)
    c.candidate = m.base.App(c.s.plan.candidate.pin, "stopped")
    c.native_values = (True, False)
    c.native_calls, c.readers = [], []
    c.after_native = lambda: None
    c.after_files = lambda: None
    c.candidate_container = dict(
        Id=c.s.run.pins.init.container_id,
        Name="/app_" + m.base.CANDIDATE,
        Image=c.s.plan.candidate.image,
        HostConfig=dict(RestartPolicy=dict(Name="no", MaximumRetryCount=0)),
        State=dict(
            Status="exited",
            Running=False,
            Paused=False,
            Restarting=False,
            Dead=False,
            Pid=0,
            ExitCode=75,
            OOMKilled=False,
            Error="",
            StartedAt="2026-09-23T00:00:00Z",
            FinishedAt="2026-09-23T00:01:00Z",
        ),
    )
    monkeypatch.setattr(c.host.docker, "container", lambda _: c.candidate_container)

    def native(docker, seal, command, generation):
        assert docker is c.host.docker and seal is c.s.plan.normal
        assert command == c.host.normal_reader.command
        assert c.s.journal.machine.state.phase in ("starting_normal", "complete")
        c.native_calls.append((seal.slug, generation))
        c.readers.append(c.host.normal_reader)
        result = m.plans.ordinary.NativeState(generation, *c.native_values)
        c.after_native()
        return result

    monkeypatch.setattr(m.normal_read.cached, "_read_probe", native)

    def read(observer):
        # Match the real observer's generation/unknown-status handling. Original
        # collector routing and all metadata gates are tested in its own suite.
        boot, began_at = observer.read_clock()
        normal, candidate = c.normal, c.candidate
        if normal.state == "running" and normal.pin == c.s.plan.normal.pin:
            state = m.plans.ordinary.NativeState(normal.generation, None, None)
            with suppress(Exception):
                state = observer.read_native(m.base.NORMAL, normal.generation)
            normal = replace(normal, healthy=state.healthy, recording=state.recording)
        if candidate.state == "running":
            state = observer.read_native(m.base.CANDIDATE, candidate.generation)
            candidate = replace(candidate, healthy=state.healthy, recording=state.recording)
        return m._StaticHostSnapshot(
            boot, began_at, observer.read_clock()[1], normal, candidate, True, True
        )

    monkeypatch.setattr(m._StaticHostObserver, "read", read)
    original = c.reader.read

    def files():
        result = original()
        c.after_files()
        return result

    monkeypatch.setattr(c.reader, "read", files)
    return c


def denied(c):
    authority.history.begins.denied(c.host.read)
    assert c.host.failed and not c.operator.closed and c.s.journal.fd >= 0


def test_fresh_restored_health_and_original_files_do_not_publish_completion(restoring):
    c = restoring
    original = c.s.plan.raw, tuple(c.s.journal.entries), c.s.journal.machine.state
    for _ in range(2):
        sample = c.host.read()
        assert sample.observation.files is c.collected.files
        assert sample.observation.normal == replace(c.normal, healthy=True, recording=False)
        assert sample.observation.candidate == c.candidate
        assert sample.boot_id == c.s.plan.boot
        assert sample.observation.sampled_at <= sample.now
    assert len(c.native_calls) == 2
    assert c.readers[0] is not c.readers[1]
    assert all(reader.used and not reader.failed for reader in c.readers)
    assert original == (c.s.plan.raw, tuple(c.s.journal.entries), c.s.journal.machine.state)


def test_original_policy_still_requires_tracked_normal_start_exit(restoring):
    c = restoring
    sample = c.host.read()
    event(c, "observe", observation=asdict(sample.observation))
    assert c.s.journal.machine.state.phase == "starting_normal"
    event(c, "bind_execution", container_id="a" * 64, execution_id="3" * 64)
    sample = c.host.read()
    event(c, "observe", observation=asdict(sample.observation))
    assert c.s.journal.machine.state.phase == "starting_normal"
    event(c, "execution_completed", execution_id="3" * 64, exit_code=0)
    sample = c.host.read()
    event(c, "observe", observation=asdict(sample.observation))
    assert c.s.journal.machine.state.phase == "complete"
    assert c.s.journal.machine.state.reason == "restored"
    assert c.s.journal.machine.state.recording_outcome == "verified"
    assert c.host.read().observation.normal.healthy is True


@pytest.mark.parametrize("healthy,recording", [(None, False), (False, False), (True, True)])
def test_unhealthy_unknown_or_recording_normal_is_not_promoted(restoring, healthy, recording):
    c = restoring
    c.native_values = healthy, recording
    sample = c.host.read()
    assert (sample.observation.normal.healthy, sample.observation.normal.recording) == (
        healthy,
        recording,
    )
    event(c, "observe", observation=asdict(sample.observation))
    assert c.s.journal.machine.state.phase == ("review" if recording else "starting_normal")


def test_not_started_normal_remains_stopped_without_a_probe(restoring):
    c = restoring
    c.normal = m.base.App(c.normal.pin, "stopped")
    assert c.host.read().observation.normal == c.normal
    assert not c.native_calls and not c.host.normal_reader.used


def test_no_native_probe_before_original_restoration_intent(host, monkeypatch):
    c = host
    c.host = m.RestoredHost(c.reader)
    calls = []
    monkeypatch.setattr(m.normal_read.cached, "_read_probe", lambda *a: calls.append(a))
    denied(c)
    assert not calls


@pytest.mark.parametrize("fault", ["pin", "original_generation", "changed_restored_generation"])
def test_changed_normal_identity_is_visible_but_never_probed(restoring, fault):
    c = restoring
    if fault == "pin":
        c.normal = replace(c.normal, pin="a" * 64)
    elif fault == "original_generation":
        c.normal = replace(c.normal, generation=c.s.plan.normal_generation)
    else:
        event(c, "observe", observation=asdict(observation(c, replace(c.normal, healthy=None))))
        c.normal = replace(c.normal, generation="b" * 64)
    sample = c.host.read()
    assert sample.observation.normal == c.normal
    assert not c.native_calls
    event(c, "observe", observation=asdict(sample.observation))
    assert c.s.journal.machine.state.phase == "review"


@pytest.mark.parametrize("fault", ["running", "unknown"])
def test_candidate_reappearance_never_launches_candidate_or_normal_probe(restoring, fault):
    c = restoring
    c.candidate = m.base.App(
        c.candidate.pin, fault, c.s.run.pins.generation if fault == "running" else None
    )
    c.candidate_container["State"].update(Status="running", Pid=123, Running=True)
    sample = c.host.read()
    assert sample.observation.candidate == c.candidate
    assert sample.observation.normal.healthy is None
    assert not c.native_calls


@pytest.mark.parametrize("fault", ["before", "after"])
def test_same_name_candidate_replacement_refuses_without_closing_custody(restoring, fault):
    c = restoring

    def replace_candidate():
        c.candidate_container["Id"] = "f" * 64

    if fault == "before":
        replace_candidate()
    else:
        c.after_native = replace_candidate
    denied(c)
    assert len(c.native_calls) == (fault == "after")


def test_failed_probe_does_not_reuse_previous_healthy_result(restoring):
    c = restoring
    assert c.host.read().observation.normal.healthy is True

    def lost():
        raise TimeoutError("PRIVATE cached reply lost")

    c.after_native = lost
    sample = c.host.read()
    assert sample.observation.normal.healthy is None
    assert sample.observation.normal.recording is None
    assert c.readers[-1].failed and c.readers[-1] is not c.readers[0]
    assert not c.host.failed


@pytest.mark.parametrize("where", ["native", "files"])
@pytest.mark.parametrize("fault", ["journal", "late", "closed"])
def test_mid_read_context_change_never_returns_a_restored_sample(
    restoring, monkeypatch, where, fault
):
    c = restoring

    def change():
        if fault == "journal":
            event(c, "bind_execution", container_id="a" * 64, execution_id="3" * 64)
        elif fault == "late":
            authority.history.continuity.advance(monkeypatch, 3)
        else:
            c.host.close()

    setattr(c, "after_" + where, change)
    denied(c)


def test_mutated_non_durable_phase_cannot_authorize_probe(restoring):
    c = restoring
    c.s.journal.machine.state = replace(c.s.journal.machine.state, restored_generation="f" * 64)
    denied(c)
    assert not c.native_calls


def test_borrowed_reader_close_refuses_without_new_probe(restoring):
    c = restoring
    c.reader.close()
    denied(c)
    assert not c.native_calls


def test_foreign_thread_cannot_start_normal_cache_probe(restoring):
    c, errors = restoring, []

    def read():
        try:
            c.host.read()
        except m.UnconfirmedHostBegin:
            errors.append(True)

    thread = Thread(target=read)
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and errors == [True]
    assert not c.native_calls and not c.reader.closed and not c.operator.closed


def test_close_retires_only_composition_and_preserves_original_files(restoring):
    c = restoring
    c.host.close()
    c.host.close()
    denied(c)
    assert c.reader.read() is c.collected
    assert not c.reader.closed and not c.s.start.closed


def test_native_result_must_match_complete_snapshot(restoring, monkeypatch):
    c = restoring
    original = m._StaticHostObserver.read

    def changed(observer):
        result = original(observer)
        return replace(result, normal=replace(result.normal, generation="b" * 64))

    monkeypatch.setattr(m._StaticHostObserver, "read", changed)
    denied(c)


def test_original_static_collector_still_has_no_native_flags(restoring):
    c = restoring
    c.host = m.FinalizedHost(c.reader)
    sample = c.host.read()
    assert sample.observation.normal.healthy is None
    assert sample.observation.normal.recording is None
    assert not c.native_calls


@pytest.fixture
def recovering(restoring, monkeypatch):
    """Actual recovery bridge and tracking, explicitly synthetic Engine return."""
    c = restoring
    event(c, "bind_execution", container_id="a" * 64, execution_id="3" * 64)
    command = dispatch_tests.h.CONTROL["starting_normal"]
    c.execution = dispatch_tests.execution(command) | dict(
        ID="3" * 64, ContainerID="a" * 64, Running=True, ExitCode=None, Pid=321
    )
    c.inspections, c.commands = [], []

    def inspect(execution):
        assert execution == "3" * 64
        c.inspections.append(execution)
        return c.execution.copy()

    def no_dispatch(*args, **kwargs):
        c.commands.append((args, kwargs))
        pytest.fail("A previously recorded restoration command must never be replayed")

    monkeypatch.setattr(c.host.docker, "inspect_execution", inspect)
    monkeypatch.setattr(c.host.docker, "create_execution", no_dispatch)
    monkeypatch.setattr(c.host.docker, "start_execution", no_dispatch)
    processes = recovery_tests.r.TrackedProcesses(
        c.s.journal,
        c.host.docker,
        images={m.base.NORMAL: c.s.plan.normal.image, m.base.CANDIDATE: c.s.plan.candidate.image},
        read_clock=lambda: (c.s.plan.boot, c.operator._clock()),
    )
    dispatch = dispatch_tests.h.TrackedDispatch(
        c.s.journal,
        c.host.docker,
        cli_image=c.s.plan.cli_image,
        cli_generation=c.s.plan.cli_generation,
        now=c.operator._clock,
    )
    c.session = m.launch.bootstrap.RecoverySession(c.s.journal, processes, dispatch, c.host.read)
    try:
        yield c
    finally:
        c.session.close()


def test_actual_recovery_bridge_waits_for_exit_then_completes_without_dispatch(recovering):
    c = recovering
    deadline = c.s.journal.machine.hard_deadline
    first = c.session.poll()
    assert first.phase == "starting_normal" and first.outcome == "observed"
    assert c.native_calls and c.s.journal.machine.state.restored_generation is None
    c.execution.update(Running=False, ExitCode=0, Pid=0)
    final = c.session.poll()
    assert final.phase == "complete" and final.outcome == "observed"
    assert ("3" * 64, 0) in c.s.journal.machine.state.completed_executions
    assert c.s.journal.machine.state.restored_generation == c.normal.generation
    assert c.s.journal.machine.state.recording_outcome == "verified"
    assert c.s.journal.machine.hard_deadline == deadline
    assert c.session.poll().outcome == "terminal"
    assert not c.commands


def test_actual_recovery_bridge_keeps_lost_exec_unknown_without_replay(recovering, monkeypatch):
    c = recovering

    def lost(_):
        raise TimeoutError("PRIVATE lost Engine inspection")

    monkeypatch.setattr(c.host.docker, "inspect_execution", lost)
    for _ in range(2):
        result = c.session.poll()
        assert result.phase == "starting_normal"
        assert c.s.journal.machine.state.restored_generation is None
    assert not c.commands
    assert not any(eid == "3" * 64 for eid, _ in c.s.journal.machine.state.completed_executions)


def test_actual_recovery_bridge_clock_expires_even_after_file_failure(recovering):
    c = recovering
    c.reader.files.bad_files = True
    result = c.session.poll()
    assert result.outcome == "observation_unavailable"
    assert result.phase == "starting_normal" and c.reader.failed and c.host.failed
    assert not c.operator.closed and c.s.journal.fd >= 0
    # This clock-only tick must not invoke the failed file reader or promote
    # the recording. It proves no shutdown itself; native/outer timers remain
    # independently responsible for stopping their exact original processes.
    limit = c.s.journal.machine.state.deadline
    c.session.processes.read_clock = lambda: (c.s.plan.boot, limit)
    count = len(c.native_calls)
    result = c.session.poll()
    assert result.phase == "review" and result.outcome == "terminal"
    assert c.s.journal.machine.state.reason == "phase_deadline"
    assert len(c.native_calls) == count and not c.commands
    assert not c.operator.closed


@pytest.mark.parametrize("state", [(None, False), (False, False), (True, True)])
def test_actual_recovery_bridge_does_not_infer_health_from_cli_exit(recovering, state):
    c = recovering
    c.execution.update(Running=False, ExitCode=0, Pid=0)
    c.native_values = state
    result = c.session.poll()
    assert result.phase == ("review" if state[1] else "starting_normal")
    assert ("3" * 64, 0) in c.s.journal.machine.state.completed_executions
    assert not c.commands
