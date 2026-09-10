"""Owned online fixtures: actual private SQL/files, simulated consent/ancestry.

The selected tests use real verified TLS with fictional responses. They do not
install browser sessions or qualify ordinary native dispatch/physical displays.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError, replace

import pytest

from sds200 import browser_device_bundle as bundle
from sds200 import browser_device_continuation_approval as approval
from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_continuation_verification as verification
from sds200 import browser_device_verification as transport
from sds200.browser_device_profile import create_browser_profile
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from sds200.browser_device_worker import BrowserWorkerSelection
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_approval import expected, unrelated
from tests.test_browser_device_continuation_current import (
    activation_candidate as activation_candidate,
)
from tests.test_browser_device_continuation_current import candidate as candidate
from tests.test_browser_device_continuation_current import native_step
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_native import server as server
from tests.test_browser_device_profile import CREDENTIAL, issuance, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned verification fixtures")
ERROR = verification.BrowserContinuationVerificationError
INTENT = "e" * 64


@pytest.fixture
def profile(tmp_path, public_key, certificates, request):
    origin = "https://127.0.0.1:8443"
    if hasattr(request, "param"):
        config, _, _ = request.getfixturevalue("server")
        origin = config.origin
        if request.param == "ip":
            origin = origin.replace("localhost", "127.0.0.1")
    private(tmp_path / "enrollment.json", json.dumps(issuance()))
    trust = certificates[1 if getattr(request, "param", None) == "hostname" else 0][0]
    private(tmp_path / "ca.pem", trust.read_bytes())
    path = tmp_path / "owned verification profile"
    create_browser_profile(path, enrollment_file=tmp_path / "enrollment.json",
        ca_file=tmp_path / "ca.pem", origin=origin, device_id="display",
        extension_id=bundle.browser_extension_identity(public_key).extension_id)
    return path


@pytest.fixture
def access(lab, candidate, monkeypatch):
    lab.clock[0] += 2
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)

    @contextmanager
    def scope():
        obj = verification._BrowserWorkerVerification(lab.configuration, selection,
            clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
        with _launch_lock(candidate.root, create=False):
            yield obj

    return scope


def run(obj, state, **kwargs):
    return obj.run(state, **{**dict(intent=INTENT, reviewed_generation=7,
                                   consent=lambda review: review), **kwargs})


def proof(config, record):
    return transport.BrowserVerifiedRecord(config.identity, record, True)


def phase(lab):
    with closing(sqlite3.connect(lab.ledger.path)) as db:
        return db.execute("SELECT phase FROM browser_epoch_approval ORDER BY revision").fetchall()


def assert_no_replay(obj, state, lab):
    before = snap(lab)
    with pytest.raises(ERROR):
        run(obj, state)
    assert snap(lab) == before


@pytest.mark.parametrize("delay", [0, 1, 9.9])
def test_owned_completion_keeps_sql_unlocked_during_proof_and_returns_no_session(
        lab, candidate, access, monkeypatch, delay):
    state = expected(candidate)
    before = unrelated(lab)
    calls = []

    def verify(config, record):
        assert phase(lab) == [("claimed",)]
        assert config == lab.configuration
        assert record == BrowserDeviceRecord(config.device_id, 7, BrowserDeviceState.ACTIVE)
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        for name in ("profile", "archives"):
            with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                    lab.args[name], exclusive=True):
                pytest.fail("Private writer acquired during proof")
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing launcher")
        calls.append(record)
        lab.clock[0] += delay
        lab.elapsed[0] += delay
        return proof(config, record)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda *a: pytest.fail("Session exchange"))
    with access() as obj:
        result = run(obj, state)
        assert result.phase == "complete" and result.state.mode is RecoveryMode.ACTIVE
        assert result.state.native_revision == state.native_revision + 2
        assert phase(lab) == [("complete",)] and unrelated(lab) == before
        assert obj.confirm() == result and len(calls) == 1
        for value in (CREDENTIAL, INTENT, str(candidate.root), state.identity, state.epoch):
            assert value not in repr(result) + repr(obj)
        with pytest.raises(FrozenInstanceError):
            result.phase = "failed"
        assert_no_replay(obj, state, lab)
        lab.clock[0] += 1000
        lab.elapsed[0] += 1000
        assert obj.confirm() == result and len(calls) == 1  # Not renewed server authority.
    blocked(lab)


@pytest.mark.parametrize("failure", ["exception", "none", "identity", "device", "generation",
    "bool-generation", "paused", "drained", "interrupt", "exit"])
def test_server_refusal_terminalizes_only_the_owned_claim(
        lab, candidate, access, monkeypatch, failure):
    state = expected(candidate)
    before = unrelated(lab)

    def verify(config, record):
        if failure in {"exception", "interrupt", "exit"}:
            raise {"exception": RuntimeError, "interrupt": KeyboardInterrupt,
                   "exit": SystemExit}[failure]("PRIVATE " + CREDENTIAL)
        result = proof(config, record)
        if failure == "none":
            return None
        if failure == "identity":
            return replace(result, identity="f" * 64)
        if failure == "drained":
            return replace(result, drained=1)
        change = {"device": dict(device_id="other"), "generation": dict(generation=8),
                  "bool-generation": dict(generation=True),
                  "paused": dict(state=BrowserDeviceState.PAUSED)}[failure]
        return replace(result, record=replace(record, **change))

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as obj:
        with pytest.raises({"interrupt": KeyboardInterrupt, "exit": SystemExit}.get(
                failure, ERROR)) as error:
            run(obj, state)
        if failure not in {"interrupt", "exit"}:
            assert CREDENTIAL not in str(error.value) and "PRIVATE" not in str(error.value)
            result = obj.confirm()
            assert result.phase == "failed" and result.state.mode is RecoveryMode.PAUSED
            assert result.state.native_revision == state.native_revision + 2
        else:
            with pytest.raises(ERROR):
                obj.confirm()
        assert phase(lab) == [("claimed" if failure in {"interrupt", "exit"} else "failed",)]
        assert unrelated(lab) == before
        assert_no_replay(obj, state, lab)


@pytest.mark.parametrize("phase_name", ["prepare", "claim"])
@pytest.mark.parametrize("committed", [False, True])
def test_no_network_after_uncertain_prepare_or_claim(
        lab, candidate, access, monkeypatch, phase_name, committed):
    state = expected(candidate)
    original = approval._commit
    commits = []

    def commit(db):
        commits.append(1)
        if len(commits) == (1 if phase_name == "prepare" else 2):
            if committed:
                original(db)
            raise RuntimeError("PRIVATE lost reply")
        original(db)

    monkeypatch.setattr(approval, "_commit", commit)
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        with pytest.raises(ERROR):
            obj.confirm()
        assert_no_replay(obj, state, lab)
    fresh_state = expected(candidate)
    if phase_name == "prepare" and not committed:
        # There is no durable attempt to adopt. A separately initiated fresh
        # review is allowed, unlike replaying the failed original object.
        monkeypatch.setattr(transport, "verify_browser_device", proof)
        reviews = []
        with access() as other:
            result = run(other, fresh_state, intent="f" * 64,
                         consent=lambda review: reviews.append(review) or review)
            assert result.phase == "complete" and len(reviews) == 1
    else:
        with access() as other, pytest.raises(ERROR):
            # Even a fresh snapshot cannot adopt the prior pending/claimed row.
            run(other, fresh_state)


@pytest.mark.parametrize("change", ["pause", "credential", "trust", "bundle", "manifest",
    "release", "intent", "ledger-inode", "owner-inode", "owner", "parent", "same-revision",
    "wall-backstep", "mono-backstep", "wall-expired", "mono-expired", "nan", "bool"])
@pytest.mark.parametrize("response", ["valid", "refused"])
def test_changes_during_network_never_overwrite_new_state(
        lab, candidate, access, monkeypatch, change, response):
    state = expected(candidate)
    captured = []

    def verify(config, record):
        if change == "pause":
            native_step(lab, candidate, "pause")
        elif change == "same-revision":
            selection = epoch._EpochSelection(candidate.paths["manifest"].read_bytes(),
                current.activation._binding(candidate.paths["manifest"]), candidate.history,
                lab.args["profile"], current.activation._binding(lab.ledger.path))
            scope = current.activation._ledger_transaction(lab.args["profile"], readonly=False)
            with scope as pair:
                db, _ = pair
                view = epoch._read(db, selection, readonly=False)
                epoch._write(db, selection, view, view.approvals,
                    {**view.state, "observed_at": lab.clock[0] + 1}, view.grant)
                db.commit()
        elif change in {"ledger-inode", "owner-inode"}:
            path = lab.ledger.path if change == "ledger-inode" else (
                candidate.root / ".sdsctl-device-launch.lock")
            path.rename(path.with_name(path.name + ".retained"))
            private(path, path.with_name(path.name + ".retained").read_bytes())
        elif change == "owner":
            candidate.captures[-1][1]._active = False
        elif change == "parent":
            monkeypatch.setattr(verification.os, "getppid", lambda: -1)
        elif change.startswith(("wall", "mono")) or change in {"nan", "bool"}:
            target = lab.elapsed if change.startswith("mono") else lab.clock
            target[0] = float("nan") if change == "nan" else True if change == "bool" else (
                target[0] + (-1 if change.endswith("backstep") else 10))
        else:
            path = candidate.paths[change]
            path.write_bytes(path.read_bytes() + b" ")
        captured.append(lab.ledger.path.read_bytes())
        if response == "refused":
            raise RuntimeError("PRIVATE refused")
        return proof(config, record)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        assert lab.ledger.path.read_bytes() == captured[0]
        with pytest.raises(ERROR):
            obj.confirm()
        assert_no_replay(obj, state, lab)


@pytest.mark.parametrize("phase_name", ["complete", "failed"])
@pytest.mark.parametrize("change", ["credential", "manifest", "native-state", "ledger-mode",
    "owner", "wall-expired", "mono-backstep", "exception", "interrupt", "exit"])
def test_post_dml_checks_rollback_whole_completion_or_failure(
        lab, candidate, access, monkeypatch, phase_name, change):
    state = expected(candidate)
    before = []

    def verify(config, record):
        before.append(lab.ledger.path.read_bytes())
        if phase_name == "failed":
            raise RuntimeError("PRIVATE transport")
        return proof(config, record)

    name = "_stage_complete" if phase_name == "complete" else "_stage_fail"
    original = getattr(epoch, name)

    def stage(*args, **kwargs):
        result = original(*args, **kwargs)
        if change in {"credential", "manifest"}:
            path = candidate.paths[change]
            path.write_bytes(path.read_bytes() + b" ")
        elif change == "native-state":
            args[0].execute("UPDATE recovery SET revision=revision+1")
        elif change == "ledger-mode":
            lab.ledger.path.chmod(0o644)
        elif change == "owner":
            candidate.captures[-1][1]._active = False
        elif change == "wall-expired":
            lab.clock[0] += 10
        elif change == "mono-backstep":
            lab.elapsed[0] -= 1
        else:
            raise {"exception": RuntimeError, "interrupt": KeyboardInterrupt,
                   "exit": SystemExit}[change]("PRIVATE write")
        return result

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(epoch, name, stage)
    with access() as obj:
        with pytest.raises({"interrupt": KeyboardInterrupt, "exit": SystemExit}.get(change, ERROR)):
            run(obj, state)
        assert lab.ledger.path.read_bytes() == before[0]
        assert phase(lab) == [("claimed",)]
        assert_no_replay(obj, state, lab)


@pytest.mark.parametrize("phase_name", ["complete", "failed"])
@pytest.mark.parametrize("committed", [False, True])
def test_lost_completion_reply_is_not_rewritten_failed_or_replayed(
        lab, candidate, access, monkeypatch, phase_name, committed):
    state = expected(candidate)
    calls, commits = [], []

    def verify(config, record):
        calls.append(record)
        if phase_name == "failed":
            raise RuntimeError("PRIVATE transport")
        return proof(config, record)

    def commit(db):
        commits.append(1)
        if committed:
            db.commit()
        raise RuntimeError("PRIVATE commit reply")

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(verification, "_commit", commit)
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        assert phase(lab) == [(phase_name if committed else "claimed",)]
        if committed:
            assert obj.confirm().phase == phase_name
        else:
            with pytest.raises(ERROR):
                obj.confirm()
        assert_no_replay(obj, state, lab)
        assert len(commits) == len(calls) == 1


@pytest.mark.parametrize("clock", ["wall", "mono"])
def test_whole_operation_budget_cannot_be_renewed_per_phase(
        lab, candidate, access, monkeypatch, clock):
    state = expected(candidate)
    target = lab.clock if clock == "wall" else lab.elapsed

    def consent(review):
        target[0] += 6
        return review

    def verify(config, record):
        target[0] += 4
        return proof(config, record)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as obj, pytest.raises(ERROR):
        run(obj, state, consent=consent)
    assert phase(lab) == [("claimed",)]


@pytest.mark.parametrize("profile", ["dns", "ip"], indirect=True)
def test_real_tls_exact_generation_is_fixed_and_issues_no_session(
        lab, candidate, access, server, monkeypatch):
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=7,
                                        state="active", drained=True)).encode()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    state = expected(candidate)
    with access() as obj:
        assert run(obj, state).phase == "complete"
        assert_no_replay(obj, state, lab)
    assert len(observed) == 1
    path, headers, body = observed[0]
    assert path == "/auth/device/verify"
    assert json.loads(body) == {"device_id": "display", "generation": 7}
    assert headers["Authorization"] == "Bearer " + CREDENTIAL
    assert not {"Cookie", "Origin"} & headers.keys()
    blocked(lab)


@pytest.mark.parametrize("profile", ["dns"], indirect=True)
@pytest.mark.parametrize("failure", ["stale", "paused", "drain", "malformed", "duplicate",
    "redirect", "cookie", "compressed", "401", "429", "503"])
def test_real_tls_refusal_consumes_without_session_or_redirect(
        lab, candidate, access, server, failure):
    _, response, observed = server
    value = dict(version=1, device_id="display", generation=7, state="active", drained=True)
    if failure in {"stale", "paused", "drain"}:
        value.update({"stale": dict(generation=8), "paused": dict(state="paused"),
                      "drain": dict(drained=False)}[failure])
    response["body"] = json.dumps(value).encode()
    if failure == "malformed":
        response["body"] = b"PRIVATE invalid"
    elif failure == "duplicate":
        response["body"] = response["body"][:-1] + b',"generation":7}'
    elif failure == "redirect":
        response.update(status=302, headers=[("Location", "https://example.invalid/private")])
    elif failure == "cookie":
        response["headers"] = [("Set-Cookie", "private=fictional")]
    elif failure == "compressed":
        response["headers"] = [("Content-Encoding", "gzip")]
    elif failure.isdigit():
        response.update(status=int(failure), headers=[("Retry-After", "60")])
    state = expected(candidate)
    with access() as obj:
        with pytest.raises(ERROR) as error:
            run(obj, state)
        assert "PRIVATE" not in str(error.value)
        assert obj.confirm().phase == "failed"
        assert_no_replay(obj, state, lab)
    assert len(observed) == 1 and phase(lab) == [("failed",)]


@pytest.mark.parametrize("profile", ["untrusted", "hostname"], indirect=True)
@pytest.mark.parametrize("server", [1], indirect=True)
def test_real_tls_certificate_failure_never_sends_credentials(lab, candidate, access, server):
    _, _, observed = server
    state = expected(candidate)
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        assert obj.confirm().phase == "failed"
        assert_no_replay(obj, state, lab)
    assert observed == [] and phase(lab) == [("failed",)]


@pytest.mark.parametrize("phase_name", ["complete", "failed"])
def test_failed_rollback_closes_connection_without_partial_state(
        lab, candidate, access, monkeypatch, phase_name):
    state = expected(candidate)
    opened, before = [], []
    connect = sqlite3.connect
    name = "_stage_complete" if phase_name == "complete" else "_stage_fail"
    original = getattr(epoch, name)

    class BrokenRollback(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError("PRIVATE rollback failure")

    def connection(*args, **kwargs):
        if args[0] == lab.ledger.path.as_uri() + "?mode=rw":
            db = connect(*args, **kwargs, factory=BrokenRollback)
            opened.append(db)
            return db
        return connect(*args, **kwargs)

    def stage(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("PRIVATE interrupted stage")

    def verify(config, record):
        before.append(lab.ledger.path.read_bytes())
        monkeypatch.setattr(sqlite3, "connect", connection)
        if phase_name == "failed":
            raise RuntimeError("PRIVATE server")
        return proof(config, record)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(epoch, name, stage)
    with access() as obj, pytest.raises(ERROR):
        run(obj, state)
    assert len(opened) == 1 and lab.ledger.path.read_bytes() == before[0]
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].commit()


@pytest.mark.parametrize("failure", ["reply", "wall-expired", "mono-expired", "interrupt"])
def test_completion_readback_failure_never_rewrites_or_replays(
        lab, candidate, access, monkeypatch, failure):
    state = expected(candidate)
    monkeypatch.setattr(transport, "verify_browser_device", proof)
    with access() as obj:
        original = obj._confirm_owned

        def failed(*args):
            result = original(*args)
            if failure in {"reply", "interrupt"}:
                raise (RuntimeError if failure == "reply" else KeyboardInterrupt)("PRIVATE reply")
            (lab.clock if failure == "wall-expired" else lab.elapsed)[0] += 10
            return result

        with monkeypatch.context() as patch:
            patch.setattr(obj, "_confirm_owned", failed)
            with pytest.raises(KeyboardInterrupt if failure == "interrupt" else ERROR):
                run(obj, state)
        assert phase(lab) == [("complete",)] and obj.confirm().phase == "complete"
        assert_no_replay(obj, state, lab)


@pytest.mark.parametrize("phase_name", ["prepared", "claimed"])
@pytest.mark.parametrize("failure", ["reply", "deadline"])
def test_approval_acknowledgement_failure_cannot_be_adopted_by_online_operation(
        lab, candidate, access, monkeypatch, phase_name, failure):
    state = expected(candidate)
    original = approval._BrowserWorkerApproval._confirm_owned

    def failed(obj, *args):
        result = original(obj, *args)
        if result.phase == phase_name:
            if failure == "reply":
                raise RuntimeError("PRIVATE approval reply")
            lab.elapsed[0] += 10
        return result

    monkeypatch.setattr(approval._BrowserWorkerApproval, "_confirm_owned", failed)
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state)
        assert phase(lab) == [(phase_name,)]
        with pytest.raises(ERROR):
            obj.confirm()
        assert_no_replay(obj, state, lab)


@pytest.mark.parametrize("clock", ["wall", "mono"])
@pytest.mark.parametrize("value", [True, float("nan"), -1, None])
def test_invalid_initial_clock_cannot_mutate_or_contact_server(
        lab, candidate, access, monkeypatch, clock, value):
    state = expected(candidate)
    before = snap(lab)
    (lab.clock if clock == "wall" else lab.elapsed)[0] = value
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    with access() as obj, pytest.raises(ERROR):
        run(obj, state)
    assert snap(lab) == before


@pytest.mark.parametrize("failure", ["cancel", "copy", "stale", "generation", "intent"])
def test_bad_review_never_reaches_network(lab, candidate, access, monkeypatch, failure):
    state = expected(candidate)
    before = snap(lab)
    kwargs = {}
    if failure in {"cancel", "copy"}:
        kwargs["consent"] = lambda review: None if failure == "cancel" else replace(review)
    elif failure == "stale":
        state = replace(state, native_revision=state.native_revision + 1)
    elif failure == "generation":
        kwargs["reviewed_generation"] = True
    else:
        kwargs["intent"] = "not an intent"
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    with access() as obj:
        with pytest.raises(ERROR):
            run(obj, state, **kwargs)
        assert_no_replay(obj, state, lab)
    assert snap(lab) == before
