from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict

import pytest

from sds200 import AudioFanoutSession, AudioStream, DaemonRuntime, PcmSinkRouter
from sds200.commands import StartSystemStatusAnalysis
from sds200.daemon_api import DaemonReadOnlyApi
from sds200.daemon_system_status_research import (
    SystemStatusResearchAttempt,
    SystemStatusResearchPolicy,
    SystemStatusResearchStatus,
)
from sds200.events import EventBus
from sds200.exceptions import (
    CommandTimeoutError,
    DaemonControlBusyError,
    DaemonControlUnavailableError,
    UnsupportedScannerFeatureError,
)
from sds200.models import Packet, ScannerInfo
from sds200.system_status_research import SystemStatusProbeRefused
from sds200.waterfall_session import WaterfallSession
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport
from .test_daemon_runtime_controls import FakeControlScanner
from .test_system_status_research import info
from .test_waterfall_session import FakeWaterfallRadio


def analysis(*, extra: str = "", screen: str = "analyze_system_status") -> ScannerInfo:
    return ScannerInfoParser().parse(
        "PSI",
        f'<ScannerInfo Mode="Trunk Scan" V_Screen="{screen}">'
        f'<SystemStatus SystemID="00123h"/>{extra}</ScannerInfo>',
    )


class ResearchScanner(FakeControlScanner):
    def __init__(self) -> None:
        super().__init__([])
        self._connected = True
        self.bus = EventBus()
        self.waterfall_session = WaterfallSession(FakeWaterfallRadio())
        self.frames = [info(), info()]
        self.timeouts: list[float] = []
        self.stages: list[str] = []
        self.commands: list[str] = []
        self.model = "SDS200"
        self.firmware = "Version 1.00.00"
        self.ack = "OK"
        self.after_stage: Callable[[str], None] = lambda _: None
        self.on_execute: Callable[[], None] = lambda: None
        self.post_ack: ScannerInfo | None = analysis()
        self.producer: threading.Thread | None = None
        self.producer_stop = threading.Event()
        self.hook_count = 0

    def _stage(self, name: str, timeout: float) -> None:
        self.stages.append(name)
        self.timeouts.append(timeout)
        self.after_stage(name)

    @contextmanager
    def _system_status_research_scope(self, *, timeout: float) -> Iterator[None]:
        self._stage("scope", timeout)
        yield

    def get_model(self, *, timeout: float = 2.0) -> str:
        self._stage("MDL", timeout)
        return self.model

    def get_firmware(self, *, timeout: float = 2.0) -> str:
        self._stage("VER", timeout)
        return self.firmware

    def get_scanner_info(self, *, timeout: float = 3.0) -> ScannerInfo:
        self._stage("GSI", timeout)
        return self.frames.pop(0)

    def execute(self, command: StartSystemStatusAnalysis, *, timeout: float) -> None:
        self.commands.append(command.wire)
        self._stage("AST", timeout)
        self.on_execute()
        command.parse_response(Packet(command="AST", fields=(self.ack,), raw="omitted"))
        if self.post_ack is not None:

            def publish() -> None:
                while not self.producer_stop.wait(0.002):
                    self.bus.emit("psi", self.post_ack)

            self.producer = threading.Thread(target=publish, daemon=True)
            self.producer.start()

    def _hook(self, event: str, callback: Callable) -> Callable[[], None]:  # type: ignore[type-arg]
        self.hook_count += 1
        unsubscribe = self.bus.subscribe(event, callback)

        def remove() -> None:
            self.hook_count -= 1
            unsubscribe()

        return remove

    def on_psi(self, callback: Callable) -> Callable[[], None]:  # type: ignore[type-arg]
        return self._hook("psi", callback)

    def on_connection(self, callback: Callable) -> Callable[[], None]:  # type: ignore[type-arg]
        return self._hook("connection", callback)

    def stop_producer(self) -> None:
        self.producer_stop.set()
        if self.producer is not None:
            self.producer.join(timeout=1)
            assert not self.producer.is_alive()


@pytest.fixture
def scanner() -> Iterator[ResearchScanner]:
    value = ResearchScanner()
    try:
        yield value
    finally:
        value.stop_producer()


def attempt() -> SystemStatusResearchAttempt:
    return SystemStatusResearchAttempt(SystemStatusResearchPolicy("Version 1.00.00"))


