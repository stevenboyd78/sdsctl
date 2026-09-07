from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message

import sds200.browser_device_http as device_http
from sds200.browser_device_http import (
    BROWSER_DEVICE_COOKIE,
    BROWSER_DEVICE_EXCHANGE_PATH,
    BrowserDeviceHTTP,
    _Admission,
    _Busy,
    _Workers,
)
from sds200.browser_device_owner import BrowserOwnerError, request_browser_owner_ack
from sds200.browser_device_sessions import BrowserDeviceSessions
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore
from sds200.web_auth import WEB_DASHBOARD_AUTH_COOKIE, WebDashboardAuthentication
from sds200.web_dashboard import create_web_dashboard_app

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux owner channel")
ORIGIN = "https://192.168.0.18:8443"


@pytest.fixture
def setup(tmp_path: Path):
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(root / "devices.sqlite")
    device = store.enroll("display")
    sessions = BrowserDeviceSessions(store)
    manual = WebDashboardAuthentication("fictional operator password", ORIGIN,
                                        display_password="fictional display password")
    raw = FastAPI()

    @raw.get("/api/v1/status")
    async def status(request: Request):
        return {"display_only": request.state.sdsctl_display_only}

    wrapper = BrowserDeviceHTTP(raw, authentication=manual, devices=sessions)
    with TestClient(wrapper, base_url=ORIGIN) as client:
        yield client, store, device, sessions, manual, wrapper


def exchange(client, credential, **kwargs):
    return client.post(BROWSER_DEVICE_EXCHANGE_PATH,
                       headers={"Authorization": "Bearer " + credential, **kwargs},
                       json={"device_id": "display"})


def test_exchange_and_read_are_display_only_and_no_store(setup) -> None:
    client, store, device, sessions, manual, wrapper = setup
    response = exchange(client, device.credential)
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert "set-cookie" not in response.headers  # Private helper handoff, not browser login.
    assert device.credential not in response.text
    token = response.json()["token"]
    client.cookies.set(BROWSER_DEVICE_COOKIE, token)
    assert client.get("/api/v1/status").json() == {"display_only": True}
    response = client.get("/auth/session")
    assert response.json()["display_only"] is True
    assert 0 < response.json()["remaining_seconds"] <= 300
    assert "no-store" in response.headers["cache-control"]
    assert client.get("/api/v1/recordings").status_code == 403
    assert client.post("/api/v1/control", json={}).status_code == 403
    assert client.get("/auth/login").status_code == 403
    assert client.post("/auth/logout").status_code == 403  # Exact Origin is mandatory.


