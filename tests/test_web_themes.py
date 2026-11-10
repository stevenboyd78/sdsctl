from __future__ import annotations

import json
import re
from dataclasses import FrozenInstanceError
from importlib.resources import files
from pathlib import Path

import pytest

from sds200.web_themes import (
    BUILT_IN_WEB_THEME_IDS,
    CINEMATIC_WEB_THEME_IDS,
    WebThemeError,
    WebThemeRegistry,
    built_in_web_theme_registry,
    load_web_theme_registry,
    read_built_in_web_theme_stylesheet,
)


def _manifest(identifier: str = "system") -> dict[str, object]:
    return {
        "schema_version": 1,
        "interface": "web",
        "id": identifier,
        "label": "System" if identifier == "system" else identifier.title(),
        "order": 0,
        "stylesheet": "theme.css",
        "color_scheme": "light dark",
        "theme_colors": {"light": "#eef2f7", "dark": "#0d1420"},
    }


def _write_theme(
    root: Path,
    *,
    directory_name: str = "system",
    document: dict[str, object] | None = None,
) -> Path:
    directory = root / directory_name
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(_manifest(directory_name) if document is None else document),
        encoding="utf-8",
    )
    (directory / "theme.css").write_text(
        f':root[data-theme="{directory_name}"] {{ color-scheme: light dark; }}\n',
        encoding="utf-8",
    )
    return directory


def test_built_in_web_theme_registry_is_ordered_and_immutable() -> None:
    registry = built_in_web_theme_registry()

    assert registry.identifiers == BUILT_IN_WEB_THEME_IDS
    assert tuple(theme.order for theme in registry.themes) == (
        0,
        10,
        20,
        30,
        40,
        50,
        *range(1000, 1220, 10),
    )
    assert tuple(theme.label for theme in registry.themes) == (
        "System",
        "LCARS",
        "Matrix-inspired",
        "First Responder",
        "Amateur Radio",
        "Pip-Boy-inspired",
        "Mission Control",
        "Cyberpunk Night",
        "Synthwave Outrun",
        "Cold War Radar",
        "Dieselpunk Command",
        "Steampunk Telegraph",
        "Retro Arcade",
        "Y2K Media Center",
        "Mecha Operations",
        "Comic Book",
        "Wizarding Archive",
        "Gothic Cathedral",
        "Fantasy Cartographer",
        "Desert Empire",
        "Carrier Air Wing",
        "Motorsport Pit Wall",
        "Noir Detective",
        "Art Deco Radio",
        "Solarpunk Communications",
        "National Parks Ranger",
        "Galactic Operations",
        "Tactical Field",
    )
    assert registry.require("matrix").stylesheet_url == ("assets/themes/matrix/theme.css")
    with pytest.raises(FrozenInstanceError):
        registry.themes[0].label = "Changed"  # type: ignore[misc]


def test_built_in_web_theme_browser_document_is_deterministic() -> None:
    registry = built_in_web_theme_registry()

    payload = json.loads(registry.browser_json())

    assert [item["id"] for item in payload] == list(BUILT_IN_WEB_THEME_IDS)
    assert payload[0] == {
        "id": "system",
        "label": "System",
        "colorScheme": "light dark",
        "themeColors": {"light": "#eef2f7", "dark": "#0d1420"},
    }
    assert payload[-1]["id"] == CINEMATIC_WEB_THEME_IDS[-1]
    assert payload[-1]["themeColors"] == {
        "light": "#10140d",
        "dark": "#10140d",
    }
    assert registry.browser_json() == registry.browser_json()


def test_built_in_stylesheets_are_isolated_package_resources() -> None:
    registry = built_in_web_theme_registry()
    base = files("sds200.web_assets").joinpath("dashboard.css").read_text(encoding="utf-8")

    assert ":root[data-theme=" not in base
    for theme in registry.themes:
        stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")
        assert f':root[data-theme="{theme.identifier}"]' in stylesheet
        assert "javascript:" not in stylesheet.lower()
        assert "https://" not in stylesheet.lower()
        assert "http://" not in stylesheet.lower()
        assert (
            re.search(
                r':root\[data-theme="[^"]+"\]\s+#[0-9a-fA-F]{3,8}\b',
                stylesheet,
            )
            is None
        )
        for other in registry.identifiers:
            if other != theme.identifier:
                assert f'data-theme="{other}"' not in stylesheet


