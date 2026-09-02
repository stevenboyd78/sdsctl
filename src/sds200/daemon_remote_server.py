"""Bounded exact-address listener for authenticated remote daemon API peers.

The listener is constructible only from an enabled, preflighted remote
configuration.  It binds one configured private address, isolates TLS
admission in bounded workers, and delivers only authenticated streams with a
transport-owned authorization context.
"""

from __future__ import annotations

import errno
import socket as socket_module
import threading
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address, ip_address
from math import isfinite
from queue import Empty, Full, Queue
from time import monotonic

from .daemon_api import (
    DAEMON_API_CONTROL_OPERATIONS,
    DaemonApiErrorCode,
    DaemonApiOperation,
    DaemonApiResponse,
)
from .daemon_remote import (
    DaemonRemoteAuthorizationScope,
    DaemonRemoteListenerConfiguration,
)
from .daemon_remote_credentials import (
    DaemonRemoteCredentialLifecycleSnapshot,
    DaemonRemoteCredentialSessionExpired,
)
from .daemon_remote_tls import (
    DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT,
    DaemonRemoteAuthenticatedPeer,
    DaemonRemoteServerTlsAdmission,
    DaemonRemoteTlsError,
)

DAEMON_REMOTE_LISTENER_DEFAULT_BACKLOG = 16
DAEMON_REMOTE_LISTENER_DEFAULT_MAX_PENDING_ADMISSIONS = 8
DAEMON_REMOTE_LISTENER_DEFAULT_MAX_READY_CLIENTS = 8
DAEMON_REMOTE_LISTENER_DEFAULT_ACCEPT_POLL_INTERVAL = 0.1
DAEMON_REMOTE_LISTENER_DEFAULT_SHUTDOWN_TIMEOUT = 6.0

DAEMON_REMOTE_OBSERVE_OPERATIONS = (
    DaemonApiOperation.HELLO,
    DaemonApiOperation.CAPABILITIES,
    DaemonApiOperation.PING,
    DaemonApiOperation.RUNTIME_SNAPSHOT,
    DaemonApiOperation.SCANNER_STATE,
    DaemonApiOperation.AUDIO_HEALTH,
)
DAEMON_REMOTE_CONTROL_OPERATIONS = DAEMON_API_CONTROL_OPERATIONS
DAEMON_REMOTE_REDACTED_RESULT_FIELDS = ("endpoint", "scanner_endpoint")

_STOPPED_DELIVERY = object()


class DaemonRemoteListenerErrorReason(StrEnum):
    """Stable redacted listener failure classes."""

    START_FAILED = "start_failed"
    SHUTDOWN_FAILED = "shutdown_failed"


_LISTENER_ERROR_MESSAGES = {
    DaemonRemoteListenerErrorReason.START_FAILED: (
        "Remote daemon listener could not start."
    ),
    DaemonRemoteListenerErrorReason.SHUTDOWN_FAILED: (
        "Remote daemon listener could not stop cleanly."
    ),
}


class DaemonRemoteListenerError(RuntimeError):
    """Report listener lifecycle failure without the private endpoint."""

    def __init__(self, reason: DaemonRemoteListenerErrorReason) -> None:
        if not isinstance(reason, DaemonRemoteListenerErrorReason):
            raise TypeError(
                "Remote daemon listener error reason must be "
                "DaemonRemoteListenerErrorReason."
            )
        self.reason = reason
        super().__init__(_LISTENER_ERROR_MESSAGES[reason])


