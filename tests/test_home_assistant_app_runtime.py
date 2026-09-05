from __future__ import annotations

from pathlib import Path

import pytest

from sds200.home_assistant_app import (
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppOptions,
)
from sds200.home_assistant_app_advanced import (
    default_home_assistant_app_advanced_access_paths,
    rotate_home_assistant_app_dashboard_password,
    rotate_home_assistant_app_server_identity,
)
from sds200.home_assistant_app_runtime import (
    HOME_ASSISTANT_APP_INGRESS_PORT,
    HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT,
    HOME_ASSISTANT_APP_RECORDING_DIRECTORY,
    HOME_ASSISTANT_APP_RTP_PORT,
    HOME_ASSISTANT_APP_RUNTIME_DIRECTORY,
    HomeAssistantAppRuntimePaths,
    build_home_assistant_daemon_command,
    build_home_assistant_media_command,
    build_home_assistant_native_web_command,
    build_home_assistant_web_command,
    default_home_assistant_app_runtime_paths,
)


def test_default_home_assistant_app_runtime_paths_are_private_and_absolute() -> None:
    paths = default_home_assistant_app_runtime_paths()

    assert paths.runtime_directory == HOME_ASSISTANT_APP_RUNTIME_DIRECTORY
    assert paths.mqtt_configuration == Path("/run/sdsctl/daemon-mqtt.toml")
    assert paths.daemon_socket == Path("/run/sdsctl/daemon.sock")
    assert paths.event_socket == Path("/run/sdsctl/events.sock")
    assert paths.pcmu_socket == Path("/run/sdsctl/pcmu.sock")
    assert paths.recording_file_socket == Path("/run/sdsctl/recordings.sock")
    assert paths.recording_directory == HOME_ASSISTANT_APP_RECORDING_DIRECTORY
    assert paths.recording_directory == Path("/media/sdsctl/recordings")
    assert paths.live_audio_socket == Path("/run/sdsctl/live-audio.sock")
    assert paths.live_audio_bridge_key == Path("/data/live-audio-bridge.key")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("runtime_directory", Path("run/sdsctl")),
        ("mqtt_configuration", Path("daemon-mqtt.toml")),
        ("daemon_socket", Path("daemon.sock")),
        ("event_socket", Path("events.sock")),
        ("pcmu_socket", Path("pcmu.sock")),
        ("recording_file_socket", Path("recordings.sock")),
        ("recording_directory", Path("recordings")),
    ],
)
def test_home_assistant_app_runtime_paths_require_absolute_paths(
    field_name: str,
    value: Path,
) -> None:
    values = {
        "runtime_directory": Path("/run/sdsctl"),
        "mqtt_configuration": Path("/run/sdsctl/daemon-mqtt.toml"),
        "daemon_socket": Path("/run/sdsctl/daemon.sock"),
        "event_socket": Path("/run/sdsctl/events.sock"),
        "pcmu_socket": Path("/run/sdsctl/pcmu.sock"),
        "recording_file_socket": Path("/run/sdsctl/recordings.sock"),
        "recording_directory": Path("/media/sdsctl/recordings"),
    }
    values[field_name] = value

    with pytest.raises(ValueError, match="must be absolute"):
        HomeAssistantAppRuntimePaths(**values)  # type: ignore[arg-type]


def test_home_assistant_app_runtime_files_stay_inside_runtime_directory() -> None:
    with pytest.raises(ValueError, match="directly inside /run/sdsctl"):
        HomeAssistantAppRuntimePaths(
            runtime_directory=Path("/run/sdsctl"),
            mqtt_configuration=Path("/tmp/daemon-mqtt.toml"),
            daemon_socket=Path("/run/sdsctl/daemon.sock"),
            event_socket=Path("/run/sdsctl/events.sock"),
            pcmu_socket=Path("/run/sdsctl/pcmu.sock"),
            recording_file_socket=Path("/run/sdsctl/recordings.sock"),
            recording_directory=Path("/media/sdsctl/recordings"),
        )


def test_home_assistant_daemon_command_uses_explicit_private_paths() -> None:
    paths = default_home_assistant_app_runtime_paths()
    options = HomeAssistantAppOptions(
        scanner_host="192.0.2.25",
        mqtt_topic_prefix="scanner/main",
    )

    assert build_home_assistant_daemon_command(
        options,
        paths,
    ) == (
        "sdsctl",
        "--host",
        "192.0.2.25",
        "daemon",
        "--rtp-bind-port",
        str(HOME_ASSISTANT_APP_RTP_PORT),
        "--mqtt-config",
        "/run/sdsctl/daemon-mqtt.toml",
        "--recording-directory",
        "/media/sdsctl/recordings",
        "--socket-path",
        "/run/sdsctl/daemon.sock",
        "--event-socket-path",
        "/run/sdsctl/events.sock",
        "--pcmu-socket-path",
        "/run/sdsctl/pcmu.sock",
        "--recording-file-socket-path",
        "/run/sdsctl/recordings.sock",
        "--waterfall-socket-path",
        "/run/sdsctl/waterfall.sock",
        "--live-audio-socket-path",
        "/run/sdsctl/live-audio.sock",
    )


def test_home_assistant_daemon_command_never_contains_mqtt_password() -> None:
    command = build_home_assistant_daemon_command(
        HomeAssistantAppOptions(scanner_host="scanner.local"),
        default_home_assistant_app_runtime_paths(),
    )

    assert all("password" not in argument.casefold() for argument in command)


