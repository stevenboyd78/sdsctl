"""Bounded shared-reader launcher: fake owner/clock only, no hardware I/O."""

import importlib.util
import json
import signal
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sds200 import (
    AudioFanoutSession,
    AudioStream,
    PcmSinkRouter,
    cli,
    daemon_display_frames,
    daemon_quick_keys,
    scanner_clock,
)
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.daemon_runtime import DaemonRuntime, DaemonRuntimeState
from sds200.exceptions import CommandRejectedError, CommandTimeoutError, ProtocolError
from sds200.models import Packet
from sds200.network import UdpTransport
from sds200.scanner_quick_keys import QuickKeySelection
from sds200.trace import TrafficTrace

from .fakes import FakeAudioTransport
from .test_daemon_display_frames import configured as configured
from .test_daemon_display_read_research import FIRMWARE
from .test_daemon_display_read_research import psi as normal_psi
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_quick_keys import ENDPOINT, TARGET, activate
from .test_daemon_supplemental_reads import SupplementalScanner
from .test_daemon_supplemental_scope import Scanner as OwnerScanner
from .test_system_status_research_launcher import launcher as shared_launcher

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "supplemental_launcher", ROOT / "scripts/research_supplemental_daemon.py"
)
launcher = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"research_system_status_daemon": shared_launcher}):
    SPEC.loader.exec_module(launcher)


class Runtime:
    def __init__(self, scanner):
        self.scanner = scanner
        self._system_status_research = self._display_read_research = None
        self.ready = True
        self.info = SimpleNamespace(
            state=DaemonRuntimeState.RUNNING,
            scanner_model="SDS200",
            scanner_firmware=FIRMWARE,
            scanner_connected=True,
            psi_active=True,
        )

    def snapshot(self):
        return self.info

    @contextmanager
    def _supplemental_read_scope(self, scanner):
        assert scanner is self.scanner
        yield self.ready


@pytest.fixture
def trial(request):
    clock = SimpleNamespace(now=10.0)
    scanner = SupplementalScanner()
    scanner.connected = True
    scanner.endpoint = TARGET
    # Constructor only; no socket/connect is used.
    scanner.transport = UdpTransport("192.0.2.10")
    scanner.trace = TrafficTrace()
    runtime = Runtime(scanner)
    policy = getattr(request, "param", False)
    continuity, timing = policy if isinstance(policy, tuple) else (policy, False)
    window = launcher.ReadWindow(
        runtime, FIRMWARE, clock=lambda: clock.now, continuity=continuity, timing=timing
    )
    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        TARGET,
        include_clock=True,
        allow_scoped_reads=False,
        read_scope=window.scope,
        clock=lambda: clock.now,
    )
    window.cache, window.feed = cache, object()
    session = cache.begin_session()
    sequence = 0

    def psi(info=None):
        nonlocal sequence
        sequence += 1
        window.observe_psi(normal_psi(favorites="None", system="None") if info is None else info)
        activate(cache, session, QuickKeySelection(None, None), sequence=sequence)

    old_reply, old_clock = scanner.reply, scanner.clock_reply

    def reply(command):
        result = old_reply(command)
        window.observe_packet(result.packet)
        return result

    def clock_reply():
        result = old_clock()
        window.observe_packet(result.packet)
        return result

    scanner.reply, scanner.clock_reply = reply, clock_reply
    try:
        yield window, scanner, cache, clock, psi
    finally:
        cache.close()


def test_no_startup_reads_and_never_rearms(trial):
    window, scanner, cache, _, psi = trial
    psi()
    assert not window.allow_poll()
    assert cache.poll_once()  # Defense in depth, even if before_poll is bypassed.
    assert scanner.reads == []
    assert window.arm()
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()
    window.stop()
    assert not window.allow_poll()
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", DaemonRuntimeState.STOPPED),
        ("scanner_model", "SDS100"),
        ("scanner_firmware", "different"),
        ("scanner_connected", False),
        ("psi_active", False),
    ],
)
def test_preflight_refusals_send_nothing(trial, field, value):
    window, scanner, _, _, _ = trial
    setattr(window.runtime.info, field, value)
    assert not window.arm()
    assert window.failure == "preflight_refused" and not scanner.reads


@pytest.mark.parametrize("field", ["transport", "research", "cache", "feed"])
def test_transport_old_research_and_incomplete_owner_refused(trial, field):
    window, scanner, _, _, _ = trial
    if field == "transport":
        scanner.transport = object()
    elif field == "research":
        window.runtime._display_read_research = object()
    else:
        setattr(window, field, None)
    assert not window.arm()
    assert not scanner.reads


