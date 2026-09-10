"""Both fixed subprocess groups must pass before publishing the combined gate."""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from scripts import run_browser_namespace_tests as runner
from scripts.check_browser_namespace_results import REQUIRED_MODULES


@pytest.fixture
def reports(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    for group, modules in runner.GROUPS.items():
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", tests=str(len(modules)),
                              failures="0", errors="0", skipped="0")
        for module in modules:
            ET.SubElement(suite, "testcase", classname=module, name="test_example")
        ET.ElementTree(root).write(stage / f"{group}.xml")
    return stage, tmp_path / "combined.xml"


def test_groups_cover_exactly_every_required_module_once():
    modules = tuple(module for group in runner.GROUPS.values() for module in group)
    assert modules == REQUIRED_MODULES
    assert len(modules) == len(set(modules))
    assert len(runner.GROUPS) == 2 and runner.MAX_SECONDS == 23 * 60


def test_success_preserves_distinct_cases_and_both_reports(reports):
    stage, report = reports
    assert runner.merge_reports(stage, report) == dict.fromkeys(REQUIRED_MODULES, 1)
    assert report.exists() and all((stage / f"{name}.xml").exists() for name in runner.GROUPS)
    assert len(list(ET.parse(report).iter("testcase"))) == 4


@pytest.mark.parametrize("fault", ["absent", "empty", "malformed", "foreign", "missing-module",
    "duplicate", "failure", "error", "skipped", "counter", "failed-counter", "symlink"])
def test_bad_batch_never_publishes_aggregate(reports, fault):
    stage, report = reports
    path = stage / "ownership.xml"
    if fault == "absent":
        path.rename(stage / "retained.xml")
    elif fault == "empty":
        path.write_text("<testsuites/>")
    elif fault == "malformed":
        path.write_text("<testsuites")
    elif fault == "symlink":
        path.rename(stage / "retained.xml")
        path.symlink_to(stage / "retained.xml")
    else:
        root = ET.parse(path).getroot()
        suite, case = root[0], root[0][0]
        if fault == "foreign":
            case.set("classname", "tests.test_other")
        elif fault == "missing-module":
            suite.remove(case)
            suite.set("tests", "2")
        elif fault == "duplicate":
            suite.append(ET.fromstring(ET.tostring(case)))
            suite.set("tests", "4")
        elif fault == "counter":
            suite.set("tests", "99")
        elif fault == "failed-counter":
            suite.set("failures", "1")
        else:
            ET.SubElement(case, fault)
        ET.ElementTree(root).write(path)
    with pytest.raises((ValueError, OSError, ET.ParseError)):
        runner.merge_reports(stage, report)
    assert not report.exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_publication_never_overwrites_existing_evidence(reports, symlink):
    stage, report = reports
    old = stage / "original"
    old.write_bytes(b"retained evidence")
    if symlink:
        report.symlink_to(old)
    else:
        report.write_bytes(old.read_bytes())
    with pytest.raises(FileExistsError):
        runner.merge_reports(stage, report)
    assert report.read_bytes() == old.read_bytes() == b"retained evidence"


@pytest.mark.parametrize("failed", [None, "ownership", "history"])
def test_fixed_commands_and_both_exit_codes_gate_publication(tmp_path, monkeypatch, failed):
    commands, merged, stopped = [], [], []

    class Job:
        def __init__(self, command, **kwargs):
            commands.append(command)
            assert kwargs["start_new_session"] is True and kwargs["stdin"] == subprocess.DEVNULL
            assert kwargs["cwd"] == runner.REPOSITORY
            self.code = 1 if failed and failed in command[-1] else 0

        def poll(self):
            return self.code

    monkeypatch.setattr(runner.subprocess, "Popen", Job)
    monkeypatch.setattr(runner, "merge_reports", lambda stage, report: merged.append(stage) or {})
    monkeypatch.setattr(runner, "_stop", lambda jobs: stopped.append(jobs))
    report = tmp_path / "combined.xml"
    if failed:
        with pytest.raises(ValueError, match="batch failed"):
            runner.run(report)
        assert merged == []
    else:
        assert runner.run(report) == {} and len(merged) == 1
    assert len(commands) == 2 and len(stopped) == 1
    for command, modules in zip(commands, runner.GROUPS.values(), strict=True):
        assert command[:5] == [runner.sys.executable, "-u", "-m", "pytest", "-q"]
        assert command[5:-1] == [module.replace(".", "/") + ".py" for module in modules]
        assert command[-1].startswith("--junitxml=")


def test_timeout_cleans_owned_jobs_and_retains_evidence_without_merge(tmp_path, monkeypatch):
    class Job:
        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return None

    stopped = []
    clock = iter([0, runner.MAX_SECONDS])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.subprocess, "Popen", Job)
    monkeypatch.setattr(runner, "_stop", lambda jobs: stopped.append(jobs))
    monkeypatch.setattr(runner, "merge_reports", lambda *args: pytest.fail("Unexpected merge"))
    with pytest.raises(TimeoutError):
        runner.run(tmp_path / "combined.xml")
    assert len(stopped) == 1 and len(stopped[0]) == 2
    assert len(list(tmp_path.glob("sdsctl-namespace-*/*.log"))) == 2


def test_environment_cannot_add_a_hidden_pytest_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k only_one_case")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected child"))
    with pytest.raises(ValueError):
        runner.run(tmp_path / "combined.xml")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux process-group cleanup")
def test_stop_reaps_its_own_term_ignoring_child_without_touching_other_groups():
    script = """
import signal
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print('ready', flush=True)
time.sleep(30)
"""
    job = subprocess.Popen([sys.executable, "-u", "-c", script],
                           stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert job.stdout is not None and select.select([job.stdout], [], [], 3)[0]
        assert job.stdout.readline().strip() == "ready"
        runner._stop({"fixture": job})
        assert job.returncode == -signal.SIGKILL
    finally:
        if job.poll() is None:
            os.killpg(job.pid, signal.SIGKILL)
            job.wait(timeout=3)
        if job.stdout is not None:
            job.stdout.close()
