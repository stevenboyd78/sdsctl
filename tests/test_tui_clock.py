from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from sds200 import __version__
from sds200.state import snapshot_from_scanner_info
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.tui_clock import LocalHeaderClock, ScannerTuiHeader
from sds200.xml_protocol import ScannerInfoParser

from .test_tui import XML

pytestmark = pytest.mark.usefixtures("local_timezone_utc")


def test_screenshot_normalizer_preserves_actual_fixed_utc_clock():
    from scripts.generate_tui_screenshots import normalize_svg

    source = (
        '<svg id="terminal-123"><text clip-path="url(#terminal-123-line-0)">'
        "Thu, 30 Jul 2026 23:15:00 +0000</text></svg>"
    )
    assert normalize_svg(source, namespace="demo") == source.replace("123", "demo") + "\n"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "23:15:00",
        "Thu, 30 Jul 2026 23:15:01 +0000",
        "Thu, 30 Jul 2026 23:15:00 +0000</text><text "
        'clip-path="url(#terminal-123-line-0)">Thu, 30 Jul 2026 23:15:00 +0000',
    ],
)
def test_screenshot_normalizer_does_not_hide_a_missing_wrong_or_duplicate_clock(text):
    from scripts.generate_tui_screenshots import normalize_svg

    with pytest.raises(RuntimeError):
        normalize_svg(
            '<svg id="terminal-123"><text '
            f'clip-path="url(#terminal-123-line-0)">{text}</text></svg>',
            namespace="demo",
        )


@pytest.mark.parametrize(
    "header", ["", "Thu, 30 Jul 2026 23:15:01 +0000", "Thu, 30 Jul 2026 23:15:00 +0000"]
)
def test_screenshot_status_timestamp_cannot_substitute_for_the_header(header):
    from scripts.generate_tui_screenshots import normalize_svg

    source = (
        '<svg id="terminal-123"><text clip-path="url(#terminal-123-line-0)">'
        f'{header}</text><text clip-path="url(#terminal-123-line-4)">'
        "Thu, 30 Jul 2026 23:15:00 +0000</text></svg>"
    )
    if header == "Thu, 30 Jul 2026 23:15:00 +0000":
        assert normalize_svg(source, namespace="demo") == source.replace("123", "demo") + "\n"
    else:
        with pytest.raises(RuntimeError):
            normalize_svg(source, namespace="demo")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 9, 10, 23, 59, 59, tzinfo=UTC), "Thu, 10 Sep 2026 23:59:59 +0000"),
        (datetime(2026, 9, 11, tzinfo=UTC), "Fri, 11 Sep 2026 00:00:00 +0000"),
        (datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC), "Thu, 31 Dec 2026 23:59:59 +0000"),
        (datetime(2027, 1, 1, tzinfo=UTC), "Fri, 01 Jan 2027 00:00:00 +0000"),
        (datetime(2028, 2, 29, 12, 3, 4, 999999, tzinfo=UTC), "Tue, 29 Feb 2028 12:03:04 +0000"),
        (datetime(1, 1, 1, tzinfo=UTC), "Mon, 01 Jan 0001 00:00:00 +0000"),
        (
            datetime(2026, 9, 10, 20, 3, 4, tzinfo=timezone(timedelta(hours=-6))),
            "Fri, 11 Sep 2026 02:03:04 +0000",
        ),
        (
            datetime(2026, 9, 11, 1, 2, 3, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            "Thu, 10 Sep 2026 19:32:03 +0000",
        ),
    ],
)
def test_header_clock_uses_full_local_date_and_24_hour_time(value, expected):
    assert LocalHeaderClock(lambda: value).render().plain == expected


@pytest.mark.parametrize("value", [datetime(2026, 9, 10), None, "PRIVATE", True])
def test_unknown_wall_clock_never_fabricates_local_time_or_prints_raw_values(value):
    assert LocalHeaderClock(lambda: value).render().plain == "Local time unavailable"


def test_clock_failure_is_sanitized_and_later_render_recovers():
    clock = Mock(side_effect=[RuntimeError("PRIVATE"), datetime(2026, 9, 11, tzinfo=UTC)])
    widget = LocalHeaderClock(clock)
    assert widget.render().plain == "Local time unavailable"
    assert widget.render().plain == "Fri, 11 Sep 2026 00:00:00 +0000"


