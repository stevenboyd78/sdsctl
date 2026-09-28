from __future__ import annotations

import asyncio
import wave
from dataclasses import asdict, replace

import pytest
from rich.text import Text
from textual.widgets import Static

from sds200.audio import AudioChunk, AudioStream
from sds200.audio_session import AudioSessionStatus
from sds200.daemon_tui import _radio_state_snapshot
from sds200.state import RadioStateSnapshot, ScannerScreenKind, snapshot_from_scanner_info
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_audio import RecordingPathPolicy, TuiAudioSession
from sds200.tui_details import ScannerDetailsScreen, scanner_details
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport
from .test_tui import _settle_responsive_layout
from .test_tui_audio_workflow import CollectingPlaybackSink, _wait_for_status

XML = """<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Example system"/><Department Name="Example department"/>
<TGID Name="Example channel" TGID="TGID:000123" U_Id="UID:000045"/>
<Property P25Status="unrecognized status" Battery="0"/>
</ScannerInfo>"""


def _state() -> RadioStateSnapshot:
    return snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML))


def _render(state: RadioStateSnapshot, **overrides: object) -> Text:
    args = dict(connected=True, current=True, stale=False, degraded=False)
    args.update(overrides)
    return scanner_details(state, **args)


@pytest.mark.parametrize("remote", [False, True])
def test_scanner_details_preserve_actual_parser_shared_snapshot_and_daemon_fields(remote):
    state = _state()
    if remote:
        state = _radio_state_snapshot(asdict(state))
    text = _render(state).plain
    assert "Talkgroup ID: TGID:000123\n" in text
    assert "Unit ID: UID:000045\n" in text
    assert "P25 status (reported): unrecognized status\n" in text
    assert "Battery (raw): 0\n" in text
    assert "volts" not in text and "%" not in text


@pytest.mark.parametrize("field", ["talkgroup_id", "unit_id", "p25_status"])
@pytest.mark.parametrize("value", [None, "", " "])
def test_scanner_details_omitted_empty_fields_are_unavailable(field, value):
    text = _render(replace(_state(), **{field: value})).plain
    labels = {
        "talkgroup_id": "Talkgroup ID",
        "unit_id": "Unit ID",
        "p25_status": "P25 status (reported)",
    }
    assert f"{labels[field]}: Unavailable\n" in text


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf"), True])
def test_scanner_details_defensively_reject_nonfinite_or_boolean_battery(value):
    assert "Battery (raw): Unavailable\n" in _render(replace(_state(), battery=value)).plain


def test_scanner_details_render_literal_bounded_unicode_without_modifying_snapshot():
    raw = "[red]TGID:0000[/red] Ω\x1b\n\r\t\u202e\u2028" + "界" * 500
    state = replace(_state(), talkgroup_id=raw, unit_id="0")
    text = _render(state)
    line = next(line for line in text.plain.splitlines() if line.startswith("Talkgroup ID:"))
    assert "[red]TGID:0000[/red] Ω??????" in line
    assert line.endswith("…") and Text(line.removeprefix("Talkgroup ID: ")).cell_len <= 128
    assert "Unit ID: 0\n" in text.plain
    assert "\x1b" not in text.plain and "\u202e" not in text.plain
    assert all(span.style == "bold" for span in text.spans)
    assert state.talkgroup_id == raw


@pytest.mark.parametrize(
    "override,health",
    [
        ({"connected": False}, "Disconnected"),
        ({"connected": None}, "Connection status unavailable"),
        ({"current": False}, "Waiting for new scanner data"),
        ({"stale": True}, "Stale scanner data"),
        ({"degraded": True}, "Degraded connection"),
    ],
)
def test_scanner_details_unconfirmed_state_clears_previous_values(override, health):
    text = _render(_state(), **override).plain
    assert text.startswith(health + " — values cleared")
    assert text.count(": Unavailable\n") == 6
    assert "000123" not in text and "000045" not in text


