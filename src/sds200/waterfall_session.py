from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import floor
from time import monotonic
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

    def get_waterfall_frame(self, *, timeout: float = 2.0) -> GwfResponse: ...

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
    gwf_poll_interval_seconds: float
    gwf_max_consecutive_failures: int
    gwf_requests: int
    last_gwf_request_at: datetime | None
    gwf_skipped_poll_deadlines: int
    last_gwf_scheduler_lag_seconds: float | None
    maximum_gwf_scheduler_lag_seconds: float | None
    gwf_round_trip_samples: int
    last_gwf_round_trip_seconds: float | None
    average_gwf_round_trip_seconds: float | None
    maximum_gwf_round_trip_seconds: float | None
    gwf_poll_failures: int
    consecutive_gwf_failures: int
    last_gwf_failure_at: datetime | None
    last_gwf_error: str | None
    gst_poll_interval_seconds: float
    gst_requests: int
    last_gst_request_at: datetime | None
    gst_skipped_poll_deadlines: int
    gst_poll_failures: int
    last_gst_failure_at: datetime | None
    last_gst_error: str | None
    waterfall_status_revision: int
    waterfall_status_refreshed_at: datetime | None
    waterfall_status_changed_at: datetime | None
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
            "gwf_poll_interval_seconds": self.gwf_poll_interval_seconds,
            "gwf_max_consecutive_failures": (
                self.gwf_max_consecutive_failures
            ),
            "gwf_requests": self.gwf_requests,
            "last_gwf_request_at": _optional_datetime(
                self.last_gwf_request_at
            ),
            "gwf_skipped_poll_deadlines": self.gwf_skipped_poll_deadlines,
            "last_gwf_scheduler_lag_seconds": (
                self.last_gwf_scheduler_lag_seconds
            ),
            "maximum_gwf_scheduler_lag_seconds": (
                self.maximum_gwf_scheduler_lag_seconds
            ),
            "gwf_round_trip_samples": self.gwf_round_trip_samples,
            "last_gwf_round_trip_seconds": self.last_gwf_round_trip_seconds,
            "average_gwf_round_trip_seconds": (
                self.average_gwf_round_trip_seconds
            ),
            "maximum_gwf_round_trip_seconds": (
                self.maximum_gwf_round_trip_seconds
            ),
            "gwf_poll_failures": self.gwf_poll_failures,
            "consecutive_gwf_failures": self.consecutive_gwf_failures,
            "last_gwf_failure_at": _optional_datetime(
                self.last_gwf_failure_at
            ),
            "last_gwf_error": self.last_gwf_error,
            "gst_poll_interval_seconds": self.gst_poll_interval_seconds,
            "gst_requests": self.gst_requests,
            "last_gst_request_at": _optional_datetime(
                self.last_gst_request_at
            ),
            "gst_skipped_poll_deadlines": self.gst_skipped_poll_deadlines,
            "gst_poll_failures": self.gst_poll_failures,
            "last_gst_failure_at": _optional_datetime(
                self.last_gst_failure_at
            ),
            "last_gst_error": self.last_gst_error,
            "waterfall_status_revision": self.waterfall_status_revision,
            "waterfall_status_refreshed_at": _optional_datetime(
                self.waterfall_status_refreshed_at
            ),
            "waterfall_status_changed_at": _optional_datetime(
                self.waterfall_status_changed_at
            ),
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


def _advance_periodic_deadline(
    deadline: float,
    interval: float,
    now: float,
) -> tuple[float, int, float]:
    lag = max(0.0, now - deadline)
    elapsed_intervals = floor(lag / interval)
    next_deadline = deadline + (elapsed_intervals + 1) * interval
    if next_deadline <= now:
        elapsed_intervals += 1
        next_deadline += interval
    return next_deadline, elapsed_intervals, lag