def test_local_conversion_overflow_does_not_crash_the_header():
    value = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
    assert LocalHeaderClock(lambda: value).render().plain == "Local time unavailable"


@pytest.mark.parametrize(
    ("zone", "instant", "expected"),
    [
        (
            "America/Denver",
            datetime(2026, 1, 11, 12, 26, 10, tzinfo=UTC),
            "Sun, 11 Jan 2026 05:26:10 -0700",
        ),
        (
            "America/Denver",
            datetime(2026, 9, 11, 12, 26, 10, tzinfo=UTC),
            "Fri, 11 Sep 2026 06:26:10 -0600",
        ),
        (
            "America/Denver",
            datetime(2026, 3, 8, 8, 59, 59, tzinfo=UTC),
            "Sun, 08 Mar 2026 01:59:59 -0700",
        ),
        ("America/Denver", datetime(2026, 3, 8, 9, tzinfo=UTC), "Sun, 08 Mar 2026 03:00:00 -0600"),
        (
            "America/Denver",
            datetime(2026, 11, 1, 7, 30, tzinfo=UTC),
            "Sun, 01 Nov 2026 01:30:00 -0600",
        ),
        (
            "America/Denver",
            datetime(2026, 11, 1, 8, 30, tzinfo=UTC),
            "Sun, 01 Nov 2026 01:30:00 -0700",
        ),
        (
            "Asia/Kathmandu",
            datetime(2026, 9, 11, 22, 26, 10, tzinfo=UTC),
            "Sat, 12 Sep 2026 04:11:10 +0545",
        ),
    ],
)
def test_clock_uses_host_timezone_dst_and_numeric_offset(host_timezone, zone, instant, expected):
    with host_timezone(zone):
        assert LocalHeaderClock(lambda: instant).render().plain == expected


def test_format_is_independent_of_locale_strftime(host_timezone):
    class NoStrftime(datetime):
        def strftime(self, format):
            # Numeric offsets are locale independent; weekday/month names are not.
            assert format == "%z"
            return super().strftime(format)

    with host_timezone("America/Denver"):
        instant = NoStrftime(2026, 9, 11, 12, 26, 10, tzinfo=UTC)
        assert LocalHeaderClock(lambda: instant).render().plain == (
            "Fri, 11 Sep 2026 06:26:10 -0600"
        )


@pytest.mark.parametrize("size", [(64, 24), (80, 32), (100, 30), (160, 45)])
@pytest.mark.parametrize("palette", [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])
def test_full_header_fits_without_moving_panels_and_tracks_clock_changes(size, palette):
    async def exercise():
        now = [datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)]
        app = ScannerTuiApp(
            ScannerIdentity(
                "sdsctl-remote-daemon",
                "SDS200",
                "Version 1.26.01",
                connection_target="192.0.2.18:50443",
            ),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            now=lambda: now[0],
            palette=palette,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            header = app.query_one(ScannerTuiHeader)
            clock = app.query_one(LocalHeaderClock)
            title = header.query_one("HeaderTitle")
            assert app.title == f"sdsctl v{__version__}"
            assert app.sub_title == ""
            assert header.region.height == clock.region.height == 1
            assert clock.region.width == 33
            assert clock.region.right == header.content_region.right
            assert title.region.right <= clock.region.x
            assert title.region.width >= len(app.title)
            assert len(clock.render().plain) == 31
            assert clock.render().plain == "Thu, 31 Dec 2026 23:59:59 +0000"
            assert "Thu, 31 Dec 2026 23:59:59 +0000" in app.export_screenshot().replace(
                "&#160;", " "
            )
            regions = {
                name: app.query_one(name).region
                for name in ("#connection", "#system", "#channel", "#status")
            }
            now[0] += timedelta(seconds=1)
            await pilot.pause(1.05)
            assert clock.render().plain == "Fri, 01 Jan 2027 00:00:00 +0000"
            assert "Fri, 01 Jan 2027 00:00:00 +0000" in app.export_screenshot().replace(
                "&#160;", " "
            )
            now[0] -= timedelta(hours=2)
            await pilot.pause(1.05)
            assert clock.render().plain == "Thu, 31 Dec 2026 22:00:00 +0000"
            assert "Thu, 31 Dec 2026 22:00:00 +0000" in app.export_screenshot().replace(
                "&#160;", " "
            )
            assert {name: app.query_one(name).region for name in regions} == regions

    asyncio.run(exercise())