@dataclass(frozen=True, slots=True, repr=False)
class DaemonRemoteApiPeer:
    """Authenticated peer that enforces its scopes before API dispatch."""

    authenticated: DaemonRemoteAuthenticatedPeer

    def __post_init__(self) -> None:
        if not isinstance(self.authenticated, DaemonRemoteAuthenticatedPeer):
            raise TypeError(
                "Remote daemon API peer requires an authenticated TLS peer."
            )

    @property
    def client_id(self) -> str:
        return self.authenticated.client_id

    @property
    def scopes(self) -> tuple[DaemonRemoteAuthorizationScope, ...]:
        return self.authenticated.scopes

    @property
    def allowed_operations(self) -> tuple[DaemonApiOperation, ...]:
        operations = list(DAEMON_REMOTE_OBSERVE_OPERATIONS)
        if self.authenticated.allows(DaemonRemoteAuthorizationScope.CONTROL):
            operations.extend(DAEMON_REMOTE_CONTROL_OPERATIONS)
        return tuple(operations)

    def handle_daemon_api_json_line(
        self,
        api: object,
        data: bytes | str,
    ) -> bytes:
        """Use only the API's fail-closed authorized dispatch entry point."""

        def dispatch() -> bytes:
            handler = getattr(api, "handle_authorized_json_line", None)
            if not callable(handler):
                return DaemonApiResponse.failure(
                    None,
                    DaemonApiErrorCode.INTERNAL_ERROR,
                    "The daemon authorization boundary is unavailable.",
                ).to_json_line()
            response = handler(
                data,
                allowed_operations=self.allowed_operations,
                redacted_result_fields=DAEMON_REMOTE_REDACTED_RESULT_FIELDS,
            )
            if not isinstance(response, bytes):
                return DaemonApiResponse.failure(
                    None,
                    DaemonApiErrorCode.INTERNAL_ERROR,
                    "The daemon authorization boundary is unavailable.",
                ).to_json_line()
            return response

        try:
            return self.authenticated.execute_if_credentials_current(dispatch)
        except DaemonRemoteCredentialSessionExpired:
            return DaemonApiResponse.failure(
                None,
                DaemonApiErrorCode.AUTHENTICATION_EXPIRED,
                "The remote daemon authentication session is no longer current.",
            ).to_json_line()

    def daemon_api_connection_current(self) -> bool:
        return self.authenticated.credentials_current

    def close_daemon_api_peer_context(self) -> None:
        self.authenticated.close()


@dataclass(frozen=True, slots=True)
class _ReadyClient:
    stream: socket_module.socket
    peer: DaemonRemoteApiPeer


@dataclass(frozen=True, slots=True)
class DaemonRemoteTcpListenerSnapshot:
    """Redacted bounded listener activity and capacity state."""

    active: bool
    address_family: str
    port: int
    backlog: int
    max_pending_admissions: int
    max_ready_clients: int
    pending_admissions: int
    ready_clients: int
    accepted_connections: int
    admitted_clients: int
    rejected_connections: int
    failed_admissions: int
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "address_family": self.address_family,
            "port": self.port,
            "backlog": self.backlog,
            "max_pending_admissions": self.max_pending_admissions,
            "max_ready_clients": self.max_ready_clients,
            "pending_admissions": self.pending_admissions,
            "ready_clients": self.ready_clients,
            "accepted_connections": self.accepted_connections,
            "admitted_clients": self.admitted_clients,
            "rejected_connections": self.rejected_connections,
            "failed_admissions": self.failed_admissions,
            "last_error": self.last_error,
        }


