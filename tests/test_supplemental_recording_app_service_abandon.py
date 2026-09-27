"""Explicit cancellation routing; real ledger/policy, synthetic native/exit edges.

No test here authenticates native replies or claims an installed service,
actual retained-file recovery, successful artifact, or audible acceptance.
"""

import time
from dataclasses import asdict, replace

import pytest

from . import test_supplemental_recording_app_service_active as active_tests

m, recording_tests, native_tests = (
    active_tests.m,
    active_tests.recording_tests,
    active_tests.native_tests,
)
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    transfer,
    service,
    candidate_fixture,
    app_candidate,
    app_phase,
    recording,
) = (
    recording_tests.layout,
    recording_tests.tree,
    recording_tests.routing,
    recording_tests.projection,
    recording_tests.binding,
    recording_tests.directory,
    recording_tests.prepared,
    recording_tests.joined,
    recording_tests.before_handoff,
    recording_tests.transfer,
    recording_tests.service,
    recording_tests.candidate_fixture,
    recording_tests.app_candidate,
    recording_tests.app_phase,
    recording_tests.recording,
)


active = active_tests.active


@pytest.fixture
def abandoned(recording, tree, monkeypatch):
    s = recording
    s.preserved_readers, s.preserved_recoveries, s.abandon_calls = [], [], []
    s.abandon_fault = None
    original_started = m.begin.relayed.Relay.started
    original_abandon = m.launch.binding.Ledger.abandon

    def started(relay):
        original_started(relay)
        relay.expected = replace(tree.expected, generation=s.run.pins.generation)
        # Synthetic authenticated start return, but actual durable host entry.
        s.ledger.started(relay.expected, now=time.monotonic(), success_sha256="a" * 64)
        return relay.expected

    def abandon(ledger, *, now):
        assert ledger is s.ledger and "transport_close" not in s.trace
        s.abandon_calls.append(ledger)
        if s.abandon_fault == "before":
            raise OSError("PRIVATE ledger failure")
        closed = original_abandon(ledger, now=now)
        if s.abandon_fault == "after":
            raise OSError("PRIVATE lost ledger acknowledgment")
        if s.abandon_fault == "return":
            return replace(closed)
        return closed

    class Preserved:
        def __init__(self, operator, ledger, journal):
            assert operator is s.driver.native.operator and operator.published
            assert ledger is s.ledger and ledger.state.closed
            assert ledger.state.expected == s.driver.recording.relay.expected
            assert ledger.state.acknowledgment is ledger.state.preservation is None
            assert journal is s.journal and s.init_exit
            assert journal.machine.process_bound(m.base.CANDIDATE, exited=True)
            if s.abandon_fault == "reader":
                raise OSError("PRIVATE retained-reader failure")
            self.operator, self.ledger, self.journal = operator, ledger, journal
            self.closed = False
            s.preserved_readers.append(self)

        def close(self):
            self.closed = True

    def recover(reader, run, session, wait):
        assert reader is s.preserved_readers[0] and run is s.run and session is s.session
        s.preserved_recoveries.append((reader, run, session))
        wait(0.25)
        native_tests.direct_native.expire(s)
        return session.poll()  # No fabricated normal restoration or successful recording.

    monkeypatch.setattr(m.begin.relayed.Relay, "started", started)
    monkeypatch.setattr(m.launch.binding.Ledger, "abandon", abandon)
    monkeypatch.setattr(m.begin.worker_exit.reconcile, "Preserved", Preserved)
    monkeypatch.setattr(recording_tests.r.recovery, "recover_preserved", recover)
    return s


def run(s, action, after=None):
    def started():
        assert s.driver.start_recording()
        action()

    return recording_tests.run(s, started, after)


def exits(s):
    s.worker_exit = s.init_exit = True
    s.engine.dead.add(s.witness.identity.container_id)


@pytest.mark.parametrize("lost_close", [False, True])
@pytest.mark.parametrize("init_exits", [False, True])
@pytest.mark.parametrize("service", [False, "offered"], indirect=True)
def test_explicit_abandon_waits_for_original_exits_not_close_ack(abandoned, lost_close, init_exits):
    s = abandoned
    original_session = s.session

    def action():
        before = s.ledger.state
        s.close_fault = lost_close
        assert s.driver.abandon_recording() is (not lost_close)
        p = s.driver.recording
        assert p.abandon_attempted and p.abandoned and not p.uncertain
        assert p.abandoned_state is s.ledger.state
        assert s.ledger.state.count == before.count + 1 == 4
        assert m.launch.binding.load(s.ledger.directory, s.ledger.binding) == s.ledger.state
        assert s.ledger.state.expected == before.expected and s.ledger.state.acknowledgment is None
        assert s.session is original_session and not s.native_captures[0].done
        assert not s.journal.machine.state.finish_requested
        assert s.trace.count("transport_close") == 1
        if init_exits:
            exits(s)

    assert run(s, action).phase == "review"
    assert len(s.preserved_recoveries) == int(init_exits) and not s.service.failed
    assert all(r.closed for r in s.preserved_readers)
    assert s.start_calls[0].closed and s.native_captures[0].closed
    assert not s.finish_calls and not s.finalized_recoveries and not s.recoveries
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.journal.machine.state.artifact_sha256 is None and len(s.engine.sent) == 2
    if s.borrowed_clock is not None:
        assert s.startup_offer.accepted and s.service.clock_witness is s.borrowed_clock
        assert not s.borrowed_clock.closed and s.borrowed_clock.fd >= 0


