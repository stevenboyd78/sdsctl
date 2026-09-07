from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sds200.browser_device_admin as admin_module
from sds200.browser_device_admin import BrowserDeviceAdmin
from sds200.browser_device_ingress import (
    BROWSER_ADMIN_PATH,
    BrowserDeviceIngress,
    BrowserDeviceIngressMiddleware,
)
from sds200.browser_device_owner import BrowserOwnerReceipt
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore
from sds200.web_dashboard import create_web_dashboard_app

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux candidate")
UID = "a" * 32
OTHER_UID = "b" * 32
ORIGIN = "https://192.168.0.18"
PEER = ("172.30.32.2", 50000)


def forbidden():
    pytest.fail("Administration must not contact the scanner daemon")


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(root / "devices.sqlite")
    admin = BrowserDeviceAdmin(store)
    configuration = BrowserDeviceIngress(admin, ORIGIN, frozenset({UID, OTHER_UID}))
    app = create_web_dashboard_app(forbidden, home_assistant_ingress=True,
                                   browser_device_admin_ingress=configuration)
    return store, admin, configuration, app


@pytest.fixture
def client(setup):
    with TestClient(setup[3], client=PEER, headers={"X-Remote-User-Id": UID}) as client:
        yield client


def nonce(client):
    response = client.get(BROWSER_ADMIN_PATH)
    assert response.status_code == 200
    return re.search(r'name="csrf" value="([a-f0-9]{64})"', response.text).group(1)


def form(client, action="enroll", record=None, token=None):
    data = {"action": action, "device_id": "display", "confirm": "display",
            "csrf": nonce(client) if token is None else token}
    if record is not None:
        data.update(generation=str(record.generation), state=record.state.value)
    return data


def post(client, data, **kwargs):
    return client.post(BROWSER_ADMIN_PATH, data=data, headers={"Origin": ORIGIN}, **kwargs)


def test_default_and_native_factory_cannot_expose_admin(setup):
    for ingress in [False, True]:
        app = create_web_dashboard_app(forbidden, home_assistant_ingress=ingress)
        with TestClient(app, client=PEER) as client:
            assert client.get(BROWSER_ADMIN_PATH).status_code == 404
            assert "home_assistant_browser_devices" not in client.get("/api/v1").json()["links"]
    with pytest.raises(ValueError):
        create_web_dashboard_app(forbidden, browser_device_admin_ingress=setup[2])


@pytest.mark.parametrize("origin", ["http://192.168.0.18", "https://192.168.0.18/",
                                    "https://user:secret@192.168.0.18", "https://example.com/path"])
def test_invalid_config_origin_refused(setup, origin):
    with pytest.raises(ValueError):
        BrowserDeviceIngress(setup[1], origin, frozenset({UID}))


@pytest.mark.parametrize("ids", [frozenset(), {UID}, frozenset({"admin"}), frozenset({True})])
def test_invalid_allowlist_refused(setup, ids):
    with pytest.raises(ValueError):
        BrowserDeviceIngress(setup[1], ORIGIN, ids)


@pytest.mark.parametrize("headers", [[], [("X-Remote-User-Id", "c" * 32)],
    [("X-Remote-User-Id", UID), ("X-Remote-User-Id", UID)],
    [("Cookie", "__Host-sdsctl-session=operator")],
    [("Cookie", "__Host-sdsctl-device-session=display")],
    [("X-Remote-User-Name", "admin")]])
def test_identity_missing_unlisted_duplicate_or_cookie_never_authorizes(setup, headers):
    with TestClient(setup[3], client=PEER) as client:
        for method in ["GET", "POST"]:
            assert client.request(method, BROWSER_ADMIN_PATH, headers=headers).status_code == 403
    assert setup[0].inventory() == ()


def test_forged_forwarded_peer_cannot_authorize(setup):
    with TestClient(setup[3], client=("192.168.0.9", 123)) as client:
        response = client.get(BROWSER_ADMIN_PATH, headers={"X-Remote-User-Id": UID,
                               "X-Forwarded-For": PEER[0], "X-Real-IP": PEER[0]})
        assert response.status_code == 403


def test_enrollment_is_one_time_attachment_not_html_or_inventory(setup, client):
    links = client.get("/api/v1").json()["links"]
    assert links["home_assistant_browser_devices"] == BROWSER_ADMIN_PATH
    data = form(client)
    response = post(client, data)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="sdsctl-browser-device.json"')
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    document = response.json()
    credential = document["credential"]
    assert document["outcome"] == {"status": "issued", "completed": False}
    assert setup[0].authenticate("display", credential) is not None
    assert credential.encode() not in setup[0].path.read_bytes()
    inventory = client.get(BROWSER_ADMIN_PATH)
    assert credential not in inventory.text
    assert "<script" not in inventory.text
    assert post(client, data).status_code == 400
    assert len(setup[0].inventory()) == 1


