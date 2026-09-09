"""A persistent, observe-only waiting screen for a temporarily absent daemon."""

from __future__ import annotations

import math
import signal
from collections.abc import Callable
from threading import Event, current_thread, main_thread
from time import monotonic
from types import FrameType
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Footer, Header, Static

from . import __version__
from .managed_display import (
    MANAGED_DISPLAY_TEMPORARY_EXIT,
    managed_display_failure_status,
    report_managed_display_wait,
)

MANAGED_DISPLAY_RETRY_SECONDS = 15.0


class _ProbeFinished(Message):
    def __init__(self, error: Exception | None) -> None:
        super().__init__()
        self.error = error


class ManagedDisplayWaitingApp(App[bool | Exception]):
    """Probe one selected remote API, never scanner controls or alternate hosts."""

    TITLE = f"SDSCTL {__version__}"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
    ]
    CSS = """
    Screen { align: center middle; }
    #connection-wait {
        width: 90%; max-width: 76; height: auto;
        border: round $warning; padding: 1 2;
    }
    #wait-title { text-style: bold; color: $warning; margin-bottom: 1; }
    #wait-target { text-wrap: nowrap; text-overflow: ellipsis; }
    #wait-detail { margin-top: 1; }
    """

    def __init__(
        self,
        target: str,
        probe: Callable[[], None],
        *,
        retry_seconds: float = MANAGED_DISPLAY_RETRY_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__()
        if not math.isfinite(retry_seconds) or retry_seconds <= 0:
            raise ValueError("Managed display retry interval must be positive.")
        self._target = target
        self._probe = probe
        self._retry_seconds = retry_seconds
        self._clock = clock
        self._next_probe = clock() + retry_seconds
        self._probe_running = False
        self._stopping = Event()

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="connection-wait"):
            yield Static("Daemon disconnected", id="wait-title")
            yield Static(Text(f"Target: {self._target}"), id="wait-target")
            yield Static("", id="wait-status")
            yield Static(
                "Live scanner data is unavailable.\n"
                "The dashboard will return automatically when the daemon is ready.",
                id="wait-detail",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#connection-wait").border_title = "Connection"
        self._tick()
        self.set_interval(0.25, self._tick)

    def _tick(self) -> None:
        if self._stopping.is_set():
            return
        if not self._probe_running and self._clock() >= self._next_probe:
            self._probe_running = True
            self._check_connection()
        remaining = max(0, math.ceil(self._next_probe - self._clock()))
        status = (
            "Checking the daemon connection..."
            if self._probe_running
            else f"Retrying in {remaining} seconds."
        )
        self.query_one("#wait-status", Static).update(status)

    @work(thread=True, exit_on_error=False)
    def _check_connection(self) -> None:
        # One bounded transport call at a time. A late result after Q or service
        # stop must never reopen the dashboard or schedule another attempt.
        if self._stopping.is_set():
            return
        error: Exception | None = None
        try:
            self._probe()
        except Exception as caught:
            error = caught
        if not self._stopping.is_set():
            self.post_message(_ProbeFinished(error))

    def on__probe_finished(self, message: _ProbeFinished) -> None:
        if self._stopping.is_set():
            return
        self._probe_running = False
        if message.error is None:
            self._stopping.set()
            self.exit(True)
        elif managed_display_failure_status(message.error) != MANAGED_DISPLAY_TEMPORARY_EXIT:
            self._stopping.set()
            self.exit(message.error)
        else:
            report_managed_display_wait("retry", message.error)
            self._next_probe = self._clock() + self._retry_seconds
            self._tick()

    def request_stop(self) -> None:
        self._stopping.set()
        self.exit(False)

    async def action_quit(self) -> None:
        self.request_stop()

    def on_unmount(self) -> None:
        self._stopping.set()


def wait_for_managed_display(target: str, probe: Callable[[], None]) -> bool:
    """Return true after readiness, false after intentional quit; raise on refusal."""

    app = ManagedDisplayWaitingApp(target, probe)

    def stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        app.request_stop()

    previous = None
    install_signal = current_thread() is main_thread()
    if install_signal:
        previous = signal.signal(signal.SIGTERM, stop)
    try:
        result = app.run()
    finally:
        app._stopping.set()
        if install_signal:
            signal.signal(signal.SIGTERM, previous)
    if isinstance(result, Exception):
        raise result
    return result is True
