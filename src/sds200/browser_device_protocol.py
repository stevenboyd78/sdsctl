"""Strict request framing for the experimental browser-device native helper.

No host registration, credential access, HTTP request or authentication is wired
to this module. It is the bounded parser foundation, not an enabled login path.
"""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, BinaryIO

BROWSER_DEVICE_REQUEST_MAX_BYTES = 4096
_LENGTH = struct.Struct("=I")


class BrowserDeviceProtocolError(ValueError):
    """A fixed, secret-free error suitable for a native-helper failure state."""


class BrowserDeviceAction(StrEnum):
    STATUS = "status"
    AUTHENTICATE = "authenticate"
    SUSPEND = "suspend"
    CLAIM_BROWSER = "claim-browser"


@dataclass(frozen=True, slots=True)
class BrowserDeviceRequest:
    action: BrowserDeviceAction


@dataclass(frozen=True, slots=True)
class BrowserResumeRequest:
    """Separate strict resume envelope; ticket/intent never appear in repr."""

    action: str
    intent: str | None = field(default=None, repr=False)
    revision: int | None = None
    generation: int | None = None
    ticket: str | None = field(default=None, repr=False)
    expires_at: float | None = None


@dataclass(frozen=True, slots=True)
class BrowserRetirementRequest:
    """Read-only proof request; operation and filesystem selection stay local."""

    identity: str = field(repr=False)
    intent: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BrowserRetirementAcknowledgement:
    """Paused browser acknowledgement; all filesystem selection remains local."""

    identity: str = field(repr=False)
    intent: str = field(repr=False)
    retirement: str = field(repr=False)
    mode: str
    revision: int


@dataclass(frozen=True, slots=True)
class BrowserRecoveryLaunchRequest:
    """Fixed generated page/worker readiness, never browser consent."""

    identity: str = field(repr=False)
    intent: str = field(repr=False)
    binding: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BrowserWorkerContextRequest:
    build: str


@dataclass(frozen=True, slots=True)
class BrowserContinuationReadRequest:
    """Fixed owned read/review only; never mutation, consent or session issuance."""

    action: str


@dataclass(frozen=True, slots=True)
class BrowserWorkerRequest:
    build: str
    request: (BrowserDeviceRequest | BrowserResumeRequest | BrowserRetirementRequest
              | BrowserRetirementAcknowledgement | BrowserRecoveryLaunchRequest
              | BrowserContinuationReadRequest)


def _resume(value: dict[str, Any]) -> BrowserResumeRequest:
    action = value["action"]
    fields = {"review-resume": set(), "prepare-resume": {"intent", "revision", "generation"},
              "commit-resume": {"intent", "ticket", "revision", "expires_at"}}[action]
    if set(value) != {"version", "action"} | fields:
        raise _invalid()
    for key in fields:
        item = value[key]
        if key in {"intent", "ticket"}:
            valid = type(item) is str and re.fullmatch(r"[a-f0-9]{64}", item) is not None
        elif key == "expires_at":
            valid = type(item) in (int, float) and 0 <= item < 2**53 and math.isfinite(item)
        else:
            valid = type(item) is int and 1 <= item < 2**53 - 1
        if not valid:
            raise _invalid()
    return BrowserResumeRequest(action, **{key: value[key] for key in fields})


