from __future__ import annotations

import logging
import queue
import select
import socket as socket_module
import threading
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Protocol

from .daemon_ipc import DaemonSocketListener
from .exceptions import DaemonIpcError
from .home_assistant_live_audio import LiveAudioLeaseClosed

logger = logging.getLogger(__name__)

DAEMON_LIVE_AUDIO_DEFAULT_MAX_CLIENTS = 4
DAEMON_LIVE_AUDIO_DEFAULT_SEND_TIMEOUT = 5.0
DAEMON_LIVE_AUDIO_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_LIVE_AUDIO_DEFAULT_SHUTDOWN_TIMEOUT = 2.0
DAEMON_LIVE_AUDIO_ACCEPTED = b"\x00"
DAEMON_LIVE_AUDIO_UNAVAILABLE = b"\x01"


class _LiveAudioLease(Protocol):
    def get(self, timeout: float | None = None) -> bytes: ...

    def close(self) -> None: ...


class _LiveAudioSession(Protocol):
    def subscribe(self) -> _LiveAudioLease: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DaemonLiveAudioServerSnapshot:
    """Low-rate, payload-free state for the private MP3 Unix service."""

    active: bool
    connected_clients: int
    max_clients: int
    accepted_clients: int
    rejected_clients: int
    chunks_sent: int
    bytes_sent: int
    last_error: str | None


