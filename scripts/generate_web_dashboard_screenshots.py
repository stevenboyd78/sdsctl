#!/usr/bin/env python3
"""Generate deterministic documentation screenshots of the web dashboard."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from struct import unpack
from typing import Self
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sds200.daemon_waterfall_protocol import (
    DaemonWaterfallRecord,
    DaemonWaterfallRecordKind,
)
from sds200.exceptions import DaemonDisconnectedError
from sds200.web_dashboard import create_web_dashboard_app

_THEME_STORAGE_KEY = "sdsctl.web.theme"
_DEMO_URL_PREFIX = "/__demo/prefix"
_DEMO_CLOCK_PATH = "/__demo/fixed-clock.js"
_DEMO_CLOCK_SCRIPT_TAG = '  <script src="__demo/fixed-clock.js"></script>\n'
_THEME_BOOTSTRAP_SCRIPT_TAG = '  <script src="assets/theme-bootstrap.js"></script>\n'
_DEMO_CLOCK_JAVASCRIPT = """\
"use strict";
(() => {
  const NativeDate = globalThis.Date;
  const fixedTimestamp = NativeDate.parse("2026-08-08T21:12:00.000Z");
  const nativeToLocaleString = NativeDate.prototype.toLocaleString;

  function DemoDate(...args) {
    if (new.target === undefined) {
      return new NativeDate(fixedTimestamp).toString();
    }
    return Reflect.construct(
      NativeDate,
      args.length === 0 ? [fixedTimestamp] : args,
      new.target,
    );
  }

  Object.setPrototypeOf(DemoDate, NativeDate);
  DemoDate.prototype = NativeDate.prototype;
  Object.defineProperty(DemoDate, "now", {
    configurable: true,
    value: () => fixedTimestamp,
  });
  Object.defineProperty(NativeDate.prototype, "toLocaleString", {
    configurable: true,
    writable: true,
    value(locales, options) {
      if (locales !== undefined || options !== undefined) {
        return nativeToLocaleString.call(this, locales, options);
      }
      return nativeToLocaleString.call(this, "en-US", {
        timeZone: "America/Denver",
        year: "numeric",
        month: "numeric",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      });
    },
  });
  Object.defineProperty(globalThis, "Date", {
    configurable: true,
    writable: true,
    value: DemoDate,
  });
})();
"""
_THEMES = (
    "system",
    "lcars",
    "matrix",
    "first-responder",
    "amateur-radio",
    "pip-boy-inspired",
)
_DEMO_THEME_SETUP_HTML = (
    "<!doctype html>\n"
    '<html lang="en">\n'
    "<head>\n"
    '  <meta charset="utf-8">\n'
    "  <title>sdsctl web screenshot setup</title>\n"
    "</head>\n"
    "<body>\n"
    "<script>\n"
    f"const allowedThemes = Object.freeze({json.dumps(_THEMES)});\n"
    'const theme = location.pathname.split("/").at(-1);\n'
    "if (allowedThemes.includes(theme)) {\n"
    f"  localStorage.setItem({json.dumps(_THEME_STORAGE_KEY)}, theme);\n"
    "}\n"
    'location.replace("/");\n'
    "</script>\n"
    "</body>\n"
    "</html>\n"
)
_CONTROL_OPERATIONS = (
    "scanner.hold_state",
    "scanner.next",
    "scanner.previous",
    "scanner.reconnect",
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_CAPTURE_HELPER = Path("scripts/capture_web_dashboard_screenshot.mjs")
_GALLERY_DIRECTORY = Path("docs/assets/web-dashboard")
_GALLERY_GUIDE = Path("docs/web-dashboard.md")
_GALLERY_WIKI = Path("wiki/Web-Dashboard.md")
_WIKI_RAW_IMAGE_PREFIX = (
    "https://raw.githubusercontent.com/stevenboyd78/sdsctl/main/docs/assets/web-dashboard/"
)
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_PNG_FILE_BYTES = 64 * 1024 * 1024
_MAX_PNG_DECODED_BYTES = 128 * 1024 * 1024
_MAX_REQUIRED_TEXT_BYTES = 4 * 1024 * 1024
_MAX_SDIST_MEMBERS = 10_000
_READY_TEXT = {
    "dashboard-message": "Daemon and scanner status are available.",
    "status-badge": "Connected",
    "daemon-state": "running",
    "scanner-model": "SDS200",
    "radio-system": "Demo Metro Public Safety",
    "recording-status": "recording",
    "recordings-message": "7 recent of 12 finalized recordings.",
    "recordings-page-status": "Page 1 of 3",
    "last-update": "8/8/2026, 3:12:00 PM",
}
_READY_LAST_UPDATE = "2026-08-08T21:12:00.000Z"


@dataclass(frozen=True, slots=True)
class Capture:
    filename: str
    theme: str
    width: int
    height: int
    device_scale_factor: int = 1


CAPTURES = (
    Capture("theme-system-1920x1080.png", "system", 1920, 1080),
    Capture("theme-system-390x844-dpr2.png", "system", 390, 844, 2),
    Capture("theme-lcars-1920x1080.png", "lcars", 1920, 1080),
    Capture("theme-matrix-1920x1080.png", "matrix", 1920, 1080),
    Capture(
        "theme-first-responder-1920x1080.png",
        "first-responder",
        1920,
        1080,
    ),
    Capture(
        "theme-amateur-radio-1920x1080.png",
        "amateur-radio",
        1920,
        1080,
    ),
    Capture(
        "theme-amateur-radio-1366x768.png",
        "amateur-radio",
        1366,
        768,
    ),
    Capture(
        "theme-pip-boy-inspired-1920x1080.png",
        "pip-boy-inspired",
        1920,
        1080,
    ),
    Capture(
        "theme-pip-boy-inspired-800x480.png",
        "pip-boy-inspired",
        800,
        480,
    ),
)


class _CaptureDomParser(HTMLParser):
    """Collect the authoritative demo values required before a capture."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.theme: str | None = None
        self.status_state: str | None = None
        self.attributes: dict[str, dict[str, str]] = {}
        self.text: dict[str, list[str]] = {element_id: [] for element_id in _READY_TEXT}
        self._stack: list[tuple[str, str | None]] = []

    def handle_starttag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        values = {name: value or "" for name, value in attributes}
        if tag == "html":
            self.theme = values.get("data-theme")

        element_id = values.get("id")
        target = element_id if element_id in self.text else None
        if element_id is not None:
            self.attributes[element_id] = values
        if element_id == "status-badge":
            self.status_state = values.get("data-state")
        self._stack.append((tag, target))

    def handle_endtag(self, tag: str) -> None:
        while self._stack:
            open_tag, _target = self._stack.pop()
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        for _tag, target in reversed(self._stack):
            if target is not None:
                self.text[target].append(data)
                return


