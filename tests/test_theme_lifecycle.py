from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

import sds200.theme_lifecycle as lifecycle
from sds200.cli import build_parser, main
from sds200.configuration import ConfigurationPaths
from sds200.theme_lifecycle import (
    HOME_ASSISTANT_CODE_TRUST_TOKEN,
    THEME_INTERFACES,
    THEME_PACKAGE_MAX_BYTES,
    ThemeLifecycleError,
    discover_theme_inventory,
    install_theme_package,
    remove_theme_package,
    validate_theme_package,
)

SOURCE_THEME_ROOT = Path(__file__).parents[1] / "src" / "sds200" / "themes"


def _write_manifest(path: Path, updates: dict[str, object]) -> None:
    manifest_path = path / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document.update(updates)
    manifest_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _web_theme(tmp_path: Path, identifier: str = "custom-web") -> Path:
    source = tmp_path / identifier
    shutil.copytree(SOURCE_THEME_ROOT / "web" / "system", source)
    _write_manifest(
        source,
        {"id": identifier, "label": "Custom Web", "order": 100},
    )
    return source


def _home_assistant_theme(
    tmp_path: Path,
    identifier: str = "custom-home-assistant",
) -> Path:
    source = tmp_path / identifier
    shutil.copytree(SOURCE_THEME_ROOT / "home-assistant" / "compact", source)
    old_module = source / "sds200-card.js"
    new_module = source / f"{identifier}.js"
    old_module.rename(new_module)
    _write_manifest(
        source,
        {
            "id": identifier,
            "label": "Custom Home Assistant",
            "order": 100,
            "module": new_module.name,
            "custom_element": f"{identifier}-card",
            "installed_filename": new_module.name,
            "resource_url": f"/local/sds200/{new_module.name}",
        },
    )
    return source


