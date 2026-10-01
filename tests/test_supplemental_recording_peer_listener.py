"""Owned disposable processes and real kernel paths/sockets, not installed authority."""

import errno
import fcntl
import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_connection as connections

NAME = "supplemental_recording_peer_listener"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(connections.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

CLIENT = r"""
import json, os, socket, sys, time
from pathlib import Path
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_peer_connection as m
m.ROOT_UID = os.geteuid()
def identity(pid, cid):
    return m.processes.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
m.processes.read_identity = identity
peer = m.processes.ProcessWitness(identity(os.getppid(), "a" * 64))
owned = channel = None
print("ready", flush=True)
try:
    for line in sys.stdin:
        command = json.loads(line)
        if command["mode"] == "connect":
            owned = m.Connection(Path(sys.argv[2]), peer,
                deadline=command.get("deadline", time.monotonic() + 2))
            channel = owned.channel
            print("connected", flush=True)
        elif command["mode"] == "send":
            channel.send(b"original-message")
            print("sent", flush=True)
        elif command["mode"] == "read":
            import select
            assert select.select([channel], [], [], 2)[0]
            print(channel.recv(128).decode(), flush=True)
        elif command["mode"] == "close":
            owned.close()
            print("closed", flush=True)
        elif command["mode"] == "bootstrap":
            import supplemental_recording_peer_bootstrap as b
            b.ROOT_UID = b.links.ROOT_UID = os.geteuid()
            plan = b.links.plans.load_bytes(command["plan"].encode(), command["sha256"])
            other = m.processes.ProcessWitness(identity(command["writer"], "c" * 64))
            clock = b.links.clock.ClockWitness(b.links.clock.read())
            endpoint = bundle = None
            try:
                owned.recheck()
                endpoint = b.Endpoint(channel, plan, clock, identity(os.getpid(), "b" * 64),
                    peer, other, role="observer", mode="receive",
                    declaration_sha256=command["declaration"], deadline=owned.deadline)
                bundle, receipt = endpoint.receive()
                owned.recheck()
                print(json.dumps(dict(context=receipt.context_sha256, offer=receipt.offer_sha256,
                    deadline=endpoint.end, descriptors=b._sockets(bundle))), flush=True)
            finally:
                if endpoint is not None: endpoint.close()
                if bundle is not None: bundle.close()
                clock.close()
                other.close()
        elif command["mode"] == "finish":
            break
finally:
    if owned is not None: owned.close()
    peer.close()
"""


def refused(call):
    with pytest.raises(m.UnconfirmedListener) as error:
        call()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


@pytest.fixture
def private(monkeypatch):
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    temporary = tempfile.TemporaryDirectory(prefix="sds-peer-listener-")
    root = Path(temporary.name)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", CLIENT, str(Path(m.__file__).parent), str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    witness = None
    owners = []

    def identity(pid, cid):
        return m.processes.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m.processes, "read_identity", identity)

    def listen(**kwargs):
        options = dict(root=root, peer=witness, deadline=time.monotonic() + 2)
        options.update(kwargs)
        owner = m.Listener(**options)
        owners.append(owner)
        return owner

    def client(mode, **values):
        connections.transport.command(child, dict(mode=mode, **values))
        return connections.transport.line(child)

    try:
        assert connections.transport.line(child) == "ready"
        witness = m.processes.ProcessWitness(identity(child.pid, "b" * 64))
        yield SimpleNamespace(
            root=root, child=child, witness=witness, listen=listen, client=client, identity=identity
        )
    finally:
        for owner in owners:
            owner.close()
        if witness is not None:
            witness.close()
        if child.poll() is None:
            child.kill()  # This fixture's original disposable process only.
        child.wait(timeout=3)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()
        temporary.cleanup()


def test_private_listener_joins_real_retained_connection_without_consuming_message(private):
    before = connections.fds()
    owner = private.listen()
    path = private.root / m.NAME
    pin = path.stat()
    assert stat.S_IMODE(pin.st_mode) == 0o600 and path.is_socket()
    assert private.client("connect") == "connected"
    assert private.client("send") == "sent"
    channel = owner.accept()
    assert channel is owner.channel and owner.accepted and owner.attempted
    assert owner.listener.fileno() == -1 and owner.recheck() is None
    raw, ancillary, flags, _ = channel.recvmsg(128, socket.CMSG_SPACE(12))
    assert raw == b"original-message" and flags == 0
    assert len(ancillary) == 1 and ancillary[0][:2] == (socket.SOL_SOCKET, socket.SCM_CREDENTIALS)
    assert m.struct.unpack("3i", ancillary[0][2]) == (private.child.pid, os.geteuid(), os.getegid())
    channel.send(b"return-message")
    assert private.client("read") == "return-message"
    owner.close()
    owner.close()
    assert connections.fds() == before and not private.witness.exited()
    assert path.stat() == pin  # No unlink/rebind/metadata repair on retirement.
    refused(owner.recheck)
    refused(private.listen)  # A fresh object cannot adopt an earlier case socket.


