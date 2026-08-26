from __future__ import annotations

import struct
import threading
import time
import wave
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sds200.audio import AudioChunk, AudioChunkHandler, AudioStream
from sds200.audio_recording import PCM_SAMPLE_WIDTH, PCMU_SAMPLE_RATE, PcmuWavRecorder
from sds200.audio_sinks import (
    AudioFanoutSession,
    AudioFanoutSnapshot,
    BufferedPlaybackSink,
    LocalPlaybackAdapter,
    PcmSinkRouter,
    PcmSinkStatistics,
    PcmSubscriberTransition,
    PcmWavSink,
    SoundDevicePlaybackSink,
    inspect_audio_backend,
)
from sds200.exceptions import AudioOutputError
from sds200.reliability import ReconnectPolicy


class FakeAudioTransport:
    def __init__(self) -> None:
        self._handler: AudioChunkHandler | None = None
        self._running = False

    @property
    def endpoint(self) -> str:
        return "fake://audio"

    @property
    def running(self) -> bool:
        return self._running

    def start(self, handler: AudioChunkHandler) -> None:
        self._handler = handler
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._handler = None

    def feed(self, chunk: AudioChunk) -> None:
        assert self._handler is not None
        self._handler(chunk)


class CollectingSink:
    def __init__(self, name: str) -> None:
        self._name = name
        self._running = False
        self.received: list[bytes] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        total = sum(map(len, self.received))
        return PcmSinkStatistics(bytes_submitted=total, bytes_written=total)

    def start(self) -> None:
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        assert self._running
        self.received.append(data)

    def stop(self) -> None:
        self._running = False


class FakeRawOutputStream:
    def __init__(self, callback: Callable[[object, int, object, object], None]) -> None:
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class FakePlaybackAdapter:
    def __init__(self) -> None:
        self._running = False
        self.reader: Callable[[int], bytes] | None = None
        self.status_reporter: Callable[[bool], None] | None = None
        self.interrupt_calls = 0
        self.close_calls = 0

    @property
    def name(self) -> str:
        return "fake-playback"

    @property
    def running(self) -> bool:
        return self._running

    def start(
        self,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None:
        self.reader = pcm_reader
        self.status_reporter = status_reporter
        self._running = True

    def interrupt(self) -> None:
        self.interrupt_calls += 1
        self._running = False

    def close(self) -> None:
        self.close_calls += 1
        self._running = False


class FakeInputOutputPair:
    def __getitem__(self, index: int) -> int:
        return (0, 2)[index]


class FakeSoundDeviceDefaults:
    device = FakeInputOutputPair()


class FakeSoundDeviceModule:
    def __init__(self) -> None:
        self.default = FakeSoundDeviceDefaults()
        self.stream: FakeRawOutputStream | None = None
        self.arguments: dict[str, object] = {}

    def RawOutputStream(self, **kwargs: object) -> FakeRawOutputStream:
        self.arguments = kwargs
        callback = kwargs["callback"]
        assert callable(callback)
        self.stream = FakeRawOutputStream(callback)
        return self.stream

    def get_portaudio_version(self) -> tuple[int, str]:
        return (1246720, "PortAudio V19.7.0")

    def query_hostapis(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "name": "ALSA",
                "default_input_device": 0,
                "default_output_device": 2,
            },
            {
                "name": "JACK Audio Connection Kit",
                "default_input_device": -1,
                "default_output_device": -1,
            },
        )

    def query_devices(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "name": "Input only",
                "index": 0,
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
                "default_samplerate": 48000.0,
            },
            {
                "name": "HDMI",
                "index": 2,
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
            {
                "name": "USB Audio",
                "index": 4,
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 44100.0,
            },
        )


def test_audio_fanout_decodes_once_for_multiple_sinks() -> None:
    transport = FakeAudioTransport()
    first = CollectingSink("first")
    second = CollectingSink("second")
    session = AudioFanoutSession(AudioStream(transport), (first, second))

    with session:
        transport.feed(AudioChunk(bytes((0xFF, 0x80, 0x00, 0x7F))))

    expected = struct.pack("<4h", 0, 32124, -32124, 0)
    assert first.received == [expected]
    assert second.received == [expected]
    snapshot = session.snapshot()
    assert snapshot.packets == 1
    assert snapshot.samples == 4
    assert snapshot.audio_duration_seconds == 4 / PCMU_SAMPLE_RATE
    assert not snapshot.running


def test_audio_fanout_emits_state_after_start_and_stop() -> None:
    transport = FakeAudioTransport()
    sink = CollectingSink("collector")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []

    unsubscribe = session.on_state(observed.append)

    session.start()
    transport.feed(AudioChunk(b"\xff"))
    session.stop()
    unsubscribe()

    assert [snapshot.running for snapshot in observed] == [True, False]
    assert observed[0].packets == 0
    assert observed[1].packets == 1
    assert observed[1].samples == 1


def test_audio_fanout_state_listener_can_close_started_session() -> None:
    transport = FakeAudioTransport()
    sink = CollectingSink("collector")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[bool] = []
    errors: list[BaseException] = []

    def close_after_start(snapshot: AudioFanoutSnapshot) -> None:
        observed.append(snapshot.running)
        if snapshot.running:
            session.close()

    session.on_state(close_after_start)

    def start_session() -> None:
        try:
            session.start()
        except BaseException as error:
            errors.append(error)

    starter = threading.Thread(target=start_session, daemon=True)
    starter.start()
    starter.join(timeout=1.0)

    assert not starter.is_alive()
    assert not errors
    assert observed == [True, False]
    assert not session.running
    assert not sink.running


def test_audio_fanout_unsubscribe_and_repeated_stop_emit_nothing_more() -> None:
    transport = FakeAudioTransport()
    sink = CollectingSink("collector")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []

    unsubscribe = session.on_state(observed.append)
    session.start()
    unsubscribe()

    session.stop()
    session.stop()

    assert [snapshot.running for snapshot in observed] == [True]


