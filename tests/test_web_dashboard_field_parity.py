from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from sds200.state import RadioStateSnapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = REPOSITORY_ROOT / "src" / "sds200" / "web_assets"


def _asset(name: str) -> str:
    return (ASSET_ROOT / name).read_text(encoding="utf-8")


def _field_targets(script: str) -> dict[str, str]:
    match = re.search(
        r"const RADIO_STATE_FIELD_TARGETS = Object\.freeze\(\{(?P<body>.*?)\}\);",
        script,
        re.DOTALL,
    )
    assert match is not None
    return dict(re.findall(r'^\s{2}([a-z0-9_]+): "([a-z0-9-]+)",$', match["body"], re.MULTILINE))


def test_dashboard_field_inventory_matches_shared_radio_state_contract() -> None:
    dashboard = _asset("dashboard.html")
    targets = _field_targets(_asset("dashboard.js"))
    shared_fields = {field.name for field in fields(RadioStateSnapshot)}

    assert len(shared_fields) == 34
    assert set(targets) == shared_fields
    assert len(set(targets.values())) == len(targets)
    for target in targets.values():
        assert dashboard.count(f'id="{target}"') == 1


def test_dashboard_renders_every_authoritative_radio_state_as_one_projection() -> None:
    script = _asset("dashboard.js")

    assert "function renderRadioState(radio)" in script
    assert ("for (const [field, target] of Object.entries(RADIO_STATE_FIELD_TARGETS))") in script
    assert "setText(target, value, fallback);" in script
    assert script.count("renderRadioState(radio);") == 1

    # Initial status, ordered event updates, polling fallback, and periodic
    # reconciliation all converge on renderSnapshot's complete projection.
    for authoritative_boundary in (
        "renderStatus(await fetchStatusPayload())",
        'kind === "stream.snapshot"',
        'kind === "scanner.psi"',
        'kind === "radio.state"',
        "renderSnapshot(currentSnapshot",
        "window.setInterval(() => {\n  void refreshStatus();",
    ):
        assert authoritative_boundary in script


def test_dashboard_field_projection_preserves_zero_and_false_like_values() -> None:
    script = _asset("dashboard.js")
    display_value = script.split("function displayValue", 1)[1].split("function setText", 1)[0]

    assert 'value === ""' in display_value
    assert "value === null" in display_value
    assert "value === undefined" in display_value
    assert "if (!value)" not in display_value
    assert "return String(value);" in display_value


def test_dashboard_exposes_mode_transition_and_unknown_fallback_hooks() -> None:
    dashboard = _asset("dashboard.html")
    stylesheet = _asset("dashboard.css")
    targets = _field_targets(_asset("dashboard.js"))

    for field in (
        "frequency",
        "sub_audio_detected",
        "weather_mode",
        "weather_same",
        "tone_out_tone_a",
        "tone_out_tone_b",
        "screen",
        "screen_kind",
    ):
        assert f'id="{targets[field]}"' in dashboard

    assert "Scanner screen" in dashboard
    assert "Screen kind" in dashboard
    assert "Special mode" in dashboard
    assert dashboard.count('class="radio-field-group" aria-labelledby=') == 4
    assert ".radio-field-groups {" in stylesheet
    assert ".radio-field-list {" in stylesheet
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in stylesheet
    assert "@media (max-width: 42rem)" in stylesheet
