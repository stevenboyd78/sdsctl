from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .exceptions import SDS200Error

HOME_ASSISTANT_INTEGRATION_DOMAIN = "sdsctl"
HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION = Path(
    "/homeassistant/custom_components/sdsctl"
)
HOME_ASSISTANT_INTEGRATION_ROLLBACK_NAME = ".sdsctl-rollback"
HOME_ASSISTANT_INTEGRATION_MAX_BYTES = 512 * 1024
HOME_ASSISTANT_INTEGRATION_BRIDGE_KEY = Path("/data/live-audio-bridge.key")

_RESOURCE_ROOT = "home_assistant_integration/custom_components/sdsctl"
_PYTHON_CACHE_DIRECTORY = "__pycache__"
_ARTIFACT_FILES = (
    "__init__.py",
    "client.py",
    "config_flow.py",
    "const.py",
    "diagnostics.py",
    "http.py",
    "manifest.json",
    "media_source.py",
    "playback.py",
    "sdsctl-logo.svg",
    "strings.json",
    "translations/en.json",
)


@dataclass(frozen=True, slots=True)
class HomeAssistantIntegrationImage:
    """One immutable, versioned custom-integration artifact."""

    version: str
    digest: str
    files: tuple[tuple[str, bytes], ...]

    @property
    def total_bytes(self) -> int:
        return sum(len(payload) for _name, payload in self.files)


@dataclass(frozen=True, slots=True)
class HomeAssistantIntegrationStatus:
    """Exact current and rollback identities without configuration secrets."""

    destination: Path
    current_version: str | None
    current_digest: str | None
    rollback_version: str | None
    rollback_digest: str | None


def built_in_home_assistant_integration_image() -> HomeAssistantIntegrationImage:
    """Read and validate the packaged first-party artifact exactly once."""

    root = files("sds200").joinpath(_RESOURCE_ROOT)
    payloads: list[tuple[str, bytes]] = []
    total = 0
    for relative in _ARTIFACT_FILES:
        resource = root.joinpath(relative)
        if not resource.is_file():
            raise SDS200Error(
                f"Packaged Home Assistant integration is missing {relative}."
            )
        payload = resource.read_bytes()
        total += len(payload)
        if total > HOME_ASSISTANT_INTEGRATION_MAX_BYTES:
            raise SDS200Error("Packaged Home Assistant integration exceeds its byte limit.")
        payloads.append((relative, payload))

    manifest = _manifest(payloads)
    if manifest.get("domain") != HOME_ASSISTANT_INTEGRATION_DOMAIN:
        raise SDS200Error("Packaged Home Assistant integration domain is invalid.")
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise SDS200Error("Packaged Home Assistant integration version is invalid.")
    return HomeAssistantIntegrationImage(
        version=version,
        digest=_image_digest(payloads),
        files=tuple(payloads),
    )


def inspect_home_assistant_integration(
    destination: str | Path = HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION,
) -> HomeAssistantIntegrationStatus:
    """Inspect current and rollback images without changing either."""

    target = _destination(destination)
    rollback = target.parent / HOME_ASSISTANT_INTEGRATION_ROLLBACK_NAME
    current = _read_directory_image(target) if target.exists() else None
    previous = _read_directory_image(rollback) if rollback.exists() else None
    return HomeAssistantIntegrationStatus(
        destination=target,
        current_version=None if current is None else current.version,
        current_digest=None if current is None else current.digest,
        rollback_version=None if previous is None else previous.version,
        rollback_digest=None if previous is None else previous.digest,
    )


def install_home_assistant_integration(
    destination: str | Path = HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION,
    *,
    confirmation_digest: str,
    replace: bool = False,
) -> HomeAssistantIntegrationStatus:
    """Install or update only after exact packaged-digest confirmation."""

    target = _destination(destination)
    image = built_in_home_assistant_integration_image()
    _confirm(confirmation_digest, image.digest, label="packaged integration")
    if not target.parent.exists():
        target.parent.mkdir(mode=0o755)
        _fsync_directory(target.parent.parent)
    rollback = target.parent / HOME_ASSISTANT_INTEGRATION_ROLLBACK_NAME
    if target.exists() and not replace:
        raise SDS200Error("Home Assistant integration already exists; use explicit update.")
    if not target.exists() and replace:
        raise SDS200Error("Home Assistant integration is absent; use explicit install.")
    if rollback.exists():
        raise SDS200Error(
            "A Home Assistant integration rollback image already exists; "
            "rollback or discard it first."
        )

    stage = _write_stage(target.parent, image)
    moved_current = False
    published = False
    try:
        if target.exists():
            os.replace(target, rollback)
            moved_current = True
        os.replace(stage, target)
        published = True
        _fsync_directory(target.parent)
        installed = _read_directory_image(target)
        if installed.digest != image.digest:
            raise SDS200Error("Home Assistant integration installed readback failed.")
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        failed = None
        if published and target.exists():
            failed = target.parent / f".sdsctl-failed-{secrets.token_hex(16)}"
            os.replace(target, failed)
        if moved_current and rollback.exists():
            os.replace(rollback, target)
            _fsync_directory(target.parent)
        if failed is not None and failed.exists():
            shutil.rmtree(failed)
        raise
    return inspect_home_assistant_integration(target)


