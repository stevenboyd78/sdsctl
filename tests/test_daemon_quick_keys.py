from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import pytest

from sds200.commands import GetDepartmentQuickKeys, GetFavoritesQuickKeys, GetSystemQuickKeys
from sds200.daemon_quick_keys import (
    READ_TIMEOUT,
    DaemonQuickKeyCache,
    QuickKeySelection,
    QuickKeySession,
)
from sds200.exceptions import CommandRejectedError, CommandTimeoutError, ProtocolError
from sds200.models import FavoritesQuickKeyState, Packet

TARGET = "fake://private-target"
ENDPOINT = UUID(int=123)
DEFAULT_SELECTION = QuickKeySelection(1, 23)


def response(command):
    prefix = ()
    if isinstance(command, GetSystemQuickKeys):
        prefix = (str(command.favorites_quick_key), "23")
    elif isinstance(command, GetDepartmentQuickKeys):
        prefix = (str(command.favorites_quick_key), str(command.system_quick_key))
    fields = (*prefix, *(str(i % 3) for i in range(100)))
    packet = Packet(command.response_command, fields, "PRIVATE_RAW_PACKET")
    return command.parse_response(packet)


class Scanner:
    connected = True
    endpoint = TARGET

    def __init__(self):
        self.reads = []
        self.reply = response

    def read_quick_keys_if_idle(self, command, *, timeout):
        self.reads.append((command.wire, timeout))
        return self.reply(command)


@pytest.fixture
def setup():
    clock, scanner = SimpleNamespace(now=10.0), Scanner()
    cache = DaemonQuickKeyCache(scanner, ENDPOINT, TARGET, clock=lambda: clock.now)
    session = cache.begin_session()
    try:
        yield cache, session, scanner, clock
    finally:
        cache.close()


def activate(cache, session, selection=DEFAULT_SELECTION, sequence=1):
    assert cache.observe(session, selection, sequence=sequence)
    cache.request_refresh()


def states(cache):
    return tuple(bank.states for bank in cache.snapshot().banks)


def test_no_reads_without_both_current_selection_and_explicit_demand(setup):
    cache, session, scanner, clock = setup
    cache.request_refresh()
    assert not cache.poll_once()
    assert not cache.snapshot().active
    clock.now = 16
    cache.observe(session, QuickKeySelection(1, 23), sequence=1)
    assert not cache.poll_once()
    assert scanner.reads == []
    cache.request_refresh()
    assert cache.poll_once()
    assert scanner.reads == [("FQK", READ_TIMEOUT)]


def test_all_consumers_share_one_bounded_cache_and_reads_are_not_in_snapshots(setup):
    cache, session, scanner, clock = setup
    activate(cache, session)
    for _ in range(100):
        cache.request_refresh()
        assert not any(states(cache))
    assert scanner.reads == []
    for moment in (10, 10.5, 11):
        clock.now = moment
        assert cache.poll_once()
        for _ in range(50):
            cache.request_refresh()
            cache.snapshot()
            assert not cache.poll_once()
    assert [wire for wire, _ in scanner.reads] == ["FQK", "SQK,1", "DQK,1,23"]
    snapshot = cache.snapshot()
    assert all(len(bank.states) == 100 for bank in snapshot.banks)
    assert snapshot.banks[0].states[:3] == tuple(FavoritesQuickKeyState)
    assert "PRIVATE_RAW_PACKET" not in repr(snapshot) and TARGET not in repr(snapshot)
    with pytest.raises(AttributeError):
        snapshot.active = False


@pytest.mark.parametrize(
    "selection,expected",
    [
        (QuickKeySelection(None, None), ["FQK"]),
        (QuickKeySelection(0, None), ["FQK", "SQK,0"]),
        (QuickKeySelection(0, 0), ["FQK", "SQK,0", "DQK,0,0"]),
    ],
)
def test_unassigned_is_not_zero_and_no_unscoped_system_reads(setup, selection, expected):
    cache, session, scanner, clock = setup
    activate(cache, session, selection)
    for moment in (10, 10.5, 11):
        clock.now = moment
        cache.poll_once()
    assert [wire for wire, _ in scanner.reads] == expected


@pytest.mark.parametrize("value", [True, -1, 100, "0", 0.0, 10**100])
def test_bad_selection_is_never_wire_data(value):
    with pytest.raises(ValueError):
        QuickKeySelection(value, None)
    with pytest.raises(ValueError):
        QuickKeySelection(0, value)


def test_system_without_favorites_is_not_a_qualified_scope():
    with pytest.raises(ValueError):
        QuickKeySelection(None, 0)


