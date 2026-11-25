from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sds200.favorites_editor import FavoritesEditorError
from sds200.favorites_navigation import FavoritesNavigationKind
from sds200.favorites_web_presentation import (
    present_favorites_web_nodes,
    present_favorites_web_review,
    present_favorites_web_summary,
)
from sds200.favorites_web_workspace import (
    FavoritesWebWorkspaceConflictError,
    FavoritesWebWorkspaceError,
    FavoritesWebWorkspaceLimitError,
    FavoritesWebWorkspaceLimits,
    FavoritesWebWorkspaceNotFoundError,
    FavoritesWebWorkspaces,
    FavoritesWebWorkspaceSource,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"
_OWNER = b"operator-session-owner-a" * 2
_OTHER_OWNER = b"operator-session-owner-b" * 2


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Tokens:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"workspace_{self.value:032d}"


def _favorites_directory(tmp_path: Path, name: str = "source") -> Path:
    directory = tmp_path / name / "BCDx36HP" / "favorites_lists"
    directory.mkdir(parents=True)
    (directory / "f_list.cfg").write_bytes((_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes())
    (directory / "f_000001.hpd").write_bytes(
        (_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes()
    )
    return directory.resolve()


def _manager(
    tmp_path: Path,
    *,
    limits: FavoritesWebWorkspaceLimits | None = None,
    clock: _Clock | None = None,
    token_factory: _Tokens | None = None,
) -> FavoritesWebWorkspaces:
    return FavoritesWebWorkspaces(
        (FavoritesWebWorkspaceSource("primary", _favorites_directory(tmp_path)),),
        limits=limits,
        clock=clock or _Clock(),
        token_factory=token_factory or _Tokens(),
    )


def _node_id(
    manager: FavoritesWebWorkspaces,
    workspace_id: str,
    *,
    revision: int,
    text: str = "Synthetic Channel",
) -> str:
    return manager.nodes(
        _OWNER,
        workspace_id,
        expected_revision=revision,
        text=text,
    )[0].node_id


def test_source_and_limits_reject_ambiguous_configuration(tmp_path: Path) -> None:
    absolute = _favorites_directory(tmp_path)

    with pytest.raises(ValueError, match="label"):
        FavoritesWebWorkspaceSource("../private", absolute)
    with pytest.raises(ValueError, match="absolute"):
        FavoritesWebWorkspaceSource("primary", Path("relative"))
    with pytest.raises(ValueError, match="limits"):
        FavoritesWebWorkspaceLimits(max_workspaces=1, max_workspaces_per_owner=2)
    with pytest.raises(ValueError, match="limits"):
        FavoritesWebWorkspaceLimits(idle_seconds=2, absolute_seconds=1)
    with pytest.raises(ValueError, match="unique"):
        FavoritesWebWorkspaces(
            (
                FavoritesWebWorkspaceSource("primary", absolute),
                FavoritesWebWorkspaceSource("primary", absolute),
            )
        )


def test_create_exposes_only_bounded_path_free_summary(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    summary = manager.create(_OWNER, "primary")

    assert manager.source_labels == ("primary",)
    assert summary.source_label == "primary"
    assert summary.revision == 0
    assert not summary.dirty
    assert summary.undo_count == 0
    assert summary.mutation_count == 0
    assert summary.document_count == 1
    assert summary.record_count > 0
    assert summary.snapshot_bytes > 0
    assert summary.error_count == 0
    assert str(tmp_path) not in repr(summary)
    assert str(tmp_path) not in repr(manager._sources["primary"])  # noqa: SLF001


def test_unknown_source_identifier_and_owner_are_indistinguishable(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.create(_OWNER, "missing")
    summary = manager.create(_OWNER, "primary")
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.summary(_OTHER_OWNER, summary.workspace_id)
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.summary(_OWNER, "not a workspace id")


def test_configured_source_failure_is_path_free_and_never_published(tmp_path: Path) -> None:
    missing = (tmp_path / "private" / "missing-favorites").resolve()
    manager = FavoritesWebWorkspaces((FavoritesWebWorkspaceSource("primary", missing),))

    with pytest.raises(FavoritesWebWorkspaceNotFoundError) as unavailable:
        manager.create(_OWNER, "primary")
    with pytest.raises(FavoritesWebWorkspaceNotFoundError) as unknown:
        manager.create(_OWNER, "unknown")

    assert str(unavailable.value) == str(unknown.value)
    assert str(missing) not in str(unavailable.value)
    assert str(missing) not in repr(unavailable.value)
    assert manager._workspaces == {}  # noqa: SLF001


def test_rename_is_revisioned_and_stale_request_cannot_mutate(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")
    channel_id = _node_id(manager, opened.workspace_id, revision=0)

    renamed = manager.rename(
        _OWNER,
        opened.workspace_id,
        expected_revision=0,
        node_id=channel_id,
        name="Web Renamed",
    )

    assert renamed.revision == 1
    assert renamed.dirty
    assert renamed.undo_count == 1
    assert renamed.mutation_count == 1
    renamed_node = manager.nodes(
        _OWNER,
        opened.workspace_id,
        expected_revision=1,
        text="Web Renamed",
    )[0]
    assert renamed_node.kind is FavoritesNavigationKind.CONVENTIONAL_CHANNEL
    assert renamed_node.can_rename
    assert renamed_node.can_duplicate
    assert renamed_node.can_delete
    assert renamed_node.node_id != channel_id
    assert "indexes" not in repr(renamed_node)
    with pytest.raises(FavoritesWebWorkspaceConflictError) as conflict:
        manager.rename(
            _OWNER,
            opened.workspace_id,
            expected_revision=0,
            node_id=channel_id,
            name="Stale",
        )
    assert (conflict.value.expected, conflict.value.actual) == (0, 1)
    assert manager.nodes(_OWNER, opened.workspace_id, expected_revision=1, text="Web Renamed")


def test_nodes_expose_only_opaque_revision_bound_references(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")

    nodes = manager.nodes(_OWNER, opened.workspace_id, expected_revision=0)
    channel = next(node for node in nodes if node.name == "Synthetic Channel")
    department = next(node for node in nodes if node.name == "Synthetic Department")

    assert channel.parent_node_id == department.node_id
    assert channel.child_count == 0
    assert channel.can_rename and channel.can_duplicate and channel.can_delete
    assert not department.can_duplicate and not department.can_delete
    assert all("FavoritesNavigationPath" not in repr(node) for node in nodes)
    assert all("source=" not in repr(node) for node in nodes)
    assert all("filename" not in repr(node) for node in nodes)


def test_node_reference_is_invalidated_after_revision_change(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")
    channel_id = _node_id(manager, opened.workspace_id, revision=0)

    manager.rename(
        _OWNER,
        opened.workspace_id,
        expected_revision=0,
        node_id=channel_id,
        name="Current Revision",
    )

    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.rename(
            _OWNER,
            opened.workspace_id,
            expected_revision=1,
            node_id=channel_id,
            name="Stale Reference",
        )
    assert manager.summary(_OWNER, opened.workspace_id).revision == 1


def test_node_identifier_collision_fails_closed(tmp_path: Path) -> None:
    manager = FavoritesWebWorkspaces(
        (FavoritesWebWorkspaceSource("primary", _favorites_directory(tmp_path)),),
        clock=_Clock(),
        token_factory=_Tokens(),
        node_token_factory=lambda: "fixed_node_identifier_123456",
    )
    opened = manager.create(_OWNER, "primary")

    with pytest.raises(FavoritesWebWorkspaceLimitError, match="node identifier"):
        manager.nodes(_OWNER, opened.workspace_id, expected_revision=0)
    assert manager.summary(_OWNER, opened.workspace_id) == opened


def test_duplicate_delete_undo_and_reset_preserve_revision_rules(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")
    channel_id = _node_id(manager, opened.workspace_id, revision=0)

    duplicated = manager.duplicate(
        _OWNER,
        opened.workspace_id,
        expected_revision=0,
        node_id=channel_id,
        name="Web Copy",
    )
    assert duplicated.record_count == opened.record_count + 1
    copied_id = _node_id(
        manager,
        opened.workspace_id,
        revision=1,
        text="Web Copy",
    )
    deleted = manager.delete(
        _OWNER,
        opened.workspace_id,
        expected_revision=1,
        node_id=copied_id,
    )
    assert deleted.revision == 2
    assert deleted.record_count == opened.record_count

    undone = manager.undo(_OWNER, opened.workspace_id, expected_revision=2)
    assert undone.revision == 3
    assert undone.record_count == opened.record_count + 1
    reset = manager.reset(_OWNER, opened.workspace_id, expected_revision=3)
    assert reset.revision == 4
    assert not reset.dirty
    assert reset.undo_count == 0
    assert reset.record_count == opened.record_count

    noop_reset = manager.reset(_OWNER, opened.workspace_id, expected_revision=4)
    noop_undo = manager.undo(_OWNER, opened.workspace_id, expected_revision=4)
    assert noop_reset.revision == noop_undo.revision == 4
    assert noop_reset.mutation_count == noop_undo.mutation_count == 4


def test_review_is_exact_and_never_executes_source(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")

    with pytest.raises(FavoritesEditorError, match="no changes"):
        manager.review(_OWNER, opened.workspace_id, expected_revision=0)
    channel_id = _node_id(manager, opened.workspace_id, revision=0)
    manager.rename(
        _OWNER,
        opened.workspace_id,
        expected_revision=0,
        node_id=channel_id,
        name="Review Only",
    )
    review = manager.review(_OWNER, opened.workspace_id, expected_revision=1)

    assert review.workspace_id == opened.workspace_id
    assert review.revision == 1
    assert review.changed_source_count == 1
    assert review.ambiguity_count == 0
    assert not review.blocked
    assert review.blockers == ()
    assert sum(change.count for change in review.changes) == 1
    assert len(review.confirmation_token) == 64
    assert "confirmation_token" not in repr(review)
    assert "FavoritesWritePlan" not in repr(review)
    assert manager.summary(_OWNER, opened.workspace_id).revision == 1


def test_explicit_presenters_emit_only_json_safe_reviewed_fields(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")
    nodes = manager.nodes(_OWNER, opened.workspace_id, expected_revision=0)
    channel_id = next(node.node_id for node in nodes if node.name == "Synthetic Channel")
    manager.rename(
        _OWNER,
        opened.workspace_id,
        expected_revision=0,
        node_id=channel_id,
        name="Presented Channel",
    )
    current = manager.summary(_OWNER, opened.workspace_id, expected_revision=1)
    current_nodes = manager.nodes(
        _OWNER,
        opened.workspace_id,
        expected_revision=1,
    )
    reviewed = manager.review(_OWNER, opened.workspace_id, expected_revision=1)

    documents = (
        present_favorites_web_summary(current),
        present_favorites_web_nodes(current_nodes),
        present_favorites_web_review(reviewed),
    )
    encoded = json.dumps(documents, sort_keys=True)

    assert reviewed.confirmation_token in encoded
    assert str(tmp_path) not in encoded
    assert "FavoritesWritePlan" not in encoded
    assert "FavoritesNavigationPath" not in encoded
    assert "raw_bytes" not in encoded
    assert "filename" not in encoded
    assert "document_index" not in encoded
    assert "source_index" not in encoded
    assert documents[2]["changes"] == [{"count": 1, "kind": "replaced"}]


def test_capacity_limits_never_evict_an_active_workspace(tmp_path: Path) -> None:
    limits = FavoritesWebWorkspaceLimits(
        max_workspaces=2,
        max_workspaces_per_owner=1,
    )
    manager = _manager(tmp_path, limits=limits)
    first = manager.create(_OWNER, "primary")

    with pytest.raises(FavoritesWebWorkspaceLimitError, match="owner capacity"):
        manager.create(_OWNER, "primary")
    second = manager.create(_OTHER_OWNER, "primary")
    with pytest.raises(FavoritesWebWorkspaceLimitError, match="capacity is full"):
        manager.create(b"third-owner-secret" * 2, "primary")
    assert manager.summary(_OWNER, first.workspace_id).workspace_id == first.workspace_id
    assert manager.summary(_OTHER_OWNER, second.workspace_id).workspace_id == second.workspace_id


def test_wrong_owner_does_not_refresh_idle_expiry(tmp_path: Path) -> None:
    clock = _Clock()
    manager = _manager(
        tmp_path,
        limits=FavoritesWebWorkspaceLimits(idle_seconds=10, absolute_seconds=100),
        clock=clock,
    )
    opened = manager.create(_OWNER, "primary")

    clock.advance(9)
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.summary(_OTHER_OWNER, opened.workspace_id)
    clock.advance(2)
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.summary(_OWNER, opened.workspace_id)


def test_authorized_reads_refresh_idle_but_not_absolute_expiry(tmp_path: Path) -> None:
    clock = _Clock()
    manager = _manager(
        tmp_path,
        limits=FavoritesWebWorkspaceLimits(idle_seconds=10, absolute_seconds=20),
        clock=clock,
    )
    opened = manager.create(_OWNER, "primary")

    clock.advance(9)
    refreshed = manager.summary(_OWNER, opened.workspace_id)
    assert refreshed.idle_expires_at == 19
    clock.advance(9)
    assert manager.summary(_OWNER, opened.workspace_id).absolute_expires_at == 20
    clock.advance(3)
    with pytest.raises(FavoritesWebWorkspaceNotFoundError):
        manager.summary(_OWNER, opened.workspace_id)


def test_discard_owner_and_terminal_close_erase_process_state(tmp_path: Path) -> None:
    limits = FavoritesWebWorkspaceLimits(max_workspaces_per_owner=2)
    manager = _manager(tmp_path, limits=limits)
    first = manager.create(_OWNER, "primary")
    second = manager.create(_OWNER, "primary")
    other = manager.create(_OTHER_OWNER, "primary")

    assert manager.discard_owner(_OWNER) == 2
    for workspace in (first, second):
        with pytest.raises(FavoritesWebWorkspaceNotFoundError):
            manager.summary(_OWNER, workspace.workspace_id)
    assert manager.summary(_OTHER_OWNER, other.workspace_id)

    manager.close()
    with pytest.raises(FavoritesWebWorkspaceError, match="closed"):
        manager.summary(_OTHER_OWNER, other.workspace_id)
    manager.close()


def test_mutation_limit_fails_before_changing_editor_state(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        limits=FavoritesWebWorkspaceLimits(max_mutations=1),
    )
    opened = manager.create(_OWNER, "primary")
    channel_id = _node_id(manager, opened.workspace_id, revision=0)
    renamed = manager.rename(
        _OWNER,
        opened.workspace_id,
        expected_revision=0,
        node_id=channel_id,
        name="One Change",
    )

    with pytest.raises(FavoritesWebWorkspaceLimitError, match="mutation limit"):
        manager.reset(_OWNER, opened.workspace_id, expected_revision=1)
    unchanged = manager.summary(_OWNER, opened.workspace_id)
    assert unchanged == renamed
    assert manager.nodes(_OWNER, opened.workspace_id, expected_revision=1, text="One Change")


@pytest.mark.parametrize(
    ("limits", "detail"),
    (
        (FavoritesWebWorkspaceLimits(max_documents=1, max_snapshot_bytes=1), "byte limit"),
        (FavoritesWebWorkspaceLimits(max_records=1), "record limit"),
    ),
)
def test_source_shape_limits_fail_before_workspace_publication(
    tmp_path: Path,
    limits: FavoritesWebWorkspaceLimits,
    detail: str,
) -> None:
    manager = _manager(tmp_path, limits=limits)

    with pytest.raises(FavoritesWebWorkspaceLimitError, match=detail):
        manager.create(_OWNER, "primary")


def test_duplicate_cannot_cross_record_limit(tmp_path: Path) -> None:
    probe = _manager(tmp_path / "probe")
    record_count = probe.create(_OWNER, "primary").record_count
    manager = _manager(
        tmp_path / "bounded",
        limits=FavoritesWebWorkspaceLimits(max_records=record_count),
    )
    opened = manager.create(_OWNER, "primary")
    channel_id = _node_id(manager, opened.workspace_id, revision=0)

    with pytest.raises(FavoritesWebWorkspaceLimitError, match="record limit"):
        manager.duplicate(
            _OWNER,
            opened.workspace_id,
            expected_revision=0,
            node_id=channel_id,
        )
    assert manager.summary(_OWNER, opened.workspace_id) == opened


def test_identifier_collision_fails_closed_without_evicting_first(tmp_path: Path) -> None:
    token = "fixed_workspace_identifier_1234567890"
    manager = FavoritesWebWorkspaces(
        (FavoritesWebWorkspaceSource("primary", _favorites_directory(tmp_path)),),
        token_factory=lambda: token,
    )
    first = manager.create(_OWNER, "primary")

    with pytest.raises(FavoritesWebWorkspaceLimitError, match="identifier generation"):
        manager.create(_OTHER_OWNER, "primary")
    retained = manager.summary(_OWNER, first.workspace_id)
    assert retained.workspace_id == first.workspace_id
    assert retained.revision == first.revision == 0
    assert retained.mutation_count == first.mutation_count == 0


def test_concurrent_same_revision_allows_exactly_one_mutation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")
    channel_id = _node_id(manager, opened.workspace_id, revision=0)

    def rename(name: str) -> object:
        try:
            return manager.rename(
                _OWNER,
                opened.workspace_id,
                expected_revision=0,
                node_id=channel_id,
                name=name,
            )
        except FavoritesWebWorkspaceConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(rename, ("Concurrent A", "Concurrent B")))

    assert sum(isinstance(result, FavoritesWebWorkspaceConflictError) for result in results) == 1
    assert manager.summary(_OWNER, opened.workspace_id).revision == 1


def test_owner_and_revision_inputs_fail_before_lookup(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    opened = manager.create(_OWNER, "primary")

    with pytest.raises(TypeError, match="bytes"):
        manager.summary("not-bytes", opened.workspace_id)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="owner secret"):
        manager.summary(b"short", opened.workspace_id)
    with pytest.raises(ValueError, match="revision"):
        manager.summary(_OWNER, opened.workspace_id, expected_revision=-1)
