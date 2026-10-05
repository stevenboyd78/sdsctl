"""Read-only display-profile acquisition paired with one Favorites snapshot.

This internal foundation does not start a Favorites synchronization, discover a
scanner, switch storage modes, open a network service, or write Favorites. An
operator-selected source supplies one exact Favorites snapshot and one exact
``profile.cfg`` copy. Two identical acquisition passes are required before a
review can begin, and the complete acquisition is checked again before the
accepted display profile is atomically replaced.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .favorites_storage import FavoritesStorageSnapshot, FavoritesStorageSource
from .favorites_storage_local import FavoritesCopiedTreeStorageSource
from .favorites_storage_usb import (
    DEFAULT_LINUX_MOUNTINFO_PATH,
    DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
    FavoritesUsbStorageCandidate,
    FavoritesUsbStorageQualificationError,
    _observe_favorites_usb_storage_path,
)
from .scanner_display_profile import MAX_PROFILE_BYTES
from .scanner_display_profile_state import (
    DisplayProfileBinding,
    DisplayProfileImport,
    DisplayProfilePreview,
    DisplayProfileSourceKind,
    ScannerDisplayProfileStore,
)
from .scanner_display_profile_storage import (
    DiskDisplayProfileSnapshot,
    DisplayProfileSourceStatus,
    DisplayProfileStorageError,
    ProfileStorageFailure,
    _access,
    _encode,
    _error,
    _File,
    _path,
    _publish,
    _read_state,
    _same_directory,
    _source,
    _store,
)


@dataclass(frozen=True, slots=True, repr=False)
class FavoritesDisplayProfileAcquisition:
    """One private exact Favorites/profile observation; raw bytes stay local."""

    favorites: FavoritesStorageSnapshot
    profile: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.favorites, FavoritesStorageSnapshot):
            raise TypeError("Favorites display acquisition requires a Favorites snapshot.")
        if not isinstance(self.profile, bytes):
            raise TypeError("Favorites display acquisition profile must be bytes.")
        if not self.profile or len(self.profile) > MAX_PROFILE_BYTES:
            raise ValueError("Favorites display acquisition profile has an invalid size.")


class FavoritesDisplayProfileAcquisitionSource(Protocol):
    """Return one internally coherent read-only Favorites/profile observation."""

    def read_acquisition(self) -> FavoritesDisplayProfileAcquisition:
        """Return exact private bytes or a sanitized storage failure."""
        ...


class FavoritesProfileReloadStatus(StrEnum):
    """Outcome of the one reload request after a durable profile acceptance."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    DIFFERENT_REVISION = "different_revision"


@dataclass(frozen=True, slots=True)
class FavoritesProfileSynchronizationResult:
    """One accepted revision and the independently observed owner-reload outcome."""

    accepted: DisplayProfileImport
    daemon_reload: FavoritesProfileReloadStatus
    daemon_revision: str | None


def _read_favorites(source: FavoritesStorageSource) -> FavoritesStorageSnapshot:
    try:
        snapshot = source.read_snapshot()
    except Exception:
        raise _error(ProfileStorageFailure.SOURCE_UNAVAILABLE) from None
    if not isinstance(snapshot, FavoritesStorageSnapshot):
        raise _error(ProfileStorageFailure.SOURCE_UNAVAILABLE)
    return snapshot


def _read_profile(reader: Callable[[], bytes]) -> bytes:
    try:
        data = reader()
    except DisplayProfileStorageError:
        raise
    except Exception:
        raise _error(ProfileStorageFailure.SOURCE_UNAVAILABLE) from None
    if not isinstance(data, bytes) or not data or len(data) > MAX_PROFILE_BYTES:
        raise _error(ProfileStorageFailure.INVALID_PROFILE)
    return data


