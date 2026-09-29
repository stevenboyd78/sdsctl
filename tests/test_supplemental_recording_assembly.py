"""Native process/UDP/Unix/WAV integration; no physical scanner or live handoff."""

import hashlib
import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sds200.audio import AudioChunk
from sds200.daemon_api import DaemonApiErrorCode
from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_event_server import DaemonEventServer
from sds200.daemon_event_stream import DaemonEventStream
from sds200.daemon_ipc import DaemonSocketListener, resolve_daemon_socket_location
from sds200.daemon_pcmu_server import DaemonPcmuServer
from sds200.daemon_process import DaemonProcess
from sds200.daemon_recording import DaemonRecordingManager
from sds200.daemon_recording_file_server import DaemonRecordingFileServer
from sds200.daemon_runtime import DaemonRuntime
from sds200.daemon_server import DaemonApiServer
from sds200.daemon_supplemental_acquisition import (
    DaemonSupplementalAcquisition,
    SupplementalAcquisitionPolicy,
)
from sds200.pcmu_stream import PcmuStream
from sds200.scanner_display_supplemental_transport import SupplementalDeliveryService

from ._supplemental_failure_diagnostics import failure_locations
from .test_audio_sinks import CollectingSink
from .test_daemon_api_recording import request
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_server import connect, read_line
from .test_supplemental_acceptance_launcher import native as native
from .test_supplemental_recording_api import ENTRIES, a, dispatch
from .test_supplemental_recording_schedule import m, s

NAME = "supplemental_recording_assembly"
SPEC = importlib.util.spec_from_file_location(NAME, Path(s.__file__).with_name(NAME + ".py"))
n = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = n
SPEC.loader.exec_module(n)


@contextmanager
def native_bundle(native, tmp_path, *, pcmu=False, policy=None, sockets_directory=None):
    runtime = DaemonRuntime(native.scanner, native.audio, native.router, psi_timeout=1)
    root = tmp_path / "recordings"
    root.mkdir()
    (root / "older.txt").write_bytes(b"older evidence unchanged")
    case = "c" * 32
    evidence = sys.modules["supplemental_recording_evidence"]
    baseline = evidence.capture_baseline(root, case)
    journal = tmp_path / "receipts"
    journal.mkdir(mode=0o700)
    probe = tmp_path / "mode-probe"
    probe.touch(mode=0o666)
    writer = m.Writer(os.geteuid(), os.getegid(), probe.stat().st_mode & 0o777)
    manager = DaemonRecordingManager(runtime, root, template=evidence.template(case))
    api = a.FiniteRecordingApi(runtime, recording_manager=manager)
    api.display_profile = native.profile
    acquisition = DaemonSupplementalAcquisition(
        runtime, native.profile, policy or SupplementalAcquisitionPolicy("Version 1.26.01", 1.0, 1)
    )
    delivery = SupplementalDeliveryService(acquisition.frames, acquisition=acquisition)
    api.display_frames, api.supplemental_display = acquisition.frames, delivery
    socket_scope = (
        TemporaryDirectory(prefix="sds-rec-")
        if sockets_directory is None
        else nullcontext(str(sockets_directory))
    )
    with socket_scope as sockets:

        def listener(name):
            return DaemonSocketListener(resolve_daemon_socket_location(Path(sockets) / name))

        server = DaemonApiServer(listener("api"), api, accept_poll_interval=0.01)
        stream = DaemonEventStream(runtime, recording_manager=manager)
        events = DaemonEventServer(listener("events"), stream, accept_poll_interval=0.01)
        files = DaemonRecordingFileServer(listener("files"), manager, accept_poll_interval=0.01)
        packets = (
            DaemonPcmuServer(
                listener("pcmu"),
                PcmuStream(runtime.audio.stream.transport),
                accept_poll_interval=0.01,
            )
            if pcmu
            else None
        )
        process = DaemonProcess(
            runtime,
            recording_manager=manager,
            api_server=server,
            event_server=events,
            recording_file_server=files,
            pcmu_server=packets,
            poll_interval=0.01,
        )

        def build(**kwargs):
            return n.NativeRecordingAssembly(
                process,
                api,
                acquisition,
                delivery,
                baseline,
                journal,
                writer,
                generation="a" * 64,
                audio_endpoint_sha256=hashlib.sha256(
                    runtime.audio.stream.endpoint.encode()
                ).hexdigest(),
                **kwargs,
            )

        value = SimpleNamespace(
            runtime=runtime,
            manager=manager,
            api=api,
            acquisition=acquisition,
            delivery=delivery,
            process=process,
            root=root,
            journal=journal,
            baseline=baseline,
            build=build,
            peer=native.peer,
            server=server,
            events=events,
            files=files,
            packets=packets,
            socket=Path(sockets) / "api",
        )
        try:
            yield value
        finally:
            acquisition.close()
            server.stop()
            files.stop()
            manager.close()
            runtime.stop()
            events.stop()
            if packets is not None:
                packets.stop()


