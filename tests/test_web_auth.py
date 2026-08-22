from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Self

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from sds200.web_auth import (
    WEB_DASHBOARD_AUTH_COOKIE,
    WEB_DASHBOARD_AUTHENTICATION_FAILED_DETAIL,
    WEB_DASHBOARD_AUTHENTICATION_REQUIRED_DETAIL,
    WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT,
    WEB_DASHBOARD_LOGIN_FAILURE_LIMIT,
    WEB_DASHBOARD_LOGIN_FAILURE_WINDOW_SECONDS,
    WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL,
    WebDashboardAuthentication,
    WebDashboardAuthenticationMiddleware,
)
from sds200.web_dashboard import create_web_dashboard_app

ORIGIN = "https://scanner.example:8443"
PASSWORD = "correct horse battery staple"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeDaemonApiClient(AbstractContextManager["FakeDaemonApiClient"]):
    def __init__(self) -> None:
        self.hello_calls = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback

    def hello(self) -> Mapping[str, object]:
        self.hello_calls += 1
        return {"protocol": "sdsctl.daemon", "version": 1}

    def runtime_snapshot(self) -> Mapping[str, object]:
        return {"runtime": {"connected": False}, "snapshot": None}


def _authentication(
    *,
    clock: FakeClock | None = None,
    tokens: Iterator[str] | None = None,
    idle_seconds: int = 1800,
    absolute_seconds: int = 28800,
    max_sessions: int = 64,
    max_requests_per_session: int = 16,
) -> WebDashboardAuthentication:
    token_factory = None if tokens is None else lambda: next(tokens)
    return WebDashboardAuthentication(
        PASSWORD,
        ORIGIN,
        idle_seconds=idle_seconds,
        absolute_seconds=absolute_seconds,
        max_sessions=max_sessions,
        max_requests_per_session=max_requests_per_session,
        clock=clock or FakeClock(),
        token_factory=token_factory,
    )


def _client(
    authentication: WebDashboardAuthentication,
    factory: object = FakeDaemonApiClient,
) -> TestClient:
    app = create_web_dashboard_app(
        factory,  # type: ignore[arg-type]
        lan_authentication=authentication,
    )
    return TestClient(app, base_url=ORIGIN)


def _login(client: TestClient, password: str = PASSWORD) -> object:
    return client.post(
        "/auth/login",
        data={"password": password},
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )


def test_authentication_validates_secret_origin_and_session_limits() -> None:
    authentication = _authentication()

    assert authentication.origin == ORIGIN
    assert PASSWORD not in repr(authentication)

    with pytest.raises(ValueError, match="at least 16 characters"):
        WebDashboardAuthentication("too short", ORIGIN)
    with pytest.raises(ValueError, match="one HTTPS origin"):
        WebDashboardAuthentication(PASSWORD, "http://scanner.example")
    with pytest.raises(ValueError, match="one HTTPS origin"):
        WebDashboardAuthentication(PASSWORD, f"{ORIGIN}/dashboard")
    with pytest.raises(ValueError, match="idle lifetime must not exceed"):
        WebDashboardAuthentication(
            PASSWORD,
            ORIGIN,
            idle_seconds=2,
            absolute_seconds=1,
        )
    with pytest.raises(ValueError, match="maximum session count"):
        WebDashboardAuthentication(PASSWORD, ORIGIN, max_sessions=0)
    with pytest.raises(ValueError, match="maximum requests per session"):
        WebDashboardAuthentication(PASSWORD, ORIGIN, max_requests_per_session=0)
    with pytest.raises(ValueError, match="one HTTPS origin"):
        WebDashboardAuthentication(PASSWORD, "https://scanner.example:0")
    with pytest.raises(ValueError, match="one HTTPS origin"):
        WebDashboardAuthentication(PASSWORD, "https://bad_host.example")

    default_port = WebDashboardAuthentication(
        PASSWORD,
        "https://SCANNER.EXAMPLE:443/",
    )
    assert default_port.origin == "https://scanner.example"


def test_authentication_sessions_are_opaque_bounded_and_expiring() -> None:
    clock = FakeClock()
    first = "a" * 43
    second = "b" * 43
    authentication = _authentication(
        clock=clock,
        tokens=iter((first, second)),
        idle_seconds=10,
        absolute_seconds=20,
        max_sessions=1,
    )

    assert authentication.issue_session() == first
    assert authentication.authorize_session(first) == 10
    assert authentication.issue_session() == second
    assert authentication.authorize_session(first) is None
    assert authentication.authorize_session(second) == 10

    clock.now += 11
    assert authentication.authorize_session(second) is None


def test_web_dashboard_authentication_rejects_invalid_factory_options() -> None:
    with pytest.raises(TypeError, match="LAN authentication"):
        create_web_dashboard_app(
            FakeDaemonApiClient,
            lan_authentication=object(),  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="mutually exclusive"):
        create_web_dashboard_app(
            FakeDaemonApiClient,
            home_assistant_ingress=True,
            lan_authentication=_authentication(),
        )


