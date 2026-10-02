"""Read-only Textual waterfall view over the existing daemon data plane."""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import ClassVar, Protocol

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.events import Resize
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Static

from .daemon_waterfall_protocol import (
    DaemonWaterfallRecord,
    DaemonWaterfallRecordKind,
)
from .terminal_text import bounded_terminal_value

logger = logging.getLogger(__name__)

TUI_WATERFALL_HISTORY_LIMIT = 256
TUI_WATERFALL_RECONNECT_DELAY = 0.5
TUI_WATERFALL_JOIN_TIMEOUT = 2.0
_WATERFALL_BIN_COUNT = 240
_MAX_SAFE_INTEGER = (1 << 53) - 1
_HEX_VALUE = re.compile(r"^[0-9a-fA-F]+$")
_GRADIENT = " .:-=+*#%@"
_LOW_INTENSITY_CEILING = 1.0 / 3.0
_MID_INTENSITY_CEILING = 2.0 / 3.0
_DARK_INTENSITY_STYLES = ("#5fd75f", "#ffaf00", "bold #ff5f5f")
_LIGHT_INTENSITY_STYLES = ("#15803d", "#b45309", "bold #b91c1c")


class TuiWaterfallClient(Protocol):
    """Minimum validated daemon waterfall client used by the TUI."""

    def receive(self) -> DaemonWaterfallRecord:
        """Receive one validated, ordered daemon waterfall record."""

    def close(self) -> None:
        """Close the stream and release its daemon-side consumer lease."""


TuiWaterfallClientFactory = Callable[[], TuiWaterfallClient]


@dataclass(frozen=True, slots=True)
class TuiWaterfallSnapshot:
    """One immutable renderer-neutral view of current relative waterfall state."""

    status: str
    paused: bool
    sequence: int | None
    frames_received: int
    responses_dropped: int
    overflows: int
    reconnects: int
    history: tuple[tuple[float, ...], ...]
    latest: tuple[float, ...] | None
    session: Mapping[str, object]
    source_received_at: datetime | None


def normalize_waterfall_values(values: object) -> tuple[float, ...]:
    """Normalize exactly 240 hexadecimal relative bins without inventing units."""

    if not isinstance(values, (list, tuple)) or len(values) != _WATERFALL_BIN_COUNT:
        raise ValueError("Waterfall frame must contain exactly 240 values.")
    numeric: list[int] = []
    for value in values:
        if not isinstance(value, str) or _HEX_VALUE.fullmatch(value) is None:
            raise ValueError("Waterfall frame contains an invalid hexadecimal value.")
        parsed = int(value, 16)
        if parsed > _MAX_SAFE_INTEGER:
            raise ValueError("Waterfall frame contains an invalid value.")
        numeric.append(parsed)
    minimum = min(numeric)
    maximum = max(numeric)
    span = maximum - minimum
    if span == 0:
        return (0.5,) * _WATERFALL_BIN_COUNT
    return tuple((value - minimum) / span for value in numeric)


