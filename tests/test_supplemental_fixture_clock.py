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
