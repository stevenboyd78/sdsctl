from __future__ import annotations

import logging
import queue
import select
import socket as socket_module
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from typing import Protocol

from .daemon_ipc import DaemonSocketListener
from .daemon_waterfall_protocol import (
    DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES,
    DaemonWaterfallRecord,
    waterfall_checkpoint_record,
    waterfall_delivery_record,
    waterfall_transition_record,
)
from .exceptions import DaemonIpcError
from .waterfall_session import (
    WaterfallSessionLease,
    WaterfallSessionSnapshot,
    WaterfallSessionTransition,
)
from .waterfall_subscriptions import WaterfallSubscriptionClosed

logger = logging.getLogger(__name__)

DAEMON_WATERFALL_DEFAULT_MAX_CLIENTS = 8
DAEMON_WATERFALL_DEFAULT_SEND_TIMEOUT = 5.0
DAEMON_WATERFALL_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_WATERFALL_DEFAULT_SHUTDOWN_TIMEOUT = 8.0


class _WaterfallSessionLike(Protocol):
    def snapshot(self) -> WaterfallSessionSnapshot: ...

    def subscribe(self) -> WaterfallSessionLease: ...

    def on_transition(
        self,
        callback: Callable[[WaterfallSessionTransition], None],
    ) -> Callable[[], None]: ...


@dataclass(frozen=True, slots=True)
class DaemonWaterfallServerSnapshot:
    """Immutable daemon-local waterfall service activity and capacity state."""

    active: bool
    connected_clients: int
    max_clients: int
    max_record_bytes: int
    accepted_clients: int
    rejected_clients: int
    records_sent: int
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "connected_clients": self.connected_clients,
            "max_clients": self.max_clients,
            "max_record_bytes": self.max_record_bytes,
            "accepted_clients": self.accepted_clients,
            "rejected_clients": self.rejected_clients,
            "records_sent": self.records_sent,
            "last_error": self.last_error,
        }