@dataclass(frozen=True, slots=True, repr=False)
class PairedFavoritesDisplayProfileSource:
    """Require two identical complete Favorites/profile acquisition passes."""

    favorites_source: FavoritesStorageSource
    profile_reader: Callable[[], bytes]

    def __post_init__(self) -> None:
        if not callable(getattr(self.favorites_source, "read_snapshot", None)):
            raise TypeError("Favorites display acquisition requires a Favorites source.")
        if not callable(self.profile_reader):
            raise TypeError("Favorites display acquisition requires a profile reader.")

    def _read_pass(self) -> FavoritesDisplayProfileAcquisition:
        return FavoritesDisplayProfileAcquisition(
            _read_favorites(self.favorites_source),
            _read_profile(self.profile_reader),
        )

    def read_acquisition(self) -> FavoritesDisplayProfileAcquisition:
        first = self._read_pass()
        second = self._read_pass()
        if first != second:
            raise _error(ProfileStorageFailure.SOURCE_CHANGED)
        return second


class FavoritesCopiedTreeDisplayProfileSource:
    """Pair an explicit copied Favorites tree with an explicit profile file."""

    def __init__(self, *, favorites_directory: Path, profile_path: Path) -> None:
        if not isinstance(favorites_directory, Path):
            raise TypeError("Favorites display copied-tree directory must be pathlib.Path.")
        self._profile_path = _path(profile_path)
        self._favorites = FavoritesCopiedTreeStorageSource(favorites_directory)

    def read_acquisition(self) -> FavoritesDisplayProfileAcquisition:
        source = PairedFavoritesDisplayProfileSource(
            self._favorites,
            lambda: _source(self._profile_path).data,
        )
        return source.read_acquisition()


@dataclass(frozen=True, slots=True, repr=False)
class _MountedUsbAcquisitionPass:
    candidate: FavoritesUsbStorageCandidate
    profile: _File


class FavoritesMountedUsbDisplayProfileSource:
    """Read the canonical profile beside one explicit mounted USB Favorites tree."""

    def __init__(
        self,
        *,
        mount_path: Path,
        mountinfo_path: Path = DEFAULT_LINUX_MOUNTINFO_PATH,
        sys_dev_block_directory: Path = DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
        require_read_only: bool = False,
    ) -> None:
        self._mount_path = _path(mount_path)
        if not isinstance(mountinfo_path, Path) or not isinstance(
            sys_dev_block_directory, Path
        ) or type(require_read_only) is not bool:
            raise TypeError("Mounted USB evidence paths and read-only policy are invalid.")
        self._mountinfo_path = mountinfo_path
        self._sys_dev_block_directory = sys_dev_block_directory
        self._require_read_only = require_read_only

    def _read_pass(self) -> _MountedUsbAcquisitionPass:
        try:
            candidate = _observe_favorites_usb_storage_path(
                self._mount_path,
                self._mountinfo_path,
                sys_dev_block_directory=self._sys_dev_block_directory,
            )
        except FavoritesUsbStorageQualificationError:
            raise _error(ProfileStorageFailure.SOURCE_UNAVAILABLE) from None
        if self._require_read_only and not candidate.is_read_only:
            raise _error(ProfileStorageFailure.SOURCE_UNAVAILABLE)
        profile = _source(candidate.mount_directory / "BCDx36HP" / "profile.cfg")
        device = profile.identity[0]
        if (os.major(device), os.minor(device)) != candidate.mount.device_number:
            raise _error(ProfileStorageFailure.SOURCE_CHANGED)
        return _MountedUsbAcquisitionPass(candidate, profile)

    def read_acquisition(self) -> FavoritesDisplayProfileAcquisition:
        first = self._read_pass()
        second = self._read_pass()
        if first != second:
            raise _error(ProfileStorageFailure.SOURCE_CHANGED)
        return FavoritesDisplayProfileAcquisition(second.candidate.snapshot, second.profile.data)


