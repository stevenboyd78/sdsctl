"""Original real journals/pidfd; explicitly synthetic Relay/Ready/platform."""

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
