from __future__ import annotations

import json
from pathlib import Path

from sds200.cli import main


def _display_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "display.toml"
    path.write_text(
        """\
version = 1
kind = "display-client"
[connection]
remote_profile = "display"
""",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_service_config_validate_bypasses_application_configuration(
    tmp_path: Path, capsys: object
) -> None:
    path = _display_manifest(tmp_path)

    assert main(["service-config", "validate", "--manifest", str(path)], environ={}) == 0

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "display-client service manifest is valid" in captured.out
    assert captured.err == ""


def test_service_config_show_sources_is_machine_readable(tmp_path: Path, capsys: object) -> None:
    path = _display_manifest(tmp_path)

    assert main(["service-config", "show-sources", "--manifest", str(path)]) == 0

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["kind"] == "display-client"
    assert payload["fields"]["connection.remote_profile"]["source"] == "manifest"


def test_service_config_set_overrides_toml_scalars_and_bare_strings(
    tmp_path: Path, capsys: object
) -> None:
    path = _display_manifest(tmp_path)

    assert (
        main(
            [
                "service-config",
                "show-sources",
                "--manifest",
                str(path),
                "--set",
                "display.startup_view=mimic",
                "--set",
                "audio.playback=true",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["fields"]["display.startup_view"] == {
        "location": "command line",
        "source": "command-line",
        "value": "mimic",
    }
    assert payload["fields"]["audio.playback"]["value"] is True


def test_service_config_set_rejects_malformed_duplicate_and_unknown_fields(
    tmp_path: Path, capsys: object
) -> None:
    path = _display_manifest(tmp_path)

    for values, expected in (
        (["broken"], "FIELD=VALUE"),
        (["display.theme=dark", "display.theme=light"], "Duplicate --set"),
        (["display.password=secret"], "does not support override"),
    ):
        arguments = ["service-config", "validate", "--manifest", str(path)]
        for value in values:
            arguments.extend(("--set", value))
        assert main(arguments) == 78
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert expected in captured.err


def test_service_config_show_command_does_not_execute(tmp_path: Path, capsys: object) -> None:
    path = _display_manifest(tmp_path)

    assert main(["service-config", "show-command", "--manifest", str(path)]) == 0

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert " tui --daemon-client --remote-profile display --managed-display " in captured.out


def test_service_config_invalid_permissions_return_configuration_status(
    tmp_path: Path, capsys: object
) -> None:
    path = _display_manifest(tmp_path)
    path.chmod(0o644)

    assert main(["service-config", "validate", "--manifest", str(path)]) == 78

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "exact mode 0600" in captured.err
