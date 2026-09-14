"""Archive structure is neither proof of native commit nor current permission."""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_resume_archive import (
    BrowserResumeArchiveError,
    BrowserResumeArchivePlan,
    _encoded,
    _fingerprint,
    inspect_resume_archive,
)
from sds200.browser_device_resume_maintenance import (
    BrowserResumeMaintenance,
    BrowserResumeMaintenanceError,
    BrowserResumeRetirementEvidence,
)
from sds200.browser_device_resume_reconciliation import (
    BrowserResumeReconciliation,
    BrowserResumeReconciliationError,
)
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_resume import INTENT, prepare
from tests.test_browser_device_resume import lab as lab
from tests.test_browser_device_resume_maintenance import snapshot

_PROFILE = Path("/fictional/native-profile")
_IDENTITY = "1" * 64


def document(kind="retire", *, schema=2, mode="paused", phase="claimed"):
    state = dict(revision=4, mode=mode, failures=0, next_at=0.0, observed_at=1000.0)
    row = dict(digest="2" * 64, identity=_IDENTITY, phase=phase, revision=4,
        mode=mode, intent="3" * 64, device="fictional-display", generation=2,
        credential_hash="4" * 64, trust_hash="5" * 64, created=990.0, expires=1110.0)
    before = dict(identity=_IDENTITY, profile=str(_PROFILE), state=state,
                  approvals=[row] if schema == 2 else [])
    review = dict(revision=4, mode=mode, approvals=len(before["approvals"]),
                  created_at=1010.0, expires_at=1130.0)
    if kind == "retire":
        review["pending"] = int(phase in {"prepared", "claimed"})
    else:
        review["intent"] = "6" * 64
        before.update(schema=schema, intent=review["intent"])
    review["fingerprint"] = _fingerprint(before)
    after = copy.deepcopy(before)
    after["state"].update(revision=5, observed_at=1020.0)
    if kind == "retire" and phase in {"prepared", "claimed"}:
        after["approvals"][-1]["phase"] = "cancelled"
    operation = ("retire-native-resume-history" if kind == "retire" else
                 "reconcile-missing-native-resume-intent")
    return dict(version=1, operation=operation, review=review, before=before, after=after)


def inspect(value, kind="retire", **changes):
    arguments = dict(kind=kind, identity=_IDENTITY, profile=_PROFILE,
                     expected_review=_encoded(value["review"]))
    arguments.update(changes)
    return inspect_resume_archive(_encoded(value), **arguments)


@pytest.mark.parametrize("kind,schema,phase", [
    *(('retire', 2, phase) for phase in ("prepared", "claimed", "cancelled", "complete", "failed")),
    ("reconcile", 1, "cancelled"), ("reconcile", 2, "cancelled"),
    ("reconcile", 2, "complete"), ("reconcile", 2, "failed"),
])
@pytest.mark.parametrize("mode", [m.value for m in RecoveryMode if m is not RecoveryMode.ACTIVE])
def test_exact_plan_is_immutable_redacted_and_performs_no_io(
    monkeypatch, kind, schema, phase, mode,
):
    import builtins
    import socket
    import sqlite3

    def forbidden(*args, **kwargs):
        pytest.fail("Archive inspection must perform no filesystem, ledger or network I/O")

    value = document(kind, schema=schema, phase=phase, mode=mode)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = inspect(value, kind)
    assert isinstance(result, BrowserResumeArchivePlan)
    assert not isinstance(result, BrowserResumeRetirementEvidence)
    assert (result.prior_revision, result.proposed_revision, result.mode) == (4, 5, mode)
    assert result.after == _encoded(value["after"])
    assert result.cancelled == int(kind == "retire" and phase in {"prepared", "claimed"})
    assert result.archived == 0
    assert all(secret not in repr(result) for secret in (
        _IDENTITY, str(_PROFILE), value["review"]["fingerprint"], "4" * 64, "5" * 64))
    with pytest.raises(FrozenInstanceError):
        result.after = b"{}"


