from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

import pytest

from sds200 import browser_device_server as server
from sds200 import browser_device_server_setup as setup
from sds200 import cli
from sds200.browser_device_store import BrowserDeviceState, BrowserDeviceStore

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux server preparation")
NATIVE = "https://192.168.20.15:8443"
INGRESS = "https://ha.example.test"
UID = "a" * 32


def prepare(root, **changes):
    options = dict(native_origin=NATIVE, ingress_origin=INGRESS, admin_user_ids=(UID,))
    options.update(changes)
    return setup.prepare_browser_device_server(root, **options)


def snapshot(root):
    return {p.name: (p.lstat().st_mode, p.lstat().st_mtime_ns,
                    p.read_bytes() if stat.S_ISREG(p.lstat().st_mode) else None)
            for p in root.iterdir()}


@pytest.mark.parametrize("origin", [NATIVE, "https://scanner.example.test", "https://[fd12::15]:8443"])
@pytest.mark.parametrize("native_only", [True, False])
def test_preparation_and_read_only_check(tmp_path, origin, native_only):
    root = tmp_path / "server's private space"
    options = dict(native_origin=origin)
    if native_only:
        options.update(native_only=True, ingress_origin=None, admin_user_ids=())
    result = prepare(root, **options)
    assert result.authority_path == root / "authority.sqlite"
    assert result.native_origin == origin
    assert result.ingress_origin == (None if native_only else INGRESS)
    assert result.admin_user_ids == (frozenset() if native_only else frozenset({UID}))
    assert root.stat().st_mode & 0o777 == 0o700
    assert {p.name for p in root.iterdir()} == {"server.json", "authority.sqlite"}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    assert BrowserDeviceStore(result.authority_path).inventory() == ()
    before = snapshot(root)
    assert server.load_browser_device_server_configuration(root / "server.json") == result
    assert snapshot(root) == before
    with pytest.raises(server.BrowserDeviceServerError, match="already exists"):
        prepare(root, **options)
    assert snapshot(root) == before


@pytest.mark.parametrize("changes", [
    {"native_origin": "http://host"}, {"native_origin": "https://HOST"},
    {"native_origin": "https://host:443"}, {"native_origin": "https://host/"},
    {"native_origin": "https://host?secret=fictional-secret"},
    {"native_origin": "https://user:fictional-secret@host"}, {"native_origin": None},
    {"native_origin": "https://host" + "a" * 17000},
    {"ingress_origin": None}, {"ingress_origin": "http://ha.example.test"},
    {"ingress_origin": "https://ha.example.test/"}, {"ingress_origin": False},
    {"admin_user_ids": ()}, {"admin_user_ids": (UID, UID)},
    {"admin_user_ids": ("A" * 32,)}, {"admin_user_ids": (True,)},
    {"admin_user_ids": ("fictional-secret",)}, {"admin_user_ids": [UID]},
    {"admin_user_ids": tuple(f"{i:032x}" for i in range(33))},
    {"native_only": True}, {"native_only": 1},
    {"native_only": True, "ingress_origin": None},
    {"native_only": True, "admin_user_ids": ()},
])
def test_bad_inputs_do_not_write_or_echo(tmp_path, changes):
    root = tmp_path / "output"
    before = snapshot(tmp_path)
    with pytest.raises(server.BrowserDeviceServerError, match="nothing was created") as caught:
        prepare(root, **changes)
    assert snapshot(tmp_path) == before
    assert not root.exists()
    assert "fictional-secret" not in str(caught.value)
    assert UID not in str(caught.value)
    assert str(root) not in str(caught.value)


@pytest.mark.parametrize("kind", ["empty", "partial", "complete", "file", "fifo", "symlink",
                                   "dangling-symlink"])
def test_existing_destinations_preserved(tmp_path, kind):
    root = tmp_path / "output"
    saved = tmp_path / "saved"
    saved.mkdir(mode=0o700)
    if kind == "complete":
        prepare(root)
    elif kind in {"empty", "partial"}:
        root.mkdir(mode=0o700)
        if kind == "partial":
            (root / "authority.sqlite").touch(mode=0o600)
    elif kind == "file":
        root.write_text("retain")
    elif kind == "fifo":
        os.mkfifo(root, 0o600)
    else:
        root.symlink_to(saved if kind == "symlink" else tmp_path / "missing")
    before = snapshot(tmp_path)
    nested = snapshot(root) if root.is_dir() else None
    with pytest.raises(server.BrowserDeviceServerError):
        prepare(root)
    assert snapshot(tmp_path) == before
    if nested is not None:
        assert snapshot(root) == nested


