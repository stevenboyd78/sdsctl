from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_lovelace import (
    HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME,
    HOME_ASSISTANT_LOVELACE_AGGREGATE_RESOURCE_URL,
    install_home_assistant_lovelace_aggregate,
)
from sds200.home_assistant_themes import (
    HomeAssistantThemeError,
    HomeAssistantThemeRegistry,
    built_in_home_assistant_theme_registry,
    read_built_in_home_assistant_card_aggregate_module,
    read_built_in_home_assistant_theme_module,
)


def aggregate_target(tmp_path: Path) -> Path:
    return (
        tmp_path
        / "homeassistant"
        / "www"
        / "sds200"
        / HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME
    )


def aggregate_text() -> str:
    return read_built_in_home_assistant_card_aggregate_module().decode("utf-8")


def test_aggregate_resource_url_uses_exact_module_digest() -> None:
    assert HOME_ASSISTANT_LOVELACE_AGGREGATE_RESOURCE_URL == (
        "/local/sds200/sds200-cards.js?v="
        "efcd8279b998d9b881c9feeb2c0293cf3bfc7f3ae67024f9d6627792db58335f"
    )


def test_aggregate_imports_every_registry_module_in_order() -> None:
    registry = built_in_home_assistant_theme_registry()

    assert aggregate_text().splitlines() == [
        f'import "{theme.resource_url}";' for theme in registry.themes
    ]


def test_aggregate_reader_rejects_imports_that_diverge_from_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packaged = Path("src/sds200/themes/home-assistant")
    copied = tmp_path / "home-assistant"
    shutil.copytree(packaged, copied)
    (copied / HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME).write_text(
        'import "/local/sds200/unreviewed.js";\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("sds200.home_assistant_themes.files", lambda _package: tmp_path)

    with pytest.raises(HomeAssistantThemeError, match="imports do not match"):
        read_built_in_home_assistant_card_aggregate_module()


def test_aggregate_reader_requires_digest_qualified_built_in_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = built_in_home_assistant_theme_registry()
    compact = registry.themes[0]
    unqualified = replace(
        compact,
        resource_url=f"/local/sds200/{compact.installed_filename}",
    )
    forged = HomeAssistantThemeRegistry((unqualified, *registry.themes[1:]))
    monkeypatch.setattr(
        "sds200.home_assistant_themes.built_in_home_assistant_theme_registry",
        lambda: forged,
    )

    with pytest.raises(HomeAssistantThemeError, match="must be digest-qualified"):
        read_built_in_home_assistant_card_aggregate_module()


def test_aggregate_loads_all_cards_and_duplicate_modules_are_idempotent(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for aggregate-card runtime validation.")

    registry = built_in_home_assistant_theme_registry()
    child_urls: dict[str, str] = {}
    for theme in registry.themes:
        path = tmp_path / theme.installed_filename
        path.write_bytes(read_built_in_home_assistant_theme_module(theme))
        child_urls[theme.resource_url] = path.as_uri()
    loader = aggregate_text()
    for resource_url, module_url in child_urls.items():
        loader = loader.replace(resource_url, module_url)
    loader_path = tmp_path / HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME
    loader_path.write_text(loader, encoding="utf-8")

    harness = f"""
globalThis.HTMLElement = class {{}};
globalThis.CustomEvent = class {{}};
const definitions = new Map();
globalThis.customElements = {{
  get: (name) => definitions.get(name),
  define: (name, constructor) => {{
    if (definitions.has(name)) throw new Error(`duplicate ${{name}}`);
    definitions.set(name, constructor);
  }},
}};
globalThis.window = globalThis;
await import({json.dumps(loader_path.as_uri())});
for (const moduleUrl of {json.dumps(list(child_urls.values()))}) {{
  await import(`${{moduleUrl}}#duplicate`);
}}
process.stdout.write(JSON.stringify({{
  elements: [...definitions.keys()],
  cards: window.customCards.map((card) => card.type),
}}));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", harness],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "elements": [
            "sds200-card",
            "sds200-display-card",
            "sds200-waterfall-card",
        ],
        "cards": [
            "sds200-card",
            "sds200-display-card",
            "sds200-waterfall-card",
        ],
    }


def test_aggregate_install_is_atomic_idempotent_and_preserves_unrelated_files(
    tmp_path: Path,
) -> None:
    target = aggregate_target(tmp_path)
    target.parent.mkdir(parents=True)
    unrelated = target.parent / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    target.write_text("stale", encoding="utf-8")

    first = install_home_assistant_lovelace_aggregate(target)
    before = target.stat()
    second = install_home_assistant_lovelace_aggregate(target)
    after = target.stat()

    assert first == second == target
    assert target.read_text(encoding="utf-8") == aggregate_text()
    assert target.stat().st_mode & 0o777 == 0o644
    assert before.st_ino == after.st_ino
    assert before.st_mtime_ns == after.st_mtime_ns
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(target.parent.glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "symlink_part",
    ["www", "sds200", HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME],
)
def test_aggregate_install_refuses_symlink_paths(
    tmp_path: Path,
    symlink_part: str,
) -> None:
    homeassistant = tmp_path / "homeassistant"
    www = homeassistant / "www"
    card_directory = www / "sds200"
    target = card_directory / HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME
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
        outside_file = outside / HOME_ASSISTANT_LOVELACE_AGGREGATE_FILENAME
        outside_file.write_text("outside", encoding="utf-8")
        target.symlink_to(outside_file)

    with pytest.raises(SDS200Error, match="refuses symlinks"):
        install_home_assistant_lovelace_aggregate(target)


def test_aggregate_install_rejects_wrong_destination() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        install_home_assistant_lovelace_aggregate(
            Path("www/sds200/sds200-cards.js")
        )
    with pytest.raises(ValueError, match="must use"):
        install_home_assistant_lovelace_aggregate(
            Path("/tmp/sds200-card.js")
        )
