"""Original real exit/file evidence and recovery policy; fixture HA/Engine facts.

The Launch/metadata shell and CLI replies are synthetic. This is local join
coverage, not qualification of an installed recovery service or native success.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_finalized_recovery as success
from . import test_supplemental_recording_preserved as files

m, h = success.m, success.h
layout, tree, routing, projection, binding, prepared, family, actors, calibration = (
    files.layout,
    files.tree,
    files.routing,
    files.projection,
    files.binding,
    files.prepared,
    files.family,
    files.actors,
    files.calibration,
)
directory, plan, journal, failure = files.directory, files.plan, files.journal, files.failure


@pytest.fixture
def bridge(failure, monkeypatch):
    yield from setup_bridge(failure, monkeypatch, files.reader(failure))


def setup_bridge(c, monkeypatch, reader):
    """Shared synthetic platform only; caller supplies its actual distinct reader."""
    c.reader = reader
    c.normal = m.base.App(c.plan.normal.pin, "stopped")
    c.candidate = m.base.App(c.plan.candidate.pin, "stopped")
    c.native_values = True, False
    c.created, c.started, c.native_calls, c.samples, c.execs, c.waits = [], [], [], [], {}, []
    c.lost_return = c.lost_inspection = False
    docker = m.plans.ordinary.Docker()
    # Explicit synthetic launch shell; it carries the original Plan/Pins/Journal
    # that the actual retained Operator and file reader independently validate.
    run = object.__new__(m.launch.Launch)
    run.owner, run.used, run.confirm_attempted = c.operator.owner, True, True
    run.failed, run.closed = True, False
    run.plan, run.pins, run.journal = c.plan, c.operator.pins, c.journal
    run.projected = c.operator.pins.host.projection
    run.read = object.__new__(m.launch.BootstrapHost)
    run.qualify = object.__new__(m.launch.CandidateQualification)
    run.read.docker = run.qualify.docker = docker
    c.run = run
    c.candidate_container = dict(
        Id=run.pins.init.container_id,
        Name="/app_" + m.base.CANDIDATE,
        Image=c.plan.candidate.image,
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
    cli = {"Id": "a" * 64}
    monkeypatch.setattr(
        docker, "container", lambda name: cli if name == h.CLI else c.candidate_container
    )
    original_generation = h.generation
    monkeypatch.setattr(
        h,
        "generation",
        lambda value, *, name, image: (
            c.plan.cli_generation
            if name == h.CLI
            else original_generation(value, name=name, image=image)
        ),
    )

    def create(cid, command):
        assert cid == cli["Id"] and command == h.CONTROL["starting_normal"]
        eid = "3" * 64
        assert eid not in c.execs, "No command replay"
        c.created.append(command)
        c.execs[eid] = success.restored.dispatch_tests.execution(command) | dict(
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
        c.normal = m.base.App(c.plan.normal.pin, "running", "e" * 64)
        if c.lost_return:
            raise OSError("PRIVATE lost start return")

    def inspect(eid):
        if c.lost_inspection and c.started:
            raise OSError("PRIVATE unavailable inspection")
        return c.execs[eid].copy()

    monkeypatch.setattr(docker, "create_execution", create)
    monkeypatch.setattr(docker, "start_execution", start)
    monkeypatch.setattr(docker, "inspect_execution", inspect)
    processes = m.TrackedProcesses(
        c.journal,
        docker,
        images={m.base.NORMAL: c.plan.normal.image, m.base.CANDIDATE: c.plan.candidate.image},
        read_clock=lambda: (c.plan.boot, c.operator._clock()),
    )
    # Both original exit receipts are already in the actual journal; no fake
    # new pidfd or missing-process inference is inserted by this recovery loop.
    dispatch = m.TrackedDispatch(
        c.journal,
        docker,
        cli_image=c.plan.cli_image,
        cli_generation=c.plan.cli_generation,
        now=c.operator._clock,
    )
    c.session = m.launch.bootstrap.RecoverySession(c.journal, processes, dispatch, lambda: None)

    def native(docker, seal, command, generation):
        assert seal is c.plan.normal and c.journal.machine.state.phase == "starting_normal"
        c.native_calls.append(generation)
        return m.plans.ordinary.NativeState(generation, *c.native_values)

    monkeypatch.setattr(m.normal_read.cached, "_read_probe", native)

    def metadata(observer):
        boot, began = observer.read_clock()
        normal = c.normal
        if normal.state == "running":
            value = observer.read_native(m.base.NORMAL, normal.generation)
            normal = replace(normal, healthy=value.healthy, recording=value.recording)
        c.samples.append(c.journal.machine.state.phase)
        return m._StaticHostSnapshot(
            boot, began, observer.read_clock()[1], normal, c.candidate, True, True
        )

    monkeypatch.setattr(m._StaticHostObserver, "read", metadata)

    def wait(seconds):
        assert seconds == 0.25
        c.waits.append(c.journal.machine.state.phase)
        assert len(c.waits) < 4

    c.wait = wait
    yield c
    c.session.close()


def recover(c, wait=None):
    return m.recover_preserved(c.reader, c.run, c.session, c.wait if wait is None else wait)


def expire(c):
    c.session.processes.read_clock = lambda: (c.plan.boot, c.journal.machine.hard_deadline)


@pytest.mark.parametrize("failure", ["known", "lost", "no_progress"], indirect=True)
def test_unconfirmed_recording_restores_only_through_original_session(bridge):
    c = bridge
    original = files.originals(c), c.plan.raw, c.operator.result
    objects = c.session.executor, c.session.processes, c.session.dispatch
    assert recover(c).phase == "complete"
    assert c.created == [h.CONTROL["starting_normal"]]
    assert c.started == ["3" * 64] and c.native_calls == ["e" * 64]
    assert c.journal.machine.state.recording_outcome == "unconfirmed"
    assert c.ledger.state.acknowledgment is None and c.reader.collected.artifact is None
    assert c.journal.machine.state.preserved_sha256 == c.reader.collected.files.evidence_sha256
    assert objects == (c.session.executor, c.session.processes, c.session.dispatch)
    assert all(path.read_bytes() == raw for path, raw in original[0].items())
    assert (c.plan.raw, c.operator.result) == original[1:]
    assert c.session.processes.closed and not c.operator.closed and not c.reader.closed
    with pytest.raises(m.UnconfirmedHostBegin):
        recover(c)
    assert c.started == ["3" * 64]


def test_failure_files_and_metadata_are_read_without_dispatch_or_health(bridge):
    c = bridge
    host = m.PreservedHost(c.reader, c.run)
    sample = host.read()
    assert sample.observation.files.stage == "retained"
    assert sample.observation.candidate.state == "stopped"
    assert sample.observation.normal.healthy is sample.observation.candidate.healthy is None
    assert not c.created and not c.native_calls
    assert c.journal.machine.state.phase == "candidate_running"
    host.close()


def test_lost_normal_start_return_is_inspected_never_replayed(bridge):
    c = bridge
    c.lost_return = True
    assert recover(c).phase == "complete"
    assert len(c.created) == len(c.started) == 1
    assert c.journal.machine.state.recording_outcome == "unconfirmed"


def test_lost_inspection_cannot_complete_even_with_healthy_normal(bridge):
    c = bridge
    c.lost_inspection = True
    assert recover(c, lambda _: expire(c)).phase == "review"
    assert len(c.created) == 1 and c.ledger.state.acknowledgment is None


@pytest.mark.parametrize("after_start", [False, True])
def test_file_failure_never_falls_back_or_restarts_and_clock_can_expire(bridge, after_start):
    c = bridge
    if not after_start:
        c.tree.wav.write_bytes(b"short")

    def wait(_):
        if after_start and len(c.waits) == 0:
            c.tree.wav.write_bytes(b"changed after restoration was dispatched")
        else:
            expire(c)
        c.waits.append(True)

    assert recover(c, wait).phase == "review"
    assert len(c.created) == int(after_start)
    assert not c.reader.closed and not c.operator.closed
    assert c.journal.machine.state.recording_outcome == "unconfirmed"


@pytest.mark.parametrize("native", [(False, False), (None, None), (True, True)])
def test_bad_normal_health_never_promotes_recovery_or_recording(bridge, native):
    c = bridge
    c.native_values = native

    def wait(_):
        if c.native_calls:
            expire(c)

    assert recover(c, wait).phase == "review"
    assert c.journal.machine.state.recording_outcome == "unconfirmed"


@pytest.mark.parametrize(
    "fault", ["running", "replacement", "bad_metadata", "finalized", "artifact"]
)
def test_metadata_and_file_stages_cannot_cross_failure_boundary(bridge, monkeypatch, fault):
    c = bridge
    host = m.PreservedHost(c.reader, c.run)
    if fault == "running":
        c.candidate_container["State"].update(Status="running", Running=True, Pid=123)
    elif fault == "replacement":
        c.candidate_container["Id"] = "e" * 64
    elif fault == "bad_metadata":
        c.candidate = m.base.App(c.plan.candidate.pin, "unknown")
    else:
        original = c.reader.read

        def altered():
            value = original()
            if fault == "finalized":
                return replace(value, files=replace(value.files, stage="finalized"))
            return replace(value, artifact=SimpleNamespace(fabricated=True))

        monkeypatch.setattr(c.reader, "read", altered)
    with pytest.raises(m.UnconfirmedHostBegin):
        host.read()
    assert host.failed and not c.created and not c.native_calls


@pytest.mark.parametrize("fault", ["plan", "pins", "journal", "docker", "read", "qualify"])
def test_rebound_launch_does_not_supply_new_recovery_authority(bridge, fault):
    c = bridge
    if fault == "plan":
        c.run.plan = m.plans.load_bytes(c.plan.raw, c.plan.sha256)
    elif fault == "pins":
        c.run.pins = replace(c.run.pins)
    elif fault == "journal":
        c.run.journal = object()
    elif fault == "docker":
        c.run.qualify.docker = m.plans.ordinary.Docker()
    else:
        setattr(c.run, fault, SimpleNamespace(docker=c.run.read.docker))
    with pytest.raises(m.UnconfirmedHostBegin):
        recover(c)
    assert not c.created and not c.native_calls


def test_success_entry_never_accepts_a_preserved_recording(bridge):
    c = bridge
    with pytest.raises(m.UnconfirmedHostBegin):
        m.recover_finalized(c.reader, c.session, c.wait)
    assert not c.reader.recovery_attempted and not c.created


def test_never_authorized_entry_cannot_accept_an_attempted_recording(bridge):
    c = bridge
    for call in (
        lambda: m.recover_never_authorized(c.reader, c.run, c.session, c.wait),
        lambda: m.NeverAuthorizedHost(c.reader, c.run),
    ):
        with pytest.raises(m.UnconfirmedHostBegin):
            call()
        assert not c.reader.recovery_attempted and not c.created
