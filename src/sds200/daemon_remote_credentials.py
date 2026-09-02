"""Atomic runtime lifecycle for authenticated remote daemon credentials.

Credential files remain operator-owned.  This module loads a complete
replacement registry before committing it, swaps generations atomically, and
invalidates every session authenticated against the preceding generation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from .daemon_remote import (
    DaemonRemoteAuthorizationScope,
    DaemonRemoteListenerConfiguration,
)
from .daemon_remote_auth import (
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteCredential,
    DaemonRemoteCredentialRegistry,
    load_daemon_remote_credential,
    load_daemon_remote_credential_registry,
)

_Result = TypeVar("_Result")
_CredentialLoader = Callable[[Path], DaemonRemoteCredential]
_Invalidator = Callable[[], None]


class DaemonRemoteCredentialReloadErrorReason(StrEnum):
    """Stable, non-secret credential reload failure classes."""

    CONFIGURATION_MISMATCH = "configuration_mismatch"
    LOAD_FAILED = "load_failed"


_RELOAD_ERROR_MESSAGES = {
    DaemonRemoteCredentialReloadErrorReason.CONFIGURATION_MISMATCH: (
        "Remote daemon credential reload does not match the active listener."
    ),
    DaemonRemoteCredentialReloadErrorReason.LOAD_FAILED: (
        "Remote daemon credential reload could not be loaded."
    ),
}


class DaemonRemoteCredentialReloadError(RuntimeError):
    """Report reload failure without a client ID, path, or secret."""

    def __init__(self, reason: DaemonRemoteCredentialReloadErrorReason) -> None:
        if not isinstance(reason, DaemonRemoteCredentialReloadErrorReason):
            raise TypeError(
                "Remote daemon credential reload error reason must be "
                "DaemonRemoteCredentialReloadErrorReason."
            )
        self.reason = reason
        super().__init__(_RELOAD_ERROR_MESSAGES[reason])


class DaemonRemoteCredentialSessionExpired(RuntimeError):
    """Signal that an authenticated connection belongs to an old generation."""

    def __init__(self) -> None:
        super().__init__("Remote daemon credential session is no longer current.")


@dataclass(frozen=True, slots=True, repr=False)
class DaemonRemoteCredentialGeneration:
    """One immutable registry selected atomically for a TLS admission attempt."""

    generation: int
    registry: DaemonRemoteCredentialRegistry = field(repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError("Remote daemon credential generation must be an integer.")
        if self.generation <= 0:
            raise ValueError(
                "Remote daemon credential generation must be greater than zero."
            )
        if not isinstance(self.registry, DaemonRemoteCredentialRegistry):
            raise TypeError(
                "Remote daemon credential generation requires a credential registry."
            )


@dataclass(frozen=True, slots=True)
class DaemonRemoteCredentialLifecycleSnapshot:
    """Redacted credential generation, reload, and session diagnostics."""

    generation: int
    configured_clients: int
    active_clients: int
    revoked_clients: int
    control_clients: int
    active_sessions: int
    successful_reloads: int
    failed_reloads: int
    invalidated_sessions: int
    invalidation_failures: int
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "generation": self.generation,
            "configured_clients": self.configured_clients,
            "active_clients": self.active_clients,
            "revoked_clients": self.revoked_clients,
            "control_clients": self.control_clients,
            "active_sessions": self.active_sessions,
            "successful_reloads": self.successful_reloads,
            "failed_reloads": self.failed_reloads,
            "invalidated_sessions": self.invalidated_sessions,
            "invalidation_failures": self.invalidation_failures,
            "last_error": self.last_error,
        }


@dataclass(slots=True, repr=False)
class _SessionRecord:
    generation: int
    invalidators: dict[int, _Invalidator] = field(repr=False)
    next_invalidator_token: int = 2


class DaemonRemoteCredentialSession:
    """Revocable lease for one successfully authenticated TLS connection."""

    __slots__ = ("_authority", "_generation", "_token")

    def __init__(
        self,
        authority: DaemonRemoteCredentialAuthority,
        *,
        token: int,
        generation: int,
    ) -> None:
        if not isinstance(authority, DaemonRemoteCredentialAuthority):
            raise TypeError(
                "Remote daemon credential sessions require a credential authority."
            )
        if isinstance(token, bool) or not isinstance(token, int):
            raise TypeError("Remote daemon credential session token must be an integer.")
        if token <= 0:
            raise ValueError(
                "Remote daemon credential session token must be greater than zero."
            )
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError(
                "Remote daemon credential session generation must be an integer."
            )
        if generation <= 0:
            raise ValueError(
                "Remote daemon credential session generation must be greater than zero."
            )
        self._authority = authority
        self._token = token
        self._generation = generation

    def __repr__(self) -> str:
        return "DaemonRemoteCredentialSession(<redacted>)"

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def active(self) -> bool:
        return self._authority._session_active(
            token=self._token,
            generation=self._generation,
        )

    def execute(self, action: Callable[[], _Result]) -> _Result:
        """Run one request only while this exact generation remains current."""

        return self._authority._execute_session(
            token=self._token,
            generation=self._generation,
            action=action,
        )

    def on_invalidate(self, invalidator: _Invalidator) -> Callable[[], None]:
        """Attach one child lease to this credential generation."""

        return self._authority._register_session_invalidator(
            token=self._token,
            generation=self._generation,
            invalidator=invalidator,
        )

    def close(self) -> None:
        """Release this session from lifecycle tracking; safe to repeat."""

        self._authority._release_session(token=self._token)


class DaemonRemoteCredentialAuthority:
    """Own one atomically replaceable credential registry and its sessions."""

    def __init__(
        self,
        configuration: DaemonRemoteListenerConfiguration,
        *,
        credential_loader: _CredentialLoader = load_daemon_remote_credential,
    ) -> None:
        if not isinstance(configuration, DaemonRemoteListenerConfiguration):
            raise TypeError(
                "Remote daemon credential authority requires "
                "DaemonRemoteListenerConfiguration."
            )
        if not configuration.enabled:
            raise ValueError(
                "Remote daemon credential authority requires an enabled configuration."
            )
        if not callable(credential_loader):
            raise TypeError("Remote daemon credential authority loader must be callable.")

        registry = load_daemon_remote_credential_registry(
            configuration,
            credential_loader=credential_loader,
        )
        self._credential_loader = credential_loader
        self._listener_identity = _listener_identity(configuration)
        self._lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._registry = registry
        self._generation = 1
        self._next_token = 1
        self._sessions: dict[int, _SessionRecord] = {}
        self._successful_reloads = 0
        self._failed_reloads = 0
        self._invalidated_sessions = 0
        self._invalidation_failures = 0
        self._last_error: str | None = None
        self._set_client_counts(configuration)

    def __repr__(self) -> str:
        snapshot = self.snapshot()
        return (
            "DaemonRemoteCredentialAuthority("
            f"generation={snapshot.generation}, "
            f"active_clients={snapshot.active_clients}, "
            f"active_sessions={snapshot.active_sessions})"
        )

    def current_generation(self) -> DaemonRemoteCredentialGeneration:
        """Return one immutable generation/registry pair under the swap lock."""

        with self._lock:
            return DaemonRemoteCredentialGeneration(
                generation=self._generation,
                registry=self._registry,
            )

    def register_session(
        self,
        generation: DaemonRemoteCredentialGeneration,
        identity: DaemonRemoteAuthenticatedIdentity,
        *,
        invalidator: _Invalidator,
    ) -> DaemonRemoteCredentialSession:
        """Register a connection only if authentication used the current registry."""

        if not isinstance(generation, DaemonRemoteCredentialGeneration):
            raise TypeError(
                "Remote daemon session registration requires a credential generation."
            )
        if not isinstance(identity, DaemonRemoteAuthenticatedIdentity):
            raise TypeError(
                "Remote daemon session registration requires an authenticated identity."
            )
        if not callable(invalidator):
            raise TypeError("Remote daemon session invalidator must be callable.")

        with self._lock:
            if (
                generation.generation != self._generation
                or generation.registry is not self._registry
            ):
                raise DaemonRemoteCredentialSessionExpired()
            token = self._next_token
            self._next_token += 1
            self._sessions[token] = _SessionRecord(
                generation=self._generation,
                invalidators={1: invalidator},
            )
        return DaemonRemoteCredentialSession(
            self,
            token=token,
            generation=generation.generation,
        )

    def reload(
        self,
        configuration: DaemonRemoteListenerConfiguration,
    ) -> DaemonRemoteCredentialLifecycleSnapshot:
        """Commit one complete replacement, then invalidate the prior generation."""

        if not isinstance(configuration, DaemonRemoteListenerConfiguration):
            raise TypeError(
                "Remote daemon credential reload requires "
                "DaemonRemoteListenerConfiguration."
            )

        with self._reload_lock:
            if (
                not configuration.enabled
                or _listener_identity(configuration) != self._listener_identity
            ):
                self._record_reload_failure(
                    DaemonRemoteCredentialReloadErrorReason.CONFIGURATION_MISMATCH
                )
                raise DaemonRemoteCredentialReloadError(
                    DaemonRemoteCredentialReloadErrorReason.CONFIGURATION_MISMATCH
                )

            try:
                registry = load_daemon_remote_credential_registry(
                    configuration,
                    credential_loader=self._credential_loader,
                )
            except Exception:
                self._record_reload_failure(
                    DaemonRemoteCredentialReloadErrorReason.LOAD_FAILED
                )
                raise DaemonRemoteCredentialReloadError(
                    DaemonRemoteCredentialReloadErrorReason.LOAD_FAILED
                ) from None

            with self._lock:
                self._registry = registry
                self._generation += 1
                records = tuple(self._sessions.values())
                self._sessions.clear()
                self._successful_reloads += 1
                self._invalidated_sessions += len(records)
                self._last_error = None
                self._set_client_counts(configuration)

            invalidation_failures = 0
            for record in records:
                for invalidator in tuple(record.invalidators.values()):
                    try:
                        invalidator()
                    except Exception:
                        invalidation_failures += 1
            if invalidation_failures:
                with self._lock:
                    self._invalidation_failures += invalidation_failures
            return self.snapshot()

    def snapshot(self) -> DaemonRemoteCredentialLifecycleSnapshot:
        with self._lock:
            return DaemonRemoteCredentialLifecycleSnapshot(
                generation=self._generation,
                configured_clients=self._configured_clients,
                active_clients=self._active_clients,
                revoked_clients=self._revoked_clients,
                control_clients=self._control_clients,
                active_sessions=len(self._sessions),
                successful_reloads=self._successful_reloads,
                failed_reloads=self._failed_reloads,
                invalidated_sessions=self._invalidated_sessions,
                invalidation_failures=self._invalidation_failures,
                last_error=self._last_error,
            )

    def _session_active(self, *, token: int, generation: int) -> bool:
        with self._lock:
            record = self._sessions.get(token)
            return (
                generation == self._generation
                and record is not None
                and record.generation == generation
            )

    def _execute_session(
        self,
        *,
        token: int,
        generation: int,
        action: Callable[[], _Result],
    ) -> _Result:
        if not callable(action):
            raise TypeError("Remote daemon credential session action must be callable.")
        with self._lock:
            record = self._sessions.get(token)
            if (
                generation != self._generation
                or record is None
                or record.generation != generation
            ):
                raise DaemonRemoteCredentialSessionExpired()
            return action()

    def _register_session_invalidator(
        self,
        *,
        token: int,
        generation: int,
        invalidator: _Invalidator,
    ) -> Callable[[], None]:
        if not callable(invalidator):
            raise TypeError(
                "Remote daemon session invalidator must be callable."
            )
        with self._lock:
            record = self._sessions.get(token)
            if (
                generation != self._generation
                or record is None
                or record.generation != generation
            ):
                raise DaemonRemoteCredentialSessionExpired()
            invalidator_token = record.next_invalidator_token
            record.next_invalidator_token += 1
            record.invalidators[invalidator_token] = invalidator

        def unsubscribe() -> None:
            with self._lock:
                current = self._sessions.get(token)
                if current is record:
                    current.invalidators.pop(invalidator_token, None)

        return unsubscribe

    def _release_session(self, *, token: int) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def _record_reload_failure(
        self,
        reason: DaemonRemoteCredentialReloadErrorReason,
    ) -> None:
        with self._lock:
            self._failed_reloads += 1
            self._last_error = reason.value

    def _set_client_counts(
        self,
        configuration: DaemonRemoteListenerConfiguration,
    ) -> None:
        active = tuple(client for client in configuration.clients if not client.revoked)
        self._configured_clients = len(configuration.clients)
        self._active_clients = len(active)
        self._revoked_clients = len(configuration.clients) - len(active)
        self._control_clients = sum(
            DaemonRemoteAuthorizationScope.CONTROL in client.scopes
            for client in active
        )


def _listener_identity(
    configuration: DaemonRemoteListenerConfiguration,
) -> tuple[object, ...]:
    return (
        configuration.bind_address,
        configuration.port,
        configuration.certificate_file,
        configuration.private_key_file,
    )


__all__ = [
    "DaemonRemoteCredentialAuthority",
    "DaemonRemoteCredentialGeneration",
    "DaemonRemoteCredentialLifecycleSnapshot",
    "DaemonRemoteCredentialReloadError",
    "DaemonRemoteCredentialReloadErrorReason",
    "DaemonRemoteCredentialSession",
    "DaemonRemoteCredentialSessionExpired",
]
