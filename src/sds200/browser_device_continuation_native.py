"""Internal paused-activation transaction core, NOT current runtime permission.

No file opener, consent adapter, migration command or worker dispatch calls this
module. A future trusted stopped-owner adapter must reconstruct current history,
exclusively create and sync the selected manifest, pin/recheck its bytes and inode,
then use one checked native transaction. These functions cannot establish those
filesystem/ownership facts from a supplied dataclass or byte string.

Schema 3 is deliberately rejected by existing recovery/resume helpers. Accepting
it there requires the complete current-epoch selector and epoch-bound approvals,
including pause/clock/sign-out cancellation. Never use this core on real profiles
before that selected path is implemented and qualified.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypeGuard

from .browser_device_continuation_history import BrowserContinuationHistory
from .browser_device_recovery import RecoveryMode, _object
from .browser_device_resume import _COLUMNS, _hex, _integer, _timestamp
from .browser_device_resume_archive import _approvals, _encoded, _state

_RECOVERY_SCHEMA = (
    "CREATE TABLE recovery (id INTEGER PRIMARY KEY CHECK(id=1), "
    "identity TEXT NOT NULL, revision INTEGER NOT NULL, mode TEXT NOT NULL, "
    "failures INTEGER NOT NULL, next_at REAL NOT NULL, observed_at REAL NOT NULL)"
)
_RESUME_SCHEMA = (
    "CREATE TABLE browser_resume (digest TEXT PRIMARY KEY, identity TEXT, phase TEXT, "
    "revision INTEGER, mode TEXT, intent TEXT, device TEXT, generation INTEGER, "
    "credential_hash TEXT, trust_hash TEXT, created REAL, expires REAL)"
)
_ANCHOR_SCHEMA = (
    "CREATE TABLE browser_continuation (id INTEGER PRIMARY KEY CHECK(id=1), "
    "epoch TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, "
    "manifest_device INTEGER NOT NULL, manifest_inode INTEGER NOT NULL)"
)
_OPERATION = "activate-paused-browser-continuation"
_LIMIT = 256 * 1024


class BrowserContinuationNativeError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Paused continuation activation is unavailable or cannot be confirmed. "
                         "Retain all evidence; do not replay or repair the operation. "
                         "No runtime permission or sign-in was established.")


@dataclass(frozen=True, slots=True)
class _PausedActivationSnapshot:
    """Exact transaction view only, never a role, browser consent or server proof."""

    epoch: str = field(repr=False)
    manifest_sha256: str = field(repr=False)
    revision: int
    mode: RecoveryMode = RecoveryMode.PAUSED


def _inode(value: object) -> TypeGuard[tuple[int, int] | list[int]]:
    return ((type(value) is tuple or type(value) is list) and len(value) == 2
            and all(type(n) is int and 0 < n < 2**63 for n in value))


def _baseline(history: BrowserContinuationHistory, profile: Path) -> dict[str, Any]:
    if (type(history) is not BrowserContinuationHistory or not isinstance(profile, Path)
            or not profile.is_absolute() or ".." in profile.parts
            or history.mode is not RecoveryMode.PAUSED
            or not _integer(history.native_revision) or not _integer(history.native_revision + 1)
            or not all(_hex(v) for v in (history.identity, history.release_id,
                                        history.intent_id, history.fingerprint))
            or type(history.native_after) is not bytes
            or not 0 < len(history.native_after) <= _LIMIT):
        raise ValueError()
    value = json.loads(history.native_after, object_pairs_hook=_object)
    fields = {"identity", "profile", "state", "approvals"}
    if (type(value) is not dict or set(value) not in (fields, fields | {"schema", "intent"})
            or _encoded(value) != history.native_after or value["identity"] != history.identity
            or value["profile"] != str(profile)):
        raise ValueError()
    schema = value.get("schema", 2)
    if (type(schema) is not int or schema not in {1, 2}
            or ("intent" in value and not _hex(value["intent"]))):
        raise ValueError()
    state = _state(value["state"])
    approvals = _approvals(value["approvals"], identity=history.identity,
                           schema=schema, state=state)
    if (state.mode is not RecoveryMode.PAUSED or state.revision != history.native_revision
            or any(row["phase"] in {"prepared", "claimed"} for row in approvals)):
        raise ValueError()
    return {"identity": history.identity, "profile": str(profile), "schema": schema,
            "state": asdict(state), "approvals": approvals}


def _manifest_document(history: BrowserContinuationHistory, *, profile: Path,
                       ledger_binding: object, epoch: str, consent_sha256: str,
                       reviewed_at: float, approved_at: float) -> dict[str, Any]:
    before = _baseline(history, profile)
    if (not _inode(ledger_binding) or not _hex(epoch) or not _hex(consent_sha256)
            or not _timestamp(reviewed_at) or not _timestamp(approved_at)
            or not _timestamp(reviewed_at + 120)
            or not before["state"]["observed_at"] <= reviewed_at <= approved_at
            or not approved_at < reviewed_at + 120):
        raise ValueError()
    return {"version": 1, "operation": _OPERATION, "epoch": epoch,
            "release_id": history.release_id, "intent_id": history.intent_id,
            "history_fingerprint": history.fingerprint, "ledger_binding": list(ledger_binding),
            "consent_sha256": consent_sha256, "reviewed_at": reviewed_at,
            "approved_at": approved_at, "expires_at": reviewed_at + 120, "before": before,
            "after": {**before, "schema": 3, "state": {
                **before["state"], "revision": history.native_revision + 1,
                "observed_at": float(approved_at)}}}


def _prepare_activation_manifest(history: BrowserContinuationHistory, *, profile: Path,
                                 ledger_binding: tuple[int, int], epoch: str,
                                 consent_sha256: str, reviewed_at: float,
                                 approved_at: float) -> bytes:
    """Pure candidate bytes, not consent, a synced file, a commit or permission."""
    try:
        raw = _encoded(_manifest_document(history, profile=profile, ledger_binding=ledger_binding,
            epoch=epoch, consent_sha256=consent_sha256, reviewed_at=reviewed_at,
            approved_at=approved_at))
        if len(raw) > _LIMIT:
            raise ValueError()
        return raw
    except Exception:
        raise BrowserContinuationNativeError() from None


def _plan(raw: bytes, history: BrowserContinuationHistory, profile: Path,
          ledger_binding: tuple[int, int]) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= _LIMIT:
        raise ValueError()
    value = json.loads(raw, object_pairs_hook=_object)
    expected = _manifest_document(history, profile=profile, ledger_binding=ledger_binding,
        epoch=value["epoch"], consent_sha256=value["consent_sha256"],
        reviewed_at=value["reviewed_at"], approved_at=value["approved_at"])
    if _encoded(expected) != raw:
        raise ValueError()
    return expected


def _transaction(db: sqlite3.Connection, profile: Path, *, readonly: bool) -> None:
    # Never set a pragma, open another database or silently start a transaction.
    if (not db.in_transaction
            or db.execute("PRAGMA database_list").fetchall()
                != [(0, "main", str(profile / "recovery.sqlite"))]
            or db.execute("PRAGMA journal_mode").fetchone() != ("delete",)
            or db.execute("PRAGMA trusted_schema").fetchone() != (0,)
            or db.execute("PRAGMA query_only").fetchone() != (int(readonly),)
            or (not readonly and db.execute("PRAGMA synchronous").fetchone() != (3,))):
        raise ValueError()


def _snapshot(db: sqlite3.Connection, before: dict[str, Any], *, activated: bool
              ) -> dict[str, Any]:
    schema = 3 if activated else before["schema"]
    expected: list[tuple[str, str, str, str | None]] = [
        ("table", "recovery", "recovery", _RECOVERY_SCHEMA)]
    if before["schema"] == 2:
        expected.extend([("table", "browser_resume", "browser_resume", _RESUME_SCHEMA),
                         ("index", "sqlite_autoindex_browser_resume_1", "browser_resume", None)])
    if activated:
        expected.append(("table", "browser_continuation", "browser_continuation", _ANCHOR_SCHEMA))
    if (db.execute("PRAGMA user_version").fetchone() != (schema,)
            or db.execute("PRAGMA application_id").fetchone() != (0,)
            or db.execute("PRAGMA quick_check").fetchall() != [("ok",)]
            or db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name")
                .fetchall()
                != sorted(expected, key=lambda row: row[1])):
        raise ValueError()
    rows = db.execute("SELECT id,identity,revision,mode,failures,next_at,observed_at "
                      "FROM recovery LIMIT 2").fetchall()
    if len(rows) != 1 or rows[0][:2] != (1, before["identity"]):
        raise ValueError()
    _, _, revision, mode, failures, next_at, observed_at = rows[0]
    state = _state(dict(revision=revision, mode=mode, failures=failures,
                        next_at=next_at, observed_at=observed_at))
    approvals = ([] if before["schema"] == 1 else [dict(zip(_COLUMNS, row, strict=True)) for row in
        db.execute("SELECT * FROM browser_resume ORDER BY revision,digest LIMIT 129").fetchall()])
    _approvals(approvals, identity=before["identity"], schema=before["schema"], state=state)
    return {"identity": before["identity"], "profile": before["profile"], "schema": schema,
            "state": asdict(state), "approvals": approvals}


def _anchor(raw: bytes, plan: dict[str, Any], binding: tuple[int, int]) -> tuple[object, ...]:
    if not _inode(binding):
        raise ValueError()
    return (1, plan["epoch"], hashlib.sha256(raw).hexdigest(), *binding)


def _exact_after(db: sqlite3.Connection, raw: bytes, plan: dict[str, Any],
                 binding: tuple[int, int]) -> _PausedActivationSnapshot:
    if (_encoded(_snapshot(db, plan["before"], activated=True)) != _encoded(plan["after"])
            or db.execute("SELECT * FROM browser_continuation LIMIT 2").fetchall()
                != [_anchor(raw, plan, binding)]):
        raise ValueError()
    return _PausedActivationSnapshot(plan["epoch"], hashlib.sha256(raw).hexdigest(),
                                     plan["after"]["state"]["revision"])


def _stage_paused_activation(db: sqlite3.Connection, *, manifest: bytes,
                             manifest_binding: tuple[int, int],
                             history: BrowserContinuationHistory, profile: Path,
                             ledger_binding: tuple[int, int], now: float) -> None:
    """Stage one native change; caller owns BEGIN IMMEDIATE and the final commit.

    Any write/readback failure aborts the WHOLE transaction, so a caller catching
    the error cannot commit partial activation or savepoint rollback bookkeeping.
    The caller must dedicate this transaction to activation. Success is UNCOMMITTED.
    History/file checks belong before this DML; validate the resulting native view
    on this same connection, never by ignoring a rollback sidecar in another reader.
    """
    try:
        _transaction(db, profile, readonly=False)
        plan = _plan(manifest, history, profile, ledger_binding)
        anchor = _anchor(manifest, plan, manifest_binding)
        if (not _timestamp(now) or not plan["approved_at"] <= now < plan["expires_at"]
                or _encoded(_snapshot(db, plan["before"], activated=False))
                    != _encoded(plan["before"])):
            raise ValueError()
        try:
            db.execute(_ANCHOR_SCHEMA)
            db.execute("INSERT INTO browser_continuation VALUES (?,?,?,?,?)", anchor)
            db.execute("UPDATE recovery SET revision=?,observed_at=? WHERE id=1",
                       (plan["after"]["state"]["revision"], plan["approved_at"]))
            db.execute("PRAGMA user_version=3")
            _exact_after(db, manifest, plan, manifest_binding)
        except BaseException:
            try:
                db.rollback()
            except BaseException:
                # An uncertain rollback must not leave a reusable writer in the
                # caller's hands. Close performs final cleanup; no retry is safe.
                db.close()
                raise
            raise
    except Exception:
        raise BrowserContinuationNativeError() from None


def _inspect_paused_activation(db: sqlite3.Connection, *, manifest: bytes,
                               manifest_binding: tuple[int, int],
                               history: BrowserContinuationHistory, profile: Path,
                               ledger_binding: tuple[int, int]) -> _PausedActivationSnapshot:
    """Exact read-only activation view, including after a lost commit reply.

    The caller must independently recheck retained history and the selected private
    manifest/ledger bytes and inodes under ownership. This view is not ongoing
    runtime permission: ANY later native state change invalidates this exact check.
    Old consent expiry is checked structurally, not renewed for confirmation.
    """
    try:
        _transaction(db, profile, readonly=True)
        plan = _plan(manifest, history, profile, ledger_binding)
        return _exact_after(db, manifest, plan, manifest_binding)
    except Exception:
        raise BrowserContinuationNativeError() from None
