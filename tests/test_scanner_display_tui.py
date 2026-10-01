from __future__ import annotations

import asyncio
import copy
import io
from dataclasses import replace
from threading import Event
from time import monotonic, sleep

import pytest
from rich.console import Console
from textual.widgets import Static

from sds200.front_panel_keys import front_panel_inventory_snapshot
from sds200.scanner_display_reader import (
    DisplayFrameReader,
    DisplayFrameSource,
    DisplayWireError,
    daemon_display_source,
    decode_display_packet,
)
from sds200.scanner_display_tui import (
    MimicRuntimeScreen,
    MimicScreen,
    render_front_panel_inventory_terminal,
    render_mimic_terminal,
    safe_terminal_text,
)
from sds200.tui import ScannerIdentity, ScannerTuiApp

from .test_scanner_display_web import scenarios
from .test_tui import _app, _plain


@pytest.fixture(scope="module")
def packets():
    return {name: item["display"] for name, item in scenarios().items()}


@pytest.mark.parametrize(
    "state,expected",
    [
        ("current", "Scanner layout unavailable."),
        ("waiting", "Connected — waiting for a new scanner frame."),
        ("disconnected", "Scanner disconnected — waiting for reconnection."),
        ("stale", "Scanner data is stale — waiting for a fresh frame."),
        ("override", "Scanner menu, popup or replay is active — normal display paused."),
        ("ambiguous_records", "Scanner data is inconsistent — waiting for a matching frame."),
        ("unsupported_screen", "This scanner screen is not supported by Mimic-SDS."),
    ],
)
def test_empty_screen_reason_and_missing_profile_are_distinct(packets, state, expected):
    frame = copy.deepcopy(packets["held_trunk"]["frames"]["preferred"])
    frame.update(status=state, screen=None)
    assert render_mimic_terminal(frame, width=100, height=30).plain == expected
    frame["profile_revision"] = None
    assert render_mimic_terminal(frame, width=100, height=30).plain == (
        "An administrator must import a display profile for this scanner."
    )


def test_all_synthetic_frames_decode_and_are_detached_immutable(packets):
    for original in packets.values():
        value = copy.deepcopy(original)
        packet = decode_display_packet(value)
        assert packet["endpoint_id"] == original["endpoint_id"]
        value["frames"].clear()
        assert len(packet["frames"]) == 3
        with pytest.raises(TypeError):
            packet["frames"]["preferred"]["status"] = "current"


@pytest.mark.parametrize("width", [60, 80, 100, 160])
def test_function_inversion_and_signal_level_survive_terminal_widths(packets, width):
    frame = copy.deepcopy(packets["held_trunk"]["frames"]["preferred"])
    function = next(r for r in frame["screen"]["regions"] if r["id"] == "function")
    function.update(
        text="F", value_status="raw_source", stored_color={"text": "ffffff", "background": "000000"}
    )
    signal = next(r for r in frame["screen"]["regions"] if r["id"] == "signal")
    signal.update(text="5", value_status="raw_source")
    output = render_mimic_terminal(frame, width=width, height=22)
    style = output.get_style_at_offset(Console(), output.plain.index("F"))
    assert style.bgcolor.name == "#ffffff" and style.color.name == "#000000"
    assert ("S5" if width == 60 else "▁▂▃▄▅") in output.plain
    for status in ("blank", "data_unavailable"):
        function.update(text=None, value_status=status)
        output = render_mimic_terminal(frame, width=width, height=22)
        # The first interior cell cannot remain an inverted white placeholder.
        style = output.get_style_at_offset(Console(), width + 2)
        assert style.bgcolor.name == "#000000"


