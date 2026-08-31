from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable

import pytest

from sds200.home_assistant_live_audio import (
    HOME_ASSISTANT_LIVE_AUDIO_FORMAT,
    LiveAudioEncoderPipeline,
    LiveAudioLeaseClosed,
    LiveAudioSession,
    LiveAudioSessionState,
    home_assistant_live_audio_encoder_config,
)
from sds200.remote_audio_encoder import AudioEncoderResult


class FakePipeline:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.start_error: BaseException | None = None
        self.publisher: Callable[[bytes], None] | None = None

    def start(self, publish: Callable[[bytes], None]) -> None:
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        self.publisher = publish

    def stop(self) -> None:
        self.stop_calls += 1
        self.publisher = None

    def publish(self, data: bytes) -> None:
        assert self.publisher is not None
        self.publisher(data)


class FakeEncoder:
    def __init__(self) -> None:
        self.inputs: list[bytes] = []
        self.outputs: deque[bytes] = deque()
        self.output_ready = threading.Condition()
        self.closed = False

    def write_pcm(self, data: bytes) -> None:
        self.inputs.append(data)
        with self.output_ready:
            self.outputs.append(b"mp3:" + data)
            self.output_ready.notify()

    def read_encoded(self, size: int) -> bytes:
        assert size > 0
        with self.output_ready:
            self.output_ready.wait_for(lambda: self.outputs or self.closed, timeout=1.0)
            if self.outputs:
                return self.outputs.popleft()
            return b""

    def finalize(
        self,
        *,
        output_waiter: Callable[[float], bool] | None = None,
    ) -> AudioEncoderResult:
        with self.output_ready:
            self.closed = True
            self.output_ready.notify_all()
        if output_waiter is not None:
            assert output_waiter(1.0)
        return AudioEncoderResult(
            returncode=0,
            interrupted=False,
            diagnostic="",
            exit_reported=False,
        )


class FakeRouter:
    def __init__(self) -> None:
        self.sink = None
        self.attach_calls = 0
        self.detach_calls = 0

    def attach(self, sink) -> None:  # type: ignore[no-untyped-def]
        self.attach_calls += 1
        self.sink = sink
        sink.start()

    def detach(
        self,
        sink,
        *,
        stop: bool = True,
        raise_on_failure: bool = False,
    ) -> None:  # type: ignore[no-untyped-def]
        del raise_on_failure
        self.detach_calls += 1
        assert sink is self.sink
        if stop:
            sink.stop()
        self.sink = None

    def submit(self, data: bytes) -> None:
        assert self.sink is not None
        self.sink.submit_pcm(data)


def test_selected_format_and_encoder_command_are_exact() -> None:
    selected = HOME_ASSISTANT_LIVE_AUDIO_FORMAT
    config = home_assistant_live_audio_encoder_config(executable="encoder")

    assert selected.container == "MP3"
    assert selected.codec == "MP3 (MPEG audio layer 3)"
    assert selected.mime_type == "audio/mpeg"
    assert selected.sample_rate == 44_100
    assert selected.channels == 1
    assert selected.bit_rate == 64_000
    assert not selected.seekable
    assert selected.duration_seconds is None
    assert config.command == (
        "encoder",
        "--silent",
        "-r",
        "--signed",
        "--little-endian",
        "--bitwidth",
        "16",
        "-s",
        "8",
        "--resample",
        "44.1",
        "-m",
        "m",
        "--cbr",
        "-b",
        "64",
        "-",
        "-",
    )


def test_first_lease_starts_one_pipeline_and_last_lease_stops_it() -> None:
    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline)

    first = session.subscribe()
    second = session.subscribe()
    pipeline.publish(b"encoded")

    assert pipeline.start_calls == 1
    assert first.get(0.1) == b"encoded"
    assert second.get(0.1) == b"encoded"
    assert session.snapshot().consumer_count == 2
    assert session.snapshot().chunks_published == 1
    assert session.snapshot().bytes_published == 7

    first.close()
    assert pipeline.stop_calls == 0
    second.close()
    assert pipeline.stop_calls == 1
    assert session.snapshot().state is LiveAudioSessionState.IDLE
    session.close()


