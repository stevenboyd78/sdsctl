"""Owned cancellation fixtures: real files/locks/SQL, simulated history/ancestry."""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing, contextmanager, nullcontext
from dataclasses import FrozenInstanceError, replace

import pytest

from sds200 import browser_device_continuation_cancel as cancel
from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_ownership as ownership
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
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned cancellation fixtures")
ERROR = cancel.BrowserContinuationCancellationError


@pytest.fixture(params=["stopped", "worker"])
def access(lab, candidate, monkeypatch, request):
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)

    @contextmanager
    def attempt():
        clocks = dict(clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
        obj = (cancel.BrowserStoppedCancellation(candidate.root, **candidate.args, **clocks)
            if request.param == "stopped" else
            cancel._BrowserWorkerCancellation(lab.configuration, selection, **clocks))
        with (_launch_lock(candidate.root, create=False)
              if request.param == "worker" else nullcontext()):
            yield obj

    return attempt


def seed(lab, candidate, mode="paused"):
    for operation in {"paused": (), "prepared": ("prepare",), "claimed": ("prepare", "claim"),
                      "active": ("prepare", "claim", "complete")}[mode]:
        native_step(lab, candidate, operation)
    lab.clock[0] += 2
    return current.inspect_stopped_continuation(candidate.root, **candidate.args)


def unrelated(lab):
    result = snap(lab)
    result["profile"].pop("recovery.sqlite")
    return result


@pytest.mark.parametrize("mode", ["paused", "prepared", "claimed", "active"])
@pytest.mark.parametrize("operation", ["pause", "correct_clock", "pause-backstep"])
def test_one_owned_commit_cancels_without_grant_or_session(
        lab, candidate, access, monkeypatch, mode, operation):
    from sds200 import browser_device_native as native

    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network"))
    expected = seed(lab, candidate, mode)
    if operation != "pause":
        lab.clock[0] -= 100
    before = unrelated(lab)
    commits = []
    original = cancel._commit

    def commit(db):
        commits.append(db)
        assert db.in_transaction
        original(db)

    monkeypatch.setattr(cancel, "_commit", commit)
    with access() as attempt:
        result = getattr(attempt, "correct_clock" if operation == "correct_clock" else "pause")(
            expected)
        assert result.operation == ("clock-correction" if operation == "correct_clock" else "pause")
        assert result.state.native_revision == expected.native_revision + (
            2 if operation == "pause-backstep" else 1)
        assert result.state.mode is (expected.mode if operation == "correct_clock"
                                     else RecoveryMode.PAUSED)
        assert result.state.epoch == expected.epoch and len(commits) == 1
        assert unrelated(lab) == before
        stable = snap(lab)
        assert attempt.confirm() == result and snap(lab) == stable
        for value in (CREDENTIAL, str(candidate.root), result.state.identity, result.state.epoch):
            assert value not in repr(result) + repr(attempt)
        with pytest.raises(FrozenInstanceError):
            result.operation = "other"
        for method in (attempt.pause, attempt.correct_clock):
            with pytest.raises(ERROR):
                method(result.state)
        assert snap(lab) == stable and len(commits) == 1
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        phases = db.execute("SELECT phase FROM browser_epoch_approval").fetchall()
        assert phases == ([] if mode == "paused" else
                          [("complete" if mode == "active" else "cancelled",)])
        grant = db.execute("SELECT active_grant FROM browser_epoch_state").fetchone()[0]
        assert bool(grant) is (mode == "active" and operation == "correct_clock")
    assert current.inspect_stopped_continuation(candidate.root, **candidate.args) == result.state
    blocked(lab)


@pytest.mark.parametrize("change", ["revision", "same-revision-phase", "epoch", "fingerprint",
                                    "identity", "origin", "device", "manifest", "mode", "type"])
def test_stale_or_supplied_snapshot_never_authorizes_cancellation(
        lab, candidate, access, change):
    expected = seed(lab, candidate, "prepared" if change == "same-revision-phase" else "paused")
    if change == "revision":
        native_step(lab, candidate, "pause")
    elif change == "same-revision-phase":
        native_step(lab, candidate, "claim")
    elif change == "type":
        expected = candidate.history
    elif change == "mode":
        expected = replace(expected, mode=expected.mode.value)
    else:
        field = {"epoch": "epoch", "fingerprint": "state_fingerprint", "identity": "identity",
                 "origin": "origin", "device": "device_id", "manifest": "manifest_sha256"}[change]
        expected = replace(expected, **{field: "f" * 64})
    before = snap(lab)
    with access() as attempt:
        with pytest.raises(ERROR):
            attempt.pause(expected)
        with pytest.raises(ERROR):
            attempt.confirm()
        assert snap(lab) == before


@pytest.mark.parametrize("name", ["manifest", "release", "intent", "ledger"])
@pytest.mark.parametrize("change", ["missing", "mode", "journal", "inode"])
def test_unsafe_fixed_selection_never_writes_or_repairs(
        lab, candidate, access, monkeypatch, name, change):
    expected = seed(lab, candidate)
    selected = current._SelectedFiles(candidate.root, **candidate.args)
    path = candidate.paths[name]
    if change == "missing":
        path.rename(path.with_name(path.name + ".retained"))
    elif change == "mode":
        path.chmod(0o644)
    elif change == "journal":
        path.with_name(path.name + "-journal").symlink_to("fictional missing sidecar")
    else:
        raw = path.read_bytes()
        path.rename(path.with_name(path.name + ".retained"))
        private(path, raw)
    before = snap(lab)
    with access() as attempt:
        if change == "inode":
            # Minimal portable journals simulate history and omit its inode anchor.
            # Exercise replacement AFTER bootstrap here; complete-chain fixtures
            # additionally prove copied journals are refused BEFORE selection.
            monkeypatch.setattr(attempt, "_select", lambda: selected)
        with pytest.raises(ERROR):
            attempt.pause(expected)
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["manifest", "credential", "trust", "bundle", "ledger-mode",
    "native-state", "owner", "process", "wall-backstep", "wall-expiry", "mono-backstep",
    "mono-expiry", "nan", "bool", "after-stage", "between-clock-and-pause"])
