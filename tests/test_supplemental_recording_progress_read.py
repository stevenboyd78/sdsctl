"""Planned stage-selected progress checks; real wire/files, synthetic platform."""

import json
import math
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_relay as relay_tests

m = relay_tests.m
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    family,
    actors,
    calibration,
    ledger,
    files,
) = (
    relay_tests.layout,
    relay_tests.tree,
    relay_tests.routing,
    relay_tests.projection,
    relay_tests.binding,
    relay_tests.directory,
    relay_tests.prepared,
    relay_tests.family,
    relay_tests.actors,
    relay_tests.calibration,
    relay_tests.ledger,
    relay_tests.files,
)


@pytest.fixture
def active(prepared, actors, calibration, ledger, files, monkeypatch, tmp_path):
    # Establish fixture directories before pinning the Engine endpoint's parent
    # identity (including its link count). A later mkdir invalidates that pin.
    progress = tmp_path / "progress"
    progress.mkdir(mode=0o700)
    alternate = tmp_path / "alternate"
    alternate.mkdir(mode=0o700)
    with relay_tests.joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        _expected, _plan, value = relay_tests.startup(case, files)
        relay_tests.send(case, value)
        case.relay.started()
        files.wav.write_bytes(b"")
        files.wav.chmod(0o600)
        case.progress = progress
        case.alternate = alternate
        case.files = files
        yield case


def advance(patch, seconds):
    monotonic, monotonic_ns = time.monotonic, time.monotonic_ns
    patch.setattr(time, "monotonic", lambda: monotonic() + seconds)
    patch.setattr(time, "monotonic_ns", lambda: monotonic_ns() + int(seconds * 1e9))


def publish(case, collected):
    relay = case.relay
    collector = m.local.protected.Collector(relay.binding.projection.host)
    tip = m.local.checkpoints.append_progress(
        case.progress, collector, relay.expected, collected, previous_tip=relay.ledger.state.tip
    )
    relay.ledger.progress(case.progress, collector, tip, now=time.monotonic())
    return tip


@pytest.mark.parametrize("finalizing", [False, True])
def test_original_owner_schedule_selects_stage_without_writes_or_success(
    active, monkeypatch, finalizing
):
    case, relay = active, active.relay
    original = relay.ledger.state, relay.ready.client.attachment.reads, len(case.requests)
    with monkeypatch.context() as patch:
        if finalizing:
            advance(patch, relay.plan.stop_at + 0.01 - time.monotonic())
        result = relay.read_progress(case.progress)
    assert result.files.stage == ("finalizing" if finalizing else "active")
    assert result.artifact is None and result.progress.metadata is None
    assert list(case.progress.iterdir()) == []
    assert original == (relay.ledger.state, relay.ready.client.attachment.reads, len(case.requests))
    assert relay.phase == "completed" and not relay.ready.processes.closed


def test_only_returned_published_tips_supply_continuity(active):
    case, relay = active, active.relay
    first = relay.read_progress(case.progress)
    tip = publish(case, first)
    case.files.wav.write_bytes(b"a" * 32)
    second = relay.read_progress(case.progress)
    assert second.progress.wav.size_after == 32 and second.artifact is None
    assert relay.ledger.state.tip == tip
    tip = publish(case, second)
    assert relay.read_progress(case.progress).progress == second.progress
    assert relay.ledger.state.tip == tip and tip.count == 2
    assert len(list(case.progress.iterdir())) == 2


@pytest.mark.parametrize("fault", ["replace", "shrink"])
@pytest.mark.parametrize("finalizing", [False, True])
def test_unpublished_read_still_retains_in_memory_file_continuity(
    active, monkeypatch, fault, finalizing
):
    case, relay = active, active.relay
    # This test checks file continuity, not scheduling. Select a deterministic
    # phase in the SAME original plan instead of racing its short stop boundary
    # under CI coverage. Only the relay's phase clock is synthetic; real file,
    # process, collector and receipt checks remain. Separate exact-boundary and
    # existing real/advancing-clock tests below still require late-read refusal.
    observed_at = relay.plan.stop_at + (0.01 if finalizing else -0.01)
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: observed_at))
    case.files.wav.write_bytes(b"a" * 32)
    result = relay.read_progress(case.progress)
    assert result.files.stage == ("finalizing" if finalizing else "active")
    assert relay.ledger.state.tip is None
    if fault == "replace":
        # Preserve the old inode outside the watched recording root.
        case.files.wav.rename(case.progress.parent / "preserved.wav")
        case.files.wav.write_bytes(b"a" * 32)
        case.files.wav.chmod(0o600)
    else:
        case.files.wav.write_bytes(b"a" * 16)
    relay_tests.refused(case, lambda: relay.read_progress(case.progress))


