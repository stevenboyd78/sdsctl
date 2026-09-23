#!/usr/bin/env python3
"""Read-only source evidence for the prospective recording host helper.

The 14-file installed idle helper cannot import this recording mechanism graph.
This separately tagged bundle includes its closed private imports AND the full
product package used by the return verifier. It never imports the observed code.
Interpreter, stdlib, third-party packages, loader environment, immutable image,
mounts and the future service entrypoint remain independent qualification gates.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

import supplemental_handoff_files as files
from supplemental_handoff_policy import checksum, digest

KIND = "finite-recording-host-source-v1"
MAX_SECONDS = 8.0
ROOTS = frozenset(
    "supplemental_recording_" + name
    for name in ("host_source", "bootstrap", "ready", "begin", "relay", "exit", "host", "binding")
)
MODULES = frozenset(
    {
        "supplemental_handoff_" + name
        for name in (
            "executor",
            "files",
            "host",
            "observer",
            "policy",
            "process",
            "protected",
            "recovery",
        )
    }
    | {
        "supplemental_recording_" + name
        for name in (
            "attachment",
            "begin",
            "binding",
            "bootstrap",
            "bridge",
            "channel",
            "checkpoints",
            "clock",
            "dispatch",
            "engine",
            "evidence",
            "exec_stream",
            "execution",
            "exit",
            "handoff",
            "host",
            "host_source",
            "monitor",
            "namespace",
            "owner",
            "preservation",
            "projection",
            "protected",
            "ready",
            "recovery",
            "relay",
            "retained",
            "source",
            "static",
            "wire",
        )
    }
)
HELPER_FILES = frozenset(name + ".py" for name in MODULES)
REQUIRED_RUNTIME = frozenset({"__init__.py", "daemon_recording.py"})
MESSAGE = "Recording host source is unconfirmed; do not launch the private host helper."


class UnconfirmedSource(ValueError):
    """A supplied source digest does not authenticate its own provenance."""


def require(value):
    if not value:
        raise UnconfirmedSource(MESSAGE)


@dataclass(frozen=True)
class Evidence:
    sha256: str
    runtime_sha256: str
    helper_sha256: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class Layout:
    runtime: Path
    helper: Path

    def _snapshot(self):
        runtime, helper = files.inventory(self.runtime), files.inventory(self.helper)
        require(set(runtime) >= REQUIRED_RUNTIME and set(helper) == HELPER_FILES)
        # inventory() includes every file, but an extra empty namespace must
        # also fail. This helper tree is deliberately flat and closed.
        fd = os.open(self.helper, files.DIRECTORY)
        try:
            before, names = files.identity(os.fstat(fd)), set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    require(entry.name in HELPER_FILES and entry.name not in names)
                    require(
                        stat.S_ISREG(os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode)
                    )
                    names.add(entry.name)
            require(names == HELPER_FILES and files.identity(os.fstat(fd)) == before)
        finally:
            os.close(fd)
        count, size = len(runtime) + len(helper), 0
        require(count <= files.MAX_FILES)
        for item in (*runtime.values(), *helper.values()):
            require(item["mode"] & 0o7022 == 0)
            size += item["size"]
            require(size <= files.MAX_TOTAL_BYTES)
        return {"schema": 1, "kind": KIND, "runtime": runtime, "helper": helper}, count, size

    def observe(self):
        try:
            for path in (self.runtime, self.helper):
                require(type(path) is type(Path()) and path.is_absolute() and path != Path("/"))
                require(".." not in path.parts)
            require(not self.runtime.is_relative_to(self.helper))
            require(not self.helper.is_relative_to(self.runtime))
            deadline = time.monotonic() + MAX_SECONDS
            first, count, size = self._snapshot()
            require(time.monotonic() < deadline)
            second, count2, size2 = self._snapshot()
            require((first, count, size) == (second, count2, size2) and time.monotonic() < deadline)
            return Evidence(
                checksum(first), checksum(first["runtime"]), checksum(first["helper"]), count, size
            )
        except Exception:
            raise UnconfirmedSource(MESSAGE) from None

    def verify(self, expected_sha256):
        try:
            digest(expected_sha256)
            result = self.observe()
            require(result.sha256 == expected_sha256)
            return result
        except Exception:
            raise UnconfirmedSource(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Read-only recording host source only; no host service or dispatch enabled.")
