"""Read-only external refresh presentation for the Favorites editor."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .favorites_editor import FavoritesEditorSession, FavoritesEditorStorage
from .favorites_external import (
    FavoritesExternalChangeKind,
    FavoritesExternalRecordPreview,
    FavoritesExternalSourceIdentity,
)
from .favorites_external_assisted_sync import (
    FavoritesExternalAssistedSynchronizationService,
    RadioReferenceAssistedSynchronizationSourceFactory,
)
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_refresh import FavoritesExternalRefreshResult
from .radioreference import RadioReferenceError

if TYPE_CHECKING:
    from .favorites_editor_external_execution import (
        FavoritesEditorExternalExecutionResult,
        FavoritesEditorExternalExecutionReview,
    )
    from .favorites_editor_external_planning import (
        FavoritesEditorExternalPlanningSnapshot,
    )


class FavoritesEditorExternalPreviewState(StrEnum):
    """Classify the editor's optional read-only external preview state."""

    READY = "ready"
    REFRESHING = "refreshing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


class FavoritesEditorExternalPreviewError(RuntimeError):
    """Report a stable, secret-free external preview failure."""


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalFieldPresentation:
    """Render one exact immutable external field preview."""

    name: str
    kind: str
    ownership: str
    local_value: str | None
    external_state: str
    external_value: str | None


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalRecordPresentation:
    """Render one exact immutable external/local record relationship."""

    kind: str
    local_target: str
    external_record_id: str
    observed_at: str
    revision: str
    fields: tuple[FavoritesEditorExternalFieldPresentation, ...]


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalPreviewPresentation:
    """Complete deterministic presentation of one successful refresh."""

    provider: str
    dataset: str
    started_at: str
    completed_at: str
    observation_count: int
    counts: tuple[tuple[str, int], ...]
    records: tuple[FavoritesEditorExternalRecordPresentation, ...]


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalPreviewSnapshot:
    """Immutable controller state for renderer consumption."""

    state: FavoritesEditorExternalPreviewState
    presentation: FavoritesEditorExternalPreviewPresentation | None
    error: str | None
    stale_reason: str | None


@dataclass(slots=True)
class FavoritesEditorExternalRefreshOwner:
    """Own one fresh provenance lifecycle and assisted refresh service."""

    service: FavoritesExternalAssistedSynchronizationService
    lifecycle: FavoritesExternalProvenanceLifecycle
    source: FavoritesExternalSourceIdentity

    def close(self) -> None:
        """Close the local lifecycle without publishing or mutating provenance."""

        self.lifecycle.close()


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalPlanningContext:
    """Retain one current refresh service and its exact adopted evidence."""

    service: FavoritesExternalAssistedSynchronizationService
    result: FavoritesExternalRefreshResult
    source: FavoritesExternalSourceIdentity

    def __post_init__(self) -> None:
        if not isinstance(
            self.service,
            FavoritesExternalAssistedSynchronizationService,
        ):
            raise TypeError(
                "External planning context requires the assisted synchronization service."
            )
        if type(self.result) is not FavoritesExternalRefreshResult:
            raise TypeError("External planning context requires an exact refresh result.")
        if not isinstance(self.source, FavoritesExternalSourceIdentity):
            raise TypeError("External planning context requires an exact source identity.")
        if self.service.lifecycle_snapshot != self.result.lifecycle_snapshot:
            raise ValueError(
                "External planning context lifecycle does not match its refresh result."
            )
        if (
            self.result.lifecycle_snapshot.state
            is not FavoritesExternalProvenanceLifecycleState.ACTIVE
        ):
            raise ValueError("External planning context requires an active lifecycle.")
        if any(
            observation.identity.source != self.source for observation in self.result.observations
        ):
            raise ValueError("External planning context observations do not match its source.")


class FavoritesEditorExternalRefreshOwnerFactory(Protocol):
    """Construct one fresh, independently closeable refresh owner."""

    def __call__(self) -> FavoritesEditorExternalRefreshOwner: ...


