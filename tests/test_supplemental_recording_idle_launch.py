"""Passive original-owner Launch join; HA/init/Engine metadata are synthetic.

Real Launch/journal/private files are used, but no native process or installed
endpoint is qualified. Native dispatch is forbidden in this idle-only assembly.
"""

import time
from threading import Thread

import pytest

from . import test_supplemental_recording_idle_observation as observation_tests

resources = observation_tests.resources
m, services = resources.m, resources.services
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
    observation,
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
    observation_tests.observation,
)
REAL_LAUNCH_INIT = m.launch.Launch.__init__


@pytest.fixture
def launch_fixture(candidate_fixture, monkeypatch):
    s = candidate_fixture

    class Endpoint:
        closed = False
        checks = 0

        def check(self):
            assert not self.closed
            self.checks += 1

        def close(self):
            self.closed = True

    s.endpoint = Endpoint()
    monkeypatch.setattr(m.launch.engine, "Endpoint", Endpoint)
    monkeypatch.setattr(m.launch.Launch, "__init__", REAL_LAUNCH_INIT)

    def forbidden(*args, **kwargs):
        pytest.fail("Passive launch preparation cannot connect, create or attach an Engine exec")

    monkeypatch.setattr(m.launch.engine.Client, "__init__", forbidden)
    return s


def prepare(s, **changes):
    values = dict(launch_sha256="a" * 64, profile_sha256="b" * 64) | changes
    return s.service.prepare_launch(s.endpoint, **values)


def at_idle(s, action):
    def prepared_action():
        s.candidate = resources.prepare(s)
        action()

    return resources.at_idle(s, prepared_action)


def test_passive_launch_borrows_original_bindings_and_keeps_explicit_cancel(launch_fixture):
    s = launch_fixture
    original_session = s.session
    runs = []

    def action():
        entries = tuple(m.base.encode(e) for e in s.journal.entries)
        reads = list(s.host.reads), list(s.cached_calls), list(s.engine.sent)
        run = prepare(s)
        runs.append(run)
        assert type(run) is m.launch.Launch
        assert run is s.service.prepared_launch
        assert run.plan is s.plan and run.journal is s.journal
        assert run.idle is s.candidate.idle and run.witness is s.witness
        assert run.read is s.candidate.reader and run.qualify is s.candidate.qualifier
        assert run.endpoint is s.endpoint and s.endpoint.checks == 1
        assert run.command.ready_by == s.plan.lease["ready_by"]
        assert not run.used and not run.confirm_attempted
        assert run.action is run.claim is run.client is run.ready is run.probe is None
        assert tuple(m.base.encode(e) for e in s.journal.entries) == entries
        assert (s.host.reads, s.cached_calls, s.engine.sent) == reads
        assert s.session is original_session and s.session.read == s.before.read
        services.publish(s, "cancel_idle")

    assert at_idle(s, action).phase == "complete"
    assert runs[0].closed and s.candidate.closed and s.witness.closed
    assert not s.endpoint.closed  # No Client took ownership.
    assert s.service.original.recheck() is s.plan and s.journal.fd >= 0
    assert s.journal.machine.state.launch_intent_sha256 is None
    assert s.journal.machine.state.authorization_generation is None
    assert s.journal.machine.state.recording_outcome == "not_attempted"
    assert len(s.engine.sent) == 4


@pytest.mark.parametrize("observe_first", [False, True])
def test_preparation_and_read_only_observation_do_not_infer_native_permission(
    launch_fixture, observation, observe_first
):
    s = launch_fixture

    def action():
        if observe_first:
            s.service.observe_candidate()
        run = prepare(s)
        if not observe_first:
            s.service.observe_candidate()
        assert not run.used and s.service.observation_attempted
        assert s.journal.machine.state.phase == "candidate_idle"
        services.publish(s, "cancel_idle")

    assert at_idle(s, action).phase == "complete"
    assert len(s.collected) == 1 and not s.endpoint.closed


def test_no_preparation_outside_original_loop(launch_fixture):
    s = launch_fixture
    resources.denied(lambda: prepare(s))
    assert s.service.failed and not s.service.launch_preparation_attempted
    assert not s.endpoint.closed and not s.engine.sent


