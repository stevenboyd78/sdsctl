from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import (
    FavoritesEditorSession,
    FavoritesEditorSourceKind,
    FavoritesExternalAssistedSynchronizationService,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleState,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalSourceIdentity,
    FavoritesNavigationPath,
    FavoritesStorageSnapshot,
    FavoritesWritePlan,
)
from sds200.favorites_editor_external_preview import (
    FavoritesEditorExternalPreviewController,
    FavoritesEditorExternalPreviewError,
    FavoritesEditorExternalPreviewState,
    FavoritesEditorExternalRefreshOwner,
)
from tests.checkpoint_c_helpers import snapshot


class _Storage:
    kind = FavoritesEditorSourceKind.COPIED_TREE

    def __init__(self, value: FavoritesStorageSnapshot, path: Path) -> None:
        self.value = value
        self.requested_path = path

    @property
    def favorites_directory(self) -> Path:
        return self.requested_path

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return self.value

    def execute(self, plan: FavoritesWritePlan) -> object:
        raise AssertionError("Read-only preview tests must not execute writes.")


class _Source:
    def __init__(self, *values: object) -> None:
        self.values = values
        self.calls = 0

    def read_observations(self) -> object:
        value = self.values[self.calls]
        self.calls += 1
        if isinstance(value, BaseException):
            raise value
        return value


def _observation() -> FavoritesExternalRecordObservation:
    return FavoritesExternalRecordObservation(
        FavoritesExternalRecordIdentity(
            FavoritesExternalSourceIdentity("radioreference", "county-49-fire"),
            "frequency-100",
        ),
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 28, 12, 30, tzinfo=UTC),
            "revision-7",
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                "Fire Dispatch",
            ),
        ),
    )


class _OwnerFactory:
    def __init__(self, storage: _Storage, path: Path, source: _Source) -> None:
        self.storage = storage
        self.path = path
        self.source = source
        self.calls = 0
        self.owners: list[FavoritesEditorExternalRefreshOwner] = []

    def __call__(self) -> FavoritesEditorExternalRefreshOwner:
        self.calls += 1
        lifecycle = FavoritesExternalProvenanceLifecycle(self.storage, self.path)
        lifecycle.start()
        owner = FavoritesEditorExternalRefreshOwner(
            FavoritesExternalAssistedSynchronizationService(lifecycle, self.source),
            lifecycle,
            FavoritesExternalSourceIdentity("radioreference", "county-49-fire"),
        )
        self.owners.append(owner)
        return owner


def _controller(
    tmp_path: Path,
    *values: object,
) -> tuple[
    FavoritesEditorSession,
    _Storage,
    _Source,
    _OwnerFactory,
    FavoritesEditorExternalPreviewController,
]:
    storage = _Storage(snapshot(), tmp_path.resolve())
    session = FavoritesEditorSession.open(storage)
    source = _Source(*values)
    factory = _OwnerFactory(storage, tmp_path / "provenance.json", source)
    controller = FavoritesEditorExternalPreviewController(
        session,
        factory,
        now=lambda: datetime(2026, 8, 28, 13, 0, tzinfo=UTC),
    )
    return session, storage, source, factory, controller


def test_construction_is_passive_and_explicit_refresh_presents_exact_result(
    tmp_path: Path,
) -> None:
    session, storage, source, factory, controller = _controller(
        tmp_path,
        (_observation(),),
    )
    original = storage.value
    provenance_path = tmp_path / "provenance.json"

    assert factory.calls == 0
    assert source.calls == 0
    assert controller.snapshot().state is FavoritesEditorExternalPreviewState.READY

    current = controller.refresh()

    assert factory.calls == 1
    assert source.calls == 1
    assert storage.value is original
    assert session.baseline_snapshot is original
    assert session.intended_snapshot is original
    assert session.undo_count == 0
    assert not provenance_path.exists()
    assert current.state is FavoritesEditorExternalPreviewState.SUCCEEDED
    assert current.presentation is not None
    assert current.presentation.provider == "radioreference"
    assert current.presentation.dataset == "county-49-fire"
    assert current.presentation.observation_count == 1
    assert dict(current.presentation.counts)["added"] == 1
    record = current.presentation.records[0]
    assert record.local_target == "unmapped"
    assert record.external_record_id == "frequency-100"
    assert record.observed_at == "2026-08-28T12:30:00+00:00"
    assert record.revision == "revision-7"
    assert record.fields[0].external_value == "Fire Dispatch"
    assert factory.owners[0].lifecycle.snapshot().state is (
        FavoritesExternalProvenanceLifecycleState.CLOSED
    )


