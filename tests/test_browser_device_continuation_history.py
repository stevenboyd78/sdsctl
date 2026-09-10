"""Retained committed history cannot grant current permission or revive old consent."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import FrozenInstanceError

import pytest

from sds200 import browser_device_continuation_history as history
from sds200 import browser_device_continuation_intent as intent
from sds200 import browser_device_guard_release as release
from sds200.browser_device_bundle import NATIVE_HOST, _json
from sds200.browser_device_handoff import BrowserHandoffError
from sds200.browser_device_launch import run_browser_recovery
from sds200.browser_device_profile_access import browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_registration import MAINTENANCE_MARKER
from sds200.browser_device_resume_maintenance import BrowserResumeRetirementEvidence
from sds200.browser_device_startup import _launch_lock
from tests import test_browser_device_launch as launch_fixture
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_guard_release import attempt, blocked, state
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_retirement_bundle import committed
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux supervised historical chain")


@pytest.fixture
def chain(lab, tmp_path, monkeypatch, request):
    kind = getattr(request, "param", "reconcile")
    monkeypatch.setattr(launch_fixture, "committed", lambda lab: committed(lab, kind))
    _, _, _, setup, _ = launch_fixture.staged.__wrapped__(lab, tmp_path, monkeypatch)
    handoff, browser, bwrap = setup()
    run_browser_recovery(handoff, browser=browser, bwrap=bwrap)
    handoff.restore()
    released = attempt(handoff)
    recorded = intent.BrowserContinuationIntent(handoff, release_id=released.release_id).apply(
        confirmation=lambda r: r.confirmation)
    return handoff, released, recorded


def inspect(chain):
    h, r, i = chain
    return history.inspect_continuation_history(h, release_id=r.release_id, intent_id=i.intent_id)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
@pytest.mark.parametrize("later", [False, True])
def test_history_survives_later_pause_but_current_confirmation_does_not(
        lab, chain, later, monkeypatch):
    from sds200 import browser_device_native as native
    from sds200 import browser_device_worker as worker

    h, r, i = chain
    original = inspect(chain)
    if later:
        # Fixture-only simulation of a future independently authorized transition;
        # ordinary worker requests remain blocked throughout this test.
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
        with pytest.raises(BrowserHandoffError):
            h.confirm(restored=True)
        with pytest.raises(release.BrowserGuardReleaseError):
            release.BrowserPausedGuardRelease(h).confirm(release_id=r.release_id)
        with pytest.raises(intent.BrowserContinuationIntentError):
            intent.BrowserContinuationIntent(h, release_id=r.release_id).confirm(
                intent_id=i.intent_id)
    before = state(lab, h)
    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network I/O"))
    # The historical reader must not turn an old confirmation into a current one.
    monkeypatch.setattr(release, "_confirmed", lambda *a, **k: pytest.fail("Live release fallback"))
    monkeypatch.setattr(intent, "_confirmed", lambda *a, **k: pytest.fail("Live intent fallback"))
    result = inspect(chain)
    assert result == original
    assert result.native_revision == r.revision and result.mode is RecoveryMode.PAUSED
    assert not isinstance(result, (BrowserResumeRetirementEvidence,
                                  release.BrowserGuardReleaseEvidence,
                                  intent.BrowserContinuationIntentEvidence))
    with pytest.raises(FrozenInstanceError):
        result.native_revision += 1
    for secret in (CREDENTIAL, result.identity, result.release_id, result.intent_id,
                   result.fingerprint, result.origin, str(lab.args["directory"])):
        assert secret not in repr(result)
    blocked(lab)
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: lab.args["directory"])
    with _launch_lock(lab.args["directory"], create=False), pytest.raises(ValueError):
        worker.normal_worker_paused_only(lab.configuration, selected)
    assert state(lab, h) == before


@pytest.mark.parametrize("target", ["review", "archive", "guard", "operation", "ack", "restored",
    "restoration-started", "ready", "launch", "supervisor", "original-host", "registered-host",
    "bundle", "recovery-bundle", "credential", "release-inode", "intent-inode", "ledger-inode"])
def test_changed_or_replaced_retained_evidence_is_not_history(lab, chain, target):
    h, _, _ = chain
    s = h._session
    paths = {"review": s._archives / h._operation / "review.json",
        "archive": s._archives / h._operation / "native-history.json",
        "guard": s._root / MAINTENANCE_MARKER, "operation": h._root / "operation.json",
        "ack": h._root / "browser-acknowledgement.json", "restored": h._root / "restored.json",
        "restoration-started": h._root / "restoration-started.json",
        "ready": h._root / "ready.json", "launch": h._root / "browser-launch-ready.json",
        "supervisor": h._root / "supervisor.json",
        "original-host": h._root / "native-host.before.json",
        "registered-host": s._root / "NativeMessagingHosts" / (NATIVE_HOST + ".json"),
        "bundle": s._registration["bundle"] / "extension" / "worker.mjs",
        "recovery-bundle": h._recovery / "extension" / "worker.mjs",
        "credential": s._profile / "device.secret",
        "release-inode": s._root / release.RELEASE_JOURNAL,
        "intent-inode": s._root / intent.INTENT_JOURNAL,
        "ledger-inode": s._profile / "recovery.sqlite"}
    path = paths[target]
    raw = path.read_bytes()
    if target.endswith("-inode"):
        path.rename(path.with_name(path.name + ".retained"))
        private(path, raw)
    else:
        path.write_bytes(raw + b" ")
    before = state(lab, h)
    with pytest.raises(history.BrowserContinuationHistoryError):
        inspect(chain)
    assert state(lab, h) == before
    blocked(lab)


@pytest.mark.parametrize("journal", ["release", "intent"])
def test_archive_and_prepared_or_malformed_journal_cannot_anchor_history(lab, chain, journal):
    h, _, _ = chain
    name = release.RELEASE_JOURNAL if journal == "release" else intent.INTENT_JOURNAL
    path = h._session._root / name
    read = release._read if journal == "release" else intent._read
    raw = read(path)
    original = json.loads(raw)
    variants = [b"{}", raw + b" ", b'{"version":1,"version":1}', _json({**original, "extra": 1})]
    for key, value in (("version", True), ("operation", "wrong"), ("release_id", "f" * 64),
        ("reviewed_at", True), ("approved_at", original["expires_at"]), ("expires_at", 1),
        ("consent_sha256", "bad"), ("journal_binding", [True, 2]), ("evidence", {})):
        variants.append(_json({**original, key: value}))
    if journal == "intent":
        variants.extend(_json({**original, key: value}) for key, value in (
            ("intent_id", "e" * 64), ("purpose", "automatic-sign-in")))
    variants.append(raw)  # Last case: exact canonical body but only prepared.
    for index, body in enumerate(variants):
        with closing(sqlite3.connect(path)) as db:
            db.execute(f"UPDATE {journal} SET body=?, phase=?", (
                body, "prepared" if index == len(variants) - 1 else "complete"))
            db.commit()
        before = state(lab, h)
        with pytest.raises(history.BrowserContinuationHistoryError):
            inspect(chain)
        assert state(lab, h) == before
    # Missing committed journal must not be replaced with a valid archive plan.
    path.rename(path.with_name(path.name + ".retained"))
    before = state(lab, h)
    with pytest.raises(history.BrowserContinuationHistoryError):
        inspect(chain)
    assert state(lab, h) == before


def test_sidecars_unsafe_names_and_wrong_selections_fail_closed(lab, chain):
    h, r, i = chain
    for name in (release.RELEASE_JOURNAL, intent.INTENT_JOURNAL):
        for suffix in ("-wal", "-shm", "-journal"):
            path = h._session._root / (name + suffix)
            path.symlink_to("fictional-missing")
            before = state(lab, h)
            with pytest.raises(history.BrowserContinuationHistoryError):
                inspect(chain)
            assert state(lab, h) == before
            path.unlink()  # Fixture-only sidecar; no real profile is involved.
    for release_id, intent_id in (("f" * 64, i.intent_id), (r.release_id, "f" * 64),
                                  (True, i.intent_id), (r.release_id, "")):
        with pytest.raises(history.BrowserContinuationHistoryError):
            history.inspect_continuation_history(h, release_id=release_id, intent_id=intent_id)
    before = state(lab, h)
    for lock in (_launch_lock(h._session._root, create=False),
                 browser_profile_access(h._session._profile, exclusive=True),
                 browser_profile_access(h._session._archives, exclusive=True)):
        with lock, pytest.raises(history.BrowserContinuationHistoryError):
            inspect(chain)
    (h._session._root / "SingletonLock").symlink_to("fictional-pid")
    with pytest.raises(history.BrowserContinuationHistoryError):
        inspect(chain)
    (h._session._root / "SingletonLock").unlink()
    assert state(lab, h) == before


def test_final_readback_refuses_changed_archive_and_sanitizes_errors(lab, chain, monkeypatch):
    h, _, _ = chain
    path = h._session._archives / h._operation / "native-history.json"
    original = history._Reader.recheck
    retained = []

    def changed(reader):
        path.write_bytes(path.read_bytes() + b" ")
        retained.append(state(lab, h))
        original(reader)

    monkeypatch.setattr(history._Reader, "recheck", changed)
    with pytest.raises(history.BrowserContinuationHistoryError) as error:
        inspect(chain)
    assert state(lab, h) == retained[0]
    assert CREDENTIAL not in str(error.value) and str(path) not in str(error.value)
