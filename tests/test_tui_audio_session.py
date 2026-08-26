from __future__ import annotations

import json
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sds200.tui_audio as tui_audio
from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_recording import PcmuWavRecorder
from sds200.audio_session import AudioSessionStatus
from sds200.audio_sinks import PcmSinkRouter, PcmSinkStatistics, PcmWavSink
from sds200.exceptions import AudioOutputError
from sds200.recording_metadata import recording_metadata_path
from sds200.recording_organization import RecordingOrganizationPolicy
from sds200.state import RadioStateSnapshot
from sds200.tui_audio import (
    RecordingPathPolicy,
    SavedPlaybackStatus,
    TuiAudioSession,
)


class CountingAudioTransport:
    def __init__(self) -> None:
        self._handler: AudioChunkHandler | None = None
        self._running = False
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def endpoint(self) -> str:
        return "audio://scanner"

    @property
    def running(self) -> bool:
        return self._running

    def start(self, handler: AudioChunkHandler) -> None:
        self._handler = handler
        self._running = True
        self.start_calls += 1

    def stop(self) -> None:
        self._handler = None
        self._running = False
        self.stop_calls += 1

    def feed(self, data: bytes) -> None:
        assert self._handler is not None
        self._handler(AudioChunk(data))


class CollectingPlaybackSink:
    def __init__(self) -> None:
        self._running = False
        self.received: list[bytes] = []
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def name(self) -> str:
        return "playback:test"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        total = sum(map(len, self.received))
        return PcmSinkStatistics(bytes_submitted=total, bytes_written=total)

    def start(self) -> None:
        self._running = True
        self.start_calls += 1

    def submit_pcm(self, data: bytes) -> None:
        assert self._running
        self.received.append(data)

    def stop(self) -> None:
        self._running = False
        self.stop_calls += 1


class BlockingSink:
    def __init__(self) -> None:
        self._running = False
        self.submitting = threading.Event()
        self.release = threading.Event()
        self.stopped_during_submit = False

    @property
    def name(self) -> str:
        return "blocking:test"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return PcmSinkStatistics()

    def start(self) -> None:
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        del data
        self.submitting.set()
        self.release.wait(timeout=1.0)

    def stop(self) -> None:
        self.stopped_during_submit = self.submitting.is_set() and not self.release.is_set()
        self._running = False


def _wait_for_saved_stop(session: TuiAudioSession) -> None:
    for _ in range(200):
        if session.saved_playback_status is SavedPlaybackStatus.STOPPED:
            return
        time.sleep(0.01)
    raise AssertionError("Saved recording did not finish playing")


def test_pcm_router_waits_for_in_flight_submission_before_stopping_sink() -> None:
    router = PcmSinkRouter()
    sink = BlockingSink()
    router.attach(sink)
    router.start()

    submit_thread = threading.Thread(target=router.submit_pcm, args=(bytes((0, 0)),))
    submit_thread.start()
    assert sink.submitting.wait(timeout=1.0)

    detach_thread = threading.Thread(target=router.detach, args=(sink,))
    detach_thread.start()
    time.sleep(0.02)
    assert detach_thread.is_alive()
    assert sink.running

    sink.release.set()
    submit_thread.join(timeout=1.0)
    detach_thread.join(timeout=1.0)

    assert not submit_thread.is_alive()
    assert not detach_thread.is_alive()
    assert not sink.stopped_during_submit
    assert not sink.running
    router.stop()


def test_recording_path_failure_transitions_session_to_failed(tmp_path: Path) -> None:
    invalid_directory = tmp_path / "not-a-directory"
    invalid_directory.write_text("occupied", encoding="utf-8")
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=invalid_directory),
    )

    session.open_audio()
    with pytest.raises(OSError):
        session.start()

    snapshot = session.snapshot()
    assert snapshot.status is AudioSessionStatus.FAILED
    assert not snapshot.active
    assert snapshot.error is not None
    session.close()


