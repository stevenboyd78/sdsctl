"""New explicit-demand acceptance assembly; synthetic loopback scanner only."""

import importlib.util
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sds200 import AudioFanoutSession, AudioStream, PcmSinkRouter, cli, daemon_display_frames
from sds200.daemon_api import DaemonReadOnlyApi
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_process import DaemonProcess, DaemonSignalController
from sds200.daemon_runtime import DaemonRuntime
from sds200.daemon_supplemental_acquisition import SupplementalAcquisitionPolicy
from sds200.network import UdpTransport
from sds200.radio import SDS200
from sds200.scanner_display_supplemental_transport import SupplementalDeliveryService

from .fakes import FakeAudioTransport
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_supplemental_acquisition import rig as rig
from .test_supplemental_native_lifecycle import LoopbackScanner

SPEC = importlib.util.spec_from_file_location(
    "accept_supplemental_daemon",
    Path(__file__).resolve().parents[1] / "scripts/accept_supplemental_daemon.py",
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)
REVISION = "a" * 40
POLICY = SupplementalAcquisitionPolicy("Version 1.26.01", 0.25, 6)


def trigger(tmp_path, *, policy=POLICY, **options):
    return launcher.AcceptanceTrigger(
        tmp_path / "case", policy, source_revision=REVISION, ready_timeout=2, **options
    )


def report(case, name="result.json"):
    return launcher.read_private(case.directory / name)


def request(case, **changes):
    launcher.publish(case.directory, "arm.json", case.identity | changes)
    case.signal(signal.SIGUSR1, None)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_revision", "bad"),
        ("ready_timeout", 0),
        ("ready_timeout", True),
        ("ready_timeout", float("nan")),
        ("ready_timeout", 601),
        ("policy", object()),
    ],
)
def test_bad_configuration_does_not_create_case(tmp_path, field, value):
    options = dict(policy=POLICY, source_revision=REVISION, ready_timeout=1)
    options[field] = value
    with pytest.raises(ValueError):
        launcher.AcceptanceTrigger(tmp_path / "case", **options)
    assert not (tmp_path / "case").exists()


def test_persistent_case_never_reused_or_overwritten(tmp_path):
    case = trigger(tmp_path)
    with pytest.raises(FileExistsError):
        trigger(tmp_path)
    launcher.publish(case.directory, "sample.json", {"ok": True})
    with pytest.raises(FileExistsError):
        launcher.publish(case.directory, "sample.json", {"ok": False})
    assert report(case, "sample.json") == {"ok": True}
    assert not list(case.directory.glob(".pending-*"))


@pytest.mark.parametrize("change", ["public", "directory", "symlink", "oversize", "fifo"])
def test_evidence_reader_rejects_unsafe_files(tmp_path, change):
    path = tmp_path / "evidence"
    if change == "directory":
        path.mkdir()
    elif change == "symlink":
        target = tmp_path / "target"
        target.write_text("{}")
        path.symlink_to(target)
    elif change == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.write_text("{}" if change == "public" else "x" * 4097)
        path.chmod(0o644 if change == "public" else 0o600)
    with pytest.raises((ValueError, OSError)):
        launcher.read_private(path)


def test_no_signal_stops_after_finite_wait_without_reads(rig, tmp_path):
    case = launcher.AcceptanceTrigger(
        tmp_path / "case", rig.owner._policy, source_revision=REVISION, ready_timeout=0.15
    )
    signals = DaemonSignalController()
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    case.start(rig.owner, delivery, signals)
    case.join()
    assert report(case)["outcome"] == "operator_wait_expired"
    assert not report(case)["ever_armed"]
    assert report(case)["read_attempts"] == 0 and not rig.peer.reads
    assert signals.stop_requested
    assert not rig.owner.arm()
    assert not report(case)["restoration_verified"]


@pytest.mark.parametrize("change", ["generation", "start_ticks", "pid", "extra"])
def test_stale_or_extra_identity_consumes_case_without_arming(rig, tmp_path, change):
    case = trigger(tmp_path, policy=rig.owner._policy)
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    case.start(rig.owner, delivery, DaemonSignalController())
    wait_for(lambda: (case.directory / "ready.json").exists())
    request(case, **{change: "wrong"})
    case.join()
    assert report(case)["outcome"] == "trigger_identity_refused"
    assert not rig.peer.reads and not rig.owner.arm()


