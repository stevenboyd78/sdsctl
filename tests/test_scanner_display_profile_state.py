from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from sds200 import scanner_display_profile_state as state
from sds200.scanner_display_profile import ScannerDisplayProfileError
from sds200.scanner_display_profile_state import (
    DisplayProfileBinding,
    DisplayProfileProvenance,
    DisplayProfileRefreshFailure,
    DisplayProfileSourceKind,
    DisplayProfileStateError,
    DisplayProfileStatus,
    ScannerDisplayProfileStore,
)

ENDPOINT = UUID(int=1)
BINDING = DisplayProfileBinding(ENDPOINT, UUID(int=2), DisplayProfileSourceKind.MANUAL_IMPORT)
NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
SOURCE = (
    b"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\tOff\tAFS\tCOLOR\r\n"
    b"DispOptItems\tDispOptId=1\tDispLayoutId=1\tFrequency\r\n"
    b"DispColors\tDispColorId=2\tColorLayoutId=1\tffffff\t000000\r\n"
)


def populated_store() -> ScannerDisplayProfileStore:
    store = ScannerDisplayProfileStore(ENDPOINT)
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    store.commit(preview, imported_at=NOW)
    return store


def test_explicit_prepare_and_commit_do_not_claim_scanner_freshness() -> None:
    store = ScannerDisplayProfileStore(ENDPOINT)
    initial = store.snapshot(ENDPOINT)
    assert initial.status is DisplayProfileStatus.UNAVAILABLE
    assert initial.last_good is initial.pending is initial.last_failure is None
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    assert store.snapshot(ENDPOINT).last_good is None
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW + timedelta(seconds=1))
    assert preview.previous_revision is None and not preview.source_changed
    assert store.snapshot(ENDPOINT).last_good is None
    imported = store.commit(preview, imported_at=NOW + timedelta(seconds=2))
    snapshot = store.snapshot(ENDPOINT)
    assert snapshot.status is DisplayProfileStatus.LAST_IMPORTED
    assert snapshot.last_good is imported
    assert imported.provenance.binding == BINDING
    assert imported.provenance.acquired_at == NOW + timedelta(seconds=1)
    assert imported.provenance.imported_at == NOW + timedelta(seconds=2)
    assert snapshot.pending is snapshot.last_failure is None
    assert snapshot.generation > initial.generation
    assert initial.last_good is None  # Old snapshots never change underneath readers.
    with pytest.raises(FrozenInstanceError):
        imported.provenance.binding = None  # type: ignore[misc, assignment]


def test_wrong_endpoint_and_foreign_store_tickets_are_refused() -> None:
    store = populated_store()
    before = store.snapshot(ENDPOINT)
    with pytest.raises(DisplayProfileStateError, match="different endpoint"):
        store.snapshot(UUID(int=9))
    with pytest.raises(DisplayProfileStateError, match="different endpoint"):
        store.begin_refresh(replace(BINDING, endpoint_id=UUID(int=9)), started_at=NOW)
    assert store.snapshot(ENDPOINT) == before
    other = ScannerDisplayProfileStore(ENDPOINT)
    ticket = other.begin_refresh(BINDING, started_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="foreign"):
        store.prepare(ticket, SOURCE, acquired_at=NOW)


@pytest.mark.parametrize(
    "binding",
    [
        replace(BINDING, source_id=UUID(int=3)),
        replace(BINDING, source_kind=DisplayProfileSourceKind.FAVORITES_SYNC),
    ],
)
def test_source_change_requires_review_even_when_content_is_identical(
    binding: DisplayProfileBinding,
) -> None:
    store = populated_store()
    old = store.snapshot(ENDPOINT).last_good
    ticket = store.begin_refresh(binding, started_at=NOW)
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    assert preview.source_changed
    assert old is not None and preview.previous_revision == old.profile.revision
    with pytest.raises(DisplayProfileStateError, match="confirm"):
        store.commit(preview, imported_at=NOW)
    assert store.snapshot(ENDPOINT).last_good is old
    imported = store.commit(preview, imported_at=NOW, confirm_source_change=True)
    assert imported.provenance.binding == binding


