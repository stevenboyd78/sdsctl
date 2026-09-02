from __future__ import annotations

import socket
from datetime import UTC, datetime

import pytest

from sds200 import (
    DaemonDisconnectedError,
    DaemonEvent,
    DaemonEventClient,
    DaemonEventKind,
    DaemonProtocolError,
    DaemonRemoteClientError,
    DaemonRemoteClientErrorReason,
    DaemonRemoteReconnectPolicy,
    DaemonWaterfallClient,
    DaemonWaterfallRecord,
    DaemonWaterfallRecordKind,
    daemon_remote_error_is_reconnectable,
)


class RemoteTransport:
    sanitizes_private_state = True

    def connect(self, *, timeout: float) -> socket.socket:
        del timeout
        raise AssertionError("The patched receive method owns this test.")


def test_remote_reconnect_policy_is_finite_and_exponentially_bounded() -> None:
    policy = DaemonRemoteReconnectPolicy(
        attempts=5,
        initial_delay=0.25,
        max_delay=1.0,
    )

    assert [policy.delay(attempt) for attempt in range(1, 6)] == [
        0.25,
        0.5,
        1.0,
        1.0,
        1.0,
    ]
    with pytest.raises(ValueError):
        policy.delay(6)
    with pytest.raises(ValueError):
        DaemonRemoteReconnectPolicy(attempts=-1)
    with pytest.raises(ValueError):
        DaemonRemoteReconnectPolicy(initial_delay=2, max_delay=1)


def test_only_remote_transport_disconnects_are_reconnectable() -> None:
    assert daemon_remote_error_is_reconnectable(DaemonDisconnectedError("closed"))
    assert daemon_remote_error_is_reconnectable(
        DaemonRemoteClientError(DaemonRemoteClientErrorReason.CONNECT_FAILED)
    )
    for reason in (
        DaemonRemoteClientErrorReason.CONFIGURATION_FAILED,
        DaemonRemoteClientErrorReason.TLS_HANDSHAKE_FAILED,
        DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED,
        DaemonRemoteClientErrorReason.SERVICE_NEGOTIATION_FAILED,
    ):
        assert not daemon_remote_error_is_reconnectable(
            DaemonRemoteClientError(reason)
        )
    assert not daemon_remote_error_is_reconnectable(
        DaemonProtocolError("malformed")
    )


def test_remote_event_watch_reconnects_and_requires_new_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DaemonEventClient(RemoteTransport())
    snapshot = DaemonEvent(
        sequence=100,
        observed_at=datetime(2026, 9, 2, tzinfo=UTC),
        kind=DaemonEventKind.SNAPSHOT,
        payload={},
    )
    items: list[DaemonEvent | Exception] = [
        DaemonDisconnectedError("closed"),
        DaemonRemoteClientError(DaemonRemoteClientErrorReason.CONNECT_FAILED),
        snapshot,
    ]

    def receive() -> DaemonEvent:
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(client, "receive", receive)
    policy = DaemonRemoteReconnectPolicy(
        attempts=2,
        initial_delay=0,
        max_delay=0,
    )

    assert list(client.watch(count=1, reconnect_policy=policy)) == [snapshot]
    assert items == []


def test_remote_event_watch_does_not_retry_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DaemonEventClient(RemoteTransport())
    calls = 0

    def receive() -> DaemonEvent:
        nonlocal calls
        calls += 1
        raise DaemonRemoteClientError(
            DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED
        )

    monkeypatch.setattr(client, "receive", receive)
    policy = DaemonRemoteReconnectPolicy(
        attempts=5,
        initial_delay=0,
        max_delay=0,
    )

    with pytest.raises(DaemonRemoteClientError):
        list(client.watch(count=1, reconnect_policy=policy))
    assert calls == 1


def test_remote_waterfall_watch_reconnects_from_fresh_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = DaemonWaterfallClient(RemoteTransport())
    checkpoint = DaemonWaterfallRecord(
        sequence=40,
        observed_at=datetime(2026, 9, 2, tzinfo=UTC),
        kind=DaemonWaterfallRecordKind.SESSION_CHECKPOINT,
        payload={"state": "running"},
    )
    items: list[DaemonWaterfallRecord | Exception] = [
        DaemonDisconnectedError("closed"),
        checkpoint,
    ]

    def receive() -> DaemonWaterfallRecord:
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(client, "receive", receive)
    policy = DaemonRemoteReconnectPolicy(
        attempts=1,
        initial_delay=0,
        max_delay=0,
    )

    assert list(client.watch(count=1, reconnect_policy=policy)) == [checkpoint]
    assert items == []


def test_reconnect_policy_is_rejected_for_local_stream_clients(tmp_path) -> None:
    from sds200 import DaemonSocketLocation, DaemonSocketSource

    location = DaemonSocketLocation(
        tmp_path / "daemon.sock",
        DaemonSocketSource.EXPLICIT,
    )
    policy = DaemonRemoteReconnectPolicy(attempts=0)

    with pytest.raises(ValueError, match="only.*authenticated remote"):
        list(DaemonEventClient(location).watch(count=1, reconnect_policy=policy))
    with pytest.raises(ValueError, match="only.*authenticated remote"):
        list(DaemonWaterfallClient(location).watch(count=1, reconnect_policy=policy))
