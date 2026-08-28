from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from sds200 import DaemonSocketLocation, cli, web_auth, web_dashboard
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_server import (
    WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
    WEB_DASHBOARD_DEFAULT_PORT,
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST,
)


class FakeDaemonApiClient:
    instances: list[FakeDaemonApiClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_response_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.instances.append(self)

    def __enter__(self) -> FakeDaemonApiClient:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback

    def hello(self) -> Mapping[str, object]:
        return {}

    def runtime_snapshot(self) -> Mapping[str, object]:
        return {}


class FakeDaemonEventClient:
    instances: list[FakeDaemonEventClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_event_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_event_bytes = max_event_bytes
        self.instances.append(self)


class FakeDaemonRecordingFileClient:
    instances: list[FakeDaemonRecordingFileClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_content_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_content_bytes = max_content_bytes
        self.instances.append(self)


class FakeDaemonPcmuClient:
    instances: list[FakeDaemonPcmuClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_endpoint_bytes: int,
        max_frame_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_endpoint_bytes = max_endpoint_bytes
        self.max_frame_bytes = max_frame_bytes
        self.instances.append(self)


class FakeDaemonWaterfallClient:
    instances: list[FakeDaemonWaterfallClient] = []

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float,
        max_record_bytes: int,
    ) -> None:
        self.location = location
        self.timeout = timeout
        self.max_record_bytes = max_record_bytes
        self.instances.append(self)


def test_web_parser_uses_loopback_defaults() -> None:
    args = cli.build_parser().parse_args(["web"])

    assert args.action == "web"
    assert args.home_assistant_ingress is False
    assert args.container_exposure is False
    assert args.authenticated_lan is False
    assert args.lan_listen_address is None
    assert args.lan_origin is None
    assert args.lan_password_env is None
    assert args.lan_tls_certfile is None
    assert args.lan_tls_keyfile is None
    assert args.daemon_socket_path is None
    assert args.daemon_event_socket_path is None
    assert args.daemon_pcmu_socket_path is None
    assert args.daemon_recording_file_socket_path is None
    assert args.daemon_waterfall_socket_path is None
    assert args.daemon_timeout == 5.0
    assert args.daemon_max_response_bytes is None
    assert args.daemon_max_event_bytes is None
    assert args.daemon_pcmu_max_endpoint_bytes is None
    assert args.daemon_pcmu_max_frame_bytes is None
    assert args.daemon_recording_file_max_content_bytes is None
    assert args.daemon_waterfall_max_record_bytes is None
    assert args.listen_address is None
    assert args.listen_port == WEB_DASHBOARD_DEFAULT_PORT
    assert args.access_log is True


def test_web_parser_accepts_home_assistant_ingress() -> None:
    args = cli.build_parser().parse_args(
        ["web", "--home-assistant-ingress"]
    )

    assert args.home_assistant_ingress is True
    assert args.listen_address is None


def test_web_parser_accepts_container_exposure() -> None:
    args = cli.build_parser().parse_args(["web", "--container-exposure"])

    assert args.container_exposure is True
    assert args.home_assistant_ingress is False
    assert args.listen_address is None


def test_web_parser_accepts_authenticated_lan_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "web",
            "--authenticated-lan",
            "--lan-listen-address",
            "192.168.0.25",
            "--lan-origin",
            "https://scanner.example:8443",
            "--lan-password-env",
            "SDSCTL_WEB_PASSWORD",
            "--lan-tls-certfile",
            "/run/secrets/dashboard.crt",
            "--lan-tls-keyfile",
            "/run/secrets/dashboard.key",
            "--listen-port",
            "8443",
        ]
    )

    assert args.authenticated_lan is True
    assert args.lan_listen_address == "192.168.0.25"
    assert args.lan_origin == "https://scanner.example:8443"
    assert args.lan_password_env == "SDSCTL_WEB_PASSWORD"
    assert args.lan_tls_certfile == Path("/run/secrets/dashboard.crt")
    assert args.lan_tls_keyfile == Path("/run/secrets/dashboard.key")
    assert args.listen_port == 8443


@pytest.mark.parametrize(
    "name",
    ["9PASSWORD", "DASHBOARD-PASSWORD", " DBOARD_PASSWORD", "PÁSSWORD"],
)
def test_web_parser_rejects_invalid_password_environment_names(name: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["web", "--lan-password-env", name])

    assert error.value.code == 2


def test_web_parser_accepts_explicit_local_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "web",
            "--daemon-socket-path",
            "/tmp/sdsctl/daemon.sock",
            "--daemon-event-socket-path",
            "/tmp/sdsctl/events.sock",
            "--daemon-pcmu-socket-path",
            "/tmp/sdsctl/pcmu.sock",
            "--daemon-recording-file-socket-path",
            "/tmp/sdsctl/recordings.sock",
            "--daemon-waterfall-socket-path",
            "/tmp/sdsctl/waterfall.sock",
            "--daemon-timeout",
            "2.5",
            "--daemon-max-response-bytes",
            "8192",
            "--daemon-max-event-bytes",
            "4096",
            "--daemon-pcmu-max-endpoint-bytes",
            "2048",
            "--daemon-pcmu-max-frame-bytes",
            "65536",
            "--daemon-recording-file-max-content-bytes",
            "1048576",
            "--daemon-waterfall-max-record-bytes",
            "32768",
            "--listen-address",
            "::1",
            "--listen-port",
            "8123",
            "--no-access-log",
        ]
    )

    assert args.daemon_socket_path == Path("/tmp/sdsctl/daemon.sock")
    assert args.daemon_event_socket_path == Path("/tmp/sdsctl/events.sock")
    assert args.daemon_pcmu_socket_path == Path("/tmp/sdsctl/pcmu.sock")
    assert args.daemon_recording_file_socket_path == Path(
        "/tmp/sdsctl/recordings.sock"
    )
    assert args.daemon_waterfall_socket_path == Path(
        "/tmp/sdsctl/waterfall.sock"
    )
    assert args.daemon_timeout == 2.5
    assert args.daemon_max_response_bytes == 8192
    assert args.daemon_max_event_bytes == 4096
    assert args.daemon_pcmu_max_endpoint_bytes == 2048
    assert args.daemon_pcmu_max_frame_bytes == 65536
    assert args.daemon_recording_file_max_content_bytes == 1048576
    assert args.daemon_waterfall_max_record_bytes == 32768
    assert args.listen_address == "::1"
    assert args.listen_port == 8123
    assert args.access_log is False


