"""In-memory, explicit-review lifecycle for scanner display profile imports.

No acquisition, disk persistence, authorization, scanner commands or renderer
hooks live here. The integration layer must supply its selected endpoint/source
identities and perform authorized reads. A UUID binding prevents accidental
cross-selection; it does not attest to the contents of a manually chosen file.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from .scanner_display_profile import (
    ScannerDisplayProfile,
    ScannerDisplayProfileError,
    parse_scanner_display_profile,
)


class DisplayProfileStateError(ValueError):
    """Sanitized refusal for a mismatched, obsolete or unreviewed operation."""


class DisplayProfileSourceKind(StrEnum):
    MANUAL_IMPORT = "manual_import"
    FAVORITES_SYNC = "favorites_sync"


class DisplayProfileRefreshFailure(StrEnum):
    UNAVAILABLE = "unavailable"
    INVALID_PROFILE = "invalid_profile"
    SOURCE_CHANGED_DURING_READ = "source_changed_during_read"


class DisplayProfileStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    LAST_IMPORTED = "last_imported"
    REFRESH_FAILED = "refresh_failed"


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise DisplayProfileStateError("An explicitly timezone-aware timestamp is required.")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DisplayProfileBinding:
    """Opaque selection IDs, never a path, credential, host name or model marker."""

    endpoint_id: UUID
    source_id: UUID
    source_kind: DisplayProfileSourceKind

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_id, UUID) or not isinstance(self.source_id, UUID):
            raise DisplayProfileStateError("Endpoint and source selection IDs must be UUIDs.")
        if not isinstance(self.source_kind, DisplayProfileSourceKind):
            raise DisplayProfileStateError("Unsupported profile source kind.")


@dataclass(frozen=True, slots=True)
class DisplayProfileProvenance:
    binding: DisplayProfileBinding
    acquired_at: datetime
    imported_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.binding, DisplayProfileBinding):
            raise DisplayProfileStateError("An explicit source binding is required.")
        acquired_at, imported_at = _utc(self.acquired_at), _utc(self.imported_at)
        if imported_at < acquired_at:
            raise DisplayProfileStateError("Import time precedes acquisition time.")
        object.__setattr__(self, "acquired_at", acquired_at)
        object.__setattr__(self, "imported_at", imported_at)


@dataclass(frozen=True, slots=True)
class DisplayProfileImport:
    profile: ScannerDisplayProfile
    provenance: DisplayProfileProvenance


@dataclass(frozen=True, slots=True)
class DisplayProfileRefresh:
    """Opaque ticket: only the exact current ticket from this store is accepted."""

    binding: DisplayProfileBinding
    started_at: datetime


@dataclass(frozen=True, slots=True)
class DisplayProfilePreview:
    refresh: DisplayProfileRefresh
    profile: ScannerDisplayProfile
    acquired_at: datetime
    previous_revision: str | None
    source_changed: bool


@dataclass(frozen=True, slots=True)
class DisplayProfileFailedRefresh:
    refresh: DisplayProfileRefresh
    reason: DisplayProfileRefreshFailure


@dataclass(frozen=True, slots=True)
class DisplayProfileSnapshot:
    endpoint_id: UUID
    generation: int
    last_good: DisplayProfileImport | None
    pending: DisplayProfileRefresh | None
    last_failure: DisplayProfileFailedRefresh | None

    @property
    def status(self) -> DisplayProfileStatus:
        if self.last_failure is not None:
            return DisplayProfileStatus.REFRESH_FAILED
        if self.last_good is None:
            return DisplayProfileStatus.UNAVAILABLE
        return DisplayProfileStatus.LAST_IMPORTED


class ScannerDisplayProfileStore:
    """One endpoint's immutable snapshots and atomic last-good replacement.

    begin_refresh -> authorized read outside this store -> prepare -> review ->
    commit. A newer begin invalidates earlier tickets/previews, even for the
    same binding/content. Failed/cancelled refreshes preserve the last import.
    Successful acquisition never proves current physical-scanner freshness.
    """

    def __init__(self, endpoint_id: UUID) -> None:
        if not isinstance(endpoint_id, UUID):
            raise DisplayProfileStateError("An endpoint selection UUID is required.")
        self._endpoint_id = endpoint_id
        self._lock = threading.Lock()
        self._generation = 0
        self._last_good: DisplayProfileImport | None = None
        self._pending: DisplayProfileRefresh | None = None
        self._preparing: DisplayProfileRefresh | None = None
        self._preview: DisplayProfilePreview | None = None
        self._last_failure: DisplayProfileFailedRefresh | None = None

    def snapshot(self, endpoint_id: UUID) -> DisplayProfileSnapshot:
        with self._lock:
            if endpoint_id != self._endpoint_id:
                raise DisplayProfileStateError("Profile belongs to a different endpoint selection.")
            return DisplayProfileSnapshot(
                self._endpoint_id,
                self._generation,
                self._last_good,
                self._pending,
                self._last_failure,
            )

    def begin_refresh(
        self,
        binding: DisplayProfileBinding,
        *,
        started_at: datetime,
    ) -> DisplayProfileRefresh:
        if (
            not isinstance(binding, DisplayProfileBinding)
            or binding.endpoint_id != self._endpoint_id
        ):
            raise DisplayProfileStateError("Profile belongs to a different endpoint selection.")
        started_at = _utc(started_at)
        with self._lock:
            if self._last_good is not None and started_at < self._last_good.provenance.imported_at:
                raise DisplayProfileStateError("Refresh time precedes the last accepted import.")
            ticket = DisplayProfileRefresh(binding, started_at)
            self._pending, self._preview = ticket, None
            self._preparing = None
            self._generation += 1
            return ticket

    def _require_current(self, ticket: DisplayProfileRefresh) -> None:
        # Equality is insufficient: a copied/foreign ticket must not replay a
        # cancelled or superseded refresh, including an A -> B -> A transition.
        if ticket is None or ticket is not self._pending:
            raise DisplayProfileStateError("Refresh is foreign, completed or superseded.")

    def prepare(
        self,
        ticket: DisplayProfileRefresh,
        data: bytes,
        *,
        acquired_at: datetime,
    ) -> DisplayProfilePreview:
        acquired_at = _utc(acquired_at)
        with self._lock:
            self._require_current(ticket)
            if acquired_at < ticket.started_at:
                raise DisplayProfileStateError("Acquisition time precedes the selected refresh.")
            if self._preview is not None or self._preparing is ticket:
                raise DisplayProfileStateError("This refresh already has a prepared preview.")
            self._preparing = ticket
        # Parsing runs outside the lock, so an old slow read cannot block a new
        # selection. Recheck ticket identity before publishing any result/error.
        try:
            profile = parse_scanner_display_profile(data)
        except (ScannerDisplayProfileError, TypeError):
            self.fail(ticket, DisplayProfileRefreshFailure.INVALID_PROFILE)
            raise
        with self._lock:
            self._require_current(ticket)
            if self._preview is not None:
                raise DisplayProfileStateError("This refresh already has a prepared preview.")
            previous = self._last_good
            preview = DisplayProfilePreview(
                ticket,
                profile,
                acquired_at,
                None if previous is None else previous.profile.revision,
                previous is not None and previous.provenance.binding != ticket.binding,
            )
            self._preview = preview
            self._preparing = None
            self._generation += 1
            return preview

    def commit(
        self,
        preview: DisplayProfilePreview,
        *,
        imported_at: datetime,
        confirm_source_change: bool = False,
    ) -> DisplayProfileImport:
        imported_at = _utc(imported_at)
        if type(confirm_source_change) is not bool:
            raise DisplayProfileStateError("Source-change confirmation must be explicit boolean.")
        with self._lock:
            if preview is None or preview is not self._preview:
                raise DisplayProfileStateError("Preview is foreign, completed or superseded.")
            self._require_current(preview.refresh)
            if preview.source_changed and not confirm_source_change:
                raise DisplayProfileStateError("Review and explicitly confirm the source change.")
            provenance = DisplayProfileProvenance(
                preview.refresh.binding,
                preview.acquired_at,
                imported_at,
            )
            imported = DisplayProfileImport(preview.profile, provenance)
            self._last_good = imported
            self._pending, self._preview, self._last_failure = None, None, None
            self._preparing = None
            self._generation += 1
            return imported

    def fail(
        self,
        ticket: DisplayProfileRefresh,
        reason: DisplayProfileRefreshFailure,
    ) -> None:
        if not isinstance(reason, DisplayProfileRefreshFailure):
            raise DisplayProfileStateError("A sanitized refresh failure category is required.")
        with self._lock:
            self._require_current(ticket)
            self._last_failure = DisplayProfileFailedRefresh(ticket, reason)
            self._pending, self._preview = None, None
            self._preparing = None
            self._generation += 1

    def cancel(self, ticket: DisplayProfileRefresh) -> None:
        with self._lock:
            self._require_current(ticket)
            self._pending, self._preview = None, None
            self._preparing = None
            self._generation += 1
