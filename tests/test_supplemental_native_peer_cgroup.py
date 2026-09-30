"""OPT-IN local disposable cgroup freeze; never installed/platform qualification.

Only fresh children are born into new groups. No existing process is migrated,
no existing control is written, and the caller's scope is NEVER frozen. One
fault keeps the original lifetime driver/outer and guardian outside a watcher
subtree. A distinct fault puts the disposable outer and peers in a fresh subtree
while its direct-child watcher and two cleanup owners remain outside. An explicit
opt-in requires user/delegation authority; CI success or writable cgroup files
are not that authority.
"""

import array
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest

from . import test_supplemental_native_peer_lifetime as lifetime

binary, direct_launcher = lifetime.binary, lifetime.direct_launcher
base = lifetime.ingress.base
layout, image_umask, supervised = base.layout, base.image_umask, base.supervised
image, configured, pair = base.image, base.configured, base.pair
helper, inputs, custody = base.helper, base.inputs, base.custody
short_budget = base.short_budget
pytestmark = [
    lifetime.pytestmark,
    pytest.mark.skipif(
        os.environ.get("SDSCTL_OFFLINE_CGROUP_TEST") != "1",
        reason="Explicit local-only disposable cgroup test approval required",
    ),
]
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def read_control(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        raw = os.read(fd, 8193)
        assert len(raw) <= 8192
        return raw.decode("ascii")
    finally:
        os.close(fd)


def events(directory):
    return dict(line.split() for line in read_control(directory, "cgroup.events").splitlines())


def boot_ns():
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


@contextmanager
def fresh_group(leaf_name="native"):
    """Retained exact directories; empty-only removal, no recovery-by-group-kill."""
    assert leaf_name in ("native", "outer")
    original = Path("/proc/self/cgroup").read_text()
    assert original.startswith("0::/") and original.count("\n") == 1
    relative = Path(original[3:].strip()).relative_to("/")
    assert ".." not in relative.parts
    caller_path = Path("/sys/fs/cgroup") / relative
    caller = os.open(caller_path, DIR)
    name = "sdsctl-offline-freeze-" + uuid.uuid4().hex
    root = leaf = None
    root_created = leaf_created = False
    try:
        assert os.fstat(caller).st_uid == os.getuid()
        assert read_control(caller, "cgroup.type") == "domain\n"
        # Never change controllers, subtree settings, or the caller's membership.
        assert read_control(caller, "cgroup.subtree_control").strip() == ""
        os.mkdir(name, mode=0o700, dir_fd=caller)
        root_created = True
        root = os.open(name, DIR, dir_fd=caller)
        os.mkdir(leaf_name, mode=0o700, dir_fd=root)
        leaf_created = True
        leaf = os.open(leaf_name, DIR, dir_fd=root)
        for directory in (root, leaf):
            assert os.fstat(directory).st_uid == os.getuid()
            assert os.fstat(directory).st_dev == os.fstat(caller).st_dev
            assert read_control(directory, "cgroup.procs") == ""
            assert events(directory) == {"populated": "0", "frozen": "0"}
        yield root, leaf, "/" + str(relative / name / leaf_name), original
    finally:
        # If exact cleanup failed, retain populated groups and fail visibly.
        # There is no recursive removal, ancestor thaw, or cgroup.kill fallback.
        try:
            if leaf is not None:
                assert read_control(leaf, "cgroup.procs") == ""
                assert events(leaf)["populated"] == "0"
            if leaf_created:
                os.rmdir(leaf_name, dir_fd=root)
            if root is not None:
                assert read_control(root, "cgroup.procs") == ""
                assert events(root)["populated"] == "0"
            if root_created:
                os.rmdir(name, dir_fd=caller)
            assert Path("/proc/self/cgroup").read_text() == original
        finally:
            for fd in (leaf, root, caller):
                if fd is not None:
                    os.close(fd)


def freeze_fresh(root, leaf, cutoff):
    # These are opened from freshly created retained roots, never caller_path.
    fd = os.open("cgroup.freeze", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
    try:
        assert boot_ns() < cutoff
        assert os.write(fd, b"1") == 1
    finally:
        os.close(fd)
    while events(root)["frozen"] != "1" or events(leaf)["frozen"] != "1":
        assert boot_ns() < cutoff, "Freeze not observed within original readiness budget"
        select.select([], [], [], 0.001)
    assert boot_ns() < cutoff
    assert events(root)["populated"] == events(leaf)["populated"] == "1"


def original_handoff(channel, driver, owned):
    raw, ancillary, flags, _ = channel.recvmsg(
        1024,
        socket.CMSG_SPACE(4 * array.array("i").itemsize) + socket.CMSG_SPACE(struct.calcsize("3i")),
        socket.MSG_CMSG_CLOEXEC,
    )
    credentials = []
    rights_count = 0
    # Own all received descriptors before validating anything else.
    for level, kind, data in ancillary:
        if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
            rights = array.array("i")
            rights.frombytes(data)
            owned.extend(rights)
            rights_count += 1
    for level, kind, data in ancillary:
        assert level == socket.SOL_SOCKET
        assert kind in (socket.SCM_RIGHTS, socket.SCM_CREDENTIALS)
        if kind == socket.SCM_CREDENTIALS:
            credentials.append(struct.unpack("3i", data))
    assert not (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
    assert rights_count == 1 and len(owned) == 4
    assert credentials == [(driver.pid, os.getuid(), os.getgid())]
    assert len({os.fstat(fd).st_ino for fd in owned}) == 4
    for fd in owned:
        assert not os.get_inheritable(fd)
        assert not select.select([fd], [], [], 0)[0]
    report = json.loads(raw)
    assert set(report) == {"native", "deadline", "ready_by"}
    assert all(type(v) is int for v in report.values())
    assert report["deadline"] - report["ready_by"] == 2 * 10**9
    assert boot_ns() < report["ready_by"]
    assert lifetime.ingress.base.m.deadlines._fdinfo(owned[0])[b"Pid"] == str(
        report["native"]
    ).encode("ascii")
    return report


@pytest.fixture(scope="module")
def cgroup_exec_launcher(tmp_path_factory):
    source = Path(__file__).parent / "fixtures/cgroup_exec_spawn.c"
    peer_source = Path(__file__).parent / "fixtures/cgroup_peer_wait.c"
    directory = tmp_path_factory.mktemp("cgroup-exec-fixture")
    output = directory / "cgroup-exec.so"
    peer = directory / "cgroup-peer"
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
    peer_result = subprocess.run(
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
            "-pie",
            "-Wl,-z,relro,-z,now",
            str(peer_source),
            "-o",
            str(peer),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert peer_result.returncode == 0, peer_result.stderr
    return output, peer


def whole_outer_handoff(channel, driver, owned):
    raw, ancillary, flags, _ = channel.recvmsg(
        2048,
        socket.CMSG_SPACE(4 * array.array("i").itemsize) + socket.CMSG_SPACE(struct.calcsize("3i")),
        socket.MSG_CMSG_CLOEXEC,
    )
    credentials = []
    rights_count = 0
    for level, kind, data in ancillary:
        if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
            rights = array.array("i")
            rights.frombytes(data)
            owned.extend(rights)
            rights_count += 1
    for level, kind, data in ancillary:
        assert level == socket.SOL_SOCKET
        assert kind in (socket.SCM_RIGHTS, socket.SCM_CREDENTIALS)
        if kind == socket.SCM_CREDENTIALS:
            credentials.append(struct.unpack("3i", data))
    assert not (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC))
    assert credentials == [(driver.pid, os.getuid(), os.getgid())]
    if rights_count == 0:
        refusal = json.loads(raw)
        assert set(refusal) == {"error", "stage"}
        locations = []
        failures = []
        with suppress(subprocess.TimeoutExpired):
            driver.wait(timeout=2)
        if refusal["stage"].startswith("transport-"):
            diagnostic = driver.stderr.read(65536)
            locations = re.findall(
                r'_whole_outer_cgroup_driver\.py", line ([0-9]+), in _outer', diagnostic
            )
            failures = sorted(
                set(re.findall(r"^([A-Za-z]+(?:Error|Exception)):", diagnostic, re.M))
            )
        pytest.fail(
            f"Whole-outer fixture refused before injection: {refusal['stage']} "
            f"({refusal['error']}); source lines={locations}, failures={failures}"
        )
    assert rights_count == 1 and len(owned) == 4
    assert len({os.fstat(fd).st_ino for fd in owned}) == 4
    assert all(not os.get_inheritable(fd) for fd in owned)
    assert not any(select.select([fd], [], [], 0)[0] for fd in owned)
    report = json.loads(raw)
    assert set(report) == {"deadline", "native", "outer", "peers", "ready_by"}
    assert all(type(report[key]) is int for key in ("deadline", "native", "outer", "ready_by"))
    assert len(report["peers"]) == 2 and all(type(pid) is int for pid in report["peers"])
    assert report["deadline"] - report["ready_by"] == 2 * 10**9
    assert boot_ns() < report["ready_by"]
    expected = [report["native"], report["outer"], *report["peers"]]
    assert [int(base.m.deadlines._fdinfo(fd)[b"Pid"]) for fd in owned] == expected
    return report


@pytest.mark.parametrize("fault", ["deadline", "guardian_exception"])
def test_native_frozen_subtree_keeps_original_outer_and_exact_guardian(
    binary, direct_launcher, fault
):
    before = len(os.listdir("/proc/self/fd"))
    with fresh_group() as (root, leaf, expected_path, original):
        parent, child = socket.socketpair(
            socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC
        )
        parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        parent.settimeout(5)  # Transport ceiling, NEVER a new readiness budget.
        handles, driver, driver_fd = [], None, None
        report, frozen, injected, normal = None, False, False, False
        try:
            driver = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    lifetime.lifetime_program(),
                    str(lifetime.ingress.base.SOURCE.parent.parent),
                    str(binary),
                    "native_cgroup_frozen",
                    "direct-owner",
                    direct_launcher.fixture_library_path,
                    str(leaf),
                    str(child.fileno()),
                ],
                pass_fds=(leaf, child.fileno()),
                env={},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            driver_fd = os.pidfd_open(driver.pid)  # Our own fresh child, not a reported PID.
            child.close()
            report = original_handoff(parent, driver, handles)
            assert read_control(root, "cgroup.procs") == ""
            assert read_control(leaf, "cgroup.procs") == f"{report['native']}\n"
            assert Path(f"/proc/{report['native']}/cgroup").read_text() == f"0::{expected_path}\n"
            assert Path("/proc/self/cgroup").read_text() == original
            assert Path(f"/proc/{driver.pid}/cgroup").read_text() == original
            for fd in handles[1:]:
                # Read membership through the retained live identity only;
                # never reopen or signal this metadata PID.
                pid = int(base.m.deadlines._fdinfo(fd)[b"Pid"])
                assert pid > 0
                assert Path(f"/proc/{pid}/cgroup").read_text() == original
            assert not any(select.select([fd], [], [], 0)[0] for fd in handles)
            freeze_fresh(root, leaf, report["ready_by"])
            frozen = True
            assert not select.select([handles[0]], [], [], 0)[0]
            if fault == "guardian_exception":
                # Failure after observed freeze but BEFORE releasing driver.
                # Independent exact handles must still retire the frozen child.
                injected = True
                raise RuntimeError("deliberate disposable guardian failure")
            assert parent.send(b"F") == 1
            observe_by = report["deadline"] + 2 * 10**9
            out, err = driver.communicate(timeout=max(0, (observe_by - boot_ns()) / 1e9))
            assert driver.returncode == 0, err
            assert json.loads(out) == {"mode": "native_cgroup_frozen", "passed": True}
            # Original outer proved forced deadline cleanup and single reap;
            # observe this BEFORE guardian fallback can signal anything.
            assert all(select.select([fd], [], [], 0)[0] for fd in handles)
            assert events(root) == events(leaf) == {"populated": "0", "frozen": "1"}
            normal = True
        except RuntimeError:
            if not injected:
                raise
        finally:
            # Guardian is outside the new subtree, retaining original handles.
            # No numeric-PID kill, cgroup.kill, thaw, or discovered target fallback.
            for fd in [*handles, driver_fd]:
                if fd is not None:
                    with suppress(ProcessLookupError):
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
            observe_by = (report["deadline"] if report else boot_ns()) + 2 * 10**9
            try:
                for fd in [*handles, driver_fd]:
                    if fd is not None:
                        assert select.select([fd], [], [], max(0, (observe_by - boot_ns()) / 1e9))[
                            0
                        ]
                if driver is not None:
                    driver.communicate(timeout=max(0.001, (observe_by - boot_ns()) / 1e9))
            finally:
                for fd in [*handles, driver_fd]:
                    if fd is not None:
                        os.close(fd)
                parent.close()
                child.close()
        assert frozen and (normal if fault == "deadline" else injected)
        assert events(root) == events(leaf) == {"populated": "0", "frozen": "1"}
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("short_budget", [True], indirect=True)
@pytest.mark.parametrize("pair", ["preparation"], indirect=True)
def test_full_original_custody_and_watch_keep_uncertainty_after_frozen_cancel(
    binary, direct_launcher, custody, pair
):
    """Join actual full comparisons; the existing short plan is not renewed.

    This full-Custody fixture already has an eight-second test plan, unlike the
    minimal lifetime driver's four-second plan. Neither original is modified.
    Synthetic Engine facts remain synthetic, even with real cgroup placement.
    """
    before = len(os.listdir("/proc/self/fd"))
    raw, cutoff = pair.plan.raw, custody.deadline_ns
    captured = []
    with fresh_group() as (root, leaf, expected_path, original):

        def launch(binary, anchors, args, **options):
            captured.append(int(args[2]))
            return lifetime.direct.direct_parent(
                direct_launcher, binary, anchors, args, cgroup_fd=leaf, **options
            )

        with lifetime.ingress.ingress(custody, binary, after_exec=True, launcher=launch) as watch:
            assert len(captured) == 1
            assert watch.deadline_ns == cutoff and pair.plan.raw == raw
            assert all(pair.counts[role]["container"] == 4 for role in pair.children)
            assert read_control(leaf, "cgroup.procs") == f"{watch.pid}\n"
            assert Path(f"/proc/{watch.pid}/cgroup").read_text() == f"0::{expected_path}\n"
            assert Path("/proc/self/cgroup").read_text() == original
            freeze_fresh(root, leaf, captured[0])
            assert os.write(watch.cancel, b"X") == 1
            # Submission is NOT cancellation success: the frozen watcher cannot
            # read. Independent original Custody timer and owner remain live.
            assert not select.select([watch.fd], [], [], 0)[0]
            assert not any(w.exited() for w in pair.witnesses.values())
            remaining = (cutoff - boot_ns()) / 1e9
            assert remaining > 0
            assert select.select([custody.timer], [], [], remaining + base.m.RETIRE_SECONDS)[0]
            assert boot_ns() >= cutoff
            assert base.m._kill_all(watch.targets)
            signal.pidfd_send_signal(watch.fd, signal.SIGKILL)
            assert select.select([watch.fd], [], [], base.m.RETIRE_SECONDS)[0]
            base.termination.exited(pair)
            with pytest.raises(base.m.UnconfirmedTermination):
                watch.finish()
            assert watch.finished
            with pytest.raises(ChildProcessError):
                os.waitpid(watch.pid, os.WNOHANG)
            assert pair.plan.raw == raw and watch.deadline_ns == cutoff
            assert events(root) == events(leaf) == {"populated": "0", "frozen": "1"}
        with (
            pytest.raises(AssertionError),
            lifetime.direct.direct_ingress(custody, binary, direct_launcher, cgroup_fd=leaf),
        ):
            pytest.fail("Consumed custody retried after cgroup failure")
    assert len(os.listdir("/proc/self/fd")) == before


def test_whole_original_outer_group_freeze_keeps_native_and_cleanup_owner_outside(
    binary, direct_launcher, cgroup_exec_launcher
):
    """Freeze only fresh outer/peers; act on the original absolute cutoff.

    The disposable driver and this test both retain exact fallback handles
    before injection.  Passing evidence is produced before either fallback may
    signal anything.  The frozen group is never resumed or group-killed.
    """
    before = len(os.listdir("/proc/self/fd"))
    original = Path("/proc/self/cgroup").read_text()
    relative = Path(original[3:].strip()).relative_to("/")
    caller_path = Path("/sys/fs/cgroup") / relative
    caller = os.open(caller_path, DIR)
    handles = []
    driver = driver_fd = None
    parent = child = None
    communicated = False
    try:
        with fresh_group("outer") as (root, leaf, expected_path, observed_original):
            assert observed_original == original
            parent, child = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC
            )
            parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            parent.settimeout(6)  # Transport ceiling, never a new readiness budget.
            helper_program = Path(__file__).with_name("_whole_outer_cgroup_driver.py")
            repo = Path(__file__).resolve().parents[1]
            driver = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(helper_program),
                    "driver",
                    str(repo),
                    str(binary),
                    direct_launcher.fixture_library_path,
                    str(cgroup_exec_launcher[0]),
                    str(cgroup_exec_launcher[1]),
                    str(leaf),
                    str(caller),
                    str(child.fileno()),
                    expected_path,
                    original,
                ],
                pass_fds=(leaf, caller, child.fileno()),
                env={},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            driver_fd = os.pidfd_open(driver.pid)
            child.close()
            child = None
            report = whole_outer_handoff(parent, driver, handles)
            assert Path("/proc/self/cgroup").read_text() == original
            assert Path(f"/proc/{driver.pid}/cgroup").read_text() == original
            assert Path(f"/proc/{report['native']}/cgroup").read_text() == original
            members = {int(line) for line in read_control(leaf, "cgroup.procs").splitlines()}
            assert members == {report["outer"], *report["peers"]}
            assert read_control(root, "cgroup.procs") == ""
            for pid in (report["outer"], *report["peers"]):
                assert Path(f"/proc/{pid}/cgroup").read_text() == f"0::{expected_path}\n"
            freeze_fresh(root, leaf, report["ready_by"])
            assert parent.send(b"F") == 1
            observe_by = report["deadline"] + int(base.m.RETIRE_SECONDS * 10**9)
            out, err = driver.communicate(timeout=max(0.001, (observe_by - boot_ns()) / 1e9))
            communicated = True
            assert driver.returncode == 0, err
            result = json.loads(out)
            assert result["passed"] is True
            assert result["deadline"] == report["deadline"]
            assert result["dispatch_ns"] >= report["deadline"]
            assert result["outer_code"] == -signal.SIGKILL
            assert result["peer_codes"] == [-signal.SIGKILL, -signal.SIGKILL]
            assert result["native_code"] in (10, 12)
            assert all(select.select([fd], [], [], 0)[0] for fd in handles)
            assert select.select([driver_fd], [], [], 0)[0]
            assert events(root) == events(leaf) == {"populated": "0", "frozen": "1"}
    finally:
        # This independent fallback is outside the tested outcome.  It uses
        # only retained exact handles and cannot turn failed evidence into pass.
        for fd in handles:
            if fd is not None:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
        if parent is not None:
            parent.close()
        if child is not None:
            child.close()
        if driver is not None and not communicated:
            try:
                driver.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                if driver_fd is not None:
                    with suppress(ProcessLookupError):
                        signal.pidfd_send_signal(driver_fd, signal.SIGKILL)
                driver.communicate(timeout=2)
        for fd in [*handles, driver_fd]:
            if fd is not None:
                with suppress(AssertionError):
                    assert select.select([fd], [], [], 2)[0]
                os.close(fd)
        os.close(caller)
    assert len(os.listdir("/proc/self/fd")) == before
