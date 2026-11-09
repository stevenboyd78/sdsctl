from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from xml.etree import ElementTree

import sds200
from sds200.state import RadioStateSnapshot

ASSET_ROOT = Path(sds200.__file__).resolve().parent / "web_assets"


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

    assert len(shared_fields) == 35
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
        "const payload = await fetchStatusPayload();\n"
        "    if (authenticationRequired) return;\n"
        "    renderStatus(payload);",
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


def test_dashboard_adaptive_profiles_offer_explicit_field_inspection() -> None:
    dashboard = _asset("dashboard.html")
    script = _asset("dashboard.js")
    stylesheet = _asset("dashboard.css")

    assert 'id="radio-activity-panel"' in dashboard
    assert 'data-screen-kind="unknown"' in dashboard
    assert 'class="scanner-display-hierarchy"' in dashboard
    for group in ("hierarchy", "rf", "identity", "special"):
        assert dashboard.count(f'data-radio-group="{group}"') == 1
        assert dashboard.count(f'id="radio-view-{group}"') == 1
    assert dashboard.count('id="radio-view-auto"') == 1
    assert dashboard.count('id="radio-scan-fallback-select"') == 1

    hierarchy_start = dashboard.index('class="scanner-display-hierarchy"')
    hierarchy_end = dashboard.index("</dl>", hierarchy_start)
    hierarchy = dashboard[hierarchy_start:hierarchy_end]
    primary_targets = ("radio-system", "radio-department", "radio-channel", "radio-site")
    for target in primary_targets:
        assert f'id="{target}"' in hierarchy
        assert dashboard.count(f'id="{target}"') == 1
    assert [hierarchy.index(f'id="{target}"') for target in primary_targets] == sorted(
        hierarchy.index(f'id="{target}"') for target in primary_targets
    )
    assert hierarchy.count('data-hold-state="unknown"') == 4

    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in stylesheet

    for kind, title in (
        ("scanning", "Now scanning"),
        ("search", "Quick Search"),
        ("close_call", "Close Call"),
        ("weather", "Weather"),
        ("tone_out", "Tone-Out"),
        ("unknown", "Scanner activity"),
    ):
        assert f'{kind}: "{title}"' in script

    assert "function normalizedScreenKind(value)" in script
    assert 'typeof value === "string" && Object.hasOwn(RADIO_SCREEN_PROFILES, value)' in script
    assert 'element("radio-activity-panel").dataset.screenKind = screenKind;' in script
    assert "renderRadioProfile(radio.screen_kind);" in script

    for contract in (
        'search: Object.freeze({layout: "search", group: "rf"})',
        'close_call: Object.freeze({layout: "search", group: "rf"})',
        'weather: Object.freeze({layout: "weather", group: "special"})',
        'tone_out: Object.freeze({layout: "tone_out", group: "special"})',
    ):
        assert contract in script
    assert 'const RADIO_SCAN_FALLBACK_STORAGE_KEY = "sdsctl.web.scan-fallback";' in script
    assert 'radioScanFallback === "detail" ? "hierarchy" : null' in script
    assert "element(`radio-group-${fieldGroup}`).hidden = fieldGroup !== group;" in script
    assert "value = toneOutDisplayValue(value);" in script


def test_production_skin_recomposition_is_trusted_and_viewport_bounded() -> None:
    viewport = _asset("dashboard-viewport.css")

    assert (
        "@media (min-width: 1280px) and (min-height: 720px) and (min-aspect-ratio: 3/2)"
    ) in viewport
    assert ("@media (width < 1280px), (height < 720px), (aspect-ratio < 3/2)") in viewport
    assert "(max-aspect-ratio: 3/2)" not in viewport
    for theme in (
        "cinematic-tactical-field",
        "cinematic-cyberpunk-night",
        "cinematic-galactic-operations",
        "cinematic-mission-control",
    ):
        assert f':root[data-theme="{theme}"] .workspace-tabs' in viewport
    assert "grid-template-rows: repeat(6, minmax(0, 1fr)) !important;" in viewport
    assert "grid-template-columns: repeat(3, minmax(0, 1fr)) !important;" in viewport
    assert "grid-template-rows: minmax(0, 1fr) auto !important;" in viewport
    assert "grid-template-rows: auto minmax(0, 1fr) auto !important;" in viewport
    assert "grid-template-rows: minmax(0, 1fr) auto auto !important;" in viewport


