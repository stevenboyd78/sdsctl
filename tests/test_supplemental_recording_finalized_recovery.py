"""Original journal/session continuation; explicitly synthetic host and exits.

Exercises the actual policy, dispatch tracking, finalized/restored read routing
and recovery loop together. This is not installed service or hardware evidence.
"""

from contextlib import suppress
from dataclasses import replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_restored_host as restored

m = restored.m
layout, tree, routing, projection, binding, directory, prepared, setup, joined, begun, completed = (
    restored.layout,
    restored.tree,
    restored.routing,
    restored.projection,
    restored.binding,
    restored.directory,
    restored.prepared,
    restored.setup,
    restored.joined,
    restored.begun,
    restored.completed,
)
host = restored.host
h = restored.dispatch_tests.h


@pytest.fixture
def cycling(host, monkeypatch):
    c = host
    c.normal = m.base.App(c.s.plan.normal.pin, "stopped")
    c.candidate = m.base.App(c.s.plan.candidate.pin, "running", c.s.run.pins.generation)
    c.stop_return_lost = c.inspect_lost = False
    c.created, c.started, c.waits, c.samples, c.native_calls = [], [], [], [], []
    c.execs, c.observers = {}, []
    c.native_values = True, False
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
            StartedAt="2026-09-24T00:00:00Z",
            FinishedAt="2026-09-24T00:01:00Z",
        ),
    )
    docker = c.s.run.read.docker
    cli = {"Id": "a" * 64}
    monkeypatch.setattr(
        docker, "container", lambda name: cli if name == h.CLI else c.candidate_container
    )
    original_generation = h.generation
    monkeypatch.setattr(
        h,
        "generation",
        lambda value, *, name, image: (
            c.s.plan.cli_generation
            if name == h.CLI
            else original_generation(value, name=name, image=image)
        ),
    )

    def create(cid, command):
        assert cid == cli["Id"]
        eid = str(len(c.created) + 3) * 64
        c.created.append(command)
        c.execs[eid] = restored.dispatch_tests.execution(command) | dict(
            ID=eid,
            ContainerID=cid,
            Running=False,
            ExitCode=None,
            Pid=0,
        )
        return eid

    def start(eid):
        c.started.append(eid)
        c.execs[eid].update(Running=False, ExitCode=0, Pid=321)
        if c.created[-1] == h.CONTROL["stopping_candidate"]:
            c.candidate = m.base.App(c.s.plan.candidate.pin, "stopped")
            if c.stop_return_lost:
                raise OSError("PRIVATE lost stop return")
        else:
            assert c.created[-1] == h.CONTROL["starting_normal"]
            c.normal = m.base.App(c.s.plan.normal.pin, "running", "e" * 64)

    def inspect(eid):
        if c.inspect_lost and c.started:
            raise OSError("PRIVATE unavailable inspection")
        return c.execs[eid].copy()

    monkeypatch.setattr(docker, "create_execution", create)
    monkeypatch.setattr(docker, "start_execution", start)
    monkeypatch.setattr(docker, "inspect_execution", inspect)
    processes = m.TrackedProcesses(
        c.s.journal,
        docker,
        images={m.base.NORMAL: c.s.plan.normal.image, m.base.CANDIDATE: c.s.plan.candidate.image},
        read_clock=lambda: (c.s.plan.boot, c.operator._clock()),
    )

    def reconcile():
        # Explicit synthetic init-exit evidence, not a native process claim.
        if c.candidate.state == "stopped" and not processes.exit_confirmed(m.base.CANDIDATE):
            restored.event(c, "process_exited", generation=c.s.run.pins.generation)

    def bind(slug, generation):
        record = processes.record(slug)
        assert slug == m.base.CANDIDATE and generation == record.generation
        return record

    monkeypatch.setattr(processes, "reconcile", reconcile)
    monkeypatch.setattr(processes, "bind_running", bind)
    dispatch = m.TrackedDispatch(
        c.s.journal,
        docker,
        cli_image=c.s.plan.cli_image,
        cli_generation=c.s.plan.cli_generation,
        now=c.operator._clock,
    )
    c.session = m.launch.bootstrap.RecoverySession(c.s.journal, processes, dispatch, c.host.read)
    original_init = m.FinalizedHost.__init__

    def init(observer, reader):
        original_init(observer, reader)
        c.observers.append(observer)

    monkeypatch.setattr(m.FinalizedHost, "__init__", init)

    def native(docker, seal, command, generation):
        assert seal is c.s.plan.normal and c.s.journal.machine.state.phase == "starting_normal"
        c.native_calls.append(generation)
        return m.plans.ordinary.NativeState(generation, *c.native_values)

    monkeypatch.setattr(m.normal_read.cached, "_read_probe", native)

    def metadata(observer):
        boot, began_at = observer.read_clock()
        normal, candidate = c.normal, c.candidate
        if normal.state == "running":
            state = m.plans.ordinary.NativeState(normal.generation, None, None)
            with suppress(Exception):
                state = observer.read_native(m.base.NORMAL, normal.generation)
            normal = replace(normal, healthy=state.healthy, recording=state.recording)
        c.samples.append((c.s.journal.machine.state.phase, normal.healthy))
        return m._StaticHostSnapshot(
            boot,
            began_at,
            observer.read_clock()[1],
            normal,
            candidate,
            True,
            True,
        )

    monkeypatch.setattr(m._StaticHostObserver, "read", metadata)

    def wait(seconds):
        assert seconds == 0.25
        c.waits.append(c.s.journal.machine.state.phase)
        assert len(c.waits) < 5, "Synthetic loop must settle without real waiting"

    c.wait = wait
    yield c
    c.session.close()


