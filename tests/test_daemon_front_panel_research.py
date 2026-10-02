from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict

import pytest

from sds200 import AudioFanoutSession, AudioStream, DaemonRuntime, PcmSinkRouter
from sds200.commands import PressFrontPanelKey
from sds200.daemon_api import DaemonReadOnlyApi
from sds200.daemon_display_read_research import DisplayReadKind, DisplayReadResearchPolicy
from sds200.daemon_front_panel_research import (
    FrontPanelResearchAttempt,
    FrontPanelResearchPolicy,
    FrontPanelResearchRefused,
    FrontPanelResearchStatus,
)
from sds200.daemon_runtime import DaemonRuntimeState
from sds200.daemon_system_status_research import SystemStatusResearchPolicy
from sds200.events import EventBus
from sds200.exceptions import (
    CommandTimeoutError,
    DaemonControlBusyError,
    UnsupportedScannerFeatureError,
)
from sds200.front_panel_keys import FrontPanelKey
from sds200.models import Packet, ScannerInfo
from sds200.waterfall_session import WaterfallSession
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport
from .test_daemon_runtime_controls import FakeControlScanner
from .test_waterfall_session import FakeWaterfallRadio

FIRMWARE = "Version 1.26.01"
MODE = "Trunk Scan"
SCREEN = "trunk_scan"


def frame(
    command: str = "GSI",
    *,
    mode: str = MODE,
    screen: str = SCREEN,
    extra: str = "",
) -> ScannerInfo:
    return ScannerInfoParser().parse(
        command,
        f'<ScannerInfo Mode="{mode}" V_Screen="{screen}">'
        f'<System Index="120"/><Site Index="240"/>{extra}</ScannerInfo>',
    )


class Scanner(FakeControlScanner):
    def __init__(self) -> None:
        super().__init__([])
        self._connected = True
        self.bus = EventBus()
        self.waterfall_session = WaterfallSession(FakeWaterfallRadio())
        self.frames = [frame(), frame()]
        self.model = "SDS200"
        self.firmware = FIRMWARE
        self.stages: list[str] = []
        self.timeouts: list[float] = []
        self.commands: list[str] = []
        self.ack = "OK"
        self.post_ack: ScannerInfo | None = frame("PSI", mode="Menu", screen="menu")
        self.after_stage: Callable[[str], None] = lambda _name: None
        self.on_execute: Callable[[], None] = lambda: None
        self.stop_event = threading.Event()
        self.producer: threading.Thread | None = None
        self.hooks = 0

    def _stage(self, name: str, timeout: float) -> None:
        self.stages.append(name)
        self.timeouts.append(timeout)
        self.after_stage(name)

    @contextmanager
    def _front_panel_research_scope(self, *, timeout: float) -> Iterator[None]:
        self._stage("scope", timeout)
        yield

    def get_model(self, *, timeout: float) -> str:
        self._stage("MDL", timeout)
        return self.model

    def get_firmware(self, *, timeout: float) -> str:
        self._stage("VER", timeout)
        return self.firmware

    def get_scanner_info(self, *, timeout: float) -> ScannerInfo:
        self._stage("GSI", timeout)
        return self.frames.pop(0)

    def execute(self, command: PressFrontPanelKey, *, timeout: float) -> None:
        self.commands.append(command.wire)
        self._stage("KEY", timeout)
        self.on_execute()
        command.parse_response(Packet("KEY", (self.ack,), "omitted"))
        if self.post_ack is not None:

            def publish() -> None:
                while not self.stop_event.wait(0.002):
                    self.bus.emit("psi", self.post_ack)

            self.producer = threading.Thread(target=publish, daemon=True)
            self.producer.start()

    def _hook(self, event: str, callback: Callable) -> Callable[[], None]:  # type: ignore[type-arg]
        self.hooks += 1
        unsubscribe = self.bus.subscribe(event, callback)

        def remove() -> None:
            self.hooks -= 1
            unsubscribe()

        return remove

    def on_psi(self, callback: Callable) -> Callable[[], None]:  # type: ignore[type-arg]
        return self._hook("psi", callback)

    def on_connection(self, callback: Callable) -> Callable[[], None]:  # type: ignore[type-arg]
        return self._hook("connection", callback)

    def close_research(self) -> None:
        self.stop_event.set()
        if self.producer is not None:
            self.producer.join(timeout=1)
            assert not self.producer.is_alive()


@pytest.fixture
def scanner() -> Iterator[Scanner]:
    value = Scanner()
    try:
        yield value
    finally:
        value.close_research()


