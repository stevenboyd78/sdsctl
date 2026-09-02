"""Authenticated leases over existing daemon-owned observation publishers.

This module deliberately owns no scanner, Waterfall, or audio transport.  It
adds a bounded remote authorization and lifecycle boundary around publishers
that the daemon already owns.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Protocol, Self, TypeVar, cast

from .daemon_events import (
    DaemonEvent,
    DaemonEventKind,
    DaemonEventSubscription,
    DaemonEventSubscriptionClosed,
)
from .daemon_remote import DaemonRemoteAuthorizationScope
from .daemon_remote_credentials import DaemonRemoteCredentialSessionExpired
from .daemon_remote_tls import DaemonRemoteAuthenticatedPeer
from .pcmu_subscriptions import (
    PcmuPacketDelivery,
    PcmuSubscription,
    PcmuSubscriptionClosed,
)
from .waterfall_session import (
    WaterfallSessionLease,
    WaterfallSessionSnapshot,
    WaterfallSessionTransition,
)
from .waterfall_subscriptions import (
    WaterfallDelivery,
    WaterfallSubscriptionClosed,
)

DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES = 24
DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES_PER_CLIENT = 3
DAEMON_REMOTE_AUDIO_ENDPOINT = "sdsctl-remote-daemon"
DAEMON_REMOTE_EVENT_REDACTED_FIELDS = (
    "access_token",
    "credential",
    "credential_file",
    "credentials",
    "directory",
    "endpoint",
    "file",
    "filename",
    "ingress",
    "ingress_id",
    "last_error",
    "path",
    "recording",
    "recordings",
    "scanner_endpoint",
    "secret",
    "token",
)

_REDACTED_FIELDS = frozenset(DAEMON_REMOTE_EVENT_REDACTED_FIELDS)
_Result = TypeVar("_Result")


class DaemonRemoteObservationKind(StrEnum):
    """Observation streams available to an authenticated remote client."""

    EVENTS = "events"
    WATERFALL = "waterfall"
    AUDIO = "audio"


class DaemonRemoteObservationErrorReason(StrEnum):
    """Stable, non-secret remote observation failure classes."""

    AUTHORIZATION_DENIED = "authorization_denied"
    AUTHENTICATION_EXPIRED = "authentication_expired"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    DUPLICATE_LEASE = "duplicate_lease"
    SOURCE_UNAVAILABLE = "source_unavailable"
    LEASE_CLOSED = "lease_closed"
    BROKER_CLOSED = "broker_closed"


_ERROR_MESSAGES = {
    DaemonRemoteObservationErrorReason.AUTHORIZATION_DENIED: (
        "Remote observation authority is unavailable."
    ),
    DaemonRemoteObservationErrorReason.AUTHENTICATION_EXPIRED: (
        "Remote observation authentication is no longer current."
    ),
    DaemonRemoteObservationErrorReason.CAPACITY_EXCEEDED: (
        "Remote observation capacity is unavailable."
    ),
    DaemonRemoteObservationErrorReason.DUPLICATE_LEASE: (
        "The remote observation lease is already active."
    ),
    DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE: (
        "The remote observation source is unavailable."
    ),
    DaemonRemoteObservationErrorReason.LEASE_CLOSED: ("The remote observation lease is closed."),
    DaemonRemoteObservationErrorReason.BROKER_CLOSED: ("The remote observation broker is closed."),
}


class DaemonRemoteObservationError(RuntimeError):
    """Report a remote lease failure without identity or endpoint detail."""

    def __init__(self, reason: DaemonRemoteObservationErrorReason) -> None:
        if not isinstance(reason, DaemonRemoteObservationErrorReason):
            raise TypeError(
                "Remote observation error reason must be DaemonRemoteObservationErrorReason."
            )
        self.reason = reason
        super().__init__(_ERROR_MESSAGES[reason])


class _EventSource(Protocol):
    def subscribe(self) -> DaemonEventSubscription: ...


class _WaterfallSource(Protocol):
    def subscribe(self) -> WaterfallSessionLease: ...

    def snapshot(self) -> WaterfallSessionSnapshot: ...

    def on_transition(
        self,
        callback: Callable[[WaterfallSessionTransition], None],
    ) -> Callable[[], None]: ...


class _AudioSource(Protocol):
    def subscribe(self) -> PcmuSubscription: ...


@dataclass(frozen=True, slots=True)
class DaemonRemoteObservationSnapshot:
    """Redacted activity and capacity for the remote lease broker."""

    closed: bool
    max_leases: int
    max_leases_per_client: int
    active_leases: int
    event_leases: int
    waterfall_leases: int
    audio_leases: int
    acquired_leases: int
    released_leases: int
    expired_leases: int
    rejected_leases: int
    filtered_events: int

    def as_dict(self) -> dict[str, object]:
        return {
            "closed": self.closed,
            "max_leases": self.max_leases,
            "max_leases_per_client": self.max_leases_per_client,
            "active_leases": self.active_leases,
            "event_leases": self.event_leases,
            "waterfall_leases": self.waterfall_leases,
            "audio_leases": self.audio_leases,
            "acquired_leases": self.acquired_leases,
            "released_leases": self.released_leases,
            "expired_leases": self.expired_leases,
            "rejected_leases": self.rejected_leases,
            "filtered_events": self.filtered_events,
        }


class _DaemonRemoteObservationLease:
    """Shared lifecycle for one source-specific remote observation lease."""

    def __init__(
        self,
        broker: DaemonRemoteObservationBroker,
        peer: DaemonRemoteAuthenticatedPeer,
        kind: DaemonRemoteObservationKind,
        source_close: Callable[[], None],
    ) -> None:
        self._broker = broker
        self._peer = peer
        self.kind = kind
        self._source_close = source_close
        self._lock = threading.RLock()
        self._closed = False
        self._expired = False
        self._remove_invalidator: Callable[[], None] = lambda: None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(kind={self.kind.value!r}, closed={self.closed})"

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def expired(self) -> bool:
        with self._lock:
            return self._expired

    def close(self) -> None:
        self._finish(expired=False, suppress_source_error=False)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, traceback
        try:
            self.close()
        except BaseException:
            if exception is None:
                raise

    def _attach_invalidator(self) -> None:
        remove = self._peer.on_credentials_invalidated(self._expire)
        with self._lock:
            if self._closed:
                remove()
            else:
                self._remove_invalidator = remove

    def _receive(
        self,
        receiver: Callable[[float | None], _Result],
        source_closed: type[Exception],
        timeout: float | None,
    ) -> _Result:
        normalized_timeout = _optional_timeout(timeout)
        self._require_current()
        try:
            result = receiver(normalized_timeout)
        except source_closed as error:
            self._raise_closed_or_expired(error)
        self._require_current()
        return result

    def _require_current(self) -> None:
        with self._lock:
            closed = self._closed
            expired = self._expired
        if expired or not self._peer.credentials_current:
            self._expire()
            raise DaemonRemoteObservationError(
                DaemonRemoteObservationErrorReason.AUTHENTICATION_EXPIRED
            )
        if closed:
            raise DaemonRemoteObservationError(DaemonRemoteObservationErrorReason.LEASE_CLOSED)

    def _raise_closed_or_expired(self, error: Exception) -> None:
        del error
        with self._lock:
            expired = self._expired
        if expired or not self._peer.credentials_current:
            self._expire()
            raise DaemonRemoteObservationError(
                DaemonRemoteObservationErrorReason.AUTHENTICATION_EXPIRED
            ) from None
        self._finish(expired=False, suppress_source_error=True)
        raise DaemonRemoteObservationError(
            DaemonRemoteObservationErrorReason.LEASE_CLOSED
        ) from None

    def _expire(self) -> None:
        self._finish(expired=True, suppress_source_error=True)

    def _close_from_broker(self) -> None:
        self._finish(expired=False, suppress_source_error=True)

    def _finish(
        self,
        *,
        expired: bool,
        suppress_source_error: bool,
    ) -> None:
        with self._lock:
            if self._closed:
                if expired:
                    self._expired = True
                return
            self._closed = True
            self._expired = expired
            remove_invalidator = self._remove_invalidator
            self._remove_invalidator = lambda: None

        remove_invalidator()
        source_error: Exception | None = None
        try:
            self._source_close()
        except Exception as error:
            source_error = error
        finally:
            self._broker._release(self, expired=expired)

        if source_error is not None and not suppress_source_error:
            raise DaemonRemoteObservationError(
                DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE
            ) from None


class DaemonRemoteEventLease(_DaemonRemoteObservationLease):
    """Filtered, redacted lease over the existing ordered event stream."""

    def __init__(
        self,
        broker: DaemonRemoteObservationBroker,
        peer: DaemonRemoteAuthenticatedPeer,
        subscription: DaemonEventSubscription,
    ) -> None:
        super().__init__(
            broker,
            peer,
            DaemonRemoteObservationKind.EVENTS,
            subscription.close,
        )
        self._subscription = subscription

    def get(self, timeout: float | None = None) -> DaemonEvent:
        deadline = _optional_deadline(timeout)
        while True:
            event = self._receive(
                self._subscription.get,
                DaemonEventSubscriptionClosed,
                _remaining_timeout(deadline),
            )
            if event.kind == DaemonEventKind.RECORDING_STATE:
                self._broker._record_filtered_event()
                continue
            return _sanitize_event(event)


class DaemonRemoteWaterfallLease(_DaemonRemoteObservationLease):
    """Demand lease over the daemon's single shared Waterfall session."""

    def __init__(
        self,
        broker: DaemonRemoteObservationBroker,
        peer: DaemonRemoteAuthenticatedPeer,
        source: _WaterfallSource,
        lease: WaterfallSessionLease,
    ) -> None:
        super().__init__(
            broker,
            peer,
            DaemonRemoteObservationKind.WATERFALL,
            lease.close,
        )
        self._source = source
        self._lease = lease

    def get(self, timeout: float | None = None) -> WaterfallDelivery:
        return self._receive(
            self._lease.get,
            WaterfallSubscriptionClosed,
            timeout,
        )

    def snapshot(self) -> WaterfallSessionSnapshot:
        self._require_current()
        snapshot = self._source.snapshot()
        self._require_current()
        return snapshot

    def on_transition(
        self,
        callback: Callable[[WaterfallSessionTransition], None],
    ) -> Callable[[], None]:
        self._require_current()
        unsubscribe = self._source.on_transition(callback)
        try:
            self._require_current()
        except BaseException:
            unsubscribe()
            raise
        return unsubscribe


