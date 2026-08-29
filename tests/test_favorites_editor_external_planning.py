from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sds200 import (
    FavoritesEditorExternalFieldChoice,
    FavoritesEditorExternalFieldDecision,
    FavoritesEditorExternalPlanningBlocker,
    FavoritesEditorExternalPlanningContext,
    FavoritesEditorExternalPlanningError,
    FavoritesEditorExternalRecordChoice,
    FavoritesEditorExternalRecordDecision,
    FavoritesExternalAssistedSynchronizationService,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalFieldOwnership,
    FavoritesExternalObservationEvidence,
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleState,
    FavoritesExternalRecordIdentity,
    FavoritesExternalRecordObservation,
    FavoritesExternalSourceIdentity,
    RadioReferenceFavoritesMappedField,
    bind_favorites_external_record,
    plan_favorites_editor_external_decisions,
    prepare_favorites_editor_external_import_decision,
    save_favorites_external_provenance,
    select_favorites_record_target,
)
from tests.checkpoint_c_helpers import (
    Storage,
    import_plan,
    linked_state,
    removed_observation,
    snapshot,
)
from tests.test_favorites_external_assisted_sync import _removal_service, _Source


def _field_context(
    tmp_path: Path,
) -> tuple[
    FavoritesEditorExternalPlanningContext,
    Storage,
    Path,
    _Source,
]:
    favorites = snapshot()
    target = select_favorites_record_target(favorites, 5, document_index=0)
    identity = FavoritesExternalRecordIdentity(
        FavoritesExternalSourceIdentity("radioreference", "county-1"),
        "frequency-101",
    )
    accepted = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 15, tzinfo=UTC),
            "accepted-r1",
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                target.record.fields[2],
            ),
            FavoritesExternalFieldObservation(
                "frequency",
                FavoritesExternalFieldObservationState.VALUE,
                target.record.fields[4],
            ),
        ),
    )
    bindings = (
        FavoritesExternalFieldBinding(
            "name",
            2,
            FavoritesExternalFieldOwnership.EXTERNAL,
        ),
        FavoritesExternalFieldBinding(
            "frequency",
            4,
            FavoritesExternalFieldOwnership.EXTERNAL,
        ),
    )
    state = bind_favorites_external_record(target, accepted, bindings)
    provenance_path = tmp_path / "state" / "provenance.json"
    save_favorites_external_provenance((state,), provenance_path)
    storage = Storage(favorites)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, provenance_path)
    lifecycle.start()
    observation = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 28, tzinfo=UTC),
            "provider-r2",
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                "Dispatch Updated",
            ),
            FavoritesExternalFieldObservation(
                "frequency",
                FavoritesExternalFieldObservationState.VALUE,
                "155100000",
            ),
        ),
    )
    source = _Source((observation,))
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    result = service.refresh()
    return (
        FavoritesEditorExternalPlanningContext(service, result, identity.source),
        storage,
        provenance_path,
        source,
    )


def _talkgroup_context(
    tmp_path: Path,
) -> tuple[FavoritesEditorExternalPlanningContext, Storage, Path, _Source]:
    favorites = snapshot()
    target = select_favorites_record_target(favorites, 14, document_index=0)
    identity = FavoritesExternalRecordIdentity(
        FavoritesExternalSourceIdentity("radioreference", "system-12042"),
        "talkgroup-1000",
    )
    accepted = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 15, tzinfo=UTC),
            "accepted-tg-r1",
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                target.record.fields[2],
            ),
            FavoritesExternalFieldObservation(
                "decimal",
                FavoritesExternalFieldObservationState.VALUE,
                target.record.fields[4],
            ),
        ),
    )
    state = bind_favorites_external_record(
        target,
        accepted,
        (
            FavoritesExternalFieldBinding("name", 2, FavoritesExternalFieldOwnership.EXTERNAL),
            FavoritesExternalFieldBinding("decimal", 4, FavoritesExternalFieldOwnership.EXTERNAL),
        ),
    )
    provenance_path = tmp_path / "state" / "talkgroup-provenance.json"
    save_favorites_external_provenance((state,), provenance_path)
    storage = Storage(favorites)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, provenance_path)
    lifecycle.start()
    observation = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 28, tzinfo=UTC),
            "provider-tg-r2",
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                "County Dispatch Updated",
            ),
            FavoritesExternalFieldObservation(
                "decimal",
                FavoritesExternalFieldObservationState.VALUE,
                "2001",
            ),
        ),
    )
    source = _Source((observation,))
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    result = service.refresh()
    return (
        FavoritesEditorExternalPlanningContext(service, result, identity.source),
        storage,
        provenance_path,
        source,
    )


