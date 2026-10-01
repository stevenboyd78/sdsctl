"""Full notice/phase join with real files; Engine and process evidence are fake."""

import os
from dataclasses import asdict, replace
from threading import Thread

import pytest

from . import test_supplemental_recording_service_operator as operators

m, cancellation, b = operators.m, operators.cancellation, operators.cancellation.b
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
    operators.layout,
    operators.tree,
    operators.routing,
    operators.projection,
    operators.binding,
    operators.directory,
    operators.prepared,
    operators.joined,
    operators.before_handoff,
    operators.transfer,
)


@pytest.fixture
def service(transfer, monkeypatch):
    s = transfer
    with operators.opened(s) as inbox:
        # All phase objects start with the SAME original CasePlan decoded object.
        # The already persisted preparation is byte-identical, never renewed.
        s.plan = inbox.plan
        s.before = m.launch.TransferHost(s.plan, s.projected, s.journal, s.docker)
        with cancellation.owned_session(s, monkeypatch):
            s.inbox = inbox
            s.coordinator = m.IdleCoordinator(inbox, s.before, s.session)
            yield s


def publish(s, action):
    return m.Publisher(
        s.inbox.original,
        action,
        m.base.checksum(s.journal.entries[0]["event"]),
        m.plans.clock.read().boottime_ns / m.plans.clock.NS,
    ).publish()


def no_wait(_):
    pytest.fail("Initial transfer or idle polling must not run a recovery wait")


def idle(s):
    publish(s, "request")
    assert s.coordinator.poll(no_wait).phase == "stopping_normal"
    assert s.coordinator.poll(no_wait).phase == "starting_candidate"
    assert s.coordinator.poll(no_wait).phase == "candidate_idle"
    assert s.coordinator.cancel is not None
    assert s.session.read == s.before.read


def refused(s):
    with pytest.raises(m.UnconfirmedOperator) as error:
        s.coordinator.poll(no_wait)
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__
    assert s.coordinator.failed and not s.coordinator.lock.locked()
    with pytest.raises(m.UnconfirmedOperator):
        s.coordinator.poll(no_wait)


def test_no_notice_never_invents_request_or_sends_an_app_command(service):
    s = service
    assert not s.engine.sent and not s.host.reads
    for _ in range(2):
        assert s.coordinator.poll(no_wait).phase == "prepared"
    assert not s.engine.sent and s.session.consume_operator is None
    assert not any(e["event"]["kind"] in ("request", "finish") for e in s.journal.entries)
    assert list(s.inbox.path.iterdir()) == []


def test_one_original_session_published_request_idle_cancel_and_restoration(service):
    s = service
    originals = s.session, s.session.processes, s.session.dispatch, s.session.executor
    plan_bytes, deadlines = s.plan.raw, s.plan.deadlines
    idle(s)
    retained = s.coordinator.cancel
    candidate_witness = retained.candidate_witness
    for _ in range(2):
        assert s.coordinator.poll(no_wait).phase == "candidate_idle"
        assert s.coordinator.cancel is retained and len(s.engine.sent) == 2
    publish(s, "cancel_idle")
    notices = {p.name: p.read_bytes() for p in s.inbox.path.iterdir()}
    waits = []
    result = s.coordinator.poll(waits.append)
    assert result.phase == "complete" and waits == [0.25, 0.25]
    assert originals == (s.session, s.session.processes, s.session.dispatch, s.session.executor)
    assert s.plan.raw == plan_bytes and s.plan.deadlines is deadlines
    assert s.coordinator.cancel is retained and retained.candidate_witness is candidate_witness
    assert s.session.processes.closed and candidate_witness.closed
    assert s.engine.sent == [
        ("stop", b.NORMAL),
        ("start", b.CANDIDATE),
        ("stop", b.CANDIDATE),
        ("start", b.NORMAL),
    ]
    state = s.journal.machine.state
    assert state.recording_outcome == "not_attempted" and state.artifact_sha256 is None
    assert state.launch_intent_sha256 is state.authorization_generation is None
    assert state.restored_generation != s.plan.normal_generation
    assert sum(e["event"]["kind"] == "request" for e in s.journal.entries) == 1
    assert sum(e["event"]["kind"] == "finish" for e in s.journal.entries) == 1
    assert {p.name: p.read_bytes() for p in s.inbox.path.iterdir()} == notices
    assert s.coordinator.finished and not s.coordinator.failed
    refused(s)
    assert len(s.engine.sent) == 4


