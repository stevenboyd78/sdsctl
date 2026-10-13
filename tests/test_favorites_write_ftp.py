from __future__ import annotations

import ftplib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pytest

import sds200.favorites_write_ftp as ftp_write
from sds200.favorites_storage import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
)
from sds200.favorites_storage_ftp import (
    FavoritesFtpReadConfiguration,
    FavoritesFtpReadCredential,
)
from sds200.favorites_write_ftp import (
    FAVORITES_FTP_WRITE_MAX_ADDITIONS,
    FavoritesFtpWriteConfiguration,
    FavoritesFtpWriteCredential,
    FavoritesFtpWriteExecutionError,
    FavoritesFtpWriteExecutionStatus,
    FavoritesFtpWriteFailureReason,
    FavoritesFtpWritePreflightError,
    FavoritesFtpWriteSession,
    FavoritesFtpWriteTransportError,
    execute_favorites_ftp_write,
    preflight_favorites_ftp_write,
)
from sds200.favorites_write_plan import FavoritesWritePlan, plan_favorites_write

_READ_SECRET = "SDSCTL_TEST_FTP_READ_PASSWORD"
_WRITE_SECRET = "SDSCTL_TEST_FTP_WRITE_PASSWORD"
_WRITE_PASSWORD = "synthetic-write-password"


def _configuration(
    **overrides: object,
) -> FavoritesFtpWriteConfiguration:
    values: dict[str, object] = {
        "target": FavoritesFtpReadConfiguration(
            host="192.0.2.20",
            favorites_directory="/favorites_lists",
            credential=FavoritesFtpReadCredential(
                username="reader",
                password_environment_variable=_READ_SECRET,
            ),
        ),
        "credential": FavoritesFtpWriteCredential(
            username="writer",
            password_environment_variable=_WRITE_SECRET,
        ),
    }
    values.update(overrides)
    return FavoritesFtpWriteConfiguration(
        **values  # type: ignore[arg-type]
    )


def _plan() -> FavoritesWritePlan:
    fixture = Path(__file__).parent / "fixtures" / "favorites"
    catalog = (fixture / "synthetic-f_list.cfg").read_bytes()
    hpd = (fixture / "synthetic-favorites.hpd").read_bytes()
    lines = catalog.splitlines(keepends=True)
    fields = lines[2].removesuffix(b"\r\n").split(b"\t")
    fields[1] = b"Added Favorites"
    fields[2] = b"f_000002.hpd"
    added_catalog_row = b"\t".join(fields) + b"\r\n"
    baseline = FavoritesStorageSnapshot(
        catalog_bytes=catalog,
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=hpd,
            ),
        ),
    )
    intended = FavoritesStorageSnapshot(
        catalog_bytes=catalog + added_catalog_row,
        documents=(
            *baseline.documents,
            FavoritesStorageDocument(
                filename="f_000002.hpd",
                content=hpd,
            ),
        ),
    )
    return plan_favorites_write(
        baseline,
        intended,
    )


class _Remote:
    def __init__(
        self,
        snapshot: FavoritesStorageSnapshot,
    ) -> None:
        self.files = {
            "f_list.cfg": snapshot.catalog_bytes,
            **{document.filename: document.content for document in snapshot.documents},
            "scanner-owned.txt": b"preserve",
        }
        self.actions: list[tuple[object, ...]] = []
        self.fail_rename_to: str | None = None
        self.fail_rename_count = 0
        self.fail_store_name: str | None = None
        self.fail_store_untyped_name: str | None = None
        self.close_error = False


