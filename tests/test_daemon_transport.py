from __future__ import annotations

import errno
import json
import queue
import socket
import threading
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DaemonApiClient,
    DaemonApiOperation,
    DaemonApiServer,
    DaemonClientTransport,
    DaemonServerAcceptor,
    DaemonServerListener,
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
    DaemonUnavailableError,
    UnixDaemonClientTransport,
)


class ScriptedTransport:
    def __init__(self) -> None:
        self.timeouts: list[float] = []
        self.requests: list[dict[str, object]] = []
        self.thread: threading.Thread | None = None

    def connect(self, *, timeout: float) -> socket.socket:
        self.timeouts.append(timeout)
        client, server = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

        def serve() -> None:
            with server:
                request_bytes = bytearray()
                while not request_bytes.endswith(b"\n"):
                    chunk = server.recv(4096)
                    if not chunk:
                        return
                    request_bytes.extend(chunk)
                request = json.loads(request_bytes)
                assert isinstance(request, dict)
                self.requests.append(request)
                response = {
                    "protocol": DAEMON_API_PROTOCOL,
                    "version": DAEMON_API_VERSION,
                    "request_id": request["request_id"],
                    "ok": True,
                    "result": {"pong": True},
                }
                server.sendall(
                    (json.dumps(response, separators=(",", ":")) + "\n").encode()
                )

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        return client


class FailingTransport:
    def connect(self, *, timeout: float) -> socket.socket:
        del timeout
        raise OSError("private-endpoint-token=must-not-escape")


class FailingUnixSocket:
    def __init__(self, error: OSError) -> None:
        self.error = error
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, path: str) -> None:
        del path
        raise self.error

    def close(self) -> None:
        self.closed = True


class ScriptedServerAcceptor:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.clients: queue.Queue[socket.socket] = queue.Queue()

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def accept(self) -> tuple[socket.socket, object]:
        if self.timeout is None:
            raise AssertionError("Server accept timeout was not configured.")
        try:
            client = self.clients.get(timeout=self.timeout)
        except queue.Empty as error:
            raise TimeoutError from error
        return client, None


class ScriptedServerListener:
    def __init__(self) -> None:
        self.acceptor = ScriptedServerAcceptor()
        self.starts = 0
        self.stops = 0

    def start(self) -> ScriptedServerAcceptor:
        self.starts += 1
        return self.acceptor

    def stop(self) -> None:
        self.stops += 1


class SetTimeoutFailingAcceptor:
    def settimeout(self, value: float | None) -> None:
        del value
        raise OSError("private-listener-detail=must-not-enter-snapshot")

    def accept(self) -> tuple[socket.socket, object]:
        raise AssertionError("A failed acceptor must never accept clients.")


class SetTimeoutFailingListener:
    def __init__(self) -> None:
        self.acceptor = SetTimeoutFailingAcceptor()
        self.stops = 0

    def start(self) -> SetTimeoutFailingAcceptor:
        return self.acceptor

    def stop(self) -> None:
        self.stops += 1


class PingApi:
    maximum_request_seconds = 0.0

    def handle_json_line(self, data: bytes | str) -> bytes:
        request = json.loads(data)
        response = {
            "protocol": DAEMON_API_PROTOCOL,
            "version": DAEMON_API_VERSION,
            "request_id": request["request_id"],
            "ok": True,
            "result": {"pong": True},
        }
        return (json.dumps(response, separators=(",", ":")) + "\n").encode()


def _location(tmp_path: Path) -> DaemonSocketLocation:
    return DaemonSocketLocation(
        tmp_path / "daemon.sock",
        DaemonSocketSource.EXPLICIT,
    )


def test_unix_transport_satisfies_transport_contract(tmp_path: Path) -> None:
    transport = UnixDaemonClientTransport(_location(tmp_path))

    assert isinstance(transport, DaemonClientTransport)
    assert transport.service_label == "Daemon"


def test_unix_listener_satisfies_server_listener_contract(tmp_path: Path) -> None:
    listener = DaemonSocketListener(_location(tmp_path))

    assert isinstance(listener, DaemonServerListener)


@pytest.mark.parametrize(
    "label",
    ["", "   ", "Daemon\nforged", "x" * 65],
)
def test_unix_transport_rejects_unsafe_service_label(
    tmp_path: Path,
    label: str,
) -> None:
    with pytest.raises(ValueError, match="service label"):
        UnixDaemonClientTransport(_location(tmp_path), label)


def test_unix_transport_rejects_non_string_service_label(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="service label"):
        UnixDaemonClientTransport(_location(tmp_path), object())  # type: ignore[arg-type]


def test_unix_transport_rejects_non_location() -> None:
    with pytest.raises(TypeError, match="location"):
        UnixDaemonClientTransport(object())  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf")])
def test_unix_transport_rejects_invalid_timeout(
    tmp_path: Path,
    timeout: object,
) -> None:
    transport = UnixDaemonClientTransport(_location(tmp_path))

    with pytest.raises((TypeError, ValueError), match="transport timeout"):
        transport.connect(timeout=timeout)  # type: ignore[arg-type]


