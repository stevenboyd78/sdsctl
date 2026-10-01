"""Private candidate only: synthetic faults and localhost UDP, never hardware."""

import errno
import os
import queue
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace

import pytest

from sds200 import network
from sds200 import radio as radio_module
from sds200.commands import GetDateTime, GetFavoritesQuickKeys, GetSystemQuickKeys
from sds200.daemon_quick_keys import DaemonQuickKeyCache
from sds200.daemon_runtime import DaemonRuntimeState
from sds200.exceptions import (
    CommandRejectedError,
    CommandTimeoutError,
    DaemonControlBusyError,
    ProtocolError,
    ScannerConnectionError,
    UnsupportedScannerFeatureError,
)
from sds200.network import UdpTransport
from sds200.radio import SDS200

from .fakes import FakeDatagramSocket, FakeTransport
from .test_daemon_display_read_research import runtime_for
from .test_daemon_quick_keys import ENDPOINT, activate

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX native UDP candidate")
REPLIES = {
    "DTM": b"DTM,0,2026,9,17,21,26,59,1\r",
    "FQK": ("FQK," + ",".join(["1"] * 100) + "\r").encode(),
}


@pytest.fixture
def udp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind(("127.0.0.1", 0))
        peer.settimeout(1)
        transport = UdpTransport("127.0.0.1", remote_port=peer.getsockname()[1], reconnect=False)
        transport._open()
        try:
            yield transport, peer
        finally:
            transport.stop()


@contextmanager
def held_elsewhere(lock):
    entered, release = Event(), Event()

    def hold():
        with lock:
            entered.set()
            assert release.wait(2)

    thread = Thread(target=hold)
    thread.start()
    try:
        assert entered.wait(1)
        yield
    finally:
        release.set()
        thread.join(1)
        assert not thread.is_alive()


def patch_write(monkeypatch, write):
    # Do not patch process-wide os.write (pytest uses it for output capture).
    monkeypatch.setattr(
        network, "os", SimpleNamespace(name=os.name, write=write, get_blocking=os.get_blocking)
    )


@pytest.mark.parametrize("command", ["FQK", "DTM"])
@pytest.mark.parametrize("timeout", [0, 0.2])
def test_native_datagram_uses_same_socket_and_preserves_receive_settings(udp, command, timeout):
    transport, peer = udp
    sock = transport._socket
    sock.settimeout(timeout)
    descriptor = sock.fileno()
    assert transport.try_write_supplemental_get(command, deadline=monotonic() + 0.25)
    data, source = peer.recvfrom(4096)
    assert data == (command + "\r").encode()
    assert source == sock.getsockname()
    assert sock is transport._socket and sock.fileno() == descriptor
    assert sock.gettimeout() == timeout and not os.get_blocking(descriptor)
    assert transport.statistics["commands_sent"] == 1
    assert transport._last_xml_commands == {} and transport._xml_retry_counts == {}


@pytest.mark.parametrize(
    "lock_name", ["_write_lock", "_socket_lock", "_statistics_lock", "decoder"]
)
def test_contended_transport_locks_never_queue_a_send(udp, monkeypatch, lock_name):
    transport, peer = udp
    patch_write(monkeypatch, lambda *_: pytest.fail("busy path wrote"))
    lock = transport._decoder._lock if lock_name == "decoder" else getattr(transport, lock_name)
    with held_elsewhere(lock):
        assert transport.try_write_supplemental_get("FQK", deadline=monotonic() + 0.25) is False
    # The call has finished: releasing the lock must not launch delayed work.
    peer.setblocking(False)
    with pytest.raises(BlockingIOError):
        peer.recv(4096)
    assert transport.statistics["commands_sent"] == 0


