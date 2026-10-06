from __future__ import annotations

import importlib.util
import json
import os
import signal
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200 import cli
from sds200.daemon_runtime import DaemonRuntime
from sds200.daemon_system_status_research import (
    SystemStatusResearchResult,
    SystemStatusResearchStatus,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "research_launcher", ROOT / "scripts/research_system_status_daemon.py"
)
assert SPEC is not None and SPEC.loader is not None
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class Runtime:
    def __init__(self) -> None:
        self.calls = 0

    def run_system_status_research(self, *, operator_ready: bool, timeout: float):
        assert operator_ready is True and timeout == 6.0
        self.calls += 1
        return SystemStatusResearchResult(
            SystemStatusResearchStatus.START_UNCONFIRMED, True, False, False, False, 0.01, "timeout"
        )


def test_no_signal_expires_without_research(tmp_path: Path) -> None:
    runtime = Runtime()
    trigger = launcher.OperatorTrigger(tmp_path / "evidence", ready_timeout=0.01)
    trigger.start(runtime)
    trigger.join()
    assert runtime.calls == 0
    ready = json.loads((trigger.directory / "ready.json").read_text())
    assert ready["pid"] == os.getpid()
    assert ready["process_start_ticks"].isdigit()
    assert ready["research_started"] is False
    assert (
        json.loads((trigger.directory / "result.json").read_text())["status"]
        == "operator_wait_expired"
    )
    assert stat.S_IMODE(trigger.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((trigger.directory / "ready.json").stat().st_mode) == 0o600


def test_signals_before_arming_or_after_attempt_do_not_start_research(tmp_path: Path) -> None:
    runtime = Runtime()
    trigger = launcher.OperatorTrigger(tmp_path / "evidence", ready_timeout=0.05)
    trigger.signal(signal.SIGUSR1, None)
    trigger.start(runtime)
    assert trigger._armed.wait(0.5)
    assert runtime.calls == 0
    trigger.signal(signal.SIGUSR1, None)
    trigger.signal(signal.SIGUSR1, None)
    trigger.join()
    trigger.signal(signal.SIGUSR1, None)
    assert runtime.calls == 1
    assert (
        json.loads((trigger.directory / "result.json").read_text())["status"] == "start_unconfirmed"
    )


def test_shutdown_cancels_the_operator_wait(tmp_path: Path) -> None:
    runtime = Runtime()
    trigger = launcher.OperatorTrigger(tmp_path / "evidence", ready_timeout=10)
    trigger.start(runtime)
    assert trigger._armed.wait(0.5)
    trigger.cancel()
    trigger.signal(signal.SIGUSR1, None)
    trigger.join()
    assert runtime.calls == 0
    assert json.loads((trigger.directory / "result.json").read_text())["status"] == "cancelled"


def test_shutdown_before_worker_start_never_runs_research(tmp_path: Path) -> None:
    runtime = Runtime()
    trigger = launcher.OperatorTrigger(tmp_path / "evidence")
    trigger.cancel()
    trigger.start(runtime)
    trigger.join()
    assert runtime.calls == 0
    assert json.loads((trigger.directory / "result.json").read_text())["status"] == "cancelled"


@pytest.mark.parametrize("symlink", [False, True])
def test_existing_evidence_is_not_adopted_or_overwritten(tmp_path: Path, symlink: bool) -> None:
    target = tmp_path / "target"
    target.mkdir()
    path = tmp_path / "evidence"
    if symlink:
        path.symlink_to(target, target_is_directory=True)
    else:
        path.mkdir()
    with pytest.raises(FileExistsError):
        launcher.OperatorTrigger(path)
    assert list(target.iterdir()) == []


def test_launcher_error_is_sanitized_and_never_retried(tmp_path: Path) -> None:
    class Failed(Runtime):
        def run_system_status_research(self, **kwargs):
            self.calls += 1
            raise RuntimeError("PRIVATE_SENTINEL")

    runtime = Failed()
    trigger = launcher.OperatorTrigger(tmp_path / "evidence")
    trigger.start(runtime)
    # A loaded coverage runner may not schedule the new worker within 500 ms.
    # This only bounds fixture startup; sanitization and one-shot behavior stay asserted below.
    assert trigger._armed.wait(5.0)
    trigger.signal(signal.SIGUSR1, None)
    trigger.join()
    assert runtime.calls == 1
    assert "PRIVATE" not in (trigger.directory / "launcher-error.json").read_text()


def test_launcher_only_adopts_normal_daemon_constructor_temporarily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_handler = signal.getsignal(signal.SIGUSR1)
    seen = []

    def fake_main(args):
        assert args == ["--host", "192.0.2.10", "daemon"]
        seen.append(cli.DaemonRuntime)
        assert issubclass(cli.DaemonRuntime, DaemonRuntime)
        assert cli.DaemonRuntime is not DaemonRuntime
        raise RuntimeError("synthetic launch failed before connection")

    monkeypatch.setattr(cli, "main", fake_main)
    with pytest.raises(RuntimeError, match="synthetic launch"):
        launcher.main(
            [
                "--expected-firmware",
                "Version 1.00.00",
                "--evidence-directory",
                str(tmp_path / "evidence"),
                "--",
                "--host",
                "192.0.2.10",
                "daemon",
            ]
        )
    assert seen and cli.DaemonRuntime is DaemonRuntime
    assert signal.getsignal(signal.SIGUSR1) == original_handler
    assert list((tmp_path / "evidence").iterdir()) == []


def test_launcher_refuses_other_cli_actions_before_creating_state(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        launcher.main(
            [
                "--expected-firmware",
                "Version 1.00.00",
                "--evidence-directory",
                str(tmp_path / "evidence"),
                "--",
                "--host",
                "192.0.2.10",
                "tui",
            ]
        )
    assert not (tmp_path / "evidence").exists()


def test_launcher_constructs_one_opt_in_owner_and_start_does_not_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts, stops = [], []
    monkeypatch.setattr(DaemonRuntime, "start", lambda self: starts.append(self))
    monkeypatch.setattr(DaemonRuntime, "stop", lambda self: stops.append(self))

    def fake_main(args):
        router = object()
        audio = SimpleNamespace(sinks=(router,))
        runtime = cli.DaemonRuntime(object(), audio, router)
        assert runtime._system_status_research is not None
        assert runtime._system_status_research._policy.expected_firmware == "Version 1.00.00"
        with pytest.raises(RuntimeError, match="exactly one daemon owner"):
            cli.DaemonRuntime(object(), audio, router)
        runtime.start()
        runtime.start()  # Does not arm a second trigger or create a new attempt.
        assert not runtime._system_status_research._attempted
        runtime.stop()
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert (
        launcher.main(
            [
                "--expected-firmware",
                "Version 1.00.00",
                "--evidence-directory",
                str(tmp_path / "evidence"),
                "--",
                "--host",
                "192.0.2.10",
                "daemon",
            ]
        )
        == 0
    )
    assert len(starts) == 2 and stops == [starts[0]]
    assert cli.DaemonRuntime is DaemonRuntime
    assert json.loads((tmp_path / "evidence/result.json").read_text())["status"] == "cancelled"
    assert not (tmp_path / "evidence/triggered.json").exists()
