from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import asdict

import pytest

from sds200 import AudioFanoutSession, AudioStream, DaemonRuntime, PcmSinkRouter
from sds200.daemon_display_read_research import (
    DisplayReadKind,
    DisplayReadRefused,
    DisplayReadResearchAttempt,
    DisplayReadResearchPolicy,
)
from sds200.daemon_runtime import DaemonRuntimeState
from sds200.daemon_system_status_research import SystemStatusResearchPolicy
from sds200.events import EventBus
from sds200.exceptions import (
    CommandRejectedError,
    CommandTimeoutError,
    DaemonControlBusyError,
    ProtocolError,
    UnsupportedScannerFeatureError,
)
from sds200.models import Packet
from sds200.waterfall_session import WaterfallSession
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport
from .test_daemon_runtime_controls import FakeControlScanner
from .test_waterfall_session import FakeWaterfallRadio

FIRMWARE = "Version 1.26.01"
STATES = tuple(str(i % 3) for i in range(100))


def psi(*, favorites="01", system="23", screen="trunk_scan", extra="", scope=True):
    records = (
        f'<MonitorList Index="700" Q_Key="{favorites}"/>'
        f'<System Index="120" Q_Key="{system}"/><Site Index="240"/>'
        if scope
        else '<System Index="120"/><Site Index="240"/>'
    )
    return ScannerInfoParser().parse(
        "PSI", f'<ScannerInfo Mode="Trunk Scan" V_Screen="{screen}">{records}{extra}</ScannerInfo>'
    )


class Scanner(FakeControlScanner):
    def __init__(self):
        super().__init__([])
        self._connected = True
        self.bus = EventBus()
        self.waterfall_session = WaterfallSession(FakeWaterfallRadio())
        self.frame = psi()
        self.stages = []
        self.commands = []
        self.timeouts = []
        self.model, self.firmware = "SDS200", FIRMWARE
        self.after_stage = lambda name: None
        self.on_execute = lambda: None
        self.fields_override = None
        self.stop_event = threading.Event()
        self.producer = None
        self.hooks = 0
        self.scope_depth = 0

    def _stage(self, name, timeout):
        self.stages.append(name)
        self.timeouts.append(timeout)
        self.after_stage(name)

    @contextmanager
    def _display_read_research_scope(self, *, timeout):
        self._stage("scope", timeout)
        self.scope_depth += 1
        try:
            yield
        finally:
            self.scope_depth -= 1

    def get_model(self, *, timeout):
        self._stage("MDL", timeout)
        if self.producer is None:

            def publish():
                while not self.stop_event.wait(0.002):
                    frame = self.frame
                    if frame is not None:
                        self.bus.emit("psi", frame)

            self.producer = threading.Thread(target=publish, daemon=True)
            self.producer.start()
        return self.model

    def get_firmware(self, *, timeout):
        self._stage("VER", timeout)
        return self.firmware

    def execute(self, command, *, timeout):
        assert self.scope_depth == 1
        self.commands.append(command.wire)
        self._stage(command.response_command, timeout)
        self.on_execute()
        name = command.response_command
        fields = (
            ("0", "2026", "09", "17", "09", "45", "02", "1")
            if name == "DTM"
            else STATES
            if name == "FQK"
            else ("1", "23", *STATES)
        )
        if self.fields_override is not None:
            fields = self.fields_override
        packet = Packet(name, fields, raw="PRIVATE_WIRE_DATA")
        self.bus.emit("packet", packet)
        return command.parse_response(packet)

    def _hook(self, event, callback):
        self.hooks += 1
        unsubscribe = self.bus.subscribe(event, callback)

        def remove():
            self.hooks -= 1
            unsubscribe()

        return remove

    def on_psi(self, callback):
        return self._hook("psi", callback)

    def on_connection(self, callback):
        return self._hook("connection", callback)

    def on_packet(self, callback):
        return self._hook("packet", callback)

    def close(self):
        self.stop_event.set()
        if self.producer is not None:
            self.producer.join(timeout=1)
            assert not self.producer.is_alive()


@pytest.fixture
def scanner():
    value = Scanner()
    try:
        yield value
    finally:
        value.close()


def attempt(kind=DisplayReadKind.CLOCK):
    return DisplayReadResearchAttempt(DisplayReadResearchPolicy(FIRMWARE, kind))