@pytest.mark.parametrize("path,bad", [
    (("version",), True), (("operation",), "activate"),
    (("review", "revision"), True), (("review", "revision"), 0),
    (("review", "revision"), 2**53 - 2), (("review", "approvals"), True),
    (("review", "pending"), True), (("review", "pending"), 2),
    (("review", "expires_at"), 1129.0), (("review", "mode"), "active"),
    (("before", "identity"), "0" * 64), (("before", "profile"), "/other/profile"),
    (("before", "state", "revision"), True), (("before", "state", "revision"), 0),
    (("before", "state", "mode"), "active"), (("before", "state", "failures"), True),
    (("before", "state", "failures"), 33), (("before", "state", "next_at"), 1300.1),
    (("before", "state", "observed_at"), 1011.0),
    (("before", "approvals", 0, "generation"), True),
    (("before", "approvals", 0, "generation"), 0),
    (("before", "approvals", 0, "revision"), 3),
    (("before", "approvals", 0, "identity"), "0" * 64),
    (("before", "approvals", 0, "digest"), "bad"),
    (("before", "approvals", 0, "intent"), "3" * 64 + "\n"),
    (("before", "approvals", 0, "phase"), "unexpected"),
    (("before", "approvals", 0, "mode"), "tls_error"),
    (("before", "approvals", 0, "device"), "invalid/device"),
    (("before", "approvals", 0, "credential_hash"), "PRIVATE credential"),
    (("before", "approvals", 0, "trust_hash"), False),
    (("before", "approvals", 0, "created"), True),
    (("before", "approvals", 0, "expires"), 1111.0),
    (("after", "state", "mode"), "active"), (("after", "state", "revision"), 6),
    (("after", "state", "observed_at"), 1009.0),
    (("after", "state", "observed_at"), 1130.0),
    (("after", "approvals", 0, "phase"), "complete"),
])
def test_invalid_plan_rejected_even_with_matching_before_fingerprint_and_review(path, bad):
    value = document()
    selected = value
    for part in path[:-1]:
        selected = selected[part]
    selected[path[-1]] = bad
    value["review"]["fingerprint"] = _fingerprint(value["before"])
    with pytest.raises(BrowserResumeArchiveError) as caught:
        inspect(value)
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("change", ["empty-v2", "extra-snapshot", "extra-state", "extra-row",
                                    "duplicate-row", "unordered", "too-many", "two-pending"])
def test_exact_snapshot_and_history_constraints(change):
    value = document()
    rows = value["before"]["approvals"]
    if change == "empty-v2":
        rows.clear()
    elif change == "extra-snapshot":
        value["before"]["credential"] = "PRIVATE"
    elif change == "extra-state":
        value["before"]["state"]["session"] = "PRIVATE"
    elif change == "extra-row":
        rows[0]["ticket"] = "PRIVATE"
    else:
        other = dict(rows[0])
        if change != "duplicate-row":
            other.update(digest="1" * 64, phase="cancelled")
        if change == "two-pending":
            other.update(digest="7" * 64, phase="prepared")
        rows.append(other)
        if change == "too-many":
            rows[:] = [dict(other, digest=f"{i:064x}") for i in range(129)]
    value["review"]["fingerprint"] = _fingerprint(value["before"])
    with pytest.raises(BrowserResumeArchiveError):
        inspect(value)


@pytest.mark.parametrize("change", ["matching-intent", "pending", "schema-bool", "schema-3",
                                    "schema1-rows", "empty-schema2", "wrong-intent"])
def test_reconciliation_never_manufactures_absence(change):
    value = document("reconcile", phase="cancelled")
    before = value["before"]
    if change == "matching-intent":
        before["approvals"][0]["intent"] = value["review"]["intent"]
    elif change == "pending":
        before["approvals"][0]["phase"] = "prepared"
    elif change.startswith("schema"):
        before["schema"] = {"schema-bool": True, "schema-3": 3, "schema1-rows": 1}[change]
    elif change == "empty-schema2":
        before["approvals"].clear()
    else:
        before["intent"] = "9" * 64
    value["review"]["fingerprint"] = _fingerprint(before)
    with pytest.raises(BrowserResumeArchiveError):
        inspect(value, "reconcile")


@pytest.mark.parametrize("change", ["noncanonical", "duplicate", "extra", "truncated", "oversize",
                                    "nan", "not-bytes", "wrong-review", "wrong-kind", "relative"])