def test_audio_fanout_state_listener_failures_are_isolated() -> None:
    transport = FakeAudioTransport()
    sink = CollectingSink("collector")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []

    def fail_listener(snapshot: AudioFanoutSnapshot) -> None:
        del snapshot
        raise RuntimeError("listener failed")

    session.on_state(fail_listener)
    session.on_state(observed.append)

    session.start()
    session.stop()

    assert [snapshot.running for snapshot in observed] == [True, False]


def test_audio_fanout_emits_stopped_state_after_start_failure() -> None:
    class FailingStartSink(CollectingSink):
        def start(self) -> None:
            raise RuntimeError("audio sink start failed")

    transport = FakeAudioTransport()
    sink = FailingStartSink("failing")
    session = AudioFanoutSession(AudioStream(transport), (sink,))
    observed: list[AudioFanoutSnapshot] = []
    session.on_state(observed.append)

    with pytest.raises(RuntimeError, match="audio sink start failed"):
        session.start()

    assert len(observed) == 1
    assert not observed[0].running
    assert observed[0].packets == 0
    assert observed[0].samples == 0


def test_buffered_playback_sink_uses_renderer_neutral_adapter() -> None:
    adapter = FakePlaybackAdapter()
    sink = BufferedPlaybackSink(
        name="playback:test",
        buffer_ms=1,
        adapter_factory=lambda: adapter,
    )

    assert isinstance(adapter, LocalPlaybackAdapter)
    sink.start()
    assert sink.running
    assert adapter.reader is not None
    assert adapter.status_reporter is not None

    pcm = bytes(range(32))
    sink.submit_pcm(pcm)
    assert adapter.reader(16) == pcm[-16:]
    adapter.status_reporter(True)

    statistics = sink.statistics
    assert statistics.bytes_submitted == 32
    assert statistics.bytes_written == 16
    assert statistics.bytes_dropped == 16
    assert statistics.overflows == 1
    assert statistics.callback_statuses == 1

    sink.set_muted(True)
    assert adapter.reader(16) == bytes(16)
    assert sink.statistics.underflows == 0

    sink.stop()
    assert adapter.interrupt_calls == 1
    assert adapter.close_calls == 1
    assert not sink.running


def test_buffered_playback_sink_rejects_invalid_adapter_factory() -> None:
    sink = BufferedPlaybackSink(
        name="playback:test",
        adapter_factory=lambda: object(),  # type: ignore[arg-type]
    )

    with pytest.raises(TypeError, match="LocalPlaybackAdapter-compatible"):
        sink.start()


def test_sounddevice_playback_uses_nonblocking_bounded_buffer() -> None:
    module = FakeSoundDeviceModule()
    sink = SoundDevicePlaybackSink(
        buffer_ms=1,
        module_loader=lambda name: module,
    )
    sink.start()
    assert module.stream is not None
    assert module.arguments["samplerate"] == PCMU_SAMPLE_RATE
    assert module.arguments["channels"] == 1
    assert module.arguments["dtype"] == "int16"

    pcm = bytes(range(32))
    sink.submit_pcm(pcm)
    output = bytearray(16)
    module.stream.callback(output, 8, object(), object())

    assert output == pcm[-16:]
    statistics = sink.statistics
    assert statistics.bytes_submitted == 32
    assert statistics.bytes_written == 16
    assert statistics.bytes_dropped == 16
    assert statistics.overflows == 1
    assert statistics.callback_statuses == 1

    underflow = bytearray(16)
    module.stream.callback(underflow, 8, object(), False)
    assert underflow == bytes(16)
    assert sink.statistics.underflows == 1

    sink.set_muted(True)
    sink.submit_pcm(bytes(range(16)))
    muted = bytearray(16)
    module.stream.callback(muted, 8, object(), object())
    assert muted == bytes(16)
    statistics = sink.statistics
    assert statistics.bytes_submitted == 32
    assert statistics.queued_bytes == 0
    assert statistics.underflows == 1
    assert statistics.callback_statuses == 2
    assert sink.muted

    sink.set_muted(False)
    assert not sink.muted
    sink.interrupt()
    assert not sink.running
    sink.stop()
    assert module.stream.closed
    assert not sink.running


def test_sounddevice_playback_reports_missing_optional_dependency() -> None:
    def missing(name: str) -> object:
        raise ModuleNotFoundError(name=name)

    sink = SoundDevicePlaybackSink(module_loader=missing)
    with pytest.raises(AudioOutputError, match=r"sds200\[playback\]"):
        sink.start()


def test_sounddevice_playback_reports_missing_portaudio_runtime() -> None:
    def missing_portaudio(name: str) -> object:
        del name
        raise OSError("PortAudio library not found")

    sink = SoundDevicePlaybackSink(module_loader=missing_portaudio)
    with pytest.raises(AudioOutputError, match=r"sudo apt install libportaudio2"):
        sink.start()


def test_audio_backend_inspection_reports_output_devices() -> None:
    module = FakeSoundDeviceModule()

    backend = inspect_audio_backend(module_loader=lambda name: module)

    assert backend.backend == "PortAudio"
    assert backend.version == "PortAudio V19.7.0"
    assert backend.default_output_device == 2
    assert [host_api.name for host_api in backend.host_apis] == [
        "ALSA",
        "JACK Audio Connection Kit",
    ]
    assert [device.index for device in backend.output_devices] == [2, 4]
    assert backend.output_devices[0].default
    assert backend.output_devices[0].host_api_name == "ALSA"
    assert not backend.output_devices[1].default
    assert backend.output_devices[1].host_api_name == "JACK Audio Connection Kit"


