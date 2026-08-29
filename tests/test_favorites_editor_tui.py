from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Button, Input, Static, Tree

from sds200 import (
    FavoritesEditorSession,
    FavoritesEditorSourceKind,
    FavoritesEditorWriteResult,
    FavoritesNavigationPath,
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    FavoritesWritePlan,
)
from sds200.favorites_editor_tui import FavoritesEditorApp
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
