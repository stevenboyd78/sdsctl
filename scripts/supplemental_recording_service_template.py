#!/usr/bin/env python3
"""Pure clock-free startup declaration, not service startup or plan acceptance.

The future continuing helper must capture and retain its OWN original clock
before an independent launcher can seal its final schema3 plan. This separate
format pins every non-clock field and explicit integer budgets before startup.
It performs no clock read, I/O, publication, observation or lifecycle operation.
Previewing a plan is neither an offer nor permission to reset an existing lease.
This module is deliberately NOT in the qualified 54-module helper image graph.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

import supplemental_recording_host_plan as plans
from supplemental_handoff_host import object_json

KIND = "finite-recording-service-template-v1"
MAX_BYTES = plans.MAX_BYTES
PLAN_FIELDS = frozenset(plans.INPUT_FIELDS - {"original_clock", "deadlines"})
MESSAGE = "Recording startup declaration is unconfirmed; do not start or reseal a service."


class UnconfirmedTemplate(ValueError):
    """No supplied plan fields, private paths or exception details are exposed."""


def require(value):
    if not value:
        raise UnconfirmedTemplate(MESSAGE)


def _plan(value, original):
    require(type(original) is plans.clock.Window)
    original.__post_init__()
    require(original.boot == value["plan"]["boot"])
    issued = original.boottime_ns / plans.clock.NS
    budget = value["budget"]
    return plans.decode(
        value["plan"]
        | {
            "original_clock": asdict(original) | {"namespace": list(original.namespace)},
            "deadlines": {
                "issued_at": issued,
                "ready_by": issued + budget["ready_seconds"],
                "stop_by": issued + budget["stop_seconds"],
                "recover_by": issued + plans.base.TOTAL_SECONDS,
            },
        }
    )


def _validate(value):
    plans.mapping(value, {"schema", "kind", "plan", "budget"})
    require(type(value["schema"]) is int and value["schema"] == 1 and value["kind"] == KIND)
    plans.mapping(value["plan"], PLAN_FIELDS)
    budget = plans.mapping(value["budget"], {"ready_seconds", "stop_seconds"})
    require(all(type(item) is int for item in budget.values()))
    require(0 < budget["ready_seconds"] <= 600)
    require(budget["ready_seconds"] < budget["stop_seconds"] <= 780)
    # Reuse ALL schema3 validation without manufacturing a live clock claim.
    # This private, fixed syntax-only sentinel is discarded, never serialized
    # into the template or returned as an observation/plan by this validator.
    sentinel = plans.clock.Window(value["plan"]["boot"], (0, 1), 1, 1, 1)
    _plan(value, sentinel)
    return value


def _read(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    value = _validate(object_json(raw))
    require(plans.base.encode(value) == raw)
    return value


@dataclass(frozen=True)
class Template:
    raw: bytes = field(repr=False)
    _validated_raw: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        try:
            require(type(self) is Template)
            if hasattr(self, "_validated_raw"):
                self._original_bytes()  # Reinitialization cannot reseal changed bytes.
            _read(self.raw)
            object.__setattr__(self, "_validated_raw", self.raw)
        except Exception:
            raise UnconfirmedTemplate(MESSAGE) from None

    def _original_bytes(self):
        """Unchanged fully validated immutable value, never an observed file cache."""
        try:
            require(type(self) is Template and type(self.raw) is bytes)
            require(type(self._validated_raw) is bytes and self.raw == self._validated_raw)
            return self.raw
        except Exception:
            raise UnconfirmedTemplate(MESSAGE) from None

    @property
    def sha256(self):
        # Construction already performed full canonical/schema validation.
        # Check the original exact bytes/type and recompute their digest, rather
        # than decoding the entire clock-free plan on every protocol guard.
        # File owners still freshly read their original files at every recheck.
        return hashlib.sha256(self._original_bytes()).hexdigest()

    def preview(self, original):
        """Pure final bytes for a supplied clock; not its ownership/provenance.

        The future owner must retain its original ClockWitness, offer exactly
        once, authenticate independent acceptance and preserve original expiry.
        Calling this codec does none of those things and enables no service.
        """
        try:
            return _plan(_read(self._original_bytes()), original)
        except Exception:
            raise UnconfirmedTemplate(MESSAGE) from None

    def check_plan(self, plan, original):
        """Check every final field against the template and supplied ORIGINAL clock.

        This is structural equality only, not a fresh clock/file/process check
        or independent approval. A valid plan with another origin still refuses.
        """
        try:
            require(type(plan) is plans.Plan)
            plans.PinnedPlan(plan)
            require(plan.raw == self.preview(original).raw)
        except Exception:
            raise UnconfirmedTemplate(MESSAGE) from None


def decode(value):
    try:
        _validate(value)  # Check original types before JSON could coerce them.
        return Template(plans.base.encode(value))
    except Exception:
        raise UnconfirmedTemplate(MESSAGE) from None


def load_bytes(raw, expected_sha256):
    """Caller must authenticate the independent template digest separately."""
    try:
        plans.base.digest(expected_sha256)
        result = Template(raw)
        require(result.sha256 == expected_sha256)
        return result
    except Exception:
        raise UnconfirmedTemplate(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Offline startup declaration only; no service, clock or App action enabled.")
