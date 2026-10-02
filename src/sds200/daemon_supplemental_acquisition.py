"""One explicitly armed, bounded acquisition candidate on the existing owner.

Internal only: no startup hook, user configuration, HTTP route or auto-rearm.
Native SDS200/UDP, exact firmware pin, global FQK/DTM and no file tracing.
Ordinary frame/context delivery cannot create or renew acquisition demand.
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic

from .daemon_display_frames import DaemonDisplayFrames, SupplementalDisplayFrameSet
from .daemon_display_profile import DaemonDisplayProfile
from .daemon_quick_keys import DaemonQuickKeyCache
from .daemon_runtime import DaemonRuntime, DaemonRuntimeState
from .network import UdpTransport
from .radio import SDS200
from .trace import TrafficTrace


@dataclass(frozen=True, slots=True)
class SupplementalAcquisitionPolicy:
    expected_firmware: str
    window_seconds: float
    max_read_attempts: int

    def __post_init__(self) -> None:
        if (
            type(self.expected_firmware) is not str
            or re.fullmatch(r"[A-Za-z0-9._ -]{1,64}", self.expected_firmware) is None
            or self.expected_firmware != self.expected_firmware.strip()
            or type(self.window_seconds) not in (int, float)
            or not math.isfinite(self.window_seconds)
            or not 0 < self.window_seconds <= 75
            or type(self.max_read_attempts) is not int
            or not 1 <= self.max_read_attempts <= 150
        ):
            raise ValueError("An exact firmware pin and bounded read window/quota are required.")


@dataclass(frozen=True, slots=True)
class SupplementalAcquisitionStatus:
    armed: bool
    ended: bool
    reason: str | None
    read_attempts: int


class _Frames(DaemonDisplayFrames):
    _acquisition: DaemonSupplementalAcquisition

    def _allow_quick_keys(self) -> bool:
        return self._acquisition._allow() and super()._allow_quick_keys()


class DaemonSupplementalAcquisition:
    """Build/start/arm/renew/close are separate, explicit internal operations.

    This creates the *only* display feed for the chosen runtime. Callers must not
    also install an ordinary feed/cache. No second scanner or scheduler is made.
    It never starts/stops the runtime, alters scanner modes or reads identity.
    Cached runtime identity must already match, and is rechecked per attempt.
    """

    def __init__(
        self,
        runtime: DaemonRuntime,
        profile: DaemonDisplayProfile,
        policy: SupplementalAcquisitionPolicy,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            os.name != "posix"
            or type(runtime) is not DaemonRuntime
            or type(profile) is not DaemonDisplayProfile
            or type(policy) is not SupplementalAcquisitionPolicy
            or type(runtime.scanner) is not SDS200
            or type(runtime.scanner.transport) is not UdpTransport
            or not callable(clock)
        ):
            raise ValueError("The exact native runtime/scanner/UDP owner is required.")
        self._runtime, self._scanner, self._transport = (
            runtime,
            runtime.scanner,
            runtime.scanner.transport,
        )
        self._policy, self._clock = policy, clock
        self._lock = threading.Lock()
        self._started = self._attempted = self._armed = self._ended = False
        self._reason: str | None = None
        self._deadline = self._latest_time = 0.0
        self._read_attempts = 0
        self._unsubscribe: Callable[[], None] | None = None
        accepted, _, _, _ = profile.frame_context()
        self._cache = DaemonQuickKeyCache(
            self._scanner,
            accepted.endpoint_id,
            profile.scanner_target,
            clock=clock,
            include_clock=True,
            allow_scoped_reads=False,
            bounded_writes=True,
            read_scope=self._scope,
        )
        self.frames = _Frames(
            profile,
            self._scanner,
            clock=clock,
            quick_keys=self._cache,
            legacy_snapshot_demand=False,
        )
        self.frames._acquisition = self
        with runtime._state_lock:
            available = not runtime._supplemental_acquisition_used
            if available:
                runtime._supplemental_acquisition_used = True
                runtime._supplemental_acquisition = self
        if not available:
            self.frames.close()
            raise ValueError("This runtime already had a supplemental acquisition owner.")

    def _now(self) -> float:
        value = self._clock()
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not self._latest_time <= value <= 1e15
        ):
            raise ValueError("A bounded monotonic acquisition clock is required.")
        self._latest_time = float(value)
        return self._latest_time

    def _end(self, reason: str) -> None:
        with self._lock:
            if self._ended:
                return
            self._ended, self._reason = True, reason
        # Never enter feed/cache locks with the acquisition lock held.
        self._cache.clear_demand()

    def _qualified(self) -> bool | None:
        runtime, scanner = self._runtime, self._scanner
        if (
            runtime.scanner is not scanner
            or scanner.transport is not self._transport
            or type(scanner.trace) is not TrafficTrace
            or scanner.trace.path is not None
        ):
            return False
        if not runtime._state_lock.acquire(blocking=False):
            return None  # Transient contention is not lost qualification.
        try:
            return (
                runtime._state is DaemonRuntimeState.RUNNING
                and runtime._scanner_model == "SDS200"
                and runtime._scanner_firmware == self._policy.expected_firmware
                and runtime._system_status_research is None
                and runtime._display_read_research is None
                and runtime._front_panel_research is None
            )
        finally:
            runtime._state_lock.release()

    def start(self) -> None:
        with self._lock:
            if self._ended:
                raise ValueError("Ended acquisition cannot be started again.")
            if self._started:
                return
            self._started = True
        try:
            self._unsubscribe = self._scanner.on_connection(self._connection)
            self.frames.start()
        except BaseException:
            self.close()
            raise

    def _connection(self, connected: bool) -> None:
        with self._lock:
            attempted = self._attempted
        if attempted and connected is not True:
            self._end("connection_ended")

    def arm(self) -> bool:
        with self._lock:
            if self._attempted or self._ended or not self._started:
                return False
            self._attempted = True
        # Runtime reservation is acquired without any feed/cache/window lock.
        with self._runtime._supplemental_read_scope(self._scanner) as available:
            if not available or not self._qualified():
                self._end("preflight_refused")
                return False
            with self._lock:
                if self._ended:
                    return False
                self._deadline = self._now() + self._policy.window_seconds
                self._armed = True
        return True

    def _allow(self) -> bool:
        with self._lock:
            if not self._armed or self._ended:
                return False
            reason = (
                "window_expired"
                if self._now() >= self._deadline
                else "quota_exhausted"
                if self._read_attempts >= self._policy.max_read_attempts
                else None
            )
        if reason is not None:
            self._end(reason)
            return False
        return True

    @contextmanager
    def _scope(self, scanner: object) -> Iterator[bool]:
        if scanner is not self._scanner or not self._allow():
            yield False
            return
        with self._runtime._supplemental_read_scope(scanner) as available:
            if not available:
                yield False
                return
            qualified = self._qualified()
            if qualified is None:
                yield False
                return
            if not qualified:
                self._end("qualification_lost")
                yield False
                return
            if not self._allow():
                yield False
                return
            with self._lock:
                allowed = not self._ended
                if allowed:
                    self._read_attempts += 1
            # An opportunity counts conservatively even if the native lane
            # refuses a write. No unbounded retry of unavailable opportunities.
            try:
                yield allowed
            finally:
                with self._lock:
                    expired = self._now() >= self._deadline
                if expired:
                    self._end("window_expired")

    def renew(self, capture: SupplementalDisplayFrameSet) -> int | None:
        if not self._allow():
            return None
        result = self.frames.renew_supplemental_demand(capture)
        # Expiry/close may win while profile access is blocked. Retire any lease
        # just created by the losing renewal; never reopen the window.
        if not self._allow():
            self._cache.clear_demand()
            return None
        return result

    def status(self) -> SupplementalAcquisitionStatus:
        self._allow()
        with self._lock:
            return SupplementalAcquisitionStatus(
                self._armed, self._ended, self._reason, self._read_attempts
            )

    def close(self) -> None:
        self._end("closed")
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        try:
            if unsubscribe is not None:
                unsubscribe()
        finally:
            self.frames.close()
            with self._runtime._state_lock:
                if self._runtime._supplemental_acquisition is self:
                    self._runtime._supplemental_acquisition = None
