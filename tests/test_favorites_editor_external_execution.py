from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from sds200 import favorites_editor_external_execution as external_execution
from sds200.favorites_editor import (
    FavoritesCopiedTreeEditorStorage,
    FavoritesEditorSession,
    FavoritesEditorSourceKind,
    FavoritesEditorWriteResult,
)
from sds200.favorites_editor_external_execution import (
    FavoritesEditorExternalExecutionError,
    FavoritesEditorExternalExecutionStatus,
    FavoritesEditorExternalProvenanceState,
    FavoritesEditorExternalRecoveryStatus,
    execute_favorites_editor_external_plan_durably,
    review_favorites_editor_external_execution,
)
from sds200.favorites_editor_external_planning import (
    FavoritesEditorExternalFieldChoice,
    FavoritesEditorExternalFieldDecision,
    plan_favorites_editor_external_decisions,
)
from sds200.favorites_editor_external_preview import (
    FavoritesEditorExternalPreviewController,
    FavoritesEditorExternalPreviewError,
    FavoritesEditorExternalRefreshOwner,
)
from sds200.favorites_external_assisted_sync import (
    FavoritesExternalAssistedSynchronizationService,
    RadioReferenceFavoritesMappedField,
)
from sds200.favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleState,
)
from sds200.favorites_external_provenance_storage import (
    load_favorites_external_provenance,
    save_favorites_external_provenance_if_current,
)
from sds200.favorites_storage import FavoritesStorageSnapshot
from sds200.favorites_write_plan import FavoritesWritePlan, plan_favorites_write
from tests.test_favorites_editor_external_planning import _field_context


class _Storage:
    def __init__(
        self,
        value: FavoritesStorageSnapshot,
        requested_path: Path,
        *,
        fail_after_mutation: bool = False,
        fail_recovery: bool = False,
        execution_entered: threading.Event | None = None,
        execution_release: threading.Event | None = None,
        kind: FavoritesEditorSourceKind = FavoritesEditorSourceKind.COPIED_TREE,
    ) -> None:
        self.value = value
        self.requested_path = requested_path
        self.fail_after_mutation = fail_after_mutation
        self.fail_recovery = fail_recovery
        self.execution_entered = execution_entered
        self.execution_release = execution_release
        self._kind = kind
        self.execution_plans: list[FavoritesWritePlan] = []

    @property
    def kind(self) -> FavoritesEditorSourceKind:
        return self._kind

    @property
    def favorites_directory(self) -> Path:
        return self.requested_path

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return self.value

    def execute(self, plan: FavoritesWritePlan) -> FavoritesEditorWriteResult:
        self.execution_plans.append(plan)
        if len(self.execution_plans) == 1 and self.execution_entered is not None:
            self.execution_entered.set()
            assert self.execution_release is not None
            assert self.execution_release.wait(timeout=5)
        self.value = plan.intended_snapshot
        if len(self.execution_plans) == 1 and self.fail_after_mutation:
            raise RuntimeError("injected primary failure")
        if len(self.execution_plans) > 1 and self.fail_recovery:
            raise RuntimeError("injected recovery failure")
        return cast(FavoritesEditorWriteResult, object())


def _favorites_plan(tmp_path: Path) -> tuple[Any, Any, _Storage, Any]:
    context, source_storage, path, source = _field_context(tmp_path)
    selected = context.result.preview.records[0]
    plan = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.NAME,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.FREQUENCY,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
        ),
    )
    storage = _Storage(source_storage.value, tmp_path / "copied-tree")
    return plan, path, storage, source


class _OwnerFactory:
    def __init__(self, storage: _Storage, path: Path, source: Any) -> None:
        self.storage = storage
        self.path = path
        self.source = source
        self.owners: list[FavoritesEditorExternalRefreshOwner] = []

    def __call__(self) -> FavoritesEditorExternalRefreshOwner:
        lifecycle = FavoritesExternalProvenanceLifecycle(self.storage, self.path)
        lifecycle.start()
        service = FavoritesExternalAssistedSynchronizationService(
            lifecycle,
            self.source,
        )
        owner = FavoritesEditorExternalRefreshOwner(
            service,
            lifecycle,
            self.source.values[0][0].identity.source,
        )
        self.owners.append(owner)
        return owner


