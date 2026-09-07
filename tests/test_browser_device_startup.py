from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from sds200 import browser_device_startup as startup
from sds200 import cli
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private, snapshot
from tests.test_browser_device_registration import register
from tests.test_browser_device_registration import source as source

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux managed browser",
)


@pytest.fixture
def inputs(tmp_path, source, profile, public_key):
    register(tmp_path, source, profile, public_key)
    return dict(root=tmp_path / "dedicated browser", bundle=source,
                profile=profile, public_key=public_key, browser=Path(sys.executable))


def test_offline_check_preserves_state_and_does_not_probe_browser(inputs, monkeypatch):
    before = [snapshot(inputs[key]) for key in ("root", "bundle", "profile")]
    original = subprocess.run

    def run(command, **kwargs):
        assert command[0] != str(inputs["browser"])
        assert "openssl" in command[0]  # Canonical public-key validation only.
        return original(command, **kwargs)

    monkeypatch.setattr(startup.subprocess, "run", run)
    result = startup.check_browser_startup(**inputs)
    assert len(result.extension_id) == 32
    assert [snapshot(inputs[key]) for key in ("root", "bundle", "profile")] == before
    assert not (inputs["root"] / ".sdsctl-device-launch.lock").exists()


@pytest.mark.parametrize("setup", [False, True])
def test_fixed_command_and_selected_entry(inputs, setup):
    result = startup.check_browser_startup(**inputs)
    command = startup.browser_startup_command(
        inputs["root"], browser=inputs["browser"], bundle=inputs["bundle"],
        registration=result, setup=setup,
    )
    assert command[-1] == (f"chrome-extension://{result.extension_id}/"
                           + ("setup.html" if setup else "startup.html"))
    assert command.count("--kiosk") == 1
    assert f"--user-data-dir={inputs['root']}" in command
    assert f"--load-extension={inputs['bundle'] / 'extension'}" in command
    assert not any(word in " ".join(command) for word in (
        "no-sandbox", "ignore-certificate", "password", CREDENTIAL, "auth/login",
    ))


def test_check_cli_requires_opt_in_and_redacts(inputs, capsys):
    args = ["browser-device-start", "--check", "--directory", str(inputs["root"])]
    for key in ("browser", "bundle", "profile", "public_key"):
        args.extend(["--" + key.replace("_", "-"), str(inputs[key])])
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert cli.main([*args, "--experimental"]) == 0
    assert "valid offline" in capsys.readouterr().out
    private(inputs["root"] / ".sdsctl-browser-registration.json", CREDENTIAL)
    assert cli.main([*args, "--experimental"]) == 78
    assert CREDENTIAL not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main([*args, "--experimental", "--setup"])


@pytest.mark.parametrize("kind", ["relative", "missing", "not-executable", "comma"])
def test_unsafe_browser_arguments_fail_offline(inputs, tmp_path, kind):
    if kind == "relative":
        inputs["browser"] = Path("chromium")
    elif kind == "missing":
        inputs["browser"] = tmp_path / "missing"
    elif kind == "not-executable":
        private(tmp_path / "browser", "fixture")
        inputs["browser"] = tmp_path / "browser"
    else:
        inputs["bundle"] = tmp_path / "comma,extension"
    with pytest.raises(startup.BrowserStartupError, match="nothing was changed"):
        startup.check_browser_startup(**inputs)


def test_duplicate_launch_and_stale_chromium_markers_are_never_removed(inputs):
    root = inputs["root"]
    with startup._launch_lock(root), pytest.raises(BlockingIOError), startup._launch_lock(root):
        pytest.fail("Second owner")
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = root / name
        path.symlink_to("fictional-stale-owner")
        with pytest.raises(ValueError), startup._launch_lock(root):
            pytest.fail("Took over a Chromium profile")
        assert path.is_symlink()
        path.unlink()


@pytest.mark.parametrize("kind", ["mode", "symlink", "hardlink", "fifo", "directory"])
def test_unsafe_launcher_lock_fails_without_replacing_it(inputs, tmp_path, kind):
    path = inputs["root"] / ".sdsctl-device-launch.lock"
    if kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    else:
        private(path, b"keep")
        if kind == "mode":
            path.chmod(0o644)
        else:
            os.link(path, tmp_path / "alias")
    inode = path.lstat().st_ino
    with pytest.raises((OSError, ValueError)), startup._launch_lock(inputs["root"]):
        pytest.fail("Unsafe lock accepted")
    assert path.lstat().st_ino == inode


@pytest.mark.parametrize("ending", ["close", "crash", "interrupt", "kill", "sigterm", "exception"])
@pytest.mark.parametrize("setup", [False, True])
def test_foreground_child_lifecycle(inputs, monkeypatch, ending, setup):
    registered = startup.check_browser_startup(**inputs)
    monkeypatch.setattr(startup, "check_browser_startup", lambda *a, **kw: registered)
    monkeypatch.setenv("DISPLAY", ":fixture")
    monkeypatch.setattr(startup.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, b"Chromium 152.0.1", b""))
    previous = signal.getsignal(signal.SIGTERM)

    class Process:
        def __init__(self, command, **kwargs):
            assert command[-1].endswith("/setup.html" if setup else "/startup.html")
            assert kwargs == dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
            self.stopped = self.killed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout=None):
            if ending == "exception" and not self.stopped:
                raise OSError(CREDENTIAL)
            if ending == "sigterm" and not self.stopped:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            if ending in {"interrupt", "kill"} and not self.stopped:
                raise KeyboardInterrupt
            if ending == "kill" and not self.killed:
                assert timeout == 10
                raise subprocess.TimeoutExpired("fictional", 10)
            return 1 if ending == "crash" else 0

        def terminate(self):
            self.stopped = True

        def kill(self):
            assert self.stopped
            self.killed = True

    monkeypatch.setattr(startup.subprocess, "Popen", Process)
    if ending == "exception":
        with pytest.raises(startup.BrowserStartupError) as error:
            startup.run_browser_startup(**inputs, setup=setup)
        assert CREDENTIAL not in str(error.value)
    else:
        expected = 75 if ending == "crash" else 0
        assert startup.run_browser_startup(**inputs, setup=setup) == expected
    assert signal.getsignal(signal.SIGTERM) == previous
    with startup._launch_lock(inputs["root"]):
        pass


@pytest.mark.parametrize("failure", ["chrome", "old", "error", "timeout", "no-display"])
def test_failed_browser_qualification_never_launches(inputs, monkeypatch, failure):
    registered = startup.check_browser_startup(**inputs)
    monkeypatch.setattr(startup, "check_browser_startup", lambda *a, **kw: registered)
    monkeypatch.setenv("DISPLAY", ":fixture")
    if failure == "no-display":
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def version(*args, **kwargs):
        assert kwargs["timeout"] == 5
        if failure == "timeout":
            raise subprocess.TimeoutExpired(CREDENTIAL, 5)
        return subprocess.CompletedProcess(args, 1 if failure == "error" else 0,
                                           b"Chromium 119.0" if failure == "old"
                                           else b"Google Chrome 152.0", CREDENTIAL.encode())

    monkeypatch.setattr(startup.subprocess, "run", version)
    monkeypatch.setattr(startup.subprocess, "Popen", lambda *a, **kw: pytest.fail("Launched"))
    with pytest.raises(startup.BrowserStartupError) as error:
        startup.run_browser_startup(**inputs)
    assert CREDENTIAL not in str(error.value)