def test_bank_expiry_is_independent_of_new_psi_and_view_reads(setup):
    cache, session, _, clock = setup
    activate(cache, session)
    assert cache.poll_once()
    clock.now = 14
    cache.observe(session, QuickKeySelection(1, 23), sequence=2)
    cache.request_refresh()
    assert states(cache)[0]
    clock.now = 15
    assert cache.snapshot().active
    assert not any(states(cache))
    assert cache.snapshot().banks[0].age_seconds == 5


@pytest.mark.parametrize("renew", ["demand", "psi", "neither"])
def test_both_psi_and_demand_must_remain_fresh(setup, renew):
    cache, session, scanner, clock = setup
    activate(cache, session)
    cache.poll_once()
    clock.now = 14
    if renew == "demand":
        cache.request_refresh()
    elif renew == "psi":
        cache.observe(session, QuickKeySelection(1, 23), sequence=2)
    clock.now = 15
    assert not cache.snapshot().active
    assert not any(states(cache)) and not cache.poll_once()
    assert len(scanner.reads) == 1


def test_selection_change_clears_cached_banks_and_rejects_older_psi(setup):
    cache, session, _, clock = setup
    activate(cache, session)
    cache.poll_once()
    assert cache.observe(session, QuickKeySelection(2, 4), sequence=2)
    assert not cache.observe(session, QuickKeySelection(1, 23), sequence=1)
    assert not any(states(cache))
    assert cache.snapshot().selection == QuickKeySelection(2, 4)
    cache.observe(session, None, sequence=3)  # Menu, ambiguous or non-scan.
    clock.now = 11
    assert not cache.poll_once() and not cache.snapshot().active


@pytest.mark.parametrize("action", ["scope", "suspend", "disconnect", "reconnect", "close"])
def test_late_replies_never_repopulate_changed_or_stopped_context(setup, action):
    cache, session, scanner, clock = setup
    activate(cache, session)
    entered, release = Event(), Event()

    def blocked(command):
        entered.set()
        assert release.wait(2)
        return response(command)

    scanner.reply = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(cache.poll_once)
        try:
            assert entered.wait(1)
            # Snapshot/observation are nonblocking while the GET is waiting.
            assert pool.submit(cache.snapshot).result(timeout=0.5).active
            clock.now = 10.1
            if action == "scope":
                cache.observe(session, QuickKeySelection(2, 4), sequence=2)
            elif action == "suspend":
                cache.observe(session, None, sequence=2)
            elif action == "disconnect":
                cache.disconnect(session)
            elif action == "reconnect":
                cache.disconnect(session)
                new = cache.begin_session()
                activate(cache, new)
            else:
                cache.close()
            clock.now = 11
            assert not cache.poll_once()  # Old read still owns the one slot.
        finally:
            release.set()
        assert work.result(timeout=1)
    assert not any(states(cache)) and len(scanner.reads) == 1


@pytest.mark.parametrize(
    "failure,expected",
    [
        (CommandTimeoutError, "timeout"),
        (ProtocolError, "invalid_response"),
        (OSError, "read_error"),
    ],
)
def test_uncertain_failures_quarantine_until_new_connection_even_after_scope_changes(
    setup, failure, expected
):
    cache, session, scanner, clock = setup
    activate(cache, session)

    def fail(_command):
        raise failure("PRIVATE_ERROR")

    scanner.reply = fail
    assert cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect == expected
    assert "PRIVATE_ERROR" not in repr(cache.snapshot())
    scanner.reply = response
    clock.now = 11
    cache.observe(session, QuickKeySelection(2, 4), sequence=2)
    cache.request_refresh()
    assert not cache.poll_once()
    cache.disconnect(session)
    activate(cache, cache.begin_session())
    assert cache.poll_once() and states(cache)[0]


def test_late_response_past_budget_is_never_fresh(setup):
    cache, session, scanner, clock = setup
    activate(cache, session)

    def too_late(command):
        clock.now += READ_TIMEOUT
        return response(command)

    scanner.reply = too_late
    assert cache.poll_once() and not any(states(cache))
    assert cache.snapshot().blocked_until_reconnect == "timeout"


def test_explicit_rejection_backs_off_only_its_bank(setup):
    cache, session, scanner, clock = setup
    activate(cache, session)

    def reject_favorites(command):
        if isinstance(command, GetFavoritesQuickKeys):
            raise CommandRejectedError("PRIVATE_REJECTION")
        return response(command)

    scanner.reply = reject_favorites
    for moment in (10, 10.5, 11):
        clock.now = moment
        cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect is None
    assert cache.snapshot().banks[0].failure == "rejected"
    assert not states(cache)[0] and all(states(cache)[1:])
    clock.now = 12
    cache.poll_once()
    assert [wire for wire, _ in scanner.reads].count("FQK") == 1