def rollback_home_assistant_integration(
    destination: str | Path = HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION,
    *,
    confirmation_digest: str,
) -> HomeAssistantIntegrationStatus:
    """Restore the retained prior image and retain the displaced current image."""

    target = _destination(destination)
    rollback = target.parent / HOME_ASSISTANT_INTEGRATION_ROLLBACK_NAME
    if not rollback.exists():
        raise SDS200Error("No Home Assistant integration rollback image exists.")
    previous = _read_directory_image(rollback)
    _confirm(confirmation_digest, previous.digest, label="rollback integration")
    if not target.exists():
        os.replace(rollback, target)
        _fsync_directory(target.parent)
        return inspect_home_assistant_integration(target)

    _read_directory_image(target)
    swap = target.parent / f".sdsctl-swap-{secrets.token_hex(16)}"
    os.replace(target, swap)
    try:
        os.replace(rollback, target)
    except BaseException:
        if swap.exists() and not target.exists():
            os.replace(swap, target)
        raise
    try:
        os.replace(swap, rollback)
    except BaseException:
        os.replace(target, rollback)
        os.replace(swap, target)
        raise
    _fsync_directory(target.parent)
    return inspect_home_assistant_integration(target)


def remove_home_assistant_integration(
    destination: str | Path = HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION,
    *,
    confirmation_digest: str,
) -> HomeAssistantIntegrationStatus:
    """Remove the current image into the recoverable rollback slot."""

    target = _destination(destination)
    rollback = target.parent / HOME_ASSISTANT_INTEGRATION_ROLLBACK_NAME
    if not target.exists():
        raise SDS200Error("Home Assistant integration is already absent.")
    if rollback.exists():
        raise SDS200Error(
            "A rollback image already exists; rollback or discard it before removal."
        )
    current = _read_directory_image(target)
    _confirm(confirmation_digest, current.digest, label="installed integration")
    os.replace(target, rollback)
    _fsync_directory(target.parent)
    return inspect_home_assistant_integration(target)


def discard_home_assistant_integration_rollback(
    destination: str | Path = HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION,
    *,
    confirmation_digest: str,
) -> HomeAssistantIntegrationStatus:
    """Permanently remove only the exact confirmed retained rollback image."""

    target = _destination(destination)
    rollback = target.parent / HOME_ASSISTANT_INTEGRATION_ROLLBACK_NAME
    if not rollback.exists():
        raise SDS200Error("No Home Assistant integration rollback image exists.")
    image = _read_directory_image(rollback)
    _confirm(confirmation_digest, image.digest, label="rollback integration")
    shutil.rmtree(rollback)
    _fsync_directory(target.parent)
    return inspect_home_assistant_integration(target)


def read_home_assistant_integration_bridge_key(
    path: str | Path = HOME_ASSISTANT_INTEGRATION_BRIDGE_KEY,
) -> str:
    """Read the private key only for an explicit terminal operator action."""

    selected = Path(path)
    if not selected.is_absolute() or selected.is_symlink():
        raise SDS200Error("Home Assistant integration bridge-key path is unsafe.")
    observed = selected.stat()
    if not stat.S_ISREG(observed.st_mode) or observed.st_mode & 0o077:
        raise SDS200Error("Home Assistant integration bridge key is not private.")
    value = selected.read_text(encoding="ascii")
    normalized = value[:-1] if value.endswith("\n") else value
    if not 43 <= len(normalized) <= 512 or value not in {normalized, normalized + "\n"}:
        raise SDS200Error("Home Assistant integration bridge key is invalid.")
    return normalized


