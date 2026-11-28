from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sds200.favorites_profile_store import (
    FavoritesProfileCompatibility,
    FavoritesProfileConflictError,
    FavoritesProfileStore,
)
from sds200.favorites_profile_workspace import FavoritesProfileWorkspacePreviews
from sds200.favorites_storage import FavoritesStorageDocument, FavoritesStorageSnapshot
from sds200.favorites_web_workspace import (
    FavoritesWebWorkspaceLimitError,
    FavoritesWebWorkspaceLimits,
    FavoritesWebWorkspaceNotFoundError,
    FavoritesWebWorkspaces,
    FavoritesWebWorkspaceSource,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"
_OWNER = b"operator-profile-preview-owner-a" * 2
_OTHER_OWNER = b"operator-profile-preview-owner-b" * 2


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Tokens:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}_{self.value:032d}"


def _profile_tokens() -> Iterator[str]:
    yield "profile_identifier_000001"


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


def _store(tmp_path: Path) -> tuple[FavoritesProfileStore, str]:
    values = _profile_tokens()
    store = FavoritesProfileStore(
        tmp_path / "profiles",
        clock=iter((100, 200, 300)).__next__,
        token_factory=values.__next__,
    )
    profile = store.create_profile(
        "Home",
        FavoritesProfileCompatibility.SHARED,
        _snapshot(),
    )
    return store, profile.summary.profile_id


def _previews(
    store: FavoritesProfileStore,
    *,
    clock: _Clock | None = None,
    limits: FavoritesWebWorkspaceLimits | None = None,
) -> FavoritesProfileWorkspacePreviews:
    return FavoritesProfileWorkspacePreviews(
        store,
        clock=clock or _Clock(),
        limits=limits,
        token_factory=_Tokens("workspace"),
        node_token_factory=_Tokens("node"),
    )


def test_exact_profile_revision_opens_path_free_read_only_navigation(tmp_path: Path) -> None:
    store, profile_id = _store(tmp_path)
    previews = _previews(store)

    opened = previews.open(_OWNER, profile_id, expected_profile_revision=1)
    nodes = previews.nodes(_OWNER, opened.workspace_id, expected_revision=0)
    channel = next(node for node in nodes if node.name == "Synthetic Channel")

    assert opened.source_label == profile_id
    assert opened.source_revision == 1
    assert opened.read_only
    assert opened.revision == 0
    assert not opened.dirty
    assert opened.document_count == 1
    assert opened.record_count > 0
    assert all(not node.can_rename for node in nodes)
    assert all(not node.can_duplicate for node in nodes)
    assert all(not node.can_delete for node in nodes)
    assert str(tmp_path) not in repr(opened)
    assert "f_000001.hpd" not in repr(opened)
    assert b"Synthetic Favorites" not in repr(opened).encode()
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        previews._workspaces.rename(  # noqa: SLF001
            _OWNER,
            opened.workspace_id,
            expected_revision=0,
            node_id=channel.node_id,
            name="Must Not Mutate",
        )

    previews.close()
    assert store.get_profile(profile_id).summary.revision == 1
    store.close()


def test_read_only_snapshot_creation_requires_explicit_server_policy(tmp_path: Path) -> None:
    manager = FavoritesWebWorkspaces(
        (FavoritesWebWorkspaceSource("unused", (tmp_path / "unused").resolve()),),
        clock=_Clock(),
        token_factory=_Tokens("workspace"),
    )

    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.create_read_only_snapshot(
            _OWNER,
            "profile_identifier_000001",
            source_revision=1,
            snapshot=_snapshot(),
        )

    manager.close()


def test_invalid_trusted_snapshot_failure_is_generic_and_not_published() -> None:
    manager = FavoritesWebWorkspaces(
        (),
        allow_read_only_snapshots=True,
        clock=_Clock(),
        token_factory=_Tokens("workspace"),
    )

    with pytest.raises(FavoritesWebWorkspaceNotFoundError) as failure:
        manager.create_read_only_snapshot(
            _OWNER,
            "profile_identifier_000001",
            source_revision=1,
            snapshot=FavoritesStorageSnapshot(catalog_bytes=b"\xff", documents=()),
        )

    assert "\\xff" not in str(failure.value)
    assert manager._workspaces == {}  # noqa: SLF001
    manager.close()


def test_stale_profile_revision_is_rejected_before_workspace_creation(tmp_path: Path) -> None:
    store, profile_id = _store(tmp_path)
    store.save_revision(
        profile_id,
        expected_revision=1,
        snapshot=_snapshot(name="Updated"),
    )
    previews = _previews(store)

    with pytest.raises(FavoritesProfileConflictError) as failure:
        previews.open(_OWNER, profile_id, expected_profile_revision=1)

    assert (failure.value.expected, failure.value.actual) == (1, 2)
    assert previews._workspaces._workspaces == {}  # noqa: SLF001
    previews.close()
    store.close()


def test_owner_isolation_and_logout_discard_preview(tmp_path: Path) -> None:
    store, profile_id = _store(tmp_path)
    previews = _previews(store)
    opened = previews.open(_OWNER, profile_id, expected_profile_revision=1)

    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        previews.summary(_OTHER_OWNER, opened.workspace_id)
    assert previews.discard_owner(_OTHER_OWNER) == 0
    assert previews.discard_owner(_OWNER) == 1
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        previews.summary(_OWNER, opened.workspace_id)

    previews.close()
    store.close()


def test_expiry_releases_capacity_without_evicting_active_preview(tmp_path: Path) -> None:
    store, profile_id = _store(tmp_path)
    clock = _Clock()
    previews = _previews(
        store,
        clock=clock,
        limits=FavoritesWebWorkspaceLimits(
            max_workspaces=1,
            max_workspaces_per_owner=1,
            idle_seconds=1,
            absolute_seconds=2,
        ),
    )
    first = previews.open(_OWNER, profile_id, expected_profile_revision=1)

    with pytest.raises(FavoritesWebWorkspaceLimitError, match="capacity is full"):
        previews.open(_OTHER_OWNER, profile_id, expected_profile_revision=1)
    clock.advance(1)
    second = previews.open(_OTHER_OWNER, profile_id, expected_profile_revision=1)

    assert second.workspace_id != first.workspace_id
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        previews.summary(_OWNER, first.workspace_id)
    previews.close()
    store.close()


def test_preview_adapter_exposes_no_mutation_or_review_surface(tmp_path: Path) -> None:
    store, _profile_id = _store(tmp_path)
    previews = _previews(store)

    for name in ("rename", "duplicate", "delete", "undo", "reset", "review"):
        assert not hasattr(previews, name)

    previews.close()
    store.close()
