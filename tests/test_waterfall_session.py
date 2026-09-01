from __future__ import annotations

import queue
from collections.abc import Callable

import pytest

from sds200.models import DisplayLine, GstResponse, GwfResponse, Packet, PwfResponse
from sds200.parser import PacketParser
from sds200.waterfall_session import (
    WaterfallSession,
    WaterfallSessionState,
)
from sds200.waterfall_subscriptions import (
    WaterfallPublisher,
    WaterfallSubscription,
    WaterfallSubscriptionClosed,
)


class FakeWaterfallRadio:
    def __init__(self, *, queue_capacity: int = 64) -> None:
        self.publisher = WaterfallPublisher(queue_capacity=queue_capacity)
        self.parser = PacketParser()
        self.start_calls: list[float] = []
        self.status_calls: list[float] = []
        self.stop_calls: list[float] = []
        self.poll_calls: list[float] = []
        self.after_poll: Callable[[], None] | None = None
        self.status_frequency_offset = 0
        self.status_errors_remaining = 0
        self.start_error: BaseException | None = None
        self.stop_error: BaseException | None = None
        self.poll_errors_remaining = 0

    def get_waterfall_status(self, *, timeout: float = 2.0) -> GstResponse:
        self.status_calls.append(timeout)
        if self.status_errors_remaining > 0:
            self.status_errors_remaining -= 1
            raise RuntimeError("synthetic GST timeout")
        offset = self.status_frequency_offset
        packet = Packet(command="GST", fields=(), raw="GST")
        return GstResponse(
            display_form="00000",
            lines=tuple(DisplayLine("", "") for _ in range(5)),
            mute="0",
            alert_led="0",
            charge_led="0",
            waterfall_mode="1",
            marker_frequency=str(1555500 + offset),
            modulation="NFM",
            marker_position="120",
            center_frequency=str(1550000 + offset),
            lower_frequency=str(1540000 + offset),
            upper_frequency=str(1560000 + offset),
            color_mode="0",
            fft_area_size="1",
            packet=packet,
        )

    def start_waterfall_publication(
        self,
        *,
        timeout: float = 3.0,
    ) -> tuple[PwfResponse, GwfResponse]:
        self.start_calls.append(timeout)
        if self.start_error is not None:
            raise self.start_error
        pwf = self._pwf("first")
        gwf = self._gwf(0)
        self.publisher.publish(pwf)
        self.publisher.publish(gwf)
        return pwf, gwf

    def stop_waterfall_publication(self, *, timeout: float = 2.0) -> None:
        self.stop_calls.append(timeout)
        if self.stop_error is not None:
            raise self.stop_error

    def get_waterfall_frame(self, *, timeout: float = 2.0) -> GwfResponse:
        self.poll_calls.append(timeout)
        if self.poll_errors_remaining > 0:
            self.poll_errors_remaining -= 1
            raise RuntimeError("synthetic GWF timeout")
        if self.after_poll is not None:
            self.after_poll()
        response = self._gwf(len(self.poll_calls))
        self.publisher.publish(response)
        return response

    def subscribe_waterfall(self) -> WaterfallSubscription:
        return self.publisher.subscribe()

    def waterfall_snapshot(self):  # type: ignore[no-untyped-def]
        return self.publisher.snapshot()

    def publish_pwf(self, value: str) -> PwfResponse:
        response = self._pwf(value)
        self.publisher.publish(response)
        return response

    def _pwf(self, value: str) -> PwfResponse:
        response = self.parser.parse_typed(
            self.parser.parse_packet(f"PWF,{value}")
        )
        assert isinstance(response, PwfResponse)
        return response

    def _gwf(self, offset: int) -> GwfResponse:
        response = self.parser.parse_typed(
            self.parser.parse_packet(
                "GWF," + ",".join(str(offset + index) for index in range(240))
            )
        )
        assert isinstance(response, GwfResponse)
        return response


