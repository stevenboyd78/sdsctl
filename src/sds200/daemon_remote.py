"""Fail-closed configuration for an authenticated remote daemon listener.

This module contains no socket or TLS implementation.  It models and preflights
the security material consumed by the separately constructed remote listener
while the packaged daemon remains local-only.
"""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from pathlib import Path

from .configuration import (
    DAEMON_REMOTE_CONFIG_FILENAME,
    ConfigurationPaths,
    resolve_configuration_paths,
)
from .exceptions import ConfigurationError

DAEMON_REMOTE_CONFIG_VERSION = 1
DAEMON_REMOTE_DEFAULT_PORT = 50443
DAEMON_REMOTE_MAX_TLS_FILE_BYTES = 1_048_576
DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES = 4_096
DAEMON_REMOTE_PRIVATE_FILE_MODE = 0o600

_PRIVATE_IPV4_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
_LINK_LOCAL_IPV4_NETWORK = IPv4Network("169.254.0.0/16")
_UNIQUE_LOCAL_IPV6_NETWORK = IPv6Network("fc00::/7")
_LINK_LOCAL_IPV6_NETWORK = IPv6Network("fe80::/10")


class DaemonRemoteAuthorizationScope(StrEnum):
    """Least-privilege capabilities assignable to one remote identity."""

    OBSERVE = "observe"
    CONTROL = "control"


def _require_text(value: object, *, label: str, maximum: int = 128) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if len(value) > maximum or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    ):
        raise ValueError(f"{label} must be at most {maximum} printable characters.")
    return value


def _require_client_id(value: object) -> str:
    client_id = _require_text(
        value,
        label="Remote daemon client ID",
        maximum=64,
    )
    if (
        not client_id.isascii()
        or not client_id[0].isalnum()
        or any(not (character.isalnum() or character in "._-") for character in client_id)
    ):
        raise ValueError(
            "Remote daemon client ID must start with an ASCII letter or digit "
            "and contain only ASCII letters, digits, '.', '_', or '-'."
        )
    return client_id


