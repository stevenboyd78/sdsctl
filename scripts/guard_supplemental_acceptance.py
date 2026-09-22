#!/usr/bin/env python3
"""Independent Linux process deadline for the private supplemental daemon trial.

This stops the one candidate daemon; it does NOT restore an App image or verify
Home Assistant health. A separate reviewed host restoration procedure is still
required. A fixed new persistent guard directory prevents automatic re-launch.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import select
import signal
import stat
import subprocess
import sys
import threading
from pathlib import Path
from time import monotonic

from accept_supplemental_daemon import publish, start_ticks, validate_directory


def _require(value: bool) -> None:
    if not value:
        raise ValueError("Finite App completion is unconfirmed; preserve the case.")


def _directory(path: Path) -> int:
    _require(path.is_absolute() and ".." not in path.parts and path != Path("/"))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _report(fd: int, name: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def reject(_value):
        raise ValueError("Non-finite completion report.")

    file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(file)
        _require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_nlink == 1
            and 0 < before.st_size <= 4096
        )
        raw = os.read(file, 4097)
        after = os.fstat(file)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        _require(len(raw) == before.st_size)
        _require(
            all(
                getattr(before, k) == getattr(after, k) == getattr(named, k)
                for k in (
                    "st_dev",
                    "st_ino",
                    "st_mode",
                    "st_uid",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            )
        )
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
        _require(type(value) is dict)
        return value
    finally:
        os.close(file)


class AppCompletion:
    """Acceptance-only witness, bound while the actual owned guardian is live.

    Used only by the source-pinned staged App supervisor. No signals, scanner
    requests, writes or restoration claim. Report contents alone never establish
    successful completion: the owned Popen return code and both pidfds must agree.
    """

    def __init__(self, directory: Path, guardian_pid: int, source: str):
        self.handles: list[int] = []
        self.used = False
        self.directory = directory
        self.guardian_pid, self.source = guardian_pid, source
        self.ready = None
        self.processes: list[int] = []
        try:
            _require(type(source) is str and re.fullmatch(r"[0-9a-f]{40}", source) is not None)
            _require(type(guardian_pid) is int and guardian_pid > 1)
            self.guard_fd = _directory(directory)
            self.handles.append(self.guard_fd)
            self.started = _report(self.guard_fd, "guard-started.json")
            _require(
                self.started
                == dict(
                    state="guard_started",
                    pid=guardian_pid,
                    start_ticks=start_ticks(guardian_pid),
                    deadline_seconds=684,
                    grace_seconds=3,
                    restoration_verified=False,
                )
            )
            self._bind(guardian_pid, os.getpid(), self.started["start_ticks"])
            self._unchanged()
            _require("guard-result.json" not in os.listdir(self.guard_fd))
        except BaseException:
            self.close()
            raise

    def _bind(self, pid: int, parent: int, ticks: str) -> None:
        handle = os.pidfd_open(pid)
        self.handles.append(handle)
        self.processes.append(handle)
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        _require(int(fields[1]) == parent)
        _require(fields[19] == ticks and not select.select([handle], [], [], 0)[0])

    def observe(self) -> None:
        """Bind readiness when published; never block App stop/sibling checks."""
        _require(not self.used and bool(self.handles))
        if self.ready is not None:
            return
        child = -1
        try:
            child = _directory(self.directory / "daemon-case")
            ready = _report(child, "ready.json")
        except FileNotFoundError:
            if child >= 0:
                os.close(child)
            return
        except BaseException:
            if child >= 0:
                os.close(child)
            raise
        self.child_fd = child
        self.handles.append(child)
        self.ready = ready
        try:
            _require(self.ready.get("kind") == "explicit-demand-clock-favorites-v1")
            _require(self.ready.get("state") == "waiting_for_operator")
            _require(self.ready.get("source_revision") == self.source)
            _require(self.ready.get("reads_require_explicit_consumer_demand") is True)
            for key, expected in (
                ("ready_timeout_seconds", 600),
                ("window_seconds", 64),
                ("max_read_attempts", 60),
            ):
                _require(type(self.ready.get(key)) in (int, float) and self.ready[key] == expected)
            self.identity = {
                k: self.ready.get(k) for k in ("kind", "pid", "start_ticks", "generation")
            }
            pid = self.identity["pid"]
            _require(type(pid) is int and pid > 1 and pid != self.guardian_pid)
            _require(
                type(self.identity["generation"]) is str
                and re.fullmatch(r"[0-9a-f]{32}", self.identity["generation"]) is not None
            )
            _require(self.identity["start_ticks"] == start_ticks(pid))
            self._bind(pid, self.guardian_pid, self.identity["start_ticks"])
            self._unchanged()
            for fd, name in ((self.guard_fd, "guard-result.json"), (self.child_fd, "result.json")):
                _require(name not in os.listdir(fd))
        except BaseException:
            self.close()
            raise

    def _unchanged(self) -> None:
        directories = [(self.guard_fd, self.directory)]
        if self.ready is not None:
            directories.append((self.child_fd, self.directory / "daemon-case"))
        for fd, path in directories:
            current = _directory(path)
            try:
                before, after = os.fstat(fd), os.fstat(current)
                _require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino))
            finally:
                os.close(current)
        _require(_report(self.guard_fd, "guard-started.json") == self.started)
        if self.ready is not None:
            _require(_report(self.child_fd, "ready.json") == self.ready)

    def finished(self, returncode: int) -> bool:
        _require(not self.used and bool(self.handles))
        self.used = True
        if type(returncode) is not int or returncode != 0:
            return False
        _require(self.ready is not None and len(self.processes) == 2)
        self._unchanged()
        _require(all(select.select([fd], [], [], 0)[0] for fd in self.processes))
        ended = _report(self.guard_fd, "guard-result.json")
        _require(type(ended.get("child_returncode")) is int)
        _require(ended.get("forced_kill") is False)
        _require(ended.get("child_exit_confirmed") is True)
        _require(ended.get("restoration_verified") is False)
        _require(
            ended
            == dict(
                state="ended",
                outcome="child_exited",
                forced_kill=False,
                child_returncode=0,
                child_exit_confirmed=True,
                restoration_verified=False,
            )
        )
        result = _report(self.child_fd, "result.json")
        _require(
            set(result)
            == {
                "kind",
                "generation",
                "state",
                "outcome",
                "ever_armed",
                "read_attempts",
                "restoration_verified",
            }
        )
        _require(
            result["kind"] == self.identity["kind"]
            and result["generation"] == self.identity["generation"]
        )
        _require(result["state"] == "ended" and result["restoration_verified"] is False)
        _require(type(result["read_attempts"]) is int and 0 <= result["read_attempts"] <= 60)
        _require(type(result["ever_armed"]) is bool)
        if result["outcome"] == "operator_wait_expired":
            _require(result["ever_armed"] is False and result["read_attempts"] == 0)
            _require(not {"arm.json", "armed.json"} & set(os.listdir(self.child_fd)))
        else:
            _require(
                result["outcome"] in ("window_expired", "quota_exhausted")
                and result["ever_armed"] is True
            )
            _require(_report(self.child_fd, "arm.json") == self.identity)
            _require(
                _report(self.child_fd, "armed.json")
                == dict(state="armed_waiting_for_demand", generation=self.identity["generation"])
            )
            if result["outcome"] == "quota_exhausted":
                _require(result["read_attempts"] == 60)
        self._unchanged()
        return True

    def close(self) -> None:
        while self.handles:
            os.close(self.handles.pop())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


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
