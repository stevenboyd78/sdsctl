"""Failure diagnostics disclose only bounded, known-checkout source locations."""

import json
from contextlib import contextmanager
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


def test_native_service_refusal_observer_keeps_original_failure_and_only_locations(
    tmp_path, monkeypatch, capsys
):
    from . import test_supplemental_recording_app_actual_service as service

    # Isolate just the fixture's failure observer: no processes or native state.
    def original_case(*args):
        yield None

    monkeypatch.setattr(service.driver, "driver_case", SimpleNamespace(__wrapped__=original_case))
    from supplemental_recording_app_begin import AppStart, begin

    original = AppStart._fail
    error = captured(diagnostics.SCRIPTS / "supplemental_recording_fixture.py")
    owner = SimpleNamespace(original_run=None, failed=False)
    with (
        monkeypatch.context() as patch,
        contextmanager(service.service_case.__wrapped__)(None, tmp_path, patch, None),
    ):
        for _ in range(6):
            with pytest.raises(begin.UnconfirmedHostBegin, match=begin.MESSAGE):
                AppStart._fail(owner, error)
    assert AppStart._fail is original and owner.failed
    assert capsys.readouterr().out == (
        "Original native recording refusal locations (no values):\n"
        "cause 1: scripts/supplemental_recording_fixture.py:1\n"
    )


def test_child_stderr_only_extracts_known_frames_and_location_notes():
    raw = (
        f'  File "{diagnostics.SCRIPTS}/supplemental_recording_fixture.py", line 12, in private\n'
        "    require(PRIVATE_VALUE)\n"
        "ValueError: PRIVATE-PAYLOAD\n"
        '  File "/private/supplemental_recording_fixture.py", line 99, in private\n'
        "cause 1: scripts/supplemental_recording_fixture.py:12\n"
        "cause 2: scripts/accept_supplemental_fixture.py:13\n"
        "cause 2: scripts/ordinary.py:14\n"
    ).encode()
    assert diagnostics.child_failure_locations(raw) == (
        "child: scripts/supplemental_recording_fixture.py:12\n"
        "child: scripts/accept_supplemental_fixture.py:13"
    )


def test_native_assembly_observer_rethrows_original_without_private_values(
    monkeypatch, tmp_path, capsys
):
    from . import test_supplemental_recording_assembly as assembly

    class PrivateError(BaseException):
        def __str__(self):
            pytest.fail("Private assembly exception was formatted")

        def __repr__(self):
            pytest.fail("Private assembly exception was represented")

    error = captured(
        diagnostics.SCRIPTS / "supplemental_recording_fixture.py", error=PrivateError()
    )
    calls = []

    @contextmanager
    def bundle(*_args):
        yield None

    def run(owner, cancel):
        calls.append((owner, cancel))
        raise error

    owner, cancel = object(), object()
    with monkeypatch.context() as patch:
        patch.setattr(assembly, "native_bundle", bundle)
        patch.setattr(assembly.n.FiniteRecordingSchedule, "run", run)
        with contextmanager(assembly.rig.__wrapped__)(None, tmp_path, patch):
            for _ in range(6):
                with pytest.raises(PrivateError) as caught:
                    assembly.n.FiniteRecordingSchedule.run(owner, cancel)
                assert caught.value is error
    assert calls == [(owner, cancel)] * 6
    assert capsys.readouterr().out == (
        "Native assembly schedule refusal locations (no values):\n"
        "cause 1: scripts/supplemental_recording_fixture.py:1\n"
    )


def test_child_stderr_limits_input_output_and_does_not_format_unknown_values():
    raw = b"\n".join(
        f"cause 1: scripts/supplemental_recording_fixture.py:{index}".encode()
        for index in range(1, 80)
    )
    assert len(diagnostics.child_failure_locations(raw).splitlines()) == 32
    assert diagnostics.child_failure_locations(b"X" * 65536 + b"\n" + raw) == ""
    assert diagnostics.child_failure_locations(object()) == ""


def test_unread_child_result_only_reports_refusal_location_notes():
    note = "cause 1: scripts/supplemental_recording_fixture.py:12"
    records = [
        {"plan": "PRIVATE_PLAN", "refusal_locations": [note]},
        {"error": None, "refusal_locations": [note]},
        {"error": "refused", "private": note, "refusal_locations": "PRIVATE"},
        {"error": "refused", "refusal_locations": [None, {}, "PRIVATE", note, note]},
    ]
    raw = b"\n".join(json.dumps(row).encode() for row in records) + b"\n"
    assert diagnostics.child_result_failure_locations(raw) == (
        "child: scripts/supplemental_recording_fixture.py:12"
    )
    assert diagnostics.child_result_failure_locations(raw.rstrip(b"\n")) == ""


@pytest.mark.parametrize("raw", [object(), b"\xff\n", b"{}\n", b"[]\n", b"[" * 2000 + b"\n"])
def test_unread_child_result_ignores_malformed_or_unknown_data(raw):
    assert diagnostics.child_result_failure_locations(raw) == ""


def test_unread_child_result_has_bounded_input_notes_and_output():
    notes = [f"cause 1: scripts/supplemental_recording_fixture.py:{n}" for n in range(1, 80)]

    def record(values):
        return json.dumps(dict(error="refused", refusal_locations=values)).encode() + b"\n"

    assert len(diagnostics.child_result_failure_locations(record(notes)).splitlines()) == 8
    raw = record(["\n".join(notes)])
    assert len(diagnostics.child_result_failure_locations(raw).splitlines()) == 32
    assert diagnostics.child_result_failure_locations(b"X" * 65536 + b"\n" + raw) == ""
