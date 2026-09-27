"""Real service/journal/ledger/FDs; synthetic App/native/exit boundary facts.

This tests service ordering, not installed provenance or scanner acceptance.
Actual App publication, execution and recovery have separate policy suites.
"""

import importlib.util
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_candidate as candidates
from . import test_supplemental_recording_app_recovery as recovery_tests  # noqa: F401
from . import test_supplemental_recording_native_phase as direct_native

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
) = (
    candidates.layout,
    candidates.tree,
    candidates.routing,
    candidates.projection,
    candidates.binding,
    candidates.directory,
    candidates.prepared,
    candidates.joined,
    candidates.before_handoff,
    candidates.transfer,
    candidates.service,
    candidates.candidate_fixture,
    candidates.app_candidate,
)
NAME = "supplemental_recording_app_service"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(candidates.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
services = candidates.direct.services


@pytest.fixture
def app_phase(app_candidate, monkeypatch):
    s = app_candidate
    s.driver = m.AppService(s.startup, s.service)
    s.fault = None
    s.trace, s.native_captures, s.recoveries = [], [], []
    s.worker_exit = s.init_exit = s.close_fault = False
    s.patch, s.original_clock_read, s.expired = monkeypatch, m.operator.plans.clock.read, False

    def tick(name):
        s.trace.append(name)
        if s.fault == name:
            raise OSError("PRIVATE lost " + name)

    class Endpoint:
        closed = False

        def check(self):
            assert not self.closed

        def close(self):
            self.closed = True

    s.endpoint, s.audit = Endpoint(), Endpoint()
    monkeypatch.setattr(m.launch.engine, "Endpoint", Endpoint)

    def publish(owner, original, **kwargs):
        assert owner is s.startup and original is s.candidate.qualifier
        assert s.inbox.lock.locked() and s.service.coordinator.finished
        assert original.native_launch_used is False
        original.native_launch_used = True
        tick("publication")
        prelaunch = object.__new__(m.inputs.NativeLaunchQualification)
        prelaunch.candidate, prelaunch.startup = original, owner
        original.native_launch_publication = object()
        return prelaunch

    def initialize(run, prelaunch, journal, endpoint, *, read):
        tick("construct")
        assert journal is s.journal and endpoint is s.endpoint and read is s.candidate.reader
        run.prelaunch = prelaunch
        run.qualify, run.ready_qualification = prelaunch, None
        run.plan, run.projected, run.journal = s.plan, s.projected, journal
        run.idle, run.witness, run.endpoint, run.read = s.candidate.idle, s.witness, endpoint, read
        run.launch_sha256, run.profile_sha256 = "a" * 64, "b" * 64
        run.command = m.launch.execution.Command(
            str(s.plan.native_root / "launch/launch.json"),
            "a" * 64,
            s.plan.candidate_runtime.source,
            s.plan.lease["ready_by"],
        )
        run.pins = m.launch.engine.dispatch.Pins(
            s.binding, run.command, s.candidate.idle.generation, s.witness.identity
        )
        run.action = run.claim = run.client = run.ready = run.probe = None
        run.used = run.failed = run.closed = run.confirm_attempted = False
        s.run = run

    def snapshot(active):
        return replace(
            s.journal.machine.baseline,
            sampled_at=s.service._now(),
            normal=m.base.App(s.plan.normal.pin, "stopped"),
            candidate=m.base.App(
                s.plan.candidate.pin,
                "running",
                s.run.pins.generation,
                True if active else None,
                False if active else None,
            ),
        )

    def start(run):
        assert run is s.run and s.driver.native is not None and s.inbox.lock.locked()
        assert not run.used
        run.used = True
        tick("launch")
        run.action = s.append(
            "authorize_operator",
            generation=run.pins.generation,
            bootstrap_sha256=s.plan.bootstrap.sha256,
            launch_plan_sha256=run.command.plan_sha256,
            idle_evidence_sha256="c" * 64,
            observation=asdict(snapshot(False)),
        )
        tick("intent")
        run.claim = object()

        def close():
            tick("transport_close")
            s.endpoint.close()
            if s.close_fault:
                raise OSError("PRIVATE lost close")

        run.client = SimpleNamespace(close=close)
        run.ready = SimpleNamespace(client=run.client)
        run.probe = object()
        run.confirm_attempted = True
        s.append(
            "operator_ready",
            generation=run.pins.generation,
            intent_sha256=run.action.intent_sha256,
            ready_evidence_sha256="d" * 64,
            received_at=s.service._now(),
            observation=asdict(snapshot(True)),
        )
        tick("ready")

    def close_run(run):
        run.closed = True
        s.trace.append("run_cleanup")

    class Operator:
        def __init__(self, plan, ready, endpoint):
            assert plan is s.plan and ready is s.run.ready and endpoint is s.audit
            tick("capture")
            self.plan, self.endpoint = plan, endpoint
            self.done = self.closed = self.published = False
            self.polls = 0
            s.native_captures.append(self)

        def poll(self):
            self.polls += 1
            tick("exit_poll")
            if not s.worker_exit:
                return None
            self.done = True
            return object()

        def publish(self, journal):
            assert journal is s.journal and self.done and not self.published
            tick("exit_publication")
            s.append(
                "operator_exited",
                generation=s.run.pins.generation,
                intent_sha256=s.run.action.intent_sha256,
                exit_evidence_sha256="e" * 64,
            )
            self.published = True
            tick("exit_ack")

        def _exited(self, role):
            assert role == "init"
            return s.init_exit

        def close(self):
            self.closed = True
            s.trace.append("operator_cleanup")

    class NeverAuthorized:
        def __init__(self, captured, ledger, journal):
            assert captured is s.driver.native.operator and captured.published
            assert ledger is s.ledger and journal is s.journal and s.init_exit
            assert journal.machine.process_bound(m.base.CANDIDATE, exited=True)
            self.closed = False

        def close(self):
            self.closed = True

    def recover(reader, run, session, wait):
        assert run is s.run and session is s.session and not s.recoveries
        s.recoveries.append((reader, run, session))
        wait(0.25)
        direct_native.expire(s)
        return session.poll()

    monkeypatch.setattr(m.inputs, "publish_launch", publish)
    monkeypatch.setattr(m.execution.AppLaunch, "__init__", initialize)
    monkeypatch.setattr(m.execution.AppLaunch, "start_confirmed", start)
    monkeypatch.setattr(m.execution.AppLaunch, "close", close_run)
    monkeypatch.setattr(m.begin.worker_exit.reconcile, "Operator", Operator)
    monkeypatch.setattr(m.begin.worker_exit.reconcile, "NeverAuthorized", NeverAuthorized)
    monkeypatch.setattr(m.recovery, "recover_never_authorized", recover)
    return s


def run(s, action, after=None):
    services.publish(s, "request")
    visited = []

    def wait(seconds):
        assert seconds == 0.25
        if s.journal.machine.state.phase == "candidate_idle" and not visited:
            visited.append(True)
            s.witness = s.attach_fixture_fd()
            s.candidate = s.driver.prepare_candidate(
                **(candidates.direct.PROFILE | dict(published=s.published, bridge_sha256="c" * 64))
            )
            s.binding = m.launch.binding.Binding(
                s.projected, s.plan.candidate_runtime.source, s.plan.sha256, s.plan.boot
            )
            path = s.plan.root / "recording-ledger"
            path.mkdir(mode=0o700)
            s.ledger = m.launch.binding.Ledger(path, s.binding, now=time.monotonic())
            action()
        elif visited:
            (after or (lambda: direct_native.expire(s)))()

    return s.driver.run(wait)


def start(s):
    return s.driver.start_native(
        s.ledger, s.endpoint, s.audit, specification=object(), profile_sha256="b" * 64
    )


def test_selected_driver_is_passive_and_reserved_once(app_phase):
    s = app_phase
    assert not s.trace and not s.engine.sent and not s.service.used
    with pytest.raises(m.operator.UnconfirmedOperator):
        m.AppService(s.startup, s.service)
    assert s.service._app_driver is s.driver and not s.service.failed


def test_direct_loop_cannot_run_a_reserved_service(app_phase):
    s = app_phase
    candidates.direct.denied(lambda: s.service.run(lambda _: None))
    assert not s.service.used and not s.driver.used and not s.engine.sent


def test_app_loop_preserves_idle_cancel_before_native_handoff(app_phase):
    s = app_phase
    assert (
        run(s, lambda: services.publish(s, "cancel_idle"), after=lambda: None).phase == "complete"
    )
    assert not s.trace and s.driver.native is None and len(s.engine.sent) == 4
    assert s.journal.fd >= 0 and s.service.closed


def test_native_handoff_keeps_original_session_and_closes_owned_handles(app_phase):
    s = app_phase
    original = s.plan.raw, s.plan.deadlines, s.session, s.session.executor

    def action():
        assert start(s)
        phase = s.driver.native
        assert phase.confirmed and phase.operator is s.native_captures[0]
        assert s.service.native is s.service.prepared_launch is None
        assert s.service.coordinator.finished and s.session.read is phase.read
        assert s.ledger.state.count == 1 and not s.endpoint.closed

    assert run(s, action).phase == "review"
    assert original == (s.plan.raw, s.plan.deadlines, s.session, s.session.executor)
    assert s.trace.index("operator_cleanup") < s.trace.index("run_cleanup")
    assert s.run.closed and s.native_captures[0].closed and not s.audit.closed
    assert not s.service.failed and s.service.closed and len(s.engine.sent) == 2
    assert not s.recoveries and s.journal.machine.state.recording_outcome == "not_attempted"


@pytest.mark.parametrize(
    "fault", ["publication", "construct", "launch", "intent", "ready", "capture"]
)
def test_uncertainty_never_reenters_idle_or_retries(app_phase, fault):
    s = app_phase

    def action():
        s.fault = fault
        assert not start(s)
        if s.driver.native.run is not None:
            s.driver.native.run.failed = True
        assert s.driver.native.uncertain and not s.service.failed
        assert s.driver.native.operator is None
        services.publish(s, "cancel_idle")

    assert run(s, action).phase == "review"
    assert s.trace.count(fault) == 1 and not s.recoveries and len(s.engine.sent) == 2
    assert not s.journal.machine.state.finish_requested and not s.service.failed


@pytest.mark.parametrize("lost_close", [False, True])
def test_transport_cancel_is_once_and_not_exit_evidence(app_phase, lost_close):
    s = app_phase

    def action():
        assert start(s)
        s.close_fault = lost_close
        assert s.driver.cancel_native() is (not lost_close)
        with pytest.raises(m.operator.UnconfirmedOperator):
            s.driver.cancel_native()
        assert not s.native_captures[0].done and s.ledger.state.count == 1

    assert run(s, action).phase == "review"
    assert s.trace.count("transport_close") == 1 and not s.recoveries


@pytest.mark.parametrize("init_exits", [False, True])
def test_actual_original_exit_boundary_gates_app_recovery(app_phase, init_exits):
    s = app_phase

    def action():
        assert start(s)
        assert s.driver.cancel_native()
        s.worker_exit, s.init_exit = True, init_exits
        if init_exits:
            s.engine.dead.add(s.witness.identity.container_id)

    assert run(s, action).phase == "review"
    assert len(s.recoveries) == int(init_exits)
    assert s.native_captures[0].published and s.ledger.state.count == 1


@pytest.mark.parametrize("fault", ["exit_poll", "exit_publication", "exit_ack"])
def test_observer_failure_expires_without_disabling_clock(app_phase, fault):
    s = app_phase

    def action():
        assert start(s)
        s.fault, s.worker_exit = fault, True

    assert run(s, action).phase == "review"
    assert s.native_captures[0].polls == 1 and s.driver.native.uncertain
    assert not s.service.failed and not s.recoveries


@pytest.mark.parametrize("notice", ["cancel_idle", "pending", "locked"])
def test_pending_idle_input_refuses_before_publication(app_phase, notice):
    s = app_phase

    def action():
        if notice == "cancel_idle":
            services.publish(s, notice)
        elif notice == "pending":
            (s.inbox.path / ".pending-cancel").write_bytes(b"preserve")
        else:
            s.inbox.lock.acquire()
        try:
            start(s)
        finally:
            if notice == "locked":
                s.inbox.lock.release()

    candidates.direct.denied(lambda: run(s, action))
    assert not s.trace and not s.driver.native_attempted and len(s.engine.sent) == 2


@pytest.mark.parametrize(
    "field", ["session", "processes", "dispatch", "journal", "lock", "_cleanup"]
)
def test_replaced_driver_custody_refuses(app_phase, monkeypatch, field):
    s = app_phase
    with monkeypatch.context() as patch:
        patch.setattr(s.driver, field, object())
        with pytest.raises(m.operator.UnconfirmedOperator):
            s.driver._context()
    assert not s.trace and not s.engine.sent


@pytest.mark.parametrize("field", ["operator", "run", "ledger", "endpoint", "reader"])
def test_replaced_phase_custody_cannot_redirect_cancel(app_phase, monkeypatch, field):
    s = app_phase

    def action():
        assert start(s)
        with monkeypatch.context() as patch:
            patch.setattr(s.driver.native, field, object())
            with pytest.raises(m.operator.UnconfirmedOperator):
                s.driver.cancel_native()

    assert run(s, action).phase == "review"
    assert "transport_close" not in s.trace and s.native_captures[0].closed and s.run.closed


@pytest.mark.parametrize("field", ["original", "lock"])
def test_replaced_driver_before_run_refuses_without_acquiring_replacement(app_phase, field):
    s = app_phase
    setattr(s.driver, field, object())
    candidates.direct.denied(lambda: s.driver.run(lambda _: None))
    assert s.service.failed and not s.service.used and not s.service.lock.locked()
    assert not s.trace and not s.engine.sent


@pytest.mark.parametrize("field", ["original", "lock"])
def test_loop_failure_closes_original_owner_and_releases_original_lock(app_phase, field):
    s = app_phase
    original, lock = s.service, s.service.lock

    def action():
        assert start(s)
        setattr(s.driver, field, object())

    candidates.direct.denied(lambda: run(s, action))
    assert original.closed and original.failed and not lock.locked()
    assert s.native_captures[0].closed and s.run.closed and s.witness.closed


@pytest.mark.parametrize(
    "fault",
    [
        "same_endpoint",
        "closed_endpoint",
        "bad_ledger",
        "poisoned",
        "changed_bytes",
        "busy",
        "old_ledger",
        "future_ledger",
        "wrong_location",
        "wrong_binding",
    ],
)
def test_invalid_native_input_cannot_publish_or_retire_idle(app_phase, fault):
    s = app_phase

    def action():
        if fault == "same_endpoint":
            s.audit = s.endpoint
        elif fault == "closed_endpoint":
            s.audit.close()
        elif fault == "bad_ledger":
            s.ledger = object()
        elif fault == "poisoned":
            s.ledger._poisoned = True
        elif fault == "changed_bytes":
            (s.ledger.directory / "0000.json").write_bytes(b"preserve")
        elif fault == "old_ledger":
            s.ledger.state = replace(
                s.ledger.state, now=s.plan.original_clock.before_ns / m.operator.plans.clock.NS - 1
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

    candidates.direct.denied(lambda: run(s, action))
    assert not s.trace and not s.driver.native_attempted and s.driver.native is None
    assert not s.service.coordinator.finished and len(s.engine.sent) == 2


def test_cleanup_registered_before_post_capture_validation_can_fail(app_phase, monkeypatch):
    s = app_phase
    original_check = m.AppNativePhase._ledger

    def check(phase):
        if phase.operator is not None:
            raise OSError("PRIVATE post-capture refusal")
        return original_check(phase)

    monkeypatch.setattr(m.AppNativePhase, "_ledger", check)

    def action():
        assert not start(s)
        assert s.driver.native.operator is s.native_captures[0]
        assert s.driver.native.uncertain

    assert run(s, action).phase == "review"
    assert s.native_captures[0].closed and not s.audit.closed and not s.recoveries


def test_loop_has_finite_ceiling_without_claiming_success(app_phase, monkeypatch):
    s = app_phase
    monkeypatch.setattr(m.operator, "IDLE_POLL_LIMIT", 2)
    result = s.driver.run(lambda _: None)
    assert result.phase == "prepared" and result.outcome == "poll_limit_unconfirmed"
    assert s.service.closed and not s.engine.sent


def test_failed_launch_cannot_accept_new_cancel_but_clock_still_expires(app_phase):
    s = app_phase

    def action():
        assert start(s)
        s.run.failed = True
        with pytest.raises(m.operator.UnconfirmedOperator):
            s.driver.cancel_native()

    assert run(s, action).phase == "review"
    assert "transport_close" not in s.trace and not s.service.failed


def test_handoff_cannot_be_repeated_or_reopen_idle(app_phase):
    s = app_phase

    def action():
        assert start(s)
        with pytest.raises(m.operator.UnconfirmedOperator):
            start(s)
        with pytest.raises(m.operator.UnconfirmedOperator):
            s.driver.prepare_candidate()

    assert run(s, action).phase == "review"
    assert s.trace.count("publication") == s.trace.count("launch") == 1


@pytest.mark.parametrize("field", ["client", "ready", "probe", "qualify", "ready_qualification"])
def test_run_member_replacement_cannot_redirect_cancel(app_phase, monkeypatch, field):
    s = app_phase

    def action():
        assert start(s)
        with monkeypatch.context() as patch:
            patch.setattr(s.run, field, object())
            with pytest.raises(m.operator.UnconfirmedOperator):
                s.driver.cancel_native()

    assert run(s, action).phase == "review"
    assert "transport_close" not in s.trace and s.run.closed and s.native_captures[0].closed
