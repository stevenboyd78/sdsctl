"""Internal owned continuation context producer; not selected by native dispatch.

Only fixed installed wrapper inputs are accepted. The returned read point is
neither browser consent nor server verification, acceptance, renewal or a lease.
Ordinary worker contexts/actions still reject continuation installations.
"""
from __future__ import annotations

from . import browser_device_continuation_current as current
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_recovery import RecoveryMode
from .browser_device_resume import _hex
from .browser_device_worker import BrowserWorkerSelection, context_document, worker_graph


class BrowserContinuationContextError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Browser continuation context is unavailable or changed. "
                         "Retain saved state; no sign-in permission was established.")


def _continuation_worker_context(
    configuration: BrowserNativeConfiguration, selection: BrowserWorkerSelection,
) -> dict[str, object]:
    """Construct inside actual live ownership and return only after scope exit.

    No requested role, saved browser state, serialized observation or filesystem
    path from a browser message may select this adapter. It remains unwired.
    """
    try:
        if (type(configuration) is not BrowserNativeConfiguration
                or type(selection) is not BrowserWorkerSelection):
            raise ValueError()
        build = worker_graph()[0]
        with current._worker_current_scope(configuration, selection) as reader:
            observed = reader.observe()
            if type(observed) is not current.BrowserContinuationObservation:
                raise ValueError()
            state = observed.state
            if (type(state) is not current.BrowserCurrentContinuation
                    or state.identity != configuration.identity
                    or state.origin != configuration.origin
                    or state.device_id != configuration.device_id
                    or type(state.mode) is not RecoveryMode
                    or state.mode not in (RecoveryMode.PAUSED, RecoveryMode.ACTIVE)
                    or type(state.native_revision) is not int
                    or not 0 < state.native_revision < 2**53 - 1
                    or not all(_hex(v) for v in (state.identity, state.epoch,
                                                state.state_fingerprint))
                    or (state.mode is RecoveryMode.PAUSED and observed.generation is not None)
                    or (state.mode is RecoveryMode.ACTIVE and (
                        type(observed.generation) is not int
                        or not 0 < observed.generation < 2**53 - 1))):
                raise ValueError()
            document = context_document(configuration, role="continuation",
                                        acknowledge=False, launch=None)
            document["continuation"] = {
                "epoch": state.epoch, "mode": state.mode.value,
                "binding": {"fingerprint": state.state_fingerprint,
                            "revision": state.native_revision, "generation": observed.generation},
            }
            if document["build"] != build or reader.observe() != observed:
                raise ValueError()
        # A caught ownership/SQL/file validation failure on scope exit must not
        # leave a successful response. No response is retained for replay.
        if worker_graph()[0] != build:
            raise ValueError()
        return document
    except Exception:
        raise BrowserContinuationContextError() from None
