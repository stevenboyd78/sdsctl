"""Explicit reviewed replacement of a private managed profile copy.

The source copy and accepted record are two files, not one filesystem transaction.
The accepted document remains the single authority. A failure after source
replacement preserves its last-good record and requires inspection, never replay.
Manual import remains read-only; only this opt-in adapter writes a managed source.
"""

from __future__ import annotations

import os
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .scanner_display_configuration import ScannerDisplayConfiguration
from .scanner_display_profile import MAX_PROFILE_BYTES
from .scanner_display_profile_state import DisplayProfileImport, DisplayProfilePreview
from .scanner_display_profile_storage import (
    DisplayProfileStorageError,
    ProfileStorageFailure,
    _access,
    _directory,
    _encode,
    _File,
    _identity,
    _private_directory,
    _publish,
    _read_file,
    _read_state,
    _same_directory,
    _store,
)


class DisplayUploadUnconfirmed(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Source-copy replacement or profile acceptance could not be confirmed. "
            "Preserve the files and inspect status before further action; do not repeat the upload."
        )


def _managed_source(directory: int, name: str) -> _File | None:
    try:
        return _read_file(directory, name, MAX_PROFILE_BYTES, private=True)
    except FileNotFoundError:
        return None


def _observe_source(path: Path) -> _File | None:
    directory = None
    try:
        directory = _directory(path.parent)
        _private_directory(directory)
        source = _managed_source(directory, path.name)
        _same_directory(path.parent, directory)
        return source
    except OSError:
        raise DisplayProfileStorageError(ProfileStorageFailure.UNSAFE_PATH) from None
    finally:
        if directory is not None:
            os.close(directory)


def _replace_source(path: Path, content: bytes, expected: _File | None) -> _File:
    """Private complete-file staging; CAS guards cooperating writers, not hostile root."""
    directory = descriptor = None
    renamed = False
    temporary = f".upload-{uuid4().hex}.tmp"
    try:
        directory = _directory(path.parent)
        _private_directory(directory)
        if _managed_source(directory, path.name) != expected:
            raise DisplayProfileStorageError(ProfileStorageFailure.SOURCE_CHANGED)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(content):
            count = os.write(descriptor, content[offset:])
            if count <= 0:
                raise OSError()
            offset += count
        os.fsync(descriptor)
        if _read_file(directory, temporary, MAX_PROFILE_BYTES, private=True).data != content:
            raise OSError()
        _private_directory(directory)
        _same_directory(path.parent, directory)
        if _managed_source(directory, path.name) != expected:
            raise DisplayProfileStorageError(ProfileStorageFailure.SOURCE_CHANGED)
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        renamed = True
        os.fsync(directory)
        observed = _managed_source(directory, path.name)
        if observed is None or observed.data != content:
            raise OSError()
        _same_directory(path.parent, directory)
        return observed
    except (OSError, DisplayProfileStorageError) as exc:
        if renamed:
            raise DisplayUploadUnconfirmed() from None
        if isinstance(exc, DisplayProfileStorageError):
            raise
        raise DisplayProfileStorageError(ProfileStorageFailure.WRITE_FAILED) from None
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
        if directory is not None:
            os.close(directory)


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedUpload:
    preview: DisplayProfilePreview
    data: bytes
    source: _File | None
    state: _File


class ScannerDisplayUpload:
    """One in-memory, one-use review; no filesystem staging before confirmation."""

    def __init__(self, configuration: ScannerDisplayConfiguration) -> None:
        self._config = configuration
        self._lock = threading.Lock()
        self._pending: _PreparedUpload | None = None

    def prepare(self, data: bytes, *, acquired_at: datetime) -> DisplayProfilePreview:
        with self._lock:
            self._pending = None
            if type(data) is not bytes or not 0 < len(data) <= MAX_PROFILE_BYTES:
                raise DisplayProfileStorageError(ProfileStorageFailure.INVALID_PROFILE)
            with _access(self._config.state_directory, exclusive=False) as directory:
                state = _read_state(directory, self._config.binding.endpoint_id)
                source = _observe_source(self._config.source_path)
                store = _store(self._config.binding.endpoint_id, state)
                try:
                    ticket = store.begin_refresh(self._config.binding, started_at=acquired_at)
                    preview = store.prepare(ticket, data, acquired_at=acquired_at)
                except (ValueError, TypeError):
                    raise DisplayProfileStorageError(
                        ProfileStorageFailure.INVALID_PROFILE
                    ) from None
            self._pending = _PreparedUpload(preview, data, source, state.file)
            return preview

    def cancel(self, preview: DisplayProfilePreview) -> None:
        with self._lock:
            if self._pending is None or self._pending.preview is not preview:
                raise DisplayProfileStorageError(ProfileStorageFailure.INVALID_REVIEW)
            self._pending = None

    def commit(
        self,
        preview: DisplayProfilePreview,
        *,
        imported_at: datetime,
        confirm_source_change: bool = False,
    ) -> DisplayProfileImport:
        with self._lock:
            pending, self._pending = self._pending, None  # Never replay, even on failure.
            if pending is None or pending.preview is not preview:
                raise DisplayProfileStorageError(ProfileStorageFailure.INVALID_REVIEW)
            replaced = False
            try:
                with _access(self._config.state_directory, exclusive=True) as directory:
                    state = _read_state(directory, self._config.binding.endpoint_id)
                    if state.file != pending.state:
                        raise DisplayProfileStorageError(ProfileStorageFailure.CONFLICT)
                    if _observe_source(self._config.source_path) != pending.source:
                        raise DisplayProfileStorageError(ProfileStorageFailure.SOURCE_CHANGED)
                    store = _store(self._config.binding.endpoint_id, state)
                    try:
                        ticket = store.begin_refresh(
                            self._config.binding, started_at=preview.acquired_at
                        )
                        prepared = store.prepare(
                            ticket, pending.data, acquired_at=preview.acquired_at
                        )
                        accepted = store.commit(
                            prepared,
                            imported_at=imported_at,
                            confirm_source_change=confirm_source_change,
                        )
                    except (ValueError, TypeError):
                        raise DisplayProfileStorageError(
                            ProfileStorageFailure.INVALID_REVIEW
                        ) from None
                    _same_directory(self._config.state_directory, directory)
                    observed = _replace_source(
                        self._config.source_path, pending.data, pending.source
                    )
                    replaced = True
                    if _observe_source(self._config.source_path) != observed:
                        raise DisplayUploadUnconfirmed()
                    _publish(
                        directory,
                        _encode(self._config.binding.endpoint_id, accepted, pending.data),
                        expected=pending.state,
                    )
                return accepted
            except (OSError, DisplayProfileStorageError):
                if replaced:
                    raise DisplayUploadUnconfirmed() from None
                raise
