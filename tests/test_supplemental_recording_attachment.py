"""Actual private Unix I/O with a synthetic Engine peer; never contacts Docker."""

import importlib.util
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import pytest

from . import test_supplemental_recording_engine_sender as senders
from . import test_supplemental_recording_exec_stream as stream

NAME = "supplemental_recording_attachment"
SPEC = importlib.util.spec_from_file_location(NAME, Path(stream.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
assert m.senders is senders.m
EXEC = "e" * 64
BEGIN = {"phase": "begin", "private": "PRIVATE"}


def exact(peer, size):
    result = bytearray()
    while len(result) < size:
        chunk = peer.recv(size - len(result))
        assert chunk
        result.extend(chunk)
    return bytes(result)


def request(peer):
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        raw.extend(exact(peer, 1))
        assert len(raw) < 8192
    expected = (
        f"POST /v1.47/exec/{EXEC}/start HTTP/1.1\r\n"
        "Host: localhost\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
        "Content-Type: application/json\r\nContent-Length: 28\r\n\r\n"
    ).encode()
    body = b'{"Detach":false,"Tty":false}'
    # Count the actual fixed body instead of assuming a transport newline.
    expected = expected.replace(b"Content-Length: 28", f"Content-Length: {len(body)}".encode())
    assert bytes(raw) == expected and exact(peer, len(body)) == body


def begun(peer):
    size = stream.w.HEADER.unpack(exact(peer, 4))[0]
    raw = exact(peer, size)
    assert stream.w.encode(json.loads(raw)) == raw
    return json.loads(raw)


@contextmanager
def attached(server, *, budget=3):
    client, peer = socket.socketpair()
    peer.settimeout(5)
    now, errors = time.monotonic(), []
    attachment = m.Attachment(client, EXEC, ready_by=now + budget, finish_by=now + budget + 3)

    def serve():
        try:
            server(peer)
        except BaseException as error:
            errors.append(error)
        finally:
            peer.close()

    thread = Thread(target=serve)
    thread.start()
    try:
        yield attachment
    finally:
        attachment.close()
        thread.join(6)
        assert not thread.is_alive(), "Synthetic peer must not outlive the test"
        assert not errors, errors
        assert client.fileno() == peer.fileno() == -1


def deny(action, attachment):
    with pytest.raises(m.UnconfirmedAttachment) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)
    assert attachment.closed and not attachment.finished
    with pytest.raises(m.UnconfirmedAttachment):
        attachment.start(deadline=time.monotonic() + 1)


@pytest.mark.parametrize("chunks", [1, 7, 65536])
@pytest.mark.parametrize("coalesced", [False, True])
def test_actual_full_duplex_fixed_request_and_raw_begin(chunks, coalesced):
    def send(peer, payload):
        for i in range(0, len(payload), chunks):
            peer.sendall(payload[i : i + chunks])

    def server(peer):
        request(peer)
        ready = stream.segment(stream.app(stream.MESSAGES[0]))
        if coalesced:
            send(peer, stream.UPGRADE + ready)
        else:
            send(peer, stream.UPGRADE)
            send(peer, ready)
        assert begun(peer) == BEGIN
        # Several operator frames may share one Docker segment and socket read.
        send(peer, stream.segment(b"".join(stream.app(x) for x in stream.MESSAGES[1:])))

    with attached(server) as attachment:
        attachment.start(deadline=attachment.ready_by)
        assert attachment.receive(deadline=attachment.ready_by) == stream.MESSAGES[0]
        attachment.send_begin(BEGIN, deadline=attachment.ready_by)
        assert [
            attachment.receive(deadline=attachment.finish_by) for _ in range(3)
        ] == stream.MESSAGES[1:]
        attachment.finish(deadline=attachment.finish_by)
        assert attachment.finished and attachment.closed
        # Framing only: no exit code or acknowledgment is invented.
        assert not hasattr(attachment, "exit_code") and not hasattr(attachment, "acknowledgment")


@pytest.mark.parametrize("stage", ["upgrade", "ready", "begun", "completed"])
def test_eof_at_each_incomplete_stage_is_not_a_success(stage):
    def server(peer):
        request(peer)
        if stage == "upgrade":
            return
        peer.sendall(stream.UPGRADE)
        if stage == "ready":
            return
        peer.sendall(stream.segment(stream.app(stream.MESSAGES[0])))
        assert begun(peer) == BEGIN
        if stage == "completed":
            peer.sendall(
                stream.segment(stream.app(stream.MESSAGES[1]) + stream.app(stream.MESSAGES[2]))
            )

    with attached(server) as attachment:

        def consume():
            attachment.start(deadline=attachment.ready_by)
            attachment.receive(deadline=attachment.ready_by)
            attachment.send_begin(BEGIN, deadline=attachment.ready_by)
            for _ in range(3):
                attachment.receive(deadline=attachment.finish_by)

        deny(consume, attachment)


@pytest.mark.parametrize("where", ["upgrade", "ready", "begin", "finish"])
def test_original_deadlines_cannot_be_extended(where):
    # No server request is needed: invalid step deadlines fail before I/O.
    def server(peer):
        assert peer.recv(1) == b""

    with attached(server) as attachment:
        actions = {
            "upgrade": lambda: attachment.start(deadline=attachment.ready_by + 0.1),
            "ready": lambda: attachment.receive(deadline=attachment.ready_by + 0.1),
            "begin": lambda: attachment.send_begin(BEGIN, deadline=attachment.ready_by + 0.1),
            "finish": lambda: attachment.finish(deadline=attachment.finish_by + 0.1),
        }
        deny(actions[where], attachment)


@pytest.mark.parametrize(
    "fault",
    ["duplicate_start", "begin_before_ready", "receive_before_start", "finish_before_start"],
)
def test_wrong_operation_order_is_consumed(fault):
    def server(peer):
        if fault == "duplicate_start":
            request(peer)
            peer.sendall(stream.UPGRADE)
        assert peer.recv(1) == b""

    with attached(server) as attachment:
        if fault == "duplicate_start":
            attachment.start(deadline=attachment.ready_by)
        action = {
            "duplicate_start": lambda: attachment.start(deadline=attachment.ready_by),
            "begin_before_ready": lambda: attachment.send_begin(
                BEGIN, deadline=attachment.ready_by
            ),
            "receive_before_start": lambda: attachment.receive(deadline=attachment.ready_by),
            "finish_before_start": lambda: attachment.finish(deadline=attachment.finish_by),
        }[fault]
        deny(action, attachment)


@pytest.mark.parametrize(
    "fault", ["before_begin", "wrong_phase", "stderr", "invalid_json", "raw_tty"]
)
def test_bad_remote_stream_is_not_operator_evidence(fault):
    def server(peer):
        request(peer)
        raw = stream.UPGRADE
        if fault == "before_begin":
            raw += stream.segment(stream.app(stream.MESSAGES[0]) + stream.app(stream.MESSAGES[1]))
        elif fault == "wrong_phase":
            raw += stream.segment(stream.app(stream.MESSAGES[2]))
        elif fault == "stderr":
            raw += stream.segment(b"PRIVATE", channel=2)
        elif fault == "invalid_json":
            raw += stream.segment(stream.w.HEADER.pack(7) + b"PRIVATE")
        else:
            raw = raw.replace(b"multiplexed-stream", b"raw-stream")
        peer.sendall(raw)

    with attached(server) as attachment:

        def consume():
            attachment.start(deadline=attachment.ready_by)
            attachment.receive(deadline=attachment.ready_by)

        deny(consume, attachment)


@pytest.mark.parametrize("phase", ["ready", "started", "completed", "exited"])
def test_stalled_response_expires_without_retry(phase):
    def server(peer):
        request(peer)
        peer.sendall(stream.UPGRADE)
        if phase != "ready":
            peer.sendall(stream.segment(stream.app(stream.MESSAGES[0])))
            assert begun(peer) == BEGIN
            for value in stream.MESSAGES[1 : stream.MESSAGES.index({"phase": phase})]:
                peer.sendall(stream.segment(stream.app(value)))
        assert peer.recv(1) == b""

    with attached(server) as attachment:
        attachment.start(deadline=attachment.ready_by)
        if phase != "ready":
            attachment.receive(deadline=attachment.ready_by)
            attachment.send_begin(BEGIN, deadline=attachment.ready_by)
            for _ in range(stream.MESSAGES.index({"phase": phase}) - 1):
                attachment.receive(deadline=attachment.finish_by)
        began = time.monotonic()
        deny(lambda: attachment.receive(deadline=began + 0.08), attachment)
        assert time.monotonic() - began < 1


@pytest.mark.parametrize("fault", ["lost_write", "interrupt_write", "duplicate_begin"])
def test_begin_delivery_loss_cannot_be_replayed(monkeypatch, fault):
    delivered = []

    def server(peer):
        request(peer)
        peer.sendall(stream.UPGRADE + stream.segment(stream.app(stream.MESSAGES[0])))
        delivered.append(begun(peer))
        assert peer.recv(1) == b""

    with attached(server) as attachment:
        attachment.start(deadline=attachment.ready_by)
        attachment.receive(deadline=attachment.ready_by)
        original = attachment._write

        def lost(raw, deadline):
            original(raw, deadline)
            if fault == "interrupt_write":
                raise KeyboardInterrupt
            raise OSError("PRIVATE lost send return")

        if fault == "duplicate_begin":
            attachment.send_begin(BEGIN, deadline=attachment.ready_by)
        else:
            monkeypatch.setattr(attachment, "_write", lost)
        if fault == "interrupt_write":
            with pytest.raises(KeyboardInterrupt):
                attachment.send_begin(BEGIN, deadline=attachment.ready_by)
            assert attachment.closed
        else:
            deny(lambda: attachment.send_begin(BEGIN, deadline=attachment.ready_by), attachment)
        assert attachment.begun
    assert delivered == [BEGIN]


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), 0, -1])
def test_bad_step_deadlines_close_without_write(value):
    with attached(lambda peer: peer.recv(1)) as attachment:
        deny(lambda: attachment.start(deadline=value), attachment)


