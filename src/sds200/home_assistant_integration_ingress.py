"""Ingress-only operator facade for the Home Assistant integration lifecycle."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from .exceptions import SDS200Error
from .home_assistant_integration_lifecycle import (
    HomeAssistantIntegrationStatus,
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

HOME_ASSISTANT_INTEGRATION_INGRESS_PROTOCOL = (
    "sdsctl.home-assistant-integration-lifecycle"
)
HOME_ASSISTANT_INTEGRATION_INGRESS_VERSION = 1
_HOME_ASSISTANT_INTEGRATION_INGRESS_LOCK = threading.Lock()

HomeAssistantIntegrationAction = Literal[
    "install",
    "update",
    "rollback",
    "remove",
    "discard-rollback",
]


def home_assistant_integration_ingress_status() -> dict[str, object]:
    """Return exact, non-secret artifact and publication identities."""

    with _exclusive_lifecycle_action():
        image = built_in_home_assistant_integration_image()
        status = inspect_home_assistant_integration()
        bridge_digest = home_assistant_integration_bridge_key_digest()
    return {
        "protocol": HOME_ASSISTANT_INTEGRATION_INGRESS_PROTOCOL,
        "version": HOME_ASSISTANT_INTEGRATION_INGRESS_VERSION,
        "artifact": {
            "version": image.version,
            "digest": image.digest,
            "bytes": image.total_bytes,
        },
        "publication": _status_payload(status),
        "bridge_key_digest": bridge_digest,
    }


def execute_home_assistant_integration_ingress_action(
    action: HomeAssistantIntegrationAction,
    *,
    confirmation_digest: str,
) -> dict[str, object]:
    """Execute one exact-confirmed lifecycle action without reloading Core."""

    confirmation = _confirmation_digest(confirmation_digest)
    with _exclusive_lifecycle_action():
        if action == "install":
            status = install_home_assistant_integration(
                confirmation_digest=confirmation,
            )
        elif action == "update":
            status = install_home_assistant_integration(
                confirmation_digest=confirmation,
                replace=True,
            )
        elif action == "rollback":
            status = rollback_home_assistant_integration(
                confirmation_digest=confirmation,
            )
        elif action == "remove":
            status = remove_home_assistant_integration(
                confirmation_digest=confirmation,
            )
        elif action == "discard-rollback":
            status = discard_home_assistant_integration_rollback(
                confirmation_digest=confirmation,
            )
        else:
            raise ValueError("Home Assistant integration lifecycle action is invalid.")
    return {
        "protocol": HOME_ASSISTANT_INTEGRATION_INGRESS_PROTOCOL,
        "version": HOME_ASSISTANT_INTEGRATION_INGRESS_VERSION,
        "action": action,
        "publication": _status_payload(status),
        "core_restart_required": True,
    }


def reveal_home_assistant_integration_bridge_key() -> dict[str, object]:
    """Return the private bridge key only for one explicit Ingress request."""

    with _exclusive_lifecycle_action():
        key = read_home_assistant_integration_bridge_key()
        digest = home_assistant_integration_bridge_key_digest()
    return {
        "protocol": HOME_ASSISTANT_INTEGRATION_INGRESS_PROTOCOL,
        "version": HOME_ASSISTANT_INTEGRATION_INGRESS_VERSION,
        "bridge_key": key,
        "bridge_key_digest": digest,
    }


def rotate_home_assistant_integration_ingress_bridge_key(
    *,
    confirmation_digest: str,
) -> dict[str, object]:
    """Rotate and return the replacement key for one explicit Ingress request."""

    confirmation = _confirmation_digest(confirmation_digest)
    with _exclusive_lifecycle_action():
        replacement = rotate_home_assistant_integration_bridge_key(
            confirmation_digest=confirmation,
        )
        digest = home_assistant_integration_bridge_key_digest()
    return {
        "protocol": HOME_ASSISTANT_INTEGRATION_INGRESS_PROTOCOL,
        "version": HOME_ASSISTANT_INTEGRATION_INGRESS_VERSION,
        "bridge_key": replacement,
        "bridge_key_digest": digest,
        "app_restart_required": True,
        "integration_reauthentication_required": True,
    }


def _status_payload(status: HomeAssistantIntegrationStatus) -> dict[str, object]:
    return {
        "destination": str(status.destination),
        "current_version": status.current_version,
        "current_digest": status.current_digest,
        "rollback_version": status.rollback_version,
        "rollback_digest": status.rollback_digest,
    }


def _confirmation_digest(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SDS200Error("An exact lowercase SHA-256 confirmation is required.")
    return value


@contextmanager
def _exclusive_lifecycle_action() -> Iterator[None]:
    if not _HOME_ASSISTANT_INTEGRATION_INGRESS_LOCK.acquire(blocking=False):
        raise SDS200Error(
            "Another Home Assistant integration lifecycle action is in progress."
        )
    try:
        yield
    finally:
        _HOME_ASSISTANT_INTEGRATION_INGRESS_LOCK.release()


__all__ = [
    "HOME_ASSISTANT_INTEGRATION_INGRESS_PROTOCOL",
    "HOME_ASSISTANT_INTEGRATION_INGRESS_VERSION",
    "HomeAssistantIntegrationAction",
    "execute_home_assistant_integration_ingress_action",
    "home_assistant_integration_ingress_status",
    "reveal_home_assistant_integration_bridge_key",
    "rotate_home_assistant_integration_ingress_bridge_key",
]
