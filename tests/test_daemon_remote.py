from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_REMOTE_CONFIG_FILENAME,
    DAEMON_REMOTE_CONFIG_VERSION,
    DAEMON_REMOTE_DEFAULT_PORT,
    DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES,
    DAEMON_REMOTE_MAX_TLS_FILE_BYTES,
    DAEMON_REMOTE_PRIVATE_FILE_MODE,
    ConfigurationError,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteClientIdentity,
    DaemonRemoteConfigurationPreflight,
    DaemonRemoteListenerConfiguration,
    default_daemon_remote_config_path,
    load_daemon_remote_configuration,
    preflight_daemon_remote_configuration,
    resolve_configuration_paths,
)


def _identity(
    credential_file: Path,
    *,
    client_id: str = "pi-display",
    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = (DaemonRemoteAuthorizationScope.OBSERVE,),
    revoked: bool = False,
) -> DaemonRemoteClientIdentity:
    return DaemonRemoteClientIdentity(
        client_id=client_id,
        credential_file=credential_file,
        scopes=scopes,
        revoked=revoked,
    )


def _configuration(
    tmp_path: Path,
    *,
    address: str = "192.168.20.10",
    clients: tuple[DaemonRemoteClientIdentity, ...] | None = None,
) -> DaemonRemoteListenerConfiguration:
    return DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address=address,
        port=DAEMON_REMOTE_DEFAULT_PORT,
        certificate_file=tmp_path / "server.crt",
        private_key_file=tmp_path / "server.key",
        clients=(clients if clients is not None else (_identity(tmp_path / "pi-display.secret"),)),
    )


def _write_private(path: Path, contents: bytes = b"private material") -> None:
    path.write_bytes(contents)
    path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)


def _complete_document(tmp_path: Path, *, address: str = "192.168.20.10") -> str:
    return (
        f"version = {DAEMON_REMOTE_CONFIG_VERSION}\n"
        "\n"
        "[listener]\n"
        "enabled = true\n"
        f'bind_address = "{address}"\n'
        f'certificate_file = "{tmp_path / "server.crt"}"\n'
        f'private_key_file = "{tmp_path / "server.key"}"\n'
        "\n"
        "[[clients]]\n"
        'client_id = "wall-display"\n'
        f'credential_file = "{tmp_path / "wall-display.secret"}"\n'
        'scopes = ["observe"]\n'
        "\n"
        "[[clients]]\n"
        'client_id = "administrator"\n'
        f'credential_file = "{tmp_path / "administrator.secret"}"\n'
        'scopes = ["observe", "control"]\n'
        "revoked = true\n"
    )


