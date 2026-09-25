"""Private local receipts; synthetic binding/clock except the native-child check."""

import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_bridge as b

tree, configured, prepared, layout, joined = b.tree, b.configured, b.prepared, b.layout, b.joined
NAME = "supplemental_recording_receipt_inventory"
SPEC = importlib.util.spec_from_file_location(NAME, Path(b.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


def denied(action):
    with pytest.raises(m.UnconfirmedInventory) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


@pytest.fixture
def inventory(joined, monkeypatch):
    s = joined
    path = s.prepared.spec.receipts
    fd = os.open(path, m.protected.monitor.DIRECTORY)
    plan = b.channel.plan(s.binding)
    observed = plan.prepared_at + 3
    monkeypatch.setattr(time, "monotonic", lambda: observed)
    completed = b.channel.completed(s.binding, s.expected)["stopped"]
    started = completed | {
        "status": "recording",
        "active": True,
        "completed_recordings": 0,
        "metadata": None,
        "stopped_at": None,
    }
    values = [
        dict(
            schema=1,
            plan=asdict(plan),
            root_sha256=s.binding.stored.contract.root_sha256,
            baseline_sha256=s.binding.stored.contract.baseline_sha256,
        ),
    ]
    for at, snapshot in (
        (plan.prepared_at, None),
        (plan.prepared_at, started),
        (plan.stop_at, None),
        (plan.stop_at, completed),
    ):
        values.append(
            dict(
                schema=1,
                case=plan.case,
                generation=plan.generation,
                at_monotonic=at,
                snapshot=snapshot,
            )
        )
    reader = m.ReceiptInventory(
        s.binding, intent_at=s.ledger.state.now, directory_identity=m.identity(os.fstat(fd))[:6]
    )

    def write(index, raw=None):
        target = path / m.NAMES[index]
        target.write_bytes(m.channel.encode(values[index]) if raw is None else raw)
        target.chmod(0o600)

    def read():
        return reader.read(fd, deadline=time.monotonic() + 2)

    try:
        yield SimpleNamespace(
            path=path,
            fd=fd,
            reader=reader,
            write=write,
            read=read,
            values=values,
            plan=plan,
            joined=s,
            observed=observed,
        )
    finally:
        os.close(fd)


def test_every_prefix_is_only_a_read_only_inventory(inventory, monkeypatch):
    s = inventory

    def forbidden(*args, **kwargs):
        pytest.fail("An inventory must not mutate a recorder or certify its output")

    for method in ("start", "stop", "close"):
        monkeypatch.setattr(m.owner.FiniteRecordingOwner, method, forbidden)
    for method in ("finalized", "pristine", "retained"):
        monkeypatch.setattr(m.protected.Collector, method, forbidden)
    fds = len(os.listdir("/proc/self/fd"))
    first = s.read()
    assert type(first) is m.Inventory and first.records == ()
    for index in range(5):
        s.write(index)
        current = s.read()
        assert tuple(name for name, *_ in current.records) == m.NAMES[: index + 1]
        assert current.records[:-1] == first.records
        assert s.read() == current
        first = current
    assert len(os.listdir("/proc/self/fd")) == fds
    assert not any(hasattr(current, attr) for attr in ("acknowledgment", "artifact", "exited"))
    assert s.joined.ledger.state.expected is None and s.joined.ledger.state.acknowledgment is None


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_a_first_checkpoint_may_observe_an_entire_completed_prefix(inventory, count):
    s = inventory
    for index in range(count):
        s.write(index)
    assert len(s.read().records) == count


@pytest.mark.parametrize(
    "fault",
    [
        "extra",
        "orphan",
        "symlink",
        "fifo",
        "hardlink",
        "mode",
        "oversize",
        "empty",
        "partial",
        "duplicate",
        "noncanonical",
    ],
)
def test_unsafe_or_partial_records_refuse_without_cleanup(inventory, fault):
    s = inventory
    s.write(0)
    path = s.path / m.NAMES[0]
    if fault == "extra":
        (s.path / "PRIVATE").write_bytes(b"PRIVATE")
    elif fault == "orphan":
        s.write(2)
    elif fault in ("symlink", "fifo"):
        path.unlink()
        if fault == "symlink":
            path.symlink_to(s.path / "PRIVATE")
        else:
            os.mkfifo(path, 0o600)
    elif fault == "hardlink":
        os.link(path, s.path.parent / "PRIVATE-link")
    elif fault == "mode":
        path.chmod(0o644)
    else:
        raw = {
            "oversize": b"x" * 8193,
            "empty": b"",
            "partial": b"{",
            "duplicate": b'{"schema":1,"schema":1}',
            "noncanonical": b'{ "schema": 1 }',
        }[fault]
        s.write(0, raw)
    before = set(s.path.iterdir())
    denied(s.read)
    denied(s.read)
    assert s.reader.failed and set(s.path.iterdir()) == before
    os.fstat(s.fd)  # The reader never closes borrowed handles.


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "plan_extra",
        "case",
        "generation",
        "endpoint",
        "root",
        "baseline",
        "early_plan",
        "late_start_bound",
        "late_finish_bound",
        "future_event",
        "early_start",
        "late_start",
        "early_stop",
        "late_stop",
        "intent_snapshot",
        "stop_intent_snapshot",
        "event_case",
        "event_generation",
        "event_extra",
        "time_order",
        "start_status",
        "start_active",
        "start_closed",
        "start_error",
        "start_completed",
        "start_metadata",
        "start_stop_time",
        "filename",
        "stop_status",
        "stop_active",
        "stop_completed",
        "stop_metadata",
        "stop_start_time",
        "stop_time",
    ],
)
def test_closed_receipt_format_and_original_bounds_are_checked(inventory, fault):
    s = inventory
    pre, intent, started, stop_intent, stopped = s.values
    if fault == "schema":
        pre["schema"] = True
    elif fault == "plan_extra":
        pre["plan"]["PRIVATE"] = True
    elif fault in ("case", "generation", "endpoint"):
        key = "audio_endpoint_sha256" if fault == "endpoint" else fault
        pre["plan"][key] = ("d" * 32) if fault == "case" else ("d" * 64)
    elif fault in ("root", "baseline"):
        pre[fault + "_sha256"] = "d" * 64
    elif fault == "early_plan":
        pre["plan"]["prepared_at"] = s.joined.ledger.state.now - 1
    elif fault == "late_start_bound":
        pre["plan"]["start_by"] = s.joined.binding.start_by + 1
    elif fault == "late_finish_bound":
        pre["plan"]["finish_by"] = s.joined.binding.finish_by + 1
    elif fault == "future_event":
        started["at_monotonic"] = s.observed + 1
    elif fault == "early_start":
        intent["at_monotonic"] = s.plan.prepared_at - 1
    elif fault == "late_start":
        started["at_monotonic"] = s.plan.start_by
    elif fault == "early_stop":
        stop_intent["at_monotonic"] = s.plan.stop_at - 1
    elif fault == "late_stop":
        stopped["at_monotonic"] = s.plan.finish_by
    elif fault == "intent_snapshot":
        intent["snapshot"] = {}
    elif fault == "stop_intent_snapshot":
        stop_intent["snapshot"] = {}
    elif fault == "event_case":
        intent["case"] = "d" * 32
    elif fault == "event_generation":
        intent["generation"] = "d" * 64
    elif fault == "event_extra":
        intent["PRIVATE"] = True
    elif fault == "time_order":
        intent["at_monotonic"] += 0.01
    elif fault == "filename":
        started["snapshot"]["recording"] = "PRIVATE.wav"
    elif fault == "stop_start_time":
        stopped["snapshot"]["started_at"] = "2026-09-23T08:00:02-06:00"
    elif fault == "stop_time":
        stopped["snapshot"]["stopped_at"] = "2020-01-01T00:00:00Z"
    else:
        side, field = fault.split("_", 1)
        key, value = {
            "status": ("status", "idle"),
            "active": ("active", side == "stop"),
            "closed": ("closed", True),
            "error": ("error", "PRIVATE"),
            "completed": ("completed_recordings", 2),
            "metadata": ("metadata", "PRIVATE"),
            "stop_time": ("stopped_at", "2026-09-23T08:00:01-06:00"),
        }[field]
        (started if side == "start" else stopped)["snapshot"][key] = value
    for index in range(5):
        s.write(index)
    denied(s.read)
    assert s.reader.failed