def _waterfall_status_key(status: GstResponse) -> tuple[object, ...]:
    return (
        status.waterfall_mode,
        status.marker_frequency,
        status.modulation,
        status.marker_position,
        status.center_frequency,
        status.lower_frequency,
        status.upper_frequency,
        status.color_mode,
        status.fft_area_size,
    )


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
        poll_interval: float = 0.25,
        poll_timeout: float = 2.0,
        status_poll_interval: float = 1.0,
        status_poll_timeout: float = 1.0,
        max_consecutive_poll_failures: int = 3,
        poll_clock: Callable[[], float] = monotonic,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if start_timeout <= 0:
            raise ValueError("Waterfall session start timeout must be greater than zero.")
        if stop_timeout <= 0:
            raise ValueError("Waterfall session stop timeout must be greater than zero.")
        if poll_interval <= 0:
            raise ValueError("Waterfall GWF poll interval must be greater than zero.")
        if poll_timeout <= 0:
            raise ValueError("Waterfall GWF poll timeout must be greater than zero.")
        if status_poll_interval <= 0:
            raise ValueError("Waterfall GST poll interval must be greater than zero.")
        if status_poll_timeout <= 0:
            raise ValueError("Waterfall GST poll timeout must be greater than zero.")
        if (
            isinstance(max_consecutive_poll_failures, bool)
            or not isinstance(max_consecutive_poll_failures, int)
            or max_consecutive_poll_failures <= 0
        ):
            raise ValueError(
                "Waterfall maximum consecutive GWF poll failures must be a "
                "positive integer."
            )
        self.radio = radio
        self.start_timeout = float(start_timeout)
        self.stop_timeout = float(stop_timeout)
        self.poll_interval = float(poll_interval)
        self.poll_timeout = float(poll_timeout)
        self.status_poll_interval = float(status_poll_interval)
        self.status_poll_timeout = float(status_poll_timeout)
        self.max_consecutive_poll_failures = max_consecutive_poll_failures
        self._poll_clock = poll_clock
        self._now = now
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        initial = now()
        self._state = WaterfallSessionState.IDLE
        self._leases: set[WaterfallSessionLease] = set()
        self._transition_sequence = 0
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._state_changed_at = initial
        self._last_failure_at: datetime | None = None
        self._last_error: str | None = None
        self._gwf_requests = 0
        self._last_gwf_request_at: datetime | None = None
        self._gwf_skipped_poll_deadlines = 0
        self._last_gwf_scheduler_lag_seconds: float | None = None
        self._maximum_gwf_scheduler_lag_seconds: float | None = None
        self._gwf_round_trip_samples = 0
        self._gwf_round_trip_total_seconds = 0.0
        self._last_gwf_round_trip_seconds: float | None = None
        self._maximum_gwf_round_trip_seconds: float | None = None
        self._gwf_poll_failures = 0
        self._consecutive_gwf_failures = 0
        self._last_gwf_failure_at: datetime | None = None
        self._last_gwf_error: str | None = None
        self._next_poll_at: float | None = None
        self._gst_requests = 0
        self._last_gst_request_at: datetime | None = None
        self._gst_skipped_poll_deadlines = 0
        self._gst_poll_failures = 0
        self._last_gst_failure_at: datetime | None = None
        self._last_gst_error: str | None = None
        self._next_gst_poll_at: float | None = None
        self._waterfall_status_revision = 0
        self._waterfall_status_refreshed_at: datetime | None = None
        self._waterfall_status_changed_at: datetime | None = None
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
                self._record_gst_request_locked()
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
            self._record_gwf_request_locked()
            self._record_gst_success_locked(status)
            self._transition_locked(WaterfallSessionState.RUNNING)
            return lease

    def poll(self) -> bool:
        """Request one due GWF frame for the shared daemon-owned session."""

        if not self._poll_lock.acquire(blocking=False):
            return False
        try:
            with self._lock:
                now = self._poll_clock()
                if (
                    self._state is not WaterfallSessionState.RUNNING
                    or not self._leases
                    or self._next_poll_at is None
                    or now < self._next_poll_at
                ):
                    return False
                self._advance_poll_deadline_locked(now)
                self._gwf_requests += 1
                self._last_gwf_request_at = self._now()

            request_started_at = self._poll_clock()
            try:
                self.radio.get_waterfall_frame(timeout=self.poll_timeout)
            except Exception as error:
                with self._lock:
                    if (
                        self._state is WaterfallSessionState.RUNNING
                        and self._leases
                    ):
                        self._gwf_poll_failures += 1
                        self._consecutive_gwf_failures += 1
                        self._last_gwf_failure_at = self._now()
                        self._last_gwf_error = (
                            f"{error.__class__.__name__}: {error}"
                        )
                        if (
                            self._consecutive_gwf_failures
                            >= self.max_consecutive_poll_failures
                        ):
                            self._record_failure_locked(error)
                            raise
                        logger.warning(
                            "Waterfall GWF poll missed error=%s "
                            "consecutive_failures=%d",
                            error.__class__.__name__,
                            self._consecutive_gwf_failures,
                        )
                return False

            request_completed_at = self._poll_clock()
            with self._lock:
                if (
                    self._state is WaterfallSessionState.RUNNING
                    and self._leases
                ):
                    self._consecutive_gwf_failures = 0
                    self._record_gwf_round_trip_locked(
                        max(0.0, request_completed_at - request_started_at)
                    )
            self._poll_waterfall_status_if_due()
            return True
        finally:
            self._poll_lock.release()

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
            if (
                self._state is not WaterfallSessionState.INTERRUPTED
                or not self._leases
            ):
                return
            start_timeout = self.start_timeout if timeout is None else float(timeout)
            if start_timeout <= 0:
                raise ValueError(
                    "Waterfall session recovery timeout must be greater than zero."
                )
            self._transition_locked(WaterfallSessionState.STARTING)
            try:
                self._record_gst_request_locked()
                status = self.radio.get_waterfall_status(timeout=start_timeout)
                self.radio.start_waterfall_publication(timeout=start_timeout)
            except BaseException as error:
                self._record_failure_locked(error)
                raise
            self._started_at = self._now()
            self._stopped_at = None
            self._last_error = None
            self._record_gwf_request_locked()
            self._record_gst_success_locked(status)
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
            self._next_poll_at = None
            self._next_gst_poll_at = None
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
            self._next_poll_at = None
            self._next_gst_poll_at = None
            self._transition_locked(WaterfallSessionState.IDLE)

    def _record_gwf_request_locked(self) -> None:
        self._gwf_requests += 1
        self._last_gwf_request_at = self._now()
        self._consecutive_gwf_failures = 0
        self._next_poll_at = self._poll_clock() + self.poll_interval

    def _advance_poll_deadline_locked(self, now: float) -> None:
        deadline = self._next_poll_at
        if deadline is None:
            raise RuntimeError("Waterfall GWF poll deadline is unavailable.")

        next_deadline, elapsed_intervals, lag = _advance_periodic_deadline(
            deadline,
            self.poll_interval,
            now,
        )

        self._next_poll_at = next_deadline
        self._gwf_skipped_poll_deadlines += elapsed_intervals
        self._last_gwf_scheduler_lag_seconds = lag
        maximum_lag = self._maximum_gwf_scheduler_lag_seconds
        if maximum_lag is None or lag > maximum_lag:
            self._maximum_gwf_scheduler_lag_seconds = lag

    def _record_gwf_round_trip_locked(self, duration: float) -> None:
        self._gwf_round_trip_samples += 1
        self._gwf_round_trip_total_seconds += duration
        self._last_gwf_round_trip_seconds = duration
        maximum_duration = self._maximum_gwf_round_trip_seconds
        if maximum_duration is None or duration > maximum_duration:
            self._maximum_gwf_round_trip_seconds = duration

    def _poll_waterfall_status_if_due(self) -> None:
        with self._lock:
            now = self._poll_clock()
            deadline = self._next_gst_poll_at
            if (
                self._state is not WaterfallSessionState.RUNNING
                or not self._leases
                or deadline is None
                or now < deadline
            ):
                return
            next_deadline, skipped, _ = _advance_periodic_deadline(
                deadline,
                self.status_poll_interval,
                now,
            )
            self._next_gst_poll_at = next_deadline
            self._gst_skipped_poll_deadlines += skipped
            self._record_gst_request_locked()

        try:
            status = self.radio.get_waterfall_status(
                timeout=self.status_poll_timeout
            )
        except Exception as error:
            with self._lock:
                if (
                    self._state is WaterfallSessionState.RUNNING
                    and self._leases
                ):
                    self._gst_poll_failures += 1
                    self._last_gst_failure_at = self._now()
                    self._last_gst_error = (
                        f"{error.__class__.__name__}: {error}"
                    )
                    logger.warning(
                        "Waterfall GST refresh missed error=%s",
                        error.__class__.__name__,
                    )
            return

        with self._lock:
            if (
                self._state is WaterfallSessionState.RUNNING
                and self._leases
            ):
                self._record_gst_success_locked(status, schedule_next=False)

    def _record_gst_request_locked(self) -> None:
        self._gst_requests += 1
        self._last_gst_request_at = self._now()

    def _record_gst_success_locked(
        self,
        status: GstResponse,
        *,
        schedule_next: bool = True,
    ) -> None:
        observed_at = self._now()
        previous = self._waterfall_status
        if (
            previous is None
            or _waterfall_status_key(previous) != _waterfall_status_key(status)
        ):
            self._waterfall_status_revision += 1
            self._waterfall_status_changed_at = observed_at
        self._waterfall_status = status
        self._waterfall_status_refreshed_at = observed_at
        self._last_gst_error = None
        if schedule_next:
            self._next_gst_poll_at = (
                self._poll_clock() + self.status_poll_interval
            )

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
            gwf_poll_interval_seconds=self.poll_interval,
            gwf_max_consecutive_failures=(
                self.max_consecutive_poll_failures
            ),
            gwf_requests=self._gwf_requests,
            last_gwf_request_at=self._last_gwf_request_at,
            gwf_skipped_poll_deadlines=self._gwf_skipped_poll_deadlines,
            last_gwf_scheduler_lag_seconds=(
                self._last_gwf_scheduler_lag_seconds
            ),
            maximum_gwf_scheduler_lag_seconds=(
                self._maximum_gwf_scheduler_lag_seconds
            ),
            gwf_round_trip_samples=self._gwf_round_trip_samples,
            last_gwf_round_trip_seconds=self._last_gwf_round_trip_seconds,
            average_gwf_round_trip_seconds=(
                None
                if self._gwf_round_trip_samples == 0
                else self._gwf_round_trip_total_seconds
                / self._gwf_round_trip_samples
            ),
            maximum_gwf_round_trip_seconds=(
                self._maximum_gwf_round_trip_seconds
            ),
            gwf_poll_failures=self._gwf_poll_failures,
            consecutive_gwf_failures=self._consecutive_gwf_failures,
            last_gwf_failure_at=self._last_gwf_failure_at,
            last_gwf_error=self._last_gwf_error,
            gst_poll_interval_seconds=self.status_poll_interval,
            gst_requests=self._gst_requests,
            last_gst_request_at=self._last_gst_request_at,
            gst_skipped_poll_deadlines=self._gst_skipped_poll_deadlines,
            gst_poll_failures=self._gst_poll_failures,
            last_gst_failure_at=self._last_gst_failure_at,
            last_gst_error=self._last_gst_error,
            waterfall_status_revision=self._waterfall_status_revision,
            waterfall_status_refreshed_at=(
                self._waterfall_status_refreshed_at
            ),
            waterfall_status_changed_at=self._waterfall_status_changed_at,
            waterfall_status=self._waterfall_status,
            publisher=self.radio.waterfall_snapshot(),
        )
