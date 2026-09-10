"""Fixture-only live-owner prepare/claim; no online authority or browser dispatch.

The trusted, in-process review callback is a fixture boundary, NOT a browser
gesture verifier or TLS proof. One process owns the entire attempt; nothing can
serialize/adopt it across native messages. No ticket, credential or grant is
returned. Ordinary schema-3 startup remains blocked. Do not use on real profiles.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Literal

from . import browser_device_continuation_activation as activation
from . import browser_device_continuation_cancel as cancel
from . import browser_device_continuation_current as current
from . import browser_device_continuation_epoch as epoch
from . import browser_device_continuation_history as history
from . import browser_device_continuation_ownership as ownership
from .browser_device_native import (
    BrowserNativeConfiguration,
    _private_read,
    load_browser_native_configuration,
)
from .browser_device_profile import _credential, _trust
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _hex, _integer, _timestamp
from .browser_device_worker import BrowserWorkerSelection


class BrowserContinuationApprovalError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Approval was refused or its outcome is unconfirmed. Retain all state; "
                         "do not replay, adopt the attempt or repair recovery files. "
                         "Readback is not consent, server authority or sign-in permission.")


@dataclass(frozen=True, slots=True, repr=False)
class _ApprovalReview:
    """Fresh callback context only; constructing/copying this proves no consent."""

    state: current.BrowserCurrentContinuation
    intent: str
    reviewed_generation: int


@dataclass(frozen=True, slots=True)
class BrowserApprovalState:
    """Exact state only; not a ticket, durable operation identity or permission."""

    phase: Literal["prepared", "claimed"]
    state: current.BrowserCurrentContinuation = field(repr=False)


class _Lifetime:
    """Conservative same-process wall/monotonic lifetime, beginning before review."""

    def __init__(self, wall: Callable[[], float], monotonic: Callable[[], float]) -> None:
        self._wall, self._monotonic = wall, monotonic
        self._start = self._last = self._sample()

    def _sample(self) -> tuple[float, float]:
        wall, mono = self._wall(), self._monotonic()
        if not _timestamp(wall) or not _timestamp(mono):
            raise ValueError()
        return wall, mono

    def check(self) -> None:
        now = self._sample()
        if any(not start <= last <= value < start + 120
               for start, last, value in zip(self._start, self._last, now, strict=True)):
            raise ValueError()
        self._last = now


def _commit(db: sqlite3.Connection) -> None:
    db.commit()


class _BrowserWorkerApproval:
    """One live-owner fixture attempt, no stopped entrypoint or serialized handoff.

    A future bridge must supply freshly verified page consent and exact reviewed
    generation. The callback here only acknowledges its exact in-memory context;
    it does not implement those browser/server checks. All results remain off
    ordinary native dispatch. A failed/lost reply never enables the next phase.
    """

    def __init__(self, configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
                 *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._configuration, self._worker = configuration, selection
        self._clock, self._monotonic = clock, monotonic
        self._process = (os.getpid(), os.getppid())
        self._prepare_attempted = self._claim_attempted = self._ready = False
        self._selected: current._SelectedFiles | None = None
        self._owner_binding: list[list[int]] | None = None
        self._private: dict[str, tuple[bytes, tuple[int, int]]] | None = None
        self._inputs: epoch._ApprovalInputs | None = None
        self._lifetime: _Lifetime | None = None
        self._after: BrowserApprovalState | None = None

    def _process_check(self) -> None:
        if self._process != (os.getpid(), os.getppid()):
            raise ValueError()

    def _owner(self, selected: current._SelectedFiles
               ) -> AbstractContextManager[ownership._HistoryOwnership]:
        return ownership._worker_history_ownership(selected.handoff,
            configuration=self._configuration, selection=self._worker)

    def _private_inputs(self) -> tuple[str, str]:
        config = self._configuration
        if load_browser_native_configuration(config.root) != config:
            raise ValueError()
        files = {}
        limits = (("client.json", 4096), ("device.secret", 128), ("ca.pem", 128 * 1024))
        for name, maximum in limits:
            path = config.root / name
            binding = activation._binding(path)
            raw = _private_read(config.root, name, maximum)
            activation._manifest_check(path, raw, binding)
            files[name] = raw, binding
        secret = files["device.secret"][0]
        _credential(secret)
        trust = _trust(files["ca.pem"][0])
        if self._private is not None and self._private != files:
            raise ValueError()
        self._private = files
        return hashlib.sha256(secret).hexdigest(), trust

    def prepare(self, expected: current.BrowserCurrentContinuation, *, intent: str,
                reviewed_generation: int,
                consent: Callable[[_ApprovalReview], _ApprovalReview | None]
                ) -> BrowserApprovalState:
        try:
            try:
                if self._prepare_attempted or self._claim_attempted:
                    raise ValueError()
                self._prepare_attempted = True
                self._process_check()
                if (type(expected) is not current.BrowserCurrentContinuation
                        or type(expected.native_revision) is not int
                        or type(expected.mode) is not RecoveryMode
                        or expected.mode is RecoveryMode.ACTIVE
                        or not _hex(intent) or not _integer(reviewed_generation)
                        or not callable(consent)):
                    raise ValueError()
                timer = cancel._Clock(self._clock, self._monotonic)
                self._lifetime = _Lifetime(self._clock, self._monotonic)
                selected = current._select_worker_files(self._configuration, self._worker)
                self._selected = selected
                with self._owner(selected) as owner:
                    self._owner_binding = owner.binding(selected.handoff)
                    with current._read_owned(selected, owner) as reader:
                        if reader.inspect() != expected:
                            raise ValueError()
                    secret, trust = self._private_inputs()
                    review = _ApprovalReview(expected, intent, reviewed_generation)
                    timer.check()
                    # No SQLite transaction is held across this trusted callback.
                    # A boolean, copied review or old review is not its acknowledgement.
                    if consent(review) is not review:
                        raise ValueError()
                    timer.check()
                    self._lifetime.check()
                    nonce = secrets.token_hex(32)
                    if not _hex(nonce):
                        raise ValueError()
                    digest = hashlib.sha256(nonce.encode("ascii")).hexdigest()
                    self._inputs = epoch._ApprovalInputs(digest,
                        intent, self._configuration.device_id, reviewed_generation, secret, trust)
                    self._write(selected, owner, expected, "prepared", timer)
                    result = self._confirm_owned(selected, owner)
                    timer.check()
                    self._lifetime.check()
                    self._ready = True
                    return result
            except BaseException:
                self._ready = False
                raise
        except Exception:
            raise BrowserContinuationApprovalError() from None

    def claim(self) -> BrowserApprovalState:
        """Consume the exact acknowledged in-memory prepare once, before any proof.

        This adapter does no proof/session exchange. Successful return remains
        an exact state, not authority to authenticate. Lost return cannot replay.
        """
        try:
            try:
                if self._claim_attempted:
                    raise ValueError()
                self._claim_attempted = True
                ready, self._ready = self._ready, False
                self._process_check()
                if (not ready or self._selected is None or self._after is None
                        or self._after.phase != "prepared" or self._lifetime is None):
                    raise ValueError()
                timer = cancel._Clock(self._clock, self._monotonic)
                self._lifetime.check()
                selected = self._selected
                with self._owner(selected) as owner:
                    self._write(selected, owner, self._after.state, "claimed", timer)
                    result = self._confirm_owned(selected, owner)
                    timer.check()
                    self._lifetime.check()
                    return result
            except BaseException:
                self._ready = False
                raise
        except Exception:
            raise BrowserContinuationApprovalError() from None

    def _write(self, selected: current._SelectedFiles, owner: ownership._HistoryOwnership,
               expected: current.BrowserCurrentContinuation, phase: Literal["prepared", "claimed"],
               timer: cancel._Clock) -> None:
        inputs, lifetime = self._inputs, self._lifetime
        if inputs is None or lifetime is None or phase not in {"prepared", "claimed"}:
            raise ValueError()
        profile = self._configuration.root
        with activation._ledger_transaction(profile, readonly=False) as (db, ledger):
            try:
                retained, checks = history._capture_owned_history(selected.handoff, owner,
                    release_id=selected.release_id, intent_id=selected.intent_id)
                raw, binding = selected.files[selected.manifest_path]
                selection = epoch._EpochSelection(raw, binding, retained, profile, ledger.binding)

                def files() -> None:
                    self._process_check()
                    if self._owner_binding != owner.binding(selected.handoff):
                        raise ValueError()
                    checks.recheck()
                    selected.recheck()
                    ledger.check_identity()
                    if self._private_inputs() != (inputs.credential_hash, inputs.trust_hash):
                        raise ValueError()

                files()
                view = epoch._read(db, selection, readonly=False)
                now = timer.check()
                lifetime.check()
                if (expected != cancel._result(selection, view.snapshot)
                        or now < view.state["next_at"] or now < view.state["observed_at"]):
                    raise ValueError()
                stage = epoch._stage_prepare if phase == "prepared" else epoch._stage_claim
                staged = stage(db, selection, view.snapshot, inputs, now=now)
                files()
                if epoch._read(db, selection, readonly=False).snapshot != staged:
                    raise ValueError()
                files()
                timer.check()
                lifetime.check()
                self._after = BrowserApprovalState(phase, cancel._result(selection, staged))
                _commit(db)
            except BaseException:
                try:
                    db.rollback()
                except BaseException:
                    db.close()
                    raise
                raise

    def _confirm_owned(self, selected: current._SelectedFiles,
                       owner: ownership._HistoryOwnership) -> BrowserApprovalState:
        self._process_check()
        after = self._after
        if (after is None or selected is not self._selected
                or self._owner_binding != owner.binding(selected.handoff)):
            raise ValueError()
        self._private_inputs()
        with current._read_owned(selected, owner) as reader:
            if reader.inspect() != after.state:
                raise ValueError()
            self._private_inputs()
            self._process_check()
        return after

    def confirm(self) -> BrowserApprovalState:
        """Read exact same-attempt after-state; never restore a failed phase gate."""
        try:
            try:
                self._process_check()
                if self._after is None or self._selected is None:
                    raise ValueError()
                with self._owner(self._selected) as owner:
                    return self._confirm_owned(self._selected, owner)
            except BaseException:
                self._ready = False
                raise
        except Exception:
            raise BrowserContinuationApprovalError() from None
