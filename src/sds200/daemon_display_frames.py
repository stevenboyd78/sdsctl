"""Passive Mimic-SDS feed from the existing scanner owner's complete PSI events.

No scanner command, poller, thread, audio lease, raw capture or file watcher.
Per-connection callback tickets reject queued callbacks from previous sessions.
Snapshots join one immutable accepted profile with one current observation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from time import monotonic
from typing import Protocol
from uuid import uuid4

from .daemon_display_profile import DaemonDisplayProfile
from .models import ScannerInfo
from .scanner_display_adapter import (
    DisplayObservationSession,
    ScannerDisplayAdapter,
    ScannerDisplayAdapterError,
    ScannerDisplayStyle,
)
from .scanner_display_frame import project_scanner_display_frame

DEFAULT_DISPLAY_STALE_SECONDS = 5.0


class _ScannerDisplaySource(Protocol):
    @property
    def endpoint(self) -> str: ...

    @property
    def connected(self) -> bool: ...

    def on_connection(self, callback: Callable[[bool], None]) -> Callable[[], None]: ...

    def on_psi(self, callback: Callable[[ScannerInfo], None]) -> Callable[[], None]: ...


class DaemonDisplayFrames:
    def __init__(
        self,
        profile: DaemonDisplayProfile,
        scanner: _ScannerDisplaySource,
        *,
        stale_after: float = DEFAULT_DISPLAY_STALE_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isinstance(profile, DaemonDisplayProfile) or not callable(clock):
            raise ValueError("An explicit daemon profile owner and clock are required.")
        cached, _, _, self._profile_invalidation = profile.frame_context()
        self._profile, self._scanner, self._clock = profile, scanner, clock
        self._endpoint_id = cached.endpoint_id
        self._adapter = ScannerDisplayAdapter(self._endpoint_id, stale_after=stale_after)
        self._stream_id = str(uuid4())
        self._lock = threading.RLock()
        self._started = self._closed = False
        self._connection_events = self._sequence = 0
        self._session: DisplayObservationSession | None = None
        self._session_id: str | None = None
        self._connection_unsubscribe: Callable[[], None] | None = None
        self._psi_unsubscribe: Callable[[], None] | None = None
        self._failure: str | None = None

    def start(self) -> None:
        try:
            with self._lock:
                if self._closed:
                    raise ValueError("The scanner display feed is closed.")
                if self._started:
                    return
                self._started = True
                self._connection_unsubscribe = self._scanner.on_connection(self._connection)
                event_count = self._connection_events
            # Do not hold our callback lock while reading a transport property.
            # A connection event arriving during this read takes precedence.
            connected = self._scanner.connected
            with self._lock:
                if not self._closed and self._connection_events == event_count:
                    self._set_connection(connected)
        except BaseException:
            self.close()
            raise

    def _disconnect(self) -> None:
        if self._psi_unsubscribe is not None:
            self._psi_unsubscribe()
            self._psi_unsubscribe = None
        if self._session is not None:
            self._adapter.disconnect(self._session)
        self._session = self._session_id = None

    def _set_connection(self, connected: bool) -> None:
        if type(connected) is not bool:
            self._disconnect()
            self._failure = "invalid_connection_state"
            return
        if not connected:
            self._disconnect()
            self._failure = None
        elif self._session is None:
            session = self._adapter.begin_session(now=self._clock())
            self._session, self._session_id = session, str(uuid4())
            try:
                self._psi_unsubscribe = self._scanner.on_psi(
                    lambda info: self._observe(session, info)
                )
                self._failure = None
            except BaseException:
                self._disconnect()
                raise

    def _connection(self, connected: bool) -> None:
        with self._lock:
            if self._closed or not self._started:
                return
            self._connection_events += 1
            self._set_connection(connected)

    def _observe(self, session: DisplayObservationSession, info: ScannerInfo) -> None:
        target = self._scanner.endpoint
        with self._lock:
            if self._closed or self._session is not session:
                return
            if target != self._profile.scanner_target:
                self._adapter.clear(session)
                self._failure = "endpoint_mismatch"
                return
            now = self._clock()
            self._sequence += 1
            try:
                self._adapter.observe(
                    session, info, sequence=self._sequence, received_at=now, now=now
                )
                self._failure = None
            except ScannerDisplayAdapterError:
                self._adapter.clear(session)
                self._failure = "invalid_observation"

    def snapshot(self) -> dict[str, object]:
        # Administrator reload can block this API read, but never scanner callbacks.
        profile, profile_failure, source_status, invalidation = self._profile.frame_context()
        with self._lock:
            now = self._clock()
            if (
                profile_failure is not None or invalidation != self._profile_invalidation
            ) and self._session is not None:
                # Do not resurrect an old observation after an administrator reload.
                self._adapter.clear(self._session)
            self._profile_invalidation = invalidation
            frames = {
                name: project_scanner_display_frame(
                    self._adapter.frame(profile, now=now, style=style)
                )
                for name, style in (
                    ("preferred", None),
                    ("simple", ScannerDisplayStyle.SIMPLE),
                    ("detail", ScannerDisplayStyle.DETAIL),
                )
            }
            return {
                "schema_version": 1,
                "endpoint_id": str(self._endpoint_id),
                "stream_id": self._stream_id,
                "session_id": self._session_id,
                "failure": profile_failure or self._failure,
                "source_status": source_status,
                "frames": frames,
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._disconnect()
            if self._connection_unsubscribe is not None:
                self._connection_unsubscribe()
                self._connection_unsubscribe = None