@pytest.mark.parametrize("target", ["directory", "listener", "channel"])
def test_changed_status_flags_retire_owned_originals(private, target):
    before = connections.fds()
    owner = private.listen()
    if target == "channel":
        assert private.client("connect") == "connected"
        owner.accept()
    fd = owner.directory if target == "directory" else getattr(owner, target).fileno()
    flag = os.O_NONBLOCK if target == "directory" else os.O_APPEND
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags ^ flag)
    assert fcntl.fcntl(fd, fcntl.F_GETFL) != flags
    refused(owner.recheck)
    assert owner.closed and owner.failed and connections.fds() == before
    assert not private.witness.exited() and (private.root / m.NAME).is_socket()


@pytest.mark.parametrize("fault", ["mode", "file", "socket", "symlink", "entry", "parent-link"])
def test_unsafe_or_nonempty_leaf_never_binds_or_changes_existing_entry(
    private, tmp_path, monkeypatch, fault
):
    path = private.root / m.NAME
    root = private.root
    spare = None
    try:
        if fault == "mode":
            root.chmod(0o755)
        elif fault == "file":
            path.write_text("PRIVATE")
        elif fault == "socket":
            spare = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            spare.bind(str(path))
        elif fault == "symlink":
            path.symlink_to("missing")
        elif fault == "entry":
            (root / "unrelated").touch()
        else:
            root = tmp_path / "alias"
            root.symlink_to(private.root, target_is_directory=True)
        entries = sorted((p.name, p.lstat()) for p in private.root.iterdir())
        monkeypatch.setattr(socket.socket, "bind", lambda *a: pytest.fail("Must not bind"))
        before = connections.fds()
        refused(lambda: private.listen(root=root))
        assert connections.fds() == before
        assert sorted((p.name, p.lstat()) for p in private.root.iterdir()) == entries
    finally:
        if spare is not None:
            spare.close()


@pytest.mark.parametrize("deadline", [True, None, "1", float("nan"), float("inf"), -1])
def test_invalid_deadline_never_opens_or_writes(private, monkeypatch, deadline):
    with monkeypatch.context() as patch:
        patch.setattr(m.os, "open", lambda *a, **k: pytest.fail("Must not open"))
        refused(lambda: private.listen(deadline=deadline))
    assert list(private.root.iterdir()) == []


def test_same_original_deadline_bounds_construct_and_accept(private):
    before = connections.fds()
    end = time.monotonic() + 0.15
    owner = private.listen(deadline=end)
    assert owner.deadline == end
    refused(owner.accept)
    assert time.monotonic() < end + 0.5
    assert owner.closed and owner.failed and owner.attempted
    assert connections.fds() == before and (private.root / m.NAME).is_socket()
    refused(owner.accept)


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "socket-path",
        "parent",
        "entry",
        "permissions",
        "peer-exit",
        "peer-identity",
        "deadline",
        "blocking",
        "inheritable",
        "passcred",
    ],
)
def test_drift_or_peer_loss_permanently_closes_only_owned_handles(private, accepted, fault):
    before = connections.fds()
    owner = private.listen()
    if accepted:
        assert private.client("connect") == "connected"
        owner.accept()
    channel = owner.channel if accepted else owner.listener
    moved = None
    try:
        if fault == "socket-path":
            (private.root / m.NAME).unlink()
            (private.root / m.NAME).write_text("PRIVATE replacement")
        elif fault == "parent":
            moved = private.root.with_name(private.root.name + "-original")
            private.root.rename(moved)
            private.root.mkdir(mode=0o700)
        elif fault == "entry":
            (private.root / "extra").touch()
        elif fault == "permissions":
            private.root.chmod(0o755)
        elif fault == "peer-exit":
            private.child.kill()
            private.child.wait(timeout=3)
        elif fault == "peer-identity":
            owner.peer.identity = replace(owner.peer.identity)
        elif fault == "deadline":
            owner.deadline += 1
        elif fault == "blocking":
            channel.setblocking(True)
        elif fault == "inheritable":
            channel.set_inheritable(True)
        else:
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 0)
        refused(owner.recheck)
        assert owner.closed and owner.failed and channel.fileno() == -1
        assert connections.fds() == before
        if fault == "socket-path":
            assert (private.root / m.NAME).read_text() == "PRIVATE replacement"
    finally:
        if moved is not None:
            private.root.rmdir()
            moved.rename(private.root)