def test_success_preserves_bare_psi_attribution_and_refusal_does_not_change_it(udp):
    transport, _ = udp
    transport._decoder.expect_command("PSI,100")
    transport._decoder.expect_command("GSI")
    with held_elsewhere(transport._write_lock):
        assert not transport.try_write_supplemental_get("FQK", deadline=monotonic() + 0.25)
        assert transport._decoder._expected_xml_command == "GSI"
    assert transport.try_write_supplemental_get("FQK", deadline=monotonic() + 0.25)
    assert transport._decoder._expected_xml_command is None
    assert transport._decoder._stream_xml_command == "PSI"
    lines = transport._decoder.feed(b'<ScannerInfo Mode="Trunk Scan" V_Screen="trunk_scan"/>')
    assert lines[0] == "PSI,<XML>,"


@pytest.mark.parametrize("bad", ["DTM,0,2026", "FQK,1", "SQK,1", "DTM\r", "fqk", "", None, 1])
def test_only_two_exact_gets_are_allowed(udp, bad):
    transport, _ = udp
    with pytest.raises(ValueError, match="exact FQK or DTM"):
        transport.try_write_supplemental_get(bad, deadline=monotonic() + 0.25)
    assert transport.statistics["commands_sent"] == 0


@pytest.mark.parametrize("deadline", [True, None, "private", -1, float("inf"), float("nan"), 101])
def test_invalid_deadlines_fail_before_send(udp, monkeypatch, deadline):
    transport, _ = udp
    monkeypatch.setattr(network, "monotonic", lambda: 100)
    with pytest.raises(ValueError) as error:
        transport.try_write_supplemental_get("DTM", deadline=deadline)
    assert "private" not in str(error.value)
    assert transport.statistics["commands_sent"] == 0


@pytest.mark.parametrize("times", [(100.25,), (100, 100.25)])
def test_expired_deadline_is_not_sent_even_if_metadata_consumes_budget(udp, monkeypatch, times):
    transport, _ = udp
    clock = iter(times)
    monkeypatch.setattr(network, "monotonic", lambda: next(clock))
    patch_write(monkeypatch, lambda *_: pytest.fail("expired path wrote"))
    assert transport.try_write_supplemental_get("DTM", deadline=100.25) is False


def test_closed_transport_is_not_opened(udp, monkeypatch):
    transport, _ = udp
    transport.stop()
    patch_write(monkeypatch, lambda *_: pytest.fail("disconnected path wrote"))
    assert transport.try_write_supplemental_get("FQK", deadline=monotonic() + 0.25) is False
    assert transport._socket is None


@pytest.mark.parametrize("kind", ["platform", "custom", "blocking"])
def test_unqualified_transport_implementations_are_explicitly_refused(udp, monkeypatch, kind):
    transport, _ = udp
    sock = transport._socket
    if kind == "platform":
        monkeypatch.setattr(network, "os", SimpleNamespace(name="nt"))
    elif kind == "custom":
        transport._socket = FakeDatagramSocket()
    else:
        # Detect actual flags, not only a stale Python timeout property.
        os.set_blocking(sock.fileno(), True)
    try:
        with pytest.raises(UnsupportedScannerFeatureError):
            transport.try_write_supplemental_get("FQK", deadline=monotonic() + 0.25)
        assert transport.statistics["commands_sent"] == 0
    finally:
        transport._socket = sock
        os.set_blocking(sock.fileno(), False)


def test_kernel_backpressure_is_not_sent_and_never_retried(udp, monkeypatch):
    transport, _ = udp
    calls = []

    def busy(*args):
        calls.append(args)
        raise BlockingIOError(errno.EAGAIN, "PRIVATE")

    patch_write(monkeypatch, busy)
    assert transport.try_write_supplemental_get("DTM", deadline=monotonic() + 0.25) is False
    assert len(calls) == 1 and transport.connected
    assert transport.statistics["commands_sent"] == 0


