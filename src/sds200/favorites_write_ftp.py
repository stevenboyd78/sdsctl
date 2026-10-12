"""Verified additive-only Favorites writes over bounded trusted-network FTP."""

from __future__ import annotations

import ftplib
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Protocol, TypeAlias

from .favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)
from .favorites_storage_evidence import favorites_storage_snapshot_sha256
from .favorites_storage_ftp import (
    FavoritesFtpReadConfiguration,
    FavoritesFtpSecretResolver,
    FavoritesFtpStorageError,
    FavoritesFtpStorageErrorReason,
    _environment_secret_resolver,
    _read_snapshot_pass,
    _validate_listing_name,
    _validate_text,
)
from .favorites_write_plan import FavoritesWritePlan

FAVORITES_FTP_WRITE_MAX_ADDITIONS = 16
_FAVORITES_FTP_WRITE_TRANSFER_BLOCK_SIZE = 64 * 1024
_FAVORITES_FTP_WRITE_ARTIFACT_SCHEMA = "sds200.favorites-ftp-write.artifact"
_FAVORITES_FTP_WRITE_ARTIFACT_VERSION = 1


class FavoritesFtpWriteFailureReason(StrEnum):
    """Stable redacted FTP write failure classes."""

    BLOCKED_PLAN = "blocked_plan"
    UNSUPPORTED_PLAN = "unsupported_plan"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    CONNECTION_FAILED = "connection_failed"
    AUTHENTICATION_FAILED = "authentication_failed"
    DIRECTORY_UNAVAILABLE = "directory_unavailable"
    OBSERVATION_FAILED = "observation_failed"
    TARGET_STALE = "target_stale"
    TARGET_UNSAFE = "target_unsafe"
    ARTIFACT_UNSAFE = "artifact_unsafe"
    STORE_FAILED = "store_failed"
    RENAME_FAILED = "rename_failed"
    DELETE_FAILED = "delete_failed"
    VERIFICATION_FAILED = "verification_failed"
    ACTIVATION_FAILED = "activation_failed"
    ROLLBACK_FAILED = "rollback_failed"
    CLEANUP_FAILED = "cleanup_failed"


class FavoritesFtpWriteTransportError(RuntimeError):
    """Report one redacted mutation-capable FTP transport failure."""

    def __init__(
        self,
        reason: FavoritesFtpWriteFailureReason,
        message: str,
    ) -> None:
        if not isinstance(reason, FavoritesFtpWriteFailureReason):
            raise TypeError(
                "Favorites FTP write transport reason must be FavoritesFtpWriteFailureReason."
            )
        if not isinstance(message, str) or not message:
            raise ValueError("Favorites FTP write transport message must not be empty.")
        self.reason = reason
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FavoritesFtpWriteCredential:
    """Explicit writable username plus a password secret reference."""

    username: str
    password_environment_variable: str

    def __post_init__(self) -> None:
        _validate_text(
            self.username,
            label="Favorites FTP write username",
        )
        variable = _validate_text(
            self.password_environment_variable,
            label="Favorites FTP write password environment-variable name",
        )
        if "=" in variable or any(character.isspace() for character in variable):
            raise ValueError("Favorites FTP write password environment-variable name is invalid.")


@dataclass(frozen=True, slots=True)
class FavoritesFtpWriteConfiguration:
    """One bounded target plus a credential that cannot fall back to read access."""

    target: FavoritesFtpReadConfiguration
    credential: FavoritesFtpWriteCredential
    max_additions: int = FAVORITES_FTP_WRITE_MAX_ADDITIONS

    def __post_init__(self) -> None:
        if not isinstance(self.target, FavoritesFtpReadConfiguration):
            raise TypeError(
                "Favorites FTP write configuration requires "
                "FavoritesFtpReadConfiguration target bounds."
            )
        if not isinstance(self.credential, FavoritesFtpWriteCredential):
            raise TypeError(
                "Favorites FTP write configuration requires FavoritesFtpWriteCredential."
            )
        if (
            self.credential.password_environment_variable
            == self.target.credential.password_environment_variable
        ):
            raise ValueError(
                "Favorites FTP read and write password secret references must be different."
            )
        if isinstance(self.max_additions, bool) or not isinstance(
            self.max_additions,
            int,
        ):
            raise TypeError("Favorites FTP write maximum additions must be an integer.")
        if not 1 <= self.max_additions <= FAVORITES_FTP_WRITE_MAX_ADDITIONS:
            raise ValueError(
                "Favorites FTP write maximum additions must be between 1 and "
                f"{FAVORITES_FTP_WRITE_MAX_ADDITIONS}."
            )


class FavoritesFtpWriteSession(Protocol):
    """Narrow FTP boundary used by verified additive write execution."""

    def list_names(self) -> tuple[str, ...]:
        """Return immediate names from the current FTP directory."""
        ...

    def retrieve_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> bytes:
        """Return one exact remote file while enforcing its byte limit."""
        ...

    def store_file(
        self,
        filename: str,
        content: bytes,
    ) -> None:
        """Store one exact bounded remote file."""
        ...

    def rename_file(
        self,
        source: str,
        destination: str,
    ) -> None:
        """Rename one immediate remote file without overwrite assumptions."""
        ...

    def delete_file(
        self,
        filename: str,
    ) -> None:
        """Delete one exact immediate remote file."""
        ...

    def close(self) -> None:
        """Close the session deterministically."""
        ...


FavoritesFtpWriteSessionFactory: TypeAlias = Callable[
    [FavoritesFtpWriteConfiguration, str],
    FavoritesFtpWriteSession,
]


