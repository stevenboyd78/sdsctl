"""The CI gate must reject missing execution, not only failing tests."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.check_browser_namespace_results import REQUIRED_MODULES, check_report, main


@pytest.fixture
def report(tmp_path):
    def make(cases=None, *, root_tag="testsuites"):
        root = ET.Element(root_tag)
        suite = ET.SubElement(root, "testsuite") if root_tag == "testsuites" else root
        if cases is None:
            cases = [(module, "test_example", None) for module in REQUIRED_MODULES]
        for module, name, outcome in cases:
            case = ET.SubElement(suite, "testcase", classname=module, name=name)
            if outcome:
                ET.SubElement(case, outcome)
        path = tmp_path / "results.xml"
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        return path
    return make


@pytest.mark.parametrize("root_tag", ["testsuites", "testsuite"])
def test_required_modules_pass(report, root_tag):
    assert check_report(report(root_tag=root_tag)) == dict.fromkeys(REQUIRED_MODULES, 1)


def test_classes_and_parameterized_cases_are_counted(report):
    cases = [(module + ".TestGroup", f"test_example[{n}]", None)
             for module in REQUIRED_MODULES for n in range(3)]
    assert check_report(report(cases)) == dict.fromkeys(REQUIRED_MODULES, 3)


@pytest.mark.parametrize("module", REQUIRED_MODULES)
@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_required_case_cannot_be_skipped_or_unsuccessful(report, module, outcome):
    cases = [(name, "test_pass", None) for name in REQUIRED_MODULES]
    cases.append((module, "test_not_passed", outcome))
    with pytest.raises(ValueError, match=f"has {outcome}"):
        check_report(report(cases))


@pytest.mark.parametrize("missing", [*REQUIRED_MODULES, "all"])
def test_missing_module_is_not_a_vacuous_pass(report, missing):
    cases = [(module, "test_pass", None) for module in REQUIRED_MODULES
             if module != missing and missing != "all"]
    # A similarly named module must not satisfy the exact module requirement.
    cases.extend((module + "_other", "test_pass", None) for module in REQUIRED_MODULES)
    with pytest.raises(ValueError, match="modules absent"):
        check_report(report(cases))


@pytest.mark.parametrize("module", REQUIRED_MODULES)
def test_duplicate_case_rejected(report, module):
    cases = [(name, "test_pass", None) for name in REQUIRED_MODULES]
    cases.append((module, "test_pass", None))
    with pytest.raises(ValueError, match="duplicate namespace test identity"):
        check_report(report(cases))


def test_unnamed_case_rejected(report):
    with pytest.raises(ValueError, match="Missing or duplicate"):
        check_report(report([(REQUIRED_MODULES[0], "", None)]))


def test_unrelated_skip_does_not_change_namespace_gate(report):
    cases = [(module, "test_pass", None) for module in REQUIRED_MODULES]
    cases.append(("tests.test_other_platform", "test_optional", "skipped"))
    assert check_report(report(cases)) == dict.fromkeys(REQUIRED_MODULES, 1)


def test_unknown_report_root_rejected(report):
    with pytest.raises(ValueError, match="Expected a JUnit"):
        check_report(report(root_tag="other"))


def test_cli_success_reports_count(report, capsys):
    assert main([str(report())]) == 0
    output = capsys.readouterr()
    assert f"gate passed: {len(REQUIRED_MODULES)} tests" in output.out
    assert output.err == ""


@pytest.mark.parametrize("kind", ["missing", "malformed", "skipped"])
def test_cli_failure_returns_nonzero(tmp_path, report, capsys, kind):
    path = tmp_path / "absent.xml"
    if kind == "malformed":
        path.write_text("<testsuites", encoding="utf-8")
    elif kind == "skipped":
        path = report([(module, "test_skip", "skipped") for module in REQUIRED_MODULES])
    assert main([str(path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "gate failed:" in output.err


def test_workflow_checks_actual_namespace_report_and_keeps_latest_full_suite():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
    ordinary, namespace = workflow.split("  browser-namespace:\n", 1)
    namespace = namespace.split("  package:\n", 1)[0]
    assert "runs-on: ubuntu-latest" in ordinary
    assert "run: pytest --cov=sds200 --cov-report=term-missing" in ordinary
    assert "runs-on: ubuntu-22.04" in namespace
    assert 'python-version: ["3.11", "3.12", "3.13", "3.14"]' in namespace
    assert "sudo apt-get install --yes --no-install-recommends bubblewrap" in namespace
    assert "assert os.geteuid() != 0; os.close(os.pidfd_open(os.getpid()))" in workflow
    assert ('bwrap --unshare-pid --as-pid-1 --die-with-parent --bind / / '
            '--dev-bind /dev /dev --proc /proc -- /bin/true') in workflow
    assert ('pytest tests/test_browser_device_launch.py '
            'tests/test_browser_device_guard_release.py') in namespace
    assert "tests/test_browser_device_continuation_intent.py" in namespace
    assert "tests/test_browser_device_continuation_history.py" in namespace
    assert '--junitxml="${RUNNER_TEMP}/namespace-test-results.xml"' in namespace
    assert ('python scripts/check_browser_namespace_results.py '
            '"${RUNNER_TEMP}/namespace-test-results.xml"') in namespace
