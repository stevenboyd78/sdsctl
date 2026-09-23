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


@dataclass(frozen=True)
class ProbeInputs:
    """Read-only locations and original pins, not a launchable configuration.

    Current recording files are deliberately not collected here. Their stage,
    preservation and native acknowledgments require the independent collector.
    """

    deployment: Path
    recordings: Path
    daemon_socket: Path
    firmware: str
    context: dict
    maximum_recording_seconds: float


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


def _probe_directory(path):
    """Inspect live output ancestry without acquiring the recorder's lock.

    No contents are read or published. Content changes are expected; descriptor
    and name identity (including permissions/owner/link count) must still match.
    The launch path and writer retain their original locking requirements.
    """
    opened, anchor = [], -1
    try:
        _path(str(path))
        anchor = os.open("/", protected.DIRECTORY)
        parent = anchor
        for name in path.parts[1:]:
            child = os.open(name, protected.DIRECTORY, dir_fd=parent)
            try:
                before = identity(os.fstat(child))[:6]
            except BaseException:
                os.close(child)
                raise
            opened.append((parent, name, child, before))
            parent = child
        info = os.fstat(parent)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        for parent, name, child, before in opened:
            require(identity(os.fstat(child))[:6] == before)
            require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:6] == before)
    finally:
        for _, _, child, _ in reversed(opened):
            os.close(child)
        if anchor >= 0:
            os.close(anchor)


def _read(path, *, expected_sha256, expected_source_sha256, pristine):
    """Shared immutable-input checks; the public launch path is always pristine."""
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
            if pristine:
                construction._empty_private(output)
            else:
                # A live probe may find sockets/receipts, but never accepts an
                # unsafe directory or changes its contents to make it pass.
                _probe_directory(output)
        construction._disjoint(specification.sockets, specification.receipts)
        for immutable in (path.parent, directory, deployment, config_path):
            construction._disjoint(immutable, stored.baseline.root)
        if pristine:
            protected.Collector(stored).pristine()
        require(_bytes(path, deadline) == raw and time.monotonic() < deadline)
        return deployment, LaunchPlan(
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


def load(path: Path, *, expected_sha256: str, expected_source_sha256: str) -> LaunchPlan:
    """Launch-only preflight: empty output directories and pristine recordings.

    No socket, DNS lookup, construction, child, recording action or publication.
    The caller must still recheck pins and retain independent hard termination.
    There is intentionally no flag to disable these launch-only requirements.
    """
    return _read(
        path,
        expected_sha256=expected_sha256,
        expected_source_sha256=expected_source_sha256,
        pristine=True,
    )[1]


def probe_inputs(path: Path, *, expected_sha256: str, expected_source_sha256: str) -> ProbeInputs:
    """Read the same sealed originals without adopting current recording state.

    Unlike load(), this cannot return a launch plan. It neither asserts pristine
    files nor replaces the sealed baseline with a new inventory. It is valid
    while a recording grows, and does not prove health, readiness or completion.
    """
    deployment, plan = _read(
        path,
        expected_sha256=expected_sha256,
        expected_source_sha256=expected_source_sha256,
        pristine=False,
    )
    return ProbeInputs(
        deployment,
        plan.stored.baseline.root,
        plan.specification.sockets / "api.sock",
        plan.specification.firmware,
        {
            "launch": plan.sha256,
            "source": plan.source_sha256,
            "projection": plan.projection_sha256,
            "host_plan": plan.host_plan_sha256,
            "profile": plan.profile_sha256,
            "manifest": plan.stored.manifest_sha256,
            "contract": plan.stored.contract.sha256,
            "generation": plan.generation,
        },
        plan.stored.contract.maximum_recording_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(
        "Launch-input preflight only; no executable CLI or operator action is enabled."
    )