class _FtplibFavoritesFtpWriteSession:
    def __init__(self, ftp: ftplib.FTP) -> None:
        self._ftp = ftp
        self._closed = False

    def list_names(self) -> tuple[str, ...]:
        try:
            return tuple(self._ftp.nlst())
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            raise FavoritesFtpStorageError(
                reason=FavoritesFtpStorageErrorReason.LISTING_FAILED,
                message="Could not list the Favorites FTP directory.",
            ) from None

    def retrieve_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> bytes:
        content = bytearray()

        def consume(chunk: bytes) -> None:
            if len(content) + len(chunk) > max_bytes:
                raise OverflowError
            content.extend(chunk)

        try:
            self._ftp.retrbinary(
                f"RETR {filename}",
                consume,
                blocksize=_FAVORITES_FTP_WRITE_TRANSFER_BLOCK_SIZE,
            )
        except OverflowError:
            raise FavoritesFtpStorageError(
                reason=FavoritesFtpStorageErrorReason.LIMIT_EXCEEDED,
                message="Favorites FTP file exceeds its configured byte limit.",
            ) from None
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            raise FavoritesFtpStorageError(
                reason=FavoritesFtpStorageErrorReason.RETRIEVAL_FAILED,
                message="Could not retrieve one Favorites FTP file.",
            ) from None
        return bytes(content)

    def store_file(
        self,
        filename: str,
        content: bytes,
    ) -> None:
        try:
            self._ftp.storbinary(
                f"STOR {filename}",
                BytesIO(content),
                blocksize=_FAVORITES_FTP_WRITE_TRANSFER_BLOCK_SIZE,
            )
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.STORE_FAILED,
                "Could not store one staged Favorites FTP file.",
            ) from None

    def rename_file(
        self,
        source: str,
        destination: str,
    ) -> None:
        try:
            self._ftp.rename(source, destination)
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.RENAME_FAILED,
                "Could not rename one staged Favorites FTP file.",
            ) from None

    def delete_file(
        self,
        filename: str,
    ) -> None:
        try:
            self._ftp.delete(filename)
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.DELETE_FAILED,
                "Could not delete one owned Favorites FTP file.",
            ) from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._ftp.quit()
        except (ftplib.Error, OSError, EOFError, UnicodeError):
            with suppress(OSError):
                self._ftp.close()
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
                "Could not close the Favorites FTP write session cleanly.",
            ) from None


def _close_unowned_ftp(ftp: ftplib.FTP) -> None:
    with suppress(OSError):
        ftp.close()


def _ftplib_write_session_factory(
    configuration: FavoritesFtpWriteConfiguration,
    password: str,
) -> FavoritesFtpWriteSession:
    ftp = ftplib.FTP()
    target = configuration.target
    try:
        ftp.connect(
            host=target.host,
            port=target.port,
            timeout=target.timeout,
        )
    except (ftplib.Error, OSError, EOFError, UnicodeError):
        _close_unowned_ftp(ftp)
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.CONNECTION_FAILED,
            "Could not connect to the Favorites FTP write endpoint.",
        ) from None
    try:
        ftp.login(
            user=configuration.credential.username,
            passwd=password,
        )
    except (ftplib.Error, OSError, EOFError, UnicodeError):
        _close_unowned_ftp(ftp)
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.AUTHENTICATION_FAILED,
            "Favorites FTP writable authentication failed.",
        ) from None
    try:
        ftp.cwd(target.favorites_directory)
    except (ftplib.Error, OSError, EOFError, UnicodeError):
        _close_unowned_ftp(ftp)
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.DIRECTORY_UNAVAILABLE,
            "Favorites FTP write directory is unavailable.",
        ) from None
    return _FtplibFavoritesFtpWriteSession(ftp)


def _resolve_write_secret(
    configuration: FavoritesFtpWriteConfiguration,
    resolver: FavoritesFtpSecretResolver,
) -> str:
    variable = configuration.credential.password_environment_variable
    try:
        password = resolver(variable)
    except Exception:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.CREDENTIAL_UNAVAILABLE,
            "Favorites FTP write password secret is unavailable.",
        ) from None
    if not isinstance(password, str) or not password:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.CREDENTIAL_UNAVAILABLE,
            "Favorites FTP write password secret is unavailable.",
        )
    return password


@dataclass(frozen=True, slots=True)
class _StableObservation:
    listing: tuple[str, ...]
    snapshot: FavoritesStorageSnapshot


def _stable_observation(
    session: FavoritesFtpWriteSession,
    configuration: FavoritesFtpWriteConfiguration,
) -> _StableObservation:
    try:
        first = _read_snapshot_pass(session, configuration.target)
        second = _read_snapshot_pass(session, configuration.target)
    except FavoritesFtpStorageError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.OBSERVATION_FAILED,
            "Could not capture a bounded Favorites FTP write observation.",
        ) from error
    if first != second:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.TARGET_STALE,
            "Favorites FTP target changed between verification passes.",
        )
    return _StableObservation(
        listing=second.listing,
        snapshot=second.snapshot,
    )


def _document_map(
    snapshot: FavoritesStorageSnapshot,
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for document in snapshot.documents:
        if document.filename in result:
            raise ValueError(
                "Favorites FTP write snapshots must not contain duplicate document filenames."
            )
        result[document.filename] = document.content
    return result


def _additive_documents(
    plan: FavoritesWritePlan,
    *,
    maximum: int,
) -> tuple[FavoritesStorageDocument, ...]:
    baseline = _document_map(plan.baseline_snapshot)
    intended = _document_map(plan.intended_snapshot)
    if set(baseline) - set(intended):
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP writes cannot delete existing HPD documents.",
        )
    if any(intended[name] != content for name, content in baseline.items()):
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP writes cannot replace existing HPD documents.",
        )
    additions = tuple(
        FavoritesStorageDocument(
            filename=name,
            content=intended[name],
        )
        for name in sorted(set(intended) - set(baseline))
    )
    if len(additions) > maximum:
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP write exceeds its configured additive-document limit.",
        )
    if additions and (plan.baseline_snapshot.catalog_bytes == plan.intended_snapshot.catalog_bytes):
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP additive writes require a changed intended catalog.",
        )
    if not additions and not plan.is_noop:
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP writes require at least one additive HPD change.",
        )
    return additions


