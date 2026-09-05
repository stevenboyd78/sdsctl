"""Ingress-only lifecycle operations for Home Assistant App advanced access."""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .exceptions import SDS200Error
from .home_assistant_app import (
    HOME_ASSISTANT_APP_OPTIONS_PATH,
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppOptions,
    HomeAssistantAppSupervisorInfo,
    load_home_assistant_app_options,
    reconcile_home_assistant_app_advanced_exposure,
)
from .home_assistant_app_advanced import (
    HomeAssistantAppAdvancedAccessPaths,
    HomeAssistantAppAdvancedAccessSnapshot,
    default_home_assistant_app_advanced_access_paths,
    inspect_home_assistant_app_advanced_access,
    issue_home_assistant_app_remote_client,
    load_home_assistant_app_advanced_access_context,
    load_home_assistant_app_advanced_access_state,
    rotate_home_assistant_app_dashboard_password,
    rotate_home_assistant_app_server_identity,
    set_home_assistant_app_remote_client_revoked,
    write_home_assistant_app_remote_daemon_configuration,
)

HOME_ASSISTANT_APP_ADVANCED_INITIALIZE_CONFIRMATION = "INITIALIZE"
HOME_ASSISTANT_APP_ADVANCED_PASSWORD_CONFIRMATION = "ROTATE"

_advanced_access_lock = threading.Lock()
logger = logging.getLogger(__name__)


@contextmanager
def _locked_advanced_access() -> Iterator[None]:
    if not _advanced_access_lock.acquire(blocking=False):
        raise SDS200Error("Another advanced-access lifecycle action is in progress.")
    try:
        yield
    finally:
        _advanced_access_lock.release()


def _context(
    *,
    options_path: str | Path,
    paths: HomeAssistantAppAdvancedAccessPaths | None,
    options: HomeAssistantAppOptions | None,
    supervisor_info: HomeAssistantAppSupervisorInfo | None,
) -> tuple[
    HomeAssistantAppOptions,
    HomeAssistantAppAdvancedExposure,
    HomeAssistantAppAdvancedAccessPaths,
]:
    selected_options = (
        load_home_assistant_app_options(options_path) if options is None else options
    )
    if not isinstance(selected_options, HomeAssistantAppOptions):
        raise TypeError("Advanced-access Ingress requires Home Assistant App options.")
    selected_paths = (
        default_home_assistant_app_advanced_access_paths()
        if paths is None
        else paths
    )
    if not isinstance(selected_paths, HomeAssistantAppAdvancedAccessPaths):
        raise TypeError("Advanced-access Ingress requires App-private paths.")
    selected_info = (
        load_home_assistant_app_advanced_access_context(selected_paths)
        if supervisor_info is None
        else supervisor_info
    )
    exposure = reconcile_home_assistant_app_advanced_exposure(
        selected_options,
        selected_info,
    )
    return selected_options, exposure, selected_paths


def _status_document(
    options: HomeAssistantAppOptions,
    exposure: HomeAssistantAppAdvancedExposure,
    snapshot: HomeAssistantAppAdvancedAccessSnapshot,
) -> dict[str, object]:
    active_clients = sum(not client.revoked for client in snapshot.clients)
    return {
        "configuration": {
            "remote_daemon_enabled": options.remote_daemon_enabled,
            "native_dashboard_enabled": options.native_dashboard_enabled,
            "server_name_configured": bool(options.advanced_access_server_name),
            "host_address_configured": bool(
                options.advanced_access_host_address
            ),
        },
        "publication": {
            "remote_daemon_host_port": exposure.remote_daemon_host_port,
            "native_dashboard_host_port": exposure.native_dashboard_host_port,
            "scope": "home_assistant_host_all_interfaces",
        },
        "lifecycle": snapshot.as_dict(),
        "readiness": {
            "remote_daemon": (
                snapshot.identity_present and active_clients > 0
            ),
            "native_dashboard": (
                snapshot.identity_present
                and snapshot.dashboard_password_present
            ),
        },
        "restart_required_after": [
            "server_identity_rotation",
            "dashboard_password_rotation",
        ],
    }


def home_assistant_app_advanced_ingress_status(
    *,
    options_path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
    paths: HomeAssistantAppAdvancedAccessPaths | None = None,
    options: HomeAssistantAppOptions | None = None,
    supervisor_info: HomeAssistantAppSupervisorInfo | None = None,
) -> dict[str, object]:
    """Return redacted advanced-access state for the Ingress workspace."""

    with _locked_advanced_access():
        selected_options, exposure, selected_paths = _context(
            options_path=options_path,
            paths=paths,
            options=options,
            supervisor_info=supervisor_info,
        )
        snapshot = inspect_home_assistant_app_advanced_access(selected_paths)
        return _status_document(selected_options, exposure, snapshot)


