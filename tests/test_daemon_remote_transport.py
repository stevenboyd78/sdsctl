from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue

import pytest

from sds200 import (
    DAEMON_API_PROTOCOL,
    DAEMON_API_VERSION,
    DAEMON_REMOTE_AUDIO_ENDPOINT,
    DAEMON_REMOTE_CLIENT_ENDPOINT,
    DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES,
    DAEMON_REMOTE_SERVICE_PROTOCOL,
    DAEMON_REMOTE_SERVICE_VERSION,
    DaemonApiClient,
    DaemonApiOperation,
    DaemonApiServer,
    DaemonEvent,
    DaemonEventClient,
    DaemonEventKind,
    DaemonEventPublisher,
    DaemonPcmuClient,
    DaemonProtocolError,
    DaemonRemoteApiPeer,
    DaemonRemoteAuthenticatedIdentity,
    DaemonRemoteAuthenticatedPeer,
    DaemonRemoteAuthorizationScope,
    DaemonRemoteClientConfiguration,
    DaemonRemoteClientError,
    DaemonRemoteClientErrorReason,
    DaemonRemoteClientIdentity,
    DaemonRemoteClientTransport,
    DaemonRemoteListenerConfiguration,
    DaemonRemoteObservationBroker,
    DaemonRemoteServerTlsAdmission,
    DaemonRemoteService,
    DaemonRemoteServiceError,
    DaemonRemoteServiceErrorReason,
    DaemonRemoteServiceRequest,
    DaemonRemoteServiceResult,
    DaemonRemoteServiceRouter,
    DaemonRemoteTlsError,
    DaemonServerAcceptor,
    DaemonServerListener,
    DaemonTuiRadio,
    DaemonWaterfallClient,
    DaemonWaterfallRecordKind,
    PcmuPacket,
    PcmuPublisher,
    WaterfallPublisher,
    WaterfallSession,
    daemon_tui_bootstrap,
)
from sds200.models import DisplayLine, GstResponse, GwfResponse, Packet, PwfResponse
from sds200.parser import PacketParser
from sds200.waterfall_subscriptions import WaterfallSubscription

CLIENT_ID = "pi-display"
CREDENTIAL_KEY = bytes(range(32))


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _write_credential(path: Path, key: bytes = CREDENTIAL_KEY) -> None:
    path.write_text(_encoded(key) + "\n", encoding="ascii")
    path.chmod(0o600)


