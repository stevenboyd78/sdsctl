"""Fixture-only owned native cancellation; no browser role, grant or network.

One explicit attempt owns fixed files/history and a dedicated native transaction.
Exact same-process readback can describe its expected state after an uncertain
reply, but cannot replay the write or prove a durable operation identity. Neither
native pause nor clock correction invalidates browser/server sessions. Ordinary
schema-3 startup and requests remain blocked; do not call on a real profile.
"""
from __future__ import annotations

import os
import sqlite3
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import browser_device_continuation_activation as activation
from . import browser_device_continuation_current as current
from . import browser_device_continuation_epoch as epoch
from . import browser_device_continuation_history as history
from . import browser_device_continuation_ownership as ownership
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _timestamp
from .browser_device_worker import BrowserWorkerSelection

_Operation = Literal["pause", "clock-correction"]


class BrowserContinuationCancellationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Cancellation was refused or its outcome is unconfirmed. "
                         "Retain all state; do not replay the attempt or repair recovery files. "
                         "Exact readback is not a session-invalidation or sign-in result.")


@dataclass(frozen=True, slots=True)
class BrowserCancellationState:
    """Exact current after-state, not durable operation provenance or session state."""

    operation: _Operation
    state: current.BrowserCurrentContinuation = field(repr=False)


class _Clock:
    def __init__(self, wall: Callable[[], float], monotonic: Callable[[], float]) -> None:
        self._wall, self._monotonic = wall, monotonic
        self._start = self._last = self._sample()

    def _sample(self) -> tuple[float, float]:
        wall, mono = self._wall(), self._monotonic()
        if not _timestamp(wall) or not _timestamp(mono):
            raise ValueError()
        return wall, mono

    def check(self) -> float:
        now = self._sample()
        if any(not start <= last <= value < start + 10
               for start, last, value in zip(self._start, self._last, now, strict=True)):
            raise ValueError()
        self._last = now
        return now[0]


def _result(selection: epoch._EpochSelection, snapshot: epoch._EpochSnapshot
            ) -> current.BrowserCurrentContinuation:
    h = selection.history
    return current.BrowserCurrentContinuation(h.identity, h.origin, h.device_id, snapshot.epoch,
        snapshot.manifest_sha256, snapshot.fingerprint, snapshot.revision, snapshot.mode)


def _commit(db: sqlite3.Connection) -> None:
    db.commit()


