from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from time import monotonic
from typing import Literal, Protocol, Self

from .audio_sinks import PcmSink, PcmSinkStatistics
from .exceptions import AudioOutputError
from .remote_audio_encoder import AudioEncoderConfig, ManagedAudioEncoder

HOME_ASSISTANT_LIVE_AUDIO_MIME_TYPE = "audio/mpeg"
HOME_ASSISTANT_LIVE_AUDIO_CODEC = "MP3 (MPEG audio layer 3)"
HOME_ASSISTANT_LIVE_AUDIO_SAMPLE_RATE = 44_100
HOME_ASSISTANT_LIVE_AUDIO_CHANNELS = 1
HOME_ASSISTANT_LIVE_AUDIO_BIT_RATE = 64_000
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_QUEUE_BYTES = 128_000
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_MAX_LEASES = 4
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_FIRST_BYTE_TIMEOUT = 15.0
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_IDLE_TIMEOUT = 15.0
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_MAXIMUM_DURATION = 4 * 60 * 60.0
HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_SWEEP_INTERVAL = 1.0

LiveAudioLeaseCloseReason = Literal[
    "client_closed",
    "first_byte_timeout",
    "idle_timeout",
    "maximum_duration",
    "pipeline_failed",
    "session_closed",
]


@dataclass(frozen=True, slots=True)
class HomeAssistantLiveAudioFormat:
    """Exact non-seekable representation resolved by the media source."""

    container: str = "MP3"
    codec: str = HOME_ASSISTANT_LIVE_AUDIO_CODEC
    mime_type: str = HOME_ASSISTANT_LIVE_AUDIO_MIME_TYPE
    sample_rate: int = HOME_ASSISTANT_LIVE_AUDIO_SAMPLE_RATE
    channels: int = HOME_ASSISTANT_LIVE_AUDIO_CHANNELS
    bit_rate: int = HOME_ASSISTANT_LIVE_AUDIO_BIT_RATE
    seekable: bool = False
    duration_seconds: float | None = None


HOME_ASSISTANT_LIVE_AUDIO_FORMAT = HomeAssistantLiveAudioFormat()


def home_assistant_live_audio_encoder_config(
    *,
    executable: str = "lame",
    stop_timeout: float = 3.0,
) -> AudioEncoderConfig:
    """Build the one supported PCM-to-MP3 encoder command."""

    return AudioEncoderConfig(
        name="Home Assistant live MP3 encoder",
        command=(
            executable,
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
        ),
        stop_timeout=stop_timeout,
    )


