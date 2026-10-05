"""Fresh post-completion files; real wire/pidfds, synthetic operator metadata."""

import json
import time
from dataclasses import replace

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
def completed(prepared, actors, calibration, ledger, files, monkeypatch):
    with relay_tests.joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, _plan, value = relay_tests.startup(case, files)
        relay_tests.send(case, value)
        case.relay.started()
        relay_tests.send(case, relay_tests.finish(case, files, expected))
        case.result = case.relay.completed()
        case.files = files
        yield case


def test_original_completion_allows_fresh_reads_not_new_returns_or_exit(completed, monkeypatch):
    case = completed
    original = (
        case.relay.native_binding,
        case.relay.guard.finish_by,
        case.relay.ledger.state,
        len(case.requests),
        case.ready.client.attachment.reads,
    )
    reads = []
    finalized = m.local.protected.Collector.finalized

    def fresh(*args, **kwargs):
        reads.append(kwargs["stopped"])
        return finalized(*args, **kwargs)

    monkeypatch.setattr(m.local.protected.Collector, "finalized", fresh)
    for _ in range(2):
        result = case.relay.recheck_completed()
        assert result == case.result.collected and result is not case.result.collected
    assert len(reads) == 2 and reads[0] is not reads[1]
    assert original == (
        case.relay.native_binding,
        case.relay.guard.finish_by,
        case.relay.ledger.state,
        len(case.requests),
        case.ready.client.attachment.reads,
    )
    assert case.relay.phase == "closed" and not case.ready.processes.exited("init")
    assert not hasattr(result, "exited") and not hasattr(result, "restored")


@pytest.mark.parametrize("fault", ["wav_bytes", "wav_replaced", "sidecar", "old", "new"])
def test_later_file_changes_cannot_reuse_previous_success(completed, fault):
    case, files = completed, completed.files
    if fault == "wav_bytes":
        raw = bytearray(files.wav.read_bytes())
        raw[-1] ^= 1  # Same size, inode, valid PCM/WAV; different original artifact.
        files.wav.write_bytes(raw)
    elif fault == "wav_replaced":
        raw = files.wav.read_bytes()
        replacement = files.root / "test-replacement.wav"
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        replacement.replace(files.wav)  # Test-only changed inode with exact original bytes.
    elif fault == "sidecar":
        value = json.loads(files.sidecar.read_bytes())
        value["source"]["scanner"] = "PRIVATE_OTHER"
        files.sidecar.write_bytes(m.host.encode(value))
    elif fault == "old":
        (files.root / "older/old.wav").write_bytes(b"PRIVATE changed old recording")
    else:
        (files.root / "PRIVATE_new.wav").write_bytes(b"unexpected")
    relay_tests.refused(case, case.relay.recheck_completed)
    assert case.relay.ledger.state.closed
    assert case.result.collected.artifact.samples == 800  # Past result is not current proof.
    assert case.ready.client.attachment.reads == 3
    assert not case.ready.processes.closed


@pytest.mark.parametrize(
    "fault", ["copy", "stopped", "artifact", "expected", "plan", "ledger", "phase", "context"]
)
def test_only_original_relay_completion_and_ledger_may_supply_recheck(completed, fault):
    case, relay = completed, completed.relay
    if fault == "copy":
        relay.completion = replace(case.result)
    elif fault == "stopped":
        relay.completion = replace(case.result, stopped_raw=b"{}")
    elif fault == "artifact":
        relay.completion = replace(case.result, collected=None)
    elif fault == "expected":
        relay.expected = replace(relay.expected, generation="f" * 64)
    elif fault == "plan":
        relay.plan = replace(relay.plan, finish_by=relay.plan.finish_by + 1)
    elif fault == "ledger":
        relay.ledger.state = replace(relay.ledger.state, sha256="f" * 64)
    elif fault == "phase":
        relay.phase = "unconfirmed"
    else:
        del relay._completion_context
    relay_tests.refused(case, relay.recheck_completed)


def test_unchanged_files_cannot_substitute_for_missing_actual_completed_return(
    prepared, actors, calibration, ledger, files, monkeypatch
):
    with relay_tests.joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, _plan, value = relay_tests.startup(case, files)
        relay_tests.send(case, value)
        case.relay.started()
        relay_tests.finish(case, files, expected)  # Deliberately do not send/receive completion.
        relay_tests.refused(case, case.relay.recheck_completed)
        assert ledger.state.acknowledgment is None and case.ready.client.attachment.reads == 2