def test_empty_provider_result_retains_configured_source_identity(tmp_path: Path) -> None:
    _session, _storage, _source, _factory, controller = _controller(tmp_path, ())

    current = controller.refresh()

    assert current.presentation is not None
    assert current.presentation.provider == "radioreference"
    assert current.presentation.dataset == "county-49-fire"
    assert current.presentation.observation_count == 0
    assert current.presentation.records == ()
    assert set(dict(current.presentation.counts).values()) == {0}


def test_refresh_is_blocked_before_factory_when_unreviewed_edits_exist(
    tmp_path: Path,
) -> None:
    session, _storage, source, factory, controller = _controller(
        tmp_path,
        (_observation(),),
    )
    session.rename(FavoritesNavigationPath((2,)), "Changed")

    with pytest.raises(FavoritesEditorExternalPreviewError, match="unreviewed"):
        controller.refresh()

    assert factory.calls == 0
    assert source.calls == 0


def test_fresh_local_mismatch_blocks_before_provider_read(tmp_path: Path) -> None:
    _session, storage, source, factory, controller = _controller(
        tmp_path,
        (_observation(),),
    )
    storage.value = FavoritesStorageSnapshot(
        catalog_bytes=storage.value.catalog_bytes + b"\r\n",
        documents=storage.value.documents,
    )

    with pytest.raises(FavoritesEditorExternalPreviewError, match="does not exactly match"):
        controller.refresh()

    assert factory.calls == 1
    assert source.calls == 0
    assert controller.snapshot().state is FavoritesEditorExternalPreviewState.FAILED


def test_failed_refresh_retains_last_success_without_leaking_provider_detail(
    tmp_path: Path,
) -> None:
    _session, _storage, source, _factory, controller = _controller(
        tmp_path,
        (_observation(),),
        RuntimeError("secret provider response"),
    )
    successful = controller.refresh()

    with pytest.raises(
        FavoritesEditorExternalPreviewError,
        match="RadioReference refresh failed: RuntimeError",
    ) as caught:
        controller.refresh()

    assert "secret provider response" not in str(caught.value)
    failed = controller.snapshot()
    assert failed.state is FavoritesEditorExternalPreviewState.FAILED
    assert failed.presentation is successful.presentation
    assert source.calls == 2


def test_invalidation_marks_successful_preview_stale(tmp_path: Path) -> None:
    _session, _storage, _source, _factory, controller = _controller(
        tmp_path,
        (_observation(),),
    )
    successful = controller.refresh()

    stale = controller.invalidate("editor reset")

    assert stale.state is FavoritesEditorExternalPreviewState.STALE
    assert stale.presentation is successful.presentation
    assert stale.stale_reason == "editor reset"


def test_only_one_refresh_can_be_in_flight(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingSource(_Source):
        def read_observations(self) -> object:
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return (_observation(),)

    storage = _Storage(snapshot(), tmp_path.resolve())
    session = FavoritesEditorSession.open(storage)
    source = BlockingSource()
    factory = _OwnerFactory(storage, tmp_path / "provenance.json", source)
    controller = FavoritesEditorExternalPreviewController(session, factory)
    errors: list[BaseException] = []

    def refresh() -> None:
        try:
            controller.refresh()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=refresh)
    thread.start()
    assert entered.wait(timeout=5)
    with pytest.raises(FavoritesEditorExternalPreviewError, match="already in progress"):
        controller.refresh()
    release.set()
    thread.join(timeout=5)

    assert not errors
    assert not thread.is_alive()
    assert source.calls == 1


def test_cancellation_discards_result_closes_owner_and_retains_prior_preview(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class CancelSource(_Source):
        def read_observations(self) -> object:
            self.calls += 1
            if self.calls == 1:
                return (_observation(),)
            entered.set()
            assert release.wait(timeout=5)
            return (_observation(),)

    storage = _Storage(snapshot(), tmp_path.resolve())
    session = FavoritesEditorSession.open(storage)
    source = CancelSource()
    factory = _OwnerFactory(storage, tmp_path / "provenance.json", source)
    controller = FavoritesEditorExternalPreviewController(session, factory)
    first = controller.refresh()
    errors: list[BaseException] = []

    def refresh() -> None:
        try:
            controller.refresh()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=refresh)
    thread.start()
    assert entered.wait(timeout=5)
    cancelled = controller.cancel()
    release.set()
    thread.join(timeout=5)

    assert cancelled.state is FavoritesEditorExternalPreviewState.CANCELLED
    assert not thread.is_alive()
    assert len(errors) == 1
    current = controller.snapshot()
    assert current.state is FavoritesEditorExternalPreviewState.CANCELLED
    assert current.presentation is first.presentation
    assert factory.owners[-1].lifecycle.snapshot().state is (
        FavoritesExternalProvenanceLifecycleState.CLOSED
    )
