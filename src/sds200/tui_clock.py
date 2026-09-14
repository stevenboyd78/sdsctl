"""Local RFC 2822 date/time presentation, not connection-uptime tracking."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from email.utils import format_datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Header, Static


def local_timestamp(now: Callable[[], datetime]) -> str:
    """Format one aware observation; never guess a timezone or expose an error."""
    try:
        value = now()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            return "Local time unavailable"
        # Convert each instant with the host's timezone rules, including DST.
        # format_datetime uses English weekday/month names independent of locale.
        return format_datetime(value.astimezone())
    except Exception:
        return "Local time unavailable"


class LocalHeaderClock(Static):
    """One-line local clock with an explicit numeric timezone offset."""

    DEFAULT_CSS = """
    LocalHeaderClock {
        dock: right;
        width: 33;
        height: 1;
        content-align: center middle;
    }
    """

    def __init__(self, now: Callable[[], datetime]) -> None:
        super().__init__(markup=False)
        self._now = now

    def on_mount(self) -> None:
        self.set_interval(1, self.refresh, name="Local header clock")

    def render(self) -> Text:
        return Text(local_timestamp(self._now))


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
        yield LocalHeaderClock(self._now)
