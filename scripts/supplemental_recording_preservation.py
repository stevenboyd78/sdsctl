#!/usr/bin/env python3
"""Read-only naming scope after a LOST recording start return; never success.

Only the exact original case's prepared/intent/started records identify the
possible output. A receipt does not authenticate a successful return, source,
process exit or recovery. Missing/unsafe scope requires administrative review;
this module never discovers a new baseline or repairs/removes recording files.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import asdict, dataclass

import supplemental_recording_owner as owner
import supplemental_recording_protected as protected
from supplemental_handoff_files import identity
from supplemental_handoff_policy import checksum, clock, encode

MESSAGE = "Unconfirmed recording output cannot be scoped; preserve all case evidence."
NAMES = frozenset(
    {"prepared.json", "start-intent.json", "started.json", "stop-intent.json", "stopped.json"}
)
REQUIRED = frozenset({"prepared.json", "start-intent.json", "started.json"})
MAX_BYTES = 8192


class UnconfirmedScope(ValueError):
    """Refusal never implies no recorder/file exists or permits a retry."""


def require(value):
    if not value:
        raise UnconfirmedScope(MESSAGE)


@dataclass(frozen=True)
class Scope:
    """Naming input for retained failure evidence, NOT a start acknowledgment.

    expected supplies only the exact case/filename/endpoint to the conservative
    retained-file collector. It must never be passed to Ledger.started or used
    to synthesize a completed acknowledgment. Host policy still requires exact
    writer exit before accepting retained evidence for restoration.
    """

    expected: protected.evidence.RecordingExpectation
    native_contract_sha256: str
    receipts_sha256: str

    def __post_init__(self):
        require(type(self.expected) is protected.evidence.RecordingExpectation)
        protected.evidence.digest(self.native_contract_sha256)
        protected.evidence.digest(self.receipts_sha256)

    @property
    def sha256(self):
        return checksum({"kind": "unconfirmed-start-preservation-scope", **asdict(self)})


def _canonical(raw):
    require(0 < len(raw) <= MAX_BYTES)
    value = json.loads(
        raw,
        object_pairs_hook=protected.evidence.unique,
        parse_constant=protected.evidence.reject_constant,
    )
    require(type(value) is dict and encode(value) == raw)
    require(type(value.get("schema")) is int and value["schema"] == 1)
    return value


def _event(raw, binding, plan):
    value = _canonical(raw)
    protected._mapping(value, {"schema", "case", "generation", "at_monotonic", "snapshot"})
    require(value["case"] == binding.stored.baseline.case)
    require(value["generation"] == binding.generation)
    clock(value["at_monotonic"])
    require(plan.prepared_at <= value["at_monotonic"] < plan.start_by)
    return value


def _record(directory, name, writer, deadline):
    require(time.monotonic() < deadline)
    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600)
    require(info.st_nlink == 1 and 0 <= info.st_size <= MAX_BYTES)
    require((info.st_uid, info.st_gid) == (writer.uid, writer.gid))
    fd = os.open(name, protected.monitor.READ, dir_fd=directory)
    try:
        require(identity(os.fstat(fd)) == identity(info))
        raw = os.read(fd, MAX_BYTES + 1)
        require(len(raw) == info.st_size)
        require(identity(os.fstat(fd)) == identity(info))
        require(identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) == identity(info))
        require(time.monotonic() < deadline)
        return raw, identity(info)
    finally:
        os.close(fd)


def read_scope(directory, binding, *, intent_at):
    """Read bounded original native receipts without asserting acknowledgment.

    binding and intent_at must come from the independently sealed projection and
    durable host start intent. The fixed receipt directory/source/mount identity
    and process exit are caller gates, not proved by these disk contents.
    """
    from supplemental_recording_channel import Binding

    try:
        require(type(binding) is Binding)
        binding.payload()
        clock(intent_at)
        require(0 < binding.start_by - intent_at <= 10)
        require(binding.finish_by - intent_at <= binding.stored.contract.maximum_recording_seconds)
        root = binding.stored.baseline.root
        require(not directory.is_relative_to(root) and not root.is_relative_to(directory))
        deadline = time.monotonic() + protected.MAX_SECONDS
        with protected._private_directory(directory, exclusive=False) as fd:
            before = identity(os.fstat(fd))
            names = set()
            with os.scandir(fd) as entries:
                for item in entries:
                    require(time.monotonic() < deadline and item.name in NAMES)
                    names.add(item.name)
            require(REQUIRED <= names <= NAMES)
            require("stopped.json" not in names or "stop-intent.json" in names)
            records = {}
            for name in sorted(names):
                records[name] = _record(fd, name, binding.stored.writer, deadline)
            require(identity(os.fstat(fd)) == before)
            # Re-read every bounded record to detect changes during the set's
            # observation, including incomplete optional stop publication.
            for name, value in records.items():
                require(_record(fd, name, binding.stored.writer, deadline) == value)
            require(identity(os.fstat(fd)) == before)
        raw = {name: value[0] for name, value in records.items()}
        prepared = _canonical(raw["prepared.json"])
        protected._mapping(prepared, {"schema", "plan", "root_sha256", "baseline_sha256"})
        plan = owner.Plan(
            **protected._mapping(prepared["plan"], set(owner.Plan.__dataclass_fields__))
        )
        contract = binding.stored.contract
        require(prepared["root_sha256"] == contract.root_sha256)
        require(prepared["baseline_sha256"] == contract.baseline_sha256)
        require(
            (plan.case, plan.generation, plan.audio_endpoint_sha256)
            == (contract.case_id, binding.generation, contract.audio_endpoint_sha256)
        )
        require(intent_at <= plan.prepared_at < plan.start_by <= binding.start_by)
        require(plan.finish_by <= binding.finish_by)
        intent = _event(raw["start-intent.json"], binding, plan)
        started = _event(raw["started.json"], binding, plan)
        require(intent["snapshot"] is None and intent["at_monotonic"] <= started["at_monotonic"])
        snapshot = owner.FiniteRecordingOwner._snapshot(started["snapshot"])
        require(snapshot["status"] == "recording" and snapshot["active"] is True)
        require(snapshot["closed"] is False and snapshot["error"] is None)
        require(snapshot["completed_recordings"] == 0)
        require(snapshot["metadata"] is None and snapshot["stopped_at"] is None)
        expected = protected.evidence.RecordingExpectation(
            plan.case, plan.generation, plan.audio_endpoint_sha256, snapshot["started_at"]
        )
        require(
            snapshot["recording"] == protected.evidence.filename(expected.case, expected.started_at)
        )
        require(time.monotonic() < deadline)
        return Scope(
            expected,
            contract.sha256,
            checksum(
                {
                    "binding": binding.payload(),
                    "intent_at": intent_at,
                    "receipts": {
                        name: hashlib.sha256(value).hexdigest() for name, value in raw.items()
                    },
                }
            ),
        )
    except Exception:
        raise UnconfirmedScope(MESSAGE) from None


if __name__ == "__main__":
    raise SystemExit("Read-only failure preservation scope; no start, acknowledgment or recovery.")
