"""Internal Mimic-SDS adapter for complete observations from one scanner owner.

This module opens no connection, subscribes to no events and exposes no route.
An eventual owner hook supplies endpoint selection, a session ticket, ordered
PSI/GSI and monotonic receipt times. Only a bounded display projection is kept,
never raw XML. Specification mappings are not physical LCD acceptance.
"""

from __future__ import annotations

import math
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .models import ScannerInfo
from .scanner_display_layout import ScannerDisplayScreen, resolve_scanner_display_screen
from .scanner_display_live import ScannerDisplayLiveValues, project_live_values
from .scanner_display_profile import ScannerDisplayDataFamily, ScannerDisplayMode
from .scanner_display_profile_state import (
    DisplayProfileProvenance,
    DisplayProfileSnapshot,
    DisplayProfileStatus,
)
from .scanner_display_values import (
    MAX_DISPLAY_VALUE_LENGTH,
    ScannerDisplayValue,
    scanner_display_values,
)
from .scanner_quick_keys import QuickKeySelection, quick_key_selection
from .state import RadioStateSnapshot, snapshot_from_scanner_info


class ScannerDisplayAdapterError(ValueError):
    """Sanitized refusal of foreign, stale-session or invalid owner input."""


class DisplayObservationStatus(StrEnum):
    DISCONNECTED = "disconnected"
    WAITING = "waiting"
    STALE = "stale"
    CURRENT = "current"
    UNSUPPORTED_SCREEN = "unsupported_screen"
    OVERRIDE = "override"
    AMBIGUOUS_RECORDS = "ambiguous_records"


class DisplayConflictReason(StrEnum):
    FOREIGN_CHANNEL = "foreign_channel"
    MODE_SCREEN_MISMATCH = "mode_screen_mismatch"
    DUPLICATE_RECORDS = "duplicate_records"
    WEATHER_FREQUENCY_SOURCES = "weather_frequency_sources"


@dataclass(frozen=True, slots=True)
class ScannerDisplayConflict:
    """Internal diagnostic: allowlisted structure only, never scanner values."""

    screen: str
    operating_mode: str | None
    reasons: tuple[DisplayConflictReason, ...]
    duplicate_tags: tuple[str, ...]
    foreign_channel_tags: tuple[str, ...]


class DisplayLayoutBasis(StrEnum):
    UNAVAILABLE = "unavailable"
    DOCUMENTED_SCREEN = "documented_screen"
    PROFILE_PREFERENCE_UNCONFIRMED = "profile_preference_unconfirmed"
    EXPLICIT_PRESENTATION_CHOICE = "explicit_presentation_choice"


class ScannerDisplayStyle(StrEnum):
    """Local presentation only, never a keypress or a scanner synchronization claim."""

    SIMPLE = "simple"
    DETAIL = "detail"


class ScannerAlertLed(StrEnum):
    """Exact Property.A_Led values, V1.02 page 18. No blink-rate semantics."""

    OFF = "Off"
    BLUE = "Blue"
    RED = "Red"
    MAGENTA = "Magenta"
    GREEN = "Green"
    CYAN = "Cyan"
    YELLOW = "Yellow"
    WHITE = "White"


@dataclass(frozen=True, slots=True)
class ScannerDisplayIndicators:
    """Only confirmed observation values; None is unknown, not Off/released."""

    alert_led: ScannerAlertLed | None = None
    system_hold: bool | None = None
    department_hold: bool | None = None
    channel_hold: bool | None = None
    site_hold: bool | None = None


@dataclass(frozen=True, slots=True, eq=False)
class DisplayObservationSession:
    """Identity ticket issued by the adapter; a copied ticket is not valid."""

    endpoint_id: UUID
    started_at: float


