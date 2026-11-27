from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sds200.web_auth as web_auth
from sds200.favorites_profile_http import FAVORITES_PROFILE_COLLECTION_PATH
from sds200.favorites_profile_store import (
    FavoritesProfileCompatibility,
    FavoritesProfileStore,
)
from sds200.favorites_storage import FavoritesStorageDocument, FavoritesStorageSnapshot
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_dashboard import (
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
    create_web_dashboard_app,
)

ORIGIN = "https://scanner.example:8443"
OPERATOR_PASSWORD = "operator profile password"
DISPLAY_PASSWORD = "display profile password"
PROFILE_ID = "profile_identifier_000001"
_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


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
def profile_store(tmp_path: Path) -> Iterator[FavoritesProfileStore]:
    store = FavoritesProfileStore(
        tmp_path / "profiles",
        clock=iter((100, 200)).__next__,
        token_factory=iter((PROFILE_ID,)).__next__,
    )
    store.create_profile(
        "Portable",
        FavoritesProfileCompatibility.SDS100,
        FavoritesStorageSnapshot(
            catalog_bytes=b"F-List\tPortable\tf_000001.hpd\r\n",
            documents=(
                FavoritesStorageDocument(
                    filename="f_000001.hpd",
                    content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
                ),
            ),
        ),
    )
    yield store
    store.close()


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


def _native_client(store: FavoritesProfileStore) -> TestClient:
    return TestClient(
        create_web_dashboard_app(
            lambda: None,
            lan_authentication=_authentication(),
            favorites_profile_store=store,
        ),
        base_url=ORIGIN,
    )


def test_profile_routes_are_opt_in_and_require_authenticated_admission(
    profile_store: FavoritesProfileStore,
) -> None:
    with pytest.raises(TypeError, match="Favorites profile store"):
        create_web_dashboard_app(
            lambda: None,
            favorites_profile_store=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="authenticated dashboard admission"):
        create_web_dashboard_app(
            lambda: None,
            favorites_profile_store=profile_store,
        )
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        assert client.get(FAVORITES_PROFILE_COLLECTION_PATH).status_code == 404

    app = create_web_dashboard_app(
        lambda: None,
        lan_authentication=_authentication(),
        favorites_profile_store=profile_store,
    )
    routes = {
        route.path: route.methods
        for route in app.routes
        if route.path.startswith(FAVORITES_PROFILE_COLLECTION_PATH)
    }
    assert routes == {
        FAVORITES_PROFILE_COLLECTION_PATH: {"GET"},
        f"{FAVORITES_PROFILE_COLLECTION_PATH}/{{profile_id}}": {"GET"},
    }


def test_operator_can_list_and_read_only_path_free_current_summaries(
    profile_store: FavoritesProfileStore,
) -> None:
    with _native_client(profile_store) as client:
        assert client.get(FAVORITES_PROFILE_COLLECTION_PATH).status_code == 401
        _login(client)
        listed = client.get(FAVORITES_PROFILE_COLLECTION_PATH)
        selected = client.get(f"{FAVORITES_PROFILE_COLLECTION_PATH}/{PROFILE_ID}")

    assert listed.status_code == selected.status_code == 200
    assert listed.headers["cache-control"] == selected.headers["cache-control"] == "no-store"
    assert listed.headers["x-content-type-options"] == "nosniff"
    assert selected.headers["x-content-type-options"] == "nosniff"
    assert listed.json()["protocol"] == "sdsctl.favorites-profiles"
    assert listed.json()["version"] == 1
    assert listed.json()["profiles"] == [selected.json()["profile"]]
    summary = selected.json()["profile"]
    assert summary["profile_id"] == PROFILE_ID
    assert summary["name"] == "Portable"
    assert summary["compatibility"] == "sds100"
    assert summary["revision"] == summary["revision_count"] == 1
    assert summary["document_count"] == 1
    encoded = str(listed.json()) + str(selected.json())
    assert "f_000001.hpd" not in encoded
    assert "synthetic" not in encoded.lower()
    assert "catalog_bytes" not in encoded
    assert "documents" not in encoded
    assert str(profile_store.database) not in encoded


def test_display_identity_cannot_read_profiles(
    profile_store: FavoritesProfileStore,
) -> None:
    with _native_client(profile_store) as client:
        _login(client, display=True)
        response = client.get(FAVORITES_PROFILE_COLLECTION_PATH)

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert PROFILE_ID not in response.text


def test_profile_reads_fail_closed_without_enumerating_store_state(
    profile_store: FavoritesProfileStore,
) -> None:
    with _native_client(profile_store) as client:
        _login(client)
        malformed = client.get(f"{FAVORITES_PROFILE_COLLECTION_PATH}/bad")
        unknown = client.get(f"{FAVORITES_PROFILE_COLLECTION_PATH}/profile_identifier_999999")
        query = client.get(f"{FAVORITES_PROFILE_COLLECTION_PATH}?page=1")
        profile_store.close()
        unavailable = client.get(FAVORITES_PROFILE_COLLECTION_PATH)

    assert malformed.status_code == unknown.status_code == 404
    assert malformed.json() == unknown.json() == {"detail": "Favorites profile is unavailable."}
    assert query.status_code == 422
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Favorites profile store is unavailable."}
    for response in (malformed, unknown, query, unavailable):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_home_assistant_ingress_admits_only_its_trusted_peer(
    profile_store: FavoritesProfileStore,
) -> None:
    app = create_web_dashboard_app(
        lambda: None,
        home_assistant_ingress=True,
        favorites_profile_store=profile_store,
    )
    with TestClient(app) as untrusted:
        denied = untrusted.get(
            FAVORITES_PROFILE_COLLECTION_PATH,
            headers={"X-Forwarded-For": WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT},
        )
    with TestClient(
        app,
        client=(WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT, 50000),
    ) as trusted:
        admitted = trusted.get(FAVORITES_PROFILE_COLLECTION_PATH)

    assert denied.status_code == 403
    assert admitted.status_code == 200
    assert admitted.json()["profiles"][0]["profile_id"] == PROFILE_ID
