from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias

from .exceptions import SDS200Error
from .home_assistant_themes import (
    BUILT_IN_HOME_ASSISTANT_THEME_IDS,
    HomeAssistantThemeManifest,
    HomeAssistantThemeRegistry,
    built_in_home_assistant_theme_registry,
    load_home_assistant_theme_package,
)
from .tui_themes import (
    BUILT_IN_TUI_THEME_IDS,
    TuiThemeManifest,
    TuiThemeRegistry,
    built_in_tui_theme_registry,
    load_tui_theme_package,
)
from .web_themes import (
    BUILT_IN_WEB_THEME_IDS,
    WebThemeManifest,
    WebThemeRegistry,
    built_in_web_theme_registry,
    load_web_theme_package,
)

ThemeInterface: TypeAlias = Literal["web", "home-assistant", "tui"]
ThemeOrigin: TypeAlias = Literal["built-in", "managed"]
ThemeManifest: TypeAlias = WebThemeManifest | HomeAssistantThemeManifest | TuiThemeManifest

THEME_INTERFACES: Final[tuple[ThemeInterface, ...]] = (
    "web",
    "home-assistant",
    "tui",
)
THEME_MANIFEST_FILENAME: Final = "manifest.json"
THEME_PACKAGE_MAX_FILES: Final = 8
THEME_PACKAGE_MAX_BYTES: Final = 4 * 1024 * 1024
THEME_DIRECTORY_MODE: Final = 0o700
THEME_FILE_MODE: Final = 0o600
HOME_ASSISTANT_CODE_TRUST_TOKEN: Final = "I-TRUST-THIS-HOME-ASSISTANT-CODE"

_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_LOCK_FILENAME: Final = ".lifecycle.lock"
_STAGE_PREFIX: Final = ".sdsctl-stage-"
_ROLLBACK_PREFIX: Final = ".sdsctl-rollback-"
_REMOVE_PREFIX: Final = ".sdsctl-remove-"