def test_default_remote_path_and_missing_configuration_are_read_only(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    expected = paths.user_config_dir / DAEMON_REMOTE_CONFIG_FILENAME

    assert paths.daemon_remote_config_file == expected
    assert default_daemon_remote_config_path(paths) == expected
    assert load_daemon_remote_configuration(paths=paths) is None
    assert expected.exists() is False


def test_disabled_configuration_is_the_only_minimal_document(tmp_path: Path) -> None:
    path = tmp_path / DAEMON_REMOTE_CONFIG_FILENAME
    path.write_text(
        f"version = {DAEMON_REMOTE_CONFIG_VERSION}\n\n[listener]\nenabled = false\n",
        encoding="utf-8",
    )

    configuration = load_daemon_remote_configuration(path)

    assert configuration == DaemonRemoteListenerConfiguration()
    assert configuration is not None
    assert configuration.as_redacted_dict() == {
        "version": DAEMON_REMOTE_CONFIG_VERSION,
        "enabled": False,
        "address_family": None,
        "port": None,
        "configured_clients": 0,
        "active_clients": 0,
        "revoked_clients": 0,
        "control_clients": 0,
    }


def test_complete_configuration_loads_secret_free_metadata(tmp_path: Path) -> None:
    path = tmp_path / DAEMON_REMOTE_CONFIG_FILENAME
    path.write_text(_complete_document(tmp_path), encoding="utf-8")

    configuration = load_daemon_remote_configuration(path)

    assert configuration is not None
    assert configuration.enabled is True
    assert configuration.bind_address == "192.168.20.10"
    assert configuration.port == DAEMON_REMOTE_DEFAULT_PORT
    assert configuration.certificate_file == tmp_path / "server.crt"
    assert configuration.private_key_file == tmp_path / "server.key"
    assert tuple(client.client_id for client in configuration.clients) == (
        "administrator",
        "wall-display",
    )
    assert configuration.clients[0].revoked is True
    assert configuration.clients[0].scopes == (
        DaemonRemoteAuthorizationScope.CONTROL,
        DaemonRemoteAuthorizationScope.OBSERVE,
    )
    assert configuration.as_redacted_dict() == {
        "version": DAEMON_REMOTE_CONFIG_VERSION,
        "enabled": True,
        "address_family": "ipv4",
        "port": DAEMON_REMOTE_DEFAULT_PORT,
        "configured_clients": 2,
        "active_clients": 1,
        "revoked_clients": 1,
        "control_clients": 0,
    }
    json.dumps(configuration.as_redacted_dict())


@pytest.mark.parametrize(
    ("address", "family"),
    [
        ("10.1.2.3", "ipv4"),
        ("172.31.255.254", "ipv4"),
        ("192.168.1.20", "ipv4"),
        ("169.254.8.9", "ipv4"),
        ("fd42::20", "ipv6"),
        ("fe80::20%eth0", "ipv6"),
    ],
)
def test_exact_nonpublic_bind_addresses_are_accepted(
    tmp_path: Path,
    address: str,
    family: str,
) -> None:
    configuration = _configuration(tmp_path, address=address)

    assert configuration.bind_address == address
    assert configuration.as_redacted_dict()["address_family"] == family


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "::",
        "127.0.0.1",
        "::1",
        "224.0.0.1",
        "ff02::1",
        "192.0.2.10",
        "198.51.100.10",
        "203.0.113.10",
        "8.8.8.8",
        "2001:4860:4860::8888",
        "scanner.example.test",
        "https://192.168.1.10",
        "192.168.1.0/24",
    ],
)
def test_wildcard_loopback_multicast_documentation_and_public_binds_fail_closed(
    tmp_path: Path,
    address: str,
) -> None:
    with pytest.raises(ValueError, match="bind address"):
        _configuration(tmp_path, address=address)