def _operation_token(
    plan: FavoritesWritePlan,
    configuration: FavoritesFtpWriteConfiguration,
) -> str:
    digest = hashlib.sha256()
    for value in (
        b"sds200-favorites-ftp-write-operation-v1",
        configuration.target.host.encode(),
        str(configuration.target.port).encode(),
        configuration.target.favorites_directory.encode(),
        favorites_storage_snapshot_sha256(plan.baseline_snapshot).encode(),
        favorites_storage_snapshot_sha256(plan.intended_snapshot).encode(),
    ):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    digest.update(secrets.token_bytes(16))
    return digest.hexdigest()[:16]


def _temporary_names(
    token: str,
    additions: tuple[FavoritesStorageDocument, ...],
) -> tuple[str, ...]:
    return (
        *(f"sdsctl_{token}_d{index:02d}.tmp" for index, _ in enumerate(additions)),
        f"sdsctl_{token}_catalog.tmp",
        f"sdsctl_{token}_catalog.bak",
        f"sdsctl_{token}_catalog.old",
        f"sdsctl_{token}_recovery.tmp",
    )


class FavoritesFtpWritePreflightError(RuntimeError):
    """Report a refusal before any FTP mutation begins."""

    def __init__(
        self,
        reason: FavoritesFtpWriteFailureReason,
        message: str,
    ) -> None:
        if not isinstance(reason, FavoritesFtpWriteFailureReason):
            raise TypeError(
                "Favorites FTP write preflight reason must be FavoritesFtpWriteFailureReason."
            )
        if not isinstance(message, str) or not message:
            raise ValueError("Favorites FTP write preflight message must not be empty.")
        self.reason = reason
        self.message = message
        super().__init__(f"Favorites FTP write preflight failed ({reason.value}): {message}")


@dataclass(frozen=True, slots=True)
class FavoritesFtpWritePreflight:
    """Exact stable remote evidence captured without mutation."""

    plan: FavoritesWritePlan
    configuration: FavoritesFtpWriteConfiguration
    observed_listing: tuple[str, ...]
    added_documents: tuple[FavoritesStorageDocument, ...]
    operation_token: str
    temporary_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan, FavoritesWritePlan):
            raise TypeError("Favorites FTP write preflight requires FavoritesWritePlan.")
        if not isinstance(self.configuration, FavoritesFtpWriteConfiguration):
            raise TypeError(
                "Favorites FTP write preflight requires FavoritesFtpWriteConfiguration."
            )
        if type(self.observed_listing) is not tuple or any(
            not isinstance(name, str) for name in self.observed_listing
        ):
            raise TypeError("Favorites FTP write observed listing must be a tuple of strings.")
        if type(self.added_documents) is not tuple or any(
            not isinstance(document, FavoritesStorageDocument) for document in self.added_documents
        ):
            raise TypeError("Favorites FTP write additions must be a tuple of documents.")
        if len(self.operation_token) != 16 or any(
            character not in "0123456789abcdef" for character in self.operation_token
        ):
            raise ValueError(
                "Favorites FTP write operation token must be 16 lowercase hex characters."
            )
        if type(self.temporary_names) is not tuple or any(
            not isinstance(name, str) for name in self.temporary_names
        ):
            raise TypeError("Favorites FTP write temporary names must be a tuple of strings.")
        if self.plan.is_blocked:
            raise ValueError("Favorites FTP write preflight cannot retain a blocked plan.")

    @property
    def is_noop(self) -> bool:
        """Return whether this accepted plan requires no remote mutation."""

        return self.plan.is_noop


