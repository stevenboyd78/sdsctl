#!/usr/bin/env python3
"""Closed finite-web startup inputs; no listener, client or launch authority.

The future host must derive the request from its actual original Ready and
authenticate the exact web exec/source/runtime independently. Matching caller
PIDs or digests do not establish those facts. Reuse the sealed live-input reader,
never the launch-only pristine-file preflight or a fresh recording inventory.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import supplemental_recording_launch_plan as launch
import supplemental_recording_probe as probe
import supplemental_recording_wire as wire

MESSAGE = "Finite dashboard launch is unconfirmed; preserve the case."
KIND = "finite-recording-web-startup"
FIELDS = frozenset({"schema", "kind", "context", "guardian", "native", "watchdog"})
CONTEXT = frozenset(
    {
        "launch",
        "source",
        "projection",
        "host_plan",
        "profile",
        "manifest",
        "contract",
        "generation",
        "ready_by",
    }
)


def require(value):
    if not value:
        raise ValueError(MESSAGE)


def _path(value):
    require(type(value) is type(Path()))
    launch.protected._path(str(value))
    return value


@dataclass(frozen=True)
class Prepared:
    """Immutable request and locations, NOT readiness or permission to listen.

    No cached health flag, new deadline, listener options or arbitrary command is
    carried. The eventual entrypoint must check actual actors/peers, original
    inputs and source before admission, and retain independent process recovery.
    """

    path: Path
    launch_sha256: str
    source_sha256: str
    request: bytes
    expected: probe.Expected
    sockets: Path
    deployment: Path
    recordings: Path
    firmware: str
    ready_by: float
    maximum_recording_seconds: float

    def __post_init__(self):
        for path in (self.path, self.sockets, self.deployment, self.recordings):
            _path(path)
        require(self.path.name == "launch.json")
        for value in (self.launch_sha256, self.source_sha256):
            launch.protected.evidence.digest(value)
        require(type(self.request) is bytes and 0 < len(self.request) <= wire.MAX_BYTES)
        document = json.loads(
            self.request, object_pairs_hook=wire._pairs, parse_constant=wire._reject
        )
        require(wire.encode(document) == self.request)
        launch.protected._mapping(document, FIELDS)
        launch.protected._mapping(document["context"], CONTEXT)
        require(type(document["schema"]) is int and document["schema"] == 1)
        require(document["kind"] == KIND)
        for key in CONTEXT - {"ready_by"}:
            launch.protected.evidence.digest(document["context"][key])
        require(type(document["context"]["ready_by"]) in (int, float))
        require(type(self.expected) is probe.Expected)
        self.expected.validate()
        require(type(self.ready_by) is float and math.isfinite(self.ready_by))
        require(time.monotonic() < self.ready_by)
        require(type(self.maximum_recording_seconds) in (int, float))
        require(
            math.isfinite(self.maximum_recording_seconds) and self.maximum_recording_seconds > 0
        )
        require(self.expected.deadline == self.ready_by + self.maximum_recording_seconds)
        require(document["context"]["ready_by"] == self.ready_by)
        require(document["context"]["launch"] == self.launch_sha256)
        require(document["context"]["source"] == self.source_sha256)
        require(document["context"]["profile"] == self.expected.profile_sha256)
        for role in ("guardian", "native"):
            require(document[role] == asdict(getattr(self.expected, role)))
        require(
            document["watchdog"]
            == asdict(self.expected.watchdog) | {"deadline": self.expected.deadline, "grace": 3}
        )
        require(
            wire.encode(
                document
                | {
                    "guardian": asdict(self.expected.guardian),
                    "native": asdict(self.expected.native),
                    "watchdog": asdict(self.expected.watchdog)
                    | {"deadline": self.expected.deadline, "grace": 3},
                }
            )
            == self.request
        )
        require(type(self.firmware) is str and 0 < len(self.firmware) <= 64)

    @property
    def request_sha256(self):
        return hashlib.sha256(self.request).hexdigest()

    def recheck(self):
        """Read the same original inputs; cannot recapture baseline or renew."""
        try:
            require(
                prepare(
                    json.loads(self.request),
                    self.path,
                    plan_sha256=self.launch_sha256,
                    source_sha256=self.source_sha256,
                )
                == self
            )
        except Exception:
            raise ValueError(MESSAGE) from None


def prepare(request, path, *, plan_sha256, source_sha256):
    """Validate an explicit original Ready-shaped request against sealed inputs.

    No deadline is derived from the current time. Startup must finish before the
    ORIGINAL ready_by; the continuing service lifetime is the original native
    watchdog deadline, not a web-specific extension. These facts still need the
    host's authenticated provenance; this reader does not discover processes.
    """
    try:
        fields = launch.protected._mapping
        fields(request, FIELDS)
        require(type(request["schema"]) is int and request["schema"] == 1)
        require(request["kind"] == KIND)
        raw = wire.encode(request)
        _path(path)
        inputs = launch.probe_inputs(
            path, expected_sha256=plan_sha256, expected_source_sha256=source_sha256
        )
        require(type(inputs) is launch.ProbeInputs)
        context = fields(request["context"], CONTEXT)
        ready_by = context["ready_by"]
        require(type(ready_by) in (int, float) and math.isfinite(ready_by))
        require(0 < ready_by - time.monotonic() <= 600)
        require(context == inputs.context | {"ready_by": ready_by})
        actors = []
        for role in ("guardian", "native", "watchdog"):
            keys = {"pid", "start_ticks", "uid", "gid"}
            value = fields(
                request[role], keys | ({"deadline", "grace"} if role == "watchdog" else set())
            )
            actors.append(probe.Process(**{key: value[key] for key in keys}))
        watch = request["watchdog"]
        require(type(watch["deadline"]) in (int, float) and math.isfinite(watch["deadline"]))
        require(watch["deadline"] == ready_by + inputs.maximum_recording_seconds)
        require(type(watch["grace"]) is int and watch["grace"] == 3)
        require(inputs.daemon_socket.name == "api.sock")
        result = Prepared(
            path,
            plan_sha256,
            source_sha256,
            raw,
            probe.Expected(*actors, context["profile"], watch["deadline"]),
            inputs.daemon_socket.parent,
            inputs.deployment,
            inputs.recordings,
            inputs.firmware,
            float(ready_by),
            inputs.maximum_recording_seconds,
        )
        require(wire.encode(request) == raw)
        require(
            launch.probe_inputs(
                path, expected_sha256=plan_sha256, expected_source_sha256=source_sha256
            )
            == inputs
        )
        require(time.monotonic() < ready_by)
        return result
    except Exception:
        raise ValueError(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Uninstalled finite WebUI input reader; no listener or launch enabled.")