@pytest.mark.parametrize("outcome", [0, 1, OSError(errno.EIO, "PRIVATE")])
def test_short_and_failed_writes_are_uncertain_without_reconnect_or_callbacks(
    udp, monkeypatch, outcome
):
    transport, _ = udp
    calls, connections = [], []
    transport._connection_handler = connections.append

    def write(*args):
        calls.append(args)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    patch_write(monkeypatch, write)
    with pytest.raises(ScannerConnectionError, match="uncertain") as error:
        transport.try_write_supplemental_get("DTM", deadline=monotonic() + 0.25)
    assert "PRIVATE" not in str(error.value)
    assert len(calls) == 1 and connections == [] and transport.connected
    assert transport.statistics["commands_sent"] == 0


@pytest.mark.parametrize("command", [GetDateTime(), GetFavoritesQuickKeys()])
def test_radio_candidate_uses_existing_parser_with_loopback_receive_thread(udp, command):
    transport, peer = udp
    # Start the owner's normal receive loop, without opening a second socket.
    transport.stop()
    scanner = SDS200.from_transport(transport)
    psi = []
    scanner.on_psi(psi.append)
    with scanner, ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(scanner._read_bounded_supplemental_if_idle, command, timeout=0.5)
        data, source = peer.recvfrom(4096)
        assert data == (command.wire + "\r").encode()
        peer.sendto(
            b'PSI,<XML>,<ScannerInfo Mode="Scan Mode" V_Screen="conventional_scan">'
            b'<ConvFrequency Name="Synthetic"/><Footer No="1" EOT="1"/></ScannerInfo>',
            source,
        )
        peer.sendto(REPLIES[command.wire], source)
        reading = result.result(timeout=1)
        assert reading.packet.command == command.wire and len(psi) == 1
        assert not scanner._responses
        scanner.send("VOL")  # Ordinary command path is unchanged after candidate read.
        assert peer.recv(4096) == b"VOL\r"


@pytest.mark.parametrize("lock_name", ["_command_lock", "_response_lock"])
def test_radio_candidate_refuses_busy_owner_without_dispatch(udp, monkeypatch, lock_name):
    transport, _ = udp
    scanner = SDS200.from_transport(transport)
    patch_write(monkeypatch, lambda *_: pytest.fail("busy owner sent"))
    with held_elsewhere(getattr(scanner, lock_name)):
        assert scanner._read_bounded_supplemental_if_idle(GetDateTime()) is None
    assert not scanner._responses


def test_radio_does_not_guess_custom_transport_or_bypass_trace_file(udp, tmp_path):
    scanner = SDS200.from_transport(FakeTransport())
    with pytest.raises(UnsupportedScannerFeatureError):
        scanner._read_bounded_supplemental_if_idle(GetDateTime())
    transport, _ = udp
    path = tmp_path / "must-not-create.log"
    scanner = SDS200.from_transport(transport, trace_path=path)
    with pytest.raises(UnsupportedScannerFeatureError):
        scanner._read_bounded_supplemental_if_idle(GetDateTime())
    assert not path.exists() and transport.statistics["commands_sent"] == 0


def test_radio_explicitly_refuses_custom_trace_before_inspecting_it(udp):
    class Trace:
        @property
        def path(self):
            pytest.fail("Custom trace callback was invoked")

    scanner = SDS200.from_transport(udp[0])
    scanner.trace = Trace()
    with pytest.raises(UnsupportedScannerFeatureError):
        scanner._read_bounded_supplemental_if_idle(GetDateTime())
    assert not scanner._responses and udp[0].statistics["commands_sent"] == 0


@pytest.mark.parametrize(
    "reply,error", [(b"DTM,NG", CommandRejectedError), (b"DTM,OK", ProtocolError)]
)
def test_reply_failures_clean_up_pending_entry_and_release_lane(udp, monkeypatch, reply, error):
    transport, _ = udp
    scanner = SDS200.from_transport(transport)

    def write(_fd, data):
        scanner._receive_line(reply.decode())
        return len(data)

    patch_write(monkeypatch, write)
    with pytest.raises(error):
        scanner._read_bounded_supplemental_if_idle(GetDateTime())
    assert not scanner._responses
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(scanner.send, "VOL").result(timeout=1)


