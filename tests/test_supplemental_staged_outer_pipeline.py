"""Actual passive flow from separately staged source; no installed admission.

The 104-module peer profile stays unchanged. Seven actual outer-policy imports
are an explicit TEST candidate, while six legacy imports belong only to the
existing fixture bootstrap. This test never calls that union a production pin.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_host_source as source
from ._passive_outer_driver import failure_types

OUTER_ADDITIONS = frozenset(
    {
        "qualify_supplemental_recording_permission",
        "qualify_supplemental_recording_preflight",
        "qualify_supplemental_recording_service",
        "qualify_supplemental_recording_startup",
        "supplemental_recording_permission_review",
        "supplemental_recording_permission_sender",
        "supplemental_recording_service_command",
    }
)
FIXTURE_ONLY = frozenset(
    {
        "accept_supplemental_daemon",
        "accept_supplemental_recording_app_idle",
        "accept_supplemental_recording_idle",
        "supplemental_handoff_operator",
        "supplemental_handoff_service",
        "supplemental_recording_probe",
    }
)


def test_outer_candidate_closure_does_not_expand_the_admitted_peer_profile():
    peer = source.m.PreparationProfile
    candidate = SimpleNamespace(
        MODULES=peer.MODULES | OUTER_ADDITIONS,
        ROOTS=peer.ROOTS
        | {"qualify_supplemental_recording_service", "supplemental_recording_permission_sender"},
    )
    assert source.static_graph(candidate) == candidate.MODULES
    assert len(peer.MODULES) == 104 and len(candidate.MODULES) == 111
    assert not (FIXTURE_ONLY & candidate.MODULES)


@pytest.fixture
def staged(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    root = tmp_path / "stage"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    names = source.m.PreparationProfile.MODULES | OUTER_ADDITIONS | FIXTURE_ONLY
    for name in names:
        shutil.copyfile(repo / "scripts" / (name + ".py"), scripts / (name + ".py"))
    shutil.copytree(
        repo / "src/sds200",
        root / "src/sds200",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    # Copy Python fixture scaffolding, not recordings, captured cases or other
    # evidence. Dynamic child fixture imports remain inside this new tree.
    tests = root / "tests"
    tests.mkdir()
    for original in (repo / "tests").glob("*.py"):
        shutil.copyfile(original, tests / original.name)
    (tests / "fixtures").mkdir()
    shutil.copyfile(
        repo / "tests/fixtures/native_direct_spawn.c", tests / "fixtures/native_direct_spawn.c"
    )
    (scripts / "native").mkdir()
    shutil.copyfile(
        repo / "scripts/native/supplemental_peer_watch.c",
        scripts / "native/supplemental_peer_watch.c",
    )
    for path in (root, *root.rglob("*")):
        assert not path.is_symlink()
        path.chmod(0o755 if path.is_dir() else 0o644)
    pins = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }
    manifest = tmp_path / "pins.json"
    manifest.write_text(json.dumps(pins))

    def run(mode, target, temporary):
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(tests / "_staged_passive_pipeline.py"),
                str(root),
                str(manifest),
                mode,
                str(target),
            ],
            env={
                "PATH": "/usr/bin:/bin",  # Local test compiler/toolchain only.
                "TMPDIR": temporary,
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            },
            capture_output=True,
            timeout=55,  # Entire fixture setup/reaping; never its original 2s work cutoff.
            check=False,
        )

    yield SimpleNamespace(root=root, repo=repo, run=run, names=names, pins=pins)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == pin for p, pin in pins.items())
    assert not list(root.rglob("__pycache__"))


@pytest.mark.parametrize("fault", ["outside", "changed"])
def test_direct_spec_loader_cannot_execute_outside_or_changed_staged_private_code(staged, fault):
    name = "supplemental_recording_permission_sender.py"
    target = (staged.repo if fault == "outside" else staged.root) / "scripts" / name
    original = target.read_bytes()
    if fault == "changed":
        target.write_bytes(b"raise AssertionError('PRIVATE_CODE_MUST_NOT_RUN')\n")
    try:
        result = staged.run("probe", target, str(staged.root.parent))
        assert result.returncode == 75 and not result.stderr
        assert json.loads(result.stdout) == dict(source_refused=True)
    finally:
        if fault == "changed":
            target.write_bytes(original)


@pytest.mark.skipif(
    not hasattr(os, "timerfd_create") or not shutil.which("cc"),
    reason="Linux timerfd API and local compiler required for original passive fixture",
)
def test_original_passive_pipeline_uses_staged_outer_policy_and_unchanged_peer_profile(staged):
    with tempfile.TemporaryDirectory(prefix="sds-staged-") as sockets:
        result = staged.run("pipeline", staged.repo, sockets)
    if result.returncode or result.stderr:
        # Bounded private fixture evidence, not unredacted pytest terminal text.
        # Retain the first failure for inspection rather than rerun until green.
        for name, raw in (("stdout", result.stdout), ("stderr", result.stderr)):
            path = staged.root.parent / ("failure-" + name)
            with path.open("xb") as stream:
                path.chmod(0o600)
                stream.write(raw[:65536])
        # Do not let assertion rewriting render CompletedProcess.stdout/args.
        raise AssertionError(
            dict(status=result.returncode, types=failure_types(result.stdout + result.stderr))
        )
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["staged_outer_result"] == 0
    observed = set(report["modules"])
    assert observed <= staged.names
    assert observed >= OUTER_ADDITIONS and observed >= FIXTURE_ONLY
    assert b"3 passed" in result.stdout  # dynamic/static/UBSan actual command exits.
