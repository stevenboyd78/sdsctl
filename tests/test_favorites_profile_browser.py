from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sds200.web_auth as web_auth
from sds200.favorites_profile_browser import FAVORITES_PROFILE_BROWSER_PATH
from sds200.favorites_profile_store import FavoritesProfileStore
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_dashboard import (
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
    create_web_dashboard_app,
)

ORIGIN = "https://scanner.example:8443"
OPERATOR_PASSWORD = "operator profile password"
DISPLAY_PASSWORD = "display profile password"


def _fast_password_key(password: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password,
        salt=salt,
        n=2,
        r=1,
        p=1,
        maxmem=1024 * 1024,
        dklen=32,
    )


@pytest.fixture(autouse=True)
def _use_fast_password_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_auth, "_derive_password_key", _fast_password_key)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FavoritesProfileStore]:
    with FavoritesProfileStore(tmp_path / "profiles") as profile_store:
        yield profile_store


def _authentication() -> WebDashboardAuthentication:
    return WebDashboardAuthentication(
        OPERATOR_PASSWORD,
        ORIGIN,
        display_password=DISPLAY_PASSWORD,
    )


def _login(client: TestClient, *, display: bool = False) -> None:
    response = client.post(
        "/auth/display/login" if display else "/auth/login",
        data={"password": DISPLAY_PASSWORD if display else OPERATOR_PASSWORD},
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_browser_shell_is_opt_in_operator_only_and_strictly_read_only(
    store: FavoritesProfileStore,
) -> None:
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        assert client.get(FAVORITES_PROFILE_BROWSER_PATH).status_code == 404
        assert client.get("/assets/favorites-profiles.js").status_code == 404

    app = create_web_dashboard_app(
        lambda: None,
        lan_authentication=_authentication(),
        favorites_profile_store=store,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(FAVORITES_PROFILE_BROWSER_PATH).status_code == 401
        _login(client)
        page = client.get(FAVORITES_PROFILE_BROWSER_PATH)
        appearance = client.get("/assets/favorites-appearance.js")
        profiles = client.get("/assets/favorites-profiles.js")
        stylesheet = client.get("/assets/favorites-workspace.css")

    assert page.status_code == 200
    assert "Favorites profiles" in page.text
    assert "Review exact plan" in page.text
    assert "disabled" in page.text
    for response in (page, appearance, profiles, stylesheet):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert "unsafe-inline" not in response.headers["content-security-policy"]
        assert "unsafe-eval" not in response.headers["content-security-policy"]
        assert "usb=()" in response.headers["permissions-policy"]

    routes = {
        (route.path, method)
        for route in app.routes
        for method in (route.methods or set())
        if route.path == FAVORITES_PROFILE_BROWSER_PATH
        or route.path.startswith("/assets/favorites-")
    }
    assert routes == {
        (FAVORITES_PROFILE_BROWSER_PATH, "GET"),
        ("/assets/favorites-appearance.js", "GET"),
        ("/assets/favorites-profiles.js", "GET"),
        ("/assets/favorites-workspace.css", "GET"),
    }


def test_display_identity_cannot_read_browser_shell_or_assets(
    store: FavoritesProfileStore,
) -> None:
    app = create_web_dashboard_app(
        lambda: None,
        lan_authentication=_authentication(),
        favorites_profile_store=store,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        _login(client, display=True)
        page = client.get(FAVORITES_PROFILE_BROWSER_PATH)
        script = client.get("/assets/favorites-profiles.js")

    assert page.status_code == script.status_code == 403
    assert "Favorites profiles" not in page.text
    assert "/api/v1/favorites/profiles" not in script.text


def test_home_assistant_ingress_admits_only_trusted_peer(
    store: FavoritesProfileStore,
) -> None:
    app = create_web_dashboard_app(
        lambda: None,
        home_assistant_ingress=True,
        favorites_profile_store=store,
    )
    with TestClient(app) as untrusted:
        denied = untrusted.get(FAVORITES_PROFILE_BROWSER_PATH)
    with TestClient(
        app,
        client=(WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT, 50000),
    ) as trusted:
        admitted = trusted.get(FAVORITES_PROFILE_BROWSER_PATH)

    assert denied.status_code == 403
    assert admitted.status_code == 200
