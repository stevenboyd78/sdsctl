from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sds200 import browser_device_maintenance as maintenance
from sds200 import cli
from sds200.browser_device_bundle import create_browser_bundle
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_registration import (
    MAINTENANCE_MARKER,
    BrowserRegistrationError,
    inspect_browser_registration,
)
from sds200.browser_device_startup import _launch_lock
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_service import set_mode
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux managed browser",
)


@pytest.fixture
def arguments(tmp_path, inputs):
    replacement = tmp_path / "next bundle"
    create_browser_bundle(replacement, profile=inputs["profile"], public_key=inputs["public_key"])
    return dict(root=inputs["root"], bundle=inputs["bundle"], profile=inputs["profile"],
                public_key=inputs["public_key"], previous_python=Path(sys.executable),
                backup=tmp_path / "private maintenance backup", replacement_bundle=replacement)


@pytest.mark.parametrize("mode", list(RecoveryMode))
@pytest.mark.parametrize("retire", [False, True])
def test_update_or_retire_preserves_state_and_retains_exact_backup(arguments, mode, retire):
    set_mode(arguments["profile"], mode)
    root = arguments["root"]
    # Synthetic opaque Chromium storage: maintenance must never parse/edit it.
    private(root / "opaque-browser-state", b"paused browser state stays here")
    saved = {key: snapshot(arguments[key]) for key in ("profile", "bundle", "replacement_bundle")}
    old_receipt = (root / maintenance.RECEIPT).read_bytes()
    old_host = (root / "NativeMessagingHosts" / maintenance.HOST).read_bytes()
    call_args = {**arguments, "replacement_bundle": None} if retire else arguments
    result = maintenance.maintain_browser_registration(**call_args)
    assert len(result.identity) == 64
    assert {key: snapshot(arguments[key]) for key in saved} == saved
    assert (root / "opaque-browser-state").read_bytes() == b"paused browser state stays here"
    backup = arguments["backup"]
    assert (backup / "registration.before.json").read_bytes() == old_receipt
    assert (backup / "native-host.before.json").read_bytes() == old_host
    assert (backup / "completed.json").read_bytes() == (backup / "operation.json").read_bytes()
    assert backup.stat().st_mode & 0o777 == 0o700
    for path in backup.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert CREDENTIAL.encode() not in path.read_bytes()
    if retire:
        assert (root / MAINTENANCE_MARKER).is_file()
        assert not (root / maintenance.RECEIPT).exists()
        assert not (root / "NativeMessagingHosts" / maintenance.HOST).exists()
    else:
        assert not (root / MAINTENANCE_MARKER).exists()
        assert inspect_browser_registration(
            root, bundle=arguments["replacement_bundle"], profile=arguments["profile"],
            public_key=arguments["public_key"],
        ) == result
    with pytest.raises(BrowserRegistrationError):
        inspect_browser_registration(root, bundle=arguments["bundle"],
                                     profile=arguments["profile"],
                                     public_key=arguments["public_key"])
    # Re-running cannot implicitly restore or reinitialize anything.
    after = snapshot(root), snapshot(backup), snapshot(arguments["profile"])
    with pytest.raises(maintenance.BrowserMaintenanceError):
        maintenance.maintain_browser_registration(**call_args)
    assert (snapshot(root), snapshot(backup), snapshot(arguments["profile"])) == after


@pytest.mark.parametrize("change", [
    "same-bundle", "edited-new-bundle", "edited-old-bundle", "old-runtime-missing",
    "existing-backup", "nested-backup", "symlink-backup", "bad-backup-parent", "guard",
    "singleton", "receipt-mode", "receipt-link", "replacement-symlink",
])
def test_unsafe_maintenance_inputs_never_switch_registration(arguments, tmp_path, change):
    root = arguments["root"]
    if change == "same-bundle":
        arguments["replacement_bundle"] = arguments["bundle"]
    elif change in {"edited-new-bundle", "edited-old-bundle"}:
        target = "replacement_bundle" if change == "edited-new-bundle" else "bundle"
        private(arguments[target] / "extension" / "worker.mjs", CREDENTIAL)
    elif change == "old-runtime-missing":
        arguments["previous_python"] = tmp_path / "missing python"
    elif change == "existing-backup":
        arguments["backup"].mkdir(mode=0o700)
        private(arguments["backup"] / "keep", CREDENTIAL)
    elif change == "nested-backup":
        arguments["backup"] = arguments["profile"] / "backup"
    elif change == "symlink-backup":
        arguments["backup"].symlink_to(tmp_path / "absent")
    elif change == "bad-backup-parent":
        parent = tmp_path / "public"
        parent.mkdir(mode=0o755)
        arguments["backup"] = parent / "backup"
    elif change == "guard":
        private(root / MAINTENANCE_MARKER, b"retain interrupted work")
    elif change == "singleton":
        (root / "SingletonLock").symlink_to("stale-marker-stays")
    elif change == "receipt-mode":
        (root / maintenance.RECEIPT).chmod(0o644)
    elif change == "receipt-link":
        os.link(root / maintenance.RECEIPT, tmp_path / "alias")
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(arguments["replacement_bundle"], target_is_directory=True)
        arguments["replacement_bundle"] = alias
    before = ((root / maintenance.RECEIPT).read_bytes(), snapshot(arguments["profile"]))
    with pytest.raises(maintenance.BrowserMaintenanceError) as caught:
        maintenance.maintain_browser_registration(**arguments)
    assert CREDENTIAL not in str(caught.value)
    assert ((root / maintenance.RECEIPT).read_bytes(), snapshot(arguments["profile"])) == before


