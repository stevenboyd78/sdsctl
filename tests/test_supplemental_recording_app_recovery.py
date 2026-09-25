"""Actual App owners/journals/recovery policy; synthetic HA/Engine/exit facts.

The shared platform fixture never contacts a real App. These tests exercise the
new App route using the original recovery session, not installed supervision.
"""

import importlib.util
import sys
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_app_finalized as evidence
from . import test_supplemental_recording_finalized_recovery as direct

candidate, app, native, launch_case, execution, joined, observing, completed, host = (
    evidence.candidate,
    evidence.app,
    evidence.native,
    evidence.launch_case,
    evidence.execution,
    evidence.joined,
    evidence.observing,
    evidence.completed,
    evidence.host,
)
layout, image_umask, supervised, image, configured = (
    evidence.layout,
    evidence.image_umask,
    evidence.supervised,
    evidence.image,
    evidence.configured,
)
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)
NAME = "supplemental_recording_app_recovery"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(evidence.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
begin, h = m.begin, direct.h


@pytest.fixture
def cycling(host, monkeypatch):
    # Reuse only the explicitly synthetic platform adapter. host/start/reader
    # are the actual App fixtures, not a direct Start relabeled as an App.
    yield from direct.cycling.__wrapped__(host, monkeypatch)


def recover(c, wait=None):
    return m.recover_finalized(c.reader, c.session, c.wait if wait is None else wait)


def test_original_app_session_stops_restores_and_checks_new_normal_health(cycling):
    c = cycling
    originals = c.s.plan.raw, c.s.start.intent, c.reader.publication
    owners = c.session.executor, c.session.processes, c.session.dispatch
    deadline = c.s.journal.machine.hard_deadline
    assert recover(c).phase == "complete"
    assert c.created == [h.CONTROL["stopping_candidate"], h.CONTROL["starting_normal"]]
    assert len(c.started) == len(set(c.started)) == 2
    assert c.native_calls == ["e" * 64]
    assert c.s.journal.machine.state.recording_outcome == "verified"
    assert c.s.journal.machine.hard_deadline == deadline
    assert owners == (c.session.executor, c.session.processes, c.session.dispatch)
    assert originals == (c.s.plan.raw, c.s.start.intent, c.reader.publication)
    assert c.session.processes.closed and not c.reader.closed and not c.operator.closed
    assert c.s.journal.fd >= 0 and c.reader.recovery_attempted
    with pytest.raises(begin.UnconfirmedHostBegin):
        recover(c)
    assert len(c.created) == 2


def test_lost_stop_return_is_inspected_never_reissued(cycling):
    c = cycling
    c.stop_return_lost = True
    assert recover(c).phase == "complete"
    assert c.created == [h.CONTROL["stopping_candidate"], h.CONTROL["starting_normal"]]
    assert len(c.started) == 2


def test_lost_inspection_does_not_replay_stop_or_restore(cycling):
    c = cycling
    c.inspect_lost = True
    assert recover(c, lambda _: direct.expire(c)).phase == "review"
    assert c.created == [h.CONTROL["stopping_candidate"]]
    assert not c.native_calls


def test_failed_file_read_stays_unavailable_but_original_clock_expires(cycling):
    c = cycling
    c.reader.files.bad_files = True
    assert recover(c, lambda _: direct.expire(c)).phase == "review"
    assert not c.created and not c.native_calls and c.reader.failed
    assert c.trace.count("files") == 1 and not c.operator.closed
    assert c.s.journal.machine.state.recording_outcome == "unconfirmed"


def test_failure_after_shutdown_never_falls_back_to_pristine_restore(cycling):
    c = cycling

    def wait(seconds):
        c.wait(seconds)
        if len(c.waits) == 1:
            c.reader.files.bad_files = True
        else:
            direct.expire(c)

    assert recover(c, wait).phase == "review"
    assert c.created == [h.CONTROL["stopping_candidate"]]
    assert c.s.journal.machine.process_bound(m.base.CANDIDATE, exited=True)
    assert c.reader.failed and not c.native_calls


@pytest.mark.parametrize("values", [(False, False), (None, None), (True, True)])
def test_new_normal_status_must_be_fresh_healthy_and_nonrecording(cycling, values):
    c = cycling
    c.native_values = values

    def wait(seconds):
        c.wait(seconds)
        if c.native_calls:
            direct.expire(c)

    assert recover(c, wait).phase == "review"
    assert len(c.created) == len(c.started) == 2
    assert c.native_calls == ["e" * 64]


def test_host_and_router_share_the_original_two_second_budget(cycling, monkeypatch):
    c = cycling
    original = m.finalized.AppFinalizedHost.read

    def late(host):
        result = original(host)
        evidence.observations.a.continuity_tests.advance(monkeypatch, 3)
        return result

    monkeypatch.setattr(m.finalized.AppFinalizedHost, "read", late)
    assert recover(c, lambda _: direct.expire(c)).phase == "review"
    assert not c.created and not c.native_calls
    assert not c.reader.failed


@pytest.mark.parametrize("field", ["executor", "dispatch", "processes"])
def test_original_session_owner_cannot_be_substituted(cycling, field):
    c = cycling
    original = getattr(c.session, field)
    setattr(c.session, field, object())
    with pytest.raises(begin.UnconfirmedHostBegin):
        recover(c)
    assert c.reader.recovery_attempted and not c.created and not c.native_calls
    setattr(c.session, field, original)  # Restore only fixture cleanup custody.
    with pytest.raises(begin.UnconfirmedHostBegin):
        recover(c)
    assert not c.created


def test_mid_read_dispatch_substitution_is_rejected_before_policy_observation(cycling, monkeypatch):
    c = cycling
    original = m.finalized.AppFinalizedHost.read
    original_dispatch = c.session.dispatch

    def changed(host):
        result = original(host)
        c.session.dispatch = object()
        return result

    monkeypatch.setattr(m.finalized.AppFinalizedHost, "read", changed)
    with pytest.raises(begin.UnconfirmedHostBegin):
        recover(c)
    assert not c.created and c.session.processes.closed
    assert not any(
        entry["event"]["kind"] == "observe"
        for entry in c.s.journal.entries[len(c.reader.history) + 1 :]
    )
    c.session.dispatch = original_dispatch


def test_restored_reader_rejects_before_actual_normal_start_intent(completed):
    c = completed
    evidence.publish(c)
    reader = m.AppRestoredHost(c.reader)
    evidence.denied(reader.read)
    assert reader.failed and not c.operator.closed and c.s.journal.fd >= 0
    assert c.s.journal.machine.state.phase == "candidate_running"
    reader.close()


@pytest.mark.parametrize(
    "fault", ["process_docker", "dispatch_docker", "images", "cli", "journal", "send", "operator"]
)
def test_foreign_recovery_bindings_refuse_before_any_app_action(cycling, fault):
    c = cycling
    if fault == "process_docker":
        c.session.processes.docker = m.plans.ordinary.Docker()
    elif fault == "dispatch_docker":
        c.session.dispatch.docker = m.plans.ordinary.Docker()
    elif fault == "images":
        changed = "sha256:" + "b" * 64
        assert changed != c.s.plan.normal.image
        c.session.processes.images[m.base.NORMAL] = changed
    elif fault == "cli":
        changed = "b" * 64
        assert changed != c.s.plan.cli_generation
        c.session.dispatch.generation = changed
    elif fault == "journal":
        c.session.journal = object()
    elif fault == "send":
        c.session.executor.send = lambda *args: None
    else:
        c.session.consume_operator = lambda: True
    before = tuple(c.s.journal.entries)
    with pytest.raises(begin.UnconfirmedHostBegin):
        recover(c)
    assert c.reader.recovery_attempted and tuple(c.s.journal.entries) == before
    assert not c.created and not c.samples and not c.native_calls
    assert not c.reader.closed and not c.operator.closed


def test_changed_route_during_wait_cannot_continue_with_replacement_reader(cycling):
    c = cycling

    def wait(_):
        c.session.read = lambda: pytest.fail("Substituted reader must not run")

    with pytest.raises(begin.UnconfirmedHostBegin):
        recover(c, wait)
    assert c.created == [h.CONTROL["stopping_candidate"]]
    assert c.session.processes.closed and not c.reader.closed and not c.operator.closed


def test_foreign_thread_cannot_consume_original_recovery_attempt(cycling):
    c, errors = cycling, []

    def attempt():
        try:
            recover(c)
        except Exception as error:
            errors.append(error)

    thread = Thread(target=attempt)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive() and len(errors) == 1
    assert type(errors[0]) is begin.UnconfirmedHostBegin
    assert not c.reader.recovery_attempted and not c.created


def test_unreviewed_restoration_subclass_is_not_an_app_policy(completed):
    c = completed
    evidence.publish(c)

    class Other(m.AppRestoredHost):
        pass

    evidence.denied(lambda: Other(c.reader))
    assert not c.reader.failed and not c.operator.closed
