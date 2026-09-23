"""Real policy journals, dispatch ledgers and owned init pidfd; synthetic host I/O.

Idle claim, installed/source checks, Engine and Ready are explicitly fixtures.
This tests adapter ordering, not Docker/HAOS, genuine native readiness or recovery.
The Engine/Ready mechanisms have separate actual transport/process qualification.
"""

import importlib.util
import json
import os
import select
import socket
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_dispatch as dispatch
from . import test_supplemental_recording_host_plan as plans
from . import test_supplemental_recording_idle_observer as idle_tests  # noqa: F401
from . import test_supplemental_recording_probe_exec as probe_tests  # noqa: F401
from . import test_supplemental_recording_ready as ready_tests  # noqa: F401

NAME = "supplemental_recording_host_launch"
SPEC = importlib.util.spec_from_file_location(NAME, Path(plans.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
b = m.base
ACTUAL_ENDPOINT, ACTUAL_CLIENT, ACTUAL_READY = m.engine.Endpoint, m.engine.Client, m.received.Ready
ACTUAL_PROBE = m.probe_exec.Sample
family, actors = ready_tests.family, ready_tests.actors
probe_child = probe_tests.probe_child
layout, tree, routing, projection, binding, directory, prepared = (
    dispatch.layout,
    dispatch.tree,
    dispatch.routing,
    dispatch.projection,
    dispatch.binding,
    dispatch.directory,
    dispatch.prepared,
)


def denied(callback):
    with pytest.raises(m.UnconfirmedHostLaunch) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@pytest.fixture
def setup(prepared, tmp_path, monkeypatch):
    _, values = plans.projected_plan(prepared.pins.host.projection)
    original = m.plans.clock.read()
    issued = original.boottime_ns / m.plans.clock.NS
    values.update(
        boot=original.boot,
        original_clock=asdict(original) | {"namespace": list(original.namespace)},
        deadlines=dict(
            issued_at=issued, ready_by=issued + 120, stop_by=issued + 400, recover_by=issued + 1500
        ),
    )
    plan = m.plans.decode(values)
    root = tmp_path / "host-launch"
    root.mkdir(mode=0o700)
    for name in ("journal", "operator-exec", "web-exec"):
        (root / name).mkdir(mode=0o700)
    # Explicit local alias, not a claim of installed /mnt/data protection.
    monkeypatch.setattr(m.plans.Plan, "root", property(lambda self: root))
    state = SimpleNamespace(trace=[], qualifies=0, reads=0, fault=None, launches=[])

    def now():
        return m.plans.clock.read().boottime_ns / m.plans.clock.NS

    normal = b.App(plan.normal.pin, "running", plan.normal_generation, True, False)
    stopped_normal = b.App(plan.normal.pin, "stopped")
    candidate = b.App(plan.candidate.pin, "stopped")
    idle_app = b.App(plan.candidate.pin, "running", prepared.pins.generation, None, None)
    files = m.bootstrap.recording.Files(
        plan.candidate.contract.sha256, "pristine", plan.candidate.contract.baseline_sha256
    )

    def observation(a=stopped_normal, c=idle_app, at=None):
        return m.bootstrap.recording.Observation(
            now() if at is None else at, a, c, True, True, True, files
        )

    idle = object.__new__(m.idle_module.Idle)
    idle.plan, idle.init, idle.generation, idle.zero_domain = (
        plan,
        prepared.pins.init,
        prepared.pins.generation,
        None,
    )

    def idle_read(self):
        assert self is idle
        state.trace.append("idle")
        return m.idle_module.Evidence(
            plan.sha256,
            idle.generation,
            idle.init,
            "a" * 64,
            "b" * 64,
            plan.lease_sha256,
            "c" * 64,
            issued,
            now(),
        )

    monkeypatch.setattr(m.idle_module.Idle, "read", idle_read)

    class Endpoint:
        closed = False

        def check(self):
            assert not self.closed

        def close(self):
            self.closed = True

    class Client:
        def __init__(self, endpoint, claim):
            self.endpoint, self.claim = endpoint, claim
            self.closed = False
            state.trace.append("client")

        def create(self):
            state.trace.append("create")
            assert journal.machine.state.phase == "starting_operator"
            assert journal.machine.state.launch_intent_sha256 is not None
            assert self.claim.state.phase == "create_intent"
            state.creates += 1
            if state.fault == "create":
                raise OSError("PRIVATE lost create return")
            self.claim.created(dispatch.execution.EXEC)
            return dispatch.execution.EXEC

        def attach(self, *, finish_by):
            state.trace.append("attach")
            assert finish_by == plan.lease["stop_by"]
            self.claim.attach_intent(dispatch.metadata(SimpleNamespace(pins=self.claim.pins)))
            if state.fault == "attach":
                raise OSError("PRIVATE lost attach return")

        def close(self):
            self.closed = True
            self.endpoint.close()

    class Ready:
        def __init__(self, client, *, profile_sha256, original_clock, zero_domain):
            state.trace.append("ready")
            assert original_clock is plan.original_clock and zero_domain is idle.zero_domain
            assert profile_sha256 == "f" * 64
            if state.fault == "ready":
                raise ValueError("PRIVATE malformed Ready")
            self.client = client
            self.failed = self.closed = False
            self.received_at = time.monotonic()
            self.ready_raw = b"synthetic-ready-not-a-native-receipt"

        def check_before_begin(self):
            assert not self.failed and not self.closed and not self.client.closed
            state.trace.append("ready_check")

        def close(self):
            self.closed = True
            self.client.close()

    class Probe:
        def __init__(self, ready):
            assert type(ready) is Ready and not ready.failed
            self.ready, self.closed = ready, False
            self.execution_id, self.request_sha256 = "8" * 64, "9" * 64

        def read(self):
            state.trace.append("probe")
            self.ready.check_before_begin()
            if state.fault == "probe":
                raise ValueError("PRIVATE lost probe return")
            return m.plans.ordinary.NativeState(
                "0" * 64 if state.fault == "probe_generation" else idle.generation,
                state.fault != "unhealthy",
                state.fault == "recording",
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(m.engine, "Endpoint", Endpoint)
    monkeypatch.setattr(m.engine, "Client", Client)
    monkeypatch.setattr(m.received, "Ready", Ready)
    monkeypatch.setattr(m.probe_exec, "Sample", Probe)
    endpoint = Endpoint()
    with m.bootstrap.Journal(root / "journal") as journal:
        baseline = observation(normal, candidate, issued)
        journal.append(plan.preparation(baseline, prepared.pins.host.projection))

        def append(kind, **fields):
            return journal.append(dict(kind=kind, now=now(), boot_id=plan.boot, **fields))

        append(
            "bind_process",
            process=asdict(b.ProcessRecord(b.NORMAL, normal.generation, "8" * 64, 1234, 100)),
        )
        append("request")
        assert (
            append("observe", observation=asdict(observation(normal, candidate))).slug == b.NORMAL
        )
        append("bind_execution", container_id="a" * 64, execution_id="1" * 64)
        append("execution_completed", execution_id="1" * 64, exit_code=0)
        append("process_exited", generation=normal.generation)
        assert append("observe", observation=asdict(observation(c=candidate))).slug == b.CANDIDATE
        append("bind_execution", container_id="a" * 64, execution_id="2" * 64)
        append("execution_completed", execution_id="2" * 64, exit_code=0)
        init = prepared.pins.init
        append(
            "bind_process",
            process=asdict(
                b.ProcessRecord(
                    b.CANDIDATE, idle.generation, init.container_id, init.pid, init.start_ticks
                )
            ),
        )
        assert append("observe", observation=asdict(observation())) is None
        assert journal.machine.state.phase == "candidate_idle"

        def read():
            state.reads += 1
            obs = observation()
            if state.fault == "changed_second" and state.reads == 2:
                obs = replace(obs, jobs_idle=False)
            if state.fault == "healthy_idle":
                obs = replace(obs, candidate=replace(obs.candidate, healthy=True, recording=False))
            return m.bootstrap.recovery.Sample(plan.boot, now(), obs)

        def qualify():
            state.qualifies += 1
            state.trace.append("qualify")
            if state.fault == f"qualify_{state.qualifies}":
                raise ValueError("PRIVATE installed source changed")
            if state.fault == "qualify_boolean":
                return True
            return None

        def launch(**changes):
            args = dict(launch_sha256="e" * 64, profile_sha256="f" * 64, read=read, qualify=qualify)
            obj = m.Launch(
                plan,
                prepared.pins.host.projection,
                journal,
                idle,
                prepared.witness,
                state.endpoint,
                **(args | changes),
            )
            state.launches.append(obj)
            return obj

        state.plan, state.journal, state.idle, state.endpoint = plan, journal, idle, endpoint
        state.launch, state.append, state.creates = launch, append, 0
        try:
            yield state
        finally:
            for obj in state.launches:
                obj.close()


def test_durable_policy_then_exact_dispatch_and_ready_remain_separate(setup):
    s = setup
    deadlines = (
        s.journal.machine.created_at,
        s.journal.machine.hard_deadline,
        s.journal.machine.state.deadline,
    )
    run = s.launch()
    ready = run.start()
    assert ready is run.ready and s.creates == 1
    assert s.qualifies == 5 and s.reads == 2
    assert s.trace.index("create") < s.trace.index("attach") < s.trace.index("ready")
    assert run.claim.state.count == 3
    assert run.claim.directory == s.plan.root / "operator-exec"
    assert run.command.ready_by == s.plan.lease["ready_by"]
    assert s.journal.machine.state.phase == "starting_operator"
    assert s.journal.machine.state.ready_evidence_sha256 is None
    assert s.journal.machine.state.authorization_generation is None
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert deadlines == (
        s.journal.machine.created_at,
        s.journal.machine.hard_deadline,
        s.journal.machine.state.deadline,
    )
    denied(s.launch)  # A fresh object cannot reauthorize this consumed case.
    denied(run.start)
    assert s.creates == 1 and ready.failed and not ready.closed
    assert run.client.closed  # Exact actor handles are retained until explicit close.


@pytest.mark.parametrize(
    "fault",
    [
        "qualify_1",
        "qualify_2",
        "qualify_3",
        "qualify_4",
        "qualify_5",
        "qualify_boolean",
        "healthy_idle",
        "changed_second",
        "create",
        "attach",
        "ready",
    ],
)
def test_failure_never_retries_or_authorizes_recording(setup, fault):
    s = setup
    run = s.launch()
    s.fault = fault
    denied(run.start)
    assert run.failed and run.used
    denied(run.start)
    assert s.creates <= 1
    assert s.journal.machine.state.ready_evidence_sha256 is None
    assert s.journal.machine.state.authorization_generation is None
    if s.journal.machine.state.launch_intent_sha256 is not None:
        denied(s.launch)
    if run.ready is not None:
        assert run.ready.failed and not run.ready.closed
    if run.client is not None:
        assert run.client.closed


@pytest.mark.parametrize("fault", ["file", "permission", "extra", "replacement"])
def test_original_journal_changes_refuse_before_engine_write(setup, fault):
    s = setup
    run = s.launch()
    path = s.journal.path
    if fault == "file":
        (path / "0000.json").write_bytes(b"{}")
    elif fault == "permission":
        (path / "0000.json").chmod(0o644)
    elif fault == "extra":
        (path / "extra").write_bytes(b"PRIVATE")
    else:
        path.rename(path.with_name("retained-journal"))
        path.mkdir(mode=0o700)
    denied(run.start)
    assert s.creates == 0


def test_lost_policy_fsync_return_consumes_intent_before_any_engine_request(setup, monkeypatch):
    s = setup
    run = s.launch()
    original = os.fsync
    calls = []

    def lost(fd):
        original(fd)
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("PRIVATE policy publication return lost")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", lost)
        denied(run.start)
    assert s.creates == 0 and s.journal.fd == -1
    with m.bootstrap.Journal(s.plan.root / "journal") as replay:
        assert replay.machine.state.launch_intent_sha256 is not None
        assert replay.machine.state.phase == "starting_operator"
    denied(run.start)


def test_finish_request_prevents_launch_without_new_deadline(setup):
    s = setup
    run = s.launch()
    end = s.journal.machine.hard_deadline
    s.append("finish")
    denied(run.start)
    assert s.creates == 0 and s.journal.machine.hard_deadline == end


def test_wrong_launch_or_profile_digest_rejected_before_intent(setup):
    for fields in ({"launch_sha256": "PRIVATE"}, {"profile_sha256": True}):
        denied(lambda fields=fields: setup.launch(**fields))
    assert setup.journal.machine.state.launch_intent_sha256 is None


def test_changed_idle_incarnation_refused_before_engine_write(setup):
    run = setup.launch()
    setup.idle.generation = "0" * 64
    denied(run.start)
    assert setup.creates == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("plan", "/data/other/launch.json"),
        ("source_sha256", "0" * 64),
        ("plan_sha256", "0" * 64),
        ("ready_by", 99999999.0),
    ],
)
def test_command_cannot_drift_even_if_caller_changes_both_references(setup, field, value):
    run = setup.launch()
    run.command = replace(run.command, **{field: value})
    run.pins = replace(run.pins, command=run.command)
    denied(run.start)
    assert setup.creates == 0


def test_expired_original_host_clock_prevents_launch(setup, monkeypatch):
    run = setup.launch()
    original = setup.plan.original_clock
    shift = 121 * m.plans.clock.NS
    expired = replace(
        original,
        before_ns=original.before_ns + shift,
        boottime_ns=original.boottime_ns + shift,
        after_ns=original.after_ns + shift,
    )
    monkeypatch.setattr(m.plans.clock, "read", lambda: expired)
    denied(run.start)
    assert setup.creates == 0


@pytest.mark.parametrize("fault", ["first", "second"])
def test_stale_full_observation_never_authorizes_dispatch(setup, fault):
    run = setup.launch()
    read = run.read
    count = 0

    def stale():
        nonlocal count
        count += 1
        sample = read()
        if count == (1 if fault == "first" else 2):
            return replace(
                sample,
                now=sample.now - 3,
                observation=replace(
                    sample.observation, sampled_at=sample.observation.sampled_at - 3
                ),
            )
        return sample

    run.read = stale
    denied(run.start)
    assert setup.creates == 0


def test_actual_probe_join_promotes_ready_but_never_authorizes_recording(setup):
    run = setup.launch()
    ready = run.start()
    original = setup.journal.machine.hard_deadline
    native = run.confirm_ready()
    machine = setup.journal.machine
    assert native == m.plans.ordinary.NativeState(setup.idle.generation, True, False)
    assert machine.state.phase == "candidate_running"
    assert machine.state.ready_evidence_sha256 is not None
    assert machine.state.authorization_generation is None
    assert machine.state.recording_outcome == "not_attempted"
    assert machine.hard_deadline == original
    event = setup.journal.entries[-1]["event"]
    assert event["kind"] == "operator_ready"
    # Positive offset is a host clock conversion, not a new timestamp.
    expected = ready.received_at + setup.plan.original_clock.offset[0] / m.plans.clock.NS
    assert event["received_at"] == pytest.approx(expected, abs=1e-6)
    assert not run.probe.closed
    denied(run.confirm_ready)
    assert ready.failed and run.client.closed and not run.probe.closed


@pytest.mark.parametrize(
    "fault", ["probe", "probe_generation", "unhealthy", "recording", "qualify_6", "qualify_7"]
)
def test_unconfirmed_or_active_probe_cannot_be_promoted_to_ready(setup, fault):
    run = setup.launch()
    run.start()
    setup.fault = fault
    denied(run.confirm_ready)
    assert setup.journal.machine.state.phase == "starting_operator"
    assert setup.journal.machine.state.ready_evidence_sha256 is None
    assert setup.journal.machine.state.authorization_generation is None
    assert run.client.closed and run.ready.failed
    denied(run.confirm_ready)


def test_late_confirmation_does_not_refresh_original_ready_receipt(setup):
    run = setup.launch()
    ready = run.start()
    ready.received_at -= 3
    denied(run.confirm_ready)
    assert setup.journal.machine.state.phase == "starting_operator"
    assert setup.journal.machine.state.ready_evidence_sha256 is None


def test_newest_host_sample_cannot_hide_oldest_probe_time(setup, monkeypatch):
    run = setup.launch()
    run.start()
    actual = run._guard
    count = 0

    def guard(phase):
        nonlocal count
        machine, now = actual(phase)
        count += 1
        # The fourth guard in confirmation supplies the lower sampling bound.
        if count == 4:
            return machine, now - 3
        return machine, now

    monkeypatch.setattr(run, "_guard", guard)
    denied(run.confirm_ready)
    assert setup.journal.machine.state.ready_evidence_sha256 is None


def test_lost_ready_publication_return_keeps_actor_handles_and_durable_evidence(setup, monkeypatch):
    run = setup.launch()
    run.start()
    original = os.fsync
    calls = []

    def lost(fd):
        original(fd)
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("PRIVATE ready fsync return lost")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", lost)
        denied(run.confirm_ready)
    assert run.ready.failed and not run.ready.closed and not run.probe.closed
    assert setup.journal.fd == -1
    with m.bootstrap.Journal(setup.plan.root / "journal") as replay:
        assert replay.machine.state.ready_evidence_sha256 is not None
        assert replay.machine.state.authorization_generation is None
    denied(run.confirm_ready)


@pytest.mark.parametrize("sender_credentials", [False, True])
@pytest.mark.parametrize(
    "fault", [None, "lost_create", "ready_context", "qualify_5", "probe_reply", "probe_exit"]
)
def test_bootstrap_launch_uses_real_transport_ready_and_retained_actor_handles(
    setup, actors, probe_child, tmp_path, monkeypatch, sender_credentials, fault
):
    """Real Unix HTTP/framing/pidfds; synthetic Engine, idle and host namespaces."""
    s = setup
    e = ready_tests.joined.engine
    transport = e.attached
    monkeypatch.setattr(m.engine, "Endpoint", ACTUAL_ENDPOINT)
    monkeypatch.setattr(m.engine, "Client", ACTUAL_CLIENT)
    monkeypatch.setattr(m.received, "Ready", ACTUAL_READY)
    monkeypatch.setattr(m.probe_exec, "Sample", ACTUAL_PROBE)
    domains = tuple(
        (st.st_dev, st.st_ino)
        for st in (os.stat("/proc/self/ns/" + name) for name in m.engine.namespace.NAMESPACES)
    )
    actors.values = tuple(replace(actor, namespaces=domains) for actor in actors.values)
    actors.lookup.update({actor.host_pid: actor for actor in actors.values})
    probe_child.actor = replace(probe_child.actor, namespaces=domains)
    actors.lookup[probe_child.actor.host_pid] = probe_child.actor
    monkeypatch.setattr(
        m.engine.namespace.Witness, "_host_domains", staticmethod(lambda: domains[3:])
    )
    path = tmp_path / "engine.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen(2)
    listener.settimeout(0.05)
    monkeypatch.setattr(m.engine, "SOCKET", path)
    monkeypatch.setattr(m.engine, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.engine, "ROOT_GID", os.getegid())
    monkeypatch.setattr(m.engine.senders, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.engine.senders, "ROOT_GID", os.getegid())
    stop, requests, errors, retained, responders = Event(), [], [], [], []
    s.endpoint = m.engine.Endpoint(sender_credentials=sender_credentials)
    run = s.launch()
    pins = SimpleNamespace(pins=run.pins)
    s.fault = fault

    def probe_reply(peer):
        try:
            header = transport.exact(peer, transport.stream.m.wire.HEADER.size)
            size = transport.stream.m.wire.HEADER.unpack(header)[0]
            request = json.loads(transport.exact(peer, size))
            assert request == json.loads(run.probe.request_raw)
            value = probe_tests.report(request)
            if fault == "probe_reply":
                value["body"]["peer_pid"] = probe_child.actor.local_pid
            probe_child.process.stdin.close()
            assert select.select([probe_child.fd], [], [], 2)[0]
            peer.sendall(transport.stream.segment(transport.stream.app(value)))
        except BaseException as error:
            errors.append(error)
        finally:
            peer.close()

    def serve():
        while not stop.is_set():
            try:
                peer, _ = listener.accept()
            except TimeoutError:
                continue
            try:
                peer.settimeout(3)
                request = e.read_request(peer)
                if sender_credentials:
                    assert request[0] == "GET /v1.47/_ping HTTP/1.1"
                    peer.sendall(transport.senders.REPLY)
                    request = e.read_request(peer)
                requests.append(request)
                index = len(requests)
                assert index <= 11
                assert s.journal.machine.state.phase == "starting_operator"
                assert s.journal.machine.state.launch_intent_sha256 == run.action.intent_sha256
                if index == 1:
                    assert run.claim.state.phase == "create_intent"
                    assert request[2] == run.command.create_body()
                    if fault != "lost_create":
                        peer.sendall(e.reply({"Id": transport.EXEC}, 201))
                elif index == 2:
                    assert run.claim.state.phase == "created"
                    peer.sendall(e.reply(dispatch.metadata(pins)))
                elif index == 3:
                    assert run.claim.state.phase == "attach_intent"
                    assert request[2] == {"Detach": False, "Tty": False}
                    value = ready_tests.envelope(pins, actors)
                    if fault == "ready_context":
                        value["context"]["source"] = "0" * 64
                    retained.append(peer.dup())
                    peer.sendall(
                        transport.stream.UPGRADE
                        + transport.stream.segment(transport.stream.app(value))
                    )
                elif index in (4, 5):
                    assert request[2] is None
                    value = dispatch.metadata(pins)
                    value.update(Running=True, Pid=actors.values[1].host_pid)
                    peer.sendall(e.reply(value))
                elif index == 6:
                    assert request[2] == run.probe.command.create_body()
                    peer.sendall(e.reply({"Id": probe_tests.PROBE_ID}, 201))
                elif index == 8:
                    assert request[2] == {"Detach": False, "Tty": False}
                    peer.sendall(transport.stream.UPGRADE)
                    responder = Thread(target=probe_reply, args=(peer.dup(),))
                    responders.append(responder)
                    responder.start()
                else:
                    assert index in (7, 9, 10, 11) and request[2] is None
                    value = probe_tests.metadata(
                        run.probe.command,
                        pid=0 if index == 7 else probe_child.actor.host_pid,
                        running=index in (9, 10),
                        code=(70 if fault == "probe_exit" else 0) if index == 11 else None,
                    )
                    peer.sendall(e.reply(value))
            except BaseException as error:
                errors.append(error)
            finally:
                peer.close()

    worker = Thread(target=serve)
    worker.start()
    try:
        if fault in (None, "probe_reply", "probe_exit"):
            ready = run.start()
            assert type(ready) is ACTUAL_READY
            assert ready.processes.refresh() == actors.values
            assert len(ready.processes.handles) == 4
            assert len(requests) == 5
            assert not ready.client.attachment.begun
            assert s.journal.machine.state.ready_evidence_sha256 is None
            if fault is None:
                assert run.confirm_ready() == m.plans.ordinary.NativeState(
                    s.idle.generation, True, False
                )
                assert s.journal.machine.state.phase == "candidate_running"
                assert s.journal.machine.state.ready_evidence_sha256 is not None
                assert len(requests) == 11
                assert not ready.client.closed
            else:
                denied(run.confirm_ready)
                assert s.journal.machine.state.ready_evidence_sha256 is None
                assert len(requests) == (10 if fault == "probe_reply" else 11)
                assert ready.failed and not ready.closed and ready.client.closed
            assert type(run.probe) is ACTUAL_PROBE
            assert run.probe.actor == probe_child.actor and run.probe.fd >= 0
            assert select.select([run.probe.fd], [], [], 0)[0]
            assert ready.processes.refresh() == actors.values
            assert not ready.client.attachment.begun
        else:
            denied(run.start)
            assert run.client.closed
            assert len(requests) == {"lost_create": 1, "ready_context": 3, "qualify_5": 5}[fault]
            if fault == "qualify_5":
                assert run.ready.failed and not run.ready.closed
                assert run.ready.processes.refresh() == actors.values
        denied(s.launch)
        assert s.journal.machine.state.authorization_generation is None
    finally:
        run.close()
        for peer in retained:
            peer.close()
        stop.set()
        worker.join(4)
        for responder in responders:
            responder.join(4)
            assert not responder.is_alive()
        listener.close()
        assert not worker.is_alive() and not errors, errors
