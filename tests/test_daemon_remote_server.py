from __future__ import annotations

import base64
import json
import queue
import socket
import threading
import time
from pathlib import Path
from typing import cast

import pytest

import sds200.daemon_remote_server as remote_server
from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DAEMON_REMOTE_CONTROL_OPERATIONS,
    DAEMON_REMOTE_LISTENER_DEFAULT_ACCEPT_POLL_INTERVAL,
    DAEMON_REMOTE_LISTENER_DEFAULT_BACKLOG,
    DAEMON_REMOTE_LISTENER_DEFAULT_MAX_PENDING_ADMISSIONS,
    DAEMON_REMOTE_LISTENER_DEFAULT_MAX_READY_CLIENTS,
    DAEMON_REMOTE_LISTENER_DEFAULT_SHUTDOWN_TIMEOUT,
    DAEMON_REMOTE_OBSERVE_OPERATIONS,
    DAEMON_REMOTE_REDACTED_RESULT_FIELDS,
    DaemonApiOperation,
    DaemonApiServer,
    DaemonReadOnlyApi,
    DaemonRemoteApiPeer,
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteAuthenticatedPeer,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteClientIdentity,
    DaemonRemoteCredentialAuthority,
    DaemonRemoteCredentialLifecycleSnapshot,
    DaemonRemoteListenerConfiguration,
    DaemonRemoteListenerError,
    DaemonRemoteListenerErrorReason,
    DaemonRemoteTcpListener,
    DaemonRemoteTlsError,
    DaemonRemoteTlsErrorReason,
    DaemonServerAcceptor,
    DaemonServerListener,
    DaemonServerPeerContext,
)


class FakeSnapshot:
    def as_dict(self) -> dict[str, object]:
        return {
            "state": "running",
            "scanner_endpoint": "udp://192.168.20.25:50536",
            "scanner_model": "SDS200",
            "scanner_firmware": "Version 1.26.01",
            "scanner_connected": True,
            "psi_interval_ms": 500,
            "psi_active": True,
            "radio_state": {"channel": "Dispatch"},
            "audio": {
                "endpoint": "rtsp://192.168.20.25/audio",
                "running": True,
            },
            "router": {"subscribers": 1},
        }


class FakeResult:
    def __init__(self, operation: str) -> None:
        self.operation = operation

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "snapshot": {
                "scanner_endpoint": "udp://192.168.20.25:50536",
                "audio": {"endpoint": "rtsp://192.168.20.25/audio"},
            },
        }


class FakeRuntime:
    def __init__(self) -> None:
        self.controls: list[tuple[str, object]] = []

    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot()

    def set_volume(self, level: int, *, timeout: float) -> FakeResult:
        self.controls.append(("volume", (level, timeout)))
        return FakeResult("volume")


class MissingAuthorizationApi:
    def __init__(self) -> None:
        self.calls = 0

    def handle_json_line(self, data: bytes | str) -> bytes:
        del data
        self.calls += 1
        raise AssertionError("Remote authorization must not use local dispatch.")


class NonBytesAuthorizationApi:
    def handle_authorized_json_line(self, data: bytes | str, **kwargs: object) -> object:
        del data, kwargs
        return object()


class ScriptedAcceptor:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.clients: queue.Queue[tuple[socket.socket, object]] = queue.Queue()

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def accept(self) -> tuple[socket.socket, object]:
        assert self.timeout is not None
        try:
            return self.clients.get(timeout=self.timeout)
        except queue.Empty as error:
            raise TimeoutError from error


class ScriptedListener:
    def __init__(self) -> None:
        self.acceptor = ScriptedAcceptor()

    def start(self) -> ScriptedAcceptor:
        return self.acceptor

    def stop(self) -> None:
        return None


class FakeStream:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def shutdown(self, how: int) -> None:
        del how
        self.closed = True

    def close(self) -> None:
        self.closed = True


class FakeListeningSocket:
    def __init__(self) -> None:
        self.family: int | None = None
        self.kind: int | None = None
        self.bound: tuple[object, ...] | None = None
        self.backlog: int | None = None
        self.timeout: float | None = None
        self.options: list[tuple[int, int, int]] = []
        self.closed = False
        self.incoming: queue.Queue[FakeStream | BaseException] = queue.Queue()

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))

    def bind(self, address: tuple[object, ...]) -> None:
        self.bound = address

    def getsockname(self) -> tuple[object, ...]:
        assert self.bound is not None
        return self.bound

    def listen(self, backlog: int) -> None:
        self.backlog = backlog

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def accept(self) -> tuple[socket.socket, object]:
        assert self.timeout is not None
        try:
            incoming = self.incoming.get(timeout=self.timeout)
        except queue.Empty as error:
            raise TimeoutError from error
        if isinstance(incoming, BaseException):
            raise incoming
        return cast(socket.socket, incoming), ("private-peer", 12345)

    def shutdown(self, how: int) -> None:
        del how
        self.closed = True

    def close(self) -> None:
        self.closed = True


