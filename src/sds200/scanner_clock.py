"""Passive, session-bound DTM samples; no transport, thread, timer or auto-read.

The opt-in shared owner worker must reserve a ticket, call the scanner's
read_clock_if_idle once, and finish the ticket even when that call fails. Only
an actual connection boundary may begin a new session. This module deliberately
does not schedule reads or enable clock fields on any display.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Literal
from uuid import UUID

from .commands import GetDateTime
from .exceptions import ProtocolError
from .models import ScannerDateTime

ClockFailure = Literal["rejected", "timeout", "invalid_response", "read_error"]
READ_TIMEOUT = 0.25
REFRESH_INTERVAL = 2.0
STALE_AFTER = 5.0
REJECTION_BACKOFF = 30.0


@dataclass(frozen=True, slots=True, eq=False)
class ClockSession:
    endpoint_id: UUID


@dataclass(frozen=True, slots=True, eq=False)
class ClockReadTicket:
    session: ClockSession
    requested_at: float
    epoch: int


@dataclass(frozen=True, slots=True)
class ClockSnapshot:
    """Exact scanner-local reading; no timezone inference or extrapolation."""

    local_time: datetime | None
    daylight_saving: str | None
    rtc_valid: bool | None
    age_seconds: float | None
    failure: ClockFailure | None
    blocked_until_reconnect: ClockFailure | None


@dataclass(frozen=True, slots=True)
class _Sample:
    local_time: datetime | None
    daylight_saving: str
    rtc_valid: bool
    requested_at: float


class ScannerClockSamples:
    def __init__(self, endpoint_id: UUID, *, clock: Callable[[], float] = monotonic) -> None:
        if not isinstance(endpoint_id, UUID):
            raise ValueError("An explicit clock endpoint identity is required.")
        self._endpoint_id, self._clock = endpoint_id, clock
        self._lock = threading.Lock()
        self._session: ClockSession | None = None
        self._pending: ClockReadTicket | None = None
        self._sample: _Sample | None = None
        self._failure: ClockFailure | None = None
        self._blocked: ClockFailure | None = None
        self._epoch = 0
        self._due = self._latest_time = 0.0

    def _now(self) -> float:
        now = self._clock()
        if (
            type(now) not in (int, float)
            or not 0 <= now <= 1e15
            or not math.isfinite(now)
            or now < self._latest_time
        ):
            self._sample = None
            self._failure = self._blocked = "read_error"
            raise ValueError("A bounded, nondecreasing monotonic clock is required.")
        self._latest_time = float(now)
        return float(now)

    def begin_session(self) -> ClockSession:
        """Owner only: call after a real connection boundary, not to retry DTM."""
        with self._lock:
            self._now()
            self._epoch += 1
            self._session = ClockSession(self._endpoint_id)
            self._sample = None
            self._failure = self._blocked = None
            self._due = 0.0
            # A previous session's in-flight request still occupies the slot.
            return self._session

    def invalidate(self, session: ClockSession) -> None:
        """Drop cached/in-flight values without lifting a timeout quarantine."""
        with self._lock:
            if session is self._session:
                self._epoch += 1
                self._sample = None

    def disconnect(self, session: ClockSession) -> None:
        with self._lock:
            if session is self._session:
                self._epoch += 1
                self._sample = None
                self._session = None

    def begin_read(self, session: ClockSession) -> ClockReadTicket | None:
        """Reserve one GET; the caller must still honor the shared lane/pacing."""
        with self._lock:
            if (
                self._session is None
                or session is not self._session
                or self._blocked
                or self._pending
            ):
                return None
            now = self._now()
            if now < self._due:
                return None
            self._pending = ClockReadTicket(session, now, self._epoch)
            self._due = now + REFRESH_INTERVAL
            return self._pending

    def finish(
        self,
        ticket: ClockReadTicket,
        result: ScannerDateTime | None = None,
        *,
        failure: ClockFailure | None = None,
    ) -> bool:
        """Finish exactly this ticket. None/no failure means the lane was busy.

        Exceptions must be translated to a fixed failure class by the caller.
        No exception text or raw packet is retained. Uncertain replies prevent
        another DTM in this connection, including after an invalidation barrier.
        """
        if failure not in (None, "rejected", "timeout", "invalid_response", "read_error"):
            raise ValueError("A recognized clock failure class is required.")
        if failure is not None and result is not None:
            raise ValueError("A clock read cannot both succeed and fail.")
        with self._lock:
            if self._pending is None or ticket is not self._pending:
                return False
            self._pending = None
            if ticket.session is not self._session or self._blocked is not None:
                return False
            now = self._now()
            if now - ticket.requested_at >= READ_TIMEOUT:
                failure = "timeout"
            elif result is not None and not self._valid_reading(result):
                failure = "invalid_response"
            if failure is not None:
                self._sample = None
                self._failure = failure
                if failure == "rejected":
                    self._due = now + REJECTION_BACKOFF
                else:
                    self._blocked = failure
                return False
            if ticket.epoch != self._epoch or result is None:
                return False
            self._sample = _Sample(
                result.local_time,
                result.daylight_saving,
                result.rtc_valid,
                ticket.requested_at,
            )
            self._failure = None
            return True

    @staticmethod
    def _valid_reading(result: ScannerDateTime) -> bool:
        if type(result) is not ScannerDateTime or type(result.rtc_valid) is not bool:
            return False
        if result.local_time is not None and (
            type(result.local_time) is not datetime or result.local_time.tzinfo is not None
        ):
            return False
        try:
            return GetDateTime().parse_response(result.packet) == result
        except (ProtocolError, TypeError, ValueError, AttributeError):
            return False

    def snapshot(self) -> ClockSnapshot:
        """Return fresh data only; never read, retry, or advance the scanner clock."""
        with self._lock:
            now = self._now()
            sample = self._sample
            if (
                self._session is None
                or self._blocked
                or sample is None
                or now - sample.requested_at >= STALE_AFTER
            ):
                sample = None
            return ClockSnapshot(
                local_time=sample.local_time if sample else None,
                daylight_saving=sample.daylight_saving if sample else None,
                rtc_valid=sample.rtc_valid if sample else None,
                age_seconds=now - sample.requested_at if sample else None,
                failure=self._failure,
                blocked_until_reconnect=self._blocked,
            )
