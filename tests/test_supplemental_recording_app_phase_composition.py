"""Real App recording phase, AppStart and active-input qualification joins.

The launch/begin fixture supplies real files, socket inodes, private journals,
ledger and App classes. The outer service shell, native returned messages,
retained continuity and Engine/Startup facts are explicitly synthetic here.
The separate service suites exercise the real outer session. This is not an
installed/native lifetime or an audible recording acceptance test.
"""

import os
import time
from dataclasses import asdict
from threading import Lock, get_ident
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_begin as begins
from . import test_supplemental_recording_app_service_recording as routing

r, m = routing.r, routing.native_tests.m
candidate, app, native, launch_case, execution, joined = (
    begins.candidate,
    begins.app,
    begins.native,
    begins.launch_case,
    begins.execution,
    begins.joined,
)
layout, image_umask, supervised, image, configured = (
    begins.layout,
    begins.image_umask,
    begins.supervised,
    begins.image,
    begins.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture
def composed(joined, monkeypatch):
    s = joined
    driver = object.__new__(m.AppService)
    driver.owner = os.getpid(), get_ident()
    driver.used, driver.recording_attempted = True, False
    driver.recording = driver._original_recording = None
    driver.plan, driver.projected, driver.journal = s.plan, s.projected, s.journal
    driver.lock, driver._cleanup = Lock(), []
    driver.lock.acquire()
    driver.session = SimpleNamespace(read=None)
    driver.failed = False

    def fail(error):
        driver.failed = True
        if not isinstance(error, Exception):
            raise error
        raise m.operator.UnconfirmedOperator(m.operator.MESSAGE) from None

    # Deliberately synthetic service lifetime, not reconstructed installed custody.
    original = SimpleNamespace(_fail=fail)
    driver.objects = None, original

    def context(*, recovering=False):
        assert not driver.failed and driver.used and driver.lock.locked()
        assert not recovering
        assert driver.owner == (os.getpid(), get_ident())
        if driver.recording is not None:
            driver.recording._context()
        else:
            assert not driver.recording_attempted

    driver._context = context
    native = driver.native = object.__new__(m.AppNativePhase)
    native.service, native.run, native.ledger = driver, s.run, s.ledger
    native.operator = SimpleNamespace(done=False)
    native.used = native.confirmed = True
    native.uncertain = native.retired = native.cancel_attempted = native.recovery_attempted = False
    native.ledger_state = s.ledger.state
    native.ledger_identity, native.ledger_lock = s.ledger._directory_identity, s.ledger._lock
    s.driver = driver
    s.started_returns = []

    def started(relay):
        assert native.retired and driver.recording.start_attempt is s.run.begin_owner
        assert relay is driver.recording.relay and relay.phase == "started"
        if s.fault == "start_return":
            raise OSError("PRIVATE lost started return")
        expected = r.begin.binding.protected.evidence.RecordingExpectation(
            s.plan.case,
            s.idle.generation,
            s.projected.host.contract.audio_endpoint_sha256,
            "2026-09-27T10:00:00-06:00",
        )
        s.ledger.started(expected, now=time.monotonic(), success_sha256="5" * 64)
        if s.fault == "lost_started_ack":
            raise OSError("PRIVATE lost durable started acknowledgment")
        relay.phase, relay.expected = "completed", expected
        s.started_returns.append(expected)
        return expected

    monkeypatch.setattr(r.begin.relayed.Relay, "started", started)

    def continuity_init(continued, idle, guard):
        assert idle is s.idle and guard is driver.recording.relay.guard
        continued.owner = driver.owner
        continued.plan, continued.idle, continued.guard = s.plan, idle, guard
        continued.ready, continued.finish_by = s.run.ready, s.plan.lease["stop_by"]
        continued.closed = continued.failed = False

    def continued_read(continued):
        assert continued.owner == driver.owner and not continued.closed
        assert time.monotonic() < continued.finish_by
        stamp = r.launch.plans.clock.read().boottime_ns / r.launch.plans.clock.NS
        return r.launch.idle_module.Continuity(s.prebegin_idle, stamp, continued.guard.check())

    def continued_guard(continued, deadline):
        assert continued.owner == driver.owner and not continued.closed
        assert not continued.failed
        assert time.monotonic() < min(deadline, continued.finish_by)

    monkeypatch.setattr(r.launch.idle_module.PostBegin, "__init__", continuity_init)
    monkeypatch.setattr(r.launch.idle_module.PostBegin, "_guard", continued_guard)
    monkeypatch.setattr(r.launch.idle_module.PostBegin, "read", continued_read)
    try:
        yield s
    finally:
        for close in reversed(driver._cleanup):
            close()
        driver.lock.release()


def test_phase_consumes_actual_app_start_and_original_durable_ledger(composed):
    s = composed
    before = s.plan.raw, s.plan.lease, s.run.ready.received_at, s.run.ready.watch_deadline
    assert s.driver.start_recording()
    p = s.driver.recording
    assert type(p) is r.AppRecordingPhase and type(p.start_attempt) is r.app_begin.AppStart
    assert p.start_attempt is s.run.begin_owner and p.relay is p.start_attempt.relay
    assert p.started and s.ledger.state.count == 3
    assert s.ledger.state.expected == s.started_returns[0]
    assert p.relay.expected is s.started_returns[0]
    assert r.launch.binding.load(s.ledger.directory, s.ledger.binding) == s.ledger.state
    assert before == (s.plan.raw, s.plan.lease, s.run.ready.received_at, s.run.ready.watch_deadline)
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert not s.driver.failed and not s.run.failed


@pytest.mark.parametrize(
    "fault",
    [
        "probe_prepare",
        "host_prepare",
        "probe",
        "source_during_probe",
        "relay",
        "start_return",
        "lost_started_ack",
    ],
)
def test_real_begin_failure_stays_uncertain_without_replacing_app_run(composed, fault):
    s = composed
    original = s.run
    s.fault = fault
    assert not s.driver.start_recording()
    p = s.driver.recording
    assert p.uncertain and not p.started and s.driver.native.retired
    assert p.run is original and s.run.begin_attempted
    assert not s.driver.failed and not s.started_returns
    assert not p.abandon_attempted and p.reader is None
    assert s.journal.machine.state.recording_outcome in ("not_attempted", "unconfirmed")
    if fault == "lost_started_ack":
        assert s.ledger.state.count == 3 and s.ledger.state.expected is not None
    else:
        assert s.ledger.state.expected is None
    s.driver._context()  # Same phase remains available for outer clock-only expiry.


def test_real_active_qualification_borrows_original_start_and_inputs(composed, monkeypatch):
    s = composed
    assert s.driver.start_recording()
    p = s.driver.recording

    def obsolete(*args):
        pytest.fail("post-begin must not renew Ready or idle observations")

    monkeypatch.setattr(r.launch.received.Ready, "check_before_begin", obsolete)
    monkeypatch.setattr(r.launch.idle_module.Idle, "read", obsolete)
    p._prepare_active()
    q, host = p.active_qualifier, p.active_host
    assert type(q) is r.observation.active.NativeActiveQualification
    assert type(host) is r.observation.AppRetainedHost
    assert q.prebegin is s.run.ready_qualification and q.start is p.start_attempt
    assert q.continuity is p.continuity and host.start is p.start_attempt
    assert q() is None  # Actual source/runtime, sockets and receipt inventory.
    original = p.active_objects, s.plan.raw, s.plan.lease, s.ledger.state
    p._prepare_active()
    assert original == (p.active_objects, s.plan.raw, s.plan.lease, s.ledger.state)
    assert s.run.ready_qualification.native_active_owner is q
    assert not p.uncertain and not s.run.failed


@pytest.mark.parametrize(
    "field", ["image_environment_sha256", "timezone", "hostname", "architecture", "runtime_workers"]
)
def test_actual_active_qualification_rejects_changed_original_profile(composed, monkeypatch, field):
    s = composed
    assert s.driver.start_recording()
    p = s.driver.recording
    p._prepare_active()
    monkeypatch.setattr(s.run.ready_qualification, field, "changed")
    with pytest.raises(r.launch.UnconfirmedHostLaunch) as caught:
        p.active_qualifier()
    assert "PRIVATE" not in str(caught.value)
    assert p.active_qualifier.failed
    assert s.journal.machine.state.recording_outcome == "unconfirmed"


@pytest.fixture
def finalizing(composed, monkeypatch):
    s = composed
    exits = r.begin.worker_exit
    operator = object.__new__(exits.reconcile.Operator)
    operator.plan, operator.clock, operator.pins = s.plan, s.plan.original_clock, s.run.pins
    operator.owner = s.driver.owner
    operator.failed = operator.closed = operator.done = operator.publish_attempted = False
    operator.result_sha256 = None
    s.driver.native.operator = operator
    assert s.driver.start_recording()
    phase, relay = s.driver.recording, s.driver.recording.relay
    expected = relay.expected
    s.finish_fault = None
    s.finish_trace = []

    def tick(name):
        s.finish_trace.append(name)
        if s.finish_fault == name:
            raise OSError("PRIVATE " + name)

    collected = r.begin.binding.protected.Collected(
        r.begin.binding.protected.Files(
            s.plan.candidate.contract.sha256, "finalized", "6" * 64, expected.generation
        ),
        artifact=object(),  # Explicit file-result boundary, not an authenticated WAV.
    )

    def complete(*, progress_directory):
        assert progress_directory is None
        tick("completion")
        acknowledgment = r.begin.binding.protected.Acknowledgment(
            expected.case,
            expected.generation,
            s.plan.candidate.contract.sha256,
            expected.started_at,
            "7" * 64,
            "8" * 64,
        )
        s.ledger.completed(acknowledgment, now=time.monotonic())
        relay.phase = "closed"
        relay.completion = SimpleNamespace(collected=collected)
        return relay.completion

    relay.completed = complete
    relay.recheck_completed = lambda: collected

    class Finalized:
        """Synthetic fourth-return/exit collector, not native receipt evidence."""

        def __init__(self, original, observer):
            assert original is relay and observer is operator
            self.relay, self.operator, self.completion = relay, operator, relay.completion
            self.used = self.failed = self.closed = False
            self._exit_receipt = None

        def _context(self):
            assert not self.closed and not self.failed
            assert not operator.closed and not operator.failed

        def collect_exit(self):
            tick("collect")
            self.used, relay.phase = True, "exited"
            s.run.client.close()
            value = exits.Exited(2, 3, 4, "a" * 64, "b" * 64, "c" * 64, time.monotonic())
            self._exit_receipt = value, r.base.checksum(asdict(value))
            return value

        def close(self):
            self.closed = True

    def poll():
        tick("poll")
        assert phase.reader.phase == "exited"
        operator.done = True
        return exits.reconcile.Evidence(*("a" * 64 for _ in range(8)), 0, False, operator._clock())

    def recheck():
        assert operator.done
        return SimpleNamespace(returncode=0, engine_sha256="c" * 64)

    def publish(journal):
        assert journal is s.journal and operator.done and not operator.publish_attempted
        tick("publish_before")
        operator.publish_attempted = True
        digest = "d" * 64
        journal.append(
            dict(
                kind="operator_exited",
                boot_id=s.plan.boot,
                now=operator._clock(),
                generation=operator.pins.generation,
                intent_sha256=journal.machine.state.launch_intent_sha256,
                exit_evidence_sha256=digest,
            )
        )
        operator.result_sha256 = digest
        tick("publish_after")
        return digest

    ready_close = s.run.ready.close

    def close():
        tick("ready_close")
        ready_close()
        s.run.ready.processes.closed = True

    monkeypatch.setattr(exits, "Finalized", Finalized)
    monkeypatch.setattr(operator, "poll", poll)
    monkeypatch.setattr(operator, "recheck", recheck)
    monkeypatch.setattr(operator, "publish", publish)
    monkeypatch.setattr(s.run.ready, "close", close)
    try:
        yield s
    finally:
        # The fault targets the marked phase operation, not fixture teardown.
        s.finish_fault = None


def test_phase_joins_real_finalized_reader_and_separate_exit_publication(finalizing):
    s = finalizing
    before = tuple(s.journal.entries)
    assert s.driver.finish_recording()
    p = s.driver.recording
    assert type(p.reader) is r.finalized.AppAuthorizedFinalized
    assert p.reader.start is p.start_attempt and p.reader.operator is p.operator
    assert p.reader.phase == "published" and s.ledger.state.closed
    assert s.finish_trace == [
        "completion",
        "collect",
        "ready_close",
        "poll",
        "publish_before",
        "publish_after",
    ]
    assert tuple(s.journal.entries[:-1]) == before
    assert s.journal.machine.state.operator_exit_sha256 == p.operator.result_sha256
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert not p.recovery_attempted  # Finalization is not App restoration.


@pytest.mark.parametrize(
    "fault", ["completion", "collect", "ready_close", "poll", "publish_before", "publish_after"]
)
def test_real_finalized_join_keeps_lost_acknowledgments_uncertain(finalizing, fault):
    s = finalizing
    s.finish_fault = fault
    assert not s.driver.finish_recording()
    p = s.driver.recording
    assert p.uncertain and p.finish_attempted and not p.recovery_attempted
    assert not p.abandon_attempted and not s.driver.failed
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.finish_trace.count(fault) == 1
    s.driver._context()
