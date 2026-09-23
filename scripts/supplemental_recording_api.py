#!/usr/bin/env python3
"""Offline-qualified candidate API restriction; not installed by a launcher.

Keep the real recording manager visible for status and library reads, but reserve
all recording start/stop dispatch for the separate finite owner. Scanner controls,
profile reload and supplemental demand are also unavailable through this API.
This is not authentication, process ownership, a scheduler or recovery authority.
"""

from __future__ import annotations

from sds200.daemon_api import (
    DaemonApiErrorCode,
    DaemonApiResponse,
    DaemonReadOnlyApi,
)
from sds200.daemon_api import (
    DaemonApiOperation as Operation,
)
from sds200.daemon_recording import DaemonRecordingManager

# Deliberately explicit: a newly added product operation needs separate review.
OBSERVATIONS = frozenset(
    (
        Operation.HELLO,
        Operation.CAPABILITIES,
        Operation.PING,
        Operation.RUNTIME_SNAPSHOT,
        Operation.REMOTE_CLIENTS,
        Operation.DISPLAY_PROFILE,
        Operation.DISPLAY_FRAME,
        Operation.DISPLAY_SUPPLEMENTAL_CONTEXT,
        Operation.DISPLAY_SUPPLEMENTAL_FRAME,
        Operation.SCANNER_STATE,
        Operation.AUDIO_HEALTH,
        Operation.RECORDING_STATUS,
        Operation.RECORDINGS_LIST,
    )
)


class FiniteRecordingApi(DaemonReadOnlyApi):
    """Permanently restricted API for one dedicated candidate process.

    All public payload/JSON/control/authorized entry points share the native
    validation path below. Peer permissions are intersected, never broadened;
    native capabilities and result redaction use that same intersection.
    The native manager is not wrapped or replaced: the finite owner and ordinary
    daemon shutdown still operate on it directly. The future assembly must prove
    that every transport uses this API and exclude other in-process mutators.
    """

    def __init__(self, runtime, **kwargs) -> None:
        manager = kwargs.get("recording_manager")
        if type(manager) is not DaemonRecordingManager or manager.runtime is not runtime:
            raise ValueError("Finite recording API requires the matching native manager.")
        super().__init__(runtime, **kwargs)
        self._bound_runtime = runtime
        self._bound_manager = manager

    def _handle_payload(
        self,
        payload: object,
        *,
        control_only: bool,
        allowed_operations: frozenset[Operation] | None,
        redacted_result_fields: frozenset[str],
    ) -> DaemonApiResponse:
        if (
            self.runtime is not self._bound_runtime
            or self.recording_manager is not self._bound_manager
            or self._bound_manager.runtime is not self._bound_runtime
        ):
            return DaemonApiResponse.failure(
                None,
                DaemonApiErrorCode.INTERNAL_ERROR,
                "The finite recording API owner binding is unconfirmed.",
            )
        allowed = OBSERVATIONS
        if allowed_operations is not None:
            allowed = allowed.intersection(allowed_operations)
        return super()._handle_payload(
            payload,
            control_only=control_only,
            allowed_operations=allowed,
            redacted_result_fields=redacted_result_fields,
        )


if __name__ == "__main__":
    raise SystemExit("Offline recording API restriction; no server or recording started.")