@pytest.mark.parametrize("kind", ["relative", "not-path", "missing-parent", "public-parent",
                                   "symlink-parent", "wrong-owner", "not-linux"])
def test_unsafe_environment_does_not_write(tmp_path, monkeypatch, kind):
    root = tmp_path / "output"
    if kind == "relative":
        root = Path("relative-output")
    elif kind == "not-path":
        root = str(root)
    elif kind == "missing-parent":
        root = tmp_path / "missing" / "output"
    elif kind == "public-parent":
        tmp_path.chmod(0o755)
    elif kind == "symlink-parent":
        link = tmp_path / "linked"
        link.symlink_to(tmp_path)
        root = link / "output"
    elif kind == "wrong-owner":
        monkeypatch.setattr(os, "geteuid", lambda: tmp_path.stat().st_uid + 1)
    else:
        monkeypatch.setattr(sys, "platform", "darwin")
    before = snapshot(tmp_path)
    with pytest.raises(server.BrowserDeviceServerError):
        prepare(root)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("stage", ["parent-fsync", "initialize", "after-initialize",
                                    "write", "partial-write", "after-write", "directory-fsync"])
def test_interrupted_preparation_retains_evidence_and_refuses_retry(tmp_path, monkeypatch, stage):
    root = tmp_path / "output"
    initialize, write, fsync = BrowserDeviceStore.initialize, setup._write, os.fsync

    def fail(*args, **kwargs):
        raise OSError("fictional-secret")

    def after_initialize(path):
        initialize(path)
        fail()

    def failed_write(fd, name, body):
        if stage == "partial-write":
            write(fd, name, body[:8])
        elif stage == "after-write":
            write(fd, name, body)
        fail()

    calls = 0

    def failed_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == (1 if stage == "parent-fsync" else 4):
            fail()
        fsync(fd)

    with monkeypatch.context() as patch:
        if stage in {"parent-fsync", "directory-fsync"}:
            patch.setattr(os, "fsync", failed_fsync)
        elif stage in {"initialize", "after-initialize"}:
            patch.setattr(BrowserDeviceStore, "initialize",
                          fail if stage == "initialize" else after_initialize)
        else:
            patch.setattr(setup, "_write", failed_write)
        with pytest.raises(server.BrowserDeviceServerError, match="not be confirmed") as caught:
            prepare(root)
    assert "fictional-secret" not in str(caught.value)
    assert root.is_dir()
    before = snapshot(root)
    with pytest.raises(server.BrowserDeviceServerError, match="already exists"):
        prepare(root)
    assert snapshot(root) == before
    if stage in {"parent-fsync", "initialize", "after-initialize", "write", "partial-write"}:
        with pytest.raises(server.BrowserDeviceServerError):
            server.load_browser_device_server_configuration(root / "server.json")


def test_concurrent_creators_do_not_overwrite_or_clean_up_winner(tmp_path, monkeypatch):
    root = tmp_path / "output"
    barrier = Barrier(2)
    mkdir = os.mkdir

    def contested(path, *args, **kwargs):
        if path == root.name and "dir_fd" in kwargs:
            barrier.wait(timeout=5)
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", contested)

    def attempt():
        try:
            return prepare(root)
        except server.BrowserDeviceServerError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert server.load_browser_device_server_configuration(root / "server.json") in results
    assert {p.name for p in root.iterdir()} == {"authority.sqlite", "server.json"}


def test_check_is_offline_and_preserves_populated_authority(tmp_path, monkeypatch, capsys):
    root = tmp_path / "output"
    store = BrowserDeviceStore(prepare(root).authority_path)
    secrets = [store.enroll(state.value).credential for state in BrowserDeviceState]
    store.transition("paused", BrowserDeviceState.PAUSED)
    store.transition("revoked", BrowserDeviceState.REVOKED)
    before = snapshot(root)
    monkeypatch.setattr(cli, "_apply_cli_configuration", lambda *a, **kw: pytest.fail("defaults"))
    monkeypatch.setattr(cli, "configure_logging", lambda *a, **kw: pytest.fail("logging"))
    monkeypatch.setattr(cli, "run_web_dashboard_server", lambda *a, **kw: pytest.fail("server"))
    monkeypatch.setattr(BrowserDeviceStore, "initialize", lambda *a: pytest.fail("initialize"))
    monkeypatch.setattr(BrowserDeviceStore, "inventory", lambda *a: pytest.fail("transaction"))
    assert cli.main(["browser-device-server", "--experimental", "check",
                     "--server-config", str(root / "server.json")], environ={}) == 0
    assert snapshot(root) == before
    output = capsys.readouterr()
    assert output.err == ""
    assert "Offline server checks passed" in output.out
    assert "readiness and TLS trust are not confirmed" in output.out
    assert not any(value in output.out for value in [UID, str(root), *secrets])