def test_client_identity_is_immutable_normalized_and_revocable(tmp_path: Path) -> None:
    identity = _identity(
        tmp_path / "client.secret",
        scopes=(
            DaemonRemoteAuthorizationScope.OBSERVE,
            DaemonRemoteAuthorizationScope.CONTROL,
        ),
        revoked=True,
    )

    assert identity.revoked is True
    assert identity.scopes == (
        DaemonRemoteAuthorizationScope.CONTROL,
        DaemonRemoteAuthorizationScope.OBSERVE,
    )
    with pytest.raises(FrozenInstanceError):
        identity.revoked = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda root: _identity(root / "secret", client_id=" bad "),
            "client ID",
        ),
        (
            lambda root: _identity(root / "secret", client_id="bad/id"),
            "client ID",
        ),
        (
            lambda root: DaemonRemoteClientIdentity(
                "client",
                Path("relative.secret"),
            ),
            "must be absolute",
        ),
        (
            lambda root: _identity(root / "secret", scopes=()),
            "must not be empty",
        ),
        (
            lambda root: _identity(
                root / "secret",
                scopes=(DaemonRemoteAuthorizationScope.CONTROL,),
            ),
            "include the observe scope",
        ),
        (
            lambda root: _identity(
                root / "secret",
                scopes=(
                    DaemonRemoteAuthorizationScope.OBSERVE,
                    DaemonRemoteAuthorizationScope.OBSERVE,
                ),
            ),
            "duplicates",
        ),
    ],
)
def test_client_identity_rejects_unsafe_metadata(
    tmp_path: Path,
    factory: object,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory(tmp_path)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda root: DaemonRemoteClientIdentity(1, root / "secret"),  # type: ignore[arg-type]
            "client ID must be a string",
        ),
        (
            lambda root: _identity(root / "secret", client_id="bad\nforged"),
            "printable characters",
        ),
        (
            lambda root: _identity(root / "secret", client_id="x" * 65),
            "at most 64",
        ),
        (
            lambda root: DaemonRemoteClientIdentity("client", object()),  # type: ignore[arg-type]
            "pathlib.Path",
        ),
        (
            lambda root: DaemonRemoteClientIdentity(
                "client",
                root / "secret",
                scopes=[DaemonRemoteAuthorizationScope.OBSERVE],  # type: ignore[arg-type]
            ),
            "scopes must be a tuple",
        ),
        (
            lambda root: DaemonRemoteClientIdentity(
                "client",
                root / "secret",
                scopes=("observe",),  # type: ignore[arg-type]
            ),
            "only DaemonRemoteAuthorizationScope",
        ),
        (
            lambda root: DaemonRemoteClientIdentity(
                "client",
                root / "secret",
                revoked=1,  # type: ignore[arg-type]
            ),
            "revoked setting must be a boolean",
        ),
    ],
)
def test_client_identity_rejects_invalid_runtime_types(
    tmp_path: Path,
    factory: object,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory(tmp_path)  # type: ignore[operator]


def test_listener_rejects_duplicate_and_fully_revoked_identities(
    tmp_path: Path,
) -> None:
    first = _identity(tmp_path / "one.secret", client_id="duplicate")
    second = _identity(tmp_path / "two.secret", client_id="duplicate")
    with pytest.raises(ValueError, match="client IDs must be unique"):
        _configuration(tmp_path, clients=(first, second))

    revoked = _identity(
        tmp_path / "revoked.secret",
        client_id="revoked",
        revoked=True,
    )
    with pytest.raises(ValueError, match="non-revoked"):
        _configuration(tmp_path, clients=(revoked,))


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda root: DaemonRemoteListenerConfiguration(enabled=1),  # type: ignore[arg-type]
            "enabled setting must be a boolean",
        ),
        (
            lambda root: DaemonRemoteListenerConfiguration(
                enabled=True,
                bind_address="192.168.1.10",
                port=True,
                certificate_file=root / "server.crt",
                private_key_file=root / "server.key",
                clients=(_identity(root / "client.secret"),),
            ),
            "port must be an integer",
        ),
        (
            lambda root: DaemonRemoteListenerConfiguration(
                enabled=True,
                bind_address="192.168.1.10",
                port=65536,
                certificate_file=root / "server.crt",
                private_key_file=root / "server.key",
                clients=(_identity(root / "client.secret"),),
            ),
            "between 1 and 65535",
        ),
        (
            lambda root: DaemonRemoteListenerConfiguration(
                enabled=True,
                bind_address="192.168.1.10",
                port=50443,
                certificate_file=object(),  # type: ignore[arg-type]
                private_key_file=root / "server.key",
                clients=(_identity(root / "client.secret"),),
            ),
            "pathlib.Path",
        ),
        (
            lambda root: DaemonRemoteListenerConfiguration(
                enabled=True,
                bind_address="192.168.1.10",
                port=50443,
                certificate_file=root / "server.pem",
                private_key_file=root / "server.pem",
                clients=(_identity(root / "client.secret"),),
            ),
            "files must differ",
        ),
        (
            lambda root: DaemonRemoteListenerConfiguration(
                enabled=True,
                bind_address="192.168.1.10",
                port=50443,
                certificate_file=root / "server.crt",
                private_key_file=root / "server.key",
                clients=(),
            ),
            "at least one client identity",
        ),
        (
            lambda root: DaemonRemoteListenerConfiguration(
                enabled=True,
                bind_address="192.168.1.10",
                port=50443,
                certificate_file=root / "server.crt",
                private_key_file=root / "server.key",
                clients=[_identity(root / "client.secret")],  # type: ignore[arg-type]
            ),
            "clients must be a tuple",
        ),
        (
            lambda root: DaemonRemoteListenerConfiguration(
                enabled=True,
                bind_address="192.168.1.10",
                port=50443,
                certificate_file=root / "server.crt",
                private_key_file=root / "server.key",
                clients=(object(),),  # type: ignore[arg-type]
            ),
            "only DaemonRemoteClientIdentity",
        ),
        (
            lambda root: _configuration(
                root,
                clients=(
                    _identity(root / "same.secret", client_id="one"),
                    _identity(root / "same.secret", client_id="two"),
                ),
            ),
            "credential files must be unique",
        ),
        (
            lambda root: _configuration(
                root,
                clients=(_identity(root / "server.key"),),
            ),
            "must not also be a client credential",
        ),
    ],
)
def test_listener_rejects_incomplete_or_unsafe_runtime_configuration(
    tmp_path: Path,
    factory: object,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory(tmp_path)  # type: ignore[operator]


def test_disabled_configuration_rejects_dormant_endpoint_or_client_settings(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Disabled remote daemon"):
        DaemonRemoteListenerConfiguration(
            bind_address="192.168.1.10",
        )

    path = tmp_path / DAEMON_REMOTE_CONFIG_FILENAME
    path.write_text(
        f"version = {DAEMON_REMOTE_CONFIG_VERSION}\n\n"
        "[listener]\n"
        "enabled = false\n"
        'bind_address = "192.168.1.10"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Disabled remote daemon"):
        load_daemon_remote_configuration(path)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("", "version must be 1"),
        ("version = 2\n", "version must be 1"),
        ("version = true\n", "version must be 1"),
        ("version = [\n", "Could not read"),
        ("version = 1\nfuture = true\n", "unsupported field"),
        ("version = 1\nlistener = true\n", r"\[listener\] table"),
        (
            "version = 1\n[listener]\nenabled = false\nfuture = true\n",
            "unsupported field",
        ),
        (
            "version = 1\n[listener]\nenabled = false\n[[clients]]\nfuture = true\n",
            "unsupported field",
        ),
    ],
)
def test_loader_rejects_malformed_versions_and_unknown_fields(
    tmp_path: Path,
    document: str,
    message: str,
) -> None:
    path = tmp_path / DAEMON_REMOTE_CONFIG_FILENAME
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_daemon_remote_configuration(path)


def test_loader_rejects_unknown_scope_without_echoing_value(tmp_path: Path) -> None:
    private_value = "secret-scope-value-must-not-escape"
    document = _complete_document(tmp_path).replace(
        'scopes = ["observe"]',
        f'scopes = ["{private_value}"]',
        1,
    )
    path = tmp_path / DAEMON_REMOTE_CONFIG_FILENAME
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigurationError) as exc_info:
        load_daemon_remote_configuration(path)

    assert "scope is unsupported" in str(exc_info.value)
    assert private_value not in str(exc_info.value)


