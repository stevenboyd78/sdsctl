from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path

import pytest

import sds200.home_assistant_app_advanced as advanced_access
from sds200.daemon_remote import (
    DaemonRemoteAuthorizationScope,
    load_daemon_remote_configuration,
)
from sds200.exceptions import SDS200Error
from sds200.home_assistant_app import (
    HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY,
    HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY,
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppSupervisorInfo,
)
from sds200.home_assistant_app_advanced import (
    HomeAssistantAppAdvancedAccessPaths,
    HomeAssistantAppAdvancedAccessState,
    HomeAssistantAppAdvancedClient,
    default_home_assistant_app_advanced_access_paths,
    inspect_home_assistant_app_advanced_access,
    issue_home_assistant_app_remote_client,
    load_home_assistant_app_advanced_access_context,
    load_home_assistant_app_advanced_access_state,
    render_home_assistant_app_remote_client_profile,
    rotate_home_assistant_app_dashboard_password,
    rotate_home_assistant_app_server_identity,
    set_home_assistant_app_remote_client_revoked,
    write_home_assistant_app_advanced_access_context,
    write_home_assistant_app_advanced_access_state,
    write_home_assistant_app_remote_daemon_configuration,
)

CERTIFICATE = b"-----BEGIN CERTIFICATE-----\npublic-test\n-----END CERTIFICATE-----\n"
PRIVATE_KEY = b"-----BEGIN PRIVATE KEY-----\nprivate-test\n-----END PRIVATE KEY-----\n"


def advanced_paths(tmp_path: Path) -> HomeAssistantAppAdvancedAccessPaths:
    return default_home_assistant_app_advanced_access_paths(
        root=tmp_path / "data" / "advanced-access",
        runtime_directory=tmp_path / "run" / "sdsctl",
    )


def fake_identity(server_name: str) -> tuple[bytes, bytes]:
    assert server_name in {"sdsctl.local", "192.168.20.15"}
    return CERTIFICATE, PRIVATE_KEY


def initialize(paths: HomeAssistantAppAdvancedAccessPaths) -> None:
    snapshot = rotate_home_assistant_app_server_identity(
        paths,
        "sdsctl.local",
        generator=fake_identity,
    )
    assert snapshot.identity_present is True


