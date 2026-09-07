from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

from sds200 import cli
from sds200.browser_device_http import BROWSER_DEVICE_COOKIE, BROWSER_DEVICE_EXCHANGE_PATH
from sds200.browser_device_ingress import BROWSER_ADMIN_PATH
from sds200.browser_device_server import (
    BrowserDeviceServerError,
    load_browser_device_server_configuration,
    parse_browser_device_server_configuration,
)
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux owner boundary")
NATIVE = "https://192.168.20.15:8443"
INGRESS = "https://ha.example.test"
UID = "a" * 32


@pytest.fixture
def server_files(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    store = BrowserDeviceStore.initialize(root / "authority.sqlite")
    path = root / "server.json"
    document = {"version": 1, "authority_path": str(store.path), "native_origin": NATIVE,
                "ingress_admin": {"origin": INGRESS, "user_ids": [UID]}}
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    return path, store, document


def snapshot(root):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in root.iterdir() if p.is_file()}


@pytest.mark.parametrize("origin", [NATIVE, "https://scanner.example.test", "https://[fd12::15]:8443"])
def test_load_is_read_only_and_preserves_all_device_states(server_files, origin):
    path, store, document = server_files
    store.enroll("active")
    store.enroll("paused")
    store.transition("paused", BrowserDeviceState.PAUSED)
    store.enroll("revoked")
    store.transition("revoked", BrowserDeviceState.REVOKED)
    document["native_origin"] = origin
    path.write_text(json.dumps(document))
    before = snapshot(path.parent)
    result = load_browser_device_server_configuration(path)
    assert result.authority_path == store.path
    assert result.native_origin == origin
    assert result.ingress_origin == INGRESS
    assert result.admin_user_ids == frozenset({UID})
    assert UID not in repr(result)
    assert snapshot(path.parent) == before
    result.require_native_origin(origin)
    result.require_ingress_admin()
    with pytest.raises(BrowserDeviceServerError, match="does not match"):
        result.require_native_origin("https://other.example.test")


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("version", "1"),
    ("authority_path", None), ("authority_path", "relative.sqlite"),
    ("native_origin", "http://192.168.20.15"), ("native_origin", "https://host/"),
    ("native_origin", "https://host:443"), ("native_origin", "https://USER:secret@host"),
    ("native_origin", "https://host/path"), ("native_origin", "https://host?x=1"),
    ("native_origin", "https://HOST"), ("native_origin", False),
    ("ingress_admin", {}), ("ingress_admin", []),
    ("ingress_admin", {"origin": INGRESS, "user_ids": []}),
    ("ingress_admin", {"origin": INGRESS, "user_ids": [UID, UID]}),
    ("ingress_admin", {"origin": INGRESS, "user_ids": [True]}),
    ("ingress_admin", {"origin": INGRESS, "user_ids": ["admin"]}),
    ("ingress_admin", {"origin": INGRESS, "user_ids": ["A" * 32]}),
    ("ingress_admin", {"origin": INGRESS, "user_ids": UID}),
    ("ingress_admin", {"origin": INGRESS, "user_ids": [f"{i:032x}" for i in range(33)]}),
    ("ingress_admin", {"origin": "http://ha.example.test", "user_ids": [UID]}),
    ("ingress_admin", {"origin": INGRESS, "user_ids": [UID], "secret": "fictional-secret"}),
    ("extra", "fictional-secret"),
])
def test_invalid_configuration_is_redacted_and_nonmutating(server_files, field, value):
    path, _, document = server_files
    document[field] = value
    path.write_text(json.dumps(document))
    before = snapshot(path.parent)
    with pytest.raises(BrowserDeviceServerError):
        parse_browser_device_server_configuration(path.read_bytes())
    with pytest.raises(BrowserDeviceServerError) as caught:
        load_browser_device_server_configuration(path)
    assert "fictional-secret" not in str(caught.value)
    assert str(path) not in str(caught.value)
    assert snapshot(path.parent) == before


@pytest.mark.parametrize("body", [b"", b"null", b"[]", b"\xff", b"{" + b" " * 16384,
                                   b'{"version":1,"version":1}', b'{} fictional-secret'])
def test_malformed_config_refused(server_files, body):
    path, *_ = server_files
    path.write_bytes(body)
    with pytest.raises(BrowserDeviceServerError):
        parse_browser_device_server_configuration(body)
    with pytest.raises(BrowserDeviceServerError):
        load_browser_device_server_configuration(path)


@pytest.mark.parametrize("kind", ["missing", "symlink", "hardlink", "fifo", "directory",
                                   "public", "public-parent", "symlink-parent"])