@dataclass(frozen=True, slots=True)
class FavoritesEditorRadioReferenceRefreshOwnerFactory:
    """Lazily compose the production RadioReference preview chain."""

    storage: FavoritesEditorStorage
    provenance_path: Path
    source_factory: RadioReferenceAssistedSynchronizationSourceFactory

    def __post_init__(self) -> None:
        if not isinstance(self.provenance_path, Path):
            raise TypeError("RadioReference preview provenance path must be pathlib.Path.")
        if not self.provenance_path.is_absolute() or not self.provenance_path.name:
            raise ValueError(
                "RadioReference preview provenance path must identify an absolute file."
            )
        if not isinstance(
            self.source_factory,
            RadioReferenceAssistedSynchronizationSourceFactory,
        ):
            raise TypeError("RadioReference preview requires the production source factory.")

    def __call__(self) -> FavoritesEditorExternalRefreshOwner:
        lifecycle = FavoritesExternalProvenanceLifecycle(
            self.storage,
            self.provenance_path,
        )
        try:
            lifecycle.start()
            source = self.source_factory()
            service = FavoritesExternalAssistedSynchronizationService(
                lifecycle,
                source,
            )
        except BaseException:
            lifecycle.close()
            raise
        return FavoritesEditorExternalRefreshOwner(
            service,
            lifecycle,
            self.source_factory.request_plan.source,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _local_target(record: FavoritesExternalRecordPreview) -> str:
    target = record.target
    if target is None:
        return "unmapped"
    filename = target.filename or "f_list.cfg"
    return f"{filename}:{target.source_index}"


def _record_presentation(
    record: FavoritesExternalRecordPreview,
) -> FavoritesEditorExternalRecordPresentation:
    identity = record.external_identity
    evidence = record.evidence
    fields = tuple(
        FavoritesEditorExternalFieldPresentation(
            name=field.name,
            kind=field.kind.value,
            ownership=field.ownership.value,
            local_value=field.local_value,
            external_state=(
                "unobserved" if field.external_state is None else field.external_state.value
            ),
            external_value=field.external_value,
        )
        for field in record.fields
    )
    return FavoritesEditorExternalRecordPresentation(
        kind=record.kind.value,
        local_target=_local_target(record),
        external_record_id="local-only" if identity is None else identity.record_id,
        observed_at=("unobserved" if evidence is None else evidence.observed_at.isoformat()),
        revision=(
            "unavailable" if evidence is None or evidence.revision is None else evidence.revision
        ),
        fields=fields,
    )


def present_favorites_editor_external_preview(
    result: FavoritesExternalRefreshResult,
    *,
    source: FavoritesExternalSourceIdentity,
    started_at: datetime,
    completed_at: datetime,
) -> FavoritesEditorExternalPreviewPresentation:
    """Project one exact refresh result into deterministic renderer data."""

    if type(result) is not FavoritesExternalRefreshResult:
        raise TypeError("Favorites editor preview requires an exact refresh result.")
    if not isinstance(source, FavoritesExternalSourceIdentity):
        raise TypeError("Favorites editor preview requires an exact source identity.")
    for label, value in (("started", started_at), ("completed", completed_at)):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"Favorites editor refresh {label} time must be aware.")
    if any(observation.identity.source != source for observation in result.observations):
        raise ValueError("External preview observations do not match the configured dataset.")
    counts = tuple(
        (
            kind.value,
            sum(record.kind is kind for record in result.preview.records),
        )
        for kind in FavoritesExternalChangeKind
    )
    return FavoritesEditorExternalPreviewPresentation(
        provider=source.provider,
        dataset=source.dataset,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        observation_count=len(result.observations),
        counts=counts,
        records=tuple(_record_presentation(record) for record in result.preview.records),
    )


def _redacted_error(error: BaseException) -> str:
    if isinstance(error, RadioReferenceError):
        return f"RadioReference refresh failed: {error.reason.value}."
    if isinstance(error, FavoritesEditorExternalPreviewError):
        return str(error)
    return f"RadioReference refresh failed: {error.__class__.__name__}."


