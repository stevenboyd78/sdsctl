"""Original App launch/begin/finalized chain consumes actual native returns.

Full original source/profile/baseline inputs, fixed paths, native processes,
transport, Ready parser, original App qualification/controller, ledger, WAV and
worker-exit evidence are real. Only recording variants mount the fixture's
predeclared recording directory writable; old evidence is retained unchanged.
Platform/Startup/image and pre-launch handoff/host-health/probe metadata remain
synthetic. This is NOT a full AppService/observer/recovery or installed trial.
Only disposable namespace paths and owned loopback peers are used.
"""

import json
import os
import time
import wave
from contextlib import contextmanager

import pytest

from . import test_supplemental_recording_app_actual_ready as actual
from . import test_supplemental_recording_app_begin as begins
from . import test_supplemental_recording_app_execution as execution
from . import test_supplemental_recording_app_finalized as finalized

bwrap, staged, mapped = actual.bwrap, actual.staged, actual.mapped
layout, image_umask, supervised = actual.layout, actual.image_umask, actual.supervised
image, configured = actual.image, actual.configured
candidate, app, native, launch_case = (
    actual.candidate,
    actual.app,
    actual.native,
    actual.launch_case,
)
pytestmark = actual.pytestmark


def route_recordings(s, mapped, monkeypatch):
    """Map only the declared synthetic host mount to its owned native fixture."""
    host_root = s.projected.host.baseline.root

    def routed(function):
        def collect(root, *args, **kwargs):
            return function(mapped.recordings if root == host_root else root, *args, **kwargs)

        return collect

    files = begins.begin.relayed.host.protected
    for owner, name in (
        (files.evidence, "opened_root"),
        (files.evidence, "inventory"),
        (files.monitor, "opened_root"),
    ):
        monkeypatch.setattr(owner, name, routed(getattr(owner, name)))


@pytest.fixture
def original_launch(launch_case, mapped, staged, tmp_path, monkeypatch, request):
    launch = execution.launch
    # Preserve actual implementations BEFORE the legacy fixture installs its
    # synthetic native adapters. Keep only that fixture's explicit host/probe
    # metadata, journal preparation and ownership/lifetime assembly.
    original = (
        (launch.engine, "Endpoint", launch.engine.Endpoint),
        (launch.engine, "Client", launch.engine.Client),
        (launch.received.Ready, "__init__", launch.received.Ready.__init__),
        (launch.received.Ready, "check_before_begin", launch.received.Ready.check_before_begin),
        (launch.received.Ready, "close", launch.received.Ready.close),
        (launch.engine.namespace.Witness, "refresh", launch.engine.namespace.Witness.refresh),
    )
    with contextmanager(execution.setup_execution)(
        launch_case,
        tmp_path,
        monkeypatch,
        service_factory=getattr(launch_case, "service_factory", None),
    ) as s:
        for owner, name, value in original:
            monkeypatch.setattr(owner, name, value)
        record = getattr(request, "param", False)
        if record:
            route_recordings(s, mapped, monkeypatch)
        with actual.native_engine(
            s,
            mapped,
            staged,
            s.prelaunch,
            monkeypatch,
            controller=True,
            recording=bool(record),
            fault=record if type(record) is str else None,
        ) as io:
            s.endpoint = io.endpoint
            io.run = s.make()
            yield s, io


def test_original_app_launch_consumes_actual_ready_and_retains_original_claim(
    original_launch, mapped
):
    s, io = original_launch
    run = io.run
    original = s.plan.raw, s.plan.lease, s.native_baseline
    before = len(s.journal.entries)
    assert not io.requests and run.ready is None
    evidence = run.start_confirmed()
    assert evidence.healthy is True
    assert len(io.requests) == 5
    assert run.client.claim is run.claim and run.claim.pins is run.pins
    assert run.claim.directory == s.plan.root / "operator-exec"
    assert run.qualify.ready is run.ready and run.qualify.prelaunch is s.prelaunch
    assert run.ready.clock is s.plan.original_clock and not run.ready.client.attachment.begun
    assert json.loads(run.ready.context_raw)["launch"] == s.prelaunch.launch_inputs.sha256
    assert s.original.native_execution_owner is run
    assert s.original.native_ready_owner is run.qualify
    assert [entry["event"]["kind"] for entry in s.journal.entries[before:]] == [
        "authorize_operator",
        "operator_ready",
    ]
    assert s.journal.machine.state.phase == "candidate_running"
    assert s.journal.machine.state.authorization_generation is None
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert not tuple((s.case_root / "receipts").iterdir()) and not mapped.scanner.reads
    assert original == (s.plan.raw, s.plan.lease, s.native_baseline)
    assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"


@pytest.mark.parametrize("fault", ["claim", "helper_mode", "receipt"])
def test_actual_ready_drift_consumes_original_launch_without_publishing_ready_or_retry(
    original_launch, mapped, monkeypatch, fault
):
    s, io = original_launch
    probe = execution.launch.probe_exec.Sample
    prepare = probe.prepare
    changed = []

    def drift(current):
        prepare(current)
        assert current.ready is io.run.ready
        changed.append(current)
        if fault == "claim":
            path = s.case_root / "launch/guardian/launch-claimed.json"
            value = json.loads(path.read_bytes())
            value["guardian_start_ticks"] += 1
            path.write_bytes(execution.b.encode(value))
        elif fault == "helper_mode":
            (s.root / actual.m.launch.plans.host.candidate_static.NATIVE).chmod(0o775)
        else:
            (s.case_root / "receipts/unexpected.json").write_bytes(b"fixture drift")

    monkeypatch.setattr(probe, "prepare", drift)
    execution.denied(io.run.start_confirmed)
    assert len(changed) == 1 and len(io.requests) == 5
    assert io.run.failed and io.run.ready.failed and io.run.client.closed
    assert not io.run.ready.closed  # Retain the exact actor handles for recovery.
    assert s.journal.machine.state.phase == "starting_operator"
    assert s.journal.machine.state.ready_evidence_sha256 is None
    assert s.journal.machine.state.authorization_generation is None
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    execution.denied(io.run.start_confirmed)
    execution.denied(s.make)
    assert len(io.requests) == 5 and len(changed) == 1 and not mapped.scanner.reads
    os.fstat(s.witness.fd)
    assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"


