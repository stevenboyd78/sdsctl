#!/usr/bin/env python3
"""Fail-closed change classifier for the ordinary CI workflow.

Only documentation-only changes may omit the expensive Python and browser-
namespace matrices.  Every unknown, mixed, empty, or manually dispatched
scope keeps the complete validation suite.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ZERO_SHA = "0" * 40
DOCUMENTATION_TREES = frozenset({"docs", "wiki"})
DOCUMENTATION_SUFFIXES = frozenset({".md", ".mdx", ".rst"})


@dataclass(frozen=True)
class ScopeDecision:
    full_suite: bool
    scope: str
    reason: str
    changed_paths: tuple[str, ...] = ()


def is_documentation_path(value: str) -> bool:
    """Return whether *value* is confined to a documentation-only surface."""

    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return False
    if path.suffix.lower() in DOCUMENTATION_SUFFIXES:
        return True
    return path.parts[0] in DOCUMENTATION_TREES


def classify_paths(paths: Sequence[str]) -> ScopeDecision:
    """Classify changed paths, defaulting every uncertain scope to full CI."""

    changed_paths = tuple(sorted(set(paths)))
    if not changed_paths:
        return ScopeDecision(True, "full", "no changed paths were available")

    non_documentation = tuple(path for path in changed_paths if not is_documentation_path(path))
    if non_documentation:
        return ScopeDecision(
            True,
            "full",
            f"{len(non_documentation)} runtime-sensitive or unclassified path(s) changed",
            changed_paths,
        )

    return ScopeDecision(
        False,
        "documentation",
        "all changed paths are confined to documentation surfaces",
        changed_paths,
    )


def changed_paths(repo: Path, base: str, head: str) -> tuple[str, ...]:
    """Return both sides of renames by disabling rename folding in the diff."""

    command = (
        "git",
        "-C",
        str(repo),
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        base,
        head,
        "--",
    )
    result = subprocess.run(command, check=True, capture_output=True)
    return tuple(os.fsdecode(raw_path) for raw_path in result.stdout.split(b"\0") if raw_path)


def decide_scope(*, event: str, base: str, head: str, repo: Path) -> ScopeDecision:
    """Resolve the event's comparison and return its conservative CI scope."""

    if event not in {"push", "pull_request"}:
        return ScopeDecision(True, "full", "a non-diff event requires full CI")
    if not base or not head:
        return ScopeDecision(True, "full", "base or head revision is unavailable")
    if base == ZERO_SHA:
        return ScopeDecision(True, "full", "the push has no usable before revision")
    return classify_paths(changed_paths(repo, base, head))


def write_github_output(path: Path, decision: ScopeDecision) -> None:
    """Append stable scalar outputs for downstream workflow jobs."""

    with path.open("a", encoding="utf-8") as output:
        output.write(f"full_suite={'true' if decision.full_suite else 'false'}\n")
        output.write(f"scope={decision.scope}\n")
        output.write(f"changed_count={len(decision.changed_paths)}\n")
        output.write(f"reason={decision.reason}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    decision = decide_scope(
        event=args.event,
        base=args.base,
        head=args.head,
        repo=args.repo,
    )
    if args.github_output is not None:
        write_github_output(args.github_output, decision)

    print(f"CI scope: {decision.scope}")
    print(f"Full suite: {'yes' if decision.full_suite else 'no'}")
    print(f"Reason: {decision.reason}")
    if decision.changed_paths:
        print("Changed paths:")
        for path in decision.changed_paths:
            print(f"- {path!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