class FavoritesEditorExternalPreviewController:
    """Mediate one-at-a-time, read-only refreshes against the editor baseline."""

    def __init__(
        self,
        session: FavoritesEditorSession,
        owner_factory: FavoritesEditorExternalRefreshOwnerFactory,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(session, FavoritesEditorSession):
            raise TypeError("External preview controller requires FavoritesEditorSession.")
        if not callable(owner_factory):
            raise TypeError("External preview controller requires an owner factory.")
        if not callable(now):
            raise TypeError("External preview controller clock must be callable.")
        self._session = session
        self._owner_factory = owner_factory
        self._now = now
        self._refresh_lock = threading.Lock()
        self._execution_lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._state_lock = threading.RLock()
        self._execution_active = False
        self._close_requested = False
        self._last_execution_result: FavoritesEditorExternalExecutionResult | None = None
        self._last_execution_error: BaseException | None = None
        self._retained_owner: FavoritesEditorExternalRefreshOwner | None = None
        self._retained_result: FavoritesExternalRefreshResult | None = None
        self._snapshot = FavoritesEditorExternalPreviewSnapshot(
            state=FavoritesEditorExternalPreviewState.READY,
            presentation=None,
            error=None,
            stale_reason=None,
        )

    def snapshot(self) -> FavoritesEditorExternalPreviewSnapshot:
        """Return current immutable renderer state."""

        with self._state_lock:
            if (
                self._session.has_changes
                and not self._execution_active
                and self._snapshot.presentation is not None
                and self._snapshot.state is not FavoritesEditorExternalPreviewState.STALE
            ):
                self._release_retained_locked()
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.STALE,
                    presentation=self._snapshot.presentation,
                    error=None,
                    stale_reason="in-memory Favorites edits changed the preview baseline",
                )
            return self._snapshot

    def _release_retained_locked(self) -> None:
        owner = self._retained_owner
        self._retained_owner = None
        self._retained_result = None
        if owner is not None:
            owner.close()

    def planning_context(self) -> FavoritesEditorExternalPlanningContext | None:
        """Return the current exact refresh evidence while its owner remains active."""

        with self._state_lock:
            if self._execution_active:
                return None
            if self._session.has_changes:
                self._release_retained_locked()
                if self._snapshot.presentation is not None:
                    self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                        state=FavoritesEditorExternalPreviewState.STALE,
                        presentation=self._snapshot.presentation,
                        error=None,
                        stale_reason=("in-memory Favorites edits changed the preview baseline"),
                    )
                return None
            owner = self._retained_owner
            result = self._retained_result
            if owner is None or result is None:
                return None
            try:
                return FavoritesEditorExternalPlanningContext(
                    owner.service,
                    result,
                    owner.source,
                )
            except (TypeError, ValueError):
                self._release_retained_locked()
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.STALE,
                    presentation=self._snapshot.presentation,
                    error=None,
                    stale_reason="retained RadioReference lifecycle evidence changed",
                )
                return None

    def invalidate(self, reason: str) -> FavoritesEditorExternalPreviewSnapshot:
        """Mark retained evidence stale after any editor/source state transition."""

        if type(reason) is not str or not reason.strip():
            raise ValueError("External preview invalidation reason must be non-empty.")
        with self._state_lock:
            if not self._execution_active:
                self._release_retained_locked()
            if self._snapshot.presentation is None:
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.READY,
                    presentation=None,
                    error=None,
                    stale_reason=None,
                )
            else:
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.STALE,
                    presentation=self._snapshot.presentation,
                    error=None,
                    stale_reason=reason,
                )
            return self._snapshot

    @property
    def execution_in_progress(self) -> bool:
        """Return whether one non-cancellable storage transaction is active."""

        with self._state_lock:
            return self._execution_active

    @property
    def last_execution_result(self) -> FavoritesEditorExternalExecutionResult | None:
        """Return retained terminal success evidence, if any."""

        with self._state_lock:
            return self._last_execution_result

    @property
    def last_execution_error(self) -> BaseException | None:
        """Return retained terminal failure evidence, if any."""

        with self._state_lock:
            return self._last_execution_error

    def _require_current_plan_locked(
        self,
        plan: FavoritesEditorExternalPlanningSnapshot,
    ) -> FavoritesEditorExternalRefreshOwner:
        from .favorites_editor_external_planning import (
            FavoritesEditorExternalPlanningSnapshot,
        )

        if type(plan) is not FavoritesEditorExternalPlanningSnapshot:
            raise TypeError("Assisted execution requires an exact aggregate plan.")
        if self._close_requested:
            raise FavoritesEditorExternalPreviewError(
                "The RadioReference preview owner is closing."
            )
        if self._execution_active:
            raise FavoritesEditorExternalPreviewError(
                "An assisted execution is already in progress."
            )
        if self._session.has_changes:
            raise FavoritesEditorExternalPreviewError(
                "Assisted execution is unavailable while manual edits exist."
            )
        owner = self._retained_owner
        result = self._retained_result
        if owner is None or result is None or plan.refresh_result is not result:
            raise FavoritesEditorExternalPreviewError(
                "Assisted execution plan does not retain the current exact refresh."
            )
        if owner.service.lifecycle_snapshot != result.lifecycle_snapshot:
            raise FavoritesEditorExternalPreviewError(
                "Assisted execution lifecycle evidence is stale."
            )
        if self._session.baseline_snapshot != plan.write_plan.baseline_snapshot:
            raise FavoritesEditorExternalPreviewError(
                "Assisted execution plan does not match the editor baseline."
            )
        return owner

    def review_external_execution(
        self,
        plan: FavoritesEditorExternalPlanningSnapshot,
    ) -> FavoritesEditorExternalExecutionReview:
        """Derive a separate full token for the current exact retained plan."""

        from .favorites_editor_external_execution import (
            review_favorites_editor_external_execution,
        )

        with self._state_lock:
            self._require_current_plan_locked(plan)
            return review_favorites_editor_external_execution(
                plan,
                self._session.storage,
            )

    def execute_external_plan(
        self,
        plan: FavoritesEditorExternalPlanningSnapshot,
        confirmation_token: str,
    ) -> FavoritesEditorExternalExecutionResult:
        """Execute once, retain terminal evidence, and consume the refresh owner."""

        from .favorites_editor_external_execution import (
            execute_favorites_editor_external_plan,
        )

        if not self._execution_lock.acquire(blocking=False):
            raise FavoritesEditorExternalPreviewError(
                "An assisted execution is already in progress."
            )
        try:
            with self._state_lock:
                owner = self._require_current_plan_locked(plan)
                self._execution_active = True
                self._last_execution_result = None
                self._last_execution_error = None
            try:
                result = execute_favorites_editor_external_plan(
                    plan,
                    owner.lifecycle,
                    self._session.storage,
                    confirmation_token,
                )
                self._session.adopt_external_execution(result.durable_result)
                with self._state_lock:
                    self._last_execution_result = result
                return result
            except BaseException as error:
                with self._state_lock:
                    self._last_execution_error = error
                raise
            finally:
                with self._state_lock:
                    self._execution_active = False
                    self._release_retained_locked()
                    self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                        state=FavoritesEditorExternalPreviewState.STALE,
                        presentation=self._snapshot.presentation,
                        error=None,
                        stale_reason="assisted execution consumed the retained refresh",
                    )
        finally:
            self._execution_lock.release()

    def cancel(self) -> FavoritesEditorExternalPreviewSnapshot:
        """Request cancellation and retain any prior immutable presentation."""

        self._cancel_requested.set()
        with self._state_lock:
            if self._snapshot.state is FavoritesEditorExternalPreviewState.REFRESHING:
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.CANCELLED,
                    presentation=self._snapshot.presentation,
                    error=None,
                    stale_reason=None,
                )
            return self._snapshot

    def refresh(self) -> FavoritesEditorExternalPreviewSnapshot:
        """Perform one explicit bounded refresh without applying any preview data."""

        if not self._refresh_lock.acquire(blocking=False):
            raise FavoritesEditorExternalPreviewError(
                "A RadioReference refresh is already in progress."
            )
        owner: FavoritesEditorExternalRefreshOwner | None = None
        try:
            with self._state_lock:
                if self._execution_active:
                    raise FavoritesEditorExternalPreviewError(
                        "An assisted execution is already in progress."
                    )
            self._cancel_requested.clear()
            started_at = self._now()
            baseline = self._session.baseline_snapshot
            if self._session.has_changes:
                self.invalidate("unreviewed in-memory Favorites edits")
                raise FavoritesEditorExternalPreviewError(
                    "RadioReference refresh is unavailable while unreviewed Favorites edits exist."
                )
            with self._state_lock:
                retained = self._snapshot.presentation
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.REFRESHING,
                    presentation=retained,
                    error=None,
                    stale_reason=None,
                )

            owner = self._owner_factory()
            lifecycle_snapshot = owner.service.lifecycle_snapshot
            if lifecycle_snapshot.favorites_snapshot != baseline:
                raise FavoritesEditorExternalPreviewError(
                    "RadioReference refresh was blocked because a fresh Favorites read "
                    "does not exactly match the editor baseline. Reload the editor."
                )

            result = owner.service.refresh()
            if self._cancel_requested.is_set():
                raise FavoritesEditorExternalPreviewError(
                    "RadioReference refresh was cancelled; its result was not adopted."
                )
            if self._session.has_changes or self._session.baseline_snapshot != baseline:
                self.invalidate("Favorites editor state changed during refresh")
                raise FavoritesEditorExternalPreviewError(
                    "RadioReference refresh completed after the Favorites editor state "
                    "changed; its result was not adopted."
                )
            presentation = present_favorites_editor_external_preview(
                result,
                source=owner.source,
                started_at=started_at,
                completed_at=self._now(),
            )
            previous_owner: FavoritesEditorExternalRefreshOwner | None
            with self._state_lock:
                previous_owner = self._retained_owner
                self._retained_owner = owner
                self._retained_result = result
                owner = None
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.SUCCEEDED,
                    presentation=presentation,
                    error=None,
                    stale_reason=None,
                )
                adopted = self._snapshot
            if previous_owner is not None:
                previous_owner.close()
            return adopted
        except Exception as error:
            message = _redacted_error(error)
            with self._state_lock:
                if self._cancel_requested.is_set():
                    self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                        state=FavoritesEditorExternalPreviewState.CANCELLED,
                        presentation=self._snapshot.presentation,
                        error=None,
                        stale_reason=None,
                    )
                elif self._snapshot.state is not FavoritesEditorExternalPreviewState.STALE:
                    self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                        state=FavoritesEditorExternalPreviewState.FAILED,
                        presentation=self._snapshot.presentation,
                        error=message,
                        stale_reason=None,
                    )
            raise FavoritesEditorExternalPreviewError(message) from None
        finally:
            try:
                if owner is not None:
                    owner.close()
            finally:
                self._refresh_lock.release()

    def close(self) -> None:
        """Cancel pending adoption and close any retained planning lifecycle."""

        self._cancel_requested.set()
        with self._state_lock:
            self._close_requested = True
            if not self._execution_active:
                self._release_retained_locked()


__all__ = [
    "FavoritesEditorExternalFieldPresentation",
    "FavoritesEditorExternalPreviewController",
    "FavoritesEditorExternalPreviewError",
    "FavoritesEditorExternalPlanningContext",
    "FavoritesEditorExternalPreviewPresentation",
    "FavoritesEditorExternalPreviewSnapshot",
    "FavoritesEditorExternalPreviewState",
    "FavoritesEditorExternalRecordPresentation",
    "FavoritesEditorExternalRefreshOwner",
    "FavoritesEditorExternalRefreshOwnerFactory",
    "FavoritesEditorRadioReferenceRefreshOwnerFactory",
    "present_favorites_editor_external_preview",
]
