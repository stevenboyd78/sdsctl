"""Shared, source-qualified indicator presentation (no scanner I/O).

Blank telemetry means confirmed off only for a known configured indicator.
Missing/invalid/stale telemetry is unknown, never a gray confirmed-off label.
These labels/bars are presentation, not pixel-exact LCD glyphs.
"""

from __future__ import annotations

from dataclasses import dataclass

TOGGLE_REGIONS = {
    "function": "F",
    "system_avoid": "AVOID",
    "department_avoid": "AVOID",
    "channel_avoid": "AVOID",
}
TOGGLE_TOKENS = {"PRI": "PRI", "CC": "CC", "WxPRI": "WX", "REC": "REC", "IFX": "IFX", "P_Ch": "P"}
INACTIVE_COLOR = "707070"
UNKNOWN_COLOR = "9aa6b2"
SIGNAL_BARS = "▁▂▃▄▅"


@dataclass(frozen=True, slots=True)
class IndicatorPresentation:
    text: str
    state: str
    foreground: str | None = None
    background: str | None = None


def present_indicator(
    identifier: str, token: str | None, selection: str, status: str, text: str | None
) -> IndicatorPresentation | None:
    """Return a known indicator's presentation; leave ordinary fields alone."""
    if selection not in ("fixed", "configured"):
        return None
    if identifier == "signal":
        if status == "raw_source" and text in tuple("012345"):
            return IndicatorPresentation(SIGNAL_BARS[: int(text)], "level_" + text)
        return IndicatorPresentation("?", "unknown", UNKNOWN_COLOR, "000000")
    label = TOGGLE_REGIONS.get(identifier) or (
        TOGGLE_TOKENS.get(token or "") if identifier.startswith("icon_") else None
    )
    if label is None:
        return None
    if status == "raw_source" and text in (label, "T-AVOID" if label == "AVOID" else label):
        return IndicatorPresentation(text, "temporary" if text == "T-AVOID" else "on")
    if status == "blank":
        return IndicatorPresentation(
            "" if identifier == "function" else label, "off", INACTIVE_COLOR, "000000"
        )
    return IndicatorPresentation("?", "unknown", UNKNOWN_COLOR, "000000")
