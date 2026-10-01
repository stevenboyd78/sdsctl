"""Real original journals/pidfd; explicitly synthetic Relay and collected files."""

import time
from dataclasses import replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_retained_history as history

m, begins, continuity = history.m, history.begins, history.continuity
layout, tree, routing, projection, binding, directory, prepared, setup, joined, begun = (
    history.layout,
    history.tree,
    history.routing,
    history.projection,
    history.binding,
    history.directory,
    history.prepared,
    history.setup,
    history.joined,
    history.begun,
)


@pytest.fixture
def observing(begun, tree):
    s = begun
    relay = s.start.relay
    expected = replace(tree.expected, generation=s.run.pins.generation)
    s.ledger.started(expected, now=time.monotonic(), success_sha256="5" * 64)
    relay.phase, relay.expected = "completed", expected
    relay.plan = SimpleNamespace(stop_at=time.monotonic() + 10, finish_by=time.monotonic() + 15)
    s.file_calls = []

    def collect(stage):
        return m.binding.protected.Collected(
            m.binding.protected.Files(
                s.plan.candidate.contract.sha256, stage, "6" * 64, expected.generation
            ),
            artifact=object() if stage == "finalized" else None,
        )

    s.result = collect("active")

    def progress(path):
        s.file_calls.append(("progress", path))
        return s.result

    def finalized():
        s.file_calls.append(("finalized",))
        return s.result

    def close():
        acknowledgment = m.binding.protected.Acknowledgment(
            expected.case,
            expected.generation,
            s.plan.candidate.contract.sha256,
            expected.started_at,
            "7" * 64,
            "8" * 64,
        )
        s.ledger.completed(acknowledgment, now=time.monotonic())
        relay.phase = "closed"
        s.result = collect("finalized")

    relay.read_progress, relay.recheck_completed = progress, finalized
    s.mark_closed = close
    return s


@pytest.mark.parametrize("stage", ["active", "finalizing", "finalized"])
def test_original_phase_and_fixed_path_select_read_only_file_evidence(observing, stage):
    s = observing
    if stage == "finalized":
        s.mark_closed()
    elif stage == "finalizing":
        s.result = replace(s.result, files=replace(s.result.files, stage=stage))
    original = s.plan.raw, s.ledger.state, tuple(s.journal.entries)
    files = {
        path: path.read_bytes()
        for directory in (s.journal.path, s.ledger.directory)
        for path in directory.iterdir()
    }
    assert s.start.read_files() is s.result
    assert s.file_calls == (
        [("finalized",)]
        if stage == "finalized"
        else [("progress", s.plan.root / "recording-progress")]
    )
    assert original == (s.plan.raw, s.ledger.state, tuple(s.journal.entries))
    assert files == {path: path.read_bytes() for path in files}
    assert len(s.relays) == 1 and not s.prepared.witness.exited()


@pytest.mark.parametrize("fault", ["contract", "generation", "stage", "artifact", "type"])
def test_file_result_must_match_original_authorized_recording(observing, fault):
    s = observing
    if fault in ("contract", "generation", "stage"):
        changes = {
            "contract": {"contract_sha256": "f" * 64},
            "generation": {"generation": "f" * 64},
            "stage": {"stage": "retained"},
        }[fault]
        s.result = replace(s.result, files=replace(s.result.files, **changes))
    elif fault == "artifact":
        s.result = replace(s.result, artifact=object())
    else:
        s.result = object()
    begins.denied(s.start.read_files)
    assert s.start.failed and s.run.client.closed and not s.prepared.witness.exited()


@pytest.mark.parametrize("fault", ["late", "finish", "ledger", "plan", "phase", "cancelled"])
def test_changed_or_late_file_read_cannot_escape_the_original_history(
    observing, monkeypatch, fault
):
    s = observing
    relay = s.start.relay
    original = relay.read_progress

    def changed(path):
        result = original(path)
        if fault == "late":
            continuity.advance(monkeypatch, 3)
        elif fault == "finish":
            now = m.plans.clock.read().boottime_ns / m.plans.clock.NS
            s.journal.append(dict(kind="finish", now=now, boot_id=s.plan.boot))
        elif fault == "ledger":
            s.ledger.state = replace(s.ledger.state, sha256="f" * 64)
        elif fault == "plan":
            relay.plan = object()
        elif fault == "phase":
            relay.phase = "unconfirmed"
        else:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(relay, "read_progress", changed)
    if fault == "cancelled":
        with pytest.raises(KeyboardInterrupt):
            s.start.read_files()
    else:
        begins.denied(s.start.read_files)
    assert s.start.failed and s.run.client.closed and not s.prepared.witness.exited()


@pytest.mark.parametrize("stage", ["active", "finalizing", "finalized"])
def test_closed_phase_cannot_reuse_unfinalized_or_unverified_result(observing, stage):
    s = observing
    s.mark_closed()
    s.result = replace(s.result, files=replace(s.result.files, stage=stage), artifact=None)
    begins.denied(s.start.read_files)


def test_active_read_crossing_original_stop_during_final_history_refuses(observing, monkeypatch):
    s = observing
    s.start.relay.plan.stop_at = time.monotonic() + 0.5
    retained = s.start.retained_history
    calls = []

    def history_read():
        result = retained()
        calls.append(True)
        if len(calls) == 2:
            continuity.advance(monkeypatch, 1)
        return result

    monkeypatch.setattr(s.start, "retained_history", history_read)
    begins.denied(s.start.read_files)
    assert len(s.file_calls) == 1 and not s.prepared.witness.exited()


@pytest.mark.parametrize("bound", ["native", "owner"])
def test_read_cannot_outlive_original_publication_bound(observing, monkeypatch, bound):
    s = observing
    relay = s.start.relay
    s.result = replace(s.result, files=replace(s.result.files, stage="finalizing"))
    if bound == "owner":
        relay.plan.finish_by = time.monotonic() + 0.5
    else:
        # Keep both actual journals/native binding unchanged. Advance to just
        # before their originally established finish bound instead.
        relay.plan.finish_by = relay.native_binding.finish_by + 1
        delta = relay.native_binding.finish_by - time.monotonic() - 0.5
        continuity.advance(monkeypatch, delta)
    collect = relay.read_progress

    def late(path):
        result = collect(path)
        continuity.advance(monkeypatch, 1)
        return result

    monkeypatch.setattr(relay, "read_progress", late)
    begins.denied(s.start.read_files)
    assert len(s.file_calls) == 1 and not s.prepared.witness.exited()


def test_file_read_cannot_precede_begin_or_actual_start(joined):
    begins.denied(joined.start.read_files)
    assert not joined.relays and joined.ledger.state.count == 1


def test_sent_begin_without_started_return_has_no_file_authority(begun):
    begins.denied(begun.start.read_files)
    assert begun.ledger.state.expected is None and begun.ledger.state.count == 2


@pytest.mark.parametrize("name", ["files_lock", "lock"])
def test_nested_read_does_not_release_an_existing_lock(observing, name):
    s = observing
    lock = getattr(s.start, name)
    assert lock.acquire(blocking=False)
    try:
        begins.denied(s.start.read_files)
        assert lock.locked()
    finally:
        lock.release()
    assert not s.file_calls and not s.prepared.witness.exited()


def test_owner_thread_is_required_before_any_file_read(observing):
    errors = []

    def other_thread():
        try:
            observing.start.read_files()
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=other_thread)
    worker.start()
    worker.join(2)
    assert not worker.is_alive() and len(errors) == 1
    assert isinstance(errors[0], m.UnconfirmedHostBegin)
    assert not observing.file_calls and not observing.prepared.witness.exited()
