from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from sds200 import (
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteAuthenticationError,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteClientIdentity,
    DaemonRemoteCredential,
    DaemonRemoteCredentialAuthority,
    DaemonRemoteCredentialGeneration,
    DaemonRemoteCredentialLifecycleSnapshot,
    DaemonRemoteCredentialRegistry,
    DaemonRemoteCredentialReloadError,
    DaemonRemoteCredentialReloadErrorReason,
    DaemonRemoteCredentialSession,
    DaemonRemoteCredentialSessionExpired,
    DaemonRemoteListenerConfiguration,
    build_daemon_remote_authentication_request,
    create_daemon_remote_challenge,
)

KEY_A = bytes(range(32))
KEY_B = bytes(reversed(range(32)))
KEY_C = b"c" * 32


def _encoded(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii")


def _write_credential(path: Path, key: bytes) -> None:
    path.write_text(_encoded(key) + "\n", encoding="ascii")
    path.chmod(0o600)


def _client(
    path: Path,
    *,
    client_id: str,
    control: bool = False,
    revoked: bool = False,
) -> DaemonRemoteClientIdentity:
    scopes = [DaemonRemoteAuthorizationScope.OBSERVE]
    if control:
        scopes.append(DaemonRemoteAuthorizationScope.CONTROL)
    return DaemonRemoteClientIdentity(
        client_id,
        path,
        tuple(scopes),
        revoked,
    )


def _configuration(
    tmp_path: Path,
    clients: tuple[DaemonRemoteClientIdentity, ...],
    *,
    address: str = "192.168.40.10",
    port: int = 50443,
    certificate: Path | None = None,
    private_key: Path | None = None,
) -> DaemonRemoteListenerConfiguration:
    return DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address=address,
        port=port,
        certificate_file=certificate or tmp_path / "server.crt",
        private_key_file=private_key or tmp_path / "server.key",
        clients=clients,
    )


def _identity(
    client_id: str,
    *,
    control: bool = False,
) -> DaemonRemoteAuthenticatedIdentity:
    scopes = [DaemonRemoteAuthorizationScope.OBSERVE]
    if control:
        scopes.append(DaemonRemoteAuthorizationScope.CONTROL)
    return DaemonRemoteAuthenticatedIdentity(client_id, tuple(scopes))


def _authenticate(
    registry: DaemonRemoteCredentialRegistry,
    *,
    client_id: str,
    key: bytes,
) -> DaemonRemoteAuthenticatedIdentity:
    challenge = create_daemon_remote_challenge(nonce_factory=lambda size: b"s" * size)
    request = build_daemon_remote_authentication_request(
        challenge,
        client_id=client_id,
        credential=DaemonRemoteCredential(key),
        nonce_factory=lambda size: b"c" * size,
    )
    return registry.authenticate(challenge, request)


def test_authority_loads_initial_generation_and_reports_only_redacted_counts(
    tmp_path: Path,
) -> None:
    active = tmp_path / "private-active.secret"
    revoked = tmp_path / "private-revoked.secret"
    _write_credential(active, KEY_A)
    configuration = _configuration(
        tmp_path,
        (
            _client(active, client_id="private-active", control=True),
            _client(revoked, client_id="private-revoked", revoked=True),
        ),
    )

    authority = DaemonRemoteCredentialAuthority(configuration)
    generation = authority.current_generation()
    snapshot = authority.snapshot()

    assert isinstance(generation, DaemonRemoteCredentialGeneration)
    assert generation.generation == 1
    assert generation.registry.active_credentials == 1
    assert snapshot == DaemonRemoteCredentialLifecycleSnapshot(
        generation=1,
        configured_clients=2,
        active_clients=1,
        revoked_clients=1,
        control_clients=1,
        active_sessions=0,
        successful_reloads=0,
        failed_reloads=0,
        invalidated_sessions=0,
        invalidation_failures=0,
        last_error=None,
    )
    rendered = repr(authority) + repr(generation) + json.dumps(snapshot.as_dict())
    assert "private-active" not in rendered
    assert "private-revoked" not in rendered
    assert str(tmp_path) not in rendered
    assert _encoded(KEY_A) not in rendered


