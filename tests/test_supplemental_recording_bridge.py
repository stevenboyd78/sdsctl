"""Real child/ledger/native files; explicit synthetic namespace mapping only."""

import importlib.util
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_binding as binding_tests
from . import test_supplemental_recording_channel as channel
from . import test_supplemental_recording_channel_native as native_tests

NAME = "supplemental_recording_bridge"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(channel.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
p, h, n = m.protected, m.host, m.native
tree, configured, prepared = native_tests.tree, native_tests.configured, native_tests.prepared
layout = binding_tests.layout


@pytest.fixture
def joined(prepared, layout, tmp_path, monkeypatch):
    # Test-only namespace aliases. No /mnt/data or /media path is created/opened.
    # Child uses its actual private tmp root. Host manifests retain a distinct
    # synthetic host alias; only its low-level read-only inventory is routed.
    case = h.projection.fixed.CANDIDATE
    data = layout.data.parent / case
    layout = replace(
        layout,
        slug=case,
        context=layout.context.parent / case.removeprefix("local_"),
        data=data,
        recordings=layout.media / prepared.tree.root.name,
        deployment=data / "deployment.toml",
        configuration=data / "configuration.toml",
        accepted=data / "accepted/accepted-profile.json",
    )
    stored = p._decode(
        p.manifest_bytes(
            replace(prepared.stored.baseline, root=layout.recordings),
            prepared.stored.writer,
            prepared.stored.contract.audio_endpoint_sha256,
        )
    )
    monkeypatch.setattr(h.projection, "NATIVE_MEDIA", prepared.tree.root.parent)
    projection = h.projection.project(layout, stored)
    assert projection.native == prepared.stored
    calls = []

    def routed(function):
        def read(root, *args, **kwargs):
            if root == layout.recordings:
                calls.append(root)
                root = prepared.tree.root
            return function(root, *args, **kwargs)

        return read

    monkeypatch.setattr(p.evidence, "opened_root", routed(p.evidence.opened_root))
    monkeypatch.setattr(p.evidence, "inventory", routed(p.evidence.inventory))
    monkeypatch.setattr(p.monitor, "opened_root", routed(p.monitor.opened_root))
    directory = tmp_path / "PRIVATE_HOST_LEDGER"
    directory.mkdir(mode=0o700)
    progress = tmp_path / "PRIVATE_HOST_PROGRESS"
    progress.mkdir(mode=0o700)
    binding = h.Binding(projection, "1" * 64, "2" * 64, binding_tests.BOOT)
    now = time.monotonic()
    ledger = h.Ledger(directory, binding, now=now)
    ledger.start_intent(
        now=time.monotonic(),
        generation=prepared.generation,
        authorization_sha256="3" * 64,
        start_by=now + 8,
        finish_by=now + 20,
    )
    native = n.Binding(
        projection.native,
        prepared.generation,
        projection.sha256,
        binding.source_sha256,
        ledger.state.start_by,
        ledger.state.finish_by,
    )
    return SimpleNamespace(
        prepared=prepared,
        ledger=ledger,
        binding=native,
        calls=calls,
        progress=progress,
        expected=replace(
            prepared.tree.expected, audio_endpoint_sha256=stored.contract.audio_endpoint_sha256
        ),
    )


def refused(action):
    with pytest.raises(m.UnconfirmedBridge) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert "PRIVATE" not in str(caught.value)


def send_start(joined, send, *, plan=None):
    n.Sender(send, joined.binding).started(joined.expected, plan or channel.plan(joined.binding))


@pytest.mark.parametrize("fault", [None, "lost_started_return", "cleanup_failure"])
def test_real_native_return_to_projected_host_ledger_and_files(joined, fault):
    def run(send):
        return native_tests.native_run(joined.prepared, joined.binding, send, fault)

    with channel.child(joined.binding, run) as receiver:
        bridge = m.Bridge(joined.ledger, receiver)
        if fault == "lost_started_return":
            refused(bridge.started)
            assert joined.ledger.state.expected is None
            refused(bridge.completed)
        else:
            expected = bridge.started()
            assert joined.ledger.state.expected == expected == receiver.expected
            if fault == "cleanup_failure":
                refused(bridge.completed)
                assert joined.ledger.state.acknowledgment is None
            else:
                result = bridge.completed()
                assert bridge.phase == "closed" and joined.ledger.state.closed
                assert result.collected.files.stage == "finalized"
                assert result.collected.artifact.samples == 1280
                assert (
                    result.acknowledgment.contract_sha256
                    == joined.ledger.binding.projection.host.contract.sha256
                )
                assert (
                    result.acknowledgment.contract_sha256 != joined.binding.stored.contract.sha256
                )
                assert result.native_return_sha256 == result.acknowledgment.completion_sha256
                assert h.load(joined.ledger.directory, joined.ledger.binding) == joined.ledger.state
                assert joined.calls and not hasattr(result, "exited")
                refused(bridge.completed)
                refused(bridge.started)
    assert receiver.fixture_returncode == 0
    assert (joined.prepared.tree.root / "older/old.wav").read_bytes() == b"old evidence unchanged"
    if fault:
        assert bridge.phase == "unconfirmed" and not joined.ledger.state.closed
        # No disk receipt is promoted to an acknowledgment. Administrative
        # scope/recovery remains separate from this successful-return bridge.
        assert (joined.prepared.spec.receipts / "started.json").exists()


@pytest.mark.parametrize(
    "fault", ["source", "projection", "native_manifest", "generation", "start", "finish"]
)
def test_refuses_mismatched_channel_before_receiving(joined, fault):
    changes = {
        "source": {"source_sha256": "f" * 64},
        "projection": {"projection_sha256": "f" * 64},
        "native_manifest": {"stored": joined.ledger.binding.projection.host},
        "generation": {"generation": "f" * 64},
        "start": {"start_by": joined.binding.start_by - 1},
        "finish": {"finish_by": joined.binding.finish_by - 1},
    }[fault]
    with channel.child(joined.binding, lambda send: None) as receiver:
        receiver.binding = replace(receiver.binding, **changes)
        refused(lambda: m.Bridge(joined.ledger, receiver))
        assert receiver.phase == "started"
        assert joined.ledger.state.count == 2


@pytest.mark.parametrize(
    "fault", ["missing_intent", "closed", "directory_extra", "memory_state", "replayed_start"]
)
def test_requires_exact_durable_unconsumed_host_intent(joined, fault):
    if fault == "missing_intent":
        # Deliberately corrupt test evidence, not a production cleanup/retry.
        (joined.ledger.directory / "0001.json").unlink()
    elif fault == "closed":
        joined.ledger.abandon(now=time.monotonic())
    elif fault == "directory_extra":
        (joined.ledger.directory / "extra").touch()
    elif fault == "memory_state":
        joined.ledger.state = replace(joined.ledger.state, sha256="f" * 64)
    else:
        joined.ledger.started(joined.expected, now=time.monotonic(), success_sha256="4" * 64)
    with channel.child(joined.binding, lambda send: None) as receiver:
        refused(lambda: m.Bridge(joined.ledger, receiver))
        assert receiver.phase == "started"


@pytest.mark.parametrize(
    "fault", ["owner", "peer", "closed_fd", "changed_directory", "changed_binding", "ledger_drift"]
)
def test_drift_after_construction_consumes_bridge_without_receive(joined, fault):
    with channel.child(joined.binding, lambda send: send_start(joined, send)) as receiver:
        bridge = m.Bridge(joined.ledger, receiver)
        if fault == "owner":
            receiver.owner_pid += 1
        elif fault == "peer":
            receiver.start_ticks += 1
        elif fault == "closed_fd":
            receiver.close()
        elif fault == "changed_directory":
            joined.ledger.directory = joined.progress
        elif fault == "changed_binding":
            joined.ledger.binding = replace(joined.ledger.binding, plan_sha256="f" * 64)
        else:
            joined.ledger.abandon(now=time.monotonic())
        refused(bridge.started)
        assert receiver.phase == "started" and bridge.phase == "unconfirmed"
        refused(bridge.started)


def test_native_plan_must_follow_host_intent(joined):
    plan = channel.plan(joined.binding)
    plan = replace(plan, prepared_at=joined.ledger.state.now - 0.01)
    with channel.child(
        joined.binding, lambda send: send_start(joined, send, plan=plan)
    ) as receiver:
        bridge = m.Bridge(joined.ledger, receiver)
        refused(bridge.started)
        assert joined.ledger.state.expected is None and joined.ledger.state.count == 2


@pytest.mark.parametrize("fault", ["late", "lost", "cancelled"])
def test_durable_start_entry_does_not_replace_timely_return(joined, monkeypatch, fault):
    with channel.child(joined.binding, lambda send: send_start(joined, send)) as receiver:
        bridge = m.Bridge(joined.ledger, receiver)
        original = joined.ledger.started

        def publish(*args, **kwargs):
            result = original(*args, **kwargs)
            if fault == "late":
                late = receiver.plan.start_by + 1
                monkeypatch.setattr(m.time, "monotonic", lambda: late)
            elif fault == "lost":
                raise TimeoutError("PRIVATE lost publication return")
            else:
                raise KeyboardInterrupt
            return result

        monkeypatch.setattr(joined.ledger, "started", publish)
        if fault == "cancelled":
            with pytest.raises(KeyboardInterrupt):
                bridge.started()
        else:
            refused(bridge.started)
        assert joined.ledger.state.expected == joined.expected
        assert bridge.phase == "unconfirmed" and joined.ledger.state.acknowledgment is None
        refused(bridge.completed)


@pytest.mark.parametrize("fault", ["changed_old", "changed_wav", "unreported_new", "native_digest"])
def test_native_success_cannot_hide_changed_host_files_or_artifact(joined, monkeypatch, fault):
    def run(send):
        return native_tests.native_run(joined.prepared, joined.binding, send, None)

    with channel.child(joined.binding, run) as receiver:
        bridge = m.Bridge(joined.ledger, receiver)
        expected = bridge.started()
        original = receiver.receive

        def receive():
            report = original()
            root = joined.prepared.tree.root
            if fault == "changed_old":
                (root / "older/old.wav").write_bytes(b"old evidence CHANGED!!")
            elif fault == "changed_wav":
                wav = root / p.evidence.filename(expected.case, expected.started_at)
                raw = bytearray(wav.read_bytes())
                raw[-1] ^= 1
                wav.write_bytes(raw)
            elif fault == "unreported_new":
                (root / "unrelated.wav").write_bytes(b"unexpected")
            else:
                # Fault-injected trusted child report after the real kernel
                # receive, to isolate independent artifact equality validation.
                value = json.loads(report.raw)
                value["body"]["artifact"]["wav_sha256"] = "f" * 64
                report = replace(report, raw=n.encode(value))
            return report

        monkeypatch.setattr(receiver, "receive", receive)
        refused(bridge.completed)
        assert joined.ledger.state.acknowledgment is None and not joined.ledger.state.closed
        assert bridge.phase == "unconfirmed" and receiver.phase == "closed"
        refused(bridge.completed)
    assert receiver.fixture_returncode == 0


@pytest.mark.parametrize("fault", [None, "missing_directory", "unpinned_tail", "changed_entry"])
def test_completion_replays_the_independently_host_pinned_progress(joined, fault):
    def run(send):
        return native_tests.native_run(joined.prepared, joined.binding, send, None)

    with channel.child(joined.binding, run) as receiver:
        bridge = m.Bridge(joined.ledger, receiver)
        expected = bridge.started()
        collector = p.Collector(joined.ledger.binding.projection.host)
        collected = collector.active(expected)
        tip = m.checkpoints.append_progress(
            joined.progress, collector, expected, collected, previous_tip=None
        )
        joined.ledger.progress(joined.progress, collector, tip, now=time.monotonic())
        if fault == "unpinned_tail":
            m.checkpoints.append_progress(
                joined.progress, collector, expected, collector.active(expected), previous_tip=tip
            )
        elif fault == "changed_entry":
            path = joined.progress / "0000.json"
            path.write_bytes(path.read_bytes() + b"\n")
        directory = None if fault == "missing_directory" else joined.progress
        if fault is None:
            result = bridge.completed(progress_directory=directory)
            assert result.collected.artifact.samples == 1280
            assert joined.ledger.state.tip == tip and joined.ledger.state.closed
        else:
            refused(lambda: bridge.completed(progress_directory=directory))
            assert joined.ledger.state.acknowledgment is None and bridge.phase == "unconfirmed"
    assert receiver.fixture_returncode == 0


@pytest.mark.parametrize("fault", ["late_publication", "lost_publication", "late_verification"])
def test_completion_deadline_includes_file_validation_and_host_publication(
    joined, monkeypatch, fault
):
    def run(send):
        return native_tests.native_run(joined.prepared, joined.binding, send, None)

    with channel.child(joined.binding, run) as receiver:
        bridge = m.Bridge(joined.ledger, receiver)
        bridge.started()
        if fault == "late_verification":
            original = p.Collector.finalized

            def verify(*args, **kwargs):
                result = original(*args, **kwargs)
                late = bridge.plan.finish_by + 1
                monkeypatch.setattr(m.time, "monotonic", lambda: late)
                return result

            monkeypatch.setattr(p.Collector, "finalized", verify)
        else:
            original = joined.ledger.completed

            def publish(*args, **kwargs):
                result = original(*args, **kwargs)
                if fault == "lost_publication":
                    raise TimeoutError("PRIVATE completion return lost")
                late = bridge.plan.finish_by + 1
                monkeypatch.setattr(m.time, "monotonic", lambda: late)
                return result

            monkeypatch.setattr(joined.ledger, "completed", publish)
        refused(bridge.completed)
        assert bridge.phase == "unconfirmed"
        assert joined.ledger.state.closed == (fault != "late_verification")
        refused(bridge.completed)
    assert receiver.fixture_returncode == 0
