"""Launch-scope contracts: real files/SQL/locks, explicitly simulated history.

Positive fixtures simulate the complete retained-chain reader. They do not
qualify a public CLI or replace full-chain browser tests. A separate negative
test restores the real reader to prove these abbreviated journals are refused.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from sds200 import browser_device_continuation_current as current
from sds200 import browser_device_continuation_startup as launch
from sds200 import browser_device_startup as startup
from sds200 import cli
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from sds200.browser_device_registration import BrowserRegistration
from sds200.browser_device_startup import _launch_lock
from tests.test_browser_device_bundle import profile as profile
from tests.test_browser_device_bundle import public_key as public_key
from tests.test_browser_device_continuation_current import (
    activation_candidate as activation_candidate,
)
from tests.test_browser_device_continuation_current import active_native, native_step
from tests.test_browser_device_continuation_current import candidate as candidate
from tests.test_browser_device_continuation_ownership import handoff as handoff
from tests.test_browser_device_native import certificates as certificates
from tests.test_browser_device_profile import private
from tests.test_browser_device_registration import source as source
from tests.test_browser_device_resume_workflow import lab as lab
from tests.test_browser_device_resume_workflow import snap
from tests.test_browser_device_startup import inputs as inputs

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0, reason="Non-root Linux owned launch fixtures"
)
REAL_CAPTURE = current.history._capture_owned_history


def test_scope_keeps_owner_but_closes_sql_before_launch(lab, candidate, monkeypatch):
    from sds200 import browser_device_native as native

    monkeypatch.setattr(native, "_post_browser_device", lambda *a, **k: pytest.fail("Network"))
    before = snap(lab)
    with launch._continuation_startup_scope(candidate.root, **candidate.args) as registered:
        assert type(registered) is BrowserRegistration
        assert registered.identity == candidate.history.identity
        assert len(registered.extension_id) == 32
        assert not any(hasattr(registered, name) for name in ("epoch", "generation", "permission"))
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("Competing browser")
        for name in ("profile", "archives"):
            with (
                pytest.raises(BrowserProfileAccessError),
                browser_profile_access(lab.args[name], exclusive=True),
            ):
                pytest.fail("Competing maintenance")
        # A peer native helper must be able to commit while the browser lives.
        with closing(sqlite3.connect(lab.ledger.path, timeout=0)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.commit()
        assert snap(lab) == before
    assert snap(lab) == before
    with _launch_lock(candidate.root, create=False):
        pass


def test_native_pause_commits_during_scope_without_cached_readpoint(lab, candidate):
    with launch._continuation_startup_scope(candidate.root, **candidate.args):
        changed = native_step(lab, candidate, "pause")
        assert changed.revision > candidate.activated.native_revision
    # Owner scope exit must not demand that the pre-launch SQL snapshot survived.
    with launch._continuation_startup_scope(candidate.root, **candidate.args):
        pass


def test_real_history_reader_refuses_abbreviated_fixture(lab, candidate, monkeypatch):
    monkeypatch.setattr(current.history, "_capture_owned_history", REAL_CAPTURE)
    before = snap(lab)
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        pytest.fail("Abbreviated test history became launch permission")
    assert snap(lab) == before


@pytest.mark.parametrize("name", ["manifest", "release", "intent", "ledger", "bundle"])
@pytest.mark.parametrize("mutation", ["missing", "symlink", "mode", "empty"])
def test_invalid_selection_never_yields_or_repairs(lab, candidate, name, mutation):
    path = candidate.paths[name]
    if mutation == "mode":
        path.chmod(0o644)
    elif mutation == "empty":
        path.write_bytes(b"")
    else:
        retained = path.with_name(path.name + ".retained")
        path.rename(retained)
        if mutation == "symlink":
            path.symlink_to(retained)
    before = snap(lab)
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        pytest.fail("Invalid launch selection")
    assert snap(lab) == before


@pytest.mark.parametrize("name", ["manifest", "release", "intent", "ledger"])
@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_sidecars_are_not_adopted(lab, candidate, name, suffix):
    path = candidate.paths[name]
    path.with_name(path.name + suffix).symlink_to("uncertain")
    before = snap(lab)
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        pytest.fail("Uncertain sidecar ignored")
    assert snap(lab) == before


@pytest.mark.parametrize("marker", ["SingletonLock", "SingletonSocket", "SingletonCookie"])
def test_live_or_stale_browser_is_not_adopted(lab, candidate, marker):
    path = candidate.root / marker
    path.symlink_to("retain-existing-browser")
    before = snap(lab)
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        pytest.fail("Existing browser adopted")
    assert path.is_symlink() and snap(lab) == before


def test_busy_lock_is_not_reinterpreted_as_caller_ownership(lab, candidate):
    before = snap(lab)
    with (
        _launch_lock(candidate.root, create=False),
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        pytest.fail("Busy lock bypassed")
    assert snap(lab) == before


def test_missing_existing_lock_is_never_recreated(lab, candidate):
    path = candidate.root / ".sdsctl-device-launch.lock"
    path.rename(path.with_name(path.name + ".retained"))
    before = snap(lab)
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        pytest.fail("Missing lock repaired")
    assert not path.exists() and snap(lab) == before


def test_failed_read_scope_exit_never_yields(lab, candidate, monkeypatch):
    original = current._CurrentRead.inspect
    count = 0

    def inspect(reader):
        nonlocal count
        count += 1
        if count == 6:  # Initial read, two observe pairs, then exit validation.
            raise current.BrowserContinuationCurrentError()
        return original(reader)

    monkeypatch.setattr(current._CurrentRead, "inspect", inspect)
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        pytest.fail("Failed read exit allowed launch")
    assert count == 6


def test_leftover_browser_marker_fails_exit_without_deletion(candidate):
    marker = candidate.root / "SingletonLock"
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        marker.symlink_to("retained-after-browser-exit")
    assert marker.is_symlink()


def test_changed_named_lock_is_detected_at_exit(candidate):
    path = candidate.root / ".sdsctl-device-launch.lock"
    with (
        pytest.raises(launch.BrowserContinuationStartupError),
        launch._continuation_startup_scope(candidate.root, **candidate.args),
    ):
        path.rename(path.with_name(path.name + ".retained"))
        private(path, b"")


@pytest.mark.parametrize("active", [False, True])
def test_offline_public_check_returns_only_registration(lab, candidate, active):
    if active:
        active_native(lab, candidate)
    before = snap(lab)
    registered = startup.check_browser_startup(**lab.inputs)
    assert type(registered) is BrowserRegistration
    assert snap(lab) == before


@pytest.mark.parametrize("phase", ["prepared", "claimed"])
def test_incomplete_approval_never_launches(lab, candidate, phase):
    native_step(lab, candidate, "prepare")
    if phase == "claimed":
        native_step(lab, candidate, "claim")
    before = snap(lab)
    with pytest.raises(startup.BrowserStartupError):
        startup.check_browser_startup(**lab.inputs)
    assert snap(lab) == before


@pytest.mark.parametrize("field", ["credential_hash", "trust_hash"])
def test_active_grant_must_match_private_inputs(lab, candidate, field):
    active_native(lab, candidate, **{field: "0" * 64})
    before = snap(lab)
    with pytest.raises(startup.BrowserStartupError):
        startup.check_browser_startup(**lab.inputs)
    assert snap(lab) == before


def test_public_setup_is_refused_without_browser_or_state_change(lab, candidate, monkeypatch):
    before = snap(lab)
    monkeypatch.setattr(startup.subprocess, "run", lambda *a, **k: pytest.fail("Probe"))
    monkeypatch.setattr(startup.subprocess, "Popen", lambda *a, **k: pytest.fail("Launch"))
    with pytest.raises(startup.BrowserStartupError):
        startup.run_browser_startup(**lab.inputs, setup=True)
    assert snap(lab) == before


def cli_args(lab):
    args = ["browser-device-start", "--experimental", "--directory", str(lab.inputs["root"])]
    for key in ("browser", "bundle", "profile", "public_key"):
        args.extend(["--" + key.replace("_", "-"), str(lab.inputs[key])])
    return args


def test_public_check_cli_is_offline_and_preserves_real_guard(lab, candidate, capsys):
    from sds200.browser_device_registration import (
        BrowserRegistrationError,
        inspect_browser_registration,
    )

    before = snap(lab)
    assert cli.main([*cli_args(lab), "--check"]) == 0
    assert "valid offline" in capsys.readouterr().out
    # No global guard removal: registration/maintenance/legacy native routes
    # retain their original strict policy.
    with pytest.raises(BrowserRegistrationError):
        inspect_browser_registration(candidate.root, **candidate.args)
    assert snap(lab) == before


@pytest.mark.parametrize("ending", ["close", "crash", "interrupt"])
def test_public_cli_holds_launch_owner_without_blocking_native_commit(
    lab, candidate, monkeypatch, ending
):
    original_run, original_popen = subprocess.run, subprocess.Popen
    launched, signals = [], []
    monkeypatch.setenv("DISPLAY", ":fictional")

    def run(command, **kwargs):
        if command == [str(lab.inputs["browser"]), "--version"]:
            return subprocess.CompletedProcess(command, 0, b"Chromium 152.0", b"")
        return original_run(command, **kwargs)

    class Browser:
        def wait(self, timeout=None):
            if ending == "interrupt" and not signals:
                raise KeyboardInterrupt
            return 75 if ending == "crash" else 0

        def send_signal(self, sig):
            signals.append(sig)

    def popen(command, **kwargs):
        if command[0] != str(lab.inputs["browser"]):
            return original_popen(command, **kwargs)
        launched.append(command)
        assert command[-1].endswith("/startup.html")
        with pytest.raises(BlockingIOError), _launch_lock(candidate.root, create=False):
            pytest.fail("CLI lost owner before launch")
        native_step(lab, candidate, "pause")
        return Browser()

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    assert cli.main(cli_args(lab)) == (75 if ending == "crash" else 0)
    assert len(launched) == 1
    assert bool(signals) == (ending == "interrupt")
    with _launch_lock(candidate.root, create=False):
        pass
