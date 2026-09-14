"""Internal paused server review, not consent, native approval or a session.

Only the fixed read-only continuation endpoint invokes this adapter. It reads
installed state and current server authority, not consent. Initial issuance must repeat
its own current-state and server-generation checks after actual browser consent.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import browser_device_continuation_cancel as cancel
from . import browser_device_continuation_current as current
from . import browser_device_continuation_ownership as ownership
from . import browser_device_verification as transport
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _hex
from .browser_device_store import BrowserDeviceRecord, BrowserDeviceState
from .browser_device_worker import BrowserWorkerSelection, worker_graph


class BrowserContinuationReviewError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Current server review failed or is unconfirmed. "
                         "No sign-in permission or session was established; retain saved state.")


@dataclass(frozen=True, slots=True)
class BrowserContinuationReview:
    """One online review point, distinct from offline native state or consent."""

    state: current.BrowserCurrentContinuation = field(repr=False)
    generation: int = field(repr=False)


class _BrowserWorkerPausedReview:
    """One owned read/network/read; no browser-supplied state, proof or selectors.

    Elapsed checks do not interrupt I/O. The fixed native dispatch also retains
    the independent ten-second native-process termination deadline.
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

    def run(self) -> BrowserContinuationReview:
        try:
            if self._attempted:
                raise ValueError()
            self._attempted = True
            self._process_check()
            timer = cancel._Clock(self._clock, self._monotonic)
            build = worker_graph()[0]
            selected = current._select_worker_files(self._configuration, self._worker)
            with ownership._worker_history_ownership(selected.handoff,
                    configuration=self._configuration, selection=self._worker) as owner:
                owner_binding = owner.binding(selected.handoff)

                def observe() -> current.BrowserCurrentContinuation:
                    self._process_check()
                    if owner.binding(selected.handoff) != owner_binding:
                        raise ValueError()
                    with current._read_owned(selected, owner) as reader:
                        observed = reader.observe()
                        if (type(observed) is not current.BrowserContinuationObservation
                                or type(observed.state) is not current.BrowserCurrentContinuation
                                or observed.state.mode is not RecoveryMode.PAUSED
                                or observed.generation is not None):
                            raise ValueError()
                        state = observed.state
                        if (state.identity != self._configuration.identity
                                or state.origin != self._configuration.origin
                                or state.device_id != self._configuration.device_id
                                or type(state.native_revision) is not int
                                or not 0 < state.native_revision < 2**53 - 1
                                or not all(_hex(v) for v in (state.identity, state.epoch,
                                    state.manifest_sha256, state.state_fingerprint))):
                            raise ValueError()
                    self._process_check()
                    timer.check()
                    return observed.state

                before = observe()
                # No SQLite transaction spans the network. Private inputs and
                # live ownership stay pinned; a newer native revision can win.
                proof = transport.verify_browser_device(self._configuration)
                if (type(proof) is not transport.BrowserVerifiedRecord
                        or type(proof.identity) is not str
                        or proof.identity != self._configuration.identity
                        or type(proof.record) is not BrowserDeviceRecord
                        or type(proof.record.device_id) is not str
                        or proof.record.device_id != self._configuration.device_id
                        or proof.record.state is not BrowserDeviceState.ACTIVE
                        or type(proof.record.generation) is not int
                        or not 1 <= proof.record.generation < 2**53 - 1
                        or proof.drained is not True):
                    raise ValueError()
                after = observe()
                if before != after:
                    raise ValueError()
            self._process_check()
            timer.check()
            selected.recheck()
            if worker_graph()[0] != build:
                raise ValueError()
            return BrowserContinuationReview(after, proof.record.generation)
        except Exception:
            raise BrowserContinuationReviewError() from None
