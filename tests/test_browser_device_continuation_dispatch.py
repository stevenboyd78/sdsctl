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
    BrowserContinuationInitialRequest,
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
from tests.test_browser_device_native import TOKEN, frame
from tests.test_browser_device_native import certificates as certificates
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
ACTUAL_EXCHANGE = transport.exchange_browser_device_at_generation


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


@pytest.mark.parametrize("action", ["continuation-current", "continuation-review",
                                    "continuation-verify-active"])
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


@pytest.mark.parametrize("action", ["continuation-current", "continuation-review",
                                    "continuation-verify-active"])
@pytest.mark.parametrize("extra", [{"role": "normal"}, {"generation": 7}, {"directory": "/tmp"},
    {"origin": "https://other.example"}, {"identity": "f" * 64}, {"proof": {}},
    {"token": "secret"}])
def test_read_parser_never_accepts_caller_authority_or_selection(action, extra):
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps({"version": 1, "action": action, **extra}).encode())


@pytest.mark.parametrize("action", ["continuation-current", "continuation-review",
                                    "continuation-verify-active"])
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


def initial_payload(candidate):
    state = current.inspect_stopped_continuation(candidate.root, **candidate.args)
    return dict(epoch=state.epoch, intent="e" * 64, binding=dict(
        fingerprint=state.state_fingerprint, revision=state.native_revision, generation=7))


def fictional_initial_payload():
    return dict(epoch="a" * 64, intent="e" * 64, binding=dict(
        fingerprint="b" * 64, revision=3, generation=7))


def test_initial_parser_exact_envelope_has_no_serialized_authority():
    from sds200.browser_device_protocol import BrowserWorkerRequest

    payload = dict(version=1, action="continuation-initial-session", **fictional_initial_payload())
    inner = parse_browser_device_request(json.dumps(payload).encode())
    assert type(inner) is BrowserContinuationInitialRequest
    assert inner == BrowserContinuationInitialRequest("a" * 64, "e" * 64, "b" * 64, 3, 7)
    wrapped = parse_browser_device_request(json.dumps(dict(version=1, action="worker-request",
        build="f" * 64, request=payload)).encode())
    assert type(wrapped) is BrowserWorkerRequest and wrapped.request == inner
    assert not hasattr(inner, "consent") and not hasattr(inner, "ticket")
    assert "e" * 64 not in repr(inner)


@pytest.mark.parametrize("field", ["epoch", "intent", "fingerprint", "revision", "generation"])
@pytest.mark.parametrize("value", [None, True, "", [], {}, -1, 0, 1.0, 2**53, "A" * 64])
def test_initial_parser_rejects_malformed_comparisons(field, value):
    payload = fictional_initial_payload()
    target = payload if field in {"epoch", "intent"} else payload["binding"]
    target[field] = value
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps(dict(version=1,
            action="continuation-initial-session", **payload)).encode())


@pytest.mark.parametrize("extra", ["role", "origin", "directory", "proof", "consent",
                                  "ticket", "token", "expires_at"])
@pytest.mark.parametrize("nested", [False, True])
def test_initial_parser_rejects_caller_authority(extra, nested):
    payload = fictional_initial_payload()
    (payload["binding"] if nested else payload)[extra] = "PRIVATE"
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps(dict(version=1,
            action="continuation-initial-session", **payload)).encode())


@pytest.mark.parametrize("revision", [2**53 - 3, 2**53 - 2, 2**53 - 1])
def test_initial_parser_requires_completion_revision_headroom(revision):
    payload = fictional_initial_payload()
    payload["binding"]["revision"] = revision
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(json.dumps(dict(version=1,
            action="continuation-initial-session", **payload)).encode())


def test_initial_parser_rejects_duplicate_nested_binding():
    payload = dict(version=1, action="continuation-initial-session", **fictional_initial_payload())
    encoded = json.dumps(payload).replace('"generation": 7', '"generation": 7, "generation": 7')
    with pytest.raises(BrowserDeviceProtocolError):
        parse_browser_device_request(encoded.encode())


@pytest.mark.parametrize("failure", ["unwrapped", "no-worker", "wrong-identity", "retirement",
                                    "selection", "missing-anchor", "unsafe-anchor", "wrong-build"])