class _Session:
    def __init__(
        self,
        remote: _Remote,
    ) -> None:
        self.remote = remote
        self.closed = False

    def list_names(self) -> tuple[str, ...]:
        self.remote.actions.append(("list",))
        return tuple(sorted(self.remote.files))

    def retrieve_file(
        self,
        filename: str,
        *,
        max_bytes: int,
    ) -> bytes:
        self.remote.actions.append(("retrieve", filename, max_bytes))
        value = self.remote.files[filename]
        if len(value) > max_bytes:
            raise AssertionError("fake transfer exceeded requested bound")
        return value

    def store_file(
        self,
        filename: str,
        content: bytes,
    ) -> None:
        self.remote.actions.append(("store", filename, content))
        if filename == self.remote.fail_store_name:
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.STORE_FAILED,
                "Synthetic store failure.",
            )
        self.remote.files[filename] = content
        if filename == self.remote.fail_store_untyped_name:
            raise RuntimeError("synthetic uncertain store return")

    def rename_file(
        self,
        source: str,
        destination: str,
    ) -> None:
        self.remote.actions.append(("rename", source, destination))
        if destination == self.remote.fail_rename_to and self.remote.fail_rename_count != 0:
            if self.remote.fail_rename_count > 0:
                self.remote.fail_rename_count -= 1
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.RENAME_FAILED,
                "Synthetic rename failure.",
            )
        if destination in self.remote.files:
            raise FavoritesFtpWriteTransportError(
                FavoritesFtpWriteFailureReason.RENAME_FAILED,
                "Synthetic destination already exists.",
            )
        self.remote.files[destination] = self.remote.files.pop(source)

    def delete_file(
        self,
        filename: str,
    ) -> None:
        self.remote.actions.append(("delete", filename))
        del self.remote.files[filename]

    def close(self) -> None:
        self.closed = True
        self.remote.actions.append(("close",))
        if self.remote.close_error:
            raise RuntimeError("synthetic close failure")


class _Factory:
    def __init__(
        self,
        remote: _Remote,
    ) -> None:
        self.remote = remote
        self.calls: list[tuple[FavoritesFtpWriteConfiguration, str]] = []
        self.sessions: list[_Session] = []

    def __call__(
        self,
        configuration: FavoritesFtpWriteConfiguration,
        password: str,
    ) -> FavoritesFtpWriteSession:
        self.calls.append((configuration, password))
        session = _Session(self.remote)
        self.sessions.append(session)
        return session


def _preflight(
    plan: FavoritesWritePlan,
    remote: _Remote,
    *,
    configuration: FavoritesFtpWriteConfiguration | None = None,
) -> tuple[ftp_write.FavoritesFtpWritePreflight, _Factory]:
    factory = _Factory(remote)
    preflight = preflight_favorites_ftp_write(
        plan,
        _configuration() if configuration is None else configuration,
        session_factory=factory,
        secret_resolver=lambda variable: (
            _WRITE_PASSWORD
            if variable == _WRITE_SECRET
            else pytest.fail("write workflow requested the read secret")
        ),
    )
    return preflight, factory


def _private_operation_path(
    tmp_path: Path,
) -> Path:
    tmp_path.chmod(0o700)
    return tmp_path / "operation"


def _assert_remote_snapshot(
    remote: _Remote,
    snapshot: FavoritesStorageSnapshot,
) -> None:
    assert remote.files["f_list.cfg"] == snapshot.catalog_bytes
    assert {name: content for name, content in remote.files.items() if name.endswith(".hpd")} == {
        document.filename: document.content for document in snapshot.documents
    }
    assert remote.files["scanner-owned.txt"] == b"preserve"


def test_ftp_write_configuration_requires_distinct_secret_references() -> None:
    target = _configuration().target
    with pytest.raises(ValueError):
        FavoritesFtpWriteConfiguration(
            target=target,
            credential=FavoritesFtpWriteCredential(
                username="writer",
                password_environment_variable=_READ_SECRET,
            ),
        )


def test_ftp_write_configuration_bounds_additions() -> None:
    assert FAVORITES_FTP_WRITE_MAX_ADDITIONS == 16
    with pytest.raises(ValueError):
        _configuration(max_additions=17)
    with pytest.raises(TypeError):
        _configuration(max_additions=True)


def test_ftp_write_preflight_is_read_only_and_uses_only_write_secret() -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    before = dict(remote.files)

    preflight, factory = _preflight(plan, remote)

    assert preflight.plan is plan
    assert [document.filename for document in preflight.added_documents] == ["f_000002.hpd"]
    assert preflight.is_noop is False
    assert remote.files == before
    assert factory.calls == [(preflight.configuration, _WRITE_PASSWORD)]
    assert factory.sessions[0].closed is True
    assert {action[0] for action in remote.actions} == {"list", "retrieve", "close"}