def test_pcm_wav_sink_drains_buffer_before_close(tmp_path: Path) -> None:
    output = tmp_path / "fanout.wav"
    recorder = PcmuWavRecorder(output)
    sink = PcmWavSink(recorder)
    sink.start()
    sink.submit_pcm(struct.pack("<4h", 0, 1, -1, 2))
    sink.stop()

    statistics = sink.statistics
    assert statistics.bytes_submitted == 4 * PCM_SAMPLE_WIDTH
    assert statistics.bytes_written == 4 * PCM_SAMPLE_WIDTH
    assert statistics.bytes_dropped == 0
    with wave.open(str(output), "rb") as recording:
        assert recording.getframerate() == PCMU_SAMPLE_RATE
        assert recording.getnframes() == 4
        assert struct.unpack("<4h", recording.readframes(4)) == (0, 1, -1, 2)



class HealthTestSink:
    def __init__(
        self,
        name: str,
        *,
        fail_start: bool = False,
        partial_start: bool = False,
        fail_submit: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self._name = name
        self._running = False
        self.fail_start = fail_start
        self.partial_start = partial_start
        self.fail_submit = fail_submit
        self.fail_stop = fail_stop
        self.received: list[bytes] = []
        self.stop_calls = 0
        self.submit_called = threading.Event()

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        total = sum(map(len, self.received))
        return PcmSinkStatistics(
            bytes_submitted=total,
            bytes_written=total,
        )

    def start(self) -> None:
        if self.fail_start:
            if self.partial_start:
                self._running = True
            raise RuntimeError("secret startup detail")
        self._running = True

    def submit_pcm(self, data: bytes) -> None:
        if self.fail_submit:
            self.submit_called.set()
            raise RuntimeError("secret submission detail")
        self.received.append(data)
        self.submit_called.set()

    def stop(self) -> None:
        self.stop_calls += 1
        self._running = False
        if self.fail_stop:
            raise RuntimeError("secret shutdown detail")


def test_pcm_router_startup_failure_does_not_abort_other_subscribers() -> None:
    router = PcmSinkRouter()
    failing = HealthTestSink(
        "failing",
        fail_start=True,
        partial_start=True,
    )
    healthy = HealthTestSink("healthy")

    router.attach(failing)
    router.attach(healthy)
    router.start()

    assert router.running
    assert healthy.running
    assert not failing.running
    assert failing.stop_calls == 1

    failing_snapshot = router.subscriber_snapshot(failing)
    healthy_snapshot = router.subscriber_snapshot(healthy)
    assert failing_snapshot is not None
    assert healthy_snapshot is not None
    assert failing_snapshot.state == "failed"
    assert failing_snapshot.health == "failed"
    assert not failing_snapshot.attached
    assert failing_snapshot.start_attempts == 1
    assert failing_snapshot.start_failures == 1
    assert failing_snapshot.failures == 1
    assert failing_snapshot.last_error == "RuntimeError"
    assert "secret" not in failing_snapshot.as_dict()["last_error"]
    assert healthy_snapshot.state == "active"
    assert healthy_snapshot.health == "healthy"

    router.stop()
    assert healthy.stop_calls == 1


def test_pcm_router_tracks_submit_health_and_isolates_listeners() -> None:
    initial = datetime(2026, 8, 3, 22, 30, tzinfo=UTC)
    current = initial

    def now() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    router = PcmSinkRouter(now=now)
    failing = HealthTestSink("failing", fail_submit=True)
    healthy = HealthTestSink("healthy")
    observed: list[PcmSubscriberTransition] = []

    def fail_listener(transition: PcmSubscriberTransition) -> None:
        del transition
        raise RuntimeError("listener failed")

    router.on_transition(fail_listener)
    router.on_transition(observed.append)
    router.attach(failing)
    router.attach(healthy)
    router.start()

    pcm = b"\x01\x00"
    router.submit_pcm(pcm)

    assert failing.submit_called.wait(timeout=1.0)
    assert healthy.submit_called.wait(timeout=1.0)
    assert healthy.received == [pcm]
    assert _wait_until(
        lambda: (
            (failed := router.subscriber_snapshot(failing)) is not None
            and failed.state == "failed"
            and failed.submit_failures == 1
            and (active := router.subscriber_snapshot(healthy)) is not None
            and active.successful_submissions == 1
        )
    )
    failing_snapshot = router.subscriber_snapshot(failing)
    healthy_snapshot = router.subscriber_snapshot(healthy)
    assert failing_snapshot is not None
    assert healthy_snapshot is not None
    assert failing_snapshot.state == "failed"
    assert failing_snapshot.submit_failures == 1
    assert failing_snapshot.submissions == 1
    assert failing_snapshot.successful_submissions == 0
    assert failing_snapshot.last_failure_at is not None
    assert failing_snapshot.last_error == "RuntimeError"
    assert healthy_snapshot.state == "active"
    assert healthy_snapshot.submissions == 1
    assert healthy_snapshot.successful_submissions == 1
    assert healthy_snapshot.submit_failures == 0

    assert _wait_until(
        lambda: any(
            transition.snapshot.name == "failing"
            and transition.state == "failed"
            for transition in observed
        )
    )
    sequences = [transition.sequence for transition in observed]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    failing_transitions = [
        transition
        for transition in observed
        if transition.snapshot.name == "failing"
    ]
    assert failing_transitions[-1].state == "failed"
    assert failing_transitions[-1].health == "failed"

    payload = router.snapshot().as_dict()
    assert payload["running"] is True
    assert payload["transition_sequence"] == sequences[-1]
    assert len(payload["subscribers"]) == 2

    router.stop()

    stopped_snapshot = router.subscriber_snapshot(failing)
    assert stopped_snapshot is not None
    assert stopped_snapshot.state == "detached"
    assert stopped_snapshot.last_error == "RuntimeError"
    assert stopped_snapshot.submit_failures == 1


def test_pcm_router_shutdown_failure_isolated_and_recorded() -> None:
    router = PcmSinkRouter()
    failing = HealthTestSink("failing", fail_stop=True)
    healthy = HealthTestSink("healthy")

    router.attach(failing)
    router.attach(healthy)
    router.start()
    router.stop()

    assert not router.running
    assert healthy.stop_calls == 1
    assert failing.stop_calls == 1

    failing_snapshot = router.subscriber_snapshot(failing)
    healthy_snapshot = router.subscriber_snapshot(healthy)
    assert failing_snapshot is not None
    assert healthy_snapshot is not None
    assert failing_snapshot.state == "failed"
    assert failing_snapshot.health == "failed"
    assert not failing_snapshot.attached
    assert failing_snapshot.stop_failures == 1
    assert failing_snapshot.failures == 1
    assert failing_snapshot.last_error == "RuntimeError"
    assert healthy_snapshot.state == "detached"
    assert healthy_snapshot.health == "inactive"
    assert not healthy_snapshot.attached


def test_pcm_router_dynamic_start_failure_reaches_requesting_caller() -> None:
    router = PcmSinkRouter()
    healthy = HealthTestSink("healthy")
    failing = HealthTestSink(
        "failing",
        fail_start=True,
        partial_start=True,
    )

    router.attach(healthy)
    router.start()

    with pytest.raises(RuntimeError, match="secret startup detail"):
        router.attach(failing)

    assert router.running
    assert healthy.running
    router.submit_pcm(b"\x01\x00")
    assert healthy.submit_called.wait(timeout=1.0)
    assert healthy.received == [b"\x01\x00"]

    snapshot = router.subscriber_snapshot(failing)
    assert snapshot is not None
    assert snapshot.state == "failed"
    assert not snapshot.attached
    assert not failing.running
    assert failing.stop_calls == 1

    router.stop()


class LifecycleTestSink:
    def __init__(
        self,
        name: str,
        *,
        block_submission: bool = False,
        failures: int = 0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._name = name
        self._running = False
        self._block_submission = block_submission
        self._failures_remaining = failures
        self._clock = clock
        self._condition = threading.Condition()
        self._in_submit = 0
        self.received: list[bytes] = []
        self.call_times: list[float] = []
        self.submit_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.stopped_during_submit = False
        self.submitting = threading.Event()
        self.release = threading.Event()
        self.stopped = threading.Event()
        if not block_submission:
            self.release.set()

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        with self._condition:
            return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._condition:
            total = sum(map(len, self.received))
        return PcmSinkStatistics(
            bytes_submitted=total,
            bytes_written=total,
        )

    def start(self) -> None:
        with self._condition:
            self.start_calls += 1
            self._running = True

    def submit_pcm(self, data: bytes) -> None:
        with self._condition:
            self.submit_calls += 1
            self._in_submit += 1
            if self._clock is not None:
                self.call_times.append(self._clock())
            should_fail = self._failures_remaining > 0
            if should_fail:
                self._failures_remaining -= 1
            self._condition.notify_all()
        self.submitting.set()
        try:
            if self._block_submission:
                assert self.release.wait(timeout=5.0)
            if should_fail:
                raise RuntimeError("secret subscriber failure detail")
            with self._condition:
                self.received.append(data)
                self._condition.notify_all()
        finally:
            with self._condition:
                self._in_submit -= 1
                self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self.stop_calls += 1
            self.stopped_during_submit |= self._in_submit > 0
            self._running = False
            self._condition.notify_all()
        self.stopped.set()

    def wait_for_submissions(self, count: int, *, timeout: float = 1.0) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self.submit_calls >= count,
                timeout=timeout,
            )

    def wait_for_received_bytes(self, count: int, *, timeout: float = 1.0) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: sum(map(len, self.received)) >= count,
                timeout=timeout,
            )


