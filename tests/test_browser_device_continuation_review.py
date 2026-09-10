"""Real private SQL/files and selected verified TLS; simulated browser ancestry.

No ordinary dispatcher, browser gesture or accepted session is enabled here.
The retained namespace-chain qualification is separate from these fixtures.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError, replace

import pytest

from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_continuation_review as review
from sds200 import browser_device_verification as transport
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from sds200.browser_device_worker import BrowserWorkerSelection, worker_graph
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_current import (
    activation_candidate as activation_candidate,
)
from tests.test_browser_device_continuation_current import active_native, native_step
from tests.test_browser_device_continuation_current import candidate as candidate
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_continuation_verification import profile as profile
from tests.test_browser_device_continuation_verification import proof
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_native import server as server
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned paused-review fixtures")
ERROR = review.BrowserContinuationReviewError


@pytest.fixture
def access(lab, candidate, monkeypatch):
    lab.clock[0] += 2
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda *a: pytest.fail("Unexpected session allocation"))

    @contextmanager
    def scope():
        obj = review._BrowserWorkerPausedReview(lab.configuration, selection,
            clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
        with _launch_lock(candidate.root, create=False):
            yield obj

    return scope


def no_replay(obj, lab):
    before = snap(lab)
    with pytest.raises(ERROR):
        obj.run()
    assert snap(lab) == before
    assert not hasattr(obj, "confirm")


@pytest.mark.parametrize("delay", [0, 1, 9.9])
@pytest.mark.parametrize("generation", [1, 7, 2**53 - 2])
def test_review_keeps_native_paused_and_sql_closed_during_network(
        lab, candidate, access, monkeypatch, delay, generation):
    before = snap(lab)
    calls = []

    def verify(config):
        assert config == lab.configuration
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        for name in ("profile", "archives"):
            with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                    lab.args[name], exclusive=True):
                pytest.fail("Private input writer acquired during review")
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing launcher")
        calls.append(True)
        lab.clock[0] += delay
        lab.elapsed[0] += delay
        return proof(config, BrowserDeviceRecord(config.device_id, generation,
                                               BrowserDeviceState.ACTIVE))

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as obj:
        result = obj.run()
        assert result.state.mode is RecoveryMode.PAUSED and result.generation == generation
        assert result.state.native_revision == candidate.activated.native_revision
        assert len(calls) == 1 and snap(lab) == before
        for value in (CREDENTIAL, str(candidate.root), result.state.identity,
                      result.state.epoch, result.state.state_fingerprint):
            assert value not in repr(result) + repr(obj)
        for field in ("token", "session", "session_ready", "proof", "accepted", "consent"):
            assert not hasattr(result, field)
        with pytest.raises(FrozenInstanceError):
            result.generation = 8
        no_replay(obj, lab)
    blocked(lab)


@pytest.mark.parametrize("failure", ["exception", "interrupt", "exit", "none", "identity",
    "record", "device", "zero", "negative", "bool", "string", "float", "large", "paused",
    "drained"])
def test_refused_or_lost_review_preserves_all_native_state(
        lab, candidate, access, monkeypatch, failure):
    calls = []

    def verify(config):
        calls.append(True)
        if failure in {"exception", "interrupt", "exit"}:
            raise {"exception": RuntimeError, "interrupt": KeyboardInterrupt,
                   "exit": SystemExit}[failure]("PRIVATE " + CREDENTIAL)
        result = proof(config, BrowserDeviceRecord(config.device_id, 7, BrowserDeviceState.ACTIVE))
        if failure == "none":
            return None
        if failure in {"identity", "drained", "record"}:
            return replace(result, **{"identity": dict(identity="f" * 64),
                "drained": dict(drained=1), "record": dict(record={})}[failure])
        change = {"device": dict(device_id="other"), "zero": dict(generation=0),
            "negative": dict(generation=-1), "bool": dict(generation=True),
            "string": dict(generation="7"), "float": dict(generation=7.0),
            "large": dict(generation=2**53 - 1), "paused": dict(state=BrowserDeviceState.PAUSED)}
        return replace(result, record=replace(result.record, **change[failure]))

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    before = snap(lab)
    with access() as obj:
        with pytest.raises({"interrupt": KeyboardInterrupt, "exit": SystemExit}.get(
                failure, ERROR)) as error:
            obj.run()
        if failure not in {"interrupt", "exit"}:
            assert CREDENTIAL not in str(error.value) and "PRIVATE" not in str(error.value)
        assert len(calls) == 1 and snap(lab) == before
        no_replay(obj, lab)


@pytest.mark.parametrize("change", ["pause", "same-revision", "credential", "trust", "bundle",
    "manifest", "release", "intent", "ledger-inode", "owner-inode", "owner", "parent",
    "wall-backstep", "mono-backstep", "wall-expired", "mono-expired", "nan", "bool"])
@pytest.mark.parametrize("response", ["valid", "refused"])
def test_changed_inputs_or_newer_pause_win_without_repair(
        lab, candidate, access, monkeypatch, change, response):
    captured = []
    if change == "same-revision":
        # The zero-step checkpoint is deliberately immutable. Advance with an
        # actual native pause first, then change the full state at that revision.
        native_step(lab, candidate, "pause")

    def verify(config):
        if change == "pause":
            native_step(lab, candidate, "pause")
        elif change == "same-revision":
            selection = epoch._EpochSelection(candidate.paths["manifest"].read_bytes(),
                current.activation._binding(candidate.paths["manifest"]), candidate.history,
                lab.args["profile"], current.activation._binding(lab.ledger.path))
            with current.activation._ledger_transaction(
                    lab.args["profile"], readonly=False) as (db, _):
                view = epoch._read(db, selection, readonly=False)
                changed = epoch._write(db, selection, view, view.approvals,
                    {**view.state, "observed_at": view.state["observed_at"] + 1}, view.grant)
                assert changed.revision == view.snapshot.revision
                assert changed.fingerprint != view.snapshot.fingerprint
                db.commit()
        elif change in {"ledger-inode", "owner-inode"}:
            path = lab.ledger.path if change == "ledger-inode" else (
                candidate.root / ".sdsctl-device-launch.lock")
            path.rename(path.with_name(path.name + ".retained"))
            private(path, path.with_name(path.name + ".retained").read_bytes())
        elif change == "owner":
            candidate.captures[-1][1]._active = False
        elif change == "parent":
            monkeypatch.setattr(review.os, "getppid", lambda: -1)
        elif change.startswith(("wall", "mono")) or change in {"nan", "bool"}:
            target = lab.elapsed if change.startswith("mono") else lab.clock
            target[0] = float("nan") if change == "nan" else True if change == "bool" else (
                target[0] + (-1 if change.endswith("backstep") else 10))
        else:
            path = candidate.paths[change]
            path.write_bytes(path.read_bytes() + b" ")
        captured.append(lab.ledger.path.read_bytes())
        if response == "refused":
            raise RuntimeError("PRIVATE lost review reply")
        return proof(config, BrowserDeviceRecord(config.device_id, 7, BrowserDeviceState.ACTIVE))

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as obj, pytest.raises(ERROR):
        obj.run()
    assert len(captured) == 1 and lab.ledger.path.read_bytes() == captured[0]
    no_replay(obj, lab)


@pytest.mark.parametrize("change", ["identity", "origin", "device", "epoch", "manifest",
    "fingerprint", "revision-bool", "revision-large", "mode", "mode-string", "generation"])
def test_malformed_current_read_never_authorizes_network(
        lab, candidate, access, monkeypatch, change):
    original = current._CurrentRead.observe
    changes = {"identity": ("identity", "f" * 64),
        "origin": ("origin", "https://different.example"), "device": ("device_id", "other"),
        "epoch": ("epoch", "bad"), "manifest": ("manifest_sha256", "bad"),
        "fingerprint": ("state_fingerprint", "bad"), "revision-bool": ("native_revision", True),
        "revision-large": ("native_revision", 2**53), "mode": ("mode", RecoveryMode.REJECTED),
        "mode-string": ("mode", "paused")}
    reached = []

    def observe(reader):
        result = original(reader)
        reached.append(True)
        if change == "generation":
            return replace(result, generation=7)
        key, value = changes[change]
        return replace(result, state=replace(result.state, **{key: value}))

    monkeypatch.setattr(current._CurrentRead, "observe", observe)
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    before = snap(lab)
    with access() as obj, pytest.raises(ERROR):
        obj.run()
    assert reached == [True] and snap(lab) == before


@pytest.mark.parametrize("failure", ["active", "owner-exit", "exit-error", "graph"])
def test_no_success_after_active_selection_or_failed_owner_exit(
        lab, candidate, access, monkeypatch, failure):
    calls = []
    if failure == "active":
        active_native(lab, candidate)
    elif failure in {"owner-exit", "exit-error"}:
        original = ownership._worker_history_ownership

        @contextmanager
        def changed(*args, **kwargs):
            with original(*args, **kwargs) as owner:
                yield owner
                if failure == "exit-error":
                    raise RuntimeError("PRIVATE owner scope exit")
                candidate.paths["manifest"].chmod(0o644)

        monkeypatch.setattr(ownership, "_worker_history_ownership", changed)
    else:
        graph = worker_graph()
        replies = iter([graph, ("f" * 64, graph[1])])
        monkeypatch.setattr(review, "worker_graph", lambda: next(replies))

    def verify(config):
        calls.append(True)
        return proof(config, BrowserDeviceRecord(config.device_id, 7, BrowserDeviceState.ACTIVE))

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    before = snap(lab)
    with access() as obj, pytest.raises(ERROR):
        obj.run()
    assert len(calls) == (0 if failure == "active" else 1)
    if failure != "owner-exit":
        assert snap(lab) == before


@pytest.mark.parametrize("profile", ["dns", "ip"], indirect=True)
def test_real_tls_reviews_current_generation_without_approval_or_session(
        lab, candidate, access, server, monkeypatch):
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=19,
                                        state="active", drained=True)).encode()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    before = snap(lab)
    with access() as obj:
        result = obj.run()
        assert result.generation == 19 and result.state.mode is RecoveryMode.PAUSED
        no_replay(obj, lab)
    assert snap(lab) == before and len(observed) == 1
    path, headers, body = observed[0]
    assert path == "/auth/device/verify" and json.loads(body) == {"device_id": "display"}
    assert headers["Authorization"] == "Bearer " + CREDENTIAL
    assert not {"Cookie", "Origin"} & headers.keys()
    blocked(lab)


@pytest.mark.parametrize("profile", ["dns"], indirect=True)
@pytest.mark.parametrize("failure", ["paused", "drain", "malformed", "duplicate", "redirect",
    "cookie", "compressed", "401", "429", "503"])
def test_real_tls_refusal_never_retries_or_changes_native_state(
        lab, candidate, access, server, failure):
    _, response, observed = server
    value = dict(version=1, device_id="display", generation=7, state="active", drained=True)
    if failure in {"paused", "drain"}:
        value.update({"paused": dict(state="paused"), "drain": dict(drained=False)}[failure])
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
    before = snap(lab)
    with access() as obj:
        with pytest.raises(ERROR) as error:
            obj.run()
        assert "PRIVATE" not in str(error.value)
        no_replay(obj, lab)
    assert snap(lab) == before and len(observed) == 1
