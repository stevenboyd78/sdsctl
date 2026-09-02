from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .daemon_ipc import (
    DAEMON_EVENT_SOCKET_FILENAME,
    DAEMON_LIVE_AUDIO_SOCKET_FILENAME,
    DAEMON_PCMU_SOCKET_FILENAME,
    DAEMON_RECORDING_FILE_SOCKET_FILENAME,
    DAEMON_SOCKET_FILENAME,
    DAEMON_WATERFALL_SOCKET_FILENAME,
)
from .home_assistant_app import (
    HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY,
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppOptions,
)
from .home_assistant_app_advanced import (
    HomeAssistantAppAdvancedAccessPaths,
    inspect_home_assistant_app_advanced_access,
    load_home_assistant_app_advanced_access_state,
)
from .home_assistant_live_audio_service_runtime import (
    HOME_ASSISTANT_LIVE_AUDIO_SERVICE_PORT,
)

HOME_ASSISTANT_APP_RUNTIME_DIRECTORY = Path("/run/sdsctl")
HOME_ASSISTANT_APP_MQTT_CONFIG_FILENAME = "daemon-mqtt.toml"
HOME_ASSISTANT_APP_MEDIA_DIRECTORY = Path("/media")
HOME_ASSISTANT_APP_LEGACY_RECORDING_DIRECTORY = Path("/data/recordings")
HOME_ASSISTANT_APP_RECORDING_DIRECTORY = (
    HOME_ASSISTANT_APP_MEDIA_DIRECTORY
    / HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY
)
HOME_ASSISTANT_APP_INGRESS_PORT = 8099
HOME_ASSISTANT_APP_RTP_PORT = 50000
HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT = 50443
HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT = 8443
HOME_ASSISTANT_APP_EXECUTABLE = "sdsctl"
HOME_ASSISTANT_APP_MEDIA_EXECUTABLE = "python3"
HOME_ASSISTANT_APP_LIVE_AUDIO_BRIDGE_KEY = Path(
    "/data/live-audio-bridge.key"
)


