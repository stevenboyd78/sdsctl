"""Opt-in owner-side supplemental cache and finite-wait background worker.

An owner supplies qualified PSI selections and connection tickets. Consumers
renew one shared demand lease; snapshots never perform I/O. A background worker
may call poll_once to perform at most one GET through the existing scanner.
Construction starts nothing; ordinary daemon startup does not opt in.
Clock reads can join the same worker, never a second scheduler/command lane.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID

from .commands import GetDepartmentQuickKeys, GetFavoritesQuickKeys, GetSystemQuickKeys
from .exceptions import CommandRejectedError, CommandTimeoutError, ProtocolError
from .models import (
    DepartmentQuickKeys,
    FavoritesQuickKeys,
    FavoritesQuickKeyState,
    ScannerDateTime,
    SystemQuickKeys,
)
from .scanner_clock import ClockReadTicket, ClockSession, ClockSnapshot, ScannerClockSamples
from .scanner_quick_keys import QuickKeySelection

Kind = Literal["favorites", "system", "department"]
Operation = Literal["favorites", "system", "department", "clock"]
Failure = Literal["rejected", "timeout", "invalid_response", "read_error"]
Read = GetFavoritesQuickKeys | GetSystemQuickKeys | GetDepartmentQuickKeys
Result = FavoritesQuickKeys | SystemQuickKeys | DepartmentQuickKeys
KINDS: tuple[Kind, ...] = ("favorites", "system", "department")
READ_TIMEOUT = 0.25
MIN_READ_GAP = 0.5
REFRESH_INTERVAL = 2.0
STALE_AFTER = 5.0
DEMAND_LIFETIME = 5.0
REJECTION_BACKOFF = 30.0


class _Scanner(Protocol):
    @property
    def endpoint(self) -> str: ...

    @property
    def connected(self) -> bool: ...

    def read_quick_keys_if_idle(self, command: Read, *, timeout: float) -> Result | None: ...


class _ClockReader(Protocol):
    def __call__(self, *, timeout: float) -> ScannerDateTime | None: ...


class SupplementalReadScope(Protocol):
    def __call__(self, scanner: object) -> AbstractContextManager[bool]: ...


@dataclass(frozen=True, slots=True, eq=False)
class QuickKeySession:
    endpoint_id: UUID


@dataclass(frozen=True, slots=True)
class QuickKeyBank:
    kind: Kind
    states: tuple[FavoritesQuickKeyState, ...] | None
    age_seconds: float | None
    failure: Failure | None


@dataclass(frozen=True, slots=True)
class QuickKeySnapshot:
    active: bool
    selection: QuickKeySelection | None
    blocked_until_reconnect: Failure | None
    banks: tuple[QuickKeyBank, ...]


@dataclass(frozen=True, slots=True)
class SupplementalSnapshot:
    """One owner/session/PSI cut, not simultaneous DTM/FQK acquisition.

    Internal only. Retain independent sample ages; never infer an LCD decade
    from the selected Favorites/System quick keys or the global state bank.
    """

    session: QuickKeySession | None
    sequence: int | None
    captured_at: float
    quick_keys: QuickKeySnapshot
    clock: ClockSnapshot | None


@dataclass(frozen=True, slots=True)
class _Cached:
    states: tuple[FavoritesQuickKeyState, ...]
    requested_at: float


class DaemonQuickKeyCache:
    def __init__(
        self,
        scanner: _Scanner,
        endpoint_id: UUID,
        scanner_target: str,
        *,
        clock: Callable[[], float] = monotonic,
        include_clock: bool = False,
        allow_scoped_reads: bool = True,
        read_scope: SupplementalReadScope | None = None,
    ) -> None:
        if (
            not isinstance(endpoint_id, UUID)
            or not isinstance(scanner_target, str)
            or not scanner_target
        ):
            raise ValueError("An explicit endpoint identity and scanner target are required.")
        if type(include_clock) is not bool or type(allow_scoped_reads) is not bool:
            raise ValueError("Explicit boolean supplemental read policies are required.")
        if read_scope is not None and not callable(read_scope):
            raise ValueError("Supplemental read scope must be an explicit callable.")
        self._read_scope = read_scope
        clock_reader = getattr(scanner, "read_clock_if_idle", None) if include_clock else None
        if include_clock and not callable(clock_reader):
            raise ValueError("Clock reads require the existing scanner owner's idle reader.")
        self._clock_reader: _ClockReader | None = clock_reader
        self._allow_scoped_reads = allow_scoped_reads
        self._clock_samples = (
            ScannerClockSamples(endpoint_id, clock=clock) if include_clock else None
        )
        self._clock_session: ClockSession | None = None
        self._scanner, self._endpoint_id, self._target, self._clock = (
            scanner,
            endpoint_id,
            scanner_target,
            clock,
        )
        self._lock = threading.RLock()
        self._session: QuickKeySession | None = None
        self._selection: QuickKeySelection | None = None
        self._seen: float | None = None
        self._sequence = -1
        self._epoch = 0
        self._closed = False
        self._attached = False
        self._pending: object | None = None
        self._blocked: Failure | None = None
        self._demand_until = self._next_poll = self._latest_time = 0.0
        self._values: dict[Kind, _Cached] = {}
        self._failures: dict[Kind, Failure] = {}
        self._due: dict[Operation, float] = {}

    def _now(self) -> float:
        now = self._clock()
        if type(now) not in (int, float) or not 0 <= now <= 1e15 or not math.isfinite(now):
            raise ValueError("A bounded monotonic clock is required.")
        if now < self._latest_time:
            raise ValueError("Quick-key monotonic clock moved backwards.")
        self._latest_time = float(now)
        return float(now)

    def _invalidate(self) -> None:
        self._epoch += 1
        self._values.clear()
        self._failures.clear()
        # Global GET backoff survives view/profile/scope barriers. A real new
        # connection resets it explicitly. Scoped legacy schedules are unchanged.
        self._due = {
            kind: due
            for kind, due in self._due.items()
            if kind == "clock" or (kind == "favorites" and not self._allow_scoped_reads)
        }
        if self._clock_samples is not None and self._clock_session is not None:
            self._clock_samples.invalidate(self._clock_session)
        # An old in-flight request still occupies the one shared read slot.

    def require_owner(self, scanner: object, endpoint_id: UUID, scanner_target: str) -> None:
        """Reject attachment to another owner, even at the same address. No I/O."""
        if (
            self._closed
            or scanner is not self._scanner
            or endpoint_id != self._endpoint_id
            or scanner_target != self._target
        ):
            raise ValueError("Quick-key cache must belong to this exact scanner owner.")

    def attach_owner(self, scanner: object, endpoint_id: UUID, scanner_target: str) -> None:
        """One lifetime attachment; two feeds cannot reset each other's session."""
        with self._lock:
            self.require_owner(scanner, endpoint_id, scanner_target)
            if self._attached:
                raise ValueError("Quick-key cache is already attached to a display feed.")
            self._attached = True

    def clear_demand(self) -> None:
        """Invalidate pending/cached banks without lifting connection quarantine."""
        with self._lock:
            self._invalidate()
            self._demand_until = 0.0

    def suspend(self, session: QuickKeySession) -> None:
        """Require a new qualified PSI after a profile/observation barrier."""
        with self._lock:
            if session is self._session:
                self._invalidate()
                self._selection = self._seen = None
                self._demand_until = 0.0

    def begin_session(self) -> QuickKeySession:
        with self._lock:
            if self._closed:
                raise ValueError("Quick-key cache is closed.")
            self._now()
            self._invalidate()
            self._due.clear()
            if self._clock_samples is not None:
                self._clock_session = self._clock_samples.begin_session()
            self._session = QuickKeySession(self._endpoint_id)
            self._selection = self._seen = self._blocked = None
            self._sequence = -1
            self._demand_until = 0.0
            return self._session

    def disconnect(self, session: QuickKeySession) -> None:
        with self._lock:
            if session is self._session:
                self._invalidate()
                if self._clock_samples is not None and self._clock_session is not None:
                    self._clock_samples.disconnect(self._clock_session)
                self._clock_session = None
                self._session = self._selection = self._seen = None
                self._demand_until = 0.0

    def observe(
        self,
        session: QuickKeySession,
        selection: QuickKeySelection | None,
        *,
        sequence: int,
    ) -> bool:
        """Owner callback: bounded state update only; None suspends non-scan views."""
        if selection is not None and not isinstance(selection, QuickKeySelection):
            raise ValueError("A qualified quick-key selection is required.")
        if type(sequence) is not int or not 0 <= sequence < 2**63:
            raise ValueError("An ordered nonnegative PSI sequence is required.")
        with self._lock:
            if self._closed or session is not self._session:
                return False
            now = self._now()
            if sequence <= self._sequence:
                return False
            if (
                (selection != self._selection and self._allow_scoped_reads)
                or (selection is None) != (self._selection is None)
                or self._seen is None
                or now - self._seen >= STALE_AFTER
            ):
                self._invalidate()
            self._sequence, self._selection, self._seen = sequence, selection, now
            return True

    def request_refresh(self) -> None:
        """Coalesce all display consumers into one finite lease. No I/O."""
        with self._lock:
            if self._closed or self._session is None:
                return
            now = self._now()
            if now >= self._demand_until:
                self._invalidate()
            self._demand_until = now + DEMAND_LIFETIME

    def _active(self, now: float) -> bool:
        return (
            not self._closed
            and self._session is not None
            and self._selection is not None
            and self._seen is not None
            and now - self._seen < STALE_AFTER
            and now < self._demand_until
        )

    def _commands(self) -> dict[Kind, Read]:
        commands: dict[Kind, Read] = {"favorites": GetFavoritesQuickKeys()}
        selection = self._selection
        if self._allow_scoped_reads and selection is not None and selection.favorites is not None:
            commands["system"] = GetSystemQuickKeys(selection.favorites)
            if selection.system is not None:
                commands["department"] = GetDepartmentQuickKeys(
                    selection.favorites, selection.system
                )
        return commands

    def snapshot(self) -> QuickKeySnapshot:
        with self._lock:
            return self._snapshot_at(self._now())

    def _snapshot_at(self, now: float) -> QuickKeySnapshot:
        # Caller holds the shared lock; all sample gates use this same cutoff.
        active = self._active(now)
        banks = []
        for kind in KINDS:
            cached = self._values.get(kind)
            age = None if cached is None else now - cached.requested_at
            fresh = active and self._blocked is None and age is not None and age < STALE_AFTER
            banks.append(
                QuickKeyBank(
                    kind,
                    cached.states if fresh and cached is not None else None,
                    age if active else None,
                    self._failures.get(kind) if active else None,
                )
            )
        return QuickKeySnapshot(
            active, self._selection if active else None, self._blocked, tuple(banks)
        )

    def clock_snapshot(self) -> ClockSnapshot | None:
        """Internal exact scanner time only; no I/O, demand renewal or extrapolation."""
        with self._lock:
            if self._clock_samples is None:
                return None
            return self._clock_snapshot_at(self._now())

    def _clock_snapshot_at(self, now: float) -> ClockSnapshot | None:
        if self._clock_samples is None:
            return None
        snapshot = self._clock_samples._snapshot_at(now)
        if self._active(now) and self._blocked is None:
            return snapshot
        return replace(
            snapshot,
            local_time=None,
            daylight_saving=None,
            rtc_valid=None,
            age_seconds=None,
            blocked_until_reconnect=self._blocked or snapshot.blocked_until_reconnect,
        )

    def supplemental_snapshot(self) -> SupplementalSnapshot:
        """Read one coherent internal cut; no I/O, worker start or demand renewal."""
        with self._lock:
            now = self._now()
            return SupplementalSnapshot(
                self._session,
                self._sequence if self._sequence >= 0 else None,
                now,
                self._snapshot_at(now),
                self._clock_snapshot_at(now),
            )

    def _quarantine(self, failure: Failure) -> None:
        self._blocked = failure
        self._values.clear()
        if self._clock_samples is not None and self._clock_session is not None:
            self._clock_samples.invalidate(self._clock_session)

    def poll_once(self) -> bool:
        """Worker only: one bounded GET, never holding the cache lock over I/O.

        Returns True if a read was attempted (even if busy/refused). No retry
        after an uncertain reply/timeout until an explicit new connection.
        """
        with self._lock:
            now = self._now()
            if not self._active(now) or self._blocked or self._pending or now < self._next_poll:
                return False
            commands = self._commands()
            operations: list[Operation] = list(commands)
            if self._clock_samples is not None:
                operations.append("clock")
            ready = sorted(
                (kind for kind in operations if self._due.get(kind, 0) <= now),
                key=lambda key: self._due.get(key, 0),
            )
            clock_ticket: ClockReadTicket | None = None
            for kind in ready:
                if kind != "clock":
                    break
                assert self._clock_samples is not None and self._clock_session is not None
                clock_ticket = self._clock_samples.begin_read(self._clock_session)
                if clock_ticket is not None:
                    break
            else:
                return False
            ticket = self._pending = object()
            epoch, session = self._epoch, self._session
            self._next_poll = now + MIN_READ_GAP

        result: Result | None = None
        clock_result: ScannerDateTime | None = None
        failure: Failure | None = None
        target_valid: bool | None = None
        try:
            scope = (
                nullcontext(True) if self._read_scope is None else self._read_scope(self._scanner)
            )
            # Never acquire runtime locks under the cache/PSI callback lock.
            with scope as allowed:
                if type(allowed) is not bool:
                    raise ValueError("Supplemental read scope must yield a boolean.")
                with self._lock:
                    if not allowed and self._session is session and session is not None:
                        # Even an otherwise-normal control can precede a mode
                        # change. Require new PSI/demand rather than old context.
                        self.suspend(session)
                    current = (
                        self._session is session
                        and self._epoch == epoch
                        and self._active(self._now())
                    )
                if allowed and current:
                    target_valid = (
                        self._scanner.connected and self._scanner.endpoint == self._target
                    )
                    if target_valid:
                        if kind == "clock":
                            assert self._clock_reader is not None
                            clock_result = self._clock_reader(timeout=READ_TIMEOUT)
                        else:
                            command = commands[kind]
                            result = self._scanner.read_quick_keys_if_idle(
                                command, timeout=READ_TIMEOUT
                            )
                        target_valid = (
                            self._scanner.connected and self._scanner.endpoint == self._target
                        )
                        if result is not None:
                            expected = {
                                GetFavoritesQuickKeys: FavoritesQuickKeys,
                                GetSystemQuickKeys: SystemQuickKeys,
                                GetDepartmentQuickKeys: DepartmentQuickKeys,
                            }[type(command)]
                            if (
                                type(result) is not expected
                                or type(result.states) is not tuple
                                or any(
                                    type(state) is not FavoritesQuickKeyState
                                    for state in result.states
                                )
                                or result != command.parse_response(result.packet)
                            ):
                                raise ProtocolError("Invalid quick-key result.")
        except CommandTimeoutError:
            failure = "timeout"
        except CommandRejectedError:
            failure = "rejected"
        except (ProtocolError, TypeError, ValueError, AttributeError):
            failure = "invalid_response"
        except Exception:
            failure = "read_error"
        except BaseException:
            with self._lock:
                if self._pending is ticket:
                    self._pending = None
                if clock_ticket is not None:
                    assert self._clock_samples is not None
                    self._clock_samples.finish(clock_ticket, failure="read_error")
                if self._session is session:
                    self._quarantine("read_error")
            raise

        with self._lock:
            # Release the shared request slot and commit/refuse its result
            # atomically; another worker cannot race a timeout quarantine.
            if self._pending is ticket:
                self._pending = None
            finished = self._now()
            self._next_poll = max(self._next_poll, finished + MIN_READ_GAP)
            if finished - now >= READ_TIMEOUT:
                failure = "timeout"
            if target_valid is False and self._session is session and session is not None:
                self.disconnect(session)
            if clock_ticket is not None:
                assert self._clock_samples is not None
                if not self._active(finished) and self._clock_session is not None:
                    self._clock_samples.invalidate(self._clock_session)
                self._clock_samples.finish(
                    clock_ticket, clock_result if failure is None else None, failure=failure
                )
                if self._session is session:
                    failure = self._clock_samples.snapshot().blocked_until_reconnect or failure
            if self._closed or self._session is not session:
                return True
            # Scope/demand changes cannot make an uncertain reply safe to retry
            # on the same wire connection, even though its value is discarded.
            if failure in ("timeout", "invalid_response", "read_error"):
                self._quarantine(failure)
            # Rejection pacing also survives a barrier while the GET was pending.
            if failure == "rejected" and (kind == "clock" or not self._allow_scoped_reads):
                self._due[kind] = finished + REJECTION_BACKOFF
            if epoch != self._epoch or not self._active(finished):
                return True
            if kind == "clock":
                self._due[kind] = finished + (
                    REJECTION_BACKOFF if failure is not None else REFRESH_INTERVAL
                )
                return True
            if failure is not None:
                self._values.pop(kind, None)
                self._failures[kind] = failure
                self._due[kind] = finished + REJECTION_BACKOFF
            elif result is None:
                self._due[kind] = finished + MIN_READ_GAP
            else:
                # Do not retain the packet, raw reply or wall-clock timestamp.
                self._values[kind] = _Cached(tuple(result.states), now)
                self._failures.pop(kind, None)
                self._due[kind] = finished + REFRESH_INTERVAL
            return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._invalidate()
            if self._clock_samples is not None and self._clock_session is not None:
                self._clock_samples.disconnect(self._clock_session)
            self._clock_session = None
            self._session = self._selection = self._seen = None
            self._demand_until = 0.0