def test_multiple_reviewed_external_fields_compose_without_execution(
    tmp_path: Path,
) -> None:
    context, storage, provenance_path, source = _field_context(tmp_path)
    baseline = storage.value
    persisted = provenance_path.read_bytes()
    selected = context.result.preview.records[0]

    plan = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.NAME,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.FREQUENCY,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
        ),
    )

    intended = select_favorites_record_target(
        plan.write_plan.intended_snapshot,
        5,
        document_index=0,
    )
    assert intended.record.fields[2] == "Dispatch Updated"
    assert intended.record.fields[4] == "155100000"
    assert plan.unresolved_decisions == 0
    assert plan.blockers == ()
    assert plan.is_complete
    assert plan.has_favorites_changes
    assert plan.has_provenance_changes
    assert plan.intended_provenance_records is not None
    fields = {field.name: field for field in plan.intended_provenance_records[0].fields}
    assert fields["name"].last_external is context.result.observations[0].fields[0]
    assert fields["frequency"].last_external is context.result.observations[0].fields[1]
    assert storage.value is baseline
    assert provenance_path.read_bytes() == persisted
    assert source.calls == 1


def test_reviewed_talkgroup_name_and_decimal_compose_without_execution(
    tmp_path: Path,
) -> None:
    context, storage, provenance_path, source = _talkgroup_context(tmp_path)
    baseline = storage.value
    persisted = provenance_path.read_bytes()
    selected = context.result.preview.records[0]

    plan = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.NAME,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.TALKGROUP_DECIMAL,
                FavoritesEditorExternalFieldChoice.EXTERNAL,
            ),
        ),
    )

    intended = select_favorites_record_target(
        plan.write_plan.intended_snapshot,
        14,
        document_index=0,
    )
    assert intended.record.command == "TGID"
    assert intended.record.fields[2] == "County Dispatch Updated"
    assert intended.record.fields[4] == "2001"
    assert plan.is_complete
    assert plan.has_favorites_changes
    assert plan.has_provenance_changes
    assert storage.value is baseline
    assert provenance_path.read_bytes() == persisted
    assert source.calls == 1


def test_local_and_detached_field_choices_change_only_intended_provenance(
    tmp_path: Path,
) -> None:
    context, storage, provenance_path, source = _field_context(tmp_path)
    selected = context.result.preview.records[0]
    persisted = provenance_path.read_bytes()

    plan = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.NAME,
                FavoritesEditorExternalFieldChoice.LOCAL,
            ),
            FavoritesEditorExternalFieldDecision(
                selected,
                RadioReferenceFavoritesMappedField.FREQUENCY,
                FavoritesEditorExternalFieldChoice.DETACHED,
            ),
        ),
    )

    assert plan.write_plan.is_noop
    assert not plan.has_favorites_changes
    assert plan.has_provenance_changes
    assert plan.is_complete
    assert plan.intended_provenance_records is not None
    fields = {field.name: field for field in plan.intended_provenance_records[0].fields}
    assert fields["name"].ownership is FavoritesExternalFieldOwnership.LOCAL
    assert fields["name"].last_external is None
    assert fields["frequency"].ownership is (FavoritesExternalFieldOwnership.DETACHED)
    assert storage.value == snapshot()
    assert provenance_path.read_bytes() == persisted
    assert source.calls == 1