def _controller_plan(
    tmp_path: Path,
    *,
    execution_entered: threading.Event | None = None,
    execution_release: threading.Event | None = None,
) -> tuple[
    FavoritesEditorSession,
    FavoritesEditorExternalPreviewController,
    Any,
    _Storage,
    _OwnerFactory,
]:
    original_plan, path, original_storage, original_source = _favorites_plan(tmp_path)
    observation = original_plan.refresh_result.observations[0]
    source = type(original_source)((observation,))
    storage = _Storage(
        original_storage.value,
        original_storage.requested_path,
        execution_entered=execution_entered,
        execution_release=execution_release,
    )
    session = FavoritesEditorSession.open(storage)
    factory = _OwnerFactory(storage, path, source)
    controller = FavoritesEditorExternalPreviewController(session, factory)
    controller.refresh()
    context = controller.planning_context()
    assert context is not None
    selected = context.result.preview.records[0]
    plan = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.NAME,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.FREQUENCY,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
        ),
    )
    return session, controller, plan, storage, factory


def _provenance_only_plan(tmp_path: Path) -> tuple[Any, Any, _Storage, Any]:
    context, source_storage, path, source = _field_context(tmp_path)
    selected = context.result.preview.records[0]
    plan = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.NAME,
                FavoritesEditorExternalFieldChoice.LOCAL,
            ),
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.FREQUENCY,
                FavoritesEditorExternalFieldChoice.DETACHED,
            ),
        ),
    )
    storage = _Storage(source_storage.value, tmp_path / "copied-tree")
    return plan, path, storage, source


def test_review_token_is_exact_deterministic_and_target_bound(tmp_path: Path) -> None:
    plan, _path, storage, source = _favorites_plan(tmp_path)

    first = review_favorites_editor_external_execution(plan, storage)
    second = review_favorites_editor_external_execution(plan, storage)
    other = _Storage(storage.value, tmp_path / "other-tree")

    assert first == second
    assert len(first.confirmation_token) == 64
    assert (
        review_favorites_editor_external_execution(plan, other).confirmation_token
        != first.confirmation_token
    )
    assert source.calls == 1


def test_confirmation_distinguishes_absent_and_empty_provenance(tmp_path: Path) -> None:
    plan, _path, storage, _source = _favorites_plan(tmp_path)
    absent = replace(
        plan,
        baseline_provenance_records=None,
        intended_provenance_records=None,
    )
    empty = replace(
        plan,
        baseline_provenance_records=(),
        intended_provenance_records=(),
    )

    assert external_execution._confirmation_token(
        absent, storage
    ) != external_execution._confirmation_token(empty, storage)


def test_exact_no_change_plan_is_not_executable(tmp_path: Path) -> None:
    plan, _path, storage, _source = _favorites_plan(tmp_path)
    no_change = replace(
        plan,
        write_plan=plan_favorites_write(
            plan.write_plan.baseline_snapshot,
            plan.write_plan.baseline_snapshot,
        ),
        intended_provenance_records=plan.baseline_provenance_records,
    )

    with pytest.raises(FavoritesEditorExternalExecutionError, match="no durable changes"):
        review_favorites_editor_external_execution(no_change, storage)


def test_favorites_changing_execution_writes_once_and_publishes(tmp_path: Path) -> None:
    plan, path, storage, source = _favorites_plan(tmp_path)
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    result = execute_favorites_editor_external_plan_durably(plan, storage, token)

    assert result.status is FavoritesEditorExternalExecutionStatus.COMPLETED
    assert result.observed_snapshot == plan.write_plan.intended_snapshot
    assert result.provenance_records == plan.intended_provenance_records
    assert result.provenance_path == path
    assert len(storage.execution_plans) == 1
    assert load_favorites_external_provenance(path, storage.value) == (
        plan.intended_provenance_records
    )
    assert source.calls == 1


