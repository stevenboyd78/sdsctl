"""Finite idle-service assembly, real files with explicit synthetic host/process I/O."""

import json
import os
from dataclasses import replace
from threading import Thread

import pytest

from . import test_supplemental_recording_idle_coordinator as coordinator_tests
from . import test_supplemental_recording_service_offer as offer_tests
from ._supplemental_fixture_budget import integer_budget

m, cancellation, b = coordinator_tests.m, coordinator_tests.cancellation, coordinator_tests.b
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
) = (
    coordinator_tests.layout,
    coordinator_tests.tree,
    coordinator_tests.routing,
    coordinator_tests.projection,
    coordinator_tests.binding,
    coordinator_tests.directory,
    coordinator_tests.prepared,
    coordinator_tests.joined,
    coordinator_tests.before_handoff,
    coordinator_tests.transfer,
)


@pytest.fixture
def service(transfer, monkeypatch, request):
    s = transfer
    path = s.plan.root / "plan.json"
    path.write_bytes(s.plan.raw)
    path.chmod(0o600)
    (s.plan.root / "inbox").mkdir(mode=0o700)
    with m.intake.CasePlan(s.plan.root, s.plan.sha256) as original:
        s.plan = original.plan
        witness = (
            m.plans.clock.ClockWitness(s.plan.original_clock)
            if getattr(request, "param", False)
            else None
        )
        s.borrowed_clock = witness
        s.startup_offer = None
        if getattr(request, "param", False) == "offered":
            # The fixture's plan/journal are already persisted synthetic inputs.
            # Prove the startup model matches those EXACT original bytes, then
            # carries its retained clock into service; no publication is implied.
            declaration = json.loads(s.plan.raw)
            times = declaration.pop("deadlines")
            declaration.pop("original_clock")
            template = offer_tests.m.template_codec.decode(
                {
                    "schema": 1,
                    "kind": offer_tests.m.template_codec.KIND,
                    "plan": declaration,
                    "budget": integer_budget(times),
                }
            )
            s.startup_offer = offer_tests.m.Offer(template, template.sha256, witness)
            assert s.startup_offer.inspect().raw == s.plan.raw
            assert s.startup_offer.accept(s.plan.sha256).raw == s.plan.raw

        def assemble():
            s.service = m.IdleService(
                original, s.projected, s.journal, s.docker, clock_witness=witness
            )
            s.inbox, s.before = s.service.inbox, s.service.transfer
            return s.service.session

        try:
            with cancellation.owned_session(s, monkeypatch, assemble):
                try:
                    yield s
                finally:
                    s.service.close()
        finally:
            if s.startup_offer is not None:
                s.startup_offer.close()
            if witness is not None:
                witness.close()


def publish(s, action):
    return coordinator_tests.publish(s, action)


def refused(owner, wait=lambda _: None):
    with pytest.raises(m.UnconfirmedOperator) as error:
        owner.run(wait)
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__
    assert owner.failed and not owner.lock.locked()


def expire(s, monkeypatch):
    clock = m.plans.clock.read
    target = s.journal.machine.state.deadline + 1

    def late():
        value = clock()
        delta = int(target * m.plans.clock.NS) - value.boottime_ns
        return replace(
            value,
            before_ns=value.before_ns + delta,
            after_ns=value.after_ns + delta,
            boottime_ns=value.boottime_ns + delta,
        )

    monkeypatch.setattr(m.plans.clock, "read", late)


def test_constructor_is_passive_and_borrows_original_plan_and_journal(service):
    s = service
    assert not s.engine.sent and not s.host.reads and not s.cached_calls
    assert len(s.journal.entries) == 1 and not s.session.processes.witnesses
    assert s.service.inbox is s.service.coordinator.inbox
    assert s.service.session is s.service.coordinator.session is s.session
    assert not s.service.used and not s.service.closed and not s.service.failed
    s.service.close()
    s.service.close()
    assert s.inbox.closed and s.session.processes.closed
    assert s.service.original.recheck() is s.plan and s.journal.fd >= 0
    assert s.journal.replayed(m.time.monotonic() + 2).state.phase == "prepared"
    assert list(s.inbox.path.iterdir()) == []
    refused(s.service)
    assert not s.engine.sent


