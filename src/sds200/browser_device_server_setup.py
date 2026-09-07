"""Explicit offline preparation of a new experimental server authority.

Never called by web/App startup. No device is enrolled and no service, socket,
trust store or password is created. Same-account processes and root are trusted.
Partial outputs are retained; there is no overwrite, repair or cleanup mode.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from .browser_device_profile import _write
from .browser_device_server import (
    BrowserDeviceServerConfiguration,
    BrowserDeviceServerError,
    load_browser_device_server_configuration,
    parse_browser_device_server_configuration,
)
from .browser_device_store import BrowserDeviceStore


def prepare_browser_device_server(
    root: Path, *, native_origin: str, native_only: bool = False,
    ingress_origin: str | None = None, admin_user_ids: tuple[str, ...] = (),
) -> BrowserDeviceServerConfiguration:
    """Create authority.sqlite and publish server.json last, only in a new directory.

    All input validation precedes writes. Run as the intended server account,
    including root inside an App container; no cross-account chown or permission
    repair is attempted. Exclusive creation also refuses another preparer's output.
    """
    try:
        if sys.platform != "linux" or not isinstance(root, Path) or not root.is_absolute():
            raise ValueError()
        BrowserDeviceStore(root)._check(database=False)
        if root.exists() or root.is_symlink():
            raise FileExistsError()
        if type(native_only) is not bool or type(admin_user_ids) is not tuple:
            raise ValueError()
        if native_only:
            if ingress_origin is not None or admin_user_ids:
                raise ValueError()
        elif ingress_origin is None:
            raise ValueError()
        document = json.dumps({
            "version": 1, "authority_path": str(root / "authority.sqlite"),
            "native_origin": native_origin,
            "ingress_admin": (None if native_only else {
                "origin": ingress_origin, "user_ids": list(admin_user_ids),
            }),
        }, separators=(",", ":")).encode("ascii")
        parse_browser_device_server_configuration(document)
    except FileExistsError:
        raise BrowserDeviceServerError(
            "Server preparation directory already exists; refusing to overwrite or reset it."
        ) from None
    except Exception:
        raise BrowserDeviceServerError(
            "Server preparation inputs are invalid or unsafe; nothing was created. "
            "Use a new directory in an owned private parent and explicit HTTPS/admin settings."
        ) from None

    parent_fd = directory_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent_fd = os.open(root.parent, flags)
        parent = os.fstat(parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError()
        os.mkdir(root.name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        directory_fd = os.open(root.name, flags, dir_fd=parent_fd)
        opened = os.fstat(directory_fd)
        selected = root.lstat()
        if (not stat.S_ISDIR(selected.st_mode)
                or opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) != 0o700
                or (opened.st_dev, opened.st_ino) != (selected.st_dev, selected.st_ino)):
            raise ValueError()
        BrowserDeviceStore.initialize(root / "authority.sqlite")
        _write(directory_fd, "server.json", document)
        os.fsync(directory_fd)
        return load_browser_device_server_configuration(root / "server.json")
    except Exception:
        raise BrowserDeviceServerError(
            "Server preparation could not be confirmed. Retain any created directory for "
            "inspection; do not retry over it or reset an existing authority."
        ) from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if parent_fd is not None:
            os.close(parent_fd)
