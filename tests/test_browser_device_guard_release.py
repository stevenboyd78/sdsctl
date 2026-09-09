"""Paused-only release, atomic journal and unchanged historical evidence.

The supervised fixture owns real processes but mimics Chromium's API. Separate
headed qualification must prove browser storage remains paused after release.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing

import pytest

from sds200 import browser_device_guard_release as release
from sds200.browser_device_handoff import BrowserHandoffError
from sds200.browser_device_launch import run_browser_recovery
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import BrowserDeviceRecovery, RecoveryMode
from sds200.browser_device_registration import MAINTENANCE_MARKER
from sds200.browser_device_startup import BrowserStartupError, _launch_lock, check_browser_startup
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_launch import staged as staged
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux supervised guard release")


@pytest.fixture
def completed(lab, staged):
    _, _, _, setup, _ = staged
    handoff, browser, bwrap = setup()
    run_browser_recovery(handoff, browser=browser, bwrap=bwrap)
    handoff.restore()
    return handoff


def attempt(handoff, callback=lambda review: review.confirmation, **kwargs):
    return release.BrowserPausedGuardRelease(handoff, **kwargs).apply(confirmation=callback)


def blocked(lab):
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)


def state(lab, handoff):
    return {**{name: snapshot(lab.args[name]) for name in (
        "directory", "profile", "bundle", "archives")}, "handoff": snapshot(handoff._root),
        "recovery_bundle": snapshot(handoff._recovery)}


def test_exact_local_release_keeps_pause_and_guard_and_historical_confirmation(lab, completed):
    before = state(lab, completed)
    old_ack = completed.confirm(restored=True)
    seen = []

    def consent(review):
        seen.append(review)
        assert review.directory == lab.args["directory"]
        assert review.profile == lab.args["profile"]
        assert review.handoff == completed._root
        assert review.origin == lab.configuration.origin
        assert review.device_id == lab.configuration.device_id
        assert review.operation_id == completed._operation
        assert review.native_revision == lab.ledger.inspect().revision
        assert all(value not in repr(review) for value in (
            review.confirmation, str(review.directory), review.origin, CREDENTIAL))
        assert state(lab, completed) == before
        with pytest.raises(BlockingIOError), _launch_lock(review.directory):
            pytest.fail("Competing launcher allowed")
        with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                review.profile, exclusive=True):
            pytest.fail("Competing private-input maintenance allowed")
        with (closing(sqlite3.connect(review.profile / "recovery.sqlite", timeout=0)) as db,
              pytest.raises(sqlite3.OperationalError)):
            db.execute("BEGIN IMMEDIATE")
        return review.confirmation

    result = attempt(completed, consent)
    assert result.mode is RecoveryMode.PAUSED and len(seen) == 1
    assert result.release_id == seen[0].release_id
    after = state(lab, completed)
    directory = after.pop("directory")
    assert after == {k: v for k, v in before.items() if k != "directory"}
    assert {k: v for k, v in directory.items()
            if k != release.RELEASE_JOURNAL} == before["directory"]
    journal = lab.args["directory"] / release.RELEASE_JOURNAL
    assert journal.stat().st_mode & 0o777 == 0o600
    assert CREDENTIAL.encode() not in journal.read_bytes()
    assert seen[0].confirmation.encode() not in journal.read_bytes()
    assert completed.confirm(restored=True) == old_ack
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert check_browser_startup(**lab.inputs).identity == result.identity
    saved = state(lab, completed)
    session = release.BrowserPausedGuardRelease(completed)
    assert session.confirm(release_id=result.release_id) == result
    assert state(lab, completed) == saved
    with pytest.raises(release.BrowserGuardReleaseError):
        session.confirm(release_id="a" * 64)
    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed)
    assert state(lab, completed) == saved


@pytest.mark.parametrize("answer", [None, "yes", True, "", "RELEASE PAUSED " + "a" * 64])
def test_cancel_wrong_confirmation_and_one_use(lab, completed, answer):
    before = state(lab, completed)
    session = release.BrowserPausedGuardRelease(completed)
    if answer is None:
        assert session.apply(confirmation=lambda _: answer) is None
    else:
        with pytest.raises(release.BrowserGuardReleaseError):
            session.apply(confirmation=lambda _: answer)
    with pytest.raises(release.BrowserGuardReleaseError):
        session.apply(confirmation=lambda r: r.confirmation)
    assert state(lab, completed) == before
    blocked(lab)


@pytest.mark.parametrize("change", ["wall-expired", "wall-backward", "elapsed-expired",
                                  "elapsed-backward", "nan", "infinity"])
def test_consent_clocks_fail_closed(lab, completed, change):
    wall, elapsed = [1000.0], [10.0]
    before = state(lab, completed)

    def consent(review):
        if change == "wall-expired":
            wall[0] += 120
        elif change == "wall-backward":
            wall[0] -= 1
        elif change == "elapsed-expired":
            elapsed[0] += 120
        elif change == "elapsed-backward":
            elapsed[0] -= 1
        elif change == "nan":
            wall[0] = float("nan")
        else:
            elapsed[0] = float("inf")
        return review.confirmation

    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed, consent, clock=lambda: wall[0], monotonic=lambda: elapsed[0])
    assert state(lab, completed) == before
    blocked(lab)


@pytest.mark.parametrize("target", ["guard", "ack", "restored", "host", "lock", "singleton",
                                   "journal-sidecar", "native-config"])
def test_changes_during_consent_are_not_released(lab, completed, target):
    root = lab.args["directory"]

    def consent(review):
        paths = {"guard": root / MAINTENANCE_MARKER,
                 "ack": completed._root / "browser-acknowledgement.json",
                 "restored": completed._root / "restored.json",
                 "host": root / "NativeMessagingHosts/org.sdsctl.browser_device.json",
                 "lock": root / ".sdsctl-device-launch.lock",
                 "native-config": lab.args["profile"] / "client.json"}
        if target == "singleton":
            private(root / "SingletonLock", b"busy")
        elif target == "journal-sidecar":
            (root / (release.RELEASE_JOURNAL + "-journal")).symlink_to("/nonexistent")
        else:
            path = paths[target]
            data = path.read_bytes()
            path.rename(path.with_name(path.name + ".old"))
            private(path, data if target in {"guard", "lock"} else data + b"changed")
        return review.confirmation

    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed, consent)
    assert not (root / release.RELEASE_JOURNAL).exists()
    blocked(lab)


@pytest.mark.parametrize("when", ["create-before", "create-after", "complete-before",
                                "recheck", "complete-after"])
def test_interrupted_result_never_replays(lab, completed, monkeypatch, when):
    seen = []
    name = "_create" if when.startswith("create") else "_complete"
    original = getattr(release, name)

    def fail(*args):
        if when.endswith("before"):
            raise OSError("fictional private failure")
        if when == "recheck":
            def failed_check():
                args[2]()
                raise OSError("fictional private failure")
            original(args[0], args[1], failed_check)
        else:
            original(*args)
        raise OSError("fictional private failure")

    monkeypatch.setattr(release, name, fail)
    with pytest.raises(release.BrowserGuardReleaseError) as error:
        attempt(completed, lambda r: seen.append(r) or r.confirmation)
    assert "fictional private failure" not in str(error.value)
    assert (lab.args["directory"] / MAINTENANCE_MARKER).exists()
    saved = state(lab, completed)
    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed)
    session = release.BrowserPausedGuardRelease(completed)
    if when == "complete-after":
        assert session.confirm(release_id=seen[0].release_id).mode is RecoveryMode.PAUSED
        check_browser_startup(**lab.inputs)
    else:
        with pytest.raises(release.BrowserGuardReleaseError):
            session.confirm(release_id=seen[0].release_id)
        blocked(lab)
    assert state(lab, completed) == saved


@pytest.mark.parametrize("target", ["journal-missing", "journal-inode", "journal-mode",
                                   "journal-schema", "journal-content", "hot-journal", "wal",
                                   "guard-missing", "guard-inode", "ack", "restored", "revision"])
def test_completed_evidence_change_blocks_ordinary_startup(lab, completed, target):
    result = attempt(completed)
    root = lab.args["directory"]
    path = root / release.RELEASE_JOURNAL
    if target in {"journal-missing", "journal-inode"}:
        data = path.read_bytes()
        path.rename(root / "old-release.sqlite")
        if target == "journal-inode":
            private(path, data)
    elif target == "journal-mode":
        path.chmod(0o644)
    elif target in {"journal-schema", "journal-content"}:
        with closing(sqlite3.connect(path)) as db:
            if target == "journal-schema":
                db.execute("CREATE TABLE extra (value)")
            else:
                db.execute("UPDATE release SET body=?", (b"{}",))
            db.commit()
    elif target in {"hot-journal", "wal"}:
        private(root / (release.RELEASE_JOURNAL + (
            "-journal" if target == "hot-journal" else "-wal")), b"unsafe")
    elif target.startswith("guard"):
        guard = root / MAINTENANCE_MARKER
        data = guard.read_bytes()
        guard.rename(root / "guard.old")
        if target == "guard-inode":
            private(guard, data)
    elif target == "revision":
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    else:
        selected = completed._root / ("browser-acknowledgement.json" if target == "ack"
                                       else "restored.json")
        selected.write_bytes(b"{}")
    before = state(lab, completed)
    blocked(lab)
    with pytest.raises(release.BrowserGuardReleaseError):
        release.BrowserPausedGuardRelease(completed).confirm(release_id=result.release_id)
    assert state(lab, completed) == before


def test_unrestored_handoff_not_eligible(lab, staged):
    _, _, _, setup, _ = staged
    handoff, browser, bwrap = setup()
    run_browser_recovery(handoff, browser=browser, bwrap=bwrap)
    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(handoff, lambda _: pytest.fail("Must reject before consent"))
    with pytest.raises(BrowserHandoffError):
        handoff.confirm(restored=True)
    blocked(lab)


@pytest.mark.parametrize("when", ["empty", "prepared", "directory-sync", "updated",
                                "commit-before", "commit-after", "final-read"])
def test_actual_process_loss_at_journal_boundaries(lab, completed, monkeypatch, when):
    """SIGKILL-like _exit: no Python cleanup, rollback, finally, or inherited writer."""
    initial = {k: v for k, v in state(lab, completed).items() if k != "directory"}
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_create, original_complete = release._create, release._complete
        original_connect = release._connect

        def consent(review):
            os.write(write_fd, review.release_id.encode("ascii"))
            return review.confirmation

        def create(*args):
            if when == "empty":
                os._exit(72)
            original_create(*args)
            if when == "prepared":
                os._exit(72)

        class Connection:
            def __init__(self, db):
                self.db, self.updated = db, False

            def execute(self, statement, *args):
                result = self.db.execute(statement, *args)
                if statement.startswith("UPDATE release"):
                    self.updated = True
                    if when == "updated":
                        os._exit(72)
                return result

            def commit(self):
                if self.updated and when == "commit-before":
                    os._exit(72)
                self.db.commit()
                if self.updated and when == "commit-after":
                    os._exit(72)

            def close(self):
                self.db.close()

        def complete(*args):
            monkeypatch.setattr(release, "_connect", lambda *a, **k: Connection(
                original_connect(*a, **k)))
            original_complete(*args)

        monkeypatch.setattr(release, "_create", create)
        monkeypatch.setattr(release, "_complete", complete)
        if when == "directory-sync":
            monkeypatch.setattr(release.os, "fsync", lambda _: os._exit(72))
        if when == "final-read":
            monkeypatch.setattr(release, "_confirmed", lambda *a, **k: os._exit(72))
        try:
            attempt(completed, consent)
        except BaseException:
            os._exit(73)
        os._exit(74)
    os.close(write_fd)
    try:
        release_id = os.read(read_fd, 65).decode("ascii")
        _, status = os.waitpid(child, 0)
    finally:
        os.close(read_fd)
    assert os.waitstatus_to_exitcode(status) == 72
    assert len(release_id) == 64
    assert (lab.args["directory"] / MAINTENANCE_MARKER).exists()
    assert {k: v for k, v in state(lab, completed).items() if k != "directory"} == initial
    saved = state(lab, completed)
    if when in {"commit-after", "final-read"}:
        assert release.BrowserPausedGuardRelease(completed).confirm(
            release_id=release_id).mode is RecoveryMode.PAUSED
        check_browser_startup(**lab.inputs)
    else:
        with pytest.raises(release.BrowserGuardReleaseError):
            release.BrowserPausedGuardRelease(completed).confirm(release_id=release_id)
        blocked(lab)
    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed)
    assert state(lab, completed) == saved


def test_failed_directory_sync_never_completes(lab, completed, monkeypatch):
    def fail(_):
        raise OSError("fictional sync failure")

    monkeypatch.setattr(release.os, "fsync", fail)
    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed)
    path = lab.args["directory"] / release.RELEASE_JOURNAL
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        assert db.execute("SELECT phase FROM release").fetchone() == ("prepared",)
    blocked(lab)


@pytest.mark.parametrize("change", ["elapsed", "guard", "journal-inode"])
def test_rechecks_after_preparation_before_commit(lab, completed, monkeypatch, change):
    elapsed = [10.0]
    original = release._create

    def changed(path, body):
        original(path, body)
        if change == "elapsed":
            elapsed[0] += 120
        elif change == "guard":
            (lab.args["directory"] / MAINTENANCE_MARKER).write_bytes(b"{}")
        else:
            data = path.read_bytes()
            path.rename(path.with_name("old.sqlite"))
            private(path, data)

    monkeypatch.setattr(release, "_create", changed)
    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed, monotonic=lambda: elapsed[0])
    blocked(lab)


@pytest.mark.parametrize("owner", ["launch", "profile", "native"])
def test_competing_ownership_rejects_before_consent(lab, completed, owner):
    root, profile = lab.args["directory"], lab.args["profile"]
    if owner == "launch":
        lock = _launch_lock(root, create=False)
    elif owner == "profile":
        lock = browser_profile_access(profile, exclusive=True)
    else:
        lock = closing(sqlite3.connect(profile / "recovery.sqlite", timeout=0))
    with lock as value:
        if owner == "native":
            value.execute("BEGIN IMMEDIATE")
        before = state(lab, completed)
        with pytest.raises(release.BrowserGuardReleaseError):
            attempt(completed, lambda _: pytest.fail("Must reject before consent"))
        assert state(lab, completed) == before
    blocked(lab)


def test_unsupervised_receipts_are_never_release_authority(lab, completed):
    completed._supervised = False
    before = state(lab, completed)
    with pytest.raises(release.BrowserGuardReleaseError):
        attempt(completed, lambda _: pytest.fail("Must reject before consent"))
    assert state(lab, completed) == before
    blocked(lab)


@pytest.mark.parametrize("change", ["none", "no-release", "changed-guard", "later-revision",
                                    "unlocked"])
def test_worker_context_requires_exact_release_even_with_running_browser(
        lab, completed, monkeypatch, change):
    from sds200 import browser_device_worker as worker
    if change != "no-release":
        attempt(completed)
    root = lab.args["directory"]
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: root)
    if change == "changed-guard":
        private(root / MAINTENANCE_MARKER, b"changed")
    elif change == "later-revision":
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    with _launch_lock(root, create=False):
        # Opaque Chromium-owned marker is present during a real worker start.
        private(root / "SingletonLock", b"fictional running browser")
        before = state(lab, completed)
        if change in {"none", "unlocked"}:
            result = worker.worker_context(lab.configuration, selected, None)
            assert result["role"] == "paused" and result["acknowledge"] is False
        else:
            with pytest.raises((ValueError, release.BrowserGuardReleaseError,
                                BrowserHandoffError, BrowserProfileAccessError, OSError)):
                worker.worker_context(lab.configuration, selected, None)
        assert state(lab, completed) == before
    if change == "unlocked":
        with pytest.raises(ValueError):
            worker.worker_context(lab.configuration, selected, None)


@pytest.mark.parametrize("action", ["prepare-resume", "review-resume", "commit-resume",
                                    "authenticate", "suspend", "claim-browser"])
def test_released_worker_refuses_new_resume_preparation_without_invalidating_release(
        lab, completed, monkeypatch, action):
    """A local paused-start receipt is not authority for a new native revision."""
    import io
    import json

    from sds200 import browser_device_native as native
    from sds200 import browser_device_resume as resume
    from sds200 import browser_device_verification as verification
    from sds200 import browser_device_worker as worker
    from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
    from sds200.browser_device_verification import BrowserVerifiedRecord
    from tests.test_browser_device_native import frame

    attempt(completed)
    root = lab.args["directory"]
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: root)
    calls = []

    def verified(*_):
        calls.append("server-verification")
        return BrowserVerifiedRecord(lab.configuration.identity, BrowserDeviceRecord(
            lab.configuration.device_id, 1, BrowserDeviceState.ACTIVE), True)

    monkeypatch.setattr(resume, "verify_browser_device", verified)
    monkeypatch.setattr(verification, "verify_browser_device", verified)
    original_resume, original_handle = native._resume_request, BrowserDeviceRecovery.handle

    def resume_request(*args):
        calls.append("resume-dispatch")
        return original_resume(*args)

    def handle(*args):
        calls.append("ordinary-mutating-dispatch")
        return original_handle(*args)

    monkeypatch.setattr(native, "_resume_request", resume_request)
    monkeypatch.setattr(BrowserDeviceRecovery, "handle", handle)
    with _launch_lock(root, create=False):
        private(root / "SingletonLock", b"fictional running browser")
        worker.worker_context(lab.configuration, selected, None)
        before = state(lab, completed)
        request = {"version": 1, "action": action}
        if action in {"prepare-resume", "commit-resume"}:
            request.update(intent="d" * 64, revision=lab.ledger.inspect().revision)
            if action == "prepare-resume":
                request["generation"] = 1
            else:
                request.update(ticket="f" * 64, expires_at=1000)
        envelope = {"version": 1, "action": "worker-request", "build": worker.worker_graph()[0],
                    "request": request}
        destination = io.BytesIO()
        result = native._native_request(lab.args["profile"], [lab.configuration.extension_origin],
            io.BytesIO(frame(envelope)), destination, expected_identity=lab.configuration.identity,
            worker=selected)
        response = json.loads(destination.getvalue()[4:])
        refused = response.get("ok") is False
        unchanged = state(lab, completed) == before
        try:
            worker.worker_context(lab.configuration, selected, None)
            release_valid = True
        except Exception:
            release_valid = False
        # Only booleans in a failure; never print a returned private ticket.
        assert result == 0 and (refused, unchanged, release_valid) == (True, True, True)
        assert calls == []
        assert state(lab, completed) == before
        worker.worker_context(lab.configuration, selected, None)


@pytest.mark.parametrize("rollback", ["none", "status", "release"])
def test_released_worker_status_never_writes_evidence_on_clock_rollback(
        lab, completed, monkeypatch, rollback):
    import io
    import json

    from sds200 import browser_device_native as native
    from sds200 import browser_device_worker as worker
    from tests.test_browser_device_native import frame

    attempt(completed)
    root = lab.args["directory"]
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: root)
    if rollback == "release":
        monkeypatch.setattr(BrowserDeviceRecovery, "_now", lambda _: 0)
    elif rollback == "status":
        # The wall clock can move after release validation but before status.
        # Only this final native snapshot sees the rollback; the gate stays real.
        monkeypatch.setattr(native, "BrowserDeviceRecovery", lambda path, identity:
            BrowserDeviceRecovery(path, identity, clock=lambda: 0))
    calls = []
    original = BrowserDeviceRecovery.handle

    def handle(*args):
        calls.append("runtime-status-write-path")
        return original(*args)

    monkeypatch.setattr(BrowserDeviceRecovery, "handle", handle)
    with _launch_lock(root, create=False):
        private(root / "SingletonLock", b"fictional running browser")
        before = state(lab, completed)
        if rollback != "release":
            worker.worker_context(lab.configuration, selected, None)
        destination = io.BytesIO()
        body = {"version": 1, "action": "worker-request", "build": worker.worker_graph()[0],
                "request": {"version": 1, "action": "status"}}
        assert native._native_request(lab.args["profile"], [lab.configuration.extension_origin],
            io.BytesIO(frame(body)), destination, expected_identity=lab.configuration.identity,
            worker=selected) == 0
        response = json.loads(destination.getvalue()[4:])
        if rollback == "release":
            # Reconciliation confirmation deliberately refuses a clock older
            # than its evidence. Do not relax that gate or repair the clock.
            assert response == {"version": 1, "ok": False, "mode": "setup_error"}
        else:
            assert response["ok"] is True and response["mode"] == "paused"
            assert response["revision"] == lab.ledger.inspect().revision
        assert "session" not in response and not calls
        assert state(lab, completed) == before
        monkeypatch.undo()
        monkeypatch.setattr(worker, "_browser_directory", lambda *_: root)
        worker.worker_context(lab.configuration, selected, None)


def test_continuation_inspection_is_read_only_and_grants_no_authority(lab, completed, monkeypatch):
    from sds200 import browser_device_native as native
    from sds200.browser_device_continuation import BrowserContinuationInspection

    released = attempt(completed)
    calls = []
    monkeypatch.setattr(native, "_post_browser_device", lambda *args: calls.append(args))
    before = state(lab, completed)
    inspection = BrowserContinuationInspection(completed, release_id=released.release_id)
    selected = inspection.review()
    assert selected.identity == lab.configuration.identity and selected.mode is RecoveryMode.PAUSED
    assert selected.native_revision == released.revision
    assert inspection.confirm(selected) is selected
    assert not any(hasattr(inspection, name) for name in ("apply", "execute", "resume", "grant"))
    assert lab.configuration.origin not in repr(selected)
    assert selected.release_id not in repr(selected)
    assert state(lab, completed) == before and calls == []
    release.BrowserPausedGuardRelease(completed).confirm(release_id=released.release_id)


@pytest.mark.parametrize("change", ["missing-release", "wrong-release", "running-browser",
                                    "busy-launcher", "busy-profile", "changed-guard"])
def test_continuation_preflight_refuses_unsafe_installation(lab, completed, change):
    from sds200.browser_device_continuation import (
        BrowserContinuationError,
        BrowserContinuationInspection,
    )
    released = attempt(completed) if change != "missing-release" else None
    chosen = released.release_id if released and change != "wrong-release" else "0" * 64
    inspection = BrowserContinuationInspection(completed, release_id=chosen)
    root = lab.args["directory"]
    if change == "running-browser":
        (root / "SingletonLock").symlink_to("fictional-pid")
    if change == "changed-guard":
        private(root / MAINTENANCE_MARKER, b"changed")
    before = state(lab, completed)
    from contextlib import nullcontext
    scope = (_launch_lock(root, create=False) if change == "busy-launcher" else
             browser_profile_access(lab.args["profile"], exclusive=True)
             if change == "busy-profile" else nullcontext())
    with scope, pytest.raises(BrowserContinuationError):
        inspection.review()
    assert state(lab, completed) == before


@pytest.mark.parametrize("change", ["copied-review", "other-inspector", "repeat-review",
                                    "expired", "wall-rollback", "monotonic-rollback", "nan",
                                    "new-revision", "changed-guard"])
def test_continuation_checkpoint_cannot_be_reused_as_a_stale_permission(lab, completed, change):
    import time
    from dataclasses import replace

    from sds200.browser_device_continuation import (
        BrowserContinuationError,
        BrowserContinuationInspection,
    )

    released = attempt(completed)
    wall, elapsed = [time.time()], [10.0]
    inspection = BrowserContinuationInspection(completed, release_id=released.release_id,
        clock=lambda: wall[0], monotonic=lambda: elapsed[0])
    selected = inspection.review()
    if change == "copied-review":
        selected = replace(selected)
    elif change == "other-inspector":
        inspection = BrowserContinuationInspection(completed, release_id=released.release_id)
    elif change == "expired":
        elapsed[0] += 120
    elif change == "wall-rollback":
        wall[0] -= 1
    elif change == "monotonic-rollback":
        elapsed[0] -= 1
    elif change == "nan":
        wall[0] = float("nan")
    elif change == "new-revision":
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    elif change == "changed-guard":
        private(lab.args["directory"] / MAINTENANCE_MARKER, b"changed")
    before = state(lab, completed)
    with pytest.raises(BrowserContinuationError):
        inspection.review() if change == "repeat-review" else inspection.confirm(selected)
    assert state(lab, completed) == before
