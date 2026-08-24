from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, ThemeRole
from sds200.tui import ScannerTuiApp
from sds200.tui_themes import (
    BUILT_IN_TUI_THEME_IDS,
    TUI_THEME_INTERFACE,
    TUI_THEME_MANIFEST_SCHEMA_VERSION,
    TUI_THEME_PALETTE_SCHEMA_VERSION,
    TuiThemeError,
    TuiThemeRegistry,
    built_in_tui_theme_registry,
    built_in_tui_theme_stylesheets,
    load_tui_theme_registry,
    read_built_in_tui_theme_stylesheet,
)


def _manifest(identifier: str = "dark") -> dict[str, object]:
    return {
        "schema_version": 1,
        "interface": "tui",
        "id": identifier,
        "label": identifier.title(),
        "order": 0,
        "palette": "palette.json",
        "palette_name": f"default-{identifier}",
        "stylesheet": "theme.tcss",
        "screen_class": "light" if identifier == "light" else None,
    }


def _palette_document(name: str = "default-dark") -> dict[str, object]:
    document = json.loads(json.dumps(DEFAULT_DARK_THEME.as_dict()))
    document["schema_version"] = 1
    document["name"] = name
    return document


def _write_theme(
    root: Path,
    *,
    directory_name: str = "dark",
    manifest: dict[str, object] | None = None,
    palette: dict[str, object] | None = None,
    stylesheet: str = "Screen { color: #ffffff; }\n",
    write_palette: bool = True,
    write_stylesheet: bool = True,
) -> Path:
    directory = root / directory_name
    directory.mkdir()
    manifest_document = _manifest(directory_name) if manifest is None else manifest
    (directory / "manifest.json").write_text(
        json.dumps(manifest_document),
        encoding="utf-8",
    )

    palette_filename = manifest_document.get("palette")
    if (
        write_palette
        and isinstance(palette_filename, str)
        and "/" not in palette_filename
        and "\\" not in palette_filename
    ):
        palette_name = manifest_document.get("palette_name")
        palette_document = (
            _palette_document(str(palette_name)) if palette is None else palette
        )
        (directory / palette_filename).write_text(
            json.dumps(palette_document),
            encoding="utf-8",
        )

    stylesheet_filename = manifest_document.get("stylesheet")
    if (
        write_stylesheet
        and isinstance(stylesheet_filename, str)
        and "/" not in stylesheet_filename
        and "\\" not in stylesheet_filename
    ):
        (directory / stylesheet_filename).write_text(stylesheet, encoding="utf-8")
    return directory


def _serialized_hash(theme: object) -> str:
    document = theme.as_dict()  # type: ignore[attr-defined]
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def test_built_in_tui_theme_registry_is_ordered_immutable_and_compatible() -> None:
    registry = built_in_tui_theme_registry()

    assert registry.identifiers == BUILT_IN_TUI_THEME_IDS
    assert tuple(theme.order for theme in registry.themes) == (0, 10)
    assert tuple(theme.label for theme in registry.themes) == ("Dark", "Light")
    assert tuple(theme.palette.name for theme in registry.themes) == (
        "default-dark",
        "default-light",
    )
    assert tuple(theme.screen_class for theme in registry.themes) == (None, "light")
    assert registry.require("dark").palette is DEFAULT_DARK_THEME
    assert registry.require("light").palette is DEFAULT_LIGHT_THEME
    assert registry.require_palette_name("default-dark").identifier == "dark"

    with pytest.raises(FrozenInstanceError):
        registry.themes[0].label = "Changed"  # type: ignore[misc]


def test_built_in_palettes_preserve_exact_serialization() -> None:
    assert set(DEFAULT_DARK_THEME.styles) == set(ThemeRole)
    assert set(DEFAULT_LIGHT_THEME.styles) == set(ThemeRole)
    assert _serialized_hash(DEFAULT_DARK_THEME) == (
        "cc2a23806614ff4e0b81c402a9d0e56c64cb86207deb08a2b13e60c24de8ae53"
    )
    assert _serialized_hash(DEFAULT_LIGHT_THEME) == (
        "3f6b5a1223a2a92b8e67a656e3eab81cbc7f812dace59b159e97ce4bf35200ad"
    )


