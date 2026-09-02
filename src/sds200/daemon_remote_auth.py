"""Versioned challenge/proof authentication for the direct-TLS daemon transport.

The primitives in this module perform no network I/O.  A later listener may use
one single-use session only after direct TLS has authenticated the server.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import stat
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from .daemon_remote import (
    DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES,
    DAEMON_REMOTE_PRIVATE_FILE_MODE,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteClientIdentity,
    DaemonRemoteListenerConfiguration,
)

DAEMON_REMOTE_AUTH_PROTOCOL = "sdsctl.daemon.auth"
DAEMON_REMOTE_AUTH_VERSION = 1
DAEMON_REMOTE_AUTH_ALGORITHM = "hmac-sha256"
DAEMON_REMOTE_AUTH_NONCE_BYTES = 32
DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES = 32
DAEMON_REMOTE_AUTH_PROOF_BYTES = hashlib.sha256().digest_size
DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES = 4_096

_ENCODED_32_BYTE_VALUE_LENGTH: Final = 43
_URLSAFE_ALPHABET: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


class DaemonRemoteAuthenticationErrorReason(StrEnum):
    """Stable redacted authentication failure classes."""

    INVALID_FRAME = "invalid_frame"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    UNSUPPORTED_VERSION = "unsupported_version"
    AUTHENTICATION_FAILED = "authentication_failed"


_AUTHENTICATION_ERROR_MESSAGES = {
    DaemonRemoteAuthenticationErrorReason.INVALID_FRAME: (
        "Remote daemon authentication frame is invalid."
    ),
    DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_PROTOCOL: (
        "Remote daemon authentication protocol is unsupported."
    ),
    DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_VERSION: (
        "Remote daemon authentication version is unsupported."
    ),
    DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED: (
        "Remote daemon authentication failed."
    ),
}


class DaemonRemoteAuthenticationError(RuntimeError):
    """Report one stable authentication failure without peer or secret data."""

    def __init__(self, reason: DaemonRemoteAuthenticationErrorReason) -> None:
        if not isinstance(reason, DaemonRemoteAuthenticationErrorReason):
            raise TypeError(
                "Remote daemon authentication error reason must be "
                "DaemonRemoteAuthenticationErrorReason."
            )
        self.reason = reason
        super().__init__(_AUTHENTICATION_ERROR_MESSAGES[reason])


class DaemonRemoteCredentialError(RuntimeError):
    """Report a uniform local credential-loading failure without path detail."""

    def __init__(self) -> None:
        super().__init__("Remote daemon credential is unavailable.")


class DaemonRemoteCredential:
    """One fixed-size secret whose representation never exposes its bytes."""

    __slots__ = ("__key",)

    def __init__(self, key: bytes) -> None:
        if type(key) is not bytes:
            raise TypeError("Remote daemon credential key must be bytes.")
        if len(key) != DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES:
            raise ValueError(
                "Remote daemon credential key must contain exactly "
                f"{DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES} bytes."
            )
        self.__key = key

    def __repr__(self) -> str:
        return "DaemonRemoteCredential(<redacted>)"

    def proof(
        self,
        challenge: DaemonRemoteChallenge,
        *,
        client_id: str,
        client_nonce: str,
    ) -> str:
        """Return one transcript-bound HMAC proof."""

        transcript = _authentication_transcript(
            challenge,
            client_id=client_id,
            client_nonce=client_nonce,
        )
        return _encode_value(hmac.new(self.__key, transcript, hashlib.sha256).digest())


@dataclass(frozen=True, slots=True)
class DaemonRemoteChallenge:
    """One fresh server challenge for a single authentication attempt."""

    server_nonce: str
    protocol: str = DAEMON_REMOTE_AUTH_PROTOCOL
    version: int = DAEMON_REMOTE_AUTH_VERSION
    algorithm: str = DAEMON_REMOTE_AUTH_ALGORITHM

    def __post_init__(self) -> None:
        if self.protocol != DAEMON_REMOTE_AUTH_PROTOCOL:
            raise ValueError("Remote daemon authentication protocol is unsupported.")
        if self.version != DAEMON_REMOTE_AUTH_VERSION:
            raise ValueError("Remote daemon authentication version is unsupported.")
        if self.algorithm != DAEMON_REMOTE_AUTH_ALGORITHM:
            raise ValueError("Remote daemon authentication algorithm is unsupported.")
        _decode_value(
            self.server_nonce,
            expected_bytes=DAEMON_REMOTE_AUTH_NONCE_BYTES,
        )

    def to_json_line(self) -> bytes:
        return _encode_frame(
            {
                "algorithm": self.algorithm,
                "protocol": self.protocol,
                "server_nonce": self.server_nonce,
                "version": self.version,
            }
        )

    @classmethod
    def from_json_line(cls, data: bytes | str) -> DaemonRemoteChallenge:
        payload = _decode_frame(data)
        _require_exact_fields(
            payload,
            {"algorithm", "protocol", "server_nonce", "version"},
        )
        protocol = payload["protocol"]
        if protocol != DAEMON_REMOTE_AUTH_PROTOCOL:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_PROTOCOL
            )
        version = payload["version"]
        if version != DAEMON_REMOTE_AUTH_VERSION:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_VERSION
            )
        if payload["algorithm"] != DAEMON_REMOTE_AUTH_ALGORITHM:
            raise _invalid_frame()
        nonce = payload["server_nonce"]
        if type(nonce) is not str:
            raise _invalid_frame()
        try:
            return cls(
                server_nonce=nonce,
                protocol=protocol,
                version=version,
                algorithm=payload["algorithm"],
            )
        except (TypeError, ValueError) as error:
            raise _invalid_frame() from error


@dataclass(frozen=True, slots=True)
class DaemonRemoteAuthenticationRequest:
    """One client identity and proof response to an exact challenge."""

    client_id: str
    client_nonce: str
    proof: str = field(repr=False)
    protocol: str = DAEMON_REMOTE_AUTH_PROTOCOL
    version: int = DAEMON_REMOTE_AUTH_VERSION

    def __post_init__(self) -> None:
        _validate_client_id(self.client_id)
        _decode_value(
            self.client_nonce,
            expected_bytes=DAEMON_REMOTE_AUTH_NONCE_BYTES,
        )
        _decode_value(
            self.proof,
            expected_bytes=DAEMON_REMOTE_AUTH_PROOF_BYTES,
        )
        if self.protocol != DAEMON_REMOTE_AUTH_PROTOCOL:
            raise ValueError("Remote daemon authentication protocol is unsupported.")
        if self.version != DAEMON_REMOTE_AUTH_VERSION:
            raise ValueError("Remote daemon authentication version is unsupported.")

    def to_json_line(self) -> bytes:
        return _encode_frame(
            {
                "client_id": self.client_id,
                "client_nonce": self.client_nonce,
                "proof": self.proof,
                "protocol": self.protocol,
                "version": self.version,
            }
        )

    @classmethod
    def from_json_line(
        cls,
        data: bytes | str,
    ) -> DaemonRemoteAuthenticationRequest:
        payload = _decode_frame(data)
        _require_exact_fields(
            payload,
            {"client_id", "client_nonce", "proof", "protocol", "version"},
        )
        protocol = payload["protocol"]
        if protocol != DAEMON_REMOTE_AUTH_PROTOCOL:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_PROTOCOL
            )
        version = payload["version"]
        if version != DAEMON_REMOTE_AUTH_VERSION:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_VERSION
            )
        client_id = payload["client_id"]
        client_nonce = payload["client_nonce"]
        proof = payload["proof"]
        if not isinstance(client_id, str):
            raise _invalid_frame()
        if not isinstance(client_nonce, str):
            raise _invalid_frame()
        if not isinstance(proof, str):
            raise _invalid_frame()
        try:
            return cls(
                client_id=client_id,
                client_nonce=client_nonce,
                proof=proof,
                protocol=protocol,
                version=version,
            )
        except (TypeError, ValueError) as error:
            raise _invalid_frame() from error


@dataclass(frozen=True, slots=True)
class DaemonRemoteAuthenticatedIdentity:
    """Non-secret identity and scopes resulting from successful proof."""

    client_id: str
    scopes: tuple[DaemonRemoteAuthorizationScope, ...]

    def __post_init__(self) -> None:
        _validate_client_id(self.client_id)
        if type(self.scopes) is not tuple or not self.scopes:
            raise TypeError("Authenticated remote daemon scopes must be a non-empty tuple.")
        if any(not isinstance(scope, DaemonRemoteAuthorizationScope) for scope in self.scopes):
            raise TypeError("Authenticated remote daemon scopes contain an unsupported value.")

    def allows(self, scope: DaemonRemoteAuthorizationScope) -> bool:
        if not isinstance(scope, DaemonRemoteAuthorizationScope):
            raise TypeError(
                "Remote daemon authorization check requires DaemonRemoteAuthorizationScope."
            )
        return scope in self.scopes


@dataclass(frozen=True, slots=True)
class DaemonRemoteAuthenticationResult:
    """One strict server result concluding the authentication exchange."""

    ok: bool
    scopes: tuple[DaemonRemoteAuthorizationScope, ...] = ()
    error: DaemonRemoteAuthenticationErrorReason | None = None
    protocol: str = DAEMON_REMOTE_AUTH_PROTOCOL
    version: int = DAEMON_REMOTE_AUTH_VERSION

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise TypeError("Remote daemon authentication result status must be a boolean.")
        if self.protocol != DAEMON_REMOTE_AUTH_PROTOCOL:
            raise ValueError("Remote daemon authentication protocol is unsupported.")
        if self.version != DAEMON_REMOTE_AUTH_VERSION:
            raise ValueError("Remote daemon authentication version is unsupported.")
        if type(self.scopes) is not tuple:
            raise TypeError("Remote daemon authentication result scopes must be a tuple.")
        if any(not isinstance(scope, DaemonRemoteAuthorizationScope) for scope in self.scopes):
            raise TypeError("Remote daemon authentication result scopes are invalid.")
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("Remote daemon authentication result scopes must be unique.")
        if self.ok:
            if not self.scopes or DaemonRemoteAuthorizationScope.OBSERVE not in self.scopes:
                raise ValueError(
                    "Successful remote daemon authentication must include observe scope."
                )
            if self.error is not None:
                raise ValueError("Successful remote daemon authentication must not contain error.")
        elif self.scopes:
            raise ValueError("Failed remote daemon authentication must not contain scopes.")
        elif not isinstance(self.error, DaemonRemoteAuthenticationErrorReason):
            raise TypeError(
                "Failed remote daemon authentication requires an authentication error reason."
            )
        object.__setattr__(
            self,
            "scopes",
            tuple(sorted(self.scopes, key=lambda scope: scope.value)),
        )

    @classmethod
    def success(
        cls,
        identity: DaemonRemoteAuthenticatedIdentity,
    ) -> DaemonRemoteAuthenticationResult:
        if not isinstance(identity, DaemonRemoteAuthenticatedIdentity):
            raise TypeError(
                "Remote daemon authentication success requires authenticated identity."
            )
        return cls(ok=True, scopes=identity.scopes)

    @classmethod
    def failure(
        cls,
        reason: DaemonRemoteAuthenticationErrorReason,
    ) -> DaemonRemoteAuthenticationResult:
        if not isinstance(reason, DaemonRemoteAuthenticationErrorReason):
            raise TypeError(
                "Remote daemon authentication failure requires an authentication error reason."
            )
        return cls(ok=False, error=reason)

    def to_json_line(self) -> bytes:
        payload: dict[str, object] = {
            "ok": self.ok,
            "protocol": self.protocol,
            "version": self.version,
        }
        if self.ok:
            payload["scopes"] = [scope.value for scope in self.scopes]
        else:
            assert self.error is not None
            payload["error"] = {
                "code": self.error.value,
                "message": _AUTHENTICATION_ERROR_MESSAGES[self.error],
            }
        return _encode_frame(payload)

    @classmethod
    def from_json_line(cls, data: bytes | str) -> DaemonRemoteAuthenticationResult:
        payload = _decode_frame(data)
        protocol = payload.get("protocol")
        if protocol != DAEMON_REMOTE_AUTH_PROTOCOL:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_PROTOCOL
            )
        version = payload.get("version")
        if version != DAEMON_REMOTE_AUTH_VERSION:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.UNSUPPORTED_VERSION
            )
        ok = payload.get("ok")
        if type(ok) is not bool:
            raise _invalid_frame()
        try:
            if ok:
                _require_exact_fields(payload, {"ok", "protocol", "scopes", "version"})
                raw_scopes = payload["scopes"]
                if not isinstance(raw_scopes, list):
                    raise _invalid_frame()
                scopes = tuple(DaemonRemoteAuthorizationScope(scope) for scope in raw_scopes)
                return cls(ok=True, scopes=scopes, protocol=protocol, version=version)

            _require_exact_fields(payload, {"error", "ok", "protocol", "version"})
            raw_error = payload["error"]
            if not isinstance(raw_error, Mapping):
                raise _invalid_frame()
            _require_exact_fields(raw_error, {"code", "message"})
            reason = DaemonRemoteAuthenticationErrorReason(raw_error["code"])
            if raw_error["message"] != _AUTHENTICATION_ERROR_MESSAGES[reason]:
                raise _invalid_frame()
            return cls(ok=False, error=reason, protocol=protocol, version=version)
        except DaemonRemoteAuthenticationError:
            raise
        except (TypeError, ValueError) as error:
            raise _invalid_frame() from error


@dataclass(frozen=True, slots=True, repr=False)
class _CredentialBinding:
    identity: DaemonRemoteClientIdentity
    credential: DaemonRemoteCredential


class DaemonRemoteCredentialRegistry:
    """Immutable active credentials used for uniform constant-time verification."""

    def __init__(self, bindings: tuple[_CredentialBinding, ...]) -> None:
        if type(bindings) is not tuple or not bindings:
            raise ValueError("Remote daemon credential registry requires active credentials.")
        if any(not isinstance(binding, _CredentialBinding) for binding in bindings):
            raise TypeError("Remote daemon credential registry bindings are invalid.")
        client_ids = tuple(binding.identity.client_id for binding in bindings)
        if len(set(client_ids)) != len(client_ids):
            raise ValueError("Remote daemon credential registry client IDs must be unique.")
        self._bindings = tuple(sorted(bindings, key=lambda binding: binding.identity.client_id))

    def __repr__(self) -> str:
        return f"DaemonRemoteCredentialRegistry(active_credentials={len(self._bindings)})"

    @property
    def active_credentials(self) -> int:
        return len(self._bindings)

    def authenticate(
        self,
        challenge: DaemonRemoteChallenge,
        request: DaemonRemoteAuthenticationRequest,
    ) -> DaemonRemoteAuthenticatedIdentity:
        if not isinstance(challenge, DaemonRemoteChallenge):
            raise TypeError("Remote daemon authentication requires DaemonRemoteChallenge.")
        if not isinstance(request, DaemonRemoteAuthenticationRequest):
            raise TypeError(
                "Remote daemon authentication requires DaemonRemoteAuthenticationRequest."
            )

        selected: DaemonRemoteClientIdentity | None = None
        for binding in self._bindings:
            expected = binding.credential.proof(
                challenge,
                client_id=request.client_id,
                client_nonce=request.client_nonce,
            )
            identifier_matches = hmac.compare_digest(
                binding.identity.client_id,
                request.client_id,
            )
            proof_matches = hmac.compare_digest(expected, request.proof)
            if identifier_matches and proof_matches:
                selected = binding.identity

        if selected is None:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED
            )
        return DaemonRemoteAuthenticatedIdentity(
            client_id=selected.client_id,
            scopes=selected.scopes,
        )


class DaemonRemoteAuthenticationSession:
    """One fresh challenge that consumes exactly one client attempt."""

    def __init__(
        self,
        registry: DaemonRemoteCredentialRegistry,
        *,
        nonce_factory: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        if not isinstance(registry, DaemonRemoteCredentialRegistry):
            raise TypeError("Remote daemon authentication session requires a credential registry.")
        self.registry = registry
        self.challenge = create_daemon_remote_challenge(nonce_factory=nonce_factory)
        self._lock = threading.Lock()
        self._consumed = False

    def authenticate(
        self,
        data: bytes | str,
    ) -> DaemonRemoteAuthenticatedIdentity:
        with self._lock:
            if self._consumed:
                raise DaemonRemoteAuthenticationError(
                    DaemonRemoteAuthenticationErrorReason.AUTHENTICATION_FAILED
                )
            self._consumed = True
        request = DaemonRemoteAuthenticationRequest.from_json_line(data)
        return self.registry.authenticate(self.challenge, request)


def create_daemon_remote_challenge(
    *,
    nonce_factory: Callable[[int], bytes] = secrets.token_bytes,
) -> DaemonRemoteChallenge:
    """Create one challenge from exactly 32 cryptographically random bytes."""

    if not callable(nonce_factory):
        raise TypeError("Remote daemon nonce factory must be callable.")
    nonce = nonce_factory(DAEMON_REMOTE_AUTH_NONCE_BYTES)
    if type(nonce) is not bytes or len(nonce) != DAEMON_REMOTE_AUTH_NONCE_BYTES:
        raise ValueError(
            "Remote daemon nonce factory must return exactly "
            f"{DAEMON_REMOTE_AUTH_NONCE_BYTES} bytes."
        )
    return DaemonRemoteChallenge(server_nonce=_encode_value(nonce))


def build_daemon_remote_authentication_request(
    challenge: DaemonRemoteChallenge,
    *,
    client_id: str,
    credential: DaemonRemoteCredential,
    nonce_factory: Callable[[int], bytes] = secrets.token_bytes,
) -> DaemonRemoteAuthenticationRequest:
    """Build one client proof for an exact server challenge."""

    if not isinstance(challenge, DaemonRemoteChallenge):
        raise TypeError("Remote daemon proof construction requires DaemonRemoteChallenge.")
    _validate_client_id(client_id)
    if not isinstance(credential, DaemonRemoteCredential):
        raise TypeError("Remote daemon proof construction requires DaemonRemoteCredential.")
    if not callable(nonce_factory):
        raise TypeError("Remote daemon nonce factory must be callable.")
    nonce = nonce_factory(DAEMON_REMOTE_AUTH_NONCE_BYTES)
    if type(nonce) is not bytes or len(nonce) != DAEMON_REMOTE_AUTH_NONCE_BYTES:
        raise ValueError(
            "Remote daemon nonce factory must return exactly "
            f"{DAEMON_REMOTE_AUTH_NONCE_BYTES} bytes."
        )
    client_nonce = _encode_value(nonce)
    return DaemonRemoteAuthenticationRequest(
        client_id=client_id,
        client_nonce=client_nonce,
        proof=credential.proof(
            challenge,
            client_id=client_id,
            client_nonce=client_nonce,
        ),
    )


def load_daemon_remote_credential(path: Path) -> DaemonRemoteCredential:
    """Load one private credential with descriptor and path identity checks."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise DaemonRemoteCredentialError()

    descriptor: int | None = None
    try:
        initial_path = path.lstat()
        _require_private_credential_metadata(initial_path)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _require_private_credential_metadata(opened)
        if _file_identity(initial_path) != _file_identity(opened):
            raise DaemonRemoteCredentialError()

        raw = bytearray()
        while len(raw) <= DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(
                    512,
                    DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES + 1 - len(raw),
                ),
            )
            if not chunk:
                break
            raw.extend(chunk)

        after_read = os.fstat(descriptor)
        final_path = path.lstat()
        _require_private_credential_metadata(final_path)
        if _file_snapshot(opened) != _file_snapshot(after_read) or _file_snapshot(
            after_read
        ) != _file_snapshot(final_path):
            raise DaemonRemoteCredentialError()
        return _credential_from_file_bytes(bytes(raw))
    except DaemonRemoteCredentialError:
        raise
    except (OSError, ValueError) as error:
        raise DaemonRemoteCredentialError() from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def load_daemon_remote_credential_registry(
    configuration: DaemonRemoteListenerConfiguration,
    *,
    credential_loader: Callable[[Path], DaemonRemoteCredential] = (load_daemon_remote_credential),
) -> DaemonRemoteCredentialRegistry:
    """Load every active identity once so peer failures cannot enumerate files."""

    if not isinstance(configuration, DaemonRemoteListenerConfiguration):
        raise TypeError(
            "Remote daemon credential loading requires DaemonRemoteListenerConfiguration."
        )
    if not configuration.enabled:
        raise ValueError("Remote daemon credential loading requires an enabled configuration.")
    if not callable(credential_loader):
        raise TypeError("Remote daemon credential loader must be callable.")
    bindings = tuple(
        _CredentialBinding(client, credential_loader(client.credential_file))
        for client in configuration.clients
        if not client.revoked
    )
    return DaemonRemoteCredentialRegistry(bindings)


