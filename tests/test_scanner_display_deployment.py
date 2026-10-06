from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from sds200 import cli
from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_api import DaemonReadOnlyApi
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_ipc import DaemonSocketListener, resolve_daemon_socket_location
from sds200.daemon_server import DaemonApiServer
from sds200.home_assistant_app import (
    HomeAssistantAppOptions,
    HomeAssistantAppSupervisorInfo,
    HomeAssistantMqttService,
    load_home_assistant_app_options,
)
from sds200.home_assistant_app_advanced import default_home_assistant_app_advanced_access_paths
from sds200.home_assistant_app_runtime import (
    HomeAssistantAppRuntimePaths,
    build_home_assistant_daemon_command,
    build_home_assistant_web_command,
    default_home_assistant_app_runtime_paths,
)
from sds200.home_assistant_app_supervisor import prepare_home_assistant_app_launch_plan
from sds200.scanner_display_configuration import (
    ScannerDisplayConfigurationError,
    load_scanner_display_configuration,
)
from sds200.scanner_display_deployment import (
    load_scanner_display_deployment,
    parse_scanner_display_deployment,
)
from sds200.scanner_display_ingress import DISPLAY_PROFILE_ADMIN_PATH as ROUTE
from sds200.scanner_display_profile_storage import initialize_display_profile_storage

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private display deployment")
UID = "a" * 32
TARGET = "udp://192.0.2.25:50536"
ORIGIN = "https://ha.example.test"
SOURCE = (
    b"Owner\tPRIVATE_SENTINEL\r\n"
    b"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\tOff\tAFS\tCOLOR\r\n"
    b"DispOptItems\tDispOptId=2\tDispLayoutId=1\tFrequency\tEmpty\r\n"
    b"DispColors\tDispColorId=2\tColorLayoutId=1\tffffff\t000000\r\n"
)


def document(values):
    return "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items()).encode()


def fields(root, **updates):
    return {
        "version": 1,
        "profile_config": str(root / "display.toml"),
        "ingress_origin": ORIGIN,
        "admin_user_ids": [UID],
        "allow_upload": False,
    } | updates


@pytest.fixture
def configured(tmp_path):
    managed = tmp_path / "managed"
    managed.mkdir(mode=0o700)
    manifest = tmp_path / "display.toml"
    manifest.write_bytes(
        document(
            {
                "version": 1,
                "endpoint_id": str(UUID(int=1)),
                "source_id": str(UUID(int=2)),
                "scanner_target": TARGET,
                "source_path": str(managed / "profile.cfg"),
                "state_directory": str(tmp_path / "accepted"),
            }
        )
    )
    manifest.chmod(0o600)
    config = load_scanner_display_configuration(manifest)
    config.source_path.write_bytes(SOURCE)
    config.source_path.chmod(0o600)
    initialize_display_profile_storage(config.state_directory, config.binding.endpoint_id)
    deployment = tmp_path / "deployment.toml"
    deployment.write_bytes(document(fields(tmp_path, allow_upload=True)))
    deployment.chmod(0o600)
    return deployment, config


