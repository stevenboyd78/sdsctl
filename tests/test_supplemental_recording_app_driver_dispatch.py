"""Accepted startup, original dispatch and all three recovery joins.

The real notice publisher, coordinator, session, process tracker and dispatcher
produce initial handoff history. Owned subprocess pidfds supply the normal exit
and candidate identity. Container/cgroup/generation metadata, full host facts,
App publication provenance and native/file/exit I/O remain synthetic. In
particular the candidate child is precreated, not an actual App startup. This
is not installed supervision, a native lifetime or scanner qualification.
"""

import copy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_driver_startup as startup

driver, pristine, failure = startup.driver, startup.pristine, startup.failure
m, base, launch = driver.m, driver.base, driver.launch
candidate, app, native, driver_case = (
    startup.candidate,
    startup.app,
    startup.native,
    driver.driver_case,
)
layout, image_umask, supervised, image, configured = (
    startup.layout,
    startup.image_umask,
    startup.supervised,
    startup.image,
    startup.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture
def launch_case(native, monkeypatch):
    native.initial_dispatch = True
    native.accepted_owner.app_idle_publication_used = True  # Synthetic publication provenance.
    yield from startup.launch_cases.setup_launch_case(
        native, monkeypatch, owner=native.accepted_owner
    )


@pytest.fixture
def dispatched(driver_case, monkeypatch):
    s = driver_case
    tracking = sys.modules[launch.TrackedProcesses.__module__]
    process, h = launch.engine.dispatch.process, driver.platform.h
    t = s.transfer_io = SimpleNamespace(
        created=[],
        started=[],
        execs={},
        samples=[],
        candidate_started=False,
        stop_lost=False,
        create_lost=False,
        inspect_lost=False,
        suppress_exit=False,
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
    )
    normal_id, cli_id = "8" * 64, "b" * 64
    t.normal = dict(
        Id=normal_id,
        Name="/app_" + base.NORMAL,
        Image=s.plan.normal.image,
        State=dict(
            Status="running",
            Running=True,
            Paused=False,
            Restarting=False,
            Dead=False,
            Pid=child.pid,
            StartedAt="2026-09-27T00:00:00Z",
        ),
    )
    t.cli = {"Id": cli_id}
    original_container, original_identity = s.docker.container, process.read_identity
    original_generation = h.generation

    def container(name):
        if name == h.CLI:
            return t.cli.copy()
        if name == "app_" + base.NORMAL:
            return copy.deepcopy(t.normal)
        assert name == "app_" + base.CANDIDATE
        return original_container(name) if t.candidate_started else None

    def identity(pid, cid):
        if (pid, cid) == (child.pid, normal_id):
            return process.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )
        return original_identity(pid, cid)

    def generation(value, *, name, image):
        if name == h.CLI:
            assert value == t.cli and image == s.plan.cli_image
            return s.plan.cli_generation
        if name == "app_" + base.NORMAL:
            assert value["Id"] == normal_id and image == s.plan.normal.image
            return s.plan.normal_generation
        return original_generation(value, name=name, image=image)

    def create(cid, command):
        assert cid == cli_id
        assert command == h.CONTROL[s.journal.machine.state.phase]
        eid = str(len(t.created) + 1) * 64
        t.created.append(command)
        t.execs[eid] = driver.platform.restored.dispatch_tests.execution(command) | dict(
            ID=eid,
            ContainerID=cid,
            Running=False,
            ExitCode=None,
            Pid=0,
        )
        if t.create_lost:
            raise OSError("PRIVATE lost create acknowledgment")
        return eid

    def start(eid):
        assert eid not in t.started
        t.started.append(eid)
        t.execs[eid].update(Running=False, ExitCode=0, Pid=321)
        command = t.created[-1]
        if command == h.CONTROL["stopping_normal"]:
            # Only this fixture's owned subprocess receives EOF. Docker/HA is
            # not contacted and no external process is signaled.
            if not t.suppress_exit:
                child.stdin.close()
                child.wait(timeout=2)
            t.normal["State"].update(Status="exited", Running=False, Pid=0)
            if t.stop_lost:
                raise OSError("PRIVATE lost stop acknowledgment")
        else:
            assert command == h.CONTROL["starting_candidate"]
            t.candidate_started = True

    def inspect(eid):
        if t.inspect_lost and t.started:
            raise OSError("PRIVATE unavailable initial inspection")
        return t.execs[eid].copy()

    def read_host(reader):
        assert reader is s.service.transfer
        now = s.service._now()
        normal = (
            base.App(s.plan.normal.pin, "running", s.plan.normal_generation, True, False)
            if t.normal["State"]["Running"]
            else base.App(s.plan.normal.pin, "stopped")
        )
        candidate = (
            base.App(s.plan.candidate.pin, "running", s.idle.generation)
            if t.candidate_started
            else base.App(s.plan.candidate.pin, "stopped")
        )
        t.samples.append(s.journal.machine.state.phase)
        observed = launch.bootstrap.recording.Observation(
            now,
            normal,
            candidate,
            True,
            True,
            True,
            launch.bootstrap.recording.Files(
                s.plan.candidate.contract.sha256,
                "pristine",
                s.plan.candidate.contract.baseline_sha256,
            ),
        )
        return launch.bootstrap.recovery.Sample(s.plan.boot, s.service._now(), observed)

    monkeypatch.setattr(s.docker, "container", container)
    monkeypatch.setattr(h, "generation", generation)
    monkeypatch.setattr(tracking, "generation", generation)
    monkeypatch.setattr(process, "read_identity", identity)
    monkeypatch.setattr(tracking, "read_identity", identity)
    monkeypatch.setattr(s.docker, "create_execution", create)
    monkeypatch.setattr(s.docker, "start_execution", start)
    monkeypatch.setattr(s.docker, "inspect_execution", inspect)
    s.transfer_read = read_host
    try:
        assert len(s.journal.entries) == 1 and not s.session.processes.witnesses
        assert type(s.session.processes) is launch.TrackedProcesses
        yield s
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        child.wait(timeout=2)


