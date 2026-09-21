"""Offline owner-to-delivery-to-client regression; never enables a live reader."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime

import pytest

from sds200.scanner_display_supplemental import SupplementalValueStatus as Status
from sds200.scanner_display_supplemental_client import SupplementalConsumer, SupplementalRequest
from sds200.scanner_display_supplemental_wire import (
    MAX_SEQUENCE,
    decode_supplemental_delivery,
    project_supplemental_delivery,
)
from sds200.scanner_quick_keys import QuickKeySelection

from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_key_worker import scan
from .test_daemon_quick_keys import activate
from .test_daemon_supplemental_reads import engine as engine
from .test_daemon_supplemental_reads import read_both, wires, worker_read_both
from .test_daemon_supplemental_reads import shared as shared
from .test_scanner_clock import fixture as fixture
from .test_scanner_clock import reading


@pytest.fixture
def payload(engine):
    feed, _, _, _, _ = engine
    worker_read_both(engine)
    capture = feed.supplemental_frame_set()
    return project_supplemental_delivery(capture, now=capture.captured_at)


def consumer(payload):
    return SupplementalConsumer(decode_supplemental_delivery(payload).context)


def deliver(client, payload, *, start=100, finish=None):
    ticket = client.begin(now=start)
    return client.accept(ticket, payload, now=start if finish is None else finish)


def newer(payload, *, psi=1, clock=0, favorites=0):
    result = deepcopy(payload)
    result["psi"]["sequence"] += psi
    result["psi"]["age_seconds"] = 0
    for name, delta in (("clock", clock), ("favorites", favorites)):
        result[name]["sample_sequence"] += delta
        result[name]["age_seconds"] = 0
    return result


def test_complete_owner_join_has_real_independent_sequences_and_no_io(engine, payload):
    feed, _, _, scanner, _ = engine
    public = feed.snapshot()
    assert payload["clock"]["sample_sequence"] == payload["favorites"]["sample_sequence"] == 1
    assert payload["clock"]["value"] == "2026-09-17T21:26:59"
    assert len(payload["favorites"]["value"]) == 100
    assert "captured_at" not in str(payload) and "PRIVATE" not in str(payload)
    client = consumer(payload)
    assert deliver(client, payload, finish=100.2)
    view = client.snapshot(now=100.2)
    assert view.clock.local_time == datetime(2026, 9, 17, 21, 26, 59)
    assert view.clock.age_seconds == pytest.approx(0.2)
    assert view.favorites.age_seconds == pytest.approx(0.7)
    assert wires(scanner) == ["FQK", "DTM"] and feed.snapshot() == public


def test_psi_and_snapshot_do_not_change_sample_ids_but_successful_reads_do(engine, payload):
    feed, cache, _, scanner, clock = engine
    initial = payload["context"]
    scanner.sample(scan("None", "None", "New PSI channel"))
    sample = feed.supplemental_frame_set()
    assert sample.context_revision == initial["context_revision"]
    assert sample.supplemental.clock.sample_sequence == 1
    assert sample.supplemental.favorites.sample_sequence == 1
    clock.now = 12
    scanner.sample(scan("None", "None"))
    feed.snapshot()
    assert cache.poll_once()
    sample = feed.supplemental_frame_set()
    assert sample.supplemental.favorites.sample_sequence == 2
    assert sample.supplemental.clock.sample_sequence == 1
    clock.now = 12.5
    assert cache.poll_once()
    assert feed.supplemental_frame_set().supplemental.clock.sample_sequence == 2
    assert wires(scanner) == ["FQK", "DTM", "FQK", "DTM"]


def test_invalidation_drops_ids_and_successful_acquisition_never_reuses_them(shared):
    cache, session, scanner, clock = shared
    read_both(shared)
    before = cache.supplemental_snapshot()
    cache.suspend(session)
    hidden = cache.supplemental_snapshot()
    assert hidden.context_revision > before.context_revision
    assert hidden.clock.sample_sequence is None
    assert all(bank.sample_sequence is None for bank in hidden.quick_keys.banks)
    cache.disconnect(session)
    clock.now = 20
    session = cache.begin_session()
    activate(cache, session, QuickKeySelection(None, None))
    assert cache.poll_once()
    clock.now = 20.5
    assert cache.poll_once()
    after = cache.supplemental_snapshot()
    assert after.clock.sample_sequence == 2
    assert after.quick_keys.banks[0].sample_sequence == 2
    assert after.context_revision > hidden.context_revision
    assert wires(scanner) == ["FQK", "DTM", "FQK", "DTM"]


def test_busy_and_discarded_clock_reads_cannot_allocate_sample_ids(fixture):
    samples, clock, session = fixture
    assert samples.finish(samples.begin_read(session), reading())
    assert samples.snapshot().sample_sequence == 1
    clock.now += 2
    assert not samples.finish(samples.begin_read(session))
    assert samples.snapshot().sample_sequence == 1
    clock.now += 2
    ticket = samples.begin_read(session)
    samples.invalidate(session)
    assert not samples.finish(ticket, reading())
    assert samples.snapshot().sample_sequence is None
    clock.now += 2
    assert samples.finish(samples.begin_read(session), reading())
    assert samples.snapshot().sample_sequence == 2


def test_clock_sequence_exhaustion_fails_closed(fixture):
    samples, _, session = fixture
    samples._sample_sequence = MAX_SEQUENCE
    assert not samples.finish(samples.begin_read(session), reading())
    assert samples.snapshot().sample_sequence is None
    assert samples.snapshot().blocked_until_reconnect == "read_error"


def test_favorites_sequence_exhaustion_quarantines_shared_worker(shared):
    cache, session, _, _ = shared
    activate(cache, session, QuickKeySelection(None, None))
    cache._sample_sequences["favorites"] = MAX_SEQUENCE
    assert cache.poll_once()
    snapshot = cache.supplemental_snapshot()
    assert snapshot.quick_keys.blocked_until_reconnect == "read_error"
    assert all(bank.sample_sequence is None for bank in snapshot.quick_keys.banks)
    assert snapshot.clock.sample_sequence is None


@pytest.mark.parametrize("field", ["clock", "favorites"])
def test_new_psi_and_repeated_reply_never_extend_auxiliary_deadline(payload, field):
    client = consumer(payload)
    assert deliver(client, payload)
    original = client.snapshot(now=100)
    # Even a wrongly zeroed server age cannot renew an already-known sample.
    for time in (101, 102, 103, 104):
        assert deliver(client, newer(payload, psi=time), start=time)
    deadline = 105 - getattr(original, field).age_seconds
    view = client.snapshot(now=deadline)
    assert getattr(view, field).status is Status.STALE
    assert deliver(client, newer(payload, psi=999), start=105)
    assert getattr(client.snapshot(now=105), field).status is Status.STALE
    refresh = newer(payload, psi=1000, **{field: 1})
    assert deliver(client, refresh, start=105.1)
    view = client.snapshot(now=105.1)
    assert getattr(view, field).status is Status.CURRENT
    assert getattr(view, "favorites" if field == "clock" else "clock").status is Status.STALE


def test_late_first_response_cannot_get_five_new_seconds_on_receipt(payload):
    client = consumer(payload)
    assert not deliver(client, payload, start=100, finish=106)
    view = client.snapshot(now=106)
    assert view.clock.status is view.favorites.status is Status.STALE
    # That expired response's sample IDs are retired even if PSI now advances.
    assert deliver(client, newer(payload, psi=10), start=106.1)
    assert client.snapshot(now=106.1).clock.local_time is None
    assert deliver(client, newer(payload, psi=11, clock=1, favorites=1), start=106.2)
    assert client.snapshot(now=106.2).clock.local_time is not None


def test_newer_auxiliary_reads_cannot_extend_repeated_psi(payload):
    client = consumer(payload)
    assert deliver(client, payload)
    assert deliver(client, newer(payload, psi=0, clock=1, favorites=1), start=104)
    view = client.snapshot(now=104.5)
    assert view.clock.status is view.favorites.status is Status.STALE
    assert not deliver(client, newer(payload, psi=0, clock=2, favorites=2), start=105)


def test_newest_request_ticket_wins_even_before_its_response(payload):
    client = consumer(payload)
    older = client.begin(now=100)
    latest = client.begin(now=100.1)
    assert not client.accept(older, payload, now=100.2)
    assert client.accept(latest, payload, now=100.3)
    assert not client.accept(latest, payload, now=100.4)  # consumed
    assert not client.accept(replace(latest), payload, now=100.5)
    assert client.snapshot(now=100.5).clock.status is Status.CURRENT
    assert not client.accept(SupplementalRequest(100.1), payload, now=100.5)


def test_suspend_discards_inflight_and_same_samples_cannot_revive(payload):
    client = consumer(payload)
    assert deliver(client, payload)
    pending = client.begin(now=100.1)
    client.suspend()
    assert not client.accept(pending, payload, now=100.2)
    assert client.snapshot(now=100.2).clock.local_time is None
    assert not deliver(client, payload, start=100.3)  # same retired PSI
    assert deliver(client, newer(payload), start=100.4)
    assert client.snapshot(now=100.4).clock.local_time is None
    assert deliver(client, newer(payload, psi=2, clock=1, favorites=1), start=100.5)
    assert client.snapshot(now=100.5).clock.local_time is not None


@pytest.mark.parametrize("field", ["clock", "favorites"])
def test_conflicting_same_id_clears_only_that_source_and_cannot_self_repair(payload, field):
    client = consumer(payload)
    assert deliver(client, payload)
    bad = newer(payload)
    bad[field]["value"] = "2026-09-18T21:26:59" if field == "clock" else "0" * 100
    if bad[field]["value"] == payload[field]["value"]:
        bad[field]["value"] = "2" * 100
    assert deliver(client, bad, start=101)
    view = client.snapshot(now=101)
    assert getattr(view, field).status is Status.INVALID_SOURCE
    assert getattr(view, "favorites" if field == "clock" else "clock").status is Status.CURRENT
    assert deliver(client, newer(payload, psi=2), start=102)
    assert getattr(client.snapshot(now=102), field).status is Status.INVALID_SOURCE
    assert deliver(client, newer(payload, psi=3, **{field: 1}), start=103)
    assert getattr(client.snapshot(now=103), field).status is Status.CURRENT


@pytest.mark.parametrize("field", ["clock", "favorites"])
@pytest.mark.parametrize("status", [state for state in Status if state is not Status.CURRENT])
def test_noncurrent_source_retires_previous_value_without_hiding_other_source(
    payload, field, status
):
    client = consumer(payload)
    assert deliver(client, payload)
    bad = newer(payload)
    bad[field] = {
        "status": status.value,
        "sample_sequence": None,
        "age_seconds": None,
        "value": None,
    }
    assert deliver(client, bad, start=101)
    view = client.snapshot(now=101)
    assert getattr(view, field).status is status
    assert getattr(view, "favorites" if field == "clock" else "clock").status is Status.CURRENT
    assert deliver(client, newer(payload, psi=2), start=102)
    assert getattr(client.snapshot(now=102), field).status is status


@pytest.mark.parametrize(
    "field",
    [
        "endpoint_id",
        "stream_id",
        "session_id",
        "profile_revision",
        "profile_invalidation",
        "context_revision",
    ],
)
def test_context_mismatch_requires_explicit_new_guard_and_rejects_foreign_ticket(payload, field):
    original = consumer(payload)
    assert deliver(original, payload)
    bad = newer(payload)
    context = bad["context"]
    context[field] = (
        context[field] + 1
        if isinstance(context[field], int)
        else "f" * 64
        if field == "profile_revision"
        else "00000000-0000-0000-0000-000000000099"
    )
    ticket = original.begin(now=101)
    assert not original.accept(ticket, bad, now=101.1)
    assert original.snapshot(now=101.1).clock.local_time is None
    replacement = consumer(bad)
    assert not replacement.accept(ticket, bad, now=101.2)
    assert deliver(replacement, bad, start=101.3)
    assert replacement.snapshot(now=101.3).clock.local_time is not None
    assert original.context != replacement.context
    with pytest.raises(AttributeError):
        original.context = replacement.context


@pytest.mark.parametrize("now", [True, -1, float("nan"), float("inf"), 10**1000, 99.9])
def test_bad_consumer_time_permanently_closes_guard(payload, now):
    client = consumer(payload)
    assert deliver(client, payload)
    with pytest.raises(ValueError, match="consumer clock"):
        client.snapshot(now=now)
    assert client.snapshot(now=101).clock.local_time is None
    with pytest.raises(ValueError, match="closed"):
        client.begin(now=101)


def test_close_and_mutating_input_cannot_restore_state(payload):
    client = consumer(payload)
    assert deliver(client, payload)
    payload["clock"]["value"] = "2026-01-01T01:01:01"
    assert client.snapshot(now=100).clock.local_time.month == 9
    pending = client.begin(now=101)
    client.close()
    assert not client.accept(pending, payload, now=102)
    assert client.snapshot(now=102).clock.local_time is None


@pytest.mark.parametrize("field", ["clock", "favorites"])
def test_older_source_id_cannot_replace_a_newer_value(payload, field):
    client = consumer(payload)
    assert deliver(client, newer(payload, clock=1, favorites=1))
    older = newer(payload, psi=2, clock=1, favorites=1)
    older[field]["sample_sequence"] -= 1
    assert deliver(client, older, start=101)
    view = client.snapshot(now=101)
    assert getattr(view, field).status is Status.INVALID_SOURCE
    assert getattr(view, "favorites" if field == "clock" else "clock").status is Status.CURRENT
    # The older reply cannot lower the retained high-water mark.
    assert deliver(client, newer(payload, psi=3, clock=1, favorites=1), start=102)
    assert getattr(client.snapshot(now=102), field).status is Status.INVALID_SOURCE
    assert deliver(client, newer(payload, psi=4, clock=2, favorites=2), start=103)
    assert getattr(client.snapshot(now=103), field).status is Status.CURRENT


def test_repeated_visibility_changes_do_not_reset_retired_sample_ids(payload):
    client = consumer(payload)
    assert deliver(client, payload)
    for cycle in range(1, 101):
        client.suspend()
        assert deliver(client, newer(payload, psi=cycle), start=100 + cycle)
        view = client.snapshot(now=100 + cycle)
        assert view.clock.local_time is view.favorites.states is None


@pytest.mark.parametrize(
    "path,value",
    [
        (("extra",), "PRIVATE"),
        (("version",), True),
        (("version",), 2),
        (("protocol",), "PRIVATE"),
        (("context", "session_id"), "PRIVATE"),
        (("context", "profile_revision"), "x" * 64),
        (("context", "context_revision"), True),
        (("context", "profile_invalidation"), 2**53),
        (("psi", "sequence"), -1),
        (("psi", "age_seconds"), 10**1000),
        (("psi", "age_seconds"), 5),
        (("clock", "sample_sequence"), None),
        (("clock", "sample_sequence"), True),
        (("clock", "sample_sequence"), 0),
        (("clock", "sample_sequence"), 2**53),
        (("clock", "age_seconds"), -1),
        (("clock", "age_seconds"), float("nan")),
        (("clock", "age_seconds"), 5),
        (("clock", "value"), "2026-02-29T04:12:00"),
        (("clock", "value"), "0000-01-01T04:12:00"),
        (("clock", "value"), "2026-09-21T04:12:00Z"),
        (("clock", "value"), "2026-09-21T04:12:60"),
        (("clock", "value"), "2026-09-21T04:12:00.0"),
        (("clock", "status"), "stale"),
        (("favorites", "value"), "0" * 99),
        (("favorites", "value"), "0" * 99 + "3"),
        (("favorites", "value"), [0] * 100),
        (("favorites", "value"), "٠" * 100),
    ],
)
def test_malformed_wire_fails_closed_with_fixed_error(payload, path, value):
    bad = deepcopy(payload)
    node = bad
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with pytest.raises(ValueError, match="Invalid supplemental delivery") as failure:
        decode_supplemental_delivery(bad)
    assert "PRIVATE" not in str(failure.value)
    client = consumer(payload)
    assert deliver(client, payload)
    assert not deliver(client, bad, start=101)
    assert client.snapshot(now=101).clock.local_time is None


def test_envelope_is_small_private_and_detached_from_capture(engine, payload):
    import json

    feed, _, _, _, _ = engine
    assert len(json.dumps(payload)) < 2048
    decoded = decode_supplemental_delivery(payload)
    assert asdict(decoded.context) == payload["context"]
    assert "source_path" not in json.dumps(payload)
    assert "daylight_saving" not in json.dumps(payload)
    sample = feed.supplemental_frame_set()
    assert sample.supplemental.clock.sample_sequence == 1
    assert "sample_sequence" not in json.dumps(feed.snapshot())


@pytest.mark.parametrize("field", ["clock", "favorites"])
@pytest.mark.parametrize("sequence", [None, True, 0, -1, 2**53])
def test_producer_refuses_current_values_without_a_bounded_acquisition_id(
    engine, payload, field, sequence
):
    feed, _, _, _, _ = engine
    sample = feed.supplemental_frame_set()
    sample = replace(
        sample,
        supplemental=replace(
            sample.supplemental,
            **{field: replace(getattr(sample.supplemental, field), sample_sequence=sequence)},
        ),
    )
    with pytest.raises(ValueError, match="Invalid supplemental delivery"):
        project_supplemental_delivery(sample, now=sample.captured_at)


@pytest.mark.parametrize("field", ["clock", "favorites"])
def test_producer_expires_one_source_without_affecting_other_source(engine, payload, field):
    feed, _, _, _, _ = engine
    sample = feed.supplemental_frame_set()
    sample = replace(
        sample,
        supplemental=replace(
            sample.supplemental,
            **{field: replace(getattr(sample.supplemental, field), age_seconds=4.9)},
        ),
    )
    delivered = project_supplemental_delivery(sample, now=sample.captured_at + 0.2)
    assert delivered[field] == {
        "status": "stale",
        "sample_sequence": None,
        "age_seconds": None,
        "value": None,
    }
    assert delivered["favorites" if field == "clock" else "clock"]["status"] == "current"
