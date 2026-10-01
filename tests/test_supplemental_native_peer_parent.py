"""Actual clone3 original-parent/PIDFD join, NOT a cgroup/platform launcher.

The extra native process creates a sibling with CLONE_PARENT and passes the
kernel-created handle to its original parent. The explicitly selected same-group
variant exercises CLONE_INTO_CGROUP with a retained FD for the caller's EXISTING
group; it does not create/move a group or qualify outside-freeze-domain placement.
All source/runtime/Engine facts remain synthetic; no installed command selects
this fixture and no numeric-PID/old-clone fallback is permitted.
"""

import array
import errno
import fcntl
import os
import select
import signal
import socket
import struct
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import pytest

from . import test_supplemental_native_peer_ingress as ingress_tests

base = ingress_tests.base
layout, image_umask, supervised = base.layout, base.image_umask, base.supervised
image, configured, pair = base.image, base.configured, base.pair
helper, inputs, custody = base.helper, base.inputs, base.custody
binary, pytestmark, short_budget = base.binary, base.pytestmark, base.short_budget


@pytest.fixture(scope="module")
def parent_launcher(tmp_path_factory, request):
    source = Path(__file__).parent / "fixtures/native_parent_spawn.c"
    output = tmp_path_factory.mktemp("native-parent-fixture") / "parent-spawn"
    variant = getattr(request, "param", None)
    assert variant in (None, "exit-after-report", "same-cgroup")
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
            "-static-pie",
            "-Wl,-z,relro,-z,now",
            *(["-DFIXTURE_REPORT_FAILURE=1"] if variant == "exit-after-report" else []),
            *(["-DFIXTURE_SAME_CGROUP=1"] if variant == "same-cgroup" else []),
            str(source),
            "-o",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return output


