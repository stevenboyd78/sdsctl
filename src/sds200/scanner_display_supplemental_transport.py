"""Opt-in cached delivery and a separate, explicitly injected bounded demand lease."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, replace
from time import monotonic
from uuid import UUID

from .daemon_display_frames import DaemonDisplayFrames, SupplementalDisplayFrameSet
from .daemon_supplemental_acquisition import DaemonSupplementalAcquisition
from .scanner_display_reader import decode_display_packet
from .scanner_display_supplemental_wire import (
    LIFETIME,
    SupplementalContext,
    bounded_seconds,
    decode_supplemental_context,
    decode_supplemental_delivery,
    project_supplemental_web_bundle,
)

MAX_BUNDLE_BYTES = 256 * 1024
CONTEXT_PROTOCOL = "sdsctl.supplemental-context"
DEMAND_PROTOCOL = "sdsctl.supplemental-demand"


class SupplementalUnavailable(ValueError):
    """Fixed category; upstream must never echo the original exception."""


class SupplementalContextChanged(ValueError):
    """The pinned owner context no longer matches; negotiate again explicitly."""


class SupplementalDemandUnconfirmed(ValueError):
    """A renewal may have happened. Never automatically replay this mutation."""


def validate_renewal_id(value: object) -> str:
    if type(value) is not str or len(value) != 36:
        raise ValueError("Invalid supplemental renewal identifier.")
    try:
        parsed = UUID(value)
        if str(parsed) != value or parsed.version != 4:
            raise ValueError
    except ValueError:
        raise ValueError("Invalid supplemental renewal identifier.") from None
    return value


def decode_demand_response(
    value: object, expected: SupplementalContext, renewal_id: str
) -> SupplementalContext:
    """Correlate this acknowledgement, allowing only the lease epoch to advance."""
    validate_renewal_id(renewal_id)
    if (
        type(value) is not dict
        or set(value) != {"protocol", "version", "context", "renewal_id", "lease_seconds"}
        or type(value["protocol"]) is not str
        or value["protocol"] != DEMAND_PROTOCOL
        or type(value["version"]) is not int
        or value["version"] != 1
        or type(value["renewal_id"]) is not str
        or value["renewal_id"] != renewal_id
        or type(value["lease_seconds"]) is not int
        or value["lease_seconds"] != 5
    ):
        raise ValueError("Invalid supplemental demand acknowledgement.")
    context = decode_supplemental_context(value["context"])
    if (
        context.context_revision not in {expected.context_revision, expected.context_revision + 1}
        or replace(context, context_revision=expected.context_revision) != expected
    ):
        raise ValueError("Invalid supplemental demand acknowledgement.")
    return context


def decode_context_response(value: object) -> SupplementalContext:
    if (
        type(value) is not dict
        or set(value) != {"protocol", "version", "context"}
        or type(value["protocol"]) is not str
        or value["protocol"] != CONTEXT_PROTOCOL
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("Invalid supplemental context response.")
    return decode_supplemental_context(value["context"])


def validate_bundle(value: object, context: SupplementalContext) -> dict[str, object]:
    """Detach bounded JSON, then apply canonical geometry and same-cut checks."""
    try:
        if type(value) is not dict or set(value) != {
            "protocol",
            "version",
            "display",
            "supplemental",
        }:
            raise ValueError
        if (
            type(value["protocol"]) is not str
            or value["protocol"] != "sdsctl.mimic-supplemental"
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise ValueError
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), ensure_ascii=True)
        if len(encoded) > MAX_BUNDLE_BYTES:
            raise ValueError
        result = json.loads(encoded)
        display = decode_display_packet(result["display"])
        auxiliary = decode_supplemental_delivery(result["supplemental"])
        if auxiliary.context != context or any(
            display[key] != getattr(context, key)
            for key in ("endpoint_id", "stream_id", "session_id")
        ):
            raise ValueError
        if display["failure"] is not None or display["source_status"] != "matches_import":
            raise ValueError
        for frame in display["frames"].values():
            if (
                frame["profile_revision"] != context.profile_revision
                or frame["sequence"] != auxiliary.psi_sequence
                or frame["age_seconds"] != auxiliary.psi_age_seconds
                or frame["profile_status"] != "last_imported"
                or frame["profile_refresh_pending"]
                or frame["status"]
                != ("current" if auxiliary.psi_age_seconds < LIFETIME else "stale")
                or frame["screen"] is None
                or frame["screen"]["mode"]
                not in {
                    "simple_conventional",
                    "detail_conventional",
                    "simple_trunk",
                    "detail_trunk",
                }
            ):
                raise ValueError
        return result  # type: ignore[no-any-return]
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError("Invalid supplemental display bundle.") from None


class SupplementalDeliveryService:
    """Explicit dependency injection only; ordinary daemon startup omits this.

    Authorization belongs to IPC/HTTP admission. This object reads one existing
    owner's immutable capture. Cached reads never renew demand. Only demand()
    may renew an explicitly supplied, separately armed acquisition owner.
    """

    def __init__(
        self,
        frames: DaemonDisplayFrames,
        *,
        clock: Callable[[], float] = monotonic,
        acquisition: DaemonSupplementalAcquisition | None = None,
    ) -> None:
        if not isinstance(frames, DaemonDisplayFrames) or not callable(clock):
            raise TypeError("An existing display owner and monotonic clock are required.")
        if acquisition is not None and (
            type(acquisition) is not DaemonSupplementalAcquisition
            or acquisition.frames is not frames
        ):
            raise TypeError("The matching exact supplemental acquisition owner is required.")
        self._frames, self._clock = frames, clock
        self._acquisition = acquisition

    @property
    def demand_enabled(self) -> bool:
        return self._acquisition is not None

    def validate_owner(self, runtime: object, frames: object) -> None:
        if self._acquisition is not None and (
            self._acquisition._runtime is not runtime or frames is not self._frames
        ):
            raise ValueError("Supplemental demand must share the daemon runtime and display owner.")

    def _capture(
        self,
    ) -> tuple[dict[str, object], SupplementalContext, SupplementalDisplayFrameSet]:
        try:
            capture = self._frames.supplemental_frame_set()
            if capture is None:
                raise ValueError
            bundle = project_supplemental_web_bundle(capture, now=bounded_seconds(self._clock()))
            context = decode_supplemental_delivery(bundle["supplemental"]).context
            return validate_bundle(bundle, context), context, capture
        except Exception:
            raise SupplementalUnavailable("Supplemental display is unavailable.") from None

    def context(self) -> dict[str, object]:
        _, context, _ = self._capture()
        return {"protocol": CONTEXT_PROTOCOL, "version": 1, "context": asdict(context)}

    def frame(self, value: object) -> dict[str, object]:
        expected = decode_supplemental_context(value)
        bundle, current, _ = self._capture()
        if current != expected:
            raise SupplementalContextChanged("Supplemental display context changed.")
        return deepcopy(bundle)

    def demand(self, value: object, renewal_id: object) -> dict[str, object]:
        expected = decode_supplemental_context(value)
        nonce = validate_renewal_id(renewal_id)
        if self._acquisition is None:
            raise SupplementalUnavailable("Supplemental demand is not enabled.")
        _, current, capture = self._capture()
        if current != expected:
            raise SupplementalContextChanged("Supplemental display context changed.")
        # No service lock over owner/feed/cache or native I/O. A competing context
        # transition may make the post-renewal acknowledgement impossible.
        try:
            revision = self._acquisition.renew(capture)
            if revision is None:
                raise ValueError
            _, after, _ = self._capture()
            if after != replace(expected, context_revision=revision):
                raise ValueError
            result = {
                "protocol": DEMAND_PROTOCOL,
                "version": 1,
                "context": asdict(after),
                "renewal_id": nonce,
                "lease_seconds": 5,
            }
            decode_demand_response(result, expected, nonce)
            return result
        except Exception:
            raise SupplementalDemandUnconfirmed(
                "Supplemental demand was not confirmed. Stop renewal; reads may have occurred."
            ) from None
