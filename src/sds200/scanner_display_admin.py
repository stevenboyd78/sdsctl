"""Bounded administrator reviews for one explicitly configured display profile.

Authorization belongs to the transport adapter. No user-supplied path, filename,
identity or scanner command enters this controller. Pending uploads live only in
memory, expire, and are never committed by preview, shutdown or browser restart.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .daemon_display_profile import display_profile_projection
from .scanner_display_configuration import ScannerDisplayConfiguration
from .scanner_display_profile_cli import _reload
from .scanner_display_profile_state import DisplayProfilePreview
from .scanner_display_upload import ScannerDisplayUpload

REVIEW_SECONDS = 300


class DisplayAdminConflict(ValueError):
    def __init__(self) -> None:
        super().__init__("Review unavailable, expired or changed. Inspect status and review again.")


class DisplayAdminBusy(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class _Review:
    user: str
    token: str
    expires: float
    preview: DisplayProfilePreview
    upload: bool


class ScannerDisplayProfileAdmin:
    def __init__(
        self,
        configuration: ScannerDisplayConfiguration,
        *,
        recording_directory: Path,
        daemon_socket_path: Path,
        allow_upload: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(configuration, ScannerDisplayConfiguration)
            or type(allow_upload) is not bool
        ):
            raise ValueError("Explicit profile administrator configuration is required.")
        configuration.require_separate_recordings(recording_directory)
        if not isinstance(daemon_socket_path, Path) or not daemon_socket_path.is_absolute():
            raise ValueError("An explicit local daemon socket is required.")
        self.configuration = configuration
        self.allow_upload = allow_upload
        self._socket = daemon_socket_path
        self._repository = configuration.repository()
        self._upload = ScannerDisplayUpload(configuration)
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: _Review | None = None
        self._closed = False

    @contextmanager
    def _access(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise DisplayAdminBusy()
        try:
            if self._closed:
                raise DisplayAdminBusy()
            if self._pending is not None and self._pending.expires <= self._clock():
                self._discard()
            yield
        finally:
            self._lock.release()

    def _discard(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            repository = self._upload if pending.upload else self._repository
            repository.cancel(pending.preview)

    def status(self) -> dict[str, object]:
        with self._access():
            snapshot = self._repository.inspect()
            checked_at = datetime.now(UTC)
            return {
                **display_profile_projection(snapshot),
                "configured_source": {
                    "source_id": str(self.configuration.binding.source_id),
                    "source_kind": self.configuration.binding.source_kind.value,
                    "path": str(self.configuration.source_path),
                },
                "status_checked_at": checked_at.isoformat(),
                "source_path": str(self.configuration.source_path),
                "scanner_target": self.configuration.scanner_target,
                "upload_enabled": self.allow_upload,
                "review_pending": self._pending is not None,
            }

    def preview(self, user: str, data: bytes | None = None) -> dict[str, object]:
        with self._access():
            if self._pending is not None:
                raise DisplayAdminConflict()
            if data is not None and not self.allow_upload:
                raise PermissionError("Managed profile uploads are disabled.")
            now = datetime.now(UTC)
            preview = (
                self._repository.prepare(self.configuration.binding, acquired_at=now)
                if data is None
                else self._upload.prepare(data, acquired_at=now)
            )
            token = secrets.token_hex(32)
            self._pending = _Review(
                user, token, self._clock() + REVIEW_SECONDS, preview, data is not None
            )
            return {
                "review_id": token,
                "revision": preview.profile.revision,
                "previous_revision": preview.previous_revision,
                "source_changed": preview.source_changed,
                "descriptor": preview.profile.as_dict(),
                "kind": "upload" if data is not None else "refresh",
                "expires_in_seconds": REVIEW_SECONDS,
            }

    def _take(self, user: str, token: str) -> _Review:
        pending = self._pending
        if (
            pending is None
            or pending.user != user
            or not secrets.compare_digest(pending.token, token)
        ):
            raise DisplayAdminConflict()
        self._pending = None  # Remove before mutation; browser retry cannot replay it.
        return pending

    def cancel(self, user: str, token: str) -> dict[str, object]:
        with self._access():
            pending = self._take(user, token)
            repository = self._upload if pending.upload else self._repository
            repository.cancel(pending.preview)
            return {"status": "cancelled", "changed": False}

    def commit(
        self, user: str, token: str, *, confirm_source_change: bool = False
    ) -> dict[str, object]:
        with self._access():
            pending = self._take(user, token)
            repository = self._upload if pending.upload else self._repository
            accepted = repository.commit(
                pending.preview,
                imported_at=datetime.now(UTC),
                confirm_source_change=confirm_source_change,
            )
            outcome = self._reload(expected_revision=accepted.profile.revision)
            return {"status": "accepted", "revision": accepted.profile.revision, **outcome}

    def _reload(self, *, expected_revision: str | None = None) -> dict[str, object]:
        try:
            result = _reload(self.configuration, self._socket)
        except Exception:
            return {
                "daemon_reload": "unconfirmed",
                "message": "Daemon reload is unconfirmed. "
                "Inspect status, then reload accepted state; do not repeat the import.",
            }
        accepted = result.get("accepted")
        if expected_revision is not None and (
            not isinstance(accepted, dict) or accepted.get("revision") != expected_revision
        ):
            return {
                "daemon_reload": "different_revision",
                "message": "This review was accepted, but the daemon reports a different revision. "
                "Another administrator may have changed it. Inspect current status.",
            }
        return {"daemon_reload": "confirmed", "daemon_accepted": accepted}

    def reload(self) -> dict[str, object]:
        with self._access():
            # This never imports source edits or consumes a pending review.
            return self._reload()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._discard()