def contents(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("origin", [ORIGIN, "https://192.0.2.18:8123", "https://[fd00::18]:8123"])
def test_deployment_supports_dns_and_literal_https_origins(tmp_path, origin):
    parsed = parse_scanner_display_deployment(document(fields(tmp_path, ingress_origin=origin)))
    assert parsed.ingress_origin == origin and parsed.admin_user_ids == frozenset({UID})
    assert parsed.allow_upload is False
    assert UID not in repr(parsed) and str(tmp_path) not in repr(parsed)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "update",
    [
        {"version": True},
        {"version": 2},
        {"extra": "PRIVATE_SENTINEL"},
        {"profile_config": "relative"},
        {"profile_config": "/"},
        {"profile_config": "/tmp/../etc/profile"},
        {"profile_config": "/tmp/x\nsecret"},
        {"profile_config": 1},
        {"profile_config": "/" + "x" * 4096},
        {"ingress_origin": "http://192.0.2.18:8123"},
        {"ingress_origin": ORIGIN + "/"},
        {"ingress_origin": ORIGIN + "/path"},
        {"ingress_origin": ORIGIN + "?token=x"},
        {"ingress_origin": "https://user:secret@ha.example.test"},
        {"ingress_origin": 12},
        {"admin_user_ids": []},
        {"admin_user_ids": [UID, UID]},
        {"admin_user_ids": [1]},
        {"admin_user_ids": "*"},
        {"admin_user_ids": ["A" * 32]},
        {"admin_user_ids": ["admin"]},
        {"admin_user_ids": [f"{n:032x}" for n in range(33)]},
        {"allow_upload": "true"},
        {"allow_upload": 1},
    ],
)
def test_deployment_fields_fail_closed_without_echo(tmp_path, update):
    with pytest.raises(ScannerDisplayConfigurationError) as error:
        parse_scanner_display_deployment(document(fields(tmp_path, **update)))
    assert "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("data", [b"", b"x" * 16385, b"\xff", b"[", b"version=1\nversion=1"])
def test_deployment_bad_documents_are_bounded(data):
    with pytest.raises(ScannerDisplayConfigurationError):
        parse_scanner_display_deployment(data)


def test_preflight_is_read_only_and_does_not_import(configured, tmp_path):
    path, config = configured
    before = contents(tmp_path)
    parsed = load_scanner_display_deployment(path)
    assert parsed.preflight(tmp_path / "recordings") == config
    assert config.repository().inspect().profile.last_good is None
    assert contents(tmp_path) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable", "fifo", "missing"])
def test_deployment_unsafe_file_refused(configured, tmp_path, kind):
    path, _ = configured
    if kind == "symlink":
        saved = path.with_suffix(".saved")
        path.rename(saved)
        path.symlink_to(saved)
    elif kind == "hardlink":
        os.link(path, path.with_suffix(".saved"))
    elif kind == "writable":
        path.chmod(0o666)
    else:
        path.unlink()
        if kind == "fifo":
            os.mkfifo(path)
    with pytest.raises(ScannerDisplayConfigurationError):
        load_scanner_display_deployment(path)


@pytest.mark.parametrize(
    "kind", ["source-mode", "source-parent", "state-mode", "state-missing", "overlap"]
)
def test_preflight_does_not_repair_storage(configured, tmp_path, kind):
    path, config = configured
    root = tmp_path / "recordings"
    if kind == "source-mode":
        config.source_path.chmod(0o644)
    elif kind == "source-parent":
        config.source_path.parent.chmod(0o755)
    elif kind == "state-mode":
        config.state_directory.chmod(0o755)
    elif kind == "state-missing":
        config.state_directory.rename(tmp_path / "kept")
    else:
        root = config.source_path.parent
    before = contents(tmp_path)
    with pytest.raises(ScannerDisplayConfigurationError):
        load_scanner_display_deployment(path).preflight(root)
    assert contents(tmp_path) == before


def test_missing_managed_copy_allowed_but_not_created(configured, tmp_path):
    path, config = configured
    config.source_path.unlink()
    load_scanner_display_deployment(path).preflight(tmp_path / "recordings")
    assert not config.source_path.exists()
    config.source_path.parent.rmdir()
    with pytest.raises(ScannerDisplayConfigurationError):
        load_scanner_display_deployment(path).preflight(tmp_path / "recordings")
    assert not config.source_path.parent.exists()


@pytest.mark.parametrize(
    "value", [False, 12, "relative", "/", "/tmp/../x", "/tmp/x\n", "/tmp/\x00"]
)
def test_app_option_rejects_bad_config_path(value):
    with pytest.raises(ValueError):
        HomeAssistantAppOptions(scanner_host="192.0.2.25", scanner_display_config=value)


def test_default_app_and_published_catalog_keep_feature_disabled(tmp_path):
    options = tmp_path / "options.json"
    options.write_text('{"scanner_host":"192.0.2.25"}')
    assert load_home_assistant_app_options(options).scanner_display_config == ""
    paths = default_home_assistant_app_runtime_paths()
    commands = build_home_assistant_daemon_command(
        load_home_assistant_app_options(options), paths
    ) + build_home_assistant_web_command(paths)
    assert all("scanner-display" not in argument for argument in commands)
    catalog = Path(__file__).resolve().parents[1] / "home-assistant/sds200/config.yaml"
    catalog_text = catalog.read_text()
    assert '  scanner_display_config: ""' in catalog_text
    assert '  scanner_display_config: "str?"' in catalog_text


def launch(configured, tmp_path, monkeypatch, *, host="192.0.2.25", display=True):
    deployment, _ = configured
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {"scanner_host": host, "scanner_display_config": str(deployment) if display else ""}
        )
    )
    runtime = tmp_path / "run"
    paths = HomeAssistantAppRuntimePaths(
        runtime,
        runtime / "mqtt.toml",
        runtime / "daemon.sock",
        runtime / "events.sock",
        runtime / "pcmu.sock",
        runtime / "recordings.sock",
        tmp_path / "recordings",
    )
    advanced = default_home_assistant_app_advanced_access_paths(
        runtime_directory=runtime,
        root=tmp_path / "advanced",
    )
    monkeypatch.setattr(
        "sds200.home_assistant_app_supervisor.fetch_home_assistant_mqtt_service",
        lambda **kw: HomeAssistantMqttService("mqtt", 1883, False, "user", "secret", "3.1.1"),
    )
    return prepare_home_assistant_app_launch_plan(
        options_path=options,
        paths=paths,
        advanced_paths=advanced,
        supervisor_info=HomeAssistantAppSupervisorInfo("172.30.33.7", {}),
        environ={},
    )


