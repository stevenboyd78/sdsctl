"""Fresh local intent, retained evidence, real process-loss boundaries; no login."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing, nullcontext

import pytest

from sds200 import browser_device_continuation_intent as intent
from sds200 import browser_device_worker as worker
from sds200.browser_device_continuation import (
    BrowserContinuationError,
    BrowserContinuationInspection,
)
from sds200.browser_device_guard_release import BrowserPausedGuardRelease
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import _launch_lock, check_browser_startup
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_guard_release import attempt, blocked, state
from tests.test_browser_device_guard_release import completed as completed
from tests.test_browser_device_launch import staged as staged
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux supervised continuation intent")


@pytest.fixture
def released(completed):
    return attempt(completed)


def writer(completed, released, **kwargs):
    return intent.BrowserContinuationIntent(completed, release_id=released.release_id, **kwargs)


def record(completed, released, callback=lambda r: r.confirmation, **kwargs):
    return writer(completed, released, **kwargs).apply(confirmation=callback)


def test_intent_is_durable_local_consent_not_activation(lab, completed, released, monkeypatch):
    from sds200 import browser_device_native as native

    monkeypatch.setattr(native, "_post_browser_device",
                        lambda *a, **k: pytest.fail("Intent must not contact a server"))
    before = state(lab, completed)
    old_ack = completed.confirm(restored=True)
    advisory = BrowserContinuationInspection(completed, release_id=released.release_id)
    checkpoint = advisory.review()
    seen = []

    def consent(review):
        seen.append(review)
        assert review.native_revision == released.revision
        assert review.purpose == "enable-fresh-resume-review"
        assert review.origin == lab.configuration.origin
        assert review.device_id == lab.configuration.device_id
        assert review.release_id == released.release_id
        assert review.directory == lab.args["directory"]
        assert review.profile == lab.args["profile"]
        assert all(secret not in repr(review) for secret in (
            review.confirmation, review.intent_id, review.origin,
            str(review.directory), CREDENTIAL))
        assert state(lab, completed) == before
        with pytest.raises(BlockingIOError), _launch_lock(review.directory, create=False):
            pytest.fail("Competing launch allowed")
        with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                review.profile, exclusive=True):
            pytest.fail("Competing private-input writer allowed")
        with (closing(sqlite3.connect(review.profile / "recovery.sqlite", timeout=0)) as db,
              pytest.raises(sqlite3.OperationalError)):
            db.execute("BEGIN IMMEDIATE")
        return review.confirmation

    result = record(completed, released, consent)
    assert result.native_revision == released.revision and result.mode is RecoveryMode.PAUSED
    assert result.purpose == "enable-fresh-resume-review"
    assert result.intent_id == seen[0].intent_id and len(seen) == 1
    after = state(lab, completed)
    directory = after.pop("directory")
    assert after == {k: v for k, v in before.items() if k != "directory"}
    assert {k: v for k, v in directory.items() if k != intent.INTENT_JOURNAL} == before["directory"]
    path = lab.args["directory"] / intent.INTENT_JOURNAL
    assert path.stat().st_mode & 0o777 == 0o600
    assert CREDENTIAL.encode() not in path.read_bytes()
    assert seen[0].confirmation.encode() not in path.read_bytes()
    assert completed.confirm(restored=True) == old_ack
    assert BrowserPausedGuardRelease(completed).confirm(release_id=released.release_id) == released
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert lab.ledger.inspect().revision == released.revision
    saved = state(lab, completed)
    # Confirmation works after expiry but grants nothing; it is not execution.
    session = writer(completed, released, clock=lambda: float("nan"))
    assert session.confirm(intent_id=result.intent_id) == result
    for wrong in ("0" * 64, "", True):
        with pytest.raises(intent.BrowserContinuationIntentError):
            session.confirm(intent_id=wrong)
    with pytest.raises(intent.BrowserContinuationIntentError):
        record(completed, released, lambda _: pytest.fail("Existing intent must not prompt"))
    with pytest.raises(BrowserContinuationError):
        advisory.confirm(checkpoint)
    blocked(lab)
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: lab.args["directory"])
    with _launch_lock(lab.args["directory"], create=False), pytest.raises(ValueError):
        worker.normal_worker_paused_only(lab.configuration, selected)
    assert state(lab, completed) == saved


@pytest.mark.parametrize("answer", [None, "yes", True, "", "advisory-checkpoint"])
def test_cancel_wrong_answer_and_advisory_are_not_consent(lab, completed, released, answer):
    if answer == "advisory-checkpoint":
        answer = BrowserContinuationInspection(completed, release_id=released.release_id).review()
    before = state(lab, completed)
    session = writer(completed, released)
    if answer is None:
        assert session.apply(confirmation=lambda _: answer) is None
    else:
        with pytest.raises(intent.BrowserContinuationIntentError):
            session.apply(confirmation=lambda _: answer)
    with pytest.raises(intent.BrowserContinuationIntentError):
        session.apply(confirmation=lambda r: r.confirmation)
    assert state(lab, completed) == before
    check_browser_startup(**lab.inputs)


@pytest.mark.parametrize("change", ["wall-expiry", "wall-backward", "elapsed-expiry",
                                    "elapsed-backward", "nan", "bool", "exception"])
def test_stale_or_invalid_consent_has_no_record(lab, completed, released, change):
    wall, elapsed = [1000.0], [10.0]
    before = state(lab, completed)

    def consent(review):
        if change == "wall-expiry":
            wall[0] += 120
        elif change == "wall-backward":
            wall[0] -= 1
        elif change == "elapsed-expiry":
            elapsed[0] += 120
        elif change == "elapsed-backward":
            elapsed[0] -= 1
        elif change == "nan":
            wall[0] = float("nan")
        elif change == "bool":
            elapsed[0] = True
        else:
            raise RuntimeError(CREDENTIAL)
        return review.confirmation

    with pytest.raises(intent.BrowserContinuationIntentError) as error:
        record(completed, released, consent, clock=lambda: wall[0], monotonic=lambda: elapsed[0])
    assert CREDENTIAL not in str(error.value)
    assert state(lab, completed) == before
    check_browser_startup(**lab.inputs)


@pytest.mark.parametrize("owner", ["launch", "profile", "native", "singleton", "wrong-release"])
def test_busy_or_wrong_installation_rejects_before_consent(lab, completed, released, owner):
    root, profile = lab.args["directory"], lab.args["profile"]
    lock = (_launch_lock(root, create=False) if owner == "launch" else
            browser_profile_access(profile, exclusive=True) if owner == "profile" else
            closing(sqlite3.connect(profile / "recovery.sqlite", timeout=0))
            if owner == "native" else nullcontext())
    if owner == "singleton":
        (root / "SingletonLock").symlink_to("fictional-pid")
    session = writer(completed, released) if owner != "wrong-release" else (
        intent.BrowserContinuationIntent(completed, release_id="f" * 64))
    with lock as value:
        if owner == "native":
            value.execute("BEGIN IMMEDIATE")
        before = state(lab, completed)
        with pytest.raises(intent.BrowserContinuationIntentError):
            session.apply(confirmation=lambda _: pytest.fail("Must not prompt"))
        assert state(lab, completed) == before


@pytest.mark.parametrize("when", ["empty", "prepared", "directory-sync", "updated",
                                "commit-before", "commit-after", "final-read"])
def test_process_loss_never_replays_or_activates(lab, completed, released, monkeypatch, when):
    initial = {k: v for k, v in state(lab, completed).items() if k != "directory"}
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_prepare, original_complete, original_connect = (
            intent._prepare, intent._complete, intent._connect)

        def consent(review):
            os.write(write_fd, review.intent_id.encode("ascii"))
            return review.confirmation

        def prepare(*args):
            if when == "empty":
                os._exit(72)
            original_prepare(*args)
            if when == "prepared":
                os._exit(72)

        class Connection:
            def __init__(self, db):
                self.db, self.updated = db, False

            def execute(self, statement, *args):
                result = self.db.execute(statement, *args)
                if statement.startswith("UPDATE intent"):
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
            monkeypatch.setattr(intent, "_connect", lambda *a, **k: Connection(
                original_connect(*a, **k)))
            original_complete(*args)

        monkeypatch.setattr(intent, "_prepare", prepare)
        monkeypatch.setattr(intent, "_complete", complete)
        if when == "directory-sync":
            monkeypatch.setattr(intent.os, "fsync", lambda _: os._exit(72))
        if when == "final-read":
            monkeypatch.setattr(intent, "_confirmed", lambda *a, **k: os._exit(72))
        try:
            record(completed, released, consent)
        except BaseException:
            os._exit(73)
        os._exit(74)
    os.close(write_fd)
    try:
        identifier = os.read(read_fd, 65).decode("ascii")
        _, status = os.waitpid(child, 0)
    finally:
        os.close(read_fd)
    assert os.waitstatus_to_exitcode(status) == 72 and len(identifier) == 64
    assert {k: v for k, v in state(lab, completed).items() if k != "directory"} == initial
    saved = state(lab, completed)
    if when in {"commit-after", "final-read"}:
        assert writer(completed, released).confirm(intent_id=identifier).mode is RecoveryMode.PAUSED
    else:
        with pytest.raises(intent.BrowserContinuationIntentError):
            writer(completed, released).confirm(intent_id=identifier)
    with pytest.raises(intent.BrowserContinuationIntentError):
        record(completed, released, lambda _: pytest.fail("Must never replay"))
    blocked(lab)  # Includes a fully committed record: still NOT activation.
    assert state(lab, completed) == saved


@pytest.mark.parametrize("change", ["guard-inode", "release-inode", "client", "ack", "credential"])
def test_evidence_changed_during_consent_creates_nothing(lab, completed, released, change):
    from sds200.browser_device_guard_release import RELEASE_JOURNAL
    from sds200.browser_device_registration import MAINTENANCE_MARKER

    root = lab.args["directory"]
    paths = {"guard-inode": root / MAINTENANCE_MARKER, "release-inode": root / RELEASE_JOURNAL,
             "client": lab.args["profile"] / "client.json",
             "credential": lab.args["profile"] / "device.secret",
             "ack": completed._root / "browser-acknowledgement.json"}

    def consent(review):
        path = paths[change]
        body = path.read_bytes()
        path.rename(path.with_name(path.name + ".retained"))
        if change == "credential":
            # Syntactically valid replacement: not merely a parse rejection.
            private(path, b"sdsctl-browser-v1." + b"9" * 64 + b"\n")
        else:
            private(path, body if change.endswith("inode") else body + b"changed")
        return review.confirmation

    with pytest.raises(intent.BrowserContinuationIntentError):
        record(completed, released, consent)
    assert not intent.has_continuation_intent(root)
    blocked(lab)


@pytest.mark.parametrize("change", ["expiry", "intent-inode", "body", "sync-failure"])
def test_prepared_state_is_rechecked_before_commit(lab, completed, released, monkeypatch, change):
    original = intent._prepare
    elapsed = [10.0]

    def prepare(path, body):
        original(path, body)
        if change == "expiry":
            elapsed[0] += 120
        elif change == "intent-inode":
            original_body = path.read_bytes()
            path.rename(path.with_name(path.name + ".retained"))
            private(path, original_body)
        elif change == "body":
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE intent SET body=?", (b"{}",))
                db.commit()

    def sync_fail(_):
        raise OSError(CREDENTIAL)

    monkeypatch.setattr(intent, "_prepare", prepare)
    if change == "sync-failure":
        monkeypatch.setattr(intent.os, "fsync", sync_fail)
    with pytest.raises(intent.BrowserContinuationIntentError):
        record(completed, released, monotonic=lambda: elapsed[0])
    assert intent.has_continuation_intent(lab.args["directory"])
    blocked(lab)
    path = lab.args["directory"] / intent.INTENT_JOURNAL
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        assert db.execute("SELECT phase FROM intent").fetchone() == ("prepared",)


@pytest.mark.parametrize("change", ["intent-inode", "sidecar", "new-revision", "schema",
                                    "phase", "typed-revision", "duplicate-json", "extra-field",
                                    "consent-digest", "expired-approval", "broader-purpose"])
def test_uncertain_or_changed_record_never_confirms_or_activates(lab, completed, released, change):
    result = record(completed, released)
    path = lab.args["directory"] / intent.INTENT_JOURNAL
    if change == "intent-inode":
        data = path.read_bytes()
        path.rename(path.with_name(path.name + ".retained"))
        private(path, data)
    elif change == "sidecar":
        path.with_name(path.name + "-journal").symlink_to("missing")
    elif change == "new-revision":
        lab.ledger.resume(lab.ledger.inspect().revision)
        lab.ledger.suspend()
    else:
        with closing(sqlite3.connect(path)) as db:
            if change == "schema":
                db.execute("CREATE TABLE unexpected (value TEXT)")
            elif change == "phase":
                db.execute("UPDATE intent SET phase='prepared'")
            else:
                raw = db.execute("SELECT body FROM intent").fetchone()[0]
                body = json.loads(raw)
                if change == "typed-revision":
                    body["evidence"]["revision"] = float(body["evidence"]["revision"])
                elif change == "extra-field":
                    body["activated"] = True
                elif change == "consent-digest":
                    body["consent_sha256"] = "bad"
                elif change == "expired-approval":
                    body["approved_at"] = body["expires_at"]
                elif change == "broader-purpose":
                    body["purpose"] = "automatic-sign-in"
                raw = intent._json(body)
                if change == "duplicate-json":
                    raw = b'{"version":1,' + raw[1:]
                db.execute("UPDATE intent SET body=?", (raw,))
            db.commit()
    saved = state(lab, completed)
    with pytest.raises(intent.BrowserContinuationIntentError):
        writer(completed, released).confirm(intent_id=result.intent_id)
    blocked(lab)
    assert state(lab, completed) == saved


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_presence_blocks_even_unguarded_profile_and_cached_worker(inputs, monkeypatch, suffix):
    import io

    from sds200 import browser_device_native as native
    from sds200.browser_device_native import load_browser_native_configuration
    from tests.test_browser_device_native import frame
    from tests.test_browser_device_profile import snapshot
    from tests.test_browser_device_worker import FAILURE, envelope

    config = load_browser_native_configuration(inputs["profile"])
    selected = worker.BrowserWorkerSelection(inputs["bundle"], inputs["public_key"])
    root = inputs["root"]
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: root)
    with _launch_lock(root):
        assert worker.normal_worker_paused_only(config, selected) is False
        # Appear after startup, without any old release/guard. Presence must not
        # make the cached worker treat this as an ordinary unguarded profile.
        (root / (intent.INTENT_JOURNAL + suffix)).symlink_to("missing")
        with pytest.raises(ValueError):
            worker.normal_worker_paused_only(config, selected)
        before = snapshot(inputs["profile"])
        for action in ("status", "claim-browser", "authenticate", "suspend"):
            destination = io.BytesIO()
            request = envelope("worker-request", request={"version": 1, "action": action})
            assert native._native_request(inputs["profile"], [config.extension_origin],
                io.BytesIO(frame(request)), destination, expected_identity=config.identity,
                worker=selected) == 0
            assert json.loads(destination.getvalue()[4:]) == FAILURE
        assert snapshot(inputs["profile"]) == before
    from sds200.browser_device_startup import BrowserStartupError
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**inputs)
