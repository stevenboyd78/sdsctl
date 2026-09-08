"""Isolated approval core; no public resume action or browser consent adapter."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from sds200 import browser_device_resume as resume_module
from sds200.browser_device_admin import BrowserAdminResult, BrowserAdminStatus
from sds200.browser_device_owner import BrowserOwnerReceipt
from sds200.browser_device_recovery import (
    BrowserDeviceRecovery,
    BrowserRecoveryError,
    ExchangeFailure,
    RecoveryMode,
)
from sds200.browser_device_resume import (
    BrowserDeviceResume,
    BrowserResumeApproval,
    BrowserResumeError,
    BrowserResumeEvidence,
)
from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from tests import test_browser_device_resume_boundaries as boundaries
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import configure, initialize, invoke, private
from tests.test_browser_device_native import root as root

authority_lab = boundaries.lab

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux native approval core"
)
INTENT = "f" * 64


def evidence(configuration, record=None, status=BrowserAdminStatus.CONFIRMED, completed=True):
    record = record or BrowserDeviceRecord("display", 4, BrowserDeviceState.ACTIVE)
    return BrowserResumeEvidence(
        configuration.identity,
        BrowserAdminResult(
            record,
            status,
            BrowserOwnerReceipt(record, "a" * 32, 123, completed),
        ),
    )


@pytest.fixture
def lab(root, certificates):
    private(root / "ca.pem", certificates[0][0].read_bytes())
    configuration = initialize(root)
    clock = [time.time()]
    elapsed = [10.0]
    ledger = BrowserDeviceRecovery(
        root / "recovery.sqlite", configuration.identity, clock=lambda: clock[0]
    )
    ledger.claim_browser()
    ledger.suspend()
    core = BrowserDeviceResume(root, clock=lambda: clock[0], monotonic=lambda: elapsed[0])
    return SimpleNamespace(
        root=root,
        configuration=configuration,
        ledger=ledger,
        clock=clock,
        elapsed=elapsed,
        core=core,
    )


def prepare(lab, **changes):
    arguments = dict(
        expected_revision=lab.ledger.inspect().revision,
        browser_intent=INTENT,
        reviewed_server=evidence(lab.configuration),
    )
    arguments.update(changes)
    return lab.core.prepare(**arguments)


def commit(lab, approval, **changes):
    arguments = dict(browser_intent=INTENT, prove_server=evidence)
    arguments.update(changes)
    return lab.core.commit(approval, **arguments)


def rows(lab):
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        return db.execute(
            "SELECT digest,phase,revision FROM browser_resume ORDER BY rowid"
        ).fetchall()


def sql(lab, command):
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        db.execute(command)
        db.commit()


def no_proof(*_):
    pytest.fail("A refused operation called the server proof adapter")


def test_prepare_is_explicit_private_and_keeps_native_stopped(lab):
    before = lab.ledger.inspect()
    approval = prepare(lab)
    after = lab.ledger.inspect()
    assert after.mode is RecoveryMode.PAUSED and after.revision == before.revision + 1
    assert approval.revision == after.revision
    assert approval.expires_at == lab.clock[0] + 120
    assert approval.ticket not in repr(approval)
    data = lab.ledger.path.read_bytes()
    assert approval.ticket.encode() not in data
    assert (lab.root / "device.secret").read_bytes() not in data
    assert rows(lab) == [
        (hashlib.sha256(approval.ticket.encode()).hexdigest(), "prepared", approval.revision)
    ]
    assert lab.ledger.path.stat().st_mode & 0o777 == 0o600
    assert lab.ledger.authenticate(lambda: pytest.fail("Preparation authenticated")).session is None


def test_confirmation_consumes_once_and_does_not_issue_a_session(lab):
    approval = prepare(lab)

    def proof(configuration, expected):
        assert configuration == lab.configuration
        assert rows(lab)[0][1] == "claimed"
        assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
        return evidence(configuration, expected)

    result = commit(lab, approval, prove_server=proof)
    assert result.mode is RecoveryMode.ACTIVE and result.revision == approval.revision + 1
    assert not hasattr(result, "session")
    assert rows(lab)[0][1] == "complete"
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize(
    "mode", [value for value in RecoveryMode if value is not RecoveryMode.ACTIVE]
)
def test_each_stopped_mode_requires_fresh_explicit_approval(lab, mode):
    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    approval = prepare(lab)
    assert lab.ledger.inspect().mode is mode
    assert commit(lab, approval).mode is RecoveryMode.ACTIVE


@pytest.mark.parametrize(
    "change",
    [
        {"expected_revision": True},
        {"expected_revision": 0},
        {"expected_revision": 2**53},
        {"expected_revision": 1},
        {"browser_intent": ""},
        {"browser_intent": "f" * 63},
        {"browser_intent": None},
        {"browser_intent": "f" * 64 + "\n"},
        {"reviewed_server": None},
    ],
)
def test_invalid_prepare_never_migrates_or_mutates(lab, change):
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        prepare(lab, **change)
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize(
    "status", [s for s in BrowserAdminStatus if s is not BrowserAdminStatus.CONFIRMED]
)
def test_unconfirmed_review_is_not_permission(lab, status):
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        prepare(lab, reviewed_server=evidence(lab.configuration, status=status))
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("change", ["identity", "device", "paused", "revoked", "receipt", "drain"])
def test_prepare_rejects_wrong_installation_device_state_or_receipt(lab, change):
    proof = evidence(lab.configuration)
    result = proof.result
    if change == "identity":
        proof = replace(proof, identity="b" * 64)
    elif change in {"device", "paused", "revoked"}:
        record = (
            replace(result.record, device_id="other")
            if change == "device"
            else replace(result.record, state=BrowserDeviceState(change))
        )
        proof = evidence(lab.configuration, record)
    else:
        proof = replace(
            proof,
            result=replace(
                result,
                receipt=None if change == "receipt" else replace(result.receipt, completed=False),
            ),
        )
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        prepare(lab, reviewed_server=proof)
    assert lab.ledger.path.read_bytes() == before


def test_active_and_pristine_ledgers_are_not_resume_targets(lab):
    lab.ledger.resume(lab.ledger.inspect().revision)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        prepare(lab)
    assert lab.ledger.path.read_bytes() == before


def test_second_approval_cannot_replace_outstanding_review(lab):
    first = prepare(lab)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        prepare(lab)
    assert lab.ledger.path.read_bytes() == before
    assert commit(lab, first).mode is RecoveryMode.ACTIVE


@pytest.mark.parametrize("change", ["ticket", "revision", "expiry", "intent"])
def test_wrong_commit_does_not_consume_someone_elses_review(lab, change):
    approval = prepare(lab)
    wrong = replace(
        approval,
        **{
            "ticket": {"ticket": "0" * 64},
            "revision": {"revision": approval.revision + 1},
            "expiry": {"expires_at": approval.expires_at + 1},
            "intent": {},
        }[change],
    )
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        commit(
            lab,
            wrong,
            browser_intent="a" * 64 if change == "intent" else INTENT,
            prove_server=no_proof,
        )
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("when", ["before", "during"])
def test_new_pause_cancels_even_an_already_paused_ledger(lab, when):
    approval = prepare(lab)

    def proof(configuration, expected):
        lab.ledger.suspend()
        return evidence(configuration, expected)

    if when == "before":
        paused = lab.ledger.suspend()
        assert paused.revision == approval.revision + 1
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof if when == "before" else proof)
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert rows(lab)[0][1] == "cancelled"
    # Idempotent cleanup remains idempotent once that cancellation is saved.
    snapshot = lab.ledger.path.read_bytes()
    lab.ledger.suspend()
    assert lab.ledger.path.read_bytes() == snapshot


def test_real_native_suspend_cancels_outstanding_approval(lab):
    approval = prepare(lab)
    result = invoke(lab.root, "suspend")
    assert result["mode"] == "paused" and result["revision"] == approval.revision + 1
    assert rows(lab)[0][1] == "cancelled"
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)


@pytest.mark.parametrize("change", ["raise", "generation", "identity", "pending", "timeout"])
def test_failed_or_stale_proof_is_consumed_without_resuming(lab, change):
    approval = prepare(lab)

    def proof(configuration, expected):
        if change == "raise":
            raise RuntimeError("PRIVATE fixture error")
        if change == "generation":
            expected = replace(expected, generation=expected.generation + 1)
        if change == "timeout":
            lab.elapsed[0] += 10.001
        result = evidence(
            configuration,
            expected,
            status=BrowserAdminStatus.PENDING
            if change == "pending"
            else BrowserAdminStatus.CONFIRMED,
        )
        return replace(result, identity="d" * 64) if change == "identity" else result

    with pytest.raises(BrowserResumeError) as error:
        commit(lab, approval, prove_server=proof)
    assert "PRIVATE" not in str(error.value) and approval.ticket not in str(error.value)
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert rows(lab)[0][1] == "failed"
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)


@pytest.mark.parametrize("offset", [-1, 120, 121])
def test_time_refusal_cannot_be_revived_by_clock_repair(lab, offset):
    approval = prepare(lab)
    baseline = lab.clock[0]
    lab.clock[0] += offset
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert rows(lab)[0][1] == "failed"
    lab.clock[0] = baseline + 1
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("target", ["device.secret", "ca.pem", "client.json"])
def test_changed_private_inputs_cannot_be_authorized_by_old_review(lab, when, target, certificates):
    approval = prepare(lab)

    def change():
        value = {
            "device.secret": "sdsctl-browser-v1." + "e" * 64,
            "ca.pem": certificates[1][0].read_bytes(),
            "client.json": "{}",
        }[target]
        private(lab.root / target, value)

    def proof(configuration, expected):
        change()
        return evidence(configuration, expected)

    if when == "before":
        change()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof if when == "before" else proof)
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert rows(lab)[0][1] == ("prepared" if when == "before" else "failed")


def test_two_committers_have_one_winner_and_pause_does_not_wait_for_proof(lab):
    approval = prepare(lab)
    entered, release = Event(), Event()

    def proof(configuration, expected):
        entered.set()
        assert release.wait(5)
        return evidence(configuration, expected)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(commit, lab, approval, prove_server=proof)
        try:
            assert entered.wait(3)
            with pytest.raises(BrowserResumeError):
                commit(lab, approval, prove_server=no_proof)
            lab.ledger.suspend()  # Finishes while proof remains blocked.
        finally:
            release.set()
        with pytest.raises(BrowserResumeError):
            future.result(timeout=3)
    assert rows(lab)[0][1] == "cancelled"


def test_interruption_after_claim_is_never_replayed_on_reopen(lab):
    approval = prepare(lab)

    def interrupted(*_):
        raise KeyboardInterrupt()  # Emulates process loss, bypassing ordinary error cleanup.

    with pytest.raises(KeyboardInterrupt):
        commit(lab, approval, prove_server=interrupted)
    assert rows(lab)[0][1] == "claimed"
    lab.core = BrowserDeviceResume(lab.root, clock=lambda: lab.clock[0])
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    with pytest.raises(BrowserResumeError):
        prepare(lab)
    lab.ledger.suspend()
    assert rows(lab)[0][1] == "cancelled"
    assert prepare(lab).ticket != approval.ticket


def test_offline_inspection_is_read_only_after_explicit_schema_upgrade(lab):
    prepare(lab)
    before = lab.ledger.path.read_bytes()
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize(
    "corruption",
    [
        "DELETE FROM browser_resume",
        "DELETE FROM recovery",
        "DROP TABLE browser_resume",
        "UPDATE browser_resume SET intent='invalid'",
        "UPDATE browser_resume SET phase='invalid'",
        "UPDATE browser_resume SET revision=0",
        "UPDATE browser_resume SET expires=created+121",
        "UPDATE browser_resume SET identity='b'",
        "UPDATE browser_resume SET device='invalid ID'",
        "PRAGMA user_version=1",
    ],
)
def test_corrupt_or_downgraded_resume_ledger_never_opens_or_resets(lab, corruption):
    approval = prepare(lab)
    sql(lab, corruption)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserRecoveryError):
        lab.ledger.inspect()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.path.read_bytes() == before


def test_pristine_native_profile_is_not_a_resume_target(root, certificates):
    private(root / "ca.pem", certificates[0][0].read_bytes())
    configuration = initialize(root)
    before = (root / "recovery.sqlite").read_bytes()
    with pytest.raises(BrowserResumeError):
        BrowserDeviceResume(root).prepare(
            expected_revision=1, browser_intent=INTENT, reviewed_server=evidence(configuration)
        )
    assert (root / "recovery.sqlite").read_bytes() == before


def test_helper_clock_correction_cancels_approval_without_corrupting_schema(lab):
    approval = prepare(lab)
    lab.clock[0] -= 20
    assert lab.ledger.status().mode is RecoveryMode.PAUSED
    assert rows(lab)[0][1] == "cancelled"
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    lab.clock[0] += 21
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)


def test_committed_but_lost_acknowledgement_cannot_replay_native_reset(lab, monkeypatch):
    approval = prepare(lab)

    def lost(*_):
        raise RuntimeError("PRIVATE post-commit fixture failure")

    with monkeypatch.context() as patch:
        patch.setattr(lab.core._recovery, "_status", lost)
        with pytest.raises(BrowserResumeError):
            commit(lab, approval)
    assert rows(lab)[0][1] == "complete"
    assert lab.ledger.inspect().mode is RecoveryMode.ACTIVE
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.path.read_bytes() == before
    # The future browser adapter must keep its own pending pause on a lost reply.
    # Native permission alone cannot be reported as signed in or session ready.


def test_actual_process_loss_after_claim_leaves_a_consumed_operation(lab):
    approval = prepare(lab)
    script = """
