"""Synthetic two-pipe framing faults; no Docker/native provenance is claimed."""

import importlib.util
import os
import select
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread

import pytest

NAME = "supplemental_recording_wire"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@contextmanager
def pair():
    incoming, writer = os.pipe()
    reader, outgoing = os.pipe()
    try:
        yield incoming, outgoing, reader, writer
    finally:
        for fd in (incoming, outgoing, reader, writer):
            os.close(fd)


def until():
    return time.monotonic() + 2


def denied(call):
    with pytest.raises(m.UnconfirmedStream) as caught:
        call()
    assert str(caught.value) == m.MESSAGE


def packet(raw):
    return m.HEADER.pack(len(raw)) + raw


def test_one_begin_four_responses_and_original_fd_flags():
    with pair() as (incoming, outgoing, reader, writer):
        server = m.Stream(incoming, outgoing, role="operator")
        client = m.Stream(reader, writer, role="host")
        try:
            client.send({"phase": "begin", "tag": "escaped \u2603"}, deadline=until())
            assert server.receive(deadline=until()) == {"phase": "begin", "tag": "escaped \u2603"}
            for phase in ("ready", "started", "completed", "exited"):
                server.send({"phase": phase}, deadline=until())
                assert client.receive(deadline=until()) == {"phase": phase}
            assert (server.reads, server.writes, client.reads, client.writes) == (1, 4, 4, 1)
            assert all(not os.get_inheritable(fd) for fd in server.fds + client.fds)
            denied(lambda: client.send({"phase": "begin"}, deadline=until()))
            denied(lambda: server.receive(deadline=until()))
            assert client.poisoned and server.poisoned
        finally:
            client.close()
            server.close()
        assert all(os.get_blocking(fd) for fd in (incoming, outgoing, reader, writer))