@pytest.mark.parametrize(
    "address",
    ["0.0.0.0", "::", "192.168.0.25", "scanner.local"],
)
def test_web_parser_rejects_non_loopback_address(address: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["web", "--listen-address", address])

    assert error.value.code == 2


@pytest.mark.parametrize(
    ("frame_bytes", "message"),
    [
        (81, "must be at least 82"),
        (131073, "must not exceed the browser stream limit of 131072"),
    ],
)
def test_web_cli_rejects_pcmu_frame_limits_outside_browser_contract(
    frame_bytes: int,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "web",
            "--daemon-pcmu-max-frame-bytes",
            str(frame_bytes),
        ],
        environ={},
    )

    assert result == 2
    assert message in capsys.readouterr().err


def test_web_cli_rejects_waterfall_record_limit_above_browser_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "web",
            "--daemon-waterfall-max-record-bytes",
            "65537",
        ],
        environ={},
    )

    assert result == 2
    assert (
        "--daemon-waterfall-max-record-bytes must not exceed the browser "
        "stream limit of 65536"
    ) in capsys.readouterr().err


def test_web_cli_builds_daemon_clients_and_runs_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeDaemonApiClient.instances.clear()
    FakeDaemonEventClient.instances.clear()
    FakeDaemonPcmuClient.instances.clear()
    FakeDaemonRecordingFileClient.instances.clear()
    FakeDaemonWaterfallClient.instances.clear()
    captured_api_factories: list[Callable[[], object]] = []
    captured_event_factories: list[Callable[[], object]] = []
    captured_pcmu_factories: list[Callable[[], object]] = []
    captured_recording_file_factories: list[Callable[[], object]] = []
    captured_waterfall_factories: list[Callable[[], object]] = []
    captured_theme_roots: list[Path] = []
    app = object()
    captured_ingress_modes: list[bool] = []
    server_calls: list[tuple[object, str, int, bool, bool]] = []

    def fake_create_app(
        api_client_factory: Callable[[], object],
        event_client_factory: Callable[[], object],
        pcmu_client_factory: Callable[[], object],
        recording_file_client_factory: Callable[[], object],
        waterfall_client_factory: Callable[[], object],
        *,
        home_assistant_ingress: bool = False,
        lan_authentication: WebDashboardAuthentication | None = None,
        managed_theme_root: Path | None = None,
    ) -> object:
        assert lan_authentication is None
        assert managed_theme_root is not None
        captured_theme_roots.append(managed_theme_root)
        captured_api_factories.append(api_client_factory)
        captured_event_factories.append(event_client_factory)
        captured_pcmu_factories.append(pcmu_client_factory)
        captured_recording_file_factories.append(
            recording_file_client_factory
        )
        captured_waterfall_factories.append(waterfall_client_factory)
        captured_ingress_modes.append(home_assistant_ingress)
        return app

    def fake_run_server(
        selected_app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        home_assistant_ingress: bool = False,
        container_exposure: bool = False,
        authenticated_lan: bool = False,
        ssl_certfile: Path | None = None,
        ssl_keyfile: Path | None = None,
    ) -> int:
        assert container_exposure is False
        assert authenticated_lan is False
        assert ssl_certfile is None
        assert ssl_keyfile is None
        server_calls.append(
            (
                selected_app,
                host,
                port,
                access_log,
                home_assistant_ingress,
            )
        )
        return 0

    monkeypatch.setattr(cli, "DaemonApiClient", FakeDaemonApiClient)
    monkeypatch.setattr(cli, "DaemonEventClient", FakeDaemonEventClient)
    monkeypatch.setattr(cli, "DaemonPcmuClient", FakeDaemonPcmuClient)
    monkeypatch.setattr(
        cli,
        "DaemonRecordingFileClient",
        FakeDaemonRecordingFileClient,
    )
    monkeypatch.setattr(
        cli,
        "DaemonWaterfallClient",
        FakeDaemonWaterfallClient,
    )
    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)

    socket_path = tmp_path / "daemon.sock"
    event_socket_path = tmp_path / "events.sock"
    pcmu_socket_path = tmp_path / "pcmu.sock"
    recording_file_socket_path = tmp_path / "recordings.sock"
    waterfall_socket_path = tmp_path / "waterfall.sock"
    result = cli.main(
        [
            "web",
            "--daemon-socket-path",
            str(socket_path),
            "--daemon-event-socket-path",
            str(event_socket_path),
            "--daemon-pcmu-socket-path",
            str(pcmu_socket_path),
            "--daemon-recording-file-socket-path",
            str(recording_file_socket_path),
            "--daemon-waterfall-socket-path",
            str(waterfall_socket_path),
            "--daemon-timeout",
            "2.5",
            "--daemon-max-response-bytes",
            "8192",
            "--daemon-max-event-bytes",
            "4096",
            "--daemon-pcmu-max-endpoint-bytes",
            "2048",
            "--daemon-pcmu-max-frame-bytes",
            "65536",
            "--daemon-recording-file-max-content-bytes",
            "1048576",
            "--daemon-waterfall-max-record-bytes",
            "32768",
            "--listen-address",
            "localhost",
            "--listen-port",
            "8123",
            "--no-access-log",
        ],
        environ={"XDG_CONFIG_HOME": str(tmp_path / "config")},
    )

    assert result == 0
    assert server_calls == [(app, "127.0.0.1", 8123, False, False)]
    assert captured_ingress_modes == [False]
    assert captured_theme_roots == [tmp_path / "config" / "sdsctl" / "themes"]
    assert len(captured_api_factories) == 1
    assert len(captured_event_factories) == 1
    assert len(captured_pcmu_factories) == 1
    assert len(captured_recording_file_factories) == 1
    assert len(captured_waterfall_factories) == 1

    daemon_client = captured_api_factories[0]()
    event_client = captured_event_factories[0]()
    pcmu_client = captured_pcmu_factories[0]()
    recording_file_client = captured_recording_file_factories[0]()
    waterfall_client = captured_waterfall_factories[0]()

    assert isinstance(daemon_client, FakeDaemonApiClient)
    assert daemon_client.location.path == socket_path
    assert daemon_client.location.source.value == "explicit"
    assert daemon_client.timeout == 2.5
    assert daemon_client.max_response_bytes == 8192

    assert isinstance(event_client, FakeDaemonEventClient)
    assert event_client.location.path == event_socket_path
    assert event_client.location.source.value == "explicit"
    assert event_client.timeout == 2.5
    assert event_client.max_event_bytes == 4096

    assert isinstance(pcmu_client, FakeDaemonPcmuClient)
    assert pcmu_client.location.path == pcmu_socket_path
    assert pcmu_client.location.source.value == "explicit"
    assert pcmu_client.timeout == 2.5
    assert pcmu_client.max_endpoint_bytes == 2048
    assert pcmu_client.max_frame_bytes == 65536

    assert isinstance(
        recording_file_client,
        FakeDaemonRecordingFileClient,
    )
    assert recording_file_client.location.path == recording_file_socket_path
    assert recording_file_client.location.source.value == "explicit"
    assert recording_file_client.timeout == 2.5
    assert recording_file_client.max_content_bytes == 1048576

    assert isinstance(waterfall_client, FakeDaemonWaterfallClient)
    assert waterfall_client.location.path == waterfall_socket_path
    assert waterfall_client.location.source.value == "explicit"
    assert waterfall_client.timeout == 2.5
    assert waterfall_client.max_record_bytes == 32768


