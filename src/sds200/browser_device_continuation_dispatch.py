"""Fixed continuation reads and one initial-session request; no ordinary UI route.

Only the identity/build-bound installed native wrapper selects this path. The
normal startup guard is unchanged. A browser cannot choose a role or a path.
Initial issuance trusts the installed worker to own document-bound consent;
comparison fields are NOT proof of a physical gesture or reusable authority.
"""
from __future__ import annotations

import time

from .browser_device_continuation_context import _continuation_worker_context
from .browser_device_continuation_review import _BrowserWorkerPausedReview
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_protocol import (
    BrowserContinuationInitialRequest,
    BrowserContinuationReadRequest,
)
from .browser_device_worker import BrowserWorkerSelection, worker_graph


def continuation_read_request(configuration: BrowserNativeConfiguration,
                              selection: BrowserWorkerSelection,
                              request: BrowserContinuationReadRequest) -> dict[str, object]:
    if type(request) is not BrowserContinuationReadRequest:
        raise ValueError()
    if request.action == "continuation-current":
        return _continuation_worker_context(configuration, selection)
    if request.action != "continuation-review":
        raise ValueError()
    build = worker_graph()[0]
    result = _BrowserWorkerPausedReview(configuration, selection).run()
    if worker_graph()[0] != build:
        raise ValueError()
    state = result.state
    return {"version": 1, "ok": True, "build": build, "identity": state.identity,
        "epoch": state.epoch, "mode": "paused",
        "binding": {"fingerprint": state.state_fingerprint, "revision": state.native_revision,
                    "generation": result.generation}}


def continuation_initial_request(configuration: BrowserNativeConfiguration,
                                 selection: BrowserWorkerSelection,
                                 request: BrowserContinuationInitialRequest
                                 ) -> dict[str, object]:
    """One fixed-wrapper request creates and consumes its own native attempt.

    No browser-supplied state object, proof, approval callback or token is used.
    A lost output remains uncertain; even a fresh helper cannot replay against
    the now-changed exact native state. There is no token readback or renewal.
    """
    from .browser_device_continuation_approval import _ApprovalReview
    from .browser_device_continuation_cancel import _Clock
    from .browser_device_continuation_current import _worker_current_scope
    from .browser_device_continuation_session import (
        BrowserInitialSession,
        _BrowserWorkerInitialSession,
    )
    from .browser_device_recovery import ExchangeSession, RecoveryMode

    if type(request) is not BrowserContinuationInitialRequest:
        raise ValueError()
    # Revalidate even the internal typed entry. Parsing cannot choose a role.
    request.__post_init__()
    build = worker_graph()[0]
    timer = _Clock(time.time, time.monotonic)
    with _worker_current_scope(configuration, selection) as reader:
        expected = reader.inspect()
        if (expected.mode is not RecoveryMode.PAUSED
                or (expected.epoch, expected.state_fingerprint, expected.native_revision)
                != (request.epoch, request.fingerprint, request.revision)):
            raise ValueError()
    timer.check()

    def acknowledge(review: _ApprovalReview) -> _ApprovalReview:
        # The trusted installed worker selected this request after its exact
        # document check. This comparison does not independently prove a click.
        timer.check()
        if (type(review) is not _ApprovalReview or review.state != expected
                or review.intent != request.intent
                or review.reviewed_generation != request.generation
                or worker_graph()[0] != build):
            raise ValueError()
        return review

    operation = _BrowserWorkerInitialSession(configuration, selection)
    result = operation.run(expected, intent=request.intent,
        reviewed_generation=request.generation, consent=acknowledge)
    timer.check()
    if type(result) is not BrowserInitialSession:
        raise ValueError()
    state = result.state
    if (state.mode is not RecoveryMode.ACTIVE
            or (state.identity, state.origin, state.device_id, state.epoch, state.manifest_sha256)
            != (expected.identity, expected.origin, expected.device_id,
                expected.epoch, expected.manifest_sha256)
            or state.native_revision != expected.native_revision + 2
            or state.state_fingerprint == expected.state_fingerprint
            or type(result.session) is not ExchangeSession
            or operation.confirm().state != state or worker_graph()[0] != build):
        raise ValueError()
    timer.check()
    elapsed = max(end - start for start, end in zip(timer._start, timer._last, strict=True))
    # Also deduct dispatch's full elapsed time conservatively: confirmation and
    # final graph/current-state checks may take time after the session adapter.
    checked = ExchangeSession(result.session.token, result.session.expires_in - elapsed)
    return {"version": 1, "ok": True, "build": build, "identity": state.identity,
        "epoch": state.epoch, "mode": "active",
        "binding": {"fingerprint": state.state_fingerprint, "revision": state.native_revision,
                    "generation": request.generation},
        "session": {"token": checked.token, "expires_in": checked.expires_in}}