def test_built_in_themes_style_held_scanner_fields() -> None:
    registry = built_in_web_theme_registry()
    base = files("sds200.web_assets").joinpath("dashboard.css").read_text(encoding="utf-8")

    assert '[data-hold-scope][data-hold-state="held"]' in base
    assert "--hold-field-background" in base
    assert "--hold-field-border" in base
    assert "@media (forced-colors: active)" in base
    for theme in registry.themes:
        stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")
        for custom_property in (
            "--hold-field-background:",
            "--hold-field-border:",
            "--hold-field-label:",
            "--hold-field-value:",
        ):
            assert custom_property in stylesheet


def test_native_select_popup_rows_have_theme_independent_contrast() -> None:
    base = files("sds200.web_assets").joinpath("dashboard.css").read_text(
        encoding="utf-8"
    )

    popup = re.search(r"select option,\s*select optgroup\s*\{([^}]+)\}", base)
    assert popup is not None
    assert "color-scheme: light;" in popup[1]
    assert "color: #111827;" in popup[1]
    assert "background: #fff;" in popup[1]
    assert "-webkit-text-fill-color: #111827;" in popup[1]

    forced_colors = base.split("@media (forced-colors: active)", 1)[1]
    forced_popup = re.search(
        r"select option,\s*select optgroup\s*\{([^}]+)\}", forced_colors
    )
    assert forced_popup is not None
    assert "color: FieldText !important;" in forced_popup[1]
    assert "background: Field !important;" in forced_popup[1]
    assert "-webkit-text-fill-color: FieldText !important;" in forced_popup[1]


HERO_CINEMATIC_THEMES = (
    (
        "cinematic-carrier-air-wing",
        "carrier-air-wing",
        "carrier-",
        ("carrier-cloud-drift-far", "carrier-radar-turn", "carrier-hud-dash"),
        "carrier-lock-acquired",
    ),
    (
        "cinematic-tactical-field",
        "tactical-field",
        "tactical-",
        ("tactical-scope-turn", "tactical-route-flow", "tactical-contact-pulse"),
        "tactical-route-lock",
    ),
    (
        "cinematic-wizarding-archive",
        "wizarding-archive",
        "archive-",
        ("archive-ring-a", "archive-stars-breathe", "archive-candle-breathe"),
        "archive-hold-ink",
    ),
    (
        "cinematic-galactic-operations",
        "galactic-operations",
        "galactic-",
        ("galactic-stars-far", "galactic-ring-a", "galactic-sweep-turn"),
        "galactic-lock-arc",
    ),
    (
        "cinematic-mission-control",
        "mission-control",
        "mission-",
        ("mission-trace-run", "mission-needle-a", "mission-lamp-test"),
        "mission-hold-lamp",
    ),
    (
        "cinematic-cyberpunk-night",
        "cyberpunk-night",
        "cyber-",
        ("cyber-rain-far", "cyber-train-pass", "cyber-holo-a"),
        "cyber-hold-glitch",
    ),
)


@pytest.mark.parametrize(
    ("identifier", "scene", "prefix", "motion_markers", "hold_marker"),
    HERO_CINEMATIC_THEMES,
)
def test_hero_cinematic_theme_reveals_only_its_matching_panorama(
    identifier: str,
    scene: str,
    prefix: str,
    motion_markers: tuple[str, ...],
    hold_marker: str,
) -> None:
    del prefix, motion_markers, hold_marker
    stylesheet = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require(identifier)
    ).decode("utf-8")
    other_scenes = {item[1] for item in HERO_CINEMATIC_THEMES if item[1] != scene}

    assert (
        "@media (min-width: 1280px) and (min-height: 720px) and (min-aspect-ratio: 3/2)"
    ) in stylesheet
    assert (
        "@media (width < 1280px), (height < 720px), (aspect-ratio < 3/2)"
    ) in stylesheet
    assert "(max-aspect-ratio: 3/2)" not in stylesheet
    assert f'[data-cinematic-scene="{scene}"]' in stylesheet
    assert stylesheet.count("data-cinematic-scene=") == 1
    assert all(other not in stylesheet for other in other_scenes)
    assert ".theme-stage > span" in stylesheet
    assert "display: inline" in stylesheet


