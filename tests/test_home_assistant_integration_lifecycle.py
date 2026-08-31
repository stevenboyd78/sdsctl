from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import ModuleType

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_integration_lifecycle import (
    built_in_home_assistant_integration_image,
    discard_home_assistant_integration_rollback,
    home_assistant_integration_bridge_key_digest,
    inspect_home_assistant_integration,
    install_home_assistant_integration,
    read_home_assistant_integration_bridge_key,
    remove_home_assistant_integration,
    rollback_home_assistant_integration,
    rotate_home_assistant_integration_bridge_key,
)


def _destination(tmp_path: Path) -> Path:
    parent = tmp_path / "homeassistant" / "custom_components"
    parent.mkdir(parents=True)
    return parent / "sdsctl"


def test_packaged_integration_is_versioned_bounded_and_complete() -> None:
    image = built_in_home_assistant_integration_image()

    assert image.version == "0.1.5"
    assert len(image.digest) == 64
    assert image.total_bytes < 512 * 1024
    names = {name for name, _payload in image.files}
    assert {
        "__init__.py",
        "brand/icon.png",
        "brand/logo.png",
        "client.py",
        "config_flow.py",
        "diagnostics.py",
        "http.py",
        "manifest.json",
        "media_source.py",
        "playback.py",
        "sdsctl-logo.svg",
        "translations/en.json",
    } <= names
    assert dict(image.files)["sdsctl-logo.svg"] == (
        Path(__file__).parents[1] / "docs" / "assets" / "sdsctl-logo.svg"
    ).read_bytes()
    icon = dict(image.files)["brand/icon.png"]
    logo = dict(image.files)["brand/logo.png"]
    assert icon.startswith(b"\x89PNG\r\n\x1a\n")
    assert logo.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", icon[16:24]) == (256, 256)
    assert struct.unpack(">II", logo[16:24]) == (1200, 300)
    manifest = json.loads(dict(image.files)["manifest.json"])
    assert manifest["domain"] == "sdsctl"
    assert manifest["version"] == image.version
    assert manifest["single_config_entry"] is True
    assert manifest["dependencies"] == ["http", "media_source"]


def test_first_install_creates_only_the_exact_custom_components_directory(
    tmp_path: Path,
) -> None:
    configuration_root = tmp_path / "homeassistant"
    configuration_root.mkdir()
    destination = configuration_root / "custom_components" / "sdsctl"
    image = built_in_home_assistant_integration_image()

    before = inspect_home_assistant_integration(destination)
    assert before.current_digest is None
    assert not destination.parent.exists()

    installed = install_home_assistant_integration(
        destination,
        confirmation_digest=image.digest,
    )

    assert installed.current_digest == image.digest
    assert destination.is_dir()


def test_install_update_and_rollback_use_exact_digest_and_readback(tmp_path: Path) -> None:
    destination = _destination(tmp_path)
    packaged = built_in_home_assistant_integration_image()

    with pytest.raises(SDS200Error, match="digest confirmation"):
        install_home_assistant_integration(
            destination,
            confirmation_digest="0" * 64,
        )

    installed = install_home_assistant_integration(
        destination,
        confirmation_digest=packaged.digest,
    )
    assert installed.current_version == packaged.version
    assert installed.current_digest == packaged.digest
    assert installed.rollback_digest is None

    (destination / "strings.json").write_text(
        '{"title":"prior local integration"}\n',
        encoding="utf-8",
    )
    prior = inspect_home_assistant_integration(destination)
    assert prior.current_digest != packaged.digest

    updated = install_home_assistant_integration(
        destination,
        confirmation_digest=packaged.digest,
        replace=True,
    )
    assert updated.current_digest == packaged.digest
    assert updated.rollback_digest == prior.current_digest

    rolled_back = rollback_home_assistant_integration(
        destination,
        confirmation_digest=prior.current_digest or "",
    )
    assert rolled_back.current_digest == prior.current_digest
    assert rolled_back.rollback_digest == packaged.digest


