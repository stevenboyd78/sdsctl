"""Read-only Textual Mimic-SDS screen and character-cell renderer."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, ClassVar

from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Static

from . import __version__
from .front_panel_keys import FRONT_PANEL_INVENTORY_VERSION, FrontPanelKey
from .scanner_display_presentation import present_indicator, signal_reception_state
from .scanner_display_reader import DisplayFrameReader
from .scanner_display_supplemental_reader import SupplementalFrameReader
from .scanner_display_web import scanner_display_browser_contract, scanner_display_empty_message
from .tui_clock import LocalHeaderClock

_CONTRACT: Any = scanner_display_browser_contract()
_STATES = {
    "current": "Current scanner data",
    "waiting": "Connected — waiting for new scanner data",
    "disconnected": "Disconnected — retrying",
    "stale": "Stale scanner data — values cleared",
    "unsupported_screen": "Unsupported screen (Waterfall remains separate)",
    "override": "Scanner menu / popup / replay — values cleared",
    "ambiguous_records": "Conflicting scanner data — values cleared",
}


def safe_terminal_text(value: str) -> str:
    """Keep ordinary text and line breaks, not controls/formatting escapes."""
    return "".join(
        char if char == "\n" or not unicodedata.category(char).startswith("C") else "?"
        for char in value
    )


_FRONT_PANEL_ENTRY_FIELDS = {
    "code",
    "label",
    "context_note",
    "reference_status",
    "control_status",
    "available",
    "unavailable_reason",
}


def _front_panel_text(value: object) -> str | None:
    if (
        type(value) is str
        and 0 < len(value) <= 256
        and value.isascii()
        and all(ord(character) >= 32 for character in value)
    ):
        return value
    return None


def render_front_panel_inventory_terminal(
    inventory: Mapping[str, object] | None,
) -> Text:
    """Render one already-fetched fail-closed inventory without adding controls."""

    unavailable = Text(
        "Front-panel keys — read-only inventory\n"
        "Inventory unavailable for this TUI connection. "
        "No scanner-key controls are enabled.\n"
    )
    if inventory is None or set(inventory) != {
        "version",
        "controls_available",
        "keys",
    }:
        return unavailable
    if (
        type(inventory["version"]) is not int
        or inventory["version"] != FRONT_PANEL_INVENTORY_VERSION
        or type(inventory["controls_available"]) is not bool
    ):
        return unavailable
    keys = inventory["keys"]
    expected_codes = tuple(key.value for key in FrontPanelKey)
    if not isinstance(keys, list) or len(keys) != len(expected_codes):
        return unavailable

    rows: list[tuple[str, str, str, str, str, str, bool]] = []
    any_available = False
    for expected_code, entry in zip(expected_codes, keys, strict=True):
        if not isinstance(entry, Mapping) or set(entry) != _FRONT_PANEL_ENTRY_FIELDS:
            return unavailable
        code = _front_panel_text(entry["code"])
        label = _front_panel_text(entry["label"])
        context_note = _front_panel_text(entry["context_note"])
        reason = _front_panel_text(entry["unavailable_reason"])
        reference_status = entry["reference_status"]
        control_status = entry["control_status"]
        available = entry["available"]
        qualified_menu = (
            code == FrontPanelKey.MENU.value
            and reference_status == "model_not_listed"
            and control_status == "qualified"
            and available is True
        )
        expected_control_status = (
            "unsupported" if reference_status == "absent_for_model" else "unqualified"
        )
        if (
            code != expected_code
            or label is None
            or context_note is None
            or reason is None
            or reference_status not in {"listed", "absent_for_model", "model_not_listed"}
            or type(available) is not bool
            or (
                not qualified_menu
                and (control_status != expected_control_status or available is not False)
            )
        ):
            return unavailable
        any_available = any_available or available
        rows.append(
            (
                code,
                label,
                context_note,
                str(reference_status),
                str(control_status),
                reason,
                available,
            )
        )

    if inventory["controls_available"] is not any_available:
        return unavailable

    output = Text(
        "Front-panel keys — read-only inventory\n"
        f"Inventory v{FRONT_PANEL_INVENTORY_VERSION} | {len(rows)} codes | "
        f"qualified elsewhere: {'1' if any_available else '0'} | controls enabled here: no\n"
        "These codes are labels, not TUI shortcuts. No scanner-key dispatch is installed.\n"
    )
    for (
        code,
        label,
        context_note,
        reference_status,
        control_status,
        reason,
        available,
    ) in rows:
        availability = "qualified elsewhere; read-only here" if available else "unavailable"
        output.append(
            f"\n{code}  {label} — {availability} "
            f"({reference_status.replace('_', ' ')}; {control_status}).\n"
            f"   {context_note} {reason}\n"
        )
    return output


def _value(region: Mapping[str, Any]) -> str:
    value = region["text"] if region["text"] is not None else "—"
    token, identifier = region["token"], region["id"]
    caption = None
    if identifier.startswith("option_") and (
        any(f"{name}_" in identifier for name in "abc")
        or token in ("Volume", "Squelch", "Volume&Squelch")
    ):
        caption = _CONTRACT["captions"].get(token)
    if caption and not value.lower().startswith(caption.lower() + ":"):
        return f"{caption}: {value}"
    return str(value)


def render_mimic_terminal(
    frame: Mapping[str, Any], *, width: int, height: int, treatment: str = "strips"
) -> Text:
    """Render a validated frame, never markup/ANSI or speculative LCD glyphs.

    Rich handles truecolor -> 256/16 color quantization at the final console.
    Logical grid geometry stays constant; terminal cells cannot change font size.
    """
    width, height = min(500, width), min(160, height)
    if width < 60 or height < 22:
        return Text("Mimic-SDS needs at least 60 columns × 22 rows.\nResize or press M to return.")
    screen = frame["screen"]
    if screen is None:
        return Text(
            scanner_display_empty_message(
                frame["status"], has_profile=frame["profile_revision"] is not None
            )
        )
    edge_width = 2
    inner_width, inner_height = width - (edge_width * 2), height - 2
    console = Console(width=inner_width)
    content: list[list[tuple[int, Text]]] = [[] for _ in range(inner_height)]
    signal = next(region for region in screen["regions"] if region["id"] == "signal")
    reception = signal_reception_state(
        signal["value_status"] if frame["status"] == "current" else "not_current",
        signal["text"],
    )
    for region in screen["regions"]:
        left = region["column"] * inner_width // 30
        right = (region["column"] + region["columns"]) * inner_width // 30
        top = region["row"] * inner_height // 20
        bottom = (region["row"] + region["rows"]) * inner_height // 20
        size = right - left
        foreground, background = "cbd5e1", "18212d"
        indicator = present_indicator(
            region["id"],
            region["token"],
            region["selection"],
            region["value_status"] if frame["status"] == "current" else "not_current",
            region["text"],
            reception,
        )
        pair = region["stored_color"]
        if screen["color_mode"] == "COLOR" and pair is not None:
            foreground, background = pair["text"], pair["background"]
            hold_key = "site_hold" if region["token"] == "SiteName" else region["id"] + "_hold"
            reverse = region["reverse_colors"] and (
                region["id"] != "function" or (indicator is not None and indicator.state == "on")
            )
            if reverse or frame["indicators"].get(hold_key) is True:
                foreground, background = background, foreground
        if indicator is not None:
            foreground = indicator.foreground or foreground
            background = indicator.background or background
        style = Style(
            color=f"#{foreground}", bgcolor=f"#{background}", bold=region["kind"] == "name"
        )
        value = (
            ""
            if region["value_status"] in ("empty", "blank") or region["kind"] == "spacer"
            else _value(region)
        )
        if indicator is not None:
            value = indicator.text
            if region["id"] == "signal" and indicator.state.startswith("level_") and size < 5:
                # Never clip five reported bars into a false lower level.
                value = "S" + indicator.state.removeprefix("level_")
        line_count = region["name_lines"] if region["kind"] == "name" else 1
        lines = list(
            Text(value, style=style).wrap(
                console, size, overflow="ellipsis", no_wrap=line_count == 1
            )
        )
        if len(lines) > line_count:
            lines = lines[:line_count]
            lines[-1].truncate(max(0, size - 1), overflow="crop")
            lines[-1].append("…")
        offset = max(0, (bottom - top - len(lines)) // 2)
        for row in range(top, bottom):
            index = row - top - offset
            line = lines[index].copy() if 0 <= index < len(lines) else Text("", style=style)
            line.truncate(size, overflow="ellipsis")
            line.align(region["alignment"], size)
            content[row].append((left, line))
    led = _CONTRACT["leds"].get(frame["indicators"]["alert_led"], "3b4654")
    led_style = Style(bgcolor=f"#{led}")
    edge_style = led_style if treatment == "border" else Style(bgcolor="#000000")
    output = Text(" " * width, style=led_style, no_wrap=True, overflow="crop")
    for row_segments in content:
        output.append("\n")
        output.append(" " * edge_width, style=edge_style)
        column = 0
        for start, segment in sorted(row_segments, key=lambda item: item[0]):
            output.append(" " * max(0, start - column), style="on #000000")
            output.append(segment)
            column = start + segment.cell_len
        output.append(" " * max(0, inner_width - column), style="on #000000")
        output.append(" " * edge_width, style=edge_style)
    output.append("\n")
    output.append(" " * width, style=led_style)
    return output


class MimicRuntimeScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    MimicRuntimeScreen { background: #0c121c; padding: 1 2; }
    MimicRuntimeScreen #mimic-runtime-scroll { height: 1fr; border: round #71849c; }
    MimicRuntimeScreen #mimic-runtime { height: auto; padding: 1; }
    MimicRuntimeScreen Static { color: #edf4fc; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,x,question_mark", "back", "Back"),
        Binding("q", "app.quit", "Quit"),
        Binding("ctrl+p", "app.command_palette", "Commands"),
    ]

    def __init__(self, content: Callable[[], Text]):
        super().__init__()
        self._content = content
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static("Mimic-SDS runtime / help — Esc or X returns", markup=False)
        with VerticalScroll(id="mimic-runtime-scroll"):
            yield Static(id="mimic-runtime", markup=False)

    def on_mount(self) -> None:
        self._refresh()
        self._timer = self.set_interval(0.5, self._refresh)

    def _refresh(self) -> None:
        self.query_one("#mimic-runtime", Static).update(self._content())

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def action_back(self) -> None:
        self.dismiss()


class MimicScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    MimicScreen { background: #000000; color: #edf4fc; layout: vertical; }
    MimicScreen #mimic-header { height: 1; background: #173552; }
    MimicScreen #mimic-title { height: 1; width: 1fr; }
    MimicScreen #mimic-health { height: 2; color: #ffb4b4; }
    MimicScreen #mimic-health.current { color: #71e1c1; }
    MimicScreen #mimic-grid { height: 1fr; overflow: hidden; }
    MimicScreen #mimic-keys { height: 1; background: #173552; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("m,escape", "back", "Dashboard"),
        Binding("v", "layout", "Profile/Simple/Detail"),
        Binding("b", "led", "LED style"),
        Binding("x,question_mark", "runtime", "Runtime / help"),
        Binding("q", "app.quit", "Quit"),
        Binding("ctrl+p", "app.command_palette", "Commands"),
    ]

    def __init__(
        self,
        reader: DisplayFrameReader | SupplementalFrameReader,
        runtime: Callable[[], Text],
        now: Callable[[], datetime],
        front_panel_inventory: Mapping[str, object] | None = None,
    ):
        super().__init__()
        self.reader, self._runtime = reader, runtime
        self._now = now
        self._front_panel_inventory = render_front_panel_inventory_terminal(front_panel_inventory)
        self.style = "preferred"
        self.treatment = "strips"
        self._timer: Timer | None = None
        self._rendered_packet: Mapping[str, Any] | None = None
        self._rendered_geometry: tuple[int, int, str, str] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="mimic-header"):
            yield Static(
                Text(
                    f"sdsctl v{__version__} | Mimic-SDS (read-only)",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
                id="mimic-title",
                markup=False,
            )
            yield LocalHeaderClock(self._now)
        yield Static(id="mimic-health", markup=False)
        yield Static(id="mimic-grid", markup=False)
        yield Static(
            "M Back | V Layout | B LED | X Runtime | ? Help | Q Quit", id="mimic-keys", markup=False
        )

    def on_mount(self) -> None:
        self.reader.set_active(True)
        self._timer = self.set_interval(0.1, self._refresh)
        self.call_after_refresh(self._refresh)

    def on_screen_suspend(self) -> None:
        self.reader.set_active(False)
        self._refresh()

    def on_screen_resume(self) -> None:
        self.reader.set_active(True)
        if self.is_mounted:
            self._refresh()

    def on_unmount(self) -> None:
        self.reader.set_active(False)
        if self._timer is not None:
            self._timer.stop()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        packet, message = self.reader.view()
        health = self.query_one("#mimic-health", Static)
        grid = self.query_one("#mimic-grid", Static)
        if packet is None:
            self._rendered_packet = None
            self._rendered_geometry = None
            health.remove_class("current")
            health.update(message + f"\nLayout: {self.style} | LED: {self.treatment}")
            grid.update(Text("No current scanner values are shown."))
            return
        frame = packet["frames"][self.style]
        health.set_class(
            frame["status"] == "current"
            and not frame["profile_refresh_pending"]
            and frame["profile_status"] not in ("unavailable", "refresh_failed"),
            "current",
        )
        basis = (
            "profile (physical toggle unconfirmed)"
            if self.style == "preferred"
            else f"{self.style} (local choice)"
        )
        profile = "refresh pending" if frame["profile_refresh_pending"] else frame["profile_status"]
        health.update(
            Text(
                f"{_STATES[frame['status']]} | Layout: {basis}\n"
                f"Profile: {profile} | LED: {frame['indicators']['alert_led'] or 'unknown'}"
                f" / {self.treatment} | X Details",
                no_wrap=True,
                overflow="ellipsis",
            )
        )
        geometry = grid.size.width, grid.size.height, self.style, self.treatment
        if packet is not self._rendered_packet or geometry != self._rendered_geometry:
            grid.update(
                render_mimic_terminal(
                    frame, width=grid.size.width, height=grid.size.height, treatment=self.treatment
                )
            )
            self._rendered_packet, self._rendered_geometry = packet, geometry

    def action_back(self) -> None:
        self.dismiss()

    def action_layout(self) -> None:
        styles = ("preferred", "simple", "detail")
        self.style = styles[(styles.index(self.style) + 1) % 3]
        self._refresh()

    def action_led(self) -> None:
        self.treatment = "border" if self.treatment == "strips" else "strips"
        self._refresh()

    def runtime_text(self) -> Text:
        output = Text(
            "Local display keys\nM / Esc: return to standard TUI; Q: quit\n"
            "V: profile preference / Simple / Detail; B: LED strips / border\n"
            "X / ?: runtime and help; Ctrl+P: command palette\n"
            "These keys do not control the scanner. Use the standard TUI for controls.\n"
            "Color approximation depends on terminal capability; no font change.\n"
            "BLACK/WHITE transforms and icon glyphs remain unqualified.\n\n"
            "LED shows reported color only; no inferred blink timing.\n\n"
        )
        output.append(self._runtime())
        return output

    def action_runtime(self) -> None:
        # Palette actions must not stack a second runtime drawer on the first.
        if not any(isinstance(screen, MimicRuntimeScreen) for screen in self.app.screen_stack):
            packet, _, supplemental_details = self.reader.view_and_details()
            details = Text("Profile at drawer open — frame polling is paused while covered\n")
            if packet is not None:
                frame = packet["frames"][self.style]
                details.append(
                    f"Profile: {frame['profile_status']}"
                    f" | Refresh pending: {frame['profile_refresh_pending']}\n"
                    f"Source: {packet['source_status'] or 'unavailable'}\n"
                    f"Accepted revision: {frame['profile_revision'] or 'none'}\n"
                )
                if frame["screen"] is not None:
                    screen = frame["screen"]
                    counts: dict[str, int] = {}
                    for region in screen["regions"]:
                        status = region["value_status"]
                        counts[status] = counts.get(status, 0) + 1
                    details.append(f"Mode: {screen['mode']} | Color mode: {screen['color_mode']}\n")
                    details.append(
                        "Fields: "
                        + ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
                        + "\n"
                    )
            else:
                details.append("No current display metadata available.\n")
            details.append("\n")
            details.append(supplemental_details)
            self.app.push_screen(
                MimicRuntimeScreen(
                    lambda: details + self.runtime_text() + Text("\n") + self._front_panel_inventory
                )
            )