def test_rotation_download_survives_unavailable_owner_and_stale_replay(setup, client):
    original = setup[1].enroll("display")
    data = form(client, "rotate", original.record)
    response = post(client, data)
    document = response.json()
    assert document["outcome"]["status"] == "owner_unavailable"
    assert document["outcome"]["completed"] is False
    assert setup[0].authenticate("display", original.credential) is None
    assert setup[0].authenticate("display", document["credential"]).generation == 2
    data["csrf"] = nonce(client)
    conflict = post(client, data)
    assert conflict.status_code == 409
    assert "credential" not in conflict.json()
    assert setup[0].authenticate("display", document["credential"]).generation == 2


@pytest.mark.parametrize("action,state", [("pause", BrowserDeviceState.PAUSED),
                                         ("revoke", BrowserDeviceState.REVOKED)])
def test_state_actions_report_pending_honestly_then_confirm_without_mutation(
    setup, client, monkeypatch, action, state,
):
    issued = setup[1].enroll("display")
    monkeypatch.setattr(admin_module, "request_browser_owner_ack",
                        lambda path, record: BrowserOwnerReceipt(record, "a" * 32, 99, False))
    result = post(client, form(client, action, issued.record))
    assert "Outcome: pending" in result.text
    record = setup[0].inventory()[0]
    assert record.state is state
    monkeypatch.setattr(admin_module, "request_browser_owner_ack",
                        lambda path, record: BrowserOwnerReceipt(record, "a" * 32, 99, True))
    confirmed = post(client, form(client, "confirm", record))
    assert "Outcome: confirmed" in confirmed.text
    assert setup[0].inventory() == (record,)


def test_resume_changes_only_reviewed_paused_device(setup, client):
    issued = setup[1].enroll("display")
    paused = setup[0].transition("display", BrowserDeviceState.PAUSED)
    assert post(client, form(client, "resume", paused)).status_code == 200
    assert setup[0].authenticate("display", issued.credential).generation == 3


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"},
    {"Origin": "null"}, {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
    {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"},
    {"Origin": ORIGIN, "Authorization": "Bearer operator"}])
def test_wrong_origin_fetch_or_authorization_cannot_mutate(setup, client, headers):
    response = client.post(BROWSER_ADMIN_PATH, data=form(client), headers=headers)
    assert response.status_code == 403
    assert setup[0].inventory() == ()


def test_duplicate_origin_refused(setup, client):
    response = client.post(BROWSER_ADMIN_PATH, data=form(client),
                            headers=[("Origin", ORIGIN), ("Origin", ORIGIN)])
    assert response.status_code == 403
    assert setup[0].inventory() == ()


def test_nonce_is_bound_to_user_and_can_only_be_consumed_once(setup, client):
    data = form(client)
    response = client.post(BROWSER_ADMIN_PATH, data=data,
                            headers={"Origin": ORIGIN, "X-Remote-User-Id": OTHER_UID})
    assert response.status_code == 400
    assert post(client, data).status_code == 200
    assert post(client, data).status_code == 400


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "wrong-confirm", "unknown-action",
                                      "oversized", "bad-nonce"])
