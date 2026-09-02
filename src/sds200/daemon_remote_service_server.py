"""Route authenticated TLS connections to existing daemon-owned services."""

from __future__ import annotations

import queue
import select
import socket as socket_module
import threading
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from queue import Empty, Full, Queue
from time import monotonic

from .daemon_events import DAEMON_EVENT_DEFAULT_MAX_BYTES
from .daemon_remote_observation import (
    DaemonRemoteAudioLease,
    DaemonRemoteEventLease,
    DaemonRemoteObservationBroker,
    DaemonRemoteObservationError,
    DaemonRemoteObservationErrorReason,
    DaemonRemoteWaterfallLease,
)
from .daemon_remote_server import DaemonRemoteApiPeer
from .daemon_remote_service import (
    DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES,
    DaemonRemoteService,
    DaemonRemoteServiceError,
    DaemonRemoteServiceErrorReason,
    DaemonRemoteServiceRequest,
    DaemonRemoteServiceResult,
)
from .daemon_transport import DaemonServerAcceptor, DaemonServerListener
from .daemon_waterfall_protocol import (
    DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES,
    DaemonWaterfallRecord,
    waterfall_checkpoint_record,
    waterfall_delivery_record,
    waterfall_transition_record,
)
from .exceptions import DaemonIpcError
from .pcmu_protocol import (
    PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
    PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
    encode_pcmu_delivery,
)
from .waterfall_session import WaterfallSessionTransition

DAEMON_REMOTE_SERVICE_DEFAULT_MAX_CLIENTS = 24
DAEMON_REMOTE_SERVICE_DEFAULT_SELECTION_TIMEOUT = 5.0
DAEMON_REMOTE_SERVICE_DEFAULT_SEND_TIMEOUT = 5.0
DAEMON_REMOTE_SERVICE_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_REMOTE_SERVICE_DEFAULT_SHUTDOWN_TIMEOUT = 8.0
DAEMON_REMOTE_SERVICE_TRANSITION_QUEUE_CAPACITY = 32

_STOPPED = object()


@dataclass(frozen=True, slots=True)
class DaemonRemoteServiceRouterSnapshot:
    """Redacted aggregate state for the authenticated service router."""

    active: bool
    max_clients: int
    connected_clients: int
    pending_selection: int
    api_clients: int
    event_clients: int
    waterfall_clients: int
    audio_clients: int
    ready_api_clients: int
    accepted_clients: int
    rejected_clients: int
    selected_clients: int
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "max_clients": self.max_clients,
            "connected_clients": self.connected_clients,
            "pending_selection": self.pending_selection,
            "api_clients": self.api_clients,
            "event_clients": self.event_clients,
            "waterfall_clients": self.waterfall_clients,
            "audio_clients": self.audio_clients,
            "ready_api_clients": self.ready_api_clients,
            "accepted_clients": self.accepted_clients,
            "rejected_clients": self.rejected_clients,
            "selected_clients": self.selected_clients,
            "last_error": self.last_error,
        }


