"""Internal, one-use paused guard release; no browser RPC, CLI, or service wiring.

The guard is NEVER removed. Normal startup requires this exact committed journal
and all original evidence. A partial journal is retained, never repaired/replayed.
Same-account/root code and the filesystem's SQLite durability guarantees remain
trusted; these local receipts do not grant server authority or resume consent.
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

from .browser_device_bundle import _json
from .browser_device_handoff import BrowserRecoveryHandoff
from .browser_device_native import _private_read, load_browser_native_configuration
from .browser_device_profile_access import browser_profile_access
from .browser_device_recovery import BrowserDeviceRecovery, RecoveryMode, _object
from .browser_device_registration import MAINTENANCE_MARKER, _matches
from .browser_device_resume import _hex
from .browser_device_startup import _launch_lock
from .browser_device_store import BrowserDeviceStore

RELEASE_JOURNAL = ".sdsctl-browser-guard-release.sqlite"
_SIDECARS = ("-journal", "-wal", "-shm")
_SCHEMA = ("CREATE TABLE release (id INTEGER PRIMARY KEY CHECK(id=1), "
           "phase TEXT NOT NULL, body BLOB NOT NULL)")
_SECONDS = 120


class BrowserGuardReleaseError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Paused guard release is busy, unsafe, stale or unconfirmed. Retain the guard "
            "and all evidence; do not retry the mutation or remove files. Confirm only "
            "the exact completed release. No browser, service or sign-in was started.")


@dataclass(frozen=True, slots=True)
class BrowserGuardReleaseReview:
    directory: Path = field(repr=False)
    profile: Path = field(repr=False)
    handoff: Path = field(repr=False)
    origin: str = field(repr=False)
    device_id: str = field(repr=False)
    operation_id: str = field(repr=False)
    release_id: str = field(repr=False)
    confirmation: str = field(repr=False)
    native_revision: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class BrowserGuardReleaseEvidence:
    identity: str = field(repr=False)
    operation_id: str = field(repr=False)
    release_id: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    revision: int
    mode: RecoveryMode = RecoveryMode.PAUSED


def _present(path: Path) -> bool:
    # lstat also refuses errors and dangling links; permission errors are not absence.
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def has_guard_release(root: Path) -> bool:
    return any(_present(root / (RELEASE_JOURNAL + suffix)) for suffix in ("", *_SIDECARS))


def _inode(path: Path) -> list[int]:
    info = path.lstat()
    return [info.st_dev, info.st_ino]


def _snapshot(handoff: BrowserRecoveryHandoff, *, browser_running: bool = False,
              ) -> dict[str, object]:
    if not handoff._supervised:
        raise ValueError()
    record, _, proof = handoff._checked(stopped=not browser_running, restored=True)
    ack = handoff._ack(record, proof, stopped=True)
    restored = handoff._restoration(record, ack)
    for name in ("restoration-started.json", "restored.json"):
        _matches(handoff._root / name, restored)
    if proof.mode is not RecoveryMode.PAUSED:
        raise ValueError()
    paths = {
        "guard": handoff._session._root / MAINTENANCE_MARKER,
        "operation": handoff._root / "operation.json",
        "ack": handoff._root / "browser-acknowledgement.json",
        "restoration_started": handoff._root / "restoration-started.json",
        "restored": handoff._root / "restored.json",
        "supervisor": handoff._root / "supervisor.json",
        "native_ledger": handoff._session._profile / "recovery.sqlite",
    }
    return {"targets": handoff._targets, "operation_id": handoff._operation,
            "browser_intent": handoff._intent, "identity": proof.identity,
            "revision": proof.revision, "mode": str(proof.mode),
            "handoff_sha256": hashlib.sha256(record).hexdigest(),
            "ack_sha256": ack.receipt_sha256,
            "bindings": {name: _inode(path) for name, path in paths.items()}}


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if any(_present(Path(str(path) + suffix)) for suffix in _SIDECARS):
        raise ValueError()
    BrowserDeviceStore(path)._check()
    db = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                         uri=True, timeout=0)
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        if readonly:
            db.execute("PRAGMA query_only=ON")
        else:
            db.execute("PRAGMA synchronous=EXTRA")
            if db.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise ValueError()
        return db
    except BaseException:
        db.close()
        raise


def _rows(db: sqlite3.Connection, *, phase: str) -> bytes:
    if (db.execute("PRAGMA user_version").fetchone() != (1,)
            or db.execute("PRAGMA quick_check").fetchall() != [("ok",)]
            or db.execute("SELECT type, name, sql FROM sqlite_schema").fetchall()
                != [("table", "release", _SCHEMA)]):
        raise ValueError()
    rows = db.execute("SELECT id, phase, body FROM release LIMIT 2").fetchall()
    if (len(rows) != 1 or rows[0][:2] != (1, phase) or type(rows[0][2]) is not bytes
            or not 0 < len(rows[0][2]) <= 32768):
        raise ValueError()
    return rows[0][2]


def _create(path: Path, body: bytes) -> None:
    # The empty name is created exclusively by apply before its inode is bound.
    with closing(_connect(path, readonly=False)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(_SCHEMA)
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO release VALUES (1, 'prepared', ?)", (body,))
        db.commit()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)  # Must succeed BEFORE any complete record is possible.
    finally:
        os.close(directory)


def _complete(path: Path, body: bytes, recheck: Callable[[], None]) -> None:
    # SQLite owns the atomic commit, including rollback-journal directory sync.
    # A lost result at/after commit is uncertain, not a license to replay. Exact
    # read-only confirmation decides whether completion actually survived.
    with closing(_connect(path, readonly=False)) as db:
        db.execute("BEGIN IMMEDIATE")
        if _rows(db, phase="prepared") != body:
            raise ValueError()
        db.execute("UPDATE release SET phase='complete' WHERE id=1")
        recheck()
        db.commit()


def _read(path: Path) -> bytes:
    # Never let a read-only confirmation replay a hot journal or create WAL files.
    if any(_present(Path(str(path) + suffix)) for suffix in _SIDECARS):
        raise ValueError()
    before = _private_read(path.parent, path.name, 65536)
    if before[:16] != b"SQLite format 3\x00" or before[18:20] != b"\x01\x01":
        raise ValueError()
    with closing(_connect(path, readonly=True)) as db:
        db.execute("BEGIN")
        body = _rows(db, phase="complete")
    _matches(path, before)
    if any(_present(Path(str(path) + suffix)) for suffix in _SIDECARS):
        raise ValueError()
    return body


def _confirmed(handoff: BrowserRecoveryHandoff, *, release_id: str,
               browser_running: bool = False) -> BrowserGuardReleaseEvidence:
    path = handoff._session._root / RELEASE_JOURNAL
    raw = _read(path)
    value = json.loads(raw, object_pairs_hook=_object)
    if (type(value) is not dict or set(value) != {
            "version", "operation", "release_id", "reviewed_at", "approved_at", "expires_at",
            "consent_sha256", "journal_binding", "evidence"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["operation"] != "release-paused-browser-guard"
            or not _hex(release_id) or value["release_id"] != release_id
            or not _hex(value["consent_sha256"])
            or value["journal_binding"] != _inode(path)
            or any(type(value[key]) not in (float, int) or not math.isfinite(value[key])
                   or value[key] < 0 for key in ("reviewed_at", "approved_at", "expires_at"))
            or not value["reviewed_at"] <= value["approved_at"] < value["expires_at"]
            or value["expires_at"] != value["reviewed_at"] + _SECONDS
            or value["evidence"] != _snapshot(handoff, browser_running=browser_running)
            or raw != _json(value)):
        raise ValueError()
    if _read(path) != raw or value["journal_binding"] != _inode(path):
        raise ValueError()
    return BrowserGuardReleaseEvidence(str(value["evidence"]["identity"]), handoff._operation,
        release_id, hashlib.sha256(raw).hexdigest(), int(value["evidence"]["revision"]))


class BrowserPausedGuardRelease:
    """Trusted local callback only. Cancellation creates nothing; an attempt is one-use."""

    def __init__(self, handoff: BrowserRecoveryHandoff, *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._handoff, self._clock, self._monotonic = handoff, clock, monotonic
        self._attempted = False

    def apply(self, *, confirmation: Callable[[BrowserGuardReleaseReview], str | None],
              ) -> BrowserGuardReleaseEvidence | None:
        try:
            if self._attempted:
                raise ValueError()
            self._attempted = True
            handoff = self._handoff
            root, profile = handoff._session._root, handoff._session._profile
            with _launch_lock(root, create=False), browser_profile_access(profile, exclusive=False):
                if has_guard_release(root):
                    raise ValueError()
                before = _snapshot(handoff)
                config = load_browser_native_configuration(profile)
                ledger = BrowserDeviceRecovery(profile / "recovery.sqlite", config.identity)
                # Shared directory ownership excludes private-input maintenance;
                # this unchanged BEGIN IMMEDIATE transaction additionally fences
                # native revisions while consent and the release commit run.
                with ledger._connection():
                    if _snapshot(handoff) != before:
                        raise ValueError()
                    now, started = self._clock(), self._monotonic()
                    if any(type(n) not in (float, int) or not math.isfinite(n) or n < 0
                           for n in (now, started)):
                        raise ValueError()
                    release_id = secrets.token_hex(32)
                    phrase = "RELEASE PAUSED " + release_id + ":" + secrets.token_hex(16)
                    revision = before["revision"]
                    assert isinstance(revision, int)
                    review = BrowserGuardReleaseReview(root, profile, handoff._root,
                        config.origin, config.device_id, handoff._operation, release_id, phrase,
                        revision, now + _SECONDS)
                    answer = confirmation(review)
                    if answer is None:
                        return None

                    def recheck() -> None:
                        wall, elapsed = self._clock(), self._monotonic() - started
                        if (type(answer) is not str or answer != phrase
                                or type(wall) not in (float, int) or not math.isfinite(wall)
                                or not now <= wall < review.expires_at
                                or not math.isfinite(elapsed) or not 0 <= elapsed < _SECONDS
                                or _snapshot(handoff) != before):
                            raise ValueError()

                    recheck()
                    approved = self._clock()
                    path = root / RELEASE_JOURNAL
                    if has_guard_release(root):
                        raise ValueError()
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    os.close(fd)
                    binding = _inode(path)
                    body = _json({"version": 1, "operation": "release-paused-browser-guard",
                        "release_id": release_id, "reviewed_at": now, "approved_at": approved,
                        "expires_at": review.expires_at,
                        "consent_sha256": hashlib.sha256(phrase.encode()).hexdigest(),
                        "journal_binding": binding, "evidence": before})
                    _create(path, body)

                    def commit_check() -> None:
                        recheck()
                        if _inode(path) != binding:
                            raise ValueError()

                    _complete(path, body, commit_check)
                    return _confirmed(handoff, release_id=release_id)
        except Exception:
            raise BrowserGuardReleaseError() from None

    def confirm(self, *, release_id: str) -> BrowserGuardReleaseEvidence:
        """Exact read-only confirmation, including after a successful commit's lost reply."""
        try:
            with _launch_lock(self._handoff._session._root, create=False), browser_profile_access(
                    self._handoff._session._profile, exclusive=False):
                return _confirmed(self._handoff, release_id=release_id)
        except Exception:
            raise BrowserGuardReleaseError() from None


