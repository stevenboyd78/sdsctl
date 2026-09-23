#!/usr/bin/env python3
"""Offline host-owned recording evidence; no native authentication or dispatch.

This independent ledger pins original manifests and returned checkpoint tips.
Native success inputs must already be authenticated by a separately qualified
host adapter. Reload is read-only and never reissues a start or stop operation.
The future installed host must keep this directory outside both App namespaces.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Lock

import supplemental_recording_checkpoints as checkpoints
import supplemental_recording_preservation as preservation
import supplemental_recording_projection as projection
import supplemental_recording_protected as protected
from supplemental_handoff_files import identity
from supplemental_handoff_policy import checksum, clock, encode, identifier

MAX_ENTRIES = checkpoints.MAX_ENTRIES + 5
MAX_BYTES = 8192
MAX_SECONDS = 5.0
MESSAGE = "Recording host binding is unconfirmed; preserve the case and do not replay operations."


class UnconfirmedBinding(ValueError):
    """Fixed message without private paths or native report contents."""


def require(value):
    if not value:
        raise UnconfirmedBinding(MESSAGE)


@dataclass(frozen=True)
class Binding:
    projection: projection.Projection
    source_sha256: str
    plan_sha256: str
    boot_id: str

    def payload(self):
        require(type(self.projection) is projection.Projection)
        protected.evidence.digest(self.source_sha256)
        protected.evidence.digest(self.plan_sha256)
        identifier(self.boot_id)
        return {
            "projection": self.projection.sha256,
            "case": self.projection.host.contract.case_id,
            "host_manifest": self.projection.host.manifest_sha256,
            "native_manifest": self.projection.native.manifest_sha256,
            "host_contract": self.projection.host.contract.sha256,
            "native_contract": self.projection.native.contract.sha256,
            "source": self.source_sha256,
            "plan": self.plan_sha256,
            "boot_id": self.boot_id,
        }


@dataclass(frozen=True)
class State:
    count: int
    sha256: str
    now: float
    generation: str | None = None
    start_by: float | None = None
    finish_by: float | None = None
    expected: protected.evidence.RecordingExpectation | None = None
    tip: checkpoints.Tip | None = None
    acknowledgment: protected.Acknowledgment | None = None
    closed: bool = False
    preservation: preservation.Scope | None = None


def _fields(event, fields):
    protected._mapping(event, {"kind", "now", "boot_id"} | set(fields))


def _apply(state, event, binding):
    require(type(event) is dict)
    clock(event.get("now"))
    require(event.get("boot_id") == binding.boot_id)
    kind, now = event.get("kind"), event["now"]
    if state is None:
        _fields(event, {"binding"})
        require(kind == "prepared" and event["binding"] == binding.payload())
        return State(0, "", now)
    require(now >= state.now and not state.closed)
    state = replace(state, now=now)
    if kind == "start_intent":
        _fields(event, {"generation", "authorization_sha256", "start_by", "finish_by"})
        require(state.generation is None)
        protected.evidence.digest(event["generation"])
        protected.evidence.digest(event["authorization_sha256"])
        clock(event["start_by"])
        clock(event["finish_by"])
        require(now < event["start_by"] < event["finish_by"])
        require(event["start_by"] - now <= 10)
        require(
            event["finish_by"] - now <= binding.projection.host.contract.maximum_recording_seconds
        )
        return replace(
            state,
            generation=event["generation"],
            start_by=event["start_by"],
            finish_by=event["finish_by"],
        )
    if kind == "started":
        _fields(event, {"expected", "success_sha256"})
        require(state.generation is not None and state.expected is None and now < state.start_by)
        protected.evidence.digest(event["success_sha256"])
        expected = protected.evidence.RecordingExpectation(
            **protected._mapping(
                event["expected"], set(protected.evidence.RecordingExpectation.__dataclass_fields__)
            )
        )
        protected.Collector(binding.projection.host)._expected(expected)
        require(expected.generation == state.generation)
        return replace(state, expected=expected)
    if kind == "progress":
        _fields(event, {"tip"})
        require(state.expected is not None and now < state.finish_by)
        tip = checkpoints.Tip(**protected._mapping(event["tip"], {"count", "sha256"}))
        require(tip.count == (1 if state.tip is None else state.tip.count + 1))
        return replace(state, tip=tip)
    if kind == "completed":
        _fields(event, {"acknowledgment"})
        require(state.expected is not None and now < state.finish_by)
        ack = protected.Acknowledgment(
            **protected._mapping(
                event["acknowledgment"], set(protected.Acknowledgment.__dataclass_fields__)
            )
        )
        require(
            (ack.case, ack.generation, ack.started_at, ack.contract_sha256)
            == (
                state.expected.case,
                state.generation,
                state.expected.started_at,
                binding.projection.host.contract.sha256,
            )
        )
        return replace(state, acknowledgment=ack, closed=True)
    if kind == "preserve_unconfirmed_start":
        _fields(event, {"scope"})
        require(state.generation is not None and state.expected is None and state.tip is None)
        raw = protected._mapping(event["scope"], set(preservation.Scope.__dataclass_fields__))
        expected = protected.evidence.RecordingExpectation(
            **protected._mapping(
                raw["expected"], set(protected.evidence.RecordingExpectation.__dataclass_fields__)
            )
        )
        scope = preservation.Scope(**(raw | {"expected": expected}))
        protected.Collector(binding.projection.host)._expected(expected)
        require(expected.generation == state.generation)
        require(scope.native_contract_sha256 == binding.projection.native.contract.sha256)
        return replace(state, preservation=scope, closed=True)
    require(kind == "abandoned")
    _fields(event, set())
    return replace(state, closed=True)


def _location(directory, binding):
    require(type(binding) is Binding)
    binding.payload()
    require(type(directory) is type(Path()))
    protected._path(str(directory))
    # This must be independent host evidence, not a file exposed through /media,
    # App data, source or any other Supervisor-managed tree.
    supervisor = Path("/mnt/data/supervisor")
    require(not directory.is_relative_to(supervisor) and not supervisor.is_relative_to(directory))


def _read(fd, binding, deadline):
    before = identity(os.fstat(fd))
    names = set()
    with os.scandir(fd) as entries:
        for item in entries:
            require(time.monotonic() <= deadline and len(names) < MAX_ENTRIES)
            names.add(item.name)
    require(names == {f"{i:04d}.json" for i in range(len(names))})
    state, previous = None, checksum(binding.payload())
    for i in range(len(names)):
        name = f"{i:04d}.json"
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
        raw = protected.evidence.read_bytes(fd, name, limit=MAX_BYTES, deadline=deadline)
        require(identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) == identity(info))
        value = json.loads(
            raw,
            object_pairs_hook=protected.evidence.unique,
            parse_constant=protected.evidence.reject_constant,
        )
        protected._mapping(value, {"schema", "kind", "previous", "event"})
        require(type(value["schema"]) is int and value["schema"] == 1)
        require(value["kind"] == "finite-recording-host-binding" and encode(value) == raw)
        require(value["previous"] == previous)
        state = _apply(state, value["event"], binding)
        previous = hashlib.sha256(raw).hexdigest()
        state = replace(state, count=i + 1, sha256=previous)
    require(identity(os.fstat(fd)) == before and time.monotonic() <= deadline)
    return state


def load(directory: Path, binding: Binding) -> State:
    """Read-only recovery evidence. Does not return an actionable controller.

    Every durable intent is consumed, including when its publication return was
    lost. A complete checkpoint entry not pinned in this ledger is NOT adopted.
    Changing host boot ID, original plan/source/projection or any entry refuses
    the binding. An actual host reboot needs separate administrative recovery.
    """
    try:
        _location(directory, binding)
        deadline = time.monotonic() + MAX_SECONDS
        with protected._private_directory(directory, exclusive=False) as fd:
            result = _read(fd, binding, deadline)
        require(result is not None and time.monotonic() <= deadline)
        return result
    except Exception:
        raise UnconfirmedBinding(MESSAGE) from None


class Ledger:
    """One live writer for a NEW host ledger. Reload deliberately cannot resume.

    A caller must authenticate native successful-return evidence BEFORE passing
    started/completed inputs. Hashes alone do not authenticate those assertions.
    No method contacts a recorder, signals a process or grants restoration.
    """

    def __init__(self, directory: Path, binding: Binding, *, now: float):
        self.directory, self.binding = directory, binding
        self.state, self._poisoned, self._lock = None, False, Lock()
        self._directory_identity = None
        try:
            self._append("prepared", now, binding=binding.payload())
        except Exception:
            self._poisoned = True
            raise UnconfirmedBinding(MESSAGE) from None

    def _append(self, kind, now, **fields):
        require(self._lock.acquire(blocking=False))
        try:
            require(not self._poisoned)
            _location(self.directory, self.binding)
            deadline = time.monotonic() + MAX_SECONDS
            with protected._private_directory(self.directory, exclusive=True) as fd:
                current = identity(os.fstat(fd))[:6]
                if self._directory_identity is None:
                    self._directory_identity = current
                require(current == self._directory_identity)
                before = _read(fd, self.binding, deadline)
                require(before == self.state)
                require(before is None or before.count < MAX_ENTRIES)
                event = {"kind": kind, "now": now, "boot_id": self.binding.boot_id} | fields
                after = _apply(before, event, self.binding)
                count = 0 if before is None else before.count
                raw = encode(
                    {
                        "schema": 1,
                        "kind": "finite-recording-host-binding",
                        "previous": checksum(self.binding.payload())
                        if before is None
                        else before.sha256,
                        "event": event,
                    }
                )
                require(len(raw) <= MAX_BYTES and time.monotonic() <= deadline)
                output = os.open(
                    f"{count:04d}.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=fd,
                )
                with os.fdopen(output, "wb") as stream:
                    require(stream.write(raw) == len(raw))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(fd)
                after = replace(after, count=count + 1, sha256=hashlib.sha256(raw).hexdigest())
                require(_read(fd, self.binding, deadline) == after)
            require(time.monotonic() <= deadline)
            self.state = after
            return after
        except BaseException as error:
            self._poisoned = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedBinding(MESSAGE) from None
        finally:
            self._lock.release()

    def start_intent(self, *, now, generation, authorization_sha256, start_by, finish_by):
        return self._append(
            "start_intent",
            now,
            generation=generation,
            authorization_sha256=authorization_sha256,
            start_by=start_by,
            finish_by=finish_by,
        )

    def started(self, expected, *, now, success_sha256):
        require(type(expected) is protected.evidence.RecordingExpectation)
        return self._append(
            "started", now, expected=asdict(expected), success_sha256=success_sha256
        )

    def progress(self, directory, collector, tip, *, now):
        """Pin only a returned tip after strict complete checkpoint replay."""
        try:
            require(
                type(collector) is protected.Collector
                and collector.stored == self.binding.projection.host
            )
            require(self.state is not None and self.state.expected is not None)
            require(
                not directory.is_relative_to(self.directory)
                and not self.directory.is_relative_to(directory)
            )
            checkpoints.load_progress(
                directory,
                collector,
                self.state.expected,
                expected_tip=tip,
                previous_tip=self.state.tip,
            )
            return self._append("progress", now, tip=asdict(tip))
        except BaseException as error:
            self._poisoned = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedBinding(MESSAGE) from None

    def completed(self, acknowledgment, *, now):
        require(type(acknowledgment) is protected.Acknowledgment)
        return self._append("completed", now, acknowledgment=asdict(acknowledgment))

    def abandon(self, *, now):
        return self._append("abandoned", now)

    def preserve_unconfirmed_start(self, scope, *, now):
        """Close a lost-start case with naming scope only, NEVER acknowledgment.

        Caller must qualify the original native receipt path/binding and obtain
        Scope through read_scope. This entry cannot be followed by started,
        progress or completed. Retained-file verification and exact exit remain
        independent gates. A failed publication is not retried.
        """
        require(type(scope) is preservation.Scope)
        return self._append("preserve_unconfirmed_start", now, scope=asdict(scope))


if __name__ == "__main__":
    raise SystemExit("Offline host binding only; no recording or recovery operation is enabled.")