def preflight_favorites_ftp_write(
    plan: FavoritesWritePlan,
    configuration: FavoritesFtpWriteConfiguration,
    *,
    session_factory: FavoritesFtpWriteSessionFactory = _ftplib_write_session_factory,
    secret_resolver: FavoritesFtpSecretResolver = _environment_secret_resolver,
) -> FavoritesFtpWritePreflight:
    """Capture a stable exact target and reject non-additive or colliding plans."""

    if not isinstance(plan, FavoritesWritePlan):
        raise TypeError("Favorites FTP write preflight requires FavoritesWritePlan.")
    if not isinstance(configuration, FavoritesFtpWriteConfiguration):
        raise TypeError("Favorites FTP write preflight requires FavoritesFtpWriteConfiguration.")
    if not callable(session_factory):
        raise TypeError("Favorites FTP write session factory must be callable.")
    if not callable(secret_resolver):
        raise TypeError("Favorites FTP write secret resolver must be callable.")
    if plan.is_blocked:
        blockers = ", ".join(blocker.value for blocker in plan.blockers)
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.BLOCKED_PLAN,
            f"Favorites write plan is blocked: {blockers}.",
        )
    additions = _additive_documents(
        plan,
        maximum=configuration.max_additions,
    )
    intended = plan.intended_snapshot
    if len(intended.catalog_bytes) > configuration.target.max_catalog_bytes:
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP intended catalog exceeds its configured byte limit.",
        )
    if any(
        len(document.content) > configuration.target.max_document_bytes for document in additions
    ):
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP intended HPD exceeds its configured byte limit.",
        )
    intended_bytes = len(intended.catalog_bytes) + sum(
        len(document.content) for document in intended.documents
    )
    if intended_bytes > configuration.target.max_snapshot_bytes:
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
            "Favorites FTP intended snapshot exceeds its configured byte limit.",
        )
    try:
        password = _resolve_write_secret(
            configuration,
            secret_resolver,
        )
    except FavoritesFtpWriteTransportError as error:
        raise FavoritesFtpWritePreflightError(
            error.reason,
            error.message,
        ) from None
    try:
        session = session_factory(configuration, password)
    except FavoritesFtpWriteTransportError as error:
        raise FavoritesFtpWritePreflightError(
            error.reason,
            error.message,
        ) from None
    except Exception:
        raise FavoritesFtpWritePreflightError(
            FavoritesFtpWriteFailureReason.CONNECTION_FAILED,
            "Could not open the Favorites FTP write preflight session.",
        ) from None
    body_error: BaseException | None = None
    try:
        observation = _stable_observation(
            session,
            configuration,
        )
        if not plan.matches_baseline_snapshot(observation.snapshot):
            raise FavoritesFtpWritePreflightError(
                FavoritesFtpWriteFailureReason.TARGET_STALE,
                "Favorites FTP target does not match the write-plan baseline.",
            )
        token = _operation_token(plan, configuration)
        temporary = _temporary_names(token, additions)
        staged_listing_count = len(observation.listing) + len(additions) + 1
        if staged_listing_count > configuration.target.max_listing_entries:
            raise FavoritesFtpWritePreflightError(
                FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN,
                "Favorites FTP staged listing would exceed its configured limit.",
            )
        listing_casefold = {name.casefold() for name in observation.listing}
        claimed = (*temporary, *(document.filename for document in additions))
        if len({name.casefold() for name in claimed}) != len(claimed):
            raise FavoritesFtpWritePreflightError(
                FavoritesFtpWriteFailureReason.TARGET_UNSAFE,
                "Favorites FTP write names collide under case-insensitive storage.",
            )
        if any(name.casefold() in listing_casefold for name in claimed):
            raise FavoritesFtpWritePreflightError(
                FavoritesFtpWriteFailureReason.TARGET_UNSAFE,
                "Favorites FTP target contains a case-insensitive write-name collision.",
            )
        return FavoritesFtpWritePreflight(
            plan=plan,
            configuration=configuration,
            observed_listing=observation.listing,
            added_documents=additions,
            operation_token=token,
            temporary_names=temporary,
        )
    except FavoritesFtpWritePreflightError:
        body_error = RuntimeError()
        raise
    except FavoritesFtpWriteTransportError as error:
        body_error = error
        raise FavoritesFtpWritePreflightError(
            error.reason,
            error.message,
        ) from None
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            session.close()
        except FavoritesFtpWriteTransportError as error:
            if body_error is None:
                raise FavoritesFtpWritePreflightError(
                    error.reason,
                    error.message,
                ) from None
        except Exception:
            if body_error is None:
                raise FavoritesFtpWritePreflightError(
                    FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
                    "Could not close the Favorites FTP write preflight session.",
                ) from None


class FavoritesFtpWriteExecutionStatus(StrEnum):
    """Stable terminal states for one bounded FTP write attempt."""

    NOOP = "noop"
    COMPLETED = "completed"
    RECOVERED = "recovered"
    FAILED_BEFORE_MUTATION = "failed_before_mutation"
    RECOVERY_REQUIRED = "recovery_required"
    CLEANUP_REQUIRED = "cleanup_required"


@dataclass(frozen=True, slots=True)
class FavoritesFtpWriteExecutionResult:
    """Durable host evidence for one terminal FTP write attempt."""

    status: FavoritesFtpWriteExecutionStatus
    artifact_directory: Path
    backup_directory: Path
    manifest_path: Path
    report_path: Path
    baseline_snapshot_sha256: str
    intended_snapshot_sha256: str
    mutation_started: bool
    rollback_attempted: bool

    def __post_init__(self) -> None:
        if not isinstance(self.status, FavoritesFtpWriteExecutionStatus):
            raise TypeError(
                "Favorites FTP write result status must be FavoritesFtpWriteExecutionStatus."
            )
        for label, path in (
            ("artifact directory", self.artifact_directory),
            ("backup directory", self.backup_directory),
            ("manifest path", self.manifest_path),
            ("report path", self.report_path),
        ):
            if not isinstance(path, Path):
                raise TypeError(f"Favorites FTP write result {label} must be pathlib.Path.")
            if not path.is_absolute():
                raise ValueError(f"Favorites FTP write result {label} must be absolute.")
        for label, value in (
            ("baseline snapshot SHA-256", self.baseline_snapshot_sha256),
            ("intended snapshot SHA-256", self.intended_snapshot_sha256),
        ):
            if (
                len(value) != 64
                or value != value.lower()
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"Favorites FTP write result {label} is invalid.")
        if not isinstance(self.mutation_started, bool):
            raise TypeError("Favorites FTP write result mutation flag must be bool.")
        if not isinstance(self.rollback_attempted, bool):
            raise TypeError("Favorites FTP write result rollback flag must be bool.")


class FavoritesFtpWriteExecutionError(RuntimeError):
    """Report a failed FTP write together with its durable terminal evidence."""

    def __init__(
        self,
        reason: FavoritesFtpWriteFailureReason,
        message: str,
        result: FavoritesFtpWriteExecutionResult,
    ) -> None:
        if not isinstance(reason, FavoritesFtpWriteFailureReason):
            raise TypeError(
                "Favorites FTP write execution reason must be FavoritesFtpWriteFailureReason."
            )
        if not isinstance(message, str) or not message:
            raise ValueError("Favorites FTP write execution message must not be empty.")
        if not isinstance(result, FavoritesFtpWriteExecutionResult):
            raise TypeError(
                "Favorites FTP write execution error requires FavoritesFtpWriteExecutionResult."
            )
        self.reason = reason
        self.message = message
        self.result = result
        super().__init__(
            "Favorites FTP write execution failed "
            f"({reason.value}, {result.status.value}): {message}"
        )


@dataclass(frozen=True, slots=True)
class _ArtifactPaths:
    root: Path
    backup: Path
    manifest: Path
    report: Path