def check_paused_guard_release(
    root: Path, *, bundle: Path, profile: Path, public_key: Path,
) -> None:
    """Normal startup's fixed journal selection, never a caller-supplied bypass.

    The launcher rechecks this under its launch lock. Offline inspection is also
    read-only and conservatively requires the released browser to be stopped.
    """
    _check_release(root, bundle=bundle, profile=profile, public_key=public_key,
                   browser_running=False)


def _check_worker_guard_release(
    root: Path, *, bundle: Path, profile: Path, public_key: Path,
) -> None:
    """Internal worker check, after verifying its live Chromium ancestor/launch owner.

    Only current Singleton presence differs from the offline check. Completed
    release, exact paused revision, restored host and old supervisor exit remain
    mandatory. There is no message/CLI option that enables this path.
    """
    _check_release(root, bundle=bundle, profile=profile, public_key=public_key,
                   browser_running=True)


def _check_release(root: Path, *, bundle: Path, profile: Path, public_key: Path,
                   browser_running: bool) -> None:
    path = root / RELEASE_JOURNAL
    raw = _read(path)
    value = json.loads(raw, object_pairs_hook=_object)
    evidence = value["evidence"]
    targets = evidence["targets"]
    if (type(targets) is not dict or set(targets) != {
            "directory", "bundle", "profile", "public_key", "archives", "handoff",
            "recovery_bundle"} or any(type(v) is not str for v in targets.values())
            or any(targets[key] != str(selected) for key, selected in {
                "directory": root, "bundle": bundle, "profile": profile,
                "public_key": public_key}.items())):
        raise ValueError()
    handoff = BrowserRecoveryHandoff(Path(targets["handoff"]),
        **{k: Path(v) for k, v in targets.items() if k != "handoff"},
        operation_id=evidence["operation_id"], browser_intent=evidence["browser_intent"],
        supervised=True)
    with browser_profile_access(profile, exclusive=False):
        result = _confirmed(handoff, release_id=value["release_id"],
                            browser_running=browser_running)
    if result.receipt_sha256 != hashlib.sha256(raw).hexdigest():
        raise ValueError()
