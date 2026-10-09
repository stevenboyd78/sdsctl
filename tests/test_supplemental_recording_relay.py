"""Actual private wire/files/pidfds with synthetic operator/Engine metadata.

Native source call sites and SO_PASSCRED are independently exercised by operator
tests. These fault fixtures do not claim an installed Docker or scanner trial.
"""

import importlib.util
import json
import select
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_bridge as local_tests
from . import test_supplemental_recording_evidence as evidence
from . import test_supplemental_recording_retained as continuity
from ._supplemental_fixture_clock import compressed_scheduler_time

NAME = "supplemental_recording_relay"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(continuity.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
begin = continuity.begin
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
) = (
    continuity.layout,
    continuity.tree,
    continuity.routing,
    continuity.projection,
    continuity.binding,
    continuity.directory,
    continuity.prepared,
    continuity.family,
    continuity.actors,
    continuity.calibration,
    continuity.ledger,
)


@pytest.fixture
def files(tree, prepared, monkeypatch):
    # Only route the exact pre-existing host alias to this test's private files.
    # No fresh baseline and no actual /mnt/data or /media access.
    alias = prepared.pins.host.projection.host.baseline.root

    def routed(function):
        def run(root, *args, **kwargs):
            return function(tree.root if root == alias else root, *args, **kwargs)

        return run

    p = m.local.protected
    monkeypatch.setattr(p.evidence, "opened_root", routed(p.evidence.opened_root))
    monkeypatch.setattr(p.evidence, "inventory", routed(p.evidence.inventory))
    monkeypatch.setattr(p.monitor, "opened_root", routed(p.monitor.opened_root))
    return tree


@contextmanager
def joined(prepared, actors, calibration, ledger, monkeypatch):
    peers = []
    with begin.ready_tests.attached(prepared, actors, monkeypatch, tap=peers) as (client, requests):
        ready = begin.ready_tests.capture(client, calibration)
        try:
            begin.intent(ledger, prepared)
            relay = m.Relay(ledger, ready)
            sent = begin.ready_tests.joined.engine.attached.begun(peers[0])
            assert sent["body"]["intent_sha256"] == relay.intent_sha256
            yield SimpleNamespace(
                relay=relay,
                ready=ready,
                peer=peers[0],
                requests=requests,
                fixture_patch=monkeypatch,
            )
        finally:
            ready.close()


def fixture_call(case, action, *args, **kwargs):
    """Run a non-timing synthetic phase without measuring CI scheduling."""

    with compressed_scheduler_time(case.fixture_patch):
        return action(*args, **kwargs)


def fixture_started(case, tree):
    """Construct, deliver and accept one ordered synthetic start phase."""

    with compressed_scheduler_time(case.fixture_patch):
        expected, plan, value = startup(case, tree)
        send(case, value)
        assert case.relay.started() == expected
        return expected, plan, value


def fixture_start_message(case, tree):
    """Construct and deliver a start for a separately faulted receiver."""

    with compressed_scheduler_time(case.fixture_patch):
        expected, plan, value = startup(case, tree)
        send(case, value)
        return expected, plan, value


def message(case, phase, body):
    relay = case.relay
    raw = begin.channel.frame(relay.native_binding, phase, body).decode("ascii")
    value = deepcopy(relay.envelope)
    value["phase"] = phase
    value["body"] = {"received": dict(value["native"], raw=raw, received_at=time.monotonic())}
    return value


def send(case, value):
    framing = begin.ready_tests.joined.engine.attached.stream
    case.peer.sendall(framing.segment(framing.app(value)))


def startup(case, tree):
    binding = case.relay.native_binding
    now = time.monotonic()
    expected = replace(tree.expected, generation=binding.generation)
    plan = m.native.owner.Plan(
        expected.case,
        expected.generation,
        expected.audio_endpoint_sha256,
        now,
        now + 0.5,
        now + 0.6,
        now + 8,
    )
    return expected, plan, message(case, "started", begin.channel.started(binding, expected, plan))


