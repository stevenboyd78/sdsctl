"""Offline pre-send guards for a future existing-owner System Status probe.

No transport, daemon operation, automatic polling, or renderer is installed by
this module. An integration must claim a command inside its existing exclusive
scanner-control scope and invoke the existing exact-acknowledgement command once.
Issuing a command is not evidence that it was sent, acknowledged, or took effect.
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass

from .commands import StartSystemStatusAnalysis
from .models import ScannerInfo

SYSTEM_STATUS_PROBE_MAX_AGE = 2.0
_UNAVAILABLE_INDEX = (1 << 32) - 1
_REJECTED_RECORDS = frozenset(
    {
        "PopupScreen",
        "PlainText",
        "ReplayDescription",
        "ReplayMode",
        "Button",
        "ConvFrequency",
        "SrchFrequency",
        "CcHitsChannel",
        "WxChannel",
        "ToneOutChannel",
        "SystemStatus",
        "Analyze",
        "RfPowerPlot",
    }
)


class SystemStatusProbeRefused(ValueError):
    """Sanitized refusal; no scanner values or profile contents in the message."""


def _index(value: object) -> int:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 10
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise SystemStatusProbeRefused("System Status selection index is unavailable.")
    result = int(value)
    if result >= _UNAVAILABLE_INDEX:
        raise SystemStatusProbeRefused("System Status selection index is unavailable.")
    return result


@dataclass(frozen=True, slots=True)
class SystemStatusTarget:
    """Scanner object indices, never broadcast SystemID/SiteID or quick keys."""

    system_index: int
    site_index: int

    def __post_init__(self) -> None:
        for value in (self.system_index, self.site_index):
            if type(value) is not int or not 0 <= value < _UNAVAILABLE_INDEX:
                raise SystemStatusProbeRefused("System Status selection index is unavailable.")


def system_status_target(info: ScannerInfo) -> SystemStatusTarget:
    """Select from one unambiguous normal trunk-scan observation, not a cache.

    This intentionally narrow preflight does not qualify mixed scan transitions
    merely because a renderer is able to display them. A caller can await a new
    ordinary frame; it must not change scanner settings to manufacture one.
    """
    if (
        info.command not in {"PSI", "GSI"}
        or info.screen != "trunk_scan"
        or info.mode not in {"Trunk Scan", "Trunk Scan Hold"}
    ):
        raise SystemStatusProbeRefused("System Status start requires a normal trunk screen.")
    counts = Counter(record.tag for record in info.records)
    if (
        counts["System"] != 1
        or counts["Site"] != 1
        or any(count > 1 for count in counts.values())
        or _REJECTED_RECORDS.intersection(counts)
    ):
        raise SystemStatusProbeRefused("System Status selection is ambiguous or obscured.")
    # Do not use the convenience nodes mapping: it can hide duplicate records.
    system = info.records_by_tag("System")[0]
    site = info.records_by_tag("Site")[0]
    return SystemStatusTarget(_index(system.get("Index")), _index(site.get("Index")))


def _fresh(observed_at: float, now: float) -> None:
    try:
        valid = (
            all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                and value >= 0
                for value in (observed_at, now)
            )
            and 0 <= now - observed_at <= SYSTEM_STATUS_PROBE_MAX_AGE
        )
    except OverflowError:
        valid = False
    if not valid:
        raise SystemStatusProbeRefused("System Status selection is not fresh.")


class SystemStatusStartGuard:
    """One command reservation bound to one owner session and selected site.

    The owner supplies monotonic receipt times (not ScannerInfo wall time) and
    a new identity object for each connection session. It must hold its own
    control/lifecycle lock from the final observation check through dispatch.
    This internal latch prevents duplicate claims, not arbitrary transport sends.
    A lost acknowledgement never makes this object reusable. There is no reset.
    """

    def __init__(
        self,
        info: ScannerInfo,
        *,
        owner_session: object,
        observed_at: float,
        now: float,
    ) -> None:
        if owner_session is None:
            raise SystemStatusProbeRefused("System Status owner session is unavailable.")
        _fresh(observed_at, now)
        self._target = system_status_target(info)
        self._owner_session = owner_session
        self._prepared_at = now
        self._claimed = False
        self._lock = threading.Lock()

    @property
    def target(self) -> SystemStatusTarget:
        return self._target

    def claim(
        self,
        info: ScannerInfo,
        *,
        owner_session: object,
        observed_at: float,
        now: float,
        operator_ready: bool,
        scanner_connected: bool,
        waterfall_idle: bool,
    ) -> StartSystemStatusAnalysis:
        """Reserve one typed start; does not send or infer analysis state.

        Readiness means an operator is present with a confirmed physical return
        path. Unknown connection/waterfall state is not treated as safe. The
        preparation itself expires after two seconds so approvals cannot linger.
        A future owner bridge must coordinate preparation and claim in one
        immediate request, never across a human-response delay.
        """
        with self._lock:
            if self._claimed:
                raise SystemStatusProbeRefused("System Status start was already reserved.")
            if owner_session is not self._owner_session:
                raise SystemStatusProbeRefused("System Status owner session has changed.")
            if any(
                value is not True
                for value in (
                    operator_ready,
                    scanner_connected,
                    waterfall_idle,
                )
            ):
                raise SystemStatusProbeRefused("System Status start is not ready.")
            _fresh(observed_at, now)
            _fresh(self._prepared_at, now)
            if observed_at < self._prepared_at or system_status_target(info) != self.target:
                raise SystemStatusProbeRefused(
                    "System Status selection changed or was not rechecked."
                )
            command = StartSystemStatusAnalysis(self.target.site_index)
            self._claimed = True
            return command
