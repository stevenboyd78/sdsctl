from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    ConfigurationPaths,
    cli,
    favorites_editor,
    favorites_editor_external_preview,
    favorites_editor_tui,
)


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


def test_radioreference_preview_parser_retains_only_non_secret_configuration(
    tmp_path: Path,
) -> None:
    args = cli.build_parser().parse_args(
        [
            "favorites",
            "edit",
            "--copied-tree",
            str(tmp_path),
            "--radioreference-preview",
            "--radioreference-username",
            "operator",
            "--radioreference-application-key-env",
            "RR_APP_KEY",
            "--radioreference-password-env",
            "RR_PASSWORD",
            "--radioreference-provenance",
            str(tmp_path / "private" / "provenance.json"),
            "--radioreference-dataset",
            "county-49-fire",
            "--radioreference-operation",
            "getCountyFreqsByTag",
            "--radioreference-parameter",
            "ctid=49",
            "--radioreference-parameter",
            "tag=4",
        ]
    )

    assert args.radioreference_preview is True
    assert args.radioreference_username == "operator"
    assert args.radioreference_application_key_env == "RR_APP_KEY"
    assert args.radioreference_password_env == "RR_PASSWORD"
    assert args.radioreference_parameter == [("ctid", 49), ("tag", 4)]


def test_radioreference_preview_dispatch_is_lazy_and_passes_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Session:
        storage = object()

    session = Session()

    def controller(value: object, factory: object) -> object:
        observed["controller_session"] = value
        observed["owner_factory"] = factory
        return "controller"

    def run_editor(value: object, external: object) -> None:
        observed["session"] = value
        observed["external"] = external

    monkeypatch.setattr(
        favorites_editor,
        "open_favorites_copied_tree_editor",
        lambda path: session,
    )
    monkeypatch.setattr(favorites_editor_tui, "run_favorites_editor", run_editor)
    monkeypatch.setattr(
        favorites_editor_external_preview,
        "FavoritesEditorExternalPreviewController",
        controller,
    )

    result = cli.main(
        [
            "favorites",
            "edit",
            "--copied-tree",
            str(tmp_path),
            "--radioreference-preview",
            "--radioreference-username",
            "operator",
            "--radioreference-application-key-env",
            "RR_APP_KEY",
            "--radioreference-password-env",
            "RR_PASSWORD",
            "--radioreference-provenance",
            str(tmp_path / "provenance.json"),
            "--radioreference-dataset",
            "subcategory-123",
            "--radioreference-operation",
            "getSubcatFreqs",
            "--radioreference-parameter",
            "scid=123",
        ],
        environ={},
    )

    assert result == 0
    assert observed["controller_session"] is session
    assert observed["session"] is session
    assert observed["external"] == "controller"
    owner_factory = observed["owner_factory"]
    assert isinstance(
        owner_factory,
        favorites_editor_external_preview.FavoritesEditorRadioReferenceRefreshOwnerFactory,
    )
    assert owner_factory.provenance_path == (tmp_path / "provenance.json").resolve()
    source_factory = owner_factory.source_factory
    assert source_factory.configuration.credential.username == "operator"
    assert source_factory.configuration.credential.password_environment_variable == (
        "RR_PASSWORD"
    )
    assert source_factory.request_plan.parameters == (("scid", 123),)


def test_radioreference_options_require_explicit_enablement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = cli.main(
        [
            "favorites",
            "edit",
            "--copied-tree",
            str(tmp_path),
            "--radioreference-username",
            "operator",
        ]
    )

    assert result == 2
    assert "require --radioreference-preview" in capsys.readouterr().err


def test_radioreference_preview_rejects_incomplete_or_misordered_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        favorites_editor,
        "open_favorites_copied_tree_editor",
        lambda path: type("Session", (), {"storage": object()})(),
    )
    common = [
        "favorites",
        "edit",
        "--copied-tree",
        str(tmp_path),
        "--radioreference-preview",
        "--radioreference-username",
        "operator",
        "--radioreference-application-key-env",
        "RR_APP_KEY",
        "--radioreference-password-env",
        "RR_PASSWORD",
        "--radioreference-provenance",
        str(tmp_path / "provenance.json"),
        "--radioreference-dataset",
        "county-49-fire",
    ]

    assert cli.main(common) == 2
    assert "--radioreference-operation" in capsys.readouterr().err

    result = cli.main(
        [
            *common,
            "--radioreference-operation",
            "getCountyFreqsByTag",
            "--radioreference-parameter",
            "tag=4",
            "--radioreference-parameter",
            "ctid=49",
        ]
    )
    assert result == 2
    assert "exactly match reviewed WSDL order" in capsys.readouterr().err