def finish(case, tree, expected):
    # Actual PCM/WAV and sidecar content checked through original host inventory.
    body = begin.channel.completed(case.relay.native_binding, expected)
    stopped = body["stopped"]
    stopped["stopped_at"] = (
        datetime.fromisoformat(expected.started_at) + timedelta(seconds=1)
    ).isoformat()
    metadata = {
        "schema": "sds200.recording-metadata",
        "version": 1,
        "recording": {
            "file": tree.wav.name,
            "format": "wav",
            "sample_rate_hz": 8000,
            "channels": 1,
            "sample_width_bytes": 2,
        },
        "source": {"endpoint": "synthetic audio source", "scanner": "PRIVATE_RADIO"},
        "boundaries": {
            "started": {"at": expected.started_at, "state": {}},
            "stopped": {"at": stopped["stopped_at"], "state": {}},
        },
        "statistics": {
            key: deepcopy(stopped[key])
            for key in (
                "elapsed_seconds",
                "packets",
                "samples",
                "audio_duration_seconds",
                "reliability",
            )
        },
        "error": None,
    }
    tree.wav.write_bytes(evidence.wav_bytes(samples=stopped["samples"]))
    tree.wav.chmod(0o600)
    tree.sidecar.write_bytes(m.host.encode(metadata))
    tree.sidecar.chmod(0o600)
    artifact = m.local.protected.evidence.verify_finalized(
        tree.baseline,
        expected,
        generation=expected.generation,
        stopped=stopped,
    )
    body["artifact"] = asdict(artifact)
    time.sleep(max(0, case.relay.plan.stop_at - time.monotonic()) + 0.001)
    return message(case, "completed", body)


def refused(case, action):
    with pytest.raises(m.UnconfirmedRelay) as error:
        action()
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)
    assert case.relay.phase == "unconfirmed" and case.ready.failed
    assert case.ready.client.closed and not case.ready.processes.closed


@pytest.mark.parametrize("exit_before_completion", [False, True])
def test_actual_wire_returns_and_original_files_required_separately_from_exit(
    prepared, actors, calibration, ledger, files, family, monkeypatch, exit_before_completion
):
    assert m.local is local_tests.m
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, plan, _value = fixture_started(case, files)
        assert case.relay.plan == plan
        completed = finish(case, files, expected)
        send(case, completed)
        if exit_before_completion:
            family.process.stdin.close()
            family.process.wait(timeout=3)
        result = fixture_call(case, case.relay.completed)
        assert ledger.state.closed and ledger.state.acknowledgment == result.acknowledgment
        assert (
            result.collected.files.stage == "finalized" and result.collected.artifact.samples == 800
        )
        assert result.acknowledgment.completion_sha256 == result.native_return_sha256
        raw = json.loads(completed["body"]["received"]["raw"])
        assert result.stopped_raw == m.host.encode(raw["body"]["stopped"])
        assert case.relay.completion is result
        assert case.ready.client.attachment.reads == 3 and len(case.requests) == 5
        assert not case.ready.processes.exited("init")
        assert case.ready.processes.exited("native") is exit_before_completion
        assert not hasattr(result, "exited") and not hasattr(result, "restored")
        assert (files.root / "older/old.wav").read_bytes() == b"old evidence unchanged"
        refused(case, case.relay.completed)
        assert case.peer.recv(1) == b""


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "kind",
        "phase",
        "extra",
        "context",
        "guardian",
        "native",
        "watchdog",
        "body",
        "pid",
        "uid_bool",
        "ticks",
        "received_future",
        "raw_unicode",
        "raw_extra",
        "raw_duplicate",
        "raw_noncanonical",
        "raw_kind",
        "raw_binding",
        "raw_phase",
        "raw_past",
        "raw_future",
        "plan_past",
        "expected_generation",
    ],
)
def test_unmatched_or_untimely_start_return_consumes_without_a_started_entry(
    prepared, actors, calibration, ledger, files, monkeypatch, fault
):
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        _expected, _plan, value = startup(case, files)
        receipt = value["body"]["received"]
        if fault == "schema":
            value["schema"] = True
        elif fault == "kind":
            value["kind"] = "PRIVATE"
        elif fault == "phase":
            value["phase"] = "completed"
        elif fault == "extra":
            value["PRIVATE"] = None
        elif fault == "context":
            value["context"]["profile"] = "e" * 64
        elif fault in ("guardian", "native", "watchdog"):
            value[fault]["uid"] = False
        elif fault == "body":
            value["body"]["PRIVATE"] = None
        elif fault == "pid":
            receipt["pid"] += 1
        elif fault == "uid_bool":
            receipt["uid"] = False
        elif fault == "ticks":
            receipt["start_ticks"] += 1
        elif fault == "received_future":
            receipt["received_at"] += 60
        elif fault == "raw_unicode":
            receipt["raw"] = "PRIVATE\u2603"
        elif fault == "raw_duplicate":
            receipt["raw"] = '{"schema":1,' + receipt["raw"][1:]
        elif fault == "raw_noncanonical":
            receipt["raw"] += " "
        else:
            raw = json.loads(receipt["raw"])
            if fault == "raw_extra":
                raw["PRIVATE"] = None
            elif fault == "raw_kind":
                raw["kind"] = "PRIVATE"
            elif fault == "raw_binding":
                raw["binding"]["manifest"] = "e" * 64
            elif fault == "raw_phase":
                raw["phase"] = "completed"
            elif fault == "raw_past":
                raw["at"] = case.relay.intent_at - 1
            elif fault == "raw_future":
                raw["at"] += 60
            elif fault == "plan_past":
                raw["body"]["plan"]["prepared_at"] = case.relay.intent_at - 1
            else:
                raw["body"]["expected"]["generation"] = "e" * 64
            receipt["raw"] = m.host.encode(raw).decode("ascii")
        send(case, value)
        refused(case, lambda: fixture_call(case, case.relay.started))
        assert ledger.state.count == 2 and ledger.state.expected is None
        refused(case, case.relay.started)
        assert len(case.requests) == 5


