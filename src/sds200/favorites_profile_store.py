"""Private persistent named Favorites profiles with immutable revisions.

This module deliberately owns no HTTP routes and performs no scanner, USB,
FTP, or copied-tree writes.  It stores exact :class:`FavoritesStorageSnapshot`
values in one private SQLite database and requires an exact current revision
for every update.  A later authenticated adapter can therefore expose named
profiles without turning a browser request into filesystem authority.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import stat
import struct
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from .favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    project_favorites_storage_snapshot,
)

FAVORITES_PROFILE_STORE_FILENAME = "profiles.sqlite3"
FAVORITES_PROFILE_STORE_SCHEMA_VERSION = 1
FAVORITES_PROFILE_DEFAULT_MAX_PROFILES = 64
FAVORITES_PROFILE_DEFAULT_MAX_REVISIONS = 128
FAVORITES_PROFILE_DEFAULT_MAX_DOCUMENTS = 256
FAVORITES_PROFILE_DEFAULT_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
FAVORITES_PROFILE_DEFAULT_MAX_NAME_CHARACTERS = 80

_PROFILE_ID = re.compile(r"[A-Za-z0-9_-]{24,128}")
_TokenFactory = Callable[[], str]
_Clock = Callable[[], int]


class FavoritesProfileStoreError(RuntimeError):
    """Base failure for private Favorites profile persistence."""


class FavoritesProfileStoreUnavailableError(FavoritesProfileStoreError):
    """The configured private store cannot be safely opened or used."""


class FavoritesProfileStoreClosedError(FavoritesProfileStoreError):
    """The profile store has already been closed."""


class FavoritesProfileNotFoundError(FavoritesProfileStoreError):
    """The requested profile or immutable revision is unavailable."""

    def __init__(self) -> None:
        super().__init__("Favorites profile is unavailable.")


class FavoritesProfileConflictError(FavoritesProfileStoreError):
    """An update was based on a stale profile revision."""

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Favorites profile revision is stale: expected {expected}, actual {actual}."
        )


class FavoritesProfileLimitError(FavoritesProfileStoreError):
    """A configured persistence capacity limit was reached."""


class FavoritesProfileNameError(ValueError):
    """A public profile name is invalid or already in use."""


class FavoritesProfileSnapshotError(ValueError):
    """A snapshot cannot be persisted as one exact profile revision."""


class FavoritesProfileCompatibility(StrEnum):
    """Scanner-family compatibility explicitly selected for one profile."""

    SDS100 = "sds100"
    SDS200 = "sds200"
    SHARED = "shared"


@dataclass(frozen=True, slots=True)
class FavoritesProfileStoreLimits:
    """Hard local limits for profile and immutable revision persistence."""

    max_profiles: int = FAVORITES_PROFILE_DEFAULT_MAX_PROFILES
    max_revisions_per_profile: int = FAVORITES_PROFILE_DEFAULT_MAX_REVISIONS
    max_documents: int = FAVORITES_PROFILE_DEFAULT_MAX_DOCUMENTS
    max_snapshot_bytes: int = FAVORITES_PROFILE_DEFAULT_MAX_SNAPSHOT_BYTES
    max_name_characters: int = FAVORITES_PROFILE_DEFAULT_MAX_NAME_CHARACTERS

    def __post_init__(self) -> None:
        values = (
            self.max_profiles,
            self.max_revisions_per_profile,
            self.max_documents,
            self.max_snapshot_bytes,
            self.max_name_characters,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Favorites profile store limits must be positive integers.")


@dataclass(frozen=True, slots=True)
class FavoritesProfileSummary:
    """Path-free current profile metadata safe for an authorized renderer."""

    profile_id: str
    name: str
    compatibility: FavoritesProfileCompatibility
    revision: int
    revision_count: int
    document_count: int
    snapshot_bytes: int
    snapshot_sha256: str
    created_at_ns: int
    updated_at_ns: int


@dataclass(frozen=True, slots=True)
class FavoritesProfileRevision:
    """One exact immutable profile revision and its exact snapshot."""

    summary: FavoritesProfileSummary
    snapshot: FavoritesStorageSnapshot


@dataclass(frozen=True, slots=True)
class _PreparedSnapshot:
    snapshot: FavoritesStorageSnapshot
    document_count: int
    snapshot_bytes: int
    sha256: str


def _profile_name(value: str, maximum: int) -> tuple[str, str]:
    if not isinstance(value, str):
        raise TypeError("Favorites profile name must be text.")
    if (
        not value
        or value.strip() != value
        or len(value) > maximum
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        raise FavoritesProfileNameError(
            "Favorites profile name must be trimmed, non-empty, bounded text without controls."
        )
    return value, unicodedata.normalize("NFKC", value).casefold()


def _compatibility(value: FavoritesProfileCompatibility) -> FavoritesProfileCompatibility:
    if not isinstance(value, FavoritesProfileCompatibility):
        raise TypeError("Favorites profile compatibility must be explicit.")
    return value


def _revision(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Favorites profile revision must be a positive integer.")
    return value


def _snapshot_digest(snapshot: FavoritesStorageSnapshot) -> str:
    digest = hashlib.sha256(b"sdsctl-favorites-profile-snapshot-v1\0")

    def add(value: bytes) -> None:
        digest.update(struct.pack(">Q", len(value)))
        digest.update(value)

    add(snapshot.catalog_bytes)
    for document in snapshot.documents:
        add(document.filename.encode("utf-8"))
        add(document.content)
    return digest.hexdigest()


def _prepare_snapshot(
    snapshot: FavoritesStorageSnapshot,
    limits: FavoritesProfileStoreLimits,
) -> _PreparedSnapshot:
    if not isinstance(snapshot, FavoritesStorageSnapshot):
        raise TypeError("Favorites profile revision requires FavoritesStorageSnapshot.")
    if len(snapshot.documents) > limits.max_documents:
        raise FavoritesProfileLimitError("Favorites profile document limit was exceeded.")
    filenames = tuple(document.filename for document in snapshot.documents)
    if len(filenames) != len(set(filenames)):
        raise FavoritesProfileSnapshotError(
            "Favorites profile documents must have unique exact filenames."
        )
    snapshot_bytes = len(snapshot.catalog_bytes) + sum(
        len(document.content) for document in snapshot.documents
    )
    if snapshot_bytes > limits.max_snapshot_bytes:
        raise FavoritesProfileLimitError("Favorites profile byte limit was exceeded.")
    try:
        project_favorites_storage_snapshot(snapshot)
    except (TypeError, ValueError) as error:
        raise FavoritesProfileSnapshotError(
            "Favorites profile snapshot is not a valid Favorites workspace."
        ) from error
    return _PreparedSnapshot(
        snapshot=snapshot,
        document_count=len(snapshot.documents),
        snapshot_bytes=snapshot_bytes,
        sha256=_snapshot_digest(snapshot),
    )


def _private_directory(root: Path) -> None:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError("Favorites profile store directory must be one absolute path.")
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile store directory could not be created."
        ) from error
    try:
        observed = root.lstat()
        canonical = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile store directory could not be inspected."
        ) from error
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode) or canonical != root:
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile store directory must be canonical and non-symbolic."
        )
    if os.name == "posix" and (
        observed.st_uid != os.geteuid() or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile store directory must be owner-only mode 0700."
        )


def _private_database(path: Path) -> None:
    created = False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        pass
    except OSError as error:
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile database could not be created."
        ) from error
    else:
        created = True
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    try:
        observed = path.lstat()
    except OSError as error:
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile database could not be inspected."
        ) from error
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile database must be one regular non-symbolic file."
        )
    if os.name == "posix" and (
        observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o600
        or observed.st_nlink != 1
    ):
        raise FavoritesProfileStoreUnavailableError(
            "Favorites profile database must be owner-only mode 0600 with one link."
        )
    if created:
        try:
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError as error:
            raise FavoritesProfileStoreUnavailableError(
                "Favorites profile database directory could not be synchronized."
            ) from error
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class FavoritesProfileStore:
    """Own one private SQLite profile store with optimistic concurrency."""

    def __init__(
        self,
        directory: Path,
        *,
        limits: FavoritesProfileStoreLimits | None = None,
        clock: _Clock = time.time_ns,
        token_factory: _TokenFactory | None = None,
    ) -> None:
        if limits is None:
            limits = FavoritesProfileStoreLimits()
        elif not isinstance(limits, FavoritesProfileStoreLimits):
            raise TypeError("Favorites profile store limits are invalid.")
        if not callable(clock) or (token_factory is not None and not callable(token_factory)):
            raise TypeError("Favorites profile store factories must be callable.")
        _private_directory(directory)
        database = directory / FAVORITES_PROFILE_STORE_FILENAME
        _private_database(database)
        try:
            connection = sqlite3.connect(
                database,
                isolation_level=None,
                check_same_thread=False,
                timeout=5.0,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error as error:
            raise FavoritesProfileStoreUnavailableError(
                "Favorites profile database could not be opened."
            ) from error
        self._directory = directory
        self._database = database
        self._limits = limits
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._initialize_schema()
        except BaseException:
            connection.close()
            raise

    @property
    def database(self) -> Path:
        """Return the configured private database path for host diagnostics."""

        return self._database

    def __enter__(self) -> FavoritesProfileStore:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise FavoritesProfileStoreClosedError("Favorites profile store is closed.")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._require_open()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            yield self._connection
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _initialize_schema(self) -> None:
        with self._lock, self._transaction() as connection:
            version_row = connection.execute("PRAGMA user_version").fetchone()
            if version_row is None:
                raise FavoritesProfileStoreUnavailableError(
                    "Favorites profile database schema could not be inspected."
                )
            version = int(version_row[0])
            if version not in (0, FAVORITES_PROFILE_STORE_SCHEMA_VERSION):
                raise FavoritesProfileStoreUnavailableError(
                    "Favorites profile database schema is unsupported."
                )
            for statement in (
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    compatibility TEXT NOT NULL,
                    latest_revision INTEGER NOT NULL,
                    created_at_ns INTEGER NOT NULL,
                    updated_at_ns INTEGER NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS revisions (
                    profile_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    compatibility TEXT NOT NULL,
                    catalog BLOB NOT NULL,
                    document_count INTEGER NOT NULL,
                    snapshot_bytes INTEGER NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    created_at_ns INTEGER NOT NULL,
                    PRIMARY KEY (profile_id, revision),
                    FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
                        ON DELETE CASCADE
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS documents (
                    profile_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    content BLOB NOT NULL,
                    PRIMARY KEY (profile_id, revision, position),
                    UNIQUE (profile_id, revision, filename),
                    FOREIGN KEY (profile_id, revision)
                        REFERENCES revisions(profile_id, revision)
                        ON DELETE CASCADE
                )
                """,
            ):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {FAVORITES_PROFILE_STORE_SCHEMA_VERSION}")
            checked = connection.execute("PRAGMA quick_check(1)").fetchone()
            if checked is None or checked[0] != "ok":
                raise FavoritesProfileStoreUnavailableError(
                    "Favorites profile database integrity check failed."
                )

    def close(self) -> None:
        """Synchronously close this owner; later operations fail closed."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()

    def _new_profile_id(self, connection: sqlite3.Connection) -> str:
        for _attempt in range(4):
            candidate = self._token_factory()
            if not isinstance(candidate, str) or _PROFILE_ID.fullmatch(candidate) is None:
                continue
            exists = connection.execute(
                "SELECT 1 FROM profiles WHERE profile_id = ?", (candidate,)
            ).fetchone()
            if exists is None:
                return candidate
        raise FavoritesProfileLimitError("Favorites profile identifier generation is unavailable.")

    def _insert_revision(
        self,
        connection: sqlite3.Connection,
        *,
        profile_id: str,
        revision: int,
        name: str,
        compatibility: FavoritesProfileCompatibility,
        prepared: _PreparedSnapshot,
        created_at_ns: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO revisions (
                profile_id, revision, name, compatibility, catalog,
                document_count, snapshot_bytes, snapshot_sha256, created_at_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                revision,
                name,
                compatibility.value,
                prepared.snapshot.catalog_bytes,
                prepared.document_count,
                prepared.snapshot_bytes,
                prepared.sha256,
                created_at_ns,
            ),
        )
        connection.executemany(
            """
            INSERT INTO documents (
                profile_id, revision, position, filename, content
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (profile_id, revision, position, document.filename, document.content)
                for position, document in enumerate(prepared.snapshot.documents)
            ),
        )

    def _summary_from_row(self, row: sqlite3.Row) -> FavoritesProfileSummary:
        return FavoritesProfileSummary(
            profile_id=str(row["profile_id"]),
            name=str(row["name"]),
            compatibility=FavoritesProfileCompatibility(str(row["compatibility"])),
            revision=int(row["revision"]),
            revision_count=int(row["revision_count"]),
            document_count=int(row["document_count"]),
            snapshot_bytes=int(row["snapshot_bytes"]),
            snapshot_sha256=str(row["snapshot_sha256"]),
            created_at_ns=int(row["profile_created_at_ns"]),
            updated_at_ns=int(row["updated_at_ns"]),
        )

    def _summary_query(self, *, where: str = "") -> str:
        return f"""
            SELECT p.profile_id, r.name, r.compatibility,
                   p.latest_revision AS revision,
                   p.latest_revision AS revision_count,
                   r.document_count, r.snapshot_bytes, r.snapshot_sha256,
                   p.created_at_ns AS profile_created_at_ns,
                   p.updated_at_ns
              FROM profiles AS p
              JOIN revisions AS r
                ON r.profile_id = p.profile_id
               AND r.revision = p.latest_revision
              {where}
        """

    def create_profile(
        self,
        name: str,
        compatibility: FavoritesProfileCompatibility,
        snapshot: FavoritesStorageSnapshot,
    ) -> FavoritesProfileRevision:
        """Create one named profile at immutable revision one."""

        exact_name, name_key = _profile_name(name, self._limits.max_name_characters)
        exact_compatibility = _compatibility(compatibility)
        prepared = _prepare_snapshot(snapshot, self._limits)
        with self._lock, self._transaction() as connection:
            count_row = connection.execute("SELECT COUNT(*) FROM profiles").fetchone()
            if count_row is None or int(count_row[0]) >= self._limits.max_profiles:
                raise FavoritesProfileLimitError("Favorites profile limit was reached.")
            if (
                connection.execute(
                    "SELECT 1 FROM profiles WHERE name_key = ?", (name_key,)
                ).fetchone()
                is not None
            ):
                raise FavoritesProfileNameError("Favorites profile name is already in use.")
            profile_id = self._new_profile_id(connection)
            now = self._clock()
            if type(now) is not int or now < 0:
                raise FavoritesProfileStoreUnavailableError(
                    "Favorites profile store clock returned an invalid timestamp."
                )
            connection.execute(
                """
                INSERT INTO profiles (
                    profile_id, name, name_key, compatibility,
                    latest_revision, created_at_ns, updated_at_ns
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    profile_id,
                    exact_name,
                    name_key,
                    exact_compatibility.value,
                    now,
                    now,
                ),
            )
            self._insert_revision(
                connection,
                profile_id=profile_id,
                revision=1,
                name=exact_name,
                compatibility=exact_compatibility,
                prepared=prepared,
                created_at_ns=now,
            )
            return self._read_revision(connection, profile_id, 1)

    def _profile_row(self, connection: sqlite3.Connection, profile_id: str) -> sqlite3.Row:
        if not isinstance(profile_id, str) or _PROFILE_ID.fullmatch(profile_id) is None:
            raise FavoritesProfileNotFoundError()
        row = connection.execute(
            "SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        if row is None:
            raise FavoritesProfileNotFoundError()
        return cast(sqlite3.Row, row)

    def save_revision(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        snapshot: FavoritesStorageSnapshot,
        name: str | None = None,
        compatibility: FavoritesProfileCompatibility | None = None,
    ) -> FavoritesProfileRevision:
        """Atomically append a revision only from the exact current revision."""

        expected = _revision(expected_revision)
        prepared = _prepare_snapshot(snapshot, self._limits)
        with self._lock, self._transaction() as connection:
            current = self._profile_row(connection, profile_id)
            actual = int(current["latest_revision"])
            if actual != expected:
                raise FavoritesProfileConflictError(expected=expected, actual=actual)
            exact_name, name_key = _profile_name(
                str(current["name"]) if name is None else name,
                self._limits.max_name_characters,
            )
            exact_compatibility = (
                FavoritesProfileCompatibility(str(current["compatibility"]))
                if compatibility is None
                else _compatibility(compatibility)
            )
            collision = connection.execute(
                "SELECT profile_id FROM profiles WHERE name_key = ? AND profile_id != ?",
                (name_key, profile_id),
            ).fetchone()
            if collision is not None:
                raise FavoritesProfileNameError("Favorites profile name is already in use.")
            current_revision = connection.execute(
                """
                SELECT name, compatibility, snapshot_sha256
                  FROM revisions
                 WHERE profile_id = ? AND revision = ?
                """,
                (profile_id, actual),
            ).fetchone()
            if current_revision is None:
                raise FavoritesProfileStoreUnavailableError(
                    "Favorites profile current revision is unavailable."
                )
            if (
                str(current_revision["name"]) == exact_name
                and str(current_revision["compatibility"]) == exact_compatibility.value
                and str(current_revision["snapshot_sha256"]) == prepared.sha256
            ):
                return self._read_revision(connection, profile_id, actual)
            if actual >= self._limits.max_revisions_per_profile:
                raise FavoritesProfileLimitError("Favorites profile revision limit was reached.")
            new_revision = actual + 1
            now = self._clock()
            if type(now) is not int or now < 0:
                raise FavoritesProfileStoreUnavailableError(
                    "Favorites profile store clock returned an invalid timestamp."
                )
            self._insert_revision(
                connection,
                profile_id=profile_id,
                revision=new_revision,
                name=exact_name,
                compatibility=exact_compatibility,
                prepared=prepared,
                created_at_ns=now,
            )
            connection.execute(
                """
                UPDATE profiles
                   SET name = ?, name_key = ?, compatibility = ?,
                       latest_revision = ?, updated_at_ns = ?
                 WHERE profile_id = ? AND latest_revision = ?
                """,
                (
                    exact_name,
                    name_key,
                    exact_compatibility.value,
                    new_revision,
                    now,
                    profile_id,
                    actual,
                ),
            )
            return self._read_revision(connection, profile_id, new_revision)

    def _read_revision(
        self,
        connection: sqlite3.Connection,
        profile_id: str,
        revision: int,
    ) -> FavoritesProfileRevision:
        row = connection.execute(
            """
            SELECT p.profile_id, r.name, r.compatibility, r.revision,
                   r.revision AS revision_count,
                   r.document_count, r.snapshot_bytes, r.snapshot_sha256,
                   p.created_at_ns AS profile_created_at_ns,
                   r.created_at_ns AS updated_at_ns,
                   r.catalog
              FROM profiles AS p
              JOIN revisions AS r ON r.profile_id = p.profile_id
             WHERE p.profile_id = ? AND r.revision = ?
            """,
            (profile_id, revision),
        ).fetchone()
        if row is None:
            raise FavoritesProfileNotFoundError()
        documents = tuple(
            FavoritesStorageDocument(
                filename=str(document["filename"]),
                content=bytes(document["content"]),
            )
            for document in connection.execute(
                """
                SELECT filename, content
                  FROM documents
                 WHERE profile_id = ? AND revision = ?
                 ORDER BY position
                """,
                (profile_id, revision),
            )
        )
        return FavoritesProfileRevision(
            summary=self._summary_from_row(row),
            snapshot=FavoritesStorageSnapshot(
                catalog_bytes=bytes(row["catalog"]),
                documents=documents,
            ),
        )

    def get_profile(
        self,
        profile_id: str,
        *,
        revision: int | None = None,
    ) -> FavoritesProfileRevision:
        """Read the current or one earlier immutable profile revision."""

        with self._lock:
            self._require_open()
            current = self._profile_row(self._connection, profile_id)
            selected = int(current["latest_revision"]) if revision is None else _revision(revision)
            return self._read_revision(self._connection, profile_id, selected)

    def list_profiles(self) -> tuple[FavoritesProfileSummary, ...]:
        """List current path-free profile metadata in stable name order."""

        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                self._summary_query(where="ORDER BY p.name_key, p.profile_id")
            ).fetchall()
            return tuple(self._summary_from_row(row) for row in rows)

    def get_profile_summary(self, profile_id: str) -> FavoritesProfileSummary:
        """Read current path-free metadata without loading snapshot bytes."""

        with self._lock:
            self._require_open()
            self._profile_row(self._connection, profile_id)
            row = self._connection.execute(
                self._summary_query(where="WHERE p.profile_id = ?"),
                (profile_id,),
            ).fetchone()
            if row is None:
                raise FavoritesProfileNotFoundError()
            return self._summary_from_row(row)


__all__ = [
    "FAVORITES_PROFILE_DEFAULT_MAX_DOCUMENTS",
    "FAVORITES_PROFILE_DEFAULT_MAX_NAME_CHARACTERS",
    "FAVORITES_PROFILE_DEFAULT_MAX_PROFILES",
    "FAVORITES_PROFILE_DEFAULT_MAX_REVISIONS",
    "FAVORITES_PROFILE_DEFAULT_MAX_SNAPSHOT_BYTES",
    "FAVORITES_PROFILE_STORE_FILENAME",
    "FAVORITES_PROFILE_STORE_SCHEMA_VERSION",
    "FavoritesProfileCompatibility",
    "FavoritesProfileConflictError",
    "FavoritesProfileLimitError",
    "FavoritesProfileNameError",
    "FavoritesProfileNotFoundError",
    "FavoritesProfileRevision",
    "FavoritesProfileSnapshotError",
    "FavoritesProfileStore",
    "FavoritesProfileStoreClosedError",
    "FavoritesProfileStoreError",
    "FavoritesProfileStoreLimits",
    "FavoritesProfileStoreUnavailableError",
    "FavoritesProfileSummary",
]