def test_six_opportunities_three_each_plus_two_post_psi(trial):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    for when in (10, 10.5, 12, 12.5, 14, 14.5):
        clock.now = when
        psi()
        assert window.allow_poll() and cache.poll_once()
        assert window.report()["status"] == "qualification_unconfirmed"
    assert len(scanner.reads) == 6 and not window.allow_poll()
    assert window.replies == {"DTM": 3, "FQK": 3}
    psi()
    assert window.report()["status"] == "qualification_unconfirmed"
    psi()
    report = window.report()
    assert report["status"] == "replies_and_psi_observed"
    assert report["samples_valid"] and report["physical_scanning_acceptance"] == "operator_required"
    assert "PRIVATE" not in json.dumps(report)
    window.stop()
    clock.now = 15
    psi()
    cache.poll_once()  # No seventh wire read even without the worker's gate.
    assert len(scanner.reads) == 6


@pytest.mark.parametrize("after", [False, True])
def test_deadline_is_fixed_not_extended_by_demand(trial, after):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    if after:
        psi()
        cache.poll_once()
    count = len(scanner.reads)
    clock.now = 18
    psi()
    assert not window.allow_poll()
    cache.poll_once()
    assert len(scanner.reads) == count
    assert window.report()["status"] == "qualification_unconfirmed"


@pytest.mark.parametrize("reason", ["disconnect", "mode", "busy", "cancel"])
def test_change_closes_window_permanently(trial, reason):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi()
    assert cache.poll_once()
    clock.now = 10.5
    if reason == "disconnect":
        window.observe_connection(False)
        window.observe_connection(True)
    elif reason == "mode":
        window.observe_psi(None)
    elif reason == "busy":
        window.runtime.ready = False
        cache.poll_once()
        window.runtime.ready = True
    else:
        window.stop("cancelled")
    psi()
    clock.now = 11
    cache.poll_once()
    assert len(scanner.reads) == 1 and window.closed


@pytest.mark.parametrize("error", [CommandTimeoutError, CommandRejectedError, RuntimeError])
def test_uncertain_or_rejected_read_cannot_continue(trial, error):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi()

    def fail(command):
        raise error("PRIVATE_SENTINEL")

    scanner.reply = fail
    cache.poll_once()
    clock.now = 10.5
    psi()
    cache.poll_once()
    assert len(scanner.reads) == 1
    assert window.report()["status"] == "qualification_unconfirmed"
    assert "PRIVATE_SENTINEL" not in json.dumps(window.report())


def test_other_packets_not_retained_and_missing_fresh_psi_blocks_read(trial):
    window, scanner, cache, _, _ = trial
    assert window.arm()
    window.observe_packet(Packet("SQK", (), "PRIVATE"))
    activate(cache, cache._session, QuickKeySelection(None, None))
    cache.poll_once()
    assert scanner.reads == [] and window.replies == {"DTM": 0, "FQK": 0}


@pytest.mark.parametrize("kind", ["favorites", "clock"])
@pytest.mark.parametrize(
    "error,category",
    [
        (CommandTimeoutError, "timeout"),
        (CommandRejectedError, "rejected"),
        (ProtocolError, "invalid_response"),
        (RuntimeError, "read_error"),
    ],
)
def test_private_report_retains_first_failure_category_without_payload(
    trial, kind, error, category
):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi()
    if kind == "clock":
        assert cache.poll_once()
        clock.now = 10.5

    def fail(*_args):
        raise error("PRIVATE_SENTINEL")

    if kind == "clock":
        scanner.clock_reply = fail
    else:
        scanner.reply = fail
    assert cache.poll_once()
    assert window.close_on_read_failure()
    report = window.report()
    fault = report["read_failure"]
    quarantine = None if category == "rejected" else category
    assert fault == {
        "cache_quarantine": quarantine,
        "bank_failures": {
            "favorites": category if kind == "favorites" else None,
            "system": None,
            "department": None,
        },
        "clock_failure": category if kind == "clock" else None,
        "clock_quarantine": quarantine,
    }
    assert report["failure"] == "read_unconfirmed"
    assert report["status"] == "qualification_unconfirmed"
    assert "PRIVATE" not in json.dumps(report) and TARGET not in json.dumps(report)
    reads = list(scanner.reads)
    # A later scope/profile barrier may clear per-bank rejection detail. The
    # first observed fault must survive without retaining raw data or rearming.
    cache.clear_demand()
    psi()
    clock.now += 2
    cache.poll_once()
    window.close_on_read_failure()
    assert window.report()["read_failure"] == fault
    assert scanner.reads == reads and not window.allow_poll()


