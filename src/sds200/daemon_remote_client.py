"""Authenticated TLS client transport for one selected daemon service."""

from __future__ import annotations

import os
import socket as socket_module
import ssl
import stat
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from math import isfinite
from pathlib import Path
from time import monotonic

from .daemon_remote import DAEMON_REMOTE_MAX_TLS_FILE_BYTES
from .daemon_remote_auth import (
    DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES,
    DaemonRemoteAuthenticationError,
    DaemonRemoteAuthenticationResult,
    DaemonRemoteChallenge,
    DaemonRemoteCredentialError,
    build_daemon_remote_authentication_request,
    load_daemon_remote_credential,
)
from .daemon_remote_liveness import configure_remote_tcp_liveness
from .daemon_remote_service import (
    DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES,
    DaemonRemoteService,
    DaemonRemoteServiceError,
    DaemonRemoteServiceErrorReason,
    DaemonRemoteServiceRequest,
    DaemonRemoteServiceResult,
)
from .daemon_remote_tls import DAEMON_REMOTE_TLS_VERSION
from .exceptions import DaemonUnavailableError

DAEMON_REMOTE_CLIENT_ENDPOINT = "sdsctl-remote-daemon"

_PRIVATE_IPV4_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
_LINK_LOCAL_IPV4_NETWORK = IPv4Network("169.254.0.0/16")
_UNIQUE_LOCAL_IPV6_NETWORK = IPv6Network("fc00::/7")
_LINK_LOCAL_IPV6_NETWORK = IPv6Network("fe80::/10")


class DaemonRemoteClientErrorReason(StrEnum):
    """Stable redacted client-transport failure classes."""

    CONFIGURATION_FAILED = "configuration_failed"
    CONNECT_FAILED = "connect_failed"
    TLS_HANDSHAKE_FAILED = "tls_handshake_failed"
    AUTHENTICATION_FAILED = "authentication_failed"
    SERVICE_NEGOTIATION_FAILED = "service_negotiation_failed"


_ERROR_MESSAGES = {
    DaemonRemoteClientErrorReason.CONFIGURATION_FAILED: (
        "Remote daemon client configuration is unavailable."
    ),
    DaemonRemoteClientErrorReason.CONNECT_FAILED: (
        "Remote daemon connection is unavailable."
    ),
    DaemonRemoteClientErrorReason.TLS_HANDSHAKE_FAILED: (
        "Remote daemon server identity could not be verified."
    ),
    DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED: (
        "Remote daemon client authentication failed."
    ),
    DaemonRemoteClientErrorReason.SERVICE_NEGOTIATION_FAILED: (
        "Remote daemon service negotiation failed."
    ),
}


class DaemonRemoteClientError(DaemonUnavailableError):
    """Report a remote connection failure without endpoint or secret detail."""

    def __init__(self, reason: DaemonRemoteClientErrorReason) -> None:
        if not isinstance(reason, DaemonRemoteClientErrorReason):
            raise TypeError(
                "Remote daemon client error reason must be "
                "DaemonRemoteClientErrorReason."
            )
        self.reason = reason
        super().__init__(_ERROR_MESSAGES[reason])


@dataclass(frozen=True, slots=True, repr=False)
class DaemonRemoteClientConfiguration:
    """Exact private endpoint and trust material for one thin client."""

    address: str = field(repr=False)
    port: int
    server_hostname: str = field(repr=False)
    certificate_file: Path = field(repr=False)
    client_id: str = field(repr=False)
    credential_file: Path = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", _private_address(self.address))
        object.__setattr__(self, "port", _port(self.port))
        object.__setattr__(
            self,
            "server_hostname",
            _server_hostname(self.server_hostname),
        )
        object.__setattr__(
            self,
            "certificate_file",
            _absolute_path(self.certificate_file, label="certificate"),
        )
        object.__setattr__(self, "client_id", _client_id(self.client_id))
        object.__setattr__(
            self,
            "credential_file",
            _absolute_path(self.credential_file, label="credential"),
        )
        if self.certificate_file == self.credential_file:
            raise ValueError(
                "Remote daemon certificate and client credential files must differ."
            )

    def __repr__(self) -> str:
        return f"DaemonRemoteClientConfiguration(port={self.port})"