class FakeAdmission:
    def __init__(
        self,
        peer: DaemonRemoteAuthenticatedPeer,
        *,
        handshake_timeout: float = 0.05,
    ) -> None:
        self.peer = peer
        self.handshake_timeout = handshake_timeout
        self.blocked: dict[str, threading.Event] = {}
        self.failures: set[str] = set()
        self.unexpected_failures: set[str] = set()
        self.calls: list[str] = []
        self.reloads: list[DaemonRemoteListenerConfiguration] = []
        self.lifecycle = DaemonRemoteCredentialLifecycleSnapshot(
            generation=1,
            configured_clients=1,
            active_clients=1,
            revoked_clients=0,
            control_clients=0,
            active_sessions=0,
            successful_reloads=0,
            failed_reloads=0,
            invalidated_sessions=0,
            invalidation_failures=0,
            last_error=None,
        )

    def admit(
        self,
        stream: socket.socket,
    ) -> tuple[socket.socket, DaemonRemoteAuthenticatedPeer]:
        fake = cast(FakeStream, stream)
        self.calls.append(fake.name)
        blocked = self.blocked.get(fake.name)
        if blocked is not None:
            blocked.wait(1.0)
        if fake.name in self.unexpected_failures:
            raise RuntimeError("private unexpected admission detail")
        if fake.name in self.failures:
            fake.close()
            raise DaemonRemoteTlsError(
                DaemonRemoteTlsErrorReason.AUTHENTICATION_FAILED
            )
        return stream, self.peer

    def credential_snapshot(self) -> DaemonRemoteCredentialLifecycleSnapshot:
        return self.lifecycle

    def reload_credentials(
        self,
        configuration: DaemonRemoteListenerConfiguration,
    ) -> DaemonRemoteCredentialLifecycleSnapshot:
        self.reloads.append(configuration)
        self.lifecycle = DaemonRemoteCredentialLifecycleSnapshot(
            generation=self.lifecycle.generation + 1,
            configured_clients=1,
            active_clients=1,
            revoked_clients=0,
            control_clients=0,
            active_sessions=0,
            successful_reloads=self.lifecycle.successful_reloads + 1,
            failed_reloads=0,
            invalidated_sessions=self.lifecycle.invalidated_sessions + 1,
            invalidation_failures=0,
            last_error=None,
        )
        return self.lifecycle


class LifecyclePeer:
    def __init__(
        self,
        *,
        current: bool,
        on_close: object | None = None,
    ) -> None:
        self.current = current
        self.on_close = on_close
        self.close_calls = 0

    def daemon_api_connection_current(self) -> bool:
        return self.current

    def close_daemon_api_peer_context(self) -> None:
        self.close_calls += 1
        if callable(self.on_close):
            self.on_close()


def _request(
    operation: DaemonApiOperation | str,
    request_id: str,
    *,
    params: dict[str, object] | None = None,
    protocol: str = DAEMON_API_PROTOCOL,
) -> bytes:
    payload: dict[str, object] = {
        "protocol": protocol,
        "version": DAEMON_API_VERSION,
        "request_id": request_id,
        "operation": str(operation),
    }
    if params is not None:
        payload["params"] = params
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode()


def _peer(*, control: bool = False) -> DaemonRemoteApiPeer:
    scopes = [DaemonRemoteAuthorizationScope.OBSERVE]
    if control:
        scopes.append(DaemonRemoteAuthorizationScope.CONTROL)
    authenticated = DaemonRemoteAuthenticatedPeer(
        DaemonRemoteAuthenticatedIdentity("pi-kiosk", tuple(scopes))
    )
    return DaemonRemoteApiPeer(authenticated)


def _configuration(*, address: str = "192.168.20.10") -> DaemonRemoteListenerConfiguration:
    return DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address=address,
        port=50443,
        certificate_file=Path("/private/server.crt"),
        private_key_file=Path("/private/server.key"),
        clients=(
            DaemonRemoteClientIdentity(
                "pi-kiosk",
                Path("/private/pi-kiosk.secret"),
            ),
        ),
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    admission: FakeAdmission,
    listening: FakeListeningSocket,
) -> None:
    monkeypatch.setattr(
        remote_server.DaemonRemoteServerTlsAdmission,
        "from_configuration",
        classmethod(
            lambda cls, configuration, *, handshake_timeout: admission
        ),
    )

    def create_socket(family: int, kind: int) -> FakeListeningSocket:
        listening.family = family
        listening.kind = kind
        return listening

    monkeypatch.setattr(remote_server.socket_module, "socket", create_socket)


def _listener(
    monkeypatch: pytest.MonkeyPatch,
    admission: FakeAdmission,
    listening: FakeListeningSocket,
    **kwargs: object,
) -> DaemonRemoteTcpListener:
    _install_fakes(monkeypatch, admission, listening)
    return DaemonRemoteTcpListener(
        _configuration(),
        accept_poll_interval=0.01,
        handshake_timeout=admission.handshake_timeout,
        shutdown_timeout=0.2,
        **kwargs,  # type: ignore[arg-type]
    )


