"""Internal renderer-neutral supplemental values, not a public frame extension.

No I/O, scheduling, wall clock, extrapolation, locale formatting, LCD decade or
glyph inference. The owner must take a fresh coherent snapshot under its feed
lock and pass the current session/PSI sequence; a retained view is not live data.
Ordinary startup and WebUI/TUI/HA renderers do not consume this candidate yet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .daemon_quick_keys import (
    STALE_AFTER,
    QuickKeyBank,
    QuickKeySession,
    QuickKeySnapshot,
    SupplementalSnapshot,
)
from .models import FavoritesQuickKeyState
from .scanner_clock import STALE_AFTER as CLOCK_STALE_AFTER
from .scanner_clock import ClockSnapshot


class SupplementalValueStatus(StrEnum):
    CURRENT = "current"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    STALE = "stale"
    BLOCKED = "blocked"
    INVALID_RTC = "invalid_rtc"
    INVALID_SOURCE = "invalid_source"


@dataclass(frozen=True, slots=True)
class DisplayClockValue:
    status: SupplementalValueStatus
    local_time: datetime | None = None
    age_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class DisplayFavoritesValue:
    status: SupplementalValueStatus
    # Index is the global Favorites quick key (0..99), never an LCD position.
    states: tuple[FavoritesQuickKeyState, ...] | None = None
    age_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class SupplementalDisplayValues:
    clock: DisplayClockValue
    favorites: DisplayFavoritesValue


def _age(value: float | None, elapsed: float) -> float | None:
    if (
        value is None
        or type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1e15
    ):
        return None
    return value + elapsed


def _clock_value(sample: ClockSnapshot | None, elapsed: float) -> DisplayClockValue:
    status = SupplementalValueStatus
    if sample is None:
        return DisplayClockValue(status.DISABLED)
    if type(sample) is not ClockSnapshot:
        return DisplayClockValue(status.INVALID_SOURCE)
    if sample.blocked_until_reconnect is not None:
        return DisplayClockValue(status.BLOCKED)
    if sample.failure is not None or sample.age_seconds is None:
        return DisplayClockValue(status.UNAVAILABLE)
    age = _age(sample.age_seconds, elapsed)
    if age is None:
        return DisplayClockValue(status.INVALID_SOURCE)
    if age >= CLOCK_STALE_AFTER:
        return DisplayClockValue(status.STALE)
    if sample.rtc_valid is False and sample.local_time is None:
        return DisplayClockValue(status.INVALID_RTC, age_seconds=age)
    value = sample.local_time
    if (
        sample.rtc_valid is not True
        or type(value) is not datetime
        or value.tzinfo is not None
        or value.microsecond != 0
        or value.fold != 0
    ):
        return DisplayClockValue(status.INVALID_SOURCE)
    # DST's opaque token must not become a UTC offset or a local timezone guess.
    return DisplayClockValue(status.CURRENT, value, age)


def _favorites_value(bank: QuickKeyBank | None, elapsed: float) -> DisplayFavoritesValue:
    status = SupplementalValueStatus
    if bank is None or bank.failure is not None:
        return DisplayFavoritesValue(status.UNAVAILABLE)
    age = _age(bank.age_seconds, elapsed)
    if bank.age_seconds is not None and age is None:
        return DisplayFavoritesValue(status.INVALID_SOURCE)
    if age is not None and age >= STALE_AFTER:
        return DisplayFavoritesValue(status.STALE)
    if bank.states is None:
        return DisplayFavoritesValue(status.UNAVAILABLE)
    if (
        age is None
        or type(bank.states) is not tuple
        or len(bank.states) != 100
        or any(type(value) is not FavoritesQuickKeyState for value in bank.states)
    ):
        return DisplayFavoritesValue(status.INVALID_SOURCE)
    return DisplayFavoritesValue(status.CURRENT, bank.states, age)


def project_supplemental_display_values(
    snapshot: SupplementalSnapshot,
    *,
    session: QuickKeySession,
    sequence: int,
    now: float,
) -> SupplementalDisplayValues:
    """Join an internal fresh owner snapshot; no authority to poll or render it.

    Session identity (not equality/address) and exact current PSI sequence must
    match. The caller is responsible for the current accepted-profile barrier.
    Independent ages increase during this projection, never the scanner time.
    """
    if (
        type(snapshot) is not SupplementalSnapshot
        or type(session) is not QuickKeySession
        or type(sequence) is not int
        or not 0 <= sequence < 2**63
        or type(now) not in (int, float)
        or not math.isfinite(now)
        or not 0 <= now <= 1e15
        or type(snapshot.captured_at) not in (int, float)
        or not math.isfinite(snapshot.captured_at)
        or not 0 <= snapshot.captured_at <= now
        or type(snapshot.quick_keys) is not QuickKeySnapshot
    ):
        raise ValueError("A current owner context and bounded monotonic cutoff are required.")
    status = SupplementalValueStatus
    keys = snapshot.quick_keys
    unavailable = SupplementalDisplayValues(
        DisplayClockValue(status.UNAVAILABLE), DisplayFavoritesValue(status.UNAVAILABLE)
    )
    if (
        snapshot.session is not session
        or type(snapshot.sequence) is not int
        or snapshot.sequence != sequence
        or keys.active is not True
        or keys.selection is None
    ):
        return unavailable
    if keys.blocked_until_reconnect is not None:
        return SupplementalDisplayValues(
            DisplayClockValue(status.BLOCKED), DisplayFavoritesValue(status.BLOCKED)
        )
    elapsed = now - snapshot.captured_at
    if type(keys.banks) is not tuple or any(type(bank) is not QuickKeyBank for bank in keys.banks):
        return SupplementalDisplayValues(
            _clock_value(snapshot.clock, elapsed), DisplayFavoritesValue(status.INVALID_SOURCE)
        )
    banks = tuple(bank for bank in keys.banks if bank.kind == "favorites")
    favorites = (
        _favorites_value(banks[0] if banks else None, elapsed)
        if len(banks) <= 1
        else DisplayFavoritesValue(status.INVALID_SOURCE)
    )
    return SupplementalDisplayValues(_clock_value(snapshot.clock, elapsed), favorites)
