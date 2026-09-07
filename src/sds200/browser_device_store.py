"""Experimental browser enrollment authority, disabled in server/App defaults.

Every operation opens the existing private SQLite database. Missing/corrupt
authority never creates a replacement or falls back to cached credentials.
Bindings are snapshots, not authorization leases: callers must still implement
session/stream invalidation and acknowledged cross-process revocation.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

_VERSION = 1
_LIMIT = 256
_PREFIX = "sdsctl-browser-v1."
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SECRET = re.compile(re.escape(_PREFIX) + r"[0-9a-f]{64}")


class BrowserDeviceStoreError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Browser enrollment authority is unavailable or operation is invalid.")


class BrowserDeviceConflict(BrowserDeviceStoreError):
    """The reviewed record changed before this operation; no mutation was made."""


class BrowserDeviceState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class BrowserDeviceBinding:
    device_id: str
    generation: int

    def __post_init__(self) -> None:
        _device_id(self.device_id)
        if type(self.generation) is not int or not 1 <= self.generation < 2**63:
            raise BrowserDeviceStoreError()


@dataclass(frozen=True, slots=True)
class BrowserDeviceRecord:
    device_id: str
    generation: int
    state: BrowserDeviceState


@dataclass(frozen=True, slots=True)
class IssuedBrowserDevice:
    """One-time caller handoff; do not serialize this object into diagnostics."""

    record: BrowserDeviceRecord
    credential: str = field(repr=False)


def _device_id(value: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise BrowserDeviceStoreError()
    return value


def _verifier(device_id: str, credential: str) -> str:
    return hashlib.sha256((device_id + "\0" + credential).encode("ascii")).hexdigest()


def _record(row: tuple[str, int, str]) -> BrowserDeviceRecord:
    device_id, generation, state = row
    if type(generation) is not int or not 1 <= generation < 2**63:
        raise BrowserDeviceStoreError()
    return BrowserDeviceRecord(_device_id(device_id), generation, BrowserDeviceState(state))


def _next_generation(record: BrowserDeviceRecord) -> int:
    if record.generation >= 2**63 - 1:
        raise BrowserDeviceStoreError()
    return record.generation + 1


def validate_browser_device_record(record: BrowserDeviceRecord) -> None:
    if not isinstance(record, BrowserDeviceRecord):
        raise BrowserDeviceStoreError()
    BrowserDeviceBinding(record.device_id, record.generation)
    if not isinstance(record.state, BrowserDeviceState):
        raise BrowserDeviceStoreError()


def _expect(record: BrowserDeviceRecord, expected: BrowserDeviceRecord | None) -> None:
    if expected is not None:
        validate_browser_device_record(expected)
        if record != expected:
            raise BrowserDeviceConflict()


class BrowserDeviceStore:
    """Small private SQLite authority with serialized generation transitions.

    Parent directory must already be private, owned by the current account and
    free of symlinks. This does not defend against a malicious same-UID process
    or root. No delete/reuse operation is supplied for enrollment identifiers.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _check(self, *, database: bool = True) -> None:
        if os.name != "posix":
            raise BrowserDeviceStoreError()
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise BrowserDeviceStoreError()
        if self.path.resolve() != self.path:
            raise BrowserDeviceStoreError()
        parent = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise BrowserDeviceStoreError()
        if database:
            info = self.path.lstat()
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            ):
                raise BrowserDeviceStoreError()

    @classmethod
    def initialize(cls, path: Path) -> BrowserDeviceStore:
        """Explicit first-time creation only; never overwrite existing authority."""
        store = cls(path)
        try:
            store._check(database=False)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            with store._connection(initializing=True) as connection:
                connection.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY, "
                                   "generation INTEGER NOT NULL, state TEXT NOT NULL, "
                                   "verifier TEXT NOT NULL)")
                connection.execute(f"PRAGMA user_version={_VERSION}")
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (OSError, ValueError, sqlite3.Error):
            # A failed creation is retained and fails closed on subsequent open.
            raise BrowserDeviceStoreError() from None
        return store

    @contextmanager
    def _connection(self, *, initializing: bool = False) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            self._check()
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not initializing and version != _VERSION:
                raise BrowserDeviceStoreError()
            yield connection
            connection.commit()
        except BrowserDeviceConflict:
            raise
        except (BrowserDeviceStoreError, OSError, ValueError, sqlite3.Error):
            raise BrowserDeviceStoreError() from None
        finally:
            if connection is not None:
                connection.close()  # Rolls back uncommitted operations on failure.

    def enroll(self, device_id: str) -> IssuedBrowserDevice:
        device_id = _device_id(device_id)
        credential = _PREFIX + secrets.token_hex(32)
        with self._connection() as connection:
            if connection.execute("SELECT count(*) FROM devices").fetchone()[0] >= _LIMIT:
                raise BrowserDeviceStoreError()
            connection.execute("INSERT INTO devices VALUES (?, 1, 'active', ?)",
                               (device_id, _verifier(device_id, credential)))
        return IssuedBrowserDevice(BrowserDeviceRecord(device_id, 1, BrowserDeviceState.ACTIVE),
                                   credential)

    def validate_existing(self) -> None:
        """Read-only startup preflight, including schema and credential verifiers.

        Never create a missing file, migrate a schema, or recover a database by
        replacing it. Runtime operations still recheck authority on every use.
        """
        connection = None
        try:
            self._check()
            # A read-only SQLite WAL open can still create -shm/-wal sidecars.
            # Reject that file format before handing the file to SQLite.
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                header = os.read(descriptor, 20)
                if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
                    raise BrowserDeviceStoreError()
            finally:
                os.close(descriptor)
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if (connection.execute("PRAGMA user_version").fetchone() != (_VERSION,)
                    or connection.execute("PRAGMA journal_mode").fetchone() != ("delete",)
                    or connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]
                    or connection.execute(
                        "SELECT type, name FROM sqlite_schema ORDER BY name"
                    ).fetchall() != [("table", "devices"), ("index", "sqlite_autoindex_devices_1")]
                    or connection.execute("PRAGMA table_info(devices)").fetchall() != [
                        (0, "device_id", "TEXT", 0, None, 1),
                        (1, "generation", "INTEGER", 1, None, 0),
                        (2, "state", "TEXT", 1, None, 0),
                        (3, "verifier", "TEXT", 1, None, 0),
                    ]):
                raise BrowserDeviceStoreError()
            rows = connection.execute(
                "SELECT device_id, generation, state, verifier FROM devices LIMIT ?",
                (_LIMIT + 1,),
            ).fetchall()
            if len(rows) > _LIMIT:
                raise BrowserDeviceStoreError()
            for device_id, generation, state, verifier in rows:
                _record((device_id, generation, state))
                if type(verifier) is not str or re.fullmatch(r"[a-f0-9]{64}", verifier) is None:
                    raise BrowserDeviceStoreError()
        except (BrowserDeviceStoreError, OSError, TypeError, ValueError, sqlite3.Error):
            raise BrowserDeviceStoreError() from None
        finally:
            if connection is not None:
                connection.close()

    def inventory(self) -> tuple[BrowserDeviceRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute("SELECT device_id, generation, state FROM devices "
                                      "ORDER BY device_id LIMIT ?", (_LIMIT + 1,)).fetchall()
            if len(rows) > _LIMIT:
                raise BrowserDeviceStoreError()
            return tuple(_record(row) for row in rows)

    def authenticate(self, device_id: str, credential: str) -> BrowserDeviceBinding | None:
        # No passwords or TUI credentials share this credential namespace.
        if (type(device_id) is not str or _ID.fullmatch(device_id) is None
                or type(credential) is not str or _SECRET.fullmatch(credential) is None):
            return None
        with self._connection() as connection:
            row = connection.execute("SELECT device_id, generation, state, verifier FROM devices "
                                     "WHERE device_id=?", (device_id,)).fetchone()
            expected = row[3] if row else "0" * 64
            if type(expected) is not str or re.fullmatch("[a-f0-9]{64}", expected) is None:
                raise BrowserDeviceStoreError()
            matches = hmac.compare_digest(expected, _verifier(device_id, credential))
            record = _record(row[:3]) if row else None
            if not matches or record is None or record.state is not BrowserDeviceState.ACTIVE:
                return None
            return BrowserDeviceBinding(record.device_id, record.generation)

    def is_current(self, binding: BrowserDeviceBinding) -> bool:
        """Snapshot check only: NOT a stream lease or acknowledgement barrier."""
        if not isinstance(binding, BrowserDeviceBinding):
            return False
        with self._connection() as connection:
            row = connection.execute("SELECT device_id, generation, state FROM devices "
                                     "WHERE device_id=?", (binding.device_id,)).fetchone()
            if row is None:
                return False
            record = _record(row)
            return (record.state is BrowserDeviceState.ACTIVE
                    and record.generation == binding.generation)

    def transition(
        self, device_id: str, target: BrowserDeviceState, *,
        expected: BrowserDeviceRecord | None = None,
    ) -> BrowserDeviceRecord:
        """Admin-only primitive; active->paused/revoked, paused->active/revoked.

        Revocation is terminal in this foundation. New enrollment needs a new ID.
        Resume never revives an old session binding. Repeating a state is a no-op.
        """
        _device_id(device_id)
        if not isinstance(target, BrowserDeviceState):
            raise BrowserDeviceStoreError()
        with self._connection() as connection:
            row = connection.execute("SELECT device_id, generation, state FROM devices "
                                     "WHERE device_id=?", (device_id,)).fetchone()
            if row is None:
                raise BrowserDeviceStoreError()
            record = _record(row)
            _expect(record, expected)
            if record.state is target:
                return record
            if record.state is BrowserDeviceState.REVOKED:
                raise BrowserDeviceStoreError()
            generation = _next_generation(record)
            connection.execute("UPDATE devices SET state=?, generation=? WHERE device_id=?",
                               (target.value, generation, device_id))
            return BrowserDeviceRecord(device_id, generation, target)

    def rotate(
        self, device_id: str, *, expected: BrowserDeviceRecord | None = None,
    ) -> IssuedBrowserDevice:
        """Replace only this device's secret; paused devices remain paused."""
        _device_id(device_id)
        credential = _PREFIX + secrets.token_hex(32)
        with self._connection() as connection:
            row = connection.execute("SELECT device_id, generation, state FROM devices "
                                     "WHERE device_id=?", (device_id,)).fetchone()
            if row is None:
                raise BrowserDeviceStoreError()
            record = _record(row)
            _expect(record, expected)
            if record.state is BrowserDeviceState.REVOKED:
                raise BrowserDeviceStoreError()
            generation = _next_generation(record)
            connection.execute("UPDATE devices SET verifier=?, generation=? WHERE device_id=?",
                               (_verifier(device_id, credential), generation, device_id))
            return IssuedBrowserDevice(BrowserDeviceRecord(device_id, generation, record.state),
                                       credential)

    def pause_binding(self, binding: BrowserDeviceBinding) -> BrowserDeviceRecord | None:
        """Compare-and-pause: a stale browser cannot pause a newer enrollment generation."""
        if not isinstance(binding, BrowserDeviceBinding):
            return None
        with self._connection() as connection:
            row = connection.execute("SELECT device_id, generation, state FROM devices "
                                     "WHERE device_id=?", (binding.device_id,)).fetchone()
            if row is None:
                return None
            record = _record(row)
            if (record.generation != binding.generation
                    or record.state is not BrowserDeviceState.ACTIVE):
                return None
            generation = _next_generation(record)
            connection.execute("UPDATE devices SET state='paused', generation=? WHERE device_id=?",
                               (generation, binding.device_id))
            return BrowserDeviceRecord(binding.device_id, generation, BrowserDeviceState.PAUSED)