@pytest.fixture
def rig(native, tmp_path, monkeypatch):
    # The native worker intentionally exports only a fixed failure bit. Retain
    # source locations before a schedule refusal is consumed; do not print
    # exceptions/private values or alter success-path reads, clocks or retries.
    original = n.FiniteRecordingSchedule.run
    reported = set()

    def observed_schedule(owner, cancel):
        try:
            return original(owner, cancel)
        except BaseException as error:
            locations = failure_locations(error)
            if locations and locations not in reported and len(reported) < 4:
                reported.add(locations)
                print("Native assembly schedule refusal locations (no values):\n" + locations)
            raise

    monkeypatch.setattr(n.FiniteRecordingSchedule, "run", observed_schedule)
    with native_bundle(native, tmp_path) as value:
        try:
            yield value
        finally:
            monkeypatch.undo()


def refusal(action):
    with pytest.raises(n.UnconfirmedAssembly) as caught:
        action()
    assert str(caught.value) == n.MESSAGE


def query(peer, op, params=None):
    peer.sendall(json.dumps(request(op, params=params)).encode() + b"\n")
    return json.loads(read_line(peer))


def wait_for_armed(rig, trial):
    # A consumed early refusal is not a late arm and must not be hidden behind
    # the operator's generic wait timeout. Keep the original two-second wait.
    wait_for(lambda: rig.acquisition.status().armed or trial.worker_error)
    assert rig.acquisition.status().armed, "Native assembly refused before acquisition armed"


def run_observed(rig, trial, operator, *, success=True):
    finished = Event()

    def operate():
        try:
            operator(finished)
        except BaseException:
            trial.cancel()
            raise

    with ThreadPoolExecutor(max_workers=1) as pool:
        observation = pool.submit(operate)
        failure = None
        try:
            if success:
                result = trial.run()
            else:
                refusal(trial.run)
                result = None
        except BaseException as error:
            failure = error
        finally:
            finished.set()
        try:
            observation.result(timeout=3)
        except BaseException:
            # Fixed booleans only; no snapshot calls, private state, exception
            # formatting or clock reads after the original cleanup boundary.
            print(
                "Native assembly operator failure phase: "
                f"worker_error={trial.worker_error is True} "
                f"controller_created={trial.controller is not None} "
                f"schedule_created={trial.schedule is not None} "
                f"cleanup_complete={trial.cleanup_complete is True}"
            )
            raise
        if failure is not None:
            raise failure
    return result


@pytest.mark.parametrize("demand", (False, True))
def test_real_native_process_recording_and_explicit_demand(rig, demand):
    trial = rig.build()
    transitions = []
    unsubscribe = rig.manager.on_state(lambda state: transitions.append(state.active))
    observed = []

    def operator(finished):
        wait_for(lambda: trial.ready)
        assert rig.manager.snapshot().status == "idle" and not rig.peer.reads
        with connect(rig.socket) as peer:
            context = query(peer, Op.DISPLAY_SUPPLEMENTAL_CONTEXT)["result"]["context"]
            for _ in range(3):
                query(peer, Op.DISPLAY_SUPPLEMENTAL_FRAME, {"context": context})
            # Binding and cached reads did not arm acquisition.
            assert not rig.acquisition.status().armed and not rig.peer.reads
            trial.request_start()
            refusal(trial.request_start)
            wait_for_armed(rig, trial)
            active = query(peer, Op.RECORDING_STATUS)["result"]
            observed.append(active["active"])
            for op in (Op.RECORDING_START, Op.RECORDING_STOP, Op.DISPLAY_PROFILE_RELOAD):
                assert query(peer, op)["error"]["code"] == DaemonApiErrorCode.AUTHORIZATION_DENIED
            if demand:
                context = query(peer, Op.DISPLAY_SUPPLEMENTAL_CONTEXT)["result"]["context"]
                response = query(
                    peer,
                    Op.DISPLAY_SUPPLEMENTAL_DEMAND,
                    {"context": context, "renewal_id": str(uuid4())},
                )
                assert response["ok"], response
                wait_for(lambda: rig.peer.reads == ["FQK"])
            # Synthetic audio goes through the real stream/fanout/router/recorder.
            while not finished.wait(0.02):
                if rig.manager.snapshot().active:
                    rig.runtime.audio.stream.transport.feed(AudioChunk(b"\x80" * 160))

    try:
        result = run_observed(rig, trial, operator)
    finally:
        unsubscribe()
    assert result.artifact.samples > 0 and result.artifact.old_files == 1
    assert observed == [True] and True in transitions and transitions[-1] is False
    assert trial.cleanup_complete and not trial.worker_error
    assert not rig.runtime.running and not rig.manager.snapshot().active
    assert rig.manager.snapshot().completed_recordings == 1
    assert not rig.server.active and not rig.events.active and not rig.files.active
    assert not rig.socket.exists()
    assert not rig.acquisition.frames.quick_key_worker_status().alive
    assert rig.peer.reads == (["FQK"] if demand else [])
    assert trial.schedule.acquisition_status.read_attempts == int(demand)
    assert trial.schedule.acquisition_status.reason == (
        "quota_exhausted" if demand else "window_expired"
    )
    assert (rig.root / "older.txt").read_bytes() == b"older evidence unchanged"
    assert {p.name for p in rig.journal.iterdir()} == {
        "prepared.json",
        "start-intent.json",
        "started.json",
        "stop-intent.json",
        "stopped.json",
    }
    refusal(trial.run)
    refusal(trial.request_start)


