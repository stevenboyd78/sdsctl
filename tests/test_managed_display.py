from __future__ import annotations

import fcntl
import os
import struct
import termios
from pathlib import Path
from types import SimpleNamespace

import pytest

import sds200.cli as cli
from sds200.daemon_remote_client import (
    DaemonRemoteClientConfiguration,
    DaemonRemoteClientError,
    DaemonRemoteClientErrorReason,
)
from sds200.daemon_remote_service import DaemonRemoteService
from sds200.exceptions import (
    ConfigurationError,
    DaemonDisconnectedError,
    DaemonProtocolError,
)
from sds200.managed_display import (
    MANAGED_DISPLAY_CONFIGURATION_EXIT,
    MANAGED_DISPLAY_TEMPORARY_EXIT,
    ManagedDisplayConfigurationError,
    ManagedDisplayTerminal,
    inspect_managed_display_terminal,
    managed_display_failure_status,
    managed_display_layout,
    require_observe_only_display,
)


def _configuration(tmp_path: Path) -> DaemonRemoteClientConfiguration:
    return DaemonRemoteClientConfiguration(
        address="192.168.20.41",
        port=50443,
        server_hostname="scanner.private.example",
        certificate_file=tmp_path / "private-ca.pem",
        client_id="pi-display-private",
        credential_file=tmp_path / "private-client.secret",
    )


def test_managed_display_inspects_exact_character_terminal() -> None:
    master, slave = os.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        terminal = inspect_managed_display_terminal(Path(os.ttyname(slave)))
    finally:
        os.close(slave)
        os.close(master)

    assert terminal == ManagedDisplayTerminal(
        columns=100,
        rows=30,
        layout="compact-split",
    )


def test_managed_display_rejects_non_terminal_and_final_symlink(
    tmp_path: Path,
) -> None:
    regular = tmp_path / "private-terminal-name"
    regular.write_text("not a terminal", encoding="utf-8")
    linked = tmp_path / "private-terminal-link"
    linked.symlink_to("/dev/null")

    for path in (regular, linked, Path("relative-terminal")):
        with pytest.raises(ManagedDisplayConfigurationError) as caught:
            inspect_managed_display_terminal(path)
        rendered = f"{caught.value!r} {caught.value}"
        assert "private-terminal" not in rendered
        assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    ("columns", "rows", "expected"),
    [
        (64, 20, "compact"),
        (90, 28, "short"),
        (100, 30, "compact-split"),
        (119, 31, "compact-split"),
        (100, 32, "standard"),
        (120, 30, "wide"),
    ],
)
def test_managed_display_layout_matches_tui_breakpoints(
    columns: int,
    rows: int,
    expected: str,
) -> None:
    assert managed_display_layout(columns, rows) == expected