def test_inspection_excludes_only_bounded_regular_python_cache_files(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    packaged = built_in_home_assistant_integration_image()
    install_home_assistant_integration(
        destination,
        confirmation_digest=packaged.digest,
    )

    cache = destination / "__pycache__"
    cache.mkdir()
    (cache / "media_source.cpython-314.pyc").write_bytes(b"runtime bytecode")
    inspected = inspect_home_assistant_integration(destination)
    assert inspected.current_digest == packaged.digest

    (cache / "unexpected.txt").write_text("not bytecode", encoding="utf-8")
    with pytest.raises(SDS200Error, match="Python cache contains an unsafe file"):
        inspect_home_assistant_integration(destination)


def test_removal_is_recoverable_then_rollback_can_be_discarded(tmp_path: Path) -> None:
    destination = _destination(tmp_path)
    image = built_in_home_assistant_integration_image()
    install_home_assistant_integration(
        destination,
        confirmation_digest=image.digest,
    )

    removed = remove_home_assistant_integration(
        destination,
        confirmation_digest=image.digest,
    )
    assert removed.current_digest is None
    assert removed.rollback_digest == image.digest

    restored = rollback_home_assistant_integration(
        destination,
        confirmation_digest=image.digest,
    )
    assert restored.current_digest == image.digest
    assert restored.rollback_digest is None

    remove_home_assistant_integration(
        destination,
        confirmation_digest=image.digest,
    )
    discarded = discard_home_assistant_integration_rollback(
        destination,
        confirmation_digest=image.digest,
    )
    assert discarded.current_digest is None
    assert discarded.rollback_digest is None


def test_lifecycle_rejects_symlink_destination(tmp_path: Path) -> None:
    destination = _destination(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    destination.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SDS200Error, match="refuses symlinks"):
        inspect_home_assistant_integration(destination)


def test_explicit_bridge_key_reader_requires_private_regular_file(tmp_path: Path) -> None:
    key = tmp_path / "bridge.key"
    key.write_text("s" * 43 + "\n", encoding="ascii")
    key.chmod(0o600)

    assert read_home_assistant_integration_bridge_key(key) == "s" * 43

    key.chmod(0o644)
    with pytest.raises(SDS200Error, match="not private"):
        read_home_assistant_integration_bridge_key(key)


def test_bridge_key_rotation_requires_current_digest_and_replaces_atomically(
    tmp_path: Path,
) -> None:
    key = tmp_path / "bridge.key"
    key.write_text("s" * 43 + "\n", encoding="ascii")
    key.chmod(0o600)
    digest = home_assistant_integration_bridge_key_digest(key)

    with pytest.raises(SDS200Error, match="digest confirmation"):
        rotate_home_assistant_integration_bridge_key(
            key,
            confirmation_digest="0" * 64,
        )
    assert read_home_assistant_integration_bridge_key(key) == "s" * 43

    replacement = rotate_home_assistant_integration_bridge_key(
        key,
        confirmation_digest=digest,
    )

    assert replacement != "s" * 43
    assert read_home_assistant_integration_bridge_key(key) == replacement
    assert key.stat().st_mode & 0o777 == 0o600
    assert home_assistant_integration_bridge_key_digest(key) != digest


def _load_playback_module() -> ModuleType:
    root = (
        Path(__file__).parents[1]
        / "src"
        / "sds200"
        / "home_assistant_integration"
        / "custom_components"
        / "sdsctl"
    )
    package_name = "_sdsctl_integration_test"
    package = ModuleType(package_name)
    package.__path__ = [str(root)]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    for module_name in ("const", "playback"):
        qualified = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(qualified, root / f"{module_name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{package_name}.playback"]


def test_core_playback_urls_are_one_time_bounded_and_independent() -> None:
    playback = _load_playback_module()
    now = [10.0]
    tokens = iter(("a" * 43, "b" * 43, "c" * 43))
    registry = playback.PlaybackRegistry(
        lifetime=5,
        max_outstanding=2,
        max_active=1,
        clock=lambda: now[0],
        token_factory=lambda: next(tokens),
    )

    first = registry.issue()
    second = registry.issue()
    with pytest.raises(playback.PlaybackUnavailable):
        registry.issue()

    first_lease = registry.redeem(first)
    with pytest.raises(playback.PlaybackUnavailable):
        registry.redeem(first)
    with pytest.raises(playback.PlaybackUnavailable):
        registry.redeem(second)
    first_lease.release()
    second_lease = registry.redeem(second)
    second_lease.release()

    third = registry.issue()
    now[0] = 16.0
    with pytest.raises(playback.PlaybackUnavailable):
        registry.redeem(third)
    snapshot = registry.snapshot()
    assert snapshot.active == 0
    assert snapshot.outstanding == 0
    assert snapshot.redeemed == 2
    assert snapshot.expired == 1


def test_custom_integration_python_sources_compile_without_home_assistant_imports() -> None:
    image = built_in_home_assistant_integration_image()
    for name, payload in image.files:
        if name.endswith(".py"):
            compile(payload, name, "exec")

    sources = "\n".join(
        payload.decode("utf-8")
        for name, payload in image.files
        if name.endswith(".py")
    )
    assert "media-source://sdsctl/live" in dict(image.files)["const.py"].decode()
    assert "SUPERVISOR_TOKEN" not in sources
    assert "async_sign_path" not in dict(image.files)["http.py"].decode()
    assert "async_sign_path" not in dict(image.files)["playback.py"].decode()
    assert "allow_redirects=False" in sources