def test_invalid_form_never_mutates(setup, client, mutation):
    data = form(client)
    if mutation == "extra":
        data["extra"] = "yes"
    elif mutation == "wrong-confirm":
        data["confirm"] = "different"
    elif mutation == "unknown-action":
        data["action"] = "delete-all"
    elif mutation == "oversized":
        data["confirm"] = "a" * 1100
    elif mutation == "bad-nonce":
        data["csrf"] = "unknown"
    body = urlencode(data) + ("&device_id=other" if mutation == "duplicate" else "")
    response = client.post(BROWSER_ADMIN_PATH, content=body,
                            headers={"Origin": ORIGIN,
                                     "Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400
    assert setup[0].inventory() == ()


def test_json_requests_and_query_parameters_refused(setup, client):
    assert client.post(BROWSER_ADMIN_PATH, json=form(client),
                       headers={"Origin": ORIGIN}).status_code == 403
    assert client.get(BROWSER_ADMIN_PATH + "?credential=fictional").status_code == 403
    assert setup[0].inventory() == ()


def test_nonce_capacity_is_bounded(client):
    for _ in range(32):
        assert client.get(BROWSER_ADMIN_PATH).status_code == 200
    assert client.get(BROWSER_ADMIN_PATH).status_code == 503


def test_nonce_expiry_prevents_mutation(setup, client, monkeypatch):
    import sds200.browser_device_ingress as ingress_module
    original = ingress_module.time.monotonic
    data = form(client)
    monkeypatch.setattr(ingress_module.time, "monotonic", lambda: original() + 301)
    assert post(client, data).status_code == 400
    assert setup[0].inventory() == ()


def test_mutations_run_off_request_loop(setup, client, monkeypatch):
    original = setup[1].enroll
    called = []

    def enroll(device_id):
        called.append(threading.current_thread().name)
        return original(device_id)

    monkeypatch.setattr(setup[1], "enroll", enroll)
    assert post(client, form(client)).status_code == 200
    assert called and called[0].startswith("browser-device")


def _scope(method="POST"):
    return {"type": "http", "path": BROWSER_ADMIN_PATH, "method": method,
            "client": PEER, "query_string": b"", "headers": [
                (b"x-remote-user-id", UID.encode()), (b"origin", ORIGIN.encode()),
                (b"content-type", b"application/x-www-form-urlencoded")]}


async def _call(middleware, data=None, *, send=None, scope=None):
    messages = []

    async def receive():
        return {"type": "http.request", "body": urlencode(data or {}).encode(),
                "more_body": False}

    async def capture(message):
        messages.append(message)

    await middleware(_scope() if scope is None else scope, receive,
                     capture if send is None else send)
    return messages


def test_no_lifespan_startup_refuses_admin_work(setup):
    middleware = BrowserDeviceIngressMiddleware(FastAPI(), configuration=setup[2])
    try:
        messages = asyncio.run(_call(middleware, scope=_scope("GET")))
        assert messages[0]["status"] == 503
    finally:
        middleware._workers.close()


def test_lost_attachment_delivery_does_not_replay_enrollment(setup):
    async def run():
        middleware = BrowserDeviceIngressMiddleware(FastAPI(), configuration=setup[2])
        middleware._ready = True
        data = {"action": "enroll", "device_id": "display", "confirm": "display",
                "csrf": middleware._nonce(UID)}

        async def disconnected(message):
            raise OSError("Simulated browser disconnected before receiving attachment")

        try:
            with pytest.raises(OSError):
                await _call(middleware, data, send=disconnected)
            assert len(setup[0].inventory()) == 1
            replay = await _call(middleware, data)
            assert replay[0]["status"] == 400
            assert setup[0].inventory()[0].generation == 1
        finally:
            middleware._workers.close()
    asyncio.run(run())


def test_form_capacity_after_commit_does_not_replace_actual_result(setup, monkeypatch):
    from sds200.browser_device_http import _Busy

    async def run():
        middleware = BrowserDeviceIngressMiddleware(FastAPI(), configuration=setup[2])
        middleware._ready = True
        record = setup[1].enroll("display").record
        data = {"action": "pause", "device_id": "display", "confirm": "display",
                "generation": str(record.generation), "state": record.state.value,
                "csrf": middleware._nonce(UID)}

        def full(uid):
            raise _Busy()

        monkeypatch.setattr(middleware, "_nonce", full)
        try:
            response = await _call(middleware, data)
            assert response[0]["status"] == 200
            assert b"owner_unavailable" in response[1]["body"]
            assert setup[0].inventory()[0].state is BrowserDeviceState.PAUSED
        finally:
            middleware._workers.close()
    asyncio.run(run())


def test_two_worker_bound_and_cancelled_mutation_cannot_be_replayed(setup, monkeypatch):
    original = setup[1].enroll
    release = threading.Event()
    started = [threading.Event(), threading.Event()]
    finished = [threading.Event(), threading.Event()]

    def blocking(device_id):
        index = int(device_id[-1])
        started[index].set()
        assert release.wait(3)
        try:
            return original(device_id)
        finally:
            finished[index].set()

    monkeypatch.setattr(setup[1], "enroll", blocking)

    async def run():
        middleware = BrowserDeviceIngressMiddleware(FastAPI(), configuration=setup[2])
        middleware._ready = True
        data = [{"action": "enroll", "device_id": f"display{i}", "confirm": f"display{i}",
                 "csrf": middleware._nonce(UID)} for i in range(3)]
        tasks = [asyncio.create_task(_call(middleware, item)) for item in data[:2]]
        try:
            for event in started:
                assert await asyncio.to_thread(event.wait, 2)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            # Cancellation does not free the underlying worker slot or permit another mutation.
            denied = await _call(middleware, data[2])
            assert denied[0]["status"] == 503
            release.set()
            for event in finished:
                assert await asyncio.to_thread(event.wait, 2)
            await tasks[1]
            replay = await _call(middleware, data[0])
            assert replay[0]["status"] == 400
            assert {record.device_id for record in setup[0].inventory()} == {"display0", "display1"}
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            middleware._workers.close()
    asyncio.run(run())


def test_body_deadline_rejects_stalled_form_before_mutation(setup):
    middleware = BrowserDeviceIngressMiddleware(FastAPI(), configuration=setup[2])
    middleware._ready = True

    async def run():
        messages = []

        async def stalled():
            await asyncio.Event().wait()

        async def capture(message):
            messages.append(message)

        start = time.monotonic()
        await middleware(_scope(), stalled, capture)
        assert 2.5 <= time.monotonic() - start < 5
        return messages

    try:
        result = asyncio.run(run())
        assert result[0]["status"] == 400
        assert setup[0].inventory() == ()
    finally:
        middleware._workers.close()
