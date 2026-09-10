"""Retained committed history cannot grant current permission or revive old consent."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import FrozenInstanceError

import pytest

from sds200 import browser_device_continuation_history as history
from sds200 import browser_device_continuation_intent as intent
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_guard_release as release
from sds200.browser_device_bundle import NATIVE_HOST, _json
from sds200.browser_device_handoff import BrowserHandoffError
from sds200.browser_device_launch import run_browser_recovery
from sds200.browser_device_profile import BrowserProfileError, inspect_browser_profile
from sds200.browser_device_profile_access import browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_registration import MAINTENANCE_MARKER
from sds200.browser_device_resume_maintenance import BrowserResumeRetirementEvidence
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_worker import BrowserWorkerSelection
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
@pytest.mark.parametrize("committed", [False, True])
def test_internal_native_activation_uses_owned_history_before_dml_and_exact_readback(
        lab, chain, tmp_path, committed):
    from sds200 import browser_device_continuation_native as activation
    from sds200.browser_device_profile import _write
    from tests.test_browser_device_continuation_native import inode, transaction

    h, r, i = chain
    selected = dict(release_id=r.release_id, intent_id=i.intent_id)
    profile = lab.args["profile"]
    path = profile / "recovery.sqlite"
    original = path.read_bytes()
    with ownership._stopped_history_ownership(h) as owner:
        retained = history._inspect_owned_history(h, owner, **selected)
        parameters = dict(history=retained, profile=profile, ledger_binding=inode(path))
        # Only this fixture supplies consent and owns/syncs a fictional manifest.
        # No installed writer or native role exposes this transaction core.
        now = time.time()
        raw = activation._prepare_activation_manifest(**parameters, epoch="a" * 64,
            consent_sha256="b" * 64, reviewed_at=now, approved_at=now)
        descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            _write(descriptor, "activation.json", raw)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        arguments = dict(**parameters, manifest=raw,
                         manifest_binding=inode(tmp_path / "activation.json"))
        with transaction(path) as db:
            assert history._inspect_owned_history(h, owner, **selected) == retained
            with (closing(sqlite3.connect(path, timeout=0)) as rival,
                  pytest.raises(sqlite3.OperationalError)):
                rival.execute("BEGIN IMMEDIATE")
            activation._stage_paused_activation(db, **arguments, now=time.time())
            with pytest.raises(history.BrowserContinuationHistoryError):
                history._inspect_owned_history(h, owner, **selected)
            # Never ignore our rollback journal to force a second historical read.
            if committed:
                db.commit()
            else:
                db.rollback()
        assert history._inspect_owned_history(h, owner, **selected) == retained
        with transaction(path, readonly=True) as db:
            if committed:
                result = activation._inspect_paused_activation(db, **arguments)
                assert (result.revision, result.mode) == (r.revision + 1, RecoveryMode.PAUSED)
            else:
                with pytest.raises(activation.BrowserContinuationNativeError):
                    activation._inspect_paused_activation(db, **arguments)
        owner.binding(h)
    if committed:
        with pytest.raises(BrowserHandoffError):
            h.confirm(restored=True)
        with pytest.raises(release.BrowserGuardReleaseError):
            release.BrowserPausedGuardRelease(h).confirm(release_id=r.release_id)
        with pytest.raises(intent.BrowserContinuationIntentError):
            intent.BrowserContinuationIntent(h, release_id=r.release_id).confirm(
                intent_id=i.intent_id)
    else:
        assert path.read_bytes() == original
        h.confirm(restored=True)
    assert (tmp_path / "activation.json").read_bytes() == raw
    blocked(lab)  # Neither the internal anchor nor historical evidence enables startup.


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


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
def test_history_never_parses_current_ledger_or_establishes_current_health(
        lab, chain, monkeypatch):
    h, r, i = chain
    original = inspect(chain)
    path = lab.args["profile"] / "recovery.sqlite"
    inode = path.stat().st_ino
    connect = sqlite3.connect

    def journal_only(database, *args, **kwargs):
        assert "recovery.sqlite" not in str(database), "Historical reader opened current ledger"
        return connect(database, *args, **kwargs)

    # Same-inode fixture corruption/unsupported schema is NOT a successor schema
    # implementation. Historical success says nothing about native ledger health.
    with closing(connect(path)) as db:
        db.execute("PRAGMA user_version=999")
    for corrupt in (False, True):
        if corrupt:
            path.write_bytes(b"retained unusable native state")
        assert path.stat().st_ino == inode
        before = state(lab, h)
        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, "connect", journal_only)
            assert inspect(chain) == original
        with pytest.raises(BrowserProfileError):
            inspect_browser_profile(lab.args["profile"])
        with pytest.raises(BrowserHandoffError):
            h.confirm(restored=True)
        with pytest.raises(release.BrowserGuardReleaseError):
            release.BrowserPausedGuardRelease(h).confirm(release_id=r.release_id)
        with pytest.raises(intent.BrowserContinuationIntentError):
            intent.BrowserContinuationIntent(h, release_id=r.release_id).confirm(
                intent_id=i.intent_id)
        blocked(lab)
        assert state(lab, h) == before


@pytest.mark.parametrize("damage", ["mode", "hardlink", "missing", "symlink",
                                    "-wal", "-shm", "-journal"])
def test_history_keeps_private_ledger_inode_and_stopped_file_checks(lab, chain, damage):
    path = lab.args["profile"] / "recovery.sqlite"
    if damage == "mode":
        path.chmod(0o644)
    elif damage == "hardlink":
        os.link(path, path.with_name("extra-ledger-link"))
    elif damage in {"missing", "symlink"}:
        retained = path.with_name("retained-ledger")
        path.rename(retained)
        if damage == "symlink":
            path.symlink_to(retained)
    else:
        path.with_name(path.name + damage).symlink_to("fictional-sidecar")
    before = state(lab, chain[0])
    with pytest.raises(history.BrowserContinuationHistoryError):
        inspect(chain)
    assert state(lab, chain[0]) == before
    blocked(lab)


@pytest.mark.parametrize("target", ["review", "archive", "guard", "operation", "ack", "restored",
    "restoration-started", "ready", "launch", "supervisor", "original-host", "registered-host",
    "bundle", "recovery-bundle", "credential", "credential-valid", "release-inode", "intent-inode",
    "ledger-inode"])
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
        "credential-valid": s._profile / "device.secret",
        "release-inode": s._root / release.RELEASE_JOURNAL,
        "intent-inode": s._root / intent.INTENT_JOURNAL,
        "ledger-inode": s._profile / "recovery.sqlite"}
    path = paths[target]
    raw = path.read_bytes()
    if target == "credential-valid":
        path.write_bytes(b"sdsctl-browser-v1." + b"e" * 64)
        # Correct syntax is insufficient: old history binds the original secret.
        history._canonical_bundle_files(**s._registration)
    elif target.endswith("-inode"):
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


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
def test_owned_reconstruction_reuses_lock_but_never_reuses_expired_scope(lab, chain):
    h, r, i = chain
    original, before = inspect(chain), state(lab, h)
    with ownership._stopped_history_ownership(h) as owner:
        # This is the future transaction caller's shape: reconstruct inside the
        # existing owner, without attempting a second exclusive launch lock.
        assert history._inspect_owned_history(h, owner, release_id=r.release_id,
                                              intent_id=i.intent_id) == original
        with pytest.raises(history.BrowserContinuationHistoryError):
            inspect(chain)  # Public entrypoint still insists on its own lock.
    for expired in (owner, object()):
        with pytest.raises(history.BrowserContinuationHistoryError):
            history._inspect_owned_history(h, expired, release_id=r.release_id,
                                           intent_id=i.intent_id)
    assert state(lab, h) == before
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
@pytest.mark.parametrize("later", [False, True])
def test_live_owner_reads_identical_history_without_normal_worker_authority(
        lab, chain, monkeypatch, later):
    from sds200 import browser_device_native as native
    from sds200 import browser_device_worker as worker

    h, r, i = chain
    original = inspect(chain)
    if later:
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    before = state(lab, h)
    selected = BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    root = lab.args["directory"]
    # Completed historical chain is real supervised fixture evidence; ONLY the
    # later live browser ancestor is simulated. This is not real-browser acceptance.
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network I/O"))
    with _launch_lock(root, create=False):
        (root / "SingletonLock").symlink_to("fictional-later-browser")
        try:
            assert history._inspect_worker_continuation_history(h,
                configuration=lab.configuration, selection=selected,
                release_id=r.release_id, intent_id=i.intent_id) == original
            with pytest.raises(history.BrowserContinuationHistoryError):
                inspect(chain)
            with pytest.raises(ValueError):
                worker.normal_worker_paused_only(lab.configuration, selected)
        finally:
            (root / "SingletonLock").unlink()  # Fixture-only marker, not profile repair.
    with pytest.raises(history.BrowserContinuationHistoryError):
        history._inspect_worker_continuation_history(h, configuration=lab.configuration,
            selection=selected, release_id=r.release_id, intent_id=i.intent_id)
    assert state(lab, h) == before
    blocked(lab)


@pytest.mark.parametrize("change", ["ancestor", "lock-inode", "credential", "trust",
    "ledger-mode", "ledger-sidecar", "release-sidecar", "intent-sidecar"])
def test_live_history_final_readback_refuses_changed_owner_or_inputs(
        lab, chain, monkeypatch, change):
    h, r, i = chain
    root = lab.args["directory"]
    selected = BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    original = history._Reader.recheck
    retained = []

    def changed(reader):
        if change == "ancestor":
            monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root.parent)
        elif change == "lock-inode":
            path = root / ".sdsctl-device-launch.lock"
            path.rename(path.with_name("retained-launch-lock"))
            private(path, b"")
        elif change == "credential":
            private(lab.args["profile"] / "device.secret", "sdsctl-browser-v1." + "e" * 64)
        elif change == "trust":
            path = lab.args["profile"] / "ca.pem"
            path.write_bytes(path.read_bytes() + b"\n")
        elif change == "ledger-mode":
            (lab.args["profile"] / "recovery.sqlite").chmod(0o644)
        else:
            path = {"ledger-sidecar": lab.args["profile"] / "recovery.sqlite",
                    "release-sidecar": root / release.RELEASE_JOURNAL,
                    "intent-sidecar": root / intent.INTENT_JOURNAL}[change]
            path.with_name(path.name + "-journal").symlink_to("fictional-sidecar")
        retained.append(state(lab, h))
        original(reader)

    monkeypatch.setattr(history._Reader, "recheck", changed)
    with _launch_lock(root, create=False), pytest.raises(history.BrowserContinuationHistoryError):
        history._inspect_worker_continuation_history(h, configuration=lab.configuration,
            selection=selected, release_id=r.release_id, intent_id=i.intent_id)
    assert state(lab, h) == retained[0]
    blocked(lab)
