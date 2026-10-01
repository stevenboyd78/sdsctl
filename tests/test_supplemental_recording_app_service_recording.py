"""App service recording routing with synthetic native and returned-message edges.

Original service/session, private journal and ledger are real. These are fault
and ownership tests, not installed-host, native-byte or audible acceptance.
"""

import importlib.util
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_service as native_tests

m = native_tests.m.operator
NAME = "supplemental_recording_app_recording"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(native_tests.m.__file__).with_name(NAME + ".py")
)
r = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = r
SPEC.loader.exec_module(r)
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
) = (
    native_tests.layout,
    native_tests.tree,
    native_tests.routing,
    native_tests.projection,
    native_tests.binding,
    native_tests.directory,
    native_tests.prepared,
    native_tests.joined,
    native_tests.before_handoff,
    native_tests.transfer,
    native_tests.service,
    native_tests.candidate_fixture,
    native_tests.app_candidate,
    native_tests.app_phase,
)


@pytest.fixture
def recording(app_phase, monkeypatch):
    s = app_phase
    s.start_calls, s.begin_calls, s.received_starts, s.finish_calls = [], [], [], []
    s.readers, s.finalized_recoveries, s.retired_checks = [], [], []
    s.start_fault = s.finish_fault = None

    def fault(stage, value):
        if value == stage:
            if value == s.start_fault:
                s.run.failed = True  # Actual Start failure also poisons Launch.
            raise OSError("PRIVATE synthetic boundary failure")

    class Relay:
        def __init__(self, start):
            self.start = start
            self.phase = "started"
            self.expected = self.completion = None
            s.begin_calls.append(self)

        def started(self):
            s.received_starts.append(self)
            fault("started", s.start_fault)
            self.expected = object()  # Explicit synthetic receipt boundary.
            self.phase = "completed"
            return self.expected

        def completed(self, *, progress_directory):
            assert self.phase == "completed"
            assert progress_directory == (
                s.plan.root / "recording-progress"
                if s.driver.recording.active_preparation_attempted
                else None
            )
            s.finish_calls.append(self)
            fault("receive", s.finish_fault)
            self.completion = SimpleNamespace(synthetic_completion=True)
            self.phase = "closed"
            return self.completion

    class Start:
        def __init__(self, run, ledger, *, native_observer=None):
            assert native_observer is None  # This fixture does not authenticate native custody.
            s.start_calls.append(self)
            assert run is s.run and ledger is s.ledger
            assert s.driver.native.retired and s.driver.recording is not None
            assert s.session.read is s.driver.recording.read
            s.retired_checks.append(True)
            with pytest.raises(m.UnconfirmedOperator):
                s.driver.native._context()
            fault("construct", s.start_fault)
            self.run, self.ledger = run, ledger
            self.relay = None
            self.closed = False

        def start_once(self):
            fault("before_authorization", s.start_fault)
            now = s.service._now()
            observation = replace(
                s.journal.machine.baseline,
                sampled_at=now,
                normal=m.base.App(s.plan.normal.pin, "stopped"),
                candidate=m.base.App(
                    s.plan.candidate.pin, "running", s.run.pins.generation, True, False
                ),
            )
            s.append(
                "authorize_recording",
                generation=s.run.pins.generation,
                contract_sha256=s.plan.candidate.contract.sha256,
                observation=asdict(observation),
            )
            fault("after_authorization", s.start_fault)
            event = s.journal.entries[-1]["event"]
            now = time.monotonic()
            self.ledger.start_intent(
                now=now,
                generation=s.run.pins.generation,
                authorization_sha256=m.base.checksum(event),
                start_by=now + 1,
                finish_by=now + 5,
            )
            fault("after_intent", s.start_fault)
            self.relay = Relay(self)
            fault("after_send", s.start_fault)
            return self.relay

        def retained_history(self):
            fault("retain", s.start_fault)
            return s.journal.machine

        def close(self):
            self.closed = True

    class Finalized:
        def __init__(self, start, operator):
            assert start is s.driver.recording.start_attempt
            assert operator is s.driver.native.operator
            fault("capture", s.finish_fault)
            self.start, self.operator = start, operator
            self.phase, self.closed = "completed", False
            self.history = tuple(m.base.encode(e) for e in s.journal.entries)
            s.readers.append(self)

            def poll():
                fault("poll", s.finish_fault)
                assert self.phase == "exited"
                operator.done = True
                operator.result_sha256 = "e" * 64
                return m.begin.worker_exit.reconcile.Evidence(
                    *("a" * 64 for _ in range(8)), 0, s.init_exit, s.service._now()
                )

            operator.poll = poll

        def collect_exit(self):
            fault("collect", s.finish_fault)
            assert tuple(m.base.encode(e) for e in s.journal.entries) == self.history
            self.phase = "exited"
            self.start.relay.phase = "exited"

        def publish_exit(self):
            assert tuple(m.base.encode(e) for e in s.journal.entries) == self.history
            fault("publish_before", s.finish_fault)
            self.operator.publish(s.journal)
            fault("publish_after", s.finish_fault)
            self.phase = "published"
            return self.operator.result_sha256

        def _history(self, end):
            assert time.monotonic() < end and self.phase == "published"
            return s.journal.machine

        def close(self):
            self.closed = True

    def recover(reader, session, wait):
        assert reader is s.readers[0] and session is s.session
        assert s.init_exit and s.journal.machine.process_bound(m.base.CANDIDATE, exited=True)
        s.finalized_recoveries.append((reader, session))
        wait(0.25)
        native_tests.direct_native.expire(s)
        return session.poll()  # Explicitly no fabricated successful restoration.

    monkeypatch.setattr(r.app_begin, "AppStart", Start)
    monkeypatch.setattr(m.begin.relayed, "Relay", Relay)
    monkeypatch.setattr(r.finalized, "AppAuthorizedFinalized", Finalized)
    monkeypatch.setattr(r.recovery, "recover_finalized", recover)
    return s


