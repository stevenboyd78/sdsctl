"""Single-consumer, transport-free freshness guard for the delivery candidate.

Not wired into any live client. Keep one instance for the lifetime of a verified
owner context, including layout/visibility changes. Context transitions require
an explicitly new guard, never an automatic rebind from an unsolicited reply.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from .models import FavoritesQuickKeyState
from .scanner_display_supplemental import (
    DisplayClockValue,
    DisplayFavoritesValue,
    SupplementalDisplayValues,
)
from .scanner_display_supplemental import SupplementalValueStatus as Status
from .scanner_display_supplemental_wire import (
    LIFETIME,
    SupplementalContext,
    SupplementalWireValue,
    bounded_seconds,
    decode_supplemental_context,
    decode_supplemental_delivery,
)


@dataclass(frozen=True, slots=True, eq=False)
class SupplementalRequest:
    started_at: float


@dataclass(slots=True)
class _Source:
    sequence: int = 0  # High-water mark survives clearing/expiry/suspension.
    value: str | None = None
    deadline: float = 0.0
    status: Status = Status.UNAVAILABLE

    def clear(self, status: Status) -> None:
        self.value, self.deadline, self.status = None, 0.0, status

    def expire(self, now: float) -> None:
        if self.value is not None and now >= self.deadline:
            self.clear(Status.STALE)

    def retire(self, sample: SupplementalWireValue) -> None:
        if sample.sample_sequence is not None:
            self.sequence = max(self.sequence, sample.sample_sequence)
        self.clear(Status.STALE)

    def accept(self, sample: SupplementalWireValue, started: float, now: float) -> None:
        self.expire(now)
        if sample.status is not Status.CURRENT:
            self.clear(sample.status)
            return
        assert sample.sample_sequence is not None and sample.age_seconds is not None
        sequence = sample.sample_sequence
        deadline = started + LIFETIME - sample.age_seconds
        if sequence < self.sequence:
            self.clear(Status.INVALID_SOURCE)
            return
        if sequence == self.sequence:
            if self.value is None:  # A retired sample never revives.
                return
            if self.value != sample.value:
                self.clear(Status.INVALID_SOURCE)
                return
            self.deadline = min(self.deadline, deadline)
        else:
            self.sequence, self.value = sequence, sample.value
            self.deadline, self.status = deadline, Status.CURRENT
        self.expire(now)


class SupplementalConsumer:
    """One event-loop owner; no worker, network, wall clock or rendering choice."""

    def __init__(self, context: SupplementalContext) -> None:
        if type(context) is not SupplementalContext:
            raise ValueError("An explicit verified supplemental context is required.")
        self._context = decode_supplemental_context(asdict(context))
        self._clock, self._favorites = _Source(), _Source()
        self._pending: SupplementalRequest | None = None
        self._latest_time = 0.0
        self._closed = False
        self._psi_sequence = -1
        self._psi_deadline = 0.0
        self._psi_retired = False

    @property
    def context(self) -> SupplementalContext:
        return self._context

    def _clear(self, status: Status) -> None:
        self._clock.clear(status)
        self._favorites.clear(status)

    def _now(self, now: float) -> float:
        try:
            moment = bounded_seconds(now)
            if moment < self._latest_time:
                raise ValueError
        except ValueError:
            self.close()
            raise ValueError("A bounded, nondecreasing consumer clock is required.") from None
        self._latest_time = moment
        return moment

    def _expire(self, now: float) -> None:
        self._clock.expire(now)
        self._favorites.expire(now)
        if self._psi_sequence >= 0 and now >= self._psi_deadline:
            self._psi_retired = True
            self._clear(Status.STALE)

    def begin(self, *, now: float) -> SupplementalRequest:
        """Replace any pending request; only this exact ticket may deliver."""
        moment = self._now(now)
        if self._closed:
            raise ValueError("Supplemental consumer is closed.")
        self._expire(moment)
        self._pending = SupplementalRequest(moment)
        return self._pending

    def accept(self, ticket: SupplementalRequest, payload: object, *, now: float) -> bool:
        """Reject old/foreign callbacks without clearing newer current values."""
        moment = self._now(now)
        if self._closed or ticket is not self._pending or self._pending is None:
            return False
        self._pending = None
        self._expire(moment)
        try:
            delivery = decode_supplemental_delivery(payload)
            if delivery.context != self.context or delivery.psi_sequence < self._psi_sequence:
                raise ValueError
        except ValueError:
            self._clear(Status.INVALID_SOURCE)
            return False
        deadline = ticket.started_at + LIFETIME - delivery.psi_age_seconds
        if delivery.psi_sequence == self._psi_sequence:
            self._psi_deadline = min(self._psi_deadline, deadline)
        else:
            self._psi_sequence = delivery.psi_sequence
            self._psi_deadline = deadline
            self._psi_retired = False
        if self._psi_retired or moment >= self._psi_deadline:
            self._psi_retired = True
            self._clock.retire(delivery.clock)
            self._favorites.retire(delivery.favorites)
            return False
        self._clock.accept(delivery.clock, ticket.started_at, moment)
        self._favorites.accept(delivery.favorites, ticket.started_at, moment)
        return True

    def snapshot(self, *, now: float) -> SupplementalDisplayValues:
        moment = self._now(now)
        self._expire(moment)
        c, f = self._clock, self._favorites
        return SupplementalDisplayValues(
            DisplayClockValue(c.status)
            if c.value is None
            else DisplayClockValue(
                Status.CURRENT,
                datetime.fromisoformat(c.value),
                max(0.0, LIFETIME - (c.deadline - moment)),
                c.sequence,
            ),
            DisplayFavoritesValue(f.status)
            if f.value is None
            else DisplayFavoritesValue(
                Status.CURRENT,
                tuple(FavoritesQuickKeyState(int(key)) for key in f.value),
                max(0.0, LIFETIME - (f.deadline - moment)),
                f.sequence,
            ),
        )

    def suspend(self) -> None:
        """Hide/stop: invalidate callbacks and samples, retain high-water marks."""
        self._pending = None
        self._psi_retired = self._psi_sequence >= 0
        self._clear(Status.UNAVAILABLE)

    def close(self) -> None:
        self.suspend()
        self._closed = True
