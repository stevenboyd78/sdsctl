"""Fixture-only current-epoch native transactions, NOT current runtime permission.

No filesystem opener, normal schema acceptance, worker role, network exchange or
browser action is exposed here. A future owned adapter must reconstruct the full
selected history, private inputs and actual immutable manifest around EVERY
transaction, and independently obtain fresh browser consent and server proof.
Caller-supplied selections/snapshots cannot establish those facts.

Each mutation requires a dedicated existing BEGIN IMMEDIATE transaction. Success
is UNCOMMITTED. Any failure aborts the entire transaction. Legacy approvals remain
byte-for-byte terminal; only separate epoch-bound rows participate in this core.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import browser_device_continuation_native as native
from .browser_device_continuation_history import BrowserContinuationHistory
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _COLUMNS, _hex, _integer, _timestamp
from .browser_device_resume_archive import _encoded


class BrowserContinuationEpochError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Current continuation transaction is unavailable. Retain the profile "
                         "for review; do not replay or repair it. No sign-in was established.")


@dataclass(frozen=True, slots=True, repr=False)
class _EpochSelection:
    """Selected facts only; not evidence of filesystem ownership or permission."""

    manifest: bytes
    manifest_binding: tuple[int, int]
    history: BrowserContinuationHistory
    profile: Path
    ledger_binding: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _EpochSnapshot:
    """Exact SQL view/CAS value only. Never a cached runtime permission lease."""

    epoch: str = field(repr=False)
    manifest_sha256: str = field(repr=False)
    fingerprint: str = field(repr=False)
    revision: int
    mode: RecoveryMode


@dataclass(frozen=True, slots=True, repr=False)
class _ApprovalInputs:
    """Digests and reviewed server generation, not raw credentials or a proof."""

    digest: str
    intent: str
    device: str
    generation: int
    credential_hash: str
    trust_hash: str


@dataclass(slots=True, repr=False)
class _View:
    plan: dict[str, Any]
    state: dict[str, Any]
    approvals: list[dict[str, Any]]
    grant: str | None
    snapshot: _EpochSnapshot


def _current_state(row: tuple[Any, ...], identity: str) -> dict[str, Any]:
    if len(row) != 7 or row[:2] != (1, identity):
        raise ValueError()
    _, _, revision, mode, failures, next_at, observed_at = row
    mode = RecoveryMode(mode)
    if (not _integer(revision) or type(failures) is not int or not 0 <= failures <= 32
            or not _timestamp(next_at) or not _timestamp(observed_at)
            or next_at > observed_at + 300):
        raise ValueError()
    return dict(revision=revision, mode=mode, failures=failures,
                next_at=next_at, observed_at=observed_at)


def _inputs(inputs: _ApprovalInputs, selection: _EpochSelection) -> None:
    if (type(inputs) is not _ApprovalInputs
            or not all(_hex(v) for v in (inputs.digest, inputs.intent,
                                        inputs.credential_hash, inputs.trust_hash))
            or inputs.device != selection.history.device_id or not _integer(inputs.generation)):
        raise ValueError()


def _read(db: sqlite3.Connection, selection: _EpochSelection, *, readonly: bool) -> _View:
    if type(selection) is not _EpochSelection:
        raise ValueError()
    native._transaction(db, selection.profile, readonly=readonly)
    plan = native._plan(selection.manifest, selection.history, selection.profile,
                        selection.ledger_binding)
    native._layout(db, plan["before"], activated=True)
    if db.execute("SELECT * FROM browser_continuation LIMIT 2").fetchall() != [
            native._anchor(selection.manifest, plan, selection.manifest_binding)]:
        raise ValueError()
    rows = db.execute("SELECT id,identity,revision,mode,failures,next_at,observed_at "
                      "FROM recovery LIMIT 2").fetchall()
    if len(rows) != 1:
        raise ValueError()
    state = _current_state(rows[0], plan["before"]["identity"])
    baseline = plan["after"]["state"]["revision"]
    if state["revision"] < baseline:
        raise ValueError()
    legacy = ([] if plan["before"]["schema"] == 1 else [dict(zip(_COLUMNS, r, strict=True)) for r in
        db.execute("SELECT * FROM browser_resume ORDER BY revision,digest LIMIT 129").fetchall()])
    if _encoded(legacy) != _encoded(plan["before"]["approvals"]):
        raise ValueError()
    approvals = [dict(zip(native._EPOCH_COLUMNS, r, strict=True)) for r in db.execute(
        "SELECT * FROM browser_epoch_approval ORDER BY revision,digest LIMIT 129").fetchall()]
    if len(approvals) > 128:
        raise ValueError()
    revisions: set[int] = set()
    legacy_digests = {row["digest"] for row in legacy}
    pending = 0
    for approval in approvals:
        _inputs(_ApprovalInputs(**{k: approval[k] for k in _ApprovalInputs.__dataclass_fields__}),
                selection)
        if (approval["identity"] != plan["before"]["identity"] or approval["epoch"] != plan["epoch"]
                or approval["phase"] not in {
                    "prepared", "claimed", "complete", "cancelled", "failed"}
                or approval["digest"] in legacy_digests
                or not _integer(approval["revision"])
                or not baseline < approval["revision"] <= state["revision"]
                or approval["revision"] in revisions
                or approval["mode"] not in {
                    m.value for m in RecoveryMode if m != RecoveryMode.ACTIVE}
                or not _timestamp(approval["created"]) or not _timestamp(approval["expires"])
                or approval["expires"] != approval["created"] + 120):
            raise ValueError()
        revisions.add(approval["revision"])
        if approval["phase"] in {"prepared", "claimed"}:
            pending += 1
            if (approval["revision"] != state["revision"] or approval["mode"] != state["mode"]
                    or not approval["created"] <= state["observed_at"] < approval["expires"]
                    or (approval["phase"] == "prepared"
                        and approval["created"] != state["observed_at"])):
                raise ValueError()
        if approval["phase"] == "complete" and approval["revision"] >= state["revision"]:
            raise ValueError()
    fences = db.execute("SELECT * FROM browser_epoch_state LIMIT 2").fetchall()
    if len(fences) != 1 or pending > 1:
        raise ValueError()
    grant = fences[0][3]
    if state["mode"] is RecoveryMode.ACTIVE:
        if (not _hex(grant) or pending or not approvals
                or approvals[-1]["digest"] != grant or approvals[-1]["phase"] != "complete"):
            raise ValueError()
    elif grant is not None:
        raise ValueError()
    fence = native._epoch_fence(plan["epoch"], state, approvals, grant)
    if fences != [fence]:
        raise ValueError()
    # The initial paused checkpoint remains exact; a fabricated zero-step state
    # or approval cannot pass merely because its revision is not lower.
    if state["revision"] == baseline:
        native._exact_after(db, selection.manifest, plan, selection.manifest_binding)
    view = dict(state=state, approvals=approvals, active_grant=grant, epoch=plan["epoch"],
                manifest_sha256=hashlib.sha256(selection.manifest).hexdigest())
    return _View(plan, state, approvals, grant, _EpochSnapshot(plan["epoch"],
        view["manifest_sha256"], hashlib.sha256(_encoded(view)).hexdigest(),
        state["revision"], state["mode"]))


def _inspect_epoch(db: sqlite3.Connection, selection: _EpochSelection) -> _EpochSnapshot:
    try:
        return _read(db, selection, readonly=True).snapshot
    except Exception:
        raise BrowserContinuationEpochError() from None


@contextmanager
def _mutation(db: sqlite3.Connection, selection: _EpochSelection, expected: _EpochSnapshot,
              now: float, *, clock_correction: bool = False) -> Iterator[_View]:
    try:
        try:
            view = _read(db, selection, readonly=False)
            if (type(expected) is not _EpochSnapshot or expected != view.snapshot
                    or not _timestamp(now) or not _integer(view.state["revision"] + 1)
                    or (not clock_correction and now < view.state["observed_at"])):
                raise ValueError()
            yield view
        except BaseException:
            try:
                db.rollback()
            except BaseException:
                db.close()
                raise
            raise
    except Exception:
        raise BrowserContinuationEpochError() from None


def _write(db: sqlite3.Connection, selection: _EpochSelection, view: _View,
           approvals: list[dict[str, Any]], state: dict[str, Any], grant: str | None
           ) -> _EpochSnapshot:
    for old, new in zip(view.approvals, approvals, strict=False):
        if {**old, "phase": new["phase"]} != new:
            raise ValueError()
        if (old["phase"] != new["phase"] and db.execute(
                "UPDATE browser_epoch_approval SET phase=? WHERE digest=? AND phase=?",
                (new["phase"], old["digest"], old["phase"])).rowcount != 1):
            raise ValueError()
    if len(approvals) not in {len(view.approvals), len(view.approvals) + 1}:
        raise ValueError()
    if len(approvals) > len(view.approvals):
        db.execute("INSERT INTO browser_epoch_approval VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   tuple(approvals[-1][c] for c in native._EPOCH_COLUMNS))
    db.execute("UPDATE recovery SET revision=?,mode=?,failures=?,next_at=?,observed_at=? "
               "WHERE id=1",
               tuple(state[k] for k in ("revision", "mode", "failures", "next_at", "observed_at")))
    fence = native._epoch_fence(view.plan["epoch"], state, approvals, grant)
    db.execute("UPDATE browser_epoch_state SET epoch=?,snapshot_sha256=?,active_grant=? WHERE id=1",
               fence[1:])
    after = _read(db, selection, readonly=False)
    if (after.state != state or after.approvals != approvals or after.grant != grant):
        raise ValueError()
    return after.snapshot


def _stage_prepare(db: sqlite3.Connection, selection: _EpochSelection, expected: _EpochSnapshot,
                   inputs: _ApprovalInputs, *, now: float) -> _EpochSnapshot:
    """Stage a fresh consent/proof-input binding; does not obtain that consent/proof."""
    with _mutation(db, selection, expected, now) as view:
        _inputs(inputs, selection)
        if (view.state["mode"] is RecoveryMode.ACTIVE or len(view.approvals) >= 128
                or any(r["phase"] in {"prepared", "claimed"} or r["digest"] == inputs.digest
                       for r in view.approvals)
                or inputs.digest in {r["digest"] for r in view.plan["before"]["approvals"]}
                or not _timestamp(now + 120)):
            raise ValueError()
        state = {**view.state, "revision": view.state["revision"] + 1, "observed_at": float(now)}
        row = dict(digest=inputs.digest, identity=selection.history.identity,
            epoch=view.plan["epoch"],
            phase="prepared", revision=state["revision"], mode=state["mode"], intent=inputs.intent,
            device=inputs.device, generation=inputs.generation,
            credential_hash=inputs.credential_hash,
            trust_hash=inputs.trust_hash, created=float(now), expires=float(now + 120))
        return _write(db, selection, view, [*view.approvals, row], state, None)


def _pending(view: _View, selection: _EpochSelection, inputs: _ApprovalInputs, phase: str,
             now: float) -> dict[str, Any]:
    _inputs(inputs, selection)
    if not view.approvals:
        raise ValueError()
    row = view.approvals[-1]
    if (row["phase"] != phase or row["revision"] != view.state["revision"]
            or row["mode"] != view.state["mode"]
            or any(row[k] != getattr(inputs, k) for k in _ApprovalInputs.__dataclass_fields__)
            or not row["created"] <= now < row["expires"]):
        raise ValueError()
    return row


def _stage_claim(db: sqlite3.Connection, selection: _EpochSelection, expected: _EpochSnapshot,
                 inputs: _ApprovalInputs, *, now: float) -> _EpochSnapshot:
    """Consume before external proof. The caller MUST commit before any network work."""
    with _mutation(db, selection, expected, now) as view:
        row = _pending(view, selection, inputs, "prepared", now)
        state = {**view.state, "observed_at": float(now)}
        return _write(db, selection, view, [*view.approvals[:-1], {**row, "phase": "claimed"}],
                      state, None)


def _stage_complete(db: sqlite3.Connection, selection: _EpochSelection, expected: _EpochSnapshot,
                    inputs: _ApprovalInputs, *, now: float) -> _EpochSnapshot:
    """Stage post-proof completion only; does NOT prove server authority or sign in.

    The future owned adapter must reacquire/recheck current inputs, epoch and owner
    after bounded external proof. There is intentionally no public caller yet.
    """
    with _mutation(db, selection, expected, now) as view:
        row = _pending(view, selection, inputs, "claimed", now)
        if now > view.state["observed_at"] + 10:
            raise ValueError()
        state = dict(revision=view.state["revision"] + 1, mode=RecoveryMode.ACTIVE,
                     failures=0, next_at=0.0, observed_at=float(now))
        return _write(db, selection, view, [*view.approvals[:-1], {**row, "phase": "complete"}],
                      state, inputs.digest)


def _stage_fail(db: sqlite3.Connection, selection: _EpochSelection, expected: _EpochSnapshot,
                inputs: _ApprovalInputs, *, now: float) -> _EpochSnapshot:
    """Terminalize a pending/uncertain approval, even expired; never overwrite pause."""
    with _mutation(db, selection, expected, now) as view:
        _inputs(inputs, selection)
        if not view.approvals:
            raise ValueError()
        row = view.approvals[-1]
        if (row["phase"] not in {"prepared", "claimed"}
                or any(row[k] != getattr(inputs, k) for k in _ApprovalInputs.__dataclass_fields__)):
            raise ValueError()
        state = {**view.state, "revision": view.state["revision"] + 1, "observed_at": float(now)}
        return _write(db, selection, view, [*view.approvals[:-1], {**row, "phase": "failed"}],
                      state, None)


def _cancelled(view: _View) -> list[dict[str, Any]]:
    return [{**r, "phase": "cancelled"} if r["phase"] in {"prepared", "claimed"} else r
            for r in view.approvals]


def _stage_pause(db: sqlite3.Connection, selection: _EpochSelection, expected: _EpochSnapshot,
                 *, now: float) -> _EpochSnapshot:
    """Shared native cancellation for future pause/sign-out; not browser session deletion."""
    with _mutation(db, selection, expected, now) as view:
        state = dict(revision=view.state["revision"] + 1, mode=RecoveryMode.PAUSED,
                     failures=0, next_at=0.0, observed_at=float(now))
        return _write(db, selection, view, _cancelled(view), state, None)


def _stage_clock_correction(db: sqlite3.Connection, selection: _EpochSelection,
                            expected: _EpochSnapshot, *, now: float) -> _EpochSnapshot:
    """Backstep cancels pending consent and invalidates stale calls; mode is preserved."""
    with _mutation(db, selection, expected, now, clock_correction=True) as view:
        if now >= view.state["observed_at"] or not _timestamp(now + 10):
            raise ValueError()
        state = {**view.state, "revision": view.state["revision"] + 1,
                 "next_at": float(now + 10), "observed_at": float(now)}
        return _write(db, selection, view, _cancelled(view), state, view.grant)
