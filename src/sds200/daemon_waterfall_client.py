from __future__ import annotations

import json
import socket as socket_module
import threading
from collections.abc import Iterator, Mapping
from contextlib import suppress
from datetime import datetime
from math import isfinite
from time import sleep
from typing import cast

from .daemon_ipc import DaemonSocketLocation
from .daemon_remote_reconnect import (
    DaemonRemoteReconnectPolicy,
    daemon_remote_error_is_reconnectable,
)
from .daemon_transport import DaemonClientTransport, UnixDaemonClientTransport
from .daemon_waterfall_protocol import (
    DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES,
    DAEMON_WATERFALL_PROTOCOL,
    DAEMON_WATERFALL_SUPPORTED_VERSIONS,
    DaemonWaterfallRecord,
    DaemonWaterfallRecordKind,
)
from .exceptions import (
    DaemonDisconnectedError,
    DaemonProtocolError,
    DaemonUnavailableError,
)

DAEMON_WATERFALL_CLIENT_DEFAULT_TIMEOUT = 5.0


class DaemonWaterfallClient:
    """Receive and validate one daemon-local waterfall JSON Lines stream."""

    def __init__(
        self,
        location: DaemonSocketLocation | DaemonClientTransport,
        *,
        timeout: float = DAEMON_WATERFALL_CLIENT_DEFAULT_TIMEOUT,
        max_record_bytes: int = DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES,
    ) -> None:
        if isinstance(location, DaemonSocketLocation):
            resolved_location: DaemonSocketLocation | None = location
            transport: DaemonClientTransport = UnixDaemonClientTransport(
                location,
                service_label="Daemon waterfall",
            )
        elif isinstance(location, DaemonClientTransport):
            resolved_location = (
                location.location
                if isinstance(location, UnixDaemonClientTransport)
                else None
            )
            transport = location
        else:
            raise TypeError(
                "Daemon waterfall client endpoint must be a DaemonSocketLocation "
                "or DaemonClientTransport."
            )
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("Waterfall connect timeout must be a number.")
        normalized_timeout = float(timeout)
        if not isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError(
                "Waterfall connect timeout must be finite and greater than zero."
            )
        if type(max_record_bytes) is not int:
            raise TypeError("Maximum waterfall record size must be an integer.")
        if max_record_bytes <= 0:
            raise ValueError(
                "Maximum waterfall record size must be greater than zero."
            )
        self.location = resolved_location
        self.transport = transport
        self.sanitizes_private_state = (
            getattr(transport, "sanitizes_private_state", None) is True
        )
        self.timeout = normalized_timeout
        self.max_record_bytes = max_record_bytes
        self._lifecycle_lock = threading.RLock()
        self._receive_lock = threading.Lock()
        self._socket: socket_module.socket | None = None
        self._buffer = bytearray()
        self._last_sequence: int | None = None

    @property
    def connected(self) -> bool:
        with self._lifecycle_lock:
            return self._socket is not None

    @property
    def last_sequence(self) -> int | None:
        with self._lifecycle_lock:
            return self._last_sequence

    def connect(self) -> socket_module.socket:
        with self._lifecycle_lock:
            if self._socket is not None:
                return self._socket
            client: socket_module.socket | None = None
            try:
                client = self.transport.connect(timeout=self.timeout)
                client.settimeout(None)
            except DaemonUnavailableError:
                raise
            except OSError as error:
                if client is not None:
                    _close_socket(client)
                raise DaemonUnavailableError(
                    "Could not establish daemon waterfall client transport."
                ) from error
            assert client is not None
            self._socket = client
            self._buffer.clear()
            self._last_sequence = None
            return client

    def close(self) -> None:
        with self._lifecycle_lock:
            client = self._socket
            self._socket = None
            self._buffer.clear()
            self._last_sequence = None
        if client is not None:
            _close_socket(client)

    def __enter__(self) -> DaemonWaterfallClient:
        self.connect()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def receive(self) -> DaemonWaterfallRecord:
        with self._receive_lock:
            client = self.connect()
            try:
                record = _decode_record(self._read_line(client))
                self._validate_order(record)
            except (DaemonDisconnectedError, DaemonProtocolError):
                self._discard_socket(client)
                raise
            return record

    def watch(
        self,
        *,
        count: int | None = None,
        reconnect_policy: DaemonRemoteReconnectPolicy | None = None,
        stop_event: threading.Event | None = None,
    ) -> Iterator[DaemonWaterfallRecord]:
        if count is not None:
            if type(count) is not int:
                raise TypeError("Waterfall record count must be an integer or None.")
            if count <= 0:
                raise ValueError("Waterfall record count must be greater than zero.")
        if reconnect_policy is not None and not isinstance(
            reconnect_policy,
            DaemonRemoteReconnectPolicy,
        ):
            raise TypeError(
                "Waterfall reconnect policy must be "
                "DaemonRemoteReconnectPolicy or None."
            )
        if reconnect_policy is not None and not self.sanitizes_private_state:
            raise ValueError(
                "Waterfall reconnect policy is available only for "
                "authenticated remote transports."
            )
        if stop_event is not None and not isinstance(stop_event, threading.Event):
            raise TypeError("Waterfall stop event must be threading.Event or None.")
        emitted = 0
        reconnect_attempt = 0
        while count is None or emitted < count:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                record = self.receive()
            except Exception as error:
                if (
                    reconnect_policy is None
                    or reconnect_attempt >= reconnect_policy.attempts
                    or not daemon_remote_error_is_reconnectable(error)
                ):
                    raise
                reconnect_attempt += 1
                delay = reconnect_policy.delay(reconnect_attempt)
                if stop_event is not None:
                    if stop_event.wait(delay):
                        return
                else:
                    sleep(delay)
                continue
            reconnect_attempt = 0
            yield record
            emitted += 1

    def _read_line(self, expected: socket_module.socket) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                size = newline + 1
                if size > self.max_record_bytes:
                    raise DaemonProtocolError(
                        "The daemon waterfall record exceeds the maximum accepted "
                        f"size of {self.max_record_bytes} bytes."
                    )
                frame = bytes(self._buffer[:newline])
                del self._buffer[:size]
                return frame
            if len(self._buffer) >= self.max_record_bytes:
                raise DaemonProtocolError(
                    "The daemon waterfall record exceeds the maximum accepted "
                    f"size of {self.max_record_bytes} bytes."
                )
            try:
                chunk = expected.recv(
                    min(65536, self.max_record_bytes - len(self._buffer))
                )
            except OSError as error:
                raise DaemonDisconnectedError(
                    "The daemon waterfall stream disconnected while receiving data."
                ) from error
            if not chunk:
                if self._buffer:
                    raise DaemonProtocolError(
                        "The daemon waterfall stream closed with an incomplete record."
                    )
                raise DaemonDisconnectedError(
                    "The daemon waterfall stream disconnected."
                )
            self._buffer.extend(chunk)

    def _validate_order(self, record: DaemonWaterfallRecord) -> None:
        with self._lifecycle_lock:
            previous = self._last_sequence
            if previous is None:
                if record.kind is not DaemonWaterfallRecordKind.SESSION_CHECKPOINT:
                    raise DaemonProtocolError(
                        "The waterfall stream did not begin with a session checkpoint."
                    )
                self._last_sequence = record.sequence
                return
            if record.kind is DaemonWaterfallRecordKind.SESSION_CHECKPOINT:
                raise DaemonProtocolError(
                    "The waterfall stream emitted an unexpected later checkpoint."
                )
            expected = previous + 1
            if record.sequence != expected:
                raise DaemonProtocolError(
                    "The waterfall record sequence is not contiguous: "
                    f"expected={expected}, received={record.sequence}."
                )
            self._last_sequence = record.sequence

    def _discard_socket(self, expected: socket_module.socket) -> None:
        with self._lifecycle_lock:
            if self._socket is expected:
                self._socket = None
                self._buffer.clear()
                self._last_sequence = None
        _close_socket(expected)

