"""Read-only scanner telemetry drawer; no command or observation owner."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .state import RadioStateSnapshot


def _literal(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        return "Unavailable"
    # Bound presentation only. Preserve prefixes, zeroes and ordinary Unicode;
    # terminal controls, bidi formatting and line breaks cannot create rows.
    value = "".join(
        "?" if unicodedata.category(char).startswith("C") or char in "\u2028\u2029" else char
        for char in value[:256]
    )
    text = Text(value)
    text.truncate(128, overflow="ellipsis")
    return text.plain


def scanner_details(
    snapshot: RadioStateSnapshot,
    *,
    connected: bool | None,
    current: bool,
    stale: bool,
    degraded: bool,
) -> Text:
    """Present existing shared fields literally, clearing unconfirmed values."""
    available = connected is True and current and not stale and not degraded
    if connected is False:
        health = "Disconnected — values cleared"
    elif connected is not True:
        health = "Connection status unavailable — values cleared"
    elif stale:
        health = "Stale scanner data — values cleared"
    elif degraded:
        health = "Degraded connection — values cleared"
    elif not current:
        health = "Waiting for new scanner data — values cleared"
    else:
        health = "Current scanner data"
    battery = snapshot.battery
    battery_text = (
        f"{battery:g}"
        if isinstance(battery, (int, float))
        and not isinstance(battery, bool)
        and math.isfinite(battery)
        else "Unavailable"
    )
    rows = [
        ("Mode", _literal(snapshot.mode)),
        ("Channel", _literal(snapshot.channel)),
        ("Talkgroup ID", _literal(snapshot.talkgroup_id)),
        ("Unit ID", _literal(snapshot.unit_id)),
        ("P25 status (reported)", _literal(snapshot.p25_status)),
        ("Battery (raw)", battery_text),
    ]
    content = Text(health + "\n\n")
    for label, value in rows:
        content.append(label + ": ", style="bold")
        content.append((value if available else "Unavailable") + "\n")
    content.append(
        "\nRead-only shared scanner telemetry. Missing fields are unavailable. "
        "Battery units and P25 status meanings are not inferred."
    )
    return content


class ScannerDetailsScreen(ModalScreen[None]):
    """An inspectable view that leaves the ordinary dashboard geometry intact."""

    DEFAULT_CSS = """
    ScannerDetailsScreen { padding: 1 2; }
    ScannerDetailsScreen #scanner-details-title { height: auto; margin-bottom: 1; }
    ScannerDetailsScreen #scanner-details-scroll { height: 1fr; border: round; }
    ScannerDetailsScreen #scanner-details-content { height: auto; padding: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,x", "back", "Back"),
        Binding("q", "app.quit", "Quit"),
        Binding("ctrl+p", "app.command_palette", "Commands"),
    ]

    def __init__(self, content: Callable[[], Text], *, screen_class: str | None):
        super().__init__()
        self._content = content
        if screen_class is not None:
            self.add_class(screen_class)

    def compose(self) -> ComposeResult:
        yield Static("Scanner details — Esc or X returns", id="scanner-details-title", markup=False)
        with VerticalScroll(id="scanner-details-scroll"):
            yield Static(self._content(), id="scanner-details-content", markup=False)

    def on_mount(self) -> None:
        self.update_details(self._content())
        self.query_one("#scanner-details-scroll", VerticalScroll).focus()

    def update_details(self, content: Text) -> None:
        self.query_one("#scanner-details-content", Static).update(content)

    def action_back(self) -> None:
        self.dismiss()
