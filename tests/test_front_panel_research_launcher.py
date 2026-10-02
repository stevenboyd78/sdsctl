import importlib.util
import json
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sds200 import cli
from sds200.daemon_front_panel_research import (
    FrontPanelResearchResult,
    FrontPanelResearchStatus,
)
from sds200.daemon_runtime import DaemonRuntime
from sds200.front_panel_keys import FrontPanelKey

from .test_system_status_research_launcher import launcher as shared_launcher

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "front_panel_launcher", ROOT / "scripts/research_front_panel_daemon.py"
)
assert SPEC is not None and SPEC.loader is not None
launcher = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"research_system_status_daemon": shared_launcher}):
    SPEC.loader.exec_module(launcher)


class Runtime:
    def __init__(self, key: FrontPanelKey = FrontPanelKey.MENU) -> None:
        self.calls = 0
        self.key = key

    def run_front_panel_research(self, *, operator_ready: bool, timeout: float):
        assert operator_ready is True and timeout == 6.0
        self.calls += 1
        return FrontPanelResearchResult(
            self.key.value,
            FrontPanelResearchStatus.PRESS_UNCONFIRMED,
            True,
            False,
            False,
            False,
            False,
            None,
            None,
            0.01,
            "timeout",
        )


def test_no_signal_expires_without_key_command(tmp_path: Path) -> None:
    runtime = Runtime()
    trigger = launcher.FrontPanelTrigger(tmp_path / "case", FrontPanelKey.MENU, ready_timeout=0.01)
    trigger.start(runtime)
    trigger.join()
    assert runtime.calls == 0
    ready = json.loads((trigger.directory / "ready.json").read_text())
    assert ready["key_code"] == "M" and ready["research_started"] is False
    result = json.loads((trigger.directory / "result.json").read_text())
    assert result["status"] == "operator_wait_expired" and result["key_code"] == "M"


def test_one_signal_consumes_one_attempt_and_saves_sanitized_result(tmp_path: Path) -> None:
    runtime = Runtime()
    trigger = launcher.FrontPanelTrigger(tmp_path / "case", FrontPanelKey.MENU)
    trigger.signal(signal.SIGUSR1, None)
    trigger.start(runtime)
    assert trigger._armed.wait(0.5)
    assert runtime.calls == 0
    trigger.signal(signal.SIGUSR1, None)
    trigger.signal(signal.SIGUSR1, None)
    trigger.join()
    assert runtime.calls == 1
    expected = asdict(Runtime().run_front_panel_research(operator_ready=True, timeout=6.0))
    assert json.loads((trigger.directory / "result.json").read_text()) == expected
    assert json.loads((trigger.directory / "triggered.json").read_text()) == {
        "status": "operator_trigger_received",
        "key_code": "M",
    }


def test_cancel_before_start_never_dispatches(tmp_path: Path) -> None:
    runtime = Runtime()
    trigger = launcher.FrontPanelTrigger(tmp_path / "case", FrontPanelKey.MENU)
    trigger.cancel()
    trigger.start(runtime)
    trigger.join()
    assert runtime.calls == 0
    assert json.loads((trigger.directory / "result.json").read_text())["status"] == "cancelled"


def test_launcher_constructs_one_owner_without_enabling_other_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_signal = signal.getsignal(signal.SIGUSR1)
    monkeypatch.setattr(DaemonRuntime, "start", lambda self: None)
    monkeypatch.setattr(DaemonRuntime, "stop", lambda self: None)

    def fake_main(args: list[str]) -> int:
        assert args == ["--host", "192.0.2.10", "daemon"]
        router = object()
        runtime = cli.DaemonRuntime(object(), SimpleNamespace(sinks=(router,)), router)
        assert runtime._system_status_research is None
        assert runtime._display_read_research is None
        assert runtime._front_panel_research._policy.key is FrontPanelKey.MENU
        assert runtime._front_panel_research._policy.expected_mode == "Trunk Scan"
        with pytest.raises(RuntimeError, match="one daemon owner"):
            cli.DaemonRuntime(object(), SimpleNamespace(sinks=(router,)), router)
        runtime.start()
        runtime.start()
        assert not runtime._front_panel_research._attempted
        runtime.stop()
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert (
        launcher.main(
            [
                "--expected-firmware",
                "Version 1.26.01",
                "--key-code",
                "M",
                "--expected-mode",
                "Trunk Scan",
                "--expected-screen",
                "trunk_scan",
                "--evidence-directory",
                str(tmp_path / "case"),
                "--",
                "--host",
                "192.0.2.10",
                "daemon",
            ]
        )
        == 0
    )
    assert cli.DaemonRuntime is DaemonRuntime
    assert signal.getsignal(signal.SIGUSR1) == original_signal
    assert not (tmp_path / "case/triggered.json").exists()


@pytest.mark.parametrize(
    "key,action",
    [("A,P", "daemon"), ("M", "tui")],
)
def test_bad_key_or_non_daemon_action_refuses_before_state_creation(
    tmp_path: Path, key: str, action: str
) -> None:
    with pytest.raises(SystemExit):
        launcher.main(
            [
                "--expected-firmware",
                "Version 1.26.01",
                "--key-code",
                key,
                "--expected-mode",
                "Trunk Scan",
                "--expected-screen",
                "trunk_scan",
                "--evidence-directory",
                str(tmp_path / "case"),
                "--",
                "--host",
                "192.0.2.10",
                action,
            ]
        )
    assert not (tmp_path / "case").exists()