def test_recording_path_policy_rejects_unsafe_templates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="include only"):
        RecordingPathPolicy(directory=tmp_path, template="{channel}.wav")
    with pytest.raises(ValueError, match="include only"):
        RecordingPathPolicy(directory=tmp_path, template="static.wav")
    with pytest.raises(ValueError, match="file name"):
        RecordingPathPolicy(directory=tmp_path, template="nested/{timestamp}.wav")
    with pytest.raises(ValueError, match=r"\.wav"):
        RecordingPathPolicy(directory=tmp_path, template="{timestamp}.raw")


def test_tui_audio_starts_live_playback_and_records_repeatedly(tmp_path: Path) -> None:
    transport = CountingAudioTransport()
    playback = CollectingPlaybackSink()

    def now() -> datetime:
        return datetime(2026, 7, 29, 2, 55, 1, tzinfo=UTC)

    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        live_playback=True,
        playback_sink=playback,
        now=now,
    )

    session.open_audio()
    assert transport.start_calls == 1
    assert not playback.running
    assert not session.live_playback_active

    session.start_live_playback()
    assert playback.running
    assert session.live_playback_active

    session.start()
    transport.feed(bytes((0xFF, 0x80)))
    session.stop()
    session.start()
    transport.feed(bytes((0x00, 0x7F)))
    session.stop()

    assert session.completed_recordings == 2
    assert {entry.path.name for entry in session.recordings} == {
        "sds200-20260729-025501.wav",
        "sds200-20260729-025501-2.wav",
    }
    assert len(playback.received) == 2
    assert list(tmp_path.glob("*.json")) == []
    for entry in session.recordings:
        with wave.open(str(entry.path), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getsampwidth() == 2
            assert recording.getframerate() == 8000
            assert recording.getnframes() == 2

    session.close()
    assert transport.stop_calls == 1
    assert not playback.running


def test_tui_audio_organizes_new_recording_from_start_boundary(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    observed_at = datetime(2026, 8, 3, 5, 30, tzinfo=UTC)
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(
            directory=tmp_path,
            organization=RecordingOrganizationPolicy.from_csv(
                "scanner,date,system,department,site,channel"
            ),
        ),
        metadata=True,
        scanner="SDS/200",
        now=lambda: observed_at,
    )
    session.update_radio_state(
        RadioStateSnapshot(
            system="County / Public Safety",
            department="Fire & EMS",
            site="North",
            channel="Dispatch: 1",
        )
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF, 0x80)))
    session.update_radio_state(
        RadioStateSnapshot(
            system="Changed",
            department="Changed",
            site="South",
            channel="Tac 2",
        )
    )
    session.stop()

    expected = (
        tmp_path
        / "SDS-200"
        / "2026-08-03"
        / "County-Public-Safety"
        / "Fire-EMS"
        / "North"
        / "Dispatch-1"
        / "sds200-20260803-053000.wav"
    )
    assert session.recordings[0].path == expected
    assert session.last_metadata_path == recording_metadata_path(expected)
    assert expected.exists()
    assert recording_metadata_path(expected).exists()
    session.close()


def test_organized_collision_and_sidecar_remain_adjacent(tmp_path: Path) -> None:
    observed_at = datetime(2026, 8, 3, 5, 30, tzinfo=UTC)
    directory = tmp_path / "SDS200" / "2026-08-03" / "Dispatch"
    directory.mkdir(parents=True)
    first = directory / "sds200-20260803-053000.wav"
    recording_metadata_path(first).write_text("{}\n", encoding="utf-8")

    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(
            directory=tmp_path,
            organization=RecordingOrganizationPolicy.from_csv(
                "scanner,date,channel"
            ),
        ),
        metadata=True,
        scanner="SDS200",
        now=lambda: observed_at,
    )
    session.update_radio_state(RadioStateSnapshot(channel="Dispatch"))

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF,)))
    session.stop()

    expected = directory / "sds200-20260803-053000-2.wav"
    assert session.recordings[0].path == expected
    assert session.last_metadata_path == recording_metadata_path(expected)
    session.close()


