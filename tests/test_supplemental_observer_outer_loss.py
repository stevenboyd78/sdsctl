"""Full automatic passive peers under original-outer loss; never installed proof."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import _staged_passive_pipeline as staged_pipeline
from . import test_supplemental_native_peer_image as image_tests
from . import test_supplemental_observer_pipeline as pipeline
from ._passive_outer_driver import failure_types

binary = image_tests.binary
reviewed_binary = image_tests.reviewed_binary
direct_launcher = image_tests.direct_launcher


def test_outer_failure_diagnostic_cannot_echo_private_values_or_invented_type_names():
    private = b"PRIVATE_INPUT_SHOULD_NOT_APPEAR"
    raw = b"E   OSError: " + private + b"\nE   " + private + b"Error: value\n"
    raw += b"E   AssertionError: " + private + b"\n"
    assert failure_types(raw) == ["AssertionError", "OSError"]
    assert failure_types(b"x" * 65536 + raw) == []


def test_explicit_outer_failure_diagnostic_keeps_only_closed_traceback_type_names():
    raw = b"ValueError: PRIVATE_INPUT\nUnknownError: PRIVATE_INPUT\n"
    raw += b"source: RuntimeError: not an exception\n  OSError: not an exception\n"
    assert failure_types(raw) == ["ValueError"]


def test_staged_native_builds_also_keep_original_runner_prohibition(monkeypatch, tmp_path):
    def unexpected_runner(*args, **kwargs):
        pytest.fail("Actual runner must never be reached")

    def build(factory):
        with pytest.raises(AssertionError, match="must not invoke the pytest runner"):
            pytest.main([])
        raise ValueError("Stop before any fixture process is constructed")

    monkeypatch.setattr(pytest, "main", unexpected_runner)
    monkeypatch.setattr(pipeline, "direct_launcher", SimpleNamespace(__wrapped__=build))
    with pytest.raises(ValueError, match="Stop before"):
        staged_pipeline.run_pipeline(tmp_path / "stage")
    assert pytest.main is unexpected_runner


@pytest.mark.parametrize("failure", [None, "helper", "joined", "body", "report"])
@pytest.mark.parametrize("reviewed", [False, True])
def test_explicit_original_fixture_retains_order_and_cleanup_without_runner(
    monkeypatch, tmp_path, failure, reviewed
):
    events = []
    runner = pytest.main

    def plain(name):
        def call(*args):
            events.append(name)
            return name

        return SimpleNamespace(__wrapped__=call)

    def owned(name):
        def call(*args):
            events.append(name)
            if failure == name:
                raise RuntimeError("Synthetic setup refusal")
            try:
                yield name
            finally:
                events.append("close-" + name)
                if name == "helper" and failure == "report":
                    args[-1].node.add_report_section("teardown", "stderr", "PRIVATE_DIAGNOSTIC")

        return SimpleNamespace(__wrapped__=call)

    for name in ("layout", "supervised", "image", "configured", "reviewed_binary"):
        monkeypatch.setattr(pipeline, name, plain(name))
    for name in ("image_umask", "helper", "joined"):
        monkeypatch.setattr(pipeline, name, owned(name))

    def exercise():
        with pipeline.original_pipeline_fixture(
            tmp_path, binary=tmp_path / "candidate" if reviewed else None
        ) as (joined, patches, image):
            assert joined == "joined" and isinstance(patches, pytest.MonkeyPatch)
            assert image == ("reviewed_binary" if reviewed else None)
            assert pytest.main is not runner
            with pytest.raises(AssertionError, match="must not invoke the pytest runner"):
                pytest.main([])
            events.append("body")
            if failure == "body":
                raise RuntimeError("Synthetic body refusal")

    if failure is None:
        exercise()
    elif failure == "report":
        with pytest.raises(AssertionError) as refused:
            exercise()
        assert str(refused.value) == "Original fixture retirement did not complete"
    else:
        with pytest.raises(RuntimeError, match="Synthetic"):
            exercise()
    expected = ["image_umask", "layout", "supervised", "image", "configured"]
    if reviewed:
        expected.append("reviewed_binary")
    expected.append("helper")
    if failure != "helper":
        expected.append("joined")
        if failure != "joined":
            expected += ["body", "close-joined"]
        expected.append("close-helper")
    expected.append("close-image_umask")
    assert events == expected and pytest.main is runner


@pytest.fixture
def socket_temporary():
    # AF_UNIX paths must stay below the kernel's fixed path limit. This fresh
    # directory holds only this fixture's sockets, not historical case inputs.
    with tempfile.TemporaryDirectory(prefix="sds-outer-") as root:
        yield root


@pytest.mark.parametrize("phase", ["armed", "writer", "observer"])
@image_tests.pytestmark
def test_original_outer_loss_stops_actual_passive_pipeline_and_preserves_case(
    reviewed_binary, direct_launcher, phase, tmp_path, socket_temporary
):
    binary, digest = reviewed_binary
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).with_name("_passive_outer_driver.py")),
            str(repo),
            str(binary),
            digest,
            direct_launcher.fixture_library_path,
            phase,
            str(tmp_path),
            socket_temporary,
        ],
        env={},
        capture_output=True,
        text=True,
        timeout=40,  # Setup/reaping ceiling, never an extension of the original work cutoff.
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(
        phase=phase, peers_exited=True, native=12, files_unchanged=True, runner_used=False
    )
