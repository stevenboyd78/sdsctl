"""Original service phase routing with real files/policy, synthetic native I/O.

Ready, Operator and closed-native reader boundaries are EXPLICIT fixtures here.
Their actual pidfd/source/runtime/exit gates have separate tests. These tests
do not claim an installed service, actual native capture or normal restoration.
"""

import time
from contextlib import nullcontext
from dataclasses import asdict, replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_idle_launch as passive

m, resources, services = passive.m, passive.resources, passive.services
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
    launch_fixture,
) = (
    passive.layout,
    passive.tree,
    passive.routing,
    passive.projection,
    passive.binding,
    passive.directory,
    passive.prepared,
    passive.joined,
    passive.before_handoff,
    passive.transfer,
    passive.service,
    passive.candidate_fixture,
    passive.launch_fixture,
)


@pytest.fixture
def native(launch_fixture, monkeypatch):
    s = launch_fixture
    s.native_calls, s.native_captures, s.closed_transports, s.recoveries = [], [], [], []
    s.launch_fault = s.capture_fault = s.observe_fault = s.close_fault = None
    s.worker_exit = s.init_exit = False
    s.audit_endpoint = type(s.endpoint)()

    def snapshot(active):
        now = s.service._now()
        return replace(
            s.journal.machine.baseline,
            sampled_at=now,
            normal=m.base.App(s.plan.normal.pin, "stopped"),
            candidate=m.base.App(
                s.plan.candidate.pin,
                "running",
                s.run.pins.generation,
                True if active else None,
                False if active else None,
            ),
        )

    def launch(run):
        assert run is s.run and not run.used
        assert s.service.coordinator.finished and s.service.native is not None
        assert s.session.read is s.service.native.read
        with pytest.raises(m.UnconfirmedOperator):
            s.session.read()
        run.used = True
        s.native_calls.append(run)
        if s.launch_fault == "before":
            run.failed = True
            raise OSError("PRIVATE launch failure")
        run.action = s.append(
            "authorize_operator",
            generation=run.pins.generation,
            bootstrap_sha256=s.plan.bootstrap.sha256,
            launch_plan_sha256=run.command.plan_sha256,
            idle_evidence_sha256="c" * 64,
            observation=asdict(snapshot(False)),
        )
        if s.launch_fault == "intent":
            run.failed = True
            raise OSError("PRIVATE lost launch acknowledgement")
        run.claim = object()

        def close():
            s.closed_transports.append(run.client)
            s.endpoint.close()
            if s.close_fault:
                raise OSError("PRIVATE lost transport close acknowledgement")

        run.client = SimpleNamespace(close=close)
        run.ready = SimpleNamespace(client=run.client, close=lambda: None)
        run.probe = SimpleNamespace(close=lambda: None)
        run.confirm_attempted = True
        s.append(
            "operator_ready",
            generation=run.pins.generation,
            intent_sha256=run.action.intent_sha256,
            ready_evidence_sha256="d" * 64,
            received_at=s.service._now(),
            observation=asdict(snapshot(True)),
        )
        if s.launch_fault == "ready":
            run.failed = True
            raise OSError("PRIVATE lost Ready acknowledgement")

    class Operator:
        def __init__(self, plan, ready, endpoint):
            assert plan is s.plan and ready is s.run.ready and endpoint is s.audit_endpoint
            assert s.ledger.state.count == 1
            s.native_captures.append(self)
            if s.capture_fault:
                raise OSError("PRIVATE capture failure")
            self.plan, self.endpoint = plan, endpoint
            self.done = self.closed = self.published = False
            self.polls = 0

        def poll(self):
            self.polls += 1
            if s.observe_fault == "poll":
                raise OSError("PRIVATE observation failure")
            if not s.worker_exit:
                return None
            self.done = True
            return object()  # Explicit synthetic exit boundary, not real Evidence.

        def publish(self, journal):
            assert self.done and not self.published and journal is s.journal
            self.published = True
            if s.observe_fault == "publish_before":
                raise OSError("PRIVATE exit publication failure")
            s.append(
                "operator_exited",
                generation=s.run.pins.generation,
                intent_sha256=s.run.action.intent_sha256,
                exit_evidence_sha256="e" * 64,
            )
            if s.observe_fault == "publish_after":
                raise OSError("PRIVATE lost exit publication acknowledgement")

        def _exited(self, role):
            assert role == "init"
            return s.init_exit

        def close(self):
            self.closed = True

    class NeverAuthorized:
        def __init__(self, operator, ledger, journal):
            assert operator is s.service.native.operator and operator.published
            assert ledger is s.ledger and journal is s.journal
            assert s.init_exit and journal.machine.process_bound(m.base.CANDIDATE, exited=True)
            self.closed = False

        def close(self):
            self.closed = True

    def recover(reader, run, session, wait):
        assert run is s.run and session is s.session and not s.recoveries
        s.recoveries.append((reader, run, session))
        # Exercise the bound wait without fabricating restoration success.
        wait(0.25)
        expire(s)
        return session.poll()

    def forbidden(*args, **kwargs):
        pytest.fail("Pre-recording routing cannot authorize or start recording")

    monkeypatch.setattr(m.launch.Launch, "start_confirmed", launch)
    monkeypatch.setattr(m.begin.worker_exit.reconcile, "Operator", Operator)
    monkeypatch.setattr(m.begin.worker_exit.reconcile, "NeverAuthorized", NeverAuthorized)
    monkeypatch.setattr(m.begin, "recover_never_authorized", recover)
    monkeypatch.setattr(m.begin.Start, "__init__", forbidden)
    return s


