#!/usr/bin/env python3
"""Closed recording host-plan description; no installed service or authority.

This distinct schema3 cannot reinterpret the installed idle-only schemas1/2.
All hashes, clock samples, inventories and image facts still need independent
provenance and fresh rechecks. Decoding never observes, prepares or starts a case.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import supplemental_handoff_observer as ordinary
import supplemental_handoff_policy as base
import supplemental_handoff_protected as fixed
import supplemental_recording_bootstrap as bootstrap
import supplemental_recording_clock as clock
import supplemental_recording_host as host
import supplemental_recording_projection as projection
from supplemental_recording_handoff import Contract

KIND = "finite-recording-host-plan-v1"
MAX_BYTES = 65536
MESSAGE = "Recording host plan is unconfirmed; preserve the case and do not launch."


class UnconfirmedPlan(ValueError):
    """Neither a syntactically valid plan nor its digest supplies live consent."""


def require(value):
    if not value:
        raise UnconfirmedPlan(MESSAGE)


def mapping(value, keys):
    require(type(value) is dict and set(value) == set(keys))
    return value


def text(value, pattern):
    require(type(value) is str and re.fullmatch(pattern, value) is not None)


@dataclass(frozen=True)
class RuntimePin:
    image: str
    source: str
    interpreter: str
    environment: str

    def __post_init__(self):
        ordinary.image_id(self.image)
        for value in (self.source, self.interpreter, self.environment):
            base.digest(value)


@dataclass(frozen=True)
class Deadlines:
    issued_at: float
    ready_by: float
    stop_by: float
    recover_by: float

    def __post_init__(self):
        for field in fields(self):
            base.clock(getattr(self, field.name))
        require(self.issued_at < self.ready_by <= self.issued_at + 600)
        require(self.ready_by < self.stop_by <= self.issued_at + 780)
        require(self.recover_by == self.issued_at + base.TOTAL_SECONDS)


@dataclass(frozen=True)
class Plan:
    raw: bytes
    case: str
    boot: str
    source: str
    firmware: str
    helper: RuntimePin
    candidate_runtime: RuntimePin
    cli_image: str
    cli_generation: str
    core_image: str
    core_generation: str
    core_version: str
    normal_generation: str
    normal: ordinary.AppSeal
    candidate: host.CandidateSeal
    layouts: tuple[fixed.ProtectedLayout, fixed.ProtectedLayout]
    installed_versions: tuple[tuple[str, str], ...]
    other_scanner_apps: tuple[str, ...]
    original_clock: clock.Window
    deadlines: Deadlines
    projection_sha256: str
    native_baseline_sha256: str

    def __post_init__(self):
        # The frozen decoded fields must still describe EXACTLY the pinned
        # bytes. dataclasses.replace() must not produce a different plan that
        # retains the old raw digest. No mutable caller mapping is stored.
        try:
            require(type(self.raw) is bytes and 0 < len(self.raw) <= MAX_BYTES)
            document = {
                name: getattr(self, name)
                for name in (
                    "case",
                    "boot",
                    "source",
                    "firmware",
                    "cli_image",
                    "cli_generation",
                    "core_image",
                    "core_generation",
                    "core_version",
                    "normal_generation",
                    "projection_sha256",
                    "native_baseline_sha256",
                )
            }
            for name in (
                "helper",
                "candidate_runtime",
                "normal",
                "candidate",
                "original_clock",
                "deadlines",
            ):
                document[name] = asdict(getattr(self, name))
            document["layouts"] = [
                {
                    key: str(value) if isinstance(value, Path) else value
                    for key, value in asdict(layout).items()
                }
                for layout in self.layouts
            ]
            document.update(
                schema=3,
                kind=KIND,
                network=ordinary.AUDIO_NETWORK,
                installed_versions=dict(self.installed_versions),
                other_scanner_apps=list(self.other_scanner_apps),
            )
            require(base.encode(document) == self.raw)
        except Exception:
            raise UnconfirmedPlan(MESSAGE) from None

    @property
    def sha256(self):
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def root(self):
        return Path("/mnt/data/sdsctl-recording-handoff-" + self.case)

    @property
    def native_root(self):
        return Path("/data/sdsctl-recording-" + self.case)

    @property
    def lease(self):
        # Immutable original clock conversion, never a current-time renewal.
        return {
            "schema": 1,
            "kind": "finite-recording-container-lease",
            "case": self.case,
            "boot": self.boot,
            "clock": "CLOCK_MONOTONIC",
            "issued_at": self.original_clock.after_ns / clock.NS,
            "ready_by": self.original_clock.native_deadline(self.deadlines.ready_by),
            "stop_by": self.original_clock.native_deadline(self.deadlines.stop_by),
        }

    @property
    def lease_sha256(self):
        return base.checksum(self.lease)

    @property
    def bootstrap(self):
        # The plan contains no self-referential digest. This subsequent journal
        # contract binds its already canonical bytes and original lease exactly.
        return bootstrap.Bootstrap(
            self.sha256,
            self.candidate_runtime.source,
            self.lease_sha256,
            self.deadlines.ready_by,
            self.deadlines.stop_by,
        )

    @property
    def idle_argv(self):
        return (
            "/usr/local/bin/python",
            "-I",
            "-B",
            "/opt/sdsctl-supplemental-recording/accept_supplemental_recording_idle.py",
            "--lease",
            str(self.native_root / "idle/lease.json"),
            "--lease-sha256",
            self.lease_sha256,
        )

    def check_clock(self, observed):
        """Fresh qualified kernel sample required; a reserialized sample is not one."""
        try:
            self.original_clock.check_later(observed)
            require(observed.boottime_ns / clock.NS < self.deadlines.recover_by)
        except Exception:
            raise UnconfirmedPlan(MESSAGE) from None

    def check_projection(self, observed):
        """Join retained original manifests, never recapture/adopt current files."""
        try:
            require(type(observed) is projection.Projection)
            require(observed.sha256 == self.projection_sha256)
            require(observed.native.manifest_sha256 == self.native_baseline_sha256)
            require(observed.host.contract == self.candidate.contract)
            require(
                observed.layout
                == next(item for item in self.layouts if item.slug == base.CANDIDATE)
            )
        except Exception:
            raise UnconfirmedPlan(MESSAGE) from None


INPUT_FIELDS = {
    "schema",
    "kind",
    "case",
    "boot",
    "source",
    "firmware",
    "helper",
    "candidate_runtime",
    "cli_image",
    "cli_generation",
    "core_image",
    "core_generation",
    "core_version",
    "normal_generation",
    "normal",
    "candidate",
    "layouts",
    "installed_versions",
    "other_scanner_apps",
    "original_clock",
    "deadlines",
    "projection_sha256",
    "native_baseline_sha256",
    "network",
}


def _layout(item):
    mapping(item, fixed.ProtectedLayout.__dataclass_fields__)
    require(all(type(value) is str for value in item.values()))
    for key, value in item.items():
        if key not in ("slug", "image_package_sha256"):
            path = Path(value)
            require(str(path) == value and not value.startswith("//"))
            require(all(32 <= ord(char) < 127 for char in value))
    return fixed.ProtectedLayout(
        **{
            key: value if key in ("slug", "image_package_sha256") else Path(value)
            for key, value in item.items()
        }
    )


def decode(value):
    """Freeze the closed declaration; no file imports, observations or actions.

    The normal seal keeps its FULL protected recordings inventory. The candidate
    retains its distinct static source seal and original recording contract;
    neither is converted into a pretend ordinary idle-file fingerprint. Native
    generation/launch plan are deliberately absent until actual idle init exists.
    """
    try:
        mapping(value, INPUT_FIELDS)
        require(type(value["schema"]) is int and value["schema"] == 3 and value["kind"] == KIND)
        raw = base.encode(value)
        require(0 < len(raw) <= MAX_BYTES)
        # Validate original container types before constructing only frozen
        # records/tuples. JSON round-tripping here would silently turn caller
        # tuples or mapping subclasses into apparently valid input shapes.
        base.identifier(value["case"], case=True)
        base.identifier(value["boot"])
        text(value["source"], r"[a-f0-9]{40}")
        text(value["firmware"], r"[A-Za-z0-9._ -]{1,64}")
        text(value["core_version"], r"[A-Za-z0-9._-]{1,128}")
        require(value["network"] == ordinary.AUDIO_NETWORK)
        for key in ("cli_image", "core_image"):
            ordinary.image_id(value[key])
        for key in (
            "cli_generation",
            "core_generation",
            "normal_generation",
            "projection_sha256",
            "native_baseline_sha256",
        ):
            base.digest(value[key])
        helper = RuntimePin(**mapping(value["helper"], RuntimePin.__dataclass_fields__))
        candidate_runtime = RuntimePin(
            **mapping(value["candidate_runtime"], RuntimePin.__dataclass_fields__)
        )
        normal = mapping(value["normal"], ordinary.AppSeal.__dataclass_fields__)
        normal = ordinary.AppSeal(
            **(
                normal
                | {
                    "files": ordinary.ProtectedFiles(
                        **mapping(normal["files"], ordinary.ProtectedFiles.__dataclass_fields__)
                    )
                }
            )
        )
        require(normal.slug == base.NORMAL)
        candidate = mapping(value["candidate"], host.CandidateSeal.__dataclass_fields__)
        candidate = host.CandidateSeal(
            **(
                candidate
                | {
                    "files": fixed.StaticFiles(
                        **mapping(candidate["files"], fixed.StaticFiles.__dataclass_fields__)
                    ),
                    "contract": Contract(
                        **mapping(candidate["contract"], Contract.__dataclass_fields__)
                    ),
                }
            )
        )
        require(candidate.contract.case_id == value["case"])
        require(candidate.image == candidate_runtime.image)
        require(candidate.files.package == candidate_runtime.source)
        require(type(value["layouts"]) is list and len(value["layouts"]) == 2)
        layouts = tuple(_layout(item) for item in value["layouts"])
        require({item.slug for item in layouts} == {base.NORMAL, base.CANDIDATE})
        versions = value["installed_versions"]
        require(type(versions) is dict and 2 <= len(versions) <= 256)
        for slug, version in versions.items():
            text(slug, r"[a-z0-9][a-z0-9_-]{0,127}")
            text(version, r"[A-Za-z0-9._-]{1,128}")
        other = value["other_scanner_apps"]
        require(type(other) is list and all(type(slug) is str for slug in other))
        require(len(set(other)) == len(other) and set(other) <= versions.keys())
        require(not set(other) & {base.NORMAL, base.CANDIDATE})
        require(
            {slug for slug in versions if "sds200" in slug or "sdsctl" in slug}
            - {base.NORMAL, base.CANDIDATE}
            <= set(other)
        )
        for seal in (normal, candidate):
            require(versions.get(seal.slug) == seal.version)
            layout = next(item for item in layouts if item.slug == seal.slug)
            require(layout.image_package_sha256 == seal.files.package)
            require(layout.deployment.is_relative_to(layout.data))
        # Full profile/data/context disjointness from either recording root.
        for left in layouts:
            for right in layouts:
                for path in (right.context, right.data, *right.profile_paths):
                    require(not path.is_relative_to(left.recordings))
                    require(not left.recordings.is_relative_to(path))
        left, right = layouts
        require(not left.recordings.is_relative_to(right.recordings))
        require(not right.recordings.is_relative_to(left.recordings))
        require(not set(left.profile_paths) & set(right.profile_paths))
        original = dict(mapping(value["original_clock"], clock.Window.__dataclass_fields__))
        require(type(original["namespace"]) is list and len(original["namespace"]) == 2)
        original["namespace"] = tuple(original["namespace"])
        original = clock.Window(**original)
        require(original.boot == value["boot"])
        deadlines = Deadlines(**mapping(value["deadlines"], Deadlines.__dataclass_fields__))
        require(deadlines.issued_at == original.boottime_ns / clock.NS)
        require(
            deadlines.ready_by + candidate.contract.maximum_recording_seconds + 3
            <= deadlines.stop_by
        )
        result = Plan(
            raw=raw,
            case=value["case"],
            boot=value["boot"],
            source=value["source"],
            firmware=value["firmware"],
            helper=helper,
            candidate_runtime=candidate_runtime,
            cli_image=value["cli_image"],
            cli_generation=value["cli_generation"],
            core_image=value["core_image"],
            core_generation=value["core_generation"],
            core_version=value["core_version"],
            normal_generation=value["normal_generation"],
            normal=normal,
            candidate=candidate,
            layouts=layouts,
            installed_versions=tuple(sorted(versions.items())),
            other_scanner_apps=tuple(other),
            original_clock=original,
            deadlines=deadlines,
            projection_sha256=value["projection_sha256"],
            native_baseline_sha256=value["native_baseline_sha256"],
        )
        lease = result.lease
        require(lease["issued_at"] < lease["ready_by"] < lease["stop_by"])
        require(lease["ready_by"] - lease["issued_at"] <= 600)
        require(lease["stop_by"] - lease["issued_at"] <= 780)
        return result
    except Exception:
        raise UnconfirmedPlan(MESSAGE) from None


def load_bytes(raw, expected_sha256):
    """Strict bounded canonical bytes; caller still authenticates file and pin."""
    try:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
        base.digest(expected_sha256)
        require(hashlib.sha256(raw).hexdigest() == expected_sha256)

        def unique(pairs):
            value = {}
            for key, item in pairs:
                require(key not in value)
                value[key] = item
            return value

        def constant(_):
            require(False)

        plan = decode(json.loads(raw, object_pairs_hook=unique, parse_constant=constant))
        require(plan.raw == raw)
        return plan
    except Exception:
        raise UnconfirmedPlan(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Closed recording plan only; no host service or App operation enabled.")
