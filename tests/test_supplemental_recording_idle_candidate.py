"""Read-only candidate assembly; real files/FDs, explicitly synthetic init/idle I/O."""

import os
from threading import Thread

import pytest

from . import test_supplemental_recording_idle_service as services

m, b = services.m, services.b
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
) = (
    services.layout,
    services.tree,
    services.routing,
    services.projection,
    services.binding,
    services.directory,
    services.prepared,
    services.joined,
    services.before_handoff,
    services.transfer,
    services.service,
)
PROFILE = dict(
    image_environment_sha256="f" * 64,
    timezone="America/Denver",
    hostname="offline-candidate",
    architecture="amd64",
    runtime_workers=2,
)


@pytest.fixture
def candidate_fixture(service, monkeypatch):
    s = service
    s.idles, s.test_fds = [], []
    monkeypatch.setattr(m.launch.runtime, "ROOT_UID", os.geteuid())

    def idle_init(self, plan, witness, generation, *, zero_domain):
        # This duplicates a real descriptor, NOT an actual process pidfd.
        # Clock namespace/idle claim content is deliberately synthetic here.
        self.plan, self.init, self.generation = plan, witness.identity, generation
        self.zero_domain, self.opened = zero_domain, []
        self.pidfd = os.dup(witness.fd)
        self.failed = self.closed = False
        s.idles.append(self)

    monkeypatch.setattr(m.launch.idle_module.Idle, "__init__", idle_init)
    monkeypatch.setattr(m.launch.engine.dispatch.process, "read_identity", s.engine.identity)

    def forbidden(*args, **kwargs):
        pytest.fail(
            "Resource preparation must not qualify, observe the host, or launch native code"
        )

    monkeypatch.setattr(m.launch.Launch, "__init__", forbidden)
    monkeypatch.setattr(m.launch.BootstrapHost, "__call__", forbidden)
    monkeypatch.setattr(m.launch.BootstrapHost, "prepare", forbidden)
    monkeypatch.setattr(m.launch.CandidateQualification, "__call__", forbidden)
    monkeypatch.setattr(m.launch.CandidateQualification, "during", forbidden)

    def attach_fixture_fd():
        witness = s.service.processes.witnesses[b.CANDIDATE]
        assert not hasattr(witness, "fd")
        witness.fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        s.test_fds.append(witness.fd)
        monkeypatch.setattr(m.launch.engine.dispatch.process, "ProcessWitness", type(witness))
        return witness

    s.attach_fixture_fd = attach_fixture_fd
    try:
        yield s
    finally:
        s.service.close()
        for fd in s.test_fds:
            os.close(fd)


def at_idle(s, action):
    services.publish(s, "request")
    visited = []

    def wait(seconds):
        assert seconds == 0.25
        if s.journal.machine.state.phase == "candidate_idle" and not visited:
            visited.append(True)
            s.witness = s.attach_fixture_fd()
            action()

    result = s.service.run(wait)
    assert visited == [True]
    return result


def prepare(s, **changes):
    return s.service.prepare_candidate(**(PROFILE | changes))


def denied(callback):
    with pytest.raises(m.UnconfirmedOperator) as error:
        callback()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_original_running_owner_prepares_only_readers_and_keeps_pristine_cancellation(
    candidate_fixture,
):
    s = candidate_fixture
    original_session = s.session
    captured = []

    def action():
        history = tuple(m.base.encode(e) for e in s.journal.entries)
        host_reads, cached_reads, commands = (
            len(s.host.reads),
            len(s.cached_calls),
            list(s.engine.sent),
        )
        candidate = prepare(s)
        captured.append(candidate)
        assert candidate.recheck() is None
        assert candidate.service is s.service and candidate.coordinator is s.service.coordinator
        assert candidate.witness is s.witness
        assert candidate.record is s.service.coordinator.cancel.candidate_record
        assert candidate.idle.plan is candidate.reader.plan is candidate.qualifier.plan is s.plan
        assert candidate.qualifier.runtime_workers == 2
        assert candidate.reader.witness is candidate.qualifier.witness is s.witness
        assert candidate.reader.projected is s.projected
        assert tuple(m.base.encode(e) for e in s.journal.entries) == history
        assert (len(s.host.reads), len(s.cached_calls), s.engine.sent) == (
            host_reads,
            cached_reads,
            commands,
        )
        assert s.session is original_session and s.session.read == s.before.read
        assert not s.witness.closed and s.journal.fd >= 0
        services.publish(s, "cancel_idle")

    assert at_idle(s, action).phase == "complete"
    candidate = captured[0]
    assert candidate.closed and candidate.idle.closed and candidate.reader.failed
    assert s.service.closed and s.witness.closed
    assert s.service.original.recheck() is s.plan and s.journal.fd >= 0
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.journal.machine.state.launch_intent_sha256 is None
    assert s.engine.sent == [
        ("stop", b.NORMAL),
        ("start", b.CANDIDATE),
        ("stop", b.CANDIDATE),
        ("start", b.NORMAL),
    ]
    denied(candidate.recheck)
    candidate.close()