def prepare(s):
    s.run = passive.prepare(s)
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    s.ledger = m.launch.binding.Ledger(path, s.run.pins.host, now=time.monotonic())


def start(s):
    return s.service.start_native(s.ledger, s.audit_endpoint)


def run(s, action, after=None):
    services.publish(s, "request")
    visited = []

    def wait(seconds):
        assert seconds == 0.25
        if s.journal.machine.state.phase == "candidate_idle" and not visited:
            visited.append(True)
            s.witness = s.attach_fixture_fd()
            s.candidate = resources.prepare(s)
            prepare(s)
            action()
        elif visited:
            if after is None:
                expire(s)
            else:
                after()

    return s.service.run(wait)


@pytest.fixture
def phase(native, monkeypatch):
    native.patch = monkeypatch
    native.original_clock_read = m.plans.clock.read
    native.expired = False
    return native


def expire(s):
    if s.expired:
        return
    s.expired = True
    state = s.journal.machine.state
    target = (
        state.trial_deadline + m.base.COMMAND_SECONDS
        if state.phase == "candidate_running"
        else state.deadline
    ) + 1
    delta = int(target * m.plans.clock.NS) - s.original_clock_read().boottime_ns

    def late():
        value = s.original_clock_read()
        return replace(
            value,
            before_ns=value.before_ns + delta,
            after_ns=value.after_ns + delta,
            boottime_ns=value.boottime_ns + delta,
        )

    s.patch.setattr(m.plans.clock, "read", late)


def test_explicit_handoff_retires_idle_before_launch_and_keeps_original_session(phase):
    s = phase
    original = s.session

    def action():
        assert start(s) is True
        owner = s.service.native
        assert owner.confirmed and not owner.uncertain
        assert owner.operator is s.native_captures[0]
        assert s.session is original and s.service.coordinator.finished
        assert s.ledger.state.count == 1 and not s.closed_transports
        assert s.journal.machine.state.authorization_generation is None

    assert run(s, action).phase == "review"
    assert len(s.native_calls) == len(s.native_captures) == 1
    assert s.native_captures[0].closed and not s.audit_endpoint.closed
    assert s.service.closed and not s.service.failed and s.witness.closed
    assert len(s.engine.sent) == 2 and not s.recoveries
    assert s.journal.machine.state.recording_outcome == "not_attempted"


@pytest.mark.parametrize("fault", ["before", "intent", "ready", "capture"])
def test_launch_uncertainty_consumes_attempt_but_preserves_original_clock_expiry(phase, fault):
    s = phase

    def action():
        if fault == "capture":
            s.capture_fault = True
        else:
            s.launch_fault = fault
        assert start(s) is False
        assert s.service.native.uncertain and not s.service.failed
        assert s.service.native.operator is None
        services.publish(s, "cancel_idle")  # Must not restore through the retired phase.

    assert run(s, action).phase == "review"
    assert len(s.native_calls) == 1 and not s.recoveries
    assert s.service.closed and not s.service.failed and len(s.engine.sent) == 2
    assert s.journal.machine.state.authorization_generation is None
    assert not s.journal.machine.state.finish_requested


