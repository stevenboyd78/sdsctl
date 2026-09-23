"""Real isolated operator via a synthetic Engine byte relay, no Docker daemon.

This checks transport interoperability with the real native path, not installed
image/exec authentication. The existing operator fixture verifies actual peer
identities, cached IPC, PCM samples, preserved files and separate native exits.
"""

import os
import select
import socket
import time
from threading import Thread

from . import test_supplemental_recording_attachment as transport
from . import test_supplemental_recording_operator as operator

tree, configured, cached, prepared, staged = (
    operator.tree,
    operator.configured,
    operator.cached,
    operator.prepared,
    operator.staged,
)


def test_actual_isolated_operator_through_synthetic_engine(staged, prepared, monkeypatch):
    created, sessions, errors = [], [], []
    original_start = operator.start

    def start(*args, **kwargs):
        process = original_start(*args, **kwargs)
        created.append(process)
        return process

    def wrap(incoming, outgoing, *, role):
        assert role == "host" and len(created) == 1
        process = created[0]
        assert (incoming, outgoing) == (process.stdout.fileno(), process.stdin.fileno())
        ready_by = float(process.args[process.args.index("--ready-by") + 1])
        client, peer = socket.socketpair()
        peer.settimeout(5)
        read, write = os.dup(incoming), os.dup(outgoing)
        os.set_blocking(write, False)

        def exact(size):
            value = bytearray()
            until = time.monotonic() + 5
            while len(value) < size:
                assert select.select([read], [], [], max(0, until - time.monotonic()))[0]
                chunk = os.read(read, size - len(value))
                assert chunk
                value.extend(chunk)
            return bytes(value)

        def forward():
            header = exact(4)
            size = transport.stream.w.HEADER.unpack(header)[0]
            assert 0 < size <= transport.stream.w.MAX_BYTES
            raw = header + exact(size)
            # Deliberately split inner headers and JSON across Docker segments.
            for index in range(0, len(raw), 17):
                peer.sendall(transport.stream.segment(raw[index : index + 17]))

        def serve():
            try:
                transport.request(peer)
                peer.sendall(transport.stream.UPGRADE)
                forward()
                # Raw upgraded stdin; no Docker output wrapper on this leg.
                header = transport.exact(peer, 4)
                size = transport.stream.w.HEADER.unpack(header)[0]
                assert 0 < size <= transport.stream.w.MAX_BYTES
                raw = header + transport.exact(peer, size)
                offset, until = 0, time.monotonic() + 2
                while offset < len(raw):
                    assert select.select([], [write], [], max(0, until - time.monotonic()))[1]
                    offset += os.write(write, raw[offset:])
                for _ in range(3):
                    forward()
                assert select.select([read], [], [], 5)[0] and os.read(read, 1) == b""
            except BaseException as error:
                errors.append(error)
            finally:
                os.close(read)
                os.close(write)
                peer.close()

        thread = Thread(target=serve)
        thread.start()
        attachment = transport.m.Attachment(
            client, transport.EXEC, ready_by=ready_by, finish_by=ready_by + 30
        )
        sessions.append((attachment, thread))
        attachment.start(deadline=ready_by)

        class ExistingFixtureInterface:
            # Only adapt the existing driver's method names/deadlines. The
            # attachment still enforces the exact original operator ready bound.
            def receive(self, *, deadline):
                bound = ready_by if attachment.reads == 0 else attachment.finish_by
                return attachment.receive(deadline=min(deadline, bound))

            def send(self, value, *, deadline):
                attachment.send_begin(value, deadline=min(deadline, ready_by))

            def close(self):
                try:
                    if attachment.reads == 4 and not attachment.closed:
                        attachment.finish(deadline=min(time.monotonic() + 5, attachment.finish_by))
                finally:
                    attachment.close()
                    thread.join(6)

        return ExistingFixtureInterface()

    monkeypatch.setattr(operator, "start", start)
    monkeypatch.setattr(operator.w, "Stream", wrap)
    try:
        operator.test_fixed_operator_actual_session(staged, prepared, monkeypatch, "record")
        assert len(sessions) == len(created) == 1 and created[0].returncode == 0
        attachment, thread = sessions[0]
        assert attachment.finished and attachment.closed and not thread.is_alive()
        assert not errors, errors
    finally:
        for attachment, thread in sessions:
            attachment.close()
            thread.join(6)
        assert all(not thread.is_alive() for _, thread in sessions)
