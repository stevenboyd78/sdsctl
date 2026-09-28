"""Actual private paths, peer credentials and pidfds; synthetic Docker cgroups.

Only owned temporary files and disposable children are used. These tests do not
qualify installed input provenance, a listener launcher, or any live service.
"""

import errno
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_bootstrap as transport

NAME = "supplemental_recording_peer_connection"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(transport.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

SERVER = r"""
import os, socket, sys
from pathlib import Path
server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
server.bind(sys.argv[1])
Path(sys.argv[1]).chmod(0o600)
server.listen(1)
print("listening", flush=True)
channel = None
for line in sys.stdin:
    command = line.strip()
    if command == "accept":
        channel, _ = server.accept()
        print("accepted", flush=True)
    if command == "close":
        channel.close()
        print("closed", flush=True)
    if command == "send":
        channel.send(b"queued-bootstrap-message")
        print("sent", flush=True)
    if command == "finish":
        break
if channel is not None: channel.close()
server.close()
"""


def denied(call):
    with pytest.raises(m.UnconfirmedConnection) as error:
        call()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def fds():
    return set(os.listdir("/proc/self/fd"))


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    temporary = tempfile.TemporaryDirectory(prefix="sds-peer-connection-")
    root = Path(temporary.name)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", SERVER, str(root / m.NAME)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    witness = None
    connections = []

    def identity(pid, cid):
        return m.processes.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    def command(value):
        child.stdin.write((value + "\n").encode())
        return transport.line(child)

    def connect(**kwargs):
        options = dict(root=root, peer=witness, deadline=time.monotonic() + 2)
        options.update(kwargs)
        connection = m.Connection(**options)
        connections.append(connection)
        return connection

    monkeypatch.setattr(m.processes, "read_identity", identity)
    try:
        assert transport.line(child) == "listening"
        witness = m.processes.ProcessWitness(identity(child.pid, "a" * 64))
        yield SimpleNamespace(
            root=root,
            child=child,
            witness=witness,
            connect=connect,
            command=command,
            identity=identity,
        )
    finally:
        for connection in connections:
            connection.close()
        if witness is not None:
            witness.close()
        if child.poll() is None:
            child.kill()  # Only this fixture's original disposable child.
        child.wait(timeout=3)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()
        temporary.cleanup()


def test_original_authenticated_peer_and_private_path_are_retained(server):
    before = fds()
    connection = server.connect()
    assert server.command("accept") == "accepted"
    assert connection.recheck() is None
    assert connection.peer is server.witness
    channel = connection.channel
    assert channel.type == socket.SOCK_SEQPACKET and channel.gettimeout() == 0.0
    assert channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1
    assert not channel.get_inheritable()
    connection.close()
    connection.close()
    assert fds() == before and not server.witness.exited()
    assert (server.root / m.NAME).is_socket()
    denied(connection.recheck)


def test_recheck_does_not_consume_queued_bootstrap_messages(server):
    connection = server.connect()
    assert server.command("accept") == "accepted"
    assert server.command("send") == "sent"
    connection.recheck()
    raw, ancillary, flags, _ = connection.channel.recvmsg(128, socket.CMSG_SPACE(12))
    assert raw == b"queued-bootstrap-message" and flags == 0
    assert len(ancillary) == 1 and ancillary[0][:2] == (socket.SOL_SOCKET, socket.SCM_CREDENTIALS)


@pytest.mark.parametrize(
    "fault", ["root-mode", "socket-mode", "extra", "symlink", "file", "missing"]
)
def test_untrusted_path_is_refused_before_connect(server, monkeypatch, fault):
    path = server.root / m.NAME
    if fault == "root-mode":
        server.root.chmod(0o755)
    elif fault == "socket-mode":
        path.chmod(0o666)
    elif fault == "extra":
        (server.root / "extra").touch()
    else:
        path.unlink()
        if fault == "symlink":
            path.symlink_to("missing")
        elif fault == "file":
            path.write_text("PRIVATE")
    monkeypatch.setattr(m.socket, "socket", lambda *a, **k: pytest.fail("Must not connect"))
    before = fds()
    denied(server.connect)
    assert fds() == before and not server.witness.exited()


@pytest.mark.parametrize("deadline", [True, None, "1", float("nan"), float("inf"), -1])
def test_invalid_or_expired_deadline_does_not_open_paths(server, monkeypatch, deadline):
    with monkeypatch.context() as patch:
        patch.setattr(m.os, "open", lambda *a, **k: pytest.fail("No path open"))
        denied(lambda: server.connect(deadline=deadline))


def test_external_deadline_only_narrows_total_budget(server):
    cutoff = time.monotonic() + 0.5
    connection = server.connect(deadline=cutoff)
    assert connection.deadline == cutoff
    connection.close()
    # Reusing this path is not part of a Connection object's behavior; no
    # second test connection is attempted here.


@pytest.mark.parametrize("fault", ["socket", "parent", "extra", "permissions"])
def test_changed_path_permanently_retires_connection_without_repair(server, fault):
    before = fds()
    connection = server.connect()
    assert server.command("accept") == "accepted"
    moved = None
    try:
        if fault == "socket":
            (server.root / m.NAME).unlink()
            (server.root / m.NAME).write_text("PRIVATE replacement")
        elif fault == "parent":
            moved = server.root.with_name(server.root.name + "-original")
            server.root.rename(moved)
            server.root.mkdir(mode=0o700)
        elif fault == "extra":
            (server.root / "extra").touch()
        else:
            server.root.chmod(0o755)
        denied(connection.recheck)
        assert connection.closed and connection.failed and connection.channel is None
        assert fds() == before
        assert not server.witness.exited()
        denied(connection.recheck)
        if fault == "socket":
            assert (server.root / m.NAME).read_text() == "PRIVATE replacement"
    finally:
        if moved is not None:
            server.root.rmdir()
            moved.rename(server.root)


@pytest.mark.parametrize(
    "fault", ["blocking", "inheritable", "passcred", "cutoff", "peer", "identity"]
)
def test_changed_original_channel_or_witness_is_not_adopted(server, fault):
    connection = server.connect()
    if fault == "blocking":
        connection.channel.setblocking(True)
    elif fault == "inheritable":
        connection.channel.set_inheritable(True)
    elif fault == "passcred":
        connection.channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 0)
    elif fault == "cutoff":
        connection.deadline += 1
    elif fault == "peer":
        connection.peer = object()
    else:
        connection.peer.identity = replace(connection.peer.identity)
    denied(connection.recheck)
    assert connection.closed