def test_recording_library_discovers_nested_wav_files(tmp_path: Path) -> None:
    nested = tmp_path / "SDS200" / "2026-08-03"
    nested.mkdir(parents=True)
    recording = nested / "dispatch.wav"
    with wave.open(str(recording), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(bytes((0, 0)))

    policy = RecordingPathPolicy(directory=tmp_path)

    assert policy.library_paths() == (recording,)


def test_tui_audio_writes_opt_in_metadata_with_boundary_state(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    observed_at = datetime(2026, 7, 30, 23, 45, tzinfo=UTC)
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        metadata=True,
        scanner="SDS200",
        now=lambda: observed_at,
    )
    session.update_radio_state(
        RadioStateSnapshot(
            system="County",
            department="Fire",
            site="North",
            channel="Dispatch",
            frequency="154.1900",
        )
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF, 0x80)))
    session.update_radio_state(
        RadioStateSnapshot(
            system="County",
            department="Fire",
            site="North",
            channel="Tac 1",
            frequency="154.2800",
            talkgroup_id="1201",
        )
    )
    session.stop()

    recording = session.recordings[0].path
    sidecar = recording_metadata_path(recording)
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert session.last_metadata_path == sidecar
    assert payload["source"] == {
        "endpoint": "audio://scanner",
        "scanner": "SDS200",
    }
    assert payload["boundaries"]["started"]["state"]["channel"] == "Dispatch"
    assert payload["boundaries"]["stopped"]["state"] == {
        "system": "County",
        "department": "Fire",
        "site": "North",
        "channel": "Tac 1",
        "frequency": "154.2800",
        "talkgroup_id": "1201",
    }
    assert payload["statistics"]["samples"] == 2
    assert session.completed_recordings == 1
    session.close()


def test_tui_recording_starting_listener_cannot_reenter_stop(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
    )
    rejected: list[AudioSessionStatus] = []

    def stop_while_starting(snapshot: object) -> None:
        if getattr(snapshot, "status", None) is AudioSessionStatus.STARTING:
            with pytest.raises(RuntimeError, match="lifecycle operation"):
                session.stop()
            rejected.append(AudioSessionStatus.STARTING)

    session.on_state(stop_while_starting)
    session.start()
    started = session.snapshot()

    assert rejected == [AudioSessionStatus.STARTING]
    assert started.status is AudioSessionStatus.RECORDING
    assert started.stopped_at is None
    assert started.error is None
    assert session.completed_recordings == 0

    session.stop()
    session.close()


def test_tui_recording_stopping_listener_cannot_finalize_twice(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        metadata=True,
    )
    session.start()
    rejected: list[AudioSessionStatus] = []

    def stop_while_stopping(snapshot: object) -> None:
        if getattr(snapshot, "status", None) is AudioSessionStatus.STOPPING:
            with pytest.raises(RuntimeError, match="lifecycle operation"):
                session.stop()
            rejected.append(AudioSessionStatus.STOPPING)

    session.on_state(stop_while_stopping)
    session.stop()

    assert rejected == [AudioSessionStatus.STOPPING]
    assert session.status is AudioSessionStatus.STOPPED
    assert session.completed_recordings == 1
    assert session.last_metadata_path is not None
    assert session.last_metadata_path.exists()
    assert len(session.recordings) == 1
    session.close()