def test_wrong_live_peer_is_not_adopted_or_followed_by_another_accept(private):
    other = subprocess.Popen([sys.executable, "-I", "-c", "input()"], stdin=subprocess.PIPE)
    peer = m.processes.ProcessWitness(private.identity(other.pid, "c" * 64))
    try:
        before = connections.fds()
        owner = private.listen(peer=peer)
        assert private.client("connect") == "connected"
        refused(owner.accept)
        assert owner.closed and owner.attempted and connections.fds() == before
        assert not peer.exited() and not private.witness.exited()
        refused(owner.accept)
    finally:
        peer.close()
        other.kill()
        other.wait(timeout=3)
        other.stdin.close()


def test_second_accept_is_never_a_new_connection_or_success_cache(private):
    owner = private.listen()
    assert private.client("connect") == "connected"
    channel = owner.accept()
    refused(owner.accept)
    assert owner.closed and channel.fileno() == -1


def test_peer_channel_close_is_refused_without_consuming_a_message(private):
    owner = private.listen()
    assert private.client("connect") == "connected"
    owner.accept()
    assert private.client("close") == "closed"
    refused(owner.recheck)


@pytest.mark.parametrize("target", ["listener", "channel", "node", "directory"])
def test_foreign_reused_descriptor_is_never_closed(private, target):
    owner = private.listen()
    if target == "channel":
        assert private.client("connect") == "connected"
        owner.accept()
    value = getattr(owner, target)
    fd = value.fileno() if isinstance(value, socket.socket) else value
    spare = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.close(fd)
        os.dup2(spare, fd, inheritable=False)
        refused(owner.recheck)
        assert os.fstat(fd) == os.fstat(spare)
        if isinstance(value, socket.socket):
            assert value.fileno() == -1
    finally:
        os.close(fd)
        os.close(spare)


@pytest.mark.parametrize("target", ["listener", "channel"])
def test_foreign_socket_wrapper_is_not_adopted_or_closed(private, target):
    owner = private.listen()
    if target == "channel":
        assert private.client("connect") == "connected"
        owner.accept()
    original = getattr(owner, target)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as foreign:
        setattr(owner, target, foreign)
        refused(owner.recheck)
        assert original.fileno() == -1 and foreign.fileno() >= 0


def test_other_thread_cannot_close_original_owner(private):
    owner = private.listen()
    result = []

    def foreign():
        try:
            owner.close()
        except m.UnconfirmedListener:
            result.append(True)

    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=2)
    assert result == [True] and not owner.closed
    owner.recheck()


@pytest.mark.parametrize("stage", ["bind", "chmod", "listen", "accept"])
def test_interrupted_creation_or_accept_preserves_evidence_and_retires_originals(
    private, monkeypatch, stage
):
    before = connections.fds()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("PRIVATE detail")

    if stage == "accept":
        owner = private.listen()
        assert private.client("connect") == "connected"
        action = owner.accept
    else:
        action = private.listen
    monkeypatch.setattr(m.os if stage == "chmod" else socket.socket, stage, interrupted)
    with pytest.raises(KeyboardInterrupt):
        action()
    assert connections.fds() == before and not private.witness.exited()
    assert (private.root / m.NAME).exists() == (stage != "bind")


def test_bind_race_refuses_without_removing_or_changing_foreign_entry(private, monkeypatch):
    before = connections.fds()

    def raced(*args):
        (private.root / m.NAME).write_text("FOREIGN")
        raise OSError(errno.EADDRINUSE, "PRIVATE")

    monkeypatch.setattr(socket.socket, "bind", raced)
    refused(private.listen)
    assert connections.fds() == before
    assert (private.root / m.NAME).read_text() == "FOREIGN"


def test_direct_execution_and_import_never_expand_observed_source_profiles():
    from . import test_supplemental_recording_service_host_source as sources

    assert NAME not in sources.m.MODULES and NAME not in sources.app.m.MODULES
    result = subprocess.run(
        [sys.executable, "-B", m.__file__], capture_output=True, text=True, timeout=5
    )
    assert result.returncode != 0 and "no active launch enabled" in result.stderr