def _capture_dom_is_ready(output: str | bytes, capture: Capture) -> bool:
    """Return whether Chrome serialized the fully rendered demo state."""

    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    parser = _CaptureDomParser()
    try:
        parser.feed(output)
        parser.close()
    except Exception:
        return False

    if parser.theme != capture.theme or parser.status_state != "online":
        return False
    for element_id, expected in _READY_TEXT.items():
        observed = " ".join("".join(parser.text[element_id]).split())
        if observed != expected:
            return False
    return parser.attributes.get("last-update", {}).get("datetime") == _READY_LAST_UPDATE


DEMO_HELLO: Mapping[str, object] = {
    "protocol": "sdsctl.daemon",
    "selected_version": 1,
    "read_only": False,
    "control_operations": list(_CONTROL_OPERATIONS),
}


DEMO_RECORDING: Mapping[str, object] = {
    "status": "recording",
    "active": True,
    "closed": False,
    "elapsed_seconds": 192.4,
    "packets": 9632,
    "samples": 1541120,
    "audio_duration_seconds": 192.64,
    "recording": "demo/live.wav",
    "reliability": {
        "packets_lost": 2,
        "duplicate_packets": 1,
        "late_packets": 3,
        "malformed_packets": 0,
        "unexpected_source_packets": 0,
        "timestamp_discontinuities": 1,
    },
}


DEMO_RECORDINGS: Mapping[str, object] = {
    "limit": 50,
    "total_entries": 12,
    "summary": {"managed_units": 12},
    "issues": [],
    "entries": [
        {
            "audio": "2026/demo/Metro_Dispatch_1512.wav",
            "recorded_at": "2026-08-08T15:12:18-06:00",
            "duration_seconds": 148.7,
            "audio_size_bytes": 1190044,
            "playable": True,
        },
        {
            "audio": "2026/demo/County_Fireground_1507.wav",
            "recorded_at": "2026-08-08T15:07:42-06:00",
            "duration_seconds": 83.2,
            "audio_size_bytes": 666040,
            "playable": True,
        },
        {
            "audio": "2026/demo/Airport_Ops_1458.wav",
            "recorded_at": "2026-08-08T14:58:09-06:00",
            "duration_seconds": 61.5,
            "audio_size_bytes": 492036,
            "playable": True,
        },
        {
            "audio": "2026/demo/Transit_Tac_1449.wav",
            "recorded_at": "2026-08-08T14:49:31-06:00",
            "duration_seconds": 104.2,
            "audio_size_bytes": 833640,
            "playable": True,
        },
        {
            "audio": "2026/demo/Search_and_Rescue_1436.wav",
            "recorded_at": "2026-08-08T14:36:17-06:00",
            "duration_seconds": 39.8,
            "audio_size_bytes": 318844,
            "playable": True,
        },
        {
            "audio": "2026/demo/Public_Works_1422.wav",
            "recorded_at": "2026-08-08T14:22:04-06:00",
            "duration_seconds": 72.1,
            "audio_size_bytes": 577236,
            "playable": True,
        },
        {
            "audio": "2026/demo/Event_Channel_1405.wav",
            "recorded_at": "2026-08-08T14:05:53-06:00",
            "duration_seconds": 25.4,
            "audio_size_bytes": 203244,
            "playable": True,
        },
    ],
}


DEMO_SNAPSHOT: Mapping[str, object] = {
    "state": "running",
    "scanner_connected": True,
    "scanner_model": "SDS200",
    "scanner_firmware": "1.26.01",
    "scanner_endpoint": "udp://192.0.2.25:50536",
    "psi_active": True,
    "psi_interval_ms": 500,
    "transition_sequence": 1842,
    "radio_state": {
        "system": "Demo Metro Public Safety",
        "department": "Central Dispatch",
        "site": "Metro Simulcast",
        "channel": "Dispatch 1 (Demo)",
        "mode": "P25",
        "screen_kind": "scanning",
        "screen": "TGID",
        "signal": 5,
        "rssi": -67,
        "battery": None,
        "system_index": 120,
        "department_index": 240,
        "site_index": 12,
        "channel_index": 400,
        "channel_number": 101,
        "channel_kind": "TGID",
        "system_hold": "Off",
        "department_hold": "Off",
        "site_hold": "Off",
        "channel_hold": "On",
        "frequency": "851.0125 MHz",
        "modulation": "NFM",
        "sub_audio_detected": None,
        "tone_out_tone_a": None,
        "tone_out_tone_b": None,
        "weather_mode": None,
        "weather_same": None,
        "service_type": "Law Dispatch",
        "talkgroup_id": "1201",
        "unit_id": "42017",
        "volume": 12,
        "squelch": 3,
        "p25_status": "DAT",
        "mute": "Off",
        "recording": "Off",
    },
    "audio": {
        "running": True,
        "state": "streaming",
    },
    "router": {
        "running": True,
    },
    "recording": dict(DEMO_RECORDING),
}


