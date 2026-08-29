"""Local Textual adapter for the renderer-neutral Favorites editor."""

from __future__ import annotations

import threading
from contextlib import suppress
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static, Tree

from .favorites_comparison import FavoritesComparisonAmbiguity, FavoritesComparisonSource
from .favorites_editor import (
    FavoritesEditorError,
    FavoritesEditorExecution,
    FavoritesEditorRecordReference,
    FavoritesEditorReview,
    FavoritesEditorSession,
)
from .favorites_editor_external_planning import (
    FavoritesEditorExternalDecision,
    FavoritesEditorExternalFieldChoice,
    FavoritesEditorExternalFieldDecision,
    FavoritesEditorExternalPlanningError,
    FavoritesEditorExternalPlanningSnapshot,
    FavoritesEditorExternalRecordChoice,
    FavoritesEditorExternalRecordDecision,
    plan_favorites_editor_external_decisions,
    prepare_favorites_editor_external_import_decision,
)
from .favorites_editor_external_preview import (
    FavoritesEditorExternalPlanningContext,
    FavoritesEditorExternalPreviewController,
    FavoritesEditorExternalPreviewError,
    FavoritesEditorExternalPreviewSnapshot,
    FavoritesEditorExternalPreviewState,
)
from .favorites_external import FavoritesExternalRecordPreview
from .favorites_external_assisted_sync import RadioReferenceFavoritesMappedField
from .favorites_navigation import FavoritesNavigationNode, FavoritesNavigationPath


def _node_label(node: FavoritesNavigationNode) -> str:
    name = node.name if node.name is not None else "<unnamed>"
    return f"{name}  [{node.kind.value}]"


def _plan_text(session: FavoritesEditorSession) -> str:
    plan = session.plan
    change_count = sum(
        len(item.record_changes)
        for item in plan.comparison.items
        if isinstance(item, FavoritesComparisonSource)
    )
    ambiguity_count = sum(
        isinstance(item, FavoritesComparisonAmbiguity) for item in plan.comparison.items
    )
    blockers = ", ".join(blocker.value for blocker in plan.blockers) or "none"
    return (
        f"Changes: {change_count}\n"
        f"Comparison ambiguities: {ambiguity_count}\n"
        f"Blockers: {blockers}\n"
        f"Undo steps: {session.undo_count}"
    )


def _review_text(review: FavoritesEditorReview) -> str:
    rows = ["Exact write plan"]
    for item in review.plan.comparison.items:
        if isinstance(item, FavoritesComparisonAmbiguity):
            rows.append(f"AMBIGUOUS {item.filename}")
            continue
        source = item.filename or "f_list.cfg"
        for change in item.record_changes:
            baseline = (
                "-"
                if change.baseline_record is None
                else f"{change.baseline_source_index}:{change.baseline_record.raw_bytes!r}"
            )
            candidate = (
                "-"
                if change.candidate_record is None
                else f"{change.candidate_source_index}:{change.candidate_record.raw_bytes!r}"
            )
            rows.append(f"{source} {change.kind.value}: {baseline} -> {candidate}")
    blockers = ", ".join(blocker.value for blocker in review.plan.blockers) or "none"
    rows.append(f"Blockers: {blockers}")
    return "\n".join(rows)


def _external_preview_text(
    snapshot: FavoritesEditorExternalPreviewSnapshot | None,
) -> str:
    if snapshot is None:
        return "RadioReference preview: not configured"
    rows = [f"RadioReference preview: {snapshot.state.value}"]
    if snapshot.error is not None:
        rows.append(f"Failure: {snapshot.error}")
    if snapshot.stale_reason is not None:
        rows.append(f"Stale: {snapshot.stale_reason}")
    presentation = snapshot.presentation
    if presentation is None:
        rows.append("No provider read has been requested.")
        return "\n".join(rows)
    rows.extend(
        (
            f"Source: {presentation.provider} / {presentation.dataset}",
            f"Refresh: {presentation.started_at} -> {presentation.completed_at}",
            f"Observations: {presentation.observation_count}",
            "Classifications: "
            + ", ".join(f"{name}={count}" for name, count in presentation.counts),
        )
    )
    for index, record in enumerate(presentation.records, start=1):
        rows.append(
            f"[{index}] {record.kind} local={record.local_target} "
            f"external={record.external_record_id} observed={record.observed_at} "
            f"revision={record.revision}"
        )
        if not record.fields:
            rows.append("    fields: none")
        for field in record.fields:
            rows.append(
                f"    {field.name}: {field.kind}; ownership={field.ownership}; "
                f"local={field.local_value!r}; external_state={field.external_state}; "
                f"external={field.external_value!r}"
            )
    return "\n".join(rows)


