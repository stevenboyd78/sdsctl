#!/usr/bin/env python3
"""Pure, separately pinned expectations for the writer AND observer runtimes.

This declaration is not observed evidence, process binding, source/runtime
qualification, plan acceptance or an action grant. An independent owner must
authenticate its digest before startup and qualify both original peers against
it. Neither a peer's self-report nor the older helper pin qualifies its partner.
The newer library graph requires its distinct kind and explicit collector
selection; no installed command or action grant selects this module.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

import supplemental_recording_peer_host_source as peer_source
import supplemental_recording_runtime as runtime
import supplemental_recording_service_host_source as source
import supplemental_recording_service_template as templates
from supplemental_handoff_host import object_json

plans = templates.plans
KIND = "finite-recording-service-runtime-expectations-v1"
PEER_KIND = "finite-recording-peer-handoff-runtime-expectations-v1"
MAX_BYTES = 8192
ROLES = ("writer", "observer")
FIELDS = frozenset({"schema", "kind", "template_sha256", "source_kind", *ROLES})
ROLE_FIELDS = frozenset(
    {
        "runtime",
        "command_sha256",
        "configuration_sha256",
        "image_environment_sha256",
        "architecture",
        "timezone",
        "hostname",
    }
)
MESSAGE = "Recording peer runtime expectations are unconfirmed; no active launch is enabled."


class UnconfirmedExpectations(ValueError):
    """No raw declarations, private paths or exception details are exposed."""


def require(value):
    if not value:
        raise UnconfirmedExpectations(MESSAGE)


def _role(value):
    plans.mapping(value, ROLE_FIELDS)
    require(
        all(
            type(item) is str
            for item in plans.mapping(
                value["runtime"], plans.RuntimePin.__dataclass_fields__
            ).values()
        )
    )
    pin = plans.RuntimePin(**plans.mapping(value["runtime"], plans.RuntimePin.__dataclass_fields__))
    for name in ("command_sha256", "configuration_sha256", "image_environment_sha256"):
        require(type(value[name]) is str)
        plans.base.digest(value[name])
    require(type(value["architecture"]) is str and value["architecture"] in ("amd64", "arm64"))
    # Use the same syntax as the existing independent HelperQualification.
    # Syntax does not verify a zoneinfo file, image, process or environment.
    runtime._timezone_name(value["timezone"])
    plans.text(value["hostname"], r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?")
    return pin


def _validate(value):
    plans.mapping(value, FIELDS)
    require(type(value["schema"]) is int and value["schema"] == 1)
    require(type(value["kind"]) is str and value["kind"] in (KIND, PEER_KIND))
    require(type(value["template_sha256"]) is str)
    plans.base.digest(value["template_sha256"])
    graph = source if value["kind"] == KIND else peer_source
    require(type(value["source_kind"]) is str and value["source_kind"] == graph.KIND)
    writer, observer = (_role(value[name]) for name in ROLES)
    # Both roles use the same reviewed joint source graph. Runtime, environment,
    # command and confinement pins are still explicit and separately checked;
    # equal bytes never prove two independent original processes.
    require(writer.source == observer.source)
    return value


def _read(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    value = _validate(object_json(raw))
    require(plans.base.encode(value) == raw)
    return value


@dataclass(frozen=True)
class Expectations:
    raw: bytes = field(repr=False)

    def __post_init__(self):
        try:
            require(type(self) is Expectations)
            _read(self.raw)
        except Exception:
            raise UnconfirmedExpectations(MESSAGE) from None

    @property
    def sha256(self):
        self.__post_init__()
        return hashlib.sha256(self.raw).hexdigest()

    def check_template(self, template):
        """Exact structural join, not independent publication or live evidence."""
        try:
            self.__post_init__()
            require(type(template) is templates.Template)
            value = _read(self.raw)
            require(template.sha256 == value["template_sha256"])
            original = templates._read(template.raw)
            require(value["writer"]["runtime"] == original["plan"]["helper"])
        except Exception:
            raise UnconfirmedExpectations(MESSAGE) from None

    def check_plan(self, template, plan, original_clock):
        """Compare one template/final-plan pair without reading or renewing time.

        The caller must already own the original clock and authenticated final
        plan. Supplying a structurally matching window does not establish that
        ownership or qualify either runtime.
        """
        try:
            self.check_template(template)
            template.check_plan(plan, original_clock)
            require(asdict(plan.helper) == _read(self.raw)["writer"]["runtime"])
        except Exception:
            raise UnconfirmedExpectations(MESSAGE) from None

    def check_command(self, role, command):
        """Compare independently supplied argv, without admitting an entrypoint.

        Hashing uses a tagged canonical argv list, not a shell command string.
        The original declaration must predate observed process arguments; never
        derive a replacement pin from the process that is being qualified.
        """
        try:
            self.__post_init__()
            require(type(role) is str and role in ROLES)
            require(command_digest(command) == _read(self.raw)[role]["command_sha256"])
        except Exception:
            raise UnconfirmedExpectations(MESSAGE) from None


def command_digest(command):
    """Pure bounded argv fingerprint; neither a launch nor command permission."""
    try:
        require(type(command) is tuple and 1 <= len(command) <= 32)
        require(all(type(item) is str and 0 < len(item) <= 4096 for item in command))
        require(all(all(32 <= ord(char) < 127 for char in item) for item in command))
        require(sum(len(item) + 1 for item in command) <= 8192)
        return plans.base.checksum(
            dict(schema=1, kind="finite-recording-peer-argv-v1", argv=list(command))
        )
    except Exception:
        raise UnconfirmedExpectations(MESSAGE) from None


def decode(value):
    """The original constructor remains closed to the original source graph."""
    return _decode(value, KIND)


def decode_peer_handoff(value):
    """Explicit newer library graph, never an entrypoint or action grant."""
    return _decode(value, PEER_KIND)


def _decode(value, kind):
    try:
        _validate(value)  # Preserve exact input types before canonical encoding.
        require(value["kind"] == kind)
        return Expectations(plans.base.encode(value))
    except Exception:
        raise UnconfirmedExpectations(MESSAGE) from None


def source_profile(expectations, *, peer_handoff=False):
    """Explicit collector selection; never infer a wider profile from a pin.

    Old callers keep their old kind and 90-module graph. The newer declaration
    AND a separate exact True opt-in are required to compare the handoff graph.
    Neither selection admits any active command or qualifies the outer owner.
    """
    try:
        require(type(expectations) is Expectations and type(peer_handoff) is bool)
        value = _read(expectations.raw)
        require(value["kind"] == (PEER_KIND if peer_handoff else KIND))
        return peer_source if peer_handoff else source
    except Exception:
        raise UnconfirmedExpectations(MESSAGE) from None


def load_bytes(raw, expected_sha256):
    """The expected digest must be authenticated outside the observed peers."""
    try:
        require(type(expected_sha256) is str)
        plans.base.digest(expected_sha256)
        result = Expectations(raw)
        require(result.sha256 == expected_sha256)
        return result
    except Exception:
        raise UnconfirmedExpectations(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Offline peer expectations only; no runtime qualification or action enabled.")