def _wait_until(predicate: object, *, timeout: float = 1.0) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Condition did not become true before timeout.")


def _read_line(stream: socket.socket) -> dict[str, object]:
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = stream.recv(1)
        if not chunk:
            raise AssertionError("Daemon API stream closed before one response.")
        payload.extend(chunk)
    result = json.loads(payload)
    assert isinstance(result, dict)
    return result


def test_observe_peer_advertises_only_safe_read_only_operations() -> None:
    runtime = FakeRuntime()
    api = DaemonReadOnlyApi(runtime)
    peer = _peer()

    response = json.loads(
        peer.handle_daemon_api_json_line(
            api,
            _request(DaemonApiOperation.HELLO, "hello-observe"),
        )
    )
    result = response["result"]

    assert result["operations"] == [
        operation.value for operation in DAEMON_REMOTE_OBSERVE_OPERATIONS
    ]
    assert result["read_only"] is True
    assert result["control_operations"] == []
    assert DaemonApiOperation.RECORDING_STATUS not in peer.allowed_operations
    assert DaemonApiOperation.RECORDINGS_LIST not in peer.allowed_operations
    assert DaemonApiOperation.RECORDING_START not in peer.allowed_operations
    assert DaemonApiOperation.RECORDING_STOP not in peer.allowed_operations
    assert isinstance(peer, DaemonServerPeerContext)


@pytest.mark.parametrize(
    "operation",
    [DaemonApiOperation.RUNTIME_SNAPSHOT, DaemonApiOperation.SCANNER_STATE],
)
def test_remote_state_removes_private_scanner_endpoint(
    operation: DaemonApiOperation,
) -> None:
    response = json.loads(
        _peer().handle_daemon_api_json_line(
            DaemonReadOnlyApi(FakeRuntime()),
            _request(operation, "state-observe"),
        )
    )

    assert response["ok"] is True
    assert "scanner_endpoint" not in response["result"]
    assert "endpoint" not in response["result"].get("audio", {})
    assert "192.168.20.25" not in json.dumps(response)
    assert DAEMON_REMOTE_REDACTED_RESULT_FIELDS == (
        "endpoint",
        "scanner_endpoint",
    )


def test_observe_peer_denies_control_before_runtime_dispatch() -> None:
    runtime = FakeRuntime()
    response = json.loads(
        _peer().handle_daemon_api_json_line(
            DaemonReadOnlyApi(runtime),
            _request(
                DaemonApiOperation.SCANNER_VOLUME_SET,
                "volume-denied",
                params={"level": 8},
            ),
        )
    )

    assert response["ok"] is False
    assert response["request_id"] == "volume-denied"
    assert response["error"]["code"] == "authorization_denied"
    assert runtime.controls == []


def test_control_peer_dispatches_scanner_control_but_not_recording_operation() -> None:
    runtime = FakeRuntime()
    api = DaemonReadOnlyApi(runtime)
    peer = _peer(control=True)

    allowed = json.loads(
        peer.handle_daemon_api_json_line(
            api,
            _request(
                DaemonApiOperation.SCANNER_VOLUME_SET,
                "volume-allowed",
                params={"level": 8},
            ),
        )
    )
    denied = json.loads(
        peer.handle_daemon_api_json_line(
            api,
            _request(DaemonApiOperation.RECORDING_START, "recording-denied"),
        )
    )

    assert allowed["ok"] is True
    assert "scanner_endpoint" not in json.dumps(allowed)
    assert "192.168.20.25" not in json.dumps(allowed)
    assert runtime.controls == [("volume", (8, 2.0))]
    assert denied["error"]["code"] == "authorization_denied"
    assert peer.allowed_operations == (
        *DAEMON_REMOTE_OBSERVE_OPERATIONS,
        *DAEMON_REMOTE_CONTROL_OPERATIONS,
    )


def test_authorized_dispatch_preserves_protocol_and_malformed_failures() -> None:
    api = DaemonReadOnlyApi(FakeRuntime())
    peer = _peer()

    malformed = json.loads(peer.handle_daemon_api_json_line(api, b"not-json\n"))
    protocol = json.loads(
        peer.handle_daemon_api_json_line(
            api,
            _request(DaemonApiOperation.PING, "bad-protocol", protocol="wrong"),
        )
    )

    assert malformed["error"]["code"] == "invalid_request"
    assert protocol["error"]["code"] == "unsupported_protocol"


def test_remote_peer_fails_closed_when_api_lacks_authorized_entry_point() -> None:
    api = MissingAuthorizationApi()
    response = json.loads(
        _peer().handle_daemon_api_json_line(
            api,
            _request(DaemonApiOperation.PING, "must-not-dispatch"),
        )
    )

    assert response["ok"] is False
    assert response["request_id"] is None
    assert response["error"]["code"] == "internal_error"
    assert api.calls == 0