@dataclass(frozen=True, slots=True, repr=False)
class _PendingFavoritesProfile:
    store: ScannerDisplayProfileStore
    preview: DisplayProfilePreview
    acquisition: FavoritesDisplayProfileAcquisition
    state: _File


class PersistentFavoritesScannerDisplayProfile:
    """Explicit review/commit for one operator-selected Favorites acquisition.

    The source must already represent an authorized read-only synchronization.
    This class persists only the validated display-profile bytes and provenance;
    it never writes the Favorites snapshot or the selected source.
    """

    def __init__(
        self,
        *,
        acquisition_source: FavoritesDisplayProfileAcquisitionSource,
        state_directory: Path,
        endpoint_id: UUID,
    ) -> None:
        if not callable(getattr(acquisition_source, "read_acquisition", None)):
            raise _error(ProfileStorageFailure.INVALID_REVIEW)
        self._source = acquisition_source
        self._root = _path(state_directory)
        if not isinstance(endpoint_id, UUID):
            raise _error(ProfileStorageFailure.INVALID_REVIEW)
        self._endpoint = endpoint_id
        self._lock = threading.Lock()
        self._pending: _PendingFavoritesProfile | None = None

    def _acquire(self) -> FavoritesDisplayProfileAcquisition:
        try:
            acquisition = self._source.read_acquisition()
        except DisplayProfileStorageError:
            raise
        except Exception:
            raise _error(ProfileStorageFailure.SOURCE_UNAVAILABLE) from None
        if not isinstance(acquisition, FavoritesDisplayProfileAcquisition):
            raise _error(ProfileStorageFailure.SOURCE_UNAVAILABLE)
        return acquisition

    def inspect(self) -> DiskDisplayProfileSnapshot:
        """Restore last-good state without claiming current scanner freshness."""
        with self._lock, _access(self._root, exclusive=False) as directory:
            state = _read_state(directory, self._endpoint)
            status = (
                DisplayProfileSourceStatus.NOT_IMPORTED
                if state.accepted is None
                else DisplayProfileSourceStatus.UNKNOWN
            )
            return DiskDisplayProfileSnapshot(
                _store(self._endpoint, state).snapshot(self._endpoint), status
            )

    def prepare(
        self,
        binding: DisplayProfileBinding,
        *,
        acquired_at: datetime,
    ) -> DisplayProfilePreview:
        with self._lock:
            self._pending = None
            if (
                not isinstance(binding, DisplayProfileBinding)
                or binding.endpoint_id != self._endpoint
                or binding.source_kind is not DisplayProfileSourceKind.FAVORITES_SYNC
            ):
                raise _error(ProfileStorageFailure.INVALID_REVIEW)
            with _access(self._root, exclusive=False) as directory:
                state = _read_state(directory, self._endpoint)
                acquisition = self._acquire()
                store = _store(self._endpoint, state)
                try:
                    ticket = store.begin_refresh(binding, started_at=acquired_at)
                except (ValueError, TypeError):
                    raise _error(ProfileStorageFailure.INVALID_REVIEW) from None
                try:
                    preview = store.prepare(
                        ticket,
                        acquisition.profile,
                        acquired_at=acquired_at,
                    )
                except (ValueError, TypeError):
                    raise _error(ProfileStorageFailure.INVALID_PROFILE) from None
            self._pending = _PendingFavoritesProfile(store, preview, acquisition, state.file)
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
                    self._pending = None
                    state = _read_state(directory, self._endpoint)
                    if state.file != pending.state:
                        raise _error(ProfileStorageFailure.CONFLICT)
                    if self._acquire() != pending.acquisition:
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
                        _encode(self._endpoint, accepted, pending.acquisition.profile),
                        expected=pending.state,
                    )
                    published = True
            except DisplayProfileStorageError:
                if published:
                    raise _error(ProfileStorageFailure.OUTCOME_UNCONFIRMED) from None
                raise
            return accepted


