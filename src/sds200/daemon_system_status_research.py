"""Internal, opt-in, one-attempt System Status command qualification.

The runtime holds its normal control/lifecycle scope before calling this helper.
There is deliberately no CLI/config/API registration, polling, remote stop,
automatic retry, or rendering. Post-ACK PSI is evidence of an observed screen,
not a scanner-provided transaction ID tying that frame to the requested site.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Protocol

from .commands import StartSystemStatusAnalysis
from .exceptions import CommandTimeoutError
from .models import ScannerInfo
from .system_status_research import SystemStatusProbeRefused, SystemStatusStartGuard

SYSTEM_STATUS_RESEARCH_MAX_TIMEOUT = 8.0


@dataclass(frozen=True, slots=True)
class SystemStatusResearchPolicy:
    """Explicit firmware pin for the SDS200/direct-UDP research boundary."""

    expected_firmware: str

    def __post_init__(self) -> None:
        value = self.expected_firmware
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 64
            or not value.isascii()
            or not value.isprintable()
            or value != value.strip()
        ):
            raise ValueError("System Status research requires an exact firmware pin.")


class SystemStatusResearchStatus(StrEnum):
    NOT_STARTED = "not_started"
    START_UNCONFIRMED = "start_unconfirmed"
    ACKNOWLEDGED_ONLY = "acknowledged_only"
    ANALYSIS_OBSERVED = "analysis_observed"
    CONNECTION_CHANGED = "connection_changed"


@dataclass(frozen=True, slots=True)
class SystemStatusResearchResult:
    status: SystemStatusResearchStatus
    start_reserved: bool
    acknowledged: bool
    analysis_observed: bool
    connection_changed: bool
    elapsed_seconds: float
    failure: str | None = None


class _IdleReservation(Protocol):
    def reserve_idle_for_research(self) -> AbstractContextManager[None]: ...


class _ResearchScanner(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def waterfall_session(self) -> _IdleReservation: ...

    def _system_status_research_scope(self, *, timeout: float) -> AbstractContextManager[None]: ...

    def get_model(self, *, timeout: float) -> str: ...

    def get_firmware(self, *, timeout: float) -> str: ...

    def get_scanner_info(self, *, timeout: float) -> ScannerInfo: ...

    def execute(self, command: StartSystemStatusAnalysis, *, timeout: float) -> None: ...

    def on_psi(self, callback: Callable[[ScannerInfo], None]) -> Callable[[], None]: ...

    def on_connection(self, callback: Callable[[bool], None]) -> Callable[[], None]: ...


def research_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < value <= SYSTEM_STATUS_RESEARCH_MAX_TIMEOUT
        or not isfinite(value)
    ):
        raise ValueError("System Status research timeout must be greater than zero and at most 8s.")
    return float(value)


class SystemStatusResearchAttempt:
    """One invocation per runtime, including preflight failures and lost ACKs.

    Policy construction/daemon startup does not invoke this. The operator must
    already be present, know the physical to-Scan return path, and keep other
    clients from changing scanner mode until manual return is complete. This
    bounded transaction does not own an ongoing analysis lifecycle afterward.
    """

    def __init__(self, policy: SystemStatusResearchPolicy) -> None:
        self._policy = policy
        self._attempted = False
        self._lock = threading.Lock()

    def run(
        self, scanner: _ResearchScanner, *, operator_ready: bool, timeout: float
    ) -> SystemStatusResearchResult:
        budget = research_timeout(timeout)
        if operator_ready is not True:
            raise SystemStatusProbeRefused("System Status research requires a ready operator.")
        with self._lock:
            if self._attempted:
                raise SystemStatusProbeRefused("System Status research was already attempted.")
            self._attempted = True

        started = monotonic()
        deadline = started + budget
        condition = threading.Condition()
        owner_session = object()
        initial_session = owner_session
        last_connected = True
        ack_at: float | None = None
        observed = False
        reserved = False
        acknowledged = False
        failure: str | None = None

        def remaining() -> float:
            value = deadline - monotonic()
            if value <= 0:
                raise CommandTimeoutError("System Status research deadline expired.")
            return value

        def connection(connected: bool) -> None:
            nonlocal owner_session, last_connected
            with condition:
                if connected != last_connected:
                    last_connected = connected
                    owner_session = object()
                condition.notify_all()

        def psi(info: ScannerInfo) -> None:
            nonlocal observed
            received = monotonic()
            with condition:
                if (
                    ack_at is not None
                    and ack_at <= received <= deadline
                    and owner_session is initial_session
                    and info.command == "PSI"
                    and info.screen == "analyze_system_status"
                    and len(info.system_statuses) == 1
                    and not any(
                        info.records_by_tag(tag)
                        for tag in ("PopupScreen", "PlainText", "ReplayDescription", "ReplayMode")
                    )
                ):
                    observed = True
                    condition.notify_all()

        def session() -> object:
            with condition:
                return owner_session

        try:
            with ExitStack() as stack:
                stack.callback(scanner.on_connection(connection))
                stack.callback(scanner.on_psi(psi))
                stack.enter_context(scanner.waterfall_session.reserve_idle_for_research())
                stack.enter_context(scanner._system_status_research_scope(timeout=remaining()))
                if not scanner.connected:
                    raise SystemStatusProbeRefused("System Status research requires a connection.")
                if scanner.get_model(timeout=remaining()) != "SDS200":
                    raise SystemStatusProbeRefused("System Status research model is not qualified.")
                if scanner.get_firmware(timeout=remaining()) != self._policy.expected_firmware:
                    raise SystemStatusProbeRefused("System Status research firmware pin differs.")
                first = scanner.get_scanner_info(timeout=remaining())
                first_at = monotonic()
                if session() is not initial_session:
                    raise SystemStatusProbeRefused("System Status research connection changed.")
                guard = SystemStatusStartGuard(
                    first, owner_session=initial_session, observed_at=first_at, now=first_at
                )
                second = scanner.get_scanner_info(timeout=remaining())
                second_at = monotonic()
                remaining()
                command = guard.claim(
                    second,
                    owner_session=session(),
                    observed_at=second_at,
                    now=second_at,
                    operator_ready=True,
                    scanner_connected=scanner.connected,
                    waterfall_idle=True,  # held idle reservation, not a stale snapshot
                )
                reserved = True
                scanner.execute(command, timeout=remaining())
                acknowledged = True
                with condition:
                    ack_at = monotonic()
                    while not observed and owner_session is initial_session:
                        wait_for = deadline - monotonic()
                        if wait_for <= 0:
                            break
                        condition.wait(wait_for)
        except Exception as error:
            # No raw error text (transport errors may include private details).
            failure = (
                "timeout"
                if isinstance(error, CommandTimeoutError)
                else "refused"
                if isinstance(error, SystemStatusProbeRefused)
                else "operation_failed"
            )

        changed = session() is not initial_session
        if changed:
            status = SystemStatusResearchStatus.CONNECTION_CHANGED
        elif observed and acknowledged:
            status = SystemStatusResearchStatus.ANALYSIS_OBSERVED
        elif acknowledged:
            status = SystemStatusResearchStatus.ACKNOWLEDGED_ONLY
        elif reserved:
            status = SystemStatusResearchStatus.START_UNCONFIRMED
        else:
            status = SystemStatusResearchStatus.NOT_STARTED
        return SystemStatusResearchResult(
            status, reserved, acknowledged, observed, changed, monotonic() - started, failure
        )
