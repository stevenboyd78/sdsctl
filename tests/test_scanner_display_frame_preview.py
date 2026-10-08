from __future__ import annotations

import base64
import hashlib
import os
import runpy
import subprocess
import sys
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest

from sds200.scanner_display_adapter import (
    DisplayObservationStatus,
    ScannerAlertLed,
    ScannerDisplayIndicators,
)
from sds200.scanner_display_frame_preview import (
    render_scanner_display_frame,
    render_scanner_display_gallery,
)
from sds200.scanner_display_layout import DisplaySlotSelection
from sds200.scanner_display_profile import ScannerDisplayColor
from sds200.scanner_display_profile_state import DisplayProfileStatus
from sds200.scanner_display_values import ScannerDisplayValueStatus


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text = []
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


@pytest.fixture(scope="module")
def scenarios():
    root = Path(__file__).resolve().parents[1]
    return runpy.run_path(str(root / "scripts/render_scanner_display_frames.py"))[
        "build_scenarios"
    ]()


def region_elements(source):
    return {
        attrs["data-region"]: attrs
        for _, attrs in Document(source).elements
        if "data-region" in attrs
    }


@pytest.mark.parametrize("style", ["simple", "detail"])
def test_uniform_profile_small_field_colors_reach_the_renderer(scenarios, style):
    frame = scenarios["held_trunk"][style]
    regions = region_elements(render_scanner_display_frame(frame))
    small = [
        slot
        for slot in frame.screen.regions
        if slot.region.option and slot.region.option.group == 3
    ]
    assert len(small) == (8 if style == "simple" else 6)
    for slot in small:
        assert slot.stored_color == ScannerDisplayColor("ffffff", "000000")
        assert "color:#ffffff;background:#000000" in regions[slot.region.id]["style"]


@pytest.mark.parametrize("style", ["simple", "detail"])
def test_clean_names_alignment_and_current_hold_inversion(scenarios, style):
    held = region_elements(render_scanner_display_frame(scenarios["held_trunk"][style]))
    released = region_elements(render_scanner_display_frame(scenarios["released_trunk"][style]))
    for name, color in (("system", "ff3030"), ("department", "40f040"), ("channel", "4477ff")):
        assert held[name]["data-hold"] == "on"
        assert f"color:#000000;background:#{color}" in held[name]["style"]
        assert released[name]["data-hold"] == "off"
        assert f"color:#{color};background:#000000" in released[name]["style"]
        assert "align-left" in held[name]["class"]
        assert ("two-line" in held[name]["class"]) == (style == "simple")
    for name in ("system_option", "department_option", "channel_option"):
        assert "align-left" in held[name]["class"]
    assert f"align-{'center' if style == 'simple' else 'left'}" in held["option_a_1"]["class"]
    source = render_scanner_display_frame(scenarios["held_trunk"][style])
    assert '<span class="field-label">system</span>' not in source
    assert '<span class="field-label">channel</span>' not in source
    assert '<span class="field-label">SiteName</span>' not in source
    if style == "detail":
        assert '<span class="field-label">TGID:</span>' in source


def test_partial_and_stale_hold_colors(scenarios):
    partial = region_elements(render_scanner_display_frame(scenarios["department_held"]["detail"]))
    assert partial["department"]["data-hold"] == "on"
    assert partial["system"]["data-hold"] == partial["channel"]["data-hold"] == "off"
    stale = region_elements(render_scanner_display_frame(scenarios["held_stale"]["detail"]))
    assert all(
        stale[name]["data-hold"] == "unknown" for name in ("system", "department", "channel")
    )
    assert "color:#ff3030;background:#000000" in stale["system"]["style"]


@pytest.mark.parametrize("led", list(ScannerAlertLed))
def test_named_led_colors_not_animation_or_field_colors(scenarios, led):
    source = render_scanner_display_frame(scenarios[f"led_{led.value.lower()}"]["detail"])
    assert f'data-led="{led.value.lower()}"' in source
    assert f"Scanner alert LED: {led.value}" in source
    assert "blink timing is not reproduced" in source
    assert "animation" not in source
    regions = region_elements(source)
    assert "color:#ff3030;background:#000000" in regions["system"]["style"]
    if led is ScannerAlertLed.OFF:
        assert "--alert-color:#707070" in source


def test_modulation_icon_activation_follows_signal_level(scenarios):
    frame = scenarios["released_trunk"]["detail"]
    slots = list(frame.screen.regions)
    values = list(frame.values)
    modulation_index = next(i for i, slot in enumerate(slots) if slot.region.id == "icon_1")
    signal_index = next(i for i, slot in enumerate(slots) if slot.region.id == "signal")
    slots[modulation_index] = replace(
        slots[modulation_index],
        token="Modulation",
        selection=DisplaySlotSelection.CONFIGURED,
        stored_color=ScannerDisplayColor("ffffff", "000000"),
    )
    values[modulation_index] = replace(
        values[modulation_index], status=ScannerDisplayValueStatus.RAW_SOURCE, text="NFM"
    )
    values[signal_index] = replace(
        values[signal_index], status=ScannerDisplayValueStatus.RAW_SOURCE, text="0"
    )
    frame = replace(
        frame,
        screen=replace(frame.screen, regions=tuple(slots)),
        values=tuple(values),
    )
    inactive = region_elements(render_scanner_display_frame(frame))["icon_1"]
    assert "color:#707070;background:#000000" in inactive["style"]

    values[signal_index] = replace(values[signal_index], text="3")
    active_frame = replace(frame, values=tuple(values))
    active = region_elements(render_scanner_display_frame(active_frame))["icon_1"]
    pair = slots[modulation_index].stored_color
    assert f"color:#{pair.text};background:#{pair.background}" in active["style"]


