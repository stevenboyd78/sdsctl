"""One-shot whole-original-outer cgroup fixture; never installed proof."""

import array
import ctypes
import fcntl
import json
import os
import re
import select
import signal
import socket
import struct
import sys
import time
from contextlib import suppress
from pathlib import Path


def _boot_ns():
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def _load_exec(path):
    library = ctypes.CDLL(str(path), use_errno=True)
    spawn = library.sds_fixture_exec_in_cgroup
    spawn.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    spawn.restype = ctypes.c_int
    return spawn


def _spawn(spawn, executable, group, argv, handles=()):
    """Return the kernel-created PID and pidfd without reopening its PID."""
    result = (ctypes.c_int * 2)(-1, -1)
    copies = []
    transferred = False
    opened = os.open(Path(executable).resolve(), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        for fd in (opened, group, *handles):
            copies.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 32))
        vector = (ctypes.c_char_p * (len(argv) + 1))(*(os.fsencode(item) for item in argv), None)
        handle_vector = (ctypes.c_int * len(handles))(*copies[2:])
        code = spawn(
            result,
            copies[0],
            copies[1],
            handle_vector,
            len(handles),
            vector,
        )
        assert code == 0
        pid, fd = result
        assert pid > 1 and fd >= 0 and not os.get_inheritable(fd)
        assert _fdinfo(fd)[b"Pid"] == str(pid).encode("ascii")
        assert not select.select([fd], [], [], 0)[0]
        transferred = True
        return pid, fd
    finally:
        if result[1] >= 0 and not transferred:
            try:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(result[1], signal.SIGKILL)
                assert select.select([result[1]], [], [], 2)[0]
                os.waitid(os.P_PIDFD, result[1], os.WEXITED)
            finally:
                os.close(result[1])
        for fd in copies:
            os.close(fd)
        os.close(opened)


def _fdinfo(fd):
    fields = {}
    with open(f"/proc/self/fdinfo/{fd}", "rb", buffering=0) as stream:
        for line in stream:
            key, separator, value = line.partition(b":")
            if separator:
                fields[key] = value.strip()
    return fields


def _recv_outer(channel, outer):
    outer_pid, outer_fd = outer
    raw, ancillary, flags, _ = channel.recvmsg(
        2048,
        socket.CMSG_SPACE(array.array("i").itemsize) + socket.CMSG_SPACE(struct.calcsize("3i")),
        socket.MSG_CMSG_CLOEXEC,
    )
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        return {"error": "AssertionError", "stage": "transport-truncated"}, None
    if not raw:
        info = os.waitid(os.P_PIDFD, outer_fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if info is None:
            stage = "transport-eof-live"
        elif info.si_code == os.CLD_EXITED:
            stage = f"transport-exit-{info.si_status}"
        else:
            stage = f"transport-signal-{info.si_status}"
        return {"error": "AssertionError", "stage": stage}, None
    descriptors = []
    credentials = []
    for level, kind, data in ancillary:
        if level != socket.SOL_SOCKET or kind not in (
            socket.SCM_RIGHTS,
            socket.SCM_CREDENTIALS,
        ):
            return {"error": "AssertionError", "stage": "transport-ancillary"}, None
        if kind == socket.SCM_RIGHTS:
            received = array.array("i")
            received.frombytes(data)
            descriptors.extend(received)
        else:
            credentials.append(struct.unpack("3i", data))
    if credentials != [(outer_pid, os.getuid(), os.getgid())]:
        return {"error": "AssertionError", "stage": "transport-credentials"}, None
    if not descriptors:
        try:
            refusal = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"error": "AssertionError", "stage": "transport-payload"}, None
        if (
            set(refusal) != {"error", "stage"}
            or refusal["error"]
            not in {
                "AssertionError",
                "OSError",
                "PermissionError",
                "RuntimeError",
                "UnsafeHandoff",
            }
            or refusal["stage"]
            not in {
                "import",
                "descriptors",
                "placement",
                "identity",
                "identity-stat",
                "identity-state",
                "identity-start",
                "identity-policy",
                "native",
                "handoff",
                "readiness",
            }
        ):
            return {"error": "AssertionError", "stage": "transport-payload"}, None
        return refusal, None
    if len(descriptors) != 1:
        for fd in descriptors:
            os.close(fd)
        return {"error": "AssertionError", "stage": "transport-rights"}, None
    assert not os.get_inheritable(descriptors[0])
    return json.loads(raw), descriptors[0]