def test_incomplete_and_duplicate_decisions_are_deterministic(tmp_path: Path) -> None:
    context, _storage, _path, _source = _field_context(tmp_path)
    selected = context.result.preview.records[0]
    name = FavoritesEditorExternalFieldDecision(
        selected,
        RadioReferenceFavoritesMappedField.NAME,
        FavoritesEditorExternalFieldChoice.LOCAL,
    )

    incomplete = plan_favorites_editor_external_decisions(context, (name,))

    assert incomplete.unresolved_decisions == 1
    assert incomplete.blockers == (FavoritesEditorExternalPlanningBlocker.INCOMPLETE_DECISIONS,)
    with pytest.raises(FavoritesEditorExternalPlanningError, match="duplicate"):
        plan_favorites_editor_external_decisions(context, (name, name))


def test_provider_removal_delete_and_keep_local_are_pure(tmp_path: Path) -> None:
    service, storage, source = _removal_service(tmp_path)
    result = service.refresh()
    selected = result.preview.records[0]
    context = FavoritesEditorExternalPlanningContext(
        service,
        result,
        selected.external_identity.source,  # type: ignore[union-attr]
    )
    baseline = storage.value

    deleted = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalRecordDecision(
                selected,
                FavoritesEditorExternalRecordChoice.DELETE,
            ),
        ),
    )

    assert deleted.is_complete
    assert deleted.has_favorites_changes
    assert deleted.intended_provenance_records == ()
    assert storage.value is baseline
    assert source.calls == 1

    keep_service, keep_storage, keep_source = _removal_service(tmp_path / "keep")
    keep_result = keep_service.refresh()
    keep_selected = keep_result.preview.records[0]
    keep_context = FavoritesEditorExternalPlanningContext(
        keep_service,
        keep_result,
        keep_selected.external_identity.source,  # type: ignore[union-attr]
    )
    kept = plan_favorites_editor_external_decisions(
        keep_context,
        (
            FavoritesEditorExternalRecordDecision(
                keep_selected,
                FavoritesEditorExternalRecordChoice.KEEP_LOCAL,
            ),
        ),
    )

    assert kept.is_complete
    assert kept.write_plan.is_noop
    assert kept.intended_provenance_records is not None
    assert kept.intended_provenance_records[0].detached
    assert keep_storage.value == baseline
    assert keep_source.calls == 1


def test_ordered_delete_and_keep_local_rebind_provenance_deterministically(
    tmp_path: Path,
) -> None:
    favorites = snapshot()
    first_state = linked_state(favorites, 5, "first")
    later_state = linked_state(favorites, 14, "later")
    provenance_path = tmp_path / "state" / "provenance.json"
    save_favorites_external_provenance((first_state, later_state), provenance_path)
    persisted = provenance_path.read_bytes()
    storage = Storage(favorites)
    lifecycle = FavoritesExternalProvenanceLifecycle(storage, provenance_path)
    lifecycle.start()
    source = _Source((removed_observation("first"), removed_observation("later")))
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    result = service.refresh()
    by_id = {
        preview.external_identity.record_id: preview
        for preview in result.preview.records
        if preview.external_identity is not None
    }
    context = FavoritesEditorExternalPlanningContext(
        service,
        result,
        first_state.external_identity.source,
    )
    decisions = (
        FavoritesEditorExternalRecordDecision(
            by_id["first"],
            FavoritesEditorExternalRecordChoice.DELETE,
        ),
        FavoritesEditorExternalRecordDecision(
            by_id["later"],
            FavoritesEditorExternalRecordChoice.KEEP_LOCAL,
        ),
    )

    first = plan_favorites_editor_external_decisions(context, decisions)
    second = plan_favorites_editor_external_decisions(context, decisions)

    assert first == second
    assert first.is_complete
    assert first.intended_provenance_records is not None
    assert len(first.intended_provenance_records) == 1
    retained = first.intended_provenance_records[0]
    assert retained.external_identity.record_id == "later"
    assert retained.detached
    assert retained.target.source_index == 13
    assert storage.value is favorites
    assert provenance_path.read_bytes() == persisted
    assert source.calls == 1


