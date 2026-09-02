from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

import sds200.daemon_remote_auth as remote_auth
from sds200 import (
    DAEMON_REMOTE_AUTH_ALGORITHM,
    DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES,
    DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES,
    DAEMON_REMOTE_AUTH_NONCE_BYTES,
    DAEMON_REMOTE_AUTH_PROOF_BYTES,
    DAEMON_REMOTE_AUTH_PROTOCOL,
    DAEMON_REMOTE_AUTH_VERSION,
    DAEMON_REMOTE_DEFAULT_PORT,
    DAEMON_REMOTE_PRIVATE_FILE_MODE,
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteAuthenticationError,
    DaemonRemoteAuthenticationErrorReason,
    DaemonRemoteAuthenticationRequest,
    DaemonRemoteAuthenticationResult,
    DaemonRemoteAuthenticationSession,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteChallenge,
    DaemonRemoteClientIdentity,
    DaemonRemoteCredential,
    DaemonRemoteCredentialError,
    DaemonRemoteCredentialRegistry,
    DaemonRemoteListenerConfiguration,
    build_daemon_remote_authentication_request,
    create_daemon_remote_challenge,
    load_daemon_remote_credential,
    load_daemon_remote_credential_registry,
)

SERVER_NONCE = bytes(range(DAEMON_REMOTE_AUTH_NONCE_BYTES))
CLIENT_NONCE = bytes(reversed(range(DAEMON_REMOTE_AUTH_NONCE_BYTES)))
CLIENT_ID = "pi-kiosk"
KEY = bytes(range(32, 32 + DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES))


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _nonce_factory(expected: bytes) -> Callable[[int], bytes]:
    def factory(size: int) -> bytes:
        assert size == DAEMON_REMOTE_AUTH_NONCE_BYTES
        return expected

    return factory


def _credential_file(path: Path, key: bytes = KEY, *, newline: bool = True) -> None:
    contents = _encoded(key) + ("\n" if newline else "")
    path.write_text(contents, encoding="ascii")
    path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)


def _client(
    path: Path,
    *,
    client_id: str = CLIENT_ID,
    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = (DaemonRemoteAuthorizationScope.OBSERVE,),
    revoked: bool = False,
) -> DaemonRemoteClientIdentity:
    return DaemonRemoteClientIdentity(
        client_id=client_id,
        credential_file=path,
        scopes=scopes,
        revoked=revoked,
    )


def _configuration(
    tmp_path: Path,
    clients: tuple[DaemonRemoteClientIdentity, ...],
) -> DaemonRemoteListenerConfiguration:
    return DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address="192.168.10.20",
        port=DAEMON_REMOTE_DEFAULT_PORT,
        certificate_file=tmp_path / "server.crt",
        private_key_file=tmp_path / "server.key",
        clients=clients,
    )


def _registry(
    tmp_path: Path,
    *,
    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = (DaemonRemoteAuthorizationScope.OBSERVE,),
) -> DaemonRemoteCredentialRegistry:
    path = tmp_path / "client.secret"
    _credential_file(path)
    return load_daemon_remote_credential_registry(
        _configuration(tmp_path, (_client(path, scopes=scopes),))
    )


def _challenge(nonce: bytes = SERVER_NONCE) -> DaemonRemoteChallenge:
    return create_daemon_remote_challenge(nonce_factory=_nonce_factory(nonce))


def _request(
    challenge: DaemonRemoteChallenge,
    credential: DaemonRemoteCredential | None = None,
    *,
    client_id: str = CLIENT_ID,
    nonce: bytes = CLIENT_NONCE,
) -> DaemonRemoteAuthenticationRequest:
    return build_daemon_remote_authentication_request(
        challenge,
        client_id=client_id,
        credential=credential or DaemonRemoteCredential(KEY),
        nonce_factory=_nonce_factory(nonce),
    )


def test_challenge_and_request_have_stable_canonical_frames() -> None:
    challenge = _challenge()
    request = _request(challenge)

    assert challenge == DaemonRemoteChallenge(server_nonce=_encoded(SERVER_NONCE))
    assert challenge.to_json_line() == (
        b'{"algorithm":"hmac-sha256","protocol":"sdsctl.daemon.auth",'
        b'"server_nonce":"AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",'
        b'"version":1}\n'
    )
    assert DaemonRemoteChallenge.from_json_line(challenge.to_json_line()) == challenge
    assert DaemonRemoteAuthenticationRequest.from_json_line(request.to_json_line()) == request
    assert request.client_nonce == _encoded(CLIENT_NONCE)
    assert request.proof == "TdXrAa9ZtQNdv4bLx8sLgunLfcEBMiSjA1D4vZskLgg"
    assert "TdXr" not in repr(request)