class DaemonWaterfallServer:
    """Serve one demand lease and bounded waterfall stream per Unix client."""

    def __init__(
        self,
        listener: DaemonSocketListener,
        session: _WaterfallSessionLike,
        *,
        max_clients: int = DAEMON_WATERFALL_DEFAULT_MAX_CLIENTS,
        max_record_bytes: int = DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES,
        send_timeout: float = DAEMON_WATERFALL_DEFAULT_SEND_TIMEOUT,
        accept_poll_interval: float = DAEMON_WATERFALL_DEFAULT_ACCEPT_POLL_INTERVAL,
        shutdown_timeout: float = DAEMON_WATERFALL_DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        _require_positive_integer(max_clients, label="Maximum waterfall clients")
        _require_positive_integer(
            max_record_bytes,
            label="Maximum waterfall record size",
        )
        self.listener = listener
        self.session = session
        self.max_clients = max_clients
        self.max_record_bytes = max_record_bytes
        self.send_timeout = _require_positive_number(
            send_timeout,
            label="Waterfall send timeout",
        )
        self.accept_poll_interval = _require_positive_number(
            accept_poll_interval,
            label="Waterfall accept poll interval",
        )
        self.shutdown_timeout = _require_positive_number(
            shutdown_timeout,
            label="Waterfall shutdown timeout",
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
        self._records_sent = 0
        self._last_error: str | None = None

    def snapshot(self) -> DaemonWaterfallServerSnapshot:
        with self._state_lock:
            return DaemonWaterfallServerSnapshot(
                active=self._active,
                connected_clients=len(self._clients),
                max_clients=self.max_clients,
                max_record_bytes=self.max_record_bytes,
                accepted_clients=self._accepted_clients,
                rejected_clients=self._rejected_clients,
                records_sent=self._records_sent,
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError(
                    "Daemon waterfall servers cannot be restarted after shutdown."
                )
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
                    name="daemon-waterfall-accept",
                    daemon=True,
                )
                self._accept_thread = thread
                with self._state_lock:
                    self._active = True
                thread.start()
            except BaseException as error:
                self._stop_event.set()
                self._stopped = True
                self._record_error(error)
                with self._state_lock:
                    self._active = False
                with suppress(BaseException):
                    self.listener.stop()
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
                _close_client(client)

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
            alive = tuple(thread.name for thread in threads if thread.is_alive())
            with self._state_lock:
                self._active = False
            self._stopped = True

            if failures:
                raise failures[0]
            if alive:
                raise DaemonIpcError(
                    "Daemon waterfall workers did not stop before the shutdown "
                    f"deadline: {', '.join(alive)}"
                )

    def _accept_loop(self, listener_socket: socket_module.socket) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    client, _ = listener_socket.accept()
                except TimeoutError:
                    continue
                except OSError as error:
                    if not self._stop_event.is_set():
                        self._record_error(error)
                    return
                self._admit_client(client)
        finally:
            with self._state_lock:
                self._active = False

    def _admit_client(self, client: socket_module.socket) -> None:
        try:
            client.settimeout(self.send_timeout)
        except OSError as error:
            _close_client(client)
            self._record_error(error)
            return

        start_error: BaseException | None = None
        with self._state_lock:
            if self._stop_event.is_set() or len(self._clients) >= self.max_clients:
                if not self._stop_event.is_set():
                    self._rejected_clients += 1
                worker = None
            else:
                number = self._accepted_clients + 1
                worker = threading.Thread(
                    target=self._serve_client,
                    args=(client,),
                    name=f"daemon-waterfall-client-{number}",
                    daemon=True,
                )
                self._clients[client] = worker
                try:
                    worker.start()
                except BaseException as error:
                    self._clients.pop(client, None)
                    start_error = error
                else:
                    self._accepted_clients = number

        if worker is None or start_error is not None:
            _close_client(client)
        if start_error is not None:
            self._record_error(start_error)

    def _serve_client(self, client: socket_module.socket) -> None:
        lease: WaterfallSessionLease | None = None
        unsubscribe = None
        transitions: queue.Queue[WaterfallSessionTransition] = queue.Queue()
        sequence = 0

        def send(record: DaemonWaterfallRecord) -> None:
            encoded = record.to_json_line()
            if len(encoded) > self.max_record_bytes:
                raise DaemonIpcError(
                    "Daemon waterfall record exceeds the maximum encoded size "
                    f"of {self.max_record_bytes} bytes."
                )
            client.sendall(encoded)
            with self._state_lock:
                self._records_sent += 1

        try:
            unsubscribe = self.session.on_transition(transitions.put_nowait)
            lease = self.session.subscribe()
            while True:
                with suppress(queue.Empty):
                    transitions.get_nowait()
                    continue
                break

            sequence += 1
            send(waterfall_checkpoint_record(sequence, self.session.snapshot()))

            while not self._stop_event.is_set():
                try:
                    transition = transitions.get_nowait()
                except queue.Empty:
                    transition = None
                if transition is not None:
                    sequence += 1
                    send(waterfall_transition_record(sequence, transition))
                    continue

                try:
                    delivery = lease.get(timeout=self.accept_poll_interval)
                except queue.Empty:
                    if _client_disconnected(client):
                        return
                    continue
                except WaterfallSubscriptionClosed:
                    return
                sequence += 1
                send(waterfall_delivery_record(sequence, delivery))
        except (OSError, WaterfallSubscriptionClosed):
            return
        except Exception as error:
            if not self._stop_event.is_set():
                self._record_error(error)
        finally:
            if unsubscribe is not None:
                unsubscribe()
            if lease is not None:
                try:
                    lease.close()
                except Exception as error:
                    if not self._stop_event.is_set():
                        self._record_error(error)
            with self._state_lock:
                self._clients.pop(client, None)
            _close_client(client)

    def _record_error(self, error: BaseException) -> None:
        error_type = error.__class__.__name__
        with self._state_lock:
            self._last_error = f"{error_type}: {error}"
        logger.warning(
            "daemon waterfall client failed error=%s",
            error_type,
        )


def _client_disconnected(client: socket_module.socket) -> bool:
    try:
        readable, _, _ = select.select((client,), (), (), 0.0)
    except (OSError, ValueError):
        return True
    if not readable:
        return False
    try:
        return client.recv(1, socket_module.MSG_PEEK) == b""
    except (BlockingIOError, TimeoutError):
        return False
    except OSError:
        return True


def _require_positive_integer(value: int, *, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


def _require_positive_number(value: float, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return normalized


def _close_client(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