def test_missing_invalid_and_stale_led_never_mean_off(scenarios):
    for name in ("led_missing", "led_invalid", "held_stale"):
        source = render_scanner_display_frame(scenarios[name]["detail"])
        assert 'data-led="unknown"' in source and 'data-led="off"' not in source
    for invalid in ('Red" onclick="bad', "url(https://invalid.test)", "Yellow"):
        frame = replace(
            scenarios["held_trunk"]["detail"], indicators=ScannerDisplayIndicators(invalid)
        )
        source = render_scanner_display_frame(frame)
        assert 'data-led="unknown"' in source and invalid not in source


def test_renderer_independently_clears_forged_stale_indicators(scenarios):
    frame = replace(scenarios["held_trunk"]["detail"], status=DisplayObservationStatus.STALE)
    source = render_scanner_display_frame(frame)
    assert 'data-led="unknown"' in source and 'data-hold="on"' not in source


@pytest.mark.parametrize("style", ["profile", "simple", "detail"])
def test_all_transitions_are_passive_fragments_with_exact_regions(scenarios, style):
    assert len(scenarios) == 33
    for variants in scenarios.values():
        frame = variants[style]
        source = render_scanner_display_frame(frame)
        document = Document(source)
        regions = [attrs["data-region"] for _, attrs in document.elements if "data-region" in attrs]
        expected = [slot.region.id for slot in frame.screen.regions] if frame.screen else []
        assert regions == expected
        assert not {tag for tag, _ in document.elements} & {
            "script",
            "img",
            "a",
            "iframe",
            "form",
            "input",
            "object",
            "svg",
            "link",
        }
        assert all(
            not key.lower().startswith("on") and key not in {"href", "src"}
            for _, attrs in document.elements
            for key in attrs
        )
        assert f'data-state="{frame.status.value}"' in source
        assert len(source.encode()) < 100_000


@pytest.mark.parametrize("name", ["conventional", "trunk", "recovered"])
def test_presentation_choice_does_not_block_qualified_scanning_data(scenarios, name):
    outputs = {
        style: render_scanner_display_frame(frame) for style, frame in scenarios[name].items()
    }
    assert "physical layout unconfirmed" in outputs["profile"]
    for style in ("simple", "detail"):
        assert "Manual presentation choice - scanner unchanged" in outputs[style]
        assert f'data-mode="{style}_' in outputs[style]
        assert 'data-value-status="raw_source"' in outputs[style]
        assert "Dispatch" in outputs[style]
    assert len({frame.profile_revision for frame in scenarios[name].values()}) == 1


@pytest.mark.parametrize("name", ["search", "weather", "tone_out"])
def test_special_families_ignore_scanning_style_and_old_hierarchy(scenarios, name):
    outputs = [render_scanner_display_frame(frame) for frame in scenarios[name].values()]
    assert len(set(outputs)) == 1
    assert "Documented operating-screen family" in outputs[0]
    assert "Dispatch" not in outputs[0] and "Metro Simulcast" not in outputs[0]


@pytest.mark.parametrize(
    "name",
    ["stale", "connection_lost", "reconnecting", "popup", "unsupported", "conflicting_records"],
)
def test_unqualified_states_clear_live_values(scenarios, name):
    output = render_scanner_display_frame(scenarios[name]["profile"])
    assert 'data-value-status="raw_source"' not in output
    assert "Dispatch" not in output and "01625500" not in output and "Demo dialog" not in output


def test_refresh_status_and_field_disappearance(scenarios):
    assert "(refresh pending)" in render_scanner_display_frame(
        scenarios["profile_refresh_pending"]["profile"]
    )
    failed = render_scanner_display_frame(scenarios["profile_refresh_failed"]["profile"])
    assert "Profile refresh failed" in failed and "Dispatch" in failed
    assert "Import a valid display profile" in render_scanner_display_frame(
        scenarios["profile_missing"]["profile"]
    )
    assert "Dispatch" not in render_scanner_display_frame(scenarios["fields_cleared"]["profile"])


def test_text_is_literal_not_active_html(scenarios):
    source = render_scanner_display_frame(scenarios["safe_text"]["simple"])
    assert "&lt;img src=x onerror=alert(1)&gt; &amp; demo" in source
    assert all(tag not in {"img", "script"} for tag, _ in Document(source).elements)
    assert any("<img src=x onerror=alert(1)> & demo" in item for item in Document(source).text)


