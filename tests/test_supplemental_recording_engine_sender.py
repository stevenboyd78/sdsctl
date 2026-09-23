"""Actual inherited listener/SCM credentials/pidfds; owned non-root fixtures.

No Docker/HA/Engine action. Root UID/GID constants are explicitly substituted
with the test account; these tests cannot qualify installed root ownership.
"""

import importlib.util
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

NAME = "supplemental_recording_engine_sender"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(__file__).parents[1] / "scripts" / (NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
REPLY = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nOK"
SERVER = r"""
import array,json,os,socket,sys
config=json.loads(sys.argv[2])
listener=socket.socket(fileno=int(sys.argv[1]))
listener.settimeout(4)
def header(peer):
    result=bytearray()
    while not result.endswith(b'\r\n\r\n'):
        raw=peer.recv(1)
        if not raw:return None
        result.extend(raw)
        assert len(result)<20000
    return bytes(result)
print('ready',flush=True)
for _ in range(config['connections']):
    peer,_=listener.accept()
    peer.settimeout(4)
    try:
        request=header(peer)
        if request is None:continue
        assert request==bytes.fromhex(config['ping'])
        response=bytes.fromhex(config['response'])
        mode=config['mode']
        if mode=='rights':
            fd=os.open('/dev/null',os.O_RDONLY)
            peer.sendmsg([response],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
            os.close(fd)
        else:
            for offset in range(0,len(response),config['chunk']):
                peer.sendall(response[offset:offset+config['chunk']])
        if mode=='close':continue
        request=header(peer)
        if request is None:continue
        if b'Content-Length: ' in request:
            length=int(request.split(b'Content-Length: ',1)[1].split(b'\r\n',1)[0])
            while length:
                part=peer.recv(length)
                assert part
                request+=part
                length-=len(part)
        assert request==bytes.fromhex(config['request'])
        payload=bytes.fromhex(config['payload'])
        if config['probe']:
            peer.sendall(bytes.fromhex(config['upgrade']))
            def exact(size):
                result=bytearray()
                while len(result)<size:
                    part=peer.recv(size-len(result))
                    assert part
                    result.extend(part)
                return bytes(result)
            size=int.from_bytes(exact(4),'big')
            assert size<65536
            assert json.loads(exact(size))=={'kind':'finite-recording-cached-probe'}
        if mode=='changed':
            child=os.fork()
            if not child:
                try:peer.sendall(payload)
                finally:os._exit(0)
            os.waitpid(child,0)
        else:
            peer.sendall(payload)
        if config['probe'] or mode=='eof':peer.shutdown(socket.SHUT_WR)
        while peer.recv(4096):pass
    except (BrokenPipeError,ConnectionResetError):
        pass
    finally:peer.close()
listener.close()
sys.stdin.read()
"""


@contextmanager
def server(
    tmp_path,
    monkeypatch,
    *,
    response=REPLY,
    mode="normal",
    chunk=4096,
    connections=1,
    request=b"GET /fixture HTTP/1.1\r\n\r\n",
    payload=b"fixture",
    probe=False,
    upgrade=b"",
):
    path = tmp_path / "sender.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(2)
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m, "ROOT_GID", os.getegid())
    config = dict(
        ping=m.PING.hex(),
        response=response.hex(),
        mode=mode,
        chunk=chunk,
        connections=connections,
        request=request.hex(),
        payload=payload.hex(),
        probe=probe,
        upgrade=upgrade.hex(),
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", SERVER, str(listener.fileno()), json.dumps(config)],
        pass_fds=(listener.fileno(),),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    handles, sender = [], m.Sender()
    try:
        assert select.select([child.stdout], [], [], 3)[0]
        assert child.stdout.readline() == b"ready\n"

        def connect():
            channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            channel.connect(str(path))
            handles.append(channel)
            return channel

        yield SimpleNamespace(
            child=child, sender=sender, connect=connect, listener=listener, path=path
        )
    finally:
        for channel in handles:
            channel.close()
        sender.close()
        if child.poll() is None:
            # Only this fixture's exact owned process; undo a deliberate test stop.
            child.send_signal(signal.SIGCONT)
        child.stdin.close()
        child.stdin = None
        output, error = child.communicate(timeout=5)
        listener.close()
        assert child.returncode == 0 and output == error == b"", error.decode()


def denied(callback, sender):
    with pytest.raises(m.UnconfirmedSender) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)
    assert sender.closed and sender.creator_fd == sender.peer_fd == -1


