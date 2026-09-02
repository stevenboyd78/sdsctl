"""Direct-TLS admission for authenticated remote daemon listeners.

This module does not bind or open a listening socket.  It turns one already
accepted stream into a TLS 1.3 stream and returns it only after the versioned
challenge/proof exchange succeeds.
"""

from __future__ import annotations

import os
import socket as socket_module
import ssl
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from pathlib import Path
from time import monotonic

from .daemon_remote import (
    DaemonRemoteAuthorizationScope,
    DaemonRemoteListenerConfiguration,
    preflight_daemon_remote_configuration,
)
from .daemon_remote_auth import (
    DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES,
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteAuthenticationError,
    DaemonRemoteAuthenticationErrorReason,
    DaemonRemoteAuthenticationResult,
    DaemonRemoteAuthenticationSession,
    DaemonRemoteCredentialError,
    DaemonRemoteCredentialRegistry,
)
from .daemon_remote_credentials import (
    DaemonRemoteCredentialAuthority,
    DaemonRemoteCredentialGeneration,
    DaemonRemoteCredentialLifecycleSnapshot,
    DaemonRemoteCredentialSession,
    DaemonRemoteCredentialSessionExpired,
)
from .exceptions import ConfigurationError

DAEMON_REMOTE_TLS_VERSION = "TLSv1.3"
DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT = 5.0


class DaemonRemoteTlsErrorReason(StrEnum):
    """Stable redacted direct-TLS admission failure classes."""

    CONFIGURATION_FAILED = "configuration_failed"
    TLS_HANDSHAKE_FAILED = "tls_handshake_failed"
    AUTHENTICATION_FAILED = "authentication_failed"
    TRANSPORT_FAILED = "transport_failed"


_TLS_ERROR_MESSAGES = {
    DaemonRemoteTlsErrorReason.CONFIGURATION_FAILED: (
        "Remote daemon TLS configuration is unavailable."
    ),
    DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED: (
        "Remote daemon TLS handshake failed."
    ),
    DaemonRemoteTlsErrorReason.AUTHENTICATION_FAILED: (
        "Remote daemon TLS authentication failed."
    ),
    DaemonRemoteTlsErrorReason.TRANSPORT_FAILED: (
        "Remote daemon TLS transport failed."
    ),
}


class DaemonRemoteTlsError(RuntimeError):
    """Report one admission failure without endpoint, peer, or secret detail."""

    def __init__(self, reason: DaemonRemoteTlsErrorReason) -> None:
        if not isinstance(reason, DaemonRemoteTlsErrorReason):
            raise TypeError("Remote daemon TLS error reason must be DaemonRemoteTlsErrorReason.")
        self.reason = reason
        super().__init__(_TLS_ERROR_MESSAGES[reason])


@dataclass(frozen=True, slots=True)
class DaemonRemoteAuthenticatedPeer:
    """Opaque non-secret peer metadata returned only after TLS authentication."""

    identity: DaemonRemoteAuthenticatedIdentity
    tls_version: str = DAEMON_REMOTE_TLS_VERSION
    credential_session: DaemonRemoteCredentialSession | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.identity, DaemonRemoteAuthenticatedIdentity):
            raise TypeError("Remote daemon TLS peer requires authenticated identity.")
        if self.tls_version != DAEMON_REMOTE_TLS_VERSION:
            raise ValueError("Remote daemon TLS peer version is unsupported.")
        if self.credential_session is not None and not isinstance(
            self.credential_session,
            DaemonRemoteCredentialSession,
        ):
            raise TypeError(
                "Remote daemon TLS peer credential session is invalid."
            )

    @property
    def client_id(self) -> str:
        return self.identity.client_id

    @property
    def scopes(self) -> tuple[DaemonRemoteAuthorizationScope, ...]:
        return self.identity.scopes

    def allows(self, scope: DaemonRemoteAuthorizationScope) -> bool:
        return self.identity.allows(scope)

    @property
    def credentials_current(self) -> bool:
        session = self.credential_session
        return session is None or session.active

    def execute_if_credentials_current(self, action: Callable[[], bytes]) -> bytes:
        """Run one authorized request in the current credential generation."""

        if not callable(action):
            raise TypeError("Remote daemon TLS peer action must be callable.")
        session = self.credential_session
        if session is None:
            return action()
        return session.execute(action)

    def close(self) -> None:
        """Release this peer's credential session; safe to repeat."""

        if self.credential_session is not None:
            self.credential_session.close()


