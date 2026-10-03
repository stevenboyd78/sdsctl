from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

import sds200.scanner_display_profile_favorites as favorites_profile
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.favorites_storage import FavoritesStorageDocument, FavoritesStorageSnapshot
from sds200.favorites_storage_usb import (
    FavoritesUsbStorageCandidate,
    FavoritesUsbStorageQualificationError,
    FavoritesUsbStorageQualificationReason,
    LinuxBlockDeviceEvidence,
    LinuxMountInfoEntry,
)
from sds200.scanner_display_configuration import ScannerDisplayConfiguration
from sds200.scanner_display_profile import parse_scanner_display_profile
from sds200.scanner_display_profile_favorites import (
    FavoritesCopiedTreeDisplayProfileSource,
    FavoritesDisplayProfileAcquisition,
    FavoritesMountedUsbDisplayProfileSource,
    FavoritesProfileReloadStatus,
    FavoritesScannerDisplayProfileSynchronization,
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
PROFILE_REVISION = parse_scanner_display_profile(PROFILE).revision
OTHER_PROFILE_REVISION = parse_scanner_display_profile(OTHER_PROFILE).revision
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


def _mounted_usb_candidate(tmp_path: Path) -> FavoritesUsbStorageCandidate:
    mount = tmp_path / "mounted-scanner"
    favorites = mount / "BCDx36HP" / "favorites_lists"
    favorites.mkdir(parents=True)
    (favorites / "f_list.cfg").write_bytes(FAVORITES.catalog_bytes)
    document = FAVORITES.documents[0]
    (favorites / document.filename).write_bytes(document.content)
    status = mount.stat()
    major, minor = os.major(status.st_dev), os.minor(status.st_dev)
    evidence = LinuxMountInfoEntry(
        mount_id=1,
        parent_id=2,
        device_major=major,
        device_minor=minor,
        root="/",
        mount_point=mount,
        mount_options=("ro",),
        optional_fields=(),
        filesystem_type="vfat",
        mount_source="/dev/test",
        super_options=("ro",),
    )
    block = LinuxBlockDeviceEvidence(
        device_major=major,
        device_minor=minor,
        sysfs_path=Path("/sys/devices/test/block/sdz1"),
        device_name="sdz1",
        usb_ancestor_path=Path("/sys/devices/test/usb1/1-1"),
        removable=True,
    )
    return FavoritesUsbStorageCandidate(evidence, block, mount, favorites, FAVORITES)


def test_mounted_usb_source_reads_canonical_profile_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _mounted_usb_candidate(tmp_path)
    profile = candidate.mount_directory / "BCDx36HP" / "profile.cfg"
    profile.write_bytes(PROFILE)
    before = profile.read_bytes(), profile.stat().st_mtime_ns
    calls: list[Path] = []

    def observe(path: Path, *args: object, **kwargs: object) -> FavoritesUsbStorageCandidate:
        calls.append(path)
        return candidate

    monkeypatch.setattr(favorites_profile, "_observe_favorites_usb_storage_path", observe)

    result = FavoritesMountedUsbDisplayProfileSource(
        mount_path=candidate.mount_directory
    ).read_acquisition()

    assert result == _acquisition()
    assert calls == [candidate.mount_directory, candidate.mount_directory]
    assert (profile.read_bytes(), profile.stat().st_mtime_ns) == before


def test_mounted_usb_source_refuses_profile_change_between_complete_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _mounted_usb_candidate(tmp_path)
    profile = candidate.mount_directory / "BCDx36HP" / "profile.cfg"
    profile.write_bytes(PROFILE)
    calls = 0

    def observe(*args: object, **kwargs: object) -> FavoritesUsbStorageCandidate:
        nonlocal calls
        calls += 1
        if calls == 2:
            profile.write_bytes(OTHER_PROFILE)
        return candidate

    monkeypatch.setattr(favorites_profile, "_observe_favorites_usb_storage_path", observe)
    source = FavoritesMountedUsbDisplayProfileSource(mount_path=candidate.mount_directory)

    _assert_error(ProfileStorageFailure.SOURCE_CHANGED, source.read_acquisition)


def test_mounted_usb_source_refuses_missing_profile_without_private_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _mounted_usb_candidate(tmp_path)
    monkeypatch.setattr(
        favorites_profile,
        "_observe_favorites_usb_storage_path",
        lambda *args, **kwargs: candidate,
    )
    source = FavoritesMountedUsbDisplayProfileSource(
        mount_path=candidate.mount_directory
    )

    error = _assert_error(
        ProfileStorageFailure.SOURCE_UNAVAILABLE,
        source.read_acquisition,
    )

    assert str(tmp_path) not in str(error)


def test_mounted_usb_source_redacts_usb_evidence_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = tmp_path / "PRIVATE_USB_TARGET"

    def refuse(*args: object, **kwargs: object) -> FavoritesUsbStorageCandidate:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.NOT_USB,
            private_path,
            "PRIVATE_DEVICE_EVIDENCE",
        )

    monkeypatch.setattr(
        favorites_profile,
        "_observe_favorites_usb_storage_path",
        refuse,
    )
    source = FavoritesMountedUsbDisplayProfileSource(mount_path=private_path)

    error = _assert_error(
        ProfileStorageFailure.SOURCE_UNAVAILABLE,
        source.read_acquisition,
    )

    assert str(tmp_path) not in str(error)
    assert "PRIVATE_DEVICE_EVIDENCE" not in str(error)


