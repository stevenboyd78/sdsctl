from __future__ import annotations

import json
import re
from dataclasses import FrozenInstanceError
from importlib.resources import files
from pathlib import Path

import pytest

from sds200.web_themes import (
    BUILT_IN_WEB_THEME_IDS,
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
    assert tuple(theme.order for theme in registry.themes) == (0, 10, 20, 30, 40, 50)
    assert tuple(theme.label for theme in registry.themes) == (
        "System",
        "LCARS-inspired",
        "Matrix-inspired",
        "First Responder",
        "Amateur Radio",
        "Pip-Boy-inspired",
    )
    assert registry.require("matrix").stylesheet_url == (
        "assets/themes/matrix/theme.css"
    )
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
    assert payload[-1]["themeColors"] == {
        "light": "#071008",
        "dark": "#071008",
    }
    assert registry.browser_json() == registry.browser_json()


def test_built_in_stylesheets_are_isolated_package_resources() -> None:
    registry = built_in_web_theme_registry()
    base = files("sds200.web_assets").joinpath("dashboard.css").read_text(
        encoding="utf-8"
    )

    assert ':root[data-theme=' not in base
    for theme in registry.themes:
        stylesheet = read_built_in_web_theme_stylesheet(theme).decode("utf-8")
        assert f':root[data-theme="{theme.identifier}"]' in stylesheet
        assert "javascript:" not in stylesheet.lower()
        assert "https://" not in stylesheet.lower()
        assert "http://" not in stylesheet.lower()
        assert re.search(
            r':root\[data-theme="[^"]+"\]\s+#[0-9a-fA-F]{3,8}\b',
            stylesheet,
        ) is None
        for other in registry.identifiers:
            if other != theme.identifier:
                assert f'data-theme="{other}"' not in stylesheet


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
    match = re.search(r':root\[data-theme="lcars"\] \.radio-field-groups\s*\{([^}]+)\}',
                      stylesheet)
    assert match is not None
    assert "border-radius: var(--radius-small);" in match[1]
    assert "999px" not in match[1]
    assert "overflow:" not in match[1] and "z-index:" not in match[1]
    # Decorative curves remain elsewhere; data-bearing corners alone change.
    assert "border-radius: 999px 0.3rem 0.3rem 999px;" in stylesheet
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
    assert "padding-right: 1.6rem;" in header[1]
    assert "border-radius: 0 0 1.4rem 0;" in header[1]
    refresh = re.search(
        r':root\[data-theme="lcars"\] #recordings-refresh\s*\{([^}]+)\}',
        stylesheet,
    )
    assert refresh is not None and "flex-shrink: 0;" in refresh[1]
    assert ".recording-library-header > div {\n  min-width: 0;" in stylesheet
    viewport = files("sds200.web_assets").joinpath(
        "dashboard-viewport.css"
    ).read_text(encoding="utf-8")
    rail = re.search(
        r':root\[data-theme="lcars"\] \.site-header\s*\{([^}]+)\}',
        viewport,
    )
    assert rail is not None
    assert "padding-left: 2.2rem !important;" in rail[1]
    panels = re.search(
        r':root\[data-theme="lcars"\] \.panel-header\s*\{([^}]+)\}',
        viewport,
    )
    assert panels is not None
    assert "margin-right: 4.2rem !important;" in panels[1]


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
