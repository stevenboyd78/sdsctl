from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    DAEMON_REMOTE_CLIENT_PROFILES_FILENAME,
    DAEMON_REMOTE_CLIENT_PROFILES_VERSION,
    ConfigurationError,
    DaemonRemoteClientConfiguration,
    DaemonRemoteClientProfiles,
    default_daemon_remote_client_profiles_path,
    load_daemon_remote_client_profiles,
    resolve_configuration_paths,
)

PRIVATE_VALUES = (
    "192.168.20.41",
    "scanner.private.example",
    "pi-display",
    "private-ca.pem",
    "private-client.secret",
)


def _valid_document(tmp_path: Path, *, port: str = "port = 50444\n") -> str:
    return (
        "version = 1\n\n"
        "[profiles.pi-display]\n"
        'address = "192.168.20.41"\n'
        f"{port}"
        'server_hostname = "scanner.private.example"\n'
        f'certificate_file = "{tmp_path / "private-ca.pem"}"\n'
        'client_id = "pi-display"\n'
        f'credential_file = "{tmp_path / "private-client.secret"}"\n'
    )


def test_remote_client_profile_default_path_and_absent_document(
    tmp_path: Path,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc",
    )

    expected = paths.user_config_dir / DAEMON_REMOTE_CLIENT_PROFILES_FILENAME
    assert default_daemon_remote_client_profiles_path(paths) == expected
    profiles = load_daemon_remote_client_profiles(paths=paths)
    assert repr(profiles) == "DaemonRemoteClientProfiles(count=0)"
    assert dict(profiles.profiles) == {}


def test_remote_client_profile_loader_selects_one_exact_strict_profile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text(_valid_document(tmp_path), encoding="utf-8")

    profiles = load_daemon_remote_client_profiles(path)
    selected = profiles.select("pi-display")

    assert isinstance(selected, DaemonRemoteClientConfiguration)
    assert selected.address == "192.168.20.41"
    assert selected.port == 50444
    assert selected.server_hostname == "scanner.private.example"
    assert selected.certificate_file == tmp_path / "private-ca.pem"
    assert selected.client_id == "pi-display"
    assert selected.credential_file == tmp_path / "private-client.secret"
    assert repr(profiles) == "DaemonRemoteClientProfiles(count=1)"
    assert repr(selected) == "DaemonRemoteClientConfiguration(port=50444)"


def test_remote_client_profile_uses_the_reviewed_default_port(
    tmp_path: Path,
) -> None:
    path = tmp_path / "profiles.toml"
    path.write_text(_valid_document(tmp_path, port=""), encoding="utf-8")

    assert load_daemon_remote_client_profiles(path).select("pi-display").port == 50443


@pytest.mark.parametrize(
    "document",
    [
        "version = 2\n[profiles]\n",
        "version = 1\n",
        "version = 1\nunsupported = true\n[profiles]\n",
        "version = 1\n[profiles.'bad profile']\naddress = 4\n",
        "version = 1\n[profiles.pi]\nunsupported = true\n",
    ],
)
def test_remote_client_profile_loader_rejects_invalid_or_unknown_shapes(
    tmp_path: Path,
    document: str,
) -> None:
    path = tmp_path / "private-profile-name.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_daemon_remote_client_profiles(path)


def test_remote_client_profile_failures_and_representations_are_redacted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-profile-name.toml"
    path.write_text(_valid_document(tmp_path), encoding="utf-8")
    profiles = load_daemon_remote_client_profiles(path)

    with pytest.raises(ConfigurationError) as missing:
        profiles.select("private-missing-profile")
    rendered = f"{missing.value!r} {missing.value} {profiles!r}"
    assert "private-missing-profile" not in rendered
    assert str(path) not in rendered
    assert all(value not in rendered for value in PRIVATE_VALUES)

    path.write_text(
        _valid_document(tmp_path).replace("192.168.20.41", "203.0.113.41"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as invalid:
        load_daemon_remote_client_profiles(path)
    rendered = f"{invalid.value!r} {invalid.value}"
    assert "203.0.113.41" not in rendered
    assert str(path) not in rendered


def test_remote_client_profiles_constructor_is_immutable_and_strict(
    tmp_path: Path,
) -> None:
    configuration = DaemonRemoteClientConfiguration(
        address="192.168.20.41",
        port=50443,
        server_hostname="scanner.private.example",
        certificate_file=tmp_path / "private-ca.pem",
        client_id="pi-display",
        credential_file=tmp_path / "private-client.secret",
    )
    profiles = DaemonRemoteClientProfiles({"pi-display": configuration})

    with pytest.raises(TypeError):
        profiles.profiles["another"] = configuration  # type: ignore[index]
    with pytest.raises((TypeError, ValueError)):
        DaemonRemoteClientProfiles({"bad profile": configuration})

    assert DAEMON_REMOTE_CLIENT_PROFILES_VERSION == 1
