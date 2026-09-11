"""UTC date/time presentation for the TUI header, not connection-uptime tracking."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Header, Static


class UtcHeaderClock(Static):
    """One-line aware UTC clock with no locale or host-timezone assumptions."""

    DEFAULT_CSS = """
    UtcHeaderClock {
        dock: right;
        width: 22;
        height: 1;
        content-align: center middle;
    }
    """

    def __init__(self, now: Callable[[], datetime]) -> None:
        super().__init__(markup=False)
        self._now = now

    def on_mount(self) -> None:
        self.set_interval(1, self.refresh, name="UTC header clock")

    def render(self) -> Text:
        try:
            value = self._now()
            # A naive value has no known UTC instant. Do not interpret it using
            # the machine's local timezone or fabricate a Z suffix for it.
            if not isinstance(value, datetime) or value.utcoffset() is None:
                return Text("UTC time unavailable")
            stamp = value.astimezone(UTC).isoformat(timespec="seconds")
            return Text(stamp.removesuffix("+00:00") + "Z")
        except Exception:
            # Clock-provider/overflow errors must not stop scanner rendering or
            # expose exception details on a sustained-operation display.
            return Text("UTC time unavailable")


class ScannerTuiHeader(Header):
    """Preserve Textual's title/icon behavior and replace its time-only clock."""

    DEFAULT_CSS = """
    ScannerTuiHeader HeaderClockSpace {
        display: none;
    }
    """

    def __init__(self, now: Callable[[], datetime]) -> None:
        super().__init__(show_clock=False)
        self._now = now

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield UtcHeaderClock(self._now)