import json, os, sys
from pathlib import Path
from sds200.browser_device_resume import BrowserDeviceResume, BrowserResumeApproval
message = json.load(sys.stdin)
core = BrowserDeviceResume(Path(sys.argv[1]))
core.commit(BrowserResumeApproval(**message['approval']), browser_intent=message['intent'],
            prove_server=lambda *_: os._exit(73))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(lab.root)],
        input=json.dumps(
            {
                "approval": {
                    "ticket": approval.ticket,
                    "revision": approval.revision,
                    "expires_at": approval.expires_at,
                },
                "intent": INTENT,
            }
        ),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 73 and result.stdout == result.stderr == ""
    assert rows(lab)[0][1] == "claimed"
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)


@pytest.mark.parametrize("stale", [False, True])
def test_real_server_owner_evidence_is_rechecked_before_native_permission(
    root,
    certificates,
    authority_lab,
    stale,
):
    # Real ASGI authority/owner, not a network/TLS evidence adapter. No session is
    # requested. Match the configured DNS/IPv4/IPv6 identity in both fixtures.
    authority = authority_lab
    configure(root, "https://" + authority.client.headers["host"])
    private(root / "ca.pem", certificates[0][0].read_bytes())
    configuration = initialize(root)
    ledger = BrowserDeviceRecovery(root / "recovery.sqlite", configuration.identity)
    paused = ledger.suspend()
    core = BrowserDeviceResume(root)
    reviewed = BrowserResumeEvidence(
        configuration.identity, authority.admin.confirm(authority.issued.record)
    )
    approval = core.prepare(
        expected_revision=paused.revision, browser_intent=INTENT, reviewed_server=reviewed
    )

    def proof(config, expected):
        assert config == configuration
        if stale:
            authority.admin.transition(expected, BrowserDeviceState.PAUSED)
        return BrowserResumeEvidence(config.identity, authority.admin.confirm(expected))

    if stale:
        with pytest.raises(BrowserResumeError):
            core.commit(approval, browser_intent=INTENT, prove_server=proof)
        assert ledger.inspect().mode is RecoveryMode.PAUSED
    else:
        assert (
            core.commit(approval, browser_intent=INTENT, prove_server=proof).mode
            is RecoveryMode.ACTIVE
        )
    assert authority.exchanges == 0


