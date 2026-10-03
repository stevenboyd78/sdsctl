from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from sds200.favorites_storage import FavoritesStorageDocument, FavoritesStorageSnapshot
from sds200.scanner_display_profile_favorites import (
    FavoritesCopiedTreeDisplayProfileSource,
    FavoritesDisplayProfileAcquisition,
    PairedFavoritesDisplayProfileSource,
    PersistentFavoritesScannerDisplayProfile,
)
from sds200.scanner_display_profile_state import DisplayProfileBinding, DisplayProfileSourceKind
from sds200.scanner_display_profile_storage import (
    DisplayProfileSourceStatus,
    DisplayProfileStorageError,
    PersistentScannerDisplayProfile,
    ProfileStorageFailure,
    initialize_display_profile_storage,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX durable storage foundation")

ENDPOINT = UUID(int=1)
SOURCE_ID = UUID(int=2)
BINDING = DisplayProfileBinding(ENDPOINT, SOURCE_ID, DisplayProfileSourceKind.FAVORITES_SYNC)
NOW = datetime(2026, 10, 3, 7, 0, tzinfo=UTC)
PROFILE = (
    b"Owner\tPRIVATE_SENTINEL\r\n"
    b"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\tOff\tAFS\tCOLOR\r\n"
    b"DispOptItems\tDispOptId=2\tDispLayoutId=1\tFrequency\tEmpty\r\n"
    b"DispColors\tDispColorId=2\tColorLayoutId=1\tffffff\t000000\r\n"
)
OTHER_PROFILE = PROFILE.replace(b"ffffff", b"123456")
FAVORITES = FavoritesStorageSnapshot(
    b"catalog\r\n",
    (FavoritesStorageDocument("f_000001.hpd", b"document\r\n"),),
)
OTHER_FAVORITES = FavoritesStorageSnapshot(b"changed\r\n", FAVORITES.documents)


class _SequenceFavorites:
    def __init__(
        self,
        snapshots: list[FavoritesStorageSnapshot | BaseException],
    ) -> None:
        self.snapshots = snapshots

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        if not self.snapshots:
            raise RuntimeError("PRIVATE_FAVORITES_FAILURE")
        value = self.snapshots.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _SequenceReader:
    def __init__(self, values: list[bytes | BaseException]) -> None:
        self.values = values

    def __call__(self) -> bytes:
        if not self.values:
            raise RuntimeError("PRIVATE_PROFILE_FAILURE")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _SequenceAcquisition:
    def __init__(self, values: list[FavoritesDisplayProfileAcquisition | BaseException]) -> None:
        self.values = values

    def read_acquisition(self) -> FavoritesDisplayProfileAcquisition:
        if not self.values:
            raise RuntimeError("PRIVATE_ACQUISITION_FAILURE")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _acquisition(
    profile: bytes = PROFILE,
    favorites: FavoritesStorageSnapshot = FAVORITES,
) -> FavoritesDisplayProfileAcquisition:
    return FavoritesDisplayProfileAcquisition(favorites, profile)


def _repository(
    state: Path,
    source: _SequenceAcquisition,
    *,
    endpoint: UUID = ENDPOINT,
) -> PersistentFavoritesScannerDisplayProfile:
    return PersistentFavoritesScannerDisplayProfile(
        acquisition_source=source,
        state_directory=state,
        endpoint_id=endpoint,
    )


def _assert_error(
    reason: ProfileStorageFailure,
    action: Callable[[], object],
) -> DisplayProfileStorageError:
    with pytest.raises(DisplayProfileStorageError) as raised:
        action()
    assert raised.value.category is reason
    assert "PRIVATE" not in str(raised.value)
    return raised.value


@pytest.fixture
def state(tmp_path: Path) -> Path:
    root = tmp_path / "private-state"
    initialize_display_profile_storage(root, ENDPOINT)
    return root


def test_paired_source_requires_two_identical_complete_passes() -> None:
    source = PairedFavoritesDisplayProfileSource(
        _SequenceFavorites([FAVORITES, FAVORITES]),
        _SequenceReader([PROFILE, PROFILE]),
    )

    result = source.read_acquisition()

    assert result == _acquisition()
    assert "PRIVATE_SENTINEL" not in repr(result)
    assert "PRIVATE_SENTINEL" not in repr(source)


@pytest.mark.parametrize("change", ["favorites", "profile"])
def test_paired_source_refuses_a_changed_verification_pass(change: str) -> None:
    favorites = [FAVORITES, OTHER_FAVORITES if change == "favorites" else FAVORITES]
    profiles = [PROFILE, OTHER_PROFILE if change == "profile" else PROFILE]
    source = PairedFavoritesDisplayProfileSource(
        _SequenceFavorites(favorites),
        _SequenceReader(profiles),
    )

    _assert_error(ProfileStorageFailure.SOURCE_CHANGED, source.read_acquisition)


@pytest.mark.parametrize("failure", ["favorites", "profile"])
def test_paired_source_redacts_partial_acquisition_failures(failure: str) -> None:
    favorites = _SequenceFavorites(
        [FAVORITES, RuntimeError("PRIVATE_PATH")]
        if failure == "favorites"
        else [FAVORITES, FAVORITES]
    )
    profiles = _SequenceReader(
        [PROFILE, RuntimeError("PRIVATE_PATH")] if failure == "profile" else [PROFILE, PROFILE]
    )
    source = PairedFavoritesDisplayProfileSource(favorites, profiles)

    _assert_error(ProfileStorageFailure.SOURCE_UNAVAILABLE, source.read_acquisition)


def test_copied_tree_source_reads_explicit_profile_without_mutation(tmp_path: Path) -> None:
    favorites = tmp_path / "copy" / "favorites_lists"
    favorites.mkdir(parents=True)
    (favorites / "f_list.cfg").write_bytes(FAVORITES.catalog_bytes)
    document = FAVORITES.documents[0]
    (favorites / document.filename).write_bytes(document.content)
    profile = tmp_path / "copy" / "profile.cfg"
    profile.write_bytes(PROFILE)
    before = profile.read_bytes(), profile.stat()

    result = FavoritesCopiedTreeDisplayProfileSource(
        favorites_directory=favorites,
        profile_path=profile,
    ).read_acquisition()

    assert result == _acquisition()
    assert (profile.read_bytes(), profile.stat()) == before


def test_copied_tree_source_refuses_missing_profile_without_private_path(tmp_path: Path) -> None:
    favorites = tmp_path / "favorites_lists"
    favorites.mkdir()
    (favorites / "f_list.cfg").write_bytes(FAVORITES.catalog_bytes)
    source = FavoritesCopiedTreeDisplayProfileSource(
        favorites_directory=favorites,
        profile_path=tmp_path / "missing-profile.cfg",
    )

    error = _assert_error(ProfileStorageFailure.SOURCE_UNAVAILABLE, source.read_acquisition)
    assert str(tmp_path) not in str(error)


def test_favorites_import_is_atomic_and_restores_with_unknown_freshness(state: Path) -> None:
    source = _SequenceAcquisition([_acquisition(), _acquisition()])
    repository = _repository(state, source)

    preview = repository.prepare(BINDING, acquired_at=NOW)
    accepted = repository.commit(preview, imported_at=NOW + timedelta(seconds=1))

    restored = _repository(state, _SequenceAcquisition([])).inspect()
    assert restored.profile.last_good == accepted
    assert restored.source_status is DisplayProfileSourceStatus.UNKNOWN
    state_bytes = (state / "accepted-profile.json").read_bytes()
    document = json.loads(state_bytes)
    assert document["accepted"]["source_kind"] == "favorites_sync"
    assert b"catalog" not in state_bytes
    assert b"document" not in state_bytes


def test_failed_refresh_preserves_last_good(state: Path) -> None:
    initial = _repository(state, _SequenceAcquisition([_acquisition(), _acquisition()]))
    accepted = initial.commit(
        initial.prepare(BINDING, acquired_at=NOW),
        imported_at=NOW,
    )
    failed = _repository(state, _SequenceAcquisition([RuntimeError("PRIVATE_FAILED_SYNC")]))

    _assert_error(
        ProfileStorageFailure.SOURCE_UNAVAILABLE,
        lambda: failed.prepare(BINDING, acquired_at=NOW + timedelta(seconds=1)),
    )
    assert failed.inspect().profile.last_good == accepted


def test_complete_acquisition_change_during_review_preserves_last_good(state: Path) -> None:
    source = _SequenceAcquisition([_acquisition(), _acquisition(profile=OTHER_PROFILE)])
    repository = _repository(state, source)
    preview = repository.prepare(BINDING, acquired_at=NOW)

    _assert_error(
        ProfileStorageFailure.SOURCE_CHANGED,
        lambda: repository.commit(preview, imported_at=NOW),
    )
    assert repository.inspect().profile.last_good is None
    _assert_error(
        ProfileStorageFailure.INVALID_REVIEW,
        lambda: repository.commit(preview, imported_at=NOW),
    )


@pytest.mark.parametrize("acquired_at", [None, NOW.replace(tzinfo=None)])
def test_invalid_acquisition_timestamp_is_review_failure(
    state: Path,
    acquired_at: datetime | None,
) -> None:
    repository = _repository(state, _SequenceAcquisition([_acquisition()]))
    _assert_error(
        ProfileStorageFailure.INVALID_REVIEW,
        lambda: repository.prepare(BINDING, acquired_at=acquired_at),  # type: ignore[arg-type]
    )


def test_invalid_profile_does_not_replace_last_good(state: Path) -> None:
    initial = _repository(state, _SequenceAcquisition([_acquisition(), _acquisition()]))
    accepted = initial.commit(initial.prepare(BINDING, acquired_at=NOW), imported_at=NOW)
    invalid = _repository(
        state,
        _SequenceAcquisition([_acquisition(profile=b"PRIVATE_INVALID")]),
    )

    _assert_error(
        ProfileStorageFailure.INVALID_PROFILE,
        lambda: invalid.prepare(BINDING, acquired_at=NOW + timedelta(seconds=1)),
    )
    assert invalid.inspect().profile.last_good == accepted


@pytest.mark.parametrize(
    "binding",
    [
        replace(BINDING, endpoint_id=UUID(int=9)),
        replace(BINDING, source_kind=DisplayProfileSourceKind.MANUAL_IMPORT),
        None,
    ],
)
def test_favorites_import_requires_exact_endpoint_and_source_kind(
    state: Path,
    binding: DisplayProfileBinding | None,
) -> None:
    repository = _repository(state, _SequenceAcquisition([_acquisition()]))
    _assert_error(
        ProfileStorageFailure.INVALID_REVIEW,
        lambda: repository.prepare(binding, acquired_at=NOW),  # type: ignore[arg-type]
    )


def test_source_replacement_requires_explicit_confirmation(state: Path, tmp_path: Path) -> None:
    manual_file = tmp_path / "profile.cfg"
    manual_file.write_bytes(PROFILE)
    manual = PersistentScannerDisplayProfile(
        source_path=manual_file,
        state_directory=state,
        endpoint_id=ENDPOINT,
    )
    manual_binding = replace(BINDING, source_kind=DisplayProfileSourceKind.MANUAL_IMPORT)
    manual_preview = manual.prepare(manual_binding, acquired_at=NOW)
    manual.commit(manual_preview, imported_at=NOW)

    source = _SequenceAcquisition([_acquisition(), _acquisition()])
    repository = _repository(state, source)
    preview = repository.prepare(BINDING, acquired_at=NOW + timedelta(seconds=1))
    assert preview.source_changed
    _assert_error(
        ProfileStorageFailure.INVALID_REVIEW,
        lambda: repository.commit(preview, imported_at=NOW + timedelta(seconds=1)),
    )

    source = _SequenceAcquisition([_acquisition(), _acquisition()])
    repository = _repository(state, source)
    preview = repository.prepare(BINDING, acquired_at=NOW + timedelta(seconds=1))
    accepted = repository.commit(
        preview,
        imported_at=NOW + timedelta(seconds=1),
        confirm_source_change=True,
    )
    assert accepted.provenance.binding == BINDING


def test_concurrent_reviews_cannot_overwrite_each_other(state: Path) -> None:
    first = _repository(state, _SequenceAcquisition([_acquisition(), _acquisition()]))
    second = _repository(state, _SequenceAcquisition([_acquisition(), _acquisition()]))
    first_preview = first.prepare(BINDING, acquired_at=NOW)
    second_preview = second.prepare(BINDING, acquired_at=NOW)
    accepted = first.commit(first_preview, imported_at=NOW)

    _assert_error(
        ProfileStorageFailure.CONFLICT,
        lambda: second.commit(second_preview, imported_at=NOW),
    )
    assert second.inspect().profile.last_good == accepted


def test_cancel_is_one_use_and_changes_no_state(state: Path) -> None:
    before = (state / "accepted-profile.json").read_bytes()
    repository = _repository(state, _SequenceAcquisition([_acquisition()]))
    preview = repository.prepare(BINDING, acquired_at=NOW)
    repository.cancel(preview)

    assert (state / "accepted-profile.json").read_bytes() == before
    _assert_error(ProfileStorageFailure.INVALID_REVIEW, lambda: repository.cancel(preview))
