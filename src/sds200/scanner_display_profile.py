"""Read-only, display-only projection of an SDS scanner's profile.cfg.

Based on SDSx00 File Specification V1.08: DisplayOption (p. 24),
DispOptItems/DispColors and their distinct layout IDs (pp. 30-39).
This internal foundation does not import files, identify an endpoint, infer
freshness, select a live screen, or execute option tokens. Callers must add
those contracts before using it as a synchronized renderer configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum

MAX_PROFILE_BYTES = 1024 * 1024
MAX_RECORDS = 10_000
MAX_RECORD_BYTES = 16 * 1024
MAX_FIELDS = 256
MAX_FIELD_BYTES = 1024
MAX_GROUP_ITEMS = 64
MAX_TOKEN_LENGTH = 64

_LINE_ENDING = re.compile(rb"\r\n|\r|\n")
_INVALID_BYTE = re.compile(rb"[^\x09\x0a\x0d\x20-\x7e]")
_TOKEN = re.compile(r"[A-Za-z0-9_ /&+.-]*")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]+")
_COLOR = re.compile(r"[0-9a-fA-F]{6}")
_METADATA = frozenset({"TargetModel", "ProductName", "FormatVersion"})


class ScannerDisplayProfileError(ValueError):
    """Sanitized structural failure; never include source fields or bytes."""

    def __init__(self, message: str, line_number: int | None = None) -> None:
        self.line_number = line_number
        where = "" if line_number is None else f" line {line_number}"
        super().__init__(f"Scanner display profile{where}: {message}")


class ScannerDisplayDataFamily(StrEnum):
    """Operating data context, independent of the manual Simple/Detail layout."""

    CONVENTIONAL = "conventional"
    TRUNK = "trunk"
    SEARCH_CLOSE_CALL = "search_close_call"
    WEATHER = "weather"
    TONE_OUT = "tone_out"


class ScannerDisplayMode(StrEnum):
    SIMPLE_CONVENTIONAL = "simple_conventional"
    SIMPLE_TRUNK = "simple_trunk"
    DETAIL_CONVENTIONAL = "detail_conventional"
    DETAIL_TRUNK = "detail_trunk"
    SEARCH_CLOSE_CALL = "search_close_call"
    WEATHER = "weather"
    TONE_OUT = "tone_out"

    @property
    def data_family(self) -> ScannerDisplayDataFamily:
        """Simple and Detail use the same qualified operating data context."""
        return {
            ScannerDisplayMode.SIMPLE_CONVENTIONAL: ScannerDisplayDataFamily.CONVENTIONAL,
            ScannerDisplayMode.DETAIL_CONVENTIONAL: ScannerDisplayDataFamily.CONVENTIONAL,
            ScannerDisplayMode.SIMPLE_TRUNK: ScannerDisplayDataFamily.TRUNK,
            ScannerDisplayMode.DETAIL_TRUNK: ScannerDisplayDataFamily.TRUNK,
            ScannerDisplayMode.SEARCH_CLOSE_CALL: ScannerDisplayDataFamily.SEARCH_CLOSE_CALL,
            ScannerDisplayMode.WEATHER: ScannerDisplayDataFamily.WEATHER,
            ScannerDisplayMode.TONE_OUT: ScannerDisplayDataFamily.TONE_OUT,
        }[self]

    @property
    def layout_ids(self) -> tuple[int, int]:
        """Return (DispLayoutId, ColorLayoutId), NOT interchangeable IDs."""
        return {
            ScannerDisplayMode.SIMPLE_CONVENTIONAL: (1, 1),
            ScannerDisplayMode.SIMPLE_TRUNK: (2, 6),
            ScannerDisplayMode.DETAIL_CONVENTIONAL: (3, 2),
            ScannerDisplayMode.DETAIL_TRUNK: (4, 7),
            ScannerDisplayMode.SEARCH_CLOSE_CALL: (5, 3),
            ScannerDisplayMode.WEATHER: (6, 4),
            ScannerDisplayMode.TONE_OUT: (7, 5),
        }[self]


@dataclass(frozen=True, slots=True)
class ScannerDisplayOptions:
    motorola_tgid_format: str
    simple_mode: bool
    edacs_tgid_format: str
    color_mode: str


@dataclass(frozen=True, slots=True)
class ScannerDisplayOptionGroup:
    layout_id: int
    group_id: int
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScannerDisplayColor:
    """Stored text/background order; semantic F/HOLD/soft-key inversion is later."""

    text: str
    background: str


@dataclass(frozen=True, slots=True)
class ScannerDisplayColorGroup:
    color_layout_id: int
    group_id: int
    colors: tuple[ScannerDisplayColor, ...]


@dataclass(frozen=True, slots=True)
class ScannerDisplayProfile:
    """Immutable projection, not a raw-file backup or proof of screen completeness.

    A profile may omit groups. Consumers must explicitly handle missing groups;
    this type neither fills them with defaults nor guesses slot/color pairings.
    Metadata describes the file, not a verified connected scanner identity.
    """

    metadata: tuple[tuple[str, str], ...]
    options: ScannerDisplayOptions
    option_groups: tuple[ScannerDisplayOptionGroup, ...]
    color_groups: tuple[ScannerDisplayColorGroup, ...]

    def options_for(self, mode: ScannerDisplayMode) -> tuple[ScannerDisplayOptionGroup, ...]:
        layout_id, _ = mode.layout_ids
        return tuple(group for group in self.option_groups if group.layout_id == layout_id)

    def colors_for(self, mode: ScannerDisplayMode) -> tuple[ScannerDisplayColorGroup, ...]:
        _, color_layout_id = mode.layout_ids
        return tuple(
            group for group in self.color_groups if group.color_layout_id == color_layout_id
        )

    def as_dict(self) -> dict[str, object]:
        """Fresh JSON-safe, allowlisted descriptor; no reserved/unrelated records."""
        return {
            "schema_version": 1,
            "metadata": dict(self.metadata),
            "options": {
                "motorola_tgid_format": self.options.motorola_tgid_format,
                "simple_mode": self.options.simple_mode,
                "edacs_tgid_format": self.options.edacs_tgid_format,
                "color_mode": self.options.color_mode,
            },
            "option_groups": [
                {
                    "layout_id": group.layout_id,
                    "group_id": group.group_id,
                    "items": list(group.items),
                }
                for group in self.option_groups
            ],
            "color_groups": [
                {
                    "color_layout_id": group.color_layout_id,
                    "group_id": group.group_id,
                    "colors": [
                        {"text": color.text, "background": color.background}
                        for color in group.colors
                    ],
                }
                for group in self.color_groups
            ],
        }

    @property
    def revision(self) -> str:
        """Content digest of normalized display data, NOT endpoint identity/freshness."""
        encoded = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


def _parse_id(value: str, prefix: str, maximum: int, line_number: int) -> int:
    # Require the exact namespace and canonical single-digit spelling.
    if value not in {f"{prefix}={number}" for number in range(1, maximum + 1)}:
        raise ScannerDisplayProfileError("unsupported or malformed display ID", line_number)
    return int(value[-1])


def _parse_options(fields: list[str], line_number: int) -> ScannerDisplayOptions:
    # Empty/reserved columns must not be collapsed. Positions include the tag.
    if len(fields) < 14:
        raise ScannerDisplayProfileError("truncated DisplayOption record", line_number)
    if (
        fields[6] not in {"DEC", "HEX"}
        or fields[11] not in {"Off", "On"}
        or fields[12] not in {"AFS", "DEC"}
        or fields[13] not in {"COLOR", "BLACK", "WHITE"}
    ):
        raise ScannerDisplayProfileError("unsupported display setting", line_number)
    return ScannerDisplayOptions(fields[6], fields[11] == "On", fields[12], fields[13])


def parse_scanner_display_profile(data: bytes) -> ScannerDisplayProfile:
    """Project bounded ASCII/tab data without retaining its private raw records.

    Accept CRLF (scanner format), LF and CR (copied/exported files). Require
    DisplayOption and at least one option and color group, but do not certify
    any mode complete. Reject malformed/duplicate known records atomically.
    Unknown records and reserved columns are ignored; safe unknown option tokens
    and positional empty slots are retained without interpretation.
    """
    if type(data) is not bytes:
        raise TypeError("Scanner display profile data must be bytes.")
    if len(data) > MAX_PROFILE_BYTES:
        raise ScannerDisplayProfileError("byte limit exceeded")
    if _INVALID_BYTE.search(data):
        raise ScannerDisplayProfileError("expected printable ASCII, tabs and line endings")

    lines = _LINE_ENDING.split(data, maxsplit=MAX_RECORDS + 1)
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) > MAX_RECORDS:
        raise ScannerDisplayProfileError("record limit exceeded")

    metadata: dict[str, str] = {}
    options: ScannerDisplayOptions | None = None
    option_groups: dict[tuple[int, int], ScannerDisplayOptionGroup] = {}
    color_groups: dict[tuple[int, int], ScannerDisplayColorGroup] = {}
    for line_number, line in enumerate(lines, start=1):
        if len(line) > MAX_RECORD_BYTES:
            raise ScannerDisplayProfileError("record byte limit exceeded", line_number)
        fields = line.decode("ascii").split("\t")
        if len(fields) > MAX_FIELDS or any(len(value) > MAX_FIELD_BYTES for value in fields):
            raise ScannerDisplayProfileError("field limit exceeded", line_number)
        tag = fields[0]
        if tag in _METADATA:
            if (
                len(fields) != 2
                or not _IDENTIFIER.fullmatch(fields[1])
                or len(fields[1]) > MAX_TOKEN_LENGTH
            ):
                raise ScannerDisplayProfileError("malformed identifying record", line_number)
            if tag in metadata:
                raise ScannerDisplayProfileError("duplicate identifying record", line_number)
            metadata[tag] = fields[1]
        elif tag == "DisplayOption":
            if options is not None:
                raise ScannerDisplayProfileError("duplicate DisplayOption record", line_number)
            options = _parse_options(fields, line_number)
        elif tag == "DispOptItems":
            if not 4 <= len(fields) <= 3 + MAX_GROUP_ITEMS:
                raise ScannerDisplayProfileError("invalid option group length", line_number)
            group_id = _parse_id(fields[1], "DispOptId", 4, line_number)
            layout_id = _parse_id(fields[2], "DispLayoutId", 7, line_number)
            items = tuple(fields[3:])
            if any(len(item) > MAX_TOKEN_LENGTH or not _TOKEN.fullmatch(item) for item in items):
                raise ScannerDisplayProfileError("invalid option token", line_number)
            key = (layout_id, group_id)
            if key in option_groups:
                raise ScannerDisplayProfileError("duplicate option group", line_number)
            option_groups[key] = ScannerDisplayOptionGroup(layout_id, group_id, items)
        elif tag == "DispColors":
            if not 5 <= len(fields) <= 3 + 2 * MAX_GROUP_ITEMS or (len(fields) - 3) % 2:
                raise ScannerDisplayProfileError("invalid color group length", line_number)
            group_id = _parse_id(fields[1], "DispColorId", 7, line_number)
            layout_id = _parse_id(fields[2], "ColorLayoutId", 7, line_number)
            if any(not _COLOR.fullmatch(value) for value in fields[3:]):
                raise ScannerDisplayProfileError(
                    "expected six-digit hexadecimal color", line_number
                )
            key = (layout_id, group_id)
            if key in color_groups:
                raise ScannerDisplayProfileError("duplicate color group", line_number)
            colors = tuple(
                ScannerDisplayColor(fields[index].lower(), fields[index + 1].lower())
                for index in range(3, len(fields), 2)
            )
            color_groups[key] = ScannerDisplayColorGroup(layout_id, group_id, colors)

    if options is None or not option_groups or not color_groups:
        raise ScannerDisplayProfileError("missing display settings, option groups or color groups")
    return ScannerDisplayProfile(
        metadata=tuple(sorted(metadata.items())),
        options=options,
        option_groups=tuple(option_groups[key] for key in sorted(option_groups)),
        color_groups=tuple(color_groups[key] for key in sorted(color_groups)),
    )