class FakeMonotonicClock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = 0.0

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


class ControlledRecorder(PcmuWavRecorder):
    def __init__(
        self,
        path: Path,
        *,
        block_write: bool = False,
        fail_write: bool = False,
        fail_close: bool = False,
    ) -> None:
        super().__init__(path)
        self.fail_write = fail_write
        self.fail_close = fail_close
        self.start_calls = 0
        self.write_calls = 0
        self.close_calls = 0
        self.write_started = threading.Event()
        self.write_release = threading.Event()
        self.closed = threading.Event()
        if not block_write:
            self.write_release.set()

    def start(self) -> None:
        self.start_calls += 1

    def write_pcm(self, data: bytes) -> None:
        del data
        self.write_calls += 1
        self.write_started.set()
        assert self.write_release.wait(timeout=5.0)
        if self.fail_write:
            raise RuntimeError("secret recorder write detail")

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        if self.fail_close:
            raise RuntimeError("secret recorder close detail")


def test_audio_fanout_producer_and_healthy_sink_ignore_blocked_sink() -> None:
    transport = FakeAudioTransport()
    blocked = LifecycleTestSink("blocked", block_submission=True)
    healthy = LifecycleTestSink("healthy")
    session = AudioFanoutSession(
        AudioStream(transport),
        (blocked, healthy),
        stop_timeout=0.1,
    )
    session.start()

    producer = threading.Thread(
        target=transport.feed,
        args=(AudioChunk(b"\xff\x80"),),
    )
    producer.start()
    producer.join(timeout=0.2)
    producer_blocked = producer.is_alive()
    if producer_blocked:
        blocked.release.set()
        producer.join(timeout=1.0)

    try:
        assert not producer_blocked
        assert blocked.submitting.wait(timeout=1.0)
        assert healthy.wait_for_received_bytes(2 * PCM_SAMPLE_WIDTH)
    finally:
        blocked.release.set()
        session.stop()


