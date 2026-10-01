"""Original service plus real host-reader worker; explicit synthetic HA/init/runtime.

These tests do not qualify installed source/runtime or actual process custody.
The complete BootstrapHost/HostObserver and original files/journal are used;
CandidateQualification hashing, Engine/Supervisor and idle evidence are fixtures.
"""

import time
from dataclasses import replace
from threading import Event, Thread

import pytest

from . import test_supplemental_recording_idle_candidate as resources

m, b, services = resources.m, resources.b, resources.services
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
) = (
    resources.layout,
    resources.tree,
    resources.routing,
    resources.projection,
    resources.binding,
    resources.directory,
    resources.prepared,
    resources.joined,
    resources.before_handoff,
    resources.transfer,
    resources.service,
    resources.candidate_fixture,
)
REAL_PREPARE = m.launch.BootstrapHost.prepare
REAL_READ = m.launch.BootstrapHost.__call__


@pytest.fixture
def observation(candidate_fixture, monkeypatch):
    s = candidate_fixture
    s.observation_order, s.collected = [], []
    s.before_join = s.after_join = lambda: None

    def idle_read(self):
        return m.launch.idle_module.Evidence(
            s.plan.sha256,
            self.generation,
            self.init,
            "a" * 64,
            "b" * 64,
            s.plan.lease_sha256,
            "c" * 64,
            s.plan.deadlines.issued_at,
            m.plans.clock.read().boottime_ns / m.plans.clock.NS,
        )

    def prepare(self):
        s.observation_order.append("prepare")
        return REAL_PREPARE(self)

    def read(self):
        s.observation_order.append("join")
        sample = REAL_READ(self)
        s.collected.append(sample)
        return sample

    def qualify(self, observe):
        # Explicit qualification double: no runtime/source success is claimed.
        s.observation_order.append("qualify-before")
        s.before_join()
        sample = observe()
        s.after_join()
        s.observation_order.append("qualify-after")
        return sample

    monkeypatch.setattr(m.launch.idle_module.Idle, "read", idle_read)
    monkeypatch.setattr(m.launch.BootstrapHost, "prepare", prepare)
    monkeypatch.setattr(m.launch.BootstrapHost, "__call__", read)
    monkeypatch.setattr(m.launch.CandidateQualification, "during", qualify)
    return s


def at_idle(s, action):
    def prepared_action():
        s.candidate = resources.prepare(s)
        action()

    return resources.at_idle(s, prepared_action)


def test_original_service_observes_without_phase_change_or_native_authority(observation):
    s = observation
    original_session = s.session
    results = []

    def action():
        entries = tuple(m.base.encode(e) for e in s.journal.entries)
        commands, cached = list(s.engine.sent), len(s.cached_calls)
        reads = len(s.host.reads)
        result = s.service.observe_candidate()
        results.append(result)
        assert type(result) is m.launch.bootstrap.recovery.Sample
        assert result.boot_id == s.plan.boot
        assert result.observation.candidate.healthy is None
        assert result.observation.candidate.recording is None
        assert result.observation.files.stage == "pristine"
        assert result.observation.normal.state == "stopped"
        assert result.observation.candidate.generation == s.candidate.record.generation
        assert result.observation.sampled_at <= s.collected[0].observation.sampled_at
        assert result.now >= s.collected[0].now
        assert 0 <= result.now - result.observation.sampled_at < 2
        assert tuple(m.base.encode(e) for e in s.journal.entries) == entries
        assert s.engine.sent == commands and len(s.cached_calls) == cached
        assert s.host.reads[reads:] == [
            "apps",
            "jobs",
            "core",
            "normal",
            "candidate",
            "apps",
            "jobs",
        ]
        assert s.session is original_session and s.session.read == s.before.read
        assert not s.candidate.reader.failed and s.candidate.reader.pending is None
        assert not s.witness.closed and s.journal.fd >= 0
        services.publish(s, "cancel_idle")

    assert at_idle(s, action).phase == "complete"
    assert s.observation_order == ["prepare", "qualify-before", "join", "qualify-after"]
    assert len(results) == 1 and s.service.observation_attempted
    assert s.service.closed and s.witness.closed and s.candidate.closed
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert s.journal.machine.state.launch_intent_sha256 is None
    assert s.journal.machine.state.authorization_generation is None
    assert len(s.engine.sent) == 4


