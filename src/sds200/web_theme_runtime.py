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
from .web_themes import (
    WebThemeError,
    WebThemeManifest,
    WebThemeRegistry,
    built_in_web_theme_registry,
    read_built_in_web_theme_stylesheet,
)

WebThemeAssetOrigin: TypeAlias = Literal["built-in", "managed"]


@dataclass(frozen=True, slots=True)
class WebThemeRuntimeAsset:
    """One immutable startup-qualified web-theme asset."""

    manifest: WebThemeManifest
    origin: WebThemeAssetOrigin
    managed_root: Path | None = None
    package_sha256: str | None = None
    directory_device: int | None = None
    directory_inode: int | None = None


@dataclass(frozen=True, slots=True)
class WebThemeRuntimeRegistry:
    """Built-in and startup-qualified managed themes for one web process."""

    registry: WebThemeRegistry
    assets: tuple[WebThemeRuntimeAsset, ...]
    ignored_managed_entries: int = 0

    def __post_init__(self) -> None:
        if tuple(asset.manifest for asset in self.assets) != self.registry.themes:
            raise WebThemeError("web theme runtime assets do not match the registry")

    @property
    def managed_identifiers(self) -> tuple[str, ...]:
        return tuple(
            asset.manifest.identifier for asset in self.assets if asset.origin == "managed"
        )

    def require_asset(self, identifier: str) -> WebThemeRuntimeAsset:
        for asset in self.assets:
            if asset.manifest.identifier == identifier:
                return asset
        raise WebThemeError(f"unknown web theme: {identifier}")


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


