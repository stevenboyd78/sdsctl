from __future__ import annotations

import json
from pathlib import Path

import pytest

from sds200 import REMOTE_AUDIO_PROFILE_VERSION, RemoteAudioProfileStore
from sds200.cli import build_parser, main


def _write_legacy_profile(path: Path) -> str:
    document = (
        "version = 1\n\n"
        '[destinations."county-feed"]\n'
        'kind = "broadcastify"\n'
        'server = "private-feed.example.test"\n'
        'mount = "/private-mount"\n'
        'environment_variable = "PRIVATE_BROADCASTIFY_SECRET"\n'
    )
    path.write_text(document, encoding="utf-8")
    return document


def test_remote_audio_cli_lists_legacy_policy_without_endpoint_or_rewrite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "remote-audio-profiles.toml"
    original = _write_legacy_profile(profile_path)

    status = main(
        [
            "remote-audio",
            "--profiles-file",
            str(profile_path),
            "list",
            "--json",
        ],
        environ={"XDG_CONFIG_HOME": str(tmp_path)},
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "kind": "broadcastify",
            "name": "county-feed",
            "ordinary_http_cleartext_credentials_acknowledged": False,
        }
    ]
    rendered = json.dumps(payload)
    assert "private-feed.example.test" not in rendered
    assert "/private-mount" not in rendered
    assert "PRIVATE_BROADCASTIFY_SECRET" not in rendered
    assert profile_path.read_text(encoding="utf-8") == original


def test_remote_audio_cli_acknowledges_migrates_and_can_revoke(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "remote-audio-profiles.toml"
    _write_legacy_profile(profile_path)
    arguments = [
        "remote-audio",
        "--profiles-file",
        str(profile_path),
        "acknowledge-cleartext",
        "county-feed",
        "--acknowledge-cleartext-credentials",
    ]

    assert main(arguments, environ={"XDG_CONFIG_HOME": str(tmp_path)}) == 0
    acknowledgement_output = capsys.readouterr().out
    profile = RemoteAudioProfileStore(profile_path).get("county-feed")
    document = profile_path.read_text(encoding="utf-8")

    assert profile.acknowledge_cleartext_credentials is True
    assert document.startswith(f"version = {REMOTE_AUDIO_PROFILE_VERSION}\n")
    assert "acknowledge_cleartext_credentials = true" in document
    assert "private-feed.example.test" not in acknowledgement_output
    assert "/private-mount" not in acknowledgement_output
    assert "PRIVATE_BROADCASTIFY_SECRET" not in acknowledgement_output

    assert (
        main(
            [
                "remote-audio",
                "--profiles-file",
                str(profile_path),
                "revoke-cleartext",
                "county-feed",
            ],
            environ={"XDG_CONFIG_HOME": str(tmp_path)},
        )
        == 0
    )
    revoke_output = capsys.readouterr().out
    profile = RemoteAudioProfileStore(profile_path).get("county-feed")

    assert profile.acknowledge_cleartext_credentials is False
    assert "private-feed.example.test" not in revoke_output
    assert "/private-mount" not in revoke_output
    assert "PRIVATE_BROADCASTIFY_SECRET" not in revoke_output


def test_remote_audio_cli_requires_explicit_acknowledgement_flag(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(
            [
                "remote-audio",
                "--profiles-file",
                str(tmp_path / "profiles.toml"),
                "acknowledge-cleartext",
                "county-feed",
            ]
        )

    assert raised.value.code == 2
