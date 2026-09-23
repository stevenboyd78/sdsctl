#!/usr/bin/env python3
"""Durable one-use private exec intents; no Docker dispatch or recording start.

A qualified host must bind image/source/container/exec evidence independently.
This ledger records create intent BEFORE an exec/create attempt, the returned
exec ID before inspecting it, and attach intent BEFORE the fixed start request.
Lost publication/HTTP returns consume the case; reopening is read-only. This
does not replace the separate durable recording-begin intent or exit evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock, get_ident

import supplemental_handoff_process as process
import supplemental_recording_binding as binding
import supplemental_recording_execution as execution

MESSAGE = "Finite recording exec intent is unconfirmed; preserve this case and do not dispatch."
MAX_BYTES = 4096


class UnconfirmedDispatch(ValueError):
    """Existing intents are consumed, never a request to recreate or reattach."""


def require(value):
    if not value:
        raise UnconfirmedDispatch(MESSAGE)


@dataclass(frozen=True)
class Pins:
    host: binding.Binding
    command: execution.Command
    generation: str
    init: process.ProcessIdentity

    def payload(self):
        require(type(self.host) is binding.Binding and type(self.command) is execution.Command)
        require(type(self.init) is process.ProcessIdentity)
        self.command.argv()
        execution._digest(self.generation)
        require(self.command.source_sha256 == self.host.source_sha256)
        return {
            "host": self.host.payload(),
            "command": asdict(self.command),
            "generation": self.generation,
            "init": asdict(self.init),
        }


@dataclass(frozen=True)
class State:
    count: int
    sha256: str
    at: float
    execution_id: str | None
    phase: str


def _location(directory, pins):
    require(type(pins) is Pins)
    pins.payload()
    binding._location(directory, pins.host)  # Host-only, outside App mount roots.


def _event(state, event, pins):
    fields = binding.protected._mapping
    require(type(event) is dict)
    binding.clock(event.get("at"))
    at = event["at"]
    require(at < pins.command.ready_by)
    if state is None:
        fields(event, {"kind", "at", "pins"})
        require(event["kind"] == "create_intent" and event["pins"] == pins.payload())
        require(0 < pins.command.ready_by - at <= 600)
        return State(1, "", at, None, "create_intent")
    require(at >= state.at)
    if state.phase == "create_intent":
        fields(event, {"kind", "at", "execution_id"})
        require(event["kind"] == "created")
        execution._digest(event["execution_id"])
        return State(2, "", at, event["execution_id"], "created")
    fields(event, {"kind", "at", "execution_id", "inspection_sha256"})
    require(state.phase == "created" and event["kind"] == "attach_intent")
    require(event["execution_id"] == state.execution_id)
    execution._digest(event["inspection_sha256"])
    return State(3, "", at, state.execution_id, "attach_intent")


def _read(fd, pins, deadline):
    before = binding.identity(os.fstat(fd))
    names = []
    with os.scandir(fd) as entries:
        for item in entries:
            require(time.monotonic() < deadline and len(names) < 3)
            names.append(item.name)
    require(sorted(names) == [f"{i:04d}.json" for i in range(len(names))])
    state, previous = None, binding.checksum(pins.payload())
    for index in range(len(names)):
        name = f"{index:04d}.json"
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        require(info.st_uid == os.geteuid() and info.st_mode & 0o7777 == 0o600)
        raw = binding.protected.evidence.read_bytes(fd, name, limit=MAX_BYTES, deadline=deadline)
        require(
            binding.identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
            == binding.identity(info)
        )
        value = json.loads(
            raw,
            object_pairs_hook=binding.protected.evidence.unique,
            parse_constant=binding.protected.evidence.reject_constant,
        )
        binding.protected._mapping(value, {"schema", "kind", "previous", "event"})
        require(type(value["schema"]) is int and value["schema"] == 1)
        require(value["kind"] == "finite-recording-exec-intents" and binding.encode(value) == raw)
        require(value["previous"] == previous)
        following = _event(state, value["event"], pins)
        previous = hashlib.sha256(raw).hexdigest()
        state = State(
            following.count, previous, following.at, following.execution_id, following.phase
        )
    require(binding.identity(os.fstat(fd)) == before and time.monotonic() < deadline)
    return state


def load(directory: Path, pins: Pins):
    """Read-only reconciliation. No capability to recreate an exec or attach."""
    try:
        _location(directory, pins)
        deadline = time.monotonic() + 2
        with binding.protected._private_directory(directory, exclusive=False) as fd:
            result = _read(fd, pins, deadline)
        require(result is not None and time.monotonic() < deadline)
        return result
    except Exception:
        raise UnconfirmedDispatch(MESSAGE) from None


class Claim:
    """New empty host directory only; publication must return before dispatch.

    The caller retains its exact init witness before opening this claim. Successful
    construction consumes create intent; a caller can then attempt only the fixed
    create body once. This object does not make that network call or grant source
    authorization. After HTTP response, created() records the ID before any
    inspect. attach_intent() requires an independently obtained created-inspect
    reply. Actual begin and native reports use their separate host ledger.

    The qualified host plan must fix this directory for the case. Choosing a
    different empty directory does not prove a previous create never happened.
    Local I/O deadlines reject late results; an independent outer supervisor
    must still bound blocked filesystem calls.
    """

    def __init__(self, directory, pins, init_witness):
        self.directory, self.pins, self.witness = directory, pins, init_witness
        self.state, self.identity = None, None
        self.owner, self.lock, self.poisoned = (os.getpid(), get_ident()), Lock(), False
        try:
            require(type(pins) is Pins)
            self._append({"kind": "create_intent", "pins": pins.payload()})
        except BaseException as error:
            self.poisoned = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedDispatch(MESSAGE) from None

    def _live(self):
        require(not self.poisoned and self.owner == (os.getpid(), get_ident()))
        _location(self.directory, self.pins)
        require(
            type(self.witness) is process.ProcessWitness and self.witness.identity == self.pins.init
        )
        require(not self.witness.exited())
        require(
            process.read_identity(self.pins.init.pid, self.pins.init.container_id) == self.pins.init
        )
        require(time.monotonic() < self.pins.command.ready_by)

    def _append(self, values):
        require(self.lock.acquire(blocking=False))
        try:
            self._live()
            deadline = min(time.monotonic() + 2, self.pins.command.ready_by)
            with binding.protected._private_directory(self.directory, exclusive=True) as fd:
                identity = binding.identity(os.fstat(fd))[:6]
                require(self.identity is None or self.identity == identity)
                self.identity = identity
                require(_read(fd, self.pins, deadline) == self.state)
                at = time.monotonic()
                event = dict(values, at=at)
                following = _event(self.state, event, self.pins)
                raw = binding.encode(
                    {
                        "schema": 1,
                        "kind": "finite-recording-exec-intents",
                        "previous": self.state.sha256
                        if self.state is not None
                        else binding.checksum(self.pins.payload()),
                        "event": event,
                    }
                )
                require(len(raw) <= MAX_BYTES and time.monotonic() < deadline)
                index = 0 if self.state is None else self.state.count
                output = os.open(
                    f"{index:04d}.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=fd,
                )
                with os.fdopen(output, "wb") as stream:
                    require(stream.write(raw) == len(raw))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(fd)
                result = State(
                    following.count,
                    hashlib.sha256(raw).hexdigest(),
                    at,
                    following.execution_id,
                    following.phase,
                )
                require(_read(fd, self.pins, deadline) == result)
            self._live()
            require(time.monotonic() < deadline)
            self.state = result
            return result
        except BaseException as error:
            self.poisoned = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedDispatch(MESSAGE) from None
        finally:
            self.lock.release()

    def created(self, execution_id):
        return self._append({"kind": "created", "execution_id": execution_id})

    def check(self):
        """Recheck the live original directory/tip before an external request."""
        try:
            self._live()
            deadline = min(time.monotonic() + 2, self.pins.command.ready_by)
            with binding.protected._private_directory(self.directory, exclusive=False) as fd:
                require(binding.identity(os.fstat(fd))[:6] == self.identity)
                require(self.state is not None and _read(fd, self.pins, deadline) == self.state)
            self._live()
            require(time.monotonic() < deadline)
        except BaseException as error:
            self.poisoned = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedDispatch(MESSAGE) from None

    def attach_intent(self, inspected):
        try:
            self._live()
            require(self.state is not None and self.state.phase == "created")
            observed = execution.inspect(
                inspected,
                execution_id=self.state.execution_id,
                container_id=self.pins.init.container_id,
                command=self.pins.command,
            )
            require(observed.phase == "created")
            return self._append(
                {
                    "kind": "attach_intent",
                    "execution_id": self.state.execution_id,
                    "inspection_sha256": binding.checksum(inspected),
                }
            )
        except BaseException as error:
            self.poisoned = True
            if not isinstance(error, Exception):
                raise
            raise UnconfirmedDispatch(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Private one-use intents only; no host action enabled.")
