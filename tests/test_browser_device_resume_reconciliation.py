"""No-matching-record fences: local synthetic ledgers, never deployment acceptance."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from threading import Event

import pytest

from sds200.browser_device_recovery import ExchangeFailure, RecoveryMode
from sds200.browser_device_resume import BrowserResumeError
from sds200.browser_device_resume_reconciliation import (
    BrowserResumeReconciliation,
    BrowserResumeReconciliationError,
)
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import INTENT, prepare, sql
from tests.test_browser_device_resume import lab as lab
from tests.test_browser_device_resume_maintenance import archive as archive
from tests.test_browser_device_resume_maintenance import history, snapshot

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux native reconciliation",
)


@pytest.fixture
def core(lab):
    return BrowserResumeReconciliation(lab.root, clock=lambda: lab.clock[0])


def seed_history(lab):
    prepare(lab, browser_intent="a" * 64)
    lab.ledger.suspend()


def version(lab):
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("mode", [mode for mode in RecoveryMode if mode is not RecoveryMode.ACTIVE])
def test_fence_preserves_history_schema_errors_and_pause(lab, core, archive, schema, mode):
    if schema == 2:
        seed_history(lab)
    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    before = snapshot(lab)
    old_rows = history(lab) if schema == 2 else []
    # Maintenance requires only client identity, not usable credentials or trust.
    (lab.root / "device.secret").unlink()
    (lab.root / "ca.pem").unlink()
    baseline = snapshot(lab)
    review = core.review(browser_intent=INTENT)
    assert snapshot(lab) == baseline
    assert review.approvals == int(schema == 2)
    assert INTENT not in repr(review) and review.fingerprint not in repr(review)
    proof = core.reconcile(review, archive=archive)
    assert (proof.mode, proof.revision) == (mode, review.revision + 1)
    assert proof.intent == INTENT and proof.identity == lab.configuration.identity
    assert all(v not in repr(proof) for v in (INTENT, proof.identity, proof.retirement))
    assert version(lab) == schema
    if schema == 2:
        assert history(lab) == old_rows
    document = json.loads(archive.read_bytes())
    old_state = document["before"]["state"]
    assert document["after"] == {
        **document["before"], "state": {**old_state, "revision": review.revision + 1}}
    assert archive.stat().st_mode & 0o777 == 0o600
    assert before["device.secret"].strip() not in archive.read_bytes()
    assert lab.ledger.authenticate(lambda: pytest.fail("No authentication")).session is None
    unchanged = snapshot(lab), archive.read_bytes()
    lab.clock[0] += 121  # Completion inspection is not a replay of expired consent.
    assert core.confirm(review, archive=archive, browser_intent=INTENT) == proof
    assert (snapshot(lab), archive.read_bytes()) == unchanged
    with pytest.raises(BrowserResumeReconciliationError):
        core.reconcile(review, archive=archive.parent / "retry.json")
    assert not (archive.parent / "retry.json").exists()


@pytest.mark.parametrize("phase", ["prepared", "claimed", "complete", "failed", "cancelled"])
def test_matching_row_of_any_phase_refuses_absence(lab, core, phase):
    prepare(lab)
    sql(lab, f"UPDATE browser_resume SET phase='{phase}'")
    before = snapshot(lab)
    with pytest.raises(BrowserResumeReconciliationError):
        core.review(browser_intent=INTENT)
    assert snapshot(lab) == before


@pytest.mark.parametrize("phase", ["prepared", "claimed"])
def test_unrelated_pending_approval_is_not_silently_cancelled(lab, core, phase):
    prepare(lab, browser_intent="a" * 64)
    sql(lab, f"UPDATE browser_resume SET phase='{phase}'")
    before = snapshot(lab)
    with pytest.raises(BrowserResumeReconciliationError):
        core.review(browser_intent=INTENT)
    assert snapshot(lab) == before


@pytest.mark.parametrize("intent", [None, True, 12, "", "x" * 64, "F" * 64, "f" * 64 + "\n"])
def test_invalid_intent_cannot_be_reviewed_or_confirmed(lab, core, archive, intent):
    review = core.review(browser_intent=INTENT)
    core.reconcile(review, archive=archive)
    before = snapshot(lab)
    with pytest.raises(BrowserResumeReconciliationError):
        core.review(browser_intent=intent)
    with pytest.raises(BrowserResumeReconciliationError):
        core.confirm(review, archive=archive, browser_intent=intent)
    assert snapshot(lab) == before


@pytest.mark.parametrize("changes", [
    {"intent": "a" * 64}, {"intent": True}, {"fingerprint": "0" * 64}, {"fingerprint": False},
    {"revision": True}, {"revision": 2**53}, {"approvals": True}, {"approvals": -1},
    {"approvals": 129}, {"mode": RecoveryMode.ACTIVE}, {"mode": "paused"},
    {"created_at": float("nan")}, {"expires_at": float("inf")}, {"expires_at": 10},
])
def test_invalid_or_altered_review_refused(lab, core, archive, changes):
    review = replace(core.review(browser_intent=INTENT), **changes)
    before = snapshot(lab)
    with pytest.raises(BrowserResumeReconciliationError):
        core.reconcile(review, archive=archive)
    assert snapshot(lab) == before and not archive.exists()


@pytest.mark.parametrize("change", ["prepare", "terminal-history", "active", "rollback", "expiry",
                                   "schema", "empty-v2", "wal", "maximum-revision"])
def test_changed_or_unsafe_native_state_refuses_without_archive(lab, core, archive, change):
    review = core.review(browser_intent=INTENT)
    if change == "prepare":
        prepare(lab)
    elif change == "terminal-history":
        seed_history(lab)
    elif change == "active":
        lab.ledger.resume(review.revision)
    elif change == "rollback":
        lab.clock[0] -= 1
    elif change == "expiry":
        lab.clock[0] += 120
    elif change == "schema":
        sql(lab, "PRAGMA user_version=3")
    elif change == "empty-v2":
        seed_history(lab)
        sql(lab, "DELETE FROM browser_resume")
    elif change == "wal":
        sql(lab, "PRAGMA journal_mode=WAL")
    else:
        sql(lab, f"UPDATE recovery SET revision={2**53 - 2}")
    before = snapshot(lab)
    with pytest.raises(BrowserResumeReconciliationError):
        core.reconcile(review, archive=archive)
    assert snapshot(lab) == before and not archive.exists()
    if change not in {"expiry", "terminal-history"}:
        with pytest.raises(BrowserResumeReconciliationError):
            core.review(browser_intent=INTENT)
        assert snapshot(lab) == before


def test_failed_prepare_rolls_back_schema_and_can_be_reconciled(lab, core, archive, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(lab.core._recovery, "_save", lambda *_: (_ for _ in ()).throw(OSError()))
        with pytest.raises(BrowserResumeError):
            prepare(lab)
    assert version(lab) == 1
    review = core.review(browser_intent=INTENT)
    assert core.reconcile(review, archive=archive).mode is RecoveryMode.PAUSED
    assert version(lab) == 1


@pytest.mark.parametrize("stage", ["archive", "post-archive", "expired-archive", "clock-rollback",
                                  "save", "lost-ack"])
def test_failure_retains_uncertainty_and_confirmation_never_retries(lab, core, archive,
                                                                  monkeypatch, stage):
    review = core.review(browser_intent=INTENT)
    original = core._history._archive

    def write(*args):
        if stage == "archive":
            raise OSError()
        original(*args)
        if stage == "post-archive":
            raise OSError()
        if stage == "expired-archive":
            lab.clock[0] += 120
        if stage == "clock-rollback":
            lab.clock[0] -= 1

    before = snapshot(lab)
    with monkeypatch.context() as patch:
        patch.setattr(core._history, "_archive", write)
        if stage == "save":
            patch.setattr(core._recovery, "_save", lambda *_: (_ for _ in ()).throw(OSError()))
        if stage == "lost-ack":
            patch.setattr(core, "confirm", lambda *_, **__: (_ for _ in ()).throw(OSError()))
        with pytest.raises(BrowserResumeReconciliationError):
            core.reconcile(review, archive=archive)
    assert archive.exists() == (stage != "archive")
    after = snapshot(lab)
    if stage == "lost-ack":
        proof = core.confirm(review, archive=archive, browser_intent=INTENT)
        assert proof.revision == review.revision + 1
    else:
        assert after == before
        with pytest.raises(BrowserResumeReconciliationError):
            core.confirm(review, archive=archive, browser_intent=INTENT)
    assert snapshot(lab) == after


@pytest.mark.parametrize("change", ["intent", "operation", "before", "after", "version", "extra",
                                   "duplicate", "new-revision", "new-intent", "clock"])
def test_confirmation_is_exact_and_read_only(lab, core, archive, change):
    review = core.review(browser_intent=INTENT)
    core.reconcile(review, archive=archive)
    intent = INTENT
    if change == "intent":
        intent = "e" * 64
    elif change == "new-revision":
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    elif change == "new-intent":
        prepare(lab)
    elif change == "clock":
        lab.clock[0] -= 1
    elif change == "duplicate":
        archive.write_text(archive.read_text().replace('"version":1', '"version":1,"version":1'))
    else:
        document = json.loads(archive.read_bytes())
        if change in {"before", "after"}:
            document[change]["state"]["revision"] += 1
        else:
            document[change] = True
        archive.write_text(json.dumps(document))
    before = snapshot(lab), archive.read_bytes()
    with pytest.raises(BrowserResumeReconciliationError):
        core.confirm(review, archive=archive, browser_intent=intent)
    assert (snapshot(lab), archive.read_bytes()) == before


@pytest.mark.parametrize("kind", ["existing", "symlink", "hardlink", "public-parent", "in-profile"])
def test_unsafe_archive_never_changes_ledger(lab, core, archive, kind):
    review = core.review(browser_intent=INTENT)
    if kind in {"existing", "symlink", "hardlink"}:
        other = archive.parent / "other"
        other.write_text("retain")
        other.chmod(0o600)
        if kind == "existing":
            archive.write_text("retain")
        elif kind == "symlink":
            archive.symlink_to(other)
        else:
            os.link(other, archive)
    elif kind == "public-parent":
        archive.parent.chmod(0o755)
    else:
        archive = lab.root / "archive.json"
    before = snapshot(lab)
    with pytest.raises(BrowserResumeReconciliationError):
        core.reconcile(review, archive=archive)
    assert snapshot(lab) == before


def test_copy_of_same_identity_profile_cannot_borrow_review(lab, core, archive):
    review = core.review(browser_intent=INTENT)
    copied = lab.root.parent / "copied"
    shutil.copytree(lab.root, copied)
    other = BrowserResumeReconciliation(copied, clock=lambda: lab.clock[0])
    with pytest.raises(BrowserResumeReconciliationError):
        other.reconcile(review, archive=archive)
    core.reconcile(review, archive=archive)
    with pytest.raises(BrowserResumeReconciliationError):
        other.confirm(review, archive=archive, browser_intent=INTENT)


@pytest.mark.parametrize("first", ["prepare", "reconcile"])
def test_delayed_prepare_and_fence_are_serialized(lab, core, archive, monkeypatch, first):
    review = core.review(browser_intent=INTENT)
    reached, release = Event(), Event()
    original = lab.core._inputs

    def inputs():
        result = original()
        reached.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(lab.core, "_inputs", inputs)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(prepare, lab, expected_revision=review.revision)
        try:
            assert reached.wait(5)
            if first == "reconcile":
                proof = core.reconcile(review, archive=archive)
                release.set()
                with pytest.raises(BrowserResumeError):
                    task.result(timeout=5)
                assert lab.ledger.inspect().revision == proof.revision
                assert version(lab) == 1
            else:
                release.set()
                task.result(timeout=5)
                with pytest.raises(BrowserResumeReconciliationError):
                    core.reconcile(review, archive=archive)
                assert not archive.exists()
        finally:
            release.set()
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED


def test_concurrent_reconciliations_commit_only_once(lab, core, archive):
    review = core.review(browser_intent=INTENT)
    archives = [archive, archive.parent / "second.json"]

    def run(path):
        try:
            return core.reconcile(review, archive=path)
        except BrowserResumeReconciliationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, archives))
    assert sum(result is not None for result in results) == 1
    assert sum(path.exists() for path in archives) == 1
    assert lab.ledger.inspect().revision == review.revision + 1


def test_full_terminal_history_is_preserved_not_retired(lab, core, archive):
    for _ in range(128):
        seed_history(lab)
    before = history(lab)
    review = core.review(browser_intent=INTENT)
    assert review.approvals == 128
    core.reconcile(review, archive=archive)
    assert history(lab) == before
    with pytest.raises(BrowserResumeError):
        prepare(lab)  # Reconciliation never bypasses the history capacity limit.


@pytest.mark.parametrize("stage", ["archived", "committed"])
def test_process_death_has_read_only_unambiguous_completion_check(lab, core, archive, stage):
    review = core.review(browser_intent=INTENT)
    before = snapshot(lab)
    script = """