@pytest.mark.parametrize("after", [False, True])
def test_crossing_original_exact_stop_boundary_cannot_return_active_progress(
    active, monkeypatch, after
):
    case, relay = active, active.relay
    original_plan, original_state = relay.plan, relay.ledger.state
    now = [original_plan.stop_at - 0.01]
    monkeypatch.setattr(m, "time", SimpleNamespace(monotonic=lambda: now[0]))
    collect = m.local.protected.Collector.active

    def crossing(*args, **kwargs):
        result = collect(*args, **kwargs)
        assert result.files.stage == "active"
        now[0] = math.nextafter(original_plan.stop_at, math.inf) if after else original_plan.stop_at
        return result

    monkeypatch.setattr(m.local.protected.Collector, "active", crossing)
    relay_tests.refused(case, lambda: relay.read_progress(case.progress))
    assert relay.plan is original_plan and relay.ledger.state == original_state
    assert relay._progress_context is None and list(case.progress.iterdir()) == []


@pytest.mark.parametrize("fault", ["extra", "already_pinned"])
def test_first_capture_cannot_adopt_existing_directory_or_tip(active, fault):
    case, relay = active, active.relay
    if fault == "extra":
        (case.progress / "PRIVATE").write_bytes(b"unacknowledged")
    else:
        collector = m.local.protected.Collector(relay.binding.projection.host)
        publish(case, collector.active(relay.expected))
    relay_tests.refused(case, lambda: relay.read_progress(case.progress))


@pytest.mark.parametrize("fault", ["plan", "expected", "started_record", "closed", "missing"])
def test_progress_requires_original_actual_started_anchor(active, fault):
    case, relay = active, active.relay
    if fault == "plan":
        relay.plan = replace(relay.plan, stop_at=relay.plan.stop_at + 0.1)
    elif fault == "expected":
        relay.expected = replace(relay.expected)
    elif fault == "started_record":
        path = relay.directory / "0002.json"
        value = json.loads(path.read_bytes())
        value["event"]["success_sha256"] = "f" * 64
        path.write_bytes(m.host.encode(value))
        relay.ledger.state = m.host.load(relay.directory, relay.binding)
    elif fault == "closed":
        relay.phase = "closed"
    else:
        del relay._started_context
    relay_tests.refused(case, lambda: relay.read_progress(case.progress))


@pytest.mark.parametrize("fault", ["extra", "replaced", "different", "rewritten"])
def test_progress_rechecks_original_directory_and_full_acknowledged_chain(active, fault):
    case, relay = active, active.relay
    first = relay.read_progress(case.progress)
    publish(case, first)
    relay.read_progress(case.progress)
    if fault == "extra":
        (case.progress / "0001.json").write_bytes(b"unacknowledged")
    elif fault == "replaced":
        case.progress.rename(case.progress.with_name("preserved"))
        case.alternate.rename(case.progress)
    elif fault == "different":
        case.progress = case.alternate
    else:
        path = case.progress / "0000.json"
        path.write_bytes(path.read_bytes() + b"\n")
    relay_tests.refused(case, lambda: relay.read_progress(case.progress))