def test_listener_connection_and_descriptor_receipt_keep_same_original_cutoff(private, monkeypatch):
    """Three real processes; fixture-supplied plan/identity, NOT a writer startup."""
    b = connections.transport.m
    monkeypatch.setattr(b, "ROOT_UID", os.geteuid())
    writer = subprocess.Popen([sys.executable, "-I", "-c", "input()"], stdin=subprocess.PIPE)
    other = m.processes.ProcessWitness(private.identity(writer.pid, "c" * 64))
    clock = endpoint = None
    bundles = b.links.pair()
    try:
        origin = b.links.clock.read()
        clock = b.links.clock.ClockWitness(origin)
        issued = origin.boottime_ns / b.links.clock.NS
        raw = connections.transport.plan_tests.value()
        raw.update(
            boot=origin.boot,
            original_clock=asdict(origin) | {"namespace": list(origin.namespace)},
            deadlines=dict(
                issued_at=issued,
                ready_by=issued + 120,
                stop_by=issued + 400,
                recover_by=issued + 1500,
            ),
        )
        plan = b.links.plans.decode(raw)
        owner = private.listen()
        assert private.client("connect", deadline=owner.deadline) == "connected"
        channel = owner.accept()
        endpoint = b.Endpoint(
            channel,
            plan,
            clock,
            private.identity(os.getpid(), "a" * 64),
            private.witness,
            other,
            role="observer",
            mode="deliver",
            declaration_sha256="d" * 64,
            deadline=owner.deadline,
        )
        connections.transport.command(
            private.child,
            dict(
                mode="bootstrap",
                plan=plan.raw.decode(),
                sha256=plan.sha256,
                writer=writer.pid,
                declaration="d" * 64,
            ),
        )
        expected = b._sockets(bundles[1])
        receipt = endpoint.deliver(bundles[1])
        owner.recheck()
        observed = json.loads(connections.transport.line(private.child))
        assert (
            observed["context"] == receipt.context_sha256
            and observed["offer"] == receipt.offer_sha256
        )
        assert observed["descriptors"] == [list(pin) for pin in expected]
        assert observed["deadline"] == endpoint.end == owner.deadline
        assert not private.witness.exited() and not other.exited()
        assert list(private.root.iterdir()) == [private.root / m.NAME]
    finally:
        if endpoint is not None:
            endpoint.close()
        for bundle in bundles:
            bundle.close()
        if clock is not None:
            clock.close()
        other.close()
        writer.kill()
        writer.wait(timeout=3)
        writer.stdin.close()


def test_expiry_during_construction_is_not_renewed_before_listen(private, monkeypatch):
    chmod = m.os.chmod

    def slow(*args, **kwargs):
        chmod(*args, **kwargs)
        monkeypatch.setattr(m.time, "monotonic", lambda: float("inf"))

    monkeypatch.setattr(m.os, "chmod", slow)
    monkeypatch.setattr(socket.socket, "listen", lambda *a: pytest.fail("Budget renewed"))
    before = connections.fds()
    refused(private.listen)
    assert connections.fds() == before and (private.root / m.NAME).is_socket()


def test_provisioning_does_not_change_process_umask(private, monkeypatch):
    monkeypatch.setattr(m.os, "umask", lambda *a: pytest.fail("Process umask changed"))
    owner = private.listen()
    assert stat.S_IMODE((private.root / m.NAME).stat().st_mode) == 0o600
    owner.recheck()


def test_permission_change_uses_original_retained_socket_inode(private, monkeypatch):
    chmod = m.os.chmod
    saved = private.root / "original-evidence"
    foreign = private.root / m.NAME

    def replaced(path, mode):
        foreign.rename(saved)
        foreign.write_text("FOREIGN")
        chmod(str(foreign), 0o644)
        chmod(path, mode)

    monkeypatch.setattr(m.os, "chmod", replaced)
    before = connections.fds()
    refused(private.listen)
    assert connections.fds() == before and foreign.read_text() == "FOREIGN"
    assert stat.S_IMODE(foreign.stat().st_mode) == 0o644
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600 and saved.is_socket()


def test_uncertain_directory_close_retires_all_handles_once(private, monkeypatch):
    before = connections.fds()
    owner = private.listen()
    originals = [fd for fd, _, _ in owner.handles]
    close, seen = os.close, []

    def uncertain(fd):
        seen.append(fd)
        close(fd)
        if fd == originals[-1]:
            raise OSError("PRIVATE uncertain close")

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "close", uncertain)
        refused(owner.close)
        owner.close()
    assert seen == list(reversed(originals)) and connections.fds() == before
