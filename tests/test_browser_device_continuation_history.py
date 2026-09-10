"""Retained committed history cannot grant current permission or revive old consent."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

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


def _browser_core_checks_native_fixture(expected, observed, result=None):
    """Actual owned native result, but modeled browser I/O: not browser acceptance."""
    from sds200.browser_device_worker import worker_graph

    node = shutil.which("node")
    assert node is not None, "The native/browser contract requires Node; do not skip it."

    def observation(value, generation):
        return dict(identity=value.identity, epoch=value.epoch, mode=value.mode.value,
                    binding=dict(fingerprint=value.state_fingerprint,
                                 revision=value.native_revision, generation=generation))

    payload = dict(settings=dict(identity=expected.identity, epoch=expected.epoch,
                                 origin=expected.origin, build=worker_graph()[0]),
                   before=observation(expected, 7),
                   after=observation(observed.state, observed.generation),
                   session=(None if result is None else
                            dict(token=result.session.token, expires_in=result.session.expires_in)))
    script = """
      import assert from 'node:assert/strict';
      import {readFileSync} from 'node:fs';
      import {pausedContinuationRecord,createInitialInstallation,classifyContinuationStartup} from
        './src/sds200/browser_assets/browser_device_continuation_state.mjs';
      const {settings,before,after,session}=JSON.parse(readFileSync(0,'utf8'));
      const paused=pausedContinuationRecord(settings);
      const pendingOwner=createInitialInstallation(settings,paused,before,
        {wall:()=>1000000,monotonic:()=>500000});
      const pending=pendingOwner.pendingRecord('e'.repeat(64));
      assert.deepEqual(classifyContinuationStartup(settings,pending,after),
        {mode:'administrator_required',sessionReady:false});
      assert.throws(()=>createInitialInstallation(settings,pending,before,
        {wall:()=>1000000,monotonic:()=>500000}));
      if(session===null) {
        // Actual native ACTIVE after uncertain issuance is never adopted.
        assert.equal(after.mode,'active');
        console.log('Native/browser modeled contract passed');
        process.exit(0);
      }
      const make=()=>{
        const a=createInitialInstallation(settings,pausedContinuationRecord(settings),before,
          {wall:()=>1000000,monotonic:()=>500000});
        const pending=a.pendingRecord('e'.repeat(64));
        a.pendingSaved(structuredClone(pending));
        return a;
      };
      const a=make();
      const details=a.sessionReturned({binding:after.binding,session},after);
      const {url,...values}=details;
      const cookie={...values,domain:new URL(url).hostname,hostOnly:true,session:false};
      a.cookieInstalled(cookie);
      const probe={tabId:1,documentId:'fixture-document',ticket:'f'.repeat(64)};
      a.probeStarted(probe);
      const accepted=a.protectedPageVerified({...probe,url:settings.origin+'/device-display',
        displayOnly:true,deviceEnrolled:true,remainingSeconds:session.expires_in},cookie,after);
      assert.deepEqual(a.acceptedSaved(structuredClone(accepted),after,cookie),
        {mode:'accepted',sessionReady:true});
      assert.deepEqual(classifyContinuationStartup(settings,accepted,after),
        {mode:'verification_required',sessionReady:false});
      // Neither a missing completion nor an extra native mutation may be adopted.
      for(const delta of [-1,1]) {
        const changed={...after,binding:{...after.binding,revision:after.binding.revision+delta}};
        assert.throws(()=>make().sessionReturned({binding:changed.binding,session},changed));
      }
      console.log('Native/browser modeled contract passed');
    """
    checked = subprocess.run([node, "--input-type=module", "-e", script],
        cwd=Path(__file__).resolve().parents[1], input=json.dumps(payload),
        text=True, capture_output=True, timeout=15, check=False)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.strip() == "Native/browser modeled contract passed"


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
def test_owned_activation_workflow_with_complete_retained_chain(lab, chain):
    from sds200.browser_device_continuation_activation import (
        BrowserPausedActivation,
        BrowserPausedActivationError,
    )

    h, r, i = chain
    retained = inspect(chain)
    core = BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id)
    seen = []
    result = core.apply(confirmation=lambda review: seen.append(review) or review.confirmation)
    assert result.mode is RecoveryMode.PAUSED and result.native_revision == r.revision + 1
    assert result.epoch == seen[0].epoch
    assert inspect(chain) == retained
    before = state(lab, h)
    assert core.confirm(epoch=result.epoch) == result
    assert state(lab, h) == before
    with pytest.raises(BrowserPausedActivationError):
        BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
            confirmation=lambda _: pytest.fail("Replayed activation"))
    with pytest.raises(BrowserHandoffError):
        h.confirm(restored=True)
    with pytest.raises(release.BrowserGuardReleaseError):
        release.BrowserPausedGuardRelease(h).confirm(release_id=r.release_id)
    with pytest.raises(intent.BrowserContinuationIntentError):
        intent.BrowserContinuationIntent(h, release_id=r.release_id).confirm(intent_id=i.intent_id)
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
def test_epoch_core_preserves_complete_history_without_enabling_runtime(lab, chain):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_continuation_epoch as epoch

    h, r, i = chain
    retained = inspect(chain)
    core = activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id)
    activated = core.apply(confirmation=lambda review: review.confirmation)
    assert activated is not None
    inputs = epoch._ApprovalInputs("a" * 64, "b" * 64, lab.configuration.device_id,
                                   1, "c" * 64, "d" * 64)
    # Fictional consent/server facts only. This exercises native transactions
    # against a complete retained chain, NOT an owned online permission adapter.
    path = h._session._root / activation.ACTIVATION_MANIFEST
    raw, binding = path.read_bytes(), activation._binding(path)
    selected_paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    for operation in ("prepare", "claim", "complete", "pause"):
        with ownership._stopped_history_ownership(h) as owner:
            with activation._ledger_transaction(lab.args["profile"],
                                                  readonly=False) as (db, ledger):
                selected, captured = history._capture_owned_history(h, owner,
                    release_id=r.release_id, intent_id=i.intent_id)
                assert selected == retained
                activation._manifest_check(path, raw, binding)
                selection = epoch._EpochSelection(raw, binding, selected,
                                                   lab.args["profile"], ledger.binding)
                before = epoch._read(db, selection, readonly=False).snapshot
                args = [] if operation == "pause" else [inputs]
                result = getattr(epoch, "_stage_" + operation)(db, selection, before, *args,
                                                               now=time.time())
                captured.recheck()
                ledger.check_identity()
                activation._manifest_check(path, raw, binding)
                assert epoch._read(db, selection, readonly=False).snapshot == result
                with pytest.raises(history.BrowserContinuationHistoryError):
                    history._inspect_owned_history(h, owner,
                        release_id=r.release_id, intent_id=i.intent_id)
                db.commit()
            assert history._inspect_owned_history(h, owner,
                release_id=r.release_id, intent_id=i.intent_id) == retained
        before_read = state(lab, h)
        current_state = current.inspect_stopped_continuation(h._session._root, **selected_paths)
        assert current_state.mode is result.mode and current_state.epoch == activated.epoch
        assert current_state.native_revision == result.revision
        assert current_state.state_fingerprint == result.fingerprint
        assert state(lab, h) == before_read
        blocked(lab)  # Even a verified active epoch is not a normal runtime role.
    assert result.mode is RecoveryMode.PAUSED
    assert inspect(chain) == retained and path.read_bytes() == raw
    with pytest.raises(activation.BrowserPausedActivationError):
        core.confirm(epoch=activated.epoch)
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
@pytest.mark.parametrize("failure", ["after-create", "after-native", "late-credential",
                                    "late-bundle", "late-journal", "late-ack"])
def test_owned_activation_interruptions_keep_evidence_and_never_replay(
        lab, chain, monkeypatch, failure):
    from sds200 import browser_device_continuation_activation as activation

    h, r, i = chain
    core = activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id)
    before = lab.ledger.path.read_bytes()
    epochs = []
    create, stage = activation._create_manifest, activation.native._stage_paused_activation

    def created(*args):
        create(*args)
        raise RuntimeError("PRIVATE after creation")

    def staged(*args, **kwargs):
        stage(*args, **kwargs)
        if failure == "late-credential":
            private(lab.args["profile"] / "device.secret", b"changed after native DML")
        elif failure == "late-bundle":
            path = h._session._registration["bundle"] / "extension" / "worker.mjs"
            path.write_bytes(path.read_bytes() + b"\n// changed after native DML\n")
        elif failure == "late-journal":
            path = h._session._root / intent.INTENT_JOURNAL
            path.with_name(path.name + "-journal").symlink_to("missing")
        else:
            raise RuntimeError("PRIVATE after native stage")

    def unconfirmed(*args):
        raise RuntimeError("PRIVATE lost final acknowledgement")

    with monkeypatch.context() as patch:
        if failure == "after-create":
            patch.setattr(activation, "_create_manifest", created)
        elif failure == "late-ack":
            patch.setattr(core, "_confirm_owned", unconfirmed)
        else:
            patch.setattr(activation.native, "_stage_paused_activation", staged)
        with pytest.raises(activation.BrowserPausedActivationError) as error:
            core.apply(confirmation=lambda review:
                       epochs.append(review.epoch) or review.confirmation)
        assert "PRIVATE" not in str(error.value)
    assert (lab.args["directory"] / activation.ACTIVATION_MANIFEST).exists()
    if failure == "late-ack":
        assert core.confirm(epoch=epochs[0]).native_revision == r.revision + 1
    else:
        assert lab.ledger.path.read_bytes() == before
        with pytest.raises(activation.BrowserPausedActivationError):
            core.confirm(epoch=epochs[0])
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
def test_captured_history_input_check_expires_with_owner(lab, chain):
    h, r, i = chain
    before = state(lab, h)
    with ownership._stopped_history_ownership(h) as owner:
        retained, inputs = history._capture_owned_history(h, owner,
            release_id=r.release_id, intent_id=i.intent_id)
        inputs.recheck()
        assert retained.native_revision == r.revision
    with pytest.raises(history.BrowserContinuationHistoryError):
        inputs.recheck()
    assert state(lab, h) == before


@pytest.mark.parametrize("change", ["archive", "runtime", "inventory"])
def test_captured_history_inputs_latch_failure_after_restored_change(
        lab, chain, monkeypatch, change):
    h, r, i = chain
    with ownership._stopped_history_ownership(h) as owner:
        _, inputs = history._capture_owned_history(h, owner,
            release_id=r.release_id, intent_id=i.intent_id)
        with monkeypatch.context() as patch:
            if change == "runtime":
                def changed_runtime(**kwargs):
                    raise RuntimeError("PRIVATE changed runtime")

                patch.setattr(history, "_canonical_bundle_files", changed_runtime)
            elif change == "archive":
                path = h._session._archives / h._operation / "native-history.json"
                raw = path.read_bytes()
                path.write_bytes(raw + b" ")
            else:
                path = h._session._root / "NativeMessagingHosts" / "unselected.json"
                private(path, b"unselected host")
            with pytest.raises(history.BrowserContinuationHistoryError):
                inputs.recheck()
        if change == "archive":
            path.write_bytes(raw)
        elif change == "inventory":
            path.unlink()
        # The old scope is still owned, but the failed input capture cannot revive.
        owner.binding(h)
        before = state(lab, h)
        with pytest.raises(history.BrowserContinuationHistoryError):
            inputs.recheck()
        # A separate full read still validates independently, without a bypass.
        assert history._inspect_owned_history(h, owner,
            release_id=r.release_id, intent_id=i.intent_id).native_revision == r.revision
        assert state(lab, h) == before


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


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
def test_fixed_live_current_read_selects_complete_chain_without_runtime_role(
        lab, chain, monkeypatch):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_native as native

    h, r, i = chain
    result = activation.BrowserPausedActivation(h,
        release_id=r.release_id, intent_id=i.intent_id).apply(confirmation=lambda r: r.confirmation)
    assert result is not None
    root = h._session._root
    selection = BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    # Full supervised historical chain above; the later browser ancestor alone
    # is simulated. This is not headed-browser or physical-display acceptance.
    monkeypatch.setattr(current, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network I/O"))
    expected = current.inspect_stopped_continuation(root, **paths)
    before = state(lab, h)
    with _launch_lock(root, create=False):
        (root / "SingletonLock").symlink_to("fictional-later-browser")
        try:
            with current._worker_current_scope(lab.configuration, selection) as reader:
                assert reader.inspect() == expected
                assert expected.epoch == result.epoch and expected.mode is RecoveryMode.PAUSED
                with pytest.raises(current.BrowserContinuationCurrentError):
                    current.inspect_stopped_continuation(root, **paths)
            with pytest.raises(current.BrowserContinuationCurrentError):
                reader.inspect()
        finally:
            (root / "SingletonLock").unlink()  # Fixture-owned marker only.
    assert state(lab, h) == before
    blocked(lab)


@pytest.mark.parametrize("change", ["archive", "release", "credential", "runtime"])
def test_current_read_rechecks_real_retained_chain_and_latches(lab, chain, monkeypatch, change):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_current as current

    h, r, i = chain
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda r: r.confirmation)
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    with pytest.raises(current.BrowserContinuationCurrentError), current._stopped_current_scope(
            h._session._root, **paths) as reader:
        if change == "runtime":
            def changed_runtime(**kwargs):
                raise RuntimeError("PRIVATE changed runtime")

            monkeypatch.setattr(history, "_canonical_bundle_files", changed_runtime)
        else:
            path = {"archive": h._session._archives / h._operation / "native-history.json",
                    "release": h._session._root / release.RELEASE_JOURNAL,
                    "credential": lab.args["profile"] / "device.secret"}[change]
            path.write_bytes(path.read_bytes() + b" ")
        before = state(lab, h)
        with pytest.raises(current.BrowserContinuationCurrentError) as error:
            reader.inspect()
        assert "PRIVATE" not in str(error.value) and CREDENTIAL not in str(error.value)
        assert state(lab, h) == before
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
@pytest.mark.parametrize("mode", ["claimed", "active"])
def test_owned_cancellation_preserves_complete_history_and_blocks_stale_grants(lab, chain, mode):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_cancel as cancel
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_continuation_epoch as epoch
    from tests.test_browser_device_continuation_current import native_step

    h, r, i = chain
    retained = inspect(chain)
    activated = activation.BrowserPausedActivation(h,
        release_id=r.release_id, intent_id=i.intent_id).apply(confirmation=lambda r: r.confirmation)
    assert activated is not None
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    candidate = SimpleNamespace(
        paths={"manifest": h._session._root / activation.ACTIVATION_MANIFEST}, history=retained)
    # Only the initial core seed supplies fictional consent/server facts. The
    # cancellation adapter itself owns and reconstructs the complete real chain.
    lab.clock[0] = time.time() + 2
    for stage in (("prepare", "claim", "complete") if mode == "active" else ("prepare", "claim")):
        native_step(lab, candidate, stage)
    expected = current.inspect_stopped_continuation(h._session._root, **paths)
    lab.clock[0] -= 100
    clocks = dict(clock=lambda: lab.clock[0], monotonic=time.monotonic)
    before = state(lab, h)
    before["profile"].pop("recovery.sqlite")
    corrector = cancel.BrowserStoppedCancellation(h._session._root, **paths, **clocks)
    corrected = corrector.correct_clock(expected)
    assert corrected.state.native_revision == expected.native_revision + 1
    assert corrected.state.mode is expected.mode
    assert corrector.confirm() == corrected
    pauser = cancel.BrowserStoppedCancellation(h._session._root, **paths, **clocks)
    paused = pauser.pause(corrected.state)
    assert paused.state.mode is RecoveryMode.PAUSED
    assert paused.state.native_revision == corrected.state.native_revision + 1
    assert pauser.confirm() == paused
    after = state(lab, h)
    after["profile"].pop("recovery.sqlite")
    assert after == before and inspect(chain) == retained
    with pytest.raises(cancel.BrowserContinuationCancellationError):
        corrector.confirm()  # A later pause is not the exact earlier after-state.
    with pytest.raises(epoch.BrowserContinuationEpochError):
        native_step(lab, candidate, "complete")  # Cancelled/completed old claim is not reusable.
    blocked(lab)


@pytest.mark.parametrize("failure", ["archive", "runtime", "commit-before", "commit-after",
                                    "copied-release", "copied-intent"])
def test_owned_cancellation_full_chain_interruptions_retain_state(
        lab, chain, monkeypatch, failure):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_cancel as cancel
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_continuation_epoch as epoch

    h, r, i = chain
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda r: r.confirmation)
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    expected = current.inspect_stopped_continuation(h._session._root, **paths)
    before = lab.ledger.path.read_bytes()
    pauser = cancel.BrowserStoppedCancellation(h._session._root, **paths)
    if failure.startswith("copied"):
        path = h._session._root / (release.RELEASE_JOURNAL if failure == "copied-release"
                                  else intent.INTENT_JOURNAL)
        raw = path.read_bytes()
        path.rename(path.with_name(path.name + ".retained"))
        private(path, raw)
    stage = epoch._stage_pause

    def staged(*args, **kwargs):
        result = stage(*args, **kwargs)
        if failure == "archive":
            path = h._session._archives / h._operation / "native-history.json"
            path.write_bytes(path.read_bytes() + b" ")
        elif failure == "runtime":
            def changed(**kwargs):
                raise RuntimeError("PRIVATE changed runtime")

            monkeypatch.setattr(history, "_canonical_bundle_files", changed)
        return result

    def commit(db):
        if failure == "commit-after":
            db.commit()
        raise sqlite3.OperationalError("PRIVATE uncertain reply")

    with monkeypatch.context() as patch:
        if failure.startswith("commit"):
            patch.setattr(cancel, "_commit", commit)
        else:
            patch.setattr(epoch, "_stage_pause", staged)
        with pytest.raises(cancel.BrowserContinuationCancellationError) as error:
            pauser.pause(expected)
        assert "PRIVATE" not in str(error.value)
    after = state(lab, h)
    if failure == "commit-after":
        assert pauser.confirm().state.native_revision == expected.native_revision + 1
    else:
        assert lab.ledger.path.read_bytes() == before
        with pytest.raises(cancel.BrowserContinuationCancellationError):
            pauser.confirm()
    assert state(lab, h) == after
    blocked(lab)


def test_live_owned_cancellation_uses_complete_chain_without_ordinary_dispatch(
        lab, chain, monkeypatch):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_cancel as cancel
    from sds200 import browser_device_continuation_current as current

    h, r, i = chain
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda r: r.confirmation)
    root = h._session._root
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    expected = current.inspect_stopped_continuation(root, **paths)
    # Only the later Chromium ancestor is simulated, not the retained chain or locks.
    monkeypatch.setattr(current, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    selection = BrowserWorkerSelection(paths["bundle"], paths["public_key"])
    with _launch_lock(root, create=False):
        (root / "SingletonLock").symlink_to("fictional-later-browser")
        try:
            attempt = cancel._BrowserWorkerCancellation(lab.configuration, selection)
            result = attempt.pause(expected)
            assert result.state.native_revision == expected.native_revision + 1
            assert result.state.mode is RecoveryMode.PAUSED and attempt.confirm() == result
        finally:
            (root / "SingletonLock").unlink()  # Fixture-only marker.
    assert current.inspect_stopped_continuation(root, **paths) == result.state
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
def test_live_owned_approval_retains_complete_history_and_pause_wins(lab, chain, monkeypatch):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_approval as approval
    from sds200 import browser_device_continuation_cancel as cancel
    from sds200 import browser_device_continuation_current as current

    h, r, i = chain
    retained = inspect(chain)
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda r: r.confirmation)
    root = h._session._root
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    expected = current.inspect_stopped_continuation(root, **paths)
    monkeypatch.setattr(current, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    selection = BrowserWorkerSelection(paths["bundle"], paths["public_key"])
    before = state(lab, h)
    before["profile"].pop("recovery.sqlite")
    with _launch_lock(root, create=False):
        (root / "SingletonLock").symlink_to("fictional-later-browser")
        try:
            obj = approval._BrowserWorkerApproval(lab.configuration, selection)
            # Consent/generation are fictional; files, SQL and the entire prior
            # chain are actual. Later Chromium ancestry alone is simulated.
            prepared = obj.prepare(expected, intent="e" * 64, reviewed_generation=7,
                                   consent=lambda review: review)
            claimed = obj.claim()
            assert claimed.state.native_revision == prepared.state.native_revision
            assert claimed.state.state_fingerprint != prepared.state.state_fingerprint
            assert claimed.state.mode is RecoveryMode.PAUSED and obj.confirm() == claimed
            paused = cancel._BrowserWorkerCancellation(lab.configuration, selection).pause(
                claimed.state)
            assert paused.state.native_revision == claimed.state.native_revision + 1
            with pytest.raises(approval.BrowserContinuationApprovalError):
                obj.confirm()
            with pytest.raises(approval.BrowserContinuationApprovalError):
                obj.claim()
        finally:
            (root / "SingletonLock").unlink()  # Fixture-only marker.
    after = state(lab, h)
    after["profile"].pop("recovery.sqlite")
    assert after == before and inspect(chain) == retained
    blocked(lab)


@pytest.mark.parametrize("failure", ["prepare-before", "prepare-after", "claim-before",
    "claim-after", "prepare-archive", "claim-runtime", "copied-release", "copied-intent"])
def test_owned_approval_complete_chain_uncertainty_never_permits_claim(
        lab, chain, monkeypatch, failure):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_approval as approval
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_continuation_epoch as epoch

    h, r, i = chain
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda r: r.confirmation)
    root = h._session._root
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    expected = current.inspect_stopped_continuation(root, **paths)
    monkeypatch.setattr(current, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    selection = BrowserWorkerSelection(paths["bundle"], paths["public_key"])
    obj = approval._BrowserWorkerApproval(lab.configuration, selection)

    def prepare():
        return obj.prepare(expected, intent="e" * 64, reviewed_generation=7,
                           consent=lambda review: review)

    if failure.startswith("copied"):
        path = root / (release.RELEASE_JOURNAL if failure == "copied-release" else
                       intent.INTENT_JOURNAL)
        raw = path.read_bytes()
        path.rename(path.with_name(path.name + ".retained"))
        private(path, raw)
    with _launch_lock(root, create=False):
        if failure.startswith("claim"):
            prepare()
        before = lab.ledger.path.read_bytes()

        def uncertain(db):
            if failure.endswith("after"):
                db.commit()
            raise sqlite3.OperationalError("PRIVATE uncertain commit")

        name = "_stage_claim" if failure.startswith("claim") else "_stage_prepare"
        stage = getattr(epoch, name)

        def changed(*args, **kwargs):
            result = stage(*args, **kwargs)
            if failure == "prepare-archive":
                path = h._session._archives / h._operation / "native-history.json"
                path.write_bytes(path.read_bytes() + b" ")
            elif failure == "claim-runtime":
                def invalid(**kwargs):
                    raise RuntimeError("PRIVATE changed runtime")

                monkeypatch.setattr(history, "_canonical_bundle_files", invalid)
            return result

        with monkeypatch.context() as patch:
            if failure.endswith(("before", "after")):
                patch.setattr(approval, "_commit", uncertain)
            else:
                patch.setattr(epoch, name, changed)
            with pytest.raises(approval.BrowserContinuationApprovalError):
                obj.claim() if failure.startswith("claim") else prepare()
        if failure.endswith("after"):
            assert obj.confirm().phase == ("claimed" if failure.startswith("claim") else "prepared")
        else:
            assert lab.ledger.path.read_bytes() == before
        stable = state(lab, h)
        with pytest.raises(approval.BrowserContinuationApprovalError):
            obj.claim()
        assert state(lab, h) == stable
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
@pytest.mark.parametrize("outcome", ["complete", "failed", "pause", "lost-completion"])
def test_owned_verification_complete_chain_keeps_history_and_cancellation(
        lab, chain, monkeypatch, outcome):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_cancel as cancel
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_continuation_verification as verification
    from sds200 import browser_device_verification as transport
    from sds200.browser_device_store import BrowserDeviceState

    h, r, i = chain
    retained = inspect(chain)
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda review: review.confirmation)
    root = h._session._root
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    expected = current.inspect_stopped_continuation(root, **paths)
    selection = BrowserWorkerSelection(paths["bundle"], paths["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    before = state(lab, h)
    before["profile"].pop("recovery.sqlite")
    calls, paused = [], []

    def verify(config, record):
        calls.append(record)
        assert record.generation == 7 and record.state is BrowserDeviceState.ACTIVE
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        if outcome == "failed":
            raise RuntimeError("PRIVATE fixture server refusal")
        if outcome == "pause":
            with current._worker_current_scope(config, selection) as reader:
                claimed = reader.inspect()
            paused.append(cancel._BrowserWorkerCancellation(config, selection).pause(claimed))
        return transport.BrowserVerifiedRecord(config.identity, record, True)

    def uncertain(db):
        db.commit()
        raise RuntimeError("PRIVATE lost completion reply")

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    if outcome == "lost-completion":
        monkeypatch.setattr(verification, "_commit", uncertain)
    with _launch_lock(root, create=False):
        (root / "SingletonLock").symlink_to("fictional-later-browser")
        try:
            obj = verification._BrowserWorkerVerification(lab.configuration, selection)
            kwargs = dict(intent="e" * 64, reviewed_generation=7, consent=lambda review: review)
            if outcome == "complete":
                result = obj.run(expected, **kwargs)
                assert result.phase == "complete" and result.state.mode is RecoveryMode.ACTIVE
                assert obj.confirm() == result
            else:
                with pytest.raises(verification.BrowserContinuationVerificationError):
                    obj.run(expected, **kwargs)
                if outcome == "pause":
                    with pytest.raises(verification.BrowserContinuationVerificationError):
                        obj.confirm()
                    with current._worker_current_scope(lab.configuration, selection) as reader:
                        assert reader.inspect() == paused[0].state
                else:
                    assert obj.confirm().phase == (
                        "complete" if outcome == "lost-completion" else "failed")
            stable = state(lab, h)
            with pytest.raises(verification.BrowserContinuationVerificationError):
                obj.run(expected, **kwargs)
            assert state(lab, h) == stable and len(calls) == 1
        finally:
            (root / "SingletonLock").unlink()  # Fresh fictional fixture only.
    after = state(lab, h)
    after["profile"].pop("recovery.sqlite")
    assert after == before and inspect(chain) == retained
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
@pytest.mark.parametrize("outcome", ["issued", "refused", "pause", "lost-verification"])
def test_owned_initial_session_complete_chain_preserves_history_and_pause(
        lab, chain, monkeypatch, outcome):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_cancel as cancel
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_continuation_session as session
    from sds200 import browser_device_continuation_verification as verification
    from sds200 import browser_device_verification as transport
    from sds200.browser_device_recovery import ExchangeSession
    from tests.test_browser_device_native import TOKEN

    h, r, i = chain
    retained = inspect(chain)
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda review: review.confirmation)
    root = h._session._root
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    expected = current.inspect_stopped_continuation(root, **paths)
    selection = BrowserWorkerSelection(paths["bundle"], paths["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    before = state(lab, h)
    before["profile"].pop("recovery.sqlite")
    calls, paused = [], []

    def verify(config, record):
        calls.append("verify")
        assert record.generation == 7
        return transport.BrowserVerifiedRecord(config.identity, record, True)

    def exchange(config, generation):
        calls.append("exchange")
        assert generation == 7
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        if outcome == "refused":
            raise RuntimeError("PRIVATE fixture refusal")
        if outcome == "pause":
            with current._worker_current_scope(config, selection) as reader:
                completed = reader.inspect()
            paused.append(cancel._BrowserWorkerCancellation(config, selection).pause(completed))
        return ExchangeSession(TOKEN, 300)

    def uncertain(db):
        db.commit()
        raise RuntimeError("PRIVATE lost verification reply")

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    if outcome == "lost-verification":
        monkeypatch.setattr(verification, "_commit", uncertain)
    with _launch_lock(root, create=False):
        (root / "SingletonLock").symlink_to("fictional-later-browser")
        try:
            obj = session._BrowserWorkerInitialSession(lab.configuration, selection)
            kwargs = dict(intent="e" * 64, reviewed_generation=7, consent=lambda review: review)
            if outcome == "issued":
                result = obj.run(expected, **kwargs)
                assert result.state.mode is RecoveryMode.ACTIVE
                # Prepare and completion advance revision; claim only changes
                # phase/fingerprint. Feed actual native fields to the JS core.
                assert result.state.native_revision == expected.native_revision + 2
                assert result.session.token == TOKEN and 290 < result.session.expires_in <= 300
                assert obj.confirm().state == result.state
                observed = current._observe_worker_continuation(lab.configuration, selection)
                assert observed.state == result.state and observed.generation == 7
                _browser_core_checks_native_fixture(expected, observed, result)
            else:
                with pytest.raises(session.BrowserContinuationSessionError):
                    obj.run(expected, **kwargs)
                if outcome == "pause":
                    with pytest.raises(session.BrowserContinuationSessionError):
                        obj.confirm()
                    with current._worker_current_scope(lab.configuration, selection) as reader:
                        assert reader.inspect() == paused[0].state
                else:
                    assert obj.confirm().phase == "complete"
                    observed = current._observe_worker_continuation(lab.configuration, selection)
                    assert observed.state.mode is RecoveryMode.ACTIVE and observed.generation == 7
                    _browser_core_checks_native_fixture(expected, observed)
            stable = state(lab, h)
            with pytest.raises(session.BrowserContinuationSessionError):
                obj.run(expected, **kwargs)
            assert state(lab, h) == stable
            assert calls == (["verify"] if outcome == "lost-verification"
                             else ["verify", "exchange"])
        finally:
            (root / "SingletonLock").unlink()  # Fresh fictional fixture only.
    after = state(lab, h)
    after["profile"].pop("recovery.sqlite")
    assert after == before and inspect(chain) == retained
    assert TOKEN.encode() not in lab.ledger.path.read_bytes()
    blocked(lab)


@pytest.mark.parametrize("chain", ["retire", "reconcile"], indirect=True)
@pytest.mark.parametrize("scenario", ["read-only", "release-before", "intent-before"])
def test_owned_active_recheck_complete_chain_never_mutates_the_native_grant(
        lab, chain, monkeypatch, scenario):
    from sds200 import browser_device_continuation_activation as activation
    from sds200 import browser_device_continuation_cancel as cancel
    from sds200 import browser_device_continuation_current as current
    from sds200 import browser_device_continuation_recheck as recheck
    from sds200 import browser_device_continuation_session as session
    from sds200 import browser_device_verification as transport
    from sds200.browser_device_recovery import ExchangeSession
    from tests.test_browser_device_native import TOKEN

    h, r, i = chain
    retained = inspect(chain)
    activation.BrowserPausedActivation(h, release_id=r.release_id, intent_id=i.intent_id).apply(
        confirmation=lambda review: review.confirmation)
    root = h._session._root
    paths = {k: lab.args[k] for k in ("bundle", "profile", "public_key")}
    expected = current.inspect_stopped_continuation(root, **paths)
    selection = BrowserWorkerSelection(paths["bundle"], paths["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: root)
    monkeypatch.setattr(transport, "verify_browser_device", lambda config, record:
                        transport.BrowserVerifiedRecord(config.identity, record, True))
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda config, generation: ExchangeSession(TOKEN, 300))
    with _launch_lock(root, create=False):
        (root / "SingletonLock").symlink_to("fictional-later-browser")
        try:
            session._BrowserWorkerInitialSession(lab.configuration, selection).run(expected,
                intent="e" * 64, reviewed_generation=7, consent=lambda review: review)
            observed = current._observe_worker_continuation(lab.configuration, selection)
            stable = state(lab, h)
            monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                                lambda *a: pytest.fail("Recheck allocated a session"))
            # Each real retained chain is expensive to construct. Reuse its
            # unchanged grant for valid/refused checks, with a fresh one-use
            # verifier for each. Pause runs last. Journal mutation scenarios have
            # separate chains and retain their changed bytes/metadata untouched.
            outcomes = ("valid", "refused", "pause") if scenario == "read-only" else (scenario,)
            for outcome in outcomes:
                assert state(lab, h) == stable
                saved, calls = [], []

                def verify(config, record, *, outcome=outcome, calls=calls, saved=saved):
                    calls.append(record)
                    assert record.generation == observed.generation == 7
                    with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
                        db.execute("BEGIN IMMEDIATE")
                        db.rollback()
                    if outcome == "pause":
                        cancel._BrowserWorkerCancellation(config, selection).pause(observed.state)
                    saved.append(state(lab, h))
                    if outcome == "refused":
                        raise RuntimeError("PRIVATE refusal")
                    return transport.BrowserVerifiedRecord(config.identity, record, True)

                monkeypatch.setattr(transport, "verify_browser_device", verify)
                obj = recheck._BrowserWorkerActiveVerification(lab.configuration, selection)
                if outcome.endswith("-before"):
                    name = (current.release.RELEASE_JOURNAL if outcome == "release-before"
                            else current.intent.INTENT_JOURNAL)
                    path = root / name
                    path.write_bytes(path.read_bytes() + b" ")
                    changed = state(lab, h)
                    with pytest.raises(recheck.BrowserContinuationRecheckError):
                        obj.run(observed)
                    assert calls == [] and state(lab, h) == changed
                    with pytest.raises(recheck.BrowserContinuationRecheckError):
                        obj.run(observed)
                    assert calls == [] and state(lab, h) == changed
                    return  # Retain the fictional changed journal; no fixture repair.
                if outcome == "valid":
                    assert obj.run(observed) == observed
                else:
                    with pytest.raises(recheck.BrowserContinuationRecheckError):
                        obj.run(observed)
                assert len(calls) == 1 and state(lab, h) == saved[0]
                if outcome != "pause":
                    assert saved[0] == stable
                with pytest.raises(recheck.BrowserContinuationRecheckError):
                    obj.run(observed)
                assert len(calls) == 1 and state(lab, h) == saved[0]
        finally:
            (root / "SingletonLock").unlink()  # Fresh fictional fixture only.
    assert inspect(chain) == retained
    blocked(lab)
