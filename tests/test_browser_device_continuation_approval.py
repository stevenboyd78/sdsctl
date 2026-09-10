"""Live-owner prepare/claim fixtures, not browser consent or online acceptance."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from contextlib import closing, contextmanager, nullcontext
from dataclasses import FrozenInstanceError, replace

import pytest

from sds200 import browser_device_continuation_approval as approval
from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_ownership as ownership
from sds200.browser_device_profile import _trust
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_worker import BrowserWorkerSelection
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_current import (
    activation_candidate as activation_candidate,
)
from tests.test_browser_device_continuation_current import candidate as candidate
from tests.test_browser_device_continuation_current import native_step
from tests.test_browser_device_continuation_native import transaction
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned approval fixtures")
ERROR = approval.BrowserContinuationApprovalError
INTENT = "e" * 64
NONCE = "c" * 64


@pytest.fixture
def access(lab, candidate, monkeypatch):
    lab.clock[0] += 2
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])

    @contextmanager
    def attempt():
        obj = approval._BrowserWorkerApproval(lab.configuration, selection,
            clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
        with _launch_lock(candidate.root, create=False):
            yield obj

    return attempt


def prepare(obj, expected, **kwargs):
    return obj.prepare(expected, **{**dict(intent=INTENT, reviewed_generation=7,
                                          consent=lambda review: review), **kwargs})


def expected(candidate):
    return current.inspect_stopped_continuation(candidate.root, **candidate.args)


def unrelated(lab):
    result = snap(lab)
    result["profile"].pop("recovery.sqlite")
    return result


@pytest.mark.parametrize("delay", [0, 1, 119.9])
def test_owned_prepare_then_claim_pins_actual_inputs_without_grant_or_network(
        lab, candidate, access, monkeypatch, delay):
    from sds200 import browser_device_native as native

    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network"))
    monkeypatch.setattr(approval.secrets, "token_hex", lambda n: NONCE)
    state = expected(candidate)
    before = unrelated(lab)
    commits = []
    original = approval._commit

    def commit(db):
        commits.append(db)
        assert db.in_transaction
        original(db)

    monkeypatch.setattr(approval, "_commit", commit)
    with access() as obj:
        prepared = prepare(obj, state)
        assert prepared.phase == "prepared" and prepared.state.mode is RecoveryMode.PAUSED
        assert prepared.state.native_revision == state.native_revision + 1
        assert obj.confirm() == prepared
        lab.clock[0] += delay
        lab.elapsed[0] += delay
        claimed = obj.claim()
        assert claimed.phase == "claimed" and claimed.state.mode is RecoveryMode.PAUSED
        assert claimed.state.native_revision == prepared.state.native_revision
        assert claimed.state.state_fingerprint != prepared.state.state_fingerprint
        stable = snap(lab)
        assert obj.confirm() == claimed and snap(lab) == stable
        assert len(commits) == 2 and unrelated(lab) == before
        for value in (CREDENTIAL, NONCE, INTENT, str(candidate.root), state.identity, state.epoch):
            assert value not in repr(obj) + repr(prepared) + repr(claimed)
        with pytest.raises(FrozenInstanceError):
            claimed.phase = "prepared"
        with pytest.raises(ERROR):
            obj.claim()
        with pytest.raises(ERROR):
            prepare(obj, claimed.state)
        assert snap(lab) == stable and len(commits) == 2
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        row = db.execute("SELECT digest,phase,intent,generation,credential_hash,trust_hash,"
                         "expires-created FROM browser_epoch_approval").fetchone()
        assert row == (hashlib.sha256(NONCE.encode()).hexdigest(), "claimed", INTENT, 7,
            hashlib.sha256(candidate.paths["credential"].read_bytes()).hexdigest(),
            _trust(candidate.paths["trust"].read_bytes()), 120)
        assert db.execute("SELECT active_grant FROM browser_epoch_state").fetchone() == (None,)
    assert NONCE.encode() not in lab.ledger.path.read_bytes()
    assert CREDENTIAL.encode() not in lab.ledger.path.read_bytes()
    blocked(lab)


def test_fresh_callback_has_no_sql_transaction_and_is_not_a_serialized_receipt(
        lab, candidate, access):
    state = expected(candidate)
    before = snap(lab)
    seen = []

    def consent(review):
        seen.append(review)
        assert review.state == state and review.intent == INTENT and review.reviewed_generation == 7
        assert snap(lab) == before
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")  # Callback is outside the native transaction.
            db.rollback()
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing launcher")
        with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                lab.args["profile"], exclusive=True):
            pytest.fail("Competing input writer")
        with pytest.raises(FrozenInstanceError):
            review.intent = "f" * 64
        assert INTENT not in repr(review)
        return review

    with access() as obj:
        prepare(obj, state, consent=consent)
        assert len(seen) == 1
    state = expected(candidate)
    with access() as obj:
        before = snap(lab)
        with pytest.raises(ERROR):
            prepare(obj, state, consent=lambda review: seen[0])
        assert snap(lab) == before


@pytest.mark.parametrize("response", ["none", "bool", "copy", "exception", "interrupt"])
def test_refused_or_interrupted_review_is_one_use_without_writes(
        lab, candidate, access, response):
    state = expected(candidate)
    before = snap(lab)

    def consent(review):
        if response == "exception":
            raise RuntimeError("PRIVATE consent")
        if response == "interrupt":
            raise KeyboardInterrupt("PRIVATE consent")
        return {"none": None, "bool": True, "copy": replace(review)}[response]

    with access() as obj:
        with pytest.raises(KeyboardInterrupt if response == "interrupt" else ERROR):
            prepare(obj, state, consent=consent)
        with pytest.raises(ERROR):
            prepare(obj, state)
        with pytest.raises(ERROR):
            obj.claim()
        with pytest.raises(ERROR):
            obj.confirm()
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["identity", "origin", "device_id", "epoch", "manifest_sha256",
    "state_fingerprint", "native_revision", "bool-revision", "mode", "type", "active",
    "generation-zero", "generation-bool", "generation-huge", "intent", "callback"])
def test_invalid_or_stale_inputs_do_not_reach_consent(lab, candidate, access, change):
    state = expected(candidate)
    kwargs = dict(consent=lambda _: pytest.fail("Invalid review was exposed"))
    if change == "type":
        state = candidate.history
    elif change == "mode":
        state = replace(state, mode="paused")
    elif change == "active":
        state = replace(state, mode=RecoveryMode.ACTIVE)
    elif change in {"native_revision", "bool-revision"}:
        state = replace(state, native_revision=True if change == "bool-revision" else 1000)
    elif change.startswith("generation"):
        kwargs["reviewed_generation"] = {"generation-zero": 0,
            "generation-bool": True, "generation-huge": 2**53}[change]
    elif change == "intent":
        kwargs["intent"] = "not an intent"
    elif change == "callback":
        kwargs["consent"] = None
    else:
        state = replace(state, **{change: "f" * 64})
    before = snap(lab)
    with access() as obj, pytest.raises(ERROR):
        prepare(obj, state, **kwargs)
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["credential", "trust", "bundle", "manifest", "release",
                                    "intent", "ledger", "pause", "clock", "owner"])
def test_change_during_callback_refuses_prepare(lab, candidate, access, change):
    state = expected(candidate)
    before = lab.ledger.path.read_bytes()

    def consent(review):
        if change == "pause":
            native_step(lab, candidate, "pause")
        elif change == "clock":
            lab.elapsed[0] += 10
        elif change == "owner":
            candidate.captures[-1][1]._active = False
        elif change == "ledger":
            path = lab.ledger.path
            path.rename(path.with_name(path.name + ".retained"))
            private(path, before)
        else:
            path = candidate.paths[change]
            path.write_bytes(path.read_bytes() + b" ")
        return review

    with access() as obj:
        with pytest.raises(ERROR):
            prepare(obj, state, consent=consent)
        with pytest.raises(ERROR):
            obj.claim()
    if change != "pause":
        assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("phase", ["prepared", "claimed"])
@pytest.mark.parametrize("change", ["credential", "trust", "manifest", "bundle", "native-state",
    "ledger-mode", "owner", "process", "wall-backstep", "wall-expiry", "mono-backstep",
    "mono-expiry", "nan", "bool", "exception", "interrupt", "exit"])
def test_post_dml_failure_rolls_back_everything(lab, candidate, access, monkeypatch, phase, change):
    state = expected(candidate)
    with access() as obj:
        if phase == "claimed":
            prepare(obj, state)
        before = lab.ledger.path.read_bytes()
        name = "_stage_prepare" if phase == "prepared" else "_stage_claim"
        stage = getattr(epoch, name)

        def changed(*args, **kwargs):
            result = stage(*args, **kwargs)
            if change in {"credential", "trust", "manifest", "bundle"}:
                path = candidate.paths[change]
                path.write_bytes(path.read_bytes() + b" ")
            elif change == "native-state":
                args[0].execute("UPDATE recovery SET revision=revision+1")
            elif change == "ledger-mode":
                lab.ledger.path.chmod(0o644)
            elif change == "owner":
                candidate.captures[-1][1]._active = False
            elif change == "process":
                monkeypatch.setattr(approval.os, "getppid", lambda: -1)
            elif change in {"exception", "interrupt", "exit"}:
                raise {"exception": RuntimeError, "interrupt": KeyboardInterrupt,
                       "exit": SystemExit}[change]("PRIVATE interrupted")
            else:
                target = lab.elapsed if change.startswith("mono") else lab.clock
                target[0] = (float("nan") if change == "nan" else True if change == "bool"
                             else target[0] + (10 if change.endswith("expiry") else -1))
            return result

        with monkeypatch.context() as patch:
            patch.setattr(epoch, name, changed)
            error_type = {"interrupt": KeyboardInterrupt, "exit": SystemExit}.get(change, ERROR)
            with pytest.raises(error_type):
                prepare(obj, state) if phase == "prepared" else obj.claim()
        assert lab.ledger.path.read_bytes() == before
        with pytest.raises(ERROR):
            obj.claim()


@pytest.mark.parametrize("phase", ["prepared", "claimed"])
@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_commit_readback_never_enables_replay_or_claim(
        lab, candidate, access, monkeypatch, phase, committed):
    state = expected(candidate)
    with access() as obj:
        if phase == "claimed":
            prepare(obj, state)
        before = lab.ledger.path.read_bytes()

        def uncertain(db):
            if committed:
                db.commit()
            raise sqlite3.OperationalError("PRIVATE uncertain reply")

        with monkeypatch.context() as patch:
            patch.setattr(approval, "_commit", uncertain)
            with pytest.raises(ERROR):
                prepare(obj, state) if phase == "prepared" else obj.claim()
        stable = snap(lab)
        if committed:
            assert obj.confirm().phase == phase
        else:
            assert lab.ledger.path.read_bytes() == before
            with pytest.raises(ERROR):
                obj.confirm()
        with pytest.raises(ERROR):
            obj.claim()
        with pytest.raises(ERROR):
            prepare(obj, state)
        assert snap(lab) == stable


@pytest.mark.parametrize("phase", ["prepared", "claimed"])
@pytest.mark.parametrize("failure", ["reply", "wall-deadline", "mono-deadline", "interrupt"])
def test_post_commit_readback_failure_is_not_repaired_into_permission(
        lab, candidate, access, monkeypatch, phase, failure):
    state = expected(candidate)
    with access() as obj:
        if phase == "claimed":
            prepare(obj, state)
        original = obj._confirm_owned

        def failed(*args):
            result = original(*args)
            if failure == "reply":
                raise RuntimeError("PRIVATE readback")
            if failure == "interrupt":
                raise KeyboardInterrupt("PRIVATE readback")
            (lab.clock if failure == "wall-deadline" else lab.elapsed)[0] += 10
            return result

        with monkeypatch.context() as patch:
            patch.setattr(obj, "_confirm_owned", failed)
            with pytest.raises(KeyboardInterrupt if failure == "interrupt" else ERROR):
                prepare(obj, state) if phase == "prepared" else obj.claim()
        stable = snap(lab)
        assert obj.confirm().phase == phase  # Readback cannot restore the phase gate.
        with pytest.raises(ERROR):
            obj.claim()
        assert snap(lab) == stable


@pytest.mark.parametrize("clock", ["wall", "mono"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, None, 2**53])
def test_invalid_initial_clocks_never_write(lab, candidate, access, clock, value):
    state = expected(candidate)
    before = snap(lab)
    (lab.clock if clock == "wall" else lab.elapsed)[0] = value
    with access() as obj, pytest.raises(ERROR):
        prepare(obj, state)
    assert snap(lab) == before


@pytest.mark.parametrize("clock", ["wall", "mono"])
@pytest.mark.parametrize("delta", [-1, 120, 121])
def test_claim_lifetime_cannot_be_restarted_in_a_new_call(lab, candidate, access, clock, delta):
    state = expected(candidate)
    with access() as obj:
        prepared = prepare(obj, state)
        (lab.clock if clock == "wall" else lab.elapsed)[0] += delta
        before = snap(lab)
        with pytest.raises(ERROR):
            obj.claim()
        assert obj.confirm() == prepared
        assert snap(lab) == before
        with pytest.raises(ERROR):
            obj.claim()


@pytest.mark.parametrize("change", ["pause", "same-revision-claim", "private-inode",
                                    "manifest-inode", "owner-inode", "process", "new-instance"])
def test_stale_claim_or_changed_ownership_cannot_adopt_prepare(
        lab, candidate, access, monkeypatch, change):
    state = expected(candidate)
    with access() as obj:
        prepared = prepare(obj, state)
        if change == "pause":
            native_step(lab, candidate, "pause")
        elif change == "same-revision-claim":
            selected = current._SelectedFiles(candidate.root, **candidate.args)
            selection = epoch._EpochSelection(selected.files[selected.manifest_path][0],
                selected.files[selected.manifest_path][1], candidate.history,
                lab.args["profile"], current.activation._binding(lab.ledger.path))
            with transaction(lab.ledger.path) as db:
                snapshot = epoch._read(db, selection, readonly=False).snapshot
                epoch._stage_claim(db, selection, snapshot, obj._inputs, now=lab.clock[0])
                db.commit()
        elif change in {"private-inode", "manifest-inode", "owner-inode"}:
            path = (candidate.paths["credential"] if change == "private-inode" else
                    candidate.paths["manifest"] if change == "manifest-inode" else
                    candidate.root / ".sdsctl-device-launch.lock")
            raw = path.read_bytes()
            path.rename(path.with_name(path.name + ".retained"))
            private(path, raw)
        elif change == "process":
            monkeypatch.setattr(approval.os, "getppid", lambda: -1)
        else:
            obj = approval._BrowserWorkerApproval(lab.configuration, obj._worker)
        before = snap(lab)
        with pytest.raises(ERROR):
            obj.claim()
        with pytest.raises(ERROR):
            obj.confirm()
        assert snap(lab) == before
        assert prepared.phase == "prepared"


def edit_state(lab, candidate, *, mode=RecoveryMode.PAUSED, delay=0):
    selection = epoch._EpochSelection(candidate.paths["manifest"].read_bytes(),
        current.activation._binding(candidate.paths["manifest"]), candidate.history,
        lab.args["profile"], current.activation._binding(lab.ledger.path))
    with transaction(lab.ledger.path) as db:
        view = epoch._read(db, selection, readonly=False)
        state = {**view.state, "revision": view.state["revision"] + 1, "mode": mode,
                 "observed_at": lab.clock[0], "next_at": lab.clock[0] + delay}
        epoch._write(db, selection, view, view.approvals, state, None)
        db.commit()


@pytest.mark.parametrize("mode", [m for m in RecoveryMode if m is not RecoveryMode.ACTIVE])
def test_all_stopped_modes_stay_stopped_after_claim(lab, candidate, access, mode):
    edit_state(lab, candidate, mode=mode)
    state = expected(candidate)
    with access() as obj:
        assert prepare(obj, state).state.mode is mode
        assert obj.claim().state.mode is mode
    blocked(lab)


@pytest.mark.parametrize("phase", ["prepared", "claimed", "active"])
def test_existing_pending_or_active_state_is_not_adopted(lab, candidate, access, phase):
    for action in {"prepared": ("prepare",), "claimed": ("prepare", "claim"),
                   "active": ("prepare", "claim", "complete")}[phase]:
        native_step(lab, candidate, action)
    lab.clock[0] += 2
    state = expected(candidate)
    before = snap(lab)
    with access() as obj, pytest.raises(ERROR):
        prepare(obj, state)
    assert snap(lab) == before


def test_retry_delay_is_enforced_without_automatic_clock_correction(lab, candidate, access):
    edit_state(lab, candidate, delay=10)
    state = expected(candidate)
    before = snap(lab)
    with access() as obj, pytest.raises(ERROR):
        prepare(obj, state)
    assert snap(lab) == before
    lab.clock[0] += 10
    with access() as obj:
        assert prepare(obj, state).phase == "prepared"
        assert obj.claim().phase == "claimed"


@pytest.mark.parametrize("change", ["directory", "intent", "normal_bundle", "configuration",
                                    "ancestor", "no-owner"])
def test_fixed_worker_selection_is_not_caller_authority(
        lab, candidate, access, monkeypatch, change):
    state = expected(candidate)
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    config = lab.configuration
    if change in {"directory", "intent", "normal_bundle"}:
        selection = replace(selection, **{
            change: "f" * 64 if change == "intent" else candidate.root})
    elif change == "configuration":
        config = replace(config, device_id="other")
    elif change == "ancestor":
        monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root.parent)
    obj = approval._BrowserWorkerApproval(config, selection,
        clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
    before = snap(lab)
    with (_launch_lock(candidate.root, create=False) if change != "no-owner" else nullcontext(),
          pytest.raises(ERROR)):
        prepare(obj, state)
    assert snap(lab) == before


@pytest.mark.parametrize("kind", ["native", "profile", "archives"])
@pytest.mark.parametrize("phase", ["prepared", "claimed"])
def test_competing_writer_refuses_attempt_without_mutating(
        lab, candidate, access, kind, phase):
    state = expected(candidate)
    with access() as obj:
        if phase == "claimed":
            prepare(obj, state)
        before = snap(lab)
        with (transaction(lab.ledger.path) if kind == "native" else
              browser_profile_access(lab.args[kind], exclusive=True), pytest.raises(ERROR)):
            prepare(obj, state) if phase == "prepared" else obj.claim()
        assert snap(lab) == before
        with pytest.raises(ERROR):
            obj.claim()


@pytest.mark.parametrize("phase", ["prepared", "claimed"])
def test_failed_rollback_closes_uncommittable_connection(
        lab, candidate, access, monkeypatch, phase):
    state = expected(candidate)
    with access() as obj:
        if phase == "claimed":
            prepare(obj, state)
        before = lab.ledger.path.read_bytes()
        opened = []
        connect = sqlite3.connect
        name = "_stage_prepare" if phase == "prepared" else "_stage_claim"
        stage = getattr(epoch, name)

        class BrokenRollback(sqlite3.Connection):
            def rollback(self):
                raise sqlite3.OperationalError("PRIVATE rollback failure")

        def connection(*args, **kwargs):
            if args[0] == lab.ledger.path.as_uri() + "?mode=rw":
                db = connect(*args, **kwargs, factory=BrokenRollback)
                opened.append(db)
                return db
            return connect(*args, **kwargs)

        def interrupted(*args, **kwargs):
            stage(*args, **kwargs)
            raise RuntimeError("PRIVATE interrupted")

        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, "connect", connection)
            patch.setattr(epoch, name, interrupted)
            with pytest.raises(ERROR):
                prepare(obj, state) if phase == "prepared" else obj.claim()
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened[0].commit()
        assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("name", ["manifest", "release", "intent", "ledger"])
@pytest.mark.parametrize("kind", ["journal", "mode", "missing"])
def test_unsafe_selected_inputs_do_not_initialize_or_repair(lab, candidate, access, name, kind):
    state = expected(candidate)
    path = candidate.paths[name]
    if kind == "journal":
        path.with_name(path.name + "-journal").symlink_to("fictional uncertain journal")
    elif kind == "mode":
        path.chmod(0o644)
    else:
        path.rename(path.with_name(path.name + ".retained"))
    before = snap(lab)
    with access() as obj, pytest.raises(ERROR):
        prepare(obj, state)
    assert snap(lab) == before


@pytest.mark.parametrize("failure", ["silent-commit", "bad-nonce", "confirm-interrupt"])
def test_failed_or_out_of_order_lifecycle_never_restores_ready(
        lab, candidate, access, monkeypatch, failure):
    state = expected(candidate)
    with access() as obj:
        if failure == "confirm-interrupt":
            prepare(obj, state)
            with monkeypatch.context() as patch:
                patch.setattr(obj, "_confirm_owned", lambda *a: (_ for _ in ()).throw(
                    KeyboardInterrupt("PRIVATE confirm")))
                with pytest.raises(KeyboardInterrupt):
                    obj.confirm()
            assert obj.confirm().phase == "prepared"
        else:
            before = snap(lab)
            with monkeypatch.context() as patch:
                if failure == "silent-commit":
                    patch.setattr(approval, "_commit", lambda db: None)
                else:
                    patch.setattr(approval.secrets, "token_hex", lambda n: "invalid")
                with pytest.raises(ERROR):
                    prepare(obj, state)
            assert snap(lab) == before
        with pytest.raises(ERROR):
            obj.claim()
    with access() as obj:
        before = snap(lab)
        with pytest.raises(ERROR):
            obj.claim()
        with pytest.raises(ERROR):
            prepare(obj, state)
        assert snap(lab) == before
