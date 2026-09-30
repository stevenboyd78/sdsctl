#!/usr/bin/env python3
"""Offline native-process recording assembly; no CLI or live handoff wiring.

This consumes already constructed native objects. It is deliberately narrower
than the product CLI: reloaders, destinations, MQTT, remote controls and waterfall
are refused, not silently removed. A future launcher and independently qualified
process deadline/recovery contract are prerequisites to live use.
"""

from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from supplemental_recording_api import FiniteRecordingApi
from supplemental_recording_evidence import RecordingBaseline, digest, template
from supplemental_recording_monitor import Writer
from supplemental_recording_owner import FiniteRecordingOwner, Plan
from supplemental_recording_schedule import FiniteRecordingSchedule, Result

from sds200.audio import AudioStream
from sds200.audio_sinks import AudioFanoutSession, PcmSinkRouter
from sds200.daemon_event_server import DaemonEventServer
from sds200.daemon_event_stream import DaemonEventStream
from sds200.daemon_pcmu_server import DaemonPcmuServer
from sds200.daemon_process import DaemonProcess, DaemonSignalController
from sds200.daemon_recording import DaemonRecordingManager
from sds200.daemon_recording_file_server import DaemonRecordingFileServer
from sds200.daemon_runtime import DaemonRuntime
from sds200.daemon_server import DaemonApiServer
from sds200.daemon_supplemental_acquisition import DaemonSupplementalAcquisition
from sds200.pcmu_stream import PcmuStream
from sds200.scanner_display_supplemental_transport import (
    SupplementalDeliveryService,
    SupplementalUnavailable,
)

MESSAGE = (
    "Native recording assembly is unconfirmed; preserve the case and use independent recovery."
)
UNSUPPORTED = (
    "destination_coordinator",
    "destination_reloader",
    "mqtt_service",
    "remote_service",
    "live_audio_server",
    "waterfall_server",
)


class UnconfirmedAssembly(ValueError):
    """No process-exit, audible-pass or restoration claim."""


def require(value: bool) -> None:
    if not value:
        raise UnconfirmedAssembly(MESSAGE)


def _failure_locations(error):
    """Bounded source locations for the explicit offline test boundary only."""
    source = Path(__file__).parent
    seen, rows = set(), []
    for index in range(8):
        if not isinstance(error, BaseException) or id(error) in seen:
            break
        seen.add(id(error))
        trace = error.__traceback__
        for _ in range(64):
            if trace is None:
                break
            path = Path(trace.tb_frame.f_code.co_filename)
            if path.parent == source and re.fullmatch(
                r"(?:accept_)?supplemental_[a-z0-9_]+\.py", path.name
            ):
                row = f"cause {index + 1}: scripts/{path.name}:{trace.tb_lineno}"
                if row not in rows:
                    rows.append(row)
                    if len(rows) == 32:
                        return tuple(rows)
            trace = trace.tb_next
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return tuple(rows)