def test_one_budget_covers_write_and_response_and_late_success_is_not_accepted(udp, monkeypatch):
    transport, _ = udp
    scanner = SDS200.from_transport(transport)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(network, "monotonic", lambda: clock.now)
    monkeypatch.setattr(radio_module, "monotonic", lambda: clock.now)

    def late_write(_fd, data):
        # Model OS descheduling after submission, without a wall-clock sleep.
        clock.now = 100.26
        scanner._receive_line(REPLIES["DTM"].decode())
        return len(data)

    patch_write(monkeypatch, late_write)
    with pytest.raises(CommandTimeoutError, match="after dispatch"):
        scanner._read_bounded_supplemental_if_idle(GetDateTime(), timeout=0.25)
    assert not scanner._responses and transport.statistics["commands_sent"] == 1


@pytest.mark.parametrize("timeout", [0, -1, 0.51, True, float("nan"), float("inf")])
def test_radio_budget_cannot_be_expanded(udp, timeout):
    scanner = SDS200.from_transport(udp[0])
    with pytest.raises((TypeError, ValueError)):
        scanner._read_bounded_supplemental_if_idle(GetDateTime(), timeout=timeout)
    assert not scanner._responses


def test_scoped_and_arbitrary_commands_are_not_admitted(udp):
    scanner = SDS200.from_transport(udp[0])
    for command in (GetSystemQuickKeys(1), "DTM", object()):
        with pytest.raises(ValueError, match="exact DTM or FQK"):
            scanner._read_bounded_supplemental_if_idle(command)
    assert not scanner._responses


def test_write_elapsed_time_is_subtracted_from_response_wait(udp, monkeypatch):
    scanner = SDS200.from_transport(udp[0])
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(network, "monotonic", lambda: clock.now)
    monkeypatch.setattr(radio_module, "monotonic", lambda: clock.now)
    budgets = []

    class Queue(queue.Queue):
        def get(self, *, timeout):
            budgets.append(timeout)
            raise queue.Empty

    monkeypatch.setattr(radio_module, "queue", SimpleNamespace(Queue=Queue, Empty=queue.Empty))

    def write(_fd, data):
        clock.now += 0.1
        return len(data)

    patch_write(monkeypatch, write)
    with pytest.raises(CommandTimeoutError, match="timed out"):
        scanner._read_bounded_supplemental_if_idle(GetDateTime(), timeout=0.25)
    assert budgets == pytest.approx([0.15]) and not scanner._responses


def test_expiry_during_response_processing_never_returns_a_valid_value(udp, monkeypatch):
    scanner = SDS200.from_transport(udp[0])
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(network, "monotonic", lambda: clock.now)
    monkeypatch.setattr(radio_module, "monotonic", lambda: clock.now)
    original = GetDateTime.parse_response

    def parse(command, response):
        value = original(command, response)
        clock.now = 100.25
        return value

    def write(_fd, data):
        scanner._receive_line(REPLIES["DTM"].decode())
        return len(data)

    monkeypatch.setattr(GetDateTime, "parse_response", parse)
    patch_write(monkeypatch, write)
    with pytest.raises(CommandTimeoutError, match="too late"):
        scanner._read_bounded_supplemental_if_idle(GetDateTime())
    assert not scanner._responses


def test_occupied_response_registry_is_left_untouched(udp, monkeypatch):
    scanner = SDS200.from_transport(udp[0])
    pending = object()
    scanner._responses["VOL"] = pending
    patch_write(monkeypatch, lambda *_: pytest.fail("occupied owner sent"))
    assert scanner._read_bounded_supplemental_if_idle(GetDateTime()) is None
    assert scanner._responses == {"VOL": pending}


@pytest.fixture
def bounded_cache(udp):
    scanner = SDS200.from_transport(udp[0])
    clock = SimpleNamespace(now=10.0)
    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        scanner.endpoint,
        clock=lambda: clock.now,
        include_clock=True,
        allow_scoped_reads=False,
        bounded_writes=True,
    )
    session = cache.begin_session()
    activate(cache, session)
    try:
        yield cache, scanner, clock, session
    finally:
        cache.close()


