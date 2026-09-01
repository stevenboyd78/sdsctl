#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from sds200.gw2_research import (
    Gw2CandidateForm,
    Gw2ProbePlan,
    Gw2ResearchLimits,
    capture_gw2_udp,
    write_private_gw2_capture,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Review or execute one bounded, exact-byte GW2 UDP research probe. "
            "This tool does not interpret FFT values."
        )
    )
    parser.add_argument("--target", required=True, help="Exact scanner IPv4 address")
    parser.add_argument("--port", type=int, default=50536, help="Scanner UDP control port")
    parser.add_argument(
        "--scanner-model",
        required=True,
        choices=("SDS200",),
        help="Physically observed scanner model",
    )
    parser.add_argument(
        "--scanner-firmware",
        required=True,
        help="Physically observed firmware string retained in capture provenance",
    )
    parser.add_argument(
        "--candidate",
        required=True,
        choices=[item.value for item in Gw2CandidateForm],
        help="One reviewed specification-derived candidate form",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New private JSON evidence path; existing files are never replaced",
    )
    parser.add_argument("--max-datagram-bytes", type=int, default=4096)
    parser.add_argument("--max-datagrams", type=int, default=32)
    parser.add_argument("--max-elapsed-seconds", type=float, default=3.0)
    parser.add_argument("--inactivity-seconds", type=float, default=0.75)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--review",
        action="store_true",
        help="Print the exact plan and confirmation token without network access",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Execute the exact confirmed plan",
    )
    parser.add_argument(
        "--confirmation",
        help="Full SHA-256 token printed by a separate --review invocation",
    )
    parser.add_argument(
        "--scanner-owner-stopped",
        action="store_true",
        help="Assert the scanner-owning daemon was independently verified stopped",
    )
    return parser


def _plan(args: argparse.Namespace) -> Gw2ProbePlan:
    return Gw2ProbePlan(
        target_host=args.target,
        target_port=args.port,
        candidate_form=Gw2CandidateForm(args.candidate),
        output_path=args.output,
        scanner_model=args.scanner_model,
        scanner_firmware=args.scanner_firmware,
        limits=Gw2ResearchLimits(
            max_datagram_bytes=args.max_datagram_bytes,
            max_datagrams=args.max_datagrams,
            max_elapsed_seconds=args.max_elapsed_seconds,
            inactivity_seconds=args.inactivity_seconds,
        ),
    )


def _review_document(plan: Gw2ProbePlan) -> dict[str, object]:
    return {
        "status": "UNEXECUTED",
        "plan": plan.as_dict(),
        "confirmation_token": plan.confirmation_token,
        "network_access_performed": False,
        "required_external_guard": (
            "Verify the sole scanner-owning daemon stopped before execution and "
            "restore it after execution regardless of probe outcome."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        plan = _plan(args)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    if args.review:
        print(json.dumps(_review_document(plan), indent=2, sort_keys=True))
        return 0

    if not args.confirmation:
        parser.error("--execute requires --confirmation from a separate review")
    if not args.scanner_owner_stopped:
        parser.error("--execute requires --scanner-owner-stopped")
    if plan.output_path.exists():
        parser.error("--output must not already exist")
    if not plan.output_path.parent.is_dir():
        parser.error("--output parent directory must already exist")

    try:
        result = capture_gw2_udp(
            plan,
            confirmation_token=args.confirmation,
            scanner_owner_stopped=args.scanner_owner_stopped,
            monotonic=time.monotonic,
        )
        write_private_gw2_capture(plan.output_path, result)
    except (OSError, TypeError, ValueError) as exc:
        print(f"GW2 research probe failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "status": "completed" if result.safe_completion else "needs_review",
        "safe_completion": result.safe_completion,
        "end_reason": result.end_reason,
        "datagram_count": len(result.observations),
        "start_sent": result.start_sent,
        "cleanup_sent": result.cleanup_sent,
        "capture_error": result.capture_error,
        "cleanup_error": result.cleanup_error,
        "evidence_path": str(plan.output_path.resolve()),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if result.safe_completion else 3


if __name__ == "__main__":
    raise SystemExit(main())