def test_session_executes_only_while_registered_and_close_is_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "client.secret"
    _write_credential(path, KEY_A)
    authority = DaemonRemoteCredentialAuthority(
        _configuration(tmp_path, (_client(path, client_id="display"),))
    )
    invalidations: list[str] = []
    session = authority.register_session(
        authority.current_generation(),
        _identity("display"),
        invalidator=lambda: invalidations.append("invalidated"),
    )

    assert isinstance(session, DaemonRemoteCredentialSession)
    assert session.generation == 1
    assert session.active is True
    assert session.execute(lambda: "allowed") == "allowed"
    assert authority.snapshot().active_sessions == 1
    assert repr(session) == "DaemonRemoteCredentialSession(<redacted>)"

    session.close()
    session.close()
    assert session.active is False
    assert authority.snapshot().active_sessions == 0
    assert invalidations == []
    with pytest.raises(DaemonRemoteCredentialSessionExpired):
        session.execute(lambda: "denied")


def test_successful_rotation_commits_new_key_and_invalidates_old_generation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "display.secret"
    _write_credential(path, KEY_A)
    configuration = _configuration(
        tmp_path,
        (_client(path, client_id="display"),),
    )
    authority = DaemonRemoteCredentialAuthority(configuration)
    old_generation = authority.current_generation()
    invalidated = threading.Event()
    session = authority.register_session(
        old_generation,
        _identity("display"),
        invalidator=invalidated.set,
    )

    _write_credential(path, KEY_B)
    snapshot = authority.reload(configuration)

    assert snapshot.generation == 2
    assert snapshot.successful_reloads == 1
    assert snapshot.failed_reloads == 0
    assert snapshot.invalidated_sessions == 1
    assert snapshot.active_sessions == 0
    assert invalidated.is_set()
    assert session.active is False
    with pytest.raises(DaemonRemoteCredentialSessionExpired):
        session.execute(lambda: None)
    with pytest.raises(DaemonRemoteCredentialSessionExpired):
        authority.register_session(
            old_generation,
            _identity("display"),
            invalidator=lambda: None,
        )

    current = authority.current_generation().registry
    assert _authenticate(current, client_id="display", key=KEY_B).client_id == "display"
    with pytest.raises(DaemonRemoteAuthenticationError):
        _authenticate(current, client_id="display", key=KEY_A)