def test_preparation_outside_original_running_loop_is_refused_without_reads(candidate_fixture):
    s = candidate_fixture
    denied(lambda: prepare(s))
    assert s.service.failed and not s.service.candidate_attempted
    assert not s.idles and not s.engine.sent and not s.host.reads


@pytest.mark.parametrize("phase", ["prepared", "stopping_normal", "starting_candidate"])
def test_preparation_in_wrong_phase_cannot_acquire_candidate_resources(candidate_fixture, phase):
    s = candidate_fixture
    if phase != "prepared":
        services.publish(s, "request")

    def wait(_):
        if s.journal.machine.state.phase == phase:
            prepare(s)

    denied(lambda: s.service.run(wait))
    assert s.service.candidate_attempted and not s.idles
    assert s.service.closed and s.service.processes.closed
    assert s.journal.machine.state.phase == phase


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_environment_sha256", "invalid"),
        ("timezone", "../private"),
        ("hostname", "bad host"),
        ("architecture", "wrong"),
        ("runtime_workers", True),
    ],
)
def test_partial_assembly_failure_closes_new_descriptors_and_original_owner(
    candidate_fixture, field, value
):
    s = candidate_fixture

    def action():
        prepare(s, **{field: value})

    denied(lambda: at_idle(s, action))
    assert all(value.closed and value.pidfd == -1 for value in s.idles)
    assert s.service.closed and s.witness.closed and s.service.candidate is None
    assert s.service.candidate_attempted and s.service.failed
    assert len(s.engine.sent) == 2 and s.journal.fd >= 0
    denied(lambda: prepare(s))


@pytest.mark.parametrize("fault", ["reader", "qualifier", "interrupt"])
def test_constructor_failures_release_only_original_new_resources(
    candidate_fixture, monkeypatch, fault
):
    s = candidate_fixture

    def failure(*args, **kwargs):
        if fault == "interrupt":
            raise KeyboardInterrupt()
        raise OSError("PRIVATE construction error")

    target = m.launch.BootstrapHost if fault == "reader" else m.launch.CandidateQualification
    monkeypatch.setattr(target, "__init__", failure)
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            at_idle(s, lambda: prepare(s))
    else:
        denied(lambda: at_idle(s, lambda: prepare(s)))
    assert len(s.idles) == 1 and s.idles[0].closed and s.idles[0].pidfd == -1
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "service",
        "coordinator",
        "witness",
        "record",
        "idle",
        "reader",
        "qualifier",
        "zero_domain",
        "process_map",
        "fd",
        "generation",
        "profile",
        "reader_plan",
        "session",
        "finish",
        "expired",
    ],
)
def test_changed_custody_profile_phase_or_deadline_cannot_be_reused(
    candidate_fixture, monkeypatch, fault
):
    s = candidate_fixture

    def action():
        candidate = prepare(s)
        if fault in (
            "service",
            "coordinator",
            "witness",
            "record",
            "idle",
            "reader",
            "qualifier",
            "zero_domain",
        ):
            setattr(candidate, fault, object())
        elif fault == "process_map":
            s.service.processes.witnesses[b.CANDIDATE] = object()
        elif fault == "fd":
            s.witness.fd = -1
        elif fault == "generation":
            candidate.idle.generation = "b" * 64
        elif fault == "profile":
            candidate.qualifier.runtime_workers = 1
        elif fault == "reader_plan":
            candidate.reader.plan = m.plans.load_bytes(s.plan.raw, s.plan.sha256)
        elif fault == "session":
            s.session.read = lambda: None
        elif fault == "finish":
            services.publish(s, "cancel_idle")
            assert s.inbox.consume()
        else:
            services.expire(s, monkeypatch)
        try:
            denied(candidate.recheck)
            assert candidate.failed
            denied(candidate.recheck)
            # Stop the owner instead of entering another phase with deliberately
            # corrupted fixture objects. No recovery is inferred from closure.
            raise KeyboardInterrupt()
        finally:
            if fault == "process_map":
                s.service.processes.witnesses[b.CANDIDATE] = s.witness

    with pytest.raises(KeyboardInterrupt):
        at_idle(s, action)
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2
    assert s.idles[0].closed and s.idles[0].pidfd == -1