def test_post_dml_failure_aborts_whole_transaction(
        lab, candidate, access, monkeypatch, change):
    expected = seed(lab, candidate, "claimed")
    before = lab.ledger.path.read_bytes()
    method = "_stage_clock_correction" if change == "between-clock-and-pause" else "_stage_pause"
    if change == "between-clock-and-pause":
        lab.clock[0] -= 100
    original = getattr(epoch, method)

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change in {"after-stage", "between-clock-and-pause"}:
            raise RuntimeError("PRIVATE post-stage failure")
        if change in {"manifest", "credential", "trust", "bundle"}:
            path = candidate.paths[change]
            path.write_bytes(path.read_bytes() + b" ")
        elif change == "ledger-mode":
            candidate.paths["ledger"].chmod(0o644)
        elif change == "native-state":
            args[0].execute("UPDATE recovery SET revision=revision+1")
        elif change == "owner":
            candidate.captures[-1][1]._active = False
        elif change == "process":
            monkeypatch.setattr(cancel.os, "getppid", lambda: -1)
        else:
            target = lab.elapsed if change.startswith("mono") else lab.clock
            target[0] = (float("nan") if change == "nan" else True if change == "bool"
                         else target[0] + (10 if change.endswith("expiry") else -1))
        return result

    with access() as attempt, monkeypatch.context() as patch:
        patch.setattr(epoch, method, changed)
        with pytest.raises(ERROR) as error:
            attempt.pause(expected)
        assert "PRIVATE" not in str(error.value) and CREDENTIAL not in str(error.value)
        assert lab.ledger.path.read_bytes() == before
        with pytest.raises(ERROR):
            attempt.pause(expected)


