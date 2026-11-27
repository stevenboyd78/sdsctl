from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sds200.favorites_profile_store import (
    FAVORITES_PROFILE_STORE_FILENAME,
    FavoritesProfileCompatibility,
    FavoritesProfileConflictError,
    FavoritesProfileLimitError,
    FavoritesProfileNameError,
    FavoritesProfileNotFoundError,
    FavoritesProfileSnapshotError,
    FavoritesProfileStore,
    FavoritesProfileStoreClosedError,
    FavoritesProfileStoreLimits,
    FavoritesProfileStoreUnavailableError,
)
from sds200.favorites_storage import FavoritesStorageDocument, FavoritesStorageSnapshot

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _snapshot(*, name: str = "Synthetic Favorites") -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=f"F-List\t{name}\tf_000001.hpd\r\n".encode(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
            ),
        ),
    )


def _tokens(*values: str) -> Iterator[str]:
    yield from values


def _store(
    tmp_path: Path,
    *,
    limits: FavoritesProfileStoreLimits | None = None,
    tokens: Iterator[str] | None = None,
) -> FavoritesProfileStore:
    token_values = tokens or _tokens("profile_identifier_000001")
    return FavoritesProfileStore(
        tmp_path / "profiles",
        limits=limits,
        clock=iter((100, 200, 300, 400, 500)).__next__,
        token_factory=token_values.__next__,
    )


@pytest.mark.parametrize("compatibility", tuple(FavoritesProfileCompatibility))
def test_profile_persists_exact_snapshot_and_compatibility(
    tmp_path: Path,
    compatibility: FavoritesProfileCompatibility,
) -> None:
    original = _snapshot()
    store = _store(tmp_path)

    created = store.create_profile("Home", compatibility, original)

    assert created.summary.profile_id == "profile_identifier_000001"
    assert created.summary.name == "Home"
    assert created.summary.compatibility is compatibility
    assert created.summary.revision == 1
    assert created.summary.revision_count == 1
    assert created.summary.document_count == 1
    assert created.summary.snapshot_bytes == (
        len(original.catalog_bytes) + len(original.documents[0].content)
    )
    assert len(created.summary.snapshot_sha256) == 64
    assert created.summary.created_at_ns == 100
    assert created.summary.updated_at_ns == 100
    assert created.snapshot == original
    store.close()

    reopened = FavoritesProfileStore(tmp_path / "profiles")
    assert reopened.get_profile(created.summary.profile_id) == created
    assert reopened.list_profiles() == (created.summary,)
    reopened.close()


def test_store_creates_owner_only_directory_and_database(tmp_path: Path) -> None:
    store = _store(tmp_path)

    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "profiles").stat().st_mode) == 0o700
        assert (
            stat.S_IMODE((tmp_path / "profiles" / FAVORITES_PROFILE_STORE_FILENAME).stat().st_mode)
            == 0o600
        )

    store.close()


def test_save_appends_immutable_revision_and_retains_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_profile(
        "Home",
        FavoritesProfileCompatibility.SDS200,
        _snapshot(),
    )

    second = store.save_revision(
        first.summary.profile_id,
        expected_revision=1,
        snapshot=_snapshot(name="Portable"),
        name="Travel",
        compatibility=FavoritesProfileCompatibility.SHARED,
    )

    assert second.summary.revision == 2
    assert second.summary.revision_count == 2
    assert second.summary.name == "Travel"
    assert second.summary.compatibility is FavoritesProfileCompatibility.SHARED
    assert second.summary.created_at_ns == 100
    assert second.summary.updated_at_ns == 200
    assert store.get_profile(first.summary.profile_id, revision=1) == first
    assert store.get_profile(first.summary.profile_id) == second
    assert store.list_profiles() == (second.summary,)
    store.close()


def test_identical_save_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.create_profile(
        "Home",
        FavoritesProfileCompatibility.SDS100,
        _snapshot(),
    )

    unchanged = store.save_revision(
        created.summary.profile_id,
        expected_revision=1,
        snapshot=created.snapshot,
    )

    assert unchanged == created
    assert store.list_profiles()[0].revision_count == 1
    store.close()


