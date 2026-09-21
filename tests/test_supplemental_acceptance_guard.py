"""Independent deadlines and guardian death on disposable local subprocesses."""

import importlib.util
import os
import select
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from threading import Event, Timer
from unittest.mock import patch

import pytest

from .test_supplemental_acceptance_launcher import REVISION, launcher

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "guard_supplemental_acceptance", SCRIPTS / "guard_supplemental_acceptance.py"
)
guard = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"accept_supplemental_daemon": launcher}):
    SPEC.loader.exec_module(guard)


@pytest.fixture
def directory(tmp_path):
    result = tmp_path / "guard"
    result.mkdir(mode=0o700)
    return result


def result(directory):
    return launcher.read_private(directory / "guard-result.json")


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), 721])
def test_deadline_refused_before_process_or_evidence(directory, value):
    with pytest.raises(ValueError):
        guard.supervise([], directory, deadline_seconds=value, cancel=Event())
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("code", [0, 17])
def test_normal_or_failed_child_exit_never_claims_restoration(directory, code):
    rc = guard.supervise(
        [sys.executable, "-c", f"raise SystemExit({code})"],
        directory,
        deadline_seconds=2,
        cancel=Event(),
    )
    assert rc == (0 if code == 0 else 1)
    assert result(directory) == {
        "state": "ended",
        "outcome": "child_exited",
        "forced_kill": False,
        "child_returncode": code,
        "child_exit_confirmed": True,
        "restoration_verified": False,
    }
    with pytest.raises(FileExistsError):
        guard.supervise([], directory, deadline_seconds=2, cancel=Event())


@pytest.mark.parametrize("ignore_term", [False, True])
def test_hung_child_is_stopped_at_independent_deadline(directory, ignore_term):
    code = "import signal,time; "
    if ignore_term:
        code += "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    code += "time.sleep(30)"
    assert (
        guard.supervise(
            [sys.executable, "-c", code],
            directory,
            deadline_seconds=0.4,
            grace_seconds=0.1,
            cancel=Event(),
        )
        == 1
    )
    report = result(directory)
    assert report["outcome"] == "deadline_expired"
    assert report["forced_kill"] is ignore_term
    assert report["child_returncode"] == -(signal.SIGKILL if ignore_term else signal.SIGTERM)
    assert report["child_exit_confirmed"] and not report["restoration_verified"]


def test_external_cancellation_stops_child_without_waiting_for_window(directory):
    cancel = Event()
    timer = Timer(0.15, cancel.set)
    timer.start()
    try:
        assert (
            guard.supervise(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                directory,
                deadline_seconds=5,
                cancel=cancel,
            )
            == 1
        )
    finally:
        timer.cancel()
        timer.join()
    assert result(directory)["outcome"] == "cancelled"
    assert result(directory)["child_exit_confirmed"]


def test_child_exit_between_cancellation_and_cleanup_retains_exit_code(directory, monkeypatch):
    cancel = Event()

    class ExitingChild:
        pid = 999999
        returncode = None
        calls = 0

        def poll(self):
            self.calls += 1
            if self.calls > 1:
                self.returncode = 0
            return self.returncode

    def spawn(*_args, **_kwargs):
        cancel.set()
        return ExitingChild()

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(os, "pidfd_open", lambda _pid: os.open(os.devnull, os.O_RDONLY))
    assert guard.supervise(["synthetic"], directory, deadline_seconds=2, cancel=cancel) == 1
    assert result(directory)["outcome"] == "cancelled"
    assert result(directory)["child_exit_confirmed"]
    assert result(directory)["child_returncode"] == 0


def test_cancel_before_launch_sends_no_process(directory, monkeypatch):
    cancel = Event()
    cancel.set()
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("Spawned after cancel"))
    assert guard.supervise([], directory, deadline_seconds=1, cancel=cancel) == 1
    assert result(directory)["outcome"] == "cancelled_before_start"
    assert not result(directory)["child_exit_confirmed"]


def test_pidfd_open_failure_still_reaps_only_owned_child(directory, monkeypatch):
    def fail(_pid):
        raise OSError("synthetic pidfd failure")

    monkeypatch.setattr(os, "pidfd_open", fail)
    with pytest.raises(OSError, match="synthetic"):
        guard.supervise(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            directory,
            deadline_seconds=1,
            cancel=Event(),
        )
    assert result(directory)["child_exit_confirmed"]


def test_guardian_identity_refused_without_modifying_this_process(monkeypatch):
    monkeypatch.setattr(launcher.ctypes, "CDLL", lambda *_a, **_k: pytest.fail("prctl called"))
    with pytest.raises(ValueError, match="guardian is required"):
        launcher.require_guardian(os.getpid(), launcher.start_ticks(os.getpid()))


def test_guardian_death_kills_only_its_disposable_child():
    child_code = (
        "import os,sys,time; "
        f"sys.path.insert(0, {str(SCRIPTS)!r}); "
        "from accept_supplemental_daemon import require_guardian,start_ticks; "
        "parent=os.getppid(); require_guardian(parent,start_ticks(parent)); "
        "print(os.getpid(),flush=True); time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
        "time.sleep(30)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True
    )
    pidfd = None
    try:
        assert select.select([parent.stdout], [], [], 5)[0], "Child did not report readiness"
        child_pid = int(parent.stdout.readline())
        pidfd = os.pidfd_open(child_pid)
        parent.kill()
        parent.wait(timeout=2)
        assert select.select([pidfd], [], [], 2)[0], "Orphaned acceptance child survived guardian"
    finally:
        if pidfd is not None:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            os.close(pidfd)
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=2)
        parent.stdout.close()


def test_actual_guard_entrypoint_fails_closed_before_scanner_on_missing_profile(tmp_path):
    directory = tmp_path / "new-guard"
    command = [
        sys.executable,
        str(SCRIPTS / "guard_supplemental_acceptance.py"),
        "--guard-directory",
        str(directory),
        "--source-revision",
        REVISION,
        "--expected-firmware",
        "Version 1.26.01",
        "--ready-timeout",
        "1",
        "--window-seconds",
        "1",
        "--",
        "--host",
        "127.0.0.1",
        "daemon",
        "--scanner-display-profile-config",
        str(tmp_path / "does-not-exist.toml"),
    ]
    child = subprocess.run(command, capture_output=True, timeout=5)
    assert child.returncode != 0
    assert result(directory)["outcome"] == "child_exited"
    assert result(directory)["child_exit_confirmed"]
    previous = (directory / "guard-result.json").read_bytes()
    rerun = subprocess.run(command, capture_output=True, timeout=5)
    assert rerun.returncode != 0
    assert (directory / "guard-result.json").read_bytes() == previous