def test_modeled_usb_adapter_uses_the_same_single_guarded_execution(
    tmp_path: Path,
) -> None:
    plan, _path, original, source = _favorites_plan(tmp_path)
    storage = _Storage(
        original.value,
        (tmp_path / "modeled-usb").resolve(),
        kind=FavoritesEditorSourceKind.USB,
    )
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    result = execute_favorites_editor_external_plan_durably(plan, storage, token)

    assert result.status is FavoritesEditorExternalExecutionStatus.COMPLETED
    assert len(storage.execution_plans) == 1
    assert storage.execution_plans[0] is plan.write_plan
    assert source.calls == 1


def test_provenance_only_execution_never_calls_storage_executor(tmp_path: Path) -> None:
    plan, path, storage, source = _provenance_only_plan(tmp_path)
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    result = execute_favorites_editor_external_plan_durably(plan, storage, token)

    assert result.status is FavoritesEditorExternalExecutionStatus.PROVENANCE_ONLY
    assert result.primary_execution is None
    assert storage.execution_plans == []
    assert load_favorites_external_provenance(path, storage.value) == (
        plan.intended_provenance_records
    )
    assert source.calls == 1


def test_stale_confirmation_and_target_are_refused_before_mutation(
    tmp_path: Path,
) -> None:
    plan, _path, storage, source = _favorites_plan(tmp_path)
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    with pytest.raises(FavoritesEditorExternalExecutionError, match="confirmation"):
        execute_favorites_editor_external_plan_durably(plan, storage, "0" * 64)

    storage.value = plan.write_plan.intended_snapshot
    with pytest.raises(FavoritesEditorExternalExecutionError, match="target"):
        execute_favorites_editor_external_plan_durably(plan, storage, token)

    assert storage.execution_plans == []
    assert source.calls == 1


def test_controller_refuses_equal_but_replaced_refresh_identity(tmp_path: Path) -> None:
    _session, controller, plan, storage, factory = _controller_plan(tmp_path)
    replaced_refresh = replace(plan.refresh_result)
    replaced_plan = replace(plan, refresh_result=replaced_refresh)

    with pytest.raises(FavoritesEditorExternalPreviewError, match="current exact refresh"):
        controller.review_external_execution(replaced_plan)

    assert storage.execution_plans == []
    assert factory.source.calls == 1


def test_uncertain_publication_that_committed_reconciles_as_success(
    tmp_path: Path,
) -> None:
    plan, _path, storage, source = _favorites_plan(tmp_path)
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    def save_then_raise(*args: Any, **kwargs: Any) -> Path:
        save_favorites_external_provenance_if_current(*args, **kwargs)
        raise OSError("injected uncertain return")

    result = execute_favorites_editor_external_plan_durably(
        plan,
        storage,
        token,
        _saver=save_then_raise,
    )

    assert result.publication_reconciled
    assert storage.value == plan.write_plan.intended_snapshot
    assert len(storage.execution_plans) == 1
    assert source.calls == 1


def test_failed_publication_reverses_favorites_to_exact_baseline(
    tmp_path: Path,
) -> None:
    plan, _path, storage, source = _favorites_plan(tmp_path)
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    def fail_before_save(*_args: Any, **_kwargs: Any) -> Path:
        raise OSError("injected publication failure")

    with pytest.raises(FavoritesEditorExternalExecutionError) as caught:
        execute_favorites_editor_external_plan_durably(
            plan,
            storage,
            token,
            _saver=fail_before_save,
        )

    error = caught.value
    assert error.recovery_status is FavoritesEditorExternalRecoveryStatus.BASELINE_RESTORED
    assert error.provenance_state is FavoritesEditorExternalProvenanceState.BASELINE
    assert storage.value == plan.write_plan.baseline_snapshot
    assert len(storage.execution_plans) == 2
    assert source.calls == 1


