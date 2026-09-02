from __future__ import annotations

import base64
import json
import queue
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from sds200 import (
    DAEMON_REMOTE_AUDIO_ENDPOINT,
    DAEMON_REMOTE_EVENT_REDACTED_FIELDS,
    DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES,
    DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES_PER_CLIENT,
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteAuthenticatedPeer,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteClientIdentity,
    DaemonRemoteCredentialAuthority,
    DaemonRemoteListenerConfiguration,
    DaemonRemoteObservationBroker,
    DaemonRemoteObservationError,
    DaemonRemoteObservationErrorReason,
    DaemonRemoteObservationKind,
    PcmuPacket,
    PcmuPublisher,
    WaterfallPublisher,
)
from sds200.models import GwfResponse
from sds200.parser import PacketParser
from sds200.waterfall_subscriptions import WaterfallSubscription


class EventSource:
    def __init__(self, *, queue_capacity: int = 4) -> None:
        self.publisher = DaemonEventPublisher(
            lambda: {
                "state": "running",
                "scanner_endpoint": "udp://192.168.20.25:50536",
                "recording": {"filename": "private.wav"},
                "nested": {
                    "endpoint": "rtsp://192.168.20.25/audio",
                    "safe": "visible",
                },
                "items": [
                    {"safe": 1, "private_path": "/private/state"},
                ],
            },
            queue_capacity=queue_capacity,
        )

    @property
    def subscriber_count(self) -> int:
        return self.publisher.subscriber_count

    def subscribe(self):  # type: ignore[no-untyped-def]
        return self.publisher.subscribe()


class FailingSource:
    def subscribe(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("private source detail")


class CloseFailureSubscription:
    def get(self, timeout: float | None = None):  # type: ignore[no-untyped-def]
        del timeout
        raise queue.Empty

    def close(self) -> None:
        raise RuntimeError("private close detail")


class CloseFailureEventSource:
    def subscribe(self) -> CloseFailureSubscription:
        return CloseFailureSubscription()


class CountingWaterfallLease:
    def __init__(
        self,
        source: CountingWaterfallSource,
        subscription: WaterfallSubscription,
    ) -> None:
        self.source = source
        self.subscription = subscription
        self.closed = False

    def get(self, timeout: float | None = None):  # type: ignore[no-untyped-def]
        return self.subscription.get(timeout)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.subscription.close()
        self.source.consumers -= 1
        if self.source.consumers == 0:
            self.source.stops += 1


class CountingWaterfallSource:
    def __init__(self, *, queue_capacity: int = 2) -> None:
        self.publisher = WaterfallPublisher(queue_capacity=queue_capacity)
        self.consumers = 0
        self.starts = 0
        self.stops = 0

    def subscribe(self) -> CountingWaterfallLease:
        subscription = self.publisher.subscribe()
        if self.consumers == 0:
            self.starts += 1
        self.consumers += 1
        return CountingWaterfallLease(self, subscription)

    def publish(self, offset: int) -> None:
        parser = PacketParser()
        response = parser.parse_typed(
            parser.parse_packet("GWF," + ",".join(str(offset + index) for index in range(240)))
        )
        self.publisher.publish(cast(GwfResponse, response))


def _peer(
    client_id: str = "pi-display",
    *,
    observe: bool = True,
) -> DaemonRemoteAuthenticatedPeer:
    scope = (
        DaemonRemoteAuthorizationScope.OBSERVE
        if observe
        else DaemonRemoteAuthorizationScope.CONTROL
    )
    return DaemonRemoteAuthenticatedPeer(DaemonRemoteAuthenticatedIdentity(client_id, (scope,)))


def _packet(sequence: int) -> PcmuPacket:
    return PcmuPacket(
        endpoint="rtsp://192.168.20.25/au:scanner.au",
        sequence=sequence,
        timestamp=sequence * 160,
        ssrc=1234,
        payload=b"private audio payload",
        observed_at=datetime(2026, 9, 2, 9, 0, tzinfo=UTC),
    )


def _broker(
    *,
    event_source: EventSource | None = None,
    waterfall_source: CountingWaterfallSource | None = None,
    audio_source: PcmuPublisher | None = None,
    max_leases: int = 24,
    max_leases_per_client: int = 3,
) -> DaemonRemoteObservationBroker:
    return DaemonRemoteObservationBroker(
        event_stream=event_source,
        waterfall_session=waterfall_source,  # type: ignore[arg-type]
        pcmu_stream=audio_source,
        max_leases=max_leases,
        max_leases_per_client=max_leases_per_client,
    )


def _write_credential(path: Path) -> None:
    encoded = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=")
    path.write_bytes(encoded + b"\n")
    path.chmod(0o600)


def _managed_peer(tmp_path: Path):  # type: ignore[no-untyped-def]
    credential = tmp_path / "private-display.secret"
    _write_credential(credential)
    configuration = DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address="192.168.20.10",
        port=50443,
        certificate_file=tmp_path / "private-server.crt",
        private_key_file=tmp_path / "private-server.key",
        clients=(DaemonRemoteClientIdentity("private-display", credential),),
    )
    authority = DaemonRemoteCredentialAuthority(configuration)
    identity = DaemonRemoteAuthenticatedIdentity(
        "private-display",
        (DaemonRemoteAuthorizationScope.OBSERVE,),
    )
    socket_invalidations: list[str] = []
    session = authority.register_session(
        authority.current_generation(),
        identity,
        invalidator=lambda: socket_invalidations.append("socket"),
    )
    peer = DaemonRemoteAuthenticatedPeer(
        identity,
        credential_session=session,
    )
    return configuration, authority, peer, socket_invalidations