def test_scope_preserves_fault_before_it_suspends_cache(trial):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi()

    def reject(_command):
        raise CommandRejectedError("PRIVATE_REJECTION")

    scanner.reply = reject
    assert cache.poll_once()
    clock.now = 10.5
    assert cache.poll_once()  # The second reservation sees and closes on the fault.
    assert window.report()["read_failure"]["bank_failures"]["favorites"] == "rejected"
    assert len(scanner.reads) == 1
    assert not cache.snapshot().active and not window.allow_poll()


def test_completion_deadline_category_does_not_claim_wire_lateness(trial):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi()
    original = scanner.reply

    def complete_late(command):
        result = original(command)  # Reply observed before the completion deadline.
        clock.now += 0.25
        return result

    scanner.reply = complete_late
    assert cache.poll_once()
    assert window.close_on_read_failure()
    report = window.report()
    assert report["reply_counts"] == {"DTM": 0, "FQK": 1}
    assert report["read_failure"]["cache_quarantine"] == "timeout"
    assert report["status"] == "qualification_unconfirmed"
    window.observe_packet(Packet("FQK", (), "PRIVATE_LATE_REPLY"))
    assert window.report()["reply_counts"] == report["reply_counts"]
    assert not window.allow_poll()


def test_read_fault_observation_is_passive_and_healthy_report_has_no_fault(trial):
    window, scanner, cache, _, psi = trial
    psi()
    lease = cache._demand_until
    assert not window.close_on_read_failure()
    assert window.report()["read_failure"] is None
    assert scanner.reads == [] and cache._demand_until == lease and not window.closed


def arguments(tmp_path):
    return [
        "--expected-firmware",
        FIRMWARE,
        "--evidence-directory",
        str(tmp_path / "case"),
        "--ready-timeout",
        "0.01",
        "--",
        "--host",
        "192.0.2.10",
        "daemon",
    ]


@pytest.mark.parametrize("fail", [False, True])
def test_launcher_one_owner_no_legacy_research_and_restores_hooks(tmp_path, monkeypatch, fail):
    original_frames = daemon_display_frames.DaemonDisplayFrames
    original_signal = signal.getsignal(signal.SIGUSR1)
    monkeypatch.setattr(DaemonRuntime, "start", lambda self: None)
    monkeypatch.setattr(DaemonRuntime, "stop", lambda self: None)

    def fake_main(_args):
        router = object()
        runtime = cli.DaemonRuntime(object(), SimpleNamespace(sinks=(router,)), router)
        assert runtime._system_status_research is None and runtime._display_read_research is None
        with pytest.raises(RuntimeError, match="one daemon owner"):
            cli.DaemonRuntime(object(), SimpleNamespace(sinks=(router,)), router)
        with pytest.raises(ValueError, match="existing owner"):
            daemon_display_frames.DaemonDisplayFrames(object(), object())
        runtime.start()
        runtime.start()
        runtime.stop()
        if fail:
            raise RuntimeError("test exit")
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    if fail:
        with pytest.raises(RuntimeError, match="test exit"):
            launcher.main(arguments(tmp_path))
    else:
        assert launcher.main(arguments(tmp_path)) == 0
    assert cli.DaemonRuntime is DaemonRuntime
    assert daemon_display_frames.DaemonDisplayFrames is original_frames
    assert signal.getsignal(signal.SIGUSR1) == original_signal
    assert not (tmp_path / "case/triggered.json").exists()


def test_trigger_expiry_and_cancel_never_arm(tmp_path):
    trigger = launcher.SupplementalTrigger(tmp_path / "case", FIRMWARE, ready_timeout=0.01)
    trigger.start(object())
    trigger.join()
    result = json.loads((tmp_path / "case/result.json").read_text())
    assert result["status"] == "operator_wait_expired"
    assert result["read_kind"] == "shared-clock-favorites"
    assert not result["research_started"]