@pytest.mark.parametrize(
    "status", [item for item in DisplayObservationStatus if item.value != "current"]
)
def test_renderer_independently_refuses_old_values_in_noncurrent_frame(scenarios, status):
    frame = replace(scenarios["conventional"]["simple"], status=status)
    output = render_scanner_display_frame(frame)
    assert "Dispatch" not in output and 'data-value-status="raw_source"' not in output


@pytest.mark.parametrize("case", ["revision", "provenance", "profile", "layout", "region"])
def test_inconsistent_frame_cannot_supply_arbitrary_geometry(scenarios, case):
    frame = scenarios["conventional"]["simple"]
    if case == "revision":
        frame = replace(frame, profile_revision="wrong")
    elif case == "provenance":
        frame = replace(frame, provenance=None)
    elif case == "profile":
        frame = replace(frame, profile_status=DisplayProfileStatus.UNAVAILABLE)
    elif case == "layout":
        frame = replace(
            frame, screen=replace(frame.screen, layout=replace(frame.screen.layout, rows=1))
        )
    else:
        slots = frame.screen.regions
        slot = replace(slots[0], region=replace(slots[0].region, id='x" onclick="bad'))
        frame = replace(frame, screen=replace(frame.screen, regions=(slot,) + slots[1:]))
    with pytest.raises(ValueError, match="canonical screen"):
        render_scanner_display_frame(frame)


@pytest.mark.parametrize("case", ["missing", "duplicate", "foreign", "no_screen"])
def test_values_must_match_screen(scenarios, case):
    frame = scenarios["trunk"]["detail"]
    if case == "missing":
        frame = replace(frame, values=frame.values[:-1])
    elif case == "duplicate":
        frame = replace(frame, values=frame.values + frame.values[:1])
    elif case == "foreign":
        frame = replace(
            frame, values=(replace(frame.values[0], region_id="foreign"),) + frame.values[1:]
        )
    else:
        frame = replace(frame, screen=None)
    with pytest.raises(ValueError):
        render_scanner_display_frame(frame)


def test_unqualified_or_malformed_colors_are_neutral(scenarios):
    frame = scenarios["conventional"]["detail"]
    slots = frame.screen.regions
    frame = replace(
        frame,
        screen=replace(
            frame.screen,
            regions=(
                replace(
                    slots[0], stored_color=ScannerDisplayColor('ffffff" onload="bad', "000000")
                ),
            )
            + slots[1:],
        ),
    )
    source = render_scanner_display_frame(frame)
    assert "onload" not in source
    document = Document(source)
    first = next(attrs for _, attrs in document.elements if "data-region" in attrs)
    # Function is unknown: no inverted rectangle and no untrusted colors.
    assert "color:#9aa6b2;background:#000000" in first["style"]
    black_white = replace(frame, screen=replace(frame.screen, color_mode="BLACK/WHITE"))
    for _, attrs in Document(render_scanner_display_frame(black_white)).elements:
        if "data-region" in attrs:
            palette = (
                "color:#9aa6b2;background:#000000"
                if attrs["data-region"]
                in {"function", "signal", "system_avoid", "department_avoid", "channel_avoid"}
                else "color:#cbd5e1;background:#18212d"
            )
            assert palette in attrs["style"]


def test_gallery_csp_hash_and_independent_controls(scenarios):
    source = render_scanner_display_gallery(scenarios)
    document = Document(source)
    templates = [attrs["id"] for tag, attrs in document.elements if tag == "template"]
    assert len(set(templates)) == len(templates) == 99
    policy = next(
        attrs["content"]
        for tag, attrs in document.elements
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    script = source.split("<script>")[1].split("</script>")[0]
    assert (
        f"'sha256-{base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()}'" in policy
    )
    assert "default-src 'none'" in policy
    assert "connect-src" not in policy  # inherits deny-all default
    assert "fetch(" not in script and "innerHTML" not in script and "localStorage" not in script
    assert len([attrs for _, attrs in document.elements if attrs.get("class") == "consumer"]) == 2


@pytest.mark.parametrize("case", ["empty", "key", "too_many", "variant"])
def test_gallery_rejects_unbounded_or_malformed_identifiers(scenarios, case):
    variants = scenarios["conventional"]
    bad = {
        "empty": {},
        "key": {'x"><script>bad</script>': variants},
        "too_many": {f"case_{i}": variants for i in range(65)},
        "variant": {"case": {"profile": variants["profile"]}},
    }[case]
    with pytest.raises(ValueError):
        render_scanner_display_gallery(bad)


def test_cli_writes_only_offline_html_and_refuses_overwrite(tmp_path):
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(root / "scripts/render_scanner_display_frames.py"),
        "--output-dir",
        str(tmp_path),
    ]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    first = subprocess.run(command, env=env, capture_output=True, timeout=10, check=False)
    assert first.returncode == 0, first.stderr
    assert {path.name for path in tmp_path.iterdir()} == {"index.html"}
    before = (tmp_path / "index.html").read_bytes()
    second = subprocess.run(command, env=env, capture_output=True, timeout=10, check=False)
    assert second.returncode != 0
    assert before == (tmp_path / "index.html").read_bytes()