class NativeRecordingAssembly:
    """One exact native lifecycle, one explicit in-process request, one recording.

    The ready/request mechanism is for the offline fixture, not an authenticated
    operator channel. A launcher must separately bind that channel to a durable
    case, exact process identity, source inventory and independent deadline.
    Returning a Result proves acknowledged file finalization and native cleanup,
    not OS process exit, optional-read success, audio quality or App restoration.
    """

    def __init__(
        self,
        process: DaemonProcess,
        api: FiniteRecordingApi,
        acquisition: DaemonSupplementalAcquisition,
        delivery: SupplementalDeliveryService,
        baseline: RecordingBaseline,
        journal: Path,
        writer: Writer,
        *,
        generation: str,
        audio_endpoint_sha256: str,
        ready_timeout: float = 10,
    ) -> None:
        self._lock = Lock()
        self._ready = Event()
        self._requested = Event()
        self._cancel = Event()
        self._attempted = False
        self._worker: Thread | None = None
        self.schedule: FiniteRecordingSchedule | None = None
        self.controller: FiniteRecordingOwner | None = None
        self.result: Result | None = None
        self.worker_error = False
        self.cleanup_complete = False
        self._returns = None
        try:
            require(type(process) is DaemonProcess and type(api) is FiniteRecordingApi)
            require(type(acquisition) is DaemonSupplementalAcquisition)
            require(type(delivery) is SupplementalDeliveryService)
            require(type(baseline) is RecordingBaseline and type(writer) is Writer)
            require(type(ready_timeout) in (int, float) and math.isfinite(ready_timeout))
            require(0 < ready_timeout <= 600)
            digest(generation)
            digest(audio_endpoint_sha256)
            self.process, self.api, self.acquisition, self.delivery = (
                process,
                api,
                acquisition,
                delivery,
            )
            self.runtime, self.manager, self.signals = (
                process.runtime,
                process.recording_manager,
                process.signals,
            )
            self.baseline, self.journal, self.writer = baseline, journal, writer
            self.generation, self.audio_endpoint_sha256 = generation, audio_endpoint_sha256
            self.ready_timeout = ready_timeout
            self._audio, self._router = self.runtime.audio, self.runtime.router
            self._stream = self._audio.stream
            self._source = self._stream.transport
            self._services = (
                process.api_server,
                process.event_server,
                process.recording_file_server,
                process.pcmu_server,
            )
            self._bindings(check_api=False)
            self._fresh()
            status = acquisition.status()
            require(not status.armed and not status.ended and not acquisition._started)
            require(self.manager.snapshot().status.value == "idle")
            require(self.manager.directory == baseline.root)
            require(self.manager.path_policy.template == template(baseline.case))
            require(self.manager.path_policy.directory == baseline.root)
            require(not self.manager.path_policy.organization.enabled)
            require(self.manager.path_policy.overwrite is False)
            # Production monotonic clocks only; no wall-clock deadlines or test
            # clock substitutions in this assembled native lifecycle.
            require(acquisition._clock is monotonic and self.manager._clock is monotonic)
            api.bind_acquisition(acquisition, delivery)
            self._bindings()
        except Exception:
            raise UnconfirmedAssembly(MESSAGE) from None

    def _fresh(self) -> None:
        require(not self.runtime.running and not self.signals._active)
        require(not self._audio.running and not self._router.snapshot().subscribers)
        require(not self._source.running)
        require(all(service is None or not service.active for service in self._services))
        require(not self.manager.snapshot().closed)
        require(self.manager.snapshot().status.value == "idle")

    def _bindings(self, *, check_api: bool = True) -> None:
        p = self.process
        require(
            type(self.runtime) is DaemonRuntime and type(self.manager) is DaemonRecordingManager
        )
        require(type(self.signals) is DaemonSignalController)
        require(p.runtime is self.runtime is self.api.runtime is self.acquisition._runtime)
        require(p.recording_manager is self.manager is self.api.recording_manager)
        require(self.manager.runtime is self.runtime and p.signals is self.signals)
        require(type(self._audio) is AudioFanoutSession and type(self._router) is PcmSinkRouter)
        require(type(self._stream) is AudioStream)
        require(self.runtime.audio is self._audio and self.runtime.router is self._router)
        require(self._audio.stream is self._stream and self._stream.transport is self._source)
        require(self._audio.sinks == (self._router,))
        require(all(getattr(p, name) is None for name in UNSUPPORTED))
        require(
            self._services == (p.api_server, p.event_server, p.recording_file_server, p.pcmu_server)
        )
        require(type(p.api_server) is DaemonApiServer and p.api_server.api is self.api)
        require(type(p.event_server) is DaemonEventServer)
        stream = p.event_server.stream
        require(type(stream) is DaemonEventStream and stream.runtime is self.runtime)
        require(stream.recording_manager is self.manager)
        require(type(p.recording_file_server) is DaemonRecordingFileServer)
        require(p.recording_file_server.recording_manager is self.manager)
        if p.pcmu_server is not None:
            require(type(p.pcmu_server) is DaemonPcmuServer)
            require(type(p.pcmu_server.stream) is PcmuStream)
            require(p.pcmu_server.stream.source is self.runtime.audio.stream.transport)
        self.delivery.validate_owner(self.runtime, self.acquisition.frames)
        require(self.api.display_frames is self.acquisition.frames)
        require(self.api.supplemental_display is self.delivery)
        if check_api:
            require(self.api.acquisition_binding_valid())

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def request_start(self, *, returns=None) -> None:
        """An explicit local-fixture request; readiness alone never starts I/O."""
        with self._lock:
            require(self.ready and not self._requested.is_set() and not self._cancel.is_set())
            require(not self.signals.stop_requested)
            if returns is not None:
                from supplemental_recording_channel import Sender

                require(type(returns) is Sender and returns.phase == "started")
                returns.binding.payload()
                require(returns.binding.stored.baseline == self.baseline)
                require(returns.binding.stored.writer == self.writer)
                require(returns.binding.generation == self.generation)
                require(
                    returns.binding.stored.contract.audio_endpoint_sha256
                    == self.audio_endpoint_sha256
                )
                now = monotonic()
                require(now + 3 <= returns.binding.start_by)
                require(
                    now + 3 + self.acquisition._policy.window_seconds + 10
                    <= returns.binding.finish_by
                )
                require(
                    3 + self.acquisition._policy.window_seconds + 10
                    <= returns.binding.stored.contract.maximum_recording_seconds
                )
            self._returns = returns
            self._ready.clear()
            self._requested.set()

    def cancel(self) -> None:
        with self._lock:
            self._ready.clear()
            self._cancel.set()

    def _current(self) -> bool:
        self._bindings()
        if not self.signals._active or self.signals.stop_requested:
            return False
        if not self.runtime.running or not self.process.api_server.active:
            return False
        if self.acquisition._qualified() is not True:
            return False
        try:
            self.delivery.context()  # Cached only; cannot create consumer demand.
        except SupplementalUnavailable:
            return False
        return True

    def _work(self) -> None:
        try:
            deadline = monotonic() + self.ready_timeout
            while not self._cancel.is_set():
                require(monotonic() < deadline and not self.signals.stop_requested)
                current = self._current()
                require(monotonic() < deadline and not self._cancel.is_set())
                with self._lock:
                    if current and not self._requested.is_set() and not self._cancel.is_set():
                        self._ready.set()
                    else:
                        self._ready.clear()
                if self._requested.is_set():
                    require(current and not self._cancel.is_set())
                    now = monotonic()
                    require(now < deadline and not self.signals.stop_requested)
                    stop = now + 3 + self.acquisition._policy.window_seconds
                    plan = Plan(
                        self.baseline.case,
                        self.generation,
                        self.audio_endpoint_sha256,
                        now,
                        now + 3,
                        stop,
                        stop + 10,
                    )
                    self.controller = FiniteRecordingOwner(
                        self.manager, self.baseline, plan, self.journal
                    )
                    self.schedule = FiniteRecordingSchedule(
                        self.controller,
                        self.api,
                        self.writer,
                        acquisition=self.acquisition,
                        returns=self._returns,
                    )
                    require(not self.signals.stop_requested)
                    self.result = self.schedule.run(self._cancel)
                    self._bindings()
                    break
                self._cancel.wait(0.025)
        except BaseException as error:
            # Only a fixed failure bit crosses the worker boundary.
            self.worker_error = True
            if os.environ.get("SDSCTL_TEST_FAILURE_LOCATIONS") == "1":
                for location in _failure_locations(error):
                    print(location, file=sys.stderr, flush=True)
        finally:
            self._ready.clear()
            self.acquisition._end("recording_assembly_ended")
            # DaemonSignalController.__enter__ resets stop requests. Do not lose
            # an early failure while the native main thread is still entering it.
            while not self._cancel.is_set() and not self.signals._active:
                self._cancel.wait(0.025)
            if self.signals._active:
                self.signals.request_stop()

    def run(self) -> Result:
        with self._lock:
            require(not self._attempted)
            self._attempted = True
        failed = False
        try:
            self._bindings()
            self._fresh()
            require(not self._cancel.is_set())
            self.acquisition.start()
            self._worker = Thread(target=self._work, name="finite-native-recording", daemon=True)
            self._worker.start()
            self.process.run()  # Original native process, not a replacement runtime.
        except Exception:
            failed = True
        finally:
            self.cancel()
            if self._worker is not None:
                self._worker.join(timeout=2)
            closed = self._worker is None or not self._worker.is_alive()
            try:
                if closed:
                    if self.controller is not None:
                        self.controller.close()
                    self.acquisition.close()
                    worker = self.acquisition.frames.quick_key_worker_status()
                    self.cleanup_complete = (
                        (worker is None or not worker.alive)
                        and self.runtime._supplemental_acquisition is None
                        and not self.runtime.running
                        and not self.manager.snapshot().active
                        and not self.signals._active
                        and not self.process.api_server.active
                        and not self.process.event_server.active
                        and not self.process.recording_file_server.active
                        and (
                            self.process.pcmu_server is None or not self.process.pcmu_server.active
                        )
                    )
            except Exception:
                self.cleanup_complete = False
        require(
            not failed
            and not self.worker_error
            and self.cleanup_complete
            and self.result is not None
        )
        if self._returns is not None:
            try:
                # This call is after the successful native run and cleanup, not
                # a filesystem-poll inference from stopped.json or WAV bytes.
                self._returns.completed(self.result.artifact, self.result.stopped)
            except Exception:
                raise UnconfirmedAssembly(MESSAGE) from None
        return self.result


if __name__ == "__main__":
    raise SystemExit("Offline native assembly only; no daemon, scanner or recording started.")
