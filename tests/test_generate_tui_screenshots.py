from __future__ import annotations

import asyncio
from pathlib import Path
from xml.etree import ElementTree

import pytest
from textual.widget import Widget

from scripts.generate_tui_screenshots import capture, create_demo_recordings


def test_recording_library_capture_shows_all_demo_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "demo-recordings"
    create_demo_recordings(directory)
    destination = tmp_path / "tui-recordings.svg"

    asyncio.run(capture(destination, size=(100, 50), show_library=True))

    svg = ElementTree.fromstring(destination.read_text(encoding="utf-8"))
    text = " ".join(
        " ".join(node.itertext())
        for node in svg.iter("{http://www.w3.org/2000/svg}text")
    )
    text = " ".join(text.split())
    entries = sorted(directory.glob("*.wav"))
    assert len(entries) == 3
    for entry in entries:
        assert entry.name in text
    assert "Recordings: 3 newest first" in text


def test_recording_library_capture_rejects_offscreen_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    create_demo_recordings(tmp_path / "demo-recordings")
    monkeypatch.setattr(Widget, "scroll_visible", lambda *args, **kwargs: None)
    destination = tmp_path / "tui-recordings.svg"

    with pytest.raises(RuntimeError, match="Recording-library screenshot omits demo entries"):
        asyncio.run(capture(destination, size=(100, 50), show_library=True))

    assert not destination.exists()