def test_audio_fanout_keeps_router_subclasses_behind_dispatch_worker() -> None:
    class BlockingRouter(PcmSinkRouter):
        def __init__(self) -> None:
            super().__init__()
            self.submitting = threading.Event()
            self.release = threading.Event()

        def submit_pcm(self, data: bytes) -> None:
            self.submitting.set()
            assert self.release.wait(timeout=5.0)
            super().submit_pcm(data)

        def stop(self) -> None:
            super().stop()

    transport = FakeAudioTransport()
    blocked = BlockingRouter()
    healthy = LifecycleTestSink("healthy")
    session = AudioFanoutSession(
        AudioStream(transport),
        (blocked, healthy),
        stop_timeout=0.1,
    )
    session.start()

    producer = threading.Thread(
        target=transport.feed,
        args=(AudioChunk(b"\xff"),),
    )
    producer.start()
    producer.join(timeout=0.2)
    try:
        assert not producer.is_alive()
        assert blocked.submitting.wait(timeout=1.0)
        assert healthy.wait_for_received_bytes(PCM_SAMPLE_WIDTH)
    finally:
        blocked.release.set()
        session.stop()


def test_exact_router_handoff_ignores_blocked_subscriber_statistics() -> None:
    class BlockingStatisticsSink(LifecycleTestSink):
        def __init__(self) -> None:
            super().__init__("blocking-statistics")
            self.block_statistics = False
            self.statistics_started = threading.Event()
            self.statistics_release = threading.Event()

        @property
        def statistics(self) -> PcmSinkStatistics:
            if self.block_statistics:
                self.statistics_started.set()
                assert self.statistics_release.wait(timeout=5.0)
            return super().statistics

    transport = FakeAudioTransport()
    router = PcmSinkRouter()
    blocked = BlockingStatisticsSink()
    healthy = LifecycleTestSink("healthy")
    router.attach(blocked)
    router.attach(healthy)
    session = AudioFanoutSession(AudioStream(transport), (router,))
    session.start()
    blocked.block_statistics = True
    snapshot_thread = threading.Thread(target=router.snapshot)
    snapshot_thread.start()
    assert blocked.statistics_started.wait(timeout=1.0)

    producer = threading.Thread(
        target=transport.feed,
        args=(AudioChunk(b"\xff"),),
    )
    producer.start()
    producer.join(timeout=0.2)
    try:
        assert not producer.is_alive()
        assert healthy.wait_for_received_bytes(PCM_SAMPLE_WIDTH)
    finally:
        blocked.statistics_release.set()
        snapshot_thread.join(timeout=1.0)
        session.stop()


@pytest.mark.parametrize("blocked_property", ["statistics", "running"])
def test_audio_fanout_stop_ignores_blocked_subscriber_telemetry(
    blocked_property: str,
) -> None:
    class BlockingTelemetrySink(LifecycleTestSink):
        def __init__(self) -> None:
            super().__init__("blocking-telemetry")
            self.block_telemetry = False
            self.telemetry_started = threading.Event()
            self.telemetry_release = threading.Event()

        def _wait_if_blocked(self, property_name: str) -> None:
            if self.block_telemetry and blocked_property == property_name:
                self.telemetry_started.set()
                assert self.telemetry_release.wait(timeout=5.0)

        @property
        def running(self) -> bool:
            self._wait_if_blocked("running")
            return super().running

        @property
        def statistics(self) -> PcmSinkStatistics:
            self._wait_if_blocked("statistics")
            return super().statistics

    stop_timeout = 0.05
    transport = FakeAudioTransport()
    router = PcmSinkRouter(stop_timeout=stop_timeout)
    blocked = BlockingTelemetrySink()
    router.attach(blocked)
    session = AudioFanoutSession(
        AudioStream(transport),
        (router,),
        stop_timeout=stop_timeout,
    )
    session.start()
    blocked.block_telemetry = True
    snapshot_thread = threading.Thread(target=session.snapshot, daemon=True)
    snapshot_thread.start()
    assert blocked.telemetry_started.wait(timeout=1.0)

    started = time.monotonic()
    try:
        session.stop()
        elapsed = time.monotonic() - started
        assert elapsed < stop_timeout * 3
        assert blocked.stop_calls == 1
    finally:
        blocked.telemetry_release.set()
        snapshot_thread.join(timeout=1.0)
    assert not snapshot_thread.is_alive()


@pytest.mark.parametrize("blocked_property", ["statistics", "running"])
def test_pcm_router_stop_ignores_blocked_subscriber_telemetry(
    blocked_property: str,
) -> None:
    class BlockingTelemetrySink(LifecycleTestSink):
        def __init__(self) -> None:
            super().__init__("blocking-telemetry")
            self.block_telemetry = False
            self.telemetry_started = threading.Event()
            self.telemetry_release = threading.Event()

        def _wait_if_blocked(self, property_name: str) -> None:
            if self.block_telemetry and blocked_property == property_name:
                self.telemetry_started.set()
                assert self.telemetry_release.wait(timeout=5.0)

        @property
        def running(self) -> bool:
            self._wait_if_blocked("running")
            return super().running

        @property
        def statistics(self) -> PcmSinkStatistics:
            self._wait_if_blocked("statistics")
            return super().statistics

    stop_timeout = 0.05
    router = PcmSinkRouter(stop_timeout=stop_timeout)
    blocked = BlockingTelemetrySink()
    healthy = LifecycleTestSink("healthy")
    router.attach(blocked)
    router.attach(healthy)
    router.start()
    blocked.block_telemetry = True
    snapshot_thread = threading.Thread(target=router.snapshot)
    snapshot_thread.start()
    assert blocked.telemetry_started.wait(timeout=1.0)

    producer = threading.Thread(target=router.submit_pcm, args=(b"\x01\x00",))
    producer.start()
    producer.join(timeout=0.2)
    assert not producer.is_alive()
    assert healthy.wait_for_received_bytes(PCM_SAMPLE_WIDTH)

    started = time.monotonic()
    router.stop(raise_on_failure=True)
    elapsed = time.monotonic() - started
    try:
        assert elapsed < stop_timeout * 3
        assert blocked.stop_calls == 1
        assert healthy.stop_calls == 1
        router.submit_pcm(b"\x02\x00")
        assert healthy.submit_calls == 1
    finally:
        blocked.telemetry_release.set()
        snapshot_thread.join(timeout=1.0)
    assert not snapshot_thread.is_alive()


