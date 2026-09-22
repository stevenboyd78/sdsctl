#!/usr/bin/env python3
"""Offline, journaled policy for one separate-App handoff; NOT a host adapter.

No subprocess, network, scanner, Supervisor or Docker operations live here.
An eventual independently supervised host adapter must qualify observations and
dispatch each returned action once. Reopening a journal reconciles observations;
it NEVER re-emits an action from an earlier process, even if dispatch was lost.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

NORMAL = "local_sds200_mimic_acceptance"
CANDIDATE = "local_sds200_supplemental_acceptance"
MAX_EVENTS = 24
MAX_BYTES = 8192
REQUEST_SECONDS = 300
COMMAND_SECONDS = 120
TRIAL_SECONDS = 720
TOTAL_SECONDS = 1500


class UnsafeHandoff(ValueError):
    """Invalid or uncertain private state; retain evidence for review."""


def require(condition: bool) -> None:
    if not condition:
        raise UnsafeHandoff("Handoff evidence is invalid; preserve the case for review.")


def clock(value: object) -> None:
    require(type(value) in (int, float))
    require(math.isfinite(value) and value >= 0)  # type: ignore[arg-type,operator]


def digest(value: object) -> None:
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None)


def identifier(value: str, *, case: bool = False) -> None:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise UnsafeHandoff("Invalid handoff identifier.") from None
    require(value == parsed.hex and (not case or parsed.version == 4))


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def checksum(value: object) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


@dataclass(frozen=True)
class App:
    # pin covers verified image/source/options/ports/protected profile identities.
    # STOPPED means Supervisor stopped AND container/process exit proven; an API
    # error, missing response or a stopped-looking dashboard is only UNKNOWN.
    pin: str
    state: str
    generation: str | None = None
    healthy: bool | None = None
    recording: bool | None = None

    def __post_init__(self) -> None:
        digest(self.pin)
        require(self.state in ("running", "stopped", "unknown"))
        for value in (self.healthy, self.recording):
            require(value is None or type(value) is bool)
        if self.state == "running":
            digest(self.generation)
        else:
            require(self.generation is None and self.healthy is None and self.recording is None)


@dataclass(frozen=True)
class Observation:
    sampled_at: float
    normal: App
    candidate: App
    other_owners_stopped: bool
    jobs_idle: bool
    core_running: bool

    def __post_init__(self) -> None:
        clock(self.sampled_at)
        require(type(self.normal) is App and type(self.candidate) is App)
        for value in (self.other_owners_stopped, self.jobs_idle, self.core_running):
            require(type(value) is bool)


@dataclass(frozen=True)
class Action:
    operation: str
    slug: str
    case_id: str
    # Adapter must re-observe these preconditions immediately before dispatch.
    preconditions_sha256: str


def preconditions(observation: Observation) -> str:
    """Time changes on re-observation; all actual safety preconditions must match."""
    values = asdict(observation)
    del values["sampled_at"]
    return checksum(values)


@dataclass(frozen=True)
class ProcessRecord:
    slug: str
    generation: str
    container_id: str
    pid: int
    start_ticks: int

    def __post_init__(self) -> None:
        require(self.slug in (NORMAL, CANDIDATE))
        digest(self.generation)
        digest(self.container_id)
        require(type(self.pid) is int and 1 < self.pid < 2**31)
        require(type(self.start_ticks) is int and 0 < self.start_ticks < 2**64)


@dataclass(frozen=True)
class State:
    phase: str = "prepared"
    deadline: float = 0
    trial_deadline: float = 0
    candidate_generation: str | None = None
    restored_generation: str | None = None
    finish_requested: bool = False
    reason: str | None = None
    # phase, immutable CLI container ID, immutable Docker exec ID. Persisted
    # before exec/start; a restart inspects these IDs, never starts them again.
    executions: tuple[tuple[str, str, str], ...] = ()
    # Independent exec inspection confirmed process exit, not App operation
    # success. Persist before Docker eventually expires its execution metadata.
    completed_executions: tuple[tuple[str, int], ...] = ()
    # The two original scanner-owning init processes, never the restored normal
    # incarnation. Binding is not exit. Only an independently observed pidfd exit
    # can be recorded; API 404/missing PID does not establish this event.
    processes: tuple[ProcessRecord, ...] = ()
    exited_processes: tuple[str, ...] = ()  # generation digests, not recycled PIDs


class Machine:
    """Pure decision policy; replay reconstructs state without executing actions."""

    def __init__(self, case_id: str, boot_id: str, now: float, baseline: Observation):
        identifier(case_id, case=True)
        identifier(boot_id)
        self.fresh(now, baseline)
        require(
            baseline.normal.state == "running"
            and baseline.normal.healthy is True
            and baseline.normal.recording is False
            and baseline.candidate.state == "stopped"
            and baseline.other_owners_stopped
            and baseline.jobs_idle
            and baseline.core_running
        )
        self.case_id, self.boot_id = case_id, boot_id
        self.baseline = baseline
        self.created_at = self.last_at = now
        self.hard_deadline = now + TOTAL_SECONDS
        self.state = State(deadline=now + REQUEST_SECONDS)

    @staticmethod
    def fresh(now: float, observation: Observation) -> None:
        clock(now)
        require(type(observation) is Observation and 0 <= now - observation.sampled_at <= 2)

    def event(self, event: dict[str, Any]) -> Action | None:
        kind = event.get("kind")
        require(
            kind
            in (
                "request",
                "finish",
                "observe",
                "bind_execution",
                "execution_completed",
                "bind_process",
                "process_exited",
                "tick",
            )
        )
        expected = {"kind", "now", "boot_id"}
        if kind == "observe":
            expected.add("observation")
        elif kind == "bind_execution":
            expected.update(("container_id", "execution_id"))
        elif kind == "execution_completed":
            expected.update(("execution_id", "exit_code"))
        elif kind == "bind_process":
            expected.add("process")
        elif kind == "process_exited":
            expected.add("generation")
        require(set(event) == expected)
        now = event["now"]
        clock(now)
        require(now >= self.last_at)
        identifier(event["boot_id"])
        require(self.state.phase not in ("complete", "review"))
        if event["boot_id"] != self.boot_id:
            self.state = replace(self.state, phase="review", reason="host_boot_changed")
        elif now >= self.hard_deadline:
            self.state = replace(self.state, phase="review", reason="hard_deadline")
        elif kind == "tick":
            # A qualified host clock can expire the case even when every remote
            # observation fails. It cannot create an App action or prove recovery.
            if self.state.phase == "candidate_running":
                if now >= self.state.trial_deadline + COMMAND_SECONDS:
                    self.review("recovery_observation_deadline")
            elif now >= self.state.deadline:
                self.review("phase_deadline")
        elif kind == "bind_process":
            value = event["process"]
            require(type(value) is dict and set(value) == set(ProcessRecord.__dataclass_fields__))
            record = ProcessRecord(**value)
            require(len(self.state.processes) < 2)
            require(
                not any(
                    old.slug == record.slug
                    or old.generation == record.generation
                    or old.container_id == record.container_id
                    for old in self.state.processes
                )
            )
            if record.slug == NORMAL:
                require(self.state.phase in ("prepared", "requested", "stopping_normal"))
                require(record.generation == self.baseline.normal.generation)
            else:
                require(
                    self.state.phase
                    in (
                        "starting_candidate",
                        "candidate_running",
                        "stopping_candidate",
                    )
                )
                require(self.state.candidate_generation in (None, record.generation))
            deadline = (
                self.state.trial_deadline + COMMAND_SECONDS
                if self.state.phase == "candidate_running"
                else self.state.deadline
            )
            require(now < deadline)
            self.state = replace(
                self.state,
                processes=(*self.state.processes, record),
                candidate_generation=(
                    record.generation
                    if record.slug == CANDIDATE
                    else self.state.candidate_generation
                ),
            )
        elif kind == "process_exited":
            digest(event["generation"])
            require(any(r.generation == event["generation"] for r in self.state.processes))
            require(event["generation"] not in self.state.exited_processes)
            self.state = replace(
                self.state,
                exited_processes=(*self.state.exited_processes, event["generation"]),
            )
        elif kind == "execution_completed":
            digest(event["execution_id"])
            require(any(eid == event["execution_id"] for _, _, eid in self.state.executions))
            require(
                not any(eid == event["execution_id"] for eid, _ in self.state.completed_executions)
            )
            require(type(event["exit_code"]) is int and 0 <= event["exit_code"] <= 255)
            self.state = replace(
                self.state,
                completed_executions=(
                    *self.state.completed_executions,
                    (event["execution_id"], event["exit_code"]),
                ),
            )
        elif kind == "bind_execution":
            require(
                self.state.phase
                in (
                    "stopping_normal",
                    "starting_candidate",
                    "stopping_candidate",
                    "starting_normal",
                )
            )
            require(now < self.state.deadline)
            digest(event["container_id"])
            digest(event["execution_id"])
            require(not any(phase == self.state.phase for phase, _, _ in self.state.executions))
            require(not any(eid == event["execution_id"] for _, _, eid in self.state.executions))
            self.state = replace(
                self.state,
                executions=(
                    *self.state.executions,
                    (self.state.phase, event["container_id"], event["execution_id"]),
                ),
            )
        elif kind == "request":
            require(self.state.phase == "prepared")
            if now >= self.state.deadline:
                self.state = replace(self.state, phase="review", reason="request_expired")
            else:
                self.state = replace(self.state, phase="requested")
        elif kind == "finish":
            require(self.state.phase == "candidate_running" and not self.state.finish_requested)
            self.state = replace(self.state, finish_requested=True)
        else:
            observation = decode_observation(event["observation"])
            self.fresh(now, observation)
            action = self.observe(now, observation)
            self.last_at = now
            return action
        self.last_at = now
        return None

    def review(self, reason: str) -> None:
        self.state = replace(self.state, phase="review", reason=reason)

    def dispatch(self, phase: str, now: float, observation: Observation) -> Action:
        operations = {
            "stopping_normal": ("stop", NORMAL),
            "starting_candidate": ("start", CANDIDATE),
            "stopping_candidate": ("stop", CANDIDATE),
            "starting_normal": ("start", NORMAL),
        }
        operation, slug = operations[phase]
        self.state = replace(
            self.state, phase=phase, deadline=min(now + COMMAND_SECONDS, self.hard_deadline)
        )
        if phase == "starting_candidate":
            self.state = replace(self.state, trial_deadline=now + TRIAL_SECONDS)
        return Action(operation, slug, self.case_id, preconditions(observation))

    def observe(self, now: float, obs: Observation) -> Action | None:
        state = self.state
        normal, candidate = obs.normal, obs.candidate
        if normal.pin != self.baseline.normal.pin or candidate.pin != self.baseline.candidate.pin:
            self.review("protected_identity_changed")
        elif not obs.other_owners_stopped:
            self.review("other_owner_not_confirmed_stopped")
        elif not obs.core_running:
            self.review("core_not_confirmed_running")
        elif state.phase != "candidate_running" and now >= state.deadline:
            self.review("phase_deadline")
        elif state.phase == "candidate_running" and now >= state.trial_deadline + COMMAND_SECONDS:
            self.review("recovery_observation_deadline")
        elif normal.state == "unknown" or candidate.state == "unknown":
            return None
        elif normal.recording is True or candidate.recording is True:
            self.review("recording_active")
        elif state.phase in ("prepared", "requested", "stopping_normal") and (
            normal.state == "running" and normal.generation != self.baseline.normal.generation
        ):
            self.review("normal_generation_changed")
        elif state.phase in ("prepared", "requested", "stopping_normal") and (
            candidate.state != "stopped"
        ):
            self.review("candidate_started_externally")
        elif state.phase in ("starting_candidate", "candidate_running", "stopping_candidate") and (
            normal.state != "stopped"
        ):
            self.review("normal_started_externally")
        elif state.phase == "starting_normal" and candidate.state != "stopped":
            self.review("candidate_reappeared")
        elif (
            state.candidate_generation is not None
            and candidate.state == "running"
            and (candidate.generation != state.candidate_generation)
        ):
            self.review("candidate_generation_changed")
        elif (
            state.restored_generation is not None
            and normal.state == "running"
            and (normal.generation != state.restored_generation)
        ):
            self.review("restored_generation_changed")
        elif not obs.jobs_idle:
            return None
        elif state.phase == "prepared":
            if normal.state != "running":
                self.review("normal_stopped_externally")
        elif state.phase == "requested":
            if normal.state != "running":
                self.review("normal_stopped_externally")
            elif normal.healthy is True and normal.recording is False:
                return self.dispatch("stopping_normal", now, obs)
        elif state.phase == "stopping_normal":
            if normal.state == "stopped":
                return self.dispatch("starting_candidate", now, obs)
        elif state.phase == "starting_candidate":
            if candidate.state == "running":
                # Bind the first observed generation even if not yet healthy.
                if state.candidate_generation is None:
                    self.state = replace(state, candidate_generation=candidate.generation)
                if candidate.healthy is False and candidate.recording is False:
                    return self.dispatch("stopping_candidate", now, obs)
                if candidate.healthy is True and candidate.recording is False:
                    self.state = replace(self.state, phase="candidate_running")
            elif state.candidate_generation is not None:
                # A previously observed candidate has now conclusively exited.
                return self.dispatch("starting_normal", now, obs)
        elif state.phase == "candidate_running":
            if candidate.state == "stopped":
                return self.dispatch("starting_normal", now, obs)
            if candidate.recording is False and (
                state.finish_requested or now >= state.trial_deadline or candidate.healthy is False
            ):
                return self.dispatch("stopping_candidate", now, obs)
        elif state.phase == "stopping_candidate":
            if candidate.state == "stopped":
                return self.dispatch("starting_normal", now, obs)
        elif state.phase == "starting_normal" and normal.state == "running":
            if normal.generation == self.baseline.normal.generation:
                self.review("old_normal_generation_reappeared")
            else:
                if state.restored_generation is None:
                    self.state = replace(state, restored_generation=normal.generation)
                if normal.healthy is True and normal.recording is False:
                    self.state = replace(self.state, phase="complete", reason="restored")
        return None


def decode_observation(value: Any) -> Observation:
    require(type(value) is dict and set(value) == set(Observation.__dataclass_fields__))
    try:
        return Observation(
            **{**value, "normal": App(**value["normal"]), "candidate": App(**value["candidate"])}
        )
    except (TypeError, KeyError):
        raise UnsafeHandoff("Invalid handoff observation.") from None


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject_constant(_value: str) -> None:
    raise UnsafeHandoff("Non-finite handoff evidence.")


class Journal:
    """Exclusive, private append-only evidence, bounded and fail-closed on damage.

    The caller creates a NEW 0700 directory. An incomplete write stays in place;
    corruption or uncertainty never triggers deletion/reinitialization.
    """

    def __init__(self, path: Path):
        require(path.is_absolute() and ".." not in path.parts)
        descriptor = os.open("/", os.O_DIRECTORY | os.O_RDONLY)
        try:
            for component in path.parts[1:]:
                child = os.open(
                    component, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        self.fd = descriptor
        self.path = path
        self.entries: list[dict[str, Any]] = []
        self.machine: Machine | None = None
        try:
            names = sorted(os.listdir(self.fd))
            require(len(names) <= MAX_EVENTS and names == [self.name(i) for i in range(len(names))])
            for name in names:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                try:
                    before = os.fstat(fd)
                    require(
                        stat.S_ISREG(before.st_mode)
                        and before.st_nlink == 1
                        and before.st_uid == os.getuid()
                        and stat.S_IMODE(before.st_mode) == 0o600
                        and 0 < before.st_size <= MAX_BYTES
                    )
                    raw = os.read(fd, MAX_BYTES + 1)
                    after = os.fstat(fd)
                    named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                    require(
                        len(raw) == before.st_size
                        and all(
                            getattr(before, field) == getattr(after, field) == getattr(named, field)
                            for field in (
                                "st_ino",
                                "st_dev",
                                "st_size",
                                "st_mtime_ns",
                                "st_ctime_ns",
                            )
                        )
                    )
                    entry = json.loads(
                        raw, object_pairs_hook=unique, parse_constant=reject_constant
                    )
                    require(
                        type(entry) is dict
                        and set(entry) == {"schema", "previous", "event"}
                        and type(entry["schema"]) is int
                        and entry["schema"] == 1
                    )
                    require(
                        entry["previous"] == checksum(self.entries[-1] if self.entries else None)
                    )
                    self.apply(entry["event"])
                    self.entries.append(entry)
                finally:
                    os.close(fd)
            self.check_directory()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def name(index: int) -> str:
        return f"{index:04d}.json"

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def check_directory(self) -> None:
        """A renamed/replaced journal must not become a second case at this path."""
        require(self.fd >= 0)
        descriptor = os.open("/", os.O_DIRECTORY | os.O_RDONLY)
        try:
            for component in self.path.parts[1:]:
                child = os.open(
                    component, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            current, opened = os.fstat(descriptor), os.fstat(self.fd)
            require(
                (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
                and current.st_uid == os.getuid()
                and stat.S_IMODE(current.st_mode) == 0o700
            )
        finally:
            os.close(descriptor)

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def apply(self, event: dict[str, Any]) -> Action | None:
        require(type(event) is dict)
        if self.machine is None:
            require(
                set(event) == {"kind", "case_id", "boot_id", "now", "observation"}
                and event["kind"] == "prepare"
            )
            self.machine = Machine(
                event["case_id"],
                event["boot_id"],
                event["now"],
                decode_observation(event["observation"]),
            )
            return None
        before = self.machine.state
        action = self.machine.event(event)
        require(self.machine.state != before)
        return action

    def append(self, event: dict[str, Any]) -> Action | None:
        require(self.fd >= 0 and len(self.entries) < MAX_EVENTS)
        try:
            self.check_directory()
            require(sorted(os.listdir(self.fd)) == [self.name(i) for i in range(len(self.entries))])
        except BaseException:
            self.close()
            raise
        # Validate on a replayed copy; a failed or no-op event cannot modify the
        # live policy. Replayed actions are deliberately discarded.
        original = self.machine
        self.machine = None
        try:
            for entry in self.entries:
                self.apply(entry["event"])
            if self.machine is not None and event.get("kind") in ("observe", "tick"):
                before = self.machine.state
                action = self.machine.event(event)
                if self.machine.state == before:
                    self.machine = original
                    return None
            else:
                action = self.apply(event)
            entry = {
                "schema": 1,
                "previous": checksum(self.entries[-1] if self.entries else None),
                "event": event,
            }
            raw = encode(entry)
            require(len(raw) <= MAX_BYTES)
            try:
                fd = os.open(
                    self.name(len(self.entries)),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self.fd,
                )
                with os.fdopen(fd, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                os.fsync(self.fd)
                self.check_directory()
            except BaseException:
                # Publication uncertainty consumes this controller too.
                self.close()
                raise
            self.entries.append(json.loads(raw))
            return action
        except BaseException:
            self.machine = original
            raise


if __name__ == "__main__":
    raise SystemExit("Offline policy only: a qualified independent host adapter is still required.")