class LiveAudioSessionState(StrEnum):
    """Lifecycle state for one shared encoded live-audio pipeline."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"
    CLOSED = "closed"


class LiveAudioPipeline(Protocol):
    """Demand-driven shared encoder/container pipeline."""

    def start(self, publish: Callable[[bytes], None]) -> None: ...

    def stop(self) -> None: ...


class _PcmSinkRouter(Protocol):
    def attach(self, sink: PcmSink) -> None: ...

    def detach(
        self,
        sink: PcmSink,
        *,
        stop: bool = True,
        raise_on_failure: bool = False,
    ) -> None: ...


class _LiveAudioEncoder(Protocol):
    def write_pcm(self, data: bytes) -> None: ...

    def read_encoded(self, size: int) -> bytes: ...

    def finalize(
        self,
        *,
        output_waiter: Callable[[float], bool] | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class LiveAudioEncoderPipelineSnapshot:
    """Low-rate, payload-free evidence for the shared encoder sink."""

    running: bool
    starts: int
    stops: int
    pcm_bytes_submitted: int
    encoded_chunks_published: int
    encoded_bytes_published: int
    last_error: str | None


class _LiveAudioEncoderSink:
    """Restartable PCM sink that drains one managed encoder output pipe."""

    def __init__(
        self,
        *,
        encoder_factory: Callable[[], _LiveAudioEncoder],
        publish: Callable[[bytes], None],
        read_size: int,
    ) -> None:
        self._encoder_factory = encoder_factory
        self._publish = publish
        self._read_size = read_size
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._encoder: _LiveAudioEncoder | None = None
        self._output_thread: threading.Thread | None = None
        self._output_done = threading.Event()
        self._running = False
        self._starts = 0
        self._stops = 0
        self._pcm_bytes_submitted = 0
        self._encoded_chunks_published = 0
        self._encoded_bytes_published = 0
        self._last_error: str | None = None

    @property
    def name(self) -> str:
        return "home-assistant-live-mp3"

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._state_lock:
            return PcmSinkStatistics(
                bytes_submitted=self._pcm_bytes_submitted,
                bytes_written=self._pcm_bytes_submitted,
            )

    def snapshot(self) -> LiveAudioEncoderPipelineSnapshot:
        with self._state_lock:
            return LiveAudioEncoderPipelineSnapshot(
                running=self._running,
                starts=self._starts,
                stops=self._stops,
                pcm_bytes_submitted=self._pcm_bytes_submitted,
                encoded_chunks_published=self._encoded_chunks_published,
                encoded_bytes_published=self._encoded_bytes_published,
                last_error=self._last_error,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._running:
                    return
                if self._encoder is not None:
                    raise RuntimeError("Live-audio encoder is still finalizing.")
                self._output_done.clear()
                self._last_error = None

            encoder = self._encoder_factory()
            thread = threading.Thread(
                target=self._drain_output,
                args=(encoder,),
                name="sds200-home-assistant-live-audio-encoder-output",
                daemon=True,
            )
            with self._state_lock:
                self._encoder = encoder
                self._output_thread = thread
                self._running = True
                self._starts += 1
            try:
                thread.start()
            except BaseException:
                with self._state_lock:
                    self._encoder = None
                    self._output_thread = None
                    self._running = False
                with suppress(Exception):
                    encoder.finalize()
                raise

    def submit_pcm(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("Live-audio PCM input must be bytes.")
        if not data:
            return
        with self._state_lock:
            encoder = self._encoder
            if not self._running or encoder is None:
                raise RuntimeError("Live-audio encoder is not running.")
        encoder.write_pcm(data)
        with self._state_lock:
            self._pcm_bytes_submitted += len(data)

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                encoder = self._encoder
                if encoder is None:
                    return
                output_thread = self._output_thread
                self._running = False

            error: BaseException | None = None
            try:
                encoder.finalize(output_waiter=self._output_done.wait)
            except BaseException as caught:
                error = caught

            if output_thread is not None and output_thread is not threading.current_thread():
                output_thread.join(timeout=0)
            with self._state_lock:
                self._encoder = None
                self._output_thread = None
                self._stops += 1
                if error is not None:
                    self._last_error = error.__class__.__name__
            if error is not None:
                raise error

    def _drain_output(self, encoder: _LiveAudioEncoder) -> None:
        try:
            while True:
                data = encoder.read_encoded(self._read_size)
                if not data:
                    return
                self._publish(data)
                with self._state_lock:
                    self._encoded_chunks_published += 1
                    self._encoded_bytes_published += len(data)
        except AudioOutputError as error:
            with self._state_lock:
                if self._running:
                    self._last_error = error.__class__.__name__
        except Exception as error:
            with self._state_lock:
                self._last_error = error.__class__.__name__
        finally:
            self._output_done.set()


class LiveAudioEncoderPipeline:
    """Attach one shared managed encoder to the existing decoded-PCM router."""

    def __init__(
        self,
        router: _PcmSinkRouter,
        *,
        encoder_factory: Callable[[], _LiveAudioEncoder] | None = None,
        read_size: int = 4096,
    ) -> None:
        self.router = router
        self.read_size = _positive_integer(read_size, "Live-audio encoder read size")
        self._encoder_factory = encoder_factory or (
            lambda: ManagedAudioEncoder(home_assistant_live_audio_encoder_config())
        )
        self._lifecycle_lock = threading.RLock()
        self._sink: _LiveAudioEncoderSink | None = None

    def start(self, publish: Callable[[bytes], None]) -> None:
        if not callable(publish):
            raise TypeError("Live-audio publisher must be callable.")
        with self._lifecycle_lock:
            if self._sink is not None:
                return
            sink = _LiveAudioEncoderSink(
                encoder_factory=self._encoder_factory,
                publish=publish,
                read_size=self.read_size,
            )
            self._sink = sink
            try:
                self.router.attach(sink)
            except BaseException:
                self._sink = None
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            sink, self._sink = self._sink, None
            if sink is None:
                return
            self.router.detach(sink, stop=True, raise_on_failure=True)

    def snapshot(self) -> LiveAudioEncoderPipelineSnapshot:
        with self._lifecycle_lock:
            sink = self._sink
            if sink is None:
                return LiveAudioEncoderPipelineSnapshot(
                    running=False,
                    starts=0,
                    stops=0,
                    pcm_bytes_submitted=0,
                    encoded_chunks_published=0,
                    encoded_bytes_published=0,
                    last_error=None,
                )
            return sink.snapshot()


class LiveAudioLeaseClosed(RuntimeError):
    """Raised when reading from a closed or expired live-audio lease."""

    def __init__(self, reason: LiveAudioLeaseCloseReason) -> None:
        self.reason = reason
        super().__init__(f"Live-audio lease is closed: {reason}.")


@dataclass(frozen=True, slots=True)
class LiveAudioLeaseSnapshot:
    """Redacted bounded-buffer and lifetime evidence for one playback."""

    lease_id: str
    closed: bool
    close_reason: LiveAudioLeaseCloseReason | None
    first_byte_delivered: bool
    queued_chunks: int
    queued_bytes: int
    chunks_delivered: int
    bytes_delivered: int
    chunks_dropped: int
    bytes_dropped: int
    overflows: int


@dataclass(frozen=True, slots=True)
class LiveAudioSessionSnapshot:
    """Redacted state for the shared live-audio representation."""

    state: LiveAudioSessionState
    consumer_count: int
    pipeline_starts: int
    pipeline_stops: int
    chunks_published: int
    bytes_published: int
    last_error: str | None
    format: HomeAssistantLiveAudioFormat


def _positive_finite(value: float, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{description} must be a number.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{description} must be finite and greater than zero.")
    return normalized


def _positive_integer(value: int, description: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{description} must be an integer.")
    if value <= 0:
        raise ValueError(f"{description} must be greater than zero.")
    return value


class LiveAudioLease:
    """One independent bounded consumer of the shared MP3 stream."""

    def __init__(
        self,
        session: LiveAudioSession,
        *,
        lease_id: str,
        created_at: float,
        queue_bytes: int,
    ) -> None:
        self._session = session
        self._lease_id = lease_id
        self._created_at = created_at
        self._queue_bytes = queue_bytes
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._closed = False
        self._close_reason: LiveAudioLeaseCloseReason | None = None
        self._first_byte_at: float | None = None
        self._last_byte_at: float | None = None
        self._chunks_delivered = 0
        self._bytes_delivered = 0
        self._chunks_dropped = 0
        self._bytes_dropped = 0
        self._overflows = 0

    @property
    def lease_id(self) -> str:
        return self._lease_id

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def get(self, timeout: float | None = None) -> bytes:
        if timeout is not None:
            _positive_finite(timeout, "Live-audio receive timeout")
        deadline = None if timeout is None else self._session.clock() + timeout

        with self._condition:
            while True:
                if self._closed:
                    assert self._close_reason is not None
                    raise LiveAudioLeaseClosed(self._close_reason)
                if self._queue:
                    data = self._queue.popleft()
                    self._queued_bytes -= len(data)
                    observed_at = self._session.clock()
                    if self._first_byte_at is None:
                        self._first_byte_at = observed_at
                    self._last_byte_at = observed_at
                    self._chunks_delivered += 1
                    self._bytes_delivered += len(data)
                    return data
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - self._session.clock()
                if remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)

    def snapshot(self) -> LiveAudioLeaseSnapshot:
        with self._condition:
            return LiveAudioLeaseSnapshot(
                lease_id=self._lease_id,
                closed=self._closed,
                close_reason=self._close_reason,
                first_byte_delivered=self._first_byte_at is not None,
                queued_chunks=len(self._queue),
                queued_bytes=self._queued_bytes,
                chunks_delivered=self._chunks_delivered,
                bytes_delivered=self._bytes_delivered,
                chunks_dropped=self._chunks_dropped,
                bytes_dropped=self._bytes_dropped,
                overflows=self._overflows,
            )

    def close(self) -> None:
        self._session._release(self, "client_closed")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.close()

    def _offer(self, data: bytes) -> None:
        with self._condition:
            if self._closed:
                return
            dropped_chunks = 0
            dropped_bytes = 0
            while self._queue and self._queued_bytes + len(data) > self._queue_bytes:
                dropped = self._queue.popleft()
                self._queued_bytes -= len(dropped)
                dropped_chunks += 1
                dropped_bytes += len(dropped)
            if len(data) > self._queue_bytes:
                dropped_chunks += 1
                dropped_bytes += len(data)
            else:
                self._queue.append(data)
                self._queued_bytes += len(data)
                self._condition.notify()
            if dropped_chunks:
                self._chunks_dropped += dropped_chunks
                self._bytes_dropped += dropped_bytes
                self._overflows += 1

    def _expiration_reason(self, now: float) -> LiveAudioLeaseCloseReason | None:
        with self._condition:
            if self._closed:
                return None
            if now - self._created_at >= self._session.maximum_duration:
                return "maximum_duration"
            if self._first_byte_at is None:
                if now - self._created_at >= self._session.first_byte_timeout:
                    return "first_byte_timeout"
                return None
            assert self._last_byte_at is not None
            if now - self._last_byte_at >= self._session.idle_timeout:
                return "idle_timeout"
            return None

    def _mark_closed(self, reason: LiveAudioLeaseCloseReason) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._closed = True
            self._close_reason = reason
            self._queue.clear()
            self._queued_bytes = 0
            self._condition.notify_all()
            return True


class LiveAudioSession:
    """Share one bounded encoder/container pipeline across playback leases."""

    def __init__(
        self,
        pipeline: LiveAudioPipeline,
        *,
        queue_bytes: int = HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_QUEUE_BYTES,
        max_leases: int = HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_MAX_LEASES,
        first_byte_timeout: float = (
            HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_FIRST_BYTE_TIMEOUT
        ),
        idle_timeout: float = HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_IDLE_TIMEOUT,
        maximum_duration: float = (
            HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_MAXIMUM_DURATION
        ),
        sweep_interval: float = HOME_ASSISTANT_LIVE_AUDIO_DEFAULT_SWEEP_INTERVAL,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.pipeline = pipeline
        self.queue_bytes = _positive_integer(queue_bytes, "Live-audio queue size")
        self.max_leases = _positive_integer(max_leases, "Live-audio maximum leases")
        self.first_byte_timeout = _positive_finite(
            first_byte_timeout,
            "Live-audio first-byte timeout",
        )
        self.idle_timeout = _positive_finite(
            idle_timeout,
            "Live-audio idle timeout",
        )
        self.maximum_duration = _positive_finite(
            maximum_duration,
            "Live-audio maximum duration",
        )
        self.sweep_interval = _positive_finite(
            sweep_interval,
            "Live-audio sweep interval",
        )
        self.clock = clock
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._state = LiveAudioSessionState.IDLE
        self._leases: set[LiveAudioLease] = set()
        self._next_lease_id = 1
        self._pipeline_starts = 0
        self._pipeline_stops = 0
        self._chunks_published = 0
        self._bytes_published = 0
        self._last_error: str | None = None
        self._stop_sweeper = threading.Event()
        self._sweeper = threading.Thread(
            target=self._sweep_loop,
            name="sds200-home-assistant-live-audio-expiry",
            daemon=True,
        )
        self._sweeper.start()

    def snapshot(self) -> LiveAudioSessionSnapshot:
        with self._state_lock:
            return LiveAudioSessionSnapshot(
                state=self._state,
                consumer_count=len(self._leases),
                pipeline_starts=self._pipeline_starts,
                pipeline_stops=self._pipeline_stops,
                chunks_published=self._chunks_published,
                bytes_published=self._bytes_published,
                last_error=self._last_error,
                format=HOME_ASSISTANT_LIVE_AUDIO_FORMAT,
            )

    def subscribe(self) -> LiveAudioLease:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._state is LiveAudioSessionState.CLOSED:
                    raise RuntimeError("Live-audio session is closed.")
                if len(self._leases) >= self.max_leases:
                    raise RuntimeError("Live-audio playback capacity is exhausted.")
                lease = LiveAudioLease(
                    self,
                    lease_id=f"live-{self._next_lease_id}",
                    created_at=self.clock(),
                    queue_bytes=self.queue_bytes,
                )
                self._next_lease_id += 1
                first_consumer = not self._leases
                self._leases.add(lease)
                if not first_consumer:
                    return lease
                self._state = LiveAudioSessionState.STARTING

            try:
                self.pipeline.start(self.publish)
            except BaseException as error:
                with self._state_lock:
                    self._leases.discard(lease)
                    lease._mark_closed("pipeline_failed")
                    self._state = LiveAudioSessionState.FAILED
                    self._last_error = error.__class__.__name__
                raise

            with self._state_lock:
                self._pipeline_starts += 1
                self._last_error = None
                self._state = LiveAudioSessionState.RUNNING
            return lease

    def publish(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("Live-audio encoded output must be bytes.")
        if not data:
            return
        with self._state_lock:
            if self._state not in {
                LiveAudioSessionState.STARTING,
                LiveAudioSessionState.RUNNING,
            }:
                return
            leases = tuple(self._leases)
            self._chunks_published += 1
            self._bytes_published += len(data)
        for lease in leases:
            lease._offer(data)

    def sweep_expired(self) -> int:
        now = self.clock()
        with self._state_lock:
            expired = tuple(
                (lease, reason)
                for lease in self._leases
                if (reason := lease._expiration_reason(now)) is not None
            )
        for lease, reason in expired:
            self._release(lease, reason)
        return len(expired)

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._state is LiveAudioSessionState.CLOSED:
                    return
                leases = tuple(self._leases)
                self._leases.clear()
                running = self._state in {
                    LiveAudioSessionState.STARTING,
                    LiveAudioSessionState.RUNNING,
                    LiveAudioSessionState.FAILED,
                }
                self._state = LiveAudioSessionState.STOPPING if running else self._state
                for lease in leases:
                    lease._mark_closed("session_closed")
            if running:
                self._stop_pipeline()
            with self._state_lock:
                self._state = LiveAudioSessionState.CLOSED
            self._stop_sweeper.set()
        if self._sweeper is not threading.current_thread():
            self._sweeper.join(timeout=max(1.0, self.sweep_interval * 2))

    def _release(
        self,
        lease: LiveAudioLease,
        reason: LiveAudioLeaseCloseReason,
    ) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if lease not in self._leases:
                    lease._mark_closed(reason)
                    return
                self._leases.remove(lease)
                lease._mark_closed(reason)
                if self._leases or self._state is LiveAudioSessionState.CLOSED:
                    return
                should_stop = self._state in {
                    LiveAudioSessionState.STARTING,
                    LiveAudioSessionState.RUNNING,
                    LiveAudioSessionState.FAILED,
                }
                if should_stop:
                    self._state = LiveAudioSessionState.STOPPING
            if should_stop:
                self._stop_pipeline()
            with self._state_lock:
                if self._state is not LiveAudioSessionState.CLOSED:
                    self._state = LiveAudioSessionState.IDLE

    def _stop_pipeline(self) -> None:
        try:
            self.pipeline.stop()
        except BaseException as error:
            with self._state_lock:
                self._last_error = error.__class__.__name__
                self._state = LiveAudioSessionState.FAILED
            raise
        with self._state_lock:
            self._pipeline_stops += 1

    def _sweep_loop(self) -> None:
        while not self._stop_sweeper.wait(self.sweep_interval):
            try:
                self.sweep_expired()
            except Exception:
                continue