def _require_absolute_path(value: object, *, label: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{label} must be a pathlib.Path.")
    if not value.is_absolute():
        raise ValueError(f"{label} must be absolute.")
    return value


def _require_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Remote daemon listener port must be an integer.")
    if not 1 <= value <= 65535:
        raise ValueError("Remote daemon listener port must be between 1 and 65535.")
    return value


def _require_bind_address(value: object) -> str:
    candidate = _require_text(
        value,
        label="Remote daemon bind address",
        maximum=128,
    )
    if "://" in candidate or "/" in candidate:
        raise ValueError("Remote daemon bind address must be one literal IP address.")
    try:
        parsed = ip_address(candidate)
    except ValueError as error:
        raise ValueError("Remote daemon bind address must be one literal IP address.") from error

    allowed = False
    if isinstance(parsed, IPv4Address):
        allowed = any(parsed in network for network in _PRIVATE_IPV4_NETWORKS)
        allowed = allowed or parsed in _LINK_LOCAL_IPV4_NETWORK
    elif isinstance(parsed, IPv6Address):
        allowed = parsed in _UNIQUE_LOCAL_IPV6_NETWORK or parsed in _LINK_LOCAL_IPV6_NETWORK

    if not allowed or parsed.is_unspecified or parsed.is_multicast or parsed.is_loopback:
        raise ValueError(
            "Remote daemon bind address must be a private, unique-local, or "
            "link-local literal address; wildcard, loopback, multicast, "
            "documentation, and public addresses are unsupported."
        )
    return str(parsed)


@dataclass(frozen=True, slots=True)
class DaemonRemoteClientIdentity:
    """Secret-free metadata for one independently revocable client."""

    client_id: str
    credential_file: Path = field(repr=False)
    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = (DaemonRemoteAuthorizationScope.OBSERVE,)
    revoked: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_id", _require_client_id(self.client_id))
        object.__setattr__(
            self,
            "credential_file",
            _require_absolute_path(
                self.credential_file,
                label="Remote daemon credential file",
            ),
        )
        if type(self.scopes) is not tuple:
            raise TypeError("Remote daemon client scopes must be a tuple.")
        if not self.scopes:
            raise ValueError("Remote daemon client scopes must not be empty.")
        if any(not isinstance(scope, DaemonRemoteAuthorizationScope) for scope in self.scopes):
            raise TypeError(
                "Remote daemon client scopes must contain only "
                "DaemonRemoteAuthorizationScope values."
            )
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("Remote daemon client scopes must not contain duplicates.")
        if DaemonRemoteAuthorizationScope.OBSERVE not in self.scopes:
            raise ValueError("Remote daemon client scopes must include the observe scope.")
        object.__setattr__(
            self,
            "scopes",
            tuple(sorted(self.scopes, key=lambda scope: scope.value)),
        )
        if type(self.revoked) is not bool:
            raise TypeError("Remote daemon client revoked setting must be a boolean.")


@dataclass(frozen=True, slots=True)
class DaemonRemoteListenerConfiguration:
    """Validated configuration for an explicitly constructed remote listener."""

    enabled: bool = False
    bind_address: str | None = field(default=None, repr=False)
    port: int | None = None
    certificate_file: Path | None = field(default=None, repr=False)
    private_key_file: Path | None = field(default=None, repr=False)
    clients: tuple[DaemonRemoteClientIdentity, ...] = ()

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("Remote daemon listener enabled setting must be a boolean.")
        if not self.enabled:
            if (
                any(
                    value is not None
                    for value in (
                        self.bind_address,
                        self.port,
                        self.certificate_file,
                        self.private_key_file,
                    )
                )
                or self.clients
            ):
                raise ValueError(
                    "Disabled remote daemon listener configuration must not "
                    "contain endpoint, TLS, or client settings."
                )
            return

        object.__setattr__(
            self,
            "bind_address",
            _require_bind_address(self.bind_address),
        )
        object.__setattr__(self, "port", _require_port(self.port))
        object.__setattr__(
            self,
            "certificate_file",
            _require_absolute_path(
                self.certificate_file,
                label="Remote daemon TLS certificate file",
            ),
        )
        object.__setattr__(
            self,
            "private_key_file",
            _require_absolute_path(
                self.private_key_file,
                label="Remote daemon TLS private-key file",
            ),
        )
        if self.certificate_file == self.private_key_file:
            raise ValueError("Remote daemon TLS certificate and private-key files must differ.")
        if type(self.clients) is not tuple:
            raise TypeError("Remote daemon clients must be a tuple.")
        if not self.clients:
            raise ValueError(
                "Enabled remote daemon listener configuration requires at least "
                "one client identity."
            )
        if any(not isinstance(client, DaemonRemoteClientIdentity) for client in self.clients):
            raise TypeError(
                "Remote daemon clients must contain only DaemonRemoteClientIdentity values."
            )
        client_ids = tuple(client.client_id for client in self.clients)
        if len(set(client_ids)) != len(client_ids):
            raise ValueError("Remote daemon client IDs must be unique.")
        credential_files = tuple(client.credential_file for client in self.clients)
        if len(set(credential_files)) != len(credential_files):
            raise ValueError("Remote daemon client credential files must be unique.")
        if self.private_key_file in credential_files:
            raise ValueError("Remote daemon TLS private key must not also be a client credential.")
        if not any(not client.revoked for client in self.clients):
            raise ValueError(
                "Enabled remote daemon listener configuration requires at least "
                "one non-revoked client identity."
            )
        object.__setattr__(
            self,
            "clients",
            tuple(sorted(self.clients, key=lambda client: client.client_id)),
        )

    def as_redacted_dict(self) -> dict[str, object]:
        """Return diagnostics without private addresses or filesystem paths."""

        active = tuple(client for client in self.clients if not client.revoked)
        return {
            "version": DAEMON_REMOTE_CONFIG_VERSION,
            "enabled": self.enabled,
            "address_family": (_address_family(self.bind_address) if self.enabled else None),
            "port": self.port if self.enabled else None,
            "configured_clients": len(self.clients),
            "active_clients": len(active),
            "revoked_clients": len(self.clients) - len(active),
            "control_clients": sum(
                DaemonRemoteAuthorizationScope.CONTROL in client.scopes for client in active
            ),
        }


@dataclass(frozen=True, slots=True)
class DaemonRemoteConfigurationPreflight:
    """Non-secret filesystem metadata checked without reading file contents."""

    enabled: bool
    certificate_bytes: int = 0
    private_key_bytes: int = 0
    active_credentials: int = 0
    revoked_credentials: int = 0


def _address_family(value: str | None) -> str:
    assert value is not None
    return "ipv4" if isinstance(ip_address(value), IPv4Address) else "ipv6"


def default_daemon_remote_config_path(
    paths: ConfigurationPaths | None = None,
) -> Path:
    """Return the deterministic optional remote-listener configuration path."""

    resolved = paths or resolve_configuration_paths()
    return resolved.user_config_dir / DAEMON_REMOTE_CONFIG_FILENAME


def load_daemon_remote_configuration(
    path: str | Path | None = None,
    *,
    paths: ConfigurationPaths | None = None,
) -> DaemonRemoteListenerConfiguration | None:
    """Load one strict versioned remote-listener configuration without I/O setup."""

    if path is not None and paths is not None:
        raise ValueError("Specify a remote daemon path or configuration paths, not both.")
    config_path = default_daemon_remote_config_path(paths) if path is None else Path(path)
    if not config_path.exists():
        return None
    try:
        document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            f"Could not read remote daemon configuration {config_path}."
        ) from error

    _reject_unexpected_fields(
        document,
        allowed={"version", "listener", "clients"},
        label="Remote daemon configuration",
    )
    version = document.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != DAEMON_REMOTE_CONFIG_VERSION
    ):
        raise ConfigurationError(
            f"Remote daemon configuration version must be {DAEMON_REMOTE_CONFIG_VERSION}."
        )
    listener = document.get("listener")
    if not isinstance(listener, Mapping):
        raise ConfigurationError("Remote daemon configuration must contain a [listener] table.")
    _reject_unexpected_fields(
        listener,
        allowed={
            "enabled",
            "bind_address",
            "port",
            "certificate_file",
            "private_key_file",
        },
        label="Remote daemon listener configuration",
    )
    enabled = listener.get("enabled", False)
    if type(enabled) is not bool:
        raise ConfigurationError("Remote daemon listener enabled setting must be a boolean.")

    raw_clients = document.get("clients", [])
    if not isinstance(raw_clients, Sequence) or isinstance(raw_clients, (str, bytes, bytearray)):
        raise ConfigurationError("Remote daemon clients must be an array of tables.")
    try:
        clients = tuple(_parse_client(raw) for raw in raw_clients)
        if not enabled:
            if set(listener) != {"enabled"} or clients:
                raise ValueError(
                    "Disabled remote daemon listener configuration must not "
                    "contain endpoint, TLS, or client settings."
                )
            return DaemonRemoteListenerConfiguration()
        return DaemonRemoteListenerConfiguration(
            enabled=True,
            bind_address=_required_string(listener, "bind_address"),
            port=_optional_integer(
                listener,
                "port",
                default=DAEMON_REMOTE_DEFAULT_PORT,
            ),
            certificate_file=Path(_required_string(listener, "certificate_file")),
            private_key_file=Path(_required_string(listener, "private_key_file")),
            clients=clients,
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError("Remote daemon configuration is invalid: " + str(error)) from error


def preflight_daemon_remote_configuration(
    configuration: DaemonRemoteListenerConfiguration,
) -> DaemonRemoteConfigurationPreflight:
    """Validate file identity, size, and mode without reading secret contents."""

    if not isinstance(configuration, DaemonRemoteListenerConfiguration):
        raise TypeError("Remote daemon preflight requires DaemonRemoteListenerConfiguration.")
    if not configuration.enabled:
        return DaemonRemoteConfigurationPreflight(enabled=False)
    assert configuration.certificate_file is not None
    assert configuration.private_key_file is not None
    certificate = _preflight_regular_file(
        configuration.certificate_file,
        label="Remote daemon TLS certificate",
        maximum_bytes=DAEMON_REMOTE_MAX_TLS_FILE_BYTES,
        private=False,
    )
    private_key = _preflight_regular_file(
        configuration.private_key_file,
        label="Remote daemon TLS private key",
        maximum_bytes=DAEMON_REMOTE_MAX_TLS_FILE_BYTES,
        private=True,
    )
    active = 0
    revoked = 0
    for client in configuration.clients:
        if client.revoked:
            revoked += 1
            continue
        _preflight_regular_file(
            client.credential_file,
            label="Remote daemon client credential",
            maximum_bytes=DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES,
            private=True,
        )
        active += 1
    return DaemonRemoteConfigurationPreflight(
        enabled=True,
        certificate_bytes=certificate.st_size,
        private_key_bytes=private_key.st_size,
        active_credentials=active,
        revoked_credentials=revoked,
    )


def _preflight_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    private: bool,
) -> os.stat_result:
    try:
        observed = path.lstat()
    except OSError as error:
        raise ConfigurationError(f"{label} file is unavailable.") from error
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise ConfigurationError(f"{label} must be one regular file.")
    if not 1 <= observed.st_size <= maximum_bytes:
        raise ConfigurationError(f"{label} size must be between 1 and {maximum_bytes} bytes.")
    if (
        private
        and os.name == "posix"
        and stat.S_IMODE(observed.st_mode) != DAEMON_REMOTE_PRIVATE_FILE_MODE
    ):
        raise ConfigurationError(f"{label} must have POSIX mode 0600.")
    return observed