def test_pcm_router_drops_oldest_complete_samples_per_blocked_sink() -> None:
    capacity_bytes = 8
    router = PcmSinkRouter(
        buffer_seconds=(
            capacity_bytes / (PCMU_SAMPLE_RATE * PCM_SAMPLE_WIDTH)
        ),
        stop_timeout=0.1,
    )
    blocked = LifecycleTestSink("blocked", block_submission=True)
    healthy = LifecycleTestSink("healthy")
    router.attach(blocked)
    router.attach(healthy)
    router.start()

    first = b"\x00\x00"
    oldest = b"\x01\x00\x01\x00"
    retained = b"\x02\x00\x02\x00\x03\x00\x03\x00"
    producer = threading.Thread(target=router.submit_pcm, args=(first,))
    producer.start()
    producer.join(timeout=0.2)
    producer_blocked = producer.is_alive()
    if producer_blocked:
        blocked.release.set()
        producer.join(timeout=1.0)

    try:
        assert not producer_blocked
        assert blocked.submitting.wait(timeout=1.0)
        assert healthy.wait_for_received_bytes(len(first))
        router.submit_pcm(oldest)
        assert healthy.wait_for_received_bytes(len(first) + len(oldest))
        router.submit_pcm(retained[:4])
        assert healthy.wait_for_received_bytes(
            len(first) + len(oldest) + len(retained[:4])
        )
        router.submit_pcm(retained[4:])
        assert healthy.wait_for_received_bytes(
            len(first) + len(oldest) + len(retained)
        )
        assert b"".join(healthy.received) == first + oldest + retained
        snapshot = router.subscriber_snapshot(blocked)
        assert snapshot is not None
        assert snapshot.statistics.bytes_submitted == (
            len(first) + len(oldest) + len(retained)
        )
        assert snapshot.statistics.bytes_dropped == len(oldest)
        assert snapshot.statistics.queued_bytes == capacity_bytes
        assert snapshot.statistics.overflows == 1

        blocked.release.set()
        assert blocked.wait_for_received_bytes(len(first) + len(retained))
        assert b"".join(blocked.received) == first + retained
    finally:
        blocked.release.set()
        router.stop()


def test_pcm_router_quarantine_uses_bounded_backoff_and_redacted_transitions() -> None:
    clock = FakeMonotonicClock()
    router = PcmSinkRouter(
        buffer_seconds=0.001,
        retry_policy=ReconnectPolicy(
            initial_delay=1.0,
            multiplier=2.0,
            max_delay=2.0,
        ),
        clock=clock,
    )
    failing = LifecycleTestSink("retrying", failures=2, clock=clock)
    observed: list[PcmSubscriberTransition] = []
    recovered = threading.Event()

    def observe(transition: PcmSubscriberTransition) -> None:
        if transition.snapshot.name != failing.name:
            return
        observed.append(transition)
        if transition.previous_state == "failed" and transition.state == "active":
            recovered.set()

    router.on_transition(observe)
    router.attach(failing)
    router.start()
    router.submit_pcm(b"\x01\x00")
    assert failing.wait_for_submissions(1)
    assert _wait_until(
        lambda: (
            (snapshot := router.subscriber_snapshot(failing)) is not None
            and snapshot.submit_failures == 1
        )
    )

    for sample in range(2, 8):
        router.submit_pcm(bytes((sample, 0)))
    clock.advance(0.99)
    router.submit_pcm(b"\x08\x00")
    time.sleep(0.02)
    assert failing.submit_calls == 1

    clock.advance(0.01)
    router.submit_pcm(b"\x09\x00")
    assert failing.wait_for_submissions(2)
    assert _wait_until(
        lambda: (
            (snapshot := router.subscriber_snapshot(failing)) is not None
            and snapshot.submit_failures == 2
        )
    )
    clock.advance(1.99)
    router.submit_pcm(b"\x0a\x00")
    time.sleep(0.02)
    assert failing.submit_calls == 2

    clock.advance(0.01)
    router.submit_pcm(b"\x0b\x00")
    assert failing.wait_for_submissions(3)
    assert recovered.wait(timeout=1.0)
    assert failing.call_times == [0.0, 1.0, 3.0]

    snapshot = router.subscriber_snapshot(failing)
    assert snapshot is not None
    assert snapshot.state == "active"
    assert snapshot.submissions == 3
    assert snapshot.submit_failures == 2
    assert snapshot.successful_submissions == 1
    assert snapshot.last_error == "RuntimeError"
    assert snapshot.statistics.bytes_dropped > 0
    assert snapshot.statistics.bytes_dropped % PCM_SAMPLE_WIDTH == 0

    sequences = [transition.sequence for transition in observed]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert [transition.state for transition in observed] == [
        "attached",
        "starting",
        "active",
        "failed",
        "active",
    ]
    assert "secret" not in repr([transition.as_dict() for transition in observed])
    router.stop()


def test_pcm_router_detach_is_bounded_without_concurrent_stop() -> None:
    router = PcmSinkRouter(stop_timeout=0.03)
    blocked = LifecycleTestSink("blocked", block_submission=True)
    router.attach(blocked)
    router.start()
    router.submit_pcm(b"\x01\x00")
    assert blocked.submitting.wait(timeout=1.0)

    started = time.monotonic()
    router.detach(blocked)
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert blocked.stop_calls == 0
    assert blocked.running
    snapshot = router.subscriber_snapshot(blocked)
    assert snapshot is not None
    assert not snapshot.attached
    assert snapshot.state == "failed"
    assert snapshot.last_error == "AudioOutputError"

    blocked.release.set()
    assert blocked.stopped.wait(timeout=1.0)
    assert blocked.stop_calls == 1
    assert not blocked.stopped_during_submit
    assert _wait_until(
        lambda: (
            (latest := router.subscriber_snapshot(blocked)) is not None
            and latest.state == "detached"
        )
    )
    router.stop()