def test_second_preparation_is_refused_without_rebinding_or_leaking(candidate_fixture):
    s = candidate_fixture

    def action():
        prepare(s)
        prepare(s)

    denied(lambda: at_idle(s, action))
    assert len(s.idles) == 1 and s.idles[0].closed
    assert s.service.candidate_attempted and s.service.closed and s.witness.closed


def test_foreign_thread_cannot_recheck_or_close_original_candidate(candidate_fixture):
    s, errors = candidate_fixture, []

    def action():
        candidate = prepare(s)

        def other_thread():
            for operation in (candidate.recheck, candidate.close):
                try:
                    operation()
                except m.UnconfirmedOperator as error:
                    errors.append(str(error))

        worker = Thread(target=other_thread)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive() and errors == [m.MESSAGE, m.MESSAGE]
        assert candidate.failed and not candidate.closed and not s.witness.closed
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        at_idle(s, action)
    assert s.service.closed and s.idles[0].closed and s.witness.closed


def test_borrowed_zero_domain_is_not_closed_by_preparation(candidate_fixture, monkeypatch):
    s = candidate_fixture
    closed = []
    domain = object.__new__(m.launch.idle_module.time_domain.ZeroDomain)
    monkeypatch.setattr(domain, "close", lambda: closed.append(True))

    def action():
        candidate = prepare(s, zero_domain=domain)
        assert candidate.idle.zero_domain is candidate.zero_domain is domain
        services.publish(s, "cancel_idle")

    assert at_idle(s, action).phase == "complete"
    assert not closed


def test_replacing_service_candidate_cannot_redirect_original_cleanup(candidate_fixture):
    s = candidate_fixture

    def action():
        s.saved_candidate = prepare(s)
        s.service.candidate = object()

    denied(lambda: at_idle(s, action))
    assert s.saved_candidate.closed and s.idles[0].closed and s.witness.closed
    assert s.journal.fd >= 0 and s.service.closed and len(s.engine.sent) == 2


@pytest.mark.parametrize("fault", ["missing", "dead", "finish", "input_failed"])
def test_lost_or_consumed_idle_cannot_prepare_resources(candidate_fixture, fault):
    s = candidate_fixture

    def action():
        if fault == "missing":
            del s.service.processes.witnesses[b.CANDIDATE]
        elif fault == "dead":
            s.engine.dead.add(s.witness.identity.container_id)
        elif fault == "finish":
            services.publish(s, "cancel_idle")
            assert s.inbox.consume()
        else:
            s.service.coordinator.input_failed = True
        try:
            denied(lambda: prepare(s))
            assert not s.idles
        finally:
            if fault == "missing":
                s.service.processes.witnesses[b.CANDIDATE] = s.witness

    denied(lambda: at_idle(s, action))
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2


def test_foreign_thread_cannot_prepare_inside_running_owner(candidate_fixture):
    s, errors = candidate_fixture, []

    def action():
        def foreign():
            try:
                prepare(s)
            except m.UnconfirmedOperator as error:
                errors.append(str(error))

        worker = Thread(target=foreign)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive() and errors == [m.MESSAGE]
        assert not s.idles and not s.witness.closed

    denied(lambda: at_idle(s, action))
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2


@pytest.mark.parametrize("fault", ["reader", "qualifier", "history", "service"])
def test_changes_during_assembly_do_not_escape_original_owner(
    candidate_fixture, monkeypatch, fault
):
    s = candidate_fixture
    original = m.launch.CandidateQualification.__init__

    def changing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if fault == "reader":
            # Existing exact-reader members are guarded again before publication.
            self.idle.generation = "b" * 64
        elif fault == "qualifier":
            self.failed = True
        elif fault == "history":
            services.publish(s, "cancel_idle")
            assert s.inbox.consume()
        else:
            s.service.failed = True

    monkeypatch.setattr(m.launch.CandidateQualification, "__init__", changing)
    denied(lambda: at_idle(s, lambda: prepare(s)))
    assert s.service.candidate is None and s.service.candidate_attempted
    assert s.idles[0].closed and s.witness.closed and len(s.engine.sent) == 2


def test_cleanup_error_withholds_completion_but_closes_other_original_handles(candidate_fixture):
    s, captured = candidate_fixture, []

    def action():
        candidate = prepare(s)
        captured.append(candidate)

        def uncertain_close():
            raise OSError("PRIVATE uncertain reader close")

        candidate._cleanup.append(uncertain_close)
        services.publish(s, "cancel_idle")

    denied(lambda: at_idle(s, action))
    assert captured[0].closed and captured[0].failed and captured[0].reader.failed
    assert s.idles[0].closed and s.witness.closed and s.service.closed
    assert s.journal.machine.state.phase == "complete"  # Durable history is retained.
    assert len(s.engine.sent) == 4 and s.journal.fd >= 0
