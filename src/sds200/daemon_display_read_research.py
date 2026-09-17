"""Explicit one-shot DTM / quick-key GET qualification on the existing owner.

No default startup hook, polling, remote operation, retries, rendering or SETs.
The runtime holds its control/lifecycle scope; this helper also excludes active
waterfall and other command-lane users. A successful reply and continued PSI are
separate evidence. Neither proves physical display parity or LCD bank selection.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Protocol, TypeVar

from .commands import (
    Command,
    GetDateTime,
    GetDepartmentQuickKeys,
    GetFavoritesQuickKeys,
    GetSystemQuickKeys,
)
from .exceptions import CommandRejectedError, CommandTimeoutError, ProtocolError
from .models import (
    DepartmentQuickKeys,
    FavoritesQuickKeys,
    Packet,
    ScannerDateTime,
    ScannerInfo,
    SystemQuickKeys,
)
from .scanner_quick_keys import quick_key_selection

READ_BUDGET = 0.25
MAX_TIMEOUT = 8.0
MAX_PSI_AGE = 1.5
T = TypeVar("T")


class DisplayReadKind(StrEnum):
    CLOCK = "clock"
    FAVORITES = "favorites"
    SYSTEM = "system"
    DEPARTMENT = "department"


class DisplayReadRefused(ValueError):
    """Fixed-text refusal without scanner or transport values."""


@dataclass(frozen=True, slots=True)
class DisplayReadResearchPolicy:
    expected_firmware: str
    kind: DisplayReadKind

    def __post_init__(self) -> None:
        if (
            not isinstance(self.expected_firmware, str)
            or re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", self.expected_firmware) is None
            or self.expected_firmware != self.expected_firmware.strip()
            or type(self.kind) is not DisplayReadKind
        ):
            raise ValueError("Display-read research requires an exact firmware pin and GET kind.")


@dataclass(frozen=True, slots=True)
class DisplayReadResearchResult:
    kind: str
    status: str
    read_reserved: bool
    response_validated: bool
    normal_psi_after_response: int
    connection_changed: bool
    scan_context_changed: bool
    elapsed_seconds: float
    failure: str | None
    response_shape: dict[str, object] | None
    sample: dict[str, object] | None


class _IdleReservation(Protocol):
    def reserve_idle_for_research(self) -> AbstractContextManager[None]: ...


class _DisplayResearchScanner(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def waterfall_session(self) -> _IdleReservation: ...

    def _display_read_research_scope(self, *, timeout: float) -> AbstractContextManager[None]: ...

    def get_model(self, *, timeout: float) -> str: ...

    def get_firmware(self, *, timeout: float) -> str: ...

    def execute(self, command: Command[T], *, timeout: float) -> T: ...

    def on_psi(self, callback: Callable[[ScannerInfo], None]) -> Callable[[], None]: ...

    def on_connection(self, callback: Callable[[bool], None]) -> Callable[[], None]: ...

    def on_packet(self, callback: Callable[[Packet], None]) -> Callable[[], None]: ...


def display_read_timeout(value: float) -> float:
    if type(value) not in (int, float) or not 0 < value <= MAX_TIMEOUT or not isfinite(value):
        raise ValueError("Display-read research timeout must be greater than zero and at most 8s.")
    return float(value)


def _selection(info: ScannerInfo, kind: DisplayReadKind) -> tuple[int, ...] | None:
    """A narrow qualified normal PSI; no object-index or unassigned-key fallback."""
    if not isinstance(info, ScannerInfo) or info.command != "PSI":
        return None
    modes = {
        "trunk_scan": {"Trunk Scan", "Trunk Scan Hold"},
        "conventional_scan": {"Conventional Scan", "Conventional Scan Hold"},
    }
    if info.screen is None or info.mode not in modes.get(info.screen, set()):
        return None
    counts = Counter(record.tag for record in info.records)
    excluded = {
        "PopupScreen",
        "PlainText",
        "ReplayDescription",
        "ReplayMode",
        "Button",
        "SrchFrequency",
        "CcHitsChannel",
        "WxChannel",
        "ToneOutChannel",
        "SystemStatus",
        "Analyze",
        "RfPowerPlot",
        "ConvFrequency" if info.screen == "trunk_scan" else "TGID",
    }
    if (
        counts["System"] != 1
        or any(n > 1 for n in counts.values())
        or excluded.intersection(counts)
    ):
        return None
    if kind in (DisplayReadKind.CLOCK, DisplayReadKind.FAVORITES):
        return ()  # Global reads do not claim a system, bank decade or channel.
    keys = quick_key_selection(info)
    if keys is None or keys.favorites is None:
        return None
    if kind is DisplayReadKind.SYSTEM:
        return (keys.favorites,)
    if keys.system is None:
        return None
    return (keys.favorites, keys.system)


def _command(kind: DisplayReadKind, selection: tuple[int, ...]) -> Command[object]:
    if kind is DisplayReadKind.CLOCK:
        return GetDateTime()
    if kind is DisplayReadKind.FAVORITES:
        return GetFavoritesQuickKeys()
    if kind is DisplayReadKind.SYSTEM:
        return GetSystemQuickKeys(selection[0])
    return GetDepartmentQuickKeys(selection[0], selection[1])


def _sample(result: object) -> dict[str, object]:
    if isinstance(result, ScannerDateTime):
        return {
            "scanner_local_time": None
            if result.local_time is None
            else result.local_time.isoformat(),
            "rtc_valid": result.rtc_valid,
            "daylight_saving_token": result.daylight_saving,
            "timezone_known": False,
        }
    if isinstance(result, (FavoritesQuickKeys, SystemQuickKeys, DepartmentQuickKeys)):
        report: dict[str, object] = {"states": [int(state) for state in result.states]}
        if isinstance(result, (SystemQuickKeys, DepartmentQuickKeys)):
            report["favorites_quick_key"] = result.favorites_quick_key
        if isinstance(result, SystemQuickKeys):
            report["reported_system_quick_key"] = result.reported_system_quick_key
        if isinstance(result, DepartmentQuickKeys):
            report["system_quick_key"] = result.system_quick_key
        return report
    raise ProtocolError("Display read returned an unexpected model.")


class DisplayReadResearchAttempt:
    """One selected GET attempt per runtime; no reset, follow-on kind or retry."""

    def __init__(self, policy: DisplayReadResearchPolicy) -> None:
        self._policy = policy
        self._attempted = False
        self._lock = threading.Lock()

    def run(
        self, scanner: _DisplayResearchScanner, *, operator_ready: bool, timeout: float
    ) -> DisplayReadResearchResult:
        budget = display_read_timeout(timeout)
        if operator_ready is not True:
            raise DisplayReadRefused("Display-read research requires a ready operator.")
        with self._lock:
            if self._attempted:
                raise DisplayReadRefused("Display-read research was already attempted.")
            self._attempted = True
        started = monotonic()
        deadline = started + budget
        condition = threading.Condition()
        changed = context_changed = reserved = validated = False
        current: tuple[int, ...] | None = None
        selected: tuple[int, ...] | None = None
        last_psi = 0.0
        completed_at: float | None = None
        after_count = 0
        failure: str | None = None
        shape: dict[str, object] | None = None
        sample: dict[str, object] | None = None
        response_code: str | None = None

        def remaining() -> float:
            seconds = deadline - monotonic()
            if seconds <= 0:
                raise CommandTimeoutError("Display-read research deadline expired.")
            return seconds

        def connected(value: bool) -> None:
            nonlocal changed
            with condition:
                # An established owner's disconnect is terminal even if it reconnects.
                if value is not True:
                    changed = True
                condition.notify_all()

        def psi(info: ScannerInfo) -> None:
            nonlocal current, last_psi, context_changed, after_count
            context = _selection(info, self._policy.kind)
            now = monotonic()
            with condition:
                current, last_psi = context, now
                if reserved and context != selected:
                    context_changed = True
                if (
                    completed_at is not None
                    and completed_at <= now <= deadline
                    and context is not None
                ):
                    after_count += 1
                condition.notify_all()

        def packet(value: Packet) -> None:
            nonlocal shape
            with condition:
                if reserved and value.command == response_code and shape is None:
                    # Counts only, including malformed replies. No raw wire text,
                    # names, addresses, arbitrary field values or XML retained.
                    shape = {
                        "field_count": min(len(value.fields), 1000),
                        "state_token_count": sum(
                            field in ("0", "1", "2") for field in value.fields[:1000]
                        ),
                    }

        try:
            with ExitStack() as stack:
                stack.callback(scanner.on_connection(connected))
                stack.callback(scanner.on_psi(psi))
                stack.callback(scanner.on_packet(packet))
                stack.enter_context(scanner.waterfall_session.reserve_idle_for_research())
                stack.enter_context(scanner._display_read_research_scope(timeout=remaining()))
                if not scanner.connected:
                    raise DisplayReadRefused("Display-read research requires a connection.")
                for getter, expected in (
                    (scanner.get_model, "SDS200"),
                    (scanner.get_firmware, self._policy.expected_firmware),
                ):
                    before = monotonic()
                    reply = getter(timeout=min(READ_BUDGET, remaining()))
                    if monotonic() - before >= READ_BUDGET:
                        raise CommandTimeoutError("Display-read identity check timed out.")
                    if changed or reply != expected:
                        raise DisplayReadRefused("Display-read identity or connection differs.")
                if not scanner.connected:
                    raise DisplayReadRefused("Display-read connection changed.")
                with condition:
                    while current is None or monotonic() - last_psi > MAX_PSI_AGE:
                        if changed:
                            raise DisplayReadRefused("Display-read connection changed.")
                        condition.wait(remaining())
                    if changed:
                        raise DisplayReadRefused("Display-read connection changed.")
                    selected = current
                    command = _command(self._policy.kind, selected)
                    response_code = command.response_command
                    reserved = True
                before = monotonic()
                result = scanner.execute(command, timeout=min(READ_BUDGET, remaining()))
                if monotonic() - before >= READ_BUDGET:
                    raise CommandTimeoutError("Display-read reply exceeded its budget.")
                # Reparse the exact source model, then only keep bounded typed data.
                if not isinstance(
                    result,
                    (ScannerDateTime, FavoritesQuickKeys, SystemQuickKeys, DepartmentQuickKeys),
                ):
                    raise ProtocolError("Display-read response model differs.")
                canonical = command.parse_response(result.packet)
                if type(canonical) is not type(result) or canonical != result:
                    raise ProtocolError("Display-read response fields differ.")
                sample = _sample(canonical)
                validated = True
                with condition:
                    completed_at = monotonic()
                    while after_count < 2 and not changed and not context_changed:
                        condition.wait(remaining())
        except Exception as error:
            failure = (
                "timeout"
                if isinstance(error, CommandTimeoutError)
                else "rejected"
                if isinstance(error, CommandRejectedError)
                else "invalid_response"
                if isinstance(error, ProtocolError)
                else "refused"
                if isinstance(error, DisplayReadRefused)
                else "operation_failed"
            )
        if changed:
            status = "connection_changed"
        elif context_changed:
            status = "scan_context_changed"
        elif validated and after_count >= 2 and failure is None:
            status = "reply_and_psi_observed"
        elif validated:
            status = "reply_only"
        elif reserved:
            status = "read_unconfirmed"
        else:
            status = "not_started"
        return DisplayReadResearchResult(
            self._policy.kind.value,
            status,
            reserved,
            validated,
            after_count,
            changed,
            context_changed,
            monotonic() - started,
            failure,
            shape,
            sample if status == "reply_and_psi_observed" else None,
        )
