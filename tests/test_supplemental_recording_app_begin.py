"""Actual App inventories/journals/ledger; explicitly synthetic Engine/Relay.

No scanner or installed service is contacted. The fixture's Relay and retained
actor guard are labeled substitutes; actual begin/private return authentication
has separate tests. Here the real App controller's authorization/lifetime joins
must work without widening any existing direct-policy gate.
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

from . import test_supplemental_recording_app_execution as executions
from . import test_supplemental_recording_idle_continuity as continuity_tests

candidate, app, native, launch_case, execution = (
    executions.candidate,
    executions.app,
    executions.native,
    executions.launch_case,
    executions.execution,
)
layout, image_umask, supervised = executions.layout, executions.image_umask, executions.supervised
image, configured = executions.image, executions.configured
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)

NAME = "supplemental_recording_app_begin"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(executions.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
begin, launch, denied = m.begin, m.launch, executions.begins.denied


@pytest.fixture
def joined(execution, monkeypatch):
    s = execution
    s.run = s.make()
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    s.ledger = begin.binding.Ledger(path, s.run.pins.host, now=time.monotonic())
    assert s.run.start_confirmed().healthy is True
    setup_relay(s, monkeypatch)

    def make():
        start = m.AppStart(s.run, s.ledger)
        s.starts.append(start)
        return start

    s.make_start = make
    s.trace.clear()
    try:
        yield s
    finally:
        for start in s.starts:
            start.close()


def setup_relay(s, monkeypatch):
    """Only native begin/retained actor I/O is synthetic; authorization is not."""
    s.starts, s.relays, s.exits = [], [], frozenset()
    s.prebegin_idle = s.idle.read()
    s.idle_adapter = s.idle.read
    native = begin.relayed.native

    def guard_check(guard):
        assert guard is s.run.ready.synthetic_guard
        assert not guard.failed and not s.run.ready.failed and not s.run.ready.closed
        assert not s.run.client.closed and s.run.client.begun
        assert time.monotonic() < guard.finish_by
        s.trace.append("retained_check")
        return s.exits

    def relay_init(relay, ledger, ready):
        assert ledger is s.ledger and ready is s.run.ready
        assert s.journal.machine.state.authorization_generation == s.idle.generation
        assert begin.binding.load(ledger.directory, ledger.binding) == ledger.state
        event = s.journal.entries[-1]["event"]
        assert event["kind"] == "authorize_recording"
        assert ledger.state.count == 2 and ledger.state.expected is None
        s.trace.append("relay")
        s.relays.append(relay)
        if s.fault == "relay":
            raise ValueError("PRIVATE uncertain begin")
        relay.ready, relay.ledger = ready, ledger
        relay.phase, relay.expected, relay.plan = "started", None, None
        relay.intent_at, relay.intent_sha256 = ledger.state.now, ledger.state.sha256
        relay.native_binding = native.Binding(
            s.projected.native,
            s.idle.generation,
            s.plan.projection_sha256,
            s.plan.candidate_runtime.source,
            ledger.state.start_by,
            ledger.state.finish_by,
        )
        relay.guard = object.__new__(begin.relayed.retained.Retained)
        relay.guard.ready, relay.guard.finish_by = ready, s.plan.lease["stop_by"]
        relay.guard.failed = False
        ready.synthetic_guard = relay.guard
        ready.client.begun = True

    monkeypatch.setattr(begin.relayed.Relay, "__init__", relay_init)
    monkeypatch.setattr(begin.relayed.retained.Retained, "check", guard_check)


def test_fresh_app_ready_then_durable_permission_and_original_begin(joined):
    s = joined
    start = s.make_start()
    assert not s.relays
    original = s.plan.raw, s.plan.lease, s.run.ready.received_at, s.run.ready.watch_deadline
    relay = start.start_once()
    assert relay is start.relay is s.relays[0]
    assert start.authorization["kind"] == "authorize_recording"
    assert start.intent.count == 2 and start.intent.generation == s.run.pins.generation
    assert start.intent.start_by <= s.run.ready.ready_by
    assert start.intent.finish_by <= s.plan.lease["stop_by"]
    assert s.trace.index("probe_prepare") < s.trace.index("probe") < s.trace.index("relay")
    assert original == (
        s.plan.raw,
        s.plan.lease,
        s.run.ready.received_at,
        s.run.ready.watch_deadline,
    )
    assert start.retained_history(require_live=True).state.phase == "candidate_running"
    assert not tuple((s.case_root / "receipts").iterdir())  # A sent begin isn't a native result.


def test_retained_app_history_does_not_poll_expired_ready(joined, monkeypatch):
    s = joined
    start = s.make_start()
    start.start_once()

    def expired(*args):
        pytest.fail("Post-begin continuity must not renew pre-begin readiness")

    monkeypatch.setattr(launch.received.Ready, "check_before_begin", expired)
    monkeypatch.setattr(launch.idle_module.Idle, "read", expired)
    with monkeypatch.context() as patch:
        continuity_tests.advance(patch, 125)
        assert time.monotonic() > s.run.ready.ready_by
        assert (
            start.retained_history(require_live=True).state.authorization_generation
            == s.idle.generation
        )


def test_duplicate_begin_owner_cannot_steal_or_poison_original(joined):
    s = joined
    start = s.make_start()
    denied(s.make_start)
    assert not start.failed and not s.run.failed and not s.run.client.closed
    assert s.run.begin_owner is start
    start.start_once()
    denied(s.make_start)
    assert len(s.relays) == 1 and start.retained_history().state.phase == "candidate_running"


@pytest.mark.parametrize(
    "fault", ["probe_prepare", "host_prepare", "probe", "source_during_probe", "relay"]
)
def test_failed_app_begin_closes_original_transport_without_releasing_actors(joined, fault):
    s = joined
    start = s.make_start()
    s.fault = fault
    denied(start.start_once)
    assert start.failed and s.run.failed and s.run.client.closed
    assert s.run.ready.failed and not s.run.ready.closed
    assert s.witness.fd >= 0 and not s.witness.exited()
    os.fstat(s.witness.fd)
    denied(start.start_once)
    denied(s.make_start)
    if fault == "relay":
        assert len(s.relays) == 1 and start.intent.count == 2
    else:
        assert not s.relays and start.intent is None
        assert s.journal.machine.state.authorization_generation is None


@pytest.mark.parametrize("field", ["run", "ready", "ledger", "plan"])
def test_substituted_begin_context_closes_only_original_app_transport(joined, field):
    s = joined
    start = s.make_start()
    setattr(start, field, object())
    denied(start.start_once)
    assert not s.relays and s.run.client.closed and not s.run.ready.closed


@pytest.mark.parametrize("fault", ["ledger", "ready_consumption", "not_confirmed"])
def test_constructor_failure_consumes_slot_and_closes_transport(joined, fault):
    s = joined
    ledger = s.ledger
    if fault == "ledger":
        ledger = object()
    elif fault == "ready_consumption":
        s.run.qualify.consumption = None
    else:
        s.run.confirm_attempted = False
    denied(lambda: m.AppStart(s.run, ledger))
    assert s.run.begin_attempted and s.run.client.closed and s.run.failed
    denied(s.make_start)


def test_post_begin_worker_exit_refuses_live_observation_not_independent_exit(joined):
    s = joined
    start = s.make_start()
    start.start_once()
    s.exits = frozenset({"native"})
    denied(lambda: start.retained_history(require_live=True))
    assert start.failed and s.run.client.closed
    os.fstat(s.witness.fd)


def test_unreviewed_begin_subclass_is_not_an_app_policy(joined):
    class Other(m.AppStart):
        pass

    s = joined
    denied(lambda: Other(s.run, s.ledger))
    assert not s.run.begin_attempted and not s.run.failed


def test_direct_recovery_gate_does_not_accept_app_start(joined):
    s = joined
    start = s.make_start()
    # The old recovery path must not silently infer App support from inheritance.
    denied(lambda: begin.AuthorizedFinalized(start, object()))
    assert not start.failed and not s.run.failed and not s.run.client.begun