class TuiWaterfallModel:
    """Thread-safe bounded relative history with local pause and clear controls."""

    def __init__(self, *, history_limit: int = TUI_WATERFALL_HISTORY_LIMIT) -> None:
        if type(history_limit) is not int:
            raise TypeError("TUI waterfall history limit must be an integer.")
        if history_limit <= 0:
            raise ValueError("TUI waterfall history limit must be greater than zero.")
        self._lock = threading.RLock()
        self._history: deque[tuple[float, ...]] = deque(maxlen=history_limit)
        self._status = "Inactive"
        self._paused = False
        self._sequence: int | None = None
        self._frames_received = 0
        self._responses_dropped = 0
        self._overflows = 0
        self._reconnects = 0
        self._connection_attempts = 0
        self._latest: tuple[float, ...] | None = None
        self._session: dict[str, object] = {}
        self._source_received_at: datetime | None = None

    def begin_connection(self) -> None:
        """Clear unconfirmed values before acquiring a fresh stream."""

        with self._lock:
            if self._connection_attempts:
                self._reconnects += 1
            self._connection_attempts += 1
            self._status = "Connecting"
            self._sequence = None
            self._history.clear()
            self._latest = None
            self._session.clear()
            self._source_received_at = None
            self._responses_dropped = 0
            self._overflows = 0

    def mark_unavailable(self) -> None:
        """Fail closed after a stream error without retaining stale values."""

        with self._lock:
            self._status = "Unavailable — reconnecting"
            self._sequence = None
            self._history.clear()
            self._latest = None
            self._session.clear()
            self._source_received_at = None
            self._responses_dropped = 0
            self._overflows = 0

    def mark_invalid(self) -> None:
        """Stop on an invalid renderer payload without retrying around it."""

        with self._lock:
            self._status = "Invalid stream — closed"
            self._sequence = None
            self._history.clear()
            self._latest = None
            self._session.clear()
            self._source_received_at = None
            self._responses_dropped = 0
            self._overflows = 0

    def mark_stopped(self) -> None:
        with self._lock:
            self._status = "Stopped"

    def toggle_paused(self) -> bool:
        with self._lock:
            self._paused = not self._paused
            return self._paused

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._latest = None

    def apply(self, record: DaemonWaterfallRecord) -> None:
        """Apply one validated envelope and strictly validate renderer payloads."""

        if not isinstance(record, DaemonWaterfallRecord):
            raise TypeError("TUI waterfall records must be DaemonWaterfallRecord values.")
        with self._lock:
            if self._sequence is None:
                if record.kind is not DaemonWaterfallRecordKind.SESSION_CHECKPOINT:
                    raise ValueError("TUI waterfall stream must begin with a checkpoint.")
            elif record.sequence != self._sequence + 1:
                raise ValueError("TUI waterfall record sequence is not contiguous.")
            self._sequence = record.sequence

            if record.kind is DaemonWaterfallRecordKind.SESSION_CHECKPOINT:
                self._session = _mapping_copy(record.payload, "session checkpoint")
                self._status = "Live"
                return
            if record.kind is DaemonWaterfallRecordKind.SESSION_TRANSITION:
                payload = _mapping_copy(record.payload, "session transition")
                self._session = _mapping_copy(payload.get("snapshot"), "session snapshot")
                self._status = "Live"
                return
            if record.kind is DaemonWaterfallRecordKind.PWF:
                session = record.payload.get("session")
                if session is not None:
                    self._session = _mapping_copy(session, "session snapshot")
                self._status = "Live"
                return
            if record.kind is not DaemonWaterfallRecordKind.GWF:
                raise ValueError("TUI waterfall record kind is unsupported.")

            payload = _mapping_copy(record.payload, "GWF payload")
            frame = normalize_waterfall_values(payload.get("values"))
            dropped = _nonnegative_integer(payload.get("responses_dropped"), "responses dropped")
            overflows = _nonnegative_integer(payload.get("overflows"), "overflows")
            received_at = _aware_datetime(payload.get("source_received_at"), "source timestamp")
            session = payload.get("session")
            if session is not None:
                self._session = _mapping_copy(session, "session snapshot")
            self._frames_received += 1
            self._responses_dropped = dropped
            self._overflows = overflows
            self._source_received_at = received_at
            self._status = "Live"
            if not self._paused:
                self._latest = frame
                self._history.append(frame)

    def snapshot(self) -> TuiWaterfallSnapshot:
        with self._lock:
            return TuiWaterfallSnapshot(
                status=self._status,
                paused=self._paused,
                sequence=self._sequence,
                frames_received=self._frames_received,
                responses_dropped=self._responses_dropped,
                overflows=self._overflows,
                reconnects=self._reconnects,
                history=tuple(self._history),
                latest=self._latest,
                session=dict(self._session),
                source_received_at=self._source_received_at,
            )