@pytest.mark.parametrize(
    "fault",
    ["id", "bool_ready", "expired", "order", "far", "inherit", "unconnected", "tcp", "datagram"],
)
def test_constructor_refuses_and_closes_owned_socket(fault):
    channel, peer = socket.socketpair()
    try:
        now = time.monotonic()
        args = dict(ready_by=now + 3, finish_by=now + 6)
        if fault == "bool_ready":
            args["ready_by"] = True
        if fault == "expired":
            args["ready_by"] = now - 1
        if fault == "order":
            args["finish_by"] = now + 2
        if fault == "far":
            args["finish_by"] = now + 781
        if fault == "inherit":
            channel.set_inheritable(True)
        if fault in ("unconnected", "tcp", "datagram"):
            channel.close()
            channel = socket.socket(
                socket.AF_INET if fault == "tcp" else socket.AF_UNIX,
                socket.SOCK_DGRAM if fault == "datagram" else socket.SOCK_STREAM,
            )
        with pytest.raises(m.UnconfirmedAttachment):
            m.Attachment(channel, "PRIVATE" if fault == "id" else EXEC, **args)
        assert channel.fileno() == -1
    finally:
        channel.close()
        peer.close()


def test_no_default_endpoint_or_connect_action():
    import inspect

    assert str(inspect.signature(m.Attachment)) == (
        "(channel, execution_id, *, ready_by, finish_by, sender=None)"
    )
    assert not any(
        hasattr(m.Attachment, name) for name in ("connect", "create", "restart", "restore")
    )
    # Refusal cannot take ownership of an arbitrary numeric descriptor.
    read, write = os.pipe()
    try:
        with pytest.raises(m.UnconfirmedAttachment):
            m.Attachment(read, EXEC, ready_by=time.monotonic() + 3, finish_by=time.monotonic() + 6)
        os.fstat(read)
    finally:
        os.close(read)
        os.close(write)