@pytest.mark.parametrize("when", ["after_publication", "after_attachment"])
def test_fresh_reads_use_original_retained_tail_not_a_renewed_publication_window(
    completed, monkeypatch, when
):
    case = completed
    relay = case.relay
    original = relay.native_binding, relay.plan, relay.guard.finish_by
    monotonic, monotonic_ns = time.monotonic, time.monotonic_ns
    bound = relay.plan.finish_by if when == "after_publication" else relay.guard.finish_by
    seconds = bound + 1 - monotonic()
    with monkeypatch.context() as patch:
        patch.setattr(time, "monotonic", lambda: monotonic() + seconds)
        patch.setattr(time, "monotonic_ns", lambda: monotonic_ns() + int(seconds * 1e9))
        if when == "after_publication":
            assert time.monotonic() < relay.guard.finish_by
            assert relay.recheck_completed() == case.result.collected
        else:
            relay_tests.refused(case, relay.recheck_completed)
    assert original == (relay.native_binding, relay.plan, relay.guard.finish_by)


def test_concurrent_read_refuses_without_releasing_the_other_read_lock(completed):
    case = completed
    assert case.relay._lock.acquire(blocking=False)
    try:
        relay_tests.refused(case, case.relay.recheck_completed)
        assert case.relay._lock.locked()
    finally:
        case.relay._lock.release()
    assert not case.ready.processes.closed


@pytest.mark.parametrize("fault", ["late", "failed", "cancelled"])
def test_post_completion_read_has_original_bounds_and_retains_handles(
    completed, monkeypatch, fault
):
    case = completed
    finalized = m.local.protected.Collector.finalized
    monotonic = time.monotonic

    def collect(*args, **kwargs):
        result = finalized(*args, **kwargs)
        if fault == "late":
            monkeypatch.setattr(time, "monotonic", lambda: monotonic() + 3)
        elif fault == "failed":
            raise OSError("PRIVATE read failed")
        else:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(m.local.protected.Collector, "finalized", collect)
    if fault == "cancelled":
        with pytest.raises(KeyboardInterrupt):
            case.relay.recheck_completed()
        assert case.relay.phase == "unconfirmed" and case.ready.client.closed
    else:
        relay_tests.refused(case, case.relay.recheck_completed)
    assert case.relay.ledger.state.closed and not case.ready.processes.closed
    assert case.ready.client.attachment.reads == 3


@pytest.mark.parametrize("fault", [None, "changed", "unpinned_tail", "replaced"])
def test_original_progress_directory_and_exact_tip_rechecked(
    prepared, actors, calibration, ledger, files, monkeypatch, fault
):
    progress = ledger.directory.with_name("PRIVATE_progress")
    progress.mkdir(mode=0o700)
    alternate = ledger.directory.with_name("PRIVATE_alternate")
    alternate.mkdir(mode=0o700)  # Create before original Engine path ancestry is pinned.
    with relay_tests.joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, _plan, value = relay_tests.startup(case, files)
        relay_tests.send(case, value)
        case.relay.started()
        files.wav.write_bytes(relay_tests.evidence.wav_bytes(samples=160))
        files.wav.chmod(0o600)
        collector = m.local.protected.Collector(ledger.binding.projection.host)
        captured = collector.active(expected)
        tip = m.local.checkpoints.append_progress(
            progress, collector, expected, captured, previous_tip=None
        )
        ledger.progress(progress, collector, tip, now=time.monotonic())
        relay_tests.send(case, relay_tests.finish(case, files, expected))
        result = case.relay.completed(progress_directory=progress)
        if fault == "changed":
            path = progress / "0000.json"
            path.write_bytes(path.read_bytes() + b"\n")
        elif fault == "unpinned_tail":
            m.local.checkpoints.append_progress(
                progress,
                collector,
                expected,
                collector.active(expected, finalizing=True, previous=result.collected.progress),
                previous_tip=tip,
            )
        elif fault == "replaced":
            (alternate / "0000.json").write_bytes((progress / "0000.json").read_bytes())
            (alternate / "0000.json").chmod(0o600)
            saved = progress.with_name("PRIVATE_retained_progress")
            progress.rename(saved)
            alternate.rename(progress)  # Same parent link count, exact original bytes; new inode.
        if fault is None:
            assert case.relay.recheck_completed() == result.collected
        else:
            relay_tests.refused(case, case.relay.recheck_completed)
        assert ledger.state.closed and ledger.state.tip == tip