class TuiWaterfallReader:
    """Own one background daemon consumer only while its screen is mounted."""

    def __init__(
        self,
        factory: TuiWaterfallClientFactory,
        model: TuiWaterfallModel,
        *,
        reconnect_delay: float = TUI_WATERFALL_RECONNECT_DELAY,
        join_timeout: float = TUI_WATERFALL_JOIN_TIMEOUT,
    ) -> None:
        if not callable(factory):
            raise TypeError("TUI waterfall client factory must be callable.")
        if reconnect_delay <= 0:
            raise ValueError("TUI waterfall reconnect delay must be greater than zero.")
        if join_timeout <= 0:
            raise ValueError("TUI waterfall join timeout must be greater than zero.")
        self._factory = factory
        self._model = model
        self._reconnect_delay = reconnect_delay
        self._join_timeout = join_timeout
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._client: TuiWaterfallClient | None = None
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="sds200-tui-waterfall",
                daemon=False,
            )
            self._thread.start()

    def close(self, *, wait: bool = True) -> bool:
        self._stop.set()
        with self._lock:
            client = self._client
            thread = self._thread
        if client is not None:
            client.close()
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(self._join_timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._lock:
                if self._thread is thread:
                    self._thread = None
        return stopped

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._model.begin_connection()
                client: TuiWaterfallClient | None = None
                try:
                    client = self._factory()
                    with self._lock:
                        self._client = client
                    while not self._stop.is_set():
                        record = client.receive()
                        try:
                            self._model.apply(record)
                        except (TypeError, ValueError) as error:
                            logger.error(
                                "TUI waterfall payload rejected error_type=%s",
                                error.__class__.__name__,
                            )
                            self._model.mark_invalid()
                            return
                except Exception as error:
                    if not self._stop.is_set():
                        logger.warning(
                            "TUI waterfall stream unavailable error_type=%s",
                            error.__class__.__name__,
                        )
                        self._model.mark_unavailable()
                finally:
                    if client is not None:
                        client.close()
                    with self._lock:
                        if self._client is client:
                            self._client = None
                if self._stop.wait(self._reconnect_delay):
                    break
        finally:
            if self._stop.is_set():
                self._model.mark_stopped()


class WaterfallScreen(ModalScreen[None]):
    """Responsive read-only relative waterfall supplied by a daemon lease."""

    DEFAULT_CSS = """
    WaterfallScreen { padding: 0 1; layout: vertical; }
    WaterfallScreen #waterfall-title,
    WaterfallScreen #waterfall-health,
    WaterfallScreen #waterfall-spectrum,
    WaterfallScreen #waterfall-history,
    WaterfallScreen #waterfall-scale,
    WaterfallScreen #waterfall-keys {
        text-wrap: nowrap;
        text-overflow: ellipsis;
        overflow: hidden;
    }
    WaterfallScreen #waterfall-title { height: 1; }
    WaterfallScreen #waterfall-health { height: 3; }
    WaterfallScreen #waterfall-spectrum { height: 3; border: round; padding: 0 1; }
    WaterfallScreen #waterfall-history { height: 1fr; border: round; padding: 0 1; }
    WaterfallScreen #waterfall-scale { height: 2; }
    WaterfallScreen #waterfall-keys { height: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("w,escape", "back", "Dashboard"),
        Binding("space", "pause", "Pause / resume"),
        Binding("c", "clear", "Clear history"),
        Binding("q", "app.quit", "Quit"),
        Binding("ctrl+p", "app.command_palette", "Commands"),
    ]

    def __init__(
        self,
        factory: TuiWaterfallClientFactory,
        *,
        screen_class: str | None = None,
        history_limit: int = TUI_WATERFALL_HISTORY_LIMIT,
    ) -> None:
        super().__init__()
        self.model = TuiWaterfallModel(history_limit=history_limit)
        self.reader = TuiWaterfallReader(factory, self.model)
        self._timer: Timer | None = None
        self._rendered: TuiWaterfallSnapshot | None = None
        self._rendered_size: tuple[int, int] | None = None
        if screen_class is not None:
            self.add_class(screen_class)

    def compose(self) -> ComposeResult:
        yield Static(
            _waterfall_title(light=self.has_class("light")),
            id="waterfall-title",
            markup=False,
        )
        yield Static(id="waterfall-health", markup=False)
        yield Static(id="waterfall-spectrum", markup=False)
        yield Static(id="waterfall-history", markup=False)
        yield Static(id="waterfall-scale", markup=False)
        yield Static(
            "W/Esc Back | Space Pause/Resume | C Clear local history | Q Quit",
            id="waterfall-keys",
            markup=False,
        )

    def on_mount(self) -> None:
        self.reader.start()
        self._timer = self.set_interval(0.1, self._refresh)
        self.call_after_refresh(self._refresh)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        if not self.reader.close(wait=True):
            logger.error("TUI waterfall reader did not stop before its bounded deadline.")

    def on_resize(self, event: Resize) -> None:
        del event
        self.call_after_refresh(lambda: self._refresh(force=True))

    def action_back(self) -> None:
        self.dismiss()

    def action_pause(self) -> None:
        self.model.toggle_paused()
        self._refresh(force=True)

    def action_clear(self) -> None:
        self.model.clear()
        self._refresh(force=True)

    def _refresh(self, *, force: bool = False) -> None:
        if not self.is_mounted:
            return
        snapshot = self.model.snapshot()
        size = (self.screen.size.width, self.screen.size.height)
        if not force and snapshot == self._rendered and size == self._rendered_size:
            return
        self._rendered = snapshot
        self._rendered_size = size

        health = self.query_one("#waterfall-health", Static)
        health.update(
            f"{snapshot.status}{' — PAUSED locally' if snapshot.paused else ''}\n"
            f"Frames: {snapshot.frames_received} | Sequence: "
            f"{snapshot.sequence if snapshot.sequence is not None else '—'} | "
            f"Queue loss: {snapshot.responses_dropped} | Overflows: {snapshot.overflows} | "
            f"Reconnects: {snapshot.reconnects}"
        )

        spectrum = self.query_one("#waterfall-spectrum", Static)
        history = self.query_one("#waterfall-history", Static)
        width = max(1, history.content_size.width - 2)
        visible_rows = max(1, history.content_size.height - 2)
        spectrum_text = Text("Latest: ")
        if snapshot.latest is None:
            spectrum_text.append("No current frame")
        else:
            spectrum_text.append(
                _render_relative_row(
                    snapshot.latest,
                    max(1, width - len("Latest: ")),
                    light=self.has_class("light"),
                )
            )
        spectrum.update(spectrum_text)
        rows = snapshot.history[-visible_rows:]
        if rows:
            rendered_history = Text()
            for index, row in enumerate(rows):
                if index:
                    rendered_history.append("\n")
                rendered_history.append(
                    _render_relative_row(row, width, light=self.has_class("light"))
                )
            history.update(rendered_history)
        else:
            history.update(Text("Waiting for relative GWF frames…"))
        self.query_one("#waterfall-scale", Static).update(_scale_text(snapshot))


def _intensity_styles(*, light: bool) -> tuple[str, str, str]:
    return _LIGHT_INTENSITY_STYLES if light else _DARK_INTENSITY_STYLES


def _waterfall_title(*, light: bool = False) -> Text:
    low_style, mid_style, high_style = _intensity_styles(light=light)
    title = Text("Relative waterfall — daemon stream | ")
    title.append("LOW", style=low_style)
    title.append(" ")
    title.append("MID", style=mid_style)
    title.append(" ")
    title.append("HIGH", style=high_style)
    title.append(" (relative per frame; uncalibrated)")
    return title


def _relative_intensity_style(level: float, *, light: bool) -> str:
    low_style, mid_style, high_style = _intensity_styles(light=light)
    if level < _LOW_INTENSITY_CEILING:
        return low_style
    if level < _MID_INTENSITY_CEILING:
        return mid_style
    return high_style


def _render_relative_row(
    values: Sequence[float],
    width: int,
    *,
    light: bool = False,
) -> Text:
    rendered = Text(no_wrap=True, overflow="crop")
    if width <= 0:
        return rendered
    if not values:
        rendered.append(" " * width)
        return rendered
    count = len(values)
    run: list[str] = []
    run_style: str | None = None
    for column in range(width):
        start = column * count // width
        stop = max(start + 1, (column + 1) * count // width)
        level = sum(values[start:stop]) / (stop - start)
        index = min(len(_GRADIENT) - 1, max(0, round(level * (len(_GRADIENT) - 1))))
        style = _relative_intensity_style(level, light=light)
        if run_style is not None and style != run_style:
            rendered.append("".join(run), style=run_style)
            run.clear()
        run.append(_GRADIENT[index])
        run_style = style
    if run_style is not None:
        rendered.append("".join(run), style=run_style)
    return rendered


def _scale_text(
    snapshot: TuiWaterfallSnapshot,
    *,
    local_timezone: tzinfo | None = None,
) -> str:
    session_state = _session_value(snapshot.session, "state")
    waterfall_status = snapshot.session.get("waterfall_status")
    status = waterfall_status if isinstance(waterfall_status, Mapping) else {}
    lower = _session_value(status, "lower_frequency")
    center = _session_value(status, "center_frequency")
    upper = _session_value(status, "upper_frequency")
    received = (
        snapshot.source_received_at.astimezone(local_timezone).isoformat()
        if snapshot.source_received_at is not None
        else "Unavailable"
    )
    return (
        f"Scanner span (raw): lower {lower} | center {center} | upper {upper} | "
        f"Session: {session_state}\n"
        f"Source local timestamp: {received}"
    )


def _session_value(source: Mapping[str, object], key: str) -> str:
    value = source.get(key)
    return bounded_terminal_value(
        value if isinstance(value, str) else None,
        unavailable="Unavailable",
    )


def _mapping_copy(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Waterfall {label} is invalid.")
    return dict(value)


def _nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Waterfall {label} is invalid.")
    return value


def _aware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Waterfall {label} is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Waterfall {label} is invalid.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Waterfall {label} is invalid.")
    return parsed


__all__ = [
    "TUI_WATERFALL_HISTORY_LIMIT",
    "TuiWaterfallClient",
    "TuiWaterfallClientFactory",
    "TuiWaterfallModel",
    "TuiWaterfallReader",
    "TuiWaterfallSnapshot",
    "WaterfallScreen",
    "normalize_waterfall_values",
]
