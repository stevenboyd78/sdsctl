from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from math import nextafter

import pytest
from rich.text import Text
from textual.pilot import Pilot
from textual.widgets import Static

from sds200 import tui as tui_module
from sds200.daemon_waterfall_protocol import (
    DaemonWaterfallRecord,
    DaemonWaterfallRecordKind,
)
from sds200.scanner_display_reader import DisplayFrameSource
from sds200.scanner_display_tui import MimicScreen
from sds200.state import ScannerScreenKind, snapshot_from_scanner_info
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, ThemePalette
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_waterfall import (
    TuiWaterfallModel,
    TuiWaterfallReader,
    WaterfallScreen,
    _render_mimic_fft,
    _render_mimic_history,
    _render_relative_row,
    _scale_text,
    _waterfall_title,
    normalize_waterfall_values,
)
from sds200.xml_protocol import ScannerInfoParser

from .test_scanner_display_web import scenarios

XML = """<?xml version="1.0" encoding="utf-8"?>
<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">
<System Name="Example P25 System" />
<Department Name="Example Department" />
<Site Name="Example Simulcast" Mod="NFM" />
<TGID Name="Example Dispatch" SvcType="Dispatch" />
<SiteFrequency Freq="769.431250MHz" />
<Property Sig="5" Rssi="-70" Rec="Off" Mute="Unmute" />
</ScannerInfo>"""


def _checkpoint(sequence: int = 1) -> DaemonWaterfallRecord:
    return DaemonWaterfallRecord(
        sequence=sequence,
        observed_at=datetime(2026, 10, 1, tzinfo=UTC),
        kind=DaemonWaterfallRecordKind.SESSION_CHECKPOINT,
        payload={
            "state": "running",
            "waterfall_status": {
                "lower_frequency": "1540000",
                "center_frequency": "1550000",
                "upper_frequency": "1560000",
                "marker_frequency": "1552500",
                "marker_position": "120",
                "modulation": "NFM",
                "fft_area_size": "1",
                "waterfall_mode": "1",
            },
        },
    )


def _gwf(sequence: int, *, offset: int = 0) -> DaemonWaterfallRecord:
    return DaemonWaterfallRecord(
        sequence=sequence,
        observed_at=datetime(2026, 10, 1, 0, 0, sequence, tzinfo=UTC),
        kind=DaemonWaterfallRecordKind.GWF,
        payload={
            "values": [f"{offset + index:x}" for index in range(240)],
            "responses_dropped": 2,
            "overflows": 1,
            "source_received_at": f"2026-10-01T00:00:{sequence:02d}+00:00",
            "session": {
                "state": "running",
                "waterfall_status": {
                    "lower_frequency": "1540000",
                    "center_frequency": "1550000",
                    "upper_frequency": "1560000",
                    "marker_frequency": "1552500",
                    "marker_position": "120",
                    "modulation": "NFM",
                    "fft_area_size": "1",
                    "waterfall_mode": "1",
                },
            },
        },
    )


def _app(
    factory=None,  # type: ignore[no-untyped-def]
    *,
    palette: ThemePalette = DEFAULT_DARK_THEME,
) -> ScannerTuiApp:
    return ScannerTuiApp(
        ScannerIdentity("sdsctl-remote-daemon", "SDS200", "Version 1.26.01"),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        waterfall_client_factory=factory,
        palette=palette,
    )


def _plain(widget: Static) -> str:
    content = widget.content
    return content if isinstance(content, str) else content.plain


async def _wait_for_waterfall_screen(
    app: ScannerTuiApp,
    pilot: Pilot[None],
    *,
    previous: WaterfallScreen | None = None,
    expected_health: str | None = None,
) -> WaterfallScreen:
    for _ in range(200):
        waterfall = app._waterfall_screen
        if waterfall is not None and waterfall is not previous and waterfall.is_mounted:
            health_ready = expected_health is None or expected_health in _plain(
                waterfall.query_one("#waterfall-health", Static)
            )
            if health_ready:
                return waterfall
        await pilot.pause(0.01)
    raise AssertionError("Automatic waterfall screen did not become ready")