def request(s):
    m.operator.Publisher(
        s.case_plan, "request", base.checksum(s.journal.entries[0]["event"]), s.service._now()
    ).publish()


def run(s, monkeypatch, action, *, record=True):
    waits = []

    def wait(seconds):
        assert seconds == 0.25
        phase = s.journal.machine.state.phase
        waits.append(phase)
        if phase == "stopping_normal":
            assert s.transfer_io.created and s.transfer_io.started, (
                s.transfer_io.created,
                s.transfer_io.started,
            )
        assert len(waits) < 8, "Synthetic dispatch must settle without real waiting"
        if phase == "candidate_idle" and not s.transitions:
            s.witness = s.session.processes.witnesses[base.CANDIDATE]
            assert s.witness.identity == s.idle.init and not s.witness.exited()
            assert s.session.processes.exit_confirmed(base.NORMAL)
            driver.handoff(s, monkeypatch, record=record)
            action()
        elif s.transitions:
            s.cycle.wait(seconds)

    request(s)
    result = s.driver.run(wait)
    assert waits[:3] == ["stopping_normal", "starting_candidate", "candidate_idle"]
    return result


@pytest.mark.parametrize("route", ["finalized", "preserved", "pristine"])
def test_original_notice_dispatch_and_process_receipts_join_recovery(
    dispatched, monkeypatch, route
):
    s = dispatched
    action = {
        "finalized": lambda: driver.prepare_recovery(s, monkeypatch),
        "preserved": lambda: failure.prepare_preservation(s, monkeypatch),
        "pristine": lambda: pristine.prepare(s, monkeypatch),
    }[route]
    assert run(s, monkeypatch, action, record=route != "pristine").phase == "complete"
    h = driver.platform.h
    assert s.transfer_io.created == [h.CONTROL["stopping_normal"], h.CONTROL["starting_candidate"]]
    assert s.transfer_io.started == ["1" * 64, "2" * 64]
    recovery_commands = 2 if route == "finalized" else 1
    assert len(s.cycle.created) == len(s.cycle.started) == recovery_commands
    events = [item["event"] for item in s.journal.entries]
    assert len([event for event in events if event["kind"] == "request"]) == 1
    assert len([event for event in events if event["kind"] == "bind_process"]) == 2
    assert len(s.journal.machine.state.completed_executions) == 2 + recovery_commands
    assert (
        s.journal.machine.state.recording_outcome
        == {
            "finalized": "verified",
            "preserved": "unconfirmed",
            "pristine": "not_attempted",
        }[route]
    )
    driver.assert_owners(s)


@pytest.mark.parametrize("driver_case", ["observed"], indirect=True)
@pytest.mark.parametrize("route", ["finalized", "preserved", "pristine"])
def test_original_observer_survives_app_native_recording_and_recovery_handoffs(
    dispatched, monkeypatch, route
):
    s = dispatched
    observer = s.service.dispatch.observe
    assert observer is not None and not s.dispatch_notices
    test_original_notice_dispatch_and_process_receipts_join_recovery(s, monkeypatch, route)
    commands = s.transfer_io.created + s.cycle.created
    phases = [
        phase
        for command in commands
        for phase, argv in driver.platform.h.CONTROL.items()
        if command == argv
    ]
    assert len(phases) == len(commands)
    assert [(notice.phase, notice.stage) for notice in s.dispatch_notices] == [
        (phase, stage) for phase in phases for stage in ("before_create", "before_start")
    ]
    assert s.service._dispatch_observer is s.driver.dispatch.observe is observer
    assert s.driver.dispatch._original_observe is observer
    assert s.service.closed and not s.startup.clock.closed


def test_lost_initial_stop_return_is_reconciled_without_reissuing(dispatched, monkeypatch):
    s = dispatched
    s.transfer_io.stop_lost = True
    assert run(s, monkeypatch, lambda: driver.prepare_recovery(s, monkeypatch)).phase == "complete"
    assert s.transfer_io.started == ["1" * 64, "2" * 64]
    assert len(s.transfer_io.created) == 2
    assert s.journal.machine.state.recording_outcome == "verified"
    driver.assert_owners(s)


@pytest.mark.parametrize("fault", ["create_lost", "inspect_lost", "suppress_exit"])
def test_uncertain_initial_transfer_expires_without_launch_or_repeat(
    dispatched, monkeypatch, fault
):
    s = dispatched
    setattr(s.transfer_io, fault, True)
    request(s)

    def wait(seconds):
        assert seconds == 0.25 and s.journal.machine.state.phase == "stopping_normal"
        assert len(s.transfer_io.created) == 1 and not s.transfer_io.candidate_started
        assert not s.driver.native_attempted and not s.driver.recording_attempted
        driver.expire(s, monkeypatch)

    assert s.driver.run(wait).phase == "review"
    assert len(s.transfer_io.created) == 1
    assert len(s.transfer_io.started) == (0 if fault == "create_lost" else 1)
    assert not s.creates and not s.transitions and not s.transfer_io.candidate_started
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.service.closed and s.session.processes.closed and not s.service.failed
    assert s.driver.native is s.driver.recording is None
    driver.assert_owners(s)
