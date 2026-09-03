from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import sds200.web_dashboard as web_dashboard


def _ingress_client(app: object) -> TestClient:
    return TestClient(
        app,
        client=(
            web_dashboard.WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
            50000,
        ),
    )


def _forbidden_daemon_factory() -> object:
    raise AssertionError("advanced-access request must not reach scanner daemon")


def test_advanced_access_routes_exist_only_in_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = {"configuration": {}, "lifecycle": {}}
    monkeypatch.setattr(
        web_dashboard,
        "home_assistant_app_advanced_ingress_status",
        lambda: status,
    )
    normal = web_dashboard.create_web_dashboard_app(_forbidden_daemon_factory)
    ingress = web_dashboard.create_web_dashboard_app(
        _forbidden_daemon_factory,
        home_assistant_ingress=True,
    )

    with TestClient(normal) as client:
        normal_status = client.get("/api/v1/home-assistant/advanced-access")
        normal_certificate = client.get(
            "/api/v1/home-assistant/advanced-access/certificate"
        )
        normal_index = client.get("/api/v1")
    with _ingress_client(ingress) as client:
        ingress_status = client.get("/api/v1/home-assistant/advanced-access")
        ingress_index = client.get("/api/v1")

    assert normal_status.status_code == 404
    assert normal_certificate.status_code == 404
    assert "home_assistant_advanced_access" not in normal_index.json()["links"]
    assert ingress_status.status_code == 200
    assert ingress_status.json() == status
    assert ingress_index.json()["links"]["home_assistant_advanced_access"] == (
        "/api/v1/home-assistant/advanced-access"
    )


def test_advanced_access_identity_and_password_routes_require_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        web_dashboard,
        "rotate_home_assistant_app_advanced_ingress_identity",
        lambda confirmation: calls.append(("identity", confirmation))
        or {"action": "identity"},
    )
    monkeypatch.setattr(
        web_dashboard,
        "rotate_home_assistant_app_advanced_ingress_dashboard_password",
        lambda confirmation: calls.append(("password", confirmation))
        or {"password": "one-time-password"},
    )
    app = web_dashboard.create_web_dashboard_app(
        _forbidden_daemon_factory,
        home_assistant_ingress=True,
    )

    with _ingress_client(app) as client:
        missing = client.post(
            "/api/v1/home-assistant/advanced-access/identity/rotate",
            json={},
        )
        identity = client.post(
            "/api/v1/home-assistant/advanced-access/identity/rotate",
            json={"confirm": "INITIALIZE"},
        )
        password = client.post(
            "/api/v1/home-assistant/advanced-access/password/rotate",
            json={"confirm": "ROTATE"},
        )

    assert missing.status_code == 422
    assert identity.json() == {"action": "identity"}
    assert password.json() == {"password": "one-time-password"}
    assert calls == [("identity", "INITIALIZE"), ("password", "ROTATE")]


def test_advanced_access_client_routes_forward_exact_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object, object, object, object]] = []

    def issue(**kwargs: object) -> dict[str, object]:
        calls.append(
            (
                "issue",
                kwargs["client_id"],
                kwargs["control"],
                kwargs["replace"],
                kwargs["confirmation"],
            )
        )
        return {"client_id": kwargs["client_id"]}

    def revoke(**kwargs: object) -> dict[str, object]:
        calls.append(
            (
                "revoke",
                kwargs["client_id"],
                kwargs["revoked"],
                None,
                kwargs["confirmation"],
            )
        )
        return {"revoked": kwargs["revoked"]}

    monkeypatch.setattr(
        web_dashboard,
        "issue_home_assistant_app_advanced_ingress_client",
        issue,
    )
    monkeypatch.setattr(
        web_dashboard,
        "set_home_assistant_app_advanced_ingress_client_revoked",
        revoke,
    )
    app = web_dashboard.create_web_dashboard_app(
        _forbidden_daemon_factory,
        home_assistant_ingress=True,
    )

    with _ingress_client(app) as client:
        issue_response = client.post(
            "/api/v1/home-assistant/advanced-access/clients",
            json={
                "client_id": "pi-display",
                "control": False,
                "replace": False,
                "confirm": "pi-display",
            },
        )
        extra = client.post(
            "/api/v1/home-assistant/advanced-access/clients",
            json={
                "client_id": "pi-display",
                "control": False,
                "replace": False,
                "confirm": "pi-display",
                "extra": True,
            },
        )
        wrong_types = client.post(
            "/api/v1/home-assistant/advanced-access/clients",
            json={
                "client_id": "pi-display",
                "control": "false",
                "replace": False,
                "confirm": "pi-display",
            },
        )
        revoke_response = client.post(
            "/api/v1/home-assistant/advanced-access/clients/pi-display/revoked",
            json={"revoked": True, "confirm": "pi-display"},
        )

    assert issue_response.status_code == 200
    assert extra.status_code == 422
    assert wrong_types.status_code == 422
    assert revoke_response.status_code == 200
    assert calls == [
        ("issue", "pi-display", False, False, "pi-display"),
        ("revoke", "pi-display", True, None, "pi-display"),
    ]


def test_advanced_access_public_certificate_download_is_bounded_to_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = b"-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----\n"
    monkeypatch.setattr(
        web_dashboard,
        "read_home_assistant_app_advanced_ingress_certificate",
        lambda: certificate,
    )
    app = web_dashboard.create_web_dashboard_app(
        _forbidden_daemon_factory,
        home_assistant_ingress=True,
    )

    with _ingress_client(app) as client:
        response = client.get(
            "/api/v1/home-assistant/advanced-access/certificate"
        )

    assert response.status_code == 200
    assert response.content == certificate
    assert response.headers["content-type"].startswith("application/x-pem-file")
    assert response.headers["content-disposition"] == (
        'attachment; filename="sdsctl-remote-server.crt"'
    )
    assert response.headers["cache-control"] == "no-store"