@pytest.mark.parametrize("lost_close", [False, True])
def test_cancel_only_closes_transport_once_and_cannot_claim_exit(phase, lost_close):
    s = phase

    def action():
        assert start(s)
        s.close_fault = lost_close
        assert s.service.cancel_native() is (not lost_close)
        assert s.service.native.cancel_attempted
        assert len(s.closed_transports) == 1
        assert not s.native_captures[0].closed and not s.native_captures[0].done
        assert not s.journal.machine.state.finish_requested

    assert run(s, action).phase == "review"
    assert len(s.closed_transports) == 1 and not s.recoveries
    assert s.ledger.state.count == 1 and len(s.engine.sent) == 2


@pytest.mark.parametrize("init_exits", [False, True])
def test_original_worker_and_init_exits_gate_same_session_recovery_entry(phase, init_exits):
    s = phase

    def action():
        assert start(s)
        assert s.service.cancel_native()
        s.worker_exit = True
        if init_exits:
            s.init_exit = True
            s.engine.dead.add(s.witness.identity.container_id)

    assert run(s, action).phase == "review"
    assert s.native_captures[0].published
    assert len(s.recoveries) == int(init_exits)
    assert s.ledger.state.count == 1 and len(s.engine.sent) == 2
    if init_exits:
        assert s.recoveries[0][2] is s.session and s.recoveries[0][0].closed


@pytest.mark.parametrize("fault", ["poll", "publish_before", "publish_after"])
def test_failed_exit_observation_is_not_retried_and_clock_still_expires(phase, fault):
    s = phase

    def action():
        assert start(s)
        s.observe_fault = fault
        s.worker_exit = True

    assert run(s, action).phase == "review"
    assert s.native_captures[0].polls == 1 and s.service.native.uncertain
    assert not s.recoveries and not s.service.failed and len(s.engine.sent) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "same_endpoint",
        "closed_endpoint",
        "bad_ledger",
        "changed_bytes",
        "poisoned",
        "busy",
        "finish",
        "ledger_intent",
        "old_ledger",
        "future_ledger",
        "wrong_location",
        "wrong_binding",
    ],
)
def test_invalid_native_inputs_refuse_before_ownership_transition(phase, fault):
    s = phase

    def action():
        if fault == "same_endpoint":
            s.audit_endpoint = s.endpoint
        elif fault == "closed_endpoint":
            s.audit_endpoint.close()
        elif fault == "bad_ledger":
            s.ledger = object()
        elif fault == "changed_bytes":
            (s.ledger.directory / "0000.json").write_bytes(b"PRIVATE")
        elif fault == "poisoned":
            s.ledger._poisoned = True
        elif fault == "finish":
            services.publish(s, "cancel_idle")
            assert s.inbox.consume()
        elif fault == "ledger_intent":
            now = time.monotonic()
            s.ledger.start_intent(
                now=now,
                generation=s.run.pins.generation,
                authorization_sha256="a" * 64,
                start_by=now + 1,
                finish_by=now + 5,
            )
        elif fault == "old_ledger":
            s.ledger.state = replace(
                s.ledger.state, now=s.plan.original_clock.before_ns / m.plans.clock.NS - 1
            )
        elif fault == "future_ledger":
            s.ledger.state = replace(s.ledger.state, now=time.monotonic() + 100)
        elif fault == "wrong_location":
            s.ledger.directory = s.ledger.directory.with_name("not-the-ledger")
        elif fault == "wrong_binding":
            s.ledger.binding = replace(s.ledger.binding, plan_sha256="f" * 64)
        else:
            s.ledger._lock.acquire()
        try:
            start(s)
        finally:
            if fault == "busy":
                s.ledger._lock.release()

    resources.denied(lambda: run(s, action))
    assert not s.native_calls and s.service.native is None
    assert s.service.native_attempted and not s.service.coordinator.finished
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2


@pytest.mark.parametrize("operation", ["start", "cancel", "idle_poll", "observe", "prepare"])
def test_phase_reuse_or_idle_reentry_is_sticky_and_cannot_dispatch(phase, operation):
    s = phase

    def action():
        assert start(s)
        if operation == "start":
            resources.denied(lambda: start(s))
        elif operation == "cancel":
            s.service.cancel_native()
            resources.denied(s.service.cancel_native)
        elif operation == "idle_poll":
            resources.denied(lambda: s.service.coordinator.poll(lambda _: None))
        elif operation == "observe":
            resources.denied(s.service.observe_candidate)
        else:
            resources.denied(lambda: resources.prepare(s))

    resources.denied(lambda: run(s, action))
    assert s.service.closed and s.service.failed and len(s.native_calls) == 1
    assert len(s.engine.sent) == 2 and not s.recoveries