def test_web_cli_home_assistant_ingress_binds_wildcard_and_enables_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = object()
    create_calls: list[bool] = []
    server_calls: list[tuple[object, str, int, bool, bool]] = []

    def fake_create_app(
        api_client_factory: Callable[[], object],
        event_client_factory: Callable[[], object],
        pcmu_client_factory: Callable[[], object],
        recording_file_client_factory: Callable[[], object],
        waterfall_client_factory: Callable[[], object],
        *,
        home_assistant_ingress: bool = False,
        lan_authentication: WebDashboardAuthentication | None = None,
        managed_theme_root: Path | None = None,
    ) -> object:
        del (
            api_client_factory,
            event_client_factory,
            pcmu_client_factory,
            recording_file_client_factory,
            waterfall_client_factory,
        )
        assert lan_authentication is None
        assert managed_theme_root is not None
        create_calls.append(home_assistant_ingress)
        return app

    def fake_run_server(
        selected_app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        home_assistant_ingress: bool = False,
        container_exposure: bool = False,
        authenticated_lan: bool = False,
        ssl_certfile: Path | None = None,
        ssl_keyfile: Path | None = None,
    ) -> int:
        assert container_exposure is False
        assert authenticated_lan is False
        assert ssl_certfile is None
        assert ssl_keyfile is None
        server_calls.append(
            (
                selected_app,
                host,
                port,
                access_log,
                home_assistant_ingress,
            )
        )
        return 0

    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)

    result = cli.main(
        [
            "web",
            "--home-assistant-ingress",
            "--daemon-socket-path",
            str(tmp_path / "daemon.sock"),
            "--daemon-event-socket-path",
            str(tmp_path / "events.sock"),
            "--daemon-pcmu-socket-path",
            str(tmp_path / "pcmu.sock"),
            "--daemon-recording-file-socket-path",
            str(tmp_path / "recordings.sock"),
            "--listen-port",
            "8099",
        ],
        environ={},
    )

    assert result == 0
    assert create_calls == [True]
    assert server_calls == [
        (
            app,
            WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST,
            8099,
            True,
            True,
        )
    ]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "::1"],
)
def test_web_cli_home_assistant_ingress_rejects_listen_address(
    address: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "web",
            "--home-assistant-ingress",
            "--listen-address",
            address,
        ],
        environ={},
    )

    assert result == 2
    assert (
        "--listen-address cannot be used with --home-assistant-ingress"
        in capsys.readouterr().err
    )


