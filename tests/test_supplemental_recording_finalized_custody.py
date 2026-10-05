"""Actual wire/files/exit collector/Operator; synthetic Engine/platform facts.

No installed host-authority journal, full source/runtime sample or independent
recovery service is claimed by this lower-level completion/file composition.
"""

import os
import socket
import time
from dataclasses import replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_exit as exiting
from . import test_supplemental_recording_idle_continuity as clock_tests
from . import test_supplemental_recording_reconcile as custody

m, r = exiting.m, exiting.relay_tests
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
    plan,
) = (
    custody.layout,
    custody.tree,
    custody.routing,
    custody.projection,
    custody.binding,
    custody.directory,
    custody.prepared,
    custody.family,
    custody.actors,
    custody.calibration,
    custody.plan,
)
files = r.files


@pytest.fixture(params=[False, True], ids=["no-progress", "progress"])
def completed(prepared, actors, calibration, plan, files, family, monkeypatch, tmp_path, request):
    """Same original real Ready, Operator, Relay, ledger and file inventory."""
    assert m.reconcile is custody.m
    path = tmp_path / "recording-ledger"
    path.mkdir(mode=0o700)
    ledger = m.returned.host.Ledger(path, prepared.pins.host, now=time.monotonic())
    progress = tmp_path / "recording-progress" if request.param else None
    if progress is not None:
        progress.mkdir(mode=0o700)
    peers = []
    live = custody.joined.live(prepared, actors)
    terminal = dict(live, Running=False, ExitCode=0)
    responses = [custody.transport.reply(live)] * 3
    responses += [custody.transport.reply(terminal)] * 16
    with custody.joined.ready(
        prepared,
        actors,
        monkeypatch,
        consume=False,
        message=lambda: custody.ready_tests.envelope(prepared, actors),
        finish_extra=prepared.pins.host.projection.native.contract.maximum_recording_seconds + 3,
        responses=responses,
        tap=peers,
    ) as (client, requests):
        ready = custody.ready_tests.capture(client, calibration)
        assert ready.clock == plan.original_clock
        ready.clock = plan.original_clock
        endpoint = m.engine.Endpoint()
        operator = None
        try:
            operator = m.reconcile.Operator(plan, ready, endpoint)
            r.begin.intent(ledger, prepared)
            relay = m.returned.Relay(ledger, ready)
            custody.transport.attached.begun(peers[0])
            case = SimpleNamespace(
                relay=relay,
                ready=ready,
                peer=peers[0],
                requests=requests,
                operator=operator,
                ledger=ledger,
                files=files,
                plan=plan,
                progress=progress,
            )
            expected, _, start = r.startup(case, files)
            r.send(case, start)
            relay.started()
            if progress is not None:
                files.wav.write_bytes(r.evidence.wav_bytes(samples=160))
                files.wav.chmod(0o600)
                collector = m.returned.local.protected.Collector(ledger.binding.projection.host)
                captured = relay.read_progress(progress)
                tip = m.returned.local.checkpoints.append_progress(
                    progress, collector, expected, captured, previous_tip=None
                )
                ledger.progress(progress, collector, tip, now=time.monotonic())
            r.send(case, r.finish(case, files, expected))
            relay.completed(progress_directory=progress)
            case.reader = m.Finalized(relay, operator)
            yield case
        finally:
            if operator is not None:
                operator.close()
            endpoint.close()
            ready.close()


def exit_and_poll(case, family):
    exiting.delivery(case, family)
    result = case.reader.collect_exit()
    case.ready.close()
    original = case.operator.poll()
    assert original.returncode == 0
    return result, original


def refused(reader, method="read"):
    with pytest.raises(m.UnconfirmedExit) as caught:
        getattr(reader, method)()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert reader.failed
    with pytest.raises(m.UnconfirmedExit):
        reader.read()


def test_actual_completion_and_worker_exit_survive_closed_ready_without_new_authority(
    completed, family, prepared, monkeypatch
):
    case = completed
    exited, original = exit_and_poll(case, family)
    reader = case.reader
    handles = dict(case.operator.handles)
    before = {
        p: p.read_bytes()
        for root in (case.ledger.directory, prepared.directory)
        for p in root.iterdir()
    }

    def forbidden(*_, **__):
        pytest.fail("Finalized reads must not collect exits, receive, fsync, publish or dispatch")

    monkeypatch.setattr(m, "collect", forbidden)
    monkeypatch.setattr(m.os, "fsync", forbidden)
    monkeypatch.setattr(m.reconcile.Operator, "poll", forbidden)
    monkeypatch.setattr(m.reconcile.Operator, "publish", forbidden)
    for _ in range(2):
        assert reader.read() == case.relay.completion.collected
    prepared.process.stdin.close()
    prepared.process.wait(timeout=3)
    assert reader.read() == case.relay.completion.collected
    assert original.init_exited is False and case.operator.result is original
    assert reader._exit_receipt[0] is exited
    assert before == {p: p.read_bytes() for p in before}
    assert case.operator.handles == handles and not case.operator.publish_attempted
    reader.close()
    assert case.operator.handles == handles and not case.operator.closed
    assert not hasattr(reader, "restored")
    refused(reader)


