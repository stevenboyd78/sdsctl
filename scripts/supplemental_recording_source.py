#!/usr/bin/env python3
"""Read-only fixed native-recording source inventory; no import from its roots.

The trusted host/guardian must itself come from an independently sealed bundle.
It reconstructs the expected digest from the reviewed immutable image before
calling verify(). This collector does not authenticate a self-supplied hash,
container image, Python interpreter or third-party dependencies on its own.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

import supplemental_handoff_files as files
from supplemental_handoff_policy import checksum, digest

KIND = "finite-recording-native-source-v1"
MAX_SECONDS = 8.0
MODULES = frozenset(
    {
        "accept_supplemental_recording",
        "accept_supplemental_recording_idle",
        "accept_supplemental_recording_operator",
        "accept_supplemental_recording_probe",
        "accept_supplemental_recording_web",
        "supplemental_handoff_cached",
        "supplemental_handoff_files",
        "supplemental_handoff_policy",
        "supplemental_recording_api",
        "supplemental_recording_assembly",
        "supplemental_recording_channel",
        "supplemental_recording_construction",
        "supplemental_recording_control",
        "supplemental_recording_evidence",
        "supplemental_recording_guardian",
        "supplemental_recording_handoff",
        "supplemental_recording_launch_plan",
        "supplemental_recording_monitor",
        "supplemental_recording_owner",
        "supplemental_recording_probe",
        "supplemental_recording_protected",
        "supplemental_recording_schedule",
        "supplemental_recording_source",
        "supplemental_recording_watchdog",
        "supplemental_recording_web_peer",
        "supplemental_recording_web_plan",
        "supplemental_recording_web_scope",
        "supplemental_recording_web_service",
        "supplemental_recording_wire",
    }
)
NATIVE_FILES = frozenset(name + ".py" for name in MODULES)
REQUIRED_RUNTIME = frozenset(
    {
        "__init__.py",
        "daemon_process.py",
        "daemon_runtime.py",
        "daemon_recording.py",
        "daemon_supplemental_acquisition.py",
        "network_audio.py",
    }
)
MESSAGE = "Finite recording source is unconfirmed; do not release the native launch gate."


class UnconfirmedSource(ValueError):
    """No candidate code was imported or executed by this collector."""


def require(value):
    if not value:
        raise UnconfirmedSource(MESSAGE)


@dataclass(frozen=True)
class Evidence:
    sha256: str
    runtime_sha256: str
    native_sha256: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class Layout:
    runtime: Path
    native: Path

    def validate(self):
        for path in (self.runtime, self.native):
            require(type(path) is type(Path()) and path.is_absolute() and path != Path("/"))
            require(".." not in path.parts)
        require(not self.runtime.is_relative_to(self.native))
        require(not self.native.is_relative_to(self.runtime))

    def _snapshot(self):
        runtime, native = files.inventory(self.runtime), files.inventory(self.native)
        require(set(runtime) >= REQUIRED_RUNTIME)
        require(set(native) == NATIVE_FILES)
        # No extra empty directory/namespace or bytecode directory is allowed in
        # the private helper root. Runtime package data AND bytecode are included
        # in their full inventory, never filtered out as harmless by extension.
        fd = os.open(self.native, files.DIRECTORY)
        try:
            before, names = files.identity(os.fstat(fd)), set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    require(entry.name in NATIVE_FILES and entry.name not in names)
                    require(
                        stat.S_ISREG(os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode)
                    )
                    names.add(entry.name)
            require(names == NATIVE_FILES and files.identity(os.fstat(fd)) == before)
        finally:
            os.close(fd)
        count, size = len(runtime) + len(native), 0
        require(count <= files.MAX_FILES)
        for item in (*runtime.values(), *native.values()):
            require(item["mode"] & 0o7022 == 0)  # No special bits or group/other write.
            size += item["size"]
            require(size <= files.MAX_TOTAL_BYTES)
        return {"schema": 1, "kind": KIND, "runtime": runtime, "native": native}, count, size

    def observe(self):
        """Hash twice, with no source import, deployment, chmod or file cleanup."""
        try:
            self.validate()
            deadline = time.monotonic() + MAX_SECONDS
            first, count, size = self._snapshot()
            require(time.monotonic() < deadline)
            second, count2, size2 = self._snapshot()
            require((first, count, size) == (second, count2, size2) and time.monotonic() < deadline)
            return Evidence(
                checksum(first), checksum(first["runtime"]), checksum(first["native"]), count, size
            )
        except Exception:
            raise UnconfirmedSource(MESSAGE) from None

    def verify(self, expected_sha256):
        try:
            digest(expected_sha256)
            observed = self.observe()
            require(observed.sha256 == expected_sha256)
            return observed
        except Exception:
            raise UnconfirmedSource(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Read-only source evidence only; no native recording launch is enabled.")
