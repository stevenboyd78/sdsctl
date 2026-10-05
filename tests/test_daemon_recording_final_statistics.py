"""Final recording evidence must not track a later shared audio session."""

import json
from dataclasses import replace

import pytest

import sds200.daemon_recording as recording
from sds200.audio_session import AudioSessionStatus

from .test_daemon_recording import FakeRuntime, FlakyFinalizationSink


def change_transport(runtime):
    transport = runtime.audio.stream.transport
    transport.statistics = replace(
        transport.statistics,
        **{name: 77 for name in recording.AudioReliabilitySnapshot().as_dict()},
    )


def test_stopped_statistics_remain_fixed_across_poll_stop_close_and_next_recording(tmp_path):
    runtime = FakeRuntime()
    manager = recording.DaemonRecordingManager(runtime, tmp_path)
    try:
        manager.start_recording()
        runtime.router.submit_pcm(b"\x00\x00" * 4)
        stopped = manager.stop_recording()
        metadata = stopped.metadata_path.read_bytes()
        wav = stopped.recording_path.read_bytes()
        change_transport(runtime)
        assert manager.snapshot().reliability == stopped.reliability
        assert manager.stop_recording().reliability == stopped.reliability
        assert json.loads(metadata)["statistics"]["reliability"] == stopped.reliability.as_dict()

        restarted = manager.start_recording()
        assert restarted.reliability.packets_lost == 77  # Existing cumulative semantics.
        runtime.audio.stream.transport.statistics = replace(
            runtime.audio.stream.transport.statistics, packets_lost=78
        )
        assert manager.snapshot().reliability.packets_lost == 78
        second = manager.stop_recording()
        assert second.reliability.packets_lost == 78
        assert json.loads(second.metadata_path.read_bytes())["statistics"]["reliability"] == (
            second.reliability.as_dict()
        )
        change_transport(runtime)
        manager.close()
        assert manager.snapshot().closed
        assert manager.snapshot().reliability == second.reliability
        assert stopped.metadata_path.read_bytes() == metadata
        assert stopped.recording_path.read_bytes() == wav
        assert runtime.audio.stream.running
    finally:
        manager.close()
        runtime.close()


@pytest.mark.parametrize("phase", ["metadata", "stopped_listener", "metadata_failure"])
def test_final_response_and_event_use_the_same_statistics_as_metadata(tmp_path, monkeypatch, phase):
    runtime = FakeRuntime()
    manager = recording.DaemonRecordingManager(runtime, tmp_path)
    original_write = recording.write_recording_metadata
    payloads = []
    final_events = []

    def write(metadata):
        payloads.append(metadata.as_dict())
        if phase != "stopped_listener":
            change_transport(runtime)
        if phase == "metadata_failure":
            raise OSError("private test failure")
        return original_write(metadata)

    def observe(snapshot):
        if snapshot.status in {AudioSessionStatus.STOPPED, AudioSessionStatus.FAILED}:
            if phase == "stopped_listener":
                change_transport(runtime)
            final_events.append(snapshot)

    monkeypatch.setattr(recording, "write_recording_metadata", write)
    manager.on_state(observe)
    try:
        manager.start_recording()
        if phase == "metadata_failure":
            with pytest.raises(recording.DaemonRecordingOperationError):
                manager.stop_recording()
            final = manager.snapshot()
            assert final.status is AudioSessionStatus.FAILED
        else:
            final = manager.stop_recording()
        expected = payloads[0]["statistics"]["reliability"]
        assert expected["packets_lost"] == 2
        assert final.reliability.as_dict() == expected
        assert final_events[-1].reliability.as_dict() == expected
        assert manager.snapshot().reliability.as_dict() == expected
    finally:
        monkeypatch.setattr(recording, "write_recording_metadata", original_write)
        manager.close()
        runtime.close()


def test_failed_start_statistics_are_stable_until_another_start(tmp_path):
    runtime = FakeRuntime()
    manager = recording.DaemonRecordingManager(runtime, tmp_path)
    try:
        runtime.attach_error = OSError("private failure")
        with pytest.raises(recording.DaemonRecordingOperationError):
            manager.start_recording()
        failed = manager.snapshot()
        assert failed.status is AudioSessionStatus.FAILED
        change_transport(runtime)
        assert manager.snapshot().reliability == failed.reliability
        runtime.attach_error = None
        assert manager.start_recording().reliability.packets_lost == 77
    finally:
        manager.close()
        runtime.close()


def test_failed_finalization_freezes_but_explicit_retry_captures_new_boundary(
    tmp_path, monkeypatch
):
    runtime = FakeRuntime()
    monkeypatch.setattr(recording, "PcmWavSink", FlakyFinalizationSink)
    manager = recording.DaemonRecordingManager(runtime, tmp_path)
    try:
        manager.start_recording()
        runtime.router.submit_pcm(b"\x00\x00" * 2)
        with pytest.raises(recording.DaemonRecordingOperationError):
            manager.stop_recording()
        failed = manager.snapshot()
        change_transport(runtime)
        assert manager.snapshot().reliability == failed.reliability
        recovered = manager.stop_recording()
        assert recovered.status is AudioSessionStatus.STOPPED
        assert recovered.reliability.packets_lost == 77
        assert json.loads(recovered.metadata_path.read_bytes())["statistics"]["reliability"] == (
            recovered.reliability.as_dict()
        )
    finally:
        manager.close()
        runtime.close()
