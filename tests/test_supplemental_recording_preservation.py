"""Failure-only naming scope and durable closure; never native acknowledgment."""

import fcntl
import json
import os
import time
from dataclasses import asdict, replace

import pytest

from . import test_supplemental_recording_bridge as bridge_tests

b = bridge_tests
h, n, p = b.h, b.n, b.p
m = h.preservation
tree, configured, prepared, layout, joined = b.tree, b.configured, b.prepared, b.layout, b.joined


def write(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


@pytest.fixture
def receipts(joined):
    path = joined.prepared.spec.receipts
    plan = b.channel.plan(joined.binding)
    snapshot = b.channel.completed(joined.binding, joined.expected)["stopped"] | {
        "status": "recording",
        "active": True,
        "metadata": None,
        "stopped_at": None,
        "completed_recordings": 0,
    }
    values = {
        "prepared.json": {
            "schema": 1,
            "plan": asdict(plan),
            "root_sha256": joined.binding.stored.contract.root_sha256,
            "baseline_sha256": joined.binding.stored.contract.baseline_sha256,
        },
        "start-intent.json": {
            "schema": 1,
            "case": plan.case,
            "generation": plan.generation,
            "at_monotonic": plan.prepared_at,
            "snapshot": None,
        },
        "started.json": {
            "schema": 1,
            "case": plan.case,
            "generation": plan.generation,
            "at_monotonic": plan.prepared_at,
            "snapshot": snapshot,
        },
    }
    for name, value in values.items():
        write(path / name, n.encode(value))
    return path


def read(joined, directory=None, **changes):
    return m.read_scope(
        directory or joined.prepared.spec.receipts,
        changes.pop("binding", joined.binding),
        intent_at=changes.pop("intent_at", joined.ledger.state.now),
        **changes,
    )


def refused(action):
    with pytest.raises(m.UnconfirmedScope) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__
    assert "PRIVATE" not in str(caught.value)


def test_actual_lost_return_is_retained_without_becoming_acknowledgment(joined):
    intent_at = joined.ledger.state.now

    def run(send):
        return b.native_tests.native_run(
            joined.prepared, joined.binding, send, "lost_started_return"
        )

    with b.channel.child(joined.binding, run) as receiver:
        bridge = b.m.Bridge(joined.ledger, receiver)
        b.refused(bridge.started)
        assert joined.ledger.state.expected is None and bridge.phase == "unconfirmed"
    assert receiver.fixture_returncode == 0  # Separate owned-child exit, not a receipt assertion.
    scope = read(joined, intent_at=intent_at)
    assert scope.native_contract_sha256 == joined.binding.stored.contract.sha256
    state = joined.ledger.preserve_unconfirmed_start(scope, now=time.monotonic())
    assert state.closed and state.preservation == scope
    assert state.expected is None and state.acknowledgment is None and state.tip is None
    assert h.load(joined.ledger.directory, joined.ledger.binding) == state
    collected = p.Collector(joined.ledger.binding.projection.host).retained(scope.expected)
    assert collected.files.stage == "retained" and collected.artifact is None
    assert collected.files.generation == state.generation
    with pytest.raises(h.UnconfirmedBinding):
        joined.ledger.started(scope.expected, now=time.monotonic(), success_sha256=scope.sha256)
    b.refused(bridge.completed)
    assert (joined.prepared.spec.receipts / "started.json").exists()
    assert (joined.prepared.tree.root / "older/old.wav").read_bytes() == b"old evidence unchanged"


def test_scope_reader_is_read_only_and_cannot_supply_success(receipts, joined, monkeypatch):
    before = {item.name: item.read_bytes() for item in receipts.iterdir()}

    def forbidden(*_, **__):
        pytest.fail("Naming scope cannot verify success, mutate a recorder, or collect files")

    monkeypatch.setattr(p.Collector, "finalized", forbidden)
    monkeypatch.setattr(p.Collector, "pristine", forbidden)
    monkeypatch.setattr(m.owner.FiniteRecordingOwner, "start", forbidden)
    monkeypatch.setattr(m.owner.FiniteRecordingOwner, "stop", forbidden)
    result = read(joined)
    assert type(result) is m.Scope and result.expected == joined.expected
    assert len(result.receipts_sha256) == len(result.sha256) == 64
    assert not hasattr(result, "acknowledgment") and not hasattr(result, "exited")
    assert joined.ledger.state.expected is None
    assert {item.name: item.read_bytes() for item in receipts.iterdir()} == before


@pytest.mark.parametrize("contents", [b"", b"{", b"unconfirmed stop", n.encode({"bogus": True})])
def test_partial_optional_stop_records_are_preserved_but_never_used_as_success(
    receipts, joined, contents
):
    initial = read(joined)
    write(receipts / "stop-intent.json", b"{")
    write(receipts / "stopped.json", contents)
    result = read(joined)
    assert result.expected == initial.expected
    assert result.receipts_sha256 != initial.receipts_sha256
    assert joined.ledger.state.acknowledgment is None


@pytest.mark.parametrize(
    "fault",
    [
        "missing_prepared",
        "missing_intent",
        "missing_started",
        "extra",
        "mode",
        "directory_mode",
        "link",
        "symlink",
        "fifo",
        "oversize",
        "empty",
        "truncated",
        "duplicate",
        "noncanonical",
        "schema_bool",
        "case",
        "generation",
        "root",
        "baseline",
        "endpoint",
        "old_plan",
        "late_start",
        "late_finish",
        "intent_snapshot",
        "time_reversed",
        "inactive",
        "closed",
        "error",
        "completed",
        "filename",
        "timestamp",
        "metadata",
        "stopped_at",
        "orphan_stop",
    ],
)
def test_unqualified_scope_is_not_adopted(receipts, joined, fault):
    name = "started.json"
    if fault.startswith("missing_"):
        missing = {
            "missing_prepared": "prepared.json",
            "missing_intent": "start-intent.json",
            "missing_started": name,
        }[fault]
        (receipts / missing).unlink()
    elif fault == "extra":
        write(receipts / "unrelated", b"x")
    elif fault == "mode":
        (receipts / name).chmod(0o644)
    elif fault == "directory_mode":
        receipts.chmod(0o755)
    elif fault == "link":
        (receipts.parent / "alias").hardlink_to(receipts / name)
    elif fault in ("symlink", "fifo"):
        (receipts / name).unlink()
        if fault == "symlink":
            (receipts / name).symlink_to(receipts / "start-intent.json")
        else:
            os.mkfifo(receipts / name)
    elif fault in ("oversize", "empty", "truncated", "duplicate", "noncanonical"):
        raw = (receipts / name).read_bytes()
        raw = {
            "oversize": b"x" * (m.MAX_BYTES + 1),
            "empty": b"",
            "truncated": b"{",
            "duplicate": raw[:-1] + b',"schema":1}',
            "noncanonical": raw + b"\n",
        }[fault]
        write(receipts / name, raw)
    elif fault == "orphan_stop":
        write(receipts / "stopped.json", b"{")
    else:
        if fault in ("root", "baseline", "endpoint", "old_plan", "late_start", "late_finish"):
            name = "prepared.json"
        elif fault in ("intent_snapshot", "time_reversed"):
            name = "start-intent.json"
        value = json.loads((receipts / name).read_bytes())
        if fault == "schema_bool":
            value["schema"] = True
        elif fault == "case":
            value["case"] = "bb12345612344abc8abc123456789abc"
        elif fault == "generation":
            value["generation"] = "f" * 64
        elif fault in ("root", "baseline"):
            value[fault + "_sha256"] = "f" * 64
        elif fault == "endpoint":
            value["plan"]["audio_endpoint_sha256"] = "f" * 64
        elif fault == "old_plan":
            value["plan"]["prepared_at"] = joined.ledger.state.now - 1
        elif fault == "late_start":
            value["plan"]["start_by"] = joined.binding.start_by + 1
        elif fault == "late_finish":
            value["plan"]["finish_by"] = joined.binding.finish_by + 1
        elif fault == "intent_snapshot":
            value["snapshot"] = {}
        elif fault == "time_reversed":
            value["at_monotonic"] += 0.1
        else:
            key, data = {
                "inactive": ("active", False),
                "closed": ("closed", True),
                "error": ("error", "failure"),
                "completed": ("completed_recordings", 1),
                "filename": ("recording", "unrelated.wav"),
                "timestamp": ("started_at", "invalid"),
                "metadata": ("metadata", "unrelated.json"),
                "stopped_at": ("stopped_at", "2026-09-23T08:00:00Z"),
            }[fault]
            value["snapshot"][key] = data
        write(receipts / name, n.encode(value))
    refused(lambda: read(joined))
    assert joined.ledger.state.expected is None and joined.ledger.state.acknowledgment is None


@pytest.mark.parametrize(
    "fault", ["generation", "native_contract", "endpoint", "case", "already_started", "closed"]
)
def test_host_failure_scope_cannot_promote_or_change_case(receipts, joined, fault):
    scope = read(joined)
    if fault == "native_contract":
        scope = replace(scope, native_contract_sha256="f" * 64)
    elif fault in ("generation", "endpoint", "case"):
        field = "audio_endpoint_sha256" if fault == "endpoint" else fault
        value = "bb12345612344abc8abc123456789abc" if fault == "case" else "f" * 64
        scope = replace(scope, expected=replace(scope.expected, **{field: value}))
    elif fault == "already_started":
        joined.ledger.started(joined.expected, now=time.monotonic(), success_sha256="4" * 64)
    else:
        joined.ledger.abandon(now=time.monotonic())
    with pytest.raises(h.UnconfirmedBinding):
        joined.ledger.preserve_unconfirmed_start(scope, now=time.monotonic())
    assert joined.ledger.state.preservation is None and joined.ledger.state.acknowledgment is None


@pytest.mark.parametrize(
    "fault", ["changed_bytes", "same_bytes_replaced", "late", "writer_uid", "writer_gid"]
)
def test_scope_set_must_stay_stable_and_bounded(receipts, joined, monkeypatch, fault):
    original = m._record
    calls = 0

    def record(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 3:
            path = receipts / "started.json"
            if fault == "changed_bytes":
                write(path, path.read_bytes() + b" ")
            elif fault == "same_bytes_replaced":
                temporary = receipts / "replacement"
                write(temporary, path.read_bytes())
                temporary.replace(path)
            elif fault == "late":
                late = time.monotonic() + p.MAX_SECONDS + 1
                monkeypatch.setattr(m.time, "monotonic", lambda: late)
        return result

    monkeypatch.setattr(m, "_record", record)
    binding = joined.binding
    if fault.startswith("writer_"):
        field = fault.removeprefix("writer_")
        writer = replace(
            binding.stored.writer, **{field: getattr(binding.stored.writer, field) + 1}
        )
        stored = p._decode(
            p.manifest_bytes(
                binding.stored.baseline, writer, binding.stored.contract.audio_endpoint_sha256
            )
        )
        binding = replace(binding, stored=stored)
    before = len(os.listdir("/proc/self/fd"))
    refused(lambda: read(joined, binding=binding))
    assert len(os.listdir("/proc/self/fd")) == before


def test_locked_receipts_do_not_prove_writer_exit(receipts, joined):
    fd = os.open(receipts, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused(lambda: read(joined))
    finally:
        os.close(fd)
    assert read(joined).expected == joined.expected
    assert joined.ledger.state.expected is None


@pytest.mark.parametrize("fault", ["fsync_lost", "return_lost", "cancelled"])
def test_uncertain_preservation_publication_stays_consumed(receipts, joined, monkeypatch, fault):
    scope = read(joined)
    original = os.fsync
    calls = 0

    def sync(fd):
        nonlocal calls
        original(fd)
        calls += 1
        if fault == "fsync_lost" or calls == 2:
            if fault == "cancelled":
                raise KeyboardInterrupt
            raise TimeoutError("PRIVATE preservation publication return lost")

    monkeypatch.setattr(os, "fsync", sync)
    exception = KeyboardInterrupt if fault == "cancelled" else h.UnconfirmedBinding
    with pytest.raises(exception):
        joined.ledger.preserve_unconfirmed_start(scope, now=time.monotonic())
    with pytest.raises(h.UnconfirmedBinding):
        joined.ledger.preserve_unconfirmed_start(scope, now=time.monotonic())
    loaded = h.load(joined.ledger.directory, joined.ledger.binding)
    assert loaded.closed and loaded.preservation == scope
    assert loaded.expected is None and loaded.acknowledgment is None
    assert (joined.ledger.directory / "0002.json").exists()