def test_proof_matches_an_independent_canonical_hmac_vector() -> None:
    challenge = _challenge()
    request = _request(challenge)
    transcript = json.dumps(
        {
            "algorithm": DAEMON_REMOTE_AUTH_ALGORITHM,
            "client_id": CLIENT_ID,
            "client_nonce": _encoded(CLIENT_NONCE),
            "protocol": DAEMON_REMOTE_AUTH_PROTOCOL,
            "server_nonce": _encoded(SERVER_NONCE),
            "version": DAEMON_REMOTE_AUTH_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    expected = _encoded(hmac.new(KEY, transcript, hashlib.sha256).digest())

    assert request.proof == expected
    assert len(base64.urlsafe_b64decode(expected + "=")) == (DAEMON_REMOTE_AUTH_PROOF_BYTES)


def test_authentication_result_has_strict_canonical_success_and_failure_frames() -> None:
    identity = DaemonRemoteAuthenticatedIdentity(
        CLIENT_ID,
        (
            DaemonRemoteAuthorizationScope.OBSERVE,
            DaemonRemoteAuthorizationScope.CONTROL,
        ),
    )
    success = DaemonRemoteAuthenticationResult.success(identity)
    failure = DaemonRemoteAuthenticationResult.failure(
        DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED
    )

    assert success.to_json_line() == (
        b'{"ok":true,"protocol":"sdsctl.daemon.auth",'
        b'"scopes":["control","observe"],"version":1}\n'
    )
    assert failure.to_json_line() == (
        b'{"error":{"code":"authentication_failed","message":'
        b'"Remote daemon authentication failed."},"ok":false,'
        b'"protocol":"sdsctl.daemon.auth","version":1}\n'
    )
    assert DaemonRemoteAuthenticationResult.from_json_line(success.to_json_line()) == success
    assert DaemonRemoteAuthenticationResult.from_json_line(failure.to_json_line()) == failure


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(protocol="future"),
        lambda payload: payload.update(version=2),
        lambda payload: payload.update(ok="yes"),
        lambda payload: payload.update(scopes="observe"),
        lambda payload: payload.update(scopes=[]),
        lambda payload: payload.update(scopes=["future"]),
        lambda payload: payload.update(scopes=["observe", "observe"]),
        lambda payload: payload.update(future=True),
        lambda payload: payload.pop("scopes"),
    ],
)
def test_authentication_success_result_parser_fails_closed(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload: dict[str, object] = json.loads(
        DaemonRemoteAuthenticationResult.success(
            DaemonRemoteAuthenticatedIdentity(
                CLIENT_ID,
                (DaemonRemoteAuthorizationScope.OBSERVE,),
            )
        ).to_json_line()
    )
    mutation(payload)

    with pytest.raises(DaemonRemoteAuthenticationError):
        DaemonRemoteAuthenticationResult.from_json_line(json.dumps(payload))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(error="failed"),
        lambda payload: payload.update(error={"code": "future", "message": "failed"}),
        lambda payload: payload.update(
            error={"code": "authentication_failed", "message": "private detail"}
        ),
        lambda payload: payload.update(scopes=["observe"]),
        lambda payload: payload["error"].update(future=True),
        lambda payload: payload.pop("error"),
    ],
)
def test_authentication_failure_result_parser_fails_closed(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload: dict[str, object] = json.loads(
        DaemonRemoteAuthenticationResult.failure(
            DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED
        ).to_json_line()
    )
    mutation(payload)

    with pytest.raises(DaemonRemoteAuthenticationError) as exc_info:
        DaemonRemoteAuthenticationResult.from_json_line(json.dumps(payload))
    assert "private detail" not in str(exc_info.value)


def test_authentication_result_construction_invariants() -> None:
    observe = (DaemonRemoteAuthorizationScope.OBSERVE,)
    reason = DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED

    with pytest.raises(TypeError, match="status"):
        DaemonRemoteAuthenticationResult(ok=1, scopes=observe)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="protocol"):
        DaemonRemoteAuthenticationResult(ok=True, scopes=observe, protocol="future")
    with pytest.raises(ValueError, match="version"):
        DaemonRemoteAuthenticationResult(ok=True, scopes=observe, version=2)
    with pytest.raises(TypeError, match="scopes must be a tuple"):
        DaemonRemoteAuthenticationResult(ok=True, scopes=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="scopes are invalid"):
        DaemonRemoteAuthenticationResult(ok=True, scopes=("observe",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="scopes must be unique"):
        DaemonRemoteAuthenticationResult(ok=True, scopes=observe + observe)
    with pytest.raises(ValueError, match="include observe"):
        DaemonRemoteAuthenticationResult(
            ok=True,
            scopes=(DaemonRemoteAuthorizationScope.CONTROL,),
        )
    with pytest.raises(ValueError, match="must not contain error"):
        DaemonRemoteAuthenticationResult(ok=True, scopes=observe, error=reason)
    with pytest.raises(ValueError, match="must not contain scopes"):
        DaemonRemoteAuthenticationResult(ok=False, scopes=observe, error=reason)
    with pytest.raises(TypeError, match="requires an authentication error reason"):
        DaemonRemoteAuthenticationResult(ok=False)
    with pytest.raises(TypeError, match="success requires"):
        DaemonRemoteAuthenticationResult.success(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="failure requires"):
        DaemonRemoteAuthenticationResult.failure("authentication_failed")  # type: ignore[arg-type]


def test_valid_proof_returns_non_secret_scopes(tmp_path: Path) -> None:
    scopes = (
        DaemonRemoteAuthorizationScope.OBSERVE,
        DaemonRemoteAuthorizationScope.CONTROL,
    )
    registry = _registry(tmp_path, scopes=scopes)
    challenge = _challenge()

    authenticated = registry.authenticate(challenge, _request(challenge))

    assert authenticated == DaemonRemoteAuthenticatedIdentity(
        client_id=CLIENT_ID,
        scopes=(
            DaemonRemoteAuthorizationScope.CONTROL,
            DaemonRemoteAuthorizationScope.OBSERVE,
        ),
    )
    assert authenticated.allows(DaemonRemoteAuthorizationScope.OBSERVE) is True
    assert authenticated.allows(DaemonRemoteAuthorizationScope.CONTROL) is True
    assert registry.active_credentials == 1
    assert repr(registry) == ("DaemonRemoteCredentialRegistry(active_credentials=1)")
    assert _encoded(KEY) not in repr(registry)


@pytest.mark.parametrize("failure", ["wrong-key", "wrong-id", "revoked-id"])
def test_bad_unknown_and_revoked_credentials_have_one_failure(
    tmp_path: Path,
    failure: str,
) -> None:
    path = tmp_path / "active.secret"
    _credential_file(path)
    revoked_path = tmp_path / "revoked.secret"
    configuration = _configuration(
        tmp_path,
        (
            _client(path),
            _client(
                revoked_path,
                client_id="revoked-client",
                revoked=True,
            ),
        ),
    )
    registry = load_daemon_remote_credential_registry(configuration)
    challenge = _challenge()
    if failure == "wrong-key":
        request = _request(
            challenge,
            DaemonRemoteCredential(b"x" * 32),
        )
    elif failure == "wrong-id":
        request = _request(challenge, client_id="unknown-client")
    else:
        request = _request(challenge, client_id="revoked-client")

    with pytest.raises(DaemonRemoteAuthenticationError) as exc_info:
        registry.authenticate(challenge, request)

    assert exc_info.value.reason is (DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED)
    assert str(exc_info.value) == "Remote daemon authentication failed."
    assert CLIENT_ID not in str(exc_info.value)
    assert _encoded(KEY) not in str(exc_info.value)
    assert revoked_path.exists() is False


def test_verification_checks_every_active_binding_without_early_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = tmp_path / "first.secret"
    second_path = tmp_path / "second.secret"
    _credential_file(first_path, b"a" * 32)
    _credential_file(second_path, b"b" * 32)
    registry = load_daemon_remote_credential_registry(
        _configuration(
            tmp_path,
            (
                _client(first_path, client_id="first"),
                _client(second_path, client_id="second"),
            ),
        )
    )
    calls: list[tuple[str, str]] = []
    original = hmac.compare_digest

    def compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(hmac, "compare_digest", compare)
    challenge = _challenge()
    request = _request(
        challenge,
        DaemonRemoteCredential(b"b" * 32),
        client_id="second",
    )

    assert registry.authenticate(challenge, request).client_id == "second"
    assert len(calls) == 4


def test_single_use_session_consumes_success_and_replay(tmp_path: Path) -> None:
    session = DaemonRemoteAuthenticationSession(
        _registry(tmp_path),
        nonce_factory=_nonce_factory(SERVER_NONCE),
    )
    request = _request(session.challenge)

    assert session.authenticate(request.to_json_line()).client_id == CLIENT_ID
    with pytest.raises(
        DaemonRemoteAuthenticationError,
        match="authentication failed",
    ) as replay:
        session.authenticate(request.to_json_line())
    assert replay.value.reason is (DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED)


def test_malformed_attempt_also_consumes_the_session(tmp_path: Path) -> None:
    session = DaemonRemoteAuthenticationSession(
        _registry(tmp_path),
        nonce_factory=_nonce_factory(SERVER_NONCE),
    )

    with pytest.raises(DaemonRemoteAuthenticationError) as malformed:
        session.authenticate(b"not-json\n")
    assert malformed.value.reason is DaemonRemoteAuthenticationErrorReason.INVALID_FRAME

    with pytest.raises(DaemonRemoteAuthenticationError) as second:
        session.authenticate(_request(session.challenge).to_json_line())
    assert second.value.reason is (DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED)


def test_proof_cannot_be_replayed_against_a_different_challenge(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    first = _challenge(b"a" * 32)
    second = _challenge(b"b" * 32)
    request = _request(first)

    with pytest.raises(
        DaemonRemoteAuthenticationError,
        match="authentication failed",
    ):
        registry.authenticate(second, request)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        (b"not-json", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        (b"[]", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        (b"{}", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        (b'{"protocol":"wrong"}', DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        (b"{}\n{}", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        (b"\xff", DaemonRemoteAuthenticationErrorReason.INVALID_FRAME),
        (
            b"x" * (DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES + 1),
            DaemonRemoteAuthenticationErrorReason.INVALID_FRAME,
        ),
    ],
)
def test_authentication_request_rejects_malformed_and_oversized_frames(
    payload: bytes,
    reason: DaemonRemoteAuthenticationErrorReason,
) -> None:
    with pytest.raises(DaemonRemoteAuthenticationError) as exc_info:
        DaemonRemoteAuthenticationRequest.from_json_line(payload)
    assert exc_info.value.reason is reason
    marker = payload[:20].decode("ascii", errors="ignore")
    if marker:
        assert marker not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        (
            "protocol",
            "future.auth",
            DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_PROTOCOL,
        ),
        (
            "version",
            2,
            DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_VERSION,
        ),
        (
            "client_nonce",
            "invalid",
            DaemonRemoteAuthenticationErrorReason.INVALID_FRAME,
        ),
        (
            "proof",
            "invalid",
            DaemonRemoteAuthenticationErrorReason.INVALID_FRAME,
        ),
        (
            "client_id",
            "bad/id",
            DaemonRemoteAuthenticationErrorReason.INVALID_FRAME,
        ),
    ],
)
def test_authentication_request_has_strict_versioned_fields(
    field: str,
    value: object,
    reason: DaemonRemoteAuthenticationErrorReason,
) -> None:
    payload = json.loads(_request(_challenge()).to_json_line())
    payload[field] = value

    with pytest.raises(DaemonRemoteAuthenticationError) as exc_info:
        DaemonRemoteAuthenticationRequest.from_json_line(json.dumps(payload))
    assert exc_info.value.reason is reason


def test_authentication_request_rejects_missing_extra_and_non_string_values() -> None:
    payload = json.loads(_request(_challenge()).to_json_line())
    del payload["proof"]
    with pytest.raises(DaemonRemoteAuthenticationError, match="frame is invalid"):
        DaemonRemoteAuthenticationRequest.from_json_line(json.dumps(payload))

    payload = json.loads(_request(_challenge()).to_json_line())
    payload["future"] = True
    with pytest.raises(DaemonRemoteAuthenticationError, match="frame is invalid"):
        DaemonRemoteAuthenticationRequest.from_json_line(json.dumps(payload))

    for field in ("client_id", "client_nonce", "proof"):
        payload = json.loads(_request(_challenge()).to_json_line())
        payload[field] = 1
        with pytest.raises(DaemonRemoteAuthenticationError, match="frame is invalid"):
            DaemonRemoteAuthenticationRequest.from_json_line(json.dumps(payload))


def test_challenge_parser_has_strict_versioned_fields() -> None:
    challenge = _challenge()
    payload = json.loads(challenge.to_json_line())
    payload["protocol"] = "future"
    with pytest.raises(DaemonRemoteAuthenticationError) as protocol:
        DaemonRemoteChallenge.from_json_line(json.dumps(payload))
    assert protocol.value.reason is (DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_PROTOCOL)

    payload = json.loads(challenge.to_json_line())
    payload["version"] = 2
    with pytest.raises(DaemonRemoteAuthenticationError) as version:
        DaemonRemoteChallenge.from_json_line(json.dumps(payload))
    assert version.value.reason is (DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_VERSION)

    mutations: tuple[Callable[[dict[str, object]], object], ...] = (
        lambda value: value.update(algorithm="future"),
        lambda value: value.update(server_nonce="invalid"),
        lambda value: value.update(server_nonce=1),
        lambda value: value.update(future=True),
        lambda value: value.pop("algorithm"),
    )
    for mutation in mutations:
        mutated_payload: dict[str, object] = json.loads(challenge.to_json_line())
        mutation(mutated_payload)
        with pytest.raises(DaemonRemoteAuthenticationError) as invalid:
            DaemonRemoteChallenge.from_json_line(json.dumps(mutated_payload))
        assert invalid.value.reason is DaemonRemoteAuthenticationErrorReason.INVALID_FRAME


def test_direct_challenge_and_request_construction_enforces_protocol_contract() -> None:
    encoded_nonce = _encoded(SERVER_NONCE)
    encoded_proof = _encoded(b"p" * DAEMON_REMOTE_AUTH_PROOF_BYTES)

    with pytest.raises(ValueError, match="protocol is unsupported"):
        DaemonRemoteChallenge(server_nonce=encoded_nonce, protocol="future")
    with pytest.raises(ValueError, match="version is unsupported"):
        DaemonRemoteChallenge(server_nonce=encoded_nonce, version=2)
    with pytest.raises(ValueError, match="algorithm is unsupported"):
        DaemonRemoteChallenge(server_nonce=encoded_nonce, algorithm="future")
    with pytest.raises(ValueError, match="protocol is unsupported"):
        DaemonRemoteAuthenticationRequest(
            client_id=CLIENT_ID,
            client_nonce=encoded_nonce,
            proof=encoded_proof,
            protocol="future",
        )
    with pytest.raises(ValueError, match="version is unsupported"):
        DaemonRemoteAuthenticationRequest(
            client_id=CLIENT_ID,
            client_nonce=encoded_nonce,
            proof=encoded_proof,
            version=2,
        )


@pytest.mark.parametrize(
    "factory",
    [None, lambda size: b"short", lambda size: "x" * size],
)
def test_nonce_factories_must_be_callable_and_return_exact_bytes(
    factory: object,
) -> None:
    with pytest.raises((TypeError, ValueError), match="nonce factory"):
        create_daemon_remote_challenge(nonce_factory=factory)  # type: ignore[arg-type]

    with pytest.raises((TypeError, ValueError), match="nonce factory"):
        build_daemon_remote_authentication_request(
            _challenge(),
            client_id=CLIENT_ID,
            credential=DaemonRemoteCredential(KEY),
            nonce_factory=factory,  # type: ignore[arg-type]
        )


def test_credential_representation_and_constructor_are_redacted() -> None:
    credential = DaemonRemoteCredential(KEY)

    assert repr(credential) == "DaemonRemoteCredential(<redacted>)"
    assert _encoded(KEY) not in repr(credential)
    with pytest.raises(TypeError, match="must be bytes"):
        DaemonRemoteCredential("secret")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        DaemonRemoteCredential(b"short")


@pytest.mark.parametrize("newline", [False, True])
def test_credential_loader_accepts_exact_base64url_with_optional_newline(
    tmp_path: Path,
    newline: bool,
) -> None:
    path = tmp_path / "client.secret"
    _credential_file(path, newline=newline)

    credential = load_daemon_remote_credential(path)
    request = _request(_challenge(), credential)

    assert request == _request(_challenge(), DaemonRemoteCredential(KEY))
    assert _encoded(KEY) not in repr(credential)


@pytest.mark.parametrize(
    "setup",
    [
        "relative",
        "missing",
        "directory",
        "symlink",
        "wrong-mode",
        "empty",
        "oversized",
        "bad-ascii",
        "bad-alphabet",
        "wrong-length",
        "extra-newline",
        "carriage-return",
    ],
)
def test_credential_loader_fails_uniformly_without_path_or_content(
    tmp_path: Path,
    setup: str,
) -> None:
    path = tmp_path / "private-client-name.secret"
    secret_marker = "do-not-log-this-secret"
    if setup == "relative":
        path = Path("relative.secret")
    elif setup == "directory":
        path.mkdir()
    elif setup == "symlink":
        real = tmp_path / "real.secret"
        _credential_file(real)
        path.symlink_to(real)
    elif setup == "wrong-mode":
        _credential_file(path)
        path.chmod(0o640)
    elif setup == "empty":
        path.write_bytes(b"")
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)
    elif setup == "oversized":
        path.write_bytes(b"x" * 4_097)
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)
    elif setup == "bad-ascii":
        path.write_bytes(b"\xff" * 43)
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)
    elif setup == "bad-alphabet":
        path.write_text("!" * 43, encoding="ascii")
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)
    elif setup == "wrong-length":
        path.write_text(secret_marker, encoding="ascii")
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)
    elif setup == "extra-newline":
        path.write_text(_encoded(KEY) + "\n\n", encoding="ascii")
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)
    elif setup == "carriage-return":
        path.write_text(_encoded(KEY) + "\r", encoding="ascii")
        path.chmod(DAEMON_REMOTE_PRIVATE_FILE_MODE)

    with pytest.raises(DaemonRemoteCredentialError) as exc_info:
        load_daemon_remote_credential(path)

    assert str(exc_info.value) == "Remote daemon credential is unavailable."
    assert "private-client-name" not in str(exc_info.value)
    assert secret_marker not in str(exc_info.value)