def _require_confirmation(value: object, expected: str) -> None:
    if type(value) is not str or value != expected:
        raise SDS200Error("Advanced-access confirmation does not match.")


def _request_parent_reload() -> None:
    reload_signal = getattr(signal, "SIGHUP", None)
    if not isinstance(reload_signal, int):
        raise SDS200Error("Advanced-access credential reload is unavailable.")
    os.kill(os.getppid(), reload_signal)


def _reload_remote_credentials_if_enabled(
    options: HomeAssistantAppOptions,
    exposure: HomeAssistantAppAdvancedExposure,
    paths: HomeAssistantAppAdvancedAccessPaths,
    request_parent_reload: Callable[[], None],
) -> bool:
    if not options.remote_daemon_enabled:
        return False
    was_active = paths.runtime_remote_configuration.exists()
    write_home_assistant_app_remote_daemon_configuration(paths, exposure)
    if was_active:
        try:
            request_parent_reload()
        except (OSError, SDS200Error):
            logger.warning(
                "Advanced-access remote credential reload could not be "
                "requested; restart this App to activate the change."
            )
            return False
    return was_active


def rotate_home_assistant_app_advanced_ingress_identity(
    confirmation: object,
    *,
    options_path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
    paths: HomeAssistantAppAdvancedAccessPaths | None = None,
    options: HomeAssistantAppOptions | None = None,
    supervisor_info: HomeAssistantAppSupervisorInfo | None = None,
) -> dict[str, object]:
    """Create or rotate TLS identity material without returning its key."""

    with _locked_advanced_access():
        selected_options, exposure, selected_paths = _context(
            options_path=options_path,
            paths=paths,
            options=options,
            supervisor_info=supervisor_info,
        )
        if not selected_options.advanced_access_server_name:
            raise SDS200Error(
                "Configure the advanced-access server name before identity setup."
            )
        before = inspect_home_assistant_app_advanced_access(selected_paths)
        expected = (
            before.certificate_sha256
            if before.identity_present
            else HOME_ASSISTANT_APP_ADVANCED_INITIALIZE_CONFIRMATION
        )
        assert expected is not None
        _require_confirmation(confirmation, expected)
        snapshot = rotate_home_assistant_app_server_identity(
            selected_paths,
            selected_options.advanced_access_server_name,
        )
        if selected_options.remote_daemon_enabled:
            active_clients = sum(not client.revoked for client in snapshot.clients)
            if active_clients > 0:
                write_home_assistant_app_remote_daemon_configuration(
                    selected_paths,
                    exposure,
                )
        return {
            "status": _status_document(selected_options, exposure, snapshot),
            "restart_required": exposure.enabled,
        }


def rotate_home_assistant_app_advanced_ingress_dashboard_password(
    confirmation: object,
    *,
    display_only: bool = False,
    options_path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
    paths: HomeAssistantAppAdvancedAccessPaths | None = None,
    options: HomeAssistantAppOptions | None = None,
    supervisor_info: HomeAssistantAppSupervisorInfo | None = None,
) -> dict[str, object]:
    """Rotate and return the native-dashboard password exactly once."""

    if type(display_only) is not bool:
        raise TypeError("Display-only flag must be boolean.")
    _require_confirmation(
        confirmation,
        "ROTATE DISPLAY" if display_only else HOME_ASSISTANT_APP_ADVANCED_PASSWORD_CONFIRMATION,
    )
    with _locked_advanced_access():
        selected_options, exposure, selected_paths = _context(
            options_path=options_path,
            paths=paths,
            options=options,
            supervisor_info=supervisor_info,
        )
        one_time = (
            rotate_home_assistant_app_dashboard_password(selected_paths, display_only=True)
            if display_only else rotate_home_assistant_app_dashboard_password(selected_paths)
        )
        snapshot = inspect_home_assistant_app_advanced_access(selected_paths)
        return {
            "status": _status_document(selected_options, exposure, snapshot),
            "password": one_time.password,
            "restart_required": selected_options.native_dashboard_enabled,
        }


