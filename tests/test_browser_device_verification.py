"""Resume transport, authenticated server snapshots, and generation-bound exchange."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from sds200.browser_device_http import BROWSER_DEVICE_EXCHANGE_PATH, BROWSER_DEVICE_VERIFY_PATH
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import ExchangeFailure, RecoveryMode
from sds200.browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from sds200.browser_device_verification import (
    exchange_browser_device_at_generation,
    verify_browser_device,
)
from tests.test_browser_device_http import setup as setup
from tests.test_browser_device_native import (
    CREDENTIAL,
    TOKEN,
    configure,
    private,
)
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import root as root
from tests.test_browser_device_native import server as server


def request(client, credential, *, generation=None, path=BROWSER_DEVICE_VERIFY_PATH, **kwargs):
    return client.post(path, headers={"Authorization": "Bearer " + credential},
                       json={"device_id": "display", **({"generation": generation}
                             if generation is not None else {})}, **kwargs)


def test_server_proof_is_authenticated_drained_and_does_not_issue_a_session(setup):
    client, store, device, sessions, *_ = setup
    before = store.path.read_bytes()
    result = request(client, device.credential)
    assert result.status_code == 200
    assert result.json() == {"version": 1, "device_id": "display", "generation": 1,
                             "state": "active", "drained": True}
    assert "no-store" in result.headers["cache-control"]
    assert "set-cookie" not in result.headers
    assert not sessions._sessions and not sessions._outstanding
    assert store.path.read_bytes() == before
    assert device.credential not in result.text


@pytest.mark.parametrize("kind", ["wrong", "paused", "revoked", "rotated", "other"])
def test_server_proof_cannot_change_or_bypass_authority(setup, kind):
    client, store, device, sessions, *_ = setup
    credential = device.credential
    if kind == "wrong":
        credential = CREDENTIAL
    elif kind == "other":
        credential = store.enroll("other").credential
    elif kind == "rotated":
        store.rotate("display")
    else:
        store.transition("display", BrowserDeviceState(kind))
    before = store.path.read_bytes()
    result = request(client, credential)
    assert result.status_code == 401
    assert store.path.read_bytes() == before and not sessions._sessions
    assert not {"generation", "state", "token", "credential"} & result.json().keys()


@pytest.mark.parametrize("path", [BROWSER_DEVICE_VERIFY_PATH, BROWSER_DEVICE_EXCHANGE_PATH])
def test_server_generation_comparison_rejects_pause_resume_aba(setup, path):
    client, store, device, sessions, *_ = setup
    store.transition("display", BrowserDeviceState.PAUSED)
    record = store.transition("display", BrowserDeviceState.ACTIVE)
    assert request(client, device.credential, generation=1, path=path).status_code == 401
    assert not sessions._sessions
    result = request(client, device.credential, generation=record.generation, path=path)
    assert result.status_code == 200 and result.json()["generation"] == record.generation


@pytest.mark.parametrize("generation", [True, False, 0, -1, 1.0, "1", None, 2**53 - 1, [], {}])
@pytest.mark.parametrize("path", [BROWSER_DEVICE_VERIFY_PATH, BROWSER_DEVICE_EXCHANGE_PATH])
def test_server_strict_generation_contract(setup, generation, path):
    client, _, device, sessions, *_ = setup
    result = client.post(path, headers={"Authorization": "Bearer " + device.credential},
                         json={"device_id": "display", "generation": generation})
    assert result.status_code == 400 and not sessions._sessions


@pytest.mark.parametrize("headers", [{"Origin": "https://localhost"},
    {"Cookie": "unrelated=x"}, {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Mode": "cors"}])
def test_proof_refuses_browser_context(setup, headers):
    client, _, device, *_ = setup
    result = client.post(BROWSER_DEVICE_VERIFY_PATH,
        headers={"Authorization": "Bearer " + device.credential, **headers},
        json={"device_id": "display"})
    assert result.status_code == 403


def test_proof_shares_exchange_admission_budget(setup):
    client, _, device, *_ = setup
    for _ in range(5):
        assert request(client, device.credential).status_code == 200
    result = request(client, device.credential, path=BROWSER_DEVICE_EXCHANGE_PATH)
    assert result.status_code == 429 and result.headers["retry-after"] == "60"


def test_server_rechecks_credential_after_drain(setup, monkeypatch):
    client, store, device, sessions, *_ = setup
    acknowledge = sessions.acknowledge

    def rotate(record):
        assert acknowledge(record)
        store.rotate("display")
        return True

    monkeypatch.setattr(sessions, "acknowledge", rotate)
    assert request(client, device.credential).status_code == 401
    assert not sessions._sessions


def test_no_proof_when_drain_unconfirmed(setup, monkeypatch):
    client, _, device, sessions, *_ = setup
    monkeypatch.setattr(sessions, "acknowledge", lambda record: False)
    result = request(client, device.credential)
    assert result.status_code == 503 and result.headers["retry-after"] == "5"
    assert "drained" not in result.json() and not sessions._sessions


def test_real_proof_waits_for_old_request_cleanup(setup):
    _, store, device, sessions, *_ = setup
    issued = sessions.issue("display", device.credential)
    assert issued is not None

    async def run():
        lease = sessions.acquire(issued.token)
        assert lease is not None
        try:
            store.transition("display", BrowserDeviceState.PAUSED)
            record = store.transition("display", BrowserDeviceState.ACTIVE)
            pending = asyncio.create_task(asyncio.to_thread(sessions.verify_for_resume,
                "display", device.credential, expected_generation=record.generation))
            await asyncio.wait_for(lease.revoked.wait(), 2)
            assert not pending.done()  # Invalidation is not acknowledgement of request completion.
            lease.release()
            assert await asyncio.wait_for(pending, 2) == record
        finally:
            lease.release()

    asyncio.run(run())


def proof_body(**changes):
    return json.dumps({"version": 1, "device_id": "display", "generation": 4,
                       "state": "active", "drained": True, **changes}).encode()


def expected():
    return BrowserDeviceRecord("display", 4, BrowserDeviceState.ACTIVE)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_verified_tls_proof_uses_fixed_context_no_proxy_no_cookie(server, host, monkeypatch):
    configuration, response, observed = server
    configure(configuration.root, configuration.origin.replace("localhost", host))
    configuration = load_browser_native_configuration(configuration.root)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "/fictional/unreadable")
    response["body"] = proof_body()
    verified = verify_browser_device(configuration, expected())
    assert verified.identity == configuration.identity and verified.record == expected()
    assert verified.drained is True
    path, headers, body = observed[0]
    assert path == BROWSER_DEVICE_VERIFY_PATH
    assert json.loads(body) == {"device_id": "display", "generation": 4}
    assert headers["Authorization"] == "Bearer " + CREDENTIAL
    assert not {"Cookie", "Origin"} & headers.keys()
    assert TOKEN not in repr(verified) and CREDENTIAL not in repr(verified)


def test_tls_generation_session_strict_echo_and_ordinary_compatibility(server):
    configuration, response, observed = server
    response["body"] = json.dumps({"token": TOKEN, "expires_in": 300, "generation": 4}).encode()
    session = exchange_browser_device_at_generation(configuration, 4)
    assert session.token == TOKEN and session.expires_in == 300
    assert json.loads(observed[0][2]) == {"device_id": "display", "generation": 4}
    assert TOKEN not in repr(session)


@pytest.mark.parametrize("kind", ["proof", "session"])
@pytest.mark.parametrize("failure", ["untrusted", "redirect", "cookie",
    "compressed", "duplicate", "boolean", "stale", "unknown", "wrong-device", "pending"])
def test_transport_rejects_unsafe_or_unconfirmed_results(server, certificates, kind, failure):
    configuration, response, observed = server
    response["body"] = proof_body() if kind == "proof" else json.dumps(
        {"token": TOKEN, "expires_in": 300, "generation": 4}).encode()
    if failure == "untrusted":
        private(configuration.root / "ca.pem", certificates[1][0].read_bytes())
    elif failure == "redirect":
        response.update(status=302, headers=[("Location", "https://example.invalid/private")])
    elif failure == "cookie":
        response["headers"] = [("Set-Cookie", "private=fixture")]
    elif failure == "compressed":
        response["headers"] = [("Content-Encoding", "gzip")]
    elif failure == "duplicate":
        response["body"] = response["body"][:-1] + b',"generation":4}'
    else:
        value = json.loads(response["body"])
        if failure == "boolean":
            value["generation"] = True
        elif failure == "stale":
            value["generation"] = 5
        elif failure == "unknown":
            value["extra"] = "private"
        elif failure == "wrong-device":
            value["device_id"] = "other"
        elif failure == "pending":
            value["drained"] = False
        response["body"] = json.dumps(value).encode()
    with pytest.raises(ExchangeFailure) as error:
        if kind == "proof":
            verify_browser_device(configuration, expected())
        else:
            exchange_browser_device_at_generation(configuration, 4)
    assert "private" not in str(error.value)
    assert CREDENTIAL not in str(error.value) and TOKEN not in str(error.value)
    if failure == "untrusted":
        assert error.value.mode is RecoveryMode.TLS_ERROR and not observed
    if failure == "redirect":
        assert len(observed) == 1


@pytest.mark.parametrize("status,mode", [(401, RecoveryMode.REJECTED),
    (429, RecoveryMode.ACTIVE), (503, RecoveryMode.ACTIVE), (403, RecoveryMode.PROTOCOL_ERROR)])
def test_proof_http_failures_keep_fixed_classification(server, status, mode):
    configuration, response, _ = server
    response.update(status=status, body=b"private error", headers=[("Retry-After", "60")])
    with pytest.raises(ExchangeFailure) as error:
        verify_browser_device(configuration)
    assert error.value.mode is mode and "private" not in str(error.value)


@pytest.mark.parametrize("server", [1], indirect=True)
@pytest.mark.parametrize("kind", ["proof", "session"])
def test_tls_hostname_mismatch_never_sends_credentials(server, kind):
    configuration, _, observed = server
    with pytest.raises(ExchangeFailure) as error:
        if kind == "proof":
            verify_browser_device(configuration)
        else:
            exchange_browser_device_at_generation(configuration, 4)
    assert error.value.mode is RecoveryMode.TLS_ERROR and not observed


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", 2**53 - 1])
def test_generation_validation_before_network(server, value):
    configuration, _, observed = server
    with pytest.raises(ExchangeFailure):
        exchange_browser_device_at_generation(configuration, value)
    with pytest.raises(ExchangeFailure):
        verify_browser_device(configuration, replace(expected(), generation=value))
    assert not observed
