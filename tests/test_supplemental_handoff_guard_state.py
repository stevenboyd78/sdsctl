"""Candidate report binding with real pidfds, private synthetic reports, no signals."""

import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_cached as cached_tests

NAME = "supplemental_handoff_guard_state"
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
g, c = sys.modules[NAME], cached_tests.c
CASE = "a" * 12 + "4" + "a" * 3 + "8" + "a" * 15
SOURCE = "b" * 40


def write(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@pytest.fixture
def reports(tmp_path, monkeypatch):
    root = tmp_path / "guard"
    root.mkdir(mode=0o700)
    (root / "daemon-case").mkdir(mode=0o700)
    peer, parent = os.getpid(), os.getppid()
    assert parent > 1
    evidence = c.CachedEvidence("a" * 64, True, False, True, peer, c.process_ticks(peer))
    write(
        root / "guard-started.json",
        {
            "state": "guard_started",
            "pid": parent,
            "start_ticks": c.process_ticks(parent),
            "deadline_seconds": 684,
            "grace_seconds": 3,
            "restoration_verified": False,
        },
    )
    write(
        root / "daemon-case/ready.json",
        {
            "state": "waiting_for_operator",
            "kind": "explicit-demand-clock-favorites-v1",
            "source_revision": SOURCE,
            "pid": peer,
            "start_ticks": evidence.peer_start_ticks,
            "generation": "c" * 32,
            "reads_require_explicit_consumer_demand": True,
            "ready_timeout_seconds": 600,
            "window_seconds": 64,
            "max_read_attempts": 60,
        },
    )
    # Only the fixed case root is remapped; /proc and pidfd calls remain real.
    monkeypatch.setattr(
        g,
        "Path",
        lambda path: (
            root
            if path == "/data/sdsctl-supplemental-acceptance-" + CASE
            else pytest.fail("Unexpected path")
        ),
    )
    return root, evidence


def live(reports, **changes):
    return g.guardian_live(reports[1], **({"case": CASE, "source": SOURCE} | changes))


def test_reports_bind_actual_ipc_peer_and_live_parent_without_signals(reports):
    before = len(list(Path("/proc/self/fd").iterdir()))
    assert live(reports)
    assert len(list(Path("/proc/self/fd").iterdir())) == before


@pytest.mark.parametrize(
    "file,key,value",
    [
        ("guard-started.json", "pid", 1),
        ("guard-started.json", "start_ticks", "1"),
        ("guard-started.json", "deadline_seconds", 685),
        ("guard-started.json", "grace_seconds", 6),
        ("guard-started.json", "restoration_verified", True),
        ("daemon-case/ready.json", "pid", 1),
        ("daemon-case/ready.json", "start_ticks", "1"),
        ("daemon-case/ready.json", "source_revision", "d" * 40),
        ("daemon-case/ready.json", "generation", "bad"),
        ("daemon-case/ready.json", "reads_require_explicit_consumer_demand", False),
        ("daemon-case/ready.json", "window_seconds", 75),
        ("daemon-case/ready.json", "max_read_attempts", 150),
        ("daemon-case/ready.json", "ready_timeout_seconds", True),
    ],
)
def test_forged_stale_or_wrong_case_reports_refused(reports, file, key, value):
    path = reports[0] / file
    data = json.loads(path.read_bytes())
    write(path, data | {key: value})
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(c.UnconfirmedCache):
        live(reports)
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_peer_must_match_reported_native_child(reports):
    with pytest.raises(c.UnconfirmedCache):
        live((reports[0], replace(reports[1], peer_pid=os.getppid())))


def test_wrong_parent_refused(reports, monkeypatch):
    monkeypatch.setattr(g, "parent_pid", lambda _: 99)
    with pytest.raises(c.UnconfirmedCache):
        live(reports)


def test_ended_guard_is_confirmed_unhealthy_not_restored(reports):
    write(reports[0] / "guard-result.json", {"state": "ended", "child_exit_confirmed": True})
    assert live(reports) is False


def test_missing_guard_evidence_is_not_an_ended_guard(reports):
    (reports[0] / "guard-started.json").unlink()
    with pytest.raises(c.UnconfirmedCache):
        live(reports)


def test_guard_ends_during_collection(reports, monkeypatch):
    original = g.read_report
    calls = 0

    def read(path):
        nonlocal calls
        result = original(path)
        calls += 1
        if calls == 3:
            write(
                reports[0] / "guard-result.json", {"state": "ended", "child_exit_confirmed": True}
            )
        return result

    monkeypatch.setattr(g, "read_report", read)
    assert live(reports) is False


def test_exited_pidfd_is_not_healthy(reports, monkeypatch):
    class Poll:
        def register(self, *args):
            pass

        def poll(self, *_):
            return [(5, 1)]

    monkeypatch.setattr(g.select, "poll", Poll)
    with pytest.raises(c.UnconfirmedCache):
        live(reports)


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "nonfinite",
        "list",
        "partial",
        "oversize",
        "symlink",
        "hardlink",
        "fifo",
        "mode",
    ],
)
def test_private_reports_refuse_unsafe_inputs(tmp_path, fault):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    path = root / "report.json"
    write(path, {"ok": True})
    if fault in ("duplicate", "nonfinite", "list", "partial", "oversize"):
        path.write_bytes(
            {
                "duplicate": b'{"x":1,"x":2}',
                "nonfinite": b'{"x":NaN}',
                "list": b"[]",
                "partial": b"{",
                "oversize": b"x" * 4097,
            }[fault]
        )
    elif fault == "symlink":
        path.rename(root / "old")
        path.symlink_to(root / "old")
    elif fault == "hardlink":
        (root / "alias").hardlink_to(path)
    elif fault == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    else:
        path.chmod(0o644)
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises((ValueError, OSError)):
        g.read_report(path)
    assert len(list(Path("/proc/self/fd").iterdir())) == before