@pytest.mark.parametrize("size", [(64, 24), (100, 30), (160, 45)])
@pytest.mark.parametrize("palette", [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])
def test_scanner_details_overlay_keeps_dashboard_geometry_live_updates_and_actions(size, palette):
    async def exercise():
        app = ScannerTuiApp(
            ScannerIdentity("usb://example", "SDS200", "Example"), _state(), palette=palette
        )
        async with app.run_test(size=size) as pilot:
            await _settle_responsive_layout(app, pilot)
            dashboard = app.screen
            regions = {
                name: app.query_one("#" + name).region
                for name in ("connection", "identity", "channel", "state")
            }
            commands = list(app.get_system_commands(dashboard))
            assert any(command.title == "Scanner details" for command in commands)
            await pilot.press("x")
            await pilot.pause()
            drawer = app.screen
            assert isinstance(drawer, ScannerDetailsScreen)
            assert drawer.has_class("light") == (palette is DEFAULT_LIGHT_THEME)
            content = drawer.query_one("#scanner-details-content", Static)
            assert isinstance(content.content, Text) and "TGID:000123" in content.content.plain
            scroll = drawer.query_one("#scanner-details-scroll")
            assert scroll.region.right <= drawer.content_region.right
            assert scroll.region.bottom <= drawer.content_region.bottom
            assert scroll.max_scroll_x == 0
            assert not app.check_action("hold_channel", ())
            assert not app.check_action("reconnect", ())
            assert not app.check_action("toggle_audio_recording", ())
            assert not app.check_action("mimic", ())
            assert {command.title for command in app.get_system_commands(drawer)} == {
                "Back to dashboard",
                "Quit",
            }
            await pilot.press("h", "c", "r", "a", "m", "g")
            assert app.screen is drawer
            assert not app._key_help_visible
            await pilot.press("ctrl+p")
            assert app.screen is not drawer
            await pilot.press("h", "r", "c", "m")
            app.update_snapshot(replace(_state(), unit_id="UID:000088"), connected=True)
            await pilot.press("escape")
            assert app.screen is drawer and "UID:000088" in content.content.plain
            for kind in (
                ScannerScreenKind.SEARCH,
                ScannerScreenKind.WEATHER,
                ScannerScreenKind.TONE_OUT,
                ScannerScreenKind.UNKNOWN,
            ):
                app.update_snapshot(RadioStateSnapshot(screen_kind=kind), connected=True)
                assert "Talkgroup ID: Unavailable" in content.content.plain
                assert "000123" not in content.content.plain
            app.update_snapshot(replace(_state(), unit_id="UID:000099"), connected=True)
            assert "UID:000099" in content.content.plain
            app._apply_connection(False)
            assert "Disconnected" in content.content.plain and "000099" not in content.content.plain
            app._apply_connection(True)
            assert "Waiting for new scanner data" in content.content.plain
            assert "000099" not in content.content.plain
            app.update_snapshot(_state(), connected=True)
            assert "TGID:000123" in content.content.plain
            await pilot.press("escape")
            await _settle_responsive_layout(app, pilot)
            assert app.screen is dashboard and app._details_screen is None
            assert {name: app.query_one("#" + name).region for name in regions} == regions
            await pilot.press("x")
            await pilot.pause()
            await pilot.resize_terminal(80, 24)
            await pilot.press("x")
            await _settle_responsive_layout(app, pilot)
            assert app.screen is dashboard
            assert app.query_one("#connection").region.right <= dashboard.content_region.right

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(100, 30), (160, 45)])
def test_scanner_details_does_not_interrupt_playback_recording_or_normal_quit(tmp_path, size):
    async def exercise():
        transport, playback = FakeAudioTransport(), CollectingPlaybackSink()
        session = TuiAudioSession(
            AudioStream(transport), RecordingPathPolicy(directory=tmp_path), playback_sink=playback
        )
        app = ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "Example"),
            _state(),
            audio_session=session,
        )
        async with app.run_test(size=size) as pilot:
            for _ in range(200):
                if session.open and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert session.open and not app._audio_pending
            await pilot.press("a")
            for _ in range(200):
                if session.live_playback_active and not app._audio_pending:
                    break
                await asyncio.sleep(0.01)
            assert session.live_playback_active and playback.running
            await pilot.press("r")
            await _wait_for_status(session, AudioSessionStatus.RECORDING)
            await pilot.press("x")
            await pilot.pause()
            assert isinstance(app.screen, ScannerDetailsScreen)
            transport.feed(AudioChunk(bytes((0xFF, 0x80))))
            await pilot.press("a", "r", "l", "space", "enter")
            app._poll_audio_state()
            assert session.status is AudioSessionStatus.RECORDING
            assert session.live_playback_active and playback.stop_calls == 0
            transport.feed(AudioChunk(bytes((0x00, 0x7F))))
            await pilot.press("escape")
            await _settle_responsive_layout(app, pilot)
            assert session.status is AudioSessionStatus.RECORDING
            assert app.query_one("#audio").display
            assert app.query_one("#body").max_scroll_y == 0
            await pilot.press("x", "q")
        assert not session.open and not transport.running
        assert not app.audio_thread_alive and playback.stop_calls == 1
        assert len(session.recordings) == 1
        with wave.open(str(session.recordings[0].path), "rb") as recorded:
            assert recorded.getnframes() == 4

    asyncio.run(exercise())


def test_scanner_details_opening_race_long_values_scroll_and_return_to_help():
    async def exercise():
        app = ScannerTuiApp(ScannerIdentity("usb://example", "SDS200", "Example"), _state())
        async with app.run_test(size=(64, 24)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            assert app.key_help_visible
            app.action_scanner_details()
            # Before the pending screen mounts, a newer snapshot must win.
            state = replace(_state(), channel="channel " * 30, unit_id="current " * 30)
            app.update_snapshot(state, connected=True)
            await pilot.pause()
            drawer = app.screen
            content = drawer.query_one("#scanner-details-content", Static)
            assert "current current" in content.content.plain
            assert "UID:000045" not in content.content.plain
            scroll = drawer.query_one("#scanner-details-scroll")
            assert scroll.max_scroll_x == 0
            await pilot.press("end")
            await pilot.pause()
            assert scroll.scroll_y == scroll.max_scroll_y
            app._stale = True
            app._refresh_view()
            assert content.content.plain.startswith("Stale scanner data")
            assert "current current" not in content.content.plain
            await pilot.press("escape")
            await _settle_responsive_layout(app, pilot)
            assert app.key_help_visible and app.query_one("#keys").display
            assert (
                "X        Open read-only scanner details" in app.query_one("#keys", Static).content
            )

    asyncio.run(exercise())