import json, os, sys
from pathlib import Path
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_resume_reconciliation import (
    BrowserResumeReconciliation, BrowserResumeReconciliationReview)
value = json.load(sys.stdin)
value['mode'] = RecoveryMode(value['mode'])
core = BrowserResumeReconciliation(Path(sys.argv[1]),clock=lambda:value['created_at'])
if sys.argv[3] == 'archived':
    write = core._history._archive
    def interrupted(*args):
        write(*args)
        os._exit(73)
    core._history._archive = interrupted
else:
    core.confirm = lambda *args, **kwargs: os._exit(73)
core.reconcile(BrowserResumeReconciliationReview(**value),archive=Path(sys.argv[2]))
"""
    result = subprocess.run([sys.executable, "-c", script, str(lab.root), str(archive), stage],
                            input=json.dumps(asdict(review)), capture_output=True, text=True,
                            timeout=10)
    assert result.returncode == 73 and result.stdout == result.stderr == ""
    assert archive.exists()
    after = snapshot(lab)
    if stage == "archived":
        assert after == before
        with pytest.raises(BrowserResumeReconciliationError):
            core.confirm(review, archive=archive, browser_intent=INTENT)
    else:
        assert core.confirm(review, archive=archive, browser_intent=INTENT).revision == (
            review.revision + 1)
    assert snapshot(lab) == after
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED


def test_no_reconciliation_action_is_exposed_by_native_protocol(lab):
    from tests.test_browser_device_native import invoke

    before = snapshot(lab)
    for action in ("reconcile", "resume-reconcile", "confirm-missing-intent"):
        assert invoke(lab.root, action)["ok"] is False
    assert snapshot(lab) == before