def test_built_in_stylesheets_are_deterministic_and_precede_shared_layout() -> None:
    css = built_in_tui_theme_stylesheets()

    assert css.index("Default dark") < css.index("Default light")
    assert "Screen {\n    background: #10151c;" in css
    assert "Screen.light {\n    background: #f3f4f6;" in css
    assert "Screen.light .panel" in css
    assert ScannerTuiApp.CSS.startswith(css)

    shared_css = ScannerTuiApp.CSS.removeprefix(css)
    for theme_owned in (
        "#10151c",
        "#f3f4f6",
        "#1b2430",
        "#ffffff",
        "#5fafff",
        "#1d4ed8",
    ):
        assert theme_owned not in shared_css
    for shared_rule in (
        "grid-size: 2",
        "Screen.-compact .panel",
        "Screen.-short #identity",
        "content-align: center middle",
    ):
        assert shared_rule in shared_css


def test_tui_theme_registry_loads_by_manifest_order(tmp_path: Path) -> None:
    later = _manifest("later")
    later["order"] = 20
    earlier = _manifest("earlier")
    earlier["order"] = 10
    _write_theme(tmp_path, directory_name="later", manifest=later)
    _write_theme(tmp_path, directory_name="earlier", manifest=earlier)

    registry = load_tui_theme_registry(tmp_path)

    assert registry.identifiers == ("earlier", "later")
    assert registry.require("later").order == 20
    with pytest.raises(TuiThemeError, match="unknown TUI theme"):
        registry.require("missing")
    with pytest.raises(TuiThemeError, match="unknown TUI palette name"):
        registry.require_palette_name("missing")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "unsupported.*manifest schema"),
        ("schema_version", True, "must be an integer"),
        ("interface", "web", "cross-interface"),
        ("id", "Bad_ID", "invalid TUI theme identifier"),
        ("label", " ", "nonblank text"),
        ("order", True, "must be an integer"),
        ("order", -1, "must not be negative"),
        ("palette", "../palette.json", "one local JSON filename"),
        ("palette", "https://example.test/palette.json", "one local JSON filename"),
        ("palette_name", "Bad Palette", "invalid TUI palette name"),
        ("stylesheet", "/tmp/theme.tcss", "one local TCSS filename"),
        ("stylesheet", "https://example.test/theme.tcss", "one local TCSS filename"),
        ("screen_class", "Bad Class", "null or lowercase kebab-case"),
    ],
)
def test_tui_theme_manifest_rejects_invalid_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    document = _manifest()
    document[field] = value
    _write_theme(tmp_path, manifest=document)

    with pytest.raises(TuiThemeError, match=message):
        load_tui_theme_registry(tmp_path)


def test_tui_theme_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    document = _manifest()
    document["unexpected"] = "value"
    _write_theme(tmp_path, manifest=document)

    with pytest.raises(TuiThemeError, match="fields do not match"):
        load_tui_theme_registry(tmp_path)


def test_tui_theme_manifest_rejects_directory_identity_mismatch(tmp_path: Path) -> None:
    _write_theme(tmp_path, directory_name="renamed", manifest=_manifest("dark"))

    with pytest.raises(TuiThemeError, match="does not match its directory"):
        load_tui_theme_registry(tmp_path)


@pytest.mark.parametrize(
    ("missing", "message"),
    [("palette", "palette is missing"), ("stylesheet", "stylesheet is missing")],
)
def test_tui_theme_manifest_rejects_missing_assets(
    tmp_path: Path,
    missing: str,
    message: str,
) -> None:
    _write_theme(
        tmp_path,
        write_palette=missing != "palette",
        write_stylesheet=missing != "stylesheet",
    )

    with pytest.raises(TuiThemeError, match=message):
        load_tui_theme_registry(tmp_path)


