"""Actual read-only fixture trees, root IDs mapped to the local test account."""

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_runtime as runtime

m = runtime.m
layout = runtime.layout
image_umask = runtime.image_umask


@pytest.fixture
def supervised(layout):
    zones = layout.root / m.ZONEINFO
    for name, raw in (("Etc/UTC", b"TZif-UTC"), ("America/Denver", b"TZif-Denver")):
        file = zones / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        file.chmod(0o644)
    (zones / "tzdata.zi").write_bytes(b"PRIVATE-FAKE-METADATA")
    (zones / "UTC").symlink_to("Etc/UTC")
    (layout.root / "etc/localtime").symlink_to("/usr/share/zoneinfo/Etc/UTC")
    return layout


def test_separate_full_profile_keeps_original_fingerprint(supervised):
    old = supervised.observe()
    result = supervised.observe_supervised("America/Denver")
    assert result.sha256 != old.sha256
    assert result.entry_count == old.entry_count + 8
    assert result.file_count == old.file_count + 3
    assert result.total_bytes > old.total_bytes
    assert supervised.verify_supervised(result.sha256, "America/Denver") == result
    assert supervised.verify(old.sha256) == old
    runtime.denied(lambda: supervised.verify_supervised(old.sha256, "America/Denver"))
    runtime.denied(lambda: supervised.verify(result.sha256))


def test_same_file_alias_still_binds_selected_name(supervised):
    assert (
        supervised.observe_supervised("UTC").sha256
        != supervised.observe_supervised("Etc/UTC").sha256
    )


@pytest.mark.parametrize("workers", [1, 2])
def test_supervised_profile_keeps_pin_during_unrelated_parent_activity(
    supervised, monkeypatch, workers
):
    supervised = replace(supervised, workers=workers)
    expected = supervised.observe_supervised("America/Denver")
    original, changed = m.os.read, []
    target = (supervised.root / m.ZONEINFO / "America/Denver").stat().st_ino

    def read(fd, size):
        value = original(fd, size)
        if value and os.fstat(fd).st_ino == target:
            sibling = supervised.root.parent / f"unrelated-{len(changed)}"
            sibling.mkdir()
            changed.append(sibling)
        return value

    monkeypatch.setattr(m.os, "read", read)
    assert supervised.verify_supervised(expected.sha256, "America/Denver") == expected
    assert len(changed) == 2
    assert (
        supervised.verify_supervised_during(
            expected.sha256,
            "America/Denver",
            lambda: "read-only-sample",
            deadline=time.monotonic() + 2,
        )
        == "read-only-sample"
    )
    assert len(changed) == 4


@pytest.mark.parametrize("path", ["America/Denver", "Etc/UTC", "tzdata.zi", "unused-zone"])
def test_selected_unselected_and_metadata_bytes_all_pinned(supervised, path):
    before = supervised.observe_supervised("America/Denver")
    old = supervised.observe()
    (supervised.root / m.ZONEINFO / path).write_bytes(b"PRIVATE_CHANGED")
    runtime.denied(lambda: supervised.verify_supervised(before.sha256, "America/Denver"))
    assert supervised.verify(old.sha256) == old


@pytest.mark.parametrize(
    "fault", ["missing", "directory", "escape", "cycle", "absolute_escape", "writable", "hardlink"]
)
def test_selected_zone_must_resolve_to_safe_inventoried_file(supervised, fault):
    path = supervised.root / m.ZONEINFO / "America/Denver"
    if fault == "writable":
        path.chmod(0o666)
    else:
        path.unlink()
        if fault == "directory":
            path.mkdir()
        elif fault == "escape":
            path.symlink_to("../../../../etc/passwd")
        elif fault == "cycle":
            path.symlink_to("Denver")
        elif fault == "absolute_escape":
            path.symlink_to("/usr/local/lib/python3.14/os.py")
        elif fault == "hardlink":
            path.hardlink_to(supervised.root / m.ZONEINFO / "Etc/UTC")
    runtime.denied(lambda: supervised.observe_supervised("America/Denver"))


@pytest.mark.parametrize("fault", ["missing", "file", "directory", "outside", "cycle"])
def test_localtime_profile_is_an_explicit_zoneinfo_link(supervised, fault):
    path = supervised.root / "etc/localtime"
    path.unlink()
    if fault == "file":
        path.write_bytes(b"TZif-PRIVATE")
    elif fault == "directory":
        path.mkdir()
    elif fault == "outside":
        path.symlink_to("/usr/local/lib/python3.14/os.py")
    elif fault == "cycle":
        path.symlink_to("localtime")
    runtime.denied(lambda: supervised.observe_supervised("America/Denver"))


