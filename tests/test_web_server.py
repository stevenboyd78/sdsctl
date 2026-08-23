from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200.web_server import (
    WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
    WEB_DASHBOARD_DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT,
    WEB_DASHBOARD_DEFAULT_HOST,
    WEB_DASHBOARD_DEFAULT_PORT,
    WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST,
    WEB_DASHBOARD_MAX_TLS_FILE_BYTES,
    _default_server_factory,
    normalize_authenticated_lan_host,
    normalize_web_dashboard_host,
    normalize_web_dashboard_port,
    run_web_dashboard_server,
)


class FakeServer:
    def __init__(self) -> None:
        self.run_calls = 0

    def run(self) -> None:
        self.run_calls += 1


class FakeServerFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, int, bool, str | None, str | None]] = []
        self.server = FakeServer()

    def __call__(
        self,
        app: object,
        *,
        host: str,
        port: int,
        access_log: bool,
        ssl_certfile: str | None = None,
        ssl_keyfile: str | None = None,
    ) -> FakeServer:
        self.calls.append((app, host, port, access_log, ssl_certfile, ssl_keyfile))
        return self.server


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("localhost", "127.0.0.1"),
        ("LOCALHOST", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.25", "127.0.0.25"),
        ("::1", "::1"),
    ],
)
def test_normalize_web_dashboard_host_accepts_loopback(
    value: str,
    expected: str,
) -> None:
    assert normalize_web_dashboard_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0.0.0.0",
        "::",
        "192.168.0.25",
        "scanner.local",
    ],
)
def test_normalize_web_dashboard_host_rejects_remote_exposure(
    value: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="localhost or a loopback IP address|must not be empty",
    ):
        normalize_web_dashboard_host(value)


def test_normalize_web_dashboard_port_validates_range() -> None:
    assert normalize_web_dashboard_port(1) == 1
    assert normalize_web_dashboard_port(65535) == 65535

    with pytest.raises(ValueError, match="between 1 and 65535"):
        normalize_web_dashboard_port(0)

    with pytest.raises(ValueError, match="between 1 and 65535"):
        normalize_web_dashboard_port(65536)

    with pytest.raises(TypeError, match="must be an integer"):
        normalize_web_dashboard_port(True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.0.0.25", "10.0.0.25"),
        ("172.16.5.10", "172.16.5.10"),
        ("192.168.0.25", "192.168.0.25"),
        ("169.254.10.2", "169.254.10.2"),
        ("fd12:3456::25", "fd12:3456::25"),
        ("fe80::25", "fe80::25"),
    ],
)
def test_normalize_authenticated_lan_host_accepts_explicit_lan_addresses(
    value: str,
    expected: str,
) -> None:
    assert normalize_authenticated_lan_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " 192.168.0.25",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::",
        "8.8.8.8",
        "2001:4860:4860::8888",
    ],
)
def test_normalize_authenticated_lan_host_rejects_non_lan_addresses(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="Authenticated LAN listen address"):
        normalize_authenticated_lan_host(value)


def test_run_web_dashboard_server_uses_normalized_configuration() -> None:
    app = object()
    factory = FakeServerFactory()

    result = run_web_dashboard_server(
        app,
        host="localhost",
        port=8123,
        access_log=False,
        server_factory=factory,
    )

    assert result == 0
    assert factory.calls == [
        (app, "127.0.0.1", 8123, False, None, None),
    ]
    assert factory.server.run_calls == 1


def test_run_web_dashboard_server_defaults_are_loopback_only() -> None:
    app = object()
    factory = FakeServerFactory()

    assert run_web_dashboard_server(
        app,
        server_factory=factory,
    ) == 0

    assert factory.calls == [
        (
            app,
            WEB_DASHBOARD_DEFAULT_HOST,
            WEB_DASHBOARD_DEFAULT_PORT,
            True,
            None,
            None,
        )
    ]


def test_run_web_dashboard_server_allows_home_assistant_ingress() -> None:
    app = object()
    factory = FakeServerFactory()

    assert run_web_dashboard_server(
        app,
        host=WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST,
        port=8099,
        home_assistant_ingress=True,
        server_factory=factory,
    ) == 0

    assert factory.calls == [
        (
            app,
            WEB_DASHBOARD_HOME_ASSISTANT_INGRESS_HOST,
            8099,
            True,
            None,
            None,
        )
    ]
    assert factory.server.run_calls == 1


