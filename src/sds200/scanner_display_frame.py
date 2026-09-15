"""Bounded renderer-neutral wire projection of a qualified Mimic-SDS frame.

Only explicit fields cross this boundary. No raw XML/profile, paths, credentials,
host clock substitutes, HTML, CSS or scanner commands are part of this payload.
"""

from __future__ import annotations

import math
import re
import unicodedata

from .scanner_display_adapter import DisplayObservationStatus, ScannerDisplayFrame
from .scanner_display_layout import scanner_display_layout
from .scanner_display_profile_state import DisplayProfileStatus
from .scanner_display_values import MAX_DISPLAY_VALUE_LENGTH, ScannerDisplayValueStatus


def project_scanner_display_frame(frame: ScannerDisplayFrame) -> dict[str, object]:
    current = frame.status is DisplayObservationStatus.CURRENT
    if frame.age_seconds is not None and (
        not math.isfinite(frame.age_seconds) or not 0 <= frame.age_seconds <= 1e15
    ):
        raise ValueError("Invalid scanner display frame age.")
    provenance = frame.provenance
    source = (
        None
        if provenance is None
        else {
            "source_id": str(provenance.binding.source_id),
            "source_kind": provenance.binding.source_kind.value,
            "acquired_at": provenance.acquired_at.isoformat(),
            "imported_at": provenance.imported_at.isoformat(),
        }
    )
    result: dict[str, object] = {
        "status": frame.status.value,
        "layout_basis": frame.layout_basis.value,
        "profile_status": frame.profile_status.value,
        "profile_refresh_pending": frame.profile_refresh_pending,
        "profile_revision": frame.profile_revision,
        "source": source,
        "sequence": frame.sequence,
        "age_seconds": frame.age_seconds,
        "screen": None,
        "indicators": {
            "alert_led": (
                frame.indicators.alert_led.value
                if current and frame.indicators.alert_led is not None
                else None
            ),
            "system_hold": frame.indicators.system_hold if current else None,
            "department_hold": frame.indicators.department_hold if current else None,
            "channel_hold": frame.indicators.channel_hold if current else None,
            "site_hold": frame.indicators.site_hold if current else None,
        },
    }
    screen = frame.screen
    if screen is None:
        if frame.values:
            raise ValueError("A scanner display without a screen cannot carry values.")
        return result
    if (
        screen.profile_revision != frame.profile_revision
        or provenance is None
        or frame.profile_status is DisplayProfileStatus.UNAVAILABLE
        or screen.layout != scanner_display_layout(screen.layout.requested_mode)
        or tuple(slot.region for slot in screen.regions) != screen.layout.regions
        or len(frame.values) != len(screen.regions)
        or len({value.region_id for value in frame.values}) != len(frame.values)
        or {value.region_id for value in frame.values}
        != {slot.region.id for slot in screen.regions}
    ):
        raise ValueError("Scanner display frame requires a matching profile and canonical regions.")
    values = {value.region_id: value for value in frame.values}
    regions = []
    for slot in screen.regions:
        region, value = slot.region, values[slot.region.id]
        status = value.status
        if not current and status not in {
            ScannerDisplayValueStatus.EMPTY,
            ScannerDisplayValueStatus.BLANK,
            ScannerDisplayValueStatus.CONFIGURATION_UNAVAILABLE,
        }:
            status = ScannerDisplayValueStatus.NOT_CURRENT
        text = value.text if status is ScannerDisplayValueStatus.RAW_SOURCE else None
        if text is not None and (
            type(text) is not str
            or len(text) > MAX_DISPLAY_VALUE_LENGTH
            or any(unicodedata.category(char).startswith("C") for char in text)
        ):
            raise ValueError("Scanner display frame contains an invalid value.")
        color = slot.stored_color
        if color is not None and any(
            re.fullmatch(r"[0-9a-fA-F]{6}", item) is None for item in (color.text, color.background)
        ):
            raise ValueError("Scanner display frame contains an invalid color.")
        regions.append(
            {
                "id": region.id,
                "kind": region.kind.value,
                "row": region.row,
                "column": region.column,
                "rows": region.rows,
                "columns": region.columns,
                "alignment": region.alignment.value,
                "name_lines": region.name_lines,
                "reverse_colors": region.reverse_colors,
                "selection": slot.selection.value,
                "token": slot.token,
                "stored_color": None
                if color is None
                else {
                    "text": color.text,
                    "background": color.background,
                },
                "value_status": status.value,
                "text": text,
            }
        )
    result["screen"] = {
        "mode": screen.layout.requested_mode.value,
        "rows": screen.layout.rows,
        "columns": screen.layout.columns,
        "color_mode": screen.color_mode,
        "regions": regions,
        "issues": [
            {"namespace": issue.namespace, "group": issue.group, "kind": issue.kind.value}
            for issue in screen.issues
        ],
    }
    return result