class DemoDaemonApiClient:
    """Immutable fake daemon client used only by the screenshot helper."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback

    def hello(self) -> dict[str, object]:
        return dict(DEMO_HELLO)

    def runtime_snapshot(self) -> dict[str, object]:
        return dict(DEMO_SNAPSHOT)

    def recording_status(self) -> dict[str, object]:
        return dict(DEMO_RECORDING)

    def recording_start(self) -> dict[str, object]:
        return dict(DEMO_RECORDING)

    def recording_stop(self) -> dict[str, object]:
        stopped = dict(DEMO_RECORDING)
        stopped.update({"status": "stopped", "active": False})
        return stopped

    def recordings_list(self) -> dict[str, object]:
        return dict(DEMO_RECORDINGS)

    def hold_state(
        self,
        scope: str,
        held: bool,
        *,
        timeout: float = 4.0,
    ) -> dict[str, object]:
        del scope, held, timeout
        return _control_result("scanner.hold_state")

    def next(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        del target, first, second, count, timeout
        return _control_result("scanner.next")

    def previous(
        self,
        target: str,
        first: str | int | None = None,
        second: str | int | None = None,
        *,
        count: int = 1,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        del target, first, second, count, timeout
        return _control_result("scanner.previous")

    def reconnect(self, *, timeout: float = 2.0) -> dict[str, object]:
        del timeout
        return _control_result("scanner.reconnect")


class DemoDaemonWaterfallClient:
    """Deterministic bounded-rate waterfall source for browser acceptance."""

    def __init__(self) -> None:
        self._closed = threading.Event()
        self._sequence = 0

    def receive(self) -> DaemonWaterfallRecord:
        if self._sequence > 0 and self._closed.wait(0.05):
            raise DaemonDisconnectedError("demo waterfall stream closed")
        if self._closed.is_set():
            raise DaemonDisconnectedError("demo waterfall stream closed")
        self._sequence += 1
        observed_at = datetime(2026, 8, 8, 21, 12, tzinfo=UTC)
        if self._sequence == 1:
            return DaemonWaterfallRecord(
                sequence=1,
                observed_at=observed_at,
                kind=DaemonWaterfallRecordKind.SESSION_CHECKPOINT,
                payload={
                    "state": "running",
                    "gwf_poll_failures": 0,
                    "waterfall_status": {
                        "lower_frequency": "1540000",
                        "center_frequency": "1550000",
                        "upper_frequency": "1560000",
                        "marker_frequency": "1555500",
                        "marker_position": "120",
                    },
                },
            )

        phase = (self._sequence - 2) % 80
        values = [
            str(12 + ((index + phase) % 80))
            for index in range(240)
        ]
        return DaemonWaterfallRecord(
            sequence=self._sequence,
            observed_at=observed_at,
            kind=DaemonWaterfallRecordKind.GWF,
            payload={
                "source_sequence": self._sequence - 1,
                "values": values,
                "responses_dropped": 0,
                "overflows": 0,
                "source_received_at": observed_at.isoformat(),
            },
        )

    def close(self) -> None:
        self._closed.set()


class _DemoUrlPrefixMiddleware:
    """Expose the demo dashboard behind one deterministic URL prefix."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        path = scope.get("path", "")
        if scope["type"] in {"http", "websocket"} and (
            path == _DEMO_URL_PREFIX or path.startswith(f"{_DEMO_URL_PREFIX}/")
        ):
            rewritten_scope: Scope = dict(scope)
            rewritten_path = path.removeprefix(_DEMO_URL_PREFIX) or "/"
            rewritten_scope["path"] = rewritten_path
            rewritten_scope["raw_path"] = rewritten_path.encode("utf-8")
            rewritten_scope["root_path"] = f"{scope.get('root_path', '')}{_DEMO_URL_PREFIX}"
            await self._app(rewritten_scope, receive, send)
            return
        await self._app(scope, receive, send)