@pytest.mark.parametrize("chunk", [1, 7, 4096])
def test_real_sender_is_not_the_live_inherited_listener_creator(tmp_path, monkeypatch, chunk):
    with server(tmp_path, monkeypatch, chunk=chunk) as fixture:
        channel = fixture.connect()
        fixture.sender.ping(channel, deadline=time.monotonic() + 1)
        assert fixture.sender.creator[0] == os.getpid()
        assert fixture.sender.peer[0] == fixture.child.pid
        assert fixture.sender.creator_fd != fixture.sender.peer_fd
        fixture.sender.check()
        assert channel.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1
        channel.sendall(b"GET /fixture HTTP/1.1\r\n\r\n")
        assert select.select([channel], [], [], 1)[0]
        assert fixture.sender.receive(channel, 4096) == b"fixture"
        assert not hasattr(fixture.sender, "create") and not hasattr(fixture.sender, "signal")


def test_later_connections_retain_original_creator_and_actual_sender(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch, connections=2) as fixture:
        first = fixture.connect()
        fixture.sender.ping(first, deadline=time.monotonic() + 1)
        identity = (
            fixture.sender.creator,
            fixture.sender.peer,
            fixture.sender.creator_fd,
            fixture.sender.peer_fd,
        )
        first.close()
        second = fixture.connect()
        fixture.sender.ping(second, deadline=time.monotonic() + 1)
        assert identity == (
            fixture.sender.creator,
            fixture.sender.peer,
            fixture.sender.creator_fd,
            fixture.sender.peer_fd,
        )


@pytest.mark.parametrize(
    "fault",
    [
        "status",
        "length",
        "type",
        "encoding",
        "chunked",
        "duplicate",
        "close",
        "body",
        "oversize",
        "extra",
        "truncated",
    ],
)
def test_ping_framing_is_closed_and_failure_closes_only_owned_handles(tmp_path, monkeypatch, fault):
    reply = REPLY
    mode = "normal"
    if fault == "status":
        reply = reply.replace(b"200 OK", b"500 PRIVATE")
    elif fault == "length":
        reply = reply.replace(b"Length: 2", b"Length: 3")
    elif fault == "type":
        reply = reply.replace(b"text/plain", b"application/json")
    elif fault == "encoding":
        reply = reply.replace(b"Content-Length:", b"Content-Encoding: gzip\r\nContent-Length:")
    elif fault == "chunked":
        reply = reply.replace(b"Content-Length:", b"Transfer-Encoding: chunked\r\nContent-Length:")
    elif fault == "duplicate":
        reply = reply.replace(b"Content-Length:", b"content-length: 2\r\nContent-Length:")
    elif fault == "close":
        reply = reply.replace(b"Content-Length:", b"Connection: close\r\nContent-Length:")
    elif fault == "body":
        reply = reply[:-2] + b"NO"
    elif fault == "oversize":
        reply = b"X" * (m.MAX_HEADER + 3)
    elif fault == "extra":
        reply += b"PRIVATE"
    else:
        reply, mode = b"HTTP/1.1 200", "close"
    with server(tmp_path, monkeypatch, response=reply, mode=mode) as fixture:
        channel = fixture.connect()
        before = len(os.listdir("/proc/self/fd"))
        denied(lambda: fixture.sender.ping(channel, deadline=time.monotonic() + 1), fixture.sender)
        assert channel.fileno() >= 0 and len(os.listdir("/proc/self/fd")) == before
        denied(lambda: fixture.sender.ping(channel, deadline=time.monotonic() + 1), fixture.sender)


def test_ancillary_rights_are_closed_not_inherited_as_peer_evidence(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch, mode="rights") as fixture:
        channel = fixture.connect()
        before = len(os.listdir("/proc/self/fd"))
        denied(lambda: fixture.sender.ping(channel, deadline=time.monotonic() + 1), fixture.sender)
        assert len(os.listdir("/proc/self/fd")) == before


