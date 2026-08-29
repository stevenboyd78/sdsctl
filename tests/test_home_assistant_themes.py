from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from sds200.home_assistant_themes import (
    BUILT_IN_HOME_ASSISTANT_THEME_IDS,
    HOME_ASSISTANT_THEME_INTERFACE,
    HOME_ASSISTANT_THEME_MANIFEST_SCHEMA_VERSION,
    HomeAssistantThemeError,
    HomeAssistantThemeRegistry,
    built_in_home_assistant_theme_registry,
    load_home_assistant_theme_registry,
    read_built_in_home_assistant_theme_module,
)


def _manifest(identifier: str = "compact") -> dict[str, object]:
    filename = "sds200-card.js" if identifier == "compact" else f"{identifier}.js"
    custom_element = "sds200-card" if identifier == "compact" else identifier
    return {
        "schema_version": 1,
        "interface": "home-assistant",
        "id": identifier,
        "label": identifier.replace("-", " ").title(),
        "order": 0,
        "module": filename,
        "custom_element": custom_element,
        "installed_filename": filename,
        "resource_url": f"/local/sds200/{filename}",
    }


def _write_theme(
    root: Path,
    *,
    directory_name: str = "compact",
    document: dict[str, object] | None = None,
    write_module: bool = True,
) -> Path:
    directory = root / directory_name
    directory.mkdir()
    manifest = _manifest(directory_name) if document is None else document
    (directory / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    module = manifest.get("module")
    if (
        write_module
        and isinstance(module, str)
        and "/" not in module
        and "\\" not in module
    ):
        (directory / module).write_text("export {};\n", encoding="utf-8")
    return directory


def test_built_in_home_assistant_theme_registry_is_ordered_and_immutable() -> None:
    registry = built_in_home_assistant_theme_registry()

    assert registry.identifiers == BUILT_IN_HOME_ASSISTANT_THEME_IDS
    assert tuple(theme.order for theme in registry.themes) == (0, 10, 20)
    assert tuple(theme.label for theme in registry.themes) == (
        "SDS200 Scanner",
        "SDS200 Display",
        "SDS200 Waterfall",
    )
    assert tuple(theme.custom_element for theme in registry.themes) == (
        "sds200-card",
        "sds200-display-card",
        "sds200-waterfall-card",
    )
    assert tuple(theme.resource_url for theme in registry.themes) == (
        "/local/sds200/sds200-card.js",
        "/local/sds200/sds200-display-card.js",
        "/local/sds200/sds200-waterfall-card.js",
    )

    with pytest.raises(FrozenInstanceError):
        registry.themes[0].label = "Changed"  # type: ignore[misc]


def test_built_in_modules_preserve_pre_extraction_bytes() -> None:
    registry = built_in_home_assistant_theme_registry()
    hashes = {
        theme.identifier: hashlib.sha256(
            read_built_in_home_assistant_theme_module(theme)
        ).hexdigest()
        for theme in registry.themes
    }

    assert hashes == {
        "compact": "0c6c09d7c127f358f58b192c6709e5983dffe0a02199f23f20ae46f13ce8d10d",
        "sds200-display": "9b73390b49064dfd250384eb5e726a20e10514e46c5904e074a2c0890609bd80",
        "waterfall": "1401fff2bd67bf4583b866d0eae296a3f0e873425fc138baac32675f7cd29fc2",
    }


def test_home_assistant_theme_registry_loads_by_manifest_order(tmp_path: Path) -> None:
    later = _manifest("later")
    later["order"] = 20
    earlier = _manifest("earlier")
    earlier["order"] = 10
    _write_theme(tmp_path, directory_name="later", document=later)
    _write_theme(tmp_path, directory_name="earlier", document=earlier)

    registry = load_home_assistant_theme_registry(tmp_path)

    assert registry.identifiers == ("earlier", "later")
    assert registry.require("later").order == 20
    with pytest.raises(HomeAssistantThemeError, match="unknown Home Assistant theme"):
        registry.require("missing")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "unsupported.*schema"),
        ("schema_version", True, "must be an integer"),
        ("interface", "web", "cross-interface"),
        ("id", "Bad_ID", "invalid.*identifier"),
        ("label", " ", "nonblank text"),
        ("order", True, "must be an integer"),
        ("order", -1, "must not be negative"),
        ("module", "../card.js", "one local JavaScript filename"),
        ("module", "https://example.test/card.js", "one local JavaScript filename"),
        ("module", "card.css", "one local JavaScript filename"),
        ("custom_element", "Bad Element", "invalid.*custom element"),
        ("installed_filename", "/tmp/card.js", "one local JavaScript filename"),
        ("installed_filename", "other.js", "filenames must match"),
        ("resource_url", "https://example.test/card.js", "exact /local/sds200/ path"),
    ],
)
def test_home_assistant_theme_manifest_rejects_invalid_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    document = _manifest()
    document[field] = value
    _write_theme(tmp_path, document=document)

    with pytest.raises(HomeAssistantThemeError, match=message):
        load_home_assistant_theme_registry(tmp_path)