@pytest.mark.parametrize(
    "path,value",
    [
        (("extra",), "private"),
        (("schema_version",), True),
        (("endpoint_id",), "/private/path"),
        (("frames", "preferred", "source", "source_kind"), "other"),
        (("frames", "preferred", "source", "acquired_at"), "2026-02-30T01:00:00+00:00"),
        (("frames", "preferred", "sequence"), True),
        (("frames", "preferred", "sequence"), -1),
        (("frames", "preferred", "age_seconds"), float("nan")),
        (("frames", "preferred", "age_seconds"), float("inf")),
        (("frames", "preferred", "age_seconds"), 10**1000),
        (("frames", "preferred", "screen", "rows"), 20.0),
        (("frames", "preferred", "screen", "mode"), "__proto__"),
        (("frames", "preferred", "screen", "regions", 0, "column"), -1),
        (("frames", "preferred", "screen", "regions", 0, "alignment"), "fixed"),
        (("frames", "preferred", "screen", "regions", 0, "token"), "x" * 65),
        (
            ("frames", "preferred", "screen", "regions", 0, "stored_color"),
            {"text": "url(x)", "background": "000000"},
        ),
        (("frames", "preferred", "screen", "regions", 0, "text"), "x" * 257),
        (("frames", "preferred", "screen", "regions", 0, "text"), "\x1b"),
        (("frames", "preferred", "screen", "regions", 0, "text"), "\ud800"),
        (("frames", "preferred", "screen", "regions", 0, "text"), "hidden\u200b"),
        (
            ("frames", "preferred", "screen", "issues"),
            [{"namespace": "path", "group": 1, "kind": "missing_group"}],
        ),
        (("frames", "preferred", "indicators", "alert_led"), "Orange"),
        (("frames", "preferred", "indicators", "channel_hold"), "true"),
        (("frames", "preferred", "status"), "stale"),
        (("frames", "simple", "sequence"), 987654),
        (("frames", "detail", "profile_revision"), "a" * 64),
        (("frames", "detail", "screen"), None),
    ],
)
def test_hostile_wire_rejected_with_fixed_error(packets, path, value):
    packet = copy.deepcopy(packets["held_trunk"])
    parent = packet
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(DisplayWireError, match=r"^Invalid Mimic-SDS frame\.$"):
        decode_display_packet(packet)


def _until(condition):
    deadline = monotonic() + 3
    while not condition():
        assert monotonic() < deadline, "reader did not reach expected state"
        sleep(0.005)


def test_reader_retains_deadline_rejects_replay_and_resets_session(packets):
    now = [0.0]
    reader = DisplayFrameReader(
        DisplayFrameSource(lambda: None, lambda: None), clock=lambda: now[0]
    )
    packet = copy.deepcopy(packets["held_trunk"])
    for frame in packet["frames"].values():
        frame["sequence"], frame["age_seconds"] = 0, 0
    frozen = decode_display_packet(packet)
    reader._accept(frozen, 0)
    assert reader.view()[0] is frozen
    now[0] = 5.1
    reader._accept(frozen, now[0])
    assert reader.view()[0] is None  # same sequence cannot renew freshness
    reader.set_active(False)
    reader._accept(frozen, 6)
    assert reader.view()[0] is None  # clearing also cannot renew it
    for frame in packet["frames"].values():
        frame["sequence"] = 1
    reader._accept(decode_display_packet(packet), 6)
    assert reader.view()[0] is not None
    with pytest.raises(DisplayWireError):
        reader._accept(frozen, 7)
    packet["session_id"] = "00000000-0000-0000-0000-000000000003"
    for frame in packet["frames"].values():
        frame["sequence"] = 0
    reader._accept(decode_display_packet(packet), 7)
    assert reader.view()[0] is not None
    packet["endpoint_id"] = "00000000-0000-0000-0000-000000000005"
    with pytest.raises(DisplayWireError):
        reader._accept(decode_display_packet(packet), 8)
    reader.close(wait=True)


