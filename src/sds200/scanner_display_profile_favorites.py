"""Read-only display-profile acquisition paired with one Favorites snapshot.

This internal foundation does not start a Favorites synchronization, discover a
scanner, switch storage modes, open a network service, or write Favorites. An
operator-selected source supplies one exact Favorites snapshot and one exact
``profile.cfg`` copy. Two identical acquisition passes are required before a
review can begin, and the complete acquisition is checked again before the
accepted display profile is atomically replaced.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .favorites_storage import FavoritesStorageSnapshot, FavoritesStorageSource
from .favorites_storage_local import FavoritesCopiedTreeStorageSource
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


__all__ = [
    "FavoritesCopiedTreeDisplayProfileSource",
    "FavoritesDisplayProfileAcquisition",
    "FavoritesDisplayProfileAcquisitionSource",
    "PairedFavoritesDisplayProfileSource",
    "PersistentFavoritesScannerDisplayProfile",
]