@pytest.mark.parametrize("committed", [False, True])
def test_lost_commit_reply_never_replays_and_only_exact_readback_can_match(
        lab, candidate, access, monkeypatch, committed):
    expected = seed(lab, candidate, "claimed")
    before = lab.ledger.path.read_bytes()

    def uncertain(db):
        if committed:
            db.commit()
        raise sqlite3.OperationalError("PRIVATE uncertain commit reply")

    with access() as attempt:
        with monkeypatch.context() as patch:
            patch.setattr(cancel, "_commit", uncertain)
            with pytest.raises(ERROR):
                attempt.pause(expected)
        after = snap(lab)
        if committed:
            result = attempt.confirm()
            assert result.state.native_revision == expected.native_revision + 1
            assert result.state.mode is RecoveryMode.PAUSED
        else:
            assert lab.ledger.path.read_bytes() == before
            with pytest.raises(ERROR):
                attempt.confirm()
        for method in (attempt.pause, attempt.correct_clock):
            with pytest.raises(ERROR):
                method(expected)
        assert snap(lab) == after


@pytest.mark.parametrize("change", ["revision", "manifest-bytes", "manifest-inode", "credential",
                                    "process", "owner", "new-instance"])
def test_confirmation_does_not_mean_any_later_pause_or_new_operation(
        lab, candidate, access, monkeypatch, change):
    expected = seed(lab, candidate)
    with access() as attempt:
        result = attempt.pause(expected)
        if change == "revision":
            native_step(lab, candidate, "pause")
        elif change in {"manifest-bytes", "manifest-inode", "credential"}:
            path = candidate.paths["credential" if change == "credential" else "manifest"]
            if change == "manifest-inode":
                raw = path.read_bytes()
                path.rename(path.with_name(path.name + ".retained"))
                private(path, raw)
            else:
                path.write_bytes(path.read_bytes() + b" ")
        elif change == "process":
            monkeypatch.setattr(cancel.os, "getpid", lambda: -1)
        elif change == "owner":
            path = candidate.root / ".sdsctl-device-launch.lock"
            path.rename(path.with_name("retained-lock"))
            private(path, b"")
        else:
            attempt = cancel.BrowserStoppedCancellation(candidate.root, **candidate.args)
        after = snap(lab)
        with pytest.raises(ERROR):
            attempt.confirm()
        with pytest.raises(TypeError):
            attempt.confirm(result)
        assert snap(lab) == after


def test_writers_and_private_input_replacement_are_excluded_until_commit(
        lab, candidate, access, monkeypatch):
    expected = seed(lab, candidate, "prepared")
    original = epoch._stage_pause

    def checked(*args, **kwargs):
        for name in ("profile", "archives"):
            with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                    lab.args[name], exclusive=True):
                pytest.fail("Competing private writer")
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing launcher")
        with (closing(sqlite3.connect(lab.ledger.path, timeout=0)) as rival,
              pytest.raises(sqlite3.OperationalError)):
            rival.execute("BEGIN IMMEDIATE")
        result = original(*args, **kwargs)
        # The portable history capture also refuses this writer's live journal.
        h, owner = candidate.captures[-1]
        with pytest.raises(ValueError):
            current.history._capture_owned_history(h, owner,
                release_id="a" * 64, intent_id="b" * 64)
        return result

    monkeypatch.setattr(epoch, "_stage_pause", checked)
    with access() as attempt:
        assert attempt.pause(expected).state.mode is RecoveryMode.PAUSED


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_interruption_after_stage_never_commits(lab, candidate, access, monkeypatch, exception):
    expected = seed(lab, candidate)
    before = lab.ledger.path.read_bytes()
    original = epoch._stage_pause

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise exception()

    monkeypatch.setattr(epoch, "_stage_pause", interrupted)
    with access() as attempt:
        with pytest.raises(exception):
            attempt.pause(expected)
        assert lab.ledger.path.read_bytes() == before
        with pytest.raises(ERROR):
            attempt.pause(expected)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "0"])
def test_invalid_initial_clock_and_nonbackstep_correction_never_write(
        lab, candidate, access, value):
    expected = seed(lab, candidate)
    before = snap(lab)
    with access() as attempt, pytest.raises(ERROR):
        attempt.correct_clock(expected)  # A forward/current clock cannot be 'corrected'.
    lab.elapsed[0] = value
    with access() as attempt, pytest.raises(ERROR):
        attempt.pause(expected)
    assert snap(lab) == before


