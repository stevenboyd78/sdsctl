"""Strict service selection for one authenticated remote daemon stream."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

DAEMON_REMOTE_SERVICE_PROTOCOL = "sdsctl.daemon.service"
DAEMON_REMOTE_SERVICE_VERSION = 1
DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES = 4_096


class DaemonRemoteService(StrEnum):
    """One existing daemon service selected after TLS authentication."""

    API = "api"
    EVENTS = "events"
    WATERFALL = "waterfall"
    AUDIO = "audio"


class DaemonRemoteServiceErrorReason(StrEnum):
    """Stable, non-secret service-selection failures."""

    INVALID_FRAME = "invalid_frame"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNSUPPORTED_SERVICE = "unsupported_service"
    AUTHORIZATION_DENIED = "authorization_denied"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    SOURCE_UNAVAILABLE = "source_unavailable"


_ERROR_MESSAGES = {
    DaemonRemoteServiceErrorReason.INVALID_FRAME: (
        "Remote daemon service selection is invalid."
    ),
    DaemonRemoteServiceErrorReason.UNSUPPORTED_PROTOCOL: (
        "Remote daemon service protocol is unsupported."
    ),
    DaemonRemoteServiceErrorReason.UNSUPPORTED_VERSION: (
        "Remote daemon service version is unsupported."
    ),
    DaemonRemoteServiceErrorReason.UNSUPPORTED_SERVICE: (
        "Remote daemon service is unsupported."
    ),
    DaemonRemoteServiceErrorReason.AUTHORIZATION_DENIED: (
        "Remote daemon service authority is unavailable."
    ),
    DaemonRemoteServiceErrorReason.CAPACITY_EXCEEDED: (
        "Remote daemon service capacity is unavailable."
    ),
    DaemonRemoteServiceErrorReason.SOURCE_UNAVAILABLE: (
        "Remote daemon service source is unavailable."
    ),
}


class DaemonRemoteServiceError(RuntimeError):
    """Report one service-selection failure without endpoint or identity."""

    def __init__(self, reason: DaemonRemoteServiceErrorReason) -> None:
        if not isinstance(reason, DaemonRemoteServiceErrorReason):
            raise TypeError(
                "Remote daemon service error reason must be "
                "DaemonRemoteServiceErrorReason."
            )
        self.reason = reason
        super().__init__(_ERROR_MESSAGES[reason])


@dataclass(frozen=True, slots=True)
class DaemonRemoteServiceRequest:
    """Select exactly one service on an authenticated TLS connection."""

    service: DaemonRemoteService
    protocol: str = DAEMON_REMOTE_SERVICE_PROTOCOL
    version: int = DAEMON_REMOTE_SERVICE_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.service, DaemonRemoteService):
            raise TypeError("Remote daemon service request requires a supported service.")
        if self.protocol != DAEMON_REMOTE_SERVICE_PROTOCOL:
            raise ValueError("Remote daemon service protocol is unsupported.")
        if self.version != DAEMON_REMOTE_SERVICE_VERSION:
            raise ValueError("Remote daemon service version is unsupported.")

    def to_json_line(self) -> bytes:
        return _encode_frame(
            {
                "protocol": self.protocol,
                "service": self.service.value,
                "version": self.version,
            }
        )

    @classmethod
    def from_json_line(cls, data: bytes | str) -> DaemonRemoteServiceRequest:
        payload = _decode_frame(data)
        _require_exact_fields(payload, {"protocol", "service", "version"})
        protocol = payload["protocol"]
        if protocol != DAEMON_REMOTE_SERVICE_PROTOCOL:
            raise DaemonRemoteServiceError(
                DaemonRemoteServiceErrorReason.UNSUPPORTED_PROTOCOL
            )
        version = payload["version"]
        if version != DAEMON_REMOTE_SERVICE_VERSION:
            raise DaemonRemoteServiceError(
                DaemonRemoteServiceErrorReason.UNSUPPORTED_VERSION
            )
        raw_service = payload["service"]
        if type(raw_service) is not str:
            raise _invalid_frame()
        try:
            service = DaemonRemoteService(raw_service)
        except (TypeError, ValueError) as error:
            raise DaemonRemoteServiceError(
                DaemonRemoteServiceErrorReason.UNSUPPORTED_SERVICE
            ) from error
        return cls(service=service, protocol=protocol, version=version)


@dataclass(frozen=True, slots=True)
class DaemonRemoteServiceResult:
    """Conclude selection before any selected service bytes are emitted."""

    ok: bool
    service: DaemonRemoteService | None = None
    error: DaemonRemoteServiceErrorReason | None = None
    protocol: str = DAEMON_REMOTE_SERVICE_PROTOCOL
    version: int = DAEMON_REMOTE_SERVICE_VERSION

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise TypeError("Remote daemon service result status must be a boolean.")
        if self.protocol != DAEMON_REMOTE_SERVICE_PROTOCOL:
            raise ValueError("Remote daemon service protocol is unsupported.")
        if self.version != DAEMON_REMOTE_SERVICE_VERSION:
            raise ValueError("Remote daemon service version is unsupported.")
        if self.ok:
            if not isinstance(self.service, DaemonRemoteService):
                raise TypeError("Successful remote daemon service result requires a service.")
            if self.error is not None:
                raise ValueError("Successful remote daemon service result must not contain error.")
        else:
            if self.service is not None:
                raise ValueError("Failed remote daemon service result must not expose a service.")
            if not isinstance(self.error, DaemonRemoteServiceErrorReason):
                raise TypeError("Failed remote daemon service result requires an error reason.")

    @classmethod
    def success(cls, service: DaemonRemoteService) -> DaemonRemoteServiceResult:
        return cls(ok=True, service=service)

    @classmethod
    def failure(
        cls,
        reason: DaemonRemoteServiceErrorReason,
    ) -> DaemonRemoteServiceResult:
        return cls(ok=False, error=reason)

    def to_json_line(self) -> bytes:
        payload: dict[str, object] = {
            "ok": self.ok,
            "protocol": self.protocol,
            "version": self.version,
        }
        if self.ok:
            assert self.service is not None
            payload["service"] = self.service.value
        else:
            assert self.error is not None
            payload["error"] = {
                "code": self.error.value,
                "message": _ERROR_MESSAGES[self.error],
            }
        return _encode_frame(payload)

    @classmethod
    def from_json_line(cls, data: bytes | str) -> DaemonRemoteServiceResult:
        payload = _decode_frame(data)
        if payload.get("protocol") != DAEMON_REMOTE_SERVICE_PROTOCOL:
            raise DaemonRemoteServiceError(
                DaemonRemoteServiceErrorReason.UNSUPPORTED_PROTOCOL
            )
        if payload.get("version") != DAEMON_REMOTE_SERVICE_VERSION:
            raise DaemonRemoteServiceError(
                DaemonRemoteServiceErrorReason.UNSUPPORTED_VERSION
            )
        ok = payload.get("ok")
        if type(ok) is not bool:
            raise _invalid_frame()
        try:
            if ok:
                _require_exact_fields(payload, {"ok", "protocol", "service", "version"})
                raw_service = payload["service"]
                if type(raw_service) is not str:
                    raise _invalid_frame()
                return cls(
                    ok=True,
                    service=DaemonRemoteService(raw_service),
                )
            _require_exact_fields(payload, {"error", "ok", "protocol", "version"})
            error = payload["error"]
            if not isinstance(error, Mapping):
                raise _invalid_frame()
            _require_exact_fields(error, {"code", "message"})
            reason = DaemonRemoteServiceErrorReason(error["code"])
            if error["message"] != _ERROR_MESSAGES[reason]:
                raise _invalid_frame()
            return cls(ok=False, error=reason)
        except DaemonRemoteServiceError:
            raise
        except (TypeError, ValueError) as error:
            raise _invalid_frame() from error


def _encode_frame(payload: Mapping[str, object]) -> bytes:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    if len(encoded) > DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES:
        raise ValueError("Remote daemon service frame exceeds its maximum size.")
    return encoded


def _decode_frame(data: bytes | str) -> Mapping[str, object]:
    if isinstance(data, str):
        try:
            encoded = data.encode("utf-8")
        except UnicodeError as error:
            raise _invalid_frame() from error
    elif isinstance(data, bytes):
        encoded = data
    else:
        raise TypeError("Remote daemon service frame must be bytes or text.")
    if not encoded or len(encoded) > DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES:
        raise _invalid_frame()
    if encoded.endswith(b"\n"):
        encoded = encoded[:-1]
    if not encoded or b"\r" in encoded or b"\n" in encoded:
        raise _invalid_frame()
    try:
        decoded: object = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid_frame() from error
    if not isinstance(decoded, Mapping) or any(
        not isinstance(key, str) for key in decoded
    ):
        raise _invalid_frame()
    return decoded


def _require_exact_fields(
    payload: Mapping[str, object],
    expected: set[str],
) -> None:
    if set(payload) != expected:
        raise _invalid_frame()


def _invalid_frame() -> DaemonRemoteServiceError:
    return DaemonRemoteServiceError(DaemonRemoteServiceErrorReason.INVALID_FRAME)


__all__ = [
    "DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES",
    "DAEMON_REMOTE_SERVICE_PROTOCOL",
    "DAEMON_REMOTE_SERVICE_VERSION",
    "DaemonRemoteService",
    "DaemonRemoteServiceError",
    "DaemonRemoteServiceErrorReason",
    "DaemonRemoteServiceRequest",
    "DaemonRemoteServiceResult",
]