async def _wait_for_mimic_screen(
    app: ScannerTuiApp,
    pilot: Pilot[None],
) -> MimicScreen:
    for _ in range(200):
        mimic = app._mimic_screen
        if mimic is not None and mimic.is_mounted and app.screen is mimic:
            return mimic
        await pilot.pause(0.01)
    raise AssertionError("Mimic screen did not become ready")


async def _wait_for_waterfall_close(
    app: ScannerTuiApp,
    pilot: Pilot[None],
    closed: threading.Event,
) -> None:
    for _ in range(200):
        if app._waterfall_screen is None and closed.is_set():
            return
        await pilot.pause(0.01)
    raise AssertionError("Automatic waterfall screen or client did not close")


def test_waterfall_normalization_is_strict_and_relative_only() -> None:
    normalized = normalize_waterfall_values([f"{index:x}" for index in range(240)])

    assert len(normalized) == 240
    assert normalized[0] == 0.0
    assert normalized[-1] == 1.0
    assert normalize_waterfall_values(["a"] * 240) == (0.5,) * 240

    for invalid in (
        ["0"] * 239,
        ["0"] * 239 + ["-1"],
        ["0"] * 239 + ["0x10"],
        ["0"] * 239 + ["20000000000000"],
    ):
        with pytest.raises(ValueError, match="Waterfall frame"):
            normalize_waterfall_values(invalid)


@pytest.mark.parametrize(
    ("light", "expected_styles"),
    [
        (False, ["#5fd75f", "#ffaf00", "bold #ff5f5f"]),
        (True, ["#15803d", "#b45309", "bold #b91c1c"]),
    ],
)
def test_waterfall_relative_intensity_uses_color_and_glyph_redundantly(
    light: bool,
    expected_styles: list[str],
) -> None:
    rendered = _render_relative_row((0.0, 0.5, 1.0), 3, light=light)

    assert rendered.plain == " =@"
    assert [span.style for span in rendered.spans] == expected_styles
    title = _waterfall_title(light=light)
    assert "LOW MID HIGH" in title.plain
    assert "relative per frame" in title.plain
    assert "uncalibrated" in title.plain


def test_waterfall_relative_intensity_band_boundaries_are_exact() -> None:
    rendered = _render_relative_row(
        (
            0.0,
            nextafter(1.0 / 3.0, 0.0),
            1.0 / 3.0,
            nextafter(2.0 / 3.0, 0.0),
            2.0 / 3.0,
            1.0,
        ),
        6,
    )

    assert [(span.start, span.end, span.style) for span in rendered.spans] == [
        (0, 2, "#5fd75f"),
        (2, 4, "#ffaf00"),
        (4, 6, "bold #ff5f5f"),
    ]


def test_mimic_waterfall_uses_radio_style_fft_marker_and_five_bands() -> None:
    fft = _render_mimic_fft(
        tuple(index / 239 for index in range(240)),
        24,
        6,
        marker_position=120,
    )
    assert len(fft.plain.splitlines()) == 6
    assert all(len(line) == 24 for line in fft.plain.splitlines())
    assert "│" in fft.plain or "◆" in fft.plain
    assert {span.style for span in fft.spans} >= {
        "bold #24d36b",
        "bold #edf4fc",
    }

    history = _render_mimic_history(
        ((0.0, 0.21, 0.41, 0.61, 1.0),),
        5,
    )
    assert history.plain == "░▒▓██"
    assert {span.style for span in history.spans} == {
        "#173b8f",
        "#00b7ff",
        "#24d36b",
        "#ffe45e",
        "bold #ff4b3e",
    }


def test_waterfall_source_timestamp_is_presented_in_local_time_only() -> None:
    model = TuiWaterfallModel()
    model.begin_connection()
    model.apply(_checkpoint())
    model.apply(_gwf(2))

    snapshot = model.snapshot()
    source_timestamp = snapshot.source_received_at
    assert source_timestamp == datetime(2026, 10, 1, 0, 0, 2, tzinfo=UTC)

    rendered = _scale_text(
        snapshot,
        local_timezone=timezone(timedelta(hours=-6)),
    )

    assert "Source local timestamp: 2026-09-30T18:00:02-06:00" in rendered
    assert source_timestamp.isoformat() not in rendered
    assert snapshot.source_received_at is source_timestamp


