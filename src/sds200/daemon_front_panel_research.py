"""Internal, opt-in, one-press front-panel qualification.

The runtime holds its normal control/lifecycle scope before calling this
helper.  There is deliberately no ordinary CLI/config/API registration,
polling, retry, sequence, held press, renderer action, or permission grant.
An acknowledgement proves only that the scanner accepted one KEY packet; a
later PSI frame is reported separately and is not treated as a transaction ID.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Protocol

from .commands import PressFrontPanelKey
from .exceptions import CommandTimeoutError
from .front_panel_keys import FrontPanelKey
from .models import ScannerInfo

FRONT_PANEL_RESEARCH_MAX_TIMEOUT = 8.0
_SAFE_CONTEXT = re.compile(r"[A-Za-z0-9._ /-]{1,64}")
_DISALLOWED_CONTEXT_TAGS = frozenset(
    {"PopupScreen", "PlainText", "ReplayDescription", "ReplayMode"}
)


class FrontPanelResearchRefused(ValueError):
    """Fixed-text refusal without private scanner or transport values."""


@dataclass(frozen=True, slots=True)
class FrontPanelResearchPolicy:
    """Exact SDS200 firmware, key and visible pre-press context pin."""

    expected_firmware: str
    key: FrontPanelKey
    expected_mode: str
    expected_screen: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.expected_firmware, str)
            or _SAFE_CONTEXT.fullmatch(self.expected_firmware) is None
            or self.expected_firmware != self.expected_firmware.strip()
            or type(self.key) is not FrontPanelKey
            or not isinstance(self.expected_mode, str)
            or _SAFE_CONTEXT.fullmatch(self.expected_mode) is None
            or self.expected_mode != self.expected_mode.strip()
            or not isinstance(self.expected_screen, str)
            or _SAFE_CONTEXT.fullmatch(self.expected_screen) is None
            or self.expected_screen != self.expected_screen.strip()
        ):
            raise ValueError(
                "Front-panel research requires exact firmware, typed key, mode and screen pins."
            )


class FrontPanelResearchStatus(StrEnum):
    NOT_STARTED = "not_started"
    PRESS_UNCONFIRMED = "press_unconfirmed"
    ACKNOWLEDGED_ONLY = "acknowledged_only"
    POST_ACK_FRAME_OBSERVED = "post_ack_frame_observed"
    CONNECTION_CHANGED = "connection_changed"


@dataclass(frozen=True, slots=True)
class FrontPanelResearchResult:
    key_code: str
    status: FrontPanelResearchStatus
    press_reserved: bool
    acknowledged: bool
    post_ack_frame_observed: bool
    connection_changed: bool
    context_changed: bool
    post_ack_mode: str | None
    post_ack_screen: str | None
    elapsed_seconds: float
    failure: str | None = None


class _IdleReservation(Protocol):
    def reserve_idle_for_research(self) -> AbstractContextManager[None]: ...


class _FrontPanelResearchScanner(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def waterfall_session(self) -> _IdleReservation: ...

    def _front_panel_research_scope(self, *, timeout: float) -> AbstractContextManager[None]: ...

    def get_model(self, *, timeout: float) -> str: ...

    def get_firmware(self, *, timeout: float) -> str: ...

    def get_scanner_info(self, *, timeout: float) -> ScannerInfo: ...

    def execute(self, command: PressFrontPanelKey, *, timeout: float) -> None: ...

    def on_psi(self, callback: Callable[[ScannerInfo], None]) -> Callable[[], None]: ...

    def on_connection(self, callback: Callable[[bool], None]) -> Callable[[], None]: ...


def front_panel_research_timeout(value: float) -> float:
    if (
        type(value) not in (int, float)
        or not 0 < value <= FRONT_PANEL_RESEARCH_MAX_TIMEOUT
        or not isfinite(value)
    ):
        raise ValueError("Front-panel research timeout must be greater than zero and at most 8s.")
    return float(value)


def _safe_context_value(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or _SAFE_CONTEXT.fullmatch(value) is None
    ):
        return None
    return value


def _qualified_context(
    info: ScannerInfo,
    *,
    command: str,
    expected_mode: str | None = None,
    expected_screen: str | None = None,
) -> tuple[str, str] | None:
    if not isinstance(info, ScannerInfo) or info.command != command:
        return None
    mode = _safe_context_value(info.mode)
    screen = _safe_context_value(info.screen)
    if mode is None or screen is None:
        return None
    if expected_mode is not None and mode != expected_mode:
        return None
    if expected_screen is not None and screen != expected_screen:
        return None
    if any(info.records_by_tag(tag) for tag in _DISALLOWED_CONTEXT_TAGS):
        return None
    return mode, screen


class FrontPanelResearchAttempt:
    """One exact press per runtime, including preflight and uncertain failures."""

    def __init__(self, policy: FrontPanelResearchPolicy) -> None:
        self._policy = policy
        self._attempted = False
        self._lock = threading.Lock()

    def run(
        self,
        scanner: _FrontPanelResearchScanner,
        *,
        operator_ready: bool,
        timeout: float,
    ) -> FrontPanelResearchResult:
        budget = front_panel_research_timeout(timeout)
        if operator_ready is not True:
            raise FrontPanelResearchRefused("Front-panel research requires a ready operator.")
        with self._lock:
            if self._attempted:
                raise FrontPanelResearchRefused("Front-panel research was already attempted.")
            self._attempted = True

        started = monotonic()
        deadline = started + budget
        condition = threading.Condition()
        connection_changed = False
        press_reserved = False
        acknowledged = False
        post_ack_at: float | None = None
        post_ack_context: tuple[str, str] | None = None
        context_changed = False
        failure: str | None = None
        expected = (self._policy.expected_mode, self._policy.expected_screen)

        def remaining() -> float:
            value = deadline - monotonic()
            if value <= 0:
                raise CommandTimeoutError("Front-panel research deadline expired.")
            return value

        def connection(connected: bool) -> None:
            nonlocal connection_changed
            with condition:
                if connected is not True:
                    connection_changed = True
                condition.notify_all()

        def psi(info: ScannerInfo) -> None:
            nonlocal post_ack_context, context_changed
            received = monotonic()
            context = _qualified_context(info, command="PSI")
            with condition:
                if (
                    post_ack_at is not None
                    and post_ack_at <= received <= deadline
                    and not connection_changed
                    and post_ack_context is None
                    and context is not None
                ):
                    post_ack_context = context
                    context_changed = context != expected
                    condition.notify_all()

        try:
            with ExitStack() as stack:
                stack.callback(scanner.on_connection(connection))
                stack.callback(scanner.on_psi(psi))
                stack.enter_context(scanner.waterfall_session.reserve_idle_for_research())
                stack.enter_context(scanner._front_panel_research_scope(timeout=remaining()))
                if not scanner.connected:
                    raise FrontPanelResearchRefused("Front-panel research requires a connection.")
                if scanner.get_model(timeout=remaining()) != "SDS200":
                    raise FrontPanelResearchRefused("Front-panel research model is not qualified.")
                if scanner.get_firmware(timeout=remaining()) != self._policy.expected_firmware:
                    raise FrontPanelResearchRefused("Front-panel research firmware pin differs.")
                for _ in range(2):
                    frame = scanner.get_scanner_info(timeout=remaining())
                    if connection_changed or not scanner.connected:
                        raise FrontPanelResearchRefused("Front-panel research connection changed.")
                    if (
                        _qualified_context(
                            frame,
                            command="GSI",
                            expected_mode=self._policy.expected_mode,
                            expected_screen=self._policy.expected_screen,
                        )
                        != expected
                    ):
                        raise FrontPanelResearchRefused(
                            "Front-panel research context is not qualified."
                        )
                remaining()
                press_reserved = True
                scanner.execute(PressFrontPanelKey(self._policy.key), timeout=remaining())
                acknowledged = True
                with condition:
                    post_ack_at = monotonic()
                    while post_ack_context is None and not connection_changed:
                        wait_for = deadline - monotonic()
                        if wait_for <= 0:
                            break
                        condition.wait(wait_for)
        except Exception as error:
            failure = (
                "timeout"
                if isinstance(error, CommandTimeoutError)
                else "refused"
                if isinstance(error, FrontPanelResearchRefused)
                else "operation_failed"
            )

        observed = post_ack_context is not None
        if connection_changed:
            status = FrontPanelResearchStatus.CONNECTION_CHANGED
        elif observed and acknowledged:
            status = FrontPanelResearchStatus.POST_ACK_FRAME_OBSERVED
        elif acknowledged:
            status = FrontPanelResearchStatus.ACKNOWLEDGED_ONLY
        elif press_reserved:
            status = FrontPanelResearchStatus.PRESS_UNCONFIRMED
        else:
            status = FrontPanelResearchStatus.NOT_STARTED
        post_mode, post_screen = post_ack_context or (None, None)
        return FrontPanelResearchResult(
            key_code=self._policy.key.value,
            status=status,
            press_reserved=press_reserved,
            acknowledged=acknowledged,
            post_ack_frame_observed=observed,
            connection_changed=connection_changed,
            context_changed=context_changed,
            post_ack_mode=post_mode,
            post_ack_screen=post_screen,
            elapsed_seconds=monotonic() - started,
            failure=failure,
        )