class _Cancellation(ABC):
    def __init__(self, *, clock: Callable[[], float], monotonic: Callable[[], float]) -> None:
        self._clock, self._monotonic = clock, monotonic
        self._process = (os.getpid(), os.getppid())
        self._attempted = False
        self._selected: current._SelectedFiles | None = None
        self._owner_binding: list[list[int]] | None = None
        self._after: BrowserCancellationState | None = None

    @abstractmethod
    def _select(self) -> current._SelectedFiles: ...

    @abstractmethod
    def _owner(self, selected: current._SelectedFiles
               ) -> AbstractContextManager[ownership._HistoryOwnership]: ...

    def _process_check(self) -> None:
        if self._process != (os.getpid(), os.getppid()):
            raise ValueError()

    def pause(self, expected: current.BrowserCurrentContinuation) -> BrowserCancellationState:
        """One explicit native pause. Clock backstep is cancelled atomically first."""
        return self._apply(expected, "pause")

    def correct_clock(self, expected: current.BrowserCurrentContinuation
                       ) -> BrowserCancellationState:
        """One explicit backstep correction, not a read-only status side effect."""
        return self._apply(expected, "clock-correction")

    def _apply(self, expected: current.BrowserCurrentContinuation,
                operation: _Operation) -> BrowserCancellationState:
        try:
            if self._attempted:
                raise ValueError()
            self._attempted = True  # Invalid inputs and failed attempts are also one-use.
            self._process_check()
            if (type(expected) is not current.BrowserCurrentContinuation
                    or type(expected.native_revision) is not int
                    or type(expected.mode) is not RecoveryMode
                    or operation not in {"pause", "clock-correction"}):
                raise ValueError()
            timer = _Clock(self._clock, self._monotonic)
            selected = self._select()
            with self._owner(selected) as owner:
                profile = selected.handoff._session._profile
                with activation._ledger_transaction(profile, readonly=False) as (db, ledger):
                    try:
                        self._process_check()
                        selected.recheck()
                        retained, inputs = history._capture_owned_history(selected.handoff, owner,
                            release_id=selected.release_id, intent_id=selected.intent_id)
                        raw, binding = selected.files[selected.manifest_path]
                        selection = epoch._EpochSelection(raw, binding, retained,
                                                            profile, ledger.binding)

                        def files() -> None:
                            self._process_check()
                            owner.binding(selected.handoff)
                            inputs.recheck()
                            selected.recheck()
                            ledger.check_identity()

                        files()
                        view = epoch._read(db, selection, readonly=False)
                        if expected != _result(selection, view.snapshot):
                            raise ValueError()
                        now = timer.check()
                        staged = view.snapshot
                        if operation == "clock-correction" or now < view.state["observed_at"]:
                            staged = epoch._stage_clock_correction(db, selection, staged, now=now)
                        if operation == "pause":
                            staged = epoch._stage_pause(db, selection, staged, now=now)
                        files()
                        if epoch._read(db, selection, readonly=False).snapshot != staged:
                            raise ValueError()
                        files()
                        self._owner_binding = owner.binding(selected.handoff)
                        timer.check()
                        # Retain the exact expected state BEFORE commit, so an uncertain
                        # reply permits readback only. No durable second journal or replay.
                        self._selected = selected
                        self._after = BrowserCancellationState(
                            operation, _result(selection, staged))
                        _commit(db)
                    except BaseException:
                        try:
                            db.rollback()
                        except BaseException:
                            db.close()
                            raise
                        raise
                return self._confirm_owned(selected, owner)
        except Exception:
            raise BrowserContinuationCancellationError() from None

    def _confirm_owned(self, selected: current._SelectedFiles,
                        owner: ownership._HistoryOwnership) -> BrowserCancellationState:
        self._process_check()
        after = self._after
        if (after is None or selected is not self._selected
                or self._owner_binding != owner.binding(selected.handoff)):
            raise ValueError()
        with current._read_owned(selected, owner) as reader:
            if reader.inspect() != after.state:
                raise ValueError()
            self._process_check()
        return after

    def confirm(self) -> BrowserCancellationState:
        """Reacquire actual ownership; exact same-process state only, never another write.

        No caller-supplied state/receipt is accepted. If this object is lost, no
        durable operation identity can be reconstructed. A later paused state is
        not confirmation; uncertainty must not trigger automatic cancellation.
        """
        try:
            self._process_check()
            if self._after is None or self._selected is None:
                raise ValueError()
            with self._owner(self._selected) as owner:
                return self._confirm_owned(self._selected, owner)
        except Exception:
            raise BrowserContinuationCancellationError() from None


class BrowserStoppedCancellation(_Cancellation):
    """Internal stopped-installation cancellation; no CLI or real-profile runbook."""

    def __init__(self, root: Path, *, bundle: Path, profile: Path, public_key: Path,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        super().__init__(clock=clock, monotonic=monotonic)
        self._root = root
        self._paths = dict(bundle=bundle, profile=profile, public_key=public_key)

    def _select(self) -> current._SelectedFiles:
        return current._SelectedFiles(self._root, **self._paths)

    def _owner(self, selected: current._SelectedFiles
               ) -> AbstractContextManager[ownership._HistoryOwnership]:
        return ownership._stopped_history_ownership(selected.handoff)


class _BrowserWorkerCancellation(_Cancellation):
    """Separate fixed-wrapper live boundary; not exposed by worker dispatch."""

    def __init__(self, configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
                 *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        super().__init__(clock=clock, monotonic=monotonic)
        self._configuration, self._worker = configuration, selection

    def _select(self) -> current._SelectedFiles:
        return current._select_worker_files(self._configuration, self._worker)

    def _owner(self, selected: current._SelectedFiles
               ) -> AbstractContextManager[ownership._HistoryOwnership]:
        return ownership._worker_history_ownership(selected.handoff,
            configuration=self._configuration, selection=self._worker)