def _invalid() -> BrowserDeviceProtocolError:
    return BrowserDeviceProtocolError("Invalid browser-device request.")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def parse_browser_device_request(
    payload: bytes,
) -> (BrowserDeviceRequest | BrowserResumeRequest | BrowserRetirementRequest
      | BrowserRetirementAcknowledgement | BrowserRecoveryLaunchRequest
      | BrowserWorkerContextRequest | BrowserWorkerRequest | BrowserContinuationReadRequest):
    """Reject caller-provided URLs, paths, secrets, roles and unknown fields."""
    if type(payload) is not bytes or not 0 < len(payload) <= BROWSER_DEVICE_REQUEST_MAX_BYTES:
        raise _invalid()
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_object)
        if (
            type(value) is not dict
            or not {"version", "action"} <= set(value)
            or type(value["version"]) is not int
            or value["version"] != 1
            or type(value["action"]) is not str
        ):
            raise _invalid()
        if value["action"] in {"worker-context", "worker-request"}:
            context = value["action"] == "worker-context"
            if (set(value) != {"version", "action", "build"} | (set() if context else {"request"})
                    or type(value["build"]) is not str
                    or re.fullmatch(r"[a-f0-9]{64}", value["build"]) is None):
                raise _invalid()
            if context:
                return BrowserWorkerContextRequest(value["build"])
            inner = value["request"]
            if (type(inner) is not dict or type(inner.get("action")) is not str
                    or inner["action"] in {
                    "worker-context", "worker-request"}):
                raise _invalid()
            parsed = parse_browser_device_request(json.dumps(inner).encode("utf-8"))
            if isinstance(parsed, (BrowserWorkerContextRequest, BrowserWorkerRequest)):
                raise _invalid()
            return BrowserWorkerRequest(value["build"], parsed)
        if value["action"] in {"continuation-current", "continuation-review"}:
            if set(value) != {"version", "action"}:
                raise _invalid()
            return BrowserContinuationReadRequest(value["action"])
        if value["action"] in {"review-resume", "prepare-resume", "commit-resume"}:
            return _resume(value)
        if value["action"] == "recovery-launch-ready":
            if (set(value) != {"version", "action", "identity", "intent", "binding"}
                    or any(type(value[key]) is not str
                           or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None
                           for key in ("identity", "intent", "binding"))):
                raise _invalid()
            return BrowserRecoveryLaunchRequest(
                value["identity"], value["intent"], value["binding"])
        if value["action"] in {"confirm-retirement", "acknowledge-retirement"}:
            acknowledge = value["action"] == "acknowledge-retirement"
            fields = {"retirement", "mode", "revision"} if acknowledge else set()
            if (set(value) != {"version", "action", "identity", "intent"} | fields
                    or any(type(value[key]) is not str
                           or re.fullmatch(r"[a-f0-9]{64}", value[key]) is None
                           for key in ({"identity", "intent", "retirement"} if acknowledge
                                       else {"identity", "intent"}))):
                raise _invalid()
            if acknowledge:
                if (type(value["revision"]) is not int or not 1 <= value["revision"] < 2**53 - 1
                        or type(value["mode"]) is not str or value["mode"] not in {
                            "paused", "credential_rejected", "tls_error", "setup_error",
                            "protocol_error"}):
                    raise _invalid()
                return BrowserRetirementAcknowledgement(
                    **{key: value[key] for key in ("identity", "intent", "retirement",
                                                   "mode", "revision")})
            return BrowserRetirementRequest(value["identity"], value["intent"])
        if set(value) != {"version", "action"}:
            raise _invalid()
        action = BrowserDeviceAction(value["action"])
    except (ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    return BrowserDeviceRequest(action)


def _read_exact(stream: BinaryIO, count: int, *, allow_eof: bool = False) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < count:
        try:
            part = stream.read(count - len(chunks))
        except OSError:
            raise _invalid() from None
        if type(part) is not bytes or len(part) > count - len(chunks):
            raise _invalid()
        if not part:
            if allow_eof and not chunks:
                return None
            raise _invalid()
        chunks.extend(part)
    return bytes(chunks)


def read_browser_device_request(
    stream: BinaryIO,
) -> (BrowserDeviceRequest | BrowserResumeRequest | BrowserRetirementRequest
      | BrowserRetirementAcknowledgement | BrowserRecoveryLaunchRequest
      | BrowserWorkerContextRequest | BrowserWorkerRequest | BrowserContinuationReadRequest | None):
    """Read one native-order frame; EOF is valid only between complete frames.

    The caller must separately enforce a read deadline and process lifetime.
    A size bound alone does not prevent a peer from stalling a pipe.
    """
    header = _read_exact(stream, _LENGTH.size, allow_eof=True)
    if header is None:
        return None
    (length,) = _LENGTH.unpack(header)
    if not 0 < length <= BROWSER_DEVICE_REQUEST_MAX_BYTES:
        raise _invalid()
    payload = _read_exact(stream, length)
    assert payload is not None
    return parse_browser_device_request(payload)
