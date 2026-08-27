from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias

from .home_assistant_themes import (
    BUILT_IN_HOME_ASSISTANT_THEME_IDS,
    HomeAssistantThemeManifest,
)
from .theme_lifecycle import (
    HOME_ASSISTANT_CODE_TRUST_TOKEN,
    THEME_FILE_MODE,
    ThemeLifecycleError,
    _absolute_root,
    _assert_open_directory_binding,
    _lifecycle_lock,
    _open_package_directory,
    _open_package_directory_relative,
    _validated_open_theme_source_snapshot,
)

HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME: Final = ".home-assistant-activations.json"
HOME_ASSISTANT_ACTIVATION_LEDGER_SCHEMA_VERSION: Final = 1
HOME_ASSISTANT_MODULE_MODE: Final = 0o644

ActivationState: TypeAlias = Literal[
    "current",
    "stale-package",
    "changed-target",
    "missing-target",
    "invalid-ledger",
]

_LEDGER_FIELDS: Final = frozenset({"schema_version", "activations"})
_RECORD_FIELDS: Final = frozenset(
    {
        "interface",
        "id",
        "package_sha256",
        "module_sha256",
        "installed_filename",
        "custom_element",
        "resource_url",
        "target_directory",
    }
)
_SHA256_LENGTH: Final = 64
_IDENTIFIER_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_JAVASCRIPT_FILENAME_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.js\Z")
_STAGE_PREFIX: Final = ".sdsctl-ha-stage-"
_ROLLBACK_PREFIX: Final = ".sdsctl-ha-rollback-"


@dataclass(frozen=True, slots=True)
class HomeAssistantActivationRecord:
    interface: str
    identifier: str
    package_sha256: str
    module_sha256: str
    installed_filename: str
    custom_element: str
    resource_url: str
    target_directory: Path

    @property
    def identity(self) -> str:
        return f"{self.interface}/{self.identifier}"

    @property
    def target_path(self) -> Path:
        return self.target_directory / self.installed_filename

    def as_dict(self) -> dict[str, object]:
        return {
            "interface": self.interface,
            "id": self.identifier,
            "identity": self.identity,
            "package_sha256": self.package_sha256,
            "module_sha256": self.module_sha256,
            "installed_filename": self.installed_filename,
            "custom_element": self.custom_element,
            "resource_url": self.resource_url,
            "target_directory": str(self.target_directory),
            "target_path": str(self.target_path),
        }

    def ledger_dict(self) -> dict[str, object]:
        payload = self.as_dict()
        del payload["identity"]
        del payload["target_path"]
        return payload


@dataclass(frozen=True, slots=True)
class HomeAssistantActivationStatus:
    state: ActivationState
    record: HomeAssistantActivationRecord | None
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "message": self.message,
            "activation": self.record.as_dict() if self.record is not None else None,
        }


@dataclass(frozen=True, slots=True)
class HomeAssistantActivationInventory:
    root: Path
    ledger: Path
    statuses: tuple[HomeAssistantActivationStatus, ...]

    @property
    def valid(self) -> bool:
        return all(status.state != "invalid-ledger" for status in self.statuses)

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "ledger": str(self.ledger),
            "valid": self.valid,
            "activations": [status.as_dict() for status in self.statuses],
        }


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _file_flags() -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _read_regular_file_snapshot(
    directory: int,
    name: str,
    *,
    label: str,
) -> tuple[bytes, os.stat_result]:
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=directory)
    except OSError as exc:
        raise ThemeLifecycleError(f"{label} cannot be securely opened") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ThemeLifecycleError(f"{label} must be a regular file")
        content = bytearray()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            content.extend(chunk)
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
            raise ThemeLifecycleError(f"{label} changed while it was being read")
        return bytes(content), after
    finally:
        os.close(descriptor)


def _read_regular_file(directory: int, name: str, *, label: str) -> bytes:
    content, _status = _read_regular_file_snapshot(directory, name, label=label)
    return content


