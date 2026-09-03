from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from sds200.state import snapshot_from_scanner_info
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_logging import TuiLogBuffer
from sds200.xml_protocol import ScannerInfoParser

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


def _app(log_buffer: TuiLogBuffer | None = None) -> ScannerTuiApp:
    return ScannerTuiApp(
        ScannerIdentity(
            endpoint="udp://192.168.0.251:50536",
            model="SDS200",
            firmware="Version 1.26.01",
        ),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        log_buffer=log_buffer,
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


def test_tui_connection_panel_renders_optional_remote_target() -> None:
    async def exercise() -> None:
        direct_app = _app()
        async with direct_app.run_test(size=(100, 30)):
            direct_connection = _plain(
                direct_app.query_one("#connection", Static)
            )
            assert "Endpoint: udp://192.168.0.251:50536" in direct_connection
            assert "Target:" not in direct_connection

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
            remote_connection = _plain(
                remote_app.query_one("#connection", Static)
            )
            assert "Endpoint: sdsctl-remote-daemon" in remote_connection
            assert "Target: 192.168.0.18:50443" in remote_connection
            assert remote_app.screen.has_class("-connection-target")

            await pilot.resize_terminal(120, 40)
            await pilot.pause()
            assert remote_app.query_one("#connection", Static).region.height >= 5

    asyncio.run(exercise())


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
                    "Quick Search"
                    if fixture_name == "synthetic-quick-search.xml"
                    else "Close Call"
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
            assert system.region.y == connection.region.bottom
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
                f"2026-09-03 WARNING sds200.test: event {index} "
                + "long diagnostic context " * 8
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
                "2026-09-03 WARNING sds200.test: event 6 "
                + "newest diagnostic context " * 8
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


def test_tui_status_transitions_include_local_since_timestamps() -> None:
    async def exercise() -> None:
        now = [datetime(2026, 7, 28, 4, 18, 32)]
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
            assert "CONNECTED since 04:18:32" in connection
            assert "AVAILABLE since 04:18:32" in status
            assert "NORMAL since 04:18:32" in status

            now[0] = datetime(2026, 7, 28, 4, 20, 5)
            app._apply_connection(False)
            await pilot.pause()
            connection = _plain(app.query_one("#connection", Static))
            status = _plain(app.query_one("#status", Static))
            assert "DISCONNECTED since 04:20:05" in connection
            assert "UNAVAILABLE since 04:20:05" in status
            assert "ERROR since 04:20:05" in status

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
