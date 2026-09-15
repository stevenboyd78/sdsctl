"""Pure SVG development preview, not a user-facing Mimic-SDS renderer.

No JavaScript, external resources, live I/O or active scanner controls. Every
preview is explicitly labelled offline; raw-source values are not LCD parity.
"""

from __future__ import annotations

import re
from html import escape

from .scanner_display_layout import DisplayRegionKind, ScannerDisplayScreen
from .scanner_display_values import ScannerDisplayValue, ScannerDisplayValueStatus

_COLOR = re.compile(r"[0-9a-fA-F]{6}")


def render_scanner_display_preview(
    screen: ScannerDisplayScreen,
    values: tuple[ScannerDisplayValue, ...],
) -> str:
    """Render one bounded logical screen with safely escaped, clipped text.

    Callers own the original profile/sample; the SVG contains only the selected
    screen's display data. This development renderer must not be used as a
    connected view: it has no endpoint, stale-status or interaction adapter.
    """
    ids = [value.region_id for value in values]
    if len(ids) != len(set(ids)) or set(ids) != {slot.region.id for slot in screen.regions}:
        raise ValueError("Preview values must match the selected regions exactly once.")
    by_id = {value.region_id: value for value in values}
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="740" viewBox="0 0 960 740"'
        ' role="img" aria-labelledby="preview-title preview-description">',
        '<title id="preview-title">Mimic-SDS offline development preview</title>',
        '<desc id="preview-description">Logical scanner regions with raw-source or unavailable '
        "sample values. Not a live scanner screen or a claim of LCD-format parity.</desc>",
        '<rect width="960" height="740" fill="#10151d"/>',
        '<g font-family="DejaVu Sans, sans-serif" fill="#f1f5f9">',
        '<text x="20" y="30" font-size="21" font-weight="bold">MIMIC-SDS / OFFLINE PREVIEW</text>',
        f'<text x="20" y="54" font-size="14">Selected mode: '
        f"{escape(screen.layout.requested_mode.value.replace('_', ' '))}</text>",
        '<text x="20" y="75" font-size="12" fill="#cbd5e1">No scanner connection. '
        "Raw source values only; no inferred units, live mode, clock, or icon behavior.</text>",
    ]
    cell_width, cell_height, top = 920 / screen.layout.columns, 26, 90
    for index, slot in enumerate(screen.regions):
        region, value = slot.region, by_id[slot.region.id]
        x, y = 20 + region.column * cell_width, top + region.row * cell_height
        width, height = region.columns * cell_width, region.rows * cell_height
        foreground, background = "cbd5e1", "18212d"
        if screen.color_mode == "COLOR" and slot.stored_color is not None:
            stored = slot.stored_color
            if _COLOR.fullmatch(stored.text) and _COLOR.fullmatch(stored.background):
                foreground, background = stored.text, stored.background
                if region.reverse_colors:
                    foreground, background = background, foreground
        label = slot.token if slot.token not in (None, "", "Empty") else region.id.replace("_", " ")
        empty = value.status in {ScannerDisplayValueStatus.EMPTY, ScannerDisplayValueStatus.BLANK}
        raw = value.status is ScannerDisplayValueStatus.RAW_SOURCE
        shown = value.text if raw and value.text is not None else "--"
        description = f"{label}: {value.status.value}"
        if raw:
            description += f"; source fields: {', '.join(value.source_fields)}"
        parts.extend(
            [
                f'<g data-region="{escape(region.id, quote=True)}" '
                f'data-value-status="{escape(value.status.value, quote=True)}">',
                f"<title>{escape(description)}</title>",
                f'<rect x="{x:.2f}" y="{y}" width="{width:.2f}" height="{height}" '
                f'fill="#{background}" stroke="#536173" stroke-width="0.5"/>',
                f'<clipPath id="region-{index}"><rect x="{x + 3:.2f}" y="{y + 1}" '
                f'width="{width - 6:.2f}" height="{height - 2}"/></clipPath>',
                f'<g clip-path="url(#region-{index})" fill="#{foreground}">',
            ]
        )
        if region.kind is not DisplayRegionKind.SPACER and not empty:
            # Preview labels remain visible even where the LCD uses an icon.
            label_text = (
                label
                if len(label) <= max(5, int(width / 5.2))
                else (label[: max(4, int(width / 5.2)) - 1] + "…")
            )
            font_size = (
                30
                if region.kind is DisplayRegionKind.NAME and region.rows >= 4
                else (24 if region.kind is DisplayRegionKind.NAME else 12)
            )
            max_characters = max(2, int((width - 8) / (font_size * 0.62)))
            shown = shown if len(shown) <= max_characters else shown[: max_characters - 1] + "…"
            parts.extend(
                [
                    f'<text x="{x + 4:.2f}" y="{y + 10}" font-size="9">{escape(label_text)}</text>',
                    f'<text x="{x + 4:.2f}" y="{y + max(23, (height + font_size) / 2):.2f}" '
                    f'font-size="{font_size}" font-weight="bold">{escape(shown)}</text>',
                ]
            )
        parts.extend(["</g>", "</g>"])
    unknown = sum(value.status is ScannerDisplayValueStatus.UNQUALIFIED for value in values)
    parts.extend(
        [
            '<text x="20" y="640" font-size="13">Blank = explicitly empty. '
            "-- = unavailable, unqualified, or not current. "
            "Cell titles provide the exact state.</text>",
            f'<text x="20" y="665" font-size="13">Mapping issues: {len(screen.issues)} | '
            f"Unqualified value regions: {unknown} | "
            f"Profile color mode: {escape(screen.color_mode)}</text>",
            '<text x="20" y="690" font-size="12" fill="#cbd5e1">Neutral cells do not claim scanner '
            "colors. BLACK/WHITE transforms and ambiguous small-field colors "
            "are not applied.</text>",
            '<text x="20" y="718" font-size="12" fill="#cbd5e1">Development artifact only. '
            "No active buttons, scanner commands, profile import, or background refresh.</text>",
            "</g></svg>",
        ]
    )
    return "\n".join(parts)
