from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    FavoritesCopiedTreeWriteExecutionResult,
    FavoritesCopiedTreeWriteExecutionStatus,
    FavoritesEditorError,
    FavoritesEditorSession,
    FavoritesEditorSourceKind,
    FavoritesNavigationPath,
    FavoritesRecordEditError,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWritePlan,
    open_favorites_copied_tree_editor,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


def _snapshot() -> FavoritesStorageSnapshot:
    return FavoritesStorageSnapshot(
        catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
            ),
        ),
    )


class _Storage:
    kind = FavoritesEditorSourceKind.COPIED_TREE

    def __init__(self, path: Path, snapshot: FavoritesStorageSnapshot) -> None:
        self.requested_path = path
        self._snapshot = snapshot
        self.executed_plan: FavoritesWritePlan | None = None

    @property
    def favorites_directory(self) -> Path:
        return self.requested_path

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return self._snapshot

    def execute(
        self,
        plan: FavoritesWritePlan,
    ) -> FavoritesCopiedTreeWriteExecutionResult:
        self.executed_plan = plan
        self._snapshot = plan.intended_snapshot
        operation = "a" * 64
        return FavoritesCopiedTreeWriteExecutionResult(
            status=FavoritesCopiedTreeWriteExecutionStatus.COMPLETED,
            target_directory=self.requested_path,
            operation_id=operation,
            backup_directory=self.requested_path.parent / "backup",
            staging_directory=self.requested_path.parent / "staging",
            displaced_directory=self.requested_path.parent / "displaced",
            rollback_manifest_path=self.requested_path.parent / "rollback.json",
            operation_report_path=self.requested_path.parent / "report.json",
        )


def _session(tmp_path: Path) -> tuple[FavoritesEditorSession, _Storage]:
    storage = _Storage(tmp_path.resolve(), _snapshot())
    return FavoritesEditorSession.open(storage), storage


def test_session_opens_one_immutable_baseline(tmp_path: Path) -> None:
    session, _storage = _session(tmp_path)

    assert session.baseline_snapshot is session.intended_snapshot
    assert not session.has_changes
    assert session.undo_count == 0
    assert session.navigation.roots[0].name == "Synthetic Favorites"
    assert session.validation.is_valid

    with pytest.raises(FrozenInstanceError):
        session.baseline_snapshot.catalog_bytes = b"changed"  # type: ignore[misc]


def test_search_and_raw_detail_are_read_only(tmp_path: Path) -> None:
    session, _storage = _session(tmp_path)

    matches = session.nodes("synthetic channel")

    assert len(matches) == 1
    assert matches[0].path == FavoritesNavigationPath((2, 2, 4, 5))
    assert session.raw_record(matches[0].path).startswith(b"C-Freq\t")
    assert not session.has_changes


def test_supported_rename_undo_and_reset_are_in_memory(tmp_path: Path) -> None:
    session, storage = _session(tmp_path)
    path = FavoritesNavigationPath((2, 2, 4, 5))

    session.rename(path, "Renamed Channel")

    assert session.node(path).name == "Renamed Channel"
    assert session.has_changes
    assert session.undo_count == 1
    assert storage.executed_plan is None

    assert session.undo()
    assert session.node(path).name == "Synthetic Channel"
    assert not session.has_changes

    session.rename(path, "Again")
    assert session.reset()
    assert session.node(path).name == "Synthetic Channel"
    assert not session.has_changes
    assert session.undo_count == 0


def test_supported_leaf_duplicate_and_delete_preserve_exact_template(
    tmp_path: Path,
) -> None:
    session, _storage = _session(tmp_path)
    original = FavoritesNavigationPath((2, 2, 4, 5))
    original_raw = session.raw_record(original)

    session.duplicate(original, name="Copied Channel")

    copied = FavoritesNavigationPath((2, 2, 4, 6))
    assert session.node(copied).name == "Copied Channel"
    assert session.raw_record(copied).replace(
        b"Copied Channel", b"Synthetic Channel"
    ) == original_raw

    session.delete(copied)
    assert session.node(original).name == "Synthetic Channel"
    with pytest.raises(FavoritesEditorError, match="not present"):
        session.node(copied)


def test_raw_record_browser_exposes_supported_non_navigation_leafs(
    tmp_path: Path,
) -> None:
    session, _storage = _session(tmp_path)
    reference = next(
        item
        for item in session.records()
        if session.record_target(item).record.command == "T-Freq"
    )
    original = session.raw_source_record(reference)

    session.duplicate_record(reference)

    created = next(
        item
        for item in session.records()
        if item.document_index == reference.document_index
        and item.source_index == reference.source_index + 1
    )
    assert session.raw_source_record(created) == original

    session.delete_record(created)
    assert session.raw_source_record(reference) == original


