from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from sds200 import cli
from sds200.daemon_api import DaemonApiOperation as Op
from sds200.daemon_api import DaemonApiRequest, DaemonReadOnlyApi
from sds200.daemon_client import DaemonApiClient
from sds200.daemon_display_frames import DaemonDisplayFrames
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_ipc import DaemonSocketListener, resolve_daemon_socket_location
from sds200.daemon_remote_server import DAEMON_REMOTE_OBSERVE_OPERATIONS
from sds200.daemon_server import DaemonApiServer
from sds200.events import EventBus
from sds200.radio import SDS200
from sds200.scanner_display_adapter import DisplayObservationStatus as Status
from sds200.scanner_display_configuration import load_scanner_display_configuration
from sds200.scanner_display_frame import project_scanner_display_frame
from sds200.scanner_display_profile import ScannerDisplayMode
from sds200.scanner_display_profile_storage import (
    DisplayProfileStorageError,
    initialize_display_profile_storage,
)
from sds200.web_auth import WebDashboardAuthentication
from sds200.web_dashboard import create_web_dashboard_app
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeTransport

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX daemon display profile")
TARGET = "udp://192.0.2.25:50536"


def profile_bytes(simple=False):
    records = [
        "Owner\tPRIVATE_SENTINEL",
        f"DisplayOption\t\t\t\t\t\tDEC\t\t\t\t\t{'On' if simple else 'Off'}\tAFS\tCOLOR",
    ]
    for mode in ScannerDisplayMode:
        layout_id, color_id = mode.layout_ids
        count = 2 if layout_id in (1, 2) else 12 if layout_id in (3, 4) else 8
        tokens = ["Frequency", "SiteName", *["Empty"] * (count - 2)]
        records.append(f"DispOptItems\tDispOptId=2\tDispLayoutId={layout_id}\t" + "\t".join(tokens))
        small_count = 8 if layout_id in (1, 2) else 6
        small = ["Volume", "Squelch", "REC", *["Empty"] * (small_count - 3)]
        records.append(f"DispOptItems\tDispOptId=3\tDispLayoutId={layout_id}\t" + "\t".join(small))
        records.append(f"DispColors\tDispColorId=2\tColorLayoutId={color_id}\tffffff\t000000")
    return "\r\n".join(records).encode()


def info(screen="trunk_scan", content=None):
    if content is None:
        content = (
            '<System Name="Demo System" Hold="On"/><Department Name="Demo Dept" Hold="Off"/>'
            '<TGID Name="Demo Channel" TGID="1234" Hold="On"/>'
            '<Site Name="Demo Site"/><SiteFrequency Freq="08511250"/>'
            '<Property VOL="0" SQL="2" A_Led="Blue"/>'
        )
    return ScannerInfoParser().parse(
        "PSI", f'<ScannerInfo Mode="Demo" V_Screen="{screen}">{content}</ScannerInfo>'
    )


class Scanner:
    endpoint = TARGET
    connected = False

    def __init__(self):
        self.events = EventBus()

    def on_psi(self, callback):
        return self.events.subscribe("psi", callback)

    def on_connection(self, callback):
        return self.events.subscribe("connection", callback)

    def connect_event(self, connected):
        self.connected = connected
        self.events.emit("connection", connected)

    def sample(self, sample=None):
        self.events.emit("psi", info() if sample is None else sample)

    def __getattr__(self, name):
        raise AssertionError(f"Scanner command or other service access forbidden: {name}")


def accept(config):
    repository = config.repository()
    now = datetime.now(UTC)
    preview = repository.prepare(config.binding, acquired_at=now)
    repository.commit(preview, imported_at=now)


