"""Internal, explicit native resume-history retirement; never permission to resume.

No CLI, native-message or browser caller is connected. A future coordinated
workflow must own browser shutdown/pending-state handling and serialize private
input writers. Same-account/root callers and their selected archives are trusted.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .browser_device_native import _private_read, load_browser_native_configuration
from .browser_device_profile import _platform, _write
from .browser_device_recovery import BrowserDeviceRecovery, RecoveryMode, _object, _State
from .browser_device_resume import _COLUMNS, _hex, _integer, _timestamp, validate_resume_state
from .browser_device_store import BrowserDeviceStore

_REVIEW_SECONDS = 120
_ARCHIVE_BYTES = 256 * 1024
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class BrowserResumeMaintenanceError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Native resume-history retirement was refused or could not be confirmed. "
            "Retain the ledger and any archive for review. Do not replay an approval, "
            "restore an archive or clear browser pause."
        )


@dataclass(frozen=True, slots=True)
class BrowserResumeMaintenanceReview:
    """Private point-in-time review, not a bearer credential or server approval."""

    fingerprint: str = field(repr=False)
    revision: int
    mode: RecoveryMode
    approvals: int
    pending: int
    created_at: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class BrowserResumeMaintenanceResult:
    revision: int
    mode: RecoveryMode
    archived: int
    cancelled: int


def _encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("ascii")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


class BrowserResumeMaintenance:
    """Archive an exact reviewed snapshot before atomically fencing old approvals.

    Requires an existing version-2, stopped ledger. Keeps its latest approval as
    a terminal anchor (schema 2 never accepts an empty history), archives all
    previous evidence, and advances revision without changing the stopped mode.
    File/SQLite completion is intentionally not presented as one transaction:
    an archive may exist without a committed retirement. Confirm is read-only.
    """

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        try:
            _platform()
            self._configuration = load_browser_native_configuration(root)
            self._recovery = BrowserDeviceRecovery(
                root / "recovery.sqlite", self._configuration.identity, clock=clock,
            )
        except Exception:
            raise BrowserResumeMaintenanceError() from None

    def _snapshot(self, db: sqlite3.Connection, now: float) -> dict[str, object]:
        if (load_browser_native_configuration(self._configuration.root) != self._configuration
                or db.execute("PRAGMA user_version").fetchone() != (2,)):
            raise BrowserResumeMaintenanceError()
        state = self._recovery._load(db, now, correct_clock=False)
        if state.mode is RecoveryMode.ACTIVE or not _integer(state.revision + 1):
            raise BrowserResumeMaintenanceError()
        rows = db.execute("SELECT * FROM browser_resume ORDER BY revision,digest").fetchall()
        return {"identity": self._configuration.identity, "profile": str(self._configuration.root),
                "state": asdict(state),
                "approvals": [dict(zip(_COLUMNS, row, strict=True)) for row in rows]}

    def _review(
        self, snapshot: dict[str, object], created: float,
    ) -> BrowserResumeMaintenanceReview:
        state = snapshot["state"]
        rows = snapshot["approvals"]
        assert isinstance(state, dict) and isinstance(rows, list)
        return BrowserResumeMaintenanceReview(
            _fingerprint(snapshot), state["revision"], RecoveryMode(state["mode"]),
            len(rows), sum(row["phase"] in {"prepared", "claimed"} for row in rows),
            created, created + _REVIEW_SECONDS,
        )

    def review(self) -> BrowserResumeMaintenanceReview:
        """Read-only native history summary; no credentials, clock repair or migration."""
        try:
            now = self._recovery._now()
            if not _timestamp(now + _REVIEW_SECONDS):
                raise BrowserResumeMaintenanceError()
            with self._recovery._inspection() as db:
                snapshot = self._snapshot(db, now)
                state = snapshot["state"]
                assert isinstance(state, dict)
                if now < state["observed_at"]:
                    raise BrowserResumeMaintenanceError()
                return self._review(snapshot, now)
        except Exception:
            raise BrowserResumeMaintenanceError() from None

    @staticmethod
    def _validate_review(review: BrowserResumeMaintenanceReview) -> None:
        if (not isinstance(review, BrowserResumeMaintenanceReview)
                or not _hex(review.fingerprint) or not _integer(review.revision)
                or not isinstance(review.mode, RecoveryMode) or review.mode is RecoveryMode.ACTIVE
                or type(review.approvals) is not int or not 1 <= review.approvals <= 128
                or type(review.pending) is not int or not 0 <= review.pending <= 1
                or not _timestamp(review.created_at) or not _timestamp(review.expires_at)
                or review.expires_at - review.created_at != _REVIEW_SECONDS):
            raise BrowserResumeMaintenanceError()

    def _archive(self, path: Path, document: dict[str, object]) -> None:
        BrowserDeviceStore(path)._check(database=False)
        if path.is_relative_to(self._configuration.root) or path.exists() or path.is_symlink():
            raise BrowserResumeMaintenanceError()
        value = _encoded(document)
        if len(value) > _ARCHIVE_BYTES:
            raise BrowserResumeMaintenanceError()
        fd = os.open(path.parent, _FLAGS)
        try:
            info = os.fstat(fd)
            if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
                    or (info.st_dev, info.st_ino) != (path.parent.stat().st_dev,
                                                     path.parent.stat().st_ino)):
                raise BrowserResumeMaintenanceError()
            _write(fd, path.name, value)  # Exclusive creation + file fsync.
            os.fsync(fd)  # Archive directory durable BEFORE changing SQLite.
            if _private_read(path.parent, path.name, _ARCHIVE_BYTES) != value:
                raise BrowserResumeMaintenanceError()
        finally:
            os.close(fd)

    def retire(
        self, review: BrowserResumeMaintenanceReview, *, archive: Path,
    ) -> BrowserResumeMaintenanceResult:
        """Explicit one-use retirement. Never restore, log in or clear browser intent.

        The write lock spans snapshot comparison, durable archive and revision
        fencing. In-flight commits lose their claim/revision on return. Any
        exception retains created files and requires inspection, never replay.
        """
        try:
            self._validate_review(review)
            now = self._recovery._now()
            if not review.created_at <= now < review.expires_at:
                raise BrowserResumeMaintenanceError()
            with self._recovery._connection() as db:
                before = self._snapshot(db, now)
                if self._review(before, review.created_at) != review:
                    raise BrowserResumeMaintenanceError()
                state = self._recovery._load(db, now, correct_clock=False)
                if now < state.observed_at:
                    raise BrowserResumeMaintenanceError()
                revised = replace(state, revision=state.revision + 1, observed_at=now)
                rows = before["approvals"]
                assert isinstance(rows, list)
                anchor = dict(rows[-1])
                if anchor["phase"] in {"prepared", "claimed"}:
                    anchor["phase"] = "cancelled"
                after = {**before, "state": asdict(revised), "approvals": [anchor]}
                document = {"version": 1, "operation": "retire-native-resume-history",
                            "review": asdict(review), "before": before, "after": after}
                self._archive(archive, document)
                # A slow filesystem may outlive the reviewed window; retain its
                # archive but roll back the ledger rather than use stale consent.
                finished = self._recovery._now()
                if not now <= finished < review.expires_at:
                    raise BrowserResumeMaintenanceError()
                db.execute("DELETE FROM browser_resume WHERE digest<>?", (anchor["digest"],))
                db.execute("UPDATE browser_resume SET phase=? WHERE digest=?",
                           (anchor["phase"], anchor["digest"]))
                self._recovery._save(db, revised)
                validate_resume_state(db)
                if self._snapshot(db, now) != after:
                    raise BrowserResumeMaintenanceError()
            return self.confirm(review, archive=archive)
        except Exception:
            raise BrowserResumeMaintenanceError() from None

    def confirm(
        self, review: BrowserResumeMaintenanceReview, *, archive: Path,
    ) -> BrowserResumeMaintenanceResult:
        """Read-only confirmation of exact archived after-state, even after lost ACK.

        Archive existence alone is NOT completion. A changed ledger fails closed;
        this does not restore files, rerun retirement or prove browser/server state.
        """
        try:
            self._validate_review(review)
            if archive.is_relative_to(self._configuration.root):
                raise BrowserResumeMaintenanceError()
            raw = _private_read(archive.parent, archive.name, _ARCHIVE_BYTES)
            document = json.loads(raw, object_pairs_hook=_object)
            if (not isinstance(document, dict)
                    or set(document) != {"version", "operation", "review", "before", "after"}
                    or type(document["version"]) is not int or document["version"] != 1
                    or document["operation"] != "retire-native-resume-history"
                    or _encoded(document["review"]) != _encoded(asdict(review))
                    or _fingerprint(document["before"]) != review.fingerprint):
                raise BrowserResumeMaintenanceError()
            # Reconstruct the exact plan, not an arbitrary after-state from a file.
            before = document["before"]
            after = document["after"]
            if self._review(before, review.created_at) != review:
                raise BrowserResumeMaintenanceError()
            state = _State(**before["state"])
            revised_at = after["state"]["observed_at"]
            if (not _timestamp(revised_at)
                    or not review.created_at <= revised_at < review.expires_at):
                raise BrowserResumeMaintenanceError()
            anchor = dict(before["approvals"][-1])
            if anchor["phase"] in {"prepared", "claimed"}:
                anchor["phase"] = "cancelled"
            expected = {**before, "state": asdict(replace(
                state, revision=state.revision + 1, observed_at=revised_at)), "approvals": [anchor]}
            if _encoded(after) != _encoded(expected):
                raise BrowserResumeMaintenanceError()
            with self._recovery._inspection() as db:
                current = self._snapshot(db, self._recovery._now())
                if _encoded(current) != _encoded(expected):
                    raise BrowserResumeMaintenanceError()
            return BrowserResumeMaintenanceResult(
                state.revision + 1, RecoveryMode(state.mode), review.approvals - 1, review.pending,
            )
        except Exception:
            raise BrowserResumeMaintenanceError() from None