def _secure_package(
    root: Path,
    identifier: str,
) -> tuple[HomeAssistantThemeManifest, str, bytes, str]:
    package_path = root / "home-assistant" / identifier
    interface_path = root / "home-assistant"
    with _open_package_directory(root) as root_directory:
        with _open_package_directory_relative(
            root_directory,
            "home-assistant",
            interface_path,
        ) as interface_directory:
            with _open_package_directory_relative(
                interface_directory,
                identifier,
                package_path,
            ) as package_directory:
                with _validated_open_theme_source_snapshot(package_directory) as snapshot:
                    validated = snapshot.package
                    if validated.summary.interface != "home-assistant":
                        raise ThemeLifecycleError(
                            "activation requires a Home Assistant theme package"
                        )
                    if not isinstance(validated.manifest, HomeAssistantThemeManifest):
                        raise ThemeLifecycleError("managed package has the wrong manifest type")
                    manifest = validated.manifest
                    digest = snapshot.image.sha256
                    if digest != validated.summary.sha256:
                        raise ThemeLifecycleError(
                            "managed Home Assistant snapshot digest is inconsistent"
                        )
                    module = snapshot.image.require_file(manifest.module)
                    result = (
                        manifest,
                        digest,
                        module,
                        hashlib.sha256(module).hexdigest(),
                    )
                _assert_open_directory_binding(package_directory)
            _assert_open_directory_binding(interface_directory)
        _assert_open_directory_binding(root_directory)
    return result


