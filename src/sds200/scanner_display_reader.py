"""Bounded read-only Mimic wire consumer and demand-driven client worker.

No scanner connection, command, profile file, terminal or authentication policy.
The injected source must provide finite reads; one worker owns it exclusively.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from threading import Condition, Thread
from time import monotonic
from types import MappingProxyType
from typing import Any, Protocol

from .scanner_display_web import scanner_display_browser_contract

MAX_DISPLAY_RESPONSE_BYTES = 256 * 1024
_CONTRACT: Any = scanner_display_browser_contract()
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00\Z")


class DisplayWireError(ValueError):
    """Fixed failure category; never include remote values or exception text."""


def _require(condition: bool) -> None:
    if not condition:
        raise DisplayWireError("Invalid Mimic-SDS frame.")


def _object(value: Any, keys: tuple[str, ...] | list[str]) -> dict[str, Any]:
    _require(type(value) is dict and set(value) == set(keys))
    return value  # type: ignore[no-any-return]


def _text(value: Any, maximum: int) -> bool:
    return (
        type(value) is str
        and len(value) <= maximum
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _matches(value: Any, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def decode_display_packet(value: object) -> Mapping[str, Any]:
    """Validate the daemon result and return an immutable detached projection."""
    data = _object(
        value,
        [
            "schema_version",
            "endpoint_id",
            "stream_id",
            "session_id",
            "failure",
            "source_status",
            "frames",
        ],
    )
    _require(type(data["schema_version"]) is int and data["schema_version"] == 1)
    _require(_matches(data["endpoint_id"], _UUID) and _matches(data["stream_id"], _UUID))
    _require(data["session_id"] is None or _matches(data["session_id"], _UUID))
    for key in ("failure", "source_status"):
        item = data[key]
        _require(item is None or (_text(item, 64) and re.fullmatch(r"[a-z_]+", item) is not None))
    frames = _object(data["frames"], ["preferred", "simple", "detail"])
    for frame in frames.values():
        _object(
            frame,
            [
                "status",
                "layout_basis",
                "profile_status",
                "profile_refresh_pending",
                "profile_revision",
                "source",
                "sequence",
                "age_seconds",
                "screen",
                "indicators",
            ],
        )
        for key, choices in (
            ("status", "statuses"),
            ("layout_basis", "bases"),
            ("profile_status", "profiles"),
        ):
            _require(type(frame[key]) is str and frame[key] in _CONTRACT[choices])
        _require(type(frame["profile_refresh_pending"]) is bool)
        _require(frame["profile_revision"] is None or _matches(frame["profile_revision"], _HASH))
        sequence, age = frame["sequence"], frame["age_seconds"]
        _require(sequence is None or (type(sequence) is int and 0 <= sequence <= 2**53 - 1))
        _require(
            age is None or (type(age) in (int, float) and 0 <= age <= 1e15 and math.isfinite(age))
        )
        _require((sequence is None) == (age is None))
        _require((frame["profile_revision"] is None) == (frame["source"] is None))
        if frame["source"] is not None:
            source = _object(
                frame["source"], ["source_id", "source_kind", "acquired_at", "imported_at"]
            )
            _require(_matches(source["source_id"], _UUID))
            _require(
                type(source["source_kind"]) is str and source["source_kind"] in _CONTRACT["sources"]
            )
            for key in ("acquired_at", "imported_at"):
                _require(_text(source[key], 40) and _matches(source[key], _STAMP))
                try:
                    datetime.fromisoformat(source[key])
                except ValueError:
                    raise DisplayWireError("Invalid Mimic-SDS frame.") from None
        indicators = _object(
            frame["indicators"], ["alert_led", "system_hold", "department_hold", "channel_hold"]
        )
        color = indicators["alert_led"]
        _require(color is None or (type(color) is str and color in _CONTRACT["leds"]))
        for key in ("system_hold", "department_hold", "channel_hold"):
            _require(indicators[key] is None or type(indicators[key]) is bool)
        if frame["status"] != "current":
            _require(all(item is None for item in indicators.values()))
        else:
            _require(data["session_id"] is not None and sequence is not None)
        if frame["status"] == "disconnected":
            _require(data["session_id"] is None and frame["screen"] is None)
        if frame["screen"] is None:
            continue
        _require(frame["source"] is not None and frame["profile_status"] != "unavailable")
        screen = _object(
            frame["screen"], ["mode", "rows", "columns", "color_mode", "regions", "issues"]
        )
        _require(type(screen["mode"]) is str and screen["mode"] in _CONTRACT["layouts"])
        layout = _CONTRACT["layouts"][screen["mode"]]
        _require(
            all(
                type(screen[key]) is int and screen[key] == layout[key]
                for key in ("rows", "columns")
            )
        )
        _require(
            type(screen["color_mode"]) is str
            and screen["color_mode"] in ("COLOR", "BLACK", "WHITE")
        )
        _require(
            type(screen["regions"]) is list and len(screen["regions"]) == len(layout["regions"])
        )
        for region, canonical in zip(screen["regions"], layout["regions"], strict=True):
            _object(
                region, [*canonical, "selection", "token", "stored_color", "value_status", "text"]
            )
            for key, expected in canonical.items():
                _require(type(region[key]) is type(expected) and region[key] == expected)
            _require(
                type(region["selection"]) is str and region["selection"] in _CONTRACT["selections"]
            )
            _require(
                type(region["value_status"]) is str
                and region["value_status"] in _CONTRACT["values"]
            )
            token = region["token"]
            _require(token is None or (_text(token, 64) and token.isascii()))
            content = region["text"]
            _require(content is None or _text(content, 256))
            _require(
                (content is not None and frame["status"] == "current")
                if region["value_status"] == "raw_source"
                else content is None
            )
            if region["stored_color"] is not None:
                pair = _object(region["stored_color"], ["text", "background"])
                _require(
                    all(_matches(item, re.compile(r"[0-9a-fA-F]{6}\Z")) for item in pair.values())
                )
        _require(type(screen["issues"]) is list and len(screen["issues"]) <= 64)
        for issue in screen["issues"]:
            _object(issue, ["namespace", "group", "kind"])
            _require(
                issue["namespace"] in ("option", "color")
                and type(issue["group"]) is int
                and 0 <= issue["group"] <= 64
                and issue["kind"] in _CONTRACT["issues"]
            )
    preferred = frames["preferred"]
    for style in ("simple", "detail"):
        other = frames[style]
        for key in (
            "status",
            "profile_status",
            "profile_refresh_pending",
            "profile_revision",
            "source",
            "sequence",
            "age_seconds",
            "indicators",
        ):
            _require(other[key] == preferred[key])
        if preferred["screen"] is None:
            _require(other["screen"] is None)
        else:
            mode = preferred["screen"]["mode"]
            family = re.sub(r"^(simple|detail)_", "", mode)
            expected = f"{style}_{family}" if family in ("trunk", "conventional") else mode
            _require(other["screen"] is not None and other["screen"]["mode"] == expected)
    try:
        _require(
            len(json.dumps(data, ensure_ascii=True, allow_nan=False)) <= MAX_DISPLAY_RESPONSE_BYTES
        )
    except (TypeError, ValueError, RecursionError):
        raise DisplayWireError("Invalid Mimic-SDS frame.") from None
    return _freeze(data)  # type: ignore[no-any-return]


@dataclass(frozen=True)
class DisplayFrameSource:
    read: Callable[[], object]
    close: Callable[[], None]


class _FrameClient(Protocol):
    def hello(self) -> dict[str, object]: ...
    def display_frame(self) -> dict[str, object]: ...
    def close(self) -> None: ...


def daemon_display_source(client: _FrameClient) -> DisplayFrameSource:
    """Negotiate each newly opened API session, using its existing credentials."""

    def read() -> object:
        hello = client.hello()
        operations = hello.get("operations")
        _require(type(operations) is list and "display.frame" in operations)
        return client.display_frame()

    return DisplayFrameSource(read, client.close)


class DisplayFrameReader:
    """One lazy finite-read worker; UI never waits for transport I/O or close."""

    def __init__(self, source: DisplayFrameSource, *, clock: Callable[[], float] = monotonic):
        self._source, self._clock = source, clock
        self._condition = Condition()
        self._active = self._closed = False
        self._generation = 0
        self._thread: Thread | None = None
        self._packet: Mapping[str, Any] | None = None
        self._endpoint: str | None = None
        self._identity: tuple[str, str | None] | None = None
        self._sequence: int | None = None
        self._deadline: float | None = None
        self._message = "Mimic-SDS inactive — values cleared"

    def set_active(self, active: bool) -> None:
        with self._condition:
            if self._closed or self._active == active:
                return
            self._active = active
            self._generation += 1
            self._packet = None
            self._message = "Waiting for current scanner data" if active else "Mimic-SDS inactive"
            if active and self._thread is None:
                self._thread = Thread(target=self._run, name="sdsctl-mimic-reader", daemon=True)
                self._thread.start()
            self._condition.notify_all()

    def view(self) -> tuple[Mapping[str, Any] | None, str]:
        with self._condition:
            if (
                self._packet is not None
                and self._packet["frames"]["preferred"]["status"] == "current"
                and self._deadline is not None
                and self._clock() >= self._deadline
            ):
                self._packet = None
                self._message = "Stale scanner data — values cleared"
            return self._packet, self._message

    def close(self, *, wait: bool = False) -> None:
        with self._condition:
            self._closed = True
            self._active = False
            self._generation += 1
            self._packet = None
            self._message = "Mimic-SDS stopped"
            self._condition.notify_all()
        if wait and self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _accept(self, packet: Mapping[str, Any], started: float) -> None:
        _require(self._endpoint is None or self._endpoint == packet["endpoint_id"])
        identity = packet["stream_id"], packet["session_id"]
        frame = packet["frames"]["preferred"]
        sequence = frame["sequence"]
        _require(
            identity != self._identity
            or sequence is None
            or self._sequence is None
            or sequence >= self._sequence
        )
        deadline = None if sequence is None else started + 5 - frame["age_seconds"]
        if identity != self._identity:
            self._sequence = self._deadline = None
        elif sequence == self._sequence and deadline is not None and self._deadline is not None:
            deadline = min(deadline, self._deadline)
        self._endpoint, self._identity = packet["endpoint_id"], identity
        if sequence is not None:
            self._sequence, self._deadline = sequence, deadline
        self._packet = packet
        if packet["failure"] is not None:
            self._packet = None
            self._message = "Display configuration unavailable — administrator review required"

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    active = self._active
                if not active:
                    with suppress(Exception):
                        self._source.close()
                with self._condition:
                    self._condition.wait_for(lambda: self._active or self._closed)
                    if self._closed:
                        return
                    ticket = self._generation
                started = self._clock()
                delay = 0.25
                try:
                    packet = decode_display_packet(self._source.read())
                    with self._condition:
                        if self._active and not self._closed and ticket == self._generation:
                            self._accept(packet, started)
                except Exception:
                    delay = 2.0
                    with self._condition:
                        if ticket == self._generation and not self._closed:
                            self._packet = None
                            self._message = "Mimic-SDS data unavailable — retrying safely"
                with self._condition:

                    def changed(expected: int = ticket) -> bool:
                        return self._closed or expected != self._generation

                    self._condition.wait_for(changed, timeout=delay)
                    if self._closed:
                        return
        finally:
            with suppress(Exception):
                self._source.close()