def test_ftp_write_preflight_missing_write_secret_fails_before_session() -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    factory = _Factory(remote)

    with pytest.raises(FavoritesFtpWritePreflightError) as captured:
        preflight_favorites_ftp_write(
            plan,
            _configuration(),
            session_factory=factory,
            secret_resolver=lambda variable: (_ for _ in ()).throw(KeyError(variable)),
        )

    assert captured.value.reason is FavoritesFtpWriteFailureReason.CREDENTIAL_UNAVAILABLE
    assert factory.calls == []
    assert _WRITE_PASSWORD not in str(captured.value)


def test_ftp_write_preflight_rejects_case_insensitive_collision() -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    remote.files["F_000002.HPD"] = b"collision"

    with pytest.raises(FavoritesFtpWritePreflightError) as captured:
        _preflight(plan, remote)

    assert captured.value.reason is FavoritesFtpWriteFailureReason.TARGET_UNSAFE
    assert not any(action[0] in {"store", "rename", "delete"} for action in remote.actions)


def test_ftp_write_preflight_rejects_stale_target() -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    remote.files["f_list.cfg"] += b"changed"

    with pytest.raises(FavoritesFtpWritePreflightError) as captured:
        _preflight(plan, remote)

    assert captured.value.reason is FavoritesFtpWriteFailureReason.TARGET_STALE


def test_ftp_write_preflight_rejects_existing_document_replacement() -> None:
    plan = _plan()
    changed = FavoritesStorageSnapshot(
        catalog_bytes=plan.intended_snapshot.catalog_bytes,
        documents=(
            FavoritesStorageDocument(
                filename="f_000001.hpd",
                content=plan.baseline_snapshot.documents[0].content + b"x",
            ),
            plan.intended_snapshot.documents[1],
        ),
    )
    replacement_plan = plan_favorites_write(
        plan.baseline_snapshot,
        changed,
    )
    remote = _Remote(plan.baseline_snapshot)

    with pytest.raises(FavoritesFtpWritePreflightError) as captured:
        _preflight(replacement_plan, remote)

    assert captured.value.reason is FavoritesFtpWriteFailureReason.UNSUPPORTED_PLAN
    assert remote.actions == []


def test_ftp_write_execution_backs_up_stages_activates_and_verifies(
    tmp_path: Path,
) -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    remote.actions.clear()

    result = execute_favorites_ftp_write(
        preflight,
        _private_operation_path(tmp_path),
        session_factory=factory,
        secret_resolver=lambda variable: _WRITE_PASSWORD,
    )

    assert result.status is FavoritesFtpWriteExecutionStatus.COMPLETED
    assert result.mutation_started is True
    assert result.rollback_attempted is False
    _assert_remote_snapshot(remote, plan.intended_snapshot)
    assert not any(name.startswith(f"sdsctl_{preflight.operation_token}_") for name in remote.files)
    assert (result.backup_directory / "f_list.cfg").read_bytes() == (
        plan.baseline_snapshot.catalog_bytes
    )
    assert (result.backup_directory / "f_000001.hpd").read_bytes() == (
        plan.baseline_snapshot.documents[0].content
    )
    assert stat_mode(result.artifact_directory) == 0o700
    assert stat_mode(result.backup_directory) == 0o700
    assert stat_mode(result.manifest_path) == 0o600
    assert stat_mode(result.report_path) == 0o600
    report = json.loads(result.report_path.read_text())
    assert report["status"] == "completed"
    assert report["backup_retained"] is True
    renames = [
        cast(tuple[str, str, str], action) for action in remote.actions if action[0] == "rename"
    ]
    document_rename = next(
        index for index, action in enumerate(renames) if action[2] == "f_000002.hpd"
    )
    catalog_backup = next(
        index for index, action in enumerate(renames) if action[1] == "f_list.cfg"
    )
    catalog_activation = next(
        index for index, action in enumerate(renames) if action[2] == "f_list.cfg"
    )
    assert document_rename < catalog_backup < catalog_activation


