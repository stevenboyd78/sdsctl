"""Actual framed dispatch with private files/SQL; simulated history and ancestor."""
from __future__ import annotations

import io
import json
import os
import struct
import sys
import tempfile
import time
from dataclasses import replace

import pytest

from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_ownership as ownership
from sds200 import browser_device_native as native
from sds200 import browser_device_verification as transport
from sds200 import browser_device_worker as worker
from sds200.browser_device_protocol import (
    BrowserContinuationReadRequest,
    BrowserDeviceProtocolError,
    parse_browser_device_request,
)
from sds200.browser_device_startup import _launch_lock
from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_current import (
    activation_candidate as activation_candidate,
)
from tests.test_browser_device_continuation_current import active_native
from tests.test_browser_device_continuation_current import candidate as candidate
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_continuation_verification import profile as profile
from tests.test_browser_device_guard_release import blocked
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import frame
from tests.test_browser_device_native import root as root
from tests.test_browser_device_native import server as server
from tests.test_browser_device_profile import CREDENTIAL
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                               reason="Non-root Linux owned continuation dispatch")
FAILURE = {"version": 1, "ok": False, "mode": "setup_error"}


@pytest.fixture
def dispatch(lab, candidate, monkeypatch):
    for module in (current, ownership, worker):
        monkeypatch.setattr(module, "_browser_directory", lambda *_: candidate.root)
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation",
                        lambda *a: pytest.fail("Unexpected session allocation"))
    monkeypatch.setattr(native, "exchange_browser_device",
                        lambda *a: pytest.fail("Unexpected legacy authentication"))

    def invoke(action, *, unwrap=False, extra=None, changes=None):
        request = {"version": 1, "action": action, **(extra or {})}
        envelope = {"version": 1, "action": "worker-context", "build": worker.worker_graph()[0]}
        if action != "worker-context":
            envelope.update(action="worker-request", request=request)
        body = request if unwrap else envelope
        destination = io.BytesIO()
        with _launch_lock(candidate.root, create=False):
            assert native._native_request(lab.args["profile"], [lab.configuration.extension_origin],
                io.BytesIO(frame(body)), destination, **{**dict(
                    expected_identity=lab.configuration.identity, worker=selected),
                    **(changes or {})}) == 0
        return json.loads(destination.getvalue()[4:])

    return invoke


@pytest.mark.parametrize("action", ["worker-context", "continuation-current",
                                    "continuation-review"])
@pytest.mark.parametrize("active", [False, True])
def test_only_fixed_read_only_route_is_selected(
        lab, candidate, dispatch, monkeypatch, action, active):
    if active:
        active_native(lab, candidate)
    calls = []

    def verify(config):
        calls.append(True)
        return transport.BrowserVerifiedRecord(config.identity,
            BrowserDeviceRecord(config.device_id, 19, BrowserDeviceState.ACTIVE), True)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    before = snap(lab)
    result = dispatch(action)
    assert snap(lab) == before and CREDENTIAL not in json.dumps(result)
    if action == "continuation-review":
        assert len(calls) == (0 if active else 1)
        if active:
            assert result == FAILURE
        else:
            assert set(result) == {"version", "ok", "build", "identity", "epoch", "mode", "binding"}
            assert result["mode"] == "paused" and result["binding"]["generation"] == 19
    else:
        assert result["role"] == "continuation" and calls == []
        assert result["continuation"]["mode"] == ("active" if active else "paused")
        assert result["acknowledge"] is False and result["launch"] is None
    blocked(lab)


@pytest.mark.parametrize("action", ["authenticate", "status", "suspend", "claim-browser",
    "review-resume", "prepare-resume", "commit-resume", "continuation-initial-session",
    "continuation-renew", "continuation-recheck"])
def test_no_fallthrough_to_authentication_or_legacy_writes(lab, candidate, dispatch, action):
    before = snap(lab)
    assert dispatch(action) == FAILURE
    assert snap(lab) == before


@pytest.mark.parametrize("action", ["continuation-current", "continuation-review"])
@pytest.mark.parametrize("failure", ["unwrapped", "no-worker", "wrong-identity", "retirement",
    "selection", "missing-anchor", "unsafe-anchor"])
