from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from sds200 import cli
from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_api import DaemonApiRequest, DaemonReadOnlyApi
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_ipc import DaemonSocketListener, resolve_daemon_socket_location
from sds200.daemon_remote_server import (
    DAEMON_REMOTE_CONTROL_OPERATIONS,
    DAEMON_REMOTE_OBSERVE_OPERATIONS,
)
from sds200.daemon_server import DaemonApiServer
from sds200.events import EventBus
from sds200.scanner_display_configuration import (
    MAX_DISPLAY_CONFIGURATION_BYTES,
    ScannerDisplayConfigurationError,
    load_scanner_display_configuration,
    parse_scanner_display_configuration,
)
from sds200.scanner_display_profile_cli import _reload
from sds200.scanner_display_profile_storage import (
    DisplayProfileStorageError,
    initialize_display_profile_storage,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX manual-profile integration")
NOW = datetime(2026, 9, 15, 9, tzinfo=UTC)
TARGET = "udp://192.0.2.25:50536"
SOURCE = (
    b"Owner\tPRIVATE_SENTINEL\r\n"
    b"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\tOff\tAFS\tCOLOR\r\n"
    b"DispOptItems\tDispOptId=2\tDispLayoutId=1\tFrequency\tEmpty\r\n"
    b"DispColors\tDispColorId=2\tColorLayoutId=1\tffffff\t000000\r\n"
)


def config_bytes(root: Path, **updates):
    values = {
        "version": 1,
        "endpoint_id": str(UUID(int=1)),
        "source_id": str(UUID(int=2)),
        "scanner_target": TARGET,
        "source_path": str(root / "profile.cfg"),
        "state_directory": str(root / "accepted"),
    } | updates
    return "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items()).encode()


@pytest.fixture
def configured(tmp_path):
    path = tmp_path / "display.toml"
    path.write_bytes(config_bytes(tmp_path))
    path.chmod(0o600)
    config = load_scanner_display_configuration(path)
    config.source_path.write_bytes(SOURCE)
    initialize_display_profile_storage(config.state_directory, config.binding.endpoint_id)
    return path, config


def accept(config):
    repo = config.repository()
    preview = repo.prepare(config.binding, acquired_at=NOW)
    return repo.commit(preview, imported_at=NOW)


def run(path, *arguments):
    paths = cli.resolve_configuration_paths(
        environ={}, home=path.parent / "isolated-home", system_config_dir=path.parent / "system"
    )
    return cli.main(
        ["scanner-display-profile", "--manifest", str(path), *arguments],
        configuration_paths=paths,
        environ={},
    )


def request(op, **params):
    return DaemonApiRequest("display-test", op.value, params).as_dict()


class NoScannerIO:
    def snapshot(self):
        pytest.fail("Display profile operation must not call scanner runtime")


def test_configuration_is_explicit_inert_and_redacted(configured):
    path, config = configured
    assert config.scanner_target == TARGET
    assert config.source_path == path.parent / "profile.cfg"
    assert TARGET not in repr(config)
    assert str(path.parent) not in repr(config)
    before = list(config.state_directory.iterdir())
    assert load_scanner_display_configuration(path) == config
    assert list(config.state_directory.iterdir()) == before
    config.require_scanner_target(TARGET)
    config.require_separate_recordings(path.parent / "recordings")
    with pytest.raises(ScannerDisplayConfigurationError):
        config.require_scanner_target("udp://192.0.2.99:50536")


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("endpoint_id", "not-a-uuid"),
        ("source_id", "00000000000000000000000000000002"),
        ("scanner_target", ""),
        ("scanner_target", "PRIVATE_SENTINEL\n"),
        ("source_path", "relative.cfg"),
        ("source_path", "/tmp/PRIVATE_SENTINEL\x00.cfg"),
        ("state_directory", "/tmp/line\nbreak"),
        ("source_path", "/" + "x" * 4097),
        ("source_path", "/tmp/../profile.cfg"),
        ("state_directory", "/"),
        ("extra", "PRIVATE_SENTINEL"),
    ],
)
def test_invalid_configuration_is_fixed_error_without_echo(tmp_path, field, value):
    with pytest.raises(ScannerDisplayConfigurationError) as error:
        parse_scanner_display_configuration(config_bytes(tmp_path, **{field: value}))
    assert str(error.value) == "Scanner display configuration is invalid or unavailable."


@pytest.mark.parametrize("data", [b"", b"\xff", b"version = 1\nversion = 1", b"x" * 16385])
def test_missing_duplicate_nonutf8_or_oversized_config_refused(data):
    assert MAX_DISPLAY_CONFIGURATION_BYTES == 16384
    with pytest.raises(ScannerDisplayConfigurationError):
        parse_scanner_display_configuration(data)