@pytest.mark.parametrize(
    ("keyword", "message"),
    (
        ({"status_poll_interval": 0.0}, "GST poll interval"),
        ({"status_poll_timeout": 0.0}, "GST poll timeout"),
    ),
)
def test_status_poll_configuration_requires_positive_values(
    keyword: dict[str, float],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        WaterfallSession(FakeWaterfallRadio(), **keyword)


def test_first_lease_starts_one_session_and_last_lease_stops_it() -> None:
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio, start_timeout=1.5, stop_timeout=0.75)

    first = session.subscribe()
    second = session.subscribe()

    assert radio.start_calls == [1.5]
    assert radio.status_calls == [1.5]
    assert session.snapshot().state is WaterfallSessionState.RUNNING
    assert session.snapshot().consumer_count == 2
    assert isinstance(first.get(0).response, PwfResponse)
    assert isinstance(first.get(0).response, GwfResponse)
    with pytest.raises(queue.Empty):
        second.get(0)

    published = radio.publish_pwf("shared")
    assert first.get(0).response is published
    assert second.get(0).response is published

    first.close()
    assert radio.stop_calls == []
    assert session.snapshot().consumer_count == 1

    second.close()
    assert radio.stop_calls == [0.75]
    assert session.snapshot().state is WaterfallSessionState.IDLE
    assert session.snapshot().consumer_count == 0


def test_failed_first_start_releases_lease_and_allows_retry() -> None:
    radio = FakeWaterfallRadio()
    radio.start_error = RuntimeError("synthetic start failure")
    session = WaterfallSession(radio)

    with pytest.raises(RuntimeError, match="synthetic start failure"):
        session.subscribe()

    failed = session.snapshot()
    assert failed.state is WaterfallSessionState.FAILED
    assert failed.consumer_count == 0
    assert failed.last_error == "RuntimeError: synthetic start failure"

    radio.start_error = None
    lease = session.subscribe()

    assert radio.start_calls == [3.0, 3.0]
    assert radio.status_calls == [3.0, 3.0]
    assert session.snapshot().state is WaterfallSessionState.RUNNING
    lease.close()


def test_last_lease_stop_failure_is_reported_without_leaking_demand() -> None:
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    lease = session.subscribe()
    radio.stop_error = OSError("synthetic stop failure")

    with pytest.raises(OSError, match="synthetic stop failure"):
        lease.close()

    snapshot = session.snapshot()
    assert lease.closed
    assert snapshot.state is WaterfallSessionState.FAILED
    assert snapshot.consumer_count == 0
    assert snapshot.last_error == "OSError: synthetic stop failure"


def test_transport_interruption_retains_demand_and_recovery_restarts_once() -> None:
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    lease = session.subscribe()

    session.mark_interrupted()

    assert session.snapshot().state is WaterfallSessionState.INTERRUPTED
    assert session.snapshot().consumer_count == 1

    session.recover(timeout=0.5)

    assert radio.start_calls == [3.0, 0.5]
    assert radio.status_calls == [3.0, 0.5]
    assert session.snapshot().state is WaterfallSessionState.RUNNING
    lease.close()


def test_recovery_is_a_noop_without_an_interrupted_session() -> None:
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    lease = session.subscribe()

    session.recover(timeout=0.5)

    assert radio.start_calls == [3.0]
    assert radio.status_calls == [3.0]
    assert session.snapshot().state is WaterfallSessionState.RUNNING
    lease.close()


def test_due_poll_requests_one_shared_gwf_frame() -> None:
    radio = FakeWaterfallRadio()
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_interval=0.25,
        poll_timeout=0.75,
        poll_clock=lambda: clock[0],
    )
    lease = session.subscribe()
    lease.get(0)
    lease.get(0)

    assert session.poll() is False
    clock[0] = 100.24
    assert session.poll() is False
    clock[0] = 100.25
    assert session.poll() is True

    delivery = lease.get(0)
    assert isinstance(delivery.response, GwfResponse)
    assert radio.poll_calls == [0.75]
    snapshot = session.snapshot()
    assert snapshot.gwf_poll_interval_seconds == 0.25
    assert snapshot.gwf_max_consecutive_failures == 3
    assert snapshot.gwf_requests == 2
    assert snapshot.gwf_skipped_poll_deadlines == 0
    assert snapshot.last_gwf_scheduler_lag_seconds == 0.0
    assert snapshot.maximum_gwf_scheduler_lag_seconds == 0.0
    assert snapshot.gwf_round_trip_samples == 1
    assert snapshot.last_gwf_round_trip_seconds == 0.0
    assert snapshot.average_gwf_round_trip_seconds == 0.0
    assert snapshot.maximum_gwf_round_trip_seconds == 0.0
    assert snapshot.gwf_poll_failures == 0
    assert snapshot.consecutive_gwf_failures == 0
    assert snapshot.last_gwf_request_at is not None
    lease.close()


