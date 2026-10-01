"""Original real journals/pidfd; explicitly synthetic Relay/Ready/platform."""

import copy
import json
import time
from dataclasses import replace

import pytest

from . import test_supplemental_recording_host_begin as begins
from . import test_supplemental_recording_idle_continuity as continuity

m = begins.m
layout, tree, routing, projection, binding, directory, prepared, setup, joined = (
    begins.layout,
    begins.tree,
    begins.routing,
    begins.projection,
    begins.binding,
    begins.directory,
    begins.prepared,
    begins.setup,
    begins.joined,
)


@pytest.fixture
def begun(joined, monkeypatch):
    s = joined
    relay = s.start.start_once()
    relay.ready, relay.ledger = s.run.ready, s.ledger
    relay.phase, relay.expected = "started", None
    relay.intent_sha256 = s.start.intent.sha256
    relay.native_binding = m.relayed.native.Binding(
        s.run.projected.native,
        s.run.pins.generation,
        s.run.projected.sha256,
        s.run.pins.host.source_sha256,
        s.start.intent.start_by,
        s.start.intent.finish_by,
    )

    class Retained:
        def __init__(self):
            self.ready, self.finish_by = s.run.ready, s.plan.lease["stop_by"]

        def check(self):
            assert not self.ready.failed and not self.ready.closed
            assert not s.run.client.closed
            assert time.monotonic() < self.finish_by
            s.trace.append("retained")
            return frozenset()

    monkeypatch.setattr(m.relayed.retained, "Retained", Retained)
    relay.guard = Retained()
    s.trace.clear()
    return s


def test_post_begin_history_keeps_fixed_authorization_without_reusing_idle_or_ready(
    begun, monkeypatch
):
    s = begun
    original = s.plan.raw, s.run.command, s.start.intent, s.start.relay.native_binding
    histories = {
        path: path.read_bytes()
        for root in (s.ledger.directory, s.journal.path)
        for path in root.iterdir()
    }
    with monkeypatch.context() as patch:
        continuity.advance(patch, 125)
        assert time.monotonic() > s.run.command.ready_by
        assert s.start.retained_history() is s.journal.machine
    assert original == (s.plan.raw, s.run.command, s.start.intent, s.start.relay.native_binding)
    assert s.trace == ["retained", "retained"]
    assert histories == {path: path.read_bytes() for path in histories}
    assert len(s.relays) == 1 and not s.prepared.witness.exited()
    assert s.ledger.state.expected is None  # History alone is not a started recording.


@pytest.mark.parametrize(
    "fault", ["cancelled", "journal_bytes", "journal_extra", "intent_rehashed"]
)
def test_changed_history_refuses_without_new_begin(begun, fault):
    s = begun
    if fault == "cancelled":
        now = m.plans.clock.read().boottime_ns / m.plans.clock.NS
        s.journal.append(dict(kind="finish", now=now, boot_id=s.plan.boot))
    elif fault == "journal_bytes":
        path = s.journal.path / "0000.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif fault == "journal_extra":
        (s.journal.path / "PRIVATE_extra").write_bytes(b"unexpected")
    else:
        path = s.ledger.directory / "0001.json"
        value = json.loads(path.read_bytes())
        value["event"]["authorization_sha256"] = "f" * 64
        path.write_bytes(m.binding.encode(value))
        s.ledger.state = m.binding.load(s.ledger.directory, s.ledger.binding)
    begins.denied(s.start.retained_history)
    assert s.run.client.closed and not s.prepared.witness.exited()
    assert len(s.relays) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "relay",
        "guard",
        "guard_ready",
        "guard_deadline",
        "authorization",
        "intent",
        "plan",
        "run",
        "phase",
    ],
)
def test_no_rebound_authority_or_relaxed_deadline(begun, fault):
    s = begun
    if fault == "relay":
        s.start.relay = object()
    elif fault == "guard":
        s.start.relay.guard = object()
    elif fault == "guard_ready":
        s.start.relay.guard.ready = object()
    elif fault == "guard_deadline":
        s.start.relay.guard.finish_by += 1
    elif fault == "authorization":
        s.start.authorization["now"] += 1
    elif fault == "intent":
        s.start.intent = replace(s.start.intent)
    elif fault == "plan":
        s.start.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
    elif fault == "run":
        s.run.closed = True
    else:
        s.start.relay.phase = "unconfirmed"
    begins.denied(s.start.retained_history)
    assert not s.prepared.witness.exited()


@pytest.mark.parametrize("boundary", ["recording_recovery", "stop"])
def test_original_recording_recovery_and_stop_windows_remain_final(begun, monkeypatch, boundary):
    bound = (
        begun.journal.machine.state.recording_deadline + m.base.COMMAND_SECONDS
        if boundary == "recording_recovery"
        else begun.plan.deadlines.stop_by
    )
    now = m.plans.clock.read().boottime_ns / m.plans.clock.NS
    with monkeypatch.context() as patch:
        continuity.advance(patch, bound + 1 - now)
        begins.denied(begun.start.retained_history)
    assert not begun.prepared.witness.exited()


