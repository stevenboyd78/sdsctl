"""Finite HTTP scope using native admission, real routing and local-only peers.

This is not launcher/source/peer/lifetime or installed-browser qualification.
Even a permissive daemon stub must not expose the ordinary mutating HTTP paths.
"""

import asyncio
import importlib.util
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200 import web_dashboard
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_supplemental import CONTEXT_PATH, DEMAND_PATH, FRAME_PATH

from . import test_web_dashboard as native
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_supplemental_acquisition import rig as rig
from .test_scanner_display_supplemental_transport import ORIGIN, PASSWORD, headers, login
from .test_supplemental_demand_transport import api as api
from .test_supplemental_demand_transport import renewal_headers, unix
from .test_supplemental_demand_transport import service as service

PATH = Path(__file__).resolve().parents[1] / "scripts/supplemental_recording_web_scope.py"
SPEC = importlib.util.spec_from_file_location("supplemental_recording_web_scope", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
PEER = web_dashboard.WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT


def forbidden(*args, **kwargs):
    pytest.fail("Forbidden native handler/client was reached")


def app(factory=forbidden, **kwargs):
    return m.Factory()(factory, home_assistant_ingress=True, **kwargs)


def test_construction_starts_no_client_and_does_not_patch_the_factory():
    selected = m.Factory()
    result = selected(forbidden, forbidden, forbidden, forbidden, home_assistant_ingress=True)
    assert selected.used and type(result) is m._Surface
    assert web_dashboard.create_web_dashboard_app is m._FACTORY
    with pytest.raises(ValueError, match="finite test dashboard"):
        selected(forbidden, home_assistant_ingress=True)
    with TestClient(result, client=(PEER, 1234)) as client:
        page = client.get("/")
        assert page.status_code == 200 and 'data-sdsctl-supplemental="demand"' in page.text
        for path in m._READS:
            if path.startswith("/assets/"):
                assert client.get(path).status_code == 200, path
        assert client.get("/assets/dashboard.css?sdsctl_source=1").status_code == 200
    # Ordinary product construction is unchanged (not forced into this scope).
    with TestClient(web_dashboard.create_web_dashboard_app(forbidden)) as client:
        assert "data-sdsctl-supplemental" not in client.get("/").text


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"home_assistant_ingress": 1},
        {"home_assistant_ingress": None},
        {"lan_authentication": object()},
        {"home_assistant_ingress": True, "lan_authentication": object()},
    ],
)
def test_invalid_auth_selection_consumes_factory_without_native_call(options):
    selected = m.Factory()
    with pytest.raises(ValueError):
        selected(forbidden, **options)
    assert selected.used
    with pytest.raises(ValueError):
        selected(forbidden, home_assistant_ingress=True)


@pytest.mark.parametrize("position", range(4))
def test_invalid_native_factories_fail_before_app_construction(position):
    values = [forbidden] * 4
    values[position] = 7
    with pytest.raises(ValueError):
        m.Factory()(*values, home_assistant_ingress=True)


@pytest.mark.parametrize(
    "key",
    [
        "managed_theme_root",
        "scanner_display_admin_ingress",
        "browser_device_sessions",
        "browser_device_admin_ingress",
        "waterfall_client_factory",
        "supplemental_delivery",
        "supplemental_demand",
        "supplemental_consumer",
        "daemon_args",
        "container_exposure",
    ],
)
def test_extra_configuration_has_no_arbitrary_forwarding(key):
    with pytest.raises(TypeError):
        app(**{key: None})


def test_prepatched_factory_is_not_overwritten_or_used(monkeypatch):
    monkeypatch.setattr(web_dashboard, "create_web_dashboard_app", forbidden)
    with pytest.raises(ValueError):
        app()
    assert web_dashboard.create_web_dashboard_app is forbidden


def test_factory_is_one_use_across_threads():
    selected = m.Factory()

    def call():
        try:
            return selected(forbidden, home_assistant_ingress=True)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: call(), range(8)))
    assert sum(type(value) is m._Surface for value in values) == 1


@pytest.mark.parametrize("peer", ["127.0.0.1", "192.0.2.1", "testclient"])
@pytest.mark.parametrize(
    "path", ["/", "/assets/mimic-supplemental.js", "/api/v1/audio", CONTEXT_PATH]
)
def test_allowed_scope_still_requires_actual_ingress_peer(peer, path):
    with TestClient(app(), client=(peer, 1234)) as client:
        response = client.get(path, headers={"X-Forwarded-For": PEER, "X-Remote-User-Id": "a" * 32})
        assert response.status_code == 403