@pytest.mark.parametrize("reason", list(DisplayProfileRefreshFailure))
def test_refresh_failure_preserves_last_good_and_recovery_clears_failure(
    reason: DisplayProfileRefreshFailure,
) -> None:
    store = populated_store()
    old = store.snapshot(ENDPOINT).last_good
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    store.fail(ticket, reason)
    failed = store.snapshot(ENDPOINT)
    assert failed.last_good is old
    assert failed.status is DisplayProfileStatus.REFRESH_FAILED
    assert failed.last_failure is not None
    assert failed.last_failure.reason is reason and failed.pending is None
    assert failed.last_failure.refresh is ticket
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    assert store.snapshot(ENDPOINT).last_failure is failed.last_failure
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    store.commit(preview, imported_at=NOW)
    assert store.snapshot(ENDPOINT).last_failure is None


def test_invalid_profile_does_not_publish_partial_data_or_private_error() -> None:
    store = populated_store()
    old = store.snapshot(ENDPOINT).last_good
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    with pytest.raises(ScannerDisplayProfileError) as raised:
        store.prepare(ticket, b"Owner\tPRIVATE_SENTINEL\nDisplayOption\tbad", acquired_at=NOW)
    snapshot = store.snapshot(ENDPOINT)
    assert snapshot.last_good is old
    assert snapshot.last_failure is not None
    assert snapshot.last_failure.reason is DisplayProfileRefreshFailure.INVALID_PROFILE
    assert snapshot.pending is None
    assert "PRIVATE_SENTINEL" not in str(raised.value) + repr(snapshot)


def test_initial_failure_has_no_defaults_and_cancel_keeps_last_good() -> None:
    store = ScannerDisplayProfileStore(ENDPOINT)
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    store.fail(ticket, DisplayProfileRefreshFailure.UNAVAILABLE)
    assert store.snapshot(ENDPOINT).last_good is None
    store = populated_store()
    old = store.snapshot(ENDPOINT).last_good
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    store.cancel(ticket)
    assert store.snapshot(ENDPOINT).last_good is old
    with pytest.raises(DisplayProfileStateError, match="superseded"):
        store.commit(preview, imported_at=NOW)


def test_copied_reused_and_superseded_evidence_is_not_a_valid_ticket() -> None:
    store = populated_store()
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="foreign"):
        store.prepare(replace(ticket), SOURCE, acquired_at=NOW)
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="foreign"):
        store.commit(replace(preview), imported_at=NOW)
    store.begin_refresh(replace(BINDING, source_id=UUID(int=3)), started_at=NOW)
    final_ticket = store.begin_refresh(BINDING, started_at=NOW)  # A -> B -> A.
    assert ticket == final_ticket and ticket is not final_ticket
    with pytest.raises(DisplayProfileStateError, match="superseded"):
        store.commit(preview, imported_at=NOW)
    final_preview = store.prepare(final_ticket, SOURCE, acquired_at=NOW)
    store.commit(final_preview, imported_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="completed"):
        store.commit(final_preview, imported_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="completed"):
        store.fail(final_ticket, DisplayProfileRefreshFailure.UNAVAILABLE)


def test_timezone_normalization_and_invalid_chronology_leave_state_unchanged() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=-6)))
    provenance = DisplayProfileProvenance(BINDING, local, local)
    assert provenance.acquired_at == NOW and provenance.acquired_at.tzinfo is UTC
    store = populated_store()
    before = store.snapshot(ENDPOINT)
    with pytest.raises(DisplayProfileStateError, match="precedes"):
        store.begin_refresh(BINDING, started_at=NOW - timedelta(seconds=1))
    assert store.snapshot(ENDPOINT) == before
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="precedes"):
        store.prepare(ticket, SOURCE, acquired_at=NOW - timedelta(seconds=1))
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="precedes"):
        store.commit(preview, imported_at=NOW - timedelta(seconds=1))
    assert store.snapshot(ENDPOINT).last_good is before.last_good


