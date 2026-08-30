from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sds200.home_assistant_integration_ingress as integration_ingress
import sds200.web_dashboard as web_dashboard
from sds200.exceptions import SDS200Error
from sds200.home_assistant_integration_lifecycle import (
    HomeAssistantIntegrationImage,
    HomeAssistantIntegrationStatus,
)

_ARTIFACT_DIGEST = "a" * 64
_CURRENT_DIGEST = "b" * 64
_ROLLBACK_DIGEST = "c" * 64
_BRIDGE_DIGEST = "d" * 64


def _status() -> HomeAssistantIntegrationStatus:
    return HomeAssistantIntegrationStatus(
        destination=Path("/homeassistant/custom_components/sdsctl"),
        current_version="0.1.0",
        current_digest=_CURRENT_DIGEST,
        rollback_version="0.0.9",
        rollback_digest=_ROLLBACK_DIGEST,
    )


def _ingress_client(app: object) -> TestClient:
    return TestClient(
        app,
        client=(
            web_dashboard.WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
            50000,
        ),
    )


def test_ingress_status_contains_exact_identities_without_bridge_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        integration_ingress,
        "built_in_home_assistant_integration_image",
        lambda: HomeAssistantIntegrationImage(
            version="0.1.0",
            digest=_ARTIFACT_DIGEST,
            files=(("manifest.json", b"{}"),),
        ),
    )
    monkeypatch.setattr(
        integration_ingress,
        "inspect_home_assistant_integration",
        _status,
    )
    monkeypatch.setattr(
        integration_ingress,
        "home_assistant_integration_bridge_key_digest",
        lambda: _BRIDGE_DIGEST,
    )

    payload = integration_ingress.home_assistant_integration_ingress_status()

    assert payload == {
        "protocol": "sdsctl.home-assistant-integration-lifecycle",
        "version": 1,
        "artifact": {
            "version": "0.1.0",
            "digest": _ARTIFACT_DIGEST,
            "bytes": 2,
        },
        "publication": {
            "destination": "/homeassistant/custom_components/sdsctl",
            "current_version": "0.1.0",
            "current_digest": _CURRENT_DIGEST,
            "rollback_version": "0.0.9",
            "rollback_digest": _ROLLBACK_DIGEST,
        },
        "bridge_key_digest": _BRIDGE_DIGEST,
    }
    assert "bridge_key" not in payload


def test_ingress_action_requires_exact_lowercase_sha256() -> None:
    for confirmation in ("", "a" * 63, "A" * 64, "z" * 64):
        with pytest.raises(
            SDS200Error,
            match="exact lowercase SHA-256 confirmation",
        ):
            integration_ingress.execute_home_assistant_integration_ingress_action(
                "install",
                confirmation_digest=confirmation,
            )


def test_ingress_action_dispatches_exact_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, bool]] = []

    def install(
        *,
        confirmation_digest: str,
        replace: bool = False,
    ) -> HomeAssistantIntegrationStatus:
        observed.append((confirmation_digest, replace))
        return _status()

    monkeypatch.setattr(
        integration_ingress,
        "install_home_assistant_integration",
        install,
    )

    payload = integration_ingress.execute_home_assistant_integration_ingress_action(
        "update",
        confirmation_digest=_ARTIFACT_DIGEST,
    )

    assert observed == [(_ARTIFACT_DIGEST, True)]
    assert payload["action"] == "update"
    assert payload["core_restart_required"] is True
    assert payload["publication"] == {
        "destination": "/homeassistant/custom_components/sdsctl",
        "current_version": "0.1.0",
        "current_digest": _CURRENT_DIGEST,
        "rollback_version": "0.0.9",
        "rollback_digest": _ROLLBACK_DIGEST,
    }


def test_ingress_action_dispatches_each_remaining_lifecycle_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, str]] = []

    def operation(name: str):
        def execute(*, confirmation_digest: str) -> HomeAssistantIntegrationStatus:
            observed.append((name, confirmation_digest))
            return _status()

        return execute

    monkeypatch.setattr(
        integration_ingress,
        "install_home_assistant_integration",
        operation("install"),
    )
    monkeypatch.setattr(
        integration_ingress,
        "rollback_home_assistant_integration",
        operation("rollback"),
    )
    monkeypatch.setattr(
        integration_ingress,
        "remove_home_assistant_integration",
        operation("remove"),
    )
    monkeypatch.setattr(
        integration_ingress,
        "discard_home_assistant_integration_rollback",
        operation("discard-rollback"),
    )

    for action in ("install", "rollback", "remove", "discard-rollback"):
        payload = integration_ingress.execute_home_assistant_integration_ingress_action(
            action,  # type: ignore[arg-type]
            confirmation_digest=_ARTIFACT_DIGEST,
        )
        assert payload["action"] == action

    assert observed == [
        ("install", _ARTIFACT_DIGEST),
        ("rollback", _ARTIFACT_DIGEST),
        ("remove", _ARTIFACT_DIGEST),
        ("discard-rollback", _ARTIFACT_DIGEST),
    ]
    with pytest.raises(ValueError, match="lifecycle action is invalid"):
        integration_ingress.execute_home_assistant_integration_ingress_action(
            "invalid",  # type: ignore[arg-type]
            confirmation_digest=_ARTIFACT_DIGEST,
        )


