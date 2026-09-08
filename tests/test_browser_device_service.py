from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sds200 import browser_device_service as service
from sds200 import cli
from sds200.browser_device_native import load_browser_native_configuration
from sds200.browser_device_recovery import BrowserDeviceRecovery, ExchangeFailure, RecoveryMode
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux managed browser",
)


def prepare(tmp_path, inputs, name='service %n ${DO_NOT_EXPAND} "quoted"'):
    output = tmp_path / name
    service.create_browser_service(output, directory=inputs["root"], **{
        key: value for key, value in inputs.items() if key != "root"
    })
    return output


def set_mode(profile, mode):
    configuration = load_browser_native_configuration(profile)
    recovery = BrowserDeviceRecovery(profile / "recovery.sqlite", configuration.identity)
    if mode is RecoveryMode.PAUSED:
        recovery.suspend()
    elif mode is not RecoveryMode.ACTIVE:
        def fail():
            raise ExchangeFailure(mode)
        recovery.authenticate(fail)


@pytest.mark.parametrize("mode", list(RecoveryMode))
def test_preparation_and_check_preserve_all_native_modes(tmp_path, inputs, mode):
    set_mode(inputs["profile"], mode)
    before = [snapshot(inputs[key]) for key in ("root", "bundle", "profile")]
    output = prepare(tmp_path, inputs)
    inspected = service.inspect_browser_service(output)
    assert inspected["directory"] == str(inputs["root"])
    assert [snapshot(inputs[key]) for key in ("root", "bundle", "profile")] == before
    assert set(p.name for p in output.iterdir()) == {"launch.py", "service.json", service.UNIT}
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert CREDENTIAL.encode() not in path.read_bytes()
    before_output = snapshot(output)
    with pytest.raises(service.BrowserServiceError):
        prepare(tmp_path, inputs)
    assert snapshot(output) == before_output


def test_service_policy_quoting_and_real_systemd_parser(tmp_path, inputs):
    output = prepare(tmp_path, inputs)
    unit = (output / service.UNIT).read_text()
    assert 'ExecStart=:' in unit and ' -I ' in unit
    assert '%%n' in unit and '${DO_NOT_EXPAND}' in unit and '\\"quoted\\"' in unit
    for setting in ("Restart=on-failure", "RestartPreventExitStatus=2 78", "RestartSec=15s",
                    "StartLimitIntervalSec=300", "StartLimitBurst=3", "KillMode=mixed",
                    "Requisite=graphical-session.target", "PartOf=graphical-session.target"):
        assert setting in unit
    assert "--setup" not in unit and "--no-sandbox" not in unit
    assert "EnvironmentFile=" not in unit and "ExecStartPre=" not in unit
    analyze = shutil.which("systemd-analyze")
    if analyze is None:
        pytest.skip("systemd parser unavailable")
    runtime = tmp_path / "systemd-runtime"
    runtime.mkdir(mode=0o700)
    checked = subprocess.run([analyze, "--user", "verify", str(output / service.UNIT)],
                             capture_output=True, timeout=10,
                             env={**os.environ, "XDG_RUNTIME_DIR": str(runtime)})
    assert checked.returncode == 0, checked.stderr.decode()


@pytest.mark.parametrize("value", ["", "newline\nunit", "tab\tunit", "delete\x7f", "null\0"])
def test_unit_control_characters_rejected(value):
    with pytest.raises(ValueError):
        service._quote(value)


@pytest.mark.parametrize("change", [
    "launch", "unit", "json", "runtime", "unknown", "extra", "missing", "symlink",
    "hardlink", "fifo", "mode", "directory-mode", "guard",
])
def test_service_tampering_or_unsafe_files_fail_without_repair(tmp_path, inputs, change):
    output = prepare(tmp_path, inputs)
    target = output / "launch.py"
    if change in {"launch", "unit", "json"}:
        private(output / {"launch": "launch.py", "unit": service.UNIT,
                          "json": "service.json"}[change], CREDENTIAL)
    elif change in {"runtime", "unknown"}:
        settings = json.loads((output / "service.json").read_text())
        settings["python" if change == "runtime" else "unreviewed"] = CREDENTIAL
        private(output / "service.json", json.dumps(settings))
    elif change == "extra":
        private(output / "extra", b"retain")
    elif change == "missing":
        target.unlink()
    elif change == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "absent")
    elif change == "hardlink":
        os.link(target, tmp_path / "alias")
    elif change == "fifo":
        target.unlink()
        os.mkfifo(target, 0o600)
    elif change == "mode":
        target.chmod(0o644)
    elif change == "directory-mode":
        output.chmod(0o755)
    else:
        private(inputs["root"] / ".sdsctl-browser-maintenance.json", b"keep guard")
    with pytest.raises(service.BrowserServiceError) as caught:
        service.inspect_browser_service(output)
    assert CREDENTIAL not in str(caught.value)
    assert output.exists()


@pytest.mark.parametrize("exit_status", [0, 75, 78])
def test_service_entry_uses_normal_start_only_and_preserves_exit(tmp_path, inputs, monkeypatch,
                                                               exit_status):
    output = prepare(tmp_path, inputs)
    def run(root, **kwargs):
        assert root == inputs["root"]
        assert "setup" not in kwargs
        return exit_status
    monkeypatch.setattr(service, "run_browser_startup", run)
    assert service.service_main(output) == exit_status


def test_service_refusal_is_redacted(tmp_path, capsys):
    assert service.service_main(tmp_path / "absent") == 78
    assert "no profile was reset" in capsys.readouterr().err


def test_service_cli_opt_in_and_no_scanner_config_or_logging(tmp_path, inputs, monkeypatch, capsys):
    def forbidden(*a, **kw):
        pytest.fail("Offline lifecycle tools must not load configuration or logging")
    monkeypatch.setattr(cli, "_apply_cli_configuration", forbidden)
    monkeypatch.setattr(cli, "configure_logging", forbidden)
    output = tmp_path / "service"
    args = ["browser-device-service", "--experimental", "create", "--directory", str(output),
            "--browser-directory", str(inputs["root"])]
    for key in ("browser", "bundle", "profile", "public_key"):
        args.extend(["--" + key.replace("_", "-"), str(inputs[key])])
    with pytest.raises(SystemExit):
        cli.main([arg for arg in args if arg != "--experimental"])
    assert cli.main(args) == 0
    assert cli.main(["browser-device-service", "--experimental", "check", "--directory",
                     str(output)]) == 0
    assert "No service was installed" in capsys.readouterr().out
    assert cli.main(args) == 78


@pytest.mark.parametrize("destination", ["relative", "inside", "symlink"])
def test_service_unsafe_destination_not_written(tmp_path, inputs, destination):
    path = tmp_path / "alias"
    if destination == "relative":
        path = Path("relative")
    elif destination == "inside":
        path = inputs["profile"] / "new service"
    else:
        path.symlink_to(tmp_path / "missing")
    with pytest.raises(service.BrowserServiceError):
        service.create_browser_service(path, directory=inputs["root"], **{
            key: value for key, value in inputs.items() if key != "root"
        })