def test_cinematic_vector_stage_is_single_decorative_local_resource() -> None:
    dashboard = _asset("dashboard.html")
    stage_start = dashboard.index('<div class="theme-stage" aria-hidden="true">')
    svg_start = dashboard.index("<svg", stage_start)
    svg_end = dashboard.index("</svg>", svg_start) + len("</svg>")
    svg_text = dashboard[svg_start:svg_end]
    svg = ElementTree.fromstring(svg_text)

    assert dashboard.count('class="cinematic-stage-art"') == 1
    assert svg.attrib == {
        "class": "cinematic-stage-art",
        "viewBox": "0 0 1920 1080",
        "preserveAspectRatio": "xMidYMid slice",
        "focusable": "false",
        "role": "presentation",
    }
    for forbidden in (
        "<text",
        "<a ",
        "<foreignObject",
        "<script",
        "tabindex=",
        'role="button"',
        "href=",
        "src=",
        "http://",
        "https://",
        "data:",
    ):
        assert forbidden not in svg_text
    assert re.search(r"\son[a-z]+\s*=", svg_text, re.IGNORECASE) is None


def test_cinematic_stage_declares_six_independent_visual_vocabulary_roots() -> None:
    dashboard = _asset("dashboard.html")
    svg_start = dashboard.index("<svg", dashboard.index('class="theme-stage"'))
    svg_end = dashboard.index("</svg>", svg_start) + len("</svg>")
    svg = ElementTree.fromstring(dashboard[svg_start:svg_end])
    scenes = [element for element in svg.findall("./g") if "data-cinematic-scene" in element.attrib]
    expected = (
        "carrier-air-wing",
        "tactical-field",
        "wizarding-archive",
        "galactic-operations",
        "mission-control",
        "cyberpunk-night",
    )
    assert tuple(scene.attrib["data-cinematic-scene"] for scene in scenes) == expected

    prefixes = {
        "carrier-air-wing": "carrier-",
        "tactical-field": "tactical-",
        "wizarding-archive": "archive-",
        "galactic-operations": "galactic-",
        "mission-control": "mission-",
        "cyberpunk-night": "cyber-",
    }
    motifs = {
        "carrier-air-wing": {
            "carrier-flight-deck",
            "carrier-hud",
            "carrier-radar",
            "carrier-aircraft",
        },
        "tactical-field": {
            "tactical-contours",
            "tactical-compass",
            "tactical-route",
            "tactical-rugged-case",
        },
        "wizarding-archive": {
            "archive-book",
            "archive-orrery",
            "archive-astrolabe",
            "archive-seal",
        },
        "galactic-operations": {
            "galactic-bridge-window",
            "galactic-planet",
            "galactic-subsystem-tower",
            "galactic-tactical",
        },
        "mission-control": {
            "mission-crt-bank",
            "mission-plotter",
            "mission-meters",
            "mission-switch-bank",
        },
        "cyberpunk-night": {
            "cyber-city",
            "cyber-transit",
            "cyber-hologram",
            "cyber-rain",
        },
    }
    class_sets: dict[str, set[str]] = {}
    for scene in scenes:
        name = scene.attrib["data-cinematic-scene"]
        classes = {
            class_name
            for element in scene.iter()
            for class_name in element.attrib.get("class", "").split()
        }
        assert classes
        assert all(class_name.startswith(prefixes[name]) for class_name in classes)
        assert motifs[name] <= classes
        class_sets[name] = classes
    for index, name in enumerate(expected):
        for other in expected[index + 1 :]:
            assert class_sets[name].isdisjoint(class_sets[other])


def test_cinematic_stage_defaults_hidden_pointer_transparent_and_high_contrast_safe() -> None:
    stylesheet = _asset("dashboard.css")
    viewport = _asset("dashboard-viewport.css")

    stage = re.search(r"\.theme-stage\s*\{(?P<body>[^}]+)\}", stylesheet)
    art = re.search(r"\.cinematic-stage-art\s*\{(?P<body>[^}]+)\}", stylesheet)
    scenes = re.search(
        r"\.cinematic-stage-art \[data-cinematic-scene\]\s*\{(?P<body>[^}]+)\}",
        stylesheet,
    )
    assert stage is not None and art is not None and scenes is not None
    for declaration in (
        "display: none",
        "contain: paint",
        "overflow: hidden",
        "pointer-events: none",
    ):
        assert declaration in stage["body"]
    for declaration in ("width: 100%", "height: 100%", "pointer-events: none"):
        assert declaration in art["body"]
    assert "display: none" in scenes["body"]
    assert "vector-effect: non-scaling-stroke" in stylesheet
    forced_colors = viewport.split("@media (forced-colors: active)", 1)[1]
    assert ".theme-stage" in forced_colors
    assert "display: none !important" in forced_colors


def test_cinematic_hierarchy_instrument_layout_tracks_ten_fields() -> None:
    dashboard = _asset("dashboard.html")
    start = dashboard.index('data-radio-group="hierarchy"')
    end = dashboard.index("</section>", start)
    hierarchy = dashboard[start:end]

    assert hierarchy.count("<div><dt>") == 10
