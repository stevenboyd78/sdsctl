"""Pure clock intervals and actual read-only kernel samples, never a suspend."""

import importlib.util
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

NAME = "supplemental_recording_clock"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(__file__).parents[1] / "scripts" / (NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
BOOT = "a" * 32
BASE = m.Window(BOOT, (4, 100), 10 * m.NS, 110 * m.NS, 10 * m.NS + 1_000_000)


def denied(action):
    with pytest.raises(m.UnconfirmedClock) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)


def test_native_deadline_subtracts_offset_instead_of_copying_host_seconds():
    native = BASE.native_deadline(120.0)
    assert native < 20 and native >= 20 - 1e-12
    assert 120.0 - native >= 100.0
    assert BASE.boottime_ns == 110 * m.NS
    assert BASE.offset == (100 * m.NS - 1_000_000, 100 * m.NS)


@pytest.mark.parametrize(
    "deadline", [True, None, "120", float("nan"), float("inf"), -1, 0, 110, 890.0001, 110.0001]
)
def test_invalid_or_ambiguous_host_deadline_refused(deadline):
    denied(lambda: BASE.native_deadline(deadline))


@pytest.mark.parametrize(
    "change",
    [
        {"boot": "PRIVATE"},
        {"boot": "a" * 31},
        {"namespace": [4, 100]},
        {"namespace": (True, 100)},
        {"namespace": (4, 0)},
        {"before_ns": True},
        {"before_ns": 0},
        {"boottime_ns": -1},
        {"after_ns": 10 * m.NS - 1},
        {"after_ns": 10 * m.NS + m.MAX_SKEW_NS + 1},
    ],
)
def test_invalid_window_is_not_a_domain_proof(change):
    denied(lambda: replace(BASE, **change))


def test_later_narrower_overlapping_interval_does_not_refresh_original_deadline():
    original = BASE.native_deadline(120.0)
    later = m.Window(BOOT, (4, 100), 11 * m.NS, 111 * m.NS - 100, 11 * m.NS + 100)
    BASE.check_later(later)
    assert BASE.native_deadline(120.0) == original


@pytest.mark.parametrize(
    "fault", ["boot", "namespace", "backwards", "suspend", "offset_reverse", "fake"]
)
def test_recheck_detects_suspend_and_domain_or_time_changes(fault):
    later = m.Window(BOOT, (4, 100), 11 * m.NS, 111 * m.NS, 11 * m.NS + 1_000_000)
    if fault == "boot":
        later = replace(later, boot="b" * 32)
    elif fault == "namespace":
        later = replace(later, namespace=(4, 101))
    elif fault == "backwards":
        later = BASE
    elif fault == "suspend":
        later = replace(later, boottime_ns=later.boottime_ns + 5 * m.NS)
    elif fault == "offset_reverse":
        later = replace(later, boottime_ns=later.boottime_ns - 5 * m.NS)
    else:
        later = {"boot": BOOT}
    denied(lambda: BASE.check_later(later))


@pytest.mark.parametrize(
    "fault", ["boot", "namespace", "wide", "late", "backwards", "last_backwards"]
)
def test_read_rejects_changed_or_delayed_kernel_observation(monkeypatch, fault):
    boots = iter([BOOT, "b" * 32 if fault == "boot" else BOOT])
    domains = iter([(4, 100), (4, 101) if fault == "namespace" else (4, 100)])
    numbers = [10 * m.NS, 110 * m.NS, 10 * m.NS + 100, 10 * m.NS + 200]
    if fault == "wide":
        numbers[2] += m.MAX_SKEW_NS
    elif fault == "late":
        numbers[3] += m.MAX_SKEW_NS
    elif fault == "backwards":
        numbers[2] = numbers[0] - 1
    elif fault == "last_backwards":
        numbers[3] = numbers[2] - 1
    clocks = iter(numbers)
    monkeypatch.setattr(m, "_boot", lambda: next(boots))
    monkeypatch.setattr(m, "_domain", lambda: next(domains))
    monkeypatch.setattr(m.time, "clock_gettime_ns", lambda _clock: next(clocks))
    denied(m.read)


def test_actual_kernel_read_and_original_deadline_binding():
    first = m.read()
    later = m.read()
    first.check_later(later)
    deadline = first.boottime_ns / m.NS + 5
    native = first.native_deadline(deadline)
    assert math.isfinite(native) and native > later.after_ns / m.NS
    assert native <= first.before_ns / m.NS + 5