def test_remote_peer_fails_closed_when_authorized_entry_point_returns_nonbytes() -> None:
    response = json.loads(
        _peer().handle_daemon_api_json_line(
            NonBytesAuthorizationApi(),
            _request(DaemonApiOperation.PING, "must-return-bytes"),
        )
    )

    assert response["ok"] is False
    assert response["request_id"] is None
    assert response["error"]["code"] == "internal_error"


def test_remote_peer_rejects_request_after_credential_generation_changes(
    tmp_path: Path,
) -> None:
    credential = tmp_path / "client.secret"
    credential.write_text(
        base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
        + "\n",
        encoding="ascii",
    )
    credential.chmod(0o600)
    configuration = DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address="192.168.20.10",
        port=50443,
        certificate_file=tmp_path / "server.crt",
        private_key_file=tmp_path / "server.key",
        clients=(DaemonRemoteClientIdentity("pi-kiosk", credential),),
    )
    authority = DaemonRemoteCredentialAuthority(configuration)
    session = authority.register_session(
        authority.current_generation(),
        DaemonRemoteAuthenticatedIdentity(
            "pi-kiosk",
            (DaemonRemoteAuthorizationScope.OBSERVE,),
        ),
        invalidator=lambda: None,
    )
    peer = DaemonRemoteApiPeer(
        DaemonRemoteAuthenticatedPeer(
            DaemonRemoteAuthenticatedIdentity(
                "pi-kiosk",
                (DaemonRemoteAuthorizationScope.OBSERVE,),
            ),
            credential_session=session,
        )
    )

    authority.reload(configuration)
    response = json.loads(
        peer.handle_daemon_api_json_line(
            DaemonReadOnlyApi(FakeRuntime()),
            _request(DaemonApiOperation.PING, "expired"),
        )
    )

    assert response["ok"] is False
    assert response["request_id"] is None
    assert response["error"]["code"] == "authentication_expired"
    assert peer.daemon_api_connection_current() is False
    peer.close_daemon_api_peer_context()


def test_api_server_preserves_peer_context_through_worker_dispatch() -> None:
    listener = ScriptedListener()
    client, accepted = socket.socketpair()
    client.settimeout(1.0)
    runtime = FakeRuntime()
    listener.acceptor.clients.put((accepted, _peer()))
    server = DaemonApiServer(
        listener,
        DaemonReadOnlyApi(runtime),
        accept_poll_interval=0.01,
    )

    try:
        with server:
            client.sendall(
                _request(
                    DaemonApiOperation.SCANNER_VOLUME_SET,
                    "server-denied",
                    params={"level": 9},
                )
            )
            response = _read_line(client)
    finally:
        client.close()

    assert response["error"]["code"] == "authorization_denied"
    assert runtime.controls == []
    assert server.snapshot().requests == 1
    assert server.snapshot().responses == 1


@pytest.mark.parametrize(
    ("allowed", "redacted", "error_type", "message"),
    [
        ([], (), TypeError, "operations must be a tuple"),
        ((), (), ValueError, "operations must not be empty"),
        (("ping",), (), TypeError, "unsupported value"),
        (
            (DaemonApiOperation.PING, DaemonApiOperation.PING),
            (),
            ValueError,
            "operations must be unique",
        ),
        ((DaemonApiOperation.PING,), [], TypeError, "fields must be a tuple"),
        ((DaemonApiOperation.PING,), ("",), TypeError, "non-empty strings"),
        (
            (DaemonApiOperation.PING,),
            ("secret", "secret"),
            ValueError,
            "fields must be unique",
        ),
    ],
)
def test_authorized_api_entry_point_is_strict(
    allowed: object,
    redacted: object,
    error_type: type[Exception],
    message: str,
) -> None:
    api = DaemonReadOnlyApi(FakeRuntime())

    with pytest.raises(error_type, match=message):
        api.handle_authorized_json_line(
            _request(DaemonApiOperation.PING, "strict"),
            allowed_operations=allowed,  # type: ignore[arg-type]
            redacted_result_fields=redacted,  # type: ignore[arg-type]
        )


def test_listener_binds_only_configured_endpoint_and_redacts_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()
    listener = _listener(monkeypatch, admission, listening)

    assert isinstance(listener, DaemonServerListener)
    assert isinstance(listener, DaemonServerAcceptor)
    assert listener.start() is listener
    assert listener.start() is listener
    assert listener.active is True
    assert listening.family == socket.AF_INET
    assert listening.kind == socket.SOCK_STREAM
    assert listening.bound == ("192.168.20.10", 50443)
    assert listening.backlog == DAEMON_REMOTE_LISTENER_DEFAULT_BACKLOG
    assert listening.timeout == DAEMON_REMOTE_LISTENER_DEFAULT_ACCEPT_POLL_INTERVAL / 10

    snapshot = listener.snapshot()
    assert snapshot.active is True
    assert snapshot.address_family == "ipv4"
    assert snapshot.port == 50443
    assert snapshot.max_pending_admissions == (
        DAEMON_REMOTE_LISTENER_DEFAULT_MAX_PENDING_ADMISSIONS
    )
    assert snapshot.max_ready_clients == DAEMON_REMOTE_LISTENER_DEFAULT_MAX_READY_CLIENTS
    assert "192.168.20.10" not in str(snapshot.as_dict())
    assert "/private" not in str(snapshot.as_dict())

    listener.stop()
    listener.stop()
    assert listener.active is False
    assert listening.closed is True
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        listener.start()


