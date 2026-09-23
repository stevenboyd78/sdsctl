"""Explicit Engine sender profile against owned inherited Unix listeners.

Actual SCM credentials, pidfds and framing; synthetic Engine data only. No
installed helper, root-account, Docker, scanner or recording qualification.
"""

import os
import time
from contextlib import contextmanager

import pytest

from . import test_supplemental_recording_engine as engines
from . import test_supplemental_recording_engine_sender as senders

m = engines.m
attached = engines.attached
stream = attached.stream


@contextmanager
def endpoint(tmp_path, monkeypatch, **kwargs):
    assert m.senders is senders.m and attached.m.senders is senders.m
    with senders.server(tmp_path, monkeypatch, **kwargs) as fixture:
        monkeypatch.setattr(m, "SOCKET", fixture.path)
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
        monkeypatch.setattr(m, "ROOT_GID", os.getegid())
        value = m.Endpoint(sender_credentials=True)
        try:
            yield value, fixture
        finally:
            value.close()


@pytest.mark.parametrize("changed", [False, True])
def test_json_reply_checks_actual_writer_not_listener_creator(tmp_path, monkeypatch, changed):
    request = (
        b"GET /v1.47/fixture HTTP/1.1\r\nHost: localhost\r\n"
        b"Connection: close\r\nContent-Type: application/json\r\nContent-Length: 0\r\n\r\n"
    )
    with endpoint(
        tmp_path,
        monkeypatch,
        request=request,
        payload=engines.reply({"fixture": True}),
        mode="changed" if changed else "normal",
    ) as (value, fixture):

        def read():
            return m._json_request(
                value, "GET", "/fixture", None, 200, deadline=time.monotonic() + 1
            )

        if changed:
            with pytest.raises(m.UnconfirmedEngine, match=m.MESSAGE):
                read()
            assert value.closed and value.sender.closed and fixture.child.poll() is None
        else:
            assert read() == {"fixture": True}
            assert value.sender.creator[0] == os.getpid()
            assert value.sender.peer[0] == fixture.child.pid
            assert value.peer is None and value.peer_fd == -1
            value.check()


@pytest.mark.parametrize("probe", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_both_attachment_types_keep_kernel_sender_checks(tmp_path, monkeypatch, probe, changed):
    body = b'{"Detach":false,"Tty":false}'
    request = (
        f"POST /v1.47/exec/{attached.EXEC}/start HTTP/1.1\r\n"
        "Host: localhost\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    response = {"kind": "finite-recording-cached-probe-result"} if probe else {"phase": "ready"}
    payload = stream.segment(stream.app(response))
    if not probe:
        payload = stream.UPGRADE + payload
    with endpoint(
        tmp_path,
        monkeypatch,
        request=request,
        payload=payload,
        probe=probe,
        upgrade=stream.UPGRADE,
        mode="changed" if changed else "normal",
    ) as (value, fixture):
        now = time.monotonic()
        sock = value.connect(deadline=now + 1)
        sender = value.sender
        channel = (
            attached.m.ProbeAttachment(sock, attached.EXEC, probe_by=now + 3, sender=sender)
            if probe
            else attached.m.Attachment(
                sock, attached.EXEC, ready_by=now + 3, finish_by=now + 4, sender=sender
            )
        )
        try:

            def read():
                channel.start(deadline=now + 3)
                if probe:
                    channel.send_request(
                        {"kind": "finite-recording-cached-probe"}, deadline=now + 3
                    )
                return channel.receive(deadline=now + 3)

            if changed:
                with pytest.raises(attached.m.UnconfirmedAttachment, match=attached.m.MESSAGE):
                    read()
                assert channel.closed and sender.closed and fixture.child.poll() is None
                with pytest.raises(senders.m.UnconfirmedSender):
                    value.check()
            else:
                assert read() == response
                if probe:
                    channel.finish(deadline=now + 3)
                    assert channel.finished and channel.closed
                channel.close()
                # Borrowed original witness survives attachment/EOF cleanup.
                assert not sender.closed and sender.peer[0] == fixture.child.pid
                sender.check()
                value.check()
        finally:
            channel.close()


@pytest.mark.parametrize("invalid", [1, "true", None, object()])
def test_sender_profile_must_be_explicit_exact_boolean(monkeypatch, invalid):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid profile must not reach socket filesystem checks")

    monkeypatch.setattr(m.os, "open", forbidden)
    with pytest.raises(m.UnconfirmedEngine, match=m.MESSAGE):
        m.Endpoint(sender_credentials=invalid)


@pytest.mark.parametrize("probe", [False, True])
@pytest.mark.parametrize("fault", ["unlearned", "arbitrary", "disabled"])
def test_attachment_cannot_replace_original_sender_with_unqualified_object(
    tmp_path, monkeypatch, probe, fault
):
    with senders.server(tmp_path, monkeypatch) as fixture:
        sock = fixture.connect()
        sender = fixture.sender
        if fault == "arbitrary":
            sender = object()
        elif fault == "disabled":
            sender.ping(sock, deadline=time.monotonic() + 1)
            sock.setsockopt(senders.socket.SOL_SOCKET, senders.socket.SO_PASSCRED, 0)
        now = time.monotonic()
        with pytest.raises(attached.m.UnconfirmedAttachment, match=attached.m.MESSAGE):
            if probe:
                attached.m.ProbeAttachment(sock, attached.EXEC, probe_by=now + 3, sender=sender)
            else:
                attached.m.Attachment(
                    sock, attached.EXEC, ready_by=now + 3, finish_by=now + 4, sender=sender
                )
        assert sock.fileno() == -1 and not fixture.sender.closed
