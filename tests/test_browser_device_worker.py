"""Strict worker context/build envelopes; no browser-selected authority or paths."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from sds200 import browser_device_native as native
from sds200 import browser_device_worker as worker
from sds200.browser_device_protocol import (
    BrowserDeviceProtocolError,
    BrowserWorkerContextRequest,
    BrowserWorkerRequest,
    parse_browser_device_request,
)
from sds200.browser_device_startup import _launch_lock
from tests.test_browser_device_bundle import create
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import frame
from tests.test_browser_device_profile import snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_startup import inputs as inputs

FAILURE = {"version": 1, "ok": False, "mode": "setup_error"}


def envelope(action="worker-context", **extra):
    return {"version": 1, "action": action, "build": worker.worker_graph()[0], **extra}


def test_fixed_graph_and_all_imports_are_present():
    digest, graph = worker.worker_graph()
    assert re.fullmatch("[a-f0-9]{64}", digest)
    assert worker.worker_graph() == (digest, graph)
    for name, body in graph.items():
        for imported in re.findall(rb"from ['\"]\./([^'\"]+)['\"]", body):
            assert "extension/" + imported.decode() in graph, name
    assert digest.encode() in graph["extension/worker.mjs"]
    assert b"manifest" not in graph["extension/worker.mjs"]


def test_acceptance_core_is_build_bound_but_not_imported_by_active_worker(monkeypatch):
    name = "browser_device_continuation_state.mjs"
    digest, graph = worker.worker_graph()
    assert "extension/" + name in graph
    for path, body in graph.items():
        if path != "extension/" + name:
            assert name.encode() not in body
    monkeypatch.setattr(worker, "MODULES", tuple(n for n in worker.MODULES if n != name))
    assert worker.worker_graph()[0] != digest


@pytest.mark.parametrize("action", ["continuation-initial-session", "continuation-accepted",
                                    "continuation-status", "continuation-renew",
                                    "continuation-observe", "continuation-recheck"])
def test_inert_acceptance_core_does_not_expose_a_native_request(action):
    for body in ({"version": 1, "action": action}, envelope("worker-request",
            request={"version": 1, "action": action})):
        with pytest.raises(BrowserDeviceProtocolError):
            parse_browser_device_request(json.dumps(body).encode())


@pytest.mark.parametrize("action", ["worker-context", "worker-request"])
def test_exact_worker_envelope(action):
    body = envelope(action, **({"request": {"version": 1, "action": "status"}}
                              if action == "worker-request" else {}))
    result = parse_browser_device_request(json.dumps(body).encode())
    assert isinstance(result, BrowserWorkerContextRequest if action == "worker-context"
                      else BrowserWorkerRequest)
    assert result.build == body["build"]


@pytest.mark.parametrize("field", ["role", "origin", "directory", "identity", "operation_id"])
@pytest.mark.parametrize("action", ["worker-context", "worker-request"])
def test_context_cannot_select_authority(field, action):
    body = envelope(action, **{field: "caller-choice"},
        **({"request": {"version": 1, "action": "status"}} if action == "worker-request" else {}))
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps(body).encode())


@pytest.mark.parametrize("inner", [None, {}, [], {"version": 1, "action": []},
    {"version": 1, "action": "worker-context", "build": "f" * 64},
    {"version": 1, "action": "worker-request", "build": "f" * 64, "request": {}},
    {"version": 1, "action": "authenticate", "origin": "https://other.example"}])
def test_nested_and_unknown_requests_refused(inner):
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps(envelope("worker-request", request=inner)).encode())


@pytest.mark.parametrize("build", [None, True, 1, "", "f" * 63, "F" * 64, [], {}])
def test_malformed_build_refused(build):
    body = envelope()
    body["build"] = build
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps(body).encode())


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux")
@pytest.mark.parametrize("kind", ["context", "request", "legacy"])
def test_generated_host_refuses_old_worker_without_accessing_state(
        tmp_path, public_key, profile, kind):
    bundle = create(tmp_path, public_key, profile)
    config = native.load_browser_native_configuration(profile)
    body = envelope()
    if kind == "request":
        body = envelope("worker-request", request={"version": 1, "action": "authenticate"})
    body["build"] = "0" * 64
    if kind == "legacy":
        body = {"version": 1, "action": "authenticate"}
    before = snapshot(profile)
    result = subprocess.run([str(bundle / "native-host"), config.extension_origin],
        input=frame(body), capture_output=True, timeout=13)
    assert result.returncode == 0 and result.stderr == b""
    assert json.loads(result.stdout[4:]) == FAILURE
    assert snapshot(profile) == before


def test_normal_context_requires_canonical_owned_registration(inputs, monkeypatch):
    config = native.load_browser_native_configuration(inputs["profile"])
    selected = worker.BrowserWorkerSelection(inputs["bundle"], inputs["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: inputs["root"])
    before = snapshot(inputs["profile"]), snapshot(inputs["bundle"])
    with _launch_lock(inputs["root"]):
        response = worker.worker_context(config, selected, None)
        assert response == worker.context_document(config, role="normal", acknowledge=False,
                                                   launch=None)
    assert (snapshot(inputs["profile"]), snapshot(inputs["bundle"])) == before
    with pytest.raises(ValueError):
        worker.worker_context(config, selected, None)  # No live launch owner.


def test_context_without_browser_ancestor_is_refused(tmp_path, public_key, profile):
    bundle = create(tmp_path, public_key, profile)
    config = native.load_browser_native_configuration(profile)
    selected = worker.BrowserWorkerSelection(bundle, public_key)
    before = snapshot(profile)
    destination = io.BytesIO()
    assert native._native_request(profile, [config.extension_origin],
        io.BytesIO(frame(envelope())), destination, expected_identity=config.identity,
        worker=selected) == 0
    assert json.loads(destination.getvalue()[4:]) == FAILURE
    assert snapshot(profile) == before


@pytest.mark.parametrize("action,mode", [("status", "active"), ("claim-browser", "active"),
                                      ("suspend", "paused")])
def test_owned_unguarded_worker_request_keeps_ordinary_behavior(inputs, monkeypatch, action, mode):
    config = native.load_browser_native_configuration(inputs["profile"])
    selected = worker.BrowserWorkerSelection(inputs["bundle"], inputs["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: inputs["root"])
    request = envelope("worker-request", request={"version": 1, "action": action})
    destination = io.BytesIO()
    with _launch_lock(inputs["root"]):
        assert native._native_request(inputs["profile"], [config.extension_origin],
            io.BytesIO(frame(request)), destination, expected_identity=config.identity,
            worker=selected) == 0
    response = json.loads(destination.getvalue()[4:])
    assert response["ok"] is True and response["mode"] == mode


def test_owned_unguarded_worker_still_allows_fresh_review_prepare_commit(inputs, monkeypatch):
    from sds200 import browser_device_resume as resume
    from sds200 import browser_device_verification as verification
    from sds200.browser_device_recovery import BrowserDeviceRecovery, ExchangeSession
    from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
    from sds200.browser_device_verification import BrowserVerifiedRecord
    from tests.test_browser_device_native import TOKEN

    config = native.load_browser_native_configuration(inputs["profile"])
    selected = worker.BrowserWorkerSelection(inputs["bundle"], inputs["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: inputs["root"])
    ledger = BrowserDeviceRecovery(inputs["profile"] / "recovery.sqlite", config.identity)
    ledger.claim_browser()
    ledger.suspend()
    record = BrowserDeviceRecord(config.device_id, 1, BrowserDeviceState.ACTIVE)
    calls = []

    def verified(configuration, expected=None):
        assert configuration == config and expected in (None, record)
        calls.append("verification")
        return BrowserVerifiedRecord(config.identity, record, True)

    def exchange(configuration, generation):
        assert configuration == config and generation == 1
        calls.append("session")
        return ExchangeSession(TOKEN, 300)

    monkeypatch.setattr(resume, "verify_browser_device", verified)
    monkeypatch.setattr(verification, "verify_browser_device", verified)
    monkeypatch.setattr(resume, "exchange_browser_device_at_generation", exchange)

    def invoke(action, **fields):
        destination = io.BytesIO()
        request = envelope("worker-request", request={"version": 1, "action": action, **fields})
        assert native._native_request(inputs["profile"], [config.extension_origin],
            io.BytesIO(frame(request)), destination, expected_identity=config.identity,
            worker=selected) == 0
        return json.loads(destination.getvalue()[4:])

    with _launch_lock(inputs["root"]):
        review = invoke("review-resume")
        assert review["ok"] is True and review["mode"] == "paused"
        prepared = invoke("prepare-resume", intent="e" * 64,
                          revision=review["revision"], generation=review["generation"])
        assert prepared["ok"] is True
        result = invoke("commit-resume", intent="e" * 64, **prepared["approval"])
        ready = result.get("ok") is True and result.get("mode") == "active"
        assert ready and calls == ["verification", "verification", "verification", "session"]
        assert worker.worker_context(config, selected, None)["role"] == "normal"


@pytest.mark.parametrize("change", ["no-owner", "new-guard", "wrong-bundle", "no-ancestor"])
def test_request_rechecks_authority_after_successful_worker_context(inputs, monkeypatch, change):
    from sds200.browser_device_registration import MAINTENANCE_MARKER

    config = native.load_browser_native_configuration(inputs["profile"])
    selected = worker.BrowserWorkerSelection(inputs["bundle"], inputs["public_key"])
    monkeypatch.setattr(worker, "_browser_directory", lambda *_: inputs["root"])
    lock = _launch_lock(inputs["root"])
    lock.__enter__()
    try:
        assert worker.worker_context(config, selected, None)["role"] == "normal"
        if change == "no-owner":
            lock.__exit__(None, None, None)
            lock = None
        elif change == "new-guard":
            (inputs["root"] / MAINTENANCE_MARKER).write_bytes(b"unconfirmed")
        elif change == "wrong-bundle":
            selected = worker.BrowserWorkerSelection(inputs["bundle"].parent, inputs["public_key"])
        else:
            def missing(*_):
                raise ValueError()
            monkeypatch.setattr(worker, "_browser_directory", missing)
        before = snapshot(inputs["profile"])
        destination = io.BytesIO()
        request = envelope("worker-request", request={"version": 1, "action": "claim-browser"})
        assert native._native_request(inputs["profile"], [config.extension_origin],
            io.BytesIO(frame(request)), destination, expected_identity=config.identity,
            worker=selected) == 0
        assert json.loads(destination.getvalue()[4:]) == FAILURE
        assert snapshot(inputs["profile"]) == before
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)


@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("space", [False, True])
@pytest.mark.parametrize("page", ["setup.html", "startup.html"])
def test_fixed_chromium_tail_supports_flattened_titles_and_literal_spaces(flat, space, page):
    root = Path("/fictional/browser with spaces" if space else "/fictional/browser")
    bundle = Path("/fictional/bundle's literal $()" if space else "/fictional/bundle")
    origin = "chrome-extension://" + "a" * 32 + "/"
    args = ["/usr/lib/chromium/chromium", "--load-extension", "--use-angle=gles", "--kiosk",
        "--user-data-dir=" + str(root), "--load-extension=" + str(bundle / "extension"),
        "--disable-extensions-except=" + str(bundle / "extension"), origin + page]
    raw = ((" " if flat else "\0").join(args) + "\0").encode()
    assert worker._selected_directory(raw, bundle, origin) == root


@pytest.mark.parametrize("change", ["other-bundle", "earlier-load", "earlier-root", "other-page",
    "ambiguous-path", "relative", "missing-nul", "oversize", "extra-tail"])
def test_ambiguous_or_noncanonical_chromium_titles_refused(change):
    origin = "chrome-extension://" + "a" * 32 + "/"
    bundle = Path("/fictional/bundle")
    prefix = "/usr/lib/chromium/chromium --kiosk"
    root = "/fictional/browser"
    if change == "earlier-load":
        prefix += " --load-extension=/other"
    if change == "earlier-root":
        prefix += " --user-data-dir=/other"
    if change == "ambiguous-path":
        root += " --load-extension=misleading"
    if change == "relative":
        root = "relative"
    raw = (prefix + " --user-data-dir=" + root + " --load-extension="
        + str(bundle / "extension") + " --disable-extensions-except="
        + str(bundle / "extension") + " " + origin + "startup.html\0").encode()
    if change == "other-bundle":
        raw = raw.replace(b"--load-extension=/fictional/bundle", b"--load-extension=/other")
    if change == "other-page":
        raw = raw.replace(b"startup.html", b"recovery.html")
    if change == "missing-nul":
        raw = raw[:-1]
    if change == "extra-tail":
        raw = raw[:-1] + b" other\0"
    if change == "oversize":
        raw = b"a" * 32769 + b"\0"
    with pytest.raises(ValueError):
        worker._selected_directory(raw, bundle, origin)
