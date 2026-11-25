from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from sds200.exceptions import ConfigurationError
from sds200.service_configuration import (
    SERVICE_CONFIGURATION_MAX_BYTES,
    BrowserKioskServiceConfiguration,
    DisplayClientServiceConfiguration,
    configured_service_template,
    load_service_configuration,
    render_service_configuration_inventory,
    service_configuration_template,
    service_launch_plan,
    write_service_configuration_template,
)


def _manifest(tmp_path: Path, text: str, *, mode: int = 0o600) -> Path:
    path = tmp_path / "service.toml"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_display_manifest_defaults_and_exact_launch_plan(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        """\
version = 1
kind = "display-client"

[connection]
remote_profile = "pi4-display"
""",
    )

    resolved = load_service_configuration(path)

    assert resolved.kind == "display-client"
    assert resolved.manifest == path
    configuration = resolved.configuration
    assert isinstance(configuration, DisplayClientServiceConfiguration)
    assert configuration.remote_profile == "pi4-display"
    assert configuration.startup_view == "dashboard"
    assert configuration.recording_metadata is True
    assert configuration.audio_playback is False
    assert resolved.origins["connection.remote_profile"].source == "manifest"
    assert resolved.origins["display.startup_view"].source == "default"

    plan = service_launch_plan(
        resolved,
        executable="/opt/sdsctl-display/bin/sdsctl",
        environ={"LANG": "C.UTF-8"},
    )
    assert plan.argv == (
        "/opt/sdsctl-display/bin/sdsctl",
        "--color",
        "auto",
        "--theme",
        "dark",
        "tui",
        "--daemon-client",
        "--remote-profile",
        "pi4-display",
        "--managed-display",
        "--startup-view",
        "dashboard",
        "--daemon-timeout",
        "5",
        "--audio-directory",
        "/var/lib/sdsctl-display/recordings",
        "--audio-buffer-ms",
        "250",
        "--audio-history-limit",
        "100",
        "--audio-metadata",
    )
    assert plan.environ == {
        "LANG": "C.UTF-8",
        "XDG_CONFIG_HOME": "/var/lib/sdsctl-display/.config",
        "XDG_STATE_HOME": "/var/lib/sdsctl-display/.local/state",
        "XDG_CACHE_HOME": "/var/cache/sdsctl-display",
    }


def test_display_manifest_all_durable_options_and_preflight(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        """\
version = 1
kind = "display-client"

[paths]
config_home = "/srv/display/config"
state_home = "/srv/display/state"
cache_home = "/srv/display/cache"

[connection]
remote_profile = "pi4-display"
timeout = 7.5

[display]
terminal = "/dev/tty3"
startup_view = "mimic"
theme = "first-responder"
color = "always"

[recording]
directory = "/srv/display/recordings"
metadata = false

[audio]
playback = true
device = 3
buffer_ms = 400
history_limit = 25

[logging]
level = "debug"
file = "/srv/display/log/sdsctl.log"
""",
    )
    resolved = load_service_configuration(path)
    configuration = resolved.configuration
    assert isinstance(configuration, DisplayClientServiceConfiguration)
    assert configuration.startup_view == "mimic"
    assert configuration.theme == "first-responder"
    assert configuration.log_level == "DEBUG"
    assert configuration.audio_device == 3

    launch = service_launch_plan(resolved, executable="sdsctl", environ={})
    assert launch.argv[:9] == (
        "sdsctl",
        "--log-level",
        "DEBUG",
        "--log-file",
        "/srv/display/log/sdsctl.log",
        "--color",
        "always",
        "--theme",
        "first-responder",
    )
    assert "--audio-metadata" not in launch.argv
    assert launch.argv[-3:] == ("--audio-playback", "--audio-device", "3")

    preflight = service_launch_plan(resolved, executable="sdsctl", environ={}, preflight=True)
    assert preflight.argv == (
        "sdsctl",
        "display-client-preflight",
        "--remote-profile",
        "pi4-display",
        "--terminal",
        "/dev/tty3",
        "--timeout",
        "7.5",
        "--audio-playback",
        "--audio-device",
        "3",
    )