def test_tui_close_rejects_stopped_listener_new_audio_work(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        metadata=True,
    )
    session.start()
    rejected: list[str] = []

    def restart_while_closing(snapshot: object) -> None:
        if (
            not rejected
            and getattr(snapshot, "status", None) is AudioSessionStatus.STOPPED
        ):
            with pytest.raises(RuntimeError, match="lifecycle operation"):
                session.start()
            rejected.append("recording")
            (entry,) = session.recordings
            with pytest.raises(RuntimeError, match="lifecycle operation"):
                session.play_recording(entry.path)
            rejected.append("saved-playback")
            with pytest.raises(RuntimeError, match="lifecycle operation"):
                session.start_live_playback()
            rejected.append("live-playback")
            with pytest.raises(RuntimeError, match="lifecycle operation"):
                session.open_audio()
            rejected.append("network-audio")

    session.on_state(restart_while_closing)
    session.close()

    assert rejected == [
        "recording",
        "saved-playback",
        "live-playback",
        "network-audio",
    ]
    assert not session.open
    assert session.status is AudioSessionStatus.STOPPED
    assert not session.active
    assert session.completed_recordings == 1
    assert session.last_metadata_path is not None
    assert session.last_metadata_path.exists()
    assert session.saved_playback_status is SavedPlaybackStatus.STOPPED
    assert session._saved_thread is None


def test_metadata_sidecar_collision_allocates_a_new_recording_name(
    tmp_path: Path,
) -> None:
    observed_at = datetime(2026, 7, 30, 23, 45, tzinfo=UTC)
    first = tmp_path / "sds200-20260730-234500.wav"
    recording_metadata_path(first).write_text("{}\n", encoding="utf-8")
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        metadata=True,
        scanner="SDS200",
        now=lambda: observed_at,
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF,)))
    session.stop()

    assert session.recordings[0].path.name == "sds200-20260730-234500-2.wav"
    assert session.last_metadata_path == (
        tmp_path / "sds200-20260730-234500-2.wav.json"
    )
    session.close()


def test_live_playback_toggle_keeps_prepared_sink_running(tmp_path: Path) -> None:
    transport = CountingAudioTransport()
    playback = CollectingPlaybackSink()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        playback_sink=playback,
    )

    session.open_audio()
    session.start_live_playback()
    assert playback.running
    assert session.live_playback_active

    session.toggle_live_playback()
    assert playback.running
    assert not session.live_playback_active
    assert not session.live_playback_enabled
    assert playback.stop_calls == 0

    session.start_live_playback()
    assert playback.running
    assert session.live_playback_active
    assert playback.stop_calls == 0

    session.close()
    assert not playback.running
    assert playback.stop_calls == 1


def test_explicit_output_remains_one_shot_and_protected(tmp_path: Path) -> None:
    output = tmp_path / "explicit.wav"
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(output=output),
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF,)))
    session.stop()

    with pytest.raises(RuntimeError, match="already been used"):
        session.start()
    session.close()


def test_tui_recording_thread_start_failure_closes_recorder_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CountingRecorder:
        instances: list[CountingRecorder] = []

        def __init__(
            self,
            path: Path,
            *,
            overwrite: bool = False,
        ) -> None:
            del overwrite
            self.path = path
            self.close_calls = 0
            self.packets = 0
            self.samples = 0
            self.instances.append(self)

        def start(self) -> None:
            return

        def write_pcm(self, data: bytes) -> None:
            del data

        def close(self) -> None:
            self.close_calls += 1

    class StartFailingThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def start(self) -> None:
            raise RuntimeError("secret thread start failure")

    session = TuiAudioSession(
        AudioStream(CountingAudioTransport()),
        RecordingPathPolicy(directory=tmp_path),
    )
    session.open_audio()
    monkeypatch.setattr(tui_audio, "PcmuWavRecorder", CountingRecorder)
    monkeypatch.setattr(
        "sds200.audio_sinks.threading.Thread",
        StartFailingThread,
    )

    with pytest.raises(RuntimeError, match="secret thread start failure"):
        session.start()
    assert len(CountingRecorder.instances) == 1
    assert CountingRecorder.instances[0].close_calls == 1
    session.close()