def test_real_owner_arm_is_passive_until_explicit_demand_and_expires(rig, tmp_path):
    case = trigger(tmp_path, policy=rig.owner._policy)
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    case.signal(signal.SIGUSR1, None)  # Early signals cannot be queued for later.
    case.start(rig.owner, delivery, DaemonSignalController())
    wait_for(lambda: (case.directory / "ready.json").exists())
    assert not rig.owner.status().armed
    request(case)
    wait_for(lambda: (case.directory / "armed.json").exists())
    for _ in range(10):
        context = delivery.context()["context"]
        delivery.frame(context)
    assert not rig.peer.reads
    delivery.demand(context, str(uuid4()))
    wait_for(lambda: rig.peer.reads == ["FQK"])
    rig.clock.now = 85
    case.join()
    result = report(case)
    assert result["outcome"] == "window_expired" and result["ever_armed"]
    assert result["read_attempts"] == 1
    case.signal(signal.SIGUSR1, None)
    assert not rig.owner.arm()


def test_readiness_requires_current_profile_and_cached_identity(rig, tmp_path, monkeypatch):
    case = trigger(tmp_path)
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    assert case._current(rig.owner, delivery)
    rig.runtime._scanner_firmware = "other"
    assert not case._current(rig.owner, delivery)
    rig.runtime._scanner_firmware = "Version 1.26.01"
    monkeypatch.setattr(rig.owner.frames, "supplemental_frame_set", lambda: None)
    assert not case._current(rig.owner, delivery)
    assert not rig.peer.reads


def test_cancel_and_double_start_do_not_arm(rig, tmp_path):
    case = trigger(tmp_path, policy=rig.owner._policy)
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    case.start(rig.owner, delivery, DaemonSignalController())
    with pytest.raises(RuntimeError, match="twice"):
        case.start(rig.owner, delivery, DaemonSignalController())
    case.cancel()
    case.join()
    case.cancel()
    case.join()
    assert report(case)["outcome"] == "cancelled"
    assert not rig.peer.reads and not rig.owner.arm()


def test_policy_mismatch_refused_before_worker_creation(rig, tmp_path):
    case = trigger(tmp_path)  # Deliberately differs from rig's 75s policy.
    delivery = SupplementalDeliveryService(rig.owner.frames, acquisition=rig.owner)
    with pytest.raises(ValueError, match="policy"):
        case.start(rig.owner, delivery, DaemonSignalController())
    assert case._worker is None and not rig.peer.reads


@pytest.mark.parametrize("phase", ["initial", "arming"])
@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_slow_preflight_cannot_publish_or_arm_after_end(rig, tmp_path, monkeypatch, phase, ending):
    case = trigger(tmp_path, policy=rig.owner._policy)
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    entered, release = Event(), Event()
    original = case._current
    calls = 0
    clock = SimpleNamespace(now=10)
    monkeypatch.setattr(launcher, "monotonic", lambda: clock.now)

    def current(*args):
        nonlocal calls
        calls += 1
        if calls == (1 if phase == "initial" else 2):
            entered.set()
            assert release.wait(1)
        return original(*args)

    monkeypatch.setattr(case, "_current", current)
    case.start(rig.owner, delivery, DaemonSignalController())
    try:
        if phase == "arming":
            wait_for(lambda: (case.directory / "ready.json").exists())
            request(case)
        assert entered.wait(1)
        if ending == "cancel":
            case.cancel()
        else:
            clock.now = 12
    finally:
        release.set()
        case.join()
    assert report(case)["outcome"] == (
        "cancelled" if ending == "cancel" else "operator_wait_expired"
    )
    assert not report(case)["ever_armed"] and not rig.peer.reads
    assert not (case.directory / "armed.json").exists()
    if phase == "initial":
        assert not (case.directory / "ready.json").exists()