@pytest.mark.parametrize("field", n.UNSUPPORTED)
def test_other_in_process_mutators_refused_before_native_startup(rig, field):
    setattr(rig.process, field, object())
    refusal(rig.build)
    assert not rig.peer.commands and not list(rig.journal.iterdir())
    setattr(rig.process, field, None)


@pytest.mark.parametrize(
    "field",
    ("api", "event_manager", "event_runtime", "file_manager", "runtime", "signals", "template"),
)
def test_mismatched_owners_refused_before_native_startup(rig, monkeypatch, field):
    if field == "api":
        monkeypatch.setattr(rig.server, "api", object())
    elif field == "event_manager":
        monkeypatch.setattr(rig.events.stream, "recording_manager", None)
    elif field == "event_runtime":
        monkeypatch.setattr(rig.events.stream, "runtime", object())
    elif field == "file_manager":
        monkeypatch.setattr(rig.files, "recording_manager", object())
    elif field == "template":
        from dataclasses import replace

        monkeypatch.setattr(
            rig.manager,
            "path_policy",
            replace(rig.manager.path_policy, template="other-{timestamp}.wav"),
        )
    else:
        monkeypatch.setattr(rig.process, field, object())
    refusal(rig.build)
    assert not rig.peer.commands and not list(rig.journal.iterdir())


@pytest.mark.parametrize("timeout", (0, True, float("nan"), 601))
def test_invalid_timeout_cannot_start_native_components(rig, timeout):
    refusal(lambda: rig.build(ready_timeout=timeout))
    assert not rig.peer.commands


def test_readiness_alone_times_out_without_recording_or_reads(rig):
    trial = rig.build(ready_timeout=0.3)
    refusal(trial.request_start)
    refusal(trial.run)
    assert trial.cleanup_complete and trial.worker_error and trial.controller is None
    assert rig.manager.snapshot().completed_recordings == 0 and not rig.peer.reads
    assert not list(rig.journal.iterdir())
    assert [p.name for p in rig.root.iterdir()] == ["older.txt"]


@pytest.mark.parametrize("fault", ("cancel", "signal", "connection", "old_file", "metadata"))
def test_failures_preserve_evidence_without_success(rig, monkeypatch, fault):
    trial = rig.build()
    if fault == "metadata":
        from sds200 import daemon_recording

        def fail(*_args, **_kwargs):
            raise OSError("PRIVATE_METADATA_PATH")

        monkeypatch.setattr(daemon_recording, "write_recording_metadata", fail)

    def operator(finished):
        wait_for(lambda: trial.ready)
        trial.request_start()
        wait_for_armed(rig, trial)
        rig.runtime.audio.stream.transport.feed(AudioChunk(b"\x80" * 160))
        if fault == "cancel":
            trial.cancel()
        elif fault == "signal":
            rig.process.signals.request_stop()
        elif fault == "connection":
            rig.acquisition._end("connection_ended")
        elif fault == "old_file":
            (rig.root / "older.txt").write_bytes(b"changed")
        else:
            finished.wait(5)

    run_observed(rig, trial, operator, success=False)
    assert trial.result is None and trial.worker_error
    assert not rig.runtime.running and not rig.manager.snapshot().active
    assert list(rig.root.glob("*.wav")) and (rig.journal / "start-intent.json").exists()
    assert not (rig.journal / "stopped.json").exists()
    refusal(trial.run)


