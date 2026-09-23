"""Independent watchdog processes target only new, owned synthetic children."""

import importlib.util
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread

import pytest

NAME = "supplemental_recording_watchdog"
SOURCE = Path(__file__).parents[1] / "scripts" / (NAME + ".py")
SPEC = importlib.util.spec_from_file_location(NAME, SOURCE)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@contextmanager
def target(*, ignore_term=False):
    program = (
        "import os, signal, time; "
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_term else "")
        + "os.write(1, b'R'); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", program],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert m.select.select([process.stdout], [], [], 2)[0]
        assert process.stdout.read(1) == b"R"
        yield process, m._live(process.pid)[1]
    finally:
        if process.poll() is None:
            process.kill()  # Only this fixture's exact unreaped Popen.
        process.wait(timeout=2)
        process.stdout.close()


def refused(action):
    with pytest.raises(m.UnconfirmedWatchdog) as caught:
        action()
    assert str(caught.value) == m.MESSAGE


@pytest.mark.parametrize("mode", ["normal_exit", "term", "kill", "zero_grace", "blocked_guardian"])
def test_independent_deadline_and_no_disarm(mode):
    with target(ignore_term=mode in ("kill", "zero_grace", "blocked_guardian")) as (process, ticks):
        deadline, grace = time.monotonic() + 0.3, 0 if mode == "zero_grace" else 0.1
        guard = m.arm(process, start_ticks=ticks, deadline=deadline, grace=grace)
        try:
            assert guard.native_pid == process.pid and guard.native_start_ticks == ticks
            assert not hasattr(guard, "disarm") and not hasattr(guard, "extend")
            if mode == "normal_exit":
                process.terminate()
            if mode == "blocked_guardian":
                # No polling, report processing or watchdog wait is running in
                # the guardian during its synthetic blocking operation.
                time.sleep(0.6)
                assert m._exited(guard.native_fd) and m._exited(guard.fd)
            result = guard.wait()
            expected = 0 if mode == "normal_exit" else (10 if mode == "term" else 11)
            assert result.returncode == expected
            assert process.wait(timeout=1) == (
                -signal.SIGTERM if expected in (0, 10) else -signal.SIGKILL
            )
            assert result.deadline == deadline and result.grace == grace
            refused(guard.wait)
        finally:
            guard.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("deadline", True),
        ("deadline", float("nan")),
        ("deadline", float("inf")),
        ("deadline", -1),
        ("deadline", "PRIVATE"),
        ("deadline", "too_long"),
        ("grace", True),
        ("grace", -1),
        ("grace", 6),
        ("grace", float("nan")),
        ("start_ticks", True),
        ("start_ticks", 0),
        ("start_ticks", "PRIVATE"),
    ],
)
def test_invalid_input_does_not_touch_unbound_native(field, value):
    with target() as (process, ticks):
        options = dict(start_ticks=ticks, deadline=time.monotonic() + 1, grace=0.1)
        options[field] = time.monotonic() + m.MAX_SECONDS + 10 if value == "too_long" else value
        refused(lambda: m.arm(process, **options))
        assert process.poll() is None


def test_stale_identity_cannot_bind_or_signal_child():
    with target() as (process, ticks):
        refused(
            lambda: m.arm(process, start_ticks=ticks + 1, deadline=time.monotonic() + 1, grace=0.1)
        )
        assert process.poll() is None


def test_unknown_or_already_reaped_process_cannot_arm():
    refused(lambda: m.arm(object(), start_ticks=1, deadline=time.monotonic() + 1, grace=0.1))
    with target() as (process, ticks):
        process.terminate()
        process.wait(timeout=1)
        refused(lambda: m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 1, grace=0.1))


def test_multithreaded_guardian_cannot_fork_or_signal():
    done = Event()
    worker = Thread(target=done.wait)
    with target() as (process, ticks):
        worker.start()
        try:
            refused(
                lambda: m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 1, grace=0.1)
            )
            assert process.poll() is None
        finally:
            done.set()
            worker.join(1)


@pytest.mark.parametrize("fault", ["fork", "closed_ready", "bad_ready", "cleanup", "cancel"])
def test_failed_arm_kills_only_already_bound_native(monkeypatch, fault):
    before = len(list(Path("/proc/self/fd").iterdir()))
    with target(ignore_term=True) as (process, ticks):
        if fault in ("fork", "cancel"):

            def failed():
                if fault == "cancel":
                    raise KeyboardInterrupt
                raise OSError("PRIVATE fork failure")

            monkeypatch.setattr(m.os, "fork", failed)
        elif fault in ("closed_ready", "bad_ready"):

            def incorrect(native, guardian, ready, deadline, grace):
                if fault == "bad_ready":
                    os.write(ready, b"X")
                return 70

            monkeypatch.setattr(m, "_watch", incorrect)
        else:
            monkeypatch.setattr(m, "_close_unrelated", lambda *_: m.require(False))
        if fault == "cancel":
            with pytest.raises(KeyboardInterrupt):
                m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 1, grace=0.1)
        else:
            refused(
                lambda: m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 1, grace=0.1)
            )
        assert process.wait(timeout=2) == -signal.SIGKILL
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_watchdog_drops_inherited_sockets_before_readiness():
    import socket

    read, write = socket.socketpair()
    try:
        with target(ignore_term=True) as (process, ticks):
            guard = m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 0.3, grace=0)
            try:
                write.close()
                read.settimeout(0.1)
                assert read.recv(1) == b""  # Watchdog did not retain the peer.
                assert guard.wait().returncode == 11
                assert process.wait(timeout=1) == -signal.SIGKILL
            finally:
                guard.close()
    finally:
        read.close()
        write.close()