def test_every_ordinary_mutating_route_is_denied_before_handler_or_body(monkeypatch):
    for name in (
        "execute_home_assistant_integration_ingress_action",
        "reveal_home_assistant_integration_bridge_key",
        "rotate_home_assistant_integration_ingress_bridge_key",
        "rotate_home_assistant_app_advanced_ingress_identity",
        "rotate_home_assistant_app_advanced_ingress_dashboard_password",
        "issue_home_assistant_app_advanced_ingress_client",
        "set_home_assistant_app_advanced_ingress_client_revoked",
        "read_home_assistant_app_advanced_ingress_certificate",
    ):
        monkeypatch.setattr(web_dashboard, name, forbidden)
    result = app()
    paths = []
    for route in result._app.routes:
        path = re.sub(r"\{[^}]+\}", "test", route.path)
        if "POST" in (getattr(route, "methods", None) or set()) and path != DEMAND_PATH:
            paths.append(path)
    assert len(paths) >= 15  # Include host-side administration, not just daemon controls.
    paths += [
        "/api/v1/home-assistant/integration",
        "/api/v1/home-assistant/advanced-access",
        "/api/v1/home-assistant/advanced-access/certificate",
        "/api/v1/openapi.json",
        "/api/v1/docs",
        "/api/v1/redoc",
        "/api/v1/waterfall",
        "/api/v1",
        "/auth/login",
        "/api/v1/home-assistant/scanner-display-profile",
        "/api/v1/new-future-control",
    ]

    async def run():
        async def receive():
            pytest.fail("Rejected route body must not be read")

        for path in paths:
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
                output = []

                async def send(message, output=output):
                    output.append(message)

                await result(
                    {"type": "http", "path": path, "method": method, "client": (PEER, 1)},
                    receive,
                    send,
                )
                assert output[0]["status"] == 404, (method, path)
                assert b"PRIVATE" not in output[1]["body"]
                assert dict(output[0]["headers"])[b"cache-control"] == b"no-store"

    asyncio.run(run())


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/recording/start/",
        "/api/v1/recordings/file/../private",
        "/api/v1/recordings/file//private",
        "/api/v1/recordings/file/./private",
        "/api/v1/recordings/file/",
        "/api/v1/recordings/file/x\\y",
        "/api/v1/recordings/file/x\x00y",
        "/api/v1/recordings/file/x\x7fy",
        "/api/v1/recordings/file/" + "x" * 4096,
        "/assets/themes/unreviewed/theme.css",
        "/assets/themes/lcars/new.js",
        "/new-route",
        "",
        None,
        1,
    ],
)
def test_unknown_and_noncanonical_paths_refuse_without_body_or_delegate(path):
    result = app()

    async def run():
        output = []

        async def receive():
            pytest.fail("Rejected body read")

        async def send(value):
            output.append(value)

        await result({"type": "http", "method": "GET", "path": path}, receive, send)
        assert output[0]["status"] == 404

    asyncio.run(run())


def test_root_path_routing_and_new_routes_remain_closed():
    result = app()
    result._app.get("/future-admin")(forbidden)
    with TestClient(result, root_path="/ingress-test", client=(PEER, 1)) as client:
        assert client.get("/ingress-test/").status_code == 200
        assert client.get("/").status_code == 200
        for path in (
            "/future-admin",
            "/ingress-test/future-admin",
            "/ingress-test/api/v1/recording/start",
        ):
            assert client.get(path).status_code == 404


def test_websocket_denied_without_native_admission_or_receive():
    result = app()

    async def run():
        output = []

        async def send(value):
            output.append(value)

        await result({"type": "websocket", "path": "/api/v1/audio"}, forbidden, send)
        assert output == [{"type": "websocket.close", "code": 1008}]
        with pytest.raises(ValueError):
            await result({"type": "unrecognized"}, forbidden, send)

    asyncio.run(run())