def test_pcm_router_stop_uses_one_global_join_deadline() -> None:
    stop_timeout = 0.05
    router = PcmSinkRouter(stop_timeout=stop_timeout)
    sinks = tuple(
        LifecycleTestSink(f"blocked-{index}", block_submission=True)
        for index in range(4)
    )
    for sink in sinks:
        router.attach(sink)
    router.start()
    router.submit_pcm(b"\x01\x00")
    assert all(sink.submitting.wait(timeout=1.0) for sink in sinks)

    started = time.monotonic()
    router.stop()
    elapsed = time.monotonic() - started
    try:
        assert elapsed < stop_timeout * 3
        assert all(sink.stop_calls == 0 for sink in sinks)
    finally:
        for sink in sinks:
            sink.release.set()

    assert all(sink.stopped.wait(timeout=1.0) for sink in sinks)
    assert all(sink.stop_calls == 1 for sink in sinks)
    assert all(not sink.stopped_during_submit for sink in sinks)


def test_pcm_router_stop_false_preserves_warm_sink_for_reattach() -> None:
    router = PcmSinkRouter()
    sink = LifecycleTestSink("warm")
    router.attach(sink)
    router.start()
    router.submit_pcm(b"\x01\x00")
    assert sink.wait_for_submissions(1)

    router.detach(sink, stop=False)
    assert sink.running
    assert sink.start_calls == 1
    assert sink.stop_calls == 0
    router.submit_pcm(b"\x02\x00")
    assert sink.submit_calls == 1

    router.attach(sink)
    assert sink.start_calls == 1
    router.submit_pcm(b"\x03\x00")
    assert sink.wait_for_submissions(2)
    assert sink.wait_for_received_bytes(2 * PCM_SAMPLE_WIDTH)
    assert b"".join(sink.received) == b"\x01\x00\x03\x00"

    router.detach(sink, raise_on_failure=True)
    assert sink.stop_calls == 1
    assert not sink.running
    router.stop()


def test_pcm_router_detach_fences_pre_detach_offer_before_reattach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = PcmSinkRouter()
    sink = LifecycleTestSink("generation-fence")
    router.attach(sink)
    router.start()

    with router._routing_lock:
        (dispatcher,) = router._active_dispatchers
    original_offer = dispatcher.offer
    offer_started = threading.Event()
    offer_release = threading.Event()

    def paused_offer(data: bytes) -> bool:
        offer_started.set()
        assert offer_release.wait(timeout=5.0)
        return original_offer(data)

    monkeypatch.setattr(dispatcher, "offer", paused_offer)
    producer = threading.Thread(target=router.submit_pcm, args=(b"\x01\x00",))
    producer.start()
    assert offer_started.wait(timeout=1.0)

    detach_done = threading.Event()
    reattach_allowed = threading.Event()
    cycle_done = threading.Event()
    cycle_failures: list[BaseException] = []
    delivered_at_detach: list[bytes] = []

    def detach_and_reattach() -> None:
        try:
            router.detach(sink, stop=False)
            with sink._condition:
                delivered_at_detach.extend(sink.received)
            detach_done.set()
            assert reattach_allowed.wait(timeout=5.0)
            router.attach(sink)
        except BaseException as error:
            cycle_failures.append(error)
        finally:
            cycle_done.set()

    cycle = threading.Thread(target=detach_and_reattach)
    cycle.start()
    try:
        assert not detach_done.wait(timeout=0.05)
    finally:
        offer_release.set()
        producer.join(timeout=1.0)
        assert detach_done.wait(timeout=1.0)
        reattach_allowed.set()
        cycle.join(timeout=1.0)

    assert not producer.is_alive()
    assert not cycle.is_alive()
    assert not cycle_failures
    router.submit_pcm(b"\x02\x00")
    expected = b"".join(delivered_at_detach) + b"\x02\x00"
    assert sink.wait_for_received_bytes(len(expected))
    assert b"".join(sink.received) == expected
    router.stop()


def test_pcm_router_stop_false_rolls_back_when_sink_cannot_quiesce() -> None:
    router = PcmSinkRouter(stop_timeout=0.02)
    sink = LifecycleTestSink("warm-blocked", block_submission=True)
    router.attach(sink)
    router.start()
    router.submit_pcm(b"\x01\x00")
    assert sink.submitting.wait(timeout=1.0)

    with pytest.raises(AudioOutputError, match="Timed out while pausing"):
        router.detach(sink, stop=False)

    snapshot = router.subscriber_snapshot(sink)
    assert snapshot is not None
    assert snapshot.attached
    assert snapshot.state == "active"
    assert sink.stop_calls == 0

    sink.release.set()
    assert sink.wait_for_received_bytes(PCM_SAMPLE_WIDTH)
    router.stop()
    assert sink.stop_calls == 1
    assert not sink.stopped_during_submit


def test_pcm_router_transition_listener_can_detach_current_sink() -> None:
    router = PcmSinkRouter(stop_timeout=0.02)
    sink = LifecycleTestSink("listener-detach", failures=1)
    listener_returned = threading.Event()

    def detach_failed(transition: PcmSubscriberTransition) -> None:
        if transition.snapshot.name == sink.name and transition.state == "failed":
            router.detach(sink, raise_on_failure=True)
            listener_returned.set()

    router.on_transition(detach_failed)
    router.attach(sink)
    router.start()
    router.submit_pcm(b"\x01\x00")

    assert listener_returned.wait(timeout=1.0)
    assert sink.stopped.wait(timeout=1.0)
    assert _wait_until(
        lambda: (
            (snapshot := router.subscriber_snapshot(sink)) is not None
            and snapshot.state == "detached"
        )
    )
    snapshot = router.subscriber_snapshot(sink)
    assert snapshot is not None
    assert snapshot.stop_failures == 0
    router.stop()