def test_listener_delivers_only_admitted_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer(control=True).authenticated)
    listening = FakeListeningSocket()
    stream = FakeStream("authenticated")
    listener = _listener(monkeypatch, admission, listening)
    listener.start()
    listener.settimeout(0.5)
    listening.incoming.put(stream)

    delivered, context = listener.accept()
    try:
        assert delivered is stream
        assert isinstance(context, DaemonRemoteApiPeer)
        assert context.client_id == "pi-kiosk"
        assert context.scopes == (
            DaemonRemoteAuthorizationScope.OBSERVE,
            DaemonRemoteAuthorizationScope.CONTROL,
        )
        snapshot = listener.snapshot()
        assert snapshot.accepted_connections == 1
        assert snapshot.admitted_clients == 1
        assert snapshot.failed_admissions == 0
    finally:
        stream.close()
        listener.stop()


def test_listener_reload_discards_invalid_queued_peers_and_retains_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listener = _listener(monkeypatch, admission, FakeListeningSocket())
    listener._started = True
    invalid_stream = FakeStream("invalid-after-reload")
    current_stream = FakeStream("current-after-reload")
    invalid_peer = LifecyclePeer(current=False)
    current_peer = LifecyclePeer(current=True)
    listener._ready.put_nowait(
        remote_server._ReadyClient(
            cast(socket.socket, invalid_stream),
            cast(DaemonRemoteApiPeer, invalid_peer),
        )
    )
    listener._ready.put_nowait(
        remote_server._ReadyClient(
            cast(socket.socket, current_stream),
            cast(DaemonRemoteApiPeer, current_peer),
        )
    )
    listener._ready.put_nowait(remote_server._STOPPED_DELIVERY)
    listener._ready_clients = 2

    assert listener.credential_snapshot() == admission.lifecycle
    snapshot = listener.reload_credentials(_configuration())

    assert snapshot.generation == 2
    assert admission.reloads == [_configuration()]
    assert invalid_stream.closed is True
    assert invalid_peer.close_calls == 1
    assert listener.snapshot().ready_clients == 1
    delivered, peer = listener.accept()
    assert cast(object, delivered) is current_stream
    assert cast(object, peer) is current_peer
    assert current_peer.close_calls == 0
    current_stream.close()
    listener.stop()


def test_listener_accept_skips_expired_peer_within_original_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = _listener(
        monkeypatch,
        FakeAdmission(_peer().authenticated),
        FakeListeningSocket(),
    )
    listener._started = True
    listener.settimeout(0.5)
    expired_stream = FakeStream("expired")
    current_stream = FakeStream("current")
    expired_peer = LifecyclePeer(current=False)
    current_peer = LifecyclePeer(current=True)
    for stream, lifecycle_peer in (
        (expired_stream, expired_peer),
        (current_stream, current_peer),
    ):
        listener._ready.put_nowait(
            remote_server._ReadyClient(
                cast(socket.socket, stream),
                cast(DaemonRemoteApiPeer, lifecycle_peer),
            )
        )
    listener._ready_clients = 2

    delivered, delivered_peer = listener.accept()

    assert cast(object, delivered) is current_stream
    assert cast(object, delivered_peer) is current_peer
    assert expired_stream.closed is True
    assert expired_peer.close_calls == 1
    assert listener.snapshot().ready_clients == 0
    current_stream.close()
    listener.stop()


def test_listener_accept_reports_stop_after_discarding_last_expired_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = _listener(
        monkeypatch,
        FakeAdmission(_peer().authenticated),
        FakeListeningSocket(),
    )
    listener._started = True
    expired_stream = FakeStream("expired-before-stop")
    expired_peer = LifecyclePeer(
        current=False,
        on_close=lambda: setattr(listener, "_stopped", True),
    )
    listener._ready.put_nowait(
        remote_server._ReadyClient(
            cast(socket.socket, expired_stream),
            cast(DaemonRemoteApiPeer, expired_peer),
        )
    )
    listener._ready_clients = 1

    with pytest.raises(OSError, match="listener is closed"):
        listener.accept()

    assert expired_stream.closed is True
    assert expired_peer.close_calls == 1


def test_listener_accept_honors_expired_absolute_delivery_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = _listener(
        monkeypatch,
        FakeAdmission(_peer().authenticated),
        FakeListeningSocket(),
    )
    listener._started = True
    listener.settimeout(0.5)
    clock = iter((10.0, 10.6))
    monkeypatch.setattr(remote_server, "monotonic", lambda: next(clock))

    with pytest.raises(TimeoutError):
        listener.accept()


