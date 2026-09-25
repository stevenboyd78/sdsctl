"""Local action-free command tests; real socket/process/clock handles.

Docker cgroups, root eligibility and external qualification are explicit fixture
substitutions, never evidence of installed host/source confinement.
"""

import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_service_permission as peers
from .test_supplemental_recording_service_startup import m as startup

NAME = "supplemental_recording_permission_probe"
SPEC = importlib.util.spec_from_file_location(NAME, Path(peers.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


def denied(action):
    with pytest.raises(m.permission.UnconfirmedPermission) as error:
        action()
    assert str(error.value) == m.permission.MESSAGE and error.value.__suppress_context__


def fds():
    return set(os.listdir("/proc/self/fd"))


@pytest.fixture
def listening():
    with tempfile.TemporaryDirectory(prefix="permission-path-") as directory:
        root = Path(directory)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(root / m.NAME))
            (root / m.NAME).chmod(0o600)
            server.listen(1)
            server.settimeout(2)
            yield SimpleNamespace(root=root, server=server, end=time.monotonic() + 2.0)


def test_connects_through_original_private_path_and_closes_without_removing_it(listening):
    before = fds()
    obj = m.PeerConnection(listening.root, listening.end)
    channel, _ = listening.server.accept()
    try:
        assert obj.recheck() is None
        assert obj.channel.gettimeout() == 0.0
        assert (listening.root / m.NAME).exists()
        assert set(listening.root.iterdir()) == {listening.root / m.NAME}
    finally:
        channel.close()
        obj.close()
    obj.close()
    assert fds() == before
    assert (listening.root / m.NAME).exists()
    denied(obj.recheck)


@pytest.mark.parametrize(
    "fault", ["directory_mode", "socket_mode", "extra", "symlink", "file", "missing"]
)
def test_unsafe_or_incomplete_channel_setup_is_not_connected(listening, fault):
    path = listening.root / m.NAME
    if fault == "directory_mode":
        listening.root.chmod(0o755)
    elif fault == "socket_mode":
        path.chmod(0o666)
    elif fault == "extra":
        (listening.root / "extra").touch()
    else:
        path.unlink()
        if fault == "symlink":
            path.symlink_to("missing")
        elif fault == "file":
            path.write_bytes(b"PRIVATE")
            path.chmod(0o600)
    before = fds()
    denied(lambda: m.PeerConnection(listening.root, listening.end))
    assert fds() == before


@pytest.mark.parametrize(
    "fault", ["socket_replaced", "parent_replaced", "blocking", "inheritable", "deadline", "extra"]
)
def test_later_path_or_channel_change_is_sticky_and_never_reconnected(listening, fault):
    obj = m.PeerConnection(listening.root, listening.end)
    channel, _ = listening.server.accept()
    moved = None
    try:
        if fault == "socket_replaced":
            (listening.root / m.NAME).unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
                replacement.bind(str(listening.root / m.NAME))
                (listening.root / m.NAME).chmod(0o600)
        elif fault == "parent_replaced":
            moved = listening.root.with_name(listening.root.name + "-original")
            listening.root.rename(moved)
            listening.root.mkdir(mode=0o700)
        elif fault == "blocking":
            obj.channel.setblocking(True)
        elif fault == "inheritable":
            os.set_inheritable(obj.fd, True)
        elif fault == "deadline":
            obj.deadline += 1
        else:
            (listening.root / "extra").touch()
        denied(obj.recheck)
        assert obj.failed and obj.closed
        denied(obj.recheck)
    finally:
        channel.close()
        obj.close()
        if moved:
            listening.root.rmdir()
            moved.rename(listening.root)


def test_no_foreign_thread_may_retire_original_channel(listening):
    obj = m.PeerConnection(listening.root, listening.end)
    channel, _ = listening.server.accept()
    failures = []

    def foreign():
        try:
            obj.close()
        except Exception as error:
            failures.append(error)

    try:
        thread = Thread(target=foreign)
        thread.start()
        thread.join(timeout=2)
        assert len(failures) == 1 and not obj.closed
        obj.recheck()
    finally:
        channel.close()
        obj.close()


def test_cleanup_continues_but_never_retries_an_uncertain_close(listening, monkeypatch):
    obj = m.PeerConnection(listening.root, listening.end)
    channel, _ = listening.server.accept()
    handles, calls = list(obj.handles), []
    close = os.close

    def uncertain(fd):
        calls.append(fd)
        close(fd)
        if fd == handles[-1]:
            raise OSError("PRIVATE close error after actual close")

    monkeypatch.setattr(m.os, "close", uncertain)
    try:
        denied(obj.close)
        assert obj.closed and obj.handles == [] and obj.channel is None
        assert calls == list(reversed(handles))
        obj.close()
        assert calls == list(reversed(handles))
    finally:
        channel.close()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "01:2:" + "a" * 64,
        "1:2:" + "a" * 64,
        "2:0:" + "a" * 64,
        "2:3:" + "A" * 64,
        "2:3:" + "a" * 64 + "\n",
        "2:3:" + "a" * 65,
    ],
)
def test_noncanonical_peer_argument_is_refused(value):
    with pytest.raises(ValueError):
        m.parse_identity(value)