def test_mounted_usb_source_refuses_profile_from_another_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _mounted_usb_candidate(tmp_path)
    profile = candidate.mount_directory / "BCDx36HP" / "profile.cfg"
    profile.write_bytes(PROFILE)
    observed = favorites_profile._source(profile)
    other_device = os.makedev(
        candidate.mount.device_major,
        candidate.mount.device_minor + 1,
    )
    foreign = favorites_profile._File(
        observed.data,
        (other_device, *observed.identity[1:]),
    )
    monkeypatch.setattr(
        favorites_profile,
        "_observe_favorites_usb_storage_path",
        lambda *args, **kwargs: candidate,
    )
    monkeypatch.setattr(favorites_profile, "_source", lambda path: foreign)
    source = FavoritesMountedUsbDisplayProfileSource(
        mount_path=candidate.mount_directory
    )

    _assert_error(ProfileStorageFailure.SOURCE_CHANGED, source.read_acquisition)


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


def _synchronization(
    state: Path,
    source: _SequenceAcquisition,
    reload_accepted: Callable[[], object],
) -> FavoritesScannerDisplayProfileSynchronization:
    return FavoritesScannerDisplayProfileSynchronization(
        repository=_repository(state, source),
        binding=BINDING,
        reload_accepted=reload_accepted,
    )


def _reload_projection(revision: str) -> dict[str, object]:
    return {
        "endpoint_id": str(ENDPOINT),
        "accepted": {"revision": revision},
    }


def test_synchronization_accepts_once_and_confirms_exact_owner_revision(state: Path) -> None:
    reloads: list[str] = []
    synchronization = _synchronization(
        state,
        _SequenceAcquisition([_acquisition(), _acquisition()]),
        lambda: reloads.append("reload") or _reload_projection(PROFILE_REVISION),
    )
    preview = synchronization.prepare(acquired_at=NOW)

    result = synchronization.commit(preview, imported_at=NOW)

    assert result.accepted.profile.revision == PROFILE_REVISION
    assert result.daemon_reload is FavoritesProfileReloadStatus.CONFIRMED
    assert result.daemon_revision == PROFILE_REVISION
    assert reloads == ["reload"]
    _assert_error(
        ProfileStorageFailure.INVALID_REVIEW,
        lambda: synchronization.commit(preview, imported_at=NOW),
    )
    assert reloads == ["reload"]


def test_synchronization_retains_acceptance_when_reload_is_unconfirmed(state: Path) -> None:
    reloads = 0

    def fail_reload() -> object:
        nonlocal reloads
        reloads += 1
        raise RuntimeError("PRIVATE_RELOAD_FAILURE")

    synchronization = _synchronization(
        state,
        _SequenceAcquisition([_acquisition(), _acquisition()]),
        fail_reload,
    )
    preview = synchronization.prepare(acquired_at=NOW)

    result = synchronization.commit(preview, imported_at=NOW)

    assert result.daemon_reload is FavoritesProfileReloadStatus.UNCONFIRMED
    assert result.daemon_revision is None
    assert reloads == 1
    restored = _repository(state, _SequenceAcquisition([])).inspect().profile.last_good
    assert restored == result.accepted


