"""Offline native ABI + original Custody join; NOT installed host qualification.

All targets are fresh disposable LOCAL children. Synthetic Engine/runtime facts
come from the existing paired fixture. No scanner, App, Docker or HAOS actions.
The launcher below is TEST-ONLY: it does not authenticate an installed binary,
outer service placement, active admission, or an exclusive recovery authority.
"""

import fcntl
import os
import select
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

from . import test_supplemental_recording_peer_termination as termination

m = termination.m
layout, image_umask, supervised = (
    termination.layout,
    termination.image_umask,
    termination.supervised,
)
image, configured, pair = termination.image, termination.configured, termination.pair
helper, inputs, custody = termination.helper, termination.inputs, termination.custody
short_budget = termination.short_budget
pytestmark = pytest.mark.skipif(
    not m.deadlines.timerfd_available() or not shutil.which("cc"),
    reason="Linux timerfd Python API and local C compiler required for offline Custody fixture",
)
SOURCE = Path(__file__).parents[1] / "scripts/native/supplemental_peer_watch.c"
MODE = "--offline-original-peer-watch-v1"


@pytest.fixture(scope="module", params=["dynamic", "static", "ubsan"])
def binary(tmp_path_factory, request):
    path = tmp_path_factory.mktemp("native-watch") / "peer-watch"
    result = subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wconversion",
            "-Wshadow",
            "-Wformat=2",
            "-fstack-protector-strong",
            "-D_FORTIFY_SOURCE=3",
            "-fPIE",
            "-static-pie" if request.param == "static" else "-pie",
            "-Wl,-z,relro,-z,now",
            *(
                ["-fsanitize=undefined", "-fsanitize-undefined-trap-on-error"]
                if request.param == "ubsan"
                else []
            ),
            str(SOURCE),
            "-o",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return path


def test_native_ceiling_matches_existing_original_policy():
    assert m.peers.launch.plans.base.TOTAL_SECONDS == 1500
    assert "#define MAX_LIFETIME (UINT64_C(1500) * NS)" in SOURCE.read_text()
    assert m.CAPTURE_SECONDS == 2.0