@dataclass(frozen=True, slots=True, repr=False)
class DaemonRemoteClientTransport:
    """Validate TLS, authenticate, and select one daemon service."""

    configuration: DaemonRemoteClientConfiguration = field(repr=False)
    service: DaemonRemoteService
    sanitizes_private_state: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, DaemonRemoteClientConfiguration):
            raise TypeError(
                "Remote daemon transport requires DaemonRemoteClientConfiguration."
            )
        if not isinstance(self.service, DaemonRemoteService):
            raise TypeError("Remote daemon transport requires a supported service.")

    def __repr__(self) -> str:
        return f"DaemonRemoteClientTransport(service={self.service.value!r})"

    def for_service(self, service: DaemonRemoteService) -> DaemonRemoteClientTransport:
        """Return another immutable transport sharing the same client identity."""

        return DaemonRemoteClientTransport(self.configuration, service)

    def connect(self, *, timeout: float) -> socket_module.socket:
        """Return one authenticated TLS stream selected for this service."""

        normalized_timeout = _positive_timeout(timeout)
        deadline = monotonic() + normalized_timeout
        try:
            credential = load_daemon_remote_credential(
                self.configuration.credential_file
            )
            context = _client_tls_context(self.configuration.certificate_file)
        except (DaemonRemoteCredentialError, OSError, ssl.SSLError) as error:
            raise DaemonRemoteClientError(
                DaemonRemoteClientErrorReason.CONFIGURATION_FAILED
            ) from error

        raw: socket_module.socket | None = None
        secured: ssl.SSLSocket | None = None
        try:
            try:
                raw = socket_module.create_connection(
                    (self.configuration.address, self.configuration.port),
                    timeout=_remaining_seconds(deadline),
                )
                configure_remote_tcp_liveness(raw)
            except OSError as error:
                raise DaemonRemoteClientError(
                    DaemonRemoteClientErrorReason.CONNECT_FAILED
                ) from error

            try:
                raw.settimeout(_remaining_seconds(deadline))
                secured = context.wrap_socket(
                    raw,
                    server_hostname=self.configuration.server_hostname,
                    do_handshake_on_connect=False,
                )
                raw = None
                secured.settimeout(_remaining_seconds(deadline))
                secured.do_handshake()
                if secured.version() != DAEMON_REMOTE_TLS_VERSION:
                    raise ssl.SSLError("unsupported TLS version")
            except (OSError, ssl.SSLError) as error:
                raise DaemonRemoteClientError(
                    DaemonRemoteClientErrorReason.TLS_HANDSHAKE_FAILED
                ) from error

            try:
                challenge = DaemonRemoteChallenge.from_json_line(
                    _receive_frame(
                        secured,
                        deadline=deadline,
                        maximum=DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES,
                    )
                )
                request = build_daemon_remote_authentication_request(
                    challenge,
                    client_id=self.configuration.client_id,
                    credential=credential,
                )
                secured.settimeout(_remaining_seconds(deadline))
                secured.sendall(request.to_json_line())
                result = DaemonRemoteAuthenticationResult.from_json_line(
                    _receive_frame(
                        secured,
                        deadline=deadline,
                        maximum=DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES,
                    )
                )
                if not result.ok:
                    assert result.error is not None
                    raise DaemonRemoteAuthenticationError(result.error)
            except (DaemonRemoteAuthenticationError, OSError, TimeoutError) as error:
                raise DaemonRemoteClientError(
                    DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED
                ) from error

            try:
                secured.settimeout(_remaining_seconds(deadline))
                secured.sendall(DaemonRemoteServiceRequest(self.service).to_json_line())
                selection = DaemonRemoteServiceResult.from_json_line(
                    _receive_frame(
                        secured,
                        deadline=deadline,
                        maximum=DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES,
                    )
                )
                if not selection.ok or selection.service is not self.service:
                    raise DaemonRemoteServiceError(
                        selection.error
                        if selection.error is not None
                        else DaemonRemoteServiceErrorReason.INVALID_FRAME
                    )
            except (DaemonRemoteServiceError, OSError, TimeoutError) as error:
                raise DaemonRemoteClientError(
                    DaemonRemoteClientErrorReason.SERVICE_NEGOTIATION_FAILED
                ) from error

            secured.settimeout(normalized_timeout)
            connected = secured
            secured = None
            return connected
        finally:
            if secured is not None:
                _close_socket(secured)
            if raw is not None:
                _close_socket(raw)