def test_waterfall_model_bounds_history_and_pause_clear_are_local() -> None:
    model = TuiWaterfallModel(history_limit=2)
    model.begin_connection()
    model.apply(_checkpoint())
    model.apply(_gwf(2))
    model.apply(_gwf(3, offset=1))
    model.apply(_gwf(4, offset=2))

    current = model.snapshot()
    assert current.status == "Live"
    assert current.frames_received == 3
    assert len(current.history) == 2
    assert (current.responses_dropped, current.overflows) == (2, 1)
    assert current.session["state"] == "running"

    assert model.toggle_paused()
    model.apply(_gwf(5, offset=3))
    paused = model.snapshot()
    assert paused.frames_received == 4
    assert paused.history == current.history
    assert paused.latest == current.latest

    model.clear()
    cleared = model.snapshot()
    assert cleared.paused
    assert cleared.history == ()
    assert cleared.latest is None
    assert not model.toggle_paused()
    model.apply(_gwf(6, offset=4))
    assert len(model.snapshot().history) == 1


def test_waterfall_model_clears_unconfirmed_values_across_reconnect() -> None:
    model = TuiWaterfallModel()
    model.begin_connection()
    model.apply(_checkpoint())
    model.apply(_gwf(2))
    model.mark_unavailable()

    unavailable = model.snapshot()
    assert unavailable.status == "Unavailable — reconnecting"
    assert unavailable.latest is None
    assert unavailable.history == ()
    assert unavailable.session == {}

    model.begin_connection()
    assert model.snapshot().reconnects == 1
    with pytest.raises(ValueError, match="begin with a checkpoint"):
        model.apply(_gwf(1))


class _QueueClient:
    def __init__(self, records: list[DaemonWaterfallRecord]) -> None:
        self.records: queue.Queue[DaemonWaterfallRecord | None] = queue.Queue()
        for record in records:
            self.records.put(record)
        self.closed = threading.Event()
        self.close_calls = 0

    def receive(self) -> DaemonWaterfallRecord:
        value = self.records.get(timeout=2)
        if value is None:
            raise OSError("closed")
        return value

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        self.records.put(None)


class _FailingClient(_QueueClient):
    def receive(self) -> DaemonWaterfallRecord:
        try:
            value = self.records.get_nowait()
        except queue.Empty as error:
            raise OSError("disconnected") from error
        if value is None:
            raise OSError("closed")
        return value


def test_waterfall_reader_owns_one_client_and_closes_it_boundedly() -> None:
    client = _QueueClient([_checkpoint(), _gwf(2)])
    factory_calls = 0

    def factory() -> _QueueClient:
        nonlocal factory_calls
        factory_calls += 1
        return client

    model = TuiWaterfallModel()
    reader = TuiWaterfallReader(factory, model, reconnect_delay=1.0)
    reader.start()
    deadline = time.monotonic() + 2
    while model.snapshot().frames_received < 1:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert factory_calls == 1
    assert reader.alive
    assert reader.close(wait=True)
    assert client.closed.is_set()
    assert client.close_calls >= 1
    assert not reader.alive


def test_waterfall_reader_reconnects_with_a_fresh_checkpoint() -> None:
    first = _FailingClient([_checkpoint(), _gwf(2)])
    second = _QueueClient([_checkpoint(), _gwf(2, offset=20)])
    clients = iter((first, second))
    factory_calls = 0

    def factory() -> _QueueClient:
        nonlocal factory_calls
        factory_calls += 1
        return next(clients)

    model = TuiWaterfallModel()
    reader = TuiWaterfallReader(factory, model, reconnect_delay=0.01)
    reader.start()
    deadline = time.monotonic() + 2
    while model.snapshot().reconnects < 1 or model.snapshot().frames_received < 2:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    current = model.snapshot()
    assert factory_calls == 2
    assert current.status == "Live"
    assert current.reconnects == 1
    assert current.frames_received == 2
    assert len(current.history) == 1
    assert first.closed.is_set()
    assert reader.close(wait=True)
    assert second.closed.is_set()