def _require_sha256(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ThemeLifecycleError(f"activation ledger {field} must be lowercase SHA-256")
    return value


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ThemeLifecycleError(f"activation ledger {field} must be nonblank text")
    return value


def _require_identifier(identifier: object) -> str:
    if not isinstance(identifier, str) or _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise ThemeLifecycleError("Home Assistant theme identity must be lowercase kebab-case")
    return identifier


def _parse_record(value: object) -> HomeAssistantActivationRecord:
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise ThemeLifecycleError("activation ledger record fields do not match schema version 1")
    if value["interface"] != "home-assistant":
        raise ThemeLifecycleError("activation ledger contains an unsupported interface")
    identifier = _require_text(value["id"], field="id")
    if _IDENTIFIER_PATTERN.fullmatch(identifier) is None:
        raise ThemeLifecycleError("activation ledger id must be lowercase kebab-case")
    target_text = _require_text(value["target_directory"], field="target_directory")
    target = Path(target_text)
    normalized_target = Path(os.path.abspath(target_text))
    if (
        not target.is_absolute()
        or target != normalized_target
        or target.name != "sds200"
        or target.parent.name != "www"
    ):
        raise ThemeLifecycleError(
            "activation ledger target directory must be an absolute www/sds200 path"
        )
    filename = _require_text(value["installed_filename"], field="installed_filename")
    resource_url = _require_text(value["resource_url"], field="resource_url")
    if (
        _JAVASCRIPT_FILENAME_PATTERN.fullmatch(filename) is None
        or resource_url != f"/local/sds200/{filename}"
    ):
        raise ThemeLifecycleError("activation ledger module identity is invalid")
    custom_element = _require_text(value["custom_element"], field="custom_element")
    if _IDENTIFIER_PATTERN.fullmatch(custom_element) is None:
        raise ThemeLifecycleError("activation ledger custom_element must be lowercase kebab-case")
    return HomeAssistantActivationRecord(
        interface="home-assistant",
        identifier=identifier,
        package_sha256=_require_sha256(value["package_sha256"], field="package_sha256"),
        module_sha256=_require_sha256(value["module_sha256"], field="module_sha256"),
        installed_filename=filename,
        custom_element=custom_element,
        resource_url=resource_url,
        target_directory=target,
    )


def _validate_records(
    records: tuple[HomeAssistantActivationRecord, ...],
) -> tuple[HomeAssistantActivationRecord, ...]:
    keys = [(str(record.target_directory), record.identifier) for record in records]
    targets = [(str(record.target_directory), record.installed_filename) for record in records]
    custom_elements = [(str(record.target_directory), record.custom_element) for record in records]
    resource_urls = [(str(record.target_directory), record.resource_url) for record in records]
    for values, label in (
        (keys, "activation identities"),
        (targets, "target filenames"),
        (custom_elements, "custom elements"),
        (resource_urls, "resource URLs"),
    ):
        if len(set(values)) != len(values):
            raise ThemeLifecycleError(f"activation ledger contains duplicate {label}")
    return tuple(
        sorted(
            records,
            key=lambda record: (str(record.target_directory), record.identifier),
        )
    )


def _read_ledger(
    root: Path,
    *,
    root_descriptor: int | None = None,
) -> tuple[HomeAssistantActivationRecord, ...]:
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    try:
        ledger_status = (
            ledger.lstat()
            if root_descriptor is None
            else os.stat(
                HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        )
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise ThemeLifecycleError("Home Assistant activation ledger is not accessible") from exc
    if not stat.S_ISREG(ledger_status.st_mode):
        raise ThemeLifecycleError("Home Assistant activation ledger must be a regular file")
    if stat.S_IMODE(ledger_status.st_mode) & 0o077:
        raise ThemeLifecycleError("Home Assistant activation ledger must have private permissions")
    opened_root_descriptor: int | None = None
    if root_descriptor is None:
        try:
            opened_root_descriptor = os.open(root, _directory_flags())
        except OSError as exc:
            raise ThemeLifecycleError("managed theme root cannot be securely opened") from exc
        root_descriptor = opened_root_descriptor
    try:
        content, opened_status = _read_regular_file_snapshot(
            root_descriptor,
            HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME,
            label="Home Assistant activation ledger",
        )
    finally:
        if opened_root_descriptor is not None:
            os.close(opened_root_descriptor)
    if (opened_status.st_dev, opened_status.st_ino) != (
        ledger_status.st_dev,
        ledger_status.st_ino,
    ):
        raise ThemeLifecycleError("Home Assistant activation ledger identity changed")
    if stat.S_IMODE(opened_status.st_mode) & 0o077:
        raise ThemeLifecycleError("Home Assistant activation ledger must have private permissions")
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ThemeLifecycleError("Home Assistant activation ledger is invalid JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != _LEDGER_FIELDS:
        raise ThemeLifecycleError("activation ledger fields do not match schema version 1")
    if parsed["schema_version"] != HOME_ASSISTANT_ACTIVATION_LEDGER_SCHEMA_VERSION:
        raise ThemeLifecycleError("unsupported Home Assistant activation ledger schema")
    values = parsed["activations"]
    if not isinstance(values, list):
        raise ThemeLifecycleError("activation ledger activations must be a list")
    return _validate_records(tuple(_parse_record(value) for value in values))


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise ThemeLifecycleError("Home Assistant activation file write failed")
        offset += written


def _write_ledger(root: Path, records: tuple[HomeAssistantActivationRecord, ...]) -> None:
    records = _validate_records(records)
    document = {
        "schema_version": HOME_ASSISTANT_ACTIVATION_LEDGER_SCHEMA_VERSION,
        "activations": [record.ledger_dict() for record in records],
    }
    content = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary_name = f".home-assistant-activations.{secrets.token_hex(8)}.tmp"
    root_descriptor = os.open(root, _directory_flags())
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary_name, flags, THEME_FILE_MODE, dir_fd=root_descriptor)
        _write_all(descriptor, content)
        os.fchmod(descriptor, THEME_FILE_MODE)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            existing_status = os.stat(
                HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing_status = None
        if existing_status is not None and not stat.S_ISREG(existing_status.st_mode):
            raise ThemeLifecycleError("Home Assistant activation ledger must remain a regular file")
        os.replace(
            temporary_name,
            HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME,
            src_dir_fd=root_descriptor,
            dst_dir_fd=root_descriptor,
        )
        os.fsync(root_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=root_descriptor)
        os.close(root_descriptor)


def _validate_target_directory(target: Path) -> Path:
    if not isinstance(target, Path):
        raise TypeError("Home Assistant target directory must be a pathlib.Path")
    if not target.is_absolute():
        raise ThemeLifecycleError("Home Assistant target directory must be absolute")
    candidate = Path(os.path.abspath(os.fspath(target.expanduser())))
    if candidate.name != "sds200" or candidate.parent.name != "www":
        raise ThemeLifecycleError(
            "Home Assistant target directory must be an absolute www/sds200 path"
        )
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            status = current.lstat()
        except FileNotFoundError as exc:
            raise ThemeLifecycleError(
                "Home Assistant target directory and all parents must already exist"
            ) from exc
        except OSError as exc:
            raise ThemeLifecycleError("Home Assistant target directory is not accessible") from exc
        if stat.S_ISLNK(status.st_mode):
            raise ThemeLifecycleError(
                f"Home Assistant target directory refuses symlink component: {current}"
            )
        if not stat.S_ISDIR(status.st_mode):
            raise ThemeLifecycleError(
                f"Home Assistant target directory component is not a directory: {current}"
            )
    return candidate


def _open_target_directory(target: Path) -> int:
    descriptor: int | None = None
    try:
        descriptor = os.open(target.anchor, _directory_flags())
        for part in target.parts[1:]:
            next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ThemeLifecycleError(
            "Home Assistant target directory cannot be securely opened"
        ) from exc


def _target_content(directory: int, filename: str) -> bytes | None:
    try:
        return _read_regular_file(
            directory,
            filename,
            label="Home Assistant deployed module",
        )
    except ThemeLifecycleError as exc:
        try:
            os.stat(filename, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        raise exc


def _stage_target(directory: int, filename: str, content: bytes) -> str:
    name = f"{_STAGE_PREFIX}{filename}-{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, HOME_ASSISTANT_MODULE_MODE, dir_fd=directory)
    try:
        _write_all(descriptor, content)
        os.fchmod(descriptor, HOME_ASSISTANT_MODULE_MODE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return name


def _replace_record(
    records: tuple[HomeAssistantActivationRecord, ...],
    record: HomeAssistantActivationRecord,
) -> tuple[HomeAssistantActivationRecord, ...]:
    kept = tuple(
        existing
        for existing in records
        if not (
            existing.identifier == record.identifier
            and existing.target_directory == record.target_directory
        )
    )
    return _validate_records((*kept, record))


def activate_home_assistant_theme(
    root: Path,
    identifier: str,
    target_directory: Path,
    *,
    confirmed_sha256: str,
    home_assistant_code_trust: str | None,
) -> HomeAssistantActivationRecord:
    """Explicitly approve and atomically deploy one managed JavaScript module."""

    managed_root = _absolute_root(root)
    identifier = _require_identifier(identifier)
    if identifier in BUILT_IN_HOME_ASSISTANT_THEME_IDS:
        raise ThemeLifecycleError(
            "built-in Home Assistant themes are owned by the Home Assistant App installer"
        )
    if home_assistant_code_trust != HOME_ASSISTANT_CODE_TRUST_TOKEN:
        raise ThemeLifecycleError(
            "Home Assistant theme activation requires the exact executable-code trust token: "
            f"{HOME_ASSISTANT_CODE_TRUST_TOKEN}"
        )
    target = _validate_target_directory(target_directory)

    with _lifecycle_lock(managed_root):
        manifest, package_sha256, module, module_sha256 = _secure_package(managed_root, identifier)
        if confirmed_sha256 != package_sha256:
            raise ThemeLifecycleError(
                "activation confirmation must exactly match the current package SHA-256: "
                f"{package_sha256}"
            )
        records = _read_ledger(managed_root)
        prior = next(
            (
                record
                for record in records
                if record.identifier == identifier and record.target_directory == target
            ),
            None,
        )
        record = HomeAssistantActivationRecord(
            interface="home-assistant",
            identifier=identifier,
            package_sha256=package_sha256,
            module_sha256=module_sha256,
            installed_filename=manifest.installed_filename,
            custom_element=manifest.custom_element,
            resource_url=manifest.resource_url,
            target_directory=target,
        )
        updated_records = _replace_record(records, record)
        target_descriptor = _open_target_directory(target)
        stage: str | None = None
        rollback: str | None = None
        published = False
        committed = False
        current: bytes | None = None
        try:
            current = _target_content(target_descriptor, manifest.installed_filename)
            if prior is None:
                if current is not None and hashlib.sha256(current).hexdigest() != module_sha256:
                    raise ThemeLifecycleError(
                        "first activation refuses to overwrite an unrelated existing target"
                    )
            else:
                if prior.installed_filename != manifest.installed_filename:
                    raise ThemeLifecycleError(
                        "reapproval cannot change the ledger-pinned installed filename"
                    )
                if current is None or hashlib.sha256(current).hexdigest() != prior.module_sha256:
                    raise ThemeLifecycleError(
                        "deployed module changed after prior activation; refusing replacement"
                    )

            if current != module:
                stage = _stage_target(target_descriptor, manifest.installed_filename, module)
                if current is not None:
                    rollback = (
                        f"{_ROLLBACK_PREFIX}{manifest.installed_filename}-{secrets.token_hex(8)}"
                    )
                    os.rename(
                        manifest.installed_filename,
                        rollback,
                        src_dir_fd=target_descriptor,
                        dst_dir_fd=target_descriptor,
                    )
                    rollback_content = _target_content(target_descriptor, rollback)
                    if rollback_content != current:
                        os.rename(
                            rollback,
                            manifest.installed_filename,
                            src_dir_fd=target_descriptor,
                            dst_dir_fd=target_descriptor,
                        )
                        rollback = None
                        raise ThemeLifecycleError(
                            "deployed module changed during atomic replacement"
                        )
                os.link(
                    stage,
                    manifest.installed_filename,
                    src_dir_fd=target_descriptor,
                    dst_dir_fd=target_descriptor,
                    follow_symlinks=False,
                )
                os.unlink(stage, dir_fd=target_descriptor)
                stage = None
                published = True
                os.fsync(target_descriptor)
                verified = _target_content(target_descriptor, manifest.installed_filename)
                if verified != module or hashlib.sha256(verified).hexdigest() != module_sha256:
                    raise ThemeLifecycleError(
                        "Home Assistant module post-write verification failed"
                    )
            else:
                os.chmod(
                    manifest.installed_filename,
                    HOME_ASSISTANT_MODULE_MODE,
                    dir_fd=target_descriptor,
                    follow_symlinks=False,
                )
                if _target_content(target_descriptor, manifest.installed_filename) != module:
                    raise ThemeLifecycleError(
                        "Home Assistant module changed during idempotent activation"
                    )
            _write_ledger(managed_root, updated_records)
            committed = True
            if rollback is not None:
                with suppress(OSError):
                    os.unlink(rollback, dir_fd=target_descriptor)
                rollback = None
                with suppress(OSError):
                    os.fsync(target_descriptor)
        except BaseException:
            if published and not committed:
                with suppress(FileNotFoundError):
                    os.unlink(manifest.installed_filename, dir_fd=target_descriptor)
            if rollback is not None and not committed:
                os.rename(
                    rollback,
                    manifest.installed_filename,
                    src_dir_fd=target_descriptor,
                    dst_dir_fd=target_descriptor,
                )
                rollback = None
            if not committed and (published or current is not None):
                os.fsync(target_descriptor)
            raise
        finally:
            if stage is not None:
                with suppress(FileNotFoundError):
                    os.unlink(stage, dir_fd=target_descriptor)
            if rollback is not None:
                with suppress(FileNotFoundError):
                    os.unlink(rollback, dir_fd=target_descriptor)
            os.close(target_descriptor)
        return record


def deactivate_home_assistant_theme(
    root: Path,
    identifier: str,
    target_directory: Path,
    *,
    confirmation: str,
) -> HomeAssistantActivationRecord:
    """Remove only one exact ledger-pinned deployed module."""

    managed_root = _absolute_root(root)
    identifier = _require_identifier(identifier)
    if confirmation != f"home-assistant/{identifier}":
        raise ThemeLifecycleError(
            f"deactivation confirmation must exactly match: home-assistant/{identifier}"
        )
    target = _validate_target_directory(target_directory)
    with _lifecycle_lock(managed_root):
        records = _read_ledger(managed_root)
        record = next(
            (
                item
                for item in records
                if item.identifier == identifier and item.target_directory == target
            ),
            None,
        )
        if record is None:
            raise ThemeLifecycleError("managed Home Assistant theme is not active at that target")
        target_descriptor = _open_target_directory(target)
        tombstone = f"{_ROLLBACK_PREFIX}{record.installed_filename}-{secrets.token_hex(8)}"
        moved = False
        committed = False
        try:
            current = _target_content(target_descriptor, record.installed_filename)
            if current is None:
                raise ThemeLifecycleError("deployed Home Assistant module is missing")
            if hashlib.sha256(current).hexdigest() != record.module_sha256:
                raise ThemeLifecycleError(
                    "deployed Home Assistant module changed; refusing uncertain deletion"
                )
            os.rename(
                record.installed_filename,
                tombstone,
                src_dir_fd=target_descriptor,
                dst_dir_fd=target_descriptor,
            )
            moved = True
            tombstone_content = _target_content(target_descriptor, tombstone)
            if tombstone_content != current:
                os.rename(
                    tombstone,
                    record.installed_filename,
                    src_dir_fd=target_descriptor,
                    dst_dir_fd=target_descriptor,
                )
                moved = False
                raise ThemeLifecycleError(
                    "deployed Home Assistant module changed during deactivation"
                )
            os.fsync(target_descriptor)
            remaining = tuple(item for item in records if item is not record)
            _write_ledger(managed_root, remaining)
            committed = True
            with suppress(OSError):
                os.unlink(tombstone, dir_fd=target_descriptor)
            moved = False
            with suppress(OSError):
                os.fsync(target_descriptor)
        except BaseException:
            if moved and not committed:
                os.rename(
                    tombstone,
                    record.installed_filename,
                    src_dir_fd=target_descriptor,
                    dst_dir_fd=target_descriptor,
                )
                os.fsync(target_descriptor)
            raise
        finally:
            os.close(target_descriptor)
        return record


def _record_status(
    root: Path,
    record: HomeAssistantActivationRecord,
) -> HomeAssistantActivationStatus:
    try:
        record.target_directory.lstat()
    except FileNotFoundError:
        return HomeAssistantActivationStatus(
            "missing-target", record, "Home Assistant target directory is missing"
        )
    except OSError as exc:
        return HomeAssistantActivationStatus("changed-target", record, str(exc))
    try:
        target = _validate_target_directory(record.target_directory)
        descriptor = _open_target_directory(target)
        try:
            content = _target_content(descriptor, record.installed_filename)
        finally:
            os.close(descriptor)
    except (OSError, ThemeLifecycleError) as exc:
        return HomeAssistantActivationStatus("changed-target", record, str(exc))
    if content is None:
        return HomeAssistantActivationStatus("missing-target", record, "deployed module is missing")
    if hashlib.sha256(content).hexdigest() != record.module_sha256:
        return HomeAssistantActivationStatus(
            "changed-target", record, "deployed module no longer matches the approved digest"
        )
    try:
        manifest, package_sha256, module, module_sha256 = _secure_package(root, record.identifier)
        if (
            package_sha256 != record.package_sha256
            or module_sha256 != record.module_sha256
            or manifest.installed_filename != record.installed_filename
            or manifest.custom_element != record.custom_element
            or manifest.resource_url != record.resource_url
            or hashlib.sha256(module).hexdigest() != record.module_sha256
        ):
            raise ThemeLifecycleError("managed package no longer matches its approved record")
    except (OSError, ThemeLifecycleError) as exc:
        return HomeAssistantActivationStatus("stale-package", record, str(exc))
    return HomeAssistantActivationStatus(
        "current", record, "managed package and deployed module match the approved digests"
    )


def home_assistant_activation_inventory(root: Path) -> HomeAssistantActivationInventory:
    """Report activation state without evaluating JavaScript or contacting Home Assistant."""

    managed_root = _absolute_root(root)
    ledger = managed_root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    with _lifecycle_lock(managed_root):
        try:
            records = _read_ledger(managed_root)
        except ThemeLifecycleError as exc:
            return HomeAssistantActivationInventory(
                root=managed_root,
                ledger=ledger,
                statuses=(HomeAssistantActivationStatus("invalid-ledger", None, str(exc)),),
            )
        statuses = tuple(_record_status(managed_root, record) for record in records)
    return HomeAssistantActivationInventory(managed_root, ledger, statuses)


def ensure_home_assistant_theme_inactive(
    root: Path,
    identifier: str,
    *,
    root_descriptor: int | None = None,
) -> None:
    """Reject removal when any strict ledger record pins the managed identity.

    The caller must already hold the managed lifecycle lock.
    """

    identifier = _require_identifier(identifier)
    records = _read_ledger(root, root_descriptor=root_descriptor)
    if any(record.identifier == identifier for record in records):
        raise ThemeLifecycleError(
            "managed Home Assistant theme is active; deactivate every target before removal"
        )


__all__ = [
    "HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME",
    "HOME_ASSISTANT_ACTIVATION_LEDGER_SCHEMA_VERSION",
    "HOME_ASSISTANT_MODULE_MODE",
    "HomeAssistantActivationInventory",
    "HomeAssistantActivationRecord",
    "HomeAssistantActivationStatus",
    "activate_home_assistant_theme",
    "deactivate_home_assistant_theme",
    "ensure_home_assistant_theme_inactive",
    "home_assistant_activation_inventory",
]
