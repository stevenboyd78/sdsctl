from pathlib import Path

from sds200 import cli
from sds200.managed_display import managed_display_service_template

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVICE = REPOSITORY_ROOT / "contrib" / "systemd" / "sdsctl-display@.service"


def test_managed_display_service_is_opt_in_observe_only_and_console_bound() -> None:
    unit = SERVICE.read_text(encoding="utf-8")

    assert (
        "ExecStart=/usr/bin/env -- /opt/sdsctl-display/bin/sdsctl tui" in unit
    )
    assert "--daemon-client --remote-profile %i --managed-display" in unit
    assert "TTYPath=/dev/tty1" in unit
    assert "Conflicts=getty@tty1.service" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "DevicePolicy=closed" in unit
    assert "DeviceAllow=/dev/tty1 rw" in unit
    assert "DeviceAllow=char-alsa rw" in unit
    assert "StateDirectory=sdsctl-display" in unit
    assert "UMask=0077" in unit
    assert "User=sdsctl-display" in unit


def test_source_and_packaged_managed_display_services_are_identical() -> None:
    assert SERVICE.read_text(encoding="utf-8") == managed_display_service_template()


def test_cli_prints_exact_packaged_managed_display_service(
    capsys,
) -> None:
    assert cli.main(["display-client-service"], environ={}) == 0
    captured = capsys.readouterr()
    assert captured.out == managed_display_service_template()
    assert captured.err == ""


def test_managed_display_service_retries_only_temporary_failure_class() -> None:
    unit = SERVICE.read_text(encoding="utf-8")

    assert "Restart=no" in unit
    assert "RestartSec=15s" in unit
    assert "RestartForceExitStatus=75" in unit
    assert "StartLimitIntervalSec=0" in unit
    assert "Restart=on-failure" not in unit
    assert "Restart=always" not in unit


def test_managed_display_service_contains_no_secret_or_listener_configuration() -> None:
    unit = SERVICE.read_text(encoding="utf-8")

    for forbidden in (
        "client.secret",
        "credential",
        "password",
        "--host",
        "--port",
        "--listen",
        "50443",
        "0.0.0.0",
    ):
        assert forbidden not in unit