def _tui_theme(tmp_path: Path, identifier: str = "custom-tui") -> Path:
    source = tmp_path / identifier
    shutil.copytree(SOURCE_THEME_ROOT / "tui" / "dark", source)
    _write_manifest(
        source,
        {
            "id": identifier,
            "label": "Custom TUI",
            "order": 100,
            "palette_name": f"{identifier}-palette",
            "screen_class": identifier,
        },
    )
    palette_path = source / "palette.json"
    palette = json.loads(palette_path.read_text(encoding="utf-8"))
    palette["name"] = f"{identifier}-palette"
    palette_path.write_text(
        json.dumps(palette, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return source


def _configuration_paths(tmp_path: Path) -> ConfigurationPaths:
    return ConfigurationPaths(
        system_config_dir=tmp_path / "etc" / "sdsctl",
        user_config_dir=tmp_path / "config" / "sdsctl",
        user_state_dir=tmp_path / "state" / "sdsctl",
        user_cache_dir=tmp_path / "cache" / "sdsctl",
        legacy_user_config_dir=tmp_path / "config" / "sds200",
    )


def test_configuration_paths_exposes_managed_theme_root(tmp_path: Path) -> None:
    paths = _configuration_paths(tmp_path)

    assert paths.theme_dir == tmp_path / "config" / "sdsctl" / "themes"


@pytest.mark.parametrize(
    ("factory", "interface"),
    [
        (_web_theme, "web"),
        (_home_assistant_theme, "home-assistant"),
        (_tui_theme, "tui"),
    ],
)
def test_validate_theme_package_reuses_each_interface_contract(
    tmp_path: Path,
    factory: object,
    interface: str,
) -> None:
    source = factory(tmp_path)  # type: ignore[operator]

    package = validate_theme_package(source)

    assert package.summary.interface == interface
    assert package.summary.identifier == source.name
    assert package.summary.origin == "managed"
    assert package.summary.path == source.absolute()
    assert package.summary.sha256 is not None
    assert package.summary.executable is (interface == "home-assistant")


def test_validate_rejects_symlink_and_special_file_entries(tmp_path: Path) -> None:
    symlink_theme = _web_theme(tmp_path / "symlink")
    (symlink_theme / "theme.css").unlink()
    (symlink_theme / "theme.css").symlink_to(tmp_path / "outside.css")
    with pytest.raises(ThemeLifecycleError, match="symlinks"):
        validate_theme_package(symlink_theme)

    fifo_theme = _web_theme(tmp_path / "fifo")
    os.mkfifo(fifo_theme / "extra")
    with pytest.raises(ThemeLifecycleError, match="regular top-level files"):
        validate_theme_package(fifo_theme)


def test_validate_rejects_package_size_limit(tmp_path: Path) -> None:
    source = _web_theme(tmp_path)
    (source / "theme.css").write_bytes(b"x" * (THEME_PACKAGE_MAX_BYTES + 1))

    with pytest.raises(ThemeLifecycleError, match="byte limit"):
        validate_theme_package(source)


def test_validate_rejects_package_file_count_limit(tmp_path: Path) -> None:
    source = _web_theme(tmp_path)
    for index in range(7):
        (source / f"extra-{index}.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(ThemeLifecycleError, match="file limit"):
        validate_theme_package(source)


def test_absent_inventory_is_read_only_and_always_lists_built_ins(
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed" / "themes"

    inventory = discover_theme_inventory(root)

    assert not root.exists()
    assert inventory.issues == ()
    assert tuple(package.identity for package in inventory.packages) == (
        "web/system",
        "web/lcars",
        "web/matrix",
        "web/first-responder",
        "web/amateur-radio",
        "web/pip-boy-inspired",
        "home-assistant/compact",
        "home-assistant/sds200-display",
        "tui/dark",
        "tui/light",
    )
    assert all(package.origin == "built-in" for package in inventory.packages)


def test_inventory_discovers_all_interfaces_and_isolates_invalid_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    for factory, interface in (
        (_web_theme, "web"),
        (_home_assistant_theme, "home-assistant"),
        (_tui_theme, "tui"),
    ):
        source = factory(tmp_path / f"source-{interface}")
        destination = root / interface / source.name
        destination.parent.mkdir(parents=True)
        shutil.copytree(source, destination)
    invalid = root / "web" / "broken"
    invalid.mkdir()
    (invalid / "manifest.json").write_text("not json", encoding="utf-8")
    unknown = root / "future-gui"
    unknown.mkdir()

    inventory = discover_theme_inventory(root)

    managed = tuple(
        package.identity for package in inventory.packages if package.origin == "managed"
    )
    assert managed == (
        "web/custom-web",
        "home-assistant/custom-home-assistant",
        "tui/custom-tui",
    )
    assert len(inventory.issues) == 2
    assert {issue.path for issue in inventory.issues} == {invalid, unknown}


def test_inventory_rejects_built_in_identity_and_registry_collisions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    shadow = _web_theme(tmp_path / "shadow", "system")
    order_collision = _web_theme(tmp_path / "order", "order-collision")
    _write_manifest(order_collision, {"order": 0})
    for source in (shadow, order_collision):
        destination = root / "web" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)

    inventory = discover_theme_inventory(root)

    assert not any(package.origin == "managed" for package in inventory.packages)
    assert len(inventory.issues) == 2
    assert any("built-in identity" in issue.message for issue in inventory.issues)
    assert any("duplicate order" in issue.message for issue in inventory.issues)


def test_inventory_isolates_interface_specific_asset_collisions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    home_assistant = _home_assistant_theme(tmp_path / "home-assistant-source")
    _write_manifest(home_assistant, {"custom_element": "sds200-card"})
    home_assistant_target = root / "home-assistant" / home_assistant.name
    home_assistant_target.parent.mkdir(parents=True)
    shutil.copytree(home_assistant, home_assistant_target)

    tui = _tui_theme(tmp_path / "tui-source")
    _write_manifest(tui, {"palette_name": "default-dark"})
    palette_path = tui / "palette.json"
    palette = json.loads(palette_path.read_text(encoding="utf-8"))
    palette["name"] = "default-dark"
    palette_path.write_text(
        json.dumps(palette, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tui_target = root / "tui" / tui.name
    tui_target.parent.mkdir(parents=True)
    shutil.copytree(tui, tui_target)

    inventory = discover_theme_inventory(root)

    assert len(inventory.issues) == 2
    assert any("duplicate custom elements" in issue.message for issue in inventory.issues)
    assert any("duplicate palette names" in issue.message for issue in inventory.issues)


def test_install_uses_private_modes_and_makes_package_discoverable(
    tmp_path: Path,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "config" / "themes"

    installed = install_theme_package(source, root)

    assert installed.path == root / "web" / "custom-web"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "web").stat().st_mode) == 0o700
    assert stat.S_IMODE(installed.path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in installed.path.iterdir())
    inventory = discover_theme_inventory(root)
    assert "web/custom-web" in {package.identity for package in inventory.packages}
    assert inventory.issues == ()
    assert not any(path.name.startswith(".sdsctl-") for path in (root / "web").iterdir())


def test_install_requires_explicit_replace_and_replaces_atomically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    first = _web_theme(tmp_path / "first")
    install_theme_package(first, root)
    replacement = _web_theme(tmp_path / "second")
    _write_manifest(replacement, {"label": "Replacement Web"})

    with pytest.raises(ThemeLifecycleError, match="explicit replacement"):
        install_theme_package(replacement, root)

    installed = install_theme_package(replacement, root, replace=True)

    assert installed.label == "Replacement Web"
    validated = validate_theme_package(root / "web" / "custom-web")
    assert validated.summary.label == "Replacement Web"
    assert not any(path.name.startswith(".sdsctl-") for path in (root / "web").iterdir())


def test_replace_rolls_back_when_activation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    first = _web_theme(tmp_path / "first")
    install_theme_package(first, root)
    replacement = _web_theme(tmp_path / "second")
    _write_manifest(replacement, {"label": "Replacement Web"})
    target = root / "web" / "custom-web"
    real_replace = lifecycle.os.replace

    def fail_stage_activation(source: object, destination: object) -> None:
        source_path = Path(source)  # type: ignore[arg-type]
        destination_path = Path(destination)  # type: ignore[arg-type]
        if source_path.parent.name.startswith(".sdsctl-stage-") and destination_path == target:
            raise OSError("injected activation failure")
        real_replace(source, destination)

    monkeypatch.setattr(lifecycle.os, "replace", fail_stage_activation)

    with pytest.raises(OSError, match="injected activation failure"):
        install_theme_package(replacement, root, replace=True)

    restored = validate_theme_package(target)
    assert restored.summary.label == "Custom Web"
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_install_recovers_interrupted_rollback_before_next_operation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    original = _web_theme(tmp_path / "original")
    install_theme_package(original, root)
    target = root / "web" / "custom-web"
    rollback = root / "web" / ".sdsctl-rollback-custom-web"
    target.rename(rollback)

    second = _web_theme(tmp_path / "second", "another-web")
    _write_manifest(second, {"order": 101})
    install_theme_package(second, root)

    assert target.is_dir()
    assert not rollback.exists()
    assert validate_theme_package(target).summary.label == "Custom Web"


def test_install_rejects_source_inside_managed_root(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    source = _web_theme(root / "incoming")

    with pytest.raises(ThemeLifecycleError, match="outside"):
        install_theme_package(source, root)


def test_home_assistant_install_requires_explicit_code_trust(tmp_path: Path) -> None:
    source = _home_assistant_theme(tmp_path / "source")
    root = tmp_path / "themes"

    with pytest.raises(ThemeLifecycleError, match="executable-code"):
        install_theme_package(source, root)

    installed = install_theme_package(
        source,
        root,
        home_assistant_code_trust=HOME_ASSISTANT_CODE_TRUST_TOKEN,
    )
    assert installed.executable is True


def test_remove_requires_exact_confirmation_and_refuses_built_ins(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    source = _tui_theme(tmp_path / "source")
    install_theme_package(source, root)

    with pytest.raises(ThemeLifecycleError, match="exactly match"):
        remove_theme_package(root, "tui", "custom-tui", confirmation="yes")
    with pytest.raises(ThemeLifecycleError, match="built-in"):
        remove_theme_package(root, "tui", "dark", confirmation="tui/dark")

    removed = remove_theme_package(
        root,
        "tui",
        "custom-tui",
        confirmation="tui/custom-tui",
    )

    assert removed.identity == "tui/custom-tui"
    assert not (root / "tui" / "custom-tui").exists()
    assert not any(path.name.startswith(".sdsctl-") for path in (root / "tui").iterdir())


def test_remove_provides_recovery_for_invalid_managed_package(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    invalid = root / "web" / "broken-theme"
    invalid.mkdir(parents=True)
    (invalid / "manifest.json").write_text("broken", encoding="utf-8")
    assert discover_theme_inventory(root).issues

    remove_theme_package(
        root,
        "web",
        "broken-theme",
        confirmation="web/broken-theme",
    )

    assert not invalid.exists()
    assert discover_theme_inventory(root).issues == ()


def test_remove_restores_target_when_tombstone_deletion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    install_theme_package(source, root)
    target = root / "web" / "custom-web"
    real_remove = lifecycle._remove_private_tree

    def fail_tombstone(path: Path) -> None:
        if path.name.startswith(".sdsctl-remove-") and path.exists():
            raise OSError("injected tombstone failure")
        real_remove(path)

    monkeypatch.setattr(lifecycle, "_remove_private_tree", fail_tombstone)

    with pytest.raises(OSError, match="injected tombstone failure"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert target.is_dir()
    assert validate_theme_package(target).summary.identifier == "custom-web"


def test_concurrent_lifecycle_operation_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    root.mkdir(parents=True)
    lock_path = root / ".lifecycle.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ThemeLifecycleError, match="in progress"):
            install_theme_package(_web_theme(tmp_path / "source"), root)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_cli_parser_exposes_theme_lifecycle_commands() -> None:
    parser = build_parser()

    listed = parser.parse_args(["themes", "list", "--json"])
    installed = parser.parse_args(
        ["themes", "install", "package", "--replace", "--trust-home-assistant-code"]
    )
    removed = parser.parse_args(
        ["themes", "remove", "web", "custom-web", "--confirm", "web/custom-web"]
    )

    assert listed.themes_action == "list"
    assert installed.replace is True
    assert installed.trust_home_assistant_code is True
    assert removed.interface == "web"
    assert tuple(THEME_INTERFACES) == ("web", "home-assistant", "tui")


def test_cli_json_install_list_validate_and_remove(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configuration_paths(tmp_path)
    source = _web_theme(tmp_path / "source")

    assert main(["themes", "validate", str(source), "--json"], configuration_paths=paths) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["valid"] is True
    assert validated["package"]["identity"] == "web/custom-web"

    assert main(["themes", "install", str(source), "--json"], configuration_paths=paths) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed["installed"] is True
    assert installed["activated"] is False

    assert main(["themes", "list", "--json"], configuration_paths=paths) == 0
    inventory = json.loads(capsys.readouterr().out)
    assert inventory["root"] == str(paths.theme_dir)
    assert "web/custom-web" in {package["identity"] for package in inventory["packages"]}

    assert (
        main(
            [
                "themes",
                "remove",
                "web",
                "custom-web",
                "--confirm",
                "web/custom-web",
                "--json",
            ],
            configuration_paths=paths,
        )
        == 0
    )
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed"] is True


def test_cli_list_returns_one_when_invalid_entries_are_present(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configuration_paths(tmp_path)
    invalid = paths.theme_dir / "web" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "manifest.json").write_text("broken", encoding="utf-8")

    assert main(["themes", "list", "--json"], configuration_paths=paths) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["issues"][0]["identity"] == "web/broken"


def test_cli_rejects_scanner_options_for_theme_lifecycle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configuration_paths(tmp_path)

    assert main(["--host", "scanner", "themes", "list"], configuration_paths=paths) == 2
    assert "not used with themes" in capsys.readouterr().err
