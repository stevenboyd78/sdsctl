"""Independent disposable subreaper for original-outer loss, OFFLINE only.

No existing process is signaled or adopted. No reported PID is reopened, no
work budget renewed, and no installation/active/recovery authority is inferred.
"""

import array
import ctypes
import hashlib
import json
import os
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path


def failure_types(raw):
    """Closed type-name vocabulary only; never echo arbitrary child text."""
    text = raw[:65536].decode("ascii", errors="replace")
    known = (
        "AssertionError",
        "OSError",
        "TypeError",
        "ValueError",
        "RuntimeError",
        "ImportError",
        "ModuleNotFoundError",
        "AttributeError",
        "TimeoutError",
    )
    return [name for name in known if re.search(r"(?m)^E +" + name + ":", text)]


def snapshot(directory):
    """Original held disposable case directory, bounded regular files only."""
    result = {}
    names = os.listdir(directory)
    assert len(names) <= 20
    for name in names:
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            continue
        assert stat.S_ISREG(metadata.st_mode) and metadata.st_size <= 262144
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
        try:
            assert os.fstat(fd) == metadata
            raw = os.read(fd, metadata.st_size + 1)
            assert len(raw) == metadata.st_size
            result[name] = hashlib.sha256(raw).hexdigest()
        finally:
            os.close(fd)
    assert {"startup-claim.json", "plan.json"} <= set(result)
    return result


def main():
    repository, binary, digest, library, phase, temporary, socket_temporary = sys.argv[1:]
    assert phase in ("armed", "writer", "observer")
    # Only this newly created driver adopts after its own disposable child dies.
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
    baseline = len(os.listdir("/proc/self/fd"))
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
    parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    parent.settimeout(15)  # Fixture setup only, NOT installed startup qualification.
    config = dict(
        fd=child.fileno(),
        binary=binary,
        digest=digest,
        library=library,
        phase=phase,
        temporary=str(Path(temporary) / "outer"),
    )
    code = (
        "import sys; sys.path[:0] = [sys.argv.pop(1), sys.argv.pop(1)]; "
        "from tests._passive_outer_process_fixture import main; raise SystemExit(main())"
    )
    process = None
    outer_fd = None
    received = []
    observed = []
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                code,
                repository,
                str(Path(repository) / "src"),
                json.dumps(config),
            ],
            pass_fds=(child.fileno(),),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"TMPDIR": socket_temporary, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        )
        outer_fd = os.pidfd_open(process.pid)  # Our own fresh, still-unreaped child.
        child.close()
        raw, ancillary, flags, _ = parent.recvmsg(
            2048, socket.CMSG_SPACE(20) + socket.CMSG_SPACE(12), socket.MSG_CMSG_CLOEXEC
        )
        if not raw:
            code = process.wait(timeout=3)
            locations = []
            socket_path_too_long = False
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                with suppress(BlockingIOError):
                    raw_output = os.read(stream.fileno(), 65536)
                    # Only exception type names; no private values/source lines.
                    locations.extend(failure_types(raw_output))
                    socket_path_too_long |= b"AF_UNIX path too long" in raw_output
            raise AssertionError(
                f"Outer exited before handoff: code={code}, types={locations}, "
                f"socket_path_too_long={socket_path_too_long}"
            )
        credentials = []
        for level, kind, value in ancillary:
            assert level == socket.SOL_SOCKET
            if kind == socket.SCM_RIGHTS:
                rights = array.array("i")
                rights.frombytes(value)
                received.extend(rights)
            else:
                assert kind == socket.SCM_CREDENTIALS and len(value) == 12
                credentials.append(struct.unpack("iII", value))
        assert flags & ~socket.MSG_CMSG_CLOEXEC == 0
        assert len(received) == 5 and credentials == [(process.pid, os.geteuid(), os.getegid())]
        writer, observer, native, cancel, directory = received
        assert all(not os.get_inheritable(fd) for fd in received)
        state = json.loads(raw)
        assert state["phase"] == phase and time.monotonic() < state["end"]
        assert state["writer_reads"] >= 6 and state["observer_reads"] >= 4
        assert time.clock_gettime_ns(time.CLOCK_BOOTTIME) < state["native_deadline_ns"]
        assert not select.select([writer, observer, native, outer_fd], [], [], 0)[0]
        for fd in (writer, observer, native):
            try:
                os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                pass  # Handle retention has NOT transferred original parenthood.
            else:
                raise AssertionError("Outer's original child unexpectedly belongs to driver")
        original = snapshot(directory)
        signal.pidfd_send_signal(outer_fd, signal.SIGKILL)
        for fd in (outer_fd, writer, observer, native):
            remaining = state["end"] - time.monotonic()
            assert remaining > 0 and select.select([fd], [], [], remaining)[0] == [fd]
        assert process.wait(timeout=0) == -signal.SIGKILL
        for fd in (writer, observer, native):
            status = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)
            assert status is not None
            observed.append((status.si_code, status.si_status))
        assert observed[0] == (os.CLD_KILLED, signal.SIGKILL)
        assert observed[1] in ((os.CLD_KILLED, signal.SIGKILL), (os.CLD_EXITED, 75))
        assert observed[2] == (os.CLD_EXITED, 12)  # Original outer loss, not EOF or work success.
        assert snapshot(directory) == original
        assert time.monotonic() < state["end"]
        print(json.dumps(dict(phase=phase, peers_exited=True, native=12, files_unchanged=True)))
    finally:
        # Fallback is outside the tested loss domain and never counts as a pass.
        # Only original handles delivered by our authenticated owned child.
        for fd in [outer_fd, *received[:3]]:
            if fd is not None:
                with suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
        if process is not None:
            process.wait(timeout=3)
        for fd in received[:3]:
            with suppress(ChildProcessError):
                assert select.select([fd], [], [], 3)[0]
                os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)
        for fd in [outer_fd, *received]:
            if fd is not None:
                os.close(fd)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                stream.close()
        parent.close()
        child.close()
        assert len(os.listdir("/proc/self/fd")) == baseline


if __name__ == "__main__":
    main()