def _decode_record(frame: bytes) -> DaemonWaterfallRecord:
    try:
        decoded: object = json.loads(frame.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DaemonProtocolError(
            "The daemon returned an invalid UTF-8 JSON waterfall record."
        ) from error
    if not isinstance(decoded, Mapping):
        raise DaemonProtocolError("The waterfall record must be a JSON object.")
    required = {
        "protocol",
        "version",
        "sequence",
        "observed_at",
        "kind",
        "payload",
    }
    missing = sorted(required - set(decoded))
    unexpected = sorted(set(decoded) - required)
    if missing or unexpected:
        raise DaemonProtocolError(
            "The waterfall record fields are invalid; "
            f"missing={missing!r}, unexpected={unexpected!r}."
        )
    if decoded["protocol"] != DAEMON_WATERFALL_PROTOCOL:
        raise DaemonProtocolError(
            f"Unsupported waterfall protocol: {decoded['protocol']!r}."
        )
    if decoded["version"] not in DAEMON_WATERFALL_SUPPORTED_VERSIONS:
        raise DaemonProtocolError(
            f"Unsupported waterfall version: {decoded['version']!r}."
        )
    try:
        observed_at = datetime.fromisoformat(cast(str, decoded["observed_at"]))
        kind = DaemonWaterfallRecordKind(cast(str, decoded["kind"]))
        return DaemonWaterfallRecord(
            protocol=cast(str, decoded["protocol"]),
            version=cast(int, decoded["version"]),
            sequence=cast(int, decoded["sequence"]),
            observed_at=observed_at,
            kind=kind,
            payload=cast(Mapping[str, object], decoded["payload"]),
        )
    except (TypeError, ValueError) as error:
        raise DaemonProtocolError(
            f"Invalid daemon waterfall record: {error}"
        ) from error


def _close_socket(client: socket_module.socket) -> None:
    with suppress(OSError):
        client.shutdown(socket_module.SHUT_RDWR)
    with suppress(OSError):
        client.close()