def test_static_make_build_has_no_interpreter_or_dynamic_dependencies(tmp_path):
    if not shutil.which("make") or not shutil.which("readelf"):
        pytest.skip("Make and readelf needed for offline static artifact inspection")
    build = subprocess.run(
        ["make", "-f", str(SOURCE.with_name("Makefile")), f"BUILD_DIR={tmp_path}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    elf = subprocess.run(
        ["readelf", "-W", "-l", "-d", str(tmp_path / "supplemental-peer-watch")],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert elf.returncode == 0 and "INTERP" not in elf.stdout and "NEEDED" not in elf.stdout
    assert "GNU_RELRO" in elf.stdout and "BIND_NOW" in elf.stdout
    stack = next(line for line in elf.stdout.splitlines() if "GNU_STACK" in line)
    assert "RWE" not in stack


def collect(pid, fd, timeout=2):
    assert select.select([fd], [], [], timeout)[0], "native child did not exit"
    found, status = os.waitpid(pid, os.WNOHANG)
    assert found == pid
    return os.waitstatus_to_exitcode(status)


def native_code(watch):
    """Observe original child exit WITHOUT consuming Watch's single reap."""
    assert select.select([watch.fd], [], [], 2)[0]
    result = os.waitid(os.P_PIDFD, watch.fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    assert result is not None and result.si_code == os.CLD_EXITED
    return result.si_status


def spawn(binary, handles, args, *, extra=()):
    """Collision-safe fixed fd ABI, pinned opened file, no Python preexec_fn.

    This fixture path is locally compiled; an opened executable fd is NOT a
    trusted source/runtime/ELF pin. Actual deployment remains unimplemented.
    """
    copies = []
    executable = os.open(binary, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        for fd in (*handles, executable, *extra):
            copies.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 20))
        actions = [(os.POSIX_SPAWN_DUP2, fd, slot) for slot, fd in enumerate(copies, 3)]
        pid = os.posix_spawn(
            "/proc/self/fd/9",
            [str(binary), *args],
            {},
            file_actions=actions,
            setsigmask=(),
            setsigdef=(signal.SIGCHLD,),
        )
        return pid
    finally:
        os.close(executable)
        for fd in copies:
            os.close(fd)


@contextmanager
def native(custody, binary, *, fault=None, outer=None, extra=()):
    """One real original capture/full re-collection -> exec -> existing Watch.

    No public command/library selects this test launcher. Original two-second
    capture budget includes collection, spawn and readiness; no retries.
    """
    owned = []
    child = child_fd = None
    watch = None
    committed = False
    custody._guard()
    assert not custody.attempted
    custody.attempted = True
    end = min(time.monotonic() + m.CAPTURE_SECONDS, custody.plan.lease["ready_by"])
    # Conservative conversion using the original offset, never a fresh budget.
    ready_by = min(
        int(m.Decimal(end) * 10**9) + custody.origin.offset[0],
        int(m.Decimal(custody.plan.deadlines.ready_by) * 10**9),
    )
    custody._live(end)
    custody.pair._collect_before(end)
    custody._live(end)

    def own(fd):
        owned.append(fd)
        return fd

    try:
        targets = tuple(own(os.dup(fd)) for fd in custody.targets)
        parent = own(os.pidfd_open(os.getpid()) if outer is None else os.dup(outer))
        read, write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        ready, send = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        owned.extend((read, write, ready, send))
        handles = [*targets, parent, read, send, custody.host_namespace]
        args = [MODE, str(custody.deadline_ns), str(ready_by), custody.origin.boot]
        if fault is not None:
            fault(handles, args)
        committed = True
        child = spawn(binary, handles, args, extra=extra)
        child_fd = own(os.pidfd_open(child))
        os.close(send)
        owned.remove(send)
        watch = m.Watch(child, child_fd, write, targets, custody.identities, custody.deadline_ns)
        for fd in (child_fd, write, *targets):
            owned.remove(fd)
        custody.armed_watch = watch
        assert select.select([ready, child_fd], [], [], max(0, end - time.monotonic()))[0]
        data = os.read(ready, 2)
        if fault is None:
            assert data == b"1"
            custody._live(end)
            custody._guard()
            assert not m.deadlines._readable(child_fd) and time.monotonic() < end
        else:
            assert data == b""
        yield watch
    finally:
        if watch is not None:
            # Fault exit 64/70 is uncertainty, NEVER a successful retirement.
            with suppress(m.UnconfirmedTermination):
                watch.close()
        elif committed:
            m._kill_all(custody.targets)
            if child_fd is not None:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(child_fd, signal.SIGKILL)
                collect(child, child_fd)
            elif child is not None:
                # Fresh owned spawn but pidfd acquisition failed: never reopen
                # or signal its numeric PID. Original peer stops make it exit;
                # reap only our own child within the existing retirement bound.
                finish_by = time.monotonic() + m.RETIRE_SECONDS
                while os.waitpid(child, os.WNOHANG)[0] == 0:
                    assert time.monotonic() < finish_by
                    select.select([], [], [], 0.01)
        for fd in reversed(owned):
            os.close(fd)


def test_unrecognized_command_is_not_a_launcher(binary):
    result = subprocess.run([str(binary)], capture_output=True, timeout=2, check=False)
    assert result.returncode == 64 and not result.stdout and not result.stderr


@pytest.mark.parametrize("pair", [False, "preparation"], indirect=True)
def test_original_custody_full_recollection_exec_and_cancel(custody, pair, binary):
    raw = pair.plan.raw
    before = len(os.listdir("/proc/self/fd"))
    with native(custody, binary) as watch:
        assert all(pair.counts[role]["container"] == 4 for role in pair.children)
        assert not any(w.exited() for w in pair.witnesses.values())
        assert watch.deadline_ns == custody.deadline_ns and pair.plan.raw == raw
        os.write(watch.cancel, b"X")
        assert select.select([watch.fd], [], [], 2)[0]
        assert watch.finish().returncode == 13
        termination.exited(pair)
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("role", ["writer", "observer"])
def test_original_peer_loss_stops_only_partner(custody, pair, binary, role, monkeypatch):
    with native(custody, binary) as watch:
        monkeypatch.setattr(m.os, "pidfd_open", lambda *_: pytest.fail("PID reopened"))
        custody.close()
        for witness in pair.witnesses.values():
            witness.close()
        pair.children[role].stdin.close()
        assert pair.children[role].wait(timeout=2) == 0
        other = next(p for name, p in pair.children.items() if name != role)
        assert other.wait(timeout=2) == -signal.SIGKILL
        assert select.select([watch.fd], [], [], 2)[0]
        assert watch.finish().returncode == 11


@pytest.mark.parametrize("short_budget", [True], indirect=True)
@pytest.mark.parametrize("stopped", ["writer", "observer", "both"])
def test_private_timer_keeps_original_cutoff_with_stopped_peers(custody, pair, binary, stopped):
    with native(custody, binary) as watch:
        for role, witness in pair.witnesses.items():
            if stopped in (role, "both"):
                signal.pidfd_send_signal(witness.fd, signal.SIGSTOP)
        # Parent's original custody timer is not the native private timer.
        # Disarming this DUPLICATE must not disarm the native absolute cutoff.
        os.timerfd_settime_ns(custody.timer, initial=0)
        cutoff = custody.deadline_ns
        custody.close()
        time.sleep(max(0, cutoff / 1e9 - time.clock_gettime(time.CLOCK_BOOTTIME)) + 0.05)
        termination.exited(pair)
        assert select.select([watch.fd], [], [], 2)[0]
        assert watch.finish().returncode == 10


def test_actual_outer_exit_wins_even_with_cancel_writer_still_open(custody, pair, binary):
    outer = subprocess.Popen(["/bin/sleep", "30"], stdin=subprocess.DEVNULL)
    fd = os.pidfd_open(outer.pid)
    try:
        with native(custody, binary, outer=fd) as watch:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            assert outer.wait(timeout=2) == -signal.SIGKILL
            assert select.select([watch.fd], [], [], 2)[0]
            assert watch.finish().returncode == 12
            termination.exited(pair)
    finally:
        with suppress(ProcessLookupError):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        outer.wait(timeout=2)
        os.close(fd)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_termination_signal_is_uncertain_and_stops_both(custody, pair, binary, signum):
    with native(custody, binary) as watch:
        signal.pidfd_send_signal(watch.fd, signum)
        assert native_code(watch) == 70
        # Native stop must happen BEFORE the caller's independent fail-close.
        termination.exited(pair)
        termination.refused(watch.finish)


@pytest.mark.parametrize(
    "fault_name",
    [
        "expired",
        "renewed",
        "ready_expired",
        "ready_renewed",
        "overflow",
        "negative",
        "leading_zero",
        "wrong_boot",
        "wrong_namespace",
        "wrong_pipe",
        "alias_pipe",
        "alias_peer",
        "outer_is_peer",
        "not_pidfd",
        "wrong_scope",
    ],
)
def test_malformed_handoff_never_reports_ready(custody, pair, binary, fault_name):
    def fault(handles, args):
        if fault_name == "expired":
            args[1] = "1"
        elif fault_name == "renewed":
            args[1] = str(time.clock_gettime_ns(time.CLOCK_BOOTTIME) + 1501 * 10**9)
        elif fault_name == "ready_expired":
            args[2] = "1"
        elif fault_name == "ready_renewed":
            args[2] = str(time.clock_gettime_ns(time.CLOCK_BOOTTIME) + 3 * 10**9)
        elif fault_name == "overflow":
            args[1] = "9" * 100
        elif fault_name == "negative":
            args[1] = "-1"
        elif fault_name == "leading_zero":
            args[1] = "0" + args[1]
        elif fault_name == "wrong_boot":
            args[3] = "0" * 32
        elif fault_name == "wrong_namespace":
            handles[5] = handles[0]
        elif fault_name == "wrong_pipe":
            handles[3] = handles[0]
        elif fault_name == "alias_pipe":
            handles[4] = handles[3]
        elif fault_name == "alias_peer":
            handles[1] = handles[0]
        elif fault_name == "outer_is_peer":
            handles[2] = handles[0]
        elif fault_name == "not_pidfd":
            handles[0] = handles[5]
        elif fault_name == "wrong_scope":
            args[0] = "--active-recording"
        else:
            pytest.fail("unknown fault")

    with native(custody, binary, fault=fault) as watch:
        precommit = fault_name in {
            "overflow",
            "negative",
            "leading_zero",
            "alias_peer",
            "not_pidfd",
            "wrong_scope",
        }
        assert native_code(watch) == (64 if precommit else 70)
        if not precommit:
            termination.exited(pair)  # Native stop, BEFORE caller fallback.
        termination.refused(watch.finish)
        # Caller already committed originals: any refusal closes this ONE case
        # with original-handle stop, never replay/native retry/adoption.
        termination.exited(pair)
        with pytest.raises(AssertionError), native(custody, binary):
            pytest.fail("second spawn")


@pytest.mark.parametrize("role", ["writer", "observer", "outer"])
def test_actual_exit_during_exec_stops_survivors_before_caller_cleanup(custody, pair, binary, role):
    outer = subprocess.Popen(["/bin/sleep", "30"], stdin=subprocess.DEVNULL)
    fd = os.pidfd_open(outer.pid)
    try:

        def fault(_handles, _args):
            if role == "outer":
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                outer.wait(timeout=2)
            else:
                pair.children[role].stdin.close()
                assert pair.children[role].wait(timeout=2) == 0

        with native(custody, binary, fault=fault, outer=fd) as watch:
            assert native_code(watch) == 70
            for name, child in pair.children.items():
                if name != role:
                    # Verify the NATIVE watcher did this, not Watch.finish's
                    # independent uncertainty cleanup in the test caller.
                    assert child.wait(timeout=2) == -signal.SIGKILL
            termination.refused(watch.finish)
    finally:
        with suppress(ProcessLookupError):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        outer.wait(timeout=2)
        os.close(fd)


def test_unrelated_inherited_writer_is_closed(custody, pair, binary):
    read, write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        with native(custody, binary, extra=(write,)) as watch:
            os.close(write)
            write = -1
            assert select.select([read], [], [], 1)[0]
            assert os.read(read, 1) == b""
            os.write(watch.cancel, b"X")
            assert select.select([watch.fd], [], [], 2)[0]
            assert watch.finish().returncode == 13
            termination.exited(pair)
    finally:
        os.close(read)
        if write >= 0:
            os.close(write)


def test_failed_native_pidfd_acquisition_retires_originals_without_pid_signal(
    custody, pair, binary, monkeypatch
):
    original = os.pidfd_open
    spawned = []
    original_spawn = spawn

    def observed_spawn(*args, **kwargs):
        pid = original_spawn(*args, **kwargs)
        spawned.append(pid)
        return pid

    def opened(pid, *args, **kwargs):
        if pid != os.getpid():
            raise OSError("fixture pidfd acquisition failure")
        return original(pid, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setitem(globals(), "spawn", observed_spawn)
        patch.setattr(os, "pidfd_open", opened)
        patch.setattr(os, "kill", lambda *_: pytest.fail("numeric PID signaled"))
        with pytest.raises(OSError, match="fixture pidfd"), native(custody, binary):
            pytest.fail("failed spawn admitted")
    termination.exited(pair)
    assert len(spawned) == 1 and custody.attempted
    with pytest.raises(ChildProcessError):
        os.waitpid(spawned[0], os.WNOHANG)  # Already reaped; never adopted/retried.