class _DaemonRemoteSelectedApiPeer:
    """Release router capacity when the API server closes one peer."""

    def __init__(
        self,
        router: DaemonRemoteServiceRouter,
        stream: socket_module.socket,
        peer: DaemonRemoteApiPeer,
    ) -> None:
        self._router = router
        self._stream = stream
        self._peer = peer
        self._lock = threading.Lock()
        self._closed = False

    def handle_daemon_api_json_line(
        self,
        api: object,
        data: bytes | str,
    ) -> bytes:
        return self._peer.handle_daemon_api_json_line(api, data)

    def daemon_api_connection_current(self) -> bool:
        return self._peer.daemon_api_connection_current()

    def close_daemon_api_peer_context(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._peer.close_daemon_api_peer_context()
        finally:
            self._router._release(self._stream)


@dataclass(frozen=True, slots=True)
class _ReadyApiClient:
    stream: socket_module.socket
    peer: _DaemonRemoteSelectedApiPeer


class DaemonRemoteServiceRouter:
    """Select and serve one bounded daemon service per authenticated stream."""

    def __init__(
        self,
        listener: DaemonServerListener,
        observations: DaemonRemoteObservationBroker,
        *,
        max_clients: int = DAEMON_REMOTE_SERVICE_DEFAULT_MAX_CLIENTS,
        selection_timeout: float = DAEMON_REMOTE_SERVICE_DEFAULT_SELECTION_TIMEOUT,
        send_timeout: float = DAEMON_REMOTE_SERVICE_DEFAULT_SEND_TIMEOUT,
        accept_poll_interval: float = DAEMON_REMOTE_SERVICE_DEFAULT_ACCEPT_POLL_INTERVAL,
        shutdown_timeout: float = DAEMON_REMOTE_SERVICE_DEFAULT_SHUTDOWN_TIMEOUT,
        max_event_bytes: int = DAEMON_EVENT_DEFAULT_MAX_BYTES,
        max_waterfall_record_bytes: int = DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES,
        max_audio_endpoint_bytes: int = PCMU_STREAM_DEFAULT_MAX_ENDPOINT_BYTES,
        max_audio_frame_bytes: int = PCMU_STREAM_DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        if not isinstance(listener, DaemonServerListener):
            raise TypeError(
                "Remote daemon service router listener must be a DaemonServerListener."
            )
        if not isinstance(observations, DaemonRemoteObservationBroker):
            raise TypeError(
                "Remote daemon service router requires a remote observation broker."
            )
        self.listener = listener
        self.observations = observations
        self.max_clients = _positive_integer(max_clients, label="Maximum remote clients")
        self.selection_timeout = _positive_number(
            selection_timeout,
            label="Remote service selection timeout",
        )
        self.send_timeout = _positive_number(
            send_timeout,
            label="Remote service send timeout",
        )
        self.accept_poll_interval = _positive_number(
            accept_poll_interval,
            label="Remote service accept poll interval",
        )
        self.shutdown_timeout = _positive_number(
            shutdown_timeout,
            label="Remote service shutdown timeout",
        )
        if self.shutdown_timeout <= self.selection_timeout:
            raise ValueError(
                "Remote service shutdown timeout must exceed selection timeout."
            )
        self.max_event_bytes = _positive_integer(
            max_event_bytes,
            label="Maximum remote event size",
        )
        self.max_waterfall_record_bytes = _positive_integer(
            max_waterfall_record_bytes,
            label="Maximum remote waterfall record size",
        )
        self.max_audio_endpoint_bytes = _positive_integer(
            max_audio_endpoint_bytes,
            label="Maximum remote audio endpoint size",
        )
        self.max_audio_frame_bytes = _positive_integer(
            max_audio_frame_bytes,
            label="Maximum remote audio frame size",
        )

        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._clients: dict[socket_module.socket, DaemonRemoteService | None] = {}
        self._workers: dict[socket_module.socket, threading.Thread] = {}
        self._api_ready: Queue[_ReadyApiClient | object] = Queue(
            maxsize=self.max_clients
        )
        self._delivery_timeout: float | None = None
        self._started = False
        self._stopped = False
        self._active = False
        self._ready_api_clients = 0
        self._accepted_clients = 0
        self._rejected_clients = 0
        self._selected_clients = 0
        self._last_error: str | None = None

    def start(self) -> DaemonRemoteServiceRouter:
        with self._lifecycle_lock:
            if self._started:
                if self._stopped:
                    raise RuntimeError(
                        "Remote daemon service routers cannot restart after shutdown."
                    )
                return self
            self._started = True
            self._stop_event.clear()
            try:
                acceptor = self.listener.start()
                acceptor.settimeout(self.accept_poll_interval)
                thread = threading.Thread(
                    target=self._accept_loop,
                    args=(acceptor,),
                    name="daemon-remote-service-accept",
                    daemon=True,
                )
                self._accept_thread = thread
                with self._state_lock:
                    self._active = True
                thread.start()
            except BaseException:
                self._stopped = True
                self._stop_event.set()
                with self._state_lock:
                    self._active = False
                    self._last_error = "start_failed"
                with suppress(BaseException):
                    self.listener.stop()
                raise
            return self

    def settimeout(self, value: float | None) -> None:
        if value is not None:
            value = _positive_number(
                value,
                label="Remote selected API accept timeout",
            )
        with self._state_lock:
            self._delivery_timeout = value

    def accept(self) -> tuple[socket_module.socket, object]:
        with self._state_lock:
            if not self._started:
                raise RuntimeError("Remote daemon service router is not active.")
            if self._stopped and self._api_ready.empty():
                raise OSError("Remote daemon service router is closed.")
            timeout = self._delivery_timeout
        deadline = None if timeout is None else monotonic() + timeout
        while True:
            remaining = None if deadline is None else deadline - monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError
            try:
                delivery = self._api_ready.get(timeout=remaining)
            except Empty as error:
                with self._state_lock:
                    stopped = self._stopped
                if stopped:
                    raise OSError(
                        "Remote daemon service router is closed."
                    ) from error
                raise TimeoutError from error
            if not isinstance(delivery, _ReadyApiClient):
                raise OSError("Remote daemon service router is closed.")
            with self._state_lock:
                self._ready_api_clients -= 1
            if not delivery.peer.daemon_api_connection_current():
                _close_stream(delivery.stream)
                delivery.peer.close_daemon_api_peer_context()
                continue
            return delivery.stream, delivery.peer

    def snapshot(self) -> DaemonRemoteServiceRouterSnapshot:
        with self._state_lock:
            services = tuple(self._clients.values())
            return DaemonRemoteServiceRouterSnapshot(
                active=self._active,
                max_clients=self.max_clients,
                connected_clients=len(self._clients),
                pending_selection=services.count(None),
                api_clients=services.count(DaemonRemoteService.API),
                event_clients=services.count(DaemonRemoteService.EVENTS),
                waterfall_clients=services.count(DaemonRemoteService.WATERFALL),
                audio_clients=services.count(DaemonRemoteService.AUDIO),
                ready_api_clients=self._ready_api_clients,
                accepted_clients=self._accepted_clients,
                rejected_clients=self._rejected_clients,
                selected_clients=self._selected_clients,
                last_error=self._last_error,
            )

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return
            self._stopped = True
            self._stop_event.set()
            failures: list[BaseException] = []
            try:
                self.listener.stop()
            except BaseException as error:
                failures.append(error)
            with self._state_lock:
                clients = tuple(self._clients)
                workers = tuple(self._workers.values())
                accept_thread = self._accept_thread
                self._active = False
            for client in clients:
                _close_stream(client)
            while True:
                try:
                    delivery = self._api_ready.get_nowait()
                except Empty:
                    break
                if isinstance(delivery, _ReadyApiClient):
                    with self._state_lock:
                        self._ready_api_clients -= 1
                    _close_stream(delivery.stream)
                    delivery.peer.close_daemon_api_peer_context()
            with suppress(Full):
                self._api_ready.put_nowait(_STOPPED)

            deadline = monotonic() + self.shutdown_timeout
            threads = tuple(
                thread
                for thread in (accept_thread, *workers)
                if thread is not None and thread is not threading.current_thread()
            )
            for thread in threads:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                thread.join(remaining)
            if any(thread.is_alive() for thread in threads):
                failures.append(
                    DaemonIpcError(
                        "Remote daemon service workers did not stop before deadline."
                    )
                )
            if failures:
                raise failures[0]

    def __enter__(self) -> DaemonRemoteServiceRouter:
        return self.start()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, traceback
        try:
            self.stop()
        except BaseException:
            if exception is None:
                raise

    def _accept_loop(self, acceptor: DaemonServerAcceptor) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    client, raw_peer = acceptor.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if not self._stop_event.is_set():
                        self._record_error("accept_failed")
                    return
                if not isinstance(raw_peer, DaemonRemoteApiPeer):
                    _close_stream(client)
                    self._record_rejection("invalid_peer")
                    continue
                self._start_client(client, raw_peer)
        finally:
            with self._state_lock:
                self._active = False

    def _start_client(
        self,
        client: socket_module.socket,
        peer: DaemonRemoteApiPeer,
    ) -> None:
        start_error: BaseException | None = None
        with self._state_lock:
            if self._stop_event.is_set() or len(self._clients) >= self.max_clients:
                worker = None
                if not self._stop_event.is_set():
                    self._rejected_clients += 1
            else:
                sequence = self._accepted_clients + 1
                worker = threading.Thread(
                    target=self._select_and_serve,
                    args=(client, peer),
                    name=f"daemon-remote-service-{sequence}",
                    daemon=True,
                )
                self._clients[client] = None
                self._workers[client] = worker
                try:
                    worker.start()
                except BaseException as error:
                    self._clients.pop(client, None)
                    self._workers.pop(client, None)
                    self._rejected_clients += 1
                    start_error = error
                else:
                    self._accepted_clients = sequence
        if worker is None or start_error is not None:
            _close_stream(client)
            peer.close_daemon_api_peer_context()
        if start_error is not None:
            self._record_error("worker_start_failed")

    def _select_and_serve(
        self,
        client: socket_module.socket,
        peer: DaemonRemoteApiPeer,
    ) -> None:
        transferred = False
        try:
            deadline = monotonic() + self.selection_timeout
            request = DaemonRemoteServiceRequest.from_json_line(
                _receive_selection_frame(client, deadline=deadline)
            )
            self._mark_selected(client, request.service)
            if request.service is DaemonRemoteService.API:
                selected_peer = _DaemonRemoteSelectedApiPeer(self, client, peer)
                _send_selection_result(
                    client,
                    DaemonRemoteServiceResult.success(request.service),
                    timeout=self.send_timeout,
                )
                self._api_ready.put_nowait(_ReadyApiClient(client, selected_peer))
                with self._state_lock:
                    self._ready_api_clients += 1
                transferred = True
                return
            if request.service is DaemonRemoteService.EVENTS:
                event_lease = self.observations.subscribe_events(peer.authenticated)
                self._serve_events(client, request.service, event_lease)
                return
            if request.service is DaemonRemoteService.WATERFALL:
                waterfall_lease = self.observations.subscribe_waterfall(
                    peer.authenticated
                )
                self._serve_waterfall(client, request.service, waterfall_lease)
                return
            audio_lease = self.observations.subscribe_audio(peer.authenticated)
            self._serve_audio(client, request.service, audio_lease)
        except DaemonRemoteServiceError as error:
            _send_selection_failure(client, error.reason, timeout=self.send_timeout)
            self._record_rejection(error.reason.value)
        except DaemonRemoteObservationError as error:
            reason = _observation_service_error(error.reason)
            _send_selection_failure(client, reason, timeout=self.send_timeout)
            self._record_rejection(reason.value)
        except (OSError, TimeoutError, Full):
            self._record_rejection("transport_failed")
        except Exception:
            self._record_error("service_failed")
        finally:
            with self._state_lock:
                self._workers.pop(client, None)
            if not transferred:
                _close_stream(client)
                peer.close_daemon_api_peer_context()
                self._release(client)

    def _serve_events(
        self,
        client: socket_module.socket,
        service: DaemonRemoteService,
        lease: DaemonRemoteEventLease,
    ) -> None:
        try:
            _send_selection_result(
                client,
                DaemonRemoteServiceResult.success(service),
                timeout=self.send_timeout,
            )
            while not self._stop_event.is_set():
                try:
                    event = lease.get(timeout=self.accept_poll_interval)
                except queue.Empty:
                    if _client_disconnected_or_sent_data(client):
                        return
                    continue
                encoded = event.to_json_line()
                if len(encoded) > self.max_event_bytes:
                    raise DaemonIpcError("Remote daemon event exceeds configured limit.")
                client.settimeout(self.send_timeout)
                client.sendall(encoded)
        except DaemonRemoteObservationError:
            return
        finally:
            with suppress(DaemonRemoteObservationError):
                lease.close()

    def _serve_waterfall(
        self,
        client: socket_module.socket,
        service: DaemonRemoteService,
        lease: DaemonRemoteWaterfallLease,
    ) -> None:
        transitions: Queue[WaterfallSessionTransition] = Queue(
            maxsize=DAEMON_REMOTE_SERVICE_TRANSITION_QUEUE_CAPACITY
        )
        transition_overflow = threading.Event()

        def on_transition(transition: WaterfallSessionTransition) -> None:
            try:
                transitions.put_nowait(transition)
            except Full:
                transition_overflow.set()

        unsubscribe = lease.on_transition(on_transition)
        sequence = 0
        try:
            while True:
                with suppress(Empty):
                    transitions.get_nowait()
                    continue
                break
            _send_selection_result(
                client,
                DaemonRemoteServiceResult.success(service),
                timeout=self.send_timeout,
            )
            sequence += 1
            self._send_waterfall_record(
                client,
                waterfall_checkpoint_record(sequence, lease.snapshot()),
            )
            while not self._stop_event.is_set():
                if transition_overflow.is_set():
                    raise DaemonIpcError(
                        "Remote waterfall transition queue exceeded its limit."
                    )
                try:
                    transition = transitions.get_nowait()
                except Empty:
                    transition = None
                if transition is not None:
                    sequence += 1
                    self._send_waterfall_record(
                        client,
                        waterfall_transition_record(sequence, transition),
                    )
                    continue
                try:
                    delivery = lease.get(timeout=self.accept_poll_interval)
                except queue.Empty:
                    if _client_disconnected_or_sent_data(client):
                        return
                    continue
                sequence += 1
                self._send_waterfall_record(
                    client,
                    waterfall_delivery_record(sequence, delivery, lease.snapshot()),
                )
        except DaemonRemoteObservationError:
            return
        finally:
            unsubscribe()
            with suppress(DaemonRemoteObservationError):
                lease.close()

    def _send_waterfall_record(
        self,
        client: socket_module.socket,
        record: DaemonWaterfallRecord,
    ) -> None:
        encoded = record.to_json_line()
        if len(encoded) > self.max_waterfall_record_bytes:
            raise DaemonIpcError("Remote waterfall record exceeds configured limit.")
        client.settimeout(self.send_timeout)
        client.sendall(encoded)

    def _serve_audio(
        self,
        client: socket_module.socket,
        service: DaemonRemoteService,
        lease: DaemonRemoteAudioLease,
    ) -> None:
        try:
            _send_selection_result(
                client,
                DaemonRemoteServiceResult.success(service),
                timeout=self.send_timeout,
            )
            while not self._stop_event.is_set():
                try:
                    delivery = lease.get(timeout=self.accept_poll_interval)
                except queue.Empty:
                    if _client_disconnected_or_sent_data(client):
                        return
                    continue
                encoded = encode_pcmu_delivery(
                    delivery,
                    max_endpoint_bytes=self.max_audio_endpoint_bytes,
                    max_frame_bytes=self.max_audio_frame_bytes,
                )
                client.settimeout(self.send_timeout)
                client.sendall(encoded)
        except DaemonRemoteObservationError:
            return
        finally:
            with suppress(DaemonRemoteObservationError):
                lease.close()

    def _mark_selected(
        self,
        client: socket_module.socket,
        service: DaemonRemoteService,
    ) -> None:
        with self._state_lock:
            if client not in self._clients:
                raise OSError("Remote daemon service client is no longer active.")
            self._clients[client] = service
            self._selected_clients += 1

    def _release(self, client: socket_module.socket) -> None:
        with self._state_lock:
            self._clients.pop(client, None)

    def _record_rejection(self, reason: str) -> None:
        with self._state_lock:
            self._rejected_clients += 1
            self._last_error = reason

    def _record_error(self, reason: str) -> None:
        with self._state_lock:
            self._last_error = reason


def _receive_selection_frame(
    client: socket_module.socket,
    *,
    deadline: float,
) -> bytes:
    frame = bytearray()
    while len(frame) < DAEMON_REMOTE_SERVICE_MAX_FRAME_BYTES:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        client.settimeout(remaining)
        chunk = client.recv(1)
        if not chunk:
            raise DaemonRemoteServiceError(
                DaemonRemoteServiceErrorReason.INVALID_FRAME
            )
        if chunk == b"\r":
            raise DaemonRemoteServiceError(
                DaemonRemoteServiceErrorReason.INVALID_FRAME
            )
        if chunk == b"\n":
            if not frame:
                raise DaemonRemoteServiceError(
                    DaemonRemoteServiceErrorReason.INVALID_FRAME
                )
            return bytes(frame)
        frame.extend(chunk)
    raise DaemonRemoteServiceError(DaemonRemoteServiceErrorReason.INVALID_FRAME)


def _send_selection_result(
    client: socket_module.socket,
    result: DaemonRemoteServiceResult,
    *,
    timeout: float,
) -> None:
    client.settimeout(timeout)
    client.sendall(result.to_json_line())


def _send_selection_failure(
    client: socket_module.socket,
    reason: DaemonRemoteServiceErrorReason,
    *,
    timeout: float,
) -> None:
    with suppress(OSError):
        _send_selection_result(
            client,
            DaemonRemoteServiceResult.failure(reason),
            timeout=timeout,
        )


def _observation_service_error(
    reason: DaemonRemoteObservationErrorReason,
) -> DaemonRemoteServiceErrorReason:
    if reason in {
        DaemonRemoteObservationErrorReason.AUTHORIZATION_DENIED,
        DaemonRemoteObservationErrorReason.AUTHENTICATION_EXPIRED,
    }:
        return DaemonRemoteServiceErrorReason.AUTHORIZATION_DENIED
    if reason in {
        DaemonRemoteObservationErrorReason.CAPACITY_EXCEEDED,
        DaemonRemoteObservationErrorReason.DUPLICATE_LEASE,
    }:
        return DaemonRemoteServiceErrorReason.CAPACITY_EXCEEDED
    return DaemonRemoteServiceErrorReason.SOURCE_UNAVAILABLE


def _client_disconnected_or_sent_data(client: socket_module.socket) -> bool:
    try:
        readable, _, _ = select.select((client,), (), (), 0.0)
    except (OSError, ValueError):
        return True
    if not readable:
        return False
    try:
        client.settimeout(0.0)
        client.recv(1)
    except (BlockingIOError, TimeoutError):
        return False
    except OSError:
        return True
    return True


def _positive_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def _positive_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return normalized


def _close_stream(stream: socket_module.socket) -> None:
    with suppress(OSError):
        stream.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        stream.close()


__all__ = [
    "DAEMON_REMOTE_SERVICE_DEFAULT_ACCEPT_POLL_INTERVAL",
    "DAEMON_REMOTE_SERVICE_DEFAULT_MAX_CLIENTS",
    "DAEMON_REMOTE_SERVICE_DEFAULT_SELECTION_TIMEOUT",
    "DAEMON_REMOTE_SERVICE_DEFAULT_SEND_TIMEOUT",
    "DAEMON_REMOTE_SERVICE_DEFAULT_SHUTDOWN_TIMEOUT",
    "DAEMON_REMOTE_SERVICE_TRANSITION_QUEUE_CAPACITY",
    "DaemonRemoteServiceRouter",
    "DaemonRemoteServiceRouterSnapshot",
]