@pytest.mark.parametrize(
    "kind,wire,count",
    [
        (DisplayReadKind.CLOCK, "DTM", 8),
        (DisplayReadKind.FAVORITES, "FQK", 100),
        (DisplayReadKind.SYSTEM, "SQK,1", 102),
        (DisplayReadKind.DEPARTMENT, "DQK,1,23", 102),
    ],
)
def test_one_exact_get_and_two_new_normal_frames_are_distinct_evidence(scanner, kind, wire, count):
    probe = attempt(kind)
    result = probe.run(scanner, operator_ready=True, timeout=0.15)
    assert result.status == "reply_and_psi_observed"
    assert result.response_validated and result.normal_psi_after_response >= 2
    assert result.read_reserved and result.failure is None
    assert scanner.commands == [wire]
    assert scanner.stages == ["scope", "MDL", "VER", wire.split(",")[0]]
    assert all(0 < n <= 0.15 for n in scanner.timeouts)
    assert scanner.scope_depth == scanner.hooks == 0
    assert result.response_shape["field_count"] == count
    assert "PRIVATE" not in str(asdict(result))
    if kind is DisplayReadKind.CLOCK:
        assert result.sample == {
            "scanner_local_time": "2026-09-17T09:45:02",
            "rtc_valid": True,
            "daylight_saving_token": "0",
            "timezone_known": False,
        }
    else:
        assert result.sample["states"] == [int(s) for s in STATES]
        if kind is DisplayReadKind.SYSTEM:
            assert result.sample["reported_system_quick_key"] == 23
    with pytest.raises(DisplayReadRefused, match="already attempted"):
        probe.run(scanner, operator_ready=True, timeout=0.15)


@pytest.mark.parametrize("kind", [DisplayReadKind.CLOCK, DisplayReadKind.FAVORITES])
def test_global_reads_do_not_need_or_invent_an_assigned_key(scanner, kind):
    scanner.frame = psi(scope=False)
    assert attempt(kind).run(scanner, operator_ready=True, timeout=0.1).sample is not None


@pytest.mark.parametrize(
    "kind,favorites,system",
    [
        (DisplayReadKind.SYSTEM, "None", "None"),
        (DisplayReadKind.DEPARTMENT, "None", "None"),
        (DisplayReadKind.DEPARTMENT, "1", "None"),
        (DisplayReadKind.SYSTEM, "100", "1"),
        (DisplayReadKind.SYSTEM, "None", "23"),
    ],
)
def test_unassigned_or_invalid_scope_is_not_key_zero_or_an_index(scanner, kind, favorites, system):
    scanner.frame = psi(favorites=favorites, system=system)
    result = attempt(kind).run(scanner, operator_ready=True, timeout=0.025)
    assert result.status == "not_started" and not result.read_reserved
    assert scanner.commands == [] and scanner.hooks == 0


@pytest.mark.parametrize(
    "frame",
    [
        None,
        psi(screen="analyze_system_status"),
        psi(screen="waterfall"),
        psi(extra="<PopupScreen/>"),
        psi(extra="<PlainText/>"),
        psi(extra="<System/>"),
        psi(extra="<ConvFrequency/>"),
    ],
)
@pytest.mark.parametrize("kind", list(DisplayReadKind))
def test_no_fresh_qualified_psi_means_no_get(scanner, frame, kind):
    scanner.frame = frame
    result = attempt(kind).run(scanner, operator_ready=True, timeout=0.025)
    assert result.status == "not_started" and result.sample is None
    assert scanner.commands == [] and scanner.hooks == 0


@pytest.mark.parametrize("field,value", [("model", "SDS100"), ("firmware", "Version 1.00.00")])
def test_identity_mismatch_never_dispatches_selected_get(scanner, field, value):
    setattr(scanner, field, value)
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status == "not_started" and result.failure == "refused"
    assert scanner.commands == []


@pytest.mark.parametrize("stage", ["MDL", "VER", "DTM"])
def test_disconnect_reconnect_is_terminal_even_if_a_reply_arrives(scanner, stage):
    def cycle(name):
        if name == stage:
            scanner.bus.emit("connection", False)
            scanner.bus.emit("connection", True)

    scanner.after_stage = cycle
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status == "connection_changed" and result.sample is None
    assert len(scanner.commands) == (1 if stage == "DTM" else 0)