def test_advanced_access_state_round_trip_is_private_and_deterministic(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    state = HomeAssistantAppAdvancedAccessState(
        identity_generation="a" * 32,
        clients=(
            HomeAssistantAppAdvancedClient(
                "pi-control",
                scopes=(
                    DaemonRemoteAuthorizationScope.CONTROL,
                    DaemonRemoteAuthorizationScope.OBSERVE,
                ),
            ),
            HomeAssistantAppAdvancedClient("pi-display", revoked=True),
        ),
    )

    write_home_assistant_app_advanced_access_state(paths, state)

    assert load_home_assistant_app_advanced_access_state(paths) == state
    assert paths.state.stat().st_mode & 0o777 == 0o600
    assert paths.root.stat().st_mode & 0o777 == 0o700
    assert paths.identities.stat().st_mode & 0o777 == 0o700
    assert paths.clients.stat().st_mode & 0o777 == 0o700
    document = json.loads(paths.state.read_text(encoding="utf-8"))
    assert [client["client_id"] for client in document["clients"]] == [
        "pi-control",
        "pi-display",
    ]
    assert document["clients"][0]["scopes"] == ["control", "observe"]


def test_advanced_access_state_rejects_unsafe_mode(tmp_path: Path) -> None:
    paths = advanced_paths(tmp_path)
    write_home_assistant_app_advanced_access_state(
        paths,
        HomeAssistantAppAdvancedAccessState(),
    )
    paths.state.chmod(0o644)

    with pytest.raises(SDS200Error, match="unsafe"):
        load_home_assistant_app_advanced_access_state(paths)


def test_advanced_access_atomic_write_completes_partial_system_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = advanced_paths(tmp_path)
    real_write = os.write
    writes: list[int] = []

    def partial_write(descriptor: int, content: bytes) -> int:
        limited = content[: max(1, len(content) // 2)]
        writes.append(len(limited))
        return real_write(descriptor, limited)

    monkeypatch.setattr(advanced_access.os, "write", partial_write)
    expected = HomeAssistantAppAdvancedAccessState(
        clients=(HomeAssistantAppAdvancedClient("pi-display"),),
    )

    write_home_assistant_app_advanced_access_state(paths, expected)

    assert len(writes) > 1
    assert load_home_assistant_app_advanced_access_state(paths) == expected


def test_advanced_access_context_is_private_strict_and_secret_free(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    info = HomeAssistantAppSupervisorInfo(
        container_address="172.30.33.7",
        network={
            "50000/udp": 50000,
            HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY: 15044,
            HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY: None,
        },
        options={
            "scanner_host": "scanner.private.invalid",
            "remote_daemon_enabled": True,
            "native_dashboard_enabled": False,
            "advanced_access_server_name": "sdsctl.local",
            "advanced_access_host_address": "192.168.20.15",
        },
    )

    target = write_home_assistant_app_advanced_access_context(paths, info)
    loaded = load_home_assistant_app_advanced_access_context(paths)
    rendered = target.read_text(encoding="ascii")

    assert target == paths.runtime_remote_configuration.parent / (
        "home-assistant-advanced-context.json"
    )
    assert target.stat().st_mode & 0o777 == 0o600
    assert loaded.container_address == "172.30.33.7"
    assert dict(loaded.network) == {
        HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY: 15044,
        HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY: None,
    }
    assert loaded.options["remote_daemon_enabled"] is True
    assert "scanner.private.invalid" not in rendered
    assert "50000/udp" not in rendered


def test_identity_rotation_selects_complete_generation(tmp_path: Path) -> None:
    paths = advanced_paths(tmp_path)

    snapshot = rotate_home_assistant_app_server_identity(
        paths,
        "sdsctl.local",
        generator=fake_identity,
    )

    state = load_home_assistant_app_advanced_access_state(paths)
    assert state.identity_generation is not None
    assert paths.certificate(state.identity_generation).read_bytes() == CERTIFICATE
    assert paths.private_key(state.identity_generation).read_bytes() == PRIVATE_KEY
    assert paths.certificate(state.identity_generation).stat().st_mode & 0o777 == 0o644
    assert paths.private_key(state.identity_generation).stat().st_mode & 0o777 == 0o600
    assert snapshot.certificate_sha256 == hashlib.sha256(CERTIFICATE).hexdigest()
    assert "private-test" not in repr(snapshot)


def test_identity_rotation_retires_the_previous_private_generation(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    previous = load_home_assistant_app_advanced_access_state(paths)
    assert previous.identity_generation is not None
    previous_directory = paths.identity_directory(previous.identity_generation)

    rotate_home_assistant_app_server_identity(
        paths,
        "192.168.20.15",
        generator=fake_identity,
    )

    current = load_home_assistant_app_advanced_access_state(paths)
    assert current.identity_generation != previous.identity_generation
    assert not previous_directory.exists()
    assert [path.name for path in paths.identities.iterdir()] == [
        current.identity_generation
    ]


def test_identity_rotation_failure_does_not_replace_selected_generation(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    before = load_home_assistant_app_advanced_access_state(paths)

    def fail(server_name: str) -> tuple[bytes, bytes]:
        del server_name
        raise SDS200Error("generation failed")

    with pytest.raises(SDS200Error, match="generation failed"):
        rotate_home_assistant_app_server_identity(
            paths,
            "sdsctl.local",
            generator=fail,
        )

    assert load_home_assistant_app_advanced_access_state(paths) == before
    assert len(tuple(paths.identities.iterdir())) == 1


def test_dashboard_password_rotation_returns_value_once_and_stores_private_file(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)

    result = rotate_home_assistant_app_dashboard_password(paths)

    assert len(result.password) == 43
    assert paths.dashboard_password.read_text(encoding="ascii") == result.password + "\n"
    assert paths.dashboard_password.stat().st_mode & 0o777 == 0o600
    assert result.password not in repr(result)
    assert inspect_home_assistant_app_advanced_access(
        paths
    ).dashboard_password_present is True


def test_remote_client_profile_quotes_dotted_client_id() -> None:
    profile = render_home_assistant_app_remote_client_profile(
        client_id="pi.display",
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=50443,
    )

    assert '[profiles."pi.display"]' in profile
    assert 'client_id = "pi.display"' in profile
    assert tuple(tomllib.loads(profile)["profiles"]) == ("pi.display",)


def test_client_issue_rotation_and_selective_revocation_are_independent(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)

    display = issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-display",
        control=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=50443,
    )
    control = issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-control",
        control=True,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=15044,
    )

    assert display.credential != control.credential
    assert display.credential not in repr(display)
    assert display.credential not in display.profile
    assert display.certificate == CERTIFICATE.decode("ascii")
    assert "port = 50443" in display.profile
    assert "server_hostname = \"sdsctl.local\"" in display.profile
    assert paths.credential("pi-display").stat().st_mode & 0o777 == 0o600
    assert display.credential not in paths.state.read_text(encoding="utf-8")

    rotated = issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-display",
        control=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=50443,
        replace=True,
    )
    assert rotated.credential != display.credential

    snapshot = set_home_assistant_app_remote_client_revoked(
        paths,
        client_id="pi-display",
        revoked=True,
    )
    clients = {client.client_id: client for client in snapshot.clients}
    assert clients["pi-display"].revoked is True
    assert clients["pi-control"].revoked is False
    assert clients["pi-control"].scopes == (
        DaemonRemoteAuthorizationScope.CONTROL,
        DaemonRemoteAuthorizationScope.OBSERVE,
    )


def test_client_issue_requires_explicit_rotation_for_existing_identity(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    arguments = {
        "client_id": "pi-display",
        "control": False,
        "host_address": "192.168.20.15",
        "server_name": "sdsctl.local",
        "host_port": 50443,
    }
    issue_home_assistant_app_remote_client(paths, **arguments)

    with pytest.raises(SDS200Error, match="rotate it explicitly"):
        issue_home_assistant_app_remote_client(paths, **arguments)


def test_client_rotation_restores_previous_credential_if_state_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    arguments = {
        "client_id": "pi-display",
        "control": False,
        "host_address": "192.168.20.15",
        "server_name": "sdsctl.local",
        "host_port": 50443,
    }
    issue_home_assistant_app_remote_client(paths, **arguments)
    credential = paths.credential("pi-display").read_bytes()
    state = paths.state.read_bytes()

    monkeypatch.setattr(
        advanced_access,
        "write_home_assistant_app_advanced_access_state",
        lambda selected_paths, candidate: (_ for _ in ()).throw(
            SDS200Error("state commit failed")
        ),
    )

    with pytest.raises(SDS200Error, match="state commit failed"):
        issue_home_assistant_app_remote_client(
            paths,
            **arguments,
            replace=True,
        )

    assert paths.credential("pi-display").read_bytes() == credential
    assert paths.state.read_bytes() == state


def test_remote_daemon_configuration_is_generated_and_preflighted(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-display",
        control=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=15044,
    )
    exposure = HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7",
        remote_daemon_host_port=15044,
    )

    assert write_home_assistant_app_remote_daemon_configuration(
        paths,
        exposure,
    ) == paths.runtime_remote_configuration

    configuration = load_daemon_remote_configuration(
        paths.runtime_remote_configuration
    )
    assert configuration is not None
    assert configuration.enabled is True
    assert configuration.bind_address == "172.30.33.7"
    assert configuration.port == 50443
    assert [client.client_id for client in configuration.clients] == ["pi-display"]
    rendered = paths.runtime_remote_configuration.read_text(encoding="utf-8")
    credential = paths.credential("pi-display").read_text(encoding="ascii").strip()
    assert credential not in rendered
    assert paths.runtime_remote_configuration.stat().st_mode & 0o777 == 0o600


def test_remote_daemon_configuration_requires_one_active_client(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    exposure = HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7",
        remote_daemon_host_port=50443,
    )

    with pytest.raises(SDS200Error, match="one active client"):
        write_home_assistant_app_remote_daemon_configuration(paths, exposure)


def test_remote_daemon_configuration_validation_failure_preserves_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-display",
        control=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=50443,
    )
    exposure = HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7",
        remote_daemon_host_port=50443,
    )
    write_home_assistant_app_remote_daemon_configuration(paths, exposure)
    previous = paths.runtime_remote_configuration.read_bytes()
    issue_home_assistant_app_remote_client(
        paths,
        client_id="laptop",
        control=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=50443,
    )
    monkeypatch.setattr(
        advanced_access,
        "preflight_daemon_remote_configuration",
        lambda configuration: (_ for _ in ()).throw(
            SDS200Error("preflight failed")
        ),
    )

    with pytest.raises(SDS200Error, match="preflight failed"):
        write_home_assistant_app_remote_daemon_configuration(paths, exposure)

    assert paths.runtime_remote_configuration.read_bytes() == previous
    assert not tuple(
        paths.runtime_remote_configuration.parent.glob(".daemon-remote.toml.*.tmp")
    )


def test_remote_client_profile_is_secret_free() -> None:
    profile = render_home_assistant_app_remote_client_profile(
        client_id="pi-display",
        host_address="192.168.20.15",
        server_name="192.168.20.15",
        host_port=15044,
    )

    assert "version = 1" in profile
    assert "address = \"192.168.20.15\"" in profile
    assert "port = 15044" in profile
    assert "secret" in profile
    assert "PRIVATE KEY" not in profile


def test_advanced_access_paths_reject_cross_directory_secret(tmp_path: Path) -> None:
    root = tmp_path / "advanced"

    with pytest.raises(ValueError, match="directly inside"):
        HomeAssistantAppAdvancedAccessPaths(
            root=root,
            state=root / "state.json",
            identities=root / "identities",
            clients=root / "clients",
            dashboard_password=tmp_path / "password.secret",
            runtime_remote_configuration=tmp_path / "run" / "daemon-remote.toml",
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode assertion")
def test_client_secret_file_is_never_group_or_other_readable(tmp_path: Path) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-display",
        control=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=50443,
    )

    assert paths.credential("pi-display").stat().st_mode & 0o077 == 0