def test_service_owns_original_clock_namespace_until_its_cleanup(service):
    s = service
    witness, fd = s.service.clock_witness, s.service.clock_witness.fd
    assert witness.original is s.plan.original_clock and not witness.closed
    assert s.service._clock()[0] == s.plan.boot
    s.service.close()
    assert witness.closed
    with pytest.raises(OSError):
        os.fstat(fd)
    assert s.journal.fd >= 0 and s.service.original.recheck() is s.plan


def test_service_releases_clock_after_all_later_owned_cleanup(service):
    s = service
    witness, observed = s.service.clock_witness, []

    def later_cleanup():
        assert not witness.closed
        os.fstat(witness.fd)
        observed.append(True)

    s.service._cleanup.append(later_cleanup)
    s.service.close()
    assert observed == [True] and witness.closed
    assert s.journal.fd >= 0 and not s.engine.sent


def test_replacement_clock_is_not_adopted_or_closed_by_service(service):
    s = service
    original = s.service.clock_witness
    replacement = m.plans.clock.ClockWitness(s.plan.original_clock)
    try:
        s.service.clock_witness = replacement
        refused(s.service)
        assert original.closed and not replacement.closed
        os.fstat(replacement.fd)
        assert not s.engine.sent
    finally:
        replacement.close()


def test_single_owner_polls_explicit_request_cancel_and_normal_restoration(service):
    s, waits = service, []
    original_session = s.session
    original_bytes, deadlines = s.plan.raw, s.plan.deadlines
    publish(s, "request")

    def wait(seconds):
        waits.append(seconds)
        assert seconds == 0.25 and s.service.session is original_session
        if s.journal.machine.state.phase == "candidate_idle":
            publish(s, "cancel_idle")

    result = s.service.run(wait)
    assert result.phase == "complete" and waits == [0.25] * 5
    assert s.service.used and s.service.closed and not s.service.failed
    assert s.inbox.closed and s.session.processes.closed
    assert s.journal.fd >= 0 and s.service.original.recheck() is s.plan
    assert s.plan.raw == original_bytes and s.plan.deadlines is deadlines
    assert s.engine.sent == [
        ("stop", b.NORMAL),
        ("start", b.CANDIDATE),
        ("stop", b.CANDIDATE),
        ("start", b.NORMAL),
    ]
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.journal.machine.state.authorization_generation is None
    notices = {p.name: p.read_bytes() for p in s.inbox.path.iterdir()}
    assert set(notices) == {"request.json", "cancel_idle.json"}
    refused(s.service)
    assert len(s.engine.sent) == 4
    assert {p.name: p.read_bytes() for p in s.inbox.path.iterdir()} == notices


@pytest.mark.parametrize("at_idle", [False, True])
@pytest.mark.parametrize("bad_input", [False, True])
def test_missing_or_refused_input_expires_without_inventing_cancel(
    service, monkeypatch, at_idle, bad_input
):
    s, waits = service, []
    if at_idle:
        publish(s, "request")
    elif bad_input:
        (s.inbox.path / ".pending-request").write_bytes(b"Preserve")

    def wait(seconds):
        waits.append(seconds)
        if not at_idle or s.journal.machine.state.phase == "candidate_idle":
            if at_idle and bad_input and not s.service.coordinator.input_failed:
                (s.inbox.path / ".pending-cancel").write_bytes(b"Preserve")
            else:
                expire(s, monkeypatch)

    result = s.service.run(wait)
    assert result.phase == "review" and s.service.closed
    assert not s.journal.machine.state.finish_requested
    assert s.service.coordinator.input_failed == bad_input
    assert len(s.engine.sent) == (2 if at_idle else 0)
    assert s.session.processes.closed and s.inbox.closed and waits
    assert s.journal.fd >= 0 and s.service.original.recheck() is s.plan


def test_defensive_poll_ceiling_cannot_create_a_terminal_success(service, monkeypatch):
    s, waits = service, []
    monkeypatch.setattr(m, "IDLE_POLL_LIMIT", 3)
    result = s.service.run(waits.append)
    assert result.phase == "prepared" and result.outcome == "poll_limit_unconfirmed"
    assert waits == [0.25] * 3 and s.service.closed
    assert not s.engine.sent and len(s.journal.entries) == 2  # Actual synthetic normal binding.
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.inbox.closed and s.session.processes.closed
    refused(s.service)