@pytest.mark.parametrize(
    "field",
    ["session", "read", "operator", "run", "ledger", "endpoint", "client", "ready", "probe"],
)
def test_replaced_native_objects_cannot_redirect_action_or_cleanup(phase, monkeypatch, field):
    s = phase

    def action():
        assert start(s)
        with monkeypatch.context() as patch:
            if field == "session":
                obj = s.service
            elif field == "read":
                obj = s.session
            elif field in ("client", "ready", "probe"):
                obj = s.run
            else:
                obj = s.service.native
            patch.setattr(obj, field, object())
            resources.denied(s.service.cancel_native)

    resources.denied(lambda: run(s, action))
    assert s.native_captures[0].closed and not s.closed_transports
    assert s.service.failed and len(s.engine.sent) == 2


def test_no_native_handoff_outside_original_loop(phase):
    s = phase
    resources.denied(lambda: s.service.start_native(object(), s.audit_endpoint))
    assert not s.native_calls and not s.service.native_attempted


@pytest.mark.parametrize("fault", ["finish", "exited", "ledger_changed", "ledger_poisoned"])
def test_changed_pre_recording_state_cannot_cancel_or_fall_back_to_idle(phase, fault):
    s = phase

    def action():
        assert start(s)
        if fault == "finish":
            s.append("finish")
        elif fault == "exited":
            s.service.native.operator.done = True
        elif fault == "ledger_changed":
            (s.ledger.directory / "0000.json").write_bytes(b"PRIVATE")
        else:
            s.ledger._poisoned = True
        s.service.cancel_native()

    resources.denied(lambda: run(s, action))
    assert not s.closed_transports and not s.recoveries
    assert s.service.native.cancel_attempted and s.native_captures[0].closed


def test_original_objects_still_checked_after_lost_close_acknowledgement(phase, monkeypatch):
    s = phase

    def action():
        assert start(s)
        original = s.service.session

        def close():
            s.service.session = object()
            raise OSError("PRIVATE lost close acknowledgement")

        monkeypatch.setattr(s.run.client, "close", close)
        try:
            resources.denied(s.service.cancel_native)
        finally:
            s.service.session = original

    resources.denied(lambda: run(s, action))
    assert s.service.failed and s.native_captures[0].closed


def test_restoration_wait_rejects_owner_replacement_and_does_not_restart_session(phase):
    s = phase

    def action():
        assert start(s)
        s.worker_exit = s.init_exit = True
        s.engine.dead.add(s.witness.identity.container_id)

    def after():
        assert len(s.recoveries) == 1
        s.service.native.reader = object()

    resources.denied(lambda: run(s, action, after))
    assert len(s.recoveries) == 1 and len(s.engine.sent) == 2
    assert s.recoveries[0][0].closed and s.native_captures[0].closed


def test_foreign_thread_cannot_take_native_ownership(phase):
    s, errors = phase, []

    def action():
        def foreign():
            try:
                start(s)
            except m.UnconfirmedOperator as error:
                errors.append(str(error))

        worker = Thread(target=foreign)
        worker.start()
        worker.join(2)
        assert not worker.is_alive() and errors == [m.MESSAGE]

    resources.denied(lambda: run(s, action))
    assert not s.native_calls and not s.service.native_attempted


