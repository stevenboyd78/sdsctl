from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_lovelace import (
    HOME_ASSISTANT_LOVELACE_CARD_FILENAME,
    HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME,
    HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_RESOURCE_URL,
    HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME,
    install_home_assistant_lovelace_cards,
    install_home_assistant_lovelace_display_card,
)
from sds200.home_assistant_themes import (
    built_in_home_assistant_theme_registry,
    read_built_in_home_assistant_theme_module,
)

EXPECTED_ENTITY_FIELDS = {
    "scanner_connected",
    "screen_kind",
    "system",
    "department",
    "site",
    "channel",
    "frequency",
    "modulation",
    "service_type",
    "tone_out_tone_a",
    "tone_out_tone_b",
    "signal",
    "rssi",
    "audio_running",
    "recording_active",
    "recording_status",
    "daemon_state",
}


def display_target(tmp_path: Path) -> Path:
    return (
        tmp_path
        / "homeassistant"
        / "www"
        / "sds200"
        / HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME
    )


def display_card_text() -> str:
    theme = built_in_home_assistant_theme_registry().require("sds200-display")
    return read_built_in_home_assistant_theme_module(theme).decode(
        "utf-8"
    )


def compact_card_text() -> str:
    theme = built_in_home_assistant_theme_registry().require("compact")
    return read_built_in_home_assistant_theme_module(theme).decode("utf-8")