def test_mission_control_full_hd_tabs_reserve_a_separate_lamp_lane() -> None:
    stylesheet = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require("cinematic-mission-control")
    ).decode("utf-8")
    full_hd = stylesheet.split(
        "@media (min-width: 1280px) and (min-height: 720px) and (min-aspect-ratio: 3/2)",
        1,
    )[1]
    tab = re.search(r'\.workspace-tabs \[role="tab"\]\s*\{([^}]+)\}', full_hd)
    lamp = re.search(r'\.workspace-tabs \[role="tab"\]::before\s*\{([^}]+)\}', full_hd)
    divider = re.search(r'\.workspace-tabs \[role="tab"\]::after\s*\{([^}]+)\}', full_hd)

    assert tab is not None and lamp is not None and divider is not None
    assert "display: grid !important;" in tab[1]
    assert "grid-template-rows: 0.9rem minmax(1.1rem, auto);" in tab[1]
    assert "row-gap: 0.4rem;" in tab[1]
    assert "align-content: center;" in tab[1]
    assert "align-items: center !important;" in tab[1]
    assert "justify-items: center !important;" in tab[1]
    assert "min-height: 4.4rem !important;" in tab[1]
    assert "padding: 0.45rem 0.55rem !important;" in tab[1]
    assert "position: static;" in lamp[1]
    assert "grid-row: 1;" in lamp[1]
    assert "align-self: center;" in lamp[1]
    assert "justify-self: center;" in lamp[1]
    assert "translate: none;" in lamp[1]
    assert "height: 0.56rem;" in lamp[1]
    assert "top: 1.72rem;" in divider[1]


def test_mission_control_windowed_desktop_hierarchy_uses_single_line_cards() -> None:
    stylesheet = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require("cinematic-mission-control")
    ).decode("utf-8")
    windowed = stylesheet.split(
        "@media (min-width: 1280px) and (min-height: 720px) and "
        "(max-height: 999px) and (min-aspect-ratio: 3/2)",
        1,
    )[1]
    card = re.search(r"\.radio-field-list\s*> div\s*\{([^}]+)\}", windowed)

    assert card is not None
    assert "grid-template-columns: minmax(0, 1fr) auto;" in card[1]
    assert "align-items: center;" in card[1]
    assert "padding-block: 0.12rem;" in card[1]
    assert "text-align: end;" in windowed


def test_mission_control_short_production_hierarchy_uses_compact_instrument_bank() -> None:
    viewport = files("sds200.web_assets").joinpath("dashboard-viewport.css").read_text()
    compact = viewport.split(
        "@media (min-width: 1280px) and (min-height: 720px) and "
        "(max-height: 800px) and (min-aspect-ratio: 3/2)",
        1,
    )[1]
    field_list = re.search(r"\.radio-field-list\s*\{([^}]+)\}", compact)
    card = re.search(r"\.radio-field-list\s*> div\s*\{([^}]+)\}", compact)

    assert field_list is not None and card is not None
    assert "grid-template-columns: repeat(4, minmax(0, 1fr)) !important;" in field_list[1]
    assert "grid-template-rows: repeat(3, auto) !important;" in field_list[1]
    assert "align-content: start !important;" in field_list[1]
    assert "gap: 0.0625rem 0.35rem !important;" in field_list[1]
    assert "grid-column: auto !important;" in card[1]
    assert "grid-row: auto !important;" in card[1]
    assert "padding-block: 0 !important;" in card[1]