def test_broker_defaults_and_snapshot_are_bounded_and_redacted() -> None:
    broker = _broker()

    snapshot = broker.snapshot()

    assert snapshot.max_leases == DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES
    assert snapshot.max_leases_per_client == (
        DAEMON_REMOTE_OBSERVATION_DEFAULT_MAX_LEASES_PER_CLIENT
    )
    assert snapshot.active_leases == 0
    assert snapshot.as_dict()["event_leases"] == 0
    assert "private-display" not in json.dumps(snapshot.as_dict())
    assert "pi-display" not in repr(broker)
    assert tuple(sorted(DAEMON_REMOTE_EVENT_REDACTED_FIELDS)) == DAEMON_REMOTE_EVENT_REDACTED_FIELDS


def test_event_lease_filters_recording_and_redacts_private_fields() -> None:
    events = EventSource()
    broker = _broker(event_source=events)
    lease = broker.subscribe_events(_peer())

    snapshot = lease.get(0)
    assert snapshot.kind == DaemonEventKind.SNAPSHOT
    assert snapshot.payload == {
        "state": "running",
        "nested": {"safe": "visible"},
        "items": ({"safe": 1},),
    }

    events.publisher.publish(
        DaemonEventKind.RECORDING_STATE,
        {"recording": "private.wav"},
    )
    events.publisher.publish(
        DaemonEventKind.SCANNER_CONNECTION,
        {
            "endpoint": "udp://192.168.20.25:50536",
            "connected": True,
            "nested_secret": "private-credential",
        },
    )
    observed = lease.get(0)

    assert observed.kind == DaemonEventKind.SCANNER_CONNECTION
    assert observed.payload == {"connected": True}
    rendered = observed.to_json_line().decode()
    assert "192.168.20.25" not in rendered
    assert "private" not in rendered
    assert broker.snapshot().filtered_events == 1

    lease.close()
    assert events.subscriber_count == 0


def test_slow_event_peer_cannot_delay_or_overflow_another_peer() -> None:
    events = EventSource(queue_capacity=2)
    broker = _broker(event_source=events)
    slow = broker.subscribe_events(_peer("slow-display"))
    healthy = broker.subscribe_events(_peer("healthy-display"))
    slow.get(0)
    healthy.get(0)

    healthy_sequences = []
    for sequence in range(1, 6):
        event = events.publisher.publish(
            DaemonEventKind.RADIO_STATE,
            {"sequence": sequence},
        )
        healthy_sequences.append(healthy.get(0).sequence)
        assert healthy_sequences[-1] == event.sequence

    assert healthy_sequences == [1, 2, 3, 4, 5]
    assert [slow.get(0).sequence, slow.get(0).sequence] == [4, 5]
    with pytest.raises(queue.Empty):
        slow.get(0)

    slow.close()
    healthy.close()


