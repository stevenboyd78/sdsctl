from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

import sds200.home_assistant_theme_activation as activation
from sds200.cli import build_parser, main
from sds200.configuration import ConfigurationPaths
from sds200.home_assistant_theme_activation import (
    HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME,
    activate_home_assistant_theme,
    deactivate_home_assistant_theme,
    home_assistant_activation_inventory,
)
from sds200.theme_lifecycle import (
    HOME_ASSISTANT_CODE_TRUST_TOKEN,
    ThemeLifecycleError,
    install_theme_package,
    remove_theme_package,
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


def _home_assistant_theme(
    tmp_path: Path,
    identifier: str = "custom-home-assistant",
) -> Path:
    source = tmp_path / identifier
    shutil.copytree(SOURCE_THEME_ROOT / "home-assistant" / "compact", source)
    module = source / f"{identifier}.js"
    (source / "sds200-card.js").rename(module)
    _write_manifest(
        source,
        {
            "id": identifier,
            "label": "Custom Home Assistant",
            "order": 100,
            "module": module.name,
            "custom_element": f"{identifier}-card",
            "installed_filename": module.name,
            "resource_url": f"/local/sds200/{module.name}",
        },
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


def _installed(
    tmp_path: Path,
    *,
    identifier: str = "custom-home-assistant",
) -> tuple[Path, Path, str]:
    root = tmp_path / "managed"
    source = _home_assistant_theme(tmp_path / "source", identifier)
    summary = install_theme_package(
        source,
        root,
        home_assistant_code_trust=HOME_ASSISTANT_CODE_TRUST_TOKEN,
    )
    assert summary.sha256 is not None
    target = tmp_path / "homeassistant" / "www" / "sds200"
    target.mkdir(parents=True)
    return root, target, summary.sha256


def _activate(root: Path, target: Path, digest: str):
    return activate_home_assistant_theme(
        root,
        "custom-home-assistant",
        target,
        confirmed_sha256=digest,
        home_assistant_code_trust=HOME_ASSISTANT_CODE_TRUST_TOKEN,
    )


def test_activation_writes_private_ledger_and_verified_module(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    unrelated = target / "unrelated.js"
    unrelated.write_text("keep", encoding="utf-8")

    record = _activate(root, target, digest)

    assert record.package_sha256 == digest
    assert (
        record.target_path.read_bytes()
        == (root / "home-assistant" / record.identifier / record.installed_filename).read_bytes()
    )
    assert record.target_path.stat().st_mode & 0o777 == 0o644
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    assert ledger.stat().st_mode & 0o777 == 0o600
    assert unrelated.read_text(encoding="utf-8") == "keep"
    inventory = home_assistant_activation_inventory(root)
    assert inventory.valid is True
    assert [status.state for status in inventory.statuses] == ["current"]


def test_activation_deploys_only_authoritative_snapshot_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, target, digest = _installed(tmp_path)
    package = root / "home-assistant" / "custom-home-assistant"
    live_module = package / "custom-home-assistant.js"
    snapshot_module = live_module.read_bytes()
    real_snapshot = activation._validated_open_theme_source_snapshot
    mutated = False

    @contextmanager
    def mutate_live_package_after_snapshot(opened: object):
        nonlocal mutated
        with real_snapshot(opened) as snapshot:  # type: ignore[arg-type]
            assert snapshot.image.sha256 == digest
            live_module.write_bytes(snapshot_module + b"\n// raced live package\n")
            mutated = True
            yield snapshot

    monkeypatch.setattr(
        activation,
        "_validated_open_theme_source_snapshot",
        mutate_live_package_after_snapshot,
    )

    record = _activate(root, target, digest)

    assert mutated
    assert live_module.read_bytes() != snapshot_module
    assert record.package_sha256 == digest
    assert record.module_sha256 == hashlib.sha256(snapshot_module).hexdigest()
    assert record.target_path.read_bytes() == snapshot_module


def test_activation_and_status_reject_symlinked_managed_interface(
    tmp_path: Path,
) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    interface = root / "home-assistant"
    retained_interface = tmp_path / "retained-home-assistant"
    interface.rename(retained_interface)
    try:
        interface.symlink_to(retained_interface, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

    with pytest.raises(ThemeLifecycleError):
        _activate(root, target, digest)

    assert record.target_path.exists()
    inventory = home_assistant_activation_inventory(root)
    assert inventory.valid is True
    assert len(inventory.statuses) == 1
    assert inventory.statuses[0].state == "stale-package"


@pytest.mark.parametrize("trust", [None, "yes"])
def test_activation_requires_exact_executable_code_trust(
    tmp_path: Path,
    trust: str | None,
) -> None:
    root, target, digest = _installed(tmp_path)

    with pytest.raises(ThemeLifecycleError, match="executable-code trust token"):
        activate_home_assistant_theme(
            root,
            "custom-home-assistant",
            target,
            confirmed_sha256=digest,
            home_assistant_code_trust=trust,
        )


def test_activation_requires_exact_current_package_digest(tmp_path: Path) -> None:
    root, target, _digest = _installed(tmp_path)

    with pytest.raises(ThemeLifecycleError, match="exactly match the current package"):
        _activate(root, target, "0" * 64)

    assert list(target.iterdir()) == []
    assert not (root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME).exists()


def test_first_activation_refuses_unrelated_existing_target(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    existing = target / "custom-home-assistant.js"
    existing.write_text("unrelated", encoding="utf-8")

    with pytest.raises(ThemeLifecycleError, match="unrelated existing target"):
        _activate(root, target, digest)

    assert existing.read_text(encoding="utf-8") == "unrelated"


def test_first_activation_accepts_identical_existing_target(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    source_module = root / "home-assistant" / "custom-home-assistant" / "custom-home-assistant.js"
    existing = target / source_module.name
    existing.write_bytes(source_module.read_bytes())

    record = _activate(root, target, digest)

    assert record.target_path == existing
    assert home_assistant_activation_inventory(root).statuses[0].state == "current"

    repeated = _activate(root, target, digest)
    assert repeated == record
    assert home_assistant_activation_inventory(root).statuses[0].state == "current"


def test_same_package_can_be_approved_for_two_exact_targets(tmp_path: Path) -> None:
    root, first_target, digest = _installed(tmp_path)
    second_target = tmp_path / "second-homeassistant" / "www" / "sds200"
    second_target.mkdir(parents=True)

    first = _activate(root, first_target, digest)
    second = activate_home_assistant_theme(
        root,
        first.identifier,
        second_target,
        confirmed_sha256=digest,
        home_assistant_code_trust=HOME_ASSISTANT_CODE_TRUST_TOKEN,
    )

    assert first.target_directory != second.target_directory
    assert [status.state for status in home_assistant_activation_inventory(root).statuses] == [
        "current",
        "current",
    ]


def test_reapproval_updates_only_unchanged_prior_target(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    old = _activate(root, target, digest)
    old_bytes = old.target_path.read_bytes()
    replacement = _home_assistant_theme(tmp_path / "replacement")
    module = replacement / "custom-home-assistant.js"
    module.write_bytes(module.read_bytes() + b"\n// approved update\n")
    summary = install_theme_package(
        replacement,
        root,
        replace=True,
        home_assistant_code_trust=HOME_ASSISTANT_CODE_TRUST_TOKEN,
    )
    assert summary.sha256 is not None
    assert home_assistant_activation_inventory(root).statuses[0].state == "stale-package"

    updated = _activate(root, target, summary.sha256)

    assert updated.target_path.read_bytes() != old_bytes
    assert home_assistant_activation_inventory(root).statuses[0].state == "current"


def test_reapproval_refuses_modified_deployed_target(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    record.target_path.write_text("operator changed this", encoding="utf-8")

    with pytest.raises(ThemeLifecycleError, match="changed after prior activation"):
        _activate(root, target, digest)

    assert record.target_path.read_text(encoding="utf-8") == "operator changed this"
    assert home_assistant_activation_inventory(root).statuses[0].state == "changed-target"


def test_activation_rolls_target_back_when_ledger_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, target, digest = _installed(tmp_path)

    def fail_ledger(_root: Path, _records: object) -> None:
        raise OSError("simulated ledger failure")

    monkeypatch.setattr(activation, "_write_ledger", fail_ledger)
    with pytest.raises(OSError, match="simulated ledger failure"):
        _activate(root, target, digest)

    assert list(target.iterdir()) == []


def test_failed_reapproval_restores_prior_target_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    prior_module = record.target_path.read_bytes()
    prior_ledger = ledger.read_bytes()
    replacement = _home_assistant_theme(tmp_path / "replacement")
    module = replacement / "custom-home-assistant.js"
    module.write_bytes(module.read_bytes() + b"\n// replacement\n")
    summary = install_theme_package(
        replacement,
        root,
        replace=True,
        home_assistant_code_trust=HOME_ASSISTANT_CODE_TRUST_TOKEN,
    )
    assert summary.sha256 is not None

    def fail_ledger(_root: Path, _records: object) -> None:
        raise OSError("simulated ledger failure")

    monkeypatch.setattr(activation, "_write_ledger", fail_ledger)
    with pytest.raises(OSError, match="simulated ledger failure"):
        _activate(root, target, summary.sha256)

    assert record.target_path.read_bytes() == prior_module
    assert ledger.read_bytes() == prior_ledger


def test_deactivation_rolls_target_and_ledger_back_when_ledger_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    original_ledger = ledger.read_bytes()
    original_module = record.target_path.read_bytes()

    def fail_ledger(_root: Path, _records: object) -> None:
        raise OSError("simulated ledger failure")

    monkeypatch.setattr(activation, "_write_ledger", fail_ledger)
    with pytest.raises(OSError, match="simulated ledger failure"):
        deactivate_home_assistant_theme(
            root,
            record.identifier,
            target,
            confirmation=record.identity,
        )

    assert record.target_path.read_bytes() == original_module
    assert ledger.read_bytes() == original_ledger


def test_status_distinguishes_missing_target_and_invalid_ledger(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    record.target_path.unlink()

    assert home_assistant_activation_inventory(root).statuses[0].state == "missing-target"

    (root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME).write_text(
        '{"unknown": true}\n', encoding="utf-8"
    )
    inventory = home_assistant_activation_inventory(root)
    assert inventory.valid is False
    assert inventory.statuses[0].state == "invalid-ledger"


def test_status_accepts_digest_qualified_resource_url(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    document = json.loads(ledger.read_text(encoding="utf-8"))
    document["activations"][0]["resource_url"] = (
        f"/local/sds200/{record.installed_filename}?v={record.module_sha256}"
    )
    ledger.write_text(json.dumps(document) + "\n", encoding="utf-8")

    inventory = home_assistant_activation_inventory(root)

    assert inventory.valid is True
    assert inventory.statuses[0].record is not None
    assert inventory.statuses[0].record.resource_url.endswith(
        f"?v={record.module_sha256}"
    )


def test_status_rejects_mismatched_digest_qualified_resource_url(
    tmp_path: Path,
) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    document = json.loads(ledger.read_text(encoding="utf-8"))
    document["activations"][0]["resource_url"] = (
        f"/local/sds200/{record.installed_filename}?v={'0' * 64}"
    )
    ledger.write_text(json.dumps(document) + "\n", encoding="utf-8")

    inventory = home_assistant_activation_inventory(root)

    assert inventory.valid is False
    assert inventory.statuses[0].state == "invalid-ledger"


def test_status_rejects_public_or_symlinked_ledger(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    _activate(root, target, digest)
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    ledger.chmod(0o644)

    assert home_assistant_activation_inventory(root).statuses[0].state == "invalid-ledger"

    ledger.unlink()
    outside = tmp_path / "outside-ledger.json"
    outside.write_text('{"schema_version": 1, "activations": []}\n', encoding="utf-8")
    ledger.symlink_to(outside)
    assert home_assistant_activation_inventory(root).statuses[0].state == "invalid-ledger"


def test_status_rejects_duplicate_target_filename_in_ledger(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    _activate(root, target, digest)
    ledger = root / HOME_ASSISTANT_ACTIVATION_LEDGER_FILENAME
    document = json.loads(ledger.read_text(encoding="utf-8"))
    duplicate = dict(document["activations"][0])
    duplicate["id"] = "another-card"
    duplicate["custom_element"] = "another-card"
    document["activations"].append(duplicate)
    ledger.write_text(json.dumps(document) + "\n", encoding="utf-8")

    inventory = home_assistant_activation_inventory(root)
    assert inventory.valid is False
    assert "duplicate target filenames" in inventory.statuses[0].message


def test_deactivation_removes_only_exact_module_and_ledger_record(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    unrelated = target / "keep.js"
    unrelated.write_text("keep", encoding="utf-8")

    removed = deactivate_home_assistant_theme(
        root,
        record.identifier,
        target,
        confirmation=record.identity,
    )

    assert removed == record
    assert not record.target_path.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert home_assistant_activation_inventory(root).statuses == ()


def test_deactivation_refuses_changed_or_missing_target(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    record = _activate(root, target, digest)
    record.target_path.write_text("changed", encoding="utf-8")

    with pytest.raises(ThemeLifecycleError, match="changed; refusing"):
        deactivate_home_assistant_theme(
            root,
            record.identifier,
            target,
            confirmation=record.identity,
        )
    assert record.target_path.read_text(encoding="utf-8") == "changed"


def test_active_managed_package_cannot_be_removed(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    _activate(root, target, digest)

    with pytest.raises(ThemeLifecycleError, match="deactivate every target"):
        remove_theme_package(
            root,
            "home-assistant",
            "custom-home-assistant",
            confirmation="home-assistant/custom-home-assistant",
        )


def test_activation_refuses_symlink_target_component(tmp_path: Path) -> None:
    root, _target, digest = _installed(tmp_path)
    real_www = tmp_path / "real" / "www"
    (real_www / "sds200").mkdir(parents=True)
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(tmp_path / "real", target_is_directory=True)

    with pytest.raises(ThemeLifecycleError, match="symlink component"):
        _activate(root, linked_parent / "www" / "sds200", digest)


def test_activation_refuses_relative_builtin_and_special_targets(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)

    with pytest.raises(ThemeLifecycleError, match="must be absolute"):
        _activate(root, Path("www/sds200"), digest)
    with pytest.raises(ThemeLifecycleError, match="owned by the Home Assistant App"):
        activate_home_assistant_theme(
            root,
            "compact",
            target,
            confirmed_sha256=digest,
            home_assistant_code_trust=HOME_ASSISTANT_CODE_TRUST_TOKEN,
        )

    special = target / "custom-home-assistant.js"
    special.mkdir()
    with pytest.raises(ThemeLifecycleError, match="regular file"):
        _activate(root, target, digest)


def test_activation_obeys_lifecycle_lock(tmp_path: Path) -> None:
    root, target, digest = _installed(tmp_path)
    descriptor = os.open(root / ".lifecycle.lock", os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ThemeLifecycleError, match="in progress"):
            _activate(root, target, digest)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_cli_parser_exposes_home_assistant_activation_commands() -> None:
    parser = build_parser()
    activated = parser.parse_args(
        [
            "themes",
            "activate",
            "home-assistant",
            "custom-home-assistant",
            "--target-directory",
            "/homeassistant/www/sds200",
            "--confirm-sha256",
            "a" * 64,
            "--trust-home-assistant-code",
        ]
    )
    deactivated = parser.parse_args(
        [
            "themes",
            "deactivate",
            "home-assistant",
            "custom-home-assistant",
            "--target-directory",
            "/homeassistant/www/sds200",
            "--confirm",
            "home-assistant/custom-home-assistant",
        ]
    )
    statuses = parser.parse_args(["themes", "activations", "--json"])

    assert activated.trust_home_assistant_code is True
    assert deactivated.themes_action == "deactivate"
    assert statuses.themes_action == "activations"


def test_cli_json_activation_status_and_deactivation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _configuration_paths(tmp_path)
    source = _home_assistant_theme(tmp_path / "source")
    target = tmp_path / "homeassistant" / "www" / "sds200"
    target.mkdir(parents=True)
    assert (
        main(
            [
                "themes",
                "install",
                str(source),
                "--trust-home-assistant-code",
                "--json",
            ],
            configuration_paths=paths,
        )
        == 0
    )
    digest = json.loads(capsys.readouterr().out)["package"]["sha256"]

    assert (
        main(
            [
                "themes",
                "activate",
                "home-assistant",
                "custom-home-assistant",
                "--target-directory",
                str(target),
                "--confirm-sha256",
                digest,
                "--trust-home-assistant-code",
                "--json",
            ],
            configuration_paths=paths,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["activated"] is True

    assert main(["themes", "activations", "--json"], configuration_paths=paths) == 0
    assert json.loads(capsys.readouterr().out)["activations"][0]["state"] == "current"

    assert (
        main(
            [
                "themes",
                "deactivate",
                "home-assistant",
                "custom-home-assistant",
                "--target-directory",
                str(target),
                "--confirm",
                "home-assistant/custom-home-assistant",
                "--json",
            ],
            configuration_paths=paths,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["deactivated"] is True
