"""Offline authenticated HTTP/Unix delivery; never accesses a real scanner."""

import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_api import DaemonApiRequest, DaemonReadOnlyApi
from sds200.daemon_client import DaemonApiClient
from sds200.daemon_ipc import DaemonSocketListener, resolve_daemon_socket_location
from sds200.daemon_remote_server import DAEMON_REMOTE_OBSERVE_OPERATIONS
from sds200.daemon_server import DaemonApiServer
from sds200.exceptions import DaemonProtocolError, DaemonRequestError
from sds200.scanner_display_profile_storage import DisplayProfileStorageError
from sds200.scanner_display_supplemental_transport import (
    SupplementalContextChanged,
    SupplementalDeliveryService,
    SupplementalUnavailable,
    decode_context_response,
    validate_bundle,
)
from sds200.scanner_display_supplemental_wire import project_supplemental_web_bundle
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_dashboard import (
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
    create_web_dashboard_app,
)
from sds200.web_supplemental import (
    CONTEXT_HEADER,
    CONTEXT_PATH,
    FRAME_PATH,
    OPERATIONS,
    VERSION_HEADER,
)

from .test_daemon_display_frames import accept, profile_bytes
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import scan
from .test_daemon_supplemental_reads import engine as engine
from .test_daemon_supplemental_reads import wires
from .test_scanner_display_supplemental_presentation import capture as capture

ORIGIN = "https://scanner.example.test"
PASSWORD = "display-password-for-tests"
VERSION = {VERSION_HEADER: "1"}


@pytest.fixture
def service(capture, engine):
    feed, _, _, _, clock = engine
    return SupplementalDeliveryService(feed, clock=lambda: clock.now)


@pytest.fixture
def context(service):
    return service.context()["context"]


@pytest.fixture
def bundle(service, context):
    return service.frame(context)


def request(api, operation, params=None):
    return api.handle_payload(DaemonApiRequest("test", operation, params or {}).as_dict())


def headers(context):
    return {**VERSION, CONTEXT_HEADER: json.dumps(context)}