@pytest.mark.parametrize(
    ("identifier", "scene", "prefix", "motion_markers", "hold_marker"),
    HERO_CINEMATIC_THEMES,
)
def test_hero_cinematic_motion_and_hold_reactions_are_scene_native(
    identifier: str,
    scene: str,
    prefix: str,
    motion_markers: tuple[str, ...],
    hold_marker: str,
) -> None:
    del scene
    stylesheet = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require(identifier)
    ).decode("utf-8")

    assert "@media (prefers-reduced-motion: no-preference)" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert "animation: none !important" in stylesheet
    assert stylesheet.count(f"@keyframes {prefix}") >= 3
    assert all(marker in stylesheet for marker in motion_markers)
    assert ':has(\n  #radio-activity-panel [data-hold-state="held"]\n)' in stylesheet
    assert hold_marker in stylesheet
    assert f"@keyframes {hold_marker}" in stylesheet


def test_orbital_keyframes_preserve_authored_svg_orientation_across_loops() -> None:
    wizarding = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require("cinematic-wizarding-archive")
    ).decode("utf-8")
    cyberpunk = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require("cinematic-cyberpunk-night")
    ).decode("utf-8")

    for marker in (
        "from { transform: rotate(60deg); }",
        "to { transform: rotate(420deg); }",
        "from { transform: rotate(-60deg); }",
        "to { transform: rotate(-420deg); }",
    ):
        assert marker in wizarding
    assert "from { transform: rotate(28deg); }" in cyberpunk
    assert "to { transform: rotate(-332deg); }" in cyberpunk


@pytest.mark.parametrize(
    "identifier",
    tuple(item[0] for item in HERO_CINEMATIC_THEMES),
)
def test_hero_cinematic_forced_colors_removes_decorative_stage(
    identifier: str,
) -> None:
    stylesheet = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require(identifier)
    ).decode("utf-8")

    assert "@media (forced-colors: active)" in stylesheet
    forced_colors = stylesheet.split("@media (forced-colors: active)", 1)[1]
    assert ".theme-stage" in forced_colors
    assert "display: none" in forced_colors


@pytest.mark.parametrize(
    "identifier",
    (
        "cinematic-y2k-media",
        "cinematic-mecha-operations",
        "cinematic-comic-book",
        "cinematic-fantasy-cartographer",
        "cinematic-national-parks",
        "cinematic-solarpunk",
    ),
)
def test_light_cinematic_theme_hover_preserves_selected_tab(identifier: str) -> None:
    theme = built_in_web_theme_registry().require(identifier)
    stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")

    assert ':hover:not(:disabled):not([aria-selected="true"])' in stylesheet


@pytest.mark.parametrize(
    "identifier",
    (
        "cinematic-mission-control",
        "cinematic-cold-war-radar",
        "cinematic-dieselpunk-command",
        "cinematic-steampunk-telegraph",
        "cinematic-retro-arcade",
        "cinematic-desert-empire",
        "cinematic-carrier-air-wing",
        "cinematic-art-deco-radio",
        "cinematic-solarpunk",
        "cinematic-national-parks",
        "cinematic-galactic-operations",
        "cinematic-noir-detective",
        "cinematic-wizarding-archive",
        "cinematic-tactical-field",
    ),
)
def test_cinematic_hover_states_use_accessible_foreground(identifier: str) -> None:
    theme = built_in_web_theme_registry().require(identifier)
    stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")
    hover = stylesheet.split(
        ":is(button, .recording-action):hover:not(:disabled)", 1
    )[1].split("}", 1)[0]

    assert "color: var(--text);" in hover


