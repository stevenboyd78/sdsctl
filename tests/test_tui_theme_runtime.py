from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sds200 import cli, resolve_configuration_paths
from sds200.rich_cli import palette_for_name
from sds200.state import RadioStateSnapshot
from sds200.theme import DEFAULT_DARK_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_theme_runtime import build_tui_theme_runtime

FIXTURE = Path(__file__).parent / "fixtures" / "replay" / "sds100-tui.jsonl"


def _write_tui_theme(
    root: Path,
    *,
    identifier: str = "solarized",
    order: int = 5,
    screen_class: str | None = "solarized-screen",
    stylesheet: str | None = None,
) -> Path:
    package = root / "tui" / identifier
    package.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "interface": "tui",
        "id": identifier,
        "label": identifier.title(),
        "order": order,
        "palette": "palette.json",
        "palette_name": identifier,
        "stylesheet": "theme.tcss",
        "screen_class": screen_class,
    }
    palette = DEFAULT_DARK_THEME.as_dict()
    palette["schema_version"] = 1
    palette["name"] = identifier
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package / "palette.json").write_text(json.dumps(palette), encoding="utf-8")
    (package / "theme.tcss").write_text(
        stylesheet
        or """Screen.solarized-screen {
    background: #002b36;
    color: #93a1a1;
}

Screen.solarized-screen .panel {
    background: #073642;
    border: round #268bd2;
}
""",
        encoding="utf-8",
    )
    return package


def test_runtime_defaults_to_exact_built_in_registry() -> None:
    runtime = build_tui_theme_runtime()

    assert runtime.registry.identifiers == ("dark", "light")
    assert runtime.managed_identifiers == ()
    assert runtime.ignored_managed_entries == 0
    assert runtime.require_asset("dark").manifest.palette is DEFAULT_DARK_THEME


def test_runtime_rejects_implicit_or_relative_managed_roots() -> None:
    with pytest.raises(TypeError, match="pathlib.Path"):
        build_tui_theme_runtime("/tmp/themes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be absolute"):
        build_tui_theme_runtime(Path("themes"))


def test_absent_root_is_an_ordinary_built_in_startup(tmp_path: Path) -> None:
    root = tmp_path / "missing"

    runtime = build_tui_theme_runtime(root)

    assert runtime.registry.identifiers == ("dark", "light")
    assert runtime.managed_identifiers == ()
    assert not root.exists()


def test_runtime_merges_managed_theme_and_retains_immutable_startup_data(
    tmp_path: Path,
) -> None:
    package = _write_tui_theme(tmp_path)

    runtime = build_tui_theme_runtime(tmp_path)
    asset = runtime.require_asset(" SOLARIZED ")
    original_stylesheet = asset.stylesheet
    original_foreground = asset.manifest.palette.resolve("text.primary").foreground

    assert runtime.registry.identifiers == ("dark", "solarized", "light")
    assert runtime.managed_identifiers == ("solarized",)
    assert asset.origin == "managed"
    assert "Screen.solarized-screen" in asset.stylesheet
    assert palette_for_name("solarized", registry=runtime.registry) is asset.manifest.palette

    for path in package.iterdir():
        path.unlink()
    package.rmdir()

    assert asset.stylesheet == original_stylesheet
    assert asset.manifest.palette.resolve("text.primary").foreground == original_foreground


@pytest.mark.parametrize(
    ("screen_class", "stylesheet"),
    [
        (None, "Screen { color: #ffffff; }\n"),
        ("solarized-screen", "Screen { color: #ffffff; }\n"),
        (
            "solarized-screen",
            "Screen.solarized-screen .panel { display: none; }\n",
        ),
        (
            "solarized-screen",
            "Screen.solarized-screen { color: $accent; }\n",
        ),
        (
            "solarized-screen",
            "Screen.solarized-screen { background: linear-gradient(red, blue); }\n",
        ),
    ],
)
def test_runtime_isolates_unsafe_managed_stylesheet(
    tmp_path: Path,
    screen_class: str | None,
    stylesheet: str,
) -> None:
    _write_tui_theme(
        tmp_path,
        screen_class=screen_class,
        stylesheet=stylesheet,
    )

    runtime = build_tui_theme_runtime(tmp_path)

    assert runtime.registry.identifiers == ("dark", "light")
    assert runtime.managed_identifiers == ()
    assert runtime.ignored_managed_entries == 1


