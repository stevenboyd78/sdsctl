"""Real journals/dispatch claims, files/socket inodes and owned init pidfd.

Startup, App/Engine metadata, host samples and Ready/probe actors are explicitly
synthetic. This exercises the real App inventory transition and action ordering,
not installed provenance, native transport, scanner behavior or recovery.
"""

import importlib.util
import os
import socket
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_ready_qualification as readers
from . import test_supplemental_recording_host_begin as begins

candidate, app, native, launch_case = (
    readers.candidate,
    readers.app,
    readers.native,
    readers.launch_case,
)
layout, image_umask, supervised = readers.layout, readers.image_umask, readers.supervised
image, configured = readers.image, readers.configured
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)

NAME = "supplemental_recording_app_execution"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(readers.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
launch, b = m.launch, m.launch.base
denied = begins.launches.denied


@pytest.fixture
def execution(launch_case, tmp_path, monkeypatch):
    s, p = launch_case, launch_case.plan
    s.prelaunch = s.publish()
    s.other = m.inputs.NativeLaunchQualification(s.startup, s.original, s.prelaunch.launch_inputs)
    assert s.other() is None
    host_root = tmp_path / "host-execution"
    host_root.mkdir(mode=0o700)
    for name in ("journal", "operator-exec", "web-exec"):
        (host_root / name).mkdir(mode=0o700)
    monkeypatch.setattr(launch.plans.Plan, "root", property(lambda self: host_root))
    s.idle.zero_domain = None
    s.trace, s.runs, s.creates, s.fault = [], [], 0, None
    s.close_error = False

    def tick(name):
        s.trace.append(name)
        if s.fault == name:
            raise ValueError("PRIVATE uncertain " + name)

    def now():
        return launch.plans.clock.read().boottime_ns / launch.plans.clock.NS

    normal = b.App(p.normal.pin, "running", p.normal_generation, True, False)
    stopped = b.App(p.normal.pin, "stopped")
    absent = b.App(p.candidate.pin, "stopped")
    idle_app = b.App(p.candidate.pin, "running", s.idle.generation, None, None)
    files = launch.bootstrap.recording.Files(
        p.candidate.contract.sha256, "pristine", p.candidate.contract.baseline_sha256
    )

    def observation(a=stopped, c=idle_app, at=None):
        return launch.bootstrap.recording.Observation(
            now() if at is None else at, a, c, True, True, True, files
        )

    class Endpoint:
        closed = False

        def check(self):
            assert not self.closed

        def close(self):
            self.closed = True

    class Client:
        def __init__(self, endpoint, claim):
            self.endpoint, self.claim = endpoint, claim
            self.closed = self.begun = False
            tick("client")

        def create(self):
            assert journal.machine.state.phase == "starting_operator"
            assert self.claim.state.phase == "create_intent"
            s.creates += 1
            tick("create")
            self.claim.created(begins.launches.dispatch.execution.EXEC)
            if s.fault == "source_after_create":
                s.container["Config"]["Cmd"][-1] = "0" * 32

        def attach(self, *, finish_by):
            assert finish_by == p.lease["stop_by"]
            metadata = begins.launches.dispatch.metadata(SimpleNamespace(pins=self.claim.pins))
            metadata["ContainerID"] = self.claim.pins.init.container_id
            self.claim.attach_intent(metadata)
            tick("attach")

        def close(self):
            tick("transport_close")
            self.closed = True
            self.endpoint.close()
            if s.close_error:
                s.close_error = False  # One deliberately lost close acknowledgment.
                raise OSError("PRIVATE close acknowledgment")

    sockets = []
    directory = os.open(s.case_root / "sockets", m.readiness.files.DIRECTORY)

    def bind(name):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sockets.append(sock)
        sock.bind(f"/proc/self/fd/{directory}/{name}")
        os.chmod(name, 0o600, dir_fd=directory)

    def ready_init(ready, client, *, profile_sha256, original_clock, zero_domain):
        tick("ready")
        assert profile_sha256 == "a" * 64 and original_clock is p.original_clock
        assert zero_domain is s.idle.zero_domain
        ready.client, ready.processes = client, object()
        ready.clock, ready.zero_domain = original_clock, zero_domain
        ready.failed = ready.closed = False
        ready.ready_by, ready.received_at = p.lease["ready_by"], time.monotonic()
        ready.watch_deadline = ready.ready_by + p.candidate.contract.maximum_recording_seconds
        ready.ready_raw = b"synthetic original ready, not native authentication"
        ready.context_raw = b.encode(launch.received._context(client.claim.pins, profile_sha256))
        for name in m.readiness.SOCKETS:
            bind(name)
        if s.fault == "extra_socket":
            bind("extra.sock")
        elif s.fault == "early_receipt":
            (s.case_root / "receipts/prepared.json").write_bytes(b"PRIVATE")
        elif s.fault == "ready_context":
            ready.context_raw = b"PRIVATE"
        elif s.fault == "source_at_ready":
            s.container["Config"]["Cmd"][-1] = "0" * 32

    def ready_check(ready):
        tick("ready_check")
        assert not ready.failed and not ready.closed and not ready.client.closed
        assert not ready.client.begun and time.monotonic() < ready.ready_by

    def ready_close(ready):
        tick("actors_close")
        ready.closed = True
        ready.client.close()

    class Probe:
        def __init__(self, ready):
            self.ready, self.closed = ready, False
            self.execution_id, self.request_sha256 = "8" * 64, "9" * 64

        def prepare(self):
            tick("probe_prepare")

        def read(self):
            tick("probe")
            self.ready.check_before_begin()
            if s.fault == "socket_during_probe":
                (s.case_root / "sockets/api.sock").unlink()
                bind("api.sock")
            elif s.fault == "source_during_probe":
                s.container["Config"]["Cmd"][-1] = "0" * 32
            elif s.fault == "old_ready":
                self.ready.received_at -= 3
            return launch.plans.ordinary.NativeState(s.idle.generation, True, False)

        def close(self):
            self.closed = True

    monkeypatch.setattr(launch.engine, "Endpoint", Endpoint)
    monkeypatch.setattr(launch.engine, "Client", Client)
    monkeypatch.setattr(launch.received.Ready, "__init__", ready_init)
    monkeypatch.setattr(launch.received.Ready, "check_before_begin", ready_check)
    monkeypatch.setattr(launch.received.Ready, "close", ready_close)
    monkeypatch.setattr(launch.probe_exec, "Sample", Probe)
    s.endpoint = Endpoint()
    host = s.host = launch.BootstrapHost(p, s.projected, s.idle, s.witness, s.docker)

    def host_read(reader):
        assert reader is host and not reader.failed
        tick("host")
        reader.pending = None
        observed = observation()
        return launch.bootstrap.recovery.Sample(p.boot, now(), observed)

    def prepare(reader):
        assert reader is host and reader.pending is None
        tick("host_prepare")
        reader.pending = True

    def discard(reader):
        assert reader is host
        s.trace.append("host_discard")
        reader.failed = True

    monkeypatch.setattr(launch.BootstrapHost, "__call__", host_read)
    monkeypatch.setattr(launch.BootstrapHost, "prepare", prepare)
    monkeypatch.setattr(launch.BootstrapHost, "discard", discard)
    try:
        with launch.bootstrap.Journal(host_root / "journal") as journal:
            s.journal = journal
            baseline = observation(normal, absent, p.deadlines.issued_at)
            journal.append(p.preparation(baseline, s.projected))

            def append(kind, **fields):
                return journal.append(dict(kind=kind, now=now(), boot_id=p.boot, **fields))

            append(
                "bind_process",
                process=asdict(b.ProcessRecord(b.NORMAL, normal.generation, "8" * 64, 1234, 100)),
            )
            append("request")
            append("observe", observation=asdict(observation(normal, absent)))
            append("bind_execution", container_id="a" * 64, execution_id="1" * 64)
            append("execution_completed", execution_id="1" * 64, exit_code=0)
            append("process_exited", generation=normal.generation)
            append("observe", observation=asdict(observation(c=absent)))
            append("bind_execution", container_id="a" * 64, execution_id="2" * 64)
            append("execution_completed", execution_id="2" * 64, exit_code=0)
            init = s.witness.identity
            append(
                "bind_process",
                process=asdict(
                    b.ProcessRecord(
                        b.CANDIDATE,
                        s.idle.generation,
                        init.container_id,
                        init.pid,
                        init.start_ticks,
                    )
                ),
            )
            append("observe", observation=asdict(observation()))
            assert journal.machine.state.phase == "candidate_idle"

            def make(original=None, **changes):
                run = m.AppLaunch(
                    s.prelaunch if original is None else original,
                    journal,
                    s.endpoint,
                    **(dict(read=host) | changes),
                )
                s.runs.append(run)
                return run

            s.make = make
            yield s
    finally:
        for run in s.runs:
            run.close()
        for sock in sockets:
            sock.close()
        os.close(directory)


def test_combined_explicit_app_transition_qualifies_actual_socket_inventory(execution):
    s = execution
    original = s.plan.raw, s.plan.lease, s.original.consumption
    events = len(s.journal.entries)
    run = s.make()
    assert s.creates == 0 and len(s.journal.entries) == events
    assert run.start_confirmed().healthy is True
    assert s.creates == 1
    assert type(run.qualify) is m.readiness.NativeReadyQualification
    assert run.qualify.prelaunch is s.prelaunch and run.qualify.ready is run.ready
    assert run.qualify.consumption[-1] != s.prelaunch.consumption[-1]
    assert run.qualify.elapsed_seconds < 2
    assert original == (s.plan.raw, s.plan.lease, s.original.consumption)
    assert s.journal.machine.state.phase == "candidate_running"
    assert s.journal.machine.state.authorization_generation is None
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert len(s.journal.entries) == events + 2
    assert not tuple((s.case_root / "receipts").iterdir())
    assert not run.client.begun
    assert s.trace.index("ready") < s.trace.index("probe_prepare") < s.trace.index("probe")
    assert not s.witness.exited()


@pytest.mark.parametrize("copy", [False, True])
def test_second_controller_cannot_acquire_original_execution_slot(execution, copy):
    s = execution
    run = s.make()
    denied(lambda: s.make(s.other if copy else s.prelaunch))
    assert not run.failed and s.original.native_execution_owner is run
    assert not s.host.failed
    assert run.start_confirmed().healthy is True
    assert s.creates == 1


@pytest.mark.parametrize("method", ["start", "confirm_ready"])
def test_split_paths_refuse_without_dispatch_or_retry(execution, method):
    s = execution
    run = s.make()
    denied(getattr(run, method))
    denied(run.start_confirmed)
    denied(s.make)
    assert s.creates == 0 and s.journal.machine.state.phase == "candidate_idle"
    os.fstat(s.witness.fd)


@pytest.mark.parametrize(
    "fault",
    [
        "create",
        "attach",
        "ready",
        "ready_check",
        "probe_prepare",
        "host_prepare",
        "probe",
        "extra_socket",
        "early_receipt",
        "ready_context",
        "source_after_create",
        "source_at_ready",
        "socket_during_probe",
        "source_during_probe",
        "old_ready",
    ],
)
def test_uncertainty_never_publishes_ready_or_retries_and_retains_actors(execution, fault):
    s = execution
    run = s.make()
    s.fault = fault
    denied(run.start_confirmed)
    assert run.failed and run.client.closed and s.host.failed
    assert s.journal.machine.state.ready_evidence_sha256 is None
    assert s.journal.machine.state.authorization_generation is None
    denied(run.start_confirmed)
    denied(s.make)
    assert s.creates == 1
    if run.ready is not None:
        assert run.ready.failed and not run.ready.closed
    os.fstat(s.witness.fd)


@pytest.mark.parametrize(
    "field", ["prelaunch", "journal", "read", "qualify", "endpoint", "projected", "profile_sha256"]
)
def test_original_bindings_cannot_be_substituted_before_dispatch(execution, field):
    s = execution
    run = s.make()
    setattr(run, field, object())
    denied(run.start_confirmed)
    assert s.creates == 0


def test_app_owner_does_not_enter_direct_begin_gate(execution):
    s = execution
    run = s.make()
    assert run.start_confirmed().healthy is True
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    ledger = launch.binding.Ledger(path, run.pins.host, now=time.monotonic())
    begins.denied(lambda: begins.m.Start(run, ledger))
    assert not run.client.begun and not run.failed
    assert s.journal.machine.state.authorization_generation is None


def test_direct_launch_gate_still_refuses_app_qualification(execution):
    s = execution
    run = launch.Launch(
        s.plan,
        s.projected,
        s.journal,
        s.idle,
        s.witness,
        s.endpoint,
        launch_sha256=s.prelaunch.launch_inputs.sha256,
        profile_sha256="a" * 64,
        read=s.host,
        qualify=s.prelaunch,
    )
    s.runs.append(run)
    denied(run.start_confirmed)
    assert s.creates == 0


def test_unreviewed_subclass_cannot_select_app_execution(execution):
    class Other(m.AppLaunch):
        pass

    s = execution
    denied(lambda: Other(s.prelaunch, s.journal, s.endpoint, read=s.host))
    assert not s.original.native_execution_used and s.creates == 0


def test_nested_failure_sanitizes_lost_close_without_automatic_retry(execution):
    s = execution
    run = s.make()
    s.fault, s.close_error = "probe", True
    denied(run.start_confirmed)
    assert run.failed and run.ready.failed and not run.ready.closed
    assert s.trace.count("transport_close") == 1
    denied(run.start_confirmed)
    assert s.trace.count("transport_close") == 1
    assert s.creates == 1 and not s.witness.exited()


@pytest.mark.parametrize("field", ["client", "ready"])
def test_failed_binding_check_closes_actual_original_not_substituted_handle(execution, field):
    s = execution
    run = s.make()
    run.start_confirmed()
    original_client, original_ready = run.client, run.ready
    replacement = SimpleNamespace(close=lambda: pytest.fail("Not an owned handle"))
    setattr(run, field, replacement)
    denied(run.start_confirmed)
    assert original_client.closed and original_ready.failed and not original_ready.closed
    assert run.failed and s.creates == 1


@pytest.mark.parametrize("fault", ["probe_prepare", "probe", "host_prepare"])
def test_fault_injection_actually_reaches_its_requested_boundary(execution, fault):
    s = execution
    run = s.make()
    s.fault = fault
    denied(run.start_confirmed)
    assert fault in s.trace and "ready" in s.trace and s.creates == 1