def test_stale_revision_is_rejected_without_mutation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.create_profile(
        "Home",
        FavoritesProfileCompatibility.SDS200,
        _snapshot(),
    )
    store.save_revision(
        created.summary.profile_id,
        expected_revision=1,
        snapshot=_snapshot(name="New"),
    )

    with pytest.raises(FavoritesProfileConflictError) as failure:
        store.save_revision(
            created.summary.profile_id,
            expected_revision=1,
            snapshot=_snapshot(name="Stale"),
        )

    assert failure.value.expected == 1
    assert failure.value.actual == 2
    assert store.get_profile(created.summary.profile_id).summary.revision == 2
    store.close()


def test_concurrent_same_revision_allows_exactly_one_update(tmp_path: Path) -> None:
    first_store = _store(tmp_path)
    created = first_store.create_profile(
        "Home",
        FavoritesProfileCompatibility.SHARED,
        _snapshot(),
    )
    second_store = FavoritesProfileStore(tmp_path / "profiles")

    def save(store: FavoritesProfileStore, name: str) -> int | str:
        try:
            return store.save_revision(
                created.summary.profile_id,
                expected_revision=1,
                snapshot=_snapshot(name=name),
            ).summary.revision
        except FavoritesProfileConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(
            executor.map(
                lambda arguments: save(*arguments),
                ((first_store, "First"), (second_store, "Second")),
            )
        )

    assert sorted(outcomes, key=str) == [2, "conflict"]
    assert first_store.get_profile(created.summary.profile_id).summary.revision == 2
    first_store.close()
    second_store.close()


def test_names_are_unique_without_case_or_whitespace_ambiguity(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        tokens=_tokens("profile_identifier_000001", "profile_identifier_000002"),
    )
    store.create_profile("Home", FavoritesProfileCompatibility.SDS200, _snapshot())

    with pytest.raises(FavoritesProfileNameError, match="already in use"):
        store.create_profile("HOME", FavoritesProfileCompatibility.SDS100, _snapshot())
    with pytest.raises(FavoritesProfileNameError, match="trimmed"):
        store.create_profile(" Home ", FavoritesProfileCompatibility.SDS100, _snapshot())

    assert len(store.list_profiles()) == 1
    store.close()


@pytest.mark.parametrize(
    ("first", "equivalent"),
    (
        ("Caf\u00e9", "Cafe\u0301"),
        ("Home", "\u210come"),
    ),
)
def test_names_are_unique_across_unicode_compatibility_forms(
    tmp_path: Path,
    first: str,
    equivalent: str,
) -> None:
    store = _store(
        tmp_path,
        tokens=_tokens("profile_identifier_000001", "profile_identifier_000002"),
    )
    store.create_profile(first, FavoritesProfileCompatibility.SHARED, _snapshot())

    with pytest.raises(FavoritesProfileNameError, match="already in use"):
        store.create_profile(
            equivalent,
            FavoritesProfileCompatibility.SHARED,
            _snapshot(),
        )

    assert [profile.name for profile in store.list_profiles()] == [first]
    store.close()


@pytest.mark.parametrize("control", ("\u0000", "\u007f", "\u202e"))
def test_profile_names_reject_control_and_format_characters(
    tmp_path: Path,
    control: str,
) -> None:
    store = _store(tmp_path)

    with pytest.raises(FavoritesProfileNameError, match="without controls"):
        store.create_profile(
            f"Home{control}Travel",
            FavoritesProfileCompatibility.SHARED,
            _snapshot(),
        )

    assert store.list_profiles() == ()
    store.close()


def test_profile_list_uses_stable_casefolded_name_order(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        tokens=_tokens(
            "profile_identifier_000001",
            "profile_identifier_000002",
            "profile_identifier_000003",
        ),
    )
    for name in ("Zulu", "alpha", "Bravo"):
        store.create_profile(name, FavoritesProfileCompatibility.SHARED, _snapshot(name=name))

    assert [profile.name for profile in store.list_profiles()] == ["alpha", "Bravo", "Zulu"]
    store.close()