def test_late_poll_retains_phase_instead_of_drifting_from_execution_time() -> None:
    radio = FakeWaterfallRadio()
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_interval=0.25,
        poll_clock=lambda: clock[0],
    )
    lease = session.subscribe()

    clock[0] = 100.30
    assert session.poll() is True
    first = session.snapshot()
    assert first.gwf_skipped_poll_deadlines == 0
    assert first.last_gwf_scheduler_lag_seconds == pytest.approx(0.05)

    clock[0] = 100.49
    assert session.poll() is False
    clock[0] = 100.50
    assert session.poll() is True
    second = session.snapshot()
    assert second.gwf_requests == 3
    assert second.gwf_skipped_poll_deadlines == 0
    assert second.last_gwf_scheduler_lag_seconds == pytest.approx(0.0)
    assert second.maximum_gwf_scheduler_lag_seconds == pytest.approx(0.05)
    lease.close()


def test_poll_skips_expired_deadlines_without_bursting() -> None:
    radio = FakeWaterfallRadio()
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_interval=0.25,
        poll_clock=lambda: clock[0],
    )
    lease = session.subscribe()

    clock[0] = 101.0
    assert session.poll() is True
    delayed = session.snapshot()
    assert delayed.gwf_skipped_poll_deadlines == 3
    assert delayed.last_gwf_scheduler_lag_seconds == pytest.approx(0.75)

    assert session.poll() is False
    clock[0] = 101.24
    assert session.poll() is False
    clock[0] = 101.25
    assert session.poll() is True
    lease.close()


def test_successful_poll_records_bounded_round_trip_telemetry() -> None:
    radio = FakeWaterfallRadio()
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_interval=0.25,
        poll_clock=lambda: clock[0],
    )
    lease = session.subscribe()

    radio.after_poll = lambda: clock.__setitem__(0, clock[0] + 0.04)
    clock[0] = 100.25
    assert session.poll() is True

    radio.after_poll = lambda: clock.__setitem__(0, clock[0] + 0.06)
    clock[0] = 100.50
    assert session.poll() is True

    snapshot = session.snapshot()
    assert snapshot.gwf_round_trip_samples == 2
    assert snapshot.last_gwf_round_trip_seconds == pytest.approx(0.06)
    assert snapshot.average_gwf_round_trip_seconds == pytest.approx(0.05)
    assert snapshot.maximum_gwf_round_trip_seconds == pytest.approx(0.06)
    lease.close()


def test_low_rate_gst_refresh_updates_changed_frequency_range() -> None:
    radio = FakeWaterfallRadio()
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_clock=lambda: clock[0],
        status_poll_interval=1.0,
        status_poll_timeout=0.4,
    )
    lease = session.subscribe()
    initial = session.snapshot()
    assert initial.gst_requests == 1
    assert initial.waterfall_status_revision == 1
    assert initial.waterfall_status is not None
    assert initial.waterfall_status.lower_frequency == "1540000"

    radio.status_frequency_offset = 250
    clock[0] = 101.0
    assert session.poll() is True

    refreshed = session.snapshot()
    assert radio.status_calls == [3.0, 0.4]
    assert refreshed.gst_requests == 2
    assert refreshed.gst_skipped_poll_deadlines == 0
    assert refreshed.gst_poll_failures == 0
    assert refreshed.waterfall_status_revision == 2
    assert refreshed.waterfall_status_refreshed_at is not None
    assert refreshed.waterfall_status_changed_at is not None
    assert refreshed.waterfall_status is not None
    assert refreshed.waterfall_status.lower_frequency == "1540250"
    assert refreshed.waterfall_status.center_frequency == "1550250"
    assert refreshed.waterfall_status.upper_frequency == "1560250"
    lease.close()


def test_late_unchanged_gst_refresh_skips_expired_slots_without_revision() -> None:
    radio = FakeWaterfallRadio()
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_clock=lambda: clock[0],
        status_poll_interval=1.0,
    )
    lease = session.subscribe()

    clock[0] = 103.1
    assert session.poll() is True
    refreshed = session.snapshot()
    assert refreshed.gst_requests == 2
    assert refreshed.gst_skipped_poll_deadlines == 2
    assert refreshed.waterfall_status_revision == 1

    assert session.poll() is False
    assert radio.status_calls == [3.0, 1.0]
    lease.close()