def test_connection_change_during_registration_is_preserved_without_arm(tmp_path, trial):
    window, scanner, _, _, _ = trial
    trigger = launcher.SupplementalTrigger(tmp_path / "case", FIRMWARE)
    trigger.window = window
    callbacks_removed = []
    scanner.on_connection = lambda cb: (cb(False), lambda: callbacks_removed.append("connection"))[
        1
    ]
    scanner.on_psi = lambda cb: lambda: callbacks_removed.append("psi")
    scanner.on_packet = lambda cb: lambda: callbacks_removed.append("packet")
    report = trigger.invoke(window.runtime)
    assert report["failure"] == "connection_changed" and not report["research_started"]
    assert callbacks_removed == ["packet", "psi", "connection"]
    assert scanner.reads == [] and not window.attempted


@pytest.mark.parametrize("continuity,timing", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("outcome", ["success", "timeout", "mode", "cancel", "expiry"])
def test_actual_feed_worker_and_trigger_lifecycle(
    tmp_path, monkeypatch, configured, outcome, continuity, timing
):
    # Shorten only scheduling intervals for this offline lifecycle test.
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.005)
    monkeypatch.setattr(daemon_quick_keys, "REFRESH_INTERVAL", 0.02)
    monkeypatch.setattr(scanner_clock, "REFRESH_INTERVAL", 0.02)
    limit = 60 if continuity else 6
    scanner = OwnerScanner()
    scanner.transport = UdpTransport("192.0.2.25")
    scanner.trace = original_trace = TrafficTrace()
    scanner.on_packet = lambda cb: scanner.events.subscribe("packet", cb)
    old_reply, old_clock = scanner.reply, scanner.clock_reply

    def reply(command):
        scanner.trace.tx(command.wire)
        if outcome == "timeout":
            raise CommandTimeoutError("PRIVATE_SENTINEL")
        result = old_reply(command)
        scanner.trace.rx("FQK,PRIVATE_RESPONSE")
        scanner.events.emit("packet", result.packet)
        return result

    def clock_reply():
        scanner.trace.tx("DTM")
        result = old_clock()
        scanner.trace.rx("DTM,PRIVATE_RESPONSE")
        scanner.events.emit("packet", result.packet)
        return result

    scanner.reply, scanner.clock_reply = reply, clock_reply
    result_file = tmp_path / "case/result.json"

    def fake_main(_args):
        router = PcmSinkRouter(name="supplemental-test")
        audio = AudioFanoutSession(AudioStream(FakeAudioTransport()), (router,))
        runtime = cli.DaemonRuntime(scanner, audio, router)
        profile = DaemonDisplayProfile(configured, lambda: scanner.endpoint)
        feed = daemon_display_frames.DaemonDisplayFrames(profile, scanner)
        stop = threading.Event()

        def emit():
            while not stop.wait(0.01):
                sample = normal_psi(favorites="None", system="None", extra='<TGID Name="Demo"/>')
                if outcome == "mode" and len(scanner.reads) >= 2:
                    sample = normal_psi(screen="unknown")
                scanner.events.emit("psi", sample)

        emitter = threading.Thread(target=emit)
        try:
            feed.start()
            runtime.start()
            runtime._scanner_model, runtime._scanner_firmware = "SDS200", FIRMWARE
            emitter.start()
            handler = signal.getsignal(signal.SIGUSR1)
            assert handler.__self__._armed.wait(1)
            assert not scanner.reads
            if outcome != "expiry":
                handler(signal.SIGUSR1, None)
                handler(signal.SIGUSR1, None)
            if outcome == "cancel":
                wait_for(lambda: len(scanner.reads) >= 1)
                handler.__self__.cancel()
            wait_for(result_file.exists)
            # The worker still exists, but cannot perform additional reads.
            count = len(scanner.reads)
            assert count <= limit
            stop.wait(0.12)
            handler(signal.SIGUSR1, None)
            assert len(scanner.reads) == count
            assert feed.quick_key_worker_status().failure is None
            report = json.loads(result_file.read_text())
            if outcome == "success":
                assert count == limit and report["status"] == "replies_and_psi_observed"
                assert not report["timing_poll_active"]
                assert report["reply_counts"] == {"DTM": limit // 2, "FQK": limit // 2}
            elif outcome == "expiry":
                assert count == 0 and report["status"] == "operator_wait_expired"
            else:
                assert 0 < count < limit and report["status"] == "qualification_unconfirmed"
                if outcome == "timeout":
                    assert report["read_failure"]["cache_quarantine"] == "timeout"
                    assert report["read_failure"]["bank_failures"]["favorites"] == "timeout"
            assert report["max_opportunities"] == limit
            assert report["read_kind"] == (
                "shared-clock-favorites-timing"
                if timing
                else (
                    "shared-clock-favorites-continuity" if continuity else "shared-clock-favorites"
                )
            )
            assert scanner.trace is original_trace
            timing_file = tmp_path / "case/timing.json"
            if timing and outcome != "expiry":
                timeline = json.loads(timing_file.read_text())
                assert not timeline["overflow"] and len(timeline["events"]) <= 512
                assert "PRIVATE" not in timing_file.read_text()
                assert timeline["scan_rejection"] == report["scan_rejection"]
                if report["failure"] == "scan_context_changed":
                    assert report["scan_rejection"]["violations"] == ["unsupported_screen"]
                    assert not report["scan_rejection"]["before_arm"]
                if outcome == "success":
                    assert report["scan_rejection"] is None
                    assert not timeline["poll_active_at_snapshot"]
                    assert len([e for e in timeline["events"] if e["event"] == "tx_intent"]) == 60
                    assert (
                        len([e for e in timeline["events"] if e["event"] == "cache_complete"]) == 60
                    )
                    assert (
                        len([e for e in timeline["events"] if e["event"] == "parsed_packet"]) == 60
                    )
            else:
                assert not timing_file.exists()
            return 0
        finally:
            stop.set()
            if emitter.is_alive():
                emitter.join(1)
            runtime.stop()
            feed.close()
            scanner.waterfall_session.close()

    monkeypatch.setattr(cli, "main", fake_main)
    args = arguments(tmp_path)
    if continuity:
        args.insert(0, "--continuity")
    if timing:
        args.insert(0, "--timing")
    args[args.index("--ready-timeout") + 1] = "0.1" if outcome == "expiry" else "10"
    assert launcher.main(args) == 0
    assert "PRIVATE_SENTINEL" not in result_file.read_text()


@pytest.mark.parametrize("trial", [True], indirect=True)
def test_continuity_is_distinct_fixed_budget_not_unbounded_polling(trial):
    window, scanner, cache, clock, psi = trial
    assert window.max_opportunities == 60 and window.window_seconds == 64
    assert not window.allow_poll() and window.arm()
    for index in range(30):
        for offset in (0, 0.5):
            clock.now = 10 + index * 2 + offset
            psi()
            assert window.allow_poll() and cache.poll_once()
            assert window.report()["status"] == "qualification_unconfirmed"
    assert len(scanner.reads) == 60
    psi()
    psi()
    assert window.report()["status"] == "replies_and_psi_observed"
    assert window.report()["max_psi_gap_seconds"] == 1.5
    assert window.replies == {"DTM": 30, "FQK": 30}
    assert not window.allow_poll()
    window.stop()
    cache.poll_once()
    assert len(scanner.reads) == 60
    with pytest.raises(ValueError, match="rearmed"):
        window.arm()


@pytest.mark.parametrize("trial", [True], indirect=True)
@pytest.mark.parametrize("cause", ["gap", "deadline", "mode", "connection", "busy"])
def test_continuity_failures_cannot_resume_after_recovery(trial, cause):
    window, scanner, cache, clock, psi = trial
    assert window.arm()
    psi()
    cache.poll_once()
    clock.now = 10.5
    if cause == "gap":
        clock.now = 12.001
        psi()
        assert window.failure == "psi_gap_exceeded"
    elif cause == "deadline":
        clock.now = 74
    elif cause == "mode":
        window.observe_psi(None)
    elif cause == "connection":
        window.observe_connection(False)
        window.observe_connection(True)
    else:
        window.runtime.ready = False
        cache.poll_once()
        window.runtime.ready = True
    psi()
    cache.poll_once()
    assert not window.allow_poll() and len(scanner.reads) == 1
    assert window.report()["status"] == "qualification_unconfirmed"


@pytest.mark.parametrize("bad", [None, 1, "true", object()])
def test_unbounded_or_ambiguous_policy_is_rejected(tmp_path, bad):
    with pytest.raises(ValueError, match="boolean"):
        launcher.ReadWindow(object(), FIRMWARE, continuity=bad)
    with pytest.raises(ValueError, match="boolean"):
        launcher.SupplementalTrigger(tmp_path / "case", FIRMWARE, continuity=bad)
    assert not (tmp_path / "case").exists()


def test_trigger_and_window_cannot_disagree_on_case(tmp_path, trial):
    window, scanner, _, _, _ = trial
    trigger = launcher.SupplementalTrigger(tmp_path / "case", FIRMWARE, continuity=True)
    trigger.window = window
    with pytest.raises(ValueError, match="exact supplemental reader owner"):
        trigger.invoke(window.runtime)
    assert not scanner.reads and not window.attempted
