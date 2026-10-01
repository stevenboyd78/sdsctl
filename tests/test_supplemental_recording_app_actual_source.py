"""Actual recorder source tree inside the original App qualification pipeline.

This closes a source-fixture gap, not native execution or installed provenance.
Runtime/Engine/Ready/baseline facts are still the explicit synthetic App fixture.
The complete reviewed source copied here is never imported from observed roots.
"""

import os

import pytest

from . import test_supplemental_recording_app_begin as begins
from . import test_supplemental_recording_app_native_qualification as qualifiers
from . import test_supplemental_recording_guardian as guardian

candidate_module = qualifiers.candidates
joined, execution, launch_case, native, app = (
    begins.joined,
    begins.execution,
    begins.launch_case,
    begins.native,
    begins.app,
)
layout, image_umask, supervised = begins.layout, begins.image_umask, begins.supervised
image, configured, staged = begins.image, begins.configured, guardian.staged
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture
def candidate(supervised, image, configured, monkeypatch, request, staged):
    yield from candidate_module.setup_candidate(
        supervised, image, configured, monkeypatch, request, source_tree=staged.layout
    )


def test_full_executable_native_source_is_inventory_equivalent_before_app_begin(joined, staged):
    s = joined
    assert s.plan.candidate_runtime.source == staged.pin
    start = s.make_start()
    original = s.plan.raw, s.plan.lease, s.run.ready.ready_raw
    descriptors = len(os.listdir("/proc/self/fd"))
    assert start.start_once() is start.relay
    assert s.ledger.state.count == 2 and len(s.relays) == 1
    assert original == (s.plan.raw, s.plan.lease, s.run.ready.ready_raw)
    # The App qualification graph remains the real pipeline; only native
    # transport/actor facts are the explicitly labeled synthetic boundary.
    assert s.run.qualify.elapsed_seconds < 2
    assert len(os.listdir("/proc/self/fd")) == descriptors
    assert not tuple((s.case_root / "receipts").iterdir())


@pytest.mark.parametrize("part", ["product", "native", "directory_mode"])
def test_complete_source_drift_after_ready_refuses_before_authorization(joined, staged, part):
    s = joined
    assert s.plan.candidate_runtime.source == staged.pin
    start = s.make_start()
    package = s.root / candidate_module.m.plans.fixed.PACKAGE
    helpers = s.root / candidate_module.m.plans.host.candidate_static.NATIVE
    target = (
        package / "daemon_runtime.py"
        if part == "product"
        else helpers / "supplemental_recording_wire.py"
    )
    assert target.is_relative_to(s.root)
    original = target.read_bytes()
    if part == "directory_mode":
        # File-only fingerprints cannot detect directory permissions. The
        # source inventory's explicit directory policy must refuse this mode.
        helpers.chmod(0o775)
        files = candidate_module.m.plans.host.candidate_static.source.files
        assert files.inventory(helpers) == files.inventory(staged.layout.native)
    else:
        target.write_bytes(original + b"\n# Changed fixture bytes after original Ready\n")
    begins.denied(start.start_once)
    assert not s.relays and s.ledger.state.count == 1
    assert s.journal.machine.state.authorization_generation is None
    assert s.run.client.closed
    # The independently staged executable source is a different copy and stays
    # untouched; changed observations cannot be repinned to make begin succeed.
    staged_path = (
        staged.layout.runtime / "daemon_runtime.py"
        if part == "product"
        else staged.layout.native / "supplemental_recording_wire.py"
    )
    assert staged_path.read_bytes() == original
