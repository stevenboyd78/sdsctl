"""Private owned exact-generation verification for one native attempt.

One process creates and consumes its own approval. The trusted in-process callback
does not independently prove browser consent. Verified HTTPS proof is real, but completion is
only native state, never an installed session or cached permission. Ordinary
schema-3 startup remains blocked. Do not invoke this private adapter directly
on deployed profiles.
"""
from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from . import browser_device_continuation_activation as activation
from . import browser_device_continuation_approval as approval
from . import browser_device_continuation_cancel as cancel
from . import browser_device_continuation_current as current
from . import browser_device_continuation_epoch as epoch
from . import browser_device_continuation_history as history
from . import browser_device_continuation_ownership as ownership
from . import browser_device_verification as transport
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from .browser_device_worker import BrowserWorkerSelection


class BrowserContinuationVerificationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Verification was refused or completion is unconfirmed. Retain all state; "
                         "do not replay, adopt the attempt or repair recovery files. "
                         "Exact native readback is not browser sign-in or session revocation.")


@dataclass(frozen=True, slots=True)
class BrowserVerificationState:
    """Exact same-attempt native after-state only, not reusable server authority."""

    phase: Literal["complete", "failed"]
    state: current.BrowserCurrentContinuation = field(repr=False)


def _commit(db: sqlite3.Connection) -> None:
    db.commit()


class _BrowserWorkerVerification:
    """One private attempt; no supplied approval, proof, transport or role.

    Elapsed checks cover the WHOLE operation, not a renewed per-phase allowance.
    They do not interrupt blocked I/O: fixed native dispatch also retains
    the independent ten-second supervising process deadline. This is not wired
    to the old two-message prepare/commit protocol or any ordinary worker role.
    """

    def __init__(self, configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
                 *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._configuration, self._worker = configuration, selection
        self._clock, self._monotonic = clock, monotonic
        self._process = (os.getpid(), os.getppid())
        self._attempted = False
        self._approval: approval._BrowserWorkerApproval | None = None
        self._claimed: approval.BrowserApprovalState | None = None
        self._after: BrowserVerificationState | None = None

    def _process_check(self) -> None:
        if self._process != (os.getpid(), os.getppid()):
            raise ValueError()

    def run(self, expected: current.BrowserCurrentContinuation, *, intent: str,
            reviewed_generation: int,
            consent: Callable[[approval._ApprovalReview], approval._ApprovalReview | None]
            ) -> BrowserVerificationState:
        try:
            if self._attempted:
                raise ValueError()
            self._attempted = True
            self._process_check()
            timer = cancel._Clock(self._clock, self._monotonic)
            obj = approval._BrowserWorkerApproval(self._configuration, self._worker,
                clock=self._clock, monotonic=self._monotonic)
            self._approval = obj
            obj.prepare(expected, intent=intent, reviewed_generation=reviewed_generation,
                        consent=consent)
            timer.check()
            self._claimed = obj.claim()  # A lost/failed return cannot authorize network work.
            timer.check()
            selected, inputs = obj._selected, obj._inputs
            if selected is None or inputs is None:
                raise ValueError()
            with obj._owner(selected) as owner:
                self._process_check()
                if obj._confirm_owned(selected, owner) != self._claimed:
                    raise ValueError()
                timer.check()
                # All native SQLite scopes have closed. Private-input coordination
                # stays held; another native pause may commit during this I/O.
                record = BrowserDeviceRecord(self._configuration.device_id,
                    inputs.generation, BrowserDeviceState.ACTIVE)
                try:
                    proof = transport.verify_browser_device(self._configuration, record)
                    if (type(proof) is not transport.BrowserVerifiedRecord
                            or proof.identity != self._configuration.identity
                            or type(proof.record) is not BrowserDeviceRecord
                            or type(proof.record.generation) is not int
                            or proof.record.state is not BrowserDeviceState.ACTIVE
                            or proof.record != record or proof.drained is not True):
                        raise ValueError()
                except Exception:
                    # Best effort only after a transport/proof refusal. If inputs,
                    # owner, clocks or exact claim changed, leave it for review.
                    # Interrupts and uncertain completion never take this path.
                    with suppress(Exception):
                        self._finish(selected, owner, timer, "failed")
                    raise
                timer.check()
                self._finish(selected, owner, timer, "complete")
                result = self._confirm_owned(selected, owner)
                timer.check()
            timer.check()
            return result
        except Exception:
            raise BrowserContinuationVerificationError() from None

    def _finish(self, selected: current._SelectedFiles, owner: ownership._HistoryOwnership,
                timer: cancel._Clock, phase: Literal["complete", "failed"]) -> None:
        obj, claimed = self._approval, self._claimed
        if (obj is None or claimed is None or claimed.phase != "claimed"
                or selected is not obj._selected or obj._inputs is None
                or obj._lifetime is None or phase not in {"complete", "failed"}):
            raise ValueError()
        inputs, lifetime = obj._inputs, obj._lifetime
        timer.check()
        lifetime.check()
        profile = self._configuration.root
        with activation._ledger_transaction(profile, readonly=False) as (db, ledger):
            try:
                retained, checks = history._capture_owned_history(selected.handoff, owner,
                    release_id=selected.release_id, intent_id=selected.intent_id)
                raw, binding = selected.files[selected.manifest_path]
                selection = epoch._EpochSelection(raw, binding, retained,
                    self._configuration.root, ledger.binding)

                def files() -> None:
                    self._process_check()
                    obj._process_check()
                    if obj._owner_binding != owner.binding(selected.handoff):
                        raise ValueError()
                    checks.recheck()
                    selected.recheck()
                    ledger.check_identity()
                    if obj._private_inputs() != (inputs.credential_hash, inputs.trust_hash):
                        raise ValueError()

                files()
                view = epoch._read(db, selection, readonly=False)
                if claimed.state != cancel._result(selection, view.snapshot):
                    raise ValueError()
                now = timer.check()
                lifetime.check()
                stage = epoch._stage_complete if phase == "complete" else epoch._stage_fail
                staged = stage(db, selection, view.snapshot, inputs, now=now)
                files()
                if epoch._read(db, selection, readonly=False).snapshot != staged:
                    raise ValueError()
                files()
                timer.check()
                lifetime.check()
                # Preserve the exact expected state before the only commit attempt.
                # A lost reply cannot cause completion to be rewritten as failed.
                self._after = BrowserVerificationState(phase, cancel._result(selection, staged))
                _commit(db)
            except BaseException:
                try:
                    db.rollback()
                except BaseException:
                    db.close()
                    raise
                raise

    def _confirm_owned(self, selected: current._SelectedFiles,
                       owner: ownership._HistoryOwnership) -> BrowserVerificationState:
        self._process_check()
        obj, after = self._approval, self._after
        if (obj is None or after is None or selected is not obj._selected
                or obj._owner_binding != owner.binding(selected.handoff)):
            raise ValueError()
        obj._process_check()
        obj._private_inputs()
        with current._read_owned(selected, owner) as reader:
            if reader.inspect() != after.state:
                raise ValueError()
            obj._private_inputs()
            self._process_check()
        return after

    def confirm(self) -> BrowserVerificationState:
        """Describe only this attempt's exact after-state, never replay proof or grant access."""
        try:
            self._process_check()
            obj = self._approval
            if obj is None or obj._selected is None or self._after is None:
                raise ValueError()
            with obj._owner(obj._selected) as owner:
                return self._confirm_owned(obj._selected, owner)
        except Exception:
            raise BrowserContinuationVerificationError() from None