@dataclass(frozen=True, slots=True)
class QuickKeyWorkerStatus:
    started: bool
    alive: bool
    stopped: bool
    failure: Literal["worker_start_failed", "worker_failed"] | None


class DaemonQuickKeyWorker:
    """One optional owner worker. Failures never escape into display rendering.

    The gate runs before taking the scanner command lane. Closing invalidates
    the cache immediately and waits at most 750 ms, never closing the shared
    scanner. An unexpectedly blocked transport may outlive that wait, but its
    result cannot be committed and this worker cannot restart itself.
    """

    def __init__(self, cache: DaemonQuickKeyCache, before_poll: Callable[[], bool]) -> None:
        self._cache, self._before_poll = cache, before_poll
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: Literal["worker_start_failed", "worker_failed"] | None = None

    def start(self) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            if self._thread is not None:
                return self._thread.is_alive()
            try:
                self._thread = threading.Thread(
                    target=self._run, name="sdsctl-quick-keys", daemon=True
                )
                self._thread.start()
            except Exception:
                self._failure = "worker_start_failed"
                self._stop.set()
                self._cache.close()
                return False
            return True

    def _run(self) -> None:
        try:
            while not self._stop.wait(MIN_READ_GAP):
                if self._before_poll() and not self._stop.is_set():
                    self._cache.poll_once()
        except BaseException:
            # Do not emit raw exception/traceback data from a background thread.
            self.fail()
        finally:
            self._stop.set()
            self._cache.close()

    def fail(self) -> None:
        """Nonblocking fault isolation for callbacks and API readers."""
        with self._lock:
            self._failure = "worker_failed"
        self._stop.set()
        self._cache.close()

    def status(self) -> QuickKeyWorkerStatus:
        with self._lock:
            return QuickKeyWorkerStatus(
                self._thread is not None,
                self._thread is not None and self._thread.is_alive(),
                self._stop.is_set(),
                self._failure,
            )

    def close(self) -> None:
        self._stop.set()
        self._cache.close()
        with self._lock:
            thread = self._thread
        # An unstarted thread after start failure cannot be joined.
        if (
            thread is not None
            and thread.ident is not None
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=0.75)
