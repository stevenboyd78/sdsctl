"""Internal versioned delivery candidate. No public route or acquisition opt-in.

Relative ages and independent successful-acquisition sequences only. Monotonic
server timestamps, raw packets and profile paths never cross this boundary.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from .daemon_display_frames import SupplementalDisplayFrameSet
from .scanner_display_supplemental import SupplementalValueStatus as Status
from .scanner_display_supplemental_presentation import present_supplemental_capture

MAX_SEQUENCE = 2**53 - 1
LIFETIME = 5.0
_CLOCK = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FAVORITES = re.compile(r"[012]{100}\Z")
_CONTEXT_FIELDS = {
    "endpoint_id",
    "stream_id",
    "session_id",
    "profile_revision",
    "profile_invalidation",
    "context_revision",
}


@dataclass(frozen=True, slots=True)
class SupplementalContext:
    endpoint_id: str
    stream_id: str
    session_id: str
    profile_revision: str
    profile_invalidation: int
    context_revision: int


@dataclass(frozen=True, slots=True)
class SupplementalWireValue:
    status: Status
    sample_sequence: int | None = None
    age_seconds: float | None = None
    value: str | None = None


@dataclass(frozen=True, slots=True)
class SupplementalDelivery:
    context: SupplementalContext
    psi_sequence: int
    psi_age_seconds: float
    clock: SupplementalWireValue
    favorites: SupplementalWireValue


def bounded_seconds(value: object) -> float:
    if (
        not isinstance(value, (int, float))
        or type(value) not in (int, float)
        or not 0 <= value <= 1e15
        or not math.isfinite(value)
    ):
        raise ValueError("Bounded nonnegative seconds are required.")
    return float(value)


def _sequence(value: object, *, positive: bool = False) -> int:
    # JSON has one numeric type in JavaScript: 1 and 1.0 are the same safe
    # integer there. Normalize equivalent Python JSON numbers, not booleans.
    if (
        not isinstance(value, (int, float))
        or type(value) not in (int, float)
        or not int(positive) <= value <= MAX_SEQUENCE
        or value != int(value)
    ):
        raise ValueError("Invalid sequence.")
    return int(value)


def _object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or len(value) != len(fields) or set(value) != fields:
        raise ValueError("Invalid fields.")
    return cast(dict[str, object], value)


def decode_supplemental_context(value: object) -> SupplementalContext:
    try:
        data = _object(value, _CONTEXT_FIELDS)
        identifiers = []
        for name in ("endpoint_id", "stream_id", "session_id"):
            identifier = data[name]
            if type(identifier) is not str or len(identifier) != 36:
                raise ValueError
            if str(UUID(identifier)) != identifier:
                raise ValueError
            identifiers.append(identifier)
        revision = data["profile_revision"]
        if type(revision) is not str or _HASH.fullmatch(revision) is None:
            raise ValueError
        return SupplementalContext(
            identifiers[0],
            identifiers[1],
            identifiers[2],
            revision,
            _sequence(data["profile_invalidation"]),
            _sequence(data["context_revision"]),
        )
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid supplemental context.") from None


def _value(value: object, *, clock: bool) -> SupplementalWireValue:
    data = _object(value, {"status", "sample_sequence", "age_seconds", "value"})
    if type(data["status"]) is not str:
        raise ValueError
    state = Status(data["status"])
    if state is not Status.CURRENT:
        if any(data[key] is not None for key in ("sample_sequence", "age_seconds", "value")):
            raise ValueError
        return SupplementalWireValue(state)
    sequence = _sequence(data["sample_sequence"], positive=True)
    age = bounded_seconds(data["age_seconds"])
    raw = data["value"]
    pattern = _CLOCK if clock else _FAVORITES
    if age >= LIFETIME or type(raw) is not str or pattern.fullmatch(raw) is None:
        raise ValueError
    if clock:
        datetime.fromisoformat(raw)  # Real calendar, no offset/fold/leap-second inference.
    return SupplementalWireValue(state, sequence, age, raw)


def decode_supplemental_delivery(value: object) -> SupplementalDelivery:
    """Strict, bounded shape; callers must also bound their transport body."""
    try:
        data = _object(value, {"protocol", "version", "context", "psi", "clock", "favorites"})
        if data["protocol"] != "sdsctl.supplemental" or type(data["protocol"]) is not str:
            raise ValueError
        if _sequence(data["version"]) != 1:
            raise ValueError
        psi = _object(data["psi"], {"sequence", "age_seconds"})
        result = SupplementalDelivery(
            decode_supplemental_context(data["context"]),
            _sequence(psi["sequence"]),
            bounded_seconds(psi["age_seconds"]),
            _value(data["clock"], clock=True),
            _value(data["favorites"], clock=False),
        )
        if result.psi_age_seconds >= LIFETIME and (
            result.clock.status is Status.CURRENT or result.favorites.status is Status.CURRENT
        ):
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid supplemental delivery.") from None


def project_supplemental_delivery(
    capture: SupplementalDisplayFrameSet, *, now: float
) -> dict[str, object]:
    """Serialize one *new* coherent owner cut, never a later schema-1 frame read."""
    view = present_supplemental_capture(capture, now=now)
    context = {
        "endpoint_id": capture.endpoint_id,
        "stream_id": capture.stream_id,
        "session_id": capture.session_id,
        "profile_revision": view.preferred.profile_revision,
        "profile_invalidation": capture.profile_invalidation,
        "context_revision": capture.context_revision,
    }
    result: dict[str, object] = {
        "protocol": "sdsctl.supplemental",
        "version": 1,
        "context": asdict(decode_supplemental_context(context)),
        "psi": {"sequence": capture.sequence, "age_seconds": view.preferred.age_seconds},
    }
    for name, sample, value in (
        ("clock", view.clock, view.clock.local_time.isoformat() if view.clock.local_time else None),
        (
            "favorites",
            view.favorites,
            "".join(str(int(key)) for key in view.favorites.states)
            if view.favorites.states is not None
            else None,
        ),
    ):
        current = sample.status is Status.CURRENT
        result[name] = {
            "status": sample.status.value,
            "sample_sequence": sample.sample_sequence if current else None,
            "age_seconds": sample.age_seconds if current else None,
            "value": value if current else None,
        }
    decode_supplemental_delivery(result)
    return result
