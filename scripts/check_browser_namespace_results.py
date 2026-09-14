"""Reject absent or skipped browser process-isolation tests in pytest's JUnit report.

This checks execution evidence, not real Chromium acceptance or a coverage target.
Run after a successful pytest invocation of all modules; a missing prerequisite must not
silently turn the hosted Linux namespace gate into a passing, skipped test run.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED_MODULES = (
    "tests.test_browser_device_launch",
    "tests.test_browser_device_guard_release",
    "tests.test_browser_device_continuation_intent",
    "tests.test_browser_device_continuation_history",
)


def check_report(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Expected a JUnit testsuites or testsuite report")
    counts = dict.fromkeys(REQUIRED_MODULES, 0)
    seen: set[tuple[str, str]] = set()
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        module = next((name for name in REQUIRED_MODULES
                       if classname == name or classname.startswith(name + ".")), None)
        if module is None:
            continue
        name = case.get("name", "")
        if not name or (classname, name) in seen:
            raise ValueError(f"Missing or duplicate namespace test identity in {module}")
        seen.add((classname, name))
        for outcome in ("skipped", "failure", "error"):
            if case.find(outcome) is not None:
                raise ValueError(f"Required namespace test {classname}::{name} has {outcome}")
        counts[module] += 1
    missing = [name for name, count in counts.items() if count == 0]
    if missing:
        raise ValueError("Required namespace modules absent: " + ", ".join(missing))
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="JUnit XML containing all namespace modules")
    args = parser.parse_args(argv)
    try:
        counts = check_report(args.report)
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"Browser namespace gate failed: {exc}", file=sys.stderr)
        return 1
    print(f"Browser namespace gate passed: {sum(counts.values())} tests; "
          "none skipped, failed, or errored.")
    for module, count in counts.items():
        print(f"  {module}: {count} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