def test_bridge_key_reveal_is_explicit_and_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        integration_ingress,
        "read_home_assistant_integration_bridge_key",
        lambda: "private-bridge-key",
    )
    monkeypatch.setattr(
        integration_ingress,
        "home_assistant_integration_bridge_key_digest",
        lambda: _BRIDGE_DIGEST,
    )

    assert integration_ingress.reveal_home_assistant_integration_bridge_key() == {
        "protocol": "sdsctl.home-assistant-integration-lifecycle",
        "version": 1,
        "bridge_key": "private-bridge-key",
        "bridge_key_digest": _BRIDGE_DIGEST,
    }


def test_bridge_key_rotation_returns_exact_restart_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def rotate(*, confirmation_digest: str) -> str:
        observed.append(confirmation_digest)
        return "replacement-bridge-key"

    monkeypatch.setattr(
        integration_ingress,
        "rotate_home_assistant_integration_bridge_key",
        rotate,
    )
    monkeypatch.setattr(
        integration_ingress,
        "home_assistant_integration_bridge_key_digest",
        lambda: _BRIDGE_DIGEST,
    )

    payload = integration_ingress.rotate_home_assistant_integration_ingress_bridge_key(
        confirmation_digest=_ARTIFACT_DIGEST,
    )

    assert observed == [_ARTIFACT_DIGEST]
    assert payload == {
        "protocol": "sdsctl.home-assistant-integration-lifecycle",
        "version": 1,
        "bridge_key": "replacement-bridge-key",
        "bridge_key_digest": _BRIDGE_DIGEST,
        "app_restart_required": True,
        "integration_reauthentication_required": True,
    }


def test_ingress_lifecycle_fails_closed_while_an_action_is_running() -> None:
    lock = integration_ingress._HOME_ASSISTANT_INTEGRATION_INGRESS_LOCK
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(
            SDS200Error,
            match="lifecycle action is in progress",
        ):
            integration_ingress.home_assistant_integration_ingress_status()
    finally:
        lock.release()


def test_lifecycle_routes_and_panel_exist_only_in_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_factory() -> object:
        raise AssertionError("lifecycle request must not reach scanner daemon")

    payload = {
        "protocol": "sdsctl.home-assistant-integration-lifecycle",
        "version": 1,
        "artifact": {
            "version": "0.1.0",
            "digest": _ARTIFACT_DIGEST,
            "bytes": 42,
        },
        "publication": {
            "destination": "/homeassistant/custom_components/sdsctl",
            "current_version": None,
            "current_digest": None,
            "rollback_version": None,
            "rollback_digest": None,
        },
        "bridge_key_digest": _BRIDGE_DIGEST,
    }
    monkeypatch.setattr(
        web_dashboard,
        "home_assistant_integration_ingress_status",
        lambda: payload,
    )

    normal = web_dashboard.create_web_dashboard_app(forbidden_factory)
    with TestClient(normal) as client:
        normal_shell = client.get("/")
        normal_status = client.get("/api/v1/home-assistant/integration")
    assert normal_status.status_code == 404
    assert "home-assistant-integration-title" not in normal_shell.text
    assert 'id="pane-tab-home-assistant"' not in normal_shell.text
    assert 'id="pane-home-assistant"' not in normal_shell.text
    assert "workspace-tabs-with-home-assistant" not in normal_shell.text
    assert normal_shell.text.count('role="tab"') == 6
    assert normal_shell.text.count('role="tabpanel"') == 6

    ingress = web_dashboard.create_web_dashboard_app(
        forbidden_factory,
        home_assistant_ingress=True,
    )
    with _ingress_client(ingress) as client:
        ingress_shell = client.get("/")
        ingress_status = client.get("/api/v1/home-assistant/integration")

    assert ingress_shell.status_code == 200
    assert 'id="home-assistant-integration-title"' in ingress_shell.text
    assert 'id="pane-tab-home-assistant"' in ingress_shell.text
    assert 'data-workspace-tab="home-assistant"' in ingress_shell.text
    assert 'id="pane-home-assistant"' in ingress_shell.text
    assert 'data-workspace-pane="home-assistant"' in ingress_shell.text
    assert 'class="workspace-tabs workspace-tabs-with-home-assistant"' in ingress_shell.text
    assert 'class="diagnostics-layout"' in ingress_shell.text
    assert "diagnostics-layout-with-integration" not in ingress_shell.text
    assert ingress_shell.text.count('role="tab"') == 7
    assert ingress_shell.text.count('role="tabpanel"') == 7
    home_assistant_pane = ingress_shell.text[
        ingress_shell.text.index('id="pane-home-assistant"') :
    ]
    diagnostics_pane = ingress_shell.text[
        ingress_shell.text.index('id="pane-diagnostics"') :
    ]
    assert home_assistant_pane.index(
        'id="home-assistant-integration-title"'
    ) < home_assistant_pane.index('id="pane-diagnostics"')
    assert 'id="home-assistant-integration-title"' not in diagnostics_pane.split(
        'id="pane-scanner"', 1
    )[0]
    assert "docker exec" not in ingress_shell.text
    assert ingress_status.status_code == 200
    assert ingress_status.json() == payload
    assert ingress_status.headers["cache-control"] == "no-store"
    assert ingress_status.headers["x-content-type-options"] == "nosniff"