def test_encoder_pipeline_attaches_one_restartable_sink_to_pcm_router() -> None:
    router = FakeRouter()
    encoders: list[FakeEncoder] = []

    def encoder_factory() -> FakeEncoder:
        encoder = FakeEncoder()
        encoders.append(encoder)
        return encoder

    pipeline = LiveAudioEncoderPipeline(
        router,  # type: ignore[arg-type]
        encoder_factory=encoder_factory,
        read_size=512,
    )
    session = LiveAudioSession(pipeline)

    first = session.subscribe()
    second = session.subscribe()
    router.submit(b"pcm")

    assert first.get(1.0) == b"mp3:pcm"
    assert second.get(1.0) == b"mp3:pcm"
    assert router.attach_calls == 1
    assert len(encoders) == 1
    assert encoders[0].inputs == [b"pcm"]
    snapshot = pipeline.snapshot()
    assert snapshot.running
    assert snapshot.starts == 1
    assert snapshot.pcm_bytes_submitted == 3
    assert snapshot.encoded_chunks_published == 1
    assert snapshot.encoded_bytes_published == 7

    first.close()
    assert router.detach_calls == 0
    second.close()
    assert router.detach_calls == 1

    restarted = session.subscribe()
    assert router.attach_calls == 2
    assert len(encoders) == 2
    restarted.close()
    session.close()


def test_slow_lease_drops_bounded_chunks_without_affecting_fast_lease() -> None:
    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline, queue_bytes=8)
    slow = session.subscribe()
    fast = session.subscribe()

    for payload in (b"aaaa", b"bbbb", b"cccc"):
        pipeline.publish(payload)
        assert fast.get(0.1) == payload

    assert slow.get(0.1) == b"bbbb"
    assert slow.get(0.1) == b"cccc"
    slow_snapshot = slow.snapshot()
    fast_snapshot = fast.snapshot()
    assert slow_snapshot.chunks_dropped == 1
    assert slow_snapshot.bytes_dropped == 4
    assert slow_snapshot.overflows == 1
    assert fast_snapshot.chunks_dropped == 0

    slow.close()
    fast.close()
    session.close()


def test_first_byte_idle_and_maximum_duration_expiry_are_distinct() -> None:
    clock = [100.0]
    pipeline = FakePipeline()
    session = LiveAudioSession(
        pipeline,
        first_byte_timeout=5.0,
        idle_timeout=7.0,
        maximum_duration=20.0,
        clock=lambda: clock[0],
    )

    abandoned = session.subscribe()
    clock[0] = 105.0
    assert session.sweep_expired() == 1
    with pytest.raises(LiveAudioLeaseClosed) as abandoned_error:
        abandoned.get(0.1)
    assert abandoned_error.value.reason == "first_byte_timeout"

    active = session.subscribe()
    pipeline.publish(b"first")
    assert active.get(0.1) == b"first"
    clock[0] = 111.9
    assert session.sweep_expired() == 0
    clock[0] = 112.0
    assert session.sweep_expired() == 1
    assert active.snapshot().close_reason == "idle_timeout"

    maximum = session.subscribe()
    pipeline.publish(b"first")
    assert maximum.get(0.1) == b"first"
    clock[0] = 131.9
    pipeline.publish(b"keepalive")
    assert maximum.get(0.1) == b"keepalive"
    clock[0] = 132.0
    assert session.sweep_expired() == 1
    assert maximum.snapshot().close_reason == "maximum_duration"
    assert pipeline.start_calls == 3
    assert pipeline.stop_calls == 3
    session.close()


def test_failed_first_start_releases_capacity_and_allows_retry() -> None:
    pipeline = FakePipeline()
    pipeline.start_error = RuntimeError("synthetic encoder failure")
    session = LiveAudioSession(pipeline, max_leases=1)

    with pytest.raises(RuntimeError, match="synthetic encoder failure"):
        session.subscribe()

    failed = session.snapshot()
    assert failed.state is LiveAudioSessionState.FAILED
    assert failed.consumer_count == 0
    assert failed.last_error == "RuntimeError"

    pipeline.start_error = None
    lease = session.subscribe()
    assert session.snapshot().state is LiveAudioSessionState.RUNNING
    lease.close()
    session.close()


def test_capacity_timeout_validation_and_session_close() -> None:
    pipeline = FakePipeline()
    session = LiveAudioSession(pipeline, max_leases=1)
    lease = session.subscribe()

    with pytest.raises(RuntimeError, match="capacity"):
        session.subscribe()
    with pytest.raises(ValueError, match="greater than zero"):
        lease.get(0)
    with pytest.raises(queue.Empty):
        lease.get(0.001)

    session.close()
    assert lease.snapshot().close_reason == "session_closed"
    with pytest.raises(RuntimeError, match="closed"):
        session.subscribe()


@pytest.mark.parametrize(
    ("keyword", "value", "message"),
    [
        ("queue_bytes", 0, "queue size"),
        ("max_leases", 0, "maximum leases"),
        ("first_byte_timeout", 0.0, "first-byte timeout"),
        ("idle_timeout", float("inf"), "idle timeout"),
        ("maximum_duration", -1.0, "maximum duration"),
        ("sweep_interval", True, "sweep interval"),
    ],
)
def test_session_configuration_is_bounded(
    keyword: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        LiveAudioSession(FakePipeline(), **{keyword: value})  # type: ignore[arg-type]
