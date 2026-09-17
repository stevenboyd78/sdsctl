"""Opt-in owner integration only; ordinary daemon startup remains passive."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Thread, get_ident
from time import monotonic
from types import SimpleNamespace
from uuid import UUID

import pytest

from sds200 import daemon_quick_keys
from sds200.daemon_display_frames import DaemonDisplayFrames
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.exceptions import CommandTimeoutError
from sds200.radio import SDS200
from sds200.scanner_display_profile_storage import (
    DisplayProfileStorageError,
    initialize_display_profile_storage,
)

from .fakes import FakeTransport
from .test_daemon_display_frames import TARGET, Scanner, info, texts
from .test_daemon_display_frames import configured as configured
from .test_daemon_quick_keys import response


def wait_for(predicate):
    deadline = monotonic() + 2
    while not predicate():
        assert monotonic() < deadline, "Background condition did not complete"
        Event().wait(0.005)


def scan(favorites="1", system="23", name="Current channel"):
    return info(
        content=(
            f'<MonitorList Q_Key="{favorites}"/><System Q_Key="{system}" Name="Current system"/>'
            f'<TGID Name="{name}"/>'
        )
    )


class QuickScanner(Scanner):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.reply = response

    def read_quick_keys_if_idle(self, command, *, timeout):
        self.reads.append((command.wire, get_ident(), timeout))
        return self.reply(command)


@pytest.fixture
def engine(configured, monkeypatch):
    # Accelerate worker wakes, not freshness or response budgets.
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.01)
    scanner, clock = QuickScanner(), SimpleNamespace(now=10.0)
    profile = DaemonDisplayProfile(configured, lambda: scanner.endpoint)
    cache = DaemonQuickKeyCache(
        scanner, configured.binding.endpoint_id, TARGET, clock=lambda: clock.now
    )
    feed = DaemonDisplayFrames(profile, scanner, clock=lambda: clock.now, quick_keys=cache)
    feed.start()
    scanner.connect_event(True)
    scanner.sample(scan())
    try:
        yield feed, cache, profile, scanner, clock
    finally:
        feed.close()


def bank(feed, index=0):
    return feed.quick_key_snapshot().banks[index].states


def test_default_feed_stays_passive_even_with_qualified_scope(configured):
    scanner = QuickScanner()
    feed = DaemonDisplayFrames(DaemonDisplayProfile(configured, lambda: TARGET), scanner)
    feed.start()
    try:
        scanner.connect_event(True)
        scanner.sample(scan())
        for _ in range(20):
            assert "Current channel" in texts(feed.snapshot())
        assert feed.quick_key_snapshot() is feed.quick_key_worker_status() is None
        assert feed.clock_snapshot() is None
        assert scanner.reads == []
    finally:
        feed.close()


def test_opt_in_worker_coalesces_display_reads_and_uses_only_background_gets(engine):
    feed, _, _, scanner, clock = engine
    main_thread = get_ident()
    assert scanner.reads == []  # PSI and connection callback perform no GET.
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: feed.snapshot(), range(40)))
    wait_for(lambda: bank(feed) is not None)
    for index, now in ((1, 10.5), (2, 11)):
        clock.now = now
        wait_for(lambda index=index: bank(feed, index) is not None)
    assert [wire for wire, _, _ in scanner.reads] == ["FQK", "SQK,1", "DQK,1,23"]
    assert all(thread != main_thread and timeout == 0.25 for _, thread, timeout in scanner.reads)
    assert len(scanner.events._callbacks["psi"]) == 1
    assert all("Current channel" in texts(snapshot) for snapshot in snapshots)
    assert all(set(snapshot) == set(snapshots[0]) for snapshot in snapshots)
    assert "quick_keys" not in snapshots[0]  # No new wire contract or rendering.


@pytest.mark.parametrize(
    "sample",
    [
        info("waterfall", ""),
        info(content="<PopupScreen/>"),
        info(content='<MonitorList Q_Key="0"/><System Q_Key="0"/><System Q_Key="1"/>'),
        info(content='<MonitorList Index="0"/><System Index="0"/>'),
        object(),
    ],
)
def test_display_demand_cannot_authorize_invalid_or_non_scan_scope(engine, sample):
    feed, _, _, scanner, _ = engine
    scanner.sample(sample)
    for _ in range(10):
        feed.snapshot()
    Event().wait(0.04)
    assert scanner.reads == [] and not feed.quick_key_snapshot().active


@pytest.mark.parametrize("renew", ["psi", "demand", "neither"])
def test_worker_stops_reads_when_either_demand_or_psi_expires(engine, renew):
    feed, _, _, scanner, clock = engine
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    clock.now = 14
    if renew == "psi":
        scanner.sample(scan())
    elif renew == "demand":
        feed.snapshot()
    clock.now = 15
    wait_for(lambda: not feed.quick_key_snapshot().active)
    count = len(scanner.reads)
    Event().wait(0.04)
    assert len(scanner.reads) == count
    assert not any(b.states for b in feed.quick_key_snapshot().banks)
    # Internal diagnostics did not renew demand; fresh PSI and a view read do.
    clock.now = 16
    scanner.sample(scan())
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)


def test_cache_timeout_does_not_blank_display_or_reset_on_duplicate_connect(engine):
    feed, _, _, scanner, clock = engine

    def timeout(_):
        raise CommandTimeoutError("PRIVATE_EXCEPTION")

    scanner.reply = timeout
    feed.snapshot()
    wait_for(lambda: feed.quick_key_snapshot().blocked_until_reconnect == "timeout")
    assert "Current channel" in texts(feed.snapshot())
    scanner.connect_event(True)  # Not a new connection.
    clock.now = 11
    scanner.sample(scan("2", "4"))
    feed.snapshot()
    Event().wait(0.04)
    assert len(scanner.reads) == 1
    scanner.reply = response
    old_callback = scanner.events._callbacks["psi"][0]
    scanner.connect_event(False)
    scanner.connect_event(True)
    old_callback(scan())
    assert feed.snapshot()["frames"]["preferred"]["status"] == "waiting"
    scanner.sample(scan())
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    assert feed.quick_key_snapshot().blocked_until_reconnect is None


@pytest.mark.parametrize("action", ["scope", "disconnect", "invalid", "close"])
def test_inflight_worker_never_blocks_callbacks_or_commits_after_context_change(engine, action):
    feed, _, _, scanner, _ = engine
    entered, release = Event(), Event()

    def delayed(command):
        entered.set()
        assert release.wait(3)
        return response(command)

    scanner.reply = delayed
    feed.snapshot()
    try:
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert "Current channel" in texts(pool.submit(feed.snapshot).result(timeout=0.5))
            if action == "scope":
                pool.submit(scanner.sample, scan("2", "4")).result(timeout=0.5)
            elif action == "disconnect":
                pool.submit(scanner.connect_event, False).result(timeout=0.5)
            elif action == "invalid":
                pool.submit(scanner.sample, object()).result(timeout=0.5)
            else:
                pool.submit(feed.close).result(timeout=1.25)
                assert feed.quick_key_worker_status().stopped
                assert feed.quick_key_worker_status().alive  # Deliberately bad transport.
        assert not any(b.states for b in feed.quick_key_snapshot().banks)
    finally:
        release.set()
    if action == "close":
        wait_for(lambda: not feed.quick_key_worker_status().alive)
    else:
        Event().wait(0.04)
    assert not any(b.states for b in feed.quick_key_snapshot().banks)
    assert len(scanner.reads) == 1


@pytest.mark.parametrize("method", ["observe", "request_refresh", "poll_once", "begin_session"])
def test_optional_component_fault_never_breaks_scanner_display_or_reconnect(
    engine, monkeypatch, method
):
    feed, cache, _, scanner, _ = engine

    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_EXCEPTION")

    monkeypatch.setattr(cache, method, broken)
    if method == "begin_session":
        scanner.connect_event(False)
        scanner.connect_event(True)
    scanner.sample(scan())
    assert "Current channel" in texts(feed.snapshot())
    wait_for(lambda: feed.quick_key_worker_status().failure == "worker_failed")
    scanner.connect_event(False)
    scanner.connect_event(True)
    scanner.sample(scan(name="After reconnect"))
    assert "After reconnect" in texts(feed.snapshot())
    assert "PRIVATE_EXCEPTION" not in repr(feed.quick_key_worker_status())
    assert feed.snapshot()["failure"] is None


def test_worker_start_failure_isolated_and_cannot_restart_on_connection(engine, monkeypatch):
    # Reuse the already-qualified owner/profile in a separate, stopped feed.
    old_feed, _, profile, scanner, clock = engine
    old_feed.close()
    cache = DaemonQuickKeyCache(
        scanner, profile.frame_context()[0].endpoint_id, TARGET, clock=lambda: clock.now
    )
    feed = DaemonDisplayFrames(profile, scanner, clock=lambda: clock.now, quick_keys=cache)

    def failed_start(_):
        raise RuntimeError("PRIVATE_START_ERROR")

    monkeypatch.setattr(Thread, "start", failed_start)
    try:
        feed.start()
        assert feed.quick_key_worker_status().failure == "worker_start_failed"
        scanner.sample(scan())
        assert "Current channel" in texts(feed.snapshot())
        scanner.connect_event(False)
        scanner.connect_event(True)
        scanner.sample(scan())
        assert "Current channel" in texts(feed.snapshot())
    finally:
        feed.close()


def test_profile_failure_repair_barrier_requires_fresh_psi_without_view_reads(engine, configured):
    feed, _, profile, scanner, clock = engine
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    state = configured.state_directory / "accepted-profile.json"
    original = state.read_bytes()
    state.write_bytes(b"broken")
    with pytest.raises(DisplayProfileStorageError):
        profile.reload()
    state.write_bytes(original)
    profile.reload()
    wait_for(lambda: not feed.quick_key_snapshot().active)
    assert not bank(feed) and not texts(feed.snapshot())
    clock.now = 11
    scanner.sample(scan())
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)


def test_slow_profile_context_does_not_hold_callback_or_scanner_lane(engine, monkeypatch):
    feed, _, profile, scanner, _ = engine
    original = profile.frame_context
    entered, release = Event(), Event()

    def delayed():
        entered.set()
        assert release.wait(3)
        return original()

    monkeypatch.setattr(profile, "frame_context", delayed)
    try:
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(scanner.sample, scan()).result(timeout=0.5)
            pool.submit(feed.close).result(timeout=1.25)
        assert scanner.reads == []
    finally:
        release.set()
    wait_for(lambda: not feed.quick_key_worker_status().alive)


def test_missing_accepted_profile_never_renews_bank_demand(configured, tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.01)
    config = replace(configured, state_directory=tmp_path / "empty")
    initialize_display_profile_storage(config.state_directory, config.binding.endpoint_id)
    scanner = QuickScanner()
    cache = DaemonQuickKeyCache(scanner, config.binding.endpoint_id, TARGET)
    feed = DaemonDisplayFrames(
        DaemonDisplayProfile(config, lambda: TARGET), scanner, quick_keys=cache
    )
    feed.start()
    try:
        scanner.connect_event(True)
        scanner.sample(scan())
        for _ in range(10):
            feed.snapshot()
        Event().wait(0.04)
        assert scanner.reads == [] and not feed.quick_key_snapshot().active
    finally:
        feed.close()


@pytest.mark.parametrize("mismatch", ["owner", "endpoint", "target", "closed"])
def test_cache_attachment_requires_exact_owner_and_binding(configured, mismatch):
    scanner = QuickScanner()
    cache = DaemonQuickKeyCache(
        QuickScanner() if mismatch == "owner" else scanner,
        UUID(int=999) if mismatch == "endpoint" else configured.binding.endpoint_id,
        "fake://foreign" if mismatch == "target" else TARGET,
    )
    if mismatch == "closed":
        cache.close()
    with pytest.raises(ValueError, match="exact scanner owner"):
        DaemonDisplayFrames(
            DaemonDisplayProfile(configured, lambda: TARGET), scanner, quick_keys=cache
        )
    assert not scanner.events._callbacks and not scanner.reads


def test_real_parser_and_owner_worker_accept_psi_from_a_different_thread_during_get(configured):
    class WireTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            complete = Event()

            def incoming():
                self.feed_line("PSI,<XML>,")
                self.feed_line('<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan">')
                self.feed_line('<MonitorList Q_Key="1"/><System Q_Key="23"/>')
                self.feed_line('<TGID Name="Arrived during GET"/>')
                self.feed_line("</ScannerInfo>")
                self.feed_line("FQK," + ",".join(["2"] * 100))
                complete.set()

            thread = Thread(target=incoming)
            thread.start()
            try:
                assert complete.wait(0.2), "GET held a PSI callback lock"
            finally:
                thread.join(1)

    transport = WireTransport(TARGET)
    radio = SDS200.from_transport(transport)
    cache = DaemonQuickKeyCache(radio, configured.binding.endpoint_id, TARGET)
    feed = DaemonDisplayFrames(
        DaemonDisplayProfile(configured, lambda: TARGET), radio, quick_keys=cache
    )
    feed.start()
    try:
        with radio:
            radio.events.emit("psi", scan())
            feed.snapshot()
            wait_for(lambda: bank(feed) is not None)
            assert "Arrived during GET" in texts(feed.snapshot())
            assert transport.writes == ["FQK"]
    finally:
        feed.close()
    assert not feed.quick_key_worker_status().alive


@pytest.mark.parametrize("subscription", ["psi", "connection"])
def test_unsubscription_error_still_closes_worker_and_invalidates_cached_data(engine, subscription):
    feed, _, _, scanner, _ = engine
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    attribute = "_" + subscription + "_unsubscribe"
    original = getattr(feed, attribute)

    def broken_unsubscribe():
        original()
        raise OSError("synthetic unsubscription failure")

    setattr(feed, attribute, broken_unsubscribe)
    with pytest.raises(OSError):
        feed.close()
    assert not feed.quick_key_worker_status().alive
    assert not feed.quick_key_snapshot().active
    assert not any(scanner.events._callbacks.values())
    assert not texts(feed.snapshot())
    feed.close()  # No repeat of the failed unsubscribe.


def test_older_profile_context_cannot_lower_invalidation_barrier(engine):
    feed, _, _, _, _ = engine
    feed.snapshot()
    wait_for(lambda: bank(feed) is not None)
    with feed._lock:
        assert feed._profile_barrier(None, 5)
        assert not feed._profile_barrier(None, 4)
        assert feed._profile_invalidation == 5
    assert not feed.quick_key_snapshot().active


def test_real_worker_yields_to_foreground_command_on_same_owner(configured):
    entered, release = Event(), Event()

    class WireTransport(FakeTransport):
        def write_command(self, command):
            super().write_command(command)
            if command == "VOL":
                entered.set()
                assert release.wait(3)
                self.feed_line("VOL,3")
            else:
                assert command in ("FQK", "SQK,1", "DQK,1,23")
                kind = command.split(",")[0]
                prefix = "" if kind == "FQK" else "1,23,"
                self.feed_line(kind + "," + prefix + ",".join(["2"] * 100))

    transport = WireTransport(TARGET)
    radio = SDS200.from_transport(transport)
    cache = DaemonQuickKeyCache(radio, configured.binding.endpoint_id, TARGET)
    feed = DaemonDisplayFrames(
        DaemonDisplayProfile(configured, lambda: TARGET), radio, quick_keys=cache
    )
    feed.start()
    try:
        with radio, ThreadPoolExecutor(max_workers=1) as pool:
            radio.events.emit("psi", scan())
            foreground = pool.submit(radio.get_volume, timeout=3)
            try:
                assert entered.wait(1)
                feed.snapshot()
                # The worker reaches its first tick while the foreground owns
                # the lane. It must not send a GET or queue within that lock.
                Event().wait(0.6)
                assert transport.writes == ["VOL"]
                radio.events.emit("psi", scan(name="Foreground still active"))
                assert "Foreground still active" in texts(feed.snapshot())
                assert not bank(feed)
            finally:
                release.set()
            assert foreground.result(timeout=1) == 3
            wait_for(lambda: any(b.states for b in feed.quick_key_snapshot().banks))
            # Fair scheduling can try a different eligible bank after FQK was
            # busy; it must still use an exact, scoped read-only command.
            assert transport.writes == ["VOL", "SQK,1"]
    finally:
        feed.close()


def test_same_cache_cannot_be_attached_to_two_feeds(engine):
    _, cache, profile, scanner, clock = engine
    with pytest.raises(ValueError, match="already attached"):
        DaemonDisplayFrames(profile, scanner, clock=lambda: clock.now, quick_keys=cache)
    assert len(scanner.events._callbacks["psi"]) == 1


def test_worker_honors_a_stricter_display_freshness_limit(configured, monkeypatch):
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.01)
    scanner, clock = QuickScanner(), SimpleNamespace(now=10.0)
    cache = DaemonQuickKeyCache(
        scanner, configured.binding.endpoint_id, TARGET, clock=lambda: clock.now
    )
    feed = DaemonDisplayFrames(
        DaemonDisplayProfile(configured, lambda: TARGET),
        scanner,
        clock=lambda: clock.now,
        quick_keys=cache,
        stale_after=1,
    )
    feed.start()
    try:
        scanner.connect_event(True)
        scanner.sample(scan())
        feed.snapshot()
        wait_for(lambda: bank(feed) is not None)
        clock.now = 11
        wait_for(lambda: not feed.quick_key_snapshot().active)
        assert len(scanner.reads) == 1  # Do not wait for the cache's five seconds.
        assert feed.snapshot()["frames"]["preferred"]["status"] == "stale"
    finally:
        feed.close()