def test_failed_rollback_closes_connection_instead_of_leaving_reusable_dml(
        lab, candidate, access, monkeypatch):
    expected = seed(lab, candidate)
    before = lab.ledger.path.read_bytes()
    connect, stage = sqlite3.connect, epoch._stage_pause
    opened, closed = [], []

    class BrokenRollback(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError("PRIVATE rollback failure")

        def close(self):
            closed.append(self)
            super().close()

    def selected(*args, **kwargs):
        if args[0] == lab.ledger.path.as_uri() + "?mode=rw":
            db = connect(*args, **kwargs, factory=BrokenRollback)
            opened.append(db)
            return db
        return connect(*args, **kwargs)

    def interrupted(*args, **kwargs):
        stage(*args, **kwargs)
        raise RuntimeError("PRIVATE stage failure")

    monkeypatch.setattr(sqlite3, "connect", selected)
    monkeypatch.setattr(epoch, "_stage_pause", interrupted)
    with access() as attempt, pytest.raises(ERROR):
        attempt.pause(expected)
    assert len(opened) == 1 and opened[0] in closed
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].commit()
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("change", ["directory", "intent", "normal_bundle", "configuration",
                                    "ancestor", "no-owner"])
def test_worker_cancellation_requires_actual_current_selection(lab, candidate, monkeypatch, change):
    expected = seed(lab, candidate)
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    configuration = lab.configuration
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)
    if change in {"directory", "intent", "normal_bundle"}:
        selection = replace(selection, **{
            change: "f" * 64 if change == "intent" else candidate.root})
    elif change == "configuration":
        configuration = replace(configuration, device_id="other")
    elif change == "ancestor":
        monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root.parent)
    before = snap(lab)
    attempt = cancel._BrowserWorkerCancellation(configuration, selection,
        clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
    with (_launch_lock(candidate.root, create=False) if change != "no-owner" else nullcontext(),
          pytest.raises(ERROR)):
        attempt.pause(expected)
    assert snap(lab) == before


@pytest.mark.parametrize("failure", ["lost-readback", "silent-no-commit"])
def test_success_requires_exact_readback_not_only_a_returning_commit(
        lab, candidate, access, monkeypatch, failure):
    expected = seed(lab, candidate)
    original = lab.ledger.path.read_bytes()

    def lost(*args):
        raise RuntimeError("PRIVATE lost after-state acknowledgement")

    with access() as attempt:
        with monkeypatch.context() as patch:
            if failure == "lost-readback":
                patch.setattr(attempt, "_confirm_owned", lost)
            else:
                patch.setattr(cancel, "_commit", lambda db: None)
            with pytest.raises(ERROR):
                attempt.pause(expected)
        before = snap(lab)
        if failure == "lost-readback":
            assert attempt.confirm().state.native_revision == expected.native_revision + 1
        else:
            assert lab.ledger.path.read_bytes() == original
            with pytest.raises(ERROR):
                attempt.confirm()
        assert snap(lab) == before


@pytest.mark.parametrize("failure", ["native-journal", "manifest-inode", "selected-journal",
                                    "sql-policy", "second-clock-backstep"])
def test_late_conflicts_abort_cancellation_without_repair(
        lab, candidate, access, monkeypatch, failure):
    expected = seed(lab, candidate)
    before = lab.ledger.path.read_bytes()
    stage = epoch._stage_pause

    def changed(*args, **kwargs):
        result = stage(*args, **kwargs)
        if failure == "native-journal":
            private(lab.ledger.path.with_name("recovery.sqlite-wal"), b"uncertain sidecar")
        elif failure == "manifest-inode":
            path = candidate.paths["manifest"]
            raw = path.read_bytes()
            path.rename(path.with_name(path.name + ".retained"))
            private(path, raw)
        elif failure == "selected-journal":
            path = candidate.paths["release"]
            path.with_name(path.name + "-journal").symlink_to("fictional sidecar")
        elif failure == "sql-policy":
            args[0].execute("PRAGMA query_only=ON")
        else:
            lab.clock[0] -= 1
        return result

    if failure == "second-clock-backstep":
        lab.clock[0] -= 100
    monkeypatch.setattr(epoch, "_stage_pause", changed)
    with access() as attempt, pytest.raises(ERROR):
        attempt.pause(expected)
    assert lab.ledger.path.read_bytes() == before