class DaemonRemoteTcpListener:
    """Own one exact-address TCP socket and bounded TLS admission pipeline."""

    def __init__(
        self,
        configuration: DaemonRemoteListenerConfiguration,
        *,
        backlog: int = DAEMON_REMOTE_LISTENER_DEFAULT_BACKLOG,
        max_pending_admissions: int = (
            DAEMON_REMOTE_LISTENER_DEFAULT_MAX_PENDING_ADMISSIONS
        ),
        max_ready_clients: int = DAEMON_REMOTE_LISTENER_DEFAULT_MAX_READY_CLIENTS,
        accept_poll_interval: float = (
            DAEMON_REMOTE_LISTENER_DEFAULT_ACCEPT_POLL_INTERVAL
        ),
        handshake_timeout: float = DAEMON_REMOTE_TLS_DEFAULT_HANDSHAKE_TIMEOUT,
        shutdown_timeout: float = DAEMON_REMOTE_LISTENER_DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        if not isinstance(configuration, DaemonRemoteListenerConfiguration):
            raise TypeError(
                "Remote daemon TCP listener requires "
                "DaemonRemoteListenerConfiguration."
            )
        if not configuration.enabled:
            raise ValueError(
                "Remote daemon TCP listener requires an enabled configuration."
            )
        self.backlog = _positive_integer(backlog, label="Remote daemon TCP backlog")
        self.max_pending_admissions = _positive_integer(
            max_pending_admissions,
            label="Maximum pending remote daemon admissions",
        )
        self.max_ready_clients = _positive_integer(
            max_ready_clients,
            label="Maximum ready remote daemon clients",
        )
        self.accept_poll_interval = _positive_number(
            accept_poll_interval,
            label="Remote daemon TCP accept poll interval",
        )
        self.shutdown_timeout = _positive_number(
            shutdown_timeout,
            label="Remote daemon TCP shutdown timeout",
        )
        self.admission = DaemonRemoteServerTlsAdmission.from_configuration(
            configuration,
            handshake_timeout=handshake_timeout,
        )
        if self.shutdown_timeout <= self.admission.handshake_timeout:
            raise ValueError(
                "Remote daemon TCP shutdown timeout must be greater than the "
                "TLS handshake timeout."
            )

        assert configuration.bind_address is not None
        assert configuration.port is not None
        self._endpoint = _listener_endpoint(
            configuration.bind_address,
            configuration.port,
        )
        self._address_family = (
            "ipv4" if self._endpoint[0] == socket_module.AF_INET else "ipv6"
        )
        self._port = configuration.port
        self._lock = threading.RLock()
        self._listener: socket_module.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._pending: dict[socket_module.socket, threading.Thread] = {}
        self._ready: Queue[_ReadyClient | object] = Queue(
            maxsize=self.max_ready_clients
        )
        self._delivery_timeout: float | None = None
        self._started = False
        self._stopped = False
        self._active = False
        self._accept_failed = False
        self._accepted_connections = 0
        self._admitted_clients = 0
        self._rejected_connections = 0
        self._failed_admissions = 0
        self._ready_clients = 0
        self._last_error: str | None = None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def snapshot(self) -> DaemonRemoteTcpListenerSnapshot:
        with self._lock:
            return DaemonRemoteTcpListenerSnapshot(
                active=self._active,
                address_family=self._address_family,
                port=self._port,
                backlog=self.backlog,
                max_pending_admissions=self.max_pending_admissions,
                max_ready_clients=self.max_ready_clients,
                pending_admissions=len(self._pending),
                ready_clients=self._ready_clients,
                accepted_connections=self._accepted_connections,
                admitted_clients=self._admitted_clients,
                rejected_connections=self._rejected_connections,
                failed_admissions=self._failed_admissions,
                last_error=self._last_error,
            )

    def credential_snapshot(
        self,
    ) -> DaemonRemoteCredentialLifecycleSnapshot | None:
        return self.admission.credential_snapshot()

    def reload_credentials(
        self,
        configuration: DaemonRemoteListenerConfiguration,
    ) -> DaemonRemoteCredentialLifecycleSnapshot:
        """Atomically replace credentials and discard invalid queued peers."""

        snapshot = self.admission.reload_credentials(configuration)
        self._discard_invalid_ready_clients()
        return snapshot

    def start(self) -> DaemonRemoteTcpListener:
        with self._lock:
            if self._listener is not None:
                return self
            if self._stopped:
                raise RuntimeError(
                    "Remote daemon TCP listeners cannot be restarted after "
                    "shutdown."
                )

            family, address = self._endpoint
            listener: socket_module.socket | None = None
            try:
                listener = socket_module.socket(family, socket_module.SOCK_STREAM)
                if family == socket_module.AF_INET6:
                    listener.setsockopt(
                        socket_module.IPPROTO_IPV6,
                        socket_module.IPV6_V6ONLY,
                        1,
                    )
                listener.bind(address)
                _verify_bound_endpoint(listener, self._endpoint)
                listener.listen(self.backlog)
                listener.settimeout(self.accept_poll_interval)
                accept_thread = threading.Thread(
                    target=self._accept_loop,
                    args=(listener,),
                    name="daemon-remote-tcp-accept",
                    daemon=True,
                )
                self._listener = listener
                self._accept_thread = accept_thread
                self._started = True
                self._active = True
                accept_thread.start()
            except BaseException as error:
                self._listener = None
                self._accept_thread = None
                self._started = True
                self._stopped = True
                self._active = False
                if listener is not None:
                    _close_stream(listener)
                raise DaemonRemoteListenerError(
                    DaemonRemoteListenerErrorReason.START_FAILED
                ) from error
            return self

    def settimeout(self, value: float | None) -> None:
        if value is not None:
            value = _positive_number(
                value,
                label="Remote daemon authenticated-client accept timeout",
            )
        with self._lock:
            self._delivery_timeout = value

    def accept(self) -> tuple[socket_module.socket, object]:
        with self._lock:
            if not self._started:
                raise RuntimeError("Remote daemon TCP listener is not active.")
            if (self._stopped or self._accept_failed) and self._ready.empty():
                raise OSError(errno.EBADF, "Remote daemon TCP listener is closed.")
            timeout = self._delivery_timeout
        deadline = None if timeout is None else monotonic() + timeout

        while True:
            with self._lock:
                if (self._stopped or self._accept_failed) and self._ready.empty():
                    raise OSError(
                        errno.EBADF,
                        "Remote daemon TCP listener is closed.",
                    )
            remaining = None if deadline is None else deadline - monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError
            try:
                delivery = self._ready.get(timeout=remaining)
            except Empty as error:
                with self._lock:
                    unavailable = self._stopped or self._accept_failed
                if unavailable:
                    raise OSError(
                        errno.EBADF,
                        "Remote daemon TCP listener is closed.",
                    ) from error
                raise TimeoutError from error

            if not isinstance(delivery, _ReadyClient):
                raise OSError(errno.EBADF, "Remote daemon TCP listener is closed.")
            with self._lock:
                stopped = self._stopped
                self._ready_clients -= 1
            if stopped:
                _close_stream(delivery.stream)
                delivery.peer.close_daemon_api_peer_context()
                raise OSError(errno.EBADF, "Remote daemon TCP listener is closed.")
            if not delivery.peer.daemon_api_connection_current():
                _close_stream(delivery.stream)
                delivery.peer.close_daemon_api_peer_context()
                continue
            return delivery.stream, delivery.peer

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._active = False
            listener = self._listener
            self._listener = None
            accept_thread = self._accept_thread
            pending_streams = tuple(self._pending)
            workers = tuple(self._pending.values())

        if listener is not None:
            _close_stream(listener)
        for stream in pending_streams:
            _close_stream(stream)

        ready_streams = self._drain_ready_clients()
        for stream in ready_streams:
            _close_stream(stream)
        with suppress(Full):
            self._ready.put_nowait(_STOPPED_DELIVERY)

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
            raise DaemonRemoteListenerError(
                DaemonRemoteListenerErrorReason.SHUTDOWN_FAILED
            )

    def __enter__(self) -> DaemonRemoteTcpListener:
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

    def _accept_loop(self, listener: socket_module.socket) -> None:
        failed = False
        try:
            while True:
                with self._lock:
                    if self._stopped:
                        return
                try:
                    stream, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    with self._lock:
                        if self._stopped:
                            return
                    failed = True
                    return
                self._start_admission(stream)
        finally:
            with self._lock:
                self._active = False
                if failed:
                    self._accept_failed = True
                    self._last_error = "accept_failed"

    def _start_admission(self, stream: socket_module.socket) -> None:
        start_error: BaseException | None = None
        with self._lock:
            self._accepted_connections += 1
            if self._stopped or len(self._pending) >= self.max_pending_admissions:
                worker = None
                self._rejected_connections += 1
            else:
                sequence = self._accepted_connections
                worker = threading.Thread(
                    target=self._admit,
                    args=(stream,),
                    name=f"daemon-remote-tls-{sequence}",
                    daemon=True,
                )
                self._pending[stream] = worker
                try:
                    worker.start()
                except BaseException as error:
                    self._pending.pop(stream, None)
                    self._failed_admissions += 1
                    self._last_error = "worker_start_failed"
                    start_error = error

        if worker is None or start_error is not None:
            _close_stream(stream)

    def _admit(self, stream: socket_module.socket) -> None:
        try:
            secured, authenticated = self.admission.admit(stream)
        except DaemonRemoteTlsError as error:
            with self._lock:
                self._pending.pop(stream, None)
                if not self._stopped:
                    self._failed_admissions += 1
                    self._last_error = error.reason.value
            return
        except BaseException:
            _close_stream(stream)
            with self._lock:
                self._pending.pop(stream, None)
                if not self._stopped:
                    self._failed_admissions += 1
                    self._last_error = "admission_failed"
            return

        delivery = _ReadyClient(
            stream=secured,
            peer=DaemonRemoteApiPeer(authenticated),
        )
        close_secured = False
        with self._lock:
            self._pending.pop(stream, None)
            if self._stopped:
                close_secured = True
            else:
                try:
                    self._ready.put_nowait(delivery)
                except Full:
                    self._rejected_connections += 1
                    close_secured = True
                else:
                    self._admitted_clients += 1
                    self._ready_clients += 1
        if close_secured:
            _close_stream(secured)
            delivery.peer.close_daemon_api_peer_context()

    def _drain_ready_clients(self) -> tuple[socket_module.socket, ...]:
        streams: list[socket_module.socket] = []
        while True:
            try:
                delivery = self._ready.get_nowait()
            except Empty:
                return tuple(streams)
            if not isinstance(delivery, _ReadyClient):
                continue
            with self._lock:
                self._ready_clients -= 1
            delivery.peer.close_daemon_api_peer_context()
            streams.append(delivery.stream)

    def _discard_invalid_ready_clients(self) -> None:
        retained: list[_ReadyClient | object] = []
        while True:
            try:
                delivery = self._ready.get_nowait()
            except Empty:
                break
            if not isinstance(delivery, _ReadyClient):
                retained.append(delivery)
                continue
            if delivery.peer.daemon_api_connection_current():
                retained.append(delivery)
                continue
            with self._lock:
                self._ready_clients -= 1
            _close_stream(delivery.stream)
            delivery.peer.close_daemon_api_peer_context()

        for delivery in retained:
            try:
                self._ready.put_nowait(delivery)
            except Full:
                if isinstance(delivery, _ReadyClient):
                    with self._lock:
                        self._ready_clients -= 1
                        self._rejected_connections += 1
                    _close_stream(delivery.stream)
                    delivery.peer.close_daemon_api_peer_context()


def _listener_endpoint(
    bind_address: str,
    port: int,
) -> tuple[int, tuple[object, ...]]:
    parsed = ip_address(bind_address)
    if isinstance(parsed, IPv4Address):
        return socket_module.AF_INET, (str(parsed), port)
    assert isinstance(parsed, IPv6Address)
    host, _, scope = str(parsed).partition("%")
    scope_id = 0
    if scope:
        scope_id = (
            int(scope)
            if scope.isdecimal()
            else socket_module.if_nametoindex(scope)
        )
    return socket_module.AF_INET6, (host, port, 0, scope_id)


def _verify_bound_endpoint(
    listener: socket_module.socket,
    endpoint: tuple[int, tuple[object, ...]],
) -> None:
    family, expected = endpoint
    observed = listener.getsockname()
    if not isinstance(observed, tuple) or len(observed) < 2:
        raise OSError("Remote daemon TCP listener returned an invalid endpoint.")
    if ip_address(str(observed[0])).packed != ip_address(str(expected[0])).packed:
        raise OSError("Remote daemon TCP listener bound an unexpected address.")
    if observed[1] != expected[1]:
        raise OSError("Remote daemon TCP listener bound an unexpected port.")
    if family == socket_module.AF_INET6 and (
        len(observed) < 4 or observed[3] != expected[3]
    ):
        raise OSError("Remote daemon TCP listener bound an unexpected scope.")


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
    "DAEMON_REMOTE_CONTROL_OPERATIONS",
    "DAEMON_REMOTE_LISTENER_DEFAULT_ACCEPT_POLL_INTERVAL",
    "DAEMON_REMOTE_LISTENER_DEFAULT_BACKLOG",
    "DAEMON_REMOTE_LISTENER_DEFAULT_MAX_PENDING_ADMISSIONS",
    "DAEMON_REMOTE_LISTENER_DEFAULT_MAX_READY_CLIENTS",
    "DAEMON_REMOTE_LISTENER_DEFAULT_SHUTDOWN_TIMEOUT",
    "DAEMON_REMOTE_OBSERVE_OPERATIONS",
    "DAEMON_REMOTE_REDACTED_RESULT_FIELDS",
    "DaemonRemoteApiPeer",
    "DaemonRemoteListenerError",
    "DaemonRemoteListenerErrorReason",
    "DaemonRemoteTcpListener",
    "DaemonRemoteTcpListenerSnapshot",
]
