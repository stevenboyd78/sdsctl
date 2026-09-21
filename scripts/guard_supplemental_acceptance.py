#!/usr/bin/env python3
"""Independent Linux process deadline for the private supplemental daemon trial.

This stops the one candidate daemon; it does NOT restore an App image or verify
Home Assistant health. A separate reviewed host restoration procedure is still
required. A fixed new persistent guard directory prevents automatic re-launch.
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from time import monotonic

from accept_supplemental_daemon import publish, start_ticks, validate_directory


def supervise(
    command: list[str],
    directory: Path,
    *,
    deadline_seconds: float,
    cancel: threading.Event,
    grace_seconds: float = 3,
) -> int:
    """Single child, fixed wall-clock deadline, pidfd TERM/KILL, no restart loop."""
    if any(
        type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= limit
        for value, limit in ((deadline_seconds, 720), (grace_seconds, 5))
    ):
        raise ValueError("Finite independent process deadlines are required.")
    validate_directory(directory)
    publish(
        directory,
        "guard-started.json",
        {
            "state": "guard_started",
            "pid": os.getpid(),
            "start_ticks": start_ticks(os.getpid()),
            "deadline_seconds": deadline_seconds,
            "grace_seconds": grace_seconds,
            "restoration_verified": False,
        },
    )
    deadline = monotonic() + deadline_seconds
    child = None
    pidfd = None
    outcome = "launch_failed"
    forced = False
    returncode = None
    try:
        if cancel.is_set():
            outcome = "cancelled_before_start"
            return 1
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
        pidfd = os.pidfd_open(child.pid)
        while True:
            returncode = child.poll()
            if returncode is not None:
                outcome = "child_exited"
                break
            if cancel.is_set():
                outcome = "cancelled"
                break
            if monotonic() >= deadline:
                outcome = "deadline_expired"
                break
            cancel.wait(min(0.05, max(0, deadline - monotonic())))
    finally:
        try:
            if child is not None and child.poll() is None:
                # A live child is still waitable: if pidfd creation failed, Popen
                # termination is restricted to our unreaped child, never a saved PID.
                try:
                    if pidfd is None:
                        child.terminate()
                    else:
                        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    returncode = child.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    forced = True
                    try:
                        if pidfd is None:
                            child.kill()
                        else:
                            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    returncode = child.wait(timeout=2)
        finally:
            if pidfd is not None:
                os.close(pidfd)
            # The child can exit after the loop decides to cancel but before
            # cleanup polls it. Use the final reaped status in either path.
            returncode = None if child is None else child.returncode
            publish(
                directory,
                "guard-result.json",
                {
                    "state": "ended",
                    "outcome": outcome,
                    "forced_kill": forced,
                    "child_returncode": returncode,
                    "child_exit_confirmed": child is not None and child.returncode is not None,
                    "restoration_verified": False,
                },
            )
    return 0 if outcome == "child_exited" and returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guard-directory", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--expected-firmware", required=True)
    parser.add_argument("--ready-timeout", type=float, default=600)
    parser.add_argument("--window-seconds", type=float, default=64)
    parser.add_argument("--max-read-attempts", type=int, default=60)
    parser.add_argument("daemon_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.guard_directory.is_absolute():
        parser.error("The new persistent guard directory must be absolute.")
    if not args.daemon_args or args.daemon_args[0] != "--":
        parser.error("Normal daemon arguments must follow --.")
    # The child validates policy/profile and hooks again before runtime startup.
    # Bound the independent deadline even when arguments are invalid.
    if (
        not math.isfinite(args.ready_timeout)
        or not 0 < args.ready_timeout <= 600
        or not math.isfinite(args.window_seconds)
        or not 0 < args.window_seconds <= 75
    ):
        parser.error("The operator wait and acquisition window must be finite.")
    args.guard_directory.mkdir(mode=0o700)
    cancel = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        nonlocal requested
        requested = True

    # The handler only sets a flag; wait cancellation is polled by a tiny adapter.
    requested = False

    class Cancellation:
        def is_set(self) -> bool:
            return requested

        def wait(self, seconds: float) -> bool:
            cancel.wait(seconds)
            return requested

    managed = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    previous = {}
    try:
        for signum in managed:
            previous[signum] = signal.signal(signum, stop)
        command = [
            sys.executable,
            str(Path(__file__).with_name("accept_supplemental_daemon.py")),
            "daemon",
            "--case-directory",
            str(args.guard_directory / "daemon-case"),
            "--source-revision",
            args.source_revision,
            "--expected-firmware",
            args.expected_firmware,
            "--ready-timeout",
            str(args.ready_timeout),
            "--window-seconds",
            str(args.window_seconds),
            "--max-read-attempts",
            str(args.max_read_attempts),
            "--guardian-pid",
            str(os.getpid()),
            "--guardian-start-ticks",
            start_ticks(os.getpid()),
            *args.daemon_args,
        ]
        return supervise(
            command,
            args.guard_directory,
            deadline_seconds=args.ready_timeout + args.window_seconds + 20,
            cancel=Cancellation(),  # type: ignore[arg-type]
        )
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
