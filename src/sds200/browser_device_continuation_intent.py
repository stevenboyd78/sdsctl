"""Internal administrator intent journal, NOT a runnable continuation grant.

No CLI, native request, credential operation, ledger mutation or online action.
Any journal/sidecar blocks normal startup and requests until a future separately
reviewed successor activation exists. Preserve uncertain files; never replay.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .browser_device_bundle import _json
from .browser_device_continuation import _released_checkpoint
from .browser_device_guard_release import _connect, _inode, _present
from .browser_device_handoff import BrowserRecoveryHandoff
from .browser_device_native import _private_read
from .browser_device_profile_access import browser_profile_access
from .browser_device_recovery import BrowserDeviceRecovery, RecoveryMode, _object
from .browser_device_registration import _matches
from .browser_device_resume import _hex
from .browser_device_startup import _launch_lock

INTENT_JOURNAL = ".sdsctl-browser-continuation-intent.sqlite"
_SIDECARS = ("-journal", "-wal", "-shm")
_SCHEMA = ("CREATE TABLE intent (id INTEGER PRIMARY KEY CHECK(id=1), "
           "phase TEXT NOT NULL, body BLOB NOT NULL)")
_SECONDS = 120


class BrowserContinuationIntentError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Administrator continuation intent is busy, unsafe, stale or unconfirmed. "
            "Keep the stopped profile and all evidence; do not delete state or retry. "
            "Use exact read-only confirmation. No native activation or sign-in occurred.")


@dataclass(frozen=True, slots=True)
class BrowserContinuationIntentReview:
    directory: Path = field(repr=False)
    profile: Path = field(repr=False)
    origin: str = field(repr=False)
    device_id: str = field(repr=False)
    release_id: str = field(repr=False)
    intent_id: str = field(repr=False)
    confirmation: str = field(repr=False)
    native_revision: int
    expires_at: float
    purpose: Literal["enable-fresh-resume-review"] = "enable-fresh-resume-review"


@dataclass(frozen=True, slots=True)
class BrowserContinuationIntentEvidence:
    identity: str = field(repr=False)
    release_id: str = field(repr=False)
    intent_id: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    native_revision: int
    mode: RecoveryMode = RecoveryMode.PAUSED
    purpose: Literal["enable-fresh-resume-review"] = "enable-fresh-resume-review"


def has_continuation_intent(root: Path) -> bool:
    """Presence is a stop condition, including empty files and dangling sidecars."""
    return any(_present(root / (INTENT_JOURNAL + suffix)) for suffix in ("", *_SIDECARS))


def _snapshot(handoff: BrowserRecoveryHandoff, release_id: str) -> dict[str, object]:
    identity, origin, device, revision, fingerprint = _released_checkpoint(handoff, release_id)
    return {"identity": identity, "origin": origin, "device_id": device,
            "revision": revision, "release_fingerprint": fingerprint,
            "targets": dict(handoff._targets), "operation_id": handoff._operation}


def _rows(db: sqlite3.Connection, *, phase: str) -> bytes:
    if (db.execute("PRAGMA user_version").fetchone() != (1,)
            or db.execute("PRAGMA quick_check").fetchall() != [("ok",)]
            or db.execute("SELECT type, name, sql FROM sqlite_schema").fetchall()
                != [("table", "intent", _SCHEMA)]):
        raise ValueError()
    rows = db.execute("SELECT id, phase, body FROM intent LIMIT 2").fetchall()
    if (len(rows) != 1 or rows[0][:2] != (1, phase) or type(rows[0][2]) is not bytes
            or not 0 < len(rows[0][2]) <= 32768):
        raise ValueError()
    return rows[0][2]


def _prepare(path: Path, body: bytes) -> None:
    # The caller exclusively created and bound this name. Prepared is NOT consent
    # completion. Directory durability must succeed before a complete row exists.
    with closing(_connect(path, readonly=False)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(_SCHEMA)
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO intent VALUES (1, 'prepared', ?)", (body,))
        db.commit()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _complete(path: Path, body: bytes, recheck: Callable[[], None]) -> None:
    # SQLite synchronous=EXTRA + DELETE owns this commit and directory sync.
    with closing(_connect(path, readonly=False)) as db:
        db.execute("BEGIN IMMEDIATE")
        if _rows(db, phase="prepared") != body:
            raise ValueError()
        db.execute("UPDATE intent SET phase='complete' WHERE id=1")
        recheck()
        db.commit()


def _read(path: Path) -> bytes:
    def no_sidecars() -> None:
        if any(_present(Path(str(path) + suffix)) for suffix in _SIDECARS):
            raise ValueError()

    no_sidecars()
    before = _private_read(path.parent, path.name, 65536)
    if before[:16] != b"SQLite format 3\x00" or before[18:20] != b"\x01\x01":
        raise ValueError()
    with closing(_connect(path, readonly=True)) as db:
        db.execute("BEGIN")
        body = _rows(db, phase="complete")
    _matches(path, before)
    no_sidecars()
    return body


def _confirmed(handoff: BrowserRecoveryHandoff, release_id: str,
               intent_id: str) -> BrowserContinuationIntentEvidence:
    path = handoff._session._root / INTENT_JOURNAL
    raw = _read(path)
    value = json.loads(raw, object_pairs_hook=_object)
    if (type(value) is not dict or set(value) != {
            "version", "operation", "intent_id", "release_id", "reviewed_at", "approved_at",
            "expires_at", "consent_sha256", "journal_binding", "evidence", "purpose"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["operation"] != "record-paused-continuation-intent"
            or value["purpose"] != "enable-fresh-resume-review"
            or not _hex(intent_id) or value["intent_id"] != intent_id
            or not _hex(release_id) or value["release_id"] != release_id
            or not _hex(value["consent_sha256"])
            or _json(value["journal_binding"]) != _json(_inode(path))
            or any(type(value[key]) not in (float, int) or not math.isfinite(value[key])
                   or value[key] < 0 for key in ("reviewed_at", "approved_at", "expires_at"))
            or not value["reviewed_at"] <= value["approved_at"] < value["expires_at"]
            or value["expires_at"] != value["reviewed_at"] + _SECONDS
            or _json(value["evidence"]) != _json(_snapshot(handoff, release_id))
            or raw != _json(value)):
        raise ValueError()
    if _read(path) != raw or value["journal_binding"] != _inode(path):
        raise ValueError()
    return BrowserContinuationIntentEvidence(str(value["evidence"]["identity"]), release_id,
        intent_id, hashlib.sha256(raw).hexdigest(), int(value["evidence"]["revision"]))


class BrowserContinuationIntent:
    """Fresh, same-process LOCAL consent only; an advisory checkpoint is not input.

    This records a pending administrator transition, not permission to change a
    native revision. Completed records still block launch. Neither startup nor
    confirmation consumes or executes them. No automatic retries or cleanup.
    """

    def __init__(self, handoff: BrowserRecoveryHandoff, *, release_id: str,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._handoff, self._release_id = handoff, release_id
        self._clock, self._monotonic = clock, monotonic
        self._attempted = False

    def apply(self, *, confirmation: Callable[[BrowserContinuationIntentReview], str | None],
              ) -> BrowserContinuationIntentEvidence | None:
        try:
            if self._attempted or not _hex(self._release_id):
                raise ValueError()
            self._attempted = True
            h = self._handoff
            root, profile = h._session._root, h._session._profile
            with _launch_lock(root, create=False), browser_profile_access(profile, exclusive=False):
                if has_continuation_intent(root):
                    raise ValueError()
                before = _snapshot(h, self._release_id)
                ledger = BrowserDeviceRecovery(profile / "recovery.sqlite", str(before["identity"]))
                # No writes to this transaction: it fences native revisions while
                # shared profile ownership excludes cooperating credential writers.
                with ledger._connection():
                    if _snapshot(h, self._release_id) != before:
                        raise ValueError()
                    now, started = self._clock(), self._monotonic()

                    def time_check() -> float:
                        wall, elapsed = self._clock(), self._monotonic()
                        if (any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                                for v in (now, started, wall, elapsed))
                                or not now <= wall < now + _SECONDS
                                or not 0 <= elapsed - started < _SECONDS):
                            raise ValueError()
                        return wall

                    time_check()
                    intent_id = secrets.token_hex(32)
                    phrase = "RECORD PAUSED INTENT " + intent_id + ":" + secrets.token_hex(16)
                    revision = before["revision"]
                    assert isinstance(revision, int)
                    review = BrowserContinuationIntentReview(root, profile, str(before["origin"]),
                        str(before["device_id"]), self._release_id, intent_id, phrase, revision,
                        now + _SECONDS)
                    answer = confirmation(review)
                    if answer is None:
                        return None

                    def recheck() -> None:
                        if type(answer) is not str or answer != phrase:
                            raise ValueError()
                        time_check()
                        if _snapshot(h, self._release_id) != before:
                            raise ValueError()
                        time_check()

                    recheck()
                    approved = time_check()
                    path = root / INTENT_JOURNAL
                    if has_continuation_intent(root):
                        raise ValueError()
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    os.close(fd)
                    binding = _inode(path)
                    body = _json({"version": 1, "operation": "record-paused-continuation-intent",
                        "purpose": review.purpose,
                        "intent_id": intent_id, "release_id": self._release_id, "reviewed_at": now,
                        "approved_at": approved, "expires_at": review.expires_at,
                        "consent_sha256": hashlib.sha256(phrase.encode()).hexdigest(),
                        "journal_binding": binding, "evidence": before})
                    _prepare(path, body)

                    def commit_check() -> None:
                        recheck()
                        if _inode(path) != binding:
                            raise ValueError()

                    _complete(path, body, commit_check)
                    return _confirmed(h, self._release_id, intent_id)
        except Exception:
            raise BrowserContinuationIntentError() from None

    def confirm(self, *, intent_id: str) -> BrowserContinuationIntentEvidence:
        """Exact completed-record confirmation; no retry, activation or file creation."""
        try:
            h = self._handoff
            with (_launch_lock(h._session._root, create=False),
                  browser_profile_access(h._session._profile, exclusive=False)):
                return _confirmed(h, self._release_id, intent_id)
        except Exception:
            raise BrowserContinuationIntentError() from None