def test_changed_context_at_trigger_fails_closed(rig, tmp_path):
    case = trigger(tmp_path, policy=rig.owner._policy)
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    case.start(rig.owner, delivery, DaemonSignalController())
    wait_for(lambda: (case.directory / "ready.json").exists())
    rig.runtime._scanner_firmware = "changed"
    request(case)
    case.join()
    assert report(case)["outcome"] == "preflight_refused"
    assert not rig.peer.reads and not rig.owner.arm()


def test_failed_request_is_redacted_and_not_retried(rig, tmp_path):
    case = trigger(tmp_path, policy=rig.owner._policy)
    delivery = SupplementalDeliveryService(
        rig.owner.frames, acquisition=rig.owner, clock=lambda: rig.clock.now
    )
    case.start(rig.owner, delivery, DaemonSignalController())
    wait_for(lambda: (case.directory / "ready.json").exists())
    (case.directory / "arm.json").write_text("SECRET bad json")
    (case.directory / "arm.json").chmod(0o600)
    case.signal(signal.SIGUSR1, None)
    case.join()
    assert report(case)["outcome"] == "launcher_failed"
    assert "SECRET" not in (case.directory / "result.json").read_text()
    assert not rig.peer.reads


def test_pidfd_arming_checks_ticks_before_publishing_and_cannot_replay(tmp_path, monkeypatch):
    case = trigger(tmp_path)
    launcher.publish(
        case.directory, "ready.json", case.identity | {"state": "waiting_for_operator"}
    )
    events = []
    monkeypatch.setattr(os, "pidfd_open", lambda pid: events.append(("open", pid)) or 98765)
    monkeypatch.setattr(os, "close", lambda fd: events.append(("close", fd)))
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *args: events.append(("send", args)))
    real_ticks = launcher.start_ticks
    monkeypatch.setattr(launcher, "start_ticks", lambda pid: "different")
    with pytest.raises(ValueError, match="identity changed"):
        launcher.arm_case(case.directory)
    assert not (case.directory / "arm.json").exists()
    monkeypatch.setattr(launcher, "start_ticks", real_ticks)
    launcher.arm_case(case.directory)
    assert report(case, "arm.json") == case.identity
    with pytest.raises(FileExistsError):
        launcher.arm_case(case.directory)
    assert sum(event[0] == "send" for event in events) == 1


@pytest.mark.parametrize(
    "module,name",
    [
        (cli, "DaemonRuntime"),
        (cli, "DaemonReadOnlyApi"),
        (cli, "DaemonProcess"),
        (daemon_display_frames, "DaemonDisplayFrames"),
    ],
)
def test_prepatched_hooks_are_preserved_and_refused(module, name, tmp_path, monkeypatch):
    foreign = object()
    monkeypatch.setattr(module, name, foreign)
    with pytest.raises(RuntimeError, match="pre-existing"):
        launcher.NativeAssembly.validate_hooks()
    assert getattr(module, name) is foreign


@pytest.fixture
def native(configured):
    peer = LoopbackScanner()
    transport = UdpTransport("127.0.0.1", remote_port=peer.socket.getsockname()[1], reconnect=False)
    scanner = SDS200.from_transport(transport)
    router = PcmSinkRouter(name="acceptance")
    audio = AudioFanoutSession(AudioStream(FakeAudioTransport()), (router,))
    profile = DaemonDisplayProfile(
        replace(configured, scanner_target=scanner.endpoint), lambda: scanner.endpoint
    )
    try:
        yield SimpleNamespace(
            scanner=scanner, router=router, audio=audio, profile=profile, peer=peer
        )
    finally:
        scanner.close()
        peer.close()