@pytest.mark.parametrize("target", ["config", "authority"])
def test_unsafe_or_missing_files_never_repaired(server_files, kind, target):
    path, store, document = server_files
    selected = path if target == "config" else store.path
    if kind == "public":
        selected.chmod(0o644)
    elif kind == "public-parent":
        selected.parent.chmod(0o755)
    elif kind == "symlink-parent":
        link = selected.parent.parent / "linked"
        link.symlink_to(selected.parent)
        if target == "config":
            path = link / path.name
        else:
            document["authority_path"] = str(link / store.path.name)
            path.write_text(json.dumps(document))
    else:
        saved = selected.with_suffix(".saved")
        selected.rename(saved)
        if kind == "symlink":
            selected.symlink_to(saved)
        elif kind == "hardlink":
            os.link(saved, selected)
        elif kind == "fifo":
            os.mkfifo(selected, 0o600)
        elif kind == "directory":
            selected.mkdir(mode=0o700)
    with pytest.raises(BrowserDeviceServerError):
        load_browser_device_server_configuration(path)
    if kind == "missing":
        assert not selected.exists()
    if kind == "public":
        assert selected.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("damage", ["empty", "garbage", "version", "column", "verifier",
                                    "trigger", "extra-table", "generation", "state", "too-many"])
def test_corrupt_authority_is_not_replaced(server_files, damage):
    path, store, _ = server_files
    store.enroll("display")
    if damage in {"empty", "garbage"}:
        store.path.write_bytes(b"" if damage == "empty" else b"fictional-secret")
    else:
        with sqlite3.connect(store.path) as connection:
            statements = {
                "version": "PRAGMA user_version=0",
                "column": "ALTER TABLE devices DROP COLUMN verifier",
                "verifier": "UPDATE devices SET verifier='fictional-secret'",
                "trigger": "CREATE TRIGGER hidden AFTER UPDATE ON devices BEGIN SELECT 1; END",
                "extra-table": "CREATE TABLE extra (value TEXT)",
                "generation": "UPDATE devices SET generation=0",
                "state": "UPDATE devices SET state='invalid'",
            }
            if damage == "too-many":
                connection.executemany("INSERT INTO devices VALUES (?,1,'active',?)",
                                       [(f"d{i}", "0" * 64) for i in range(256)])
            else:
                connection.execute(statements[damage])
        connection.close()
    before = snapshot(path.parent)
    with pytest.raises(BrowserDeviceServerError):
        load_browser_device_server_configuration(path)
    assert snapshot(path.parent) == before


def native_arguments(path):
    certificate = path.parent / "server.crt"
    key = path.parent / "server.key"
    certificate.write_text("synthetic certificate")
    key.write_text("synthetic private key")
    key.chmod(0o600)
    return ["web", "--experimental-browser-devices", "--browser-device-config", str(path),
            "--authenticated-lan", "--lan-origin", NATIVE,
            "--lan-listen-address", "192.168.20.15", "--listen-port", "8443",
            "--lan-password-env", "TEST_OPERATOR_PASSWORD", "--lan-tls-certfile", str(certificate),
            "--lan-tls-keyfile", str(key)]


def run_cli_app(monkeypatch, arguments):
    apps = []
    monkeypatch.setattr(cli, "run_web_dashboard_server", lambda app, **kw: apps.append(app) or 0)
    assert cli.main(arguments, environ={
        "TEST_OPERATOR_PASSWORD": "fictional operator password",
    }) == 0
    assert len(apps) == 1
    return apps[0]