def test_tui_theme_manifest_rejects_undeclared_files(tmp_path: Path) -> None:
    directory = _write_theme(tmp_path)
    (directory / "extra.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(TuiThemeError, match="contains undeclared files"):
        load_tui_theme_registry(tmp_path)


@pytest.mark.parametrize("stylesheet", ["", "@import 'remote';", "A { x: url(foo); }"])
def test_tui_theme_manifest_rejects_nonlocal_stylesheets(
    tmp_path: Path,
    stylesheet: str,
) -> None:
    _write_theme(tmp_path, stylesheet=stylesheet)

    with pytest.raises(TuiThemeError, match="nonblank and local-only"):
        load_tui_theme_registry(tmp_path)


def test_tui_palette_rejects_unknown_top_level_fields(tmp_path: Path) -> None:
    palette = _palette_document()
    palette["unexpected"] = "value"
    _write_theme(tmp_path, palette=palette)

    with pytest.raises(TuiThemeError, match="palette fields do not match"):
        load_tui_theme_registry(tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("schema", "unsupported TUI palette schema"),
        ("name", "name does not match"),
        ("missing_role", "missing roles"),
        ("unknown_role", "unknown roles"),
        ("style_fields", "invalid style fields"),
        ("foreground", "null or #RRGGBB"),
        ("flag", "must be boolean"),
    ],
)
def test_tui_palette_rejects_invalid_documents(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    palette = _palette_document()
    styles = palette["styles"]
    assert isinstance(styles, dict)
    if mutation == "schema":
        palette["schema_version"] = 2
    elif mutation == "name":
        palette["name"] = "other"
    elif mutation == "missing_role":
        styles.pop("text.primary")
    elif mutation == "unknown_role":
        styles["unknown.role"] = {"foreground": "#ffffff"}
    else:
        style = styles["text.primary"]
        assert isinstance(style, dict)
        if mutation == "style_fields":
            style["unexpected"] = True
        elif mutation == "foreground":
            style["foreground"] = "red"
        else:
            style["bold"] = 1
    _write_theme(tmp_path, palette=palette)

    with pytest.raises(TuiThemeError, match=message):
        load_tui_theme_registry(tmp_path)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("identifier", "duplicate identities"),
        ("order", "duplicate order values"),
        ("palette", "duplicate palette names"),
        ("screen_class", "duplicate screen classes"),
    ],
)
def test_tui_theme_registry_rejects_duplicate_contract_fields(
    field: str,
    message: str,
) -> None:
    dark, light = built_in_tui_theme_registry().themes
    if field == "palette":
        duplicate = replace(light, palette=dark.palette)
    elif field == "screen_class":
        duplicate = replace(dark, screen_class=light.screen_class)
        dark, light = light, duplicate
        duplicate = light
    else:
        duplicate = replace(light, **{field: getattr(dark, field)})

    with pytest.raises(TuiThemeError, match=message):
        TuiThemeRegistry((dark, duplicate))


def test_tui_theme_registry_rejects_empty_and_nondeterministic_registry() -> None:
    themes = built_in_tui_theme_registry().themes
    with pytest.raises(TuiThemeError, match="must not be empty"):
        TuiThemeRegistry(())
    with pytest.raises(TuiThemeError, match="deterministic order"):
        TuiThemeRegistry(tuple(reversed(themes)))


def test_built_in_stylesheet_reader_rejects_forged_manifest() -> None:
    theme = built_in_tui_theme_registry().themes[0]
    forged = replace(theme, label="Forged")

    with pytest.raises(TuiThemeError, match="not a canonical built-in"):
        read_built_in_tui_theme_stylesheet(forged)


def test_tui_theme_contract_constants_are_stable() -> None:
    assert TUI_THEME_MANIFEST_SCHEMA_VERSION == 1
    assert TUI_THEME_PALETTE_SCHEMA_VERSION == 1
    assert TUI_THEME_INTERFACE == "tui"