def test_candidate_must_already_be_prepared(launch_fixture):
    s = launch_fixture
    resources.denied(lambda: resources.at_idle(s, lambda: prepare(s)))
    assert s.service.launch_preparation_attempted and s.service.prepared_launch is None
    assert s.service.closed and not s.endpoint.closed and len(s.engine.sent) == 2


@pytest.mark.parametrize("field", ["launch_sha256", "profile_sha256"])
def test_invalid_digest_is_not_derived_from_observed_files(launch_fixture, field):
    s = launch_fixture
    resources.denied(lambda: at_idle(s, lambda: prepare(s, **{field: "invalid"})))
    assert s.service.launch_preparation_attempted and s.service.prepared_launch is None
    assert not s.endpoint.closed and s.service.closed and s.witness.closed
    assert len(s.engine.sent) == 2 and s.journal.machine.state.launch_intent_sha256 is None


@pytest.mark.parametrize("fault", ["closed_endpoint", "bad_endpoint", "finish", "expired"])
def test_failed_preconditions_cannot_prepare_or_dispatch(launch_fixture, monkeypatch, fault):
    s = launch_fixture

    def action():
        if fault == "closed_endpoint":
            s.endpoint.closed = True
        elif fault == "bad_endpoint":
            s.endpoint = object()
        elif fault == "finish":
            services.publish(s, "cancel_idle")
            assert s.inbox.consume()
        else:
            services.expire(s, monkeypatch)
        prepare(s)

    resources.denied(lambda: at_idle(s, action))
    assert s.service.closed and s.witness.closed and len(s.engine.sent) == 2
    assert s.journal.machine.state.launch_intent_sha256 is None


def test_second_preparation_is_consumed_without_replacement(launch_fixture):
    s, first = launch_fixture, []

    def action():
        first.append(prepare(s))
        prepare(s)

    resources.denied(lambda: at_idle(s, action))
    assert first[0] is s.service.prepared_launch and first[0].closed
    assert not s.endpoint.closed and len(s.engine.sent) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "prepared_launch",
        "endpoint",
        "pins",
        "command",
        "launch_sha256",
        "profile_sha256",
        "plan",
        "projected",
        "journal",
        "idle",
        "witness",
        "read",
        "qualify",
        "used",
        "failed",
        "closed",
        "confirm_attempted",
        "action",
        "claim",
        "client",
        "ready",
        "probe",
        "endpoint_closed",
    ],
)
def test_changed_or_used_launch_never_falls_back_to_idle_cancellation(
    launch_fixture, monkeypatch, fault
):
    s, originals = launch_fixture, []

    def action():
        run = prepare(s)
        originals.append(run)
        # A fresh Python monkeypatch context restores invalid fixture attributes
        # before actual original cleanup. The service failure remains sticky.
        with monkeypatch.context() as patch:
            if fault == "prepared_launch":
                patch.setattr(s.service, fault, object())
            elif fault == "endpoint_closed":
                patch.setattr(s.endpoint, "closed", True)
            elif fault in ("launch_sha256", "profile_sha256"):
                patch.setattr(run, fault, "c" * 64)
            elif fault in ("used", "failed", "closed", "confirm_attempted"):
                patch.setattr(run, fault, True)
            else:
                patch.setattr(run, fault, object())
            resources.denied(lambda: s.service.observe_candidate())
        services.publish(s, "cancel_idle")
        # Catching and undoing mutation must not unpoison the original owner.

    resources.denied(lambda: at_idle(s, action))
    assert s.service.failed and s.service.closed and originals[0].closed
    assert not s.endpoint.closed and s.witness.closed and len(s.engine.sent) == 2
    assert s.journal.machine.state.phase == "candidate_idle"


