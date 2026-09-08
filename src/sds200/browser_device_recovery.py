"""Experimental native-helper recovery ledger; no launcher or extension enables it.

No credentials, cookies or session tokens are persisted. A trusted installation
supplies an identity fingerprint and a bounded, verified-TLS exchange callback.
The native transport module supplies the callback and process deadline; this
module does no network I/O. Concurrent helpers share the private ledger.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .browser_device_protocol import BrowserDeviceAction, BrowserDeviceRequest
from .browser_device_store import BrowserDeviceStore, BrowserDeviceStoreError


class BrowserRecoveryError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Browser recovery state is unavailable or invalid.")


class RecoveryMode(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    REJECTED = "credential_rejected"
    TLS_ERROR = "tls_error"
    SETUP_ERROR = "setup_error"
    PROTOCOL_ERROR = "protocol_error"


@dataclass(frozen=True, slots=True)
class ExchangeSession:
    """Private native-pipe handoff only; never generically serialize or log."""

    token: str = field(repr=False)
    expires_in: float

    def __post_init__(self) -> None:
        if (type(self.token) is not str
                or re.fullmatch(r"sdsctl-browser-session-v1\.[a-f0-9]{64}", self.token) is None
                or type(self.expires_in) not in (int, float)
                or not math.isfinite(self.expires_in) or not 1 <= self.expires_in <= 3600):
            raise BrowserRecoveryError()


class ExchangeFailure(Exception):
    """Transport supplies only a fixed classification, never response/secret text."""

    def __init__(self, mode: RecoveryMode = RecoveryMode.ACTIVE, *, retry_after: int = 0):
        if (not isinstance(mode, RecoveryMode) or mode is RecoveryMode.PAUSED
                or type(retry_after) is not int or not 0 <= retry_after <= 300):
            raise BrowserRecoveryError()
        super().__init__("Browser-device exchange was not completed.")
        self.mode = mode  # ACTIVE means transient and retryable, not authenticated.
        self.retry_after = retry_after


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def parse_exchange_response(
    status: int, body: bytes, *, content_type: str, retry_after: str | None = None,
) -> ExchangeSession:
    """Decode the actual /auth/device/session contract, not the earlier fixture.

    Retry-After supports bounded delta seconds only. TLS/network failures must be
    classified by the verified transport before calling this function. Redirects
    must not have been followed. Error response bodies are never exposed.
    """
    if type(status) is not int:
        raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR)
    if status == 401:
        raise ExchangeFailure(RecoveryMode.REJECTED)
    if status in {408, 429, 500, 502, 503, 504}:
        delay = 0
        if isinstance(retry_after, str) and re.fullmatch(r"[0-9]{1,6}", retry_after):
            delay = min(300, int(retry_after))
        raise ExchangeFailure(retry_after=delay)
    if status != 200 or content_type != "application/json":
        raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR)
    try:
        if type(body) is not bytes or not 0 < len(body) <= 4096:
            raise ValueError()
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
        if type(value) is not dict or set(value) != {"token", "expires_in"}:
            raise ValueError()
        return ExchangeSession(value["token"], value["expires_in"])
    except (ValueError, UnicodeError, RecursionError, BrowserRecoveryError):
        raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR) from None


@dataclass(frozen=True, slots=True)
class RecoveryStatus:
    mode: RecoveryMode
    revision: int
    retry_after: float


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    status: RecoveryStatus
    session: ExchangeSession | None = field(default=None, repr=False)
    renew_after: float = 0


@dataclass(frozen=True, slots=True)
class _State:
    revision: int
    mode: RecoveryMode
    failures: int
    next_at: float
    observed_at: float


class BrowserDeviceRecovery:
    """Private existing SQLite state. Missing/corrupt state never silently resets.

    Same-account/root processes are trusted. Initialization and explicit resume
    are administrator operations, not native-message actions. Bind identity to
    the verified installation (server identity, device ID and extension identity)
    without storing a secret; do not change this fingerprint to bypass a pause.
    """

    def __init__(
        self, path: Path, identity: str, *, clock: Callable[[], float] = time.time,
        jitter: Callable[[], float] = lambda: secrets.randbelow(1001) / 1000,
    ) -> None:
        if type(identity) is not str or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
            raise BrowserRecoveryError()
        self.path = path
        self._identity = identity
        self._clock = clock
        self._jitter = jitter

    def _now(self) -> float:
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise BrowserRecoveryError()
        return float(now)

    @classmethod
    def initialize(cls, path: Path, identity: str) -> BrowserDeviceRecovery:
        instance = cls(path, identity)
        try:
            BrowserDeviceStore(path)._check(database=False)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            with instance._connection(initializing=True) as db:
                db.execute("CREATE TABLE recovery (id INTEGER PRIMARY KEY CHECK(id=1), "
                           "identity TEXT NOT NULL, revision INTEGER NOT NULL, mode TEXT NOT NULL, "
                           "failures INTEGER NOT NULL, next_at REAL NOT NULL, "
                           "observed_at REAL NOT NULL)")
                db.execute("INSERT INTO recovery VALUES (1, ?, 1, 'active', 0, 0, ?)",
                           (identity, instance._now()))
                db.execute("PRAGMA user_version=1")
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, ValueError, sqlite3.Error, BrowserDeviceStoreError):
            raise BrowserRecoveryError() from None
        return instance

    @contextmanager
    def _connection(self, *, initializing: bool = False) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            BrowserDeviceStore(self.path)._check()
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2)
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            if not initializing:
                self._check_version(db)
            yield db
            db.commit()
        except (OSError, ValueError, sqlite3.Error, BrowserDeviceStoreError):
            raise BrowserRecoveryError() from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _check_version(db: sqlite3.Connection) -> None:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 2:
            from .browser_device_resume import validate_resume_state

            validate_resume_state(db)
        elif version != 1 or db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='browser_resume'"
        ).fetchone() is not None:
            raise BrowserRecoveryError()

    @staticmethod
    def _cancel_resume(db: sqlite3.Connection) -> bool:
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            return False
        from .browser_device_resume import cancel_resume_approvals

        return cancel_resume_approvals(db)

    def _load(
        self, db: sqlite3.Connection, now: float, *, correct_clock: bool = True,
    ) -> _State:
        rows = db.execute("SELECT id, identity, revision, mode, failures, next_at, observed_at "
                          "FROM recovery LIMIT 2").fetchall()
        if len(rows) != 1:
            raise BrowserRecoveryError()
        identifier, identity, revision, mode, failures, next_at, observed_at = rows[0]
        if (identifier != 1 or identity != self._identity
                or type(revision) is not int or not 1 <= revision < 2**63 - 1
                or type(failures) is not int or not 0 <= failures <= 32
                or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                       for value in (next_at, observed_at))):
            raise BrowserRecoveryError()
        if next_at > observed_at + 300:
            raise BrowserRecoveryError()
        state = _State(revision, RecoveryMode(mode), failures, next_at, observed_at)
        if now < observed_at and correct_clock:
            # Pi clocks may step during startup. Bound retry waits, but never clear a pause/error.
            self._cancel_resume(db)
            state = _State(revision + 1, state.mode, failures, now + 10, now)
            self._save(db, state)
        return state

    @staticmethod
    def _save(db: sqlite3.Connection, state: _State) -> None:
        db.execute("UPDATE recovery SET revision=?, mode=?, failures=?, next_at=?, observed_at=? "
                   "WHERE id=1", (state.revision, state.mode.value, state.failures,
                                  state.next_at, state.observed_at))

    @staticmethod
    def _status(state: _State, now: float) -> RecoveryStatus:
        delay = max(0, state.next_at - now) if state.mode is RecoveryMode.ACTIVE else 0
        return RecoveryStatus(state.mode, state.revision, delay)

    def handle(
        self, request: BrowserDeviceRequest, exchange: Callable[[], ExchangeSession],
    ) -> RecoveryResult:
        """Dispatch parsed fixed native actions. Resume is deliberately not a native action."""
        if not isinstance(request, BrowserDeviceRequest):
            raise BrowserRecoveryError()
        if request.action is BrowserDeviceAction.STATUS:
            return RecoveryResult(self.status())
        if request.action is BrowserDeviceAction.SUSPEND:
            return RecoveryResult(self.suspend())
        if request.action is BrowserDeviceAction.AUTHENTICATE:
            return self.authenticate(exchange)
        if request.action is BrowserDeviceAction.CLAIM_BROWSER:
            return RecoveryResult(self.claim_browser())
        raise BrowserRecoveryError()

    def claim_browser(self) -> RecoveryStatus:
        """Consume first-run eligibility once, without credentials or network I/O.

        Only a pristine active revision-1 ledger qualifies. Never reset a used,
        paused, terminal, clock-corrected or previously claimed installation.
        Lost acknowledgement requires review, not automatic reinitialization.
        """
        now = self._now()
        with self._connection() as db:
            state = self._load(db, now, correct_clock=False)
            if (state.revision != 1 or state.mode is not RecoveryMode.ACTIVE
                    or state.failures != 0 or state.next_at != 0 or now < state.observed_at):
                raise BrowserRecoveryError()
            claimed = _State(2, RecoveryMode.ACTIVE, 0, 0, now)
            self._save(db, claimed)
            return self._status(claimed, now)

    def status(self) -> RecoveryStatus:
        now = self._now()
        with self._connection() as db:
            return self._status(self._load(db, now), now)

    def inspect(self) -> RecoveryStatus:
        """Read-only offline inspection; do not clear modes or persist clock correction.

        A rolled-back clock uses the last observed time for this delay snapshot.
        The next runtime status call still performs its normal durable correction.
        """
        try:
            BrowserDeviceStore(self.path)._check()
            # This ledger uses rollback journals. A read-only WAL connection can
            # still create/update -wal/-shm files, so reject that format before
            # SQLite opens it. Do not switch modes or run recovery here.
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb", buffering=0) as stream:
                header = stream.read(20)
            if len(header) != 20 or header[:16] != b"SQLite format 3\0" or header[18:] != b"\1\1":
                raise BrowserRecoveryError()
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                         timeout=2)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                self._check_version(db)
                now = self._now()
                state = self._load(db, now, correct_clock=False)
                return self._status(state, max(now, state.observed_at))
        except (OSError, ValueError, sqlite3.Error, BrowserDeviceStoreError):
            raise BrowserRecoveryError() from None

    def suspend(self) -> RecoveryStatus:
        """Persist offline intent before attempting server logout or clearing cookies.

        This does not claim server sessions were revoked. A resumed installation
        still needs server reconciliation; only a reviewed administrator resumes.
        """
        now = self._now()
        with self._connection() as db:
            state = self._load(db, now)
            cancelled = self._cancel_resume(db)
            if state.mode is not RecoveryMode.PAUSED or cancelled:
                state = _State(state.revision + 1, RecoveryMode.PAUSED, 0, 0, now)
                self._save(db, state)
            return self._status(state, now)

    def resume(self, expected_revision: int) -> RecoveryStatus:
        """Explicit administrator reset only; NOT reachable through native requests.

        Caller must verify user intent, repaired setup/trust and server state.
        A credential/config replacement or ordinary login must not call this.
        """
        now = self._now()
        with self._connection() as db:
            state = self._load(db, now)
            if type(expected_revision) is not int or state.revision != expected_revision:
                raise BrowserRecoveryError()
            self._cancel_resume(db)
            state = _State(state.revision + 1, RecoveryMode.ACTIVE, 0, 0, now)
            self._save(db, state)
            return self._status(state, now)

    def authenticate(
        self, exchange: Callable[[], ExchangeSession], *, expected_revision: int | None = None,
    ) -> RecoveryResult:
        """One bounded exchange at most. Caller schedules renewal; no sleeping loop.

        Callback must finish within ten seconds. The persisted 15-second claim
        prevents concurrent helpers and survives a crash. An expired claim may
        be replaced; revision checks discard its late response. No token is saved.
        """
        now = self._now()
        with self._connection() as db:
            state = self._load(db, now)
            if expected_revision is not None and (
                type(expected_revision) is not int or expected_revision != state.revision
            ):
                raise BrowserRecoveryError()
            if state.mode is not RecoveryMode.ACTIVE or state.next_at > now:
                return RecoveryResult(self._status(state, now))
            claim = _State(state.revision + 1, state.mode, state.failures, now + 15, now)
            self._save(db, claim)
        session = None
        failure = None
        started = time.monotonic()
        try:
            session = exchange()
            if not isinstance(session, ExchangeSession):
                raise ExchangeFailure(RecoveryMode.PROTOCOL_ERROR)
        except ExchangeFailure as error:
            failure = error
        except Exception:
            # Unexpected transport errors must not become an unbounded retry loop or expose text.
            failure = ExchangeFailure(RecoveryMode.SETUP_ERROR)
        elapsed = max(0, time.monotonic() - started)
        if elapsed > 10:
            session = None
            # A callback can return just before the supervisor notices its deadline.
            # Discard its late session, but do not turn a slow network into a saved
            # setup error that survives every later helper invocation.
            if failure is None:
                failure = ExchangeFailure()
        now = self._now()
        with self._connection() as db:
            current = self._load(db, now)
            if current.revision != claim.revision or current.mode is not RecoveryMode.ACTIVE:
                return RecoveryResult(self._status(current, now))  # Suspend/new claim wins.
            if failure is not None:
                failures = min(32, claim.failures + 1)
                delay = 0.0
                if failure.mode is RecoveryMode.ACTIVE:
                    jitter = self._jitter()
                    if (type(jitter) not in (int, float) or not math.isfinite(jitter)
                            or not 0 <= jitter <= 1):
                        raise BrowserRecoveryError()
                    backoff = min(60, 2 ** min(failures, 6)) * (0.75 + 0.25 * jitter)
                    delay = max(backoff, failure.retry_after)
                state = _State(claim.revision, failure.mode, failures, now + delay, now)
                self._save(db, state)
                return RecoveryResult(self._status(state, now))
            assert session is not None
            lifetime = session.expires_in - elapsed  # Conservatively include exchange time.
            if lifetime < 1:
                state = _State(claim.revision, RecoveryMode.ACTIVE, claim.failures, now + 2, now)
                self._save(db, state)
                return RecoveryResult(self._status(state, now))
            state = _State(claim.revision, RecoveryMode.ACTIVE, 0,
                           now + min(10, lifetime / 4), now)
            self._save(db, state)
            return RecoveryResult(self._status(state, now),
                                  ExchangeSession(session.token, lifetime), lifetime * 0.75)