def test_raw_record_browser_reads_orphan_diagnostics_without_enabling_edits(
    tmp_path: Path,
) -> None:
    snapshot = FavoritesStorageSnapshot(
        catalog_bytes=b"TargetModel\tBCDx36HP\r\nFormatVersion\t1.00\r\n",
        documents=(
            FavoritesStorageDocument(
                filename="orphan.hpd",
                content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
            ),
        ),
    )
    session = FavoritesEditorSession.open(_Storage(tmp_path.resolve(), snapshot))
    reference = next(item for item in session.records() if item.record.command == "T-Freq")

    assert session.raw_source_record(reference).startswith(b"T-Freq\t")
    with pytest.raises(FavoritesRecordEditError, match="not bound"):
        session.delete_record(reference)


def test_raw_record_mutation_rejects_reference_shifted_by_prior_edit(
    tmp_path: Path,
) -> None:
    session, _storage = _session(tmp_path)
    frequencies = tuple(
        item for item in session.records() if item.record.command == "T-Freq"
    )

    session.duplicate_record(frequencies[0])

    with pytest.raises(FavoritesEditorError, match="stale"):
        session.delete_record(frequencies[1])


def test_review_token_is_invalidated_by_any_later_edit(tmp_path: Path) -> None:
    session, _storage = _session(tmp_path)
    path = FavoritesNavigationPath((2, 2, 4, 5))
    session.rename(path, "First")
    review = session.review()

    session.rename(path, "Second")

    with pytest.raises(FavoritesEditorError, match="stale"):
        session.execute(review.confirmation_token)


def test_execute_requires_review_token_and_verifies_fresh_reload(
    tmp_path: Path,
) -> None:
    session, storage = _session(tmp_path)
    path = FavoritesNavigationPath((2, 2, 4, 5))
    session.rename(path, "Committed")
    review = session.review()

    execution = session.execute(review.confirmation_token)

    assert storage.executed_plan == review.plan
    assert execution.result.operation_id == "a" * 64
    assert execution.reloaded_snapshot == review.plan.intended_snapshot
    assert session.baseline_snapshot == review.plan.intended_snapshot
    assert session.intended_snapshot == review.plan.intended_snapshot
    assert not session.has_changes
    assert session.undo_count == 0


def test_execute_rejects_reload_mismatch(tmp_path: Path) -> None:
    session, storage = _session(tmp_path)
    path = FavoritesNavigationPath((2, 2, 4, 5))
    session.rename(path, "Committed")
    review = session.review()
    original_execute = storage.execute

    def execute_without_reload(
        plan: FavoritesWritePlan,
    ) -> FavoritesCopiedTreeWriteExecutionResult:
        result = original_execute(plan)
        storage._snapshot = review.plan.baseline_snapshot
        return result

    storage.execute = execute_without_reload  # type: ignore[method-assign]

    with pytest.raises(FavoritesEditorError, match="fresh reload"):
        session.execute(review.confirmation_token)


def test_review_rejects_noop(tmp_path: Path) -> None:
    session, _storage = _session(tmp_path)

    with pytest.raises(FavoritesEditorError, match="no changes"):
        session.review()


def test_concrete_copied_tree_session_uses_verified_executor_and_reloads(
    tmp_path: Path,
) -> None:
    favorites = tmp_path / "favorites_lists"
    favorites.mkdir()
    (favorites / "f_list.cfg").write_bytes(
        (_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes()
    )
    (favorites / "f_000001.hpd").write_bytes(
        (_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes()
    )
    session = open_favorites_copied_tree_editor(favorites)
    path = FavoritesNavigationPath((2, 2, 4, 5))
    session.rename(path, "Durable Rename")
    review = session.review()

    execution = session.execute(review.confirmation_token)

    assert execution.result.status is FavoritesCopiedTreeWriteExecutionStatus.COMPLETED
    assert execution.result.operation_id is not None
    assert execution.result.backup_directory is not None
    assert execution.result.backup_directory.is_dir()
    assert execution.result.rollback_manifest_path is not None
    assert execution.result.rollback_manifest_path.is_file()
    assert execution.result.operation_report_path is not None
    assert execution.result.operation_report_path.is_file()
    assert session.node(path).name == "Durable Rename"
    assert not session.has_changes