def test_home_assistant_daemon_command_selects_explicit_remote_configuration(
    tmp_path: Path,
) -> None:
    remote_configuration = tmp_path / "run" / "daemon-remote.toml"

    command = build_home_assistant_daemon_command(
        HomeAssistantAppOptions(scanner_host="scanner.local"),
        default_home_assistant_app_runtime_paths(),
        remote_configuration=remote_configuration,
    )

    assert command[-2:] == ("--remote-config", str(remote_configuration))


def test_home_assistant_daemon_command_rejects_relative_remote_configuration() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        build_home_assistant_daemon_command(
            HomeAssistantAppOptions(scanner_host="scanner.local"),
            default_home_assistant_app_runtime_paths(),
            remote_configuration=Path("daemon-remote.toml"),
        )


def test_home_assistant_media_command_uses_only_private_bridge_paths() -> None:
    assert build_home_assistant_media_command(
        default_home_assistant_app_runtime_paths()
    ) == (
        "python3",
        "-m",
        "sds200.home_assistant_live_audio_service_runtime",
        "--daemon-live-audio-socket",
        "/run/sdsctl/live-audio.sock",
        "--bridge-secret-file",
        "/data/live-audio-bridge.key",
        "--listen-port",
        "8100",
    )


def test_home_assistant_runtime_uses_configured_media_subdirectory() -> None:
    options = HomeAssistantAppOptions(
        scanner_host="scanner.local",
        recording_directory="radio/sds200",
    )

    paths = default_home_assistant_app_runtime_paths(options)

    assert paths.recording_directory == Path("/media/radio/sds200")


def test_home_assistant_web_command_enables_ingress_and_private_clients() -> None:
    paths = default_home_assistant_app_runtime_paths()

    assert build_home_assistant_web_command(paths) == (
        "sdsctl",
        "web",
        "--home-assistant-ingress",
        "--daemon-socket-path",
        "/run/sdsctl/daemon.sock",
        "--daemon-event-socket-path",
        "/run/sdsctl/events.sock",
        "--daemon-pcmu-socket-path",
        "/run/sdsctl/pcmu.sock",
        "--daemon-recording-file-socket-path",
        "/run/sdsctl/recordings.sock",
        "--daemon-waterfall-socket-path",
        "/run/sdsctl/waterfall.sock",
        "--listen-port",
        str(HOME_ASSISTANT_APP_INGRESS_PORT),
    )


def test_home_assistant_native_web_command_is_separate_and_secret_free(
    tmp_path: Path,
) -> None:
    advanced_paths = default_home_assistant_app_advanced_access_paths(
        root=tmp_path / "data" / "advanced-access",
        runtime_directory=tmp_path / "run" / "sdsctl",
    )
    rotate_home_assistant_app_server_identity(
        advanced_paths,
        "sdsctl.local",
        generator=lambda server_name: (
            b"-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----\n",
            b"-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----\n",
        ),
    )
    password = rotate_home_assistant_app_dashboard_password(advanced_paths).password
    options = HomeAssistantAppOptions(
        scanner_host="scanner.local",
        native_dashboard_enabled=True,
        advanced_access_server_name="sdsctl.local",
    )
    exposure = HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7",
        native_dashboard_host_port=10443,
    )

    command = build_home_assistant_native_web_command(
        options,
        exposure,
        default_home_assistant_app_runtime_paths(),
        advanced_paths,
    )

    assert command[:4] == (
        "sdsctl",
        "web",
        "--authenticated-lan",
        "--lan-listen-address",
    )
    assert command[4] == "172.30.33.7"
    assert "--home-assistant-ingress" not in command
    assert command[command.index("--listen-port") + 1] == str(
        HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT
    )
    assert command[command.index("--lan-public-port") + 1] == "10443"
    assert command[command.index("--lan-origin") + 1] == (
        "https://sdsctl.local:10443"
    )
    assert password not in command
    assert command[command.index("--lan-password-file") + 1] == str(
        advanced_paths.dashboard_password
    )
    assert "--lan-display-password-file" not in command
    display_password = rotate_home_assistant_app_dashboard_password(
        advanced_paths, display_only=True,
    ).password
    display_command = build_home_assistant_native_web_command(
        options, exposure, default_home_assistant_app_runtime_paths(), advanced_paths,
    )
    assert display_command[display_command.index("--lan-display-password-file") + 1] == str(
        advanced_paths.display_password
    )
    assert display_password not in display_command


def test_home_assistant_native_web_command_requires_initialized_lifecycle(
    tmp_path: Path,
) -> None:
    advanced_paths = default_home_assistant_app_advanced_access_paths(
        root=tmp_path / "advanced-access",
        runtime_directory=tmp_path / "run",
    )
    options = HomeAssistantAppOptions(
        scanner_host="scanner.local",
        native_dashboard_enabled=True,
        advanced_access_server_name="sdsctl.local",
    )
    exposure = HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7",
        native_dashboard_host_port=8443,
    )

    with pytest.raises(ValueError, match="identity is unavailable"):
        build_home_assistant_native_web_command(
            options,
            exposure,
            default_home_assistant_app_runtime_paths(),
            advanced_paths,
        )


@pytest.mark.parametrize("port", [0, 65536])
def test_home_assistant_web_command_rejects_invalid_ingress_port(
    port: int,
) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        build_home_assistant_web_command(
            default_home_assistant_app_runtime_paths(),
            ingress_port=port,
        )