@pytest.mark.parametrize(
    "fault", ["old", "wav", "sidecar", "extra", "native_artifact", "native_stop"]
)
def test_completion_cannot_hide_file_changes_or_false_native_artifact(
    prepared, actors, calibration, ledger, files, monkeypatch, fault
):
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, _plan, _start = fixture_started(case, files)
        completed = finish(case, files, expected)
        if fault == "old":
            (files.root / "older/old.wav").write_bytes(b"PRIVATE changed old file")
        elif fault == "wav":
            files.wav.write_bytes(b"PRIVATE truncated")
        elif fault == "sidecar":
            files.sidecar.write_bytes(b"PRIVATE replaced metadata")
        elif fault == "extra":
            (files.root / "PRIVATE_extra").write_bytes(b"extra")
        else:
            raw = json.loads(completed["body"]["received"]["raw"])
            if fault == "native_artifact":
                raw["body"]["artifact"]["wav_sha256"] = "e" * 64
            else:
                raw["body"]["stopped"]["active"] = True
            completed["body"]["received"]["raw"] = m.host.encode(raw).decode()
        send(case, completed)
        refused(case, lambda: fixture_call(case, case.relay.completed))
        assert ledger.state.expected == expected and ledger.state.acknowledgment is None
        assert files.wav.exists() and files.sidecar.exists()


@pytest.mark.parametrize("phase", ["started", "completed"])
@pytest.mark.parametrize("fault", ["lost", "clock_changed", "ledger_replaced", "cancelled"])
def test_returned_publication_is_required_even_if_entry_exists_on_disk(
    prepared, actors, calibration, ledger, files, monkeypatch, phase, fault
):
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        if phase == "completed":
            expected, _plan, _start = fixture_started(case, files)
            send(case, finish(case, files, expected))
        else:
            expected, _plan, _start = fixture_start_message(case, files)
        original = getattr(ledger, phase)

        def publish(*args, **kwargs):
            result = original(*args, **kwargs)
            if fault == "lost":
                raise OSError("PRIVATE lost publication return")
            if fault == "cancelled":
                raise KeyboardInterrupt
            if fault == "clock_changed":
                calibration.offset += 10
            if fault == "ledger_replaced":
                old = ledger.directory.with_name("PRIVATE_original_host_ledger")
                ledger.directory.rename(old)
                ledger.directory.mkdir(mode=0o700)
                for entry in old.iterdir():
                    copy = ledger.directory / entry.name
                    copy.write_bytes(entry.read_bytes())
                    copy.chmod(0o600)
            return result

        monkeypatch.setattr(ledger, phase, publish)
        if fault == "cancelled":
            with pytest.raises(KeyboardInterrupt):
                fixture_call(case, getattr(case.relay, phase))
            assert case.relay.phase == "unconfirmed" and case.ready.failed
        else:
            refused(case, lambda: fixture_call(case, getattr(case.relay, phase)))
        assert ledger.state.expected == expected
        assert (ledger.state.acknowledgment is not None) is (phase == "completed")
        refused(case, case.relay.completed)