def test_ftp_write_execution_noop_creates_evidence_without_write_session(
    tmp_path: Path,
) -> None:
    additive_plan = _plan()
    plan = plan_favorites_write(
        additive_plan.baseline_snapshot,
        additive_plan.baseline_snapshot,
    )
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    session_count = len(factory.sessions)
    remote.actions.clear()

    result = execute_favorites_ftp_write(
        preflight,
        _private_operation_path(tmp_path),
        session_factory=factory,
        secret_resolver=lambda variable: pytest.fail("no-op execution must not resolve a secret"),
    )

    assert result.status is FavoritesFtpWriteExecutionStatus.NOOP
    assert result.mutation_started is False
    assert len(factory.sessions) == session_count
    assert remote.actions == []
    _assert_remote_snapshot(remote, plan.baseline_snapshot)
    assert json.loads(result.report_path.read_text())["status"] == "noop"


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_ftp_write_execution_refuses_drift_before_mutation(
    tmp_path: Path,
) -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    remote.files["new-foreign-file.txt"] = b"changed"
    remote.actions.clear()

    with pytest.raises(FavoritesFtpWriteExecutionError) as captured:
        execute_favorites_ftp_write(
            preflight,
            _private_operation_path(tmp_path),
            session_factory=factory,
            secret_resolver=lambda variable: _WRITE_PASSWORD,
        )

    assert captured.value.result.status is FavoritesFtpWriteExecutionStatus.FAILED_BEFORE_MUTATION
    assert captured.value.result.mutation_started is False
    assert not any(action[0] in {"store", "rename", "delete"} for action in remote.actions)


def test_ftp_write_execution_refuses_an_existing_host_target_lock(
    tmp_path: Path,
) -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    operation = _private_operation_path(tmp_path)
    lock_path = ftp_write._target_lock_path(  # noqa: SLF001
        preflight,
        operation.parent,
    )
    lock_path.mkdir(mode=0o700)
    call_count = len(factory.calls)
    remote.actions.clear()

    with pytest.raises(FavoritesFtpWriteTransportError) as captured:
        execute_favorites_ftp_write(
            preflight,
            operation,
            session_factory=factory,
            secret_resolver=lambda variable: _WRITE_PASSWORD,
        )

    assert captured.value.reason is FavoritesFtpWriteFailureReason.TARGET_UNSAFE
    assert len(factory.calls) == call_count
    assert remote.actions == []
    assert not operation.exists()


def test_ftp_write_execution_recovers_catalog_switch_failure(
    tmp_path: Path,
) -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    remote.fail_rename_to = "f_list.cfg"
    remote.fail_rename_count = 1
    remote.actions.clear()

    with pytest.raises(FavoritesFtpWriteExecutionError) as captured:
        execute_favorites_ftp_write(
            preflight,
            _private_operation_path(tmp_path),
            session_factory=factory,
            secret_resolver=lambda variable: _WRITE_PASSWORD,
        )

    assert captured.value.reason is FavoritesFtpWriteFailureReason.RENAME_FAILED
    assert captured.value.result.status is FavoritesFtpWriteExecutionStatus.RECOVERED
    assert captured.value.result.rollback_attempted is True
    _assert_remote_snapshot(remote, plan.baseline_snapshot)
    assert tuple(sorted(remote.files)) == preflight.observed_listing
    report = json.loads(captured.value.result.report_path.read_text())
    assert report["status"] == "recovered"
    assert report["failure_reason"] == "rename_failed"


def test_ftp_write_execution_recovers_an_untyped_uncertain_store_failure(
    tmp_path: Path,
) -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    remote.fail_store_untyped_name = preflight.temporary_names[0]

    with pytest.raises(FavoritesFtpWriteExecutionError) as captured:
        execute_favorites_ftp_write(
            preflight,
            _private_operation_path(tmp_path),
            session_factory=factory,
            secret_resolver=lambda variable: _WRITE_PASSWORD,
        )

    assert captured.value.reason is FavoritesFtpWriteFailureReason.ACTIVATION_FAILED
    assert captured.value.result.status is FavoritesFtpWriteExecutionStatus.RECOVERED
    _assert_remote_snapshot(remote, plan.baseline_snapshot)
    assert tuple(sorted(remote.files)) == preflight.observed_listing


