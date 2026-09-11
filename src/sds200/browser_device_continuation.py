"""Read-only preflight for a FUTURE administrator continuation after recovery.

No grant, native action, credential exchange, CLI or journal writer exists here.
A valid checkpoint means only that the old paused recovery still validates.
It does not permit normal sign-in or authorize a change to the bound ledger.
"""
from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .browser_device_guard_release import _confirmed
from .browser_device_handoff import BrowserRecoveryHandoff
from .browser_device_native import load_browser_native_configuration
from .browser_device_profile_access import browser_profile_access
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _hex
from .browser_device_startup import _launch_lock


class BrowserContinuationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Paused continuation checkpoint is unsafe, stale or unconfirmed. "
                         "Keep all recovery evidence. No permission or sign-in was granted.")


@dataclass(frozen=True, slots=True)
class BrowserContinuationCheckpoint:
    identity: str = field(repr=False)
    origin: str = field(repr=False)
    device_id: str = field(repr=False)
    release_id: str = field(repr=False)
    fingerprint: str = field(repr=False)
    native_revision: int
    expires_at: float
    mode: RecoveryMode = RecoveryMode.PAUSED


def _released_checkpoint(handoff: BrowserRecoveryHandoff,
                         release_id: str) -> tuple[str, str, str, int, str]:
    evidence = _confirmed(handoff, release_id=release_id)
    config = load_browser_native_configuration(handoff._session._profile)
    if evidence.identity != config.identity or evidence.mode is not RecoveryMode.PAUSED:
        raise ValueError()
    # Full canonical handoff/archive/host/guard chain plus this installed profile.
    digest = hashlib.sha256((evidence.receipt_sha256 + "\0" +
                             str(config.root)).encode()).hexdigest()
    return config.identity, config.origin, config.device_id, evidence.revision, digest


class BrowserContinuationInspection:
    """One same-process review and recheck; never an executable consent object.

    Hold stopped managed-launcher ownership and read locks while validating all
    existing release evidence. Recheck under the same ownership immediately
    before returning. Opaque Chromium storage is not read or declared valid;
    the prior browser acknowledgement is historical, not a current session proof.
    """

    def __init__(self, handoff: BrowserRecoveryHandoff, *, release_id: str,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._handoff, self._release_id = handoff, release_id
        self._clock, self._monotonic = clock, monotonic
        self._attempted = False
        self._review: BrowserContinuationCheckpoint | None = None
        self._started = self._wall = 0.0

    def _snapshot(self) -> tuple[str, str, str, int, str]:
        from .browser_device_continuation_intent import has_continuation_intent

        if has_continuation_intent(self._handoff._session._root):
            raise ValueError()
        return _released_checkpoint(self._handoff, self._release_id)

    def _time(self) -> None:
        now, elapsed = self._clock(), self._monotonic()
        if (any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                for v in (now, elapsed, self._wall, self._started))
                or not self._wall <= now < self._wall + 120
                or not 0 <= elapsed - self._started < 120):
            raise ValueError()

    def review(self) -> BrowserContinuationCheckpoint:
        try:
            if self._attempted or not _hex(self._release_id):
                raise ValueError()
            self._attempted = True
            self._wall, self._started = self._clock(), self._monotonic()
            h = self._handoff
            with (_launch_lock(h._session._root, create=False),
                  browser_profile_access(h._session._profile, exclusive=False)):
                self._time()
                identity, origin, device, revision, digest = self._snapshot()
                selected = BrowserContinuationCheckpoint(identity, origin, device,
                    self._release_id, digest, revision, self._wall + 120)
                if self._snapshot() != (identity, origin, device, revision, digest):
                    raise ValueError()
                self._time()
                self._review = selected
                return selected
        except Exception:
            raise BrowserContinuationError() from None

    def confirm(self, selected: BrowserContinuationCheckpoint) -> BrowserContinuationCheckpoint:
        """Recheck exact in-memory advisory selection; not a durable resume approval."""
        try:
            if selected is not self._review or self._review is None:
                raise ValueError()
            h = self._handoff
            with (_launch_lock(h._session._root, create=False),
                  browser_profile_access(h._session._profile, exclusive=False)):
                self._time()
                if self._snapshot() != (selected.identity, selected.origin, selected.device_id,
                                        selected.native_revision, selected.fingerprint):
                    raise ValueError()
                self._time()
                return selected
        except Exception:
            raise BrowserContinuationError() from None
