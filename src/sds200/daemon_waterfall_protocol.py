from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from .models import GwfResponse, PwfResponse
from .waterfall_session import (
    WaterfallSessionSnapshot,
    WaterfallSessionTransition,
)
from .waterfall_subscriptions import WaterfallDelivery

DAEMON_WATERFALL_PROTOCOL = "sdsctl.waterfall"
DAEMON_WATERFALL_VERSION = 1
DAEMON_WATERFALL_SUPPORTED_VERSIONS = (DAEMON_WATERFALL_VERSION,)
DAEMON_WATERFALL_DEFAULT_MAX_RECORD_BYTES = 64 * 1024


class DaemonWaterfallRecordKind(StrEnum):
    """Stable record kinds on the daemon-local waterfall stream."""

    SESSION_CHECKPOINT = "session.checkpoint"
    SESSION_TRANSITION = "session.transition"
    PWF = "waterfall.pwf"
    GWF = "waterfall.gwf"


@dataclass(frozen=True, slots=True)
class DaemonWaterfallRecord:
    """One versioned, ordered daemon-local waterfall JSON Lines record."""

    sequence: int
    observed_at: datetime
    kind: DaemonWaterfallRecordKind
    payload: Mapping[str, object] = field(default_factory=dict)
    protocol: str = DAEMON_WATERFALL_PROTOCOL
    version: int = DAEMON_WATERFALL_VERSION

    def __post_init__(self) -> None:
        if self.protocol != DAEMON_WATERFALL_PROTOCOL:
            raise ValueError(
                f"Unsupported waterfall protocol: {self.protocol!r}."
            )
        if self.version not in DAEMON_WATERFALL_SUPPORTED_VERSIONS:
            raise ValueError(
                f"Unsupported waterfall protocol version: {self.version!r}."
            )
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("Waterfall record sequence must be a positive integer.")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Waterfall record timestamp must include a timezone.")
        if not isinstance(self.kind, DaemonWaterfallRecordKind):
            raise TypeError("Waterfall record kind must be a DaemonWaterfallRecordKind.")
        if not isinstance(self.payload, Mapping):
            raise TypeError("Waterfall record payload must be a mapping.")
        if any(not isinstance(key, str) for key in self.payload):
            raise TypeError("Waterfall record payload keys must be strings.")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def as_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "kind": self.kind.value,
            "payload": dict(self.payload),
        }

    def to_json_line(self) -> bytes:
        return (
            json.dumps(
                self.as_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )


def waterfall_checkpoint_record(
    sequence: int,
    snapshot: WaterfallSessionSnapshot,
    *,
    observed_at: datetime | None = None,
) -> DaemonWaterfallRecord:
    return DaemonWaterfallRecord(
        sequence=sequence,
        observed_at=observed_at or datetime.now(UTC),
        kind=DaemonWaterfallRecordKind.SESSION_CHECKPOINT,
        payload=snapshot.as_dict(),
    )


def waterfall_transition_record(
    sequence: int,
    transition: WaterfallSessionTransition,
) -> DaemonWaterfallRecord:
    return DaemonWaterfallRecord(
        sequence=sequence,
        observed_at=transition.observed_at,
        kind=DaemonWaterfallRecordKind.SESSION_TRANSITION,
        payload=transition.as_dict(),
    )


def waterfall_delivery_record(
    sequence: int,
    delivery: WaterfallDelivery,
    snapshot: WaterfallSessionSnapshot,
) -> DaemonWaterfallRecord:
    response = delivery.response
    if isinstance(response, PwfResponse):
        kind = DaemonWaterfallRecordKind.PWF
    elif isinstance(response, GwfResponse):
        kind = DaemonWaterfallRecordKind.GWF
    else:
        raise TypeError("Waterfall delivery contains an unsupported response.")
    return DaemonWaterfallRecord(
        sequence=sequence,
        observed_at=response.packet.received_at,
        kind=kind,
        payload={
            "source_sequence": delivery.sequence,
            "values": response.values,
            "responses_dropped": delivery.responses_dropped,
            "overflows": delivery.overflows,
            "source_received_at": response.packet.received_at.isoformat(),
            "session": snapshot.as_dict(),
        },
    )