def test_prepared_read_overlaps_qualification_and_keeps_original_start(observation, monkeypatch):
    s = observation
    entered, release = Event(), Event()
    original = m.launch._PreparedHostRead._run

    def blocked(self):
        entered.set()
        assert release.wait(1)
        original(self)

    monkeypatch.setattr(m.launch._PreparedHostRead, "_run", blocked)

    def during():
        assert entered.wait(1)
        pending = s.candidate.reader.pending
        s.original_read = pending
        assert pending.worker.is_alive() and not pending.done.is_set()
        release.set()

    s.before_join = during

    def action():
        result = s.service.observe_candidate()
        assert result.observation.sampled_at <= s.original_read.began
        assert not s.original_read.worker.is_alive()
        services.publish(s, "cancel_idle")

    try:
        assert at_idle(s, action).phase == "complete"
    finally:
        release.set()


@pytest.mark.parametrize("previously_run", [False, True])
def test_not_running_service_cannot_start_observation(observation, previously_run):
    s = observation
    if previously_run:
        at_idle(s, lambda: services.publish(s, "cancel_idle"))
    resources.denied(s.service.observe_candidate)
    assert s.service.failed and not s.observation_order


def test_active_service_without_candidate_preparation_cannot_observe(observation):
    s = observation
    resources.denied(lambda: resources.at_idle(s, s.service.observe_candidate))
    assert s.service.observation_attempted and s.service.failed and s.service.closed
    assert not s.observation_order and len(s.engine.sent) == 2


def test_second_observation_is_not_an_automatic_retry(observation):
    s = observation

    def action():
        s.service.observe_candidate()
        s.service.observe_candidate()

    resources.denied(lambda: at_idle(s, action))
    assert s.observation_order.count("prepare") == 1
    assert s.service.failed and s.service.closed and s.witness.closed
    assert len(s.engine.sent) == 2


@pytest.mark.parametrize("when", ["prepare", "before", "after"])
@pytest.mark.parametrize("error", [OSError("PRIVATE failure"), KeyboardInterrupt(), SystemExit(75)])
def test_refused_or_interrupted_read_poison_owner_and_discard_original_worker(
    observation, monkeypatch, when, error
):
    s = observation

    def fail():
        raise error

    if when == "prepare":
        original = m.launch.BootstrapHost.prepare

        def uncertain(self):
            original(self)
            fail()

        monkeypatch.setattr(m.launch.BootstrapHost, "prepare", uncertain)
    elif when == "before":
        s.before_join = fail
    else:
        s.after_join = fail

    if isinstance(error, Exception):
        resources.denied(lambda: at_idle(s, s.service.observe_candidate))
    else:
        with pytest.raises(type(error)):
            at_idle(s, s.service.observe_candidate)
    assert s.service.failed and s.service.closed and s.witness.closed
    assert s.candidate.reader.failed and s.candidate.closed and len(s.engine.sent) == 2
    pending = s.candidate.reader.pending
    if pending:
        pending.worker.join(2)
        assert pending.cancelled.is_set() and not pending.worker.is_alive()
    resources.denied(s.service.observe_candidate)
    assert s.journal.machine.state.recording_outcome == "not_attempted"


@pytest.mark.parametrize("fault", ["skip", "duplicate", "replace", "pending"])
def test_qualifier_cannot_skip_repeat_or_replace_original_observation(
    observation, monkeypatch, fault
):
    s = observation

    def unconfirmed(self, observe):
        if fault == "skip":
            return None
        sample = observe()
        if fault == "duplicate":
            observe()
        elif fault == "pending":
            s.candidate.reader.pending = object()
            return sample
        return replace(sample)

    monkeypatch.setattr(m.launch.CandidateQualification, "during", unconfirmed)
    resources.denied(lambda: at_idle(s, s.service.observe_candidate))
    assert s.service.failed and s.service.closed and s.witness.closed
    assert len(s.engine.sent) == 2 and s.observation_order.count("join") <= 1


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize(
    "fault", ["reader", "qualifier", "candidate", "session", "finish", "deadline"]
)
def test_phase_and_original_custody_must_survive_entire_read(observation, monkeypatch, when, fault):
    s = observation

    def change():
        if fault in ("reader", "qualifier"):
            setattr(s.candidate, fault, object())
        elif fault == "candidate":
            s.service.candidate = object()
        elif fault == "session":
            s.session.read = lambda: None
        elif fault == "finish":
            services.publish(s, "cancel_idle")
            assert s.inbox.consume()
        else:
            services.expire(s, monkeypatch)

    if when == "before":
        s.before_join = change
    else:
        s.after_join = change
    resources.denied(lambda: at_idle(s, s.service.observe_candidate))
    assert s.service.failed and s.service.closed and s.witness.closed
    assert s.candidate.closed and len(s.engine.sent) == 2


