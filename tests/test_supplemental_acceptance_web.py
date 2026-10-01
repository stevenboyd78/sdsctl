"""Actual authenticated factory/CLI assembly, using only local fixtures."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200 import cli, web_dashboard
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_supplemental import CONTEXT_PATH, DEMAND_PATH, FRAME_PATH

from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import wait_for
from .test_daemon_supplemental_acquisition import rig as rig
from .test_scanner_display_supplemental_transport import ORIGIN, PASSWORD, headers, login
from .test_supplemental_demand_transport import api as api
from .test_supplemental_demand_transport import renewal_headers, unix
from .test_supplemental_demand_transport import service as service

SPEC = importlib.util.spec_from_file_location(
    "accept_supplemental_web",
    Path(__file__).resolve().parents[1] / "scripts/accept_supplemental_web.py",
)
assert SPEC is not None and SPEC.loader is not None
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)
PEER = web_dashboard.WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_CLIENT


@pytest.mark.parametrize("mode", ["ingress", "native"])
def test_real_adapter_auth_demand_and_native_owner(mode, rig, service, api, tmp_path):
    options = (
        {"home_assistant_ingress": True}
        if mode == "ingress"
        else {
            "lan_authentication": WebDashboardAuthentication(
                "operator-test-password", ORIGIN, display_password=PASSWORD
            )
        }
    )
    with unix(tmp_path, api) as factory:
        with adapter.installed() as selected:
            app = web_dashboard.create_web_dashboard_app(factory, **options)
            assert selected.used
        assert web_dashboard.create_web_dashboard_app is adapter._FACTORY
        with TestClient(app, base_url=ORIGIN) as untrusted:
            for path in ("/", "/assets/mimic-supplemental.js", CONTEXT_PATH):
                expected = 403 if mode == "ingress" else 302 if path == "/" else 401
                assert untrusted.get(path, follow_redirects=False).status_code == expected
            assert untrusted.post(DEMAND_PATH).status_code == 403
            assert untrusted.post(DEMAND_PATH, headers={"Origin": ORIGIN}).status_code == (
                403 if mode == "ingress" else 401
            )
        with TestClient(app, base_url=ORIGIN, client=(PEER, 1234)) as client:
            if mode == "native":
                login(client)
            page = client.get("/").text
            assert 'data-sdsctl-supplemental="demand"' in page
            assert client.get("/assets/mimic-supplemental.js").status_code == 200
            if mode == "native":
                assert 'data-access-mode="display"' in page
            context = service.context()["context"]
            assert client.get(FRAME_PATH, headers=headers(context)).status_code == 200
            assert rig.peer.reads == []  # Rendering/reading is not arming or demand.
            assert rig.owner.arm()
            if mode == "native":
                assert (
                    client.post(
                        DEMAND_PATH, headers={**renewal_headers(context), "Origin": "null"}
                    ).status_code
                    == 403
                )
                assert rig.peer.reads == []
            reply = client.post(DEMAND_PATH, headers=renewal_headers(context))
            assert reply.status_code == 200
            wait_for(lambda: rig.peer.reads == ["FQK"])
            if mode == "native":
                assert client.post("/api/v1/recordings/start").status_code == 403


@pytest.mark.parametrize("key", sorted(adapter._SELECTIONS))
@pytest.mark.parametrize("value", [False, True, None])
def test_existing_selection_refused_without_native_factory(monkeypatch, key, value):
    monkeypatch.setattr(adapter, "_FACTORY", lambda *a, **k: pytest.fail("Factory invoked"))
    selected = adapter.AcceptanceFactory()
    with pytest.raises(ValueError, match="already selected"):
        selected(lambda: None, home_assistant_ingress=True, **{key: value})
    with pytest.raises(RuntimeError, match="already used"):
        selected(lambda: None, home_assistant_ingress=True)


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"home_assistant_ingress": 1},
        {"home_assistant_ingress": None},
        {"home_assistant_ingress": True, "lan_authentication": object()},
    ],
)
def test_ambiguous_or_unauthenticated_factory_is_refused(options):
    with pytest.raises(ValueError):
        adapter.AcceptanceFactory()(lambda: None, **options)


def test_bad_native_authentication_retains_original_validation():
    with pytest.raises(TypeError, match="WebDashboardAuthentication"):
        adapter.AcceptanceFactory()(lambda: None, lan_authentication=object())


@pytest.mark.parametrize("failure", [ValueError, RuntimeError, KeyboardInterrupt])
def test_hooks_restore_after_failure_and_duplicate_invocation(failure):
    with pytest.raises(failure), adapter.installed():
        raise failure("fixed fixture error")
    assert web_dashboard.create_web_dashboard_app is adapter._FACTORY
    with adapter.installed() as selected:
        with pytest.raises(RuntimeError, match="already patched"), adapter.installed():
            pytest.fail("Nested hooks")
        selected(lambda: None, home_assistant_ingress=True)
        with pytest.raises(RuntimeError, match="already used"):
            selected(lambda: None, home_assistant_ingress=True)
    assert web_dashboard.create_web_dashboard_app is adapter._FACTORY


def test_prepatched_factory_is_preserved(monkeypatch):
    def other(*args, **kwargs):
        return None

    monkeypatch.setattr(web_dashboard, "create_web_dashboard_app", other)
    with pytest.raises(RuntimeError, match="already patched"), adapter.installed():
        pytest.fail("Unexpected installation")
    assert web_dashboard.create_web_dashboard_app is other


@pytest.mark.parametrize(
    "args",
    [
        ["daemon"],
        ["web"],
        ["web", "--container-exposure"],
        ["web", "--home-assistant-ingress", "--container-exposure"],
        ["web", "--home-assistant-ingress", "--authenticated-lan"],
        ["web", "--home-assistant-ingress", "--experimental-browser-devices"],
        ["web", "--home-assistant-ingress", "--browser-device-config", "/unused"],
    ],
)
def test_wrong_cli_actions_and_conflicts_fail_before_start(monkeypatch, args):
    monkeypatch.setattr(cli, "main", lambda *args: pytest.fail("CLI invoked"))
    with pytest.raises(ValueError):
        adapter.main(args)
    assert web_dashboard.create_web_dashboard_app is adapter._FACTORY


def test_actual_ingress_cli_uses_adapter_and_restores_it(monkeypatch, tmp_path):
    calls = []

    def server(app, **options):
        calls.append(options)
        with TestClient(app, client=(PEER, 1234)) as client:
            assert 'data-sdsctl-supplemental="demand"' in client.get("/").text
        return 0

    monkeypatch.setattr(cli, "run_web_dashboard_server", server)
    monkeypatch.setattr(cli, "DaemonApiClient", lambda *a, **k: pytest.fail("Daemon opened"))
    assert (
        adapter.main(
            [
                "web",
                "--home-assistant-ingress",
                "--daemon-socket-path",
                str(tmp_path / "s"),
            ]
        )
        == 0
    )
    assert len(calls) == 1 and calls[0]["home_assistant_ingress"] is True
    assert calls[0]["authenticated_lan"] is False
    assert web_dashboard.create_web_dashboard_app is adapter._FACTORY
    with TestClient(web_dashboard.create_web_dashboard_app(lambda: None)) as client:
        assert "data-sdsctl-supplemental" not in client.get("/").text


@pytest.mark.parametrize("mode", ["failure", "unused", "error_return"])
def test_cli_hook_cleanup_after_failure_or_no_construction(monkeypatch, mode):
    def run(arguments):
        assert web_dashboard.create_web_dashboard_app is not adapter._FACTORY
        if mode == "failure":
            raise RuntimeError("fixed test failure")
        return 2 if mode == "error_return" else 0

    monkeypatch.setattr(cli, "main", run)
    if mode == "error_return":
        assert adapter.main(["web", "--home-assistant-ingress"]) == 2
    else:
        with pytest.raises(RuntimeError):
            adapter.main(["web", "--home-assistant-ingress"])
    assert web_dashboard.create_web_dashboard_app is adapter._FACTORY
