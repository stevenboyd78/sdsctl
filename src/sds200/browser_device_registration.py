"""Explicit, new-directory-only Chromium native-host registration for experiments.

No personal browser directory, policy, trust store, state database or service is
modified. Source bundles must match this installed runtime byte for byte.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .browser_device_bundle import NATIVE_HOST, _artifacts, _json, browser_extension_identity
from .browser_device_profile import _platform, _profile_inputs, _write, inspect_browser_profile
from .browser_device_recovery import RecoveryMode
from .browser_device_store import BrowserDeviceStore
from .exceptions import ConfigurationError

MAINTENANCE_MARKER = ".sdsctl-browser-maintenance.json"


class BrowserRegistrationError(ConfigurationError):
    """Only fixed diagnostics, never input paths, bundle contents or credentials."""


@dataclass(frozen=True, slots=True)
class BrowserRegistration:
    extension_id: str
    identity: str

    @property
    def setup_url(self) -> str:
        return f"chrome-extension://{self.extension_id}/setup.html"


def _matches(path: Path, expected: bytes, mode: int = 0o600) -> None:
    BrowserDeviceStore(path)._check(database=False)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1
                or info.st_size != len(expected) or stream.read(len(expected) + 1) != expected):
            raise ValueError()


def _validated_bundle(
    bundle: Path, profile: Path, public_key: Path, *, fresh: bool = True,
) -> tuple[BrowserRegistration, bytes]:
    """Canonical files AND current native state; ordinary callers stay strict."""
    inspected = inspect_browser_profile(profile)
    if fresh and (inspected.mode is not RecoveryMode.ACTIVE or inspected.revision != 1):
        raise ValueError()
    result, manifest, trust_sha256 = _canonical_bundle_files(bundle, profile, public_key)
    if inspected.identity != result.identity or inspected.trust_sha256 != trust_sha256:
        raise ValueError()
    return result, manifest


def _canonical_bundle_files(
    bundle: Path, profile: Path, public_key: Path,
) -> tuple[BrowserRegistration, bytes, str]:
    """Private-input and installed-runtime byte checks ONLY, not a runnable grant.

    This internal representation never opens or validates recovery.sqlite. A
    historical reader independently checks retained commit anchors and ownership;
    normal registration/inspection must use _validated_bundle with live checks.
    The final value is the validated trust digest, never a credential.
    """
    key = browser_extension_identity(public_key)
    config, trust_sha256 = _profile_inputs(profile)
    if config.extension_origin != f"chrome-extension://{key.extension_id}/":
        raise ValueError()
    artifacts = _artifacts(bundle, config, key, public_key)
    receipt = _json({
        "version": 1, "experimental": True, "extension_id": key.extension_id,
        "identity": config.identity, "public_key_sha256": key.public_key_sha256,
        "trust_sha256": trust_sha256,
        "files": {name: hashlib.sha256(body).hexdigest() for name, body in artifacts.items()},
    })
    # Do not trust a caller-edited receipt/hash to authorize arbitrary executable
    # files. Compare every byte to the canonical assets from this installed runtime.
    _matches(bundle / "bundle.json", receipt)
    if {p.name for p in bundle.iterdir()} != {
        "bundle.json", "extension", "native-host", "native_host.py", NATIVE_HOST + ".json",
    }:
        raise ValueError()
    if {p.name for p in (bundle / "extension").iterdir()} != {
        name.removeprefix("extension/") for name in artifacts if name.startswith("extension/")
    }:
        raise ValueError()
    for name, value in artifacts.items():
        _matches(bundle / name, value, 0o700 if name == "native-host" else 0o600)
    return (BrowserRegistration(key.extension_id, config.identity),
            artifacts[NATIVE_HOST + ".json"], trust_sha256)


def _receipt(result: BrowserRegistration, bundle: Path, profile: Path) -> bytes:
    return _json({
        "version": 1, "experimental": True, "bundle": str(bundle),
        "native_profile": str(profile), "extension_id": result.extension_id,
        "identity": result.identity, "setup_url": result.setup_url,
    })


def inspect_browser_registration(
    root: Path, *, bundle: Path, profile: Path, public_key: Path,
) -> BrowserRegistration:
    """Offline, read-only validation of fresh OR used/paused dedicated registration.

    Chromium owns its internal files. Never parse/reset browser storage or call
    native status (which can persist clock correction) during this inspection.
    """
    try:
        _platform()
        # Lazy import avoids the internal handoff/startup dependency cycle. There
        # is no public ignore-guard flag: every proof is revalidated read-only.
        from .browser_device_continuation_intent import has_continuation_intent
        from .browser_device_guard_release import check_paused_guard_release, has_guard_release

        # A recorded intent is not successor activation, even when complete.
        if has_continuation_intent(root):
            raise ValueError()
        if ((root / MAINTENANCE_MARKER).exists() or (root / MAINTENANCE_MARKER).is_symlink()
                or has_guard_release(root)):
            check_paused_guard_release(root, bundle=bundle, profile=profile, public_key=public_key)
        return _inspect_registration_files(root, bundle=bundle, profile=profile,
                                            public_key=public_key)
    except Exception:
        raise BrowserRegistrationError(
            "Browser registration is invalid or unsafe; nothing was changed. "
            "Review its original runtime, canonical bundle, private profile and registration."
        ) from None


def _inspect_registration_files(
    root: Path, *, bundle: Path, profile: Path, public_key: Path,
) -> BrowserRegistration:
    """Canonical files only; callers MUST separately enforce maintenance guards.

    Internal stopped maintenance can inspect files behind its exact retained
    guard. Normal inspection/startup must always use inspect_browser_registration.
    """
    result, manifest = _validated_bundle(bundle, profile, public_key, fresh=False)
    _matches(root / ".sdsctl-browser-registration.json", _receipt(result, bundle, profile))
    hosts = root / "NativeMessagingHosts"
    _matches(hosts / (NATIVE_HOST + ".json"), manifest)
    if {entry.name for entry in hosts.iterdir()} != {NATIVE_HOST + ".json"}:
        raise ValueError()
    return result


def register_browser_directory(
    root: Path, *, bundle: Path, profile: Path, public_key: Path,
) -> BrowserRegistration:
    """Create only a new dedicated user-data directory; never load the extension.

    The registration becomes discoverable only when the selected Chromium uses
    this exact user-data directory. Existing directories are always refused,
    including this tool's own partial or complete prior output.
    """
    try:
        _platform()
        BrowserDeviceStore(root)._check(database=False)
        if root.exists() or root.is_symlink():
            raise FileExistsError()
        result, manifest = _validated_bundle(bundle, profile, public_key)
        receipt = _receipt(result, bundle, profile)
    except FileExistsError:
        raise BrowserRegistrationError(
            "Browser data directory already exists; refusing to overwrite or reuse it."
        ) from None
    except Exception:
        raise BrowserRegistrationError(
            "Browser registration inputs are invalid or unsafe; nothing was registered. "
            "Use a matching reviewed bundle, its original runtime and a fresh native profile."
        ) from None

    parent_fd = root_fd = hosts_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent_fd = os.open(root.parent, flags)
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError()
        os.mkdir(root.name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        root_fd = os.open(root.name, flags, dir_fd=parent_fd)
        opened = os.fstat(root_fd)
        if (opened.st_dev, opened.st_ino) != (root.stat().st_dev, root.stat().st_ino):
            raise ValueError()
        os.mkdir("NativeMessagingHosts", 0o700, dir_fd=root_fd)
        hosts_fd = os.open("NativeMessagingHosts", flags, dir_fd=root_fd)
        _write(hosts_fd, NATIVE_HOST + ".json", manifest)
        os.fsync(hosts_fd)
        _write(root_fd, ".sdsctl-browser-registration.json", receipt)
        os.fsync(root_fd)
        return result
    except Exception:
        raise BrowserRegistrationError(
            "Browser registration could not be confirmed. Retain any created directory "
            "for review; do not reuse or retry over it. No browser or service was started."
        ) from None
    finally:
        for descriptor in (hosts_fd, root_fd, parent_fd):
            if descriptor is not None:
                os.close(descriptor)