@pytest.mark.parametrize("damage", ["missing-config", "missing-authority", "corrupt", "wal",
                                    "config-mode", "authority-mode", "config-symlink",
                                    "authority-hardlink", "schema", "verifier"])
def test_cli_failed_check_preserves_invalid_files(tmp_path, damage, capsys):
    root = tmp_path / "output"
    store = BrowserDeviceStore(prepare(root).authority_path)
    path = root / "server.json"
    if damage.startswith("missing-"):
        (path if damage == "missing-config" else store.path).rename(root / "saved")
    elif damage == "corrupt":
        store.path.write_bytes(b"fictional-secret")
    elif damage.endswith("-mode"):
        (path if damage == "config-mode" else store.path).chmod(0o644)
    elif damage == "config-symlink":
        path.rename(root / "saved")
        path.symlink_to(root / "saved")
    elif damage == "authority-hardlink":
        os.link(store.path, root / "linked")
    else:
        store.enroll("display")
        with closing(sqlite3.connect(store.path)) as connection, connection:
            connection.execute({
                "wal": "PRAGMA journal_mode=WAL", "schema": "PRAGMA user_version=2",
                "verifier": "UPDATE devices SET verifier='fictional-secret'",
            }[damage])
    before = snapshot(root)
    assert cli.main(["browser-device-server", "--experimental", "check",
                     "--server-config", str(path)], environ={}) == 78
    assert snapshot(root) == before
    output = capsys.readouterr()
    assert output.out == ""
    assert "fictional-secret" not in output.err and str(path) not in output.err


@pytest.mark.parametrize("native_only", [True, False])
def test_cli_create_and_check_private_config(tmp_path, monkeypatch, capsys, native_only):
    root = tmp_path / "output"
    monkeypatch.setattr(cli, "configure_logging", lambda *a, **kw: pytest.fail("logging"))
    admin_options = (["--native-only"] if native_only else
                     ["--ingress-origin", INGRESS, "--admin-user-id", UID,
                      "--admin-user-id", "b" * 32])
    assert cli.main(["browser-device-server", "--experimental", "create", "--directory", str(root),
                     "--native-origin", NATIVE, *admin_options], environ={}) == 0
    result = server.load_browser_device_server_configuration(root / "server.json")
    assert result.admin_user_ids == (frozenset() if native_only else frozenset({UID, "b" * 32}))
    assert "empty authority" in capsys.readouterr().out


@pytest.mark.parametrize("arguments", [
    [], ["--experimental"], ["create"], ["--experimental", "create"],
    ["--experimental", "check"],
    ["--experimental", "create", "--directory", "/fictional", "--native-origin", NATIVE],
    ["--experimental", "create", "--directory", "/fictional", "--native-origin", NATIVE,
     "--native-only", "--ingress-origin", INGRESS],
    ["--experimental", "check", "--server-config", "/fictional", "--native-only"],
])
def test_cli_requires_explicit_modes_and_options(arguments, monkeypatch):
    monkeypatch.setattr(setup, "prepare_browser_device_server",
                        lambda *a, **kw: pytest.fail("write"))
    with pytest.raises(SystemExit) as caught:
        cli.main(["browser-device-server", *arguments], environ={})
    assert caught.value.code == 2


def test_cli_rejects_native_only_with_admin_ids_before_writes(tmp_path, capsys):
    root = tmp_path / "output"
    assert cli.main(["browser-device-server", "--experimental", "create", "--directory", str(root),
                     "--native-origin", NATIVE, "--native-only", "--admin-user-id", UID]) == 78
    assert not root.exists()
    assert UID not in capsys.readouterr().err


def test_parser_is_pure_and_shared(tmp_path, monkeypatch):
    document = {"version": 1, "authority_path": str(tmp_path / "missing.sqlite"),
                "native_origin": NATIVE, "ingress_admin": None}
    monkeypatch.setattr(BrowserDeviceStore, "validate_existing", lambda *a: pytest.fail("read"))
    config = server.parse_browser_device_server_configuration(json.dumps(document).encode())
    assert config.authority_path == tmp_path / "missing.sqlite"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("body", [b"", b"null", b"[]", b"\xff", b"{" + b" " * 16384,
                                   b'{"version":1,"version":1}', b'{} fictional-secret'])
def test_parser_rejects_malformed_bounded_input(body):
    with pytest.raises(server.BrowserDeviceServerError):
        server.parse_browser_device_server_configuration(body)