def home_assistant_integration_bridge_key_digest(
    path: str | Path = HOME_ASSISTANT_INTEGRATION_BRIDGE_KEY,
) -> str:
    """Return a non-secret confirmation digest for the current bridge key."""

    value = read_home_assistant_integration_bridge_key(path)
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def rotate_home_assistant_integration_bridge_key(
    path: str | Path = HOME_ASSISTANT_INTEGRATION_BRIDGE_KEY,
    *,
    confirmation_digest: str,
) -> str:
    """Atomically rotate the persistent key after exact current-digest confirmation."""

    selected = Path(path)
    current = read_home_assistant_integration_bridge_key(selected)
    _confirm(
        confirmation_digest,
        hashlib.sha256(current.encode("ascii")).hexdigest(),
        label="current bridge key",
    )
    replacement = secrets.token_urlsafe(32)
    temporary = selected.parent / f".{selected.name}.{secrets.token_hex(16)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        _write_all(descriptor, (replacement + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, selected)
        _fsync_directory(selected.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    if read_home_assistant_integration_bridge_key(selected) != replacement:
        raise SDS200Error("Home Assistant integration bridge-key rotation failed.")
    return replacement


def _destination(value: str | Path) -> Path:
    target = Path(value)
    if not target.is_absolute():
        raise ValueError("Home Assistant integration destination must be absolute.")
    if (
        target.name != HOME_ASSISTANT_INTEGRATION_DOMAIN
        or target.parent.name != "custom_components"
    ):
        raise ValueError(
            "Home Assistant integration destination must end in custom_components/sdsctl."
        )
    if target.parent.is_symlink() or target.is_symlink():
        raise SDS200Error("Home Assistant integration lifecycle refuses symlinks.")
    configuration_root = target.parent.parent
    if (
        configuration_root.is_symlink()
        or not configuration_root.exists()
        or not configuration_root.is_dir()
    ):
        raise SDS200Error("Home Assistant configuration directory is unavailable.")
    if target.parent.exists() and not target.parent.is_dir():
        raise SDS200Error("Home Assistant custom_components path is not a directory.")
    return target


def _manifest(payloads: list[tuple[str, bytes]]) -> dict[str, object]:
    try:
        payload = dict(payloads)["manifest.json"]
    except KeyError as error:
        raise SDS200Error("Home Assistant integration manifest is missing.") from error
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SDS200Error("Home Assistant integration manifest is invalid.") from error
    if not isinstance(decoded, dict):
        raise SDS200Error("Home Assistant integration manifest is invalid.")
    return decoded


def _image_digest(payloads: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, payload in sorted(payloads):
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _read_directory_image(root: Path) -> HomeAssistantIntegrationImage:
    if root.is_symlink() or not root.is_dir():
        raise SDS200Error("Home Assistant integration image is not a safe directory.")
    payloads: list[tuple[str, bytes]] = []
    total = 0
    for directory, directory_names, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in tuple(directory_names):
            child = directory_path / name
            if child.is_symlink():
                raise SDS200Error("Home Assistant integration image contains a symlink.")
            if name == _PYTHON_CACHE_DIRECTORY:
                total += _validated_python_cache_bytes(child)
                if total > HOME_ASSISTANT_INTEGRATION_MAX_BYTES:
                    raise SDS200Error(
                        "Home Assistant integration image exceeds its byte limit."
                    )
                directory_names.remove(name)
        for name in filenames:
            child = directory_path / name
            if child.is_symlink() or not child.is_file():
                raise SDS200Error("Home Assistant integration image contains an unsafe file.")
            relative = child.relative_to(root).as_posix()
            payload = child.read_bytes()
            total += len(payload)
            if total > HOME_ASSISTANT_INTEGRATION_MAX_BYTES:
                raise SDS200Error("Home Assistant integration image exceeds its byte limit.")
            payloads.append((relative, payload))
    manifest = _manifest(payloads)
    if manifest.get("domain") != HOME_ASSISTANT_INTEGRATION_DOMAIN:
        raise SDS200Error("Home Assistant integration image domain is invalid.")
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise SDS200Error("Home Assistant integration image version is invalid.")
    return HomeAssistantIntegrationImage(
        version=version,
        digest=_image_digest(payloads),
        files=tuple(sorted(payloads)),
    )


def _validated_python_cache_bytes(root: Path) -> int:
    """Validate bounded Core-created bytecode without adding it to identity."""

    total = 0
    for directory, directory_names, filenames in os.walk(root, followlinks=False):
        if directory_names:
            raise SDS200Error(
                "Home Assistant integration Python cache contains a directory."
            )
        directory_path = Path(directory)
        for name in filenames:
            child = directory_path / name
            if child.is_symlink() or not child.is_file() or child.suffix != ".pyc":
                raise SDS200Error(
                    "Home Assistant integration Python cache contains an unsafe file."
                )
            total += len(child.read_bytes())
    return total


def _write_stage(parent: Path, image: HomeAssistantIntegrationImage) -> Path:
    stage = parent / f".sdsctl-stage-{secrets.token_hex(16)}"
    stage.mkdir(mode=0o700)
    try:
        for relative, payload in image.files:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                _write_all(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        directories = [Path(directory) for directory, _names, _files in os.walk(stage)]
        for directory in directories:
            directory.chmod(0o755)
        for directory in reversed(directories):
            _fsync_directory(directory)
        observed = _read_directory_image(stage)
        if observed.digest != image.digest:
            raise SDS200Error("Home Assistant integration staging readback failed.")
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _confirm(provided: str, expected: str, *, label: str) -> None:
    if not isinstance(provided, str) or not secrets.compare_digest(provided, expected):
        raise SDS200Error(f"Exact {label} digest confirmation is required.")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Home Assistant integration staging write failed.")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicit lifecycle for the packaged sdsctl Home Assistant integration."
    )
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("artifact", help="Show packaged version and digest")
    commands.add_parser("bridge-key", help="Print the private key to this terminal")
    rotate_key = commands.add_parser(
        "rotate-bridge-key",
        help="Atomically rotate the private key without restarting the App or Core",
    )
    rotate_key.add_argument("--confirm", required=True, metavar="CURRENT_SHA256")
    for action in ("status", "install", "update", "rollback", "remove", "discard-rollback"):
        command = commands.add_parser(action)
        command.add_argument(
            "--destination",
            type=Path,
            default=HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION,
        )
        if action not in {"status"}:
            command.add_argument("--confirm", required=True, metavar="SHA256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "artifact":
            image = built_in_home_assistant_integration_image()
            print(f"version={image.version}")
            print(f"digest={image.digest}")
            print(f"bytes={image.total_bytes}")
            return 0
        if args.action == "bridge-key":
            print(f"key={read_home_assistant_integration_bridge_key()}")
            print(f"digest={home_assistant_integration_bridge_key_digest()}")
            return 0
        if args.action == "rotate-bridge-key":
            replacement = rotate_home_assistant_integration_bridge_key(
                confirmation_digest=args.confirm,
            )
            print(f"key={replacement}")
            print(
                "Restart the sdsctl App now, then complete Home Assistant "
                "integration reauthentication with this key."
            )
            return 0
        if args.action == "status":
            status = inspect_home_assistant_integration(args.destination)
        elif args.action in {"install", "update"}:
            status = install_home_assistant_integration(
                args.destination,
                confirmation_digest=args.confirm,
                replace=args.action == "update",
            )
        elif args.action == "rollback":
            status = rollback_home_assistant_integration(
                args.destination,
                confirmation_digest=args.confirm,
            )
        elif args.action == "remove":
            status = remove_home_assistant_integration(
                args.destination,
                confirmation_digest=args.confirm,
            )
        else:
            status = discard_home_assistant_integration_rollback(
                args.destination,
                confirmation_digest=args.confirm,
            )
    except (OSError, SDS200Error, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"destination={status.destination}")
    print(f"current_version={status.current_version or 'absent'}")
    print(f"current_digest={status.current_digest or 'absent'}")
    print(f"rollback_version={status.rollback_version or 'absent'}")
    print(f"rollback_digest={status.rollback_digest or 'absent'}")
    print("Home Assistant Core was not restarted or reloaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HOME_ASSISTANT_INTEGRATION_DEFAULT_DESTINATION",
    "HomeAssistantIntegrationImage",
    "HomeAssistantIntegrationStatus",
    "built_in_home_assistant_integration_image",
    "discard_home_assistant_integration_rollback",
    "home_assistant_integration_bridge_key_digest",
    "inspect_home_assistant_integration",
    "install_home_assistant_integration",
    "read_home_assistant_integration_bridge_key",
    "remove_home_assistant_integration",
    "rotate_home_assistant_integration_bridge_key",
    "rollback_home_assistant_integration",
]