def test_web_cli_container_exposure_uses_wildcard_without_ingress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    create_calls: list[bool] = []
    server_calls: list[tuple[str, bool, bool]] = []

    def fake_create_app(
        *args: object,
        home_assistant_ingress: bool = False,
        lan_authentication: WebDashboardAuthentication | None = None,
        managed_theme_root: Path | None = None,
    ) -> object:
        del args
        assert lan_authentication is None
        assert managed_theme_root is not None
        create_calls.append(home_assistant_ingress)
        return object()

    def fake_run_server(
        app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        home_assistant_ingress: bool = False,
        container_exposure: bool = False,
        authenticated_lan: bool = False,
        ssl_certfile: Path | None = None,
        ssl_keyfile: Path | None = None,
    ) -> int:
        del app, port, access_log
        assert authenticated_lan is False
        assert ssl_certfile is None
        assert ssl_keyfile is None
        server_calls.append((host, home_assistant_ingress, container_exposure))
        return 0

    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)
    result = cli.main(
        [
            "web",
            "--container-exposure",
            "--daemon-socket-path",
            str(tmp_path / "daemon.sock"),
            "--daemon-event-socket-path",
            str(tmp_path / "events.sock"),
            "--daemon-pcmu-socket-path",
            str(tmp_path / "pcmu.sock"),
            "--daemon-recording-file-socket-path",
            str(tmp_path / "recordings.sock"),
        ],
        environ={},
    )

    assert result == 0
    assert create_calls == [False]
    assert server_calls == [(WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST, False, True)]


