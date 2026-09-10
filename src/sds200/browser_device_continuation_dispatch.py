"""Read-only fixed continuation dispatch. No approval, mutation or session action.

Only the identity/build-bound installed native wrapper selects this path. The
normal startup guard is unchanged. A browser cannot choose a role or a path.
"""
from __future__ import annotations

from .browser_device_continuation_context import _continuation_worker_context
from .browser_device_continuation_review import _BrowserWorkerPausedReview
from .browser_device_native import BrowserNativeConfiguration
from .browser_device_protocol import BrowserContinuationReadRequest
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