def test_changed_live_writer_cannot_reuse_original_creator_and_ping(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch, mode="changed") as fixture:
        channel = fixture.connect()
        fixture.sender.ping(channel, deadline=time.monotonic() + 1)
        channel.sendall(b"GET /fixture HTTP/1.1\r\n\r\n")
        assert select.select([channel], [], [], 1)[0]
        denied(lambda: fixture.sender.receive(channel, 4096), fixture.sender)
        assert fixture.child.poll() is None  # Original sender is still alive, but not the writer.


@pytest.mark.parametrize(
    "fault", ["disabled", "closed_fd", "creator_ticks", "sender_ticks", "stopped"]
)
def test_uncertain_sender_is_not_rebound(tmp_path, monkeypatch, fault):
    with server(tmp_path, monkeypatch) as fixture:
        channel = fixture.connect()
        fixture.sender.ping(channel, deadline=time.monotonic() + 1)
        if fault == "disabled":
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 0)
        elif fault == "closed_fd":
            os.close(fixture.sender.peer_fd)
        elif fault in ("creator_ticks", "sender_ticks"):
            field = "creator" if fault == "creator_ticks" else "peer"
            pid, ticks, uid, gid = getattr(fixture.sender, field)
            setattr(fixture.sender, field, (pid, ticks + 1, uid, gid))
        else:
            signal.pidfd_send_signal(fixture.sender.peer_fd, signal.SIGSTOP)
            limit = time.monotonic() + 1
            while (
                Path(f"/proc/{fixture.child.pid}/stat").read_text().rpartition(") ")[2].split()[0]
                != "T"
            ):
                assert time.monotonic() < limit
        denied(lambda: fixture.sender.receive(channel, 4096), fixture.sender)


def test_nonblocking_empty_read_does_not_poison_or_send_another_ping(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as fixture:
        channel = fixture.connect()
        fixture.sender.ping(channel, deadline=time.monotonic() + 1)
        with pytest.raises(BlockingIOError):
            fixture.sender.receive(channel, 4096)
        fixture.sender.check()
        assert not fixture.sender.closed


def test_empty_eof_is_framing_only_while_original_writer_remains_live(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch, mode="eof") as fixture:
        channel = fixture.connect()
        fixture.sender.ping(channel, deadline=time.monotonic() + 1)
        original = fixture.sender.peer, fixture.sender.peer_fd
        channel.sendall(b"GET /fixture HTTP/1.1\r\n\r\n")
        assert select.select([channel], [], [], 1)[0]
        assert fixture.sender.receive(channel, 4096) == b"fixture"
        assert select.select([channel], [], [], 1)[0]
        assert fixture.sender.receive(channel, 4096) == b""
        assert original == (fixture.sender.peer, fixture.sender.peer_fd)
        assert fixture.child.poll() is None
        fixture.sender.check()


def test_sender_exit_is_not_hidden_by_live_listener_creator(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as fixture:
        channel = fixture.connect()
        fixture.sender.ping(channel, deadline=time.monotonic() + 1)
        peer_fd = fixture.sender.peer_fd
        channel.close()
        fixture.child.stdin.close()
        assert select.select([peer_fd], [], [], 2)[0]
        assert not select.select([fixture.sender.creator_fd], [], [], 0)[0]
        denied(lambda: fixture.sender.receive(channel, 4096), fixture.sender)


@pytest.mark.parametrize("invalid", [True, None, -1, float("nan"), float("inf")])
def test_bad_deadline_never_sends_ping(tmp_path, monkeypatch, invalid):
    with server(tmp_path, monkeypatch) as fixture:
        channel = fixture.connect()
        denied(lambda: fixture.sender.ping(channel, deadline=invalid), fixture.sender)


def test_unpinged_socket_and_wrong_account_are_not_credentials(tmp_path, monkeypatch):
    with server(tmp_path, monkeypatch) as fixture:
        channel = fixture.connect()
        denied(lambda: fixture.sender.receive(channel, 4096), fixture.sender)
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid() + 1)
    with pytest.raises(m.UnconfirmedSender):
        m.Sender()
