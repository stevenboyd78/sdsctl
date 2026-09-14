"""Pure tests for the isolated real-browser harness, not browser acceptance."""

import runpy
import sys
from pathlib import Path

import pytest


@pytest.fixture
def arguments(monkeypatch):
    # The standalone fixture imports adjacent test helpers. Do not retain its
    # temporary import path in the rest of the suite.
    monkeypatch.setattr(sys, "path", list(sys.path))
    script = (
        Path(__file__).resolve().parents[1] / "scripts/experimental/qualify_browser_first_start.py"
    )
    return runpy.run_path(str(script), run_name="qualification_test")["process_arguments"]


@pytest.mark.parametrize("separator", [b"\0", b" "])
def test_browser_argument_fixture_accepts_nul_and_rewritten_title(arguments, separator):
    expected = ["/usr/lib/chromium/chromium", "--user-data-dir=/tmp/fixture/profile", "--kiosk"]
    assert arguments(separator.join(s.encode() for s in expected) + b"\0") == expected
    assert "--user-data-dir=/tmp/fixture" not in arguments(
        separator.join(s.encode() for s in expected)
    )


@pytest.mark.parametrize("body", [b"", b"x" * 16385])
def test_browser_argument_fixture_bounds_input(arguments, body):
    with pytest.raises(ValueError):
        arguments(body)