def clone_parent(cloner, binary, anchors, args, *, extra=(), cgroup_fd=None):
    """TEST-ONLY broker -> original child handle, under the original cutoff.

    The original parent authenticates its directly spawned fixture sender and
    reaps that sender separately. Watch must reap the native child itself.
    The broker's reported ECHILD is corroborated by our own waitid, not trusted
    as standalone owner/exit/source evidence. No reported PID is reopened.
    """
    assert not extra and args[0] == ingress_tests.MODE
    ready_by = int(args[2])
    before = len(os.listdir("/proc/self/fd"))
    parent, child = socket.socketpair(
        socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC
    )
    parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    copies, opened, received = [], [], []
    launcher_pid = launcher_fd = original = None
    transferred = False

    def remaining():
        left = (ready_by - time.clock_gettime_ns(time.CLOCK_BOOTTIME)) / 1e9
        assert left > 0, "original clone-parent startup cutoff expired"
        return left

    try:
        for path in (binary, cloner):
            opened.append(os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW))
        handles = [*anchors, opened[0], child.fileno(), opened[1]]
        slots = [0, 1, 2, 9, 10, 11]
        if cgroup_fd is not None:
            handles.append(cgroup_fd)
            slots.append(12)
        for fd in handles:
            copies.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 32))
        remaining()
        launcher_pid = os.posix_spawn(
            "/proc/self/fd/11",
            [str(cloner), "--offline-parent-spawn-fixture-v1", *args],
            {},
            file_actions=[
                (os.POSIX_SPAWN_DUP2, fd, slot) for fd, slot in zip(copies, slots, strict=True)
            ],
            setsigmask=(),
            setsigdef=(signal.SIGCHLD,),
        )
        launcher_fd = os.pidfd_open(launcher_pid)  # Fresh OWNED spawn, not a reported PID.
        child.close()
        assert select.select([parent], [], [], remaining())[0]
        payload, ancillary, flags, _ = parent.recvmsg(
            9, socket.CMSG_SPACE(4) + socket.CMSG_SPACE(12), socket.MSG_CMSG_CLOEXEC
        )
        # Close received rights even if later packet authentication fails.
        credentials = []
        for level, kind, raw in ancillary:
            assert level == socket.SOL_SOCKET
            if kind == socket.SCM_RIGHTS:
                rights = array.array("i")
                rights.frombytes(raw)
                received.extend(rights)
            else:
                assert kind == socket.SCM_CREDENTIALS and len(raw) == 12
                credentials.append(struct.unpack("iII", raw))
        assert flags & ~socket.MSG_CMSG_CLOEXEC == 0
        assert len(payload) == 8 and len(ancillary) == 2 and len(received) == 1
        assert credentials == [(launcher_pid, os.geteuid(), os.getegid())]
        pid, cannot_reap = struct.unpack("=II", payload)
        fd = received[0]
        assert cannot_reap == errno.ECHILD
        assert base.m.deadlines._fdinfo(fd).get(b"Pid") == str(pid).encode("ascii")
        assert not os.get_inheritable(fd)
        # Kernel verifies that the native is OUR direct child; not a proxy Watch.
        state = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        original = base.OriginalChild(pid, fd)
        assert state is None  # An exited original is still ours to retire/reap.
        assert select.select([parent], [], [], remaining())[0]
        assert parent.recvmsg(1, 128, socket.MSG_CMSG_CLOEXEC)[:2] == (b"", [])
        assert select.select([launcher_fd], [], [], remaining())[0]
        found, status = os.waitpid(launcher_pid, os.WNOHANG)
        assert found == launcher_pid
        launcher_pid = None
        assert os.waitstatus_to_exitcode(status) == 0
        remaining()
        transferred = True
        return original
    finally:
        if original is not None and not transferred:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(original.fd, signal.SIGKILL)
            assert select.select([original.fd], [], [], base.m.RETIRE_SECONDS)[0]
            assert os.waitpid(original.pid, os.WNOHANG)[0] == original.pid
        if launcher_pid is not None and launcher_fd is not None:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(launcher_fd, signal.SIGKILL)
            assert select.select([launcher_fd], [], [], base.m.RETIRE_SECONDS)[0]
            assert os.waitpid(launcher_pid, os.WNOHANG)[0] == launcher_pid
        for fd in [*copies, *opened, *([] if launcher_fd is None else [launcher_fd])]:
            os.close(fd)
        for fd in received:
            if not transferred or original is None or fd != original.fd:
                os.close(fd)
        parent.close()
        child.close()
        assert len(os.listdir("/proc/self/fd")) == before + int(transferred)


