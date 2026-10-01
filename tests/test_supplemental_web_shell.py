"""Explicit candidate shell wiring; all requests are ASGI/local Node only."""

import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.web_dashboard import (
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
    create_web_dashboard_app,
)

from .test_scanner_display_supplemental_transport import ORIGIN, login, web

HELPER = "/assets/mimic-supplemental.js"


@pytest.mark.parametrize("consumer", [False, True])
@pytest.mark.parametrize("demand", [False, True])
def test_native_display_shell_selection_is_explicit_and_does_not_acquire(consumer, demand):
    calls = []
    app = web(
        lambda: calls.append("daemon"),
        supplemental_delivery=True,
        supplemental_demand=demand,
        supplemental_consumer=consumer,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(HELPER).status_code == 401
        login(client)
        page = client.get("/").text
        assert 'data-access-mode="display"' in page
        assert ("data-sdsctl-supplemental" in page) is consumer
        helper = client.get(HELPER)
        assert helper.status_code == (200 if consumer else 403)
        if consumer:
            mode = "demand" if demand else "cached"
            assert f'data-sdsctl-supplemental="{mode}"' in page
            assert (
                page.index('src="assets/mimic-supplemental.js"')
                < page.index('src="assets/mimic-sds.js"')
                < page.index('src="assets/dashboard.js"')
            )
            assert (
                helper.text
                == files("sds200.web_assets").joinpath("mimic-supplemental.js").read_text()
            )
            assert "no-store" in helper.headers["cache-control"]
        assert not calls


def test_trusted_ingress_shell_and_asset_keep_peer_checks():
    calls = []
    app = create_web_dashboard_app(
        lambda: calls.append("daemon"),
        home_assistant_ingress=True,
        supplemental_delivery=True,
        supplemental_demand=True,
        supplemental_consumer=True,
    )
    with TestClient(app) as client:
        for path in ("/", HELPER):
            assert (
                client.get(
                    path,
                    headers={
                        "X-Forwarded-For": WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT,
                    },
                ).status_code
                == 403
            )
    with TestClient(app, client=(WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT, 1234)) as client:
        page = client.get("/").text
        assert 'data-sdsctl-supplemental="demand"' in page
        assert 'src="assets/mimic-supplemental.js"' in page
        assert client.get(HELPER).status_code == 200
    assert not calls


@pytest.mark.parametrize("value", [None, "true", 0, 1])
def test_consumer_flag_requires_exact_boolean(value):
    with pytest.raises(TypeError):
        web(lambda: None, supplemental_delivery=True, supplemental_consumer=value)


def test_consumer_requires_delivery_and_authenticated_admission():
    with pytest.raises(ValueError):
        web(lambda: None, supplemental_consumer=True)
    with pytest.raises(ValueError):
        create_web_dashboard_app(
            lambda: None, supplemental_delivery=True, supplemental_consumer=True
        )


def test_normal_app_does_not_serve_or_select_supplemental_consumer():
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        page = client.get("/").text
        assert "data-sdsctl-supplemental" not in page
        assert 'src="assets/mimic-supplemental.js"' not in page
        assert client.get(HELPER).status_code == 404


@pytest.mark.parametrize("origin", ["https://ha.example.test", "http://192.0.2.10:8123"])
def test_actual_dashboard_bootstrap_selection_and_failure_isolation(origin):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for actual dashboard bootstrap qualification.")
    script = files("sds200.web_assets").joinpath("dashboard.js").read_text()
    start = script.index("function initializeMimicDisplay() {")
    end = script.index("\ninitializeMimicDisplay();", start)
    result = subprocess.run(
        [node, str(Path(__file__).with_name("supplemental_web_shell.cjs")), origin],
        input=script[start:end],
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