@pytest.mark.parametrize("error", [OSError("PRIVATE"), KeyboardInterrupt(), SystemExit(75)])
def test_wait_interruption_closes_original_custody_without_retry(service, error):
    s = service
    publish(s, "request")

    def interrupt(seconds):
        assert seconds == 0.25
        raise error

    if isinstance(error, Exception):
        refused(s.service, interrupt)
    else:
        with pytest.raises(type(error)):
            s.service.run(interrupt)
    assert s.service.used and s.service.failed and s.service.closed
    assert s.session.processes.closed and s.inbox.closed
    assert s.engine.sent == [("stop", b.NORMAL)]
    assert s.journal.fd >= 0 and s.service.original.recheck() is s.plan
    assert s.journal.machine.state.phase == "stopping_normal"
    refused(s.service)


@pytest.mark.parametrize(
    "member",
    [
        "original",
        "plan",
        "projected",
        "journal",
        "docker",
        "transfer",
        "processes",
        "dispatch",
        "session",
        "inbox",
        "coordinator",
    ],
)
def test_replaced_component_refuses_and_closes_only_original_owned_handles(service, member):
    s = service
    process, inbox = s.service.processes, s.inbox
    setattr(s.service, member, object())
    refused(s.service)
    assert process.closed and inbox.closed and s.service.closed
    assert not s.engine.sent and s.journal.fd >= 0


def test_foreign_thread_cannot_run_or_close_owner_resources(service):
    s, errors = service, []

    def foreign():
        for operation in (lambda: s.service.run(lambda _: None), s.service.close):
            try:
                operation()
            except m.UnconfirmedOperator as error:
                errors.append(str(error))

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and errors == [m.MESSAGE, m.MESSAGE]
    assert not s.service.closed and not s.inbox.closed and not s.session.processes.closed
    assert not s.engine.sent and not s.service.used
    refused(s.service)  # Original owner fails closed and releases its own resources.
    assert s.service.closed and s.inbox.closed and s.session.processes.closed


def test_reentrant_run_poisons_outer_loop_without_second_dispatch(service):
    s = service
    publish(s, "request")

    def reenter(seconds):
        assert seconds == 0.25
        with pytest.raises(m.UnconfirmedOperator):
            s.service.run(lambda _: None)

    refused(s.service, reenter)
    assert s.service.closed and s.engine.sent == [("stop", b.NORMAL)]
    assert s.session.processes.closed and s.inbox.closed


def test_failed_partial_assembly_releases_new_inbox_but_not_borrowed_inputs(service):
    s = service
    publish(s, "request")
    assert s.service.coordinator.poll(lambda _: None).phase == "stopping_normal"
    descriptors = set(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedOperator):
        m.IdleService(s.service.original, s.projected, s.journal, s.docker)
    assert set(os.listdir("/proc/self/fd")) == descriptors
    assert not s.inbox.closed and not s.session.processes.closed
    assert s.journal.fd >= 0 and s.service.original.recheck() is s.plan
    assert len(s.engine.sent) == 1


def test_invalid_wait_consumes_owner_and_closes_without_dispatch(service):
    s = service
    publish(s, "request")
    refused(s.service, None)
    assert s.service.used and s.service.closed and not s.engine.sent


def test_input_can_arrive_after_start_without_an_implicit_request(service):
    s, phases = service, []

    def wait(seconds):
        assert seconds == 0.25
        phase = s.journal.machine.state.phase
        phases.append(phase)
        if phase == "prepared" and len(phases) == 3:
            assert not s.engine.sent
            publish(s, "request")
        elif phase == "candidate_idle":
            publish(s, "cancel_idle")

    assert s.service.run(wait).phase == "complete"
    assert phases[:3] == ["prepared"] * 3 and len(s.engine.sent) == 4


