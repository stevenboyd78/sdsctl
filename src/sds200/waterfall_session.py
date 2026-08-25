from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from .models import GstResponse, GwfResponse, PwfResponse
from .waterfall_subscriptions import (
    WaterfallDelivery,
    WaterfallPublisherSnapshot,
    WaterfallSubscription,
    WaterfallSubscriptionSnapshot,
)

logger = logging.getLogger(__name__)


class WaterfallSessionState(StrEnum):
    """Lifecycle state for one shared scanner waterfall publication session."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    CLOSED = "closed"


class _WaterfallRadio(Protocol):
    def get_waterfall_status(self, *, timeout: float = 2.0) -> GstResponse: ...

    def start_waterfall_publication(
        self,
        *,
        timeout: float = 3.0,
    ) -> tuple[PwfResponse, GwfResponse]: ...

    def stop_waterfall_publication(self, *, timeout: float = 2.0) -> None: ...

    def subscribe_waterfall(self) -> WaterfallSubscription: ...

    def waterfall_snapshot(self) -> WaterfallPublisherSnapshot: ...


@dataclass(frozen=True, slots=True)
class WaterfallSessionSnapshot:
    """Immutable shared-session lifecycle and radio-publication telemetry."""

    state: WaterfallSessionState
    consumer_count: int
    transition_sequence: int
    started_at: datetime | None
    stopped_at: datetime | None
    state_changed_at: datetime
    last_failure_at: datetime | None
    last_error: str | None
    waterfall_status: GstResponse | None
    publisher: WaterfallPublisherSnapshot

    @property
    def active(self) -> bool:
        return self.state in {
            WaterfallSessionState.STARTING,
            WaterfallSessionState.RUNNING,
            WaterfallSessionState.STOPPING,
        }

    @property
    def closed(self) -> bool:
        return self.state is WaterfallSessionState.CLOSED

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "active": self.active,
            "closed": self.closed,
            "consumer_count": self.consumer_count,
            "transition_sequence": self.transition_sequence,
            "started_at": _optional_datetime(self.started_at),
            "stopped_at": _optional_datetime(self.stopped_at),
            "state_changed_at": self.state_changed_at.isoformat(),
            "last_failure_at": _optional_datetime(self.last_failure_at),
            "last_error": self.last_error,
            "waterfall_status": (
                None
                if self.waterfall_status is None
                else self.waterfall_status.as_dict()
            ),
            "publisher": self.publisher.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class WaterfallSessionTransition:
    """One ordered immutable shared-session lifecycle transition."""

    sequence: int
    observed_at: datetime
    previous_state: WaterfallSessionState
    state: WaterfallSessionState
    snapshot: WaterfallSessionSnapshot

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "previous_state": self.previous_state.value,
            "state": self.state.value,
            "snapshot": self.snapshot.as_dict(),
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _optional_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


class WaterfallSessionLease:
    """One consumer lease on the shared scanner waterfall session."""

    def __init__(
        self,
        session: WaterfallSession,
        subscription: WaterfallSubscription,
    ) -> None:
        self._session = session
        self._subscription = subscription
        self._lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def get(self, timeout: float | None = None) -> WaterfallDelivery:
        return self._subscription.get(timeout)

    def snapshot(self) -> WaterfallSubscriptionSnapshot:
        return self._subscription.snapshot()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._session._release(self)

    def __enter__(self) -> WaterfallSessionLease:
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

    def _close_subscription(self) -> None:
        self._subscription.close()


class WaterfallSession:
    """Own one demand-driven PWF/GWF lifecycle shared by bounded consumers."""

    def __init__(
        self,
        radio: _WaterfallRadio,
        *,
        start_timeout: float = 3.0,
        stop_timeout: float = 2.0,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if start_timeout <= 0:
            raise ValueError("Waterfall session start timeout must be greater than zero.")
        if stop_timeout <= 0:
            raise ValueError("Waterfall session stop timeout must be greater than zero.")
        self.radio = radio
        self.start_timeout = float(start_timeout)
        self.stop_timeout = float(stop_timeout)
        self._now = now
        self._lock = threading.RLock()
        initial = now()
        self._state = WaterfallSessionState.IDLE
        self._leases: set[WaterfallSessionLease] = set()
        self._transition_sequence = 0
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._state_changed_at = initial
        self._last_failure_at: datetime | None = None
        self._last_error: str | None = None
        self._waterfall_status: GstResponse | None = None
        self._callbacks: set[Callable[[WaterfallSessionTransition], None]] = set()

    @property
    def state(self) -> WaterfallSessionState:
        with self._lock:
            return self._state

    @property
    def consumer_count(self) -> int:
        with self._lock:
            return len(self._leases)

    def snapshot(self) -> WaterfallSessionSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def on_transition(
        self,
        callback: Callable[[WaterfallSessionTransition], None],
    ) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("Waterfall session transition callback must be callable.")
        with self._lock:
            if self._state is WaterfallSessionState.CLOSED:
                raise RuntimeError("Waterfall session is closed.")
            self._callbacks.add(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._callbacks.discard(callback)

        return unsubscribe

    def subscribe(self) -> WaterfallSessionLease:
        with self._lock:
            if self._state is WaterfallSessionState.CLOSED:
                raise RuntimeError("Waterfall session is closed.")

            subscription = self.radio.subscribe_waterfall()
            lease = WaterfallSessionLease(self, subscription)
            first_consumer = not self._leases
            self._leases.add(lease)
            if not first_consumer:
                return lease

            self._transition_locked(WaterfallSessionState.STARTING)
            try:
                status = self.radio.get_waterfall_status(timeout=self.start_timeout)
                self.radio.start_waterfall_publication(timeout=self.start_timeout)
            except BaseException as error:
                self._leases.discard(lease)
                subscription.close()
                self._record_failure_locked(error)
                raise

            self._started_at = self._now()
            self._stopped_at = None
            self._last_error = None
            self._waterfall_status = status
            self._transition_locked(WaterfallSessionState.RUNNING)
            return lease

    def mark_interrupted(self) -> None:
        """Record transport loss without releasing existing consumer demand."""

        with self._lock:
            if (
                self._leases
                and self._state
                not in {WaterfallSessionState.CLOSED, WaterfallSessionState.INTERRUPTED}
            ):
                self._transition_locked(WaterfallSessionState.INTERRUPTED)

    def recover(self, *, timeout: float | None = None) -> None:
        """Re-establish publication after reconnect while consumer demand remains."""

        with self._lock:
            if self._state is WaterfallSessionState.CLOSED or not self._leases:
                return
            start_timeout = self.start_timeout if timeout is None else float(timeout)
            if start_timeout <= 0:
                raise ValueError(
                    "Waterfall session recovery timeout must be greater than zero."
                )
            self._transition_locked(WaterfallSessionState.STARTING)
            try:
                status = self.radio.get_waterfall_status(timeout=start_timeout)
                self.radio.start_waterfall_publication(timeout=start_timeout)
            except BaseException as error:
                self._record_failure_locked(error)
                raise
            self._started_at = self._now()
            self._stopped_at = None
            self._last_error = None
            self._waterfall_status = status
            self._transition_locked(WaterfallSessionState.RUNNING)

    def close(self) -> None:
        with self._lock:
            if self._state is WaterfallSessionState.CLOSED:
                return

            leases = tuple(self._leases)
            self._leases.clear()
            for lease in leases:
                lease._close_subscription()

            stop_error: BaseException | None = None
            if self._state is not WaterfallSessionState.IDLE:
                self._transition_locked(WaterfallSessionState.STOPPING)
                try:
                    self.radio.stop_waterfall_publication(timeout=self.stop_timeout)
                except BaseException as error:
                    stop_error = error
                    self._last_failure_at = self._now()
                    self._last_error = f"{error.__class__.__name__}: {error}"

            self._stopped_at = self._now()
            self._transition_locked(WaterfallSessionState.CLOSED)
            self._callbacks.clear()
            if stop_error is not None:
                raise stop_error

    def _release(self, lease: WaterfallSessionLease) -> None:
        with self._lock:
            if lease not in self._leases:
                lease._close_subscription()
                return
            self._leases.remove(lease)
            lease._close_subscription()
            if self._leases or self._state is WaterfallSessionState.CLOSED:
                return

            self._transition_locked(WaterfallSessionState.STOPPING)
            try:
                self.radio.stop_waterfall_publication(timeout=self.stop_timeout)
            except BaseException as error:
                self._record_failure_locked(error)
                raise
            self._stopped_at = self._now()
            self._transition_locked(WaterfallSessionState.IDLE)

    def _record_failure_locked(self, error: BaseException) -> None:
        self._last_failure_at = self._now()
        self._last_error = f"{error.__class__.__name__}: {error}"
        self._transition_locked(WaterfallSessionState.FAILED)

    def _transition_locked(self, state: WaterfallSessionState) -> None:
        previous = self._state
        observed_at = self._now()
        self._state = state
        self._state_changed_at = observed_at
        self._transition_sequence += 1
        transition = WaterfallSessionTransition(
            sequence=self._transition_sequence,
            observed_at=observed_at,
            previous_state=previous,
            state=state,
            snapshot=self._snapshot_locked(),
        )
        for callback in tuple(self._callbacks):
            try:
                callback(transition)
            except Exception:
                logger.exception("Waterfall session transition callback failed")

    def _snapshot_locked(self) -> WaterfallSessionSnapshot:
        return WaterfallSessionSnapshot(
            state=self._state,
            consumer_count=len(self._leases),
            transition_sequence=self._transition_sequence,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            state_changed_at=self._state_changed_at,
            last_failure_at=self._last_failure_at,
            last_error=self._last_error,
            waterfall_status=self._waterfall_status,
            publisher=self.radio.waterfall_snapshot(),
        )