def test_waterfall_leases_share_demand_and_release_independently() -> None:
    waterfall = CountingWaterfallSource()
    broker = _broker(waterfall_source=waterfall)
    first = broker.subscribe_waterfall(_peer("first-display"))
    second = broker.subscribe_waterfall(_peer("second-display"))

    assert waterfall.starts == 1
    assert waterfall.consumers == 2
    waterfall.publish(10)
    assert first.get(0).sequence == 1
    assert second.get(0).sequence == 1

    first.close()
    assert waterfall.consumers == 1
    assert waterfall.stops == 0
    second.close()
    assert waterfall.consumers == 0
    assert waterfall.stops == 1


def test_audio_lease_preserves_delivery_but_redacts_scanner_endpoint() -> None:
    audio = PcmuPublisher(queue_capacity=2)
    broker = _broker(audio_source=audio)
    lease = broker.subscribe_audio(_peer())
    original = _packet(10)

    publication = audio.publish(original)
    delivery = lease.get(0)

    assert delivery.stream_sequence == publication.stream_sequence
    assert delivery.packet.endpoint == DAEMON_REMOTE_AUDIO_ENDPOINT
    assert delivery.packet.payload == original.payload
    assert delivery.packet.sequence == original.sequence
    assert original.endpoint not in repr(delivery)
    assert audio.subscriber_count == 1

    lease.close()
    assert audio.subscriber_count == 0


def test_slow_audio_peer_drops_only_its_own_bounded_queue() -> None:
    audio = PcmuPublisher(queue_capacity=2)
    broker = _broker(audio_source=audio)
    slow = broker.subscribe_audio(_peer("slow-audio"))
    healthy = broker.subscribe_audio(_peer("healthy-audio"))

    for sequence in range(10, 15):
        audio.publish(_packet(sequence))
        assert healthy.get(0).packet.sequence == sequence

    slow_delivery = slow.get(0)
    assert slow_delivery.stream_sequence == 4
    assert slow_delivery.packets_dropped == 3
    assert slow_delivery.overflows == 3
    assert healthy.closed is False

    slow.close()
    healthy.close()


def test_capacity_duplicate_authorization_and_unavailable_fail_closed() -> None:
    events = EventSource()
    waterfall = CountingWaterfallSource()
    broker = _broker(
        event_source=events,
        waterfall_source=waterfall,
        max_leases=2,
        max_leases_per_client=1,
    )
    first_peer = _peer("first")
    first = broker.subscribe_events(first_peer)

    with pytest.raises(DaemonRemoteObservationError) as duplicate:
        broker.subscribe_events(first_peer)
    assert duplicate.value.reason is (DaemonRemoteObservationErrorReason.DUPLICATE_LEASE)
    with pytest.raises(DaemonRemoteObservationError) as per_client:
        broker.subscribe_waterfall(first_peer)
    assert per_client.value.reason is (DaemonRemoteObservationErrorReason.CAPACITY_EXCEEDED)
    second = broker.subscribe_waterfall(_peer("second"))
    with pytest.raises(DaemonRemoteObservationError) as total:
        broker.subscribe_events(_peer("third"))
    assert total.value.reason is (DaemonRemoteObservationErrorReason.CAPACITY_EXCEEDED)
    with pytest.raises(DaemonRemoteObservationError) as unauthorized:
        _broker(event_source=events).subscribe_events(_peer("control-only", observe=False))
    assert unauthorized.value.reason is (DaemonRemoteObservationErrorReason.AUTHORIZATION_DENIED)
    with pytest.raises(DaemonRemoteObservationError) as unavailable:
        _broker().subscribe_audio(_peer("no-audio"))
    assert unavailable.value.reason is (DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE)

    assert broker.snapshot().rejected_leases == 3
    first.close()
    second.close()