def test_unauthenticated_requests_never_reach_daemon_or_stream_factories() -> None:
    def forbidden_factory() -> object:
        raise AssertionError("unauthenticated request reached a daemon factory")

    with _client(_authentication(), forbidden_factory) as client:
        shell = client.get("/", follow_redirects=False)
        health = client.get("/healthz")
        status = client.get("/api/v1/status")
        events = client.get("/api/v1/events")
        audio = client.get("/api/v1/audio")
        recording = client.get("/api/v1/recordings/file/example.wav")

    assert shell.status_code == 302
    assert shell.headers["location"] == "/auth/login"
    for response in (health, status, events, audio, recording):
        assert response.status_code == 401
        assert response.json() == {"detail": WEB_DASHBOARD_AUTHENTICATION_REQUIRED_DETAIL}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_login_failure_is_generic_secret_free_and_no_store() -> None:
    submitted = "this is the wrong private password"
    with _client(_authentication()) as client:
        response = _login(client, submitted)

    assert response.status_code == 401  # type: ignore[attr-defined]
    assert WEB_DASHBOARD_AUTHENTICATION_FAILED_DETAIL in response.text  # type: ignore[attr-defined]
    assert submitted not in response.text  # type: ignore[attr-defined]
    assert response.headers["cache-control"] == "no-store"  # type: ignore[attr-defined]
    assert WEB_DASHBOARD_AUTH_COOKIE not in response.headers.get(  # type: ignore[attr-defined]
        "set-cookie", ""
    )


