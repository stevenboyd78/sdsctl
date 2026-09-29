"""Actual original-outer/native lifetime faults in a disposable reaper process.

Reuses the native ingress and the unmodified direct-child Watch owner. This is
NOT systemd/cgroup placement, a qualified launcher, or App permission. The small
kernel fixture has synthetic container identities; the separate full-command
suite supplies original Custody/comparison/command coverage, not installed proof.
"""

import inspect
import json
import subprocess
import sys

import pytest

from . import test_supplemental_native_peer_direct as direct
from . import test_supplemental_native_peer_ingress as ingress

binary, pytestmark = ingress.binary, ingress.pytestmark
direct_launcher = direct.direct_launcher

# Only this disposable driver becomes a subreaper. Pytest's process attributes
# are untouched. All signaling uses retained original pidfds; no process search,
# numeric PID signal, host cgroup/service operation or historical case input.
PROGRAM = r"""
import array, ctypes, fcntl, json, os, select, signal, socket, subprocess, sys, time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_peer_termination as m
assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
binary, mode = Path(sys.argv[2]), sys.argv[3]
transport, library_path = sys.argv[4:6]
base = SimpleNamespace(m=m, OriginalChild=OriginalChild)
ingress_tests = SimpleNamespace(MODE="--offline-original-peer-ingress-v1")
direct_spawn = load_direct(library_path) if transport == "direct-owner" else None
base_fds = len(os.listdir("/proc/self/fd"))
targets, peers, identities = [], [], []
outer = outer_fd = native_pid = native_fd = extra_cancel = None

def stopped_child(fd, deadline):
    # Signal submission is not proof of a stopped process. Only the actual
    # direct parent observes this status, through its original child pidfd.
    while True:
        info = os.waitid(os.P_PIDFD, fd, os.WSTOPPED | os.WNOHANG | os.WNOWAIT)
        if info is not None:
            assert info.si_code == os.CLD_STOPPED and info.si_status == signal.SIGSTOP
            return
        remaining = (deadline - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9
        assert remaining > 0
        select.select([], [], [], min(0.01, remaining))

parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
try:
    for index in range(2):
        peer = subprocess.Popen(["/bin/sleep", "30"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        peers.append(peer)
        targets.append(os.pidfd_open(peer.pid))
        # These are explicitly SYNTHETIC container IDs, not a runtime comparison.
        ident = m.deadlines.links.domains.process.process_identity(
            peer.pid, ("a" if index == 0 else "b") * 64,
            Path(f"/proc/{peer.pid}/stat").read_text(),
            "0::/system.slice/docker-" + ("a" if index == 0 else "b") * 64 + ".scope\n")
        identities.append(ident)
    issued = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    ready_by, recover_by = issued + 2 * 10**9, issued + 4 * 10**9
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
    outer = os.fork()
    if outer == 0:
        # Original Watch is constructed HERE, never borrowed across fork.
        # Peers remain the driver's children; this outer owns only its watch.
        parent.close()
        watch = None
        owned = []
        channels = []
        try:
            def own(fd):
                owned.append(fd)
                return fd
            original_outer = own(os.pidfd_open(os.getpid()))
            ns = own(os.open("/proc/self/ns/time", os.O_RDONLY | os.O_CLOEXEC))
            timer = own(os.timerfd_create(time.CLOCK_BOOTTIME,
                flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK))
            os.timerfd_settime_ns(timer, flags=os.TFD_TIMER_ABSTIME, initial=recover_by)
            cancel_read, cancel_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            ready, ready_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            owned.extend((cancel_read, cancel_write, ready, ready_write))
            sender, receiver = socket.socketpair(socket.AF_UNIX,
                socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC)
            channels.extend((sender, receiver))
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            args = ["--offline-original-peer-ingress-v1", str(recover_by), str(ready_by), boot]
            anchors = [receiver.fileno(), original_outer, ns]
            if direct_spawn is None:
                pid = spawn_standard(binary, anchors, args)
                fd = os.pidfd_open(pid)  # Fresh OWNED spawn, before any loss.
            else:
                original = direct_parent(direct_spawn, binary, anchors, args)
                pid, fd = original.pid, original.fd  # Kernel handle in original outer.
            receiver.close()
            watch = m.Watch(pid, fd, cancel_write, tuple(targets), tuple(identities), recover_by)
            owned.remove(cancel_write)
            # Driver receives the ORIGINAL pidfd; it never reopens reported PID.
            child.sendmsg([json.dumps({"pid": pid, "deadline": recover_by}).encode()],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd, cancel_write]))])
            remaining = (ready_by - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9
            assert remaining > 0
            child.settimeout(remaining)
            assert child.recv(2) == b"G"  # Fixture barrier, NOT placement evidence.
            handles = [*targets, original_outer, cancel_read, ready_write, ns]
            assert sender.sendmsg([b"original-peer-handles-v1"],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", handles))]) == 24
            sender.shutdown(socket.SHUT_WR)
            sender.close()
            os.close(ready_write)
            owned.remove(ready_write)
            remaining = (ready_by - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9
            assert remaining > 0 and select.select([ready, fd], [], [], remaining)[0] == [ready]
            assert os.read(ready, 2) == b"1"
            assert time.clock_gettime_ns(time.CLOCK_BOOTTIME) < ready_by
            child.send(b"R")
            if mode == "native_frozen":
                stopped_child(fd, recover_by)
            # Independent original-outer deadline. A stopped/killed native watch
            # cannot prevent THIS original owner from stopping its retained peers.
            assert select.select([fd, timer], [], [])[0]
            forced = not select.select([fd], [], [], 0)[0]
            if forced:
                assert m._kill_all(targets)
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                assert select.select([fd], [], [], m.RETIRE_SECONDS)[0]
            info = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            assert info is not None
            uncertain, outcome = False, None
            try:
                outcome = watch.finish()
            except m.UnconfirmedTermination:
                uncertain = True
            # Even SIGKILL uncertainty is reaped by the ORIGINAL direct parent.
            assert watch.finished
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            else:
                raise AssertionError("watch not reaped exactly once")
            child.send(json.dumps({"uncertain": uncertain, "forced": forced,
                "code": None if outcome is None else outcome.returncode,
                "native_status": info.si_status, "native_kind": info.si_code,
                "deadline": watch.deadline_ns}).encode())
            watch.close()
            watch = None
            for channel in channels:
                channel.close()
            for item in owned:
                os.close(item)
            child.close()
            os._exit(0)
        except BaseException:
            if watch is not None:
                with suppress(BaseException):
                    watch.close()
            os._exit(75)
    child.close()
    outer_fd = os.pidfd_open(outer)
    remaining = (ready_by - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9
    assert remaining > 0
    parent.settimeout(remaining)
    raw, ancillary, flags, _ = parent.recvmsg(
        1024, socket.CMSG_SPACE(2 * array.array("i").itemsize))
    assert flags == 0 and len(ancillary) == 1
    level, kind, descriptors = ancillary[0]
    assert (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS)
    received = array.array("i")
    received.frombytes(descriptors)
    assert len(received) == 2
    native_fd, extra_cancel = received
    report = json.loads(raw)
    native_pid = report["pid"]
    assert report["deadline"] == recover_by and not select.select([native_fd], [], [], 0)[0]
    if mode == "outer_before_ingress":
        signal.pidfd_send_signal(outer_fd, signal.SIGKILL)
    else:
        parent.send(b"G")
        assert parent.recv(2) == b"R" and time.clock_gettime_ns(time.CLOCK_BOOTTIME) < ready_by
        if mode in ("outer_exit", "outer_frozen"):
            signal.pidfd_send_signal(outer_fd,
                signal.SIGKILL if mode == "outer_exit" else signal.SIGSTOP)
            if mode == "outer_frozen":
                stopped_child(outer_fd, recover_by)
        elif mode in ("native_exit", "native_frozen"):
            signal.pidfd_send_signal(native_fd,
                signal.SIGKILL if mode == "native_exit" else signal.SIGSTOP)
        else:
            raise AssertionError("unknown fixture scenario")
    if mode in ("outer_exit", "outer_before_ingress"):
        assert select.select([outer_fd], [], [], 1)[0]
        assert os.waitstatus_to_exitcode(os.waitpid(outer, 0)[1]) == -signal.SIGKILL
        outer = None  # Only this isolated driver now adopts/reaps the orphan.
        assert select.select([native_fd], [], [], 2)[0]
        code = os.waitstatus_to_exitcode(os.waitpid(native_pid, 0)[1])
        native_pid = None
        assert code == (64 if mode == "outer_before_ingress" else 12)
        if mode == "outer_before_ingress":
            # No authenticated target handoff: native must not acquire target
            # authority. Independent original fixture owner retires its peers.
            assert not any(select.select([fd], [], [], 0)[0] for fd in targets)
            assert m._kill_all(targets)
        else:
            assert time.clock_gettime_ns(time.CLOCK_BOOTTIME) < recover_by
    else:
        remaining = max(0, (recover_by - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9)
        if mode == "outer_frozen":
            assert select.select([native_fd], [], [], remaining + 1)[0]
            # Still the live outer's child, NOT ours. A pidfd is not reap authority.
            try:
                os.waitpid(native_pid, os.WNOHANG)
            except ChildProcessError:
                pass
            else:
                raise AssertionError("non-parent unexpectedly reaped native")
            assert all(select.select([fd], [], [], 1)[0] for fd in targets)
            signal.pidfd_send_signal(outer_fd, signal.SIGCONT)
        # Only observe/reap after cutoff; never restart the readiness/live budget.
        observe_remaining = (recover_by + 2 * 10**9
            - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9
        assert observe_remaining > 0
        parent.settimeout(observe_remaining)
        result = json.loads(parent.recv(1024))
        assert result["deadline"] == recover_by
        if mode == "outer_frozen":
            assert result == dict(uncertain=False, forced=False, code=10,
                native_status=10, native_kind=os.CLD_EXITED, deadline=recover_by)
        else:
            assert result == dict(uncertain=True, forced=mode == "native_frozen", code=None,
                native_status=signal.SIGKILL, native_kind=os.CLD_KILLED, deadline=recover_by)
        assert select.select([outer_fd], [], [], 1)[0]
        assert os.waitstatus_to_exitcode(os.waitpid(outer, 0)[1]) == 0
        outer = native_pid = None  # Native was already reaped by original outer.
    assert all(peer.wait(timeout=1) == -signal.SIGKILL for peer in peers)
    print(json.dumps({"mode": mode, "passed": True}), flush=True)
finally:
    # Exact retained test handles only. The driver never acts on live resources.
    for fd in [*targets, native_fd, outer_fd]:
        if fd is not None:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
    for pid in (outer, native_pid):
        if pid is not None:
            with suppress(ChildProcessError):
                os.waitpid(pid, 0)
    for peer in peers:
        peer.wait(timeout=2)
    for fd in [*targets, native_fd, outer_fd, extra_cancel]:
        if fd is not None:
            os.close(fd)
    parent.close()
    child.close()
    assert len(os.listdir("/proc/self/fd")) == base_fds
"""


@pytest.mark.parametrize(
    "mode", ["outer_before_ingress", "outer_exit", "outer_frozen", "native_exit", "native_frozen"]
)
@pytest.mark.parametrize("transport", ["standard", "direct-owner"])
def test_actual_outer_and_native_failure_domains_preserve_original_ownership(
    binary, mode, transport, direct_launcher
):
    # Existing collision-safe exec mapping, unchanged. No fork-owned Python
    # application object is passed to the native process or another owner.
    program = (
        "import os, fcntl, signal, ctypes\nfrom dataclasses import dataclass\n"
        + inspect.getsource(ingress.base.OriginalChild)
        + inspect.getsource(ingress.spawn_standard)
        + inspect.getsource(direct.load_direct)
        + inspect.getsource(direct.direct_parent)
        + PROGRAM
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            program,
            str(ingress.base.SOURCE.parent.parent),
            str(binary),
            mode,
            transport,
            direct_launcher.fixture_library_path,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env={},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"mode": mode, "passed": True}