def run(s, action, after=None):
    def joined():
        assert native_tests.start(s)
        s.run.ready.close = lambda: None  # Synthetic returned-Ready cleanup edge.
        action()

    return native_tests.run(s, joined, after)


@pytest.mark.parametrize("service", [False, "offered"], indirect=True)
def test_recording_requires_separate_explicit_handoff_and_actual_started_return(recording):
    s = recording
    original = s.session

    def action():
        assert not s.start_calls and s.ledger.state.count == 1
        assert s.driver.start_recording()
        assert s.session is original and s.driver.native.retired
        assert s.driver.recording.started and not s.driver.recording.uncertain
        assert s.ledger.state.count == 2 and s.ledger.state.expected is None
        assert s.journal.machine.state.recording_outcome == "unconfirmed"
        assert not s.finish_calls and not s.finalized_recoveries

    assert run(s, action).phase == "review"
    assert len(s.start_calls) == len(s.begin_calls) == len(s.received_starts) == 1
    assert s.start_calls[0].closed and s.native_captures[0].closed
    assert not s.service.failed and not s.recoveries
    if s.borrowed_clock is not None:
        assert s.startup_offer.accepted and s.service.clock_witness is s.borrowed_clock
        assert not s.borrowed_clock.closed and s.borrowed_clock.fd >= 0


@pytest.mark.parametrize(
    "stage,count,authorized",
    [
        ("construct", 1, False),
        ("before_authorization", 1, False),
        ("after_authorization", 1, True),
        ("after_intent", 2, True),
        ("after_send", 2, True),
        ("started", 2, True),
        ("retain", 2, True),
    ],
)
def test_failed_start_retires_native_without_disabling_original_clock(
    recording, stage, count, authorized
):
    s = recording
    s.start_fault = stage

    def action():
        assert not s.driver.start_recording()
        assert s.run.failed and s.driver.recording.uncertain
        assert s.driver.native.retired and not s.service.failed
        native_tests.services.publish(s, "cancel_idle")

    assert run(s, action).phase == "review"
    assert s.ledger.state.count == count and len(s.start_calls) == 1
    assert (s.journal.machine.state.authorization_generation is not None) is authorized
    assert not s.recoveries and not s.finalized_recoveries and "transport_close" not in s.trace
    assert len(s.engine.sent) == 2 and s.native_captures[0].closed
    if stage != "construct":
        assert s.start_calls[0].closed