@pytest.mark.parametrize(
    ("identifier", "fallbacks"),
    (
        ("cinematic-y2k-media", ("background-color: #176fa8",)),
        ("cinematic-mecha-operations", ("background-color: #0c6f9b",)),
        ("cinematic-cyberpunk-night", ("background-color: #ff4bd8",)),
        ("cinematic-galactic-operations", ("background-color: #7ac9ee",)),
        ("cinematic-tactical-field", ("background-color: #172015",)),
        (
            "cinematic-wizarding-archive",
            ("background-color: #c88b3f", "background-color: #c79f5e"),
        ),
    ),
)
def test_gradient_surfaces_have_solid_contrast_fallbacks(
    identifier: str,
    fallbacks: tuple[str, ...],
) -> None:
    theme = built_in_web_theme_registry().require(identifier)
    stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")

    assert all(fallback in stylesheet for fallback in fallbacks)


def test_pip_boy_inspired_theme_uses_stable_declarative_hooks() -> None:
    registry = built_in_web_theme_registry()
    theme = registry.require("pip-boy-inspired")
    stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")

    assert theme.label == "Pip-Boy-inspired"
    assert theme.order == 50
    assert theme.color_scheme == "dark"
    assert theme.light_theme_color == "#071008"
    assert theme.dark_theme_color == "#071008"
    for selector in (
        ".workspace-shell",
        ".workspace-tabs",
        ".workspace-deck",
        "[data-workspace-pane]",
        "#radio-activity-panel",
        ".radio-field-groups",
    ):
        assert selector in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert "@media (forced-colors: active)" in stylesheet
    for prohibited in (
        "@import",
        "url(",
        "javascript:",
        "display: none",
        "visibility: hidden",
        "[hidden]",
    ):
        assert prohibited not in stylesheet.lower()


def test_lcars_data_groups_keep_bounded_corners_without_removing_containment() -> None:
    theme = built_in_web_theme_registry().require("lcars")
    stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")
    match = re.search(
        r':root\[data-theme="lcars"\] :is\(\.radio-field-groups,[^{]+\{([^}]+)\}', stylesheet
    )
    assert match is not None
    assert "border-radius: 0;" in match[1]
    assert "999px" not in match[1]
    assert "overflow:" not in match[1] and "z-index:" not in match[1]
    # Decorative frame curves remain separate from data-bearing groups.
    assert "border-radius: 0 0 0 clamp(2rem, 5vw, 4.25rem);" in stylesheet
    base = files("sds200.web_assets").joinpath("dashboard.css").read_text(encoding="utf-8")
    groups = re.findall(r"(?m)^\.radio-field-groups\s*\{([^}]+)\}", base)
    assert groups and "overflow: hidden;" in groups[-1]


def test_lcars_header_content_reserves_space_for_decorative_edges() -> None:
    stylesheet = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require("lcars")
    ).decode("utf-8")
    header = re.search(
        r':root\[data-theme="lcars"\] \.panel-header\s*\{([^}]+)\}',
        stylesheet,
    )
    assert header is not None
    assert "border-radius: 0;" in header[1]
    viewport = (
        files("sds200.web_assets").joinpath("dashboard-viewport.css").read_text(encoding="utf-8")
    )
    assert ':root[data-theme="lcars"]' not in viewport
    layout = files("sds200.web_assets").joinpath("lcars-v2.css").read_text()
    assert "--lcars-v2-nav-cap: 3.2rem;" in layout
    assert ".workspace-tabs::before" in layout
    assert "flex: 0 0 var(--lcars-v2-nav-cap) !important;" in layout


