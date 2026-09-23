#!/usr/bin/env python3
"""Fixed local native-child owner; uninstalled, no public operator transport.

The guardian, interpreter/image and expected pins must already be authenticated
by the independent host. Matching a self-supplied digest is not authorization.
This owner consumes a private launch claim, launches only the fixed child, arms
the independent watchdog before gate release, and separately reaps its child.
It never restores another scanner owner or treats a file as a native return.
"""

from __future__ import annotations

import importlib.util
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import current_thread, main_thread

import supplemental_recording_control as control
import supplemental_recording_source as source
import supplemental_recording_watchdog as watchdog

MESSAGE = "Finite recording guardian is unconfirmed; preserve this case and do not retry."
GRACE_SECONDS = 3


class UnconfirmedGuardian(ValueError):
    """Native/container exit and recording acknowledgment stay separate facts."""


def require(value):
    if not value:
        raise UnconfirmedGuardian(MESSAGE)


@dataclass(frozen=True)
class Exit:
    pid: int
    start_ticks: int
    returncode: int
    reaped_at: float
    watchdog: watchdog.Outcome


def _origins(layout):
    # An isolated guardian and child use the same installed Python environment.
    # No source-root/PYTHONPATH override or alternate executable is accepted.
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1)
    require(Path(__file__) == layout.native / "supplemental_recording_guardian.py")
    runtime = importlib.util.find_spec("sds200")
    require(runtime is not None and runtime.origin == str(layout.runtime / "__init__.py"))
    for name in source.MODULES:
        module = sys.modules.get(name)
        if module is not None:
            require(getattr(module, "__file__", None) == str(layout.native / (name + ".py")))
    require(Path(sys.executable).is_absolute() and ".." not in Path(sys.executable).parts)


def _claim(directory, context, evidence, deadline):
    """Consume an existing empty private case before Popen; never erase on error."""
    p = context.plan
    for other in (
        p.stored.baseline.root,
        p.specification.sockets,
        p.specification.receipts,
        p.configuration.source_path,
        p.configuration.state_directory,
    ):
        control.launch.construction._disjoint(directory, other)
    raw = control.encode(
        {
            "schema": 1,
            "kind": "finite-recording-guardian-claim",
            "context": context.payload(),
            "source": asdict(evidence),
            "guardian_pid": os.getpid(),
            "guardian_start_ticks": control.returns._identity(os.getpid())[1],
            "hard_deadline": deadline,
            "grace_seconds": GRACE_SECONDS,
        }
    )
    require(len(raw) <= 8192)
    with control.launch.protected._private_directory(directory, exclusive=True) as fd:
        with os.scandir(fd) as entries:
            require(next(entries, None) is None)
        output = os.open(
            "launch-claimed.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=fd,
        )
        with os.fdopen(output, "wb") as stream:
            require(stream.write(raw) == len(raw))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)