def _client_tls_context(certificate_file: Path) -> ssl.SSLContext:
    before = _certificate_snapshot(certificate_file)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.options |= ssl.OP_NO_COMPRESSION
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=os.fspath(certificate_file))
    after = _certificate_snapshot(certificate_file)
    if before != after:
        raise OSError("certificate changed while loading")
    return context


def _certificate_snapshot(path: Path) -> tuple[int, int, int, int, int, int]:
    observed = path.lstat()
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise OSError("certificate is not a regular file")
    if not 1 <= observed.st_size <= DAEMON_REMOTE_MAX_TLS_FILE_BYTES:
        raise OSError("certificate size is invalid")
    return (
        stat.S_IFMT(observed.st_mode),
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _receive_frame(
    stream: ssl.SSLSocket,
    *,
    deadline: float,
    maximum: int,
) -> bytes:
    frame = bytearray()
    while len(frame) < maximum:
        stream.settimeout(_remaining_seconds(deadline))
        chunk = stream.recv(1)
        if not chunk or chunk == b"\r":
            raise OSError("remote frame closed or was invalid")
        if chunk == b"\n":
            if not frame:
                raise OSError("remote frame was empty")
            return bytes(frame)
        frame.extend(chunk)
    raise OSError("remote frame exceeded its maximum size")


def _private_address(value: object) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError("Remote daemon client address must be a literal private address.")
    if "://" in value or "/" in value:
        raise ValueError("Remote daemon client address must be a literal private address.")
    address_text, separator, scope = value.partition("%")
    if separator and (
        not scope
        or len(scope) > 64
        or any(not (character.isalnum() or character in "._-") for character in scope)
    ):
        raise ValueError("Remote daemon client IPv6 scope is invalid.")
    try:
        parsed = ip_address(address_text)
    except ValueError as error:
        raise ValueError(
            "Remote daemon client address must be a literal private address."
        ) from error
    allowed = False
    if isinstance(parsed, IPv4Address):
        if separator:
            raise ValueError("Remote daemon IPv4 addresses cannot contain a scope.")
        allowed = any(parsed in network for network in _PRIVATE_IPV4_NETWORKS)
        allowed = allowed or parsed in _LINK_LOCAL_IPV4_NETWORK
    elif isinstance(parsed, IPv6Address):
        allowed = parsed in _UNIQUE_LOCAL_IPV6_NETWORK or parsed in _LINK_LOCAL_IPV6_NETWORK
    if not allowed or parsed.is_unspecified or parsed.is_loopback or parsed.is_multicast:
        raise ValueError("Remote daemon client address must be a literal private address.")
    return value


def _port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Remote daemon client port must be an integer.")
    if not 1 <= value <= 65535:
        raise ValueError("Remote daemon client port must be between 1 and 65535.")
    return value


def _server_hostname(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > 253
        or "://" in value
        or "/" in value
        or "@" in value
        or any(ord(character) < 0x21 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError("Remote daemon TLS server hostname is invalid.")
    return value


def _client_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > 64
        or not value.isascii()
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "._-") for character in value)
    ):
        raise ValueError("Remote daemon client ID is invalid.")
    return value


def _absolute_path(value: object, *, label: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"Remote daemon client {label} path must be a pathlib.Path.")
    if not value.is_absolute():
        raise ValueError(f"Remote daemon client {label} path must be absolute.")
    return value


def _positive_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Remote daemon client timeout must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError("Remote daemon client timeout must be finite and positive.")
    return normalized


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("remote daemon client deadline expired")
    return remaining


def _close_socket(stream: socket_module.socket) -> None:
    with suppress(OSError):
        stream.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        stream.close()


__all__ = [
    "DAEMON_REMOTE_CLIENT_ENDPOINT",
    "DaemonRemoteClientConfiguration",
    "DaemonRemoteClientError",
    "DaemonRemoteClientErrorReason",
    "DaemonRemoteClientTransport",
]