def test_actual_ready_cannot_begin_when_original_observer_acknowledgment_is_lost(
    original_launch, mapped
):
    s, io = original_launch
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    ledger = begins.begin.binding.Ledger(path, io.run.pins.host, now=time.monotonic())
    assert io.run.start_confirmed().healthy
    notices = []

    def lost(notice):
        notices.append(notice)
        assert notice.pins is io.run.pins
        assert notice.actors == io.run.ready.processes.actors
        assert notice.history == tuple(execution.b.encode(e) for e in s.journal.entries)
        assert notice.dispatch_sha256 == io.run.claim.state.sha256
        assert ledger.state.count == 1 and not io.run.client.attachment.begun
        # Deliberate fixture failure, not an independent observer attestation.
        raise OSError("Fixture observer acknowledgment lost")

    start = begins.m.AppStart(io.run, ledger, native_observer=lost)
    try:
        begins.denied(start.start_once)
        assert len(notices) == 1 and start.native_observation_attempted
        assert start.failed and io.run.failed and io.run.client.closed
        assert ledger.state.count == 1 and ledger.state.expected is None
        assert s.journal.machine.state.authorization_generation is None
        assert s.journal.machine.state.recording_outcome == "not_attempted"
        assert start.intent is None and start.relay is None
        begins.denied(start.start_once)
        assert len(notices) == 1 and len(io.requests) == 5
        assert not mapped.scanner.reads and not tuple((s.case_root / "receipts").iterdir())
        assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
    finally:
        start.close()


@pytest.mark.parametrize("original_launch", [True, "lost_completed", "finalized"], indirect=True)
def test_original_app_authorization_reaches_actual_recording_without_inferred_recovery(
    original_launch, mapped, request
):
    s, io = original_launch
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    ledger = io.ledger = begins.begin.binding.Ledger(path, io.run.pins.host, now=time.monotonic())
    assert io.run.start_confirmed().healthy
    original = s.plan.raw, s.plan.lease, io.run.ready.ready_raw
    mode = request.node.callspec.params["original_launch"]
    start = audit = observer = reader = None
    try:
        if mode == "finalized":
            io.handlers.insert(5, io.metadata)
            io.handlers.extend([io.exited_metadata, io.exited_metadata])
            audit = actual.engine.m.Endpoint()
            observer = begins.begin.worker_exit.reconcile.Operator(s.plan, io.run.ready, audit)
            assert not set(observer.handles.values()).intersection(
                io.run.ready.processes.handles.values()
            )
        start = begins.m.AppStart(io.run, ledger)
        relay = start.start_once()
        assert start.authorization["kind"] == "authorize_recording" and start.intent.count == 2
        assert relay is start.relay and relay.ready is io.run.ready
        relay.started()
        assert ledger.state.count == 3 and ledger.state.expected is not None
        for index in range(8):
            mapped.rtsp.packets.sendto(
                actual.io.child.construction.make_rtp(
                    bytes(range(160)), sequence=100 + index, timestamp=1000 + index * 160
                ),
                mapped.rtsp.target,
            )
        if mode == "lost_completed":
            with pytest.raises(begins.begin.relayed.UnconfirmedRelay):
                relay.completed()
            assert not ledger.state.closed and ledger.state.acknowledgment is None
            assert relay.phase == "unconfirmed" and len(io.requests) == 5
        else:
            completion = relay.completed()
            assert completion.collected.artifact.samples == 1280
            assert ledger.state.closed and ledger.state.acknowledgment is not None
            if mode == "finalized":
                reader = finalized.m.AppAuthorizedFinalized(start, observer)
                exited = reader.collect_exit()
                receipt = observer.poll()
                assert receipt.returncode == 0 and not receipt.init_exited
                io.run.ready.close()
                digest = reader.publish_exit()
                assert digest == observer.result_sha256
                assert s.journal.machine.state.operator_exit_sha256 == digest
                assert s.journal.machine.state.finish_requested
                assert reader.phase == "published" and len(io.requests) == 9
            else:
                exited = actual.io.m.collect(relay)
                assert len(io.requests) == 6
            assert exited.guardian_pid == io.envelopes[0]["guardian"]["pid"]
            assert relay.phase == "exited"
        assert original == (s.plan.raw, s.plan.lease, io.run.ready.ready_raw)
        assert not s.witness.exited()  # Worker exit is NOT candidate init exit or restoration.
        assert s.journal.machine.state.recording_outcome == "unconfirmed"
        if mode != "finalized":
            assert s.journal.machine.state.operator_exit_sha256 is None
        recordings = list(mapped.recordings.glob("sdsctl-acceptance-*.wav"))
        assert len(recordings) == 1
        with wave.open(str(recordings[0]), "rb") as saved:
            assert saved.getnframes() == 1280
        assert (mapped.recordings / "previous.wav").read_bytes() == b"old evidence unchanged"
        assert [frame["phase"] for frame in io.envelopes] == [
            "ready",
            "started",
            "completed",
            "exited",
        ]
    finally:
        if reader is not None:
            reader.close()
        if start is not None:
            start.close()
        if observer is not None:
            observer.close()
        if audit is not None:
            audit.close()