class FavoritesScannerDisplayProfileSynchronization:
    """Couple one explicit Favorites review to one revision-checked owner reload.

    The durable acceptance is authoritative even when the reload result is lost.
    A caller may inspect or reload that accepted state later, but must not replay
    the acquisition commit merely to obtain a different reload result.
    """

    def __init__(
        self,
        *,
        repository: PersistentFavoritesScannerDisplayProfile,
        binding: DisplayProfileBinding,
        reload_accepted: Callable[[], object],
    ) -> None:
        if (
            not isinstance(repository, PersistentFavoritesScannerDisplayProfile)
            or not isinstance(binding, DisplayProfileBinding)
            or binding.source_kind is not DisplayProfileSourceKind.FAVORITES_SYNC
            or not callable(reload_accepted)
        ):
            raise _error(ProfileStorageFailure.INVALID_REVIEW)
        self._repository = repository
        self._binding = binding
        self._reload_accepted = reload_accepted
        self._lock = threading.Lock()
        self._pending: DisplayProfilePreview | None = None

    def prepare(self, *, acquired_at: datetime) -> DisplayProfilePreview:
        """Acquire and prepare once; a prior unconsumed review is never displaced."""
        with self._lock:
            if self._pending is not None:
                raise _error(ProfileStorageFailure.CONFLICT)
            preview = self._repository.prepare(self._binding, acquired_at=acquired_at)
            self._pending = preview
            return preview

    def cancel(self, preview: DisplayProfilePreview) -> None:
        with self._lock:
            if self._pending is not preview:
                raise _error(ProfileStorageFailure.INVALID_REVIEW)
            self._repository.cancel(preview)
            self._pending = None

    def commit(
        self,
        preview: DisplayProfilePreview,
        *,
        imported_at: datetime,
        confirm_source_change: bool = False,
    ) -> FavoritesProfileSynchronizationResult:
        """Accept once, then request exactly one reload and classify its evidence."""
        with self._lock:
            if self._pending is not preview:
                raise _error(ProfileStorageFailure.INVALID_REVIEW)
            # Consume before durable mutation. A lost result is not replay authority.
            self._pending = None
            accepted = self._repository.commit(
                preview,
                imported_at=imported_at,
                confirm_source_change=confirm_source_change,
            )
            try:
                result = self._reload_accepted()
            except Exception:
                return FavoritesProfileSynchronizationResult(
                    accepted, FavoritesProfileReloadStatus.UNCONFIRMED, None
                )
            if not isinstance(result, Mapping) or result.get("endpoint_id") != str(
                self._binding.endpoint_id
            ):
                return FavoritesProfileSynchronizationResult(
                    accepted, FavoritesProfileReloadStatus.UNCONFIRMED, None
                )
            daemon_accepted = result.get("accepted")
            daemon_revision = (
                daemon_accepted.get("revision")
                if isinstance(daemon_accepted, Mapping)
                and isinstance(daemon_accepted.get("revision"), str)
                and len(daemon_accepted["revision"]) == 64
                and all(char in "0123456789abcdef" for char in daemon_accepted["revision"])
                else None
            )
            status = (
                FavoritesProfileReloadStatus.CONFIRMED
                if daemon_revision == accepted.profile.revision
                else FavoritesProfileReloadStatus.DIFFERENT_REVISION
            )
            return FavoritesProfileSynchronizationResult(accepted, status, daemon_revision)


__all__ = [
    "FavoritesCopiedTreeDisplayProfileSource",
    "FavoritesDisplayProfileAcquisition",
    "FavoritesDisplayProfileAcquisitionSource",
    "FavoritesMountedUsbDisplayProfileSource",
    "FavoritesProfileReloadStatus",
    "FavoritesProfileSynchronizationResult",
    "FavoritesScannerDisplayProfileSynchronization",
    "PairedFavoritesDisplayProfileSource",
    "PersistentFavoritesScannerDisplayProfile",
]
