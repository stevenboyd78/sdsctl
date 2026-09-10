"""Portable owned workflow fixtures with explicitly simulated retained history.

Actual ownership, private files, clocks, SQLite transactions and sync operations
are exercised here. Complete historical-chain integration stays in the required
namespace suite; this fixture is not a substitute for that evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from sds200 import browser_device_continuation_activation as activation
from sds200 import browser_device_continuation_native as native
from sds200.browser_device_continuation_history import BrowserContinuationHistory
from sds200.browser_device_continuation_intent import (
    ACTIVATION_MANIFEST,
    INTENT_JOURNAL,
    has_continuation_intent,
)
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_resume_archive import _encoded
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
from sds200.browser_device_startup import _launch_lock
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume import evidence
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned activation fixtures")
ERROR = activation.BrowserPausedActivationError


@pytest.fixture(params=[1, 2])
def candidate(lab, handoff, monkeypatch, request):
    if request.param == 2:
        lab.native.prepare(expected_revision=lab.ledger.inspect().revision,
            browser_intent="f" * 64, reviewed_server=evidence(lab.configuration))
        lab.ledger.suspend()
    profile = lab.args["profile"]
    with lab.ledger._inspection() as db:
        before = native._snapshot(db, {"schema": request.param,
            "identity": lab.configuration.identity, "profile": str(profile)}, activated=False)
    retained = BrowserContinuationHistory(lab.configuration.identity, lab.configuration.origin,
        lab.configuration.device_id, "a" * 64, "b" * 64, "c" * 64,
        _encoded({**before, "intent": "f" * 64}), before["state"]["revision"])
    boundary = BrowserResumeBoundary(profile, archives=lab.args["archives"])
    binding = boundary._binding()

    class Inputs:
        def __init__(self, owner):
            self.owner = owner

        def recheck(self):
            self.owner.binding(handoff)
            assert boundary._binding() == binding

    def capture(h, owner, **selection):
        assert h is handoff and selection == dict(release_id="a" * 64, intent_id="b" * 64)
        inputs = Inputs(owner)
        inputs.recheck()
        activation._no_sidecars(profile / "recovery.sqlite")
        return retained, inputs

    monkeypatch.setattr(activation, "_capture_owned_history", capture)
    # Not a valid journal: this portable fixture simulates the historical reader,
    # while the ordinary startup path must continue refusing the fixed artifact.
    private(lab.args["directory"] / INTENT_JOURNAL, b"fictional retained intent")
    args = dict(release_id="a" * 64, intent_id="b" * 64,
                clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
    return SimpleNamespace(core=activation.BrowserPausedActivation(handoff, **args),
        fresh=lambda: activation.BrowserPausedActivation(handoff, **args),
        path=lab.args["directory"] / ACTIVATION_MANIFEST, retained=retained)


def accept(review):
    return review.confirmation


def test_exact_local_consent_keeps_pause_and_one_authoritative_commit(lab, candidate, monkeypatch):
    from sds200 import browser_device_native as server

    monkeypatch.setattr(server, "_post_browser_device", lambda *a, **k: pytest.fail("Network"))
    before = snap(lab)
    selected = []

    def consent(review):
        selected.append(review)
        assert snap(lab) == before
        assert review.directory == lab.args["directory"] and review.profile == lab.args["profile"]
        assert review.native_revision == candidate.retained.native_revision
        with pytest.raises(BlockingIOError), _launch_lock(review.directory, create=False):
            pytest.fail("Competing launcher")
        with (pytest.raises(BrowserProfileAccessError),
              browser_profile_access(review.profile, exclusive=True)):
            pytest.fail("Competing private writer")
        with (closing(sqlite3.connect(review.profile / "recovery.sqlite", timeout=0)) as rival,
              pytest.raises(sqlite3.OperationalError)):
            rival.execute("BEGIN IMMEDIATE")
        with pytest.raises(FrozenInstanceError):
            review.native_revision += 1
        assert review.confirmation not in repr(review) and str(review.directory) not in repr(review)
        return review.confirmation

    result = candidate.core.apply(confirmation=consent)
    assert result.mode is RecoveryMode.PAUSED
    assert result.native_revision == selected[0].native_revision + 1
    assert result.epoch == selected[0].epoch
    assert candidate.path.stat().st_mode & 0o777 == 0o600
    raw = candidate.path.read_bytes()
    assert result.manifest_sha256 == hashlib.sha256(raw).hexdigest()
    assert CREDENTIAL.encode() not in raw and selected[0].confirmation.encode() not in raw
    assert json.loads(raw)["consent_sha256"] == hashlib.sha256(
        selected[0].confirmation.encode()).hexdigest()
    for secret in (result.epoch, result.identity, result.manifest_sha256):
        assert secret not in repr(result)
    baseline = snap(lab)
    lab.clock[0] += 10000
    assert candidate.fresh().confirm(epoch=result.epoch) == result
    assert snap(lab) == baseline
    blocked(lab)
    with pytest.raises(ERROR):
        candidate.fresh().apply(confirmation=lambda _: pytest.fail("Replay offered consent"))


@pytest.mark.parametrize("answer", [None, "wrong", True])
def test_cancel_or_wrong_consent_creates_nothing_and_instance_is_one_use(lab, candidate, answer):
    before = snap(lab)
    if answer is None:
        assert candidate.core.apply(confirmation=lambda _: answer) is None
    else:
        with pytest.raises(ERROR):
            candidate.core.apply(confirmation=lambda _: answer)
    assert snap(lab) == before and not candidate.path.exists()
    with pytest.raises(ERROR):
        candidate.core.apply(confirmation=accept)


@pytest.mark.parametrize("change", ["wall-expired", "monotonic-expired", "wall-backward",
                                    "monotonic-backward", "nan", "credential"])
def test_stale_consent_and_changed_private_input_refuse_before_manifest(lab, candidate, change):
    native_before = lab.ledger.path.read_bytes()

    def consent(review):
        if change == "wall-expired":
            lab.clock[0] += 120
        elif change == "monotonic-expired":
            lab.elapsed[0] += 120
        elif change == "wall-backward":
            lab.clock[0] -= 1
        elif change == "monotonic-backward":
            lab.elapsed[0] -= 1
        elif change == "nan":
            lab.clock[0] = float("nan")
        else:
            private(lab.args["profile"] / "device.secret", b"changed private input")
        return review.confirmation

    with pytest.raises(ERROR):
        candidate.core.apply(confirmation=consent)
    assert lab.ledger.path.read_bytes() == native_before
    assert not candidate.path.exists()


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
@pytest.mark.parametrize("kind", ["file", "dangling", "directory"])
def test_existing_artifact_blocks_before_consent_and_is_never_adopted(
        lab, candidate, suffix, kind):
    path = candidate.path.with_name(candidate.path.name + suffix)
    if kind == "file":
        private(path, b"uncertain")
    elif kind == "dangling":
        path.symlink_to("missing-target")
    else:
        path.mkdir(mode=0o700)
    before = lab.ledger.path.read_bytes()
    assert has_continuation_intent(path.parent)
    with pytest.raises(ERROR):
        candidate.core.apply(confirmation=lambda _: pytest.fail("Existing artifact consent"))
    assert lab.ledger.path.read_bytes() == before
    assert path.lstat()


@pytest.mark.parametrize("stage", ["create", "file-sync", "directory-sync", "after-create",
                                   "native-before", "native-after", "late-input", "late-expiry"])
def test_interrupted_precommit_stages_keep_native_bytes_and_retain_partial_manifest(
        lab, candidate, monkeypatch, stage):
    before = lab.ledger.path.read_bytes()
    create, fsync = activation._create_manifest, os.fsync
    native_stage = native._stage_paused_activation

    def create_fault(*args):
        if stage == "create":
            raise OSError("PRIVATE create failure")
        create(*args)
        raise OSError("PRIVATE after-create failure")

    calls = 0

    def sync_fault(fd):
        nonlocal calls
        calls += 1
        if calls == (1 if stage == "file-sync" else 2):
            raise OSError("PRIVATE sync failure")
        fsync(fd)

    def stage_fault(*args, **kwargs):
        if stage == "native-before":
            raise RuntimeError("PRIVATE before native")
        native_stage(*args, **kwargs)
        if stage == "native-after":
            raise RuntimeError("PRIVATE after native")
        if stage == "late-input":
            private(lab.args["profile"] / "device.secret", b"changed after native DML")
        if stage == "late-expiry":
            lab.elapsed[0] += 120

    if stage in {"create", "after-create"}:
        monkeypatch.setattr(activation, "_create_manifest", create_fault)
    elif stage.endswith("sync"):
        monkeypatch.setattr(os, "fsync", sync_fault)
    else:
        monkeypatch.setattr(native, "_stage_paused_activation", stage_fault)
    with pytest.raises(ERROR) as error:
        candidate.core.apply(confirmation=accept)
    assert "PRIVATE" not in str(error.value) and CREDENTIAL not in str(error.value)
    assert lab.ledger.path.read_bytes() == before
    assert candidate.path.exists() == (stage != "create")
    if candidate.path.exists():
        with pytest.raises(ERROR):
            candidate.fresh().apply(confirmation=lambda _: pytest.fail("Retried partial state"))


@pytest.mark.parametrize("committed", [False, True])
def test_commit_failure_or_lost_reply_is_resolved_only_by_readonly_confirmation(
        lab, candidate, monkeypatch, committed):
    connect = sqlite3.connect

    class UncertainCommit(sqlite3.Connection):
        def commit(self):
            if committed:
                super().commit()
            raise sqlite3.OperationalError("PRIVATE uncertain commit")

    def selected(*args, **kwargs):
        return connect(*args, **kwargs, factory=UncertainCommit)

    seen = []
    before = lab.ledger.path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", selected)
        with pytest.raises(ERROR):
            candidate.core.apply(confirmation=lambda r: seen.append(r) or r.confirmation)
    after = snap(lab)
    assert candidate.path.exists()
    if committed:
        result = candidate.fresh().confirm(epoch=seen[0].epoch)
        assert result.native_revision == seen[0].native_revision + 1
    else:
        assert lab.ledger.path.read_bytes() == before
        with pytest.raises(ERROR):
            candidate.fresh().confirm(epoch=seen[0].epoch)
    assert snap(lab) == after


@pytest.mark.parametrize("change", ["missing", "mode", "inode", "bytes", "epoch", "revision"])
def test_confirmation_is_exact_and_does_not_repair(lab, candidate, change):
    result = candidate.core.apply(confirmation=accept)
    path = candidate.path
    if change == "missing":
        path.rename(path.with_name("retained-manifest"))
    elif change == "mode":
        path.chmod(0o644)
    elif change == "inode":
        raw = path.read_bytes()
        path.rename(path.with_name("retained-manifest"))
        private(path, raw)
    elif change == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "revision":
        with closing(sqlite3.connect(lab.ledger.path)) as db:
            db.execute("UPDATE recovery SET revision=revision+1")
            db.commit()
    before = snap(lab)
    with pytest.raises(ERROR):
        candidate.fresh().confirm(epoch="f" * 64 if change == "epoch" else result.epoch)
    assert snap(lab) == before


def test_wal_header_is_refused_without_changing_mode(lab, candidate):
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(ERROR):
        candidate.core.apply(confirmation=lambda _: pytest.fail("WAL consent"))
    assert lab.ledger.path.read_bytes() == before and not candidate.path.exists()


def test_activation_presence_alone_is_a_stop_marker(lab, candidate):
    (candidate.path.parent / INTENT_JOURNAL).rename(candidate.path.parent / "retained-intent")
    private(candidate.path, b"prepared only")
    assert has_continuation_intent(candidate.path.parent)
    blocked(lab)


@pytest.mark.parametrize("change", ["manifest-bytes", "manifest-inode", "manifest-mode",
                                    "manifest-sidecar", "ledger-mode", "ledger-wal"])
def test_late_file_change_rolls_back_and_never_confirms(lab, candidate, monkeypatch, change):
    before = lab.ledger.path.read_bytes()
    stage = native._stage_paused_activation

    def staged(*args, **kwargs):
        stage(*args, **kwargs)
        if change == "manifest-bytes":
            candidate.path.write_bytes(candidate.path.read_bytes() + b" ")
        elif change == "manifest-inode":
            raw = candidate.path.read_bytes()
            candidate.path.rename(candidate.path.with_name("retained-manifest"))
            private(candidate.path, raw)
        elif change == "manifest-mode":
            candidate.path.chmod(0o644)
        elif change == "manifest-sidecar":
            candidate.path.with_name(candidate.path.name + "-journal").symlink_to("missing")
        elif change == "ledger-mode":
            lab.ledger.path.chmod(0o644)
        else:
            private(lab.ledger.path.with_name("recovery.sqlite-wal"), b"uncertain")

    monkeypatch.setattr(native, "_stage_paused_activation", staged)
    with pytest.raises(ERROR):
        candidate.core.apply(confirmation=accept)
    assert lab.ledger.path.read_bytes() == before
    before = snap(lab)
    epoch = json.loads(candidate.path.read_bytes())["epoch"]
    with pytest.raises(ERROR):
        candidate.fresh().confirm(epoch=epoch)
    assert snap(lab) == before


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_process_interruption_after_native_stage_cannot_commit(
        lab, candidate, monkeypatch, exception):
    before = lab.ledger.path.read_bytes()
    stage = native._stage_paused_activation

    def staged(*args, **kwargs):
        stage(*args, **kwargs)
        raise exception()

    monkeypatch.setattr(native, "_stage_paused_activation", staged)
    with pytest.raises(exception):
        candidate.core.apply(confirmation=accept)
    assert candidate.path.exists() and lab.ledger.path.read_bytes() == before
    epoch = json.loads(candidate.path.read_bytes())["epoch"]
    with pytest.raises(ERROR):
        candidate.fresh().confirm(epoch=epoch)
