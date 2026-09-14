"""Experimental display-only sessions, not enabled by production launchers.

Tokens never grant operator access. Callers must enforce the display route allowlist,
strict origin/cookie policy and bounded authentication admission separately. Store
checks are snapshots, not a cross-process revocation barrier. A service must run
the reconciler and consume each lease's cancellation signal for its entire request.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from sds200.browser_device_store import (
    BrowserDeviceBinding,
    BrowserDeviceRecord,
    BrowserDeviceState,
    BrowserDeviceStore,
    BrowserDeviceStoreError,
)

_PREFIX = "sdsctl-browser-session-v1."
_TOKEN = re.compile(re.escape(_PREFIX) + r"[a-f0-9]{64}")
_Result = TypeVar("_Result")


class BrowserSessionEnded(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Browser-device session ended.")


class BrowserSessionUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Browser-device sessions are temporarily unavailable.")


def _digest(token: str | None) -> bytes | None:
    if type(token) is not str or _TOKEN.fullmatch(token) is None:
        return None
    return hashlib.sha256(token.encode("ascii")).digest()


@dataclass(frozen=True, slots=True)
class IssuedBrowserSession:
    token: str = field(repr=False)
    lifetime_seconds: float


@dataclass(slots=True)
class _Session:
    binding: BrowserDeviceBinding
    absolute_deadline: float
    idle_deadline: float
    watchers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Event]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class BrowserSessionLease:
    """Display-only request handle; release in finally, stop on revoked or deadline."""

    binding: BrowserDeviceBinding
    revoked: asyncio.Event = field(repr=False)
    remaining_seconds: float
    _release: Callable[[], None] = field(repr=False)
    _request_deadline: float = field(repr=False)

    def release(self) -> None:
        self._release()  # Manager removal is idempotent, including after revocation.


async def run_browser_session_request(
    lease: BrowserSessionLease, request: Callable[[], Awaitable[_Result]],
    *, release: bool = True,
) -> _Result:
    """Run one already-authorized operation until completion, expiry or revocation.

    Does not choose routes or send HTTP responses. Cancellation must be cooperative:
    this waits for the operation's cleanup and must not be treated as acknowledged
    stream termination until it actually returns. Delayed dispatch cannot extend
    the lease's original hard deadline.
    """
    async def invoke() -> _Result:
        if lease.revoked.is_set() or time.monotonic() >= lease._request_deadline:
            raise BrowserSessionEnded()
        return await request()

    operation: asyncio.Task[_Result] | None = None
    cancellation: asyncio.Task[bool] | None = None
    try:
        remaining = lease._request_deadline - time.monotonic()
        if lease.revoked.is_set() or remaining <= 0:
            raise BrowserSessionEnded()
        operation = asyncio.create_task(invoke())
        cancellation = asyncio.create_task(lease.revoked.wait())
        done, _ = await asyncio.wait(
            {operation, cancellation}, timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation in done or not done:
            raise BrowserSessionEnded()
        return await operation
    finally:
        tasks = [task for task in (operation, cancellation) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if release:
                lease.release()


class BrowserDeviceSessions:
    """Bounded process-local session registry; restart invalidates all sessions.

    Synchronous methods serialize SQLite reads with a private lock. HTTP integration
    must bound/offload blocking work; it must not put unbounded to_thread admission
    in front of authentication. No secret or session token is retained here.
    """

    def __init__(
        self, store: BrowserDeviceStore, *, absolute_seconds: float = 300,
        idle_seconds: float = 60, max_sessions: int = 64,
        max_sessions_per_device: int = 2, max_requests_per_session: int = 16,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        durations = (absolute_seconds, idle_seconds)
        limits = (max_sessions, max_sessions_per_device, max_requests_per_session)
        if (any(type(value) not in (int, float) or not math.isfinite(value)
                or not 0 < value <= 3600 for value in durations)
                or idle_seconds > absolute_seconds
                or any(type(value) is not int or not 1 <= value <= 256 for value in limits)
                or max_sessions_per_device > max_sessions):
            raise ValueError("Invalid browser-device session limits.")
        self._store = store
        self._absolute = float(absolute_seconds)
        self._idle = float(idle_seconds)
        self._max_sessions = max_sessions
        self._per_device = max_sessions_per_device
        self._per_session = max_requests_per_session
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[bytes, _Session] = {}
        # Retain completion tracking after invalidation until request cleanup finishes.
        self._outstanding: dict[int, tuple[BrowserDeviceBinding, threading.Event]] = {}
        self._watcher_id = 0
        self._closed = False
        self._monitor_started = False

    @property
    def authority_path(self) -> Path:
        return self._store.path

    def drain_closed(self, *, timeout: float = 5) -> bool:
        """Shutdown barrier before handing authority ownership to another process."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("Invalid browser-device drain deadline.")
        deadline = time.monotonic() + timeout
        with self._lock:
            if not self._closed:
                return False
            pending = [done for _, done in self._outstanding.values()]
        return all(done.wait(max(0, deadline - time.monotonic())) for done in pending)

    def _remove_locked(self, digest: bytes) -> None:
        session = self._sessions.pop(digest, None)
        if session is not None:
            for loop, event in session.watchers.values():
                with suppress(RuntimeError):  # A departed request loop may be closed.
                    loop.call_soon_threadsafe(event.set)

    def _close_locked(self) -> None:
        self._closed = True
        for digest in tuple(self._sessions):
            self._remove_locked(digest)

    def close(self) -> None:
        """Terminal close: signal all watchers and reject all subsequent use."""
        with self._lock:
            self._close_locked()

    def _reconcile_locked(self) -> bool:
        if self._closed:
            return False
        try:
            current = {
                BrowserDeviceBinding(record.device_id, record.generation)
                for record in self._store.inventory()
                if record.state is BrowserDeviceState.ACTIVE
            }
        except BrowserDeviceStoreError:
            self._close_locked()  # Never resurrect cached sessions after repair.
            return False
        now = self._clock()
        for digest, session in tuple(self._sessions.items()):
            if (session.binding not in current or now >= session.absolute_deadline
                    or (not session.watchers and now >= session.idle_deadline)):
                self._remove_locked(digest)
        return True

    def reconcile(self) -> bool:
        """Signal invalid/expired leases; True is NOT a stream-termination ACK."""
        with self._lock:
            return self._reconcile_locked()

    async def run_reconciler(self, *, interval_seconds: float = 1) -> None:
        """Service-lifetime polling; cancellation/failure closes this manager.

        Detection delay includes scheduling and authority I/O, not just interval.
        Awaiting this task's termination does not itself await request termination.
        """
        if (type(interval_seconds) not in (int, float)
                or not math.isfinite(interval_seconds) or not 0 < interval_seconds <= 5):
            raise ValueError("Invalid browser-device reconciliation interval.")
        with self._lock:
            if self._monitor_started or self._closed:
                raise RuntimeError("Browser-device reconciler is unavailable.")
            self._monitor_started = True
        try:
            while await asyncio.to_thread(self.reconcile):
                await asyncio.sleep(interval_seconds)
        finally:
            self.close()

    def issue(self, device_id: str, credential: str) -> IssuedBrowserSession | None:
        """Compatibility primitive: return None for any failed admission."""
        try:
            return self.issue_or_raise(device_id, credential)
        except BrowserSessionUnavailable:
            return None

    def issue_or_raise(
        self, device_id: str, credential: str, *, expected_generation: int | None = None,
    ) -> IssuedBrowserSession | None:
        """Return None for rejected credentials; distinguish transient unavailability."""
        if expected_generation is not None and (
            type(expected_generation) is not int or not 1 <= expected_generation < 2**53 - 1
        ):
            return None
        with self._lock:
            if not self._reconcile_locked():
                raise BrowserSessionUnavailable()
            try:
                binding = self._store.authenticate(device_id, credential)
                if (binding is None or not self._store.is_current(binding)
                        or (expected_generation is not None
                            and binding.generation != expected_generation)):
                    return None
            except BrowserDeviceStoreError:
                self._close_locked()
                raise BrowserSessionUnavailable() from None
            if (len(self._sessions) >= self._max_sessions
                    or sum(s.binding.device_id == device_id for s in self._sessions.values())
                    >= self._per_device):
                raise BrowserSessionUnavailable()  # Never evict another active display.
            token = _PREFIX + secrets.token_hex(32)
            digest = _digest(token)
            if digest is None or digest in self._sessions:
                raise BrowserSessionUnavailable()
            now = self._clock()
            self._sessions[digest] = _Session(binding, now + self._absolute, now + self._idle)
            return IssuedBrowserSession(token, self._absolute)

    def verify_for_resume(
        self, device_id: str, credential: str, *, expected_generation: int | None = None,
    ) -> BrowserDeviceRecord | None:
        """Credential-authenticated snapshot + older-request drain, never a mutation.

        Run in the existing bounded exchange worker. Release our lock while
        waiting for request cleanup; reauthenticate the SAME binding afterwards.
        A subsequent session request must separately require this generation.
        """
        if expected_generation is not None and (
            type(expected_generation) is not int or not 1 <= expected_generation < 2**53 - 1
        ):
            return None
        try:
            with self._lock:
                if not self._reconcile_locked():
                    raise BrowserSessionUnavailable()
                binding = self._store.authenticate(device_id, credential)
                if (binding is None or binding.generation >= 2**53 - 1
                        or (expected_generation is not None
                            and binding.generation != expected_generation)):
                    return None
            record = BrowserDeviceRecord(device_id, binding.generation, BrowserDeviceState.ACTIVE)
            if not self.acknowledge(record):
                raise BrowserSessionUnavailable()
            with self._lock:
                if not self._reconcile_locked():
                    raise BrowserSessionUnavailable()
                if self._store.authenticate(device_id, credential) != binding:
                    return None
            return record
        except BrowserDeviceStoreError:
            self.close()
            raise BrowserSessionUnavailable() from None

    def acquire(self, token: str | None) -> BrowserSessionLease | None:
        """Call from the request's event loop; rejected requests get no lease."""
        return self.acquire_for_loop(token, asyncio.get_running_loop())

    def acquire_for_loop(
        self, token: str | None, loop: asyncio.AbstractEventLoop,
    ) -> BrowserSessionLease | None:
        """Bounded HTTP worker entry; cancellation is delivered to the request loop."""
        digest = _digest(token)
        if digest is None:
            return None
        with self._lock:
            if not self._reconcile_locked():
                return None
            session = self._sessions.get(digest)
            if (session is None or len(session.watchers) >= self._per_session
                    or len(self._outstanding) >= self._max_sessions * self._per_session):
                return None
            self._watcher_id += 1
            watcher_id = self._watcher_id
            event = asyncio.Event()
            session.watchers[watcher_id] = (loop, event)
            self._outstanding[watcher_id] = (session.binding, threading.Event())
            now = self._clock()
            session.idle_deadline = min(session.absolute_deadline, now + self._idle)
            return BrowserSessionLease(
                session.binding, event, session.absolute_deadline - now,
                lambda: self._release(digest, watcher_id),
                time.monotonic() + session.absolute_deadline - now,
            )

    def _release(self, digest: bytes, watcher_id: int) -> None:
        with self._lock:
            outstanding = self._outstanding.pop(watcher_id, None)
            if outstanding is not None:
                outstanding[1].set()
            session = self._sessions.get(digest)
            if session is not None and session.watchers.pop(watcher_id, None) is not None:
                session.idle_deadline = min(session.absolute_deadline, self._clock() + self._idle)

    def revoke_session(self, token: str | None) -> None:
        """Revoke this short session only; NOT persistent device sign-out/pause."""
        digest = _digest(token)
        if digest is not None:
            with self._lock:
                self._remove_locked(digest)

    def pause(self, binding: BrowserDeviceBinding) -> BrowserDeviceRecord | None:
        """Persist pause and signal local sessions; NOT a completion acknowledgement."""
        with self._lock:
            if self._closed:
                raise BrowserSessionUnavailable()
            try:
                record = self._store.pause_binding(binding)
                self._reconcile_locked()
                return record
            except BrowserDeviceStoreError:
                self._close_locked()
                raise BrowserSessionUnavailable() from None

    def acknowledge(self, record: BrowserDeviceRecord, *, timeout: float = 5) -> bool:
        """Local web-owner barrier for a committed generation; run in a bounded worker.

        True means older requests in THIS manager finished and the requested record
        was still current at the final check. It is not an ACK from other workers,
        nor a durable receipt guaranteeing the administrator won't later resume.
        Caller must not include its own still-held lease in the drain set.
        """
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 30):
            raise ValueError("Invalid browser-device acknowledgement deadline.")
        deadline = time.monotonic() + timeout
        with self._lock:
            if not self._reconcile_locked():
                return False
            pending = [done for binding, done in self._outstanding.values()
                       if binding.device_id == record.device_id
                       and binding.generation < record.generation]
        for done in pending:
            if not done.wait(max(0, deadline - time.monotonic())):
                return False
        with self._lock:
            if self._closed or time.monotonic() >= deadline:
                return False
            try:
                return record in self._store.inventory()
            except BrowserDeviceStoreError:
                self._close_locked()
                return False