def test_login_issues_secure_cookie_and_authorizes_dashboard() -> None:
    daemon = FakeDaemonApiClient()
    with _client(_authentication(), lambda: daemon) as client:
        login = _login(client)
        status = client.get("/api/v1/status")

    assert login.status_code == 303  # type: ignore[attr-defined]
    assert login.headers["location"] == "/"  # type: ignore[attr-defined]
    cookie = login.headers["set-cookie"]  # type: ignore[attr-defined]
    assert cookie.startswith(f"{WEB_DASHBOARD_AUTH_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie
    assert "SameSite=strict" in cookie
    assert "Secure" in cookie
    assert "Domain=" not in cookie
    assert PASSWORD not in cookie
    assert status.status_code == 200
    assert status.headers["cache-control"] == "no-store"
    assert status.headers["strict-transport-security"] == "max-age=31536000"
    assert status.headers["x-content-type-options"] == "nosniff"
    assert daemon.hello_calls == 1


def test_reauthentication_rotates_and_revokes_the_previous_session() -> None:
    first_token = "a" * 43
    second_token = "b" * 43
    authentication = _authentication(tokens=iter((first_token, second_token)))
    with _client(authentication) as client:
        first_login = _login(client)
        second_login = _login(client)
        current_session = client.get("/api/v1/status")

    old_session = _client(authentication)
    try:
        old_session.cookies.set(
            WEB_DASHBOARD_AUTH_COOKIE,
            first_token,
            path="/",
        )
        old_response = old_session.get("/api/v1/status")
    finally:
        old_session.close()

    assert first_login.status_code == 303  # type: ignore[attr-defined]
    assert second_login.status_code == 303  # type: ignore[attr-defined]
    assert current_session.status_code == 200
    assert old_response.status_code == 401


def test_authentication_requires_exact_request_and_mutation_origins() -> None:
    def forbidden_factory() -> object:
        raise AssertionError("hostile-origin request reached daemon")

    authentication = _authentication()
    app = create_web_dashboard_app(
        forbidden_factory,
        lan_authentication=authentication,
    )
    with TestClient(app, base_url="https://other.example:8443") as wrong_host:
        host_response = wrong_host.get("/auth/login")
    with _client(authentication, forbidden_factory) as client:
        missing_login_origin = client.post(
            "/auth/login",
            data={"password": PASSWORD},
        )
        wrong_login_origin = client.post(
            "/auth/login",
            data={"password": PASSWORD},
            headers={"Origin": "https://other.example:8443"},
        )
        _login(client)
        mutation = client.post(
            "/api/v1/recording/start",
            headers={"Origin": "https://other.example:8443"},
        )

    assert host_response.status_code == 400
    assert host_response.json() == {"detail": WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL}
    for response in (missing_login_origin, wrong_login_origin, mutation):
        assert response.status_code == 403
        assert response.json() == {"detail": WEB_DASHBOARD_ORIGIN_REQUIRED_DETAIL}


def test_logout_revokes_only_current_session() -> None:
    authentication = _authentication(tokens=iter(("a" * 43, "b" * 43)))
    first = _client(authentication)
    second = _client(authentication)
    try:
        _login(first)
        _login(second)
        logout = first.post(
            "/auth/logout",
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )

        assert logout.status_code == 303  # type: ignore[attr-defined]
        assert first.get("/api/v1/status").status_code == 401
        assert second.get("/api/v1/status").status_code == 200
    finally:
        first.close()
        second.close()


def test_login_attempts_are_bounded_per_peer_without_leaking_secret() -> None:
    clock = FakeClock()
    authentication = _authentication(clock=clock)
    with _client(authentication) as client:
        failures = [
            _login(client, f"wrong private password {index:02d}")
            for index in range(WEB_DASHBOARD_LOGIN_FAILURE_LIMIT)
        ]
        throttled_correct = _login(client)
        clock.now += WEB_DASHBOARD_LOGIN_FAILURE_WINDOW_SECONDS + 1
        accepted = _login(client)

    for response in (*failures, throttled_correct):
        assert response.status_code == 401  # type: ignore[attr-defined]
        assert PASSWORD not in response.text  # type: ignore[attr-defined]
    assert accepted.status_code == 303  # type: ignore[attr-defined]


def test_global_failure_limit_resists_peer_rotation_without_locking_out_password() -> None:
    authentication = _authentication()

    for index in range(WEB_DASHBOARD_GLOBAL_LOGIN_FAILURE_LIMIT):
        assert not authentication.authenticate_password(
            "wrong private password",
            f"192.0.2.{index}",
        )

    assert authentication.authenticate_password(PASSWORD, "198.51.100.10")


def test_hostile_mutation_does_not_refresh_session_idle_lifetime() -> None:
    clock = FakeClock()
    authentication = _authentication(
        clock=clock,
        idle_seconds=10,
        absolute_seconds=20,
    )
    with _client(authentication) as client:
        _login(client)
        clock.now += 9
        hostile = client.post(
            "/api/v1/recording/start",
            headers={"Origin": "https://other.example:8443"},
        )
        clock.now += 2
        expired = client.get("/api/v1/status")

    assert hostile.status_code == 403
    assert expired.status_code == 401


def test_duplicate_cookie_and_cross_site_fetch_are_rejected() -> None:
    token = "a" * 43
    authentication = _authentication(tokens=iter((token,)))
    with _client(authentication) as client:
        _login(client)
        client.cookies.clear()
        duplicate = client.get(
            "/api/v1/status",
            headers={
                "Cookie": (
                    f"{WEB_DASHBOARD_AUTH_COOKIE}={token}; {WEB_DASHBOARD_AUTH_COOKIE}={token}"
                )
            },
        )
        cross_site = client.get(
            "/api/v1/status",
            headers={
                "Cookie": f"{WEB_DASHBOARD_AUTH_COOKIE}={token}",
                "Sec-Fetch-Site": "cross-site",
            },
        )

    assert duplicate.status_code == 401
    assert cross_site.status_code == 403


def test_expired_session_is_rejected_before_daemon_access() -> None:
    clock = FakeClock()
    daemon = FakeDaemonApiClient()
    authentication = _authentication(
        clock=clock,
        idle_seconds=10,
        absolute_seconds=20,
    )
    with _client(authentication, lambda: daemon) as client:
        _login(client)
        assert client.get("/api/v1/status").status_code == 200
        clock.now += 11
        expired = client.get("/api/v1/status")

    assert expired.status_code == 401
    assert daemon.hello_calls == 1


def test_revocation_closes_an_active_authorized_response() -> None:
    async def exercise() -> None:
        token = "a" * 43
        authentication = _authentication(tokens=iter((token,)))
        assert authentication.issue_session() == token
        response_started = asyncio.Event()
        app_stopped = asyncio.Event()
        messages: list[Message] = []

        async def streaming_app(
            scope: Scope,
            receive: Receive,
            send: Send,
        ) -> None:
            del scope, receive
            try:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-length", b"1024")],
                    }
                )
                response_started.set()
                await asyncio.Event().wait()
            finally:
                app_stopped.set()

        middleware = WebDashboardAuthenticationMiddleware(
            streaming_app,
            authentication=authentication,
        )
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/events",
            "raw_path": b"/api/v1/events",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"scanner.example:8443"),
                (b"cookie", f"{WEB_DASHBOARD_AUTH_COOKIE}={token}".encode()),
            ],
            "client": ("192.0.2.10", 50000),
            "server": ("scanner.example", 8443),
        }

        async def receive() -> Message:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: Message) -> None:
            messages.append(message)

        request = asyncio.create_task(middleware(scope, receive, send))
        await asyncio.wait_for(response_started.wait(), timeout=1)
        authentication.revoke_session(token)
        await asyncio.wait_for(request, timeout=1)

        assert app_stopped.is_set()
        assert [message["type"] for message in messages] == [
            "http.response.start",
            "http.response.body",
        ]
        start_headers = dict(messages[0]["headers"])
        assert start_headers[b"cache-control"] == b"no-store"
        assert b"content-length" not in start_headers
        assert messages[1]["more_body"] is False

    asyncio.run(exercise())


def test_active_requests_are_bounded_per_session() -> None:
    async def exercise() -> None:
        token = "a" * 43
        authentication = _authentication(
            tokens=iter((token,)),
            max_requests_per_session=1,
        )
        assert authentication.issue_session() == token

        first = authentication.acquire_session(token)
        assert first is not None
        assert authentication.acquire_session(token) is None
        first.release()

        replacement = authentication.acquire_session(token)
        assert replacement is not None
        replacement.release()

    asyncio.run(exercise())