def _require_absolute_path(value: object, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{label} must be a path.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute: {path}")
    return path


def _require_executable(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Home Assistant App executable must be a string.")
    if not value or value.strip() != value:
        raise ValueError(
            "Home Assistant App executable must not be empty or padded."
        )
    if "\x00" in value:
        raise ValueError(
            "Home Assistant App executable must not contain a null byte."
        )
    return value


@dataclass(frozen=True, slots=True)
class HomeAssistantAppRuntimePaths:
    """Absolute runtime paths shared by the daemon and Ingress web children."""

    runtime_directory: Path
    mqtt_configuration: Path
    daemon_socket: Path
    event_socket: Path
    pcmu_socket: Path
    recording_file_socket: Path
    recording_directory: Path
    waterfall_socket: Path | None = None
    live_audio_socket: Path | None = None
    live_audio_bridge_key: Path | None = None

    def __post_init__(self) -> None:
        runtime_directory = _require_absolute_path(
            self.runtime_directory,
            label="Home Assistant App runtime directory",
        )
        object.__setattr__(self, "runtime_directory", runtime_directory)
        if self.waterfall_socket is None:
            object.__setattr__(
                self,
                "waterfall_socket",
                runtime_directory / DAEMON_WATERFALL_SOCKET_FILENAME,
            )
        if self.live_audio_socket is None:
            object.__setattr__(
                self,
                "live_audio_socket",
                runtime_directory / DAEMON_LIVE_AUDIO_SOCKET_FILENAME,
            )
        if self.live_audio_bridge_key is None:
            object.__setattr__(
                self,
                "live_audio_bridge_key",
                runtime_directory / "live-audio-bridge.key",
            )

        for field_name, label in (
            ("mqtt_configuration", "Home Assistant App MQTT configuration"),
            ("daemon_socket", "Home Assistant App daemon socket"),
            ("event_socket", "Home Assistant App event socket"),
            ("pcmu_socket", "Home Assistant App PCMU socket"),
            (
                "recording_file_socket",
                "Home Assistant App recording-file socket",
            ),
            ("waterfall_socket", "Home Assistant App waterfall socket"),
            (
                "live_audio_socket",
                "Home Assistant App live-audio socket",
            ),
        ):
            path = _require_absolute_path(
                getattr(self, field_name),
                label=label,
            )
            if path.parent != runtime_directory:
                raise ValueError(
                    f"{label} must be directly inside "
                    f"{runtime_directory}: {path}"
                )
            object.__setattr__(self, field_name, path)

        object.__setattr__(
            self,
            "recording_directory",
            _require_absolute_path(
                self.recording_directory,
                label="Home Assistant App recording directory",
            ),
        )
        object.__setattr__(
            self,
            "live_audio_bridge_key",
            _require_absolute_path(
                self.live_audio_bridge_key,
                label="Home Assistant App live-audio bridge key",
            ),
        )


def default_home_assistant_app_runtime_paths(
    options: HomeAssistantAppOptions | None = None,
) -> HomeAssistantAppRuntimePaths:
    """Return private runtime paths plus the configured mapped-media library."""

    runtime = HOME_ASSISTANT_APP_RUNTIME_DIRECTORY
    recording_relative = (
        HOME_ASSISTANT_APP_DEFAULT_RECORDING_DIRECTORY
        if options is None
        else options.recording_directory
    )
    return HomeAssistantAppRuntimePaths(
        runtime_directory=runtime,
        mqtt_configuration=runtime / HOME_ASSISTANT_APP_MQTT_CONFIG_FILENAME,
        daemon_socket=runtime / DAEMON_SOCKET_FILENAME,
        event_socket=runtime / DAEMON_EVENT_SOCKET_FILENAME,
        pcmu_socket=runtime / DAEMON_PCMU_SOCKET_FILENAME,
        recording_file_socket=(
            runtime / DAEMON_RECORDING_FILE_SOCKET_FILENAME
        ),
        waterfall_socket=runtime / DAEMON_WATERFALL_SOCKET_FILENAME,
        live_audio_socket=runtime / DAEMON_LIVE_AUDIO_SOCKET_FILENAME,
        live_audio_bridge_key=HOME_ASSISTANT_APP_LIVE_AUDIO_BRIDGE_KEY,
        recording_directory=(
            HOME_ASSISTANT_APP_MEDIA_DIRECTORY / recording_relative
        ),
    )


def build_home_assistant_daemon_command(
    options: HomeAssistantAppOptions,
    paths: HomeAssistantAppRuntimePaths,
    *,
    remote_configuration: Path | None = None,
    executable: str = HOME_ASSISTANT_APP_EXECUTABLE,
) -> tuple[str, ...]:
    """Build the single-owner daemon child command without embedding secrets."""

    if not isinstance(options, HomeAssistantAppOptions):
        raise TypeError(
            "Home Assistant daemon command requires App options."
        )
    if not isinstance(paths, HomeAssistantAppRuntimePaths):
        raise TypeError(
            "Home Assistant daemon command requires App runtime paths."
        )

    program = _require_executable(executable)
    assert paths.waterfall_socket is not None
    assert paths.live_audio_socket is not None
    command = (
        program,
        "--host",
        options.scanner_host,
        "daemon",
        "--rtp-bind-port",
        str(HOME_ASSISTANT_APP_RTP_PORT),
        "--mqtt-config",
        os.fspath(paths.mqtt_configuration),
        "--recording-directory",
        os.fspath(paths.recording_directory),
        "--socket-path",
        os.fspath(paths.daemon_socket),
        "--event-socket-path",
        os.fspath(paths.event_socket),
        "--pcmu-socket-path",
        os.fspath(paths.pcmu_socket),
        "--recording-file-socket-path",
        os.fspath(paths.recording_file_socket),
        "--waterfall-socket-path",
        os.fspath(paths.waterfall_socket),
        "--live-audio-socket-path",
        os.fspath(paths.live_audio_socket),
    )
    if remote_configuration is None:
        return command
    normalized_remote_configuration = _require_absolute_path(
        remote_configuration,
        label="Home Assistant App remote-daemon configuration",
    )
    return command + (
        "--remote-config",
        os.fspath(normalized_remote_configuration),
    )


def build_home_assistant_web_command(
    paths: HomeAssistantAppRuntimePaths,
    *,
    executable: str = HOME_ASSISTANT_APP_EXECUTABLE,
    ingress_port: int = HOME_ASSISTANT_APP_INGRESS_PORT,
) -> tuple[str, ...]:
    """Build the Ingress web child command against the private daemon sockets."""

    if not isinstance(paths, HomeAssistantAppRuntimePaths):
        raise TypeError(
            "Home Assistant web command requires App runtime paths."
        )
    if type(ingress_port) is not int:
        raise TypeError(
            "Home Assistant App Ingress port must be an integer."
        )
    if not 1 <= ingress_port <= 65535:
        raise ValueError(
            "Home Assistant App Ingress port must be between 1 and 65535."
        )

    program = _require_executable(executable)
    assert paths.waterfall_socket is not None
    return (
        program,
        "web",
        "--home-assistant-ingress",
        "--daemon-socket-path",
        os.fspath(paths.daemon_socket),
        "--daemon-event-socket-path",
        os.fspath(paths.event_socket),
        "--daemon-pcmu-socket-path",
        os.fspath(paths.pcmu_socket),
        "--daemon-recording-file-socket-path",
        os.fspath(paths.recording_file_socket),
        "--daemon-waterfall-socket-path",
        os.fspath(paths.waterfall_socket),
        "--listen-port",
        str(ingress_port),
    )


def build_home_assistant_native_web_command(
    options: HomeAssistantAppOptions,
    exposure: HomeAssistantAppAdvancedExposure,
    paths: HomeAssistantAppRuntimePaths,
    advanced_paths: HomeAssistantAppAdvancedAccessPaths,
    *,
    executable: str = HOME_ASSISTANT_APP_EXECUTABLE,
    listen_port: int = HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT,
) -> tuple[str, ...]:
    """Build the separately authenticated, non-Ingress native dashboard."""

    if not isinstance(options, HomeAssistantAppOptions):
        raise TypeError("Home Assistant native web command requires App options.")
    if not isinstance(exposure, HomeAssistantAppAdvancedExposure):
        raise TypeError("Home Assistant native web command requires App exposure.")
    if not isinstance(paths, HomeAssistantAppRuntimePaths):
        raise TypeError("Home Assistant native web command requires App runtime paths.")
    if not isinstance(advanced_paths, HomeAssistantAppAdvancedAccessPaths):
        raise TypeError(
            "Home Assistant native web command requires advanced-access paths."
        )
    if type(listen_port) is not int or not 1 <= listen_port <= 65535:
        raise ValueError(
            "Home Assistant App native dashboard port must be between 1 and 65535."
        )
    if not options.native_dashboard_enabled:
        raise ValueError("Home Assistant App native dashboard is not enabled.")
    if exposure.native_dashboard_host_port is None:
        raise ValueError("Home Assistant App native dashboard is not published.")

    snapshot = inspect_home_assistant_app_advanced_access(advanced_paths)
    state = load_home_assistant_app_advanced_access_state(advanced_paths)
    if not snapshot.identity_present or state.identity_generation is None:
        raise ValueError("Home Assistant App native dashboard identity is unavailable.")
    if not snapshot.dashboard_password_present:
        raise ValueError("Home Assistant App native dashboard password is unavailable.")

    program = _require_executable(executable)
    assert paths.waterfall_socket is not None
    origin_host = options.advanced_access_server_name
    if ":" in origin_host:
        origin_host = f"[{origin_host}]"
    return (
        program,
        "web",
        "--authenticated-lan",
        "--lan-listen-address",
        exposure.container_address,
        "--lan-origin",
        f"https://{origin_host}:{exposure.native_dashboard_host_port}",
        "--lan-public-port",
        str(exposure.native_dashboard_host_port),
        "--lan-password-file",
        os.fspath(advanced_paths.dashboard_password),
        "--lan-tls-certfile",
        os.fspath(advanced_paths.certificate(state.identity_generation)),
        "--lan-tls-keyfile",
        os.fspath(advanced_paths.private_key(state.identity_generation)),
        "--daemon-socket-path",
        os.fspath(paths.daemon_socket),
        "--daemon-event-socket-path",
        os.fspath(paths.event_socket),
        "--daemon-pcmu-socket-path",
        os.fspath(paths.pcmu_socket),
        "--daemon-recording-file-socket-path",
        os.fspath(paths.recording_file_socket),
        "--daemon-waterfall-socket-path",
        os.fspath(paths.waterfall_socket),
        "--listen-port",
        str(listen_port),
    )


def build_home_assistant_media_command(
    paths: HomeAssistantAppRuntimePaths,
    *,
    executable: str = HOME_ASSISTANT_APP_MEDIA_EXECUTABLE,
    listen_port: int = HOME_ASSISTANT_LIVE_AUDIO_SERVICE_PORT,
) -> tuple[str, ...]:
    """Build the private Core-facing media child command."""

    if not isinstance(paths, HomeAssistantAppRuntimePaths):
        raise TypeError("Home Assistant media command requires App runtime paths.")
    if type(listen_port) is not int or not 1 <= listen_port <= 65535:
        raise ValueError("Home Assistant media port must be between 1 and 65535.")
    program = _require_executable(executable)
    assert paths.live_audio_socket is not None
    assert paths.live_audio_bridge_key is not None
    return (
        program,
        "-m",
        "sds200.home_assistant_live_audio_service_runtime",
        "--daemon-live-audio-socket",
        os.fspath(paths.live_audio_socket),
        "--bridge-secret-file",
        os.fspath(paths.live_audio_bridge_key),
        "--listen-port",
        str(listen_port),
    )


__all__ = [
    "HOME_ASSISTANT_APP_EXECUTABLE",
    "HOME_ASSISTANT_APP_LIVE_AUDIO_BRIDGE_KEY",
    "HOME_ASSISTANT_APP_MEDIA_EXECUTABLE",
    "HOME_ASSISTANT_APP_INGRESS_PORT",
    "HOME_ASSISTANT_APP_MQTT_CONFIG_FILENAME",
    "HOME_ASSISTANT_APP_MEDIA_DIRECTORY",
    "HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT",
    "HOME_ASSISTANT_APP_LEGACY_RECORDING_DIRECTORY",
    "HOME_ASSISTANT_APP_RECORDING_DIRECTORY",
    "HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT",
    "HOME_ASSISTANT_APP_RTP_PORT",
    "HOME_ASSISTANT_APP_RUNTIME_DIRECTORY",
    "HomeAssistantAppRuntimePaths",
    "build_home_assistant_daemon_command",
    "build_home_assistant_media_command",
    "build_home_assistant_native_web_command",
    "build_home_assistant_web_command",
    "default_home_assistant_app_runtime_paths",
]
