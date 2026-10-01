"""Fail-closed runtime admission also runs on Linux Python 3.11/3.12.

Unlike real timerfd lifetime tests, these tests do not need that optional stdlib
API. API absence is never hidden with a replacement timer or action permission.
"""

import os

import pytest

from . import test_supplemental_recording_service_deadline as watched

m = watched.m
prepared = watched.prepared


@pytest.mark.parametrize(
    "name",
    [
        "timerfd_create",
        "timerfd_settime_ns",
        "timerfd_gettime_ns",
        "TFD_CLOEXEC",
        "TFD_NONBLOCK",
        "TFD_TIMER_ABSTIME",
    ],
)
def test_missing_timer_api_refuses_before_acquisition_and_consumes_attempt(
    prepared, monkeypatch, name
):
    with watched.original_link(prepared, monkeypatch) as link:
        before = len(os.listdir("/proc/self/fd"))
        monkeypatch.delattr(os, name, raising=False)
        assert not m.timerfd_available()
        watched.denied(lambda: m.DeadlineWatch(link))
        assert link.deadline_capture_attempted and not link.closed and not link.failed
        assert len(os.listdir("/proc/self/fd")) == before
        assert not prepared.witness.exited()
        watched.denied(lambda: m.DeadlineWatch(link))


def test_existing_product_runtime_requirement_is_not_raised():
    import tomllib
    from pathlib import Path

    root = Path(m.__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as source:
        assert tomllib.load(source)["project"]["requires-python"] == ">=3.11"
