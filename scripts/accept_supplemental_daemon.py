#!/usr/bin/env python3
"""Private, one-shot clock/global Favorites acceptance launcher.

Not installed by packaging or selected by the normal CLI/App. Use only from a
reviewed source-pinned staging inventory, with an independent restoration guard.
The source-revision argument records that inventory pin; it is not a verifier.
A NEW persistent case directory is mandatory, including after a process restart.
Startup and cached readers cannot arm acquisition. The separate ``arm`` command
checks the ready process identity and sends one generation-bound request.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import signal
import stat
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic

from sds200 import cli, daemon_display_frames
from sds200.daemon_api import DaemonReadOnlyApi
from sds200.daemon_display_frames import DaemonDisplayFrames
from sds200.daemon_display_profile import DaemonDisplayProfile
from sds200.daemon_process import DaemonProcess, DaemonProcessResult, DaemonSignalController
from sds200.daemon_runtime import DaemonRuntime
from sds200.daemon_supplemental_acquisition import (
    DaemonSupplementalAcquisition,
    SupplementalAcquisitionPolicy,
)
from sds200.scanner_display_supplemental_transport import (
    SupplementalDeliveryService,
    SupplementalUnavailable,
)

CASE_KIND = "explicit-demand-clock-favorites-v1"


def require_guardian(pid: int, ticks: str) -> None:
    """Fail before construction if the Linux deadline parent is absent or changed.

    SIGKILL on unexpected guardian death is deliberate: a stuck interpreter must
    not keep the scanner connection. The normal path uses bounded graceful TERM.
    Check again after prctl to close the parent-exits-during-installation race.
    """
    if sys.platform != "linux" or pid <= 1 or os.getppid() != pid or start_ticks(pid) != ticks:
        raise ValueError("The exact independent deadline guardian is required.")
    libc = ctypes.CDLL(None, use_errno=True)
    # Linux PR_SET_PDEATHSIG. This only affects this short-lived private process.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise RuntimeError("Cannot install the independent guardian-death stop.")
    if os.getppid() != pid or start_ticks(pid) != ticks:
        raise ValueError("The independent deadline guardian exited.")


def start_ticks(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def publish(directory: Path, name: str, value: dict[str, object]) -> None:
    """Publish a complete private report without replacing any existing evidence."""
    temporary = directory / (".pending-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, directory / name, follow_symlinks=False)
    finally:
        temporary.unlink()


def read_private(path: Path) -> dict[str, object]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= 4096
        ):
            raise ValueError("A bounded private regular evidence file is required.")
        data = stream.read(4097)
    if len(data) > 4096:
        raise ValueError("Acceptance evidence is oversized.")
    value = json.loads(data)
    if type(value) is not dict:
        raise ValueError("Acceptance evidence must be an object.")
    return value


def validate_directory(directory: Path) -> None:
    info = directory.lstat()
    if (
        not directory.is_absolute()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("An absolute private acceptance directory is required.")


def arm_case(directory: Path) -> None:
    """Linux pidfd prevents signaling a reused PID; an uncertain send is not retried."""
    validate_directory(directory)
    ready = read_private(directory / "ready.json")
    identity = {key: ready.get(key) for key in ("kind", "pid", "start_ticks", "generation")}
    pid = identity["pid"]
    if (
        identity["kind"] != CASE_KIND
        or type(pid) is not int
        or pid <= 1
        or type(identity["start_ticks"]) is not str
        or not re.fullmatch(r"[0-9]+", identity["start_ticks"])
        or type(identity["generation"]) is not str
        or not re.fullmatch(r"[0-9a-f]{32}", identity["generation"])
        or ready.get("state") != "waiting_for_operator"
        or (directory / "result.json").exists()
    ):
        raise ValueError("No current acceptance window can be armed.")
    # Open before checking /proc, so a replacement process is never targeted.
    fd = os.pidfd_open(pid)
    try:
        if start_ticks(pid) != identity["start_ticks"]:
            raise ValueError("Acceptance process identity changed.")
        publish(directory, "arm.json", identity)
        signal.pidfd_send_signal(fd, signal.SIGUSR1)
    finally:
        os.close(fd)


class AcceptanceTrigger:
    """One finite operator wait and one separately finite demand window."""

    def __init__(
        self,
        directory: Path,
        policy: SupplementalAcquisitionPolicy,
        *,
        source_revision: str,
        ready_timeout: float = 600,
    ) -> None:
        if (
            type(policy) is not SupplementalAcquisitionPolicy
            or not directory.is_absolute()
            or type(source_revision) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
            or type(ready_timeout) not in (int, float)
            or not math.isfinite(ready_timeout)
            or not 0 < ready_timeout <= 600
        ):
            raise ValueError("A new pinned acceptance case and finite wait are required.")
        directory.mkdir(mode=0o700)
        validate_directory(directory)
        self.directory, self.policy = directory, policy
        self.source_revision, self.ready_timeout = source_revision, ready_timeout
        self.identity = {
            "kind": CASE_KIND,
            "pid": os.getpid(),
            "start_ticks": start_ticks(os.getpid()),
            "generation": uuid.uuid4().hex,
        }
        self._cancel = threading.Event()
        self._accept_signal = self._requested = False
        self._worker: threading.Thread | None = None

    def signal(self, _signum: int, _frame: object) -> None:
        # No locks, file I/O, commands or blocking operations in the signal handler.
        if self._accept_signal:
            self._accept_signal = False
            self._requested = True

    def start(
        self,
        owner: DaemonSupplementalAcquisition,
        delivery: SupplementalDeliveryService,
        signals: DaemonSignalController,
    ) -> None:
        if self._worker is not None:
            raise RuntimeError("The acceptance trigger cannot be started twice.")
        if (
            type(owner) is not DaemonSupplementalAcquisition
            or owner._policy != self.policy
            or type(delivery) is not SupplementalDeliveryService
            or delivery._acquisition is not owner
            or type(signals) is not DaemonSignalController
        ):
            raise ValueError("Trigger policy and delivery must match the exact native owner.")
        self._worker = threading.Thread(
            target=self._run,
            args=(owner, delivery, signals),
            name="supplemental-acceptance",
            daemon=True,
        )
        self._worker.start()

    def _current(
        self, owner: DaemonSupplementalAcquisition, delivery: SupplementalDeliveryService
    ) -> bool:
        if owner._qualified() is not True:
            return False
        try:
            delivery.context()  # Cached compatible PSI/profile only; no demand or probe.
        except SupplementalUnavailable:
            return False
        return True

    def _run(
        self,
        owner: DaemonSupplementalAcquisition,
        delivery: SupplementalDeliveryService,
        signals: DaemonSignalController,
    ) -> None:
        outcome = "cancelled"
        try:
            deadline = monotonic() + self.ready_timeout
            ready = False
            while not self._cancel.is_set():
                if monotonic() >= deadline:
                    outcome = "operator_wait_expired"
                    break
                if not ready and self._current(owner, delivery):
                    if self._cancel.is_set():
                        break
                    if monotonic() >= deadline:
                        outcome = "operator_wait_expired"
                        break
                    # Set before publishing; arm_case cannot see a partial ready file.
                    self._accept_signal = True
                    publish(
                        self.directory,
                        "ready.json",
                        self.identity
                        | {
                            "state": "waiting_for_operator",
                            "source_revision": self.source_revision,
                            "ready_timeout_seconds": self.ready_timeout,
                            "window_seconds": self.policy.window_seconds,
                            "max_read_attempts": self.policy.max_read_attempts,
                            "reads_require_explicit_consumer_demand": True,
                        },
                    )
                    ready = True
                if self._requested:
                    request = read_private(self.directory / "arm.json")
                    if request != self.identity:
                        outcome = "trigger_identity_refused"
                        break
                    current = self._current(owner, delivery)
                    if self._cancel.is_set():
                        break
                    if monotonic() >= deadline:
                        outcome = "operator_wait_expired"
                        break
                    if not current or not owner.arm():
                        outcome = "preflight_refused"
                        break
                    publish(
                        self.directory,
                        "armed.json",
                        {
                            "state": "armed_waiting_for_demand",
                            "generation": self.identity["generation"],
                        },
                    )
                    while not self._cancel.wait(0.05):
                        status = owner.status()
                        if status.ended:
                            outcome = status.reason or "ended"
                            break
                    break
                self._cancel.wait(0.05)
        except Exception:
            # Do not export raw scanner values, paths, arguments or exceptions.
            outcome = "launcher_failed"
        finally:
            self._accept_signal = False
            try:
                owner._end("acceptance_ended")
                status = owner.status()
                publish(
                    self.directory,
                    "result.json",
                    {
                        "kind": CASE_KIND,
                        "generation": self.identity["generation"],
                        "state": "ended",
                        "outcome": outcome,
                        "ever_armed": status.armed,
                        "read_attempts": status.read_attempts,
                        "restoration_verified": False,
                    },
                )
            finally:
                signals.request_stop()

    def cancel(self) -> None:
        self._accept_signal = False
        self._cancel.set()

    def join(self) -> None:
        if self._worker is not None:
            self._worker.join(timeout=2)
            if self._worker.is_alive():
                raise RuntimeError("Acceptance cleanup needs the independent deadline guard.")


class NativeAssembly:
    """Capture original native objects; never subclass or replace the scanner owner."""

    def __init__(self, trigger: AcceptanceTrigger) -> None:
        self.trigger = trigger
        self.runtime: DaemonRuntime | None = None
        self.api: DaemonReadOnlyApi | None = None
        self.owner: DaemonSupplementalAcquisition | None = None
        self.delivery: SupplementalDeliveryService | None = None
        self.process: DaemonProcess | None = None
        self._run_attempted = False

    @staticmethod
    def validate_hooks() -> None:
        if (
            cli.DaemonRuntime is not DaemonRuntime
            or cli.DaemonReadOnlyApi is not DaemonReadOnlyApi
            or cli.DaemonProcess is not DaemonProcess
            or daemon_display_frames.DaemonDisplayFrames is not DaemonDisplayFrames
        ):
            raise RuntimeError("Review pre-existing daemon construction hooks first.")

    def make_runtime(self, *args: object, **kwargs: object) -> DaemonRuntime:
        if self.runtime is not None:
            raise RuntimeError("Only one native runtime is allowed.")
        self.runtime = DaemonRuntime(*args, **kwargs)  # type: ignore[arg-type]
        return self.runtime

    def make_api(self, runtime: object, **kwargs: object) -> DaemonReadOnlyApi:
        if self.api is not None or runtime is not self.runtime or runtime is None:
            raise RuntimeError("Only the same native runtime API is allowed.")
        self.api = DaemonReadOnlyApi(runtime, **kwargs)  # type: ignore[arg-type]
        if self.api.display_frames is not None or self.api.supplemental_display is not None:
            raise RuntimeError("An existing display owner cannot be replaced.")
        return self.api

    def make_frames(self, profile: object, scanner: object) -> DaemonDisplayFrames:
        if (
            self.owner is not None
            or self.runtime is None
            or self.api is None
            or type(profile) is not DaemonDisplayProfile
            or profile is not self.api.display_profile
            or scanner is not self.runtime.scanner
            or self.api.display_frames is not None
            or self.api.supplemental_display is not None
        ):
            raise RuntimeError("Exactly one matching native display profile/feed is required.")
        self.owner = DaemonSupplementalAcquisition(self.runtime, profile, self.trigger.policy)
        self.delivery = SupplementalDeliveryService(self.owner.frames, acquisition=self.owner)
        self.delivery.validate_owner(self.runtime, self.owner.frames)
        self.api.supplemental_display = self.delivery
        return self.owner.frames

    def make_process(self, runtime: object, **kwargs: object) -> NativeAssembly:
        if self.process is not None or runtime is not self.runtime or runtime is None:
            raise RuntimeError("Only one native daemon process is allowed.")
        self.process = DaemonProcess(runtime, **kwargs)  # type: ignore[arg-type]
        return self

    def run(self) -> DaemonProcessResult:
        if self._run_attempted:
            raise RuntimeError("The acceptance daemon lifecycle cannot be retried.")
        self._run_attempted = True
        if (
            self.process is None
            or self.owner is None
            or self.delivery is None
            or self.api is None
            or self.api.display_frames is not self.owner.frames
            or self.api.supplemental_display is not self.delivery
            or self.api.runtime is not self.runtime
            or self.process.runtime is not self.runtime
            or type(self.process.signals) is not DaemonSignalController
        ):
            raise RuntimeError("Incomplete acceptance assembly; refusing runtime startup.")
        self.delivery.validate_owner(self.runtime, self.api.display_frames)
        self.owner.start()
        self.trigger.start(self.owner, self.delivery, self.process.signals)
        return self.process.run()

    @contextmanager
    def installed(self) -> Iterator[None]:
        self.validate_hooks()
        previous_signal = signal.getsignal(signal.SIGUSR1)
        try:
            signal.signal(signal.SIGUSR1, self.trigger.signal)
            cli.DaemonRuntime = self.make_runtime
            cli.DaemonReadOnlyApi = self.make_api
            cli.DaemonProcess = self.make_process
            daemon_display_frames.DaemonDisplayFrames = self.make_frames
            yield
        finally:
            self.trigger.cancel()
            cleanup_complete = False
            try:
                self.trigger.join()
            finally:
                try:
                    if self.owner is not None:
                        self.owner.close()
                    cleanup_complete = (
                        self.trigger._worker is None or not self.trigger._worker.is_alive()
                    )
                    if self.owner is not None:
                        worker = self.owner.frames.quick_key_worker_status()
                        cleanup_complete = (
                            cleanup_complete
                            and worker is not None
                            and not worker.alive
                            and self.runtime._supplemental_acquisition is None
                        )
                finally:
                    cli.DaemonRuntime = DaemonRuntime
                    cli.DaemonReadOnlyApi = DaemonReadOnlyApi
                    cli.DaemonProcess = DaemonProcess
                    daemon_display_frames.DaemonDisplayFrames = DaemonDisplayFrames
                    signal.signal(signal.SIGUSR1, previous_signal)
                    publish(
                        self.trigger.directory,
                        "cleanup.json",
                        {
                            "state": "closed",
                            "cleanup_complete": cleanup_complete,
                            "owner_constructed": self.owner is not None,
                            "assembly_run_attempted": self._run_attempted,
                            "restoration_verified": False,
                        },
                    )
            if not cleanup_complete:
                raise RuntimeError("Acceptance cleanup was not confirmed; preserve this case.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    arm = sub.add_parser("arm")
    arm.add_argument("--case-directory", required=True, type=Path)
    launch = sub.add_parser("daemon")
    launch.add_argument("--case-directory", required=True, type=Path)
    launch.add_argument("--source-revision", required=True)
    launch.add_argument("--expected-firmware", required=True)
    launch.add_argument("--guardian-pid", type=int, required=True)
    launch.add_argument("--guardian-start-ticks", required=True)
    launch.add_argument("--ready-timeout", type=float, default=600)
    launch.add_argument("--window-seconds", type=float, default=64)
    launch.add_argument("--max-read-attempts", type=int, default=60)
    launch.add_argument("daemon_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.operation == "arm":
        arm_case(args.case_directory)
        return 0
    if not args.daemon_args or args.daemon_args[0] != "--":
        parser.error("Normal daemon arguments must follow --.")
    daemon_args = args.daemon_args[1:]
    parsed = cli.build_parser(suppress_configuration_defaults=True).parse_args(daemon_args)
    if parsed.action != "daemon" or parsed.scanner_display_profile_config is None:
        parser.error("Only a daemon with an explicit existing display profile is allowed.")
    require_guardian(args.guardian_pid, args.guardian_start_ticks)
    NativeAssembly.validate_hooks()
    policy = SupplementalAcquisitionPolicy(
        args.expected_firmware, args.window_seconds, args.max_read_attempts
    )
    trigger = AcceptanceTrigger(
        args.case_directory,
        policy,
        source_revision=args.source_revision,
        ready_timeout=args.ready_timeout,
    )
    assembly = NativeAssembly(trigger)
    with assembly.installed():
        return cli.main(daemon_args)


if __name__ == "__main__":
    raise SystemExit(main())
