"""Prepare/check inert user-service artifacts for an experimental registration.

Never install/enable a unit, import environment/trust, initialize state or start
a desktop. The generated foreground entry revalidates all artifacts at launch.
The installed interpreter and same-account/root files are trusted dependencies.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from .browser_device_bundle import _json
from .browser_device_native import _private_read
from .browser_device_profile import _platform, _write
from .browser_device_recovery import _object
from .browser_device_registration import _matches
from .browser_device_startup import check_browser_startup, run_browser_startup
from .browser_device_store import BrowserDeviceStore
from .exceptions import ConfigurationError

UNIT = "sdsctl-browser-device.service"
_PATHS = ("directory", "bundle", "profile", "public_key", "browser")


class BrowserServiceError(ConfigurationError):
    """Fixed, redacted diagnostics only."""


def _quote(value: str) -> str:
    # systemd is not a shell: quote backslashes/double quotes and %% specifiers.
    # ExecStart's ':' prefix disables $ expansion. Refuse control characters.
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError()
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def _artifacts(root: Path, settings: dict[str, str]) -> dict[str, bytes]:
    python = str(Path(sys.executable))  # Keep the installed venv, not its symlink target.
    if not Path(python).is_absolute() or not os.access(python, os.X_OK):
        raise ValueError()
    return {
        "service.json": _json({"version": 1, "experimental": True, "python": python,
                               **settings}),
        "launch.py": (
            "from pathlib import Path\n"
            "from sds200.browser_device_service import service_main\n"
            f"raise SystemExit(service_main(Path({str(root)!r})))\n"
        ).encode(),
        UNIT: (
            "[Unit]\nDescription=SDSCTL experimental managed browser display\n"
            "Documentation=https://github.com/stevenboyd78/sdsctl/blob/main/docs/browser-device-service.md\n"
            "Requisite=graphical-session.target\nPartOf=graphical-session.target\n"
            "After=graphical-session.target\nStartLimitIntervalSec=300\nStartLimitBurst=3\n\n"
            "[Service]\nType=exec\n"
            f"ExecStart=:{_quote(python)} -I {_quote(str(root / 'launch.py'))}\n"
            "Restart=on-failure\nRestartPreventExitStatus=2 78\nRestartSec=15s\n"
            "TimeoutStopSec=20s\nKillMode=control-group\nUMask=0077\n"
            "# Use an existing non-root graphical session and Chromium's normal sandbox.\n"
            "# No password, environment file, TLS bypass, setup or resume command.\n\n"
            "[Install]\nWantedBy=graphical-session.target\n"
        ).encode(),
    }


def _check_settings(settings: dict[str, str]) -> None:
    for value in settings.values():
        _quote(value)
    check_browser_startup(
        Path(settings["directory"]), bundle=Path(settings["bundle"]),
        profile=Path(settings["profile"]), public_key=Path(settings["public_key"]),
        browser=Path(settings["browser"]),
    )


def inspect_browser_service(root: Path) -> dict[str, str]:
    """Read-only canonical check; neither server login nor first-run proof."""
    try:
        _platform()
        body = _private_read(root, "service.json", 32768)
        settings = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
        if (type(settings) is not dict
                or set(settings) != {"version", "experimental", "python", *_PATHS}
                or type(settings["version"]) is not int or settings["version"] != 1
                or settings["experimental"] is not True
                or settings["python"] != sys.executable
                or any(type(settings[key]) is not str for key in _PATHS)):
            raise ValueError()
        paths = {key: settings[key] for key in _PATHS}
        _check_settings(paths)
        artifacts = _artifacts(root, paths)
        if {path.name for path in root.iterdir()} != set(artifacts):
            raise ValueError()
        for name, expected in artifacts.items():
            _matches(root / name, expected)
        return paths
    except Exception:
        raise BrowserServiceError(
            "Managed browser service files are invalid or unsafe; nothing was changed. "
            "Review the original runtime, canonical service files and registration."
        ) from None


def create_browser_service(
    root: Path, *, directory: Path, bundle: Path, profile: Path, public_key: Path, browser: Path,
) -> None:
    """Write a new private review directory only. Never contact systemd/Chromium."""
    descriptors: list[int] = []
    try:
        _platform()
        BrowserDeviceStore(root)._check(database=False)
        if root.exists() or root.is_symlink():
            raise ValueError()
        # Do not put generated files in a protected input or Chromium's storage.
        if any(root.is_relative_to(path) for path in (directory, bundle, profile)):
            raise ValueError()
        settings = dict(zip(_PATHS, map(str, (directory, bundle, profile, public_key, browser)),
                            strict=True))
        _check_settings(settings)
        artifacts = _artifacts(root, settings)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent_fd = os.open(root.parent, flags)
        descriptors.append(parent_fd)
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError()
        os.mkdir(root.name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        root_fd = os.open(root.name, flags, dir_fd=parent_fd)
        descriptors.append(root_fd)
        for name in ("launch.py", UNIT, "service.json"):
            _write(root_fd, name, artifacts[name])
        os.fsync(root_fd)
    except Exception:
        raise BrowserServiceError(
            "Managed browser service preparation could not be confirmed. Use a new private "
            "directory and retain any partial output; no service was installed or enabled."
        ) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def service_main(root: Path) -> int:
    """Canonical runtime entry; launcher alone owns the browser and exit policy."""
    try:
        settings = inspect_browser_service(root)
        return run_browser_startup(
            Path(settings["directory"]), bundle=Path(settings["bundle"]),
            profile=Path(settings["profile"]), public_key=Path(settings["public_key"]),
            browser=Path(settings["browser"]),
        )
    except ConfigurationError:
        print("Managed browser service needs review; no profile was reset or resumed.",
              file=sys.stderr)
        return 78