def test_registry_loads_active_credentials_once_and_skips_revoked(
    tmp_path: Path,
) -> None:
    active_path = tmp_path / "active.secret"
    revoked_path = tmp_path / "revoked.secret"
    configuration = _configuration(
        tmp_path,
        (
            _client(active_path),
            _client(revoked_path, client_id="revoked", revoked=True),
        ),
    )
    calls: list[Path] = []

    def loader(path: Path) -> DaemonRemoteCredential:
        calls.append(path)
        return DaemonRemoteCredential(KEY)

    registry = load_daemon_remote_credential_registry(
        configuration,
        credential_loader=loader,
    )

    assert registry.active_credentials == 1
    assert calls == [active_path]


def test_registry_loader_rejects_disabled_invalid_or_noncallable_input(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="requires DaemonRemote"):
        load_daemon_remote_credential_registry(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="enabled configuration"):
        load_daemon_remote_credential_registry(DaemonRemoteListenerConfiguration())
    path = tmp_path / "active.secret"
    configuration = _configuration(tmp_path, (_client(path),))
    with pytest.raises(TypeError, match="loader must be callable"):
        load_daemon_remote_credential_registry(
            configuration,
            credential_loader=None,  # type: ignore[arg-type]
        )


def test_authentication_objects_reject_wrong_runtime_types(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    challenge = _challenge()
    request = _request(challenge)

    with pytest.raises(TypeError, match="requires DaemonRemoteChallenge"):
        registry.authenticate(object(), request)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="requires DaemonRemoteAuthenticationRequest"):
        registry.authenticate(challenge, object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="session requires"):
        DaemonRemoteAuthenticationSession(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="proof construction requires DaemonRemoteChallenge"):
        build_daemon_remote_authentication_request(
            object(),  # type: ignore[arg-type]
            client_id=CLIENT_ID,
            credential=DaemonRemoteCredential(KEY),
        )
    with pytest.raises(TypeError, match="proof construction requires DaemonRemoteCredential"):
        build_daemon_remote_authentication_request(
            challenge,
            client_id=CLIENT_ID,
            credential=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="authorization check"):
        DaemonRemoteAuthenticatedIdentity(
            CLIENT_ID,
            (DaemonRemoteAuthorizationScope.OBSERVE,),
        ).allows(object())  # type: ignore[arg-type]


