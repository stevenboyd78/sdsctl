"""Regression tests for scheduler-independent supplemental fixture time."""

import time
from types import SimpleNamespace

import pytest

from ._supplemental_fixture_clock import compressed_scheduler_time


def test_compressed_time_hides_external_pause_and_restores_sources(monkeypatch):
    wall = [100.0]
    stable = object()
    direct = object()
    witnessed = object()
    clock = SimpleNamespace(read=lambda: direct)
    witness = SimpleNamespace(_last=stable, read=lambda: witnessed)
    monkeypatch.setattr(time, "monotonic", lambda: wall[0])

    with compressed_scheduler_time(
        monkeypatch,
        clock_module=clock,
        witness=witness,
    ):
        began = time.monotonic()
        wall[0] += 3.0  # Synthetic shared-runner pause; not a production timeout change.
        ended = time.monotonic()
        assert began < ended < began + 0.001
        assert clock.read() is stable and witness.read() is stable

    assert time.monotonic() == 103.0
    assert clock.read() is direct and witness.read() is witnessed


def test_module_scoped_time_keeps_peer_process_clock_real(monkeypatch):
    wall = [200.0]
    policy = SimpleNamespace(time=time)
    peer = SimpleNamespace(time=time)
    monkeypatch.setattr(time, "monotonic", lambda: wall[0])

    with compressed_scheduler_time(monkeypatch, time_modules=(policy,)):
        began = policy.time.monotonic()
        wall[0] += 3.0
        ended = policy.time.monotonic()
        assert began < ended < began + 0.001
        assert time.monotonic() == peer.time.monotonic() == 203.0

    assert policy.time is time and policy.time.monotonic() == 203.0


@pytest.mark.parametrize(
    "clock,witness",
    [(SimpleNamespace(read=lambda: None), None), (None, object())],
)
def test_clock_and_witness_are_an_atomic_fixture_pair(monkeypatch, clock, witness):
    with (
        pytest.raises(AssertionError, match="supplied together"),
        compressed_scheduler_time(
            monkeypatch,
            clock_module=clock,
            witness=witness,
        ),
    ):
        pass
