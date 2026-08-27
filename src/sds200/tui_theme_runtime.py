from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from .exceptions import SDS200Error
from .theme_lifecycle import (
    THEME_MANIFEST_FILENAME,
    THEME_PACKAGE_MAX_BYTES,
    _bounded_package_names,
    discover_theme_inventory,
    validate_theme_package,
)
from .tui_themes import (
    TuiThemeError,
    TuiThemeManifest,
    TuiThemeRegistry,
    built_in_tui_theme_registry,
    read_built_in_tui_theme_stylesheet,
    validate_managed_tui_theme_stylesheet,
)

TuiThemeAssetOrigin: TypeAlias = Literal["built-in", "managed"]


@dataclass(frozen=True, slots=True)
class TuiThemeRuntimeAsset:
    """One immutable startup-qualified terminal theme."""

    manifest: TuiThemeManifest
    origin: TuiThemeAssetOrigin
    stylesheet: str


@dataclass(frozen=True, slots=True)
class TuiThemeRuntimeRegistry:
    """Built-in and startup-qualified managed themes for one terminal command."""

    registry: TuiThemeRegistry
    assets: tuple[TuiThemeRuntimeAsset, ...]
    ignored_managed_entries: int = 0

    def __post_init__(self) -> None:
        if tuple(asset.manifest for asset in self.assets) != self.registry.themes:
            raise TuiThemeError("TUI theme runtime assets do not match the registry")

    @property
    def managed_identifiers(self) -> tuple[str, ...]:
        return tuple(
            asset.manifest.identifier for asset in self.assets if asset.origin == "managed"
        )

    def require_asset(self, identifier: str) -> TuiThemeRuntimeAsset:
        normalized = identifier.strip().casefold()
        for asset in self.assets:
            if asset.manifest.identifier == normalized:
                return asset
        choices = ", ".join(self.registry.identifiers)
        raise TuiThemeError(f"unknown terminal theme {identifier!r}; available themes: {choices}")


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _file_flags() -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _read_regular_file(directory: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=directory)
    except OSError as exc:
        raise TuiThemeError("managed TUI theme asset is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise TuiThemeError("managed TUI theme package contains a non-regular file")
        if before.st_size > THEME_PACKAGE_MAX_BYTES:
            raise TuiThemeError("managed TUI theme package exceeds the size limit")
        chunks: list[bytes] = []
        remaining = THEME_PACKAGE_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > THEME_PACKAGE_MAX_BYTES:
            raise TuiThemeError("managed TUI theme package exceeds the size limit")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise TuiThemeError("managed TUI theme package changed while being read")
        return content
    except OSError as exc:
        raise TuiThemeError("managed TUI theme asset could not be read") from exc
    finally:
        os.close(descriptor)


def _secure_stylesheet(
    root: Path,
    manifest: TuiThemeManifest,
    expected_digest: str,
) -> str:
    descriptors: list[int] = []
    try:
        root_descriptor = os.open(root, _directory_flags())
        descriptors.append(root_descriptor)
        interface_descriptor = os.open("tui", _directory_flags(), dir_fd=root_descriptor)
        descriptors.append(interface_descriptor)
        package_descriptor = os.open(
            manifest.identifier,
            _directory_flags(),
            dir_fd=interface_descriptor,
        )
        descriptors.append(package_descriptor)
        directory_status = os.fstat(package_descriptor)
        expected_files = {
            THEME_MANIFEST_FILENAME,
            manifest.palette_filename,
            manifest.stylesheet,
        }
        if set(_bounded_package_names(package_descriptor)) != expected_files:
            raise TuiThemeError("managed TUI theme package contents changed after discovery")
        contents: dict[str, bytes] = {}
        total_bytes = 0
        for name in sorted(expected_files):
            content = _read_regular_file(package_descriptor, name)
            total_bytes += len(content)
            if total_bytes > THEME_PACKAGE_MAX_BYTES:
                raise TuiThemeError("managed TUI theme package exceeds the size limit")
            contents[name] = content
        digest = hashlib.sha256()
        for name in sorted(expected_files):
            encoded_name = name.encode("utf-8")
            content = contents[name]
            digest.update(len(encoded_name).to_bytes(4, "big"))
            digest.update(encoded_name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        if digest.hexdigest() != expected_digest:
            raise TuiThemeError("managed TUI theme package changed after discovery")
        if set(_bounded_package_names(package_descriptor)) != expected_files:
            raise TuiThemeError("managed TUI theme package changed while being read")
        final_directory_status = os.fstat(package_descriptor)
        if (
            directory_status.st_dev,
            directory_status.st_ino,
            directory_status.st_mtime_ns,
            directory_status.st_ctime_ns,
        ) != (
            final_directory_status.st_dev,
            final_directory_status.st_ino,
            final_directory_status.st_mtime_ns,
            final_directory_status.st_ctime_ns,
        ):
            raise TuiThemeError("managed TUI theme package changed while being read")
        try:
            return contents[manifest.stylesheet].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TuiThemeError("managed TUI stylesheet is not UTF-8 text") from exc
    except TuiThemeError:
        raise
    except SDS200Error as exc:
        raise TuiThemeError("managed TUI theme asset is unavailable") from exc
    except OSError as exc:
        raise TuiThemeError("managed TUI theme asset is unavailable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def build_tui_theme_runtime(
    managed_root: Path | None = None,
) -> TuiThemeRuntimeRegistry:
    """Build one immutable terminal-theme registry for a command process."""

    built_in_registry = built_in_tui_theme_registry()
    assets = [
        TuiThemeRuntimeAsset(
            manifest=manifest,
            origin="built-in",
            stylesheet=read_built_in_tui_theme_stylesheet(manifest),
        )
        for manifest in built_in_registry.themes
    ]
    if managed_root is None:
        return TuiThemeRuntimeRegistry(registry=built_in_registry, assets=tuple(assets))
    if not isinstance(managed_root, Path):
        raise TypeError("Managed TUI theme root must be a pathlib.Path or None")
    if not managed_root.is_absolute():
        raise ValueError("Managed TUI theme root must be absolute")

    try:
        inventory = discover_theme_inventory(managed_root)
    except (SDS200Error, OSError):
        return TuiThemeRuntimeRegistry(
            registry=built_in_registry,
            assets=tuple(assets),
            ignored_managed_entries=1,
        )

    ignored_entries = len(inventory.issues)
    for summary in inventory.packages:
        if summary.interface != "tui" or summary.origin != "managed":
            continue
        if summary.path is None or summary.sha256 is None:
            ignored_entries += 1
            continue
        try:
            expected_path = managed_root / "tui" / summary.identifier
            if summary.path != expected_path:
                raise TuiThemeError("managed TUI theme package path is outside its interface root")
            validated = validate_theme_package(summary.path)
            if not isinstance(validated.manifest, TuiThemeManifest):
                raise TuiThemeError("managed theme package is not a TUI theme")
            if validated.summary.sha256 != summary.sha256:
                raise TuiThemeError("managed TUI theme package changed after discovery")
            stylesheet = _secure_stylesheet(
                managed_root,
                validated.manifest,
                summary.sha256,
            )
            validate_managed_tui_theme_stylesheet(validated.manifest, stylesheet)
            assets.append(
                TuiThemeRuntimeAsset(
                    manifest=validated.manifest,
                    origin="managed",
                    stylesheet=stylesheet,
                )
            )
        except (SDS200Error, OSError, TypeError, ValueError):
            ignored_entries += 1

    ordered_assets = tuple(sorted(assets, key=lambda asset: asset.manifest.order))
    registry = TuiThemeRegistry(tuple(asset.manifest for asset in ordered_assets))
    return TuiThemeRuntimeRegistry(
        registry=registry,
        assets=ordered_assets,
        ignored_managed_entries=ignored_entries,
    )


__all__ = [
    "TuiThemeRuntimeAsset",
    "TuiThemeRuntimeRegistry",
    "build_tui_theme_runtime",
]
