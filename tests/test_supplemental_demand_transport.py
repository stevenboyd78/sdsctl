"""Separate authenticated mutation on one real native loopback owner; no hardware."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sds200.daemon_api import (
    DAEMON_API_CONTROL_OPERATIONS,
    DAEMON_API_READ_ONLY_OPERATIONS,
    DaemonApiRequest,
    DaemonReadOnlyApi,
)
from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_client import DaemonApiClient
from sds200.daemon_ipc import DaemonSocketListener, resolve_daemon_socket_location
from sds200.daemon_remote_server import DAEMON_REMOTE_OBSERVE_OPERATIONS
from sds200.daemon_server import DaemonApiServer
from sds200.exceptions import DaemonDisconnectedError
from sds200.scanner_display_supplemental_reader import (
    SupplementalFrameReader,
    daemon_supplemental_source,
)
from sds200.scanner_display_supplemental_transport import (
    SupplementalContextChanged,
    SupplementalDeliveryService,
    SupplementalDemandUnconfirmed,
    decode_context_response,
    decode_demand_response,
    validate_renewal_id,
)
from sds200.web_dashboard import (
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
    create_web_dashboard_app,
)
from sds200.web_supplemental import (
    DEMAND_PATH,
    FRAME_PATH,
    RENEWAL_HEADER,
)

from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_supplemental_acquisition import rig as rig
from .test_scanner_display_supplemental_transport import (
    ORIGIN,
    headers,
    login,
    request,
    web,
)


@pytest.fixture
def service(rig):
    return SupplementalDeliveryService(
        rig.owner.frames, clock=lambda: rig.clock.now, acquisition=rig.owner
    )


@pytest.fixture
def api(rig, service):
    return DaemonReadOnlyApi(
        rig.runtime, display_frames=rig.owner.frames, supplemental_display=service
    )


@contextmanager
def unix(tmp_path, api):
    location = resolve_daemon_socket_location(tmp_path / "s")
    server = DaemonApiServer(DaemonSocketListener(location), api)
    server.start()
    try:
        yield lambda: DaemonApiClient(location)
    finally:
        server.stop()


def renewal_headers(context, nonce=None):
    return {**headers(context), RENEWAL_HEADER: nonce or str(uuid4()), "Origin": ORIGIN}


def test_exact_owner_runtime_and_feed_required(rig, service):
    for owner in (object(), SimpleNamespace(frames=rig.owner.frames)):
        with pytest.raises(TypeError):
            SupplementalDeliveryService(rig.owner.frames, acquisition=owner)
    with pytest.raises(TypeError):
        SupplementalDeliveryService(object(), acquisition=rig.owner)
    for runtime, frames in ((SimpleNamespace(), rig.owner.frames), (rig.runtime, None)):
        with pytest.raises(ValueError):
            DaemonReadOnlyApi(runtime, display_frames=frames, supplemental_display=service)
    assert rig.peer.reads == []


def test_only_explicit_owner_advertises_mutation_with_observe_grant(rig, service, api):
    for enabled in (False, True):
        selected = api if enabled else DaemonReadOnlyApi(rig.runtime)
        payload = DaemonApiRequest("hello", Op.HELLO, {}).as_dict()
        result = json.loads(
            selected.handle_authorized_json_line(
                json.dumps(payload), allowed_operations=DAEMON_REMOTE_OBSERVE_OPERATIONS
            )
        )["result"]
        assert (Op.DISPLAY_SUPPLEMENTAL_DEMAND in result["operations"]) is enabled
        assert result["read_only"] is not enabled
        assert result["control_operations"] == []
    assert Op.DISPLAY_SUPPLEMENTAL_DEMAND not in DAEMON_API_READ_ONLY_OPERATIONS
    assert Op.DISPLAY_SUPPLEMENTAL_DEMAND not in DAEMON_API_CONTROL_OPERATIONS
    assert rig.peer.reads == []


def test_passive_reads_then_initial_epoch_ack_and_coalesced_renewal(rig, service):
    before = decode_context_response(service.context())
    assert rig.owner.arm()
    for _ in range(3):
        service.context()
        service.frame(asdict(before))
        rig.owner.frames.snapshot()
    assert rig.peer.reads == []
    nonce = str(uuid4())
    first = service.demand(asdict(before), nonce)
    after = decode_demand_response(first, before, nonce)
    assert after.context_revision == before.context_revision + 1
    wait_for(lambda: rig.peer.reads == ["FQK"])
    wait_for(lambda: rig.owner._cache._pending is None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: service.demand(asdict(after), str(uuid4())), range(20)))
    assert all(reply["context"] == asdict(after) for reply in replies)
    assert rig.peer.reads == ["FQK"]
    assert service.frame(asdict(after))["supplemental"]["favorites"]["value"] is not None
    assert rig.owner.status().read_attempts == 1


def test_expired_lease_advances_once_and_no_cached_read_can_extend(rig, service):
    assert rig.owner.arm()
    first = service.demand(service.context()["context"], str(uuid4()))["context"]
    wait_for(lambda: rig.peer.reads == ["FQK"])
    rig.clock.now = 15
    wait_for(lambda: rig.owner.frames.supplemental_frame_set() is not None)
    current = service.context()["context"]
    for _ in range(4):
        service.frame(current)
    assert not rig.owner._cache.snapshot().active
    second = service.demand(current, str(uuid4()))["context"]
    assert second["context_revision"] == current["context_revision"] + 1
    assert second != first
    rig.clock.now = 85
    wait_for(lambda: rig.owner.frames.supplemental_frame_set() is not None)
    # Expiry may retire the cache epoch between context() and demand(). Both
    # rejection paths are correct: an already-stale context is refused before
    # renewal, or the matching context reaches the now-ended acquisition owner.
    # Do not make worker scheduling order part of the acceptance contract.
    with pytest.raises((SupplementalDemandUnconfirmed, SupplementalContextChanged)):
        service.demand(service.context()["context"], str(uuid4()))
    assert rig.owner.status().reason == "window_expired"
    assert not rig.owner._cache.snapshot().active
    assert not rig.owner.arm()


@pytest.mark.parametrize("retire_between_calls", [False, True])
def test_ended_window_rejects_both_current_and_newly_retired_context(
    rig, service, monkeypatch, retire_between_calls
):
    assert rig.owner.arm()
    service.demand(service.context()["context"], str(uuid4()))
    wait_for(lambda: rig.peer.reads == ["FQK"])
    wait_for(lambda: rig.owner._cache._pending is None)
    rig.owner._end("window_expired")
    context = service.context()["context"]
    attempts = rig.owner.status().read_attempts
    if retire_between_calls:
        original = service._capture

        def retired():
            rig.owner._cache.clear_demand()
            return original()

        monkeypatch.setattr(service, "_capture", retired)
    error = SupplementalContextChanged if retire_between_calls else SupplementalDemandUnconfirmed
    with pytest.raises(error):
        service.demand(context, str(uuid4()))
    assert rig.owner.status().reason == "window_expired"
    assert rig.owner.status().read_attempts == attempts
    assert rig.peer.reads == ["FQK"]
    assert not rig.owner._cache.snapshot().active and not rig.owner.arm()


@pytest.mark.parametrize(
    "field",
    [
        "endpoint_id",
        "stream_id",
        "session_id",
        "profile_revision",
        "profile_invalidation",
        "context_revision",
    ],
)
def test_stale_context_cannot_mutate_any_binding_field(rig, service, field):
    assert rig.owner.arm()
    context = service.context()["context"]
    context[field] = (
        context[field] + 1
        if isinstance(context[field], int)
        else "0" * 64
        if field == "profile_revision"
        else str(uuid4())
    )
    with pytest.raises(SupplementalContextChanged):
        service.demand(context, str(uuid4()))
    assert not rig.owner._cache.snapshot().active and rig.peer.reads == []


@pytest.mark.parametrize(
    "nonce",
    [None, True, 1, "", "x" * 10000, str(uuid4()).upper(), "00000000-0000-0000-0000-000000000000"],
)
def test_bad_nonce_rejected(nonce):
    with pytest.raises(ValueError):
        validate_renewal_id(nonce)


def test_authorization_and_shapes_checked_before_mutation(rig, service, api):
    assert rig.owner.arm()
    context = service.context()["context"]
    params = {"context": context, "renewal_id": str(uuid4())}
    payload = DaemonApiRequest("demand", Op.DISPLAY_SUPPLEMENTAL_DEMAND, params).as_dict()
    denied = json.loads(
        api.handle_authorized_json_line(
            json.dumps(payload), allowed_operations=(Op.HELLO, Op.DISPLAY_SUPPLEMENTAL_FRAME)
        )
    )
    assert denied["error"]["code"] == "authorization_denied"
    for bad in ({}, {"context": context}, {**params, "extra": 1}, {**params, "renewal_id": True}):
        assert request(api, Op.DISPLAY_SUPPLEMENTAL_DEMAND, bad).error.code == "invalid_parameters"
    assert api.handle_control_payload(payload).error.code == "unknown_operation"
    assert not rig.owner._cache.snapshot().active and rig.peer.reads == []


@pytest.mark.parametrize("damage", ["nonce", "epoch", "identity", "shape", "version", "lease"])
def test_ack_replay_or_context_mismatch_rejected(rig, service, damage):
    assert rig.owner.arm()
    binding = decode_context_response(service.context())
    nonce = str(uuid4())
    ack = deepcopy(service.demand(asdict(binding), nonce))
    if damage == "nonce":
        nonce = str(uuid4())
    elif damage == "epoch":
        ack["context"]["context_revision"] += 1
    elif damage == "identity":
        ack["context"]["session_id"] = str(uuid4())
    elif damage == "shape":
        ack["extra"] = "PRIVATE"
    elif damage == "version":
        ack["version"] = True
    else:
        ack["lease_seconds"] = 999
    with pytest.raises(ValueError):
        decode_demand_response(ack, binding, nonce)


def test_post_mutation_capture_failure_is_uncertain_not_clean_refusal(rig, service, monkeypatch):
    assert rig.owner.arm()
    context = service.context()["context"]
    capture = service._capture
    called = []

    def fail_after():
        called.append(1)
        if len(called) > 1:
            raise ValueError("PRIVATE")
        return capture()

    monkeypatch.setattr(service, "_capture", fail_after)
    with pytest.raises(SupplementalDemandUnconfirmed, match="reads may have occurred") as error:
        service.demand(context, str(uuid4()))
    assert "PRIVATE" not in str(error.value)
    wait_for(lambda: rig.peer.reads == ["FQK"])


def test_native_display_http_unix_roundtrip_and_admission(rig, service, api, tmp_path):
    with (
        unix(tmp_path, api) as factory,
        TestClient(
            web(factory, supplemental_delivery=True, supplemental_demand=True), base_url=ORIGIN
        ) as client,
    ):
        context = service.context()["context"]
        good = renewal_headers(context)
        assert client.post(DEMAND_PATH, headers=good).status_code == 401
        login(client)
        assert rig.owner.arm()
        for bad in (
            {},
            {**good, "Origin": "https://evil.example"},
            {**good, "Sec-Fetch-Site": "cross-site"},
        ):
            assert client.post(DEMAND_PATH, headers=bad).status_code == 403
        for changes in ({RENEWAL_HEADER: "bad"}, {"x-sdsctl-supplemental-version": "2"}):
            assert client.post(DEMAND_PATH, headers={**good, **changes}).status_code == 422
        assert client.post(DEMAND_PATH, headers=good, content=b"not empty").status_code == 422
        assert client.post(DEMAND_PATH + "?extra=1", headers=good).status_code == 422
        assert (
            client.post(
                DEMAND_PATH, headers=list(good.items()) + [(RENEWAL_HEADER, str(uuid4()))]
            ).status_code
            == 422
        )
        assert client.get(DEMAND_PATH, headers=good).status_code == 403
        for path in (FRAME_PATH, "/api/v1/recordings/start", "/api/v1/control/hold"):
            assert client.post(path, headers=good).status_code == 403
        assert rig.peer.reads == []
        result = client.post(DEMAND_PATH, headers=good)
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        current = result.json()["context"]
        assert current["context_revision"] == context["context_revision"] + 1
        assert client.post(DEMAND_PATH, headers=good).status_code == 409
        wait_for(lambda: rig.peer.reads == ["FQK"])
        assert client.get(FRAME_PATH, headers=headers(current)).status_code == 200
        assert (
            client.post(
                "/auth/logout", headers={"Origin": ORIGIN}, follow_redirects=False
            ).status_code
            == 303
        )
        assert client.post(DEMAND_PATH, headers=renewal_headers(current)).status_code == 401
        rig.clock.now = 15
        Event().wait(0.04)
        assert not rig.owner._cache.snapshot().active


@pytest.mark.parametrize("web_enabled,daemon_enabled", [(False, True), (True, False)])
def test_both_demand_gates_required(rig, service, api, tmp_path, web_enabled, daemon_enabled):
    if not daemon_enabled:
        api = DaemonReadOnlyApi(
            rig.runtime,
            supplemental_display=SupplementalDeliveryService(
                rig.owner.frames, clock=lambda: rig.clock.now
            ),
        )
    with (
        unix(tmp_path, api) as factory,
        TestClient(
            web(factory, supplemental_delivery=True, supplemental_demand=web_enabled),
            base_url=ORIGIN,
        ) as client,
    ):
        login(client)
        assert rig.owner.arm()
        result = client.post(DEMAND_PATH, headers=renewal_headers(service.context()["context"]))
        assert result.status_code == (503 if web_enabled else 403)
        assert rig.peer.reads == []


@pytest.mark.parametrize("value", [None, "true", 0, 1])
def test_demand_flag_exact_bool(value):
    with pytest.raises(TypeError):
        create_web_dashboard_app(
            lambda: None, supplemental_delivery=True, supplemental_demand=value
        )


def test_demand_requires_delivery_and_authentication():
    with pytest.raises(ValueError):
        create_web_dashboard_app(lambda: None, supplemental_demand=True)
    with pytest.raises(ValueError):
        create_web_dashboard_app(lambda: None, supplemental_delivery=True, supplemental_demand=True)


def test_trusted_ingress_only(rig, service, api, tmp_path):
    with unix(tmp_path, api) as factory:
        app = create_web_dashboard_app(
            factory,
            home_assistant_ingress=True,
            supplemental_delivery=True,
            supplemental_demand=True,
        )
        with TestClient(app) as client:
            assert (
                client.post(
                    DEMAND_PATH,
                    headers={
                        **renewal_headers(service.context()["context"]),
                        "X-Forwarded-For": WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
                    },
                ).status_code
                == 403
            )
        assert rig.owner.arm()
        with TestClient(app, client=(WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT, 1234)) as client:
            reply = client.post(DEMAND_PATH, headers=renewal_headers(service.context()["context"]))
            assert reply.status_code == 200
        wait_for(lambda: rig.peer.reads == ["FQK"])


@pytest.mark.parametrize("consumer", [False, True])
def test_managed_cookie_requires_origin_and_live_session(rig, service, api, tmp_path, consumer):
    from sds200.browser_device_http import BROWSER_DEVICE_COOKIE, BROWSER_DEVICE_EXCHANGE_PATH
    from sds200.browser_device_sessions import BrowserDeviceSessions
    from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore

    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(root / "devices.sqlite")
    device = store.enroll("display")
    sessions = BrowserDeviceSessions(store)
    with (
        unix(tmp_path, api) as factory,
        TestClient(
            web(
                factory,
                supplemental_delivery=True,
                supplemental_demand=True,
                supplemental_consumer=consumer,
                browser_device_sessions=sessions,
            ),
            base_url=ORIGIN,
        ) as client,
    ):
        token = client.post(
            BROWSER_DEVICE_EXCHANGE_PATH,
            json={"device_id": "display"},
            headers={"Authorization": "Bearer " + device.credential},
        )
        assert token.status_code == 200
        client.cookies.set(BROWSER_DEVICE_COOKIE, token.json()["token"])
        page = client.get("/").text
        assert 'data-access-mode="display"' in page
        assert ("data-sdsctl-supplemental" in page) is consumer
        assert client.get("/assets/mimic-supplemental.js").status_code == (200 if consumer else 403)
        assert rig.peer.reads == []
        good = renewal_headers(service.context()["context"])
        assert rig.owner.arm()
        assert client.post(DEMAND_PATH, headers={**good, "Origin": "null"}).status_code == 403
        assert (
            client.post(DEMAND_PATH, headers={**good, "Sec-Fetch-Site": "cross-site"}).status_code
            == 403
        )
        assert rig.peer.reads == []
        result = client.post(DEMAND_PATH, headers=good)
        assert result.status_code == 200
        wait_for(lambda: rig.peer.reads == ["FQK"])
        store.transition("display", BrowserDeviceState.REVOKED)
        assert (
            client.post(DEMAND_PATH, headers=renewal_headers(result.json()["context"])).status_code
            == 401
        )
        rig.clock.now = 15
        Event().wait(0.04)
        assert not rig.owner._cache.snapshot().active


def test_two_actual_unix_tui_consumers_share_lease_without_cancelling_sibling(
    rig, service, api, tmp_path
):
    assert rig.owner.arm()
    with unix(tmp_path, api) as factory:
        first = SupplementalFrameReader(daemon_supplemental_source(factory(), demand=True))
        second = SupplementalFrameReader(daemon_supplemental_source(factory(), demand=True))
        try:
            first.set_active(True)
            wait_for(lambda: first.view()[0] is not None)
            second.set_active(True)
            wait_for(lambda: second.view()[0] is not None)
            first.close(wait=True)
            rig.clock.now = 14
            wait_for(lambda: rig.owner._cache._demand_until == 19)
            assert not second._terminal and rig.owner._cache.snapshot().active
            second.close(wait=True)
            rig.clock.now = 19
            wait_for(lambda: rig.owner.frames.supplemental_frame_set() is not None)
            assert not rig.owner._cache.snapshot().active
            assert all(command in {"FQK", "DTM"} for command in rig.peer.reads)
            assert rig.owner.status().read_attempts <= 6
        finally:
            first.close(wait=True)
            second.close(wait=True)


def test_http_lost_daemon_ack_reports_uncertainty_without_replaying(rig, service, api, tmp_path):
    calls = []
    with unix(tmp_path, api) as factory:

        class LostAck:
            def __enter__(self):
                self.client = factory()
                return self

            def __exit__(self, *_):
                self.client.close()

            def hello(self):
                return self.client.hello()

            def display_supplemental_demand(self, context, nonce):
                calls.append(nonce)
                self.client.display_supplemental_demand(context, nonce)
                raise DaemonDisconnectedError("PRIVATE")

        with TestClient(
            web(LostAck, supplemental_delivery=True, supplemental_demand=True), base_url=ORIGIN
        ) as client:
            login(client)
            assert rig.owner.arm()
            response = client.post(
                DEMAND_PATH, headers=renewal_headers(service.context()["context"])
            )
            assert response.status_code == 503
            assert "reads may have occurred" in response.text and "PRIVATE" not in response.text
            assert len(calls) == 1
            wait_for(lambda: rig.peer.reads == ["FQK"])
            rig.clock.now = 15
            Event().wait(0.04)
            assert not rig.owner._cache.snapshot().active