def test_app_launch_wires_one_manifest_without_new_listeners(configured, tmp_path, monkeypatch):
    deployment, config = configured
    source, state = config.source_path.read_bytes(), contents(config.state_directory)
    plan = launch(configured, tmp_path, monkeypatch)
    assert plan.daemon_command[-2:] == (
        "--scanner-display-profile-config",
        str(tmp_path / "display.toml"),
    )
    index = plan.web_command.index("--scanner-display-admin-config")
    assert plan.web_command[index + 1 : index + 4] == (
        str(deployment),
        "--scanner-display-recording-directory",
        str(tmp_path / "recordings"),
    )
    assert plan.native_web_command == ()
    assert plan.advanced_exposure.enabled is False
    assert "scanner-display" not in " ".join(plan.media_command)
    assert config.source_path.read_bytes() == source and contents(config.state_directory) == state


def test_wrong_scanner_stops_launch_before_runtime_side_effects(configured, tmp_path, monkeypatch):
    with pytest.raises(ScannerDisplayConfigurationError):
        launch(configured, tmp_path, monkeypatch, host="192.0.2.26")
    assert not (tmp_path / "run").exists()
    assert not (tmp_path / "advanced").exists()
    assert not (tmp_path / "recordings").exists()


def test_candidate_enable_restart_disable_reenable_preserves_reviewed_import(
    configured, tmp_path, monkeypatch
):
    """Launch-plan/reconstructed-owner test, not a claim of live Supervisor upgrade."""
    _, config = configured
    repository = config.repository()
    preview = repository.prepare(config.binding, acquired_at=datetime.now(UTC))
    repository.commit(preview, imported_at=datetime.now(UTC))
    accepted = contents(config.state_directory)
    revision = repository.inspect().profile.last_good.profile.revision
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    (recordings / "keep.wav").write_bytes(b"synthetic recording sentinel")
    recording_files = contents(recordings)

    for enabled in (False, True, True, False, True):
        plan = launch(configured, tmp_path, monkeypatch, display=enabled)
        assert ("--scanner-display-profile-config" in plan.daemon_command) is enabled
        assert ("--scanner-display-admin-config" in plan.web_command) is enabled
        assert not plan.native_web_command and not plan.advanced_exposure.enabled
        if enabled:
            # Model a fresh daemon owner: accepted state, never an implicit import.
            owner = DaemonDisplayProfile(config, lambda: TARGET)
            assert owner.snapshot()["accepted"]["revision"] == revision
        assert contents(config.state_directory) == accepted
        assert contents(recordings) == recording_files
        assert config.source_path.read_bytes() == SOURCE


@pytest.mark.parametrize("edit", [SOURCE.replace(b"ffffff", b"123456"), b"invalid profile"])
def test_restart_does_not_accept_changed_or_invalid_managed_source(
    configured, tmp_path, monkeypatch, edit
):
    _, config = configured
    repository = config.repository()
    preview = repository.prepare(config.binding, acquired_at=datetime.now(UTC))
    repository.commit(preview, imported_at=datetime.now(UTC))
    accepted = contents(config.state_directory)
    prior = DaemonDisplayProfile(config, lambda: TARGET).snapshot()["accepted"]
    config.source_path.write_bytes(edit)
    launch(configured, tmp_path, monkeypatch)
    restored = DaemonDisplayProfile(config, lambda: TARGET).snapshot()
    assert restored["accepted"] == prior
    expected_status = "invalid_source" if edit == b"invalid profile" else "changed_since_import"
    assert restored["source_status"] == expected_status
    assert contents(config.state_directory) == accepted
    assert config.source_path.read_bytes() == edit


def test_disabled_launch_does_not_require_or_repair_candidate_state(
    configured, tmp_path, monkeypatch
):
    _, config = configured
    config.state_directory.rename(tmp_path / "preserved-for-review")
    before = contents(tmp_path / "preserved-for-review")
    plan = launch(configured, tmp_path, monkeypatch, display=False)
    assert "--scanner-display-profile-config" not in plan.daemon_command
    assert "--scanner-display-admin-config" not in plan.web_command
    assert not config.state_directory.exists()
    assert contents(tmp_path / "preserved-for-review") == before


