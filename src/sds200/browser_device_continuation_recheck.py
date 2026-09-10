"""Fixture-only exact active-generation verification, without native mutation.

This is not accepted browser state, an installed session or a renewal operation.
No ordinary role or native action invokes it. A future fixed browser controller
must independently own accepted-state selection; native ACTIVE can also follow
an uncertain initial exchange and cannot authorize adopting that browser state.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable

from . import browser_device_continuation_cancel as cancel
from . import browser_device_continuation_current as current
from . import browser_device_continuation_ownership as ownership
from . import browser_device_verification as transport
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_recovery import RecoveryMode
from .browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from .browser_device_worker import BrowserWorkerSelection


class BrowserContinuationRecheckError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Current server verification failed or is unconfirmed. "
                         "No native state was changed and no browser session was established. "
                         "Retain saved state; do not replay initial sign-in.")


class _BrowserWorkerActiveVerification:
    """One owned read/network/read attempt, not a cached or serialized proof.

    No supplied proof, transport, grant or requested role is consumed. The total
    elapsed checks do NOT interrupt I/O; future dispatch must also retain the
    native supervisor's independent ten-second termination deadline.
    """

    def __init__(self, configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
                 *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._configuration, self._worker = configuration, selection
        self._clock, self._monotonic = clock, monotonic
        self._process = (os.getpid(), os.getppid())
        self._attempted = False

    def _process_check(self) -> None:
        if self._process != (os.getpid(), os.getppid()):
            raise ValueError()

    def run(self, expected: current.BrowserContinuationObservation
            ) -> current.BrowserContinuationObservation:
        try:
            if self._attempted:
                raise ValueError()
            self._attempted = True
            self._process_check()
            # Strict scalar types prevent bool/int equality or caller objects
            # from standing in for any actual owned read comparison.
            if (type(expected) is not current.BrowserContinuationObservation
                    or type(expected.state) is not current.BrowserCurrentContinuation
                    or type(expected.generation) is not int
                    or not 1 <= expected.generation < 2**53 - 1
                    or type(expected.state.native_revision) is not int
                    or expected.state.mode is not RecoveryMode.ACTIVE
                    or any(type(getattr(expected.state, name)) is not str for name in (
                        "identity", "origin", "device_id", "epoch", "manifest_sha256",
                        "state_fingerprint"))):
                raise ValueError()
            timer = cancel._Clock(self._clock, self._monotonic)
            selected = current._select_worker_files(self._configuration, self._worker)
            with ownership._worker_history_ownership(selected.handoff,
                    configuration=self._configuration, selection=self._worker) as owner:
                owner_binding = owner.binding(selected.handoff)

                def observe() -> current.BrowserContinuationObservation:
                    self._process_check()
                    if owner.binding(selected.handoff) != owner_binding:
                        raise ValueError()
                    with current._read_owned(selected, owner) as reader:
                        observed = reader.observe()
                        if observed != expected:
                            raise ValueError()
                    self._process_check()
                    timer.check()
                    return observed

                observe()
                # All SQLite scopes are CLOSED across this network request.
                # Original private inputs/owner stay pinned; a native pause can
                # commit, and must win the fresh full-fingerprint comparison.
                record = BrowserDeviceRecord(self._configuration.device_id,
                    expected.generation, BrowserDeviceState.ACTIVE)
                proof = transport.verify_browser_device(self._configuration, record)
                if (type(proof) is not transport.BrowserVerifiedRecord
                        or type(proof.identity) is not str
                        or proof.identity != self._configuration.identity
                        or type(proof.record) is not BrowserDeviceRecord
                        or type(proof.record.device_id) is not str
                        or type(proof.record.generation) is not int
                        or proof.record.state is not BrowserDeviceState.ACTIVE
                        or proof.record != record or proof.drained is not True):
                    raise ValueError()
                timer.check()
                result = observe()
            self._process_check()
            timer.check()
            # Returns the actual last read, not the supplied comparison object.
            # No reusable proof/session is retained and no confirm/replay API exists.
            return result
        except Exception:
            raise BrowserContinuationRecheckError() from None