@pytest.mark.parametrize("action", ["request", "cancel_idle"])
def test_lost_durable_notice_ack_never_causes_resubmission(service, monkeypatch, action):
    s, losses = service, []
    append = s.journal.append
    kind = "request" if action == "request" else "finish"

    def lose(event):
        result = append(event)
        if event["kind"] == kind:
            losses.append(kind)
            raise OSError("PRIVATE postcommit acknowledgement lost")
        return result

    monkeypatch.setattr(s.journal, "append", lose)
    publish(s, "request")

    def wait(seconds):
        assert seconds == 0.25
        if s.journal.machine.state.phase == "candidate_idle":
            if action == "request":
                # A failed Inbox is not reconstructed just to accept a cancel.
                expire(s, monkeypatch)
            else:
                publish(s, "cancel_idle")

    result = s.service.run(wait)
    assert result.phase == ("review" if action == "request" else "complete")
    assert losses == [kind] and s.service.coordinator.input_failed
    assert sum(entry["event"]["kind"] == kind for entry in s.journal.entries) == 1
    assert len(s.engine.sent) == (2 if action == "request" else 4)
    assert s.service.closed and s.inbox.closed and s.session.processes.closed


def test_failed_host_read_is_not_retried_but_original_clock_still_expires(service, monkeypatch):
    s, calls = service, []
    publish(s, "request")

    def unavailable(*args):
        calls.append(True)
        raise OSError("PRIVATE cached read lost")

    monkeypatch.setattr(m.launch.normal_read.cached, "_read_probe", unavailable)
    waits = []

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            expire(s, monkeypatch)

    assert s.service.run(wait).phase == "review"
    assert calls == [True] and waits == [0.25] * 3
    assert not s.engine.sent and s.service.closed


def test_closure_error_releases_other_resources_and_withholds_success(service):
    s, closed = service, []

    def uncertain_close():
        closed.append(True)
        raise OSError("PRIVATE descriptor close error")

    # Inject a failure in the closure chain without replacing/dropping any of
    # the original callbacks. ExitStack must still close all actual resources.
    s.service._cleanup.append(uncertain_close)
    with pytest.raises(m.UnconfirmedOperator) as error:
        s.service.close()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__
    assert s.service.failed and s.service.closed and closed == [True]
    assert s.session.processes.closed and s.inbox.closed and s.journal.fd >= 0
    assert s.service.clock_witness.closed
    s.service.close()
    assert closed == [True] and s.service.original.recheck() is s.plan


@pytest.mark.parametrize("service", [True], indirect=True)
def test_borrowed_startup_clock_survives_every_service_cleanup_callback(service):
    s = service
    witness = s.borrowed_clock
    assert s.service.clock_witness is witness
    fd, observed = witness.fd, []

    def after():
        assert not witness.closed and witness.fd == fd
        os.fstat(fd)
        observed.append(True)

    s.service._cleanup.append(after)
    s.service.close()
    assert observed == [True] and s.service.closed
    assert not witness.closed and witness.fd == fd
    s.plan.check_clock(witness.read())
    assert not s.engine.sent and s.journal.fd >= 0 and s.service.original.recheck() is s.plan


@pytest.mark.parametrize("service", [True, "offered"], indirect=True)
@pytest.mark.parametrize("route", ["complete", "expiry", "interrupted", "lost_notice", "lost_read"])
def test_borrowed_startup_clock_survives_original_service_paths(service, monkeypatch, route):
    witness = service.borrowed_clock
    if route == "complete":
        test_single_owner_polls_explicit_request_cancel_and_normal_restoration(service)
    elif route == "expiry":
        test_missing_or_refused_input_expires_without_inventing_cancel(
            service, monkeypatch, True, True
        )
    elif route == "interrupted":
        test_wait_interruption_closes_original_custody_without_retry(service, KeyboardInterrupt())
    elif route == "lost_notice":
        test_lost_durable_notice_ack_never_causes_resubmission(service, monkeypatch, "cancel_idle")
    else:
        test_failed_host_read_is_not_retried_but_original_clock_still_expires(service, monkeypatch)
    assert service.service.closed and not witness.closed
    assert service.service.clock_witness is witness
    os.fstat(witness.fd)


@pytest.mark.parametrize("service", [True], indirect=True)
def test_failed_borrowed_service_cleanup_does_not_close_clock(service):
    s = service

    def fail():
        raise OSError("private-secret")

    s.service._cleanup.append(fail)
    with pytest.raises(m.UnconfirmedOperator):
        s.service.close()
    assert s.service.failed and s.service.closed
    assert not s.borrowed_clock.closed
    assert s.session.processes.closed and s.inbox.closed
    s.plan.check_clock(s.borrowed_clock.read())