def _read_regular_file(directory: int, name: str) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=directory)
    except OSError as exc:
        raise WebThemeError("managed web theme asset is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise WebThemeError("managed web theme package contains a non-regular file")
        if before.st_size > THEME_PACKAGE_MAX_BYTES:
            raise WebThemeError("managed web theme package exceeds the size limit")
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
            raise WebThemeError("managed web theme package exceeds the size limit")
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
            raise WebThemeError("managed web theme package changed while being read")
        return content, after
    except OSError as exc:
        raise WebThemeError("managed web theme asset could not be read") from exc
    finally:
        os.close(descriptor)


def _package_bytes(
    root: Path,
    manifest: WebThemeManifest,
) -> tuple[bytes, str, os.stat_result]:
    descriptors: list[int] = []
    try:
        root_descriptor = os.open(root, _directory_flags())
        descriptors.append(root_descriptor)
        interface_descriptor = os.open("web", _directory_flags(), dir_fd=root_descriptor)
        descriptors.append(interface_descriptor)
        package_descriptor = os.open(
            manifest.identifier,
            _directory_flags(),
            dir_fd=interface_descriptor,
        )
        descriptors.append(package_descriptor)
        directory_status = os.fstat(package_descriptor)
        expected_files = {THEME_MANIFEST_FILENAME, manifest.stylesheet}
        if set(_bounded_package_names(package_descriptor)) != expected_files:
            raise WebThemeError("managed web theme package contents changed after startup")
        contents: dict[str, bytes] = {}
        total_bytes = 0
        for name in sorted(expected_files):
            content, _ = _read_regular_file(package_descriptor, name)
            total_bytes += len(content)
            if total_bytes > THEME_PACKAGE_MAX_BYTES:
                raise WebThemeError("managed web theme package exceeds the size limit")
            contents[name] = content
        digest = hashlib.sha256()
        for name in sorted(expected_files):
            encoded_name = name.encode("utf-8")
            content = contents[name]
            digest.update(len(encoded_name).to_bytes(4, "big"))
            digest.update(encoded_name)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
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
            raise WebThemeError("managed web theme package changed while being read")
        return (
            contents[manifest.stylesheet],
            digest.hexdigest(),
            final_directory_status,
        )
    except WebThemeError:
        raise
    except SDS200Error as exc:
        raise WebThemeError("managed web theme asset is unavailable") from exc
    except OSError as exc:
        raise WebThemeError("managed web theme asset is unavailable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _managed_runtime_asset(
    root: Path,
    path: Path,
    package_sha256: str,
) -> WebThemeRuntimeAsset:
    expected_path = root / "web" / path.name
    if path != expected_path:
        raise WebThemeError("managed web theme package path is outside its interface root")
    validated = validate_theme_package(path)
    if not isinstance(validated.manifest, WebThemeManifest):
        raise WebThemeError("managed theme package is not a web theme")
    manifest = validated.manifest
    if validated.summary.sha256 != package_sha256:
        raise WebThemeError("managed web theme package changed after discovery")
    _, secure_digest, directory_status = _package_bytes(root, manifest)
    if secure_digest != package_sha256:
        raise WebThemeError("managed web theme package changed after discovery")
    return WebThemeRuntimeAsset(
        manifest=manifest,
        origin="managed",
        managed_root=root,
        package_sha256=package_sha256,
        directory_device=directory_status.st_dev,
        directory_inode=directory_status.st_ino,
    )


def build_web_theme_runtime(
    managed_root: Path | None = None,
) -> WebThemeRuntimeRegistry:
    """Build one immutable web-theme registry for a dashboard process."""

    built_in_registry = built_in_web_theme_registry()
    assets = [
        WebThemeRuntimeAsset(manifest=manifest, origin="built-in")
        for manifest in built_in_registry.themes
    ]
    if managed_root is None:
        return WebThemeRuntimeRegistry(registry=built_in_registry, assets=tuple(assets))
    if not isinstance(managed_root, Path):
        raise TypeError("Managed web theme root must be a pathlib.Path or None")
    if not managed_root.is_absolute():
        raise ValueError("Managed web theme root must be absolute")

    try:
        inventory = discover_theme_inventory(managed_root)
    except (SDS200Error, OSError):
        return WebThemeRuntimeRegistry(
            registry=built_in_registry,
            assets=tuple(assets),
            ignored_managed_entries=1,
        )

    ignored_entries = len(inventory.issues)
    for summary in inventory.packages:
        if summary.interface != "web" or summary.origin != "managed":
            continue
        if summary.path is None or summary.sha256 is None:
            ignored_entries += 1
            continue
        try:
            assets.append(
                _managed_runtime_asset(
                    managed_root,
                    summary.path,
                    summary.sha256,
                )
            )
        except (SDS200Error, OSError, TypeError, ValueError):
            ignored_entries += 1

    ordered_assets = tuple(sorted(assets, key=lambda asset: asset.manifest.order))
    registry = WebThemeRegistry(tuple(asset.manifest for asset in ordered_assets))
    return WebThemeRuntimeRegistry(
        registry=registry,
        assets=ordered_assets,
        ignored_managed_entries=ignored_entries,
    )


def read_web_theme_stylesheet(asset: WebThemeRuntimeAsset) -> bytes:
    """Read one runtime-qualified stylesheet or fail closed after any change."""

    if asset.origin == "built-in":
        return read_built_in_web_theme_stylesheet(asset.manifest)
    if (
        asset.managed_root is None
        or asset.package_sha256 is None
        or asset.directory_device is None
        or asset.directory_inode is None
    ):
        raise WebThemeError("managed web theme asset metadata is incomplete")
    content, digest, directory_status = _package_bytes(
        asset.managed_root,
        asset.manifest,
    )
    if (directory_status.st_dev, directory_status.st_ino) != (
        asset.directory_device,
        asset.directory_inode,
    ):
        raise WebThemeError("managed web theme package was replaced after startup")
    if digest != asset.package_sha256:
        raise WebThemeError("managed web theme package changed after startup")
    return content


__all__ = [
    "WebThemeRuntimeAsset",
    "WebThemeRuntimeRegistry",
    "build_web_theme_runtime",
    "read_web_theme_stylesheet",
]