@pytest.mark.parametrize(
    "stage", ["receive", "capture", "collect", "poll", "publish_before", "publish_after"]
)
def test_completion_or_exit_failure_never_becomes_success_or_pristine_recovery(recording, stage):
    s = recording
    s.finish_fault = stage

    def action():
        assert s.driver.start_recording()
        assert not s.driver.finish_recording()
        assert s.driver.recording.uncertain and s.driver.recording.finish_attempted

    assert run(s, action).phase == "review"
    assert len(s.finish_calls) == 1 and not s.recoveries and not s.finalized_recoveries
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.journal.machine.state.artifact_sha256 is None
    assert not s.service.failed and all(reader.closed for reader in s.readers)


@pytest.mark.parametrize("init_exits", [False, True])
@pytest.mark.parametrize("service", [False, "offered"], indirect=True)
def test_finalized_join_requires_worker_publication_and_original_init_exit(recording, init_exits):
    s = recording

    def action():
        assert s.driver.start_recording()
        assert s.driver.finish_recording()
        assert s.readers[0].phase == "published" and s.native_captures[0].published
        assert s.journal.machine.state.recording_outcome == "unconfirmed"
        if init_exits:
            s.init_exit = True
            s.engine.dead.add(s.witness.identity.container_id)

    assert run(s, action).phase == "review"
    assert len(s.finalized_recoveries) == int(init_exits) and not s.recoveries
    assert s.readers[0].closed and s.start_calls[0].closed
    assert len(s.engine.sent) == 2  # Synthetic recovery must not assert restoration.


@pytest.mark.parametrize(
    "operation",
    ["native_cancel", "direct_native_cancel", "native_poll", "start_again", "finish_again"],
)
def test_wrong_phase_and_repeated_actions_are_sticky_refusals(recording, operation):
    s = recording

    def action():
        assert s.driver.start_recording()
        if operation == "native_cancel":
            s.driver.cancel_native()
        elif operation == "direct_native_cancel":
            s.driver.native.cancel()
        elif operation == "native_poll":
            s.driver.native.poll(lambda _: None)
        elif operation == "start_again":
            s.driver.start_recording()
        else:
            assert s.driver.finish_recording()
            s.driver.finish_recording()

    native_tests.direct_native.resources.denied(lambda: run(s, action))
    assert s.service.failed and s.service.closed and len(s.start_calls) == 1
    assert "transport_close" not in s.trace and not s.recoveries and not s.finalized_recoveries


@pytest.mark.parametrize(
    "field", ["recording", "session", "run", "ledger", "operator", "start_attempt", "relay", "read"]
)
def test_replaced_objects_cannot_redirect_recording_or_owned_cleanup(recording, monkeypatch, field):
    s = recording

    def action():
        assert s.driver.start_recording()
        target = s.driver if field in ("recording", "session") else s.driver.recording
        with monkeypatch.context() as patch:
            patch.setattr(target, field, object())
            native_tests.direct_native.resources.denied(s.driver.finish_recording)

    native_tests.direct_native.resources.denied(lambda: run(s, action))
    assert s.start_calls[0].closed and s.native_captures[0].closed
    assert not s.finish_calls and s.service.failed