def test_revocation_removes_identity_and_reconnects_unchanged_clients(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.secret"
    second = tmp_path / "second.secret"
    _write_credential(first, KEY_A)
    _write_credential(second, KEY_B)
    current = _configuration(
        tmp_path,
        (
            _client(first, client_id="first"),
            _client(second, client_id="second", control=True),
        ),
    )
    authority = DaemonRemoteCredentialAuthority(current)
    invalidated: list[str] = []
    for client_id in ("first", "second"):
        authority.register_session(
            authority.current_generation(),
            _identity(client_id, control=client_id == "second"),
            invalidator=lambda client_id=client_id: invalidated.append(client_id),
        )

    replacement = _configuration(
        tmp_path,
        (
            _client(first, client_id="first", revoked=True),
            _client(second, client_id="second", control=True),
        ),
    )
    snapshot = authority.reload(replacement)

    assert snapshot.active_clients == 1
    assert snapshot.revoked_clients == 1
    assert snapshot.control_clients == 1
    assert snapshot.invalidated_sessions == 2
    assert sorted(invalidated) == ["first", "second"]
    registry = authority.current_generation().registry
    with pytest.raises(DaemonRemoteAuthenticationError):
        _authenticate(registry, client_id="first", key=KEY_A)
    assert _authenticate(registry, client_id="second", key=KEY_B).client_id == "second"


def test_failed_reload_preserves_registry_generation_and_live_sessions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "client.secret"
    _write_credential(path, KEY_A)
    configuration = _configuration(
        tmp_path,
        (_client(path, client_id="display"),),
    )
    authority = DaemonRemoteCredentialAuthority(configuration)
    invalidated = threading.Event()
    session = authority.register_session(
        authority.current_generation(),
        _identity("display"),
        invalidator=invalidated.set,
    )
    path.chmod(0o640)

    with pytest.raises(DaemonRemoteCredentialReloadError) as captured:
        authority.reload(configuration)

    assert captured.value.reason is DaemonRemoteCredentialReloadErrorReason.LOAD_FAILED
    assert captured.value.__cause__ is None
    snapshot = authority.snapshot()
    assert snapshot.generation == 1
    assert snapshot.successful_reloads == 0
    assert snapshot.failed_reloads == 1
    assert snapshot.active_sessions == 1
    assert snapshot.last_error == "load_failed"
    assert session.active is True
    assert invalidated.is_set() is False
    assert (
        _authenticate(
            authority.current_generation().registry,
            client_id="display",
            key=KEY_A,
        ).client_id
        == "display"
    )

    path.chmod(0o600)
    recovered = authority.reload(configuration)
    assert recovered.generation == 2
    assert recovered.successful_reloads == 1
    assert recovered.failed_reloads == 1
    assert recovered.last_error is None


@pytest.mark.parametrize(
    "replacement",
    ["disabled", "address", "port", "certificate", "private-key"],
)
def test_reload_rejects_listener_identity_changes_without_loading_credentials(
    tmp_path: Path,
    replacement: str,
) -> None:
    path = tmp_path / "private-client.secret"
    _write_credential(path, KEY_A)
    configuration = _configuration(
        tmp_path,
        (_client(path, client_id="private-client"),),
    )
    calls: list[Path] = []

    def load(credential_path: Path) -> DaemonRemoteCredential:
        calls.append(credential_path)
        return DaemonRemoteCredential(KEY_A)

    authority = DaemonRemoteCredentialAuthority(
        configuration,
        credential_loader=load,
    )
    calls.clear()
    if replacement == "disabled":
        changed = DaemonRemoteListenerConfiguration()
    else:
        changed = _configuration(
            tmp_path,
            (_client(path, client_id="private-client"),),
            address=("192.168.40.11" if replacement == "address" else "192.168.40.10"),
            port=50444 if replacement == "port" else 50443,
            certificate=(
                tmp_path / "changed.crt"
                if replacement == "certificate"
                else tmp_path / "server.crt"
            ),
            private_key=(
                tmp_path / "changed.key"
                if replacement == "private-key"
                else tmp_path / "server.key"
            ),
        )

    with pytest.raises(DaemonRemoteCredentialReloadError) as captured:
        authority.reload(changed)

    assert (
        captured.value.reason
        is DaemonRemoteCredentialReloadErrorReason.CONFIGURATION_MISMATCH
    )
    assert calls == []
    assert authority.snapshot().generation == 1
    assert authority.snapshot().failed_reloads == 1
    rendered = str(captured.value) + repr(authority)
    assert "192.168" not in rendered
    assert "private-client" not in rendered
    assert str(tmp_path) not in rendered


def test_reload_is_linearized_after_in_flight_authorized_action(
    tmp_path: Path,
) -> None:
    path = tmp_path / "client.secret"
    _write_credential(path, KEY_A)
    configuration = _configuration(
        tmp_path,
        (_client(path, client_id="display"),),
    )
    authority = DaemonRemoteCredentialAuthority(configuration)
    invalidated = threading.Event()
    session = authority.register_session(
        authority.current_generation(),
        _identity("display"),
        invalidator=invalidated.set,
    )
    action_started = threading.Event()
    release_action = threading.Event()
    action_finished = threading.Event()
    reload_finished = threading.Event()

    def action() -> None:
        action_started.set()
        release_action.wait(1.0)

    def run_action() -> None:
        session.execute(action)
        action_finished.set()

    def reload() -> None:
        authority.reload(configuration)
        reload_finished.set()

    action_thread = threading.Thread(target=run_action)
    reload_thread = threading.Thread(target=reload)
    action_thread.start()
    assert action_started.wait(1.0)
    reload_thread.start()
    time.sleep(0.03)
    assert reload_finished.is_set() is False

    release_action.set()
    action_thread.join(1.0)
    reload_thread.join(1.0)

    assert action_finished.is_set()
    assert reload_finished.is_set()
    assert invalidated.is_set()
    assert session.active is False


def test_invalidator_failure_does_not_restore_old_generation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "client.secret"
    _write_credential(path, KEY_A)
    configuration = _configuration(
        tmp_path,
        (_client(path, client_id="display"),),
    )
    authority = DaemonRemoteCredentialAuthority(configuration)

    def fail_invalidation() -> None:
        raise RuntimeError("private invalidator detail")

    session = authority.register_session(
        authority.current_generation(),
        _identity("display"),
        invalidator=fail_invalidation,
    )
    snapshot = authority.reload(configuration)

    assert snapshot.generation == 2
    assert snapshot.invalidated_sessions == 1
    assert snapshot.invalidation_failures == 1
    assert snapshot.last_error is None
    assert session.active is False
    assert "private invalidator detail" not in json.dumps(snapshot.as_dict())


def test_credential_lifecycle_constructors_and_inputs_are_strict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "client.secret"
    _write_credential(path, KEY_A)
    configuration = _configuration(
        tmp_path,
        (_client(path, client_id="display"),),
    )
    authority = DaemonRemoteCredentialAuthority(configuration)
    generation = authority.current_generation()
    identity = _identity("display")

    with pytest.raises(TypeError, match="authority requires"):
        DaemonRemoteCredentialAuthority(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="enabled configuration"):
        DaemonRemoteCredentialAuthority(DaemonRemoteListenerConfiguration())
    with pytest.raises(TypeError, match="loader must be callable"):
        DaemonRemoteCredentialAuthority(
            configuration,
            credential_loader=None,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="reload requires"):
        authority.reload(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="credential generation"):
        authority.register_session(
            object(),  # type: ignore[arg-type]
            identity,
            invalidator=lambda: None,
        )
    with pytest.raises(TypeError, match="authenticated identity"):
        authority.register_session(
            generation,
            object(),  # type: ignore[arg-type]
            invalidator=lambda: None,
        )
    with pytest.raises(TypeError, match="invalidator must be callable"):
        authority.register_session(
            generation,
            identity,
            invalidator=None,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="action must be callable"):
        session = authority.register_session(
            generation,
            identity,
            invalidator=lambda: None,
        )
        session.execute(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="reload error reason"):
        DaemonRemoteCredentialReloadError("load_failed")  # type: ignore[arg-type]
    for value, error_type in ((True, TypeError), ("1", TypeError), (0, ValueError)):
        with pytest.raises(error_type, match="credential generation"):
            DaemonRemoteCredentialGeneration(
                cast(int, value),
                generation.registry,
            )
    with pytest.raises(TypeError, match="credential registry"):
        DaemonRemoteCredentialGeneration(
            1,
            cast(DaemonRemoteCredentialRegistry, object()),
        )
    with pytest.raises(TypeError, match="credential authority"):
        DaemonRemoteCredentialSession(
            cast(DaemonRemoteCredentialAuthority, object()),
            token=1,
            generation=1,
        )
    for value, error_type in ((True, TypeError), ("1", TypeError), (0, ValueError)):
        with pytest.raises(error_type, match="session token"):
            DaemonRemoteCredentialSession(
                authority,
                token=cast(int, value),
                generation=1,
            )
    for value, error_type in ((True, TypeError), ("1", TypeError), (0, ValueError)):
        with pytest.raises(error_type, match="session generation"):
            DaemonRemoteCredentialSession(
                authority,
                token=1,
                generation=cast(int, value),
            )
    assert str(DaemonRemoteCredentialSessionExpired()).endswith(
        "is no longer current."
    )