def test_strict_command_line_overrides_revalidate_and_report_provenance(
    tmp_path: Path,
) -> None:
    path = _manifest(
        tmp_path,
        """\
version = 1
kind = "display-client"
[connection]
remote_profile = "display"
""",
    )

    resolved = load_service_configuration(
        path,
        overrides={"display.startup_view": "mimic", "audio.playback": True},
    )
    configuration = resolved.configuration
    assert isinstance(configuration, DisplayClientServiceConfiguration)
    assert configuration.startup_view == "mimic"
    assert configuration.audio_playback is True
    assert resolved.origins["display.startup_view"].source == "command-line"
    assert resolved.origins["display.startup_view"].location == "command line"
    plan = service_launch_plan(resolved, executable="sdsctl", environ={})
    assert "--startup-view" in plan.argv
    assert plan.argv[plan.argv.index("--startup-view") + 1] == "mimic"
    assert "--audio-playback" in plan.argv


def test_command_line_overrides_reject_unknown_and_unsafe_values(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        """\
version = 1
kind = "browser-kiosk"
[server]
origin = "https://scanner.example"
ca_file = "/etc/sdsctl/server.crt"
[kiosk]
browser = "/usr/bin/chromium"
profile_directory = "/home/display/kiosk"
""",
    )

    with pytest.raises(ConfigurationError, match="does not support override"):
        load_service_configuration(path, overrides={"kiosk.device_key": "secret"})
    with pytest.raises(ConfigurationError, match="must be an absolute path"):
        load_service_configuration(path, overrides={"server.ca_file": "relative.crt"})


def test_browser_manifest_and_role_bound_launch_plan(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        """\
version = 1
kind = "browser-kiosk"

[server]
origin = "https://scanner.example:8443"
ca_file = "/etc/sdsctl/server.crt"

[kiosk]
browser = "/usr/bin/chromium"
role = "operator"
profile_directory = "/home/display/.sdsctl-operator-kiosk"
""",
    )
    resolved = load_service_configuration(path)
    configuration = resolved.configuration
    assert isinstance(configuration, BrowserKioskServiceConfiguration)
    assert configuration.role == "operator"

    launch = service_launch_plan(resolved, executable="sdsctl", environ={"DISPLAY": ":0"})
    assert launch.argv == (
        "sdsctl",
        "browser-kiosk",
        "--origin",
        "https://scanner.example:8443",
        "--ca-file",
        "/etc/sdsctl/server.crt",
        "--role",
        "operator",
        "--browser",
        "/usr/bin/chromium",
        "--profile-directory",
        "/home/display/.sdsctl-operator-kiosk",
    )
    assert launch.environ == {"DISPLAY": ":0"}

    preflight = service_launch_plan(
        resolved, executable="sdsctl", environ={"DISPLAY": ":0"}, preflight=True
    )
    assert preflight.argv[-1] == "--preflight-only"
    assert "--browser" not in preflight.argv
    assert "--profile-directory" not in preflight.argv


@pytest.mark.parametrize(
    "text, expected",
    (
        (
            """\
version = 2
kind = "display-client"
[connection]
remote_profile = "display"
""",
            "version must be 1",
        ),
        (
            """\
version = 1
kind = "display-client"
surprise = true
[connection]
remote_profile = "display"
""",
            "unsupported field",
        ),
        (
            """\
version = 1
kind = "display-client"
[connection]
remote_profile = "display"
password = "do-not-accept"
""",
            "unsupported field",
        ),
        (
            """\
version = 1
kind = "browser-kiosk"
[server]
origin = "https://scanner.example"
ca_file = "relative.crt"
[kiosk]
browser = "/usr/bin/chromium"
profile_directory = "/home/display/kiosk"
""",
            "server.ca_file must be an absolute path",
        ),
        (
            """\
version = 1
kind = "browser-kiosk"
[server]
origin = "https://scanner.example"
ca_file = "/etc/sdsctl/server.crt"
[kiosk]
browser = "/usr/bin/chromium"
profile_directory = "/home/display/kiosk"
device_key = "must-not-be-silently-ignored"
""",
            "unsupported field",
        ),
    ),
)
def test_manifest_rejects_unsupported_or_unsafe_values(
    tmp_path: Path, text: str, expected: str
) -> None:
    path = _manifest(tmp_path, text)

    with pytest.raises(ConfigurationError, match=expected):
        load_service_configuration(path)


