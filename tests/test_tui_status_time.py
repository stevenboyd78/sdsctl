"""Status-observation time is not the daemon's or scanner transport's uptime."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from rich.cells import cell_len
from textual.widgets import Static

from sds200.state import snapshot_from_scanner_info
from sds200.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME
from sds200.tui import ScannerIdentity, ScannerTuiApp
from sds200.xml_protocol import ScannerInfoParser

from .test_tui import XML, _plain


def app_at(now, *, remote=False, palette=DEFAULT_DARK_THEME):
    return ScannerTuiApp(
        ScannerIdentity("sdsctl-remote-daemon" if remote else "/dev/ttyACM0",
                        "SDS200" if remote else "SDS100", "Version 1.26.01",
                        connection_target="[2001:db8::18]:50443" if remote else None),
        snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
        now=now, palette=palette,
    )


def test_status_observation_is_fixed_until_label_changes_not_elapsed_uptime():
    now = Mock(return_value=datetime(2026, 12, 31, 18, 59, 59,
                                    tzinfo=timezone(timedelta(hours=-5))))
    app = app_at(now)
    assert app._transition_stamp("connection", "CONNECTED") == "2026-12-31T23:59:59Z"
    now.return_value = datetime(2027, 3, 1, tzinfo=UTC)
    assert app._transition_stamp("connection", "CONNECTED") == "2026-12-31T23:59:59Z"
    assert now.call_count == 1
    assert app._transition_stamp("connection", "DEGRADED") == "2027-03-01T00:00:00Z"
    # Availability has an independent observation, not a shared uptime origin.
    assert app._transition_stamp("availability", "AVAILABLE") == "2027-03-01T00:00:00Z"
    now.return_value = datetime(2027, 2, 28, 22, tzinfo=UTC)
    assert app._transition_stamp("connection", "DEGRADED") == "2027-03-01T00:00:00Z"
    assert app._transition_stamp("connection", "CONNECTED") == "2027-02-28T22:00:00Z"
    assert app._transition_stamp("availability", "AVAILABLE") == "2027-03-01T00:00:00Z"
    assert app_at(now)._transition_stamp("connection", "CONNECTED") == "2027-02-28T22:00:00Z"


@pytest.mark.parametrize("bad", [None, True, "PRIVATE", datetime(2026, 1, 1),
    datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))), RuntimeError("PRIVATE")])
def test_unknown_status_start_stays_unknown_until_a_new_status(bad):
    now = Mock(side_effect=[bad, datetime(2027, 1, 1, tzinfo=UTC)])
    app = app_at(now)
    assert app._transition_stamp("connection", "CONNECTED") == "UTC time unavailable"
    assert app._transition_stamp("connection", "CONNECTED") == "UTC time unavailable"
    assert now.call_count == 1
    assert app._transition_stamp("connection", "DISCONNECTED") == "2027-01-01T00:00:00Z"


@pytest.mark.parametrize("size", [(80, 32), (100, 30), (120, 40), (160, 45)])
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("palette", [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])
def test_utc_status_row_fits_and_repeated_frames_do_not_move_panels(size, remote, palette):
    async def exercise():
        now = [datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)]
        app = app_at(lambda: now[0], remote=remote, palette=palette)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            connection = app.query_one("#connection", Static)
            short = size[1] < 32
            prefix = "Status: CONNECTED @ " if short else "Status since: "
            assert prefix + "2026-12-31T23:59:59Z" in _plain(connection)
            assert len(_plain(connection).splitlines()) == 3 + int(remote) - int(short)
            assert ("Target:" in _plain(connection)) is remote
            assert all(cell_len(line) <= connection.content_region.width
                       for line in _plain(connection).splitlines())
            assert len(_plain(connection).splitlines()) <= connection.content_region.height
            if remote:
                assert "2001:db8::18" in app.export_screenshot()
            regions = {name: app.query_one(name).region for name in
                       ("#connection", "#system", "#channel", "#state", "#status")}
            now[0] += timedelta(days=2, seconds=1)
            app.update_snapshot(app._snapshot, connected=True)
            await pilot.press("t")
            await pilot.pause()
            assert prefix + "2026-12-31T23:59:59Z" in _plain(connection)
            assert {name: app.query_one(name).region for name in regions} == regions
            app._apply_connection(False)
            await pilot.pause()
            assert "DISCONNECTED" in _plain(connection)
            assert "2027-01-03T00:00:00Z" in _plain(connection)
            assert connection.region == regions["#connection"]
            # The full dates must actually be visible, not only in a renderable.
            assert "2027-01-03T00:00:00Z" in app.export_screenshot()
            if size[0] >= 120:
                status = app.query_one("#status", Static)
                assert "UNAVAILABLE @ 2027-01-03T00:00:00Z" in _plain(status)
                assert all(cell_len(line) <= status.content_region.width
                           for line in _plain(status).splitlines()[:2])
            await pilot.press("g")
            await pilot.pause()
            assert connection.region == regions["#connection"]
    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(100, 30), (160, 45)])
def test_remote_timestamp_preserves_scanner_panel_with_audio_and_drawers(tmp_path, size):
    from sds200.audio import AudioStream
    from sds200.tui_audio import RecordingPathPolicy, TuiAudioSession

    from .fakes import FakeAudioTransport

    async def exercise():
        session = TuiAudioSession(AudioStream(FakeAudioTransport()),
                                  RecordingPathPolicy(directory=tmp_path))
        app = ScannerTuiApp(
            ScannerIdentity("sdsctl-remote-daemon", "SDS200", "Version 1.26.01",
                            connection_target="192.0.2.25:50443"),
            snapshot_from_scanner_info(ScannerInfoParser().parse("GSI", XML)),
            now=lambda: datetime(2027, 1, 1, tzinfo=UTC), audio_session=session)
        async with app.run_test(size=size) as pilot:
            for key in (None, "g", "question_mark", "question_mark"):
                if key:
                    await pilot.press(key)
                await pilot.pause()
                if not app.key_help_visible:
                    body = app.query_one("#body")
                    identity = app.query_one("#identity")
                    assert identity.region.bottom <= body.content_region.bottom
                    assert body.max_scroll_y == 0
                connection = app.query_one("#connection", Static)
                assert len(_plain(connection).splitlines()) <= connection.content_region.height
                svg = app.export_screenshot()
                if not app.key_help_visible:
                    assert "192.0.2.25:50443" in svg
                    assert "2027-01-01T00:00:00Z" in svg
    asyncio.run(exercise())
