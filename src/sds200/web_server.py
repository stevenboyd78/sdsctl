"""Web server adapter with explicit guarded wildcard-listener modes."""

from __future__ import annotations

import os
import stat
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Protocol, cast

WEB_DASHBOARD_DEFAULT_HOST = "127.0.0.1"
WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST = "0.0.0.0"
WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST = "0.0.0.0"
WEB_DASHBOARD_DEFAULT_PORT = 8000
WEB_DASHBOARD_DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT = 2
WEB_DASHBOARD_MAX_TLS_FILE_BYTES = 1024 * 1024
_AUTHENTICATED_LAN_NETWORKS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("169.254.0.0/16"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
)
WEB_DASHBOARD_INSTALL_ERROR = (
    "Web dashboard support is not installed; install it with: "
    'python -m pip install "sds200[web]"'
)


class WebDashboardServer(Protocol):
    """Minimum synchronous server lifecycle used by the CLI."""

    def run(self) -> None:
        """Run until orderly shutdown."""


class WebDashboardServerFactory(Protocol):
    """Construct one configured web server."""

    def __call__(
        self,
        app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        ssl_certfile: str | None = None,
        ssl_keyfile: str | None = None,
    ) -> WebDashboardServer:
        """Return one configured server."""


def normalize_web_dashboard_host(host: str) -> str:
    """Require localhost or an explicit loopback IP address."""

    if not isinstance(host, str):
        raise TypeError("Web dashboard listen address must be a string.")

    normalized = host.strip()

    if not normalized:
        raise ValueError(
            "Web dashboard listen address must not be empty."
        )

    if normalized.lower() == "localhost":
        return WEB_DASHBOARD_DEFAULT_HOST

    try:
        address = ip_address(normalized)
    except ValueError as error:
        raise ValueError(
            "Web dashboard listen address must be localhost or a "
            "loopback IP address."
        ) from error

    if not address.is_loopback:
        raise ValueError(
            "Web dashboard listen address must be localhost or a "
            "loopback IP address."
        )

    return address.compressed


def normalize_web_dashboard_port(port: int) -> int:
    """Validate one nonzero TCP listen port."""

    if type(port) is not int:
        raise TypeError("Web dashboard listen port must be an integer.")

    if not 1 <= port <= 65535:
        raise ValueError(
            "Web dashboard listen port must be between 1 and 65535."
        )

    return port


def normalize_authenticated_lan_host(host: str) -> str:
    """Require one explicit private or link-local unicast IP address."""

    if not isinstance(host, str):
        raise TypeError("Authenticated LAN listen address must be a string.")
    if not host or host.strip() != host:
        raise ValueError(
            "Authenticated LAN listen address must not be empty or padded."
        )
    try:
        address = ip_address(host)
    except ValueError as error:
        raise ValueError(
            "Authenticated LAN listen address must be a literal private, "
            "unique-local, or link-local IP address."
        ) from error
    if not any(address in network for network in _AUTHENTICATED_LAN_NETWORKS):
        raise ValueError(
            "Authenticated LAN listen address must be a literal private, "
            "unique-local, or link-local IP address."
        )
    return address.compressed


def run_web_dashboard_server(
    app: object,
    *,
    host: str = WEB_DASHBOARD_DEFAULT_HOST,
    port: int = WEB_DASHBOARD_DEFAULT_PORT,
    access_log: bool = True,
    home_assistant_ingress: bool = False,
    container_exposure: bool = False,
    authenticated_lan: bool = False,
    ssl_certfile: Path | None = None,
    ssl_keyfile: Path | None = None,
    server_factory: WebDashboardServerFactory | None = None,
) -> int:
    """Run one web server with explicit guarded wildcard-listener modes."""

    if type(home_assistant_ingress) is not bool:
        raise TypeError(
            "Home Assistant Ingress server setting must be boolean."
        )

    if type(container_exposure) is not bool:
        raise TypeError(
            "Generic container-exposure server setting must be boolean."
        )

    if type(authenticated_lan) is not bool:
        raise TypeError("Authenticated LAN server setting must be boolean.")

    if sum((home_assistant_ingress, container_exposure, authenticated_lan)) > 1:
        raise ValueError(
            "Home Assistant Ingress, generic container exposure, and "
            "authenticated LAN access are mutually exclusive."
        )

    if home_assistant_ingress:
        if host != WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST:
            raise ValueError(
                "Home Assistant Ingress web server must listen on "
                f"{WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST}."
            )
        normalized_host = WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST
    elif container_exposure:
        if host != WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST:
            raise ValueError(
                "Generic container-exposure web server must listen on "
                f"{WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST}."
            )
        normalized_host = WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST
    elif authenticated_lan:
        normalized_host = normalize_authenticated_lan_host(host)
    else:
        normalized_host = normalize_web_dashboard_host(host)

    normalized_port = normalize_web_dashboard_port(port)

    if type(access_log) is not bool:
        raise TypeError("Web dashboard access-log setting must be boolean.")

    if authenticated_lan:
        certificate_path, private_key_path = normalize_authenticated_lan_tls_files(
            ssl_certfile,
            ssl_keyfile,
        )
        normalized_certfile = os.fspath(certificate_path)
        normalized_keyfile = os.fspath(private_key_path)
    else:
        if ssl_certfile is not None or ssl_keyfile is not None:
            raise ValueError(
                "TLS certificate and private-key files require authenticated LAN access."
            )
        normalized_certfile = None
        normalized_keyfile = None

    selected_factory = server_factory or _default_server_factory

    if not callable(selected_factory):
        raise TypeError("Web dashboard server factory must be callable.")

    server = selected_factory(
        app,
        host=normalized_host,
        port=normalized_port,
        access_log=access_log,
        ssl_certfile=normalized_certfile,
        ssl_keyfile=normalized_keyfile,
    )
    server.run()
    return 0