@pytest.mark.parametrize("fault", ["no_eof", "trailing_byte", "fifth_frame"])
def test_four_complete_frames_do_not_substitute_for_clean_eof(fault):
    def server(peer):
        request(peer)
        peer.sendall(stream.UPGRADE + stream.segment(stream.app(stream.MESSAGES[0])))
        assert begun(peer) == BEGIN
        peer.sendall(stream.segment(b"".join(stream.app(value) for value in stream.MESSAGES[1:])))
        if fault != "no_eof":
            # Wait until the test has consumed all four messages before sending
            # its bad trailer, so the failure is specifically the EOF gate.
            assert exact(peer, 1) == b"X"
            peer.sendall(
                b"X" if fault == "trailing_byte" else stream.segment(stream.app({"phase": "extra"}))
            )
        else:
            assert peer.recv(1) == b""

    with attached(server) as attachment:
        attachment.start(deadline=attachment.ready_by)
        attachment.receive(deadline=attachment.ready_by)
        attachment.send_begin(BEGIN, deadline=attachment.ready_by)
        for _ in range(3):
            attachment.receive(deadline=attachment.finish_by)
        if fault != "no_eof":
            # Fixture-only marker; not part of Attachment's exposed protocol.
            attachment.channel.send(b"X")
        deny(lambda: attachment.finish(deadline=time.monotonic() + 0.08), attachment)


