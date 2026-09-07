from __future__ import annotations

import io
import json
import struct

import pytest

from sds200.browser_device_protocol import (
    BROWSER_DEVICE_REQUEST_MAX_BYTES,
    BrowserDeviceAction,
    BrowserDeviceProtocolError,
    parse_browser_device_request,
    read_browser_device_request,
)


def _frame(payload: bytes) -> bytes:
    return struct.pack("=I", len(payload)) + payload


@pytest.mark.parametrize("action", list(BrowserDeviceAction))
def test_only_declared_actions_are_accepted(action: BrowserDeviceAction) -> None:
    payload = json.dumps({"version": 1, "action": action.value}).encode()
    assert parse_browser_device_request(payload).action is action
    assert read_browser_device_request(io.BytesIO(_frame(payload))).action is action


@pytest.mark.parametrize("value", [
    {}, [], None, True, "secret-value",
    {"version": True, "action": "status"},
    {"version": 1.0, "action": "status"},
    {"version": 2, "action": "status"},
    {"version": 1, "action": "resume"},
    {"version": 1, "action": "operator"},
    {"version": 1, "action": None},
    {"version": 1, "action": {"password": "secret-value"}},
])
def test_invalid_request_is_redacted(value: object) -> None:
    with pytest.raises(BrowserDeviceProtocolError) as caught:
        parse_browser_device_request(json.dumps(value).encode())
    assert str(caught.value) == "Invalid browser-device request."
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("field", ["url", "origin", "path", "credential", "role", "token"])
def test_caller_cannot_supply_destinations_or_secrets(field: str) -> None:
    payload = json.dumps({"version": 1, "action": "authenticate", field: "secret-value"}).encode()
    with pytest.raises(BrowserDeviceProtocolError, match="^Invalid browser-device request.$"):
        parse_browser_device_request(payload)


@pytest.mark.parametrize("payload", [
    b"", b"\xff", b"not JSON", b'{"version":1,"version":1,"action":"status"}',
    b'{"version":1,"action":"status","action":"authenticate"}',
    b'{"version":1,"action":"status"} trailing',
    b"[" * 1500 + b"]" * 1500,
    b"x" * (BROWSER_DEVICE_REQUEST_MAX_BYTES + 1),
])
def test_malformed_or_oversized_payload_is_rejected(payload: bytes) -> None:
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(payload)


def test_oversized_frame_rejected_without_reading_body() -> None:
    stream = io.BytesIO(struct.pack("=I", 0xFFFFFFFF) + b"secret-value")
    with pytest.raises(BrowserDeviceProtocolError):
        read_browser_device_request(stream)
    assert stream.tell() == 4


@pytest.mark.parametrize(
    "data", [b"\x01", b"\x01\x00\x00", struct.pack("=I", 0), _frame(b"{}")[:-1]],
)
def test_partial_or_empty_frame_is_rejected(data: bytes) -> None:
    with pytest.raises(BrowserDeviceProtocolError):
        read_browser_device_request(io.BytesIO(data))


def test_consecutive_frames_and_clean_eof() -> None:
    payload = b'{"version":1,"action":"status"}'
    stream = io.BytesIO(_frame(payload) * 2)
    assert read_browser_device_request(stream).action is BrowserDeviceAction.STATUS
    assert read_browser_device_request(stream).action is BrowserDeviceAction.STATUS
    assert read_browser_device_request(stream) is None


def test_fragmented_pipe_reads() -> None:
    class Fragmented(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            return super().read(min(size, 1))

    stream = Fragmented(_frame(b'{"version":1,"action":"status"}'))
    assert read_browser_device_request(stream).action is BrowserDeviceAction.STATUS


def test_io_failure_does_not_expose_os_error() -> None:
    class Failed(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise OSError("secret-value in private path")

    with pytest.raises(BrowserDeviceProtocolError) as caught:
        read_browser_device_request(Failed())
    assert str(caught.value) == "Invalid browser-device request."
    assert caught.value.__suppress_context__
