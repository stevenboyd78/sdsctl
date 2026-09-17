"""Passive clock lifecycle tests; no scanner or automatic polling."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sds200.commands import GetDateTime
from sds200.scanner_clock import (
    READ_TIMEOUT,
    REFRESH_INTERVAL,
    REJECTION_BACKOFF,
    STALE_AFTER,
    ClockReadTicket,
    ClockSession,
    ScannerClockSamples,
)

from .test_clock_reads import FIELDS, packet


@pytest.fixture
def fixture():
    clock = SimpleNamespace(now=10.0)
    samples = ScannerClockSamples(uuid4(), clock=lambda: clock.now)
    return samples, clock, samples.begin_session()


def reading(fields=FIELDS):
    return GetDateTime().parse_response(packet(fields))


def test_exact_clock_is_cached_without_raw_packet_timezone_or_extrapolation(fixture):
    samples, clock, session = fixture
    ticket = samples.begin_read(session)
    clock.now += 0.1
    assert samples.finish(ticket, reading())
    clock.now += 1
    snapshot = samples.snapshot()
    assert snapshot.local_time == datetime(2026, 9, 17, 21, 26, 59)
    assert snapshot.local_time.tzinfo is None
    assert snapshot.rtc_valid is True
    assert snapshot.daylight_saving == "0"
    assert snapshot.age_seconds == pytest.approx(1.1)
    assert "PRIVATE" not in repr(snapshot)
    assert "packet" not in repr(snapshot)
    assert snapshot.failure is snapshot.blocked_until_reconnect is None
    assert samples.snapshot() == snapshot  # Repeated consumers do not refresh.


def test_freshness_is_measured_from_dispatch_not_completion(fixture):
    samples, clock, session = fixture
    ticket = samples.begin_read(session)
    clock.now += 0.2
    assert samples.finish(ticket, reading())
    clock.now = ticket.requested_at + STALE_AFTER - 0.001
    assert samples.snapshot().local_time is not None
    clock.now += 0.001
    snapshot = samples.snapshot()
    assert snapshot.local_time is snapshot.age_seconds is snapshot.rtc_valid is None
    assert snapshot.daylight_saving is None


def test_invalid_rtc_clears_previously_valid_clock_without_quarantine(fixture):
    samples, clock, session = fixture
    assert samples.finish(samples.begin_read(session), reading())
    clock.now += REFRESH_INTERVAL
    invalid = reading(("0", "0000", "00", "00", "00", "00", "00", "0"))
    assert samples.finish(samples.begin_read(session), invalid)
    snapshot = samples.snapshot()
    assert snapshot.local_time is None
    assert snapshot.rtc_valid is False
    assert snapshot.age_seconds == 0
    assert snapshot.blocked_until_reconnect is None


@pytest.mark.parametrize("failure", ["timeout", "invalid_response", "read_error"])
def test_uncertain_failure_quarantines_even_after_invalidation(fixture, failure):
    samples, clock, session = fixture
    assert samples.finish(samples.begin_read(session), reading())
    clock.now += REFRESH_INTERVAL
    ticket = samples.begin_read(session)
    samples.invalidate(session)
    assert not samples.finish(ticket, failure=failure)
    clock.now += 100
    assert samples.begin_read(session) is None
    samples.invalidate(session)
    assert samples.begin_read(session) is None
    snapshot = samples.snapshot()
    assert snapshot.local_time is None
    assert snapshot.failure == snapshot.blocked_until_reconnect == failure
    samples.disconnect(session)
    new = samples.begin_session()  # Represents an actual reconnect by the owner.
    assert samples.finish(samples.begin_read(new), reading())
    assert samples.snapshot().blocked_until_reconnect is None


def test_definite_rejection_uses_backoff_not_connection_quarantine(fixture):
    samples, clock, session = fixture
    assert not samples.finish(samples.begin_read(session), failure="rejected")
    assert samples.snapshot().failure == "rejected"
    assert samples.snapshot().blocked_until_reconnect is None
    clock.now += REJECTION_BACKOFF - 0.001
    assert samples.begin_read(session) is None
    clock.now = 10.0 + REJECTION_BACKOFF
    assert samples.finish(samples.begin_read(session), reading())
    assert samples.snapshot().failure is None


@pytest.mark.parametrize(
    "result,failure", [(reading(), None), (None, "rejected"), (None, "read_error")]
)
def test_over_budget_result_is_uncertain_even_if_it_looks_successful(fixture, result, failure):
    samples, clock, session = fixture
    ticket = samples.begin_read(session)
    clock.now += READ_TIMEOUT
    assert not samples.finish(ticket, result, failure=failure)
    assert samples.snapshot().blocked_until_reconnect == "timeout"


@pytest.mark.parametrize("failure", [None, "timeout", "invalid_response"])
def test_old_session_cannot_populate_or_poison_new_session(fixture, failure):
    samples, clock, session = fixture
    old = samples.begin_read(session)
    samples.disconnect(session)
    new = samples.begin_session()
    assert samples.begin_read(new) is None  # Old in-flight read still owns the slot.
    clock.now += 1
    assert not samples.finish(old, reading() if failure is None else None, failure=failure)
    snapshot = samples.snapshot()
    assert snapshot.local_time is snapshot.blocked_until_reconnect is None
    assert samples.finish(samples.begin_read(new), reading())
    assert samples.begin_read(session) is None


def test_success_after_same_session_barrier_is_discarded(fixture):
    samples, clock, session = fixture
    ticket = samples.begin_read(session)
    samples.invalidate(session)
    assert not samples.finish(ticket, reading())
    assert samples.snapshot().local_time is None
    assert samples.snapshot().blocked_until_reconnect is None
    clock.now += REFRESH_INTERVAL
    assert samples.finish(samples.begin_read(session), reading())


def test_busy_completion_releases_slot_but_keeps_rate_limit(fixture):
    samples, clock, session = fixture
    assert not samples.finish(samples.begin_read(session))
    assert samples.snapshot().failure is None
    assert samples.begin_read(session) is None
    clock.now += REFRESH_INTERVAL
    assert samples.finish(samples.begin_read(session), reading())


def test_forged_foreign_and_duplicate_tickets_cannot_release_pending_slot(fixture):
    samples, _, session = fixture
    assert samples.begin_read(ClockSession(session.endpoint_id)) is None
    ticket = samples.begin_read(session)
    assert not samples.finish(
        ClockReadTicket(session, ticket.requested_at, ticket.epoch), reading()
    )
    assert samples.begin_read(session) is None
    assert samples.finish(ticket, reading())
    assert not samples.finish(ticket, failure="timeout")
    assert samples.snapshot().failure is None
    samples.disconnect(session)
    assert samples.begin_read(session) is None
    assert samples.snapshot().local_time is None


def test_concurrent_reservations_coalesce_to_one_ticket(fixture):
    samples, _, session = fixture
    with ThreadPoolExecutor(max_workers=8) as pool:
        tickets = list(pool.map(lambda _: samples.begin_read(session), range(32)))
    assert sum(ticket is not None for ticket in tickets) == 1


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        replace(reading(), local_time=datetime(2026, 9, 17, 21, 26, 59, tzinfo=UTC)),
        replace(reading(), local_time=datetime(2026, 9, 18)),
        replace(reading(), rtc_valid=1),
        replace(reading(), rtc_valid=False),
        replace(reading(), local_time=None),
        replace(reading(), daylight_saving="PRIVATE_MUTATED_TOKEN"),
        replace(reading(), packet=packet(("PRIVATE_BAD_PACKET",))),
        replace(reading(), packet=packet((None, *FIELDS[1:]))),
    ],
)
def test_sample_must_match_strict_reparsed_source_fields(fixture, bad):
    samples, _, session = fixture
    assert not samples.finish(samples.begin_read(session), bad)
    snapshot = samples.snapshot()
    assert snapshot.local_time is None
    assert snapshot.blocked_until_reconnect == "invalid_response"
    assert "PRIVATE" not in repr(snapshot)


@pytest.mark.parametrize("bad", [True, -1, 1e16, float("nan"), float("inf"), 9.0])
def test_clock_failure_clears_values_and_quarantines(fixture, bad):
    samples, clock, session = fixture
    assert samples.finish(samples.begin_read(session), reading())
    clock.now = bad
    with pytest.raises(ValueError, match="monotonic"):
        samples.snapshot()
    clock.now = 12
    assert samples.snapshot().local_time is None
    assert samples.snapshot().blocked_until_reconnect == "read_error"
    assert samples.begin_read(session) is None


def test_invalid_clock_on_completion_releases_slot_but_blocks_session(fixture):
    samples, clock, session = fixture
    ticket = samples.begin_read(session)
    clock.now = float("nan")
    with pytest.raises(ValueError):
        samples.finish(ticket, reading())
    clock.now = 12
    assert samples.begin_read(session) is None
    new = samples.begin_session()
    assert samples.begin_read(new) is not None


def test_clock_failure_during_pending_get_cannot_be_cleared_by_its_completion(fixture):
    samples, clock, session = fixture
    ticket = samples.begin_read(session)
    clock.now = float("nan")
    with pytest.raises(ValueError):
        samples.snapshot()
    clock.now = 10.1
    assert not samples.finish(ticket, reading())
    snapshot = samples.snapshot()
    assert snapshot.local_time is None
    assert snapshot.failure == snapshot.blocked_until_reconnect == "read_error"
    assert samples.begin_read(session) is None
    new = samples.begin_session()
    assert samples.finish(samples.begin_read(new), reading())


def test_passive_store_has_no_initial_session_and_rejects_unknown_failures():
    samples = ScannerClockSamples(uuid4())
    assert samples.snapshot().local_time is None
    assert samples.begin_read(None) is None
    assert not samples.finish(None)
    session = samples.begin_session()
    ticket = samples.begin_read(session)
    with pytest.raises(ValueError, match="failure class"):
        samples.finish(ticket, failure="PRIVATE_UNKNOWN_ERROR")
    with pytest.raises(ValueError, match="both"):
        samples.finish(ticket, reading(), failure="timeout")
    assert samples.finish(ticket, reading())


def test_endpoint_identity_must_be_explicit():
    with pytest.raises(ValueError, match="identity"):
        ScannerClockSamples("fake://scanner")