def test_duplicate_document_filenames_are_rejected(tmp_path: Path) -> None:
    content = (_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes()
    duplicate = FavoritesStorageSnapshot(
        catalog_bytes=b"F-List\tHome\tf_000001.hpd\r\n",
        documents=(
            FavoritesStorageDocument(filename="f_000001.hpd", content=content),
            FavoritesStorageDocument(filename="f_000001.hpd", content=content),
        ),
    )
    store = _store(tmp_path)

    with pytest.raises(FavoritesProfileSnapshotError, match="unique"):
        store.create_profile("Home", FavoritesProfileCompatibility.SDS200, duplicate)

    assert store.list_profiles() == ()
    store.close()


def test_malformed_favorites_snapshot_is_rejected_without_publication(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(FavoritesProfileSnapshotError, match="valid Favorites workspace"):
        store.create_profile(
            "Home",
            FavoritesProfileCompatibility.SDS200,
            FavoritesStorageSnapshot(catalog_bytes=b"\xff", documents=()),
        )

    assert store.list_profiles() == ()
    store.close()


@pytest.mark.parametrize("limit_kind", ("documents", "bytes"))
def test_snapshot_shape_limits_fail_before_profile_publication(
    tmp_path: Path,
    limit_kind: str,
) -> None:
    content = (_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes()
    documents = (
        FavoritesStorageDocument(filename="f_000001.hpd", content=content),
        FavoritesStorageDocument(filename="f_000002.hpd", content=content),
    )
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=b"F-List\tHome\tf_000001.hpd\r\n",
        documents=documents if limit_kind == "documents" else documents[:1],
    )
    limits = FavoritesProfileStoreLimits(
        max_documents=1,
        max_snapshot_bytes=1 if limit_kind == "bytes" else 1024 * 1024,
    )
    store = _store(tmp_path, limits=limits)

    with pytest.raises(FavoritesProfileLimitError, match=limit_kind[:-1]):
        store.create_profile("Home", FavoritesProfileCompatibility.SDS200, snapshot)

    assert store.list_profiles() == ()
    store.close()


def test_store_limits_fail_without_partial_profile_or_revision(tmp_path: Path) -> None:
    limits = FavoritesProfileStoreLimits(
        max_profiles=1,
        max_revisions_per_profile=1,
        max_documents=1,
        max_snapshot_bytes=1024 * 1024,
        max_name_characters=8,
    )
    store = _store(
        tmp_path,
        limits=limits,
        tokens=_tokens("profile_identifier_000001", "profile_identifier_000002"),
    )
    created = store.create_profile(
        "Home",
        FavoritesProfileCompatibility.SDS200,
        _snapshot(),
    )

    with pytest.raises(FavoritesProfileLimitError, match="profile limit"):
        store.create_profile("Other", FavoritesProfileCompatibility.SDS100, _snapshot())
    with pytest.raises(FavoritesProfileLimitError, match="revision limit"):
        store.save_revision(
            created.summary.profile_id,
            expected_revision=1,
            snapshot=_snapshot(name="Changed"),
        )
    with pytest.raises(FavoritesProfileNameError, match="bounded"):
        store.save_revision(
            created.summary.profile_id,
            expected_revision=1,
            snapshot=created.snapshot,
            name="Much too long",
        )

    assert store.get_profile(created.summary.profile_id) == created
    assert store.list_profiles() == (created.summary,)
    store.close()


def test_unknown_profile_and_revision_share_unavailable_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.create_profile(
        "Home",
        FavoritesProfileCompatibility.SDS200,
        _snapshot(),
    )

    with pytest.raises(FavoritesProfileNotFoundError, match="unavailable"):
        store.get_profile("unknown_profile_identifier")
    with pytest.raises(FavoritesProfileNotFoundError, match="unavailable"):
        store.get_profile(created.summary.profile_id, revision=2)

    store.close()


def test_existing_non_private_directory_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    root.mkdir(mode=0o700)
    if os.name != "posix":
        pytest.skip("POSIX mode contract")
    root.chmod(0o755)

    with pytest.raises(FavoritesProfileStoreUnavailableError, match="0700"):
        FavoritesProfileStore(root)


def test_existing_non_private_database_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    database = store.database
    store.close()
    if os.name != "posix":
        pytest.skip("POSIX mode contract")
    database.chmod(0o644)

    with pytest.raises(FavoritesProfileStoreUnavailableError, match="0600"):
        FavoritesProfileStore(tmp_path / "profiles")


def test_unsupported_database_schema_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    database = store.database
    store.close()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(FavoritesProfileStoreUnavailableError, match="unsupported"):
        FavoritesProfileStore(tmp_path / "profiles")


def test_closed_store_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.close()

    with pytest.raises(FavoritesProfileStoreClosedError):
        store.list_profiles()
    with pytest.raises(FavoritesProfileStoreClosedError):
        store.create_profile(
            "Home",
            FavoritesProfileCompatibility.SDS200,
            _snapshot(),
        )
