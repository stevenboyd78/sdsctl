from types import SimpleNamespace
from xml.sax.saxutils import quoteattr

import pytest

from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.radio import SDS200
from sds200.scanner_display_adapter import ScannerDisplayAdapter, ScannerDisplayAdapterError
from sds200.scanner_display_frame import project_scanner_display_frame
from sds200.scanner_quick_keys import QuickKeySelection

from .fakes import FakeTransport
from .test_scanner_display_adapter import ENDPOINT, info, live_adapter, store_for


@pytest.mark.parametrize("screen", ["conventional_scan", "trunk_scan"])
@pytest.mark.parametrize(
    "favorites,system,expected",
    [
        ("01", "23", QuickKeySelection(1, 23)),
        ("0", "0", QuickKeySelection(0, 0)),
        ("99", "99", QuickKeySelection(99, 99)),
        ("None", "None", QuickKeySelection(None, None)),
        ("01", "None", QuickKeySelection(1, None)),
        ("None", "23", None),
    ],
)
def test_exact_assigned_scan_scope_from_qualified_observation(screen, favorites, system, expected):
    adapter, session = live_adapter(
        info(
            screen,
            f'<MonitorList Index="123" Q_Key={quoteattr(favorites)}/>'
            f'<System Index="456" Q_Key={quoteattr(system)}/>',
        )
    )
    assert adapter.quick_key_selection(session, now=11) == expected
    # Context stays internal; no new frame wire keys or source indices.
    frame = project_scanner_display_frame(adapter.frame(store_for().snapshot(ENDPOINT), now=11))
    assert "quick_keys" not in frame


@pytest.mark.parametrize(
    "bad", ["", "0 ", " 1", "100", "000", "-1", "２", "unknown", "1\u200b", "x" * 300]
)
@pytest.mark.parametrize("tag", ["MonitorList", "System"])
def test_unqualified_selector_is_not_cast_to_a_key(bad, tag):
    records = "".join(
        f"<{name} Q_Key={quoteattr(bad if name == tag else '0')}/>"
        for name in ("MonitorList", "System")
    )
    adapter, session = live_adapter(info("trunk_scan", records))
    assert adapter.quick_key_selection(session, now=11) is None


@pytest.mark.parametrize(
    "content",
    [
        '<MonitorList Index="0"/><System Index="0"/>',
        '<MonitorList Q_Key="0"/>',
        '<System Q_Key="0"/>',
        '<MonitorList Q_Key="0"/><MonitorList Q_Key="1"/><System Q_Key="0"/>',
        '<MonitorList Q_Key="0"/><System Q_Key="0"/><System Q_Key="1"/>',
        '<MonitorList Q_Key="0"/><System Q_Key="0"/><PopupScreen/>',
        '<MonitorList Q_Key="0"/><System Q_Key="0"/><PlainText/>',
        '<MonitorList Q_Key="0"/><System Q_Key="0"/><ConvFrequency/><TGID/>',
    ],
)
def test_missing_ambiguous_or_overlaid_selection_cannot_drive_reads(content):
    adapter, session = live_adapter(info("trunk_scan", content))
    assert adapter.quick_key_selection(session, now=11) is None


@pytest.mark.parametrize(
    "screen", ["waterfall", "wx_alert", "quick_search", "tone_out", "menu_tree"]
)
def test_non_scan_modes_do_not_reuse_leftover_scope(screen):
    adapter, session = live_adapter(info(screen, '<MonitorList Q_Key="1"/><System Q_Key="23"/>'))
    assert adapter.quick_key_selection(session, now=11) is None


def test_selection_expires_and_session_tickets_cannot_be_reused():
    adapter, session = live_adapter(
        info("trunk_scan", '<MonitorList Q_Key="1"/><System Q_Key="23"/>')
    )
    assert adapter.quick_key_selection(session, now=11) == QuickKeySelection(1, 23)
    # Freshness begins at receipt (11), not session creation (10).
    assert adapter.quick_key_selection(session, now=15.999) == QuickKeySelection(1, 23)
    assert adapter.quick_key_selection(session, now=16) is None
    adapter.disconnect(session)
    new_session = adapter.begin_session(now=17)
    assert adapter.quick_key_selection(new_session, now=17) is None
    with pytest.raises(ScannerDisplayAdapterError):
        adapter.quick_key_selection(session, now=17)


@pytest.mark.parametrize("favorites_reply", ["valid", "rejected", "malformed", "timeout"])
def test_real_owner_parser_cache_and_idle_get_share_transport_without_blocking_psi(
    favorites_reply,
):
    """Complete fake wire observations/read responses; no physical scanner."""
    clock = SimpleNamespace(now=10.0)
    adapter = ScannerDisplayAdapter(ENDPOINT, stale_after=5)
    session = adapter.begin_session(now=10)
    seen = []

    class WireTransport(FakeTransport):
        def scan(self):
            self.feed_line("PSI,<XML>,")
            self.feed_line('<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">')
            self.feed_line('<MonitorList Q_Key="1"/><System Q_Key="23" Name="Demo"/>')
            self.feed_line(f'<TGID Name="Demo {len(self.writes)}"/>')
            self.feed_line("</ScannerInfo>")

        def write_command(self, command):
            super().write_command(command)
            self.scan()  # A complete PSI can arrive during the bank request.
            kind = command.split(",")[0]
            if kind == "FQK" and favorites_reply != "valid":
                if favorites_reply != "timeout":
                    self.feed_line("ERR" if favorites_reply == "rejected" else "FQK,x")
                return
            prefix = "" if kind == "FQK" else "1,23,"
            self.feed_line(kind + "," + prefix + ",".join("2" for _ in range(100)))

    transport = WireTransport()
    radio = SDS200.from_transport(transport)
    cache = DaemonQuickKeyCache(radio, ENDPOINT, transport.endpoint, clock=lambda: clock.now)
    ticket = cache.begin_session()

    def observe(sample):
        seen.append(sample.screen)
        adapter.observe(session, sample, sequence=len(seen), received_at=clock.now, now=clock.now)
        cache.observe(
            ticket, adapter.quick_key_selection(session, now=clock.now), sequence=len(seen)
        )

    unsubscribe = radio.on_psi(observe)
    try:
        with radio:
            transport.scan()
            assert transport.writes == []
            cache.request_refresh()
            quarantined = favorites_reply in ("malformed", "timeout")
            for index, moment in enumerate((10, 10.5, 11)):
                clock.now = moment
                assert cache.poll_once() is (index == 0 or not quarantined)
            expected = ["FQK"] if quarantined else ["FQK", "SQK,1", "DQK,1,23"]
            assert transport.writes == expected
            assert seen == ["trunk_scan"] * (1 + len(expected))
            snapshot = cache.snapshot()
            if favorites_reply == "valid":
                assert all(bank.states for bank in snapshot.banks)
            elif favorites_reply == "rejected":
                assert snapshot.banks[0].failure == "rejected"
                assert snapshot.banks[0].states is None
                assert all(bank.states for bank in snapshot.banks[1:])
            else:
                assert snapshot.blocked_until_reconnect in ("invalid_response", "timeout")
                assert not any(bank.states for bank in snapshot.banks)
            # Bank failure never changes the otherwise-current PSI frame.
            frame = adapter.frame(store_for().snapshot(ENDPOINT), now=11)
            assert any(value.text == f"Demo {len(expected)}" for value in frame.values)
    finally:
        unsubscribe()
        cache.close()
