#!/usr/bin/env python3
"""Independent local-process deadline, not an installed container supervisor.

Arm only around a source-qualified, newly owned child held at its launch gate.
The watchdog is a separate process: report validation, fsync or a blocked guardian
cannot extend its deadline. It never launches/restarts a scanner, edits files or
claims recording success. Source authentication and container-init exit/recovery
remain separate host responsibilities. No public executable mode is enabled.
"""

from __future__ import annotations

import errno
import math
import os
import select
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from threading import Lock, current_thread, main_thread

MAX_SECONDS = 780  # At most 600 seconds of readiness + 180 seconds of recording.
MAX_GRACE = 5
MAX_FDS = 4096
MESSAGE = (
    "Finite recording watchdog is unconfirmed; preserve the case and use exact-process recovery."
)


class UnconfirmedWatchdog(ValueError):
    """No exit or restoration proof can be reconstructed from a missing return."""


def require(value):
    if not value:
        raise UnconfirmedWatchdog(MESSAGE)


def _live(pid):
    with open(f"/proc/{pid}/stat", "rb", buffering=0) as stream:
        raw = stream.read(4097)
    require(len(raw) <= 4096)
    prefix, delimiter, suffix = raw.rpartition(b") ")
    require(delimiter and prefix.startswith(str(pid).encode() + b" ("))
    fields = suffix.split()
    require(len(fields) >= 20 and fields[0] in (b"R", b"S", b"D", b"T", b"t", b"I"))
    require(fields[1].isdigit() and fields[19].isdigit() and int(fields[19]) > 0)
    return int(fields[1]), int(fields[19])


def _exited(fd):
    return bool(select.select([fd], [], [], 0)[0])


def _signal(fd, value):
    try:
        signal.pidfd_send_signal(fd, value)
    except ProcessLookupError:
        require(_exited(fd))


def _close_unrelated(keep):
    # The child must not keep runtime/report sockets or recording files alive.
    names = os.listdir("/proc/self/fd")
    require(len(names) <= MAX_FDS and all(name.isdecimal() for name in names))
    for name in names:
        fd = int(name)
        if fd not in keep:
            try:
                os.close(fd)
            except OSError as error:
                require(error.errno == errno.EBADF)  # Closed proc-listing fd only.


def _watch(native, guardian, ready, deadline, grace):
    """Fixed child loop. No validation callback, filesystem writes or threads."""
    try:
        _close_unrelated({native, guardian, ready})
        require(not _exited(native) and not _exited(guardian) and time.monotonic() < deadline)
        require(os.write(ready, b"1") == 1)
        os.close(ready)
        while True:
            exited = select.select([native, guardian], [], [], max(0, deadline - time.monotonic()))[
                0
            ]
            if native in exited:
                return 0
            if guardian in exited:
                _signal(native, signal.SIGKILL)
                return 12
            if time.monotonic() >= deadline:
                break
        _signal(native, signal.SIGTERM)
        # The grace period starts at the ORIGINAL deadline, not after a delayed
        # signal/loop wake. It can never extend the case's configured cutoff.
        kill_by = deadline + grace
        while True:
            exited = select.select([native, guardian], [], [], max(0, kill_by - time.monotonic()))[
                0
            ]
            if native in exited:
                return 10
            if guardian in exited or time.monotonic() >= kill_by:
                _signal(native, signal.SIGKILL)
                return 12 if guardian in exited else 11
    except BaseException:
        # Failure cannot leave an already bound native process unsupervised.
        with suppress(BaseException):
            _signal(native, signal.SIGKILL)
        return 70


@dataclass(frozen=True)
class Outcome:
    native_pid: int
    native_start_ticks: int
    watchdog_pid: int
    watchdog_start_ticks: int
    deadline: float
    grace: float
    returncode: int