def test_pcm_router_stop_false_rollback_preserves_later_submit_failure() -> None:
    router = PcmSinkRouter(stop_timeout=0.02)
    sink = LifecycleTestSink(
        "warm-blocked-failure",
        block_submission=True,
        failures=1,
    )
    router.attach(sink)
    router.start()
    router.submit_pcm(b"\x01\x00")
    assert sink.submitting.wait(timeout=1.0)

    with pytest.raises(AudioOutputError, match="Timed out while pausing"):
        router.detach(sink, stop=False)

    sink.release.set()
    assert _wait_until(
        lambda: (
            (snapshot := router.subscriber_snapshot(sink)) is not None
            and snapshot.state == "failed"
        )
    )
    snapshot = router.subscriber_snapshot(sink)
    assert snapshot is not None
    assert snapshot.attached
    assert snapshot.last_error == "RuntimeError"
    assert "secret" not in repr(snapshot)
    router.stop()


def test_pcm_wav_timeout_leaves_finalization_with_worker(tmp_path: Path) -> None:
    recorder = ControlledRecorder(tmp_path / "blocked.wav", block_write=True)
    sink = PcmWavSink(
        recorder,
        stop_timeout=0.02,
    )
    sink.start()
    sink.submit_pcm(b"\x01\x00")
    assert recorder.write_started.wait(timeout=1.0)

    started = time.monotonic()
    with pytest.raises(AudioOutputError, match="Timed out"):
        sink.stop()
    assert time.monotonic() - started < 0.2
    assert recorder.close_calls == 0

    recorder.write_release.set()
    assert recorder.closed.wait(timeout=1.0)
    sink.stop()
    sink.stop()
    assert recorder.close_calls == 1


def test_pcm_wav_concurrent_stops_share_one_bounded_wait(tmp_path: Path) -> None:
    recorder = ControlledRecorder(
        tmp_path / "concurrent-stop.wav",
        block_write=True,
    )
    sink = PcmWavSink(  # type: ignore[arg-type]
        recorder,
        stop_timeout=0.03,
    )
    sink.start()
    sink.submit_pcm(b"\x01\x00")
    assert recorder.write_started.wait(timeout=1.0)

    barrier = threading.Barrier(3)
    outcomes: list[tuple[BaseException, float]] = []

    def stop_sink() -> None:
        barrier.wait()
        started = time.monotonic()
        try:
            sink.stop()
        except BaseException as error:
            outcomes.append((error, time.monotonic() - started))

    threads = tuple(threading.Thread(target=stop_sink) for _ in range(2))
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=0.2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2
    assert all(isinstance(error, AudioOutputError) for error, _ in outcomes)
    assert all(elapsed < 0.15 for _, elapsed in outcomes)
    assert recorder.close_calls == 0

    recorder.write_release.set()
    assert recorder.closed.wait(timeout=1.0)
    sink.stop()
    assert recorder.close_calls == 1


def test_pcm_wav_write_failure_closes_once_on_worker(tmp_path: Path) -> None:
    recorder = ControlledRecorder(tmp_path / "write-failure.wav", fail_write=True)
    sink = PcmWavSink(recorder)
    sink.start()
    sink.submit_pcm(b"\x01\x00")
    assert recorder.closed.wait(timeout=1.0)

    with pytest.raises(AudioOutputError, match="PCM WAV sink failed") as first:
        sink.stop()
    with pytest.raises(AudioOutputError, match="PCM WAV sink failed") as second:
        sink.stop()
    assert "secret" not in str(first.value)
    assert str(second.value) == str(first.value)
    assert recorder.close_calls == 1
    assert sink.statistics.bytes_dropped == PCM_SAMPLE_WIDTH
    assert sink.statistics.queued_bytes == 0


def test_pcm_wav_close_failure_is_reported_once(tmp_path: Path) -> None:
    recorder = ControlledRecorder(tmp_path / "close-failure.wav", fail_close=True)
    sink = PcmWavSink(recorder)
    sink.start()
    sink.submit_pcm(b"\x01\x00")

    with pytest.raises(AudioOutputError, match="PCM WAV sink failed") as first:
        sink.stop()
    with pytest.raises(AudioOutputError, match="PCM WAV sink failed") as second:
        sink.stop()
    assert "secret" not in str(first.value)
    assert str(second.value) == str(first.value)
    assert recorder.close_calls == 1
    assert sink.statistics.bytes_written == PCM_SAMPLE_WIDTH


def test_pcm_wav_thread_start_failure_closes_partial_recorder_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = ControlledRecorder(tmp_path / "thread-start-failure.wav")

    class StartFailingThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def start(self) -> None:
            raise RuntimeError("secret thread start detail")

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(
        "sds200.audio_sinks.threading.Thread",
        StartFailingThread,
    )
    sink = PcmWavSink(recorder)

    with pytest.raises(RuntimeError, match="secret thread start detail"):
        sink.start()
    assert recorder.start_calls == 1
    assert recorder.close_calls == 1
    assert not sink.running

    sink.stop()
    assert recorder.close_calls == 1


def test_pcm_wav_thread_start_cleanup_failure_persists_redacted_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = ControlledRecorder(
        tmp_path / "thread-start-close-failure.wav",
        fail_close=True,
    )

    class StartFailingThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def start(self) -> None:
            raise RuntimeError("secret thread start detail")

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

    monkeypatch.setattr(
        "sds200.audio_sinks.threading.Thread",
        StartFailingThread,
    )
    sink = PcmWavSink(recorder)

    with pytest.raises(RuntimeError, match="secret thread start detail"):
        sink.start()
    assert recorder.close_calls == 1

    with pytest.raises(AudioOutputError, match="RuntimeError") as first:
        sink.stop()
    with pytest.raises(AudioOutputError, match="RuntimeError") as second:
        sink.stop()
    assert "secret recorder close detail" not in str(first.value)
    assert str(second.value) == str(first.value)
    assert recorder.close_calls == 1
