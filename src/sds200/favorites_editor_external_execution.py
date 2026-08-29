"""Guarded execution of one exact reviewed assisted Favorites plan."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .favorites_editor import (
    FavoritesEditorSourceKind,
    FavoritesEditorStorage,
    FavoritesEditorWriteResult,
)
from .favorites_editor_external_planning import (
    FavoritesEditorExternalFieldDecision,
    FavoritesEditorExternalPlanningSnapshot,
    FavoritesEditorExternalRecordDecision,
)
from .favorites_external import FavoritesExternalRecordState
from .favorites_external_provenance import (
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD,
    FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    serialize_favorites_external_provenance,
)
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_provenance_storage import (
    load_favorites_external_provenance,
    save_favorites_external_provenance_if_current,
)
from .favorites_storage import FavoritesStorageSnapshot
from .favorites_write_plan import plan_favorites_write


class FavoritesEditorExternalExecutionStage(StrEnum):
    """Classify the terminal stage of an assisted execution failure."""

    VALIDATION = "validation"
    FAVORITES_EXECUTION = "favorites_execution"
    FAVORITES_READBACK = "favorites_readback"
    PROVENANCE_PUBLICATION = "provenance_publication"
    FAVORITES_RECOVERY = "favorites_recovery"


class FavoritesEditorExternalRecoveryStatus(StrEnum):
    """Classify cross-store recovery after an execution failure."""

    NOT_REQUIRED = "not_required"
    BASELINE_RETAINED = "baseline_retained"
    BASELINE_RESTORED = "baseline_restored"
    INCOMPLETE = "incomplete"


class FavoritesEditorExternalProvenanceState(StrEnum):
    """Classify exact durable provenance observed during reconciliation."""

    BASELINE = "baseline"
    INTENDED = "intended"
    UNKNOWN = "unknown"


class FavoritesEditorExternalExecutionStatus(StrEnum):
    """Classify one completed assisted execution."""

    COMPLETED = "completed"
    PROVENANCE_ONLY = "provenance_only"


class FavoritesEditorExternalExecutionError(RuntimeError):
    """Report a refused or failed assisted execution with recovery evidence."""

    def __init__(
        self,
        message: str,
        *,
        stage: FavoritesEditorExternalExecutionStage,
        recovery_status: FavoritesEditorExternalRecoveryStatus,
        provenance_state: FavoritesEditorExternalProvenanceState,
        primary_execution: FavoritesEditorWriteResult | None = None,
        recovery_execution: FavoritesEditorWriteResult | None = None,
        observed_snapshot: FavoritesStorageSnapshot | None = None,
        cause_type: str | None = None,
    ) -> None:
        if type(message) is not str or not message.strip():
            raise ValueError("Assisted execution error message must be non-empty.")
        if not isinstance(stage, FavoritesEditorExternalExecutionStage):
            raise TypeError("Assisted execution error stage is invalid.")
        if not isinstance(recovery_status, FavoritesEditorExternalRecoveryStatus):
            raise TypeError("Assisted execution recovery status is invalid.")
        if not isinstance(provenance_state, FavoritesEditorExternalProvenanceState):
            raise TypeError("Assisted execution provenance state is invalid.")
        if observed_snapshot is not None and not isinstance(
            observed_snapshot, FavoritesStorageSnapshot
        ):
            raise TypeError("Assisted execution observed snapshot is invalid.")
        if cause_type is not None and (type(cause_type) is not str or not cause_type.strip()):
            raise ValueError("Assisted execution cause type must be non-empty or None.")
        self.stage = stage
        self.recovery_status = recovery_status
        self.provenance_state = provenance_state
        self.primary_execution = primary_execution
        self.recovery_execution = recovery_execution
        self.observed_snapshot = observed_snapshot
        self.cause_type = cause_type
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalExecutionReview:
    """One exact assisted plan and its separate full confirmation token."""

    plan: FavoritesEditorExternalPlanningSnapshot
    storage_kind: str
    requested_path: Path
    confirmation_token: str

    def __post_init__(self) -> None:
        if type(self.plan) is not FavoritesEditorExternalPlanningSnapshot:
            raise TypeError("Assisted execution review requires an exact plan.")
        if type(self.storage_kind) is not str or not self.storage_kind:
            raise ValueError("Assisted execution review storage kind is invalid.")
        if not isinstance(self.requested_path, Path):
            raise TypeError("Assisted execution review path must be pathlib.Path.")
        if type(self.confirmation_token) is not str or (
            len(self.confirmation_token) != 64
            or any(character not in "0123456789abcdef" for character in self.confirmation_token)
        ):
            raise ValueError("Assisted execution confirmation must be lowercase SHA-256.")


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalDurableExecutionResult:
    """Exact intended Favorites and provenance verified after execution."""

    plan: FavoritesEditorExternalPlanningSnapshot
    status: FavoritesEditorExternalExecutionStatus
    primary_execution: FavoritesEditorWriteResult | None
    observed_snapshot: FavoritesStorageSnapshot
    provenance_records: tuple[FavoritesExternalRecordState, ...] | None
    provenance_path: Path
    publication_reconciled: bool = False

    def __post_init__(self) -> None:
        if type(self.plan) is not FavoritesEditorExternalPlanningSnapshot:
            raise TypeError("Durable assisted execution requires an exact plan.")
        if not isinstance(self.status, FavoritesEditorExternalExecutionStatus):
            raise TypeError("Durable assisted execution status is invalid.")
        if not isinstance(self.observed_snapshot, FavoritesStorageSnapshot):
            raise TypeError("Durable assisted execution snapshot is invalid.")
        if self.provenance_records is not None and type(self.provenance_records) is not tuple:
            raise TypeError("Durable assisted execution provenance must be a tuple or None.")
        if self.provenance_records is not None and any(
            not isinstance(record, FavoritesExternalRecordState)
            for record in self.provenance_records
        ):
            raise TypeError("Durable assisted execution provenance is invalid.")
        if not isinstance(self.provenance_path, Path):
            raise TypeError("Durable assisted execution path must be pathlib.Path.")
        if type(self.publication_reconciled) is not bool:
            raise TypeError("Assisted publication reconciliation flag must be bool.")
        if self.observed_snapshot != self.plan.write_plan.intended_snapshot:
            raise ValueError(
                "Durable assisted execution must retain the intended Favorites snapshot."
            )
        if self.provenance_records != self.plan.intended_provenance_records:
            raise ValueError("Durable assisted execution must retain intended provenance.")
        if self.provenance_path != self.plan.refresh_result.lifecycle_snapshot.provenance_path:
            raise ValueError("Durable assisted execution provenance path differs from its plan.")
        if self.status is FavoritesEditorExternalExecutionStatus.COMPLETED:
            if not self.plan.has_favorites_changes or self.primary_execution is None:
                raise ValueError("Completed assisted execution requires a Favorites write result.")
        elif self.plan.has_favorites_changes or self.primary_execution is not None:
            raise ValueError("Provenance-only assisted execution cannot retain a Favorites write.")


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalExecutionResult:
    """Retain exact plan, durability, and lifecycle-advancement evidence."""

    plan: FavoritesEditorExternalPlanningSnapshot
    durable_result: FavoritesEditorExternalDurableExecutionResult
    lifecycle_snapshot: FavoritesExternalProvenanceLifecycleSnapshot

    def __post_init__(self) -> None:
        if type(self.plan) is not FavoritesEditorExternalPlanningSnapshot:
            raise TypeError("Assisted editor execution result requires an exact plan.")
        if type(self.durable_result) is not FavoritesEditorExternalDurableExecutionResult:
            raise TypeError("Assisted editor execution result requires exact durable evidence.")
        if type(self.lifecycle_snapshot) is not FavoritesExternalProvenanceLifecycleSnapshot:
            raise TypeError(
                "Assisted editor execution result requires an exact lifecycle snapshot."
            )
        if self.durable_result.plan is not self.plan:
            raise ValueError("Assisted editor durable evidence belongs to another plan.")
        if self.lifecycle_snapshot.state is not FavoritesExternalProvenanceLifecycleState.ACTIVE:
            raise ValueError("Assisted editor result requires an active lifecycle.")
        if self.lifecycle_snapshot.favorites_snapshot != self.durable_result.observed_snapshot:
            raise ValueError("Assisted editor lifecycle Favorites evidence is not the readback.")
        if self.lifecycle_snapshot.provenance_records != self.durable_result.provenance_records:
            raise ValueError("Assisted editor lifecycle provenance is not the published state.")
        if self.lifecycle_snapshot.provenance_path != self.durable_result.provenance_path:
            raise ValueError("Assisted editor lifecycle provenance path is not the published path.")


class _ProvenanceLoader(Protocol):
    def __call__(
        self,
        path: str | Path,
        snapshot: FavoritesStorageSnapshot,
        *,
        max_bytes: int,
        max_records: int,
        max_fields_per_record: int,
    ) -> tuple[FavoritesExternalRecordState, ...] | None: ...


class _ProvenanceSaver(Protocol):
    def __call__(
        self,
        records: tuple[FavoritesExternalRecordState, ...],
        path: str | Path,
        *,
        expected_current_records: tuple[FavoritesExternalRecordState, ...] | None,
        max_bytes: int,
        max_records: int,
        max_fields_per_record: int,
    ) -> Path: ...


class _TokenDigest:
    def __init__(self) -> None:
        self._value = hashlib.sha256()

    def add(self, value: bytes) -> None:
        self._value.update(len(value).to_bytes(8, "big"))
        self._value.update(value)

    def hexdigest(self) -> str:
        return self._value.hexdigest()


def _snapshot_token_bytes(snapshot: FavoritesStorageSnapshot) -> bytes:
    digest = _TokenDigest()
    digest.add(snapshot.catalog_bytes)
    for document in snapshot.documents:
        digest.add(document.filename.encode("utf-8"))
        digest.add(document.content)
    return bytes.fromhex(digest.hexdigest())


def _provenance_token_bytes(
    records: tuple[FavoritesExternalRecordState, ...] | None,
) -> bytes:
    if records is None:
        return b"absent"
    return b"present\x00" + serialize_favorites_external_provenance(records)


def _decision_token_bytes(
    decision: FavoritesEditorExternalFieldDecision | FavoritesEditorExternalRecordDecision,
) -> bytes:
    if isinstance(decision, FavoritesEditorExternalFieldDecision):
        return "|".join(
            (
                "field",
                repr(decision.preview),
                decision.field.value,
                decision.choice.value,
            )
        ).encode("utf-8")
    return "|".join(
        (
            "record",
            repr(decision.preview),
            decision.choice.value,
            repr(decision.anchor),
            repr(decision.template),
            repr(decision.bindings),
        )
    ).encode("utf-8")


def _confirmation_token(
    plan: FavoritesEditorExternalPlanningSnapshot,
    storage: FavoritesEditorStorage,
) -> str:
    digest = _TokenDigest()
    digest.add(b"sdsctl-assisted-favorites-execution-v1")
    digest.add(storage.kind.value.encode("ascii"))
    digest.add(str(storage.requested_path).encode("utf-8"))
    digest.add(repr(plan.refresh_result).encode("utf-8"))
    digest.add(_snapshot_token_bytes(plan.write_plan.baseline_snapshot))
    digest.add(_snapshot_token_bytes(plan.write_plan.intended_snapshot))
    digest.add(_provenance_token_bytes(plan.baseline_provenance_records))
    digest.add(_provenance_token_bytes(plan.intended_provenance_records))
    digest.add(str(plan.unresolved_decisions).encode("ascii"))
    for blocker in plan.blockers:
        digest.add(blocker.value.encode("ascii"))
    for decision in plan.decisions:
        digest.add(_decision_token_bytes(decision))
    return digest.hexdigest()


def review_favorites_editor_external_execution(
    plan: FavoritesEditorExternalPlanningSnapshot,
    storage: FavoritesEditorStorage,
) -> FavoritesEditorExternalExecutionReview:
    """Review one complete current plan and derive its exact confirmation token."""

    if type(plan) is not FavoritesEditorExternalPlanningSnapshot:
        raise TypeError("Assisted execution review requires an exact planning snapshot.")
    if (
        not isinstance(getattr(storage, "kind", None), FavoritesEditorSourceKind)
        or not isinstance(getattr(storage, "requested_path", None), Path)
        or not callable(getattr(storage, "read_snapshot", None))
        or not callable(getattr(storage, "execute", None))
    ):
        raise TypeError("Assisted execution review requires FavoritesEditorStorage.")
    if not plan.is_complete:
        blockers = ", ".join(blocker.value for blocker in plan.blockers) or "unknown"
        raise FavoritesEditorExternalExecutionError(
            f"Assisted execution plan is incomplete or blocked: {blockers}.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.BASELINE,
        )
    if not plan.has_favorites_changes and not plan.has_provenance_changes:
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution plan has no durable changes.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.BASELINE,
        )
    lifecycle = plan.refresh_result.lifecycle_snapshot
    if lifecycle.favorites_snapshot != plan.write_plan.baseline_snapshot:
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution Favorites baseline differs from refresh evidence.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.UNKNOWN,
        )
    if lifecycle.provenance_records != plan.baseline_provenance_records:
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution provenance baseline differs from refresh evidence.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.UNKNOWN,
        )
    return FavoritesEditorExternalExecutionReview(
        plan=plan,
        storage_kind=storage.kind.value,
        requested_path=storage.requested_path,
        confirmation_token=_confirmation_token(plan, storage),
    )


def _read_storage(storage: FavoritesEditorStorage) -> FavoritesStorageSnapshot | None:
    try:
        observed = storage.read_snapshot()
    except BaseException:
        return None
    return observed if isinstance(observed, FavoritesStorageSnapshot) else None


def _reconcile_provenance(
    plan: FavoritesEditorExternalPlanningSnapshot,
    path: Path,
    loader: _ProvenanceLoader,
    *,
    max_bytes: int,
    max_records: int,
    max_fields_per_record: int,
) -> FavoritesEditorExternalProvenanceState:
    candidates = (
        (
            FavoritesEditorExternalProvenanceState.INTENDED,
            plan.write_plan.intended_snapshot,
            plan.intended_provenance_records,
        ),
        (
            FavoritesEditorExternalProvenanceState.BASELINE,
            plan.write_plan.baseline_snapshot,
            plan.baseline_provenance_records,
        ),
    )
    for state, snapshot, expected in candidates:
        try:
            observed = loader(
                path,
                snapshot,
                max_bytes=max_bytes,
                max_records=max_records,
                max_fields_per_record=max_fields_per_record,
            )
        except BaseException:
            continue
        if observed == expected:
            return state
    return FavoritesEditorExternalProvenanceState.UNKNOWN


def _recover_favorites_baseline(
    plan: FavoritesEditorExternalPlanningSnapshot,
    storage: FavoritesEditorStorage,
    *,
    primary_execution: FavoritesEditorWriteResult | None,
    cause_type: str,
    stage: FavoritesEditorExternalExecutionStage,
    provenance_state: FavoritesEditorExternalProvenanceState,
) -> FavoritesEditorExternalExecutionError:
    baseline = plan.write_plan.baseline_snapshot
    intended = plan.write_plan.intended_snapshot
    observed = _read_storage(storage)
    if observed == baseline:
        return FavoritesEditorExternalExecutionError(
            "Assisted execution failed while the exact Favorites baseline was retained.",
            stage=stage,
            recovery_status=FavoritesEditorExternalRecoveryStatus.BASELINE_RETAINED,
            provenance_state=provenance_state,
            primary_execution=primary_execution,
            observed_snapshot=observed,
            cause_type=cause_type,
        )
    if observed != intended:
        return FavoritesEditorExternalExecutionError(
            "Assisted execution recovery is incomplete; Favorites is neither "
            "baseline nor intended.",
            stage=FavoritesEditorExternalExecutionStage.FAVORITES_RECOVERY,
            recovery_status=FavoritesEditorExternalRecoveryStatus.INCOMPLETE,
            provenance_state=provenance_state,
            primary_execution=primary_execution,
            observed_snapshot=observed,
            cause_type=cause_type,
        )
    reverse_plan = plan_favorites_write(intended, baseline)
    if reverse_plan.is_blocked or reverse_plan.is_noop:
        return FavoritesEditorExternalExecutionError(
            "Assisted execution could not derive an unblocked exact reverse plan.",
            stage=FavoritesEditorExternalExecutionStage.FAVORITES_RECOVERY,
            recovery_status=FavoritesEditorExternalRecoveryStatus.INCOMPLETE,
            provenance_state=provenance_state,
            primary_execution=primary_execution,
            observed_snapshot=observed,
            cause_type=cause_type,
        )
    try:
        recovery_execution = storage.execute(reverse_plan)
    except BaseException as recovery_error:
        return FavoritesEditorExternalExecutionError(
            "Assisted execution exact reverse Favorites recovery failed.",
            stage=FavoritesEditorExternalExecutionStage.FAVORITES_RECOVERY,
            recovery_status=FavoritesEditorExternalRecoveryStatus.INCOMPLETE,
            provenance_state=provenance_state,
            primary_execution=primary_execution,
            observed_snapshot=_read_storage(storage),
            cause_type=recovery_error.__class__.__name__,
        )
    recovered = _read_storage(storage)
    if recovered != baseline:
        return FavoritesEditorExternalExecutionError(
            "Assisted execution reverse recovery failed exact baseline readback.",
            stage=FavoritesEditorExternalExecutionStage.FAVORITES_RECOVERY,
            recovery_status=FavoritesEditorExternalRecoveryStatus.INCOMPLETE,
            provenance_state=provenance_state,
            primary_execution=primary_execution,
            recovery_execution=recovery_execution,
            observed_snapshot=recovered,
            cause_type=cause_type,
        )
    return FavoritesEditorExternalExecutionError(
        "Assisted execution failed and exact Favorites baseline was restored.",
        stage=stage,
        recovery_status=FavoritesEditorExternalRecoveryStatus.BASELINE_RESTORED,
        provenance_state=provenance_state,
        primary_execution=primary_execution,
        recovery_execution=recovery_execution,
        observed_snapshot=recovered,
        cause_type=cause_type,
    )


def execute_favorites_editor_external_plan_durably(
    plan: FavoritesEditorExternalPlanningSnapshot,
    storage: FavoritesEditorStorage,
    confirmation_token: str,
    *,
    max_bytes: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_BYTES,
    max_records: int = FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_RECORDS,
    max_fields_per_record: int = (FAVORITES_EXTERNAL_PROVENANCE_DEFAULT_MAX_FIELDS_PER_RECORD),
    _loader: _ProvenanceLoader = load_favorites_external_provenance,
    _saver: _ProvenanceSaver = save_favorites_external_provenance_if_current,
) -> FavoritesEditorExternalDurableExecutionResult:
    """Execute, verify, conditionally publish, and reconcile one exact plan."""

    if type(confirmation_token) is not str:
        raise TypeError("Assisted execution confirmation token must be a string.")
    review = review_favorites_editor_external_execution(plan, storage)
    if confirmation_token != review.confirmation_token:
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution confirmation is stale or does not match the exact plan.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.UNKNOWN,
        )
    baseline = plan.write_plan.baseline_snapshot
    intended = plan.write_plan.intended_snapshot
    path = plan.refresh_result.lifecycle_snapshot.provenance_path
    fresh = _read_storage(storage)
    if fresh != baseline:
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution target no longer matches the exact Favorites baseline.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.UNKNOWN,
            observed_snapshot=fresh,
        )
    try:
        current_provenance = _loader(
            path,
            baseline,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
    except BaseException as error:
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution could not verify exact persisted provenance.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.UNKNOWN,
            observed_snapshot=fresh,
            cause_type=error.__class__.__name__,
        ) from None
    if current_provenance != plan.baseline_provenance_records:
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution persisted provenance no longer matches the baseline.",
            stage=FavoritesEditorExternalExecutionStage.VALIDATION,
            recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
            provenance_state=FavoritesEditorExternalProvenanceState.UNKNOWN,
            observed_snapshot=fresh,
        )

    primary_execution: FavoritesEditorWriteResult | None = None
    if plan.has_favorites_changes:
        try:
            primary_execution = storage.execute(plan.write_plan)
        except BaseException as error:
            raise _recover_favorites_baseline(
                plan,
                storage,
                primary_execution=None,
                cause_type=error.__class__.__name__,
                stage=FavoritesEditorExternalExecutionStage.FAVORITES_EXECUTION,
                provenance_state=FavoritesEditorExternalProvenanceState.BASELINE,
            ) from None
        observed = _read_storage(storage)
        if observed != intended:
            raise _recover_favorites_baseline(
                plan,
                storage,
                primary_execution=primary_execution,
                cause_type="FavoritesReadbackMismatch",
                stage=FavoritesEditorExternalExecutionStage.FAVORITES_READBACK,
                provenance_state=FavoritesEditorExternalProvenanceState.BASELINE,
            )
    else:
        observed = fresh

    reconciled = False
    if plan.has_provenance_changes:
        intended_records = plan.intended_provenance_records
        if intended_records is None:
            raise FavoritesEditorExternalExecutionError(
                "Assisted execution cannot publish absent intended provenance.",
                stage=FavoritesEditorExternalExecutionStage.VALIDATION,
                recovery_status=FavoritesEditorExternalRecoveryStatus.NOT_REQUIRED,
                provenance_state=FavoritesEditorExternalProvenanceState.BASELINE,
                primary_execution=primary_execution,
                observed_snapshot=observed,
            )
        publication_error: BaseException | None = None
        try:
            _saver(
                intended_records,
                path,
                expected_current_records=current_provenance,
                max_bytes=max_bytes,
                max_records=max_records,
                max_fields_per_record=max_fields_per_record,
            )
            verified = _loader(
                path,
                intended,
                max_bytes=max_bytes,
                max_records=max_records,
                max_fields_per_record=max_fields_per_record,
            )
            if verified != intended_records:
                publication_error = ValueError("ProvenanceReadbackMismatch")
        except BaseException as error:
            publication_error = error
        if publication_error is not None:
            provenance_state = _reconcile_provenance(
                plan,
                path,
                _loader,
                max_bytes=max_bytes,
                max_records=max_records,
                max_fields_per_record=max_fields_per_record,
            )
            if provenance_state is FavoritesEditorExternalProvenanceState.INTENDED:
                reconciled = True
            elif provenance_state is FavoritesEditorExternalProvenanceState.BASELINE:
                if plan.has_favorites_changes:
                    raise _recover_favorites_baseline(
                        plan,
                        storage,
                        primary_execution=primary_execution,
                        cause_type=publication_error.__class__.__name__,
                        stage=(FavoritesEditorExternalExecutionStage.PROVENANCE_PUBLICATION),
                        provenance_state=provenance_state,
                    ) from None
                raise FavoritesEditorExternalExecutionError(
                    "Assisted provenance-only execution failed with baseline retained.",
                    stage=FavoritesEditorExternalExecutionStage.PROVENANCE_PUBLICATION,
                    recovery_status=(FavoritesEditorExternalRecoveryStatus.BASELINE_RETAINED),
                    provenance_state=provenance_state,
                    observed_snapshot=observed,
                    cause_type=publication_error.__class__.__name__,
                ) from None
            else:
                raise FavoritesEditorExternalExecutionError(
                    "Assisted execution provenance publication state is incomplete.",
                    stage=FavoritesEditorExternalExecutionStage.PROVENANCE_PUBLICATION,
                    recovery_status=FavoritesEditorExternalRecoveryStatus.INCOMPLETE,
                    provenance_state=provenance_state,
                    primary_execution=primary_execution,
                    observed_snapshot=observed,
                    cause_type=publication_error.__class__.__name__,
                ) from None

    final_snapshot = _read_storage(storage)
    if final_snapshot != intended:
        provenance_state = _reconcile_provenance(
            plan,
            path,
            _loader,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
        if provenance_state is FavoritesEditorExternalProvenanceState.BASELINE:
            raise _recover_favorites_baseline(
                plan,
                storage,
                primary_execution=primary_execution,
                cause_type="FinalFavoritesReadbackMismatch",
                stage=FavoritesEditorExternalExecutionStage.FAVORITES_READBACK,
                provenance_state=provenance_state,
            ) from None
        raise FavoritesEditorExternalExecutionError(
            "Assisted execution final Favorites readback is incomplete.",
            stage=FavoritesEditorExternalExecutionStage.FAVORITES_READBACK,
            recovery_status=FavoritesEditorExternalRecoveryStatus.INCOMPLETE,
            provenance_state=provenance_state,
            primary_execution=primary_execution,
            observed_snapshot=final_snapshot,
        )
    final_provenance_error: BaseException | None = None
    try:
        final_provenance = _loader(
            path,
            intended,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
        if final_provenance != plan.intended_provenance_records:
            final_provenance_error = ValueError("FinalProvenanceReadbackMismatch")
    except BaseException as error:
        final_provenance_error = error
    if final_provenance_error is not None:
        provenance_state = _reconcile_provenance(
            plan,
            path,
            _loader,
            max_bytes=max_bytes,
            max_records=max_records,
            max_fields_per_record=max_fields_per_record,
        )
        if provenance_state is FavoritesEditorExternalProvenanceState.INTENDED:
            reconciled = True
        elif provenance_state is FavoritesEditorExternalProvenanceState.BASELINE:
            if plan.has_favorites_changes:
                raise _recover_favorites_baseline(
                    plan,
                    storage,
                    primary_execution=primary_execution,
                    cause_type=final_provenance_error.__class__.__name__,
                    stage=(FavoritesEditorExternalExecutionStage.PROVENANCE_PUBLICATION),
                    provenance_state=provenance_state,
                ) from None
            raise FavoritesEditorExternalExecutionError(
                "Assisted provenance-only execution lost final publication readback.",
                stage=FavoritesEditorExternalExecutionStage.PROVENANCE_PUBLICATION,
                recovery_status=(FavoritesEditorExternalRecoveryStatus.BASELINE_RETAINED),
                provenance_state=provenance_state,
                observed_snapshot=final_snapshot,
                cause_type=final_provenance_error.__class__.__name__,
            ) from None
        else:
            raise FavoritesEditorExternalExecutionError(
                "Assisted execution final provenance readback is incomplete.",
                stage=FavoritesEditorExternalExecutionStage.PROVENANCE_PUBLICATION,
                recovery_status=FavoritesEditorExternalRecoveryStatus.INCOMPLETE,
                provenance_state=provenance_state,
                primary_execution=primary_execution,
                observed_snapshot=final_snapshot,
                cause_type=final_provenance_error.__class__.__name__,
            ) from None
    return FavoritesEditorExternalDurableExecutionResult(
        plan=plan,
        status=(
            FavoritesEditorExternalExecutionStatus.COMPLETED
            if plan.has_favorites_changes
            else FavoritesEditorExternalExecutionStatus.PROVENANCE_ONLY
        ),
        primary_execution=primary_execution,
        observed_snapshot=final_snapshot,
        provenance_records=plan.intended_provenance_records,
        provenance_path=path,
        publication_reconciled=reconciled,
    )


def execute_favorites_editor_external_plan(
    plan: FavoritesEditorExternalPlanningSnapshot,
    lifecycle: FavoritesExternalProvenanceLifecycle,
    storage: FavoritesEditorStorage,
    confirmation_token: str,
) -> FavoritesEditorExternalExecutionResult:
    """Execute one exact plan and atomically advance its lifecycle owner."""

    if type(plan) is not FavoritesEditorExternalPlanningSnapshot:
        raise TypeError("Assisted editor execution requires an exact plan.")
    if not isinstance(lifecycle, FavoritesExternalProvenanceLifecycle):
        raise TypeError("Assisted editor execution requires FavoritesExternalProvenanceLifecycle.")
    durable_result, lifecycle_snapshot = lifecycle._execute_editor_external_durably_from_snapshot(
        plan.refresh_result.lifecycle_snapshot,
        plan,
        storage,
        confirmation_token,
    )
    return FavoritesEditorExternalExecutionResult(
        plan=plan,
        durable_result=durable_result,
        lifecycle_snapshot=lifecycle_snapshot,
    )


__all__ = [
    "FavoritesEditorExternalDurableExecutionResult",
    "FavoritesEditorExternalExecutionError",
    "FavoritesEditorExternalExecutionReview",
    "FavoritesEditorExternalExecutionResult",
    "FavoritesEditorExternalExecutionStage",
    "FavoritesEditorExternalExecutionStatus",
    "FavoritesEditorExternalProvenanceState",
    "FavoritesEditorExternalRecoveryStatus",
    "execute_favorites_editor_external_plan",
    "execute_favorites_editor_external_plan_durably",
    "review_favorites_editor_external_execution",
]