def _parse_client(raw: object) -> DaemonRemoteClientIdentity:
    if not isinstance(raw, Mapping):
        raise TypeError("Each remote daemon client must be a TOML table.")
    _reject_unexpected_fields(
        raw,
        allowed={"client_id", "credential_file", "scopes", "revoked"},
        label="Remote daemon client configuration",
    )
    raw_scopes = raw.get("scopes", [DaemonRemoteAuthorizationScope.OBSERVE.value])
    if not isinstance(raw_scopes, Sequence) or isinstance(raw_scopes, (str, bytes, bytearray)):
        raise TypeError("Remote daemon client scopes must be an array.")
    scopes: list[DaemonRemoteAuthorizationScope] = []
    for raw_scope in raw_scopes:
        if type(raw_scope) is not str:
            raise TypeError("Remote daemon client scope values must be strings.")
        try:
            scopes.append(DaemonRemoteAuthorizationScope(raw_scope))
        except ValueError as error:
            raise ValueError("Remote daemon client scope is unsupported.") from error
    revoked = raw.get("revoked", False)
    if type(revoked) is not bool:
        raise TypeError("Remote daemon client revoked setting must be a boolean.")
    return DaemonRemoteClientIdentity(
        client_id=_required_string(raw, "client_id"),
        credential_file=Path(_required_string(raw, "credential_file")),
        scopes=tuple(scopes),
        revoked=revoked,
    )