def test_unix_transport_opens_private_stream_with_timeout(tmp_path: Path) -> None:
    location = _location(tmp_path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(location.path))
    listener.listen(1)

    transport = UnixDaemonClientTransport(location)
    client = transport.connect(timeout=0.75)
    accepted, _ = listener.accept()
    try:
        assert client.family == socket.AF_UNIX
        assert client.type == socket.SOCK_STREAM
        assert client.gettimeout() == pytest.approx(0.75)
    finally:
        client.close()
        accepted.close()
        listener.close()


def test_unix_transport_preserves_missing_socket_error(tmp_path: Path) -> None:
    location = _location(tmp_path)
    transport = UnixDaemonClientTransport(location)

    with pytest.raises(
        DaemonUnavailableError,
        match=f"Daemon socket was not found: {location.path}",
    ):
        transport.connect(timeout=1.0)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ConnectionRefusedError(errno.ECONNREFUSED, "private detail"),
            "Waterfall socket is present but not accepting connections",
        ),
        (
            PermissionError(errno.EACCES, "private detail"),
            "Permission denied while connecting to waterfall socket",
        ),
        (
            PermissionError(errno.EPERM, "private detail"),
            "Permission denied while connecting to waterfall socket",
        ),
        (
            TimeoutError("private detail"),
            "Timed out connecting to waterfall socket",
        ),
        (
            OSError(errno.EIO, "bounded detail"),
            "Could not connect to waterfall socket",
        ),
    ],
)
def test_unix_transport_maps_connect_failure_and_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: OSError,
    expected: str,
) -> None:
    failing_socket = FailingUnixSocket(error)
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *args: failing_socket,
    )
    transport = UnixDaemonClientTransport(
        _location(tmp_path),
        service_label=" Waterfall ",
    )

    with pytest.raises(DaemonUnavailableError, match=expected):
        transport.connect(timeout=0.75)

    assert transport.service_label == "Waterfall"
    assert failing_socket.timeout == 0.75
    assert failing_socket.closed is True


def test_api_client_uses_transport_without_assuming_unix_location() -> None:
    transport = ScriptedTransport()
    client = DaemonApiClient(transport, timeout=0.75)

    with client:
        assert client.request(DaemonApiOperation.PING) == {"pong": True}
        assert client.location is None
        assert client.transport is transport

    assert transport.timeouts == [0.75]
    assert len(transport.requests) == 1
    assert transport.requests[0]["operation"] == DaemonApiOperation.PING
    assert transport.thread is not None
    transport.thread.join(timeout=1.0)
    assert transport.thread.is_alive() is False


def test_api_client_preserves_location_for_explicit_unix_transport(
    tmp_path: Path,
) -> None:
    location = _location(tmp_path)
    client = DaemonApiClient(UnixDaemonClientTransport(location))

    assert client.location is location


def test_api_client_rejects_non_transport_endpoint() -> None:
    with pytest.raises(TypeError, match="DaemonClientTransport"):
        DaemonApiClient(object())  # type: ignore[arg-type]


def test_api_client_redacts_unclassified_transport_os_error() -> None:
    client = DaemonApiClient(FailingTransport())

    with pytest.raises(
        DaemonUnavailableError,
        match="Could not establish daemon client transport",
    ) as captured:
        client.connect()

    assert "must-not-escape" not in str(captured.value)


def test_api_server_uses_listener_without_assuming_unix_location() -> None:
    listener = ScriptedServerListener()
    client, accepted = socket.socketpair()
    client.settimeout(1.0)
    listener.acceptor.clients.put(accepted)
    server = DaemonApiServer(
        listener,
        PingApi(),
        accept_poll_interval=0.01,
        shutdown_timeout=0.5,
    )

    try:
        with server:
            client.sendall(
                json.dumps(
                    {
                        "protocol": DAEMON_API_PROTOCOL,
                        "version": DAEMON_API_VERSION,
                        "request_id": "transport-server-1",
                        "operation": "ping",
                    }
                ).encode()
                + b"\n"
            )
            response_bytes = bytearray()
            while not response_bytes.endswith(b"\n"):
                response_bytes.extend(client.recv(4096))
            response = json.loads(response_bytes)

            assert response["request_id"] == "transport-server-1"
            assert response["result"] == {"pong": True}
            assert isinstance(listener, DaemonServerListener)
            assert isinstance(listener.acceptor, DaemonServerAcceptor)
    finally:
        client.close()

    snapshot = server.snapshot()
    assert snapshot.accepted_clients == 1
    assert snapshot.requests == 1
    assert snapshot.responses == 1
    assert listener.acceptor.timeout == 0.01
    assert listener.starts == 1
    assert listener.stops == 1


def test_api_server_rejects_non_listener() -> None:
    with pytest.raises(TypeError, match="DaemonServerListener"):
        DaemonApiServer(object(), PingApi())  # type: ignore[arg-type]


def test_api_server_listener_startup_cleanup_is_transport_neutral() -> None:
    listener = SetTimeoutFailingListener()
    server = DaemonApiServer(listener, PingApi())

    with pytest.raises(OSError, match="private-listener-detail"):
        server.start()

    assert listener.stops == 1
    assert server.active is False
    assert server.snapshot().last_error == "OSError"
    assert "private-listener-detail" not in str(server.snapshot().as_dict())