def _credential_from_file_bytes(raw: bytes) -> DaemonRemoteCredential:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if len(raw) != _ENCODED_32_BYTE_VALUE_LENGTH or b"\n" in raw or b"\r" in raw:
        raise DaemonRemoteCredentialError()
    try:
        encoded = raw.decode("ascii")
        key = _decode_value(
            encoded,
            expected_bytes=DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES,
        )
    except (UnicodeError, ValueError) as error:
        raise DaemonRemoteCredentialError() from error
    return DaemonRemoteCredential(key)


def _require_private_credential_metadata(observed: os.stat_result) -> None:
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise DaemonRemoteCredentialError()
    if not 1 <= observed.st_size <= DAEMON_REMOTE_MAX_CREDENTIAL_FILE_BYTES:
        raise DaemonRemoteCredentialError()
    if os.name == "posix" and stat.S_IMODE(observed.st_mode) != DAEMON_REMOTE_PRIVATE_FILE_MODE:
        raise DaemonRemoteCredentialError()


def _file_identity(observed: os.stat_result) -> tuple[int, int, int]:
    return stat.S_IFMT(observed.st_mode), observed.st_dev, observed.st_ino


def _file_snapshot(observed: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        *_file_identity(observed),
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _authentication_transcript(
    challenge: DaemonRemoteChallenge,
    *,
    client_id: str,
    client_nonce: str,
) -> bytes:
    _validate_client_id(client_id)
    _decode_value(
        client_nonce,
        expected_bytes=DAEMON_REMOTE_AUTH_NONCE_BYTES,
    )
    return json.dumps(
        {
            "algorithm": challenge.algorithm,
            "client_id": client_id,
            "client_nonce": client_nonce,
            "protocol": challenge.protocol,
            "server_nonce": challenge.server_nonce,
            "version": challenge.version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _validate_client_id(value: object) -> str:
    if type(value) is not str or not value or len(value) > 64:
        raise ValueError("Remote daemon client ID is invalid.")
    if (
        value.strip() != value
        or not value.isascii()
        or not value[0].isalnum()
        or any(not (character.isalnum() or character in "._-") for character in value)
    ):
        raise ValueError("Remote daemon client ID is invalid.")
    return value


def _encode_value(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_value(value: object, *, expected_bytes: int) -> bytes:
    if (
        type(value) is not str
        or len(value) != _ENCODED_32_BYTE_VALUE_LENGTH
        or any(character not in _URLSAFE_ALPHABET for character in value)
    ):
        raise ValueError("Remote daemon authentication value is invalid.")
    try:
        decoded = base64.b64decode(
            value + "=",
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as error:
        raise ValueError("Remote daemon authentication value is invalid.") from error
    if len(decoded) != expected_bytes:
        raise ValueError("Remote daemon authentication value is invalid.")
    return decoded


def _encode_frame(payload: Mapping[str, object]) -> bytes:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    if len(encoded) > DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES:
        raise ValueError("Remote daemon authentication frame is too large.")
    return encoded


def _decode_frame(data: bytes | str) -> Mapping[str, object]:
    if isinstance(data, str):
        try:
            encoded = data.encode("utf-8")
        except UnicodeError as error:
            raise _invalid_frame() from error
    elif isinstance(data, bytes):
        encoded = data
    else:
        raise _invalid_frame()
    if not encoded or len(encoded) > DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES:
        raise _invalid_frame()
    if encoded.endswith(b"\n"):
        encoded = encoded[:-1]
    if not encoded or b"\n" in encoded or b"\r" in encoded:
        raise _invalid_frame()
    try:
        payload = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid_frame() from error
    if not isinstance(payload, Mapping) or any(
        type(field_name) is not str for field_name in payload
    ):
        raise _invalid_frame()
    return payload


def _require_exact_fields(
    payload: Mapping[str, object],
    expected: set[str],
) -> None:
    if set(payload) != expected:
        raise _invalid_frame()


def _invalid_frame() -> DaemonRemoteAuthenticationError:
    return DaemonRemoteAuthenticationError(DaemonRemoteAuthenticationErrorReason.INVALID_FRAME)


__all__ = [
    "DAEMON_REMOTE_AUTH_ALGORITHM",
    "DAEMON_REMOTE_AUTH_CREDENTIAL_BYTES",
    "DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES",
    "DAEMON_REMOTE_AUTH_NONCE_BYTES",
    "DAEMON_REMOTE_AUTH_PROOF_BYTES",
    "DAEMON_REMOTE_AUTH_PROTOCOL",
    "DAEMON_REMOTE_AUTH_VERSION",
    "DaemonRemoteAuthenticatedIdentity",
    "DaemonRemoteAuthenticationError",
    "DaemonRemoteAuthenticationErrorReason",
    "DaemonRemoteAuthenticationRequest",
    "DaemonRemoteAuthenticationResult",
    "DaemonRemoteAuthenticationSession",
    "DaemonRemoteChallenge",
    "DaemonRemoteCredential",
    "DaemonRemoteCredentialError",
    "DaemonRemoteCredentialRegistry",
    "build_daemon_remote_authentication_request",
    "create_daemon_remote_challenge",
    "load_daemon_remote_credential",
    "load_daemon_remote_credential_registry",
]