def test_initial_route_requires_exact_installed_selection(
        lab, candidate, dispatch, monkeypatch, failure):
    payload = initial_payload(candidate)
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    changes = {}
    if failure == "no-worker":
        changes["worker"] = None
    elif failure == "wrong-identity":
        changes["expected_identity"] = "f" * 64
    elif failure == "retirement":
        changes["retirement"] = native.BrowserRetirementSelection(lab.args["archives"], "e" * 64)
    elif failure == "selection":
        changes["worker"] = worker.BrowserWorkerSelection(
            lab.args["bundle"], lab.args["public_key"], directory=candidate.root)
    elif failure == "missing-anchor":
        path = candidate.paths["manifest"]
        path.rename(path.with_name(path.name + ".retained"))
    elif failure == "unsafe-anchor":
        candidate.paths["manifest"].chmod(0o644)
    elif failure == "wrong-build":
        actual = native.worker_graph
        monkeypatch.setattr(native, "worker_graph", lambda: ("f" * 64, actual()[1]))
    before = snap(lab)
    assert dispatch("continuation-initial-session", extra=payload,
        unwrap=failure == "unwrapped", changes=changes) == FAILURE
    assert snap(lab) == before


@pytest.mark.parametrize("field", ["epoch", "fingerprint", "revision"])
def test_initial_stale_comparison_refused_before_mutation(
        lab, candidate, dispatch, monkeypatch, field):
    payload = initial_payload(candidate)
    target = payload if field == "epoch" else payload["binding"]
    target[field] = target[field] + 1 if field == "revision" else "f" * 64
    monkeypatch.setattr(transport, "verify_browser_device", lambda *a: pytest.fail("Network"))
    before = snap(lab)
    assert dispatch("continuation-initial-session", extra=payload) == FAILURE
    assert snap(lab) == before


@pytest.fixture
def initial_transport(monkeypatch):
    calls = []

    def verify(config, record):
        calls.append(("verify", record.generation))
        return transport.BrowserVerifiedRecord(config.identity, record, True)

    def exchange(config, generation):
        calls.append(("exchange", generation))
        return native.ExchangeSession(TOKEN, 300)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    return calls


def test_initial_framed_request_issues_once_without_persisting_a_token(
        lab, candidate, dispatch, initial_transport):
    payload = initial_payload(candidate)
    response = dispatch("continuation-initial-session", extra=payload)
    assert response["ok"] is True
    assert set(response) == {"version", "ok", "build", "identity", "epoch", "mode",
                             "binding", "session"}
    assert response["build"] == worker.worker_graph()[0]
    assert response["identity"] == lab.configuration.identity
    assert response["epoch"] == payload["epoch"] and response["mode"] == "active"
    assert response["binding"]["revision"] == payload["binding"]["revision"] + 2
    assert response["binding"]["fingerprint"] != payload["binding"]["fingerprint"]
    assert response["binding"]["generation"] == 7
    assert response["session"]["token"] == TOKEN
    assert 290 < response["session"]["expires_in"] < 300
    assert initial_transport == [("verify", 7), ("exchange", 7)]
    before = snap(lab)
    for intent in [payload["intent"], "d" * 64]:
        assert dispatch("continuation-initial-session",
                        extra={**payload, "intent": intent}) == FAILURE
    assert snap(lab) == before and initial_transport == [("verify", 7), ("exchange", 7)]
    for root_path in [lab.args["profile"], candidate.root]:
        for path in root_path.rglob("*"):
            if path.is_file() and not path.is_symlink():
                assert TOKEN.encode() not in path.read_bytes()
    assert CREDENTIAL not in json.dumps(response)
    blocked(lab)


@pytest.mark.parametrize("active", [False, True])
def test_framed_active_verification_is_fresh_and_read_only(
        lab, candidate, dispatch, monkeypatch, active):
    if active:
        active_native(lab, candidate)
    calls = []

    def verify(config, record):
        assert config == lab.configuration
        assert record == BrowserDeviceRecord(config.device_id, 7, BrowserDeviceState.ACTIVE)
        calls.append(record)
        return transport.BrowserVerifiedRecord(config.identity, record, True)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    before = snap(lab)
    result = dispatch("continuation-verify-active")
    assert snap(lab) == before
    if active:
        context = dispatch("continuation-current")
        observed = context["continuation"]
        assert result == dict(version=1, ok=True, build=worker.worker_graph()[0],
            identity=lab.configuration.identity, epoch=observed["epoch"], mode="active",
            binding=observed["binding"])
        assert len(calls) == 1
        # Another explicit read is fresh verification, not a cached proof or a
        # session retry. No ordinary startup selects either request.
        assert dispatch("continuation-verify-active") == result and len(calls) == 2
    else:
        assert result == FAILURE and calls == []
    assert TOKEN not in json.dumps(result) and CREDENTIAL not in json.dumps(result)
    blocked(lab)


@pytest.mark.parametrize("failure", ["generation", "paused", "revoked", "undrained",
                                    "lost-proof", "native-pause", "final-state-change"])