def test_fixed_paths_and_identity_codec_are_not_publication_or_baseline_paths():
    identity = m.process.ProcessIdentity(22, 123, "a" * 64)
    assert m.parse_identity(m.identity_argument(identity)) == identity
    case = peers.template_tests.value()["plan"]["case"]
    assert m.peer_root(case) == Path("/mnt/data/sdsctl-recording-peer-" + case)
    assert m.peer_root(case) != m.declaration.declaration_root(case)


@pytest.mark.parametrize(
    "value",
    [
        b"0::/\n",
        b"0::/../docker-" + b"a" * 64 + b".scope\n",
        b"x" * 4097,
        b"0::/system.slice/docker-" + b"A" * 64 + b".scope\n",
    ],
)
def test_current_process_identity_does_not_adopt_relative_or_oversize_cgroup(value, monkeypatch):
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: io.BytesIO(value))
    monkeypatch.setattr(m.process, "read_identity", lambda *_: pytest.fail("No unbound proc read"))
    denied(m.current_identity)


def test_current_process_identity_reads_exact_self_cgroup_then_independent_stat(monkeypatch):
    cid = "a" * 64
    expected = m.process.ProcessIdentity(os.getpid(), 123, cid)

    def opened(path, mode, buffering):
        assert (path, mode, buffering) == ("/proc/self/cgroup", "rb", 0)
        return io.BytesIO(f"0::/system.slice/docker-{cid}.scope\n".encode())

    def identity(pid, container):
        assert (pid, container) == (os.getpid(), cid)
        return expected

    monkeypatch.setattr("builtins.open", opened)
    monkeypatch.setattr(m.process, "read_identity", identity)
    assert m.current_identity() is expected


@pytest.mark.parametrize("deadline", [None, True, float("inf"), float("nan"), 0.0])
def test_connection_requires_finite_original_deadline_before_opening_paths(listening, deadline):
    before = fds()
    denied(lambda: m.PeerConnection(listening.root, deadline))
    assert fds() == before


def test_interrupt_cleanup_keeps_original_interrupt_and_attempts_each_close_once():
    calls = []

    def final():
        calls.append("clock")

    def bad():
        calls.append("socket")
        raise OSError("PRIVATE")

    with pytest.raises(KeyboardInterrupt):
        m._cleanup([final, bad], KeyboardInterrupt())
    assert calls == ["socket", "clock"]


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--permission-probe"],
        ["x", "a" * 64, "b" * 64, "2:3:" + "c" * 64, "--permission-probe"],
    ],
)
def test_direct_uninstalled_command_refuses_before_import_or_io(args):
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(m.__file__)), *args], capture_output=True, timeout=3
    )
    assert result.returncode == 64 and result.stdout == b""
    assert result.stderr.decode().strip() == m.MESSAGE