@dataclass(frozen=True, slots=True)
class _TargetLock:
    path: Path
    device: int
    inode: int


def _require_private_parent(path: Path) -> None:
    try:
        observed = path.lstat()
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not inspect the FTP write artifact parent.",
        ) from error
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "FTP write artifact parent must be a real directory.",
        )
    if hasattr(os, "getuid") and observed.st_uid != os.getuid():
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "FTP write artifact parent must be owned by the current user.",
        )
    if stat.S_IMODE(observed.st_mode) & 0o077:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "FTP write artifact parent must not grant group or other access.",
        )


def _target_lock_path(
    preflight: FavoritesFtpWritePreflight,
    parent: Path,
) -> Path:
    digest = hashlib.sha256()
    for value in (
        b"sds200-favorites-ftp-write-target-lock-v1",
        preflight.configuration.target.host.encode(),
        str(preflight.configuration.target.port).encode(),
        preflight.configuration.target.favorites_directory.encode(),
    ):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return parent / f".sdsctl-favorites-ftp-{digest.hexdigest()[:16]}.lock"


def _acquire_target_lock(
    preflight: FavoritesFtpWritePreflight,
    parent: Path,
) -> _TargetLock:
    _require_private_parent(parent)
    path = _target_lock_path(preflight, parent)
    try:
        path.mkdir(mode=0o700)
        observed = path.lstat()
    except FileExistsError:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.TARGET_UNSAFE,
            "Another Favorites FTP write may already own this host target lock.",
        ) from None
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not establish the Favorites FTP host target lock.",
        ) from error
    if not stat.S_ISDIR(observed.st_mode):
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Favorites FTP host target lock is not a directory.",
        )
    _fsync_directory(parent)
    return _TargetLock(
        path=path,
        device=observed.st_dev,
        inode=observed.st_ino,
    )


def _release_target_lock(lock: _TargetLock) -> None:
    try:
        observed = lock.path.lstat()
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
            "Could not inspect the Favorites FTP host target lock at release.",
        ) from error
    if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
        lock.device,
        lock.inode,
    ):
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
            "Favorites FTP host target lock changed before release.",
        )
    try:
        lock.path.rmdir()
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
            "Could not release the Favorites FTP host target lock.",
        ) from error
    _fsync_directory(lock.path.parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not open an FTP write artifact directory for synchronization.",
        ) from error
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not synchronize an FTP write artifact directory.",
        ) from error
    finally:
        os.close(descriptor)


def _write_exclusive_bytes(
    path: Path,
    content: bytes,
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not exclusively create one FTP write artifact.",
        ) from error
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("zero-length write")
            offset += written
        os.fsync(descriptor)
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or stat.S_IMODE(observed.st_mode) != 0o600:
            raise OSError("artifact mode or type changed")
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not durably write one FTP write artifact.",
        ) from error
    finally:
        os.close(descriptor)
    try:
        if path.read_bytes() != content:
            raise OSError("artifact readback differs")
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "FTP write artifact failed exact readback verification.",
        ) from error


def _canonical_json(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _replace_json(
    path: Path,
    payload: object,
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if os.path.lexists(temporary):
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "FTP write report temporary path already exists.",
        )
    _write_exclusive_bytes(temporary, _canonical_json(payload))
    try:
        os.replace(temporary, path)
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not atomically publish the FTP write report.",
        ) from error
    _fsync_directory(path.parent)


def _prepare_artifacts(
    preflight: FavoritesFtpWritePreflight,
    artifact_directory: Path,
) -> _ArtifactPaths:
    if not isinstance(artifact_directory, Path):
        raise TypeError("Favorites FTP write artifact directory must be pathlib.Path.")
    if not artifact_directory.is_absolute():
        raise ValueError("Favorites FTP write artifact directory must be absolute.")
    _require_private_parent(artifact_directory.parent)
    try:
        artifact_directory.mkdir(mode=0o700)
        backup = artifact_directory / "backup"
        backup.mkdir(mode=0o700)
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not exclusively create private FTP write artifacts.",
        ) from error
    paths = _ArtifactPaths(
        root=artifact_directory,
        backup=backup,
        manifest=artifact_directory / "rollback-manifest.json",
        report=artifact_directory / "operation-report.json",
    )
    baseline = preflight.plan.baseline_snapshot
    _write_exclusive_bytes(
        backup / "f_list.cfg",
        baseline.catalog_bytes,
    )
    for document in baseline.documents:
        _write_exclusive_bytes(
            backup / document.filename,
            document.content,
        )
    _fsync_directory(backup)
    manifest_payload = {
        "schema": _FAVORITES_FTP_WRITE_ARTIFACT_SCHEMA,
        "version": _FAVORITES_FTP_WRITE_ARTIFACT_VERSION,
        "operation_token": preflight.operation_token,
        "target": {
            "host": preflight.configuration.target.host,
            "port": preflight.configuration.target.port,
            "directory": preflight.configuration.target.favorites_directory,
        },
        "baseline_snapshot_sha256": favorites_storage_snapshot_sha256(baseline),
        "intended_snapshot_sha256": favorites_storage_snapshot_sha256(
            preflight.plan.intended_snapshot
        ),
        "observed_listing": list(preflight.observed_listing),
        "added_documents": [document.filename for document in preflight.added_documents],
        "temporary_names": list(preflight.temporary_names),
        "backup_retained": True,
    }
    _write_exclusive_bytes(
        paths.manifest,
        _canonical_json(manifest_payload),
    )
    _fsync_directory(artifact_directory)
    if _read_backup_snapshot(paths.backup) != baseline:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Verified FTP write host backup differs from the write-plan baseline.",
        )
    return paths