def test_gst_refresh_failure_preserves_frames_and_last_valid_range() -> None:
    radio = FakeWaterfallRadio()
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_clock=lambda: clock[0],
        status_poll_interval=1.0,
    )
    lease = session.subscribe()

    radio.status_errors_remaining = 1
    radio.status_frequency_offset = 500
    clock[0] = 101.0
    assert session.poll() is True
    failed = session.snapshot()
    assert failed.state is WaterfallSessionState.RUNNING
    assert failed.gst_poll_failures == 1
    assert failed.last_gst_error == "RuntimeError: synthetic GST timeout"
    assert failed.waterfall_status_revision == 1
    assert failed.waterfall_status is not None
    assert failed.waterfall_status.lower_frequency == "1540000"

    clock[0] = 102.0
    assert session.poll() is True
    recovered = session.snapshot()
    assert recovered.gst_poll_failures == 1
    assert recovered.last_gst_error is None
    assert recovered.waterfall_status_revision == 2
    assert recovered.waterfall_status is not None
    assert recovered.waterfall_status.lower_frequency == "1540500"
    lease.close()


def test_one_missed_gwf_poll_is_tolerated_and_next_success_recovers() -> None:
    radio = FakeWaterfallRadio()
    radio.poll_errors_remaining = 1
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_interval=0.25,
        poll_clock=lambda: clock[0],
    )
    lease = session.subscribe()

    clock[0] = 100.25
    assert session.poll() is False
    missed = session.snapshot()
    assert missed.state is WaterfallSessionState.RUNNING
    assert missed.gwf_requests == 2
    assert missed.gwf_poll_failures == 1
    assert missed.consecutive_gwf_failures == 1
    assert missed.last_gwf_failure_at is not None
    assert missed.last_gwf_error == "RuntimeError: synthetic GWF timeout"

    clock[0] = 100.50
    assert session.poll() is True
    recovered = session.snapshot()
    assert recovered.state is WaterfallSessionState.RUNNING
    assert recovered.gwf_requests == 3
    assert recovered.gwf_poll_failures == 1
    assert recovered.consecutive_gwf_failures == 0
    lease.close()


def test_consecutive_gwf_poll_failure_threshold_fails_session() -> None:
    radio = FakeWaterfallRadio()
    radio.poll_errors_remaining = 3
    clock = [100.0]
    session = WaterfallSession(
        radio,
        poll_interval=0.25,
        max_consecutive_poll_failures=3,
        poll_clock=lambda: clock[0],
    )
    lease = session.subscribe()

    for _attempt in range(2):
        clock[0] += 0.25
        assert session.poll() is False
        assert session.state is WaterfallSessionState.RUNNING

    clock[0] += 0.25
    with pytest.raises(RuntimeError, match="synthetic GWF timeout"):
        session.poll()

    failed = session.snapshot()
    assert failed.state is WaterfallSessionState.FAILED
    assert failed.gwf_requests == 4
    assert failed.gwf_poll_failures == 3
    assert failed.consecutive_gwf_failures == 3
    lease.close()


def test_session_close_wakes_consumers_and_attempts_stop() -> None:
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    lease = session.subscribe()

    session.close()

    assert session.snapshot().state is WaterfallSessionState.CLOSED
    assert session.snapshot().consumer_count == 0
    assert radio.stop_calls == [2.0]
    with pytest.raises(WaterfallSubscriptionClosed):
        lease.get(0)
    with pytest.raises(RuntimeError, match="closed"):
        session.subscribe()


def test_session_transitions_are_ordered_and_callback_failures_are_isolated() -> None:
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    observed = []
    session.on_transition(observed.append)
    session.on_transition(lambda _: (_ for _ in ()).throw(RuntimeError("ignored")))

    lease = session.subscribe()
    lease.close()

    assert [transition.sequence for transition in observed] == [1, 2, 3, 4]
    assert [transition.state for transition in observed] == [
        WaterfallSessionState.STARTING,
        WaterfallSessionState.RUNNING,
        WaterfallSessionState.STOPPING,
        WaterfallSessionState.IDLE,
    ]


def test_slow_lease_overflow_does_not_degrade_another_lease() -> None:
    radio = FakeWaterfallRadio(queue_capacity=2)
    session = WaterfallSession(radio)
    slow = session.subscribe()
    fast = session.subscribe()
    slow.get(0)
    slow.get(0)

    for index in range(5):
        response = radio.publish_pwf(str(index))
        assert fast.get(0).response is response

    slow_snapshot = slow.snapshot()
    fast_snapshot = fast.snapshot()
    assert slow_snapshot.responses_dropped == 3
    assert slow_snapshot.overflows == 3
    assert fast_snapshot.responses_dropped == 0
    assert fast_snapshot.overflows == 0

    slow.close()
    fast.close()
