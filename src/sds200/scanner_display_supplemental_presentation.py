"""Point-in-time clock/Favorites presentation from one coherent owner capture.

No acquisition, demand, host clock, timezone conversion or public API change.
The owner must reacquire a capture after any lifecycle/context change. A retained
presentation is a preview, never authority to refresh a live display's lease.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from .daemon_display_frames import DEFAULT_DISPLAY_STALE_SECONDS, SupplementalDisplayFrameSet
from .daemon_quick_keys import STALE_AFTER as FAVORITES_STALE_AFTER
from .models import FavoritesQuickKeyState
from .scanner_clock import STALE_AFTER as CLOCK_STALE_AFTER
from .scanner_display_adapter import (
    DisplayObservationStatus,
    ScannerDisplayFrame,
    ScannerDisplayIndicators,
)
from .scanner_display_frame import project_scanner_display_frame
from .scanner_display_layout import DisplaySlotSelection
from .scanner_display_profile import ScannerDisplayDataFamily
from .scanner_display_supplemental import (
    DisplayClockValue,
    DisplayFavoritesValue,
    SupplementalDisplayValues,
    SupplementalValueStatus,
)
from .scanner_display_values import ScannerDisplayValue, ScannerDisplayValueStatus

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_KEY_LABELS = {
    FavoritesQuickKeyState.NONEXISTENT: "Absent",
    FavoritesQuickKeyState.DISABLED: "Off",
    FavoritesQuickKeyState.ENABLED: "On",
}


@dataclass(frozen=True, slots=True)
class SupplementalPresentation:
    """Internal immutable view; independent ages, never a scanner LCD bank guess."""

    preferred: ScannerDisplayFrame
    simple: ScannerDisplayFrame
    detail: ScannerDisplayFrame
    clock: DisplayClockValue
    favorites: DisplayFavoritesValue
    favorites_rows: tuple[str, ...]


def _bounded(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and type(value) in (int, float)
        and 0 <= value <= 1e15
        and math.isfinite(value)
    )


def _clock(value: DisplayClockValue, elapsed: float) -> DisplayClockValue:
    status = SupplementalValueStatus
    if type(value) is not DisplayClockValue or type(value.status) is not status:
        return DisplayClockValue(status.INVALID_SOURCE)
    if value.status is not status.CURRENT:
        # Never allow a value hidden under an unavailable/invalid status to leak.
        return DisplayClockValue(value.status)
    local = value.local_time
    if (
        not _bounded(value.age_seconds)
        or type(local) is not datetime
        or local.tzinfo is not None
        or local.microsecond != 0
        or local.fold != 0
    ):
        return DisplayClockValue(status.INVALID_SOURCE)
    assert value.age_seconds is not None
    age = value.age_seconds + elapsed
    if age >= CLOCK_STALE_AFTER:
        return DisplayClockValue(status.STALE)
    return DisplayClockValue(status.CURRENT, local, age)


def _favorites(value: DisplayFavoritesValue, elapsed: float) -> DisplayFavoritesValue:
    status = SupplementalValueStatus
    if type(value) is not DisplayFavoritesValue or type(value.status) is not status:
        return DisplayFavoritesValue(status.INVALID_SOURCE)
    if value.status is not status.CURRENT:
        return DisplayFavoritesValue(value.status)
    if (
        not _bounded(value.age_seconds)
        or type(value.states) is not tuple
        or len(value.states) != 100
        or any(type(item) is not FavoritesQuickKeyState for item in value.states)
    ):
        return DisplayFavoritesValue(status.INVALID_SOURCE)
    assert value.age_seconds is not None
    age = value.age_seconds + elapsed
    if age >= FAVORITES_STALE_AFTER:
        return DisplayFavoritesValue(status.STALE)
    return DisplayFavoritesValue(status.CURRENT, value.states, age)


def _frame(
    frame: ScannerDisplayFrame, clock: DisplayClockValue, elapsed: float
) -> ScannerDisplayFrame:
    assert frame.age_seconds is not None and frame.screen is not None
    age = frame.age_seconds + elapsed
    if age >= DEFAULT_DISPLAY_STALE_SECONDS:
        return replace(
            frame,
            status=DisplayObservationStatus.STALE,
            age_seconds=age,
            indicators=ScannerDisplayIndicators(),
            values=tuple(
                replace(
                    value,
                    status=(
                        value.status
                        if value.status
                        in {
                            ScannerDisplayValueStatus.EMPTY,
                            ScannerDisplayValueStatus.BLANK,
                            ScannerDisplayValueStatus.CONFIGURATION_UNAVAILABLE,
                        }
                        else ScannerDisplayValueStatus.NOT_CURRENT
                    ),
                    text=None,
                    source_fields=(),
                )
                for value in frame.values
            ),
        )
    slots = {slot.region.id: slot for slot in frame.screen.regions}
    values = []
    for value in frame.values:
        slot = slots[value.region_id]
        # Only valid profile-selected small Day/Time slots. No hard-coded cell,
        # new placement, replacing user-selected data or resurrecting Empty.
        if (
            slot.selection is DisplaySlotSelection.CONFIGURED
            and slot.region.option is not None
            and slot.region.option.group == 3
            and slot.token in {"Day", "Time"}
            and value.status
            in {
                ScannerDisplayValueStatus.UNQUALIFIED,
                ScannerDisplayValueStatus.DATA_UNAVAILABLE,
                ScannerDisplayValueStatus.RAW_SOURCE,
            }
        ):
            local = clock.local_time
            text = None
            state = ScannerDisplayValueStatus.DATA_UNAVAILABLE
            if clock.status is SupplementalValueStatus.CURRENT and local is not None:
                text = (
                    f"{_MONTHS[local.month - 1]}{local.day:02}"
                    if slot.token == "Day"
                    else f"{local.hour:02}:{local.minute:02}"
                )
                state = ScannerDisplayValueStatus.RAW_SOURCE
            value = ScannerDisplayValue(value.region_id, state, text, ("DTM",))
        values.append(value)
    return replace(frame, age_seconds=age, values=tuple(values))


def present_supplemental_capture(
    capture: SupplementalDisplayFrameSet, *, now: float
) -> SupplementalPresentation:
    """Present one captured owner cut, aging each source without advancing RTC.

    Only the already-qualified scan families can carry this candidate. Reject
    mixed layout/profile/sequence inputs. This does not make an old capture
    valid after reconnect, profile replacement, mode change or owner shutdown.
    Live owners must obtain a fresh capture, not call this on a retained one.
    """
    if (
        type(capture) is not SupplementalDisplayFrameSet
        or not _bounded(now)
        or not _bounded(capture.captured_at)
        or now < capture.captured_at
        or type(capture.sequence) is not int
        or not 0 <= capture.sequence < 2**53
        or type(capture.profile_invalidation) is not int
        or capture.profile_invalidation < 0
        or type(capture.supplemental) is not SupplementalDisplayValues
    ):
        raise ValueError("A coherent supplemental capture and monotonic cutoff are required.")
    try:
        for identifier in (capture.endpoint_id, capture.stream_id, capture.session_id):
            if type(identifier) is not str or str(UUID(identifier)) != identifier:
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A coherent supplemental capture identity is required.") from None
    frames = (capture.preferred, capture.simple, capture.detail)
    reference = capture.preferred
    for frame in frames:
        if (
            type(frame) is not ScannerDisplayFrame
            or frame.status is not DisplayObservationStatus.CURRENT
            or type(frame.sequence) is not int
            or frame.sequence != capture.sequence
            or not _bounded(frame.age_seconds)
            or frame.screen is None
            or frame.provenance is None
            or str(frame.provenance.binding.endpoint_id) != capture.endpoint_id
            or frame.screen.layout.requested_mode.data_family
            not in {
                ScannerDisplayDataFamily.CONVENTIONAL,
                ScannerDisplayDataFamily.TRUNK,
            }
            or any(
                getattr(frame, key) != getattr(reference, key)
                for key in (
                    "profile_revision",
                    "profile_status",
                    "profile_refresh_pending",
                    "provenance",
                    "sequence",
                    "age_seconds",
                    "indicators",
                )
            )
        ):
            raise ValueError("Supplemental layouts must share one current scanner context.")
        project_scanner_display_frame(frame)  # Existing canonical geometry/value checks.
    assert reference.screen is not None
    assert capture.simple.screen is not None and capture.detail.screen is not None
    family = reference.screen.layout.requested_mode.data_family
    if (
        any(
            frame.screen is None or frame.screen.layout.requested_mode.data_family is not family
            for frame in frames
        )
        or not capture.simple.screen.layout.requested_mode.value.startswith("simple_")
        or not capture.detail.screen.layout.requested_mode.value.startswith("detail_")
    ):
        raise ValueError("Supplemental layouts must share one scanner family.")
    elapsed = now - capture.captured_at
    clock = _clock(capture.supplemental.clock, elapsed)
    favorites = _favorites(capture.supplemental.favorites, elapsed)
    assert reference.age_seconds is not None
    if reference.age_seconds + elapsed >= DEFAULT_DISPLAY_STALE_SECONDS:
        clock = DisplayClockValue(SupplementalValueStatus.STALE)
        favorites = DisplayFavoritesValue(SupplementalValueStatus.STALE)
    rows = (
        ()
        if favorites.states is None
        else tuple(
            "  ".join(
                f"{key:02}:{_KEY_LABELS[favorites.states[key]]}" for key in range(start, start + 10)
            )
            for start in range(0, 100, 10)
        )
    )
    return SupplementalPresentation(
        preferred=_frame(capture.preferred, clock, elapsed),
        simple=_frame(capture.simple, clock, elapsed),
        detail=_frame(capture.detail, clock, elapsed),
        clock=clock,
        favorites=favorites,
        favorites_rows=rows,
    )