def _read_backup_snapshot(
    backup: Path,
) -> FavoritesStorageSnapshot:
    try:
        catalog = (backup / "f_list.cfg").read_bytes()
        documents = tuple(
            FavoritesStorageDocument(
                filename=path.name,
                content=path.read_bytes(),
            )
            for path in sorted(backup.glob("*.hpd"))
        )
    except OSError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.ARTIFACT_UNSAFE,
            "Could not read the verified FTP write host backup.",
        ) from error
    return FavoritesStorageSnapshot(
        catalog_bytes=catalog,
        documents=documents,
    )


def _result(
    *,
    preflight: FavoritesFtpWritePreflight,
    paths: _ArtifactPaths,
    status: FavoritesFtpWriteExecutionStatus,
    mutation_started: bool,
    rollback_attempted: bool,
) -> FavoritesFtpWriteExecutionResult:
    return FavoritesFtpWriteExecutionResult(
        status=status,
        artifact_directory=paths.root,
        backup_directory=paths.backup,
        manifest_path=paths.manifest,
        report_path=paths.report,
        baseline_snapshot_sha256=favorites_storage_snapshot_sha256(
            preflight.plan.baseline_snapshot
        ),
        intended_snapshot_sha256=favorites_storage_snapshot_sha256(
            preflight.plan.intended_snapshot
        ),
        mutation_started=mutation_started,
        rollback_attempted=rollback_attempted,
    )


def _write_report(
    result: FavoritesFtpWriteExecutionResult,
    *,
    failure_reason: FavoritesFtpWriteFailureReason | None = None,
) -> None:
    payload = {
        "schema": _FAVORITES_FTP_WRITE_ARTIFACT_SCHEMA,
        "version": _FAVORITES_FTP_WRITE_ARTIFACT_VERSION,
        "status": result.status.value,
        "failure_reason": (None if failure_reason is None else failure_reason.value),
        "baseline_snapshot_sha256": result.baseline_snapshot_sha256,
        "intended_snapshot_sha256": result.intended_snapshot_sha256,
        "mutation_started": result.mutation_started,
        "rollback_attempted": result.rollback_attempted,
        "backup_retained": True,
    }
    _replace_json(result.report_path, payload)


def _bounded_listing(
    session: FavoritesFtpWriteSession,
    configuration: FavoritesFtpWriteConfiguration,
) -> tuple[str, ...]:
    try:
        raw = session.list_names()
    except FavoritesFtpStorageError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.OBSERVATION_FAILED,
            "Could not list the Favorites FTP write target.",
        ) from error
    except FavoritesFtpWriteTransportError:
        raise
    except Exception:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.OBSERVATION_FAILED,
            "Could not list the Favorites FTP write target.",
        ) from None
    if not isinstance(raw, tuple):
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.TARGET_UNSAFE,
            "Favorites FTP write listing must be an immutable tuple.",
        )
    if len(raw) > configuration.target.max_listing_entries:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.TARGET_UNSAFE,
            "Favorites FTP write listing exceeds its configured limit.",
        )
    try:
        names = tuple(_validate_listing_name(name) for name in raw)
    except FavoritesFtpStorageError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.TARGET_UNSAFE,
            "Favorites FTP write listing contains an unsafe name.",
        ) from error
    if len(set(names)) != len(names):
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.TARGET_UNSAFE,
            "Favorites FTP write listing contains duplicate names.",
        )
    return tuple(sorted(names))


def _retrieve_exact(
    session: FavoritesFtpWriteSession,
    configuration: FavoritesFtpWriteConfiguration,
    filename: str,
    expected: bytes,
) -> None:
    maximum = (
        configuration.target.max_catalog_bytes
        if filename == "f_list.cfg" or "catalog" in filename
        else configuration.target.max_document_bytes
    )
    try:
        observed = session.retrieve_file(
            filename,
            max_bytes=maximum,
        )
    except FavoritesFtpStorageError as error:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.VERIFICATION_FAILED,
            "Could not retrieve one FTP write verification file.",
        ) from error
    except FavoritesFtpWriteTransportError:
        raise
    except Exception:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.VERIFICATION_FAILED,
            "Could not retrieve one FTP write verification file.",
        ) from None
    if not isinstance(observed, bytes) or observed != expected:
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.VERIFICATION_FAILED,
            "One FTP write file failed exact byte readback.",
        )


def _require_observation(
    session: FavoritesFtpWriteSession,
    preflight: FavoritesFtpWritePreflight,
    *,
    snapshot: FavoritesStorageSnapshot,
    listing: tuple[str, ...],
) -> None:
    observation = _stable_observation(
        session,
        preflight.configuration,
    )
    if observation.snapshot != snapshot or observation.listing != tuple(sorted(listing)):
        raise FavoritesFtpWriteTransportError(
            FavoritesFtpWriteFailureReason.TARGET_STALE,
            "Favorites FTP target changed outside the owned write sequence.",
        )


def _stage_remote(
    session: FavoritesFtpWriteSession,
    preflight: FavoritesFtpWritePreflight,
) -> tuple[tuple[str, FavoritesStorageDocument], ...]:
    document_temporary_names = preflight.temporary_names[: len(preflight.added_documents)]
    staged_documents = tuple(
        zip(
            document_temporary_names,
            preflight.added_documents,
            strict=True,
        )
    )
    for temporary, document in staged_documents:
        session.store_file(temporary, document.content)
        _retrieve_exact(
            session,
            preflight.configuration,
            temporary,
            document.content,
        )
    catalog_temporary = preflight.temporary_names[len(preflight.added_documents)]
    catalog = preflight.plan.intended_snapshot.catalog_bytes
    session.store_file(catalog_temporary, catalog)
    _retrieve_exact(
        session,
        preflight.configuration,
        catalog_temporary,
        catalog,
    )
    expected_listing = (
        *preflight.observed_listing,
        *(temporary for temporary, _ in staged_documents),
        catalog_temporary,
    )
    _require_observation(
        session,
        preflight,
        snapshot=preflight.plan.baseline_snapshot,
        listing=expected_listing,
    )
    return staged_documents


