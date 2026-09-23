#!/usr/bin/env python3
"""Offline-qualified candidate API restriction; not installed by a launcher.

Keep the real recording manager visible for status and library reads, but reserve
all recording start/stop dispatch for the separate finite owner. Scanner controls,
profile reload are also unavailable through this API. Supplemental demand is
denied unless separately bound to the exact native finite acquisition owner.
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
from sds200.daemon_supplemental_acquisition import DaemonSupplementalAcquisition
from sds200.scanner_display_supplemental_transport import SupplementalDeliveryService

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
        self._acquisition = None
        self._delivery = None
        self._acquisition_binding_attempted = False

    def bind_acquisition(self, acquisition, delivery) -> None:
        """Bind once, without arming or renewing demand; never grant controls.

        The native owner still gates every demand by its explicit arm, deadline,
        read quota and current scanner context. A failed binding is not retried.
        This is an internal assembly operation, not an API request.
        """
        if self._acquisition_binding_attempted:
            raise ValueError("Finite acquisition binding cannot be retried.")
        self._acquisition_binding_attempted = True
        self._acquisition, self._delivery = acquisition, delivery
        if not self.acquisition_binding_valid():
            raise ValueError("The finite acquisition binding is unconfirmed.")

    def acquisition_binding_valid(self) -> bool:
        owner, delivery = self._acquisition, self._delivery
        return (
            type(owner) is DaemonSupplementalAcquisition
            and type(delivery) is SupplementalDeliveryService
            and self.runtime is self._bound_runtime is owner._runtime
            and self._bound_runtime._supplemental_acquisition is owner
            and delivery._acquisition is owner
            and delivery._frames is owner.frames is self.display_frames
            and self.supplemental_display is delivery
        )

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
            or (self._acquisition_binding_attempted and not self.acquisition_binding_valid())
        ):
            return DaemonApiResponse.failure(
                None,
                DaemonApiErrorCode.INTERNAL_ERROR,
                "The finite recording API owner binding is unconfirmed.",
            )
        allowed = OBSERVATIONS
        if self._acquisition_binding_attempted:
            allowed = allowed | {Operation.DISPLAY_SUPPLEMENTAL_DEMAND}
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