@pytest.mark.parametrize("state", ["cancelled", "unconfirmed", "exited", "ledger_changed"])
def test_ineligible_native_cannot_grant_recording(recording, state):
    s = recording

    def action():
        if state == "cancelled":
            s.driver.cancel_native()
        elif state == "unconfirmed":
            s.driver.native.uncertain = True
        elif state == "exited":
            s.driver.native.operator.done = True
        else:
            (s.ledger.directory / "0000.json").write_bytes(b"PRIVATE changed ledger")
        s.driver.start_recording()

    native_tests.direct_native.resources.denied(lambda: run(s, action))
    assert not s.start_calls and s.driver.recording is None
    assert not s.driver.native.retired and s.service.failed


def test_recording_outside_original_running_owner_is_not_authorized(recording):
    s = recording
    native_tests.direct_native.resources.denied(s.driver.start_recording)
    assert not s.start_calls and not s.driver.recording_attempted


@pytest.mark.parametrize("field", ["used", "confirm_attempted"])
def test_retirement_allows_launch_failure_but_not_loss_of_original_consumption(
    recording, monkeypatch, field
):
    s = recording

    def action():
        assert s.driver.start_recording()
        with monkeypatch.context() as patch:
            patch.setattr(s.run, field, False)
            native_tests.direct_native.resources.denied(s.driver.finish_recording)

    native_tests.direct_native.resources.denied(lambda: run(s, action))
    assert s.service.failed and s.start_calls[0].closed and not s.finish_calls


@pytest.mark.parametrize("field", ["reader", "completion"])
def test_finalized_objects_remain_original_until_recovery(recording, monkeypatch, field):
    s = recording

    def action():
        assert s.driver.start_recording() and s.driver.finish_recording()
        with monkeypatch.context() as patch:
            patch.setattr(s.driver.recording, field, object())
            with pytest.raises(m.UnconfirmedOperator):
                s.driver.recording.poll(lambda _: None)

    # Direct private phase refusal cannot borrow a replacement object or clean
    # it up; restored original objects may still take only clock expiry/review.
    assert run(s, action).phase == "review"
    assert s.readers[0].closed and s.start_calls[0].closed and not s.finalized_recoveries


@pytest.mark.parametrize("stage", ["start", "started", "finish", "collect"])
def test_interruption_closes_only_original_owned_resources_without_retries(
    recording, monkeypatch, stage
):
    s = recording

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("PRIVATE interrupted boundary")

    def action():
        if stage == "start":
            monkeypatch.setattr(r.app_begin.AppStart, "start_once", interrupt)
        elif stage == "started":
            monkeypatch.setattr(m.begin.relayed.Relay, "started", interrupt)
        if stage in ("start", "started"):
            s.driver.start_recording()
        else:
            assert s.driver.start_recording()
            if stage == "finish":
                monkeypatch.setattr(m.begin.relayed.Relay, "completed", interrupt)
            else:
                monkeypatch.setattr(r.finalized.AppAuthorizedFinalized, "collect_exit", interrupt)
            s.driver.finish_recording()

    with pytest.raises(KeyboardInterrupt):
        run(s, action)
    assert s.service.closed and s.service.failed and s.start_calls[0].closed
    assert s.native_captures[0].closed and not s.audit.closed
    assert all(reader.closed for reader in s.readers)
    assert not s.recoveries and not s.finalized_recoveries and len(s.engine.sent) == 2


@pytest.mark.parametrize("attempt", ["before_start", "failed_start", "failed_finish"])
def test_finish_never_reuses_a_failed_or_unstarted_recording(recording, attempt):
    s = recording

    def action():
        if attempt == "failed_start":
            s.start_fault = "after_authorization"
            assert not s.driver.start_recording()
        elif attempt == "failed_finish":
            assert s.driver.start_recording()
            s.finish_fault = "receive"
            assert not s.driver.finish_recording()
        s.driver.finish_recording()

    native_tests.direct_native.resources.denied(lambda: run(s, action))
    assert s.service.failed and len(s.finish_calls) == int(attempt == "failed_finish")
    assert not s.recoveries and not s.finalized_recoveries