def test_no_post_reply_psi_is_not_a_continuity_pass(scanner):
    def pause():
        scanner.frame = None
        # A valid pre-response event must not count as post-response progress.
        scanner.bus.emit("psi", psi())

    scanner.on_execute = pause
    result = attempt().run(scanner, operator_ready=True, timeout=0.025)
    assert result.status == "reply_only" and result.response_validated
    assert result.normal_psi_after_response == 0 and result.sample is None


@pytest.mark.parametrize(
    "kind,new_frame",
    [
        (DisplayReadKind.CLOCK, psi(screen="waterfall")),
        (DisplayReadKind.FAVORITES, psi(screen="waterfall")),
        (DisplayReadKind.SYSTEM, psi(favorites="2")),
        (DisplayReadKind.DEPARTMENT, psi(system="24")),
    ],
)
def test_scope_or_mode_change_and_return_cannot_revalidate_a_read(scanner, kind, new_frame):
    def change():
        scanner.bus.emit("psi", new_frame)
        scanner.bus.emit("psi", psi())

    scanner.on_execute = change
    result = attempt(kind).run(scanner, operator_ready=True, timeout=0.1)
    assert result.status == "scan_context_changed" and result.sample is None


@pytest.mark.parametrize(
    "error,failure",
    [
        (CommandTimeoutError("PRIVATE_HOST"), "timeout"),
        (CommandRejectedError("PRIVATE_HOST"), "rejected"),
        (ProtocolError("PRIVATE_HOST"), "invalid_response"),
        (OSError("PRIVATE_HOST"), "operation_failed"),
    ],
)
@pytest.mark.parametrize(
    "kind,wire",
    [
        (DisplayReadKind.CLOCK, "DTM"),
        (DisplayReadKind.FAVORITES, "FQK"),
        (DisplayReadKind.SYSTEM, "SQK,1"),
        (DisplayReadKind.DEPARTMENT, "DQK,1,23"),
    ],
)
def test_uncertain_read_stops_without_retry_or_follow_on_get(scanner, error, failure, kind, wire):
    def fail():
        raise error

    scanner.on_execute = fail
    probe = attempt(kind)
    result = probe.run(scanner, operator_ready=True, timeout=0.1)
    assert result.status == "read_unconfirmed" and result.failure == failure
    assert scanner.commands == [wire] and scanner.hooks == 0
    assert "PRIVATE" not in str(asdict(result))
    with pytest.raises(DisplayReadRefused):
        probe.run(scanner, operator_ready=True, timeout=0.1)
    with scanner.waterfall_session.reserve_idle_for_research():
        pass


def test_unexpected_sqk_reply_shape_is_evidence_not_a_guessed_layout(scanner):
    scanner.fields_override = ("1", *STATES)
    result = attempt(DisplayReadKind.SYSTEM).run(scanner, operator_ready=True, timeout=0.1)
    assert result.failure == "invalid_response" and result.sample is None
    assert result.response_shape == {"field_count": 101, "state_token_count": 101}
    assert scanner.commands == ["SQK,1"]


def test_invalid_rtc_is_reported_as_invalid_without_an_invented_clock(scanner):
    scanner.fields_override = ("0", "0000", "00", "00", "00", "00", "00", "0")
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status == "reply_and_psi_observed"
    assert result.sample["rtc_valid"] is False and result.sample["scanner_local_time"] is None


def test_research_never_stops_an_active_waterfall(scanner):
    with scanner.waterfall_session.subscribe():
        result = attempt().run(scanner, operator_ready=True, timeout=0.1)
        assert result.status == "not_started"
        assert scanner.waterfall_session.consumer_count == 1
    assert scanner.commands == [] and scanner.stages == []


def test_research_reservation_excludes_new_waterfall_consumers(scanner):
    def during():
        with pytest.raises(RuntimeError, match="reserved"):
            scanner.waterfall_session.subscribe()

    scanner.on_execute = during
    assert attempt().run(scanner, operator_ready=True, timeout=0.1).response_validated