class DaemonRemoteServerTlsAdmission:
    """Authenticate one accepted stream without owning a network listener."""

    def __init__(
        self,
        context: ssl.SSLContext,
        registry: DaemonRemoteCredentialRegistry,
        *,
        handshake_timeout: float = DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT,
        credential_authority: DaemonRemoteCredentialAuthority | None = None,
    ) -> None:
        if not isinstance(context, ssl.SSLContext):
            raise TypeError("Remote daemon TLS admission requires an SSLContext.")
        if not isinstance(registry, DaemonRemoteCredentialRegistry):
            raise TypeError("Remote daemon TLS admission requires a credential registry.")
        if credential_authority is not None and not isinstance(
            credential_authority,
            DaemonRemoteCredentialAuthority,
        ):
            raise TypeError(
                "Remote daemon TLS admission credential authority is invalid."
            )
        if (
            credential_authority is not None
            and credential_authority.current_generation().registry is not registry
        ):
            raise ValueError(
                "Remote daemon TLS admission registry must match its credential authority."
            )
        self.context = context
        self._registry = registry
        self.credential_authority = credential_authority
        self.handshake_timeout = _positive_timeout(handshake_timeout)

    @property
    def registry(self) -> DaemonRemoteCredentialRegistry:
        authority = self.credential_authority
        if authority is None:
            return self._registry
        return authority.current_generation().registry

    def credential_snapshot(self) -> DaemonRemoteCredentialLifecycleSnapshot | None:
        authority = self.credential_authority
        return None if authority is None else authority.snapshot()

    def reload_credentials(
        self,
        configuration: DaemonRemoteListenerConfiguration,
    ) -> DaemonRemoteCredentialLifecycleSnapshot:
        """Atomically replace active credentials and invalidate old sessions."""

        authority = self.credential_authority
        if authority is None:
            raise RuntimeError(
                "Remote daemon TLS admission does not own reloadable credentials."
            )
        return authority.reload(configuration)

    @classmethod
    def from_configuration(
        cls,
        configuration: DaemonRemoteListenerConfiguration,
        *,
        handshake_timeout: float = DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT,
    ) -> DaemonRemoteServerTlsAdmission:
        """Load TLS identity and active credentials from one enabled configuration."""

        if not isinstance(configuration, DaemonRemoteListenerConfiguration):
            raise TypeError(
                "Remote daemon TLS loading requires DaemonRemoteListenerConfiguration."
            )
        if not configuration.enabled:
            raise ValueError("Remote daemon TLS loading requires an enabled configuration.")
        assert configuration.certificate_file is not None
        assert configuration.private_key_file is not None

        certificate = configuration.certificate_file
        private_key = configuration.private_key_file
        try:
            preflight_daemon_remote_configuration(configuration)
            before_certificate = _tls_file_snapshot(certificate, private=False)
            before_private_key = _tls_file_snapshot(private_key, private=True)
            authority = DaemonRemoteCredentialAuthority(configuration)
            registry = authority.current_generation().registry
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
            context.options |= ssl.OP_NO_COMPRESSION
            context.load_cert_chain(
                certfile=os.fspath(certificate),
                keyfile=os.fspath(private_key),
            )
            after_certificate = _tls_file_snapshot(certificate, private=False)
            after_private_key = _tls_file_snapshot(private_key, private=True)
            if (
                before_certificate != after_certificate
                or before_private_key != after_private_key
            ):
                raise DaemonRemoteTlsError(
                    DaemonRemoteTlsErrorReason.CONFIGURATION_FAILED
                )
        except DaemonRemoteTlsError:
            raise
        except (ConfigurationError, DaemonRemoteCredentialError, OSError, ssl.SSLError) as error:
            raise DaemonRemoteTlsError(
                DaemonRemoteTlsErrorReason.CONFIGURATION_FAILED
            ) from error

        return cls(
            context,
            registry,
            handshake_timeout=handshake_timeout,
            credential_authority=authority,
        )

    def admit(
        self,
        stream: socket_module.socket,
    ) -> tuple[ssl.SSLSocket, DaemonRemoteAuthenticatedPeer]:
        """Return one TLS stream only after successful single-use authentication."""

        if not isinstance(stream, socket_module.socket) or isinstance(stream, ssl.SSLSocket):
            raise TypeError("Remote daemon TLS admission requires one unwrapped socket.")

        secured: ssl.SSLSocket | None = None
        credential_session: DaemonRemoteCredentialSession | None = None
        tls_established = False
        admitted = False
        deadline = monotonic() + self.handshake_timeout
        authority = self.credential_authority
        generation: DaemonRemoteCredentialGeneration | None = None
        if authority is not None:
            generation = authority.current_generation()
            registry = generation.registry
        else:
            registry = self._registry
        try:
            secured = self.context.wrap_socket(
                stream,
                server_side=True,
                do_handshake_on_connect=False,
            )
            secured.settimeout(_remaining_admission_seconds(deadline))
            secured.do_handshake()
            tls_established = True
            tls_version = secured.version()
            if tls_version != DAEMON_REMOTE_TLS_VERSION:
                raise DaemonRemoteTlsError(
                    DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED
                )

            session = DaemonRemoteAuthenticationSession(registry)
            secured.settimeout(_remaining_admission_seconds(deadline))
            secured.sendall(session.challenge.to_json_line())
            request = _receive_authentication_frame(secured, deadline=deadline)
            identity = session.authenticate(request)
            if authority is not None:
                assert generation is not None
                secured_for_invalidation = secured
                try:
                    credential_session = authority.register_session(
                        generation,
                        identity,
                        invalidator=lambda: _close_stream(
                            secured_for_invalidation
                        ),
                    )
                except DaemonRemoteCredentialSessionExpired as error:
                    raise DaemonRemoteTlsError(
                        DaemonRemoteTlsErrorReason.AUTHENTICATION_FAILED
                    ) from error
            secured.settimeout(_remaining_admission_seconds(deadline))
            secured.sendall(
                DaemonRemoteAuthenticationResult.success(identity).to_json_line()
            )
            admitted = True
            return secured, DaemonRemoteAuthenticatedPeer(
                identity=identity,
                tls_version=tls_version,
                credential_session=credential_session,
            )
        except DaemonRemoteAuthenticationError as error:
            if tls_established and secured is not None:
                _send_authentication_failure(secured, error)
            raise DaemonRemoteTlsError(
                DaemonRemoteTlsErrorReason.AUTHENTICATION_FAILED
            ) from error
        except DaemonRemoteTlsError:
            raise
        except TimeoutError as error:
            reason = (
                DaemonRemoteTlsErrorReason.AUTHENTICATION_FAILED
                if tls_established
                else DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED
            )
            raise DaemonRemoteTlsError(reason) from error
        except ssl.SSLError as error:
            reason = (
                DaemonRemoteTlsErrorReason.TRANSPORT_FAILED
                if tls_established
                else DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED
            )
            raise DaemonRemoteTlsError(reason) from error
        except OSError as error:
            reason = (
                DaemonRemoteTlsErrorReason.TRANSPORT_FAILED
                if tls_established
                else DaemonRemoteTlsErrorReason.TLS_HANDSHAKE_FAILED
            )
            raise DaemonRemoteTlsError(reason) from error
        finally:
            if not admitted:
                if credential_session is not None:
                    credential_session.close()
                _close_stream(secured if secured is not None else stream)


