"""Original AppService loop with actual launch, begin, finalized and recovery joins.

The original CasePlan, clock witness, IdleService, coordinator, session, driver,
candidate, publication/qualification, launch, start, ledger and App recovery are
real. Source files, socket inodes and an owned child pidfd are real too. Startup
acceptance, initial App-transfer events, idle evidence, Engine/HA and native
return/exit/file facts remain explicitly synthetic. This is a composition test,
NOT installed-source, independent supervision, native WAV or hardware evidence.
"""

import hashlib
import os
import time
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_phase_composition as phases
from . import test_supplemental_recording_candidate_qualification as candidates
from . import test_supplemental_recording_finalized_recovery as platform

m, r = phases.m, phases.r
launch, base = m.launch, m.base
begins, executions = phases.begins, phases.begins.executions
app, native, launch_case = phases.app, phases.native, phases.launch_case
layout, image_umask, supervised, image, configured = (
    phases.layout,
    phases.image_umask,
    phases.supervised,
    phases.image,
    phases.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture
def candidate(supervised, image, configured, monkeypatch, request, tmp_path):
    root = tmp_path / "host-execution"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(launch.plans.Plan, "root", property(lambda self: root))
    with ExitStack() as cleanup:

        def decode(value, projected):
            raw = base.encode(value)
            path = root / "plan.json"
            path.write_bytes(raw)
            path.chmod(0o600)
            original = m.operator.intake.CasePlan(root, hashlib.sha256(raw).hexdigest())
            cleanup.callback(original.close)
            decode.original = original
            return original.plan  # The FIRST plan object, before any owner binds it.

        for s in candidates.setup_candidate(
            supervised, image, configured, monkeypatch, request, decode=decode
        ):
            s.case_plan = decode.original
            yield s


@pytest.fixture
def driver_case(launch_case, tmp_path, monkeypatch, request):
    s = launch_case
    s.startup.original = s.case_plan
    s.dispatch_notices = []
    s.candidate_notices = []
    s.native_notices = []

    def observe(notice):
        # Test-only receipt, not independent CLI/App custody. The real peer
        # transport and service assembly have separate process compositions.
        assert s.driver.dispatch is s.service.dispatch is s.session.dispatch
        assert s.service.clock_witness is s.startup.clock
        assert notice.history == tuple(base.encode(item) for item in s.journal.entries)
        s.dispatch_notices.append(notice)
        return notice.receipt

    observer = observe if getattr(request, "param", None) == "observed" else None

    def observe_candidate(notice):
        # Synthetic receipt at the real pre-publication phase, not an actual
        # separate observer or installed-native qualification in this fixture.
        assert not s.driver.native_attempted and s.driver.native is None
        assert s.service.inbox.lock.locked() and s.candidate_owner is s.service.candidate
        assert notice.process == s.candidate_owner.witness.identity
        assert notice.history == tuple(base.encode(item) for item in s.journal.entries)
        s.candidate_notices.append(notice)
        return notice.receipt

    def observe_native(notice):
        # Synthetic acknowledgment in the actual original service->begin path.
        # Separate-process custody and actual native Ready have other fixtures.
        assert s.driver.native.retired and s.driver.recording is not None
        assert s.driver.recording.start_attempt.native_observation_attempted
        assert s.ledger.state.count == 1
        assert s.journal.machine.state.authorization_generation is None
        assert notice.history == tuple(base.encode(item) for item in s.journal.entries)
        s.native_notices.append(notice)
        if getattr(s, "native_observer_fault", None) is not None:
            return s.native_observer_fault(notice)
        return notice.receipt

    with ExitStack() as cleanup:

        @contextmanager
        def accepted_service(s):
            monkeypatch.setattr(
                launch.TransferHost,
                "read",
                (lambda reader: s.transfer_read(reader))
                if getattr(s, "initial_dispatch", False)
                else (lambda reader: s.host()),
            )
            with s.startup.idle_service(s.docker, dispatch_observer=observer) as original:
                s.service = original
                yield original

        def prepared(s):
            assert len(s.journal.entries) == 1 and s.journal.machine.state.phase == "prepared"
            if not getattr(s, "accepted_startup", False):
                (s.plan.root / "inbox").mkdir(mode=0o700)
                monkeypatch.setattr(launch.TransferHost, "read", lambda self: s.host())
                s.service = m.operator.IdleService(
                    s.case_plan,
                    s.projected,
                    s.journal,
                    s.docker,
                    clock_witness=s.startup.clock,
                    dispatch_observer=observer,
                )
                cleanup.callback(s.service.close)
            s.driver = m.AppService(
                s.startup,
                s.service,
                candidate_observer=observe_candidate if observer is not None else None,
                native_observer=observe_native if observer is not None else None,
            )
            s.session = s.service.session
            s.original_owners = (
                s.session,
                s.session.processes,
                s.session.dispatch,
                s.session.executor,
            )
            s.original_callbacks = s.session.processes.read_clock, s.session.dispatch.now
            s.original_deadline = s.journal.machine.hard_deadline
            s.original_plan_bytes = s.plan.raw

        execution = contextmanager(executions.setup_execution)
        s = cleanup.enter_context(
            execution(
                s,
                tmp_path,
                monkeypatch,
                prepared=prepared,
                publish=False,
                service_factory=accepted_service if getattr(s, "accepted_startup", False) else None,
            )
        )
        assert not s.service.used
        # Initial App-transfer evidence is supplied by the existing synthetic
        # platform fixture, never attributed to an actual installed dispatch.
        if getattr(s, "initial_dispatch", False):
            assert s.journal.machine.state.phase == "prepared"
            assert not s.service.processes.witnesses
        else:
            assert s.journal.machine.state.phase == "candidate_idle"
            s.service.processes.witnesses[base.CANDIDATE] = s.witness
            monkeypatch.setattr(s.service.processes, "reconcile", lambda: None)
            monkeypatch.setattr(
                s.service.processes,
                "bind_running",
                lambda slug, generation: s.service.processes.record(slug),
            )

        idle_read = launch.idle_module.Idle.read
        original_idle = s.idle

        def idle_init(idle, plan, witness, generation, *, zero_domain):
            assert plan is s.plan and witness is s.witness
            idle.plan, idle.init, idle.generation = plan, witness.identity, generation
            idle.zero_domain, idle.opened = zero_domain, []
            idle.pidfd = os.dup(witness.fd)
            idle.failed = idle.closed = False
            s.idle = idle

        def capture(observer, plan, ready, endpoint):
            assert plan is s.plan and ready is s.driver.native.run.ready
            assert endpoint is not ready.client.endpoint
            observer.owner = s.driver.owner
            observer.plan, observer.clock, observer.pins = (
                plan,
                plan.original_clock,
                ready.client.claim.pins,
            )
            observer.endpoint, observer.handles = endpoint, {}
            observer.ready_sha256 = hashlib.sha256(ready.ready_raw).hexdigest()
            observer.failed = observer.closed = observer.done = observer.publish_attempted = False
            observer.result_sha256 = None

        monkeypatch.setattr(launch.idle_module.Idle, "__init__", idle_init)
        monkeypatch.setattr(launch.idle_module.Idle, "read", lambda self: idle_read(original_idle))
        monkeypatch.setattr(m.begin.worker_exit.reconcile.Operator, "__init__", capture)
        s.audit = type(s.endpoint)()
        s.cleanup = cleanup
        s.transitions = []
        s.recording_fault = None
        s.clock_read = launch.plans.clock.read
        yield s


def handoff(s, monkeypatch, *, record=True):
    s.transitions.append("native")
    profile = dict(
        published=s.published,
        bridge_sha256=hashlib.sha256(s.bridge_raw).hexdigest(),
        image_environment_sha256=launch.runtime.environment(s.image_env),
        timezone=candidates.env.TIMEZONE,
        hostname=candidates.env.HOSTNAME,
        architecture="amd64",
    )
    s.candidate_owner = s.driver.prepare_candidate(**profile)
    s.host = s.bootstrap_host = s.candidate_owner.reader
    binding = launch.binding.Binding(
        s.projected, s.plan.candidate_runtime.source, s.plan.sha256, s.plan.boot
    )
    path = s.plan.root / "recording-ledger"
    path.mkdir(mode=0o700)
    s.ledger = launch.binding.Ledger(path, binding, now=time.monotonic())
    assert s.driver.start_native(
        s.ledger, s.endpoint, s.audit, specification=s.spec, profile_sha256="a" * 64
    )
    s.run = s.driver.native.run
    if not record:
        return
    begins.setup_relay(s, monkeypatch)
    phases.setup_returns(s, monkeypatch)
    s.transitions.append("begin")
    s.fault = s.recording_fault
    s.start_result = s.driver.start_recording()
    if s.start_result:
        phases.setup_completion(s, monkeypatch)


def prepare_recovery(s, monkeypatch):
    phase = s.driver.recording
    s.transitions.append("finish")
    assert s.driver.finish_recording()
    c = SimpleNamespace(s=s, host=phase.reader, operator=phase.operator)
    s.cycle = c
    s.cleanup.enter_context(
        contextmanager(platform.setup_cycling)(
            c, monkeypatch, read_clock=s.service._now, session=s.session
        )
    )
    c.reader = phase.reader
    monkeypatch.setattr(c.operator, "_exited", lambda role: role == "init")

    def read():
        files = c.reader.files
        files._context()
        assert files.used and s.run.ready.closed and c.operator.done
        return files.completion.collected  # Explicit synthetic finalized-file I/O.

    monkeypatch.setattr(c.reader.files, "read", read, raising=False)
    return c


def run(s, monkeypatch, action, *, later=None, record=True):
    def wait(seconds):
        assert seconds == 0.25
        if not s.transitions:
            handoff(s, monkeypatch, record=record)
            action()
        elif later is not None:
            later(seconds)
        else:
            s.cycle.wait(seconds)

    return s.driver.run(wait)


def expire(s, monkeypatch, *, hard=False):
    """Advance the synthetic underlying clock, never replace owner callbacks."""
    state = s.journal.machine.state
    target = (
        s.original_deadline
        if hard
        else (
            state.trial_deadline + base.COMMAND_SECONDS
            if state.phase == "candidate_running"
            else state.deadline
        )
    ) + 1
    if not hard:
        assert target < s.plan.deadlines.recover_by
    delta = int(target * launch.plans.clock.NS) - s.clock_read().boottime_ns

    def late():
        observed = s.clock_read()
        return replace(
            observed,
            before_ns=observed.before_ns + delta,
            after_ns=observed.after_ns + delta,
            boottime_ns=observed.boottime_ns + delta,
        )

    monkeypatch.setattr(launch.plans.clock, "read", late)


def assert_owners(s):
    assert s.original_owners == (
        s.session,
        s.session.processes,
        s.session.dispatch,
        s.session.executor,
    )
    assert s.session is s.driver.session is s.service.session
    assert s.original_callbacks[0] is s.session.processes.read_clock
    assert s.original_callbacks[1] is s.session.dispatch.now
    assert s.plan.raw == s.original_plan_bytes == s.case_plan.recheck().raw
    assert s.journal.machine.hard_deadline == s.original_deadline


def test_original_driver_runs_actual_app_handoffs_and_original_session_recovery(
    driver_case, monkeypatch
):
    s = driver_case
    result = run(s, monkeypatch, lambda: prepare_recovery(s, monkeypatch))
    assert result.phase == "complete"
    assert s.transitions == ["native", "begin", "finish"]
    assert s.creates == 1 and len(s.cycle.created) == len(set(s.cycle.started)) == 2
    assert s.cycle.native_calls == ["e" * 64]
    assert s.journal.machine.state.recording_outcome == "verified"
    assert type(s.run) is m.execution.AppLaunch
    assert type(s.driver.recording.start_attempt) is r.app_begin.AppStart
    assert type(s.driver.recording.reader) is r.finalized.AppAuthorizedFinalized
    assert s.service.closed and s.session.processes.closed
    assert not s.driver.lock.locked() and not s.startup.clock.closed
    assert s.service.native is s.service.recording is s.service.prepared_launch is None
    assert s.run.closed and s.driver.native.operator.closed
    assert s.driver.recording.reader.closed and s.candidate_owner.closed
    assert s.journal.fd >= 0
    assert_owners(s)


@pytest.mark.parametrize("fault", ["relay", "start_return", "lost_started_ack"])
def test_original_driver_lost_begin_stays_uncertain_and_expires_without_recovery(
    driver_case, monkeypatch, fault
):
    s = driver_case
    s.recording_fault = fault

    def action():
        assert not s.start_result and s.driver.recording.uncertain
        assert s.driver.native.retired and not s.driver.native.uncertain
        s.driver._context()  # No obsolete live Ready/pristine ledger requirement.
        expire(s, monkeypatch)

    assert run(s, monkeypatch, action).phase == "review"
    assert s.creates == 1 and not hasattr(s, "cycle")
    assert s.driver.recording.reader is None and not s.driver.recording.recovery_attempted
    assert not s.driver.recording.abandon_attempted
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.ledger.state.count == (3 if fault == "lost_started_ack" else 2)
    assert not s.service.failed and s.service.closed and not s.driver.lock.locked()
    assert s.run.closed and s.driver.native.operator.closed
    assert_owners(s)


@pytest.mark.parametrize("driver_case", ["observed"], indirect=True)
@pytest.mark.parametrize("fault", ["lost", "wrong", "callback"])
def test_original_driver_native_ack_failure_expires_without_authorization(
    driver_case, monkeypatch, fault
):
    s = driver_case

    def failed(notice):
        if fault == "lost":
            raise OSError("PRIVATE lost native evidence acknowledgment")
        if fault == "wrong":
            return "0" * 64
        # A coordinated replacement would satisfy the static object tuple.
        # The original recording wrapper must still refuse it before begin.
        s.driver.native_observer = lambda notice: notice.receipt
        s.driver.objects = (*s.driver.objects[:-1], s.driver.native_observer)
        return notice.receipt

    s.native_observer_fault = failed

    def action():
        assert not s.start_result and s.driver.recording.uncertain
        assert s.driver.native.retired and not s.relays
        assert len(s.native_notices) == 1 and s.ledger.state.count == 1
        assert s.journal.machine.state.authorization_generation is None
        assert s.driver.recording.start_attempt.failed and s.run.client.closed
        expire(s, monkeypatch)

    assert run(s, monkeypatch, action).phase == "review"
    assert s.creates == 1 and not hasattr(s, "cycle")
    assert not s.driver.recording.recovery_attempted
    assert not s.driver.recording.abandon_attempted
    assert s.ledger.state.count == 1 and len(s.native_notices) == 1
    assert not any(e["event"]["kind"] == "authorize_recording" for e in s.journal.entries)
    assert s.service.closed and s.run.closed and not s.service.failed
    assert_owners(s)


@pytest.mark.parametrize("fault", ["completion", "collect", "ready_close", "publish_after"])
def test_original_driver_lost_finish_is_not_retried_or_promoted_to_recovery(
    driver_case, monkeypatch, fault
):
    s = driver_case

    def action():
        s.finish_fault = fault
        assert not s.driver.finish_recording()
        assert s.driver.recording.uncertain and s.driver.recording.finish_attempted
        s.finish_fault = None  # Fault injection ends; uncertainty must persist.
        expire(s, monkeypatch)

    assert run(s, monkeypatch, action).phase == "review"
    assert s.creates == 1 and not hasattr(s, "cycle")
    assert not s.driver.recording.recovery_attempted and not s.driver.recording.abandon_attempted
    assert s.finish_trace.count("completion") == 1
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.service.closed and s.run.closed and not s.service.failed
    assert_owners(s)


def test_original_driver_lost_recovery_stop_reply_is_inspected_not_reissued(
    driver_case, monkeypatch
):
    s = driver_case

    def action():
        prepare_recovery(s, monkeypatch).stop_return_lost = True

    assert run(s, monkeypatch, action).phase == "complete"
    assert s.cycle.created == [
        platform.h.CONTROL["stopping_candidate"],
        platform.h.CONTROL["starting_normal"],
    ]
    assert len(s.cycle.started) == len(set(s.cycle.started)) == 2
    assert_owners(s)


def test_original_driver_lost_recovery_inspection_expires_original_session(
    driver_case, monkeypatch
):
    s = driver_case

    def action():
        prepare_recovery(s, monkeypatch).inspect_lost = True

    assert run(s, monkeypatch, action, later=lambda _: expire(s, monkeypatch)).phase == "review"
    assert s.cycle.created == [platform.h.CONTROL["stopping_candidate"]]
    assert not s.cycle.native_calls
    assert s.service.closed and s.driver.recording.recovery_attempted
    assert s.journal.machine.state.reason != "restored"
    assert_owners(s)


@pytest.mark.parametrize("health", [(False, False), (None, None), (True, True)])
def test_original_driver_requires_fresh_healthy_nonrecording_normal_app(
    driver_case, monkeypatch, health
):
    s = driver_case

    def action():
        prepare_recovery(s, monkeypatch).native_values = health

    def later(seconds):
        s.cycle.wait(seconds)
        if s.cycle.native_calls:
            expire(s, monkeypatch)

    assert run(s, monkeypatch, action, later=later).phase == "review"
    assert len(s.cycle.created) == len(s.cycle.started) == 2
    assert s.cycle.native_calls == ["e" * 64]
    assert s.journal.machine.state.reason != "restored"
    assert_owners(s)


def test_original_driver_waits_for_independent_init_exit_without_refreshing_ready(
    driver_case, monkeypatch
):
    s = driver_case

    def obsolete(*args, **kwargs):
        pytest.fail("Finalized driver must not refresh expired Ready")

    def action():
        c = prepare_recovery(s, monkeypatch)
        monkeypatch.setattr(c.operator, "_exited", lambda role: False)
        monkeypatch.setattr(s.run, "_app_context", obsolete)
        monkeypatch.setattr(type(s.run.qualify), "__call__", obsolete)
        monkeypatch.setattr(launch.received.Ready, "check_before_begin", obsolete)

    def later(seconds):
        assert not s.cycle.created and not s.driver.recording.recovery_attempted
        expire(s, monkeypatch)

    assert run(s, monkeypatch, action, later=later).phase == "review"
    assert not s.cycle.created and not s.cycle.native_calls
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert_owners(s)


@pytest.mark.parametrize("field", ["session", "processes", "dispatch", "executor"])
def test_original_driver_recovery_wait_cannot_replace_owner_and_cleanup_keeps_original(
    driver_case, monkeypatch, field
):
    s = driver_case
    original_session = s.session

    def later(seconds):
        if field == "session":
            s.driver.session = object()
        else:
            setattr(original_session, field, object())

    with pytest.raises(m.operator.UnconfirmedOperator):
        run(s, monkeypatch, lambda: prepare_recovery(s, monkeypatch), later=later)
    assert s.service.failed and s.service.closed and not s.driver.lock.locked()
    assert s.original_owners[1].closed and s.run.closed and s.driver.native.operator.closed
    assert len(s.cycle.created) == 1 and not s.cycle.native_calls
    assert s.journal.machine.state.recording_outcome != "verified"
    assert s.journal.fd >= 0 and not s.startup.clock.closed


def test_original_driver_refuses_past_hard_deadline_instead_of_renewing_authority(
    driver_case, monkeypatch
):
    s = driver_case
    with pytest.raises(m.operator.UnconfirmedOperator):
        run(s, monkeypatch, lambda: expire(s, monkeypatch, hard=True))
    assert s.service.failed and s.service.closed and s.run.closed
    assert not s.driver.lock.locked() and s.original_owners[1].closed
    assert not s.driver.recording.recovery_attempted and not hasattr(s, "cycle")
    assert s.journal.machine.state.recording_outcome == "unconfirmed"
    assert s.plan.raw == s.original_plan_bytes
    assert s.journal.machine.hard_deadline == s.original_deadline
