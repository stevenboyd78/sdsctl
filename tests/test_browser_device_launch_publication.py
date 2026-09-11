"""Deterministic publication interleaving: real private-directory locks, fake PID scope."""
from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from sds200 import browser_device_launch as launch
from sds200.browser_device_profile_access import BrowserProfileAccessError, browser_profile_access
from tests.test_browser_device_profile import private

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux advisory directory locks")


@pytest.mark.parametrize("published", [b"", b"ready"])
@pytest.mark.parametrize("outcome", ["complete", "corrupt", "stuck", "missing-ack",
                                     "ready-exit-failure", "ack-exit-failure"])
def test_supervisor_waits_for_readiness_publisher_to_release_ownership(
        tmp_path, monkeypatch, published, outcome):
    root = tmp_path / "handoff"
    root.mkdir(mode=0o700)
    publication = browser_profile_access(root, exclusive=True)
    writing = [False]
    reads, sleeps, closed, stopped = [], [], [], []
    clock = [100.0]
    observed_ack = [False]
    expected = object()
    real_matches = launch._matches

    def matches(path, body):
        if path.name == launch._LAUNCH:
            reads.append(writing[0])
            assert not writing[0], "Readiness was read before its publisher released ownership"
        real_matches(path, body)

    def write_supervisor(*args):
        publication.__enter__()
        writing[0] = True
        # A real exclusive publisher can expose the new filename before writing
        # any bytes, or before its post-write checks/release.
        private(root / launch._LAUNCH, published)

    def finish_publication(seconds):
        sleeps.append(seconds)
        clock[0] += 0.1
        if outcome == "stuck" or len(sleeps) > 1:
            clock[0] += launch._TOTAL_SECONDS + 1
        elif writing[0]:
            (root / launch._LAUNCH).write_bytes(b"corrupt" if outcome == "corrupt" else b"ready")
            publication.__exit__(None, None, None)
            writing[0] = False
            if outcome != "missing-ack":
                private(root / launch._ACK, b"ack")

    @contextmanager
    def checked_access(path, *, exclusive):
        with browser_profile_access(path, exclusive=exclusive) as identity:
            yield identity
            if (outcome == "ready-exit-failure" or
                    outcome == "ack-exit-failure" and observed_ack[0]):
                raise BrowserProfileAccessError()

    def acknowledge(*args):
        observed_ack[0] = True

    def activate(callback):
        callback()
        return expected

    messages = iter([{"event": "running"}, {"event": "stopped"}])
    scope = SimpleNamespace(process=[10, 20], namespace=[1, 2], owner_namespace=[1, 3], pidfd=42,
        preflight=lambda: None, send=lambda value: stopped.append(value) if value == b"s" else None,
        message=lambda _: next(messages),
        close=lambda: closed.append(True),
        child=SimpleNamespace(poll=lambda: None, wait=lambda **_: 0))
    handoff = SimpleNamespace(_root=root, _recovery=root,
        _checked=lambda **_: (b"record", b"original", b"proof"),
        _launch_body=lambda *a, **k: b"ready", _ack=acknowledge, activate=activate)
    monkeypatch.setenv("DISPLAY", ":fictional")
    monkeypatch.setattr(launch, "_executable", lambda _: (1, 2, 3, 4))
    monkeypatch.setattr(launch, "recovery_browser_command", lambda *a: ())
    monkeypatch.setattr(launch, "_Namespace", lambda *a: scope)
    monkeypatch.setattr(launch, "_write_file", write_supervisor)
    monkeypatch.setattr(launch, "_matches", matches)
    monkeypatch.setattr(launch, "browser_profile_access", checked_access)
    monkeypatch.setattr(launch.time, "sleep", finish_publication)
    monkeypatch.setattr(launch.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(launch.select, "select", lambda *a: ([42], [], []))
    try:
        if outcome == "complete":
            assert launch.run_browser_recovery(handoff, browser=root, bwrap=root) is expected
        else:
            with pytest.raises(launch.BrowserRecoveryLaunchError):
                launch.run_browser_recovery(handoff, browser=root, bwrap=root)
    finally:
        if writing[0]:
            publication.__exit__(None, None, None)
    assert reads == ([] if outcome == "stuck" else [False])
    assert sleeps == [0.1] * (1 if outcome in {"complete", "stuck"} else 2)
    assert closed == [True]
    assert stopped == ([b"s"] if outcome == "complete" else [])
    assert (root / launch._LAUNCH).read_bytes() == (
        published if outcome == "stuck" else b"corrupt" if outcome == "corrupt" else b"ready")
    assert observed_ack[0] is (outcome in {"complete", "ack-exit-failure"})
