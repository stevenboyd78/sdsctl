from __future__ import annotations

import json
import stat
import time
from datetime import UTC, datetime

import pytest

from sds200.daemon_ipc import (
    DaemonSocketListener,
    resolve_daemon_waterfall_socket_location,
)
from sds200.daemon_waterfall_client import (
    DaemonWaterfallClient,
    _decode_record,
)
from sds200.daemon_waterfall_protocol import (
    DAEMON_WATERFALL_PROTOCOL,
    DAEMON_WATERFALL_VERSION,
    DaemonWaterfallRecord,
    DaemonWaterfallRecordKind,
)
from sds200.daemon_waterfall_server import DaemonWaterfallServer
from sds200.exceptions import DaemonProtocolError
from sds200.models import DisplayLine, GstResponse, GwfResponse, Packet, PwfResponse
from sds200.parser import PacketParser
from sds200.waterfall_session import WaterfallSession, WaterfallSessionState
from sds200.waterfall_subscriptions import WaterfallPublisher, WaterfallSubscription


class FakeWaterfallRadio:
    def __init__(self) -> None:
        self.publisher = WaterfallPublisher(queue_capacity=8)
        self.parser = PacketParser()
        self.status_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    def get_waterfall_status(self, *, timeout: float = 2.0) -> GstResponse:
        del timeout
        self.status_calls += 1
        return GstResponse(
            display_form="00000",
            lines=tuple(DisplayLine("", "") for _ in range(5)),
            mute="0",
            alert_led="0",
            charge_led="0",
            waterfall_mode="1",
            marker_frequency="1555500",
            modulation="NFM",
            marker_position="120",
            center_frequency="1550000",
            lower_frequency="1540000",
            upper_frequency="1560000",
            color_mode="0",
            fft_area_size="1",
            packet=Packet(command="GST", fields=(), raw="GST"),
        )

    def start_waterfall_publication(
        self,
        *,
        timeout: float = 3.0,
    ) -> tuple[PwfResponse, GwfResponse]:
        del timeout
        self.start_calls += 1
        pwf = self._pwf("first")
        gwf = self._gwf()
        self.publisher.publish(pwf)
        self.publisher.publish(gwf)
        return pwf, gwf

    def stop_waterfall_publication(self, *, timeout: float = 2.0) -> None:
        del timeout
        self.stop_calls += 1

    def subscribe_waterfall(self) -> WaterfallSubscription:
        return self.publisher.subscribe()

    def waterfall_snapshot(self):  # type: ignore[no-untyped-def]
        return self.publisher.snapshot()

    def publish(self, value: str) -> PwfResponse:
        response = self._pwf(value)
        self.publisher.publish(response)
        return response

    def _pwf(self, value: str) -> PwfResponse:
        parsed = self.parser.parse_typed(
            self.parser.parse_packet(f"PWF,{value}")
        )
        assert isinstance(parsed, PwfResponse)
        return parsed

    def _gwf(self) -> GwfResponse:
        parsed = self.parser.parse_typed(
            self.parser.parse_packet(
                "GWF," + ",".join(str(index) for index in range(240))
            )
        )
        assert isinstance(parsed, GwfResponse)
        return parsed


