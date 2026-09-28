"""Read-only inventory of the 27 keys in the SDS200 V1.02 specification, page 35.

The table's model columns actually say BCD536HP and SDS100. A listed key is
documentation evidence, not qualified model/firmware/transport support. This
module neither constructs commands nor grants access to the existing hold-key
path. General key dispatch, press modes and per-session authorization remain
separate work; every presentation below is therefore unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class FrontPanelKey(StrEnum):
    MENU = "M"
    FUNCTION = "F"
    AVOID = "L"
    DIGIT_1 = "1"
    DIGIT_2 = "2"
    DIGIT_3 = "3"
    DIGIT_4 = "4"
    DIGIT_5 = "5"
    DIGIT_6 = "6"
    DIGIT_7 = "7"
    DIGIT_8 = "8"
    DIGIT_9 = "9"
    DIGIT_0 = "0"
    DOT_NO = "."
    ENTER_YES = "E"
    ROTARY_RIGHT = ">"
    ROTARY_LEFT = "<"
    ROTARY_PUSH = "^"
    VOLUME_PUSH = "V"
    SQUELCH_PUSH = "Q"
    REPLAY = "Y"
    SOFT_1 = "A"
    SOFT_2 = "B"
    SOFT_3 = "C"
    ZIP = "Z"
    SERVICE_TYPE = "T"
    RANGE = "R"


@dataclass(frozen=True, slots=True)
class KeyDefinition:
    code: FrontPanelKey
    label: str
    context_note: str


_NUMERIC_NOTE = "Numeric entry and quick-key behavior depend on the current scanner context."
_SOFT_NOTE = "Use the current scanner soft-key label; do not assume a hold target."

# Preserve the reference table's order, including 1..9,0. These generic labels
# are not claims about a particular model's printed key legends.
FRONT_PANEL_KEYS: tuple[KeyDefinition, ...] = (
    KeyDefinition(FrontPanelKey.MENU, "Menu", "The current menu or dialog must be visible."),
    KeyDefinition(
        FrontPanelKey.FUNCTION, "Function", "Alternate-function behavior depends on context."
    ),
    KeyDefinition(FrontPanelKey.AVOID, "Avoid", "May change persistent scanner settings."),
    *(KeyDefinition(FrontPanelKey(digit), digit, _NUMERIC_NOTE) for digit in "1234567890"),
    KeyDefinition(FrontPanelKey.DOT_NO, "Dot / No", "Decimal entry or dialog rejection."),
    KeyDefinition(FrontPanelKey.ENTER_YES, "Enter / Yes", "May confirm a persistent change."),
    KeyDefinition(FrontPanelKey.ROTARY_RIGHT, "Rotary right", "One ordered step; no held repeat."),
    KeyDefinition(FrontPanelKey.ROTARY_LEFT, "Rotary left", "One ordered step; no held repeat."),
    KeyDefinition(FrontPanelKey.ROTARY_PUSH, "Rotary push", "Distinct from rotation or Enter."),
    KeyDefinition(FrontPanelKey.VOLUME_PUSH, "Volume-knob push", "Not a volume-level setter."),
    KeyDefinition(FrontPanelKey.SQUELCH_PUSH, "Squelch-knob push", "Not a squelch-level setter."),
    KeyDefinition(FrontPanelKey.REPLAY, "Replay", "Scanner playback, not daemon audio playback."),
    KeyDefinition(FrontPanelKey.SOFT_1, "Soft key 1", _SOFT_NOTE),
    KeyDefinition(FrontPanelKey.SOFT_2, "Soft key 2", _SOFT_NOTE),
    KeyDefinition(FrontPanelKey.SOFT_3, "Soft key 3", _SOFT_NOTE),
    KeyDefinition(FrontPanelKey.ZIP, "ZIP", "Location entry may change scanning selection."),
    KeyDefinition(FrontPanelKey.SERVICE_TYPE, "Service Type", "May change scanning selection."),
    KeyDefinition(FrontPanelKey.RANGE, "Range", "May change scanning selection."),
)

ReferenceStatus = Literal["listed", "absent_for_model", "model_not_listed"]


@dataclass(frozen=True, slots=True)
class KeyPresentation:
    definition: KeyDefinition
    label: str
    reference_status: ReferenceStatus
    unavailable_reason: str

    @property
    def available(self) -> Literal[False]:
        """Presentation only; never use reference membership as authorization."""
        return False


def front_panel_inventory(model: str | None = None) -> tuple[KeyPresentation, ...]:
    """Describe every requested key without qualifying or dispatching any key.

    Recognize only the two exact model names printed in the reference table
    (case/outer whitespace ignored). SDS200, SDS150 and missing/unknown models
    retain unknown reference support, rather than inheriting another column.
    No supplied model text is included in labels or reasons.
    """
    if model is not None and type(model) is not str:
        raise TypeError("Scanner model must be text or unavailable.")
    normalized = (
        model.strip().upper() if model is not None and len(model) <= 32 and model.isascii() else ""
    )
    entries: list[KeyPresentation] = []
    for definition in FRONT_PANEL_KEYS:
        label = definition.label
        status: ReferenceStatus = "model_not_listed"
        reason = "No model column in the reviewed table; general key control is not qualified."
        if normalized in ("SDS100", "BCD536HP"):
            status = "listed"
            reason = "Listed in the reference; general key control is not qualified."
        if normalized == "SDS100":
            if definition.code in (FrontPanelKey.SQUELCH_PUSH, FrontPanelKey.SERVICE_TYPE):
                status = "absent_for_model"
                reason = "The reviewed table lists this key as absent for SDS100."
            elif definition.code is FrontPanelKey.VOLUME_PUSH:
                label = "Backlight"
        entries.append(KeyPresentation(definition, label, status, reason))
    return tuple(entries)