def test_read_route_requires_bound_installed_continuation(
        lab, candidate, dispatch, monkeypatch, action, failure):
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    changes = {}
    if failure == "no-worker":
        changes["worker"] = None
    elif failure == "wrong-identity":
        changes["expected_identity"] = "f" * 64
    elif failure == "retirement":
        changes["retirement"] = native.BrowserRetirementSelection(lab.args["archives"], "e" * 64)
    elif failure == "selection":
        selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
        changes["worker"] = replace(selected, directory=candidate.root)
    elif failure == "missing-anchor":
        path = candidate.paths["manifest"]
        path.rename(path.with_name(path.name + ".retained"))
    elif failure == "unsafe-anchor":
        candidate.paths["manifest"].chmod(0o644)
    before = snap(lab)
    assert dispatch(action, unwrap=failure == "unwrapped", changes=changes) == FAILURE
    assert snap(lab) == before


@pytest.mark.parametrize("action", ["continuation-current", "continuation-review"])
@pytest.mark.parametrize("extra", [{"role": "normal"}, {"generation": 7}, {"directory": "/tmp"},
    {"origin": "https://other.example"}, {"identity": "f" * 64}, {"proof": {}},
    {"token": "secret"}])
def test_read_parser_never_accepts_caller_authority_or_selection(action, extra):
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps({"version": 1, "action": action, **extra}).encode())


@pytest.mark.parametrize("action", ["continuation-current", "continuation-review"])
def test_read_parser_is_exact_and_inert(action):
    result = parse_browser_device_request(json.dumps({"version": 1, "action": action}).encode())
    assert type(result) is BrowserContinuationReadRequest and result.action == action


@pytest.mark.parametrize("profile", ["dns", "ip"], indirect=True)
def test_framed_review_uses_verified_fixed_tls_without_issuing_cookie(
        lab, candidate, dispatch, server):
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=19,
                                        state="active", drained=True)).encode()
    before = snap(lab)
    result = dispatch("continuation-review")
    assert result["ok"] is True and result["binding"]["generation"] == 19
    assert result["mode"] == "paused" and snap(lab) == before
    assert len(observed) == 1
    path, headers, body = observed[0]
    assert path == "/auth/device/verify" and json.loads(body) == {"device_id": "display"}
    assert headers["Authorization"] == "Bearer " + CREDENTIAL
    assert not {"Cookie", "Origin"} & headers.keys()


def test_real_ten_second_supervisor_reaps_stalled_continuation_review(
        lab, candidate, dispatch, monkeypatch):
    assert native._TOTAL_SECONDS == 10
    read_fd, write_fd = os.pipe()

    def blocked_review(configuration):
        os.write(write_fd, struct.pack("=I", os.getpid()))
        time.sleep(30)  # The independent real supervisor must terminate this child.
        raise RuntimeError("Unreachable fictional review")

    monkeypatch.setattr(transport, "verify_browser_device", blocked_review)
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    request = {"version": 1, "action": "worker-request", "build": worker.worker_graph()[0],
               "request": {"version": 1, "action": "continuation-review"}}
    before = snap(lab)
    try:
        with (tempfile.TemporaryFile(buffering=0) as source,
              tempfile.TemporaryFile(buffering=0) as destination,
              _launch_lock(candidate.root, create=False)):
            source.write(frame(request))
            source.seek(0)
            started = time.monotonic()
            code = native.run_browser_native(lab.args["profile"],
                [lab.configuration.extension_origin], source, destination,
                expected_identity=lab.configuration.identity, worker=selected)
            elapsed = time.monotonic() - started
            assert code == 2 and 9.5 <= elapsed < 13
            os.set_blocking(read_fd, False)
            child, = struct.unpack("=I", os.read(read_fd, 4))  # Proves review was reached.
            with pytest.raises(ChildProcessError):
                os.waitpid(child, os.WNOHANG)
            destination.seek(0)
            assert destination.read() == b""
        assert snap(lab) == before
    finally:
        os.close(read_fd)
        os.close(write_fd)