def _external_plan_text(
    plan: FavoritesEditorExternalPlanningSnapshot | None,
) -> str:
    if plan is None:
        return (
            "Assisted synchronization plan: unavailable\n"
            "Refresh first, then make explicit decisions. No assisted plan can execute."
        )
    blockers = ", ".join(blocker.value for blocker in plan.blockers) or "none"
    return "\n".join(
        (
            "Assisted synchronization plan: UNEXECUTED",
            f"Decisions: {len(plan.decisions)}",
            f"Unresolved supported decisions: {plan.unresolved_decisions}",
            f"Favorites bytes changed: {'yes' if plan.has_favorites_changes else 'no'}",
            f"Provenance changed: {'yes' if plan.has_provenance_changes else 'no'}",
            f"Write-plan blockers: {blockers}",
            "Milestone 28.2 cannot write Favorites or publish provenance.",
        )
    )


def _external_prepared_import_text(
    decision: FavoritesEditorExternalRecordDecision | None,
) -> str:
    if decision is None:
        return (
            "Prepared import: none\n"
            "Select an exact local template and prepare an import before adoption."
        )
    anchor = decision.anchor
    template = decision.template
    assert anchor is not None
    assert template is not None
    identity = decision.preview.external_identity
    external_record = "unavailable" if identity is None else identity.record_id
    filename = anchor.filename or "f_list.cfg"
    bindings = ", ".join(
        f"{binding.name}@{binding.field_index}={binding.ownership.value}"
        for binding in decision.bindings
    )
    return "\n".join(
        (
            "Prepared import: NOT ADOPTED",
            f"Provider record: {external_record}",
            f"Insertion anchor: {filename}:{anchor.source_index}",
            f"Derived target: {filename}:{anchor.source_index + 1}",
            f"Template command: {template.command}",
            f"Bindings: {bindings}",
            f"Resulting raw record: {template.raw_bytes!r}",
            "Review this evidence, then choose Adopt prepared import.",
        )
    )