def test_original_session_drives_stop_restore_and_health_without_new_authority(cycling):
    c = cycling
    original = c.s.plan.raw, c.s.start.intent, c.reader.publication
    deadline = c.s.journal.machine.hard_deadline
    objects = c.session.executor, c.session.processes, c.session.dispatch
    result = m.recover_finalized(c.reader, c.session, c.wait)
    assert result.phase == "complete"
    assert c.created == [h.CONTROL["stopping_candidate"], h.CONTROL["starting_normal"]]
    assert len(c.started) == 2 and len(set(c.started)) == 2
    assert c.waits == ["stopping_candidate", "starting_normal"]
    assert c.native_calls == ["e" * 64]
    assert c.s.journal.machine.state.recording_outcome == "verified"
    assert c.s.journal.machine.hard_deadline == deadline
    assert original == (c.s.plan.raw, c.s.start.intent, c.reader.publication)
    assert objects == (c.session.executor, c.session.processes, c.session.dispatch)
    assert c.session.processes.closed and all(o.closed for o in c.observers)
    assert not c.reader.closed and not c.operator.closed and c.s.journal.fd >= 0
    assert c.reader.recovery_attempted
    count = len(c.created)
    with pytest.raises(m.UnconfirmedHostBegin):
        m.recover_finalized(c.reader, c.session, c.wait)
    assert len(c.created) == count


def test_lost_stop_return_is_inspected_not_reissued(cycling):
    c = cycling
    c.stop_return_lost = True
    assert m.recover_finalized(c.reader, c.session, c.wait).phase == "complete"
    assert c.created == [h.CONTROL["stopping_candidate"], h.CONTROL["starting_normal"]]
    assert len(c.started) == 2


def expire(c):
    limit = c.s.journal.machine.hard_deadline
    c.session.processes.read_clock = lambda: (c.s.plan.boot, limit)


def test_lost_inspection_never_replays_stop_or_starts_normal(cycling):
    c = cycling
    c.inspect_lost = True
    c.wait = lambda _: expire(c)
    result = m.recover_finalized(c.reader, c.session, c.wait)
    assert result.phase == "review"
    assert c.created == [h.CONTROL["stopping_candidate"]]
    assert not c.native_calls


def test_file_failure_never_falls_back_and_clock_still_expires(cycling):
    c = cycling
    c.reader.files.bad_files = True
    c.wait = lambda _: expire(c)
    result = m.recover_finalized(c.reader, c.session, c.wait)
    assert result.phase == "review" and not c.created and not c.native_calls
    assert c.reader.failed and c.trace.count("files") == 1
    assert not c.operator.closed and c.s.journal.machine.state.recording_outcome == "unconfirmed"


