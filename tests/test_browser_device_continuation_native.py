"""Portable transaction tests, not a filesystem adapter or deployment acceptance.

The history dataclass is deliberately assembled from a real native archive here;
only the separate namespace tests reconstruct the complete retained chain. These
fixtures must never be interpreted as proving current runtime permission.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
from types import SimpleNamespace

import pytest

from sds200 import browser_device_continuation_native as native
from sds200.browser_device_continuation_history import BrowserContinuationHistory
from sds200.browser_device_recovery import BrowserRecoveryError, RecoveryMode
from sds200.browser_device_resume import BrowserResumeError
from sds200.browser_device_resume_archive import _encoded, inspect_resume_archive
from sds200.browser_device_resume_maintenance import BrowserResumeMaintenance
from sds200.browser_device_resume_reconciliation import BrowserResumeReconciliation
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import private
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import INTENT, evidence, prepare, sql
from tests.test_browser_device_resume import lab as lab
from tests.test_browser_device_resume_maintenance import snapshot

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux native activation fixtures")
ERROR = native.BrowserContinuationNativeError


def inode(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino


class FaultConnection(sqlite3.Connection):
    fault = None
    rollback_fault = False

    def execute(self, statement, *args, **kwargs):
        result = super().execute(statement, *args, **kwargs)
        if self.fault and statement.startswith(self.fault):
            self.fault = None
            raise RuntimeError("PRIVATE injected failure after " + statement)
        return result

    def rollback(self):
        if self.rollback_fault:
            raise sqlite3.OperationalError("PRIVATE rollback failure")
        return super().rollback()


@contextmanager
def transaction(path, *, readonly=False):
    mode = "ro" if readonly else "rw"
    with closing(sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True,
                                 timeout=0, factory=FaultConnection)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        if readonly:
            db.execute("PRAGMA query_only=ON")
        else:
            db.execute("PRAGMA synchronous=EXTRA")
        db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
        yield db


@pytest.fixture(params=[("reconcile", 1), ("reconcile", 2), ("retire", 2)])
def candidate(lab, tmp_path, request):
    kind, schema = request.param
    old_approval = None
    if schema == 2:
        old_approval = prepare(lab)
        lab.ledger.suspend()
    archive = tmp_path / "native-history.json"
    if kind == "retire":
        core = BrowserResumeMaintenance(lab.root, clock=lambda: lab.clock[0])
        review = core.review()
        core.retire(review, archive=archive)
    else:
        core = BrowserResumeReconciliation(lab.root, clock=lambda: lab.clock[0])
        review = core.review(browser_intent="e" * 64)
        core.reconcile(review, archive=archive)
    plan = inspect_resume_archive(archive.read_bytes(), kind=kind,
        identity=lab.configuration.identity, profile=lab.root,
        expected_review=_encoded(asdict(review)))
    retained = BrowserContinuationHistory(lab.configuration.identity, lab.configuration.origin,
        lab.configuration.device_id, "1" * 64, "2" * 64, "3" * 64,
        plan.after, plan.proposed_revision)
    parameters = dict(history=retained, profile=lab.root, ledger_binding=inode(lab.ledger.path))
    preparation = dict(epoch="4" * 64,
                       consent_sha256=hashlib.sha256(b"fictional consent").hexdigest(),
                       reviewed_at=lab.clock[0], approved_at=lab.clock[0] + 1)
    manifest = native._prepare_activation_manifest(**parameters, **preparation)
    manifest_path = tmp_path / "activation.json"
    private(manifest_path, manifest)
    # Fictional caller-owned creation/sync, NOT an installed activation writer.
    with manifest_path.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    arguments = dict(**parameters, manifest=manifest, manifest_binding=inode(manifest_path))
    return SimpleNamespace(args=arguments, preparation=preparation, path=manifest_path,
                           now=lab.clock[0] + 2, old_approval=old_approval)


def activate(lab, candidate):
    with transaction(lab.ledger.path) as db:
        native._stage_paused_activation(db, **candidate.args, now=candidate.now)
        db.commit()


def inspect(lab, candidate, **changes):
    with transaction(lab.ledger.path, readonly=True) as db:
        return native._inspect_paused_activation(db, **{**candidate.args, **changes})


def test_atomic_paused_anchor_preserves_history_and_old_helpers_refuse(lab, candidate, monkeypatch):
    from sds200 import browser_device_native as exchange

    monkeypatch.setattr(exchange, "_post_browser_device",
                        lambda *a, **k: pytest.fail("Network I/O"))
    before = snapshot(lab)
    document = json.loads(candidate.args["manifest"])
    activate(lab, candidate)
    result = inspect(lab, candidate)
    assert result.revision == candidate.args["history"].native_revision + 1
    assert result.mode is RecoveryMode.PAUSED
    assert result.manifest_sha256 == hashlib.sha256(candidate.path.read_bytes()).hexdigest()
    assert (not hasattr(result, "session") and not hasattr(result, "permission")
            and not isinstance(result, BrowserContinuationHistory))
    with pytest.raises(FrozenInstanceError):
        result.revision += 1
    for private_value in (result.epoch, result.manifest_sha256, lab.configuration.identity,
                          str(lab.root), "fictional consent"):
        assert private_value not in repr(result)
    assert b"fictional consent" not in candidate.path.read_bytes()
    assert (lab.root / "device.secret").read_bytes().strip() not in candidate.path.read_bytes()
    assert {k: v for k, v in snapshot(lab).items() if k != "recovery.sqlite"} == {
        k: v for k, v in before.items() if k != "recovery.sqlite"}
    with transaction(lab.ledger.path, readonly=True) as db:
        assert native._snapshot(db, document["before"], activated=True) == document["after"]
    for call in (lab.ledger.inspect, lab.ledger.status, lab.ledger.suspend,
                 lambda: lab.ledger.resume(result.revision),
                 lambda: lab.ledger.authenticate(lambda: pytest.fail("Authentication"))):
        with pytest.raises(BrowserRecoveryError):
            call()
    with pytest.raises(BrowserResumeError):
        lab.core.prepare(expected_revision=result.revision, browser_intent=INTENT,
                         reviewed_server=evidence(lab.configuration))
    if candidate.old_approval:
        with pytest.raises(BrowserResumeError):
            lab.core.commit(candidate.old_approval, browser_intent=INTENT,
                            prove_server=lambda *a: pytest.fail("Old approval proof"))


def test_staging_is_not_commit_and_rollback_keeps_prepared_manifest(lab, candidate):
    before = snapshot(lab), candidate.path.read_bytes()
    with transaction(lab.ledger.path) as db:
        native._stage_paused_activation(db, **candidate.args, now=candidate.now)
        assert db.in_transaction
        assert lab.ledger.path.with_name("recovery.sqlite-journal").exists()
        with pytest.raises(ERROR):
            inspect(lab, candidate)  # Another reader still sees the legacy state.
        db.rollback()
    assert (snapshot(lab), candidate.path.read_bytes()) == before
    with pytest.raises(ERROR):
        inspect(lab, candidate)  # A fully synced manifest alone is never success.


@pytest.mark.parametrize("fault", ["CREATE TABLE browser_continuation", "INSERT INTO",
    "CREATE TABLE browser_epoch_approval", "CREATE TABLE browser_epoch_state",
    "INSERT INTO browser_epoch_state", "UPDATE recovery", "PRAGMA user_version=3",
    "SELECT * FROM browser_continuation", "SELECT * FROM browser_epoch_approval",
    "SELECT * FROM browser_epoch_state"])
def test_partial_failure_cannot_be_committed_by_outer_caller(lab, candidate, fault):
    before = snapshot(lab), candidate.path.read_bytes()
    with transaction(lab.ledger.path) as db:
        db.fault = fault
        with pytest.raises(ERROR) as error:
            native._stage_paused_activation(db, **candidate.args, now=candidate.now)
        assert "PRIVATE" not in str(error.value) and str(lab.root) not in str(error.value)
        db.commit()  # Deliberately misguided caller cannot commit a partial stage.
    assert (snapshot(lab), candidate.path.read_bytes()) == before
    with pytest.raises(ERROR):
        inspect(lab, candidate)


def test_lost_reply_readback_never_replays_or_renews_expired_consent(lab, candidate):
    activate(lab, candidate)  # Model a committed write whose reply was not received.
    before = snapshot(lab), candidate.path.read_bytes()
    first = inspect(lab, candidate)
    lab.clock[0] += 10000
    assert inspect(lab, candidate) == first
    with transaction(lab.ledger.path) as db, pytest.raises(ERROR):
        native._stage_paused_activation(db, **candidate.args, now=candidate.now)
    assert (snapshot(lab), candidate.path.read_bytes()) == before


@pytest.mark.parametrize("statement", [
    "UPDATE recovery SET revision=revision+1", "UPDATE recovery SET mode='active'",
    "UPDATE recovery SET observed_at=observed_at+1", "UPDATE recovery SET failures=1",
    "UPDATE recovery SET next_at=1", "UPDATE recovery SET identity='different'",
    "PRAGMA user_version=4", "PRAGMA application_id=1", "CREATE TABLE extra (x)",
    "CREATE VIEW extra AS SELECT * FROM recovery", "CREATE INDEX extra ON recovery(mode)",
    "CREATE TRIGGER extra AFTER UPDATE ON recovery BEGIN SELECT 1; END",
])
@pytest.mark.parametrize("after", [False, True])
def test_any_changed_state_or_noncanonical_schema_refuses_without_repair(
        lab, candidate, statement, after):
    if after:
        activate(lab, candidate)
    sql(lab, statement)
    before = snapshot(lab)
    if after:
        with pytest.raises(ERROR):
            inspect(lab, candidate)
    else:
        with transaction(lab.ledger.path) as db, pytest.raises(ERROR):
            native._stage_paused_activation(db, **candidate.args, now=candidate.now)
    assert snapshot(lab) == before


@pytest.mark.parametrize("field,value", [("epoch", "f" * 64), ("manifest_sha256", "f" * 64),
                                        ("manifest_device", 1), ("manifest_inode", 1)])
def test_wrong_anchor_cannot_confirm(lab, candidate, field, value):
    activate(lab, candidate)
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        db.execute(f"UPDATE browser_continuation SET {field}=?", (value,))
        db.commit()
    before = snapshot(lab)
    with pytest.raises(ERROR):
        inspect(lab, candidate)
    assert snapshot(lab) == before


@pytest.mark.parametrize("change", ["none", "bytes", "duplicate", "extra", "active", "revision",
    "expiry", "epoch", "identity", "intent", "history", "ledger", "oversize", "truncated"])
def test_malformed_or_substituted_manifest_refuses(lab, candidate, change):
    raw = candidate.args["manifest"]
    value = json.loads(raw)
    if change == "none":
        raw = None
    elif change == "bytes":
        raw += b" "
    elif change == "duplicate":
        raw = b'{"version":1,' + raw[1:]
    elif change == "extra":
        raw = _encoded({**value, "committed": True})
    elif change == "active":
        value["after"]["state"]["mode"] = "active"
        raw = _encoded(value)
    elif change == "revision":
        value["after"]["state"]["revision"] += 1
        raw = _encoded(value)
    elif change == "ledger":
        value["ledger_binding"][1] += 1
        raw = _encoded(value)
    elif change in {"expiry", "epoch", "identity", "intent", "history"}:
        key = {"expiry": "expires_at", "epoch": "epoch", "identity": "release_id",
               "intent": "intent_id", "history": "history_fingerprint"}[change]
        value[key] = False if change in {"epoch", "expiry"} else "e" * 64
        raw = _encoded(value)
    elif change == "oversize":
        raw = b" " * (native._LIMIT + 1)
    else:
        raw = raw[:17]
    before = snapshot(lab)
    with transaction(lab.ledger.path) as db, pytest.raises(ERROR):
        native._stage_paused_activation(db, **{**candidate.args, "manifest": raw},
                                        now=candidate.now)
    assert snapshot(lab) == before


@pytest.mark.parametrize("change", ["no-transaction", "query-only", "trusted", "synchronous",
                                    "attached", "temp-table", "temp-view",
                                    "expired", "backward", "nan"])
def test_transaction_policy_and_fresh_consent_window_are_required(lab, candidate, change):
    before = snapshot(lab)
    with transaction(lab.ledger.path) as db:
        now = candidate.now
        if change == "no-transaction":
            db.rollback()
        elif change == "query-only":
            db.execute("PRAGMA query_only=ON")
        elif change == "trusted":
            db.execute("PRAGMA trusted_schema=ON")
        elif change == "synchronous":
            db.rollback()
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
        elif change == "attached":
            db.execute("ATTACH ':memory:' AS other")
        elif change == "temp-table":
            db.execute("CREATE TEMP TABLE recovery (secret TEXT)")
        elif change == "temp-view":
            db.execute("CREATE TEMP VIEW recovery AS SELECT 1")
        elif change == "expired":
            now = candidate.preparation["reviewed_at"] + 120
        elif change == "backward":
            now = candidate.preparation["approved_at"] - 1
        else:
            now = float("nan")
        with pytest.raises(ERROR):
            native._stage_paused_activation(db, **candidate.args, now=now)
    assert snapshot(lab) == before


def test_transaction_recheck_allows_only_empty_sqlite_temp_schema(lab, candidate):
    with transaction(lab.ledger.path) as db:
        native._transaction(db, lab.root, readonly=False)
        db.execute("PRAGMA quick_check").fetchall()
        native._transaction(db, lab.root, readonly=False)
        native._stage_paused_activation(db, **candidate.args, now=candidate.now)
        native._transaction(db, lab.root, readonly=False)
        db.commit()
    inspect(lab, candidate)


@pytest.mark.parametrize("field,value", [
    ("mode", RecoveryMode.ACTIVE), ("native_revision", True), ("native_revision", 2**53 - 2),
    ("identity", "bad"), ("fingerprint", "bad"), ("native_after", b"{}\n"),
])
def test_history_shape_is_not_trusted_blindly(lab, candidate, field, value):
    arguments = {key: candidate.args[key] for key in ("history", "profile", "ledger_binding")}
    arguments["history"] = replace(arguments["history"], **{field: value})
    before = snapshot(lab)
    with pytest.raises(ERROR):
        native._prepare_activation_manifest(**arguments, **candidate.preparation)
    assert snapshot(lab) == before


@pytest.mark.parametrize("field,value", [
    ("epoch", False), ("consent_sha256", "plaintext"), ("reviewed_at", float("inf")),
    ("approved_at", True), ("ledger_binding", (True, 1)), ("ledger_binding", (1, 2**63)),
])
def test_manifest_preparation_rejects_invalid_selection(lab, candidate, field, value):
    arguments = {key: candidate.args[key] for key in ("history", "profile", "ledger_binding")}
    arguments.update(candidate.preparation)
    arguments[field] = value
    before = snapshot(lab)
    with pytest.raises(ERROR):
        native._prepare_activation_manifest(**arguments)
    assert snapshot(lab) == before


def test_terminal_history_cannot_be_reactivated_or_rewritten(lab, candidate):
    if candidate.old_approval is None:
        with transaction(lab.ledger.path, readonly=True) as db:
            assert db.execute("SELECT 1 FROM sqlite_schema WHERE name='browser_resume'")\
                .fetchall() == []
        return  # Schema 1 has no approval table by construction.
    for stage in (False, True):
        if stage:
            activate(lab, candidate)
        for phase in ("prepared", "claimed", "complete", "failed"):
            sql(lab, f"UPDATE browser_resume SET phase='{phase}'")
            before = snapshot(lab)
            if stage:
                with pytest.raises(ERROR):
                    inspect(lab, candidate)
            else:
                with transaction(lab.ledger.path) as db, pytest.raises(ERROR):
                    native._stage_paused_activation(db, **candidate.args, now=candidate.now)
            assert snapshot(lab) == before
            sql(lab, "UPDATE browser_resume SET phase='cancelled'")


@pytest.mark.parametrize("change", ["manifest-inode", "ledger-inode", "history", "missing-anchor",
                                   "missing-raw", "different-epoch", "writable-reader"])
def test_exact_readback_requires_every_selected_binding(lab, candidate, change):
    activate(lab, candidate)
    arguments = dict(candidate.args)
    if change in {"manifest-inode", "ledger-inode"}:
        key = "manifest_binding" if change == "manifest-inode" else "ledger_binding"
        arguments[key] = arguments[key][0], arguments[key][1] + 1
    elif change == "history":
        arguments["history"] = replace(arguments["history"], fingerprint="f" * 64)
    elif change == "missing-anchor":
        sql(lab, "DELETE FROM browser_continuation")
    elif change == "missing-raw":
        arguments["manifest"] = b""
    elif change == "different-epoch":
        value = json.loads(arguments["manifest"])
        arguments["manifest"] = _encoded({**value, "epoch": "f" * 64})
    before = snapshot(lab)
    with (transaction(lab.ledger.path, readonly=change != "writable-reader") as db,
          pytest.raises(ERROR)):
        native._inspect_paused_activation(db, **arguments)
    assert snapshot(lab) == before


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_final_validation_failure_or_interruption_aborts_entire_transaction(
        lab, candidate, monkeypatch, exception):
    original = native._exact_after

    def interrupt(*args):
        original(*args)
        raise exception("PRIVATE late interruption")

    monkeypatch.setattr(native, "_exact_after", interrupt)
    before = snapshot(lab)
    with transaction(lab.ledger.path) as db:
        with pytest.raises(ERROR if exception is RuntimeError else exception):
            native._stage_paused_activation(db, **candidate.args, now=candidate.now)
        assert not db.in_transaction
        db.commit()
    assert snapshot(lab) == before


@pytest.mark.parametrize("after", [False, True])
def test_wal_is_never_adopted_or_changed_to_rollback_mode(lab, candidate, after):
    if after:
        activate(lab, candidate)
    sql(lab, "PRAGMA journal_mode=WAL")
    with transaction(lab.ledger.path, readonly=after) as db:
        with pytest.raises(ERROR):
            if after:
                native._inspect_paused_activation(db, **candidate.args)
            else:
                native._stage_paused_activation(db, **candidate.args, now=candidate.now)
        assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_another_ledger_with_identical_bytes_is_not_selected(lab, candidate, tmp_path):
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    path = other / "recovery.sqlite"
    private(path, lab.ledger.path.read_bytes())
    before = path.read_bytes()
    with transaction(path) as db, pytest.raises(ERROR):
        native._stage_paused_activation(db, **candidate.args, now=candidate.now)
    assert path.read_bytes() == before


def test_integer_clock_values_roundtrip_through_sqlite_real(lab, candidate):
    arguments = {key: candidate.args[key] for key in ("history", "profile", "ledger_binding")}
    approved = int(candidate.preparation["approved_at"]) + 1
    raw = native._prepare_activation_manifest(**arguments,
        **{**candidate.preparation, "reviewed_at": approved - 1, "approved_at": approved})
    candidate.args["manifest"] = raw
    candidate.now = approved + 1
    candidate.path.write_bytes(raw)  # Fixture-only alternative to initial creation.
    activate(lab, candidate)
    assert inspect(lab, candidate).mode is RecoveryMode.PAUSED


def test_failed_rollback_closes_writer_and_cannot_be_committed(lab, candidate):
    before = snapshot(lab)
    with transaction(lab.ledger.path) as db:
        db.fault = "UPDATE recovery"
        db.rollback_fault = True
        with pytest.raises(ERROR) as error:
            native._stage_paused_activation(db, **candidate.args, now=candidate.now)
        assert "PRIVATE" not in str(error.value)
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.commit()
    assert snapshot(lab) == before