class FavoritesEditorApp(App[None]):
    """Full-screen local Favorites browser, editor, and write reviewer."""

    TITLE = "sdsctl Favorites Workspace Editor"
    CSS = """
    #body { height: 1fr; }
    #navigation { width: 48%; min-width: 36; border: solid $primary; }
    #detail-column { width: 1fr; padding: 0 1; }
    #favorites-tree, #records-tree { height: 1fr; }
    #search-results { height: 5; border: solid $secondary; padding: 0 1; }
    #detail, #diagnostics, #plan, #external-preview, #external-plan,
    #external-import-preview, #status {
        border: solid $secondary; padding: 0 1;
    }
    #detail { min-height: 7; }
    #diagnostics { min-height: 5; }
    #plan { min-height: 6; }
    #external-preview { min-height: 5; }
    #external-plan { min-height: 7; }
    #external-import-preview { min-height: 5; }
    #status { min-height: 4; }
    #controls { height: auto; }
    #edit-name, #confirmation { margin: 1 0 0 0; }
    Button { margin: 0 1 0 0; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("u", "undo", "Undo"),
        Binding("ctrl+r", "reset_edits", "Reset"),
        Binding("ctrl+p", "review", "Review"),
        Binding("ctrl+x", "execute", "Execute"),
        Binding("ctrl+g", "external_refresh", "RR refresh"),
        Binding("ctrl+e", "external_use", "RR use external"),
        Binding("ctrl+l", "external_local", "RR keep local"),
        Binding("ctrl+d", "external_detach", "RR detach field"),
        Binding("/", "focus_search", "Search"),
    ]

    def __init__(
        self,
        session: FavoritesEditorSession,
        external_preview: FavoritesEditorExternalPreviewController | None = None,
    ) -> None:
        super().__init__()
        if not isinstance(session, FavoritesEditorSession):
            raise TypeError("Favorites editor TUI requires FavoritesEditorSession.")
        self.session = session
        if external_preview is not None and not isinstance(
            external_preview,
            FavoritesEditorExternalPreviewController,
        ):
            raise TypeError(
                "Favorites editor external preview must be "
                "FavoritesEditorExternalPreviewController or None."
            )
        self.external_preview = external_preview
        self._external_decisions: tuple[FavoritesEditorExternalDecision, ...] = ()
        self._external_plan: FavoritesEditorExternalPlanningSnapshot | None = None
        self._external_prepared_import: FavoritesEditorExternalRecordDecision | None = None
        self._external_refresh_thread: threading.Thread | None = None
        self.selected_path: FavoritesNavigationPath | None = None
        self.selected_record: FavoritesEditorRecordReference | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="navigation"):
                yield Input(placeholder="Search names (/)", id="search")
                yield Static("Search results: all nodes", id="search-results")
                yield Tree[FavoritesNavigationPath]("Favorites", id="favorites-tree")
                yield Tree[FavoritesEditorRecordReference](
                    "All exact source records",
                    id="records-tree",
                )
            with VerticalScroll(id="detail-column"):
                yield Static("Select a Favorites record.", id="detail")
                yield Static("Diagnostics", id="diagnostics")
                yield Static(_plan_text(self.session), id="plan")
                yield Static(
                    _external_preview_text(
                        None if self.external_preview is None else self.external_preview.snapshot()
                    ),
                    id="external-preview",
                )
                yield Button("Refresh RadioReference preview", id="external-refresh")
                yield Static(_external_plan_text(None), id="external-plan")
                yield Input(
                    placeholder="RadioReference preview record number (for example 1)",
                    id="external-record-index",
                )
                yield Input(
                    placeholder="Mapped field: name, frequency, or talkgroup_decimal",
                    id="external-field",
                )
                with Horizontal(id="external-field-controls"):
                    yield Button("Use external", id="external-use")
                    yield Button("Keep local", id="external-local")
                    yield Button("Detach field", id="external-detach")
                with Horizontal(id="external-record-controls"):
                    yield Button(
                        "Prepare import after selected template",
                        id="external-import-prepare",
                    )
                    yield Button("Adopt prepared import", id="external-import-adopt")
                    yield Button("Ignore added", id="external-ignore")
                    yield Button("Delete removed", id="external-delete", variant="warning")
                    yield Button("Keep local record", id="external-keep-record")
                    yield Button("Detach record", id="external-detach-record")
                yield Static(
                    _external_prepared_import_text(None),
                    id="external-import-preview",
                )
                yield Button("Clear assisted decisions", id="external-clear")
                yield Input(placeholder="New Name Tag", id="edit-name")
                with Horizontal(id="controls"):
                    yield Button("Rename", id="rename")
                    yield Button("Duplicate leaf", id="duplicate")
                    yield Button("Delete leaf", id="delete", variant="warning")
                    yield Button("Undo", id="undo")
                    yield Button("Reset", id="reset")
                yield Button("Review exact plan", id="review", variant="primary")
                yield Input(
                    placeholder="Paste full review confirmation token",
                    id="confirmation",
                )
                yield Button("Execute confirmed plan", id="execute", variant="error")
                yield Static("No writes occur until review and confirmation.", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_tree()
        self._rebuild_record_tree()
        self._refresh_diagnostics()
        self._refresh_detail()
        self._refresh_external_preview()

    def on_unmount(self) -> None:
        if self.external_preview is not None:
            self.external_preview.close()

    def _append_tree_node(self, tree_node: Any, node: FavoritesNavigationNode) -> None:
        child = tree_node.add(
            _node_label(node),
            data=node.path,
            expand=True,
            allow_expand=bool(node.children),
        )
        for descendant in node.children:
            self._append_tree_node(child, descendant)

    def _rebuild_tree(self, preferred: FavoritesNavigationPath | None = None) -> None:
        tree = self.query_one("#favorites-tree", Tree)
        tree.clear()
        tree.root.expand()
        selected = None
        for root in self.session.navigation.roots:
            self._append_tree_node(tree.root, root)

        def find(tree_node: Any) -> Any:
            if tree_node.data == preferred:
                return tree_node
            for child in tree_node.children:
                match = find(child)
                if match is not None:
                    return match
            return None

        if preferred is not None:
            selected = find(tree.root)
        if selected is not None:
            tree.select_node(selected)
            self.selected_path = preferred
        elif self.session.navigation.roots:
            self.selected_path = self.session.navigation.roots[0].path

    def _rebuild_record_tree(self) -> None:
        tree = self.query_one("#records-tree", Tree)
        tree.clear()
        tree.root.expand()
        groups: dict[tuple[int | None, str], Any] = {}
        for reference in self.session.records():
            key = (reference.document_index, reference.filename)
            group = groups.get(key)
            if group is None:
                group = tree.root.add(reference.filename, expand=False)
                groups[key] = group
            group.add(
                f"{reference.source_index}: {reference.record.command}",
                data=reference,
                allow_expand=False,
            )

    def _selected(self) -> FavoritesNavigationPath:
        if self.selected_path is None:
            raise FavoritesEditorError("Select a Favorites record first.")
        return self.selected_path

    def _selected_reference(self) -> FavoritesEditorRecordReference | None:
        return self.selected_record

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def _refresh_external_preview(self) -> None:
        snapshot = None if self.external_preview is None else self.external_preview.snapshot()
        context = (
            None if self.external_preview is None else self.external_preview.planning_context()
        )
        self.query_one("#external-preview", Static).update(_external_preview_text(snapshot))
        self.query_one("#external-plan", Static).update(_external_plan_text(self._external_plan))
        self.query_one("#external-import-preview", Static).update(
            _external_prepared_import_text(self._external_prepared_import)
        )
        button = self.query_one("#external-refresh", Button)
        button.disabled = (
            self.external_preview is None
            or self.session.has_changes
            or (
                snapshot is not None
                and snapshot.state is FavoritesEditorExternalPreviewState.REFRESHING
            )
        )
        planning_disabled = context is None or self.session.has_changes
        for control_id in (
            "external-use",
            "external-local",
            "external-detach",
            "external-import-prepare",
            "external-import-adopt",
            "external-ignore",
            "external-delete",
            "external-keep-record",
            "external-detach-record",
            "external-clear",
        ):
            self.query_one(f"#{control_id}", Button).disabled = planning_disabled
        self.query_one("#external-import-adopt", Button).disabled = (
            planning_disabled or self._external_prepared_import is None
        )
        for input_id in ("external-record-index", "external-field"):
            self.query_one(f"#{input_id}", Input).disabled = planning_disabled

    def _clear_external_planning(self) -> None:
        self._external_decisions = ()
        self._external_plan = None
        self._external_prepared_import = None
        self.query_one("#external-plan", Static).update(_external_plan_text(None))
        self.query_one("#external-import-preview", Static).update(
            _external_prepared_import_text(None)
        )

    def _invalidate_external_preview(self, reason: str) -> None:
        self._clear_external_planning()
        if self.external_preview is not None:
            self.external_preview.invalidate(reason)
        self._refresh_external_preview()

    def _refresh_detail(self) -> None:
        if self.selected_record is not None:
            try:
                raw_bytes = self.session.raw_source_record(self.selected_record)
            except (FavoritesEditorError, ValueError) as error:
                self.query_one("#detail", Static).update(str(error))
                return
            raw = raw_bytes.decode("ascii", errors="backslashreplace").rstrip("\r\n")
            self.query_one("#detail", Static).update(
                f"Raw source: {self.selected_record.filename}:"
                f"{self.selected_record.source_index}\n"
                f"Command: {self.selected_record.record.command}\n"
                f"Fields: {self.selected_record.record.field_count}\n"
                f"Raw: {raw}"
            )
            return
        if self.selected_path is None:
            return
        try:
            node = self.session.node(self.selected_path)
            target = self.session.target(self.selected_path)
        except FavoritesEditorError as error:
            self.query_one("#detail", Static).update(str(error))
            return
        raw = target.record.raw_bytes.decode("ascii", errors="backslashreplace").rstrip("\r\n")
        document = target.filename or "f_list.cfg"
        self.query_one("#detail", Static).update(
            f"Name: {node.name or '<unnamed>'}\n"
            f"Kind: {node.kind.value}\n"
            f"Source: {document}:{target.source_index}\n"
            f"Path: {node.path.indexes!r}\n"
            f"Raw: {raw}"
        )

    def _refresh_diagnostics(self) -> None:
        diagnostics = self.session.validation.diagnostics
        if not diagnostics:
            text = "Diagnostics: none"
        else:
            rows = [f"Diagnostics: {len(diagnostics)}"]
            rows.extend(f"{item.severity.value}: {item.message}" for item in diagnostics[:8])
            if len(diagnostics) > 8:
                rows.append(f"… {len(diagnostics) - 8} more")
            text = "\n".join(rows)
        self.query_one("#diagnostics", Static).update(text)
        self.query_one("#plan", Static).update(_plan_text(self.session))

    def _after_edit(self, preferred: FavoritesNavigationPath | None, message: str) -> None:
        self._invalidate_external_preview("Favorites editor state changed")
        self.query_one("#confirmation", Input).value = ""
        self._rebuild_tree(preferred)
        self._rebuild_record_tree()
        self._refresh_detail()
        self._refresh_diagnostics()
        self._set_status(message)

    def on_tree_node_selected(
        self,
        event: Tree.NodeSelected[Any],
    ) -> None:
        if event.node.data is None:
            return
        if event.control.id == "records-tree":
            self.selected_record = event.node.data
            self.selected_path = None
        else:
            self.selected_path = event.node.data
            self.selected_record = None
        self._refresh_detail()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        text = event.value.strip()
        matches = self.session.nodes(text if text else None)
        labels = tuple(node.name or "<unnamed>" for node in matches[:6])
        suffix = "" if len(matches) <= 6 else f"\n… {len(matches) - 6} more"
        display = "\n".join(labels) if labels else "No matches"
        self.query_one("#search-results", Static).update(
            f"Search results: {len(matches)}\n{display}{suffix}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "rename": self.action_rename,
            "duplicate": self.action_duplicate,
            "delete": self.action_delete,
            "undo": self.action_undo,
            "reset": self.action_reset_edits,
            "review": self.action_review,
            "execute": self.action_execute,
            "external-refresh": self.action_external_refresh,
            "external-use": self.action_external_use,
            "external-local": self.action_external_local,
            "external-detach": self.action_external_detach,
            "external-import-prepare": self.action_external_import_prepare,
            "external-import-adopt": self.action_external_import_adopt,
            "external-ignore": self.action_external_ignore,
            "external-delete": self.action_external_delete,
            "external-keep-record": self.action_external_keep_record,
            "external-detach-record": self.action_external_detach_record,
            "external-clear": self.action_external_clear,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_rename(self) -> None:
        try:
            name = self.query_one("#edit-name", Input).value
            reference = self._selected_reference()
            if reference is not None:
                self.session.rename_record(reference, name)
                preferred = None
            else:
                preferred = self._selected()
                self.session.rename(preferred, name)
            self._after_edit(
                preferred,
                "Name Tag replaced in memory. Review before writing.",
            )
        except (FavoritesEditorError, ValueError) as error:
            self._set_status(f"Rename rejected: {error}")

    def action_duplicate(self) -> None:
        try:
            value = self.query_one("#edit-name", Input).value
            reference = self._selected_reference()
            if reference is not None:
                self.session.duplicate_record(reference, name=value or None)
                preferred = None
            else:
                preferred = self._selected()
                self.session.duplicate(preferred, name=value or None)
            self._after_edit(preferred, "Exact-template leaf created in memory.")
        except (FavoritesEditorError, ValueError) as error:
            self._set_status(f"Create rejected: {error}")

    def action_delete(self) -> None:
        try:
            reference = self._selected_reference()
            if reference is not None:
                self.session.delete_record(reference)
                parent = None
                self.selected_record = None
            else:
                path = self._selected()
                parent = path.parent
                self.session.delete(path)
            self._after_edit(parent, "Supported HPD leaf deleted in memory.")
        except (FavoritesEditorError, ValueError) as error:
            self._set_status(f"Delete rejected: {error}")

    def action_undo(self) -> None:
        changed = self.session.undo()
        self._after_edit(self.selected_path, "Undo applied." if changed else "Nothing to undo.")

    def action_reset_edits(self) -> None:
        changed = self.session.reset()
        self._after_edit(
            self.selected_path,
            "All in-memory edits discarded." if changed else "No edits to reset.",
        )

    def _external_context(self) -> FavoritesEditorExternalPlanningContext:
        if self.external_preview is None:
            raise FavoritesEditorExternalPlanningError("RadioReference preview is not configured.")
        context = self.external_preview.planning_context()
        if context is None:
            raise FavoritesEditorExternalPlanningError(
                "Refresh RadioReference before making assisted decisions."
            )
        return context

    def _external_selected_preview(
        self,
    ) -> tuple[
        FavoritesEditorExternalPlanningContext,
        FavoritesExternalRecordPreview,
    ]:
        context = self._external_context()
        raw_index = self.query_one("#external-record-index", Input).value.strip()
        try:
            index = int(raw_index)
        except ValueError:
            raise FavoritesEditorExternalPlanningError(
                "Enter a valid one-based RadioReference preview record number."
            ) from None
        if index < 1 or index > len(context.result.preview.records):
            raise FavoritesEditorExternalPlanningError(
                "RadioReference preview record number is outside the current result."
            )
        return context, context.result.preview.records[index - 1]

    def _external_selected_field(self) -> RadioReferenceFavoritesMappedField:
        value = self.query_one("#external-field", Input).value.strip().lower().replace("-", "_")
        try:
            return RadioReferenceFavoritesMappedField(value)
        except ValueError:
            raise FavoritesEditorExternalPlanningError(
                "Mapped field must be name, frequency, or talkgroup_decimal."
            ) from None

    @staticmethod
    def _same_external_decision_slot(
        existing: FavoritesEditorExternalDecision,
        replacement: FavoritesEditorExternalDecision,
    ) -> bool:
        if isinstance(
            existing,
            FavoritesEditorExternalFieldDecision,
        ) and isinstance(replacement, FavoritesEditorExternalFieldDecision):
            return existing.preview is replacement.preview and existing.field is replacement.field
        if isinstance(
            existing,
            FavoritesEditorExternalRecordDecision,
        ) and isinstance(replacement, FavoritesEditorExternalRecordDecision):
            return existing.preview is replacement.preview
        return False

    def _adopt_external_decision(
        self,
        decision: FavoritesEditorExternalDecision,
    ) -> None:
        context = self._external_context()
        decisions = tuple(
            existing
            for existing in self._external_decisions
            if not self._same_external_decision_slot(existing, decision)
        ) + (decision,)
        plan = plan_favorites_editor_external_decisions(context, decisions)
        self._external_decisions = decisions
        self._external_plan = plan
        self._refresh_external_preview()
        self._set_status(
            "Assisted decision adopted in memory. "
            f"Unresolved: {plan.unresolved_decisions}. "
            "This assisted plan cannot execute in Milestone 28.2."
        )

    def _choose_external_field(
        self,
        choice: FavoritesEditorExternalFieldChoice,
    ) -> None:
        try:
            _context, preview = self._external_selected_preview()
            self._adopt_external_decision(
                FavoritesEditorExternalFieldDecision(
                    preview,
                    self._external_selected_field(),
                    choice,
                )
            )
        except (FavoritesEditorExternalPlanningError, ValueError) as error:
            self._set_status(f"Assisted field decision rejected: {error}")

    def action_external_use(self) -> None:
        self._choose_external_field(FavoritesEditorExternalFieldChoice.EXTERNAL)

    def action_external_local(self) -> None:
        self._choose_external_field(FavoritesEditorExternalFieldChoice.LOCAL)

    def action_external_detach(self) -> None:
        self._choose_external_field(FavoritesEditorExternalFieldChoice.DETACHED)

    def _choose_external_record(
        self,
        choice: FavoritesEditorExternalRecordChoice,
    ) -> None:
        try:
            _context, preview = self._external_selected_preview()
            self._adopt_external_decision(FavoritesEditorExternalRecordDecision(preview, choice))
        except (FavoritesEditorExternalPlanningError, ValueError) as error:
            self._set_status(f"Assisted record decision rejected: {error}")

    def action_external_ignore(self) -> None:
        self._choose_external_record(FavoritesEditorExternalRecordChoice.IGNORE)

    def action_external_delete(self) -> None:
        self._choose_external_record(FavoritesEditorExternalRecordChoice.DELETE)

    def action_external_keep_record(self) -> None:
        self._choose_external_record(FavoritesEditorExternalRecordChoice.KEEP_LOCAL)

    def action_external_detach_record(self) -> None:
        self._choose_external_record(FavoritesEditorExternalRecordChoice.DETACHED)

    def action_external_import_prepare(self) -> None:
        try:
            context, preview = self._external_selected_preview()
            reference = self._selected_reference()
            if reference is not None:
                anchor = self.session.record_target(reference)
            else:
                anchor = self.session.target(self._selected())
            decision = prepare_favorites_editor_external_import_decision(
                context,
                preview,
                anchor,
            )
            # Recompute the exact aggregate form as a final pure validation of the
            # proposed anchor, template, bindings, result bytes, and write blockers.
            plan_favorites_editor_external_decisions(context, (decision,))
            self._external_prepared_import = decision
            self._refresh_external_preview()
            self._set_status("Import prepared but not adopted. Review the complete exact evidence.")
        except (
            FavoritesEditorError,
            FavoritesEditorExternalPlanningError,
            ValueError,
        ) as error:
            self._external_prepared_import = None
            self._refresh_external_preview()
            self._set_status(f"Assisted import decision rejected: {error}")

    def action_external_import_adopt(self) -> None:
        decision = self._external_prepared_import
        if decision is None:
            self._set_status("Prepare and review an assisted import before adoption.")
            return
        try:
            self._adopt_external_decision(decision)
        except (FavoritesEditorExternalPlanningError, ValueError) as error:
            self._set_status(f"Assisted import decision rejected: {error}")
            return
        self._external_prepared_import = None
        self._refresh_external_preview()

    def action_external_clear(self) -> None:
        try:
            context = self._external_context()
            self._external_decisions = ()
            self._external_prepared_import = None
            self._external_plan = plan_favorites_editor_external_decisions(
                context,
                (),
            )
            self._refresh_external_preview()
            self._set_status(
                "All assisted decisions cleared. Favorites and provenance are unchanged."
            )
        except FavoritesEditorExternalPlanningError as error:
            self._set_status(f"Assisted decision clearing unavailable: {error}")

    def action_external_refresh(self) -> None:
        if self.external_preview is None:
            self._set_status("RadioReference preview is not configured.")
            return
        if self._external_refresh_thread is not None and self._external_refresh_thread.is_alive():
            self._set_status("A RadioReference refresh is already in progress.")
            return
        if self.session.has_changes:
            self._invalidate_external_preview("unreviewed in-memory Favorites edits")
            self._set_status("RadioReference refresh is unavailable while unreviewed edits exist.")
            return
        self._clear_external_planning()
        self.query_one("#external-refresh", Button).disabled = True
        self.query_one("#external-preview", Static).update(
            "RadioReference preview: refreshing\nOne explicit provider read is in progress."
        )
        self._external_refresh_thread = threading.Thread(
            target=self._run_external_refresh,
            name="favorites-radioreference-refresh",
            daemon=True,
        )
        self._external_refresh_thread.start()
        self.set_timer(0.05, self._poll_external_refresh)

    def _run_external_refresh(self) -> None:
        assert self.external_preview is not None
        with suppress(FavoritesEditorExternalPreviewError):
            self.external_preview.refresh()

    def _poll_external_refresh(self) -> None:
        thread = self._external_refresh_thread
        if thread is not None and thread.is_alive():
            self.set_timer(0.05, self._poll_external_refresh)
            return
        self._external_refresh_thread = None
        if self.external_preview is not None:
            context = self.external_preview.planning_context()
            if context is not None:
                self._external_plan = plan_favorites_editor_external_decisions(
                    context,
                    (),
                )
        self._refresh_external_preview()

    def action_review(self) -> None:
        try:
            review = self.session.review()
        except FavoritesEditorError as error:
            self._set_status(f"Review unavailable: {error}")
            return
        blockers = ", ".join(blocker.value for blocker in review.plan.blockers) or "none"
        self.query_one("#plan", Static).update(_review_text(review))
        self._set_status(
            "Exact plan reviewed.\n"
            f"Blockers: {blockers}\n"
            f"Confirmation token: {review.confirmation_token}"
        )

    def action_execute(self) -> None:
        token = self.query_one("#confirmation", Input).value.strip()
        try:
            execution = self.session.execute(token)
        except (FavoritesEditorError, OSError, RuntimeError, ValueError) as error:
            self._set_status(self._execution_error_text(error))
            return
        self.query_one("#confirmation", Input).value = ""
        self._rebuild_tree(self.selected_path)
        self._rebuild_record_tree()
        self._refresh_diagnostics()
        self._invalidate_external_preview("Favorites write completed and baseline reloaded")
        self._set_status(self._execution_text(execution))

    @staticmethod
    def _execution_text(execution: FavoritesEditorExecution) -> str:
        result = execution.result
        values = [
            f"Execution: {result.status.value}",
            f"Target: {result.target_directory}",
            f"Operation ID: {result.operation_id or 'none (no-op)'}",
            f"Backup: {result.backup_directory or 'none'}",
            f"Rollback manifest: {result.rollback_manifest_path or 'none'}",
            f"Operation report: {result.operation_report_path or 'none'}",
            "Fresh reload: exact intended snapshot verified",
        ]
        return "\n".join(values)

    @staticmethod
    def _execution_error_text(error: BaseException) -> str:
        result = getattr(error, "result", None)
        operation_id = getattr(error, "operation_id", None)
        report_path = getattr(error, "report_path", None)
        recovery_status = getattr(error, "recovery_status", None)
        if result is not None:
            operation_id = result.operation_id
            report_path = result.operation_report_path
        return "\n".join(
            (
                f"Execution rejected or failed: {error}",
                f"Operation ID: {operation_id or 'unavailable'}",
                f"Operation report: {report_path or 'unavailable'}",
                f"Recovery: {recovery_status or 'unavailable'}",
            )
        )


def run_favorites_editor(
    session: FavoritesEditorSession,
    external_preview: FavoritesEditorExternalPreviewController | None = None,
) -> None:
    """Run the local interactive editor without opening scanner control."""

    FavoritesEditorApp(session, external_preview).run()


__all__ = ["FavoritesEditorApp", "run_favorites_editor"]