def test_run_web_dashboard_server_allows_generic_container_exposure() -> None:
    app = object()
    factory = FakeServerFactory()

    assert run_web_dashboard_server(
        app,
        host=WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
        container_exposure=True,
        server_factory=factory,
    ) == 0

    assert factory.calls == [
        (
            app,
            WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
            WEB_DASHBOARD_DEFAULT_PORT,
            True,
            None,
            None,
        )
    ]


def test_run_web_dashboard_server_allows_authenticated_lan_tls(
    tmp_path: Path,
) -> None:
    app = object()
    factory = FakeServerFactory()
    certificate = tmp_path / "dashboard.crt"
    private_key = tmp_path / "dashboard.key"
    certificate.write_text("certificate", encoding="utf-8")
    private_key.write_text("private key", encoding="utf-8")
    private_key.chmod(0o600)

    assert (
        run_web_dashboard_server(
            app,
            host="192.168.0.25",
            port=8443,
            authenticated_lan=True,
            ssl_certfile=certificate,
            ssl_keyfile=private_key,
            server_factory=factory,
        )
        == 0
    )

    assert factory.calls == [
        (
            app,
            "192.168.0.25",
            8443,
            True,
            str(certificate),
            str(private_key),
        )
    ]


def test_run_web_dashboard_server_allows_authenticated_ipv6_lan_tls(
    tmp_path: Path,
) -> None:
    factory = FakeServerFactory()
    certificate = tmp_path / "dashboard.crt"
    private_key = tmp_path / "dashboard.key"
    certificate.write_text("certificate", encoding="utf-8")
    private_key.write_text("private key", encoding="utf-8")
    private_key.chmod(0o640)

    assert (
        run_web_dashboard_server(
            object(),
            host="fd12:3456::25",
            port=8443,
            authenticated_lan=True,
            ssl_certfile=certificate,
            ssl_keyfile=private_key,
            server_factory=factory,
        )
        == 0
    )

    assert factory.calls[0][1:] == (
        "fd12:3456::25",
        8443,
        True,
        str(certificate),
        str(private_key),
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode-bit contract")
def test_authenticated_lan_tls_requires_private_key_permissions(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "dashboard.crt"
    private_key = tmp_path / "dashboard.key"
    certificate.write_text("certificate", encoding="utf-8")
    private_key.write_text("private key", encoding="utf-8")
    private_key.chmod(0o644)

    with pytest.raises(ValueError, match="POSIX other class"):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            authenticated_lan=True,
            ssl_certfile=certificate,
            ssl_keyfile=private_key,
        )


def test_authenticated_lan_tls_rejects_encrypted_private_key(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "dashboard.crt"
    private_key = tmp_path / "dashboard.key"
    certificate.write_text("certificate", encoding="utf-8")
    private_key.write_text(
        "certificate-prefix" * 300 + "\n-----BEGIN ENCRYPTED PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    private_key.chmod(0o600)
    factory = FakeServerFactory()

    with pytest.raises(ValueError, match="must be an unencrypted PEM key"):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            authenticated_lan=True,
            ssl_certfile=certificate,
            ssl_keyfile=private_key,
            server_factory=factory,
        )

    assert factory.calls == []


@pytest.mark.parametrize(
    ("certificate_value", "key_value", "message"),
    [
        (None, Path("/unused/key.pem"), "certificate path must be a pathlib.Path"),
        (Path("/unused/cert.pem"), None, "private key path must be a pathlib.Path"),
        (
            Path("relative.crt"),
            Path("/unused/key.pem"),
            "certificate path must be absolute",
        ),
    ],
)
def test_authenticated_lan_tls_requires_a_complete_absolute_pair(
    certificate_value: Path | None,
    key_value: Path | None,
    message: str,
) -> None:
    factory = FakeServerFactory()

    with pytest.raises((TypeError, ValueError), match=message):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            authenticated_lan=True,
            ssl_certfile=certificate_value,
            ssl_keyfile=key_value,
            server_factory=factory,
        )

    assert factory.calls == []


def test_authenticated_lan_tls_rejects_missing_and_nonregular_files(
    tmp_path: Path,
) -> None:
    missing_certificate = tmp_path / "missing.crt"
    private_key = tmp_path / "dashboard.key"
    private_key.write_text("private key", encoding="utf-8")
    private_key.chmod(0o600)
    factory = FakeServerFactory()

    with pytest.raises(ValueError, match="certificate file is unavailable"):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            authenticated_lan=True,
            ssl_certfile=missing_certificate,
            ssl_keyfile=private_key,
            server_factory=factory,
        )

    certificate_directory = tmp_path / "certificate-directory"
    certificate_directory.mkdir()
    with pytest.raises(ValueError, match="certificate path must name a regular file"):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            authenticated_lan=True,
            ssl_certfile=certificate_directory,
            ssl_keyfile=private_key,
            server_factory=factory,
        )

    assert factory.calls == []


