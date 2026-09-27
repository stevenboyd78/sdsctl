"""Real observer kernel handles; synthetic Docker metadata, no live Apps.

Owned harmless subprocesses stand in for the helper. Linux pidfds, namespaces,
BOOTTIME timerfds and signals to those fixture children are real. One test
shortens only the test plan's total duration to exercise actual timer expiry.
These tests are not installed supervision or App/native recovery evidence.
"""

import importlib.util
import json
import os
import select
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_service_clock_link as clock_tests

NAME = "supplemental_recording_service_deadline"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(clock_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
prepared = clock_tests.prepared


def denied(action):
    with pytest.raises(m.UnconfirmedDeadline) as error:
        action()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


@contextmanager
def original_link(prepared, monkeypatch, *, short=False):
    monkeypatch.setattr(m.links.domains, "ROOT_UID", os.geteuid())
    plan = prepared.plan
    if short:
        # Local test policy only. The production 1500-second hard deadline is
        # unchanged; no installed plan or old case is renewed or reused.
        monkeypatch.setattr(m.links.plans.base, "TOTAL_SECONDS", 6)
        origin = m.links.plans.clock.read()
        issued = origin.boottime_ns / m.links.plans.clock.NS
        # Use the existing complete plan fixture's schema, not the template's
        # integer-budget protocol (which deliberately refuses these values).
        supplied = json.loads(plan.raw)
        supplied["candidate"]["contract"]["maximum_recording_seconds"] = 1
        supplied["original_clock"] = dict(
            boot=origin.boot,
            namespace=list(origin.namespace),
            before_ns=origin.before_ns,
            after_ns=origin.after_ns,
            boottime_ns=origin.boottime_ns,
        )
        supplied["deadlines"] = dict(
            issued_at=issued, ready_by=issued + 1, stop_by=issued + 5, recover_by=issued + 6
        )
        plan = m.links.plans.decode(supplied)
    observer = m.links.plans.clock.ClockWitness(plan.original_clock)
    domain = m.links.domains.ZeroDomain(observer.original, prepared.witness)
    link = None
    try:
        link = m.links.ObserverClock(plan, observer, domain, prepared.identity)
        yield link
    finally:
        if link is not None:
            link.close()
        domain.close()
        observer.close()


@pytest.fixture
def case(prepared, monkeypatch):
    with original_link(prepared, monkeypatch) as link:
        watch = m.DeadlineWatch(link)
        try:
            yield prepared, link, watch
        finally:
            watch.close()


def test_capture_retains_original_bound_handles_and_never_closes_borrowed_owners(case):
    prepared, link, watch = case
    assert watch.plan is link.plan and watch.target is link.target
    assert watch.poll() == m.Status(False, False)
    assert watch.helper_fd != link.domain.pidfd
    assert watch.host_fd != link.domain.handles[os.getpid()][0]
    assert watch.target_fd != link.domain.handles[prepared.child.pid][0]
    assert link.deadline_capture_attempted
    assert all(not os.get_inheritable(fd) for fd, _ in watch._owned)
    handles = tuple(fd for fd, _ in watch._owned)
    watch.close()
    watch.close()
    for fd in handles:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert not prepared.witness.exited()
    assert not link.closed and not link.domain.closed and not link.observer.closed
    assert link.read().namespace == link.local_original.namespace
    denied(watch.poll)
    denied(lambda: m.DeadlineWatch(link))


def test_helper_exit_survives_all_original_startup_handles_closing(case, monkeypatch):
    prepared, link, watch = case
    link.close()
    link.domain.close()
    link.observer.close()
    prepared.witness.close()
    prepared.child.stdin.close()
    assert prepared.child.wait(timeout=2) == 0
    original_open, original_stat = os.open, os.stat

    def no_reopen(path, *args, **kwargs):
        assert not str(path).startswith(f"/proc/{prepared.child.pid}/")
        return original_open(path, *args, **kwargs)

    def no_stat(path, *args, **kwargs):
        assert not str(path).startswith(f"/proc/{prepared.child.pid}/")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", no_reopen)
    monkeypatch.setattr(os, "stat", no_stat)
    monkeypatch.setattr(link, "read", lambda: pytest.fail("expired startup link reused"))
    monkeypatch.setattr(
        m.links.domains.process, "read_identity", lambda *_: pytest.fail("PID reopened")
    )
    assert watch.poll() == m.Status(True, False)
    assert watch.poll() == m.Status(True, False)


def test_actual_boottime_deadline_is_independent_of_frozen_helper(prepared, monkeypatch):
    with original_link(prepared, monkeypatch, short=True) as link:
        watch = m.DeadlineWatch(link)
        try:
            original = link.plan.raw, watch.deadline_ns
            signal.pidfd_send_signal(watch.helper_fd, signal.SIGSTOP)
            assert watch.poll() == m.Status(False, False)
            # There is no running Python clock callback or service tick here.
            # The kernel itself makes the original absolute timer readable.
            assert select.select([watch.timer_fd], [], [], 7)[0] == [watch.timer_fd]
            assert watch.poll() == m.Status(False, True)
            assert watch.poll() == m.Status(False, True)  # The expiry is not consumed.
            assert (link.plan.raw, watch.deadline_ns) == original
        finally:
            signal.pidfd_send_signal(watch.helper_fd, signal.SIGCONT)
            watch.close()


def test_fatal_helper_exit_uses_the_preexisting_pidfd_without_recapture(prepared, monkeypatch):
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys; print('ready',flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    witness = None
    try:
        assert select.select([child.stdout], [], [], 3)[0]
        assert child.stdout.readline() == b"ready\n"
        process = m.links.domains.process

        def identity(pid, cid):
            return process.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )

        monkeypatch.setattr(process, "read_identity", identity)
        target = identity(child.pid, "b" * 64)
        witness = process.ProcessWitness(target)
        fixture = SimpleNamespace(plan=prepared.plan, witness=witness, identity=target)
        with original_link(fixture, monkeypatch) as link:
            watch = m.DeadlineWatch(link)
            try:
                signal.pidfd_send_signal(watch.helper_fd, signal.SIGKILL)
                assert child.wait(timeout=3) == -signal.SIGKILL
                link.close()
                link.domain.close()
                witness.close()
                assert watch.poll() == m.Status(True, False)
            finally:
                watch.close()
    finally:
        if child.poll() is None:
            child.kill()  # Only this fixture's exact owned, unreaped child.
        child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()
        if witness is not None:
            witness.close()


@pytest.mark.parametrize("state", ["closed", "failed"])
def test_capture_after_startup_retirement_is_refused_without_acquiring_handles(
    prepared, monkeypatch, state
):
    with original_link(prepared, monkeypatch) as link:
        before = len(os.listdir("/proc/self/fd"))
        setattr(link, state, True)
        denied(lambda: m.DeadlineWatch(link))
        assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
def test_partial_handle_failure_is_consumed_without_leaks(prepared, monkeypatch, stage):
    with original_link(prepared, monkeypatch) as link:
        before = len(os.listdir("/proc/self/fd"))
        count, real = [0], m.DeadlineWatch._own

        def fail(watch, fd):
            result = real(watch, fd)
            count[0] += 1
            if count[0] == stage:
                raise OSError("PRIVATE ACQUISITION DETAIL")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(m.DeadlineWatch, "_own", fail)
            denied(lambda: m.DeadlineWatch(link))
        assert len(os.listdir("/proc/self/fd")) == before
        assert link.deadline_capture_attempted
        denied(lambda: m.DeadlineWatch(link))
        assert not prepared.witness.exited() and link.read()


def test_exit_during_capture_cannot_be_reported_as_successful_capture(prepared, monkeypatch):
    with original_link(prepared, monkeypatch) as link:
        before = len(os.listdir("/proc/self/fd"))
        real = os.timerfd_settime_ns

        def exited(*args, **kwargs):
            result = real(*args, **kwargs)
            prepared.child.stdin.close()
            assert prepared.child.wait(timeout=2) == 0
            return result

        monkeypatch.setattr(os, "timerfd_settime_ns", exited)
        denied(lambda: m.DeadlineWatch(link))
        assert link.deadline_capture_attempted and link.failed
        assert len(os.listdir("/proc/self/fd")) == before - 1  # Closed fixture stdin.


def test_capture_refuses_a_live_pidfd_for_a_different_process(prepared, monkeypatch):
    with original_link(prepared, monkeypatch) as link:
        original = link.domain.pidfd
        foreign = os.pidfd_open(os.getpid())
        try:
            link.domain.pidfd = foreign
            # Existing live checks of the named child must not disguise that
            # the separately retained descriptor actually names the observer.
            assert link.read()
            before = len(os.listdir("/proc/self/fd"))
            denied(lambda: m.DeadlineWatch(link))
            assert len(os.listdir("/proc/self/fd")) == before
            assert link.deadline_capture_attempted and os.fstat(foreign)
        finally:
            link.domain.pidfd = original
            os.close(foreign)


@pytest.mark.parametrize("fault", ["disarm", "earlier", "later", "periodic"])
def test_timer_rearming_or_disarming_is_not_a_deadline_renewal(case, fault):
    _, _, watch = case
    initial = (
        watch.deadline_ns
        + {"disarm": 0, "earlier": -1_000_000_000, "later": 1_000_000_000, "periodic": 0}[fault]
    )
    if fault == "disarm":
        initial = 0
    os.timerfd_settime_ns(
        watch.timer_fd,
        flags=os.TFD_TIMER_ABSTIME,
        initial=initial,
        interval=1_000_000_000 if fault == "periodic" else 0,
    )
    denied(watch.poll)
    assert watch.failed
    denied(watch.poll)


@pytest.mark.parametrize(
    "field",
    [
        "plan",
        "target",
        "pin",
        "host_namespace",
        "target_namespace",
        "deadline_ns",
        "helper_fd",
        "timer_fd",
        "_owned",
    ],
)
def test_replaced_custody_is_not_adopted(case, field):
    _, _, watch = case
    original = getattr(watch, field)
    setattr(watch, field, object())
    denied(watch.poll)
    setattr(watch, field, original)  # Only permit fixture cleanup; poison remains.
    denied(watch.poll)


@pytest.mark.parametrize("which", ["plan", "target"])
def test_frozen_data_mutation_is_not_resealed(case, which):
    _, link, watch = case
    if which == "plan":
        object.__setattr__(link.plan, "firmware", "PRIVATE MUTATION")
    else:
        object.__setattr__(link.target, "start_ticks", link.target.start_ticks + 1)
    denied(watch.poll)


def test_foreign_thread_cannot_poll_or_close_owned_handles(case):
    _, _, watch = case
    errors = []

    def other():
        for action in (watch.poll, watch.close):
            try:
                action()
            except BaseException as error:
                errors.append(error)

    worker = Thread(target=other)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive() and len(errors) == 2
    assert all(type(error) is m.UnconfirmedDeadline for error in errors)
    assert watch.failed and not watch.closed
    denied(watch.poll)


def test_clock_link_still_refuses_after_original_ready_deadline(case, monkeypatch):
    _, link, watch = case
    monkeypatch.setattr(m.links.time, "monotonic", lambda: link.plan.lease["ready_by"])
    clock_tests.denied(link.read)
    assert watch.poll() == m.Status(False, False)
    assert link.failed  # No weakening of the original startup-only verifier.


def test_replaced_timer_clock_is_not_accepted_even_with_matching_remaining_time(case):
    _, _, watch = case
    import time

    foreign = os.timerfd_create(time.CLOCK_MONOTONIC, flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK)
    try:
        now_boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        now_mono = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        os.timerfd_settime_ns(
            foreign,
            flags=os.TFD_TIMER_ABSTIME,
            initial=watch.deadline_ns - now_boot + now_mono,
        )
        # Linux timerfds share an anonymous inode; this is the reason for the
        # additional clock-id check rather than assuming fstat alone is enough.
        assert m._identity(foreign) == m._identity(watch.timer_fd)
        os.dup2(foreign, watch.timer_fd, inheritable=False)
        denied(watch.poll)
    finally:
        os.close(foreign)


def test_fd_metadata_failure_does_not_close_caller_handles(case, monkeypatch):
    prepared, _, watch = case
    import builtins

    real = builtins.open

    def failed(path, *args, **kwargs):
        if path == f"/proc/self/fdinfo/{watch.timer_fd}":
            raise OSError("PRIVATE metadata failure")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", failed)
    denied(watch.poll)
    assert watch.failed and not prepared.witness.exited()


@pytest.mark.parametrize("fault", ["POLLNVAL", "POLLERR", "POLLHUP"])
def test_pidfd_error_is_never_promoted_to_exit(case, monkeypatch, fault):
    _, _, watch = case
    original = m._readable

    def bad(fd):
        if fd != watch.helper_fd:
            return original(fd)

        class Poll:
            def register(self, current, events):
                assert current == fd and events == select.POLLIN

            def poll(self, timeout):
                assert timeout == 0
                return [(fd, getattr(select, fault))]

        with monkeypatch.context() as patch:
            patch.setattr(select, "poll", Poll)
            return original(fd)

    monkeypatch.setattr(m, "_readable", bad)
    denied(watch.poll)


def test_original_namespace_change_is_not_accepted(case, monkeypatch):
    _, _, watch = case
    from types import SimpleNamespace

    real = os.stat

    def replaced(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if path == "/proc/self/ns/time_for_children":
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
        return info

    monkeypatch.setattr(os, "stat", replaced)
    denied(watch.poll)


def test_initial_guard_time_counts_toward_the_same_poll_budget(case, monkeypatch):
    _, _, watch = case
    real = m.time.monotonic
    now = [real()]
    guard = watch._guard

    def slow():
        guard()
        now[0] += m.MAX_SECONDS

    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(watch, "_guard", slow)
    denied(watch.poll)


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_interrupted_poll_poison_is_preserved_without_losing_exit_handles(case, monkeypatch, kind):
    _, _, watch = case
    real = m._timer_metadata

    def interrupted(fd):
        raise kind

    monkeypatch.setattr(m, "_timer_metadata", interrupted)
    with pytest.raises(kind):
        watch.poll()
    assert watch.failed and not watch.closed and os.fstat(watch.helper_fd)
    monkeypatch.setattr(m, "_timer_metadata", real)
    denied(watch.poll)


def test_consumed_expiry_is_uncertainty_not_a_fresh_timer(prepared, monkeypatch):
    with original_link(prepared, monkeypatch, short=True) as link:
        watch = m.DeadlineWatch(link)
        try:
            assert select.select([watch.timer_fd], [], [], 7)[0]
            assert watch.poll().recovery_deadline_expired
            assert len(os.read(watch.timer_fd, 8)) == 8
            denied(watch.poll)
        finally:
            watch.close()


def test_watcher_does_not_enable_an_active_command_or_existing_profile():
    path = Path(m.__file__)
    for name in (
        "supplemental_recording_service_command",
        "supplemental_recording_host_source",
        "supplemental_recording_app_host_source",
        "qualify_supplemental_recording_service",
    ):
        assert NAME not in path.with_name(name + ".py").read_text()
    assert not hasattr(m.DeadlineWatch, "extend") and not hasattr(m.DeadlineWatch, "rearm")
