from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, IPv6Network, ip_address
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from typing import TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .daemon_mqtt import (
    DAEMON_MQTT_CONFIG_VERSION,
    DaemonMqttConfiguration,
    DaemonMqttHomeAssistantConfiguration,
    load_daemon_mqtt_configuration,
)
from .exceptions import ConfigurationError, SDS200Error

HOME_ASSISTANT_APP_OPTIONS_PATH = Path("/data/options.json")
HOME_ASSISTANT_SUPERVISOR_URL = "http://supervisor"
HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE = "SUPERVISOR_TOKEN"
HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE = "SDSCTL_HOME_ASSISTANT_MQTT_PASSWORD"
HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX = "sdsctl"
HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY = "sdsctl/recordings"
HOME_ASSISTANT_APP_SUPERVISOR_TIMEOUT = 5.0
HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY = "50443/tcp"
HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY = "8443/tcp"

_HOME_ASSISTANT_APP_PRIVATE_IPV4_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
    IPv4Network("169.254.0.0/16"),
)
_HOME_ASSISTANT_APP_PRIVATE_IPV6_NETWORKS = (
    IPv6Network("fc00::/7"),
    IPv6Network("fe80::/10"),
)

SupervisorJsonRequester: TypeAlias = Callable[[str, str, float], object]


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string.")
    if not value or value.strip() != value:
        raise ValueError(f"{label} must not be empty or padded.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} must not contain control characters.")
    return value


def _require_topic_prefix(value: object) -> str:
    topic = _require_text(value, label="Home Assistant App MQTT topic prefix")
    if topic.startswith("/") or topic.endswith("/"):
        raise ValueError(
            "Home Assistant App MQTT topic prefix must not start or end with '/'."
        )
    if "//" in topic:
        raise ValueError(
            "Home Assistant App MQTT topic prefix must not contain empty topic levels."
        )
    if "#" in topic or "+" in topic:
        raise ValueError(
            "Home Assistant App MQTT topic prefix must not contain subscription wildcards."
        )
    return topic


def _require_recording_directory(value: object) -> str:
    directory = _require_text(
        value,
        label="Home Assistant App recording directory",
    )
    if "\\" in directory:
        raise ValueError(
            "Home Assistant App recording directory must use '/' separators."
        )

    path = PurePosixPath(directory)
    if path.is_absolute():
        raise ValueError(
            "Home Assistant App recording directory must be relative to /media."
        )

    parts = directory.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            "Home Assistant App recording directory must not contain "
            "empty, '.' or '..' path components."
        )

    return path.as_posix()


