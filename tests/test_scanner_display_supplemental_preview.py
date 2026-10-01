"""Actual renderer compatibility at a single synthetic cut, not live delivery."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.scanner_display_frame import project_scanner_display_frame
from sds200.scanner_display_frame_preview import render_scanner_display_gallery
from sds200.scanner_display_tui import render_mimic_terminal
from sds200.web_dashboard import create_web_dashboard_app

from .test_home_assistant_mimic_lovelace import module

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/render_scanner_display_supplemental.py"


@pytest.fixture(scope="module")
def preview():
    return runpy.run_path(str(SCRIPT))


def payload(view):
    return {
        "protocol": "sdsctl.web",
        "version": 1,
        "display": {
            "schema_version": 1,
            "endpoint_id": "00000000-0000-0000-0000-000000000064",
            "stream_id": "00000000-0000-0000-0000-000000000066",
            "session_id": "00000000-0000-0000-0000-000000000067",
            "failure": None,
            "source_status": "matching",
            "frames": {
                style: project_scanner_display_frame(getattr(view, style))
                for style in ("preferred", "simple", "detail")
            },
        },
    }


def test_preview_is_explicitly_synthetic_and_no_network(preview):
    html = preview["render_preview"]()
    assert html.count("<template ") == 21
    assert html.count('<pre class="supplemental-notes">') == 21
    assert "SYNTHETIC POINT-IN-TIME PREVIEW" in html
    assert "NOT the scanner&#x27;s F0/S0/D0 display" in html
    assert "99:On" in html and "04:12" in html and "Sep21" in html
    assert "default-src &#x27;none&#x27;" in html
    script = html.split("<script>")[1].split("</script>")[0]
    for forbidden in ("fetch", "XMLHttpRequest", "WebSocket", "localStorage", "innerHTML"):
        assert forbidden not in script


def test_notes_are_outside_lcd_and_cannot_inject_html(preview):
    view = preview["build_presentations"]()["current_trunk"]
    frames = {
        "sample": {
            "profile": view.preferred,
            "simple": view.simple,
            "detail": view.detail,
        }
    }
    html = render_scanner_display_gallery(
        frames, notes={"sample": '<img src=x onerror="bad()"> & </template><script>bad()</script>'}
    )
    assert "<img " not in html and html.count("<script>") == 1
    assert "&lt;img " in html and "&lt;/template&gt;" in html
    assert '</section><pre class="supplemental-notes">' in html
    for bad in ({"missing": "x"}, {"sample": "x" * 20001}, {"sample": None}):
        with pytest.raises(ValueError, match="plain-text"):
            render_scanner_display_gallery(frames, notes=bad)


def test_cli_refuses_overwrite_and_only_writes_preview(tmp_path):
    command = [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path)]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    first = subprocess.run(command, env=env, capture_output=True, timeout=15)
    assert first.returncode == 0, first.stderr
    assert "7 synthetic" in first.stdout.decode()
    before = (tmp_path / "index.html").read_bytes()
    second = subprocess.run(command, env=env, capture_output=True, timeout=15)
    assert second.returncode != 0
    assert before == (tmp_path / "index.html").read_bytes()
    assert {path.name for path in tmp_path.iterdir()} == {"index.html"}


@pytest.mark.parametrize("width,height", [(60, 22), (100, 30), (160, 50)])
def test_terminal_point_in_time_rendering_all_states(preview, width, height):
    for name, view in preview["build_presentations"]().items():
        for frame in (view.preferred, view.simple, view.detail):
            rendered = render_mimic_terminal(
                project_scanner_display_frame(frame), width=width, height=height
            ).plain
            expected = name not in {
                "clock_expired",
                "psi_expired",
                "clock_invalid",
                "supplemental_disabled",
            }
            assert ("04:12" in rendered) is expected
            assert all(len(line) <= width for line in rendered.splitlines())


def test_web_decoder_and_ha_card_accept_clock_at_same_cut(preview):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node needed for actual renderer tests")
    views = preview["build_presentations"]()
    samples = {name: payload(view) for name, view in views.items()}
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        script = client.get("/assets/mimic-sds.js").text
    runner = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const window = {};
vm.runInNewContext(input.script, {window});
for (const [name, payload] of Object.entries(input.samples)) {
  const data = window.sdsctlMimic.decode(payload);
  for (const frame of Object.values(data.frames)) {
    for (const cell of frame.screen.regions.filter(r => ['Day', 'Time'].includes(r.token))) {
      const expected = !['clock_expired', 'psi_expired', 'clock_invalid',
        'supplemental_disabled'].includes(name);
      assert.equal(cell.value_status === 'raw_source', expected);
      if (expected) assert.equal(window.sdsctlMimic.presentValue(cell),
        cell.token === 'Day' ? 'Sep21' : '04:12');
      else assert.equal(cell.text, null);
    }
  }
}
console.log('web point-in-time render passed');
"""
    result = subprocess.run(
        [node, "-e", runner],
        input=json.dumps({"script": script, "samples": samples}),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "point-in-time render passed" in result.stdout
    samples["held_trunk"] = samples["current_trunk"]
    result = subprocess.run(
        [node, str(ROOT / "tests/home_assistant_mimic_lifecycle.cjs")],
        input=json.dumps(
            {
                "script": module(),
                "waterfall": module("waterfall"),
                "scenarios": samples,
                "case": "render_all",
            }
        ),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "render_all passed"