@pytest.mark.parametrize("name", ["PRIVATE_SENTINEL\x00.toml", "line\nbreak.toml", "x" * 4097])
def test_invalid_manifest_path_is_sanitized_without_file_access(tmp_path, name):
    with pytest.raises(ScannerDisplayConfigurationError) as error:
        load_scanner_display_configuration(tmp_path / name)
    assert str(error.value) == "Scanner display configuration is invalid or unavailable."


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable", "fifo", "directory"])
def test_unsafe_configuration_not_followed_repaired_or_blocked(configured, kind):
    path, _ = configured
    if kind == "writable":
        path.chmod(0o666)
    else:
        moved = path.with_suffix(".keep")
        path.rename(moved)
        if kind == "symlink":
            path.symlink_to(moved)
        elif kind == "hardlink":
            os.link(moved, path)
        elif kind == "fifo":
            os.mkfifo(path)
        else:
            path.mkdir()
    with pytest.raises(ScannerDisplayConfigurationError):
        load_scanner_display_configuration(path)


def test_config_cannot_be_its_source_or_live_inside_state(tmp_path):
    path = tmp_path / "display.toml"
    for overrides in ({"source_path": str(path)}, {"state_directory": str(tmp_path)}):
        path.write_bytes(config_bytes(tmp_path, **overrides))
        with pytest.raises(ScannerDisplayConfigurationError):
            load_scanner_display_configuration(path)


@pytest.mark.parametrize("kind", ["ancestor", "state", "state-child", "source"])
def test_recording_overlap_refused_without_mutation(configured, kind):
    path, config = configured
    target = {
        "ancestor": path.parent,
        "state": config.state_directory,
        "state-child": config.state_directory / "recordings",
        "source": config.source_path,
    }[kind]
    before = config.source_path.read_bytes()
    with pytest.raises(ScannerDisplayConfigurationError):
        config.require_separate_recordings(target)
    assert config.source_path.read_bytes() == before


def test_recording_root_symlink_is_resolved_before_overlap_check(configured):
    path, config = configured
    alias = path.parent / "recordings-link"
    alias.symlink_to(config.state_directory, target_is_directory=True)
    with pytest.raises(ScannerDisplayConfigurationError):
        config.require_separate_recordings(alias)


def test_daemon_cache_only_adopts_accepted_import_on_explicit_reload(configured, monkeypatch):
    _, config = configured
    owner = DaemonDisplayProfile(config, lambda: TARGET)
    assert owner.snapshot()["accepted"] is None
    imported = accept(config)
    assert owner.snapshot()["accepted"] is None
    loaded = owner.reload()
    assert loaded["accepted"]["revision"] == imported.profile.revision
    assert "PRIVATE_SENTINEL" not in json.dumps(loaded)
    assert str(config.source_path) not in json.dumps(loaded)
    assert TARGET not in json.dumps(loaded)
    # Altering a response cannot mutate the owner cache; regular reads do no disk I/O.
    loaded["accepted"]["descriptor"]["option_groups"].clear()
    monkeypatch.setattr(owner._repository, "inspect", lambda: pytest.fail("unexpected file read"))
    for _ in range(25):
        assert owner.snapshot()["accepted"]["descriptor"]["option_groups"]


@pytest.mark.parametrize(
    "edit,status", [(b"bad copy", "invalid_source"), (None, "source_unavailable")]
)
def test_owner_reload_retains_accepted_profile_if_source_is_invalid_or_missing(
    configured, edit, status
):
    _, config = configured
    imported = accept(config)
    owner = DaemonDisplayProfile(config, lambda: TARGET)
    if edit is None:
        config.source_path.unlink()
    else:
        config.source_path.write_bytes(edit)
    result = owner.reload()
    assert result["accepted"]["revision"] == imported.profile.revision
    assert result["source_status"] == status


def test_cache_clears_on_endpoint_switch_until_explicit_reload(configured):
    _, config = configured
    accept(config)
    current = [TARGET]
    owner = DaemonDisplayProfile(config, lambda: current[0])
    current[0] = "/dev/ttyACM0"
    assert owner.snapshot()["accepted"] is None
    assert owner.snapshot()["failure"] == "endpoint_mismatch"
    current[0] = TARGET
    assert owner.snapshot()["accepted"] is None
    assert owner.reload()["accepted"] is not None


def test_corrupt_state_clears_cached_view_without_reinitialization(configured):
    _, config = configured
    accept(config)
    owner = DaemonDisplayProfile(config, lambda: TARGET)
    state = config.state_directory / "accepted-profile.json"
    state.write_bytes(b"PRIVATE_SENTINEL invalid")
    with pytest.raises(DisplayProfileStorageError):
        owner.reload()
    assert owner.snapshot()["accepted"] is None
    assert owner.snapshot()["failure"] == "state_invalid"
    assert state.read_bytes() == b"PRIVATE_SENTINEL invalid"