def _require_bool(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be boolean.")
    return value


def _require_advanced_access_server_name(value: object) -> str:
    if value == "":
        return ""
    name = _require_text(
        value,
        label="Home Assistant App advanced-access server name",
    ).lower()
    if "://" in name or "/" in name or "%" in name:
        raise ValueError(
            "Home Assistant App advanced-access server name must be one "
            "private address or local hostname."
        )
    try:
        address = ip_address(name)
    except ValueError:
        if len(name) > 253 or not name.isascii():
            raise ValueError(
                "Home Assistant App advanced-access server name is invalid."
            ) from None
        labels = name.split(".")
        if not all(
            label
            and len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            raise ValueError(
                "Home Assistant App advanced-access server name is invalid."
            ) from None
        if len(labels) > 1 and not (
            name.endswith(".local") or name.endswith(".home.arpa")
        ):
            raise ValueError(
                "Home Assistant App advanced-access server name must be a "
                "single-label, .local, or .home.arpa private hostname."
            ) from None
        return name

    allowed = (
        any(address in network for network in _HOME_ASSISTANT_APP_PRIVATE_IPV4_NETWORKS)
        if isinstance(address, IPv4Address)
        else any(
            address in network for network in _HOME_ASSISTANT_APP_PRIVATE_IPV6_NETWORKS
        )
    )
    if not allowed or address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError(
            "Home Assistant App advanced-access server name must be a private, "
            "unique-local, or link-local address."
        )
    return str(address)


def _require_advanced_access_host_address(value: object) -> str:
    if value == "":
        return ""
    selected = _require_text(
        value,
        label="Home Assistant App advanced-access host address",
    )
    address_text, separator, scope = selected.partition("%")
    try:
        address = ip_address(address_text)
    except ValueError as error:
        raise ValueError(
            "Home Assistant App advanced-access host address must be one "
            "literal private address."
        ) from error
    if separator and (
        not scope
        or isinstance(address, IPv4Address)
        or len(scope) > 64
        or any(
            not (character.isalnum() or character in "._-")
            for character in scope
        )
    ):
        raise ValueError(
            "Home Assistant App advanced-access host address has an invalid "
            "IPv6 scope."
        )
    allowed = (
        any(address in network for network in _HOME_ASSISTANT_APP_PRIVATE_IPV4_NETWORKS)
        if isinstance(address, IPv4Address)
        else any(
            address in network for network in _HOME_ASSISTANT_APP_PRIVATE_IPV6_NETWORKS
        )
    )
    if (
        not allowed
        or address.is_unspecified
        or address.is_loopback
        or address.is_multicast
        or address_text != address.compressed
    ):
        raise ValueError(
            "Home Assistant App advanced-access host address must be one "
            "literal private address."
        )
    return f"{address.compressed}%{scope}" if separator else address.compressed


@dataclass(frozen=True, slots=True)
class HomeAssistantAppOptions:
    """Strict user-editable options consumed from /data/options.json."""

    scanner_host: str
    mqtt_topic_prefix: str = HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX
    recording_directory: str = HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY
    remote_daemon_enabled: bool = False
    native_dashboard_enabled: bool = False
    advanced_access_server_name: str = ""
    advanced_access_host_address: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scanner_host",
            _require_text(
                self.scanner_host,
                label="Home Assistant App scanner host",
            ),
        )
        object.__setattr__(
            self,
            "mqtt_topic_prefix",
            _require_topic_prefix(self.mqtt_topic_prefix),
        )
        object.__setattr__(
            self,
            "recording_directory",
            _require_recording_directory(self.recording_directory),
        )
        object.__setattr__(
            self,
            "remote_daemon_enabled",
            _require_bool(
                self.remote_daemon_enabled,
                label="Home Assistant App remote-daemon enabled setting",
            ),
        )
        object.__setattr__(
            self,
            "native_dashboard_enabled",
            _require_bool(
                self.native_dashboard_enabled,
                label="Home Assistant App native-dashboard enabled setting",
            ),
        )
        object.__setattr__(
            self,
            "advanced_access_server_name",
            _require_advanced_access_server_name(self.advanced_access_server_name),
        )
        object.__setattr__(
            self,
            "advanced_access_host_address",
            _require_advanced_access_host_address(self.advanced_access_host_address),
        )
        if (
            self.remote_daemon_enabled or self.native_dashboard_enabled
        ) and not self.advanced_access_server_name:
            raise ValueError(
                "Enabled Home Assistant App advanced access requires an "
                "advanced-access server name."
            )
        if self.remote_daemon_enabled and not self.advanced_access_host_address:
            raise ValueError(
                "Enabled Home Assistant App remote access requires an "
                "advanced-access host address."
            )


@dataclass(frozen=True, slots=True)
class HomeAssistantAppSupervisorInfo:
    """Validated non-secret App network state returned by Supervisor."""

    container_address: str
    network: Mapping[str, int | None]
    options: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        try:
            address = ip_address(self.container_address)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Home Assistant Supervisor App address must be one literal IP address."
            ) from error
        allowed = (
            any(
                address in network
                for network in _HOME_ASSISTANT_APP_PRIVATE_IPV4_NETWORKS
            )
            if isinstance(address, IPv4Address)
            else any(
                address in network
                for network in _HOME_ASSISTANT_APP_PRIVATE_IPV6_NETWORKS
            )
        )
        if (
            not allowed
            or address.is_unspecified
            or address.is_loopback
            or address.is_multicast
        ):
            raise ValueError(
                "Home Assistant Supervisor App address must be private, "
                "unique-local, or link-local."
            )
        object.__setattr__(self, "container_address", str(address))

        if not isinstance(self.network, Mapping):
            raise TypeError("Home Assistant Supervisor App network must be a mapping.")
        normalized_network: dict[str, int | None] = {}
        for key, value in self.network.items():
            if type(key) is not str or not key:
                raise ValueError(
                    "Home Assistant Supervisor App network contains an invalid key."
                )
            if value is not None and (
                type(value) is not int or not 1 <= value <= 65535
            ):
                raise ValueError(
                    "Home Assistant Supervisor App network contains an invalid port."
                )
            normalized_network[key] = value
        object.__setattr__(self, "network", MappingProxyType(normalized_network))
        if not isinstance(self.options, Mapping):
            raise TypeError("Home Assistant Supervisor App options must be a mapping.")
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


