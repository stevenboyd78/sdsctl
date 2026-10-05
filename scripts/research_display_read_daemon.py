#!/usr/bin/env python3
"""Temporary same-owner one-shot GET launcher. No signal means no research reads.

Reuses only the existing private operator-trigger lifecycle; the System Status
policy is never enabled. Each fresh image pins exactly one of four GET kinds.
Never restart/rearm a failed case: preserve its evidence for review first.
"""

from __future__ import annotations

import argparse
import signal
from dataclasses import asdict
from pathlib import Path

from research_system_status_daemon import OperatorTrigger

from sds200 import cli
from sds200.daemon_display_read_research import DisplayReadKind, DisplayReadResearchPolicy
from sds200.daemon_runtime import DaemonRuntime


class DisplayReadTrigger(OperatorTrigger):
    def __init__(
        self, directory: Path, kind: DisplayReadKind, *, ready_timeout: float = 600
    ) -> None:
        if type(kind) is not DisplayReadKind:
            raise ValueError("An explicit display GET kind is required.")
        super().__init__(directory, ready_timeout=ready_timeout)
        self.kind = kind

    def write(self, filename: str, report: dict[str, object]) -> None:
        super().write(filename, {**report, "read_kind": self.kind.value})

    def invoke(self, runtime: DaemonRuntime) -> dict[str, object]:
        return dict(asdict(runtime.run_display_read_research(operator_ready=True, timeout=6.0)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-firmware", required=True)
    parser.add_argument(
        "--read-kind", choices=[kind.value for kind in DisplayReadKind], required=True
    )
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
    policy = DisplayReadResearchPolicy(args.expected_firmware, DisplayReadKind(args.read_kind))
    trigger = DisplayReadTrigger(
        args.evidence_directory, policy.kind, ready_timeout=args.ready_timeout
    )
    original_runtime = cli.DaemonRuntime
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
            runtime_kwargs["display_read_research"] = policy
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
        cli.DaemonRuntime = ResearchRuntime
        return cli.main(daemon_args)
    finally:
        trigger.cancel()
        try:
            trigger.join()
        finally:
            cli.DaemonRuntime = original_runtime
            signal.signal(signal.SIGUSR1, original_signal)


if __name__ == "__main__":
    raise SystemExit(main())