def test_recording_observations_and_media_keep_native_behavior_without_control():
    observed = native.FakeDaemonApiClient(
        hello={"operations": ["recording.status", "recordings.list", "recording.start"]}
    )
    audio = native.FakeDaemonPcmuClient(deliveries=[native.pcmu_delivery(1, b"\xff" * 160)])
    download = native.FakeDaemonRecordingFileClient(payload=b"RIFFsynthetic")
    result = app(
        lambda: observed,
        pcmu_client_factory=lambda: audio,
        recording_file_client_factory=lambda: download,
    )
    with TestClient(result, client=(PEER, 1)) as client:
        assert client.get("/api/v1/recording").status_code == 200
        assert client.get("/api/v1/recordings").status_code == 200
        assert client.post("/api/v1/recording/start").status_code == 404
        assert client.post("/api/v1/recording/stop").status_code == 404
        assert client.get("/api/v1/audio").status_code == 200
        reply = client.get("/api/v1/recordings/file/2026/test.wav")
        assert reply.status_code == 200 and reply.content == b"RIFFsynthetic"
    assert audio.closed and audio.connect_calls == 1
    assert download.identifiers == ["2026/test.wav"] and download.downloads[0].closed
    assert observed.recording_start_calls == observed.recording_stop_calls == 0
    assert observed.control_calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/%72ecording/start",
        "/api/v1/recordings/file/%2e%2e/private",
        "/api/v1/recordings/file/x%5cy",
        "/assets/themes/lcars/%2e%2e/theme.css",
    ],
)
def test_encoded_paths_cannot_escape_the_same_decoded_route_policy(path):
    with TestClient(app(), client=(PEER, 1)) as client:
        assert client.get(path).status_code == 404


def test_native_operator_session_still_cannot_use_host_admin_or_recording_actions():
    download = native.FakeDaemonRecordingFileClient()
    authentication = WebDashboardAuthentication("operator-test-password", ORIGIN)
    result = m.Factory()(
        forbidden,
        recording_file_client_factory=lambda: download,
        lan_authentication=authentication,
    )
    with TestClient(result, base_url=ORIGIN) as client:
        assert client.get("/auth/login").status_code == 200
        assert (
            client.post(
                "/auth/login",
                data={"password": "operator-test-password"},
                headers={"Origin": "null"},
                follow_redirects=False,
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/auth/login",
                data={"password": "operator-test-password"},
                headers={"Origin": ORIGIN},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert client.get("/api/v1/recordings/file/test.wav").status_code == 200
        for path in (
            "/api/v1/recording/start",
            "/api/v1/recording/stop",
            "/api/v1/scanner/next",
            "/api/v1/home-assistant/integration/install",
            "/api/v1/home-assistant/advanced-access/password/rotate",
        ):
            assert client.post(path, headers={"Origin": ORIGIN}).status_code == 404


@pytest.mark.parametrize("mode", ["ingress", "native"])
def test_actual_authenticated_demand_remains_bounded_and_cannot_start_or_stop_recording(
    mode, rig, service, api, tmp_path
):
    options = (
        {"home_assistant_ingress": True}
        if mode == "ingress"
        else {
            "lan_authentication": WebDashboardAuthentication(
                "operator-test-password", ORIGIN, display_password=PASSWORD
            ),
        }
    )
    with unix(tmp_path, api) as factory:
        result = m.Factory()(factory, **options)
        with TestClient(result, base_url=ORIGIN, client=(PEER, 1)) as client:
            if mode == "native":
                assert client.get("/", follow_redirects=False).status_code == 302
                assert client.get(CONTEXT_PATH).status_code == 401
                login(client)
                assert client.get("/auth/session").status_code == 200
                assert (
                    client.get("/api/v1/audio").status_code == 403
                )  # Display-only remains narrower.
            assert client.get("/").status_code == 200
            assert (
                client.get(CONTEXT_PATH, headers={"x-sdsctl-supplemental-version": "1"}).status_code
                == 200
            )
            context = service.context()["context"]
            assert client.get(FRAME_PATH, headers=headers(context)).status_code == 200
            assert rig.peer.reads == [] and not rig.owner.status().armed
            assert rig.owner.arm()  # Explicit test-fixture action, never an HTTP route.
            assert (
                client.post(
                    DEMAND_PATH, headers=renewal_headers(context), content=b"no"
                ).status_code
                == 422
            )
            assert rig.peer.reads == []
            if mode == "native":
                assert (
                    client.post(
                        DEMAND_PATH, headers={**renewal_headers(context), "Origin": "null"}
                    ).status_code
                    == 403
                )
                assert rig.peer.reads == []
            assert client.post(DEMAND_PATH, headers=renewal_headers(context)).status_code == 200
            wait_for(lambda: rig.peer.reads == ["FQK"])
            for path in (
                "/api/v1/recording/start",
                "/api/v1/recording/stop",
                "/api/v1/scanner/reconnect",
            ):
                assert client.post(path, headers={"Origin": ORIGIN}).status_code == 404
            if mode == "native":
                assert (
                    client.post(
                        "/auth/logout", headers={"Origin": ORIGIN}, follow_redirects=False
                    ).status_code
                    == 303
                )
                assert client.get(CONTEXT_PATH).status_code == 401
