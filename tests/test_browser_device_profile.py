from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier

import pytest

from sds200 import browser_device_profile as profile
from sds200 import cli
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, ExchangeFailure, RecoveryMode
from sds200.browser_device_store import BrowserDeviceStore
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_native import invoke
from tests.test_browser_device_native import root as root
from tests.test_browser_device_native import server as server

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="Non-root Linux browser profile",
)
EXTENSION_ID = "a" * 32
CREDENTIAL = "sdsctl-browser-v1." + "b" * 64


def private(path: Path, body: str | bytes) -> None:
    path.write_bytes(body.encode() if isinstance(body, str) else body)
    path.chmod(0o600)


def issuance() -> dict[str, object]:
    return {
        "version": 1,
        "device_id": "display",
        "generation": 1,
        "credential": CREDENTIAL,
        "outcome": {"status": "issued", "completed": False},
    }


@pytest.fixture
def incoming(tmp_path, certificates):
    private(tmp_path / "enrollment.json", json.dumps(issuance()))
    private(tmp_path / "ca.pem", certificates[0][0].read_bytes())
    return tmp_path


def create(incoming: Path, **changes):
    arguments = dict(
        enrollment_file=incoming / "enrollment.json",
        ca_file=incoming / "ca.pem",
        origin="https://localhost",
        device_id="display",
        extension_id=EXTENSION_ID,
    )
    arguments.update(changes)
    return profile.create_browser_profile(incoming / "native", **arguments)


def snapshot(root: Path):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir() if p.is_file()}


@pytest.mark.parametrize(
    "origin",
    [
        "https://localhost",
        "https://192.168.1.20:8443",
        "https://[fd00::20]:8443",
        "https://display.example",
    ],
)
def test_private_import_and_actual_native_status(incoming, origin):
    before = snapshot(incoming)
    result = create(incoming, origin=origin)
    root = incoming / "native"
    assert root.stat().st_mode & 0o777 == 0o700
    assert set(snapshot(root)) == {"device.secret", "ca.pem", "recovery.sqlite", "client.json"}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    assert snapshot(incoming) == before
    assert result.mode is RecoveryMode.ACTIVE and len(result.identity) == 64
    assert CREDENTIAL not in repr(result)
    document = json.loads((root / "client.json").read_text())
    assert document == {
        "version": 1,
        "origin": origin,
        "device_id": "display",
        "extension_origin": "chrome-extension://" + EXTENSION_ID + "/",
    }
    config = load_browser_native_configuration(root)
    assert config.identity == result.identity
    assert invoke(root, "status")["mode"] == "active"
    assert invoke(root, "suspend")["mode"] == "paused"
    assert profile.inspect_browser_profile(root).mode is RecoveryMode.PAUSED


@pytest.mark.parametrize(
    "mode",
    [
        None,
        RecoveryMode.PAUSED,
        RecoveryMode.TLS_ERROR,
        RecoveryMode.REJECTED,
        RecoveryMode.PROTOCOL_ERROR,
    ],
)
def test_check_is_read_only_and_create_never_resets_modes(incoming, mode):
    result = create(incoming)
    root = incoming / "native"
    recovery = BrowserDeviceRecovery(root / "recovery.sqlite", result.identity)
    if mode is RecoveryMode.PAUSED:
        recovery.suspend()
    elif mode is not None:

        def fail():
            raise ExchangeFailure(mode)

        recovery.authenticate(fail)
    before = snapshot(root)
    assert profile.inspect_browser_profile(root).mode is (mode or RecoveryMode.ACTIVE)
    assert snapshot(root) == before
    with pytest.raises(profile.BrowserProfileError, match="already exists"):
        create(incoming)
    assert snapshot(root) == before