@pytest.mark.parametrize("fault", ["error", "interrupt", "delayed", "journal_changed"])
def test_constructor_and_final_recheck_failures_preserve_custody_and_no_retry(
    launch_fixture, monkeypatch, fault
):
    s, created = launch_fixture, []

    def construct(self, *args, **kwargs):
        if fault == "error":
            raise OSError("PRIVATE constructor failure")
        if fault == "interrupt":
            raise KeyboardInterrupt()
        REAL_LAUNCH_INIT(self, *args, **kwargs)
        created.append(self)
        if fault == "delayed":
            before = time.monotonic()
            monkeypatch.setattr(m.time, "monotonic", lambda: before + 3)
        else:
            services.publish(s, "cancel_idle")
            assert s.inbox.consume()

    monkeypatch.setattr(m.launch.Launch, "__init__", construct)
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            at_idle(s, lambda: prepare(s))
    else:
        resources.denied(lambda: at_idle(s, lambda: prepare(s)))
    assert s.service.closed and s.witness.closed and not s.endpoint.closed
    assert len(s.engine.sent) == 2 and all(run.closed for run in created)
    resources.denied(lambda: prepare(s))


def test_foreign_thread_cannot_prepare_launch(launch_fixture):
    s, errors = launch_fixture, []

    def action():
        def other_thread():
            try:
                prepare(s)
            except m.UnconfirmedOperator as error:
                errors.append(str(error))

        worker = Thread(target=other_thread)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive() and errors == [m.MESSAGE]

    resources.denied(lambda: at_idle(s, action))
    assert s.service.failed and s.service.closed and s.witness.closed
    assert not s.service.launch_preparation_attempted and not s.endpoint.closed


def test_reentrant_preparation_consumes_attempt_and_cannot_hide_refusal(
    launch_fixture, monkeypatch
):
    s = launch_fixture
    original_check = s.endpoint.check

    def check():
        original_check()
        resources.denied(lambda: prepare(s))

    monkeypatch.setattr(s.endpoint, "check", check)
    resources.denied(lambda: at_idle(s, lambda: prepare(s)))
    assert s.service.failed and s.service.closed and s.witness.closed
    assert s.service.prepared_launch.closed and not s.endpoint.closed
    assert len(s.engine.sent) == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_launch_cleanup_runs_before_owned_idle_closure(launch_fixture, monkeypatch, cancel):
    s, order = launch_fixture, []
    original_close = m.launch.Launch.close

    def close(run):
        assert not s.candidate.closed and not s.candidate.idle.closed
        # Completed recovery already closes its original process tracker.
        # Interrupted idle closure still holds it until all new readers close.
        assert s.witness.closed is cancel
        assert s.service.original.recheck() is s.plan and s.journal.fd >= 0
        order.append("launch")
        original_close(run)

    monkeypatch.setattr(m.launch.Launch, "close", close)

    def action():
        prepare(s)
        if cancel:
            services.publish(s, "cancel_idle")
        else:
            raise KeyboardInterrupt()

    if cancel:
        assert at_idle(s, action).phase == "complete"
    else:
        with pytest.raises(KeyboardInterrupt):
            at_idle(s, action)
    assert order == ["launch"] and s.witness.closed and s.candidate.closed
    assert not s.endpoint.closed


def test_closure_failure_still_releases_remaining_original_owned_resources(
    launch_fixture, monkeypatch
):
    s = launch_fixture
    original_close = m.launch.Launch.close

    def close(run):
        original_close(run)
        raise OSError("PRIVATE closure failure")

    monkeypatch.setattr(m.launch.Launch, "close", close)

    def action():
        prepare(s)
        services.publish(s, "cancel_idle")

    resources.denied(lambda: at_idle(s, action))
    assert s.service.closed and s.candidate.closed and s.candidate.idle.closed
    assert s.witness.closed and s.service.inbox.closed
    assert s.service.failed and not s.endpoint.closed and len(s.engine.sent) == 4
    assert s.journal.fd >= 0


def test_replaced_public_launch_attribute_cannot_redirect_original_cleanup(launch_fixture):
    s, runs = launch_fixture, []

    def action():
        runs.append(prepare(s))
        s.service.prepared_launch = object()

    resources.denied(lambda: at_idle(s, action))
    assert runs[0].closed and s.witness.closed and s.candidate.closed
    assert not s.endpoint.closed and len(s.engine.sent) == 2
