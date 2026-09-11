from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from sds200 import __version__
from sds200.state import snapshot_from_scanner_info
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_clock import ScannerTuiHeader, UtcHeaderClock
from sds200.xml_protocol import ScannerInfoParser

from .test_tui import XML


def test_screenshot_normalizer_preserves_actual_fixed_utc_clock():
    from scripts.generate_tui_screenshots import normalize_svg
    source = '<svg id="terminal-123"><text>2026-07-30T23:15:00Z</text></svg>'
    assert normalize_svg(source, namespace="demo") == source.replace("123", "demo") + "\n"


@pytest.mark.parametrize("text", ["", "23:15:00", "2026-07-30T23:15:01Z",
                                   "2026-07-30T23:15:00Z</text><text>2026-07-30T23:15:00Z"])
def test_screenshot_normalizer_does_not_hide_a_missing_wrong_or_duplicate_clock(text):
    from scripts.generate_tui_screenshots import normalize_svg
    with pytest.raises(RuntimeError):
        normalize_svg(f'<svg id="terminal-123"><text>{text}</text></svg>', namespace="demo")


@pytest.mark.parametrize(("value", "expected"), [
    (datetime(2026, 9, 10, 23, 59, 59, tzinfo=UTC), "2026-09-10T23:59:59Z"),
    (datetime(2026, 9, 11, tzinfo=UTC), "2026-09-11T00:00:00Z"),
    (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), "2026-12-31T23:59:59Z"),
    (datetime(2027, 1, 1, tzinfo=UTC), "2027-01-01T00:00:00Z"),
    (datetime(2028, 2, 29, 12, 3, 4, 999999, tzinfo=UTC), "2028-02-29T12:03:04Z"),
    (datetime(1, 1, 1, tzinfo=UTC), "0001-01-01T00:00:00Z"),
    (datetime(2026, 9, 10, 20, 3, 4, tzinfo=timezone(timedelta(hours=-6))),
     "2026-09-11T02:03:04Z"),
    (datetime(2026, 9, 11, 1, 2, 3, tzinfo=timezone(timedelta(hours=5, minutes=30))),
     "2026-09-10T19:32:03Z"),
])
def test_header_clock_uses_full_utc_date_and_24_hour_time(value, expected):
    assert UtcHeaderClock(lambda: value).render().plain == expected


@pytest.mark.parametrize("value", [datetime(2026, 9, 10), None, "PRIVATE", True])
def test_unknown_wall_clock_never_fabricates_utc_or_prints_raw_values(value):
    assert UtcHeaderClock(lambda: value).render().plain == "UTC time unavailable"


def test_clock_failure_is_sanitized_and_later_render_recovers():
    clock = Mock(side_effect=[RuntimeError("PRIVATE"), datetime(2026, 9, 11, tzinfo=UTC)])
    widget = UtcHeaderClock(clock)
    assert widget.render().plain == "UTC time unavailable"
    assert widget.render().plain == "2026-09-11T00:00:00Z"


def test_utc_conversion_overflow_does_not_crash_the_header():
    value = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
    assert UtcHeaderClock(lambda: value).render().plain == "UTC time unavailable"


@pytest.mark.parametrize("size", [(64, 24), (80, 32), (100, 30), (160, 45)])
@pytest.mark.parametrize("palette", [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])
def test_full_header_fits_without_moving_panels_and_tracks_clock_changes(size, palette):
    async def exercise():
        now = [datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)]
        app = ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "Version 1.26.01",
                            connection_target="192.0.2.18:50443"),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            now=lambda: now[0], palette=palette,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            header = app.query_one(ScannerTuiHeader)
            clock = app.query_one(UtcHeaderClock)
            title = header.query_one("HeaderTitle")
            assert app.title == f"sdsctl v{__version__}"
            assert app.sub_title == ""
            assert header.region.height == clock.region.height == 1
            assert clock.region.width == 22
            assert clock.region.right == header.content_region.right
            assert title.region.right <= clock.region.x
            assert title.region.width >= len(app.title)
            assert len(clock.render().plain) == 20
            assert clock.render().plain == "2026-12-31T23:59:59Z"
            assert "2026-12-31T23:59:59Z" in app.export_screenshot()
            regions = {name: app.query_one(name).region
                       for name in ("#connection", "#system", "#channel", "#status")}
            now[0] += timedelta(seconds=1)
            await pilot.pause(1.05)
            assert clock.render().plain == "2027-01-01T00:00:00Z"
            assert "2027-01-01T00:00:00Z" in app.export_screenshot()
            now[0] -= timedelta(hours=2)
            await pilot.pause(1.05)
            assert clock.render().plain == "2026-12-31T22:00:00Z"
            assert "2026-12-31T22:00:00Z" in app.export_screenshot()
            assert {name: app.query_one(name).region for name in regions} == regions
    asyncio.run(exercise())