def _send_guardian(channel, report, handles):
    sent = channel.sendmsg(
        [json.dumps(report, sort_keys=True).encode("ascii")],
        [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", handles))],
    )
    assert sent > 0


def _wait_all(handles, cutoff):
    pending = set(handles)
    while pending:
        remaining = (cutoff - _boot_ns()) / 1e9
        assert remaining > 0
        ready = select.select(list(pending), [], [], remaining)[0]
        assert ready
        pending.difference_update(ready)


def _wait_exec(pid, fd, executable, cutoff):
    expected = os.stat(executable)
    while True:
        assert not select.select([fd], [], [], 0)[0], "Disposable peer exited before exec receipt"
        try:
            observed = os.stat(f"/proc/{pid}/exe")
        except FileNotFoundError:
            observed = None
        if observed is not None and (observed.st_dev, observed.st_ino) == (
            expected.st_dev,
            expected.st_ino,
        ):
            return
        remaining = (cutoff - _boot_ns()) / 1e9
        assert remaining > 0, "Disposable peer exec was not observed within setup ceiling"
        select.select([], [], [], min(0.001, remaining))


def _reap(pid):
    return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])


def _driver(args):
    repo, binary, direct_library, exec_library, peer_executable = map(Path, args[:5])
    target_fd, caller_fd, guardian_fd = map(int, args[5:8])
    expected_path, original_cgroup = args[8:10]
    sys.path[:0] = [str(repo), str(repo / "src"), str(repo / "scripts")]
    from tests import test_supplemental_native_peer_lifetime as lifetime

    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
    spawn = _load_exec(exec_library)
    guardian = socket.socket(fileno=guardian_fd)
    guardian.settimeout(8)
    peers = []
    outer = native = None
    outer_channel = outer_child = None
    timer = None
    reaped = set()
    completed = False
    guardian_sent = False
    stage = "peer-spawn"
    try:
        setup_by = _boot_ns() + 2 * 10**9
        for _ in range(2):
            peer = _spawn(spawn, peer_executable, target_fd, [str(peer_executable)])
            _wait_exec(*peer, peer_executable, setup_by)
            peers.append(peer)
        stage = "outer-spawn"
        outer_channel, outer_child = socket.socketpair(
            socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC
        )
        outer_channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        helper = Path(__file__).resolve()
        site_packages = next(path for path in sys.path if Path(path).name == "site-packages")
        outer_argv = [
            str(Path(sys.executable).resolve()),
            "-I",
            "-B",
            str(helper),
            "outer",
            str(repo),
            str(binary),
            str(direct_library),
            site_packages,
            str(peers[0][0]),
            str(peers[1][0]),
            original_cgroup,
            expected_path,
        ]
        outer = _spawn(
            spawn,
            Path(sys.executable),
            target_fd,
            outer_argv,
            (outer_child.fileno(), caller_fd, peers[0][1], peers[1][1]),
        )
        outer_child.close()
        outer_child = None
        stage = "outer-handoff"
        report, native_fd = _recv_outer(outer_channel, outer)
        if native_fd is None:
            exited = [item for item in peers if select.select([item[1]], [], [], 0)[0]]
            if exited:
                statuses = [
                    os.waitid(os.P_PIDFD, item[1], os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    for item in exited
                ]
                report = {
                    "error": "AssertionError",
                    "stage": "peer-exit-" + "-".join(str(info.si_status) for info in statuses),
                }
            guardian.send(json.dumps(report, sort_keys=True).encode("ascii"))
            guardian_sent = True
            raise RuntimeError("Original outer refused before exact native handoff")
        native = (report["native"], native_fd)
        assert report == {
            "deadline": report["deadline"],
            "native": native[0],
            "outer": outer[0],
            "peers": [peers[0][0], peers[1][0]],
            "ready_by": report["ready_by"],
        }
        assert all(type(report[key]) is int for key in ("deadline", "native", "outer", "ready_by"))
        assert report["deadline"] - report["ready_by"] == 2 * 10**9
        assert _boot_ns() < report["ready_by"]
        assert _fdinfo(native[1])[b"Pid"] == str(native[0]).encode("ascii")
        outer_channel.send(b"G")
        assert outer_channel.recv(2) == b"R"
        assert _boot_ns() < report["ready_by"]
        timer = os.timerfd_create(time.CLOCK_BOOTTIME, flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK)
        os.timerfd_settime_ns(timer, flags=os.TFD_TIMER_ABSTIME, initial=report["deadline"])
        _send_guardian(
            guardian,
            report,
            [native[1], outer[1], peers[0][1], peers[1][1]],
        )
        guardian_sent = True
        remaining = (report["ready_by"] - _boot_ns()) / 1e9
        assert remaining > 0
        guardian.settimeout(remaining)
        assert guardian.recv(2) == b"F"
        watched = [timer, native[1], outer[1], peers[0][1], peers[1][1]]
        while True:
            remaining = (report["deadline"] - _boot_ns()) / 1e9
            assert remaining > 0
            ready = select.select(watched, [], [], remaining)[0]
            assert ready
            assert not any(fd in ready for fd in watched[1:]), (
                "Process exited before original cutoff"
            )
            if timer in ready:
                break
        assert os.read(timer, 8) == struct.pack("Q", 1)
        dispatched_ns = _boot_ns()
        signal.pidfd_send_signal(outer[1], signal.SIGKILL)
        observe_by = report["deadline"] + int(lifetime.ingress.base.m.RETIRE_SECONDS * 10**9)
        _wait_all([native[1], outer[1], peers[0][1], peers[1][1]], observe_by)
        outer_code = _reap(outer[0])
        reaped.add(outer[0])
        peer_codes = []
        for pid, _ in peers:
            peer_codes.append(_reap(pid))
            reaped.add(pid)
        native_code = _reap(native[0])
        reaped.add(native[0])
        assert outer_code == -signal.SIGKILL
        assert peer_codes == [-signal.SIGKILL, -signal.SIGKILL]
        assert native_code in (10, 12)
        assert Path("/proc/self/cgroup").read_text() == original_cgroup
        completed = True
        print(
            json.dumps(
                {
                    "deadline": report["deadline"],
                    "dispatch_ns": dispatched_ns,
                    "native_code": native_code,
                    "outer_code": outer_code,
                    "peer_codes": peer_codes,
                    "passed": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    except BaseException as error:
        if not guardian_sent:
            refusal = {
                "error": type(error).__name__,
                "stage": stage,
            }
            assert refusal["error"] in {
                "AssertionError",
                "OSError",
                "PermissionError",
                "RuntimeError",
                "UnsafeHandoff",
            }
            guardian.send(json.dumps(refusal, sort_keys=True).encode("ascii"))
        raise
    finally:
        # Exact fallback handles remain outside the injected subtree.  This is
        # cleanup only and never converts a failed observation into success.
        for item in (native, outer, *peers):
            if item is not None:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(item[1], signal.SIGKILL)
        if not completed:
            deadline = _boot_ns() + 2 * 10**9
            for item in (native, outer, *peers):
                if item is not None:
                    with suppress(AssertionError):
                        _wait_all([item[1]], deadline)
        for item in (outer, *peers, native):
            if item is not None and item[0] not in reaped:
                with suppress(ChildProcessError):
                    os.waitpid(item[0], 0)
        for item in (native, outer, *peers):
            if item is not None:
                os.close(item[1])
        if timer is not None:
            os.close(timer)
        if outer_channel is not None:
            outer_channel.close()
        if outer_child is not None:
            outer_child.close()
        guardian.close()


def _outer(args):
    repo, binary, direct_library = map(Path, args[:3])
    site_packages = Path(args[3])
    peer_pids = list(map(int, args[4:6]))
    original_cgroup, expected_path = args[6:8]
    control = socket.socket(fileno=3)
    caller_group = 4
    peer_fds = [5, 6]
    watch = None
    owned = []
    channels = []
    stage = "import"
    try:
        sys.path[:0] = [
            str(site_packages),
            str(repo),
            str(repo / "src"),
            str(repo / "scripts"),
        ]
        from tests import test_supplemental_native_peer_lifetime as lifetime

        base = lifetime.ingress.base
        stage = "descriptors"
        for fd in (3, 4, 5, 6):
            os.set_inheritable(fd, False)
        stage = "placement"
        assert Path("/proc/self/cgroup").read_text() == f"0::{expected_path}\n"
        targets = []
        identities = []
        stage = "identity"
        for index, (pid, fd) in enumerate(zip(peer_pids, peer_fds, strict=True)):
            assert _fdinfo(fd)[b"Pid"] == str(pid).encode("ascii")
            assert Path(f"/proc/{pid}/cgroup").read_text() == f"0::{expected_path}\n"
            targets.append(fd)
            marker = "a" if index == 0 else "b"
            stat_text = Path(f"/proc/{pid}/stat").read_text()
            first, closing, rest = stat_text.rpartition(") ")
            fields = rest.split()
            stage = "identity-stat"
            assert bool(closing) and first.startswith(str(pid) + " (") and len(fields) >= 20
            stage = "identity-state"
            assert fields[0] in {"R", "S", "D", "T", "t", "I"}
            stage = "identity-start"
            assert re.fullmatch(r"[1-9][0-9]*", fields[19]) is not None
            stage = "identity-policy"
            identities.append(
                base.m.deadlines.links.domains.process.process_identity(
                    pid,
                    marker * 64,
                    stat_text,
                    f"0::/system.slice/docker-{marker * 64}.scope\n",
                )
            )
        issued = _boot_ns()
        ready_by, recover_by = issued + 2 * 10**9, issued + 4 * 10**9
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
        original_outer = os.pidfd_open(os.getpid())
        owned.append(original_outer)
        namespace = os.open("/proc/self/ns/time", os.O_RDONLY | os.O_CLOEXEC)
        owned.append(namespace)
        timer = os.timerfd_create(time.CLOCK_BOOTTIME, flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK)
        owned.append(timer)
        os.timerfd_settime_ns(timer, flags=os.TFD_TIMER_ABSTIME, initial=recover_by)
        cancel_read, cancel_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        ready, ready_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        owned.extend((cancel_read, cancel_write, ready, ready_write))
        sender, receiver = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_SEQPACKET | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC,
        )
        channels.extend((sender, receiver))
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        stage = "native"
        spawn = lifetime.direct.load_direct(direct_library)
        arguments = [
            "--offline-original-peer-ingress-v1",
            str(recover_by),
            str(ready_by),
            boot,
        ]
        anchors = [receiver.fileno(), original_outer, namespace]
        original = lifetime.direct.direct_parent(
            spawn, binary, anchors, arguments, cgroup_fd=caller_group
        )
        native_pid, native_fd = original.pid, original.fd
        assert Path(f"/proc/{native_pid}/cgroup").read_text() == original_cgroup
        receiver.close()
        watch = base.m.Watch(
            native_pid,
            native_fd,
            cancel_write,
            tuple(targets),
            tuple(identities),
            recover_by,
        )
        owned.remove(cancel_write)
        report = {
            "deadline": recover_by,
            "native": native_pid,
            "outer": os.getpid(),
            "peers": peer_pids,
            "ready_by": ready_by,
        }
        stage = "handoff"
        control.sendmsg(
            [json.dumps(report, sort_keys=True).encode("ascii")],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [native_fd]))],
        )
        stage = "readiness"
        remaining = (ready_by - _boot_ns()) / 1e9
        assert remaining > 0
        control.settimeout(remaining)
        assert control.recv(2) == b"G"
        handles = [*targets, original_outer, cancel_read, ready_write, namespace]
        assert (
            sender.sendmsg(
                [b"original-peer-handles-v1"],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", handles))],
            )
            == 24
        )
        sender.shutdown(socket.SHUT_WR)
        sender.close()
        os.close(ready_write)
        owned.remove(ready_write)
        remaining = (ready_by - _boot_ns()) / 1e9
        assert remaining > 0
        assert select.select([ready, native_fd], [], [], remaining)[0] == [ready]
        assert os.read(ready, 2) == b"1"
        assert _boot_ns() < ready_by
        control.send(b"R")
        # This owner is deliberately frozen before either original descriptor
        # becomes readable.  If injection is refused, retain normal cleanup.
        assert select.select([native_fd, timer], [], [])[0]
        forced = not select.select([native_fd], [], [], 0)[0]
        if forced:
            assert base.m._kill_all(targets)
            signal.pidfd_send_signal(native_fd, signal.SIGKILL)
            assert select.select([native_fd], [], [], base.m.RETIRE_SECONDS)[0]
        with suppress(base.m.UnconfirmedTermination):
            watch.finish()
        watch.close()
        watch = None
        for channel in channels:
            channel.close()
        for fd in owned:
            os.close(fd)
        control.close()
        return
    except BaseException as error:
        refusal = {"error": type(error).__name__, "stage": stage}
        if refusal["error"] in {
            "AssertionError",
            "OSError",
            "PermissionError",
            "RuntimeError",
            "UnsafeHandoff",
        }:
            with suppress(BaseException):
                control.send(json.dumps(refusal, sort_keys=True).encode("ascii"))
        if watch is not None:
            with suppress(BaseException):
                watch.close()
        raise


def main():
    assert len(sys.argv) >= 3
    mode = sys.argv[1]
    if mode == "driver":
        assert len(sys.argv) == 12
        _driver(sys.argv[2:])
    elif mode == "outer":
        assert len(sys.argv) == 10
        _outer(sys.argv[2:])
    else:
        raise AssertionError("Unknown whole-outer fixture role")


if __name__ == "__main__":
    main()
