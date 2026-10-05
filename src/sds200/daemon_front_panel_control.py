"""Exact, opt-in production boundary for the qualified SDS200 Menu press.

This module intentionally does not generalize the 27-key inventory.  Physical
evidence currently qualifies only one ``M`` press on one firmware from one
fresh visible context.  Every invocation rechecks that complete boundary before
emitting exactly one packet; there is no retry, sequence, held gesture, context
inference, or acknowledgement-to-state claim.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from .commands import PressFrontPanelKey
from .exceptions import DaemonControlUnavailableError
from .front_panel_keys import FrontPanelKey
from .models import ScannerInfo

QUALIFIED_MENU_MODEL = "SDS200"
QUALIFIED_MENU_FIRMWARE = "Version 1.26.01"
QUALIFIED_MENU_MODE = "Trunk Scan"
QUALIFIED_MENU_SCREEN = "trunk_scan"
_SAFE_CONTEXT = re.compile(r"[A-Za-z0-9._ /-]{1,64}")
_DISALLOWED_CONTEXT_TAGS = frozenset(
    {"PopupScreen", "PlainText", "ReplayDescription", "ReplayMode"}
)


@dataclass(frozen=True, slots=True)
class QualifiedMenuControlPolicy:
    """Explicit opt-in token pinned to the one physically qualified boundary."""

    model: str = QUALIFIED_MENU_MODEL
    firmware: str = QUALIFIED_MENU_FIRMWARE
    mode: str = QUALIFIED_MENU_MODE
    screen: str = QUALIFIED_MENU_SCREEN
    key: FrontPanelKey = FrontPanelKey.MENU

    def __post_init__(self) -> None:
        if (
            self.model != QUALIFIED_MENU_MODEL
            or self.firmware != QUALIFIED_MENU_FIRMWARE
            or self.mode != QUALIFIED_MENU_MODE
            or self.screen != QUALIFIED_MENU_SCREEN
            or self.key is not FrontPanelKey.MENU
        ):
            raise ValueError(
                "Menu control policy must match the physically qualified boundary."
            )


class _IdleReservation(Protocol):
    def reserve_idle_for_control(self) -> AbstractContextManager[None]: ...


class QualifiedMenuScanner(Protocol):
    @property
    def connected(self) -> bool: ...

    @property
    def waterfall_session(self) -> _IdleReservation: ...

    def _front_panel_control_scope(
        self, *, timeout: float
    ) -> AbstractContextManager[None]: ...

    def get_model(self, *, timeout: float) -> str: ...

    def get_firmware(self, *, timeout: float) -> str: ...

    def get_scanner_info(self, *, timeout: float) -> ScannerInfo: ...

    def execute(self, command: PressFrontPanelKey, *, timeout: float) -> None: ...


def _safe_context(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or _SAFE_CONTEXT.fullmatch(value) is None
    ):
        return None
    return value


def _matches_policy(
    info: ScannerInfo,
    policy: QualifiedMenuControlPolicy,
) -> bool:
    return (
        isinstance(info, ScannerInfo)
        and info.command == "GSI"
        and _safe_context(info.mode) == policy.mode
        and _safe_context(info.screen) == policy.screen
        and not any(info.records_by_tag(tag) for tag in _DISALLOWED_CONTEXT_TAGS)
    )


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    value = deadline - clock()
    if value <= 0:
        raise DaemonControlUnavailableError(
            "Qualified Menu control expired before its preflight completed."
        )
    return value


def execute_qualified_menu_press(
    scanner: QualifiedMenuScanner,
    policy: QualifiedMenuControlPolicy,
    *,
    timeout: float,
    clock: Callable[[], float],
) -> None:
    """Verify two fresh exact preflights and emit one qualified ``M`` press."""

    if not isinstance(policy, QualifiedMenuControlPolicy):
        raise TypeError("Qualified Menu control requires an explicit policy.")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("Qualified Menu control timeout must be finite and positive.")
    if not callable(clock):
        raise TypeError("Qualified Menu control requires a monotonic clock.")

    deadline = clock() + float(timeout)
    try:
        with ExitStack() as stack:
            stack.enter_context(scanner.waterfall_session.reserve_idle_for_control())
            stack.enter_context(
                scanner._front_panel_control_scope(
                    timeout=_remaining(deadline, clock)
                )
            )
            if not scanner.connected:
                raise DaemonControlUnavailableError(
                    "Qualified Menu control requires an existing connection."
                )
            if scanner.get_model(timeout=_remaining(deadline, clock)) != policy.model:
                raise DaemonControlUnavailableError(
                    "Qualified Menu control model does not match."
                )
            if (
                scanner.get_firmware(timeout=_remaining(deadline, clock))
                != policy.firmware
            ):
                raise DaemonControlUnavailableError(
                    "Qualified Menu control firmware does not match."
                )
            for _ in range(2):
                frame = scanner.get_scanner_info(
                    timeout=_remaining(deadline, clock)
                )
                if not scanner.connected or not _matches_policy(frame, policy):
                    raise DaemonControlUnavailableError(
                        "Qualified Menu control context does not match."
                    )
            scanner.execute(
                PressFrontPanelKey(FrontPanelKey.MENU),
                timeout=_remaining(deadline, clock),
            )
    except DaemonControlUnavailableError:
        raise
    except (RuntimeError, ValueError) as error:
        raise DaemonControlUnavailableError(
            "Qualified Menu control is unavailable."
        ) from error


__all__ = [
    "QUALIFIED_MENU_FIRMWARE",
    "QUALIFIED_MENU_MODE",
    "QUALIFIED_MENU_MODEL",
    "QUALIFIED_MENU_SCREEN",
    "QualifiedMenuControlPolicy",
    "QualifiedMenuScanner",
    "execute_qualified_menu_press",
]