def test_api_disabled_by_default_no_generic_parameters_or_control_dispatch(configured):
    api = DaemonReadOnlyApi(NoScannerIO())
    capabilities = api.handle_payload(request(Op.CAPABILITIES)).result
    assert Op.DISPLAY_PROFILE.value not in capabilities["operations"]
    assert Op.DISPLAY_PROFILE_RELOAD.value not in capabilities["operations"]
    for operation in (Op.DISPLAY_PROFILE, Op.DISPLAY_PROFILE_RELOAD):
        assert api.handle_payload(request(operation)).error.code == "unsupported_operation"
    _, config = configured
    api.display_profile = DaemonDisplayProfile(config, lambda: TARGET)
    for operation in (Op.DISPLAY_PROFILE, Op.DISPLAY_PROFILE_RELOAD):
        assert (
            api.handle_payload(request(operation, path="/tmp/evil")).error.code
            == "invalid_parameters"
        )
        assert api.handle_control_payload(request(operation)).error.code == "unknown_operation"


@pytest.mark.parametrize(
    "allowed",
    [
        DAEMON_REMOTE_OBSERVE_OPERATIONS,
        DAEMON_REMOTE_OBSERVE_OPERATIONS + DAEMON_REMOTE_CONTROL_OPERATIONS,
        (Op.HELLO, Op.DISPLAY_PROFILE, Op.DISPLAY_PROFILE_RELOAD),
    ],
)
def test_remote_clients_can_read_projection_but_never_reload_even_if_allowlisted(
    configured, allowed
):
    _, config = configured
    accept(config)
    owner = DaemonDisplayProfile(config, lambda: TARGET)
    api = DaemonReadOnlyApi(NoScannerIO(), display_profile=owner)
    for operation in (Op.DISPLAY_PROFILE, Op.DISPLAY_PROFILE_RELOAD, Op.HELLO):
        response = json.loads(
            api.handle_authorized_json_line(
                json.dumps(request(operation)),
                allowed_operations=allowed,
            )
        )
        assert "PRIVATE_SENTINEL" not in json.dumps(response)
        if operation is Op.DISPLAY_PROFILE_RELOAD:
            assert response["error"]["code"] == "authorization_denied"
        else:
            assert response["ok"] is True
            if operation is Op.HELLO:
                assert Op.DISPLAY_PROFILE_RELOAD.value not in response["result"]["operations"]
    assert api.handle_payload(request(Op.DISPLAY_PROFILE_RELOAD)).error is None


def test_cli_status_preview_and_cancel_never_import_or_own_scanner(configured, capsys, monkeypatch):
    path, config = configured
    monkeypatch.setattr(cli, "selected_radio", lambda *a, **k: pytest.fail("scanner selected"))
    state = config.state_directory / "accepted-profile.json"
    before = state.read_bytes()
    assert run(path, "status") == 0
    assert run(path, "preview") == 0
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert run(path, "import") == 0
    assert state.read_bytes() == before
    output = capsys.readouterr().out
    assert "PRIVATE_SENTINEL" not in output
    assert "Cancelled" in output


def test_cli_initialization_is_explicit_and_no_import(tmp_path, capsys, monkeypatch):
    path = tmp_path / "display.toml"
    path.write_bytes(config_bytes(tmp_path))
    path.chmod(0o600)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    assert run(path, "init") == 2
    assert not (tmp_path / "accepted").exists()
    assert run(path, "init", "--yes") == 0
    assert run(path, "init", "--yes") == 2
    assert "No profile was imported" in capsys.readouterr().out


def test_cli_import_requires_confirmation_then_advances_only_accepted_record(
    configured, monkeypatch, capsys
):
    path, config = configured
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    assert run(path, "import") == 2
    assert config.repository().inspect().profile.last_good is None
    assert run(path, "import", "--yes") == 0
    assert config.repository().inspect().profile.last_good is not None
    assert config.source_path.read_bytes() == SOURCE
    output = capsys.readouterr().out
    assert "explicit local reload" in output
    assert "PRIVATE_SENTINEL" not in output


def test_cli_file_changed_during_interactive_review_is_refused(configured, monkeypatch):
    path, config = configured
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))

    def answer(prompt):
        config.source_path.write_bytes(SOURCE.replace(b"ffffff", b"123456"))
        return "y"

    monkeypatch.setattr("builtins.input", answer)
    assert run(path, "import") == 2
    assert config.repository().inspect().profile.last_good is None


