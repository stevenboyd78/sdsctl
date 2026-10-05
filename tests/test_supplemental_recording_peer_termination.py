"""Real owned processes/pidfds/timers/signals; synthetic Engine and image facts.

No live HA/Pi/scanner/Engine access. These are NOT installed outer provenance,
active action admission, original native exits, or App recovery evidence.
"""

import importlib.util
import json
import os
import select
import signal
import socket
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

from . import test_supplemental_recording_peer_runtime_pair as pairs
from . import test_supplemental_recording_service_deadline as timer_tests
from . import test_supplemental_recording_watchdog as watchdog_tests

NAME = "supplemental_recording_peer_termination"
SPEC = importlib.util.spec_from_file_location(NAME, Path(pairs.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
assert m.deadlines is timer_tests.m and m.cleanup is watchdog_tests.m
layout, image_umask, supervised = pairs.layout, pairs.image_umask, pairs.supervised
image, configured, pair = pairs.image, pairs.configured, pairs.pair
pytestmark = pytest.mark.skipif(
    not m.deadlines.timerfd_available(), reason="Linux timerfd API required"
)


@pytest.fixture
def short_budget(request):
    return getattr(request, "param", False)


@pytest.fixture
def helper(supervised, image, configured, monkeypatch, request, short_budget):
    # The production 1500-second policy is unchanged. Only the test plan has a
    # eight-second original lifetime, BEFORE pair/template/declaration creation.
    source = pairs.helper.__wrapped__(supervised, image, configured, monkeypatch, request)
    original = next(source)
    try:
        if short_budget:
            monkeypatch.setattr(m.peers.launch.plans.base, "TOTAL_SECONDS", 8)
            raw = json.loads(original.plan.raw)
            raw["candidate"]["contract"]["maximum_recording_seconds"] = 1
            issued = raw["deadlines"]["issued_at"]
            raw["deadlines"].update(ready_by=issued + 3, stop_by=issued + 7, recover_by=issued + 8)
            original.plan = m.peers.launch.plans.decode(raw)
        yield original
    finally:
        with pytest.raises(StopIteration):
            next(source)


@pytest.fixture
def inputs(pair):
    clock = m.deadlines.links.plans.clock.ClockWitness(pair.plan.original_clock)
    domains = []
    try:
        for role in m.peers.declarations.ROLES:
            domains.append(
                m.deadlines.links.domains.ZeroDomain(clock.original, pair.witnesses[role])
            )
        yield pair.obj, clock, *domains
    finally:
        for domain in domains:
            domain.close()
        clock.close()


@pytest.fixture
def custody(inputs):
    value = m.Custody(*inputs)
    try:
        yield value
    finally:
        value.close()


def refused(action):
    with pytest.raises(m.UnconfirmedTermination) as error:
        action()
    assert str(error.value) == m.MESSAGE


def exited(pair):
    for process in pair.children.values():
        assert process.wait(timeout=2) == -signal.SIGKILL


def test_capture_is_read_only_retains_originals_and_consumes_one_slot(
    custody, inputs, pair, monkeypatch
):
    assert custody.plan is pair.plan and custody.clock is inputs[1]
    assert custody.deadline_ns == int(
        m.Decimal(pair.plan.deadlines.recover_by) * m.peers.launch.plans.clock.NS
    )
    assert all(not witness.exited() for witness in pair.witnesses.values())
    assert custody.targets != tuple(w.fd for w in pair.witnesses.values())
    assert all(pair.counts[role]["container"] == 2 for role in m.peers.declarations.ROLES)
    monkeypatch.setattr(m.os, "fork", lambda: pytest.fail("capture forked"))
    monkeypatch.setattr(m.signal, "pidfd_send_signal", lambda *_: pytest.fail("capture signaled"))
    refused(lambda: m.Custody(*inputs))
    custody.close()
    assert not inputs[1].closed and all(not domain.closed for domain in inputs[2:])
    assert all(not witness.exited() for witness in pair.witnesses.values())


@pytest.mark.parametrize(
    "scope", [None, True, "", "finite-recording-preflight-permission-v1", "ready", "f" * 64]
)
def test_no_termination_scope_inferred_from_readiness_or_permission(
    custody, pair, scope, monkeypatch
):
    monkeypatch.setattr(m.os, "fork", lambda: pytest.fail("invalid scope forked"))
    refused(lambda: m.arm(custody, scope=scope))
    assert custody.attempted and all(not witness.exited() for witness in pair.witnesses.values())
    refused(lambda: m.arm(custody, scope=m.SCOPE))


@pytest.mark.parametrize("pair", [False, "preparation"], indirect=True)
def test_arm_runs_both_full_comparisons_again_then_cancel_stops_only_original_peers(custody, pair):
    before = pair.plan.raw
    watch = m.arm(custody, scope=m.SCOPE)
    try:
        assert all(pair.counts[role]["container"] == 4 for role in m.peers.declarations.ROLES)
        assert all(not witness.exited() for witness in pair.witnesses.values())
        refused(lambda: m.arm(custody, scope=m.SCOPE))
        assert not hasattr(watch, "disarm") and not hasattr(watch, "extend")
        assert pair.plan.raw == before
        watch.close()
        exited(pair)
    finally:
        watch.close()


@pytest.mark.parametrize("role", m.peers.declarations.ROLES)
def test_actual_peer_loss_stops_partner_without_reopening_any_target(
    custody, pair, role, monkeypatch
):
    watch = m.arm(custody, scope=m.SCOPE)
    try:

        def no_discovery(*_args, **_kwargs):
            pytest.fail("process rediscovery after arming")

        monkeypatch.setattr(m.peers.launch.engine.dispatch.process, "read_identity", no_discovery)
        monkeypatch.setattr(m.os, "pidfd_open", no_discovery)
        custody.close()
        for witness in pair.witnesses.values():
            witness.close()
        pair.children[role].stdin.close()
        assert pair.children[role].wait(timeout=2) == 0
        other = next(value for key, value in pair.children.items() if key != role)
        assert other.wait(timeout=2) == -signal.SIGKILL
        assert select.select([watch.fd], [], [], 2)[0]
        result = watch.finish()
        assert result.returncode == 11 and result.deadline_ns == custody.deadline_ns
        assert result.writer is pair.qualifiers["writer"].init
        assert result.observer is pair.qualifiers["observer"].init
        refused(watch.finish)
    finally:
        watch.close()


@pytest.mark.parametrize("short_budget", [True], indirect=True)
@pytest.mark.parametrize("stopped", ["writer", "observer", "both"])
@pytest.mark.parametrize("pair", [False, "preparation"], indirect=True)
def test_original_kernel_deadline_stops_frozen_peers_without_parent_polling(custody, pair, stopped):
    watch = m.arm(custody, scope=m.SCOPE)
    try:
        for role, witness in pair.witnesses.items():
            if stopped in (role, "both"):
                signal.pidfd_send_signal(witness.fd, signal.SIGSTOP)
        deadline = custody.deadline_ns
        custody.close()  # Watcher has its OWN copies, including original timer.
        # This parent performs no service tick, collector or watcher polling.
        time.sleep(max(0, deadline / 1e9 - time.clock_gettime(time.CLOCK_BOOTTIME)) + 0.1)
        exited(pair)
        assert select.select([watch.fd], [], [], 1)[0]
        result = watch.finish()
        assert result.returncode == 10 and result.deadline_ns == deadline
    finally:
        watch.close()


@pytest.mark.parametrize("role", m.peers.declarations.ROLES)
def test_runtime_mismatch_between_capture_and_arm_refuses_without_signaling(custody, pair, role):
    pair.containers[role]["State"]["Paused"] = True
    refused(lambda: m.arm(custody, scope=m.SCOPE))
    assert custody.attempted and all(not witness.exited() for witness in pair.witnesses.values())


@pytest.mark.parametrize("index", [2, 3])
def test_closed_domain_refuses_capture_without_collecting_other_role(inputs, pair, index):
    inputs[index].close()
    refused(lambda: m.Custody(*inputs))
    assert pair.obj.termination_capture_attempted
    assert not any(pair.counts[role]["container"] for role in m.peers.declarations.ROLES)
    assert all(not witness.exited() for witness in pair.witnesses.values())


def test_different_but_equal_clock_origin_cannot_replace_original(inputs, pair):
    clock = m.deadlines.links.plans.clock.ClockWitness(replace(pair.plan.original_clock))
    try:
        refused(lambda: m.Custody(inputs[0], clock, *inputs[2:]))
        assert all(not witness.exited() for witness in pair.witnesses.values())
    finally:
        clock.close()


def test_second_domain_cannot_be_writer_domain_twice(inputs, pair):
    refused(lambda: m.Custody(*inputs[:3], inputs[2]))
    assert all(not witness.exited() for witness in pair.witnesses.values())


def test_multithreaded_outer_process_cannot_arm(custody, pair):
    done = Event()
    worker = Thread(target=done.wait)
    worker.start()
    try:
        refused(lambda: m.arm(custody, scope=m.SCOPE))
        assert all(not witness.exited() for witness in pair.witnesses.values())
    finally:
        done.set()
        worker.join(2)


@pytest.mark.parametrize("fault", ["fork", "ready", "cleanup", "interrupt"])
def test_failed_arm_stops_both_committed_original_peers_without_fd_leak(
    custody, pair, monkeypatch, fault
):
    before = set(os.listdir("/proc/self/fd"))
    if fault in ("fork", "interrupt"):

        def failed():
            if fault == "interrupt":
                raise KeyboardInterrupt
            raise OSError("PRIVATE FAILURE")

        monkeypatch.setattr(m.os, "fork", failed)
    elif fault == "ready":
        monkeypatch.setattr(m, "_child", lambda *_args: 70)
    else:
        monkeypatch.setattr(m.cleanup, "_close_unrelated", lambda *_args: m.require(False))
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            m.arm(custody, scope=m.SCOPE)
    else:
        refused(lambda: m.arm(custody, scope=m.SCOPE))
    exited(pair)
    assert set(os.listdir("/proc/self/fd")) == before


def test_parent_close_stops_even_frozen_watcher_and_reports_uncertainty(custody, pair):
    watch = m.arm(custody, scope=m.SCOPE)
    signal.pidfd_send_signal(watch.fd, signal.SIGSTOP)
    refused(watch.close)
    assert watch.closed
    exited(pair)
    with pytest.raises(ChildProcessError):
        os.waitpid(watch.pid, os.WNOHANG)


def test_no_engine_mutation_or_journal_write(custody, pair, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("termination attempted an Engine operation")

    for name in ("create_execution", "start_execution", "_request"):
        monkeypatch.setattr(type(pair.qualifiers["writer"].docker), name, forbidden)
    watch = m.arm(custody, scope=m.SCOPE)
    watch.close()
    exited(pair)


def test_signal_failure_for_first_peer_does_not_skip_second(monkeypatch):
    attempts = []
    monkeypatch.setattr(m.deadlines, "_readable", lambda _fd: False)

    def send(fd, signum):
        attempts.append((fd, signum))
        if fd == 123:
            raise PermissionError("PRIVATE")

    monkeypatch.setattr(m.signal, "pidfd_send_signal", send)
    assert not m._kill_all((123, 456))
    assert attempts == [(123, signal.SIGKILL), (456, signal.SIGKILL)]


@contextmanager
def kernel_child(custody, parent):
    """Core-loop fixture; parent handle is a separate owned harmless process.

    The public arm() tests above prove its handle is actually the outer caller.
    Here that one input is synthetic so pytest can reap the watcher directly,
    without adopting orphan processes or changing the test runner's subreaper.
    """
    cancel, send = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    ready, response = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    pid, fd = None, None
    try:
        pid = os.fork()
        if pid == 0:
            os._exit(
                m._child(
                    tuple(f for f, _ in custody._resources),
                    custody.targets,
                    parent,
                    custody.timer,
                    cancel,
                    response,
                )
            )
        fd = os.pidfd_open(pid)
        assert select.select([ready], [], [], 2)[0]
        assert os.read(ready, 1) == b"1"
        yield fd
    finally:
        if fd is not None:
            if not select.select([fd], [], [], 0)[0]:
                os.write(send, b"X")
            if not select.select([fd], [], [], 2)[0]:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            assert select.select([fd], [], [], 2)[0]
            os.waitpid(pid, 0)
            os.close(fd)
        for handle in (cancel, send, ready, response):
            os.close(handle)


@pytest.mark.parametrize("pair", [False, "preparation"], indirect=True)
def test_actual_designated_parent_loss_stops_both_without_cancel_eof(custody, pair):
    with watchdog_tests.target() as (parent, _ticks):
        fd = os.pidfd_open(parent.pid)
        try:
            with kernel_child(custody, fd) as child_fd:
                parent.terminate()
                assert parent.wait(timeout=2) == -signal.SIGTERM
                exited(pair)
                assert select.select([child_fd], [], [], 2)[0]
                assert os.waitid(os.P_PIDFD, child_fd, os.WEXITED | os.WNOWAIT).si_status == 12
        finally:
            os.close(fd)


def test_kernel_loop_reports_both_exits_without_claiming_work_succeeded(custody, pair):
    fd = os.pidfd_open(os.getpid())
    try:
        with kernel_child(custody, fd) as child_fd:
            # Stop the watcher, make both original exits observable, then resume.
            signal.pidfd_send_signal(child_fd, signal.SIGSTOP)
            for process in pair.children.values():
                process.stdin.close()
                assert process.wait(timeout=2) == 0
            signal.pidfd_send_signal(child_fd, signal.SIGCONT)
            assert select.select([child_fd], [], [], 2)[0]
            assert os.waitid(os.P_PIDFD, child_fd, os.WEXITED | os.WNOWAIT).si_status == 0
    finally:
        os.close(fd)


def test_watcher_keeps_no_unrelated_socket_alive(custody, pair):
    left, right = socket.socketpair()
    watch = None
    try:
        watch = m.arm(custody, scope=m.SCOPE)
        right.close()
        left.settimeout(0.2)
        assert left.recv(1) == b""
    finally:
        if watch is not None:
            watch.close()
        left.close()
        right.close()
    exited(pair)


@pytest.mark.parametrize("fault", ["disarm", "renew", "monotonic"])
def test_disarmed_changed_or_wrong_clock_timer_cannot_arm(custody, pair, fault):
    if fault == "disarm":
        os.timerfd_settime_ns(custody.timer, initial=0)
    elif fault == "renew":
        os.timerfd_settime_ns(
            custody.timer, flags=os.TFD_TIMER_ABSTIME, initial=custody.deadline_ns + 10**9
        )
    else:
        fake = os.timerfd_create(time.CLOCK_MONOTONIC, flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK)
        os.timerfd_settime_ns(fake, flags=os.TFD_TIMER_ABSTIME, initial=custody.deadline_ns)
        os.dup2(fake, custody.timer, inheritable=False)
        os.close(fake)
    refused(lambda: m.arm(custody, scope=m.SCOPE))
    assert all(not witness.exited() for witness in pair.witnesses.values())


@pytest.mark.parametrize("role", m.peers.declarations.ROLES)
def test_invalid_role_source_prevents_capture_and_leaks_no_descriptors(inputs, pair, role):
    before = set(os.listdir("/proc/self/fd"))
    pair.containers[role]["State"]["Paused"] = True
    refused(lambda: m.Custody(*inputs))
    assert set(os.listdir("/proc/self/fd")) == before
    assert all(not witness.exited() for witness in pair.witnesses.values())


def test_watch_pidfd_acquisition_failure_stops_peers_and_reaps_owned_fork(
    custody, pair, monkeypatch
):
    actual_open, actual_fork = os.pidfd_open, os.fork
    children = []

    def fork():
        pid = actual_fork()
        if pid:
            children.append(pid)
        return pid

    def open_pid(pid, *args):
        if pid in children:
            raise OSError("PRIVATE")
        return actual_open(pid, *args)

    monkeypatch.setattr(m.os, "fork", fork)
    monkeypatch.setattr(m.os, "pidfd_open", open_pid)
    refused(lambda: m.arm(custody, scope=m.SCOPE))
    exited(pair)
    assert len(children) == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(children[0], os.WNOHANG)


def test_outer_clock_keeps_its_own_original_not_writer_window(pair):
    clock = m.deadlines.links.plans.clock.ClockWitness(m.deadlines.links.plans.clock.read())
    assert clock.original != pair.plan.original_clock
    domains = []
    custody = None
    try:
        for role in m.peers.declarations.ROLES:
            domains.append(
                m.deadlines.links.domains.ZeroDomain(clock.original, pair.witnesses[role])
            )
        custody = m.Custody(pair.obj, clock, *domains)
        assert custody.origin is clock.original
        assert custody.plan.original_clock is pair.plan.original_clock
    finally:
        if custody is not None:
            custody.close()
        for domain in domains:
            domain.close()
        clock.close()


@pytest.mark.parametrize("operation", ["close", "finish"])
def test_unexpected_watcher_exit_stops_both_and_never_claims_success(custody, pair, operation):
    watch = m.arm(custody, scope=m.SCOPE)
    try:
        signal.pidfd_send_signal(watch.fd, signal.SIGKILL)
        assert select.select([watch.fd], [], [], 2)[0]
        refused(getattr(watch, operation))
        exited(pair)
    finally:
        watch.close()


def test_child_drops_inherited_signal_handlers_and_mask(custody):
    previous = signal.signal(signal.SIGUSR1, lambda *_args: None)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR2})
    watch = None
    try:
        watch = m.arm(custody, scope=m.SCOPE)
        fields = dict(
            line.split(":", 1)
            for line in Path(f"/proc/{watch.pid}/status").read_text().splitlines()
        )
        # glibc retains its reserved NPTL handler (not exposed by Python).
        # All application-settable handlers, including ours, must be gone.
        exposed = sum(1 << (int(signum) - 1) for signum in signal.valid_signals())
        assert int(fields["SigCgt"], 16) & exposed == 0
        assert int(fields["SigBlk"], 16) == 0
    finally:
        signal.signal(signal.SIGUSR1, previous)
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        if watch is not None:
            watch.close()


def test_nonowner_thread_cannot_stop_peers_by_calling_finish(custody, pair):
    watch = m.arm(custody, scope=m.SCOPE)
    caught = []

    def finish():
        try:
            watch.finish()
        except m.UnconfirmedTermination:
            caught.append(True)

    worker = Thread(target=finish)
    try:
        worker.start()
        worker.join(2)
        assert caught == [True] and not watch.finished
        assert all(not witness.exited() for witness in pair.witnesses.values())
    finally:
        watch.close()


def test_inherited_child_reaper_prevents_arm_before_signaling(custody, pair):
    previous = signal.signal(signal.SIGCHLD, lambda *_args: None)
    try:
        refused(lambda: m.arm(custody, scope=m.SCOPE))
        assert all(not witness.exited() for witness in pair.witnesses.values())
    finally:
        signal.signal(signal.SIGCHLD, previous)


@pytest.mark.parametrize("limit", [True, "secret", float("nan"), float("inf"), -1])
def test_outer_pair_bound_refuses_invalid_or_elapsed_limit_before_io(pair, limit):
    with pytest.raises(m.peers.launch.UnconfirmedHostLaunch):
        pair.obj._collect_before(limit)
    assert pair.obj.failed
    assert not any(pair.counts[role]["container"] for role in m.peers.declarations.ROLES)


def test_outer_pair_bound_reaches_both_original_collectors_without_renewal(pair, monkeypatch):
    observed = []
    original = m.peers.PeerRuntimeQualification._collect_before

    def collect(self, deadline):
        observed.append(deadline)
        return original(self, deadline)

    monkeypatch.setattr(m.peers.PeerRuntimeQualification, "_collect_before", collect)
    end = time.monotonic() + 1
    pair.obj._collect_before(end)
    assert observed == [end, end]


def test_future_outer_bound_cannot_extend_original_two_seconds(pair, monkeypatch):
    observed = []
    original = m.peers.PeerRuntimeQualification._collect_before

    def collect(self, deadline):
        observed.append(deadline)
        return original(self, deadline)

    monkeypatch.setattr(m.peers.PeerRuntimeQualification, "_collect_before", collect)
    before = time.monotonic()
    pair.obj._collect_before(before + 100)
    after = time.monotonic()
    assert len(observed) == 2 and observed[0] == observed[1]
    assert before + 2 <= observed[0] <= after + 2


def test_reused_peer_descriptor_never_signals_foreign_child_or_leaks_watcher(custody, pair):
    watch = m.arm(custody, scope=m.SCOPE)
    with watchdog_tests.target() as (foreign, _ticks):
        fd = os.pidfd_open(foreign.pid)
        replaced = watch.targets[0]
        try:
            os.dup2(fd, replaced, inheritable=False)
            refused(watch.close)
            assert watch.closed and foreign.poll() is None
            os.fstat(replaced)  # Foreign replacement was not closed, either.
            exited(pair)  # Watcher still retained BOTH real original handles.
            with pytest.raises(ChildProcessError):
                os.waitpid(watch.pid, os.WNOHANG)
        finally:
            os.close(fd)
            os.close(replaced)


def test_permission_probe_failure_never_starts_watcher(custody, pair, monkeypatch):
    original = signal.pidfd_send_signal

    def send(fd, signum, *args):
        if signum == 0:
            raise PermissionError("PRIVATE")
        return original(fd, signum, *args)

    monkeypatch.setattr(m.signal, "pidfd_send_signal", send)
    monkeypatch.setattr(m.os, "fork", lambda: pytest.fail("no permission to arm watcher"))
    refused(lambda: m.arm(custody, scope=m.SCOPE))
    exited(pair)