class Session:
    """One native process and one ready/begin sequence, never a replacement PID.

    Use open_session(), not construction with a caller-supplied process. Begin
    requires a separately durable, source-qualified host intent; no method here
    authenticates an operator. close() cancels an unfinished child and reaps it,
    but cannot manufacture a recording success or container-init exit witness.
    """

    def __init__(self, process, channels, context, ticks):
        self.process, self.channels, self.context = process, channels, context
        self.ticks, self.owner = ticks, os.getpid()
        self.native_fd = os.pidfd_open(process.pid)
        self.parent = self.watch = self.receiver = self.exit = None
        self.phase, self.closed = "ready", False
        try:
            require(control.returns._identity(process.pid) == (self.owner, ticks))
            require(not select.select([self.native_fd], [], [], 0)[0])
        except BaseException:
            # No Session is returned to the caller on constructor failure.
            # Retain Popen's ownership for outer cleanup, but don't leak this fd.
            os.close(self.native_fd)
            self.native_fd = -1
            raise

    def _owner(self):
        require(not self.closed and self.owner == os.getpid())

    def _wait_message(self, deadline):
        self._owner()
        require(self.watch is not None and self.watch.fd >= 0)
        require(time.monotonic() < deadline)
        fds = (self.channels.incoming, self.native_fd, self.watch.fd)
        ready = select.select(fds, [], [], deadline - time.monotonic())[0]
        require(time.monotonic() < deadline)
        # A watcher may exit normally only after native exit. If it dies while
        # native is alive, immediately fail closed even if a report is queued.
        require(self.watch.fd not in ready or self.native_fd in ready)
        require(self.channels.incoming in ready)

    def receive_ready(self):
        try:
            self._owner()
            require(self.phase == "ready")
            self.phase = "unconfirmed"
            self._wait_message(self.context.ready_by)
            received = self.parent.receive_ready()
            self.phase = "begin"
            return received
        except BaseException:
            self.close()
            raise UnconfirmedGuardian(MESSAGE) from None

    def begin(self, binding, *, intent_at, intent_sha256):
        try:
            self._owner()
            require(self.phase == "begin" and type(binding) is control.returns.Binding)
            self.phase = "unconfirmed"
            require(binding.finish_by <= self.watch.deadline)
            require(not select.select([self.watch.fd, self.native_fd], [], [], 0)[0])
            self.receiver = self.parent.begin(
                binding, intent_at=intent_at, intent_sha256=intent_sha256
            )
            self.phase = "started"
        except BaseException:
            self.close()
            raise UnconfirmedGuardian(MESSAGE) from None

    def receive(self):
        try:
            self._owner()
            require(self.phase in ("started", "completed") and self.receiver is not None)
            phase, self.phase = self.phase, "unconfirmed"
            deadline = (
                self.receiver.binding.start_by
                if phase == "started"
                else min(self.receiver.binding.finish_by, self.receiver.plan.finish_by)
            )
            self._wait_message(deadline)
            received = self.receiver.receive()
            self.phase = "completed" if phase == "started" else "returned"
            return received
        except BaseException:
            self.close()
            raise UnconfirmedGuardian(MESSAGE) from None

    def wait(self):
        """Observe Popen exit independently of native messages and watcher action."""
        try:
            self._owner()
            require(self.exit is None and self.watch is not None)
            deadline = self.watch.deadline + self.watch.grace + 1
            while not select.select([self.native_fd], [], [], 0)[0]:
                require(time.monotonic() < deadline)
                ready = select.select(
                    [self.native_fd, self.watch.fd], [], [], deadline - time.monotonic()
                )[0]
                require(self.native_fd in ready)  # Early watchdog death is not native exit.
            code = self.process.wait(timeout=0)
            reaped_at = time.monotonic()
            outcome = self._watch_outcome()
            self.exit = Exit(self.process.pid, self.ticks, code, reaped_at, outcome)
            return self.exit
        except BaseException:
            self.close()
            raise UnconfirmedGuardian(MESSAGE) from None

    def _watch_outcome(self):
        # Native exit has already been independently reaped. A frozen watcher
        # must not make cleanup wait out the entire original readiness window.
        # Bound and reap only this owned watcher; a forced exit is unconfirmed,
        # never a successful watchdog outcome or permission to restore ownership.
        if not select.select([self.watch.fd], [], [], 3)[0]:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(self.watch.fd, signal.SIGKILL)
            require(select.select([self.watch.fd], [], [], 3)[0])
        return self.watch.wait()

    def close(self):
        if self.closed:
            return
        require(self.owner == os.getpid())
        self.closed, self.phase = True, "closed"
        try:
            # No repeat stop/finalize operation and no PID lookup after failure.
            if not select.select([self.native_fd], [], [], 0)[0]:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(self.native_fd, signal.SIGKILL)
            require(select.select([self.native_fd], [], [], 3)[0])
            self.process.wait(timeout=0)
            if self.watch is not None and not self.watch._used:
                self._watch_outcome()
        except Exception:
            raise UnconfirmedGuardian(MESSAGE) from None
        finally:
            for owned in (self.receiver, self.parent, self.watch, self.channels):
                if owned is not None:
                    owned.close()
            os.close(self.native_fd)
            self.native_fd = -1


