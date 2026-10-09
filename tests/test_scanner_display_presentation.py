from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from sds200.scanner_display_presentation import (
    INACTIVE_COLOR,
    SIGNAL_BARS,
    TOGGLE_REGIONS,
    TOGGLE_TOKENS,
    UNKNOWN_COLOR,
    present_indicator,
    signal_reception_state,
)
from sds200.web_dashboard import create_web_dashboard_app

CASES = [(identifier, None, "fixed", label) for identifier, label in TOGGLE_REGIONS.items()]
CASES += [("icon_1", token, "configured", label) for token, label in TOGGLE_TOKENS.items()]


@pytest.mark.parametrize("identifier,token,selection,label", CASES)
def test_active_inactive_unknown_and_hidden_are_distinct(identifier, token, selection, label):
    active = present_indicator(identifier, token, selection, "raw_source", label)
    assert active.text == label and active.state == "on"
    assert active.foreground is active.background is None  # retain profile colors
    off = present_indicator(identifier, token, selection, "blank", None)
    assert off.text == ("" if identifier == "function" else label)
    assert off.state == "off" and off.foreground == INACTIVE_COLOR and off.background == "000000"
    for state in ("data_unavailable", "invalid_source", "not_current", "unqualified"):
        unknown = present_indicator(identifier, token, selection, state, label)
        assert unknown.text == "?" and unknown.state == "unknown"
        assert unknown.foreground == UNKNOWN_COLOR
    for hidden in ("empty", "blank", "missing_group", "invalid_group_size"):
        assert present_indicator(identifier, token, hidden, "blank", None) is None


def test_temporary_avoid_is_not_permanent_and_other_values_are_not_toggle_icons():
    assert (
        present_indicator("channel_avoid", None, "fixed", "raw_source", "T-AVOID").state
        == "temporary"
    )
    assert present_indicator("function", None, "fixed", "raw_source", "T-AVOID").state == "unknown"
    for token in ("LVL", "GPS", "SCR", "REP"):
        assert present_indicator("icon_1", token, "configured", "blank", None) is None
    assert present_indicator("option_1", "REC", "configured", "raw_source", "Off") is None


@pytest.mark.parametrize("level", range(6))
def test_signal_uses_reported_level_only(level):
    presentation = present_indicator("signal", None, "fixed", "raw_source", str(level))
    assert presentation.text == SIGNAL_BARS[:level]
    assert presentation.state == f"level_{level}"
    assert present_indicator("signal", None, "fixed", "not_current", str(level)).state == "unknown"


def test_modulation_icon_uses_reported_signal_only_for_activation():
    assert signal_reception_state("raw_source", "0") == "off"
    assert all(signal_reception_state("raw_source", str(level)) == "on" for level in range(1, 6))
    assert signal_reception_state("data_unavailable", "5") == "unknown"
    assert signal_reception_state("raw_source", "6") == "unknown"

    active = present_indicator("icon_2", "Modulation", "configured", "raw_source", "NFM", "on")
    assert active.text == "NFM" and active.state == "on"
    assert active.foreground is active.background is None
    inactive = present_indicator("icon_2", "Modulation", "configured", "raw_source", "NFM", "off")
    assert inactive.text == "NFM" and inactive.state == "off"
    assert inactive.foreground == INACTIVE_COLOR and inactive.background == "000000"
    unknown = present_indicator(
        "icon_2", "Modulation", "configured", "raw_source", "NFM", "unknown"
    )
    assert unknown.text == "?" and unknown.state == "unknown"
    assert unknown.foreground == UNKNOWN_COLOR and unknown.background == "000000"


def test_javascript_and_python_presentations_agree_for_all_states():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node needed for shared presentation parity")
    cases = []
    for identifier, token, selection, label in [
        *CASES,
        ("signal", None, "fixed", "3"),
        ("icon_2", "Modulation", "configured", "NFM"),
    ]:
        receptions = ("on", "off", "unknown") if token == "Modulation" else ("unknown",)
        for source in (label, None, "T-AVOID", "0", "5", "6", "invalid"):
            for status in (
                "raw_source",
                "blank",
                "empty",
                "data_unavailable",
                "invalid_source",
                "not_current",
            ):
                for selected in (selection, "empty", "blank", "missing_group"):
                    for reception in receptions:
                        result = present_indicator(
                            identifier, token, selected, status, source, reception
                        )
                        expected = (
                            None
                            if result is None
                            else {k: v for k, v in asdict(result).items() if v is not None}
                        )
                        cases.append(
                            {
                                "region": {
                                    "id": identifier,
                                    "token": token,
                                    "selection": selected,
                                    "value_status": status,
                                    "text": source,
                                },
                                "reception": reception,
                                "expected": expected,
                            }
                        )
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        script = client.get("/assets/mimic-sds.js").text
    runner = """
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const window = {};
require('node:vm').runInNewContext(input.script, {window});
for (const item of input.cases) {
  const actual = JSON.parse(JSON.stringify(
    window.sdsctlMimic.presentIndicator(item.region, true, item.reception)));
  assert.deepEqual(actual, item.expected);
}
console.log(input.cases.length);
"""
    result = subprocess.run(
        [node, "-e", runner],
        input=json.dumps({"script": script, "cases": cases}),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) == len(cases)