def test_web_cli_authenticated_lan_resolves_secret_and_configures_tls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = object()
    authentications: list[WebDashboardAuthentication] = []
    server_calls: list[
        tuple[object, str, int, bool, bool, Path | None, Path | None]
    ] = []

    def fake_create_app(
        *args: object,
        home_assistant_ingress: bool = False,
        lan_authentication: WebDashboardAuthentication | None = None,
        managed_theme_root: Path | None = None,
    ) -> object:
        del args
        assert home_assistant_ingress is False
        assert lan_authentication is not None
        assert managed_theme_root is not None
        authentications.append(lan_authentication)
        return app

    def fake_run_server(
        selected_app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        home_assistant_ingress: bool = False,
        container_exposure: bool = False,
        authenticated_lan: bool = False,
        ssl_certfile: Path | None = None,
        ssl_keyfile: Path | None = None,
    ) -> int:
        assert home_assistant_ingress is False
        assert container_exposure is False
        server_calls.append(
            (
                selected_app,
                host,
                port,
                authenticated_lan,
                access_log,
                ssl_certfile,
                ssl_keyfile,
            )
        )
        return 0

    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        fake_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", fake_run_server)
    certificate = tmp_path / "dashboard.crt"
    private_key = tmp_path / "dashboard.key"
    certificate.write_text("certificate", encoding="utf-8")
    private_key.write_text("private key", encoding="utf-8")
    private_key.chmod(0o600)
    result = cli.main(
        [
            "web",
            "--authenticated-lan",
            "--lan-listen-address",
            "192.168.0.25",
            "--lan-origin",
            "https://scanner.example:8443",
            "--lan-password-env",
            "SDSCTL_WEB_PASSWORD",
            "--lan-tls-certfile",
            str(certificate),
            "--lan-tls-keyfile",
            str(private_key),
            "--listen-port",
            "8443",
            "--no-access-log",
        ],
        environ={"SDSCTL_WEB_PASSWORD": "correct horse battery staple"},
    )

    assert result == 0
    assert len(authentications) == 1
    authentication = authentications[0]
    assert authentication.origin == "https://scanner.example:8443"
    assert authentication.password_matches("correct horse battery staple")
    assert "correct horse battery staple" not in repr(authentication)
    assert server_calls == [
        (
            app,
            "192.168.0.25",
            8443,
            True,
            False,
            certificate,
            private_key,
        )
    ]