def test_lcars_horizontal_navigation_clears_the_fluid_elbow() -> None:
    layout = files("sds200.web_assets").joinpath("lcars-v2.css").read_text()

    assert "--lcars-v2-horizontal-nav-inset: 0.8rem;" in layout
    assert "--lcars-v2-horizontal-elbow-radius: 1.25rem;" in layout
    assert "padding-inline: var(--lcars-v2-horizontal-nav-inset) !important;" in layout
    assert "padding-block-start: var(--lcars-v2-horizontal-nav-inset) !important;" in layout
    assert "@media (min-width: 44rem), (orientation: landscape)" in layout
    assert "position: relative;\n    z-index: 1;" in layout
    horizontal = layout.split("@media (max-width: 63.99rem), (max-height: 37.99rem)", 1)[1].split(
        "@media", 1
    )[0]
    assert ".workspace-shell::before" in horizontal
    assert "radial-gradient(circle at 100% 100%" in horizontal
    assert "calc(var(--lcars-v2-rail) + var(--lcars-v2-horizontal-elbow-radius))" in horizontal
    assert (
        "calc(var(--lcars-v2-shell-stroke) + var(--lcars-v2-horizontal-elbow-radius))" in horizontal
    )
    # The wide vertical rail has its own independent decorative cap and must
    # retain the existing edge-to-edge navigation geometry.
    desktop = layout.split("@media (min-width: 64rem) and (min-height: 38rem)", 2)[2]
    tabs = desktop.split(".workspace-tabs {", 1)[1].split("}", 1)[0]
    assert "padding: 0 !important;" in tabs


@pytest.mark.parametrize("theme_id", ("first-responder", "amateur-radio"))
def test_overview_label_clears_the_header_divider(theme_id: str) -> None:
    stylesheet = read_built_in_web_theme_stylesheet(
        built_in_web_theme_registry().require(theme_id)
    ).decode("utf-8")
    divider = re.search(r"\.site-header::after\s*\{([^}]+)\}", stylesheet)
    overview = re.search(r"\.overview\s*\{([^}]+)\}", stylesheet)
    assert divider is not None and overview is not None
    assert "bottom: 0;" in divider[1]
    assert "padding-top: 0.5rem;" in overview[1]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schema_version", 2, "unsupported"),
        ("interface", "tui", "cross-interface"),
        ("id", "System Theme", "invalid web theme identifier"),
        ("label", "", "must be nonblank text"),
        ("order", -1, "must not be negative"),
        ("stylesheet", "../theme.css", "one local CSS filename"),
        ("stylesheet", "/theme.css", "one local CSS filename"),
        ("stylesheet", "https://example.test/theme.css", "one local CSS filename"),
        ("stylesheet", "theme.js", "one local CSS filename"),
        ("color_scheme", "auto", "unsupported web theme color scheme"),
        (
            "theme_colors",
            {"light": "blue", "dark": "#0d1420"},
            "light color",
        ),
    ),
)
def test_web_theme_manifest_rejects_invalid_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    document = _manifest()
    document[field] = value
    _write_theme(tmp_path, document=document)

    with pytest.raises(WebThemeError, match=message):
        load_web_theme_registry(tmp_path)


def test_web_theme_manifest_rejects_directory_identity_mismatch(
    tmp_path: Path,
) -> None:
    _write_theme(tmp_path, directory_name="renamed", document=_manifest("system"))

    with pytest.raises(WebThemeError, match="does not match its directory"):
        load_web_theme_registry(tmp_path)


def test_web_theme_manifest_rejects_unknown_and_undeclared_files(
    tmp_path: Path,
) -> None:
    directory = _write_theme(tmp_path)
    (directory / "theme.js").write_text("alert('no');\n", encoding="utf-8")

    with pytest.raises(WebThemeError, match="contains undeclared files"):
        load_web_theme_registry(tmp_path)


def test_web_theme_registry_rejects_duplicate_order(tmp_path: Path) -> None:
    _write_theme(tmp_path)
    second = _manifest("second")
    second["order"] = 0
    _write_theme(tmp_path, directory_name="second", document=second)

    with pytest.raises(WebThemeError, match="duplicate order"):
        load_web_theme_registry(tmp_path)


def test_web_theme_registry_rejects_duplicate_identity() -> None:
    theme = built_in_web_theme_registry().themes[0]

    with pytest.raises(WebThemeError, match="duplicate identities"):
        WebThemeRegistry((theme, theme))


def test_web_theme_registry_requires_system_first(tmp_path: Path) -> None:
    document = _manifest("alternate")
    _write_theme(tmp_path, directory_name="alternate", document=document)

    with pytest.raises(WebThemeError, match="System must be the first"):
        load_web_theme_registry(tmp_path)
