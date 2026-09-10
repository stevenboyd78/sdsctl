"""Real private files/locks/SQL, simulated full history and live browser ancestry.

Minimal journals here exercise fixed target selection, NOT full historical proof.
The separate required namespace fixtures use complete real retained chains.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_recovery as recovery
from sds200.browser_device_bundle import _json
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_resume_boundary import BrowserResumeBoundary
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_worker import BrowserWorkerSelection
from tests import test_browser_device_continuation_activation as activation_tests
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
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
                               reason="Non-root Linux owned current-state fixtures")
ERROR = current.BrowserContinuationCurrentError
activation_candidate = activation_tests.candidate


@pytest.fixture
def candidate(lab, handoff, activation_candidate, monkeypatch):
    activated = activation_candidate.core.apply(confirmation=lambda r: r.confirmation)
    root = lab.args["directory"]
    bodies = {
        "release": dict(release_id="a" * 64, evidence=dict(targets=handoff._targets,
            operation_id=handoff._operation, browser_intent=handoff._intent)),
        "intent": dict(release_id="a" * 64, intent_id="b" * 64),
    }
    for name, module in (("release", current.release), ("intent", current.intent)):
        path = root / (module.RELEASE_JOURNAL if name == "release" else module.INTENT_JOURNAL)
        if path.exists():
            path.unlink()  # Only the prior portable fixture's fictional placeholder.
        private(path, b"")
        raw = _json(bodies[name])
        (module._create if name == "release" else module._prepare)(path, raw)
        module._complete(path, raw, lambda: None)
    boundary = BrowserResumeBoundary(lab.args["profile"], archives=lab.args["archives"])
    binding = boundary._binding()
    captures = []

    class Inputs:
        def __init__(self, h, owner):
            self.h, self.owner = h, owner

        def recheck(self):
            self.owner.binding(self.h)
            assert boundary._binding() == binding
            current.history._canonical_bundle_files(**self.h._session._registration)

    def capture(h, owner, **ids):
        assert h._targets == handoff._targets and ids == dict(
            release_id="a" * 64, intent_id="b" * 64)
        captured = Inputs(h, owner)
        captured.recheck()
        current.activation._no_sidecars(lab.ledger.path)
        captures.append((h, owner))
        return activation_candidate.retained, captured

    monkeypatch.setattr(current.history, "_capture_owned_history", capture)
    paths = dict(manifest=activation_candidate.path, release=root / current.release.RELEASE_JOURNAL,
        intent=root / current.intent.INTENT_JOURNAL, ledger=lab.ledger.path,
        credential=lab.args["profile"] / "device.secret", trust=lab.args["profile"] / "ca.pem",
        bundle=lab.args["bundle"] / "extension" / "worker.mjs")
    return SimpleNamespace(args={k: lab.args[k] for k in ("bundle", "profile", "public_key")},
        root=root, activated=activated, history=activation_candidate.retained,
        paths=paths, captures=captures)


@pytest.fixture(params=["stopped", "worker"])
def access(lab, candidate, monkeypatch, request):
    selection = BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)

    @contextmanager
    def scope():
        if request.param == "stopped":
            with current._stopped_current_scope(candidate.root, **candidate.args) as reader:
                yield reader
        else:
            with (_launch_lock(candidate.root, create=False),
                  current._worker_current_scope(lab.configuration, selection) as reader):
                yield reader

    return scope


def test_read_only_exact_state_private_results_and_scope_expiry(
        lab, candidate, access, monkeypatch):
    from sds200 import browser_device_native as native

    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network I/O"))
    monkeypatch.setattr(recovery.BrowserDeviceRecovery, "_load",
                        lambda *a, **k: pytest.fail("Legacy load/clock correction"))
    before = snap(lab)
    with access() as reader:
        result = reader.inspect()
        assert result.native_revision == candidate.activated.native_revision
        assert result.mode is recovery.RecoveryMode.PAUSED
        assert result.epoch == candidate.activated.epoch
        assert len(candidate.captures) == 1
        assert result == reader.inspect() and snap(lab) == before
        assert not isinstance(result, (
            epoch._EpochSnapshot, current.history.BrowserContinuationHistory))
        assert not any(hasattr(result, n) for n in ("permission", "session", "commit", "role"))
        with pytest.raises(FrozenInstanceError):
            result.native_revision += 1
        for value in (CREDENTIAL, str(candidate.root), result.identity, result.origin,
                      result.epoch, result.state_fingerprint, result.manifest_sha256):
            assert value not in repr(result) + repr(reader)
        for key in ("profile", "archives"):
            with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                    lab.args[key], exclusive=True):
                pytest.fail("Competing private-input writer")
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing launcher")
        with pytest.raises(sqlite3.OperationalError):
            reader._db.execute("UPDATE recovery SET revision=revision+1")
        assert reader.inspect() == result
        lab.clock[0] -= 100000  # Advisory read does not correct clocks or cancel consent.
        assert reader.inspect() == result
    with pytest.raises(ERROR):
        reader.inspect()
    assert snap(lab) == before


def native_step(lab, candidate, operation):
    selection = epoch._EpochSelection(candidate.paths["manifest"].read_bytes(),
        current.activation._binding(candidate.paths["manifest"]), candidate.history,
        lab.args["profile"], current.activation._binding(lab.ledger.path))
    inputs = epoch._ApprovalInputs("d" * 64, "e" * 64, lab.configuration.device_id,
                                   1, "f" * 64, "0" * 64)
    with transaction(lab.ledger.path) as db:
        before = epoch._read(db, selection, readonly=False).snapshot
        args = [] if operation == "pause" else [inputs]
        after = getattr(epoch, "_stage_" + operation)(db, selection, before, *args,
                                                     now=lab.clock[0] + 1)
        db.commit()
        return after


def test_new_scope_sees_each_valid_current_state_but_never_grants_runtime(lab, candidate, access):
    previous = None
    for operation in ("prepare", "claim", "complete", "pause"):
        changed = native_step(lab, candidate, operation)
        before = snap(lab)
        with access() as reader:
            result = reader.inspect()
            assert result.native_revision == changed.revision and result.mode is changed.mode
            assert result.state_fingerprint == changed.fingerprint
            assert previous != result
            previous = result
        assert snap(lab) == before
    blocked(lab)


@pytest.mark.parametrize("name", ["manifest", "release", "intent", "ledger"])
@pytest.mark.parametrize("change", ["absent", "symlink", "mode", "empty", "hardlink", "directory"])
def test_unsafe_selected_files_fail_without_repair(lab, candidate, access, name, change):
    path = candidate.paths[name]
    original = path.with_name(path.name + ".retained")
    if change == "mode":
        path.chmod(0o644)
    elif change == "empty":
        path.write_bytes(b"")
    elif change == "hardlink":
        os.link(path, original)
    else:
        path.rename(original)
        if change == "symlink":
            path.symlink_to(original)
        elif change == "directory":
            path.mkdir(mode=0o700)
    before = snap(lab)
    with pytest.raises(ERROR), access():
        pytest.fail("Unsafe selection was yielded")
    assert snap(lab) == before


@pytest.mark.parametrize("name", ["manifest", "release", "intent", "ledger"])
@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_sidecar_presence_is_never_adopted(lab, candidate, access, name, suffix):
    path = candidate.paths[name]
    path.with_name(path.name + suffix).symlink_to("fictional-sidecar")
    before = snap(lab)
    with pytest.raises(ERROR), access():
        pytest.fail("Sidecar was ignored")
    assert snap(lab) == before


@pytest.mark.parametrize("name", ["manifest", "release", "intent",
                                  "credential", "trust", "bundle"])
def test_late_change_latches_even_after_bytes_are_restored(lab, candidate, access, name):
    path = candidate.paths[name]
    raw = path.read_bytes()
    with pytest.raises(ERROR), access() as reader:
        path.write_bytes(raw + b" ")
        with pytest.raises(ERROR) as error:
            reader.inspect()
        assert CREDENTIAL not in str(error.value) and str(path) not in str(error.value)
        path.write_bytes(raw)
        with pytest.raises(ERROR):
            reader.inspect()
    with pytest.raises(ERROR):
        reader.inspect()


@pytest.mark.parametrize("name", ["manifest", "release", "intent", "ledger"])
def test_same_bytes_at_new_inode_do_not_preserve_open_scope(candidate, access, name):
    path = candidate.paths[name]
    with pytest.raises(ERROR), access() as reader:
        saved = path.with_name(path.name + ".retained")
        path.rename(saved)
        private(path, saved.read_bytes())
        with pytest.raises(ERROR):
            reader.inspect()


@pytest.mark.parametrize("event", ["parent", "process", "owner", "before-final-read"])
def test_owner_lifetime_and_uncaught_exit_checks(candidate, access, monkeypatch, event):
    with pytest.raises(ERROR), access() as reader:
        if event == "owner":
            reader._owner._active = False
        elif event == "before-final-read":
            candidate.paths["manifest"].chmod(0o644)
            # No explicit recheck: scope exit still refuses the changed file.
        else:
            with monkeypatch.context() as patch:
                name = "getpid" if event == "process" else "getppid"
                original = getattr(current.os, name)()
                patch.setattr(current.os, name, lambda: original + 1)
                with pytest.raises(ERROR):
                    reader.inspect()
            with pytest.raises(ERROR):
                reader.inspect()


def test_native_writer_journal_invalidates_read_scope_without_adoption(lab, candidate, access):
    before = snap(lab)
    with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as rival:
        with pytest.raises(ERROR), access() as reader:
            rival.execute("BEGIN IMMEDIATE")
            rival.execute("UPDATE recovery SET revision=revision+1")
            assert lab.ledger.path.with_name("recovery.sqlite-journal").exists()
            with pytest.raises(ERROR):
                reader.inspect()
        rival.rollback()
    assert snap(lab) == before


@pytest.mark.parametrize("call", [1, 2, 3])
def test_change_after_native_read_is_caught_before_result_or_scope_exit(
        candidate, access, monkeypatch, call):
    original = epoch._inspect_epoch
    count = []

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        count.append(result)
        if len(count) == call:
            candidate.paths["credential"].write_bytes(b"fictional changed private input")
        return result

    monkeypatch.setattr(epoch, "_inspect_epoch", changed)
    with pytest.raises(ERROR), access() as reader:
        reader.inspect()
    assert len(count) == call


@pytest.mark.parametrize("field", ["directory", "bundle", "profile", "public_key",
                                   "archives", "handoff", "recovery_bundle"])
def test_fixed_targets_cannot_redirect_current_selection(lab, candidate, access, field):
    path = candidate.paths["release"]
    body = json.loads(current.release._read(path))
    body["evidence"]["targets"][field] = str(candidate.root)
    if field == "directory":
        body["evidence"]["targets"][field] = str(candidate.root.parent)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE release SET body=?", (_json(body),))
        db.commit()
    before = snap(lab)
    with pytest.raises(ERROR), access():
        pytest.fail("Unselected target was accepted")
    assert snap(lab) == before


@pytest.mark.parametrize("field,value", [("release_id", "f" * 64), ("intent_id", True)])
def test_wrong_journal_identifiers_are_not_caller_permission(lab, candidate, access, field, value):
    path = candidate.paths["intent"]
    body = json.loads(current.intent._read(path))
    body[field] = value
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE intent SET body=?", (_json(body),))
        db.commit()
    before = snap(lab)
    with pytest.raises(ERROR), access():
        pytest.fail("Wrong journal identity was accepted")
    assert snap(lab) == before


@pytest.mark.parametrize("change", ["directory", "intent", "normal_bundle", "bundle", "public_key",
                                    "type", "configuration", "ancestor", "no-owner"])
def test_worker_uses_only_actual_ancestor_and_fixed_wrapper(
        lab, candidate, monkeypatch, change):
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    configuration = lab.configuration
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)
    if change in {"directory", "intent", "normal_bundle", "bundle", "public_key"}:
        selection = replace(selection, **{
            change: "f" * 64 if change == "intent" else candidate.root})
    elif change == "type":
        selection = object()
    elif change == "configuration":
        configuration = replace(configuration, device_id="other")
    elif change == "ancestor":
        monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root.parent)
    before = snap(lab)
    if change == "no-owner":
        with pytest.raises(ERROR):
            current._inspect_worker_continuation(configuration, selection)
    else:
        with _launch_lock(candidate.root, create=False), pytest.raises(ERROR):
            current._inspect_worker_continuation(configuration, selection)
    assert snap(lab) == before


def test_public_advisory_reacquires_ownership_and_never_falls_back(lab, candidate):
    before = snap(lab)
    result = current.inspect_stopped_continuation(candidate.root, **candidate.args)
    assert result.native_revision == candidate.activated.native_revision and snap(lab) == before
    with _launch_lock(candidate.root, create=False), pytest.raises(ERROR):
        current.inspect_stopped_continuation(candidate.root, **candidate.args)
    # Fixture-only missing-manifest refusal, not cleanup advice.
    candidate.paths["manifest"].unlink()
    with pytest.raises(ERROR):
        current.inspect_stopped_continuation(candidate.root, **candidate.args)


@pytest.mark.parametrize("name", ["release", "intent"])
@pytest.mark.parametrize("change", ["prepared", "extra-table", "noncanonical", "wrong-schema"])
def test_fixed_journals_require_complete_canonical_schema(lab, candidate, access, name, change):
    with closing(sqlite3.connect(candidate.paths[name])) as db:
        if change == "prepared":
            db.execute(f"UPDATE {name} SET phase='prepared'")
        elif change == "extra-table":
            db.execute("CREATE TABLE unrelated (value TEXT)")
        elif change == "wrong-schema":
            db.execute("PRAGMA user_version=2")
        else:
            raw = db.execute(f"SELECT body FROM {name}").fetchone()[0]
            db.execute(f"UPDATE {name} SET body=?", (raw + b" ",))
        db.commit()
    before = snap(lab)
    with pytest.raises(ERROR), access():
        pytest.fail("Incomplete/noncanonical selection was yielded")
    assert snap(lab) == before


def test_interruption_releases_read_transaction_and_expires_scope(lab, candidate, access):
    before = snap(lab)
    with pytest.raises(KeyboardInterrupt), access() as reader:
        reader.inspect()
        raise KeyboardInterrupt()
    with pytest.raises(ERROR):
        reader.inspect()
    assert snap(lab) == before
    assert current.inspect_stopped_continuation(
        candidate.root, **candidate.args).mode is recovery.RecoveryMode.PAUSED


def test_read_scope_checks_native_mode_again_after_selection(candidate, access):
    with pytest.raises(ERROR), access() as reader:
        candidate.paths["ledger"].chmod(0o644)
        with pytest.raises(ERROR):
            reader.inspect()
        candidate.paths["ledger"].chmod(0o600)
        with pytest.raises(ERROR):
            reader.inspect()


@pytest.mark.parametrize("name", ["root", "bundle", "profile", "public_key"])
@pytest.mark.parametrize("change", ["relative", "string", "symlink"])
def test_installed_path_selection_never_normalizes_unsafe_targets(lab, candidate, name, change):
    args = dict(root=candidate.root, **candidate.args)
    path = args[name]
    if change == "relative":
        args[name] = Path(path.name)
    elif change == "string":
        args[name] = str(path)
    else:
        link = path.with_name(path.name + ".alias")
        link.symlink_to(path)
        args[name] = link
    before = snap(lab)
    with pytest.raises(ERROR):
        current.inspect_stopped_continuation(**args)
    assert snap(lab) == before


def test_reopened_transaction_cannot_renew_original_scoped_snapshot(lab, candidate, access):
    with pytest.raises(ERROR), access() as reader:
        before = reader.inspect()
        # Deliberate misuse of an internal connection: no public API exposes this.
        reader._db.rollback()
        changed = native_step(lab, candidate, "prepare")
        assert changed.revision != before.native_revision
        reader._db.execute("BEGIN")
        with pytest.raises(ERROR):
            reader.inspect()


def test_worker_advisory_returns_only_after_live_scope_rechecks(lab, candidate, monkeypatch):
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)
    expected = current.inspect_stopped_continuation(candidate.root, **candidate.args)
    before = snap(lab)
    with _launch_lock(candidate.root, create=False):
        selected = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
        assert current._inspect_worker_continuation(lab.configuration, selected) == expected
    assert snap(lab) == before