def test_manifest_requires_absolute_private_regular_file(tmp_path: Path) -> None:
    text = """\
version = 1
kind = "display-client"
[connection]
remote_profile = "display"
"""
    public = _manifest(tmp_path, text, mode=0o640)
    with pytest.raises(ConfigurationError, match="exact mode 0600"):
        load_service_configuration(public)

    public.chmod(0o600)
    link = tmp_path / "linked.toml"
    link.symlink_to(public)
    with pytest.raises(ConfigurationError, match="must not contain symlinks"):
        load_service_configuration(link)

    with pytest.raises(ConfigurationError, match="must be absolute"):
        load_service_configuration(Path("relative.toml"))


def test_manifest_reader_rejects_oversize_non_utf8_and_non_regular_inputs(
    tmp_path: Path,
) -> None:
    oversize = tmp_path / "oversize.toml"
    oversize.write_bytes(b"x" * (SERVICE_CONFIGURATION_MAX_BYTES + 1))
    oversize.chmod(0o600)
    with pytest.raises(ConfigurationError, match="64 KiB size limit"):
        load_service_configuration(oversize)

    invalid_utf8 = tmp_path / "invalid-utf8.toml"
    invalid_utf8.write_bytes(b"\xff\xfe")
    invalid_utf8.chmod(0o600)
    with pytest.raises(ConfigurationError, match="valid UTF-8"):
        load_service_configuration(invalid_utf8)

    directory = tmp_path / "manifest-directory"
    directory.mkdir(mode=0o700)
    with pytest.raises(ConfigurationError, match="regular file"):
        load_service_configuration(directory)


def test_inventory_is_deterministic_and_contains_no_credential_value(
    tmp_path: Path,
) -> None:
    path = _manifest(
        tmp_path,
        """\
version = 1
kind = "browser-kiosk"
[server]
origin = "https://scanner.example"
ca_file = "/etc/sdsctl/server.crt"
[kiosk]
browser = "/usr/bin/chromium"
profile_directory = "/home/display/kiosk"
""",
    )

    payload = json.loads(render_service_configuration_inventory(load_service_configuration(path)))

    assert payload["kind"] == "browser-kiosk"
    assert payload["credentials"] == "not embedded"
    assert payload["fields"]["kiosk.role"]["source"] == "default"
    assert payload["fields"]["server.origin"]["source"] == "manifest"
    assert "password" not in json.dumps(payload).lower()
    assert "device_key" not in json.dumps(payload).lower()


@pytest.mark.parametrize("kind", ("display-client", "browser-kiosk"))
def test_template_is_valid_private_atomic_and_never_overwrites(tmp_path: Path, kind: str) -> None:
    output = tmp_path / f"{kind}.toml"

    created = write_service_configuration_template(output, kind=kind)  # type: ignore[arg-type]

    assert created == output
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    loaded = load_service_configuration(output)
    assert loaded.kind == kind
    assert "credential" not in output.read_text(encoding="utf-8").lower()
    with pytest.raises(ConfigurationError, match="already exists"):
        write_service_configuration_template(output, kind=kind)  # type: ignore[arg-type]


def test_packaged_configured_units_are_thin_and_keep_existing_sandbox() -> None:
    display = configured_service_template("display-client")
    kiosk = configured_service_template("browser-kiosk")

    assert (
        "ExecStart=/opt/sdsctl-display/bin/sdsctl service-config run "
        "--manifest /etc/sdsctl/display-client.toml"
    ) in display
    assert "--remote-profile" not in display
    assert "DevicePolicy=closed" in display
    assert "TTYPath=/dev/tty1" in display
    assert (
        "ExecStart=/opt/sdsctl-kiosk/bin/sdsctl service-config run "
        "--manifest /etc/sdsctl/browser-kiosk.toml"
    ) in kiosk
    assert "--origin" not in kiosk
    assert "EnvironmentFile=" not in kiosk


def test_templates_do_not_claim_accepted_device_enrollment() -> None:
    kiosk = service_configuration_template("browser-kiosk")

    assert "Device enrollment is intentionally absent" in kiosk
    assert "device_key" not in kiosk
