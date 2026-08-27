from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, Literal, NoReturn, TypeAlias

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
_CAPABILITY_PREFIX: Final = ".sdsctl-capability-"
_ROOT_CREATE_PREFIX: Final = ".sdsctl-root-create-"
_INTERFACE_CREATE_PREFIX: Final = ".sdsctl-interface-create-"
_STAGE_PREFIX: Final = ".sdsctl-stage-"
_STAGE_RECORD_FILENAME: Final = ".sdsctl-stage.json"
_ROLLBACK_PREFIX: Final = ".sdsctl-rollback-"
_REMOVE_PREFIX: Final = ".sdsctl-remove-"
_REMOVE_RECORD_PREFIX: Final = ".sdsctl-removal-record-"
_PURGE_PREFIX: Final = ".sdsctl-purge-"
_CONFLICT_PREFIX: Final = ".sdsctl-conflict-"
_CAPABILITY_CONFLICT_PREFIX: Final = f"{_CONFLICT_PREFIX}capability-"
_FAILED_PUBLICATION_NAME: Final = ".failed-publication"
_STAGE_RECORD_SCHEMA_VERSION: Final = 1
_REMOVAL_RECORD_SCHEMA_VERSION: Final = 1
_COPY_CHUNK_BYTES: Final = 64 * 1024
_TRANSACTION_RECORD_MAX_BYTES: Final = 4096
_AT_FDCWD: Final = -100
_RENAME_NOREPLACE: Final = 1
_SECURE_OPEN_PROBE: Final = os.open
_SECURE_MKDIR_PROBE: Final = os.mkdir
_SECURE_RENAME_PROBE: Final = os.rename
_SECURE_RMDIR_PROBE: Final = os.rmdir
_SECURE_STAT_PROBE: Final = os.stat
_SECURE_SCANDIR_PROBE: Final = os.scandir
_SECURE_UNLINK_PROBE: Final = os.unlink
_STAGE_TOKEN_PATTERN: Final = re.compile(r"[0-9a-f]{32}\Z")

_MetadataIdentity: TypeAlias = tuple[int, int, int, int, int, int, int]


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


@dataclass(frozen=True, slots=True)
class _PackageEntry:
    name: str
    identity: _MetadataIdentity

    @property
    def size(self) -> int:
        return self.identity[4]


@dataclass(frozen=True, slots=True)
class _PackageInventory:
    entries: tuple[_PackageEntry, ...]
    total_bytes: int


@dataclass(frozen=True, slots=True)
class _OpenedPackageDirectory:
    path: Path
    resolved_path: Path
    descriptor: int
    path_identity: _MetadataIdentity
    descriptor_identity: _MetadataIdentity


@dataclass(frozen=True, slots=True)
class _PackageImage:
    files: tuple[tuple[str, bytes], ...]
    sha256: str
    total_bytes: int
    directory_identity: _MetadataIdentity
    entries: tuple[_PackageEntry, ...]

    def require_file(self, name: str) -> bytes:
        for candidate, content in self.files:
            if candidate == name:
                return content
        raise ThemeLifecycleError(f"validated theme snapshot is missing {name!r}")


@dataclass(frozen=True, slots=True)
class _ValidatedThemeSourceSnapshot:
    package: ValidatedThemePackage
    directory: Path
    image: _PackageImage
    source: _OpenedPackageDirectory
    source_inventory: _PackageInventory


@dataclass(frozen=True, slots=True)
class _RemovalRecord:
    interface: ThemeInterface
    identifier: str
    token: str
    target_identity: tuple[int, int]
    directory_identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _StageRecord:
    interface: ThemeInterface
    identifier: str
    token: str
    directory_identity: tuple[int, int]


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