def test_offline_check_preserves_clock_rollback_state(incoming, monkeypatch):
    result = create(incoming)
    root = incoming / "native"
    before = snapshot(root)
    monkeypatch.setattr(BrowserDeviceRecovery, "_now", lambda self: 0.0)
    checked = profile.inspect_browser_profile(root)
    assert checked.revision == result.revision
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"generation": True},
        {"generation": 2},
        {"generation": 0},
        {"device_id": "another"},
        {"credential": "operator-password"},
        {"credential": CREDENTIAL + "\n"},
        {"credential": "sdsctl-browser-session-v1." + "c" * 64},
        {"role": "operator"},
        {"outcome": {"status": "issued", "completed": 0}},
        {"outcome": {"status": "confirmed", "completed": True}},
        {"outcome": {}},
    ],
)
def test_invalid_and_rotation_handoffs_are_rejected_before_writes(incoming, change):
    document = {**issuance(), **change}
    private(incoming / "enrollment.json", json.dumps(document))
    before = snapshot(incoming)
    with pytest.raises(profile.BrowserProfileError) as error:
        create(incoming)
    assert not (incoming / "native").exists()
    assert snapshot(incoming) == before
    assert CREDENTIAL not in str(error.value)
    assert str(incoming) not in str(error.value)


@pytest.mark.parametrize(
    "body", [b"", b"{}", b'{"version":1,"version":1}', b"[", b"x" * 4097, b"\xff", b"[" * 2000]
)
def test_malformed_handoff_is_bounded_and_redacted(incoming, body):
    private(incoming / "enrollment.json", body)
    with pytest.raises(profile.BrowserProfileError):
        create(incoming)
    assert not (incoming / "native").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "http://localhost"),
        ("origin", "https://localhost/"),
        ("origin", "https://user:secret@localhost"),
        ("origin", "https://localhost?secret=1"),
        ("origin", "https://2130706433"),
        ("origin", "https://localhost:0"),
        ("extension_id", "b" * 31),
        ("extension_id", "q" * 32),
        ("device_id", "../escape"),
    ],
)
def test_bad_configuration_creates_nothing(incoming, field, value):
    with pytest.raises(profile.BrowserProfileError):
        create(incoming, **{field: value})
    assert not (incoming / "native").exists()


