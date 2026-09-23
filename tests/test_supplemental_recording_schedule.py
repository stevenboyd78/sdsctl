"""Deterministic schedule and real recorder/file checks; no hardware or live assembly."""

import importlib.util
import os
import sys
import time
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest

from sds200.daemon_recording import DaemonRecordingManager

from .test_supplemental_recording_api import a
from .test_supplemental_recording_monitor import m
from .test_supplemental_recording_owner import native as native
from .test_supplemental_recording_owner import o

NAME = "supplemental_recording_schedule"
SPEC = importlib.util.spec_from_file_location(NAME, Path(o.__file__).with_name(NAME + ".py"))
s = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = s
SPEC.loader.exec_module(s)


@pytest.fixture
def scheduled(native, monkeypatch):
    owner = native.build()
    api = a.FiniteRecordingApi(native.runtime, recording_manager=native.manager)
    # Qualify expected local creation mode without changing the process umask.
    probe = native.journal.parent / "mode-probe"
    probe.touch(mode=0o666)
    mode = probe.stat().st_mode & 0o777
    writer = m.Writer(os.geteuid(), os.getegid(), mode)
    schedule = s.FiniteRecordingSchedule(owner, api, writer)
    cancel = Event()
    waits = []

    def wait(seconds):
        assert 0 < seconds <= s.POLL_SECONDS
        waits.append(seconds)
        native.clock.value += seconds
        native.wall.value += timedelta(seconds=seconds)
        native.runtime.router.submit_pcm(b"\x12\x34" * 160)
        return cancel.is_set()

    monkeypatch.setattr(cancel, "wait", wait)
    native.schedule, native.cancel, native.waits = schedule, cancel, waits
    return native


def refusal(action):
    with pytest.raises(s.UnconfirmedSchedule) as caught:
        action()
    assert str(caught.value) == s.MESSAGE and caught.value.__suppress_context__


def test_fixed_stop_joins_owner_monitor_api_and_final_content_verifier(scheduled):
    n = scheduled
    result = n.schedule.run(n.cancel)
    assert n.clock.value == n.plan.stop_at and sum(n.waits) == 64
    assert result.active_observations == 64 and result.last_observation.publication == "published"
    assert result.artifact.samples == 160 * len(n.waits)
    assert n.schedule.phase == "verified" and n.schedule.owner.phase == "stopped"
    assert n.runtime.running and n.runtime.attach_calls == n.runtime.detach_calls == 1
    assert n.plan.stop_at == 164 and n.plan.finish_by == 170
    with pytest.raises(FrozenInstanceError):
        result.active_observations = 1000
    refusal(lambda: n.schedule.run(n.cancel))
    assert n.schedule.phase == "verified"
    assert n.runtime.attach_calls == n.runtime.detach_calls == 1


def test_default_schedule_cannot_silently_gain_an_unscheduled_reader(scheduled):
    scheduled.schedule.api._acquisition_binding_attempted = True
    refusal(lambda: scheduled.schedule.run(scheduled.cancel))
    assert scheduled.runtime.attach_calls == 0
    assert scheduled.schedule.phase == "unconfirmed"


@pytest.mark.parametrize("point", ("before_start", "active", "after_stop"))
def test_cancellation_never_dispatches_an_implicit_stop_or_retry(scheduled, monkeypatch, point):
    n = scheduled
    if point == "before_start":
        n.cancel.set()
    elif point == "active":
        original = n.cancel.wait

        def wait(seconds):
            original(seconds)
            n.cancel.set()

        monkeypatch.setattr(n.cancel, "wait", wait)
    else:
        original = n.schedule.owner.stop

        def stopped():
            value = original()
            n.cancel.set()
            return value

        monkeypatch.setattr(n.schedule.owner, "stop", stopped)
    refusal(lambda: n.schedule.run(n.cancel))
    assert n.schedule.phase == "unconfirmed"
    assert n.runtime.attach_calls == (0 if point == "before_start" else 1)
    assert n.runtime.detach_calls == (1 if point == "after_stop" else 0)
    n.cancel.clear()
    refusal(lambda: n.schedule.run(n.cancel))
    refusal(lambda: s.FiniteRecordingSchedule(n.schedule.owner, n.schedule.api, n.schedule.writer))


@pytest.mark.parametrize(
    "replacement", ("plan", "manager", "baseline", "api_runtime", "api_manager")
)
def test_rebinding_during_the_wait_never_extends_or_restarts_a_case(
    scheduled, monkeypatch, replacement
):
    n = scheduled
    original = n.cancel.wait

    def wait(seconds):
        original(seconds)
        if replacement == "plan":
            monkeypatch.setattr(n.schedule.owner, "plan", replace(n.plan, stop_at=165))
        elif replacement == "baseline":
            monkeypatch.setattr(n.schedule.owner, "baseline", replace(n.baseline))
        elif replacement == "manager":
            monkeypatch.setattr(n.schedule.owner, "manager", object())
        else:
            monkeypatch.setattr(
                n.schedule.api,
                "runtime" if replacement == "api_runtime" else "recording_manager",
                object(),
            )

    monkeypatch.setattr(n.cancel, "wait", wait)
    refusal(lambda: n.schedule.run(n.cancel))
    assert n.runtime.attach_calls == 1 and n.runtime.detach_calls == 0
    assert n.clock.value < n.plan.stop_at


