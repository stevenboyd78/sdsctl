"""Dedicated native construction; only temporary files and loopback networking."""

import hashlib
import importlib.util
import os
import socket
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sds200.audio_recording import decode_mulaw
from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_recording import DaemonRecordingManager
from sds200.network_audio import NetworkAudioTransport
from sds200.rtsp import RtpTransportInfo

from . import test_daemon_display_frames as profiles
from . import test_supplemental_recording_assembly as assembly
from . import test_supplemental_recording_protected as protection
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_server import connect
from .test_network_audio import FakeRtspClient, make_rtp
from .test_supplemental_native_lifecycle import LoopbackScanner

NAME = "supplemental_recording_construction"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(assembly.n.__file__).with_name(NAME + ".py")
)
c = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = c
SPEC.loader.exec_module(c)
tree, configured = protection.tree, profiles.configured


def test_passive_plan_import_does_not_load_construction_only_services():
    """A fresh isolated interpreter proves the light path, not warm sys.modules."""
    root = Path(__file__).resolve().parents[1]
    script = r"""
import sys, socket, threading
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / "scripts"), str(root / "src")]
def forbidden(*args, **kwargs):
    raise AssertionError("Passive import attempted activity")
socket.socket.connect = socket.socket.bind = socket.socket.listen = forbidden
threading.Thread.start = forbidden
import supplemental_recording_launch_plan as launch
spec = launch.construction.Specification(
    "127.0.0.1", 50536, 554, "127.0.0.1", 0,
    Path("/tmp/passive-sockets"), Path("/tmp/passive-receipts"),
    "Version 1.26.01", 1.0, 2, 3,
)
assert spec.policy().window_seconds == 1.0
assert not {
    "supplemental_recording_api", "supplemental_recording_assembly",
    "sds200.daemon_api", "sds200.daemon_server", "sds200.daemon_process",
} & sys.modules.keys()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(root)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.fixture
def prepared(tree, configured, tmp_path):
    with TemporaryDirectory(prefix="finite-build-") as local:
        base = Path(local)
        sockets, receipts = base / "sockets", base / "receipts"
        sockets.mkdir(mode=0o700)
        receipts.mkdir(mode=0o700)
        spec = c.Specification(
            "127.0.0.1",
            50536,
            554,
            "127.0.0.1",
            0,
            sockets,
            receipts,
            "Version 1.26.01",
            1.0,
            2,
            3,
        )
        endpoint = NetworkAudioTransport(spec.host).endpoint
        # Capture a separate manifest for this fixture's real native endpoint.
        manifest = tmp_path / "construction-manifest"
        manifest.mkdir(mode=0o700)
        probe = tmp_path / "writer-mode"
        probe.touch(mode=0o666)
        writer = c.protected.monitor.Writer(
            os.geteuid(), os.getegid(), probe.stat().st_mode & 0o777
        )
        stored = protection.p.save_baseline(
            manifest, tree.baseline, writer, hashlib.sha256(endpoint.encode()).hexdigest()
        )
        config = replace(configured, scanner_target="udp://127.0.0.1:50536")
        yield SimpleNamespace(
            spec=spec,
            stored=stored,
            config=config,
            tree=tree,
            generation="a" * 64,
        )


def build(prepared, **changes):
    return c.construct(
        changes.pop("specification", prepared.spec),
        changes.pop("stored", prepared.stored),
        changes.pop("configuration", prepared.config),
        generation=prepared.generation,
        **changes,
    )


def refused(action):
    with pytest.raises(c.UnconfirmedConstruction) as caught:
        action()
    assert str(caught.value) == c.MESSAGE and caught.value.__suppress_context__
    assert "PRIVATE" not in str(caught.value)


def enter(prepared, **changes):
    with build(prepared, **changes):
        pass


def test_construction_is_passive_and_closes_only_unstarted_resources(prepared, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Construction cannot open a network socket or start a scanner")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/PRIVATE_unrelated")
    with build(prepared) as trial:
        assert type(trial) is assembly.n.NativeRecordingAssembly
        assert not trial.runtime.running and not trial.runtime.scanner.connected
        assert not trial.manager.snapshot().active and not trial.ready
        assert not trial.acquisition.status().armed and not trial.acquisition._started
        assert trial.runtime.audio.stream.transport is trial.process.pcmu_server.stream.source
        assert trial.runtime is trial.manager.runtime is trial.api.runtime
        assert trial.api.display_profile.scanner_target == prepared.config.scanner_target
        assert all(getattr(trial.process, name) is None for name in assembly.n.UNSUPPORTED)
        assert list(prepared.spec.sockets.iterdir()) == []
        assert list(prepared.spec.receipts.iterdir()) == []
    assert trial.manager.snapshot().closed
    assert trial.process.event_server.stream.closed and trial.process.pcmu_server.stream.closed
    assert not trial.runtime.running and not trial.runtime.audio.stream.transport.running
    assert list(prepared.spec.receipts.iterdir()) == []
    assert protection.p.Collector(prepared.stored).pristine().files.stage == "pristine"


@pytest.mark.parametrize(
    "host", ["192.168.1.10", "scanner", "scanner.example.test", "RADIO-1.local"]
)
def test_specification_accepts_addresses_and_hostnames_without_dns(prepared, monkeypatch, host):
    def forbidden(*_):
        pytest.fail("Validation must not resolve a hostname")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    assert replace(prepared.spec, host=host, rtp_bind_port=50000).host == host


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", ""),
        ("host", " PRIVATE_host"),
        ("host", "http://scanner"),
        ("host", "user@scanner"),
        ("host", "scanner/path"),
        ("host", "a..b"),
        ("host", "256.1.2.3"),
        ("host", "127.1"),
        ("host", "01.2.3.4"),
        ("host", "-scanner"),
        ("host", "a" * 64),
        ("host", "::1"),
        ("control_port", True),
        ("control_port", 0),
        ("control_port", 65536),
        ("rtsp_port", "554"),
        ("rtsp_port", -1),
        ("rtp_bind_port", True),
        ("rtp_bind_port", -1),
        ("rtp_bind_address", "::"),
        ("rtp_bind_address", "localhost"),
        ("rtp_bind_address", "0.0.0.0"),  # Ephemeral RTP is loopback-only.
        ("sockets", Path("relative")),
        ("sockets", Path("/")),
        ("receipts", "/tmp/text"),
        ("sockets", Path("/tmp") / ("x" * 100)),
        ("firmware", ""),
        ("read_window_seconds", True),
        ("read_window_seconds", 76),
        ("max_read_attempts", 0),
        ("max_read_attempts", 151),
        ("ready_timeout", float("nan")),
        ("ready_timeout", 0),
        ("ready_timeout", 601),
    ],
)
def test_invalid_specification_is_sanitized_and_side_effect_free(prepared, field, value):
    refused(lambda: replace(prepared.spec, **{field: value}))
    assert list(prepared.spec.sockets.iterdir()) == list(prepared.spec.receipts.iterdir()) == []


@pytest.mark.parametrize(
    "field",
    [
        "destination_config",
        "mqtt_config",
        "remote_config",
        "live_audio_socket",
        "waterfall",
        "daemon_arguments",
    ],
)
def test_ordinary_daemon_options_cannot_be_silently_ignored(prepared, field):
    with pytest.raises(TypeError):
        replace(prepared.spec, **{field: "PRIVATE"})


@pytest.mark.parametrize(
    "fault", ["target", "endpoint", "old_file", "short_contract", "writer", "generation", "stored"]
)
def test_bound_profile_baseline_endpoint_and_process_inputs_required(prepared, fault):
    changes = {}
    if fault == "target":
        changes["configuration"] = replace(prepared.config, scanner_target="udp://other:50536")
    elif fault in ("endpoint", "short_contract", "writer"):
        raw = protection.p.manifest_bytes(
            prepared.stored.baseline,
            replace(prepared.stored.writer, uid=prepared.stored.writer.uid + 1)
            if fault == "writer"
            else prepared.stored.writer,
            "f" * 64 if fault == "endpoint" else prepared.stored.contract.audio_endpoint_sha256,
            maximum_recording_seconds=5 if fault == "short_contract" else 180,
        )
        changes["stored"] = protection.p._decode(raw)
    elif fault == "generation":
        prepared.generation = "private unbound generation"
    elif fault == "stored":
        changes["stored"] = replace(prepared.stored, manifest_sha256="f" * 64)
    else:
        (prepared.tree.root / "older/old.wav").write_bytes(b"changed")
    refused(lambda: enter(prepared, **changes))
    assert list(prepared.spec.sockets.iterdir()) == list(prepared.spec.receipts.iterdir()) == []


@pytest.mark.parametrize(
    "fault", ["same", "nested", "recordings", "profile", "public", "used", "symlink"]
)
def test_private_directories_cannot_overlap_or_adopt_old_state(prepared, fault):
    changes = {}
    if fault == "same":
        changes["receipts"] = prepared.spec.sockets
    elif fault == "nested":
        child = prepared.spec.sockets / "child"
        child.mkdir(mode=0o700)
        changes["receipts"] = child
    elif fault == "recordings":
        changes["receipts"] = prepared.tree.root
    elif fault == "profile":
        changes["receipts"] = prepared.config.state_directory
    elif fault == "public":
        prepared.spec.receipts.chmod(0o755)
    elif fault == "used":
        (prepared.spec.receipts / "previous.json").write_text("PRIVATE")
    else:
        moved = prepared.spec.receipts.with_name("old")
        prepared.spec.receipts.rename(moved)
        prepared.spec.receipts.symlink_to(moved, target_is_directory=True)
    refused(lambda: enter(prepared, specification=replace(prepared.spec, **changes)))
    if fault == "used":
        assert (prepared.spec.receipts / "previous.json").read_text() == "PRIVATE"


@pytest.mark.parametrize(
    "point", ["profile", "manager", "acquisition", "events", "pcmu", "assembly"]
)
def test_construction_fault_unwinds_only_objects_already_built(prepared, monkeypatch, point):
    made = []
    services = c._services()
    monkeypatch.setattr(c, "_services", lambda: services)
    for name in (
        "SDS200",
        "NetworkAudioTransport",
        "DaemonRecordingManager",
        "DaemonEventStream",
        "PcmuStream",
    ):
        # Preserve concrete classes used by strict native bindings. Track the
        # returned actual instances without replacing their types.
        if name == "SDS200":
            original = services.SDS200.from_transport

            def from_transport(*args, original=original, **kwargs):
                value = original(*args, **kwargs)
                made.append(value)
                return value

            monkeypatch.setattr(services.SDS200, "from_transport", from_transport)
        else:
            original = getattr(services, name)

            def make(*args, original=original, **kwargs):
                value = original(*args, **kwargs)
                made.append(value)
                return value

            monkeypatch.setattr(services, name, make)
    target = {
        "profile": "DaemonDisplayProfile",
        "manager": "DaemonRecordingManager",
        "acquisition": "DaemonSupplementalAcquisition",
        "events": "DaemonEventStream",
        "pcmu": "PcmuStream",
        "assembly": "NativeRecordingAssembly",
    }[point]

    def fail(*_, **__):
        raise RuntimeError("PRIVATE construction failure")

    monkeypatch.setattr(services, target, fail)
    refused(lambda: enter(prepared))
    assert not made[0].connected
    for value in made:
        if isinstance(value, DaemonRecordingManager):
            assert value.snapshot().closed
        if hasattr(value, "closed"):
            assert value.closed
        if isinstance(value, NetworkAudioTransport):
            assert not value.running
    assert not list(prepared.spec.sockets.iterdir())
    assert not list(prepared.spec.receipts.iterdir())


def test_attempted_uncertain_native_lifecycle_is_not_cleaned_up_twice(prepared, monkeypatch):
    calls = []
    with pytest.raises(c.UnconfirmedConstruction), build(prepared) as trial:
        # Model the ownership boundary only, without starting native I/O.
        trial._attempted = True
        monkeypatch.setattr(trial.manager, "close", lambda: calls.append("stop"))
    assert calls == []
    # Fixture owns these never-started objects; explicit cleanup is not a live retry.
    monkeypatch.undo()
    trial.acquisition.close()
    trial.manager.close()
    trial.process.event_server.stream.close()
    trial.process.pcmu_server.stream.close()
    trial.runtime.scanner.close()


@pytest.mark.parametrize("demand", [False, True])
def test_constructed_native_run_records_real_loopback_rtp_and_obeys_explicit_gate(prepared, demand):
    scanner = LoopbackScanner()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.bind(("127.0.0.1", 0))

            class Rtsp(FakeRtspClient):
                def start(self, client_port):
                    self.started_ports.append(client_port)
                    return RtpTransportInfo(
                        source="127.0.0.1", server_port=sender.getsockname()[1], ssrc=5678
                    )

            rtsp = Rtsp()
            spec = replace(prepared.spec, control_port=scanner.socket.getsockname()[1])
            config = replace(prepared.config, scanner_target=f"udp://127.0.0.1:{spec.control_port}")
            finished = Event()
            samples = []
            with build(prepared, specification=spec, configuration=config) as trial:
                source = trial.runtime.audio.stream.transport
                source._rtsp_client_factory = lambda *_: rtsp  # Synthetic negotiation only.

                def operator():
                    try:
                        wait_for(lambda: trial.ready)
                        # Native runtime startup owns one shared audio session;
                        # readiness alone must not start recording or GETs.
                        assert len(rtsp.started_ports) == 1 and not scanner.reads
                        assert not trial.manager.snapshot().active
                        with connect(spec.sockets / "api.sock") as peer:
                            assert (
                                assembly.query(peer, Op.RECORDING_STATUS)["result"]["active"]
                                is False
                            )
                            trial.request_start()
                            wait_for(lambda: trial.acquisition.status().armed)
                            assert assembly.query(peer, Op.RECORDING_START)["ok"] is False
                            assert assembly.query(peer, Op.RECORDING_STOP)["ok"] is False
                            if demand:
                                context = assembly.query(peer, Op.DISPLAY_SUPPLEMENTAL_CONTEXT)[
                                    "result"
                                ]["context"]
                                assert assembly.query(
                                    peer,
                                    Op.DISPLAY_SUPPLEMENTAL_DEMAND,
                                    {"context": context, "renewal_id": str(uuid4())},
                                )["ok"]
                            payload = bytes(range(160))
                            for index in range(8):
                                sender.sendto(
                                    make_rtp(
                                        payload, sequence=100 + index, timestamp=1000 + index * 160
                                    ),
                                    ("127.0.0.1", rtsp.started_ports[0]),
                                )
                            wait_for(lambda: trial.manager.snapshot().samples == 1280)
                            samples.append(trial.manager.snapshot().samples)
                            if demand:
                                wait_for(lambda: bool(scanner.reads))
                        finished.wait(6)
                    except BaseException:
                        trial.cancel()
                        raise

                with ThreadPoolExecutor(max_workers=1) as pool:
                    observed = pool.submit(operator)
                    failure = None
                    try:
                        result = trial.run()
                    except BaseException as error:
                        failure = error
                    finally:
                        finished.set()
                    observed.result(timeout=3)
                    if failure is not None:
                        raise failure
                assert result.artifact.samples == 1280
                assert samples == [1280] and trial.cleanup_complete
                assert bool(scanner.reads) == demand
            assert len(rtsp.started_ports) == rtsp.teardowns == 1 and rtsp.closed
            assert not source.running and not scanner.errors
            assert scanner.commands[-1] == "PSI,0"
            wavs = list(prepared.tree.root.glob("sdsctl-acceptance-*.wav"))
            assert len(wavs) == 1
            with wave.open(str(wavs[0]), "rb") as wav:
                assert wav.readframes(1280) == decode_mulaw(bytes(range(160))) * 8
            assert (prepared.tree.root / "older/old.wav").read_bytes() == b"old evidence unchanged"
    finally:
        scanner.close()