@pytest.mark.parametrize("bad_time", [datetime(2026, 1, 1), None, 0, "PRIVATE_SENTINEL"])
def test_all_timestamp_boundaries_require_aware_datetimes(bad_time: object) -> None:
    store = populated_store()
    with pytest.raises(DisplayProfileStateError, match="timezone-aware"):
        store.begin_refresh(BINDING, started_at=bad_time)  # type: ignore[arg-type]
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="timezone-aware"):
        store.prepare(ticket, SOURCE, acquired_at=bad_time)  # type: ignore[arg-type]
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="timezone-aware"):
        store.commit(preview, imported_at=bad_time)  # type: ignore[arg-type]


@pytest.mark.parametrize("failure", ["PRIVATE_SENTINEL", None, False])
def test_only_sanitized_failure_categories_are_stored(failure: object) -> None:
    store = populated_store()
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="sanitized"):
        store.fail(ticket, failure)  # type: ignore[arg-type]
    assert store.snapshot(ENDPOINT).last_failure is None


@pytest.mark.parametrize("invalid", ["192.0.2.1", "PRIVATE_SENTINEL", None, 1])
def test_binding_accepts_only_opaque_uuid_selections(invalid: object) -> None:
    with pytest.raises(DisplayProfileStateError):
        ScannerDisplayProfileStore(invalid)  # type: ignore[arg-type]
    with pytest.raises(DisplayProfileStateError):
        DisplayProfileBinding(invalid, UUID(int=2), BINDING.source_kind)  # type: ignore[arg-type]
    with pytest.raises(DisplayProfileStateError):
        DisplayProfileBinding(ENDPOINT, invalid, BINDING.source_kind)  # type: ignore[arg-type]
    with pytest.raises(DisplayProfileStateError):
        DisplayProfileBinding(ENDPOINT, UUID(int=2), invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", ["true", 1, None])
def test_source_change_confirmation_cannot_be_a_truthy_string(invalid: object) -> None:
    store = populated_store()
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    with pytest.raises(DisplayProfileStateError, match="boolean"):
        store.commit(preview, imported_at=NOW, confirm_source_change=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("old_data", [SOURCE, b"invalid profile"])
def test_old_slow_success_or_failure_cannot_overwrite_new_import(
    monkeypatch: pytest.MonkeyPatch,
    old_data: bytes,
) -> None:
    entered, release = threading.Event(), threading.Event()
    original = state.parse_scanner_display_profile

    def delayed(data: bytes):
        if data == old_data:
            entered.set()
            assert release.wait(5)
        return original(data)

    monkeypatch.setattr(state, "parse_scanner_display_profile", delayed)
    store = ScannerDisplayProfileStore(ENDPOINT)
    old = store.begin_refresh(BINDING, started_at=NOW)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.prepare, old, old_data, acquired_at=NOW)
        try:
            assert entered.wait(5)
            new = store.begin_refresh(BINDING, started_at=NOW)
            preview = store.prepare(new, SOURCE + b"\n", acquired_at=NOW)
            current = store.commit(preview, imported_at=NOW)
        finally:
            release.set()
        with pytest.raises(DisplayProfileStateError, match="superseded"):
            future.result(timeout=5)
    assert store.snapshot(ENDPOINT).last_good is current
    assert store.snapshot(ENDPOINT).last_failure is None


def test_same_ticket_cannot_start_two_competing_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()
    original = state.parse_scanner_display_profile

    def delayed(data: bytes):
        entered.set()
        assert release.wait(5)
        return original(data)

    monkeypatch.setattr(state, "parse_scanner_display_profile", delayed)
    store = ScannerDisplayProfileStore(ENDPOINT)
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.prepare, ticket, SOURCE, acquired_at=NOW)
        try:
            assert entered.wait(5)
            with pytest.raises(DisplayProfileStateError, match="already"):
                store.prepare(ticket, b"invalid", acquired_at=NOW)
        finally:
            release.set()
        preview = future.result(timeout=5)
    store.commit(preview, imported_at=NOW)


def test_exactly_one_concurrent_commit_can_publish() -> None:
    store = populated_store()
    ticket = store.begin_refresh(BINDING, started_at=NOW)
    preview = store.prepare(ticket, SOURCE, acquired_at=NOW)
    barrier = threading.Barrier(2)

    def commit() -> bool:
        barrier.wait(timeout=5)
        try:
            store.commit(preview, imported_at=NOW)
        except DisplayProfileStateError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(commit), executor.submit(commit)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(results) == [False, True]
