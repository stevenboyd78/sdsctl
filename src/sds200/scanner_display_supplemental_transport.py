"""Opt-in cached delivery service. No auth policy, scanner I/O or demand renewal."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from time import monotonic

from .daemon_display_frames import DaemonDisplayFrames
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


class SupplementalUnavailable(ValueError):
    """Fixed category; upstream must never echo the original exception."""


class SupplementalContextChanged(ValueError):
    """The pinned owner context no longer matches; negotiate again explicitly."""


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
    owner's immutable capture, never starts a worker or requests a refresh.
    """

    def __init__(
        self, frames: DaemonDisplayFrames, *, clock: Callable[[], float] = monotonic
    ) -> None:
        if not isinstance(frames, DaemonDisplayFrames) or not callable(clock):
            raise TypeError("An existing display owner and monotonic clock are required.")
        self._frames, self._clock = frames, clock

    def _capture(self) -> tuple[dict[str, object], SupplementalContext]:
        try:
            capture = self._frames.supplemental_frame_set()
            if capture is None:
                raise ValueError
            bundle = project_supplemental_web_bundle(capture, now=bounded_seconds(self._clock()))
            context = decode_supplemental_delivery(bundle["supplemental"]).context
            return validate_bundle(bundle, context), context
        except Exception:
            raise SupplementalUnavailable("Supplemental display is unavailable.") from None

    def context(self) -> dict[str, object]:
        _, context = self._capture()
        return {"protocol": CONTEXT_PROTOCOL, "version": 1, "context": asdict(context)}

    def frame(self, value: object) -> dict[str, object]:
        expected = decode_supplemental_context(value)
        bundle, current = self._capture()
        if current != expected:
            raise SupplementalContextChanged("Supplemental display context changed.")
        return deepcopy(bundle)
