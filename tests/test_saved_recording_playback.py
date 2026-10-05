"""Actual saved-player functions with synthetic media events; no radio access."""

import shutil
import subprocess
from pathlib import Path

import pytest

from .test_web_workspace import _dashboard_parser

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/sds200/web_assets/dashboard.js"


@pytest.mark.parametrize(
    "scenario",
    [
        "initial",
        "pause_resume",
        "native_pause",
        "stop",
        "ended",
        "error",
        "replacement_rejection",
        "pause_rejection",
        "stop_rejection",
        "play_failure",
        "stale_events",
        "authentication",
        "display_only",
        "session_stop",
        "pagehide",
    ],
)
def test_saved_player_lifecycle(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for saved-player behavior tests")
    script = SOURCE.read_text()
    functions = (
        "function playSavedRecording("
        + script.split("function playSavedRecording(", 1)[1].split(
            "function recordingsPageCount(", 1
        )[0]
    )
    listeners = (
        'const savedRecordingPlayer = element("saved-recording-player");'
        + script.split('const savedRecordingPlayer = element("saved-recording-player");', 1)[
            1
        ].split("window.setInterval(", 1)[0]
    )
    session = (
        "function stopNativeSessionActivity("
        + script.split("function stopNativeSessionActivity(", 1)[1].split(
            "async function dashboardFetch(", 1
        )[0]
    )
    pagehide = (
        'window.addEventListener("pagehide",'
        + script.split('window.addEventListener("pagehide",', 1)[1].split(
            'window.addEventListener("pageshow",', 1
        )[0]
    )
    result = subprocess.run(
        [node, str(ROOT / "tests/saved_recording_playback.cjs"), scenario],
        input="\n".join((functions, listeners, session, pagehide)),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{scenario} passed" in result.stdout


def test_saved_controls_are_accessible_separate_from_live_audio():
    elements = _dashboard_parser().elements
    for name in ("saved-playback-toggle", "saved-playback-stop"):
        control = elements[name]
        assert control.tag == "button"
        assert control.attributes["type"] == "button"
        assert "disabled" in control.attributes
        assert "pane-recordings" in control.ancestors
        assert "pane-audio" not in control.ancestors
    assert "controls" in elements["saved-recording-player"].attributes
