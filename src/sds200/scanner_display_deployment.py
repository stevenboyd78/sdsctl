"""Opt-in App/Ingress wiring for an already provisioned display profile.

One deployment file selects the daemon profile manifest and the disjoint
administrator audience. Loading it never creates files, imports a profile,
connects to a scanner, repairs permissions, or enables a network listener.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .scanner_display_configuration import (
    MAX_DISPLAY_CONFIGURATION_BYTES,
    ScannerDisplayConfiguration,
    ScannerDisplayConfigurationError,
    _read_configuration,
    load_scanner_display_configuration,
)
from .scanner_display_profile_storage import DisplayProfileStorageError, _path


@dataclass(frozen=True, slots=True, repr=False)
class ScannerDisplayDeployment:
    profile_config: Path
    ingress_origin: str
    admin_user_ids: frozenset[str]
    allow_upload: bool

    def __post_init__(self) -> None:
        # Optional web dependencies are loaded only for this explicit opt-in.
        from .web_auth import _normalize_origin

        try:
            _path(self.profile_config)
            if (
                len(str(self.profile_config)) > 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in str(self.profile_config))
                or type(self.ingress_origin) is not str
                or len(self.ingress_origin) > 2048
                or _normalize_origin(self.ingress_origin) != self.ingress_origin
                or type(self.admin_user_ids) is not frozenset
                or not 1 <= len(self.admin_user_ids) <= 32
                or any(
                    type(uid) is not str or re.fullmatch(r"[a-f0-9]{32}", uid) is None
                    for uid in self.admin_user_ids
                )
                or type(self.allow_upload) is not bool
            ):
                raise ScannerDisplayConfigurationError()
        except (ValueError, TypeError, DisplayProfileStorageError):
            raise ScannerDisplayConfigurationError() from None

    def preflight(self, recording_directory: Path) -> ScannerDisplayConfiguration:
        """Inspect existing state and upload target without staging or importing."""
        from .scanner_display_upload import _observe_source

        try:
            _path(recording_directory)
            config = load_scanner_display_configuration(self.profile_config)
            config.require_separate_recordings(recording_directory)
            if self.profile_config.is_relative_to(recording_directory.resolve()):
                raise ScannerDisplayConfigurationError()
            config.repository().inspect()
            if self.allow_upload:
                # A missing managed file is allowed, but its private parent must
                # already exist. No mkdir/chmod or adoption of an unsafe old copy.
                _observe_source(config.source_path)
            return config
        except (OSError, RuntimeError):
            raise ScannerDisplayConfigurationError() from None


def parse_scanner_display_deployment(data: bytes) -> ScannerDisplayDeployment:
    try:
        if type(data) is not bytes or not 0 < len(data) <= MAX_DISPLAY_CONFIGURATION_BYTES:
            raise ScannerDisplayConfigurationError()
        values = tomllib.loads(data.decode("utf-8"))
        if (
            set(values)
            != {"version", "profile_config", "ingress_origin", "admin_user_ids", "allow_upload"}
            or type(values["version"]) is not int
            or values["version"] != 1
            or type(values["profile_config"]) is not str
            or type(values["admin_user_ids"]) is not list
            or any(type(uid) is not str for uid in values["admin_user_ids"])
            or len(set(values["admin_user_ids"])) != len(values["admin_user_ids"])
        ):
            raise ScannerDisplayConfigurationError()
        return ScannerDisplayDeployment(
            Path(values["profile_config"]),
            values["ingress_origin"],
            frozenset(values["admin_user_ids"]),
            values["allow_upload"],
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ScannerDisplayConfigurationError() from None


def load_scanner_display_deployment(path: Path) -> ScannerDisplayDeployment:
    deployment = parse_scanner_display_deployment(_read_configuration(path))
    config = load_scanner_display_configuration(deployment.profile_config)
    if path == config.source_path or path.is_relative_to(config.state_directory):
        raise ScannerDisplayConfigurationError()
    return deployment
