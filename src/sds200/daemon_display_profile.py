"""Cached display-only projection for one explicitly configured daemon owner.

Reload reads accepted state, never imports source edits. No scanner commands,
network reads, watcher, per-frame file I/O or access to recording services.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from .scanner_display_configuration import (
    ScannerDisplayConfiguration,
    ScannerDisplayConfigurationError,
)
from .scanner_display_profile_storage import (
    DiskDisplayProfileSnapshot,
    DisplayProfileStorageError,
)


def display_profile_projection(snapshot: DiskDisplayProfileSnapshot) -> dict[str, object]:
    """Fresh allowlisted payload; no raw source, paths, or unrelated configuration."""
    imported = snapshot.profile.last_good
    accepted = None
    if imported is not None:
        provenance = imported.provenance
        accepted = {
            "revision": imported.profile.revision,
            "source_id": str(provenance.binding.source_id),
            "source_kind": provenance.binding.source_kind.value,
            "acquired_at": provenance.acquired_at.isoformat(),
            "imported_at": provenance.imported_at.isoformat(),
            "descriptor": imported.profile.as_dict(),
        }
    return {
        "schema_version": 1,
        "endpoint_id": str(snapshot.profile.endpoint_id),
        "profile_status": snapshot.profile.status.value,
        "source_status": snapshot.source_status.value,
        "accepted": accepted,
    }


class DaemonDisplayProfile:
    def __init__(
        self,
        configuration: ScannerDisplayConfiguration,
        scanner_target: Callable[[], str],
    ) -> None:
        if not isinstance(configuration, ScannerDisplayConfiguration) or not callable(
            scanner_target
        ):
            raise ScannerDisplayConfigurationError()
        self._configuration = configuration
        self._scanner_target = scanner_target
        self._repository = configuration.repository()
        self._lock = threading.Lock()
        self._snapshot: DiskDisplayProfileSnapshot | None = None
        self._failure: str | None = None
        self._checked_at: datetime | None = None
        # Configuration opt-in must fail before scanner startup if initial state/target is wrong.
        self.reload()

    def reload(self) -> dict[str, object]:
        """Local-administrator operation: reload accepted state, NOT the raw source."""
        with self._lock:
            try:
                self._configuration.require_scanner_target(self._scanner_target())
                snapshot = self._repository.inspect()
                self._configuration.require_scanner_target(self._scanner_target())
            except (ScannerDisplayConfigurationError, DisplayProfileStorageError) as exc:
                self._snapshot = None
                self._failure = (
                    exc.category.value
                    if isinstance(exc, DisplayProfileStorageError)
                    else "endpoint_mismatch"
                )
                self._checked_at = datetime.now(UTC)
                raise
            self._snapshot, self._failure = snapshot, None
            self._checked_at = datetime.now(UTC)
            return self._projection()

    def _projection(self) -> dict[str, object]:
        if self._snapshot is None:
            result: dict[str, object] = {"schema_version": 1, "accepted": None}
        else:
            result = display_profile_projection(self._snapshot)
        return {
            **result,
            "endpoint_id": str(self._configuration.binding.endpoint_id),
            "configured": True,
            "failure": self._failure,
            "checked_at": None if self._checked_at is None else self._checked_at.isoformat(),
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            try:
                self._configuration.require_scanner_target(self._scanner_target())
            except ScannerDisplayConfigurationError:
                # Once a selection mismatch is observed, only an explicit reload can restore it.
                self._snapshot = None
                self._failure = "endpoint_mismatch"
            return self._projection()