@pytest.mark.parametrize("kind", ["worker_only", "init_only", "neither"])
def test_missing_original_exit_cannot_restore(abandoned, kind):
    s = abandoned

    def action():
        assert s.driver.abandon_recording()
        if kind == "worker_only":
            s.worker_exit = True
        elif kind == "init_only":
            s.init_exit = True
            s.engine.dead.add(s.witness.identity.container_id)

    assert run(s, action).phase == "review"
    assert not s.preserved_recoveries and not s.preserved_readers
    assert not s.finish_calls and s.ledger.state.closed and not s.service.failed


@pytest.mark.parametrize("fault", ["before", "after", "return"])
def test_abandon_publication_uncertainty_does_not_close_or_adopt_ledger(abandoned, fault):
    s = abandoned
    s.abandon_fault = fault

    def action():
        assert not s.driver.abandon_recording()
        assert s.driver.recording.uncertain and not s.driver.recording.abandoned
        assert s.driver.recording.abandoned_state is None
        assert s.ledger.state.closed is (fault != "before")
        exits(s)

    assert run(s, action).phase == "review"
    assert len(s.abandon_calls) == 1 and "transport_close" not in s.trace
    assert not s.preserved_recoveries and not s.finish_calls and not s.service.failed


@pytest.mark.parametrize("fault", ["poll", "publish_before", "publish_after", "reader"])
def test_lost_exit_or_reader_cannot_fall_back_or_republish(abandoned, fault):
    s = abandoned
    s.fault = {
        "poll": "exit_poll",
        "publish_before": "exit_publication",
        "publish_after": "exit_ack",
    }.get(fault)
    s.abandon_fault = fault

    def action():
        assert s.driver.abandon_recording()
        exits(s)

    assert run(s, action).phase == "review"
    assert s.driver.recording.uncertain and not s.service.failed
    assert not s.preserved_recoveries and not s.finalized_recoveries and not s.recoveries
    assert s.native_captures[0].polls == 1
    assert s.ledger.state.acknowledgment is None and s.trace.count("transport_close") == 1


@pytest.mark.parametrize("operation", ["abandon", "finish", "observe", "native_cancel", "start"])
def test_abandoned_phase_rejects_repeat_or_success_route(abandoned, operation):
    s = abandoned

    def action():
        assert s.driver.abandon_recording()
        {
            "abandon": s.driver.abandon_recording,
            "finish": s.driver.finish_recording,
            "observe": s.driver.observe_recording,
            "native_cancel": s.driver.cancel_native,
            "start": s.driver.start_recording,
        }[operation]()

    native_tests.direct_native.resources.denied(lambda: run(s, action))
    assert s.service.failed and s.service.closed and len(s.abandon_calls) == 1
    assert (
        s.trace.count("transport_close") == 1 and not s.finish_calls and not s.preserved_recoveries
    )


@pytest.mark.parametrize(
    "prior", ["no_start", "failed_start", "finish", "failed_finish", "active_failure"]
)
def test_abandon_cannot_repair_an_ineligible_recording(abandoned, prior):
    s = abandoned

    def action():
        if prior == "failed_start":
            s.start_fault = "started"
            assert not s.driver.start_recording()
        elif prior != "no_start":
            assert s.driver.start_recording()
            if prior == "active_failure":
                s.driver.recording.uncertain = True  # Distinct existing tested active failure.
            else:
                s.finish_fault = "receive" if prior == "failed_finish" else None
                assert s.driver.finish_recording() is (prior == "finish")
        s.driver.abandon_recording()

    native_tests.direct_native.resources.denied(lambda: recording_tests.run(s, action))
    assert s.service.failed and not s.abandon_calls and "transport_close" not in s.trace
    assert not s.preserved_recoveries


