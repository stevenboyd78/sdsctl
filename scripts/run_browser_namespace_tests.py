"""Run every required namespace module in two isolated, bounded pytest processes.

This changes scheduling, not coverage, assertions, prerequisites or the CI gate.
All per-batch logs/reports are retained. No aggregate is published unless both
processes pass and their exact, non-overlapping module reports validate.
"""
from __future__ import annotations

import argparse
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from pathlib import Path

if __package__:
    from .check_browser_namespace_results import REQUIRED_MODULES, check_report
else:
    from check_browser_namespace_results import REQUIRED_MODULES, check_report

GROUPS = {"ownership": REQUIRED_MODULES[:3], "history": REQUIRED_MODULES[3:]}
MAX_SECONDS = 23 * 60  # Leaves setup/cleanup margin inside the unchanged 25m job.
REPOSITORY = Path(__file__).resolve().parents[1]


def merge_reports(stage: Path, report: Path) -> dict[str, int]:
    aggregate = ET.Element("testsuites")
    seen: set[tuple[str, str]] = set()
    for name, modules in GROUPS.items():
        path = stage / f"{name}.xml"
        if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Unsafe or oversized batch report")
        root = ET.parse(path).getroot()
        suites = list(root) if root.tag == "testsuites" else [root]
        if root.tag not in {"testsuite", "testsuites"} or not suites:
            raise ValueError("Invalid batch report root")
        counts = dict.fromkeys(modules, 0)
        for suite in suites:
            cases = list(suite.findall("testcase"))
            if (suite.tag != "testsuite" or suite.findall("testsuite")
                    or int(suite.get("tests", "-1")) != len(cases)
                    or any(int(suite.get(key, "-1")) != 0
                           for key in ("failures", "errors", "skipped"))):
                raise ValueError("Incomplete or unsuccessful batch report")
            for case in cases:
                classname, test_name = case.get("classname", ""), case.get("name", "")
                module = next((m for m in modules
                               if classname == m or classname.startswith(m + ".")), None)
                key = classname, test_name
                if module is None or not test_name or key in seen:
                    raise ValueError("Foreign, missing or duplicated namespace case")
                if any(case.find(key) is not None for key in ("skipped", "failure", "error")):
                    raise ValueError("Unsuccessful namespace case")
                counts[module] += 1
                seen.add(key)
            aggregate.append(suite)
        if not all(counts.values()):
            raise ValueError("Required namespace module absent from its batch")
    combined = stage / "combined.xml"
    with combined.open("xb") as stream:
        ET.ElementTree(aggregate).write(stream, encoding="utf-8", xml_declaration=True)
        stream.flush()
        os.fsync(stream.fileno())
    counts = check_report(combined)  # Preserve the existing independent strict gate.
    os.link(combined, report)  # Atomic, no overwrite of an existing report or symlink.
    return counts


def _stop(jobs: dict[str, subprocess.Popen]) -> None:
    live = [job for job in jobs.values() if job.poll() is None]
    for job in live:
        with suppress(ProcessLookupError):
            os.killpg(job.pid, signal.SIGTERM)  # Only groups created by this runner.
    until = time.monotonic() + 2
    for job in live:
        try:
            job.wait(timeout=max(0.01, until - time.monotonic()))
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(job.pid, signal.SIGKILL)
            job.wait(timeout=2)


def run(report: Path) -> dict[str, int]:
    if (not sys.platform.startswith("linux") or not report.is_absolute()
            or report.parent.resolve() != report.parent
            or report.exists() or report.is_symlink() or os.environ.get("PYTEST_ADDOPTS")):
        raise ValueError("Expected a new absolute report path on Linux")
    stage = Path(tempfile.mkdtemp(prefix="sdsctl-namespace-", dir=report.parent))
    print(f"Namespace evidence retained in {stage}", flush=True)
    jobs: dict[str, subprocess.Popen] = {}
    results: dict[str, int] = {}
    started = time.monotonic()
    try:
        for name, modules in GROUPS.items():
            command = [sys.executable, "-u", "-m", "pytest", "-q",
                       *(module.replace(".", "/") + ".py" for module in modules),
                       f"--junitxml={stage / (name + '.xml')}"]
            with (stage / f"{name}.log").open("xb") as stream:
                jobs[name] = subprocess.Popen(command, cwd=REPOSITORY, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            print(f"Started {name}: {len(modules)} fixed namespace modules", flush=True)
        while len(results) < len(jobs):
            if time.monotonic() - started >= MAX_SECONDS:
                raise TimeoutError("Namespace batches exceeded their bounded runtime")
            for name, job in jobs.items():
                code = job.poll()
                if name not in results and code is not None:
                    results[name] = code
                    print(f"::group::Namespace {name} (exit {code})", flush=True)
                    print((stage / f"{name}.log").read_text(errors="replace"), flush=True)
                    print("::endgroup::", flush=True)
            if len(results) < len(jobs):
                time.sleep(0.2)
        if any(code != 0 for code in results.values()):
            raise ValueError("At least one namespace batch failed; aggregate not published")
        return merge_reports(stage, report)
    finally:
        _stop(jobs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path,
                        help="New absolute combined JUnit report; individual evidence is retained")
    args = parser.parse_args(argv)
    old = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise InterruptedError("Namespace runner interrupted; evidence retained")

    try:
        signal.signal(signal.SIGTERM, interrupted)
        counts = run(args.report)
        print(f"All {sum(counts.values())} namespace cases passed; no skips or duplicates.")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"Namespace execution failed: {error}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old)


if __name__ == "__main__":
    raise SystemExit(main())