def test_closing_observer_handles_cannot_disarm_deadline():
    with target(ignore_term=True) as (process, ticks):
        guard = m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 0.3, grace=0)
        duplicate = os.dup(guard.fd)  # Retain this fixture's independent exit handle.
        try:
            guard.close()
            assert process.wait(timeout=1) == -signal.SIGKILL
            assert m.select.select([duplicate], [], [], 1)[0]
            assert os.waitpid(guard.pid, 0)[0] == guard.pid
            refused(guard.wait)
        finally:
            os.close(duplicate)


def test_lost_watchdog_wait_return_cannot_be_retried(monkeypatch):
    with target() as (process, ticks):
        guard = m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 1, grace=0.1)
        try:
            process.terminate()
            process.wait(timeout=1)
            original = os.waitpid

            def lost(pid, flags):
                result = original(pid, flags)
                if pid == guard.pid and result[0] == pid:
                    raise OSError("PRIVATE lost wait return")
                return result

            monkeypatch.setattr(os, "waitpid", lost)
            refused(guard.wait)
            refused(guard.wait)
        finally:
            guard.close()


@pytest.mark.parametrize("mode", ["guardian_exit", "guardian_frozen"])
def test_watchdog_outlives_or_runs_independently_of_guardian(mode):
    # This isolated driver alone becomes a child subreaper; pytest's process
    # attributes are unchanged. It reaps all synthetic descendants, including
    # those orphaned by deliberately killing their guardian.
    program = r"""
import ctypes, importlib.util, json, os, select, signal, subprocess, sys, time
from contextlib import suppress
libc = ctypes.CDLL(None)
assert libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER, this driver only
spec = importlib.util.spec_from_file_location("watchdog_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
mode = sys.argv[2]
read, write = os.pipe()
guardian = os.fork()
if guardian == 0:
    try:
        assert libc.prctl(1, signal.SIGKILL, 0, 0, 0) == 0
        os.close(read)
        native = subprocess.Popen([sys.executable, "-I", "-c",
            "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "os.write(1,b'R'); time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert select.select([native.stdout], [], [], 1)[0] and native.stdout.read(1) == b"R"
        ticks = m._live(native.pid)[1]
        guard = m.arm(native, start_ticks=ticks,
            deadline=time.monotonic()+(5 if mode == "guardian_exit" else 0.4), grace=0.1)
        os.write(write, json.dumps([native.pid, ticks, guard.pid, guard.ticks]).encode())
        os.close(write)
        if mode == "guardian_exit":
            signal.pause()
        else:
            assert guard.wait().returncode == 11
            assert native.wait(timeout=1) == -signal.SIGKILL
            guard.close()
            native.stdout.close()
        os._exit(0)
    except BaseException:
        os._exit(75)
os.close(write)
handles = [(guardian, os.pidfd_open(guardian))]
try:
    assert select.select([read], [], [], 2)[0]
    native, native_ticks, watcher, watcher_ticks = json.loads(os.read(read, 1024))
    for pid, ticks in ((native, native_ticks), (watcher, watcher_ticks)):
        fd = os.pidfd_open(pid)
        handles.append((pid, fd))
        assert m._live(pid) == (guardian, ticks) and not m._exited(fd)
    started = time.monotonic()
    signal.pidfd_send_signal(handles[0][1],
        signal.SIGKILL if mode == "guardian_exit" else signal.SIGSTOP)
    if mode == "guardian_exit":
        assert select.select([handles[0][1]], [], [], 1)[0]
        assert os.waitpid(guardian, 0)[0] == guardian
        assert select.select([handles[1][1]], [], [], 1)[0]
        assert select.select([handles[2][1]], [], [], 1)[0]
        assert os.waitstatus_to_exitcode(os.waitpid(native, 0)[1]) == -signal.SIGKILL
        assert os.waitstatus_to_exitcode(os.waitpid(watcher, 0)[1]) == 12
        assert time.monotonic()-started < 2  # Well before its five-second deadline.
    else:
        time.sleep(0.75)  # Guardian cannot run ANY Python/thread/callback here.
        assert m._exited(handles[1][1]) and m._exited(handles[2][1])
        signal.pidfd_send_signal(handles[0][1], signal.SIGCONT)
        assert select.select([handles[0][1]], [], [], 1)[0]
        assert os.waitstatus_to_exitcode(os.waitpid(guardian, 0)[1]) == 0
finally:
    os.close(read)
    # Kill/reap only identities bound while alive in this isolated fixture.
    for pid, fd in handles:
        with suppress(ProcessLookupError):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        select.select([fd], [], [], 1)
        with suppress(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        os.close(fd)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program, str(SOURCE), mode],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=8,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert not result.stdout


def test_killed_watchdog_is_not_reported_as_native_exit():
    with target(ignore_term=True) as (process, ticks):
        guard = m.arm(process, start_ticks=ticks, deadline=time.monotonic() + 2, grace=0.1)
        try:
            signal.pidfd_send_signal(guard.fd, signal.SIGKILL)
            refused(guard.wait)
            refused(guard.wait)
            assert process.poll() is None  # Caller still owns native recovery.
        finally:
            guard.close()
