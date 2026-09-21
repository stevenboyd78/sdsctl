"""Offline source-to-renderer joins; no hardware or public acquisition opt-in."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

import pytest

from sds200.models import FavoritesQuickKeyState as Key
from sds200.scanner_display_adapter import DisplayObservationStatus
from sds200.scanner_display_frame import project_scanner_display_frame
from sds200.scanner_display_frame_preview import render_scanner_display_frame
from sds200.scanner_display_supplemental import (
    DisplayClockValue,
    DisplayFavoritesValue,
    SupplementalDisplayValues,
)
from sds200.scanner_display_supplemental import (
    SupplementalValueStatus as Status,
)
from sds200.scanner_display_supplemental_presentation import present_supplemental_capture
from sds200.scanner_display_tui import render_mimic_terminal
from sds200.scanner_display_values import ScannerDisplayValueStatus

from .test_daemon_display_frames import accept, profile_bytes
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import scan
from .test_daemon_supplemental_reads import engine as engine
from .test_daemon_supplemental_reads import wires, worker_read_both


@pytest.fixture
def capture(engine, configured):
    feed, _, profile, _, _ = engine
    configured.source_path.write_bytes(profile_bytes().replace(b"Volume\tSquelch", b"Day\tTime"))
    accept(configured)
    profile.reload()
    worker_read_both(engine)
    return feed.supplemental_frame_set()


def present(capture, *, now=None):
    return present_supplemental_capture(capture, now=capture.captured_at if now is None else now)


def clock_cells(frame):
    values = {value.region_id: value for value in frame.values}
    return {
        slot.token: values[slot.region.id]
        for slot in frame.screen.regions
        if slot.token in {"Day", "Time"} and slot.region.option.group == 3
    }


def test_clock_follows_profile_in_all_layouts_without_io_or_mutation(capture, engine):
    feed, _, _, scanner, _ = engine
    original = feed.snapshot()
    result = present(capture)
    assert result.clock.local_time == datetime(2026, 9, 17, 21, 26, 59)
    for style in ("preferred", "simple", "detail"):
        frame = getattr(result, style)
        assert clock_cells(frame)["Day"].text == "Sep17"
        assert clock_cells(frame)["Time"].text == "21:26"
        assert clock_cells(frame)["Time"].source_fields == ("DTM",)
        assert clock_cells(getattr(capture, style))["Time"].text is None
        assert frame.screen is getattr(capture, style).screen
        assert frame.sequence == capture.sequence
        assert "Sep17" in render_scanner_display_frame(frame)
        assert (
            "21:26"
            in render_mimic_terminal(
                project_scanner_display_frame(frame), width=120, height=32
            ).plain
        )
    assert feed.snapshot() == original
    assert wires(scanner) == ["FQK", "DTM"]
    with pytest.raises(FrozenInstanceError):
        result.clock = None


def test_favorites_are_numbered_global_states_not_an_inferred_lcd_bank(capture):
    keys = tuple([Key.ENABLED, Key.DISABLED, Key.NONEXISTENT] * 33 + [Key.ENABLED])
    capture = replace(
        capture,
        supplemental=replace(
            capture.supplemental, favorites=DisplayFavoritesValue(Status.CURRENT, keys, 0)
        ),
    )
    result = present(capture)
    assert len(result.favorites_rows) == 10
    assert result.favorites_rows[0].startswith("00:On  01:Off  02:Absent")
    assert result.favorites_rows[-1].endswith("99:On")
    all_text = " ".join(result.favorites_rows)
    for key in range(100):
        assert all_text.count(f"{key:02}:") == 1
    assert not any(prefix in all_text for prefix in ("F0:", "S0:", "D0:"))
    for style in ("preferred", "simple", "detail"):
        assert all(value.source_fields != ("FQK",) for value in getattr(result, style).values)


@pytest.mark.parametrize(
    "local,date,time",
    [
        (datetime(2024, 2, 29, 0, 0), "Feb29", "00:00"),
        (datetime(2026, 1, 1, 4, 12), "Jan01", "04:12"),
        (datetime(2026, 12, 31, 23, 59, 59), "Dec31", "23:59"),
    ],
)
def test_clock_uses_fixed_months_24h_and_never_extrapolates(capture, local, date, time):
    capture = replace(
        capture,
        supplemental=replace(
            capture.supplemental, clock=DisplayClockValue(Status.CURRENT, local, 0)
        ),
    )
    for delay in (0, 0.999, 2):
        result = present(capture, now=capture.captured_at + delay)
        assert result.clock.local_time == local
        assert clock_cells(result.detail)["Day"].text == date
        assert clock_cells(result.detail)["Time"].text == time
        assert result.clock.age_seconds == pytest.approx(delay)


@pytest.mark.parametrize("older", ["clock", "favorites"])
def test_sources_expire_independently_without_hiding_fresh_psi(capture, older):
    aux = replace(
        capture.supplemental,
        **{older: replace(getattr(capture.supplemental, older), age_seconds=4.5)},
    )
    result = present(replace(capture, supplemental=aux), now=capture.captured_at + 0.5)
    assert getattr(result, older).status is Status.STALE
    assert getattr(result, "favorites" if older == "clock" else "clock").status is Status.CURRENT
    assert result.detail.status is DisplayObservationStatus.CURRENT
    assert "Current channel" in {value.text for value in result.detail.values}
    assert bool(clock_cells(result.detail)["Time"].text) is (older != "clock")
    assert bool(result.favorites_rows) is (older != "favorites")


def test_stale_psi_clears_every_value_even_when_supplemental_is_fresh(capture):
    result = present(capture, now=capture.captured_at + 4.5)
    assert result.clock.status is result.favorites.status is Status.STALE
    assert result.clock.local_time is result.favorites.states is None
    assert not result.favorites_rows
    for frame in (result.preferred, result.simple, result.detail):
        assert frame.status is DisplayObservationStatus.STALE
        assert all(value.text is None for value in frame.values)
        assert not any(project_scanner_display_frame(frame)["indicators"].values())


@pytest.mark.parametrize("state", [s for s in Status if s is not Status.CURRENT])
def test_noncurrent_status_never_leaks_embedded_values(capture, state):
    result = present(
        replace(
            capture,
            supplemental=SupplementalDisplayValues(
                replace(capture.supplemental.clock, status=state),
                replace(capture.supplemental.favorites, status=state),
            ),
        )
    )
    assert result.clock.status is result.favorites.status is state
    assert result.clock.local_time is result.favorites.states is None
    assert not result.favorites_rows
    assert all(value.text is None for value in clock_cells(result.detail).values())


@pytest.mark.parametrize(
    "change",
    [
        {"local_time": datetime(2026, 9, 17, tzinfo=UTC)},
        {"local_time": datetime(2026, 9, 17, microsecond=1)},
        {"local_time": datetime(2026, 9, 17, fold=1)},
        {"local_time": "PRIVATE<svg>"},
        {"age_seconds": True},
        {"age_seconds": -1},
        {"age_seconds": float("nan")},
        {"age_seconds": float("inf")},
        {"age_seconds": 10**1000},
    ],
)
def test_invalid_clock_fails_only_auxiliary_field(capture, change):
    result = present(
        replace(
            capture,
            supplemental=replace(
                capture.supplemental, clock=replace(capture.supplemental.clock, **change)
            ),
        )
    )
    assert result.clock.status is Status.INVALID_SOURCE
    assert result.favorites.status is Status.CURRENT
    assert "PRIVATE" not in render_scanner_display_frame(result.detail)
    assert all(value.text is None for value in clock_cells(result.detail).values())


@pytest.mark.parametrize("states", [[Key.ENABLED] * 100, (Key.ENABLED,) * 99, (2,) * 100, None])
def test_invalid_favorites_never_become_absent_or_off(capture, states):
    result = present(
        replace(
            capture,
            supplemental=replace(
                capture.supplemental,
                favorites=replace(capture.supplemental.favorites, states=states),
            ),
        )
    )
    assert result.favorites.status is Status.INVALID_SOURCE and result.favorites_rows == ()
    assert result.clock.status is Status.CURRENT


@pytest.mark.parametrize(
    "now", [True, -1, 10.49, float("nan"), float("inf"), 1e16, 10**1000, "PRIVATE"]
)
def test_invalid_cutoff_is_rejected(capture, now):
    with pytest.raises(ValueError, match="monotonic") as failure:
        present(capture, now=now)
    assert "PRIVATE" not in str(failure.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sequence", True),
        ("sequence", 2**53),
        ("sequence", 90),
        ("profile_invalidation", -1),
        ("session_id", "PRIVATE"),
        ("endpoint_id", "00000000-0000-0000-0000-000000000099"),
    ],
)
def test_capture_identity_and_sequence_are_checked(capture, field, value):
    with pytest.raises(ValueError):
        present(replace(capture, **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("sequence", 999),
        ("profile_revision", "f" * 64),
        ("age_seconds", 1),
        ("age_seconds", True),
        ("profile_refresh_pending", True),
        ("status", DisplayObservationStatus.STALE),
    ],
)
def test_layouts_cannot_mix_different_cuts(capture, field, value):
    with pytest.raises(ValueError):
        present(replace(capture, simple=replace(capture.simple, **{field: value})))


def test_reconnect_mode_change_and_no_demand_leave_no_live_presentation(engine, capture):
    feed, cache, _, scanner, clock = engine
    reads = wires(scanner)
    for _ in range(10):
        assert present(feed.supplemental_frame_set()).clock.status is Status.CURRENT
    scanner.connect_event(False)
    assert feed.supplemental_frame_set() is None
    scanner.connect_event(True)
    assert feed.supplemental_frame_set() is None
    scanner.sample(scan("None", "None"))
    result = present(feed.supplemental_frame_set())
    assert result.clock.local_time is result.favorites.states is None
    clock.now += 10
    assert not cache.poll_once() and wires(scanner) == reads


def test_no_private_paths_raw_packets_or_zone_claims_cross_presentation(capture):
    rendered = json.dumps(project_scanner_display_frame(present(capture).detail))
    for text in ("PRIVATE", "udp://", "profile.cfg", "<ScannerInfo", "UTC", "+0000"):
        assert text not in rendered


@pytest.mark.parametrize("replacement", [b"Empty\tEmpty", b"Volume\tSquelch", b"Day\tEmpty"])
def test_profile_choice_is_never_replaced_by_an_extra_clock(engine, configured, replacement):
    feed, _, profile, _, _ = engine
    configured.source_path.write_bytes(profile_bytes().replace(b"Volume\tSquelch", replacement))
    accept(configured)
    profile.reload()
    worker_read_both(engine)
    sample = feed.supplemental_frame_set()
    result = present(sample)
    for style in ("preferred", "simple", "detail"):
        original = getattr(sample, style)
        actual = getattr(result, style)
        for before, after in zip(original.values, actual.values, strict=True):
            if before.region_id not in {v.region_id for v in clock_cells(actual).values()}:
                assert before == after
        assert len(clock_cells(actual)) == (1 if replacement == b"Day\tEmpty" else 0)


@pytest.mark.parametrize(
    "state",
    [
        ScannerDisplayValueStatus.EMPTY,
        ScannerDisplayValueStatus.BLANK,
        ScannerDisplayValueStatus.CONFIGURATION_UNAVAILABLE,
        ScannerDisplayValueStatus.INVALID_REGION,
        ScannerDisplayValueStatus.INVALID_SOURCE,
    ],
)
def test_clock_does_not_resurrect_rejected_or_empty_profile_slots(capture, state):
    def alter(frame):
        ids = {value.region_id for value in clock_cells(frame).values()}
        return replace(
            frame,
            values=tuple(
                replace(value, status=state) if value.region_id in ids else value
                for value in frame.values
            ),
        )

    result = present(
        replace(
            capture,
            **{
                style: alter(getattr(capture, style)) for style in ("preferred", "simple", "detail")
            },
        )
    )
    for frame in (result.preferred, result.simple, result.detail):
        assert all(
            value.status is state and value.text is None for value in clock_cells(frame).values()
        )