@pytest.mark.parametrize("header", [
    {"Origin": ORIGIN}, {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Mode": "cors"},
    {"Cookie": "unrelated=1"}, {"Content-Type": "text/plain"},
])
def test_native_exchange_rejects_browser_context(setup, header) -> None:
    client, _, device, *_ = setup
    assert exchange(client, device.credential, **header).status_code == 403


@pytest.mark.parametrize("body", [
    b'{"device_id":"display","device_id":"display"}',
    b'{"device_id":"display","role":"operator"}', b'{"device_id":true}',
    b'{}', b'[]', b'\xff', b'x' * 1025,
])
def test_malformed_exchange_bodies_rejected(setup, body) -> None:
    client, _, device, *_ = setup
    response = client.post(BROWSER_DEVICE_EXCHANGE_PATH, content=body,
                           headers={"Content-Type": "application/json",
                                    "Authorization": "Bearer " + device.credential})
    assert response.status_code == 400
    assert device.credential not in response.text


@pytest.mark.parametrize("cookies", [
    f"{BROWSER_DEVICE_COOKIE}=x; {WEB_DASHBOARD_AUTH_COOKIE}=y",
    f"{BROWSER_DEVICE_COOKIE}=x; {BROWSER_DEVICE_COOKIE}=x",
    f"{WEB_DASHBOARD_AUTH_COOKIE}=x; {WEB_DASHBOARD_AUTH_COOKIE}=x",
    BROWSER_DEVICE_COOKIE, f"{BROWSER_DEVICE_COOKIE}=", "x=" + "a" * 8192,
])
def test_cookie_conflicts_never_fall_back_to_operator(setup, cookies) -> None:
    client, *_ = setup
    assert client.get("/api/v1/status", headers={"Cookie": cookies}).status_code == 400


def test_conflict_with_valid_operator_and_valid_device(setup) -> None:
    client, _, device, _, manual, _ = setup
    token = exchange(client, device.credential).json()["token"]
    operator = manual.issue_session()
    response = client.get("/api/v1/status", headers=[
        ("Cookie", f"{BROWSER_DEVICE_COOKIE}={token}"),
        ("Cookie", f"{WEB_DASHBOARD_AUTH_COOKIE}={operator}"),
    ])
    assert response.status_code == 400


def test_manual_operator_and_display_sessions_unchanged(setup) -> None:
    client, _, _, _, manual, _ = setup
    assert client.get("/auth/login").status_code == 200
    assert client.get("/auth/display/login").status_code == 200
    for display in (False, True):
        token = manual.issue_session(display_only=display)
        response = client.get("/auth/session", headers={
            "Cookie": f"{WEB_DASHBOARD_AUTH_COOKIE}={token}",
        })
        assert response.status_code == 200
        assert response.json()["display_only"] is display


@pytest.mark.parametrize("headers", [
    {"Host": "wrong.example"}, {"Host": "192.168.0.18:443"},
    {"X-Forwarded-Host": "192.168.0.18:8443", "Host": "wrong.example"},
])
def test_exact_origin_not_forwarded_headers(setup, headers) -> None:
    client, _, device, *_ = setup
    assert exchange(client, device.credential, **headers).status_code == 400


def test_exchange_attempts_bounded_and_query_forbidden(setup) -> None:
    client, _, device, *_ = setup
    assert client.get(BROWSER_DEVICE_EXCHANGE_PATH).status_code == 403
    assert client.post(BROWSER_DEVICE_EXCHANGE_PATH + "?secret=fictional").status_code == 403
    for _ in range(5):
        assert exchange(client, "wrong").status_code == 401
    assert exchange(client, device.credential).status_code == 429


def test_admission_window_and_global_bound() -> None:
    now = [0.0]
    admission = _Admission(lambda: now[0])
    for index in range(60):
        assert admission.admit(str(index))
    assert not admission.admit("new")
    now[0] = 60
    assert admission.admit("new")
    assert len(admission._peers) == 1


def test_cancelled_worker_does_not_free_slot_until_completion() -> None:
    workers = _Workers()
    entered = [threading.Event(), threading.Event()]
    finish = threading.Event()
    abandoned = []

    def block(index):
        entered[index].set()
        finish.wait(timeout=3)
        return index

    async def check() -> None:
        jobs = [asyncio.create_task(workers.run(lambda i=i: block(i), abandoned.append))
                for i in range(2)]
        try:
            for event in entered:
                assert await asyncio.to_thread(event.wait, 1)
            jobs[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await jobs[0]
            with pytest.raises(_Busy):
                await workers.run(lambda: 3)
            finish.set()
            assert await jobs[1] == 1
            for _ in range(100):
                if abandoned:
                    break
                await asyncio.sleep(0.001)
            assert abandoned == [0]
        finally:
            finish.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            workers.close()
    asyncio.run(check())


def test_asgi_stream_revocation_finishes_response_after_cleanup(setup) -> None:
    _, store, device, sessions, _, wrapper = setup
    issued = sessions.issue("display", device.credential)
    assert issued is not None

    async def check() -> None:
        started, cleaned = asyncio.Event(), asyncio.Event()
        messages: list[Message] = []

        async def app(scope, receive, send):
            try:
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"data: live\n\n",
                            "more_body": True})
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.body" and not message.get("more_body"):
                assert cleaned.is_set()
            messages.append(message)

        wrapper._app = app
        scope = {"type": "http", "scheme": "https", "method": "GET",
                 "path": "/api/v1/events", "query_string": b"",
                 "headers": [(b"host", b"192.168.0.18:8443"),
                             (b"cookie", f"{BROWSER_DEVICE_COOKIE}={issued.token}".encode())]}
        task = asyncio.create_task(wrapper(scope, receive, send))
        await asyncio.wait_for(started.wait(), 1)
        store.transition("display", BrowserDeviceState.REVOKED)
        sessions.reconcile()
        await asyncio.wait_for(task, 1)
        assert cleaned.is_set()
        assert messages[-1]["more_body"] is False
        assert b"no-store" in dict(messages[0]["headers"])[b"cache-control"]
    asyncio.run(check())