def test_ingress_mutation_route_requires_one_confirm_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_factory() -> object:
        raise AssertionError("lifecycle request must not reach scanner daemon")

    observed: list[tuple[str, str]] = []

    def execute(action: str, *, confirmation_digest: str) -> dict[str, object]:
        observed.append((action, confirmation_digest))
        return {
            "action": action,
            "publication": {},
            "core_restart_required": True,
        }

    monkeypatch.setattr(
        web_dashboard,
        "execute_home_assistant_integration_ingress_action",
        execute,
    )
    app = web_dashboard.create_web_dashboard_app(
        forbidden_factory,
        home_assistant_ingress=True,
    )

    with _ingress_client(app) as client:
        missing = client.post(
            "/api/v1/home-assistant/integration/install",
            json={},
        )
        extra = client.post(
            "/api/v1/home-assistant/integration/install",
            json={"confirm": _ARTIFACT_DIGEST, "extra": True},
        )
        accepted = client.post(
            "/api/v1/home-assistant/integration/install",
            json={"confirm": _ARTIFACT_DIGEST},
        )

    assert missing.status_code == 422
    assert extra.status_code == 422
    assert accepted.status_code == 200
    assert observed == [("install", _ARTIFACT_DIGEST)]
    assert accepted.headers["cache-control"] == "no-store"


def test_ingress_bridge_key_route_never_exists_outside_ingress() -> None:
    def forbidden_factory() -> object:
        raise AssertionError("lifecycle request must not reach scanner daemon")

    app = web_dashboard.create_web_dashboard_app(forbidden_factory)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/home-assistant/integration/bridge-key/reveal",
        )

    assert response.status_code == 404


def test_ingress_index_and_bridge_key_routes_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_factory() -> object:
        raise AssertionError("lifecycle request must not reach scanner daemon")

    observed: list[str] = []
    monkeypatch.setattr(
        web_dashboard,
        "reveal_home_assistant_integration_bridge_key",
        lambda: {"bridge_key": "private-bridge-key"},
    )

    def rotate(*, confirmation_digest: str) -> dict[str, object]:
        observed.append(confirmation_digest)
        return {"bridge_key_digest": _BRIDGE_DIGEST}

    monkeypatch.setattr(
        web_dashboard,
        "rotate_home_assistant_integration_ingress_bridge_key",
        rotate,
    )
    app = web_dashboard.create_web_dashboard_app(
        forbidden_factory,
        home_assistant_ingress=True,
    )

    with _ingress_client(app) as client:
        index = client.get("/api/v1")
        revealed = client.post(
            "/api/v1/home-assistant/integration/bridge-key/reveal",
        )
        invalid = client.post(
            "/api/v1/home-assistant/integration/bridge-key/rotate",
            json={"confirm": None},
        )
        rotated = client.post(
            "/api/v1/home-assistant/integration/bridge-key/rotate",
            json={"confirm": _ARTIFACT_DIGEST},
        )

    assert index.status_code == 200
    assert index.json()["links"]["home_assistant_integration"] == (
        "/api/v1/home-assistant/integration"
    )
    assert revealed.status_code == 200
    assert revealed.json() == {"bridge_key": "private-bridge-key"}
    assert invalid.status_code == 422
    assert rotated.status_code == 200
    assert rotated.json() == {"bridge_key_digest": _BRIDGE_DIGEST}
    assert observed == [_ARTIFACT_DIGEST]


def test_ingress_lifecycle_route_maps_safe_operation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_factory() -> object:
        raise AssertionError("lifecycle request must not reach scanner daemon")

    def fail() -> dict[str, object]:
        raise ValueError("lifecycle unavailable")

    monkeypatch.setattr(
        web_dashboard,
        "reveal_home_assistant_integration_bridge_key",
        fail,
    )
    app = web_dashboard.create_web_dashboard_app(
        forbidden_factory,
        home_assistant_ingress=True,
    )

    with _ingress_client(app) as client:
        response = client.post(
            "/api/v1/home-assistant/integration/bridge-key/reveal",
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "lifecycle unavailable"}
    assert response.headers["cache-control"] == "no-store"
