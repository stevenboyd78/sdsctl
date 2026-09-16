"""Opt-in owner-side quick-key cache, not yet wired to daemon startup or frames.

An owner supplies qualified PSI selections and connection tickets. Consumers
renew one shared demand lease; snapshots never perform I/O. A background worker
may call poll_once to perform at most one GET through the existing scanner.
No thread, transport, profile reader, renderer, or API is started here.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID

from .commands import GetDepartmentQuickKeys, GetFavoritesQuickKeys, GetSystemQuickKeys
from .exceptions import CommandRejectedError, CommandTimeoutError, ProtocolError
from .models import DepartmentQuickKeys, FavoritesQuickKeys, FavoritesQuickKeyState, SystemQuickKeys
from .scanner_quick_keys import QuickKeySelection

Kind = Literal["favorites", "system", "department"]
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
    ) -> None:
        if (
            not isinstance(endpoint_id, UUID)
            or not isinstance(scanner_target, str)
            or not scanner_target
        ):
            raise ValueError("An explicit endpoint identity and scanner target are required.")
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
        self._pending: object | None = None
        self._blocked: Failure | None = None
        self._demand_until = self._next_poll = self._latest_time = 0.0
        self._values: dict[Kind, _Cached] = {}
        self._failures: dict[Kind, Failure] = {}
        self._due: dict[Kind, float] = {}

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
        self._due.clear()
        # An old in-flight request still occupies the one shared read slot.

    def begin_session(self) -> QuickKeySession:
        with self._lock:
            if self._closed:
                raise ValueError("Quick-key cache is closed.")
            self._now()
            self._invalidate()
            self._session = QuickKeySession(self._endpoint_id)
            self._selection = self._seen = self._blocked = None
            self._sequence = -1
            self._demand_until = 0.0
            return self._session

    def disconnect(self, session: QuickKeySession) -> None:
        with self._lock:
            if session is self._session:
                self._invalidate()
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
                selection != self._selection
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
        if selection is not None and selection.favorites is not None:
            commands["system"] = GetSystemQuickKeys(selection.favorites)
            if selection.system is not None:
                commands["department"] = GetDepartmentQuickKeys(
                    selection.favorites, selection.system
                )
        return commands

    def snapshot(self) -> QuickKeySnapshot:
        with self._lock:
            now = self._now()
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
            ready = [kind for kind in KINDS if kind in commands and self._due.get(kind, 0) <= now]
            if not ready:
                return False
            kind = min(ready, key=lambda key: self._due.get(key, 0))
            command = commands[kind]
            ticket = self._pending = object()
            epoch, session = self._epoch, self._session
            self._next_poll = now + MIN_READ_GAP

        result: Result | None = None
        failure: Failure | None = None
        target_valid = False
        try:
            target_valid = self._scanner.connected and self._scanner.endpoint == self._target
            if target_valid:
                result = self._scanner.read_quick_keys_if_idle(command, timeout=READ_TIMEOUT)
                target_valid = self._scanner.connected and self._scanner.endpoint == self._target
                if result is not None:
                    expected = {
                        GetFavoritesQuickKeys: FavoritesQuickKeys,
                        GetSystemQuickKeys: SystemQuickKeys,
                        GetDepartmentQuickKeys: DepartmentQuickKeys,
                    }[type(command)]
                    if (
                        type(result) is not expected
                        or type(result.states) is not tuple
                        or any(type(state) is not FavoritesQuickKeyState for state in result.states)
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
                if self._session is session:
                    self._blocked = "read_error"
                    self._values.clear()
            raise

        with self._lock:
            # Release the shared request slot and commit/refuse its result
            # atomically; another worker cannot race a timeout quarantine.
            if self._pending is ticket:
                self._pending = None
            finished = self._now()
            if self._closed or self._session is not session:
                return True
            if not target_valid:
                if session is not None:
                    self.disconnect(session)
                return True
            if finished - now >= READ_TIMEOUT:
                failure = "timeout"
            # Scope/demand changes cannot make an uncertain reply safe to retry
            # on the same wire connection, even though its value is discarded.
            if failure in ("timeout", "invalid_response", "read_error"):
                self._blocked = failure
                self._values.clear()
            if epoch != self._epoch or not self._active(finished):
                return True
            self._next_poll = max(self._next_poll, finished + MIN_READ_GAP)
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
            self._session = self._selection = self._seen = None
            self._demand_until = 0.0