def policy(key: FrontPanelKey = FrontPanelKey.MENU) -> FrontPanelResearchPolicy:
    return FrontPanelResearchPolicy(FIRMWARE, key, MODE, SCREEN)


def attempt(key: FrontPanelKey = FrontPanelKey.MENU) -> FrontPanelResearchAttempt:
    return FrontPanelResearchAttempt(policy(key))


def runtime_for(scanner: Scanner, **kwargs: object) -> DaemonRuntime:
    router = PcmSinkRouter(name="front-panel-research")
    audio = AudioFanoutSession(AudioStream(FakeAudioTransport()), (router,))
    return DaemonRuntime(scanner, audio, router, **kwargs)  # type: ignore[arg-type]


def test_one_typed_press_after_two_exact_preflights_and_post_ack_frame(
    scanner: Scanner,
) -> None:
    probe = attempt()
    result = probe.run(scanner, operator_ready=True, timeout=0.2)
    assert result.status is FrontPanelResearchStatus.POST_ACK_FRAME_OBSERVED
    assert result.key_code == "M"
    assert result.press_reserved and result.acknowledged and result.post_ack_frame_observed
    assert result.context_changed
    assert (result.post_ack_mode, result.post_ack_screen) == ("Menu", "menu")
    assert result.failure is None
    assert scanner.commands == ["KEY,M,P"]
    assert scanner.stages == ["scope", "MDL", "VER", "GSI", "GSI", "KEY"]
    assert scanner.timeouts == sorted(scanner.timeouts, reverse=True)
    assert scanner.hooks == 0
    assert set(asdict(result)) == {
        "key_code",
        "status",
        "press_reserved",
        "acknowledged",
        "post_ack_frame_observed",
        "connection_changed",
        "context_changed",
        "post_ack_mode",
        "post_ack_screen",
        "elapsed_seconds",
        "failure",
    }
    with pytest.raises(FrontPanelResearchRefused, match="already attempted"):
        probe.run(scanner, operator_ready=True, timeout=0.1)


@pytest.mark.parametrize("key", list(FrontPanelKey))
def test_every_inventory_key_is_typed_but_no_sequence_is_created(
    scanner: Scanner, key: FrontPanelKey
) -> None:
    scanner.post_ack = frame("PSI")
    result = attempt(key).run(scanner, operator_ready=True, timeout=0.1)
    assert result.key_code == key.value
    assert scanner.commands == [f"KEY,{key.value},P"]
    assert result.context_changed is False


@pytest.mark.parametrize(
    "field,value",
    [("model", "SDS100"), ("firmware", "Version 1.27.00")],
)
def test_identity_mismatch_refuses_before_gsi_or_key(
    scanner: Scanner, field: str, value: str
) -> None:
    setattr(scanner, field, value)
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is FrontPanelResearchStatus.NOT_STARTED
    assert result.failure == "refused"
    assert scanner.commands == [] and "GSI" not in scanner.stages


@pytest.mark.parametrize(
    "bad_frame",
    [
        frame(mode="Scan Hold"),
        frame(screen="conventional_scan"),
        frame(extra="<PopupScreen/>"),
        frame(extra='<PlainText Text="private"/>'),
    ],
)
def test_context_mismatch_or_overlay_refuses_before_key(
    scanner: Scanner, bad_frame: ScannerInfo
) -> None:
    scanner.frames[1] = bad_frame
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is FrontPanelResearchStatus.NOT_STARTED
    assert result.failure == "refused" and scanner.commands == []


def test_no_operator_readiness_performs_no_io_and_does_not_consume_attempt(
    scanner: Scanner,
) -> None:
    probe = attempt()
    for value in (False, None, 1, "yes"):
        with pytest.raises(FrontPanelResearchRefused, match="ready operator"):
            probe.run(scanner, operator_ready=value, timeout=0.1)  # type: ignore[arg-type]
    assert scanner.stages == []
    assert probe.run(scanner, operator_ready=True, timeout=0.1).acknowledged


def test_pre_ack_psi_and_missing_post_ack_frame_do_not_confirm_context(
    scanner: Scanner,
) -> None:
    scanner.on_execute = lambda: scanner.bus.emit("psi", frame("PSI", mode="Menu", screen="menu"))
    scanner.post_ack = None
    result = attempt().run(scanner, operator_ready=True, timeout=0.03)
    assert result.status is FrontPanelResearchStatus.ACKNOWLEDGED_ONLY
    assert result.acknowledged and not result.post_ack_frame_observed