def _metadata_identity(status: os.stat_result) -> _MetadataIdentity:
    return (
        stat.S_IFMT(status.st_mode),
        status.st_dev,
        status.st_ino,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _require_secure_snapshot_support() -> None:
    supported = (
        hasattr(os, "O_CLOEXEC")
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and _SECURE_OPEN_PROBE in os.supports_dir_fd
        and _SECURE_MKDIR_PROBE in os.supports_dir_fd
        and _SECURE_STAT_PROBE in os.supports_dir_fd
        and _SECURE_STAT_PROBE in os.supports_follow_symlinks
        and _SECURE_SCANDIR_PROBE in os.supports_fd
    )
    if not supported:
        raise ThemeLifecycleError(
            "managed theme package validation requires secure POSIX descriptor support"
        )


def _require_secure_mutation_support() -> None:
    _require_secure_snapshot_support()
    if not (
        _SECURE_RENAME_PROBE in os.supports_dir_fd
        and _SECURE_RMDIR_PROBE in os.supports_dir_fd
        and _SECURE_UNLINK_PROBE in os.supports_dir_fd
    ):
        raise ThemeLifecycleError(
            "managed theme mutation requires descriptor-relative rename and deletion support"
        )
    if not _atomic_noreplace_available():
        raise ThemeLifecycleError(
            "managed theme mutation requires atomic no-replace rename support"
        )


def _atomic_noreplace_available() -> bool:
    if os.name != "posix":
        return False
    try:
        library = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return False
    return hasattr(library, "renameat2")


def _rename_noreplace(
    source: str | Path,
    destination: str | Path,
    *,
    src_dir_fd: int | None = None,
    dst_dir_fd: int | None = None,
) -> None:
    """Atomically rename one entry only when the destination is absent."""

    if not _atomic_noreplace_available():
        raise ThemeLifecycleError(
            "managed theme mutation requires atomic no-replace rename support"
        )
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise ThemeLifecycleError(
            "managed theme mutation requires atomic no-replace rename support"
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(
        _AT_FDCWD if src_dir_fd is None else src_dir_fd,
        os.fsencode(source),
        _AT_FDCWD if dst_dir_fd is None else dst_dir_fd,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), os.fspath(destination))


def _raise_with_lifecycle_error_precedence(
    ordinary_primary: BaseException,
    *chronological_errors: BaseException | None,
) -> NoReturn:
    """Raise the newest critical BaseException or the intended ordinary error."""

    errors = tuple(error for error in chronological_errors if error is not None)
    primary = next(
        (error for error in reversed(errors) if not isinstance(error, Exception)),
        ordinary_primary,
    )
    cause = next(
        (error for error in reversed(errors) if error is not primary),
        None,
    )
    if cause is None:
        raise primary
    raise primary from cause


def _probe_atomic_noreplace(
    directory: Path,
    *,
    opened: _OpenedPackageDirectory | None = None,
) -> None:
    """Qualify no-replace directory renames on the managed target filesystem."""

    token = secrets.token_hex(16)
    source_name = f"{_CAPABILITY_PREFIX}{token}-source"
    occupied_name = f"{_CAPABILITY_PREFIX}{token}-occupied"
    destination_name = f"{_CAPABILITY_PREFIX}{token}-destination"
    created: dict[str, tuple[int, int, int]] = {}
    parent_context = (
        nullcontext(opened) if opened is not None else _open_package_directory(directory)
    )
    with parent_context as parent:
        try:
            for name in (source_name, occupied_name):
                os.mkdir(name, THEME_DIRECTORY_MODE, dir_fd=parent.descriptor)
                identity = _relative_entry_identity(parent.descriptor, name)
                if identity is None or identity[0] != stat.S_IFDIR:
                    raise ThemeLifecycleError(
                        "atomic no-replace capability probe directory was not created"
                    )
                created[name] = identity

            collision_error: BaseException | None = None
            try:
                _rename_noreplace(
                    source_name,
                    occupied_name,
                    src_dir_fd=parent.descriptor,
                    dst_dir_fd=parent.descriptor,
                )
            except BaseException as exc:
                collision_error = exc
            if collision_error is not None and not isinstance(collision_error, OSError):
                raise collision_error
            if (
                not isinstance(collision_error, OSError)
                or collision_error.errno != errno.EEXIST
                or _relative_entry_identity(parent.descriptor, source_name) != created[source_name]
                or _relative_entry_identity(parent.descriptor, occupied_name)
                != created[occupied_name]
            ):
                raise ThemeLifecycleError(
                    "managed theme filesystem does not preserve no-replace collisions"
                ) from collision_error

            occupied_identity = created[occupied_name]
            if not _remove_exact_empty_directory(
                parent.path / occupied_name,
                expected_identity=(occupied_identity[1], occupied_identity[2]),
                parent=parent,
            ):
                raise ThemeLifecycleError(
                    "atomic no-replace occupied probe could not be removed safely"
                )
            created.pop(occupied_name)
            move_error: BaseException | None = None
            try:
                _rename_noreplace(
                    source_name,
                    destination_name,
                    src_dir_fd=parent.descriptor,
                    dst_dir_fd=parent.descriptor,
                )
            except BaseException as exc:
                move_error = exc
            if (
                _relative_entry_identity(parent.descriptor, destination_name)
                != created[source_name]
                or _relative_entry_identity(parent.descriptor, source_name) is not None
            ):
                raise ThemeLifecycleError(
                    "managed theme filesystem does not support atomic no-replace rename"
                ) from move_error
            created[destination_name] = created.pop(source_name)
            if move_error is not None:
                raise move_error
        finally:
            for name, identity in tuple(created.items()):
                observed = _relative_entry_identity(parent.descriptor, name)
                if observed == identity:
                    if not _remove_exact_empty_directory(
                        parent.path / name,
                        expected_identity=(identity[1], identity[2]),
                        parent=parent,
                    ):
                        raise ThemeLifecycleError(
                            "atomic no-replace capability probe entry could not be removed safely"
                        )
                elif observed is not None:
                    raise ThemeLifecycleError(
                        "atomic no-replace capability probe entry was replaced"
                    )


def _directory_flags() -> int:
    _require_secure_snapshot_support()
    return os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def _source_file_flags() -> int:
    _require_secure_snapshot_support()
    return os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW


@contextmanager
def _open_package_directory(path: Path) -> Iterator[_OpenedPackageDirectory]:
    _require_secure_snapshot_support()
    normalized = Path(os.path.abspath(path))
    try:
        path_status = normalized.lstat()
    except OSError as exc:
        raise ThemeLifecycleError("theme package directory is not accessible") from exc
    if stat.S_ISLNK(path_status.st_mode):
        raise ThemeLifecycleError("theme package directory must not be a symlink")
    if not stat.S_ISDIR(path_status.st_mode):
        raise ThemeLifecycleError("theme package source must be a directory")
    descriptor: int | None = None
    try:
        try:
            resolved_parent = normalized.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ThemeLifecycleError("theme package parent cannot be resolved") from exc
        descriptor = os.open(resolved_parent, _directory_flags())
        next_descriptor = os.open(
            normalized.name,
            _directory_flags(),
            dir_fd=descriptor,
        )
        os.close(descriptor)
        descriptor = next_descriptor
        opened_status = os.fstat(descriptor)
        if not stat.S_ISDIR(opened_status.st_mode) or (
            opened_status.st_dev,
            opened_status.st_ino,
        ) != (
            path_status.st_dev,
            path_status.st_ino,
        ):
            raise ThemeLifecycleError("theme package directory changed while it was opened")
        opened = _OpenedPackageDirectory(
            path=normalized,
            resolved_path=resolved_parent / normalized.name,
            descriptor=descriptor,
            path_identity=_metadata_identity(path_status),
            descriptor_identity=_metadata_identity(opened_status),
        )
    except ThemeLifecycleError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ThemeLifecycleError("theme package directory cannot be securely opened") from exc
    try:
        yield opened
    finally:
        os.close(descriptor)


def _retain_package_directory_relative(
    parent: _OpenedPackageDirectory,
    name: str,
    path: Path,
) -> _OpenedPackageDirectory:
    """Open one real child directory beneath an already retained parent."""

    _require_secure_snapshot_support()
    normalized = Path(os.path.abspath(path))
    descriptor: int | None = None
    try:
        relative_status = os.stat(
            name,
            dir_fd=parent.descriptor,
            follow_symlinks=False,
        )
        path_status = normalized.lstat()
        if (
            not stat.S_ISDIR(relative_status.st_mode)
            or not stat.S_ISDIR(path_status.st_mode)
            or (relative_status.st_dev, relative_status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
        ):
            raise ThemeLifecycleError("theme package directory binding changed")
        descriptor = os.open(
            name,
            _directory_flags(),
            dir_fd=parent.descriptor,
        )
        opened_status = os.fstat(descriptor)
        if not stat.S_ISDIR(opened_status.st_mode) or (
            opened_status.st_dev,
            opened_status.st_ino,
        ) != (
            relative_status.st_dev,
            relative_status.st_ino,
        ):
            raise ThemeLifecycleError("theme package directory changed while it was opened")
        opened = _OpenedPackageDirectory(
            path=normalized,
            resolved_path=parent.resolved_path / name,
            descriptor=descriptor,
            path_identity=_metadata_identity(path_status),
            descriptor_identity=_metadata_identity(opened_status),
        )
    except ThemeLifecycleError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ThemeLifecycleError("theme package directory cannot be securely opened") from exc
    return opened


@contextmanager
def _open_package_directory_relative(
    parent: _OpenedPackageDirectory,
    name: str,
    path: Path,
) -> Iterator[_OpenedPackageDirectory]:
    opened = _retain_package_directory_relative(parent, name, path)
    try:
        yield opened
    finally:
        os.close(opened.descriptor)


def _assert_open_directory_binding(opened: _OpenedPackageDirectory) -> None:
    """Verify that a retained directory remains bound at its original pathname."""

    try:
        descriptor_status = os.fstat(opened.descriptor)
        path_status = opened.path.lstat()
    except OSError as exc:
        raise ThemeLifecycleError("theme package directory binding changed") from exc
    if (
        not stat.S_ISDIR(path_status.st_mode)
        or _metadata_identity(descriptor_status) != opened.descriptor_identity
        or _metadata_identity(path_status) != opened.path_identity
    ):
        raise ThemeLifecycleError("theme package directory binding changed")


def _bounded_package_names(directory: int) -> tuple[str, ...]:
    names: list[str] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > THEME_PACKAGE_MAX_FILES:
                    raise ThemeLifecycleError(
                        f"theme package exceeds the {THEME_PACKAGE_MAX_FILES}-file limit"
                    )
    except ThemeLifecycleError:
        raise
    except OSError as exc:
        raise ThemeLifecycleError("theme package directory cannot be read") from exc
    try:
        return tuple(sorted(names, key=lambda name: name.encode("utf-8")))
    except UnicodeError as exc:
        raise ThemeLifecycleError("theme package filenames must be valid UTF-8") from exc


def _package_inventory(directory: int) -> _PackageInventory:
    names = _bounded_package_names(directory)
    if not names:
        raise ThemeLifecycleError("theme package must not be empty")
    if THEME_MANIFEST_FILENAME not in names:
        raise ThemeLifecycleError("theme package is missing manifest.json")

    entries: list[_PackageEntry] = []
    total_bytes = 0
    for name in names:
        try:
            status = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except OSError as exc:
            raise ThemeLifecycleError("theme package entry is not accessible") from exc
        if stat.S_ISLNK(status.st_mode):
            raise ThemeLifecycleError("theme package must not contain symlinks")
        if not stat.S_ISREG(status.st_mode):
            raise ThemeLifecycleError("theme package may contain only regular top-level files")
        total_bytes += status.st_size
        if total_bytes > THEME_PACKAGE_MAX_BYTES:
            raise ThemeLifecycleError(
                f"theme package exceeds the {THEME_PACKAGE_MAX_BYTES}-byte limit"
            )
        entries.append(_PackageEntry(name, _metadata_identity(status)))
    return _PackageInventory(tuple(entries), total_bytes)


def _assert_open_package_unchanged(
    opened: _OpenedPackageDirectory,
    inventory: _PackageInventory,
) -> None:
    try:
        descriptor_status = os.fstat(opened.descriptor)
        path_status = opened.path.lstat()
    except OSError as exc:
        raise ThemeLifecycleError("theme package directory changed during snapshot") from exc
    if (
        _metadata_identity(descriptor_status) != opened.descriptor_identity
        or _metadata_identity(path_status) != opened.path_identity
        or stat.S_ISLNK(path_status.st_mode)
    ):
        raise ThemeLifecycleError("theme package directory changed during snapshot")
    final = _package_inventory(opened.descriptor)
    if final != inventory:
        raise ThemeLifecycleError("theme package entries changed during snapshot")


def _package_digest(files: Sequence[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for filename, content in files:
        try:
            name = filename.encode("utf-8")
        except UnicodeError as exc:
            raise ThemeLifecycleError("theme package filenames must be valid UTF-8") from exc
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _read_open_package(
    opened: _OpenedPackageDirectory,
    inventory: _PackageInventory,
) -> _PackageImage:
    files: list[tuple[str, bytes]] = []
    actual_total = 0
    for entry in inventory.entries:
        try:
            descriptor = os.open(
                entry.name,
                _source_file_flags(),
                dir_fd=opened.descriptor,
            )
        except OSError as exc:
            raise ThemeLifecycleError("theme package entry changed before reading") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ThemeLifecycleError("theme package may contain only regular top-level files")
            if _metadata_identity(before) != entry.identity:
                raise ThemeLifecycleError("theme package entry changed before reading")
            content = bytearray()
            while len(content) < entry.size:
                package_remaining = THEME_PACKAGE_MAX_BYTES - actual_total
                file_remaining = entry.size - len(content)
                request = min(
                    _COPY_CHUNK_BYTES,
                    package_remaining,
                    file_remaining,
                )
                if request <= 0:
                    raise ThemeLifecycleError(
                        "theme package reached the aggregate byte limit before EOF"
                    )
                chunk = os.read(descriptor, request)
                if not chunk:
                    break
                content.extend(chunk)
                actual_total += len(chunk)
                if actual_total > THEME_PACKAGE_MAX_BYTES:
                    raise ThemeLifecycleError(
                        "theme package changed beyond the aggregate byte limit"
                    )
            after = os.fstat(descriptor)
            if len(content) != entry.size or _metadata_identity(after) != entry.identity:
                raise ThemeLifecycleError("theme package file changed while it was being read")
            try:
                final_status = os.stat(
                    entry.name,
                    dir_fd=opened.descriptor,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise ThemeLifecycleError(
                    "theme package entry changed while it was being read"
                ) from exc
            if _metadata_identity(final_status) != entry.identity:
                raise ThemeLifecycleError("theme package entry changed while it was being read")
            files.append((entry.name, bytes(content)))
        finally:
            os.close(descriptor)
    if actual_total != inventory.total_bytes:
        raise ThemeLifecycleError("theme package aggregate byte count changed during snapshot")
    _assert_open_package_unchanged(opened, inventory)
    immutable_files = tuple(files)
    return _PackageImage(
        files=immutable_files,
        sha256=_package_digest(immutable_files),
        total_bytes=actual_total,
        directory_identity=opened.descriptor_identity,
        entries=inventory.entries,
    )


def _read_package_image(directory: Path) -> _PackageImage:
    with _open_package_directory(directory) as opened:
        inventory = _package_inventory(opened.descriptor)
        return _read_open_package(opened, inventory)


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


def _assert_package_image(
    directory: Path,
    expected: _PackageImage,
    *,
    require_identity: bool,
) -> _PackageImage:
    observed = _read_package_image(directory)
    if (
        observed.files != expected.files
        or observed.sha256 != expected.sha256
        or observed.total_bytes != expected.total_bytes
        or (
            require_identity
            and observed.directory_identity[1:3] != expected.directory_identity[1:3]
        )
        or (require_identity and observed.entries != expected.entries)
    ):
        raise ThemeLifecycleError("private theme snapshot changed after acquisition")
    return observed


def _validate_private_theme_package(
    directory: Path,
    *,
    reported_path: Path,
    expected: _PackageImage,
) -> ValidatedThemePackage:
    _assert_package_image(directory, expected, require_identity=True)
    try:
        interface = _manifest_interface(directory)
        if interface == "web":
            manifest: ThemeManifest = load_web_theme_package(directory)
        elif interface == "home-assistant":
            manifest = load_home_assistant_theme_package(directory)
        else:
            manifest = load_tui_theme_package(directory)
    except BaseException as exc:
        try:
            _assert_package_image(directory, expected, require_identity=True)
        except ThemeLifecycleError as changed:
            raise changed from exc
        raise
    _assert_package_image(directory, expected, require_identity=True)
    if manifest.identifier != reported_path.name:
        raise ThemeLifecycleError("theme manifest identity must match its source directory")
    return ValidatedThemePackage(
        summary=_summary(
            interface,
            manifest,
            origin="managed",
            path=reported_path,
            sha256=expected.sha256,
        ),
        manifest=manifest,
    )


@contextmanager
def _validated_open_theme_source_snapshot(
    opened: _OpenedPackageDirectory,
) -> Iterator[_ValidatedThemeSourceSnapshot]:
    with TemporaryDirectory(prefix="sdsctl-theme-snapshot-") as temporary:
        temporary_root = Path(temporary)
        snapshot_path = temporary_root / opened.path.name
        source_inventory = _package_inventory(opened.descriptor)
        image = _copy_open_package_without_links(
            opened,
            source_inventory,
            snapshot_path,
        )
        package = _validate_private_theme_package(
            snapshot_path,
            reported_path=opened.path,
            expected=image,
        )
        _assert_open_package_unchanged(opened, source_inventory)
        yield _ValidatedThemeSourceSnapshot(
            package=package,
            directory=snapshot_path,
            image=image,
            source=opened,
            source_inventory=source_inventory,
        )


@contextmanager
def _validated_theme_source_snapshot(
    source: Path,
) -> Iterator[_ValidatedThemeSourceSnapshot]:
    if not isinstance(source, Path):
        raise TypeError("Theme package source must be a pathlib.Path")
    candidate = source.expanduser().absolute()
    if not candidate.name or candidate.name in {".", ".."}:
        raise ThemeLifecycleError("theme package source directory must have a safe name")
    _require_secure_snapshot_support()
    with (
        _open_package_directory(candidate) as opened,
        _validated_open_theme_source_snapshot(opened) as snapshot,
    ):
        yield snapshot


def validate_theme_package(source: Path) -> ValidatedThemePackage:
    """Snapshot and validate one explicit unpacked local theme directory."""

    with _validated_theme_source_snapshot(source) as snapshot:
        return snapshot.package


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


def _creation_candidate_prefix(prefix: str, target_name: str) -> str:
    return f"{prefix}{target_name}--"


def _recover_directory_creation_candidates(
    parent: _OpenedPackageDirectory,
    *,
    prefix: str,
    target_name: str,
) -> None:
    candidate_prefix = _creation_candidate_prefix(prefix, target_name)
    try:
        with os.scandir(parent.descriptor) as iterator:
            names = tuple(
                entry.name for entry in iterator if entry.name.startswith(candidate_prefix)
            )
    except OSError as exc:
        raise ThemeLifecycleError(
            "managed theme directory creation artifacts cannot be inspected"
        ) from exc
    for name in sorted(names):
        token = name.removeprefix(candidate_prefix)
        if _STAGE_TOKEN_PATTERN.fullmatch(token) is None:
            raise ThemeLifecycleError(
                "managed theme directory has an invalid creation artifact; it was "
                "preserved for explicit operator reconciliation"
            )
        identity = _relative_entry_identity(parent.descriptor, name)
        if identity is None:
            continue
        if identity[0] != stat.S_IFDIR or not _remove_exact_empty_directory(
            parent.path / name,
            expected_identity=(identity[1], identity[2]),
            parent=parent,
        ):
            raise ThemeLifecycleError(
                "managed theme directory has a populated creation artifact; it was "
                "preserved for explicit operator reconciliation"
            )


def _recover_parent_capability_artifacts(
    parent: _OpenedPackageDirectory,
) -> None:
    try:
        with os.scandir(parent.descriptor) as iterator:
            entries = tuple(iterator)
    except OSError as exc:
        raise ThemeLifecycleError(
            "managed theme parent capability artifacts cannot be inspected"
        ) from exc
    purge_names = tuple(entry.name for entry in entries if entry.name.startswith(_PURGE_PREFIX))
    if purge_names:
        raise ThemeLifecycleError(
            "managed theme parent has an unauthenticated interrupted purge; it was "
            "preserved for explicit operator reconciliation: "
            f"{parent.path / sorted(purge_names)[0]}"
        )
    names = tuple(entry.name for entry in entries if entry.name.startswith(_CAPABILITY_PREFIX))
    for name in sorted(names):
        _remove_empty_capability_probe(
            parent.path / name,
            parent=parent,
            reported_parent=parent.path,
        )


def _create_configured_directory(
    parent: _OpenedPackageDirectory,
    *,
    target_name: str,
    prefix: str,
) -> tuple[int, int]:
    _probe_atomic_noreplace(parent.path, opened=parent)
    _recover_parent_capability_artifacts(parent)
    _recover_directory_creation_candidates(
        parent,
        prefix=prefix,
        target_name=target_name,
    )
    token = secrets.token_hex(16)
    candidate_name = f"{_creation_candidate_prefix(prefix, target_name)}{token}"
    candidate_path = parent.path / candidate_name
    candidate_identity: tuple[int, int] | None = None
    published = False
    try:
        try:
            os.mkdir(candidate_name, THEME_DIRECTORY_MODE, dir_fd=parent.descriptor)
        except BaseException as exc:
            interrupted = _relative_entry_identity(parent.descriptor, candidate_name)
            if interrupted is not None and interrupted[0] == stat.S_IFDIR:
                candidate_identity = (interrupted[1], interrupted[2])
            if isinstance(exc, OSError):
                raise ThemeLifecycleError(
                    "managed theme private directory cannot be created"
                ) from exc
            raise
        created = _relative_entry_identity(parent.descriptor, candidate_name)
        if created is None or created[0] != stat.S_IFDIR:
            raise ThemeLifecycleError("managed theme private directory was not created")
        candidate_identity = (created[1], created[2])
        try:
            descriptor = os.open(
                candidate_name,
                _directory_flags(),
                dir_fd=parent.descriptor,
            )
        except OSError as exc:
            raise ThemeLifecycleError("managed theme private directory cannot be retained") from exc
        try:
            retained = os.fstat(descriptor)
            if (
                stat.S_IFMT(retained.st_mode),
                retained.st_dev,
                retained.st_ino,
            ) != _directory_entry_identity(candidate_identity):
                raise ThemeLifecycleError("managed theme private directory was replaced")
            with os.scandir(descriptor) as iterator:
                if next(iterator, None) is not None:
                    raise ThemeLifecycleError(
                        "new managed theme private directory was unexpectedly populated"
                    )
            if _relative_entry_identity(
                parent.descriptor, candidate_name
            ) != _directory_entry_identity(candidate_identity):
                raise ThemeLifecycleError("managed theme private directory was replaced")
            os.fchmod(descriptor, THEME_DIRECTORY_MODE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        operation_error: BaseException | None = None
        try:
            _rename_noreplace(
                candidate_name,
                target_name,
                src_dir_fd=parent.descriptor,
                dst_dir_fd=parent.descriptor,
            )
        except BaseException as exc:
            operation_error = exc
        published = (
            _relative_entry_identity(parent.descriptor, target_name)
            == _directory_entry_identity(candidate_identity)
            and _relative_entry_identity(parent.descriptor, candidate_name) is None
        )
        if not published:
            if operation_error is not None:
                raise ThemeLifecycleError(
                    "managed theme filesystem does not preserve no-replace directory creation"
                ) from operation_error
            raise ThemeLifecycleError("managed theme private directory could not be published")
        os.fsync(parent.descriptor)
        if operation_error is not None:
            raise operation_error
        return candidate_identity
    except BaseException as creation_error:
        if not published and candidate_identity is not None:
            expected = _directory_entry_identity(candidate_identity)
            observed = _relative_entry_identity(parent.descriptor, candidate_name)
            if observed == expected:
                _remove_exact_empty_directory(
                    candidate_path,
                    expected_identity=candidate_identity,
                    parent=parent,
                )
            elif observed is not None:
                raise ThemeLifecycleError(
                    "managed theme private directory was replaced and preserved"
                ) from creation_error
        raise


def _prepare_directory(path: Path) -> tuple[int, int]:
    try:
        status = path.lstat()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, mode=THEME_DIRECTORY_MODE, exist_ok=True)
        with _open_package_directory(path.parent) as opened_parent:
            expected_identity = _create_configured_directory(
                opened_parent,
                target_name=path.name,
                prefix=_ROOT_CREATE_PREFIX,
            )
        status = path.lstat()
    except OSError as exc:
        raise ThemeLifecycleError("managed theme path cannot be inspected") from exc
    else:
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise ThemeLifecycleError("managed theme path must be a real directory")
        expected_identity = (status.st_dev, status.st_ino)
        with _open_package_directory(path.parent) as opened_parent:
            _recover_parent_capability_artifacts(opened_parent)
            _recover_directory_creation_candidates(
                opened_parent,
                prefix=_ROOT_CREATE_PREFIX,
                target_name=path.name,
            )
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise ThemeLifecycleError("managed theme path must be a real directory")
    with _open_package_directory(path) as opened:
        descriptor_status = os.fstat(opened.descriptor)
        path_status = path.lstat()
        identity = (descriptor_status.st_dev, descriptor_status.st_ino)
        if (
            not stat.S_ISDIR(path_status.st_mode)
            or identity != expected_identity
            or (path_status.st_dev, path_status.st_ino) != expected_identity
        ):
            raise ThemeLifecycleError("managed theme directory binding changed")
        os.fchmod(opened.descriptor, THEME_DIRECTORY_MODE)
        _assert_directory_binding(opened)
        return identity


def _assert_directory_binding(opened: _OpenedPackageDirectory) -> None:
    try:
        descriptor_status = os.fstat(opened.descriptor)
        path_status = opened.path.lstat()
    except OSError as exc:
        raise ThemeLifecycleError("managed theme directory binding cannot be verified") from exc
    if (
        not stat.S_ISDIR(descriptor_status.st_mode)
        or not stat.S_ISDIR(path_status.st_mode)
        or (descriptor_status.st_dev, descriptor_status.st_ino)
        != (path_status.st_dev, path_status.st_ino)
    ):
        raise ThemeLifecycleError("managed theme directory binding changed")


def _retained_directory_path(opened: _OpenedPackageDirectory) -> Path:
    return Path(f"/proc/self/fd/{opened.descriptor}")


def _assert_lifecycle_bindings(
    root: _OpenedPackageDirectory,
    interface: _OpenedPackageDirectory,
) -> None:
    _assert_directory_binding(root)
    _assert_directory_binding(interface)


@contextmanager
def _prepare_interface_directory(
    root: _OpenedPackageDirectory,
    interface: ThemeInterface,
    path: Path,
) -> Iterator[_OpenedPackageDirectory]:
    _assert_directory_binding(root)
    try:
        status = os.stat(
            interface,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        expected_identity = _create_configured_directory(
            root,
            target_name=interface,
            prefix=_INTERFACE_CREATE_PREFIX,
        )
        status = os.stat(
            interface,
            dir_fd=root.descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise ThemeLifecycleError("managed theme interface cannot be inspected") from exc
    else:
        expected_identity = (status.st_dev, status.st_ino)
        _recover_parent_capability_artifacts(root)
        _recover_directory_creation_candidates(
            root,
            prefix=_INTERFACE_CREATE_PREFIX,
            target_name=interface,
        )
    if not stat.S_ISDIR(status.st_mode):
        raise ThemeLifecycleError("managed theme interface must be a real directory")
    if (status.st_dev, status.st_ino) != expected_identity:
        raise ThemeLifecycleError("managed theme interface binding changed")
    with _open_package_directory_relative(root, interface, path) as opened:
        if (
            opened.descriptor_identity[1],
            opened.descriptor_identity[2],
        ) != expected_identity:
            raise ThemeLifecycleError("managed theme interface binding changed")
        os.fchmod(opened.descriptor, THEME_DIRECTORY_MODE)
        _assert_directory_binding(root)
        _assert_directory_binding(opened)
        try:
            yield opened
        finally:
            _assert_directory_binding(opened)
            _assert_directory_binding(root)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise ThemeLifecycleError("staged theme package file could not be written")
        offset += written


def _copy_open_package_without_links(
    source: _OpenedPackageDirectory,
    inventory: _PackageInventory,
    destination: Path,
    *,
    destination_parent: _OpenedPackageDirectory | None = None,
) -> _PackageImage:
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    source_files: list[tuple[str, bytes]] = []
    actual_total = 0
    destination_directory_identity: tuple[int, int] | None = None
    parent_context = (
        nullcontext(destination_parent)
        if destination_parent is not None
        else _open_package_directory(destination.parent)
    )
    with parent_context as opened_destination_parent:
        os.fchmod(opened_destination_parent.descriptor, THEME_DIRECTORY_MODE)
        try:
            os.mkdir(
                destination.name,
                THEME_DIRECTORY_MODE,
                dir_fd=opened_destination_parent.descriptor,
            )
            staged_descriptor = os.open(
                destination.name,
                _directory_flags(),
                dir_fd=opened_destination_parent.descriptor,
            )
        except OSError as exc:
            raise ThemeLifecycleError("private theme snapshot cannot be created") from exc
        try:
            os.fchmod(staged_descriptor, THEME_DIRECTORY_MODE)
            staged_status = os.fstat(staged_descriptor)
            created_status = os.stat(
                destination.name,
                dir_fd=opened_destination_parent.descriptor,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(created_status.st_mode) or (
                created_status.st_dev,
                created_status.st_ino,
            ) != (
                staged_status.st_dev,
                staged_status.st_ino,
            ):
                raise ThemeLifecycleError("private theme snapshot directory was replaced")
            destination_directory_identity = (
                staged_status.st_dev,
                staged_status.st_ino,
            )
            for entry in inventory.entries:
                try:
                    source_file = os.open(
                        entry.name,
                        _source_file_flags(),
                        dir_fd=source.descriptor,
                    )
                except OSError as exc:
                    raise ThemeLifecycleError("theme package entry changed before staging") from exc
                try:
                    before = os.fstat(source_file)
                    if not stat.S_ISREG(before.st_mode):
                        raise ThemeLifecycleError(
                            "theme package may contain only regular top-level files"
                        )
                    if _metadata_identity(before) != entry.identity:
                        raise ThemeLifecycleError("theme package entry changed before staging")
                    try:
                        destination_file = os.open(
                            entry.name,
                            destination_flags,
                            THEME_FILE_MODE,
                            dir_fd=staged_descriptor,
                        )
                    except OSError as exc:
                        raise ThemeLifecycleError(
                            "staged theme package file cannot be created"
                        ) from exc
                    try:
                        content = bytearray()
                        while len(content) < entry.size:
                            package_remaining = THEME_PACKAGE_MAX_BYTES - actual_total
                            file_remaining = entry.size - len(content)
                            request = min(
                                _COPY_CHUNK_BYTES,
                                package_remaining,
                                file_remaining,
                            )
                            if request <= 0:
                                raise ThemeLifecycleError(
                                    "theme package reached the aggregate byte limit before EOF"
                                )
                            chunk = os.read(source_file, request)
                            if not chunk:
                                break
                            content.extend(chunk)
                            actual_total += len(chunk)
                            if actual_total > THEME_PACKAGE_MAX_BYTES:
                                raise ThemeLifecycleError(
                                    "theme package changed beyond the aggregate byte limit"
                                )
                            _write_all(destination_file, chunk)
                        after = os.fstat(source_file)
                        if (
                            len(content) != entry.size
                            or _metadata_identity(after) != entry.identity
                        ):
                            raise ThemeLifecycleError(
                                "theme package file changed while it was being staged"
                            )
                        try:
                            final_status = os.stat(
                                entry.name,
                                dir_fd=source.descriptor,
                                follow_symlinks=False,
                            )
                        except OSError as exc:
                            raise ThemeLifecycleError(
                                "theme package entry changed while it was being staged"
                            ) from exc
                        if _metadata_identity(final_status) != entry.identity:
                            raise ThemeLifecycleError(
                                "theme package entry changed while it was being staged"
                            )
                        os.fchmod(destination_file, THEME_FILE_MODE)
                        os.fsync(destination_file)
                        written = os.fstat(destination_file)
                        if not stat.S_ISREG(written.st_mode) or written.st_size != len(content):
                            raise ThemeLifecycleError(
                                "staged theme package byte count does not match its source"
                            )
                        source_files.append((entry.name, bytes(content)))
                    finally:
                        os.close(destination_file)
                finally:
                    os.close(source_file)
            os.fsync(staged_descriptor)
            try:
                final_status = os.stat(
                    destination.name,
                    dir_fd=opened_destination_parent.descriptor,
                    follow_symlinks=False,
                )
                retained_status = os.fstat(staged_descriptor)
            except OSError as exc:
                raise ThemeLifecycleError("private theme snapshot changed during staging") from exc
            if not stat.S_ISDIR(final_status.st_mode) or (
                final_status.st_dev,
                final_status.st_ino,
            ) != (retained_status.st_dev, retained_status.st_ino):
                raise ThemeLifecycleError("private theme snapshot changed during staging")
            _assert_directory_binding(opened_destination_parent)
        finally:
            os.close(staged_descriptor)

    if actual_total != inventory.total_bytes:
        raise ThemeLifecycleError("theme package aggregate byte count changed during staging")
    _assert_open_package_unchanged(source, inventory)
    copied_files = tuple(source_files)
    staged = _read_package_image(destination)
    if destination_directory_identity != (
        staged.directory_identity[1],
        staged.directory_identity[2],
    ):
        raise ThemeLifecycleError("private theme snapshot directory was replaced")
    if (
        staged.files != copied_files
        or staged.sha256 != _package_digest(copied_files)
        or staged.total_bytes != actual_total
    ):
        raise ThemeLifecycleError("private theme snapshot does not match its source bytes")
    return staged


def _copy_package_without_links(
    source: Path,
    destination: Path,
    *,
    destination_parent: _OpenedPackageDirectory | None = None,
) -> _PackageImage:
    with _open_package_directory(source) as opened:
        inventory = _package_inventory(opened.descriptor)
        return _copy_open_package_without_links(
            opened,
            inventory,
            destination,
            destination_parent=destination_parent,
        )


def _relative_entry_identity(directory: int, name: str) -> tuple[int, int, int] | None:
    try:
        status = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ThemeLifecycleError("private lifecycle entry cannot be inspected") from exc
    return stat.S_IFMT(status.st_mode), status.st_dev, status.st_ino


def _detach_private_entry(
    directory: int,
    name: str,
    identity: tuple[int, int, int],
) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    detached: str | None = None
    for index in range(len(alphabet) ** 4):
        value = index
        encoded = ""
        while True:
            encoded = alphabet[value % len(alphabet)] + encoded
            value //= len(alphabet)
            if value == 0:
                break
        if encoded != name and _relative_entry_identity(directory, encoded) is None:
            detached = encoded
            break
    if detached is None:
        raise ThemeLifecycleError("private lifecycle detach path cannot be reserved")
    operation_error: BaseException | None = None
    try:
        _rename_noreplace(
            name,
            detached,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
    except BaseException as exc:
        operation_error = exc
    if _relative_entry_identity(directory, detached) == identity:
        if operation_error is not None:
            raise operation_error
        return detached
    if operation_error is not None:
        raise operation_error
    raise ThemeLifecycleError("private lifecycle entry could not be detached safely")


def _next_retained_private_entry(
    directory: int,
) -> tuple[str, _MetadataIdentity] | None:
    try:
        with os.scandir(directory) as iterator:
            entry = next(iterator, None)
            if entry is None:
                return None
            return entry.name, _metadata_identity(entry.stat(follow_symlinks=False))
    except OSError as exc:
        raise ThemeLifecycleError("private lifecycle directory cannot be enumerated") from exc


def _clear_retained_private_directory(directory: int) -> None:
    frames: list[tuple[int, str, tuple[int, int, int]]] = []
    opened_children: list[int] = []
    current = directory
    try:
        while True:
            entry = _next_retained_private_entry(current)
            if entry is None:
                if not frames:
                    return
                child = current
                parent, detached, identity = frames.pop()
                if not opened_children or opened_children[-1] != child:
                    raise ThemeLifecycleError("private lifecycle cleanup descriptor stack changed")
                os.close(child)
                opened_children.pop()
                current = parent
                if _relative_entry_identity(current, detached) != identity:
                    raise ThemeLifecycleError(
                        "private lifecycle child directory changed during cleanup"
                    )
                os.rmdir(detached, dir_fd=current)
                continue

            name, metadata = entry
            identity = (metadata[0], metadata[1], metadata[2])
            detached = _detach_private_entry(current, name, identity)
            if identity[0] != stat.S_IFDIR:
                if _relative_entry_identity(current, detached) != identity:
                    raise ThemeLifecycleError("private lifecycle entry changed during cleanup")
                os.unlink(detached, dir_fd=current)
                continue

            try:
                child = os.open(detached, _directory_flags(), dir_fd=current)
            except OSError as exc:
                raise ThemeLifecycleError(
                    "private lifecycle child directory cannot be retained"
                ) from exc
            child_status = os.fstat(child)
            child_identity = (
                stat.S_IFMT(child_status.st_mode),
                child_status.st_dev,
                child_status.st_ino,
            )
            if child_identity != identity:
                os.close(child)
                raise ThemeLifecycleError(
                    "private lifecycle child directory changed before cleanup"
                )
            frames.append((current, detached, identity))
            opened_children.append(child)
            current = child
    finally:
        for descriptor in reversed(opened_children):
            os.close(descriptor)


def _remove_private_tree(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
    parent: _OpenedPackageDirectory | None = None,
) -> None:
    """Remove one private tree through retained descriptor-relative traversal."""

    _require_secure_mutation_support()
    parent_context = (
        nullcontext(parent) if parent is not None else _open_package_directory(path.parent)
    )
    with parent_context as opened_parent:
        identity = _relative_entry_identity(opened_parent.descriptor, path.name)
        if identity is None:
            return
        file_type, device, inode = identity
        if file_type != stat.S_IFDIR:
            raise ThemeLifecycleError("lifecycle recovery path must be a real directory")
        observed_identity = (device, inode)
        if expected_identity is not None and observed_identity != expected_identity:
            raise ThemeLifecycleError("private lifecycle directory identity changed before cleanup")

        purge_name = f"{_PURGE_PREFIX}{secrets.token_hex(16)}"
        if _relative_entry_identity(opened_parent.descriptor, purge_name) is not None:
            raise ThemeLifecycleError("private lifecycle purge path unexpectedly exists")
        operation_error: BaseException | None = None
        try:
            _rename_noreplace(
                path.name,
                purge_name,
                src_dir_fd=opened_parent.descriptor,
                dst_dir_fd=opened_parent.descriptor,
            )
        except BaseException as exc:
            operation_error = exc
        if _relative_entry_identity(opened_parent.descriptor, purge_name) != identity:
            if operation_error is not None:
                raise operation_error
            raise ThemeLifecycleError("private lifecycle directory could not be detached safely")

        retained: int | None = None
        cleanup_error: BaseException | None = None
        removal_attempted = False
        try:
            try:
                retained = os.open(
                    purge_name,
                    _directory_flags(),
                    dir_fd=opened_parent.descriptor,
                )
            except OSError as exc:
                raise ThemeLifecycleError(
                    "private lifecycle directory cannot be retained for cleanup"
                ) from exc
            retained_status = os.fstat(retained)
            if (
                stat.S_IFMT(retained_status.st_mode),
                retained_status.st_dev,
                retained_status.st_ino,
            ) != identity:
                raise ThemeLifecycleError(
                    "private lifecycle directory changed before retained cleanup"
                )
            _clear_retained_private_directory(retained)
            if _relative_entry_identity(opened_parent.descriptor, purge_name) != identity:
                raise ThemeLifecycleError(
                    "private lifecycle directory binding changed during cleanup"
                )
            removal_attempted = True
            os.rmdir(purge_name, dir_fd=opened_parent.descriptor)
        except BaseException as exc:
            cleanup_error = exc
        finally:
            if retained is not None:
                os.close(retained)

        if (
            removal_attempted
            and _relative_entry_identity(opened_parent.descriptor, purge_name) is None
        ):
            if operation_error is not None or cleanup_error is not None:
                ordinary_primary = operation_error if operation_error is not None else cleanup_error
                assert ordinary_primary is not None
                _raise_with_lifecycle_error_precedence(
                    ordinary_primary,
                    operation_error,
                    cleanup_error,
                )
            return
        if cleanup_error is not None:
            _raise_with_lifecycle_error_precedence(
                cleanup_error,
                operation_error,
                cleanup_error,
            )
        raise ThemeLifecycleError("private lifecycle directory could not be removed")


def _remove_empty_capability_probe(
    path: Path,
    *,
    parent: _OpenedPackageDirectory | None = None,
    reported_parent: Path | None = None,
) -> None:
    """Remove only a retained, verified-empty interrupted capability probe."""

    parent_context = (
        nullcontext(parent) if parent is not None else _open_package_directory(path.parent)
    )
    with parent_context as opened_parent:
        identity = _relative_entry_identity(opened_parent.descriptor, path.name)
        if identity is None:
            return
        if identity[0] != stat.S_IFDIR:
            raise ThemeLifecycleError(
                "atomic no-replace capability artifact is not a real directory"
            )
        retained_name = f"{_CAPABILITY_PREFIX}{secrets.token_hex(16)}-retained"
        _rename_noreplace(
            path.name,
            retained_name,
            src_dir_fd=opened_parent.descriptor,
            dst_dir_fd=opened_parent.descriptor,
        )
        if _relative_entry_identity(opened_parent.descriptor, retained_name) != identity:
            raise ThemeLifecycleError("atomic no-replace capability artifact could not be retained")
        try:
            descriptor = os.open(
                retained_name,
                _directory_flags(),
                dir_fd=opened_parent.descriptor,
            )
        except OSError as exc:
            raise ThemeLifecycleError(
                "atomic no-replace capability artifact cannot be inspected"
            ) from exc
        try:
            retained_status = os.fstat(descriptor)
            if (
                stat.S_IFMT(retained_status.st_mode),
                retained_status.st_dev,
                retained_status.st_ino,
            ) != identity:
                raise ThemeLifecycleError(
                    "atomic no-replace capability artifact changed before inspection"
                )
            with os.scandir(descriptor) as iterator:
                populated = next(iterator, None) is not None
        except OSError as exc:
            raise ThemeLifecycleError(
                "atomic no-replace capability artifact cannot be enumerated"
            ) from exc
        finally:
            os.close(descriptor)
        if populated:
            preserved = path.parent / (f"{_CAPABILITY_CONFLICT_PREFIX}{secrets.token_hex(16)}")
            _move_entry_to_preserved_conflict(
                path.parent / retained_name,
                preserved,
            )
            raise ThemeLifecycleError(
                "populated atomic no-replace capability artifact was preserved for "
                "operator inspection: "
                f"{(reported_parent or path.parent) / preserved.name}"
            )
        if _relative_entry_identity(opened_parent.descriptor, retained_name) != identity:
            raise ThemeLifecycleError(
                "atomic no-replace capability artifact changed before removal; all "
                "entries were preserved for operator reconciliation"
            )
        if not _remove_exact_empty_directory(
            path.parent / retained_name,
            expected_identity=(identity[1], identity[2]),
            parent=opened_parent,
        ):
            raise ThemeLifecycleError(
                "atomic no-replace capability artifact could not be removed safely"
            )


def _path_entry_identity(path: Path) -> tuple[int, int, int] | None:
    """Return a no-follow entry identity without requiring a particular file type."""

    try:
        status = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ThemeLifecycleError("theme lifecycle path identity cannot be read") from exc
    return stat.S_IFMT(status.st_mode), status.st_dev, status.st_ino


def _path_directory_identity(path: Path) -> tuple[int, int] | None:
    identity = _path_entry_identity(path)
    if identity is None:
        return None
    file_type, device, inode = identity
    if file_type != stat.S_IFDIR:
        raise ThemeLifecycleError("theme lifecycle path must remain a real directory")
    return device, inode


def _directory_entry_identity(identity: tuple[int, int]) -> tuple[int, int, int]:
    return stat.S_IFDIR, identity[0], identity[1]


def _assert_private_directory_identity(
    path: Path,
    expected_identity: tuple[int, int],
    *,
    parent: _OpenedPackageDirectory,
    label: str,
) -> None:
    if _relative_entry_identity(parent.descriptor, path.name) != _directory_entry_identity(
        expected_identity
    ):
        raise ThemeLifecycleError(f"{label} directory binding changed")


def _stage_artifact_identity(name: str) -> tuple[str, str]:
    if not name.startswith(_STAGE_PREFIX):
        raise ThemeLifecycleError("managed theme stage has an invalid name")
    suffix = name.removeprefix(_STAGE_PREFIX)
    identifier, separator, token = suffix.partition("--")
    if separator != "--" or _STAGE_TOKEN_PATTERN.fullmatch(token) is None:
        raise ThemeLifecycleError("managed theme stage lacks a valid transaction identity")
    return _theme_identifier(identifier), token


def _removal_record_artifact_identity(name: str) -> tuple[str, str]:
    if not name.startswith(_REMOVE_RECORD_PREFIX):
        raise ThemeLifecycleError("managed theme removal record has an invalid name")
    suffix = name.removeprefix(_REMOVE_RECORD_PREFIX)
    identifier, separator, token = suffix.partition("--")
    if separator != "--" or _STAGE_TOKEN_PATTERN.fullmatch(token) is None:
        raise ThemeLifecycleError("managed theme removal record lacks a valid transaction identity")
    return _theme_identifier(identifier), token


def _stage_record_document(
    interface: ThemeInterface,
    identifier: str,
    token: str,
    directory_identity: tuple[int, int],
) -> bytes:
    document = {
        "id": identifier,
        "interface": interface,
        "schema_version": _STAGE_RECORD_SCHEMA_VERSION,
        "stage_device": directory_identity[0],
        "stage_inode": directory_identity[1],
        "token": token,
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_stage_record(path: Path) -> _StageRecord:
    with _open_package_directory(path) as opened:
        try:
            descriptor = os.open(
                _STAGE_RECORD_FILENAME,
                _source_file_flags(),
                dir_fd=opened.descriptor,
            )
        except OSError as exc:
            raise ThemeLifecycleError(
                "managed theme stage transaction record cannot be opened"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size < 1
                or before.st_size > _TRANSACTION_RECORD_MAX_BYTES
            ):
                raise ThemeLifecycleError("managed theme stage transaction record is invalid")
            content = bytearray()
            while len(content) <= _TRANSACTION_RECORD_MAX_BYTES:
                chunk = os.read(
                    descriptor,
                    min(
                        _COPY_CHUNK_BYTES,
                        _TRANSACTION_RECORD_MAX_BYTES + 1 - len(content),
                    ),
                )
                if not chunk:
                    break
                content.extend(chunk)
            after = os.fstat(descriptor)
            current = os.stat(
                _STAGE_RECORD_FILENAME,
                dir_fd=opened.descriptor,
                follow_symlinks=False,
            )
            if (
                len(content) != before.st_size
                or len(content) > _TRANSACTION_RECORD_MAX_BYTES
                or _metadata_identity(after) != _metadata_identity(before)
                or _metadata_identity(current) != _metadata_identity(before)
            ):
                raise ThemeLifecycleError(
                    "managed theme stage transaction record changed while read"
                )
        except OSError as exc:
            raise ThemeLifecycleError(
                "managed theme stage transaction record cannot be read"
            ) from exc
        finally:
            os.close(descriptor)
        _assert_directory_binding(opened)
        directory_status = os.fstat(opened.descriptor)
        directory_identity = (directory_status.st_dev, directory_status.st_ino)

    try:
        parsed = json.loads(bytes(content))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ThemeLifecycleError("managed theme stage transaction record is invalid") from exc
    expected_fields = {
        "id",
        "interface",
        "schema_version",
        "stage_device",
        "stage_inode",
        "token",
    }
    if not isinstance(parsed, dict) or set(parsed) != expected_fields:
        raise ThemeLifecycleError("managed theme stage transaction record fields do not match")
    if parsed["schema_version"] != _STAGE_RECORD_SCHEMA_VERSION:
        raise ThemeLifecycleError("managed theme stage transaction record schema is unsupported")
    interface = _theme_interface(parsed["interface"])
    identifier = _theme_identifier(parsed["id"])
    token = parsed["token"]
    device = parsed["stage_device"]
    inode = parsed["stage_inode"]
    if (
        not isinstance(token, str)
        or _STAGE_TOKEN_PATTERN.fullmatch(token) is None
        or type(device) is not int
        or type(inode) is not int
        or device < 0
        or inode <= 0
        or (device, inode) != directory_identity
    ):
        raise ThemeLifecycleError("managed theme stage transaction identity is invalid")
    return _StageRecord(
        interface=interface,
        identifier=identifier,
        token=token,
        directory_identity=directory_identity,
    )


def _remove_exact_empty_directory(
    path: Path,
    *,
    expected_identity: tuple[int, int],
    parent: _OpenedPackageDirectory,
) -> bool:
    expected = _directory_entry_identity(expected_identity)
    if _relative_entry_identity(parent.descriptor, path.name) != expected:
        return False
    retained_name = f"{_PURGE_PREFIX}{secrets.token_hex(16)}"
    if _relative_entry_identity(parent.descriptor, retained_name) is not None:
        raise ThemeLifecycleError("private empty-directory detach path unexpectedly exists")
    try:
        descriptor = os.open(
            path.name,
            _directory_flags(),
            dir_fd=parent.descriptor,
        )
    except OSError as exc:
        raise ThemeLifecycleError("partial managed theme stage cannot be retained") from exc
    try:
        retained = os.fstat(descriptor)
        if (
            stat.S_IFMT(retained.st_mode),
            retained.st_dev,
            retained.st_ino,
        ) != expected:
            return False
        with os.scandir(descriptor) as iterator:
            if next(iterator, None) is not None:
                return False
        operation_error: BaseException | None = None
        try:
            _rename_noreplace(
                path.name,
                retained_name,
                src_dir_fd=parent.descriptor,
                dst_dir_fd=parent.descriptor,
            )
        except BaseException as exc:
            operation_error = exc
        if (
            _relative_entry_identity(parent.descriptor, retained_name) != expected
            or _relative_entry_identity(parent.descriptor, path.name) == expected
        ):
            if operation_error is not None:
                raise operation_error
            return False
        with os.scandir(descriptor) as iterator:
            if next(iterator, None) is not None:
                if _relative_entry_identity(parent.descriptor, path.name) is None:
                    _rename_noreplace(
                        retained_name,
                        path.name,
                        src_dir_fd=parent.descriptor,
                        dst_dir_fd=parent.descriptor,
                    )
                return False
        if _relative_entry_identity(parent.descriptor, retained_name) != expected:
            return False
        try:
            os.rmdir(retained_name, dir_fd=parent.descriptor)
        except OSError as exc:
            if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                return False
            raise ThemeLifecycleError("private empty directory cannot be removed") from exc
        removed = _relative_entry_identity(parent.descriptor, retained_name) is None
        if removed and operation_error is not None:
            raise operation_error
        return removed
    finally:
        os.close(descriptor)


def _remove_empty_incomplete_transaction_directory(
    path: Path,
    *,
    parent: _OpenedPackageDirectory | None,
) -> bool:
    parent_context = (
        nullcontext(parent) if parent is not None else _open_package_directory(path.parent)
    )
    with parent_context as opened_parent:
        identity = _relative_entry_identity(opened_parent.descriptor, path.name)
        if identity is None:
            return True
        if identity[0] != stat.S_IFDIR:
            return False
        expected_identity = (identity[1], identity[2])
        try:
            descriptor = os.open(
                path.name,
                _directory_flags(),
                dir_fd=opened_parent.descriptor,
            )
        except OSError:
            return False
        try:
            retained = os.fstat(descriptor)
            if (
                stat.S_IFMT(retained.st_mode),
                retained.st_dev,
                retained.st_ino,
            ) != identity:
                return False
            try:
                with os.scandir(descriptor) as iterator:
                    safe = next(iterator, None) is None
            except OSError:
                return False
        finally:
            os.close(descriptor)
        if not safe:
            return False
        if _relative_entry_identity(
            opened_parent.descriptor, path.name
        ) != _directory_entry_identity(expected_identity):
            return False
        _remove_private_tree(
            path,
            expected_identity=expected_identity,
            parent=opened_parent,
        )
        return True


def _create_stage_container(
    path: Path,
    *,
    interface: ThemeInterface,
    identifier: str,
    token: str,
    parent: _OpenedPackageDirectory,
) -> _StageRecord:
    named_identifier, named_token = _stage_artifact_identity(path.name)
    if named_identifier != identifier or named_token != token:
        raise ThemeLifecycleError("managed theme stage identity does not match its name")

    directory_identity: tuple[int, int] | None = None
    verified_empty = False
    try:
        if _relative_entry_identity(parent.descriptor, path.name) is not None:
            raise ThemeLifecycleError("managed theme stage already exists")
        try:
            os.mkdir(path.name, THEME_DIRECTORY_MODE, dir_fd=parent.descriptor)
        except BaseException as exc:
            interrupted = _relative_entry_identity(parent.descriptor, path.name)
            if interrupted is not None and interrupted[0] == stat.S_IFDIR:
                directory_identity = (interrupted[1], interrupted[2])
            if isinstance(exc, OSError):
                raise ThemeLifecycleError("managed theme stage cannot be created") from exc
            raise

        created = _relative_entry_identity(parent.descriptor, path.name)
        if created is None or created[0] != stat.S_IFDIR:
            raise ThemeLifecycleError("managed theme stage was not created")
        directory_identity = (created[1], created[2])
        try:
            descriptor = os.open(
                path.name,
                _directory_flags(),
                dir_fd=parent.descriptor,
            )
        except OSError as exc:
            raise ThemeLifecycleError("managed theme stage cannot be retained") from exc
        try:
            retained = os.fstat(descriptor)
            if (
                stat.S_IFMT(retained.st_mode),
                retained.st_dev,
                retained.st_ino,
            ) != _directory_entry_identity(directory_identity):
                raise ThemeLifecycleError("managed theme stage was replaced")
            with os.scandir(descriptor) as iterator:
                if next(iterator, None) is not None:
                    raise ThemeLifecycleError("new managed theme stage was unexpectedly populated")
            if _relative_entry_identity(parent.descriptor, path.name) != _directory_entry_identity(
                directory_identity
            ):
                raise ThemeLifecycleError("managed theme stage was replaced")
            verified_empty = True
            payload = _stage_record_document(
                interface,
                identifier,
                token,
                directory_identity,
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
            record_file = os.open(
                _STAGE_RECORD_FILENAME,
                flags,
                THEME_FILE_MODE,
                dir_fd=descriptor,
            )
            try:
                _write_all(record_file, payload)
                os.fchmod(record_file, THEME_FILE_MODE)
                os.fsync(record_file)
            finally:
                os.close(record_file)
            os.fsync(descriptor)
            os.fsync(parent.descriptor)
        finally:
            os.close(descriptor)

        record = _read_stage_record(path)
        if (
            record.interface != interface
            or record.identifier != identifier
            or record.token != token
            or record.directory_identity != directory_identity
        ):
            raise ThemeLifecycleError(
                "managed theme stage transaction record changed after creation"
            )
        return record
    except BaseException as creation_error:
        if directory_identity is not None:
            expected = _directory_entry_identity(directory_identity)
            observed = _relative_entry_identity(parent.descriptor, path.name)
            if observed == expected:
                if verified_empty:
                    _remove_private_tree(
                        path,
                        expected_identity=directory_identity,
                        parent=parent,
                    )
                else:
                    _remove_exact_empty_directory(
                        path,
                        expected_identity=directory_identity,
                        parent=parent,
                    )
            elif observed is not None:
                raise ThemeLifecycleError(
                    "partial managed theme stage was replaced and preserved"
                ) from creation_error
        raise


def _removal_record_document(
    interface: ThemeInterface,
    identifier: str,
    token: str,
    target_identity: tuple[int, int],
) -> bytes:
    document = {
        "id": identifier,
        "interface": interface,
        "schema_version": _REMOVAL_RECORD_SCHEMA_VERSION,
        "target_device": target_identity[0],
        "target_inode": target_identity[1],
        "token": token,
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_removal_record(path: Path) -> _RemovalRecord:
    with _open_package_directory(path) as opened:
        inventory = _package_inventory(opened.descriptor)
        image = _read_open_package(opened, inventory)
        _assert_open_package_unchanged(opened, inventory)
    if tuple(name for name, _content in image.files) != (THEME_MANIFEST_FILENAME,):
        raise ThemeLifecycleError("managed theme removal record has unexpected files")
    try:
        parsed = json.loads(image.require_file(THEME_MANIFEST_FILENAME))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ThemeLifecycleError("managed theme removal record is invalid") from exc
    expected_fields = {
        "id",
        "interface",
        "schema_version",
        "target_device",
        "target_inode",
        "token",
    }
    if not isinstance(parsed, dict) or set(parsed) != expected_fields:
        raise ThemeLifecycleError("managed theme removal record fields do not match")
    if parsed["schema_version"] != _REMOVAL_RECORD_SCHEMA_VERSION:
        raise ThemeLifecycleError("managed theme removal record schema is unsupported")
    interface = _theme_interface(parsed["interface"])
    identifier = _theme_identifier(parsed["id"])
    token = parsed["token"]
    device = parsed["target_device"]
    inode = parsed["target_inode"]
    if (
        not isinstance(token, str)
        or _STAGE_TOKEN_PATTERN.fullmatch(token) is None
        or type(device) is not int
        or type(inode) is not int
        or device < 0
        or inode <= 0
    ):
        raise ThemeLifecycleError("managed theme removal record identity is invalid")
    return _RemovalRecord(
        interface=interface,
        identifier=identifier,
        token=token,
        target_identity=(device, inode),
        directory_identity=(
            image.directory_identity[1],
            image.directory_identity[2],
        ),
    )


def _create_removal_record(
    path: Path,
    *,
    interface: ThemeInterface,
    identifier: str,
    token: str,
    target_identity: tuple[int, int],
    parent: _OpenedPackageDirectory | None = None,
) -> _RemovalRecord:
    named_identifier, named_token = _removal_record_artifact_identity(path.name)
    if named_identifier != identifier or named_token != token:
        raise ThemeLifecycleError("managed theme removal record identity does not match its name")
    payload = _removal_record_document(
        interface,
        identifier,
        token,
        target_identity,
    )
    directory_identity: tuple[int, int] | None = None
    verified_empty = False
    try:
        parent_context = (
            nullcontext(parent) if parent is not None else _open_package_directory(path.parent)
        )
        with parent_context as opened_parent:
            if _relative_entry_identity(opened_parent.descriptor, path.name) is not None:
                raise ThemeLifecycleError("managed theme removal record already exists")
            try:
                try:
                    os.mkdir(
                        path.name,
                        THEME_DIRECTORY_MODE,
                        dir_fd=opened_parent.descriptor,
                    )
                except BaseException as exc:
                    interrupted = _relative_entry_identity(
                        opened_parent.descriptor,
                        path.name,
                    )
                    if interrupted is not None and interrupted[0] == stat.S_IFDIR:
                        directory_identity = (interrupted[1], interrupted[2])
                    if isinstance(exc, OSError):
                        raise ThemeLifecycleError(
                            "managed theme removal record cannot be created"
                        ) from exc
                    raise
                created = _relative_entry_identity(opened_parent.descriptor, path.name)
                if created is None or created[0] != stat.S_IFDIR:
                    raise ThemeLifecycleError(
                        "managed theme removal record directory was not created"
                    )
                directory_identity = (created[1], created[2])
                descriptor = os.open(
                    path.name,
                    _directory_flags(),
                    dir_fd=opened_parent.descriptor,
                )
            except OSError as exc:
                raise ThemeLifecycleError("managed theme removal record cannot be created") from exc
            try:
                directory_status = os.fstat(descriptor)
                if (
                    directory_status.st_dev,
                    directory_status.st_ino,
                ) != directory_identity or _relative_entry_identity(
                    opened_parent.descriptor,
                    path.name,
                ) != _directory_entry_identity(directory_identity):
                    raise ThemeLifecycleError("managed theme removal record was replaced")
                with os.scandir(descriptor) as iterator:
                    if next(iterator, None) is not None:
                        raise ThemeLifecycleError(
                            "new managed theme removal record was unexpectedly populated"
                        )
                if _relative_entry_identity(
                    opened_parent.descriptor, path.name
                ) != _directory_entry_identity(directory_identity):
                    raise ThemeLifecycleError("managed theme removal record was replaced")
                verified_empty = True
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
                record_file = os.open(
                    THEME_MANIFEST_FILENAME,
                    flags,
                    THEME_FILE_MODE,
                    dir_fd=descriptor,
                )
                try:
                    _write_all(record_file, payload)
                    os.fchmod(record_file, THEME_FILE_MODE)
                    os.fsync(record_file)
                finally:
                    os.close(record_file)
                os.fsync(descriptor)
                os.fsync(opened_parent.descriptor)
            finally:
                os.close(descriptor)

        record = _read_removal_record(path)
        if (
            record.interface != interface
            or record.identifier != identifier
            or record.token != token
            or record.target_identity != target_identity
            or record.directory_identity != directory_identity
        ):
            raise ThemeLifecycleError("managed theme removal record changed after creation")
        return record
    except BaseException as creation_error:
        if directory_identity is not None:
            expected = _directory_entry_identity(directory_identity)
            observed = _path_entry_identity(path)
            if observed == expected:
                if verified_empty:
                    _remove_private_tree(
                        path,
                        expected_identity=directory_identity,
                        parent=parent,
                    )
                elif parent is not None:
                    _remove_exact_empty_directory(
                        path,
                        expected_identity=directory_identity,
                        parent=parent,
                    )
                else:
                    with _open_package_directory(path.parent) as opened_parent:
                        _remove_exact_empty_directory(
                            path,
                            expected_identity=directory_identity,
                            parent=opened_parent,
                        )
            elif observed is not None:
                raise ThemeLifecycleError(
                    "partial managed theme removal record was replaced and preserved"
                ) from creation_error
        raise


def _validated_recovery_package(
    directory: Path,
    *,
    target: Path,
    interface: str,
) -> tuple[ValidatedThemePackage, _PackageImage]:
    with TemporaryDirectory(prefix="sdsctl-theme-recovery-") as temporary:
        snapshot = Path(temporary) / target.name
        with _open_package_directory(directory) as opened:
            inventory = _package_inventory(opened.descriptor)
            source_image = _read_open_package(opened, inventory)
            snapshot_image = _copy_open_package_without_links(
                opened,
                inventory,
                snapshot,
            )
            if (
                snapshot_image.files != source_image.files
                or snapshot_image.sha256 != source_image.sha256
                or snapshot_image.total_bytes != source_image.total_bytes
            ):
                raise ThemeLifecycleError("managed theme rollback snapshot changed during recovery")
            package = _validate_private_theme_package(
                snapshot,
                reported_path=target,
                expected=snapshot_image,
            )
            _assert_open_package_unchanged(opened, inventory)
    if package.summary.interface != interface or package.summary.identifier != target.name:
        raise ThemeLifecycleError("managed theme rollback identity does not match its target")
    return package, source_image


def _move_entry_to_private_quarantine(
    source: Path,
    quarantine: Path,
    *,
    container: Path,
    container_identity: tuple[int, int],
    opened_container: _OpenedPackageDirectory | None = None,
) -> bool:
    """Move one exact pathname entry without following it into a private container."""

    expected_container = _directory_entry_identity(container_identity)
    if opened_container is None:
        if _path_entry_identity(container) != expected_container:
            raise ThemeLifecycleError("private publication stage was replaced during recovery")
    else:
        retained_status = os.fstat(opened_container.descriptor)
        if (
            stat.S_IFMT(retained_status.st_mode),
            retained_status.st_dev,
            retained_status.st_ino,
        ) != expected_container:
            raise ThemeLifecycleError(
                "private publication stage descriptor changed during recovery"
            )
    source_identity = _path_entry_identity(source)
    if source_identity is None:
        return False
    if _path_entry_identity(quarantine) is not None:
        raise ThemeLifecycleError("private publication quarantine is not empty")

    operation_error: BaseException | None = None
    try:
        _rename_noreplace(source, quarantine)
    except BaseException as exc:
        operation_error = exc

    moved = (
        (
            _path_entry_identity(container) == expected_container
            if opened_container is None
            else (
                stat.S_IFMT(os.fstat(opened_container.descriptor).st_mode),
                os.fstat(opened_container.descriptor).st_dev,
                os.fstat(opened_container.descriptor).st_ino,
            )
            == expected_container
        )
        and _path_entry_identity(quarantine) == source_identity
        and _path_entry_identity(source) != source_identity
    )
    if moved:
        if operation_error is not None:
            raise operation_error
        return True
    if operation_error is not None:
        raise operation_error
    raise ThemeLifecycleError("published theme could not be quarantined safely")


def _move_entry_to_preserved_conflict(source: Path, conflict: Path) -> bool:
    """Detach an unknown target entry without following or deleting it."""

    source_identity = _path_entry_identity(source)
    if source_identity is None:
        return False
    if _path_entry_identity(conflict) is not None:
        raise ThemeLifecycleError(
            f"managed theme conflict quarantine already exists: {conflict.name}"
        )
    operation_error: BaseException | None = None
    try:
        _rename_noreplace(source, conflict)
    except BaseException as exc:
        operation_error = exc
    if (
        _path_entry_identity(conflict) == source_identity
        and _path_entry_identity(source) != source_identity
    ):
        if operation_error is not None:
            raise operation_error
        return True
    if operation_error is not None:
        raise operation_error
    raise ThemeLifecycleError("foreign managed theme target could not be quarantined")


def _quarantine_failed_publication(
    target: Path,
    *,
    staged_identity: tuple[int, int] | None,
    stage_container: Path,
    stage_container_identity: tuple[int, int],
    conflict: Path,
    opened_stage: _OpenedPackageDirectory | None = None,
) -> Path | None:
    """Delete only the known stage; preserve an unknown target as a conflict."""

    target_identity = _path_entry_identity(target)
    if target_identity is None:
        return None
    if staged_identity is not None and target_identity == _directory_entry_identity(
        staged_identity
    ):
        quarantine_root = (
            _retained_directory_path(opened_stage) if opened_stage is not None else stage_container
        )
        _move_entry_to_private_quarantine(
            target,
            quarantine_root / _FAILED_PUBLICATION_NAME,
            container=stage_container,
            container_identity=stage_container_identity,
            opened_container=opened_stage,
        )
        return None
    _move_entry_to_preserved_conflict(target, conflict)
    return conflict


def _try_restore_previous_package(
    rollback: Path,
    target: Path,
    previous_identity: tuple[int, int],
) -> bool:
    """Atomically restore the retained package, tolerating a foreign leaf entry."""

    expected = _directory_entry_identity(previous_identity)
    if _path_entry_identity(target) == expected:
        return True
    if _path_entry_identity(rollback) != expected:
        raise ThemeLifecycleError("previous managed theme is unavailable for rollback")

    operation_error: BaseException | None = None
    try:
        _rename_noreplace(rollback, target)
    except BaseException as exc:
        operation_error = exc

    if _path_entry_identity(target) == expected:
        if operation_error is not None:
            raise operation_error
        return True
    if isinstance(operation_error, OSError) and operation_error.errno in {
        errno.EEXIST,
        errno.EISDIR,
        errno.ENOTEMPTY,
        errno.ENOTDIR,
    }:
        return False
    if operation_error is not None:
        raise operation_error
    raise ThemeLifecycleError("previous managed theme could not be restored")


def _recover_interface(
    interface_root: Path,
    *,
    opened: _OpenedPackageDirectory | None = None,
    reported_root: Path | None = None,
) -> None:
    interface = _theme_interface(opened.path.name if opened is not None else interface_root.name)
    operator_root = reported_root or interface_root
    try:
        entries = sorted(interface_root.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ThemeLifecycleError("theme interface directory cannot be recovered") from exc
    capability_conflicts = tuple(
        entry for entry in entries if entry.name.startswith(_CAPABILITY_CONFLICT_PREFIX)
    )
    if capability_conflicts:
        raise ThemeLifecycleError(
            "managed theme interface has a preserved capability-probe conflict; "
            "inspect and remove or relocate it before retrying: "
            f"{operator_root / capability_conflicts[0].name}"
        )
    purge_artifacts = tuple(entry for entry in entries if entry.name.startswith(_PURGE_PREFIX))
    if purge_artifacts:
        raise ThemeLifecycleError(
            "managed theme interface has an unauthenticated interrupted purge; it "
            "was preserved for explicit operator reconciliation: "
            f"{operator_root / purge_artifacts[0].name}"
        )

    operations: dict[str, set[str]] = {}
    removal_records: dict[str, tuple[Path, str]] = {}
    removal_tombstones: dict[str, Path] = {}
    for entry in entries:
        operation: str | None = None
        identifier: str | None = None
        if entry.name.startswith(_STAGE_PREFIX):
            operation = "stage"
            identifier, _token = _stage_artifact_identity(entry.name)
        elif entry.name.startswith(_ROLLBACK_PREFIX):
            operation = "rollback"
            identifier = entry.name.removeprefix(_ROLLBACK_PREFIX)
        elif entry.name.startswith(_REMOVE_RECORD_PREFIX):
            operation = "remove"
            identifier, record_token = _removal_record_artifact_identity(entry.name)
        elif entry.name.startswith(_REMOVE_PREFIX):
            operation = "remove"
            identifier = entry.name.removeprefix(_REMOVE_PREFIX)
        if operation is not None and identifier is not None:
            normalized = _theme_identifier(identifier)
            operations.setdefault(normalized, set()).add(operation)
            if entry.name.startswith(_REMOVE_RECORD_PREFIX):
                if normalized in removal_records:
                    raise ThemeLifecycleError(
                        "managed theme has multiple removal transaction records; all "
                        "were preserved for explicit operator reconciliation"
                    )
                removal_records[normalized] = (entry, record_token)
            elif entry.name.startswith(_REMOVE_PREFIX):
                removal_tombstones[normalized] = entry
    for identifier, pending in operations.items():
        if "remove" in pending and pending.intersection({"rollback", "stage"}):
            kinds = ", ".join(sorted(pending))
            raise ThemeLifecycleError(
                "managed theme has incompatible pending lifecycle artifacts for "
                f"{identifier}: {kinds}; all were preserved for explicit operator "
                "reconciliation"
            )
    orphaned_tombstones = sorted(set(removal_tombstones) - set(removal_records))
    if orphaned_tombstones:
        identifiers = ", ".join(orphaned_tombstones)
        raise ThemeLifecycleError(
            "managed theme removal tombstone lacks its transaction record; it was "
            f"preserved for explicit operator reconciliation: {identifiers}"
        )

    for entry in entries:
        if entry.name.startswith(_CAPABILITY_PREFIX):
            _remove_empty_capability_probe(
                entry,
                parent=opened,
                reported_parent=operator_root,
            )
        elif entry.name.startswith(_STAGE_PREFIX):
            identifier, token = _stage_artifact_identity(entry.name)
            try:
                stage_record = _read_stage_record(entry)
            except ThemeLifecycleError:
                if _remove_empty_incomplete_transaction_directory(
                    entry,
                    parent=opened,
                ):
                    continue
                raise
            if (
                stage_record.interface != interface
                or stage_record.identifier != identifier
                or stage_record.token != token
            ):
                raise ThemeLifecycleError(
                    "managed theme stage transaction record does not match its location"
                )
            _remove_private_tree(
                entry,
                expected_identity=stage_record.directory_identity,
                parent=opened,
            )
        elif entry.name.startswith(_ROLLBACK_PREFIX):
            identifier = entry.name.removeprefix(_ROLLBACK_PREFIX)
            _theme_identifier(identifier)
            target = interface_root / identifier
            _package, image = _validated_recovery_package(
                entry,
                target=target,
                interface=interface,
            )
            rollback_identity = (
                image.directory_identity[1],
                image.directory_identity[2],
            )
            rollback_entry = _directory_entry_identity(rollback_identity)
            if _path_entry_identity(target) is not None:
                active = validate_theme_package(target)
                if active.summary.interface != interface or active.summary.identifier != identifier:
                    raise ThemeLifecycleError(
                        "managed theme target is invalid during rollback recovery"
                    )
                raise ThemeLifecycleError(
                    "managed theme target and validated rollback both exist; both were "
                    "preserved for explicit operator reconciliation"
                )
            else:
                if _path_entry_identity(entry) != rollback_entry:
                    raise ThemeLifecycleError("managed theme rollback changed before promotion")
                promotion_error: BaseException | None = None
                try:
                    _rename_noreplace(entry, target)
                except BaseException as exc:
                    promotion_error = exc
                promoted = _path_entry_identity(target) == rollback_entry
                if not promoted:
                    conflict_error: BaseException | None = None
                    if _path_entry_identity(target) is not None:
                        conflict = interface_root / f"{_CONFLICT_PREFIX}{identifier}"
                        try:
                            _move_entry_to_preserved_conflict(target, conflict)
                        except BaseException as exc:
                            conflict_error = exc
                    promotion_failure = ThemeLifecycleError(
                        "managed theme rollback could not be promoted safely"
                    )
                    _raise_with_lifecycle_error_precedence(
                        conflict_error or promotion_failure,
                        promotion_error,
                        conflict_error,
                        promotion_failure,
                    )
                try:
                    _assert_package_image(target, image, require_identity=True)
                except BaseException as validation_error:
                    target_entry = _path_entry_identity(target)
                    return_error: BaseException | None = None
                    conflict_error = None
                    if target_entry == rollback_entry:
                        try:
                            _rename_noreplace(target, entry)
                        except BaseException as exc:
                            return_error = exc
                        if (
                            _path_entry_identity(entry) != rollback_entry
                            or _path_entry_identity(target) == rollback_entry
                        ):
                            return_failure = ThemeLifecycleError(
                                "unverified promoted rollback could not be retained"
                            )
                            _raise_with_lifecycle_error_precedence(
                                return_failure,
                                promotion_error,
                                validation_error,
                                return_error,
                                return_failure,
                            )
                    elif target_entry is not None:
                        conflict = interface_root / f"{_CONFLICT_PREFIX}{identifier}"
                        try:
                            _move_entry_to_preserved_conflict(target, conflict)
                        except BaseException as exc:
                            conflict_error = exc
                    if return_error is not None or conflict_error is not None:
                        ordinary_primary = return_error or conflict_error
                        assert ordinary_primary is not None
                        _raise_with_lifecycle_error_precedence(
                            ordinary_primary,
                            promotion_error,
                            validation_error,
                            return_error,
                            conflict_error,
                        )
                    validation_failure = ThemeLifecycleError(
                        "promoted managed theme rollback failed exact image verification"
                    )
                    _raise_with_lifecycle_error_precedence(
                        validation_failure,
                        promotion_error,
                        validation_error,
                        validation_failure,
                    )
                if promotion_error is not None:
                    raise promotion_error
        elif entry.name.startswith((_REMOVE_PREFIX, _REMOVE_RECORD_PREFIX)):
            continue

    for identifier, (record_path, token) in sorted(removal_records.items()):
        tombstone = removal_tombstones.get(identifier)
        target = interface_root / identifier
        try:
            removal_record = _read_removal_record(record_path)
        except ThemeLifecycleError:
            if (
                tombstone is None
                and _path_entry_identity(target) is not None
                and _remove_empty_incomplete_transaction_directory(
                    record_path,
                    parent=opened,
                )
            ):
                continue
            raise
        if (
            removal_record.interface != interface
            or removal_record.identifier != identifier
            or removal_record.token != token
        ):
            raise ThemeLifecycleError(
                "managed theme removal record identity does not match its location"
            )
        if opened is not None:
            _assert_private_directory_identity(
                record_path,
                removal_record.directory_identity,
                parent=opened,
                label="managed theme removal record",
            )
        elif _path_entry_identity(record_path) != _directory_entry_identity(
            removal_record.directory_identity
        ):
            raise ThemeLifecycleError("managed theme removal record directory binding changed")
        target_entry = _path_entry_identity(target)
        expected_target = _directory_entry_identity(removal_record.target_identity)
        if tombstone is None:
            if target_entry not in {None, expected_target}:
                raise ThemeLifecycleError(
                    "managed theme target changed while a removal record was pending; "
                    "both were preserved for explicit operator reconciliation"
                )
        else:
            if opened is not None:
                _assert_private_directory_identity(
                    record_path,
                    removal_record.directory_identity,
                    parent=opened,
                    label="managed theme removal record",
                )
            elif _path_entry_identity(record_path) != _directory_entry_identity(
                removal_record.directory_identity
            ):
                raise ThemeLifecycleError("managed theme removal record directory binding changed")
            if _path_entry_identity(tombstone) != expected_target:
                raise ThemeLifecycleError(
                    "managed theme removal tombstone does not match its transaction "
                    "record; both were preserved for explicit operator reconciliation"
                )
            if target_entry is not None:
                raise ThemeLifecycleError(
                    "managed theme target and recorded removal tombstone both exist; "
                    "both were preserved for explicit operator reconciliation"
                )
            _remove_private_tree(
                tombstone,
                expected_identity=removal_record.target_identity,
                parent=opened,
            )
        _remove_private_tree(
            record_path,
            expected_identity=removal_record.directory_identity,
            parent=opened,
        )


def _require_no_lifecycle_conflict(
    interface_root: Path,
    identifier: str,
    *,
    reported_root: Path | None = None,
) -> Path:
    conflict = interface_root / f"{_CONFLICT_PREFIX}{identifier}"
    if _path_entry_identity(conflict) is not None:
        raise ThemeLifecycleError(
            "managed theme has a preserved concurrent-write conflict; inspect and "
            "remove or relocate it before retrying: "
            f"{(reported_root or interface_root) / conflict.name}"
        )
    return conflict


@contextmanager
def _lifecycle_lock(root: Path) -> Iterator[_OpenedPackageDirectory]:
    try:
        import fcntl
    except ModuleNotFoundError as exc:
        raise ThemeLifecycleError(
            "managed theme mutation requires operating-system file locking"
        ) from exc

    root_identity = _prepare_directory(root)
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with _open_package_directory(root) as opened_root:
        if (
            opened_root.descriptor_identity[1],
            opened_root.descriptor_identity[2],
        ) != root_identity:
            raise ThemeLifecycleError("managed theme root changed before locking")
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                flags,
                THEME_FILE_MODE,
                dir_fd=opened_root.descriptor,
            )
        except OSError as exc:
            raise ThemeLifecycleError("theme lifecycle lock cannot be opened") from exc
        try:
            lock_status = os.fstat(descriptor)
            lock_path_status = os.stat(
                _LOCK_FILENAME,
                dir_fd=opened_root.descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(lock_status.st_mode)
                or not stat.S_ISREG(lock_path_status.st_mode)
                or lock_status.st_nlink != 1
                or lock_path_status.st_nlink != 1
                or (lock_status.st_dev, lock_status.st_ino)
                != (lock_path_status.st_dev, lock_path_status.st_ino)
            ):
                raise ThemeLifecycleError("theme lifecycle lock must be one private regular file")
            os.fchmod(descriptor, THEME_FILE_MODE)
            locked_status = os.fstat(descriptor)
            locked_path_status = os.stat(
                _LOCK_FILENAME,
                dir_fd=opened_root.descriptor,
                follow_symlinks=False,
            )
            if (
                locked_status.st_nlink != 1
                or locked_path_status.st_nlink != 1
                or (locked_status.st_dev, locked_status.st_ino)
                != (locked_path_status.st_dev, locked_path_status.st_ino)
            ):
                raise ThemeLifecycleError("theme lifecycle lock changed before acquisition")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise ThemeLifecycleError(
                        "another theme lifecycle operation is in progress"
                    ) from exc
                raise ThemeLifecycleError("theme lifecycle lock cannot be acquired") from exc
            try:
                _assert_directory_binding(opened_root)
                yield opened_root
                _assert_directory_binding(opened_root)
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
    _require_secure_mutation_support()
    with _validated_theme_source_snapshot(source) as source_snapshot:
        package = source_snapshot.package
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

        resolved_root = managed_root.resolve(strict=False)
        resolved_source = source_snapshot.source.resolved_path
        if resolved_source == resolved_root or resolved_root in resolved_source.parents:
            raise ThemeLifecycleError("theme source must be outside the managed theme root")

        configured_interface_root = managed_root / interface
        with (
            _lifecycle_lock(managed_root) as opened_root,
            _prepare_interface_directory(
                opened_root,
                interface,
                configured_interface_root,
            ) as opened_interface,
        ):
            _assert_open_package_unchanged(
                source_snapshot.source,
                source_snapshot.source_inventory,
            )
            interface_root = _retained_directory_path(opened_interface)
            retained_root = interface_root / ".."
            _probe_atomic_noreplace(interface_root, opened=opened_interface)
            _recover_interface(
                interface_root,
                opened=opened_interface,
                reported_root=configured_interface_root,
            )
            _assert_lifecycle_bindings(opened_root, opened_interface)
            target = interface_root / identifier
            discovery_target = retained_root / interface / identifier
            conflict = _require_no_lifecycle_conflict(
                interface_root,
                identifier,
                reported_root=configured_interface_root,
            )
            stage_token = secrets.token_hex(16)
            stage_container = interface_root / (f"{_STAGE_PREFIX}{identifier}--{stage_token}")
            rollback = interface_root / f"{_ROLLBACK_PREFIX}{identifier}"

            target_exists = target.exists()
            if target_exists and not replace:
                raise ThemeLifecycleError("managed theme already exists; use explicit replacement")
            previous_identity: tuple[int, int] | None = None
            if target_exists:
                target_status = target.lstat()
                if stat.S_ISLNK(target_status.st_mode) or not stat.S_ISDIR(target_status.st_mode):
                    raise ThemeLifecycleError("existing managed theme must be a real directory")
                previous_identity = (target_status.st_dev, target_status.st_ino)

            _assert_lifecycle_bindings(opened_root, opened_interface)
            _validate_candidate_collision(
                package,
                retained_root,
                excluded=discovery_target if target_exists else None,
            )
            _assert_lifecycle_bindings(opened_root, opened_interface)
            if _path_entry_identity(rollback) is not None:
                raise ThemeLifecycleError(
                    "managed theme rollback appeared after recovery; it was preserved"
                )
            stage_container_identity: tuple[int, int] | None = None
            opened_stage: _OpenedPackageDirectory | None = None
            staged_identity: tuple[int, int] | None = None
            publication_attempted = False
            previous_saved = False
            committed = False
            preserved_conflict: Path | None = None
            try:
                stage_record = _create_stage_container(
                    stage_container,
                    interface=interface,
                    identifier=identifier,
                    token=stage_token,
                    parent=opened_interface,
                )
                stage_container_identity = stage_record.directory_identity
                opened_stage = _retain_package_directory_relative(
                    opened_interface,
                    stage_container.name,
                    stage_container,
                )
                if (
                    opened_stage.descriptor_identity[1],
                    opened_stage.descriptor_identity[2],
                ) != stage_container_identity:
                    raise ThemeLifecycleError("managed theme stage changed before retained staging")
                stage = _retained_directory_path(opened_stage) / identifier
                staged_image = _copy_package_without_links(
                    source_snapshot.directory,
                    stage,
                    destination_parent=opened_stage,
                )
                staged_identity = (
                    staged_image.directory_identity[1],
                    staged_image.directory_identity[2],
                )
                staged = _validate_private_theme_package(
                    stage,
                    reported_path=stage,
                    expected=staged_image,
                )
                if (
                    staged.summary.interface != interface
                    or staged.summary.identifier != identifier
                    or staged.summary.sha256 != source_snapshot.image.sha256
                    or staged.manifest != package.manifest
                    or staged_image.files != source_snapshot.image.files
                    or staged_image.total_bytes != source_snapshot.image.total_bytes
                ):
                    raise ThemeLifecycleError(
                        "staged theme package does not match the validated snapshot"
                    )
                _assert_lifecycle_bindings(opened_root, opened_interface)
                _validate_candidate_collision(
                    staged,
                    retained_root,
                    excluded=discovery_target if target_exists else None,
                )
                _assert_lifecycle_bindings(opened_root, opened_interface)
                _assert_open_package_unchanged(
                    source_snapshot.source,
                    source_snapshot.source_inventory,
                )
                _assert_package_image(stage, staged_image, require_identity=True)
                _assert_directory_binding(opened_stage)
                if previous_identity is not None:
                    _assert_lifecycle_bindings(opened_root, opened_interface)
                    try:
                        _rename_noreplace(target, rollback)
                    finally:
                        previous_saved = _path_entry_identity(
                            rollback
                        ) == _directory_entry_identity(previous_identity)
                    if not previous_saved:
                        raise ThemeLifecycleError(
                            "previous managed theme could not be retained for rollback"
                        )
                    _assert_lifecycle_bindings(opened_root, opened_interface)
                _assert_lifecycle_bindings(opened_root, opened_interface)
                _assert_directory_binding(opened_stage)
                publication_attempted = True
                try:
                    _rename_noreplace(stage, target)
                finally:
                    published = _path_entry_identity(target) == _directory_entry_identity(
                        staged_identity
                    )
                if not published:
                    raise ThemeLifecycleError("private theme stage could not be published")
                _assert_package_image(target, staged_image, require_identity=True)
                installed = validate_theme_package(target)
                if (
                    installed.summary.interface != interface
                    or installed.summary.identifier != identifier
                    or installed.summary.sha256 != staged_image.sha256
                    or installed.manifest != staged.manifest
                ):
                    raise ThemeLifecycleError(
                        "published theme package does not match its private stage"
                    )
                _assert_lifecycle_bindings(opened_root, opened_interface)
                _validate_candidate_collision(
                    installed,
                    retained_root,
                    excluded=discovery_target,
                )
                _assert_lifecycle_bindings(opened_root, opened_interface)
                _assert_package_image(target, staged_image, require_identity=True)
                _assert_lifecycle_bindings(opened_root, opened_interface)
                _assert_directory_binding(opened_stage)
                if previous_identity is not None:
                    if _path_entry_identity(rollback) != _directory_entry_identity(
                        previous_identity
                    ):
                        raise ThemeLifecycleError(
                            "previous managed theme changed before transaction commit"
                        )
                    committed = True
                    _remove_private_tree(
                        rollback,
                        expected_identity=previous_identity,
                        parent=opened_interface,
                    )
                else:
                    committed = True
            except BaseException as operation_error:
                if committed:
                    raise
                if previous_identity is not None:
                    previous_entry = _directory_entry_identity(previous_identity)
                    target_entry = _path_entry_identity(target)
                    rollback_entry = _path_entry_identity(rollback)
                    if target_entry != previous_entry:
                        if rollback_entry != previous_entry:
                            raise ThemeLifecycleError(
                                "managed theme replacement could not preserve its rollback"
                            ) from operation_error
                        if target_entry is not None:
                            if stage_container_identity is None:
                                raise ThemeLifecycleError(
                                    "private publication stage is unavailable for rollback"
                                ) from operation_error
                            preserved_conflict = _quarantine_failed_publication(
                                target,
                                staged_identity=staged_identity,
                                stage_container=stage_container,
                                stage_container_identity=stage_container_identity,
                                conflict=conflict,
                                opened_stage=opened_stage,
                            )
                        if not _try_restore_previous_package(
                            rollback,
                            target,
                            previous_identity,
                        ):
                            raise ThemeLifecycleError(
                                "previous managed theme could not be restored"
                            ) from operation_error
                    if _path_entry_identity(target) != previous_entry:
                        raise ThemeLifecycleError(
                            "previous managed theme was not restored after failure"
                        ) from operation_error
                elif publication_attempted and _path_entry_identity(target) is not None:
                    if stage_container_identity is None:
                        raise ThemeLifecycleError(
                            "private publication stage is unavailable for cleanup"
                        ) from operation_error
                    preserved_conflict = _quarantine_failed_publication(
                        target,
                        staged_identity=staged_identity,
                        stage_container=stage_container,
                        stage_container_identity=stage_container_identity,
                        conflict=conflict,
                        opened_stage=opened_stage,
                    )
                if preserved_conflict is not None:
                    raise ThemeLifecycleError(
                        "a concurrent entry replaced the managed theme target; it was "
                        "preserved for operator inspection at "
                        f"{configured_interface_root / preserved_conflict.name}"
                    ) from operation_error
                raise
            finally:
                try:
                    if stage_container_identity is not None:
                        if _path_entry_identity(stage_container) != _directory_entry_identity(
                            stage_container_identity
                        ):
                            raise ThemeLifecycleError(
                                "private publication stage changed before cleanup"
                            )
                        _remove_private_tree(
                            stage_container,
                            expected_identity=stage_container_identity,
                            parent=opened_interface,
                        )
                    elif _path_entry_identity(stage_container) is not None:
                        raise ThemeLifecycleError(
                            "unrecognized private publication stage cannot be removed"
                        )
                finally:
                    if opened_stage is not None:
                        os.close(opened_stage.descriptor)

        return ThemePackageSummary(
            interface=interface,
            identifier=identifier,
            label=installed.summary.label,
            order=installed.summary.order,
            origin="managed",
            executable=installed.summary.executable,
            path=managed_root / interface / identifier,
            sha256=installed.summary.sha256,
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
    _require_secure_mutation_support()
    normalized_interface = _theme_interface(interface)
    normalized_identifier = _theme_identifier(identifier)
    expected_confirmation = f"{normalized_interface}/{normalized_identifier}"
    if confirmation != expected_confirmation:
        raise ThemeLifecycleError(
            f"removal confirmation must exactly match: {expected_confirmation}"
        )
    if normalized_identifier in _built_in_ids(normalized_interface):
        raise ThemeLifecycleError("built-in themes cannot be removed")

    configured_interface_root = managed_root / normalized_interface
    with (
        _lifecycle_lock(managed_root) as opened_root,
        _prepare_interface_directory(
            opened_root,
            normalized_interface,
            configured_interface_root,
        ) as opened_interface,
    ):
        interface_root = _retained_directory_path(opened_interface)
        _probe_atomic_noreplace(interface_root, opened=opened_interface)
        _recover_interface(
            interface_root,
            opened=opened_interface,
            reported_root=configured_interface_root,
        )
        _assert_lifecycle_bindings(opened_root, opened_interface)
        conflict = _require_no_lifecycle_conflict(
            interface_root,
            normalized_identifier,
            reported_root=configured_interface_root,
        )
        if normalized_interface == "home-assistant":
            from .home_assistant_theme_activation import (
                ensure_home_assistant_theme_inactive,
            )

            _assert_lifecycle_bindings(opened_root, opened_interface)
            ensure_home_assistant_theme_inactive(
                managed_root,
                normalized_identifier,
                root_descriptor=opened_root.descriptor,
            )
            _assert_lifecycle_bindings(opened_root, opened_interface)
        target = interface_root / normalized_identifier
        tombstone = interface_root / f"{_REMOVE_PREFIX}{normalized_identifier}"
        record_token = secrets.token_hex(16)
        record_path = interface_root / (
            f"{_REMOVE_RECORD_PREFIX}{normalized_identifier}--{record_token}"
        )
        if not target.exists():
            raise ThemeLifecycleError("managed theme does not exist")
        target_status = target.lstat()
        if stat.S_ISLNK(target_status.st_mode) or not stat.S_ISDIR(target_status.st_mode):
            raise ThemeLifecycleError("managed theme must be a real directory")
        target_identity = (target_status.st_dev, target_status.st_ino)
        record: _RemovalRecord | None = None
        try:
            _assert_lifecycle_bindings(opened_root, opened_interface)
            record = _create_removal_record(
                record_path,
                interface=normalized_interface,
                identifier=normalized_identifier,
                token=record_token,
                target_identity=target_identity,
                parent=opened_interface,
            )
            _assert_private_directory_identity(
                record_path,
                record.directory_identity,
                parent=opened_interface,
                label="managed theme removal record",
            )
            _assert_lifecycle_bindings(opened_root, opened_interface)
        except BaseException:
            if record is not None:
                _remove_private_tree(
                    record_path,
                    expected_identity=record.directory_identity,
                    parent=opened_interface,
                )
            raise
        assert record is not None
        saved = False
        try:
            _assert_lifecycle_bindings(opened_root, opened_interface)
            _assert_private_directory_identity(
                record_path,
                record.directory_identity,
                parent=opened_interface,
                label="managed theme removal record",
            )
            try:
                _rename_noreplace(target, tombstone)
            finally:
                saved = _path_entry_identity(tombstone) == _directory_entry_identity(
                    target_identity
                )
            _assert_private_directory_identity(
                record_path,
                record.directory_identity,
                parent=opened_interface,
                label="managed theme removal record",
            )
            _assert_lifecycle_bindings(opened_root, opened_interface)
        except BaseException as retention_error:
            observation_error: BaseException | None = None
            if not saved:
                try:
                    saved = _path_entry_identity(tombstone) == _directory_entry_identity(
                        target_identity
                    )
                except BaseException as exc:
                    observation_error = exc
                    saved = False
            if saved:
                restore_error: BaseException | None = None
                try:
                    _rename_noreplace(tombstone, target)
                except BaseException as exc:
                    restore_error = exc
                finally:
                    restored = _path_entry_identity(target) == _directory_entry_identity(
                        target_identity
                    )
                if not restored:
                    restoration_failure = ThemeLifecycleError(
                        "managed theme removal could not restore its retained target"
                    )
                    _raise_with_lifecycle_error_precedence(
                        restoration_failure,
                        retention_error,
                        restore_error,
                        restoration_failure,
                    )
                record_cleanup_error: BaseException | None = None
                try:
                    _remove_private_tree(
                        record_path,
                        expected_identity=record.directory_identity,
                        parent=opened_interface,
                    )
                except BaseException as exc:
                    record_cleanup_error = exc
                if restore_error is not None or record_cleanup_error is not None:
                    ordinary_primary = restore_error or record_cleanup_error
                    assert ordinary_primary is not None
                    _raise_with_lifecycle_error_precedence(
                        ordinary_primary,
                        retention_error,
                        restore_error,
                        record_cleanup_error,
                    )
            else:
                tombstone_entry = _path_entry_identity(tombstone)
                target_entry = _path_entry_identity(target)
                if (
                    tombstone_entry is not None
                    and tombstone_entry != _directory_entry_identity(target_identity)
                    and target_entry == _directory_entry_identity(target_identity)
                ):
                    conflict_error: BaseException | None = None
                    try:
                        _move_entry_to_preserved_conflict(tombstone, conflict)
                    except BaseException as exc:
                        conflict_error = exc
                    if conflict_error is not None:
                        _raise_with_lifecycle_error_precedence(
                            conflict_error,
                            retention_error,
                            observation_error,
                            conflict_error,
                        )
                    record_cleanup_error = None
                    try:
                        _remove_private_tree(
                            record_path,
                            expected_identity=record.directory_identity,
                            parent=opened_interface,
                        )
                    except BaseException as exc:
                        record_cleanup_error = exc
                    if observation_error is not None or record_cleanup_error is not None:
                        ordinary_primary = observation_error or record_cleanup_error
                        assert ordinary_primary is not None
                        _raise_with_lifecycle_error_precedence(
                            ordinary_primary,
                            retention_error,
                            observation_error,
                            record_cleanup_error,
                        )
                    conflict_failure = ThemeLifecycleError(
                        "a concurrent entry occupied the managed theme removal "
                        "tombstone; it was preserved for operator inspection: "
                        f"{configured_interface_root / conflict.name}"
                    )
                    _raise_with_lifecycle_error_precedence(
                        conflict_failure,
                        retention_error,
                        conflict_failure,
                    )
                if tombstone_entry is None and target_entry == _directory_entry_identity(
                    target_identity
                ):
                    record_cleanup_error = None
                    try:
                        _remove_private_tree(
                            record_path,
                            expected_identity=record.directory_identity,
                            parent=opened_interface,
                        )
                    except BaseException as exc:
                        record_cleanup_error = exc
                    if observation_error is not None or record_cleanup_error is not None:
                        ordinary_primary = observation_error or record_cleanup_error
                        assert ordinary_primary is not None
                        _raise_with_lifecycle_error_precedence(
                            ordinary_primary,
                            retention_error,
                            observation_error,
                            record_cleanup_error,
                        )
                if observation_error is not None:
                    _raise_with_lifecycle_error_precedence(
                        observation_error,
                        retention_error,
                        observation_error,
                    )
            raise
        if not saved:
            if _path_entry_identity(tombstone) is not None:
                _move_entry_to_preserved_conflict(tombstone, conflict)
                _remove_private_tree(
                    record_path,
                    expected_identity=record.directory_identity,
                    parent=opened_interface,
                )
                raise ThemeLifecycleError(
                    "a concurrent entry replaced the managed theme during removal; "
                    "the unexpected entry was preserved for operator inspection: "
                    f"{configured_interface_root / conflict.name}"
                )
            raise ThemeLifecycleError("managed theme could not be retained for removal")
        try:
            _assert_lifecycle_bindings(opened_root, opened_interface)
            _assert_private_directory_identity(
                record_path,
                record.directory_identity,
                parent=opened_interface,
                label="managed theme removal record",
            )
            _remove_private_tree(
                tombstone,
                expected_identity=target_identity,
                parent=opened_interface,
            )
        except BaseException as cleanup_error:
            if (
                _path_entry_identity(tombstone) == _directory_entry_identity(target_identity)
                and _path_entry_identity(target) is None
            ):
                restore_error = None
                try:
                    _rename_noreplace(tombstone, target)
                except BaseException as exc:
                    restore_error = exc
                finally:
                    restored = _path_entry_identity(target) == _directory_entry_identity(
                        target_identity
                    )
                if not restored:
                    restoration_failure = ThemeLifecycleError(
                        "managed theme removal could not restore its retained target"
                    )
                    _raise_with_lifecycle_error_precedence(
                        restoration_failure,
                        cleanup_error,
                        restore_error,
                        restoration_failure,
                    )
                record_cleanup_error = None
                try:
                    _remove_private_tree(
                        record_path,
                        expected_identity=record.directory_identity,
                        parent=opened_interface,
                    )
                except BaseException as exc:
                    record_cleanup_error = exc
                if restore_error is not None or record_cleanup_error is not None:
                    ordinary_primary = restore_error or record_cleanup_error
                    assert ordinary_primary is not None
                    _raise_with_lifecycle_error_precedence(
                        ordinary_primary,
                        cleanup_error,
                        restore_error,
                        record_cleanup_error,
                    )
            raise
        if _path_entry_identity(target) is not None:
            _move_entry_to_preserved_conflict(target, conflict)
            _remove_private_tree(
                record_path,
                expected_identity=record.directory_identity,
                parent=opened_interface,
            )
            raise ThemeLifecycleError(
                "a concurrent entry appeared at the removed managed theme target; "
                "the unexpected entry was preserved for operator inspection: "
                f"{configured_interface_root / conflict.name}"
            )
        _remove_private_tree(
            record_path,
            expected_identity=record.directory_identity,
            parent=opened_interface,
        )

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
