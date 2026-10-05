"""Explicit local administrator configuration for one daemon's display profile.

This is not an App options schema, credential profile or discovery mechanism.
Untrusted callers never choose filesystem paths through the daemon API.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .scanner_display_profile_state import DisplayProfileBinding, DisplayProfileSourceKind
from .scanner_display_profile_storage import (
    DisplayProfileStorageError,
    PersistentScannerDisplayProfile,
    _directory,
    _path,
    _read_file,
    _same_directory,
)

MAX_DISPLAY_CONFIGURATION_BYTES = 16384


class ScannerDisplayConfigurationError(ValueError):
    def __init__(self) -> None:
        super().__init__("Scanner display configuration is invalid or unavailable.")


@dataclass(frozen=True, slots=True, repr=False)
class ScannerDisplayConfiguration:
    binding: DisplayProfileBinding
    scanner_target: str
    source_path: Path
    state_directory: Path

    def __post_init__(self) -> None:
        try:
            if (
                not isinstance(self.binding, DisplayProfileBinding)
                or self.binding.source_kind is not DisplayProfileSourceKind.MANUAL_IMPORT
                or not isinstance(self.scanner_target, str)
                or not 1 <= len(self.scanner_target) <= 512
                or any(not 33 <= ord(char) <= 126 for char in self.scanner_target)
            ):
                raise ScannerDisplayConfigurationError()
            _path(self.source_path)
            _path(self.state_directory)
            for path in (self.source_path, self.state_directory):
                if len(str(path)) > 4096 or any(
                    ord(char) < 32 or ord(char) == 127 for char in str(path)
                ):
                    raise ScannerDisplayConfigurationError()
            if self.source_path.is_relative_to(self.state_directory):
                raise ScannerDisplayConfigurationError()
        except DisplayProfileStorageError:
            raise ScannerDisplayConfigurationError() from None

    def repository(self) -> PersistentScannerDisplayProfile:
        return PersistentScannerDisplayProfile(
            source_path=self.source_path,
            state_directory=self.state_directory,
            endpoint_id=self.binding.endpoint_id,
        )

    def require_scanner_target(self, target: str) -> None:
        if target != self.scanner_target:
            raise ScannerDisplayConfigurationError()

    def require_separate_recordings(self, recording_directory: Path) -> None:
        """Keep profile source/state outside recording inventory and retention roots."""
        try:
            # Resolve aliases for the recording root only; profile paths have separate
            # no-follow validation. No directory is created and no profile is read.
            root = recording_directory.resolve()
            for path in (self.source_path, self.state_directory):
                if path.is_relative_to(root) or root.is_relative_to(path):
                    raise ScannerDisplayConfigurationError()
        except (OSError, RuntimeError):
            raise ScannerDisplayConfigurationError() from None


def parse_scanner_display_configuration(data: bytes) -> ScannerDisplayConfiguration:
    try:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_DISPLAY_CONFIGURATION_BYTES:
            raise ScannerDisplayConfigurationError()
        values = tomllib.loads(data.decode("utf-8"))
        expected = {
            "version",
            "endpoint_id",
            "source_id",
            "scanner_target",
            "source_path",
            "state_directory",
        }
        if (
            set(values) != expected
            or type(values["version"]) is not int
            or values["version"] != 1
            or any(type(value) is not str for key, value in values.items() if key != "version")
        ):
            raise ScannerDisplayConfigurationError()
        endpoint, source = UUID(values["endpoint_id"]), UUID(values["source_id"])
        if str(endpoint) != values["endpoint_id"] or str(source) != values["source_id"]:
            raise ScannerDisplayConfigurationError()
        return ScannerDisplayConfiguration(
            DisplayProfileBinding(endpoint, source, DisplayProfileSourceKind.MANUAL_IMPORT),
            values["scanner_target"],
            Path(values["source_path"]),
            Path(values["state_directory"]),
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ScannerDisplayConfigurationError() from None


def _read_configuration(path: Path) -> bytes:
    """Pinned bounded read shared by the profile and private deployment manifests."""
    directory = None
    try:
        path = _path(path)
        if len(str(path)) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in str(path)):
            raise ScannerDisplayConfigurationError()
        directory = _directory(path.parent)
        file = _read_file(directory, path.name, MAX_DISPLAY_CONFIGURATION_BYTES, private=False)
        # _File.identity is the pinned reader's dev/inode/size/times/mode/uid/... tuple.
        mode, owner = file.identity[5:7]
        if owner not in (0, os.geteuid()) or stat.S_IMODE(mode) & 0o022:
            raise ScannerDisplayConfigurationError()
        _same_directory(path.parent, directory)
        return file.data
    except (OSError, DisplayProfileStorageError):
        raise ScannerDisplayConfigurationError() from None
    finally:
        if directory is not None:
            os.close(directory)


def load_scanner_display_configuration(path: Path) -> ScannerDisplayConfiguration:
    """Read a bounded regular config, owned by root/service and not writable by others."""
    config = parse_scanner_display_configuration(_read_configuration(path))
    if path == config.source_path or path.is_relative_to(config.state_directory):
        raise ScannerDisplayConfigurationError()
    return config
