"""Direct original-owner clone/PIDFD joins; no helper report or installed proof.

The small C-only child path execs the existing native watcher; it never resumes
Python after clone. Source/runtime/Engine/placement remain synthetic. The local
shared fixture is neither an installed library nor a new production selector.
"""

import ctypes
import fcntl
import os
import select
import signal
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
short_budget, binary, pytestmark = base.short_budget, base.binary, base.pytestmark


@pytest.fixture(scope="module")
def direct_launcher(tmp_path_factory):
    source = Path(__file__).parent / "fixtures/native_direct_spawn.c"
    output = tmp_path_factory.mktemp("native-direct-fixture") / "direct-spawn.so"
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
            "-fPIC",
            "-shared",
            "-Wl,-z,relro,-z,now",
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
    spawn = load_direct(output)
    spawn.fixture_library_path = str(output)
    return spawn


def load_direct(path):
    library = ctypes.CDLL(str(path), use_errno=True)
    spawn = library.sds_fixture_spawn
    spawn.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    spawn.restype = ctypes.c_int
    return spawn


def direct_parent(
    spawn, binary, anchors, args, *, extra=(), cgroup_fd=None, fault=None, observed=None
):
    """Original caller owns kernel outputs even if return/adoption fails."""
    assert not extra and len(anchors) == 3 and len(args) == 4
    assert args[0] == ingress_tests.MODE and fault in (None, "return", "interrupt", "pending")
    cutoff = int(args[2])
    before = len(os.listdir("/proc/self/fd"))
    result = (ctypes.c_int * 2)(-1, -1)
    executable = os.open(binary, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    copies, transferred = [], False
    try:
        for fd in (*anchors, executable):
            copies.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 32))
        assert time.clock_gettime_ns(time.CLOCK_BOOTTIME) < cutoff
        code = spawn(
            result,
            (ctypes.c_int * 4)(*copies),
            *(arg.encode("ascii") for arg in args[1:]),
            -1 if cgroup_fd is None else cgroup_fd,
            int(fault == "return") + 2 * int(fault == "pending"),
        )
        if fault == "interrupt":
            raise KeyboardInterrupt("Disposable interruption before handle adoption")
        assert code == 0
        pid, fd = result
        assert pid > 0 and fd >= 0 and not os.get_inheritable(fd)
        assert base.m.deadlines._fdinfo(fd).get(b"Pid") == str(pid).encode("ascii")
        assert os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
        assert time.clock_gettime_ns(time.CLOCK_BOOTTIME) < cutoff
        original = base.OriginalChild(pid, fd)
        transferred = True
        return original
    finally:
        # This original memory is available even if the C call returns an
        # error or Python is interrupted BEFORE constructing OriginalChild.
        if observed is not None:
            observed.append(tuple(result))
        if result[1] >= 0 and not transferred:
            try:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(result[1], signal.SIGKILL)
                assert select.select([result[1]], [], [], base.m.RETIRE_SECONDS)[0]
                info = os.waitid(os.P_PIDFD, result[1], os.WEXITED | os.WNOHANG)
                assert info is not None and (result[0] == -1 or info.si_pid == result[0])
            finally:
                os.close(result[1])
        for fd in copies:
            os.close(fd)
        os.close(executable)
        assert len(os.listdir("/proc/self/fd")) == before + int(transferred)


def direct_ingress(custody, binary, spawn, **options):
    return ingress_tests.ingress(
        custody,
        binary,
        after_exec=True,
        launcher=lambda binary, anchors, args, **kw: direct_parent(
            spawn, binary, anchors, args, **options, **kw
        ),
    )


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
def test_direct_kernel_handle_stays_with_original_custody_and_watch(
    custody, pair, binary, direct_launcher, monkeypatch
):
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff = custody.plan.raw, custody.deadline_ns
    opened, pidfd_open = [], os.pidfd_open

    def record(pid, *args):
        opened.append(pid)
        return pidfd_open(pid, *args)

    monkeypatch.setattr(os, "pidfd_open", record)
    with direct_ingress(custody, binary, direct_launcher) as watch:
        assert watch.pid not in opened
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
@pytest.mark.parametrize("fault", ["return", "interrupt", "pending"])
def test_direct_child_accounted_before_adoption_without_discovery_or_retry(
    custody, pair, binary, direct_launcher, fault
):
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff = custody.plan.raw, custody.deadline_ns
    observed = []
    error = AssertionError if fault == "return" else KeyboardInterrupt
    original_handler = signal.getsignal(signal.SIGUSR1)

    def pending(signum, frame):
        raise KeyboardInterrupt("Native caller interruption before ctypes returned")

    try:
        if fault == "pending":
            signal.signal(signal.SIGUSR1, pending)
        with (
            pytest.raises(error),
            direct_ingress(custody, binary, direct_launcher, fault=fault, observed=observed),
        ):
            pytest.fail("Failed direct spawn admitted readiness")
    finally:
        signal.signal(signal.SIGUSR1, original_handler)
    assert len(observed) == 1 and all(value >= 0 for value in observed[0])
    with pytest.raises(ChildProcessError):
        os.waitpid(observed[0][0], os.WNOHANG)
    assert custody.attempted and custody.armed_watch is None
    assert custody.plan.raw == raw and custody.deadline_ns == cutoff
    base.termination.exited(pair)
    with pytest.raises(AssertionError), direct_ingress(custody, binary, direct_launcher):
        pytest.fail("Consumed original custody retried")
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
def test_direct_invalid_cgroup_refuses_before_any_child_without_fallback(
    custody, pair, binary, direct_launcher, tmp_path
):
    observed = []
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        with (
            pytest.raises(AssertionError),
            direct_ingress(custody, binary, direct_launcher, cgroup_fd=fd, observed=observed),
        ):
            pytest.fail("Invalid cgroup allowed fallback creation")
        assert observed == [(-1, -1)]
        assert custody.attempted and custody.armed_watch is None
        base.termination.exited(pair)
    finally:
        os.close(fd)


@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
def test_direct_birth_in_existing_group_preserves_owner_not_independent_placement(
    custody, pair, binary, direct_launcher
):
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
        with direct_ingress(custody, binary, direct_launcher, cgroup_fd=fd) as watch:
            assert Path("/proc/self/cgroup").read_text() == current
            assert os.fstat(fd) == identity
            os.write(watch.cancel, b"X")
            assert base.native_code(watch) == 13
            base.termination.exited(pair)
            assert watch.finish().returncode == 13
    finally:
        os.close(fd)
