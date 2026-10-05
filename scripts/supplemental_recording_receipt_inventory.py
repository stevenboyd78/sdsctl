#!/usr/bin/env python3
"""Bounded append-only native receipt inventory; never an acknowledgment.

Uninstalled groundwork for the active App input policy. The caller must lend
the original directory and independently authenticated begin binding. This
reader owns no descriptors, discovers no process, and authorizes no action.
Use at authenticated checkpoints, not to infer success from a disk publication.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from threading import Lock, get_ident

import supplemental_recording_channel as channel
import supplemental_recording_preservation as preservation

owner, protected = channel.owner, channel.protected
identity = preservation.identity
NAMES = ("prepared.json", "start-intent.json", "started.json", "stop-intent.json", "stopped.json")
MESSAGE = "Native receipt inventory is unconfirmed; preserve evidence and do not retry."


class UnconfirmedInventory(ValueError):
    """No successful return, writer exit, cleanup or recovery is established."""


def require(value):
    if not value:
        raise UnconfirmedInventory(MESSAGE)


@dataclass(frozen=True)
class Inventory:
    """Immutable file observations only; not a native return or completion."""

    directory: tuple[int, ...]
    records: tuple[tuple[str, bytes, tuple[int, ...]], ...]


def _sequence(records, binding, intent_at, sampled_at):
    """Validate the closed native-owner format without inventing a return."""
    if not records:
        return
    contract = binding.stored.contract
    prepared = preservation._canonical(records[0][1])
    protected._mapping(prepared, {"schema", "plan", "root_sha256", "baseline_sha256"})
    plan = owner.Plan(**protected._mapping(prepared["plan"], set(owner.Plan.__dataclass_fields__)))
    require(prepared["root_sha256"] == contract.root_sha256)
    require(prepared["baseline_sha256"] == contract.baseline_sha256)
    require(
        (plan.case, plan.generation, plan.audio_endpoint_sha256)
        == (contract.case_id, binding.generation, contract.audio_endpoint_sha256)
    )
    require(intent_at <= plan.prepared_at <= sampled_at)
    require(plan.start_by <= binding.start_by and plan.finish_by <= binding.finish_by)
    require(plan.finish_by - plan.prepared_at <= contract.maximum_recording_seconds)
    previous_at, started = plan.prepared_at, None
    for name, raw, _ in records[1:]:
        value = preservation._canonical(raw)
        protected._mapping(value, {"schema", "case", "generation", "at_monotonic", "snapshot"})
        require((value["case"], value["generation"]) == (plan.case, plan.generation))
        at = owner.clock(value["at_monotonic"])
        require(previous_at <= at <= sampled_at and at < plan.finish_by)
        require(at < plan.start_by if name in NAMES[1:3] else at >= plan.stop_at)
        previous_at = at
        if name in ("start-intent.json", "stop-intent.json"):
            require(value["snapshot"] is None)
            continue
        snapshot = owner.FiniteRecordingOwner._snapshot(value["snapshot"])
        require(snapshot["closed"] is False and snapshot["error"] is None)
        require(
            snapshot["recording"] == protected.evidence.filename(plan.case, snapshot["started_at"])
        )
        if name == "started.json":
            require(snapshot["status"] == "recording" and snapshot["active"] is True)
            require(snapshot["completed_recordings"] == 0)
            require(snapshot["metadata"] is None and snapshot["stopped_at"] is None)
            started = snapshot
        else:
            require(started is not None)
            require(snapshot["status"] == "stopped" and snapshot["active"] is False)
            require(snapshot["completed_recordings"] == 1)
            require(all(snapshot[key] == started[key] for key in ("recording", "started_at")))
            require(snapshot["metadata"] == snapshot["recording"] + ".json")
            require(
                protected.evidence.timestamp(snapshot["stopped_at"])
                >= protected.evidence.timestamp(snapshot["started_at"])
            )


class ReceiptInventory:
    """Retain one original append-only prefix under fixed begin/finish bounds.

    Empty and incomplete *sequences* are observations, not success or failure of
    a recorder. Individual files must be complete canonical records. Previously
    seen bytes and inodes cannot change/disappear. New records may only extend
    the ordered five-file prefix. A raced/partial publication refuses the sample
    and consumes this reader; it is never repaired, retried or silently adopted.

    Directory ancestry, actual original descriptor custody, live actors, full
    source/runtime qualification and independently authenticated native returns
    remain caller obligations. Reconstructing this object cannot substitute for
    retaining the original reader. No installed or active service selects it.
    """

    def __init__(self, binding, *, intent_at, directory_identity):
        self.failed = False
        self.owner, self.lock = (os.getpid(), get_ident()), Lock()
        self.inventory = self._original_inventory = self._inventory_pins = None
        try:
            require(type(binding) is channel.Binding)
            payload = channel.encode(binding.payload())
            owner.clock(intent_at)
            require(0 < binding.start_by - intent_at <= 10)
            require(
                binding.finish_by - intent_at <= binding.stored.contract.maximum_recording_seconds
            )
            require(type(directory_identity) is tuple and len(directory_identity) == 6)
            require(all(type(item) is int for item in directory_identity))
            writer = binding.stored.writer
            require(stat.S_ISDIR(directory_identity[2]))
            require(stat.S_IMODE(directory_identity[2]) == 0o700)
            require(directory_identity[3:5] == (writer.uid, writer.gid))
            self.binding, self.intent_at, self.directory_identity = (
                binding,
                intent_at,
                directory_identity,
            )
            self.original = binding, payload, intent_at, directory_identity
        except BaseException as error:
            self._fail(error)

    def _guard(self, deadline):
        require(not self.failed and self.owner == (os.getpid(), get_ident()))
        original, payload, intent, directory = self.original
        require(type(self.binding) is channel.Binding and self.binding is original)
        require(channel.encode(self.binding.payload()) == payload)
        require((self.intent_at, self.directory_identity) == (intent, directory))
        require(self.inventory is self._original_inventory)
        if self.inventory is None:
            require(self._inventory_pins is None)
        else:
            require(type(self.inventory) is Inventory)
            require((self.inventory.directory, self.inventory.records) == self._inventory_pins)
        require(self.intent_at <= time.monotonic() < min(deadline, self.binding.finish_by))

    def _names(self, fd, deadline):
        names = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                self._guard(deadline)
                require(entry.name in NAMES and entry.name not in names)
                names.add(entry.name)
        require(names == set(NAMES[: len(names)]))
        return NAMES[: len(names)]

    def read(self, directory, *, deadline):
        """Borrow one original directory fd. Do not close it or touch the writer."""
        acquired = False
        try:
            require(self.lock.acquire(blocking=False))
            acquired = True
            began = time.monotonic()
            owner.clock(deadline)
            deadline = min(deadline, began + 2, self.binding.finish_by)
            self._guard(deadline)
            require(type(directory) is int and directory >= 0)
            before = identity(os.fstat(directory))
            require(before[:6] == self.directory_identity)
            names = self._names(directory, deadline)
            records = tuple(
                (name, *preservation._record(directory, name, self.binding.stored.writer, deadline))
                for name in names
            )
            require(identity(os.fstat(directory)) == before)
            for name, raw, observed in records:
                require(
                    preservation._record(directory, name, self.binding.stored.writer, deadline)
                    == (raw, observed)
                )
            require(self._names(directory, deadline) == names)
            require(identity(os.fstat(directory)) == before)
            _sequence(records, self.binding, self.intent_at, time.monotonic())
            previous = self.inventory
            if previous is not None:
                require(type(previous) is Inventory)
                require(records[: len(previous.records)] == previous.records)
                if len(records) == len(previous.records):
                    require(before == previous.directory)
            self._guard(deadline)
            result = Inventory(before, records)
            self.inventory = self._original_inventory = result
            self._inventory_pins = before, records
            return result
        except BaseException as error:
            self._fail(error)
        finally:
            if acquired:
                self.lock.release()

    def _fail(self, error):
        self.failed = True
        if not isinstance(error, Exception):
            raise error
        raise UnconfirmedInventory(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Read-only receipt inventory; no acknowledgment or installed action.")
