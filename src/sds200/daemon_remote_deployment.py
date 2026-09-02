"""Redacted preflight for the isolated remote-daemon Compose deployment."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, ip_address
from pathlib import Path

from .daemon_remote import (
    DAEMON_REMOTE_DEFAULT_PORT,
    DaemonRemoteConfigurationPreflight,
    DaemonRemoteListenerConfiguration,
    load_daemon_remote_configuration,
    normalize_daemon_remote_address,
    preflight_daemon_remote_configuration,
)
from .exceptions import ConfigurationError

DAEMON_REMOTE_COMPOSE_CONTAINER_ADDRESS = "172.30.32.2"
DAEMON_REMOTE_COMPOSE_CONFIG_PATH = Path("/config/sdsctl/daemon-remote.toml")
DAEMON_REMOTE_COMPOSE_RTP_PORT = 50000
DAEMON_REMOTE_DEPLOYMENT_ERROR = (
    "Remote daemon container deployment preflight failed."
)


class DaemonRemoteDeploymentPreflightError(ConfigurationError):
    """Report a deployment-preflight failure without private details."""

    def __init__(self) -> None:
        super().__init__(DAEMON_REMOTE_DEPLOYMENT_ERROR)


@dataclass(frozen=True, slots=True)
class DaemonRemoteDeploymentPreflight:
    """Non-secret evidence for one successful container deployment preflight."""

    published_address_family: str
    listener_address_family: str
    port: int
    configuration: DaemonRemoteConfigurationPreflight

    def __post_init__(self) -> None:
        if self.published_address_family not in {"ipv4", "ipv6"}:
            raise ValueError("Published address family must be ipv4 or ipv6.")
        if self.listener_address_family not in {"ipv4", "ipv6"}:
            raise ValueError("Listener address family must be ipv4 or ipv6.")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise TypeError("Deployment port must be an integer.")
        if not 1 <= self.port <= 65535:
            raise ValueError("Deployment port must be between 1 and 65535.")
        if not isinstance(self.configuration, DaemonRemoteConfigurationPreflight):
            raise TypeError(
                "Deployment preflight requires remote configuration evidence."
            )
        if not self.configuration.enabled:
            raise ValueError("Deployment preflight evidence must be enabled.")


def preflight_daemon_remote_container_configuration(
    configuration: DaemonRemoteListenerConfiguration,
    *,
    published_address: object,
    expected_port: object = DAEMON_REMOTE_DEFAULT_PORT,
) -> DaemonRemoteDeploymentPreflight:
    """Validate one enabled listener against the fixed Compose boundary."""

    try:
        if not isinstance(configuration, DaemonRemoteListenerConfiguration):
            raise TypeError("Invalid listener configuration.")
        normalized_published = normalize_daemon_remote_address(published_address)
        normalized_bind = DAEMON_REMOTE_COMPOSE_CONTAINER_ADDRESS
        normalized_port = _deployment_port(expected_port)
        if not configuration.enabled:
            raise ValueError("Listener configuration is disabled.")
        if configuration.bind_address != normalized_bind:
            raise ValueError("Listener address does not match the deployment.")
        if configuration.port != normalized_port:
            raise ValueError("Listener port does not match the deployment.")
        evidence = preflight_daemon_remote_configuration(configuration)
    except Exception as error:
        if isinstance(error, DaemonRemoteDeploymentPreflightError):
            raise
        raise DaemonRemoteDeploymentPreflightError() from error

    return DaemonRemoteDeploymentPreflight(
        published_address_family=_address_family(normalized_published),
        listener_address_family=_address_family(normalized_bind),
        port=normalized_port,
        configuration=evidence,
    )


def preflight_daemon_remote_container_deployment(
    path: str | Path,
    *,
    published_address: object,
    expected_port: object = DAEMON_REMOTE_DEFAULT_PORT,
) -> DaemonRemoteDeploymentPreflight:
    """Load and validate one container listener using only redacted failures."""

    try:
        configuration = load_daemon_remote_configuration(path)
        if configuration is None:
            raise ValueError("Listener configuration is absent.")
        return preflight_daemon_remote_container_configuration(
            configuration,
            published_address=published_address,
            expected_port=expected_port,
        )
    except Exception as error:
        if isinstance(error, DaemonRemoteDeploymentPreflightError):
            raise
        raise DaemonRemoteDeploymentPreflightError() from error


def _deployment_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Deployment port must be an integer.")
    if not 1 <= value <= 65535:
        raise ValueError("Deployment port must be between 1 and 65535.")
    return value


def _address_family(value: str) -> str:
    return "ipv4" if isinstance(ip_address(value), IPv4Address) else "ipv6"