@pytest.mark.parametrize(
    "error", [CommandTimeoutError("private endpoint"), OSError("private endpoint")]
)
def test_uncertain_send_is_sanitized_and_never_retried(scanner: Scanner, error: Exception) -> None:
    def fail() -> None:
        raise error

    scanner.on_execute = fail
    probe = attempt()
    result = probe.run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is FrontPanelResearchStatus.PRESS_UNCONFIRMED
    assert result.press_reserved and not result.acknowledged
    assert "private" not in str(asdict(result)).lower()
    assert scanner.commands == ["KEY,M,P"]
    with pytest.raises(FrontPanelResearchRefused, match="already attempted"):
        probe.run(scanner, operator_ready=True, timeout=0.1)


def test_disconnect_after_dispatch_is_terminal(scanner: Scanner) -> None:
    scanner.on_execute = lambda: scanner.bus.emit("connection", False)
    result = attempt().run(scanner, operator_ready=True, timeout=0.1)
    assert result.status is FrontPanelResearchStatus.CONNECTION_CHANGED
    assert result.press_reserved and result.connection_changed
    assert scanner.commands == ["KEY,M,P"]


def test_existing_waterfall_is_never_stopped_to_make_room(scanner: Scanner) -> None:
    with scanner.waterfall_session.subscribe():
        result = attempt().run(scanner, operator_ready=True, timeout=0.1)
        assert result.status is FrontPanelResearchStatus.NOT_STARTED
        assert scanner.stages == []
        assert scanner.waterfall_session.consumer_count == 1
    assert scanner.commands == [] and scanner.hooks == 0


@pytest.mark.parametrize("value", [0, -1, True, None, "2", float("nan"), float("inf"), 8.01])
def test_invalid_timeout_is_refused_without_io(scanner: Scanner, value: object) -> None:
    with pytest.raises(ValueError, match="timeout"):
        attempt().run(scanner, operator_ready=True, timeout=value)  # type: ignore[arg-type]
    assert scanner.stages == []


@pytest.mark.parametrize(
    "args",
    [
        ("", FrontPanelKey.MENU, MODE, SCREEN),
        ("bad\n", FrontPanelKey.MENU, MODE, SCREEN),
        (FIRMWARE, "M", MODE, SCREEN),
        (FIRMWARE, FrontPanelKey.MENU, "", SCREEN),
        (FIRMWARE, FrontPanelKey.MENU, MODE, "x,private"),
    ],
)
def test_policy_requires_exact_bounded_typed_pins(args: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="exact firmware"):
        FrontPanelResearchPolicy(*args)  # type: ignore[arg-type]


def test_runtime_disabled_by_default_and_opt_in_is_explicit(scanner: Scanner) -> None:
    runtime = runtime_for(scanner)
    with pytest.raises(UnsupportedScannerFeatureError, match="disabled"):
        runtime.run_front_panel_research(operator_ready=True)
    runtime = runtime_for(scanner, front_panel_research=policy())
    runtime._state = DaemonRuntimeState.RUNNING
    result = runtime.run_front_panel_research(operator_ready=True, timeout=0.1)
    assert result.acknowledged and scanner.commands == ["KEY,M,P"]


def test_runtime_serializes_research_with_normal_controls_without_consuming_attempt(
    scanner: Scanner,
) -> None:
    runtime = runtime_for(scanner, front_panel_research=policy())
    runtime._state = DaemonRuntimeState.RUNNING
    with runtime._control_lock, pytest.raises(DaemonControlBusyError):
        runtime.run_front_panel_research(operator_ready=True, timeout=0.1)
    assert scanner.commands == []
    assert runtime.run_front_panel_research(operator_ready=True, timeout=0.1).acknowledged


def test_opt_in_research_is_not_a_public_api_operation(scanner: Scanner) -> None:
    api = DaemonReadOnlyApi(runtime_for(scanner, front_panel_research=policy()))
    response = api.handle_payload(
        {
            "protocol": "sdsctl.daemon",
            "version": 1,
            "request_id": "test",
            "operation": "scanner.front_panel.press",
            "params": {"key": "M"},
        }
    )
    assert response.error is not None
    assert response.error.code.value == "unknown_operation"
    assert scanner.commands == []


@pytest.mark.parametrize(
    "other",
    [
        {"system_status_research": SystemStatusResearchPolicy(FIRMWARE)},
        {"display_read_research": DisplayReadResearchPolicy(FIRMWARE, DisplayReadKind.CLOCK)},
    ],
)
def test_front_panel_policy_is_mutually_exclusive_with_other_research(
    scanner: Scanner, other: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="one research policy"):
        runtime_for(scanner, front_panel_research=policy(), **other)
    with pytest.raises(TypeError, match="explicit policy"):
        runtime_for(scanner, front_panel_research=True)
