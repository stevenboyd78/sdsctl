from __future__ import annotations

from pathlib import Path

import pytest

import sds200.home_assistant_live_audio_service_runtime as runtime


def _key(tmp_path: Path) -> Path:
    path = tmp_path / "bridge.key"
    path.write_text("k" * 43 + "\n", encoding="ascii")
    path.chmod(0o600)
    return path


def test_bridge_key_loader_requires_private_regular_file(tmp_path: Path) -> None:
    path = _key(tmp_path)
    assert runtime.load_home_assistant_live_audio_bridge_secret(path) == "k" * 43

    path.chmod(0o640)
    with pytest.raises(ValueError, match="mode 0600"):
        runtime.load_home_assistant_live_audio_bridge_secret(path)

    target = tmp_path / "target"
    target.write_text("z" * 43, encoding="ascii")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        runtime.load_home_assistant_live_audio_bridge_secret(link)


def test_runtime_parser_requires_private_paths() -> None:
    parser = runtime.build_parser()
    args = parser.parse_args(
        [
            "--daemon-live-audio-socket",
            "/run/sdsctl/live-audio.sock",
            "--bridge-secret-file",
            "/data/live-audio-bridge.key",
        ]
    )
    assert args.listen_port == 8100


def test_runtime_builds_private_service_without_access_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _key(tmp_path)
    calls: list[tuple[object, dict[str, object]]] = []

    def run_server(app: object, **kwargs: object) -> int:
        calls.append((app, dict(kwargs)))
        return 17

    monkeypatch.setattr(runtime, "run_web_dashboard_server", run_server)

    result = runtime.main(
        [
            "--daemon-live-audio-socket",
            str(tmp_path / "live-audio.sock"),
            "--bridge-secret-file",
            str(key),
            "--listen-port",
            "8124",
        ]
    )

    assert result == 17
    assert len(calls) == 1
    assert calls[0][1] == {
        "host": "0.0.0.0",
        "port": 8124,
        "access_log": False,
        "container_exposure": True,
    }
    assert "k" * 43 not in repr(calls[0][0])