@pytest.mark.parametrize(
    "args",
    [
        ["--scanner-display-admin-config", "/missing"],
        ["--scanner-display-recording-directory", "/recordings"],
        [],
        ["--container-exposure"],
        ["--authenticated-lan"],
    ],
)
def test_web_cli_refuses_wrong_mode_or_incomplete_options(args, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("No daemon or listener may start for invalid options")

    monkeypatch.setattr(cli, "DaemonApiClient", forbidden)
    monkeypatch.setattr(cli, "run_web_dashboard_server", forbidden)
    if not any("scanner-display" in item for item in args):
        args += [
            "--scanner-display-admin-config",
            "/missing",
            "--scanner-display-recording-directory",
            "/recordings",
        ]
    parsed = cli.build_parser().parse_args(["web", *args])
    with pytest.raises(ValueError):
        cli._run_web(parsed, environ={})


class NoScannerIO:
    def __getattr__(self, name):
        raise AssertionError(f"Scanner I/O forbidden: {name}")


def test_real_cli_ingress_upload_reloads_only_accepted_daemon_state(
    configured, tmp_path, monkeypatch
):
    deployment, config = configured
    owner = DaemonDisplayProfile(config, lambda: TARGET)
    api = DaemonReadOnlyApi(NoScannerIO(), display_profile=owner)
    socket_path = tmp_path / "s"
    server = DaemonApiServer(DaemonSocketListener(resolve_daemon_socket_location(socket_path)), api)
    server.start()
    calls = []

    def serve(app, **kwargs):
        assert kwargs["home_assistant_ingress"] is True
        assert kwargs["authenticated_lan"] is False
        assert owner.snapshot()["accepted"] is None
        with TestClient(app, client=("172.30.32.2", 1234)) as client:
            assert client.get(ROUTE).status_code == 403
            headers = {"X-Remote-User-Id": UID}
            page = client.get(ROUTE, headers=headers)
            assert page.status_code == 200
            token = re.search(r'name="profile-csrf" content="([a-f0-9]+)"', page.text).group(1)
            headers |= {
                "Origin": ORIGIN,
                "X-SDSCTL-Profile-CSRF": token,
                "Content-Type": "application/octet-stream",
            }
            preview = client.post(
                ROUTE + "/upload", headers=headers, content=SOURCE.replace(b"ffffff", b"123456")
            )
            assert preview.status_code == 200, preview.text
            assert owner.snapshot()["accepted"] is None
            data = preview.json()
            assert "PRIVATE_SENTINEL" not in preview.text
            headers |= {"X-SDSCTL-Profile-CSRF": data["csrf"], "Content-Type": "application/json"}
            accepted = client.post(
                ROUTE + "/commit",
                headers=headers,
                json={
                    "review_id": data["review_id"],
                    "confirm_source_change": False,
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["daemon_reload"] == "confirmed"
            assert owner.snapshot()["accepted"]["revision"] == data["revision"]
            assert (
                client.get(ROUTE + "/status", headers={"X-Remote-User-Id": "b" * 32}).status_code
                == 403
            )
            calls.append("complete")
        return 0

    monkeypatch.setattr(cli, "run_web_dashboard_server", serve)
    try:
        args = cli.build_parser().parse_args(
            [
                "web",
                "--home-assistant-ingress",
                "--scanner-display-admin-config",
                str(deployment),
                "--scanner-display-recording-directory",
                str(tmp_path / "recordings"),
                "--daemon-socket-path",
                str(socket_path),
            ]
        )
        assert cli._run_web(args, environ={}) == 0
        assert calls == ["complete"]
    finally:
        server.stop()


@pytest.mark.parametrize("mismatch", ["endpoint", "disabled", "failure"])
def test_web_preflight_refuses_wrong_daemon_without_writes(
    configured, tmp_path, monkeypatch, mismatch
):
    deployment, _ = configured
    before = contents(tmp_path)
    requests = []

    class Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def request(self, operation):
            requests.append(operation)
            return {
                "endpoint_id": str(UUID(int=9 if mismatch == "endpoint" else 1)),
                "configured": mismatch != "disabled",
                "failure": "unsafe_path" if mismatch == "failure" else None,
            }

    monkeypatch.setattr(cli, "DaemonApiClient", Client)
    args = cli.build_parser().parse_args(
        [
            "web",
            "--home-assistant-ingress",
            "--scanner-display-admin-config",
            str(deployment),
            "--scanner-display-recording-directory",
            str(tmp_path / "recordings"),
        ]
    )
    with pytest.raises(ValueError, match="does not match"):
        cli._run_web(args, environ={})
    assert requests == [Op.DISPLAY_PROFILE]
    assert contents(tmp_path) == before