@pytest.mark.parametrize("at_idle", [False, True])
@pytest.mark.parametrize("bad_input", [False, True])
def test_missing_or_refused_input_cannot_disable_independent_expiry(
    service,
    monkeypatch,
    at_idle,
    bad_input,
):
    s = service
    if at_idle:
        idle(s)
    if bad_input:
        path = s.inbox.path / ".pending-request"
        path.write_bytes(b"preserve uncertain publication")
        assert s.coordinator.poll(no_wait).phase == ("candidate_idle" if at_idle else "prepared")
        assert s.coordinator.input_failed and s.inbox.failed

        def do_not_retry():
            pytest.fail("Poisoned input must not be retried")

        monkeypatch.setattr(s.inbox, "consume", do_not_retry)
    clock = m.plans.clock.read
    target = s.journal.machine.state.deadline + 1

    def late():
        value = clock()
        amount = int(target * m.plans.clock.NS) - value.boottime_ns
        return replace(
            value,
            before_ns=value.before_ns + amount,
            after_ns=value.after_ns + amount,
            boottime_ns=value.boottime_ns + amount,
        )

    monkeypatch.setattr(m.plans.clock, "read", late)
    sent = list(s.engine.sent)
    result = s.coordinator.poll(no_wait)
    assert result.phase == "review" and s.coordinator.finished
    assert s.engine.sent == sent and not s.journal.machine.state.finish_requested
    if bad_input:
        assert path.read_bytes() == b"preserve uncertain publication"
    # No invented cancellation/normal restoration or replacement session.
    assert not s.session.processes.closed


def test_publication_lock_contention_is_not_an_input_failure(service):
    s = service
    publish(s, "request")
    with m.launch.binding.protected._private_directory(s.inbox.path, exclusive=True):
        assert s.coordinator.poll(no_wait).phase == "prepared"
    assert not s.coordinator.input_failed
    assert s.coordinator.poll(no_wait).phase == "stopping_normal"


@pytest.mark.parametrize("route", ["request", "cancel_idle"])
def test_durable_notice_with_lost_consumer_ack_is_not_resubmitted(service, monkeypatch, route):
    s = service
    if route == "cancel_idle":
        idle(s)
    publish(s, route)
    append = s.journal.append
    kind = "request" if route == "request" else "finish"
    lost = []

    def lose_ack(event):
        result = append(event)
        if event["kind"] == kind:
            lost.append(kind)
            raise OSError("PRIVATE lost durable acknowledgement")
        return result

    monkeypatch.setattr(s.journal, "append", lose_ack)
    result = s.coordinator.poll(lambda _: None)
    assert s.coordinator.input_failed and lost == [kind]
    assert sum(e["event"]["kind"] == kind for e in s.journal.entries) == 1
    if route == "request":
        assert result.phase == "stopping_normal" and len(s.engine.sent) == 1
        assert s.coordinator.poll(no_wait).phase == "starting_candidate"
        assert lost == [kind]
    else:
        assert result.phase == "complete" and len(s.engine.sent) == 4


@pytest.mark.parametrize(
    "member",
    [
        "inbox",
        "transfer",
        "session",
        "original",
        "plan",
        "projected",
        "journal",
        "processes",
        "dispatch",
        "executor",
    ],
)
def test_phase_objects_cannot_be_replaced(service, member):
    s = service
    setattr(s.coordinator, member, object())
    refused(s)
    assert not s.engine.sent


@pytest.mark.parametrize("fault", ["callback", "reader", "clock", "now", "executor", "plan"])
def test_nested_session_and_plan_custody_cannot_be_replaced(service, fault):
    s = service
    if fault == "callback":
        s.session.consume_operator = lambda: True
    elif fault == "reader":
        s.session.read = lambda: None
    elif fault == "clock":
        s.session.processes.read_clock = lambda: (s.plan.boot, s.plan.deadlines.issued_at)
    elif fault == "now":
        s.session.dispatch.now = lambda: s.plan.deadlines.issued_at
    elif fault == "executor":
        s.session.executor = object()
    else:
        s.before.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
    refused(s)
    assert not s.engine.sent


def test_foreign_thread_cannot_advance_or_close_caller_custody(service):
    s = service
    errors = []

    def worker():
        try:
            s.coordinator.poll(no_wait)
        except m.UnconfirmedOperator as error:
            errors.append(str(error))

    thread = Thread(target=worker)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and errors == [m.MESSAGE]
    assert not s.engine.sent and not s.session.processes.closed
    refused(s)


def test_cancel_interruption_never_retries_or_reconstructs_session(service):
    s = service
    idle(s)
    publish(s, "cancel_idle")

    def interrupted(seconds):
        assert seconds == 0.25
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        s.coordinator.poll(interrupted)
    assert s.session.processes.closed and s.coordinator.finished and s.coordinator.failed
    assert s.journal.machine.state.phase == "stopping_candidate"
    assert len(s.engine.sent) == 3
    refused(s)
    assert len(s.engine.sent) == 3