@pytest.mark.parametrize("kind", ["file", "link", "directory"])
def test_etc_timezone_is_not_silently_admitted(supervised, kind):
    path = supervised.root / "etc/timezone"
    if kind == "file":
        path.write_bytes(b"UTC")
    elif kind == "link":
        path.symlink_to("missing")
    else:
        path.mkdir()
    runtime.denied(lambda: supervised.observe_supervised("America/Denver"))


@pytest.mark.parametrize(
    "zone", [None, "", "/etc/localtime", "A/../B", "PRIVATE_MISSING", "America"]
)
def test_bad_missing_or_directory_zone_is_not_a_silent_fallback(supervised, zone):
    runtime.denied(lambda: supervised.observe_supervised(zone))


@pytest.mark.parametrize("pin", [None, True, "x", "a" * 64])
def test_independent_expected_fingerprint_is_mandatory(supervised, pin):
    runtime.denied(lambda: supervised.verify_supervised(pin, "America/Denver"))


def test_changed_observation_refused_and_all_descriptors_closed(supervised, monkeypatch):
    original = m.Layout._snapshot
    calls = 0

    def changed(self, deadline, **kwargs):
        nonlocal calls
        result = original(self, deadline, **kwargs)
        calls += 1
        if calls == 1:
            (self.root / m.ZONEINFO / "tzdata.zi").write_bytes(b"PRIVATE_CHANGED")
        return result

    monkeypatch.setattr(m.Layout, "_snapshot", changed)
    before = len(os.listdir("/proc/self/fd"))
    runtime.denied(lambda: supervised.observe_supervised("America/Denver"))
    assert len(os.listdir("/proc/self/fd")) == before and calls == 2


def test_no_timezone_selection_can_bypass_the_runtime_tree(supervised):
    (supervised.root / "usr/local/lib/python3.14/sitecustomize.py").write_bytes(b"PRIVATE")
    runtime.denied(lambda: supervised.observe_supervised("America/Denver"))
    runtime.denied(lambda: replace(supervised, root=Path("relative")).observe_supervised("UTC"))


@pytest.mark.parametrize("workers", [1, 2])
def test_bracket_checks_every_runtime_file_before_and_after_callback(
    supervised, monkeypatch, workers
):
    supervised = replace(supervised, workers=workers)
    expected = supervised.observe_supervised("America/Denver")
    snapshot, trace = m.Layout._snapshot, []

    def read(self, deadline, **kwargs):
        trace.append("snapshot")
        return snapshot(self, deadline, **kwargs)

    def observe():
        trace.append("observe")
        return result

    result = object()
    monkeypatch.setattr(m.Layout, "_snapshot", read)
    assert (
        supervised.verify_supervised_during(
            expected.sha256, "America/Denver", observe, deadline=time.monotonic() + 2
        )
        is result
    )
    assert trace == ["snapshot", "observe", "snapshot"]


@pytest.mark.parametrize("stage", ["before", "during", "late", "exception"])
@pytest.mark.parametrize("path", ["usr/local/lib/python3.14/os.py", m.ZONEINFO + "/tzdata.zi"])
@pytest.mark.parametrize("workers", [1, 2])
def test_bracket_no_result_escapes_changed_runtime_or_failed_observation(
    supervised, monkeypatch, stage, path, workers
):
    supervised = replace(supervised, workers=workers)
    expected = supervised.observe_supervised("America/Denver")
    end, calls = time.monotonic() + 2, []
    file = supervised.root / path
    if stage == "before":
        file.write_bytes(b"PRIVATE_CHANGED")

    def observe():
        calls.append(True)
        if stage == "during":
            file.write_bytes(b"PRIVATE_CHANGED")
        elif stage == "late":
            monkeypatch.setattr(m.time, "monotonic", lambda: end)
        elif stage == "exception":
            raise OSError("PRIVATE observation error")
        return object()

    before = len(os.listdir("/proc/self/fd"))
    runtime.denied(
        lambda: supervised.verify_supervised_during(
            expected.sha256, "America/Denver", observe, deadline=end
        )
    )
    assert len(calls) == (stage != "before")
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("end", [None, True, float("nan"), float("inf"), -1])
def test_bracket_requires_original_bounded_absolute_deadline(supervised, end):
    expected, calls = supervised.observe_supervised("America/Denver"), []
    runtime.denied(
        lambda: supervised.verify_supervised_during(
            expected.sha256, "America/Denver", lambda: calls.append(1), deadline=end
        )
    )
    assert calls == []
