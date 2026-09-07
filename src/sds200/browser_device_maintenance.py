"""Explicit stopped-browser registration update/retirement, never state repair.

The caller selects trusted installed old/new runtimes. Old code is inspected by
that old interpreter, not authenticated by a caller-editable digest receipt.
Only two registration files change; credentials and Chromium state stay put.
An interrupted switch retains a private backup and a missing receipt or guard.
An error at final acknowledgement can also mean a valid but unconfirmed update.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from .browser_device_bundle import NATIVE_HOST, _json, browser_extension_identity
from .browser_device_native import _private_read
from .browser_device_profile import _platform, _write, inspect_browser_profile
from .browser_device_registration import (
    MAINTENANCE_MARKER,
    BrowserRegistration,
    _matches,
    _receipt,
    _validated_bundle,
)
from .browser_device_startup import _launch_lock
from .browser_device_store import BrowserDeviceStore
from .exceptions import ConfigurationError

RECEIPT = ".sdsctl-browser-registration.json"
HOST = NATIVE_HOST + ".json"
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PRIOR_CHECK = """import sys
from pathlib import Path
from sds200.browser_device_startup import check_browser_startup
check_browser_startup(Path(sys.argv[1]), bundle=Path(sys.argv[2]),
    profile=Path(sys.argv[3]), public_key=Path(sys.argv[4]), browser=Path(sys.argv[5]))
"""


class BrowserMaintenanceError(ConfigurationError):
    """Only fixed, redacted diagnostics."""


def _old_registration(
    root: Path, *, bundle: Path, profile: Path, public_key: Path, previous_python: Path,
) -> tuple[BrowserRegistration, bytes, bytes]:
    _platform()
    if (not previous_python.is_absolute() or not previous_python.is_file()
            or not os.access(previous_python, os.X_OK)):
        raise ValueError()
    # Inspect with a deliberately selected trusted interpreter, never one read
    # from a bundle/receipt. -I ignores PYTHONPATH and user site packages.
    checked = subprocess.run(
        [str(previous_python), "-I", "-c", _PRIOR_CHECK, str(root), str(bundle),
         str(profile), str(public_key), str(previous_python)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=20, check=False, env={"PATH": os.defpath, "LC_ALL": "C"},
    )
    if checked.returncode != 0:
        raise ValueError()
    key = browser_extension_identity(public_key)
    inspected = inspect_browser_profile(profile)
    result = BrowserRegistration(key.extension_id, inspected.identity)
    receipt = _receipt(result, bundle, profile)
    _matches(root / RECEIPT, receipt)
    manifest = _private_read(bundle, HOST, 16384)
    _matches(root / "NativeMessagingHosts" / HOST, manifest)
    return result, receipt, manifest


def maintain_browser_registration(
    root: Path, *, bundle: Path, profile: Path, public_key: Path, previous_python: Path,
    backup: Path, replacement_bundle: Path | None = None,
) -> BrowserRegistration:
    """Switch to a canonical same-identity bundle, or retire local registration.

    No systemctl, browser stop, native exchange, recursive deletion, credential
    rotation or automatic rollback. The new private backup must not exist.
    The old receipt is removed before changing the host. Incomplete registration
    or a retained guard blocks launch; never automatically roll back an uncertain
    final acknowledgement. Retirement retains the guard permanently. Neither
    action counts as server-side revocation or cryptographic erasure.
    """
    descriptors: list[int] = []
    try:
        _platform()
        BrowserDeviceStore(backup)._check(database=False)
        if backup.exists() or backup.is_symlink():
            raise ValueError()
        for path in (root, bundle, profile, replacement_bundle):
            if path is not None and (backup == path or backup.is_relative_to(path)):
                raise ValueError()
        if replacement_bundle == bundle:
            raise ValueError()
        guard = root / MAINTENANCE_MARKER
        if guard.exists() or guard.is_symlink():
            raise ValueError()
        # Validate ownership before _launch_lock, then repeat under its OS lock.
        BrowserDeviceStore(root / RECEIPT)._check()
        with _launch_lock(root):
            if guard.exists() or guard.is_symlink():
                raise ValueError()
            original, old_receipt, old_manifest = _old_registration(
                root, bundle=bundle, profile=profile, public_key=public_key,
                previous_python=previous_python,
            )
            replacement = None
            if replacement_bundle is not None:
                result, manifest = _validated_bundle(
                    replacement_bundle, profile, public_key, fresh=False,
                )
                if result != original:
                    raise ValueError()
                replacement = (result, manifest, _receipt(result, replacement_bundle, profile))
            # All protected inputs validated before creating the review backup.
            parent_fd = os.open(backup.parent, _FLAGS)
            descriptors.append(parent_fd)
            parent = os.fstat(parent_fd)
            if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
                raise ValueError()
            os.mkdir(backup.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            backup_fd = os.open(backup.name, _FLAGS, dir_fd=parent_fd)
            descriptors.append(backup_fd)
            _write(backup_fd, "registration.before.json", old_receipt)
            _write(backup_fd, "native-host.before.json", old_manifest)
            operation = _json({
                "version": 1, "experimental": True,
                "operation": "update" if replacement else "retire",
                "directory": str(root), "previous_bundle": str(bundle),
                "replacement_bundle": str(replacement_bundle) if replacement else None,
                "identity": original.identity, "extension_id": original.extension_id,
            })
            _write(backup_fd, "operation.json", operation)
            if replacement is not None and replacement_bundle is not None:
                _write(backup_fd, "registration.after.json", replacement[2])
                _write(backup_fd, "native-host.after.json", replacement[1])
            os.fsync(backup_fd)
            root_fd = os.open(root, _FLAGS)
            descriptors.append(root_fd)
            hosts_fd = os.open("NativeMessagingHosts", _FLAGS, dir_fd=root_fd)
            descriptors.append(hosts_fd)
            # A missing receipt blocks old launchers too. Publish the new one
            # only after the manifest is durable. Never remove Chromium locks.
            os.unlink(RECEIPT, dir_fd=root_fd)
            os.fsync(root_fd)
            _write(root_fd, MAINTENANCE_MARKER, operation)
            os.fsync(root_fd)
            os.unlink(HOST, dir_fd=hosts_fd)
            os.fsync(hosts_fd)
            if replacement is not None and replacement_bundle is not None:
                _write(hosts_fd, HOST, replacement[1])
                os.fsync(hosts_fd)
                _write(root_fd, RECEIPT, replacement[2])
                os.fsync(root_fd)
                _matches(root / RECEIPT, replacement[2])
                _matches(root / "NativeMessagingHosts" / HOST, replacement[1])
                _validated_bundle(replacement_bundle, profile, public_key, fresh=False)
            _write(backup_fd, "completed.json", operation)
            os.fsync(backup_fd)
            if replacement is not None:
                os.unlink(MAINTENANCE_MARKER, dir_fd=root_fd)
                os.fsync(root_fd)
            return original
    except Exception:
        raise BrowserMaintenanceError(
            "Browser maintenance could not be confirmed. Stop and retain all files for review; "
            "do not delete guards, restore receipts or reset profiles. Check the stopped browser, "
            "trusted old runtime, canonical replacement and unused private backup directory."
        ) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