def test_cli_import_acknowledges_persistence_when_reload_fails(configured, monkeypatch, capsys):
    path, config = configured
    from sds200 import scanner_display_profile_cli as command

    def fail(*args):
        raise OSError("PRIVATE_SENTINEL")

    monkeypatch.setattr(command, "_reload", fail)
    assert run(path, "import", "--yes", "--daemon-socket-path", "/tmp/unused.sock") == 2
    assert config.repository().inspect().profile.last_good is not None
    output = capsys.readouterr()
    assert "profile was accepted" in output.err
    assert "do not repeat the import" in output.err
    assert "PRIVATE_SENTINEL" not in output.err


def test_real_unix_api_reload_updates_only_matching_daemon(configured, tmp_path):
    _, config = configured
    owner = DaemonDisplayProfile(config, lambda: TARGET)
    api = DaemonReadOnlyApi(NoScannerIO(), display_profile=owner)
    location = resolve_daemon_socket_location(tmp_path / "s")
    server = DaemonApiServer(DaemonSocketListener(location), api)
    server.start()
    try:
        accepted = accept(config)
        assert owner.snapshot()["accepted"] is None
        result = _reload(config, tmp_path / "s")
        assert result["accepted"]["revision"] == accepted.profile.revision
        assert owner.snapshot()["accepted"]["revision"] == accepted.profile.revision
    finally:
        server.stop()


def test_reload_refuses_wrong_daemon_before_mutation(configured, monkeypatch):
    _, config = configured
    from sds200 import scanner_display_profile_cli as command

    calls = []

    class Client:
        def __init__(self, location):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, operation):
            calls.append(operation)
            return {"endpoint_id": str(UUID(int=999))}

    monkeypatch.setattr(command, "DaemonApiClient", Client)
    with pytest.raises(ValueError, match="does not match"):
        _reload(config, Path("/tmp/unused.sock"))
    assert calls == [Op.DISPLAY_PROFILE]


@pytest.mark.parametrize("failure", ["invalid_config", "recording_overlap", "wrong_target"])
def test_daemon_configuration_failure_never_starts_scanner_or_process(
    configured, monkeypatch, failure, capsys
):
    path, config = configured
    if failure == "invalid_config":
        path.write_bytes(b"PRIVATE_SENTINEL")
    if failure == "wrong_target":
        monkeypatch.setattr(
            cli, "selected_radio", lambda *a, **k: SimpleNamespace(endpoint="WRONG")
        )
    else:
        monkeypatch.setattr(cli, "selected_radio", lambda *a, **k: pytest.fail("scanner selected"))
    monkeypatch.setattr(cli, "DaemonRuntime", lambda *a, **k: pytest.fail("runtime created"))
    paths = cli.resolve_configuration_paths(
        environ={}, home=path.parent / "isolated-home", system_config_dir=path.parent / "system"
    )
    arguments = ["--host", "192.0.2.25", "daemon", "--scanner-display-profile-config", str(path)]
    if failure == "recording_overlap":
        arguments += ["--recording-directory", str(config.state_directory)]
    assert cli.main(arguments, configuration_paths=paths, environ={}) == 2
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().err


def test_daemon_cli_wires_cache_into_single_existing_api_owner(configured, monkeypatch):
    path, config = configured
    accept(config)
    from sds200.daemon_process import DaemonProcessResult

    captured = []
    events = EventBus()
    scanner = SimpleNamespace(
        endpoint=TARGET, connected=False,
        on_connection=lambda callback: events.subscribe("connection", callback),
        on_psi=lambda callback: events.subscribe("psi", callback),
    )
    monkeypatch.setattr(cli, "selected_radio", lambda *a, **k: scanner)
    monkeypatch.setattr(cli, "DaemonEventStream", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "DaemonDestinationCoordinator", lambda *a, **k: SimpleNamespace())

    class Process:
        def __init__(self, runtime, **kwargs):
            captured.append((runtime, kwargs["api_server"].api))

        def run(self):
            return DaemonProcessResult(last_signal=None)

    monkeypatch.setattr(cli, "DaemonProcess", Process)
    paths = cli.resolve_configuration_paths(
        environ={}, home=path.parent / "isolated-home", system_config_dir=path.parent / "system"
    )
    # Construction only, with a fake process; no sockets or scanner transport starts.
    assert (
        cli.main(
            ["--host", "192.0.2.25", "daemon", "--scanner-display-profile-config", str(path)],
            configuration_paths=paths,
            environ={},
        )
        == 0
    )
    assert len(captured) == 1
    runtime, api = captured[0]
    assert api.runtime is runtime
    assert api.display_frames is not None
    assert not any(events._callbacks.values())
    result = api.handle_payload(request(Op.DISPLAY_PROFILE))
    assert result.result["accepted"]["descriptor"]["option_groups"]