def test_real_dashboard_factory_requires_opt_in_native_auth(setup) -> None:
    _, store, device, _, _, _ = setup
    sessions = BrowserDeviceSessions(store)
    with pytest.raises(ValueError, match="native HTTPS"):
        create_web_dashboard_app(lambda: None, browser_device_sessions=sessions)
    with pytest.raises(ValueError, match="native HTTPS"):
        create_web_dashboard_app(lambda: None, home_assistant_ingress=True,
                                 browser_device_sessions=sessions)
    isolated = store.path.parent / "isolated"
    isolated.mkdir(mode=0o700)
    isolated_store = BrowserDeviceStore.initialize(isolated / "devices.sqlite")
    device = isolated_store.enroll("display")
    sessions = BrowserDeviceSessions(isolated_store)
    auth = WebDashboardAuthentication("another fictional operator password", ORIGIN)
    app = create_web_dashboard_app(lambda: None, lan_authentication=auth,
                                   browser_device_sessions=sessions)
    with TestClient(app, base_url=ORIGIN) as client:
        token = exchange(client, device.credential).json()["token"]
        client.cookies.set(BROWSER_DEVICE_COOKIE, token)
        response = client.get("/")
        assert response.status_code == 200
        assert "Scanner dashboard" in response.text
        assert client.get("/assets/dashboard.css").status_code == 200
        assert client.get("/api/v1/openapi.json").status_code == 403


def test_browser_device_feature_is_disabled_by_default() -> None:
    auth = WebDashboardAuthentication("another fictional operator password", ORIGIN)
    app = create_web_dashboard_app(lambda: None, lan_authentication=auth)
    with TestClient(app, base_url=ORIGIN) as client:
        assert exchange(client, "fictional").status_code != 200
        assert client.get("/auth/login").status_code == 200


def test_duplicate_host_and_authorization_and_cross_site_read(setup) -> None:
    client, _, device, _, _, _ = setup
    token = exchange(client, device.credential).json()["token"]
    assert client.get("/api/v1/status", headers=[
        ("Host", "192.168.0.18:8443"), ("Host", "192.168.0.18:8443"),
        ("Cookie", f"{BROWSER_DEVICE_COOKIE}={token}"),
    ]).status_code == 400
    for extra in ({"Sec-Fetch-Site": "cross-site"}, {"Authorization": "Bearer fictional"}):
        assert client.get("/api/v1/status", headers={
            "Cookie": f"{BROWSER_DEVICE_COOKIE}={token}", **extra,
        }).status_code == 403
    assert client.post(BROWSER_DEVICE_EXCHANGE_PATH, content='{"device_id":"display"}',
                       headers=[("Content-Type", "application/json"),
                                ("Authorization", "Bearer " + device.credential),
                                ("Authorization", "Bearer " + device.credential)],
                       ).status_code == 403


def test_capacity_and_authority_failure_are_retryable_not_bad_credentials(setup) -> None:
    client, store, device, _, _, _ = setup
    assert exchange(client, device.credential).status_code == 200
    assert exchange(client, device.credential).status_code == 200
    response = exchange(client, device.credential)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    store.path.rename(store.path.with_suffix(".saved"))
    assert exchange(client, device.credential).status_code == 503


def test_authentication_recovers_after_rate_limit_window(setup) -> None:
    client, _, device, _, _, wrapper = setup
    now = [0.0]
    wrapper._admission = _Admission(lambda: now[0])
    for _ in range(5):
        assert exchange(client, "wrong").status_code == 401
    response = exchange(client, device.credential)
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    now[0] = 60
    assert exchange(client, device.credential).status_code == 200