def runtime_for(scanner: ResearchScanner, *, enabled: bool = True) -> DaemonRuntime:
    router = PcmSinkRouter(name="research-test")
    audio = AudioFanoutSession(AudioStream(FakeAudioTransport()), (router,))
    return DaemonRuntime(
        scanner,
        audio,
        router,
        system_status_research=SystemStatusResearchPolicy(scanner.firmware) if enabled else None,
    )


def test_single_exact_start_observed_after_ack_and_cleanup(scanner: ResearchScanner) -> None:
    probe = attempt()
    result = probe.run(scanner, operator_ready=True, timeout=0.2)
    assert result.status is SystemStatusResearchStatus.ANALYSIS_OBSERVED
    assert result.acknowledged and result.analysis_observed and result.start_reserved
    assert not result.connection_changed and result.failure is None
    assert scanner.commands == ["AST,SYSTEM_STATUS,240"]
    assert scanner.stages == ["scope", "MDL", "VER", "GSI", "GSI", "AST"]
    assert scanner.timeouts == sorted(scanner.timeouts, reverse=True)
    assert all(0 < value <= 0.2 for value in scanner.timeouts)
    assert scanner.hook_count == 0
    with scanner.waterfall_session.reserve_idle_for_research():
        pass
    with pytest.raises(SystemStatusProbeRefused, match="already attempted"):
        probe.run(scanner, operator_ready=True, timeout=0.2)


def test_analysis_observation_does_not_require_or_invent_identifiers(
    scanner: ResearchScanner,
) -> None:
    # Synthetic minimal shape matching the qualified optional-attribute boundary.
    # Observing analysis is separate from receiving identifiers or target parity.
    scanner.post_ack = ScannerInfoParser().parse(
        "PSI",
        '<ScannerInfo Mode="Trunk Scan" V_Screen="analyze_system_status">'
        '<SystemStatus Signal="10" Quality="0" Activity="0"/></ScannerInfo>',
    )
    result = attempt().run(scanner, operator_ready=True, timeout=0.2)
    assert result.status is SystemStatusResearchStatus.ANALYSIS_OBSERVED
    assert result.acknowledged and result.analysis_observed
    assert scanner.post_ack.system_statuses[0].attributes == {
        "Signal": "10",
        "Quality": "0",
        "Activity": "0",
    }
    assert set(asdict(result)) == {
        "status",
        "start_reserved",
        "acknowledged",
        "analysis_observed",
        "connection_changed",
        "elapsed_seconds",
        "failure",
    }


@pytest.mark.parametrize(
    "field,value", [("model", "SDS100"), ("model", "SDS150"), ("firmware", "Version 1.01.00")]
)
def test_identity_pin_refuses_before_selection_or_start(
    scanner: ResearchScanner, field: str, value: str
) -> None:
    setattr(scanner, field, value)
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is SystemStatusResearchStatus.NOT_STARTED
    assert result.failure == "refused"
    assert scanner.commands == [] and "GSI" not in scanner.stages
    assert scanner.hook_count == 0


@pytest.mark.parametrize(
    "frame",
    [
        info(site="241"),
        info(system="121"),
        info(screen="analyze_system_status"),
        info(extra="<PopupScreen/>"),
        info(site="4294967295"),
    ],
)
def test_selection_change_or_bad_recheck_never_sends(
    scanner: ResearchScanner, frame: ScannerInfo
) -> None:
    scanner.frames[1] = frame
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is SystemStatusResearchStatus.NOT_STARTED
    assert scanner.commands == [] and scanner.hook_count == 0


def test_no_readiness_means_no_io_or_consumed_attempt(scanner: ResearchScanner) -> None:
    probe = attempt()
    for value in (False, None, 1, "yes"):
        with pytest.raises(SystemStatusProbeRefused, match="ready operator"):
            probe.run(scanner, operator_ready=value, timeout=0.2)  # type: ignore[arg-type]
    assert scanner.stages == []
    assert probe.run(scanner, operator_ready=True, timeout=0.2).analysis_observed