def test_reader_lazy_hide_late_response_and_close(packets):
    entered, release = Event(), Event()
    calls, closes = [], []

    def read():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return packets["held_trunk"]

    reader = DisplayFrameReader(DisplayFrameSource(read, lambda: closes.append(1)))
    try:
        assert not reader.alive and not calls
        reader.set_active(True)
        assert entered.wait(2)
        reader.set_active(False)  # must not wait on read
        release.set()
        _until(lambda: closes)
        assert reader.view()[0] is None
        assert len(calls) == 1
        reader.set_active(True)
        _until(lambda: reader.view()[0] is not None)
    finally:
        release.set()
        reader.close(wait=True)
    count = len(calls)
    reader.set_active(True)
    assert not reader.alive and reader.view()[0] is None
    assert len(calls) == count and len(closes) >= 2


def test_reader_failure_sanitized_and_transport_closed(packets):
    def fail():
        raise RuntimeError("secret /private/path \x1b[31m")

    closed = Event()
    reader = DisplayFrameReader(DisplayFrameSource(fail, closed.set))
    try:
        reader.set_active(True)
        _until(lambda: "retrying" in reader.view()[1])
        assert "secret" not in reader.view()[1] and reader.view()[0] is None
    finally:
        reader.close(wait=True)
    assert closed.is_set() and not reader.alive


def test_source_negotiates_before_display_request():
    calls = []

    class Client:
        def hello(self):
            calls.append("hello")
            return {"operations": ["display.frame"]}

        def display_frame(self):
            calls.append("display")
            return {}

        def close(self):
            calls.append("close")

    source = daemon_display_source(Client())
    assert not calls
    assert source.read() == {}
    source.close()
    assert calls == ["hello", "display", "close"]


@pytest.mark.parametrize("size", [(60, 22), (100, 26), (160, 41), (240, 56)])
def test_renderer_geometry_for_every_synthetic_layout(packets, size):
    for packet in packets.values():
        for frame in decode_display_packet(packet)["frames"].values():
            if frame["screen"] is None:
                continue
            result = render_mimic_terminal(frame, width=size[0], height=size[1])
            lines = result.split("\n", allow_blank=True)
            assert len(lines) == size[1]
            assert all(line.cell_len == size[0] for line in lines)
            assert "\x1b" not in result.plain