def test_failed_prepare_rolls_back_schema_and_native_revision(lab, monkeypatch):
    before = lab.ledger.path.read_bytes()

    def reject(_):
        raise BrowserRecoveryError()

    monkeypatch.setattr(resume_module, "validate_resume_state", reject)
    with pytest.raises(BrowserResumeError):
        prepare(lab)
    assert lab.ledger.path.read_bytes() == before
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        assert (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='browser_resume'").fetchone() is None
        )


def test_failed_final_commit_rolls_back_native_permission(lab, monkeypatch):
    approval = prepare(lab)
    original = lab.core._recovery._save

    def interrupted(db, state):
        original(db, state)
        if state.mode is RecoveryMode.ACTIVE:
            raise OSError("PRIVATE simulated final write failure")

    monkeypatch.setattr(lab.core._recovery, "_save", interrupted)
    with pytest.raises(BrowserResumeError):
        commit(lab, approval)
    assert rows(lab)[0][1] == "failed"
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert lab.ledger.inspect().revision == approval.revision


@pytest.mark.parametrize("elapsed", [-1, float("nan"), float("inf")])
def test_invalid_monotonic_proof_deadline_is_consumed(lab, elapsed):
    approval = prepare(lab)

    def proof(configuration, expected):
        lab.elapsed[0] += elapsed
        return evidence(configuration, expected)

    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=proof)
    assert rows(lab)[0][1] == "failed"
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED


