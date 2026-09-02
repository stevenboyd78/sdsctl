from __future__ import annotations

import errno
import os
import socket as socket_module
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from typing import Protocol, runtime_checkable

from .daemon_ipc import DaemonSocketLocation
from .exceptions import DaemonUnavailableError


@runtime_checkable
class DaemonClientTransport(Protocol):
    """Open one bounded stream connection to a daemon service."""

    def connect(self, *, timeout: float) -> socket_module.socket:
        """Return one connected stream or raise ``DaemonUnavailableError``."""


@runtime_checkable
class DaemonServerAcceptor(Protocol):
    """Accept bounded client streams for one daemon service."""

    def settimeout(self, value: float | None) -> None:
        """Set the bounded interval used while waiting for one client."""

    def accept(self) -> tuple[socket_module.socket, object]:
        """Return one transport-ready client stream and opaque peer address."""


@runtime_checkable
class DaemonServerPeerContext(Protocol):
    """Apply transport-owned policy before one daemon API request dispatch."""

    def handle_daemon_api_json_line(
        self,
        api: object,
        data: bytes | str,
    ) -> bytes:
        """Return one response without bypassing transport authorization."""


@runtime_checkable
class DaemonServerManagedPeerContext(DaemonServerPeerContext, Protocol):
    """Add revocable lifecycle state to an authorized transport peer."""

    def daemon_api_connection_current(self) -> bool:
        """Return whether this transport-authenticated connection remains valid."""

    def close_daemon_api_peer_context(self) -> None:
        """Release transport-owned authentication or authorization state."""


@runtime_checkable
class DaemonServerListener(Protocol):
    """Own one daemon listener and its transport-specific lifecycle."""

    def start(self) -> DaemonServerAcceptor:
        """Start once and return the acceptor owned by this listener."""

    def stop(self) -> None:
        """Stop accepting clients and release all listener resources."""


@dataclass(frozen=True, slots=True)
class UnixDaemonClientTransport:
    """Open one private local daemon stream through a Unix-domain socket."""

    location: DaemonSocketLocation
    service_label: str = "Daemon"

    def __post_init__(self) -> None:
        if not isinstance(self.location, DaemonSocketLocation):
            raise TypeError(
                "Unix daemon transport location must be a DaemonSocketLocation."
            )
        if not isinstance(self.service_label, str):
            raise TypeError("Unix daemon transport service label must be a string.")
        normalized_label = self.service_label.strip()
        if not normalized_label:
            raise ValueError(
                "Unix daemon transport service label must not be empty."
            )
        if len(normalized_label) > 64 or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in normalized_label
        ):
            raise ValueError(
                "Unix daemon transport service label must be at most 64 "
                "printable characters."
            )
        object.__setattr__(self, "service_label", normalized_label)

    def connect(self, *, timeout: float) -> socket_module.socket:
        normalized_timeout = _positive_timeout(timeout)
        client: socket_module.socket | None = None
        try:
            client = socket_module.socket(
                socket_module.AF_UNIX,
                socket_module.SOCK_STREAM,
            )
            client.settimeout(normalized_timeout)
            client.connect(os.fspath(self.location.path))
        except OSError as error:
            if client is not None:
                _close_socket(client)
            self._raise_unavailable(error)
        assert client is not None
        return client

    def _raise_unavailable(self, error: OSError) -> None:
        path = self.location.path
        label = self.service_label
        if error.errno == errno.ENOENT:
            raise DaemonUnavailableError(
                f"{label} socket was not found: {path}"
            ) from error
        if error.errno == errno.ECONNREFUSED:
            raise DaemonUnavailableError(
                f"{label} socket is present but not accepting connections: {path}"
            ) from error
        if error.errno in {errno.EACCES, errno.EPERM}:
            raise DaemonUnavailableError(
                f"Permission denied while connecting to {label.lower()} socket: "
                f"{path}"
            ) from error
        if isinstance(error, TimeoutError):
            raise DaemonUnavailableError(
                f"Timed out connecting to {label.lower()} socket: {path}"
            ) from error

        detail = error.strerror or error.__class__.__name__
        raise DaemonUnavailableError(
            f"Could not connect to {label.lower()} socket {path}: {detail}"
        ) from error


def _positive_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Daemon client transport timeout must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            "Daemon client transport timeout must be finite and greater than zero."
        )
    return normalized


def _close_socket(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.close()