def test_file_failure_after_shutdown_never_authorizes_normal_start(cycling):
    c = cycling

    def wait(seconds):
        c.wait(seconds)
        if len(c.waits) == 1:
            c.reader.files.bad_files = True
        else:
            expire(c)

    assert m.recover_finalized(c.reader, c.session, wait).phase == "review"
    assert c.created == [h.CONTROL["stopping_candidate"]]
    assert c.s.journal.machine.process_bound(m.base.CANDIDATE, exited=True)
    assert c.reader.failed and not c.native_calls


def test_router_selection_and_file_read_share_one_two_second_budget(cycling, monkeypatch):
    c = cycling
    now, offset = m.time.monotonic, [0]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: now() + offset[0]))
    original = m.FinalizedHost.read

    def delayed(observer):
        sample = original(observer)
        offset[0] += 2
        return sample

    monkeypatch.setattr(m.FinalizedHost, "read", delayed)
    c.wait = lambda _: expire(c)
    assert m.recover_finalized(c.reader, c.session, c.wait).phase == "review"
    assert not c.created and not c.native_calls
    assert not c.reader.failed  # Inner read passed; its outer time budget did not.


def test_observer_route_mutation_is_rejected_before_policy_observation(cycling, monkeypatch):
    c = cycling
    original = m.FinalizedHost.read

    def changed(observer):
        sample = original(observer)
        c.session.dispatch = object()
        return sample

    monkeypatch.setattr(m.FinalizedHost, "read", changed)
    with pytest.raises(m.UnconfirmedHostBegin):
        m.recover_finalized(c.reader, c.session, c.wait)
    assert not c.created and c.session.processes.closed
    assert not any(
        e["event"]["kind"] == "observe" for e in c.s.journal.entries[len(c.reader.history) + 1 :]
    )


@pytest.mark.parametrize("values", [(False, False), (None, False), (True, True)])
def test_restoration_requires_fresh_healthy_nonrecording_normal(cycling, values):
    c = cycling
    c.native_values = values

    def wait(seconds):
        c.wait(seconds)
        if c.native_calls:
            expire(c)

    result = m.recover_finalized(c.reader, c.session, wait)
    assert result.phase == "review"
    # The policy pins an observed new generation before health is confirmed;
    # only complete/restored is the successful restoration verdict.
    assert c.s.journal.machine.state.reason != "restored"


@pytest.mark.parametrize(
    "fault",
    ["process_docker", "dispatch_docker", "images", "cli", "journal", "executor", "operator"],
)
def test_foreign_bridge_is_rejected_before_any_read_or_dispatch(cycling, fault):
    c = cycling
    if fault == "process_docker":
        c.session.processes.docker = m.plans.ordinary.Docker()
    elif fault == "dispatch_docker":
        c.session.dispatch.docker = m.plans.ordinary.Docker()
    elif fault == "images":
        c.session.processes.images[m.base.NORMAL] = "sha256:" + "a" * 64
    elif fault == "cli":
        c.session.dispatch.generation = "a" * 64
    elif fault == "journal":
        c.session.journal = object()
    elif fault == "executor":
        c.session.executor.send = lambda *args: None
    else:
        c.session.consume_operator = lambda: True
    before = tuple(c.s.journal.entries)
    with pytest.raises(m.UnconfirmedHostBegin):
        m.recover_finalized(c.reader, c.session, c.wait)
    assert tuple(c.s.journal.entries) == before
    assert not c.created and not c.samples and not c.observers
    assert c.reader.recovery_attempted and not c.reader.closed and not c.operator.closed


def test_changed_route_during_wait_cannot_continue_or_restore_old_reader(cycling):
    c = cycling

    def wait(_):
        c.session.read = lambda: pytest.fail("Replacement route must not run")

    with pytest.raises(m.UnconfirmedHostBegin):
        m.recover_finalized(c.reader, c.session, wait)
    assert c.created == [h.CONTROL["stopping_candidate"]]
    assert c.session.processes.closed and all(o.closed for o in c.observers)
    assert not c.reader.closed and not c.operator.closed


def test_foreign_thread_cannot_consume_original_recovery(cycling):
    c = cycling
    errors = []

    def run():
        try:
            m.recover_finalized(c.reader, c.session, c.wait)
        except Exception as error:
            errors.append(error)

    thread = Thread(target=run)
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and len(errors) == 1
    assert type(errors[0]) is m.UnconfirmedHostBegin
    assert not c.reader.recovery_attempted and not c.created