@pytest.fixture
def configured(tmp_path):
    path = tmp_path / "display.toml"
    values = {
        "version": 1,
        "endpoint_id": str(UUID(int=1)),
        "source_id": str(UUID(int=2)),
        "scanner_target": TARGET,
        "source_path": str(tmp_path / "profile.cfg"),
        "state_directory": str(tmp_path / "accepted"),
    }
    path.write_text("\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items()))
    path.chmod(0o600)
    config = load_scanner_display_configuration(path)
    config.source_path.write_bytes(profile_bytes())
    initialize_display_profile_storage(config.state_directory, config.binding.endpoint_id)
    accept(config)
    return config


@pytest.fixture
def live(configured):
    scanner, clock = Scanner(), SimpleNamespace(now=10.0)
    profile = DaemonDisplayProfile(configured, lambda: scanner.endpoint)
    feed = DaemonDisplayFrames(profile, scanner, clock=lambda: clock.now)
    feed.start()
    scanner.connect_event(True)
    scanner.sample()
    try:
        yield feed, profile, scanner, clock
    finally:
        feed.close()


def texts(snapshot):
    return {
        region["text"]
        for frame in snapshot["frames"].values()
        for region in (frame["screen"] or {}).get("regions", [])
        if region["text"] is not None
    }


def test_stream_has_independent_presentations_no_private_source_or_scanner_commands(live, tmp_path):
    feed, _, _, _ = live
    snapshot = feed.snapshot()
    assert snapshot["schema_version"] == 1
    assert UUID(snapshot["stream_id"]) and UUID(snapshot["session_id"])
    frames = snapshot["frames"]
    assert frames["preferred"]["screen"]["mode"] == "detail_trunk"
    assert frames["simple"]["screen"]["mode"] == "simple_trunk"
    assert frames["detail"]["screen"]["mode"] == "detail_trunk"
    assert frames["preferred"]["layout_basis"] == "profile_preference_unconfirmed"
    assert frames["simple"]["layout_basis"] == "explicit_presentation_choice"
    assert "Demo Channel" in texts(snapshot)
    assert {frame["sequence"] for frame in frames.values()} == {1}
    assert {frame["age_seconds"] for frame in frames.values()} == {0}
    assert frames["simple"]["indicators"] == {
        "alert_led": "Blue",
        "system_hold": True,
        "department_hold": False,
        "channel_hold": True,
    }
    rendered = json.dumps(snapshot, allow_nan=False)
    for forbidden in ("PRIVATE_SENTINEL", str(tmp_path), TARGET, "<ScannerInfo", "source_path"):
        assert forbidden not in rendered
    assert len(rendered.encode()) < 256 * 1024
    frames["simple"]["screen"]["regions"].clear()
    assert feed.snapshot()["frames"]["simple"]["screen"]["regions"]


def test_existing_radio_parser_supplies_only_complete_psi_without_commands(configured):
    transport = FakeTransport(TARGET)
    radio = SDS200.from_transport(transport, expected_model="SDS200")
    clock = SimpleNamespace(now=10.0)
    feed = DaemonDisplayFrames(
        DaemonDisplayProfile(configured, lambda: radio.endpoint),
        radio,
        clock=lambda: clock.now,
    )
    feed.start()
    try:
        with radio:
            transport.feed_line("PSI,<XML>,")
            transport.feed_line('<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">')
            transport.feed_line('<TGID Name="Complete frame only"/>')
            assert not texts(feed.snapshot())
            transport.feed_line("</ScannerInfo>")
            assert "Complete frame only" in texts(feed.snapshot())
            sequence = feed.snapshot()["frames"]["preferred"]["sequence"]
            clock.now = 13.0
            transport.feed_line("VOL,5")
            radio.events.emit("state", radio.state.snapshot)
            assert feed.snapshot()["frames"]["preferred"]["sequence"] == sequence
            assert feed.snapshot()["frames"]["preferred"]["age_seconds"] == 3.0
            transport.set_connected(False)
            assert not texts(feed.snapshot())
            transport.set_connected(True)
            assert feed.snapshot()["frames"]["preferred"]["status"] == "waiting"
    finally:
        feed.close()
    assert transport.writes == []


def test_equal_complete_observations_refresh_but_status_reads_do_not(live):
    feed, _, scanner, clock = live
    clock.now = 14
    assert feed.snapshot()["frames"]["preferred"]["age_seconds"] == 4
    scanner.sample()
    assert feed.snapshot()["frames"]["preferred"]["sequence"] == 2
    clock.now = 18
    assert feed.snapshot()["frames"]["preferred"]["status"] == "current"
    clock.now = 19
    stale = feed.snapshot()
    assert stale["frames"]["preferred"]["status"] == "stale"
    assert not texts(stale)
    assert set(stale["frames"]["preferred"]["indicators"].values()) == {None}
    assert stale["frames"]["preferred"]["screen"] is not None


def test_disconnect_reconnect_rejects_already_copied_old_callbacks(live):
    feed, _, scanner, clock = live
    first = feed.snapshot()
    callback = scanner.events._callbacks["psi"][0]
    scanner.connect_event(False)
    disconnected = feed.snapshot()
    assert disconnected["session_id"] is None and not texts(disconnected)
    clock.now = 11
    scanner.connect_event(True)
    callback(info())
    waiting = feed.snapshot()
    assert waiting["session_id"] != first["session_id"]
    assert waiting["stream_id"] == first["stream_id"]
    assert waiting["frames"]["simple"]["status"] == "waiting"
    assert not texts(waiting)
    scanner.sample()
    assert texts(feed.snapshot())


@pytest.mark.parametrize(
    "screen,content,mode",
    [
        (
            "conventional_scan",
            '<System Name="Conv"/><ConvFrequency Name="Simplex" Freq="01465200"/>',
            "detail_conventional",
        ),
        ("custom_search", '<SrchFrequency Freq="01465200"/>', "search_close_call"),
        ("quick_search", '<SrchFrequency Freq="01465200"/>', "search_close_call"),
        ("close_call", '<SrchFrequency Freq="01465200"/>', "search_close_call"),
        ("cc_searching", "<Property/>", "search_close_call"),
        ("wx_alert", '<WxChannel Name="Demo WX" Freq="01625500"/>', "weather"),
        ("tone_out", '<ToneOutChannel Name="Demo tones" Freq="01541500"/>', "tone_out"),
    ],
)
def test_family_changes_use_complete_current_screen_without_previous_names(
    live, screen, content, mode
):
    feed, _, scanner, clock = live
    clock.now = 11
    scanner.sample(info(screen, content))
    snapshot = feed.snapshot()
    assert snapshot["frames"]["preferred"]["screen"]["mode"] == mode
    assert "Demo Channel" not in texts(snapshot)
    if screen != "conventional_scan":
        assert {frame["screen"]["mode"] for frame in snapshot["frames"].values()} == {mode}
        assert {frame["layout_basis"] for frame in snapshot["frames"].values()} == {
            "documented_screen"
        }
    assert set(snapshot["frames"]["preferred"]["indicators"].values()) == {None}


@pytest.mark.parametrize(
    "sample,status",
    [
        (info("waterfall", ""), "unsupported_screen"),
        (info(content="<PopupScreen/>"), "override"),
        (info(content="<Property/><Property/>"), "ambiguous_records"),
        (info(content='<WxChannel Name="wrong family"/>'), "ambiguous_records"),
        (info("unexpected", ""), "unsupported_screen"),
    ],
)
def test_unqualified_samples_clear_previous_values_and_indicators(live, sample, status):
    feed, _, scanner, _ = live
    scanner.sample(sample)
    snapshot = feed.snapshot()
    assert snapshot["frames"]["preferred"]["status"] == status
    assert not texts(snapshot)
    assert set(snapshot["frames"]["preferred"]["indicators"].values()) == {None}


def test_invalid_observation_is_cleared_and_next_complete_sample_recovers(live):
    feed, _, scanner, _ = live
    scanner.events.emit("psi", object())
    rejected = feed.snapshot()
    assert rejected["failure"] == "invalid_observation"
    assert not texts(rejected)
    scanner.sample()
    assert feed.snapshot()["failure"] is None and texts(feed.snapshot())


def test_manual_acceptance_and_reload_update_one_coherent_profile_for_all_styles(live, configured):
    feed, profile, scanner, _ = live
    old = feed.snapshot()
    configured.source_path.write_bytes(profile_bytes(simple=True).replace(b"ffffff", b"123456"))
    assert feed.snapshot() == old
    accept(configured)
    assert feed.snapshot() == old
    profile.reload()
    new = feed.snapshot()
    revisions = {frame["profile_revision"] for frame in new["frames"].values()}
    assert len(revisions) == 1
    assert old["frames"]["preferred"]["profile_revision"] not in revisions
    assert new["frames"]["preferred"]["screen"]["mode"] == "simple_trunk"
    assert new["frames"]["detail"]["screen"]["mode"] == "detail_trunk"
    assert new["frames"]["preferred"]["sequence"] == 1
    assert len(scanner.events._callbacks["psi"]) == 1


def test_concurrent_readers_receive_coherent_frames_and_leave_one_subscription(live):
    feed, _, scanner, _ = live
    start = threading.Barrier(4)
    samples = [info(content=f'<TGID Name="Channel {index}"/>') for index in range(80)]

    def publish():
        start.wait(3)
        for sample in samples:
            scanner.sample(sample)

    def read():
        start.wait(3)
        previous = 0
        for _ in range(40):
            snapshot = feed.snapshot()
            frames = snapshot["frames"].values()
            sequences = {frame["sequence"] for frame in frames}
            assert len(sequences) == 1
            sequence = sequences.pop()
            assert sequence >= previous
            previous = sequence
            names = {text for text in texts(snapshot) if text.startswith("Channel ")}
            assert len(names) <= 1
            assert len({frame["profile_revision"] for frame in frames}) == 1
            assert len({frame["age_seconds"] for frame in frames}) == 1

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(publish), *[executor.submit(read) for _ in range(3)]]
        for future in futures:
            future.result(timeout=10)
    assert feed.snapshot()["frames"]["simple"]["sequence"] == 81
    assert len(scanner.events._callbacks["psi"]) == 1


