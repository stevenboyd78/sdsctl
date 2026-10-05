"""Offline shared DTM/FQK scheduling; not periodic hardware acceptance."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from threading import Event, Thread, get_ident
from types import SimpleNamespace

import pytest

from sds200 import daemon_quick_keys
from sds200.daemon_display_frames import DaemonDisplayFrames
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.exceptions import CommandRejectedError, CommandTimeoutError, ProtocolError
from sds200.radio import SDS200
from sds200.scanner_display_profile_storage import DisplayProfileStorageError
from sds200.scanner_quick_keys import QuickKeySelection

from .fakes import FakeTransport
from .test_clock_reads import FIELDS
from .test_daemon_display_frames import configured as configured
from .test_daemon_display_frames import info
from .test_daemon_quick_key_worker import QuickScanner, bank, scan, texts, wait_for
from .test_daemon_quick_keys import ENDPOINT, TARGET, Scanner, activate, response, states
from .test_scanner_clock import reading


class SupplementalScanner(QuickScanner):
    def __init__(self):
        super().__init__()
        self.clock_reply = reading

    def read_clock_if_idle(self, *, timeout):
        self.reads.append(("DTM", get_ident(), timeout))
        return self.clock_reply()


@pytest.fixture
def shared():
    scanner, clock = SupplementalScanner(), SimpleNamespace(now=10.0)
    scanner.connected = True
    scanner.endpoint = TARGET
    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        TARGET,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
    )
    session = cache.begin_session()
    try:
        yield cache, session, scanner, clock
    finally:
        cache.close()


def wires(scanner):
    return [entry[0] for entry in scanner.reads]


def read_both(shared):
    cache, session, _, clock = shared
    activate(cache, session, QuickKeySelection(None, None))
    assert cache.poll_once()
    clock.now = 10.5
    assert cache.poll_once()
    assert states(cache)[0] and cache.clock_snapshot().local_time is not None


def test_clock_is_explicit_and_requires_the_existing_owner_reader():
    scanner = Scanner()  # Bank-only legacy owners need no clock method.
    cache = DaemonQuickKeyCache(scanner, ENDPOINT, TARGET)
    assert cache.clock_snapshot() is None and scanner.reads == []
    cache.close()
    with pytest.raises(ValueError, match="existing scanner owner"):
        DaemonQuickKeyCache(scanner, ENDPOINT, TARGET, include_clock=True)


@pytest.mark.parametrize("option", ["include_clock", "allow_scoped_reads"])
@pytest.mark.parametrize("value", [None, 1, 0, "false", [], object()])
def test_policy_requires_exact_booleans(option, value):
    with pytest.raises(ValueError, match="boolean"):
        DaemonQuickKeyCache(SupplementalScanner(), ENDPOINT, TARGET, **{option: value})


@pytest.mark.parametrize("selection", [QuickKeySelection(None, None), QuickKeySelection(0, 0)])
def test_one_fair_schedule_no_scoped_reads_no_snapshot_io(shared, selection):
    cache, session, scanner, clock = shared
    assert not cache.poll_once() and scanner.reads == []
    activate(cache, session, selection)
    for index, now in enumerate((10, 10.5, 12, 12.5, 14, 14.5)):
        clock.now = now
        cache.observe(session, selection, sequence=index + 2)
        for _ in range(30):
            cache.request_refresh()
            cache.snapshot()
            cache.clock_snapshot()
        assert len(scanner.reads) == index
        assert cache.poll_once()
        assert not cache.poll_once()
    assert wires(scanner) == ["FQK", "DTM"] * 3
    assert all(entry[-1] == 0.25 for entry in scanner.reads)
    assert states(cache)[1:] == (None, None)
    snapshot = cache.clock_snapshot()
    assert snapshot.local_time == datetime(2026, 9, 17, 21, 26, 59)
    assert snapshot.local_time.tzinfo is None
    assert "PRIVATE" not in repr(snapshot) and "fake://" not in repr(snapshot)


def test_global_scope_changes_do_not_starve_clock_or_refresh_banks_early(shared):
    cache, session, scanner, clock = shared
    read_both(shared)
    for sequence in range(2, 10):
        clock.now += 0.1
        cache.observe(session, QuickKeySelection(sequence, sequence), sequence=sequence)
        cache.request_refresh()
        assert cache.clock_snapshot().local_time is not None and states(cache)[0]
        assert not cache.poll_once()
    assert wires(scanner) == ["FQK", "DTM"]


def test_clock_can_share_legacy_scoped_schedule_without_a_second_worker():
    scanner, clock = SupplementalScanner(), SimpleNamespace(now=10.0)
    scanner.connected, scanner.endpoint = True, TARGET
    cache = DaemonQuickKeyCache(
        scanner, ENDPOINT, TARGET, clock=lambda: clock.now, include_clock=True
    )
    try:
        session = cache.begin_session()
        activate(cache, session, QuickKeySelection(0, 0))
        for now in (10, 10.5, 11, 11.5):
            clock.now = now
            assert cache.poll_once()
        assert wires(scanner) == ["FQK", "SQK,0", "DQK,0,0", "DTM"]
        assert all(states(cache)) and cache.clock_snapshot().local_time is not None
        cache.observe(session, QuickKeySelection(1, 23), sequence=2)
        assert not any(states(cache)) and cache.clock_snapshot().local_time is None
    finally:
        cache.close()


def test_minimum_gap_is_measured_from_completion(shared):
    cache, session, scanner, clock = shared
    activate(cache, session)

    def slow(command):
        clock.now += 0.125
        return response(command)

    scanner.reply = slow
    assert cache.poll_once()
    clock.now = 10.5
    assert not cache.poll_once()
    clock.now = 10.625
    assert cache.poll_once() and wires(scanner) == ["FQK", "DTM"]


def test_busy_lane_does_not_spin_or_quarantine(shared):
    cache, session, scanner, clock = shared
    activate(cache, session)
    scanner.reply, scanner.clock_reply = lambda _: None, lambda: None
    assert cache.poll_once()
    clock.now = 10.5
    assert cache.poll_once()
    for _ in range(50):
        assert not cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect is None
    assert cache.clock_snapshot().blocked_until_reconnect is None
    assert wires(scanner) == ["FQK", "DTM"]
    scanner.reply, scanner.clock_reply = response, reading
    clock.now = 11
    assert cache.poll_once()
    clock.now = 12.5
    assert cache.poll_once()
    assert wires(scanner) == ["FQK", "DTM", "FQK", "DTM"]


@pytest.mark.parametrize("renew", ["psi", "demand", "both", "neither"])
def test_clock_freshness_demand_and_psi_are_independent(shared, renew):
    cache, session, scanner, clock = shared
    read_both(shared)
    exact = cache.clock_snapshot().local_time
    clock.now = 14
    if renew in ("psi", "both"):
        cache.observe(session, QuickKeySelection(None, None), sequence=2)
    if renew in ("demand", "both"):
        cache.request_refresh()
    assert cache.clock_snapshot().local_time == exact  # Never ticks/extrapolates.
    clock.now = 15
    assert (cache.clock_snapshot().local_time is not None) == (renew == "both")
    clock.now = 15.5
    assert cache.clock_snapshot().local_time is None  # Dispatch age, not latest PSI.
    assert wires(scanner) == ["FQK", "DTM"]


def test_invalid_rtc_replaces_valid_clock_without_quarantine(shared):
    cache, _, scanner, clock = shared
    read_both(shared)
    scanner.clock_reply = lambda: reading(("0", "0000", "00", "00", "00", "00", "00", "0"))
    clock.now = 12
    cache.poll_once()
    clock.now = 12.5
    cache.poll_once()
    assert cache.clock_snapshot().local_time is None
    assert cache.clock_snapshot().rtc_valid is False
    assert cache.snapshot().blocked_until_reconnect is None


@pytest.mark.parametrize("source", ["FQK", "DTM"])
@pytest.mark.parametrize(
    "error,expected",
    [
        (CommandTimeoutError, "timeout"),
        (ProtocolError, "invalid_response"),
        (OSError, "read_error"),
    ],
)
def test_any_uncertain_read_quarantines_both_until_real_reconnect(shared, source, error, expected):
    cache, session, scanner, clock = shared
    read_both(shared)

    def fail(*_):
        raise error("PRIVATE_FAILURE")

    if source == "FQK":
        scanner.reply = fail
        clock.now = 12
    else:
        clock.now = 12
        cache.poll_once()
        scanner.clock_reply = fail
        clock.now = 12.5
    assert cache.poll_once()
    assert not any(states(cache)) and cache.clock_snapshot().local_time is None
    assert cache.snapshot().blocked_until_reconnect == expected
    assert cache.clock_snapshot().blocked_until_reconnect == expected
    cache.suspend(session)
    clock.now = 13
    activate(cache, session, sequence=3)
    assert not cache.poll_once()
    assert "PRIVATE" not in repr(cache.snapshot()) + repr(cache.clock_snapshot())
    scanner.reply, scanner.clock_reply = response, reading
    cache.disconnect(session)
    activate(cache, cache.begin_session())
    assert cache.poll_once()
    clock.now = 13.5
    assert cache.poll_once()
    assert states(cache)[0] and cache.clock_snapshot().local_time is not None
    assert cache.snapshot().blocked_until_reconnect is None


@pytest.mark.parametrize(
    "bad", [object(), replace(reading(), rtc_valid=1), replace(reading(), local_time=None)]
)
def test_clock_store_validation_quarantines_shared_schedule(shared, bad):
    cache, session, scanner, clock = shared
    activate(cache, session)
    cache.poll_once()
    scanner.clock_reply = lambda: bad
    clock.now = 10.5
    assert cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect == "invalid_response"
    assert not any(states(cache)) and cache.clock_snapshot().local_time is None


@pytest.mark.parametrize("source", ["FQK", "DTM"])
def test_definite_rejection_backs_off_one_operation_across_barriers(shared, source):
    cache, session, scanner, clock = shared

    def rejected(*_):
        raise CommandRejectedError("PRIVATE_REJECTION")

    if source == "FQK":
        scanner.reply = rejected
    else:
        scanner.clock_reply = rejected
    activate(cache, session)
    cache.poll_once()
    clock.now = 10.5
    cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect is None
    scanner.reply, scanner.clock_reply = response, reading
    for sequence, now in enumerate(range(11, 40), start=2):
        clock.now = now
        cache.suspend(session)  # Profile/mode barrier cannot evade global backoff.
        activate(cache, session, sequence=sequence)
        cache.poll_once()
    assert wires(scanner).count(source) == 1
    assert len(scanner.reads) > 4  # The other global operation can still progress.
    clock.now = 40.5
    activate(cache, session, sequence=40)
    assert cache.poll_once()
    clock.now = 41
    cache.poll_once()  # Another overdue operation may fairly go first.
    assert wires(scanner).count(source) == 2


@pytest.mark.parametrize("source", ["FQK", "DTM"])
def test_inflight_rejection_cannot_lose_global_backoff_at_a_barrier(shared, source):
    cache, session, scanner, clock = shared
    activate(cache, session)

    def rejected(*_):
        cache.suspend(session)
        activate(cache, session, sequence=2)
        raise CommandRejectedError("PRIVATE_REJECTION")

    if source == "FQK":
        scanner.reply = rejected
    else:
        cache.poll_once()
        clock.now = 10.5
        scanner.clock_reply = rejected
    cache.poll_once()
    scanner.reply, scanner.clock_reply = response, reading
    for sequence, now in enumerate(range(11, 40), start=3):
        clock.now = now
        activate(cache, session, sequence=sequence)
        cache.poll_once()
    assert wires(scanner).count(source) == 1
    assert cache.snapshot().blocked_until_reconnect is None


@pytest.mark.parametrize(
    "action", ["mode", "profile", "demand", "disconnect", "reconnect", "close"]
)
@pytest.mark.parametrize("uncertain", [False, True])
def test_inflight_clock_shares_one_slot_and_cannot_cross_barriers(shared, action, uncertain):
    cache, session, scanner, clock = shared
    activate(cache, session)
    cache.poll_once()
    clock.now = 10.5
    entered, release = Event(), Event()

    def delayed():
        entered.set()
        assert release.wait(2)
        if uncertain:
            raise CommandTimeoutError("PRIVATE_LATE_RESPONSE")
        return reading()

    scanner.clock_reply = delayed
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(cache.poll_once)
        try:
            assert entered.wait(1)
            assert pool.submit(cache.snapshot).result(timeout=0.5).active
            assert pool.submit(cache.clock_snapshot).result(timeout=0.5).local_time is None
            if action == "mode":
                cache.observe(session, None, sequence=2)
            elif action == "profile":
                cache.suspend(session)
            elif action == "demand":
                cache.clear_demand()
            elif action in ("disconnect", "reconnect"):
                cache.disconnect(session)
                if action == "reconnect":
                    activate(cache, cache.begin_session())
            else:
                cache.close()
            assert not cache.poll_once()
            assert cache.clock_snapshot().local_time is None
        finally:
            release.set()
        assert work.result(timeout=1)
    assert cache.clock_snapshot().local_time is None
    assert not any(states(cache))
    assert wires(scanner) == ["FQK", "DTM"]
    if action in ("mode", "profile", "demand"):
        assert cache.snapshot().blocked_until_reconnect == ("timeout" if uncertain else None)
    if action == "reconnect":
        assert cache.snapshot().blocked_until_reconnect is None
        scanner.clock_reply = reading
        clock.now = 11
        assert cache.poll_once()
        clock.now = 11.5
        assert cache.poll_once() and cache.clock_snapshot().local_time is not None


@pytest.fixture
def engine(configured, monkeypatch):
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.01)
    scanner, clock = SupplementalScanner(), SimpleNamespace(now=10.0)
    profile = DaemonDisplayProfile(configured, lambda: scanner.endpoint)
    cache = DaemonQuickKeyCache(
        scanner,
        configured.binding.endpoint_id,
        scanner.endpoint,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
    )
    feed = DaemonDisplayFrames(profile, scanner, clock=lambda: clock.now, quick_keys=cache)
    feed.start()
    scanner.connect_event(True)
    scanner.sample(scan("None", "None"))
    try:
        yield feed, cache, profile, scanner, clock
    finally:
        feed.close()


def worker_read_both(engine):
    feed, _, _, _, clock = engine
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    clock.now = 10.5
    wait_for(lambda: feed.clock_snapshot().local_time is not None)


def test_feed_has_one_background_thread_no_new_wire_keys_or_reading_in_callbacks(engine):
    feed, _, _, scanner, _ = engine
    assert scanner.reads == []
    worker_read_both(engine)
    assert wires(scanner) == ["FQK", "DTM"]
    assert len({thread for _, thread, _ in scanner.reads}) == 1
    assert scanner.reads[0][1] != get_ident()
    assert len(scanner.events._callbacks["psi"]) == 1
    snapshot = feed.snapshot()
    assert "Current channel" in texts(snapshot)
    assert "clock" not in snapshot and "quick_keys" not in snapshot
    feed.close()
    assert feed.clock_snapshot().local_time is None
    assert not feed.quick_key_worker_status().alive


def test_profile_repair_invalidates_both_and_requires_new_psi(engine, configured):
    feed, _, profile, scanner, clock = engine
    worker_read_both(engine)
    state = configured.state_directory / "accepted-profile.json"
    original = state.read_bytes()
    state.write_bytes(b"broken")
    with pytest.raises(DisplayProfileStorageError):
        profile.reload()
    state.write_bytes(original)
    profile.reload()
    wait_for(lambda: not feed.quick_key_snapshot().active)
    assert feed.clock_snapshot().local_time is None and not bank(feed)
    clock.now = 11
    feed.snapshot()
    Event().wait(0.04)
    assert wires(scanner) == ["FQK", "DTM"]
    scanner.sample(scan("0", "0"))
    feed.snapshot()
    clock.now = 12
    wait_for(lambda: bank(feed) is not None)
    clock.now = 12.5
    wait_for(lambda: feed.clock_snapshot().local_time is not None)
    assert wires(scanner) == ["FQK", "DTM"] * 2


@pytest.mark.parametrize(
    "sample",
    [
        info("waterfall", ""),
        info(content="<PopupScreen/>"),
        info(content='<MonitorList Q_Key="None"/><System Q_Key="0"/>'),
        info(content='<MonitorList Q_Key="0"/><System Q_Key="0"/><System Q_Key="1"/>'),
        info(content='<MonitorList Index="0"/><System Index="0"/>'),
        object(),
    ],
)
def test_global_only_worker_still_requires_unambiguous_normal_scan_psi(engine, sample):
    feed, _, _, scanner, clock = engine
    worker_read_both(engine)
    scanner.sample(sample)
    clock.now = 13
    for _ in range(10):
        feed.snapshot()
    Event().wait(0.04)
    assert feed.clock_snapshot().local_time is None and not bank(feed)
    assert wires(scanner) == ["FQK", "DTM"]


def test_duplicate_connect_cannot_lift_shared_clock_quarantine(engine):
    feed, _, _, scanner, clock = engine

    def failure():
        raise CommandTimeoutError("PRIVATE_CLOCK_TIMEOUT")

    scanner.clock_reply = failure
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    clock.now = 10.5
    wait_for(lambda: feed.quick_key_snapshot().blocked_until_reconnect == "timeout")
    assert "Current channel" in texts(feed.snapshot())
    scanner.connect_event(True)
    clock.now = 13
    scanner.sample(scan("0", "0"))
    feed.snapshot()
    Event().wait(0.04)
    assert wires(scanner) == ["FQK", "DTM"]
    scanner.clock_reply = reading
    scanner.connect_event(False)
    scanner.connect_event(True)
    scanner.sample(scan("None", "None"))
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    clock.now = 13.5
    wait_for(lambda: feed.clock_snapshot().local_time is not None)
    assert wires(scanner) == ["FQK", "DTM"] * 2


def test_clock_elapsed_deadline_quarantines_even_with_valid_reply(shared):
    cache, session, scanner, clock = shared
    activate(cache, session)
    cache.poll_once()

    def late():
        clock.now += 0.25
        return reading()

    clock.now = 10.5
    scanner.clock_reply = late
    cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect == "timeout"
    assert not any(states(cache)) and cache.clock_snapshot().local_time is None


@pytest.mark.parametrize("change", ["endpoint", "disconnect"])
def test_target_change_during_clock_read_invalidates_all_values(shared, change):
    cache, session, scanner, clock = shared
    activate(cache, session)
    cache.poll_once()

    def changed():
        if change == "endpoint":
            scanner.endpoint = "fake://other-owner"
        else:
            scanner.connected = False
        return reading()

    scanner.clock_reply = changed
    clock.now = 10.5
    cache.poll_once()
    assert not cache.snapshot().active and not any(states(cache))
    assert cache.clock_snapshot().local_time is None
    scanner.endpoint, scanner.connected = TARGET, True
    clock.now = 11
    cache.request_refresh()
    assert not cache.poll_once()  # Requires a real new connection ticket/PSI.


def test_clock_base_exception_releases_slot_and_quarantines_same_connection(shared):
    cache, session, scanner, clock = shared
    activate(cache, session)
    cache.poll_once()

    def interrupted():
        raise KeyboardInterrupt("PRIVATE_EXCEPTION")

    scanner.clock_reply = interrupted
    clock.now = 10.5
    with pytest.raises(KeyboardInterrupt):
        cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect == "read_error"
    scanner.clock_reply = reading
    cache.disconnect(session)
    activate(cache, cache.begin_session())
    clock.now = 11
    cache.poll_once()
    clock.now = 11.5
    cache.poll_once()
    assert cache.clock_snapshot().local_time is not None


def test_shared_gets_yield_to_real_owner_lane_and_allow_psi_during_reply(configured):
    entered, release = Event(), Event()
    clock = SimpleNamespace(now=10.0)

    class WireTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            if command == "VOL":
                entered.set()
                assert release.wait(3)
                self.feed_line("VOL,3")
                return
            assert command in ("FQK", "DTM")
            complete = Event()

            def incoming():
                self.feed_line("PSI,<XML>,")
                self.feed_line('<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">')
                self.feed_line('<MonitorList Q_Key="None"/><System Q_Key="None"/>')
                self.feed_line('<TGID Name="Arrived during GET"/></ScannerInfo>')
                fields = ["2"] * 100 if command == "FQK" else FIELDS
                self.feed_line(command + "," + ",".join(fields))
                complete.set()

            thread = Thread(target=incoming)
            thread.start()
            try:
                assert complete.wait(0.2), "GET held the cache or PSI callback lock"
            finally:
                thread.join(1)

    transport = WireTransport(TARGET)
    radio = SDS200.from_transport(transport)
    cache = DaemonQuickKeyCache(
        radio,
        configured.binding.endpoint_id,
        TARGET,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
    )
    # Drive the scheduler deterministically while using the real parser/lane.
    session = cache.begin_session()
    sequence = 1

    def observation(_):
        nonlocal sequence
        sequence += 1
        cache.observe(session, QuickKeySelection(None, None), sequence=sequence)

    unsubscribe = radio.on_psi(observation)
    try:
        with radio, ThreadPoolExecutor(max_workers=2) as pool:
            activate(cache, session, QuickKeySelection(None, None))
            foreground = pool.submit(radio.get_volume, timeout=3)
            try:
                assert entered.wait(1)
                assert pool.submit(cache.poll_once).result(timeout=0.2)
                clock.now = 10.5
                assert pool.submit(cache.poll_once).result(timeout=0.2)
                assert transport.writes == ["VOL"]
            finally:
                release.set()
            assert foreground.result(timeout=1) == 3
            clock.now = 11
            assert cache.poll_once()
            clock.now = 12.5
            assert cache.poll_once()
            assert transport.writes == ["VOL", "FQK", "DTM"]
            assert sequence == 3
            assert states(cache)[0] and cache.clock_snapshot().local_time is not None
    finally:
        cache.close()
        unsubscribe()


def test_clock_worker_fault_isolated_and_cannot_self_restart(engine):
    feed, _, _, scanner, clock = engine

    def interrupted():
        raise KeyboardInterrupt("PRIVATE_WORKER_FAULT")

    scanner.clock_reply = interrupted
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    clock.now = 10.5
    wait_for(lambda: feed.quick_key_worker_status().failure == "worker_failed")
    assert "Current channel" in texts(feed.snapshot())
    assert feed.clock_snapshot().local_time is None
    scanner.connect_event(False)
    scanner.connect_event(True)
    scanner.sample(scan())
    clock.now = 13
    assert "Current channel" in texts(feed.snapshot())
    Event().wait(0.04)
    assert wires(scanner) == ["FQK", "DTM"]
    assert "PRIVATE" not in repr(feed.quick_key_worker_status())


def test_clock_worker_close_is_bounded_and_late_reply_cannot_restore_data(engine):
    feed, _, _, scanner, clock = engine
    entered, release = Event(), Event()

    def delayed():
        entered.set()
        assert release.wait(3)
        return reading()

    scanner.clock_reply = delayed
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    clock.now = 10.5
    try:
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(feed.close).result(timeout=1.25)
        assert feed.clock_snapshot().local_time is None
        assert not bank(feed)
        assert scanner.connected  # Closing supplemental reads never closes scanner.
    finally:
        release.set()
    wait_for(lambda: not feed.quick_key_worker_status().alive)
    assert feed.clock_snapshot().local_time is None
    assert wires(scanner) == ["FQK", "DTM"]