@pytest.mark.parametrize("fault", ["exit", "closed-channel"])
def test_original_peer_loss_is_not_reconnected(server, fault):
    connection = server.connect()
    assert server.command("accept") == "accepted"
    if fault == "exit":
        server.child.kill()
        server.child.wait(timeout=3)
    else:
        assert server.command("close") == "closed"
        assert not server.witness.exited()
    denied(connection.recheck)


def test_wrong_live_original_peer_cannot_authenticate_listener(server):
    other = subprocess.Popen([sys.executable, "-I", "-c", "input()"], stdin=subprocess.PIPE)
    peer = m.processes.ProcessWitness(server.identity(other.pid, "b" * 64))
    try:
        before = fds()
        denied(lambda: server.connect(peer=peer))
        assert fds() == before and not peer.exited()
    finally:
        peer.close()
        other.kill()
        other.wait(timeout=3)
        other.stdin.close()


@pytest.mark.parametrize("target", ["channel", "directory"])
def test_foreign_reused_descriptor_is_detached_not_closed(server, target):
    connection = server.connect()
    channel = connection.channel
    fd = channel.fileno() if target == "channel" else connection.directory
    spare = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.close(fd)
        os.dup2(spare, fd, inheritable=False)
        denied(connection.recheck)
        assert os.fstat(fd) == os.fstat(spare)
        assert connection.closed and channel.fileno() == -1
    finally:
        os.close(fd)
        os.close(spare)


