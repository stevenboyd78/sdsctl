"""Owned foreground launch validation, not consent, authentication or guard repair.

The exact installed history and current epoch must validate together. Only a
complete PAUSED or ACTIVE continuation may open the fixed startup page. Browser
STOP and native/server authorization remain independently enforced by the worker.
This scope keeps the existing launcher/profile ownership through browser exit,
but releases its read-only SQL transaction before any browser is launched.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import browser_device_continuation_current as current
from . import browser_device_continuation_epoch as epoch
from .browser_device_continuation_ownership import _stopped_history_ownership
from .browser_device_recovery import RecoveryMode
from .browser_device_registration import BrowserRegistration, _canonical_bundle_files


class BrowserContinuationStartupError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Continuation launch validation failed. Retain the profile and "
            "evidence; no state was reset or sign-in permission established."
        )


@contextmanager
def _continuation_startup_scope(
    root: Path,
    *,
    bundle: Path,
    profile: Path,
    public_key: Path,
) -> Iterator[BrowserRegistration]:
    """Acquire ownership ourselves; never adopt a busy lock or serialized proof.

    No fallback to normal registration is safe after continuation marker selection.
    No current-state object escapes the read transaction as cached authorization.
    All registration, private-input and complete-history checks remain mandatory.
    """
    try:
        selected = current._SelectedFiles(
            root, bundle=bundle, profile=profile, public_key=public_key
        )
        with _stopped_history_ownership(selected.handoff) as owner:
            with current._read_owned(selected, owner) as reader:
                observed = reader.observe()
                state = observed.state
                view = epoch._read(reader._db, reader._selection, readonly=True)
                registration, _, _ = _canonical_bundle_files(bundle, profile, public_key)
                if (
                    state.identity != registration.identity
                    or state.mode not in (RecoveryMode.PAUSED, RecoveryMode.ACTIVE)
                    or any(row["phase"] in {"prepared", "claimed"} for row in view.approvals)
                    or (state.mode is RecoveryMode.PAUSED and observed.generation is not None)
                    or (
                        state.mode is RecoveryMode.ACTIVE
                        and (
                            type(observed.generation) is not int
                            or not 0 < observed.generation < 2**53 - 1
                        )
                    )
                    or reader.observe() != observed
                ):
                    raise ValueError()
            # _read_owned has closed SQL and invalidated its reader. Keeping that
            # transaction alive would block the worker's approval/pause commits.
            owner.binding(selected.handoff)
            yield registration
            # The owner checks again on exit; leftover browser singleton markers
            # or changed private directories fail without deleting/repairing them.
    except Exception:
        raise BrowserContinuationStartupError() from None