def test_competing_launcher_prevents_maintenance(arguments):
    with _launch_lock(arguments["root"]), pytest.raises(maintenance.BrowserMaintenanceError):
        maintenance.maintain_browser_registration(**arguments)
    assert not arguments["backup"].exists()


@pytest.mark.parametrize("failure", ["reject", "timeout", "io"])
def test_old_runtime_check_is_bounded_isolated_redacted(arguments, monkeypatch, failure):
    def run(command, **kwargs):
        assert command[0] == str(arguments["previous_python"])
        assert command[1:3] == ["-I", "-c"]
        assert kwargs["timeout"] == 20
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["env"] == {"PATH": os.defpath, "LC_ALL": "C"}
        if failure == "timeout":
            raise subprocess.TimeoutExpired(CREDENTIAL, 20)
        if failure == "io":
            raise OSError(CREDENTIAL)
        return subprocess.CompletedProcess(command, 78)
    monkeypatch.setattr(maintenance.subprocess, "run", run)
    with pytest.raises(maintenance.BrowserMaintenanceError) as caught:
        maintenance.maintain_browser_registration(**arguments)
    assert CREDENTIAL not in str(caught.value)
    assert not arguments["backup"].exists()


@pytest.mark.parametrize("fail_at", [maintenance.HOST, maintenance.RECEIPT, "completed.json"])
def test_interrupted_commit_keeps_guard_and_does_not_restore_receipt(arguments, monkeypatch,
                                                                   fail_at):
    write = maintenance._write
    def fail(directory, name, body):
        if name == fail_at:
            raise OSError("simulated full filesystem")
        write(directory, name, body)
    monkeypatch.setattr(maintenance, "_write", fail)
    before = snapshot(arguments["profile"])
    with pytest.raises(maintenance.BrowserMaintenanceError):
        maintenance.maintain_browser_registration(**arguments)
    assert (arguments["root"] / MAINTENANCE_MARKER).is_file()
    assert (arguments["backup"] / "registration.before.json").is_file()
    assert snapshot(arguments["profile"]) == before
    for bundle_key in ("bundle", "replacement_bundle"):
        with pytest.raises(BrowserRegistrationError):
            inspect_browser_registration(arguments["root"], bundle=arguments[bundle_key],
                                         profile=arguments["profile"],
                                         public_key=arguments["public_key"])
    assert not (arguments["backup"] / "completed.json").exists()


def test_backup_failure_leaves_registration_usable(arguments, monkeypatch):
    monkeypatch.setattr(maintenance, "_write", lambda *a: (_ for _ in ()).throw(OSError()))
    with pytest.raises(maintenance.BrowserMaintenanceError):
        maintenance.maintain_browser_registration(**arguments)
    assert not (arguments["root"] / MAINTENANCE_MARKER).exists()
    inspect_browser_registration(arguments["root"], bundle=arguments["bundle"],
                                 profile=arguments["profile"], public_key=arguments["public_key"])


@pytest.mark.parametrize("fail_at", [maintenance.RECEIPT, maintenance.HOST])
def test_unlink_failure_never_restores_old_files(arguments, monkeypatch, fail_at):
    unlink = maintenance.os.unlink
    def fail(name, **kwargs):
        if name == fail_at:
            raise OSError("simulated unlink failure")
        return unlink(name, **kwargs)
    before = snapshot(arguments["profile"])
    monkeypatch.setattr(maintenance.os, "unlink", fail)
    with pytest.raises(maintenance.BrowserMaintenanceError):
        maintenance.maintain_browser_registration(**arguments)
    assert snapshot(arguments["profile"]) == before
    if fail_at == maintenance.RECEIPT:
        # Commit never began, so the unchanged old registration is still usable.
        assert not (arguments["root"] / MAINTENANCE_MARKER).exists()
        inspect_browser_registration(arguments["root"], bundle=arguments["bundle"],
                                     profile=arguments["profile"],
                                     public_key=arguments["public_key"])
    else:
        assert not (arguments["root"] / maintenance.RECEIPT).exists()
        assert (arguments["root"] / MAINTENANCE_MARKER).exists()


def test_guard_write_failure_leaves_receipt_absent_even_for_legacy_runtime(arguments, monkeypatch):
    write = maintenance._write
    def fail(directory, name, body):
        if name == MAINTENANCE_MARKER:
            raise OSError("simulated guard write failure")
        write(directory, name, body)
    monkeypatch.setattr(maintenance, "_write", fail)
    with pytest.raises(maintenance.BrowserMaintenanceError):
        maintenance.maintain_browser_registration(**arguments)
    assert not (arguments["root"] / maintenance.RECEIPT).exists()
    assert (arguments["backup"] / "registration.before.json").exists()


def test_cli_requires_confirmation_and_never_loads_scanner_configuration(arguments, monkeypatch,
                                                                       capsys):
    args = ["browser-device-maintenance", "--experimental", "update"]
    for key, value in arguments.items():
        args.extend(["--directory" if key == "root" else "--" + key.replace("_", "-"), str(value)])
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    monkeypatch.setattr(cli, "_apply_cli_configuration",
                        lambda *a, **k: pytest.fail("configuration"))
    monkeypatch.setattr(cli, "configure_logging", lambda *a, **k: pytest.fail("logging"))
    assert cli.main([*args, "--confirm-stopped-maintenance"]) == 0
    assert "credentials and trust preserved" in capsys.readouterr().out
