"""Synthetic candidate projection: no live scanner, public fields or LCD claims."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from threading import Event

import pytest

from sds200.daemon_quick_keys import QuickKeySession
from sds200.exceptions import CommandTimeoutError
from sds200.scanner_display_supplemental import SupplementalValueStatus as Status
from sds200.scanner_display_supplemental import (
    project_supplemental_display_values,
)
from sds200.scanner_quick_keys import QuickKeySelection

from .test_daemon_display_frames import configured as configured
from .test_daemon_display_frames import info, texts
from .test_daemon_quick_key_worker import scan, wait_for
from .test_daemon_quick_keys import activate
from .test_daemon_supplemental_reads import engine as engine
from .test_daemon_supplemental_reads import read_both, wires, worker_read_both
from .test_daemon_supplemental_reads import shared as shared
from .test_scanner_clock import reading


def project(snapshot, **kwargs):
    return project_supplemental_display_values(
        snapshot,
        **dict(session=snapshot.session, sequence=snapshot.sequence, now=snapshot.captured_at)
        | kwargs,
    )


@pytest.fixture
def sample(shared):
    read_both(shared)
    return shared[0].supplemental_snapshot()


def test_snapshot_has_one_cutoff_independent_ages_and_no_io(shared, sample):
    cache, session, scanner, clock = shared
    assert sample.session is session and sample.sequence == 1 and sample.captured_at == 10.5
    assert sample.quick_keys.banks[0].age_seconds == 0.5
    assert sample.clock.age_seconds == 0
    for _ in range(20):
        assert cache.supplemental_snapshot() == sample
    calls = []

    def cutoff():
        calls.append(True)
        return 11.0

    cache._clock = cutoff
    # A separate clock read would produce a contradictory cutoff or fail.
    cache._clock_samples._clock = lambda: pytest.fail("sample store read its own clock")
    view = cache.supplemental_snapshot()
    assert len(calls) == 1
    assert view.quick_keys.banks[0].age_seconds == 1
    assert view.clock.age_seconds == 0.5
    assert wires(scanner) == ["FQK", "DTM"]
    clock.now = 11
    cache._clock = lambda: clock.now


def test_projection_preserves_exact_local_clock_and_all_global_keys(sample):
    view = project(sample, now=11.0)
    assert view.clock.status is view.favorites.status is Status.CURRENT
    assert view.clock.local_time == datetime(2026, 9, 17, 21, 26, 59)
    assert view.clock.local_time.tzinfo is None
    assert view.clock.age_seconds == 0.5 and view.favorites.age_seconds == 1.0
    assert view.favorites.states == sample.quick_keys.banks[0].states
    assert len(view.favorites.states) == 100
    assert not hasattr(view.favorites, "decade") and not hasattr(view.favorites, "glyphs")
    assert not hasattr(view.clock, "utc_offset")
    assert "PRIVATE" not in repr(view) and "fake://" not in repr(view)
    with pytest.raises(FrozenInstanceError):
        view.clock.age_seconds = 0


def test_expiry_is_independent_and_never_advances_scanner_time(sample):
    view = project(sample, now=14.999)
    assert view.clock.local_time == sample.clock.local_time
    assert view.favorites.status is Status.CURRENT
    view = project(sample, now=15.0)
    assert view.favorites.status is Status.STALE and view.favorites.states is None
    assert view.clock.status is Status.CURRENT
    view = project(sample, now=15.5)
    assert view.clock.status is Status.STALE and view.clock.local_time is None


@pytest.mark.parametrize("mismatch", ["copy", "different", "older", "newer", "bool"])
def test_session_identity_and_exact_psi_sequence_are_required(sample, mismatch):
    kwargs = {}
    if mismatch in ("copy", "different"):
        kwargs["session"] = QuickKeySession(sample.session.endpoint_id)
    elif mismatch == "bool":
        sample = replace(sample, sequence=True)
        kwargs["sequence"] = 1
    else:
        kwargs["sequence"] = sample.sequence + (-1 if mismatch == "older" else 1)
    result = project(sample, **kwargs)
    assert result.clock.local_time is result.favorites.states is None


@pytest.mark.parametrize("field", ["now", "captured_at"])
@pytest.mark.parametrize("bad", [True, "PRIVATE", None, -1, float("nan"), float("inf"), 1e16])
def test_invalid_monotonic_input_is_rejected_without_echo(sample, field, bad):
    kwargs = {}
    if field == "now":
        kwargs["now"] = bad
    else:
        sample = replace(sample, captured_at=bad)
        kwargs["now"] = 11.0
    with pytest.raises(ValueError, match="bounded monotonic") as failure:
        project(sample, **kwargs)
    assert "PRIVATE" not in str(failure.value)


def test_future_capture_and_reversed_time_are_rejected(sample):
    with pytest.raises(ValueError):
        project(sample, now=10.49)


@pytest.mark.parametrize(
    "updates",
    [
        {"local_time": datetime(2026, 9, 17, tzinfo=UTC)},
        {"local_time": datetime(2026, 9, 17, microsecond=1)},
        {"local_time": datetime(2026, 9, 17, fold=1)},
        {"local_time": "PRIVATE"},
        {"local_time": None},
        {"rtc_valid": 1},
        {"rtc_valid": False},
        {"age_seconds": -1},
        {"age_seconds": True},
        {"age_seconds": float("nan")},
    ],
)
def test_invalid_clock_is_suppressed_without_hiding_favorites(sample, updates):
    result = project(replace(sample, clock=replace(sample.clock, **updates)))
    assert result.clock.status is Status.INVALID_SOURCE and result.clock.local_time is None
    assert result.favorites.status is Status.CURRENT


def test_invalid_rtc_is_not_disabled_or_a_fabricated_time(sample):
    result = project(replace(sample, clock=replace(sample.clock, rtc_valid=False, local_time=None)))
    assert result.clock.status is Status.INVALID_RTC and result.clock.local_time is None
    assert project(replace(sample, clock=None)).clock.status is Status.DISABLED


@pytest.mark.parametrize("failure", ["timeout", "rejected", "invalid_response", "read_error"])
def test_clock_failure_has_no_value_and_does_not_hide_independent_favorites(sample, failure):
    result = project(replace(sample, clock=replace(sample.clock, failure=failure)))
    assert result.clock.local_time is None and result.favorites.status is Status.CURRENT


@pytest.mark.parametrize("failure", ["timeout", "invalid_response", "read_error"])
def test_shared_quarantine_suppresses_both_even_if_samples_are_retained(sample, failure):
    keys = replace(sample.quick_keys, blocked_until_reconnect=failure)
    result = project(replace(sample, quick_keys=keys))
    assert result.clock.status is result.favorites.status is Status.BLOCKED
    assert result.clock.local_time is result.favorites.states is None


@pytest.mark.parametrize("change", ["duplicate", "integers", "short", "list", "age", "missing"])
def test_invalid_favorites_does_not_hide_valid_clock(sample, change):
    banks = sample.quick_keys.banks
    if change == "duplicate":
        banks = (banks[0], banks[0])
    else:
        update = {
            "integers": {"states": tuple(int(s) for s in banks[0].states)},
            "short": {"states": banks[0].states[:10]},
            "list": {"states": list(banks[0].states)},
            "age": {"age_seconds": float("inf")},
            "missing": {"age_seconds": None},
        }[change]
        banks = (replace(banks[0], **update), *banks[1:])
    result = project(replace(sample, quick_keys=replace(sample.quick_keys, banks=banks)))
    assert result.favorites.status is Status.INVALID_SOURCE and result.favorites.states is None
    assert result.clock.status is Status.CURRENT


def test_projection_does_not_infer_scoped_bank_or_decade_from_selection(sample):
    for selection in (
        QuickKeySelection(None, None),
        QuickKeySelection(0, 0),
        QuickKeySelection(99, 99),
    ):
        assert project(
            replace(sample, quick_keys=replace(sample.quick_keys, selection=selection))
        ) == project(sample)


def test_new_snapshot_never_renews_demand(shared, sample):
    cache, _, scanner, clock = shared
    clock.now = 15
    for _ in range(10):
        result = cache.supplemental_snapshot()
        assert not result.quick_keys.active and result.clock.local_time is None
    assert not cache.poll_once() and wires(scanner) == ["FQK", "DTM"]


def test_inflight_get_does_not_block_snapshots_and_cannot_revive_old_context(shared):
    cache, session, scanner, clock = shared
    activate(cache, session)
    cache.poll_once()
    clock.now = 10.5
    entered, release = Event(), Event()

    def delayed():
        entered.set()
        assert release.wait(2)
        return reading()

    scanner.clock_reply = delayed
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(cache.poll_once)
        try:
            assert entered.wait(1)
            snapshot = pool.submit(cache.supplemental_snapshot).result(timeout=0.5)
            assert snapshot.clock.local_time is None
            cache.disconnect(session)
            new_session = cache.begin_session()
            activate(cache, new_session)
            assert project(snapshot, session=new_session).favorites.states is None
        finally:
            release.set()
        assert pending.result(timeout=1)
    assert cache.supplemental_snapshot().clock.local_time is None


def test_feed_candidate_projection_is_not_public_and_never_renews_demand(engine):
    feed, cache, _, scanner, clock = engine
    for _ in range(10):
        result = feed.supplemental_values()
        assert result.clock.local_time is result.favorites.states is None
    assert scanner.reads == []
    worker_read_both(engine)
    assert feed.supplemental_values().clock.status is Status.CURRENT
    assert "clock" not in feed.snapshot() and "supplemental" not in feed.snapshot()
    clock.now = 15.5
    scanner.sample(scan("None", "None"))
    for _ in range(10):
        assert feed.supplemental_values().clock.local_time is None
    assert not cache.poll_once() and wires(scanner) == ["FQK", "DTM"]


@pytest.mark.parametrize("action", ["popup", "waterfall", "disconnect", "close", "target"])
def test_feed_context_barriers_suppress_both(engine, action):
    feed, _, _, scanner, _ = engine
    worker_read_both(engine)
    if action == "popup":
        scanner.sample(info(content="<PopupScreen/>"))
    elif action == "waterfall":
        scanner.sample(info("waterfall", ""))
    elif action == "disconnect":
        scanner.connect_event(False)
    elif action == "close":
        feed.close()
    else:
        scanner.endpoint = "fake://other-owner"
    assert feed.supplemental_values() is None


def test_auxiliary_projection_fault_does_not_blank_current_psi(engine, monkeypatch):
    feed, cache, _, scanner, _ = engine
    worker_read_both(engine)

    def broken():
        raise RuntimeError("PRIVATE_EXCEPTION")

    monkeypatch.setattr(cache, "supplemental_snapshot", broken)
    assert feed.supplemental_values() is None
    assert "Current channel" in texts(feed.snapshot())
    assert feed.quick_key_worker_status().stopped


def test_shared_read_timeout_leaves_normal_psi_intact(engine):
    feed, _, _, scanner, _ = engine

    def timeout(*_):
        raise CommandTimeoutError("PRIVATE_EXCEPTION")

    scanner.reply = timeout
    feed.snapshot()
    wait_for(lambda: feed.quick_key_snapshot().blocked_until_reconnect == "timeout")
    result = feed.supplemental_values()
    assert result.clock.status is result.favorites.status is Status.BLOCKED
    assert "Current channel" in texts(feed.snapshot())


def test_profile_repair_between_views_requires_new_psi(engine, configured):
    from sds200.scanner_display_profile_storage import DisplayProfileStorageError

    feed, cache, profile, scanner, clock = engine
    worker_read_both(engine)
    path = configured.state_directory / "accepted-profile.json"
    original = path.read_bytes()
    path.write_bytes(b"invalid")
    with pytest.raises(DisplayProfileStorageError):
        profile.reload()
    path.write_bytes(original)
    profile.reload()
    assert feed.supplemental_values() is None
    assert not cache.supplemental_snapshot().quick_keys.active
    scanner.sample(scan("None", "None"))
    view = feed.supplemental_values()
    assert view.clock.local_time is view.favorites.states is None
    # New PSI alone is not renewed display demand.
    clock.now = 12
    assert not cache.poll_once() and wires(scanner) == ["FQK", "DTM"]


def test_retired_profile_context_cannot_publish_values_or_lower_barrier(engine):
    feed, _, profile, _, _ = engine
    worker_read_both(engine)
    original = profile.frame_context
    context = original()
    feed._profile_invalidation = context[-1] + 1
    try:
        profile.frame_context = lambda: context
        assert feed.supplemental_values() is None
        assert feed._profile_invalidation == context[-1] + 1
    finally:
        profile.frame_context = original


def test_slow_profile_context_does_not_block_psi_callback(engine, monkeypatch):
    feed, _, profile, scanner, _ = engine
    worker_read_both(engine)
    original = profile.frame_context
    entered, release = Event(), Event()

    def slow():
        entered.set()
        assert release.wait(2)
        return original()

    monkeypatch.setattr(profile, "frame_context", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(feed.supplemental_values)
        try:
            assert entered.wait(1)
            pool.submit(scanner.sample, scan("None", "None", "Fresh channel")).result(timeout=0.5)
        finally:
            release.set()
        assert pending.result(timeout=1).clock.status is Status.CURRENT
    assert "Fresh channel" in texts(feed.snapshot())


def test_feed_reads_exact_sequence_not_a_retained_cut(engine, monkeypatch):
    feed, cache, _, scanner, _ = engine
    worker_read_both(engine)
    old = cache.supplemental_snapshot()
    scanner.sample(scan("None", "None", "Next channel"))
    monkeypatch.setattr(cache, "supplemental_snapshot", lambda: old)
    view = feed.supplemental_values()
    assert view.clock.local_time is view.favorites.states is None
    assert "Next channel" in texts(feed.snapshot())


def test_closed_feed_never_retains_projection(engine):
    feed, _, _, _, _ = engine
    worker_read_both(engine)
    feed.close()
    for _ in range(5):
        assert feed.supplemental_values() is None


@pytest.mark.parametrize("failure", ["timeout", "invalid_response", "read_error"])
def test_clock_quarantine_independently_blocks_clock(sample, failure):
    result = project(replace(sample, clock=replace(sample.clock, blocked_until_reconnect=failure)))
    assert result.clock.status is Status.BLOCKED and result.clock.local_time is None


@pytest.mark.parametrize("field", ["states", "failure"])
def test_unavailable_favorites_do_not_become_all_off(sample, field):
    bank = replace(sample.quick_keys.banks[0], **{field: None if field == "states" else "rejected"})
    result = project(replace(sample, quick_keys=replace(sample.quick_keys, banks=(bank,))))
    assert result.favorites.status is Status.UNAVAILABLE and result.favorites.states is None


def test_missing_and_malformed_banks_remain_separate_from_clock(sample):
    for banks in ((), (object(),), [sample.quick_keys.banks[0]]):
        result = project(replace(sample, quick_keys=replace(sample.quick_keys, banks=banks)))
        assert result.favorites.states is None and result.clock.status is Status.CURRENT


def test_invalid_snapshot_shapes_and_context_types_are_sanitized(sample):
    for changes in ({"session": None}, {"sequence": True}, {"sequence": -1}):
        with pytest.raises(ValueError):
            project(sample, **changes)
    with pytest.raises(ValueError):
        project(replace(sample, quick_keys=object()))
    result = project(replace(sample, clock=object()))
    assert result.clock.status is Status.INVALID_SOURCE


def test_explicit_cutoff_rejects_regression_and_cannot_revive_clock(shared, sample):
    cache, _, _, clock = shared
    clock.now = 12
    cache.supplemental_snapshot()
    with pytest.raises(ValueError):
        cache._clock_samples._snapshot_at(11)
    assert cache.clock_snapshot().local_time is None
