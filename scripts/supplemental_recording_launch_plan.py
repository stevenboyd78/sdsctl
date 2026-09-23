#!/usr/bin/env python3
"""Bounded explicit native launch inputs; offline preflight, no process launch.

The fixed guardian must independently authenticate its child executable/source
and the expected plan digest before calling this reader. A self-supplied digest
does not authenticate anything. Ordinary CLI args/configuration are not accepted.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import supplemental_handoff_cached as cached
import supplemental_recording_construction as construction
import supplemental_recording_protected as protected
from supplemental_handoff_files import identity

from sds200.scanner_display_configuration import (
    ScannerDisplayConfiguration,
    _read_configuration,
    load_scanner_display_configuration,
)

MAX_BYTES = 16384
MAX_SECONDS = 5.0
MESSAGE = "Finite recording launch inputs are unconfirmed; no runtime was started."


class UnconfirmedLaunchPlan(ValueError):
    """Fixed diagnostics never include private configuration or path contents."""


def require(value):
    if not value:
        raise UnconfirmedLaunchPlan(MESSAGE)


@dataclass(frozen=True)
class LaunchPlan:
    specification: construction.Specification
    stored: protected.StoredBaseline
    configuration: ScannerDisplayConfiguration
    generation: str
    source_sha256: str
    projection_sha256: str
    host_plan_sha256: str
    profile_sha256: str
    sha256: str


def _bytes(path, deadline):
    require(type(path) is type(Path()) and path.name == "launch.json")
    with protected._private_directory(path.parent, exclusive=False) as fd:
        before = identity(os.fstat(fd))
        info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
        raw = protected.evidence.read_bytes(fd, path.name, limit=MAX_BYTES, deadline=deadline)
        require(identity(os.stat(path.name, dir_fd=fd, follow_symlinks=False)) == identity(info))
        require(identity(os.fstat(fd)) == before)
    return raw


def _path(value):
    return protected._path(value)


def load(path: Path, *, expected_sha256: str, expected_source_sha256: str) -> LaunchPlan:
    """Validate pinned original baseline/profile and only the declared services.

    Pure preflight: no socket, DNS lookup, daemon/recorder construction, child,
    recording action, receipt publication or operator request. The caller must
    still recheck pins at launch and retain independent hard termination.
    """
    try:
        for digest in (expected_sha256, expected_source_sha256):
            protected.evidence.digest(digest)
        deadline = time.monotonic() + MAX_SECONDS
        raw = _bytes(path, deadline)
        require(hashlib.sha256(raw).hexdigest() == expected_sha256)
        value = json.loads(
            raw,
            object_pairs_hook=protected.evidence.unique,
            parse_constant=protected.evidence.reject_constant,
        )
        protected._mapping(
            value,
            {
                "schema",
                "kind",
                "specification",
                "baseline",
                "profile",
                "generation",
                "source_sha256",
                "projection_sha256",
                "host_plan_sha256",
            },
        )
        require(type(value["schema"]) is int and value["schema"] == 1)
        require(value["kind"] == "finite-recording-native-launch-v1")
        require(protected.encode(value) == raw)
        for key in ("generation", "source_sha256", "projection_sha256", "host_plan_sha256"):
            protected.evidence.digest(value[key])
        require(value["source_sha256"] == expected_source_sha256)
        fields = protected._mapping(
            value["specification"], set(construction.Specification.__dataclass_fields__)
        )
        specification = construction.Specification(
            **(fields | {key: _path(fields[key]) for key in ("sockets", "receipts")})
        )
        original = protected._mapping(value["baseline"], {"directory", "contract", "sha256"})
        contract = protected.Contract(
            **protected._mapping(original["contract"], set(protected.Contract.__dataclass_fields__))
        )
        directory = _path(original["directory"])
        stored = protected.load_baseline(
            directory, expected_contract=contract, expected_sha256=original["sha256"]
        )
        require(stored.writer.uid == os.geteuid() and stored.writer.gid == os.getegid())
        require(3 + specification.read_window_seconds + 10 <= contract.maximum_recording_seconds)
        profile = protected._mapping(value["profile"], {"deployment", "sha256"})
        protected.evidence.digest(profile["sha256"])
        deployment = _path(profile["deployment"])
        observed, matching = cached.profile_files(deployment, stored.baseline.root)
        require(observed == profile["sha256"])
        config_path = _path(
            tomllib.loads(_read_configuration(deployment).decode())["profile_config"]
        )
        configuration = load_scanner_display_configuration(config_path)
        configuration.require_scanner_target(
            f"udp://{specification.host}:{specification.control_port}"
        )
        require(configuration.scanner_target == matching["target"])
        require(cached.profile_files(deployment, stored.baseline.root) == (observed, matching))
        # Runtime publication cannot overlap its immutable inputs or recording
        # root. All private output directories already exist before any reader
        # pins ancestor identities; preflight never creates them.
        fixed = (
            path.parent,
            directory,
            deployment,
            config_path,
            configuration.source_path,
            configuration.state_directory,
            stored.baseline.root,
        )
        for output in (specification.sockets, specification.receipts):
            for other in fixed:
                construction._disjoint(output, other)
            construction._empty_private(output)
        construction._disjoint(specification.sockets, specification.receipts)
        for immutable in (path.parent, directory, deployment, config_path):
            construction._disjoint(immutable, stored.baseline.root)
        protected.Collector(stored).pristine()
        require(_bytes(path, deadline) == raw and time.monotonic() < deadline)
        return LaunchPlan(
            specification,
            stored,
            configuration,
            value["generation"],
            value["source_sha256"],
            value["projection_sha256"],
            value["host_plan_sha256"],
            observed,
            expected_sha256,
        )
    except Exception:
        raise UnconfirmedLaunchPlan(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit(
        "Launch-input preflight only; no executable CLI or operator action is enabled."
    )