def issue_home_assistant_app_advanced_ingress_client(
    *,
    client_id: object,
    control: object,
    replace: object,
    confirmation: object,
    options_path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
    paths: HomeAssistantAppAdvancedAccessPaths | None = None,
    options: HomeAssistantAppOptions | None = None,
    supervisor_info: HomeAssistantAppSupervisorInfo | None = None,
    request_parent_reload: Callable[[], None] = _request_parent_reload,
) -> dict[str, object]:
    """Issue or rotate one independent remote client credential exactly once."""

    if type(client_id) is not str:
        raise TypeError("Advanced-access client ID must be a string.")
    if type(control) is not bool or type(replace) is not bool:
        raise TypeError("Advanced-access client flags must be boolean.")
    _require_confirmation(confirmation, client_id)
    with _locked_advanced_access():
        selected_options, exposure, selected_paths = _context(
            options_path=options_path,
            paths=paths,
            options=options,
            supervisor_info=supervisor_info,
        )
        remote_port = exposure.remote_daemon_host_port
        if remote_port is None:
            raise SDS200Error(
                "Enable the advanced remote daemon and assign its Network "
                "host port before issuing a client."
            )
        enrollment = issue_home_assistant_app_remote_client(
            selected_paths,
            client_id=client_id,
            control=control,
            host_address=selected_options.advanced_access_host_address,
            server_name=selected_options.advanced_access_server_name,
            host_port=remote_port,
            replace=replace,
        )
        reloaded = _reload_remote_credentials_if_enabled(
            selected_options,
            exposure,
            selected_paths,
            request_parent_reload,
        )
        snapshot = inspect_home_assistant_app_advanced_access(selected_paths)
        return {
            "status": _status_document(selected_options, exposure, snapshot),
            "client_id": enrollment.client_id,
            "credential": enrollment.credential,
            "certificate": enrollment.certificate,
            "profile": enrollment.profile,
            "reload_requested": reloaded,
            "restart_required": (
                selected_options.remote_daemon_enabled and not reloaded
            ),
        }


def set_home_assistant_app_advanced_ingress_client_revoked(
    *,
    client_id: object,
    revoked: object,
    confirmation: object,
    options_path: str | Path = HOME_ASSISTANT_APP_OPTIONS_PATH,
    paths: HomeAssistantAppAdvancedAccessPaths | None = None,
    options: HomeAssistantAppOptions | None = None,
    supervisor_info: HomeAssistantAppSupervisorInfo | None = None,
    request_parent_reload: Callable[[], None] = _request_parent_reload,
) -> dict[str, object]:
    """Selectively revoke or restore one client and reload active credentials."""

    if type(client_id) is not str:
        raise TypeError("Advanced-access client ID must be a string.")
    if type(revoked) is not bool:
        raise TypeError("Advanced-access revoked state must be boolean.")
    _require_confirmation(confirmation, client_id)
    with _locked_advanced_access():
        selected_options, exposure, selected_paths = _context(
            options_path=options_path,
            paths=paths,
            options=options,
            supervisor_info=supervisor_info,
        )
        if selected_options.remote_daemon_enabled and revoked:
            state = load_home_assistant_app_advanced_access_state(selected_paths)
            remaining = sum(
                not client.revoked and client.client_id != client_id
                for client in state.clients
            )
            if remaining == 0:
                raise SDS200Error(
                    "The enabled remote daemon requires one active client."
                )
        snapshot = set_home_assistant_app_remote_client_revoked(
            selected_paths,
            client_id=client_id,
            revoked=revoked,
        )
        reloaded = _reload_remote_credentials_if_enabled(
            selected_options,
            exposure,
            selected_paths,
            request_parent_reload,
        )
        return {
            "status": _status_document(selected_options, exposure, snapshot),
            "reload_requested": reloaded,
            "restart_required": (
                selected_options.remote_daemon_enabled and not reloaded
            ),
        }


def read_home_assistant_app_advanced_ingress_certificate(
    *,
    paths: HomeAssistantAppAdvancedAccessPaths | None = None,
) -> bytes:
    """Return only the selected public certificate for explicit download."""

    with _locked_advanced_access():
        selected_paths = (
            default_home_assistant_app_advanced_access_paths()
            if paths is None
            else paths
        )
        state = load_home_assistant_app_advanced_access_state(selected_paths)
        if state.identity_generation is None:
            raise SDS200Error("Advanced-access server identity is not initialized.")
        inspect_home_assistant_app_advanced_access(selected_paths)
        return selected_paths.certificate(state.identity_generation).read_bytes()


__all__ = [
    "HOME_ASSISTANT_APP_ADVANCED_INITIALIZE_CONFIRMATION",
    "HOME_ASSISTANT_APP_ADVANCED_PASSWORD_CONFIRMATION",
    "home_assistant_app_advanced_ingress_status",
    "issue_home_assistant_app_advanced_ingress_client",
    "read_home_assistant_app_advanced_ingress_certificate",
    "rotate_home_assistant_app_advanced_ingress_dashboard_password",
    "rotate_home_assistant_app_advanced_ingress_identity",
    "set_home_assistant_app_advanced_ingress_client_revoked",
]