@dataclass(frozen=True, slots=True)
class ScannerDisplayFrame:
    """Immutable internal frame. CURRENT is data age, not profile freshness.

    Renderers must present status and layout_basis independently of the profile
    status. No source values accompany stale or unknown operating data. A
    profile/default or explicit Simple/Detail choice is presentation, not a
    claim that the physical scanner is using that layout.
    Missing configuration is not a reason to invent a default scanner profile.
    """

    status: DisplayObservationStatus
    layout_basis: DisplayLayoutBasis
    profile_status: DisplayProfileStatus
    profile_refresh_pending: bool
    provenance: DisplayProfileProvenance | None
    profile_revision: str | None
    sequence: int | None
    age_seconds: float | None
    screen: ScannerDisplayScreen | None
    values: tuple[ScannerDisplayValue, ...]
    indicators: ScannerDisplayIndicators = ScannerDisplayIndicators()


@dataclass(frozen=True, slots=True)
class _Observation:
    sequence: int
    received_at: float
    status: DisplayObservationStatus
    screen_id: str | None
    snapshot: RadioStateSnapshot
    indicators: ScannerDisplayIndicators = ScannerDisplayIndicators()
    live_values: ScannerDisplayLiveValues = ScannerDisplayLiveValues()
    quick_keys: QuickKeySelection | None = None
    conflict: ScannerDisplayConflict | None = None


# Remote Command Specification V1.02 pp.17-18 defines V_Screen, not a
# Simple/Detail selector. Restrict the first projection to these exact IDs.
_SPECIAL_MODES = {
    "custom_search": ScannerDisplayMode.SEARCH_CLOSE_CALL,
    "quick_search": ScannerDisplayMode.SEARCH_CLOSE_CALL,
    "close_call": ScannerDisplayMode.SEARCH_CLOSE_CALL,
    "cc_searching": ScannerDisplayMode.SEARCH_CLOSE_CALL,
    "wx_alert": ScannerDisplayMode.WEATHER,
    "tone_out": ScannerDisplayMode.TONE_OUT,
}
_DATA_FAMILIES = {
    "conventional_scan": ScannerDisplayDataFamily.CONVENTIONAL,
    "trunk_scan": ScannerDisplayDataFamily.TRUNK,
    **{screen: mode.data_family for screen, mode in _SPECIAL_MODES.items()},
}
# Only documented, unambiguous operating-mode labels. Unknown Mode text does
# not override an exact V_Screen; a known contradictory pair is refused.
_OPERATING_FAMILIES = {
    "Scan Mode": ScannerDisplayDataFamily.CONVENTIONAL,
    "Scan Hold": ScannerDisplayDataFamily.CONVENTIONAL,
    "Trunk Scan": ScannerDisplayDataFamily.TRUNK,
    "Trunk Scan Hold": ScannerDisplayDataFamily.TRUNK,
    "Custom Search": ScannerDisplayDataFamily.SEARCH_CLOSE_CALL,
    "Custom Search Hold": ScannerDisplayDataFamily.SEARCH_CLOSE_CALL,
    "Quick Search": ScannerDisplayDataFamily.SEARCH_CLOSE_CALL,
    "Quick Search Hold": ScannerDisplayDataFamily.SEARCH_CLOSE_CALL,
    "Close Call Only": ScannerDisplayDataFamily.SEARCH_CLOSE_CALL,
    "Close Call": ScannerDisplayDataFamily.SEARCH_CLOSE_CALL,
    "Tone-Out": ScannerDisplayDataFamily.TONE_OUT,
}
# Observed on SDS200 ordinary scanning: these two root labels can lag V_Screen
# across a conventional/trunk transition. Use the exact V_Screen only when its
# matching channel record is present. Foreign/duplicate records still refuse
# the observation. This does not qualify held, search or other mode mismatches.
_QUALIFIED_SCAN_TRANSITIONS = {
    ("conventional_scan", "Trunk Scan"): "ConvFrequency",
    ("trunk_scan", "Scan Mode"): "TGID",
}
# Observed during ordinary SDS200 scanning with CC DND: complete scanner replies
# retained this trunk-screen structure while Mode briefly said Close Call, both
# with and without the optional OverWrite channel message. Require the core
# structure and exact CC policy, not just a TGID or V_Screen. Dedicated Close
# Call screens and all other mismatches stay separate.
_CC_DND_TRUNK_RECORDS = frozenset(
    {"System", "Department", "Site", "SiteFrequency", "TGID", "Property", "DualWatch"}
)
_CHANNEL_TAGS = frozenset(
    {"ConvFrequency", "TGID", "SrchFrequency", "CcHitsChannel", "WxChannel", "ToneOutChannel"}
)
_ALLOWED_TAGS = {
    "conventional_scan": frozenset(
        {
            "MonitorList",
            "System",
            "Department",
            "ConvFrequency",
            "Property",
            "DualWatch",
            "UnitID",
            "InfoArea1",
            "InfoArea2",
            "OverWrite",
        }
    ),
    "trunk_scan": frozenset(
        {
            "MonitorList",
            "System",
            "Department",
            "Site",
            "SiteFrequency",
            "TGID",
            "Property",
            "DualWatch",
            "UnitID",
            "InfoArea1",
            "InfoArea2",
            "OverWrite",
        }
    ),
    "custom_search": frozenset(
        {"SrchFrequency", "Property", "DualWatch", "InfoArea1", "InfoArea2"}
    ),
    "quick_search": frozenset({"SrchFrequency", "Property", "DualWatch", "InfoArea1", "InfoArea2"}),
    "close_call": frozenset({"SrchFrequency", "Property", "DualWatch", "InfoArea1", "InfoArea2"}),
    "cc_searching": frozenset({"Property", "DualWatch", "InfoArea1", "InfoArea2"}),
    "wx_alert": frozenset({"WxChannel", "SrchFrequency", "WxMode", "Property"}),
    "tone_out": frozenset({"ToneOutChannel", "Property"}),
}
# ViewDescription itself also carries ordinary InfoArea entries, so it does
# not imply an overlay. OverWrite replaces only the scan channel name (p.23),
# whereas these child tags and replay markers obscure the screen (pp.23-24).
_OVERRIDE_TAGS = frozenset(
    {"PopupScreen", "PlainText", "ReplayDescription", "ReplayMode", "Button"}
)


