from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import ConfigurationPaths, cli, favorites_editor, favorites_editor_tui


def _paths(tmp_path: Path) -> ConfigurationPaths:
    return ConfigurationPaths(
        system_config_dir=Path("/etc/sdsctl"),
        user_config_dir=tmp_path / "config",
        user_state_dir=tmp_path / "state",
        user_cache_dir=tmp_path / "cache",
        legacy_user_config_dir=tmp_path / "legacy",
    )


def test_favorites_edit_parser_requires_exactly_one_source(tmp_path: Path) -> None:
    parser = cli.build_parser()

    copied = parser.parse_args(
        ["favorites", "edit", "--copied-tree", str(tmp_path)]
    )
    usb = parser.parse_args(["favorites", "edit", "--usb", str(tmp_path)])

    assert copied.action == "favorites"
    assert copied.favorites_action == "edit"
    assert copied.copied_tree == tmp_path
    assert copied.usb is None
    assert usb.usb == tmp_path
    assert usb.copied_tree is None

    with pytest.raises(SystemExit):
        parser.parse_args(["favorites", "edit"])
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "favorites",
                "edit",
                "--copied-tree",
                str(tmp_path),
                "--usb",
                str(tmp_path),
            ]
        )


def test_copied_tree_editor_dispatch_never_selects_scanner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    session = object()

    def open_editor(path: Path) -> object:
        observed["path"] = path
        return session

    def run_editor(value: object) -> None:
        observed["session"] = value

    def reject_scanner(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Favorites editor must not select scanner hardware.")

    monkeypatch.setattr(favorites_editor, "open_favorites_copied_tree_editor", open_editor)
    monkeypatch.setattr(favorites_editor_tui, "run_favorites_editor", run_editor)
    monkeypatch.setattr(cli, "selected_radio", reject_scanner)

    assert cli.main(["favorites", "edit", "--copied-tree", str(tmp_path)]) == 0
    assert observed == {"path": tmp_path.resolve(), "session": session}


def test_copied_tree_dispatch_preserves_symlink_for_source_safety_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "favorites-link"
    link.symlink_to(target, target_is_directory=True)
    observed: list[Path] = []

    def open_editor(path: Path) -> object:
        observed.append(path)
        return object()

    monkeypatch.setattr(
        favorites_editor,
        "open_favorites_copied_tree_editor",
        open_editor,
    )
    monkeypatch.setattr(favorites_editor_tui, "run_favorites_editor", lambda value: None)

    assert cli.main(["favorites", "edit", "--copied-tree", str(link)]) == 0
    assert observed == [link.absolute()]
    assert observed[0].is_symlink()


def test_usb_editor_uses_private_xdg_host_state_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    session = object()

    def open_editor(path: Path, *, host_state_directory: Path) -> object:
        observed["path"] = path
        observed["host_state"] = host_state_directory
        return session

    monkeypatch.setattr(favorites_editor, "open_favorites_usb_editor", open_editor)
    monkeypatch.setattr(favorites_editor_tui, "run_favorites_editor", lambda value: None)

    assert (
        cli.main(
            ["favorites", "edit", "--usb", str(tmp_path)],
            configuration_paths=_paths(tmp_path),
        )
        == 0
    )
    assert observed == {
        "path": tmp_path.resolve(),
        "host_state": tmp_path / "state" / "favorites-usb-writes",
    }


def test_copied_tree_rejects_usb_host_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "favorites",
            "edit",
            "--copied-tree",
            str(tmp_path),
            "--usb-host-state",
            str(tmp_path / "state"),
        ]
    )

    assert result == 2
    assert "--usb-host-state is only valid with --usb" in capsys.readouterr().err
