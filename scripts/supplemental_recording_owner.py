#!/usr/bin/env python3
"""Finite, journaled native recording owner; offline qualification only.

Not wired into any launcher, host plan, daemon API or public configuration.
The caller must independently bind the native process/generation and provide a
hard outer termination deadline. Checks here are cooperative: they neither
cancel blocked I/O nor start a timer. Existing live handoffs still forbid recording.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from supplemental_handoff_files import DIRECTORY, identity
from supplemental_recording_evidence import (
    MAX_RECORDING_SECONDS,
    RELIABILITY,
    SINK,
    SNAPSHOT,
    RecordingBaseline,
    RecordingExpectation,
    capture_baseline,
    digest,
    filename,
    mapping,
    number,
    opened_root,
    template,
    timestamp,
)

from sds200.daemon_recording import DaemonRecordingManager

MESSAGE = (
    "Finite recording ownership is unconfirmed; preserve the case and use independent recovery."
)


class UnconfirmedOwner(ValueError):
    """No private path, radio identity, receipt or arbitrary exception text."""


def require(value: bool) -> None:
    if not value:
        raise UnconfirmedOwner(MESSAGE)


def clock(value: Any) -> float:
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0)
    return float(value)


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class Plan:
    case: str
    generation: str
    audio_endpoint_sha256: str
    prepared_at: float
    start_by: float
    stop_at: float
    finish_by: float

    def __post_init__(self) -> None:
        try:
            template(self.case)
            digest(self.generation)
            digest(self.audio_endpoint_sha256)
            times = [
                clock(v) for v in (self.prepared_at, self.start_by, self.stop_at, self.finish_by)
            ]
            require(times[0] < times[1] < times[2] < times[3])
            require(times[1] - times[0] <= 10 and times[3] - times[2] <= 10)
            require(times[3] - times[0] <= MAX_RECORDING_SECONDS)
        except Exception:
            raise UnconfirmedOwner(MESSAGE) from None


class _Receipts:
    """One new private journal, exclusive files + file/directory fsync.

    An existing/partial journal is evidence only, never authority to replay an
    operation. Advisory locking serializes cooperating owners; it cannot protect
    against a privileged actor deleting/replacing the whole case.
    """

    def __init__(self, path: Path) -> None:
        self.handles: list[tuple[int, str, int, tuple[int, ...]]] = []
        self.anchor = -1
        self.fd = -1
        self.files: dict[str, bytes] = {}
        try:
            require(type(path) is type(Path()) and path.is_absolute() and path != Path("/"))
            require(".." not in path.parts and len(path.parts) <= 32)
            self.anchor = os.open("/", DIRECTORY)
            parent = self.anchor
            for name in path.parts[1:]:
                child = os.open(name, DIRECTORY, dir_fd=parent)
                try:
                    before = identity(os.fstat(child))
                except BaseException:
                    os.close(child)
                    raise
                self.handles.append((parent, name, child, before))
                parent = child
            self.fd = parent
            info = os.fstat(parent)
            require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
            fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self) -> None:
        require(self.fd >= 0)
        for parent, name, child, before in self.handles:
            require(identity(os.fstat(child))[:6] == before[:6])
            require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False))[:6] == before[:6])
        # scandir prevents an unexpected large directory from allocating a list.
        names = set()
        with os.scandir(self.fd) as children:
            for entry in children:
                require(entry.name in self.files and entry.name not in names)
                names.add(entry.name)
        require(names == set(self.files))
        for name, raw in self.files.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            try:
                before = os.fstat(fd)
                require(
                    stat.S_ISREG(before.st_mode)
                    and stat.S_IMODE(before.st_mode) == 0o600
                    and before.st_uid == os.geteuid()
                    and before.st_nlink == 1
                    and before.st_size == len(raw)
                )
                require(os.read(fd, len(raw) + 1) == raw)
                require(identity(os.fstat(fd)) == identity(before))
                require(
                    identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False))
                    == identity(before)
                )
            finally:
                os.close(fd)

    def publish(self, name: str, value: Any) -> None:
        require(name in ("prepared", "start-intent", "started", "stop-intent", "stopped"))
        name += ".json"
        require(name not in self.files)
        self.check()
        raw = encoded(value)
        require(0 < len(raw) <= 8192)
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
        )
        with os.fdopen(fd, "wb") as output:
            require(output.write(raw) == len(raw))
            output.flush()
            os.fsync(output.fileno())
        os.fsync(self.fd)
        self.files[name] = raw
        self.check()

    def close(self) -> None:
        self.fd = -1
        for _, _, child, _ in reversed(self.handles):
            os.close(child)
        self.handles.clear()
        if self.anchor >= 0:
            os.close(self.anchor)
            self.anchor = -1


class FiniteRecordingOwner:
    """One native manager, one start and one scheduled stop; no implicit retry.

    This object's caller is trusted to supply a source-pinned native manager and
    a process-bound plan, exclude every other recording mutator, and invoke stop
    at the fixed deadline. Native shutdown/independent recovery remain responsible
    after any uncertainty, including an unacknowledged successful start or stop.
    Closing this controller only releases its journal; it does not stop a writer.
    """

    def __init__(
        self,
        manager: DaemonRecordingManager,
        baseline: RecordingBaseline,
        plan: Plan,
        journal: Path,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._receipts: _Receipts | None = None
        self._lock = Lock()
        self._phase = "unconfirmed"
        self._started: dict[str, Any] | None = None
        self._last_clock = 0.0
        try:
            require(type(manager) is DaemonRecordingManager)
            require(type(baseline) is RecordingBaseline and type(plan) is Plan)
            require(baseline.case == plan.case)
            require(
                not journal.is_relative_to(baseline.root)
                and not baseline.root.is_relative_to(journal)
            )
            self.manager, self.baseline, self.plan, self.monotonic = (
                manager,
                baseline,
                plan,
                monotonic,
            )
            self._runtime = manager.runtime
            self._binding()
            self._idle()
            require(plan.prepared_at <= self._now() < plan.start_by)
            require(capture_baseline(baseline.root, plan.case) == baseline)
            self._receipts = _Receipts(journal)
            self._receipts.publish(
                "prepared",
                {
                    "schema": 1,
                    "plan": asdict(plan),
                    "root_sha256": hashlib.sha256(os.fsencode(baseline.root)).hexdigest(),
                    "baseline_sha256": hashlib.sha256(
                        encoded(
                            {
                                "root_identity": baseline.root_identity,
                                "files": [(name, asdict(item)) for name, item in baseline.files],
                            }
                        )
                    ).hexdigest(),
                },
            )
            self._phase = "prepared"
        except BaseException as error:
            if self._receipts is not None:
                self._receipts.close()
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedOwner(MESSAGE) from None

    @property
    def phase(self) -> str:
        return self._phase

    def _now(self) -> float:
        now = clock(self.monotonic())
        require(now >= self._last_clock)
        self._last_clock = now
        return now

    def _binding(self) -> None:
        policy = self.manager.path_policy
        require(self.manager.runtime is self._runtime and self._runtime.running is True)
        require(
            self.manager.directory == self.baseline.root and policy.directory == self.baseline.root
        )
        require(policy.template == template(self.plan.case) and policy.overwrite is False)
        require(policy.organization.enabled is False)
        endpoint = self._runtime.audio.stream.endpoint
        require(type(endpoint) is str and 0 < len(endpoint) <= 4096)
        require(hashlib.sha256(endpoint.encode()).hexdigest() == self.plan.audio_endpoint_sha256)
        with opened_root(self.baseline.root, deadline=time.monotonic() + 2) as fd:
            require(identity(os.fstat(fd))[:6] == self.baseline.root_identity)

    @staticmethod
    def _snapshot(value: Any) -> dict[str, Any]:
        mapping(value, SNAPSHOT)
        require(type(value["active"]) is bool and type(value["closed"]) is bool)
        for key in ("completed_recordings", "packets", "samples"):
            number(value[key])
        require(value["samples"] <= MAX_RECORDING_SECONDS * 8000)
        for key in ("elapsed_seconds", "audio_duration_seconds"):
            require(clock(value[key]) <= MAX_RECORDING_SECONDS)
        require(value["audio_duration_seconds"] == value["samples"] / 8000)
        for group, keys in (("sink", SINK), ("reliability", RELIABILITY)):
            for item in mapping(value[group], keys).values():
                number(item)
        return value

    def _idle(self) -> None:
        value = self._snapshot(self.manager.snapshot().as_dict())
        require(value["status"] == "idle" and value["active"] is False)
        require(
            value["completed_recordings"] == 0
            and value["closed"] is False
            and value["error"] is None
        )
        require(
            all(value[key] is None for key in ("recording", "metadata", "started_at", "stopped_at"))
        )

    def _active(self, value: dict[str, Any]) -> None:
        self._snapshot(value)
        require(value["status"] == "recording" and value["active"] is True)
        require(
            value["completed_recordings"] == 0
            and value["closed"] is False
            and value["error"] is None
        )
        require(value["metadata"] is None and value["stopped_at"] is None)
        require(value["recording"] == filename(self.plan.case, value["started_at"]))
        if self._started is not None:
            require(all(value[key] == self._started[key] for key in ("recording", "started_at")))

    def _publish(self, name: str, now: float, snapshot: dict[str, Any] | None = None) -> None:
        require(self._receipts is not None)
        self._receipts.publish(
            name,
            {
                "schema": 1,
                "case": self.plan.case,
                "generation": self.plan.generation,
                "at_monotonic": now,
                "snapshot": snapshot,
            },
        )

    def _operation(self, start: bool) -> RecordingExpectation | dict[str, Any]:
        require(self._lock.acquire(blocking=False))
        try:
            require(self._phase == ("prepared" if start else "recording"))
            # Even a pre-dispatch failure consumes this in-memory operation.
            self._phase = "unconfirmed"
            first = self._now()
            require(self.plan.prepared_at <= first < self.plan.finish_by)
            require(first < self.plan.start_by if start else first >= self.plan.stop_at)
            deadline = min(
                first + (2 if start else 5), self.plan.start_by if start else self.plan.finish_by
            )
            self._binding()
            if start:
                self._idle()
                require(capture_baseline(self.baseline.root, self.plan.case) == self.baseline)
            else:
                self._active(self.manager.snapshot().as_dict())
            self._publish("start-intent" if start else "stop-intent", first)
            # Both fsyncs precede the only dispatch. Changed identity or a late
            # publication withholds it; no later call can replay the intent.
            self._binding()
            if start:
                self._idle()
            else:
                self._active(self.manager.snapshot().as_dict())
            require(self._receipts is not None)
            self._receipts.check()
            require(self._now() < deadline)
            value = self._snapshot(
                (
                    self.manager.start_recording() if start else self.manager.stop_recording()
                ).as_dict()
            )
            self._binding()
            now = self._now()
            require(now < deadline)
            if start:
                self._active(value)
                self._started = value
                self._active(self.manager.snapshot().as_dict())
                result = RecordingExpectation(
                    self.plan.case,
                    self.plan.generation,
                    self.plan.audio_endpoint_sha256,
                    value["started_at"],
                )
            else:
                require(self._started is not None)
                require(value["status"] == "stopped" and value["active"] is False)
                require(
                    value["completed_recordings"] == 1
                    and value["closed"] is False
                    and value["error"] is None
                )
                require(
                    all(value[key] == self._started[key] for key in ("recording", "started_at"))
                )
                require(value["metadata"] == value["recording"] + ".json")
                require(timestamp(value["stopped_at"]) >= timestamp(value["started_at"]))
                require(value == self.manager.snapshot().as_dict())
                result = value
            self._publish("started" if start else "stopped", now, value)
            require(self._now() < deadline)
            self._phase = "recording" if start else "stopped"
            return result
        except Exception:
            raise UnconfirmedOwner(MESSAGE) from None
        finally:
            self._lock.release()

    def start(self) -> RecordingExpectation:
        """Return a bound start receipt, not proof of future recording quality."""
        return self._operation(True)  # type: ignore[return-value]

    def stop(self) -> dict[str, Any]:
        """Return the native stopped snapshot, not file/exit/restoration proof."""
        return self._operation(False)  # type: ignore[return-value]

    def close(self) -> None:
        require(self._lock.acquire(blocking=False))
        try:
            self._phase = "closed"
            if self._receipts is not None:
                self._receipts.close()
        finally:
            self._lock.release()


if __name__ == "__main__":
    raise SystemExit("Offline qualification only; no recording or handoff was started.")