def test_ftp_write_execution_preserves_backup_when_rollback_fails(
    tmp_path: Path,
) -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    remote.fail_rename_to = "f_list.cfg"
    remote.fail_rename_count = -1

    with pytest.raises(FavoritesFtpWriteExecutionError) as captured:
        execute_favorites_ftp_write(
            preflight,
            _private_operation_path(tmp_path),
            session_factory=factory,
            secret_resolver=lambda variable: _WRITE_PASSWORD,
        )

    assert captured.value.reason is FavoritesFtpWriteFailureReason.ROLLBACK_FAILED
    assert captured.value.result.status is FavoritesFtpWriteExecutionStatus.RECOVERY_REQUIRED
    assert captured.value.result.manifest_path.is_file()
    assert captured.value.result.backup_directory.is_dir()
    assert (captured.value.result.backup_directory / "f_list.cfg").read_bytes() == (
        plan.baseline_snapshot.catalog_bytes
    )


def test_ftp_write_execution_surfaces_cleanup_failure_after_verified_success(
    tmp_path: Path,
) -> None:
    plan = _plan()
    remote = _Remote(plan.baseline_snapshot)
    preflight, factory = _preflight(plan, remote)
    remote.close_error = True

    with pytest.raises(FavoritesFtpWriteExecutionError) as captured:
        execute_favorites_ftp_write(
            preflight,
            _private_operation_path(tmp_path),
            session_factory=factory,
            secret_resolver=lambda variable: _WRITE_PASSWORD,
        )

    assert captured.value.result.status is FavoritesFtpWriteExecutionStatus.CLEANUP_REQUIRED
    _assert_remote_snapshot(remote, plan.intended_snapshot)


class _FakeFtplib:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.files = {
            "f_list.cfg": b"catalog",
        }

    def connect(
        self,
        host: str,
        port: int,
        timeout: float,
    ) -> None:
        self.calls.append(("connect", host, port, timeout))

    def login(
        self,
        user: str,
        passwd: str,
    ) -> None:
        self.calls.append(("login", user, passwd))

    def cwd(self, directory: str) -> None:
        self.calls.append(("cwd", directory))

    def nlst(self) -> list[str]:
        self.calls.append(("nlst",))
        return sorted(self.files)

    def retrbinary(
        self,
        command: str,
        callback: Callable[[bytes], None],
        *,
        blocksize: int,
    ) -> None:
        self.calls.append(("retrbinary", command, blocksize))
        callback(self.files[command.removeprefix("RETR ")])

    def storbinary(
        self,
        command: str,
        source: object,
        *,
        blocksize: int,
    ) -> None:
        self.calls.append(("storbinary", command, blocksize))
        self.files[command.removeprefix("STOR ")] = cast(BytesReader, source).read()

    def rename(
        self,
        source: str,
        destination: str,
    ) -> None:
        self.calls.append(("rename", source, destination))
        self.files[destination] = self.files.pop(source)

    def delete(
        self,
        filename: str,
    ) -> None:
        self.calls.append(("delete", filename))
        del self.files[filename]

    def quit(self) -> None:
        self.calls.append(("quit",))

    def close(self) -> None:
        self.calls.append(("close",))


class BytesReader(Protocol):
    def read(self) -> bytes: ...


def test_ftplib_write_adapter_uses_explicit_mutating_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeFtplib()
    monkeypatch.setattr(
        ftplib,
        "FTP",
        lambda: fake,
    )
    configuration = _configuration()

    session = ftp_write._ftplib_write_session_factory(  # noqa: SLF001
        configuration,
        _WRITE_PASSWORD,
    )
    session.store_file("stage.tmp", b"payload")
    assert (
        session.retrieve_file(
            "stage.tmp",
            max_bytes=32,
        )
        == b"payload"
    )
    session.rename_file(
        "stage.tmp",
        "active.tmp",
    )
    session.delete_file("active.tmp")
    session.close()

    assert ("login", "writer", _WRITE_PASSWORD) in fake.calls
    assert ("cwd", "/favorites_lists") in fake.calls
    assert (
        "storbinary",
        "STOR stage.tmp",
        ftp_write._FAVORITES_FTP_WRITE_TRANSFER_BLOCK_SIZE,  # noqa: SLF001
    ) in fake.calls
    assert ("rename", "stage.tmp", "active.tmp") in fake.calls
    assert ("delete", "active.tmp") in fake.calls
    assert ("quit",) in fake.calls
    assert not any(call[:2] == ("login", "reader") for call in fake.calls)
