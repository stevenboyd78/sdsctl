from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from textual.widgets import Button, Input, Static, Tree

from sds200 import (
    FavoritesEditorSession,
    FavoritesEditorSourceKind,
    FavoritesEditorWriteResult,
    FavoritesExternalFieldObservation,
    FavoritesExternalFieldObservationState,
    FavoritesExternalRecordObservation,
    FavoritesNavigationPath,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWritePlan,
)
from sds200.favorites_editor_tui import FavoritesEditorApp
from tests.test_favorites_editor_external_execution import _controller_plan
from tests.test_favorites_editor_external_preview import _controller, _observation

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "favorites"


class _ReadOnlyTestStorage:
    kind = FavoritesEditorSourceKind.COPIED_TREE

    def __init__(self, path: Path) -> None:
        self.requested_path = path
        self._snapshot = FavoritesStorageSnapshot(
            catalog_bytes=(_FIXTURE_ROOT / "synthetic-f_list.cfg").read_bytes(),
            documents=(
                FavoritesStorageDocument(
                    filename="f_000001.hpd",
                    content=(_FIXTURE_ROOT / "synthetic-favorites.hpd").read_bytes(),
                ),
            ),
        )

    @property
    def favorites_directory(self) -> Path:
        return self.requested_path

    def read_snapshot(self) -> FavoritesStorageSnapshot:
        return self._snapshot

    def execute(self, plan: FavoritesWritePlan) -> FavoritesEditorWriteResult:
        raise AssertionError("This UI test must not execute a write.")


def _app(tmp_path: Path) -> FavoritesEditorApp:
    session = FavoritesEditorSession.open(_ReadOnlyTestStorage(tmp_path.resolve()))
    return FavoritesEditorApp(session)


def _import_observation() -> FavoritesExternalRecordObservation:
    observation = _observation()
    return replace(
        observation,
        fields=(
            *observation.fields,
            FavoritesExternalFieldObservation(
                "frequency",
                FavoritesExternalFieldObservationState.VALUE,
                "154250000",
            ),
        ),
    )


