from __future__ import annotations

import queue
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from sds200.daemon_ipc import (
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
)
from sds200.daemon_live_audio_client import DaemonLiveAudioClient
from sds200.daemon_live_audio_server import DaemonLiveAudioServer
from sds200.exceptions import DaemonUnavailableError
from sds200.home_assistant_live_audio import LiveAudioSession


class FakePipeline:
    def __init__(self) -> None:
        self.publisher: Callable[[bytes], None] | None = None
        self.starts = 0
        self.stops = 0

    def start(self, publish: Callable[[bytes], None]) -> None:
        self.publisher = publish
        self.starts += 1

    def stop(self) -> None:
        self.publisher = None
        self.stops += 1

    def publish(self, data: bytes) -> None:
        assert self.publisher is not None
        self.publisher(data)


def _location(tmp_path: Path) -> DaemonSocketLocation:
    return DaemonSocketLocation(
        tmp_path / "live-audio.sock",
        DaemonSocketSource.EXPLICIT,
    )


def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true")
        time.sleep(0.01)


def test_two_unix_clients_share_one_daemon_encoder_session(
    tmp_path: Path,
) -> None:
    location = _location(tmp_path)
    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline)
    server = DaemonLiveAudioServer(
        DaemonSocketListener(location),
        session,
        accept_poll_interval=0.01,
    )
    server.start()
    first = DaemonLiveAudioClient(location)
    second = DaemonLiveAudioClient(location)
    first.connect()
    second.connect()

    assert pipeline.starts == 1
    assert session.snapshot().consumer_count == 2
    pipeline.publish(b"encoded-mp3")
    assert first.get(1.0) == b"encoded-mp3"
    assert second.get(1.0) == b"encoded-mp3"

    first.close()
    _wait_for(lambda: session.snapshot().consumer_count == 1)
    assert pipeline.stops == 0
    second.close()
    _wait_for(lambda: session.snapshot().consumer_count == 0)
    assert pipeline.stops == 1
    snapshot = server.snapshot()
    assert snapshot.accepted_clients == 2
    assert snapshot.chunks_sent == 2
    assert snapshot.bytes_sent == 22
    server.stop()


def test_daemon_live_audio_server_rejects_excess_clients(
    tmp_path: Path,
) -> None:
    location = _location(tmp_path)
    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline, max_leases=1)
    server = DaemonLiveAudioServer(
        DaemonSocketListener(location),
        session,
        max_clients=1,
        accept_poll_interval=0.01,
    )
    server.start()
    first = DaemonLiveAudioClient(location)
    first.connect()

    second = DaemonLiveAudioClient(location)
    with pytest.raises(DaemonUnavailableError, match="capacity"):
        second.connect()

    assert server.snapshot().rejected_clients == 1
    first.close()
    server.stop()


def test_server_stop_closes_clients_session_and_socket(tmp_path: Path) -> None:
    location = _location(tmp_path)
    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline)
    server = DaemonLiveAudioServer(
        DaemonSocketListener(location),
        session,
        accept_poll_interval=0.01,
    )
    server.start()
    client = DaemonLiveAudioClient(location)
    client.connect()
    assert location.path.exists()

    server.stop()

    assert not location.path.exists()
    assert session.snapshot().state.value == "closed"
    assert not server.snapshot().active
    client.close()


def test_client_read_timeout_is_bounded_without_closing_lease(
    tmp_path: Path,
) -> None:
    location = _location(tmp_path)
    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline)
    server = DaemonLiveAudioServer(
        DaemonSocketListener(location),
        session,
        accept_poll_interval=0.01,
    )
    server.start()
    client = DaemonLiveAudioClient(location)
    client.connect()

    with pytest.raises(queue.Empty):
        client.get(0.01)

    pipeline.publish(b"later")
    assert client.get(1.0) == b"later"
    client.close()
    server.stop()