def test_home_assistant_theme_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    document = _manifest()
    document["unexpected"] = "value"
    _write_theme(tmp_path, document=document)

    with pytest.raises(HomeAssistantThemeError, match="fields do not match"):
        load_home_assistant_theme_registry(tmp_path)


def test_home_assistant_theme_manifest_rejects_directory_identity_mismatch(
    tmp_path: Path,
) -> None:
    _write_theme(
        tmp_path,
        directory_name="renamed",
        document=_manifest("compact"),
    )

    with pytest.raises(HomeAssistantThemeError, match="does not match its directory"):
        load_home_assistant_theme_registry(tmp_path)


def test_home_assistant_theme_manifest_rejects_missing_module(tmp_path: Path) -> None:
    _write_theme(tmp_path, write_module=False)

    with pytest.raises(HomeAssistantThemeError, match="module is missing"):
        load_home_assistant_theme_registry(tmp_path)


def test_home_assistant_theme_manifest_rejects_undeclared_files(tmp_path: Path) -> None:
    directory = _write_theme(tmp_path)
    (directory / "extra.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(HomeAssistantThemeError, match="contains undeclared files"):
        load_home_assistant_theme_registry(tmp_path)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("identifier", "duplicate identities"),
        ("order", "duplicate order values"),
        ("custom_element", "duplicate custom elements"),
        ("installed_filename", "duplicate installed filenames"),
        ("resource_url", "duplicate resource URLs"),
    ],
)
def test_home_assistant_theme_registry_rejects_duplicate_contract_fields(
    field: str,
    message: str,
) -> None:
    first = built_in_home_assistant_theme_registry().themes[0]
    second = built_in_home_assistant_theme_registry().themes[1]
    duplicate = replace(second, **{field: getattr(first, field)})

    with pytest.raises(HomeAssistantThemeError, match=message):
        HomeAssistantThemeRegistry((first, duplicate))


def test_home_assistant_theme_registry_rejects_nondeterministic_order() -> None:
    themes = built_in_home_assistant_theme_registry().themes

    with pytest.raises(HomeAssistantThemeError, match="deterministic order"):
        HomeAssistantThemeRegistry(tuple(reversed(themes)))


def test_home_assistant_theme_registry_rejects_empty_registry() -> None:
    with pytest.raises(HomeAssistantThemeError, match="must not be empty"):
        HomeAssistantThemeRegistry(())


def test_built_in_module_reader_rejects_forged_manifest() -> None:
    theme = built_in_home_assistant_theme_registry().themes[0]
    forged = replace(theme, label="Forged")

    with pytest.raises(HomeAssistantThemeError, match="not a canonical built-in"):
        read_built_in_home_assistant_theme_module(forged)


def test_home_assistant_theme_contract_constants_are_stable() -> None:
    assert HOME_ASSISTANT_THEME_MANIFEST_SCHEMA_VERSION == 1
    assert HOME_ASSISTANT_THEME_INTERFACE == "home-assistant"
