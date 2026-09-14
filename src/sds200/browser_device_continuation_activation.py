"""Internal stopped-owner activation workflow. No CLI, native action or sign-in.

Fixture-only until current permission and epoch-bound approvals are implemented.
This creates an immutable private manifest and commits its paused native anchor.
It never changes browser storage, clears a guard, replaces credentials or talks
to a server. Uncertain artifacts are retained; confirmation never retries apply.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import browser_device_continuation_native as native
from .browser_device_continuation_history import _capture_owned_history
from .browser_device_continuation_intent import ACTIVATION_MANIFEST
from .browser_device_continuation_ownership import _HistoryOwnership, _stopped_history_ownership
from .browser_device_guard_release import _inode, _present
from .browser_device_handoff import BrowserRecoveryHandoff
from .browser_device_native import _private_read
from .browser_device_recovery import RecoveryMode
from .browser_device_registration import _matches
from .browser_device_resume import _hex, _timestamp
from .browser_device_resume_archive import _encoded
from .browser_device_store import BrowserDeviceStore

_SIDECARS = ("-journal", "-wal", "-shm")


def _binding(path: Path) -> tuple[int, int]:
    device, inode = _inode(path)
    return device, inode


class BrowserPausedActivationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Paused activation was refused or its outcome is unconfirmed. "
                         "Keep the profile and all evidence. Do not retry, delete or repair state; "
                         "use exact read-only confirmation. No sign-in permission was established.")


@dataclass(frozen=True, slots=True)
class BrowserPausedActivationReview:
    directory: Path = field(repr=False)
    profile: Path = field(repr=False)
    origin: str = field(repr=False)
    device_id: str = field(repr=False)
    epoch: str = field(repr=False)
    confirmation: str = field(repr=False)
    native_revision: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class BrowserPausedActivationEvidence:
    """Exact committed paused checkpoint, not ongoing current permission."""

    identity: str = field(repr=False)
    epoch: str = field(repr=False)
    manifest_sha256: str = field(repr=False)
    native_revision: int
    mode: RecoveryMode = RecoveryMode.PAUSED


def _no_sidecars(path: Path) -> None:
    if any(_present(Path(str(path) + suffix)) for suffix in _SIDECARS):
        raise ValueError()


def _absent(root: Path) -> None:
    path = root / ACTIVATION_MANIFEST
    if _present(path):
        raise ValueError()
    _no_sidecars(path)


def _manifest_check(path: Path, raw: bytes, binding: tuple[int, int]) -> None:
    _no_sidecars(path)
    if tuple(_inode(path)) != binding:
        raise ValueError()
    _matches(path, raw)
    if tuple(_inode(path)) != binding:
        raise ValueError()
    _no_sidecars(path)


def _create_manifest(root: Path, raw: bytes) -> tuple[int, int]:
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open(ACTIVATION_MANIFEST, flags, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as stream:
            info = os.fstat(stream.fileno())
            binding = info.st_dev, info.st_ino
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _manifest_check(root / ACTIVATION_MANIFEST, raw, binding)
        os.fsync(directory)
        _manifest_check(root / ACTIVATION_MANIFEST, raw, binding)
        return binding
    finally:
        os.close(directory)


class _LedgerFile:
    def __init__(self, profile: Path) -> None:
        self.path = profile / "recovery.sqlite"
        self.binding = _binding(self.path)
        self.check_identity()
        _no_sidecars(self.path)

    def check_identity(self) -> None:
        """Fixed rollback-mode file only; never open SQL or ignore a history journal."""
        BrowserDeviceStore(self.path)._check()
        if tuple(_inode(self.path)) != self.binding:
            raise ValueError()
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            info = os.fstat(stream.fileno())
            header = stream.read(20)
            if ((info.st_dev, info.st_ino) != self.binding or len(header) != 20
                    or header[:16] != b"SQLite format 3\0" or header[18:] != b"\1\1"):
                raise ValueError()
        if (tuple(_inode(self.path)) != self.binding
                or any(_present(Path(str(self.path) + suffix)) for suffix in ("-wal", "-shm"))):
            raise ValueError()


@contextmanager
def _ledger_transaction(profile: Path, *, readonly: bool
                        ) -> Iterator[tuple[sqlite3.Connection, _LedgerFile]]:
    ledger = _LedgerFile(profile)
    mode = "ro" if readonly else "rw"
    with closing(sqlite3.connect(ledger.path.as_uri() + "?mode=" + mode,
                                 uri=True, timeout=0)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        if readonly:
            db.execute("PRAGMA query_only=ON")
        else:
            db.execute("PRAGMA synchronous=EXTRA")
        # Inspect, never switch an unsupported WAL database to DELETE.
        db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
        native._transaction(db, profile, readonly=readonly)
        ledger.check_identity()
        _no_sidecars(ledger.path)
        yield db, ledger
        # No automatic commit on scope exit, including after caller exceptions.


class BrowserPausedActivation:
    """Fresh same-process local consent; inaccessible from ordinary runtime paths."""

    def __init__(self, handoff: BrowserRecoveryHandoff, *, release_id: str, intent_id: str,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._handoff = handoff
        self._selection = dict(release_id=release_id, intent_id=intent_id)
        self._clock, self._monotonic = clock, monotonic
        self._attempted = False

    def apply(self, *, confirmation: Callable[[BrowserPausedActivationReview], str | None]
              ) -> BrowserPausedActivationEvidence | None:
        try:
            if self._attempted or not all(_hex(v) for v in self._selection.values()):
                raise ValueError()
            self._attempted = True
            h = self._handoff
            with _stopped_history_ownership(h) as owner:
                root, profile = h._session._root, h._session._profile
                _absent(root)
                with _ledger_transaction(profile, readonly=False) as (db, ledger):
                    history, inputs = _capture_owned_history(h, owner, **self._selection)
                    baseline = native._baseline(history, profile)
                    if (_encoded(native._snapshot(db, baseline, activated=False))
                            != _encoded(baseline)):
                        raise ValueError()
                    started, now = self._monotonic(), self._clock()
                    last = [now, started]

                    def time_check() -> float:
                        wall, elapsed = self._clock(), self._monotonic()
                        if (not all(_timestamp(v) for v in (now, started, wall, elapsed, now + 120))
                                or not baseline["state"]["observed_at"] <= now <= last[0] <= wall
                                or not started <= last[1] <= elapsed
                                or not wall < now + 120 or not elapsed - started < 120):
                            raise ValueError()
                        last[:] = [wall, elapsed]
                        return wall

                    time_check()
                    epoch = secrets.token_hex(32)
                    phrase = "ACTIVATE PAUSED CONTINUATION " + epoch + ":" + secrets.token_hex(16)
                    answer = confirmation(BrowserPausedActivationReview(root, profile,
                        history.origin, history.device_id, epoch, phrase,
                        history.native_revision, now + 120))
                    if answer is None:
                        return None
                    if type(answer) is not str or answer != phrase:
                        raise ValueError()
                    approved = time_check()
                    inputs.recheck()
                    if _capture_owned_history(h, owner, **self._selection)[0] != history:
                        raise ValueError()
                    _absent(root)
                    raw = native._prepare_activation_manifest(history, profile=profile,
                        ledger_binding=ledger.binding, epoch=epoch,
                        consent_sha256=hashlib.sha256(phrase.encode()).hexdigest(),
                        reviewed_at=now, approved_at=approved)
                    binding = _create_manifest(root, raw)
                    if _capture_owned_history(h, owner, **self._selection)[0] != history:
                        raise ValueError()
                    native._stage_paused_activation(db, manifest=raw, manifest_binding=binding,
                        history=history, profile=profile, ledger_binding=ledger.binding,
                        now=time_check())
                    inputs.recheck()
                    _manifest_check(root / ACTIVATION_MANIFEST, raw, binding)
                    ledger.check_identity()
                    native._transaction(db, profile, readonly=False)
                    plan = native._plan(raw, history, profile, ledger.binding)
                    native._exact_after(db, raw, plan, binding)
                    inputs.recheck()
                    time_check()
                    db.commit()
                return self._confirm_owned(owner, epoch)
        except Exception:
            raise BrowserPausedActivationError() from None

    def _confirm_owned(self, owner: _HistoryOwnership, epoch: str
                       ) -> BrowserPausedActivationEvidence:
        if not _hex(epoch) or not all(_hex(v) for v in self._selection.values()):
            raise ValueError()
        h = self._handoff
        root, profile = h._session._root, h._session._profile
        path = root / ACTIVATION_MANIFEST
        binding = _binding(path)
        raw = _private_read(root, ACTIVATION_MANIFEST, native._LIMIT)
        _manifest_check(path, raw, binding)
        with _ledger_transaction(profile, readonly=True) as (db, ledger):
            history, inputs = _capture_owned_history(h, owner, **self._selection)
            result = native._inspect_paused_activation(db, manifest=raw, manifest_binding=binding,
                history=history, profile=profile, ledger_binding=ledger.binding)
            if result.epoch != epoch:
                raise ValueError()
            inputs.recheck()
            _manifest_check(path, raw, binding)
            ledger.check_identity()
            _no_sidecars(ledger.path)
            owner.binding(h)
            return BrowserPausedActivationEvidence(history.identity, result.epoch,
                                                    result.manifest_sha256, result.revision)

    def confirm(self, *, epoch: str) -> BrowserPausedActivationEvidence:
        """Exact stopped readback, not repair, consent renewal or current permission."""
        try:
            with _stopped_history_ownership(self._handoff) as owner:
                return self._confirm_owned(owner, epoch)
        except Exception:
            raise BrowserPausedActivationError() from None