def _receive_authentication_frame(
    stream: ssl.SSLSocket,
    *,
    deadline: float | None = None,
) -> bytes:
    frame = bytearray()
    while len(frame) <= DAEMON_REMOTE_AUTH_MAX_FRAME_BYTES:
        if deadline is not None:
            stream.settimeout(_remaining_admission_seconds(deadline))
        chunk = stream.recv(1)
        if not chunk:
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.INVALID_FRAME
            )
        if chunk == b"\n":
            if not frame:
                raise DaemonRemoteAuthenticationError(
                    DaemonRemoteAuthenticationErrorReason.INVALID_FRAME
                )
            return bytes(frame)
        if chunk == b"\r":
            raise DaemonRemoteAuthenticationError(
                DaemonRemoteAuthenticationErrorReason.INVALID_FRAME
            )
        frame.extend(chunk)
    raise DaemonRemoteAuthenticationError(
        DaemonRemoteAuthenticationErrorReason.INVALID_FRAME
    )


def _send_authentication_failure(
    stream: ssl.SSLSocket,
    error: DaemonRemoteAuthenticationError,
) -> None:
    with suppress(OSError):
        stream.sendall(DaemonRemoteAuthenticationResult.failure(error.reason).to_json_line())


def _tls_file_snapshot(path: Path, *, private: bool) -> tuple[int, int, int, int, int, int]:
    observed = path.lstat()
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise DaemonRemoteTlsError(DaemonRemoteTlsErrorReason.CONFIGURATION_FAILED)
    if private and os.name == "posix" and stat.S_IMODE(observed.st_mode) != 0o600:
        raise DaemonRemoteTlsError(DaemonRemoteTlsErrorReason.CONFIGURATION_FAILED)
    return (
        stat.S_IFMT(observed.st_mode),
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _positive_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Remote daemon TLS handshake timeout must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            "Remote daemon TLS handshake timeout must be finite and greater than zero."
        )
    return normalized


def _remaining_admission_seconds(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Remote daemon TLS admission deadline expired.")
    return remaining


def _close_stream(stream: socket_module.socket) -> None:
    with suppress(OSError):
        stream.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        stream.close()


__all__ = [
    "DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT",
    "DAEMON_REMOTE_TLS_VERSION",
    "DaemonRemoteAuthenticatedPeer",
    "DaemonRemoteServerTlsAdmission",
    "DaemonRemoteTlsError",
    "DaemonRemoteTlsErrorReason",
]