class DaemonLiveAudioServer:
    """Fan out daemon-owned shared MP3 leases to private Unix clients."""

    def __init__(
        self,
        listener: DaemonSocketListener,
        session: _LiveAudioSession,
        *,
        max_clients: int = DAEMON_LIVE_AUDIO_DEFAULT_MAX_CLIENTS,
        send_timeout: float = DAEMON_LIVE_AUDIO_DEFAULT_SEND_TIMEOUT,
        accept_poll_interval: float = (DAEMON_LIVE_AUDIO_DEFAULT_ACCEPT_POLL_INTERVAL),
        shutdown_timeout: float = DAEMON_LIVE_AUDIO_DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        if not isinstance(listener, DaemonSocketListener):
            raise TypeError("Daemon live-audio listener is invalid.")
        if not callable(getattr(session, "subscribe", None)) or not callable(
            getattr(session, "close", None)
        ):
            raise TypeError("Daemon live-audio session is invalid.")
        self.listener = listener
        self.session = session
        self.max_clients = _positive_integer(max_clients, "maximum clients")
        self.send_timeout = _positive_number(send_timeout, "send timeout")
        self.accept_poll_interval = _positive_number(
            accept_poll_interval,
            "accept poll interval",
        )
        self.shutdown_timeout = _positive_number(
            shutdown_timeout,
            "shutdown timeout",
        )
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._clients: dict[socket_module.socket, threading.Thread] = {}
        self._started = False
        self._stopped = False
        self._active = False
        self._accepted_clients = 0
        self._rejected_clients = 0
        self._chunks_sent = 0
        self._bytes_sent = 0
        self._last_error: str | None = None

    def snapshot(self) -> DaemonLiveAudioServerSnapshot:
        with self._state_lock:
            return DaemonLiveAudioServerSnapshot(
                active=self._active,
                connected_clients=len(self._clients),
                max_clients=self.max_clients,
                accepted_clients=self._accepted_clients,
                rejected_clients=self._rejected_clients,
                chunks_sent=self._chunks_sent,
                bytes_sent=self._bytes_sent,
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError("Daemon live-audio servers cannot restart after shutdown.")
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            try:
                listener_socket = self.listener.start()
                listener_socket.settimeout(self.accept_poll_interval)
                thread = threading.Thread(
                    target=self._accept_loop,
                    args=(listener_socket,),
                    name="daemon-live-audio-accept",
                    daemon=True,
                )
                self._accept_thread = thread
                with self._state_lock:
                    self._active = True
                thread.start()
            except BaseException as error:
                self._record_error(error)
                self._stop_event.set()
                self._stopped = True
                with self._state_lock:
                    self._active = False
                with suppress(Exception):
                    self.listener.stop()
                with suppress(Exception):
                    self.session.close()
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                return
            self._stop_event.set()
            failures: list[BaseException] = []
            if self._started:
                try:
                    self.listener.stop()
                except BaseException as error:
                    failures.append(error)

            with self._state_lock:
                clients = tuple(self._clients)
                workers = tuple(self._clients.values())
                accept_thread = self._accept_thread
            for client in clients:
                _close_socket(client)

            deadline = monotonic() + self.shutdown_timeout
            for thread in tuple(
                item
                for item in (accept_thread, *workers)
                if item is not None and item is not threading.current_thread()
            ):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                thread.join(remaining)

            try:
                self.session.close()
            except BaseException as error:
                failures.append(error)
            with self._state_lock:
                self._active = False
                alive = tuple(worker.name for worker in workers if worker.is_alive())
            self._stopped = True
            if failures:
                raise failures[0]
            if alive:
                raise DaemonIpcError(
                    "Daemon live-audio workers did not stop before the shutdown deadline."
                )

    def _accept_loop(self, listener_socket: socket_module.socket) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    client, _address = listener_socket.accept()
                except TimeoutError:
                    continue
                except OSError as error:
                    if self._stop_event.is_set():
                        return
                    self._record_error(error)
                    continue
                self._accept_client(client)
        finally:
            with self._state_lock:
                self._active = False

    def _accept_client(self, client: socket_module.socket) -> None:
        client.settimeout(self.send_timeout)
        with self._state_lock:
            at_capacity = len(self._clients) >= self.max_clients
            if at_capacity:
                self._rejected_clients += 1
        if at_capacity:
            with suppress(OSError):
                client.sendall(DAEMON_LIVE_AUDIO_UNAVAILABLE)
            _close_socket(client)
            return

        thread = threading.Thread(
            target=self._serve_client,
            args=(client,),
            name="daemon-live-audio-client",
            daemon=True,
        )
        with self._state_lock:
            self._clients[client] = thread
            self._accepted_clients += 1
        try:
            thread.start()
        except BaseException as error:
            with self._state_lock:
                self._clients.pop(client, None)
            _close_socket(client)
            self._record_error(error)

    def _serve_client(self, client: socket_module.socket) -> None:
        lease: _LiveAudioLease | None = None
        try:
            try:
                lease = self.session.subscribe()
            except RuntimeError:
                with self._state_lock:
                    self._rejected_clients += 1
                client.sendall(DAEMON_LIVE_AUDIO_UNAVAILABLE)
                return
            client.sendall(DAEMON_LIVE_AUDIO_ACCEPTED)
            while not self._stop_event.is_set():
                try:
                    data = lease.get(self.accept_poll_interval)
                except queue.Empty:
                    if _client_disconnected(client):
                        return
                    continue
                except LiveAudioLeaseClosed:
                    return
                if not data:
                    continue
                client.sendall(data)
                with self._state_lock:
                    self._chunks_sent += 1
                    self._bytes_sent += len(data)
        except (BrokenPipeError, ConnectionError, OSError):
            return
        except Exception as error:
            self._record_error(error)
        finally:
            if lease is not None:
                with suppress(Exception):
                    lease.close()
            with self._state_lock:
                self._clients.pop(client, None)
            _close_socket(client)

    def _record_error(self, error: BaseException) -> None:
        with self._state_lock:
            self._last_error = error.__class__.__name__


def _close_socket(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()


def _client_disconnected(client: socket_module.socket) -> bool:
    try:
        readable, _writable, _exceptional = select.select(
            (client,),
            (),
            (),
            0,
        )
        if not readable:
            return False
        return client.recv(1, socket_module.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def _positive_integer(value: object, description: str) -> int:
    if type(value) is not int:
        raise TypeError(f"Daemon live-audio {description} must be an integer.")
    if value <= 0:
        raise ValueError(f"Daemon live-audio {description} must be greater than zero.")
    return value


def _positive_number(value: object, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Daemon live-audio {description} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"Daemon live-audio {description} must be finite and positive.")
    return normalized


__all__ = [
    "DAEMON_LIVE_AUDIO_ACCEPTED",
    "DAEMON_LIVE_AUDIO_DEFAULT_ACCEPT_POLL_INTERVAL",
    "DAEMON_LIVE_AUDIO_DEFAULT_MAX_CLIENTS",
    "DAEMON_LIVE_AUDIO_DEFAULT_SEND_TIMEOUT",
    "DAEMON_LIVE_AUDIO_DEFAULT_SHUTDOWN_TIMEOUT",
    "DAEMON_LIVE_AUDIO_UNAVAILABLE",
    "DaemonLiveAudioServer",
    "DaemonLiveAudioServerSnapshot",
]
