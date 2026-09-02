from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import sds200.cli as cli
from sds200.daemon_remote import (
    DAEMON_REMOTE_DEFAULT_PORT,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteClientIdentity,
    DaemonRemoteListenerConfiguration,
)
from sds200.daemon_remote_deployment import (
    DAEMON_REMOTE_COMPOSE_CONFIG_PATH,
    DAEMON_REMOTE_COMPOSE_CONTAINER_ADDRESS,
    DAEMON_REMOTE_DEPLOYMENT_ERROR,
    DaemonRemoteDeploymentPreflight,
    DaemonRemoteDeploymentPreflightError,
    preflight_daemon_remote_container_configuration,
    preflight_daemon_remote_container_deployment,
)


def _listener_configuration(tmp_path: Path) -> DaemonRemoteListenerConfiguration:
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.key"
    credential = tmp_path / "display.secret"
    certificate.write_bytes(b"certificate")
    private_key.write_bytes(b"private-key")
    credential.write_bytes(b"credential")
    private_key.chmod(0o600)
    credential.chmod(0o600)
    return DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address=DAEMON_REMOTE_COMPOSE_CONTAINER_ADDRESS,
        port=DAEMON_REMOTE_DEFAULT_PORT,
        certificate_file=certificate,
        private_key_file=private_key,
        clients=(
            DaemonRemoteClientIdentity(
                "display",
                credential,
                scopes=(DaemonRemoteAuthorizationScope.OBSERVE,),
            ),
        ),
    )


def _write_listener_document(
    path: Path,
    configuration: DaemonRemoteListenerConfiguration,
) -> None:
    certificate = configuration.certificate_file
    private_key = configuration.private_key_file
    credential = configuration.clients[0].credential_file
    assert certificate is not None
    assert private_key is not None
    path.write_text(
        "\n".join(
            (
                "version = 1",
                "",
                "[listener]",
                "enabled = true",
                f'bind_address = "{DAEMON_REMOTE_COMPOSE_CONTAINER_ADDRESS}"',
                f"port = {DAEMON_REMOTE_DEFAULT_PORT}",
                f'certificate_file = "{certificate}"',
                f'private_key_file = "{private_key}"',
                "",
                "[[clients]]",
                'client_id = "display"',
                f'credential_file = "{credential}"',
                'scopes = ["observe"]',
                "",
            )
        ),
        encoding="utf-8",
    )


def test_container_deployment_preflight_returns_only_redacted_evidence(
    tmp_path: Path,
) -> None:
    configuration = _listener_configuration(tmp_path)

    evidence = preflight_daemon_remote_container_configuration(
        configuration,
        published_address="192.168.20.10",
    )

    assert evidence == DaemonRemoteDeploymentPreflight(
        published_address_family="ipv4",
        listener_address_family="ipv4",
        port=DAEMON_REMOTE_DEFAULT_PORT,
        configuration=evidence.configuration,
    )
    assert evidence.configuration.enabled is True
    assert evidence.configuration.active_credentials == 1
    rendered = repr(evidence)
    assert "192.168.20.10" not in rendered
    assert DAEMON_REMOTE_COMPOSE_CONTAINER_ADDRESS not in rendered
    assert str(tmp_path) not in rendered
    assert "display" not in rendered


def test_container_deployment_preflight_loads_the_exact_document(
    tmp_path: Path,
) -> None:
    configuration = _listener_configuration(tmp_path)
    document = tmp_path / "daemon-remote.toml"
    _write_listener_document(document, configuration)

    evidence = preflight_daemon_remote_container_deployment(
        document,
        published_address="fd00::20",
    )

    assert evidence.published_address_family == "ipv6"
    assert evidence.listener_address_family == "ipv4"
    assert evidence.port == DAEMON_REMOTE_DEFAULT_PORT