def test_reload_queue_refill_race_closes_retained_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = _listener(
        monkeypatch,
        FakeAdmission(_peer().authenticated),
        FakeListeningSocket(),
    )
    stream = FakeStream("retained-refill-race")
    peer = LifecyclePeer(current=True)
    ready = remote_server._ReadyClient(
        cast(socket.socket, stream),
        cast(DaemonRemoteApiPeer, peer),
    )

    class RefilledQueue:
        def __init__(self) -> None:
            self.delivery: object | None = ready

        def get_nowait(self) -> object:
            if self.delivery is None:
                raise queue.Empty
            delivery = self.delivery
            self.delivery = None
            return delivery

        def put_nowait(self, delivery: object) -> None:
            del delivery
            raise queue.Full

    listener._ready = RefilledQueue()  # type: ignore[assignment]
    listener._ready_clients = 1

    listener._discard_invalid_ready_clients()

    assert listener.snapshot().ready_clients == 0
    assert listener.snapshot().rejected_connections == 1
    assert stream.closed is True
    assert peer.close_calls == 1


def test_slow_admission_does_not_block_second_authenticated_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    release_slow = threading.Event()
    admission.blocked["slow"] = release_slow
    listening = FakeListeningSocket()
    slow = FakeStream("slow")
    fast = FakeStream("fast")
    listener = _listener(
        monkeypatch,
        admission,
        listening,
        max_pending_admissions=2,
    )
    listener.start()
    listener.settimeout(0.5)
    listening.incoming.put(slow)
    _wait_until(lambda: listener.snapshot().pending_admissions == 1)
    listening.incoming.put(fast)

    delivered, _ = listener.accept()
    assert delivered is fast
    assert listener.snapshot().pending_admissions == 1

    release_slow.set()
    delivered_slow, _ = listener.accept()
    assert delivered_slow is slow
    slow.close()
    fast.close()
    listener.stop()


def test_pending_admission_capacity_rejects_excess_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    release = threading.Event()
    admission.blocked["first"] = release
    listening = FakeListeningSocket()
    first = FakeStream("first")
    excess = FakeStream("excess")
    listener = _listener(
        monkeypatch,
        admission,
        listening,
        max_pending_admissions=1,
    )
    listener.start()
    listening.incoming.put(first)
    _wait_until(lambda: listener.snapshot().pending_admissions == 1)
    listening.incoming.put(excess)
    _wait_until(lambda: listener.snapshot().rejected_connections == 1)

    assert excess.closed is True
    assert admission.calls == ["first"]
    release.set()
    listener.stop()


def test_ready_capacity_and_authentication_failure_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    admission.failures.add("unauthenticated")
    listening = FakeListeningSocket()
    first = FakeStream("first")
    excess = FakeStream("excess")
    unauthenticated = FakeStream("unauthenticated")
    listener = _listener(
        monkeypatch,
        admission,
        listening,
        max_ready_clients=1,
        max_pending_admissions=3,
    )
    listener.start()
    listening.incoming.put(first)
    _wait_until(lambda: listener.snapshot().ready_clients == 1)
    listening.incoming.put(excess)
    listening.incoming.put(unauthenticated)
    _wait_until(
        lambda: (
            listener.snapshot().rejected_connections == 1
            and listener.snapshot().failed_admissions == 1
        )
    )

    snapshot = listener.snapshot()
    assert snapshot.admitted_clients == 1
    assert snapshot.ready_clients == 1
    assert snapshot.last_error == "authentication_failed"
    assert excess.closed is True
    assert unauthenticated.closed is True
    listener.stop()
    assert first.closed is True


def test_unexpected_admission_failure_is_redacted_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    admission.unexpected_failures.add("unexpected")
    listening = FakeListeningSocket()
    stream = FakeStream("unexpected")
    listener = _listener(monkeypatch, admission, listening)
    listener.start()
    listening.incoming.put(stream)
    _wait_until(lambda: listener.snapshot().failed_admissions == 1)

    snapshot = listener.snapshot()
    assert snapshot.last_error == "admission_failed"
    assert "private" not in str(snapshot.as_dict())
    assert stream.closed is True
    listener.stop()


def test_admitted_stream_is_closed_when_listener_stops_during_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    release = threading.Event()
    admission.blocked["stopping"] = release
    listening = FakeListeningSocket()
    stream = FakeStream("stopping")
    listener = _listener(monkeypatch, admission, listening)
    listener.start()
    listening.incoming.put(stream)
    _wait_until(lambda: listener.snapshot().pending_admissions == 1)

    with listener._lock:
        listener._stopped = True
        listener._active = False
    release.set()
    _wait_until(lambda: listener.snapshot().pending_admissions == 0)

    assert stream.closed is True
    assert listener.snapshot().admitted_clients == 0
    listener.stop()


