"""Preflight and service-manager contracts for unattended TUI displays."""

from __future__ import annotations

import logging
import os
import socket
import stat
from contextlib import suppress
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from .daemon_remote_client import (
    DaemonRemoteClientError,
    DaemonRemoteClientErrorReason,
)
from .exceptions import (
    ConfigurationError,
    DaemonDisconnectedError,
    DaemonProtocolError,
)

MANAGED_DISPLAY_TEMPORARY_EXIT = 75
MANAGED_DISPLAY_CONFIGURATION_EXIT = 78
MANAGED_DISPLAY_SERVICE_FILENAME = "sdsctl-display@.service"


def report_managed_display_wait(
    event: Literal["waiting", "retry", "ready"], error: BaseException | None = None
) -> None:
    """Keep fixed outage events in the TUI/file log and best-effort local journal."""

    if event not in {"waiting", "retry", "ready"}:
        raise ValueError("Unknown managed display event.")
    reason = "none"
    if isinstance(error, DaemonRemoteClientError):
        reason = error.reason.value
    elif isinstance(error, DaemonDisconnectedError):
        reason = "disconnected"
    elif error is not None:
        reason = "local_failure"
    message = f"managed display connection event={event} reason={reason}"
    logging.getLogger(__name__).log(
        logging.INFO if event == "ready" else logging.WARNING, message
    )
    # stdout and stderr belong to the full-screen renderer. Do not redirect
    # either service stream to journald or put endpoint/credential values here.
    payload = (
        f"PRIORITY={6 if event == 'ready' else 4}\n"
        f"SYSLOG_IDENTIFIER=sdsctl-display\nMESSAGE={message}\n"
    ).encode("ascii")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as journal:
            journal.settimeout(0.1)
            journal.sendto(payload, "/run/systemd/journal/socket")
    except OSError:
        # A missing/full journal must not stop recovery or leak logging errors
        # onto the console. The bounded TUI buffer and optional file still work.
        pass


@dataclass(frozen=True, slots=True)
class ManagedDisplayTerminal:
    """Sanitized terminal geometry used to predict the responsive TUI layout."""

    columns: int
    rows: int
    layout: str


class ManagedDisplayConfigurationError(ConfigurationError):
    """A permanent managed-display configuration failure."""


def inspect_managed_display_terminal(path: Path) -> ManagedDisplayTerminal:
    """Inspect one exact character terminal without following a final symlink."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise ManagedDisplayConfigurationError(
            "Managed display terminal path must be absolute."
        )

    descriptor: int | None = None
    try:
        observed = path.lstat()
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISCHR(observed.st_mode):
            raise ManagedDisplayConfigurationError(
                "Managed display terminal must be one character device."
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOCTTY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISCHR(opened.st_mode) or (
            observed.st_dev,
            observed.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise ManagedDisplayConfigurationError(
                "Managed display terminal changed while it was inspected."
            )
        geometry = os.get_terminal_size(descriptor)
    except ManagedDisplayConfigurationError:
        raise
    except OSError as error:
        raise ManagedDisplayConfigurationError(
            "Managed display terminal is unavailable."
        ) from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)

    if geometry.columns <= 0 or geometry.lines <= 0:
        raise ManagedDisplayConfigurationError(
            "Managed display terminal geometry is unavailable."
        )
    return ManagedDisplayTerminal(
        columns=geometry.columns,
        rows=geometry.lines,
        layout=managed_display_layout(geometry.columns, geometry.lines),
    )


def managed_display_layout(columns: int, rows: int) -> str:
    """Return the stable responsive-layout name for terminal geometry."""

    if isinstance(columns, bool) or not isinstance(columns, int) or columns <= 0:
        raise ValueError("Managed display terminal columns must be positive.")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
        raise ValueError("Managed display terminal rows must be positive.")
    if columns >= 120:
        return "wide"
    if rows < 32 and columns >= 100:
        return "compact-split"
    if columns < 80:
        return "compact"
    if rows < 32:
        return "short"
    return "standard"


def require_observe_only_display(hello: object) -> None:
    """Reject a managed display when the authenticated identity can control."""

    if not isinstance(hello, dict):
        raise ManagedDisplayConfigurationError(
            "Managed display authorization could not be verified."
        )
    operations = hello.get("control_operations")
    if not isinstance(operations, list) or any(
        not isinstance(operation, str) for operation in operations
    ):
        raise ManagedDisplayConfigurationError(
            "Managed display authorization could not be verified."
        )
    if operations:
        raise ManagedDisplayConfigurationError(
            "Managed displays require an observe-only remote identity."
        )


def managed_display_failure_status(error: BaseException) -> int:
    """Classify failures for a service manager without exposing private state."""

    if isinstance(error, DaemonDisconnectedError):
        return MANAGED_DISPLAY_TEMPORARY_EXIT
    if isinstance(error, DaemonRemoteClientError):
        if error.reason is DaemonRemoteClientErrorReason.CONNECT_FAILED:
            return MANAGED_DISPLAY_TEMPORARY_EXIT
        return MANAGED_DISPLAY_CONFIGURATION_EXIT
    if isinstance(error, (ConfigurationError, DaemonProtocolError)):
        return MANAGED_DISPLAY_CONFIGURATION_EXIT
    return 2


def managed_display_service_template() -> str:
    """Return the exact packaged systemd unit without host-dependent content."""

    resource = files("sds200.service_assets").joinpath(
        MANAGED_DISPLAY_SERVICE_FILENAME
    )
    return resource.read_text(encoding="utf-8")


__all__ = [
    "MANAGED_DISPLAY_CONFIGURATION_EXIT",
    "MANAGED_DISPLAY_SERVICE_FILENAME",
    "MANAGED_DISPLAY_TEMPORARY_EXIT",
    "ManagedDisplayConfigurationError",
    "ManagedDisplayTerminal",
    "inspect_managed_display_terminal",
    "managed_display_failure_status",
    "managed_display_layout",
    "managed_display_service_template",
    "report_managed_display_wait",
    "require_observe_only_display",
]
