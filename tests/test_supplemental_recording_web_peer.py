"""Real private sockets/processes; launch identity is explicitly a test fixture.

No scanner, Docker, HA, user file, installed source or recovery qualification.
"""

import importlib.util
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from contextvars import copy_context
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sds200.daemon_client import DaemonApiClient
from sds200.daemon_event_client import DaemonEventClient
from sds200.daemon_pcmu_client import DaemonPcmuClient
from sds200.daemon_recording_file_client import DaemonRecordingFileClient
from sds200.exceptions import DaemonUnavailableError

from .test_supplemental_recording_probe import m as probe

PATH = Path(probe.__file__).with_name("supplemental_recording_web_peer.py")
SPEC = importlib.util.spec_from_file_location("supplemental_recording_web_peer", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

TREE = r"""
import json,os,select,socket,sys,threading
sys.path.insert(0,sys.argv[2])
from sds200.daemon_recording_file_protocol import (
    encode_recording_file_response,RecordingFileResponseStatus,
)
root=sys.argv[1]
done_read,done_write=os.pipe()
ready_read,ready_write=os.pipe()
children=[]
try:
    native=os.fork()
    if native==0:
        os.close(done_write);os.close(ready_read)
        listeners=[]
        def serve(listener,name):
            while True:
                peer,_=listener.accept()
                def connection(peer=peer,name=name):
                    with peer:
                        if name in ('events','pcmu'):
                            peer.recv(1)
                            return
                        request=peer.recv(8192)
                        if not request:return
                        if name=='api':
                            value=json.loads(request)
                            reply={k:value[k] for k in ('protocol','version','request_id')}
                            reply.update(ok=True,result={'pong':True})
                            peer.sendall((json.dumps(reply)+'\n').encode())
                        else:
                            if request.endswith(b'blocked.wav'):
                                peer.sendall(encode_recording_file_response(
                                    RecordingFileResponseStatus.OK,content_length=8)+b'RIFF')
                                peer.recv(1)
                                return
                            peer.sendall(encode_recording_file_response(RecordingFileResponseStatus.OK,content_length=8)+b'RIFFtest')
                threading.Thread(target=connection,daemon=True).start()
        for name in ('api','events','pcmu','recordings'):
            listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            path=root+'/'+name+'.sock'
            listener.bind(path);os.chmod(path,0o600);listener.listen(128)
            listeners.append(listener)
            threading.Thread(target=serve,args=(listener,name),daemon=True).start()
        os.write(ready_write,b'1');os.close(ready_write)
        os.read(done_read,1)
        os._exit(0)
    children.append(native)
    watch=os.fork()
    if watch==0:
        os.close(done_write);os.close(ready_read);os.close(ready_write)
        os.read(done_read,1);os._exit(0)
    children.append(watch)
    os.close(done_read);os.close(ready_write)
    assert os.read(ready_read,1)==b'1'
    os.close(ready_read)
    facts=[]
    for pid in (os.getpid(),native,watch):
        with open(f'/proc/{pid}/stat','rb') as f:
            ticks=int(f.read().rpartition(b') ')[2].split()[19])
        facts.append(dict(pid=pid,start_ticks=ticks,uid=os.geteuid(),gid=os.getegid()))
    print(json.dumps(facts),flush=True)
    os.read(0,1)
finally:
    os.close(done_write)
    for pid in children:os.waitpid(pid,0)
"""


@pytest.fixture
def tree(tmp_path):
    sockets = tmp_path / "s"
    sockets.mkdir(mode=0o700)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", TREE, str(sockets), str(PATH.parents[1] / "src")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    handles = []
    owners = []
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        facts = json.loads(child.stdout.readline())
        assert facts[0]["pid"] == child.pid
        handles = [os.pidfd_open(item["pid"]) for item in facts]
        expected = probe.Expected(
            *(probe.Process(**item) for item in facts), "a" * 64, time.monotonic() + 10
        )

        def bind(**changes):
            selected = m.Peers(replace(expected, **changes), sockets)
            owners.append(selected)
            return selected

        yield SimpleNamespace(
            child=child, handles=handles, expected=expected, sockets=sockets, bind=bind
        )
    finally:
        for owner in owners:
            owner.close()
        for handle in handles:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(handle, signal.SIGCONT)
        child.stdin.close()
        child.stdin = None
        out, err = child.communicate(timeout=5)
        assert child.returncode == 0 and out == err == b""
        for handle in handles:
            assert select.select([handle], [], [], 0)[0]
            os.close(handle)


def wait_closed(owner):
    assert owner._stop.wait(2)
    if owner._thread is not None:
        owner._thread.join(2)
        assert not owner._thread.is_alive()
    assert owner._closed and not owner._handles and owner._directory_fd is None
    assert not owner._namespace_handles and not owner._connections


def test_four_native_factories_bind_each_original_connection(tree):
    owner = tree.bind()
    assert owner._connections == set()  # Construction opens no IPC connection.
    with owner.api() as client:
        assert type(client) is DaemonApiClient
        assert isinstance(client._socket, socket.socket)
        assert not client._socket.get_inheritable()
        assert client.request("ping") == {"pong": True}
    assert owner._connections == set()
    with owner.recordings().open("2026/test.wav") as download:
        assert download.read() == b"RIFFtest"
    assert type(owner.recordings()) is DaemonRecordingFileClient
    for factory, expected in ((owner.events, DaemonEventClient), (owner.pcmu, DaemonPcmuClient)):
        client = factory()
        assert type(client) is expected
        raw = client.connect()
        assert raw.gettimeout() <= owner._deadline - time.monotonic() + 0.1
        assert raw.gettimeout() > 0
        client.close()
    assert owner._connections == set()
    owner.close()
    wait_closed(owner)
    with pytest.raises(DaemonUnavailableError, match="original finite"):
        owner.api()


def test_deadline_is_original_and_wakes_blocked_native_read(tree):
    deadline = time.monotonic() + 0.4
    owner = tree.bind(deadline=deadline)
    client = owner.events()
    peer = client.connect()
    errors = []

    def receive():
        try:
            peer.recv(1)
        except (OSError, DaemonUnavailableError) as error:
            errors.append(type(error))

    thread = threading.Thread(target=receive)
    thread.start()
    wait_closed(owner)
    thread.join(1)
    assert not thread.is_alive() and errors
    assert owner._deadline == deadline and peer.fileno() == -1
    client.close()
    with pytest.raises(DaemonUnavailableError):
        owner.pcmu()


@pytest.mark.parametrize("role", [0, 1, 2])
def test_frozen_original_actor_closes_all_streams_and_does_not_resume(tree, role):
    owner = tree.bind()
    clients = [owner.events(), owner.pcmu()]
    peers = [client.connect() for client in clients]
    signal.pidfd_send_signal(tree.handles[role], signal.SIGSTOP)
    wait_closed(owner)
    assert all(peer.fileno() == -1 for peer in peers)
    signal.pidfd_send_signal(tree.handles[role], signal.SIGCONT)
    with pytest.raises(DaemonUnavailableError):
        owner.check()
    for client in clients:
        client.close()


@pytest.mark.parametrize("name", m.NAMES)
def test_wrong_kernel_peer_receives_no_protocol_bytes(tree, name):
    # Replace only this fixture's unused listener before the original snapshot.
    # All other native sockets still have the expected daemon PID. A path and
    # correct UID/mode cannot stand in for SO_PEERCRED's original process.
    (tree.sockets / name).rename(tree.sockets / (name + ".retained"))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
        replacement.bind(str(tree.sockets / name))
        os.chmod(tree.sockets / name, 0o600)
        replacement.listen(1)
        replacement.settimeout(1)
        owner = tree.bind()
        with pytest.raises(DaemonUnavailableError):
            owner._connect(name, 0.5)
        with replacement.accept()[0] as peer:
            peer.settimeout(1)
            assert peer.recv(1) == b""
        wait_closed(owner)


def test_socket_inode_replacement_closes_existing_stream(tree):
    owner = tree.bind()
    peer = owner.events().connect()
    (tree.sockets / "api.sock").rename(tree.sockets / "api.retained")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
        replacement.bind(str(tree.sockets / "api.sock"))
        os.chmod(tree.sockets / "api.sock", 0o600)
        replacement.listen(1)
        wait_closed(owner)
        assert peer.fileno() == -1
        assert not select.select([replacement], [], [], 0)[0]


def test_directory_replacement_cannot_redirect_through_a_symlink(tree):
    owner = tree.bind()
    old = tree.sockets.with_name("retained")
    tree.sockets.rename(old)
    tree.sockets.symlink_to(old, target_is_directory=True)
    wait_closed(owner)
    with pytest.raises(DaemonUnavailableError):
        tree.bind()


def test_later_deadline_field_does_not_renew_original(tree):
    deadline = time.monotonic() + 0.3
    selected = replace(tree.expected, deadline=deadline)
    owner = m.Peers(selected, tree.sockets)
    try:
        # Even hostile mutation of the input dataclass cannot change the
        # already copied original deadline in this continuing witness.
        object.__setattr__(selected, "deadline", deadline + 60)
        wait_closed(owner)
        assert owner._deadline == deadline
    finally:
        owner.close()


@pytest.mark.parametrize("change", ["ticks", "uid", "parent", "expired", "too_long"])
def test_wrong_original_facts_refuse_without_client_connection(tree, change):
    expected = tree.expected
    if change == "ticks":
        expected = replace(expected, native=replace(expected.native, start_ticks=1))
    elif change == "uid":
        expected = replace(expected, native=replace(expected.native, uid=os.geteuid() + 1))
    elif change == "parent":
        expected = replace(expected, guardian=expected.watchdog, watchdog=expected.guardian)
    else:
        expected = replace(
            expected, deadline=time.monotonic() + (-1 if change == "expired" else 900)
        )
    before = set(os.listdir("/proc/self/fd"))
    with pytest.raises(DaemonUnavailableError, match="original finite"):
        m.Peers(expected, tree.sockets)
    assert set(os.listdir("/proc/self/fd")) == before


def test_original_namespace_change_fails_closed(tree, monkeypatch):
    owner = tree.bind()
    monkeypatch.setattr(m, "_ns", lambda pid: ((0, 1),) * len(m.NAMESPACES))
    wait_closed(owner)


def test_bounded_connection_count_consumes_owner_without_replacing_streams(tree, monkeypatch):
    monkeypatch.setattr(m, "MAX_CONNECTIONS", 2)
    owner = tree.bind()
    peers = [owner.events().connect(), owner.pcmu().connect()]
    with pytest.raises(DaemonUnavailableError):
        owner.api().connect()
    wait_closed(owner)
    assert all(peer.fileno() == -1 for peer in peers)


def test_monitor_start_failure_cleans_all_retained_handles(tree, monkeypatch):
    def fail_start(self):
        raise OSError("private start failure")

    monkeypatch.setattr(m.threading.Thread, "start", fail_start)
    before = set(os.listdir("/proc/self/fd"))
    with pytest.raises(DaemonUnavailableError, match="original finite"):
        tree.bind()
    assert set(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("name", m.NAMES)
def test_changed_socket_permissions_refuse_and_close_without_protocol(tree, name):
    owner = tree.bind()
    os.chmod(tree.sockets / name, 0o666)
    wait_closed(owner)


def test_request_cleanup_closes_only_its_connections_and_rejects_late_workers(tree):
    owner = tree.bind()
    outside = owner.pcmu().connect()
    with owner.request_scope() as ticket:
        peer = owner.events().connect()
        old_worker = copy_context()
        assert peer._ticket is ticket and outside._ticket is None
        assert owner.check_request(ticket) > 0
    assert peer.fileno() == -1 and outside.fileno() >= 0
    with pytest.raises(DaemonUnavailableError):
        old_worker.run(lambda: owner.events().connect())
    assert owner.check() > 0 and owner._connections == {outside}
    with pytest.raises(DaemonUnavailableError):
        owner.check_request(ticket)
    with owner.request_scope() as fresh:
        assert fresh is not ticket
        with owner.api() as client:
            assert client.request("ping") == {"pong": True}
    assert owner.check() > 0
    outside.close()


def test_native_clients_keep_default_timeouts_with_original_deadline_clamp(tree):
    owner = tree.bind()
    for factory in (owner.api, owner.events, owner.pcmu, owner.recordings):
        assert factory().timeout == 5.0