def _activate_remote(
    session: FavoritesFtpWriteSession,
    preflight: FavoritesFtpWritePreflight,
    staged_documents: tuple[tuple[str, FavoritesStorageDocument], ...],
) -> None:
    catalog_temporary = preflight.temporary_names[len(preflight.added_documents)]
    catalog_backup = preflight.temporary_names[len(preflight.added_documents) + 1]
    for temporary, document in staged_documents:
        session.rename_file(
            temporary,
            document.filename,
        )
        _retrieve_exact(
            session,
            preflight.configuration,
            document.filename,
            document.content,
        )
    session.rename_file(
        "f_list.cfg",
        catalog_backup,
    )
    try:
        session.rename_file(
            catalog_temporary,
            "f_list.cfg",
        )
    except BaseException:
        with suppress(Exception):
            session.rename_file(
                catalog_backup,
                "f_list.cfg",
            )
        raise
    expected_listing = (
        *preflight.observed_listing,
        *(document.filename for _, document in staged_documents),
        catalog_backup,
    )
    _require_observation(
        session,
        preflight,
        snapshot=preflight.plan.intended_snapshot,
        listing=expected_listing,
    )
    session.delete_file(catalog_backup)
    final_listing = (
        *preflight.observed_listing,
        *(document.filename for _, document in staged_documents),
    )
    _require_observation(
        session,
        preflight,
        snapshot=preflight.plan.intended_snapshot,
        listing=final_listing,
    )


def _delete_if_present(
    session: FavoritesFtpWriteSession,
    configuration: FavoritesFtpWriteConfiguration,
    filename: str,
) -> None:
    if filename in _bounded_listing(session, configuration):
        session.delete_file(filename)


def _rollback_remote(
    session: FavoritesFtpWriteSession,
    preflight: FavoritesFtpWritePreflight,
) -> None:
    configuration = preflight.configuration
    baseline = preflight.plan.baseline_snapshot
    additions = tuple(document.filename for document in preflight.added_documents)
    catalog_old = preflight.temporary_names[len(preflight.added_documents) + 2]
    recovery = preflight.temporary_names[len(preflight.added_documents) + 3]
    names = _bounded_listing(session, configuration)
    catalog_is_baseline = False
    if "f_list.cfg" in names:
        try:
            _retrieve_exact(
                session,
                configuration,
                "f_list.cfg",
                baseline.catalog_bytes,
            )
        except FavoritesFtpWriteTransportError:
            catalog_is_baseline = False
        else:
            catalog_is_baseline = True
    if not catalog_is_baseline:
        _delete_if_present(session, configuration, recovery)
        session.store_file(recovery, baseline.catalog_bytes)
        _retrieve_exact(
            session,
            configuration,
            recovery,
            baseline.catalog_bytes,
        )
        if "f_list.cfg" in _bounded_listing(session, configuration):
            _delete_if_present(session, configuration, catalog_old)
            session.rename_file(
                "f_list.cfg",
                catalog_old,
            )
        try:
            session.rename_file(
                recovery,
                "f_list.cfg",
            )
        except BaseException:
            with suppress(Exception):
                if catalog_old in _bounded_listing(session, configuration):
                    session.rename_file(
                        catalog_old,
                        "f_list.cfg",
                    )
            raise
    for filename in additions:
        _delete_if_present(
            session,
            configuration,
            filename,
        )
    for temporary in preflight.temporary_names:
        _delete_if_present(
            session,
            configuration,
            temporary,
        )
    _require_observation(
        session,
        preflight,
        snapshot=baseline,
        listing=preflight.observed_listing,
    )