def test_tui_mounts_complete_tree_and_searches_names(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = _app(tmp_path)

        async with app.run_test(size=(140, 50)) as pilot:
            tree = app.query_one("#favorites-tree", Tree)
            assert tree.root.children
            records = app.query_one("#records-tree", Tree)
            assert tuple(str(child.label) for child in records.root.children) == (
                "f_list.cfg",
                "f_000001.hpd",
            )

            app.query_one("#search", Input).value = "dispatch"
            await pilot.pause()

            results = str(app.query_one("#search-results", Static).render())
            assert "Synthetic Dispatch" in results

    asyncio.run(exercise())


def test_tui_edit_is_in_memory_and_review_requires_separate_token(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        app = _app(tmp_path)
        path = FavoritesNavigationPath((2, 2, 4, 5))

        async with app.run_test(size=(140, 50)) as pilot:
            app.selected_path = path
            app.query_one("#edit-name", Input).value = "TUI Rename"
            app.action_rename()
            await pilot.pause()

            assert app.session.node(path).name == "TUI Rename"
            assert app.session.has_changes
            assert app.query_one("#confirmation", Input).value == ""

            app.action_review()
            await pilot.pause()

            status = str(app.query_one("#status", Static).render())
            plan = str(app.query_one("#plan", Static).render())
            assert "Exact plan reviewed" in status
            assert "Confirmation token:" in status
            assert "f_000001.hpd replaced" in plan
            assert "Synthetic Channel" in plan
            assert "TUI Rename" in plan
            assert app.query_one("#confirmation", Input).value == ""

    asyncio.run(exercise())


def test_tui_reset_discards_without_writing(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = _app(tmp_path)
        path = FavoritesNavigationPath((2, 2, 4, 5))

        async with app.run_test(size=(140, 50)) as pilot:
            app.selected_path = path
            app.query_one("#edit-name", Input).value = "Temporary"
            app.action_rename()
            app.action_reset_edits()
            await pilot.pause()

            assert app.session.node(path).name == "Synthetic Channel"
            assert not app.session.has_changes
            assert "discarded" in str(app.query_one("#status", Static).render())

    asyncio.run(exercise())


def test_tui_external_refresh_is_explicit_and_renders_complete_preview(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        session, _storage, source, factory, controller = _controller(
            tmp_path,
            (_observation(),),
        )
        app = FavoritesEditorApp(session, controller)

        async with app.run_test(size=(160, 60)) as pilot:
            assert factory.calls == 0
            assert source.calls == 0
            button = app.query_one("#external-refresh", Button)
            assert not button.disabled

            app.action_external_refresh()
            for _attempt in range(20):
                await asyncio.sleep(0.01)
                await pilot.pause()
                if source.calls == 1 and not button.disabled:
                    break

            rendered = str(app.query_one("#external-preview", Static).render())
            assert source.calls == 1
            assert "RadioReference preview: succeeded" in rendered
            assert "radioreference / county-49-fire" in rendered
            assert "added=1" in rendered
            assert "external=frequency-100" in rendered
            assert "Fire Dispatch" in rendered

    asyncio.run(exercise())


def test_tui_edit_invalidates_preview_and_disables_refresh(tmp_path: Path) -> None:
    async def exercise() -> None:
        session, _storage, source, _factory, controller = _controller(
            tmp_path,
            (_observation(),),
        )
        app = FavoritesEditorApp(session, controller)

        async with app.run_test(size=(160, 60)) as pilot:
            app.action_external_refresh()
            for _attempt in range(20):
                await asyncio.sleep(0.01)
                await pilot.pause()
                if source.calls == 1:
                    break
            app.selected_path = FavoritesNavigationPath((2, 2, 4, 5))
            app.query_one("#edit-name", Input).value = "Edited after refresh"
            app.action_rename()
            await pilot.pause()

            rendered = str(app.query_one("#external-preview", Static).render())
            assert "RadioReference preview: stale" in rendered
            assert "Favorites editor state changed" in rendered
            assert app.query_one("#external-refresh", Button).disabled

    asyncio.run(exercise())


def test_tui_assisted_decisions_are_explicit_unexecuted_and_clearable(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        session, storage, source, _factory, controller = _controller(
            tmp_path,
            (_observation(),),
        )
        baseline = storage.value
        app = FavoritesEditorApp(session, controller)

        async with app.run_test(size=(170, 70)) as pilot:
            assert app.query_one("#external-ignore", Button).disabled
            app.action_external_refresh()
            for _attempt in range(30):
                await asyncio.sleep(0.01)
                await pilot.pause()
                if source.calls == 1 and not app.query_one("#external-ignore", Button).disabled:
                    break

            app.query_one("#external-record-index", Input).value = "1"
            app.action_external_ignore()
            await pilot.pause()

            plan = str(app.query_one("#external-plan", Static).render())
            status = str(app.query_one("#status", Static).render())
            assert "Assisted synchronization plan: UNEXECUTED" in plan
            assert "Decisions: 1" in plan
            assert "Unresolved supported decisions: 0" in plan
            assert "Favorites bytes changed: no" in plan
            assert "Provenance changed: no" in plan
            assert "cannot execute in Milestone 28.2" in status
            assert not session.has_changes
            assert storage.value is baseline
            assert not (tmp_path / "provenance.json").exists()

            app.action_external_clear()
            await pilot.pause()

            cleared = str(app.query_one("#external-plan", Static).render())
            assert "Decisions: 0" in cleared
            assert "Unresolved supported decisions: 1" in cleared
            assert "incomplete_decisions" in cleared
            assert storage.value is baseline

    asyncio.run(exercise())


def test_tui_import_requires_exact_preparation_before_adoption(tmp_path: Path) -> None:
    async def exercise() -> None:
        session, storage, source, _factory, controller = _controller(
            tmp_path,
            (_import_observation(),),
        )
        baseline = storage.value
        app = FavoritesEditorApp(session, controller)

        async with app.run_test(size=(180, 80)) as pilot:
            app.action_external_refresh()
            for _attempt in range(30):
                await asyncio.sleep(0.01)
                await pilot.pause()
                if (
                    source.calls == 1
                    and not app.query_one("#external-import-prepare", Button).disabled
                ):
                    break

            app.selected_path = FavoritesNavigationPath((2, 2, 4, 5))
            app.query_one("#external-record-index", Input).value = "1"
            app.action_external_import_prepare()
            await pilot.pause()

            prepared = str(app.query_one("#external-import-preview", Static).render())
            plan_before = str(app.query_one("#external-plan", Static).render())
            assert "Prepared import: NOT ADOPTED" in prepared
            assert "Provider record: frequency-100" in prepared
            assert "Insertion anchor: f_000001.hpd:5" in prepared
            assert "Derived target: f_000001.hpd:6" in prepared
            assert "Template command: C-Freq" in prepared
            assert "name@2=external" in prepared
            assert "frequency@4=external" in prepared
            assert "Resulting raw record: b'C-Freq" in prepared
            assert "Decisions: 0" in plan_before
            assert not app.query_one("#external-import-adopt", Button).disabled
            assert storage.value is baseline
            assert not (tmp_path / "provenance.json").exists()

            app.action_external_import_adopt()
            await pilot.pause()

            adopted = str(app.query_one("#external-plan", Static).render())
            preparation = str(app.query_one("#external-import-preview", Static).render())
            assert "Decisions: 1" in adopted
            assert "Unresolved supported decisions: 0" in adopted
            assert "Favorites bytes changed: yes" in adopted
            assert "Prepared import: none" in preparation
            assert storage.value is baseline
            assert not session.has_changes
            assert not (tmp_path / "provenance.json").exists()
            assert source.calls == 1

    asyncio.run(exercise())


def test_tui_assisted_review_and_execution_are_separate_and_single_use(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        session, controller, plan, storage, factory = _controller_plan(tmp_path)
        app = FavoritesEditorApp(session, controller)

        async with app.run_test(size=(180, 80)) as pilot:
            app._external_decisions = plan.decisions
            app._external_plan = plan
            app._refresh_external_preview()

            app.action_external_review()
            await pilot.pause()
            status = str(app.query_one("#status", Static).render())
            assert "Exact assisted plan reviewed" in status
            assert f"Target: {storage.requested_path}" in status
            assert "Confirmation token:" in status
            token = controller.review_external_execution(plan).confirmation_token
            assert app.query_one("#external-confirmation", Input).value == ""

            app.query_one("#external-confirmation", Input).value = token
            app.action_external_execute()
            for _attempt in range(100):
                await asyncio.sleep(0.01)
                await pilot.pause()
                if app._external_execution_thread is None:
                    break

            status = str(app.query_one("#status", Static).render())
            assisted_plan = str(app.query_one("#external-plan", Static).render())
            assert "Assisted execution: completed" in status
            assert "Fresh Favorites and provenance readback" in status
            assert "Assisted synchronization plan: unavailable" in assisted_plan
            assert session.baseline_snapshot == storage.value
            assert controller.planning_context() is None
            assert factory.source.calls == 1

    asyncio.run(exercise())


def test_tui_failed_assisted_confirmation_consumes_and_clears_plan(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        _session, controller, plan, storage, factory = _controller_plan(tmp_path)
        app = FavoritesEditorApp(_session, controller)

        async with app.run_test(size=(180, 80)) as pilot:
            app._external_decisions = plan.decisions
            app._external_plan = plan
            app._refresh_external_preview()
            app.query_one("#external-confirmation", Input).value = "0" * 64
            app.action_external_execute()
            for _attempt in range(100):
                await asyncio.sleep(0.01)
                await pilot.pause()
                if app._external_execution_thread is None:
                    break

            status = str(app.query_one("#status", Static).render())
            assisted_plan = str(app.query_one("#external-plan", Static).render())
            assert "Assisted execution rejected or failed" in status
            assert "Recovery: not_required" in status
            assert "Assisted synchronization plan: unavailable" in assisted_plan
            assert controller.planning_context() is None
            assert storage.execution_plans == []
            assert factory.source.calls == 1

    asyncio.run(exercise())