@pytest.mark.parametrize("fault", ("monitor", "active_state", "old_file", "clock_back", "deadline"))
def test_uncertain_progress_is_not_a_success_or_replay_authority(scheduled, monkeypatch, fault):
    n = scheduled
    original = n.cancel.wait

    def wait(seconds):
        original(seconds)
        if fault == "monitor":

            def fail(*_args, **_kwargs):
                raise OSError("PRIVATE_IO_FAILURE")

            monkeypatch.setattr(s, "observe", fail)
        elif fault == "active_state":
            n.manager.stop_recording()  # Competing in-process mutator, not an API grant.
        elif fault == "old_file":
            (n.root / "older.wav").write_bytes(b"changed")
        elif fault == "clock_back":
            n.clock.value = 1
        else:
            n.clock.value = n.plan.finish_by

    monkeypatch.setattr(n.cancel, "wait", wait)
    refusal(lambda: n.schedule.run(n.cancel))
    assert n.schedule.phase == "unconfirmed" and n.runtime.attach_calls == 1
    assert not (n.journal / "stop-intent.json").exists()
    refusal(lambda: n.schedule.run(n.cancel))


@pytest.mark.parametrize("step", ("observation", "stop", "verification"))
def test_slow_calls_cannot_move_the_fixed_finish_deadline(scheduled, monkeypatch, step):
    n = scheduled
    target, attribute = (
        (s, "observe")
        if step == "observation"
        else ((n.schedule.owner, "stop") if step == "stop" else (s, "verify_finalized"))
    )
    original = getattr(target, attribute)

    def late(*args, **kwargs):
        value = original(*args, **kwargs)
        n.clock.value = n.plan.finish_by
        return value

    monkeypatch.setattr(target, attribute, late)
    refusal(lambda: n.schedule.run(n.cancel))
    assert n.plan.finish_by == 170 and n.schedule.phase == "unconfirmed"
    assert n.runtime.detach_calls == (0 if step == "observation" else 1)


def test_concurrent_run_does_not_start_another_writer(scheduled, monkeypatch):
    n = scheduled
    entered, release = Event(), Event()
    original = n.schedule.owner.start
    errors = []

    def held():
        entered.set()
        assert release.wait(2)
        return original()

    def run():
        try:
            n.schedule.run(n.cancel)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(n.schedule.owner, "start", held)
    worker = Thread(target=run)
    worker.start()
    try:
        assert entered.wait(2)
        with pytest.raises(s.UnconfirmedSchedule):
            n.schedule.run(n.cancel)
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors
    assert n.runtime.attach_calls == n.runtime.detach_calls == 1


def test_invalid_cancellation_consumes_the_run_before_start(scheduled):
    refusal(lambda: scheduled.schedule.run(object()))
    refusal(lambda: scheduled.schedule.run(scheduled.cancel))
    assert scheduled.runtime.attach_calls == 0


def test_interrupted_run_closes_controller_without_stopping_writer(scheduled, monkeypatch):
    n = scheduled

    def interrupted(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(n.cancel, "wait", interrupted)
    with pytest.raises(KeyboardInterrupt):
        n.schedule.run(n.cancel)
    assert n.schedule.phase == "unconfirmed" and n.schedule.owner.phase == "closed"
    assert n.runtime.attach_calls == 1 and n.runtime.detach_calls == 0
    assert n.manager.snapshot().active
    refusal(lambda: n.schedule.run(n.cancel))


def test_late_final_observation_does_not_begin_another_expensive_read(scheduled, monkeypatch):
    n = scheduled
    original = s.observe

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["stage"] == "finalizing":
            n.clock.value = n.plan.finish_by
        return result

    def forbidden(*_args, **_kwargs):
        pytest.fail("Final verification must not start after deadline")

    monkeypatch.setattr(s, "observe", slow)
    monkeypatch.setattr(s, "verify_finalized", forbidden)
    refusal(lambda: n.schedule.run(n.cancel))
    assert n.schedule.owner.phase == "closed"
    assert n.runtime.attach_calls == n.runtime.detach_calls == 1


def test_real_monotonic_waits_finalize_without_a_manual_stop(native):
    n = native
    manager = DaemonRecordingManager(
        n.runtime,
        n.root,
        template=o.template(n.case),
        clock=time.monotonic,
        now=lambda: datetime.now(UTC),
    )
    now = time.monotonic()
    plan = replace(
        n.plan, prepared_at=now, start_by=now + 0.75, stop_at=now + 1.5, finish_by=now + 7.5
    )
    owner = o.FiniteRecordingOwner(manager, n.baseline, plan, n.journal)
    api = a.FiniteRecordingApi(n.runtime, recording_manager=manager)
    probe = n.journal.parent / "real-mode-probe"
    probe.touch(mode=0o666)
    writer = m.Writer(os.geteuid(), os.getegid(), probe.stat().st_mode & 0o777)
    schedule = s.FiniteRecordingSchedule(owner, api, writer)
    done = Event()

    def feed():
        while not done.wait(0.02):
            n.runtime.router.submit_pcm(b"\x12\x34" * 160)

    feeder = Thread(target=feed)
    feeder.start()
    try:
        result = schedule.run(Event())
        assert plan.stop_at <= time.monotonic() < plan.finish_by
        assert result.artifact.samples > 0 and result.active_observations >= 1
        assert manager.snapshot().status.value == "stopped"
        assert n.runtime.running and n.runtime.attach_calls == n.runtime.detach_calls == 1
    finally:
        done.set()
        feeder.join(2)
        owner.close()
        manager.close()
    assert not feeder.is_alive()