def test_frame_reads_never_read_profile_files_or_refresh_source(live, monkeypatch):
    feed, profile, _, _ = live

    def forbidden(*a, **kw):
        pytest.fail("Frame reads must not touch files or scanner commands")

    monkeypatch.setattr(profile._repository, "inspect", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    for _ in range(8):
        assert texts(feed.snapshot())


def test_slow_profile_reload_does_not_block_scanner_callbacks(live, monkeypatch):
    feed, profile, scanner, clock = live
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()
    inspect = profile._repository.inspect

    def delayed():
        entered.set()
        assert release.wait(3)
        return inspect()

    monkeypatch.setattr(profile._repository, "inspect", delayed)
    worker = threading.Thread(target=profile.reload)
    callback = threading.Thread(target=lambda: (scanner.sample(), observed.set()))
    worker.start()
    try:
        assert entered.wait(1)
        clock.now = 11
        callback.start()
        assert observed.wait(1), "PSI observation blocked behind filesystem reload"
    finally:
        release.set()
        worker.join(3)
        callback.join(3)
    assert feed.snapshot()["frames"]["preferred"]["sequence"] == 2


def test_wrong_target_cannot_publish_values_or_reuse_previous_frame(live):
    feed, profile, scanner, _ = live
    scanner.endpoint = "udp://192.0.2.26:50536"
    scanner.sample()
    assert feed.snapshot()["failure"] == "endpoint_mismatch"
    scanner.endpoint = TARGET
    assert feed.snapshot()["failure"] == "endpoint_mismatch"
    assert not texts(feed.snapshot())
    profile.reload()
    assert not texts(feed.snapshot())
    scanner.sample()
    assert texts(feed.snapshot())


def test_bad_accepted_state_and_reload_never_publishes_old_content(live, configured):
    feed, profile, scanner, _ = live
    state = configured.state_directory / "accepted-profile.json"
    saved = state.read_bytes()
    state.write_bytes(b"broken")
    with pytest.raises(DisplayProfileStorageError):
        profile.reload()
    assert not texts(feed.snapshot())
    state.write_bytes(saved)
    profile.reload()
    assert not texts(feed.snapshot())
    scanner.sample()
    assert texts(feed.snapshot())


def test_start_close_are_idempotent_and_unsubscribe_all_callbacks(configured):
    scanner = Scanner()
    scanner.connected = True
    profile = DaemonDisplayProfile(configured, lambda: TARGET)
    feed = DaemonDisplayFrames(profile, scanner)
    assert not scanner.events._callbacks
    feed.start()
    feed.start()
    assert len(scanner.events._callbacks["psi"]) == 1
    assert len(scanner.events._callbacks["connection"]) == 1
    callback = scanner.events._callbacks["psi"][0]
    feed.close()
    feed.close()
    callback(info())
    scanner.connect_event(True)
    assert not any(scanner.events._callbacks.values())
    assert feed.snapshot()["frames"]["preferred"]["status"] == "disconnected"
    with pytest.raises(ValueError, match="closed"):
        feed.start()


@pytest.mark.parametrize("stage", ["connection", "connected", "psi"])
def test_start_failure_closes_feed_and_removes_registered_callbacks(configured, stage):
    class BrokenScanner(Scanner):
        def on_connection(self, callback):
            if stage == "connection":
                raise OSError("synthetic subscription failure")
            return super().on_connection(callback)

        @property
        def connected(self):
            if stage == "connected":
                raise OSError("synthetic status failure")
            return True

        def on_psi(self, callback):
            if stage == "psi":
                raise OSError("synthetic observation subscription failure")
            return super().on_psi(callback)

    scanner = BrokenScanner()
    feed = DaemonDisplayFrames(DaemonDisplayProfile(configured, lambda: TARGET), scanner)
    with pytest.raises(OSError, match="synthetic"):
        feed.start()
    assert not any(scanner.events._callbacks.values())
    assert feed.snapshot()["frames"]["preferred"]["status"] == "disconnected"
    with pytest.raises(ValueError, match="closed"):
        feed.start()


@pytest.mark.parametrize("failure", ["disk", "target"])
def test_profile_failure_and_repair_between_frame_reads_still_invalidate(live, configured, failure):
    feed, profile, scanner, _ = live
    assert texts(feed.snapshot())
    if failure == "disk":
        state = configured.state_directory / "accepted-profile.json"
        saved = state.read_bytes()
        state.write_bytes(b"broken")
        with pytest.raises(DisplayProfileStorageError):
            profile.reload()
        state.write_bytes(saved)
    else:
        scanner.endpoint = "udp://192.0.2.26:50536"
        assert profile.snapshot()["failure"] == "endpoint_mismatch"
        scanner.endpoint = TARGET
    profile.reload()
    assert not texts(feed.snapshot())
    scanner.sample()
    assert texts(feed.snapshot())


def test_connection_change_during_initial_property_read_wins(configured):
    class RacyScanner(Scanner):
        @property
        def connected(self):
            self.events.emit("connection", True)
            return False

    scanner = RacyScanner()
    feed = DaemonDisplayFrames(DaemonDisplayProfile(configured, lambda: TARGET), scanner)
    feed.start()
    try:
        scanner.sample()
        assert texts(feed.snapshot())
    finally:
        feed.close()


def test_serializer_independently_clears_forged_stale_values(live):
    feed, profile, _, clock = live
    frame = feed._adapter.frame(profile.frame_context()[0], now=clock.now)
    rendered = project_scanner_display_frame(replace(frame, status=Status.STALE))
    assert all(region["text"] is None for region in rendered["screen"]["regions"])
    assert set(rendered["indicators"].values()) == {None}
    with pytest.raises(ValueError, match="canonical"):
        project_scanner_display_frame(replace(frame, values=frame.values[:-1]))


@pytest.mark.parametrize("text", ["unsafe\x1b[2J", "hidden\u200b", "x" * 257, "\ud800"])
def test_serializer_rejects_control_characters_and_oversized_values(live, text):
    feed, profile, _, clock = live
    frame = feed._adapter.frame(profile.frame_context()[0], now=clock.now)
    values = tuple(
        replace(value, text=text) if value.text is not None else value for value in frame.values
    )
    with pytest.raises(ValueError, match="invalid value"):
        project_scanner_display_frame(replace(frame, values=values))


@pytest.mark.parametrize("age", [-1, float("nan"), float("inf"), 1e16])
def test_serializer_rejects_invalid_ages(live, age):
    feed, profile, _, clock = live
    frame = feed._adapter.frame(profile.frame_context()[0], now=clock.now)
    with pytest.raises(ValueError, match="age"):
        project_scanner_display_frame(replace(frame, age_seconds=age))


def test_current_psi_does_not_invent_missing_accepted_configuration(configured, tmp_path):
    empty_config = replace(configured, state_directory=tmp_path / "empty-state")
    initialize_display_profile_storage(
        empty_config.state_directory, empty_config.binding.endpoint_id
    )
    scanner = Scanner()
    feed = DaemonDisplayFrames(DaemonDisplayProfile(empty_config, lambda: TARGET), scanner)
    feed.start()
    try:
        scanner.connect_event(True)
        scanner.sample()
        result = feed.snapshot()
        assert not texts(result)
        for frame in result["frames"].values():
            assert frame["status"] == "current"
            assert frame["profile_status"] == "unavailable"
            assert frame["screen"] is frame["profile_revision"] is frame["source"] is None
    finally:
        feed.close()


def test_read_only_api_is_conditional_and_refuses_parameters_or_control_dispatch(live):
    feed, _, _, _ = live
    runtime = SimpleNamespace()
    default = DaemonReadOnlyApi(runtime)
    configured = DaemonReadOnlyApi(runtime, display_frames=feed)
    request = DaemonApiRequest("display", Op.DISPLAY_FRAME)
    assert default.handle_payload(request.as_dict()).error.code.value == "unsupported_operation"
    assert (
        Op.DISPLAY_FRAME
        not in default.handle_payload(DaemonApiRequest("hello", Op.HELLO).as_dict()).result[
            "operations"
        ]
    )
    assert (
        Op.DISPLAY_FRAME
        in configured.handle_payload(DaemonApiRequest("hello", Op.HELLO).as_dict()).result[
            "operations"
        ]
    )
    request_line = json.dumps(request.as_dict()) + "\n"
    response = configured.handle_authorized_json_line(
        request_line, allowed_operations=DAEMON_REMOTE_OBSERVE_OPERATIONS
    )
    assert texts(json.loads(response)["result"])
    assert (
        json.loads(
            configured.handle_authorized_json_line(request_line, allowed_operations=(Op.HELLO,))
        )["error"]["code"]
        == "authorization_denied"
    )
    assert configured.handle_control_payload(request.as_dict()).error is not None
    for params in ({"style": "simple"}, {"path": "/secret"}, {"upload": True}):
        assert (
            configured.handle_payload(
                DaemonApiRequest("bad", Op.DISPLAY_FRAME, params).as_dict()
            ).error.code.value
            == "invalid_parameters"
        )
    assert len(response) < 256 * 1024


@pytest.mark.parametrize("enabled", [False, True])
def test_real_unix_to_web_display_only_read_path(live, tmp_path, enabled):
    feed, _, _, _ = live
    api = DaemonReadOnlyApi(SimpleNamespace(), display_frames=feed if enabled else None)
    location = resolve_daemon_socket_location(tmp_path / "s")
    server = DaemonApiServer(DaemonSocketListener(location), api)
    server.start()
    origin, password = "https://scanner.example.test", "display-password-for-tests"
    auth = WebDashboardAuthentication(
        "operator-password-for-tests", origin, display_password=password
    )
    app = create_web_dashboard_app(lambda: DaemonApiClient(location), lan_authentication=auth)
    try:
        with TestClient(app, base_url=origin) as client:
            assert client.get("/api/v1/display-frame").status_code == 401
            assert (
                client.post(
                    "/auth/display/login",
                    data={"password": password},
                    headers={"Origin": origin},
                    follow_redirects=False,
                ).status_code
                == 303
            )
            response = client.get("/api/v1/display-frame")
            assert response.status_code == (200 if enabled else 503)
            if enabled:
                assert texts(response.json()["display"])
                assert response.headers["cache-control"] == "no-store"
                assert client.get("/api/v1/display-frame?path=/secret").status_code == 422
            assert (
                client.post("/api/v1/display-frame", headers={"Origin": origin}).status_code == 403
            )
            assert client.get("/api/v1/home-assistant/scanner-display-profile").status_code == 403
    finally:
        server.stop()


@pytest.mark.parametrize("failure", [False, True])
def test_cli_attaches_feed_only_around_process_run_and_cleans_up(
    configured, tmp_path, monkeypatch, failure
):
    scanner = Scanner()
    scanner.endpoint = "/dev/demo"
    scanner.waterfall_session = None
    manifest = tmp_path / "display.toml"
    manifest.write_text(manifest.read_text().replace(TARGET, scanner.endpoint))
    calls = []
    monkeypatch.setattr(cli, "selected_radio", lambda *a, **kw: scanner)
    monkeypatch.setattr(cli, "DaemonEventStream", lambda *a, **kw: SimpleNamespace(**kw))

    class Process:
        def __init__(self, runtime, **kw):
            self.api = kw["api_server"].api

        def run(self):
            calls.append("run")
            assert len(scanner.events._callbacks["connection"]) == 1
            scanner.connect_event(True)
            scanner.sample()
            assert texts(self.api.display_frames.snapshot())
            if failure:
                raise OSError("synthetic process failure")
            return SimpleNamespace(last_signal=None)

    monkeypatch.setattr(cli, "DaemonProcess", Process)
    paths = cli.resolve_configuration_paths(
        environ={}, home=tmp_path / "home", system_config_dir=tmp_path / "etc"
    )
    result = cli.main(
        ["--port", scanner.endpoint, "daemon", "--scanner-display-profile-config", str(manifest)],
        configuration_paths=paths,
        environ={},
    )
    assert result == (2 if failure else 0)
    assert calls == ["run"]
    assert not any(scanner.events._callbacks.values())