def test_authenticated_identity_and_registry_enforce_internal_invariants(
    tmp_path: Path,
) -> None:
    identity = _client(tmp_path / "client.secret")
    credential = DaemonRemoteCredential(KEY)
    binding = remote_auth._CredentialBinding(identity, credential)

    with pytest.raises(TypeError, match="non-empty tuple"):
        DaemonRemoteAuthenticatedIdentity(CLIENT_ID, [])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="unsupported value"):
        DaemonRemoteAuthenticatedIdentity(CLIENT_ID, ("observe",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires active credentials"):
        DaemonRemoteCredentialRegistry(())
    with pytest.raises(TypeError, match="bindings are invalid"):
        DaemonRemoteCredentialRegistry((object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="IDs must be unique"):
        DaemonRemoteCredentialRegistry((binding, binding))


def test_error_reason_constructor_is_strict() -> None:
    with pytest.raises(TypeError, match="error reason"):
        DaemonRemoteAuthenticationError("authentication_failed")  # type: ignore[arg-type]


def test_loader_detects_path_replacement_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "client.secret"
    replacement = tmp_path / "replacement.secret"
    _credential_file(path)
    _credential_file(replacement, b"r" * 32)
    original_lstat = Path.lstat
    calls = 0

    def changing_lstat(target: Path) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 2:
            return original_lstat(replacement)
        return original_lstat(target)

    monkeypatch.setattr(Path, "lstat", changing_lstat)

    with pytest.raises(DaemonRemoteCredentialError):
        load_daemon_remote_credential(path)


def test_loader_detects_opened_descriptor_identity_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "client.secret"
    replacement = tmp_path / "replacement.secret"
    _credential_file(path)
    _credential_file(replacement, b"r" * 32)
    replacement_stat = replacement.stat()
    original_fstat = os.fstat
    calls = 0

    def changing_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 1:
            return replacement_stat
        return original_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", changing_fstat)

    with pytest.raises(DaemonRemoteCredentialError):
        load_daemon_remote_credential(path)


def test_loader_closes_descriptor_when_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "client.secret"
    _credential_file(path)
    original_close = os.close
    closed: list[int] = []

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "close", close)

    def fail_parse(raw: bytes) -> DaemonRemoteCredential:
        del raw
        raise ValueError("private detail")

    monkeypatch.setattr(
        remote_auth,
        "_credential_from_file_bytes",
        fail_parse,
    )

    with pytest.raises(DaemonRemoteCredentialError) as exc_info:
        load_daemon_remote_credential(path)

    assert len(closed) == 1
    assert "private detail" not in str(exc_info.value)


def test_private_frame_and_value_defenses_are_bounded_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="client ID is invalid"):
        remote_auth._validate_client_id(1)
    with pytest.raises(ValueError, match="value is invalid"):
        remote_auth._decode_value(_encoded(KEY), expected_bytes=31)
    with pytest.raises(ValueError, match="frame is too large"):
        remote_auth._encode_frame({"padding": "x" * DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES})
    with pytest.raises(DaemonRemoteAuthenticationError) as invalid_type:
        remote_auth._decode_frame(object())  # type: ignore[arg-type]
    assert invalid_type.value.reason is DaemonRemoteAuthenticationErrorReason.INVALID_FRAME
    with pytest.raises(DaemonRemoteAuthenticationError) as invalid_unicode:
        remote_auth._decode_frame("\ud800")
    assert invalid_unicode.value.reason is DaemonRemoteAuthenticationErrorReason.INVALID_FRAME

    def invalid_base64(*args: object, **kwargs: object) -> bytes:
        del args, kwargs
        raise ValueError("private decoder detail")

    monkeypatch.setattr(base64, "b64decode", invalid_base64)
    with pytest.raises(ValueError, match="value is invalid") as decode_error:
        remote_auth._decode_value(_encoded(KEY), expected_bytes=32)
    assert "private decoder detail" not in str(decode_error.value)


def test_protocol_constants_are_bounded_and_versioned() -> None:
    assert DAEMON_REMOTE_AUTH_PROTOCOL == "sdsctl.daemon.auth"
    assert DAEMON_REMOTE_AUTH_VERSION == 1
    assert DAEMON_REMOTE_AUTH_ALGORITHM == "hmac-sha256"
    assert DAEMON_REMOTE_AUTH_NONCE_BYTES == 32
    assert DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES == 32
    assert DAEMON_REMOTE_AUTH_PROOF_BYTES == 32
    assert DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES == 4_096
    assert len(_challenge().to_json_line()) < DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES
