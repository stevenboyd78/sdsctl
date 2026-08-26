from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib import import_module
from math import isfinite
from time import monotonic
from typing import Literal, Protocol, Self, cast, runtime_checkable

from .audio import AudioChunk, AudioStream
from .audio_recording import (
    PCM_CHANNELS,
    PCM_SAMPLE_WIDTH,
    PCMU_SAMPLE_RATE,
    PcmuWavRecorder,
    decode_mulaw,
)
from .events import EventBus
from .exceptions import AudioOutputError
from .reliability import ReconnectPolicy

logger = logging.getLogger(__name__)
_PCM_BYTES_PER_SECOND = PCMU_SAMPLE_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH
_DEFAULT_DISPATCH_BUFFER_SECONDS = 1.0
_DEFAULT_DISPATCH_STOP_TIMEOUT = 6.0
_DEFAULT_FANOUT_STOP_TIMEOUT = 10.0
_DEFAULT_DISPATCH_RETRY_POLICY = ReconnectPolicy(
    initial_delay=0.25,
    multiplier=2.0,
    max_delay=5.0,
)


def _require_positive_finite(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number greater than zero")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be a finite number greater than zero")
    return normalized


def _validate_retry_policy(policy: ReconnectPolicy) -> ReconnectPolicy:
    _require_positive_finite(
        policy.initial_delay,
        "PCM dispatch retry initial delay",
    )
    _require_positive_finite(
        policy.multiplier,
        "PCM dispatch retry multiplier",
    )
    _require_positive_finite(
        policy.max_delay,
        "PCM dispatch retry maximum delay",
    )
    return policy


@dataclass(frozen=True, slots=True)
class PcmSinkStatistics:
    """Immutable counters for one decoded-PCM destination."""

    bytes_submitted: int = 0
    bytes_written: int = 0
    bytes_dropped: int = 0
    queued_bytes: int = 0
    underflows: int = 0
    overflows: int = 0
    callback_statuses: int = 0

    @property
    def queued_seconds(self) -> float:
        return self.queued_bytes / _PCM_BYTES_PER_SECOND


@runtime_checkable
class PcmSink(Protocol):
    """Nonblocking destination for 8 kHz mono signed 16-bit PCM."""

    @property
    def name(self) -> str: ...

    @property
    def running(self) -> bool: ...

    @property
    def statistics(self) -> PcmSinkStatistics: ...

    def start(self) -> None: ...

    def submit_pcm(self, data: bytes) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class MuteablePcmSink(PcmSink, Protocol):
    """PCM sink that can stay prepared while intentional silence is emitted."""

    @property
    def muted(self) -> bool: ...

    def set_muted(self, muted: bool) -> None: ...


class _PcmDispatchWorker:
    """Bounded single-owner delivery and finalization for one PCM sink."""

    def __init__(
        self,
        sink: PcmSink,
        *,
        capacity_bytes: int,
        retry_policy: ReconnectPolicy,
        clock: Callable[[], float],
        on_submission: Callable[
            [_PcmDispatchWorker, BaseException | None],
            None,
        ] | None = None,
        on_finalized: Callable[
            [_PcmDispatchWorker, BaseException | None],
            None,
        ] | None = None,
        notify: Callable[[], None] | None = None,
    ) -> None:
        self.sink = sink
        self.name = sink.name
        self._capacity_bytes = max(
            PCM_SAMPLE_WIDTH,
            capacity_bytes - capacity_bytes % PCM_SAMPLE_WIDTH,
        )
        self._retry_policy = retry_policy
        self._clock = clock
        self._on_submission = on_submission
        self._on_finalized = on_finalized
        self._notify = notify
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._finalizing = False
        self._drain = True
        self._done = threading.Event()
        self._finalization_error: BaseException | None = None
        self._timeout_reported = False
        self._retry_attempt = 0
        self._retry_delay: float | None = None
        self._retry_deadline: float | None = None
        self._retry_exhausted = False
        self._in_submission = False
        self._bytes_submitted = 0
        self._bytes_dropped = 0
        self._overflows = 0

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def finalizing(self) -> bool:
        with self._condition:
            return self._finalizing

    @property
    def finalization_error(self) -> BaseException | None:
        with self._condition:
            return self._finalization_error

    @property
    def owns_current_thread(self) -> bool:
        return self._thread is threading.current_thread()

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._condition:
            bytes_submitted = self._bytes_submitted
            bytes_dropped = self._bytes_dropped
            queued_bytes = self._queued_bytes
            overflows = self._overflows
        delegate = _safe_sink_statistics(self.sink)
        return PcmSinkStatistics(
            bytes_submitted=bytes_submitted,
            bytes_written=delegate.bytes_written,
            bytes_dropped=bytes_dropped + delegate.bytes_dropped,
            queued_bytes=queued_bytes + delegate.queued_bytes,
            underflows=delegate.underflows,
            overflows=overflows + delegate.overflows,
            callback_statuses=delegate.callback_statuses,
        )

    @property
    def dispatch_statistics(self) -> PcmSinkStatistics:
        """Return queue telemetry without invoking arbitrary sink code."""

        with self._condition:
            return PcmSinkStatistics(
                bytes_submitted=self._bytes_submitted,
                bytes_dropped=self._bytes_dropped,
                queued_bytes=self._queued_bytes,
                overflows=self._overflows,
            )

    def start(self, *, accepting: bool = True) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("PCM dispatch worker was already started")
            self._accepting = accepting
            thread = threading.Thread(
                target=self._run,
                name=f"sds200-pcm-dispatch:{self.name}",
                daemon=True,
            )
            self._thread = thread
        try:
            thread.start()
        except BaseException:
            with self._condition:
                self._accepting = False
                self._thread = None
            raise

    def offer(self, data: bytes) -> bool:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples")
        if not data:
            return True
        with self._condition:
            self._bytes_submitted += len(data)
            if not self._accepting or self._finalizing or self._done.is_set():
                self._bytes_dropped += len(data)
                return False

            dropped = 0
            if self._retry_attempt or self._retry_exhausted:
                dropped += self._discard_queue_locked()
            dropped += self._append_bounded_locked(data)
            if dropped:
                self._bytes_dropped += dropped
                self._overflows += 1
            self._condition.notify()
            return True

    def account_nonblocking_offer(self, data: bytes) -> bool:
        """Account work delivered directly to a known nonblocking router."""

        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples")
        if not data:
            return True
        with self._condition:
            self._bytes_submitted += len(data)
            if not self._accepting or self._finalizing or self._done.is_set():
                self._bytes_dropped += len(data)
                return False
            return True

    def pause(self) -> None:
        with self._condition:
            if self._finalizing or self._done.is_set():
                return
            self._accepting = False
            self._bytes_dropped += self._discard_queue_locked()
            self._condition.notify_all()

    def resume(self) -> None:
        with self._condition:
            if self._finalizing or self._done.is_set():
                raise RuntimeError("PCM dispatch worker is finalizing")
            self._accepting = True
            self._condition.notify_all()

    def request_finalize(self, *, drain: bool = True) -> None:
        with self._condition:
            if self._done.is_set():
                return
            self._accepting = False
            self._finalizing = True
            self._drain = self._drain and drain
            if not self._drain or self._retry_attempt or self._retry_exhausted:
                self._bytes_dropped += self._discard_queue_locked()
            self._condition.notify_all()

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout=max(0.0, timeout))

    def wait_idle(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._in_submission,
                timeout=max(0.0, timeout),
            )

    def report_timeout_once(self) -> bool:
        with self._condition:
            if self._timeout_reported:
                return False
            self._timeout_reported = True
            return True

    def _append_bounded_locked(self, data: bytes) -> int:
        dropped = 0
        if len(data) > self._capacity_bytes:
            dropped += len(data) - self._capacity_bytes
            data = data[-self._capacity_bytes :]

        overflow = max(
            0,
            self._queued_bytes + len(data) - self._capacity_bytes,
        )
        while overflow and self._queue:
            oldest = self._queue[0]
            if len(oldest) <= overflow:
                self._queue.popleft()
                self._queued_bytes -= len(oldest)
                overflow -= len(oldest)
                dropped += len(oldest)
                continue
            self._queue[0] = oldest[overflow:]
            self._queued_bytes -= overflow
            dropped += overflow
            overflow = 0

        self._queue.append(data)
        self._queued_bytes += len(data)
        return dropped

    def _discard_queue_locked(self) -> int:
        dropped = self._queued_bytes
        self._queue.clear()
        self._queued_bytes = 0
        return dropped

    def _next_data(self) -> bytes | None:
        with self._condition:
            while True:
                quarantined = bool(
                    self._retry_attempt or self._retry_exhausted
                )
                if self._finalizing and (not self._drain or quarantined):
                    self._bytes_dropped += self._discard_queue_locked()
                    return None

                if self._queue and not self._retry_exhausted:
                    if self._retry_deadline is not None:
                        remaining = self._retry_deadline - self._clock()
                        if remaining > 0:
                            self._condition.wait(timeout=remaining)
                            continue
                    data = self._queue.popleft()
                    self._queued_bytes -= len(data)
                    self._in_submission = True
                    return data

                if self._finalizing:
                    return None
                self._condition.wait()

    def _record_submission_failure(
        self,
        data: bytes,
        error: BaseException,
    ) -> None:
        with self._condition:
            self._bytes_dropped += len(data) + self._discard_queue_locked()
            self._retry_attempt += 1
            if self._retry_policy.allows(self._retry_attempt):
                self._retry_delay = (
                    self._retry_policy.initial_delay
                    if self._retry_delay is None
                    else min(
                        self._retry_delay * self._retry_policy.multiplier,
                        self._retry_policy.max_delay,
                    )
                )
                self._retry_deadline = (
                    self._clock()
                    + self._retry_delay
                )
                self._retry_exhausted = False
            else:
                self._retry_deadline = None
                self._retry_exhausted = True
            self._condition.notify_all()
        logger.warning(
            "PCM dispatch submission failed sink=%s error=%s",
            self.name,
            error.__class__.__name__,
        )

    def _record_submission_success(self) -> None:
        with self._condition:
            self._retry_attempt = 0
            self._retry_delay = None
            self._retry_deadline = None
            self._retry_exhausted = False

    def _mark_submission_finished(self) -> None:
        with self._condition:
            self._in_submission = False
            self._condition.notify_all()

    def _invoke_submission_callback(
        self,
        error: BaseException | None,
    ) -> None:
        if self._on_submission is not None:
            try:
                self._on_submission(self, error)
            except BaseException as error:
                logger.exception(
                    "PCM dispatch internal submission callback failed sink=%s",
                    self.name,
                )

    def _invoke_notify(self) -> None:
        if self._notify is None:
            return
        try:
            self._notify()
        except BaseException:
            logger.exception(
                "PCM dispatch transition notification failed sink=%s",
                self.name,
            )

    def _run(self) -> None:
        worker_error: BaseException | None = None
        try:
            while True:
                data = self._next_data()
                if data is None:
                    break
                try:
                    self.sink.submit_pcm(data)
                except BaseException as error:
                    try:
                        self._record_submission_failure(data, error)
                        self._invoke_submission_callback(error)
                    finally:
                        self._mark_submission_finished()
                    self._invoke_notify()
                else:
                    try:
                        self._record_submission_success()
                        self._invoke_submission_callback(None)
                    finally:
                        self._mark_submission_finished()
                    self._invoke_notify()
        except BaseException as error:
            worker_error = error
            logger.warning(
                "PCM dispatch worker failed sink=%s error=%s",
                self.name,
                error.__class__.__name__,
            )
        finally:
            finalization_error = worker_error
            try:
                if type(self.sink) is PcmSinkRouter:
                    self.sink.stop(raise_on_failure=True)
                else:
                    self.sink.stop()
            except BaseException as error:
                if finalization_error is None:
                    finalization_error = error
                logger.warning(
                    "PCM dispatch finalization failed sink=%s error=%s",
                    self.name,
                    error.__class__.__name__,
                )

            if self._on_finalized is not None:
                try:
                    self._on_finalized(self, finalization_error)
                except BaseException:
                    logger.exception(
                        "PCM dispatch internal finalization callback failed "
                        "sink=%s",
                        self.name,
                    )

            with self._condition:
                self._accepting = False
                self._finalizing = True
                self._in_submission = False
                self._finalization_error = finalization_error
                self._done.set()
                self._condition.notify_all()
            self._invoke_notify()


@dataclass(frozen=True, slots=True)
class AudioFanoutSnapshot:
    """Current state of one transport-independent PCM fanout session."""

    endpoint: str
    running: bool
    packets: int
    samples: int
    sinks: tuple[tuple[str, PcmSinkStatistics], ...]

    @property
    def audio_duration_seconds(self) -> float:
        return self.samples / PCMU_SAMPLE_RATE


class AudioFanoutSession:
    """Decode PCMU once and enqueue it independently for every PCM sink."""

    def __init__(
        self,
        stream: AudioStream,
        sinks: Iterable[PcmSink],
        *,
        buffer_seconds: float = _DEFAULT_DISPATCH_BUFFER_SECONDS,
        stop_timeout: float = _DEFAULT_FANOUT_STOP_TIMEOUT,
        retry_policy: ReconnectPolicy | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.stream = stream
        self.sinks = tuple(sinks)
        if not self.sinks:
            raise ValueError("Audio fanout requires at least one PCM sink")
        self._sink_names = tuple(sink.name for sink in self.sinks)
        self.buffer_seconds = _require_positive_finite(
            buffer_seconds,
            "Audio fanout buffer",
        )
        self.stop_timeout = _require_positive_finite(
            stop_timeout,
            "Audio fanout stop timeout",
        )
        self.retry_policy = _validate_retry_policy(
            retry_policy or _DEFAULT_DISPATCH_RETRY_POLICY
        )
        self._clock = clock
        self._capacity_bytes = max(
            PCM_SAMPLE_WIDTH,
            int(_PCM_BYTES_PER_SECOND * self.buffer_seconds),
        )
        self.events = EventBus()
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._unsubscribe: Callable[[], None] | None = None
        self._dispatchers: tuple[_PcmDispatchWorker, ...] = ()
        self._started = False
        self._stopped = False
        self._packets = 0
        self._samples = 0

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._started and not self._stopped and self.stream.running

    def on_state(
        self,
        callback: Callable[[AudioFanoutSnapshot], None],
    ) -> Callable[[], None]:
        """Subscribe to completed audio fanout lifecycle changes."""

        return self.events.subscribe("state", callback)

    def snapshot(self) -> AudioFanoutSnapshot:
        """Return enriched sink telemetry for explicit inspection."""

        return self._snapshot(enrich_sinks=True)

    def lifecycle_snapshot(self) -> AudioFanoutSnapshot:
        """Return cached telemetry without invoking arbitrary sink code."""

        return self._snapshot(enrich_sinks=False)

    def _snapshot(self, *, enrich_sinks: bool) -> AudioFanoutSnapshot:
        with self._state_lock:
            packets = self._packets
            samples = self._samples
            dispatchers = self._dispatchers
        if dispatchers:
            sink_statistics = tuple(
                (
                    dispatcher.name,
                    (
                        dispatcher.statistics
                        if enrich_sinks
                        else dispatcher.dispatch_statistics
                    ),
                )
                for dispatcher in dispatchers
            )
        elif enrich_sinks:
            sink_statistics = tuple(
                (name, _safe_sink_statistics(sink))
                for name, sink in zip(self._sink_names, self.sinks, strict=True)
            )
        else:
            sink_statistics = tuple(
                (name, PcmSinkStatistics())
                for name in self._sink_names
            )
        return AudioFanoutSnapshot(
            endpoint=self.stream.endpoint,
            running=self.running,
            packets=packets,
            samples=samples,
            sinks=sink_statistics,
        )

    def start(self) -> None:
        caught: BaseException | None = None
        with self._lifecycle_lock:
            with self._state_lock:
                if self._started:
                    raise RuntimeError("Audio fanout sessions can only be started once.")
                self._started = True

            dispatchers: list[_PcmDispatchWorker] = []
            unsubscribe: Callable[[], None] | None = None
            try:
                for sink in self.sinks:
                    sink.start()
                    dispatcher = _PcmDispatchWorker(
                        sink,
                        capacity_bytes=self._capacity_bytes,
                        retry_policy=self.retry_policy,
                        clock=self._clock,
                    )
                    try:
                        dispatcher.start()
                    except BaseException:
                        try:
                            sink.stop()
                        except Exception:
                            logger.exception(
                                "Audio sink cleanup failed after dispatch "
                                "thread start error sink=%s",
                                sink.name,
                            )
                        raise
                    dispatchers.append(dispatcher)
                with self._state_lock:
                    self._dispatchers = tuple(dispatchers)
                unsubscribe = self.stream.on_chunk(self._receive_chunk)
                self.stream.start()
            except BaseException as error:
                if unsubscribe is not None:
                    unsubscribe()
                try:
                    self.stream.stop()
                except Exception:
                    logger.exception("Audio stream cleanup failed after start error")
                self._finalize_dispatchers(tuple(reversed(dispatchers)))
                with self._state_lock:
                    self._stopped = True
                caught = error
            else:
                with self._state_lock:
                    self._unsubscribe = unsubscribe

            snapshot = self.lifecycle_snapshot()

        self.events.emit("state", snapshot)
        if caught is not None:
            raise caught
        logger.info(
            "audio fanout started endpoint=%s sinks=%s",
            self.stream.endpoint,
            ",".join(self._sink_names),
        )

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if not self._started or self._stopped:
                    return
                self._stopped = True
                unsubscribe, self._unsubscribe = self._unsubscribe, None

            failures: list[BaseException] = []
            try:
                self.stream.stop()
            except BaseException as error:
                failures.append(error)
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except BaseException as error:
                    failures.append(error)
            failures.extend(
                self._finalize_dispatchers(tuple(reversed(self._dispatchers)))
            )

            snapshot = self.lifecycle_snapshot()

        self.events.emit("state", snapshot)
        logger.info(
            "audio fanout stopped endpoint=%s packets=%d samples=%d",
            snapshot.endpoint,
            snapshot.packets,
            snapshot.samples,
        )
        if failures:
            raise failures[0]

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _receive_chunk(self, chunk: AudioChunk) -> None:
        if not chunk.data:
            return
        pcm = decode_mulaw(chunk.data)
        with self._state_lock:
            self._packets += 1
            self._samples += len(chunk.data)
            dispatchers = self._dispatchers
        for dispatcher in dispatchers:
            sink = dispatcher.sink
            if type(sink) is PcmSinkRouter:
                if dispatcher.account_nonblocking_offer(pcm):
                    sink.submit_pcm(pcm)
            else:
                dispatcher.offer(pcm)

    def _finalize_dispatchers(
        self,
        dispatchers: tuple[_PcmDispatchWorker, ...],
    ) -> list[BaseException]:
        for dispatcher in dispatchers:
            dispatcher.request_finalize()

        deadline = monotonic() + self.stop_timeout
        failures: list[BaseException] = []
        for dispatcher in dispatchers:
            remaining = max(0.0, deadline - monotonic())
            if not dispatcher.wait(remaining):
                failures.append(
                    AudioOutputError(
                        "Timed out while finalizing PCM sink "
                        f"{dispatcher.name}"
                    )
                )
                continue
            error = dispatcher.finalization_error
            if error is not None:
                failures.append(error)
        return failures

PcmSubscriberState = Literal[
    "detached",
    "attached",
    "starting",
    "active",
    "stopping",
    "failed",
]

PcmSubscriberHealth = Literal[
    "inactive",
    "healthy",
    "degraded",
    "failed",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "PCM subscriber wall clock must return a timezone-aware datetime."
        )
    return value


def _subscriber_health(state: PcmSubscriberState) -> PcmSubscriberHealth:
    if state == "active":
        return "healthy"
    if state in {"starting", "stopping"}:
        return "degraded"
    if state == "failed":
        return "failed"
    return "inactive"


def _statistics_as_dict(
    statistics: PcmSinkStatistics,
) -> dict[str, int]:
    return {
        "bytes_submitted": statistics.bytes_submitted,
        "bytes_written": statistics.bytes_written,
        "bytes_dropped": statistics.bytes_dropped,
        "queued_bytes": statistics.queued_bytes,
        "underflows": statistics.underflows,
        "overflows": statistics.overflows,
        "callback_statuses": statistics.callback_statuses,
    }


def _safe_sink_running(sink: PcmSink) -> bool:
    try:
        return sink.running
    except Exception:
        return False


def _safe_sink_statistics(sink: PcmSink) -> PcmSinkStatistics:
    try:
        return sink.statistics
    except Exception:
        return PcmSinkStatistics()


def _redacted_error_type(error: BaseException) -> str:
    return error.__class__.__name__


@dataclass(frozen=True, slots=True)
class PcmSubscriberSnapshot:
    """Immutable health and metrics for one router subscriber."""

    subscriber_id: str
    name: str
    state: PcmSubscriberState
    health: PcmSubscriberHealth
    attached: bool
    running: bool
    statistics: PcmSinkStatistics
    start_attempts: int
    submissions: int
    successful_submissions: int
    failures: int
    start_failures: int
    submit_failures: int
    stop_failures: int
    transition_sequence: int
    state_changed_at: datetime
    last_started_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "subscriber_id": self.subscriber_id,
            "name": self.name,
            "state": self.state,
            "health": self.health,
            "attached": self.attached,
            "running": self.running,
            "statistics": _statistics_as_dict(self.statistics),
            "start_attempts": self.start_attempts,
            "submissions": self.submissions,
            "successful_submissions": self.successful_submissions,
            "failures": self.failures,
            "start_failures": self.start_failures,
            "submit_failures": self.submit_failures,
            "stop_failures": self.stop_failures,
            "transition_sequence": self.transition_sequence,
            "state_changed_at": self.state_changed_at.isoformat(),
            "last_started_at": (
                self.last_started_at.isoformat()
                if self.last_started_at is not None
                else None
            ),
            "last_failure_at": (
                self.last_failure_at.isoformat()
                if self.last_failure_at is not None
                else None
            ),
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class PcmSubscriberTransition:
    """One ordered immutable subscriber lifecycle state change."""

    sequence: int
    observed_at: datetime
    previous_state: PcmSubscriberState
    state: PcmSubscriberState
    previous_health: PcmSubscriberHealth
    health: PcmSubscriberHealth
    snapshot: PcmSubscriberSnapshot

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "observed_at": self.observed_at.isoformat(),
            "previous_state": self.previous_state,
            "state": self.state,
            "previous_health": self.previous_health,
            "health": self.health,
            "snapshot": self.snapshot.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class PcmSinkRouterSnapshot:
    """Immutable state for one dynamic PCM subscriber router."""

    name: str
    running: bool
    statistics: PcmSinkStatistics
    subscribers: tuple[PcmSubscriberSnapshot, ...]
    transition_sequence: int

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "running": self.running,
            "statistics": _statistics_as_dict(self.statistics),
            "subscribers": [
                subscriber.as_dict()
                for subscriber in self.subscribers
            ],
            "transition_sequence": self.transition_sequence,
        }


@dataclass(slots=True)
class _PcmSubscriberRecord:
    sink: PcmSink
    subscriber_id: str
    name: str
    state: PcmSubscriberState
    attached: bool
    start_attempts: int
    submissions: int
    successful_submissions: int
    failures: int
    start_failures: int
    submit_failures: int
    stop_failures: int
    transition_sequence: int
    state_changed_at: datetime
    last_started_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    dispatcher: _PcmDispatchWorker | None


class PcmSinkRouter:
    """Route PCM through bounded, independently owned subscriber workers."""

    def __init__(
        self,
        *,
        name: str = "pcm-sink-router",
        now: Callable[[], datetime] = _utc_now,
        buffer_seconds: float = _DEFAULT_DISPATCH_BUFFER_SECONDS,
        stop_timeout: float = _DEFAULT_DISPATCH_STOP_TIMEOUT,
        retry_policy: ReconnectPolicy | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not name or name.strip() != name:
            raise ValueError("PCM sink router name must not be empty or padded")
        _require_aware_datetime(now())

        self._name = name
        self._now = now
        self.buffer_seconds = _require_positive_finite(
            buffer_seconds,
            "PCM sink router buffer",
        )
        self.stop_timeout = _require_positive_finite(
            stop_timeout,
            "PCM sink router stop timeout",
        )
        self.retry_policy = _validate_retry_policy(
            retry_policy or _DEFAULT_DISPATCH_RETRY_POLICY
        )
        self._clock = clock
        self._capacity_bytes = max(
            PCM_SAMPLE_WIDTH,
            int(_PCM_BYTES_PER_SECOND * self.buffer_seconds),
        )
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._routing_lock = threading.Lock()
        self._records: list[_PcmSubscriberRecord] = []
        self._running = False
        self._routing_running = False
        self._active_dispatchers: tuple[_PcmDispatchWorker, ...] = ()
        self._bytes_submitted = 0
        self._next_subscriber_id = 1
        self._transition_sequence = 0
        self._pending_transitions: deque[PcmSubscriberTransition] = deque()
        self._emitting_transitions = False
        self.events = EventBus()

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def statistics(self) -> PcmSinkStatistics:
        return self.snapshot().statistics

    def on_transition(
        self,
        callback: Callable[[PcmSubscriberTransition], None],
    ) -> Callable[[], None]:
        """Subscribe to ordered immutable subscriber state changes."""

        return self.events.subscribe("transition", callback)

    def snapshot(self) -> PcmSinkRouterSnapshot:
        """Return enriched subscriber telemetry for explicit inspection."""

        with self._state_lock:
            running = self._running
            transition_sequence = self._transition_sequence
            captured = tuple(
                (
                    self._snapshot_record_locked(record),
                    record.sink,
                    record.dispatcher,
                )
                for record in self._records
            )
        with self._routing_lock:
            bytes_submitted = self._bytes_submitted
        subscribers = tuple(
            self._enrich_snapshot(snapshot, sink, dispatcher)
            for snapshot, sink, dispatcher in captured
        )
        return PcmSinkRouterSnapshot(
            name=self.name,
            running=running,
            statistics=self._aggregate_statistics(
                bytes_submitted,
                subscribers,
            ),
            subscribers=subscribers,
            transition_sequence=transition_sequence,
        )

    def lifecycle_snapshot(self) -> PcmSinkRouterSnapshot:
        """Return cached telemetry without invoking arbitrary sink code."""

        with self._state_lock:
            running = self._running
            transition_sequence = self._transition_sequence
            subscribers = tuple(
                self._snapshot_record_locked(record)
                for record in self._records
            )
        with self._routing_lock:
            bytes_submitted = self._bytes_submitted
        return PcmSinkRouterSnapshot(
            name=self.name,
            running=running,
            statistics=self._aggregate_statistics(
                bytes_submitted,
                subscribers,
            ),
            subscribers=subscribers,
            transition_sequence=transition_sequence,
        )

    def subscriber_snapshot(
        self,
        sink: PcmSink,
    ) -> PcmSubscriberSnapshot | None:
        with self._state_lock:
            record = self._find_record_locked(sink)
            if record is None:
                return None
            snapshot = self._snapshot_record_locked(record)
            dispatcher = record.dispatcher
        return self._enrich_snapshot(snapshot, sink, dispatcher)

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._running:
                    return
                self._running = True
                self._refresh_routing_locked()
                records = tuple(
                    record for record in self._records if record.attached
                )

            for record in records:
                self._start_subscriber(record)

        self._emit_pending_transitions()

    def attach(self, sink: PcmSink) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                record = self._find_record_locked(sink)
                if record is None:
                    timestamp = _require_aware_datetime(self._now())
                    record = _PcmSubscriberRecord(
                        sink=sink,
                        subscriber_id=(
                            f"{sink.name}:{self._next_subscriber_id}"
                        ),
                        name=sink.name,
                        state="detached",
                        attached=False,
                        start_attempts=0,
                        submissions=0,
                        successful_submissions=0,
                        failures=0,
                        start_failures=0,
                        submit_failures=0,
                        stop_failures=0,
                        transition_sequence=0,
                        state_changed_at=timestamp,
                        last_started_at=None,
                        last_failure_at=None,
                        last_error=None,
                        dispatcher=None,
                    )
                    self._next_subscriber_id += 1
                    self._records.append(record)

                if record.attached:
                    return

                dispatcher = record.dispatcher
                if (
                    dispatcher is not None
                    and dispatcher.finalizing
                    and not dispatcher.done
                ):
                    raise AudioOutputError(
                        f"PCM sink {record.name} is still finalizing"
                    )

                record.attached = True
                self._transition_locked(record, "attached")
                running = self._running
                if not running:
                    self._refresh_routing_locked()

            failure = self._start_subscriber(record) if running else None

        self._emit_pending_transitions()
        if failure is not None:
            raise failure

    def detach(
        self,
        sink: PcmSink,
        *,
        stop: bool = True,
        raise_on_failure: bool = False,
    ) -> None:
        failure: BaseException | None = None
        warm_dispatcher: _PcmDispatchWorker | None = None
        with self._lifecycle_lock:
            with self._state_lock:
                record = self._find_record_locked(sink)
                if record is None:
                    return
                dispatcher = record.dispatcher
                if not stop:
                    if not record.attached:
                        return
                    record.attached = False
                    self._refresh_routing_locked()
                    if dispatcher is not None:
                        dispatcher.pause()
                    self._transition_locked(record, "detached")
                    warm_dispatcher = dispatcher
                    dispatcher = None
                else:
                    if (
                        not record.attached
                        and dispatcher is None
                    ):
                        return
                    record.attached = False
                    self._refresh_routing_locked()
                    if dispatcher is None or (
                        dispatcher.done
                        and dispatcher.finalization_error is not None
                    ):
                        dispatcher = self._new_dispatcher(record)
                        record.dispatcher = dispatcher
                        self._transition_locked(record, "stopping")
                        try:
                            dispatcher.start(accepting=False)
                        except BaseException as error:
                            record.dispatcher = None
                            self._record_stop_failure_locked(record, error)
                            dispatcher = None
                            failure = error
                    elif dispatcher.done:
                        self._transition_locked(record, "detached")
                        dispatcher = None
                    else:
                        self._transition_locked(record, "stopping")

            if (
                warm_dispatcher is not None
                and not warm_dispatcher.owns_current_thread
                and not warm_dispatcher.wait_idle(self.stop_timeout)
            ):
                failure = AudioOutputError(
                    f"Timed out while pausing PCM sink {record.name}"
                )
                with self._state_lock:
                    record.attached = True
                    warm_dispatcher.resume()
                    self._transition_locked(record, "active")
                    self._refresh_routing_locked()

            if stop and dispatcher is not None:
                dispatcher.request_finalize()
                if not dispatcher.owns_current_thread:
                    failure = self._wait_for_dispatcher(
                        record,
                        dispatcher,
                        monotonic() + self.stop_timeout,
                    )

        self._emit_pending_transitions()
        if failure is not None and (raise_on_failure or not stop):
            raise failure

    def submit_pcm(self, data: bytes) -> None:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples")
        if not data:
            return
        with self._routing_lock:
            if not self._routing_running:
                return
            dispatchers = self._active_dispatchers
            self._bytes_submitted += len(data)
            for dispatcher in dispatchers:
                dispatcher.offer(data)

    def stop(self, *, raise_on_failure: bool = False) -> None:
        failure: BaseException | None = None
        with self._lifecycle_lock:
            deadline = monotonic() + self.stop_timeout
            with self._state_lock:
                dispatchers = tuple(
                    (record, record.dispatcher)
                    for record in reversed(self._records)
                    if record.dispatcher is not None
                    and not record.dispatcher.done
                )
                if not self._running and not dispatchers:
                    return
                self._running = False
                for record, _ in dispatchers:
                    record.attached = False
                self._refresh_routing_locked()
                for record, _ in dispatchers:
                    self._transition_locked(record, "stopping")

            for _, dispatcher in dispatchers:
                assert dispatcher is not None
                dispatcher.request_finalize()

            for record, dispatcher in dispatchers:
                assert dispatcher is not None
                if dispatcher.owns_current_thread:
                    continue
                dispatch_failure = self._wait_for_dispatcher(
                    record,
                    dispatcher,
                    deadline,
                )
                if failure is None and dispatch_failure is not None:
                    failure = dispatch_failure

        self._emit_pending_transitions()
        if raise_on_failure and failure is not None:
            raise failure

    def _start_subscriber(
        self,
        record: _PcmSubscriberRecord,
    ) -> BaseException | None:
        with self._state_lock:
            dispatcher = record.dispatcher
            if (
                dispatcher is not None
                and not dispatcher.done
                and not dispatcher.finalizing
            ):
                try:
                    dispatcher.resume()
                except Exception as error:
                    return self._record_start_failure_locked(record, error)
                self._transition_locked(record, "active")
                self._refresh_routing_locked()
                return None
            if dispatcher is not None and dispatcher.done:
                record.dispatcher = None
            record.start_attempts += 1
            self._transition_locked(record, "starting")

        try:
            record.sink.start()
        except Exception as error:
            with self._state_lock:
                self._record_start_failure_locked(record, error)
            logger.warning(
                "PCM sink router subscriber failed to start sink=%s error=%s",
                record.name,
                error.__class__.__name__,
            )

            # A failed start may still have opened partial audio resources.
            try:
                record.sink.stop()
            except Exception as cleanup_error:
                with self._state_lock:
                    self._record_stop_failure_locked(record, cleanup_error)
                logger.warning(
                    "PCM sink router subscriber startup cleanup failed "
                    "sink=%s error=%s",
                    record.name,
                    cleanup_error.__class__.__name__,
                )

            return error

        dispatcher = self._new_dispatcher(record)
        try:
            dispatcher.start()
        except BaseException as error:
            try:
                record.sink.stop()
            except BaseException as cleanup_error:
                with self._state_lock:
                    self._record_stop_failure_locked(record, cleanup_error)
                logger.warning(
                    "PCM sink router dispatch startup cleanup failed "
                    "sink=%s error=%s",
                    record.name,
                    cleanup_error.__class__.__name__,
                )
            with self._state_lock:
                self._record_start_failure_locked(record, error)
            return error

        observed_at = _require_aware_datetime(self._now())
        with self._state_lock:
            record.dispatcher = dispatcher
            record.last_started_at = observed_at
            self._transition_locked(
                record,
                "active",
                observed_at=observed_at,
            )
            self._refresh_routing_locked()
        return None

    def _new_dispatcher(
        self,
        record: _PcmSubscriberRecord,
    ) -> _PcmDispatchWorker:
        return _PcmDispatchWorker(
            record.sink,
            capacity_bytes=self._capacity_bytes,
            retry_policy=self.retry_policy,
            clock=self._clock,
            on_submission=lambda worker, error: self._submission_completed(
                record,
                worker,
                error,
            ),
            on_finalized=lambda worker, error: self._finalization_completed(
                record,
                worker,
                error,
            ),
            notify=self._emit_pending_transitions,
        )

    def _submission_completed(
        self,
        record: _PcmSubscriberRecord,
        dispatcher: _PcmDispatchWorker,
        error: BaseException | None,
    ) -> None:
        observed_at = _require_aware_datetime(self._now())
        with self._state_lock:
            if record.dispatcher is not dispatcher:
                return
            record.submissions += 1
            if error is None:
                record.successful_submissions += 1
                if record.attached and record.state == "failed":
                    self._transition_locked(
                        record,
                        "active",
                        observed_at=observed_at,
                    )
                return

            record.failures += 1
            record.submit_failures += 1
            record.last_failure_at = observed_at
            record.last_error = _redacted_error_type(error)
            self._transition_locked(
                record,
                "failed",
                observed_at=observed_at,
            )

    def _finalization_completed(
        self,
        record: _PcmSubscriberRecord,
        dispatcher: _PcmDispatchWorker,
        error: BaseException | None,
    ) -> None:
        with self._state_lock:
            if record.dispatcher is not dispatcher:
                return
            if error is None:
                self._transition_locked(record, "detached")
            else:
                self._record_stop_failure_locked(record, error)

    def _record_start_failure_locked(
        self,
        record: _PcmSubscriberRecord,
        error: BaseException,
    ) -> BaseException:
        observed_at = _require_aware_datetime(self._now())
        record.attached = False
        record.failures += 1
        record.start_failures += 1
        record.last_failure_at = observed_at
        record.last_error = _redacted_error_type(error)
        self._transition_locked(record, "failed", observed_at=observed_at)
        self._refresh_routing_locked()
        return error

    def _record_stop_failure_locked(
        self,
        record: _PcmSubscriberRecord,
        error: BaseException,
    ) -> None:
        observed_at = _require_aware_datetime(self._now())
        record.failures += 1
        record.stop_failures += 1
        record.last_failure_at = observed_at
        record.last_error = _redacted_error_type(error)
        self._transition_locked(record, "failed", observed_at=observed_at)

    def _wait_for_dispatcher(
        self,
        record: _PcmSubscriberRecord,
        dispatcher: _PcmDispatchWorker,
        deadline: float,
    ) -> BaseException | None:
        remaining = max(0.0, deadline - monotonic())
        if dispatcher.wait(remaining):
            return dispatcher.finalization_error

        error = AudioOutputError(
            f"Timed out while finalizing PCM sink {record.name}"
        )
        if dispatcher.report_timeout_once():
            with self._state_lock:
                self._record_stop_failure_locked(record, error)
        return error

    def _refresh_routing_locked(self) -> None:
        dispatchers = tuple(
            record.dispatcher
            for record in self._records
            if record.attached and record.dispatcher is not None
        )
        with self._routing_lock:
            self._routing_running = self._running
            self._active_dispatchers = dispatchers

    def _find_record_locked(
        self,
        sink: PcmSink,
    ) -> _PcmSubscriberRecord | None:
        return next(
            (
                record
                for record in self._records
                if record.sink is sink
            ),
            None,
        )

    def _transition_locked(
        self,
        record: _PcmSubscriberRecord,
        state: PcmSubscriberState,
        *,
        observed_at: datetime | None = None,
    ) -> PcmSubscriberTransition | None:
        if record.state == state:
            return None

        timestamp = _require_aware_datetime(
            self._now() if observed_at is None else observed_at
        )
        previous_state = record.state
        previous_health = _subscriber_health(previous_state)
        self._transition_sequence += 1
        record.state = state
        record.transition_sequence = self._transition_sequence
        record.state_changed_at = timestamp

        snapshot = self._snapshot_record_locked(record)
        transition = PcmSubscriberTransition(
            sequence=self._transition_sequence,
            observed_at=timestamp,
            previous_state=previous_state,
            state=state,
            previous_health=previous_health,
            health=snapshot.health,
            snapshot=snapshot,
        )
        self._pending_transitions.append(transition)
        return transition

    def _emit_pending_transitions(self) -> None:
        with self._state_lock:
            if self._emitting_transitions:
                return
            self._emitting_transitions = True

        while True:
            with self._state_lock:
                if not self._pending_transitions:
                    self._emitting_transitions = False
                    return
                transition = self._pending_transitions.popleft()

            try:
                self.events.emit("transition", transition)
            except BaseException:
                with self._state_lock:
                    self._emitting_transitions = False
                raise

    def _snapshot_record_locked(
        self,
        record: _PcmSubscriberRecord,
    ) -> PcmSubscriberSnapshot:
        dispatcher = record.dispatcher
        statistics = (
            dispatcher.dispatch_statistics
            if dispatcher is not None
            else PcmSinkStatistics()
        )
        return PcmSubscriberSnapshot(
            subscriber_id=record.subscriber_id,
            name=record.name,
            state=record.state,
            health=_subscriber_health(record.state),
            attached=record.attached,
            running=dispatcher is not None and not dispatcher.done,
            statistics=statistics,
            start_attempts=record.start_attempts,
            submissions=record.submissions,
            successful_submissions=record.successful_submissions,
            failures=record.failures,
            start_failures=record.start_failures,
            submit_failures=record.submit_failures,
            stop_failures=record.stop_failures,
            transition_sequence=record.transition_sequence,
            state_changed_at=record.state_changed_at,
            last_started_at=record.last_started_at,
            last_failure_at=record.last_failure_at,
            last_error=record.last_error,
        )

    @staticmethod
    def _enrich_snapshot(
        snapshot: PcmSubscriberSnapshot,
        sink: PcmSink,
        dispatcher: _PcmDispatchWorker | None,
    ) -> PcmSubscriberSnapshot:
        statistics = (
            dispatcher.statistics
            if dispatcher is not None
            else _safe_sink_statistics(sink)
        )
        return replace(
            snapshot,
            running=_safe_sink_running(sink),
            statistics=statistics,
        )

    @staticmethod
    def _aggregate_statistics(
        bytes_submitted: int,
        subscribers: tuple[PcmSubscriberSnapshot, ...],
    ) -> PcmSinkStatistics:
        statistics = tuple(
            subscriber.statistics
            for subscriber in subscribers
            if subscriber.attached
        )
        return PcmSinkStatistics(
            bytes_submitted=bytes_submitted,
            bytes_written=sum(item.bytes_written for item in statistics),
            bytes_dropped=sum(item.bytes_dropped for item in statistics),
            queued_bytes=sum(item.queued_bytes for item in statistics),
            underflows=sum(item.underflows for item in statistics),
            overflows=sum(item.overflows for item in statistics),
            callback_statuses=sum(
                item.callback_statuses for item in statistics
            ),
        )


class _PcmBuffer:
    def __init__(self, capacity_bytes: int) -> None:
        if capacity_bytes < PCM_SAMPLE_WIDTH:
            raise ValueError("PCM buffer must hold at least one sample")
        self.capacity_bytes = capacity_bytes - capacity_bytes % PCM_SAMPLE_WIDTH
        self._data = bytearray()
        self._lock = threading.RLock()

    @property
    def queued_bytes(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def push(self, data: bytes) -> int:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples")
        if not data:
            return 0
        with self._lock:
            total = len(self._data) + len(data)
            dropped = max(0, total - self.capacity_bytes)
            if dropped:
                drop_from_existing = min(dropped, len(self._data))
                del self._data[:drop_from_existing]
                drop_from_new = dropped - drop_from_existing
                if drop_from_new:
                    data = data[drop_from_new:]
            self._data.extend(data)
            return dropped

    def pop(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("PCM read size must not be negative")
        size -= size % PCM_SAMPLE_WIDTH
        with self._lock:
            available = min(size, len(self._data))
            result = bytes(self._data[:available])
            del self._data[:available]
            return result


class _WritableBuffer(Protocol):
    def __setitem__(self, key: slice, value: bytes) -> None: ...


class _RawOutputStream(Protocol):
    def start(self) -> object: ...

    def stop(self) -> object: ...

    def close(self) -> object: ...


@runtime_checkable
class LocalPlaybackAdapter(Protocol):
    """Backend-specific local PCM consumer used by a buffered playback sink."""

    @property
    def name(self) -> str: ...

    @property
    def running(self) -> bool: ...

    def start(
        self,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None: ...

    def interrupt(self) -> None:
        """Promptly interrupt backend playback from another thread."""
        ...

    def close(self) -> None: ...


LocalPlaybackAdapterFactory = Callable[[], LocalPlaybackAdapter]


class _SoundDeviceDefaults(Protocol):
    device: object


class _SoundDeviceModule(Protocol):
    default: _SoundDeviceDefaults

    def RawOutputStream(
        self,
        *,
        samplerate: int,
        channels: int,
        dtype: str,
        device: str | int | None,
        callback: Callable[[object, int, object, object], None],
    ) -> _RawOutputStream: ...

    def get_portaudio_version(self) -> tuple[int, str]: ...

    def query_hostapis(self) -> object: ...

    def query_devices(self) -> object: ...


@dataclass(frozen=True, slots=True)
class AudioHostApiInfo:
    """One local PortAudio host API."""

    index: int
    name: str
    default_output_device: int | None


@dataclass(frozen=True, slots=True)
class AudioOutputDeviceInfo:
    """One local output-capable audio device."""

    index: int
    name: str
    host_api_index: int
    host_api_name: str
    max_output_channels: int
    default_samplerate: float
    default: bool


@dataclass(frozen=True, slots=True)
class AudioBackendInfo:
    """Immutable local-audio backend and output-device inventory."""

    backend: str
    version: str
    default_output_device: int | None
    host_apis: tuple[AudioHostApiInfo, ...]
    output_devices: tuple[AudioOutputDeviceInfo, ...]


def _load_sounddevice(
    module_loader: Callable[[str], object] = import_module,
) -> _SoundDeviceModule:
    try:
        return cast(_SoundDeviceModule, module_loader("sounddevice"))
    except ModuleNotFoundError as error:
        raise AudioOutputError(
            "Live playback support is not installed; install it with: "
            'python -m pip install "sds200[playback]"'
        ) from error
    except OSError as error:
        detail = str(error)
        if "portaudio" in detail.casefold():
            raise AudioOutputError(
                "PortAudio is required for local playback but its shared library "
                "was not found. On Debian or Raspberry Pi OS, install it with: "
                "sudo apt install libportaudio2"
            ) from error
        raise AudioOutputError(
            f"Could not load local audio playback support: {detail}"
        ) from error


def _mapping_entries(value: object, *, label: str) -> tuple[Mapping[str, object], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise AudioOutputError(f"{label} returned an unexpected value")
    entries: list[Mapping[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            raise AudioOutputError(f"{label} returned an unexpected entry")
        entries.append(cast(Mapping[str, object], entry))
    return tuple(entries)


def _required_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AudioOutputError(f"{label} is missing")
    return value


def _required_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AudioOutputError(f"{label} is not an integer")
    return value


def _optional_device_index(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _default_output_device(value: object) -> int | None:
    try:
        output = cast(Sequence[object], value)[1]
    except (IndexError, TypeError):
        return _optional_device_index(value)
    return _optional_device_index(output)


def _required_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioOutputError(f"{label} is not numeric")
    return float(value)


def inspect_audio_backend(
    *,
    module_loader: Callable[[str], object] = import_module,
) -> AudioBackendInfo:
    """Return PortAudio host APIs and output devices without opening a stream."""

    module = _load_sounddevice(module_loader)
    try:
        _, version = module.get_portaudio_version()
        host_entries = _mapping_entries(
            module.query_hostapis(),
            label="PortAudio host API query",
        )
        device_entries = _mapping_entries(
            module.query_devices(),
            label="PortAudio device query",
        )
        default_output = _default_output_device(module.default.device)

        host_apis: list[AudioHostApiInfo] = []
        host_names: dict[int, str] = {}
        for index, entry in enumerate(host_entries):
            name = _required_text(
                entry.get("name"),
                label=f"PortAudio host API {index} name",
            )
            host_names[index] = name
            host_apis.append(
                AudioHostApiInfo(
                    index=index,
                    name=name,
                    default_output_device=_optional_device_index(
                        entry.get("default_output_device")
                    ),
                )
            )

        output_devices: list[AudioOutputDeviceInfo] = []
        for fallback_index, entry in enumerate(device_entries):
            max_output_channels = _required_integer(
                entry.get("max_output_channels"),
                label=f"PortAudio device {fallback_index} output channels",
            )
            if max_output_channels <= 0:
                continue
            index = _required_integer(
                entry.get("index", fallback_index),
                label=f"PortAudio device {fallback_index} index",
            )
            host_api_index = _required_integer(
                entry.get("hostapi"),
                label=f"PortAudio device {index} host API",
            )
            output_devices.append(
                AudioOutputDeviceInfo(
                    index=index,
                    name=_required_text(
                        entry.get("name"),
                        label=f"PortAudio device {index} name",
                    ),
                    host_api_index=host_api_index,
                    host_api_name=host_names.get(host_api_index, "unknown"),
                    max_output_channels=max_output_channels,
                    default_samplerate=_required_number(
                        entry.get("default_samplerate"),
                        label=f"PortAudio device {index} default sample rate",
                    ),
                    default=index == default_output,
                )
            )
    except AudioOutputError:
        raise
    except Exception as error:
        raise AudioOutputError(
            f"Could not inspect local audio devices: {error}"
        ) from error

    return AudioBackendInfo(
        backend="PortAudio",
        version=version,
        default_output_device=default_output,
        host_apis=tuple(host_apis),
        output_devices=tuple(output_devices),
    )


class BufferedPlaybackSink:
    """Bounded newest-audio PCM sink backed by one local playback adapter."""

    def __init__(
        self,
        *,
        name: str,
        adapter_factory: LocalPlaybackAdapterFactory,
        buffer_ms: int = 250,
    ) -> None:
        if not name or name.strip() != name:
            raise ValueError("Playback sink name must not be empty or padded")
        if buffer_ms <= 0:
            raise ValueError("Playback buffer must be greater than zero milliseconds")

        capacity = max(
            PCM_SAMPLE_WIDTH,
            _PCM_BYTES_PER_SECOND * buffer_ms // 1000,
        )
        self._name = name
        self.buffer_ms = buffer_ms
        self._adapter_factory = adapter_factory
        self._buffer = _PcmBuffer(capacity)
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.RLock()
        self._adapter: LocalPlaybackAdapter | None = None
        self._bytes_submitted = 0
        self._bytes_written = 0
        self._bytes_dropped = 0
        self._underflows = 0
        self._overflows = 0
        self._callback_statuses = 0
        self._muted = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        with self._lock:
            adapter = self._adapter
        return adapter is not None and adapter.running

    @property
    def muted(self) -> bool:
        with self._lock:
            return self._muted

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._lock:
            return PcmSinkStatistics(
                bytes_submitted=self._bytes_submitted,
                bytes_written=self._bytes_written,
                bytes_dropped=self._bytes_dropped,
                queued_bytes=self._buffer.queued_bytes,
                underflows=self._underflows,
                overflows=self._overflows,
                callback_statuses=self._callback_statuses,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._adapter is not None:
                    return

            adapter = self._adapter_factory()
            if not isinstance(adapter, LocalPlaybackAdapter):
                raise TypeError(
                    "Local playback adapter factories must return "
                    "LocalPlaybackAdapter-compatible objects."
                )

            try:
                adapter.start(self._read_pcm, self._report_status)
            except BaseException:
                try:
                    adapter.interrupt()
                except Exception:
                    logger.exception(
                        "Local playback adapter interrupt failed after start error "
                        "adapter=%s",
                        adapter.name,
                    )
                try:
                    adapter.close()
                except Exception:
                    logger.exception(
                        "Local playback adapter cleanup failed after start error "
                        "adapter=%s",
                        adapter.name,
                    )
                raise

            with self._lock:
                self._adapter = adapter

        logger.info(
            "audio playback started sink=%s adapter=%s",
            self.name,
            adapter.name,
        )

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            self._muted = muted
            if muted:
                self._buffer.clear()

    def submit_pcm(self, data: bytes) -> None:
        with self._lock:
            if self._muted:
                return
            dropped = self._buffer.push(data)
            self._bytes_submitted += len(data)
            self._bytes_dropped += dropped
            if dropped:
                self._overflows += 1

    def interrupt(self) -> None:
        with self._lock:
            adapter = self._adapter
        if adapter is not None:
            adapter.interrupt()

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                adapter, self._adapter = self._adapter, None
            if adapter is None:
                return

            failure: BaseException | None = None
            try:
                adapter.interrupt()
            except BaseException as error:
                failure = error
            try:
                adapter.close()
            except BaseException as error:
                if failure is None:
                    failure = error

            self._buffer.clear()
            logger.info(
                "audio playback stopped sink=%s adapter=%s",
                self.name,
                adapter.name,
            )
            if failure is not None:
                if isinstance(failure, AudioOutputError):
                    raise failure
                raise AudioOutputError(
                    f"Could not close audio output device: {failure}"
                ) from failure

    def _read_pcm(self, size: int) -> bytes:
        if size < 0 or size % PCM_SAMPLE_WIDTH:
            raise ValueError(
                "Playback adapter reads must request complete 16-bit samples"
            )

        with self._lock:
            if self._muted:
                return bytes(size)

            pcm = self._buffer.pop(size)
            missing = size - len(pcm)
            self._bytes_written += len(pcm)
            if missing:
                self._underflows += 1
            return pcm + bytes(missing)

    def _report_status(self, active: bool) -> None:
        if not active:
            return
        with self._lock:
            self._callback_statuses += 1


class SoundDevicePlaybackAdapter:
    """Local playback adapter implemented with sounddevice and PortAudio."""

    def __init__(
        self,
        *,
        device: str | int | None = None,
        module_loader: Callable[[str], object] = import_module,
    ) -> None:
        self.device = device
        self._module_loader = module_loader
        self._lock = threading.RLock()
        self._stream: _RawOutputStream | None = None
        self._interrupted = False

    @property
    def name(self) -> str:
        return (
            "portaudio:default"
            if self.device is None
            else f"portaudio:{self.device}"
        )

    @property
    def running(self) -> bool:
        with self._lock:
            return self._stream is not None and not self._interrupted

    def start(
        self,
        pcm_reader: Callable[[int], bytes],
        status_reporter: Callable[[bool], None],
    ) -> None:
        with self._lock:
            if self._stream is not None:
                return

            module = _load_sounddevice(self._module_loader)

            def playback_callback(
                outdata: object,
                frames: int,
                time_info: object,
                status: object,
            ) -> None:
                del time_info
                requested = frames * PCM_CHANNELS * PCM_SAMPLE_WIDTH
                cast(_WritableBuffer, outdata)[:] = pcm_reader(requested)
                status_reporter(bool(status))

            stream: _RawOutputStream | None = None
            try:
                stream = module.RawOutputStream(
                    samplerate=PCMU_SAMPLE_RATE,
                    channels=PCM_CHANNELS,
                    dtype="int16",
                    device=self.device,
                    callback=playback_callback,
                )
                stream.start()
            except Exception as error:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        logger.exception(
                            "Audio output cleanup failed after start error"
                        )
                raise AudioOutputError(
                    f"Could not open audio output device: {error}"
                ) from error

            self._stream = stream
            self._interrupted = False

    def interrupt(self) -> None:
        with self._lock:
            stream = self._stream
            if stream is None or self._interrupted:
                return
            self._interrupted = True

        try:
            stream.stop()
        except Exception as error:
            raise AudioOutputError(
                f"Could not interrupt audio output device: {error}"
            ) from error

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            interrupted = self._interrupted
            self._interrupted = False

        if stream is None:
            return

        failure: BaseException | None = None
        if not interrupted:
            try:
                stream.stop()
            except BaseException as error:
                failure = error
        try:
            stream.close()
        except BaseException as error:
            if failure is None:
                failure = error

        if failure is not None:
            raise AudioOutputError(
                f"Could not close audio output device: {failure}"
            ) from failure


class SoundDevicePlaybackSink(BufferedPlaybackSink):
    """Compatibility sink for sounddevice/PortAudio local playback."""

    def __init__(
        self,
        *,
        device: str | int | None = None,
        buffer_ms: int = 250,
        module_loader: Callable[[str], object] = import_module,
    ) -> None:
        self.device = device
        self._module_loader = module_loader
        super().__init__(
            name=(
                "playback:default"
                if device is None
                else f"playback:{device}"
            ),
            buffer_ms=buffer_ms,
            adapter_factory=lambda: SoundDevicePlaybackAdapter(
                device=device,
                module_loader=module_loader,
            ),
        )


class PcmWavSink:
    """Write and finalize one WAV recorder from a single bounded worker."""

    def __init__(
        self,
        recorder: PcmuWavRecorder,
        *,
        buffer_seconds: float = 5.0,
        stop_timeout: float = 5.0,
    ) -> None:
        self.recorder = recorder
        self.buffer_seconds = _require_positive_finite(
            buffer_seconds,
            "WAV sink buffer",
        )
        self.stop_timeout = _require_positive_finite(
            stop_timeout,
            "WAV sink stop timeout",
        )
        capacity = max(
            PCM_SAMPLE_WIDTH,
            int(_PCM_BYTES_PER_SECOND * self.buffer_seconds),
        )
        self._capacity_bytes = capacity - capacity % PCM_SAMPLE_WIDTH
        self._lifecycle_lock = threading.Lock()
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._stop_waiters = 0
        self._error: BaseException | None = None
        self._bytes_submitted = 0
        self._bytes_written = 0
        self._bytes_dropped = 0
        self._overflows = 0

    @property
    def name(self) -> str:
        return f"wav:{self.recorder.path}"

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def statistics(self) -> PcmSinkStatistics:
        with self._condition:
            return PcmSinkStatistics(
                bytes_submitted=self._bytes_submitted,
                bytes_written=self._bytes_written,
                bytes_dropped=self._bytes_dropped,
                queued_bytes=self._queued_bytes,
                overflows=self._overflows,
            )

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._condition:
                if self._thread is not None:
                    return
            self.recorder.start()
            with self._condition:
                self._stopping = False
                self._error = None
                self._done.clear()
                thread = threading.Thread(
                    target=self._run,
                    name="sds200-pcm-wav",
                    daemon=True,
                )
                self._thread = thread
            try:
                thread.start()
            except BaseException:
                with self._condition:
                    self._thread = None
                    self._stopping = True
                    self._done.set()
                    self._condition.notify_all()
                try:
                    self.recorder.close()
                except BaseException as cleanup_error:
                    with self._condition:
                        self._error = cleanup_error
                    logger.warning(
                        "PCM WAV cleanup failed after worker start error "
                        "error=%s",
                        cleanup_error.__class__.__name__,
                    )
                raise

    def submit_pcm(self, data: bytes) -> None:
        if len(data) % PCM_SAMPLE_WIDTH:
            raise ValueError("PCM data must contain complete 16-bit samples")
        if not data:
            return
        with self._condition:
            if self._thread is None or self._stopping:
                raise RuntimeError("WAV sink is not running")
            self._bytes_submitted += len(data)
            dropped = 0
            if len(data) > self._capacity_bytes:
                dropped += len(data) - self._capacity_bytes
                data = data[-self._capacity_bytes :]
            while self._queue and self._queued_bytes + len(data) > self._capacity_bytes:
                removed = self._queue.popleft()
                self._queued_bytes -= len(removed)
                dropped += len(removed)
            if dropped:
                self._bytes_dropped += dropped
                self._overflows += 1
            self._queue.append(data)
            self._queued_bytes += len(data)
            self._condition.notify()

    def stop(self) -> None:
        with self._lifecycle_lock, self._condition:
            thread = self._thread
            if thread is None:
                error = self._error
                if error is None:
                    return
                raise AudioOutputError(
                    "PCM WAV sink failed: "
                    f"{error.__class__.__name__}"
                ) from error
            self._stopping = True
            self._stop_waiters += 1
            self._condition.notify_all()

        completed = self._done.wait(timeout=self.stop_timeout)
        with self._lifecycle_lock, self._condition:
            error = self._error
            self._stop_waiters -= 1
            if (
                completed
                and self._stop_waiters == 0
                and self._thread is thread
            ):
                self._thread = None

        if not completed:
            raise AudioOutputError(
                "Timed out while finalizing the PCM WAV sink"
            )
        if error is not None:
            raise AudioOutputError(
                f"PCM WAV sink failed: {error.__class__.__name__}"
            ) from error

    def _run(self) -> None:
        write_error: BaseException | None = None
        data = b""
        try:
            while True:
                with self._condition:
                    while not self._queue and not self._stopping:
                        self._condition.wait()
                    if not self._queue and self._stopping:
                        return
                    data = self._queue.popleft()
                    self._queued_bytes -= len(data)
                self.recorder.write_pcm(data)
                with self._condition:
                    self._bytes_written += len(data)
        except BaseException as error:
            write_error = error
            with self._condition:
                self._stopping = True
                self._bytes_dropped += len(data) + self._queued_bytes
                self._queue.clear()
                self._queued_bytes = 0
                self._condition.notify_all()
        finally:
            finalization_error = write_error
            try:
                self.recorder.close()
            except BaseException as close_error:
                if finalization_error is None:
                    finalization_error = close_error
                logger.warning(
                    "PCM WAV recorder close failed error=%s",
                    close_error.__class__.__name__,
                )
            with self._condition:
                self._error = finalization_error
                self._stopping = True
                self._done.set()
                self._condition.notify_all()
