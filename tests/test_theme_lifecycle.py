from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

import sds200.theme_lifecycle as lifecycle
from sds200.cli import build_parser, main
from sds200.configuration import ConfigurationPaths
from sds200.exceptions import SDS200Error
from sds200.theme_lifecycle import (
    HOME_ASSISTANT_CODE_TRUST_TOKEN,
    THEME_INTERFACES,
    THEME_PACKAGE_MAX_BYTES,
    THEME_PACKAGE_MAX_FILES,
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


class _MutationStream:
    """Trigger one deterministic source mutation immediately before reading."""

    def __init__(
        self,
        stream: object,
        *,
        before_read: Callable[[], None],
        bytes_read: list[int],
        read_limit: int | None,
        source: Path,
    ) -> None:
        self._stream = stream
        self._before_read = before_read
        self._bytes_read = bytes_read
        self._read_limit = read_limit
        self._source = source

    def __enter__(self) -> _MutationStream:
        self._stream.__enter__()  # type: ignore[attr-defined]
        return self

    def __exit__(self, *arguments: object) -> object:
        return self._stream.__exit__(*arguments)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)

    def read(self, size: int = -1) -> object:
        self._before_read()
        if self._read_limit is not None and size < 0:
            projected = self._bytes_read[0] + self._source.stat().st_size
            if projected > self._read_limit:
                raise AssertionError("theme source was read past its aggregate byte budget")
        result = self._stream.read(size)  # type: ignore[attr-defined]
        if isinstance(result, (bytes, str)):
            self._bytes_read[0] += len(result)
            if self._read_limit is not None and self._bytes_read[0] > self._read_limit:
                raise AssertionError("theme source was read past its aggregate byte budget")
        return result


def _post_inspection_mutations(
    monkeypatch: pytest.MonkeyPatch,
    mutations: Mapping[Path, Callable[[], None]],
    *,
    read_limit: int | None = None,
    mutate_on_fstat: bool = True,
) -> dict[str, object]:
    """Mutate opened source files after their first inspection and before read."""

    targets = {path.absolute(): mutation for path, mutation in mutations.items()}
    triggered = {path: False for path in targets}
    bytes_read = [0]
    descriptor_targets: dict[int, Path] = {}
    real_path_open = Path.open
    real_os_open = lifecycle.os.open
    real_os_read = lifecycle.os.read
    real_os_fstat = lifecycle.os.fstat
    real_os_close = lifecycle.os.close

    def trigger(path: Path) -> None:
        if triggered[path]:
            return
        triggered[path] = True
        targets[path]()

    def path_open(path: Path, *args: object, **kwargs: object) -> object:
        stream = real_path_open(path, *args, **kwargs)  # type: ignore[arg-type]
        target = path.absolute()
        if target not in targets or triggered[target]:
            return stream
        return _MutationStream(
            stream,
            before_read=lambda: trigger(target),
            bytes_read=bytes_read,
            read_limit=read_limit,
            source=target,
        )

    def os_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = real_os_open(  # type: ignore[call-overload]
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )
        opened = real_os_fstat(descriptor)
        for target in targets:
            if triggered[target]:
                continue
            try:
                candidate = target.lstat()
            except OSError:
                continue
            if (opened.st_dev, opened.st_ino) == (
                candidate.st_dev,
                candidate.st_ino,
            ):
                descriptor_targets[descriptor] = target
                break
        return descriptor

    def os_fstat(descriptor: int) -> os.stat_result:
        result = real_os_fstat(descriptor)
        target = descriptor_targets.get(descriptor)
        if target is not None and mutate_on_fstat:
            trigger(target)
        return result

    def os_read(descriptor: int, size: int) -> bytes:
        target = descriptor_targets.get(descriptor)
        if target is not None:
            trigger(target)
        result = real_os_read(descriptor, size)
        if target is not None:
            bytes_read[0] += len(result)
            if read_limit is not None and bytes_read[0] > read_limit:
                raise AssertionError("theme source was read past its aggregate byte budget")
        return result

    def os_close(descriptor: int) -> None:
        descriptor_targets.pop(descriptor, None)
        real_os_close(descriptor)

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(lifecycle.os, "open", os_open)
    monkeypatch.setattr(lifecycle.os, "fstat", os_fstat)
    monkeypatch.setattr(lifecycle.os, "read", os_read)
    monkeypatch.setattr(lifecycle.os, "close", os_close)
    return {
        "bytes_read": bytes_read,
        "triggered": triggered,
    }


def _pre_open_substitution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: Path,
    target: Path,
    substitute: Callable[[], None],
    refuse_symlink_follow: bool = False,
) -> dict[str, bool]:
    """Substitute one inventoried source entry immediately before it is opened."""

    source = source.absolute()
    target = target.absolute()
    state = {"triggered": False, "unsafe_follow_attempt": False}
    source_descriptors: set[int] = set()
    real_path_open = Path.open
    real_os_open = lifecycle.os.open
    real_os_fstat = lifecycle.os.fstat
    real_os_close = lifecycle.os.close

    def trigger() -> None:
        if state["triggered"]:
            return
        state["triggered"] = True
        substitute()

    def reject_unsafe_symlink_follow(flags: int | None = None) -> None:
        if not refuse_symlink_follow or not target.is_symlink():
            return
        if flags is None or not flags & getattr(os, "O_NOFOLLOW", 0):
            state["unsafe_follow_attempt"] = True
            raise AssertionError("theme validation attempted to follow a substituted symlink")

    def path_open(path: Path, *args: object, **kwargs: object) -> object:
        if path.absolute() == target:
            trigger()
            reject_unsafe_symlink_follow()
        return real_path_open(path, *args, **kwargs)  # type: ignore[arg-type]

    def os_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        try:
            requested = Path(os.fsdecode(path))  # type: ignore[arg-type]
        except TypeError:
            requested = Path()
        is_target = (requested.is_absolute() and requested.absolute() == target) or (
            not requested.is_absolute()
            and requested.name == target.name
            and dir_fd in source_descriptors
        )
        if is_target:
            trigger()
            reject_unsafe_symlink_follow(flags)
        descriptor = real_os_open(  # type: ignore[call-overload]
            path,
            flags,
            mode,
            dir_fd=dir_fd,
        )
        opened = real_os_fstat(descriptor)
        try:
            source_status = source.lstat()
        except OSError:
            source_status = None
        if source_status is not None and (opened.st_dev, opened.st_ino) == (
            source_status.st_dev,
            source_status.st_ino,
        ):
            source_descriptors.add(descriptor)
        return descriptor

    def os_close(descriptor: int) -> None:
        source_descriptors.discard(descriptor)
        real_os_close(descriptor)

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(lifecycle.os, "open", os_open)
    monkeypatch.setattr(lifecycle.os, "close", os_close)
    return state


def _rewrite_same_size(path: Path, replacement: bytes) -> Callable[[], None]:
    original_status = path.stat()
    assert len(replacement) == original_status.st_size

    def rewrite() -> None:
        path.write_bytes(replacement)
        os.utime(
            path,
            ns=(
                original_status.st_atime_ns,
                original_status.st_mtime_ns + 1_000_000_000,
            ),
        )

    return rewrite


def _assert_no_publication(root: Path, interface: str, identifier: str) -> None:
    target = root / interface / identifier
    assert not target.exists()
    if target.parent.exists():
        assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def _is_configured_entry(candidate: Path, configured: Path) -> bool:
    """Match a retained descriptor path to its configured directory entry."""

    if candidate.name != configured.name:
        return False
    try:
        candidate_parent = candidate.parent.stat()
        configured_parent = configured.parent.stat()
    except OSError:
        return False
    return (candidate_parent.st_dev, candidate_parent.st_ino) == (
        configured_parent.st_dev,
        configured_parent.st_ino,
    )


def _is_retained_stage_entry(candidate: Path, configured_interface_root: Path) -> bool:
    """Recognize a package entry beneath a retained randomized stage descriptor."""

    try:
        candidate_parent = candidate.parent.stat()
        artifacts = tuple(configured_interface_root.iterdir())
    except OSError:
        return False
    for artifact in artifacts:
        if not artifact.name.startswith(".sdsctl-stage-"):
            continue
        try:
            status = artifact.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(status.st_mode) and (
            status.st_dev,
            status.st_ino,
        ) == (
            candidate_parent.st_dev,
            candidate_parent.st_ino,
        ):
            return True
    return False


def _is_tokenized_artifact(name: str, prefix: str) -> bool:
    token = name.removeprefix(prefix)
    return (
        name.startswith(prefix)
        and len(token) == 32
        and all(character in "0123456789abcdef" for character in token)
    )


def _swap_tokenized_directory_after_mkdir(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prefix: str,
    foreign: Path,
    displaced: Path,
) -> dict[str, Path | None]:
    """Replace one freshly created randomized directory after the real mkdir."""

    real_mkdir = lifecycle.os.mkdir
    state: dict[str, Path | None] = {"entry": None}

    def swap_after_mkdir(
        path: object,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)  # type: ignore[arg-type]
        name = os.fsdecode(path)  # type: ignore[arg-type]
        if state["entry"] is not None or dir_fd is None or not _is_tokenized_artifact(name, prefix):
            return
        entry = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / name
        entry.rename(displaced)
        foreign.rename(entry)
        state["entry"] = entry

    monkeypatch.setattr(lifecycle.os, "mkdir", swap_after_mkdir)
    return state


def _resize_web_package_total(source: Path, total_bytes: int) -> None:
    manifest = source / "manifest.json"
    stylesheet = source / "theme.css"
    manifest_content = manifest.read_bytes()
    stylesheet_content = stylesheet.read_bytes()
    manifest_size = total_bytes // 2
    stylesheet_size = total_bytes - manifest_size
    assert len(manifest_content) <= manifest_size
    assert len(stylesheet_content) <= stylesheet_size
    manifest.write_bytes(manifest_content + b" " * (manifest_size - len(manifest_content)))
    stylesheet.write_bytes(stylesheet_content + b" " * (stylesheet_size - len(stylesheet_content)))
    assert sum(path.stat().st_size for path in source.iterdir()) == total_bytes


def _independent_package_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.iterdir(), key=lambda candidate: candidate.name):
        name = path.name.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


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


def test_install_rejects_symlink_substitution_without_reading_external_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        pytest.skip("descriptor-relative O_NOFOLLOW is unavailable")

    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    stylesheet = source / "theme.css"
    external = tmp_path / "private-external.css"
    external.write_text("PRIVATE EXTERNAL THEME MATERIAL", encoding="utf-8")

    def substitute() -> None:
        stylesheet.unlink()
        try:
            stylesheet.symlink_to(external)
        except OSError as error:
            pytest.skip(f"symbolic links are unavailable: {error}")

    state = _pre_open_substitution(
        monkeypatch,
        source=source,
        target=stylesheet,
        substitute=substitute,
        refuse_symlink_follow=True,
    )

    with pytest.raises((ThemeLifecycleError, AssertionError)) as captured:
        install_theme_package(source, root)

    assert isinstance(captured.value, ThemeLifecycleError)
    assert state == {"triggered": True, "unsafe_follow_attempt": False}
    assert external.read_text(encoding="utf-8") == "PRIVATE EXTERNAL THEME MATERIAL"
    _assert_no_publication(root, "web", "custom-web")