@pytest.mark.parametrize(
    "fault",
    [
        "bytes",
        "inode",
        "missing",
        "directory_metadata",
        "binding",
        "inventory",
        "pins",
        "deadline",
        "thread",
        "borrowed_directory",
    ],
)
def test_observed_prefix_cannot_be_replaced_or_reacquired(inventory, monkeypatch, fault):
    s = inventory
    s.write(0)
    observed = s.read()
    target = s.path / m.NAMES[0]
    action = s.read
    if fault == "bytes":
        s.write(0, b"PRIVATE")
    elif fault == "inode":
        other = s.path / "replacement"
        other.write_bytes(target.read_bytes())
        other.chmod(0o600)
        other.replace(target)
    elif fault == "missing":
        target.unlink()
    elif fault == "directory_metadata":
        os.utime(s.path, ns=(1, 1))
    elif fault == "binding":
        s.reader.binding = replace(s.joined.binding)
    elif fault == "inventory":
        s.reader.inventory = None
    elif fault == "pins":
        object.__setattr__(observed, "records", ())
    elif fault == "deadline":

        def action():
            return s.reader.read(s.fd, deadline=time.monotonic())
    elif fault == "borrowed_directory":

        def action():
            return s.reader.read(True, deadline=time.monotonic() + 2)
    else:
        errors = []

        def other_thread():
            try:
                s.read()
            except m.UnconfirmedInventory as error:
                errors.append(error)

        thread = Thread(target=other_thread)
        thread.start()
        thread.join(timeout=1)
        assert len(errors) == 1 and not thread.is_alive()
    denied(action)
    os.fstat(s.fd)