@dataclass(frozen=True, slots=True)
class HomeAssistantAppAdvancedExposure:
    """Reconciled advanced listener state used by the App launch plan."""

    container_address: str
    remote_daemon_host_port: int | None = None
    native_dashboard_host_port: int | None = None

    def __post_init__(self) -> None:
        validated = HomeAssistantAppSupervisorInfo(
            container_address=self.container_address,
            network={
                HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY: (
                    self.remote_daemon_host_port
                ),
                HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY: (
                    self.native_dashboard_host_port
                ),
            },
        )
        object.__setattr__(self, "container_address", validated.container_address)

    @property
    def enabled(self) -> bool:
        return (
            self.remote_daemon_host_port is not None
            or self.native_dashboard_host_port is not None
        )


@dataclass(frozen=True, slots=True)
class HomeAssistantMqttService:
    """Validated Supervisor-provided MQTT service connection details."""

    host: str
    port: int
    ssl: bool
    username: str
    password: str = field(repr=False)
    protocol: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "host",
            _require_text(self.host, label="Supervisor MQTT host"),
        )
        if type(self.port) is not int:
            raise TypeError("Supervisor MQTT port must be an integer.")
        if not 1 <= self.port <= 65535:
            raise ValueError(
                "Supervisor MQTT port must be between 1 and 65535."
            )
        if type(self.ssl) is not bool:
            raise TypeError("Supervisor MQTT SSL setting must be boolean.")
        object.__setattr__(
            self,
            "username",
            _require_text(self.username, label="Supervisor MQTT username"),
        )
        object.__setattr__(
            self,
            "password",
            _require_text(self.password, label="Supervisor MQTT password"),
        )
        object.__setattr__(
            self,
            "protocol",
            _require_text(self.protocol, label="Supervisor MQTT protocol"),
        )


def load_home_assistant_app_options(
    path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
) -> HomeAssistantAppOptions:
    """Load the strict Home Assistant App user options document."""

    options_path = Path(path)
    try:
        raw = options_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(
            f"Could not read Home Assistant App options {options_path}: {error}"
        ) from error

    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"Home Assistant App options {options_path} must contain valid JSON."
        ) from error

    if not isinstance(payload, Mapping):
        raise ConfigurationError(
            f"Home Assistant App options {options_path} must contain one JSON object."
        )

    allowed = {
        "scanner_host",
        "mqtt_topic_prefix",
        "recording_directory",
        "remote_daemon_enabled",
        "native_dashboard_enabled",
        "advanced_access_server_name",
        "advanced_access_host_address",
    }
    unexpected = sorted(str(key) for key in payload if key not in allowed)
    if unexpected:
        fields = ", ".join(repr(field) for field in unexpected)
        raise ConfigurationError(
            f"Home Assistant App options {options_path} have unsupported field(s): {fields}."
        )

    if "scanner_host" not in payload:
        raise ConfigurationError(
            f"Home Assistant App options {options_path} require 'scanner_host'."
        )

    try:
        return HomeAssistantAppOptions(
            scanner_host=payload["scanner_host"],
            mqtt_topic_prefix=payload.get(
                "mqtt_topic_prefix",
                HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX,
            ),
            recording_directory=payload.get(
                "recording_directory",
                HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY,
            ),
            remote_daemon_enabled=payload.get("remote_daemon_enabled", False),
            native_dashboard_enabled=payload.get("native_dashboard_enabled", False),
            advanced_access_server_name=payload.get(
                "advanced_access_server_name",
                "",
            ),
            advanced_access_host_address=payload.get(
                "advanced_access_host_address",
                "",
            ),
        )
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"Invalid Home Assistant App options {options_path}: {error}"
        ) from error


