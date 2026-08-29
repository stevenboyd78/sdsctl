"""Read-only external refresh presentation for the Favorites editor."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

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
)
from .favorites_external_refresh import FavoritesExternalRefreshResult
from .radioreference import RadioReferenceError


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
            raise TypeError(
                "RadioReference preview requires the production source factory."
            )

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
                "unobserved"
                if field.external_state is None
                else field.external_state.value
            ),
            external_value=field.external_value,
        )
        for field in record.fields
    )
    return FavoritesEditorExternalRecordPresentation(
        kind=record.kind.value,
        local_target=_local_target(record),
        external_record_id="local-only" if identity is None else identity.record_id,
        observed_at=(
            "unobserved" if evidence is None else evidence.observed_at.isoformat()
        ),
        revision=(
            "unavailable"
            if evidence is None or evidence.revision is None
            else evidence.revision
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
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(f"Favorites editor refresh {label} time must be aware.")
    if any(
        observation.identity.source != source
        for observation in result.observations
    ):
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
        self._cancel_requested = threading.Event()
        self._state_lock = threading.RLock()
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
                and self._snapshot.presentation is not None
                and self._snapshot.state
                is not FavoritesEditorExternalPreviewState.STALE
            ):
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.STALE,
                    presentation=self._snapshot.presentation,
                    error=None,
                    stale_reason="in-memory Favorites edits changed the preview baseline",
                )
            return self._snapshot

    def invalidate(self, reason: str) -> FavoritesEditorExternalPreviewSnapshot:
        """Mark retained evidence stale after any editor/source state transition."""

        if type(reason) is not str or not reason.strip():
            raise ValueError("External preview invalidation reason must be non-empty.")
        with self._state_lock:
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
            self._cancel_requested.clear()
            started_at = self._now()
            baseline = self._session.baseline_snapshot
            if self._session.has_changes:
                self.invalidate("unreviewed in-memory Favorites edits")
                raise FavoritesEditorExternalPreviewError(
                    "RadioReference refresh is unavailable while unreviewed Favorites "
                    "edits exist."
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
            with self._state_lock:
                self._snapshot = FavoritesEditorExternalPreviewSnapshot(
                    state=FavoritesEditorExternalPreviewState.SUCCEEDED,
                    presentation=presentation,
                    error=None,
                    stale_reason=None,
                )
                return self._snapshot
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


__all__ = [
    "FavoritesEditorExternalFieldPresentation",
    "FavoritesEditorExternalPreviewController",
    "FavoritesEditorExternalPreviewError",
    "FavoritesEditorExternalPreviewPresentation",
    "FavoritesEditorExternalPreviewSnapshot",
    "FavoritesEditorExternalPreviewState",
    "FavoritesEditorExternalRecordPresentation",
    "FavoritesEditorExternalRefreshOwner",
    "FavoritesEditorExternalRefreshOwnerFactory",
    "FavoritesEditorRadioReferenceRefreshOwnerFactory",
    "present_favorites_editor_external_preview",
]
