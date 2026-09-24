"""Failure diagnostics disclose only bounded, known-checkout source locations."""

from types import SimpleNamespace

import pytest

from . import _supplemental_failure_diagnostics as diagnostics
from . import conftest


def captured(path, *, error=None):
    namespace = {"private": error or ValueError("PRIVATE-TOKEN-AND-PATH")}
    try:
        exec(compile("raise private\n", str(path), "exec"), namespace)
    except BaseException as caught:
        return caught
    raise AssertionError("Fixture did not raise")


def test_suppressed_chain_reports_only_safe_relative_locations():
    first = captured(diagnostics.SCRIPTS / "supplemental_recording_fixture.py")
    try:
        raise first
    except ValueError:
        try:
            raise RuntimeError("PRIVATE-WRAPPER") from None
        except RuntimeError as wrapped:
            result = diagnostics.failure_locations(wrapped)
    assert result == "cause 2: scripts/supplemental_recording_fixture.py:1"
    assert "PRIVATE" not in result and str(diagnostics.SCRIPTS) not in result


@pytest.mark.parametrize(
    "path",
    [
        "elsewhere/supplemental_recording_fixture.py",
        "scripts/ordinary.py",
        "scripts/supplemental_private-secret.py",
        "scripts/sub/supplemental_recording_fixture.py",
    ],
)
def test_unknown_path_or_name_is_omitted(path):
    assert diagnostics.failure_locations(captured(diagnostics.SCRIPTS.parent / path)) == ""


def test_exception_messages_and_locals_are_never_formatted():
    class PrivateError(ValueError):
        def __str__(self):
            pytest.fail("Private error was formatted")

        def __repr__(self):
            pytest.fail("Private error was represented")

    error = captured(diagnostics.SCRIPTS / "supplemental_handoff_fixture.py", error=PrivateError())
    assert diagnostics.failure_locations(error) == (
        "cause 1: scripts/supplemental_handoff_fixture.py:1"
    )


def test_cycle_ends_without_repeating_the_original_exception():
    first = captured(diagnostics.SCRIPTS / "supplemental_recording_fixture.py")
    second = RuntimeError()
    first.__context__, second.__context__ = second, first
    assert len(diagnostics.failure_locations(first).splitlines()) == 1


def test_long_chain_has_fixed_limit_and_follows_explicit_cause():
    errors = [
        captured(diagnostics.SCRIPTS / "supplemental_recording_fixture.py") for _ in range(20)
    ]
    for left, right in zip(errors, errors[1:], strict=False):
        left.__cause__ = right
        left.__context__ = ValueError("PRIVATE")
    rows = diagnostics.failure_locations(errors[0]).splitlines()
    assert len(rows) == 8 and rows[-1].startswith("cause 8:")


@pytest.mark.parametrize("error", [None, "PRIVATE", 10])
def test_nonexception_is_ignored(error):
    assert diagnostics.failure_locations(error) == ""


@pytest.mark.parametrize("fault", [None, "success", "no_error", "other_test", "other_script"])
def test_report_hook_is_restricted_to_failing_supplemental_tests(fault):
    error = captured(
        diagnostics.SCRIPTS
        / ("ordinary.py" if fault == "other_script" else "supplemental_recording_fixture.py")
    )
    report = SimpleNamespace(failed=fault != "success", sections=[])
    item = SimpleNamespace(
        path=diagnostics.SCRIPTS.parent
        / "tests"
        / ("test_other.py" if fault == "other_test" else "test_supplemental_fixture.py")
    )
    call = SimpleNamespace(excinfo=None if fault == "no_error" else SimpleNamespace(value=error))
    hook = conftest.pytest_runtest_makereport(item, call)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))
    assert report.sections == (
        [
            (
                "Supplemental refusal locations (no values)",
                "cause 1: scripts/supplemental_recording_fixture.py:1",
            )
        ]
        if fault is None
        else []
    )