class ThemeLifecycleError(SDS200Error):
    """Raised when managed theme discovery or mutation cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class ThemePackageSummary:
    """Stable operator-facing metadata for one valid theme package."""

    interface: ThemeInterface
    identifier: str
    label: str
    order: int
    origin: ThemeOrigin
    executable: bool
    path: Path | None
    sha256: str | None

    @property
    def identity(self) -> str:
        return f"{self.interface}/{self.identifier}"

    def as_dict(self) -> dict[str, object]:
        return {
            "interface": self.interface,
            "id": self.identifier,
            "identity": self.identity,
            "label": self.label,
            "order": self.order,
            "origin": self.origin,
            "executable": self.executable,
            "path": str(self.path) if self.path is not None else None,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ThemeDiscoveryIssue:
    """One isolated invalid entry under the managed theme root."""

    interface: str | None
    identifier: str | None
    path: Path
    message: str

    @property
    def identity(self) -> str | None:
        if self.interface is None or self.identifier is None:
            return None
        return f"{self.interface}/{self.identifier}"

    def as_dict(self) -> dict[str, object]:
        return {
            "interface": self.interface,
            "id": self.identifier,
            "identity": self.identity,
            "path": str(self.path),
            "error": self.message,
        }


@dataclass(frozen=True, slots=True)
class ThemeInventory:
    """Immutable built-in plus managed theme discovery result."""

    root: Path
    packages: tuple[ThemePackageSummary, ...]
    issues: tuple[ThemeDiscoveryIssue, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "packages": [package.as_dict() for package in self.packages],
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class ValidatedThemePackage:
    """One source directory that passed its complete interface contract."""

    summary: ThemePackageSummary
    manifest: ThemeManifest


@dataclass(frozen=True, slots=True)
class ThemeRemoval:
    """Evidence for one completed managed-package removal."""

    interface: ThemeInterface
    identifier: str
    path: Path

    @property
    def identity(self) -> str:
        return f"{self.interface}/{self.identifier}"

    def as_dict(self) -> dict[str, object]:
        return {
            "interface": self.interface,
            "id": self.identifier,
            "identity": self.identity,
            "path": str(self.path),
            "removed": True,
        }


def _absolute_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise TypeError("Managed theme root must be a pathlib.Path")
    if not root.is_absolute():
        raise ThemeLifecycleError("managed theme root must be absolute")
    return root


def _theme_interface(value: object) -> ThemeInterface:
    if value not in THEME_INTERFACES:
        choices = ", ".join(THEME_INTERFACES)
        raise ThemeLifecycleError(f"theme interface must be one of: {choices}")
    return value


def _theme_identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ThemeLifecycleError("theme identity must be lowercase kebab-case")
    return value


def _package_files(directory: Path) -> tuple[Path, ...]:
    try:
        directory_status = directory.lstat()
    except OSError as exc:
        raise ThemeLifecycleError("theme package directory is not accessible") from exc
    if stat.S_ISLNK(directory_status.st_mode):
        raise ThemeLifecycleError("theme package directory must not be a symlink")
    if not stat.S_ISDIR(directory_status.st_mode):
        raise ThemeLifecycleError("theme package source must be a directory")

    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ThemeLifecycleError("theme package directory cannot be read") from exc
    if len(entries) > THEME_PACKAGE_MAX_FILES:
        raise ThemeLifecycleError(f"theme package exceeds the {THEME_PACKAGE_MAX_FILES}-file limit")

    files: list[Path] = []
    total_bytes = 0
    for entry in entries:
        try:
            entry_status = entry.lstat()
        except OSError as exc:
            raise ThemeLifecycleError("theme package entry is not accessible") from exc
        if stat.S_ISLNK(entry_status.st_mode):
            raise ThemeLifecycleError("theme package must not contain symlinks")
        if not stat.S_ISREG(entry_status.st_mode):
            raise ThemeLifecycleError("theme package may contain only regular top-level files")
        total_bytes += entry_status.st_size
        if total_bytes > THEME_PACKAGE_MAX_BYTES:
            raise ThemeLifecycleError(
                f"theme package exceeds the {THEME_PACKAGE_MAX_BYTES}-byte limit"
            )
        files.append(entry)
    if not files:
        raise ThemeLifecycleError("theme package must not be empty")
    if not (directory / THEME_MANIFEST_FILENAME).is_file():
        raise ThemeLifecycleError("theme package is missing manifest.json")
    return tuple(files)


def _package_digest(directory: Path) -> str:
    files = _package_files(directory)
    digest = hashlib.sha256()
    for path in files:
        name = path.name.encode("utf-8")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ThemeLifecycleError("theme package file cannot be read") from exc
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    if files != _package_files(directory):
        raise ThemeLifecycleError("theme package changed while it was being read")
    return digest.hexdigest()


def _manifest_interface(directory: Path) -> ThemeInterface:
    try:
        parsed = json.loads((directory / THEME_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThemeLifecycleError("theme package has an invalid manifest") from exc
    if not isinstance(parsed, dict):
        raise ThemeLifecycleError("theme manifest must be a JSON object")
    return _theme_interface(parsed.get("interface"))


def _summary(
    interface: ThemeInterface,
    manifest: ThemeManifest,
    *,
    origin: ThemeOrigin,
    path: Path | None,
    sha256: str | None,
) -> ThemePackageSummary:
    return ThemePackageSummary(
        interface=interface,
        identifier=manifest.identifier,
        label=manifest.label,
        order=manifest.order,
        origin=origin,
        executable=interface == "home-assistant",
        path=path,
        sha256=sha256,
    )


def validate_theme_package(source: Path) -> ValidatedThemePackage:
    """Validate one explicit unpacked local theme directory without writing."""

    if not isinstance(source, Path):
        raise TypeError("Theme package source must be a pathlib.Path")
    candidate = source.expanduser().absolute()
    _package_files(candidate)
    digest = _package_digest(candidate)
    interface = _manifest_interface(candidate)
    if interface == "web":
        manifest: ThemeManifest = load_web_theme_package(candidate)
    elif interface == "home-assistant":
        manifest = load_home_assistant_theme_package(candidate)
    else:
        manifest = load_tui_theme_package(candidate)
    if manifest.identifier != candidate.name:
        raise ThemeLifecycleError("theme manifest identity must match its source directory")
    return ValidatedThemePackage(
        summary=_summary(
            interface,
            manifest,
            origin="managed",
            path=candidate,
            sha256=digest,
        ),
        manifest=manifest,
    )


def _built_in_manifests() -> dict[ThemeInterface, list[ThemeManifest]]:
    return {
        "web": list(built_in_web_theme_registry().themes),
        "home-assistant": list(built_in_home_assistant_theme_registry().themes),
        "tui": list(built_in_tui_theme_registry().themes),
    }


def _built_in_summaries() -> tuple[ThemePackageSummary, ...]:
    manifests = _built_in_manifests()
    return tuple(
        _summary(interface, manifest, origin="built-in", path=None, sha256=None)
        for interface in THEME_INTERFACES
        for manifest in manifests[interface]
    )


def _validate_registry(
    interface: ThemeInterface,
    manifests: Sequence[ThemeManifest],
) -> None:
    ordered = tuple(sorted(manifests, key=lambda manifest: manifest.order))
    if interface == "web":
        WebThemeRegistry(
            tuple(manifest for manifest in ordered if isinstance(manifest, WebThemeManifest))
        )
    elif interface == "home-assistant":
        HomeAssistantThemeRegistry(
            tuple(
                manifest for manifest in ordered if isinstance(manifest, HomeAssistantThemeManifest)
            )
        )
    else:
        TuiThemeRegistry(
            tuple(manifest for manifest in ordered if isinstance(manifest, TuiThemeManifest))
        )


def _issue(path: Path, message: str, *, interface: str | None = None) -> ThemeDiscoveryIssue:
    identifier = path.name if _IDENTIFIER_PATTERN.fullmatch(path.name) else None
    return ThemeDiscoveryIssue(
        interface=interface,
        identifier=identifier,
        path=path,
        message=message,
    )


def _discover_managed(
    root: Path,
    *,
    excluded: Path | None = None,
) -> tuple[tuple[ValidatedThemePackage, ...], tuple[ThemeDiscoveryIssue, ...]]:
    if not root.exists():
        return (), ()
    try:
        root_status = root.lstat()
    except OSError as exc:
        raise ThemeLifecycleError("managed theme root is not accessible") from exc
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        return (), (_issue(root, "managed theme root must be a real directory"),)

    accepted_manifests = _built_in_manifests()
    accepted: list[ValidatedThemePackage] = []
    issues: list[ThemeDiscoveryIssue] = []
    try:
        interface_entries = sorted(root.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ThemeLifecycleError("managed theme root cannot be read") from exc

    for interface_entry in interface_entries:
        if interface_entry.name.startswith("."):
            continue
        if interface_entry.name not in THEME_INTERFACES:
            issues.append(_issue(interface_entry, "unsupported theme interface directory"))
            continue
        interface = _theme_interface(interface_entry.name)
        try:
            interface_status = interface_entry.lstat()
        except OSError:
            issues.append(_issue(interface_entry, "theme interface is not accessible"))
            continue
        if stat.S_ISLNK(interface_status.st_mode) or not stat.S_ISDIR(interface_status.st_mode):
            issues.append(
                _issue(
                    interface_entry,
                    "theme interface must be a real directory",
                    interface=interface,
                )
            )
            continue
        try:
            package_entries = sorted(interface_entry.iterdir(), key=lambda entry: entry.name)
        except OSError:
            issues.append(
                _issue(
                    interface_entry,
                    "theme interface directory cannot be read",
                    interface=interface,
                )
            )
            continue
        for package_entry in package_entries:
            if package_entry.name.startswith(".") or package_entry == excluded:
                continue
            try:
                package = validate_theme_package(package_entry)
                if package.summary.interface != interface:
                    raise ThemeLifecycleError("theme package is stored under the wrong interface")
                if package.summary.identifier in _built_in_ids(interface):
                    raise ThemeLifecycleError("managed theme must not shadow a built-in identity")
                proposed = [*accepted_manifests[interface], package.manifest]
                _validate_registry(interface, proposed)
            except (SDS200Error, OSError, TypeError, ValueError) as exc:
                issues.append(_issue(package_entry, str(exc), interface=interface))
                continue
            accepted_manifests[interface].append(package.manifest)
            accepted.append(package)

    return tuple(accepted), tuple(issues)


def discover_theme_inventory(root: Path) -> ThemeInventory:
    """Discover built-ins and isolate every invalid managed package entry."""

    managed_root = _absolute_root(root)
    managed, issues = _discover_managed(managed_root)
    package_order = {name: index for index, name in enumerate(THEME_INTERFACES)}
    packages = tuple(
        sorted(
            (*_built_in_summaries(), *(package.summary for package in managed)),
            key=lambda package: (
                package_order[package.interface],
                0 if package.origin == "built-in" else 1,
                package.order,
                package.identifier,
            ),
        )
    )
    ordered_issues = tuple(sorted(issues, key=lambda issue: (str(issue.path), issue.message)))
    return ThemeInventory(root=managed_root, packages=packages, issues=ordered_issues)


def _built_in_ids(interface: ThemeInterface) -> tuple[str, ...]:
    if interface == "web":
        return BUILT_IN_WEB_THEME_IDS
    if interface == "home-assistant":
        return BUILT_IN_HOME_ASSISTANT_THEME_IDS
    return BUILT_IN_TUI_THEME_IDS


def _prepare_directory(path: Path) -> None:
    if path.exists():
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise ThemeLifecycleError("managed theme path must be a real directory")
    else:
        path.mkdir(parents=True, mode=THEME_DIRECTORY_MODE)
    path.chmod(THEME_DIRECTORY_MODE)


def _normalize_package_modes(directory: Path) -> None:
    directory.chmod(THEME_DIRECTORY_MODE)
    for path in _package_files(directory):
        path.chmod(THEME_FILE_MODE)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise ThemeLifecycleError("staged theme package file could not be written")
        offset += written


def _copy_package_without_links(source: Path, destination: Path) -> None:
    files = _package_files(source)
    source_flags = os.O_RDONLY | os.O_CLOEXEC
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        destination_flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW

    try:
        source_directory = os.open(source, directory_flags)
    except OSError as exc:
        raise ThemeLifecycleError("theme package directory changed before staging") from exc
    try:
        destination.mkdir(mode=THEME_DIRECTORY_MODE)
        for path in files:
            try:
                source_file = os.open(path.name, source_flags, dir_fd=source_directory)
            except OSError as exc:
                raise ThemeLifecycleError(
                    "theme package entry changed before staging"
                ) from exc
            try:
                before = os.fstat(source_file)
                if not stat.S_ISREG(before.st_mode):
                    raise ThemeLifecycleError(
                        "theme package may contain only regular files"
                    )
                content = bytearray()
                while True:
                    chunk = os.read(source_file, 64 * 1024)
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > THEME_PACKAGE_MAX_BYTES:
                        raise ThemeLifecycleError(
                            "theme package changed beyond the byte limit during staging"
                        )
                after = os.fstat(source_file)
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
                    raise ThemeLifecycleError(
                        "theme package file changed while it was being staged"
                    )
            finally:
                os.close(source_file)

            destination_path = destination / path.name
            try:
                destination_file = os.open(
                    destination_path,
                    destination_flags,
                    THEME_FILE_MODE,
                )
            except OSError as exc:
                raise ThemeLifecycleError(
                    "staged theme package file cannot be created"
                ) from exc
            try:
                _write_all(destination_file, bytes(content))
                os.fsync(destination_file)
            finally:
                os.close(destination_file)
    finally:
        os.close(source_directory)


def _remove_private_tree(path: Path) -> None:
    if not path.exists():
        return
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise ThemeLifecycleError("lifecycle recovery path must be a real directory")
    shutil.rmtree(path)


def _recover_interface(interface_root: Path) -> None:
    try:
        entries = sorted(interface_root.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ThemeLifecycleError("theme interface directory cannot be recovered") from exc
    for entry in entries:
        if entry.name.startswith(_STAGE_PREFIX):
            _remove_private_tree(entry)
        elif entry.name.startswith(_ROLLBACK_PREFIX):
            identifier = entry.name.removeprefix(_ROLLBACK_PREFIX)
            _theme_identifier(identifier)
            target = interface_root / identifier
            if target.exists():
                _remove_private_tree(entry)
            else:
                os.replace(entry, target)
        elif entry.name.startswith(_REMOVE_PREFIX):
            identifier = entry.name.removeprefix(_REMOVE_PREFIX)
            _theme_identifier(identifier)
            _remove_private_tree(entry)


@contextmanager
def _lifecycle_lock(root: Path) -> Iterator[None]:
    try:
        import fcntl
    except ModuleNotFoundError as exc:
        raise ThemeLifecycleError(
            "managed theme mutation requires operating-system file locking"
        ) from exc

    _prepare_directory(root)
    lock_path = root / _LOCK_FILENAME
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, THEME_FILE_MODE)
    except OSError as exc:
        raise ThemeLifecycleError("theme lifecycle lock cannot be opened") from exc
    try:
        lock_status = os.fstat(descriptor)
        if not stat.S_ISREG(lock_status.st_mode):
            raise ThemeLifecycleError("theme lifecycle lock must be a regular file")
        os.fchmod(descriptor, THEME_FILE_MODE)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ThemeLifecycleError(
                    "another theme lifecycle operation is in progress"
                ) from exc
            raise ThemeLifecycleError("theme lifecycle lock cannot be acquired") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _validate_candidate_collision(
    package: ValidatedThemePackage,
    root: Path,
    *,
    excluded: Path | None,
) -> None:
    managed, _ = _discover_managed(root, excluded=excluded)
    manifests = _built_in_manifests()[package.summary.interface]
    manifests.extend(
        existing.manifest
        for existing in managed
        if existing.summary.interface == package.summary.interface
    )
    manifests.append(package.manifest)
    _validate_registry(package.summary.interface, manifests)


def install_theme_package(
    source: Path,
    root: Path,
    *,
    replace: bool = False,
    home_assistant_code_trust: str | None = None,
) -> ThemePackageSummary:
    """Stage, validate, and publish one managed theme package."""

    managed_root = _absolute_root(root)
    package = validate_theme_package(source)
    interface = package.summary.interface
    identifier = package.summary.identifier
    if identifier in _built_in_ids(interface):
        raise ThemeLifecycleError("managed theme must not shadow a built-in identity")
    if (
        interface == "home-assistant"
        and home_assistant_code_trust != HOME_ASSISTANT_CODE_TRUST_TOKEN
    ):
        raise ThemeLifecycleError(
            "Home Assistant theme installation requires the exact executable-code "
            f"trust token: {HOME_ASSISTANT_CODE_TRUST_TOKEN}"
        )

    source_path = package.summary.path
    assert source_path is not None
    resolved_root = managed_root.resolve(strict=False)
    resolved_source = source_path.resolve(strict=True)
    if resolved_source == resolved_root or resolved_root in resolved_source.parents:
        raise ThemeLifecycleError("theme source must be outside the managed theme root")

    with _lifecycle_lock(managed_root):
        interface_root = managed_root / interface
        _prepare_directory(interface_root)
        _recover_interface(interface_root)
        target = interface_root / identifier
        stage_container = interface_root / f"{_STAGE_PREFIX}{identifier}"
        stage = stage_container / identifier
        rollback = interface_root / f"{_ROLLBACK_PREFIX}{identifier}"

        target_exists = target.exists()
        if target_exists and not replace:
            raise ThemeLifecycleError("managed theme already exists; use explicit replacement")
        if target_exists:
            target_status = target.lstat()
            if stat.S_ISLNK(target_status.st_mode) or not stat.S_ISDIR(target_status.st_mode):
                raise ThemeLifecycleError("existing managed theme must be a real directory")

        _validate_candidate_collision(
            package,
            managed_root,
            excluded=target if target_exists else None,
        )
        _remove_private_tree(stage_container)
        _remove_private_tree(rollback)
        activated = False
        previous_saved = False
        try:
            stage_container.mkdir(mode=THEME_DIRECTORY_MODE)
            _copy_package_without_links(resolved_source, stage)
            _normalize_package_modes(stage)
            staged = validate_theme_package(stage)
            if (
                staged.summary.interface != interface
                or staged.summary.identifier != identifier
                or staged.summary.sha256 != package.summary.sha256
            ):
                raise ThemeLifecycleError(
                    "staged theme package does not match the validated source"
                )
            if _package_digest(resolved_source) != package.summary.sha256:
                raise ThemeLifecycleError("theme source changed during installation")
            _validate_candidate_collision(
                staged,
                managed_root,
                excluded=target if target_exists else None,
            )
            if target_exists:
                os.replace(target, rollback)
                previous_saved = True
            os.replace(stage, target)
            activated = True
            installed = validate_theme_package(target)
            _validate_candidate_collision(
                installed,
                managed_root,
                excluded=target,
            )
            _remove_private_tree(rollback)
        except BaseException:
            if activated and target.exists():
                _remove_private_tree(target)
            if previous_saved and rollback.exists():
                os.replace(rollback, target)
            raise
        finally:
            _remove_private_tree(stage_container)
            if rollback.exists() and target.exists():
                _remove_private_tree(rollback)

    return ThemePackageSummary(
        interface=interface,
        identifier=identifier,
        label=package.summary.label,
        order=package.summary.order,
        origin="managed",
        executable=package.summary.executable,
        path=managed_root / interface / identifier,
        sha256=package.summary.sha256,
    )


def remove_theme_package(
    root: Path,
    interface: ThemeInterface | str,
    identifier: str,
    *,
    confirmation: str,
) -> ThemeRemoval:
    """Remove one exact managed directory through a recoverable tombstone."""

    managed_root = _absolute_root(root)
    normalized_interface = _theme_interface(interface)
    normalized_identifier = _theme_identifier(identifier)
    expected_confirmation = f"{normalized_interface}/{normalized_identifier}"
    if confirmation != expected_confirmation:
        raise ThemeLifecycleError(
            f"removal confirmation must exactly match: {expected_confirmation}"
        )
    if normalized_identifier in _built_in_ids(normalized_interface):
        raise ThemeLifecycleError("built-in themes cannot be removed")

    with _lifecycle_lock(managed_root):
        interface_root = managed_root / normalized_interface
        _prepare_directory(interface_root)
        _recover_interface(interface_root)
        if normalized_interface == "home-assistant":
            from .home_assistant_theme_activation import (
                ensure_home_assistant_theme_inactive,
            )

            ensure_home_assistant_theme_inactive(managed_root, normalized_identifier)
        target = interface_root / normalized_identifier
        tombstone = interface_root / f"{_REMOVE_PREFIX}{normalized_identifier}"
        if not target.exists():
            raise ThemeLifecycleError("managed theme does not exist")
        target_status = target.lstat()
        if stat.S_ISLNK(target_status.st_mode) or not stat.S_ISDIR(target_status.st_mode):
            raise ThemeLifecycleError("managed theme must be a real directory")
        _remove_private_tree(tombstone)
        os.replace(target, tombstone)
        try:
            _remove_private_tree(tombstone)
        except BaseException:
            if tombstone.exists() and not target.exists():
                os.replace(tombstone, target)
            raise

    return ThemeRemoval(
        interface=normalized_interface,
        identifier=normalized_identifier,
        path=managed_root / normalized_interface / normalized_identifier,
    )


__all__ = [
    "HOME_ASSISTANT_CODE_TRUST_TOKEN",
    "THEME_DIRECTORY_MODE",
    "THEME_FILE_MODE",
    "THEME_INTERFACES",
    "THEME_PACKAGE_MAX_BYTES",
    "THEME_PACKAGE_MAX_FILES",
    "ThemeDiscoveryIssue",
    "ThemeInterface",
    "ThemeInventory",
    "ThemeLifecycleError",
    "ThemePackageSummary",
    "ThemeRemoval",
    "ValidatedThemePackage",
    "discover_theme_inventory",
    "install_theme_package",
    "remove_theme_package",
    "validate_theme_package",
]
