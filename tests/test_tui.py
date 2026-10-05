from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from math import nextafter
from pathlib import Path

import pytest
from rich.text import Text
from textual.pilot import Pilot
from textual.widgets import Static

from sds200 import __version__
from sds200.audio import AudioStream
from sds200.state import snapshot_from_scanner_info
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_audio import RecordingPathPolicy, TuiAudioSession
from sds200.tui_logging import TuiLogBuffer
from sds200.xml_protocol import ScannerInfoParser

from .fakes import FakeAudioTransport

FIXTURES = Path(__file__).parent / "fixtures" / "scanner_info"

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Utah Communications Authority (P25)" />
<Department Name="Harris Dynamic Patch - Northern Utah" />
<Site Name="Utah County Simulcast" Mod="NFM" />
<TGID Name="Patch 65132" TGID="TGID:65132" SvcType="Interop" U_Id="UID:9190014" />
<SiteFrequency Freq="769.431250MHz" />
<Property VOL="10" SQL="2" Sig="5" Rssi="-42" Rec="On" Mute="Unmute" />
</ScannerInfo>"""


def _app(
    log_buffer: TuiLogBuffer | None = None,
    *,
    audio_session: TuiAudioSession | None = None,
) -> ScannerTuiApp:
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="udp://192.168.0.251:50536",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        log_buffer=log_buffer,
        audio_session=audio_session,
        palette=DEFAULT_DARK_THEME,
    )


def _fixture_app(name: str) -> ScannerTuiApp:
    xml = (FIXTURES / name).read_text(encoding="utf-8")
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="udp://192.168.0.251:50536",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", xml)),
        palette=DEFAULT_DARK_THEME,
    )


def _plain(widget: Static) -> str:
    content = widget.content
    assert isinstance(content, (str, Text))
    return content if isinstance(content, str) else content.plain


async def _settle_responsive_layout(app: ScannerTuiApp, pilot: Pilot[None]) -> None:
    # on_resize queues _refresh_responsive_view AFTER a refresh; that callback
    # can itself invalidate panel geometry and remove a temporary scrollbar.
    # Pilot.pause drains the queue as it stood on entry, not future redraws.
    # Wait through the public refresh barrier and its resulting layout before
    # comparing widget regions. Keep every size/overflow assertion unchanged.
    await pilot.pause()
    refreshed = asyncio.Event()
    assert app.call_after_refresh(refreshed.set)
    await asyncio.wait_for(refreshed.wait(), timeout=5)
    await pilot.pause()


def test_tui_shell_renders_identity_and_semantic_snapshot() -> None:
    async def exercise() -> None:
        app = _app()
        async with app.run_test(size=(80, 32)):
            assert "CONNECTED" in _plain(app.query_one("#connection", Static))
            assert "SDS200" in _plain(app.query_one("#identity", Static))
            assert "Utah Communications Authority" in _plain(app.query_one("#system", Static))
            assert "Patch 65132" in _plain(app.query_one("#channel", Static))
            state = _plain(app.query_one("#state", Static))
            assert "RECEIVING" in state
            assert "STRONG (5)" in state
            assert "Scanner recording: RECORDING" in state
            assert "UNMUTED" in state
            assert not app.audio_controls_available
            assert app.query_one_optional("#audio", Static) is None

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(64, 24), (100, 30), (120, 30), (160, 45)])
@pytest.mark.parametrize("model", ["SDS100", "SDS150", "SDS200"])
def test_tui_header_identifies_app_and_scanner_panel_retains_hardware(
    size: tuple[int, int], model: str
) -> None:
    async def exercise() -> None:
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="sdsctl-remote-daemon",
                model=model,
                firmware="Version 1.26.01",
                connection_target="192.0.2.25:50443",
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert app.title == f"sdsctl v{__version__}"
            assert app.sub_title == ""
            identity = app.query_one("#identity", Static)
            assert identity.display
            assert identity.border_title == "Scanner"
            assert f"Model: {model}" in _plain(identity)
            assert "Firmware: Version 1.26.01" in _plain(identity)
            assert "Target: 192.0.2.25:50443" in _plain(app.query_one("#connection", Static))
            assert ("\n" not in _plain(identity)) == (size[1] < 32)

    asyncio.run(exercise())


def test_pi_scanner_identity_fits_with_audio_and_logs(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = _app(
            audio_session=TuiAudioSession(
                AudioStream(FakeAudioTransport()),
                RecordingPathPolicy(directory=tmp_path),
            )
        )
        async with app.run_test(size=(100, 30)) as pilot:
            await _settle_responsive_layout(app, pilot)
            body = app.query_one("#body")
            identity = app.query_one("#identity", Static)
            for show_logs in (False, True, False):
                if app.logs_visible != show_logs:
                    await pilot.press("g")
                    await _settle_responsive_layout(app, pilot)
                lower_panel = app.query_one("#logs" if show_logs else "#audio")
                assert identity.display
                assert identity.region.height == 3
                assert identity.region.width == body.scrollable_content_region.width
                assert identity.region.y == lower_panel.region.bottom
                assert identity.region.bottom <= body.content_region.bottom
                assert body.max_scroll_y == 0

            await pilot.resize_terminal(160, 45)
            await _settle_responsive_layout(app, pilot)
            assert "\n" in _plain(identity)
            assert identity.region.y == app.query_one("#connection").region.y
            await pilot.resize_terminal(100, 30)
            await _settle_responsive_layout(app, pilot)
            assert "\n" not in _plain(identity)
            assert identity.region.bottom <= body.content_region.bottom
            assert body.max_scroll_y == 0

    asyncio.run(exercise())


def test_tui_connection_panel_renders_optional_remote_target() -> None:
    async def exercise() -> None:
        direct_app = _app()
        async with direct_app.run_test(size=(100, 30)):
            direct_connection = _plain(direct_app.query_one("#connection", Static))
            assert "Endpoint: udp://192.168.0.251:50536" in direct_connection
            assert "Target:" not in direct_connection
            assert "Daemon:" not in direct_connection
            assert "Link for " not in direct_connection

        remote_app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="sdsctl-remote-daemon",
                model="SDS200",
                firmware="Version 1.26.01",
                connection_target="192.168.0.18:50443",
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            palette=DEFAULT_DARK_THEME,
        )
        async with remote_app.run_test(size=(100, 30)) as pilot:
            remote_connection = _plain(remote_app.query_one("#connection", Static))
            assert "Endpoint: sdsctl-remote-daemon" in remote_connection
            assert "Target: 192.168.0.18:50443" in remote_connection
            assert remote_app.screen.has_class("-connection-target")

            await pilot.resize_terminal(120, 40)
            await pilot.pause()
            assert remote_app.query_one("#connection", Static).region.height >= 5

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(100, 30), (160, 45)])
@pytest.mark.parametrize("palette", [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])
@pytest.mark.parametrize("audio", [False, True])
def test_daemon_version_remains_visible_on_both_pi_layouts(tmp_path, size, palette, audio):
    async def exercise() -> None:
        value = ["99.1.2"]
        clock = [100.0]
        link_start: list[float | None] = [None]
        app = ScannerTuiApp(
            ScannerIdentity(
                "sdsctl-remote-daemon", "SDS200", "Version 1.26.01", "192.0.2.18:50443"
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            daemon_version_source=lambda: value[0],
            daemon_link_since_source=lambda: link_start[0],
            audio_session=(
                TuiAudioSession(
                    AudioStream(FakeAudioTransport()), RecordingPathPolicy(directory=tmp_path)
                )
                if audio
                else None
            ),
            palette=palette,
            clock=lambda: clock[0],
        )
        async with app.run_test(size=size) as pilot:
            await _settle_responsive_layout(app, pilot)
            assert "Daemon: 99.1.2" in _plain(app.query_one("#connection", Static))
            assert "Link for Unavailable" in _plain(
                app.query_one("#connection", Static)
            )
            link_start[0] = 40.0
            app._refresh_view()
            await _settle_responsive_layout(app, pilot)
            assert "Link for 00:01:00" in _plain(
                app.query_one("#connection", Static)
            )
            for reported, expected in [
                ("99.1.2", "99.1.2"),
                (None, "Unavailable"),
                ("[bold]99.1.2[/bold]", "Unavailable"),
                ("99.1.2\x1b[31m", "Unavailable"),
                ("99.2.3rc1", "99.2.3rc1"),
            ]:
                value[0] = reported
                app._refresh_view()
                await _settle_responsive_layout(app, pilot)
                connection = app.query_one("#connection", Static)
                text = _plain(connection)
                assert f"Daemon: {expected}" in text
                assert "Link for 00:01:00" in text
                assert "Target: 192.0.2.18:50443" in text
                daemon_row = next(line for line in text.splitlines() if line.startswith("Daemon:"))
                if size == (100, 30):
                    assert daemon_row == f"Daemon: {expected} | Link for 00:01:00"
                    assert "sdsctl-remote-daemon" not in daemon_row
                    assert f"{expected} | s." not in daemon_row
                else:
                    assert f"Daemon: {expected} | sdsc" in daemon_row
                assert connection.content_region.height >= len(text.splitlines())
                assert all(
                    len(line) <= connection.content_region.width for line in text.splitlines()
                )
                assert app.query_one("#body").max_scroll_y == 0
                assert (
                    app.query_one("#identity").region.bottom <= app.query_one("#body").region.bottom
                )
                drawer = app._mimic_runtime().plain
                assert f"Application: sdsctl v{__version__}" in drawer
                assert f"Daemon: {expected}" in drawer
                assert "Daemon event link for: 00:01:00" in drawer
                assert "Firmware: Version 1.26.01" in drawer
                assert app.title == f"sdsctl v{__version__}"
            resized = (160, 45) if size == (100, 30) else (100, 30)
            await pilot.resize_terminal(*resized)
            await _settle_responsive_layout(app, pilot)
            resized_text = _plain(app.query_one("#connection", Static))
            assert "Daemon: 99.2.3rc1" in resized_text
            resized_daemon_row = next(
                line for line in resized_text.splitlines() if line.startswith("Daemon:")
            )
            if resized == (100, 30):
                assert resized_daemon_row == "Daemon: 99.2.3rc1 | Link for 00:01:00"
                assert "sdsctl-remote-daemon" not in resized_daemon_row
            else:
                assert "Daemon: 99.2.3rc1 | sdsc" in resized_daemon_row
            assert app.query_one("#body").max_scroll_y == 0
            # Long but valid endpoint metadata cannot create a hidden row.
            # The full token remains accessible in the runtime drawer.
            value[0] = "99.1.2+" + "a" * 57
            app._refresh_view()
            await _settle_responsive_layout(app, pilot)
            connection = app.query_one("#connection", Static)
            text = _plain(connection)
            assert "Daemon: 99.1.2+" in text
            assert "…" in text
            assert f"Daemon: {value[0]}" in app._mimic_runtime().plain
            assert all(len(line) <= connection.content_region.width for line in text.splitlines())
            assert connection.content_region.height >= len(text.splitlines())
            assert "Target: 192.0.2.18:50443" in text
            assert app.query_one("#body").max_scroll_y == 0

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0.0, "00:00:00"),
        (59.999, "00:00:59"),
        (60.0, "00:01:00"),
        (3_599.999, "00:59:59"),
        (3_600.0, "01:00:00"),
        (86_399.999, "23:59:59"),
        (86_400.0, "1d 00:00:00"),
        (183_845.0, "2d 03:04:05"),
        (366 * 86_400 + 1.0, "366d 00:00:01"),
    ],
)
def test_daemon_event_link_duration_uses_exact_monotonic_boundaries(
    elapsed: float,
    expected: str,
) -> None:
    app = ScannerTuiApp(
        ScannerIdentity(
            endpoint="sdsctl-remote-daemon",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        daemon_version_source=lambda: "99.1.2",
        daemon_link_since_source=lambda: 10.0,
        clock=lambda: 10.0 + elapsed,
    )

    assert app._daemon_link_duration_text() == expected


def test_daemon_event_link_duration_ignores_wall_clock_and_dst_changes() -> None:
    monotonic_clock = [70.0]
    wall_clock = [datetime.fromisoformat("2026-11-01T01:59:59-06:00")]
    app = ScannerTuiApp(
        ScannerIdentity("daemon", "SDS200", "Version 1.26.01"),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        daemon_link_since_source=lambda: 10.0,
        clock=lambda: monotonic_clock[0],
        now=lambda: wall_clock[0],
    )

    assert app._daemon_link_duration_text() == "00:01:00"
    wall_clock[0] = datetime.fromisoformat("2026-11-01T01:00:00-07:00")
    assert app._daemon_link_duration_text() == "00:01:00"
    monotonic_clock[0] = 71.0
    assert app._daemon_link_duration_text() == "00:01:01"


@pytest.mark.parametrize(
    ("seconds", "previous"),
    [(60, "00:00:59"), (3_600, "00:59:59"), (86_400, "23:59:59")],
)
def test_daemon_event_link_uses_no_tolerance_at_float_boundaries(
    seconds: int,
    previous: str,
) -> None:
    started_at = 1_000_000.1
    observed_at = [started_at + seconds]
    app = ScannerTuiApp(
        ScannerIdentity("daemon", "SDS200", "Version 1.26.01"),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        daemon_link_since_source=lambda: started_at,
        clock=lambda: observed_at[0],
    )

    exact = "1d 00:00:00" if seconds == 86_400 else (
        "01:00:00" if seconds == 3_600 else "00:01:00"
    )
    assert app._daemon_link_duration_text() == exact
    observed_at[0] = nextafter(started_at + seconds, float("-inf"))
    assert app._daemon_link_duration_text() == previous


@pytest.mark.parametrize(
    "started_at",
    [None, True, "10", float("nan"), float("inf"), 11.0],
)
def test_daemon_event_link_duration_is_explicitly_unavailable_for_bad_source(
    started_at: object,
) -> None:
    app = ScannerTuiApp(
        ScannerIdentity("daemon", "SDS200", "Version 1.26.01"),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        daemon_link_since_source=lambda: started_at,  # type: ignore[return-value]
        clock=lambda: 10.0,
    )

    assert app._daemon_link_duration_text() == "Unavailable"


def test_tui_renders_mode_aware_quick_search_and_close_call_details() -> None:
    async def exercise() -> None:
        cases = (
            (
                "synthetic-quick-search.xml",
                (
                    "Mode: Quick Search Hold",
                    "V_Screen: quick_search",
                    "State node: SrchFrequency",
                ),
                (
                    "Search frequency: 154.280000MHz",
                    "Modulation: NFM",
                    "Hold: ON",
                    "Signal: GOOD (3)",
                    "RSSI: -82",
                    "Detected tone / code: CTCSS 123.0Hz",
                ),
            ),
            (
                "synthetic-close-call-searching.xml",
                (
                    "Mode: Close Call Only",
                    "V_Screen: cc_searching",
                    "State node: -",
                ),
                (
                    "Close Call frequency: -",
                    "Modulation: -",
                    "Hold: -",
                    "Detected tone / code: -",
                ),
            ),
            (
                "synthetic-close-call.xml",
                (
                    "Mode: Close Call Only",
                    "V_Screen: close_call",
                    "State node: SrchFrequency",
                ),
                (
                    "Close Call frequency: 155.752500MHz",
                    "Modulation: NFM",
                    "Hold: OFF",
                    "Signal: STRONG (4)",
                    "RSSI: -71",
                    "Detected tone / code: NAC 293h",
                ),
            ),
            (
                "synthetic-close-call-hits.xml",
                (
                    "Mode: Close Call",
                    "V_Screen: cchits_with_scan",
                    "State node: CcHitsChannel",
                ),
                (
                    "Close Call hit: Synthetic Close Call Hit",
                    "Frequency: 155.752500MHz",
                    "Modulation: NFM",
                    "Hold: OFF",
                    "Signal: STRONG (4)",
                    "RSSI: -71",
                    "Detected tone / code: NAC 293h",
                ),
            ),
        )

        for fixture_name, system_values, channel_values in cases:
            app = _fixture_app(fixture_name)
            async with app.run_test(size=(80, 36)):
                system_widget = app.query_one("#system", Static)
                channel_widget = app.query_one("#channel", Static)
                system = _plain(system_widget)
                channel = _plain(channel_widget)

                assert system_widget.border_title == "Screen Mode"
                assert channel_widget.border_title == (
                    "Quick Search" if fixture_name == "synthetic-quick-search.xml" else "Close Call"
                )
                for value in system_values:
                    assert value in system
                for value in channel_values:
                    assert value in channel

    asyncio.run(exercise())


def test_tui_panels_have_descriptive_border_titles() -> None:
    async def exercise() -> None:
        app = _app()

        async with app.run_test(size=(120, 40)):
            expected = {
                "#keys": "Keyboard Reference",
                "#connection": "Connection",
                "#identity": "Scanner",
                "#system": "System / Site",
                "#channel": "Channel",
                "#state": "Scanner State",
                "#status": "Live PSI / Controls",
                "#logs": "Operational Logs",
            }

            for selector, title in expected.items():
                assert app.query_one(selector, Static).border_title == title

    asyncio.run(exercise())


def test_tui_theme_binding_switches_semantic_palettes() -> None:
    async def exercise() -> None:
        app = _app()
        async with app.run_test(size=(80, 32)) as pilot:
            assert app.palette is DEFAULT_DARK_THEME
            await pilot.press("t")
            await pilot.pause()
            assert app.palette is DEFAULT_LIGHT_THEME

    asyncio.run(exercise())


def test_tui_bindings_include_clean_quit() -> None:
    bindings = {(binding.key, binding.action) for binding in ScannerTuiApp.BINDINGS}
    assert ("q", "quit") in bindings
    assert ("t", "toggle_theme") in bindings
    assert ("c", "reconnect") in bindings
    assert ("ctrl+p", "command_palette") in bindings
    palette_binding = next(
        binding for binding in ScannerTuiApp.BINDINGS if binding.action == "command_palette"
    )
    assert palette_binding.description == "Command Palette"
    assert palette_binding.key_display == "^p"
    assert palette_binding.show
    assert ("question_mark", "toggle_key_help") in bindings
    assert ("g", "toggle_logs") in bindings
    assert ("h", "hold_channel") in bindings
    assert ("s", "hold_system") in bindings
    assert ("d", "hold_department") in bindings
    assert ("i", "hold_site") in bindings
    assert ("n", "next_channel") in bindings
    assert ("p", "previous_channel") in bindings
    assert ("plus", "volume_up") in bindings
    assert ("minus", "volume_down") in bindings
    assert ("right_square_bracket", "squelch_up") in bindings
    assert ("left_square_bracket", "squelch_down") in bindings


def test_tui_responsive_breakpoints_and_key_help() -> None:
    async def exercise() -> None:
        compact = _app()
        async with compact.run_test(size=(64, 20)) as pilot:
            await pilot.pause()
            assert compact.screen.has_class("-compact")
            assert compact.screen.has_class("-short")
            assert not compact.key_help_visible
            assert not compact.query_one("#footer").display

            compact_footer = compact.query_one("#compact-footer", Static)
            assert compact_footer.display
            assert _plain(compact_footer) == "Q Quit | C Reconnect | G Logs | ? Keys"
            assert compact_footer.region.bottom == compact.screen.region.bottom

            await pilot.press("question_mark")
            await pilot.pause()
            assert compact.key_help_visible
            assert compact.screen.has_class("show-keys")
            keys = _plain(compact.query_one("#keys", Static))
            assert "Hold current channel" in keys
            assert "Hold current system / department" in keys
            assert "Hold current site" in keys
            assert "Raise / lower squelch" in keys
            assert "Reconnect scanner" in keys
            assert "Show or hide operational logs" in keys
            assert "Command Palette" in keys
            assert "live scanner playback" not in keys
            assert "audio recording" not in keys

            await pilot.press("question_mark")
            await pilot.pause()
            assert not compact.key_help_visible
            assert not compact.screen.has_class("show-keys")

        pi_screen = _app()
        async with pi_screen.run_test(size=(90, 28)) as pilot:
            await pilot.pause()
            assert pi_screen.screen.has_class("-standard")
            assert pi_screen.screen.has_class("-short")
            assert not pi_screen.screen.has_class("-compact")
            assert not pi_screen.query_one("#footer").display
            assert pi_screen.query_one("#compact-footer", Static).display

            body = pi_screen.query_one("#body")
            connection = pi_screen.query_one("#connection")
            system = pi_screen.query_one("#system")
            channel = pi_screen.query_one("#channel")
            state = pi_screen.query_one("#state")
            status = pi_screen.query_one("#status")
            logs = pi_screen.query_one("#logs")

            assert connection.region.y == body.region.y
            identity = pi_screen.query_one("#identity")
            assert identity.region.y == connection.region.bottom
            assert system.region.y == identity.region.bottom
            assert channel.region.y == system.region.bottom
            assert state.region.y == channel.region.bottom
            assert pi_screen.query_one_optional("#audio", Static) is None
            assert status.region.y == state.region.bottom
            assert status.region.bottom <= body.region.bottom
            assert pi_screen.logs_visible
            assert logs.display
            assert logs.region.y == status.region.bottom
            assert logs.region.bottom <= body.region.bottom
            assert connection.styles.border_top[0] == ""

        physical_pi = _app()
        async with physical_pi.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert physical_pi.screen.has_class("-split")
            assert physical_pi.screen.has_class("-short")
            assert physical_pi.screen.has_class("-no-audio")
            assert physical_pi.query_one_optional("#audio", Static) is None
            assert not physical_pi.check_action("toggle_audio_playback", ())
            assert not physical_pi.check_action("toggle_audio_recording", ())
            assert not physical_pi.check_action("toggle_recording_library", ())

            body = physical_pi.query_one("#body")
            connection = physical_pi.query_one("#connection")
            system = physical_pi.query_one("#system")
            channel = physical_pi.query_one("#channel")
            state = physical_pi.query_one("#state")
            status = physical_pi.query_one("#status")
            logs = physical_pi.query_one("#logs")

            assert physical_pi.screen.has_class("-pi-dashboard")
            assert not physical_pi.logs_visible
            assert not logs.display
            assert connection.region.y == channel.region.y == body.region.y
            assert connection.region.right < channel.region.x
            assert system.region.y == max(connection.region.bottom, channel.region.bottom)
            assert system.region.x == body.region.x
            assert system.region.width == body.region.width
            assert state.region.y == status.region.y == system.region.bottom
            assert state.region.right < status.region.x
            assert system.styles.text_wrap == "nowrap"
            assert system.styles.text_overflow == "ellipsis"

            hierarchy = _plain(system)
            channel_details = _plain(channel)
            assert "Channel: Patch 65132" in hierarchy
            assert "Channel:" not in channel_details
            assert "Frequency: 769.431250MHz" in channel_details

            for panel, title in (
                (connection, "Connection"),
                (system, "System / Site / Channel"),
                (channel, "Channel Details"),
                (state, "Scanner State"),
                (status, "Live PSI / Controls"),
                (logs, "Operational Logs"),
            ):
                assert panel.styles.border_top[0] == "round"
                assert panel.border_title == title

            system_height = system.region.height
            lower_row_y = state.region.y
            physical_pi.update_snapshot(
                replace(
                    physical_pi._snapshot,
                    system=(
                        "Church of Jesus Christ of Latter Day Saints "
                        "with an intentionally extended display name"
                    ),
                ),
                connected=True,
            )
            await pilot.pause()
            assert system.region.height == system_height
            assert state.region.y == lower_row_y
            assert physical_pi.query_one("#body").max_scroll_y == 0

            await pilot.press("g")
            await pilot.pause()
            assert physical_pi.logs_visible
            assert logs.display
            assert logs.region.y == max(state.region.bottom, status.region.bottom)
            assert logs.region.x == body.region.x
            assert logs.region.width == body.region.width
            assert logs.region.bottom <= body.region.bottom

            await pilot.press("question_mark")
            await pilot.pause()
            keys = physical_pi.query_one("#keys")
            assert physical_pi.key_help_visible
            assert not physical_pi.logs_visible
            assert not logs.display
            assert keys.region.x == logs.region.x
            assert keys.region.width >= body.region.width - 2
            await pilot.press("question_mark")
            await pilot.pause()

        standard = _app()
        async with standard.run_test(size=(90, 32)) as pilot:
            await pilot.pause()
            assert standard.screen.has_class("-standard")
            assert standard.screen.has_class("-tall")
            assert standard.query_one("#footer").display
            assert not standard.query_one("#compact-footer", Static).display

            body = standard.query_one("#body")
            connection = standard.query_one("#connection")
            system = standard.query_one("#system")

            assert connection.region.y > body.region.y
            assert system.region.y > connection.region.bottom

        tall_at_pi_width = _app()
        async with tall_at_pi_width.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            assert tall_at_pi_width.screen.has_class("-split")
            assert tall_at_pi_width.screen.has_class("-tall")

            connection = tall_at_pi_width.query_one("#connection")
            system = tall_at_pi_width.query_one("#system")
            assert system.region.y > connection.region.bottom

        wide = _app()
        async with wide.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert wide.screen.has_class("-wide")
            assert wide.screen.has_class("-tall")
            assert wide.query_one("#footer").display
            assert not wide.query_one("#compact-footer", Static).display

    asyncio.run(exercise())


def test_tui_restores_standard_panel_order_after_pi_layout_resize() -> None:
    async def exercise() -> None:
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            body = app.query_one("#body")
            channel = app.query_one("#channel", Static)
            system = app.query_one("#system", Static)

            assert app.screen.has_class("-pi-dashboard")
            assert [child.id for child in body.children] == [
                "keys",
                "connection",
                "channel",
                "system",
                "state",
                "status",
                "logs",
                "identity",
            ]
            assert channel.border_title == "Channel Details"
            assert "Channel: Patch 65132" in _plain(system)
            assert "Channel:" not in _plain(channel)

            await pilot.resize_terminal(120, 40)
            await pilot.pause()

            assert not app.screen.has_class("-pi-dashboard")
            assert app.logs_visible
            assert [child.id for child in body.children] == [
                "keys",
                "connection",
                "identity",
                "system",
                "channel",
                "state",
                "status",
                "logs",
            ]
            assert system.border_title == "System / Site"
            assert channel.border_title == "Channel"
            assert "Channel: Patch 65132" not in _plain(system)
            assert "Channel: Patch 65132" in _plain(channel)

            await pilot.resize_terminal(100, 30)
            await pilot.pause()

            assert app.screen.has_class("-pi-dashboard")
            assert not app.logs_visible
            assert channel.border_title == "Channel Details"
            assert "Channel: Patch 65132" in _plain(system)
            assert "Channel:" not in _plain(channel)
            assert body.max_scroll_y == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(120, 40), (160, 45), (240, 67)])
@pytest.mark.parametrize("with_audio", [False, True])
@pytest.mark.parametrize("resized", [False, True])
def test_wide_tui_packs_psi_beside_audio_and_logs_across_full_width(
    tmp_path: Path, size: tuple[int, int], with_audio: bool, resized: bool
) -> None:
    async def exercise() -> None:
        session = (
            TuiAudioSession(
                AudioStream(FakeAudioTransport()),
                RecordingPathPolicy(directory=tmp_path),
            )
            if with_audio
            else None
        )
        app = _app(audio_session=session)
        async with app.run_test(size=(80, 24) if resized else size) as pilot:
            if resized:
                await pilot.resize_terminal(*size)
            await _settle_responsive_layout(app, pilot)
            body = app.query_one("#body")
            state = app.query_one("#state")
            status = app.query_one("#status")
            logs = app.query_one("#logs")
            assert logs.region.x == body.scrollable_content_region.x
            assert logs.region.width == body.scrollable_content_region.width
            assert logs.region.y == status.region.bottom + 1
            assert logs.region.bottom <= body.content_region.bottom
            assert body.max_scroll_y == 0
            if with_audio:
                audio = app.query_one("#audio")
                assert audio.styles.row_span == 2
                assert audio.region.y == state.region.y
                assert status.region.x == state.region.x
                assert status.region.y == state.region.bottom + 1
                assert logs.region.y > audio.region.bottom
            else:
                assert status.region.y == state.region.y

    asyncio.run(exercise())


@pytest.mark.parametrize("with_audio", [False, True])
def test_wide_tui_keyboard_reference_and_logs_toggle_independently(
    tmp_path: Path, with_audio: bool
) -> None:
    async def exercise() -> None:
        buffer = TuiLogBuffer(limit=3)
        session = (
            TuiAudioSession(
                AudioStream(FakeAudioTransport()),
                RecordingPathPolicy(directory=tmp_path),
            )
            if with_audio
            else None
        )
        app = _app(buffer, audio_session=session)
        async with app.run_test(size=(160, 45)) as pilot:
            body = app.query_one("#body")
            keys = app.query_one("#keys")
            logs = app.query_one("#logs", Static)
            await pilot.press("question_mark")
            await pilot.pause()
            assert app.key_help_visible and keys.display
            assert app.logs_visible and logs.display
            assert logs.region.x == body.scrollable_content_region.x
            assert logs.region.width == body.scrollable_content_region.width
            assert keys.region.width == logs.region.width
            buffer.append("2026-09-04 WARNING sds200.test: both panels open")
            app._poll_log_buffer()
            assert "both panels open" in _plain(logs)

            await pilot.press("question_mark")
            assert not app.key_help_visible
            assert app.logs_visible
            await pilot.pause()
            assert body.max_scroll_y == 0
            await pilot.press("g", "question_mark")
            assert app.key_help_visible
            assert not app.logs_visible
            await pilot.press("g")
            assert app.key_help_visible
            assert app.logs_visible
            await pilot.press("g")
            assert app.key_help_visible
            assert not app.logs_visible

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(100, 30), (120, 30), (100, 40)])
def test_wide_tui_resize_restores_small_screen_drawer_exclusivity(
    size: tuple[int, int],
) -> None:
    async def exercise() -> None:
        app = _app()
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.press("question_mark")
            assert app.key_help_visible and app.logs_visible
            await pilot.resize_terminal(*size)
            await pilot.pause()
            assert app.key_help_visible
            assert not app.logs_visible
            await pilot.press("g")
            assert app.logs_visible
            assert not app.key_help_visible

    asyncio.run(exercise())


def test_tui_log_panel_is_visible_by_default_and_retains_hidden_records() -> None:
    async def exercise() -> None:
        buffer = TuiLogBuffer(limit=3)
        buffer.append("2026-07-30 WARNING sds200.test: first warning")
        app = _app(buffer)

        async with app.run_test(size=(120, 40)) as pilot:
            logs = app.query_one("#logs", Static)
            assert app.logs_visible
            assert not app.screen.has_class("hide-logs")
            assert "first warning" in _plain(logs)

            await pilot.press("g")
            await pilot.pause()
            assert not app.logs_visible
            assert app.screen.has_class("hide-logs")

            status = app.query_one("#status", Static)
            status_lines = _plain(status).splitlines()
            assert "Detail:" in status_lines[-1]
            assert status.size.height >= len(status_lines)

            buffer.append("2026-07-30 ERROR sds200.test: hidden error")
            app._poll_log_buffer()

            await pilot.press("g")
            await pilot.pause()
            assert app.logs_visible
            assert not app.screen.has_class("hide-logs")
            assert "hidden error" in _plain(logs)

    asyncio.run(exercise())


def test_short_tui_log_panel_keeps_only_newest_rows_without_body_scroll() -> None:
    async def exercise() -> None:
        buffer = TuiLogBuffer(limit=10)
        for index in range(6):
            buffer.append(
                f"2026-09-03 WARNING sds200.test: event {index} " + "long diagnostic context " * 8
            )
        app = _app(buffer)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            body = app.query_one("#body")
            logs = app.query_one("#logs", Static)
            assert not app.logs_visible
            assert not logs.display

            await pilot.press("g")
            await pilot.pause()
            rendered = _plain(logs)

            assert "event 1" not in rendered
            assert "event 2" in rendered
            assert "event 3" in rendered
            assert "event 4" in rendered
            assert "event 5" in rendered
            assert logs.region.height == 7
            assert logs.styles.text_wrap == "nowrap"
            assert logs.styles.text_overflow == "ellipsis"
            assert body.max_scroll_y == 0

            buffer.append(
                "2026-09-03 WARNING sds200.test: event 6 " + "newest diagnostic context " * 8
            )
            app._poll_log_buffer()
            await pilot.pause()

            rendered = _plain(logs)
            assert "event 2" not in rendered
            assert "event 3" in rendered
            assert "event 4" in rendered
            assert "event 5" in rendered
            assert "event 6" in rendered
            assert body.max_scroll_y == 0

    asyncio.run(exercise())


def test_tui_status_transitions_include_local_since_timestamps(local_timezone_utc) -> None:
    async def exercise() -> None:
        from datetime import UTC

        now = [datetime(2026, 7, 28, 4, 18, 32, tzinfo=UTC)]
        app = ScannerTuiApp(
            ScannerIdentity(
                endpoint="udp://192.168.0.251:50536",
                model="SDS200",
                firmware="Version 1.26.01",
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            palette=DEFAULT_DARK_THEME,
            now=lambda: now[0],
        )

        async with app.run_test(size=(80, 32)) as pilot:
            connection = _plain(app.query_one("#connection", Static))
            status = _plain(app.query_one("#status", Static))
            assert (
                "Connection: CONNECTED\nStatus since: Tue, 28 Jul 2026 04:18:32 +0000"
            ) in connection
            assert "AVAILABLE @ Tue, 28 Jul 2026 04:18:32 +0000" in status
            assert "NORMAL @ Tue, 28 Jul 2026 04:18:32 +0000" in status

            now[0] = datetime(2026, 7, 28, 4, 20, 5, tzinfo=UTC)
            app._apply_connection(False)
            await pilot.pause()
            connection = _plain(app.query_one("#connection", Static))
            status = _plain(app.query_one("#status", Static))
            assert (
                "Connection: DISCONNECTED\nStatus since: Tue, 28 Jul 2026 04:20:05 +0000"
            ) in connection
            assert "UNAVAILABLE @ Tue, 28 Jul 2026 04:20:05 +0000" in status
            assert "ERROR @ Tue, 28 Jul 2026 04:20:05 +0000" in status

    asyncio.run(exercise())


def test_tui_renders_mode_aware_weather_details() -> None:
    async def exercise() -> None:
        cases = (
            (
                "synthetic-weather.xml",
                (
                    "Mode: WX Scan",
                    "V_Screen: wx_alert",
                    "State node: WxChannel",
                ),
                (
                    "Weather channel: WX 7",
                    "Frequency: 162.550000MHz",
                    "Modulation: FM",
                    "Weather mode: Monitor Weather",
                    "Hold: OFF",
                    "Signal: STRONG (5)",
                    "RSSI: -58",
                    "SAME selection: -",
                ),
            ),
            (
                "synthetic-weather-hold.xml",
                (
                    "Mode: WX Hold",
                    "V_Screen: wx_alert",
                    "State node: WxChannel",
                ),
                (
                    "Weather channel: WX 7",
                    "Frequency: 162.550000MHz",
                    "Modulation: FM",
                    "Weather mode: Monitor Weather",
                    "Hold: ON",
                    "Signal: STRONG (5)",
                    "RSSI: -58",
                    "SAME selection: -",
                ),
            ),
            (
                "synthetic-weather-alert.xml",
                (
                    "Mode: Weather Alert Hold",
                    "V_Screen: weather_alert",
                    "State node: WxChannel",
                ),
                (
                    "Weather channel: WX 4: Synthetic Weather Channel 4",
                    "Frequency: 162.475000MHz",
                    "Modulation: FM",
                    "Weather mode: Weather Alert",
                    "Hold: ON",
                    "Signal: STRONG (4)",
                    "RSSI: -64",
                    "SAME selection: Front Range Counties",
                ),
            ),
        )

        for fixture_name, system_values, channel_values in cases:
            app = _fixture_app(fixture_name)
            async with app.run_test(size=(80, 36)):
                system_widget = app.query_one("#system", Static)
                channel_widget = app.query_one("#channel", Static)
                system = _plain(system_widget)
                channel = _plain(channel_widget)

                assert system_widget.border_title == "Screen Mode"
                assert channel_widget.border_title == "Weather"
                for value in system_values:
                    assert value in system
                for value in channel_values:
                    assert value in channel

    asyncio.run(exercise())


def test_tui_renders_mode_aware_tone_out_details() -> None:
    async def exercise() -> None:
        cases = (
            (
                "synthetic-tone-out.xml",
                (
                    "Mode: Tone-Out",
                    "V_Screen: tone_out",
                    "State node: ToneOutChannel",
                ),
                (
                    "Tone Out profile: FTO 3: Synthetic Tone Out 3",
                    "Frequency: 154.190000MHz",
                    "Modulation: NFM",
                    "Tone A: 600.9Hz",
                    "Tone B: 1006.9Hz",
                    "Hold: OFF",
                    "Signal: WEAK (1)",
                    "RSSI: -104",
                ),
            ),
            (
                "synthetic-tone-out-hold.xml",
                (
                    "Mode: Tone-Out",
                    "V_Screen: tone_out",
                    "State node: ToneOutChannel",
                ),
                (
                    "Tone Out profile: FTO 12: Synthetic Tone Out 12",
                    "Frequency: 153.830000MHz",
                    "Modulation: NFM",
                    "Tone A: 879.0Hz",
                    "Tone B: 0.0Hz",
                    "Hold: ON",
                    "Signal: STRONG (4)",
                    "RSSI: -73",
                ),
            ),
        )

        for fixture_name, system_values, channel_values in cases:
            app = _fixture_app(fixture_name)
            async with app.run_test(size=(80, 36)):
                system_widget = app.query_one("#system", Static)
                channel_widget = app.query_one("#channel", Static)
                system = _plain(system_widget)
                channel = _plain(channel_widget)

                assert system_widget.border_title == "Screen Mode"
                assert channel_widget.border_title == "Tone Out"
                for value in system_values:
                    assert value in system
                for value in channel_values:
                    assert value in channel

    asyncio.run(exercise())
