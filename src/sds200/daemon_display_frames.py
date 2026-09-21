"""Mimic-SDS feed from the existing scanner owner's complete PSI events.

Passive by default: no commands, threads, audio lease, raw capture or watcher.
An explicitly injected quick-key cache opts into one background reader; daemon
startup does not inject it. Optional clock reads share that worker. Supplemental
values remain internal, not rendered or on wire.
Per-connection callback tickets reject queued callbacks from previous sessions.
Snapshots join one immutable accepted profile with one current observation.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import uuid4

from .daemon_display_profile import DaemonDisplayProfile
from .daemon_quick_keys import (
    DaemonQuickKeyCache,
    DaemonQuickKeyWorker,
    QuickKeySession,
    QuickKeySnapshot,
    QuickKeyWorkerStatus,
)
from .models import ScannerInfo
from .scanner_clock import ClockSnapshot
from .scanner_display_adapter import (
    DisplayObservationSession,
    ScannerDisplayAdapter,
    ScannerDisplayAdapterError,
    ScannerDisplayConflict,
    ScannerDisplayFrame,
    ScannerDisplayStyle,
)
from .scanner_display_frame import project_scanner_display_frame
from .scanner_display_supplemental import (
    SupplementalDisplayValues,
    project_supplemental_display_values,
)

DEFAULT_DISPLAY_STALE_SECONDS = 5.0
_LOGGER = logging.getLogger(__name__)
_CONFLICT_LOG_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class SupplementalDisplayFrameSet:
    """Internal point-in-time join, not a public payload or future-read authority.

    All layouts use one accepted profile, owner session, PSI sequence and cutoff.
    Supplemental replies retain independent ages; they were not acquired with
    that PSI packet. Reacquire the entire set for each subsequent presentation.
    """

    endpoint_id: str
    stream_id: str
    session_id: str
    sequence: int
    profile_invalidation: int
    captured_at: float
    preferred: ScannerDisplayFrame
    simple: ScannerDisplayFrame
    detail: ScannerDisplayFrame
    supplemental: SupplementalDisplayValues
    context_revision: int = 0


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
        quick_keys: DaemonQuickKeyCache | None = None,
    ) -> None:
        if not isinstance(profile, DaemonDisplayProfile) or not callable(clock):
            raise ValueError("An explicit daemon profile owner and clock are required.")
        cached, _, _, self._profile_invalidation = profile.frame_context()
        self._profile, self._scanner, self._clock = profile, scanner, clock
        self._endpoint_id = cached.endpoint_id
        self._adapter = ScannerDisplayAdapter(self._endpoint_id, stale_after=stale_after)
        if quick_keys is not None:
            if not isinstance(quick_keys, DaemonQuickKeyCache):
                raise ValueError("An explicit owner quick-key cache is required.")
            quick_keys.attach_owner(scanner, self._endpoint_id, profile.scanner_target)
        self._quick_keys = quick_keys
        self._quick_key_session: QuickKeySession | None = None
        self._quick_key_worker = (
            None if quick_keys is None else DaemonQuickKeyWorker(quick_keys, self._allow_quick_keys)
        )
        self._stream_id = str(uuid4())
        self._lock = threading.RLock()
        self._started = self._closed = False
        self._connection_events = self._sequence = 0
        self._session: DisplayObservationSession | None = None
        self._session_id: str | None = None
        self._connection_unsubscribe: Callable[[], None] | None = None
        self._psi_unsubscribe: Callable[[], None] | None = None
        self._failure: str | None = None
        self._pending_conflict: ScannerDisplayConflict | None = None
        self._conflict_count = 0
        self._next_conflict_log_at = 0.0

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
            if self._quick_key_worker is not None:
                self._quick_key_worker.start()
        except BaseException:
            self.close()
            raise

    def _disconnect(self) -> None:
        unsubscribe, self._psi_unsubscribe = self._psi_unsubscribe, None
        try:
            if unsubscribe is not None:
                unsubscribe()
        finally:
            if self._session is not None:
                self._adapter.disconnect(self._session)
            if self._quick_keys is not None and self._quick_key_session is not None:
                self._quick_keys.disconnect(self._quick_key_session)
            self._quick_key_session = None
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
                if (
                    self._quick_keys is not None
                    and self._quick_key_worker is not None
                    and not self._quick_key_worker.status().stopped
                ):
                    try:
                        self._quick_key_session = self._quick_keys.begin_session()
                    except Exception:
                        self._quick_key_worker.fail()
                        self._quick_key_session = None
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
                self._suspend_quick_keys()
                self._failure = "endpoint_mismatch"
                return
            now = self._clock()
            self._sequence += 1
            try:
                conflict = self._adapter.observe(
                    session, info, sequence=self._sequence, received_at=now, now=now
                )
                if conflict is not None:
                    self._pending_conflict = conflict
                    self._conflict_count = min(999999, self._conflict_count + 1)
                if self._quick_keys is not None and self._quick_key_session is not None:
                    try:
                        self._quick_keys.observe(
                            self._quick_key_session,
                            self._adapter.quick_key_selection(session, now=now),
                            sequence=self._sequence,
                        )
                    except Exception:
                        assert self._quick_key_worker is not None
                        self._quick_key_worker.fail()
                        self._quick_key_session = None
                self._failure = None
            except ScannerDisplayAdapterError:
                self._adapter.clear(session)
                self._suspend_quick_keys()
                self._failure = "invalid_observation"

    def _suspend_quick_keys(self) -> None:
        if self._quick_keys is not None and self._quick_key_session is not None:
            self._quick_keys.suspend(self._quick_key_session)

    def _profile_barrier(self, failure: str | None, invalidation: int) -> bool:
        # Caller holds the feed lock, after obtaining the profile context.
        if invalidation < self._profile_invalidation:
            return False  # An older concurrent context cannot lower the barrier.
        if (
            failure is not None or invalidation != self._profile_invalidation
        ) and self._session is not None:
            self._adapter.clear(self._session)
            self._suspend_quick_keys()
        self._profile_invalidation = invalidation
        return True

    def _allow_quick_keys(self) -> bool:
        # Slow administrator reload can delay this worker, never the PSI callback
        # or a scanner control: no command/cache/feed lock held while obtaining it.
        profile, failure, _, invalidation = self._profile.frame_context()
        with self._lock:
            if self._closed or not self._started or self._quick_keys is None:
                return False
            current = self._profile_barrier(failure, invalidation)
            if not current or failure is not None or profile.last_good is None:
                self._quick_keys.clear_demand()
                return False
            if (
                self._session is None
                or self._adapter.quick_key_selection(self._session, now=self._clock()) is None
            ):
                self._suspend_quick_keys()
                return False
            return True

    def quick_key_snapshot(self) -> QuickKeySnapshot | None:
        """Internal qualification only; no demand renewal, I/O or new API keys."""
        return None if self._quick_keys is None else self._quick_keys.snapshot()

    def quick_key_worker_status(self) -> QuickKeyWorkerStatus | None:
        return None if self._quick_key_worker is None else self._quick_key_worker.status()

    def clock_snapshot(self) -> ClockSnapshot | None:
        """Internal qualification only, through the same optional owner worker."""
        return None if self._quick_keys is None else self._quick_keys.clock_snapshot()

    def supplemental_values(self) -> SupplementalDisplayValues | None:
        """Internal values-only view; do not join this with a later frame read."""
        capture = self.supplemental_frame_set()
        return None if capture is None else capture.supplemental

    def supplemental_frame_set(self) -> SupplementalDisplayFrameSet | None:
        """Internal coherent projection; no I/O, demand renewal or public change.

        Obtain the immutable profile before taking the callback lock. Under that
        lock, read the cache and then check PSI freshness at the final cutoff;
        bind all three layouts and supplemental values to that same context.
        A concurrent profile reload is observed on a subsequent read, not by
        mixing a different profile into an already captured frame set.
        """
        if self._quick_keys is None:
            return None
        profile, failure, _, invalidation = self._profile.frame_context()
        target = self._scanner.endpoint
        with self._lock:
            if not self._profile_barrier(failure, invalidation):
                return None
            if (
                self._closed
                or not self._started
                or failure is not None
                or profile.last_good is None
                or self._session is None
                or self._session_id is None
                or self._quick_key_session is None
                or self._failure is not None
            ):
                return None
            if target != self._profile.scanner_target:
                self._suspend_quick_keys()
                return None
            try:
                sample = self._quick_keys.supplemental_snapshot()
                now = self._clock()
                if self._adapter.quick_key_selection(self._session, now=now) is None:
                    self._suspend_quick_keys()
                    return None
                supplemental = project_supplemental_display_values(
                    sample,
                    session=self._quick_key_session,
                    sequence=self._sequence,
                    now=now,
                )
                return SupplementalDisplayFrameSet(
                    endpoint_id=str(self._endpoint_id),
                    stream_id=self._stream_id,
                    session_id=self._session_id,
                    sequence=self._sequence,
                    profile_invalidation=invalidation,
                    captured_at=now,
                    preferred=self._adapter.frame(profile, now=now),
                    simple=self._adapter.frame(profile, now=now, style=ScannerDisplayStyle.SIMPLE),
                    detail=self._adapter.frame(profile, now=now, style=ScannerDisplayStyle.DETAIL),
                    supplemental=supplemental,
                    context_revision=sample.context_revision,
                )
            except Exception:
                # Auxiliary failure must not hide otherwise-current PSI data.
                assert self._quick_key_worker is not None
                self._quick_key_worker.fail()
                return None

    def snapshot(self) -> dict[str, object]:
        # Administrator reload can block this API read, but never scanner callbacks.
        profile, profile_failure, source_status, invalidation = self._profile.frame_context()
        with self._lock:
            now = self._clock()
            current = self._profile_barrier(profile_failure, invalidation)
            if self._quick_keys is not None:
                if (
                    current
                    and not self._closed
                    and profile_failure is None
                    and profile.last_good is not None
                    and self._session is not None
                    and self._adapter.quick_key_selection(self._session, now=now) is not None
                ):
                    try:
                        self._quick_keys.request_refresh()
                    except Exception:
                        assert self._quick_key_worker is not None
                        self._quick_key_worker.fail()
                else:
                    self._quick_keys.clear_demand()
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
            result: dict[str, object] = {
                "schema_version": 1,
                "endpoint_id": str(self._endpoint_id),
                "stream_id": self._stream_id,
                "session_id": self._session_id,
                "failure": profile_failure or self._failure,
                "source_status": source_status,
                "frames": frames,
            }
            conflict = None
            count = 0
            if self._pending_conflict is not None and now >= self._next_conflict_log_at:
                conflict, self._pending_conflict = self._pending_conflict, None
                count, self._conflict_count = self._conflict_count, 0
                self._next_conflict_log_at = now + _CONFLICT_LOG_INTERVAL_SECONDS
        if conflict is not None:
            # On a display read, outside the feed lock, never on the PSI callback.
            # Remember the latest structure across recovery, without retaining
            # XML, attributes, source paths, endpoint addresses or names.
            # A failed diagnostic sink must not turn a valid read into 503.
            with suppress(Exception):
                _LOGGER.warning(
                    "Mimic scanner observation conflicts=%d latest_screen=%s "
                    "latest_mode=%s reasons=%s duplicate_tags=%s foreign_channel_tags=%s",
                    count,
                    conflict.screen,
                    conflict.operating_mode or "unrecognized",
                    ",".join(conflict.reasons),
                    ",".join(conflict.duplicate_tags) or "none",
                    ",".join(conflict.foreign_channel_tags) or "none",
                )
        return result

    def close(self) -> None:
        try:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                try:
                    self._disconnect()
                finally:
                    unsubscribe, self._connection_unsubscribe = self._connection_unsubscribe, None
                    if unsubscribe is not None:
                        unsubscribe()
        finally:
            # Never join under the callback lock, even if unsubscription failed:
            # an in-flight GET may deliver PSI before its transport returns.
            if self._quick_key_worker is not None:
                self._quick_key_worker.close()
