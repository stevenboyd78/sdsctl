"""Source-qualified, renderer-neutral SDS screen regions for offline previews.

Semantic geometry follows the supplied Display Table Layouts grids, checked
against SDSx00 File Specification V1.08 pp.31-39. Coordinates are logical cells,
not LCD pixels. Option/color positions are ONE-based and in separate namespaces.
This does not infer a live mode, supply scanner data, or authorize any control.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .scanner_display_profile import (
    ScannerDisplayColor,
    ScannerDisplayMode,
    ScannerDisplayProfile,
)


class DisplayRegionKind(StrEnum):
    NAME = "name"
    OPTION = "option"
    INDICATOR = "indicator"
    INFORMATION = "information"
    SOFT_KEY = "soft_key"
    SPACER = "spacer"


class DisplaySlotSelection(StrEnum):
    FIXED = "fixed"
    CONFIGURED = "configured"
    BLANK = "blank"
    EMPTY = "empty"
    MISSING_GROUP = "missing_group"
    INVALID_GROUP_SIZE = "invalid_group_size"


class DisplayMappingIssueKind(StrEnum):
    MISSING_GROUP = "missing_group"
    INVALID_GROUP_SIZE = "invalid_group_size"
    UNQUALIFIED_COLOR_ORDER = "unqualified_color_order"
    UNEXPECTED_GROUP = "unexpected_group"


@dataclass(frozen=True, slots=True)
class DisplayProfilePosition:
    group: int
    position: int


@dataclass(frozen=True, slots=True)
class ScannerDisplayRegion:
    id: str
    kind: DisplayRegionKind
    row: int
    column: int
    rows: int
    columns: int
    option: DisplayProfilePosition | None = None
    color: DisplayProfilePosition | None = None
    reverse_colors: bool = False


@dataclass(frozen=True, slots=True)
class ScannerDisplayLayout:
    requested_mode: ScannerDisplayMode
    regions: tuple[ScannerDisplayRegion, ...]
    option_group_sizes: tuple[tuple[int, int], ...]
    color_group_sizes: tuple[tuple[int, int], ...]
    # The detail/special small-color tables conflict with their six-slot grids.
    # Preserve raw records in the profile, but do not guess their color pairing.
    unqualified_color_groups: tuple[int, ...]
    rows: int = 20
    columns: int = 30


@dataclass(frozen=True, slots=True)
class DisplayMappingIssue:
    namespace: str
    group: int
    kind: DisplayMappingIssueKind


@dataclass(frozen=True, slots=True)
class ResolvedScannerDisplayRegion:
    region: ScannerDisplayRegion
    selection: DisplaySlotSelection
    token: str | None
    stored_color: ScannerDisplayColor | None


@dataclass(frozen=True, slots=True)
class ScannerDisplayScreen:
    layout: ScannerDisplayLayout
    profile_revision: str
    color_mode: str
    regions: tuple[ResolvedScannerDisplayRegion, ...]
    issues: tuple[DisplayMappingIssue, ...]


def scanner_display_layout(mode: ScannerDisplayMode) -> ScannerDisplayLayout:
    """Return an explicitly requested mode, never a guessed current LCD screen."""
    if not isinstance(mode, ScannerDisplayMode):
        raise ValueError("An explicit supported scanner display mode is required.")
    simple = mode in {ScannerDisplayMode.SIMPLE_CONVENTIONAL, ScannerDisplayMode.SIMPLE_TRUNK}
    scan = mode in {
        ScannerDisplayMode.SIMPLE_CONVENTIONAL,
        ScannerDisplayMode.SIMPLE_TRUNK,
        ScannerDisplayMode.DETAIL_CONVENTIONAL,
        ScannerDisplayMode.DETAIL_TRUNK,
    }
    regions: list[ScannerDisplayRegion] = []

    def add(
        name: str,
        kind: DisplayRegionKind,
        row: int,
        column: int,
        columns: int,
        *,
        rows: int = 1,
        option: tuple[int, int] | None = None,
        color: tuple[int, int] | None = None,
        reverse: bool = False,
    ) -> None:
        regions.append(
            ScannerDisplayRegion(
                name,
                kind,
                row,
                column,
                rows,
                columns,
                None if option is None else DisplayProfilePosition(*option),
                None if color is None else DisplayProfilePosition(*color),
                reverse,
            )
        )

    indicator, option_kind = DisplayRegionKind.INDICATOR, DisplayRegionKind.OPTION
    information, spacer = DisplayRegionKind.INFORMATION, DisplayRegionKind.SPACER
    add("function", indicator, 0, 0, 2, color=(6, 1), reverse=True)
    for index in range(1, 5):
        add(
            f"option_{index}",
            option_kind,
            0,
            2 + (index - 1) * 6,
            6,
            option=(3, index),
            color=(3, index) if simple else None,
        )
    add("signal", indicator, 0, 26, 2, color=(6, 2))
    add("battery", indicator, 0, 28, 2, color=(6, 3))
    if simple:
        add("spacer_0", spacer, 1, 0, 2, color=(6, 4))
        for index in range(5, 9):
            add(
                f"option_{index}",
                option_kind,
                1,
                2 + (index - 5) * 6,
                6,
                option=(3, index),
                color=(3, index),
            )
    else:
        add("information_1", information, 1, 0, 14, color=(1, 7 if scan else 9))
        for position, index in enumerate((7, 8), start=5):
            add(f"option_{index}", option_kind, 1, 14 + (index - 7) * 6, 6, option=(3, position))
        for index in (1, 2):
            add(
                f"information_{index + 1}",
                information,
                index + 1,
                0,
                15,
                color=(1, index + (7 if scan else 9)),
            )
            position = index + (10 if scan else 6)
            add(
                f"option_c_{index}",
                option_kind,
                index + 1,
                15,
                15,
                option=(2, position),
                color=(4, position),
            )
    add("key_lock", indicator, 1, 26, 2, color=(6, 5 if simple else 4))
    add("direction", indicator, 1, 28, 2, color=(6, 6 if simple else 5))

    if scan:
        for index, name in enumerate(("system", "department", "channel")):
            row, height = (2 + index * 5, 4) if simple else (4 + index * 3, 2)
            add(name, DisplayRegionKind.NAME, row, 0, 30, rows=height, color=(1, 1 + index * 2))
            add(
                f"{name}_option",
                option_kind,
                row + height,
                0,
                26,
                option=(1, index + 1),
                color=(2, index + 1),
            )
            add(f"{name}_avoid", indicator, row + height, 26, 4, color=(1, 2 + index * 2))
    else:
        for index in range(1, 4):
            add(f"primary_{index}", information, 2 + index * 2, 0, 30, rows=2, color=(1, index))
        add("sub_information", information, 10, 0, 16, color=(1, 4))
        add("modulation", information, 10, 16, 6, color=(1, 5))
        add("avoid", indicator, 10, 22, 4, color=(1, 6))
        add("hold", indicator, 10, 26, 4, color=(1, 7), reverse=True)
        add("detail_information", information, 11, 0, 30, rows=4, color=(1, 8))

    large_rows = 1 if simple else (5 if scan else 3)
    for row_index in range(large_rows):
        for column_index, name in enumerate(("a", "b")):
            position = row_index * 2 + column_index + 1
            add(
                f"option_{name}_{row_index + 1}",
                option_kind,
                18 - large_rows + row_index,
                column_index * 15,
                15,
                option=(2, position),
                color=(4, position),
            )
    for index in range(1, 11):
        add(
            f"icon_{index}",
            option_kind,
            18,
            (index - 1) * 3,
            3,
            option=(4, index),
            color=(5, index),
        )
    for index, (column, width) in enumerate(((0, 9), (10, 10), (21, 9)), start=1):
        add(
            f"soft_key_{index}",
            DisplayRegionKind.SOFT_KEY,
            19,
            column,
            width,
            color=(7, index * 2 - 1),
            reverse=True,
        )
    add("spacer_1", spacer, 19, 9, 1, color=(7, 2))
    add("spacer_2", spacer, 19, 20, 1, color=(7, 4))

    large_count = 2 if simple else (12 if scan else 8)
    option_sizes = ([(1, 3)] if scan else []) + [
        (2, large_count),
        (3, 8 if simple else 6),
        (4, 10),
    ]
    color_sizes = [(1, 6 if simple else (9 if scan else 11))]
    if scan:
        color_sizes.append((2, 3))
    if simple:
        color_sizes.append((3, 8))
    color_sizes.extend(((4, large_count), (5, 10), (6, 6 if simple else 5), (7, 5)))
    return ScannerDisplayLayout(
        mode,
        tuple(regions),
        tuple(option_sizes),
        tuple(color_sizes),
        () if simple else (3,),
    )


def resolve_scanner_display_screen(
    profile: ScannerDisplayProfile,
    mode: ScannerDisplayMode,
) -> ScannerDisplayScreen:
    """Map known positions only; missing/extra group items never shift slots.

    Colors are stored values, not applied COLOR/BLACK/WHITE or inverted output.
    A configured token is not a claim that live data or LCD formatting exists.
    """
    layout = scanner_display_layout(mode)
    options = {group.group_id: group.items for group in profile.options_for(mode)}
    colors = {group.group_id: group.colors for group in profile.colors_for(mode)}
    issues: list[DisplayMappingIssue] = []
    valid_options: set[int] = set()
    valid_colors: set[int] = set()
    for namespace, actual_sizes, expected, valid in (
        (
            "option",
            {key: len(value) for key, value in options.items()},
            layout.option_group_sizes,
            valid_options,
        ),
        (
            "color",
            {key: len(value) for key, value in colors.items()},
            layout.color_group_sizes,
            valid_colors,
        ),
    ):
        for group, count in expected:
            if group not in actual_sizes:
                issues.append(
                    DisplayMappingIssue(namespace, group, DisplayMappingIssueKind.MISSING_GROUP)
                )
            elif actual_sizes[group] != count:
                issues.append(
                    DisplayMappingIssue(
                        namespace, group, DisplayMappingIssueKind.INVALID_GROUP_SIZE
                    )
                )
            else:
                valid.add(group)
        allowed = {group for group, _ in expected}
        if namespace == "color":
            allowed.update(layout.unqualified_color_groups)
        for group in sorted(actual_sizes.keys() - allowed):
            issues.append(
                DisplayMappingIssue(namespace, group, DisplayMappingIssueKind.UNEXPECTED_GROUP)
            )
    for group in layout.unqualified_color_groups:
        issues.append(
            DisplayMappingIssue("color", group, DisplayMappingIssueKind.UNQUALIFIED_COLOR_ORDER)
        )

    resolved: list[ResolvedScannerDisplayRegion] = []
    for region in layout.regions:
        token = None
        selection = DisplaySlotSelection.FIXED
        if region.option is not None:
            group, position = region.option.group, region.option.position
            if group not in options:
                selection = DisplaySlotSelection.MISSING_GROUP
            elif group not in valid_options:
                selection = DisplaySlotSelection.INVALID_GROUP_SIZE
            else:
                token = options[group][position - 1]
                selection = {
                    "": DisplaySlotSelection.BLANK,
                    "Empty": DisplaySlotSelection.EMPTY,
                }.get(
                    token,
                    DisplaySlotSelection.CONFIGURED,
                )
        stored_color = None
        if region.color is not None and region.color.group in valid_colors:
            stored_color = colors[region.color.group][region.color.position - 1]
        resolved.append(ResolvedScannerDisplayRegion(region, selection, token, stored_color))
    return ScannerDisplayScreen(
        layout,
        profile.revision,
        profile.options.color_mode,
        tuple(resolved),
        tuple(issues),
    )