def test_busy_reader_does_not_quarantine_or_spin(setup):
    cache, session, scanner, clock = setup
    activate(cache, session)
    scanner.reply = lambda _: None
    assert cache.poll_once()
    for _ in range(100):
        cache.request_refresh()
        assert not cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect is None
    clock.now = 10.5
    scanner.reply = response
    assert cache.poll_once()
    assert len(scanner.reads) == 2


@pytest.mark.parametrize("change", ["disconnected", "target"])
@pytest.mark.parametrize("when", ["before", "during"])
def test_actual_scanner_identity_checked_around_read(setup, change, when):
    cache, session, scanner, _ = setup
    activate(cache, session)

    def mutate():
        if change == "target":
            scanner.endpoint = "fake://foreign-private-target"
        else:
            scanner.connected = False

    if when == "before":
        mutate()
    else:

        def reply(command):
            mutate()
            return response(command)

        scanner.reply = reply
    assert cache.poll_once()
    assert not cache.snapshot().active and not any(states(cache))
    assert len(scanner.reads) == (0 if when == "before" else 1)
    assert not cache.observe(session, QuickKeySelection(1, 23), sequence=2)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: replace(r, states=(True,) * 100),
        lambda r: replace(r, states=tuple(reversed(r.states))),
        lambda r: object(),
    ],
)
def test_forged_typed_result_is_not_cached(setup, mutate):
    cache, session, scanner, _ = setup
    activate(cache, session)
    scanner.reply = lambda c: mutate(response(c))
    cache.poll_once()
    assert not any(states(cache))
    assert cache.snapshot().blocked_until_reconnect == "invalid_response"


def test_copied_ticket_and_closed_cache_cannot_be_restarted(setup):
    cache, session, _, _ = setup
    assert not cache.observe(QuickKeySession(ENDPOINT), QuickKeySelection(1, 23), sequence=1)
    cache.close()
    cache.request_refresh()
    assert not cache.observe(session, QuickKeySelection(1, 23), sequence=1)
    assert not cache.poll_once()
    with pytest.raises(ValueError, match="closed"):
        cache.begin_session()


@pytest.mark.parametrize("now", [-1, float("nan"), float("inf"), True, "10", 10**400])
def test_invalid_clock_is_refused_without_io(setup, now):
    cache, _, scanner, clock = setup
    clock.now = now
    with pytest.raises(ValueError):
        cache.poll_once()
    assert scanner.reads == []


def test_read_freshness_is_measured_from_request_start_not_reply(setup):
    cache, session, scanner, clock = setup
    activate(cache, session)

    def delayed(command):
        clock.now += 0.2
        return response(command)

    scanner.reply = delayed
    cache.poll_once()
    assert cache.snapshot().banks[0].age_seconds == pytest.approx(0.2)


def test_scope_change_during_timely_reply_discards_without_poisoning_new_selection(setup):
    cache, session, scanner, clock = setup
    activate(cache, session)

    def change_scope(command):
        clock.now += 0.1
        cache.observe(session, QuickKeySelection(2, 4), sequence=2)
        return response(command)

    scanner.reply = change_scope
    cache.poll_once()
    assert not any(states(cache))
    assert cache.snapshot().blocked_until_reconnect is None
    scanner.reply = response
    clock.now = 11
    cache.poll_once()
    assert states(cache)[0]


def test_demand_expiry_and_renewal_invalidates_in_flight_reply(setup):
    cache, session, scanner, clock = setup
    activate(cache, session)

    def renew_after_gap(command):
        clock.now = 16
        cache.observe(session, DEFAULT_SELECTION, sequence=2)
        cache.request_refresh()
        return response(command)

    scanner.reply = renew_after_gap
    cache.poll_once()
    assert not any(states(cache))
    assert cache.snapshot().blocked_until_reconnect == "timeout"


def test_nonstandard_worker_interruption_clears_pending_and_quarantines(setup):
    cache, session, scanner, _ = setup
    activate(cache, session)

    def interrupt(_command):
        raise KeyboardInterrupt

    scanner.reply = interrupt
    with pytest.raises(KeyboardInterrupt):
        cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect == "read_error"
    assert not any(states(cache)) and not cache.poll_once()