def test_rotation_immediately_expires_all_child_leases(
    tmp_path: Path,
) -> None:
    configuration, authority, peer, socket_invalidations = _managed_peer(tmp_path)
    events = EventSource()
    waterfall = CountingWaterfallSource()
    audio = PcmuPublisher()
    broker = _broker(
        event_source=events,
        waterfall_source=waterfall,
        audio_source=audio,
    )
    event_lease = broker.subscribe_events(peer)
    waterfall_lease = broker.subscribe_waterfall(peer)
    audio_lease = broker.subscribe_audio(peer)

    assert broker.snapshot().active_leases == 3
    assert waterfall.consumers == 1
    authority.reload(configuration)

    snapshot = broker.snapshot()
    assert snapshot.active_leases == 0
    assert snapshot.released_leases == 3
    assert snapshot.expired_leases == 3
    assert events.subscriber_count == 0
    assert waterfall.consumers == 0
    assert waterfall.stops == 1
    assert audio.subscriber_count == 0
    assert socket_invalidations == ["socket"]
    for lease in (event_lease, waterfall_lease, audio_lease):
        assert lease.closed is True
        assert lease.expired is True
        with pytest.raises(DaemonRemoteObservationError) as captured:
            lease.get(0)
        assert captured.value.reason is (DaemonRemoteObservationErrorReason.AUTHENTICATION_EXPIRED)


def test_expired_peer_cannot_acquire_a_source_lease(tmp_path: Path) -> None:
    _, _, peer, _ = _managed_peer(tmp_path)
    assert peer.credential_session is not None
    peer.credential_session.close()
    broker = _broker(event_source=EventSource())

    with pytest.raises(DaemonRemoteObservationError) as captured:
        broker.subscribe_events(peer)

    assert captured.value.reason is (DaemonRemoteObservationErrorReason.AUTHENTICATION_EXPIRED)
    assert broker.snapshot().active_leases == 0
    assert broker.snapshot().rejected_leases == 1


def test_broker_close_releases_leases_but_not_owned_publishers() -> None:
    events = EventSource()
    waterfall = CountingWaterfallSource()
    audio = PcmuPublisher()
    broker = _broker(
        event_source=events,
        waterfall_source=waterfall,
        audio_source=audio,
    )
    leases = (
        broker.subscribe_events(_peer("events")),
        broker.subscribe_waterfall(_peer("waterfall")),
        broker.subscribe_audio(_peer("audio")),
    )

    broker.close()
    broker.close()

    assert all(lease.closed for lease in leases)
    assert broker.snapshot().closed is True
    assert broker.snapshot().active_leases == 0
    assert events.publisher.closed is False
    assert waterfall.publisher.closed is False
    assert audio.closed is False
    with pytest.raises(DaemonRemoteObservationError) as captured:
        broker.subscribe_events(_peer("late"))
    assert captured.value.reason is (DaemonRemoteObservationErrorReason.BROKER_CLOSED)


def test_context_managers_release_broker_and_lease() -> None:
    events = EventSource()

    with _broker(event_source=events) as broker:
        with broker.subscribe_events(_peer()) as lease:
            assert lease.get().kind == DaemonEventKind.SNAPSHOT
            assert "pi-display" not in repr(lease)
            assert events.subscriber_count == 1
        assert lease.closed is True
        assert events.subscriber_count == 0

    assert broker.snapshot().closed is True


def test_source_close_and_acquisition_failures_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing_close = DaemonRemoteObservationBroker(
        event_stream=CloseFailureEventSource(),  # type: ignore[arg-type]
        waterfall_session=None,
        pcmu_stream=None,
    )
    lease = failing_close.subscribe_events(_peer("close-failure"))
    with pytest.raises(DaemonRemoteObservationError) as close_error:
        lease.close()
    assert close_error.value.reason is (DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE)
    assert "private" not in str(close_error.value)
    assert failing_close.snapshot().active_leases == 0

    failing_source = DaemonRemoteObservationBroker(
        event_stream=FailingSource(),  # type: ignore[arg-type]
        waterfall_session=None,
        pcmu_stream=None,
    )
    with pytest.raises(DaemonRemoteObservationError) as source_error:
        failing_source.subscribe_events(_peer("source-failure"))
    assert source_error.value.reason is (DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE)
    assert "private" not in str(source_error.value)

    events = EventSource()
    invalidator_failure = _broker(event_source=events)

    def fail_invalidator(
        self: DaemonRemoteAuthenticatedPeer,
        invalidator: object,
    ) -> object:
        del self, invalidator
        raise RuntimeError("private invalidator detail")

    monkeypatch.setattr(
        DaemonRemoteAuthenticatedPeer,
        "on_credentials_invalidated",
        fail_invalidator,
    )
    with pytest.raises(DaemonRemoteObservationError) as invalidator_error:
        invalidator_failure.subscribe_events(_peer("invalidator-failure"))
    assert invalidator_error.value.reason is (DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE)
    assert events.subscriber_count == 0


