#!/usr/bin/env python3
"""Build bounded, scanner-free host-runtime fixtures; never execute them here.

This does not observe or control an App and is NOT the handoff guard. Each fresh
fixture requires a UUIDv4, an already-installed immutable image ID, and its own
reviewed launch. Do not resend a launch after an uncertain SSH result. The host
must reconcile that exact unit/container using independent read-only checks.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from uuid import UUID

MAX_REMOTE_COMMAND_BYTES = 8000  # Below HAOS Dropbear's 9000-byte command limit.


def log_markers(log: str) -> tuple[bool, bool]:
    """Only worker output lines count, not systemd's printed command arguments."""
    if not isinstance(log, str) or len(log.encode()) > 65536:
        raise ValueError("Invalid runtime fixture log.")
    lines = log.splitlines()
    starts, completions = (lines.count("SDSCTL_RUNTIME_" + tag) for tag in ("STARTED", "COMPLETED"))
    if (
        starts > 1
        or completions > starts
        or (
            completions
            and lines.index("SDSCTL_RUNTIME_COMPLETED") < lines.index("SDSCTL_RUNTIME_STARTED")
        )
    ):
        raise ValueError("Ambiguous runtime fixture markers.")
    return bool(starts), bool(completions)


@dataclass(frozen=True)
class RuntimeFixture:
    unit: str
    container: str
    argv: tuple[str, ...]

    @property
    def remote_command(self) -> str:
        command = shlex.join(self.argv)
        if len(command.encode()) > MAX_REMOTE_COMMAND_BYTES:
            raise ValueError("Runtime fixture command exceeds the transport bound.")
        return command


def fixture(image: str, fixture_id: str, mode: str) -> RuntimeFixture:
    """Two fixed probes only: normal completion and a forced deadline cleanup.

    Completion waits 12s, allowing the launch SSH session to end before a second
    session observes it. Expiry deliberately ignores TERM and waits 60s; the
    8s service deadline and bounded ExecStopPost must remove that exact container.
    A Docker CLI exit alone is never evidence that the container has exited.
    """
    if not isinstance(image, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
        raise ValueError("An immutable local image ID is required.")
    try:
        parsed = UUID(fixture_id)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A fresh fixture UUIDv4 is required.") from None
    if fixture_id != parsed.hex or parsed.version != 4:
        raise ValueError("A fresh fixture UUIDv4 is required.")
    if mode not in ("complete", "expire"):
        raise ValueError("Unknown runtime fixture mode.")
    name = "sdsctl-runtime-" + fixture_id
    program = (
        "import signal,sys,time; "
        "assert hasattr(time,'CLOCK_BOOTTIME'); "
        "assert not any(n.startswith('sds200') for n in sys.modules); "
        + ("signal.signal(signal.SIGTERM,signal.SIG_IGN); " if mode == "expire" else "")
        + "print('SDSCTL_RUNTIME_STARTED',flush=True); "
        + ("time.sleep(12); " if mode == "complete" else "time.sleep(60); ")
        + "print('SDSCTL_RUNTIME_COMPLETED',flush=True)"
    )
    argv = (
        "/usr/bin/systemd-run",
        "--unit=" + name,
        "--no-ask-password",
        "--expand-environment=no",
        "--service-type=exec",
        "--property=RemainAfterExit=yes",
        "--property=Restart=no",
        "--property=RuntimeMaxSec=" + ("30s" if mode == "complete" else "8s"),
        "--property=TimeoutStopSec=5s",
        # This remains supervised if the attached Docker CLI dies. It has no
        # shell, wildcard, App name, or user-provided command argument.
        "--property=ExecStopPost=-/usr/bin/docker stop --time=2 " + name,
        "/usr/bin/docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--pids-limit=32",
        "--memory=64m",
        "--cpus=0.25",
        "--entrypoint=/usr/local/bin/python",
        image,
        "-I",
        "-B",
        "-c",
        program,
    )
    result = RuntimeFixture(name + ".service", name, argv)
    _ = result.remote_command  # Check the byte bound before the caller can launch.
    return result


if __name__ == "__main__":
    raise SystemExit("Fixture builder only; no host operation was attempted.")