@pytest.mark.parametrize("color_system", ["truecolor", "256", "standard", None])
def test_renderer_holds_led_and_terminal_color_fallback(packets, color_system, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    frame = decode_display_packet(packets["held_trunk"])["frames"]["detail"]
    rendered = render_mimic_terminal(frame, width=100, height=26, treatment="border")
    output = io.StringIO()
    console = Console(file=output, width=100, color_system=color_system, force_terminal=True)
    console.print(rendered, end="")
    assert ("\x1b[" in output.getvalue()) is (color_system is not None)
    assert "Demo Regional Communications Authority" in output.getvalue()
    index = rendered.plain.index("Demo Regional Communications Authority")
    style = rendered.get_style_at_offset(console, index)
    pair = next(r["stored_color"] for r in frame["screen"]["regions"] if r["id"] == "system")
    assert style.color.name == "#" + pair["background"]
    assert style.bgcolor.name == "#" + pair["text"]
    assert rendered.get_style_at_offset(console, 0).bgcolor is not None


def test_renderer_wide_unicode_and_markup_are_literal(packets):
    packet = copy.deepcopy(packets["held_trunk"])
    for frame in packet["frames"].values():
        region = next(r for r in frame["screen"]["regions"] if r["id"] == "system")
        region["text"] = "界" * 90 + "[red]literal[/red]"
    for frame in decode_display_packet(packet)["frames"].values():
        result = render_mimic_terminal(frame, width=100, height=26)
        assert all(line.cell_len == 100 for line in result.split("\n"))
    assert "Resize" in render_mimic_terminal(frame, width=40, height=20).plain
    assert safe_terminal_text("one\n\x1b[31m\u200b") == "one\n?[31m?"


def test_front_panel_inventory_renderer_is_complete_disabled_and_fail_closed():
    inventory = front_panel_inventory_snapshot("SDS200")
    rendered = render_front_panel_inventory_terminal(inventory).plain

    assert "Inventory v1 | 27 codes | controls enabled: no" in rendered
    assert "not TUI shortcuts" in rendered
    assert rendered.count("— unavailable (") == 27
    for entry in inventory["keys"]:
        assert f"\n{entry['code']}  {entry['label']} — unavailable" in rendered
        assert entry["context_note"] in rendered
        assert entry["unavailable_reason"] in rendered

    malformed = copy.deepcopy(inventory)
    malformed["keys"][0]["label"] = "secret /private/path \x1b[31m"
    refused = render_front_panel_inventory_terminal(malformed).plain
    assert "Inventory unavailable" in refused
    assert "secret" not in refused and "/private/path" not in refused

    enabled = copy.deepcopy(inventory)
    enabled["controls_available"] = True
    enabled["keys"][0]["available"] = True
    assert "Inventory unavailable" in render_front_panel_inventory_terminal(enabled).plain


@pytest.mark.parametrize("held", [True, False, None])
def test_renderer_site_hold_is_independent_and_uses_profile_color(packets, held):
    packet = copy.deepcopy(packets["held_trunk"])
    frame = packet["frames"]["detail"]
    for variant in packet["frames"].values():
        variant["indicators"]["site_hold"] = held
    regions = [r for r in frame["screen"]["regions"] if r["token"] == "SiteName"]
    assert regions
    # Unique synthetic labels allow exact checks for every configured site slot.
    for index, region in enumerate(regions):
        region["text"] = f"Site{index}"
    frozen = decode_display_packet(packet)["frames"]["detail"]
    rendered = render_mimic_terminal(frozen, width=160, height=41)
    console = Console(width=160, color_system="truecolor")
    for region in regions:
        style = rendered.get_style_at_offset(console, rendered.plain.index(region["text"]))
        pair = region["stored_color"]
        reversed_colors = region["reverse_colors"] or held is True
        assert style.color.name == "#" + pair["background" if reversed_colors else "text"]
        assert style.bgcolor.name == "#" + pair["text" if reversed_colors else "background"]


def test_unicode_limit_matches_server_and_browser(packets):
    packet = copy.deepcopy(packets["held_trunk"])
    name = next(
        r for r in packet["frames"]["preferred"]["screen"]["regions"] if r["id"] == "system"
    )
    name["text"] = "🚀" * 256
    assert decode_display_packet(packet)
    name["text"] += "🚀"
    with pytest.raises(DisplayWireError):
        decode_display_packet(packet)


def test_actual_tui_clears_stale_values_and_marks_profile_refresh(packets):
    async def exercise():
        def sample(name, sequence):
            packet = copy.deepcopy(packets[name])
            for frame in packet["frames"].values():
                if frame["sequence"] is not None:
                    frame["sequence"] = sequence
            return packet

        selected = [sample("held_trunk", 100)]
        app = ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "fixture"),
            _app()._snapshot,
            display_source=DisplayFrameSource(lambda: selected[0], lambda: None),
            clock=lambda: 1,
        )
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("m")
                await pilot.pause(0.3)
                screen = app.screen
                for index, scenario in enumerate(
                    (
                        "stale",
                        "connection_lost",
                        "reconnecting",
                        "recovered",
                        "profile_refresh_pending",
                        "profile_refresh_failed",
                    )
                ):
                    selected[0] = sample(scenario, 101 + index)
                    await pilot.pause(0.5)
                    grid = _plain(screen.query_one("#mimic-grid", Static))
                    health = _plain(screen.query_one("#mimic-health", Static))
                    if scenario in ("stale", "connection_lost", "reconnecting"):
                        assert "Demo Regional" not in grid and "Dispatch 2" not in grid
                    if scenario == "stale":
                        assert "Stale" in health and "LED: unknown" in health
                    if scenario.startswith("profile_refresh"):
                        assert "refresh" in health and not screen.query_one(
                            "#mimic-health"
                        ).has_class("current")
                await pilot.resize_terminal(160, 45)
                await pilot.pause(0.3)
                assert screen.max_scroll_y == 0
                await pilot.press("x")
                await pilot.pause()
                screen.action_runtime()
                assert sum(isinstance(s, MimicRuntimeScreen) for s in app.screen_stack) == 1
                await pilot.press("q")
        finally:
            app._mimic_reader.close(wait=True)
        assert not app._mimic_reader.alive

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(100, 30), (160, 45)])
def test_actual_tui_screen_drawer_palette_and_return(packets, size):
    async def exercise():
        baseline = _app()
        reads, closes = [], []

        def read():
            reads.append(1)
            return packets["held_trunk"]

        app = ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "fixture", "scanner.example:50443"),
            baseline._snapshot,
            display_source=DisplayFrameSource(read, lambda: closes.append(1)),
            front_panel_inventory=front_panel_inventory_snapshot("SDS200"),
        )
        try:
            async with app.run_test(size=size) as pilot:
                assert not reads
                await pilot.press("m")
                await pilot.pause(0.3)
                assert isinstance(app.screen, MimicScreen)
                screen = app.screen
                assert "Demo Regional Communications Authority" in _plain(
                    screen.query_one("#mimic-grid", Static)
                )
                assert screen.max_scroll_y == 0
                assert screen.query_one("#mimic-grid").size.height == size[1] - 4
                app._log_buffer.append("New log while Mimic is open")
                app.update_snapshot(
                    replace(app._snapshot, channel="Updated while covered"), connected=True
                )
                await pilot.press("v", "b", "h", "s", "d", "a", "r", "c")
                assert screen.style == "simple" and screen.treatment == "border"
                assert not app.check_action("toggle_channel_hold", ())
                await pilot.press("x")
                await pilot.pause(0.3)
                assert isinstance(app.screen, MimicRuntimeScreen)
                assert "scanner.example:50443" in _plain(
                    app.screen.query_one("#mimic-runtime", Static)
                )
                assert "New log while Mimic is open" in _plain(
                    app.screen.query_one("#mimic-runtime", Static)
                )
                runtime = _plain(app.screen.query_one("#mimic-runtime", Static))
                assert "Inventory v1 | 27 codes | controls enabled: no" in runtime
                assert runtime.count("— unavailable (") == 27
                assert "No scanner-key dispatch is installed" in runtime
                assert closes and app._mimic_reader.view()[0] is None
                paused = len(reads)
                await pilot.pause(0.3)
                assert len(reads) == paused
                await pilot.press("escape")
                await pilot.pause(0.3)
                assert app.screen is screen
                await pilot.press("ctrl+p")
                await pilot.press("v", "b", "h", "s")
                assert app.screen is not screen
                assert screen.style == "simple" and screen.treatment == "border"
                await pilot.press("escape")
                await pilot.press("m")
                await pilot.pause()
                assert app._mimic_screen is None
                assert "CONNECTED" in _plain(app.query_one("#connection", Static))
                assert "Updated while covered" in (
                    _plain(app.query_one("#channel", Static))
                    + _plain(app.query_one("#system", Static))
                )
                assert "New log while Mimic is open" in _plain(app.query_one("#logs", Static))
                await pilot.press("m")
                await pilot.pause(0.3)
                assert app.screen.style == "simple" and app.screen.treatment == "border"
        finally:
            app._mimic_reader.close(wait=True)
        assert not app._mimic_reader.alive

    asyncio.run(exercise())


def test_old_daemon_or_direct_usb_tui_has_no_mimic_action():
    app = _app()
    assert not app.check_action("mimic", ())
    app.action_mimic()
    assert app._mimic_screen is None and app._mimic_reader is None