def test_waterfall_reader_does_not_retry_an_invalid_payload() -> None:
    invalid = DaemonWaterfallRecord(
        sequence=2,
        observed_at=datetime(2026, 10, 1, tzinfo=UTC),
        kind=DaemonWaterfallRecordKind.GWF,
        payload={
            "values": ["0"] * 239,
            "responses_dropped": 0,
            "overflows": 0,
            "source_received_at": "2026-10-01T00:00:02+00:00",
        },
    )
    client = _FailingClient([_checkpoint(), invalid])
    factory_calls = 0

    def factory() -> _QueueClient:
        nonlocal factory_calls
        factory_calls += 1
        return client

    model = TuiWaterfallModel()
    reader = TuiWaterfallReader(factory, model, reconnect_delay=0.01)
    reader.start()
    deadline = time.monotonic() + 2
    while reader.alive:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert factory_calls == 1
    assert model.snapshot().status == "Invalid stream — closed"
    assert client.closed.is_set()


@pytest.mark.parametrize("size", [(100, 30), (160, 45)])
@pytest.mark.parametrize(
    ("palette", "expected_styles"),
    [
        (DEFAULT_DARK_THEME, {"#5fd75f", "#ffaf00", "bold #ff5f5f"}),
        (DEFAULT_LIGHT_THEME, {"#15803d", "#b45309", "bold #b91c1c"}),
    ],
    ids=["dark", "light"],
)
def test_daemon_tui_waterfall_is_responsive_and_releases_lease(
    size: tuple[int, int],
    palette: ThemePalette,
    expected_styles: set[str],
) -> None:
    async def exercise() -> None:
        clients: list[_QueueClient] = []

        def factory() -> _QueueClient:
            client = _QueueClient([_checkpoint(), _gwf(2)])
            clients.append(client)
            return client

        app = _app(factory, palette=palette)
        async with app.run_test(size=size) as pilot:
            assert app.waterfall_available
            assert app.check_action("waterfall", ())
            assert "relative waterfall" in _plain(app.query_one("#keys", Static)).lower()

            await pilot.press("w")
            await pilot.pause(0.3)
            waterfall = app._waterfall_screen
            assert waterfall is not None
            assert waterfall.has_class("light") == (palette is DEFAULT_LIGHT_THEME)
            assert len(clients) == 1
            assert waterfall.reader.alive
            assert "Frames: 1" in _plain(waterfall.query_one("#waterfall-health", Static))
            assert "uncalibrated" in _plain(waterfall.query_one("#waterfall-title", Static))
            assert "Scanner span (raw): lower 1540000 | center 1550000 | upper 1560000" in _plain(
                waterfall.query_one("#waterfall-scale", Static)
            )
            assert "Source local timestamp:" in _plain(
                waterfall.query_one("#waterfall-scale", Static)
            )
            spectrum = waterfall.query_one("#waterfall-spectrum", Static)
            assert "Latest:" in _plain(spectrum)
            spectrum_content = spectrum.content
            assert isinstance(spectrum_content, Text)
            assert {span.style for span in spectrum_content.spans} >= expected_styles
            assert spectrum.content_size.height >= 1
            history = waterfall.query_one("#waterfall-history", Static)
            assert history.region.right <= waterfall.screen.region.right
            assert history.region.bottom <= waterfall.screen.region.bottom

            await pilot.press("space")
            await pilot.pause()
            assert waterfall.model.snapshot().paused
            await pilot.press("c")
            await pilot.pause()
            assert waterfall.model.snapshot().history == ()

            resized = (160, 45) if size == (100, 30) else (100, 30)
            await pilot.resize_terminal(*resized)
            await pilot.pause(0.2)
            assert history.region.right <= waterfall.screen.region.right
            assert history.region.bottom <= waterfall.screen.region.bottom

            await pilot.press("escape")
            await pilot.pause(0.2)
            assert app._waterfall_screen is None
            assert clients[0].closed.is_set()
            assert not waterfall.reader.alive

    asyncio.run(exercise())