def _reject_unexpected_fields(
    value: Mapping[str, object],
    *,
    allowed: set[str],
    label: str,
) -> None:
    unexpected = sorted(str(field) for field in value if field not in allowed)
    if unexpected:
        rendered = ", ".join(repr(field) for field in unexpected)
        raise ConfigurationError(f"{label} has unsupported field(s): {rendered}.")


def _required_string(value: Mapping[str, object], field_name: str) -> str:
    candidate = value.get(field_name)
    if type(candidate) is not str:
        raise TypeError(f"Remote daemon {field_name.replace('_', ' ')} must be a string.")
    return candidate


def _optional_integer(
    value: Mapping[str, object],
    field_name: str,
    *,
    default: int,
) -> int:
    candidate = value.get(field_name, default)
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise TypeError(f"Remote daemon {field_name.replace('_', ' ')} must be an integer.")
    return candidate


__all__ = [
    "DAEMON_REMOTE_CONFIG_VERSION",
    "DAEMON_REMOTE_DEFAULT_PORT",
    "DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES",
    "DAEMON_REMOTE_MAX_TLS_FILE_BYTES",
    "DAEMON_REMOTE_PRIVATE_FILE_MODE",
    "DaemonRemoteAuthorizationScope",
    "DaemonRemoteClientIdentity",
    "DaemonRemoteConfigurationPreflight",
    "DaemonRemoteListenerConfiguration",
    "default_daemon_remote_config_path",
    "load_daemon_remote_configuration",
    "preflight_daemon_remote_configuration",
]
