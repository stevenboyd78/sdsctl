"""Experimental verified resume transport; no native message/CLI wiring.

Uses only fixed private configuration and the normal verified HTTPS transport.
Caller MUST enforce the native runner's independent total process deadline.
Device proof never changes server authority or grants browser consent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .browser_device_native import BrowserNativeConfiguration, _post_browser_device
from .browser_device_recovery import (
    ExchangeFailure,
    ExchangeSession,
    RecoveryMode,
    _object,
    parse_exchange_response,
)
from .browser_device_store import BrowserDeviceRecord, BrowserDeviceState


@dataclass(frozen=True, slots=True)
class BrowserVerifiedRecord:
    """Context-bound TLS adapter result, not a signed or serializable approval."""

    identity: str
    record: BrowserDeviceRecord
    drained: bool


def _generation(value: int) -> None:
    if type(value) is not int or not 1 <= value < 2**53 - 1:
        raise ExchangeFailure(RecoveryMode.SETUP_ERROR)


def _document(status: int, body: bytes, content_type: str, retry: str | None) -> dict[str, object]:
    if status != 200:
        # Keep the existing secret-free status classification, including rate limits.
        parse_exchange_response(status, body, content_type=content_type, retry_after=retry)
    try:
        if content_type != "application/json" or not 0 < len(body) <= 4096:
            raise ValueError()
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
        if type(value) is not dict:
            raise ValueError()
        return value
    except Exception:
        raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR) from None


def verify_browser_device(
    configuration: BrowserNativeConfiguration, expected: BrowserDeviceRecord | None = None,
) -> BrowserVerifiedRecord:
    """Authenticate current device, confirm older requests drained, issue no token."""
    payload: dict[str, object] = {"device_id": configuration.device_id}
    if expected is not None:
        if (not isinstance(expected, BrowserDeviceRecord)
                or expected.device_id != configuration.device_id
                or expected.state is not BrowserDeviceState.ACTIVE):
            raise ExchangeFailure(RecoveryMode.SETUP_ERROR)
        _generation(expected.generation)
        payload["generation"] = expected.generation
    value = _document(*_post_browser_device(configuration, "/auth/device/verify", payload))
    try:
        generation = value["generation"]
        if (set(value) != {"version", "device_id", "generation", "state", "drained"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["device_id"] != configuration.device_id
                or type(generation) is not int or not 1 <= generation < 2**53 - 1
                or value["state"] != "active" or value["drained"] is not True
                or (expected is not None and generation != expected.generation)):
            raise ValueError()
        return BrowserVerifiedRecord(configuration.identity, BrowserDeviceRecord(
            configuration.device_id, generation, BrowserDeviceState.ACTIVE), True)
    except Exception:
        raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR) from None


def exchange_browser_device_at_generation(
    configuration: BrowserNativeConfiguration, generation: int,
) -> ExchangeSession:
    """Issue a display-only session for EXACTLY the reviewed generation, never latest."""
    _generation(generation)
    value = _document(*_post_browser_device(configuration, "/auth/device/session",
        {"device_id": configuration.device_id, "generation": generation}))
    try:
        if (set(value) != {"token", "expires_in", "generation"}
                or type(value["generation"]) is not int or value["generation"] != generation):
            raise ValueError()
        return parse_exchange_response(200, json.dumps({"token": value["token"],
            "expires_in": value["expires_in"]}).encode("ascii"), content_type="application/json")
    except Exception:
        raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR) from None
