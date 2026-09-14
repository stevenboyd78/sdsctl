"""Portable current SQL tests; selections are fictional, not owned runtime permission."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_native as native
from sds200.browser_device_recovery import BrowserRecoveryError, RecoveryMode
from sds200.browser_device_resume_archive import _encoded
from tests.test_browser_device_continuation_native import activate, inspect, transaction
from tests.test_browser_device_continuation_native import candidate as candidate
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import lab as lab
from tests.test_browser_device_resume import sql
from tests.test_browser_device_resume_maintenance import snapshot

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux current-epoch transaction fixtures")
ERROR = epoch.BrowserContinuationEpochError


@pytest.fixture
def current(lab, candidate):
    activate(lab, candidate)
    selection = epoch._EpochSelection(**candidate.args)
    inputs = epoch._ApprovalInputs("a" * 64, "b" * 64, lab.configuration.device_id,
                                   1, "c" * 64, "d" * 64)
    return SimpleNamespace(selection=selection, inputs=inputs, now=candidate.now + 1)


def read(lab, current):
    with transaction(lab.ledger.path, readonly=True) as db:
        return epoch._inspect_epoch(db, current.selection)


def step(lab, current, operation, *, expected=None, inputs=None, now=None, commit=True):
    expected = read(lab, current) if expected is None else expected
    args = [] if operation in {"pause", "clock_correction"} else [inputs or current.inputs]
    with transaction(lab.ledger.path) as db:
        result = getattr(epoch, "_stage_" + operation)(db, current.selection, expected, *args,
            now=current.now if now is None else now)
        if commit:
            db.commit()
        return result


def phases(lab):
    with transaction(lab.ledger.path, readonly=True) as db:
        return db.execute("SELECT phase FROM browser_epoch_approval ORDER BY revision").fetchall()


def progress(lab, current, phase):
    for operation in ("prepare", "claim", "complete"):
        step(lab, current, operation)
        if operation == phase:
            break


def test_separate_epoch_approval_lifecycle_and_exact_paused_confirmation(lab, candidate, current):
    initial = read(lab, current)
    assert initial.mode is RecoveryMode.PAUSED
    assert initial.revision == inspect(lab, candidate).revision
    assert initial.epoch == candidate.preparation["epoch"]
    with pytest.raises(FrozenInstanceError):
        initial.revision += 1
    for private in (initial.epoch, initial.fingerprint, initial.manifest_sha256,
                    lab.configuration.identity, str(lab.root)):
        assert private not in repr(initial) + repr(current.selection) + repr(current.inputs)
    assert not hasattr(initial, "permission") and not hasattr(initial, "session")
    prepared = step(lab, current, "prepare")
    assert prepared.revision == initial.revision + 1 and prepared.mode is RecoveryMode.PAUSED
    claimed = step(lab, current, "claim")
    assert claimed.revision == prepared.revision and claimed.fingerprint != prepared.fingerprint
    # A claim changes the exact CAS view even though the native revision is the same.
    with pytest.raises(ERROR):
        step(lab, current, "complete", expected=prepared)
    completed = step(lab, current, "complete", expected=claimed)
    assert completed.revision == prepared.revision + 1 and completed.mode is RecoveryMode.ACTIVE
    assert phases(lab) == [("complete",)]
    with pytest.raises(native.BrowserContinuationNativeError):
        inspect(lab, candidate)  # Do not relax the exact initial paused view.
    paused = step(lab, current, "pause")
    assert paused.mode is RecoveryMode.PAUSED and paused.revision == completed.revision + 1
    assert phases(lab) == [("complete",)]  # Terminal evidence survives sign-out.
    with transaction(lab.ledger.path, readonly=True) as db:
        plan = json.loads(current.selection.manifest)
        legacy = ([] if plan["before"]["schema"] == 1 else db.execute(
            "SELECT * FROM browser_resume ORDER BY revision,digest").fetchall())
        assert legacy == [tuple(row[c] for c in epoch._COLUMNS)
                          for row in plan["before"]["approvals"]]
        assert db.execute("SELECT active_grant FROM browser_epoch_state").fetchone() == (None,)
    for call in (lab.ledger.inspect, lab.ledger.status, lab.ledger.suspend,
                 lambda: lab.ledger.resume(paused.revision),
                 lambda: lab.ledger.authenticate(lambda: pytest.fail("Network I/O"))):
        with pytest.raises(BrowserRecoveryError):
            call()


@pytest.mark.parametrize("phase", ["prepare", "claim", "complete"])
def test_pause_wins_against_stale_or_reconstructed_completion(lab, current, phase):
    progress(lab, current, phase)
    stale = read(lab, current)
    paused = step(lab, current, "pause")
    assert paused.revision > stale.revision and paused.mode is RecoveryMode.PAUSED
    assert phases(lab) == [("complete" if phase == "complete" else "cancelled",)]
    before = snapshot(lab)
    for expected in (stale, paused):
        for operation in ("claim", "complete", "fail"):
            with pytest.raises(ERROR):
                step(lab, current, operation, expected=expected)
    assert snapshot(lab) == before
    replacement = replace(current.inputs, digest="e" * 64, intent="f" * 64)
    assert step(lab, current, "prepare", inputs=replacement).revision == paused.revision + 1


@pytest.mark.parametrize("phase", ["prepare", "claim", "complete"])
def test_clock_backstep_cancels_pending_and_invalidates_every_stale_call(lab, current, phase):
    progress(lab, current, phase)
    stale = read(lab, current)
    corrected = step(lab, current, "clock_correction", now=current.now - 100)
    assert corrected.revision == stale.revision + 1 and corrected.mode is stale.mode
    assert phases(lab) == [("complete" if phase == "complete" else "cancelled",)]
    with transaction(lab.ledger.path, readonly=True) as db:
        assert db.execute("SELECT observed_at,next_at FROM recovery").fetchone() == (
            current.now - 100, current.now - 90)
    with pytest.raises(ERROR):
        step(lab, current, "complete", expected=stale)
    # Clock correction can precede the original activation date without reviving a grant.
    if phase != "complete":
        fresh = replace(current.inputs, digest="e" * 64)
        assert step(lab, current, "prepare", inputs=fresh,
                    now=current.now - 99).mode is RecoveryMode.PAUSED


@pytest.mark.parametrize("operation", ["claim", "complete"])
@pytest.mark.parametrize("delta", [120, 121, 1000])
def test_expiry_never_completes_and_can_be_terminalized_without_replay(
        lab, current, operation, delta):
    progress(lab, current, "prepare" if operation == "claim" else "claim")
    before = snapshot(lab)
    with pytest.raises(ERROR):
        step(lab, current, operation, now=current.now + delta)
    assert snapshot(lab) == before
    failed = step(lab, current, "fail", now=current.now + delta)
    assert failed.mode is RecoveryMode.PAUSED and phases(lab) == [("failed",)]
    for op in ("prepare", "claim", "complete", "fail"):
        with pytest.raises(ERROR):
            step(lab, current, op, now=current.now + delta)


@pytest.mark.parametrize("field,value", [
    ("digest", "e" * 64), ("intent", "e" * 64), ("device", "other"),
    ("generation", 2), ("credential_hash", "e" * 64), ("trust_hash", "e" * 64)])
@pytest.mark.parametrize("operation", ["claim", "complete", "fail"])
def test_changed_inputs_or_server_generation_refuse(lab, current, field, value, operation):
    progress(lab, current, "prepare" if operation == "claim" else "claim")
    before = snapshot(lab)
    with pytest.raises(ERROR):
        step(lab, current, operation, inputs=replace(current.inputs, **{field: value}))
    assert snapshot(lab) == before


@pytest.mark.parametrize("field,value", [
    ("digest", "raw secret"), ("intent", True), ("generation", True), ("generation", 0),
    ("generation", 2**53), ("credential_hash", ""), ("trust_hash", "Z" * 64),
    ("device", "other")])
def test_malformed_preparation_is_sanitized_and_atomic(lab, current, field, value):
    before = snapshot(lab)
    with pytest.raises(ERROR) as error:
        step(lab, current, "prepare", inputs=replace(current.inputs, **{field: value}))
    assert str(lab.root) not in str(error.value) and "raw secret" not in str(error.value)
    assert snapshot(lab) == before


@pytest.mark.parametrize("now", [True, -1, float("nan"), float("inf"), 2**53, 1])
@pytest.mark.parametrize("operation", ["prepare", "pause", "clock_correction"])
def test_invalid_clock_has_no_write(lab, current, now, operation):
    # A finite positive backstep is only permitted by explicit clock correction.
    if now == 1 and type(now) is int and operation == "clock_correction":
        assert step(lab, current, operation, now=now).mode is RecoveryMode.PAUSED
        return
    before = snapshot(lab)
    with pytest.raises(ERROR):
        step(lab, current, operation, now=now)
    assert snapshot(lab) == before


def test_forward_clock_is_not_a_correction(lab, current):
    with pytest.raises(ERROR):
        step(lab, current, "clock_correction")


@pytest.mark.parametrize("delta,allowed", [(-1, False), (0, True), (10, True), (10.01, False)])
def test_completion_is_bound_to_claim_time_not_just_initial_review(lab, current, delta, allowed):
    step(lab, current, "prepare")
    claimed_at = current.now + 30
    step(lab, current, "claim", now=claimed_at)
    before = snapshot(lab)
    if allowed:
        assert step(lab, current, "complete", now=claimed_at + delta).mode is RecoveryMode.ACTIVE
    else:
        with pytest.raises(ERROR):
            step(lab, current, "complete", now=claimed_at + delta)
        assert snapshot(lab) == before


@pytest.mark.parametrize("operation", ["claim", "complete", "fail"])
def test_no_approval_is_not_a_pending_operation(lab, current, operation):
    before = snapshot(lab)
    with pytest.raises(ERROR):
        step(lab, current, operation)
    assert snapshot(lab) == before


@pytest.mark.parametrize("phase", ["prepare", "claim", "complete", "fail"])
def test_duplicate_preparation_and_old_terminal_ticket_refuse(lab, current, phase):
    progress(lab, current, "claim" if phase == "fail" else phase)
    if phase == "fail":
        step(lab, current, "fail")
    before = snapshot(lab)
    with pytest.raises(ERROR):
        step(lab, current, "prepare")
    assert snapshot(lab) == before


def test_legacy_digest_cannot_be_relabelled_as_new_consent(lab, current):
    plan = json.loads(current.selection.manifest)
    for old in plan["before"]["approvals"]:
        with pytest.raises(ERROR):
            step(lab, current, "prepare", inputs=replace(current.inputs, digest=old["digest"]))


@pytest.mark.parametrize("field,value", [
    ("epoch", "e" * 64), ("manifest_sha256", "e" * 64), ("fingerprint", "e" * 64),
    ("revision", 999), ("mode", RecoveryMode.ACTIVE)])
def test_snapshot_is_exact_not_revision_ordering(lab, current, field, value):
    before = snapshot(lab)
    with pytest.raises(ERROR):
        step(lab, current, "prepare", expected=replace(read(lab, current), **{field: value}))
    assert snapshot(lab) == before


@pytest.mark.parametrize("change", ["epoch", "manifest-version", "manifest-inode", "ledger-inode",
                                   "history", "manifest-bytes", "profile"])
def test_changed_selected_binding_refuses_without_fallback(lab, current, change):
    s = current.selection
    if change in {"epoch", "manifest-version"}:
        document = json.loads(s.manifest)
        document["epoch" if change == "epoch" else "version"] = "f" * 64 if change == "epoch" else 1
        changed = replace(s, manifest=_encoded(document))
    else:
        changed = replace(s, **{
            "manifest-inode": {
                "manifest_binding": (s.manifest_binding[0], s.manifest_binding[1] + 1)},
            "ledger-inode": {"ledger_binding": (s.ledger_binding[0], s.ledger_binding[1] + 1)},
            "history": {"history": replace(s.history, fingerprint="f" * 64)},
            "manifest-bytes": {"manifest": s.manifest + b" "},
            "profile": {"profile": s.profile.parent},
        }[change])
    before = snapshot(lab)
    with transaction(lab.ledger.path, readonly=True) as db, pytest.raises(ERROR):
        epoch._inspect_epoch(db, changed)
    assert snapshot(lab) == before


@pytest.mark.parametrize("statement", [
    "UPDATE recovery SET revision=revision+1", "UPDATE recovery SET mode='active'",
    "UPDATE recovery SET failures=1", "UPDATE recovery SET next_at=1",
    "UPDATE recovery SET identity='private'", "DELETE FROM recovery",
    "UPDATE browser_epoch_state SET epoch='private'",
    "UPDATE browser_epoch_state SET snapshot_sha256='private'",
    "UPDATE browser_epoch_state SET active_grant='private'", "DELETE FROM browser_epoch_state",
    "UPDATE browser_continuation SET manifest_inode=manifest_inode+1",
    "DELETE FROM browser_continuation", "PRAGMA user_version=2", "PRAGMA application_id=9",
    "CREATE TABLE extra(x)", "CREATE VIEW extra AS SELECT * FROM recovery",
    "CREATE TRIGGER extra AFTER UPDATE ON recovery BEGIN SELECT 1; END",
])
def test_unfenced_or_noncanonical_sql_is_not_current_permission(lab, current, statement):
    sql(lab, statement)
    before = snapshot(lab)
    with pytest.raises(ERROR):
        read(lab, current)
    assert snapshot(lab) == before


@pytest.mark.parametrize("field,value", [
    ("epoch", "e" * 64), ("identity", "e" * 64), ("phase", "unknown"),
    ("revision", 1), ("mode", "active"), ("device", "other"), ("generation", 0),
    ("created", -1), ("expires", 999), ("intent", "bad"), ("digest", "bad"),
    ("credential_hash", "bad"), ("trust_hash", "bad"), ("phase", "complete")])
def test_even_recomputed_fence_cannot_bypass_approval_invariants(lab, current, field, value):
    progress(lab, current, "prepare")
    with transaction(lab.ledger.path) as db:
        # Closed test parameter allowlist, never caller-selected runtime SQL.
        db.execute(f"UPDATE browser_epoch_approval SET {field}=?", (value,))
        s = current.selection
        rows = [dict(zip(native._EPOCH_COLUMNS, r, strict=True)) for r in db.execute(
            "SELECT * FROM browser_epoch_approval ORDER BY revision,digest").fetchall()]
        state = epoch._current_state(db.execute("SELECT * FROM recovery").fetchone(),
                                      s.history.identity)
        fence = native._epoch_fence(json.loads(s.manifest)["epoch"], state, rows, None)
        db.execute("UPDATE browser_epoch_state SET snapshot_sha256=?", (fence[2],))
        db.commit()
    before = snapshot(lab)
    with pytest.raises(ERROR):
        read(lab, current)
    assert snapshot(lab) == before


@pytest.mark.parametrize("operation", ["prepare", "claim", "complete", "fail", "pause",
                                       "clock_correction"])
def test_success_is_uncommitted_and_abandoned_stage_is_not_authority(lab, current, operation):
    if operation != "prepare":
        progress(lab, current, "prepare" if operation == "claim" else "claim")
    before = snapshot(lab)
    step(lab, current, operation, commit=False,
         now=current.now - 1 if operation == "clock_correction" else current.now)
    assert snapshot(lab) == before


@pytest.mark.parametrize("operation", ["prepare", "claim", "complete", "fail", "pause",
                                       "clock_correction"])
@pytest.mark.parametrize("fault", ["UPDATE recovery", "UPDATE browser_epoch_state"])
def test_write_failure_rolls_back_whole_transaction_even_if_caller_commits(
        lab, current, operation, fault):
    if operation != "prepare":
        progress(lab, current, "claim" if operation != "claim" else "prepare")
    expected, before = read(lab, current), snapshot(lab)
    args = [] if operation in {"pause", "clock_correction"} else [current.inputs]
    now = current.now - 1 if operation == "clock_correction" else current.now
    with transaction(lab.ledger.path) as db:
        db.fault = fault
        with pytest.raises(ERROR) as error:
            getattr(epoch, "_stage_" + operation)(db, current.selection, expected, *args, now=now)
        assert "PRIVATE" not in str(error.value)
        assert not db.in_transaction
        db.commit()
    assert snapshot(lab) == before


def test_failed_rollback_closes_connection(lab, current):
    expected, before = read(lab, current), snapshot(lab)
    with transaction(lab.ledger.path) as db:
        db.fault, db.rollback_fault = "INSERT INTO browser_epoch_approval", True
        with pytest.raises(ERROR):
            epoch._stage_prepare(db, current.selection, expected, current.inputs, now=current.now)
        with pytest.raises(sqlite3.ProgrammingError):
            db.commit()
    assert snapshot(lab) == before


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_final_readback_failure_or_interrupt_rolls_back(lab, current, monkeypatch, failure):
    expected, before = read(lab, current), snapshot(lab)
    original = epoch._read
    calls = []

    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 2:
            raise failure("PRIVATE readback interruption")
        return result

    with monkeypatch.context() as patch, transaction(lab.ledger.path) as db:
        patch.setattr(epoch, "_read", interrupted)
        with pytest.raises(ERROR if failure is RuntimeError else KeyboardInterrupt):
            epoch._stage_prepare(db, current.selection, expected, current.inputs, now=current.now)
        assert not db.in_transaction
        db.commit()
    assert snapshot(lab) == before


@pytest.mark.parametrize("policy", ["unstarted", "query-only", "trusted", "durability", "temp"])
def test_transaction_policy_is_not_silently_repaired(lab, current, policy):
    expected, before = read(lab, current), snapshot(lab)
    with transaction(lab.ledger.path) as db:
        if policy == "unstarted":
            db.rollback()
        elif policy == "durability":
            db.rollback()
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
        else:
            db.execute({"query-only": "PRAGMA query_only=ON", "trusted": "PRAGMA trusted_schema=ON",
                        "temp": "CREATE TEMP TABLE recovery(x)"}[policy])
        with pytest.raises(ERROR):
            epoch._stage_prepare(db, current.selection, expected, current.inputs, now=current.now)
        db.commit()
    assert snapshot(lab) == before


def test_terminal_legacy_rows_cannot_change_after_activation(lab, current):
    plan = json.loads(current.selection.manifest)
    if plan["before"]["schema"] == 2:
        sql(lab, "UPDATE browser_resume SET phase='failed'")
        with pytest.raises(ERROR):
            read(lab, current)


def test_approval_history_is_bounded_without_pruning_terminal_evidence(lab, current):
    for i in range(128):
        inputs = replace(current.inputs, digest=hashlib.sha256(str(i).encode()).hexdigest())
        step(lab, current, "prepare", inputs=inputs)
        step(lab, current, "fail", inputs=inputs)
    before = snapshot(lab)
    with pytest.raises(ERROR):
        step(lab, current, "prepare")
    assert snapshot(lab) == before and phases(lab) == [("failed",)] * 128