@pytest.mark.parametrize(
    "value", [0, -1, True, None, "2", float("nan"), float("inf"), 8.01, 10**1000]
)
def test_invalid_timeout_is_refused_without_io(scanner: ResearchScanner, value: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        attempt().run(scanner, operator_ready=True, timeout=value)  # type: ignore[arg-type]
    assert scanner.stages == []


@pytest.mark.parametrize("value", [None, "", " v1", "v1 ", "v\n1", "é", "x" * 65])
def test_bad_firmware_policy_is_sanitized(value: object) -> None:
    with pytest.raises(ValueError, match="exact firmware pin"):
        SystemStatusResearchPolicy(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("stage", ["MDL", "VER", "GSI"])
def test_disconnect_reconnect_during_preflight_refuses(
    scanner: ResearchScanner, stage: str
) -> None:
    def cycle(name: str) -> None:
        if name == stage:
            scanner.bus.emit("connection", False)
            scanner.bus.emit("connection", True)

    scanner.after_stage = cycle
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is SystemStatusResearchStatus.CONNECTION_CHANGED
    assert not result.start_reserved and scanner.commands == []


def test_disconnect_during_start_is_uncertain_and_not_retried(scanner: ResearchScanner) -> None:
    scanner.on_execute = lambda: scanner.bus.emit("connection", False)
    probe = attempt()
    result = probe.run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is SystemStatusResearchStatus.CONNECTION_CHANGED
    assert result.start_reserved and not result.analysis_observed
    with pytest.raises(SystemStatusProbeRefused):
        probe.run(scanner, operator_ready=True, timeout=0.1)
    assert len(scanner.commands) == 1


@pytest.mark.parametrize(
    "error", [CommandTimeoutError("private endpoint"), OSError("private endpoint")]
)
def test_start_error_is_sanitized_consumed_and_not_retried(
    scanner: ResearchScanner, error: Exception
) -> None:
    def fail() -> None:
        raise error

    scanner.on_execute = fail
    probe = attempt()
    result = probe.run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is SystemStatusResearchStatus.START_UNCONFIRMED
    assert not result.acknowledged and not result.analysis_observed
    assert "private" not in str(asdict(result))
    assert scanner.hook_count == 0
    with pytest.raises(SystemStatusProbeRefused):
        probe.run(scanner, operator_ready=True, timeout=0.1)
    assert len(scanner.commands) == 1


def test_nonexact_ack_and_pre_ack_frame_do_not_pass(scanner: ResearchScanner) -> None:
    scanner.on_execute = lambda: scanner.bus.emit("psi", analysis())
    scanner.ack = "NG"
    result = attempt().run(scanner, operator_ready=True, timeout=0.04)
    assert result.status is SystemStatusResearchStatus.START_UNCONFIRMED
    assert not result.acknowledged and not result.analysis_observed


def test_pre_ack_frame_does_not_pass_even_when_ack_later_succeeds(scanner: ResearchScanner) -> None:
    scanner.on_execute = lambda: scanner.bus.emit("psi", analysis())
    scanner.post_ack = None
    result = attempt().run(scanner, operator_ready=True, timeout=0.04)
    assert result.status is SystemStatusResearchStatus.ACKNOWLEDGED_ONLY
    assert result.acknowledged and not result.analysis_observed


@pytest.mark.parametrize(
    "frame",
    [
        None,
        info(),
        analysis(extra="<SystemStatus/>"),
        analysis(extra="<PopupScreen/>"),
        analysis(screen="trunk_scan"),
    ],
)
def test_no_qualified_post_ack_frame_does_not_pass(
    scanner: ResearchScanner, frame: ScannerInfo | None
) -> None:
    scanner.post_ack = frame
    result = attempt().run(scanner, operator_ready=True, timeout=0.04)
    assert result.status is SystemStatusResearchStatus.ACKNOWLEDGED_ONLY
    assert not result.analysis_observed
    assert scanner.hook_count == 0 and len(scanner.commands) == 1


def test_preflight_timeout_does_not_send_and_consumes_attempt(scanner: ResearchScanner) -> None:
    scanner.after_stage = lambda _: time.sleep(0.02)
    probe = attempt()
    result = probe.run(scanner, operator_ready=True, timeout=0.01)
    assert result.status is SystemStatusResearchStatus.NOT_STARTED
    assert result.failure == "timeout" and scanner.commands == []
    with pytest.raises(SystemStatusProbeRefused):
        probe.run(scanner, operator_ready=True, timeout=0.1)


def test_selection_preparation_expires_even_with_time_left(
    scanner: ResearchScanner, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [10.0]
    monkeypatch.setattr("sds200.daemon_system_status_research.monotonic", lambda: clock[0])

    def delay_second_gsi(stage: str) -> None:
        if stage == "GSI" and scanner.stages.count("GSI") == 2:
            clock[0] += 2.1

    scanner.after_stage = delay_second_gsi
    result = attempt().run(scanner, operator_ready=True, timeout=8)
    assert result.status is SystemStatusResearchStatus.NOT_STARTED
    assert result.failure == "refused" and scanner.commands == []


def test_exception_cleanup_releases_waterfall_reservation(scanner: ResearchScanner) -> None:
    def fail(stage: str) -> None:
        if stage == "MDL":
            raise RuntimeError("private diagnostic")

    scanner.after_stage = fail
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is SystemStatusResearchStatus.NOT_STARTED
    assert scanner.hook_count == 0
    with scanner.waterfall_session.subscribe():
        assert scanner.waterfall_session.consumer_count == 1


def test_idle_exclusion_prevents_concurrent_waterfall_start(scanner: ResearchScanner) -> None:
    def check() -> None:
        with pytest.raises(RuntimeError, match="reserved"):
            scanner.waterfall_session.subscribe()
        worker = threading.Thread(target=scanner.waterfall_session.mark_interrupted)
        worker.start()
        worker.join(timeout=0.5)
        assert not worker.is_alive(), "Receive callback must not block behind scanner I/O"

    scanner.on_execute = check
    assert attempt().run(scanner, operator_ready=True, timeout=0.2).analysis_observed


def test_existing_waterfall_is_not_stopped_by_research(scanner: ResearchScanner) -> None:
    with scanner.waterfall_session.subscribe():
        result = attempt().run(scanner, operator_ready=True, timeout=0.1)
        assert result.status is SystemStatusResearchStatus.NOT_STARTED
        assert scanner.stages == [] and scanner.waterfall_session.consumer_count == 1
    assert scanner.commands == [] and scanner.hook_count == 0


def test_runtime_disabled_by_default_and_never_runs_at_startup(scanner: ResearchScanner) -> None:
    runtime = runtime_for(scanner, enabled=False)
    try:
        runtime.start()
        with pytest.raises(UnsupportedScannerFeatureError, match="disabled"):
            runtime.run_system_status_research(operator_ready=True)
        assert scanner.commands == []
    finally:
        runtime.stop()


def test_enabled_runtime_still_requires_explicit_invocation(scanner: ResearchScanner) -> None:
    runtime = runtime_for(scanner)
    try:
        with pytest.raises(DaemonControlUnavailableError):
            runtime.run_system_status_research(operator_ready=True)
        runtime.start()
        assert scanner.commands == []
        result = runtime.run_system_status_research(operator_ready=True, timeout=0.2)
        assert result.analysis_observed
        with pytest.raises(SystemStatusProbeRefused, match="already attempted"):
            runtime.run_system_status_research(operator_ready=True, timeout=0.2)
        # Failed/repeated research leaves ordinary control locks usable.
        runtime.next("SYS", 1)
    finally:
        runtime.stop()
    assert scanner.hook_count == 0


def test_normal_controls_and_stop_are_serialized_with_research(scanner: ResearchScanner) -> None:
    runtime = runtime_for(scanner)
    runtime.start()
    entered = threading.Event()
    release = threading.Event()
    results = []

    def block() -> None:
        entered.set()
        assert release.wait(0.5)

    scanner.on_execute = block
    worker = threading.Thread(
        target=lambda: results.append(
            runtime.run_system_status_research(operator_ready=True, timeout=0.8)
        )
    )
    stopper = threading.Thread(target=runtime.stop)
    try:
        worker.start()
        assert entered.wait(0.5)
        with pytest.raises(DaemonControlBusyError):
            runtime.next("SYS", 1)
        with pytest.raises(DaemonControlBusyError):
            runtime.run_system_status_research(operator_ready=True)
        stopper.start()
        assert "scanner.close" not in scanner.order
        release.set()
        worker.join(timeout=1)
        stopper.join(timeout=1)
        assert not worker.is_alive() and not stopper.is_alive()
        assert len(results) == 1 and results[0].analysis_observed
        assert len(scanner.commands) == 1
    finally:
        release.set()
        worker.join(timeout=1)
        if stopper.ident is not None:
            stopper.join(timeout=1)
        runtime.stop()


def test_research_is_not_an_api_operation_even_on_opted_in_runtime(
    scanner: ResearchScanner,
) -> None:
    api = DaemonReadOnlyApi(runtime_for(scanner))
    response = api.handle_payload(
        {
            "protocol": "sdsctl.daemon",
            "version": 1,
            "request_id": "test",
            "operation": "research.system_status.start",
            "params": {},
        }
    )
    assert response.error is not None
    assert response.error.code.value == "unknown_operation"
    assert scanner.commands == []