@pytest.mark.parametrize("fault", ["append", "rewrite", "time", "read_failure"])
def test_uncertain_mid_collection_never_returns_an_inventory(inventory, monkeypatch, fault):
    s = inventory
    s.write(0)
    original = m.preservation._record
    calls = 0

    def changed(*args, **kwargs):
        nonlocal calls
        value = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            if fault == "append":
                s.write(1)
            elif fault == "rewrite":
                s.write(0, b"PRIVATE")
            elif fault == "time":
                monkeypatch.setattr(time, "monotonic", lambda: s.observed + 3)
            else:
                raise OSError("PRIVATE")
        return value

    monkeypatch.setattr(m.preservation, "_record", changed)
    denied(s.read)
    assert s.reader.inventory is None and s.reader.failed
    os.fstat(s.fd)


def test_actual_native_owner_receipts_fit_without_supplying_an_acknowledgment(joined):
    s = joined
    fd = os.open(s.prepared.spec.receipts, m.protected.monitor.DIRECTORY)
    try:
        reader = m.ReceiptInventory(
            s.binding, intent_at=s.ledger.state.now, directory_identity=m.identity(os.fstat(fd))[:6]
        )
        assert reader.read(fd, deadline=time.monotonic() + 2).records == ()

        def run(send):
            return b.native_tests.native_run(s.prepared, s.binding, send, None)

        with b.channel.child(s.binding, run) as receiver:
            # Consume the actual private returns separately; do not submit any
            # disk observation to the host ledger or native-return consumer.
            assert json.loads(receiver.receive().raw)["phase"] == "started"
            assert json.loads(receiver.receive().raw)["phase"] == "completed"
        assert receiver.fixture_returncode == 0
        observed = reader.read(fd, deadline=time.monotonic() + 2)
        assert tuple(name for name, *_ in observed.records) == m.NAMES
        assert s.ledger.state.expected is None and s.ledger.state.acknowledgment is None
        assert (s.prepared.tree.root / "older/old.wav").read_bytes() == b"old evidence unchanged"
    finally:
        os.close(fd)