@pytest.mark.parametrize("value", (0, -1, True, 3.5, "100"))
def test_managed_display_layout_rejects_invalid_geometry(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        managed_display_layout(value, 30)  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        managed_display_layout(100, value)  # type: ignore[arg-type]


def test_managed_display_requires_verified_observe_only_identity() -> None:
    require_observe_only_display({"control_operations": []})

    for hello in (
        None,
        {},
        {"control_operations": "scanner.next"},
        {"control_operations": [4]},
        {"control_operations": ["scanner.next"]},
    ):
        with pytest.raises(ManagedDisplayConfigurationError):
            require_observe_only_display(hello)


def test_managed_display_failure_status_separates_retryable_failures() -> None:
    assert (
        managed_display_failure_status(
            DaemonRemoteClientError(DaemonRemoteClientErrorReason.CONNECT_FAILED)
        )
        == MANAGED_DISPLAY_TEMPORARY_EXIT
    )
    assert (
        managed_display_failure_status(DaemonDisconnectedError("closed"))
        == MANAGED_DISPLAY_TEMPORARY_EXIT
    )
    for reason in (
        DaemonRemoteClientErrorReason.CONFIGURATION_FAILED,
        DaemonRemoteClientErrorReason.TLS_HANDSHAKE_FAILED,
        DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED,
        DaemonRemoteClientErrorReason.SERVICE_NEGOTIATION_FAILED,
    ):
        assert (
            managed_display_failure_status(DaemonRemoteClientError(reason))
            == MANAGED_DISPLAY_CONFIGURATION_EXIT
        )
    assert (
        managed_display_failure_status(ConfigurationError("private detail"))
        == MANAGED_DISPLAY_CONFIGURATION_EXIT
    )
    assert (
        managed_display_failure_status(DaemonProtocolError("private detail"))
        == MANAGED_DISPLAY_CONFIGURATION_EXIT
    )
    assert managed_display_failure_status(OSError("unexpected")) == 2


@pytest.mark.parametrize(
    "arguments",
    (
        ["tui", "--managed-display"],
        ["tui", "--daemon-client", "--managed-display"],
    ),
)
def test_managed_display_requires_one_remote_daemon_profile(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(arguments, environ={}) == 2
    captured = capsys.readouterr()
    assert "stopped unexpectedly" in captured.out
    assert "ValueError" in captured.err


def test_display_preflight_checks_observe_services_and_redacts_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configuration = _configuration(tmp_path)
    observed_services: list[DaemonRemoteService] = []

    class FakeApiClient:
        def __init__(self, transport: object, **kwargs: object) -> None:
            del transport, kwargs

        def __enter__(self) -> FakeApiClient:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def hello(self) -> dict[str, object]:
            return {
                "operations": ["runtime.snapshot"],
                "control_operations": [],
            }

        def runtime_snapshot(self) -> dict[str, object]:
            return {"scanner_connected": False}

    monkeypatch.setattr(
        cli,
        "inspect_managed_display_terminal",
        lambda path: ManagedDisplayTerminal(100, 30, "compact-split"),
    )
    monkeypatch.setattr(
        cli,
        "_selected_remote_client_configuration",
        lambda *args, **kwargs: configuration,
    )
    monkeypatch.setattr(cli, "DaemonApiClient", FakeApiClient)
    monkeypatch.setattr(
        cli,
        "_probe_remote_display_service",
        lambda config, service, *, timeout: observed_services.append(service),
    )

    assert cli.main(
        [
            "display-client-preflight",
            "--remote-profile",
            "pi-display-private",
            "--terminal",
            "/dev/tty-private",
        ],
        environ={},
    ) == 0

    assert observed_services == [DaemonRemoteService.EVENTS, DaemonRemoteService.AUDIO]
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "Managed display preflight passed.\n"
        "Terminal geometry: 100 columns x 30 rows\n"
        "Responsive layout: compact-split\n"
        "Remote services: API, events, audio\n"
        "Authorization: observe only\n"
        "Audio playback: not requested\n"
    )
    for private in (
        "192.168.20.41",
        "scanner.private.example",
        "pi-display-private",
        "private-ca.pem",
        "private-client.secret",
        str(tmp_path),
        "/dev/tty-private",
    ):
        assert private not in captured.out


@pytest.mark.parametrize(
    ("error", "status", "category"),
    [
        (
            DaemonRemoteClientError(DaemonRemoteClientErrorReason.CONNECT_FAILED),
            MANAGED_DISPLAY_TEMPORARY_EXIT,
            "temporary",
        ),
        (RuntimeError("private runtime detail"), 2, "local"),
    ],
)
def test_display_preflight_reports_only_stable_failure_class(
    error: Exception,
    status: int,
    category: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "inspect_managed_display_terminal",
        lambda path: (_ for _ in ()).throw(error),
    )

    assert cli.main(
        ["display-client-preflight", "--remote-profile", "private-profile"],
        environ={},
    ) == status
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: managed display {category} preflight failed.\n"
    assert "private-profile" not in captured.err
    assert "private runtime detail" not in captured.err


def test_display_preflight_validates_one_selected_local_audio_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configuration = _configuration(tmp_path)

    class FakeApiClient:
        def __init__(self, transport: object, **kwargs: object) -> None:
            del transport, kwargs

        def __enter__(self) -> FakeApiClient:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def hello(self) -> dict[str, object]:
            return {
                "operations": ["runtime.snapshot"],
                "control_operations": [],
            }

        def runtime_snapshot(self) -> dict[str, object]:
            return {"scanner_connected": True}

    monkeypatch.setattr(
        cli,
        "inspect_managed_display_terminal",
        lambda path: ManagedDisplayTerminal(100, 30, "compact-split"),
    )
    monkeypatch.setattr(
        cli,
        "_selected_remote_client_configuration",
        lambda *args, **kwargs: configuration,
    )
    monkeypatch.setattr(cli, "DaemonApiClient", FakeApiClient)
    monkeypatch.setattr(
        cli,
        "_probe_remote_display_service",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        cli,
        "inspect_audio_backend",
        lambda: SimpleNamespace(
            output_devices=(SimpleNamespace(index=3, name="Private output"),)
        ),
    )

    arguments = [
        "display-client-preflight",
        "--remote-profile",
        "private-profile",
        "--audio-playback",
        "--audio-device",
        "3",
    ]
    assert cli.main(arguments, environ={}) == 0
    captured = capsys.readouterr()
    assert "Audio playback: available" in captured.out
    assert "Private output" not in captured.out

    arguments[-1] = "4"
    assert cli.main(arguments, environ={}) == 2
    captured = capsys.readouterr()
    assert captured.err == "error: managed display local preflight failed.\n"
    assert "Private output" not in captured.err


@pytest.mark.parametrize(
    ("reason", "status", "message"),
    [
        (
            DaemonRemoteClientErrorReason.CONNECT_FAILED,
            MANAGED_DISPLAY_TEMPORARY_EXIT,
            "temporarily unavailable",
        ),
        (
            DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED,
            MANAGED_DISPLAY_CONFIGURATION_EXIT,
            "configuration failed",
        ),
    ],
)
def test_managed_tui_returns_service_manager_status_without_private_detail(
    reason: DaemonRemoteClientErrorReason,
    status: int,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise DaemonRemoteClientError(reason)

    monkeypatch.setattr(cli, "_run_tui_with_logging", fail)

    assert cli.main(
        [
            "tui",
            "--daemon-client",
            "--remote-profile",
            "private-profile",
            "--managed-display",
        ],
        environ={},
    ) == status
    captured = capsys.readouterr()
    assert message in captured.out
    assert "private-profile" not in captured.out
    assert "private-profile" not in captured.err
    assert "DaemonRemoteClientError" in captured.err


def test_managed_tui_sanitizes_unexpected_local_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise RuntimeError("private runtime detail")

    monkeypatch.setattr(cli, "_run_tui_with_logging", fail)

    assert cli.main(
        [
            "tui",
            "--daemon-client",
            "--remote-profile",
            "private-profile",
            "--managed-display",
        ],
        environ={},
    ) == 2
    captured = capsys.readouterr()
    assert "stopped unexpectedly" in captured.out
    assert "RuntimeError" in captured.err
    assert "private runtime detail" not in captured.out
    assert "private runtime detail" not in captured.err