class Watchdog:
    """One separately running deadline; there is deliberately no disarm/rearm.

    wait() observes/reaps only this owned watchdog. The native Popen owner must
    still wait/reap its native child; a watchdog's signal does not prove exit.
    close() closes this process's handles without cancelling the watchdog.
    """

    def __init__(
        self, *, native_pid, native_start_ticks, native_fd, pid, ticks, fd, deadline, grace
    ):
        self.native_pid, self.native_start_ticks, self.native_fd = (
            native_pid,
            native_start_ticks,
            native_fd,
        )
        self.pid, self.ticks, self.fd = pid, ticks, fd
        self.deadline, self.grace, self.owner = deadline, grace, os.getpid()
        self._used, self._lock = False, Lock()

    def wait(self):
        with self._lock:
            require(not self._used and self.owner == os.getpid() and self.fd >= 0)
            self._used = True
        try:
            remaining = max(0, self.deadline + self.grace + 1 - time.monotonic())
            require(select.select([self.fd], [], [], remaining)[0] == [self.fd])
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            require(pid == self.pid)
            code = os.waitstatus_to_exitcode(status)
            require(code in (0, 10, 11, 12))
            return Outcome(
                self.native_pid,
                self.native_start_ticks,
                self.pid,
                self.ticks,
                self.deadline,
                self.grace,
                code,
            )
        except Exception:
            raise UnconfirmedWatchdog(MESSAGE) from None

    def close(self):
        for attribute in ("native_fd", "fd"):
            fd = getattr(self, attribute)
            if fd >= 0:
                os.close(fd)
                setattr(self, attribute, -1)


def arm(process, *, start_ticks, deadline, grace):
    """Bind an actual owned Popen before releasing its separate launch gate.

    Fork only from a single-threaded guardian. Candidate stdin/stdout/report fds
    are not retained by the watchdog. After native identity is bound, arming
    failure kills that exact native process, never a PID reconstructed later.
    The caller must still reap it and retain all case/filesystem evidence.
    """
    native = guardian = read = write = watch_fd = -1
    watch_pid, committed = None, False
    try:
        require(current_thread() is main_thread() and len(os.listdir("/proc/self/task")) == 1)
        require(type(process) is subprocess.Popen and process._child_created)
        require(type(process.pid) is int and process.pid > 1 and process.returncode is None)
        require(type(start_ticks) is int and start_ticks > 0)
        for value in (deadline, grace):
            require(type(value) in (int, float) and math.isfinite(value))
        require(0 < deadline - time.monotonic() <= MAX_SECONDS and 0 <= grace <= MAX_GRACE)
        native = os.pidfd_open(process.pid)
        parent, ticks = _live(process.pid)
        require(parent == os.getpid() and ticks == start_ticks and not _exited(native))
        committed = True
        guardian = os.pidfd_open(os.getpid())
        read, write = os.pipe2(os.O_CLOEXEC)
        watch_pid = os.fork()
        if watch_pid == 0:
            os._exit(_watch(native, guardian, write, deadline, grace))
        os.close(write)
        write = -1
        watch_fd = os.pidfd_open(watch_pid)
        watch_parent, watch_ticks = _live(watch_pid)
        require(watch_parent == os.getpid() and not _exited(watch_fd))
        ready_by = min(deadline, time.monotonic() + 1)
        require(time.monotonic() < ready_by)
        ready = select.select([read, watch_fd], [], [], ready_by - time.monotonic())[0]
        require(read in ready and os.read(read, 2) == b"1")
        require(time.monotonic() < ready_by and not _exited(native) and not _exited(watch_fd))
        result = Watchdog(
            native_pid=process.pid,
            native_start_ticks=ticks,
            native_fd=native,
            pid=watch_pid,
            ticks=watch_ticks,
            fd=watch_fd,
            deadline=deadline,
            grace=grace,
        )
        native = watch_fd = -1
        return result
    except BaseException as error:
        if committed and native >= 0:
            with suppress(BaseException):
                _signal(native, signal.SIGKILL)
        if watch_pid is not None:
            # The watcher is our other exact child. Do not wait indefinitely if
            # even its startup failed; a live-bound pidfd is required to signal.
            if watch_fd >= 0:
                with suppress(BaseException):
                    _signal(watch_fd, signal.SIGKILL)
                    select.select([watch_fd], [], [], 1)
            with suppress(ChildProcessError):
                os.waitpid(watch_pid, os.WNOHANG)
        if isinstance(error, Exception):
            raise UnconfirmedWatchdog(MESSAGE) from None
        raise
    finally:
        for fd in (native, guardian, read, write, watch_fd):
            if fd >= 0:
                os.close(fd)


if __name__ == "__main__":
    raise SystemExit("Offline exact-process deadline only; no installed launcher is enabled.")
