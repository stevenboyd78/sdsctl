"""Research-only phase timing; synthetic transports, never a real scanner."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sds200.commands import GetFavoritesQuickKeys
from sds200.exceptions import CommandTimeoutError
from sds200.radio import SDS200
from sds200.trace import TrafficTrace

from .fakes import FakeTransport
from .test_supplemental_research_launcher import FIRMWARE, launcher
from .test_supplemental_research_launcher import trial as trial


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_trace_retains_metadata_only_and_delegates_every_original_call(trial):
    window, scanner, _, clock, _ = trial
    original = scanner.trace
    with patch.object(original, "tx") as tx, patch.object(original, "rx") as rx:
        with window.trace_scope():
            scanner.trace.tx("FQK")  # Before arming: no timing retained.
            assert not window.timing_events
            assert window.arm()
            scanner.trace.tx("FQK")
            clock.now += 0.01
            scanner.trace.rx("FQK,PRIVATE_DATA")
            scanner.trace.tx("FQK,PRIVATE_SET")
            scanner.trace.rx("PRIVATE_UNKNOWN")
            scanner.trace.tx("VOL")
            scanner.trace.rx("NG\r")
            window.stop("read_unconfirmed")
            scanner.trace.rx("DTM,PRIVATE_LATE")
        assert scanner.trace is original
        assert tx.call_count == 4 and rx.call_count == 4
    report = window.timing_report()
    assert [row["event"] for row in report["events"]] == [
        "armed",
        "tx_intent",
        "rx_line",
        "rx_rejection",
        "closed",
        "rx_line",
    ]
    assert report["events"][2]["monotonic_seconds"] == 10.01
    assert not report["outgoing_wire_delivery_established"]
    assert "PRIVATE" not in json.dumps(report)
    report["events"].clear()
    assert len(window.timing_events) == 6


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_probe_restored_on_exception_and_foreign_trace_refused(trial):
    window, scanner, _, _, _ = trial
    original = scanner.trace
    with pytest.raises(RuntimeError, match="synthetic"), window.trace_scope():
        raise RuntimeError("synthetic")
    assert scanner.trace is original
    scanner.trace = object()
    with pytest.raises(ValueError, match="unmodified trace"), window.trace_scope():
        pass


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_metadata_limit_closes_future_admissions_without_unbounded_storage(trial):
    window, scanner, cache, _, psi = trial
    assert window.arm()
    for _ in range(launcher.TIMING_EVENT_LIMIT - 1):
        window.record_timing("rx_line", "FQK")
    psi()
    assert cache.poll_once()  # Scope cannot admit a GET after metadata overflows.
    assert not scanner.reads and window.inflight == window.opportunities == 0
    for _ in range(100):
        window.record_timing("rx_line", "DTM")
    assert len(window.timing_events) == 512
    assert window.closed and window.failure == "timing_overflow"
    assert not window.allow_poll()


@pytest.mark.parametrize("trial", [(True, True)], indirect=True)
def test_diagnostic_labels_cannot_retain_arbitrary_content(trial):
    window, *_ = trial
    with pytest.raises(ValueError):
        window.record_timing("PRIVATE", "FQK")
    with pytest.raises(ValueError):
        window.record_timing("rx_line", "PRIVATE")
    assert not window.timing_events


@pytest.mark.parametrize("value", [True, None, 1, "yes"])
def test_timing_cannot_be_unpinned_or_implicitly_enable_continuity(tmp_path, value):
    with pytest.raises(ValueError, match="Timing"):
        launcher.ReadWindow(object(), FIRMWARE, timing=value)
    with pytest.raises(ValueError, match="Timing"):
        launcher.SupplementalTrigger(tmp_path / "no-case", FIRMWARE, timing=value)
    assert not (tmp_path / "no-case").exists()


@pytest.mark.parametrize("delay", ["arrival", "publication"])
def test_real_response_lane_distinguishes_late_arrival_from_delayed_publication(delay):
    """Same timeout, different trace order; no guessed hardware root cause."""
    sent, release, parsed, completed = (threading.Event() for _ in range(4))
    transport = FakeTransport()
    radio = SDS200.from_transport(transport)
    window = launcher.ReadWindow(
        SimpleNamespace(scanner=radio), FIRMWARE, continuity=True, timing=True
    )
    window.started = monotonic()  # Offline instrumentation test, not hardware preflight.
    original_write = transport.write_command

    def write(command):
        original_write(command)
        sent.set()

    transport.write_command = write

    def publication(_response):
        if delay == "publication":
            parsed.set()
            assert release.wait(2)

    def receive():
        assert sent.wait(1)
        if delay == "arrival":
            assert release.wait(2)
        transport.feed_line("FQK," + ",".join(["0"] * 100))
        completed.set()

    unsubscribe_packet = radio.on_packet(window.observe_packet)
    unsubscribe_response = radio.on_response(publication)
    original_trace = radio.trace
    try:
        with radio, window.trace_scope(), ThreadPoolExecutor(max_workers=2) as pool:
            incoming = pool.submit(receive)
            request = pool.submit(radio.read_quick_keys_if_idle, GetFavoritesQuickKeys())
            try:
                if delay == "publication":
                    assert parsed.wait(1)
                with pytest.raises(CommandTimeoutError):
                    request.result(timeout=1)
                window.record_timing("cache_complete")
            finally:
                release.set()
            incoming.result(timeout=1)
            assert completed.is_set()
        assert radio.trace is original_trace and type(original_trace) is TrafficTrace
    finally:
        release.set()
        unsubscribe_packet()
        unsubscribe_response()
    events = [row["event"] for row in window.timing_events]
    assert transport.writes == ["FQK"]
    assert events[0] == "tx_intent"
    if delay == "arrival":
        assert events.index("cache_complete") < events.index("rx_line")
    else:
        assert (
            events.index("rx_line") < events.index("parsed_packet") < events.index("cache_complete")
        )
    assert "PRIVATE" not in json.dumps(window.timing_report())
