from __future__ import annotations

import queue
import socket as socket_module
import threading
from contextlib import suppress
from math import isfinite

from .daemon_ipc import DaemonSocketLocation
from .daemon_live_audio_server import (
    DAEMON_LIVE_AUDIO_ACCEPTED,
    DAEMON_LIVE_AUDIO_UNAVAILABLE,
)
from .exceptions import DaemonProtocolError, DaemonUnavailableError
from .home_assistant_live_audio import LiveAudioLeaseClosed

DAEMON_LIVE_AUDIO_CLIENT_DEFAULT_TIMEOUT = 5.0
DAEMON_LIVE_AUDIO_CLIENT_DEFAULT_READ_SIZE = 16 * 1024


class DaemonLiveAudioClient:
    """Expose one validated daemon MP3 Unix connection as an audio lease."""

    def __init__(
        self,
        location: DaemonSocketLocation,
        *,
        timeout: float = DAEMON_LIVE_AUDIO_CLIENT_DEFAULT_TIMEOUT,
        read_size: int = DAEMON_LIVE_AUDIO_CLIENT_DEFAULT_READ_SIZE,
    ) -> None:
        if not isinstance(location, DaemonSocketLocation):
            raise TypeError("Daemon live-audio location is invalid.")
        self.location = location
        self.timeout = _positive_number(timeout, "connect timeout")
        if type(read_size) is not int:
            raise TypeError("Daemon live-audio read size must be an integer.")
        if read_size <= 0:
            raise ValueError("Daemon live-audio read size must be greater than zero.")
        self.read_size = read_size
        self._state_lock = threading.RLock()
        self._receive_lock = threading.Lock()
        self._socket: socket_module.socket | None = None

    def connect(self) -> socket_module.socket:
        with self._state_lock:
            if self._socket is not None:
                return self._socket
            client = socket_module.socket(
                socket_module.AF_UNIX,
                socket_module.SOCK_STREAM,
            )
            client.settimeout(self.timeout)
            try:
                client.connect(str(self.location.path))
                status = client.recv(1)
                if status == DAEMON_LIVE_AUDIO_UNAVAILABLE:
                    raise DaemonUnavailableError(
                        "Daemon live-audio playback capacity is unavailable."
                    )
                if status != DAEMON_LIVE_AUDIO_ACCEPTED:
                    raise DaemonProtocolError("Daemon live-audio handshake is incompatible.")
                client.settimeout(None)
            except BaseException:
                client.close()
                raise
            self._socket = client
            return client

    def get(self, timeout: float | None = None) -> bytes:
        if timeout is not None:
            normalized_timeout = _positive_number(timeout, "read timeout")
        else:
            normalized_timeout = None
        with self._receive_lock:
            client = self.connect()
            client.settimeout(normalized_timeout)
            try:
                data = client.recv(self.read_size)
            except TimeoutError as error:
                raise queue.Empty from error
            except OSError as error:
                self.close()
                raise LiveAudioLeaseClosed("pipeline_failed") from error
            if not data:
                self.close()
                raise LiveAudioLeaseClosed("pipeline_failed")
            return data

    def close(self) -> None:
        with self._state_lock:
            client, self._socket = self._socket, None
        if client is not None:
            with suppress(OSError):
                client.shutdown(socket_module.SHUT_RDWR)
            client.close()

    def __enter__(self) -> DaemonLiveAudioClient:
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


def _positive_number(value: object, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Daemon live-audio {description} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"Daemon live-audio {description} must be finite and positive.")
    return normalized


__all__ = [
    "DAEMON_LIVE_AUDIO_CLIENT_DEFAULT_READ_SIZE",
    "DAEMON_LIVE_AUDIO_CLIENT_DEFAULT_TIMEOUT",
    "DaemonLiveAudioClient",
]
