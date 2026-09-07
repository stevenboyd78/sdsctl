"""Experimental administrator workflow; no production launcher or CLI enables it.

Call only from an authenticated administrator boundary, in a bounded worker.
This module is not authentication. Mutations commit before contacting the sole
web owner; failed acknowledgement never rolls back authority or repeats rotation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .browser_device_owner import (
    BrowserOwnerError,
    BrowserOwnerReceipt,
    request_browser_owner_ack,
)
from .browser_device_store import (
    BrowserDeviceRecord,
    BrowserDeviceState,
    BrowserDeviceStore,
    BrowserDeviceStoreError,
    IssuedBrowserDevice,
    validate_browser_device_record,
)


class BrowserAdminStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    OWNER_UNAVAILABLE = "owner_unavailable"
    SUPERSEDED = "superseded"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"


@dataclass(frozen=True, slots=True)
class BrowserAdminResult:
    """Point-in-time acknowledgement, never a promise against later changes."""

    record: BrowserDeviceRecord
    status: BrowserAdminStatus
    receipt: BrowserOwnerReceipt | None = None

    def as_dict(self) -> dict[str, object]:
        """Explicit redacted status document; never includes a credential."""
        return {
            "device_id": self.record.device_id,
            "generation": self.record.generation,
            "state": self.record.state.value,
            "status": self.status.value,
            "completed": self.status is BrowserAdminStatus.CONFIRMED,
        }


@dataclass(frozen=True, slots=True)
class BrowserAdminRotation:
    """One-time private handoff, even if ACK fails. Never generically serialize."""

    result: BrowserAdminResult
    credential: str = field(repr=False)


class BrowserDeviceAdmin:
    def __init__(self, store: BrowserDeviceStore) -> None:
        self._store = store

    def inventory(self) -> tuple[BrowserDeviceRecord, ...]:
        return self._store.inventory()

    def enroll(self, device_id: str) -> IssuedBrowserDevice:
        """Create a new identity; does not claim the web service is ready."""
        return self._store.enroll(device_id)

    def transition(
        self, expected: BrowserDeviceRecord, target: BrowserDeviceState,
    ) -> BrowserAdminResult:
        validate_browser_device_record(expected)
        committed = self._store.transition(expected.device_id, target, expected=expected)
        return self.confirm(committed)

    def rotate(self, expected: BrowserDeviceRecord) -> BrowserAdminRotation:
        validate_browser_device_record(expected)
        issued = self._store.rotate(expected.device_id, expected=expected)
        return BrowserAdminRotation(self.confirm(issued.record), issued.credential)

    def confirm(self, record: BrowserDeviceRecord) -> BrowserAdminResult:
        """Retry ONLY acknowledgement for this exact record; never mutate it.

        An owner response and a subsequent inventory check must agree. A missing
        owner is unknown completion, not proof of zero old streams. If another
        administrator changes the device during ACK, this result is superseded.
        """
        validate_browser_device_record(record)
        state = self._current(record)
        if state is not None:
            return BrowserAdminResult(record, state)
        receipt = None
        status = BrowserAdminStatus.OWNER_UNAVAILABLE
        try:
            receipt = request_browser_owner_ack(self._store.path, record)
            if receipt.record == record:
                status = (BrowserAdminStatus.CONFIRMED if receipt.completed
                          else BrowserAdminStatus.PENDING)
            else:
                receipt = None  # Never accept an acknowledgement for another record.
        except BrowserOwnerError:
            pass
        state = self._current(record)
        return BrowserAdminResult(record, state or status, receipt)

    def _current(self, record: BrowserDeviceRecord) -> BrowserAdminStatus | None:
        try:
            if record not in self._store.inventory():
                return BrowserAdminStatus.SUPERSEDED
        except BrowserDeviceStoreError:
            return BrowserAdminStatus.AUTHORITY_UNAVAILABLE
        return None
