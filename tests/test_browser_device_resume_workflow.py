"""Trusted stopped-browser session; canonical installation, no production changes."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200 import browser_device_resume_workflow as workflow
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, ExchangeFailure, RecoveryMode
from sds200.browser_device_registration import (
    MAINTENANCE_MARKER,
    BrowserRegistrationError,
    inspect_browser_registration,
)
from sds200.browser_device_resume import BrowserDeviceResume
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
from sds200.browser_device_resume_workflow import BrowserResumeWorkflow, BrowserResumeWorkflowError
from sds200.browser_device_startup import BrowserStartupError, _launch_lock, check_browser_startup
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume import INTENT, evidence
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux stopped-browser workflow",
)


@pytest.fixture
def lab(inputs, tmp_path):
    archives = tmp_path / "native evidence"
    archives.mkdir(mode=0o700)
    config = load_browser_native_configuration(inputs["profile"])
    clock, elapsed = [time.time()], [10.0]
    ledger = BrowserDeviceRecovery(inputs["profile"] / "recovery.sqlite", config.identity,
                                    clock=lambda: clock[0])
    ledger.claim_browser()
    ledger.suspend()
    # Ordinary prior launcher use leaves this zero-byte coordination file.
    with _launch_lock(inputs["root"]):
        pass
    private(inputs["root"] / "opaque-browser-state", b"do not parse or change browser state")
    args = {"directory": inputs["root"], "profile": inputs["profile"],
            "bundle": inputs["bundle"], "public_key": inputs["public_key"], "archives": archives}
    native = BrowserDeviceResume(inputs["profile"], clock=lambda: clock[0],
                                 monotonic=lambda: elapsed[0])
    return SimpleNamespace(inputs=inputs, args=args, configuration=config, ledger=ledger,
                           clock=clock, elapsed=elapsed, native=native,
                           make=lambda: BrowserResumeWorkflow(**args, clock=lambda: clock[0],
                                                               monotonic=lambda: elapsed[0]))


def prepare(lab, kind):
    if kind == "retire":
        lab.native.prepare(expected_revision=lab.ledger.inspect().revision,
            browser_intent=INTENT, reviewed_server=evidence(lab.configuration))


def apply(lab, callback=lambda reviewed: reviewed.confirmation, *, kind="reconcile", session=None):
    return (session or lab.make()).apply(kind=kind, browser_intent=INTENT, confirmation=callback)


def snap(lab):
    return {name: snapshot(lab.args[name])
            for name in ("directory", "profile", "bundle", "archives")}


@pytest.mark.parametrize("kind", ["retire", "reconcile"])
@pytest.mark.parametrize("mode", [m for m in RecoveryMode if m is not RecoveryMode.ACTIVE])
def test_exact_review_guard_before_commit_and_read_only_confirmation(lab, monkeypatch, kind, mode):
    if mode is not RecoveryMode.PAUSED:
        lab.ledger.resume(lab.ledger.inspect().revision)

        def fail():
            raise ExchangeFailure(mode)

        lab.ledger.authenticate(fail)
    prepare(lab, kind)
    before = snap(lab)
    seen = []

    def consent(reviewed):
        seen.append(reviewed)
        assert reviewed.directory == lab.args["directory"]
        assert reviewed.profile == lab.args["profile"] and reviewed.archives == lab.args["archives"]
        assert reviewed.origin == lab.configuration.origin
        assert reviewed.device_id == lab.configuration.device_id
        assert reviewed.native_revision == lab.ledger.inspect().revision
        assert reviewed.native_mode == mode and reviewed.kind == kind
        assert reviewed.approvals == (1 if kind == "retire" else 0)
        assert reviewed.pending == (1 if kind == "retire" else 0)
        assert reviewed.expires_at == lab.clock[0] + 120
        assert all(value not in repr(reviewed) for value in
                   (CREDENTIAL, reviewed.operation_id, str(reviewed.directory), INTENT))
        assert snap(lab) == before  # No browser or native changes during review.
        with pytest.raises(BlockingIOError), _launch_lock(lab.args["directory"]):
            pytest.fail("Competing launcher was allowed during consent")
        return reviewed.confirmation

    original = BrowserResumeBoundary.execute

    def execute(boundary, selected):
        marker = lab.args["directory"] / MAINTENANCE_MARKER
        document = json.loads(marker.read_bytes())
        assert document["operation_id"] == selected.operation_id
        assert document["intent"] == INTENT and document["kind"] == kind
        assert marker.stat().st_mode & 0o777 == 0o600
        assert CREDENTIAL not in marker.read_text()
        assert snapshot(lab.args["profile"]) == before["profile"]
        with pytest.raises(BrowserStartupError):
            check_browser_startup(**lab.inputs)
        return original(boundary, selected)

    monkeypatch.setattr(BrowserResumeBoundary, "execute", execute)
    result = apply(lab, consent, kind=kind)
    assert result.mode == mode and result.revision == seen[0].native_revision + 1
    assert len(seen) == 1 and lab.ledger.inspect().mode is mode
    after = snap(lab)
    assert set(after["directory"]) == set(before["directory"]) | {MAINTENANCE_MARKER}
    assert {name: body for name, body in after["directory"].items()
            if name != MAINTENANCE_MARKER} == before["directory"]
    assert after["bundle"] == before["bundle"]
    assert {name: body for name, body in after["profile"].items()
            if name != "recovery.sqlite"} == {
                name: body for name, body in before["profile"].items() if name != "recovery.sqlite"}
    lab.clock[0] += 200  # Expired review may only confirm its already-committed operation.
    assert lab.make().confirm(operation_id=seen[0].operation_id, browser_intent=INTENT) == result
    assert snap(lab) == after
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)
    with pytest.raises(BrowserResumeWorkflowError):
        apply(lab)
    assert snap(lab) == after


@pytest.mark.parametrize("answer", [None, "yes", True, "", "MAINTAIN " + "e" * 64])
def test_cancel_or_wrong_confirmation_never_mutates_and_session_is_one_use(lab, answer):
    session = lab.make()
    before = snap(lab)
    if answer is None:
        assert apply(lab, lambda _: answer, session=session) is None
    else:
        with pytest.raises(BrowserResumeWorkflowError):
            apply(lab, lambda _: answer, session=session)
    assert snap(lab) == before
    with pytest.raises(BrowserResumeWorkflowError):
        apply(lab, session=session)
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["wall-expired", "wall-rollback", "elapsed-expired",
                                   "elapsed-rollback", "nan", "exception"])
def test_stale_or_failed_consent_never_writes_guard_or_native(lab, change):
    before = snap(lab)

    def callback(reviewed):
        if change == "wall-expired":
            lab.clock[0] += 120
        elif change == "wall-rollback":
            lab.clock[0] -= 1
        elif change == "elapsed-expired":
            lab.elapsed[0] += 120
        elif change == "elapsed-rollback":
            lab.elapsed[0] -= 1
        elif change == "nan":
            lab.elapsed[0] = float("nan")
        else:
            raise RuntimeError(CREDENTIAL)
        return reviewed.confirmation

    with pytest.raises(BrowserResumeWorkflowError) as caught:
        apply(lab, callback)
    assert CREDENTIAL not in str(caught.value) and snap(lab) == before


@pytest.mark.parametrize("change", ["singleton", "receipt", "bundle", "lock-replaced", "lock-mode",
                                   "root-replaced", "native-change", "secret-change"])
def test_changes_during_review_refuse_and_preserve_external_edits(lab, change):
    changed = []

    def callback(reviewed):
        root = lab.args["directory"]
        if change == "singleton":
            (root / "SingletonLock").symlink_to("stale-marker-stays")
        elif change == "receipt":
            private(root / ".sdsctl-browser-registration.json", b"edited registration")
        elif change == "bundle":
            private(lab.args["bundle"] / "extension/worker.mjs", b"edited bundle")
        elif change == "lock-replaced":
            (root / workflow._LOCK).unlink()
            private(root / workflow._LOCK, b"")
        elif change == "lock-mode":
            (root / workflow._LOCK).chmod(0o644)
        elif change == "root-replaced":
            old = root.with_name("old-browser")
            root.rename(old)
            shutil.copytree(old, root)
        elif change == "native-change":
            lab.ledger.resume(lab.ledger.inspect().revision)
            lab.ledger.suspend()
        else:
            private(lab.args["profile"] / "device.secret", "sdsctl-browser-v1." + "e" * 64)
        changed.append(snapshot(lab.args["profile"]))
        return reviewed.confirmation

    with pytest.raises(BrowserResumeWorkflowError):
        apply(lab, callback)
    assert snapshot(lab.args["profile"]) == changed[0]
    if change not in {"native-change", "secret-change"}:
        assert not (lab.args["directory"] / MAINTENANCE_MARKER).exists()
    else:
        assert (lab.args["directory"] / MAINTENANCE_MARKER).is_file()
    if change == "singleton":
        assert (lab.args["directory"] / "SingletonLock").is_symlink()


@pytest.mark.parametrize("change", ["busy", "singleton", "marker", "missing-secret",
                                   "missing-ca", "edited-receipt", "active"])
def test_unsafe_or_running_installation_refuses_before_review(lab, change):
    root = lab.args["directory"]
    if change == "singleton":
        (root / "SingletonCookie").symlink_to("missing-marker")
    elif change == "marker":
        private(root / MAINTENANCE_MARKER, b"prior unrelated work")
    elif change == "missing-secret":
        (lab.args["profile"] / "device.secret").unlink()
    elif change == "missing-ca":
        (lab.args["profile"] / "ca.pem").unlink()
    elif change == "edited-receipt":
        private(root / ".sdsctl-browser-registration.json", b"edited")
    elif change == "active":
        lab.ledger.resume(lab.ledger.inspect().revision)
    before = snap(lab)

    def callback(_):
        pytest.fail("Unsafe profile reached consent UI")

    if change == "busy":
        with _launch_lock(root), pytest.raises(BrowserResumeWorkflowError):
            apply(lab, callback)
    else:
        with pytest.raises(BrowserResumeWorkflowError):
            apply(lab, callback)
    assert snap(lab) == before


def test_review_creates_only_missing_coordination_lock_and_cancel_preserves_browser(lab):
    lock = lab.args["directory"] / workflow._LOCK
    lock.unlink()
    before = snap(lab)
    assert apply(lab, lambda _: None) is None
    after = snap(lab)
    assert after["directory"].pop(workflow._LOCK)[0] == b""
    assert after == before and lock.stat().st_mode & 0o777 == 0o600


def test_new_process_session_does_not_accept_old_consent_even_for_same_native_review(lab):
    seen = []
    assert apply(lab, lambda reviewed: seen.append(reviewed)) is None
    before = snap(lab)

    def stale(reviewed):
        assert reviewed.operation_id == seen[0].operation_id  # Same fixed fixture clock/state.
        assert reviewed.confirmation != seen[0].confirmation
        return seen[0].confirmation

    with pytest.raises(BrowserResumeWorkflowError):
        apply(lab, stale)
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["removed", "edited", "expired"])
def test_guard_change_or_delay_before_execution_never_mutates_native(lab, monkeypatch, change):
    original = BrowserResumeWorkflow._guard

    def guard(self, body):
        original(self, body)
        if change == "removed":
            (lab.args["directory"] / MAINTENANCE_MARKER).unlink()
        elif change == "edited":
            private(lab.args["directory"] / MAINTENANCE_MARKER, b"changed marker")
        else:
            lab.elapsed[0] += 120

    monkeypatch.setattr(BrowserResumeWorkflow, "_guard", guard)
    before = snapshot(lab.args["profile"])
    with pytest.raises(BrowserResumeWorkflowError):
        apply(lab)
    assert snapshot(lab.args["profile"]) == before
    assert not list(lab.args["archives"].iterdir())


@pytest.mark.parametrize("phase", ["guard-write", "guard-fsync", "before-native", "after-native"])
def test_failures_retain_guard_and_read_only_confirmation_never_replays(lab, monkeypatch, phase):
    seen = []
    original_guard, original_execute = workflow._write, BrowserResumeBoundary.execute

    def guard(directory, name, body):
        assert name == MAINTENANCE_MARKER
        if phase == "guard-write":
            original_guard(directory, name, b"partial")
            raise OSError(CREDENTIAL)
        original_guard(directory, name, body)

    def execute(boundary, review):
        if phase == "before-native":
            raise OSError(CREDENTIAL)
        result = original_execute(boundary, review)
        if phase == "after-native":
            raise OSError(CREDENTIAL)
        return result

    monkeypatch.setattr(workflow, "_write", guard)
    monkeypatch.setattr(BrowserResumeBoundary, "execute", execute)
    if phase == "guard-fsync":
        monkeypatch.setattr(workflow.os, "fsync", lambda _: (_ for _ in ()).throw(OSError()))
    before = snapshot(lab.args["profile"])
    with pytest.raises(BrowserResumeWorkflowError) as caught:
        apply(lab, lambda reviewed: (seen.append(reviewed), reviewed.confirmation)[1])
    assert CREDENTIAL not in str(caught.value)
    assert (lab.args["directory"] / MAINTENANCE_MARKER).exists()
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)
    after = snap(lab)
    if phase == "after-native":
        result = lab.make().confirm(operation_id=seen[0].operation_id, browser_intent=INTENT)
        assert result.mode is RecoveryMode.PAUSED
    else:
        assert snapshot(lab.args["profile"]) == before
        with pytest.raises(BrowserResumeWorkflowError):
            lab.make().confirm(operation_id=seen[0].operation_id, browser_intent=INTENT)
    assert snap(lab) == after


@pytest.mark.parametrize("change", ["no-lock", "no-guard", "partial", "duplicate",
    "wrong-operation", "wrong-intent", "other-marker", "changed-review", "bool-count",
    "changed-target", "guard-link", "guard-hardlink", "native-change"])
def test_read_only_confirmation_refuses_missing_changed_or_unrelated_evidence(lab, change):
    seen = []
    apply(lab, lambda r: (seen.append(r), r.confirmation)[1])
    root = lab.args["directory"]
    marker = root / MAINTENANCE_MARKER
    raw = marker.read_bytes()
    args = {"operation_id": seen[0].operation_id, "browser_intent": INTENT}
    if change == "no-lock":
        (root / workflow._LOCK).unlink()
    elif change == "no-guard":
        marker.unlink()
    elif change == "partial":
        private(marker, b"partial")
    elif change == "duplicate":
        private(marker, raw.replace(b'"version":1', b'"version":1,"version":1'))
    elif change == "wrong-operation":
        args["operation_id"] = "../elsewhere"
    elif change == "wrong-intent":
        args["browser_intent"] = "e" * 64
    elif change == "guard-link":
        other = root / "other"
        private(other, raw)
        marker.unlink()
        marker.symlink_to(other)
    elif change == "guard-hardlink":
        os.link(marker, root / "other")
    elif change == "native-change":
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    else:
        data = json.loads(raw)
        if change == "other-marker":
            data["operation"] = "update"
        elif change == "changed-review":
            data["kind"] = "retire"
        elif change == "bool-count":
            data["approvals"] = False
        else:
            data["targets"]["directory"] = "/untrusted"
        private(marker, workflow._encoded(data))
    before = snap(lab)
    with pytest.raises(BrowserResumeWorkflowError):
        lab.make().confirm(**args)
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["nested", "same", "symlink", "relative"])
def test_ambiguous_root_selection_is_rejected(lab, change):
    args = dict(lab.args)
    if change == "nested":
        args["archives"] = args["directory"] / "nested"
    elif change == "same":
        args["archives"] = args["profile"]
    elif change == "symlink":
        alias = args["archives"].with_name("alias")
        alias.symlink_to(args["archives"])
        args["archives"] = alias
    else:
        args["directory"] = Path("relative")
    with pytest.raises(BrowserResumeWorkflowError):
        BrowserResumeWorkflow(**args)


PROCESS = """
import os,sys
from pathlib import Path
import sds200.browser_device_resume_workflow as workflow
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
args={name:Path(value) for name,value in zip(
    ('directory','profile','bundle','public_key','archives'),sys.argv[1:6])}