@pytest.mark.parametrize(
    ("transform", "message"),
    [
        (
            lambda document: document.replace('bind_address = "192.168.20.10"\n', ""),
            "bind address must be a string",
        ),
        (
            lambda document: document.replace('certificate_file = "', "certificate_file = 1 # ", 1),
            "certificate file must be a string",
        ),
        (
            lambda document: document.replace("enabled = true", "enabled = 1", 1),
            "enabled setting must be a boolean",
        ),
        (
            lambda document: document.replace(
                'bind_address = "192.168.20.10"',
                'bind_address = "192.168.20.10"\nport = "50443"',
                1,
            ),
            "port must be an integer",
        ),
        (
            lambda document: document.replace('client_id = "wall-display"', "client_id = 1", 1),
            "client id must be a string",
        ),
        (
            lambda document: document.replace('scopes = ["observe"]', "scopes = 1", 1),
            "scopes must be an array",
        ),
        (
            lambda document: document.replace('scopes = ["observe"]', "scopes = [1]", 1),
            "scope values must be strings",
        ),
        (
            lambda document: document.replace(
                'client_id = "wall-display"',
                'client_id = "wall-display"\nrevoked = 1',
                1,
            ),
            "revoked setting must be a boolean",
        ),
    ],
)
def test_loader_rejects_incomplete_and_wrongly_typed_enabled_documents(
    tmp_path: Path,
    transform: object,
    message: str,
) -> None:
    path = tmp_path / DAEMON_REMOTE_CONFIG_FILENAME
    path.write_text(transform(_complete_document(tmp_path)), encoding="utf-8")  # type: ignore[operator]

    with pytest.raises(ConfigurationError, match=message):
        load_daemon_remote_configuration(path)