class _DemoClockMiddleware:
    """Load a deterministic clock before the packaged dashboard JavaScript."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        path = scope.get("path", "")
        shell_paths = {"/", _DEMO_URL_PREFIX, f"{_DEMO_URL_PREFIX}/"}
        if scope["type"] != "http" or scope.get("method") != "GET" or path not in shell_paths:
            await self._app(scope, receive, send)
            return

        response_start: Message | None = None
        body_parts: list[bytes] = []

        async def inject_clock(message: Message) -> None:
            nonlocal response_start
            if message["type"] == "http.response.start":
                response_start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return

            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            if response_start is None:
                raise RuntimeError("demo shell body preceded its response headers")

            body = b"".join(body_parts)
            marker = _THEME_BOOTSTRAP_SCRIPT_TAG.encode("utf-8")
            if body.count(marker) != 1:
                raise RuntimeError(
                    "packaged dashboard shell does not contain its expected theme bootstrap script"
                )
            body = body.replace(
                marker,
                _DEMO_CLOCK_SCRIPT_TAG.encode("utf-8") + marker,
                1,
            )

            headers = [
                (name, value)
                for name, value in response_start["headers"]
                if name.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(body)).encode("ascii")))
            updated_start = dict(response_start)
            updated_start["headers"] = headers
            await send(updated_start)
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )

        await self._app(scope, receive, inject_clock)


def _control_result(operation: str) -> dict[str, object]:
    return {
        "sequence": 1,
        "operation": operation,
        "started_at": "2026-08-08T21:12:00+00:00",
        "completed_at": "2026-08-08T21:12:00+00:00",
        "snapshot": dict(DEMO_SNAPSHOT),
    }


def _demo_theme_response(theme: str) -> HTMLResponse:
    """Return a fixed screenshot-setup page for one repository-defined theme."""

    if theme not in _THEMES:
        raise HTTPException(status_code=404, detail="unknown demo theme")

    return HTMLResponse(
        content=_DEMO_THEME_SETUP_HTML,
        headers={"Cache-Control": "no-store"},
    )


def create_demo_app() -> FastAPI:
    """Return the production dashboard wired to deterministic fake data."""

    app = create_web_dashboard_app(
        DemoDaemonApiClient,
        waterfall_client_factory=DemoDaemonWaterfallClient,
    )

    @app.get(
        "/__demo/theme/{theme}",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def select_demo_theme(theme: str) -> HTMLResponse:
        return _demo_theme_response(theme)

    @app.get(
        _DEMO_CLOCK_PATH,
        include_in_schema=False,
        response_class=Response,
    )
    def deterministic_demo_clock() -> Response:
        return Response(
            content=_DEMO_CLOCK_JAVASCRIPT,
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    # Exercise the same relative asset and API URL behavior used behind a
    # reverse-proxy or Home Assistant Ingress path prefix. This is intentionally
    # demo-only; the middleware changes only the request scope presented to the
    # unmodified production dashboard routes.
    app.add_middleware(_DemoUrlPrefixMiddleware)
    app.add_middleware(_DemoClockMiddleware)

    return app


def _find_chrome(explicit: str | None) -> str:
    candidates = (
        explicit,
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    )
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())
    raise SystemExit("Chrome/Chromium was not found. Use --chrome to provide an executable.")


def _find_node(explicit: str | None) -> str:
    for candidate in (explicit, "node"):
        if candidate is None:
            continue
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())
    raise SystemExit("Node.js 24 or newer was not found. Use --node to provide its executable.")


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_server(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/healthz", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as error:
            last_error = error
        time.sleep(0.1)
    raise RuntimeError(f"demo server did not become ready at {base_url}: {last_error}")


def _capture_viewport_is_exact(viewport: object, capture: Capture) -> bool:
    if not isinstance(viewport, dict):
        return False
    if viewport.get("width") != capture.width or viewport.get("height") != capture.height:
        return False

    def close(name: str, expected: float, tolerance: float) -> bool:
        observed = viewport.get(name)
        return (
            isinstance(observed, (int, float))
            and not isinstance(observed, bool)
            and abs(observed - expected) <= tolerance
        )

    return (
        close("dpr", capture.device_scale_factor, 0.001)
        and close("visualWidth", capture.width, 0.01)
        and close("visualHeight", capture.height, 0.01)
        and close("visualScale", 1, 0.001)
        and viewport.get("reducedMotion") is True
    )


def _capture(
    *,
    chrome: str,
    node: str,
    profile_dir: Path,
    base_url: str,
    capture: Capture,
    output_dir: Path,
    virtual_time_ms: int,
    capture_timeout_seconds: float,
) -> Path:
    if Path(capture.filename).name != capture.filename:
        raise RuntimeError(f"capture filename must be a basename: {capture.filename}")
    destination = output_dir / capture.filename
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{destination.stem}-",
        suffix=".png",
        dir=output_dir,
        delete=False,
    ) as staging_stream:
        staging = Path(staging_stream.name)

    command = [
        node,
        str(_REPOSITORY_ROOT / _CAPTURE_HELPER),
        "--base-url",
        base_url,
        "--chrome",
        chrome,
        "--profile-dir",
        str(profile_dir),
        "--theme",
        capture.theme,
        "--width",
        str(capture.width),
        "--height",
        str(capture.height),
        "--dpr",
        str(capture.device_scale_factor),
        "--output",
        str(staging),
        "--settle-ms",
        str(virtual_time_ms),
        "--timeout-ms",
        str(max(1, round(capture_timeout_seconds * 1000))),
    ]

    try:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=capture_timeout_seconds + 10.0,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ""
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            raise RuntimeError(
                "Node/CDP screenshot capture timed out before completing "
                f"{capture.filename}:\n{output}"
            ) from error

        if completed.returncode != 0:
            raise RuntimeError(
                f"Node/CDP screenshot capture failed for {capture.filename}:\n{completed.stdout}"
            )
        try:
            result = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise RuntimeError(
                "Node/CDP screenshot capture returned invalid JSON for "
                f"{capture.filename}:\n{completed.stdout}"
            ) from error
        if not isinstance(result, dict):
            raise RuntimeError(
                f"Node/CDP screenshot capture returned a non-object for {capture.filename}"
            )
        outer_html = result.get("outerHTML")
        viewport = result.get("viewport")
        if not isinstance(outer_html, str) or not _capture_dom_is_ready(outer_html, capture):
            raise RuntimeError(
                "Chrome captured the page before the deterministic dashboard state "
                f"was ready: {capture.filename}"
            )
        if not _capture_viewport_is_exact(viewport, capture):
            raise RuntimeError(
                "Node/CDP screenshot capture did not use the exact requested CSS "
                f"viewport for {capture.filename}: {viewport!r}"
            )
        if not _valid_screenshot(staging, capture):
            raise RuntimeError(f"CDP did not create a valid screenshot: {staging}")
        if result.get("pngBytes") != staging.stat().st_size:
            raise RuntimeError(
                f"CDP reported an inconsistent PNG byte count for {capture.filename}"
            )

        os.replace(staging, destination)
    finally:
        staging.unlink(missing_ok=True)

    return destination


def _read_regular_file(path: Path, maximum_bytes: int) -> bytes | None:
    """Read one bounded regular file without accepting a symlink or type race."""

    descriptor: int | None = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_size > maximum_bytes
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            return None
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            content = stream.read(maximum_bytes + 1)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(content) > maximum_bytes or len(content) != after.st_size:
        return None
    return content


def _png_dimensions_bytes(content: bytes) -> tuple[int, int] | None:
    """Validate complete bounded non-interlaced PNG bytes and return dimensions."""

    if len(content) > _MAX_PNG_FILE_BYTES:
        return None
    if not content.startswith(_PNG_SIGNATURE):
        return None

    offset = len(_PNG_SIGNATURE)
    chunk_index = 0
    width = 0
    height = 0
    bit_depth = 0
    color_type = -1
    seen_ihdr = False
    seen_plte = False
    seen_idat = False
    ended_idat = False
    seen_iend = False
    palette_entries = 0
    compressed_parts: list[bytes] = []

    while offset < len(content):
        if len(content) - offset < 12:
            return None
        chunk_length = unpack(">I", content[offset : offset + 4])[0]
        if chunk_length > 0x7FFFFFFF or chunk_length > len(content) - offset - 12:
            return None
        chunk_type = content[offset + 4 : offset + 8]
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + chunk_length
        chunk_data = content[chunk_data_start:chunk_data_end]
        stored_crc = unpack(">I", content[chunk_data_end : chunk_data_end + 4])[0]
        calculated_crc = zlib.crc32(chunk_data, zlib.crc32(chunk_type)) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            return None
        if len(chunk_type) != 4 or not all(
            ord("A") <= byte <= ord("Z") or ord("a") <= byte <= ord("z") for byte in chunk_type
        ):
            return None
        # PNG reserves the third chunk-type bit; it must remain uppercase.
        if chunk_type[2] & 0x20:
            return None

        offset = chunk_data_end + 4
        if chunk_type == b"IHDR":
            if chunk_index != 0 or seen_ihdr or chunk_length != 13:
                return None
            (
                width,
                height,
                bit_depth,
                color_type,
                compression_method,
                filter_method,
                interlace_method,
            ) = unpack(">IIBBBBB", chunk_data)
            allowed_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                width <= 0
                or height <= 0
                or bit_depth not in allowed_depths.get(color_type, set())
                or compression_method != 0
                or filter_method != 0
                or interlace_method != 0
            ):
                return None
            seen_ihdr = True
        elif not seen_ihdr:
            return None
        elif chunk_type == b"PLTE":
            if seen_plte or seen_idat or color_type in {0, 4}:
                return None
            if chunk_length == 0 or chunk_length % 3 != 0 or chunk_length > 768:
                return None
            palette_entries = chunk_length // 3
            if color_type == 3 and palette_entries > 2**bit_depth:
                return None
            seen_plte = True
        elif chunk_type == b"IDAT":
            if ended_idat or (color_type == 3 and not seen_plte):
                return None
            seen_idat = True
            compressed_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if not seen_idat or seen_iend or chunk_length != 0 or offset != len(content):
                return None
            seen_iend = True
            break
        else:
            if seen_idat:
                ended_idat = True
            # Unknown critical chunks cannot be decoded safely.
            if not chunk_type[0] & 0x20:
                return None
        chunk_index += 1

    if not seen_iend or (color_type == 3 and not seen_plte):
        return None

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    expected_decoded_bytes = (row_bytes + 1) * height
    if expected_decoded_bytes > _MAX_PNG_DECODED_BYTES:
        return None
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(
            b"".join(compressed_parts),
            expected_decoded_bytes + 1,
        )
    except zlib.error:
        return None
    if (
        len(decoded) != expected_decoded_bytes
        or not decompressor.eof
        or decompressor.unconsumed_tail
        or decompressor.unused_data
    ):
        return None
    stride = row_bytes + 1
    if any(decoded[row * stride] > 4 for row in range(height)):
        return None
    return width, height


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    """Validate a complete regular non-interlaced PNG and return its dimensions."""

    content = _read_regular_file(path, _MAX_PNG_FILE_BYTES)
    if content is None:
        return None
    return _png_dimensions_bytes(content)


def _valid_screenshot(path: Path, capture: Capture) -> bool:
    dimensions = _png_dimensions(path)
    if dimensions is None:
        return False
    expected_size = (
        capture.width * capture.device_scale_factor,
        capture.height * capture.device_scale_factor,
    )
    return dimensions == expected_size


def _markdown_image_targets(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    return tuple(target.strip() for target in _MARKDOWN_IMAGE.findall(text))


def _gallery_contract_errors(root: Path = _REPOSITORY_ROOT) -> tuple[str, ...]:
    """Return exact source-gallery contract violations without starting Chrome."""

    errors: list[str] = []
    expected_filenames = tuple(capture.filename for capture in CAPTURES)
    expected_filename_set = set(expected_filenames)
    if len(CAPTURES) != 9:
        errors.append(f"CAPTURES contains {len(CAPTURES)} entries instead of 9")
    if len(expected_filename_set) != len(expected_filenames):
        errors.append("CAPTURES contains duplicate filenames")

    gallery_directory = root / _GALLERY_DIRECTORY
    try:
        gallery_metadata = gallery_directory.lstat()
        if not stat.S_ISDIR(gallery_metadata.st_mode):
            raise NotADirectoryError("gallery path is not a real directory")
        observed_entries = {path.name for path in gallery_directory.iterdir()}
    except OSError as error:
        errors.append(f"cannot inspect {_GALLERY_DIRECTORY}: {error}")
        observed_entries = set()
    if observed_entries != expected_filename_set:
        missing = sorted(expected_filename_set - observed_entries)
        unexpected = sorted(observed_entries - expected_filename_set)
        errors.append(f"gallery directory mismatch; missing={missing}, unexpected={unexpected}")

    for capture in CAPTURES:
        screenshot = gallery_directory / capture.filename
        relative_screenshot = _GALLERY_DIRECTORY / capture.filename
        try:
            metadata = screenshot.lstat()
        except OSError as error:
            errors.append(f"{relative_screenshot} is missing or inaccessible: {error}")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            entry_type = "symbolic link" if stat.S_ISLNK(metadata.st_mode) else "non-regular entry"
            errors.append(f"{relative_screenshot} is a {entry_type}, not a regular image file")
            continue
        if not _valid_screenshot(screenshot, capture):
            errors.append(
                f"{relative_screenshot} is not a valid "
                "capture with its declared physical dimensions"
            )

    expected_guide_targets = {f"assets/web-dashboard/{filename}" for filename in expected_filenames}
    try:
        guide_targets = tuple(
            target
            for target in _markdown_image_targets(root / _GALLERY_GUIDE)
            if target.endswith(".png")
        )
    except OSError as error:
        errors.append(f"cannot inspect {_GALLERY_GUIDE}: {error}")
        guide_targets = ()
    if len(guide_targets) != 9 or set(guide_targets) != expected_guide_targets:
        errors.append(
            "canonical guide PNG references do not name each authoritative "
            "gallery asset exactly once"
        )

    expected_wiki_targets = {
        f"{_WIKI_RAW_IMAGE_PREFIX}{filename}" for filename in expected_filenames
    }
    try:
        wiki_targets = tuple(
            target
            for target in _markdown_image_targets(root / _GALLERY_WIKI)
            if target.endswith(".png")
        )
    except OSError as error:
        errors.append(f"cannot inspect {_GALLERY_WIKI}: {error}")
        wiki_targets = ()
    if len(wiki_targets) != 9 or set(wiki_targets) != expected_wiki_targets:
        errors.append(
            "wiki PNG references do not name each authoritative raw-main gallery asset exactly once"
        )
    return tuple(errors)


def _verify_gallery_contract(root: Path = _REPOSITORY_ROOT) -> None:
    errors = _gallery_contract_errors(root)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise RuntimeError(f"web dashboard gallery contract failed:\n{details}")


def _resolve_sdist(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.is_dir():
        raise RuntimeError(f"source-distribution path does not exist: {path}")
    candidates = tuple(sorted(path.glob("sds200-*.tar.gz")))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one sds200 source distribution in {path}, found {len(candidates)}"
        )
    return candidates[0]


def _required_sdist_sources(root: Path = _REPOSITORY_ROOT) -> dict[str, Path]:
    relative_paths = (
        _GALLERY_GUIDE,
        Path("docs/assets/README.md"),
        _GALLERY_WIKI,
        Path("scripts/generate_web_dashboard_screenshots.py"),
        _CAPTURE_HELPER,
        Path("scripts/audit_web_dashboard_browser.mjs"),
        *(_GALLERY_DIRECTORY / capture.filename for capture in CAPTURES),
    )
    return {relative.as_posix(): root / relative for relative in relative_paths}


def _verified_sdist_source_bytes(root: Path) -> dict[str, bytes]:
    """Read the verified checkout inputs that the source archive must reproduce."""

    _verify_gallery_contract(root)
    source_bytes: dict[str, bytes] = {}
    captures_by_name = {
        (_GALLERY_DIRECTORY / capture.filename).as_posix(): capture for capture in CAPTURES
    }
    for relative_name, source in _required_sdist_sources(root).items():
        maximum_bytes = (
            _MAX_PNG_FILE_BYTES if relative_name in captures_by_name else _MAX_REQUIRED_TEXT_BYTES
        )
        content = _read_regular_file(source, maximum_bytes)
        if content is None:
            raise RuntimeError(
                "required source-distribution checkout input is missing, unreadable, "
                f"a symlink, non-regular, or too large: {relative_name}"
            )
        capture = captures_by_name.get(relative_name)
        if capture is not None:
            expected_dimensions = (
                capture.width * capture.device_scale_factor,
                capture.height * capture.device_scale_factor,
            )
            if _png_dimensions_bytes(content) != expected_dimensions:
                raise RuntimeError(
                    "required source-distribution checkout PNG is invalid or has the "
                    f"wrong dimensions: {relative_name}"
                )
        source_bytes[relative_name] = content
    return source_bytes


def _normalized_sdist_member_name(name: str) -> tuple[str, str | None]:
    """Return one canonical archive root and optional root-relative member name."""

    raw_parts = name.split("/")
    if not name or name.startswith("/") or any(part in {"", ".", ".."} for part in raw_parts):
        raise RuntimeError(f"source distribution contains a non-canonical member name: {name!r}")
    parts = PurePosixPath(name).parts
    root = parts[0]
    relative = PurePosixPath(*parts[1:]).as_posix() if len(parts) > 1 else None
    return root, relative


def _read_sdist_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    expected_size: int,
) -> bytes:
    if member.size != expected_size:
        raise RuntimeError(
            f"source distribution member has size {member.size}, expected {expected_size}: "
            f"{member.name}"
        )
    stream = archive.extractfile(member)
    if stream is None:
        raise RuntimeError(f"cannot read source distribution member: {member.name}")
    try:
        content = stream.read(expected_size + 1)
    except (OSError, tarfile.TarError) as error:
        raise RuntimeError(
            f"cannot read source distribution member {member.name}: {error}"
        ) from error
    finally:
        stream.close()
    if len(content) != expected_size:
        raise RuntimeError(
            f"source distribution member ended before its declared bounded size: {member.name}"
        )
    return content


def _verify_source_distribution(path: Path, root: Path = _REPOSITORY_ROOT) -> Path:
    """Require an exact regular-file copy of the verified source gallery contract."""

    distribution = _resolve_sdist(path)
    expected_source_bytes = _verified_sdist_source_bytes(root)
    try:
        with tarfile.open(distribution, mode="r:gz") as archive:
            members = tuple(archive.getmembers())
            if len(members) > _MAX_SDIST_MEMBERS:
                raise RuntimeError(
                    "source distribution contains too many members: "
                    f"{len(members)} > {_MAX_SDIST_MEMBERS}"
                )

            roots: set[str] = set()
            members_by_relative_name: dict[str, list[tarfile.TarInfo]] = {}
            for member in members:
                archive_root, relative_name = _normalized_sdist_member_name(member.name)
                roots.add(archive_root)
                if relative_name is not None:
                    members_by_relative_name.setdefault(relative_name, []).append(member)
            if len(roots) != 1:
                raise RuntimeError(
                    "source distribution must contain one top-level directory, "
                    f"found {sorted(roots)}"
                )

            required_names = set(expected_source_bytes)
            missing = sorted(required_names - members_by_relative_name.keys())
            gallery_prefix = f"{_GALLERY_DIRECTORY.as_posix()}/"
            observed_gallery = {
                name.removeprefix(gallery_prefix)
                for name in members_by_relative_name
                if name.startswith(gallery_prefix)
            }
            expected_gallery = {capture.filename for capture in CAPTURES}
            unexpected_gallery = sorted(observed_gallery - expected_gallery)
            if missing or unexpected_gallery:
                raise RuntimeError(
                    "source distribution gallery contract failed; "
                    f"missing={missing}, unexpected={unexpected_gallery}"
                )

            captures_by_name = {
                (_GALLERY_DIRECTORY / capture.filename).as_posix(): capture for capture in CAPTURES
            }
            for relative_name, expected_content in expected_source_bytes.items():
                matching_members = members_by_relative_name[relative_name]
                if len(matching_members) != 1:
                    raise RuntimeError(
                        "source distribution contains duplicate required member "
                        f"{relative_name}: {len(matching_members)} copies"
                    )
                member = matching_members[0]
                if not member.isfile():
                    raise RuntimeError(
                        "source distribution required member is not a regular file: "
                        f"{relative_name}"
                    )
                archived_content = _read_sdist_member(
                    archive,
                    member,
                    len(expected_content),
                )
                capture = captures_by_name.get(relative_name)
                if capture is not None:
                    expected_dimensions = (
                        capture.width * capture.device_scale_factor,
                        capture.height * capture.device_scale_factor,
                    )
                    if _png_dimensions_bytes(archived_content) != expected_dimensions:
                        raise RuntimeError(
                            "source distribution PNG is invalid or has the wrong "
                            f"dimensions: {relative_name}"
                        )
                if archived_content != expected_content:
                    archived_digest = hashlib.sha256(archived_content).hexdigest()
                    checkout_digest = hashlib.sha256(expected_content).hexdigest()
                    raise RuntimeError(
                        "source distribution member does not exactly match the "
                        f"verified checkout: {relative_name}; "
                        f"archive sha256={archived_digest}, checkout sha256={checkout_digest}"
                    )
    except (OSError, tarfile.TarError) as error:
        raise RuntimeError(f"cannot inspect source distribution {distribution}: {error}") from error
    return distribution


def _selected_captures(names: Sequence[str]) -> tuple[Capture, ...]:
    if not names:
        return CAPTURES

    requested = set(names)
    available = {capture.filename for capture in CAPTURES}
    unknown = sorted(requested - available)
    if unknown:
        joined = ", ".join(unknown)
        raise SystemExit(f"unknown capture filename(s): {joined}")

    return tuple(capture for capture in CAPTURES if capture.filename in requested)


def _serve(port: int) -> None:
    uvicorn.run(
        create_demo_app(),
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_level="warning",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _verify_capture_repeatability(
    *,
    chrome: str,
    node: str,
    profile_root: Path,
    scratch_root: Path,
    base_url: str,
    capture: Capture,
    capture_index: int,
    virtual_time_ms: int,
    capture_timeout_seconds: float,
) -> str:
    """Capture twice in one environment without touching the checked-in gallery."""

    destinations: list[Path] = []
    for run in ("first", "second"):
        profile_dir = profile_root / f"capture-{capture_index:02d}-{run}"
        output_dir = scratch_root / f"capture-{capture_index:02d}-{run}"
        profile_dir.mkdir()
        output_dir.mkdir()
        destinations.append(
            _capture(
                chrome=chrome,
                node=node,
                profile_dir=profile_dir,
                base_url=base_url,
                capture=capture,
                output_dir=output_dir,
                virtual_time_ms=virtual_time_ms,
                capture_timeout_seconds=capture_timeout_seconds,
            )
        )

    first_digest, second_digest = (_sha256(destination) for destination in destinations)
    if first_digest != second_digest:
        raise RuntimeError(
            "same-Chrome repeatability check produced different PNG bytes for "
            f"{capture.filename}: {first_digest} != {second_digest}"
        )
    return first_digest


def _generate(args: argparse.Namespace) -> None:
    chrome = _find_chrome(args.chrome)
    node = _find_node(args.node)
    captures = _selected_captures(args.only)
    output_dir = args.output_dir.resolve()
    if not args.verify_repeatability:
        output_dir.mkdir(parents=True, exist_ok=True)

    port = args.port or _available_port()
    base_url = f"http://127.0.0.1:{port}"

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    server = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            "--port",
            str(port),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_for_server(base_url)
        with tempfile.TemporaryDirectory(prefix="sds200-web-screenshots-") as profile:
            profile_root = Path(profile)
            print(f"Chrome: {chrome}")
            print(f"Node.js: {node}")
            print(f"Demo server: {base_url}")
            if args.verify_repeatability:
                with tempfile.TemporaryDirectory(
                    prefix="sds200-web-screenshot-repeatability-"
                ) as scratch:
                    scratch_root = Path(scratch)
                    for index, capture in enumerate(captures, start=1):
                        digest = _verify_capture_repeatability(
                            chrome=chrome,
                            node=node,
                            profile_root=profile_root,
                            scratch_root=scratch_root,
                            base_url=base_url,
                            capture=capture,
                            capture_index=index,
                            virtual_time_ms=args.virtual_time_ms,
                            capture_timeout_seconds=args.capture_timeout_seconds,
                        )
                        print(
                            f"Repeatable {capture.theme:16} "
                            f"{capture.width}x{capture.height} "
                            f"DPR {capture.device_scale_factor}: sha256={digest}"
                        )
            else:
                print(f"Output: {output_dir}")
                for index, capture in enumerate(captures, start=1):
                    profile_dir = profile_root / f"capture-{index:02d}"
                    profile_dir.mkdir()
                    destination = _capture(
                        chrome=chrome,
                        node=node,
                        profile_dir=profile_dir,
                        base_url=base_url,
                        capture=capture,
                        output_dir=output_dir,
                        virtual_time_ms=args.virtual_time_ms,
                        capture_timeout_seconds=args.capture_timeout_seconds,
                    )
                    size = destination.stat().st_size
                    print(
                        f"Captured {capture.theme:16} "
                        f"{capture.width}x{capture.height} "
                        f"DPR {capture.device_scale_factor}: "
                        f"{destination} ({size} bytes)"
                    )
    finally:
        server.terminate()
        try:
            server.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5.0)

        if server.stdout is not None:
            server_output = server.stdout.read()
            server.stdout.close()
            if server.returncode not in (0, -15) and server_output:
                print(server_output, file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic exact-CSS-viewport Chrome screenshots of "
            "the real packaged sdsctl web dashboard through Node.js 24 CDP "
            "using fictional demo scanner data."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--serve",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    mode.add_argument(
        "--list",
        action="store_true",
        help="list deterministic captures without running Chrome",
    )
    mode.add_argument(
        "--verify-gallery",
        action="store_true",
        help="verify the exact checked-in gallery and Markdown references without Chrome",
    )
    mode.add_argument(
        "--verify-sdist",
        type=Path,
        metavar="PATH",
        help="verify that one source distribution includes the complete gallery contract",
    )
    mode.add_argument(
        "--verify-repeatability",
        action="store_true",
        help=(
            "capture twice with one Chrome executable into temporary directories "
            "and require byte-identical PNGs"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="local demo-server port; generation chooses a free port by default",
    )
    parser.add_argument(
        "--chrome",
        help="Chrome/Chromium executable or command name",
    )
    parser.add_argument(
        "--node",
        help="Node.js 24-or-newer executable or command name",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/assets/web-dashboard"),
        help="screenshot output directory",
    )
    parser.add_argument(
        "--virtual-time-ms",
        type=int,
        default=2500,
        help=("post-readiness settling milliseconds for the CDP capture (legacy option name)"),
    )
    parser.add_argument(
        "--capture-timeout-seconds",
        type=float,
        default=20.0,
        help="startup and CDP-operation timeout seconds for each Chrome capture",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILENAME",
        help="capture only one named output; may be repeated",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()

    if args.list:
        for capture in CAPTURES:
            print(
                f"{capture.filename}: "
                f"theme={capture.theme} "
                f"viewport={capture.width}x{capture.height} "
                f"device-scale-factor={capture.device_scale_factor}"
            )
        return

    if args.verify_gallery:
        try:
            _verify_gallery_contract()
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
        print("Web dashboard gallery contract passed for 9 authoritative PNG files.")
        return

    if args.verify_sdist is not None:
        try:
            distribution = _verify_source_distribution(args.verify_sdist.resolve())
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
        print(f"Source-distribution gallery contract passed: {distribution}")
        return

    if args.serve:
        if args.port <= 0:
            raise SystemExit("--serve requires a positive --port")
        _serve(args.port)
        return

    if args.virtual_time_ms < 0:
        raise SystemExit("--virtual-time-ms must be non-negative")
    if args.capture_timeout_seconds <= 0:
        raise SystemExit("--capture-timeout-seconds must be positive")

    _generate(args)


if __name__ == "__main__":
    main()