def test_journal_mutation_cannot_create_authority(service):
    s = service
    publish(s, "request")
    (s.journal.path / s.journal.name(0)).write_bytes(b"PRIVATE altered preparation")
    refused(s)
    assert not s.engine.sent and (s.inbox.path / "request.json").is_file()


def test_coordinator_does_not_own_extra_descriptors(service):
    s = service
    before = len(os.listdir("/proc/self/fd"))
    s.coordinator.poll(no_wait)
    # Original normal process witness is synthetic; no new file-owning coordinator.
    assert len(os.listdir("/proc/self/fd")) == before


def test_cannot_construct_a_new_coordinator_after_transfer(service):
    s = service
    idle(s)
    with pytest.raises(m.UnconfirmedOperator):
        m.IdleCoordinator(s.inbox, s.before, s.session)
    assert len(s.engine.sent) == 2 and not s.session.processes.closed


@pytest.mark.parametrize("replacement", [False, True])
def test_retained_idle_reader_cannot_be_lost_or_reconstructed(service, replacement):
    s = service
    idle(s)
    s.coordinator.cancel = m.launch.NeverLaunchedHost(s.before, s.session) if replacement else None
    publish(s, "cancel_idle")
    refused(s)
    assert len(s.engine.sent) == 2 and not s.journal.machine.state.finish_requested


def test_native_launch_cannot_fall_back_into_pristine_cancel(service):
    s = service
    idle(s)
    sample = s.before.read()
    s.append(
        "authorize_operator",
        generation=s.journal.machine.state.candidate_generation,
        bootstrap_sha256=s.plan.bootstrap.sha256,
        launch_plan_sha256="d" * 64,
        idle_evidence_sha256="e" * 64,
        observation=asdict(sample.observation),
    )
    publish(s, "cancel_idle")
    refused(s)
    assert len(s.engine.sent) == 2 and not s.journal.machine.state.finish_requested


@pytest.mark.parametrize("fault", ["old_recording", "new_recording", "candidate"])
def test_changed_pristine_state_cannot_dispatch_cancellation(service, fault):
    s = service
    idle(s)
    publish(s, "cancel_idle")
    if fault == "old_recording":
        path = s.recording_root / s.projected.host.baseline.files[0][0]
        path.write_bytes(b"PRIVATE changed")
    elif fault == "new_recording":
        path = s.recording_root / "unexpected.wav"
        path.write_bytes(b"PRIVATE new")
    else:
        s.host.values["app_" + b.CANDIDATE]["State"]["Pid"] += 1
    refused(s)
    assert len(s.engine.sent) == 2 and s.journal.machine.state.finish_requested
    assert not s.session.processes.closed


def test_lost_candidate_stop_reply_is_reconciled_not_reissued(service):
    s = service
    idle(s)
    publish(s, "cancel_idle")
    s.engine.start_error = OSError("PRIVATE lost stop acknowledgement")

    def wait(seconds):
        assert seconds == 0.25
        s.engine.start_error = None

    result = s.coordinator.poll(wait)
    assert result.phase == "complete"
    assert s.engine.sent.count(("stop", b.CANDIDATE)) == 1 and len(s.engine.sent) == 4


def test_missing_candidate_exit_never_starts_normal_or_fabricates_success(service):
    s = service
    idle(s)
    publish(s, "cancel_idle")
    s.engine.omit_exit = True
    waits = []

    def stop_observing(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        s.coordinator.poll(stop_observing)
    assert waits == [0.25] * 3
    assert s.journal.machine.state.phase == "stopping_candidate"
    assert len(s.engine.sent) == 3 and s.engine.sent[-1] == ("stop", b.CANDIDATE)
    assert s.session.processes.closed and s.coordinator.failed


def test_unavailable_host_observation_cannot_retry_the_cache_or_disable_expiry(
    service,
    monkeypatch,
):
    s = service
    publish(s, "request")
    calls = []

    def unavailable(*args):
        calls.append(True)
        raise OSError("PRIVATE cache error")

    monkeypatch.setattr(m.launch.normal_read.cached, "_read_probe", unavailable)
    for _ in range(2):
        result = s.coordinator.poll(no_wait)
        assert result.phase == "requested" and result.outcome == "observation_unavailable"
    assert calls == [True] and s.before.failed and not s.engine.sent
    clock = m.plans.clock.read
    target = s.journal.machine.state.deadline + 1

    def expired():
        value = clock()
        shift = int(target * m.plans.clock.NS) - value.boottime_ns
        return replace(
            value,
            before_ns=value.before_ns + shift,
            after_ns=value.after_ns + shift,
            boottime_ns=value.boottime_ns + shift,
        )

    monkeypatch.setattr(m.plans.clock, "read", expired)
    assert s.coordinator.poll(no_wait).phase == "review"
    assert calls == [True] and not s.engine.sent