@contextmanager
def open_session(layout, path, *, plan_sha256, source_sha256, ready_by):
    """Launch only the reviewed fixed child, never an arbitrary argv/callback.

    The host must first authenticate this guardian/interpreter/image and expected
    pins and exact launch.json path. The private durable claim prevents reuse,
    including after lost returns. Its existing directory is fixed beside the
    pinned plan; callers cannot choose another claim namespace to retry.
    This context does not install a service, start a recorder or open a public API.
    """
    left = right = session = process = None
    read = write = -1
    try:
        require(current_thread() is main_thread() and len(os.listdir("/proc/self/task")) == 1)
        require(type(layout) is source.Layout)
        _origins(layout)
        evidence = layout.verify(source_sha256)
        plan = control.launch.load(
            path, expected_sha256=plan_sha256, expected_source_sha256=source_sha256
        )
        case_directory = path.parent / "guardian"
        context = control.Context(plan, ready_by)
        context.payload()
        require(0 < ready_by - time.monotonic() <= plan.specification.ready_timeout)
        deadline = ready_by + plan.stored.contract.maximum_recording_seconds
        for root in (layout.native, layout.runtime):
            control.launch.construction._disjoint(case_directory, root)
        _claim(case_directory, context, evidence, deadline)
        left, right = control.pair()
        read, write = os.pipe2(os.O_CLOEXEC)
        fields = {
            "plan": str(path),
            "plan-sha256": plan_sha256,
            "source-sha256": source_sha256,
            "incoming-fd": right.incoming.fileno(),
            "outgoing-fd": right.outgoing.fileno(),
            "gate-fd": read,
            "parent-pid": os.getpid(),
            "parent-ticks": control.returns._identity(os.getpid())[1],
            "parent-uid": os.geteuid(),
            "parent-gid": os.getegid(),
            "ready-by": ready_by,
        }
        args = [sys.executable, "-I", "-B", str(layout.native / "accept_supplemental_recording.py")]
        args += [part for name, value in fields.items() for part in ("--" + name, str(value))]
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/bin:/bin"},
            close_fds=True,
            pass_fds=(right.incoming.fileno(), right.outgoing.fileno(), read),
        )
        right.close()
        right = None
        os.close(read)
        read = -1
        ticks = control.returns._identity(process.pid)[1]
        session = Session(process, left, context, ticks)
        session.parent = control.Parent(
            left, context, pid=process.pid, start_ticks=ticks, uid=os.geteuid(), gid=os.getegid()
        )
        session.watch = watchdog.arm(
            process, start_ticks=ticks, deadline=deadline, grace=GRACE_SECONDS
        )
        require(layout.verify(source_sha256) == evidence)
        require(
            control.launch.load(
                path, expected_sha256=plan_sha256, expected_source_sha256=source_sha256
            )
            == plan
        )
        require(time.monotonic() < ready_by)
        require(not select.select([session.native_fd, session.watch.fd], [], [], 0)[0])
        require(os.write(write, b"1") == 1)
        os.close(write)
        write = -1
        yield session
    except Exception:
        raise UnconfirmedGuardian(MESSAGE) from None
    finally:
        for fd in (read, write):
            if fd >= 0:
                os.close(fd)
        try:
            if session is not None:
                session.close()
            elif process is not None:
                # Only this fresh Popen, still held at its gate. Popen owns its
                # unreaped child identity; never reconstruct from a saved PID.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
        finally:
            for channels in (left, right):
                if channels is not None:
                    channels.close()


if __name__ == "__main__":
    raise SystemExit("Private local guardian only; installed host/operator relay is not enabled.")