def _generate_tls_identity(tmp_path: Path) -> tuple[Path, Path]:
    executable = shutil.which("openssl")
    if executable is None:
        pytest.skip("OpenSSL is required for remote transport integration coverage.")
    certificate = tmp_path / "private-server.crt"
    private_key = tmp_path / "private-server.key"
    subprocess.run(
        [
            executable,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-keyout",
            os.fspath(private_key),
            "-out",
            os.fspath(certificate),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-days",
            "1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    private_key.chmod(0o600)
    return certificate, private_key


def _tls_configurations(
    tmp_path: Path,
) -> tuple[
    DaemonRemoteListenerConfiguration,
    DaemonRemoteClientConfiguration,
]:
    certificate, private_key = _generate_tls_identity(tmp_path)
    credential = tmp_path / "private-client.secret"
    _write_credential(credential)
    server = DaemonRemoteListenerConfiguration(
        enabled=True,
        bind_address="192.168.20.10",
        port=50443,
        certificate_file=certificate,
        private_key_file=private_key,
        clients=(DaemonRemoteClientIdentity(CLIENT_ID, credential),),
    )
    client = DaemonRemoteClientConfiguration(
        address="192.168.20.10",
        port=50443,
        server_hostname="localhost",
        certificate_file=certificate,
        client_id=CLIENT_ID,
        credential_file=credential,
    )
    return server, client


def _receive_line(stream: socket.socket) -> bytes:
    frame = bytearray()
    while not frame.endswith(b"\n"):
        chunk = stream.recv(1)
        if not chunk:
            raise AssertionError("Stream closed before the expected frame.")
        frame.extend(chunk)
    return bytes(frame)


@pytest.mark.parametrize("tcp", (False, True))
@pytest.mark.parametrize("service", tuple(DaemonRemoteService))
def test_remote_client_validates_tls_authentication_and_selects_exact_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: DaemonRemoteService,
    tcp: bool,
) -> None:
    server_configuration, client_configuration = _tls_configurations(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(
        server_configuration
    )
    if tcp:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client_raw = socket.create_connection(listener.getsockname(), timeout=2.0)
            server_raw, _ = listener.accept()
    else:
        client_raw, server_raw = socket.socketpair()
    outcome: Queue[object] = Queue()

    def serve() -> None:
        try:
            secured, peer = admission.admit(server_raw)
            request = DaemonRemoteServiceRequest.from_json_line(
                _receive_line(secured)
            )
            secured.sendall(DaemonRemoteServiceResult.success(request.service).to_json_line())
            outcome.put((secured, peer, request))
        except BaseException as error:
            outcome.put(error)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def connect(address: object, timeout: float) -> socket.socket:
        assert address == ("192.168.20.10", 50443)
        assert timeout > 0
        return client_raw

    monkeypatch.setattr(socket, "create_connection", connect)
    transport = DaemonRemoteClientTransport(client_configuration, service)
    connected = transport.connect(timeout=2.0)
    try:
        assert connected.version() == "TLSv1.3"  # type: ignore[attr-defined]
        assert transport.sanitizes_private_state is True
        assert transport.for_service(DaemonRemoteService.API).service is (
            DaemonRemoteService.API
        )
        assert "192.168.20.10" not in repr(transport)
        assert CLIENT_ID not in repr(transport)
        assert "private-client.secret" not in repr(client_configuration)
        if tcp:
            assert connected.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
    finally:
        connected.close()

    thread.join(2.0)
    assert thread.is_alive() is False
    accepted = outcome.get_nowait()
    assert isinstance(accepted, tuple)
    secured, peer, request = accepted
    assert request.service is service
    if tcp:
        assert secured.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 1
    peer.close()
    secured.close()


def test_remote_client_authentication_failure_is_typed_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_configuration, client_configuration = _tls_configurations(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(
        server_configuration
    )
    _write_credential(client_configuration.credential_file, b"x" * 32)
    client_raw, server_raw = socket.socketpair()

    thread = threading.Thread(
        target=lambda: _admission_failure(admission, server_raw),
        daemon=True,
    )
    thread.start()
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, timeout: client_raw,
    )

    with pytest.raises(DaemonRemoteClientError) as captured:
        DaemonRemoteClientTransport(
            client_configuration,
            DaemonRemoteService.API,
        ).connect(timeout=2.0)

    assert captured.value.reason is DaemonRemoteClientErrorReason.AUTHENTICATION_FAILED
    assert CLIENT_ID not in str(captured.value)
    assert _encoded(CREDENTIAL_KEY) not in str(captured.value)
    assert "192.168.20.10" not in str(captured.value)
    thread.join(2.0)


def _admission_failure(
    admission: DaemonRemoteServerTlsAdmission,
    server: socket.socket,
) -> None:
    with pytest.raises(DaemonRemoteTlsError):
        admission.admit(server)


def test_remote_client_rejects_mismatched_service_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_configuration, client_configuration = _tls_configurations(tmp_path)
    admission = DaemonRemoteServerTlsAdmission.from_configuration(
        server_configuration
    )
    client_raw, server_raw = socket.socketpair()

    def serve() -> None:
        secured, peer = admission.admit(server_raw)
        _receive_line(secured)
        secured.sendall(
            DaemonRemoteServiceResult.success(DaemonRemoteService.EVENTS).to_json_line()
        )
        peer.close()
        secured.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, timeout: client_raw,
    )

    with pytest.raises(DaemonRemoteClientError) as captured:
        DaemonRemoteClientTransport(
            client_configuration,
            DaemonRemoteService.API,
        ).connect(timeout=2.0)
    assert captured.value.reason is (
        DaemonRemoteClientErrorReason.SERVICE_NEGOTIATION_FAILED
    )
    thread.join(2.0)


def test_remote_client_configuration_rejects_unsafe_endpoints_and_paths(
    tmp_path: Path,
) -> None:
    certificate = tmp_path / "server.crt"
    credential = tmp_path / "client.secret"
    for address in ("0.0.0.0", "127.0.0.1", "203.0.113.2", "scanner.example"):
        with pytest.raises(ValueError, match="literal private address"):
            DaemonRemoteClientConfiguration(
                address,
                50443,
                "localhost",
                certificate,
                CLIENT_ID,
                credential,
            )
    with pytest.raises(ValueError, match="must be absolute"):
        DaemonRemoteClientConfiguration(
            "192.168.20.10",
            50443,
            "localhost",
            Path("server.crt"),
            CLIENT_ID,
            credential,
        )
    with pytest.raises(ValueError, match="hostname"):
        DaemonRemoteClientConfiguration(
            "192.168.20.10",
            50443,
            "https://localhost",
            certificate,
            CLIENT_ID,
            credential,
        )


def test_service_selection_protocol_is_canonical_strict_and_versioned() -> None:
    request = DaemonRemoteServiceRequest(DaemonRemoteService.WATERFALL)
    result = DaemonRemoteServiceResult.success(DaemonRemoteService.WATERFALL)
    failure = DaemonRemoteServiceResult.failure(
        DaemonRemoteServiceErrorReason.CAPACITY_EXCEEDED
    )

    assert json.loads(request.to_json_line()) == {
        "protocol": DAEMON_REMOTE_SERVICE_PROTOCOL,
        "service": "waterfall",
        "version": DAEMON_REMOTE_SERVICE_VERSION,
    }
    assert DaemonRemoteServiceRequest.from_json_line(request.to_json_line()) == request
    assert DaemonRemoteServiceResult.from_json_line(result.to_json_line()) == result
    assert DaemonRemoteServiceResult.from_json_line(failure.to_json_line()) == failure
    assert len(request.to_json_line()) <= DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES

    with pytest.raises(DaemonRemoteServiceError) as extra:
        DaemonRemoteServiceRequest.from_json_line(
            b'{"protocol":"sdsctl.daemon.service","service":"api",'
            b'"version":1,"private":"detail"}'
        )
    assert extra.value.reason is DaemonRemoteServiceErrorReason.INVALID_FRAME
    with pytest.raises(DaemonRemoteServiceError) as unsupported:
        DaemonRemoteServiceRequest.from_json_line(
            b'{"protocol":"sdsctl.daemon.service","service":"recordings",'
            b'"version":1}'
        )
    assert unsupported.value.reason is (
        DaemonRemoteServiceErrorReason.UNSUPPORTED_SERVICE
    )
    for malformed in (
        request.to_json_line() + b"\n",
        b'{"protocol":"sdsctl.daemon.service","service":1,"version":1}',
    ):
        with pytest.raises(DaemonRemoteServiceError) as captured:
            DaemonRemoteServiceRequest.from_json_line(malformed)
        assert captured.value.reason is DaemonRemoteServiceErrorReason.INVALID_FRAME


class ScriptedAcceptor:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.clients: queue.Queue[tuple[socket.socket, object]] = queue.Queue()

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def accept(self) -> tuple[socket.socket, object]:
        try:
            return self.clients.get(timeout=self.timeout)
        except queue.Empty as error:
            raise TimeoutError from error


class ScriptedListener:
    def __init__(self) -> None:
        self.acceptor = ScriptedAcceptor()
        self.starts = 0
        self.stops = 0

    def start(self) -> ScriptedAcceptor:
        self.starts += 1
        return self.acceptor

    def stop(self) -> None:
        self.stops += 1


class ConnectedTransport:
    sanitizes_private_state = True

    def __init__(self, client: socket.socket) -> None:
        self.client = client
        self.connects = 0

    def connect(self, *, timeout: float) -> socket.socket:
        assert timeout > 0
        self.connects += 1
        return self.client


def _peer(client_id: str = CLIENT_ID) -> DaemonRemoteApiPeer:
    return DaemonRemoteApiPeer(
        DaemonRemoteAuthenticatedPeer(
            DaemonRemoteAuthenticatedIdentity(
                client_id,
                (DaemonRemoteAuthorizationScope.OBSERVE,),
            )
        )
    )


def _private_runtime_snapshot() -> dict[str, object]:
    return {
        "state": "running",
        "scanner_endpoint": "udp://192.168.20.25:50536",
        "scanner_connected": True,
        "psi_interval_ms": 500,
        "psi_active": True,
        "radio_state": {},
        "audio": {"endpoint": "rtsp://192.168.20.25/audio"},
        "recording": {"path": "/private/recording.wav"},
        "router": {},
        "started_at": "2026-09-02T10:00:00+00:00",
        "stopped_at": None,
        "state_changed_at": "2026-09-02T10:00:00+00:00",
        "transition_sequence": 1,
        "last_failure_at": None,
        "last_error": None,
    }


def _event_source() -> DaemonEventPublisher:
    return DaemonEventPublisher(_private_runtime_snapshot)


def _sanitized_runtime_snapshot() -> dict[str, object]:
    snapshot = _private_runtime_snapshot()
    snapshot.pop("scanner_endpoint")
    audio = dict(snapshot["audio"])  # type: ignore[arg-type]
    audio.pop("endpoint")
    snapshot["audio"] = audio
    return snapshot


def _serve_one_api_result(
    server: socket.socket,
    result: dict[str, object],
) -> None:
    with server:
        request = json.loads(_receive_line(server))
        server.sendall(
            json.dumps(
                {
                    "protocol": DAEMON_API_PROTOCOL,
                    "version": DAEMON_API_VERSION,
                    "request_id": request["request_id"],
                    "ok": True,
                    "result": result,
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )


def test_remote_api_and_tui_accept_only_the_documented_sanitized_snapshot() -> None:
    client_socket, server_socket = socket.socketpair()
    thread = threading.Thread(
        target=_serve_one_api_result,
        args=(server_socket, _sanitized_runtime_snapshot()),
        daemon=True,
    )
    thread.start()
    client = DaemonApiClient(ConnectedTransport(client_socket))
    snapshot = client.runtime_snapshot()
    bootstrap = daemon_tui_bootstrap(snapshot, sanitized=True)
    client.close()
    thread.join(1.0)

    assert bootstrap.endpoint == DAEMON_REMOTE_CLIENT_ENDPOINT
    assert bootstrap.connected is True
    assert "scanner_endpoint" not in snapshot

    leaking_client, leaking_server = socket.socketpair()
    leaking_snapshot = _sanitized_runtime_snapshot()
    leaking_snapshot["scanner_endpoint"] = "udp://192.168.20.25:50536"
    leaking_thread = threading.Thread(
        target=_serve_one_api_result,
        args=(leaking_server, leaking_snapshot),
        daemon=True,
    )
    leaking_thread.start()
    leaking_api = DaemonApiClient(ConnectedTransport(leaking_client))
    with pytest.raises(DaemonProtocolError, match="private fields"):
        leaking_api.runtime_snapshot()
    leaking_thread.join(1.0)


def test_remote_event_client_rejects_nested_private_fields() -> None:
    client_socket, server_socket = socket.socketpair()
    transport = ConnectedTransport(client_socket)
    client = DaemonEventClient(transport)
    snapshot_payload = _sanitized_runtime_snapshot()
    snapshot_payload.pop("last_error")
    snapshot_payload.pop("recording")
    snapshot = DaemonEvent(
        sequence=1,
        observed_at=datetime(2026, 9, 2, tzinfo=UTC),
        kind=DaemonEventKind.SNAPSHOT,
        payload=snapshot_payload,
    )
    leaking = DaemonEvent(
        sequence=2,
        observed_at=datetime(2026, 9, 2, tzinfo=UTC),
        kind=DaemonEventKind.SCANNER_CONNECTION,
        payload={
            "connected": True,
            "detail": {"credential_file": "/private/client.secret"},
        },
    )
    server_socket.sendall(snapshot.to_json_line() + leaking.to_json_line())
    assert client.receive().kind == DaemonEventKind.SNAPSHOT
    with pytest.raises(DaemonProtocolError, match="private fields"):
        client.receive()
    assert client.connected is False
    server_socket.close()


def test_daemon_tui_rejects_mixed_local_and_remote_privacy_boundaries() -> None:
    class Endpoint:
        def __init__(self, *, sanitized: bool) -> None:
            self.sanitizes_private_state = sanitized

    with pytest.raises(ValueError, match="same privacy boundary"):
        DaemonTuiRadio(  # type: ignore[arg-type]
            Endpoint(sanitized=True),
            Endpoint(sanitized=False),
        )


def _empty_broker(
    *,
    event_stream: object | None = None,
    waterfall_session: object | None = None,
    pcmu_stream: object | None = None,
) -> DaemonRemoteObservationBroker:
    return DaemonRemoteObservationBroker(
        event_stream=event_stream,  # type: ignore[arg-type]
        waterfall_session=waterfall_session,  # type: ignore[arg-type]
        pcmu_stream=pcmu_stream,  # type: ignore[arg-type]
    )


def _open_selected_client(
    listener: ScriptedListener,
    service: DaemonRemoteService,
    *,
    client_id: str = CLIENT_ID,
) -> socket.socket:
    client, server = socket.socketpair()
    client.settimeout(2.0)
    listener.acceptor.clients.put((server, _peer(client_id)))
    client.sendall(DaemonRemoteServiceRequest(service).to_json_line())
    result = DaemonRemoteServiceResult.from_json_line(_receive_line(client))
    assert result == DaemonRemoteServiceResult.success(service)
    return client


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for remote transport state.")
        time.sleep(0.01)


def test_router_serves_sanitized_events_through_shared_event_client() -> None:
    listener = ScriptedListener()
    events = _event_source()
    broker = _empty_broker(event_stream=events)
    router = DaemonRemoteServiceRouter(
        listener,
        broker,
        accept_poll_interval=0.01,
    ).start()
    client_socket = _open_selected_client(listener, DaemonRemoteService.EVENTS)
    transport = ConnectedTransport(client_socket)
    client = DaemonEventClient(transport)
    try:
        snapshot = client.receive()
        assert snapshot.kind == DaemonEventKind.SNAPSHOT
        assert "scanner_endpoint" not in snapshot.payload
        assert "last_error" not in snapshot.payload
        assert "recording" not in snapshot.payload
        assert client.location is None
        assert transport.connects == 1
    finally:
        client.close()
        _wait_for(lambda: router.snapshot().connected_clients == 0)
        router.stop()


def test_connected_inventory_groups_services_and_removes_closed_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = ScriptedListener()
    router = DaemonRemoteServiceRouter(
        listener, _empty_broker(event_stream=_event_source()), accept_poll_interval=0.01,
    )
    server = DaemonApiServer(router, AuthorizedPingApi(), accept_poll_interval=0.01)
    server.start()
    clients: list[socket.socket] = []
    try:
        for identifier, service in (
            ("display-a", DaemonRemoteService.API),
            ("display-a", DaemonRemoteService.EVENTS),
            ("display-b", DaemonRemoteService.EVENTS),
        ):
            clients.append(_open_selected_client(listener, service, client_id=identifier))
        inventory = router.connected_clients_snapshot()
        assert inventory["active"] is True
        rows = inventory["clients"]
        assert isinstance(rows, list)
        assert [row["client_id"] for row in rows] == ["display-a", "display-b"]
        assert rows[0]["services"] == {"api": 1, "events": 1}
        assert rows[0]["connections"] == 2
        assert rows[0]["scopes"] == ["observe"]
        assert rows[0]["connected_seconds"] >= 0
        assert set(rows[0]) == {
            "client_id", "services", "scopes", "connections", "connected_seconds",
        }
        assert "display-a" not in json.dumps(router.snapshot().as_dict())
        with monkeypatch.context() as patch:
            patch.setattr(
                DaemonRemoteApiPeer, "daemon_api_connection_current",
                lambda peer: peer.client_id != "display-a",
            )
            current = router.connected_clients_snapshot()["clients"]
            assert isinstance(current, list)
            assert [row["client_id"] for row in current] == ["display-b"]
        for client in clients:
            client.close()
        _wait_for(lambda: router.snapshot().connected_clients == 0)
        assert router.connected_clients_snapshot() == {"active": True, "clients": []}
    finally:
        for client in clients:
            client.close()
        server.stop()
    assert router.connected_clients_snapshot() == {"active": False, "clients": []}


class AuthorizedPingApi:
    maximum_request_seconds = 0.0

    def handle_json_line(self, data: bytes | str) -> bytes:
        del data
        raise AssertionError("Remote API requests must use authorized dispatch.")

    def handle_authorized_json_line(
        self,
        data: bytes | str,
        *,
        allowed_operations: object,
        redacted_result_fields: object,
    ) -> bytes:
        del allowed_operations, redacted_result_fields
        request = json.loads(data)
        return (
            json.dumps(
                {
                    "protocol": DAEMON_API_PROTOCOL,
                    "version": DAEMON_API_VERSION,
                    "request_id": request["request_id"],
                    "ok": True,
                    "result": {"pong": True},
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )


def test_router_hands_selected_api_to_existing_api_server() -> None:
    listener = ScriptedListener()
    router = DaemonRemoteServiceRouter(
        listener,
        _empty_broker(),
        accept_poll_interval=0.01,
    )
    server = DaemonApiServer(
        router,
        AuthorizedPingApi(),
        accept_poll_interval=0.01,
        shutdown_timeout=1.0,
    )
    server.start()
    client_socket = _open_selected_client(listener, DaemonRemoteService.API)
    client = DaemonApiClient(ConnectedTransport(client_socket))
    try:
        assert client.request(DaemonApiOperation.PING) == {"pong": True}
        assert isinstance(router, DaemonServerListener)
        assert isinstance(router, DaemonServerAcceptor)
    finally:
        client.close()
        server.stop()

    snapshot = router.snapshot()
    assert snapshot.accepted_clients == 1
    assert snapshot.selected_clients == 1
    assert snapshot.connected_clients == 0
    assert listener.starts == 1
    assert listener.stops == 1


def _packet(sequence: int) -> PcmuPacket:
    return PcmuPacket(
        endpoint="rtsp://192.168.20.25/au:scanner.au",
        sequence=sequence,
        timestamp=sequence * 160,
        ssrc=1234,
        payload=b"remote audio",
        observed_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
    )


def test_router_serves_redacted_audio_through_shared_pcmu_client() -> None:
    listener = ScriptedListener()
    audio = PcmuPublisher()
    router = DaemonRemoteServiceRouter(
        listener,
        _empty_broker(pcmu_stream=audio),
        accept_poll_interval=0.01,
    ).start()
    client_socket = _open_selected_client(listener, DaemonRemoteService.AUDIO)
    audio.publish(_packet(10))
    client = DaemonPcmuClient(ConnectedTransport(client_socket))
    try:
        delivery = client.receive()
        assert delivery.packet.endpoint == DAEMON_REMOTE_AUDIO_ENDPOINT
        assert delivery.packet.payload == b"remote audio"
        assert client.location is None
    finally:
        client.close()
        _wait_for(lambda: router.snapshot().connected_clients == 0)
        router.stop()


class FakeWaterfallRadio:
    def __init__(self) -> None:
        self.publisher = WaterfallPublisher(queue_capacity=8)
        self.parser = PacketParser()
        self.starts = 0
        self.stops = 0

    def get_waterfall_status(self, *, timeout: float = 2.0) -> GstResponse:
        del timeout
        return GstResponse(
            display_form="00000",
            lines=tuple(DisplayLine("", "") for _ in range(5)),
            mute="0",
            alert_led="0",
            charge_led="0",
            waterfall_mode="1",
            marker_frequency="9490000",
            modulation="FMB",
            marker_position="120",
            center_frequency="9490000",
            lower_frequency="9418000",
            upper_frequency="9562000",
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
        self.starts += 1
        pwf = self.parser.parse_typed(self.parser.parse_packet("PWF,one,two"))
        gwf = self.parser.parse_typed(
            self.parser.parse_packet(
                "GWF," + ",".join(str(index) for index in range(240))
            )
        )
        assert isinstance(pwf, PwfResponse)
        assert isinstance(gwf, GwfResponse)
        self.publisher.publish(pwf)
        self.publisher.publish(gwf)
        return pwf, gwf

    def get_waterfall_frame(self, *, timeout: float = 2.0) -> GwfResponse:
        del timeout
        raise AssertionError("The remote router must not own a Waterfall poller.")

    def stop_waterfall_publication(self, *, timeout: float = 2.0) -> None:
        del timeout
        self.stops += 1

    def subscribe_waterfall(self) -> WaterfallSubscription:
        return self.publisher.subscribe()

    def waterfall_snapshot(self):  # type: ignore[no-untyped-def]
        return self.publisher.snapshot()


def test_router_serves_existing_shared_waterfall_session_protocol() -> None:
    listener = ScriptedListener()
    radio = FakeWaterfallRadio()
    session = WaterfallSession(radio)
    router = DaemonRemoteServiceRouter(
        listener,
        _empty_broker(waterfall_session=session),
        accept_poll_interval=0.01,
    ).start()
    client_socket = _open_selected_client(listener, DaemonRemoteService.WATERFALL)
    client = DaemonWaterfallClient(ConnectedTransport(client_socket))
    try:
        checkpoint = client.receive()
        pwf = client.receive()
        gwf = client.receive()
        assert checkpoint.kind is DaemonWaterfallRecordKind.SESSION_CHECKPOINT
        assert checkpoint.payload["consumer_count"] == 1
        assert pwf.kind is DaemonWaterfallRecordKind.PWF
        assert gwf.kind is DaemonWaterfallRecordKind.GWF
        assert len(gwf.payload["values"]) == 240  # type: ignore[arg-type]
        assert radio.starts == 1
    finally:
        client.close()
        _wait_for(lambda: session.consumer_count == 0)
        router.stop()
    assert radio.stops == 1


def test_router_rejects_malformed_unavailable_and_over_capacity_peers() -> None:
    listener = ScriptedListener()
    router = DaemonRemoteServiceRouter(
        listener,
        _empty_broker(),
        max_clients=1,
        selection_timeout=0.1,
        accept_poll_interval=0.01,
        shutdown_timeout=1.0,
    ).start()

    malformed_client, malformed_server = socket.socketpair()
    malformed_client.settimeout(1.0)
    listener.acceptor.clients.put((malformed_server, _peer("malformed")))
    malformed_client.sendall(b"{}\n")
    malformed = DaemonRemoteServiceResult.from_json_line(
        _receive_line(malformed_client)
    )
    assert malformed.error is DaemonRemoteServiceErrorReason.INVALID_FRAME
    malformed_client.close()

    _wait_for(lambda: router.snapshot().connected_clients == 0)
    oversized_client, oversized_server = socket.socketpair()
    oversized_client.settimeout(1.0)
    listener.acceptor.clients.put((oversized_server, _peer("oversized")))
    oversized_client.sendall(b"x" * DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES)
    oversized = DaemonRemoteServiceResult.from_json_line(
        _receive_line(oversized_client)
    )
    assert oversized.error is DaemonRemoteServiceErrorReason.INVALID_FRAME
    oversized_client.close()

    _wait_for(lambda: router.snapshot().connected_clients == 0)
    unavailable_client, unavailable_server = socket.socketpair()
    unavailable_client.settimeout(1.0)
    listener.acceptor.clients.put((unavailable_server, _peer("unavailable")))
    unavailable_client.sendall(
        DaemonRemoteServiceRequest(DaemonRemoteService.AUDIO).to_json_line()
    )
    unavailable = DaemonRemoteServiceResult.from_json_line(
        _receive_line(unavailable_client)
    )
    assert unavailable.error is DaemonRemoteServiceErrorReason.SOURCE_UNAVAILABLE
    unavailable_client.close()

    _wait_for(lambda: router.snapshot().connected_clients == 0)
    silent_client, silent_server = socket.socketpair()
    silent_client.settimeout(1.0)
    listener.acceptor.clients.put((silent_server, _peer("silent")))
    _wait_for(lambda: router.snapshot().connected_clients == 1)
    excess_client, excess_server = socket.socketpair()
    excess_client.settimeout(1.0)
    listener.acceptor.clients.put((excess_server, _peer("excess")))
    assert excess_client.recv(1) == b""
    _wait_for(lambda: router.snapshot().connected_clients == 0)
    silent_client.close()
    excess_client.close()

    snapshot = router.snapshot()
    assert snapshot.rejected_clients >= 3
    assert snapshot.max_clients == 1
    assert CLIENT_ID not in json.dumps(snapshot.as_dict())
    router.stop()
