"""Pure aggregate assisted-synchronization planning for the Favorites editor."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from .favorites_editing import (
    FavoritesRecordSourceKind,
    FavoritesRecordTarget,
    _replace_favorites_record_field,
    create_favorites_record_after,
    delete_favorites_record,
    select_favorites_record_target,
)
from .favorites_editor_external_preview import FavoritesEditorExternalPlanningContext
from .favorites_external import (
    FavoritesExternalChangeKind,
    FavoritesExternalFieldBinding,
    FavoritesExternalFieldOwnership,
    FavoritesExternalFieldState,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordPreview,
    FavoritesExternalRecordState,
    bind_favorites_external_record,
    detach_favorites_external_field,
)
from .favorites_external_assisted_sync import RadioReferenceFavoritesMappedField
from .favorites_external_provenance import (
    deserialize_favorites_external_provenance,
    serialize_favorites_external_provenance,
)
from .favorites_external_refresh_detach import FavoritesExternalRefreshDetachScope
from .favorites_file import FavoritesSourceRecord
from .favorites_storage import FavoritesStorageSnapshot
from .favorites_write_plan import FavoritesWritePlan, plan_favorites_write
from .radioreference_mapping import (
    radioreference_favorites_frequency_mapping,
    radioreference_favorites_frequency_name_mapping,
    radioreference_favorites_talkgroup_decimal_mapping,
    radioreference_favorites_talkgroup_name_mapping,
)

if TYPE_CHECKING:
    from .favorites_external_refresh import FavoritesExternalRefreshResult


class FavoritesEditorExternalPlanningError(ValueError):
    """Report invalid, contradictory, foreign, or stale assisted decisions."""


class FavoritesEditorExternalFieldChoice(StrEnum):
    """Classify one explicit mapped-field decision."""

    EXTERNAL = "external"
    LOCAL = "local"
    DETACHED = "detached"


class FavoritesEditorExternalRecordChoice(StrEnum):
    """Classify one explicit provider-record decision."""

    IMPORT = "import"
    IGNORE = "ignore"
    DELETE = "delete"
    KEEP_LOCAL = "keep_local"
    DETACHED = "detached"


class FavoritesEditorExternalPlanningBlocker(StrEnum):
    """Classify why an assisted plan is not complete and ready for later execution."""

    INCOMPLETE_DECISIONS = "incomplete_decisions"
    UNRESOLVED_CONFLICTS = "unresolved_conflicts"
    WRITE_PLAN_BLOCKED = "write_plan_blocked"


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalFieldDecision:
    """Retain one exact preview field and an explicit ownership/value choice."""

    preview: FavoritesExternalRecordPreview
    field: RadioReferenceFavoritesMappedField
    choice: FavoritesEditorExternalFieldChoice

    def __post_init__(self) -> None:
        if type(self.preview) is not FavoritesExternalRecordPreview:
            raise TypeError("External field decision requires an exact preview.")
        if not isinstance(self.field, RadioReferenceFavoritesMappedField):
            raise TypeError("External field decision requires a reviewed mapped field.")
        if not isinstance(self.choice, FavoritesEditorExternalFieldChoice):
            raise TypeError("External field decision requires an explicit field choice.")


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalRecordDecision:
    """Retain one exact preview and its explicit structural or ownership choice."""

    preview: FavoritesExternalRecordPreview
    choice: FavoritesEditorExternalRecordChoice
    anchor: FavoritesRecordTarget | None = None
    template: FavoritesSourceRecord | None = None
    bindings: tuple[FavoritesExternalFieldBinding, ...] = ()

    def __post_init__(self) -> None:
        if type(self.preview) is not FavoritesExternalRecordPreview:
            raise TypeError("External record decision requires an exact preview.")
        if not isinstance(self.choice, FavoritesEditorExternalRecordChoice):
            raise TypeError("External record decision requires an explicit record choice.")
        if type(self.bindings) is not tuple:
            raise TypeError("External record decision bindings must be an immutable tuple.")
        if any(not isinstance(binding, FavoritesExternalFieldBinding) for binding in self.bindings):
            raise TypeError("External record decision bindings contain an invalid value.")
        if self.choice is FavoritesEditorExternalRecordChoice.IMPORT:
            if not isinstance(self.anchor, FavoritesRecordTarget):
                raise ValueError("External record import requires an exact anchor.")
            if not isinstance(self.template, FavoritesSourceRecord):
                raise ValueError("External record import requires an exact template.")
            if not self.bindings:
                raise ValueError("External record import requires reviewed bindings.")
        elif self.anchor is not None or self.template is not None or self.bindings:
            raise ValueError(
                "Only an external record import may retain anchor, template, or bindings."
            )


FavoritesEditorExternalDecision = (
    FavoritesEditorExternalFieldDecision | FavoritesEditorExternalRecordDecision
)


@dataclass(frozen=True, slots=True)
class FavoritesEditorExternalPlanningSnapshot:
    """One exact, unexecuted aggregate synchronization plan."""

    refresh_result: FavoritesExternalRefreshResult
    decisions: tuple[FavoritesEditorExternalDecision, ...]
    write_plan: FavoritesWritePlan
    baseline_provenance_records: tuple[FavoritesExternalRecordState, ...] | None
    intended_provenance_records: tuple[FavoritesExternalRecordState, ...] | None
    unresolved_decisions: int
    blockers: tuple[FavoritesEditorExternalPlanningBlocker, ...]

    def __post_init__(self) -> None:
        from .favorites_external_refresh import FavoritesExternalRefreshResult

        if type(self.refresh_result) is not FavoritesExternalRefreshResult:
            raise TypeError("External planning snapshot requires an exact refresh result.")
        if type(self.decisions) is not tuple:
            raise TypeError("External planning decisions must be an immutable tuple.")
        if any(
            type(decision)
            not in {
                FavoritesEditorExternalFieldDecision,
                FavoritesEditorExternalRecordDecision,
            }
            for decision in self.decisions
        ):
            raise TypeError("External planning snapshot contains an invalid decision.")
        if not isinstance(self.write_plan, FavoritesWritePlan):
            raise TypeError("External planning snapshot requires FavoritesWritePlan.")
        for records in (
            self.baseline_provenance_records,
            self.intended_provenance_records,
        ):
            if records is not None and type(records) is not tuple:
                raise TypeError("External planning provenance must be an immutable tuple or None.")
            if records is not None and any(
                not isinstance(record, FavoritesExternalRecordState) for record in records
            ):
                raise TypeError("External planning provenance contains an invalid record.")
        if type(self.unresolved_decisions) is not int or self.unresolved_decisions < 0:
            raise ValueError("External planning unresolved decision count must be non-negative.")
        if type(self.blockers) is not tuple or any(
            not isinstance(blocker, FavoritesEditorExternalPlanningBlocker)
            for blocker in self.blockers
        ):
            raise TypeError("External planning blockers must be immutable typed values.")

    @property
    def is_complete(self) -> bool:
        """Return whether every supported decision is resolved without blockers."""

        return not self.blockers

    @property
    def has_favorites_changes(self) -> bool:
        """Return whether the plan changes exact Favorites bytes."""

        return self.write_plan.has_changes

    @property
    def has_provenance_changes(self) -> bool:
        """Return whether the plan changes intended provenance."""

        return self.baseline_provenance_records != self.intended_provenance_records


@dataclass(slots=True)
class _ProvenanceEntry:
    state: FavoritesExternalRecordState
    document_index: int | None
    source_index: int
    import_anchor_index: int | None = None


@dataclass(frozen=True, slots=True)
class _StructuralOperation:
    decision: FavoritesEditorExternalRecordDecision
    document_index: int
    source_index: int


def _require_preview(
    context: FavoritesEditorExternalPlanningContext,
    preview: FavoritesExternalRecordPreview,
) -> None:
    if sum(item is preview for item in context.result.preview.records) != 1:
        raise FavoritesEditorExternalPlanningError(
            "External decision requires the exact retained preview once."
        )


def _observation(
    context: FavoritesEditorExternalPlanningContext,
    preview: FavoritesExternalRecordPreview,
) -> FavoritesExternalRecordObservation:
    if preview.external_identity is None or preview.evidence is None:
        raise FavoritesEditorExternalPlanningError(
            "External decision requires exact provider identity and evidence."
        )
    matches = tuple(
        observation
        for observation in context.result.observations
        if observation.identity == preview.external_identity
        and observation.evidence == preview.evidence
    )
    if len(matches) != 1:
        raise FavoritesEditorExternalPlanningError(
            "External decision requires one exact retained observation."
        )
    return matches[0]


def prepare_favorites_editor_external_import_decision(
    context: FavoritesEditorExternalPlanningContext,
    preview: FavoritesExternalRecordPreview,
    anchor: FavoritesRecordTarget,
) -> FavoritesEditorExternalRecordDecision:
    """Derive one reviewed import template from an explicit compatible HPD anchor."""

    if not isinstance(context, FavoritesEditorExternalPlanningContext):
        raise TypeError("External import preparation requires a planning context.")
    if type(preview) is not FavoritesExternalRecordPreview:
        raise TypeError("External import preparation requires an exact preview.")
    if not isinstance(anchor, FavoritesRecordTarget):
        raise TypeError("External import preparation requires an exact anchor.")
    _require_preview(context, preview)
    if preview.kind is not FavoritesExternalChangeKind.ADDED or preview.target is not None:
        raise FavoritesEditorExternalPlanningError(
            "External import preparation requires an unbound added record."
        )
    if anchor.source_kind is not FavoritesRecordSourceKind.HPD or anchor.document_index is None:
        raise FavoritesEditorExternalPlanningError(
            "External import preparation requires a compatible HPD anchor."
        )
    observation = _observation(context, preview)
    fields = list(anchor.record.fields)
    if anchor.record.command == "C-Freq":
        mappings = (
            radioreference_favorites_frequency_name_mapping(anchor, observation),
            radioreference_favorites_frequency_mapping(anchor, observation),
        )
    elif anchor.record.command == "TGID":
        mappings = (
            radioreference_favorites_talkgroup_name_mapping(anchor, observation),
            radioreference_favorites_talkgroup_decimal_mapping(anchor, observation),
        )
    else:
        raise FavoritesEditorExternalPlanningError(
            "External import template must be a conventional frequency or talkgroup."
        )
    bindings = tuple(
        FavoritesExternalFieldBinding(
            mapping.field.name,
            mapping.field_index,
            FavoritesExternalFieldOwnership.EXTERNAL,
        )
        for mapping in mappings
    )
    for mapping in mappings:
        fields[mapping.field_index] = mapping.scanner_value
    content = "\t".join((anchor.record.command, *fields)).encode("ascii")
    template = FavoritesSourceRecord(
        content=content,
        line_ending=anchor.record.line_ending,
    )
    return FavoritesEditorExternalRecordDecision(
        preview,
        FavoritesEditorExternalRecordChoice.IMPORT,
        anchor=anchor,
        template=template,
        bindings=bindings,
    )


def _baseline_entry(
    entries: list[_ProvenanceEntry],
    preview: FavoritesExternalRecordPreview,
) -> _ProvenanceEntry:
    target = preview.target
    if target is None:
        raise FavoritesEditorExternalPlanningError(
            "External decision requires one exact linked local target."
        )
    matches = tuple(
        entry
        for entry in entries
        if entry.import_anchor_index is None
        and entry.document_index == target.document_index
        and entry.source_index == target.source_index
        and entry.state.external_identity == preview.external_identity
    )
    if len(matches) != 1:
        raise FavoritesEditorExternalPlanningError(
            "External decision requires one exact linked baseline provenance record."
        )
    return matches[0]


def _field_name(field: RadioReferenceFavoritesMappedField) -> str:
    if field is RadioReferenceFavoritesMappedField.TALKGROUP_DECIMAL:
        return "decimal"
    return field.value


def _decision_key(decision: FavoritesEditorExternalDecision) -> tuple[int, str]:
    if isinstance(decision, FavoritesEditorExternalFieldDecision):
        return (id(decision.preview), f"field:{decision.field.value}")
    return (id(decision.preview), "record")


def _actionable_field_names(
    preview: FavoritesExternalRecordPreview,
) -> tuple[str, ...]:
    if preview.target is None:
        return ()
    command = preview.target.record.command
    supported = (
        ("name", "frequency")
        if command == "C-Freq"
        else ("name", "decimal")
        if command == "TGID"
        else ()
    )
    return tuple(
        name
        for name in supported
        if any(
            field.name == name
            and field.kind
            in {
                FavoritesExternalChangeKind.ADDED,
                FavoritesExternalChangeKind.REPLACED,
                FavoritesExternalChangeKind.REMOVED,
                FavoritesExternalChangeKind.CONFLICT,
            }
            for field in preview.fields
        )
    )


def _decision_completeness(
    result_records: tuple[FavoritesExternalRecordPreview, ...],
    decisions: tuple[FavoritesEditorExternalDecision, ...],
) -> tuple[int, bool]:
    decided_fields = {
        (id(decision.preview), _field_name(decision.field))
        for decision in decisions
        if isinstance(decision, FavoritesEditorExternalFieldDecision)
    }
    decided_records = {
        id(decision.preview)
        for decision in decisions
        if isinstance(decision, FavoritesEditorExternalRecordDecision)
    }
    unresolved = 0
    unresolved_conflict = False
    for preview in result_records:
        if preview.kind is FavoritesExternalChangeKind.ADDED and preview.target is None:
            unresolved += int(id(preview) not in decided_records)
            continue
        if preview.kind is FavoritesExternalChangeKind.REMOVED:
            unresolved += int(id(preview) not in decided_records)
            continue
        for field_name in _actionable_field_names(preview):
            if (id(preview), field_name) not in decided_fields:
                unresolved += 1
                unresolved_conflict = (
                    unresolved_conflict or preview.kind is FavoritesExternalChangeKind.CONFLICT
                )
    return unresolved, unresolved_conflict


def _field_state(
    state: FavoritesExternalRecordState,
    *,
    name: str,
    field_index: int,
) -> FavoritesExternalFieldState | None:
    named = tuple(field for field in state.fields if field.name == name)
    if len(named) > 1:
        raise FavoritesEditorExternalPlanningError(
            "External planning found duplicate field provenance names."
        )
    collisions = tuple(
        field for field in state.fields if field.field_index == field_index and field.name != name
    )
    if collisions:
        raise FavoritesEditorExternalPlanningError(
            "External mapped field collides with another provenance field."
        )
    return None if not named else named[0]


def _apply_field_decision(
    context: FavoritesEditorExternalPlanningContext,
    decision: FavoritesEditorExternalFieldDecision,
    snapshot: FavoritesStorageSnapshot,
    entries: list[_ProvenanceEntry],
) -> FavoritesStorageSnapshot:
    preview = decision.preview
    if preview.target is None:
        raise FavoritesEditorExternalPlanningError(
            "Mapped-field decisions require a linked local target."
        )
    observation = _observation(context, preview)
    mapping = context.service.radioreference_field_mapping(
        context.result,
        preview,
        observation,
        decision.field,
    )
    entry = _baseline_entry(entries, preview)
    current_target = select_favorites_record_target(
        snapshot,
        entry.source_index,
        document_index=entry.document_index,
    )
    name = mapping.field.name
    existing = _field_state(
        entry.state,
        name=name,
        field_index=mapping.field_index,
    )

    if decision.choice is FavoritesEditorExternalFieldChoice.EXTERNAL:
        snapshot = _replace_favorites_record_field(
            snapshot,
            current_target,
            mapping.field_index,
            mapping.scanner_value,
        )
        current_target = select_favorites_record_target(
            snapshot,
            entry.source_index,
            document_index=entry.document_index,
        )
        accepted = FavoritesExternalFieldState(
            name=name,
            field_index=mapping.field_index,
            ownership=FavoritesExternalFieldOwnership.EXTERNAL,
            last_external=mapping.field,
        )
        fields = (
            (*entry.state.fields, accepted)
            if existing is None
            else tuple(accepted if field is existing else field for field in entry.state.fields)
        )
        entry.state = replace(
            entry.state,
            target=current_target,
            fields=fields,
            last_observation=observation.evidence,
        )
        return snapshot

    if existing is None:
        existing = FavoritesExternalFieldState(
            name=name,
            field_index=mapping.field_index,
            ownership=FavoritesExternalFieldOwnership.LOCAL,
        )
        entry.state = replace(entry.state, fields=(*entry.state.fields, existing))

    if decision.choice is FavoritesEditorExternalFieldChoice.LOCAL:
        local = replace(
            existing,
            ownership=FavoritesExternalFieldOwnership.LOCAL,
            last_external=None,
        )
        entry.state = replace(
            entry.state,
            fields=tuple(local if field is existing else field for field in entry.state.fields),
            last_observation=observation.evidence,
        )
        return snapshot

    if existing.ownership is not FavoritesExternalFieldOwnership.EXTERNAL:
        raise FavoritesEditorExternalPlanningError(
            "Field detach requires an externally owned field."
        )
    entry.state = detach_favorites_external_field(entry.state, name)
    return snapshot


def _record_operation(
    context: FavoritesEditorExternalPlanningContext,
    decision: FavoritesEditorExternalRecordDecision,
    entries: list[_ProvenanceEntry],
) -> _StructuralOperation | None:
    preview = decision.preview
    choice = decision.choice
    if choice is FavoritesEditorExternalRecordChoice.IMPORT:
        assert decision.anchor is not None
        assert decision.template is not None
        context.service.plan_record_import(
            context.result,
            preview,
            decision.anchor,
            decision.template,
            decision.bindings,
        )
        if (
            decision.anchor.source_kind is not FavoritesRecordSourceKind.HPD
            or decision.anchor.document_index is None
        ):
            raise FavoritesEditorExternalPlanningError(
                "External record import requires an HPD insertion anchor."
            )
        return _StructuralOperation(
            decision,
            decision.anchor.document_index,
            decision.anchor.source_index,
        )
    if choice is FavoritesEditorExternalRecordChoice.IGNORE:
        if preview.kind is not FavoritesExternalChangeKind.ADDED or preview.target is not None:
            raise FavoritesEditorExternalPlanningError(
                "Ignore is available only for an unbound added provider record."
            )
        _observation(context, preview)
        return None

    entry = _baseline_entry(entries, preview)
    if choice is FavoritesEditorExternalRecordChoice.DELETE:
        context.service.plan_record_delete(context.result, preview)
        if entry.document_index is None:
            raise FavoritesEditorExternalPlanningError(
                "External record deletion requires an HPD target."
            )
        return _StructuralOperation(
            decision,
            entry.document_index,
            entry.source_index,
        )
    if choice is FavoritesEditorExternalRecordChoice.KEEP_LOCAL:
        plan = context.service.plan_record_keep_local(context.result, preview)
        entry.state = plan.intended_state
        return None
    plan = context.service.plan_detach(
        context.result,
        preview,
        FavoritesExternalRefreshDetachScope.RECORD,
    )
    entry.state = plan.intended_state
    return None


def _apply_structural_operations(
    context: FavoritesEditorExternalPlanningContext,
    operations: list[_StructuralOperation],
    snapshot: FavoritesStorageSnapshot,
    entries: list[_ProvenanceEntry],
) -> tuple[FavoritesStorageSnapshot, dict[int, tuple[int, ...]], dict[int, tuple[int, ...]]]:
    locations = [(item.document_index, item.source_index) for item in operations]
    if len(set(locations)) != len(locations):
        raise FavoritesEditorExternalPlanningError(
            "External structural decisions target the same baseline location."
        )
    deleted_locations = {
        (item.document_index, item.source_index)
        for item in operations
        if item.decision.choice is FavoritesEditorExternalRecordChoice.DELETE
    }
    imported_locations = {
        (item.document_index, item.source_index)
        for item in operations
        if item.decision.choice is FavoritesEditorExternalRecordChoice.IMPORT
    }
    if deleted_locations & imported_locations:
        raise FavoritesEditorExternalPlanningError(
            "An external import anchor cannot also be deleted."
        )

    deletes: dict[int, list[int]] = {}
    imports: dict[int, list[int]] = {}
    for operation in sorted(
        operations,
        key=lambda item: (item.document_index, item.source_index),
        reverse=True,
    ):
        decision = operation.decision
        current_target = select_favorites_record_target(
            snapshot,
            operation.source_index,
            document_index=operation.document_index,
        )
        if decision.choice is FavoritesEditorExternalRecordChoice.DELETE:
            snapshot = delete_favorites_record(snapshot, current_target)
            deletes.setdefault(operation.document_index, []).append(operation.source_index)
            entries[:] = [
                entry
                for entry in entries
                if not (
                    entry.import_anchor_index is None
                    and entry.document_index == operation.document_index
                    and entry.source_index == operation.source_index
                    and entry.state.external_identity == decision.preview.external_identity
                )
            ]
            continue

        assert decision.anchor is not None
        assert decision.template is not None
        observation = _observation(context, decision.preview)
        snapshot = create_favorites_record_after(
            snapshot,
            current_target,
            decision.template,
        )
        created = select_favorites_record_target(
            snapshot,
            operation.source_index + 1,
            document_index=operation.document_index,
        )
        state = bind_favorites_external_record(
            created,
            observation,
            decision.bindings,
        )
        entries.append(
            _ProvenanceEntry(
                state,
                operation.document_index,
                operation.source_index + 1,
                import_anchor_index=operation.source_index,
            )
        )
        imports.setdefault(operation.document_index, []).append(operation.source_index)
    return (
        snapshot,
        {key: tuple(sorted(value)) for key, value in deletes.items()},
        {key: tuple(sorted(value)) for key, value in imports.items()},
    )


def _rebind_entries(
    snapshot: FavoritesStorageSnapshot,
    entries: list[_ProvenanceEntry],
    deletes: dict[int, tuple[int, ...]],
    imports: dict[int, tuple[int, ...]],
) -> tuple[FavoritesExternalRecordState, ...]:
    rebound: list[FavoritesExternalRecordState] = []
    for entry in entries:
        if entry.document_index is None:
            final_index = entry.source_index
        elif entry.import_anchor_index is None:
            final_index = (
                entry.source_index
                + sum(
                    anchor < entry.source_index for anchor in imports.get(entry.document_index, ())
                )
                - sum(
                    deleted < entry.source_index
                    for deleted in deletes.get(entry.document_index, ())
                )
            )
        else:
            final_index = (
                entry.import_anchor_index
                + 1
                + sum(
                    anchor < entry.import_anchor_index
                    for anchor in imports.get(entry.document_index, ())
                )
                - sum(
                    deleted < entry.import_anchor_index
                    for deleted in deletes.get(entry.document_index, ())
                )
            )
        target = select_favorites_record_target(
            snapshot,
            final_index,
            document_index=entry.document_index,
        )
        if target.record.raw_bytes != entry.state.target.record.raw_bytes:
            raise FavoritesEditorExternalPlanningError(
                "External planning could not exactly rebind intended provenance."
            )
        rebound.append(replace(entry.state, target=target))
    records = tuple(rebound)
    content = serialize_favorites_external_provenance(records)
    if deserialize_favorites_external_provenance(content, snapshot) != records:
        raise FavoritesEditorExternalPlanningError(
            "External planning intended provenance did not exactly round trip."
        )
    return records


def plan_favorites_editor_external_decisions(
    context: FavoritesEditorExternalPlanningContext,
    decisions: tuple[FavoritesEditorExternalDecision, ...],
) -> FavoritesEditorExternalPlanningSnapshot:
    """Compose explicit decisions into one exact unexecuted synchronization plan."""

    if not isinstance(context, FavoritesEditorExternalPlanningContext):
        raise TypeError("External planning requires a current exact planning context.")
    if type(decisions) is not tuple:
        raise TypeError("External planning decisions must be an immutable tuple.")
    if any(
        type(decision)
        not in {
            FavoritesEditorExternalFieldDecision,
            FavoritesEditorExternalRecordDecision,
        }
        for decision in decisions
    ):
        raise TypeError("External planning received an invalid decision.")
    if context.service.lifecycle_snapshot != context.result.lifecycle_snapshot:
        raise FavoritesEditorExternalPlanningError(
            "External planning context is stale or belongs to another lifecycle."
        )

    keys = tuple(_decision_key(decision) for decision in decisions)
    if len(set(keys)) != len(keys):
        raise FavoritesEditorExternalPlanningError(
            "External planning received duplicate decisions for one selection."
        )
    record_previews = {
        id(decision.preview)
        for decision in decisions
        if isinstance(decision, FavoritesEditorExternalRecordDecision)
    }
    if any(
        id(decision.preview) in record_previews
        for decision in decisions
        if isinstance(decision, FavoritesEditorExternalFieldDecision)
    ):
        raise FavoritesEditorExternalPlanningError(
            "Record and field decisions for the same preview are contradictory."
        )
    for decision in decisions:
        _require_preview(context, decision.preview)

    baseline = context.result.lifecycle_snapshot.favorites_snapshot
    if baseline is None:
        raise FavoritesEditorExternalPlanningError(
            "External planning requires retained Favorites baseline evidence."
        )
    baseline_records = context.result.lifecycle_snapshot.provenance_records
    entries = [
        _ProvenanceEntry(
            state=state,
            document_index=state.target.document_index,
            source_index=state.target.source_index,
        )
        for state in (baseline_records or ())
    ]
    intended = baseline
    for decision in decisions:
        if isinstance(decision, FavoritesEditorExternalFieldDecision):
            intended = _apply_field_decision(
                context,
                decision,
                intended,
                entries,
            )

    operations: list[_StructuralOperation] = []
    for decision in decisions:
        if isinstance(decision, FavoritesEditorExternalRecordDecision):
            operation = _record_operation(context, decision, entries)
            if operation is not None:
                operations.append(operation)
    intended, deletes, imports = _apply_structural_operations(
        context,
        operations,
        intended,
        entries,
    )

    provenance_changed = bool(decisions) and (bool(entries) or baseline_records is not None)
    intended_records = (
        _rebind_entries(intended, entries, deletes, imports)
        if provenance_changed
        else baseline_records
    )
    write_plan = plan_favorites_write(baseline, intended)
    unresolved, unresolved_conflict = _decision_completeness(
        context.result.preview.records,
        decisions,
    )
    blockers: list[FavoritesEditorExternalPlanningBlocker] = []
    if unresolved:
        blockers.append(FavoritesEditorExternalPlanningBlocker.INCOMPLETE_DECISIONS)
    if unresolved_conflict:
        blockers.append(FavoritesEditorExternalPlanningBlocker.UNRESOLVED_CONFLICTS)
    if write_plan.is_blocked:
        blockers.append(FavoritesEditorExternalPlanningBlocker.WRITE_PLAN_BLOCKED)
    return FavoritesEditorExternalPlanningSnapshot(
        refresh_result=context.result,
        decisions=decisions,
        write_plan=write_plan,
        baseline_provenance_records=baseline_records,
        intended_provenance_records=intended_records,
        unresolved_decisions=unresolved,
        blockers=tuple(blockers),
    )


__all__ = [
    "FavoritesEditorExternalDecision",
    "FavoritesEditorExternalFieldChoice",
    "FavoritesEditorExternalFieldDecision",
    "FavoritesEditorExternalPlanningBlocker",
    "FavoritesEditorExternalPlanningError",
    "FavoritesEditorExternalPlanningSnapshot",
    "FavoritesEditorExternalRecordChoice",
    "FavoritesEditorExternalRecordDecision",
    "plan_favorites_editor_external_decisions",
    "prepare_favorites_editor_external_import_decision",
]
