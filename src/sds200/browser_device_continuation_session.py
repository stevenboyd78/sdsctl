"""Fixture-only initial session issuance after the same owned verification attempt.

No native/browser dispatch, cookie installation, renewal or retry is enabled.
Native completion is not browser readiness. Lost/late issuance stays uncertain:
a server token may exist, but readback cannot retrieve it or issue another one.
Do not use this adapter on real profiles.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import browser_device_continuation_approval as approval
from . import browser_device_continuation_cancel as cancel
from . import browser_device_continuation_current as current
from . import browser_device_continuation_verification as verification
from . import browser_device_verification as transport
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_recovery import ExchangeSession, RecoveryMode
from .browser_device_worker import BrowserWorkerSelection


class BrowserContinuationSessionError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Initial session was refused or its outcome is unconfirmed. "
                         "Retain all state; do not retry or adopt the completed approval. "
                         "Native readback cannot recover a session or prove remote revocation.")


@dataclass(frozen=True, slots=True)
class BrowserInitialSession:
    """Private in-memory handoff only, never a replay ticket or browser readiness."""

    state: current.BrowserCurrentContinuation = field(repr=False)
    session: ExchangeSession = field(repr=False)


class _BrowserWorkerInitialSession:
    """One fixture operation creates its own verifier and consumes its return once.

    No supplied proof/result/transport/grant is accepted. The total elapsed budget
    spans consent, proof and issuance; future dispatch must ALSO retain the native
    supervisor's independent ten-second deadline. Consent remains simulated here.
    A bearer is returned at most once, not made into a single-use HTTP credential.
    """

    def __init__(self, configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
                 *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._configuration, self._worker = configuration, selection
        self._clock, self._monotonic = clock, monotonic
        self._process = (os.getpid(), os.getppid())
        self._attempted = False
        self._verification: verification._BrowserWorkerVerification | None = None

    def _process_check(self) -> None:
        if self._process != (os.getpid(), os.getppid()):
            raise ValueError()

    def run(self, expected: current.BrowserCurrentContinuation, *, intent: str,
            reviewed_generation: int,
            consent: Callable[[approval._ApprovalReview], approval._ApprovalReview | None]
            ) -> BrowserInitialSession:
        try:
            if self._attempted:
                raise ValueError()
            self._attempted = True
            self._process_check()
            timer = cancel._Clock(self._clock, self._monotonic)
            verifier = verification._BrowserWorkerVerification(self._configuration, self._worker,
                clock=self._clock, monotonic=self._monotonic)
            self._verification = verifier
            # A lost acknowledgement cannot be replaced by confirm(), even if
            # its exact native state happens to be complete.
            completed = verifier.run(expected, intent=intent,
                reviewed_generation=reviewed_generation, consent=consent)
            timer.check()
            obj = verifier._approval
            if (type(completed) is not verification.BrowserVerificationState
                    or completed is not verifier._after or completed.phase != "complete"
                    or completed.state.mode is not RecoveryMode.ACTIVE
                    or obj is None or obj._inputs is None or obj._selected is None
                    or obj._inputs.generation != reviewed_generation):
                raise ValueError()
            selected = obj._selected
            with obj._owner(selected) as owner:
                self._process_check()
                if verifier._confirm_owned(selected, owner) is not completed:
                    raise ValueError()
                timer.check()
                # Full current SQL scopes are closed; original private-file/owner
                # coordination stays held. A newer native pause can still commit.
                issued = transport.exchange_browser_device_at_generation(
                    self._configuration, obj._inputs.generation)
                timer.check()
                if type(issued) is not ExchangeSession:
                    raise ValueError()
                # Reconstruct even a transport result: never trust a subclass,
                # malformed fields or a session with no conservative life left.
                checked = ExchangeSession(issued.token, issued.expires_in)
                self._process_check()
                if verifier._confirm_owned(selected, owner) is not completed:
                    raise ValueError()
                timer.check()
            self._process_check()
            timer.check()
            # Subtract ALL elapsed time (including proof) conservatively. These
            # are the same already-validated wall/monotonic samples, not new clocks.
            elapsed = max(end - start for start, end in zip(
                timer._start, timer._last, strict=True))
            result = ExchangeSession(checked.token, checked.expires_in - elapsed)
            return BrowserInitialSession(completed.state, result)
        except Exception:
            # No automatic pause/failure rewrite or exchange retry. Native ACTIVE
            # may remain after uncertainty, but ordinary schema-3 roles stay blocked.
            raise BrowserContinuationSessionError() from None

    def confirm(self) -> verification.BrowserVerificationState:
        """Exact native readback only, including after lost issuance; never a token."""
        try:
            self._process_check()
            if self._verification is None:
                raise ValueError()
            return self._verification.confirm()
        except Exception:
            raise BrowserContinuationSessionError() from None
