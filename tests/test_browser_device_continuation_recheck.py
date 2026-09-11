"""Actual private files/SQL; fixture-owned ancestry/history, no browser acceptance."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from dataclasses import replace

import pytest

from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_epoch as epoch
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_continuation_recheck as recheck
from sds200 import browser_device_verification as transport
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from sds200.browser_device_worker import BrowserWorkerSelection
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
                               reason="Non-root Linux owned active-verification fixtures")
ERROR = recheck.BrowserContinuationRecheckError


@pytest.fixture
def access(lab, candidate, monkeypatch):
    active_native(lab, candidate)
    lab.clock[0] += 2
    selection = BrowserWorkerSelection(candidate.args["bundle"], candidate.args["public_key"])
    monkeypatch.setattr(current, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(ownership, "_browser_directory", lambda *_: candidate.root)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda *a: pytest.fail("Unexpected session allocation"))

    @contextmanager
    def scope():
        obj = recheck._BrowserWorkerActiveVerification(lab.configuration, selection,
            clock=lambda: lab.clock[0], monotonic=lambda: lab.elapsed[0])
        with _launch_lock(candidate.root, create=False):
            expected = current._observe_worker_continuation(lab.configuration, selection)
            yield obj, expected

    return scope


def no_replay(obj, expected, lab):
    before = snap(lab)
    with pytest.raises(ERROR):
        obj.run(expected)
    assert snap(lab) == before
    assert not hasattr(obj, "confirm")


@pytest.mark.parametrize("delay", [0, 1, 9.9])
def test_exact_active_verification_is_native_read_only_and_keeps_sql_closed(
        lab, candidate, access, monkeypatch, delay):
    before = snap(lab)
    calls = []

    def verify(config, record):
        assert config == lab.configuration
        assert record == BrowserDeviceRecord(config.device_id, 7, BrowserDeviceState.ACTIVE)
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        for name in ("profile", "archives"):
            with pytest.raises(BrowserProfileAccessError), browser_profile_access(
                    lab.args[name], exclusive=True):
                pytest.fail("Private input writer acquired during network")
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing launcher")
        calls.append(record)
        lab.clock[0] += delay
        lab.elapsed[0] += delay
        return proof(config, record)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as (obj, expected):
        result = obj.run(expected)
        assert result == expected and result is not expected
        assert len(calls) == 1 and snap(lab) == before
        for value in (CREDENTIAL, str(candidate.root), result.state.identity,
                      result.state.epoch, result.state.state_fingerprint):
            assert value not in repr(result) + repr(obj)
        for field in ("token", "session", "session_ready", "proof", "accepted"):
            assert not hasattr(result, field)
        no_replay(obj, expected, lab)
    blocked(lab)


@pytest.mark.parametrize("change", ["none", "dict", "bool-generation", "null-generation",
    "generation", "identity", "fingerprint", "revision", "bool-revision", "paused",
    "origin", "device", "epoch", "manifest"])
def test_supplied_state_is_comparison_not_authority(lab, candidate, access, monkeypatch, change):
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    before = snap(lab)
    with access() as (obj, expected):
        if change == "none":
            value = None
        elif change == "dict":
            value = dict(state=expected.state, generation=expected.generation)
        elif change in {"bool-generation", "null-generation", "generation"}:
            value = replace(expected, generation={"bool-generation": True,
                "null-generation": None, "generation": 8}[change])
        else:
            key, value = {"identity": ("identity", "f" * 64),
                "fingerprint": ("state_fingerprint", "f" * 64),
                "revision": ("native_revision", expected.state.native_revision + 1),
                "bool-revision": ("native_revision", True), "paused": ("mode", RecoveryMode.PAUSED),
                "origin": ("origin", "https://example.invalid"), "device": ("device_id", "other"),
                "epoch": ("epoch", "f" * 64), "manifest": ("manifest_sha256", "f" * 64)}[change]
            value = replace(expected, state=replace(expected.state, **{key: value}))
        with pytest.raises(ERROR):
            obj.run(value)
        assert snap(lab) == before
        no_replay(obj, expected, lab)


@pytest.mark.parametrize("failure", ["exception", "none", "identity", "device", "generation",
    "bool-generation", "paused", "drained", "interrupt", "exit"])
def test_refusal_or_lost_proof_never_rewrites_the_active_grant(
        lab, candidate, access, monkeypatch, failure):
    calls = []

    def verify(config, record):
        calls.append(record)
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
        return replace(result, record=replace(record, **{
            "device": dict(device_id="other"), "generation": dict(generation=8),
            "bool-generation": dict(generation=True),
            "paused": dict(state=BrowserDeviceState.PAUSED)}[failure]))

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    before = snap(lab)
    with access() as (obj, expected):
        with pytest.raises({"interrupt": KeyboardInterrupt, "exit": SystemExit}.get(
                failure, ERROR)) as error:
            obj.run(expected)
        if failure not in {"interrupt", "exit"}:
            assert CREDENTIAL not in str(error.value) and "PRIVATE" not in str(error.value)
        assert len(calls) == 1 and snap(lab) == before
        no_replay(obj, expected, lab)


@pytest.mark.parametrize("change", ["pause", "same-revision", "credential", "trust", "bundle",
    "manifest", "release", "intent", "ledger-inode", "owner-inode", "owner", "parent",
    "wall-backstep", "mono-backstep", "wall-expired", "mono-expired", "nan", "bool"])
@pytest.mark.parametrize("response", ["valid", "refused"])
def test_changed_state_or_owner_during_network_is_preserved_and_refused(
        lab, candidate, access, monkeypatch, change, response):
    captured = []

    def verify(config, record):
        if change == "pause":
            native_step(lab, candidate, "pause")
        elif change == "same-revision":
            selection = epoch._EpochSelection(candidate.paths["manifest"].read_bytes(),
                current.activation._binding(candidate.paths["manifest"]), candidate.history,
                lab.args["profile"], current.activation._binding(lab.ledger.path))
            with current.activation._ledger_transaction(
                    lab.args["profile"], readonly=False) as pair:
                db, _ = pair
                view = epoch._read(db, selection, readonly=False)
                epoch._write(db, selection, view, view.approvals,
                    {**view.state, "observed_at": lab.clock[0] + 1}, view.grant)
                db.commit()
        elif change in {"ledger-inode", "owner-inode"}:
            path = lab.ledger.path if change == "ledger-inode" else (
                candidate.root / ".sdsctl-device-launch.lock")
            saved = path.with_name(path.name + ".retained")
            path.rename(saved)
            private(path, saved.read_bytes())
        elif change == "owner":
            candidate.captures[-1][1]._active = False
        elif change == "parent":
            monkeypatch.setattr(recheck.os, "getppid", lambda: -1)
        elif change.startswith(("wall", "mono")) or change in {"nan", "bool"}:
            target = lab.elapsed if change.startswith("mono") else lab.clock
            target[0] = float("nan") if change == "nan" else True if change == "bool" else (
                target[0] + (-1 if change.endswith("backstep") else 10))
        else:
            path = candidate.paths[change]
            path.write_bytes(path.read_bytes() + b" ")
        captured.append(snap(lab))
        if response == "refused":
            raise RuntimeError("PRIVATE refused")
        return proof(config, record)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as (obj, expected):
        with pytest.raises(ERROR):
            obj.run(expected)
        assert snap(lab) == captured[0]
        no_replay(obj, expected, lab)


@pytest.mark.parametrize("clock", ["wall", "mono"])
def test_budget_spans_native_reads_and_network(lab, candidate, access, monkeypatch, clock):
    target = lab.clock if clock == "wall" else lab.elapsed
    original = current._CurrentRead.observe

    def observe(reader):
        result = original(reader)
        target[0] += 6
        return result

    def verify(config, record):
        target[0] += 4
        return proof(config, record)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    with access() as (obj, expected):
        monkeypatch.setattr(current._CurrentRead, "observe", observe)
        before = snap(lab)
        with pytest.raises(ERROR):
            obj.run(expected)
        assert snap(lab) == before
        no_replay(obj, expected, lab)


@pytest.mark.parametrize("profile", ["dns", "ip"], indirect=True)
def test_real_tls_uses_only_fixed_origin_and_exact_generation(
        lab, candidate, access, server, monkeypatch):
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=7,
                                        state="active", drained=True)).encode()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    before = snap(lab)
    with access() as (obj, expected):
        assert obj.run(expected) == expected
        assert snap(lab) == before
        no_replay(obj, expected, lab)
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
def test_real_tls_refusal_preserves_native_state_without_replay(
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
    before = snap(lab)
    with access() as (obj, expected):
        with pytest.raises(ERROR) as error:
            obj.run(expected)
        assert "PRIVATE" not in str(error.value) and CREDENTIAL not in str(error.value)
        assert snap(lab) == before and len(observed) == 1
        no_replay(obj, expected, lab)


def test_lost_live_scope_is_not_replaced_by_saved_observation(lab, candidate, access, monkeypatch):
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    with access() as (obj, expected):
        pass
    before = snap(lab)
    with pytest.raises(ERROR):
        obj.run(expected)
    assert snap(lab) == before
    no_replay(obj, expected, lab)


@pytest.mark.parametrize("name", ["credential", "trust", "manifest"])
def test_stale_private_inputs_are_rejected_before_network(
        lab, candidate, access, monkeypatch, name):
    # Pre-entry journal changes need the complete historical digest, which this
    # portable fixture intentionally simulates. The real namespace chains cover
    # release/intent changes before run; portable during-I/O cases still exercise
    # the real same-attempt byte pinning for both journals.
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    with access() as (obj, expected):
        path = candidate.paths[name]
        path.write_bytes(path.read_bytes() + b" ")
        before = snap(lab)
        with pytest.raises(ERROR):
            obj.run(expected)
        assert snap(lab) == before
        no_replay(obj, expected, lab)
