"""Conservative raw-source values for the offline Mimic-SDS descriptor.

This is not an LCD-format renderer or a live-mode detector. Callers must supply
an independently qualified matching mode and freshness before any sample value
is projected. No fields are cached or filled from daemon runtime state.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from .scanner_display_layout import DisplaySlotSelection, ScannerDisplayScreen
from .scanner_display_profile import ScannerDisplayMode
from .state import RadioStateSnapshot

MAX_DISPLAY_VALUE_LENGTH = 256


class ScannerDisplayValueStatus(StrEnum):
    RAW_SOURCE = "raw_source"
    EMPTY = "empty"
    BLANK = "blank"
    CONFIGURATION_UNAVAILABLE = "configuration_unavailable"
    DATA_UNAVAILABLE = "data_unavailable"
    UNQUALIFIED = "unqualified"
    UNKNOWN_TOKEN = "unknown_token"
    INVALID_REGION = "invalid_region"
    INVALID_SOURCE = "invalid_source"
    NOT_CURRENT = "not_current"
    MODE_UNQUALIFIED = "mode_unqualified"
    MODE_MISMATCH = "mode_mismatch"


@dataclass(frozen=True, slots=True)
class ScannerDisplayValue:
    region_id: str
    status: ScannerDisplayValueStatus
    text: str | None = None
    source_fields: tuple[str, ...] = ()


# File Specification V1.08 p.57 (huge), p.60 (large and small).
# Icon membership is not established by those tables and remains unqualified.
_HUGE = frozenset(
    {
        "CTCSS/DCS",
        "FL_Name",
        "Frequency",
        "NumberTag",
        "SysSubID",
        "ServiceType",
        "SiteId",
        "SiteName",
        "SystemType",
        "SystemId",
        "TGID",
        "UnitId",
        "UnitIdName",
        "Volume&Squelch",
        "WACN",
    }
)
_LARGE = _HUGE | frozenset(
    {
        "BattVoltage",
        "D_ErrorCount",
        "Filter",
        "latitude",
        "Lcn",
        "longitude",
        "Noise",
        "Rssi",
        "Rssi Bar",
        "TdmaSlot",
        "USB1_vbus",
        "USB2_vbus",
    }
)
_SMALL = frozenset(
    {
        "ATT",
        "SCR",
        "CC",
        "Day",
        "P25Status",
        "GPS",
        "IFX",
        "Modulation",
        "P_Ch",
        "PRI",
        "REC",
        "REP",
        "Squelch",
        "TdmaSlot",
        "Time",
        "Volume",
        "LVL",
        "WxPRI",
    }
)
_GROUP_TOKENS = {1: _HUGE, 2: _LARGE, 3: _SMALL}

# Explicit allowlist, not getattr(snapshot, user_profile_token). These fields
# preserve source spelling/zeroes. Unit conversion, code labels, graph scales
# and profile-specific TGID formatting still require separate qualification.
_TEXT_FIELDS = {
    "SiteName": "site",
    "Frequency": "frequency",
    "CTCSS/DCS": "sub_audio_detected",
    "ServiceType": "service_type",
    "TGID": "talkgroup_id",
    "UnitId": "unit_id",
    "P25Status": "p25_status",
    "REC": "recording",
}
_NAME_FIELDS = {"system": "system", "department": "department", "channel": "channel"}


def _text(value: object) -> tuple[ScannerDisplayValueStatus, str | None]:
    if value is None or value == "":
        return ScannerDisplayValueStatus.DATA_UNAVAILABLE, None
    if (
        not isinstance(value, str)
        or len(value) > MAX_DISPLAY_VALUE_LENGTH
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        return ScannerDisplayValueStatus.INVALID_SOURCE, None
    return ScannerDisplayValueStatus.RAW_SOURCE, value


def _level(value: object) -> tuple[ScannerDisplayValueStatus, str | None]:
    if value is None:
        return ScannerDisplayValueStatus.DATA_UNAVAILABLE, None
    # A type/size bound, not an invented physical volume/squelch range.
    if type(value) is not int or not -(2**31) <= value < 2**31:
        return ScannerDisplayValueStatus.INVALID_SOURCE, None
    return ScannerDisplayValueStatus.RAW_SOURCE, str(value)


def scanner_display_values(
    screen: ScannerDisplayScreen,
    snapshot: RadioStateSnapshot,
    *,
    source_mode: ScannerDisplayMode | None = None,
    current: bool = False,
) -> tuple[ScannerDisplayValue, ...]:
    """Project one immutable snapshot; stale/unknown/mismatched modes emit no data.

    RAW_SOURCE explicitly does not claim scanner LCD formatting. Renderer text
    must be inserted with escaping/textContent (never interpreted as markup).
    Profile Color/BLACK/WHITE and ID-format settings are not applied here.
    """
    if not isinstance(snapshot, RadioStateSnapshot) or type(current) is not bool:
        raise ValueError("An explicit scanner snapshot and boolean freshness are required.")
    if source_mode is not None and not isinstance(source_mode, ScannerDisplayMode):
        raise ValueError("A qualified source mode must use a supported scanner mode.")
    gate = None
    if not current:
        gate = ScannerDisplayValueStatus.NOT_CURRENT
    elif source_mode is None:
        gate = ScannerDisplayValueStatus.MODE_UNQUALIFIED
    elif source_mode is not screen.layout.requested_mode:
        gate = ScannerDisplayValueStatus.MODE_MISMATCH

    values: list[ScannerDisplayValue] = []
    for slot in screen.regions:
        region, token = slot.region, slot.token
        status, text = ScannerDisplayValueStatus.UNQUALIFIED, None
        fields: tuple[str, ...] = ()
        if slot.selection is DisplaySlotSelection.EMPTY:
            status = ScannerDisplayValueStatus.EMPTY
        elif slot.selection is DisplaySlotSelection.BLANK:
            status = ScannerDisplayValueStatus.BLANK
        elif slot.selection in {
            DisplaySlotSelection.MISSING_GROUP,
            DisplaySlotSelection.INVALID_GROUP_SIZE,
        }:
            status = ScannerDisplayValueStatus.CONFIGURATION_UNAVAILABLE
        elif gate is not None:
            status = gate
        elif region.option is None:
            field = _NAME_FIELDS.get(region.id)
            if field is not None:
                status, text = _text(getattr(snapshot, field))
                fields = (field,)
        elif region.option.group == 4:
            # The slot geometry is known; an icon's data/glyph behavior is not.
            status = ScannerDisplayValueStatus.UNQUALIFIED
        elif token not in _GROUP_TOKENS[region.option.group]:
            status = (
                ScannerDisplayValueStatus.INVALID_REGION
                if token in _HUGE | _LARGE | _SMALL
                else ScannerDisplayValueStatus.UNKNOWN_TOKEN
            )
        elif token in _TEXT_FIELDS:
            field = _TEXT_FIELDS[token]
            status, text = _text(getattr(snapshot, field))
            fields = (field,)
        elif token in {"Volume", "Squelch", "Volume&Squelch"}:
            fields = {
                "Volume": ("volume",),
                "Squelch": ("squelch",),
                "Volume&Squelch": ("volume", "squelch"),
            }[token]
            parts = [_level(getattr(snapshot, field)) for field in fields]
            if all(part[0] is ScannerDisplayValueStatus.RAW_SOURCE for part in parts):
                status = ScannerDisplayValueStatus.RAW_SOURCE
                text = " / ".join(part[1] for part in parts if part[1] is not None)
            elif any(part[0] is ScannerDisplayValueStatus.INVALID_SOURCE for part in parts):
                status = ScannerDisplayValueStatus.INVALID_SOURCE
            else:
                status = ScannerDisplayValueStatus.DATA_UNAVAILABLE
        elif token == "Rssi":
            fields = ("rssi",)
            if snapshot.rssi is None:
                status = ScannerDisplayValueStatus.DATA_UNAVAILABLE
            elif (
                type(snapshot.rssi) not in (int, float)
                or abs(snapshot.rssi) > 1e9
                or not math.isfinite(snapshot.rssi)
            ):
                status = ScannerDisplayValueStatus.INVALID_SOURCE
            else:
                status, text = ScannerDisplayValueStatus.RAW_SOURCE, str(snapshot.rssi)
        values.append(ScannerDisplayValue(region.id, status, text, fields))
    return tuple(values)