@pytest.mark.parametrize("fault", ["late", "boundary", "ledger", "cancelled"])
def test_changed_or_late_read_cannot_return_progress(active, monkeypatch, fault):
    case, relay = active, active.relay
    collect = m.local.protected.Collector.active

    def changed(*args, **kwargs):
        result = collect(*args, **kwargs)
        if fault == "late":
            advance(monkeypatch, 3)
        elif fault == "boundary":
            advance(monkeypatch, relay.plan.stop_at + 0.01 - time.monotonic())
        elif fault == "ledger":
            publish(case, result)
        else:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(m.local.protected.Collector, "active", changed)
    if fault == "cancelled":
        with pytest.raises(KeyboardInterrupt):
            relay.read_progress(case.progress)
        assert relay.phase == "unconfirmed" and relay.ready.client.closed
    else:
        relay_tests.refused(case, lambda: relay.read_progress(case.progress))
    assert not relay.ready.processes.closed


def test_concurrent_progress_keeps_existing_lock_and_handles(active):
    case, relay = active, active.relay
    assert relay._lock.acquire(blocking=False)
    try:
        relay_tests.refused(case, lambda: relay.read_progress(case.progress))
        assert relay._lock.locked()
    finally:
        relay._lock.release()


def test_no_progress_authority_before_actual_started_return(
    prepared, actors, calibration, ledger, files, monkeypatch, tmp_path
):
    with relay_tests.joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        progress = tmp_path / "progress"
        progress.mkdir(mode=0o700)
        relay_tests.refused(case, lambda: case.relay.read_progress(progress))
        assert ledger.state.expected is None and case.ready.client.attachment.reads == 1


def test_original_publication_deadline_cannot_be_extended_for_progress(active, monkeypatch):
    case, relay = active, active.relay
    original = relay.plan, relay.native_binding, relay.guard.finish_by
    advance(monkeypatch, relay.plan.finish_by + 1 - time.monotonic())
    relay_tests.refused(case, lambda: relay.read_progress(case.progress))
    assert original == (relay.plan, relay.native_binding, relay.guard.finish_by)


@pytest.mark.parametrize("fault", ["plan", "started_record"])
def test_completion_also_requires_the_original_started_anchor(active, fault):
    case, relay = active, active.relay
    value = relay_tests.finish(case, case.files, relay.expected)
    if fault == "plan":
        relay.plan = replace(relay.plan)
    else:
        path = relay.directory / "0002.json"
        raw = json.loads(path.read_bytes())
        raw["event"]["success_sha256"] = "f" * 64
        path.write_bytes(m.host.encode(raw))
        relay.ledger.state = m.host.load(relay.directory, relay.binding)
    relay_tests.send(case, value)
    relay_tests.refused(case, relay.completed)
    assert not relay.ledger.state.closed and relay.ready.client.attachment.reads == 2


def test_completion_preserves_the_original_observed_and_acknowledged_progress(active):
    case, relay = active, active.relay
    sample = relay.read_progress(case.progress)
    tip = publish(case, sample)
    relay_tests.send(case, relay_tests.finish(case, case.files, relay.expected))
    result = relay.completed(progress_directory=case.progress)
    assert relay.ledger.state.closed and relay.ledger.state.tip == tip
    assert result.collected.artifact.samples == 800
    assert relay.recheck_completed() == result.collected


@pytest.mark.parametrize("fault", ["unpublished", "older", "different", "replaced", "omitted"])
def test_completion_cannot_forget_or_replace_observed_progress(active, fault):
    case, relay = active, active.relay
    sample = relay.read_progress(case.progress)
    if fault != "unpublished":
        publish(case, sample)
    if fault == "older":
        case.files.wav.write_bytes(b"a" * 32)
        relay.read_progress(case.progress)  # Newer observation has not been acknowledged.
    elif fault in ("different", "replaced"):
        copy = case.alternate / "0000.json"
        copy.write_bytes((case.progress / "0000.json").read_bytes())
        copy.chmod(0o600)
        if fault == "different":
            case.progress = case.alternate
        else:
            case.progress.rename(case.progress.with_name("preserved"))
            case.alternate.rename(case.progress)
    value = relay_tests.finish(case, case.files, relay.expected)
    relay_tests.send(case, value)
    directory = None if fault == "omitted" else case.progress
    relay_tests.refused(case, lambda: relay.completed(progress_directory=directory))
    assert not relay.ledger.state.closed and relay.ready.client.attachment.reads == 2