def test_cli_wires_disjoint_admin_and_native_apps_with_shared_authority(server_files, monkeypatch):
    path, store, _ = server_files
    admin_app = run_cli_app(monkeypatch, ["web", "--home-assistant-ingress",
                            "--experimental-browser-devices", "--browser-device-config", str(path)])
    native_app = run_cli_app(monkeypatch, native_arguments(path))
    with TestClient(native_app, base_url=NATIVE) as native, TestClient(
        admin_app, client=("172.30.32.2", 12345), headers={"X-Remote-User-Id": UID},
    ) as admin:
        inventory = admin.get(BROWSER_ADMIN_PATH)
        assert inventory.status_code == 200
        token = re.search(r'name="csrf" value="([a-f0-9]{64})"', inventory.text).group(1)
        form = {"action": "enroll", "device_id": "display", "confirm": "display", "csrf": token}
        assert admin.post(BROWSER_ADMIN_PATH, data=form,
                          headers={"Origin": NATIVE}).status_code == 403
        issued = admin.post(BROWSER_ADMIN_PATH, data=form, headers={"Origin": INGRESS})
        assert issued.status_code == 200
        assert "attachment" in issued.headers["content-disposition"]
        credential = issued.json()["credential"]
        assert credential not in admin.get(BROWSER_ADMIN_PATH).text
        assert admin.post(BROWSER_DEVICE_EXCHANGE_PATH).status_code == 404
        response = native.post(BROWSER_DEVICE_EXCHANGE_PATH, json={"device_id": "display"},
                               headers={"Authorization": "Bearer " + credential})
        assert response.status_code == 200
        native.cookies.set(BROWSER_DEVICE_COOKIE, response.json()["token"])
        assert 'data-access-mode="display"' in native.get("/device-display").text
        assert native.get(BROWSER_ADMIN_PATH).status_code == 403
        assert native.get("/api/v1/recordings").status_code == 403
        assert native.post("/api/v1/control").status_code == 403
        token = re.search(r'name="csrf" value="([a-f0-9]{64})"',
                          admin.get(BROWSER_ADMIN_PATH).text).group(1)
        result = admin.post(BROWSER_ADMIN_PATH, headers={"Origin": INGRESS}, data={
            "action": "revoke", "device_id": "display", "confirm": "display", "csrf": token,
            "generation": "1", "state": "active",
        })
        assert result.status_code == 200
        assert "Outcome: confirmed" in result.text
        assert store.inventory()[0].state is BrowserDeviceState.REVOKED
        assert native.get("/device-display").status_code == 401
    # A fresh owner can start after ordered shutdown; the database is retained.
    replacement = run_cli_app(monkeypatch, native_arguments(path))
    with TestClient(replacement, base_url=NATIVE) as client:
        assert client.get("/device-display").status_code == 401


def test_cli_configured_admin_rejects_forged_peer(server_files, monkeypatch):
    path, *_ = server_files
    app = run_cli_app(monkeypatch, ["web", "--home-assistant-ingress",
                       "--experimental-browser-devices", "--browser-device-config", str(path)])
    with TestClient(app, client=("192.168.20.30", 12345)) as client:
        assert client.get(BROWSER_ADMIN_PATH, headers={"X-Remote-User-Id": UID,
                          "X-Forwarded-For": "172.30.32.2"}).status_code == 403


@pytest.mark.parametrize("arguments", [
    ["--experimental-browser-devices"], ["--browser-device-config", "/missing"],
    ["--experimental-browser-devices", "--browser-device-config", "/missing"],
    ["--container-exposure", "--experimental-browser-devices",
     "--browser-device-config", "/missing"],
    ["--home-assistant-ingress", "--experimental-browser-devices",
     "--browser-device-config", "/missing"],
])
def test_cli_invalid_options_never_start_a_server(monkeypatch, arguments):
    monkeypatch.setattr(cli, "run_web_dashboard_server", lambda *a, **kw: pytest.fail("started"))
    assert cli.main(["web", *arguments], environ={}) == 2


def test_cli_rejects_mismatched_native_origin(server_files, monkeypatch):
    path, _, document = server_files
    document["native_origin"] = "https://other.example.test:8443"
    path.write_text(json.dumps(document))
    monkeypatch.setattr(cli, "run_web_dashboard_server", lambda *a, **kw: pytest.fail("started"))
    assert cli.main(native_arguments(path), environ={
        "TEST_OPERATOR_PASSWORD": "fictional operator password",
    }) == 2


def test_native_only_config_does_not_authorize_ingress(server_files, monkeypatch):
    path, _, document = server_files
    document["ingress_admin"] = None
    path.write_text(json.dumps(document))
    config = load_browser_device_server_configuration(path)
    assert config.ingress_origin is None and config.admin_user_ids == frozenset()
    run_cli_app(monkeypatch, native_arguments(path))
    monkeypatch.setattr(cli, "run_web_dashboard_server", lambda *a, **kw: pytest.fail("started"))
    assert cli.main(["web", "--home-assistant-ingress", "--experimental-browser-devices",
                     "--browser-device-config", str(path)], environ={}) == 2


def test_wal_authority_is_refused_without_creating_sidecars(server_files):
    path, store, _ = server_files
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    before = snapshot(path.parent)
    assert not store.path.with_name(store.path.name + "-shm").exists()
    with pytest.raises(BrowserDeviceServerError):
        load_browser_device_server_configuration(path)
    assert snapshot(path.parent) == before


def test_cli_second_native_owner_refuses_startup(server_files, monkeypatch):
    path, *_ = server_files
    first = run_cli_app(monkeypatch, native_arguments(path))
    second = run_cli_app(monkeypatch, native_arguments(path))
    with TestClient(first, base_url=NATIVE), pytest.raises(RuntimeError), TestClient(second):
        pytest.fail("Duplicate owner must never serve")