@pytest.mark.parametrize(
    "published_address",
    (
        "0.0.0.0",
        "127.0.0.1",
        "192.0.2.10",
        "198.51.100.10",
        "203.0.113.10",
        "8.8.8.8",
        "ff02::1",
        "localhost",
        "https://192.168.20.10",
    ),
)
def test_container_deployment_preflight_rejects_unsafe_published_addresses(
    tmp_path: Path,
    published_address: str,
) -> None:
    configuration = _listener_configuration(tmp_path)

    with pytest.raises(
        DaemonRemoteDeploymentPreflightError,
        match="^Remote daemon container deployment preflight failed\\.$",
    ) as caught:
        preflight_daemon_remote_container_configuration(
            configuration,
            published_address=published_address,
        )

    assert str(caught.value) == DAEMON_REMOTE_DEPLOYMENT_ERROR
    assert published_address not in repr(caught.value)


def test_container_deployment_preflight_rejects_boundary_mismatches(
    tmp_path: Path,
) -> None:
    configuration = _listener_configuration(tmp_path)
    invalid_configurations = (
        DaemonRemoteListenerConfiguration(),
        replace(configuration, bind_address="172.30.32.3"),
        replace(configuration, port=50444),
    )

    for invalid in invalid_configurations:
        with pytest.raises(DaemonRemoteDeploymentPreflightError) as caught:
            preflight_daemon_remote_container_configuration(
                invalid,
                published_address="192.168.20.10",
            )

        rendered = repr(caught.value)
        for private in (
            "192.168.20.10",
            DAEMON_REMOTE_COMPOSE_CONTAINER_ADDRESS,
            "172.30.32.3",
            str(tmp_path),
            "display",
        ):
            assert private not in rendered


@pytest.mark.parametrize("expected_port", (True, 0, 65536, "50443"))
def test_container_deployment_preflight_rejects_invalid_expected_ports(
    tmp_path: Path,
    expected_port: object,
) -> None:
    with pytest.raises(DaemonRemoteDeploymentPreflightError):
        preflight_daemon_remote_container_configuration(
            _listener_configuration(tmp_path),
            published_address="192.168.20.10",
            expected_port=expected_port,
        )


def test_container_deployment_preflight_redacts_absent_and_malformed_documents(
    tmp_path: Path,
) -> None:
    absent = tmp_path / "private-name.toml"
    for path in (absent, tmp_path / "malformed.toml"):
        if path.name == "malformed.toml":
            path.write_text("not = [valid", encoding="utf-8")
        with pytest.raises(DaemonRemoteDeploymentPreflightError) as caught:
            preflight_daemon_remote_container_deployment(
                path,
                published_address="192.168.20.10",
            )
        rendered = repr(caught.value)
        assert str(path) not in rendered
        assert "192.168.20.10" not in rendered


def test_cli_container_deployment_preflight_uses_fixed_defaults(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}

    def preflight(
        path: Path,
        *,
        published_address: object,
        expected_port: object,
    ) -> object:
        observed.update(
            path=path,
            published_address=published_address,
            expected_port=expected_port,
        )
        return object()

    monkeypatch.setattr(
        cli,
        "preflight_daemon_remote_container_deployment",
        preflight,
    )

    assert cli.main(
        ["daemon-remote-preflight", "--published-address", "192.168.20.10"],
        environ={},
    ) == 0

    assert observed == {
        "path": DAEMON_REMOTE_COMPOSE_CONFIG_PATH,
        "published_address": "192.168.20.10",
        "expected_port": DAEMON_REMOTE_DEFAULT_PORT,
    }
    captured = capsys.readouterr()
    assert captured.out == "Remote daemon container deployment preflight passed.\n"
    assert captured.err == ""
    assert "192.168.20.10" not in captured.out


def test_cli_container_deployment_preflight_has_one_fixed_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "private.toml"

    def fail(*_args: object, **_kwargs: object) -> object:
        raise DaemonRemoteDeploymentPreflightError()

    monkeypatch.setattr(
        cli,
        "preflight_daemon_remote_container_deployment",
        fail,
    )

    assert cli.main(
        [
            "daemon-remote-preflight",
            "--published-address",
            "192.168.20.10",
            "--remote-config",
            str(private_path),
        ],
        environ={},
    ) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {DAEMON_REMOTE_DEPLOYMENT_ERROR}\n"
    assert "192.168.20.10" not in captured.err
    assert str(private_path) not in captured.err
