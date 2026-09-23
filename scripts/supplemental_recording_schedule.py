#!/usr/bin/env python3
"""One cooperative finite recording run; offline qualification, no live assembly.

The caller must qualify a separate process termination deadline before calling
run(). This synchronous scheduler cannot interrupt blocked native or filesystem
I/O. Its failures never authorize a retry, imply writer exit or restore an App.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from threading import Event, Lock

from supplemental_recording_api import FiniteRecordingApi
from supplemental_recording_evidence import FinalizedRecording, verify_finalized
from supplemental_recording_monitor import MAX_FILES, Observation, Writer, observe
from supplemental_recording_owner import FiniteRecordingOwner

MESSAGE = (
    "Finite recording schedule is unconfirmed; preserve the case and use independent recovery."
)
POLL_SECONDS = 0.25
SAMPLE_SECONDS = 1.0


class UnconfirmedSchedule(ValueError):
    """A consumed run with no exit or recovery claim."""


def require(value: bool) -> None:
    if not value:
        raise UnconfirmedSchedule(MESSAGE)


@dataclass(frozen=True)
class Result:
    artifact: FinalizedRecording
    active_observations: int
    last_observation: Observation


class FiniteRecordingSchedule:
    """Run once after readiness, in the caller's supervised native worker.

    No thread, socket, signal or retry is created here. Cancellation abandons this
    operation; the caller must request native shutdown, and the independent outer
    supervisor must enforce process termination even if this worker is blocked.
    API filtering does not exclude arbitrary trusted in-process manager calls.
    """

    def __init__(self, owner: FiniteRecordingOwner, api: FiniteRecordingApi, writer: Writer):
        self._lock = Lock()
        self._phase = "unconfirmed"
        try:
            require(type(owner) is FiniteRecordingOwner and owner.phase == "prepared")
            require(type(api) is FiniteRecordingApi and type(writer) is Writer)
            require(len(owner.baseline.files) <= MAX_FILES - 3)
            self.owner, self.api, self.writer = owner, api, writer
            self._plan, self._manager, self._baseline = owner.plan, owner.manager, owner.baseline
            self._runtime = owner.manager.runtime
            self._binding()
            self._phase = "prepared"
        except Exception:
            raise UnconfirmedSchedule(MESSAGE) from None

    @property
    def phase(self) -> str:
        return self._phase

    def _binding(self) -> None:
        require(
            self.owner.plan is self._plan
            and self.owner.manager is self._manager
            and self.owner.baseline is self._baseline
            and self._manager.runtime is self._runtime
            and self.api.runtime is self.api._bound_runtime is self._runtime
            and self.api.recording_manager is self.api._bound_manager is self._manager
        )
        self.owner._binding()

    def run(self, cancel: Event) -> Result:
        require(self._lock.acquire(blocking=False))
        consumed = False
        try:
            require(self._phase == "prepared")
            self._phase = "unconfirmed"  # Consume before any precondition or dispatch.
            consumed = True
            require(type(cancel) is Event and not cancel.is_set())
            self._binding()
            expected = self.owner.start()
            previous = None
            count = 0
            next_sample = self.owner._now()
            while True:
                require(not cancel.is_set())
                self._binding()
                now = self.owner._now()
                require(now < self._plan.finish_by)
                if now >= self._plan.stop_at:
                    break
                require(self.owner.phase == "recording")
                self.owner._active(self._manager.snapshot().as_dict())
                require(self.owner._receipts is not None)
                self.owner._receipts.check()
                if now >= next_sample:
                    previous = observe(
                        self._baseline,
                        expected,
                        generation=self._plan.generation,
                        writer=self.writer,
                        stage="recording",
                        previous=previous,
                    )
                    count += 1
                    next_sample = now + SAMPLE_SECONDS
                now = self.owner._now()
                require(now < self._plan.finish_by)
                if now < self._plan.stop_at:
                    cancel.wait(min(POLL_SECONDS, self._plan.stop_at - now))
            require(previous is not None and count > 0 and not cancel.is_set())
            stopped = self.owner.stop()
            self._binding()
            require(not cancel.is_set() and self.owner._now() < self._plan.finish_by)
            final = observe(
                self._baseline,
                expected,
                generation=self._plan.generation,
                writer=self.writer,
                stage="finalizing",
                previous=previous,
            )
            require(not cancel.is_set() and self.owner._now() < self._plan.finish_by)
            proof = verify_finalized(
                self._baseline, expected, generation=self._plan.generation, stopped=stopped
            )
            self._binding()
            require(not cancel.is_set() and self.owner._now() < self._plan.finish_by)
            self._phase = "verified"
            return Result(proof, count, final)
        except BaseException as error:
            if consumed:
                # Consume the native controller even if cancellation occurred
                # before start. Closing only releases receipts; it never stops
                # a recorder or grants permission to reuse the journal.
                with suppress(Exception):
                    self.owner.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedSchedule(MESSAGE) from None
        finally:
            self._lock.release()


if __name__ == "__main__":
    raise SystemExit("Offline finite recording scheduler; no recording or handoff started.")
