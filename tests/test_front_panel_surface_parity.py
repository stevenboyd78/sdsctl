from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from sds200.daemon_api import DaemonApiOperation
from sds200.front_panel_keys import front_panel_inventory_snapshot
from sds200.web_dashboard import (
    WEB_DASHBOARD_API_PROTOCOL,
    WEB_DASHBOARD_API_VERSION,
    create_web_dashboard_app,
)


class _FrontPanelDaemonClient:
    def __init__(self, inventory: Mapping[str, object]) -> None:
        self._inventory = dict(inventory)
        self.hello_calls = 0
        self.front_panel_calls = 0
        self.snapshot_calls = 0

    def __enter__(self) -> _FrontPanelDaemonClient:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback

    def hello(self) -> dict[str, object]:
        self.hello_calls += 1
        return {
            "operations": [
                DaemonApiOperation.SCANNER_FRONT_PANEL_INVENTORY.value,
            ]
        }

    def front_panel_inventory(self) -> dict[str, object]:
        self.front_panel_calls += 1
        return dict(self._inventory)

    def runtime_snapshot(self) -> dict[str, object]:
        self.snapshot_calls += 1
        return {"scanner_model": "private model value"}


@pytest.mark.parametrize(
    "model",
    [None, "SDS200", "SDS100", "BCD536HP"],
    ids=["unlisted", "sds200", "sds100", "bcd536hp"],
)
def test_web_projection_preserves_each_fail_closed_model_inventory(
    model: str | None,
) -> None:
    inventory = front_panel_inventory_snapshot(model)
    daemon_client = _FrontPanelDaemonClient(inventory)
    app = create_web_dashboard_app(lambda: daemon_client)

    with TestClient(app) as client:
        response = client.get("/api/v1/scanner/front-panel")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "protocol": WEB_DASHBOARD_API_PROTOCOL,
        "version": WEB_DASHBOARD_API_VERSION,
        "front_panel": inventory,
    }
    assert daemon_client.hello_calls == 1
    assert daemon_client.front_panel_calls == 1
    assert daemon_client.snapshot_calls == 0
    assert "private model value" not in response.text

    keys = inventory["keys"]
    assert isinstance(keys, list)
    assert all(isinstance(entry, Mapping) and entry["available"] is False for entry in keys)
    assert inventory["controls_available"] is False


def test_web_exposes_no_general_front_panel_control_route() -> None:
    app = create_web_dashboard_app(
        lambda: _FrontPanelDaemonClient(front_panel_inventory_snapshot("SDS200"))
    )
    front_panel_routes = [
        route for route in app.routes if isinstance(route, APIRoute) and "front-panel" in route.path
    ]

    assert [(route.path, route.methods) for route in front_panel_routes] == [
        ("/api/v1/scanner/front-panel", {"GET"})
    ]