def _time(value: float) -> float:
    # Bound before float conversion, including arbitrarily large Python ints.
    if type(value) not in (int, float) or not 0 <= value <= 1e15 or not math.isfinite(value):
        raise ScannerDisplayAdapterError("A finite nonnegative monotonic time is required.")
    return float(value)


def _bounded_text(value: str | None) -> str | None:
    if value is not None and (
        len(value) > MAX_DISPLAY_VALUE_LENGTH
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        # Fixed invalid sentinel, not raw input. The value projector reports
        # INVALID_SOURCE and never emits this sentinel as a displayed value.
        return "\x00"
    return value


def _bounded_level(value: int | None) -> int | None:
    return value if value is None or -(2**31) <= value < 2**31 else 2**31


def _bounded_rssi(value: float | None) -> float | None:
    return value if value is None or (math.isfinite(value) and abs(value) <= 1e9) else 1e10


def _cc_dnd_trunk_transition(info: ScannerInfo, counts: Counter[str]) -> bool:
    dual_watch = info.node("DualWatch")
    return (
        info.command == "PSI"
        and info.mode == "Close Call"
        and info.screen == "trunk_scan"
        and all(counts[tag] == 1 for tag in _CC_DND_TRUNK_RECORDS)
        and dual_watch is not None
        and dual_watch.get("CC") == "DND"
    )


def _project(info: ScannerInfo, sequence: int, received_at: float) -> _Observation:
    tags = set(info.nodes)
    status = DisplayObservationStatus.CURRENT
    screen_id = info.screen if info.screen in _ALLOWED_TAGS else None
    mode_words = (info.mode or "").casefold().split()
    conflict = None
    if (
        tags & _OVERRIDE_TAGS
        or "menu" in mode_words
        or "replay" in mode_words
        or ("OverWrite" in tags and screen_id not in {"conventional_scan", "trunk_scan"})
    ):
        status = DisplayObservationStatus.OVERRIDE
    elif screen_id is None:
        status = DisplayObservationStatus.UNSUPPORTED_SCREEN
    else:
        allowed = _ALLOWED_TAGS[screen_id]
        counts = Counter(record.tag for record in info.records)
        operating_family = _OPERATING_FAMILIES.get(info.mode or "")
        foreign = tuple(sorted((tags & _CHANNEL_TAGS) - allowed))
        duplicates = tuple(sorted(tag for tag in allowed if counts[tag] > 1))
        reasons = []
        if foreign:
            reasons.append(DisplayConflictReason.FOREIGN_CHANNEL)
        transition_tag = _QUALIFIED_SCAN_TRANSITIONS.get((screen_id, info.mode or ""))
        if (
            operating_family is not None
            and operating_family is not _DATA_FAMILIES[screen_id]
            and transition_tag not in tags
            and not _cc_dnd_trunk_transition(info, counts)
        ):
            reasons.append(DisplayConflictReason.MODE_SCREEN_MISMATCH)
        if duplicates:
            reasons.append(DisplayConflictReason.DUPLICATE_RECORDS)
        # Do not choose between two simultaneous frequency sources in WX.
        if screen_id == "wx_alert" and {"WxChannel", "SrchFrequency"} <= tags:
            reasons.append(DisplayConflictReason.WEATHER_FREQUENCY_SOURCES)
        if reasons:
            status = DisplayObservationStatus.AMBIGUOUS_RECORDS
            conflict = ScannerDisplayConflict(
                screen_id,
                info.mode if info.mode in _OPERATING_FAMILIES else None,
                tuple(reasons),
                duplicates,
                foreign,
            )
    if status is not DisplayObservationStatus.CURRENT or screen_id is None:
        return _Observation(
            sequence, received_at, status, None, RadioStateSnapshot(), conflict=conflict
        )

    # Build the existing shared snapshot from ONLY this screen's records. For
    # example a leftover System/Site cannot populate a weather option. This
    # temporary object never escapes; the original XML is not copied or kept.
    allowed = _ALLOWED_TAGS[screen_id]
    selected = ScannerInfo(
        command=info.command,
        mode=None,
        screen=screen_id,
        nodes={tag: node for tag, node in info.nodes.items() if tag in allowed},
        raw_xml="",
        received_at=info.received_at,
    )
    snapshot = snapshot_from_scanner_info(selected)
    live_values = project_live_values(selected)
    property_node = selected.node("Property")
    raw_led = None if property_node is None else property_node.get("A_Led")
    try:
        alert_led = None if raw_led is None else ScannerAlertLed(raw_led)
    except ValueError:
        alert_led = None
    # Only scanning name bands have qualified hold-driven inversion. Exact
    # On/Off values are independent; missing/invalid attributes never mean Off.
    indicators = ScannerDisplayIndicators(
        alert_led=alert_led,
        system_hold={"On": True, "Off": False}.get(snapshot.system_hold or ""),
        department_hold={"On": True, "Off": False}.get(snapshot.department_hold or ""),
        site_hold={"On": True, "Off": False}.get(snapshot.site_hold or "")
        if screen_id == "trunk_scan"
        else None,
        channel_hold=(
            {"On": True, "Off": False}.get(snapshot.channel_hold or "")
            if screen_id in {"conventional_scan", "trunk_scan"}
            else None
        ),
    )
    # Cache only fields used by the reviewed value projector, with bounded
    # text. Unrelated scanner fields, raw root mode text and XML are discarded.
    snapshot = RadioStateSnapshot(
        system=_bounded_text(snapshot.system),
        department=_bounded_text(snapshot.department),
        channel=_bounded_text(snapshot.channel),
        site=_bounded_text(snapshot.site),
        frequency=_bounded_text(snapshot.frequency),
        sub_audio_detected=_bounded_text(snapshot.sub_audio_detected),
        service_type=_bounded_text(snapshot.service_type),
        talkgroup_id=_bounded_text(snapshot.talkgroup_id),
        unit_id=_bounded_text(snapshot.unit_id),
        p25_status=_bounded_text(snapshot.p25_status),
        recording=_bounded_text(snapshot.recording),
        volume=_bounded_level(snapshot.volume),
        squelch=_bounded_level(snapshot.squelch),
        rssi=_bounded_rssi(snapshot.rssi),
    )
    return _Observation(
        sequence,
        received_at,
        status,
        screen_id,
        snapshot,
        indicators,
        live_values,
        quick_key_selection(selected),
    )


class ScannerDisplayAdapter:
    """One selected endpoint, with explicit invalidation and no I/O or timers.

    The scanner owner must call begin_session for each new connection, feed
    complete ordered observations (including unchanged PSI), and disconnect on
    connection loss. Wall-clock ScannerInfo.received_at is not a freshness clock.
    Partial state changes or scalar getters must NOT refresh this adapter.
    """

    def __init__(self, endpoint_id: UUID, *, stale_after: float) -> None:
        if not isinstance(endpoint_id, UUID):
            raise ScannerDisplayAdapterError("An endpoint selection UUID is required.")
        stale_after = _time(stale_after)
        if not 0 < stale_after <= 300:
            raise ScannerDisplayAdapterError(
                "Stale interval must be greater than zero and at most 300s."
            )
        self._endpoint_id = endpoint_id
        self._stale_after = stale_after
        self._lock = threading.Lock()
        self._session: DisplayObservationSession | None = None
        self._observation: _Observation | None = None
        self._latest_time = 0.0

    def _advance_time(self, now: float) -> float:
        now = _time(now)
        if now < self._latest_time:
            raise ScannerDisplayAdapterError("Monotonic clock moved backwards.")
        self._latest_time = now
        return now

    def begin_session(self, *, now: float) -> DisplayObservationSession:
        with self._lock:
            now = self._advance_time(now)
            self._session = DisplayObservationSession(self._endpoint_id, now)
            self._observation = None
            return self._session

    def _require_session(self, session: DisplayObservationSession) -> None:
        if self._session is None or session is not self._session:
            raise ScannerDisplayAdapterError("Observation session is foreign or no longer active.")

    def disconnect(self, session: DisplayObservationSession) -> None:
        with self._lock:
            self._require_session(session)
            self._session = None
            self._observation = None

    def clear(self, session: DisplayObservationSession) -> None:
        """Invalidate a rejected owner observation; never refresh its receipt time."""
        with self._lock:
            self._require_session(session)
            self._observation = None

    def observe(
        self,
        session: DisplayObservationSession,
        info: ScannerInfo,
        *,
        sequence: int,
        received_at: float,
        now: float,
    ) -> ScannerDisplayConflict | None:
        """Store one observation; return only a bounded structural conflict, if any."""
        if not isinstance(info, ScannerInfo) or info.command not in {"PSI", "GSI"}:
            raise ScannerDisplayAdapterError("A complete PSI or GSI observation is required.")
        if type(sequence) is not int or not 0 <= sequence < 2**63:
            raise ScannerDisplayAdapterError(
                "An ordered nonnegative observation sequence is required."
            )
        received_at, now = _time(received_at), _time(now)
        with self._lock:
            self._require_session(session)
            previous = self._observation
            if not session.started_at <= received_at <= now:
                raise ScannerDisplayAdapterError("Receipt time is outside the current session.")
            if previous is not None and (
                sequence <= previous.sequence or received_at < previous.received_at
            ):
                raise ScannerDisplayAdapterError("Observation is duplicate or out of order.")
            self._advance_time(now)
            self._observation = _project(info, sequence, received_at)
            return self._observation.conflict

    def quick_key_selection(
        self, session: DisplayObservationSession, *, now: float
    ) -> QuickKeySelection | None:
        """Internal owner context only; does not add fields to display.frame."""
        with self._lock:
            self._require_session(session)
            now = self._advance_time(now)
            observation = self._observation
            if (
                observation is None
                or observation.status is not DisplayObservationStatus.CURRENT
                or now - observation.received_at >= self._stale_after
            ):
                return None
            return observation.quick_keys

    def frame(
        self,
        profile: DisplayProfileSnapshot,
        *,
        now: float,
        style: ScannerDisplayStyle | None = None,
    ) -> ScannerDisplayFrame:
        if style is not None and not isinstance(style, ScannerDisplayStyle):
            raise ScannerDisplayAdapterError(
                "An explicit supported presentation style is required."
            )
        if (
            not isinstance(profile, DisplayProfileSnapshot)
            or profile.endpoint_id != self._endpoint_id
        ):
            raise ScannerDisplayAdapterError("Profile belongs to a different endpoint selection.")
        imported = profile.last_good
        if imported is not None and imported.provenance.binding.endpoint_id != self._endpoint_id:
            raise ScannerDisplayAdapterError("Imported profile has a different endpoint binding.")
        with self._lock:
            now = self._advance_time(now)
            observation = self._observation
            status = (
                DisplayObservationStatus.DISCONNECTED
                if self._session is None
                else DisplayObservationStatus.WAITING
            )
            basis = DisplayLayoutBasis.UNAVAILABLE
            sequence = None
            age = None
            screen = None
            values: tuple[ScannerDisplayValue, ...] = ()
            if observation is not None:
                sequence = observation.sequence
                age = now - observation.received_at
                status = (
                    DisplayObservationStatus.STALE
                    if age >= self._stale_after
                    else observation.status
                )
                if imported is not None and observation.screen_id is not None:
                    mode = _SPECIAL_MODES.get(observation.screen_id)
                    source_family = None if mode is None else mode.data_family
                    if mode is None:
                        simple = (
                            imported.profile.options.simple_mode
                            if style is None
                            else style is ScannerDisplayStyle.SIMPLE
                        )
                        trunk = observation.screen_id == "trunk_scan"
                        source_family = (
                            ScannerDisplayDataFamily.TRUNK
                            if trunk
                            else ScannerDisplayDataFamily.CONVENTIONAL
                        )
                        mode = (
                            ScannerDisplayMode.SIMPLE_TRUNK
                            if trunk and simple
                            else ScannerDisplayMode.DETAIL_TRUNK
                            if trunk
                            else ScannerDisplayMode.SIMPLE_CONVENTIONAL
                            if simple
                            else ScannerDisplayMode.DETAIL_CONVENTIONAL
                        )
                        basis = (
                            DisplayLayoutBasis.PROFILE_PREFERENCE_UNCONFIRMED
                            if style is None
                            else DisplayLayoutBasis.EXPLICIT_PRESENTATION_CHOICE
                        )
                    else:
                        basis = DisplayLayoutBasis.DOCUMENTED_SCREEN
                    screen = resolve_scanner_display_screen(imported.profile, mode)
                    values = scanner_display_values(
                        screen,
                        observation.snapshot,
                        source_family=source_family,
                        current=status is DisplayObservationStatus.CURRENT,
                        live_values=observation.live_values,
                    )
            return ScannerDisplayFrame(
                status=status,
                layout_basis=basis,
                profile_status=profile.status,
                profile_refresh_pending=profile.pending is not None,
                provenance=None if imported is None else imported.provenance,
                profile_revision=None if imported is None else imported.profile.revision,
                sequence=sequence,
                age_seconds=age,
                screen=screen,
                values=values,
                indicators=(
                    observation.indicators
                    if observation is not None and status is DisplayObservationStatus.CURRENT
                    else ScannerDisplayIndicators()
                ),
            )