def assemble(assembly, native, *, profile=True):
    runtime = cli.DaemonRuntime(
        native.scanner, native.audio, native.router, psi_timeout=1, psi_recover_after=3
    )
    api = cli.DaemonReadOnlyApi(runtime)
    api.display_profile = native.profile
    process = cli.DaemonProcess(runtime)
    if profile:
        frames = daemon_display_frames.DaemonDisplayFrames(native.profile, native.scanner)
        api.display_frames = frames
        frames.start()
    return runtime, api, process


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "no_profile",
        "duplicate_runtime",
        "duplicate_api",
        "duplicate_frames",
        "duplicate_process",
        "startup_error",
    ],
)
def test_exact_native_assembly_and_all_cleanup_paths(native, tmp_path, monkeypatch, fault):
    case = launcher.AcceptanceTrigger(
        tmp_path / "case", POLICY, source_revision=REVISION, ready_timeout=0.2
    )
    assembly = launcher.NativeAssembly(case)
    previous_signal = signal.getsignal(signal.SIGUSR1)
    original_frames = daemon_display_frames.DaemonDisplayFrames
    expected_error = fault != "none"
    try:
        with assembly.installed():
            runtime, api, process = assemble(assembly, native, profile=fault != "no_profile")
            assert type(runtime) is DaemonRuntime and type(api) is DaemonReadOnlyApi
            assert not native.peer.commands
            if fault == "duplicate_runtime":
                cli.DaemonRuntime(native.scanner, native.audio, native.router)
            elif fault == "duplicate_api":
                cli.DaemonReadOnlyApi(runtime)
            elif fault == "duplicate_frames":
                daemon_display_frames.DaemonDisplayFrames(native.profile, native.scanner)
            elif fault == "duplicate_process":
                cli.DaemonProcess(runtime)
            elif fault == "startup_error":

                def fail(_self):
                    raise RuntimeError("synthetic startup failure")

                monkeypatch.setattr(DaemonRuntime, "start", fail)
            process.run()
            assert not expected_error
    except RuntimeError:
        assert expected_error
    assert cli.DaemonRuntime is DaemonRuntime
    assert cli.DaemonReadOnlyApi is DaemonReadOnlyApi
    assert cli.DaemonProcess is DaemonProcess
    assert daemon_display_frames.DaemonDisplayFrames is original_frames
    assert signal.getsignal(signal.SIGUSR1) == previous_signal
    assert not native.peer.reads
    assert report(case, "cleanup.json")["cleanup_complete"]
    assert not report(case, "cleanup.json")["restoration_verified"]
    if assembly.owner is not None:
        assert not assembly.owner.frames.quick_key_worker_status().alive
        assert runtime._supplemental_acquisition is None
        assert runtime._supplemental_acquisition_used


def test_native_process_real_pidfd_arm_and_demand_without_runtime_subclass(native, tmp_path):
    case = trigger(tmp_path, policy=replace(POLICY, window_seconds=2, max_read_attempts=1))
    assembly = launcher.NativeAssembly(case)

    def operator():
        wait_for(lambda: (case.directory / "ready.json").exists())
        launcher.arm_case(case.directory)  # Signals this process; exact installed handler.
        wait_for(lambda: (case.directory / "armed.json").exists())
        service = assembly.delivery
        service.demand(service.context()["context"], str(uuid4()))

    with assembly.installed():
        runtime, api, process = assemble(assembly, native)
        with ThreadPoolExecutor(max_workers=1) as pool:
            observed = pool.submit(operator)
            process.run()
            observed.result(timeout=2)
            with pytest.raises(RuntimeError, match="cannot be retried"):
                process.run()
    assert native.peer.reads == ["FQK"]
    assert report(case)["outcome"] == "quota_exhausted"
    assert report(case)["read_attempts"] == 1
    assert not assembly.owner.frames.quick_key_worker_status().alive
    assert not runtime.scanner.connected
    assert api.supplemental_display is assembly.delivery


def test_main_rejects_missing_profile_before_any_hook_or_case(tmp_path):
    with pytest.raises(SystemExit):
        launcher.main(
            [
                "daemon",
                "--case-directory",
                str(tmp_path / "case"),
                "--expected-firmware",
                POLICY.expected_firmware,
                "--source-revision",
                REVISION,
                "--guardian-pid",
                str(os.getppid()),
                "--guardian-start-ticks",
                "0",
                "--",
                "--host",
                "127.0.0.1",
                "daemon",
            ]
        )
    assert not (tmp_path / "case").exists()
    launcher.NativeAssembly.validate_hooks()