def test_template_import_and_ignore_reuse_exact_retained_evidence(
    tmp_path: Path,
) -> None:
    lifecycle, storage, provenance_path, existing = import_plan(tmp_path)
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, _Source())
    selected = existing.selected_preview
    assert selected.external_identity is not None
    context = FavoritesEditorExternalPlanningContext(
        service,
        existing.refresh_result,
        selected.external_identity.source,
    )
    persisted = provenance_path.read_bytes() if provenance_path.exists() else None

    imported = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalRecordDecision(
                selected,
                FavoritesEditorExternalRecordChoice.IMPORT,
                anchor=existing.anchor,
                template=existing.template,
                bindings=existing.bindings,
            ),
        ),
    )

    assert imported.is_complete
    assert imported.write_plan == existing.write_plan
    assert imported.intended_provenance_records == existing.intended_provenance_records
    assert storage.value == existing.write_plan.baseline_snapshot
    assert (provenance_path.read_bytes() if provenance_path.exists() else None) == persisted

    ignored = plan_favorites_editor_external_decisions(
        context,
        (
            FavoritesEditorExternalRecordDecision(
                selected,
                FavoritesEditorExternalRecordChoice.IGNORE,
            ),
        ),
    )
    assert ignored.is_complete
    assert ignored.write_plan.is_noop
    assert not ignored.has_provenance_changes


def test_reviewed_import_preparation_uses_explicit_anchor_and_provider_values(
    tmp_path: Path,
) -> None:
    favorites = snapshot()
    anchor = select_favorites_record_target(favorites, 5, document_index=0)
    identity = FavoritesExternalRecordIdentity(
        FavoritesExternalSourceIdentity("radioreference", "county-1"),
        "frequency-102",
    )
    observation = FavoritesExternalRecordObservation(
        identity,
        FavoritesExternalObservationEvidence(
            datetime(2026, 8, 28, tzinfo=UTC),
            "provider-r3",
        ),
        (
            FavoritesExternalFieldObservation(
                "name",
                FavoritesExternalFieldObservationState.VALUE,
                "New Dispatch",
            ),
            FavoritesExternalFieldObservation(
                "frequency",
                FavoritesExternalFieldObservationState.VALUE,
                "154250000",
            ),
        ),
    )
    storage = Storage(favorites)
    lifecycle = FavoritesExternalProvenanceLifecycle(
        storage,
        tmp_path / "missing-provenance.json",
    )
    lifecycle.start()
    source = _Source((observation,))
    service = FavoritesExternalAssistedSynchronizationService(lifecycle, source)
    result = service.refresh()
    context = FavoritesEditorExternalPlanningContext(service, result, identity.source)

    decision = prepare_favorites_editor_external_import_decision(
        context,
        result.preview.records[0],
        anchor,
    )
    plan = plan_favorites_editor_external_decisions(context, (decision,))

    assert decision.template is not None
    assert decision.template.command == "C-Freq"
    assert decision.template.fields[2] == "New Dispatch"
    assert decision.template.fields[4] == "154250000"
    assert tuple(binding.name for binding in decision.bindings) == (
        "name",
        "frequency",
    )
    assert plan.is_complete
    assert plan.has_favorites_changes
    assert plan.intended_provenance_records is not None
    assert plan.intended_provenance_records[0].external_identity == identity
    assert storage.value is favorites
    assert not (tmp_path / "missing-provenance.json").exists()
    assert source.calls == 1


def test_stale_lifecycle_and_record_field_contradictions_are_rejected(
    tmp_path: Path,
) -> None:
    context, _storage, _path, _source = _field_context(tmp_path)
    selected = context.result.preview.records[0]
    with pytest.raises(FavoritesEditorExternalPlanningError, match="contradictory"):
        plan_favorites_editor_external_decisions(
            context,
            (
                FavoritesEditorExternalFieldDecision(
                    selected,
                    RadioReferenceFavoritesMappedField.NAME,
                    FavoritesEditorExternalFieldChoice.LOCAL,
                ),
                FavoritesEditorExternalRecordDecision(
                    selected,
                    FavoritesEditorExternalRecordChoice.DETACHED,
                ),
            ),
        )

    lifecycle = context.service.lifecycle_snapshot
    assert lifecycle.state is FavoritesExternalProvenanceLifecycleState.ACTIVE
    # The service exposes no execution through this planner; closing its lifecycle
    # is enough to prove retained evidence is rejected as stale.
    context.service._lifecycle.close()
    with pytest.raises(FavoritesEditorExternalPlanningError, match="stale"):
        plan_favorites_editor_external_decisions(context, ())