def test_interrupt_does_not_become_a_recoverable_launch_failure(phase, monkeypatch):
    s = phase

    def interrupt(_):
        raise KeyboardInterrupt()

    monkeypatch.setattr(m.launch.Launch, "start_confirmed", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(s, lambda: start(s))
    assert s.service.failed and s.service.closed and not s.recoveries
    assert s.service.coordinator.finished and len(s.engine.sent) == 2


@pytest.mark.parametrize("kind", ["valid", "malformed", "pending", "unknown", "directory"])
def test_unconsumed_idle_notice_refuses_native_before_retirement(phase, kind):
    s = phase
    retained = []

    def action():
        path = s.inbox.path / "cancel_idle.json"
        if kind == "valid":
            services.publish(s, "cancel_idle")
        elif kind == "directory":
            path.mkdir(mode=0o700)
        else:
            if kind == "pending":
                path = s.inbox.path / ".pending-cancel_idle"
            elif kind == "unknown":
                path = s.inbox.path / "unrecognized.json"
            path.write_bytes(b"PRIVATE incomplete cancellation")
            path.chmod(0o600)
        retained.append(path)
        start(s)

    resources.denied(lambda: run(s, action))
    assert retained[0].exists()
    assert s.service.native_attempted and s.service.native is None
    assert not s.service.coordinator.finished and not s.run.used
    assert not s.native_calls and not s.recoveries and len(s.engine.sent) == 2
    assert not s.journal.machine.state.finish_requested
    assert s.journal.machine.state.launch_intent_sha256 is None
    assert s.inbox.failed and s.inbox.closed and not s.inbox.lock.locked()


@pytest.mark.parametrize("kind", ["publisher", "reader", "inbox"])
def test_busy_idle_inbox_is_not_treated_as_absent_cancellation(phase, kind):
    s = phase

    def action():
        guard = (
            nullcontext()
            if kind == "inbox"
            else m.launch.binding.protected._private_directory(
                s.inbox.path, exclusive=kind == "publisher"
            )
        )
        if kind == "inbox":
            s.inbox.lock.acquire()
        try:
            with guard:
                start(s)
        finally:
            if kind == "inbox":
                # A failed acquisition must not unlock someone else's lock.
                assert s.inbox.lock.locked()
                s.inbox.lock.release()

    resources.denied(lambda: run(s, action))
    assert s.service.native is None and not s.run.used
    assert not s.service.coordinator.finished and not s.native_calls
    assert s.inbox.failed and len(s.engine.sent) == 2
    assert s.journal.machine.state.launch_intent_sha256 is None


@pytest.mark.parametrize("stage", ["before_intent", "after_ready", "capture"])
def test_idle_publication_excluded_through_native_dispatch_and_capture(phase, monkeypatch, stage):
    s, attempts = phase, []

    def publish_during():
        attempts.append(True)
        resources.denied(s.publisher.publish)
        assert s.publisher.used and s.publisher.failed
        assert not (s.inbox.path / "cancel_idle.json").exists()
        assert s.inbox.lock.locked()

    original_start = m.launch.Launch.start_confirmed
    original_capture = m.begin.worker_exit.reconcile.Operator.__init__

    def launched(run):
        if stage == "before_intent":
            publish_during()
        original_start(run)
        if stage == "after_ready":
            publish_during()

    def captured(operator, *args):
        if stage == "capture":
            publish_during()
        original_capture(operator, *args)

    monkeypatch.setattr(m.launch.Launch, "start_confirmed", launched)
    monkeypatch.setattr(m.begin.worker_exit.reconcile.Operator, "__init__", captured)

    def action():
        s.publisher = m.Publisher(
            s.inbox.original,
            "cancel_idle",
            m.base.checksum(s.journal.entries[0]["event"]),
            m.plans.clock.read().boottime_ns / m.plans.clock.NS,
        )
        assert start(s)
        assert not s.inbox.lock.locked() and not s.inbox.failed
        assert attempts == [True]
        # A failed publisher remains spent after the lock has been released.
        resources.denied(s.publisher.publish)

    assert run(s, action).phase == "review"
    assert len(s.native_calls) == len(s.native_captures) == 1
    assert not s.service.failed and not s.recoveries and len(s.engine.sent) == 2


def test_late_idle_publication_is_not_a_native_cancellation(phase):
    s = phase

    def action():
        assert start(s)
        services.publish(s, "cancel_idle")
        assert (s.inbox.path / "cancel_idle.json").is_file()

    assert run(s, action).phase == "review"
    assert not s.closed_transports and not s.recoveries
    assert not s.journal.machine.state.finish_requested
    assert not s.service.native.cancel_attempted and not s.service.failed


def test_noncooperating_inbox_mutation_after_dispatch_is_not_accepted(phase, monkeypatch):
    s = phase
    original_start = m.launch.Launch.start_confirmed

    def changed(run):
        original_start(run)
        # Bypasses the publication protocol deliberately. It is NOT consumed or
        # mistaken for confirmed cancellation after native activity has started.
        (s.inbox.path / ".pending-cancel_idle").write_bytes(b"PRIVATE")

    monkeypatch.setattr(m.launch.Launch, "start_confirmed", changed)
    resources.denied(lambda: run(s, lambda: start(s)))
    assert s.service.failed and s.service.closed and s.inbox.failed
    assert s.native_captures[0].closed and len(s.native_calls) == 1
    assert not s.closed_transports and not s.recoveries and len(s.engine.sent) == 2
    assert (s.plan.root / "inbox" / ".pending-cancel_idle").exists()
    assert not s.journal.machine.state.finish_requested