@pytest.mark.parametrize(
    ("projection", "status", "revision"),
    [
        ({}, FavoritesProfileReloadStatus.UNCONFIRMED, None),
        (
            {"endpoint_id": str(UUID(int=9)), "accepted": {"revision": PROFILE_REVISION}},
            FavoritesProfileReloadStatus.UNCONFIRMED,
            None,
        ),
        (
            _reload_projection(OTHER_PROFILE_REVISION),
            FavoritesProfileReloadStatus.DIFFERENT_REVISION,
            OTHER_PROFILE_REVISION,
        ),
        (
            _reload_projection("PRIVATE_MALFORMED_REVISION"),
            FavoritesProfileReloadStatus.DIFFERENT_REVISION,
            None,
        ),
        (
            {"endpoint_id": str(ENDPOINT), "accepted": None},
            FavoritesProfileReloadStatus.DIFFERENT_REVISION,
            None,
        ),
    ],
)
def test_synchronization_requires_exact_endpoint_and_revision_confirmation(
    state: Path,
    projection: dict[str, object],
    status: FavoritesProfileReloadStatus,
    revision: str | None,
) -> None:
    synchronization = _synchronization(
        state,
        _SequenceAcquisition([_acquisition(), _acquisition()]),
        lambda: projection,
    )
    result = synchronization.commit(
        synchronization.prepare(acquired_at=NOW),
        imported_at=NOW,
    )

    assert result.daemon_reload is status
    assert result.daemon_revision == revision


def test_synchronization_never_reloads_failed_or_cancelled_review(state: Path) -> None:
    reloads: list[str] = []
    changed = _synchronization(
        state,
        _SequenceAcquisition([_acquisition(), _acquisition(profile=OTHER_PROFILE)]),
        lambda: reloads.append("reload"),
    )
    preview = changed.prepare(acquired_at=NOW)
    _assert_error(
        ProfileStorageFailure.SOURCE_CHANGED,
        lambda: changed.commit(preview, imported_at=NOW),
    )
    cancelled = _synchronization(
        state,
        _SequenceAcquisition([_acquisition()]),
        lambda: reloads.append("reload"),
    )
    preview = cancelled.prepare(acquired_at=NOW)
    cancelled.cancel(preview)

    assert reloads == []
    _assert_error(
        ProfileStorageFailure.INVALID_REVIEW,
        lambda: cancelled.commit(preview, imported_at=NOW),
    )


def test_synchronization_refuses_to_displace_pending_review(state: Path) -> None:
    synchronization = _synchronization(
        state,
        _SequenceAcquisition([_acquisition(), _acquisition()]),
        lambda: _reload_projection(PROFILE_REVISION),
    )
    preview = synchronization.prepare(acquired_at=NOW)

    _assert_error(
        ProfileStorageFailure.CONFLICT,
        lambda: synchronization.prepare(acquired_at=NOW),
    )
    synchronization.cancel(preview)


def test_synchronization_reloads_owner_without_conflating_manual_source_freshness(
    state: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manual_source = tmp_path / "manual-profile.cfg"
    manual_source.write_bytes(b"PRIVATE_INVALID_MANUAL_SOURCE")
    manual_binding = replace(
        BINDING,
        source_id=UUID(int=3),
        source_kind=DisplayProfileSourceKind.MANUAL_IMPORT,
    )
    configuration = ScannerDisplayConfiguration(
        manual_binding,
        "usb://selected-scanner",
        manual_source,
        state,
    )
    owner = DaemonDisplayProfile(configuration, lambda: "usb://selected-scanner")
    synchronization = _synchronization(
        state,
        _SequenceAcquisition([_acquisition(), _acquisition()]),
        owner.reload,
    )
    preview = synchronization.prepare(acquired_at=NOW)
    monkeypatch.setattr(
        "sds200.scanner_display_profile_storage._source",
        lambda path: pytest.fail(f"manual source read during Favorites reload: {path}"),
    )

    result = synchronization.commit(preview, imported_at=NOW)

    assert result.daemon_reload is FavoritesProfileReloadStatus.CONFIRMED
    projection = owner.snapshot()
    assert projection["source_status"] == DisplayProfileSourceStatus.UNKNOWN.value
    assert projection["accepted"]["source_kind"] == DisplayProfileSourceKind.FAVORITES_SYNC.value
    profile, failure, source_status, _ = owner.frame_context()
    assert profile.last_good == result.accepted
    assert failure is None
    assert source_status == DisplayProfileSourceStatus.UNKNOWN.value