def login(client):
    result = client.post(
        "/auth/display/login",
        data={"password": PASSWORD},
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    assert result.status_code == 303


def web(factory, **options):
    return create_web_dashboard_app(
        factory,
        lan_authentication=WebDashboardAuthentication(
            "operator-password-for-tests", ORIGIN, display_password=PASSWORD
        ),
        **options,
    )


def test_service_detaches_same_cut_without_demand_or_io(service, context, engine):
    feed, cache, _, scanner, clock = engine
    for _ in range(4):
        result = service.frame(context)
        binding = decode_context_response(service.context())
        assert asdict(binding) == context
        assert validate_bundle(result, binding) == result
        assert result["supplemental"]["clock"]["value"] == "2026-09-17T21:26:59"
        assert len(result["supplemental"]["favorites"]["value"]) == 100
        # Base PSI geometry never bakes auxiliary text into a long-lived frame.
        assert "21:26" not in json.dumps(result["display"])
        result["supplemental"]["clock"]["value"] = "PRIVATE"
    assert wires(scanner) == ["FQK", "DTM"]
    clock.now = 15.5
    scanner.sample(scan("None", "None"))
    for _ in range(5):
        expired = service.frame(service.context()["context"])
        assert expired["supplemental"]["clock"]["value"] is None
        assert expired["supplemental"]["favorites"]["value"] is None
    assert not cache.poll_once() and wires(scanner) == ["FQK", "DTM"]
    feed.close()
    with pytest.raises(SupplementalUnavailable):
        service.context()


def test_service_never_activates_dormant_owner(engine):
    feed, cache, _, scanner, clock = engine
    service = SupplementalDeliveryService(feed, clock=lambda: clock.now)
    context = service.context()["context"]
    for _ in range(5):
        assert service.frame(context)["supplemental"]["clock"]["value"] is None
    assert not cache.poll_once() and scanner.reads == []


@pytest.mark.parametrize("change", ["reconnect", "profile", "profile_repair"])
def test_service_requires_renegotiation_after_context_change(
    service, context, engine, configured, change
):
    _, _, profile, scanner, _ = engine
    if change == "reconnect":
        scanner.connect_event(False)
        with pytest.raises(SupplementalUnavailable):
            service.frame(context)
        scanner.connect_event(True)
        with pytest.raises(SupplementalUnavailable):
            service.context()
        scanner.sample(scan("None", "None"))
    else:
        if change == "profile":
            configured.source_path.write_bytes(profile_bytes(simple=True))
            accept(configured)
        else:
            # Same profile bytes do not erase a failed-reload invalidation epoch.
            state = configured.state_directory / "accepted-profile.json"
            original = state.read_bytes()
            state.write_bytes(b"broken")
            with pytest.raises(DisplayProfileStorageError):
                profile.reload()
            state.write_bytes(original)
        profile.reload()
        if change == "profile_repair":
            # First read observes the retained barrier and discards pre-repair PSI.
            with pytest.raises(SupplementalUnavailable):
                service.context()
        scanner.sample(scan("None", "None"))
    with pytest.raises(SupplementalContextChanged):
        service.frame(context)
    newer = service.context()["context"]
    assert newer != context
    assert service.frame(newer)["supplemental"]["context"] == newer


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
def test_every_binding_field_is_checked(service, context, field):
    other = dict(context)
    other[field] = (
        "0" * 64
        if field == "profile_revision"
        else context[field] + 1
        if field.endswith("invalidation") or field == "context_revision"
        else str(uuid4())
    )
    with pytest.raises(SupplementalContextChanged):
        service.frame(other)


@pytest.mark.parametrize(
    "path,value",
    [
        (("protocol",), "PRIVATE"),
        (("version",), True),
        (("extra",), "PRIVATE"),
        (("display", "source_status"), "changed"),
        (("display", "session_id"), str(uuid4())),
        (("display", "frames", "detail", "sequence"), 987),
        (("display", "frames", "detail", "age_seconds"), 1.25),
        (("display", "frames", "detail", "profile_revision"), "0" * 64),
        (("display", "frames", "detail", "profile_refresh_pending"), True),
        (("display", "frames", "detail", "status"), "stale"),
        (("display", "frames", "detail", "screen"), None),
        (("supplemental", "clock", "value"), "PRIVATE"),
        (("supplemental", "clock", "age_seconds"), float("nan")),
        (("supplemental", "context", "context_revision"), 999),
        (("supplemental", "favorites", "value"), "0" * 300000),
    ],
)
def test_bundle_validation_fails_closed(bundle, context, path, value):
    changed = deepcopy(bundle)
    target = changed
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    binding = decode_context_response(
        {
            "protocol": "sdsctl.supplemental-context",
            "version": 1,
            "context": context,
        }
    )
    with pytest.raises(ValueError, match="^Invalid supplemental display bundle.$"):
        validate_bundle(changed, binding)


def test_delayed_server_projection_is_valid_but_cleared(capture, service):
    binding = decode_context_response(service.context())
    stale = project_supplemental_web_bundle(capture, now=capture.captured_at + 5)
    result = validate_bundle(stale, binding)
    assert result["supplemental"]["clock"]["value"] is None
    assert result["supplemental"]["favorites"]["value"] is None
    assert all(frame["status"] == "stale" for frame in result["display"]["frames"].values())


def test_serialized_bundle_limit_is_enforced(bundle, service, monkeypatch):
    import sds200.scanner_display_supplemental_transport as transport

    binding = decode_context_response(service.context())
    size = len(json.dumps(bundle, allow_nan=False, separators=(",", ":"), ensure_ascii=True))
    monkeypatch.setattr(transport, "MAX_BUNDLE_BYTES", size)
    assert validate_bundle(bundle, binding) == bundle
    monkeypatch.setattr(transport, "MAX_BUNDLE_BYTES", size - 1)
    with pytest.raises(ValueError, match="^Invalid supplemental display bundle.$"):
        validate_bundle(bundle, binding)


def test_daemon_capabilities_params_roles_and_default_disabled(service, context):
    for enabled in (False, True):
        api = DaemonReadOnlyApi(
            SimpleNamespace(), supplemental_display=service if enabled else None
        )
        capabilities = request(api, Op.HELLO).result["operations"]
        assert OPERATIONS.issubset(capabilities) is enabled
        response = request(api, Op.DISPLAY_SUPPLEMENTAL_CONTEXT)
        assert (response.error is None) is enabled
        if not enabled:
            assert response.error.code.value == "unsupported_operation"
        for params in ({}, {"context": {}}, {"context": context, "path": "PRIVATE"}):
            invalid = request(api, Op.DISPLAY_SUPPLEMENTAL_FRAME, params)
            assert invalid.error.code.value == "invalid_parameters"
            assert "PRIVATE" not in invalid.to_json_line().decode()
        req = DaemonApiRequest("test", Op.DISPLAY_SUPPLEMENTAL_FRAME, {"context": context})
        denied = json.loads(
            api.handle_authorized_json_line(
                json.dumps(req.as_dict()), allowed_operations=(Op.HELLO,)
            )
        )
        assert denied["error"]["code"] == "authorization_denied"
        assert api.handle_control_payload(req.as_dict()).error is not None
        allowed = json.loads(
            api.handle_authorized_json_line(
                json.dumps(req.as_dict()), allowed_operations=DAEMON_REMOTE_OBSERVE_OPERATIONS
            )
        )
        assert allowed["ok"] is enabled


@contextmanager
def unix_api(tmp_path, service):
    location = resolve_daemon_socket_location(tmp_path / "s")
    server = DaemonApiServer(
        DaemonSocketListener(location),
        DaemonReadOnlyApi(SimpleNamespace(), supplemental_display=service),
    )
    server.start()
    try:
        yield lambda: DaemonApiClient(location)
    finally:
        server.stop()


def test_real_unix_authenticated_delivery_reconnect_and_renegotiation(service, engine, tmp_path):
    _, _, _, scanner, _ = engine
    with (
        unix_api(tmp_path, service) as factory,
        TestClient(web(factory, supplemental_delivery=True), base_url=ORIGIN) as client,
    ):
        for path in (CONTEXT_PATH, FRAME_PATH):
            assert client.get(path, headers=VERSION).status_code == 401
        login(client)
        negotiation = client.get(CONTEXT_PATH, headers=VERSION)
        assert negotiation.status_code == 200
        context = negotiation.json()["context"]
        reply = client.get(FRAME_PATH, headers=headers(context))
        assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
        assert reply.headers["x-content-type-options"] == "nosniff"
        assert reply.json()["supplemental"]["clock"]["value"]
        for path in (CONTEXT_PATH, FRAME_PATH):
            assert client.post(path, headers={"Origin": ORIGIN}).status_code == 403
        assert client.get("/api/v1/home-assistant/scanner-display-profile").status_code == 403
        scanner.connect_event(False)
        unavailable = client.get(FRAME_PATH, headers=headers(context))
        assert unavailable.status_code == 503
        scanner.connect_event(True)
        scanner.sample(scan("None", "None"))
        changed = client.get(FRAME_PATH, headers=headers(context))
        assert changed.status_code == 409
        assert changed.headers["cache-control"] == "no-store"
        assert "context" not in changed.json() and "session_id" not in changed.text
        new_context = client.get(CONTEXT_PATH, headers=VERSION).json()["context"]
        assert new_context != context
        assert client.get(FRAME_PATH, headers=headers(new_context)).status_code == 200
    assert wires(scanner) == ["FQK", "DTM"]


@pytest.mark.parametrize("enabled", [False, True])
def test_web_and_daemon_must_both_opt_in(tmp_path, enabled):
    with (
        unix_api(tmp_path, None) as factory,
        TestClient(web(factory, supplemental_delivery=enabled), base_url=ORIGIN) as client,
    ):
        login(client)
        assert client.get(CONTEXT_PATH, headers=VERSION).status_code == (503 if enabled else 404)


@pytest.mark.parametrize("value", [None, "true", 0, 1])
def test_opt_in_must_be_exact_bool(value):
    with pytest.raises(TypeError):
        create_web_dashboard_app(lambda: None, supplemental_delivery=value)


def test_opt_in_cannot_expose_anonymous_route():
    with pytest.raises(ValueError, match="authenticated"):
        create_web_dashboard_app(lambda: None, supplemental_delivery=True)


def test_ingress_admits_only_supervisor_even_with_forged_forwarding(service, tmp_path):
    with unix_api(tmp_path, service) as factory:
        app = create_web_dashboard_app(
            factory, home_assistant_ingress=True, supplemental_delivery=True
        )
        with TestClient(app) as untrusted:
            assert (
                untrusted.get(
                    CONTEXT_PATH,
                    headers={
                        **VERSION,
                        "X-Forwarded-For": WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
                        "X-Ingress-Path": "/forged",
                        "X-Remote-User-Id": "forged",
                    },
                ).status_code
                == 403
            )
        with TestClient(
            app, client=(WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT, 50000)
        ) as trusted:
            negotiation = trusted.get(CONTEXT_PATH, headers=VERSION)
            assert negotiation.status_code == 200
            reply = trusted.get(FRAME_PATH, headers=headers(negotiation.json()["context"]))
            assert reply.status_code == 200


def test_managed_device_revocation_blocks_previously_negotiated_context(service, tmp_path):
    from sds200.browser_device_http import BROWSER_DEVICE_COOKIE, BROWSER_DEVICE_EXCHANGE_PATH
    from sds200.browser_device_sessions import BrowserDeviceSessions
    from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore

    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(root / "devices.sqlite")
    device = store.enroll("display")
    sessions = BrowserDeviceSessions(store)
    with (
        unix_api(tmp_path, service) as factory,
        TestClient(
            web(factory, supplemental_delivery=True, browser_device_sessions=sessions),
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
        negotiation = client.get(CONTEXT_PATH, headers=VERSION)
        assert negotiation.status_code == 200
        binding = headers(negotiation.json()["context"])
        assert client.get(FRAME_PATH, headers=binding).status_code == 200
        assert client.post(FRAME_PATH, headers={**binding, "Origin": ORIGIN}).status_code == 403
        store.transition("display", BrowserDeviceState.REVOKED)
        for path in (CONTEXT_PATH, FRAME_PATH):
            assert (
                client.get(path, headers=binding if path == FRAME_PATH else VERSION).status_code
                == 401
            )


def test_saved_context_does_not_replace_expired_authentication(service, tmp_path):
    from sds200.web_auth import WEB_DASHBOARD_AUTH_COOKIE

    now = SimpleNamespace(value=100.0)
    auth = WebDashboardAuthentication(
        "operator-password-for-tests",
        ORIGIN,
        display_password=PASSWORD,
        idle_seconds=60,
        absolute_seconds=120,
        clock=lambda: now.value,
    )
    with unix_api(tmp_path, service) as factory:
        app = create_web_dashboard_app(factory, lan_authentication=auth, supplemental_delivery=True)
        with TestClient(app, base_url=ORIGIN) as client:
            client.cookies.set(WEB_DASHBOARD_AUTH_COOKIE, auth.issue_session(display_only=True))
            context = client.get(CONTEXT_PATH, headers=VERSION).json()["context"]
            assert client.get(FRAME_PATH, headers=headers(context)).status_code == 200
            now.value = 221
            assert client.get(FRAME_PATH, headers=headers(context)).status_code == 401


@pytest.mark.parametrize(
    "path,extra,query",
    [
        (CONTEXT_PATH, [], ""),
        (CONTEXT_PATH, [(VERSION_HEADER, "2")], ""),
        (CONTEXT_PATH, [(VERSION_HEADER, "1"), (VERSION_HEADER, "1")], ""),
        (CONTEXT_PATH, [(VERSION_HEADER, "1"), (CONTEXT_HEADER, "{}")], ""),
        (CONTEXT_PATH, [(VERSION_HEADER, "1")], "?ignored=PRIVATE"),
        (FRAME_PATH, [(VERSION_HEADER, "1")], ""),
        (FRAME_PATH, [(VERSION_HEADER, "1"), (CONTEXT_HEADER, "null")], ""),
        (FRAME_PATH, [(VERSION_HEADER, "1"), (CONTEXT_HEADER, "PRIVATE")], ""),
        (FRAME_PATH, [(VERSION_HEADER, "1"), (CONTEXT_HEADER, "x" * 1025)], ""),
        (FRAME_PATH, [(VERSION_HEADER, "1"), (CONTEXT_HEADER, "{}"), (CONTEXT_HEADER, "{}")], ""),
    ],
)
def test_bad_http_negotiation_rejected_before_daemon(path, extra, query):
    def factory():
        pytest.fail("invalid input must not reach daemon")

    with TestClient(web(factory, supplemental_delivery=True), base_url=ORIGIN) as client:
        login(client)
        result = client.get(path + query, headers=extra)
        assert result.status_code == 422
        assert result.headers["cache-control"] == "no-store" and "PRIVATE" not in result.text


def test_duplicate_json_context_keys_are_rejected_before_daemon(context):
    def factory():
        pytest.fail("duplicate context key reached daemon")

    raw = json.dumps(context)
    for duplicate in ('"session_id"', '"session_\\u0069d"'):
        bad = raw[:-1] + f', {duplicate}: "{context["session_id"]}"' + "}"
        with TestClient(web(factory, supplemental_delivery=True), base_url=ORIGIN) as client:
            login(client)
            result = client.get(FRAME_PATH, headers={**VERSION, CONTEXT_HEADER: bad})
            assert result.status_code == 422


@pytest.mark.parametrize("operation", ["context", "frame"])
def test_client_bad_response_closes_connection(tmp_path, context, monkeypatch, operation):
    client = DaemonApiClient(resolve_daemon_socket_location(tmp_path / "unused"))
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True))
    monkeypatch.setattr(client, "request", lambda *a, **kw: {"PRIVATE": "bad"})
    with pytest.raises(DaemonProtocolError, match="^Invalid supplemental"):
        if operation == "context":
            client.display_supplemental_context()
        else:
            client.display_supplemental_frame(context)
    assert closed == [True]


def test_client_snapshots_request_context_before_sending(tmp_path, context, bundle, monkeypatch):
    client = DaemonApiClient(resolve_daemon_socket_location(tmp_path / "unused"))
    original = deepcopy(context)

    def send(op, *, params):
        context["session_id"] = str(uuid4())
        assert params == {"context": original}
        return bundle

    monkeypatch.setattr(client, "request", send)
    assert client.display_supplemental_frame(context) == bundle
    with pytest.raises(ValueError):
        client.display_supplemental_frame({"PRIVATE": "invalid"})


@pytest.mark.parametrize(
    "error",
    [
        OSError("PRIVATE"),
        ValueError("PRIVATE"),
        DaemonRequestError("supplemental_unavailable", "PRIVATE", request_id="test"),
        DaemonRequestError("supplemental_context_changed", "PRIVATE", request_id="test"),
    ],
)
def test_http_errors_are_fixed_and_not_cacheable(context, error):
    class Client:
        def hello(self):
            return {"operations": sorted(OPERATIONS)}

        def display_supplemental_frame(self, context):
            raise error

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with TestClient(web(Client, supplemental_delivery=True), base_url=ORIGIN) as client:
        login(client)
        result = client.get(FRAME_PATH, headers=headers(context))
        assert result.status_code == (
            409
            if isinstance(error, DaemonRequestError)
            and error.code == "supplemental_context_changed"
            else 503
        )
        assert "PRIVATE" not in result.text and result.headers["cache-control"] == "no-store"