@pytest.mark.parametrize("origin", ["https://scanner.home.arpa:8443",
                                    "https://[fd00::18]:8443"])
def test_exchange_supports_dns_and_literal_ipv6_without_proxy(setup, origin) -> None:
    _, store, device, _, _, _ = setup
    isolated = store.path.parent / "isolated"
    isolated.mkdir(mode=0o700)
    isolated_store = BrowserDeviceStore.initialize(isolated / "devices.sqlite")
    device = isolated_store.enroll("display")
    sessions = BrowserDeviceSessions(isolated_store)
    auth = WebDashboardAuthentication("another fictional operator password", origin)
    wrapper = BrowserDeviceHTTP(FastAPI(), authentication=auth, devices=sessions)
    # This TestClient version misparses IPv6 URL authorities before invoking ASGI.
    # Supply the literal authority through Host to exercise our exact-origin path.
    with TestClient(wrapper, base_url=ORIGIN) as client:
        assert exchange(client, device.credential, Host=urlsplit(origin).netloc).status_code == 200


def test_stalled_body_releases_admission_slot(setup, monkeypatch) -> None:
    _, _, device, _, _, wrapper = setup
    monkeypatch.setattr(device_http, "_BODY_TIMEOUT_SECONDS", 0.01)

    async def check() -> None:
        sent = []

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "scheme": "https", "method": "POST",
                 "path": BROWSER_DEVICE_EXCHANGE_PATH, "query_string": b"",
                 "client": ("192.0.2.1", 10), "headers": [
                     (b"host", b"192.168.0.18:8443"),
                     (b"content-type", b"application/json"),
                     (b"authorization", ("Bearer " + device.credential).encode()),
                 ]}
        await asyncio.wait_for(wrapper(scope, receive, send), 1)
        assert sent[0]["status"] == 400
        assert wrapper._body_slots.acquire(blocking=False)
        assert wrapper._body_slots.acquire(blocking=False)
        wrapper._body_slots.release()
        wrapper._body_slots.release()
    asyncio.run(check())


def test_abandoned_worker_cleanup_survives_request_loop_shutdown() -> None:
    workers = _Workers()
    entered, finish, cleaned = threading.Event(), threading.Event(), threading.Event()

    def job():
        entered.set()
        finish.wait(timeout=3)
        return "fictional result"

    async def check() -> None:
        task = asyncio.create_task(workers.run(job, lambda _: cleaned.set()))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(check())  # Original request loop is now closed.
        finish.set()
        assert cleaned.wait(timeout=1)
    finally:
        finish.set()
        workers.close()


def test_device_logout_persists_pause_and_does_not_resume_on_manual_login(setup) -> None:
    client, store, device, _, manual, _ = setup
    token = exchange(client, device.credential).json()["token"]
    cookie = f"{BROWSER_DEVICE_COOKIE}={token}"
    response = client.post("/auth/logout", headers={"Cookie": cookie, "Origin": ORIGIN})
    assert response.status_code == 200
    assert "Automatic sign-in is paused" in response.text
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "no-store" in response.headers["cache-control"]
    assert store.inventory()[0].state is BrowserDeviceState.PAUSED
    assert exchange(client, device.credential).status_code == 401
    manual_token = manual.issue_session(display_only=True)
    assert client.get("/auth/session", headers={
        "Cookie": f"{WEB_DASHBOARD_AUTH_COOKIE}={manual_token}",
    }).status_code == 200
    assert BrowserDeviceStore(store.path).authenticate("display", device.credential) is None
    store.transition("display", BrowserDeviceState.ACTIVE)
    assert client.get("/auth/session", headers={"Cookie": cookie}).status_code == 401
    assert exchange(client, device.credential).status_code == 200


def test_logout_pending_shutdown_never_claims_completed(setup, monkeypatch) -> None:
    client, store, device, sessions, _, _ = setup
    token = exchange(client, device.credential).json()["token"]
    monkeypatch.setattr(sessions, "acknowledge", lambda record: False)
    response = client.post("/auth/logout", headers={
        "Cookie": f"{BROWSER_DEVICE_COOKIE}={token}", "Origin": ORIGIN,
    })
    assert response.status_code == 202
    assert "shutdown could not be confirmed" in response.text
    assert store.inventory()[0].state is BrowserDeviceState.PAUSED