def _execute_favorites_ftp_write_locked(
    preflight: FavoritesFtpWritePreflight,
    artifact_directory: Path,
    *,
    session_factory: FavoritesFtpWriteSessionFactory = _ftplib_write_session_factory,
    secret_resolver: FavoritesFtpSecretResolver = _environment_secret_resolver,
) -> FavoritesFtpWriteExecutionResult:
    """Back up, stage, verify, activate, and recover one additive FTP write."""

    if not isinstance(preflight, FavoritesFtpWritePreflight):
        raise TypeError("Favorites FTP write execution requires FavoritesFtpWritePreflight.")
    if not callable(session_factory):
        raise TypeError("Favorites FTP write session factory must be callable.")
    if not callable(secret_resolver):
        raise TypeError("Favorites FTP write secret resolver must be callable.")
    paths = _prepare_artifacts(
        preflight,
        artifact_directory,
    )
    if preflight.is_noop:
        result = _result(
            preflight=preflight,
            paths=paths,
            status=FavoritesFtpWriteExecutionStatus.NOOP,
            mutation_started=False,
            rollback_attempted=False,
        )
        _write_report(result)
        return result
    mutation_started = False
    rollback_attempted = False
    session: FavoritesFtpWriteSession | None = None
    terminal_error: FavoritesFtpWriteExecutionError | None = None
    success: FavoritesFtpWriteExecutionResult | None = None
    try:
        password = _resolve_write_secret(
            preflight.configuration,
            secret_resolver,
        )
        try:
            session = session_factory(
                preflight.configuration,
                password,
            )
        except FavoritesFtpWriteTransportError:
            raise
        except Exception:
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.CONNECTION_FAILED,
                "Could not open the Favorites FTP write execution session.",
            ) from None
        _require_observation(
            session,
            preflight,
            snapshot=preflight.plan.baseline_snapshot,
            listing=preflight.observed_listing,
        )
        mutation_started = True
        staged = _stage_remote(
            session,
            preflight,
        )
        _activate_remote(
            session,
            preflight,
            staged,
        )
        success = _result(
            preflight=preflight,
            paths=paths,
            status=FavoritesFtpWriteExecutionStatus.COMPLETED,
            mutation_started=True,
            rollback_attempted=False,
        )
    except BaseException as error:
        if isinstance(error, FavoritesFtpWriteTransportError):
            reason = error.reason
            message = error.message
        else:
            reason = FavoritesFtpWriteFailureReason.ACTIVATION_FAILED
            message = "Favorites FTP write failed inside the guarded mutation sequence."
        if mutation_started and session is not None:
            rollback_attempted = True
            try:
                _rollback_remote(
                    session,
                    preflight,
                )
            except BaseException:
                result = _result(
                    preflight=preflight,
                    paths=paths,
                    status=FavoritesFtpWriteExecutionStatus.RECOVERY_REQUIRED,
                    mutation_started=True,
                    rollback_attempted=True,
                )
                _write_report(
                    result,
                    failure_reason=FavoritesFtpWriteFailureReason.ROLLBACK_FAILED,
                )
                terminal_error = FavoritesFtpWriteExecutionError(
                    FavoritesFtpWriteFailureReason.ROLLBACK_FAILED,
                    "FTP write failed and automatic rollback could not be verified.",
                    result,
                )
            else:
                result = _result(
                    preflight=preflight,
                    paths=paths,
                    status=FavoritesFtpWriteExecutionStatus.RECOVERED,
                    mutation_started=True,
                    rollback_attempted=True,
                )
                _write_report(
                    result,
                    failure_reason=reason,
                )
                terminal_error = FavoritesFtpWriteExecutionError(
                    reason,
                    (
                        "FTP write did not complete; the exact baseline was "
                        "automatically restored and verified."
                    ),
                    result,
                )
        else:
            result = _result(
                preflight=preflight,
                paths=paths,
                status=FavoritesFtpWriteExecutionStatus.FAILED_BEFORE_MUTATION,
                mutation_started=False,
                rollback_attempted=False,
            )
            _write_report(
                result,
                failure_reason=reason,
            )
            terminal_error = FavoritesFtpWriteExecutionError(
                reason,
                message,
                result,
            )
    finally:
        if session is not None:
            try:
                session.close()
            except BaseException:
                if terminal_error is None:
                    result = _result(
                        preflight=preflight,
                        paths=paths,
                        status=FavoritesFtpWriteExecutionStatus.CLEANUP_REQUIRED,
                        mutation_started=mutation_started,
                        rollback_attempted=rollback_attempted,
                    )
                    _write_report(
                        result,
                        failure_reason=FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
                    )
                    terminal_error = FavoritesFtpWriteExecutionError(
                        FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
                        (
                            "FTP write reached a verified terminal remote state, "
                            "but session cleanup failed."
                        ),
                        result,
                    )
    if terminal_error is not None:
        raise terminal_error
    if success is None:
        raise RuntimeError("Favorites FTP write execution did not produce a terminal result.")
    _write_report(success)
    return success


def execute_favorites_ftp_write(
    preflight: FavoritesFtpWritePreflight,
    artifact_directory: Path,
    *,
    session_factory: FavoritesFtpWriteSessionFactory = _ftplib_write_session_factory,
    secret_resolver: FavoritesFtpSecretResolver = _environment_secret_resolver,
) -> FavoritesFtpWriteExecutionResult:
    """Execute one additive FTP write under the private host target lock."""

    if not isinstance(preflight, FavoritesFtpWritePreflight):
        raise TypeError("Favorites FTP write execution requires FavoritesFtpWritePreflight.")
    if not isinstance(artifact_directory, Path):
        raise TypeError("Favorites FTP write artifact directory must be pathlib.Path.")
    if not artifact_directory.is_absolute():
        raise ValueError("Favorites FTP write artifact directory must be absolute.")
    lock = _acquire_target_lock(
        preflight,
        artifact_directory.parent,
    )
    primary_error: BaseException | None = None
    result: FavoritesFtpWriteExecutionResult | None = None
    try:
        result = _execute_favorites_ftp_write_locked(
            preflight,
            artifact_directory,
            session_factory=session_factory,
            secret_resolver=secret_resolver,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            _release_target_lock(lock)
        except FavoritesFtpWriteTransportError:
            if primary_error is None:
                if result is None:
                    raise
                cleanup_result = _result(
                    preflight=preflight,
                    paths=_ArtifactPaths(
                        root=result.artifact_directory,
                        backup=result.backup_directory,
                        manifest=result.manifest_path,
                        report=result.report_path,
                    ),
                    status=FavoritesFtpWriteExecutionStatus.CLEANUP_REQUIRED,
                    mutation_started=result.mutation_started,
                    rollback_attempted=result.rollback_attempted,
                )
                _write_report(
                    cleanup_result,
                    failure_reason=FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
                )
                raise FavoritesFtpWriteExecutionError(
                    FavoritesFtpWriteFailureReason.CLEANUP_FAILED,
                    (
                        "FTP write reached a verified terminal remote state, "
                        "but the private host target lock could not be released."
                    ),
                    cleanup_result,
                ) from None
    if result is None:
        raise RuntimeError("Favorites FTP write execution did not produce a terminal result.")
    return result


__all__ = [
    "FAVORITES_FTP_WRITE_MAX_ADDITIONS",
    "FavoritesFtpWriteConfiguration",
    "FavoritesFtpWriteCredential",
    "FavoritesFtpWriteExecutionError",
    "FavoritesFtpWriteExecutionResult",
    "FavoritesFtpWriteExecutionStatus",
    "FavoritesFtpWriteFailureReason",
    "FavoritesFtpWritePreflight",
    "FavoritesFtpWritePreflightError",
    "FavoritesFtpWriteSession",
    "FavoritesFtpWriteSessionFactory",
    "FavoritesFtpWriteTransportError",
    "execute_favorites_ftp_write",
    "preflight_favorites_ftp_write",
]