@pytest.mark.parametrize("fragment", [1, 3, 4096])
def test_fragmented_headers_and_maximum_frame(fragment):
    # Construct exactly MAX_BYTES of canonical JSON, with a real producer that
    # cannot block this test on a pipe smaller than the frame.
    raw = m.encode({"value": "x" * (m.MAX_BYTES - len(b'{"value":""}'))})
    assert len(raw) == m.MAX_BYTES
    with pair() as (incoming, outgoing, _reader, writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        done = Event()
        os.set_blocking(writer, False)

        def produce():
            data = packet(raw)
            offset = 0
            while offset < len(data) and not done.is_set():
                if not select.select([], [writer], [], 0.01)[1]:
                    continue
                try:
                    offset += os.write(writer, data[offset : offset + fragment])
                except BlockingIOError:
                    continue

        thread = Thread(target=produce)
        thread.start()
        try:
            assert stream.receive(deadline=until()) == {"value": "x" * (m.MAX_BYTES - 12)}
        finally:
            done.set()
            stream.close()
            thread.join(2)
            assert not thread.is_alive()


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"null",
        b"true",
        b"0",
        b"{",
        b'{"a":1,"a":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x": 1}',
        b'{"b":1,"a":2}',
        b'{"x":1}\n',
        b'{"x":1}{}',
        b'{"x":1e0}',
        b'{"x":"\xff"}',
    ],
)
def test_noncanonical_or_malformed_payload_poison_is_not_retryable(raw):
    with pair() as (incoming, outgoing, _reader, writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        try:
            os.write(writer, packet(raw))
            denied(lambda: stream.receive(deadline=until()))
            os.write(writer, packet(b"{}"))
            denied(lambda: stream.receive(deadline=until()))
            denied(lambda: stream.send({}, deadline=until()))
            assert stream.poisoned
        finally:
            stream.close()


@pytest.mark.parametrize("size", [0, m.MAX_BYTES + 1, 2**32 - 1])
def test_length_refused_before_payload_read(size):
    with pair() as (incoming, outgoing, _reader, writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        try:
            os.write(writer, m.HEADER.pack(size))
            denied(lambda: stream.receive(deadline=until()))
        finally:
            stream.close()


@pytest.mark.parametrize("data", [b"", b"\0", b"\0\0\0", m.HEADER.pack(4), m.HEADER.pack(4) + b"{"])
def test_eof_never_returns_partial_frame(data):
    incoming, writer = os.pipe()
    reader, outgoing = os.pipe()
    stream = m.Stream(incoming, outgoing, role="operator")
    try:
        os.write(writer, data)
        os.close(writer)
        denied(lambda: stream.receive(deadline=until()))
    finally:
        stream.close()
        for fd in (incoming, outgoing, reader):
            os.close(fd)


@pytest.mark.parametrize("deadline", [0, -1, True, None, float("nan"), float("inf"), "PRIVATE"])
def test_bad_deadlines_poison_stream(deadline):
    with pair() as (incoming, outgoing, _reader, _writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        try:
            denied(lambda: stream.receive(deadline=deadline))
            assert stream.poisoned
        finally:
            stream.close()


@pytest.mark.parametrize("fault", ["future", "timeout", "closed", "owner"])
def test_deadline_lifecycle_and_owner_checks(fault):
    with pair() as (incoming, outgoing, _reader, _writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        owner = stream.owner
        deadline = until()
        if fault == "future":
            deadline = time.monotonic() + m.MAX_SECONDS + 1
        if fault == "timeout":
            deadline = time.monotonic() + 0.01
        if fault == "closed":
            stream.close()
        if fault == "owner":
            stream.owner = (0, 0)
        try:
            denied(lambda: stream.receive(deadline=deadline))
        finally:
            stream.owner = owner
            stream.close()


@pytest.mark.parametrize(
    "value", [None, [], {"secret": float("nan")}, {1: "value"}, {"x": ()}, {"x": "x" * m.MAX_BYTES}]
)
def test_encoding_refusals_are_fixed_and_poison_send(value):
    with pair() as (incoming, outgoing, _reader, _writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        try:
            denied(lambda: stream.send(value, deadline=until()))
            denied(lambda: stream.send({}, deadline=until()))
        finally:
            stream.close()


@pytest.mark.parametrize(
    "fault",
    ["same", "wrong_input", "wrong_output", "duplicate", "role", "bool", "file", "datagram"],
)
def test_constructor_refusals_restore_original_flags_and_fds(tmp_path, fault):
    with pair() as (incoming, outgoing, reader, writer):
        before = len(os.listdir("/proc/self/fd"))
        i, o, role = incoming, outgoing, "operator"
        extra = None
        if fault == "same":
            o = i
        if fault == "wrong_input":
            i = writer
        if fault == "wrong_output":
            o = reader
        if fault == "duplicate":
            extra = o = os.dup(i)
        if fault == "role":
            role = "PRIVATE"
        if fault == "bool":
            i = True
        if fault == "file":
            extra = i = os.open(tmp_path / "file", os.O_CREAT | os.O_RDWR, 0o600)
        if fault == "datagram":
            extra = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            i = extra.fileno()
        try:
            denied(lambda: m.Stream(i, o, role=role))
        finally:
            if type(extra) is int:
                os.close(extra)
            elif extra is not None:
                extra.close()
        assert len(os.listdir("/proc/self/fd")) == before
        assert all(os.get_blocking(fd) for fd in (incoming, outgoing, reader, writer))


@pytest.mark.parametrize("role,limit", [("host", 1), ("operator", 4)])
def test_send_budget_cannot_be_extended(role, limit):
    with pair() as (incoming, outgoing, reader, _writer):
        stream = m.Stream(incoming, outgoing, role=role)
        try:
            for _ in range(limit):
                stream.send({}, deadline=until())
                assert os.read(reader, 6) == packet(b"{}")
            denied(lambda: stream.send({}, deadline=until()))
            assert not select.select([reader], [], [], 0)[0]
        finally:
            stream.close()


@pytest.mark.parametrize("direction", ["read", "write"])
def test_short_and_temporarily_unavailable_io(direction, monkeypatch):
    with pair() as (incoming, outgoing, reader, writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        actual, calls = getattr(m.os, direction), []

        def limited(fd, value):
            if not calls:
                calls.append(True)
                raise BlockingIOError()
            calls.append(True)
            return actual(fd, min(value, 1) if direction == "read" else value[:1])

        try:
            monkeypatch.setattr(m.os, direction, limited)
            if direction == "read":
                os.write(writer, packet(b"{}"))
                assert stream.receive(deadline=until()) == {}
            else:
                stream.send({}, deadline=until())
                assert os.read(reader, 6) == packet(b"{}")
            assert len(calls) == 7
        finally:
            stream.close()


@pytest.mark.parametrize("fault", ["zero", "late", "lost_return"])
def test_ambiguous_send_poisoned_even_if_peer_received_bytes(monkeypatch, fault):
    with pair() as (incoming, outgoing, reader, _writer):
        stream = m.Stream(incoming, outgoing, role="operator")
        actual = os.write

        def write(fd, value):
            if fault == "zero":
                return 0
            count = actual(fd, value)
            if fault == "late":
                time.sleep(0.03)
            else:
                raise OSError("PRIVATE_LOST_RETURN")
            return count

        try:
            monkeypatch.setattr(m.os, "write", write)
            denied(lambda: stream.send({}, deadline=time.monotonic() + 0.02))
            assert stream.poisoned
            if fault != "zero":
                assert os.read(reader, 6) == packet(b"{}")
            denied(lambda: stream.send({}, deadline=until()))
        finally:
            stream.close()


def test_eof_when_sending_cannot_be_retried():
    incoming, writer = os.pipe()
    reader, outgoing = os.pipe()
    stream = m.Stream(incoming, outgoing, role="operator")
    os.close(reader)
    try:
        denied(lambda: stream.send({}, deadline=until()))
        denied(lambda: stream.send({}, deadline=until()))
    finally:
        stream.close()
        for fd in (incoming, outgoing, writer):
            os.close(fd)