stage=sys.argv[6]
original_guard=workflow.BrowserResumeWorkflow._guard
original_execute=BrowserResumeBoundary.execute
def guard(self,body):
    original_guard(self,body)
    if stage=='guard': os._exit(61)
def execute(self,review):
    result=original_execute(self,review)
    if stage=='commit': os._exit(62)
    return result
workflow.BrowserResumeWorkflow._guard=guard
BrowserResumeBoundary.execute=execute
def consent(review):
    print(review.operation_id,flush=True)
    if stage=='consent':
        sys.stdin.readline()
    return review.confirmation
workflow.BrowserResumeWorkflow(**args).apply(
    kind='reconcile',browser_intent='f'*64,confirmation=consent)
"""


@pytest.mark.parametrize("stage", ["consent", "guard", "commit"])
def test_real_process_loss_releases_owner_without_replaying_or_removing_guard(lab, stage):
    command = [sys.executable, "-c", PROCESS,
               *[str(lab.args[name]) for name in
                 ("directory", "profile", "bundle", "public_key", "archives")], stage]
    before = snap(lab)
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=5)
            operation = child.stdout.readline().strip()
        assert len(operation) == 64
        if stage == "consent":
            with pytest.raises(BlockingIOError), _launch_lock(lab.args["directory"]):
                pytest.fail("Review did not hold managed launcher ownership")
            child.kill()
        _, error = child.communicate(timeout=5)
        assert error == ""
        with _launch_lock(lab.args["directory"], create=False):
            pass  # Kernel released ownership, never delete the lock file.
        after = snap(lab)
        lab.clock[0] = time.time()
        if stage == "commit":
            assert child.returncode == 62
            confirmed = lab.make().confirm(operation_id=operation, browser_intent=INTENT)
            assert confirmed.mode is RecoveryMode.PAUSED
        else:
            with pytest.raises(BrowserResumeWorkflowError):
                lab.make().confirm(operation_id=operation, browser_intent=INTENT)
            assert after["profile"] == before["profile"]
        assert snap(lab) == after
        if stage != "consent":
            assert (lab.args["directory"] / MAINTENANCE_MARKER).is_file()
            with pytest.raises(BrowserRegistrationError):
                inspect_browser_registration(lab.args["directory"], **{
                    key: lab.args[key] for key in ("bundle", "profile", "public_key")})
        else:
            assert after == before
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
