"""Internal context-bound leases; fake owner only, no routes or hardware."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sds200.daemon_display_frames import DaemonDisplayFrames
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_quick_keys import DaemonQuickKeyCache, DaemonQuickKeyWorker
from sds200.exceptions import CommandTimeoutError
from sds200.scanner_display_profile_storage import DisplayProfileStorageError
from sds200.scanner_display_supplemental_transport import SupplementalDeliveryService

from .test_daemon_display_frames import accept, info, profile_bytes
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import scan
from .test_daemon_supplemental_reads import SupplementalScanner, wires


@pytest.fixture
def explicit(configured, monkeypatch):
    # Scheduling is exercised by native/coordinator tests. These tests drive one
    # cache synchronously, without racing a second background scheduler.
    monkeypatch.setattr(DaemonQuickKeyWorker, "start", lambda _: True)
    scanner, clock = SupplementalScanner(), SimpleNamespace(now=10.0)
    profile = DaemonDisplayProfile(configured, lambda: scanner.endpoint)
    cache = DaemonQuickKeyCache(
        scanner,
        configured.binding.endpoint_id,
        scanner.endpoint,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
    )
    feed = DaemonDisplayFrames(
        profile,
        scanner,
        quick_keys=cache,
        clock=lambda: clock.now,
        legacy_snapshot_demand=False,
    )
    feed.start()
    scanner.connect_event(True)
    scanner.sample(scan("None", "None"))
    try:
        yield SimpleNamespace(feed=feed, cache=cache, profile=profile, scanner=scanner, clock=clock)
    finally:
        feed.close()


def ticket(rig):
    value = rig.feed.supplemental_frame_set()
    assert value is not None
    return value


def renew(rig):
    return rig.feed.renew_supplemental_demand(ticket(rig))


def test_all_delivery_paths_are_passive_without_explicit_demand(explicit):
    rig = explicit
    service = SupplementalDeliveryService(rig.feed, clock=lambda: rig.clock.now)
    for _ in range(20):
        rig.feed.snapshot()
        rig.feed.supplemental_values()
        service.frame(service.context()["context"])
    assert not rig.cache.poll_once()
    assert rig.scanner.reads == []
    initial = ticket(rig)
    revision = renew(rig)
    assert revision == initial.context_revision + 1
    assert ticket(rig).context_revision == revision
    assert rig.scanner.reads == []  # Granting a lease still performs no I/O.
    assert rig.cache.poll_once()
    rig.clock.now = 10.5
    assert rig.cache.poll_once()
    assert wires(rig.scanner) == ["FQK", "DTM"]


def test_snapshots_do_not_extend_or_cancel_the_explicit_shared_lease(explicit):
    rig = explicit
    renew(rig)
    assert rig.cache.poll_once()
    for now in (11, 12, 13, 14, 14.999):
        rig.clock.now = now
        rig.scanner.sample(scan("None", "None"))
        rig.feed.snapshot()
        assert rig.cache.snapshot().active
    rig.clock.now = 15
    rig.scanner.sample(scan("None", "None"))
    rig.feed.snapshot()
    assert not rig.cache.snapshot().active
    assert not rig.cache.poll_once()
    assert wires(rig.scanner) == ["FQK"]


def test_concurrent_first_renewal_has_one_winner_and_new_context(explicit):
    rig = explicit
    captured = ticket(rig)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: rig.feed.renew_supplemental_demand(captured), range(40)))
    assert outcomes.count(captured.context_revision + 1) == 1
    assert outcomes.count(None) == 39
    fresh = ticket(rig)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: rig.feed.renew_supplemental_demand(fresh), range(40)))
    assert outcomes == [fresh.context_revision] * 40
    assert rig.cache.poll_once() and not rig.cache.poll_once()
    assert wires(rig.scanner) == ["FQK"]


def test_expired_lease_retires_old_samples_without_resetting_read_schedule(explicit):
    rig = explicit
    renew(rig)
    assert rig.cache.poll_once()
    rig.clock.now = 10.5
    assert rig.cache.poll_once()
    old = ticket(rig)
    rig.clock.now = 15
    rig.scanner.sample(scan("None", "None"))
    # Recovering previously stale PSI also advances the context, independently
    # from the expired lease. Renew the newly negotiated cut, not the old one.
    recovered = ticket(rig)
    assert renew(rig) == recovered.context_revision + 1
    assert rig.feed.renew_supplemental_demand(old) is None
    capture = ticket(rig)
    assert capture.supplemental.clock.sample_sequence is None
    assert capture.supplemental.favorites.sample_sequence is None
    assert rig.cache.poll_once()
    assert ticket(rig).supplemental.favorites.sample_sequence == 2


@pytest.mark.parametrize(
    "field", ["endpoint_id", "stream_id", "session_id", "profile_invalidation", "context_revision"]
)
def test_wrong_binding_cannot_renew(explicit, field):
    rig = explicit
    captured = ticket(rig)
    wrong = 999 if field.endswith("revision") or field.endswith("invalidation") else "wrong"
    assert rig.feed.renew_supplemental_demand(replace(captured, **{field: wrong})) is None
    assert not rig.cache.snapshot().active and rig.scanner.reads == []


@pytest.mark.parametrize(
    "barrier", ["disconnect", "reconnect", "stale", "mode", "profile", "repair", "close"]
)
def test_context_barriers_refuse_old_ticket(explicit, configured, barrier):
    rig = explicit
    captured = ticket(rig)
    if barrier in {"disconnect", "reconnect"}:
        rig.scanner.connect_event(False)
        if barrier == "reconnect":
            rig.scanner.connect_event(True)
            rig.scanner.sample(scan())
    elif barrier == "stale":
        rig.clock.now += 5
    elif barrier == "mode":
        rig.scanner.sample(info("waterfall", ""))
    elif barrier == "profile":
        configured.source_path.write_bytes(profile_bytes(simple=True))
        accept(configured)
        rig.profile.reload()
    elif barrier == "repair":
        path = configured.state_directory / "accepted-profile.json"
        original = path.read_bytes()
        path.write_bytes(b"broken")
        with pytest.raises(DisplayProfileStorageError):
            rig.profile.reload()
        path.write_bytes(original)
        rig.profile.reload()
    else:
        rig.feed.close()
    assert rig.feed.renew_supplemental_demand(captured) is None
    assert not rig.cache.snapshot().active and rig.scanner.reads == []


def test_failed_or_changed_source_cannot_renew(explicit, configured):
    rig = explicit
    captured = ticket(rig)
    configured.source_path.write_bytes(profile_bytes(simple=True))
    rig.profile.reload()  # Source mismatch without accepting a new revision.
    assert rig.feed.renew_supplemental_demand(captured) is None
    assert not rig.cache.poll_once()


def test_quarantine_cannot_be_lifted_by_new_demand(explicit):
    rig = explicit
    renew(rig)

    def fail(_):
        raise CommandTimeoutError("private failure")

    rig.scanner.reply = fail
    assert rig.cache.poll_once()
    for now in (11, 20, 100):
        rig.clock.now = now
        rig.scanner.sample(scan())
        assert renew(rig) is None
        assert not rig.cache.poll_once()
    assert wires(rig.scanner) == ["FQK"]


@pytest.mark.parametrize("value", [None, 0, 1, "false", []])
def test_snapshot_policy_requires_exact_bool(configured, value):
    scanner = SupplementalScanner()
    profile = DaemonDisplayProfile(configured, lambda: scanner.endpoint)
    with pytest.raises(ValueError, match="policy"):
        DaemonDisplayFrames(profile, scanner, legacy_snapshot_demand=value)


def test_legacy_feed_and_default_feed_cannot_accept_explicit_renewal(explicit, configured):
    rig = explicit
    legacy = DaemonDisplayFrames(rig.profile, rig.scanner)
    try:
        legacy.start()
        assert legacy.renew_supplemental_demand(ticket(rig)) is None
    finally:
        legacy.close()


@pytest.mark.parametrize("value", [None, True, -1, 2**53, "1", float("nan")])
def test_cache_demand_rejects_invalid_revisions(explicit, value):
    rig = explicit
    with pytest.raises(ValueError):
        rig.cache.renew_demand_if_current(rig.cache._session, value)
    assert not rig.cache.snapshot().active