class DaemonRemoteAudioLease(_DaemonRemoteObservationLease):
    """Endpoint-redacted lease over daemon-owned accepted PCMU packets."""

    def __init__(
        self,
        broker: DaemonRemoteObservationBroker,
        peer: DaemonRemoteAuthenticatedPeer,
        subscription: PcmuSubscription,
    ) -> None:
        super().__init__(
            broker,
            peer,
            DaemonRemoteObservationKind.AUDIO,
            subscription.close,
        )
        self._subscription = subscription

    def get(self, timeout: float | None = None) -> PcmuPacketDelivery:
        delivery = self._receive(
            self._subscription.get,
            PcmuSubscriptionClosed,
            timeout,
        )
        packet = replace(
            delivery.packet,
            endpoint=DAEMON_REMOTE_AUDIO_ENDPOINT,
        )
        publication = replace(delivery.publication, packet=packet)
        return replace(delivery, publication=publication)


class DaemonRemoteObservationBroker:
    """Issue bounded authenticated leases over daemon-owned publishers."""

    def __init__(
        self,
        *,
        event_stream: _EventSource | None,
        waterfall_session: _WaterfallSource | None,
        pcmu_stream: _AudioSource | None,
        max_leases: int = DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES,
        max_leases_per_client: int = (DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES_PER_CLIENT),
    ) -> None:
        self.event_stream = cast(
            _EventSource | None,
            _optional_source(
                event_stream,
                label="Remote event source",
            ),
        )
        self.waterfall_session = cast(
            _WaterfallSource | None,
            _optional_source(
                waterfall_session,
                label="Remote Waterfall source",
            ),
        )
        self.pcmu_stream = cast(
            _AudioSource | None,
            _optional_source(
                pcmu_stream,
                label="Remote audio source",
            ),
        )
        self.max_leases = _positive_integer(
            max_leases,
            label="Maximum remote observation leases",
        )
        self.max_leases_per_client = _positive_integer(
            max_leases_per_client,
            label="Maximum remote observation leases per client",
        )
        if self.max_leases_per_client > self.max_leases:
            raise ValueError(
                "Maximum remote observation leases per client must not "
                "exceed the total lease limit."
            )

        self._lock = threading.RLock()
        self._closed = False
        self._leases: dict[
            _DaemonRemoteObservationLease,
            tuple[str, DaemonRemoteObservationKind],
        ] = {}
        self._client_kinds: dict[
            str,
            set[DaemonRemoteObservationKind],
        ] = {}
        self._acquired_leases = 0
        self._released_leases = 0
        self._expired_leases = 0
        self._rejected_leases = 0
        self._filtered_events = 0

    def __repr__(self) -> str:
        snapshot = self.snapshot()
        return (
            "DaemonRemoteObservationBroker("
            f"active_leases={snapshot.active_leases}, "
            f"closed={snapshot.closed})"
        )

    def snapshot(self) -> DaemonRemoteObservationSnapshot:
        with self._lock:
            kinds = tuple(kind for _, kind in self._leases.values())
            return DaemonRemoteObservationSnapshot(
                closed=self._closed,
                max_leases=self.max_leases,
                max_leases_per_client=self.max_leases_per_client,
                active_leases=len(self._leases),
                event_leases=kinds.count(DaemonRemoteObservationKind.EVENTS),
                waterfall_leases=kinds.count(DaemonRemoteObservationKind.WATERFALL),
                audio_leases=kinds.count(DaemonRemoteObservationKind.AUDIO),
                acquired_leases=self._acquired_leases,
                released_leases=self._released_leases,
                expired_leases=self._expired_leases,
                rejected_leases=self._rejected_leases,
                filtered_events=self._filtered_events,
            )

    def subscribe_events(
        self,
        peer: DaemonRemoteAuthenticatedPeer,
    ) -> DaemonRemoteEventLease:
        return cast(
            DaemonRemoteEventLease,
            self._acquire(peer, DaemonRemoteObservationKind.EVENTS),
        )

    def subscribe_waterfall(
        self,
        peer: DaemonRemoteAuthenticatedPeer,
    ) -> DaemonRemoteWaterfallLease:
        return cast(
            DaemonRemoteWaterfallLease,
            self._acquire(peer, DaemonRemoteObservationKind.WATERFALL),
        )

    def subscribe_audio(
        self,
        peer: DaemonRemoteAuthenticatedPeer,
    ) -> DaemonRemoteAudioLease:
        return cast(
            DaemonRemoteAudioLease,
            self._acquire(peer, DaemonRemoteObservationKind.AUDIO),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            leases = tuple(self._leases)
        for lease in leases:
            lease._close_from_broker()

    def __enter__(self) -> DaemonRemoteObservationBroker:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def _acquire(
        self,
        peer: DaemonRemoteAuthenticatedPeer,
        kind: DaemonRemoteObservationKind,
    ) -> _DaemonRemoteObservationLease:
        if not isinstance(peer, DaemonRemoteAuthenticatedPeer):
            raise TypeError("Remote observation leases require an authenticated TLS peer.")
        if not peer.allows(DaemonRemoteAuthorizationScope.OBSERVE):
            self._reject()
            raise DaemonRemoteObservationError(
                DaemonRemoteObservationErrorReason.AUTHORIZATION_DENIED
            )

        try:
            return peer.execute_if_credentials_current(lambda: self._acquire_current(peer, kind))
        except DaemonRemoteCredentialSessionExpired:
            self._reject()
            raise DaemonRemoteObservationError(
                DaemonRemoteObservationErrorReason.AUTHENTICATION_EXPIRED
            ) from None

    def _acquire_current(
        self,
        peer: DaemonRemoteAuthenticatedPeer,
        kind: DaemonRemoteObservationKind,
    ) -> _DaemonRemoteObservationLease:
        client_id = peer.client_id
        with self._lock:
            if self._closed:
                self._rejected_leases += 1
                raise DaemonRemoteObservationError(DaemonRemoteObservationErrorReason.BROKER_CLOSED)
            client_kinds = self._client_kinds.get(client_id, set())
            if kind in client_kinds:
                self._rejected_leases += 1
                raise DaemonRemoteObservationError(
                    DaemonRemoteObservationErrorReason.DUPLICATE_LEASE
                )
            if (
                len(self._leases) >= self.max_leases
                or len(client_kinds) >= self.max_leases_per_client
            ):
                self._rejected_leases += 1
                raise DaemonRemoteObservationError(
                    DaemonRemoteObservationErrorReason.CAPACITY_EXCEEDED
                )

            try:
                lease = self._subscribe_source(peer, kind)
            except DaemonRemoteObservationError:
                self._rejected_leases += 1
                raise
            except Exception:
                self._rejected_leases += 1
                raise DaemonRemoteObservationError(
                    DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE
                ) from None

            self._leases[lease] = (client_id, kind)
            self._client_kinds.setdefault(client_id, set()).add(kind)
            self._acquired_leases += 1
            try:
                lease._attach_invalidator()
            except DaemonRemoteCredentialSessionExpired:
                lease._close_from_broker()
                raise
            except Exception:
                lease._close_from_broker()
                self._rejected_leases += 1
                raise DaemonRemoteObservationError(
                    DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE
                ) from None
            return lease

    def _subscribe_source(
        self,
        peer: DaemonRemoteAuthenticatedPeer,
        kind: DaemonRemoteObservationKind,
    ) -> _DaemonRemoteObservationLease:
        if kind is DaemonRemoteObservationKind.EVENTS:
            event_source = self.event_stream
            if event_source is None:
                raise DaemonRemoteObservationError(
                    DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE
                )
            return DaemonRemoteEventLease(
                self,
                peer,
                event_source.subscribe(),
            )
        if kind is DaemonRemoteObservationKind.WATERFALL:
            waterfall_source = self.waterfall_session
            if waterfall_source is None:
                raise DaemonRemoteObservationError(
                    DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE
                )
            return DaemonRemoteWaterfallLease(
                self,
                peer,
                waterfall_source,
                waterfall_source.subscribe(),
            )
        audio_source = self.pcmu_stream
        if audio_source is None:
            raise DaemonRemoteObservationError(
                DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE
            )
        return DaemonRemoteAudioLease(
            self,
            peer,
            audio_source.subscribe(),
        )

    def _release(
        self,
        lease: _DaemonRemoteObservationLease,
        *,
        expired: bool,
    ) -> None:
        with self._lock:
            registered = self._leases.pop(lease, None)
            if registered is None:
                return
            client_id, kind = registered
            client_kinds = self._client_kinds.get(client_id)
            if client_kinds is not None:
                client_kinds.discard(kind)
                if not client_kinds:
                    self._client_kinds.pop(client_id, None)
            self._released_leases += 1
            if expired:
                self._expired_leases += 1

    def _reject(self) -> None:
        with self._lock:
            self._rejected_leases += 1

    def _record_filtered_event(self) -> None:
        with self._lock:
            self._filtered_events += 1


def _sanitize_event(event: DaemonEvent) -> DaemonEvent:
    payload = _sanitize_remote_value(event.payload)
    assert isinstance(payload, dict)
    return DaemonEvent(
        sequence=event.sequence,
        observed_at=event.observed_at,
        kind=event.kind,
        payload=payload,
        protocol=event.protocol,
        version=event.version,
    )


def _sanitize_remote_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _sanitize_remote_value(child)
            for key, child in value.items()
            if isinstance(key, str) and not _sensitive_field(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_remote_value(child) for child in value]
    return value


def _sensitive_field(field: str) -> bool:
    normalized = field.casefold()
    return (
        normalized in _REDACTED_FIELDS
        or normalized.endswith("_path")
        or normalized.endswith("_file")
        or normalized.endswith("_directory")
        or normalized.endswith("_token")
        or normalized.endswith("_credential")
        or normalized.endswith("_secret")
    )


def _optional_source(source: object | None, *, label: str) -> object | None:
    if source is not None and not callable(getattr(source, "subscribe", None)):
        raise TypeError(f"{label} must provide subscribe().")
    return source


def _positive_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def _optional_timeout(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Remote observation lease timeout must be a number or None.")
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0:
        raise ValueError("Remote observation lease timeout must be finite and non-negative.")
    return normalized


def _optional_deadline(value: float | None) -> float | None:
    timeout = _optional_timeout(value)
    return None if timeout is None else monotonic() + timeout


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - monotonic()
    return max(0.0, remaining)


__all__ = [
    "DAEMON_REMOTE_AUDIO_ENDPOINT",
    "DAEMON_REMOTE_EVENT_REDACTED_FIELDS",
    "DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES",
    "DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES_PER_CLIENT",
    "DaemonRemoteAudioLease",
    "DaemonRemoteEventLease",
    "DaemonRemoteObservationBroker",
    "DaemonRemoteObservationError",
    "DaemonRemoteObservationErrorReason",
    "DaemonRemoteObservationKind",
    "DaemonRemoteObservationSnapshot",
    "DaemonRemoteWaterfallLease",
]
