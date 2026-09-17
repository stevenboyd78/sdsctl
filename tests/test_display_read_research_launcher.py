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
from sds200.daemon_display_read_research import DisplayReadKind, DisplayReadResearchResult
from sds200.daemon_runtime import DaemonRuntime

from .test_system_status_research_launcher import launcher as shared_launcher

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "display_read_launcher", ROOT / "scripts/research_display_read_daemon.py"
)
launcher = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"research_system_status_daemon": shared_launcher}):
    SPEC.loader.exec_module(launcher)


class Runtime:
    def __init__(self):
        self.calls = 0

    def run_display_read_research(self, *, operator_ready, timeout):
        assert operator_ready is True and timeout == 6.0
        self.calls += 1
        return DisplayReadResearchResult(
            "clock", "read_unconfirmed", True, False, 0, False, False, 0.25, "timeout", None, None
        )


def test_read_launcher_expires_without_read_or_ast(tmp_path):
    runtime = Runtime()
    trigger = launcher.DisplayReadTrigger(
        tmp_path / "case", DisplayReadKind.CLOCK, ready_timeout=0.01
    )
    trigger.start(runtime)
    trigger.join()
    assert runtime.calls == 0
    ready = json.loads((trigger.directory / "ready.json").read_text())
    assert ready["read_kind"] == "clock" and ready["research_started"] is False
    result = json.loads((trigger.directory / "result.json").read_text())
    assert result["status"] == "operator_wait_expired"


def test_read_launcher_consumes_one_trigger_and_preserves_result(tmp_path):
    runtime = Runtime()
    trigger = launcher.DisplayReadTrigger(tmp_path / "case", DisplayReadKind.CLOCK)
    trigger.signal(signal.SIGUSR1, None)
    trigger.start(runtime)
    assert trigger._armed.wait(0.5)
    assert runtime.calls == 0
    trigger.signal(signal.SIGUSR1, None)
    trigger.signal(signal.SIGUSR1, None)
    trigger.join()
    trigger.signal(signal.SIGUSR1, None)
    assert runtime.calls == 1
    saved = json.loads((trigger.directory / "result.json").read_text())
    expected = asdict(Runtime().run_display_read_research(operator_ready=True, timeout=6.0))
    assert saved == {**expected, "read_kind": "clock"}
    with pytest.raises(FileExistsError):
        launcher.DisplayReadTrigger(tmp_path / "case", DisplayReadKind.CLOCK)


def test_read_launcher_cancel_never_dispatches(tmp_path):
    runtime = Runtime()
    trigger = launcher.DisplayReadTrigger(tmp_path / "case", DisplayReadKind.SYSTEM)
    trigger.cancel()
    trigger.start(runtime)
    trigger.join()
    assert runtime.calls == 0
    assert json.loads((trigger.directory / "result.json").read_text())["status"] == "cancelled"


def test_read_launcher_constructs_one_owner_without_enabling_ast(tmp_path, monkeypatch):
    original_signal = signal.getsignal(signal.SIGUSR1)
    monkeypatch.setattr(DaemonRuntime, "start", lambda self: None)
    monkeypatch.setattr(DaemonRuntime, "stop", lambda self: None)

    def fake_main(args):
        assert args == ["--host", "192.0.2.10", "daemon"]
        router = object()
        runtime = cli.DaemonRuntime(object(), SimpleNamespace(sinks=(router,)), router)
        assert runtime._system_status_research is None
        assert runtime._display_read_research._policy.kind is DisplayReadKind.CLOCK
        with pytest.raises(RuntimeError, match="one daemon owner"):
            cli.DaemonRuntime(object(), SimpleNamespace(sinks=(router,)), router)
        runtime.start()
        runtime.start()
        assert not runtime._display_read_research._attempted
        runtime.stop()
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert (
        launcher.main(
            [
                "--expected-firmware",
                "Version 1.26.01",
                "--read-kind",
                "clock",
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


@pytest.mark.parametrize("kind,action", [("DTM,0,2026", "daemon"), ("clock", "tui")])
def test_bad_kind_or_action_is_rejected_before_evidence_creation(tmp_path, kind, action):
    with pytest.raises(SystemExit):
        launcher.main(
            [
                "--expected-firmware",
                "Version 1.26.01",
                "--read-kind",
                kind,
                "--evidence-directory",
                str(tmp_path / "case"),
                "--",
                "--host",
                "192.0.2.10",
                action,
            ]
        )
    assert not (tmp_path / "case").exists()