def test_original_control_contention_refuses_arm_and_preserves_evidence(rig, capsys):
    trial = rig.build()
    observed = []

    def operator(_finished):
        wait_for(lambda: trial.ready)
        # Hold the actual original reservation lock in the operator thread.
        # No fake clock/scope/arm reply or retry makes this refusal pass.
        with rig.runtime._control_lock:
            trial.request_start()
            with pytest.raises(AssertionError, match="refused before acquisition armed"):
                wait_for_armed(rig, trial)
            state = rig.acquisition.status()
            assert state.ended and not state.armed and state.reason == "preflight_refused"
            assert not rig.acquisition.arm()
            observed.append(state)

    run_observed(rig, trial, operator, success=False)
    assert len(observed) == 1 and trial.worker_error and trial.cleanup_complete
    assert trial.result is None and not rig.runtime.running
    assert not rig.manager.snapshot().active and not rig.peer.reads
    assert (rig.root / "older.txt").read_bytes() == b"older evidence unchanged"
    assert list(rig.root.glob("*.wav")) and (rig.journal / "start-intent.json").exists()
    assert not (rig.journal / "stopped.json").exists()
    assert trial.controller.phase == "closed" and trial.schedule.phase == "unconfirmed"
    refusal(trial.run)
    output = capsys.readouterr().out
    assert "Native assembly schedule refusal locations (no values):" in output
    assert "scripts/supplemental_recording_schedule.py:" in output
    assert str(rig.root) not in output and str(rig.journal) not in output


@pytest.mark.parametrize("entry", ENTRIES)
def test_bound_demand_still_obeys_peer_permissions_and_mutation_denials(rig, entry):
    rig.build()
    for operation in Op:
        if operation in a.OBSERVATIONS or operation == Op.DISPLAY_SUPPLEMENTAL_DEMAND:
            continue
        response = json.loads(dispatch(rig.api, entry, request(operation)))
        assert response["error"]["code"] == DaemonApiErrorCode.AUTHORIZATION_DENIED
    response = json.loads(
        dispatch(
            rig.api,
            "authorized",
            request(Op.DISPLAY_SUPPLEMENTAL_DEMAND),
            allowed_operations=(Op.PING,),
        )
    )
    assert response["error"]["code"] == DaemonApiErrorCode.AUTHORIZATION_DENIED
    assert not rig.peer.commands


@pytest.mark.parametrize("replacement", ("frames", "service", "owner", "runtime_owner"))
def test_bound_api_fails_closed_on_changed_acquisition(rig, monkeypatch, replacement):
    rig.build()
    target, attr = {
        "frames": (rig.api, "display_frames"),
        "service": (rig.api, "supplemental_display"),
        "owner": (rig.delivery, "_acquisition"),
        "runtime_owner": (rig.runtime, "_supplemental_acquisition"),
    }[replacement]
    monkeypatch.setattr(target, attr, object())
    response = rig.api.handle_payload(request(Op.PING))
    assert response.error.code == DaemonApiErrorCode.INTERNAL_ERROR
    assert not rig.peer.commands


def test_failed_acquisition_binding_is_terminal(rig):
    with pytest.raises(ValueError):
        rig.api.bind_acquisition(object(), rig.delivery)
    with pytest.raises(ValueError, match="retried"):
        rig.api.bind_acquisition(rig.acquisition, rig.delivery)
    assert rig.api.handle_payload(request(Op.PING)).error.code == DaemonApiErrorCode.INTERNAL_ERROR
    assert not rig.peer.commands


@pytest.mark.parametrize("extra", ("fanout", "router"))
def test_existing_pcm_sinks_cannot_hide_a_second_writer(rig, monkeypatch, extra):
    sink = CollectingSink("unreviewed-destination")
    if extra == "fanout":
        monkeypatch.setattr(rig.runtime.audio, "sinks", (rig.runtime.router, sink))
    else:
        rig.runtime.router.attach(sink)
    refusal(rig.build)
    assert not rig.peer.commands and not list(rig.journal.iterdir())


def test_stop_request_survives_delayed_native_signal_installation(rig, monkeypatch):
    trial = rig.build(ready_timeout=0.025)
    original = rig.process.run

    def delayed():
        Event().wait(0.1)
        return original()

    monkeypatch.setattr(rig.process, "run", delayed)
    refusal(trial.run)
    assert trial.cleanup_complete and trial.worker_error
    assert rig.manager.snapshot().completed_recordings == 0
    assert not rig.peer.reads and not list(rig.journal.iterdir())


def test_an_already_running_audio_source_is_not_reused(rig, monkeypatch):
    monkeypatch.setattr(rig.runtime.audio.stream.transport, "_running", True)
    refusal(rig.build)
    assert not rig.peer.commands and not list(rig.journal.iterdir())
