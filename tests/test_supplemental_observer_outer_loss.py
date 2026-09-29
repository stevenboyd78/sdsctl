"""Full automatic passive peers under original-outer loss; never installed proof."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from . import test_supplemental_native_peer_image as image_tests
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
        phase=phase, peers_exited=True, native=12, files_unchanged=True
    )