def parent_ingress(custody, binary, cloner, *, cgroup_fd=None):
    return ingress_tests.ingress(
        custody,
        binary,
        after_exec=True,
        launcher=lambda binary, anchors, args, **kw: clone_parent(
            cloner, binary, anchors, args, cgroup_fd=cgroup_fd, **kw
        ),
    )


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
def test_original_clone_pidfd_is_direct_child_without_reopen(
    custody, pair, binary, parent_launcher, monkeypatch
):
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff = custody.plan.raw, custody.deadline_ns
    opened, pidfd_open = [], os.pidfd_open

    def record(pid, *args):
        opened.append(pid)
        return pidfd_open(pid, *args)

    monkeypatch.setattr(os, "pidfd_open", record)
    with parent_ingress(custody, binary, parent_launcher) as watch:
        assert watch.pid not in opened  # Received CLONE_PIDFD, never PID reopened.
        assert watch.identities is custody.identities and watch.deadline_ns == cutoff
        assert custody.plan.raw == raw
        assert not any(w.exited() for w in pair.witnesses.values())
        os.write(watch.cancel, b"X")
        assert base.native_code(watch) == 13
        base.termination.exited(pair)
        assert watch.finish().returncode == 13
        with pytest.raises(ChildProcessError):
            os.waitpid(watch.pid, os.WNOHANG)
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
@pytest.mark.parametrize("parent_launcher", ["same-cgroup"], indirect=True)
def test_clone_birth_into_retained_existing_group_is_not_independent_placement(
    custody, pair, binary, parent_launcher
):
    # Read-only capture of THIS local fixture's existing cgroup. No mkdir, move,
    # freeze, service, namespace creation, delegation or host permission changes.
    current = Path("/proc/self/cgroup").read_text()
    assert current.startswith("0::/") and current.count("\n") == 1
    relative = Path(current[3:].strip()).relative_to("/")
    assert ".." not in relative.parts
    target = Path("/sys/fs/cgroup") / relative
    if not os.access(target / "cgroup.procs", os.W_OK):
        pytest.skip("No write authority in own current cgroup; no placement fallback")
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    identity = os.fstat(fd)
    try:
        with parent_ingress(custody, binary, parent_launcher, cgroup_fd=fd) as watch:
            assert Path("/proc/self/cgroup").read_text() == current
            assert os.fstat(fd) == identity
            os.write(watch.cancel, b"X")
            assert base.native_code(watch) == 13
            base.termination.exited(pair)
            assert watch.finish().returncode == 13
    finally:
        os.close(fd)


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
@pytest.mark.parametrize("parent_launcher", ["exit-after-report"], indirect=True)
def test_failed_exporter_preserves_original_attempt_and_stops_without_native_readiness(
    custody, pair, binary, parent_launcher
):
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff = custody.plan.raw, custody.deadline_ns
    with pytest.raises(AssertionError), parent_ingress(custody, binary, parent_launcher):
        pytest.fail("Failed launcher released native readiness")
    assert custody.attempted and custody.armed_watch is None
    assert custody.plan.raw == raw and custody.deadline_ns == cutoff
    base.termination.exited(pair)
    with pytest.raises(AssertionError), parent_ingress(custody, binary, parent_launcher):
        pytest.fail("Failed original attempt retried")
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
@pytest.mark.parametrize("parent_launcher", ["same-cgroup"], indirect=True)
def test_non_cgroup_directory_refuses_before_clone_without_fallback(
    custody, pair, binary, parent_launcher, tmp_path
):
    before = len(os.listdir("/proc/self/fd"))
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        with (
            pytest.raises(AssertionError),
            parent_ingress(custody, binary, parent_launcher, cgroup_fd=fd),
        ):
            pytest.fail("Unqualified directory enabled a fallback launch")
        assert custody.attempted and custody.armed_watch is None
        base.termination.exited(pair)
    finally:
        os.close(fd)
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
@pytest.mark.parametrize("transport", ["direct", "parent"])
def test_ingress_send_failure_after_owned_spawn_still_reaps_known_child(
    custody, pair, binary, parent_launcher, monkeypatch, transport
):
    before = len(os.listdir("/proc/self/fd"))
    children, attempts = [], []
    sendmsg = socket.socket.sendmsg

    def launcher(binary, anchors, args, **kw):
        original = (
            clone_parent(parent_launcher, binary, anchors, args, **kw)
            if transport == "parent"
            else ingress_tests.spawn_standard(binary, anchors, args, **kw)
        )
        children.append(original)
        return original

    def fail_native_transfer(channel, buffers, *args):
        if buffers == [ingress_tests.PACKET]:
            attempts.append(True)
            raise BrokenPipeError("Disposable original ingress fault")
        return sendmsg(channel, buffers, *args)

    monkeypatch.setattr(socket.socket, "sendmsg", fail_native_transfer)
    with (
        pytest.raises(BrokenPipeError, match="Disposable original ingress fault"),
        ingress_tests.ingress(custody, binary, after_exec=True, launcher=launcher),
    ):
        pytest.fail("Failed native transfer released readiness")
    assert len(children) == len(attempts) == 1
    assert custody.attempted and custody.armed_watch is None
    base.termination.exited(pair)
    with pytest.raises(ChildProcessError):
        child = children[0]
        os.waitpid(child.pid if type(child) is base.OriginalChild else child, os.WNOHANG)
    assert len(os.listdir("/proc/self/fd")) == before
