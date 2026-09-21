"""Distinct bounded-write research policy; localhost fixtures only."""

import json
import os
import signal
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sds200 import cli, daemon_display_frames, daemon_quick_keys, scanner_clock
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.daemon_runtime import DaemonRuntime, DaemonRuntimeState
from sds200.radio import SDS200
from sds200.trace import TrafficTrace

from .test_bounded_supplemental_io import REPLIES, patch_write
from .test_bounded_supplemental_io import udp as udp
from .test_daemon_display_frames import configured as configured
from .test_daemon_display_read_research import runtime_for
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_quick_keys import ENDPOINT, activate
from .test_supplemental_research_launcher import (
    FIRMWARE,
    Runtime,
    arguments,
    launcher,
    normal_psi,
)
from .test_supplemental_transition_wait import transition

POLICY = dict(continuity=True, timing=True, transition_wait=True, bounded_writes=True)


@pytest.fixture
def trial(udp, monkeypatch):
    scanner = SDS200.from_transport(udp[0])
    clock = SimpleNamespace(now=10.0)
    window = launcher.ReadWindow(Runtime(scanner), FIRMWARE, clock=lambda: clock.now, **POLICY)
    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        scanner.endpoint,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
        bounded_writes=True,
        read_scope=window.scope,
    )
    window.cache, window.feed = cache, object()
    session = cache.begin_session()
    sequence = 0
    calls = []

    def psi(info=None):
        nonlocal sequence
        sequence += 1
        window.observe_psi(normal_psi() if info is None else info)
        activate(cache, session, sequence=sequence)

    def write(_fd, data):
        command = data.decode().strip()
        calls.append(command)
        scanner._receive_line(REPLIES[command].decode())
        return len(data)

    patch_write(monkeypatch, write)
    remove = scanner.on_packet(window.observe_packet)
    try:
        yield window, scanner, cache, clock, psi, calls
    finally:
        remove()
        cache.close()


@pytest.mark.parametrize("owner", ["window", "trigger"])
@pytest.mark.parametrize(
    "changes",
    [
        {"bounded_writes": 1},
        {"bounded_writes": None},
        {"transition_wait": False},
        {"timing": False},
        {"continuity": False},
    ],
)
def test_invalid_policy_refused_before_side_effects(tmp_path, owner, changes):
    policy = POLICY | changes
    with pytest.raises(ValueError):
        if owner == "window":
            launcher.ReadWindow(object(), FIRMWARE, **policy)
        else:
            launcher.SupplementalTrigger(tmp_path / "case", FIRMWARE, **policy)
    assert not (tmp_path / "case").exists()