@pytest.mark.parametrize("service", [True], indirect=True)
def test_clock_replacement_refuses_without_closing_either_borrowed_handle(service):
    s = service
    replacement = m.plans.clock.ClockWitness(s.plan.original_clock)
    try:
        s.service.clock_witness = replacement
        refused(s.service)
        assert not s.borrowed_clock.closed and not replacement.closed
        os.fstat(s.borrowed_clock.fd)
        os.fstat(replacement.fd)
        assert not s.engine.sent
    finally:
        replacement.close()


@pytest.mark.parametrize("service", [True], indirect=True)
def test_failed_partial_assembly_does_not_close_supplied_original_clock(service):
    s = service
    publish(s, "request")
    assert s.service.coordinator.poll(lambda _: None).phase == "stopping_normal"
    descriptors = set(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedOperator):
        m.IdleService(
            s.service.original,
            s.projected,
            s.journal,
            s.docker,
            clock_witness=s.borrowed_clock,
        )
    assert set(os.listdir("/proc/self/fd")) == descriptors
    assert not s.borrowed_clock.closed and not s.inbox.closed and not s.session.processes.closed
    assert s.journal.fd >= 0 and len(s.engine.sent) == 1


@pytest.mark.parametrize("bad", [True, {}, "private-secret", object()])
def test_non_witness_borrowed_clock_refuses_without_dispatch(service, bad):
    s = service
    descriptors = set(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedOperator):
        m.IdleService(s.service.original, s.projected, s.journal, s.docker, clock_witness=bad)
    assert set(os.listdir("/proc/self/fd")) == descriptors
    assert not s.engine.sent and not s.service.clock_witness.closed


@pytest.mark.parametrize("service", [True], indirect=True)
@pytest.mark.parametrize(
    "fault", ["closed", "failed", "later_origin", "foreign_owner", "int_float"]
)
def test_borrowed_clock_must_be_live_same_owner_and_exact_original_window(service, fault):
    s, witness = service, service.borrowed_clock
    original, owner = witness.original, witness.owner
    if fault == "closed":
        witness.close()
    elif fault == "failed":
        witness.failed = True
    elif fault == "later_origin":
        witness.original = m.plans.clock.read()
    elif fault == "foreign_owner":
        witness.owner = owner[0] + 1, owner[1]
    else:
        # A bypassed frozen Window with numerically equal, differently typed ns
        # must not pass ordinary dataclass equality.
        witness.original = replace(original)
        object.__setattr__(witness.original, "before_ns", float(original.before_ns))
        assert witness.original == original
    descriptors = set(os.listdir("/proc/self/fd"))
    try:
        with pytest.raises(m.UnconfirmedOperator):
            m.IdleService(
                s.service.original, s.projected, s.journal, s.docker, clock_witness=witness
            )
        assert set(os.listdir("/proc/self/fd")) == descriptors
        assert not s.engine.sent and len(s.journal.entries) == 1
        assert witness.closed == (fault == "closed")
    finally:
        witness.owner, witness.original = owner, original


def test_run_cannot_return_success_when_final_closure_is_unconfirmed(service, monkeypatch):
    s = service
    monkeypatch.setattr(m, "IDLE_POLL_LIMIT", 1)

    def uncertain_close():
        raise OSError("PRIVATE close acknowledgement")

    s.service._cleanup.append(uncertain_close)
    refused(s.service)
    assert s.service.closed and s.inbox.closed and s.session.processes.closed


def test_lost_original_candidate_exit_cannot_restore_normal(service):
    s = service
    publish(s, "request")
    after_stop = []

    def wait(seconds):
        assert seconds == 0.25
        phase = s.journal.machine.state.phase
        if phase == "candidate_idle":
            s.engine.omit_exit = True
            publish(s, "cancel_idle")
        elif phase == "stopping_candidate":
            after_stop.append(True)
            if len(after_stop) == 3:
                raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        s.service.run(wait)
    assert len(s.engine.sent) == 3 and s.engine.sent[-1] == ("stop", b.CANDIDATE)
    assert not s.journal.machine.state.restored_generation
    assert s.session.processes.closed and s.inbox.closed and s.service.closed