@pytest.mark.parametrize("bad", [False, None, 1, "yes"])
def test_missing_operator_readiness_never_consumes_attempt(scanner, bad):
    probe = attempt()
    with pytest.raises(DisplayReadRefused, match="ready operator"):
        probe.run(scanner, operator_ready=bad, timeout=0.1)
    assert scanner.stages == []
    assert probe.run(scanner, operator_ready=True, timeout=0.1).response_validated


@pytest.mark.parametrize(
    "bad", [0, -1, True, None, "2", float("nan"), float("inf"), 8.01, 10**1000]
)
def test_invalid_budgets_are_rejected_before_io(scanner, bad):
    with pytest.raises(ValueError):
        attempt().run(scanner, operator_ready=True, timeout=bad)
    assert scanner.stages == []


@pytest.mark.parametrize(
    "firmware,kind",
    [
        ("", DisplayReadKind.CLOCK),
        ("bad\n", DisplayReadKind.CLOCK),
        (FIRMWARE, "clock"),
        (FIRMWARE, "DTM,0,2026"),
    ],
)
def test_policy_is_pinned_not_arbitrary_command_text(firmware, kind):
    with pytest.raises(ValueError):
        DisplayReadResearchPolicy(firmware, kind)


def runtime_for(scanner, **kwargs):
    router = PcmSinkRouter(name="read-research")
    audio = AudioFanoutSession(AudioStream(FakeAudioTransport()), (router,))
    return DaemonRuntime(scanner, audio, router, **kwargs)


def test_runtime_is_disabled_by_default_and_serializes_opt_in_with_controls(scanner):
    runtime = runtime_for(scanner)
    with pytest.raises(UnsupportedScannerFeatureError, match="disabled"):
        runtime.run_display_read_research(operator_ready=True)
    runtime = runtime_for(
        scanner, display_read_research=DisplayReadResearchPolicy(FIRMWARE, DisplayReadKind.CLOCK)
    )
    runtime._state = DaemonRuntimeState.RUNNING
    with runtime._control_lock, pytest.raises(DaemonControlBusyError):
        runtime.run_display_read_research(operator_ready=True)
    assert scanner.commands == []
    assert runtime.run_display_read_research(operator_ready=True, timeout=0.1).response_validated
    with pytest.raises(DisplayReadRefused, match="already attempted"):
        runtime.run_display_read_research(operator_ready=True)


def test_two_research_policies_cannot_share_a_runtime(scanner):
    with pytest.raises(ValueError, match="one research policy"):
        runtime_for(
            scanner,
            display_read_research=DisplayReadResearchPolicy(FIRMWARE, DisplayReadKind.CLOCK),
            system_status_research=SystemStatusResearchPolicy(FIRMWARE),
        )
    with pytest.raises(TypeError, match="explicit policy"):
        runtime_for(scanner, display_read_research=True)


def test_slow_completed_reply_is_unconfirmed(scanner):
    scanner.on_execute = lambda: time.sleep(0.26)
    result = attempt().run(scanner, operator_ready=True, timeout=0.5)
    assert result.failure == "timeout" and not result.response_validated
    assert scanner.commands == ["DTM"]


def test_stale_psi_is_not_enough_to_dispatch_even_when_identity_passes(scanner, monkeypatch):
    monkeypatch.setattr("sds200.daemon_display_read_research.MAX_PSI_AGE", 0.01)

    def stale(name):
        if name == "VER":
            scanner.frame = None
            scanner.bus.emit("psi", psi())
            time.sleep(0.03)

    scanner.after_stage = stale
    result = attempt().run(scanner, operator_ready=True, timeout=0.07)
    assert result.status == "not_started" and result.failure == "timeout"
    assert scanner.commands == []


def test_captured_unpadded_clock_reply_qualifies_offline_without_extra_reads(scanner):
    from .test_clock_reads import CAPTURED_FIELDS

    scanner.fields_override = CAPTURED_FIELDS
    result = attempt().run(scanner, operator_ready=True, timeout=0.15)
    assert result.status == "reply_and_psi_observed"
    assert result.sample == {
        "scanner_local_time": "2026-09-17T03:38:10",
        "rtc_valid": True,
        "daylight_saving_token": "1",
        "timezone_known": False,
    }
    assert result.normal_psi_after_response >= 2
    assert result.response_shape["field_count"] == 8
    assert scanner.commands == ["DTM"]