@pytest.mark.skipif(os.name != "posix", reason="Native POSIX candidate")
def test_sixty_read_policy_has_no_wrapper_no_trace_phases_and_no_rearm(trial):
    window, scanner, cache, clock, psi, calls = trial
    trace = scanner.trace
    with window.trace_scope():
        assert scanner.trace is trace and type(trace) is TrafficTrace
        psi()
        assert cache.poll_once() and calls == []  # No GET before explicit arm.
        assert window.arm()
        for index in range(60):
            clock.now = 10.5 + (index // 2) * 2 + (index % 2) * 0.5
            psi()
            assert cache.poll_once()
        psi()
        psi()
        assert window.report()["status"] == "replies_and_psi_observed"
        assert not window.allow_poll()
        assert calls == ["FQK", "DTM"] * 30
        report = window.timing_report()
        assert report["schema"] == 2
        assert report["write_policy"] == "native-posix-nonblocking"
        assert report["unobserved_phases"] == ["tx_intent", "rx_line", "rx_rejection"]
        assert not report["outgoing_wire_delivery_established"]
        events = [item["event"] for item in report["events"]]
        assert events.count("parsed_packet") == events.count("scope_enter") == 60
        assert not set(report["unobserved_phases"]) & set(events)
        window.stop()
        with pytest.raises(ValueError, match="rearmed"):
            window.arm()
    assert scanner.trace is trace


@pytest.mark.skipif(os.name != "posix", reason="Native POSIX candidate")
def test_native_policy_preserves_withholding_and_requires_two_fresh_psi(trial):
    window, _, cache, clock, psi, calls = trial
    assert window.arm()
    psi()
    assert cache.poll_once()
    clock.now = 10.6
    psi(transition())
    assert not window.allow_poll()
    cache.poll_once()
    assert calls == ["FQK"]
    clock.now = 10.8
    psi()
    assert not window.allow_poll()
    clock.now = 11.2
    psi()
    assert window.allow_poll() and not cache.poll_once()  # Existing reservation pacing remains.
    clock.now = 12
    psi()
    assert cache.poll_once()
    clock.now = 12.7
    psi()
    assert cache.poll_once()
    assert calls == ["FQK", "FQK", "DTM"]
    assert window.transition_report()["recoveries"] == 1


@pytest.mark.parametrize("phase", ["tx_intent", "rx_line", "rx_rejection"])
def test_unobserved_trace_phases_cannot_be_invented(trial, phase):
    window, *_ = trial
    with pytest.raises(ValueError, match="no trace-wrapper"):
        window.record_timing(phase, "DTM")


@pytest.mark.parametrize("failure", ["owner", "cache", "trace", "file"])
def test_wrong_owner_or_policy_refused_before_arm(trial, tmp_path, failure):
    window, scanner, cache, _, _, calls = trial
    if failure == "owner":
        window.runtime.scanner = SimpleNamespace(transport=scanner.transport)
    elif failure == "cache":
        cache._bounded_scanner = None
    elif failure == "trace":
        scanner.trace = object()
    else:
        scanner.trace = TrafficTrace(tmp_path / "not-written.log")
    assert not window.arm() and calls == []
    assert window.report()["failure"] == "preflight_refused"


def test_native_trace_cannot_be_replaced_or_file_traced(trial, tmp_path):
    window, scanner, *_ = trial
    original = scanner.trace
    with pytest.raises(RuntimeError, match="ownership changed"), window.trace_scope():
        scanner.trace = TrafficTrace()
    scanner.trace = TrafficTrace(tmp_path / "not-written.log")
    with pytest.raises(ValueError, match="file tracing"), window.trace_scope():
        pytest.fail("file tracing admitted")
    assert not (tmp_path / "not-written.log").exists()
    scanner.trace = original


def test_trigger_marker_cannot_be_confused_with_old_cases(tmp_path):
    trigger = launcher.SupplementalTrigger(tmp_path / "case", FIRMWARE, **POLICY)
    trigger.write("ready.json", {"research_started": False})
    report = json.loads((tmp_path / "case/ready.json").read_text())
    assert report["read_kind"] == "shared-clock-favorites-bounded-write"
    assert report["max_opportunities"] == 60 and report["window_seconds"] == 64
    assert report["scan_transition_wait_enabled"] is True
    assert report["write_policy"] == "native-posix-nonblocking" and report["timing_schema"] == 2
    assert not report["research_started"]


def test_main_wires_exact_native_cache_without_startup_gets(
    udp,
    tmp_path,
    monkeypatch,
    configured,
):
    scanner = SDS200.from_transport(udp[0])
    original_frames = daemon_display_frames.DaemonDisplayFrames
    original_signal = signal.getsignal(signal.SIGUSR1)

    def main(_args):
        router = object()
        runtime = cli.DaemonRuntime(scanner, SimpleNamespace(sinks=(router,)), router)
        assert runtime._system_status_research is None
        assert runtime._display_read_research is None
        config = replace(configured, scanner_target=scanner.endpoint)
        profile = DaemonDisplayProfile(config, lambda: scanner.endpoint)
        feed = daemon_display_frames.DaemonDisplayFrames(profile, scanner)
        try:
            assert feed._window.bounded_writes
            assert feed._window.cache._bounded_scanner is scanner
            assert not feed._window.allow_poll()
            assert not feed._window.cache.poll_once()
            assert udp[0].statistics["commands_sent"] == 0
        finally:
            feed.close()
        return 0

    monkeypatch.setattr(cli, "main", main)
    args = arguments(tmp_path)
    args[args.index("--ready-timeout") + 1] = "10"
    args[0:0] = ["--continuity", "--timing", "--transition-wait", "--bounded-writes"]
    assert launcher.main(args) == 0
    assert cli.DaemonRuntime is DaemonRuntime
    assert daemon_display_frames.DaemonDisplayFrames is original_frames
    assert signal.getsignal(signal.SIGUSR1) == original_signal
    assert not (tmp_path / "case/triggered.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="Native POSIX candidate")
@pytest.mark.parametrize("outcome", ["success", "timeout", "transition"])
def test_real_worker_trigger_and_native_udp_lifecycle(
    udp,
    tmp_path,
    monkeypatch,
    configured,
    outcome,
):
    # Accelerate only refresh scheduling, never the write/reply deadline.
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.005)
    monkeypatch.setattr(daemon_quick_keys, "REFRESH_INTERVAL", 0.02)
    monkeypatch.setattr(scanner_clock, "REFRESH_INTERVAL", 0.02)
    transport, peer = udp
    transport.stop()
    scanner = SDS200.from_transport(transport)
    original_trace = scanner.trace
    result_file = tmp_path / "case/result.json"
    sent = []

    # The fixture substitutes cached startup identity only; no scanner startup
    # commands are issued. Runtime locks and the native transport are real.
    monkeypatch.setattr(
        DaemonRuntime,
        "start",
        lambda runtime: setattr(
            runtime,
            "_state",
            DaemonRuntimeState.RUNNING,
        ),
    )
    monkeypatch.setattr(
        DaemonRuntime,
        "stop",
        lambda runtime: setattr(
            runtime,
            "_state",
            DaemonRuntimeState.STOPPED,
        ),
    )

    def main(_args):
        with scanner:
            scanner._psi_active = True
            template = runtime_for(scanner)
            runtime = cli.DaemonRuntime(scanner, template.audio, template.router)
            runtime._scanner_model, runtime._scanner_firmware = "SDS200", FIRMWARE
            profile = DaemonDisplayProfile(
                replace(configured, scanner_target=scanner.endpoint),
                lambda: scanner.endpoint,
            )
            feed = daemon_display_frames.DaemonDisplayFrames(profile, scanner)
            stop, peer_failed = threading.Event(), threading.Event()
            peer.settimeout(0.005)
            peer_errors = []
            sample_bytes = (
                b'PSI,<XML>,<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">'
                b'<MonitorList Q_Key="None"/><System Index="1" Name="Synthetic" Q_Key="None"/>'
                b"<Department/><Site/><SiteFrequency/>"
                b"<TGID/><Property/><DualWatch/>"
                b'<Footer No="1" EOT="1"/></ScannerInfo>'
            )
            address = transport._socket.getsockname()

            def respond():
                try:
                    while not stop.is_set():
                        try:
                            data, source = peer.recvfrom(4096)
                        except TimeoutError:
                            pass
                        else:
                            command = data.decode().strip()
                            assert command in REPLIES
                            sent.append(command)
                            if outcome != "timeout":
                                peer.sendto(REPLIES[command], source)
                        peer.sendto(sample_bytes, address)
                except BaseException as exc:
                    peer_errors.append(exc)
                    peer_failed.set()

            responder = threading.Thread(target=respond)
            try:
                feed.start()
                runtime.start()
                handler = signal.getsignal(signal.SIGUSR1)
                assert handler.__self__._armed.wait(1)
                responder.start()
                wait_for(lambda: feed.snapshot()["frames"]["preferred"]["status"] == "current")
                assert sent == []
                handler(signal.SIGUSR1, None)
                if outcome == "transition":
                    window = handler.__self__.window
                    wait_for(lambda: len(sent) >= 2)

                    # Inject a complete between-poll transition, not a read-race
                    # exception; the normal native PSI resumes it twice.
                    def inject_transition():
                        with window.lock:
                            if not window.inflight and not window.timing_poll_active:
                                window.observe_psi(transition())
                                return True
                        assert not peer_failed.is_set()
                        return False

                    wait_for(inject_transition)
                wait_for(result_file.exists)
                assert not peer_errors
                report = json.loads(result_file.read_text())
                assert report["read_kind"] == "shared-clock-favorites-bounded-write"
                count = len(sent)
                if outcome == "timeout":
                    assert count == 1 and report["status"] == "qualification_unconfirmed"
                    assert report["read_failure"]["cache_quarantine"] == "timeout"
                else:
                    assert sent == ["FQK", "DTM"] * 30
                    assert report["status"] == "replies_and_psi_observed"
                    if outcome == "transition":
                        assert report["transition_wait"]["recoveries"] == 1
                assert scanner.trace is original_trace
                timeline = json.loads((tmp_path / "case/timing.json").read_text())
                assert timeline["schema"] == 2 and not timeline["overflow"]
                events = [entry["event"] for entry in timeline["events"]]
                assert events.count("scope_enter") == events.count("scope_exit") == count
                assert events.count("cache_complete") == count
                assert not {"tx_intent", "rx_line", "rx_rejection"} & set(events)
                handler(signal.SIGUSR1, None)  # No rearming or GET after completion.
                stop.wait(0.06)
                assert len(sent) == count and not scanner._responses
                assert feed.quick_key_worker_status().failure is None
                return 0
            finally:
                stop.set()
                if responder.is_alive():
                    responder.join(1)
                runtime.stop()
                feed.close()
                assert not responder.is_alive()

    monkeypatch.setattr(cli, "main", main)
    args = arguments(tmp_path)
    args[args.index("--ready-timeout") + 1] = "10"
    args[0:0] = ["--continuity", "--timing", "--transition-wait", "--bounded-writes"]
    assert launcher.main(args) == 0