@pytest.mark.parametrize("name", ["enrollment.json", "ca.pem"])
@pytest.mark.parametrize("kind", ["mode", "symlink", "hardlink", "fifo", "missing", "directory"])
def test_unsafe_source_files_are_not_repaired(incoming, name, kind):
    source = incoming / name
    if kind == "mode":
        source.chmod(0o644)
    elif kind == "hardlink":
        os.link(source, incoming / "extra-link")
    else:
        source.rename(incoming / "saved")
        if kind == "symlink":
            source.symlink_to(incoming / "saved")
        elif kind == "fifo":
            os.mkfifo(source, 0o600)
        elif kind == "directory":
            source.mkdir(mode=0o700)
    with pytest.raises(profile.BrowserProfileError):
        create(incoming)
    assert not (incoming / "native").exists()
    if kind == "mode":
        assert source.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(
    "bad", [b"garbage", b"", b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"]
)
def test_malformed_trust_is_rejected(incoming, bad):
    private(incoming / "ca.pem", bad)
    with pytest.raises(profile.BrowserProfileError):
        create(incoming)
    assert not (incoming / "native").exists()


@pytest.mark.parametrize("fill", [b" ", b"A"])
def test_maximum_malformed_pem_does_not_backtrack(fill):
    prefix = b"-----BEGIN CERTIFICATE-----"
    body = prefix + fill * (128 * 1024 - len(prefix))
    # Bound the regression test in a separate process: malformed local input
    # must not wedge setup even when it meets the maximum input size.
    script = """
import sys
from sds200.browser_device_profile import _trust
try:
    _trust(sys.stdin.buffer.read())
except ValueError:
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=body,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == b""


def test_trust_bundle_rejects_private_keys_and_trailing_content(incoming, certificates):
    cert = (incoming / "ca.pem").read_bytes()
    for suffix in (certificates[0][1].read_bytes(), b"ignored text"):
        private(incoming / "ca.pem", cert + suffix)
        with pytest.raises(profile.BrowserProfileError):
            create(incoming)
    private(incoming / "ca.pem", cert + b"\n" + certificates[1][0].read_bytes())
    assert create(incoming).mode is RecoveryMode.ACTIVE


@pytest.mark.parametrize("file", ["client.json", "device.secret", "ca.pem", "recovery.sqlite"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "mode"])
def test_check_refuses_damaged_profile_without_repair(incoming, file, damage):
    create(incoming)
    root = incoming / "native"
    target = root / file
    if damage == "missing":
        target.rename(root / "retained")
    elif damage == "corrupt":
        private(target, b"malformed")
    else:
        target.chmod(0o644)
    before = snapshot(root)
    with pytest.raises(profile.BrowserProfileError):
        profile.inspect_browser_profile(root)
    assert snapshot(root) == before


@pytest.mark.parametrize("change", ["identity", "schema"])
def test_ledger_identity_or_schema_mismatch_is_not_reset(incoming, change):
    create(incoming)
    root = incoming / "native"
    with closing(sqlite3.connect(root / "recovery.sqlite")) as db:
        if change == "identity":
            db.execute("UPDATE recovery SET identity=?", ("0" * 64,))
        else:
            db.execute("PRAGMA user_version=99")
        db.commit()
    before = snapshot(root)
    with pytest.raises(profile.BrowserProfileError):
        profile.inspect_browser_profile(root)
    assert snapshot(root) == before


def test_failure_retains_partial_profile_without_publishing_config(incoming, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(CREDENTIAL)

    monkeypatch.setattr(BrowserDeviceRecovery, "initialize", fail)
    with pytest.raises(profile.BrowserProfileError) as error:
        create(incoming)
    root = incoming / "native"
    assert set(snapshot(root)) == {"device.secret", "ca.pem"}
    assert CREDENTIAL not in str(error.value)
    before = snapshot(root)
    with pytest.raises(profile.BrowserProfileError):
        create(incoming)
    assert snapshot(root) == before


def test_two_importers_cannot_overwrite_each_other(incoming, monkeypatch):
    barrier = Barrier(2)
    original = profile._enrollment

    def wait(*args):
        result = original(*args)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(profile, "_enrollment", wait)

    def run():
        try:
            return create(incoming)
        except profile.BrowserProfileError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert profile.inspect_browser_profile(incoming / "native").mode is RecoveryMode.ACTIVE


def test_real_issuer_attachment_is_compatible(incoming):
    from sds200.browser_device_admin import BrowserDeviceAdmin
    from sds200.browser_device_ingress import _attachment

    store = BrowserDeviceStore.initialize(incoming / "authority.sqlite")
    issued = BrowserDeviceAdmin(store).enroll("display")
    attachment = _attachment(
        issued.record, issued.credential, {"status": "issued", "completed": False}
    )
    private(incoming / "enrollment.json", bytes(attachment.body))
    assert create(incoming).mode is RecoveryMode.ACTIVE
    saved = (incoming / "native" / "device.secret").read_text().strip()
    assert store.authenticate("display", saved).generation == issued.record.generation


def test_cli_requires_opt_in_is_offline_and_redacts_secrets(incoming, capsys, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Offline setup attempted network access")

    import socket

    monkeypatch.setattr(socket, "create_connection", no_network)
    arguments = [
        "browser-device-profile",
        "--experimental",
        "create",
        "--directory",
        str(incoming / "native"),
        "--enrollment-file",
        str(incoming / "enrollment.json"),
        "--ca-file",
        str(incoming / "ca.pem"),
        "--origin",
        "https://localhost",
        "--device-id",
        "display",
        "--extension-id",
        EXTENSION_ID,
    ]
    assert cli.main(arguments) == 0
    assert cli.main(arguments) == 78
    assert (
        cli.main(
            [
                "browser-device-profile",
                "--experimental",
                "check",
                "--directory",
                str(incoming / "native"),
            ]
        )
        == 0
    )
    with pytest.raises(SystemExit) as exit:
        cli.main([value for value in arguments if value != "--experimental"])
    assert exit.value.code == 2
    output = capsys.readouterr()
    assert "does not prove server authentication" in output.out
    assert CREDENTIAL not in output.out + output.err
    assert str(incoming) not in output.out + output.err


@pytest.mark.parametrize("kind", ["relative", "mode", "symlink", "missing", "owner"])
def test_unsafe_destination_parent_is_not_repaired(incoming, monkeypatch, kind):
    root = incoming / "native"
    if kind == "relative":
        root = Path("native")
    elif kind == "mode":
        incoming.chmod(0o755)
    elif kind == "symlink":
        alias = incoming / "alias"
        alias.symlink_to(incoming, target_is_directory=True)
        root = alias / "native"
    elif kind == "missing":
        root = incoming / "missing" / "native"
    else:
        uid = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: uid + 1)
    with pytest.raises(profile.BrowserProfileError):
        profile.create_browser_profile(
            root,
            enrollment_file=incoming / "enrollment.json",
            ca_file=incoming / "ca.pem",
            origin="https://localhost",
            device_id="display",
            extension_id=EXTENSION_ID,
        )
    assert not (incoming / "native").exists()
    if kind == "mode":
        assert incoming.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_existing_unrelated_target_is_preserved(incoming, kind):
    target = incoming / "native"
    if kind == "file":
        private(target, b"personal file")
    elif kind == "directory":
        target.mkdir(mode=0o700)
        private(target / "personal-file", b"keep me")
    else:
        target.symlink_to(incoming / "enrollment.json")
    before = snapshot(incoming if kind != "directory" else target)
    with pytest.raises(profile.BrowserProfileError):
        create(incoming)
    assert snapshot(incoming if kind != "directory" else target) == before
    assert target.exists()


@pytest.mark.parametrize("platform", ["root", "darwin"])
def test_unsupported_platform_is_refused_without_writes(incoming, monkeypatch, platform):
    if platform == "root":
        monkeypatch.setattr(os, "geteuid", lambda: 0)
    else:
        monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(profile.BrowserProfileError):
        create(incoming)
    assert not (incoming / "native").exists()


def test_offline_check_refuses_wal_without_creating_auxiliary_files(incoming):
    create(incoming)
    root = incoming / "native"
    with closing(sqlite3.connect(root / "recovery.sqlite")) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    before = snapshot(root)
    assert not (root / "recovery.sqlite-wal").exists()
    assert not (root / "recovery.sqlite-shm").exists()
    with pytest.raises(profile.BrowserProfileError):
        profile.inspect_browser_profile(root)
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE recovery SET mode='unknown'",
        "UPDATE recovery SET revision=0",
        "UPDATE recovery SET failures=33",
        "UPDATE recovery SET next_at=observed_at+301",
        "DELETE FROM recovery",
    ],
)
def test_offline_check_preserves_invalid_ledger_state(incoming, statement):
    create(incoming)
    root = incoming / "native"
    with closing(sqlite3.connect(root / "recovery.sqlite")) as db:
        db.execute(statement)
        db.commit()
    before = snapshot(root)
    with pytest.raises(profile.BrowserProfileError):
        profile.inspect_browser_profile(root)
    assert snapshot(root) == before


def test_last_write_failure_does_not_reset_initialized_ledger(incoming, monkeypatch):
    original = profile._write

    def fail(fd, name, body):
        if name == "client.json":
            raise OSError(CREDENTIAL)
        original(fd, name, body)

    monkeypatch.setattr(profile, "_write", fail)
    with pytest.raises(profile.BrowserProfileError) as error:
        create(incoming)
    assert CREDENTIAL not in str(error.value)
    root = incoming / "native"
    before = snapshot(root)
    assert set(before) == {"device.secret", "ca.pem", "recovery.sqlite"}
    with pytest.raises(profile.BrowserProfileError):
        create(incoming)
    assert snapshot(root) == before


@pytest.mark.parametrize("server", [0, 1], indirect=True)
def test_imported_profile_drives_real_native_https_exchange(incoming, server, request):
    configuration, _, observed = server
    private(incoming / "ca.pem", (configuration.root / "ca.pem").read_bytes())
    create(incoming, origin=configuration.origin)
    imported = incoming / "native"
    reply = invoke(imported)
    if request.node.callspec.params["server"] == 0:
        assert reply["mode"] == "active"
        assert reply["session"]["token"].startswith("sdsctl-browser-session-v1.")
        assert len(observed) == 1 and observed[0][0] == "/auth/device/session"
        assert observed[0][1]["Authorization"] == "Bearer " + CREDENTIAL
        assert json.loads(observed[0][2]) == {"device_id": "display"}
        assert invoke(imported, "suspend")["mode"] == "paused"
        assert invoke(imported)["mode"] == "paused"
        assert profile.inspect_browser_profile(imported).mode is RecoveryMode.PAUSED
        assert len(observed) == 1
    else:
        assert reply["mode"] == "tls_error"
        assert profile.inspect_browser_profile(imported).mode is RecoveryMode.TLS_ERROR
        assert not observed