def test_install_rejects_regular_file_replacement_after_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    stylesheet = source / "theme.css"
    original = stylesheet.read_bytes()
    replacement_bytes = original.replace(b"#eef2f7", b"#eef2f6", 1)
    assert replacement_bytes != original
    assert len(replacement_bytes) == len(original)
    replacement = tmp_path / "replacement.css"
    replacement.write_bytes(replacement_bytes)

    state = _pre_open_substitution(
        monkeypatch,
        source=source,
        target=stylesheet,
        substitute=lambda: os.replace(replacement, stylesheet),
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert state["triggered"] is True
    _assert_no_publication(root, "web", "custom-web")


def test_install_rejects_source_directory_replacement_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    replacement = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement, {"label": "Substituted Web"})
    root = tmp_path / "themes"
    saved_source = source.with_name("validated-source")
    real_lifecycle_lock = lifecycle._lifecycle_lock
    swapped = False

    @contextmanager
    def swap_after_validation(managed_root: Path):
        nonlocal swapped
        with real_lifecycle_lock(managed_root) as opened_root:
            source.rename(saved_source)
            replacement.rename(source)
            swapped = True
            yield opened_root

    monkeypatch.setattr(lifecycle, "_lifecycle_lock", swap_after_validation)

    with pytest.raises(ThemeLifecycleError, match="match|changed"):
        install_theme_package(source, root)

    assert swapped
    saved_manifest = json.loads((saved_source / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["label"] == "Custom Web"
    _assert_no_publication(root, "web", "custom-web")


def test_install_rejects_source_root_symlink_substitution_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source").absolute()
    root = tmp_path / "themes"
    saved_source = source.with_name("saved-custom-web")
    external = tmp_path / "external-source"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("external material", encoding="utf-8")
    source_parent_status = source.parent.stat()
    real_open = lifecycle.os.open
    real_fstat = lifecycle.os.fstat
    substituted = False

    def substitute_before_component_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal substituted
        if not substituted and dir_fd is not None:
            try:
                requested = Path(os.fsdecode(path))  # type: ignore[arg-type]
            except TypeError:
                requested = Path()
            parent_status = real_fstat(dir_fd)
            if requested == Path(source.name) and (
                parent_status.st_dev,
                parent_status.st_ino,
            ) == (
                source_parent_status.st_dev,
                source_parent_status.st_ino,
            ):
                source.rename(saved_source)
                try:
                    source.symlink_to(external, target_is_directory=True)
                except OSError as error:
                    pytest.skip(f"symbolic links are unavailable: {error}")
                substituted = True
        return real_open(path, flags, mode, dir_fd=dir_fd)  # type: ignore[call-overload]

    monkeypatch.setattr(lifecycle.os, "open", substitute_before_component_open)

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert substituted
    assert saved_source.is_dir()
    assert source.is_symlink()
    assert marker.read_text(encoding="utf-8") == "external material"
    _assert_no_publication(root, "web", "custom-web")


def test_install_allows_symlinked_trusted_source_parent(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-source-parent"
    source = _web_theme(real_parent)
    linked_parent = tmp_path / "linked-source-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")
    root = tmp_path / "themes"

    linked_source = linked_parent / source.name
    expected_digest = _independent_package_digest(source)

    installed = install_theme_package(linked_source, root)

    assert installed.sha256 == expected_digest
    assert _independent_package_digest(root / "web" / "custom-web") == expected_digest


def test_install_rejects_private_snapshot_directory_symlink_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    external = tmp_path / "external-snapshot"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("external material", encoding="utf-8")
    real_copy = lifecycle._copy_open_package_without_links
    state: dict[str, object] = {"substituted": False}

    def substitute_private_snapshot(
        opened: lifecycle._OpenedPackageDirectory,
        inventory: lifecycle._PackageInventory,
        destination: Path,
    ) -> lifecycle._PackageImage:
        image = real_copy(opened, inventory, destination)
        if not state["substituted"] and destination.parent.name.startswith(
            "sdsctl-theme-snapshot-"
        ):
            retained = destination.with_name(f".retained-{destination.name}")
            destination.rename(retained)
            try:
                destination.symlink_to(external, target_is_directory=True)
            except OSError as error:
                pytest.skip(f"symbolic links are unavailable: {error}")
            state.update(
                substituted=True,
                temporary_root=destination.parent,
            )
        return image

    monkeypatch.setattr(
        lifecycle,
        "_copy_open_package_without_links",
        substitute_private_snapshot,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert state["substituted"] is True
    temporary_root = state["temporary_root"]
    assert isinstance(temporary_root, Path)
    assert not temporary_root.exists()
    assert marker.read_text(encoding="utf-8") == "external material"
    _assert_no_publication(root, "web", "custom-web")


def test_install_rejects_publication_stage_directory_symlink_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    external = tmp_path / "external-stage"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("external material", encoding="utf-8")
    real_copy = lifecycle._copy_package_without_links
    state: dict[str, object] = {"substituted": False}

    def substitute_publication_stage(
        snapshot: Path,
        destination: Path,
        *,
        destination_parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> lifecycle._PackageImage:
        image = real_copy(
            snapshot,
            destination,
            destination_parent=destination_parent,
        )
        assert destination_parent is not None
        retained = destination.with_name(f".retained-{destination.name}")
        destination.rename(retained)
        try:
            destination.symlink_to(external, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symbolic links are unavailable: {error}")
        state.update(
            substituted=True,
            stage_container=root / "web" / destination_parent.path.name,
        )
        return image

    monkeypatch.setattr(
        lifecycle,
        "_copy_package_without_links",
        substitute_publication_stage,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert state["substituted"] is True
    stage_container = state["stage_container"]
    assert isinstance(stage_container, Path)
    assert not stage_container.exists()
    assert marker.read_text(encoding="utf-8") == "external material"
    _assert_no_publication(root, "web", "custom-web")


def test_install_rejects_source_truncation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    stylesheet = source / "theme.css"
    original_size = stylesheet.stat().st_size

    def truncate() -> None:
        with stylesheet.open("r+b") as stream:
            stream.truncate(original_size // 2)

    state = _post_inspection_mutations(
        monkeypatch,
        {stylesheet: truncate},
        mutate_on_fstat=False,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert state["triggered"] == {stylesheet.absolute(): True}
    assert stylesheet.stat().st_size == original_size // 2
    _assert_no_publication(root, "web", "custom-web")


def test_install_preserves_exact_bytes_and_digest_across_repeated_short_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    expected_digest = _independent_package_digest(source)
    expected_files = {
        path.name: path.read_bytes()
        for path in sorted(source.iterdir(), key=lambda candidate: candidate.name)
    }
    identities = {
        (status.st_dev, status.st_ino): path.name
        for path in source.iterdir()
        for status in (path.stat(),)
    }
    read_counts = {name: 0 for name in expected_files}
    real_read = lifecycle.os.read
    real_fstat = lifecycle.os.fstat

    def short_read(descriptor: int, size: int) -> bytes:
        status = real_fstat(descriptor)
        name = identities.get((status.st_dev, status.st_ino))
        if name is not None:
            read_counts[name] += 1
            return real_read(descriptor, min(size, 7))
        return real_read(descriptor, size)

    monkeypatch.setattr(lifecycle.os, "read", short_read)

    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"

    assert all(count > 1 for count in read_counts.values())
    assert {
        path.name: path.read_bytes()
        for path in sorted(target.iterdir(), key=lambda candidate: candidate.name)
    } == expected_files
    assert installed.sha256 == expected_digest
    assert _independent_package_digest(target) == expected_digest
    assert validate_theme_package(target).summary.sha256 == expected_digest


def test_install_enforces_byte_cap_while_growing_source_is_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    stylesheet = source / "theme.css"

    def grow() -> None:
        with stylesheet.open("r+b") as stream:
            stream.truncate(2 * THEME_PACKAGE_MAX_BYTES)

    read_limit = THEME_PACKAGE_MAX_BYTES + 64 * 1024
    state = _post_inspection_mutations(
        monkeypatch,
        {stylesheet: grow},
        read_limit=read_limit,
        mutate_on_fstat=False,
    )

    with pytest.raises((ThemeLifecycleError, AssertionError)) as captured:
        install_theme_package(source, root)

    assert isinstance(captured.value, ThemeLifecycleError)
    assert state["triggered"] == {stylesheet.absolute(): True}
    assert state["bytes_read"][0] <= read_limit  # type: ignore[index]
    _assert_no_publication(root, "web", "custom-web")


def test_install_never_reads_past_exact_aggregate_cap_when_last_file_grows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    _resize_web_package_total(source, THEME_PACKAGE_MAX_BYTES)
    root = tmp_path / "themes"
    manifest = source / "manifest.json"
    stylesheet = source / "theme.css"

    def grow_stylesheet() -> None:
        with stylesheet.open("ab") as stream:
            stream.write(b" ")

    state = _post_inspection_mutations(
        monkeypatch,
        {
            manifest: lambda: None,
            stylesheet: grow_stylesheet,
        },
        read_limit=THEME_PACKAGE_MAX_BYTES,
        mutate_on_fstat=False,
    )

    with pytest.raises((ThemeLifecycleError, AssertionError)) as captured:
        install_theme_package(source, root)

    assert isinstance(captured.value, ThemeLifecycleError)
    assert state["triggered"] == {
        manifest.absolute(): True,
        stylesheet.absolute(): True,
    }
    assert state["bytes_read"][0] <= THEME_PACKAGE_MAX_BYTES  # type: ignore[index]
    _assert_no_publication(root, "web", "custom-web")


def test_install_enforces_one_aggregate_cap_across_growing_source_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    manifest = source / "manifest.json"
    stylesheet = source / "theme.css"
    grown_size = THEME_PACKAGE_MAX_BYTES // 2 + 128 * 1024

    def pad_with_spaces(path: Path) -> Callable[[], None]:
        original = path.read_bytes()
        assert len(original) < grown_size

        def grow() -> None:
            path.write_bytes(original + b" " * (grown_size - len(original)))

        return grow

    read_limit = THEME_PACKAGE_MAX_BYTES + 64 * 1024
    state = _post_inspection_mutations(
        monkeypatch,
        {
            manifest: pad_with_spaces(manifest),
            stylesheet: pad_with_spaces(stylesheet),
        },
        read_limit=read_limit,
        mutate_on_fstat=False,
    )

    with pytest.raises((ThemeLifecycleError, AssertionError)) as captured:
        install_theme_package(source, root)

    assert isinstance(captured.value, ThemeLifecycleError)
    triggered = state["triggered"]
    assert isinstance(triggered, dict)
    assert any(triggered.values())
    assert state["bytes_read"][0] <= read_limit  # type: ignore[index]
    _assert_no_publication(root, "web", "custom-web")


@pytest.mark.parametrize(
    ("factory", "asset_name", "old", "new"),
    [
        (_web_theme, "theme.css", b"#eef2f7", b"#eef2f6"),
        (
            _home_assistant_theme,
            "custom-home-assistant.js",
            b'"sds200-card"',
            b'"sds200-cbrd"',
        ),
        (_tui_theme, "palette.json", b"#f5f5f5", b"#f5f5f4"),
    ],
)
def test_validate_rejects_same_size_interface_asset_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: object,
    asset_name: str,
    old: bytes,
    new: bytes,
) -> None:
    source = factory(tmp_path)  # type: ignore[operator]
    asset = source / asset_name
    original = asset.read_bytes()
    replacement = original.replace(old, new, 1)
    assert replacement != original
    mutation = _rewrite_same_size(asset, replacement)
    state = _post_inspection_mutations(monkeypatch, {asset: mutation})

    with pytest.raises(ThemeLifecycleError):
        validate_theme_package(source)

    assert state["triggered"] == {asset.absolute(): True}


def test_validate_rejects_manifest_interface_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path)
    manifest = source / "manifest.json"
    original = manifest.read_bytes()
    replacement = original.replace(b'"interface": "web"', b'"interface": "tui"', 1)
    assert replacement != original
    mutation = _rewrite_same_size(manifest, replacement)
    state = _post_inspection_mutations(monkeypatch, {manifest: mutation})

    with pytest.raises(ThemeLifecycleError):
        validate_theme_package(source)

    assert state["triggered"] == {manifest.absolute(): True}


def test_replace_rolls_back_when_source_asset_mutates_during_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original_source = _web_theme(tmp_path / "original")
    install_theme_package(original_source, root)
    target = root / "web" / "custom-web"
    original_digest = validate_theme_package(target).summary.sha256

    replacement_source = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement_source, {"label": "Replacement Web"})
    stylesheet = replacement_source / "theme.css"
    original = stylesheet.read_bytes()
    replacement = original.replace(b"#eef2f7", b"#eef2f6", 1)
    assert replacement != original
    mutation = _rewrite_same_size(stylesheet, replacement)
    state = _post_inspection_mutations(monkeypatch, {stylesheet: mutation})

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(replacement_source, root, replace=True)

    assert state["triggered"] == {stylesheet.absolute(): True}
    restored = validate_theme_package(target)
    assert restored.summary.label == "Custom Web"
    assert restored.summary.sha256 == original_digest
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


@pytest.mark.parametrize(
    ("factory", "loader_name"),
    [
        (_web_theme, "load_web_theme_package"),
        (_home_assistant_theme, "load_home_assistant_theme_package"),
        (_tui_theme, "load_tui_theme_package"),
    ],
)
def test_validate_parses_only_an_isolated_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: object,
    loader_name: str,
) -> None:
    source = factory(tmp_path)  # type: ignore[operator]
    source = source.absolute()
    real_loader = getattr(lifecycle, loader_name)
    parser_paths: list[Path] = []

    def isolated_loader(directory: object) -> object:
        parser_path = Path(directory)  # type: ignore[arg-type]
        parser_paths.append(parser_path.absolute())
        assert parser_path.absolute() != source, "interface parser consumed the live source"
        return real_loader(directory)

    monkeypatch.setattr(lifecycle, loader_name, isolated_loader)

    package = validate_theme_package(source)

    assert parser_paths
    assert all(path != source for path in parser_paths)
    assert package.summary.path == source


@pytest.mark.parametrize(
    ("overflow", "accepted"),
    [(0, True), (1, False)],
    ids=("exact-boundary", "one-byte-overflow"),
)
def test_validate_enforces_exact_multi_file_aggregate_byte_boundary(
    tmp_path: Path,
    overflow: int,
    accepted: bool,
) -> None:
    source = _web_theme(tmp_path)
    total = THEME_PACKAGE_MAX_BYTES + overflow
    _resize_web_package_total(source, total)
    sizes = [path.stat().st_size for path in source.iterdir()]
    assert len(sizes) == 2
    assert all(size < THEME_PACKAGE_MAX_BYTES for size in sizes)
    assert sum(sizes) == total

    if accepted:
        assert validate_theme_package(source).summary.identifier == "custom-web"
    else:
        with pytest.raises(ThemeLifecycleError, match="byte limit"):
            validate_theme_package(source)


def test_validate_stops_after_bounded_ninth_name_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path).absolute()
    for index in range(24):
        (source / f"extra-{index:02d}.txt").write_text("extra", encoding="utf-8")
    source_status = source.stat()
    real_listdir = lifecycle.os.listdir
    real_scandir = lifecycle.os.scandir
    real_fstat = lifecycle.os.fstat
    state = {"scandir_used": False, "listdir_used": False, "overrun": False}

    def is_source(path: object) -> bool:
        if isinstance(path, int):
            try:
                status = real_fstat(path)
            except OSError:
                return False
            return (status.st_dev, status.st_ino) == (
                source_status.st_dev,
                source_status.st_ino,
            )
        try:
            return Path(os.fsdecode(path)).absolute() == source  # type: ignore[arg-type]
        except TypeError:
            return False

    class BoundedScandir:
        def __init__(self, delegate: object) -> None:
            self._delegate = delegate
            self._yielded = 0

        def __enter__(self) -> BoundedScandir:
            self._delegate.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *arguments: object) -> object:
            return self._delegate.__exit__(*arguments)  # type: ignore[attr-defined]

        def __iter__(self) -> BoundedScandir:
            return self

        def __next__(self) -> object:
            if self._yielded >= THEME_PACKAGE_MAX_FILES + 1:
                state["overrun"] = True
                raise AssertionError("theme validation enumerated beyond the ninth name")
            entry = next(self._delegate)  # type: ignore[arg-type]
            self._yielded += 1
            return entry

        def close(self) -> None:
            self._delegate.close()  # type: ignore[attr-defined]

    def bounded_scandir(path: object = ".") -> object:
        delegate = real_scandir(path)  # type: ignore[arg-type]
        if not is_source(path):
            return delegate
        state["scandir_used"] = True
        return BoundedScandir(delegate)

    def reject_unbounded_listdir(path: object = ".") -> object:
        if is_source(path):
            state["listdir_used"] = True
            raise AssertionError("theme validation used unbounded name enumeration")
        return real_listdir(path)  # type: ignore[arg-type]

    monkeypatch.setattr(lifecycle.os, "scandir", bounded_scandir)
    monkeypatch.setattr(lifecycle.os, "listdir", reject_unbounded_listdir)

    with pytest.raises((ThemeLifecycleError, AssertionError)) as captured:
        validate_theme_package(source)

    assert isinstance(captured.value, ThemeLifecycleError)
    assert state == {
        "scandir_used": True,
        "listdir_used": False,
        "overrun": False,
    }


def test_install_rejects_final_source_membership_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    stylesheet = source / "theme.css"
    late_entry = source / "late-entry.txt"
    state = _post_inspection_mutations(
        monkeypatch,
        {stylesheet: lambda: late_entry.write_text("late", encoding="utf-8")},
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert state["triggered"] == {stylesheet.absolute(): True}
    assert late_entry.is_file()
    _assert_no_publication(root, "web", "custom-web")


def test_install_rejects_stage_mutation_and_removes_private_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source").absolute()
    root = (tmp_path / "themes").absolute()
    real_loader = lifecycle.load_web_theme_package
    state: dict[str, Path | None] = {"mutated": None}

    def mutate_stage_loader(directory: object) -> object:
        stage = Path(directory).absolute()  # type: ignore[arg-type]
        if (
            state["mutated"] is None
            and stage != source
            and _is_retained_stage_entry(stage, root / "web")
        ):
            stylesheet = stage / "theme.css"
            original = stylesheet.read_bytes()
            replacement = original.replace(b"#eef2f7", b"#eef2f6", 1)
            assert replacement != original
            _rewrite_same_size(stylesheet, replacement)()
            state["mutated"] = stage
        return real_loader(directory)

    monkeypatch.setattr(lifecycle, "load_web_theme_package", mutate_stage_loader)

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert state["mutated"] is not None
    _assert_no_publication(root, "web", "custom-web")


def test_install_digest_equals_exact_published_stage_bytes(tmp_path: Path) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"

    installed = install_theme_package(source, root)

    assert installed.path is not None
    published_digest = _independent_package_digest(installed.path)
    assert published_digest == _independent_package_digest(source)
    assert installed.sha256 == published_digest
    assert validate_theme_package(installed.path).summary.sha256 == published_digest


@pytest.mark.parametrize(
    "missing_capability",
    (
        "no-follow",
        "nonblocking",
        "dir-fd",
        "rename-dir-fd",
        "rmdir-dir-fd",
        "unlink-dir-fd",
        "scandir-fd",
        "atomic-noreplace",
    ),
)
def test_install_fails_closed_without_required_snapshot_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_capability: str,
) -> None:
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        pytest.skip("descriptor-relative O_NOFOLLOW is unavailable")
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"

    if missing_capability == "no-follow":
        monkeypatch.delattr(lifecycle.os, "O_NOFOLLOW")
    elif missing_capability == "nonblocking":
        monkeypatch.delattr(lifecycle.os, "O_NONBLOCK")
    elif missing_capability == "dir-fd":
        monkeypatch.setattr(
            lifecycle.os,
            "supports_dir_fd",
            set(lifecycle.os.supports_dir_fd) - {lifecycle.os.open},
        )
    elif missing_capability == "rename-dir-fd":
        monkeypatch.setattr(
            lifecycle.os,
            "supports_dir_fd",
            set(lifecycle.os.supports_dir_fd) - {lifecycle.os.rename},
        )
    elif missing_capability == "rmdir-dir-fd":
        monkeypatch.setattr(
            lifecycle.os,
            "supports_dir_fd",
            set(lifecycle.os.supports_dir_fd) - {lifecycle.os.rmdir},
        )
    elif missing_capability == "unlink-dir-fd":
        monkeypatch.setattr(
            lifecycle.os,
            "supports_dir_fd",
            set(lifecycle.os.supports_dir_fd) - {lifecycle.os.unlink},
        )
    elif missing_capability == "scandir-fd":
        monkeypatch.setattr(
            lifecycle.os,
            "supports_fd",
            set(lifecycle.os.supports_fd) - {lifecycle.os.scandir},
        )
    else:
        monkeypatch.setattr(lifecycle, "_atomic_noreplace_available", lambda: False)

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    _assert_no_publication(root, "web", "custom-web")


def test_install_fails_cleanly_when_target_filesystem_lacks_noreplace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    real_rename = lifecycle._rename_noreplace
    injected = False

    def unsupported_rename(
        source_path: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal injected
        source_name = Path(source_path).name
        destination_name = Path(destination).name
        if (
            not injected
            and source_name.startswith(".sdsctl-capability-")
            and source_name.endswith("-source")
            and destination_name.startswith(".sdsctl-capability-")
            and destination_name.endswith("-occupied")
        ):
            injected = True
            raise OSError(errno.ENOSYS, "injected unsupported rename", str(destination))
        real_rename(
            source_path,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(lifecycle, "_rename_noreplace", unsupported_rename)

    with pytest.raises(ThemeLifecycleError, match="does not preserve no-replace"):
        install_theme_package(source, root)

    assert injected
    interface_root = root / "web"
    assert not (interface_root / "custom-web").exists()
    if interface_root.exists():
        assert not any(
            path.name.startswith((".sdsctl-capability-", ".sdsctl-stage-"))
            for path in interface_root.iterdir()
        )
    assert not any(
        path.name.startswith((".sdsctl-capability-", ".sdsctl-purge-"))
        for path in tmp_path.iterdir()
    )


def test_install_recovers_empty_interrupted_capability_probe(tmp_path: Path) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    interface_root = root / "web"
    orphan = interface_root / ".sdsctl-capability-interrupted-source"
    orphan.mkdir(parents=True)

    installed = install_theme_package(source, root)

    assert installed.identity == "web/custom-web"
    assert not orphan.exists()
    assert not any(path.name.startswith(".sdsctl-capability-") for path in interface_root.iterdir())


@pytest.mark.parametrize("interrupt_after_move", (False, True))
def test_capability_probe_preserves_interruption_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after_move: bool,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    real_rename = lifecycle._rename_noreplace

    def interrupt_probe(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        destination = str(destination_path)
        if not interrupt_after_move and destination.endswith("-occupied"):
            raise KeyboardInterrupt("injected pre-probe-rename interruption")
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if interrupt_after_move and destination.endswith("-destination"):
            raise KeyboardInterrupt("injected post-probe-rename interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", interrupt_probe)

    with pytest.raises(KeyboardInterrupt, match="probe-rename interruption"):
        install_theme_package(source, root)

    interface_root = root / "web"
    assert not (interface_root / "custom-web").exists()
    if interface_root.exists():
        assert not any(
            path.name.startswith(".sdsctl-capability-") for path in interface_root.iterdir()
        )


def test_capability_recovery_preserves_empty_binding_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface_root = tmp_path / "themes" / "web"
    orphan = interface_root / ".sdsctl-capability-interrupted-source"
    orphan.mkdir(parents=True)
    displaced = tmp_path / "displaced-capability"
    foreign = tmp_path / "foreign-capability"
    foreign.mkdir()
    real_identity = lifecycle._relative_entry_identity
    retained_checks = 0

    def substitute_before_final_identity(
        directory: int,
        name: str,
    ) -> tuple[int, int, int] | None:
        nonlocal retained_checks
        if name.endswith("-retained"):
            retained_checks += 1
            if retained_checks == 2:
                retained = interface_root / name
                retained.rename(displaced)
                foreign.rename(retained)
        return real_identity(directory, name)

    monkeypatch.setattr(
        lifecycle,
        "_relative_entry_identity",
        substitute_before_final_identity,
    )

    with pytest.raises(ThemeLifecycleError, match="changed before removal"):
        lifecycle._remove_empty_capability_probe(orphan)

    assert retained_checks == 2
    assert displaced.is_dir()
    retained_entries = tuple(
        path for path in interface_root.iterdir() if path.name.endswith("-retained")
    )
    assert len(retained_entries) == 1
    assert retained_entries[0].is_dir()


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
        "home-assistant/waterfall",
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
    real_rename = lifecycle._rename_noreplace

    def fail_stage_activation(
        source: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if _is_retained_stage_entry(
            source_path,
            target.parent,
        ) and _is_configured_entry(destination_path, target):
            raise OSError("injected activation failure")
        real_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(lifecycle, "_rename_noreplace", fail_stage_activation)

    with pytest.raises(OSError, match="injected activation failure"):
        install_theme_package(replacement, root, replace=True)

    restored = validate_theme_package(target)
    assert restored.summary.label == "Custom Web"
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_fresh_install_cleans_publication_after_post_rename_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    target = root / "web" / "custom-web"
    real_rename = lifecycle._rename_noreplace

    def publish_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if _is_retained_stage_entry(
            source_candidate,
            target.parent,
        ) and _is_configured_entry(destination_candidate, target):
            raise KeyboardInterrupt("injected post-publication interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", publish_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-publication"):
        install_theme_package(source, root)

    _assert_no_publication(root, "web", "custom-web")


def test_fresh_install_preserves_empty_target_created_at_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    target = root / "web" / "custom-web"
    conflict = root / "web" / ".sdsctl-conflict-custom-web"
    real_rename = lifecycle._rename_noreplace
    foreign_identity: tuple[int, int] | None = None

    def collide_with_publication(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal foreign_identity
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        if (
            foreign_identity is None
            and _is_retained_stage_entry(source_candidate, target.parent)
            and _is_configured_entry(destination_candidate, target)
        ):
            target.mkdir()
            status = target.stat()
            foreign_identity = (status.st_dev, status.st_ino)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(lifecycle, "_rename_noreplace", collide_with_publication)

    with pytest.raises(ThemeLifecycleError, match="preserved for operator inspection"):
        install_theme_package(source, root)

    assert foreign_identity is not None
    conflict_status = conflict.stat()
    assert (conflict_status.st_dev, conflict_status.st_ino) == foreign_identity
    assert not target.exists()
    assert not any(
        path.name.startswith((".sdsctl-stage-", ".sdsctl-rollback-", ".sdsctl-purge-"))
        for path in target.parent.iterdir()
    )


def test_replacement_preserves_empty_target_created_at_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original_source = _web_theme(tmp_path / "original")
    original = install_theme_package(original_source, root)
    replacement = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement, {"label": "Replacement Web"})
    target = root / "web" / "custom-web"
    conflict = root / "web" / ".sdsctl-conflict-custom-web"
    real_rename = lifecycle._rename_noreplace
    foreign_identity: tuple[int, int] | None = None

    def collide_with_publication(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal foreign_identity
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        if (
            foreign_identity is None
            and _is_retained_stage_entry(source_candidate, target.parent)
            and _is_configured_entry(destination_candidate, target)
        ):
            target.mkdir()
            status = target.stat()
            foreign_identity = (status.st_dev, status.st_ino)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(lifecycle, "_rename_noreplace", collide_with_publication)

    with pytest.raises(ThemeLifecycleError, match="preserved for operator inspection"):
        install_theme_package(replacement, root, replace=True)

    assert foreign_identity is not None
    conflict_status = conflict.stat()
    assert (conflict_status.st_dev, conflict_status.st_ino) == foreign_identity
    restored = validate_theme_package(target)
    assert restored.summary.sha256 == original.sha256
    assert not any(
        path.name.startswith((".sdsctl-stage-", ".sdsctl-rollback-", ".sdsctl-purge-"))
        for path in target.parent.iterdir()
    )


def test_replace_restores_target_after_post_rollback_rename_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original_source = _web_theme(tmp_path / "original")
    original = install_theme_package(original_source, root)
    target = root / "web" / "custom-web"
    replacement = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement, {"label": "Replacement Web"})
    rollback = root / "web" / ".sdsctl-rollback-custom-web"
    real_rename = lifecycle._rename_noreplace

    def save_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if _is_configured_entry(
            source_candidate,
            target,
        ) and _is_configured_entry(destination_candidate, rollback):
            raise KeyboardInterrupt("injected post-rollback interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", save_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-rollback"):
        install_theme_package(replacement, root, replace=True)

    restored = validate_theme_package(target)
    assert restored.summary.label == "Custom Web"
    assert restored.summary.sha256 == original.sha256
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_replace_keeps_verified_publication_after_post_rollback_delete_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original_source = _web_theme(tmp_path / "original")
    install_theme_package(original_source, root)
    target = root / "web" / "custom-web"
    replacement = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement, {"label": "Replacement Web"})
    expected = validate_theme_package(replacement)
    rollback = root / "web" / ".sdsctl-rollback-custom-web"
    real_remove = lifecycle._remove_private_tree
    interrupted = False

    def delete_then_interrupt(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        nonlocal interrupted
        destructive_rollback_delete = _is_configured_entry(path, rollback) and path.is_dir()
        real_remove(path, expected_identity=expected_identity, parent=parent)
        if destructive_rollback_delete:
            assert not rollback.exists()
            interrupted = True
            raise KeyboardInterrupt("injected post-rollback-deletion interruption")

    monkeypatch.setattr(lifecycle, "_remove_private_tree", delete_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-rollback-deletion"):
        install_theme_package(replacement, root, replace=True)

    assert interrupted
    published = validate_theme_package(target)
    assert published.summary.label == "Replacement Web"
    assert published.summary.sha256 == expected.summary.sha256
    assert not rollback.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_replace_rolls_back_after_post_activation_integrity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original_source = _web_theme(tmp_path / "original")
    original = install_theme_package(original_source, root)
    target = root / "web" / "custom-web"
    replacement = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement, {"label": "Replacement Web"})
    real_rename = lifecycle._rename_noreplace
    mutated = False

    def mutate_after_activation(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal mutated
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if (
            not mutated
            and _is_retained_stage_entry(source_candidate, target.parent)
            and _is_configured_entry(destination_candidate, target)
        ):
            stylesheet = target / "theme.css"
            content = stylesheet.read_bytes()
            changed = content.replace(b"#eef2f7", b"#eef2f6", 1)
            assert changed != content
            _rewrite_same_size(stylesheet, changed)()
            mutated = True

    monkeypatch.setattr(lifecycle, "_rename_noreplace", mutate_after_activation)

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(replacement, root, replace=True)

    assert mutated
    restored = validate_theme_package(target)
    assert restored.summary.label == "Custom Web"
    assert restored.summary.sha256 == original.sha256
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_replace_restores_target_after_final_publication_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original_source = _web_theme(tmp_path / "original")
    original = install_theme_package(original_source, root)
    target = root / "web" / "custom-web"
    interface_root = target.parent
    conflict = interface_root / ".sdsctl-conflict-custom-web"
    replacement = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement, {"label": "Replacement Web"})
    displaced_publication = tmp_path / "displaced-publication"
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("external material", encoding="utf-8")
    real_collision_validation = lifecycle._validate_candidate_collision
    substituted = False

    def substitute_after_final_collision(
        package: lifecycle.ValidatedThemePackage,
        managed_root: Path,
        *,
        excluded: Path | None,
    ) -> None:
        nonlocal substituted
        real_collision_validation(
            package,
            managed_root,
            excluded=excluded,
        )
        if not substituted and _is_configured_entry(package.summary.path, target):
            target.rename(displaced_publication)
            target.symlink_to(external, target_is_directory=True)
            substituted = True

    monkeypatch.setattr(
        lifecycle,
        "_validate_candidate_collision",
        substitute_after_final_collision,
    )

    with pytest.raises(ThemeLifecycleError, match="preserved for operator inspection"):
        install_theme_package(replacement, root, replace=True)

    assert substituted
    assert marker.read_text(encoding="utf-8") == "external material"
    restored = validate_theme_package(target)
    assert restored.summary.label == "Custom Web"
    assert restored.summary.sha256 == original.sha256
    assert conflict.is_symlink()
    assert (conflict / "marker.txt").read_text(encoding="utf-8") == "external material"
    assert not any(
        path.name.startswith((".sdsctl-stage-", ".sdsctl-rollback-", ".sdsctl-purge-"))
        for path in interface_root.iterdir()
    )


def test_replace_preserves_nonempty_foreign_target_as_blocking_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original_source = _web_theme(tmp_path / "original")
    original = install_theme_package(original_source, root)
    target = root / "web" / "custom-web"
    interface_root = target.parent
    conflict = interface_root / ".sdsctl-conflict-custom-web"
    replacement = _web_theme(tmp_path / "replacement")
    _write_manifest(replacement, {"label": "Replacement Web"})
    displaced_publication = tmp_path / "displaced-publication"
    foreign = tmp_path / "foreign-target"
    foreign.mkdir()
    marker = foreign / "marker.txt"
    marker.write_text("foreign material", encoding="utf-8")
    real_collision_validation = lifecycle._validate_candidate_collision
    substituted = False

    def substitute_after_final_collision(
        package: lifecycle.ValidatedThemePackage,
        managed_root: Path,
        *,
        excluded: Path | None,
    ) -> None:
        nonlocal substituted
        real_collision_validation(
            package,
            managed_root,
            excluded=excluded,
        )
        if not substituted and _is_configured_entry(package.summary.path, target):
            target.rename(displaced_publication)
            foreign.rename(target)
            substituted = True

    monkeypatch.setattr(
        lifecycle,
        "_validate_candidate_collision",
        substitute_after_final_collision,
    )

    with pytest.raises(ThemeLifecycleError, match="preserved for operator inspection"):
        install_theme_package(replacement, root, replace=True)

    assert substituted
    restored = validate_theme_package(target)
    assert restored.summary.label == "Custom Web"
    assert restored.summary.sha256 == original.sha256
    assert conflict.is_dir()
    assert not conflict.is_symlink()
    assert (conflict / "marker.txt").read_text(encoding="utf-8") == "foreign material"
    assert not any(
        path.name.startswith((".sdsctl-stage-", ".sdsctl-rollback-", ".sdsctl-purge-"))
        for path in interface_root.iterdir()
    )

    with pytest.raises(ThemeLifecycleError, match="preserved concurrent-write conflict"):
        install_theme_package(replacement, root, replace=True)

    assert (conflict / "marker.txt").read_text(encoding="utf-8") == "foreign material"
    assert validate_theme_package(target).summary.sha256 == original.sha256


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


def test_rollback_recovery_preserves_target_created_at_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original = _web_theme(tmp_path / "original")
    install_theme_package(original, root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    conflict = interface_root / ".sdsctl-conflict-custom-web"
    target.rename(rollback)
    real_rename = lifecycle._rename_noreplace
    foreign_identity: tuple[int, int] | None = None

    def collide_with_promotion(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal foreign_identity
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        if (
            foreign_identity is None
            and source_candidate == rollback
            and destination_candidate == target
        ):
            target.mkdir()
            status = target.stat()
            foreign_identity = (status.st_dev, status.st_ino)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(lifecycle, "_rename_noreplace", collide_with_promotion)

    with pytest.raises(ThemeLifecycleError, match="could not be promoted safely"):
        lifecycle._recover_interface(interface_root)

    assert foreign_identity is not None
    conflict_status = conflict.stat()
    assert (conflict_status.st_dev, conflict_status.st_ino) == foreign_identity
    assert rollback.is_dir()
    assert not target.exists()


def test_rollback_recovery_quarantines_substitution_after_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    original = _web_theme(tmp_path / "original")
    install_theme_package(original, root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    conflict = interface_root / ".sdsctl-conflict-custom-web"
    target.rename(rollback)
    retained_parent = tmp_path / "retained"
    retained_parent.mkdir()
    retained = retained_parent / "custom-web"
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("external material", encoding="utf-8")
    real_assertion = lifecycle._assert_package_image
    substituted = False

    def substitute_before_promoted_assertion(
        path: Path,
        image: lifecycle._PackageImage,
        *,
        require_identity: bool,
    ) -> None:
        nonlocal substituted
        if path == target and not rollback.exists() and not substituted:
            target.rename(retained)
            try:
                target.symlink_to(external, target_is_directory=True)
            except OSError as error:
                pytest.skip(f"symbolic links are unavailable: {error}")
            substituted = True
        real_assertion(path, image, require_identity=require_identity)

    monkeypatch.setattr(
        lifecycle,
        "_assert_package_image",
        substitute_before_promoted_assertion,
    )

    with pytest.raises(ThemeLifecycleError, match="failed exact image verification"):
        lifecycle._recover_interface(interface_root)

    assert substituted
    assert not target.exists()
    assert conflict.is_symlink()
    assert conflict.readlink() == external
    assert marker.read_text(encoding="utf-8") == "external material"
    assert validate_theme_package(retained).summary.identifier == "custom-web"


def test_install_rejects_symlinked_rollback_without_publishing_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    interface_root = root / "web"
    interface_root.mkdir(parents=True)
    target = interface_root / "custom-web"
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    external = _web_theme(tmp_path / "external")
    marker = external / "marker.txt"
    marker.write_text("external material", encoding="utf-8")
    try:
        rollback.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")
    source = _web_theme(tmp_path / "source")

    with pytest.raises(ThemeLifecycleError, match="symlink"):
        install_theme_package(source, root)

    assert rollback.is_symlink()
    assert not target.exists()
    assert marker.read_text(encoding="utf-8") == "external material"
    assert not any(path.name.startswith(".sdsctl-stage-") for path in interface_root.iterdir())


def test_install_rejects_mismatched_real_rollback_without_publishing_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    interface_root = root / "web"
    interface_root.mkdir(parents=True)
    target = interface_root / "custom-web"
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    mismatched = _web_theme(tmp_path / "mismatched", "another-web")
    shutil.copytree(mismatched, rollback)
    source = _web_theme(tmp_path / "source")

    with pytest.raises(SDS200Error, match="does not match"):
        install_theme_package(source, root)

    assert rollback.is_dir()
    assert not rollback.is_symlink()
    assert not target.exists()
    assert not any(path.name.startswith(".sdsctl-stage-") for path in interface_root.iterdir())


def test_install_rejects_rollback_symlink_substitution_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    interface_root = root / "web"
    interface_root.mkdir(parents=True)
    target = interface_root / "custom-web"
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    conflict = interface_root / ".sdsctl-conflict-custom-web"
    valid_rollback = _web_theme(tmp_path / "rollback-source")
    shutil.copytree(valid_rollback, rollback)
    retained_parent = tmp_path / "retained-rollback"
    retained_parent.mkdir()
    retained_rollback = retained_parent / "custom-web"
    external = tmp_path / "external-rollback-target"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("external material", encoding="utf-8")
    external_status = external.stat()
    source = _web_theme(tmp_path / "source")
    real_validation = lifecycle._validated_recovery_package
    substituted = False

    def validate_then_substitute(
        directory: Path,
        *,
        target: Path,
        interface: str,
    ) -> tuple[lifecycle.ValidatedThemePackage, lifecycle._PackageImage]:
        nonlocal substituted
        package, image = real_validation(
            directory,
            target=target,
            interface=interface,
        )
        assert _is_configured_entry(directory, rollback)
        directory.rename(retained_rollback)
        try:
            directory.symlink_to(external, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symbolic links are unavailable: {error}")
        substituted = True
        return package, image

    monkeypatch.setattr(
        lifecycle,
        "_validated_recovery_package",
        validate_then_substitute,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    assert substituted
    assert not target.exists()
    preserved_links = [path for path in (rollback, conflict) if path.is_symlink()]
    assert len(preserved_links) == 1
    assert preserved_links[0].readlink() == external
    assert marker.read_text(encoding="utf-8") == "external material"
    retained_external_status = external.stat()
    assert (
        retained_external_status.st_dev,
        retained_external_status.st_ino,
    ) == (
        external_status.st_dev,
        external_status.st_ino,
    )
    assert validate_theme_package(retained_rollback).summary.identifier == "custom-web"
    assert not any(
        path.name.startswith((".sdsctl-stage-", ".sdsctl-purge-"))
        for path in interface_root.iterdir()
    )


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

    def fail_tombstone(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        if path.name.startswith(".sdsctl-remove-") and path.exists():
            raise OSError("injected tombstone failure")
        real_remove(path, expected_identity=expected_identity, parent=parent)

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


def test_remove_reraises_post_restore_interruption_after_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    target_status = target.stat()
    real_remove = lifecycle._remove_private_tree
    real_rename = lifecycle._rename_noreplace
    restored_then_interrupted = False
    record_cleanup_interrupted = False

    def fail_tombstone(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        nonlocal record_cleanup_interrupted
        if path.name == tombstone.name and path.exists():
            raise OSError("injected tombstone failure")
        real_remove(path, expected_identity=expected_identity, parent=parent)
        if path.name.startswith(".sdsctl-removal-record-"):
            record_cleanup_interrupted = True
            raise OSError("injected post-record-cleanup failure")

    def restore_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal restored_then_interrupted
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if _is_configured_entry(
            Path(source_path),
            tombstone,
        ) and _is_configured_entry(Path(destination_path), target):
            restored_then_interrupted = True
            raise KeyboardInterrupt("injected post-cleanup-restoration interruption")

    monkeypatch.setattr(lifecycle, "_remove_private_tree", fail_tombstone)
    monkeypatch.setattr(lifecycle, "_rename_noreplace", restore_then_interrupt)

    with pytest.raises(
        KeyboardInterrupt,
        match="post-cleanup-restoration",
    ) as interrupted:
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert restored_then_interrupted
    assert record_cleanup_interrupted
    assert isinstance(interrupted.value.__cause__, OSError)
    assert "post-record-cleanup" in str(interrupted.value.__cause__)
    restored_status = target.stat()
    assert (restored_status.st_dev, restored_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_remove_restores_exact_target_after_post_retention_rename_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    target_status = target.stat()
    real_rename = lifecycle._rename_noreplace
    interrupted = False

    def retain_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal interrupted
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if _is_configured_entry(
            source_candidate,
            target,
        ) and _is_configured_entry(destination_candidate, tombstone):
            interrupted = True
            raise KeyboardInterrupt("injected post-retention-rename interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", retain_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-retention-rename"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert interrupted
    restored_status = target.stat()
    assert (restored_status.st_dev, restored_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    restored = validate_theme_package(target)
    assert restored.summary.sha256 == installed.sha256
    assert not tombstone.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_remove_reraises_post_restore_interruption_after_retention_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    target_status = target.stat()
    real_rename = lifecycle._rename_noreplace
    real_remove = lifecycle._remove_private_tree
    retained_then_failed = False
    restored_then_interrupted = False
    record_cleanup_interrupted = False

    def fail_retention_and_interrupt_restoration(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal retained_then_failed, restored_then_interrupted
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if _is_configured_entry(
            source_candidate,
            target,
        ) and _is_configured_entry(destination_candidate, tombstone):
            retained_then_failed = True
            raise OSError("injected post-retention failure")
        if _is_configured_entry(
            source_candidate,
            tombstone,
        ) and _is_configured_entry(destination_candidate, target):
            restored_then_interrupted = True
            raise KeyboardInterrupt("injected post-retention-restoration interruption")

    def clean_record_then_fail(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        nonlocal record_cleanup_interrupted
        real_remove(path, expected_identity=expected_identity, parent=parent)
        if path.name.startswith(".sdsctl-removal-record-"):
            record_cleanup_interrupted = True
            raise OSError("injected post-record-cleanup failure")

    monkeypatch.setattr(
        lifecycle,
        "_rename_noreplace",
        fail_retention_and_interrupt_restoration,
    )
    monkeypatch.setattr(lifecycle, "_remove_private_tree", clean_record_then_fail)

    with pytest.raises(
        KeyboardInterrupt,
        match="post-retention-restoration",
    ) as interrupted:
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert retained_then_failed
    assert restored_then_interrupted
    assert record_cleanup_interrupted
    assert isinstance(interrupted.value.__cause__, OSError)
    assert "post-record-cleanup" in str(interrupted.value.__cause__)
    restored_status = target.stat()
    assert (restored_status.st_dev, restored_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not tombstone.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


@pytest.mark.parametrize("failure_stage", ("retention", "cleanup"))
def test_remove_prefers_critical_record_cleanup_over_ordinary_restore_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    target_status = target.stat()
    real_remove = lifecycle._remove_private_tree
    real_rename = lifecycle._rename_noreplace

    def fail_cleanup_and_interrupt_record(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        if failure_stage == "cleanup" and path.name == tombstone.name and path.exists():
            raise OSError("ordinary tombstone cleanup failure")
        real_remove(path, expected_identity=expected_identity, parent=parent)
        if path.name.startswith(".sdsctl-removal-record-"):
            raise KeyboardInterrupt("critical record cleanup")

    def fail_retention_and_restoration(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if (
            failure_stage == "retention"
            and _is_configured_entry(source_candidate, target)
            and _is_configured_entry(destination_candidate, tombstone)
        ):
            raise OSError("ordinary retention failure")
        if _is_configured_entry(
            source_candidate,
            tombstone,
        ) and _is_configured_entry(destination_candidate, target):
            raise OSError("ordinary restoration failure")

    monkeypatch.setattr(
        lifecycle,
        "_remove_private_tree",
        fail_cleanup_and_interrupt_record,
    )
    monkeypatch.setattr(lifecycle, "_rename_noreplace", fail_retention_and_restoration)

    with pytest.raises(KeyboardInterrupt, match="critical record cleanup") as interrupted:
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert isinstance(interrupted.value.__cause__, OSError)
    assert "restoration failure" in str(interrupted.value.__cause__)
    restored_status = target.stat()
    assert (restored_status.st_dev, restored_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not tombstone.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_remove_reraises_interrupted_saved_observation_after_safe_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    target_status = target.stat()
    real_identity = lifecycle._path_entry_identity
    real_remove = lifecycle._remove_private_tree
    real_rename = lifecycle._rename_noreplace
    tombstone_observations = 0

    def fail_retention(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if _is_configured_entry(
            Path(source_path),
            target,
        ) and _is_configured_entry(Path(destination_path), tombstone):
            raise OSError("injected retention failure")
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    def interrupt_fallback_observation(path: Path) -> tuple[int, int, int] | None:
        nonlocal tombstone_observations
        if path.name == tombstone.name:
            tombstone_observations += 1
            if tombstone_observations == 2:
                raise KeyboardInterrupt("injected saved-observation interruption")
        return real_identity(path)

    def clean_record_then_fail(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        real_remove(path, expected_identity=expected_identity, parent=parent)
        if path.name.startswith(".sdsctl-removal-record-"):
            raise OSError("ordinary post-observation cleanup failure")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", fail_retention)
    monkeypatch.setattr(lifecycle, "_path_entry_identity", interrupt_fallback_observation)
    monkeypatch.setattr(lifecycle, "_remove_private_tree", clean_record_then_fail)

    with pytest.raises(KeyboardInterrupt, match="saved-observation") as interrupted:
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert tombstone_observations >= 3
    assert isinstance(interrupted.value.__cause__, OSError)
    assert "post-observation cleanup" in str(interrupted.value.__cause__)
    restored_status = target.stat()
    assert (restored_status.st_dev, restored_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not tombstone.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_remove_prefers_critical_cleanup_over_ordinary_saved_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    target_status = target.stat()
    real_identity = lifecycle._path_entry_identity
    real_remove = lifecycle._remove_private_tree
    real_rename = lifecycle._rename_noreplace
    tombstone_observations = 0

    def fail_retention(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if _is_configured_entry(
            Path(source_path),
            target,
        ) and _is_configured_entry(Path(destination_path), tombstone):
            raise OSError("ordinary retention failure")
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    def fail_fallback_observation(path: Path) -> tuple[int, int, int] | None:
        nonlocal tombstone_observations
        if path.name == tombstone.name:
            tombstone_observations += 1
            if tombstone_observations == 2:
                raise OSError("ordinary saved observation failure")
        return real_identity(path)

    def clean_record_then_interrupt(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        real_remove(path, expected_identity=expected_identity, parent=parent)
        if path.name.startswith(".sdsctl-removal-record-"):
            raise KeyboardInterrupt("critical post-observation cleanup")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", fail_retention)
    monkeypatch.setattr(lifecycle, "_path_entry_identity", fail_fallback_observation)
    monkeypatch.setattr(lifecycle, "_remove_private_tree", clean_record_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="critical post-observation") as interrupted:
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert tombstone_observations >= 3
    assert isinstance(interrupted.value.__cause__, OSError)
    assert "saved observation" in str(interrupted.value.__cause__)
    restored_status = target.stat()
    assert (restored_status.st_dev, restored_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not tombstone.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in target.parent.iterdir())


def test_remove_preserves_foreign_entry_substituted_before_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    interface_root = target.parent
    tombstone = interface_root / ".sdsctl-remove-custom-web"
    conflict = interface_root / ".sdsctl-conflict-custom-web"
    displaced_parent = tmp_path / "displaced"
    displaced_parent.mkdir()
    displaced_target = displaced_parent / "custom-web"
    foreign = tmp_path / "foreign-target"
    foreign.mkdir()
    marker = foreign / "marker.txt"
    marker.write_text("foreign material", encoding="utf-8")
    real_rename = lifecycle._rename_noreplace
    substituted = False

    def substitute_before_retention(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal substituted
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        if (
            not substituted
            and _is_configured_entry(source_candidate, target)
            and _is_configured_entry(destination_candidate, tombstone)
        ):
            target.rename(displaced_target)
            foreign.rename(target)
            substituted = True
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(lifecycle, "_rename_noreplace", substitute_before_retention)

    with pytest.raises(ThemeLifecycleError, match="preserved for operator inspection"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert substituted
    assert not target.exists()
    assert not tombstone.exists()
    assert conflict.is_dir()
    assert (conflict / "marker.txt").read_text(encoding="utf-8") == "foreign material"
    assert validate_theme_package(displaced_target).summary.sha256 == installed.sha256
    assert not any(
        path.name.startswith((".sdsctl-remove-", ".sdsctl-purge-"))
        for path in interface_root.iterdir()
    )

    with pytest.raises(ThemeLifecycleError, match="preserved concurrent-write conflict"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert (conflict / "marker.txt").read_text(encoding="utf-8") == "foreign material"
    assert validate_theme_package(displaced_target).summary.sha256 == installed.sha256


def test_remove_preserves_concurrent_target_during_interruption_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    real_rename = lifecycle._rename_noreplace
    foreign_identity: tuple[int, int] | None = None

    def interrupt_after_concurrent_target(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal foreign_identity
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if _is_configured_entry(
            source_candidate,
            target,
        ) and _is_configured_entry(destination_candidate, tombstone):
            target.mkdir()
            status = target.stat()
            foreign_identity = (status.st_dev, status.st_ino)
            raise KeyboardInterrupt("injected concurrent restore target")

    monkeypatch.setattr(
        lifecycle,
        "_rename_noreplace",
        interrupt_after_concurrent_target,
    )

    with pytest.raises(KeyboardInterrupt, match="concurrent restore") as interrupted:
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert isinstance(interrupted.value.__cause__, ThemeLifecycleError)
    assert "could not restore" in str(interrupted.value.__cause__)
    assert foreign_identity is not None
    target_status = target.stat()
    assert (target_status.st_dev, target_status.st_ino) == foreign_identity
    assert _independent_package_digest(tombstone) == installed.sha256

    with pytest.raises(ThemeLifecycleError, match="both were preserved"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    target_status = target.stat()
    assert (target_status.st_dev, target_status.st_ino) == foreign_identity
    assert _independent_package_digest(tombstone) == installed.sha256


def test_remove_preserves_incomplete_detached_cleanup_for_operator_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    install_theme_package(source, root)
    target = root / "web" / "custom-web"
    real_clear = lifecycle._clear_retained_private_directory

    def fail_retained_cleanup(_directory: int) -> None:
        raise OSError("injected retained cleanup failure")

    monkeypatch.setattr(
        lifecycle,
        "_clear_retained_private_directory",
        fail_retained_cleanup,
    )

    with pytest.raises(OSError, match="injected retained cleanup failure"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    purge_entries = tuple(
        path for path in target.parent.iterdir() if path.name.startswith(".sdsctl-purge-")
    )
    assert len(purge_entries) == 1
    assert (purge_entries[0] / "manifest.json").is_file()
    assert not target.exists()

    monkeypatch.setattr(
        lifecycle,
        "_clear_retained_private_directory",
        real_clear,
    )
    with pytest.raises(
        ThemeLifecycleError,
        match="unauthenticated interrupted purge",
    ) as blocked:
        install_theme_package(source, root)

    assert str(purge_entries[0]) in str(blocked.value)
    assert (purge_entries[0] / "manifest.json").is_file()

    shutil.rmtree(purge_entries[0])
    reinstalled = install_theme_package(source, root)

    assert reinstalled.identity == "web/custom-web"
    assert target.is_dir()
    assert not any(path.name.startswith(".sdsctl-purge-") for path in target.parent.iterdir())


def test_remove_preserves_target_inserted_during_tombstone_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    conflict = root / "web" / ".sdsctl-conflict-custom-web"
    real_remove = lifecycle._remove_private_tree
    foreign_identity: tuple[int, int] | None = None

    def insert_target_after_cleanup(
        path: Path,
        *,
        expected_identity: tuple[int, int] | None = None,
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> None:
        nonlocal foreign_identity
        should_insert = _is_configured_entry(path, tombstone) and path.exists()
        real_remove(path, expected_identity=expected_identity, parent=parent)
        if should_insert:
            target.mkdir()
            status = target.stat()
            foreign_identity = (status.st_dev, status.st_ino)

    monkeypatch.setattr(lifecycle, "_remove_private_tree", insert_target_after_cleanup)

    with pytest.raises(ThemeLifecycleError, match="preserved for operator inspection"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert foreign_identity is not None
    conflict_status = conflict.stat()
    assert (conflict_status.st_dev, conflict_status.st_ino) == foreign_identity
    assert not target.exists()
    assert not tombstone.exists()


def test_remove_restores_retained_target_after_identity_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    tombstone = root / "web" / ".sdsctl-remove-custom-web"
    target_status = target.stat()
    real_identity = lifecycle._path_entry_identity
    injected = False

    def fail_first_retained_identity(path: Path) -> tuple[int, int, int] | None:
        nonlocal injected
        if _is_configured_entry(path, tombstone) and tombstone.exists() and not injected:
            injected = True
            raise OSError("injected retained identity read failure")
        return real_identity(path)

    monkeypatch.setattr(lifecycle, "_path_entry_identity", fail_first_retained_identity)

    with pytest.raises(OSError, match="injected retained identity read failure"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    assert injected
    restored_status = target.stat()
    assert (restored_status.st_dev, restored_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not tombstone.exists()


def test_remove_cleans_partial_transaction_record_before_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    installed = install_theme_package(source, root)
    target = root / "web" / "custom-web"
    record = root / "web" / ".sdsctl-removal-record-custom-web"
    real_write = lifecycle._write_all

    def fail_partial_record(descriptor: int, content: bytes) -> None:
        assert os.write(descriptor, content[:5]) == 5
        raise OSError("injected partial removal record write")

    monkeypatch.setattr(lifecycle, "_write_all", fail_partial_record)

    with pytest.raises(OSError, match="partial removal record write"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    monkeypatch.setattr(lifecycle, "_write_all", real_write)
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not record.exists()
    assert not any(path.name.startswith(".sdsctl-purge-") for path in target.parent.iterdir())


def test_recovery_completes_recorded_invalid_package_removal(tmp_path: Path) -> None:
    root = tmp_path / "themes"
    interface_root = root / "web"
    target = interface_root / "broken-theme"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("broken", encoding="utf-8")
    target_status = target.stat()
    target_identity = (target_status.st_dev, target_status.st_ino)
    tombstone = interface_root / ".sdsctl-remove-broken-theme"
    token = "a" * 32
    record_path = interface_root / f".sdsctl-removal-record-broken-theme--{token}"
    lifecycle._create_removal_record(
        record_path,
        interface="web",
        identifier="broken-theme",
        token=token,
        target_identity=target_identity,
    )
    lifecycle._rename_noreplace(target, tombstone)

    lifecycle._recover_interface(interface_root)

    assert not target.exists()
    assert not tombstone.exists()
    assert not record_path.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in interface_root.iterdir())


def test_recovery_preserves_incompatible_removal_and_rollback_artifacts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    source = _web_theme(tmp_path / "source")
    install_theme_package(source, root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    tombstone = interface_root / ".sdsctl-remove-custom-web"
    token = "b" * 32
    record_path = interface_root / f".sdsctl-removal-record-custom-web--{token}"
    shutil.copytree(target, rollback)
    target_status = target.stat()
    lifecycle._create_removal_record(
        record_path,
        interface="web",
        identifier="custom-web",
        token=token,
        target_identity=(target_status.st_dev, target_status.st_ino),
    )
    lifecycle._rename_noreplace(target, tombstone)

    with pytest.raises(ThemeLifecycleError, match="incompatible pending lifecycle"):
        lifecycle._recover_interface(interface_root)

    assert not target.exists()
    assert rollback.is_dir()
    assert tombstone.is_dir()
    assert record_path.is_dir()


def test_install_preserves_populated_replacement_of_randomized_root_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    foreign = tmp_path / "foreign-root-candidate"
    foreign.mkdir()
    (foreign / "marker.txt").write_text("foreign root", encoding="utf-8")
    displaced = tmp_path / "displaced-root-candidate"
    prefix = f".sdsctl-root-create-{root.name}--"
    state = _swap_tokenized_directory_after_mkdir(
        monkeypatch,
        prefix=prefix,
        foreign=foreign,
        displaced=displaced,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    entry = state["entry"]
    assert entry is not None
    assert _is_tokenized_artifact(entry.name, prefix)
    assert (entry / "marker.txt").read_text(encoding="utf-8") == "foreign root"
    assert not (entry / "web" / "custom-web").exists()
    assert displaced.is_dir()
    assert not root.exists()


def test_install_preserves_populated_replacement_of_randomized_interface_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    root.mkdir()
    foreign = tmp_path / "foreign-interface-candidate"
    foreign.mkdir()
    (foreign / "marker.txt").write_text("foreign interface", encoding="utf-8")
    displaced = tmp_path / "displaced-interface-candidate"
    prefix = ".sdsctl-interface-create-web--"
    state = _swap_tokenized_directory_after_mkdir(
        monkeypatch,
        prefix=prefix,
        foreign=foreign,
        displaced=displaced,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    entry = state["entry"]
    assert entry is not None
    assert _is_tokenized_artifact(entry.name, prefix)
    assert (entry / "marker.txt").read_text(encoding="utf-8") == "foreign interface"
    assert not (entry / "custom-web").exists()
    assert displaced.is_dir()
    assert not (root / "web").exists()


def test_install_rejects_existing_root_replacement_after_initial_lstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = (tmp_path / "themes").absolute()
    root.mkdir()
    foreign = tmp_path / "foreign-root"
    foreign.mkdir()
    foreign.chmod(0o755)
    (foreign / "marker.txt").write_text("foreign root", encoding="utf-8")
    displaced = tmp_path / "displaced-root"
    real_lstat = Path.lstat
    replaced = False

    def replace_after_initial_lstat(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal replaced
        result = real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if not replaced and path.absolute() == root:
            root.rename(displaced)
            foreign.rename(root)
            replaced = True
        return result

    monkeypatch.setattr(Path, "lstat", replace_after_initial_lstat)

    with pytest.raises(ThemeLifecycleError, match="changed"):
        install_theme_package(source, root)

    assert replaced
    assert (root / "marker.txt").read_text(encoding="utf-8") == "foreign root"
    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    assert {entry.name for entry in root.iterdir()} == {"marker.txt"}
    assert displaced.is_dir()


def test_install_rejects_existing_interface_replacement_after_initial_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    interface_root = root / "web"
    interface_root.mkdir(parents=True)
    root_status = root.stat()
    root_identity = (root_status.st_dev, root_status.st_ino)
    foreign = tmp_path / "foreign-interface"
    foreign.mkdir()
    foreign.chmod(0o755)
    (foreign / "marker.txt").write_text("foreign interface", encoding="utf-8")
    displaced = tmp_path / "displaced-interface"
    real_stat = lifecycle.os.stat
    replaced = False

    def replace_after_initial_stat(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replaced
        result = real_stat(  # type: ignore[call-overload]
            path,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
        )
        if (
            not replaced and dir_fd is not None and os.fsdecode(path) == "web"  # type: ignore[arg-type]
        ):
            parent_status = os.fstat(dir_fd)
            if (parent_status.st_dev, parent_status.st_ino) == root_identity:
                interface_root.rename(displaced)
                foreign.rename(interface_root)
                replaced = True
        return result

    monkeypatch.setattr(lifecycle.os, "stat", replace_after_initial_stat)

    with pytest.raises(ThemeLifecycleError, match="binding changed"):
        install_theme_package(source, root)

    assert replaced
    assert (interface_root / "marker.txt").read_text(encoding="utf-8") == ("foreign interface")
    assert stat.S_IMODE(interface_root.stat().st_mode) == 0o755
    assert {entry.name for entry in interface_root.iterdir()} == {"marker.txt"}
    assert displaced.is_dir()


def test_install_preserves_populated_replacement_of_randomized_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    foreign = tmp_path / "foreign-stage"
    foreign.mkdir()
    (foreign / "marker.txt").write_text("foreign stage", encoding="utf-8")
    displaced = tmp_path / "displaced-stage"
    prefix = ".sdsctl-stage-custom-web--"
    state = _swap_tokenized_directory_after_mkdir(
        monkeypatch,
        prefix=prefix,
        foreign=foreign,
        displaced=displaced,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, root)

    entry = state["entry"]
    assert entry is not None
    assert _is_tokenized_artifact(entry.name, prefix)
    assert (entry / "marker.txt").read_text(encoding="utf-8") == "foreign stage"
    assert not (root / "web" / "custom-web").exists()
    assert displaced.is_dir()


def test_remove_preserves_populated_replacement_of_randomized_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    installed = install_theme_package(_web_theme(tmp_path / "source"), root)
    target = root / "web" / "custom-web"
    target_status = target.stat()
    foreign = tmp_path / "foreign-removal-record"
    foreign.mkdir()
    (foreign / "marker.txt").write_text("foreign record", encoding="utf-8")
    displaced = tmp_path / "displaced-removal-record"
    prefix = ".sdsctl-removal-record-custom-web--"
    state = _swap_tokenized_directory_after_mkdir(
        monkeypatch,
        prefix=prefix,
        foreign=foreign,
        displaced=displaced,
    )

    with pytest.raises(ThemeLifecycleError):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    entry = state["entry"]
    assert entry is not None
    assert _is_tokenized_artifact(entry.name, prefix)
    assert (entry / "marker.txt").read_text(encoding="utf-8") == "foreign record"
    retained_status = target.stat()
    assert (retained_status.st_dev, retained_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not (root / "web" / ".sdsctl-remove-custom-web").exists()
    assert displaced.is_dir()


def test_remove_exact_cleans_record_after_post_mkdir_interruption_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    installed = install_theme_package(_web_theme(tmp_path / "source"), root)
    target = root / "web" / "custom-web"
    prefix = ".sdsctl-removal-record-custom-web--"
    real_mkdir = lifecycle.os.mkdir
    state: dict[str, Path | None] = {"entry": None}

    def interrupt_after_mkdir(
        path: object,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)  # type: ignore[arg-type]
        name = os.fsdecode(path)  # type: ignore[arg-type]
        if state["entry"] is None and dir_fd is not None and _is_tokenized_artifact(name, prefix):
            state["entry"] = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / name
            raise KeyboardInterrupt("injected post-removal-record-mkdir interruption")

    monkeypatch.setattr(lifecycle.os, "mkdir", interrupt_after_mkdir)

    with pytest.raises(KeyboardInterrupt, match="post-removal-record-mkdir"):
        remove_theme_package(
            root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    entry = state["entry"]
    assert entry is not None
    assert not entry.exists()
    assert validate_theme_package(target).summary.sha256 == installed.sha256
    assert not any(
        path.name.startswith(".sdsctl-removal-record-") for path in target.parent.iterdir()
    )
    assert not (target.parent / ".sdsctl-remove-custom-web").exists()

    monkeypatch.setattr(lifecycle.os, "mkdir", real_mkdir)
    removed = remove_theme_package(
        root,
        "web",
        "custom-web",
        confirmation="web/custom-web",
    )

    assert removed.identity == "web/custom-web"
    assert not target.exists()


def test_recovery_cleans_empty_tokenized_stage_but_preserves_partial_record(
    tmp_path: Path,
) -> None:
    interface_root = tmp_path / "themes" / "web"
    interface_root.mkdir(parents=True)
    empty_stage = interface_root / f".sdsctl-stage-custom-web--{'a' * 32}"
    empty_stage.mkdir()

    lifecycle._recover_interface(interface_root)

    assert not empty_stage.exists()

    partial_stage = interface_root / f".sdsctl-stage-custom-web--{'b' * 32}"
    partial_stage.mkdir()
    partial_record = partial_stage / ".sdsctl-stage.json"
    partial_record.write_text("{", encoding="utf-8")

    with pytest.raises(ThemeLifecycleError):
        lifecycle._recover_interface(interface_root)

    assert partial_stage.is_dir()
    assert partial_record.read_text(encoding="utf-8") == "{"


def test_recovery_cleans_empty_tokenized_removal_record_but_preserves_partial_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    installed = install_theme_package(_web_theme(tmp_path / "source"), root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    empty_record = interface_root / (f".sdsctl-removal-record-custom-web--{'c' * 32}")
    empty_record.mkdir()

    lifecycle._recover_interface(interface_root)

    assert not empty_record.exists()
    assert validate_theme_package(target).summary.sha256 == installed.sha256

    partial_record = interface_root / (f".sdsctl-removal-record-custom-web--{'d' * 32}")
    partial_record.mkdir()
    partial_manifest = partial_record / "manifest.json"
    partial_manifest.write_text("{", encoding="utf-8")

    with pytest.raises(ThemeLifecycleError):
        lifecycle._recover_interface(interface_root)

    assert partial_record.is_dir()
    assert partial_manifest.read_text(encoding="utf-8") == "{"
    assert validate_theme_package(target).summary.sha256 == installed.sha256


def test_install_rejects_hardlinked_lifecycle_lock_before_chmod(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    root.mkdir()
    external_lock = tmp_path / "external-lock"
    external_lock.write_text("external lock", encoding="utf-8")
    external_lock.chmod(0o640)
    lifecycle_lock = root / ".lifecycle.lock"
    os.link(external_lock, lifecycle_lock)
    before = external_lock.stat()
    assert before.st_nlink == 2

    with pytest.raises(ThemeLifecycleError, match="one private regular file"):
        install_theme_package(_web_theme(tmp_path / "source"), root)

    after = external_lock.stat()
    lock_after = lifecycle_lock.stat()
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode) == 0o640
    assert after.st_nlink == lock_after.st_nlink == 2
    assert (after.st_dev, after.st_ino) == (lock_after.st_dev, lock_after.st_ino)
    assert external_lock.read_text(encoding="utf-8") == "external lock"
    assert not (root / "web").exists()


def test_remove_private_tree_reraises_post_detach_interruption_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "private-parent"
    private = parent / "private"
    nested = private / "nested"
    nested.mkdir(parents=True)
    (private / "marker.txt").write_text("private", encoding="utf-8")
    (nested / "child.txt").write_text("child", encoding="utf-8")
    real_rename = lifecycle._rename_noreplace
    interrupted = False

    def detach_then_interrupt(
        source: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal interrupted
        real_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if Path(source).name == private.name and Path(destination).name.startswith(
            ".sdsctl-purge-"
        ):
            interrupted = True
            raise KeyboardInterrupt("injected post-private-detach interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", detach_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-private-detach"):
        lifecycle._remove_private_tree(private)

    assert interrupted
    assert not private.exists()
    assert not any(path.name.startswith(".sdsctl-purge-") for path in parent.iterdir())


def test_remove_private_tree_reraises_post_rmdir_interruption_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "private-parent"
    private = parent / "private"
    private.mkdir(parents=True)
    (private / "marker.txt").write_text("private", encoding="utf-8")
    real_rmdir = lifecycle.os.rmdir
    interrupted = False

    def rmdir_then_interrupt(
        path: str | bytes,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal interrupted
        real_rmdir(path, dir_fd=dir_fd)
        if Path(path).name.startswith(".sdsctl-purge-"):
            interrupted = True
            raise KeyboardInterrupt("injected post-rmdir interruption")

    monkeypatch.setattr(lifecycle.os, "rmdir", rmdir_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-rmdir"):
        lifecycle._remove_private_tree(private)

    assert interrupted
    assert not private.exists()
    assert not any(path.name.startswith(".sdsctl-purge-") for path in parent.iterdir())


@pytest.mark.parametrize(
    ("detach_critical", "expected_message"),
    ((True, "critical detach"), (False, "critical cleanup")),
)
def test_remove_private_tree_prefers_critical_detach_or_cleanup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    detach_critical: bool,
    expected_message: str,
) -> None:
    parent = tmp_path / "private-parent"
    private = parent / "private"
    private.mkdir(parents=True)
    (private / "marker.txt").write_text("private", encoding="utf-8")
    real_rename = lifecycle._rename_noreplace
    real_rmdir = lifecycle.os.rmdir

    def detach_then_fail(
        source: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        real_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if Path(source).name == private.name and Path(destination).name.startswith(
            ".sdsctl-purge-"
        ):
            if detach_critical:
                raise KeyboardInterrupt("critical detach")
            raise OSError("ordinary detach")

    def rmdir_then_fail(
        path: str | bytes,
        *,
        dir_fd: int | None = None,
    ) -> None:
        real_rmdir(path, dir_fd=dir_fd)
        if Path(path).name.startswith(".sdsctl-purge-"):
            if detach_critical:
                raise OSError("ordinary cleanup")
            raise KeyboardInterrupt("critical cleanup")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", detach_then_fail)
    monkeypatch.setattr(lifecycle.os, "rmdir", rmdir_then_fail)

    with pytest.raises(KeyboardInterrupt, match=expected_message):
        lifecycle._remove_private_tree(private)

    assert not private.exists()
    assert not any(path.name.startswith(".sdsctl-purge-") for path in parent.iterdir())


def test_detach_private_entry_reraises_post_success_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "detach-parent"
    parent.mkdir()
    source = parent / "entry"
    source.write_text("retained", encoding="utf-8")
    source_status = source.stat()
    identity = (stat.S_IFMT(source_status.st_mode), source_status.st_dev, source_status.st_ino)
    real_rename = lifecycle._rename_noreplace
    detached_name: str | None = None

    def rename_then_interrupt(
        source_name: str | Path,
        destination_name: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal detached_name
        real_rename(
            source_name,
            destination_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        detached_name = os.fsdecode(destination_name)
        raise KeyboardInterrupt("injected post-entry-detach interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", rename_then_interrupt)
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt, match="post-entry-detach"):
            lifecycle._detach_private_entry(descriptor, source.name, identity)
    finally:
        os.close(descriptor)

    assert detached_name is not None
    detached = parent / detached_name
    assert not source.exists()
    assert detached.read_text(encoding="utf-8") == "retained"
    detached_status = detached.stat()
    assert (detached_status.st_dev, detached_status.st_ino) == (
        source_status.st_dev,
        source_status.st_ino,
    )


def test_private_quarantine_reraises_post_success_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "publication"
    source.mkdir()
    (source / "marker.txt").write_text("publication", encoding="utf-8")
    source_status = source.stat()
    container = tmp_path / "stage"
    container.mkdir()
    container_status = container.stat()
    quarantine = container / ".failed-publication"
    real_rename = lifecycle._rename_noreplace

    def rename_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        raise KeyboardInterrupt("injected post-private-quarantine interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", rename_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-private-quarantine"):
        lifecycle._move_entry_to_private_quarantine(
            source,
            quarantine,
            container=container,
            container_identity=(container_status.st_dev, container_status.st_ino),
        )

    assert not source.exists()
    assert (quarantine / "marker.txt").read_text(encoding="utf-8") == "publication"
    quarantine_status = quarantine.stat()
    assert (quarantine_status.st_dev, quarantine_status.st_ino) == (
        source_status.st_dev,
        source_status.st_ino,
    )


def test_preserved_conflict_reraises_post_success_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "foreign"
    source.mkdir()
    (source / "marker.txt").write_text("foreign", encoding="utf-8")
    source_status = source.stat()
    conflict = tmp_path / ".sdsctl-conflict-custom-web"
    real_rename = lifecycle._rename_noreplace

    def rename_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        raise KeyboardInterrupt("injected post-conflict-preservation interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", rename_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-conflict-preservation"):
        lifecycle._move_entry_to_preserved_conflict(source, conflict)

    assert not source.exists()
    assert (conflict / "marker.txt").read_text(encoding="utf-8") == "foreign"
    conflict_status = conflict.stat()
    assert (conflict_status.st_dev, conflict_status.st_ino) == (
        source_status.st_dev,
        source_status.st_ino,
    )


def test_restore_previous_package_reraises_post_success_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback = _web_theme(tmp_path / "rollback")
    target = tmp_path / "custom-web"
    rollback_status = rollback.stat()
    previous_identity = (rollback_status.st_dev, rollback_status.st_ino)
    expected_digest = _independent_package_digest(rollback)
    real_rename = lifecycle._rename_noreplace

    def rename_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        raise KeyboardInterrupt("injected post-package-restoration interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", rename_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-package-restoration"):
        lifecycle._try_restore_previous_package(
            rollback,
            target,
            previous_identity,
        )

    assert not rollback.exists()
    target_status = target.stat()
    assert (target_status.st_dev, target_status.st_ino) == previous_identity
    assert _independent_package_digest(target) == expected_digest


def test_rollback_recovery_reraises_post_promotion_interruption_with_valid_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    installed = install_theme_package(_web_theme(tmp_path / "source"), root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    target_status = target.stat()
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    target.rename(rollback)
    real_rename = lifecycle._rename_noreplace
    interrupted = False

    def promote_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal interrupted
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if Path(source_path) == rollback and Path(destination_path) == target:
            interrupted = True
            raise KeyboardInterrupt("injected post-rollback-promotion interruption")

    monkeypatch.setattr(lifecycle, "_rename_noreplace", promote_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-rollback-promotion"):
        lifecycle._recover_interface(interface_root)

    assert interrupted
    assert not rollback.exists()
    promoted_status = target.stat()
    assert (promoted_status.st_dev, promoted_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(target).summary.sha256 == installed.sha256


def test_rollback_recovery_reraises_post_return_interruption_after_failed_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    installed = install_theme_package(_web_theme(tmp_path / "source"), root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    target_status = target.stat()
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    target.rename(rollback)
    real_assertion = lifecycle._assert_package_image
    real_rename = lifecycle._rename_noreplace
    promoted = False
    validation_failed = False
    returned_then_interrupted = False

    def fail_promoted_validation(
        path: Path,
        image: lifecycle._PackageImage,
        *,
        require_identity: bool,
    ) -> lifecycle._PackageImage:
        nonlocal validation_failed
        if promoted and path == target and not validation_failed:
            validation_failed = True
            raise ThemeLifecycleError("injected promoted validation failure")
        return real_assertion(path, image, require_identity=require_identity)

    def return_then_interrupt(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal promoted, returned_then_interrupted
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if Path(source_path) == rollback and Path(destination_path) == target:
            promoted = True
        elif promoted and Path(source_path) == target and Path(destination_path) == rollback:
            returned_then_interrupted = True
            raise KeyboardInterrupt("injected post-rollback-return interruption")

    monkeypatch.setattr(lifecycle, "_assert_package_image", fail_promoted_validation)
    monkeypatch.setattr(lifecycle, "_rename_noreplace", return_then_interrupt)

    with pytest.raises(KeyboardInterrupt, match="post-rollback-return"):
        lifecycle._recover_interface(interface_root)

    assert promoted
    assert validation_failed
    assert returned_then_interrupted
    assert not target.exists()
    rollback_status = rollback.stat()
    assert (rollback_status.st_dev, rollback_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert _independent_package_digest(rollback) == installed.sha256


def test_rollback_recovery_prefers_critical_validation_over_ordinary_return_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "themes"
    installed = install_theme_package(_web_theme(tmp_path / "source"), root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    target_status = target.stat()
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    target.rename(rollback)
    real_assertion = lifecycle._assert_package_image
    real_rename = lifecycle._rename_noreplace
    promoted = False
    validation_interrupted = False

    def interrupt_promoted_validation(
        path: Path,
        image: lifecycle._PackageImage,
        *,
        require_identity: bool,
    ) -> lifecycle._PackageImage:
        nonlocal validation_interrupted
        if promoted and path == target and not validation_interrupted:
            validation_interrupted = True
            raise KeyboardInterrupt("critical promoted validation")
        return real_assertion(path, image, require_identity=require_identity)

    def promote_and_fail_return(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal promoted
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if Path(source_path) == rollback and Path(destination_path) == target:
            promoted = True
        elif promoted and Path(source_path) == target and Path(destination_path) == rollback:
            raise OSError("ordinary rollback return failure")

    monkeypatch.setattr(lifecycle, "_assert_package_image", interrupt_promoted_validation)
    monkeypatch.setattr(lifecycle, "_rename_noreplace", promote_and_fail_return)

    with pytest.raises(KeyboardInterrupt, match="critical promoted validation") as interrupted:
        lifecycle._recover_interface(interface_root)

    assert promoted
    assert validation_interrupted
    assert isinstance(interrupted.value.__cause__, OSError)
    assert "rollback return" in str(interrupted.value.__cause__)
    assert not target.exists()
    rollback_status = rollback.stat()
    assert (rollback_status.st_dev, rollback_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert _independent_package_digest(rollback) == installed.sha256


@pytest.mark.parametrize(
    ("critical_stage", "expected_message"),
    (("promotion", "critical promotion"), ("validation", "critical validation")),
)
def test_rollback_recovery_prefers_critical_promotion_or_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    critical_stage: str,
    expected_message: str,
) -> None:
    root = tmp_path / "themes"
    installed = install_theme_package(_web_theme(tmp_path / "source"), root)
    interface_root = root / "web"
    target = interface_root / "custom-web"
    target_status = target.stat()
    rollback = interface_root / ".sdsctl-rollback-custom-web"
    target.rename(rollback)
    real_assertion = lifecycle._assert_package_image
    real_rename = lifecycle._rename_noreplace
    promoted = False
    validation_failed = False

    def fail_promoted_validation(
        path: Path,
        image: lifecycle._PackageImage,
        *,
        require_identity: bool,
    ) -> lifecycle._PackageImage:
        nonlocal validation_failed
        if promoted and path == target and not validation_failed:
            validation_failed = True
            if critical_stage == "validation":
                raise KeyboardInterrupt("critical validation")
            raise ThemeLifecycleError("ordinary validation failure")
        return real_assertion(path, image, require_identity=require_identity)

    def promote_then_fail(
        source_path: str | Path,
        destination_path: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal promoted
        real_rename(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if Path(source_path) == rollback and Path(destination_path) == target:
            promoted = True
            if critical_stage == "promotion":
                raise KeyboardInterrupt("critical promotion")
            raise OSError("ordinary promotion failure")

    monkeypatch.setattr(lifecycle, "_assert_package_image", fail_promoted_validation)
    monkeypatch.setattr(lifecycle, "_rename_noreplace", promote_then_fail)

    with pytest.raises(KeyboardInterrupt, match=expected_message):
        lifecycle._recover_interface(interface_root)

    assert promoted
    assert validation_failed
    assert not target.exists()
    rollback_status = rollback.stat()
    assert (rollback_status.st_dev, rollback_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert _independent_package_digest(rollback) == installed.sha256


def test_install_root_swap_during_collision_validation_publishes_nowhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _web_theme(tmp_path / "source")
    configured_root = tmp_path / "themes"
    retained_root = tmp_path / "retained-root"
    replacement_root = tmp_path / "replacement-root"
    replacement_root.mkdir()
    (replacement_root / "marker.txt").write_text("replacement", encoding="utf-8")
    real_validation = lifecycle._validate_candidate_collision
    swapped = False

    def validate_then_swap_root(
        package: lifecycle.ValidatedThemePackage,
        root: Path,
        *,
        excluded: Path | None,
    ) -> None:
        nonlocal swapped
        real_validation(package, root, excluded=excluded)
        if not swapped:
            configured_root.rename(retained_root)
            replacement_root.rename(configured_root)
            swapped = True

    monkeypatch.setattr(
        lifecycle,
        "_validate_candidate_collision",
        validate_then_swap_root,
    )

    with pytest.raises(ThemeLifecycleError):
        install_theme_package(source, configured_root)

    assert swapped
    assert not (retained_root / "web" / "custom-web").exists()
    assert not (configured_root / "web" / "custom-web").exists()
    assert (configured_root / "marker.txt").read_text(encoding="utf-8") == "replacement"
    assert not any(
        path.name.startswith(".sdsctl-stage-") for path in (retained_root / "web").iterdir()
    )


def test_remove_root_swap_during_record_creation_retains_target_without_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_root = tmp_path / "themes"
    installed = install_theme_package(
        _web_theme(tmp_path / "source"),
        configured_root,
    )
    configured_target = configured_root / "web" / "custom-web"
    target_status = configured_target.stat()
    retained_root = tmp_path / "retained-root"
    replacement_root = tmp_path / "replacement-root"
    replacement_root.mkdir()
    (replacement_root / "marker.txt").write_text("replacement", encoding="utf-8")
    real_create = lifecycle._create_removal_record
    swapped = False

    def swap_root_then_create_record(
        path: Path,
        *,
        interface: lifecycle.ThemeInterface,
        identifier: str,
        token: str,
        target_identity: tuple[int, int],
        parent: lifecycle._OpenedPackageDirectory | None = None,
    ) -> lifecycle._RemovalRecord:
        nonlocal swapped
        if not swapped:
            configured_root.rename(retained_root)
            replacement_root.rename(configured_root)
            swapped = True
        return real_create(
            path,
            interface=interface,
            identifier=identifier,
            token=token,
            target_identity=target_identity,
            parent=parent,
        )

    monkeypatch.setattr(
        lifecycle,
        "_create_removal_record",
        swap_root_then_create_record,
    )

    with pytest.raises(ThemeLifecycleError):
        remove_theme_package(
            configured_root,
            "web",
            "custom-web",
            confirmation="web/custom-web",
        )

    retained_target = retained_root / "web" / "custom-web"
    assert swapped
    retained_status = retained_target.stat()
    assert (retained_status.st_dev, retained_status.st_ino) == (
        target_status.st_dev,
        target_status.st_ino,
    )
    assert validate_theme_package(retained_target).summary.sha256 == installed.sha256
    assert not any(
        path.name.startswith(".sdsctl-removal-record-") for path in retained_target.parent.iterdir()
    )
    assert not (retained_target.parent / ".sdsctl-remove-custom-web").exists()
    assert not (configured_root / "web").exists()
    assert (configured_root / "marker.txt").read_text(encoding="utf-8") == "replacement"


@pytest.mark.parametrize(
    "artifact_name",
    (
        ".sdsctl-conflict-custom-web",
        f".sdsctl-conflict-capability-{'e' * 32}",
    ),
)
def test_conflict_diagnostic_uses_configured_path(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    root = tmp_path / "themes"
    interface_root = root / "web"
    interface_root.mkdir(parents=True)
    artifact = interface_root / artifact_name
    artifact.mkdir()
    (artifact / "marker.txt").write_text("preserved", encoding="utf-8")

    with pytest.raises(ThemeLifecycleError) as blocked:
        install_theme_package(_web_theme(tmp_path / "source"), root)

    message = str(blocked.value)
    assert str(artifact) in message
    assert "/proc/self/fd" not in message
    assert (artifact / "marker.txt").read_text(encoding="utf-8") == "preserved"


def test_recovery_preserves_and_blocks_arbitrary_populated_purge(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    interface_root = root / "web"
    interface_root.mkdir(parents=True)
    purge = interface_root / ".sdsctl-purge-arbitrary-foreign-artifact"
    purge.mkdir()
    marker = purge / "marker.txt"
    marker.write_text("foreign purge", encoding="utf-8")

    with pytest.raises(
        ThemeLifecycleError,
        match="unauthenticated interrupted purge",
    ) as blocked:
        install_theme_package(_web_theme(tmp_path / "source"), root)

    message = str(blocked.value)
    assert str(purge) in message
    assert "/proc/self/fd" not in message
    assert marker.read_text(encoding="utf-8") == "foreign purge"
    assert not (interface_root / "custom-web").exists()


def test_existing_root_parent_populated_purge_blocks_before_root_mutation(
    tmp_path: Path,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    root.mkdir()
    root.chmod(0o755)
    root_status = root.stat()
    purge = tmp_path / ".sdsctl-purge-foreign-root-parent"
    purge.mkdir()
    marker = purge / "marker.txt"
    marker.write_text("foreign root-parent purge", encoding="utf-8")

    with pytest.raises(
        ThemeLifecycleError,
        match="parent has an unauthenticated interrupted purge",
    ) as blocked:
        install_theme_package(source, root)

    message = str(blocked.value)
    assert str(purge) in message
    assert "/proc/self/fd" not in message
    assert marker.read_text(encoding="utf-8") == "foreign root-parent purge"
    retained_root = root.stat()
    assert (retained_root.st_dev, retained_root.st_ino) == (
        root_status.st_dev,
        root_status.st_ino,
    )
    assert stat.S_IMODE(retained_root.st_mode) == 0o755
    assert tuple(root.iterdir()) == ()


def test_existing_interface_parent_populated_purge_blocks_before_interface_mutation(
    tmp_path: Path,
) -> None:
    source = _web_theme(tmp_path / "source")
    root = tmp_path / "themes"
    interface_root = root / "web"
    interface_root.mkdir(parents=True)
    root.chmod(0o700)
    interface_root.chmod(0o700)
    lifecycle_lock = root / ".lifecycle.lock"
    lifecycle_lock.write_text("", encoding="utf-8")
    lifecycle_lock.chmod(0o600)
    purge = root / ".sdsctl-purge-foreign-interface-parent"
    purge.mkdir()
    marker = purge / "marker.txt"
    marker.write_text("foreign interface-parent purge", encoding="utf-8")
    root_entries = {path.name for path in root.iterdir()}
    interface_status = interface_root.stat()

    with pytest.raises(
        ThemeLifecycleError,
        match="parent has an unauthenticated interrupted purge",
    ) as blocked:
        install_theme_package(source, root)

    message = str(blocked.value)
    assert str(purge) in message
    assert "/proc/self/fd" not in message
    assert marker.read_text(encoding="utf-8") == "foreign interface-parent purge"
    assert {path.name for path in root.iterdir()} == root_entries
    retained_interface = interface_root.stat()
    assert (retained_interface.st_dev, retained_interface.st_ino) == (
        interface_status.st_dev,
        interface_status.st_ino,
    )
    assert stat.S_IMODE(retained_interface.st_mode) == 0o700
    assert tuple(interface_root.iterdir()) == ()
    lock_status = lifecycle_lock.stat()
    assert stat.S_IMODE(lock_status.st_mode) == 0o600
    assert lock_status.st_nlink == 1


def test_remove_handles_deep_invalid_package_without_recursive_cleanup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "themes"
    target = root / "web" / "deep-invalid"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("broken", encoding="utf-8")
    nested = target
    for _index in range(1100):
        nested = nested / "a"
        nested.mkdir()

    removed = remove_theme_package(
        root,
        "web",
        "deep-invalid",
        confirmation="web/deep-invalid",
    )

    assert removed.identity == "web/deep-invalid"
    assert not target.exists()
    assert not any(path.name.startswith(".sdsctl-") for path in (root / "web").iterdir())


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