def test_tui_terminal_wav_failure_is_retained_and_not_in_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
    )

    def fail_write(self: PcmuWavRecorder, data: bytes) -> None:
        del self, data
        raise OSError("sensitive WAV write failure")

    monkeypatch.setattr(PcmuWavRecorder, "write_pcm", fail_write)
    session.open_audio()
    session.start()
    output = session.snapshot().output_path
    transport.feed(bytes((0xFF,)))

    for _ in range(2):
        with pytest.raises(AudioOutputError, match="PCM WAV sink failed"):
            session.stop()
        snapshot = session.snapshot()
        assert snapshot.status is AudioSessionStatus.FAILED
        assert "sensitive" not in (snapshot.error or "")
        assert session.completed_recordings == 0
        assert all(entry.path != output for entry in session.recordings)

    with pytest.raises(RuntimeError, match="already active"):
        session.start()
    with pytest.raises(AudioOutputError, match="PCM WAV sink failed"):
        session.close()


def test_tui_blocked_wav_stop_is_bounded_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingRecorder:
        instance: BlockingRecorder | None = None

        def __init__(
            self,
            path: Path,
            *,
            overwrite: bool = False,
        ) -> None:
            del overwrite
            self.path = path
            self._lock = threading.Lock()
            self._packets = 0
            self._samples = 0
            self.write_started = threading.Event()
            self.write_release = threading.Event()
            self.closed = threading.Event()
            type(self).instance = self

        @property
        def packets(self) -> int:
            with self._lock:
                return self._packets

        @property
        def samples(self) -> int:
            with self._lock:
                return self._samples

        def start(self) -> None:
            self.path.touch()

        def write_pcm(self, data: bytes) -> None:
            with self._lock:
                self.write_started.set()
                assert self.write_release.wait(timeout=5.0)
                self._packets += 1
                self._samples += len(data) // 2

        def close(self) -> None:
            with self._lock:
                self.closed.set()

    def short_timeout_sink(
        recorder: BlockingRecorder,
        *,
        buffer_seconds: float = 5.0,
    ) -> PcmWavSink:
        return PcmWavSink(  # type: ignore[arg-type]
            recorder,
            buffer_seconds=buffer_seconds,
            stop_timeout=0.02,
        )

    transport = CountingAudioTransport()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
    )
    monkeypatch.setattr(tui_audio, "PcmuWavRecorder", BlockingRecorder)
    monkeypatch.setattr(tui_audio, "PcmWavSink", short_timeout_sink)
    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF,)))
    recorder = BlockingRecorder.instance
    assert recorder is not None
    assert recorder.write_started.wait(timeout=1.0)

    started = time.monotonic()
    with pytest.raises(AudioOutputError, match="Timed out while finalizing"):
        session.stop()
    assert time.monotonic() - started < 0.2
    assert session.status is AudioSessionStatus.FAILED
    assert session.snapshot().status is AudioSessionStatus.FAILED

    recorder.write_release.set()
    assert recorder.closed.wait(timeout=1.0)
    session.stop()
    assert session.status is AudioSessionStatus.STOPPED
    session.close()


def test_saved_playback_temporarily_replaces_and_restores_live_audio(
    tmp_path: Path,
) -> None:
    transport = CountingAudioTransport()
    playback = CollectingPlaybackSink()
    session = TuiAudioSession(
        AudioStream(transport),
        RecordingPathPolicy(directory=tmp_path),
        live_playback=True,
        playback_sink=playback,
    )

    session.open_audio()
    session.start()
    transport.feed(bytes((0xFF, 0x80, 0x00, 0x7F)))
    session.stop()
    entry = session.recordings[0]

    session.play_recording(entry.path)
    _wait_for_saved_stop(session)

    assert session.live_playback_enabled
    assert session.live_playback_active
    assert session.saved_playback_path == entry.path
    assert session.saved_playback_error is None
    assert playback.start_calls >= 2
    session.close()