def test_dashboard_tui_automatically_tracks_waterfall_and_honors_manual_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(tui_module, "TUI_WATERFALL_AUTO_SWITCH_DELAY", 0.05)
        monkeypatch.setattr(tui_module, "TUI_WATERFALL_AUTO_RETURN_DELAY", 0.05)
        clients: list[_QueueClient] = []

        def factory() -> _QueueClient:
            client = _QueueClient([_checkpoint(), _gwf(2)])
            clients.append(client)
            return client

        app = _app(factory)
        scanning = app._snapshot
        waterfall_state = replace(
            scanning,
            mode="Waterfall",
            screen="waterfall",
            screen_kind=ScannerScreenKind.WATERFALL,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            app.update_snapshot(waterfall_state, connected=True)
            first = await _wait_for_waterfall_screen(app, pilot)
            assert not first.mimic
            assert app._waterfall_auto_origin == "dashboard"

            await pilot.press("escape")
            await _wait_for_waterfall_close(app, pilot, clients[0].closed)
            assert app._waterfall_auto_suppressed
            assert clients[0].closed.is_set()

            app.update_snapshot(scanning, connected=True)
            assert not app._waterfall_auto_suppressed
            app.update_snapshot(waterfall_state, connected=True)
            await _wait_for_waterfall_screen(app, pilot, previous=first)

            app.update_snapshot(scanning, connected=True)
            await _wait_for_waterfall_close(app, pilot, clients[-1].closed)
            assert clients[-1].closed.is_set()

    asyncio.run(exercise())


def test_mimic_tui_automatically_uses_sds_style_waterfall_and_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(tui_module, "TUI_WATERFALL_AUTO_SWITCH_DELAY", 0.05)
        monkeypatch.setattr(tui_module, "TUI_WATERFALL_AUTO_RETURN_DELAY", 0.05)
        packet = scenarios()["held_trunk"]["display"]
        client = _QueueClient([_checkpoint(), _gwf(2)])
        baseline = _app()
        app = ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "fixture"),
            baseline._snapshot,
            display_source=DisplayFrameSource(lambda: packet, lambda: None),
            waterfall_client_factory=lambda: client,
            startup_view="mimic",
        )
        scanning = app._snapshot
        waterfall_state = replace(
            scanning,
            mode="Waterfall",
            screen="waterfall",
            screen_kind=ScannerScreenKind.WATERFALL,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            mimic = await _wait_for_mimic_screen(app, pilot)
            assert isinstance(app.screen, MimicScreen)

            app.update_snapshot(waterfall_state, connected=True)
            waterfall = await _wait_for_waterfall_screen(
                app,
                pilot,
                expected_health="MARKER 1552500 | NFM | FFT 50%",
            )
            assert waterfall.mimic
            assert app.screen is waterfall
            assert app._mimic_screen is mimic
            assert app._waterfall_auto_origin == "mimic"
            assert "SDS200 | WATERFALL" in _plain(waterfall.query_one("#waterfall-title", Static))
            assert "MARKER 1552500 | NFM | FFT 50%" in _plain(
                waterfall.query_one("#waterfall-health", Static)
            )
            assert "Return to Mimic" in _plain(waterfall.query_one("#waterfall-keys", Static))

            app.update_snapshot(scanning, connected=True)
            await _wait_for_waterfall_close(app, pilot, client.closed)
            assert app.screen is mimic
            assert isinstance(app.screen, MimicScreen)
            assert client.closed.is_set()

    asyncio.run(exercise())


def test_direct_tui_does_not_advertise_or_open_waterfall() -> None:
    async def exercise() -> None:
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            assert not app.waterfall_available
            assert not app.check_action("waterfall", ())
            assert "relative waterfall" not in _plain(app.query_one("#keys", Static)).lower()
            await pilot.press("w")
            await pilot.pause()
            assert app._waterfall_screen is None

    asyncio.run(exercise())