def test_web_cli_authenticated_lan_requires_secret_and_matching_origin_port(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "web",
        "--authenticated-lan",
        "--lan-listen-address",
        "192.168.0.25",
        "--lan-origin",
        "https://scanner.example:8443",
        "--lan-password-env",
        "SDSCTL_WEB_PASSWORD",
        "--lan-tls-certfile",
        str(tmp_path / "dashboard.crt"),
        "--lan-tls-keyfile",
        str(tmp_path / "dashboard.key"),
    ]

    assert cli.main([*arguments, "--listen-port", "8443"], environ={}) == 2
    missing_error = capsys.readouterr().err
    assert "SDSCTL_WEB_PASSWORD" in missing_error
    assert "correct horse" not in missing_error

    assert (
        cli.main(
            [*arguments, "--listen-port", "8443"],
            environ={"SDSCTL_WEB_PASSWORD": "short"},
        )
        == 2
    )
    short_error = capsys.readouterr().err
    assert "at least 16 characters" in short_error
    assert "short" not in short_error

    assert (
        cli.main(
            [*arguments, "--listen-port", "9443"],
            environ={"SDSCTL_WEB_PASSWORD": "correct horse battery staple"},
        )
        == 2
    )
    assert "origin port must match --listen-port" in capsys.readouterr().err


def test_web_cli_reports_unavailable_password_derivation_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable_scrypt(*args: object, **kwargs: object) -> bytes:
        del args, kwargs
        raise ValueError("unsupported")

    monkeypatch.setattr(web_auth.hashlib, "scrypt", unavailable_scrypt)
    result = cli.main(
        [
            "web",
            "--authenticated-lan",
            "--lan-listen-address",
            "192.168.0.25",
            "--lan-origin",
            "https://scanner.example:8443",
            "--lan-password-env",
            "SDSCTL_WEB_PASSWORD",
            "--lan-tls-certfile",
            str(tmp_path / "dashboard.crt"),
            "--lan-tls-keyfile",
            str(tmp_path / "dashboard.key"),
            "--listen-port",
            "8443",
        ],
        environ={"SDSCTL_WEB_PASSWORD": "correct horse battery staple"},
    )

    error = capsys.readouterr().err
    assert result == 2
    assert "password derivation is unavailable" in error
    assert "correct horse battery staple" not in error
    assert "Traceback" not in error


def test_web_cli_validates_tls_before_constructing_app_or_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_key = tmp_path / "dashboard.key"
    private_key.write_text("private key", encoding="utf-8")
    private_key.chmod(0o600)

    def forbidden_create_app(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("app must not be constructed")

    def forbidden_run_server(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise AssertionError("server must not be constructed")

    monkeypatch.setattr(
        web_dashboard,
        "create_web_dashboard_app",
        forbidden_create_app,
    )
    monkeypatch.setattr(cli, "run_web_dashboard_server", forbidden_run_server)

    result = cli.main(
        [
            "web",
            "--authenticated-lan",
            "--lan-listen-address",
            "192.168.0.25",
            "--lan-origin",
            "https://scanner.example:8443",
            "--lan-password-env",
            "SDSCTL_WEB_PASSWORD",
            "--lan-tls-certfile",
            str(tmp_path / "missing.crt"),
            "--lan-tls-keyfile",
            str(private_key),
            "--listen-port",
            "8443",
        ],
        environ={"SDSCTL_WEB_PASSWORD": "correct horse battery staple"},
    )

    assert result == 2
    assert "TLS certificate file is unavailable" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["--lan-listen-address", "192.168.0.25"],
            "--lan-* options require --authenticated-lan",
        ),
        (
            ["--authenticated-lan"],
            "--authenticated-lan requires:",
        ),
        (
            ["--authenticated-lan", "--container-exposure"],
            "--authenticated-lan cannot be used",
        ),
        (
            [
                "--authenticated-lan",
                "--listen-address",
                "127.0.0.1",
            ],
            "--listen-address cannot be used with --authenticated-lan",
        ),
    ],
)
def test_web_cli_rejects_incomplete_or_conflicting_authenticated_lan_options(
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["web", *arguments], environ={}) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments,message",
    [
        (
            ["--container-exposure", "--listen-address", "127.0.0.1"],
            "--listen-address cannot be used with --container-exposure",
        ),
        (
            ["--container-exposure", "--home-assistant-ingress"],
            "--container-exposure cannot be used with --home-assistant-ingress",
        ),
    ],
)
def test_web_cli_rejects_conflicting_container_exposure_options(
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["web", *arguments], environ={}) == 2
    assert message in capsys.readouterr().err

def test_web_cli_rejects_scanner_connection_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        ["--host", "192.168.0.251", "web"],
        environ={},
    )

    assert result == 2
    assert (
        "Scanner connection selectors are not used with daemon-client."
        in capsys.readouterr().err
    )
