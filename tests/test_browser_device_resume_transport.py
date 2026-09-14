"""Actual verified loopback TLS into the real ASGI/owner and native approval core.

The HTTP fixture forwards to TestClient; this is not a deployed browser/server.
All private profiles, credentials and certificate keys are synthetic temporary data.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import sds200.browser_device_resume as resume_module
from sds200.browser_device_http import BROWSER_DEVICE_COOKIE, BrowserDeviceHTTP
from sds200.browser_device_recovery import BrowserDeviceRecovery, BrowserRecoveryError, RecoveryMode
from sds200.browser_device_resume import BrowserDeviceResume, BrowserResumeError
from sds200.browser_device_sessions import BrowserDeviceSessions
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore
from sds200.web_auth import WebDashboardAuthentication
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import configure, initialize, private

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() == 0,
                                reason="Non-root Linux native approval")
INTENT = "f" * 64


@pytest.fixture
def lab(tmp_path, certificates):
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(authority / "devices.sqlite")
    issued = store.enroll("display")
    sessions = BrowserDeviceSessions(store)
    observed = []
    raw = FastAPI()

    @raw.get("/")
    @raw.get("/api/v1/status")
    async def status(request: Request):
        return {"display_only": request.state.sdsctl_display_only}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            # Never retain Authorization or response.
            observed.append((self.path, json.loads(body)))
            result = client.post(self.path, content=body, headers=dict(self.headers))
            self.send_response(result.status_code)
            for key, value in result.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(result.content)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(*certificates[0])
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    origin = f"https://localhost:{httpd.server_port}"
    manual = WebDashboardAuthentication("fictional operator password", origin)
    wrapper = BrowserDeviceHTTP(raw, authentication=manual, devices=sessions)
    root = tmp_path / "native"
    root.mkdir(mode=0o700)
    configure(root, origin)
    private(root / "device.secret", issued.credential)
    private(root / "ca.pem", certificates[0][0].read_bytes())
    configuration = initialize(root)
    ledger = BrowserDeviceRecovery(root / "recovery.sqlite", configuration.identity)
    ledger.claim_browser()
    ledger.suspend()
    core = BrowserDeviceResume(root)
    with TestClient(wrapper, base_url=origin) as client:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(root=root, core=core, ledger=ledger, store=store, issued=issued,
                sessions=sessions, client=client, configuration=configuration, observed=observed)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(3)


def prepare(lab, generation=1):
    return lab.core.prepare_verified(expected_revision=lab.ledger.inspect().revision,
        browser_intent=INTENT, expected_generation=generation)


def test_real_tls_owner_core_and_generation_session_roundtrip(lab):
    authority_before = lab.store.path.read_bytes()
    approval = prepare(lab)
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert not lab.sessions._sessions
    result = lab.core.commit_session(approval, browser_intent=INTENT)
    assert result.status.mode is RecoveryMode.ACTIVE and result.session is not None
    assert result.status.revision == approval.revision + 2
    response = lab.client.get("/api/v1/status", headers={"Cookie":
        BROWSER_DEVICE_COOKIE + "=" + result.session.token})
    assert response.status_code == 200 and response.json() == {"display_only": True}
    assert lab.store.path.read_bytes() == authority_before
    assert lab.observed == [("/auth/device/verify", {"device_id":"display", "generation":1}),
        ("/auth/device/verify", {"device_id":"display", "generation":1}),
        ("/auth/device/session", {"device_id":"display", "generation":1})]
    with pytest.raises(BrowserResumeError):
        lab.core.commit_session(approval, browser_intent=INTENT)
    assert len(lab.observed) == 3
    contents = lab.ledger.path.read_bytes()
    assert approval.ticket.encode() not in contents
    assert result.session.token.encode() not in contents
    assert lab.issued.credential.encode() not in contents


@pytest.mark.parametrize("action", ["pause", "revoke", "rotate", "aba", "native-pause"])
def test_change_after_preparation_refuses_consumption_or_session(lab, action):
    approval = prepare(lab)
    if action == "native-pause":
        lab.ledger.suspend()
    elif action == "rotate":
        lab.store.rotate("display")
    else:
        lab.store.transition("display", BrowserDeviceState.REVOKED if action == "revoke"
                             else BrowserDeviceState.PAUSED)
        if action == "aba":
            lab.store.transition("display", BrowserDeviceState.ACTIVE)
    with pytest.raises(BrowserResumeError):
        lab.core.commit_session(approval, browser_intent=INTENT)
    assert not lab.sessions._sessions
    assert lab.ledger.inspect().mode is RecoveryMode.PAUSED
    assert not any(path.endswith("session") for path, _ in lab.observed)


def test_server_changes_after_proof_cannot_silently_select_new_generation(lab, monkeypatch):
    approval = prepare(lab)
    original = resume_module.exchange_browser_device_at_generation

    def changed(config, generation):
        lab.store.transition("display", BrowserDeviceState.PAUSED)
        lab.store.transition("display", BrowserDeviceState.ACTIVE)
        return original(config, generation)

    monkeypatch.setattr(resume_module, "exchange_browser_device_at_generation", changed)
    result = lab.core.commit_session(approval, browser_intent=INTENT)
    assert result.session is None and result.status.mode is RecoveryMode.REJECTED
    assert not lab.sessions._sessions
    assert lab.observed[-1][1]["generation"] == 1


def test_native_change_between_commit_and_exchange_is_not_overridden(lab, monkeypatch):
    approval = prepare(lab)
    original = lab.core._recovery.authenticate

    def changed(exchange, *, expected_revision):
        paused = lab.ledger.suspend()
        lab.ledger.resume(paused.revision)  # Trusted local ABA still invalidates the old review.
        return original(exchange, expected_revision=expected_revision)

    monkeypatch.setattr(lab.core._recovery, "authenticate", changed)
    with pytest.raises(BrowserResumeError):
        lab.core.commit_session(approval, browser_intent=INTENT)
    assert not lab.sessions._sessions
    assert len(lab.observed) == 2


def test_authenticate_expected_revision_is_checked_before_io(lab):
    state = lab.ledger.resume(lab.ledger.inspect().revision)
    before = lab.ledger.path.read_bytes()
    with pytest.raises(BrowserRecoveryError):
        lab.ledger.authenticate(lambda: pytest.fail("stale revision contacted server"),
                                expected_revision=state.revision - 1)
    assert lab.ledger.path.read_bytes() == before


@pytest.mark.parametrize("kind", ["revision", "intent", "generation"])
def test_prepare_input_refusal_does_not_contact_server(lab, kind):
    arguments = dict(expected_revision=lab.ledger.inspect().revision,
                     browser_intent=INTENT, expected_generation=1)
    arguments[{"revision":"expected_revision", "intent":"browser_intent",
               "generation":"expected_generation"}[kind]] = 0
    with pytest.raises(BrowserResumeError):
        lab.core.prepare_verified(**arguments)
    assert not lab.observed