def test_mutating_executor_failure_is_reversed_and_recovery_failure_is_typed(
    tmp_path: Path,
) -> None:
    plan, _path, original, source = _favorites_plan(tmp_path)
    storage = _Storage(
        original.value,
        original.requested_path,
        fail_after_mutation=True,
        fail_recovery=True,
    )
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    with pytest.raises(FavoritesEditorExternalExecutionError) as caught:
        execute_favorites_editor_external_plan_durably(plan, storage, token)

    error = caught.value
    assert error.recovery_status is FavoritesEditorExternalRecoveryStatus.INCOMPLETE
    assert error.provenance_state is FavoritesEditorExternalProvenanceState.BASELINE
    assert len(storage.execution_plans) == 2
    assert source.calls == 1


def test_controller_adopts_lifecycle_and_editor_then_consumes_refresh(
    tmp_path: Path,
) -> None:
    session, controller, plan, storage, factory = _controller_plan(tmp_path)
    review = controller.review_external_execution(plan)

    result = controller.execute_external_plan(plan, review.confirmation_token)

    assert result.durable_result.observed_snapshot == plan.write_plan.intended_snapshot
    assert result.lifecycle_snapshot.favorites_snapshot == storage.value
    assert result.lifecycle_snapshot.provenance_records == plan.intended_provenance_records
    assert session.baseline_snapshot == storage.value
    assert session.intended_snapshot == storage.value
    assert controller.last_execution_result is result
    assert controller.last_execution_error is None
    assert controller.planning_context() is None
    assert factory.owners[0].lifecycle.snapshot().state is (
        FavoritesExternalProvenanceLifecycleState.CLOSED
    )
    assert factory.source.calls == 1


def test_controller_refuses_concurrent_execution_and_close_waits_for_terminal_evidence(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    session, controller, plan, storage, factory = _controller_plan(
        tmp_path,
        execution_entered=entered,
        execution_release=release,
    )
    review = controller.review_external_execution(plan)
    outcomes: list[object] = []

    def execute() -> None:
        try:
            outcomes.append(controller.execute_external_plan(plan, review.confirmation_token))
        except BaseException as error:
            outcomes.append(error)

    thread = threading.Thread(target=execute)
    thread.start()
    assert entered.wait(timeout=5)
    assert controller.execution_in_progress
    with pytest.raises(FavoritesEditorExternalPreviewError, match="already"):
        controller.execute_external_plan(plan, review.confirmation_token)

    controller.close()
    assert controller.execution_in_progress
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(outcomes) == 1
    assert not isinstance(outcomes[0], BaseException)
    assert not controller.execution_in_progress
    assert controller.last_execution_result is outcomes[0]
    assert session.baseline_snapshot == storage.value
    assert factory.owners[0].lifecycle.snapshot().state is (
        FavoritesExternalProvenanceLifecycleState.CLOSED
    )


def test_real_copied_tree_backend_retains_all_durable_operation_artifacts(
    tmp_path: Path,
) -> None:
    plan, _path, _original, source = _favorites_plan(tmp_path / "planning")
    target = tmp_path / "favorites_lists"
    target.mkdir()
    (target / "f_list.cfg").write_bytes(plan.write_plan.baseline_snapshot.catalog_bytes)
    for document in plan.write_plan.baseline_snapshot.documents:
        (target / document.filename).write_bytes(document.content)
    storage = FavoritesCopiedTreeEditorStorage(target)
    token = review_favorites_editor_external_execution(plan, storage).confirmation_token

    result = execute_favorites_editor_external_plan_durably(plan, storage, token)

    primary = result.primary_execution
    assert primary is not None
    assert primary.operation_id is not None
    assert primary.backup_directory is not None
    assert primary.backup_directory.is_dir()
    assert primary.staging_directory is not None
    assert not primary.staging_directory.exists()
    assert primary.displaced_directory is not None
    assert primary.displaced_directory.is_dir()
    assert primary.rollback_manifest_path is not None
    assert primary.rollback_manifest_path.is_file()
    assert primary.operation_report_path is not None
    assert primary.operation_report_path.is_file()
    assert storage.read_snapshot() == plan.write_plan.intended_snapshot
    assert source.calls == 1
