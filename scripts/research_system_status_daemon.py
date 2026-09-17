#!/usr/bin/env python3
"""Temporary same-owner research launcher; never installed by ordinary packaging.

Launch the normal daemon arguments after --. Once its private ready.json exists,
an administrator can signal its verified PID with SIGUSR1 while the operator is
physically at the scanner. The signal requests one bounded research attempt; it
does not install an API operation. No signal means no research commands.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from types import FrameType
from typing import Protocol

from sds200 import cli
from sds200.daemon_runtime import DaemonRuntime
from sds200.daemon_system_status_research import (
    SystemStatusResearchPolicy,
    SystemStatusResearchResult,
)


class _Runtime(Protocol):
    def run_system_status_research(
        self, *, operator_ready: bool, timeout: float
    ) -> SystemStatusResearchResult: ...


class OperatorTrigger:
    """One trigger window, one fresh evidence directory, no retained raw data."""

    def __init__(self, directory: Path, *, ready_timeout: float = 600) -> None:
        if not directory.is_absolute() or not 0 < ready_timeout <= 1800:
            raise ValueError("A fresh absolute evidence directory and bounded wait are required.")
        # Existing files/symlinks are never overwritten or adopted as a trigger.
        directory.mkdir(mode=0o700)
        self.directory = directory
        self.ready_timeout = ready_timeout
        self._signal = threading.Event()
        self._cancel = threading.Event()
        self._armed = threading.Event()
        self._worker: threading.Thread | None = None

    def write(self, filename: str, report: dict[str, object]) -> None:
        fd = os.open(
            self.directory / filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        with os.fdopen(fd, "w") as output:
            json.dump(report, output, indent=2)

    def signal(self, _signum: int, _frame: FrameType | None) -> None:
        if self._armed.is_set():
            self._armed.clear()
            self._signal.set()

    def start(self, runtime: _Runtime) -> None:
        if self._worker is not None:
            raise RuntimeError("Research trigger can only be started once.")

        def run() -> None:
            try:
                self.write(
                    "ready.json",
                    {
                        "pid": os.getpid(),
                        "process_start_ticks": Path("/proc/self/stat")
                        .read_text()
                        .rsplit(")", 1)[1]
                        .split()[19],
                        "generation": uuid.uuid4().hex,
                        "status": "waiting_for_operator",
                        "ready_timeout_seconds": self.ready_timeout,
                        "research_started": False,
                    },
                )
                self._armed.set()
                signaled = self._signal.wait(self.ready_timeout)
                self._armed.clear()
                if self._cancel.is_set():
                    result: dict[str, object] = {"status": "cancelled", "research_started": False}
                elif not signaled:
                    result = {"status": "operator_wait_expired", "research_started": False}
                else:
                    self.write("triggered.json", {"status": "operator_trigger_received"})
                    result = dict(
                        asdict(
                            runtime.run_system_status_research(
                                operator_ready=True,
                                timeout=6.0,
                            )
                        )
                    )
                self.write("result.json", result)
            except Exception:
                # Do not export the exception message, command line or scanner values.
                self.write("launcher-error.json", {"status": "launcher_failed"})
            finally:
                self._armed.clear()

        self._worker = threading.Thread(target=run, name="system-status-research", daemon=True)
        self._worker.start()

    def cancel(self) -> None:
        self._cancel.set()
        self._armed.clear()
        self._signal.set()

    def join(self) -> None:
        if self._worker is not None:
            self._worker.join(timeout=9)
            if self._worker.is_alive():
                raise RuntimeError("Research worker did not finish within its deadline.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-firmware", required=True)
    parser.add_argument("--evidence-directory", type=Path, required=True)
    parser.add_argument("--ready-timeout", type=float, default=600)
    parser.add_argument("daemon_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.daemon_args or args.daemon_args[0] != "--":
        parser.error("Normal daemon arguments must follow --.")
    daemon_args = args.daemon_args[1:]
    parsed = cli.build_parser(suppress_configuration_defaults=True).parse_args(daemon_args)
    if parsed.action != "daemon":
        parser.error("Only the single-owner daemon may use this temporary launcher.")
    policy = SystemStatusResearchPolicy(args.expected_firmware)
    trigger = OperatorTrigger(args.evidence_directory, ready_timeout=args.ready_timeout)
    # Temporary launcher intentionally replaces this internal construction hook.
    original_runtime = cli.DaemonRuntime  # type: ignore[attr-defined]
    if original_runtime is not DaemonRuntime:
        raise RuntimeError("Review the existing daemon owner before enabling research.")
    original_signal = signal.getsignal(signal.SIGUSR1)
    constructed = False

    class ResearchRuntime(DaemonRuntime):
        def __init__(self, *runtime_args: object, **runtime_kwargs: object) -> None:
            nonlocal constructed
            if constructed:
                raise RuntimeError("Research launcher must have exactly one daemon owner.")
            constructed = True
            runtime_kwargs["system_status_research"] = policy
            super().__init__(*runtime_args, **runtime_kwargs)  # type: ignore[arg-type]
            self._research_trigger_started = False

        def start(self) -> None:
            super().start()
            if not self._research_trigger_started:
                self._research_trigger_started = True
                trigger.start(self)

        def stop(self) -> None:
            trigger.cancel()
            super().stop()

    try:
        signal.signal(signal.SIGUSR1, trigger.signal)
        cli.DaemonRuntime = ResearchRuntime  # type: ignore[attr-defined]
        return cli.main(daemon_args)
    finally:
        trigger.cancel()
        try:
            trigger.join()
        finally:
            cli.DaemonRuntime = original_runtime  # type: ignore[attr-defined]
            signal.signal(signal.SIGUSR1, original_signal)


if __name__ == "__main__":
    raise SystemExit(main())