def test_worker_start_failure_is_redacted_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()
    stream = FakeStream("worker-start")
    listener = _listener(monkeypatch, admission, listening)
    listener.start()

    class FailingThread:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def start(self) -> None:
            raise RuntimeError("private worker detail")

    monkeypatch.setattr(remote_server.threading, "Thread", FailingThread)
    listener._start_admission(cast(socket.socket, stream))

    snapshot = listener.snapshot()
    assert snapshot.accepted_connections == 1
    assert snapshot.failed_admissions == 1
    assert snapshot.last_error == "worker_start_failed"
    assert "private" not in str(snapshot.as_dict())
    assert stream.closed is True
    listener.stop()


def test_listener_accept_failure_is_redacted_and_unblocks_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()
    listener = _listener(monkeypatch, admission, listening)
    listener.start()
    listener.settimeout(0.05)
    listening.incoming.put(OSError("private endpoint 192.168.20.10"))
    _wait_until(lambda: listener.snapshot().last_error == "accept_failed")

    with pytest.raises(OSError, match="listener is closed") as captured:
        listener.accept()
    assert "192.168.20.10" not in str(captured.value)
    listener.stop()


def test_listener_start_failure_is_uniform_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()

    def fail_bind(address: tuple[object, ...]) -> None:
        raise OSError(f"private bind failed: {address!r}")

    listening.bind = fail_bind  # type: ignore[method-assign]
    listener = _listener(monkeypatch, admission, listening)

    with pytest.raises(DaemonRemoteListenerError) as captured:
        listener.start()

    assert captured.value.reason is DaemonRemoteListenerErrorReason.START_FAILED
    assert str(captured.value) == "Remote daemon listener could not start."
    assert "192.168.20.10" not in str(captured.value)
    assert listening.closed is True


