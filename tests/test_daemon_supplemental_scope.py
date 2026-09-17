"""Offline runtime/Waterfall exclusion for opt-in supplemental reads."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from sds200 import daemon_quick_keys
from sds200.daemon_display_frames import DaemonDisplayFrames
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_display_read_research import DisplayReadKind, DisplayReadResearchPolicy
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.daemon_runtime import DaemonRuntimeState
from sds200.daemon_system_status_research import SystemStatusResearchPolicy
from sds200.events import EventBus
from sds200.exceptions import CommandTimeoutError, DaemonControlBusyError
from sds200.scanner_quick_keys import QuickKeySelection
from sds200.waterfall_session import WaterfallSession, WaterfallSessionState

from .test_daemon_display_frames import TARGET, texts
from .test_daemon_display_frames import configured as configured
from .test_daemon_display_read_research import FIRMWARE, runtime_for
from .test_daemon_quick_key_worker import scan, wait_for
from .test_daemon_quick_keys import ENDPOINT, activate, response, states
from .test_daemon_runtime_controls import FakeControlScanner
from .test_scanner_clock import reading
from .test_waterfall_session import FakeWaterfallRadio


class Scanner(FakeControlScanner):
    def __init__(self):
        super().__init__([])
        self.events = EventBus()
        self.waterfall_radio = FakeWaterfallRadio()
        self.waterfall_session = WaterfallSession(self.waterfall_radio)
        self.reads = []
        self.reply = response
        self.clock_reply = reading

    @property
    def endpoint(self):
        return TARGET

    def on_connection(self, callback):
        return self.events.subscribe("connection", callback)

    def on_psi(self, callback):
        return self.events.subscribe("psi", callback)

    def _emit_psi(self):
        self.events.emit("psi", scan("None", "None"))

    def connect(self):
        super().connect()
        self.events.emit("connection", True)

    def close(self):
        super().close()
        self.events.emit("connection", False)

    def read_quick_keys_if_idle(self, command, *, timeout):
        self.reads.append(command.wire)
        return self.reply(command)

    def read_clock_if_idle(self, *, timeout):
        self.reads.append("DTM")
        return self.clock_reply()


@pytest.fixture
def base():
    scanner = Scanner()
    runtime = runtime_for(scanner)
    runtime.start()
    try:
        yield runtime, scanner
    finally:
        runtime.stop()
        scanner.waterfall_session.close()


@pytest.fixture
def guarded(base):
    runtime, scanner = base
    clock = SimpleNamespace(now=10.0)
    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        TARGET,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
        read_scope=runtime._supplemental_read_scope,
    )
    session = cache.begin_session()
    activate(cache, session, QuickKeySelection(None, None))
    try:
        yield runtime, scanner, cache, session, clock
    finally:
        cache.close()


@contextmanager
def hold_elsewhere(lock):
    entered, release = Event(), Event()

    def hold():
        with lock:
            entered.set()
            assert release.wait(3)

    worker = Thread(target=hold)
    worker.start()
    try:
        assert entered.wait(1)
        yield
    finally:
        release.set()
        worker.join(1)
        assert not worker.is_alive()


def available(lock):
    if not lock.acquire(blocking=False):
        return False
    lock.release()
    return True


def test_reservation_is_passive_and_does_not_hold_state_or_callback_locks(base):
    runtime, scanner = base
    order = list(scanner.order)
    with runtime._supplemental_read_scope(scanner) as allowed:
        assert allowed is True
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert not pool.submit(available, runtime._control_lock).result(timeout=0.2)
            assert not pool.submit(available, runtime._lifecycle_lock).result(timeout=0.2)
            assert pool.submit(available, runtime._state_lock).result(timeout=0.2)
            pool.submit(scanner._emit_psi).result(timeout=0.2)
            assert pool.submit(scanner.waterfall_session.snapshot).result(timeout=0.2)
        with pytest.raises(RuntimeError, match="reserved"):
            scanner.waterfall_session.subscribe()
        assert scanner.reads == []
    assert scanner.order == order
    assert scanner.waterfall_radio.start_calls == scanner.waterfall_radio.stop_calls == []
    with scanner.waterfall_session.subscribe():
        assert scanner.waterfall_session.consumer_count == 1


@pytest.mark.parametrize("state", list(DaemonRuntimeState))
def test_only_running_runtime_can_reserve(base, state):
    runtime, scanner = base
    try:
        runtime._state = state
        with runtime._supplemental_read_scope(scanner) as allowed:
            assert allowed is (state is DaemonRuntimeState.RUNNING)
    finally:
        runtime._state = DaemonRuntimeState.RUNNING
    assert scanner.reads == []


@pytest.mark.parametrize("property_name", ["_connected", "_psi_active"])
def test_inactive_connection_or_psi_cannot_reserve(base, property_name):
    runtime, scanner = base
    setattr(scanner, property_name, False)
    try:
        with runtime._supplemental_read_scope(scanner) as allowed:
            assert allowed is False
    finally:
        setattr(scanner, property_name, True)


@pytest.mark.parametrize("lock_name", ["_control_lock", "_lifecycle_lock", "_state_lock"])
def test_reservation_yields_to_each_busy_runtime_lock_without_holding_other_locks(base, lock_name):
    runtime, scanner = base
    with (
        hold_elsewhere(getattr(runtime, lock_name)),
        ThreadPoolExecutor(max_workers=1) as pool,
        runtime._supplemental_read_scope(scanner) as allowed,
    ):
        assert allowed is False
        for name in ("_control_lock", "_lifecycle_lock", "_state_lock"):
            if name != lock_name:
                assert pool.submit(available, getattr(runtime, name)).result(timeout=0.2)
    assert scanner.reads == []


@pytest.mark.parametrize("state", list(WaterfallSessionState))
def test_only_idle_waterfall_can_be_reserved(base, state):
    runtime, scanner = base
    waterfall = scanner.waterfall_session
    try:
        waterfall._state = state
        with runtime._supplemental_read_scope(scanner) as allowed:
            assert allowed is (state is WaterfallSessionState.IDLE)
    finally:
        waterfall._state = WaterfallSessionState.IDLE
    assert scanner.reads == []
    assert scanner.waterfall_radio.start_calls == scanner.waterfall_radio.stop_calls == []


def test_busy_reserved_or_subscribed_waterfall_yields_without_stopping_it(base):
    runtime, scanner = base
    waterfall = scanner.waterfall_session
    for context in (
        lambda: hold_elsewhere(waterfall._lock),
        waterfall.reserve_idle_for_research,
        waterfall.subscribe,
    ):
        with context(), runtime._supplemental_read_scope(scanner) as allowed:
            assert allowed is False
    assert scanner.reads == []
    assert len(scanner.waterfall_radio.start_calls) == 1
    assert len(scanner.waterfall_radio.stop_calls) == 1  # Test lease's cleanup only.


@pytest.mark.parametrize("policy", ["clock", "favorites", "system_status"])
def test_research_policy_excludes_periodic_reader_even_when_not_currently_executing(policy):
    scanner = Scanner()
    if policy == "system_status":
        runtime = runtime_for(scanner, system_status_research=SystemStatusResearchPolicy(FIRMWARE))
    else:
        runtime = runtime_for(
            scanner,
            display_read_research=DisplayReadResearchPolicy(FIRMWARE, DisplayReadKind(policy)),
        )
    with runtime, runtime._supplemental_read_scope(scanner) as allowed:
        assert allowed is False
    assert scanner.reads == []


def test_foreign_scanner_same_address_is_not_the_runtime_owner(base):
    runtime, scanner = base
    other = Scanner()
    assert other.endpoint == scanner.endpoint
    with (
        pytest.raises(ValueError, match="exact runtime scanner"),
        runtime._supplemental_read_scope(other),
    ):
        pytest.fail("Foreign scanner must not reserve runtime")
    assert available(runtime._control_lock)


@pytest.mark.parametrize(
    "candidate", [None, object(), SimpleNamespace(reserve_idle_for_research=lambda: None)]
)
def test_missing_or_custom_waterfall_reservation_fails_closed(base, candidate):
    runtime, scanner = base
    original = scanner.waterfall_session
    try:
        scanner.waterfall_session = candidate
        with runtime._supplemental_read_scope(scanner) as allowed:
            assert allowed is False
    finally:
        scanner.waterfall_session = original


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_body_exceptions_are_not_mistaken_for_busy_reservation_and_release_all_locks(base, error):
    runtime, scanner = base
    with pytest.raises(error), runtime._supplemental_read_scope(scanner) as allowed:
        assert allowed
        raise error("PRIVATE_TEST_FAILURE")
    with runtime._supplemental_read_scope(scanner) as allowed:
        assert allowed


def test_unexpected_waterfall_entry_fault_is_not_silently_treated_as_contention(
    guarded, monkeypatch
):
    runtime, scanner, cache, _, _ = guarded

    @contextmanager
    def broken():
        raise RuntimeError("PRIVATE_RESERVATION_FAILURE")
        yield  # pragma: no cover

    monkeypatch.setattr(scanner.waterfall_session, "reserve_idle_for_research", broken)
    assert cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect == "read_error"
    assert scanner.reads == [] and available(runtime._control_lock)


@pytest.mark.parametrize("kind", ["favorites", "clock"])
def test_busy_gate_discards_auxiliary_data_without_quarantine_and_requires_new_psi(guarded, kind):
    runtime, scanner, cache, session, clock = guarded
    if kind == "clock":
        cache.poll_once()
        clock.now = 10.5
    before = list(scanner.reads)
    with hold_elsewhere(runtime._control_lock):
        assert cache.poll_once()
    assert scanner.reads == before
    assert not cache.snapshot().active and not any(states(cache))
    assert cache.clock_snapshot().local_time is None
    assert cache.snapshot().blocked_until_reconnect is None
    clock.now = 13
    cache.request_refresh()
    assert not cache.poll_once()  # A view alone cannot reuse pre-control PSI.
    activate(cache, session, QuickKeySelection(None, None), sequence=2)
    assert cache.poll_once()
    clock.now = 13.5
    assert cache.poll_once()
    assert states(cache)[0] and cache.clock_snapshot().local_time is not None


def test_ordinary_foreground_control_and_reconnect_win_before_background_reservation(guarded):
    runtime, scanner, cache, session, clock = guarded
    for sequence, operation in enumerate(("hold", "reconnect"), start=2):
        scanner.block_operation = operation
        scanner.control_started.clear()
        scanner.release_control.clear()
        clock.now += 1
        activate(cache, session, sequence=sequence)
        with ThreadPoolExecutor(max_workers=2) as pool:
            foreground = (
                pool.submit(runtime.hold, "SYS", 1)
                if operation == "hold"
                else pool.submit(runtime.reconnect)
            )
            try:
                assert scanner.control_started.wait(1)
                assert pool.submit(cache.poll_once).result(timeout=0.2)
                assert scanner.reads == []
            finally:
                scanner.release_control.set()
            foreground.result(timeout=1)


def test_reserved_read_preserves_foreground_busy_behavior_and_callback_progress(guarded):
    runtime, scanner, cache, _, _ = guarded
    entered, release = Event(), Event()

    def delayed(command):
        entered.set()
        assert release.wait(2)
        return response(command)

    scanner.reply = delayed
    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(cache.poll_once)
        try:
            assert entered.wait(1)
            with pytest.raises(DaemonControlBusyError):
                runtime.hold("SYS", 1)
            with pytest.raises(RuntimeError, match="reserved"):
                scanner.waterfall_session.subscribe()
            pool.submit(scanner._emit_psi).result(timeout=0.2)
            pool.submit(runtime.snapshot).result(timeout=0.2)
            pool.submit(cache.snapshot).result(timeout=0.2)
        finally:
            release.set()
        assert task.result(timeout=1)
    assert states(cache)[0]
    runtime.hold("SYS", 1)  # Locks were released after the read.


@pytest.mark.parametrize("change", ["mode", "demand", "reconnect", "close"])
def test_context_is_rechecked_after_scope_entry_before_any_wire_read(base, change):
    runtime, scanner = base
    clock = SimpleNamespace(now=10.0)

    @contextmanager
    def changed_scope(owner):
        with runtime._supplemental_read_scope(owner) as allowed:
            if change == "mode":
                cache.observe(session, None, sequence=2)
            elif change == "demand":
                cache.clear_demand()
            elif change == "reconnect":
                cache.disconnect(session)
                activate(cache, cache.begin_session())
            else:
                cache.close()
            yield allowed

    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        TARGET,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
        read_scope=changed_scope,
    )
    try:
        session = cache.begin_session()
        activate(cache, session)
        assert cache.poll_once()
        assert scanner.reads == [] and not any(states(cache))
    finally:
        cache.close()


@pytest.mark.parametrize("phase", ["entry", "exit", "invalid_yield"])
def test_unexpected_scope_fault_quarantines_without_exception_text(base, phase):
    _, scanner = base

    @contextmanager
    def failed_scope(_):
        if phase == "entry":
            raise OSError("PRIVATE_ENTRY")
        yield "yes" if phase == "invalid_yield" else True
        raise OSError("PRIVATE_EXIT")

    cache = DaemonQuickKeyCache(scanner, ENDPOINT, TARGET, read_scope=failed_scope)
    try:
        activate(cache, cache.begin_session())
        assert cache.poll_once()
        assert cache.snapshot().blocked_until_reconnect == (
            "invalid_response" if phase == "invalid_yield" else "read_error"
        )
        assert scanner.reads == (["FQK"] if phase == "exit" else [])
        assert not any(states(cache)) and "PRIVATE" not in repr(cache.snapshot())
    finally:
        cache.close()


@pytest.mark.parametrize("kind", ["favorites", "clock"])
def test_timeout_releases_runtime_and_waterfall_but_does_not_retry_auxiliary_read(guarded, kind):
    runtime, scanner, cache, session, clock = guarded

    def failed(*_):
        raise CommandTimeoutError("PRIVATE_TIMEOUT")

    if kind == "clock":
        cache.poll_once()
        clock.now = 10.5
        scanner.clock_reply = failed
    else:
        scanner.reply = failed
    assert cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect == "timeout"
    with runtime._supplemental_read_scope(scanner) as allowed:
        assert allowed  # Coordination lock release is not a quarantine reset.
    clock.now = 13
    activate(cache, session, sequence=2)
    assert not cache.poll_once()


def test_stop_waits_for_reserved_get_then_disallows_further_read(guarded):
    runtime, scanner, cache, _, clock = guarded
    entered, release = Event(), Event()

    def delayed(command):
        entered.set()
        assert release.wait(2)
        return response(command)

    scanner.reply = delayed
    with ThreadPoolExecutor(max_workers=2) as pool:
        read = pool.submit(cache.poll_once)
        try:
            assert entered.wait(1)
            stop = pool.submit(runtime.stop)
            assert not stop.done()
            assert scanner.connected
        finally:
            release.set()
        read.result(timeout=1)
        stop.result(timeout=1)
    assert not scanner.connected and not runtime.running
    clock.now = 13
    assert cache.poll_once()  # Refused before sending anything else.
    assert scanner.reads == ["FQK"]
    assert not any(states(cache))


def test_real_feed_worker_uses_runtime_gate_and_keeps_normal_display_on_refusal(
    base, configured, monkeypatch
):
    runtime, scanner = base
    monkeypatch.setattr(daemon_quick_keys, "MIN_READ_GAP", 0.01)
    clock = SimpleNamespace(now=10.0)
    cache = DaemonQuickKeyCache(
        scanner,
        configured.binding.endpoint_id,
        TARGET,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
        read_scope=runtime._supplemental_read_scope,
    )
    feed = DaemonDisplayFrames(
        DaemonDisplayProfile(configured, lambda: TARGET),
        scanner,
        clock=lambda: clock.now,
        quick_keys=cache,
    )
    feed.start()
    try:
        scanner._emit_psi()
        feed.snapshot()
        wait_for(lambda: cache.snapshot().banks[0].states is not None)
        clock.now = 10.5
        wait_for(lambda: feed.clock_snapshot().local_time is not None)
        with scanner.waterfall_session.subscribe():
            clock.now = 12
            wait_for(lambda: not cache.snapshot().active)
            assert feed.clock_snapshot().local_time is None
            assert "Current channel" in texts(feed.snapshot())
            assert scanner.reads == ["FQK", "DTM"]
        clock.now = 13
        feed.snapshot()
        Event().wait(0.04)
        assert scanner.reads == ["FQK", "DTM"]  # No post-transition PSI yet.
        scanner._emit_psi()
        feed.snapshot()
        wait_for(lambda: cache.snapshot().banks[0].states is not None)
        clock.now = 13.5
        wait_for(lambda: feed.clock_snapshot().local_time is not None)
        runtime.stop()
        assert feed.clock_snapshot().local_time is None
        assert not cache.snapshot().active
    finally:
        feed.close()
    assert not feed.quick_key_worker_status().alive