def test_loader_rejects_non_array_and_non_table_clients(tmp_path: Path) -> None:
    path = tmp_path / DAEMON_REMOTE_CONFIG_FILENAME
    path.write_text(
        f"version = {DAEMON_REMOTE_CONFIG_VERSION}\nclients = true\n[listener]\nenabled = false\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="array of tables"):
        load_daemon_remote_configuration(path)

    path.write_text(
        f"version = {DAEMON_REMOTE_CONFIG_VERSION}\nclients = [1]\n[listener]\nenabled = false\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="must be a TOML table"):
        load_daemon_remote_configuration(path)


def test_repr_and_diagnostics_redact_private_endpoint_and_paths(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)

    rendered = repr(configuration)
    diagnostics = json.dumps(configuration.as_redacted_dict())

    assert "192.168.20.10" not in rendered
    assert "server.crt" not in rendered
    assert "server.key" not in rendered
    assert "pi-display.secret" not in rendered
    assert "192.168.20.10" not in diagnostics
    assert os.fspath(tmp_path) not in diagnostics


def test_preflight_checks_metadata_without_reading_secret_contents(
    tmp_path: Path,
) -> None:
    configuration = _configuration(
        tmp_path,
        clients=(
            _identity(tmp_path / "active.secret"),
            _identity(
                tmp_path / "removed.secret",
                client_id="removed-client",
                revoked=True,
            ),
        ),
    )
    (tmp_path / "server.crt").write_bytes(b"certificate")
    _write_private(tmp_path / "server.key", b"private key")
    _write_private(tmp_path / "active.secret", b"opaque credential")

    result = preflight_daemon_remote_configuration(configuration)

    assert result == DaemonRemoteConfigurationPreflight(
        enabled=True,
        certificate_bytes=len(b"certificate"),
        private_key_bytes=len(b"private key"),
        active_credentials=1,
        revoked_credentials=1,
    )
    assert (tmp_path / "removed.secret").exists() is False


def test_disabled_preflight_performs_no_filesystem_work() -> None:
    assert preflight_daemon_remote_configuration(
        DaemonRemoteListenerConfiguration()
    ) == DaemonRemoteConfigurationPreflight(enabled=False)


@pytest.mark.parametrize("target", ["server.key", "active.secret"])
def test_preflight_requires_exact_private_file_mode(
    tmp_path: Path,
    target: str,
) -> None:
    configuration = _configuration(
        tmp_path,
        clients=(_identity(tmp_path / "active.secret"),),
    )
    (tmp_path / "server.crt").write_bytes(b"certificate")
    _write_private(tmp_path / "server.key")
    _write_private(tmp_path / "active.secret")
    (tmp_path / target).chmod(0o640)

    with pytest.raises(ConfigurationError, match="POSIX mode 0600"):
        preflight_daemon_remote_configuration(configuration)


@pytest.mark.parametrize(
    ("target", "maximum"),
    [
        ("server.crt", DAEMON_REMOTE_MAX_TLS_FILE_BYTES),
        ("server.key", DAEMON_REMOTE_MAX_TLS_FILE_BYTES),
        ("active.secret", DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES),
    ],
)
def test_preflight_rejects_empty_and_oversized_files_without_path_disclosure(
    tmp_path: Path,
    target: str,
    maximum: int,
) -> None:
    configuration = _configuration(
        tmp_path,
        clients=(_identity(tmp_path / "active.secret"),),
    )
    (tmp_path / "server.crt").write_bytes(b"certificate")
    _write_private(tmp_path / "server.key")
    _write_private(tmp_path / "active.secret")
    path = tmp_path / target
    path.write_bytes(b"")
    if target != "server.crt":
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)

    with pytest.raises(ConfigurationError) as empty_error:
        preflight_daemon_remote_configuration(configuration)
    assert os.fspath(path) not in str(empty_error.value)

    path.write_bytes(b"x" * (maximum + 1))
    if target != "server.crt":
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)
    with pytest.raises(ConfigurationError) as oversized_error:
        preflight_daemon_remote_configuration(configuration)
    assert os.fspath(path) not in str(oversized_error.value)


def test_preflight_rejects_symlinked_secret(tmp_path: Path) -> None:
    configuration = _configuration(tmp_path)
    (tmp_path / "server.crt").write_bytes(b"certificate")
    _write_private(tmp_path / "server.key")
    real = tmp_path / "real.secret"
    _write_private(real)
    (tmp_path / "pi-display.secret").symlink_to(real)

    with pytest.raises(ConfigurationError, match="one regular file"):
        preflight_daemon_remote_configuration(configuration)


def test_preflight_rejects_unavailable_and_directory_files_without_path_disclosure(
    tmp_path: Path,
) -> None:
    configuration = _configuration(tmp_path)

    with pytest.raises(ConfigurationError) as unavailable:
        preflight_daemon_remote_configuration(configuration)
    assert "unavailable" in str(unavailable.value)
    assert os.fspath(tmp_path) not in str(unavailable.value)

    (tmp_path / "server.crt").mkdir()
    with pytest.raises(ConfigurationError, match="one regular file"):
        preflight_daemon_remote_configuration(configuration)


def test_loader_path_arguments_are_mutually_exclusive(tmp_path: Path) -> None:
    paths = resolve_configuration_paths(environ={}, home=tmp_path / "home")

    with pytest.raises(ValueError, match="not both"):
        load_daemon_remote_configuration(tmp_path / "remote.toml", paths=paths)


def test_preflight_rejects_wrong_configuration_type() -> None:
    with pytest.raises(TypeError, match="preflight requires"):
        preflight_daemon_remote_configuration(object())  # type: ignore[arg-type]