def test_framed_active_verification_refuses_changed_authority_without_repair(
        lab, candidate, dispatch, monkeypatch, failure):
    from sds200 import browser_device_continuation_recheck as recheck
    from tests.test_browser_device_continuation_current import native_step

    active_native(lab, candidate)
    retained = []
    calls = []

    def verify(config, record):
        calls.append(record)
        if failure == "native-pause":
            native_step(lab, candidate, "pause")
        retained.append(snap(lab))
        if failure == "lost-proof":
            raise RuntimeError("PRIVATE " + CREDENTIAL)
        if failure == "generation":
            record = replace(record, generation=8)
        if failure in {"paused", "revoked"}:
            record = replace(record, state=BrowserDeviceState(failure))
        return transport.BrowserVerifiedRecord(config.identity, record, failure != "undrained")

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    if failure == "final-state-change":
        original = recheck._BrowserWorkerActiveVerification.run

        def changed(operation, expected):
            result = original(operation, expected)
            native_step(lab, candidate, "pause")
            retained[:] = [snap(lab)]
            return result

        monkeypatch.setattr(recheck._BrowserWorkerActiveVerification, "run", changed)
    assert dispatch("continuation-verify-active") == FAILURE
    assert len(calls) == 1 and snap(lab) == retained[0]
    blocked(lab)


@pytest.mark.parametrize("profile,server", [("dns", 0), ("ip", 0), ("ipv6", "ipv6")],
                         indirect=True)
def test_framed_active_verification_with_actual_verified_tls(
        lab, candidate, dispatch, server, monkeypatch):
    active_native(lab, candidate)
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=7,
                                        state="active", drained=True)).encode()
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    before = snap(lab)
    result = dispatch("continuation-verify-active")
    assert result["ok"] is True and result["binding"]["generation"] == 7
    assert result["mode"] == "active" and snap(lab) == before
    assert len(observed) == 1
    path, headers, body = observed[0]
    assert path == "/auth/device/verify" and json.loads(body) == {
        "device_id": "display", "generation": 7}
    assert headers["Authorization"] == "Bearer " + CREDENTIAL
    assert not {"Cookie", "Origin"} & headers.keys()
    assert "session" not in result
    blocked(lab)


@pytest.mark.parametrize("stage", ["verification", "output"])
def test_real_supervisor_bounds_active_verification_and_output(
        lab, candidate, dispatch, monkeypatch, stage):
    active_native(lab, candidate)
    assert native._TOTAL_SECONDS == 10
    read_fd, write_fd = os.pipe()

    def stall(*args):
        os.write(write_fd, struct.pack("=I", os.getpid()))
        time.sleep(30)  # Independent supervisor terminates this fixture child.
        raise RuntimeError("Unreachable fictional active verification")

    def verify(config, record):
        return transport.BrowserVerifiedRecord(config.identity, record, True)

    monkeypatch.setattr(transport, "verify_browser_device",
                        stall if stage == "verification" else verify)
    if stage == "output":
        original = native.json.dumps

        def serialize(value, *args, **kwargs):
            if (type(value) is dict and value.get("mode") == "active"
                    and value.get("ok") is True and "binding" in value):
                stall()
            return original(value, *args, **kwargs)

        monkeypatch.setattr(native.json, "dumps", serialize)
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    request = {"version": 1, "action": "worker-request", "build": worker.worker_graph()[0],
               "request": {"version": 1, "action": "continuation-verify-active"}}
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
            assert code == 2 and 9.5 <= time.monotonic() - started < 13
            os.set_blocking(read_fd, False)
            child, = struct.unpack("=I", os.read(read_fd, 4))
            with pytest.raises(ChildProcessError):
                os.waitpid(child, os.WNOHANG)
            destination.seek(0)
            assert destination.read() == b""
        assert snap(lab) == before
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("failure", ["wrong-generation", "undrained", "revoked", "lost-proof",
                                    "lost-exchange"])
def test_initial_framed_failure_does_not_retry(lab, candidate, dispatch, monkeypatch, failure):
    payload = initial_payload(candidate)
    calls = []

    def verify(config, record):
        calls.append("verify")
        if failure == "lost-proof":
            raise RuntimeError("PRIVATE " + CREDENTIAL)
        if failure == "wrong-generation":
            record = replace(record, generation=8)
        elif failure == "revoked":
            record = replace(record, state=BrowserDeviceState.REVOKED)
        return transport.BrowserVerifiedRecord(config.identity, record, failure != "undrained")

    def exchange(*args):
        calls.append("exchange")
        raise RuntimeError("PRIVATE " + TOKEN)

    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", exchange)
    assert dispatch("continuation-initial-session", extra=payload) == FAILURE
    assert calls == (["verify", "exchange"] if failure == "lost-exchange" else ["verify"])
    before = snap(lab)
    assert dispatch("continuation-initial-session", extra=payload) == FAILURE
    assert snap(lab) == before
    assert calls == (["verify", "exchange"] if failure == "lost-exchange" else ["verify"])
    blocked(lab)


