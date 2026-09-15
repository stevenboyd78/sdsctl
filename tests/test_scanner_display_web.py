from __future__ import annotations

import json
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.scanner_display_frame import project_scanner_display_frame
from sds200.scanner_display_web import scanner_display_browser_contract
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_dashboard import create_web_dashboard_app


def scenarios():
    root = Path(__file__).resolve().parents[1]
    frames = runpy.run_path(str(root / "scripts/render_scanner_display_frames.py"))[
        "build_scenarios"
    ]()
    result = {}
    for name, variants in frames.items():
        preferred = variants["profile"]
        result[name] = {
            "protocol": "sdsctl.web",
            "version": 1,
            "display": {
                "schema_version": 1,
                "endpoint_id": "00000000-0000-0000-0000-000000000064",
                "stream_id": "00000000-0000-0000-0000-000000000001",
                "session_id": None
                if preferred.status == "disconnected"
                else "00000000-0000-0000-0000-000000000002",
                "failure": None,
                "source_status": "matching",
                "frames": {
                    ("preferred" if style == "profile" else style): project_scanner_display_frame(
                        frame
                    )
                    for style, frame in variants.items()
                },
            },
        }
    return result


def test_browser_contract_uses_only_canonical_source_geometry():
    contract = scanner_display_browser_contract()
    assert len(contract["layouts"]) == 7
    assert set(contract["leds"]) == {
        "Off",
        "Blue",
        "Red",
        "Magenta",
        "Green",
        "Cyan",
        "Yellow",
        "White",
    }
    assert "source_path" not in json.dumps(contract)
    assert all(
        layout["rows"] == 20 and layout["columns"] == 30 for layout in contract["layouts"].values()
    )


@pytest.mark.parametrize("mode", ["generic", "operator", "display"])
def test_mimic_assets_are_packaged_and_authorized_read_only(mode):
    origin = "https://scanner.example.test"
    auth = (
        None
        if mode == "generic"
        else WebDashboardAuthentication(
            "operator-password-testing", origin, display_password="display-password-testing"
        )
    )
    app = create_web_dashboard_app(lambda: None, lan_authentication=auth)
    with TestClient(app, base_url=origin) as client:
        if auth:
            path = "/auth/display/login" if mode == "display" else "/auth/login"
            response = client.post(
                path,
                data={"password": f"{mode}-password-testing"},
                headers={"Origin": origin},
                follow_redirects=False,
            )
            assert response.status_code == 303
        html = client.get("/")
        assert "assets/mimic-sds.js" in html.text and "assets/mimic-sds.css" in html.text
        for asset in ("mimic-sds.js", "mimic-sds.css"):
            response = client.get(f"/assets/{asset}")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert "unsafe-inline" not in response.headers["content-security-policy"]
        script = client.get("/assets/mimic-sds.js").text
        assert "__SDSCTL_MIMIC_CONTRACT__" not in script
        assert "innerHTML" not in script


def test_javascript_decoder_accepts_all_synthetic_frames_and_rejects_bad_wire_data():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node needed for browser decoder tests")
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        script = client.get("/assets/mimic-sds.js").text
    runner = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const window = {};
vm.runInNewContext(input.script, {window});
const {decode, presentValue} = window.sdsctlMimic;
for (const payload of Object.values(input.scenarios)) assert.ok(decode(payload));
const original = input.scenarios.held_trunk;
let rejected = 0;
for (const mutate of [
 p => p.version = 2,
 p => p.extra = 'private',
 p => p.display.endpoint_id = '/private/path',
 p => p.display.schema_version = 99,
 p => p.display.frames.preferred.source.source_kind = 'unexpected',
 p => p.display.frames.preferred.source.acquired_at = 'today',
 p => p.display.frames.preferred.sequence = -1,
 p => p.display.frames.preferred.age_seconds = Infinity,
 p => p.display.frames.preferred.screen.rows = 999999,
 p => p.display.frames.preferred.screen.mode = '__proto__',
 p => p.display.frames.preferred.screen.regions.reverse(),
 p => p.display.frames.preferred.screen.regions[0].column = -1,
 p => p.display.frames.preferred.screen.regions[0].alignment = 'fixed',
 p => p.display.frames.preferred.screen.regions[0].stored_color =
   {text: 'url(x)', background:'000000'},
 p => p.display.frames.preferred.screen.regions[0].token = 'x'.repeat(65),
 p => p.display.frames.preferred.screen.regions.find(r => r.text !== null).text = 'x'.repeat(257),
 p => p.display.frames.preferred.screen.regions.find(r => r.text !== null).text = '\u001b',
 p => p.display.frames.preferred.screen.regions.find(r => r.text !== null).text = 'hidden\u200b',
 p => p.display.frames.preferred.screen.regions.find(r => r.text !== null).text = '\ud800',
 p => p.display.frames.preferred.indicators.alert_led = 'Orange',
 p => p.display.frames.preferred.indicators.channel_hold = 'true',
 p => p.display.frames.preferred.status = 'stale',
 p => p.display.frames.simple.sequence += 1,
 p => p.display.frames.detail.profile_revision = 'a'.repeat(64),
 p => p.display.frames.detail.screen = null,
 p => p.display.frames.preferred.screen.issues = [{namespace:'path',group:1,kind:'missing_group'}],
]) {
 const payload = structuredClone(original); mutate(payload);
 assert.throws(() => decode(payload)); rejected++;
}
assert.equal(presentValue({id:'option_a_1',token:'TGID',text:'TGID:1234'}), 'TGID:1234');
assert.equal(presentValue({id:'option_a_1',token:'TGID',text:'00101'}), 'TGID: 00101');
assert.equal(presentValue({id:'option_a_1',token:'Frequency',text:'773.343750MHz'}),
 '773.343750MHz');
assert.equal(presentValue({id:'option_a_1',token:'Frequency',text:'00949000'}), '00949000');
console.log(JSON.stringify({accepted: Object.keys(input.scenarios).length, rejected}));
"""
    result = subprocess.run(
        [node, "-e", runner],
        input=json.dumps({"script": script, "scenarios": scenarios()}),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"accepted": 33, "rejected": 26}


def test_javascript_controller_freshness_identity_and_session_lifecycle():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node needed for browser controller tests")
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        script = client.get("/assets/mimic-sds.js").text
    result = subprocess.run(
        [node, str(Path(__file__).with_name("scanner_display_browser_lifecycle.cjs"))],
        input=json.dumps({"script": script, "scenarios": scenarios()}),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "recovery passed" in result.stdout
