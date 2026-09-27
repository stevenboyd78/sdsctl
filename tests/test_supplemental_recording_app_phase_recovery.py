"""Actual App phase/start/finalized readers and its original recovery session.

The session exists BEFORE recording begin and retains the same process tracker,
dispatcher, executor, journal and deadline throughout. The outer AppService shell,
native completion/file/exit facts and HA/Engine I/O remain explicitly synthetic.
This composes the recording-to-recovery seam, NOT the full startup/service driver
or an installed native lifetime. No scanner or live App is contacted.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_app_phase_composition as phases
from . import test_supplemental_recording_finalized_recovery as platform

r, m = phases.r, phases.m
candidate, app, native, launch_case, execution, joined = (
    phases.candidate,
    phases.app,
    phases.native,
    phases.launch_case,
    phases.execution,
    phases.joined,
)
layout, image_umask, supervised, image, configured = (
    phases.layout,
    phases.image_umask,
    phases.supervised,
    phases.image,
    phases.configured,
)
composed, finalizing = phases.composed, phases.finalizing
pytestmark = pytest.mark.parametrize("candidate", ["app_native"], indirect=True)


@pytest.fixture
def phase_session(joined, monkeypatch):
    s = joined

    def unavailable():
        raise ValueError("No phase observation yet")

    def clock():
        return m.operator.plans.clock.read().boottime_ns / m.operator.plans.clock.NS

    # Only the platform fixture requires this initial reader; start_recording
    # installs the actual phase's unavailable reader before beginning.
    c = SimpleNamespace(s=s, host=SimpleNamespace(read=unavailable), platform_patches=[])
    s.cycle = c
    # Keep live launch/begin metadata until finalization. The returned session
    # already exists, but exited-platform facts cannot replace live qualification.
    deferred = SimpleNamespace(setattr=lambda *args: c.platform_patches.append(args))
    yield from (item.session for item in platform.setup_cycling(c, deferred, read_clock=clock))


@pytest.fixture
def recovering(finalizing, monkeypatch):
    s, c = finalizing, finalizing.cycle
    p = s.driver.recording
    assert p.objects[-1] is s.driver.session is c.session
    c.original_session = c.session
    c.owners = c.session.processes, c.session.dispatch, c.session.executor
    c.clock_callbacks = c.session.processes.read_clock, c.session.dispatch.now
    c.deadline = s.journal.machine.hard_deadline
    read_clock = m.operator.plans.clock.read

    def expire():
        # Advance the underlying test clock, never replace owner callbacks.
        def late():
            observed = read_clock()
            delta = int((c.deadline + 1) * m.operator.plans.clock.NS) - observed.boottime_ns
            return replace(
                observed,
                before_ns=observed.before_ns + delta,
                after_ns=observed.after_ns + delta,
                boottime_ns=observed.boottime_ns + delta,
            )

        monkeypatch.setattr(m.operator.plans.clock, "read", late)

    c.expire = expire
    c.plan_bytes, c.start = s.plan.raw, p.start_attempt
    c.operator = p.operator
    c.init_exited, c.file_bad = True, False
    c.file_reads = []
    s.driver.processes = c.session.processes
    assert s.driver.finish_recording()
    for args in c.platform_patches:
        monkeypatch.setattr(*args)
    c.reader = p.reader
    c.publication, c.closed_ledger = c.reader.publication, s.ledger.state

    def exited(role):
        assert role == "init"
        return c.init_exited

    def read():
        # Explicit file-result boundary, not a substitute for native WAV proof.
        files = c.reader.files
        files._context()
        assert files.used and s.run.ready.closed and c.operator.done
        c.file_reads.append(True)
        if c.file_bad:
            files.failed = True
            raise ValueError("PRIVATE changed file")
        return files.completion.collected

    monkeypatch.setattr(c.operator, "_exited", exited)
    monkeypatch.setattr(c.reader.files, "read", read, raising=False)
    yield c


def poll(c, wait=None):
    return c.s.driver.recording.poll(c.wait if wait is None else wait)


def assert_owners(c):
    p, s = c.s.driver.recording, c.s
    assert c.session is c.original_session is s.driver.session is p.objects[-1]
    assert c.owners == (c.session.processes, c.session.dispatch, c.session.executor)
    assert c.clock_callbacks[0] is c.session.processes.read_clock
    assert c.clock_callbacks[1] is c.session.dispatch.now
    assert s.journal.machine.hard_deadline == c.deadline
    assert s.plan.raw == c.plan_bytes and p.start_attempt is c.start
    assert p.reader is c.reader and c.reader.publication is c.publication
    assert s.ledger.state is c.closed_ledger


def test_phase_enters_actual_original_session_and_requires_new_normal_health(recovering):
    c = recovering
    assert poll(c).phase == "complete"
    assert c.s.driver.recording.recovery_attempted and c.reader.recovery_attempted
    assert c.created == [
        platform.h.CONTROL["stopping_candidate"],
        platform.h.CONTROL["starting_normal"],
    ]
    assert len(c.started) == len(set(c.started)) == 2
    assert c.native_calls == ["e" * 64] and c.file_reads
    assert c.s.journal.machine.state.recording_outcome == "verified"
    assert c.session.processes.closed and not c.reader.closed and not c.operator.closed
    assert_owners(c)


def test_phase_waits_for_independent_init_exit_without_refreshing_ready(recovering, monkeypatch):
    c = recovering
    c.init_exited = False

    def obsolete(*args, **kwargs):
        pytest.fail("Finalized phase must not refresh expired readiness")

    monkeypatch.setattr(c.s.run, "_app_context", obsolete)
    monkeypatch.setattr(type(c.s.run.qualify), "__call__", obsolete)
    monkeypatch.setattr(r.launch.received.Ready, "check_before_begin", obsolete)
    for _ in range(2):
        assert poll(c).phase == "candidate_running"
        assert not c.created and not c.s.driver.recording.recovery_attempted
    c.init_exited = True
    assert poll(c).phase == "complete"
    assert_owners(c)


def test_phase_lost_stop_reply_is_inspected_without_replay(recovering):
    c = recovering
    c.stop_return_lost = True
    assert poll(c).phase == "complete"
    assert len(c.created) == len(c.started) == len(set(c.started)) == 2
    assert_owners(c)


def test_phase_lost_inspection_expires_same_session_without_restore(recovering):
    c = recovering
    c.inspect_lost = True
    assert poll(c, lambda _: c.expire()).phase == "review"
    assert c.created == [platform.h.CONTROL["stopping_candidate"]]
    assert not c.native_calls
    assert_owners(c)


@pytest.mark.parametrize("values", [(False, False), (None, None), (True, True)])
def test_phase_does_not_infer_restored_health_from_finished_recording(recovering, values):
    c = recovering
    c.native_values = values

    def wait(seconds):
        c.wait(seconds)
        if c.native_calls:
            c.expire()

    assert poll(c, wait).phase == "review"
    assert len(c.created) == len(c.started) == 2 and c.native_calls == ["e" * 64]
    assert_owners(c)


@pytest.mark.parametrize("after_stop", [False, True])
def test_phase_failed_file_read_is_sticky_and_never_uses_pristine_fallback(recovering, after_stop):
    c = recovering
    c.file_bad = not after_stop

    def wait(seconds):
        if after_stop and not c.waits:
            c.file_bad = True
            c.wait(seconds)
        else:
            c.expire()

    assert poll(c, wait).phase == "review"
    assert c.reader.failed and not c.native_calls
    assert len(c.created) == len(c.started) == int(after_stop)
    assert c.s.journal.machine.state.recording_outcome == "unconfirmed"
    assert c.s.driver.recording.abandon_attempted is False
    assert_owners(c)


def test_recovery_wait_cannot_substitute_phase_session(recovering):
    c = recovering

    def replace_session(_):
        c.s.driver.session = SimpleNamespace(read=lambda: pytest.fail("not original"))

    with pytest.raises(m.operator.UnconfirmedOperator):
        poll(c, replace_session)
    assert len(c.created) == 1 and not c.native_calls
    assert c.session.processes.closed
    c.s.driver.session = c.original_session  # Original cleanup only, never a retry.
    assert_owners(c)


def test_missing_init_exit_leaves_original_clock_expiry_available(recovering):
    c = recovering
    c.init_exited = False
    assert poll(c).phase == "candidate_running"
    c.expire()
    assert poll(c).phase == "review"
    assert not c.s.driver.recording.recovery_attempted and not c.reader.recovery_attempted
    assert not c.created and not c.native_calls
    assert c.s.journal.machine.state.recording_outcome == "unconfirmed"
    assert_owners(c)


def test_completed_phase_cannot_dispatch_recovery_twice(recovering):
    c = recovering
    assert poll(c).phase == "complete"
    with pytest.raises(m.operator.UnconfirmedOperator):
        poll(c)
    assert len(c.created) == len(c.started) == 2
    assert_owners(c)