@pytest.mark.parametrize("lost", ["before-write", "partial-write", "after-write", "flush"])
def test_lost_initial_native_output_never_reissues(
        lab, candidate, dispatch, initial_transport, lost):
    payload = initial_payload(candidate)
    request = dict(version=1, action="worker-request", build=worker.worker_graph()[0],
        request=dict(version=1, action="continuation-initial-session", **payload))
    delivered = bytearray()

    class LostOutput:
        def write(self, data):
            if lost == "partial-write" and not delivered:
                delivered.extend(data[:8])
                return 8
            if lost in {"before-write", "partial-write"}:
                raise OSError("Fictional lost output")
            delivered.extend(data)
            if lost == "after-write":
                raise OSError("Fictional lost acknowledgement")
            return len(data)

        def flush(self):
            raise OSError("Fictional lost flush acknowledgement")

    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    with _launch_lock(candidate.root, create=False):
        assert native._native_request(lab.args["profile"], [lab.configuration.extension_origin],
            io.BytesIO(frame(request)), LostOutput(),
            expected_identity=lab.configuration.identity, worker=selected) == 1
    assert initial_transport == [("verify", 7), ("exchange", 7)]
    before = snap(lab)
    assert dispatch("continuation-initial-session", extra=payload) == FAILURE
    assert snap(lab) == before and initial_transport == [("verify", 7), ("exchange", 7)]
    context = dispatch("continuation-current")
    assert context["continuation"]["mode"] == "active"
    assert TOKEN not in json.dumps(context)
    blocked(lab)


@pytest.mark.parametrize("stage", ["verify", "exchange", "output"])
def test_real_supervisor_bounds_initial_issuance_and_output(
        lab, candidate, dispatch, initial_transport, monkeypatch, stage):
    assert native._TOTAL_SECONDS == 10
    payload = initial_payload(candidate)
    request = dict(version=1, action="worker-request", build=worker.worker_graph()[0],
        request=dict(version=1, action="continuation-initial-session", **payload))
    selected = worker.BrowserWorkerSelection(lab.args["bundle"], lab.args["public_key"])
    read_fd, write_fd = os.pipe()

    def stall(*args):
        os.write(write_fd, struct.pack("=I", os.getpid()))
        time.sleep(30)
        raise RuntimeError("Unreachable fictional stall")

    if stage in {"verify", "exchange"}:
        monkeypatch.setattr(transport, "verify_browser_device" if stage == "verify"
                            else "exchange_browser_device_at_generation", stall)
    else:
        # Keep actual native request and real output pipe. Stall serialization
        # only once the successful initial reply, including its token, exists.
        original = native.json.dumps

        def serialize(value, *args, **kwargs):
            if (type(value) is dict and type(value.get("session")) is dict
                    and value["session"].get("token") == TOKEN):
                stall()
            return original(value, *args, **kwargs)

        monkeypatch.setattr(native.json, "dumps", serialize)
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
            child, = struct.unpack("=I", os.read(read_fd, 4))
            with pytest.raises(ChildProcessError):
                os.waitpid(child, os.WNOHANG)
            destination.seek(0)
            assert destination.read() == b""
        before = snap(lab)
        assert dispatch("continuation-initial-session", extra=payload) == FAILURE
        assert snap(lab) == before
        blocked(lab)
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("profile,server", [("dns", 0), ("ip", 0), ("ipv6", "ipv6")],
                         indirect=True)
def test_initial_framed_dispatch_with_actual_verified_tls(
        lab, candidate, dispatch, server, monkeypatch):
    _, response, observed = server
    response["body"] = json.dumps(dict(version=1, device_id="display", generation=7,
        state="active", drained=True)).encode()
    original = transport.verify_browser_device

    def verify(*args):
        result = original(*args)
        response["body"] = json.dumps(dict(token=TOKEN, expires_in=300, generation=7)).encode()
        return result

    # Restore the actual exchange that the read-only dispatch fixture prohibits.
    monkeypatch.setattr(transport, "exchange_browser_device_at_generation", ACTUAL_EXCHANGE)
    monkeypatch.setattr(transport, "verify_browser_device", verify)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    payload = initial_payload(candidate)
    result = dispatch("continuation-initial-session", extra=payload)
    assert result["ok"] is True and result["session"]["token"] == TOKEN
    assert result["binding"]["generation"] == 7
    assert [call[0] for call in observed] == ["/auth/device/verify", "/auth/device/session"]
    for _, headers, body in observed:
        assert json.loads(body) == dict(device_id="display", generation=7)
        assert headers["Authorization"] == "Bearer " + CREDENTIAL
        assert not {"Cookie", "Origin"} & headers.keys()
    before = snap(lab)
    assert dispatch("continuation-initial-session", extra=payload) == FAILURE
    assert len(observed) == 2 and snap(lab) == before
    blocked(lab)
