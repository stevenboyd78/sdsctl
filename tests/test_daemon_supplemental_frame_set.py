"""Offline, immutable joins only; no display or acquisition opt-in."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Event

import pytest

from sds200.scanner_display_adapter import DisplayObservationStatus
from sds200.scanner_display_supplemental import SupplementalValueStatus as Status

from .test_daemon_display_frames import accept, profile_bytes, texts
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import scan
from .test_daemon_supplemental_reads import engine as engine
from .test_daemon_supplemental_reads import wires, worker_read_both


def layouts(capture):
    return (capture.preferred, capture.simple, capture.detail)


def names(capture):
    return {value.text for frame in layouts(capture) for value in frame.values if value.text}


def test_join_is_immutable_and_shares_one_profile_sequence_and_cutoff(engine):
    feed, _, _, scanner, _ = engine
    worker_read_both(engine)
    public_before = feed.snapshot()
    capture = feed.supplemental_frame_set()
    assert capture.captured_at == 10.5
    assert {frame.sequence for frame in layouts(capture)} == {capture.sequence}
    assert {frame.age_seconds for frame in layouts(capture)} == {0.5}
    assert {frame.status for frame in layouts(capture)} == {DisplayObservationStatus.CURRENT}
    assert len({frame.profile_revision for frame in layouts(capture)}) == 1
    assert capture.supplemental.clock.age_seconds == 0
    assert capture.supplemental.favorites.age_seconds == 0.5
    assert (
        capture.supplemental.clock.status is capture.supplemental.favorites.status is Status.CURRENT
    )
    assert len(capture.supplemental.favorites.states) == 100
    assert "Current channel" in names(capture)
    assert feed.supplemental_values() == capture.supplemental
    public = feed.snapshot()
    assert public == public_before
    assert capture.endpoint_id == public["endpoint_id"]
    assert capture.stream_id == public["stream_id"]
    assert capture.session_id == public["session_id"]
    assert set(public) == {
        "schema_version",
        "endpoint_id",
        "stream_id",
        "session_id",
        "failure",
        "source_status",
        "frames",
    }
    assert "supplemental" not in public and "captured_at" not in public
    assert wires(scanner) == ["FQK", "DTM"]
    with pytest.raises(FrozenInstanceError):
        capture.captured_at = 11
    with pytest.raises(FrozenInstanceError):
        capture.detail.values[0].text = "replacement"


def test_join_does_not_start_or_renew_demand(engine):
    feed, cache, _, scanner, clock = engine
    for _ in range(10):
        capture = feed.supplemental_frame_set()
        assert "Current channel" in names(capture)
        assert (
            capture.supplemental.clock.local_time is capture.supplemental.favorites.states is None
        )
    assert scanner.reads == []
    worker_read_both(engine)
    clock.now = 15.5
    scanner.sample(scan("None", "None"))
    for _ in range(10):
        assert feed.supplemental_frame_set().supplemental.clock.local_time is None
    assert not cache.poll_once() and wires(scanner) == ["FQK", "DTM"]


def test_all_layouts_use_the_final_cutoff_even_when_construction_takes_time(engine, monkeypatch):
    feed, _, _, _, clock = engine
    worker_read_both(engine)
    original = feed._adapter.frame
    calls = []

    def frame(profile, *, now, style=None):
        calls.append((profile, now))
        clock.now += 0.01
        return original(profile, now=now, style=style)

    monkeypatch.setattr(feed._adapter, "frame", frame)
    capture = feed.supplemental_frame_set()
    assert len(calls) == 3 and all(profile is calls[0][0] for profile, _ in calls)
    assert {now for _, now in calls} == {capture.captured_at} == {10.5}
    assert {frame.age_seconds for frame in layouts(capture)} == {0.5}
    assert capture.supplemental.clock.age_seconds == 0


def test_new_psi_cannot_interleave_between_cache_and_frame_projection(engine, monkeypatch):
    feed, cache, _, scanner, _ = engine
    worker_read_both(engine)
    old = feed.supplemental_frame_set()
    original = cache.supplemental_snapshot
    captured, release, arriving = Event(), Event(), Event()

    def hold_capture():
        snapshot = original()
        captured.set()
        assert release.wait(2)
        return snapshot

    def new_observation():
        arriving.set()
        scanner.sample(scan("None", "None", "Next channel"))

    monkeypatch.setattr(cache, "supplemental_snapshot", hold_capture)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(feed.supplemental_frame_set)
        try:
            assert captured.wait(1)
            observation = pool.submit(new_observation)
            assert arriving.wait(1)
        finally:
            release.set()
        current = pending.result(timeout=1)
        observation.result(timeout=1)
    assert current == old
    assert "Next channel" not in names(current)
    newer = feed.supplemental_frame_set()
    assert newer.sequence == old.sequence + 1 and "Next channel" in names(newer)
    assert "Current channel" not in names(newer)


def test_successful_profile_reload_replaces_all_layouts_not_the_retained_set(engine, configured):
    feed, _, profile, _, _ = engine
    worker_read_both(engine)
    old = feed.supplemental_frame_set()
    configured.source_path.write_bytes(profile_bytes(simple=True).replace(b"ffffff", b"123456"))
    accept(configured)
    profile.reload()
    new = feed.supplemental_frame_set()
    assert len({frame.profile_revision for frame in layouts(new)}) == 1
    assert new.preferred.profile_revision != old.preferred.profile_revision
    assert new.sequence == old.sequence and new.supplemental == old.supplemental
    assert old.preferred.screen.layout.requested_mode.value == "detail_trunk"
    assert new.preferred.screen.layout.requested_mode.value == "simple_trunk"


def test_reconnect_requires_new_psi_and_never_reuses_cached_replies(engine):
    feed, _, _, scanner, _ = engine
    worker_read_both(engine)
    old = feed.supplemental_frame_set()
    scanner.connect_event(False)
    assert feed.supplemental_frame_set() is None
    scanner.connect_event(True)
    assert feed.supplemental_frame_set() is None
    scanner.sample(scan("None", "None", "Reconnected channel"))
    new = feed.supplemental_frame_set()
    assert new.stream_id == old.stream_id and new.session_id != old.session_id
    assert new.sequence > old.sequence
    assert "Reconnected channel" in names(new)
    assert new.supplemental.clock.local_time is new.supplemental.favorites.states is None
    assert old.supplemental.clock.status is Status.CURRENT  # A retained point-in-time value only.


def test_retired_cache_cut_cannot_attach_values_to_a_new_frame(engine, monkeypatch):
    feed, cache, _, scanner, _ = engine
    worker_read_both(engine)
    old = cache.supplemental_snapshot()
    scanner.sample(scan("None", "None", "Next channel"))
    monkeypatch.setattr(cache, "supplemental_snapshot", lambda: old)
    capture = feed.supplemental_frame_set()
    assert "Next channel" in names(capture)
    assert {frame.sequence for frame in layouts(capture)} == {old.sequence + 1}
    assert (
        capture.supplemental.clock.status
        is capture.supplemental.favorites.status
        is Status.UNAVAILABLE
    )


def test_auxiliary_frame_construction_fault_leaves_public_frames_usable(engine, monkeypatch):
    feed, _, _, _, _ = engine
    worker_read_both(engine)
    original = feed._adapter.frame

    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(feed._adapter, "frame", broken)
    assert feed.supplemental_frame_set() is None
    assert feed.quick_key_worker_status().stopped
    monkeypatch.setattr(feed._adapter, "frame", original)
    assert "Current channel" in texts(feed.snapshot())