def _default_server_factory(
    app: object,
    *,
    host: str,
    port: int,
    access_log: bool,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> WebDashboardServer:
    try:
        import uvicorn
    except ModuleNotFoundError as error:
        if error.name == "uvicorn":
            raise ValueError(
                WEB_DASHBOARD_INSTALL_ERROR
            ) from error
        raise

    config = uvicorn.Config(
        cast(Any, app),
        host=host,
        port=port,
        access_log=access_log,
        log_config=None,
        proxy_headers=False,
        server_header=False,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        timeout_graceful_shutdown=(
            WEB_DASHBOARD_DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT
        ),
    )
    return uvicorn.Server(config)


def normalize_authenticated_lan_tls_files(
    certificate: Path | None,
    private_key: Path | None,
) -> tuple[Path, Path]:
    """Validate the complete native-TLS file pair before app construction."""

    certificate_path = _normalize_tls_path(
        certificate,
        label="Authenticated LAN TLS certificate",
    )
    private_key_path = _normalize_tls_path(
        private_key,
        label="Authenticated LAN TLS private key",
    )
    normalized_certificate = _normalize_tls_file(
        certificate_path,
        label="Authenticated LAN TLS certificate",
        private=False,
    )
    normalized_private_key = _normalize_tls_file(
        private_key_path,
        label="Authenticated LAN TLS private key",
        private=True,
    )
    return normalized_certificate, normalized_private_key


def _normalize_tls_path(value: Path | None, *, label: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{label} path must be a pathlib.Path.")
    if not value.is_absolute():
        raise ValueError(f"{label} path must be absolute.")
    return value


def _normalize_tls_file(
    value: Path,
    *,
    label: str,
    private: bool,
) -> Path:
    try:
        metadata = value.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} path must name a regular file.")
        if metadata.st_size > WEB_DASHBOARD_MAX_TLS_FILE_BYTES:
            raise ValueError(
                f"{label} file must not exceed {WEB_DASHBOARD_MAX_TLS_FILE_BYTES} bytes."
            )
        with value.open("rb") as stream:
            contents = stream.read(WEB_DASHBOARD_MAX_TLS_FILE_BYTES + 1)
    except OSError as error:
        raise ValueError(f"{label} file is unavailable.") from error
    if private and os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o007:
        raise ValueError(
            "Authenticated LAN TLS private key must not grant permissions "
            "to the POSIX other class."
        )
    if len(contents) > WEB_DASHBOARD_MAX_TLS_FILE_BYTES:
        raise ValueError(
            f"{label} file must not exceed "
            f"{WEB_DASHBOARD_MAX_TLS_FILE_BYTES} bytes."
        )
    encrypted_markers = (
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
        b"PROC-TYPE: 4,ENCRYPTED",
    )
    if private and any(marker in contents.upper() for marker in encrypted_markers):
        raise ValueError(
            "Authenticated LAN TLS private key must be an unencrypted PEM key."
        )
    return value


__all__ = [
    "WEB_DASHBOARD_DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT",
    "WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST",
    "WEB_DASHBOARD_DEFAULT_HOST",
    "WEB_DASHBOARD_DEFAULT_PORT",
    "WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST",
    "WEB_DASHBOARD_INSTALL_ERROR",
    "WEB_DASHBOARD_MAX_TLS_FILE_BYTES",
    "WebDashboardServer",
    "WebDashboardServerFactory",
    "normalize_authenticated_lan_host",
    "normalize_authenticated_lan_tls_files",
    "normalize_web_dashboard_host",
    "normalize_web_dashboard_port",
    "run_web_dashboard_server",
]