def test_bounded_canonical_selected_document(change):
    value = document()
    raw = _encoded(value)
    arguments = dict(kind="retire", identity=_IDENTITY, profile=_PROFILE,
                     expected_review=_encoded(value["review"]))
    if change == "noncanonical":
        raw = json.dumps(value).encode()
    elif change == "duplicate":
        raw = b'{"version":1,' + raw[1:]
    elif change == "extra":
        raw = _encoded({**value, "committed": True})
    elif change == "truncated":
        raw = raw[:15]
    elif change == "oversize":
        raw = b" " * (256 * 1024 + 1)
    elif change == "nan":
        raw = raw.replace(b'"observed_at":1000.0', b'"observed_at":NaN')
    elif change == "not-bytes":
        raw = raw.decode()
    elif change == "wrong-review":
        arguments["expected_review"] = b"{}\n"
    elif change == "wrong-kind":
        arguments["kind"] = "reconcile"
    else:
        arguments["profile"] = Path("relative")
    with pytest.raises(BrowserResumeArchiveError):
        inspect_resume_archive(raw, **arguments)


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux fixture")
@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("stage", ["rolled-back", "committed", "later-pause"])
def test_real_archive_plan_does_not_substitute_for_native_commit_or_current_state(
    lab, tmp_path, monkeypatch, kind, stage,
):
    archive = tmp_path / "native-history.json"
    if kind == "retire":
        prepare(lab)
        core = BrowserResumeMaintenance(lab.root, clock=lambda: lab.clock[0])
        review = core.review()
        error = BrowserResumeMaintenanceError
        apply = core.retire
        confirm_arguments = {"archive": archive}
        ledger = core._recovery
    else:
        core = BrowserResumeReconciliation(lab.root, clock=lambda: lab.clock[0])
        review = core.review(browser_intent=INTENT)
        error = BrowserResumeReconciliationError
        apply = core.reconcile
        confirm_arguments = {"archive": archive, "browser_intent": INTENT}
        ledger = core._recovery
    before = snapshot(lab)
    with monkeypatch.context() as patch:
        if stage == "rolled-back":
            def fail(*args):
                raise OSError("PRIVATE lost commit")
            patch.setattr(ledger, "_save", fail)
            with pytest.raises(error):
                apply(review, archive=archive)
        else:
            apply(review, archive=archive)
    if stage == "rolled-back":
        assert snapshot(lab) == before
    if stage == "later-pause":
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    selected = dict(kind=kind, identity=lab.configuration.identity, profile=lab.root,
                    expected_review=_encoded(asdict(review)))
    baseline = snapshot(lab), archive.read_bytes()
    plan = inspect_resume_archive(archive.read_bytes(), **selected)
    assert plan.proposed_revision == review.revision + 1
    assert not isinstance(plan, BrowserResumeRetirementEvidence)
    if stage == "committed":
        assert core.confirm(review, **confirm_arguments).revision == plan.proposed_revision
    else:
        with pytest.raises(error):
            core.confirm(review, **confirm_arguments)
    with pytest.raises(error):
        if kind == "retire":
            core.confirm(plan, archive=archive)
        else:
            core.confirm(plan, archive=archive, browser_intent=INTENT)
    assert (snapshot(lab), archive.read_bytes()) == baseline


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux fixture")
@pytest.mark.parametrize("kind", ["retire", "reconcile"])
def test_strict_confirmation_rechecks_archive_after_current_ledger(
    lab, tmp_path, monkeypatch, kind,
):
    archive = tmp_path / "native-history.json"
    if kind == "retire":
        prepare(lab)
        core = BrowserResumeMaintenance(lab.root, clock=lambda: lab.clock[0])
        review = core.review()
        core.retire(review, archive=archive)
        error, arguments = BrowserResumeMaintenanceError, {"archive": archive}
    else:
        core = BrowserResumeReconciliation(lab.root, clock=lambda: lab.clock[0])
        review = core.review(browser_intent=INTENT)
        core.reconcile(review, archive=archive)
        error = BrowserResumeReconciliationError
        arguments = {"archive": archive, "browser_intent": INTENT}
    before = snapshot(lab)
    raw = archive.read_bytes()
    original = core._snapshot

    def changed(*args):
        result = original(*args)
        archive.write_bytes(raw + b" ")
        return result

    monkeypatch.setattr(core, "_snapshot", changed)
    with pytest.raises(error):
        core.confirm(review, **arguments)
    assert archive.read_bytes() == raw + b" "  # Retained, not rewritten or repaired.
    assert snapshot(lab) == before