@pytest.mark.parametrize("phase", ["started", "completed"])
def test_lost_transport_never_uses_receipt_files_or_process_exit(
    prepared, actors, calibration, ledger, files, monkeypatch, phase
):
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        if phase == "completed":
            expected, _plan, _value = fixture_started(case, files)
            finish(case, files, expected)  # Correct files, deliberately NO return.
        case.peer.shutdown(2)
        refused(case, lambda: fixture_call(case, getattr(case.relay, phase)))
        assert ledger.state.acknowledgment is None and len(case.requests) == 5
        assert not case.ready.processes.exited("init")


def test_no_external_begin_or_new_bridge_after_consumed_begin(
    prepared, actors, calibration, ledger, monkeypatch
):
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        with pytest.raises(m.UnconfirmedRelay):
            m.Relay(ledger, case.ready)
        assert case.peer.recv(1) == b"" and ledger.state.count == 2
        assert select.select([case.ready.processes.handles["init"]], [], [], 0)[0] == []


@pytest.mark.parametrize("fault", [None, "missing", "unpinned_tail", "changed"])
def test_completion_keeps_the_independent_original_progress_tip(
    prepared, actors, calibration, ledger, files, monkeypatch, fault
):
    progress = ledger.directory.with_name("PRIVATE_progress")
    progress.mkdir(mode=0o700)  # Before binding the original Engine socket parent.
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, _plan, _value = fixture_started(case, files)
        files.wav.write_bytes(evidence.wav_bytes(samples=160))
        files.wav.chmod(0o600)
        collector = m.local.protected.Collector(ledger.binding.projection.host)
        captured = collector.active(expected)
        tip = m.local.checkpoints.append_progress(
            progress, collector, expected, captured, previous_tip=None
        )
        ledger.progress(progress, collector, tip, now=time.monotonic())
        if fault == "unpinned_tail":
            m.local.checkpoints.append_progress(
                progress, collector, expected, collector.active(expected), previous_tip=tip
            )
        elif fault == "changed":
            path = progress / "0000.json"
            path.write_bytes(path.read_bytes() + b"\n")
        send(case, finish(case, files, expected))

        def action():
            return fixture_call(
                case,
                case.relay.completed,
                progress_directory=None if fault == "missing" else progress,
            )

        if fault:
            refused(case, action)
            assert ledger.state.acknowledgment is None
        else:
            result = action()
            assert result.collected.artifact.samples == 800 and ledger.state.tip == tip


def test_completion_after_original_ready_does_not_refresh_any_dispatch_time(
    prepared, actors, calibration, ledger, files, monkeypatch
):
    prepared.pins = replace(
        prepared.pins,
        command=replace(prepared.pins.command, ready_by=time.monotonic() + 4),
    )
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, _plan, _value = fixture_started(case, files)
        original = case.ready.ready_by, case.relay.native_binding, case.relay.guard.state
        monotonic, monotonic_ns = time.monotonic, time.monotonic_ns
        with monkeypatch.context() as patch:
            # Advance both fixture clocks without waiting or suspending the host.
            patch.setattr(time, "monotonic", lambda: monotonic() + 5)
            patch.setattr(time, "monotonic_ns", lambda: monotonic_ns() + 5_000_000_000)
            assert time.monotonic() > case.ready.ready_by
            send(case, finish(case, files, expected))
            result = case.relay.completed()
        assert result.collected.artifact.samples == 800 and ledger.state.closed
        assert (case.ready.ready_by, case.relay.native_binding, case.relay.guard.state) == original
        assert len(case.requests) == 5


@pytest.mark.parametrize("when", ["verification", "publication"])
def test_completion_checks_original_deadline_after_blocking_work(
    prepared, actors, calibration, ledger, files, monkeypatch, when
):
    with joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        expected, _plan, _value = fixture_started(case, files)
        send(case, finish(case, files, expected))
        target = m.local.protected.Collector if when == "verification" else ledger
        name = "finalized" if when == "verification" else "completed"
        original = getattr(target, name)
        with monkeypatch.context() as patch:

            def late(*args, **kwargs):
                result = original(*args, **kwargs)
                patch.setattr(time, "monotonic", lambda: case.relay.plan.finish_by + 1)
                return result

            patch.setattr(target, name, late)
            refused(case, case.relay.completed)
            assert ledger.state.closed is (when == "publication")
