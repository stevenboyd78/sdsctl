"""Opt-in, bounded Mimic HTTP timing; never response content or request identity.

The middleware belongs INSIDE existing authentication/Ingress guards. Trace IDs
are correlation labels, not credentials. Missing log entries prove nothing:
budgets, a stripped header, an unfinished request or a failed log sink can omit
them. A body_sent entry means ASGI send returned, not that a browser received it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, Full, Queue

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TRACE_HEADER = b"x-sdsctl-mimic-trace"
_STATE_KEY = "sdsctl_mimic_http_trace"
_IDENTIFIER = re.compile(rb"[0-9a-f]{16}-[1-9][0-9]{0,5}\Z")
_MAX_TRACES = 512
_MIN_INTERVAL = 0.1
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Event:
    identifier: str
    stage: str
    utc: str
    elapsed_ms: int
    status: int
    sent_bytes: int


class _TraceSink:
    """Bounded, idle-expiring daemon worker: never perform log IO in ASGI."""

    def __init__(self) -> None:
        self._queue: Queue[_Event] = Queue(maxsize=128)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._dropped = 0

    def submit(self, event: _Event) -> bool:
        with self._lock:
            try:
                self._queue.put_nowait(event)
            except Full:
                self._dropped = min(999999, self._dropped + 1)
                return False
            if self._thread is None:
                worker = threading.Thread(target=self._run, name="mimic-http-timing", daemon=True)
                self._thread = worker
                try:
                    worker.start()
                except Exception:
                    self._thread = None
                    return False
        return True

    def _run(self) -> None:
        while True:
            try:
                event = self._queue.get(timeout=5)
            except Empty:
                with self._lock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            with self._lock:
                dropped = self._dropped
            try:
                # Explicit opt-in diagnostics must be visible at the App's
                # default WARNING threshold without enabling general debug logs.
                logger.warning(
                    "Mimic HTTP trace id=%s stage=%s utc=%s "
                    "elapsed_ms=%d status=%d bytes=%d dropped=%d",
                    event.identifier,
                    event.stage,
                    event.utc,
                    event.elapsed_ms,
                    event.status,
                    event.sent_bytes,
                    dropped,
                )
            except Exception:
                with self._lock:
                    self._dropped = min(999999, self._dropped + 1)
            finally:
                self._queue.task_done()


@dataclass(slots=True)
class _Trace:
    identifier: str
    started: float
    clock: Callable[[], float]
    sink: _TraceSink
    status: int = 0
    sent_bytes: int = 0

    def emit(self, stage: str) -> bool:
        # Diagnostics never replace an HTTP result, including logger failure.
        try:
            return self.sink.submit(
                _Event(
                    self.identifier,
                    stage,
                    datetime.now(UTC).isoformat(timespec="milliseconds"),
                    min(300000, max(0, round((self.clock() - self.started) * 1000))),
                    self.status,
                    self.sent_bytes,
                )
            )
        except Exception:
            return False


def mark_display_handler(scope: Scope) -> None:
    """Mark entry to the synchronous route, after any worker-pool wait."""
    trace = scope.get("state", {}).get(_STATE_KEY)
    if isinstance(trace, _Trace):
        trace.emit("handler")


class MimicRequestTimingMiddleware:
    """At most 512 admitted requests per App process, no new routes/authority."""

    def __init__(self, app: ASGIApp, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._app = app
        self._clock = clock
        self._sink = _TraceSink()
        self._remaining = _MAX_TRACES
        self._next = float("-inf")
        self._lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        identifier = None
        if (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/api/v1/display-frame"
            and not scope.get("query_string")
        ):
            labels = [
                value for key, value in scope.get("headers", ()) if key.lower() == TRACE_HEADER
            ]
            if len(labels) == 1 and len(labels[0]) <= 23 and _IDENTIFIER.fullmatch(labels[0]):
                identifier = labels[0].decode("ascii")
        trace = None
        if identifier is not None:
            now = self._clock()
            with self._lock:
                if self._remaining > 0 and now >= self._next:
                    self._remaining -= 1
                    self._next = now + _MIN_INTERVAL
                    trace = _Trace(identifier, now, self._clock, self._sink)
            if trace is not None and not trace.emit("received"):
                trace = None
        if trace is None:
            await self._app(scope, receive, send)
            return

        scope = {**scope, "state": {**scope.get("state", {}), _STATE_KEY: trace}}
        body_sent = False

        async def traced_send(message: Message) -> None:
            nonlocal body_sent
            if message["type"] == "http.response.start":
                trace.status = int(message["status"])
                message = {**message, "headers": list(message.get("headers", ()))}
                MutableHeaders(raw=message["headers"])[TRACE_HEADER.decode()] = trace.identifier
            await send(message)
            if message["type"] == "http.response.start":
                trace.emit("headers_sent")
            elif message["type"] == "http.response.body":
                trace.sent_bytes = min(2147483647, trace.sent_bytes + len(message.get("body", b"")))
                if not message.get("more_body", False):
                    body_sent = True
                    trace.emit("body_sent")

        try:
            await self._app(scope, receive, traced_send)
        except BaseException:
            trace.emit("interrupted")
            raise
        finally:
            if not body_sent:
                with suppress(Exception):
                    trace.emit("incomplete")
