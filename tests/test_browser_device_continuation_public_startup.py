"""Public CLI with a complete real retained chain and synthetic recovery browser.

The historical reader, journals, manifest, SQL, ownership and installed CLI are
not mocked. The existing recovery fixture models native-pipe acknowledgement,
not Chromium rendering. This suite is not real-browser or human acceptance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sds200.browser_device_continuation_activation import BrowserPausedActivation
from sds200.browser_device_continuation_current import inspect_stopped_continuation
from sds200.browser_device_continuation_intent import ACTIVATION_MANIFEST, BrowserContinuationIntent
from sds200.browser_device_recovery import RecoveryMode
from sds200.browser_device_startup import BrowserStartupError, check_browser_startup
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_intent import released as released
from tests.test_browser_device_guard_release import completed as completed
from tests.test_browser_device_guard_release import state
from tests.test_browser_device_launch import staged as staged
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import CREDENTIAL, private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="Non-root Linux real retained-chain integration",
)


@pytest.fixture
def activated(lab, completed, released):
    recorded = BrowserContinuationIntent(completed, release_id=released.release_id).apply(
        confirmation=lambda review: review.confirmation
    )
    # A completed intent alone still cannot launch.
    before = state(lab, completed)
    with pytest.raises(BrowserStartupError):
        check_browser_startup(**lab.inputs)
    assert state(lab, completed) == before
    value = BrowserPausedActivation(
        completed, release_id=released.release_id, intent_id=recorded.intent_id
    ).apply(confirmation=lambda review: review.confirmation)
    assert value.mode is RecoveryMode.PAUSED
    return value


def args(lab, *mode):
    command = [
        str(Path(sys.executable).with_name("sdsctl")),
        "browser-device-start",
        "--experimental",
        "--directory",
        str(lab.inputs["root"]),
        *mode,
    ]
    for key in ("browser", "bundle", "profile", "public_key"):
        command.extend(["--" + key.replace("_", "-"), str(lab.inputs[key])])
    return command


def run_cli(lab, *mode):
    return subprocess.run(
        args(lab, *mode), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20
    )


def test_complete_history_allows_offline_cli_without_state_change(lab, completed, activated):
    before = state(lab, completed)
    run = run_cli(lab, "--check")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "valid offline" in run.stdout and not run.stderr
    assert state(lab, completed) == before
    current = inspect_stopped_continuation(
        lab.inputs["root"],
        **{name: lab.inputs[name] for name in ("bundle", "profile", "public_key")},
    )
    assert current.epoch == activated.epoch and current.mode is RecoveryMode.PAUSED


def test_complete_history_still_refuses_setup_cli(lab, completed, activated):
    before = state(lab, completed)
    run = run_cli(lab, "--setup")
    assert run.returncode == 78 and not run.stdout
    assert "no state was reset or locks removed" in run.stderr
    assert CREDENTIAL not in run.stderr
    assert state(lab, completed) == before


@pytest.mark.parametrize("damage", ["manifest", "receipt", "host", "intent-sidecar", "guard"])
def test_damaged_full_chain_cannot_fall_back_to_normal_cli(lab, completed, activated, damage):
    root = lab.inputs["root"]
    path = {
        "manifest": root / ACTIVATION_MANIFEST,
        "receipt": root / ".sdsctl-browser-registration.json",
        "host": root / "NativeMessagingHosts/org.sdsctl.browser_device.json",
        "intent-sidecar": root / ".sdsctl-browser-continuation-intent.sqlite-journal",
        "guard": root / ".sdsctl-browser-maintenance.json",
    }[damage]
    if damage == "intent-sidecar":
        path.symlink_to("preserve-uncertain-sidecar")
    else:
        path.rename(path.with_name(path.name + ".retained"))
    before = state(lab, completed)
    run = run_cli(lab, "--check")
    assert run.returncode == 78 and not run.stdout
    assert "nothing was changed" in run.stderr
    assert CREDENTIAL not in run.stderr
    assert state(lab, completed) == before


@pytest.mark.parametrize("child_exit", [0, 2])
def test_full_chain_foreground_cli_owns_real_child(lab, completed, activated, tmp_path, child_exit):
    browser = tmp_path / "fictional-public-browser"
    observed = tmp_path / "public-launch-observed.json"
    script = """import fcntl,json,os,sqlite3,sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("Chromium 152.0")
    raise SystemExit(0)
root = Path(ROOT)
assert sys.argv[-1].startswith("chrome-extension://")
assert sys.argv[-1].endswith("/startup.html")
assert "--user-data-dir=" + str(root) in sys.argv
with (root / ".sdsctl-device-launch.lock").open("r+") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pass
    else:
        raise AssertionError("Actual CLI lost launch ownership")
for path in (PROFILE, ARCHIVES):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise AssertionError("Actual CLI lost private-input ownership")
    finally:
        os.close(fd)
db = sqlite3.connect(str(Path(PROFILE) / "recovery.sqlite"), timeout=0)
try:
    db.execute("BEGIN IMMEDIATE")
    db.commit()
finally:
    db.close()
with Path(OBSERVED).open("x") as stream:
    json.dump({"launchOwned":True,"sqlAvailable":True,"syntheticBrowser":True}, stream)
raise SystemExit(CHILD_EXIT)
"""
    for key, value in {
        "ROOT": str(lab.inputs["root"]),
        "PROFILE": str(lab.args["profile"]),
        "ARCHIVES": str(lab.args["archives"]),
        "OBSERVED": str(observed),
        "CHILD_EXIT": child_exit,
    }.items():
        script = script.replace(key, repr(value))
    private(browser, ("#!" + sys.executable + "\n" + script).encode())
    browser.chmod(0o700)
    lab.inputs["browser"] = browser
    before = state(lab, completed)
    result = run_cli(lab)
    assert result.returncode == (0 if child_exit == 0 else 75), result.stdout + result.stderr
    assert not result.stdout and not result.stderr
    assert json.loads(observed.read_text()) == {
        "launchOwned": True,
        "sqlAvailable": True,
        "syntheticBrowser": True,
    }
    assert state(lab, completed) == before