OBSERVER = r"""
import json, os, socket, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_permission_sender as sender
g = sender.permission
g.ROOT_UID = g.domains.ROOT_UID = os.geteuid()
def identity(pid, cid):
    return g.domains.process.process_identity(pid, cid,
        Path(f"/proc/{pid}/stat").read_text(), f"0::/system.slice/docker-{cid}.scope\n")
g.domains.process.read_identity = identity
value = json.loads(sys.stdin.buffer.readline())
template = g.templates.decode(value["template"])
timer = g.clock.ClockWitness(g.clock.read())
witness = g.domains.process.ProcessWitness(identity(os.getppid(), "b" * 64))
domain = g.domains.ZeroDomain(timer.original, witness)
obj = None
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(sys.argv[2])
        os.chmod(sys.argv[2], 0o600)
        server.listen(1)
        server.settimeout(20)
        print("ready", flush=True)
        with server.accept()[0] as channel:
            channel.setblocking(False)
            obj = sender.Sender(template, template.sha256, "d" * 64,
                identity(os.getpid(), "a" * 64), witness, domain, timer, channel,
                lambda review: None) # SYNTHETIC qualifier; no installed authority.
            obj.receive()
            if sys.argv[3] == "send":
                obj.send()
                print("local-write-complete", flush=True)
                sys.stdin.buffer.read()
finally:
    if obj:
        obj.close()
    domain.close()
    witness.close()
    timer.close()
"""


@pytest.mark.parametrize("mode", ["send", "refuse"])
def test_complete_empty_scope_probe_with_real_original_peer_and_clock(
    mode, tmp_path, monkeypatch, capsys
):
    # Actual protocol and owned handles; synthetic cgroups/path aliases only.
    monkeypatch.setattr(m.permission, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.permission.domains, "ROOT_UID", os.geteuid())

    def identity(pid, cid):
        return m.process.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m.process, "read_identity", identity)
    monkeypatch.setattr(m, "current_identity", lambda: identity(os.getpid(), "b" * 64))
    value = peers.template_tests.value()
    value["plan"]["boot"] = m.clock.read().boot
    template = m.declaration.codec.decode(value)
    root = tmp_path / "declaration"
    root.mkdir(mode=0o700)
    (root / m.declaration.NAME).write_bytes(template.raw)
    (root / m.declaration.NAME).chmod(0o600)
    monkeypatch.setattr(m.declaration, "declaration_root", lambda case: root)
    closed = []
    for cls, label in (
        (m.declaration.Declaration, "declaration"),
        (m.clock.ClockWitness, "clock"),
        (m.process.ProcessWitness, "pidfd"),
        (m.permission.domains.ZeroDomain, "domain"),
        (m.PeerConnection, "channel"),
        (m.permission.Permission, "permission"),
    ):
        actual = cls.close

        def closing(self, actual=actual, label=label):
            closed.append(label)
            return actual(self)

        monkeypatch.setattr(cls, "close", closing)

    def forbidden(*args, **kwargs):
        pytest.fail("Permission probe selected a host/service operation")

    for name in ("prepare", "prepare_service", "prepare_service_from_baseline", "idle_service"):
        monkeypatch.setattr(startup.Startup, name, forbidden)
    monkeypatch.setattr(m.permission.Permission, "prepare_service", forbidden)
    before = fds()
    with tempfile.TemporaryDirectory(prefix="protocol-") as directory:
        socket_root = Path(directory)
        monkeypatch.setattr(m, "peer_root", lambda case: socket_root)
        child = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                OBSERVER,
                str(Path(m.__file__).parent),
                str(socket_root / m.NAME),
                mode,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        try:
            child.stdin.write(json.dumps({"template": value}).encode() + b"\n")
            assert peers.line(child) == b"ready\n"
            observer = identity(child.pid, "a" * 64)
            start = time.monotonic()
            if mode == "send":
                assert m.permission_probe(root, template.sha256, "d" * 64, observer) == 75
                assert 12 < time.monotonic() - start < 16
                assert peers.line(child) == b"local-write-complete\n"
                assert "empty scope; no service action" in capsys.readouterr().out
            else:
                denied(lambda: m.permission_probe(root, template.sha256, "d" * 64, observer))
                assert capsys.readouterr().out == ""
        finally:
            child.stdin.close()
            child.wait(timeout=5)
            assert child.returncode == 0, child.stderr.read().decode()
            child.stdout.close()
            child.stderr.close()
    assert fds() == before
    assert closed == ["permission", "channel", "domain", "pidfd", "clock", "declaration"]
    assert list(root.iterdir()) == [root / m.declaration.NAME]
    assert (root / m.declaration.NAME).read_bytes() == template.raw
