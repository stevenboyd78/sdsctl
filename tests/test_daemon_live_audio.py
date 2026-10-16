from __future__ import annotations

import queue
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import sds200.daemon_live_audio_server as live_audio_server
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


def test_accept_loop_retries_timeout_but_stops_after_socket_failure(
    tmp_path: Path,
) -> None:
    class ScriptedAcceptor:
        def __init__(self) -> None:
            self.calls = 0

        def accept(self) -> tuple[socket.socket, object]:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError
            if self.calls == 2:
                raise OSError("private listener failure detail")
            raise AssertionError("terminal listener failure was retried")

    pipeline = FakePipeline()
    server = DaemonLiveAudioServer(
        DaemonSocketListener(_location(tmp_path)),
        LiveAudioSession(pipeline),
    )
    acceptor = ScriptedAcceptor()
    with server._state_lock:
        server._active = True

    server._accept_loop(cast(socket.socket, acceptor))

    snapshot = server.snapshot()
    assert acceptor.calls == 2
    assert not snapshot.active
    assert snapshot.connected_clients == 0
    assert snapshot.accepted_clients == 0
    assert snapshot.rejected_clients == 0
    assert snapshot.last_error == "OSError"
    assert pipeline.starts == 0


def test_shutdown_refuses_client_that_has_not_reached_admission(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    service_socket, peer_socket = socket.socketpair()

    class GatedClient:
        def settimeout(self, timeout: float) -> None:
            entered.set()
            if not release.wait(1.0):
                raise TimeoutError("admission gate was not released")
            service_socket.settimeout(timeout)

        def sendall(self, data: bytes) -> None:
            service_socket.sendall(data)

        def shutdown(self, how: int) -> None:
            service_socket.shutdown(how)

        def close(self) -> None:
            service_socket.close()

    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline)
    server = DaemonLiveAudioServer(
        DaemonSocketListener(_location(tmp_path)),
        session,
        accept_poll_interval=0.01,
    )
    admission_errors: list[BaseException] = []

    def admit() -> None:
        try:
            server._accept_client(cast(socket.socket, GatedClient()))
        except BaseException as error:
            admission_errors.append(error)

    admission = threading.Thread(target=admit)
    try:
        try:
            admission.start()
            assert entered.wait(1.0)
            server.stop()
        finally:
            release.set()
            if admission.ident is not None:
                admission.join(1.0)

        peer_socket.settimeout(1.0)
        assert peer_socket.recv(1) == b""
        assert not admission.is_alive()
        assert admission_errors == []
        snapshot = server.snapshot()
        assert not snapshot.active
        assert snapshot.connected_clients == 0
        assert snapshot.accepted_clients == 0
        assert snapshot.rejected_clients == 0
        assert snapshot.chunks_sent == 0
        assert snapshot.bytes_sent == 0
        assert snapshot.last_error is None
        assert session.snapshot().state.value == "closed"
        assert pipeline.starts == 0
    finally:
        release.set()
        if admission.ident is not None:
            admission.join(1.0)
        peer_socket.close()
        service_socket.close()


def test_shutdown_snapshots_worker_admitted_before_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_entered = threading.Event()
    release_constructor = threading.Event()
    worker_release = threading.Event()
    join_entered = threading.Event()
    stop_done = threading.Event()
    real_thread = threading.Thread
    service_socket, peer_socket = socket.socketpair()

    class ObservedRLock:
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self.contended = threading.Event()

        def __enter__(self) -> ObservedRLock:
            if not self._lock.acquire(blocking=False):
                self.contended.set()
                self._lock.acquire()
            return self

        def __exit__(self, *args: object) -> None:
            del args
            self._lock.release()

    workers: list[GatedWorker] = []

    class GatedWorker:
        def __init__(
            self,
            *,
            target: Callable[..., object],
            args: tuple[object, ...],
            name: str,
            daemon: bool,
        ) -> None:
            constructor_entered.set()
            if not release_constructor.wait(1.0):
                raise TimeoutError("worker constructor gate was not released")
            self._target = target
            self._args = args
            self._thread = real_thread(
                target=self._run,
                name=name,
                daemon=daemon,
            )
            workers.append(self)

        @property
        def name(self) -> str:
            return self._thread.name

        def _run(self) -> None:
            worker_release.wait()
            self._target(*self._args)

        def start(self) -> None:
            self._thread.start()

        def join(self, timeout: float | None = None) -> None:
            join_entered.set()
            worker_release.set()
            self._thread.join(timeout)

        def is_alive(self) -> bool:
            return self._thread.is_alive()

    pipeline = FakePipeline()
    server = DaemonLiveAudioServer(
        DaemonSocketListener(_location(tmp_path)),
        LiveAudioSession(pipeline),
        accept_poll_interval=0.01,
    )
    observed_lock = ObservedRLock()
    server._state_lock = cast(threading.RLock, observed_lock)
    admission_errors: list[BaseException] = []
    stop_errors: list[BaseException] = []

    def admit() -> None:
        try:
            server._accept_client(service_socket)
        except BaseException as error:
            admission_errors.append(error)

    def stop_server() -> None:
        try:
            server.stop()
        except BaseException as error:
            stop_errors.append(error)
        finally:
            stop_done.set()

    admission = real_thread(target=admit)
    stopper = real_thread(target=stop_server)
    monkeypatch.setattr(live_audio_server.threading, "Thread", GatedWorker)
    try:
        admission.start()
        assert constructor_entered.wait(1.0)
        stopper.start()
        assert server._stop_event.wait(1.0)
        _wait_for(lambda: observed_lock.contended.is_set() or stop_done.is_set())
        assert observed_lock.contended.is_set()
        assert not stop_done.is_set()

        release_constructor.set()
        assert join_entered.wait(1.0)
        assert stop_done.wait(1.0)
        admission.join(1.0)
        stopper.join(1.0)

        snapshot = server.snapshot()
        assert admission_errors == []
        assert stop_errors == []
        assert not admission.is_alive()
        assert not stopper.is_alive()
        assert workers and not workers[0].is_alive()
        assert snapshot.connected_clients == 0
        assert snapshot.accepted_clients == 1
        assert snapshot.rejected_clients == 0
        assert snapshot.last_error is None
        assert pipeline.starts == 1
        assert pipeline.stops == 1
    finally:
        release_constructor.set()
        worker_release.set()
        if admission.ident is not None:
            admission.join(1.0)
        if stopper.ident is not None:
            stopper.join(1.0)
        for worker in workers:
            worker.join(1.0)
        peer_socket.close()
        service_socket.close()


def test_worker_start_failure_rolls_back_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_socket, peer_socket = socket.socketpair()

    class FailingWorker:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def start(self) -> None:
            raise RuntimeError("private worker start failure detail")

    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline)
    server = DaemonLiveAudioServer(
        DaemonSocketListener(_location(tmp_path)),
        session,
    )
    monkeypatch.setattr(live_audio_server.threading, "Thread", FailingWorker)
    try:
        server._accept_client(service_socket)

        peer_socket.settimeout(1.0)
        assert peer_socket.recv(1) == b""
        snapshot = server.snapshot()
        assert snapshot.connected_clients == 0
        assert snapshot.accepted_clients == 0
        assert snapshot.rejected_clients == 0
        assert snapshot.chunks_sent == 0
        assert snapshot.bytes_sent == 0
        assert snapshot.last_error == "RuntimeError"
        assert session.snapshot().consumer_count == 0
        assert pipeline.starts == 0
    finally:
        server.stop()
        peer_socket.close()
        service_socket.close()
