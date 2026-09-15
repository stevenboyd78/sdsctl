"""Explicit local-file imports with durable, private last-accepted state.

Internal Linux/POSIX foundation, not a public CLI/upload route. The operator
selects one source file and an independent private state directory. Source bytes
are read-only; one atomic state document holds their accepted copy and provenance.
No automatic path discovery, file watching, scanner access or permission repair.
Directory locks serialize cooperating writers, not arbitrary same-account edits.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .scanner_display_profile import MAX_PROFILE_BYTES, parse_scanner_display_profile
from .scanner_display_profile_state import (
    DisplayProfileBinding,
    DisplayProfileImport,
    DisplayProfilePreview,
    DisplayProfileProvenance,
    DisplayProfileSnapshot,
    DisplayProfileSourceKind,
    ScannerDisplayProfileStore,
)

_STATE_FILE = "accepted-profile.json"
_STATE_LIMIT = 2 * MAX_PROFILE_BYTES + 4096


class ProfileStorageFailure(StrEnum):
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    UNSAFE_PATH = "unsafe_path"
    STATE_UNAVAILABLE = "state_unavailable"
    STATE_INVALID = "state_invalid"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_CHANGED = "source_changed"
    INVALID_PROFILE = "invalid_profile"
    BUSY = "busy"
    CONFLICT = "conflict"
    INVALID_REVIEW = "invalid_review"
    WRITE_FAILED = "write_failed"
    OUTCOME_UNCONFIRMED = "outcome_unconfirmed"


class DisplayProfileStorageError(RuntimeError):
    """Only a fixed category, never raw file text, paths or OS exception details."""

    def __init__(self, category: ProfileStorageFailure) -> None:
        self.category = category
        super().__init__(f"Scanner display profile storage: {category.value}.")


class DisplayProfileSourceStatus(StrEnum):
    NOT_IMPORTED = "not_imported"
    MATCHES_IMPORT = "matches_import"
    CHANGED = "changed_since_import"
    INVALID = "invalid_source"
    UNAVAILABLE = "source_unavailable"
    UNSAFE = "unsafe_source"


@dataclass(frozen=True, slots=True)
class DiskDisplayProfileSnapshot:
    """Safe display projection; source-copy equality is not scanner freshness."""

    profile: DisplayProfileSnapshot
    source_status: DisplayProfileSourceStatus


@dataclass(frozen=True, slots=True, repr=False)
class _File:
    data: bytes
    identity: tuple[int, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _State:
    generation: UUID
    source: bytes | None
    accepted: DisplayProfileImport | None
    file: _File


@dataclass(frozen=True, slots=True, repr=False)
class _Pending:
    store: ScannerDisplayProfileStore
    preview: DisplayProfilePreview
    source: _File
    state: _File


def _error(category: ProfileStorageFailure) -> DisplayProfileStorageError:
    return DisplayProfileStorageError(category)


def _path(value: Path) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or not value.name
        or ".." in value.parts
    ):
        raise _error(ProfileStorageFailure.UNSAFE_PATH)
    return value


def _platform() -> None:
    if os.name != "posix" or not all(
        hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK")
    ):
        raise _error(ProfileStorageFailure.UNSUPPORTED_PLATFORM)


def _directory(path: Path) -> int:
    """Open every component relative to a pinned directory; never follow links."""
    _platform()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _identity(observed: os.stat_result) -> tuple[int, ...]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
        observed.st_mode,
        observed.st_uid,
        observed.st_gid,
        observed.st_nlink,
    )


def _private_directory(descriptor: int) -> None:
    observed = os.fstat(descriptor)
    if observed.st_uid != os.geteuid() or stat.S_IMODE(observed.st_mode) != 0o700:
        raise _error(ProfileStorageFailure.UNSAFE_PATH)


def _same_directory(path: Path, descriptor: int) -> None:
    check = _directory(path)
    try:
        a, b = os.fstat(descriptor), os.fstat(check)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise _error(ProfileStorageFailure.UNSAFE_PATH)
    finally:
        os.close(check)


@contextmanager
def _access(root: Path, *, exclusive: bool) -> Iterator[int]:
    _platform()
    import fcntl

    descriptor = None
    try:
        descriptor = _directory(root)
        _private_directory(descriptor)
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _error(ProfileStorageFailure.BUSY) from None
        yield descriptor
        _private_directory(descriptor)
        _same_directory(root, descriptor)
    except OSError:
        raise _error(ProfileStorageFailure.STATE_UNAVAILABLE) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_file(directory: int, name: str, limit: int, *, private: bool) -> _File:
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise _error(ProfileStorageFailure.UNSAFE_PATH)
        if private and (before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600):
            raise _error(ProfileStorageFailure.UNSAFE_PATH)
        if before.st_size <= 0 or before.st_size > limit:
            raise _error(ProfileStorageFailure.INVALID_PROFILE)
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            _identity(before) != _identity(after)
            or _identity(after) != _identity(named)
            or len(data) != after.st_size
            or len(data) > limit
        ):
            raise _error(ProfileStorageFailure.SOURCE_CHANGED)
        return _File(data, _identity(after))
    finally:
        os.close(descriptor)


def _source(path: Path) -> _File:
    directory = None
    try:
        directory = _directory(path.parent)
        result = _read_file(directory, path.name, MAX_PROFILE_BYTES, private=False)
        _same_directory(path.parent, directory)
        return result
    except OSError as exc:
        category = (
            ProfileStorageFailure.UNSAFE_PATH
            if exc.errno in (errno.ELOOP, errno.ENOTDIR)
            else ProfileStorageFailure.SOURCE_UNAVAILABLE
        )
        raise _error(category) from None
    finally:
        if directory is not None:
            os.close(directory)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError()
    result = UUID(value)
    if str(result) != value:
        raise ValueError()
    return result


def _decode(file: _File, endpoint: UUID) -> _State:
    try:
        doc = json.loads(file.data, object_pairs_hook=_unique_object)
        if (
            not isinstance(doc, dict)
            or set(doc) != {"schema", "endpoint_id", "generation", "accepted"}
            or type(doc["schema"]) is not int
            or doc["schema"] != 1
            or _uuid(doc["endpoint_id"]) != endpoint
        ):
            raise ValueError()
        generation = _uuid(doc["generation"])
        record = doc["accepted"]
        if record is None:
            return _State(generation, None, None, file)
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "source_id",
                "source_kind",
                "acquired_at",
                "imported_at",
                "raw_sha256",
                "revision",
                "raw",
            }
            or not all(isinstance(value, str) for value in record.values())
        ):
            raise ValueError()
        if record["source_kind"] != DisplayProfileSourceKind.MANUAL_IMPORT.value:
            raise ValueError()
        data = base64.b64decode(record["raw"], validate=True)
        if base64.b64encode(data).decode("ascii") != record["raw"]:
            raise ValueError()
        profile = parse_scanner_display_profile(data)
        if (
            hashlib.sha256(data).hexdigest() != record["raw_sha256"]
            or profile.revision != record["revision"]
        ):
            raise ValueError()
        binding = DisplayProfileBinding(
            endpoint, _uuid(record["source_id"]), DisplayProfileSourceKind.MANUAL_IMPORT
        )
        provenance = DisplayProfileProvenance(
            binding,
            datetime.fromisoformat(record["acquired_at"]),
            datetime.fromisoformat(record["imported_at"]),
        )
        return _State(generation, data, DisplayProfileImport(profile, provenance), file)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise _error(ProfileStorageFailure.STATE_INVALID) from None


def _read_state(directory: int, endpoint: UUID) -> _State:
    try:
        return _decode(_read_file(directory, _STATE_FILE, _STATE_LIMIT, private=True), endpoint)
    except DisplayProfileStorageError as exc:
        if exc.category is ProfileStorageFailure.INVALID_PROFILE:
            raise _error(ProfileStorageFailure.STATE_INVALID) from None
        if exc.category is ProfileStorageFailure.SOURCE_CHANGED:
            raise _error(ProfileStorageFailure.CONFLICT) from None
        raise
    except OSError:
        raise _error(ProfileStorageFailure.STATE_UNAVAILABLE) from None


def _encode(endpoint: UUID, accepted: DisplayProfileImport | None, source: bytes | None) -> bytes:
    record = None
    if accepted is not None and source is not None:
        provenance = accepted.provenance
        record = {
            "source_id": str(provenance.binding.source_id),
            "source_kind": provenance.binding.source_kind.value,
            "acquired_at": provenance.acquired_at.isoformat(),
            "imported_at": provenance.imported_at.isoformat(),
            "raw_sha256": hashlib.sha256(source).hexdigest(),
            "revision": accepted.profile.revision,
            "raw": base64.b64encode(source).decode("ascii"),
        }
    return json.dumps(
        {"schema": 1, "endpoint_id": str(endpoint), "generation": str(uuid4()), "accepted": record},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _publish(directory: int, content: bytes, *, expected: _File | None) -> None:
    """Atomic single-document commit; post-rename errors are explicitly uncertain."""
    temporary = f".profile-{uuid4().hex}.tmp"
    descriptor = None
    renamed = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError()
            offset += written
        os.fsync(descriptor)
        if _read_file(directory, temporary, _STATE_LIMIT, private=True).data != content:
            raise OSError()
        try:
            current = _read_file(directory, _STATE_FILE, _STATE_LIMIT, private=True)
        except FileNotFoundError:
            current = None
        if current != expected:
            raise _error(ProfileStorageFailure.CONFLICT)
        os.replace(temporary, _STATE_FILE, src_dir_fd=directory, dst_dir_fd=directory)
        renamed = True
        os.fsync(directory)
        if _read_file(directory, _STATE_FILE, _STATE_LIMIT, private=True).data != content:
            raise OSError()
    except DisplayProfileStorageError:
        if renamed:
            raise _error(ProfileStorageFailure.OUTCOME_UNCONFIRMED) from None
        raise
    except OSError:
        if renamed:
            raise _error(ProfileStorageFailure.OUTCOME_UNCONFIRMED) from None
        raise _error(ProfileStorageFailure.WRITE_FAILED) from None
    finally:
        if descriptor is not None:
            identity = _identity(os.fstat(descriptor))[:2]
            os.close(descriptor)
            if not renamed:
                with suppress(OSError):
                    if (
                        _identity(os.stat(temporary, dir_fd=directory, follow_symlinks=False))[:2]
                        == identity
                    ):
                        os.unlink(temporary, dir_fd=directory)


def initialize_display_profile_storage(root: Path, endpoint_id: UUID) -> None:
    """Create exactly one new private directory. Never initialize/repair an existing one."""
    root = _path(root)
    if not isinstance(endpoint_id, UUID):
        raise _error(ProfileStorageFailure.INVALID_REVIEW)
    parent = None
    try:
        parent = _directory(root.parent)
        os.mkdir(root.name, 0o700, dir_fd=parent)
        os.fsync(parent)
        with _access(root, exclusive=True) as directory:
            _publish(directory, _encode(endpoint_id, None, None), expected=None)
    except OSError:
        raise _error(ProfileStorageFailure.WRITE_FAILED) from None
    finally:
        if parent is not None:
            os.close(parent)


def _store(endpoint: UUID, state: _State) -> ScannerDisplayProfileStore:
    store = ScannerDisplayProfileStore(endpoint)
    if state.accepted is not None and state.source is not None:
        provenance = state.accepted.provenance
        ticket = store.begin_refresh(provenance.binding, started_at=provenance.acquired_at)
        preview = store.prepare(ticket, state.source, acquired_at=provenance.acquired_at)
        store.commit(preview, imported_at=provenance.imported_at)
    return store


class PersistentScannerDisplayProfile:
    """Explicit prepare/review/commit for one local source and one endpoint.

    Callers supply trusted paths/identity and enforce administrator authority.
    Source files are never written. A preview is one-use and object-bound; other
    writers or changed source bytes invalidate it. inspect() never adopts edits.
    """

    def __init__(self, *, source_path: Path, state_directory: Path, endpoint_id: UUID) -> None:
        self._source_path, self._root = _path(source_path), _path(state_directory)
        if not isinstance(endpoint_id, UUID) or self._source_path.is_relative_to(self._root):
            raise _error(ProfileStorageFailure.UNSAFE_PATH)
        self._endpoint = endpoint_id
        self._lock = threading.Lock()
        self._pending: _Pending | None = None

    def inspect(self) -> DiskDisplayProfileSnapshot:
        """Read/revalidate durable last-good state and report source-copy status."""
        with self._lock, _access(self._root, exclusive=False) as directory:
            state = _read_state(directory, self._endpoint)
            status = DisplayProfileSourceStatus.NOT_IMPORTED
            if state.accepted is not None:
                try:
                    source = _source(self._source_path)
                    parse_scanner_display_profile(source.data)
                    status = (
                        DisplayProfileSourceStatus.MATCHES_IMPORT
                        if source.data == state.source
                        else DisplayProfileSourceStatus.CHANGED
                    )
                except DisplayProfileStorageError as exc:
                    status = {
                        ProfileStorageFailure.INVALID_PROFILE: DisplayProfileSourceStatus.INVALID,
                        ProfileStorageFailure.UNSAFE_PATH: DisplayProfileSourceStatus.UNSAFE,
                        ProfileStorageFailure.SOURCE_CHANGED: DisplayProfileSourceStatus.CHANGED,
                    }.get(exc.category, DisplayProfileSourceStatus.UNAVAILABLE)
                except (ValueError, TypeError):
                    status = DisplayProfileSourceStatus.INVALID
            return DiskDisplayProfileSnapshot(
                _store(self._endpoint, state).snapshot(self._endpoint), status
            )

    def prepare(
        self, binding: DisplayProfileBinding, *, acquired_at: datetime
    ) -> DisplayProfilePreview:
        with self._lock:
            self._pending = None
            if (
                not isinstance(binding, DisplayProfileBinding)
                or binding.endpoint_id != self._endpoint
                or binding.source_kind is not DisplayProfileSourceKind.MANUAL_IMPORT
            ):
                raise _error(ProfileStorageFailure.INVALID_REVIEW)
            with _access(self._root, exclusive=False) as directory:
                state = _read_state(directory, self._endpoint)
                source = _source(self._source_path)
                store = _store(self._endpoint, state)
                try:
                    ticket = store.begin_refresh(binding, started_at=acquired_at)
                except (ValueError, TypeError):
                    raise _error(ProfileStorageFailure.INVALID_REVIEW) from None
                try:
                    preview = store.prepare(ticket, source.data, acquired_at=acquired_at)
                except (ValueError, TypeError):
                    raise _error(ProfileStorageFailure.INVALID_PROFILE) from None
            # Retain no usable review if the final directory identity check failed.
            self._pending = _Pending(store, preview, source, state.file)
            return preview

    def cancel(self, preview: DisplayProfilePreview) -> None:
        with self._lock:
            if self._pending is None or self._pending.preview is not preview:
                raise _error(ProfileStorageFailure.INVALID_REVIEW)
            self._pending = None

    def commit(
        self,
        preview: DisplayProfilePreview,
        *,
        imported_at: datetime,
        confirm_source_change: bool = False,
    ) -> DisplayProfileImport:
        with self._lock:
            pending = self._pending
            if pending is None or pending.preview is not preview:
                raise _error(ProfileStorageFailure.INVALID_REVIEW)
            published = False
            try:
                with _access(self._root, exclusive=True) as directory:
                    # Consume before checking/writing: uncertainty needs a fresh review.
                    self._pending = None
                    state = _read_state(directory, self._endpoint)
                    if state.file != pending.state:
                        raise _error(ProfileStorageFailure.CONFLICT)
                    if _source(self._source_path) != pending.source:
                        raise _error(ProfileStorageFailure.SOURCE_CHANGED)
                    try:
                        accepted = pending.store.commit(
                            preview,
                            imported_at=imported_at,
                            confirm_source_change=confirm_source_change,
                        )
                    except (ValueError, TypeError):
                        raise _error(ProfileStorageFailure.INVALID_REVIEW) from None
                    _same_directory(self._root, directory)
                    _publish(
                        directory,
                        _encode(self._endpoint, accepted, pending.source.data),
                        expected=pending.state,
                    )
                    published = True
            except DisplayProfileStorageError:
                if published:
                    raise _error(ProfileStorageFailure.OUTCOME_UNCONFIRMED) from None
                raise
            return accepted