def test_underlying_source_shutdown_closes_only_the_remote_lease() -> None:
    events = EventSource()
    broker = _broker(event_source=events)
    lease = broker.subscribe_events(_peer())
    lease.get(0)
    events.publisher.close()

    with pytest.raises(DaemonRemoteObservationError) as captured:
        lease.get(0)

    assert captured.value.reason is (DaemonRemoteObservationErrorReason.LEASE_CLOSED)
    assert lease.closed is True
    assert lease.expired is False
    assert broker.snapshot().active_leases == 0
    with pytest.raises(DaemonRemoteObservationError) as closed_again:
        lease.get(0)
    assert closed_again.value.reason is (DaemonRemoteObservationErrorReason.LEASE_CLOSED)


@pytest.mark.parametrize("kind", ["events", "waterfall"])
def test_each_absent_source_has_the_same_redacted_failure(kind: str) -> None:
    broker = _broker()
    with pytest.raises(DaemonRemoteObservationError) as captured:
        if kind == "events":
            broker.subscribe_events(_peer(f"absent-{kind}"))
        else:
            broker.subscribe_waterfall(_peer(f"absent-{kind}"))
    assert captured.value.reason is (DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE)


@pytest.mark.parametrize(
    ("keyword", "value", "error_type", "message"),
    [
        ("max_leases", True, TypeError, "must be an integer"),
        ("max_leases", 0, ValueError, "greater than zero"),
        ("max_leases_per_client", 0, ValueError, "greater than zero"),
    ],
)
def test_broker_limits_are_strict(
    keyword: str,
    value: object,
    error_type: type[Exception],
    message: str,
) -> None:
    arguments = {keyword: value}
    with pytest.raises(error_type, match=message):
        DaemonRemoteObservationBroker(
            event_stream=None,
            waterfall_session=None,
            pcmu_stream=None,
            **arguments,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("timeout", [True, "1", float("inf"), -1.0])
def test_lease_timeout_is_strict(timeout: object) -> None:
    events = EventSource()
    lease = _broker(event_source=events).subscribe_events(_peer())
    lease.get(0)

    with pytest.raises((TypeError, ValueError), match="lease timeout"):
        lease.get(timeout)  # type: ignore[arg-type]


def test_error_and_constructor_inputs_do_not_echo_private_values() -> None:
    with pytest.raises(TypeError, match="authenticated TLS peer"):
        _broker(event_source=EventSource()).subscribe_events(
            object()  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="must provide subscribe"):
        DaemonRemoteObservationBroker(
            event_stream=object(),  # type: ignore[arg-type]
            waterfall_session=None,
            pcmu_stream=None,
        )
    with pytest.raises(ValueError, match="must not exceed"):
        _broker(max_leases=1, max_leases_per_client=2)
    with pytest.raises(TypeError, match="error reason"):
        DaemonRemoteObservationError("private")  # type: ignore[arg-type]

    error = DaemonRemoteObservationError(DaemonRemoteObservationErrorReason.SOURCE_UNAVAILABLE)
    assert "private" not in str(error)
    assert "endpoint" not in str(error)
    assert set(DaemonRemoteObservationKind) == {
        DaemonRemoteObservationKind.EVENTS,
        DaemonRemoteObservationKind.WATERFALL,
        DaemonRemoteObservationKind.AUDIO,
    }
