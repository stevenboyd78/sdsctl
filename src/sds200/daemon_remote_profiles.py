"""Strict named profiles for packaged authenticated daemon clients."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .configuration import ConfigurationPaths, resolve_configuration_paths
from .daemon_remote import DAEMON_REMOTE_DEFAULT_PORT
from .daemon_remote_client import DaemonRemoteClientConfiguration
from .exceptions import ConfigurationError

DAEMON_REMOTE_CLIENT_PROFILES_VERSION = 1
_PROFILE_FIELDS = frozenset(
    {
        "address",
        "port",
        "server_hostname",
        "certificate_file",
        "client_id",
        "credential_file",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class DaemonRemoteClientProfiles:
    """Immutable named remote-client configurations with redacted identity."""

    profiles: Mapping[str, DaemonRemoteClientConfiguration] = field(
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.profiles, Mapping):
            raise TypeError("Remote daemon client profiles must be a mapping.")
        normalized: dict[str, DaemonRemoteClientConfiguration] = {}
        for name, configuration in self.profiles.items():
            normalized_name = _profile_name(name)
            if normalized_name in normalized:
                raise ValueError("Remote daemon client profile names must be unique.")
            if not isinstance(configuration, DaemonRemoteClientConfiguration):
                raise TypeError(
                    "Remote daemon client profiles must contain only "
                    "DaemonRemoteClientConfiguration values."
                )
            normalized[normalized_name] = configuration
        object.__setattr__(
            self,
            "profiles",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    def __repr__(self) -> str:
        return f"DaemonRemoteClientProfiles(count={len(self.profiles)})"

    def select(self, name: str) -> DaemonRemoteClientConfiguration:
        """Return one exact named profile without disclosing its identity."""

        normalized = _profile_name(name)
        try:
            return self.profiles[normalized]
        except KeyError as error:
            raise ConfigurationError(
                "Selected remote daemon client profile is unavailable."
            ) from error


def default_daemon_remote_client_profiles_path(
    paths: ConfigurationPaths | None = None,
) -> Path:
    """Return the deterministic optional remote-client profile document."""

    resolved = paths or resolve_configuration_paths()
    return resolved.daemon_remote_client_profiles_file


def load_daemon_remote_client_profiles(
    path: str | Path | None = None,
    *,
    paths: ConfigurationPaths | None = None,
) -> DaemonRemoteClientProfiles:
    """Load one strict versioned profile document without selecting a profile."""

    if path is not None and paths is not None:
        raise ValueError(
            "Specify a remote daemon client profile path or configuration "
            "paths, not both."
        )
    profile_path = (
        default_daemon_remote_client_profiles_path(paths)
        if path is None
        else Path(path)
    )
    if not profile_path.exists():
        return DaemonRemoteClientProfiles()
    try:
        document = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            "Could not read remote daemon client profile configuration."
        ) from error

    _reject_unexpected_fields(
        document,
        allowed={"version", "profiles"},
        label="Remote daemon client profile configuration",
    )
    version = document.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != DAEMON_REMOTE_CLIENT_PROFILES_VERSION
    ):
        raise ConfigurationError(
            "Remote daemon client profile configuration version must be "
            f"{DAEMON_REMOTE_CLIENT_PROFILES_VERSION}."
        )

    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, Mapping):
        raise ConfigurationError(
            "Remote daemon client profile configuration must contain a "
            "[profiles] table."
        )

    parsed: dict[str, DaemonRemoteClientConfiguration] = {}
    for raw_name, raw_profile in raw_profiles.items():
        try:
            name = _profile_name(raw_name)
            if not isinstance(raw_profile, Mapping):
                raise TypeError(
                    "Each remote daemon client profile must be a TOML table."
                )
            _reject_unexpected_fields(
                raw_profile,
                allowed=_PROFILE_FIELDS,
                label="Remote daemon client profile",
            )
            parsed[name] = _parse_profile(raw_profile)
        except ConfigurationError:
            raise
        except (TypeError, ValueError) as error:
            raise ConfigurationError(
                "Remote daemon client profile configuration is invalid."
            ) from error
    return DaemonRemoteClientProfiles(parsed)


def _parse_profile(
    raw: Mapping[Any, Any],
) -> DaemonRemoteClientConfiguration:
    return DaemonRemoteClientConfiguration(
        address=_required_string(raw, "address"),
        port=_optional_port(raw),
        server_hostname=_required_string(raw, "server_hostname"),
        certificate_file=Path(_required_string(raw, "certificate_file")),
        client_id=_required_string(raw, "client_id"),
        credential_file=Path(_required_string(raw, "credential_file")),
    )


def _profile_name(value: object) -> str:
    if type(value) is not str:
        raise TypeError("Remote daemon client profile name must be a string.")
    if (
        not value
        or len(value) > 64
        or not value.isascii()
        or not value[0].isalnum()
        or any(
            not (character.isalnum() or character in "._-")
            for character in value
        )
    ):
        raise ValueError(
            "Remote daemon client profile name must start with an ASCII "
            "letter or digit and contain only ASCII letters, digits, '.', "
            "'_', or '-'."
        )
    return value


def _reject_unexpected_fields(
    value: Mapping[Any, Any],
    *,
    allowed: set[str] | frozenset[str],
    label: str,
) -> None:
    unexpected = sorted(
        str(field)
        for field in value
        if not isinstance(field, str) or field not in allowed
    )
    if unexpected:
        rendered = ", ".join(repr(field) for field in unexpected)
        raise ConfigurationError(
            f"{label} has unsupported field(s): {rendered}."
        )


def _required_string(
    value: Mapping[Any, Any],
    field_name: str,
) -> str:
    candidate = value.get(field_name)
    if type(candidate) is not str:
        raise TypeError(
            "Remote daemon client profile "
            f"{field_name.replace('_', ' ')} must be a string."
        )
    return candidate


def _optional_port(value: Mapping[Any, Any]) -> int:
    candidate = value.get("port", DAEMON_REMOTE_DEFAULT_PORT)
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise TypeError("Remote daemon client profile port must be an integer.")
    return candidate


__all__ = [
    "DAEMON_REMOTE_CLIENT_PROFILES_VERSION",
    "DaemonRemoteClientProfiles",
    "default_daemon_remote_client_profiles_path",
    "load_daemon_remote_client_profiles",
]