def test_full_window_cannot_be_renewed_after_delayed_preparation(observation, monkeypatch):
    s = observation
    original = m.launch.BootstrapHost.prepare

    def slow(self):
        time.sleep(2.01)
        return original(self)

    monkeypatch.setattr(m.launch.BootstrapHost, "prepare", slow)
    resources.denied(lambda: at_idle(s, s.service.observe_candidate))
    assert s.observation_order == ["prepare", "qualify-before"]
    assert s.service.failed and s.candidate.reader.failed and len(s.engine.sent) == 2


def test_foreign_thread_cannot_start_the_owned_observation(observation):
    s, errors = observation, []

    def action():
        def foreign():
            try:
                s.service.observe_candidate()
            except m.UnconfirmedOperator as error:
                errors.append(str(error))

        worker = Thread(target=foreign)
        worker.start()
        worker.join(2)
        assert not worker.is_alive() and errors == [m.MESSAGE]

    resources.denied(lambda: at_idle(s, action))
    assert s.service.failed and s.service.closed and not s.observation_order
    assert not s.service.observation_attempted and s.witness.closed


@pytest.mark.parametrize("fault", ["boot", "future", "stale", "native_health", "native_recording"])
def test_joined_sample_does_not_promote_invalid_or_native_results(observation, monkeypatch, fault):
    s = observation
    original = m.launch.BootstrapHost.__call__

    def changed(self):
        sample = original(self)
        if fault == "boot":
            return replace(sample, boot_id="0" * 32)
        if fault == "future":
            return replace(sample, now=sample.now + 10)
        if fault == "stale":
            return replace(sample, observation=replace(sample.observation, sampled_at=0))
        candidate = replace(
            sample.observation.candidate,
            **{"healthy" if fault == "native_health" else "recording": True},
        )
        return replace(sample, observation=replace(sample.observation, candidate=candidate))

    monkeypatch.setattr(m.launch.BootstrapHost, "__call__", changed)
    resources.denied(lambda: at_idle(s, s.service.observe_candidate))
    assert s.service.failed and s.service.closed and len(s.engine.sent) == 2


def test_post_read_qualification_time_counts_towards_original_window(observation):
    s = observation
    s.after_join = lambda: time.sleep(2.01)
    resources.denied(lambda: at_idle(s, s.service.observe_candidate))
    assert s.service.failed and s.service.closed and s.candidate.reader.failed
    assert len(s.engine.sent) == 2


def test_catching_refusal_cannot_continue_original_service(observation):
    s = observation

    def failed_qualifier():
        raise OSError("PRIVATE qualification failure")

    s.before_join = failed_qualifier

    def action():
        resources.denied(s.service.observe_candidate)
        resources.denied(s.service.observe_candidate)

    resources.denied(lambda: at_idle(s, action))
    assert s.service.closed and s.witness.closed and s.candidate.reader.failed
    assert s.observation_order == ["prepare", "qualify-before"]
    assert len(s.engine.sent) == 2


def test_reentrant_collection_cannot_dispatch_second_worker(observation):
    s = observation
    s.before_join = s.service.observe_candidate
    resources.denied(lambda: at_idle(s, s.service.observe_candidate))
    assert s.service.failed and s.service.closed and s.candidate.reader.failed
    assert s.observation_order == ["prepare", "qualify-before"]
    assert len(s.engine.sent) == 2