def test_authenticated_lan_tls_rejects_oversized_files(tmp_path: Path) -> None:
    certificate = tmp_path / "dashboard.crt"
    private_key = tmp_path / "dashboard.key"
    certificate.write_bytes(b"x" * (WEB_DASHBOARD_MAX_TLS_FILE_BYTES + 1))
    private_key.write_text("private key", encoding="utf-8")
    private_key.chmod(0o600)
    factory = FakeServerFactory()

    with pytest.raises(ValueError, match="certificate file must not exceed"):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            authenticated_lan=True,
            ssl_certfile=certificate,
            ssl_keyfile=private_key,
            server_factory=factory,
        )

    assert factory.calls == []


def test_tls_files_require_authenticated_lan_mode(tmp_path: Path) -> None:
    certificate = tmp_path / "dashboard.crt"
    certificate.write_text("certificate", encoding="utf-8")

    with pytest.raises(ValueError, match="require authenticated LAN access"):
        run_web_dashboard_server(
            object(),
            ssl_certfile=certificate,
        )


def test_run_web_dashboard_server_container_exposure_requires_wildcard() -> None:
    with pytest.raises(
        ValueError,
        match="container-exposure web server must listen on 0.0.0.0",
    ):
        run_web_dashboard_server(
            object(),
            host=WEB_DASHBOARD_DEFAULT_HOST,
            container_exposure=True,
        )


def test_run_web_dashboard_server_requires_boolean_container_setting() -> None:
    with pytest.raises(TypeError, match="container-exposure.*boolean"):
        run_web_dashboard_server(
            object(),
            container_exposure=1,  # type: ignore[arg-type]
        )


def test_run_web_dashboard_server_requires_boolean_authenticated_lan_setting() -> None:
    with pytest.raises(TypeError, match="Authenticated LAN.*boolean"):
        run_web_dashboard_server(
            object(),
            authenticated_lan=1,  # type: ignore[arg-type]
        )


def test_run_web_dashboard_server_rejects_both_wildcard_modes() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_web_dashboard_server(
            object(),
            host=WEB_DASHBOARD_CONTAINER_EXPOSURE_HOST,
            home_assistant_ingress=True,
            container_exposure=True,
        )

    with pytest.raises(ValueError, match="mutually exclusive"):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            container_exposure=True,
            authenticated_lan=True,
        )


def test_run_web_dashboard_server_ingress_requires_wildcard_host() -> None:
    with pytest.raises(
        ValueError,
        match="must listen on 0.0.0.0",
    ):
        run_web_dashboard_server(
            object(),
            host="192.168.0.25",
            home_assistant_ingress=True,
        )


def test_run_web_dashboard_server_requires_boolean_ingress_setting() -> None:
    with pytest.raises(
        TypeError,
        match="Ingress server setting must be boolean",
    ):
        run_web_dashboard_server(
            object(),
            home_assistant_ingress=1,  # type: ignore[arg-type]
        )


def test_default_server_factory_bounds_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeUvicornConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            captured["app"] = app
            captured.update(kwargs)

    class FakeUvicornServer:
        def __init__(self, config: object) -> None:
            self.config = config

        def run(self) -> None:
            raise AssertionError("server should not run during construction")

    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(
            Config=FakeUvicornConfig,
            Server=FakeUvicornServer,
        ),
    )

    app = object()
    server = _default_server_factory(
        app,
        host="127.0.0.1",
        port=8123,
        access_log=False,
        ssl_certfile="/run/secrets/dashboard.crt",
        ssl_keyfile="/run/secrets/dashboard.key",
    )

    assert isinstance(server, FakeUvicornServer)
    assert captured["app"] is app
    assert captured["timeout_graceful_shutdown"] == (
        WEB_DASHBOARD_DEFAULT_GRACEFUL_SHUTDOWN_TIMEOUT
    )
    assert captured["ssl_certfile"] == "/run/secrets/dashboard.crt"
    assert captured["ssl_keyfile"] == "/run/secrets/dashboard.key"
    assert captured["proxy_headers"] is False
    assert captured["server_header"] is False


def test_run_web_dashboard_server_requires_callable_factory() -> None:
    with pytest.raises(
        TypeError,
        match="server factory must be callable",
    ):
        run_web_dashboard_server(
            object(),
            server_factory=object(),  # type: ignore[arg-type]
        )