def run_display_card_javascript(body: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for display-card runtime validation.")

    harness = f"""
global.HTMLElement = class {{}};
global.CustomEvent = class {{}};
global.customElements = {{get: () => null, define: () => undefined}};
global.window = {{}};
{display_card_text()}
{body}
"""
    completed = subprocess.run(
        [node, "-e", harness],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_display_card_resource_url_uses_home_assistant_local_path() -> None:
    assert HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_RESOURCE_URL == (
        "/local/sds200/sds200-display-card.js?v="
        "b2d47c2b7abd19a92b2ee61b6b3de00362366f8df828d7786c54ae35aa0ada72"
    )


def test_install_cards_installs_all_packaged_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "sds200.home_assistant_lovelace.HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY",
        tmp_path,
    )

    compact = tmp_path / HOME_ASSISTANT_LOVELACE_CARD_FILENAME
    display = tmp_path / HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME
    waterfall = tmp_path / HOME_ASSISTANT_LOVELACE_WATERFALL_CARD_FILENAME

    assert install_home_assistant_lovelace_cards() == (
        compact,
        display,
        waterfall,
    )
    assert compact.read_text(encoding="utf-8") == compact_card_text()
    assert display.read_text(encoding="utf-8") == display_card_text()
    assert waterfall.read_text(encoding="utf-8").startswith(
        '"use strict";\n'
    )


def test_display_card_packaged_asset_is_importable() -> None:
    text = display_card_text()

    assert 'const SDS200_DISPLAY_CARD_TYPE = "sds200-display-card";' in text
    assert 'const SDS200_DISPLAY_CARD_TAG = "sds200-display-card";' in text


def test_display_card_uses_exact_existing_discovery_fields() -> None:
    fields = set(
        re.findall(
            r'key: "([a-z_]+)"',
            display_card_text(),
        )
    )

    assert fields == EXPECTED_ENTITY_FIELDS


def test_display_card_has_all_layout_palette_and_fit_presets() -> None:
    text = display_card_text()

    for value in (
        "simple",
        "detail",
        "search",
        "weather",
        "tone_out",
        "auto",
    ):
        assert f'value: "{value}"' in text

    for value in (
        "color",
        "black_on_white",
        "white_on_black",
    ):
        assert f'value: "{value}"' in text

    assert 'value: "card"' in text
    assert 'value: "viewport"' in text
    assert "SDS200_DISPLAY_SCAN_LAYOUTS" in text
    assert 'name: "scan_layout"' in text


def test_display_card_reuses_every_system_web_palette() -> None:
    expected = {
        item["id"]: [
            item[field]
            for field in (
                "background",
                "surface",
                "panel",
                "foreground",
                "foreground-muted",
                "border",
                "primary",
                "secondary",
                "warning",
                "error",
                "success",
                "accent",
            )
        ]
        for item in json.loads(
            Path("src/sds200/web_assets/system-palettes.json").read_text(
                encoding="utf-8"
            )
        )
    }
    result = run_display_card_javascript(
        """
const properties = new Map();
applyDisplaySystemPalette({style: {setProperty: (name, value) => {
  properties.set(name, value);
}}}, "nord");
process.stdout.write(JSON.stringify({
  palettes: SDS200_DISPLAY_SYSTEM_PALETTES,
  options: SDS200_DISPLAY_PALETTES.map(({value}) => value),
  config: requireDisplayCardConfig({palette: "nord", entities: {}}),
  properties: Object.fromEntries(properties),
}));
"""
    )

    assert result["palettes"] == expected
    assert result["options"] == [
        "color",
        "black_on_white",
        "white_on_black",
        *expected,
    ]
    assert result["config"]["palette"] == "nord"
    assert result["properties"]["--frame-bg"] == "#2E3440"
    assert result["properties"]["--active"] == "#A3BE8C"


def test_display_card_auto_layout_maps_known_screens_and_safe_fallbacks() -> None:
    result = run_display_card_javascript(
        """
const card = Object.create(Sds200DisplayCard.prototype);
card._config = requireDisplayCardConfig({
  layout: "auto",
  scan_layout: "detail",
  entities: {screen_kind: "sensor.sds200_screen_kind"},
});
const result = {};
for (const kind of [
  "scanning",
  "search",
  "close_call",
  "weather",
  "tone_out",
  "unknown",
  "future_mode",
  "unavailable",
  "",
]) {
  card._stateText = (_field, fallback) => kind || fallback;
  result[kind || "missing"] = card._resolvedLayout();
}
process.stdout.write(JSON.stringify(result));
"""
    )

    assert result == {
        "scanning": "detail",
        "search": "search",
        "close_call": "search",
        "weather": "weather",
        "tone_out": "tone_out",
        "unknown": "detail",
        "future_mode": "detail",
        "unavailable": "detail",
        "missing": "detail",
    }


def test_display_card_explicit_layout_ignores_screen_kind() -> None:
    result = run_display_card_javascript(
        """
const result = {};
for (const layout of ["simple", "detail", "search", "weather", "tone_out"]) {
  const card = Object.create(Sds200DisplayCard.prototype);
  card._config = requireDisplayCardConfig({layout, entities: {}});
  card._stateText = () => "weather";
  result[layout] = card._resolvedLayout();
}
process.stdout.write(JSON.stringify(result));
"""
    )

    assert result == {
        "simple": "simple",
        "detail": "detail",
        "search": "search",
        "weather": "weather",
        "tone_out": "tone_out",
    }


def test_display_card_tone_out_layout_presents_configured_tones() -> None:
    text = display_card_text()

    assert '["Tone A", "tone_out_tone_a"]' in text
    assert '["Tone B", "tone_out_tone_b"]' in text
    assert "function toneOutDisplay(value)" in text
    assert 'Number(match[1]) === 0 ? "Detect" : value' in text
    assert '["tone_out_tone_a", "tone_out_tone_b"].includes(field)' in text
    assert "special-layout-tone_out" in text
    assert "grid-template-rows: repeat(5, minmax(0, 1fr));" in text


def test_display_card_uses_graphical_configuration_and_domain_filters() -> None:
    text = display_card_text()

    for required in (
        "static getConfigForm()",
        'name: "layout"',
        'name: "palette"',
        'name: "fit"',
        'type: "expandable"',
        'name: "entities"',
        "selector: { select:",
        "selector: { text:",
        'domain: "binary_sensor"',
        'domain: "sensor"',
        "computeLabel:",
        "computeHelper:",
        "assertConfig:",
        "SDS200 display card title must be text.",
        "supportedFields",
        "is not supported.",
        r"/^[a-z0-9_]+\.[a-z0-9_]+$/",
    ):
        assert required in text


def test_display_card_uses_supported_state_context_subscription() -> None:
    text = display_card_text()

    for required in (
        "new CustomEvent(",
        '"context-request"',
        'event.context = "states";',
        "event.subscribe = true;",
        "event.callback = this._updateStates;",
        "disconnectedCallback()",
        "this._unsubscribe();",
    ):
        assert required in text

    assert "set hass(value)" not in text
    assert "this._hass.states" not in text


def test_display_card_is_read_only_transport_free_and_self_contained() -> None:
    text = display_card_text()

    for forbidden in (
        "innerHTML",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "callService",
        "callApi",
        "callWS",
        "requestFullscreen",
        "import(",
    ):
        assert forbidden not in text


def test_display_card_has_bounded_responsive_scanner_surface() -> None:
    text = display_card_text()

    for required in (
        "aspect-ratio: 4 / 3;",
        "100dvh - 4rem",
        "container-type: inline-size;",
        "font-size: clamp(",
        "text-overflow: ellipsis;",
        "white-space: nowrap;",
        "overflow: hidden;",
        'data-fit="viewport"',
        "grid-template-rows:",
        "grid-template-columns:",
    ):
        assert required in text

    assert "overflow: auto" not in text
    assert "overflow: scroll" not in text


def test_display_card_registers_one_additive_picker_entry() -> None:
    text = display_card_text()

    assert "window.customCards" in text
    assert "customElements.define" in text
    assert 'name: "SDS200 Display"' in text
    assert "documentationURL:" in text


def test_display_card_install_copies_packaged_asset_atomically(
    tmp_path: Path,
) -> None:
    target = display_target(tmp_path)

    installed = install_home_assistant_lovelace_display_card(target)

    assert installed == target
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0o644
    assert target.read_text(encoding="utf-8") == display_card_text()


def test_display_card_install_is_idempotent_and_replaces_stale_file(
    tmp_path: Path,
) -> None:
    target = display_target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stale", encoding="utf-8")

    first = install_home_assistant_lovelace_display_card(target)
    before = target.stat()
    second = install_home_assistant_lovelace_display_card(target)
    after = target.stat()

    assert first == second == target
    assert target.read_text(encoding="utf-8") == display_card_text()
    assert before.st_ino == after.st_ino
    assert before.st_mtime_ns == after.st_mtime_ns


@pytest.mark.parametrize(
    "symlink_part",
    [
        "www",
        "sds200",
        "sds200-display-card.js",
    ],
)
def test_display_card_install_refuses_symlink_paths(
    tmp_path: Path,
    symlink_part: str,
) -> None:
    homeassistant = tmp_path / "homeassistant"
    www = homeassistant / "www"
    card_directory = www / "sds200"
    target = card_directory / HOME_ASSISTANT_LOVELACE_DISPLAY_CARD_FILENAME
    outside = tmp_path / "outside"

    homeassistant.mkdir()
    outside.mkdir()

    if symlink_part == "www":
        www.symlink_to(outside, target_is_directory=True)
    elif symlink_part == "sds200":
        www.mkdir()
        card_directory.symlink_to(outside, target_is_directory=True)
    else:
        card_directory.mkdir(parents=True)
        outside_file = outside / "outside.js"
        outside_file.write_text("outside", encoding="utf-8")
        target.symlink_to(outside_file)

    with pytest.raises(SDS200Error, match="refuses symlinks"):
        install_home_assistant_lovelace_display_card(target)


def test_display_card_install_rejects_wrong_destination() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        install_home_assistant_lovelace_display_card(
            Path("www/sds200/sds200-display-card.js")
        )

    with pytest.raises(ValueError, match="must use"):
        install_home_assistant_lovelace_display_card(
            Path("/tmp/sds200-card.js")
        )


def test_display_card_install_preserves_unrelated_files_and_no_temporary(
    tmp_path: Path,
) -> None:
    target = display_target(tmp_path)
    target.parent.mkdir(parents=True)
    unrelated = target.parent / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    install_home_assistant_lovelace_display_card(target)

    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(target.parent.glob(".*.tmp")) == []