def test_explicit_cache_policy_routes_both_kinds_without_legacy_fallback(
    bounded_cache, monkeypatch
):
    cache, scanner, clock, _ = bounded_cache
    calls = []

    def write(_fd, data):
        command = data.decode().strip()
        calls.append(command)
        scanner._receive_line(REPLIES[command].decode())
        return len(data)

    def forbidden(*args, **kwargs):
        pytest.fail("bounded candidate fell back to legacy I/O or TX trace")

    monkeypatch.setattr(scanner, "read_clock_if_idle", forbidden)
    monkeypatch.setattr(scanner, "read_quick_keys_if_idle", forbidden)
    monkeypatch.setattr(scanner.trace, "tx", forbidden)
    monkeypatch.setattr(scanner.transport, "write_command", forbidden)
    patch_write(monkeypatch, write)
    assert cache.poll_once()
    clock.now = 10.5
    assert cache.poll_once()
    sample = cache.supplemental_snapshot()
    assert sample.quick_keys.banks[0].states is not None and sample.clock.local_time is not None
    assert calls == ["FQK", "DTM"]


@pytest.mark.parametrize("phase", ["favorites", "clock"])
@pytest.mark.parametrize("failure", ["write", "short", "timeout"])
def test_uncertain_cache_reads_quarantine_both_kinds_without_retry(
    bounded_cache, monkeypatch, phase, failure
):
    cache, scanner, clock, session = bounded_cache
    calls = []
    failed_command = "FQK" if phase == "favorites" else "DTM"

    def write(_fd, data):
        command = data.decode().strip()
        calls.append(command)
        if command == failed_command:
            if failure == "write":
                raise OSError(errno.EIO, "PRIVATE")
            if failure == "short":
                return 1
            raise CommandTimeoutError("Synthetic timeout after dispatch")
        scanner._receive_line(REPLIES[command].decode())
        return len(data)

    patch_write(monkeypatch, write)
    assert cache.poll_once()
    if phase == "clock":
        clock.now = 10.5
        assert cache.poll_once()
    expected = "timeout" if failure == "timeout" else "read_error"
    assert cache.snapshot().blocked_until_reconnect == expected
    assert cache.clock_snapshot().local_time is None
    assert all(bank.states is None for bank in cache.snapshot().banks)
    clock.now = 13
    activate(cache, session, sequence=2)
    assert not cache.poll_once()
    assert calls == (["FQK"] if phase == "favorites" else ["FQK", "DTM"])
    assert not scanner._responses and "PRIVATE" not in repr(cache.supplemental_snapshot())


def test_backpressure_does_not_quarantine_or_queue_automatic_transport_retries(
    bounded_cache, monkeypatch
):
    cache, scanner, clock, _ = bounded_cache
    calls = []

    def write(_fd, data):
        calls.append(data)
        raise BlockingIOError(errno.EAGAIN, "busy")

    patch_write(monkeypatch, write)
    assert cache.poll_once()
    assert cache.snapshot().blocked_until_reconnect is None
    assert not cache.poll_once()  # Still obeys the shared minimum gap.
    clock.now = 10.5
    assert cache.poll_once()
    assert calls == [b"FQK\r", b"DTM\r"]
    assert cache.clock_snapshot().blocked_until_reconnect is None and not scanner._responses


@pytest.mark.parametrize("changes", [{"bounded_writes": 1}, {"allow_scoped_reads": True}])
def test_bounded_policy_requires_explicit_boolean_and_no_scoped_gets(udp, changes):
    scanner = SDS200.from_transport(udp[0])
    with pytest.raises(ValueError):
        DaemonQuickKeyCache(
            scanner,
            ENDPOINT,
            scanner.endpoint,
            **({"bounded_writes": True, "allow_scoped_reads": False} | changes),
        )


