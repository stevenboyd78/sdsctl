#!/usr/bin/env python3
"""Offline bridge for the separate recording journal; not a live host service.

Uses the existing fixed CLI dispatch and exact-process reconciliation. Callers
still need a reviewed recording-aware protected-file/native-state collector,
new sealed host plan, durable operator gate and independent process deadline.
There is intentionally no CLI, automatic request, recording start or retry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import supplemental_handoff_executor as dispatch
from supplemental_handoff_policy import identifier, require
from supplemental_handoff_recovery import RecoverySession as BaseRecoverySession
from supplemental_recording_handoff import Journal, Machine, Observation


@dataclass(frozen=True)
class Sample(dispatch.Sample):
    observation: Observation

    def __post_init__(self) -> None:
        identifier(self.boot_id)
        Machine.fresh(self.now, self.observation)


class Executor(dispatch.Executor):
    sample_type = Sample

    def __init__(
        self,
        journal: Journal,
        read: Callable[[], Sample],
        send: Callable[[tuple[str, ...], str], None],
    ):
        require(type(journal) is Journal and type(journal.machine) is Machine)
        super().__init__(journal, read, send)


class RecoverySession(BaseRecoverySession):
    """Same pidfd/CLI receipt gates with truthful recording-aware observations.

    Completion means normal-App restoration only. The journal's separate
    recording_outcome never implies audible quality or supplemental-read success.
    """

    sample_type = Sample
    executor_type = Executor


if __name__ == "__main__":
    raise SystemExit("Offline recording recovery bridge only; no live host plan is enabled.")
