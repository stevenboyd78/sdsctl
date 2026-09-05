from __future__ import annotations

import json
from pathlib import Path

import pytest

import sds200.home_assistant_app_advanced_ingress as advanced_ingress
from sds200.exceptions import SDS200Error
from sds200.home_assistant_app import (
    HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY,
    HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY,
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppOptions,
    HomeAssistantAppSupervisorInfo,
)
from sds200.home_assistant_app_advanced import (
    HomeAssistantAppAdvancedAccessPaths,
    default_home_assistant_app_advanced_access_paths,
    issue_home_assistant_app_remote_client,
    rotate_home_assistant_app_server_identity,
    write_home_assistant_app_advanced_access_context,
    write_home_assistant_app_remote_daemon_configuration,
)
from sds200.home_assistant_app_advanced_ingress import (
    HOME_ASSISTANT_APP_ADVANCED_INITIALIZE_CONFIRMATION,
    HOME_ASSISTANT_APP_ADVANCED_PASSWORD_CONFIRMATION,
    home_assistant_app_advanced_ingress_status,
    issue_home_assistant_app_advanced_ingress_client,
    read_home_assistant_app_advanced_ingress_certificate,
    rotate_home_assistant_app_advanced_ingress_dashboard_password,
    rotate_home_assistant_app_advanced_ingress_identity,
    set_home_assistant_app_advanced_ingress_client_revoked,
)

CERTIFICATE = b"-----BEGIN CERTIFICATE-----\npublic-test\n-----END CERTIFICATE-----\n"
PRIVATE_KEY = b"-----BEGIN PRIVATE KEY-----\nprivate-test\n-----END PRIVATE KEY-----\n"


def advanced_paths(tmp_path: Path) -> HomeAssistantAppAdvancedAccessPaths:
    return default_home_assistant_app_advanced_access_paths(
        root=tmp_path / "data" / "advanced-access",
        runtime_directory=tmp_path / "run" / "sdsctl",
    )


def options(*, remote: bool = False, native: bool = False) -> HomeAssistantAppOptions:
    return HomeAssistantAppOptions(
        scanner_host="scanner.local",
        remote_daemon_enabled=remote,
        native_dashboard_enabled=native,
        advanced_access_server_name="sdsctl.local",
        advanced_access_host_address=("192.168.20.15" if remote else ""),
    )


def supervisor_info(
    *,
    remote_port: int | None = None,
    native_port: int | None = None,
) -> HomeAssistantAppSupervisorInfo:
    return HomeAssistantAppSupervisorInfo(
        container_address="172.30.33.7",
        network={
            HOME_ASSISTANT_APP_REMOTE_DAEMON_PORT_KEY: remote_port,
            HOME_ASSISTANT_APP_NATIVE_DASHBOARD_PORT_KEY: native_port,
        },
    )


def initialize(paths: HomeAssistantAppAdvancedAccessPaths) -> None:
    rotate_home_assistant_app_server_identity(
        paths,
        "sdsctl.local",
        generator=lambda server_name: (CERTIFICATE, PRIVATE_KEY),
    )


def test_advanced_ingress_status_is_redacted_and_reports_readiness(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-display",
        control=False,
        replace=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=15044,
    )

    document = home_assistant_app_advanced_ingress_status(
        paths=paths,
        options=options(),
        supervisor_info=supervisor_info(),
    )
    rendered = repr(document)

    assert document["readiness"] == {
        "remote_daemon": True,
        "native_dashboard": False,
    }
    assert "pi-display" in rendered
    assert "172.30.33.7" not in rendered
    assert "private-test" not in rendered
    assert "credential" not in rendered