def test_bounded_policy_does_not_accept_custom_scanner_claims():
    with pytest.raises(ValueError, match="native scanner owner"):
        DaemonQuickKeyCache(
            object(), ENDPOINT, "fake://", bounded_writes=True, allow_scoped_reads=False
        )


@pytest.mark.parametrize("reply", [True, False])
def test_native_candidate_preserves_runtime_exclusions_and_receive_callbacks(udp, reply):
    transport, peer = udp
    transport.stop()
    scanner = SDS200.from_transport(transport)
    runtime = runtime_for(scanner)
    # No runtime startup (and thus no identity/mode commands). Only the native
    # localhost receive thread is started; mark the fixture's PSI as active.
    runtime._state = DaemonRuntimeState.RUNNING
    seen_psi = Event()
    scanner.on_psi(lambda _: seen_psi.set())
    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        scanner.endpoint,
        include_clock=True,
        allow_scoped_reads=False,
        bounded_writes=True,
        read_scope=runtime._supplemental_read_scope,
    )
    try:
        with scanner, ThreadPoolExecutor(max_workers=1) as pool:
            scanner._psi_active = True
            session = cache.begin_session()
            activate(cache, session)
            result = pool.submit(cache.poll_once)
            data, source = peer.recvfrom(4096)
            assert data == b"FQK\r"
            with pytest.raises(DaemonControlBusyError):
                runtime.hold("SYS", 1)
            with pytest.raises(RuntimeError, match="reserved"):
                scanner.waterfall_session.subscribe()
            assert runtime.snapshot().scanner_connected
            assert cache.supplemental_snapshot().quick_keys.active
            peer.sendto(
                b'PSI,<XML>,<ScannerInfo Mode="Scan Mode" V_Screen="conventional_scan">'
                b'<ConvFrequency Name="Synthetic"/><Footer No="1" EOT="1"/></ScannerInfo>',
                source,
            )
            assert seen_psi.wait(1)
            if reply:
                peer.sendto(REPLIES["FQK"], source)
            assert result.result(timeout=1)
            assert not scanner._responses
            assert cache.snapshot().blocked_until_reconnect == (None if reply else "timeout")
            # Successful reads and timeouts both release every reservation.
            with runtime._supplemental_read_scope(scanner) as allowed:
                assert allowed
            scanner.send("VOL")
            assert peer.recv(4096) == b"VOL\r"
    finally:
        cache.close()
        runtime._state = DaemonRuntimeState.STOPPED


def test_real_timeout_and_late_reply_cannot_revive_quarantined_cache(udp):
    transport, peer = udp
    transport.stop()
    scanner = SDS200.from_transport(transport)
    late_reply = Event()
    scanner.on_packet(lambda packet: late_reply.set() if packet.command == "FQK" else None)
    cache = DaemonQuickKeyCache(
        scanner,
        ENDPOINT,
        scanner.endpoint,
        include_clock=True,
        allow_scoped_reads=False,
        bounded_writes=True,
    )
    try:
        with scanner, ThreadPoolExecutor(max_workers=1) as pool:
            session = cache.begin_session()
            activate(cache, session)
            result = pool.submit(cache.poll_once)
            data, source = peer.recvfrom(4096)
            assert data == b"FQK\r"
            assert result.result(timeout=1)  # Peer deliberately sends no reply yet.
            assert cache.snapshot().blocked_until_reconnect == "timeout"
            assert not scanner._responses
            peer.sendto(REPLIES["FQK"], source)
            assert late_reply.wait(1)
            activate(cache, session, sequence=2)
            assert not cache.poll_once()
            sample = cache.supplemental_snapshot()
            assert sample.clock.local_time is None
            assert all(bank.states is None for bank in sample.quick_keys.banks)
            assert sample.quick_keys.blocked_until_reconnect == "timeout"
            assert transport.statistics["commands_sent"] == 1
    finally:
        cache.close()