@pytest.mark.parametrize(
    "fault", ["wav", "wav_inode", "metadata", "old", "extra", "ledger", "intent"]
)
def test_changed_files_or_history_never_reuse_original_completion(completed, family, fault):
    case = completed
    exit_and_poll(case, family)
    if fault == "wav":
        raw = bytearray(case.files.wav.read_bytes())
        raw[-1] ^= 1
        case.files.wav.write_bytes(raw)
    elif fault == "wav_inode":
        raw = case.files.wav.read_bytes()
        previous = case.files.wav.stat().st_ino
        case.files.wav.rename(case.files.root.parent / "retained-original.wav")
        case.files.wav.write_bytes(raw)
        case.files.wav.chmod(0o600)
        assert case.files.wav.stat().st_ino != previous
    elif fault == "metadata":
        raw = case.files.sidecar.read_bytes().replace(b"PRIVATE_RADIO", b"PRIVATE_OTHER")
        case.files.sidecar.write_bytes(raw)
    elif fault == "old":
        (case.files.root / "old.json").write_bytes(b"changed")
    elif fault == "extra":
        (case.files.root / "unexpected.wav").write_bytes(b"")
    else:
        name = "0001.json" if fault == "intent" else "0000.json"
        (case.ledger.directory / name).write_bytes(b"{}")
    refused(case.reader)
    assert not case.operator.closed and case.ready.closed


@pytest.mark.parametrize(
    "fault",
    [
        "not_exited",
        "not_polled",
        "not_closed",
        "copied_completion",
        "mutated_completion",
        "schedule",
        "state",
        "stopped",
        "context",
        "clock",
        "phase",
        "closed",
        "failed",
    ],
)
def test_original_completion_and_lifecycle_cannot_be_replaced(completed, family, fault):
    case = completed
    reader = case.reader
    if fault != "not_exited":
        exiting.delivery(case, family)
        reader.collect_exit()
        if fault != "not_closed":
            case.ready.close()
        if fault != "not_polled":
            case.operator.poll()
    if fault == "copied_completion":
        case.relay.completion = replace(case.relay.completion)
    elif fault == "mutated_completion":
        object.__setattr__(reader.completion.collected.artifact, "samples", 1)
    elif fault == "schedule":
        object.__setattr__(reader.schedule, "finish_by", reader.schedule.finish_by + 60)
    elif fault == "state":
        case.ledger.state = replace(case.ledger.state, sha256="f" * 64)
    elif fault == "stopped":
        object.__setattr__(reader.completion, "stopped_raw", b"{}")
    elif fault == "context":
        case.relay._completion_context = tuple(list(case.relay._completion_context))
    elif fault == "clock":
        reader.operator.clock = replace(reader.clock)
    elif fault == "phase":
        case.relay.phase = "closed"
    elif fault == "closed":
        reader.close()
    elif fault == "failed":
        reader.failed = True
    refused(reader)


def test_existing_exit_collector_is_one_use_and_lost_return_cannot_be_recovered(
    completed, family, monkeypatch
):
    case = completed
    exiting.delivery(case, family)
    collect = m.collect
    calls = []

    def lost(relay):
        calls.append(True)
        collect(relay)
        raise OSError("PRIVATE lost return")

    monkeypatch.setattr(m, "collect", lost)
    refused(case.reader, "collect_exit")
    assert calls == [True] and case.reader._exit_receipt is None
    refused(case.reader, "collect_exit")
    assert calls == [True] and case.relay.phase == "exited"


def test_finalized_reader_does_not_relax_live_relay_guard(completed, family):
    case = completed
    exit_and_poll(case, family)
    with pytest.raises(m.returned.UnconfirmedRelay):
        case.relay.recheck_completed()
    refused(case.reader)


def test_missing_fourth_return_remains_unconfirmed(completed, family):
    case = completed
    exiting.end_family(family)
    case.peer.shutdown(socket.SHUT_WR)
    refused(case.reader, "collect_exit")
    assert case.reader._exit_receipt is None and case.ready.failed