def test_other_thread_cannot_close_connection(server):
    connection = server.connect()
    errors = []

    def foreign():
        try:
            connection.close()
        except m.UnconfirmedConnection:
            errors.append(True)

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=2)
    assert errors == [True] and not connection.closed
    connection.recheck()


@pytest.mark.parametrize("result", [errno.EAGAIN, errno.ECONNREFUSED, errno.EINTR])
def test_connect_refusal_is_one_attempt_without_retry(server, monkeypatch, result):
    calls = []

    def refused(channel, path):
        calls.append(path)
        return result

    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    before = fds()
    denied(server.connect)
    assert len(calls) == 1 and calls[0].startswith("/proc/self/fd/")
    assert fds() == before


def test_construction_time_is_not_renewed_after_path_reads(server, monkeypatch):
    original = m.Connection._paths
    called = []

    def slow(connection):
        original(connection)
        called.append(True)
        monkeypatch.setattr(m.time, "monotonic", lambda: connection.deadline)

    monkeypatch.setattr(m.Connection, "_paths", slow)
    monkeypatch.setattr(m.socket.socket, "connect_ex", lambda *a: pytest.fail("Budget renewed"))
    before = fds()
    denied(server.connect)
    assert called == [True] and fds() == before


@pytest.mark.parametrize("fault", ["symlink-parent", "relative", "root", "traversal", "text"])
def test_noncanonical_or_indirect_root_is_refused(server, tmp_path, fault):
    if fault == "symlink-parent":
        root = tmp_path / "alias"
        root.symlink_to(server.root, target_is_directory=True)
    elif fault == "relative":
        root = Path("relative")
    elif fault == "root":
        root = Path("/")
    elif fault == "traversal":
        root = server.root / ".." / server.root.name
    else:
        root = str(server.root)
    before = fds()
    denied(lambda: server.connect(root=root))
    assert fds() == before


def test_socket_wrapper_replacement_is_not_closed_or_adopted(server):
    connection = server.connect()
    original = connection.channel
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as foreign:
        connection.channel = foreign
        denied(connection.recheck)
        assert original.fileno() == -1 and foreign.fileno() >= 0


def test_initial_clock_cutoff_cannot_be_extended_by_large_external_deadline(server):
    began = time.monotonic()
    connection = server.connect(deadline=began + 100)
    assert began < connection.deadline <= time.monotonic() + m.SECONDS
    assert connection.deadline < began + 3


def test_interruption_after_socket_creation_cleans_originals_and_propagates(server, monkeypatch):
    def interrupted(*args):
        raise KeyboardInterrupt("PRIVATE")

    monkeypatch.setattr(socket.socket, "connect_ex", interrupted)
    before = fds()
    with pytest.raises(KeyboardInterrupt):
        server.connect()
    assert fds() == before and not server.witness.exited()


def test_cleanup_continues_after_uncertain_close_and_never_retries(server, monkeypatch):
    before = fds()
    connection = server.connect()
    originals = [fd for fd, _ in connection.handles]
    close, seen = os.close, []

    def uncertain(fd):
        seen.append(fd)
        close(fd)
        if fd == originals[-1]:
            raise OSError("PRIVATE close result")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "close", uncertain)
        denied(connection.close)
        connection.close()
    assert seen == list(reversed(originals)) and fds() == before


def test_pending_connect_failure_is_not_assumed_connected(server, monkeypatch):
    calls = []

    def pending(channel, path):
        calls.append(path)
        return errno.EINPROGRESS

    monkeypatch.setattr(socket.socket, "connect_ex", pending)
    before = fds()
    denied(server.connect)
    assert len(calls) == 1 and fds() == before


def test_import_and_direct_execution_never_launch_or_change_source_profiles():
    from . import test_supplemental_recording_service_host_source as source_tests

    source = source_tests.m
    assert NAME not in source.MODULES
    assert NAME not in source_tests.app.m.MODULES
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(m.__file__))],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode != 0 and "no active launch enabled" in completed.stderr
