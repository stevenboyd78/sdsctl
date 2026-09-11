"""Internal no-matching-record reconciliation; never permission to authenticate.

Absence from retained history is not proof that an intent never existed. This
explicit operation archives the current evidence and fences delayed preparations
without inventing an approval, pruning history or changing native stopped mode.
No CLI, native-message action or installed browser adapter is connected.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .browser_device_native import _private_read, load_browser_native_configuration
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _COLUMNS, _hex, _integer, _timestamp
from .browser_device_resume_archive import inspect_resume_archive
from .browser_device_resume_maintenance import (
    _ARCHIVE_BYTES,
    _REVIEW_SECONDS,
    BrowserResumeMaintenance,
    BrowserResumeRetirementEvidence,
    _encoded,
    _fingerprint,
)

_OPERATION = "reconcile-missing-native-resume-intent"


class BrowserResumeReconciliationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Missing native resume intent reconciliation was refused or could not be confirmed. "
            "Retain the ledger and any archive for review. Do not replay a request, "
            "restore an archive or clear browser pause."
        )


@dataclass(frozen=True, slots=True)
class BrowserResumeReconciliationReview:
    """Private, exact intent/snapshot binding; not a bearer credential or consent UI."""

    fingerprint: str = field(repr=False)
    intent: str = field(repr=False)
    revision: int
    mode: RecoveryMode
    approvals: int
    created_at: float
    expires_at: float


class BrowserResumeReconciliation:
    """Advance a stopped ledger revision only after explicit durable review.

    Supports existing schemas 1 and 2 without migration. Refuses any matching
    retained row (including terminal rows), and any other prepared/claimed row.
    Same-account/root callers, chosen archives and private input writers are
    trusted, as for native history retirement. No credentials or network needed.
    """

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        try:
            self._history = BrowserResumeMaintenance(root, clock=clock)
            self._configuration = self._history._configuration
            self._recovery = self._history._recovery
        except Exception:
            raise BrowserResumeReconciliationError() from None

    def _snapshot(
        self, db: sqlite3.Connection, now: float, intent: str,
    ) -> dict[str, object]:
        if (not _hex(intent) or not _timestamp(now)
                or load_browser_native_configuration(self._configuration.root)
                != self._configuration
                or db.execute("PRAGMA journal_mode").fetchone() != ("delete",)):
            raise BrowserResumeReconciliationError()
        # Both connection contexts validate schema before reaching this method.
        version = db.execute("PRAGMA user_version").fetchone()[0]
        state = self._recovery._load(db, now, correct_clock=False)
        if (state.mode is RecoveryMode.ACTIVE or not _integer(state.revision + 1)
                or now < state.observed_at):
            raise BrowserResumeReconciliationError()
        rows = ([] if version == 1 else
                db.execute("SELECT * FROM browser_resume ORDER BY revision,digest").fetchall())
        approvals = [dict(zip(_COLUMNS, row, strict=True)) for row in rows]
        if any(row["intent"] == intent or row["phase"] in {"prepared", "claimed"}
               for row in approvals):
            raise BrowserResumeReconciliationError()
        return {"identity": self._configuration.identity, "profile": str(self._configuration.root),
                "schema": version, "intent": intent, "state": asdict(state), "approvals": approvals}

    @staticmethod
    def _review(
        snapshot: dict[str, object], created: float,
    ) -> BrowserResumeReconciliationReview:
        state, rows, intent = snapshot["state"], snapshot["approvals"], snapshot["intent"]
        assert isinstance(state, dict) and isinstance(rows, list) and isinstance(intent, str)
        return BrowserResumeReconciliationReview(
            _fingerprint(snapshot), intent, state["revision"], RecoveryMode(state["mode"]),
            len(rows), created, created + _REVIEW_SECONDS,
        )

    @staticmethod
    def _validate_review(review: BrowserResumeReconciliationReview) -> None:
        if (not isinstance(review, BrowserResumeReconciliationReview)
                or not _hex(review.fingerprint) or not _hex(review.intent)
                or not _integer(review.revision)
                or not isinstance(review.mode, RecoveryMode) or review.mode is RecoveryMode.ACTIVE
                or type(review.approvals) is not int or not 0 <= review.approvals <= 128
                or not _timestamp(review.created_at) or not _timestamp(review.expires_at)
                or review.expires_at - review.created_at != _REVIEW_SECONDS):
            raise BrowserResumeReconciliationError()

    def review(self, *, browser_intent: str) -> BrowserResumeReconciliationReview:
        """Read-only absence review; no clock repair, schema migration or state reset."""
        try:
            now = self._recovery._now()
            if not _timestamp(now + _REVIEW_SECONDS):
                raise BrowserResumeReconciliationError()
            with self._recovery._inspection() as db:
                return self._review(self._snapshot(db, now, browser_intent), now)
        except Exception:
            raise BrowserResumeReconciliationError() from None

    def reconcile(
        self, review: BrowserResumeReconciliationReview, *, archive: Path,
    ) -> BrowserResumeRetirementEvidence:
        """One-use stopped revision fence, archived before SQLite commit.

        The write lock serializes against preparation. If prepare wins, the
        reviewed snapshot no longer matches; if reconciliation wins, a delayed
        prepare's expected revision is obsolete. Neither result permits sign-in.
        Any exception retains evidence for read-only confirmation, not retry.
        """
        try:
            self._validate_review(review)
            now = self._recovery._now()
            if not review.created_at <= now < review.expires_at:
                raise BrowserResumeReconciliationError()
            with self._recovery._connection() as db:
                before = self._snapshot(db, now, review.intent)
                if self._review(before, review.created_at) != review:
                    raise BrowserResumeReconciliationError()
                state = self._recovery._load(db, now, correct_clock=False)
                revised = replace(state, revision=state.revision + 1, observed_at=now)
                after = {**before, "state": asdict(revised)}
                self._history._archive(archive, {
                    "version": 1, "operation": _OPERATION, "review": asdict(review),
                    "before": before, "after": after,
                })
                finished = self._recovery._now()
                if not now <= finished < review.expires_at:
                    raise BrowserResumeReconciliationError()
                self._recovery._save(db, revised)
                if self._snapshot(db, now, review.intent) != after:
                    raise BrowserResumeReconciliationError()
            return self.confirm(review, archive=archive, browser_intent=review.intent)
        except Exception:
            raise BrowserResumeReconciliationError() from None

    def confirm(
        self, review: BrowserResumeReconciliationReview, *, archive: Path, browser_intent: str,
    ) -> BrowserResumeRetirementEvidence:
        """Read-only exact after-state check, including after a lost acknowledgement.

        The trusted worker adapter selects the review/archive out of band and
        supplies its exact pending intent. Archive existence alone is not success.
        Evidence means only no matching retained row plus a stopped revision fence;
        it does not establish historical absence, server state or browser consent.
        """
        try:
            self._validate_review(review)
            if (not _hex(browser_intent) or browser_intent != review.intent
                    or archive.is_relative_to(self._configuration.root)):
                raise BrowserResumeReconciliationError()
            raw = _private_read(archive.parent, archive.name, _ARCHIVE_BYTES)
            plan = inspect_resume_archive(raw, kind="reconcile",
                identity=self._configuration.identity, profile=self._configuration.root,
                expected_review=_encoded(asdict(review)))
            with self._recovery._inspection() as db:
                current = self._snapshot(db, self._recovery._now(), browser_intent)
                if _encoded(current) != plan.after:
                    raise BrowserResumeReconciliationError()
            if _private_read(archive.parent, archive.name, _ARCHIVE_BYTES) != raw:
                raise BrowserResumeReconciliationError()
            return BrowserResumeRetirementEvidence(
                self._configuration.identity, browser_intent, review.fingerprint,
                plan.mode, plan.proposed_revision,
            )
        except Exception:
            raise BrowserResumeReconciliationError() from None