def _supervisor_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environ is None else environ
    token = source.get(HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE)
    if token is None or not token or token.strip() != token:
        raise SDS200Error(
            f"{HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE} is missing or invalid."
        )
    return token


def _request_supervisor_json(
    url: str,
    token: str,
    timeout: float,
) -> object:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise SDS200Error(
            "Could not query the Home Assistant Supervisor "
            f"({error.__class__.__name__})."
        ) from error

    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SDS200Error(
            "Home Assistant Supervisor returned invalid JSON."
        ) from error


def _supervisor_mqtt_port(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("Supervisor MQTT port must be an integer or decimal string.")
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.isdecimal():
        port = int(value)
    else:
        raise TypeError("Supervisor MQTT port must be an integer or decimal string.")
    if not 1 <= port <= 65535:
        raise ValueError("Supervisor MQTT port must be between 1 and 65535.")
    return port


def parse_home_assistant_mqtt_service_response(
    payload: object,
) -> HomeAssistantMqttService:
    """Validate one raw Supervisor API response envelope for /services/mqtt."""

    if not isinstance(payload, Mapping):
        raise SDS200Error(
            "Home Assistant Supervisor MQTT response must be a JSON object."
        )
    if payload.get("result") != "ok":
        raise SDS200Error(
            "Home Assistant Supervisor MQTT service request did not succeed."
        )

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise SDS200Error(
            "Home Assistant Supervisor MQTT response must contain a data object."
        )

    required = {"host", "port", "ssl", "username", "password", "protocol"}
    missing = sorted(field for field in required if field not in data)
    if missing:
        fields = ", ".join(repr(field) for field in missing)
        raise SDS200Error(
            "Home Assistant Supervisor MQTT response omitted required "
            f"field(s): {fields}."
        )

    try:
        return HomeAssistantMqttService(
            host=data["host"],
            port=_supervisor_mqtt_port(data["port"]),
            ssl=data["ssl"],
            username=data["username"],
            password=data["password"],
            protocol=data["protocol"],
        )
    except (TypeError, ValueError) as error:
        raise SDS200Error(
            f"Invalid Home Assistant Supervisor MQTT service response: {error}"
        ) from error


def parse_home_assistant_app_supervisor_info_response(
    payload: object,
) -> HomeAssistantAppSupervisorInfo:
    """Validate one raw Supervisor `/addons/self/info` response."""

    if not isinstance(payload, Mapping) or payload.get("result") != "ok":
        raise SDS200Error(
            "Home Assistant Supervisor App information request did not succeed."
        )
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise SDS200Error(
            "Home Assistant Supervisor App information must contain a data object."
        )
    network = data.get("network")
    options = data.get("options")
    if not isinstance(network, Mapping) or not isinstance(options, Mapping):
        raise SDS200Error(
            "Home Assistant Supervisor App information omitted network or options state."
        )
    container_address = data.get("ip_address")
    if not isinstance(container_address, str):
        raise SDS200Error(
            "Home Assistant Supervisor App information omitted its address."
        )
    try:
        return HomeAssistantAppSupervisorInfo(
            container_address=container_address,
            network=network,
            options=options,
        )
    except (TypeError, ValueError) as error:
        raise SDS200Error(
            "Home Assistant Supervisor App information is invalid."
        ) from error


def fetch_home_assistant_app_supervisor_info(
    *,
    environ: Mapping[str, str] | None = None,
    supervisor_url: str = HOME_ASSISTANT_SUPERVISOR_URL,
    timeout: float = HOME_ASSISTANT_APP_SUPERVISOR_TIMEOUT,
    requester: SupervisorJsonRequester | None = None,
) -> HomeAssistantAppSupervisorInfo:
    """Fetch authoritative network and option state for this App."""

    normalized_url = _require_text(
        supervisor_url,
        label="Home Assistant Supervisor URL",
    ).rstrip("/")
    if not normalized_url:
        raise ValueError("Home Assistant Supervisor URL must not be empty.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("Home Assistant Supervisor timeout must be a number.")
    normalized_timeout = float(timeout)
    if normalized_timeout <= 0:
        raise ValueError(
            "Home Assistant Supervisor timeout must be greater than zero."
        )
    token = _supervisor_token(environ)
    selected_requester = requester or _request_supervisor_json
    payload = selected_requester(
        f"{normalized_url}/addons/self/info",
        token,
        normalized_timeout,
    )
    return parse_home_assistant_app_supervisor_info_response(payload)


def reconcile_home_assistant_app_advanced_exposure(
    options: HomeAssistantAppOptions,
    info: HomeAssistantAppSupervisorInfo,
) -> HomeAssistantAppAdvancedExposure:
    """Fail closed unless requested and effective advanced exposure agrees."""

    if not isinstance(options, HomeAssistantAppOptions):
        raise TypeError("Advanced App exposure requires Home Assistant App options.")
    if not isinstance(info, HomeAssistantAppSupervisorInfo):
        raise TypeError("Advanced App exposure requires Supervisor App information.")

    for field_name in (
        "remote_daemon_enabled",
        "native_dashboard_enabled",
        "advanced_access_server_name",
        "advanced_access_host_address",
    ):
        if info.options.get(field_name, getattr(options, field_name)) != getattr(
            options,
            field_name,
        ):
            raise SDS200Error(
                "Home Assistant App options disagree with Supervisor state."
            )

    remote_port = info.network.get(HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY)
    native_port = info.network.get(HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY)
    requested = (
        ("remote daemon", options.remote_daemon_enabled, remote_port),
        ("native dashboard", options.native_dashboard_enabled, native_port),
    )
    for label, enabled, host_port in requested:
        if enabled and host_port is None:
            raise SDS200Error(
                f"Enabled Home Assistant App {label} requires its Network mapping."
            )
        if not enabled and host_port is not None:
            raise SDS200Error(
                f"Home Assistant App {label} Network mapping must be disabled "
                "when the feature is disabled."
            )
    if remote_port is not None and remote_port == native_port:
        raise SDS200Error(
            "Home Assistant App advanced listeners require distinct host ports."
        )
    return HomeAssistantAppAdvancedExposure(
        container_address=info.container_address,
        remote_daemon_host_port=remote_port,
        native_dashboard_host_port=native_port,
    )


def fetch_home_assistant_mqtt_service(
    *,
    environ: Mapping[str, str] | None = None,
    supervisor_url: str = HOME_ASSISTANT_SUPERVISOR_URL,
    timeout: float = HOME_ASSISTANT_APP_SUPERVISOR_TIMEOUT,
    requester: SupervisorJsonRequester | None = None,
) -> HomeAssistantMqttService:
    """Fetch and validate the MQTT service selected by Home Assistant Supervisor."""

    normalized_url = _require_text(
        supervisor_url,
        label="Home Assistant Supervisor URL",
    ).rstrip("/")
    if not normalized_url:
        raise ValueError("Home Assistant Supervisor URL must not be empty.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("Home Assistant Supervisor timeout must be a number.")
    normalized_timeout = float(timeout)
    if normalized_timeout <= 0:
        raise ValueError(
            "Home Assistant Supervisor timeout must be greater than zero."
        )

    token = _supervisor_token(environ)
    selected_requester = requester or _request_supervisor_json
    payload = selected_requester(
        f"{normalized_url}/services/mqtt",
        token,
        normalized_timeout,
    )
    return parse_home_assistant_mqtt_service_response(payload)


def build_home_assistant_daemon_mqtt_configuration(
    options: HomeAssistantAppOptions,
    service: HomeAssistantMqttService,
) -> DaemonMqttConfiguration:
    """Map Home Assistant App settings to the existing generic daemon MQTT model."""

    if not isinstance(options, HomeAssistantAppOptions):
        raise TypeError(
            "Home Assistant daemon MQTT configuration requires App options."
        )
    if not isinstance(service, HomeAssistantMqttService):
        raise TypeError(
            "Home Assistant daemon MQTT configuration requires an MQTT service."
        )
    if service.ssl:
        raise ConfigurationError(
            "The Home Assistant MQTT service requires TLS, but daemon MQTT TLS "
            "is not supported yet."
        )

    return DaemonMqttConfiguration(
        host=service.host,
        port=service.port,
        username=service.username,
        password_environment_variable=(
            HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE
        ),
        topic_prefix=options.mqtt_topic_prefix,
        qos=1,
        retain=True,
        commands_enabled=False,
        home_assistant=DaemonMqttHomeAssistantConfiguration(
            enabled=True,
            controls_enabled=True,
        ),
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_home_assistant_daemon_mqtt_configuration(
    options: HomeAssistantAppOptions,
    service: HomeAssistantMqttService,
) -> str:
    """Render a password-free daemon MQTT manifest for the App runtime."""

    config = build_home_assistant_daemon_mqtt_configuration(options, service)
    assert config.username is not None
    assert config.password_environment_variable is not None

    return (
        f"version = {DAEMON_MQTT_CONFIG_VERSION}\n"
        "\n"
        "[broker]\n"
        f"host = {_toml_string(config.host)}\n"
        f"port = {config.port}\n"
        f"username = {_toml_string(config.username)}\n"
        "password_environment_variable = "
        f"{_toml_string(config.password_environment_variable)}\n"
        f"topic_prefix = {_toml_string(config.topic_prefix)}\n"
        f"qos = {config.qos}\n"
        f"retain = {str(config.retain).lower()}\n"
        f"commands_enabled = {str(config.commands_enabled).lower()}\n"
        "\n"
        "[home_assistant]\n"
        "enabled = true\n"
        "controls_enabled = true\n"
    )


def write_home_assistant_daemon_mqtt_configuration(
    path: str | Path,
    options: HomeAssistantAppOptions,
    service: HomeAssistantMqttService,
) -> Path:
    """Atomically write and validate the generated password-free MQTT manifest."""

    target = Path(path)
    if not target.is_absolute():
        raise ValueError(
            "Home Assistant daemon MQTT configuration path must be absolute."
        )
    if not target.name:
        raise ValueError(
            "Home Assistant daemon MQTT configuration path must identify a file."
        )

    rendered = render_home_assistant_daemon_mqtt_configuration(
        options,
        service,
    )
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())

        assert temporary is not None
        parsed = load_daemon_mqtt_configuration(temporary)
        if parsed is None:
            raise AssertionError(
                "Generated Home Assistant daemon MQTT configuration disappeared."
            )
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                temporary.unlink()

    return target


def home_assistant_mqtt_password_environment(
    service: HomeAssistantMqttService,
) -> dict[str, str]:
    """Return only the secret environment entry required by the daemon child."""

    if not isinstance(service, HomeAssistantMqttService):
        raise TypeError(
            "Home Assistant MQTT password environment requires an MQTT service."
        )
    return {
        HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE: service.password,
    }


__all__ = [
    "HOME_ASSISTANT_APP_DEFAULT_MQTT_TOPIC_PREFIX",
    "HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY",
    "HOME_ASSISTANT_APP_MQTT_PASSWORD_VARIABLE",
    "HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY",
    "HOME_ASSISTANT_APP_OPTIONS_PATH",
    "HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY",
    "HOME_ASSISTANT_APP_SUPERVISOR_TIMEOUT",
    "HOME_ASSISTANT_SUPERVISOR_TOKEN_VARIABLE",
    "HOME_ASSISTANT_SUPERVISOR_URL",
    "HomeAssistantAppAdvancedExposure",
    "HomeAssistantAppOptions",
    "HomeAssistantAppSupervisorInfo",
    "HomeAssistantMqttService",
    "build_home_assistant_daemon_mqtt_configuration",
    "fetch_home_assistant_mqtt_service",
    "fetch_home_assistant_app_supervisor_info",
    "home_assistant_mqtt_password_environment",
    "load_home_assistant_app_options",
    "parse_home_assistant_mqtt_service_response",
    "parse_home_assistant_app_supervisor_info_response",
    "reconcile_home_assistant_app_advanced_exposure",
    "render_home_assistant_daemon_mqtt_configuration",
    "write_home_assistant_daemon_mqtt_configuration",
]