def _wait_for(predicate, *, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for waterfall test condition.")
        time.sleep(0.01)


def test_waterfall_record_json_contract_is_canonical_and_versioned() -> None:
    record = DaemonWaterfallRecord(
        sequence=1,
        observed_at=datetime(2026, 8, 25, tzinfo=UTC),
        kind=DaemonWaterfallRecordKind.SESSION_CHECKPOINT,
        payload={"state": "running"},
    )

    encoded = record.to_json_line()
    decoded = json.loads(encoded)

    assert encoded.endswith(b"\n")
    assert decoded == {
        "kind": "session.checkpoint",
        "observed_at": "2026-08-25T00:00:00+00:00",
        "payload": {"state": "running"},
        "protocol": DAEMON_WATERFALL_PROTOCOL,
        "sequence": 1,
        "version": DAEMON_WATERFALL_VERSION,
    }
    assert _decode_record(encoded.rstrip(b"\n")) == record


def test_waterfall_client_rejects_missing_checkpoint_and_sequence_gap(tmp_path) -> None:
    client = DaemonWaterfallClient(
        resolve_daemon_waterfall_socket_location(tmp_path / "waterfall.sock")
    )
    response = DaemonWaterfallRecord(
        sequence=1,
        observed_at=datetime.now(UTC),
        kind=DaemonWaterfallRecordKind.PWF,
        payload={},
    )

    with pytest.raises(DaemonProtocolError, match="begin with a session checkpoint"):
        client._validate_order(response)

    checkpoint = DaemonWaterfallRecord(
        sequence=1,
        observed_at=datetime.now(UTC),
        kind=DaemonWaterfallRecordKind.SESSION_CHECKPOINT,
        payload={},
    )
    client._validate_order(checkpoint)
    gap = DaemonWaterfallRecord(
        sequence=3,
        observed_at=datetime.now(UTC),
        kind=DaemonWaterfallRecordKind.PWF,
        payload={},
    )
    with pytest.raises(DaemonProtocolError, match="not contiguous"):
        client._validate_order(gap)


def test_waterfall_unix_stream_starts_on_first_client_and_stops_on_departure(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    location = resolve_daemon_waterfall_socket_location(tmp_path / "waterfall.sock")
    server = DaemonWaterfallServer(
        DaemonSocketListener(location),
        session,
        accept_poll_interval=0.01,
    )
    server.start()

    assert stat.S_IMODE(location.path.stat().st_mode) == 0o600
    client = DaemonWaterfallClient(location)
    checkpoint = client.receive()
    pwf = client.receive()
    gwf = client.receive()

    assert checkpoint.sequence == 1
    assert checkpoint.kind is DaemonWaterfallRecordKind.SESSION_CHECKPOINT
    assert checkpoint.payload["state"] == "running"
    status = checkpoint.payload["waterfall_status"]
    assert isinstance(status, dict)
    assert status["waterfall_mode"] == "1"
    assert status["lower_frequency"] == "1540000"
    assert pwf.sequence == 2
    assert pwf.kind is DaemonWaterfallRecordKind.PWF
    assert pwf.payload["source_sequence"] == 1
    assert gwf.sequence == 3
    assert gwf.kind is DaemonWaterfallRecordKind.GWF
    assert len(gwf.payload["values"]) == 240  # type: ignore[arg-type]
    live_session = gwf.payload["session"]
    assert isinstance(live_session, dict)
    assert live_session["state"] == "running"
    assert live_session["waterfall_status_revision"] == 1
    assert live_session["waterfall_status"]["lower_frequency"] == "1540000"
    assert radio.status_calls == 1
    assert radio.start_calls == 1

    client.close()
    _wait_for(lambda: session.consumer_count == 0)

    assert radio.stop_calls == 1
    assert session.state is WaterfallSessionState.IDLE
    server.stop()
    assert not location.path.exists()


def test_multiple_waterfall_clients_share_one_scanner_session(tmp_path) -> None:  # type: ignore[no-untyped-def]
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    location = resolve_daemon_waterfall_socket_location(tmp_path / "waterfall.sock")
    server = DaemonWaterfallServer(
        DaemonSocketListener(location),
        session,
        accept_poll_interval=0.01,
    )
    server.start()
    first = DaemonWaterfallClient(location)
    second = DaemonWaterfallClient(location)

    assert first.receive().kind is DaemonWaterfallRecordKind.SESSION_CHECKPOINT
    assert first.receive().kind is DaemonWaterfallRecordKind.PWF
    assert first.receive().kind is DaemonWaterfallRecordKind.GWF
    assert second.receive().kind is DaemonWaterfallRecordKind.SESSION_CHECKPOINT
    assert radio.start_calls == 1
    assert session.consumer_count == 2

    published = radio.publish("shared")
    first_record = first.receive()
    second_record = second.receive()
    assert first_record.payload["values"] == list(published.values)
    assert second_record.payload["values"] == list(published.values)

    first.close()
    _wait_for(lambda: session.consumer_count == 1)
    assert radio.stop_calls == 0
    second.close()
    _wait_for(lambda: session.consumer_count == 0)
    assert radio.stop_calls == 1
    server.stop()


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"protocol":"wrong"}',
        (
            b'{"kind":"waterfall.future","observed_at":"2026-08-25T00:00:00Z",'
            b'"payload":{},"protocol":"sdsctl.waterfall","sequence":1,"version":1}'
        ),
    ],
)
def test_waterfall_client_rejects_invalid_records(payload: bytes) -> None:
    with pytest.raises(DaemonProtocolError):
        _decode_record(payload)
