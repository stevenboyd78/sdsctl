"""Actual Unix socket/pidfd and durable requests, explicitly synthetic Engine.

The fixture substitutes only the fixed socket path/root IDs with its owned local
server. It does not contact Docker or claim installed root-peer/source proof.
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Event, Thread

import pytest

from . import test_supplemental_recording_attachment as attached
from . import test_supplemental_recording_dispatch as intents

NAME = "supplemental_recording_engine"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(intents.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
layout, tree, routing, projection, binding, directory, prepared = (
    intents.layout,
    intents.tree,
    intents.routing,
    intents.projection,
    intents.binding,
    intents.directory,
    intents.prepared,
)


def read_request(peer):
    header = bytearray()
    while b"\r\n\r\n" not in header:
        octet = peer.recv(1)
        if not octet and not header:
            return None  # Credential rejection must send no HTTP bytes.
        assert octet
        header.extend(octet)
        assert len(header) <= m.MAX_HEADER
    lines = header.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:] if line)
    body = attached.exact(peer, int(headers["Content-Length"]))
    return lines[0], headers, json.loads(body) if body else None


def reply(value, status=200):
    body = json.dumps(value, separators=(",", ":")).encode()
    return (
        f"HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode() + body


@contextmanager
def engine(prepared, monkeypatch, handlers):
    endpoint = client = None
    path = prepared.directory.parent / "PRIVATE_engine.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    path.chmod(0o600)
    listener.listen(2)
    listener.settimeout(0.05)
    stop, requests, errors = Event(), [], []
    monkeypatch.setattr(m, "SOCKET", path)
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m, "ROOT_GID", os.getegid())

    def serve():
        while not stop.is_set():
            try:
                peer, _ = listener.accept()
            except TimeoutError:
                continue
            try:
                peer.settimeout(3)
                request = read_request(peer)
                if request is None:
                    continue
                requests.append(request)
                assert len(requests) <= len(handlers), "Never send an extra request"
                handler = handlers[len(requests) - 1]
                if callable(handler):
                    handler(peer, request)
                else:
                    peer.sendall(handler)
            except BaseException as error:
                errors.append(error)
            finally:
                peer.close()

    worker = Thread(target=serve)
    worker.start()
    try:
        claim = intents.create(prepared)
        endpoint = m.Endpoint()
        client = m.Client(endpoint, claim)
        yield client, requests
    finally:
        if client is not None:
            client.close()
        elif endpoint is not None:
            endpoint.close()
        stop.set()
        worker.join(timeout=4)
        listener.close()
        assert not worker.is_alive() and not errors, errors


def refused(action, client):
    with pytest.raises(m.UnconfirmedEngine) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)
    assert client.closed and client.claim.poisoned and client.endpoint.closed
    with pytest.raises(m.UnconfirmedEngine):
        client.create()


def test_fixed_create_inspect_and_upgrade_follow_actual_durable_intents(prepared, monkeypatch):
    def created(peer, request):
        state = intents.m.load(prepared.directory, prepared.pins)
        assert state.phase == "create_intent"
        assert (
            request[0] == f"POST /v1.47/containers/{prepared.pins.init.container_id}/exec HTTP/1.1"
        )
        assert request[2] == prepared.pins.command.create_body()
        peer.sendall(reply({"Id": attached.EXEC}, 201))

    def inspected(peer, request):
        assert intents.m.load(prepared.directory, prepared.pins).phase == "created"
        assert request[0] == f"GET /v1.47/exec/{attached.EXEC}/json HTTP/1.1"
        assert request[2] is None
        peer.sendall(reply(intents.metadata(prepared)))

    def upgraded(peer, request):
        assert intents.m.load(prepared.directory, prepared.pins).phase == "attach_intent"
        assert request[0] == f"POST /v1.47/exec/{attached.EXEC}/start HTTP/1.1"
        assert request[1]["Connection"] == "Upgrade" and request[1]["Upgrade"] == "tcp"
        assert request[2] == {"Detach": False, "Tty": False}
        peer.sendall(
            attached.stream.UPGRADE
            + attached.stream.segment(attached.stream.app({"phase": "ready"}))
        )
        assert attached.begun(peer) == attached.BEGIN
        peer.sendall(
            attached.stream.segment(
                b"".join(attached.stream.app(v) for v in attached.stream.MESSAGES[1:])
            )
        )

    with engine(prepared, monkeypatch, [created, inspected, upgraded]) as (client, requests):
        assert client.create() == attached.EXEC
        channel = client.attach(finish_by=prepared.pins.command.ready_by + 5)
        assert channel.receive(deadline=channel.ready_by) == {"phase": "ready"}
        # Framing-only synthetic begin, NOT host recording authorization.
        channel.send_begin(attached.BEGIN, deadline=channel.ready_by)
        assert [
            channel.receive(deadline=channel.finish_by) for _ in range(3)
        ] == attached.stream.MESSAGES[1:]
        channel.finish(deadline=channel.finish_by)
        assert len(requests) == 3 and channel.finished
        assert client.endpoint.peer[0] == os.getpid()
        assert client.endpoint.peer_fd >= 0  # Actual retained kernel pidfd.
        assert not hasattr(client, "native_exit") and not hasattr(client, "begin")


@pytest.mark.parametrize("stage", ["create", "inspect", "start"])
def test_lost_response_consumes_exact_dispatch_without_retry(prepared, monkeypatch, stage):
    handlers = [
        reply({"Id": attached.EXEC}, 201),
        reply(intents.metadata(prepared)),
        attached.stream.UPGRADE,
    ]
    index = ["create", "inspect", "start"].index(stage)
    handlers[index] = b""  # Request was read; response was lost.
    with engine(prepared, monkeypatch, handlers[: index + 1]) as (client, requests):
        if index:
            client.create()
        refused(
            client.create
            if not index
            else lambda: client.attach(finish_by=prepared.pins.command.ready_by + 5),
            client,
        )
        assert len(requests) == index + 1
        state = intents.m.load(prepared.directory, prepared.pins)
        assert state.phase == ["create_intent", "created", "attach_intent"][index]


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "wrong_id",
        "extra",
        "array",
        "private_error",
        "chunked",
        "truncated",
        "oversize",
        "redirect",
        "encoding",
        "duplicate_header",
        "bad_header",
    ],
)
def test_create_reply_must_be_bounded_closed_json(prepared, monkeypatch, fault):
    response = reply({"Id": attached.EXEC}, 201)
    if fault == "duplicate":
        response = reply({"Id": attached.EXEC}, 201)
        original = response.split(b"\r\n\r\n", 1)[1]
        altered = b'{"Id":"PRIVATE",' + original[1:]
        response = response.replace(
            str(len(original)).encode() + b"\r\n", str(len(altered)).encode() + b"\r\n"
        ).replace(original, altered)
    elif fault == "wrong_id":
        response = reply({"Id": "PRIVATE"}, 201)
    elif fault == "extra":
        response = reply({"Id": attached.EXEC, "PRIVATE": True}, 201)
    elif fault == "array":
        response = reply([], 201)
    elif fault == "private_error":
        response = reply({"message": "PRIVATE"}, 500)
    elif fault == "chunked":
        response = response.replace(
            b"Content-Type:", b"Transfer-Encoding: chunked\r\nContent-Type:"
        )
    elif fault == "truncated":
        response = response[:-1]
    elif fault == "oversize":
        response = reply({"Id": "x" * m.MAX_BODY}, 201)
    elif fault == "redirect":
        response = response.replace(b"201 OK", b"307 Redirect")
    elif fault == "encoding":
        response = response.replace(b"Content-Type:", b"Content-Encoding: gzip\r\nContent-Type:")
    elif fault == "duplicate_header":
        response = response.replace(b"Content-Length:", b"content-length: 1\r\nContent-Length:")
    else:
        response = response.replace(b"Connection: close", b"Connection: PRIVATE\x7f")
    with engine(prepared, monkeypatch, [response]) as (client, requests):
        refused(client.create, client)
        assert len(requests) == 1
        assert intents.m.load(prepared.directory, prepared.pins).phase == "create_intent"


@pytest.mark.parametrize("fault", ["id", "container", "running", "argv"])
def test_wrong_inspection_never_sends_start(prepared, monkeypatch, fault):
    value = intents.metadata(prepared)
    if fault == "id":
        value["ID"] = "f" * 64
    elif fault == "container":
        value["ContainerID"] = "f" * 64
    elif fault == "running":
        value.update(Running=True, Pid=123)
    else:
        value["ProcessConfig"]["arguments"].append("PRIVATE")
    with engine(prepared, monkeypatch, [reply({"Id": attached.EXEC}, 201), reply(value)]) as (
        client,
        requests,
    ):
        client.create()
        refused(lambda: client.attach(finish_by=prepared.pins.command.ready_by + 5), client)
        assert len(requests) == 2 and client.claim.state.phase == "created"


@pytest.mark.parametrize(
    "fault",
    ["directory", "history", "init_exit", "socket_mode", "parent_mode", "peer_fd", "peer_ticks"],
)
def test_changed_original_evidence_refuses_next_network_request(prepared, monkeypatch, fault):
    with engine(prepared, monkeypatch, [reply({"Id": attached.EXEC}, 201)]) as (client, requests):
        client.create()
        if fault == "directory":
            original = prepared.directory.with_name("PRIVATE_old")
            prepared.directory.rename(original)
            prepared.directory.mkdir(mode=0o700)
            for old in original.iterdir():
                new = prepared.directory / old.name
                new.write_bytes(old.read_bytes())
                new.chmod(0o600)
        elif fault == "history":
            (prepared.directory / "0001.json").write_bytes(b"PRIVATE")
        elif fault == "init_exit":
            prepared.process.stdin.close()
            prepared.process.wait(timeout=3)
        elif fault == "socket_mode":
            m.SOCKET.chmod(0o666)
        elif fault == "parent_mode":
            m.SOCKET.parent.chmod(0o755)
        elif fault == "peer_fd":
            os.close(client.endpoint.peer_fd)
        else:
            monkeypatch.setattr(m, "_ticks", lambda *_: 1)
        refused(lambda: client.attach(finish_by=prepared.pins.command.ready_by + 5), client)
        assert len(requests) == 1


@pytest.mark.parametrize("position", [1, 2])
def test_lost_created_publication_prevents_inspect_and_start(prepared, monkeypatch, position):
    with engine(prepared, monkeypatch, [reply({"Id": attached.EXEC}, 201)]) as (client, requests):
        original, calls = os.fsync, []

        def lost(fd):
            original(fd)
            calls.append(fd)
            if len(calls) == position:
                raise OSError("PRIVATE")

        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", lost)
            refused(client.create, client)
        assert len(requests) == 1
        assert intents.m.load(prepared.directory, prepared.pins).phase == "created"


def test_new_client_cannot_turn_existing_exec_id_into_another_create(prepared, monkeypatch):
    with engine(prepared, monkeypatch, [reply({"Id": attached.EXEC}, 201)]) as (client, requests):
        client.create()
        second = m.Client(client.endpoint, client.claim)
        refused(second.create, second)
        assert len(requests) == 1


def test_endpoint_rejects_untrusted_kernel_peer_credentials(prepared, monkeypatch):
    with engine(prepared, monkeypatch, []) as (client, requests):
        # Preserve actual local UID ownership checks but require a different
        # peer GID. The connected server receives no HTTP byte before refusal.
        monkeypatch.setattr(m, "ROOT_GID", os.getegid() + 1)
        refused(client.create, client)
        assert not requests


def test_duplicate_create_is_never_retried(prepared, monkeypatch):
    with engine(prepared, monkeypatch, [reply({"Id": attached.EXEC}, 201)]) as (client, requests):
        client.create()
        refused(client.create, client)
        assert len(requests) == 1


@pytest.mark.parametrize("position", [1, 2])
def test_lost_attach_publication_prevents_start(prepared, monkeypatch, position):
    handlers = [reply({"Id": attached.EXEC}, 201), reply(intents.metadata(prepared))]
    with engine(prepared, monkeypatch, handlers) as (client, requests):
        client.create()
        original, calls = os.fsync, []

        def lost(fd):
            original(fd)
            calls.append(fd)
            if len(calls) == position:
                raise OSError("PRIVATE")

        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", lost)
            refused(lambda: client.attach(finish_by=prepared.pins.command.ready_by + 5), client)
        assert len(requests) == 2
        assert intents.m.load(prepared.directory, prepared.pins).phase == "attach_intent"


def test_slow_response_cannot_extend_original_deadline(prepared, monkeypatch):
    def slow(peer, _request):
        # Intentional fixture delay beyond the per-request 1s bound, not a
        # production retry/backoff. Neither a late response nor EOF proves exit.
        time.sleep(1.1)
        with suppress(BrokenPipeError):
            peer.sendall(reply({"Id": attached.EXEC}, 201))

    with engine(prepared, monkeypatch, [slow]) as (client, requests):
        began = time.monotonic()
        refused(client.create, client)
        assert time.monotonic() - began < 2
        assert len(requests) == 1 and client.claim.state.phase == "create_intent"


@pytest.mark.parametrize("fault", ["partial", "stderr", "wrong_status"])
def test_unusable_upgrade_keeps_attach_consumed(prepared, monkeypatch, fault):
    bad = {
        "partial": attached.stream.UPGRADE[:-1],
        "stderr": attached.stream.UPGRADE + attached.stream.segment(b"PRIVATE", channel=2),
        "wrong_status": reply({"PRIVATE": True}),
    }[fault]
    handlers = [reply({"Id": attached.EXEC}, 201), reply(intents.metadata(prepared)), bad]
    with engine(prepared, monkeypatch, handlers) as (client, requests):
        client.create()
        refused(lambda: client.attach(finish_by=prepared.pins.command.ready_by + 5), client)
        assert len(requests) == 3
        assert intents.m.load(prepared.directory, prepared.pins).phase == "attach_intent"


@pytest.mark.parametrize(
    "fault", ["world_writable", "special_mode", "hardlink", "symlink", "regular"]
)
def test_endpoint_rejects_unsafe_socket_before_connection(prepared, monkeypatch, fault):
    path = prepared.directory.parent / "PRIVATE_endpoint.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen(1)
        if fault == "world_writable":
            path.chmod(0o666)
        elif fault == "special_mode":
            path.chmod(0o4600)
        elif fault == "hardlink":
            os.link(path, path.with_name("PRIVATE_alias"))
        elif fault == "symlink":
            original = path.with_name("PRIVATE_original")
            path.rename(original)
            path.symlink_to(original)
        else:
            path.rename(path.with_name("PRIVATE_original"))
            path.write_bytes(b"PRIVATE")
            path.chmod(0o600)
        monkeypatch.setattr(m, "SOCKET", path)
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
        with pytest.raises(m.UnconfirmedEngine, match=m.MESSAGE):
            m.Endpoint()
    finally:
        listener.close()


def test_actual_owned_engine_peer_exit_prevents_reconnection(prepared, monkeypatch):
    path = prepared.directory.parent / "PRIVATE_child.sock"
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            (
                "import socket,sys,os; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
                "s.bind(sys.argv[1]);os.chmod(sys.argv[1],0o600);s.listen(1);"
                "print('ready',flush=True);sys.stdin.read();s.close()"
            ),
            str(path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    endpoint = None
    try:
        assert child.stdout.readline() == b"ready\n"
        monkeypatch.setattr(m, "SOCKET", path)
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
        monkeypatch.setattr(m, "ROOT_GID", os.getegid())
        endpoint = m.Endpoint()
        channel = endpoint.connect(deadline=time.monotonic() + 1)
        channel.close()
        assert endpoint.peer[0] == child.pid
        child.stdin.close()
        assert child.wait(timeout=3) == 0
        with pytest.raises(m.UnconfirmedEngine, match=m.MESSAGE):
            endpoint.connect(deadline=time.monotonic() + 1)
        assert endpoint.closed and endpoint.peer_fd == -1
    finally:
        if endpoint is not None:
            endpoint.close()
        child.stdin.close()
        child.stdout.close()
        child.wait(timeout=3)