def test_bounded_approval_history_is_retained_not_auto_deleted(lab, monkeypatch):
    monkeypatch.setattr(resume_module, "_MAX_APPROVALS", 2)
    for _ in range(2):
        assert commit(lab, prepare(lab)).mode is RecoveryMode.ACTIVE
        lab.ledger.suspend()
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        prepare(lab)
    assert lab.ledger.path.read_bytes() == before
    assert [row[1] for row in rows(lab)] == ["complete", "complete"]


def test_ticket_collision_cannot_overwrite_a_completed_operation(lab, monkeypatch):
    first = prepare(lab)
    commit(lab, first)
    lab.ledger.suspend()
    monkeypatch.setattr(resume_module.secrets, "token_hex", lambda _: first.ticket)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        prepare(lab)
    assert lab.ledger.path.read_bytes() == before and len(rows(lab)) == 1


def test_separate_trusted_reset_invalidates_pending_approval(lab):
    approval = prepare(lab)
    lab.ledger.resume(approval.revision)
    assert rows(lab)[0][1] == "cancelled"
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("file", ["device.secret", "ca.pem", "recovery.sqlite"])
def test_unsafe_files_are_refused_without_repair(lab, file):
    approval = prepare(lab)
    path = lab.root / file
    path.chmod(0o644)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(
    "command",
    [
        "UPDATE browser_resume SET revision=revision+1",
        "UPDATE recovery SET mode='active'",
        "INSERT INTO browser_resume SELECT '" + "0" * 64 + "',identity,phase,revision,mode,intent,"
        "device,generation,credential_hash,trust_hash,created,expires FROM browser_resume",
    ],
)
def test_pending_approval_inconsistency_fails_closed(lab, command):
    prepare(lab)
    sql(lab, command)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserRecoveryError):
        lab.ledger.inspect()
    with pytest.raises(BrowserRecoveryError):
        lab.ledger.authenticate(lambda: pytest.fail("Corrupt state attempted authentication"))
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize(
    "approval",
    [
        None,
        {},
        BrowserResumeApproval("invalid", 1, 120),
        BrowserResumeApproval("a" * 64, True, 120),
        BrowserResumeApproval("a" * 64, 1, float("nan")),
    ],
)
def test_malformed_approval_never_mutates_or_calls_proof(lab, approval):
    prepare(lab)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.path.read_bytes() == before


def test_valid_but_changed_configuration_is_not_the_reviewed_identity(lab):
    approval = prepare(lab)
    configure(lab.root, "https://different.example")
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)
    assert lab.ledger.path.read_bytes() == before


def test_failure_to_record_proof_failure_retains_consumed_claim(lab):
    approval = prepare(lab)

    def unavailable(*_):
        lab.ledger.path.chmod(0o644)
        raise OSError("PRIVATE fixture permission change")

    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=unavailable)
    assert lab.ledger.path.stat().st_mode & 0o777 == 0o644  # Engine does not repair it.
    assert rows(lab)[0][1] == "claimed"
    lab.ledger.path.chmod(0o600)  # Restore ONLY this test-owned file for inspection.
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    with pytest.raises(BrowserResumeError):
        commit(lab, approval, prove_server=no_proof)


def test_invalid_initial_configuration_is_redacted(lab):
    private(lab.root / "client.json", "PRIVATE invalid fixture")
    with pytest.raises(BrowserResumeError) as error:
        BrowserDeviceResume(lab.root)
    assert "PRIVATE" not in str(error.value) and str(lab.root) not in str(error.value)