def test_runtime_isolates_malformed_entries_without_hiding_valid_theme(
    tmp_path: Path,
) -> None:
    _write_tui_theme(tmp_path)
    broken = tmp_path / "tui" / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{}", encoding="utf-8")

    runtime = build_tui_theme_runtime(tmp_path)

    assert runtime.managed_identifiers == ("solarized",)
    assert runtime.ignored_managed_entries == 1


def test_textual_applies_managed_class_and_t_falls_back_to_dark(
    tmp_path: Path,
) -> None:
    _write_tui_theme(tmp_path)
    asset = build_tui_theme_runtime(tmp_path).require_asset("solarized")
    app = ScannerTuiApp(
        ScannerIdentity("replay://theme", "SDS100", "Version 1"),
        RadioStateSnapshot(),
        palette=asset.manifest.palette,
        screen_class=asset.manifest.screen_class,
        managed_stylesheet=asset.stylesheet,
    )

    async def exercise() -> None:
        async with app.run_test(size=(80, 32)) as pilot:
            assert app.palette is asset.manifest.palette
            assert app.screen.has_class("solarized-screen")
            await pilot.press("t")
            await pilot.pause()
            assert app.palette is DEFAULT_DARK_THEME
            assert not app.screen.has_class("solarized-screen")

    asyncio.run(exercise())


@pytest.mark.parametrize("selection_source", ["command-line", "environment", "config"])
def test_tui_cli_selects_managed_theme_from_resolved_xdg_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection_source: str,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc",
    )
    _write_tui_theme(paths.theme_dir)
    theme_arguments: list[str] = []
    environ: dict[str, str] = {}
    if selection_source == "command-line":
        theme_arguments = ["--theme", "solarized"]
    elif selection_source == "environment":
        environ["SDSCTL_THEME"] = "solarized"
    else:
        paths.user_config_file.write_text(
            'version = 1\n\n[application]\ntheme = "solarized"\n',
            encoding="utf-8",
        )
    captured: dict[str, object] = {}
    monkeypatch.setattr("sds200.tui.run_tui", lambda **kwargs: captured.update(kwargs))

    result = cli.main(
        ["--replay", str(FIXTURE), *theme_arguments, "tui"],
        configuration_paths=paths,
        environ=environ,
    )

    assert result == 0
    assert captured["palette"] is not DEFAULT_DARK_THEME
    assert captured["screen_class"] == "solarized-screen"
    assert "Screen.solarized-screen" in str(captured["managed_stylesheet"])


def test_rich_scanner_info_uses_managed_palette_without_changing_plain_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc",
    )
    _write_tui_theme(paths.theme_dir)
    scanner_info_fixture = (
        Path(__file__).parent / "fixtures" / "replay" / "sds100-scanner-info.jsonl"
    )
    result = cli.main(
        [
            "--replay",
            str(scanner_info_fixture),
            "--theme",
            "solarized",
            "--no-color",
            "scanner-info",
        ],
        configuration_paths=paths,
        environ={},
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.err == ""
    assert "Mode:" in output.out
    assert "System:" in output.out
    assert "\x1b[" not in output.out


@pytest.mark.parametrize("action", ["tui", "scanner-info"])
def test_unknown_terminal_selection_fails_before_scanner_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc",
    )

    def forbidden_scanner(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("scanner selection must not run")

    monkeypatch.setattr(cli, "selected_radio", forbidden_scanner)

    result = cli.main(
        ["--replay", str(FIXTURE), "--theme", "missing", action],
        configuration_paths=paths,
        environ={},
    )

    assert result == 2
    assert "available themes: dark, light" in capsys.readouterr().err


def test_nonrendering_command_does_not_resolve_unavailable_theme(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc",
    )
    paths.theme_dir.parent.mkdir(parents=True)
    paths.theme_dir.write_text("not a directory", encoding="utf-8")

    result = cli.main(
        ["--theme", "missing", "completion", "bash"],
        configuration_paths=paths,
        environ={},
    )

    assert result == 0
    assert "argcomplete" in capsys.readouterr().out