@pytest.mark.parametrize(
    ("keyword", "value", "error_type", "message"),
    [
        ("backlog", True, TypeError, "backlog must be an integer"),
        ("backlog", 0, ValueError, "backlog must be greater than zero"),
        (
            "max_pending_admissions",
            0,
            ValueError,
            "pending remote daemon admissions",
        ),
        ("max_ready_clients", 0, ValueError, "ready remote daemon clients"),
        ("accept_poll_interval", float("inf"), ValueError, "finite"),
        ("shutdown_timeout", 0.05, ValueError, "greater than the TLS"),
    ],
)
def test_listener_rejects_invalid_limits(
    monkeypatch: pytest.MonkeyPatch,
    keyword: str,
    value: object,
    error_type: type[Exception],
    message: str,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()
    _install_fakes(monkeypatch, admission, listening)

    with pytest.raises(error_type, match=message):
        DaemonRemoteTcpListener(
            _configuration(),
            handshake_timeout=admission.handshake_timeout,
            **{keyword: value},
        )


def test_listener_and_peer_constructors_are_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()
    _install_fakes(monkeypatch, admission, listening)

    with pytest.raises(TypeError, match="ListenerConfiguration"):
        DaemonRemoteTcpListener(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="enabled configuration"):
        DaemonRemoteTcpListener(DaemonRemoteListenerConfiguration())
    with pytest.raises(TypeError, match="authenticated TLS peer"):
        DaemonRemoteApiPeer(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="error reason"):
        DaemonRemoteListenerError("start_failed")  # type: ignore[arg-type]


def test_listener_timeout_and_inactive_accept_are_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()
    listener = _listener(monkeypatch, admission, listening)

    with pytest.raises(RuntimeError, match="not active"):
        listener.accept()
    for value in (True, 0, -1, float("inf")):
        with pytest.raises((TypeError, ValueError), match="accept timeout"):
            listener.settimeout(value)  # type: ignore[arg-type]
    listener.start()
    listener.settimeout(0.01)
    with pytest.raises(TimeoutError):
        listener.accept()
    listener.stop()


def test_accept_maps_stop_sentinel_and_ready_stop_race_to_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listener = _listener(monkeypatch, admission, FakeListeningSocket())
    listener._started = True
    listener._ready.put_nowait(remote_server._STOPPED_DELIVERY)

    with pytest.raises(OSError, match="listener is closed"):
        listener.accept()

    stream = FakeStream("ready-during-stop")
    listener._ready.put_nowait(
        remote_server._ReadyClient(cast(socket.socket, stream), _peer())
    )
    listener._ready_clients = 1
    listener._stopped = True
    with pytest.raises(OSError, match="listener is closed"):
        listener.accept()
    assert stream.closed is True


def test_accept_queue_race_after_stop_is_reported_as_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listener = _listener(monkeypatch, admission, FakeListeningSocket())
    listener._started = True

    class ClosingQueue:
        def empty(self) -> bool:
            return False

        def get(self, *, timeout: float | None) -> object:
            del timeout
            listener._stopped = True
            raise queue.Empty

    listener._ready = ClosingQueue()  # type: ignore[assignment]
    with pytest.raises(OSError, match="listener is closed"):
        listener.accept()


def test_listener_context_manager_starts_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listener = _listener(monkeypatch, admission, FakeListeningSocket())

    with listener as active:
        assert active.active is True

    assert listener.active is False


def test_context_exit_preserves_body_error_but_reports_standalone_stop_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listener = _listener(monkeypatch, admission, FakeListeningSocket())

    def fail_stop() -> None:
        raise RuntimeError("stop failed")

    monkeypatch.setattr(listener, "stop", fail_stop)
    listener.__exit__(RuntimeError, RuntimeError("body failed"), None)
    with pytest.raises(RuntimeError, match="stop failed"):
        listener.__exit__(None, None, None)


def test_shutdown_reports_worker_that_does_not_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    stream = FakeStream("pending")
    listener = _listener(monkeypatch, admission, FakeListeningSocket())

    class StuckThread:
        def join(self, timeout: float | None = None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return True

    listener._pending[cast(socket.socket, stream)] = cast(
        threading.Thread,
        StuckThread(),
    )
    with pytest.raises(DaemonRemoteListenerError) as captured:
        listener.stop()

    assert captured.value.reason is DaemonRemoteListenerErrorReason.SHUTDOWN_FAILED
    assert stream.closed is True


def test_shutdown_stops_joining_after_one_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    stream = FakeStream("pending-deadline")
    listener = _listener(monkeypatch, admission, FakeListeningSocket())

    class FinishedThread:
        def __init__(self) -> None:
            self.joins: list[float | None] = []

        def join(self, timeout: float | None = None) -> None:
            self.joins.append(timeout)

        def is_alive(self) -> bool:
            return False

    first = FinishedThread()
    second = FinishedThread()
    listener._accept_thread = cast(threading.Thread, first)
    listener._pending[cast(socket.socket, stream)] = cast(threading.Thread, second)
    clock = iter((10.0, 10.1, 10.3))
    monkeypatch.setattr(remote_server, "monotonic", lambda: next(clock))

    listener.stop()

    assert len(first.joins) == 1
    assert second.joins == []
    assert stream.closed is True


def test_accept_loop_treats_socket_error_during_stop_as_normal_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listener = _listener(monkeypatch, admission, FakeListeningSocket())

    class ClosingListeningSocket:
        def accept(self) -> tuple[socket.socket, object]:
            listener._stopped = True
            raise OSError("closed during stop")

    listener._active = True
    listener._accept_loop(cast(socket.socket, ClosingListeningSocket()))

    assert listener.snapshot().active is False
    assert listener.snapshot().last_error is None


def test_ready_queue_drain_ignores_stop_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listener = _listener(monkeypatch, admission, FakeListeningSocket())
    listener._ready.put_nowait(remote_server._STOPPED_DELIVERY)

    assert listener._drain_ready_clients() == ()


def test_ipv6_endpoint_is_v6_only_and_scope_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = FakeAdmission(_peer().authenticated)
    listening = FakeListeningSocket()
    monkeypatch.setattr(remote_server.socket_module, "if_nametoindex", lambda name: 7)
    _install_fakes(monkeypatch, admission, listening)
    listener = DaemonRemoteTcpListener(
        _configuration(address="fe80::20%eth0"),
        accept_poll_interval=0.01,
        handshake_timeout=0.05,
        shutdown_timeout=0.2,
    )

    listener.start()
    try:
        assert listening.family == socket.AF_INET6
        assert listening.bound == ("fe80::20", 50443, 0, 7)
        assert listening.options == [
            (socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ]
        assert listener.snapshot().address_family == "ipv6"
    finally:
        listener.stop()


@pytest.mark.parametrize(
    ("observed", "message"),
    [
        ("not-a-tuple", "invalid endpoint"),
        (("192.168.20.11", 50443), "unexpected address"),
        (("192.168.20.10", 50444), "unexpected port"),
        (("fe80::20", 50443, 0), "unexpected scope"),
        (("fe80::20", 50443, 0, 8), "unexpected scope"),
    ],
)
def test_bound_endpoint_verification_is_exact(
    observed: object,
    message: str,
) -> None:
    class ObservedSocket:
        def getsockname(self) -> object:
            return observed

    endpoint = (
        (socket.AF_INET6, ("fe80::20", 50443, 0, 7))
        if isinstance(observed, tuple) and str(observed[0]).startswith("fe80")
        else (socket.AF_INET, ("192.168.20.10", 50443))
    )
    with pytest.raises(OSError, match=message):
        remote_server._verify_bound_endpoint(
            cast(socket.socket, ObservedSocket()),
            endpoint,
        )


def test_listener_constants_are_explicit() -> None:
    assert DAEMON_REMOTE_LISTENER_DEFAULT_BACKLOG == 16
    assert DAEMON_REMOTE_LISTENER_DEFAULT_MAX_PENDING_ADMISSIONS == 8
    assert DAEMON_REMOTE_LISTENER_DEFAULT_MAX_READY_CLIENTS == 8
    assert DAEMON_REMOTE_LISTENER_DEFAULT_ACCEPT_POLL_INTERVAL == 0.1
    assert DAEMON_REMOTE_LISTENER_DEFAULT_SHUTDOWN_TIMEOUT == 6.0