@pytest.mark.parametrize(
    "fault",
    [
        "completion_value",
        "exit_value",
        "operator_value",
        "done",
        "failed",
        "wrong_execution",
        "copied_clock",
    ],
)
def test_capture_requires_actual_original_relay_and_pre_begin_operator(
    completed, monkeypatch, fault
):
    case = completed
    relay, operator = case.relay, case.operator
    if fault == "completion_value":
        relay = relay.completion
    elif fault == "exit_value":
        relay = m.Exited(1, 2, 3, "a" * 64, "b" * 64, "c" * 64, time.monotonic())
    elif fault == "operator_value":
        operator = object()
    elif fault in ("done", "failed"):
        monkeypatch.setattr(operator, fault, True)
    elif fault == "wrong_execution":
        monkeypatch.setattr(operator, "execution_id", "f" * 64)
    else:
        monkeypatch.setattr(case.ready, "clock", replace(case.ready.clock))
    handles, request_count = dict(case.operator.handles), len(case.requests)
    with pytest.raises(m.UnconfirmedExit):
        m.Finalized(relay, operator)
    assert case.operator.handles == handles and not case.operator.closed
    assert len(case.requests) == request_count
    assert not case.ready.client.closed and case.relay.phase == "closed"


@pytest.mark.parametrize("fault", ["changed", "extra", "directory"])
@pytest.mark.parametrize("completed", [True], indirect=True, ids=["progress"])
def test_original_progress_chain_remains_exact(completed, family, fault):
    case = completed
    assert case.progress is not None
    exit_and_poll(case, family)
    if fault == "changed":
        target = case.progress / "0000.json"
        target.write_bytes(target.read_bytes() + b"\n")
    elif fault == "extra":
        (case.progress / "0001.json").write_bytes(b"{}")
    else:
        original = case.progress.with_name("original-progress")
        case.progress.rename(original)
        case.progress.mkdir(mode=0o700)
        for source in original.iterdir():
            target = case.progress / source.name
            target.write_bytes(source.read_bytes())
            target.chmod(0o600)
    refused(case.reader)


@pytest.mark.parametrize(
    "fault", ["late", "phase", "receipt", "files", "cancelled", "closed", "used"]
)
def test_read_checks_entire_window_and_context_after_file_collection(
    completed, family, monkeypatch, fault
):
    case = completed
    exit_and_poll(case, family)
    original = m.returned.local.protected.Collector.finalized

    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if fault == "late":
            clock_tests.advance(monkeypatch, 3)
        elif fault == "phase":
            case.relay.phase = "closed"
        elif fault == "receipt":
            value, digest = case.reader._exit_receipt
            case.reader._exit_receipt = replace(value), digest
        elif fault == "files":
            result = replace(result, files=replace(result.files, evidence_sha256="f" * 64))
        elif fault == "cancelled":
            raise KeyboardInterrupt
        elif fault == "closed":
            case.reader.close()
        else:
            case.reader.used = False
        return result

    monkeypatch.setattr(m.returned.local.protected.Collector, "finalized", changed)
    if fault == "cancelled":
        with pytest.raises(KeyboardInterrupt):
            case.reader.read()
        assert case.reader.failed
    else:
        refused(case.reader)
    assert not case.operator.closed


@pytest.mark.parametrize(
    "fault", ["foreign_thread", "nested", "recovery_deadline", "handle", "exit_mutated", "plan"]
)
def test_no_new_owner_process_handle_or_recovery_window(completed, family, monkeypatch, fault):
    case = completed
    exited, _ = exit_and_poll(case, family)
    reader = case.reader
    if fault == "foreign_thread":
        errors = []

        def other():
            try:
                reader.read()
            except BaseException as error:
                errors.append(error)

        worker = Thread(target=other)
        worker.start()
        worker.join(2)
        assert not worker.is_alive() and len(errors) == 1
        assert isinstance(errors[0], m.UnconfirmedExit)
    elif fault == "nested":
        assert reader.lock.acquire(blocking=False)
        try:
            refused(reader)
            assert reader.lock.locked()
        finally:
            reader.lock.release()
    else:
        if fault == "recovery_deadline":
            clock_tests.advance(monkeypatch, 1600)
        elif fault == "handle":
            with open(os.devnull, "rb") as stream:
                os.dup2(stream.fileno(), case.operator.handles["native"])
        elif fault == "exit_mutated":
            object.__setattr__(exited, "completion_sha256", "f" * 64)
        else:
            object.__setattr__(
                reader.plan.deadlines, "recover_by", reader.plan.deadlines.recover_by + 100
            )
        refused(reader)
    assert reader.failed and not case.operator.closed