def test_advanced_ingress_uses_parent_runtime_context_without_supervisor_token(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    options_path = tmp_path / "options.json"
    options_path.write_text(
        json.dumps({"scanner_host": "scanner.local"}),
        encoding="utf-8",
    )
    write_home_assistant_app_advanced_access_context(
        paths,
        supervisor_info(),
    )

    document = home_assistant_app_advanced_ingress_status(
        options_path=options_path,
        paths=paths,
    )

    assert document["configuration"] == {
        "remote_daemon_enabled": False,
        "native_dashboard_enabled": False,
        "server_name_configured": False,
        "host_address_configured": False,
    }
    assert document["publication"] == {
        "remote_daemon_host_port": None,
        "native_dashboard_host_port": None,
        "scope": "home_assistant_host_all_interfaces",
    }


def test_advanced_ingress_status_fails_closed_during_another_action(
    tmp_path: Path,
) -> None:
    lock = advanced_ingress._advanced_access_lock
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(SDS200Error, match="action is in progress"):
            home_assistant_app_advanced_ingress_status(
                paths=advanced_paths(tmp_path),
                options=options(),
                supervisor_info=supervisor_info(),
            )
    finally:
        lock.release()


def test_advanced_ingress_identity_requires_exact_confirmation_and_hides_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = advanced_paths(tmp_path)
    monkeypatch.setattr(
        "sds200.home_assistant_app_advanced_ingress."
        "rotate_home_assistant_app_server_identity",
        lambda selected_paths, server_name: rotate_home_assistant_app_server_identity(
            selected_paths,
            server_name,
            generator=lambda value: (CERTIFICATE, PRIVATE_KEY),
        ),
    )

    with pytest.raises(SDS200Error, match="confirmation"):
        rotate_home_assistant_app_advanced_ingress_identity(
            "wrong",
            paths=paths,
            options=options(),
            supervisor_info=supervisor_info(),
        )

    result = rotate_home_assistant_app_advanced_ingress_identity(
        HOME_ASSISTANT_APP_ADVANCED_INITIALIZE_CONFIRMATION,
        paths=paths,
        options=options(),
        supervisor_info=supervisor_info(),
    )

    assert result["restart_required"] is False
    assert "private-test" not in repr(result)
    assert read_home_assistant_app_advanced_ingress_certificate(paths=paths) == (
        CERTIFICATE
    )


def test_advanced_ingress_dashboard_password_is_returned_once(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)

    result = rotate_home_assistant_app_advanced_ingress_dashboard_password(
        HOME_ASSISTANT_APP_ADVANCED_PASSWORD_CONFIRMATION,
        paths=paths,
        options=options(native=True),
        supervisor_info=supervisor_info(native_port=15443),
    )
    password = result["password"]

    assert isinstance(password, str)
    assert len(password) == 43
    assert result["restart_required"] is True
    status = home_assistant_app_advanced_ingress_status(
        paths=paths,
        options=options(native=True),
        supervisor_info=supervisor_info(native_port=15443),
    )
    assert password not in repr(status)
    assert status["lifecycle"]["dashboard_password_present"] is True  # type: ignore[index]


def test_display_password_rotation_is_separate_and_requires_its_confirmation(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    arguments = dict(paths=paths, options=options(native=True),
                     supervisor_info=supervisor_info(native_port=15443))
    operator = rotate_home_assistant_app_advanced_ingress_dashboard_password(
        "ROTATE", **arguments,
    )["password"]
    with pytest.raises(SDS200Error, match="confirmation"):
        rotate_home_assistant_app_advanced_ingress_dashboard_password(
            "ROTATE", display_only=True, **arguments,
        )
    first = rotate_home_assistant_app_advanced_ingress_dashboard_password(
        "ROTATE DISPLAY", display_only=True, **arguments,
    )
    second = rotate_home_assistant_app_advanced_ingress_dashboard_password(
        "ROTATE DISPLAY", display_only=True, **arguments,
    )
    assert first["password"] != second["password"] != operator
    assert paths.dashboard_password.read_text().strip() == operator
    assert paths.display_password.read_text().strip() == second["password"]
    assert paths.display_password.stat().st_mode & 0o777 == 0o600
    assert second["restart_required"] is True
    assert second["password"] not in repr(second["status"])


def test_advanced_ingress_client_issue_and_rotation_reload_enabled_daemon(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    reloads: list[str] = []

    first = issue_home_assistant_app_advanced_ingress_client(
        client_id="pi-display",
        control=False,
        replace=False,
        confirmation="pi-display",
        paths=paths,
        options=options(remote=True),
        supervisor_info=supervisor_info(remote_port=15044),
        request_parent_reload=lambda: reloads.append("reload"),
    )
    second = issue_home_assistant_app_advanced_ingress_client(
        client_id="pi-display",
        control=True,
        replace=True,
        confirmation="pi-display",
        paths=paths,
        options=options(remote=True),
        supervisor_info=supervisor_info(remote_port=15044),
        request_parent_reload=lambda: reloads.append("reload"),
    )

    assert first["credential"] != second["credential"]
    assert first["certificate"] == CERTIFICATE.decode("ascii")
    assert '/etc/sdsctl/remote/pi-display/server.crt' in second["profile"]
    assert first["reload_requested"] is False
    assert first["restart_required"] is True
    assert second["reload_requested"] is True
    assert second["restart_required"] is False
    assert reloads == ["reload"]
    assert paths.runtime_remote_configuration.is_file()


def test_advanced_ingress_client_issue_requires_effective_host_port(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)

    with pytest.raises(SDS200Error, match="assign its Network host port"):
        issue_home_assistant_app_advanced_ingress_client(
            client_id="pi-display",
            control=False,
            replace=False,
            confirmation="pi-display",
            paths=paths,
            options=options(),
            supervisor_info=supervisor_info(),
        )

    assert not paths.credential("pi-display").exists()


def test_advanced_ingress_client_rotation_falls_back_to_explicit_restart(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    first = issue_home_assistant_app_advanced_ingress_client(
        client_id="pi-display",
        control=False,
        replace=False,
        confirmation="pi-display",
        paths=paths,
        options=options(remote=True),
        supervisor_info=supervisor_info(remote_port=15044),
        request_parent_reload=lambda: None,
    )

    def fail_reload() -> None:
        raise OSError("private failure detail")

    with caplog.at_level("WARNING"):
        rotated = issue_home_assistant_app_advanced_ingress_client(
            client_id="pi-display",
            control=False,
            replace=True,
            confirmation="pi-display",
            paths=paths,
            options=options(remote=True),
            supervisor_info=supervisor_info(remote_port=15044),
            request_parent_reload=fail_reload,
        )

    assert rotated["credential"] != first["credential"]
    assert rotated["reload_requested"] is False
    assert rotated["restart_required"] is True
    assert "restart this App" in caplog.text
    assert "private failure detail" not in caplog.text


def test_advanced_ingress_selective_revocation_preserves_other_client(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    for client_id in ("pi-display", "laptop"):
        issue_home_assistant_app_remote_client(
            paths,
            client_id=client_id,
            control=False,
            replace=False,
            host_address="192.168.20.15",
            server_name="sdsctl.local",
            host_port=15044,
        )
    write_home_assistant_app_remote_daemon_configuration(
        paths,
        HomeAssistantAppAdvancedExposure(
            container_address="172.30.33.7",
            remote_daemon_host_port=15044,
        ),
    )
    reloads: list[str] = []

    result = set_home_assistant_app_advanced_ingress_client_revoked(
        client_id="pi-display",
        revoked=True,
        confirmation="pi-display",
        paths=paths,
        options=options(remote=True),
        supervisor_info=supervisor_info(remote_port=15044),
        request_parent_reload=lambda: reloads.append("reload"),
    )

    clients = result["status"]["lifecycle"]["clients"]  # type: ignore[index]
    assert clients == [
        {"client_id": "laptop", "scopes": ["observe"], "revoked": False},
        {"client_id": "pi-display", "scopes": ["observe"], "revoked": True},
    ]
    assert reloads == ["reload"]


def test_advanced_ingress_rejects_revoking_last_enabled_client(
    tmp_path: Path,
) -> None:
    paths = advanced_paths(tmp_path)
    initialize(paths)
    issue_home_assistant_app_remote_client(
        paths,
        client_id="pi-display",
        control=False,
        replace=False,
        host_address="192.168.20.15",
        server_name="sdsctl.local",
        host_port=15044,
    )

    with pytest.raises(SDS200Error, match="requires one active client"):
        set_home_assistant_app_advanced_ingress_client_revoked(
            client_id="pi-display",
            revoked=True,
            confirmation="pi-display",
            paths=paths,
            options=options(remote=True),
            supervisor_info=supervisor_info(remote_port=15044),
            request_parent_reload=lambda: None,
        )
