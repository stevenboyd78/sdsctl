"""Public, source-code-only contract for the guarded Mimic-SDS browser reader."""

from __future__ import annotations

from .scanner_display_adapter import DisplayLayoutBasis, DisplayObservationStatus
from .scanner_display_frame_preview import _CAPTIONS, _LED_COLORS
from .scanner_display_layout import (
    DisplayMappingIssueKind,
    DisplaySlotSelection,
    scanner_display_layout,
)
from .scanner_display_profile import ScannerDisplayMode
from .scanner_display_profile_state import DisplayProfileSourceKind, DisplayProfileStatus
from .scanner_display_values import ScannerDisplayValueStatus


def scanner_display_empty_message(status: str, *, has_profile: bool) -> str:
    """Describe absent geometry without mistaking a conflict for an unsupported screen."""
    if not has_profile:
        return "An administrator must import a display profile for this scanner."
    return {
        "current": "Scanner layout unavailable.",
        "waiting": "Connected — waiting for a new scanner frame.",
        "disconnected": "Scanner disconnected — waiting for reconnection.",
        "stale": "Scanner data is stale — waiting for a fresh frame.",
        "unsupported_screen": "This scanner screen is not supported by Mimic-SDS.",
        "override": "Scanner menu, popup or replay is active — normal display paused.",
        "ambiguous_records": "Scanner data is inconsistent — waiting for a matching frame.",
    }[status]


def scanner_display_browser_contract() -> dict[str, object]:
    """Canonical geometry/enums only; never a user profile or live sample."""
    layouts = {}
    for mode in ScannerDisplayMode:
        layout = scanner_display_layout(mode)
        layouts[mode.value] = {
            "rows": layout.rows,
            "columns": layout.columns,
            "regions": [
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
                }
                for region in layout.regions
            ],
        }
    return {
        "layouts": layouts,
        "statuses": [item.value for item in DisplayObservationStatus],
        "empty_messages": {
            item.value: scanner_display_empty_message(item.value, has_profile=True)
            for item in DisplayObservationStatus
        },
        "missing_profile_message": scanner_display_empty_message("current", has_profile=False),
        "bases": [item.value for item in DisplayLayoutBasis],
        "profiles": [item.value for item in DisplayProfileStatus],
        "selections": [item.value for item in DisplaySlotSelection],
        "values": [item.value for item in ScannerDisplayValueStatus],
        "issues": [item.value for item in DisplayMappingIssueKind],
        "sources": [item.value for item in DisplayProfileSourceKind],
        "captions": dict(_CAPTIONS),
        "leds": {key.value: value for key, value in _LED_COLORS.items()},
    }