def test_post_begin_read_is_not_available_before_actual_return(joined):
    begins.denied(joined.start.retained_history)
    assert not joined.relays and joined.ledger.state.count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"recording_outcome": "verified"},
        {"files_stage": "finalized"},
        {"reason": "not-in-the-original-journal"},
    ],
)
def test_cached_policy_cannot_replace_durable_authorization(begun, change):
    s = begun
    originals = {path: path.read_bytes() for path in s.journal.path.iterdir()}
    s.journal.machine.state = replace(s.journal.machine.state, **change)
    begins.denied(s.start.retained_history)
    assert {path: path.read_bytes() for path in originals} == originals
    assert not s.prepared.witness.exited() and s.journal.fd >= 0
    assert len(s.relays) == 1


@pytest.mark.parametrize("fault", ["cache", "entry", "extra", "append", "machine"])
def test_host_history_cannot_change_during_original_file_read(begun, monkeypatch, fault):
    s = begun
    read, calls = m.binding.protected.evidence.read_bytes, []
    originals = {path: path.read_bytes() for path in s.journal.path.iterdir()}

    def changed(fd, name, **kwargs):
        raw = read(fd, name, **kwargs)
        if fd == s.journal.fd and not calls:
            calls.append(True)
            if fault == "cache":
                s.journal.machine.state = replace(s.journal.machine.state, files_stage="finalized")
            elif fault == "entry":
                s.journal.entries[-1]["previous"] = "f" * 64
            elif fault == "extra":
                (s.journal.path / "extra").write_bytes(b"PRIVATE")
            elif fault == "machine":
                s.journal.machine = copy.deepcopy(s.journal.machine)
            else:
                now = m.plans.clock.read().boottime_ns / m.plans.clock.NS
                s.journal.append(dict(kind="finish", now=now, boot_id=s.plan.boot))
        return raw

    monkeypatch.setattr(m.binding.protected.evidence, "read_bytes", changed)
    begins.denied(s.start.retained_history)
    assert len(calls) == 1 and len(s.relays) == 1
    assert {path: path.read_bytes() for path in originals} == originals
    assert s.journal.fd >= 0 and not s.prepared.witness.exited()


def test_retained_history_does_not_relax_bootstrap_history(begun, monkeypatch):
    with monkeypatch.context() as patch:
        continuity.advance(patch, 125)
        assert begun.start.retained_history() is begun.journal.machine
        with pytest.raises(m.launch.UnconfirmedHostLaunch, match=m.launch.MESSAGE):
            begun.run._history()


@pytest.mark.parametrize("when", ["identity", "journal", "ledger"])
def test_late_history_collection_refuses_and_preserves_original_handles(begun, monkeypatch, when):
    s = begun
    target, name = {
        "identity": (s.start, "_identity"),
        "journal": (s.run, "_history_until"),
        "ledger": (s.start, "_read_ledger"),
    }[when]
    read = getattr(target, name)

    def late(*args):
        result = read(*args)
        continuity.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(target, name, late)
    begins.denied(s.start.retained_history)
    assert not s.prepared.witness.exited() and s.start.failed


@pytest.mark.parametrize("require_live", [False, True])
def test_history_live_requirement_uses_both_fresh_checks_not_cached_exits(
    begun, monkeypatch, require_live
):
    s = begun
    guard = s.start.relay.guard
    guard.exits = frozenset({"native"})  # An old field is not a fresh observation.
    assert s.start.retained_history(require_live=require_live) is s.journal.machine
    assert s.trace == ["retained", "retained"]
    assert not s.start.failed and not s.prepared.witness.exited()


@pytest.mark.parametrize("check_number", [1, 2])
@pytest.mark.parametrize("role", ["guardian", "native", "watchdog"])
def test_active_history_refuses_exit_at_either_original_boundary(
    begun, monkeypatch, check_number, role
):
    guard = begun.start.relay.guard
    original = guard.check
    calls = []

    def changed():
        result = original()
        calls.append(True)
        return frozenset({role}) if len(calls) == check_number else result

    monkeypatch.setattr(guard, "check", changed)
    begins.denied(lambda: begun.start.retained_history(require_live=True))
    assert len(calls) == check_number
    assert begun.start.failed and begun.run.client.closed
    assert not begun.prepared.witness.exited()


def test_nonactive_history_still_allows_observed_worker_exit(begun, monkeypatch):
    guard = begun.start.relay.guard
    original = guard.check

    def exited():
        original()
        return frozenset({"native", "watchdog", "guardian"})

    monkeypatch.setattr(guard, "check", exited)
    assert begun.start.retained_history() is begun.journal.machine
    assert begun.trace == ["retained", "retained"]


@pytest.mark.parametrize("require_live", [None, 0, 1, "false"])
def test_history_rejects_nonboolean_live_requirement(begun, require_live):
    begins.denied(lambda: begun.start.retained_history(require_live=require_live))
    assert begun.start.failed and not begun.prepared.witness.exited()