@pytest.mark.parametrize(
    "partial",
    [b"HTTP/1.1 101", stream.UPGRADE + b"\1\0\0", stream.UPGRADE + stream.segment(b"\0\0\0\x20{")],
)
def test_slow_partial_http_docker_or_inner_frame_is_bounded(partial):
    def server(peer):
        request(peer)
        peer.sendall(partial)
        assert peer.recv(1) == b""

    with attached(server) as attachment:

        def consume():
            deadline = time.monotonic() + 0.08
            attachment.start(deadline=deadline)
            attachment.receive(deadline=deadline)

        deny(consume, attachment)


def test_actual_socket_backpressure_has_absolute_write_deadline():
    client, peer = socket.socketpair()
    try:
        client.setblocking(False)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        # Saturate only this disposable connection. The peer never drains it.
        while True:
            try:
                client.send(b"x" * 4096)
            except BlockingIOError:
                break
        now = time.monotonic()
        attachment = m.Attachment(client, EXEC, ready_by=now + 1, finish_by=now + 2)
        deny(lambda: attachment.start(deadline=now + 0.08), attachment)
        assert time.monotonic() - now < 1
    finally:
        client.close()
        peer.close()


def test_parse_return_after_original_deadline_is_discarded(monkeypatch):
    def server(peer):
        request(peer)
        peer.sendall(stream.UPGRADE + stream.segment(stream.app(stream.MESSAGES[0])))
        assert peer.recv(1) == b""

    with attached(server) as attachment:
        original, deadline = attachment.decoder.feed, time.monotonic() + 0.1

        def late(raw):
            result = original(raw)
            time.sleep(max(0, deadline - time.monotonic()) + 0.01)
            return result

        monkeypatch.setattr(attachment.decoder, "feed", late)
        deny(lambda: attachment.start(deadline=deadline), attachment)
        assert not attachment.pending


def test_partial_http_write_failure_is_not_restarted(monkeypatch):
    delivered = []

    def server(peer):
        raw = bytearray()
        while True:
            chunk = peer.recv(4096)
            if not chunk:
                break
            raw.extend(chunk)
        delivered.append(bytes(raw))

    with attached(server) as attachment:

        def partial(raw, deadline):
            assert attachment.channel.send(raw[:11]) == 11
            raise OSError("PRIVATE lost HTTP write")

        monkeypatch.setattr(attachment, "_write", partial)
        deny(lambda: attachment.start(deadline=attachment.ready_by), attachment)
    assert delivered == [b"POST /v1.47"]