@pytest.mark.parametrize("drained", [True, False])
def test_device_logout_json_contract_distinguishes_saved_pause_from_drain(
    setup, monkeypatch, drained,
) -> None:
    client, store, device, sessions, _, _ = setup
    token = exchange(client, device.credential).json()["token"]
    if not drained:
        monkeypatch.setattr(sessions, "acknowledge", lambda record: False)
    response = client.post("/auth/logout", headers={
        "Cookie": f"{BROWSER_DEVICE_COOKIE}={token}", "Origin": ORIGIN,
        "Accept": "application/json", "Sec-Fetch-Site": "same-origin",
    })
    assert response.status_code == (200 if drained else 202)
    assert response.json() == {
        "version": 1, "device_logout": True, "paused": True, "drained": drained,
    }
    assert "no-store" in response.headers["cache-control"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert store.inventory()[0].state is BrowserDeviceState.PAUSED
    assert token not in response.text and device.credential not in response.text


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_logout_csrf_does_not_pause(setup, origin) -> None:
    client, store, device, _, _, _ = setup
    token = exchange(client, device.credential).json()["token"]
    headers = {"Cookie": f"{BROWSER_DEVICE_COOKIE}={token}"}
    if origin is not None:
        headers["Origin"] = origin
    assert client.post("/auth/logout", headers=headers).status_code == 403
    assert store.inventory()[0].state is BrowserDeviceState.ACTIVE


def test_ack_waits_for_final_asgi_send_not_only_handler_cleanup(setup) -> None:
    _, store, device, sessions, _, wrapper = setup
    issued = sessions.issue("display", device.credential)
    assert issued is not None

    async def check() -> None:
        started, final_send, finish_send = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"live", "more_body": True})
            started.set()
            await asyncio.Event().wait()

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.body" and not message.get("more_body"):
                final_send.set()
                await finish_send.wait()

        wrapper._app = app
        scope = {"type": "http", "scheme": "https", "method": "GET",
                 "path": "/api/v1/events", "query_string": b"", "headers": [
                     (b"host", b"192.168.0.18:8443"),
                     (b"cookie", f"{BROWSER_DEVICE_COOKIE}={issued.token}".encode()),
                 ]}
        request = asyncio.create_task(wrapper(scope, receive, send))
        await asyncio.wait_for(started.wait(), 1)
        paused = store.transition("display", BrowserDeviceState.PAUSED)
        sessions.reconcile()
        await asyncio.wait_for(final_send.wait(), 1)
        assert not await asyncio.to_thread(sessions.acknowledge, paused, timeout=0.01)
        finish_send.set()
        await asyncio.wait_for(request, 1)
        assert await asyncio.to_thread(sessions.acknowledge, paused)
    asyncio.run(check())


def test_http_lifespan_provides_private_owner_ack_and_rejects_second_owner(setup) -> None:
    client, store, device, _, _, _ = setup
    auth = WebDashboardAuthentication("different fictional operator password", ORIGIN)
    second = BrowserDeviceHTTP(FastAPI(), authentication=auth,
                               devices=BrowserDeviceSessions(store))
    with pytest.raises(BrowserOwnerError), TestClient(second, base_url=ORIGIN):
        pass
    assert exchange(client, device.credential).status_code == 200
    paused = store.transition("display", BrowserDeviceState.PAUSED)
    receipt = request_browser_owner_ack(store.path, paused)
    assert receipt.completed and receipt.owner_pid == os.getpid()


def test_device_http_refuses_to_serve_without_owner_lifespan(setup) -> None:
    _, store, _, _, _, _ = setup
    auth = WebDashboardAuthentication("different fictional operator password", ORIGIN)
    second = BrowserDeviceHTTP(FastAPI(), authentication=auth,
                               devices=BrowserDeviceSessions(store))
    client = TestClient(second, base_url=ORIGIN)
    try:
        assert client.get("/auth/login").status_code == 503
    finally:
        client.close()
        second._workers.close()