@pytest.mark.parametrize("field", ["ledger", "operator", "relay", "start_attempt", "session"])
def test_abandon_cannot_redirect_original_objects(abandoned, monkeypatch, field):
    s = abandoned

    def action():
        target = s.service if field == "session" else s.driver.recording
        with monkeypatch.context() as patch:
            patch.setattr(target, field, object())
            native_tests.direct_native.resources.denied(s.driver.abandon_recording)

    native_tests.direct_native.resources.denied(lambda: run(s, action))
    assert not s.abandon_calls and "transport_close" not in s.trace
    assert s.start_calls[0].closed and s.native_captures[0].closed


@pytest.mark.parametrize("changed", ["state", "poisoned"])
def test_pending_abandon_requires_original_acknowledged_state(abandoned, changed):
    s = abandoned

    def action():
        assert s.driver.abandon_recording()
        if changed == "state":
            s.ledger.state = replace(s.ledger.state)
        else:
            s.ledger._poisoned = True
        exits(s)

    assert run(s, action).phase == "review"
    assert s.driver.recording.uncertain and not s.preserved_readers and not s.service.failed


def test_successful_active_read_is_durably_retained_before_abandon(active, abandoned, monkeypatch):
    s = abandoned

    def progress(ledger, directory, collector, tip, *, now):
        assert ledger is s.ledger and directory == s.plan.root / "recording-progress"
        assert collector.stored == s.run.projected.host and not s.abandon_calls
        s.checkpoint_calls.append("ledger")
        # Actual durable tip entry; the reused file/checkpoint fixture remains
        # SYNTHETIC and is not retained-file or byte authentication.
        return ledger._append("progress", now, tip=asdict(tip))

    monkeypatch.setattr(m.launch.binding.Ledger, "progress", progress)

    def action():
        assert s.driver.observe_recording() is not None
        assert s.driver.abandon_recording()
        assert s.checkpoint_calls == ["read", "append", "ledger"]
        assert s.ledger.state.tip is not None and s.ledger.state.closed

    assert run(s, action).phase == "review"
    assert not s.service.failed and not s.finish_calls and not s.finalized_recoveries


@pytest.mark.parametrize(
    "fault",
    ["checkpoint_read", "checkpoint_append", "checkpoint_ledger_before", "checkpoint_ledger_after"],
)
def test_lost_active_checkpoint_cannot_be_discarded_to_abandon(active, abandoned, fault):
    s = abandoned

    def action():
        assert s.driver.observe_recording() is not None
        s.active_fault = fault
        assert not s.driver.abandon_recording()
        assert s.driver.recording.uncertain and not s.driver.recording.abandoned
        exits(s)

    assert run(s, action).phase == "review"
    assert not s.abandon_calls and "transport_close" not in s.trace and not s.finish_calls
    assert not s.preserved_recoveries and not s.service.failed


@pytest.mark.parametrize(
    "fault",
    [
        "missing_start",
        "mismatch",
        "generation",
        "closed",
        "poisoned",
        "relay_phase",
        "operator_done",
    ],
)
def test_confirmed_route_still_requires_open_known_original_start(abandoned, fault):
    s = abandoned

    def action():
        p = s.driver.recording
        if fault == "missing_start":
            s.ledger.state = replace(s.ledger.state, expected=None)
        elif fault == "mismatch":
            p.relay.expected = replace(p.relay.expected, generation="b" * 64)
        elif fault == "generation":
            s.ledger.state = replace(s.ledger.state, generation="b" * 64)
        elif fault == "closed":
            s.ledger.state = replace(s.ledger.state, closed=True)
        elif fault == "poisoned":
            s.ledger._poisoned = True
        elif fault == "relay_phase":
            p.relay.phase = "unconfirmed"
        else:
            p.operator.done = True
        assert not s.driver.abandon_recording()
        assert p.uncertain and not p.abandoned

    assert run(s, action).phase == "review"
    assert not s.service.failed and not s.abandon_calls and "transport_close" not in s.trace


@pytest.mark.parametrize("stage", ["ledger_before", "ledger_after", "transport"])
def test_interruption_never_retries_abandon_or_closes_replacement_custody(
    abandoned, monkeypatch, stage
):
    s = abandoned
    original = m.launch.binding.Ledger.abandon

    def interrupt(*args, **kwargs):
        if stage == "ledger_after":
            original(*args, **kwargs)
        raise KeyboardInterrupt("PRIVATE interrupted cancellation")

    def action():
        if stage == "transport":
            monkeypatch.setattr(s.run.client, "close", interrupt)
        else:
            monkeypatch.setattr(m.launch.binding.Ledger, "abandon", interrupt)
        s.driver.abandon_recording()

    with pytest.raises(KeyboardInterrupt):
        run(s, action)
    assert s.service.closed and s.service.failed and s.start_calls[0].closed
    assert s.native_captures[0].closed and not s.audit.closed
    assert not s.preserved_recoveries and not s.finish_calls
    assert s.ledger.state.closed is (stage != "ledger_before")
