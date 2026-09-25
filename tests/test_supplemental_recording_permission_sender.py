"""One actual local stream, child clock and original observer handles.

Qualification callbacks are explicitly synthetic read-only test policies. They
are not a finite command/source policy, installed proof, or a receiver ack.
"""

import importlib.util
import json
import select
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_permission_review as review_tests

NAME = "supplemental_recording_permission_sender"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(review_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
peer, supplied = review_tests.peer, review_tests.supplied
pytestmark = pytest.mark.parametrize("peer", ["challenge"], indirect=True)


@pytest.fixture
def offered(supplied, request):
    p, calls, objects = supplied.peer, [], []

    def qualification(review):
        # Synthetic trusted-code boundary only; no claim of source qualification.
        assert review.raw == supplied.raw
        assert review.target is p.observer and review.observer is p.target
        assert review.timer is p.timer and review.domain is p.domain
        calls.append(review)
        review.read()

    selected = getattr(request, "param", None)
    raw = supplied.raw
    if selected is not None:
        value = json.loads(raw)
        value[selected] = "PRIVATE wrong challenge field"
        raw = review_tests.encoded(value)
    p.child.stdin.write(raw)
    assert review_tests.peer_tests.line(p.child) == b"challenge\n"

    def make(**changes):
        args = dict(
            template=p.template,
            template_sha256=p.template.sha256,
            baseline_sha256=p.baseline_sha256,
            observer=p.target,
            target=p.observer,
            domain=p.domain,
            timer=p.timer,
            channel=p.channel,
            qualify=qualification,
        )
        obj = m.Sender(**(args | changes))
        objects.append(obj)
        return obj

    try:
        yield SimpleNamespace(p=p, supplied=supplied, calls=calls, make=make)
    finally:
        for obj in objects:
            obj.close()


def denied(action):
    with pytest.raises(m.UnconfirmedDelivery) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def no_reply(case):
    poller = select.poll()
    poller.register(case.p.child.stdout.fileno(), select.POLLIN | select.POLLHUP)
    assert not poller.poll(0)


def test_receive_is_read_only_then_one_explicit_qualification_and_local_write(offered):
    s, obj = offered, offered.make()
    original = s.p.timer.original
    review = obj.receive()
    assert review is obj.review and review.raw == s.supplied.raw
    assert not s.calls and not obj.send_attempted and not obj.write_attempted
    no_reply(s)
    assert obj.send() is None
    assert s.calls == [review]
    assert obj.qualification_attempted and obj.write_attempted and obj.write_complete
    reply = json.loads(review_tests.peer_tests.line(s.p.child))
    assert reply == json.loads(m.permission.permission_bytes(review.challenge_sha256))
    assert s.p.timer.original is original
    assert not hasattr(obj, "accepted")
    obj.close()
    s.p.borrowed()


@pytest.mark.parametrize("result", [True, False, "approved", {}, 1])
def test_tokens_are_not_qualification_code_contract(offered, result):
    obj = offered.make(qualify=lambda _: result)
    obj.receive()
    denied(obj.send)
    assert obj.send_attempted and obj.qualification_attempted and not obj.write_attempted
    no_reply(offered)
    offered.p.borrowed()


@pytest.mark.parametrize("bad", [None, True, "approved"])
def test_non_callable_qualification_refuses_before_receiving(offered, bad):
    denied(lambda: offered.make(qualify=bad))
    no_reply(offered)
    offered.p.borrowed()


@pytest.mark.parametrize("error", [ValueError("PRIVATE"), KeyboardInterrupt(), SystemExit(94)])
def test_failed_or_interrupted_qualification_never_sends_and_cannot_retry(offered, error):
    calls = []

    def qualify(_):
        calls.append(True)
        raise error

    obj = offered.make(qualify=qualify)
    obj.receive()
    if isinstance(error, Exception):
        denied(obj.send)
    else:
        with pytest.raises(type(error)) as caught:
            obj.send()
        assert caught.value is error
    assert calls == [True] and not obj.write_attempted and not obj.lock.locked()
    denied(obj.send)
    assert calls == [True]
    no_reply(offered)
    offered.p.borrowed()


def test_prequeued_challenge_allowed_only_at_sender_not_permission_receiver(offered):
    # The listener may finish target binding after the target has sent its
    # challenge. Receiver policy still forbids preemptive permission bytes.
    obj = offered.make()
    assert obj.receive().raw == offered.supplied.raw
    no_reply(offered)
    assert not offered.calls


@pytest.mark.parametrize(
    "offered", ["case", "baseline_sha256", "domain_sha256", "target"], indirect=True
)
def test_received_frame_must_pass_independent_review_before_any_response(offered):
    obj = offered.make()
    denied(obj.receive)
    denied(obj.send)
    assert not offered.calls and not obj.write_attempted
    no_reply(offered)
    offered.p.borrowed()


def test_receive_timeout_does_not_rearm_or_send_permission(offered, monkeypatch):
    monkeypatch.setattr(m.permission, "WAIT_SECONDS", 0.7)
    monkeypatch.setattr(m.permission, "IO_SECONDS", 0.15)
    obj = offered.make()
    original = obj.deadline

    def blocked(*_args):
        raise BlockingIOError()

    monkeypatch.setattr(socket.socket, "recv", blocked)
    denied(obj.receive)
    assert obj.failed and obj.receive_attempted and not obj.received
    assert obj.deadline == original and not obj.write_attempted
    no_reply(offered)
    offered.p.borrowed()


def test_extra_queued_frame_is_not_treated_as_another_attempt(offered, monkeypatch):
    obj = offered.make()
    real = socket.socket.recv

    def duplicate(channel, length, flags=0):
        raw = real(channel, length, flags)
        return raw if flags & socket.MSG_PEEK else raw + raw

    monkeypatch.setattr(socket.socket, "recv", duplicate)
    denied(obj.receive)
    assert not offered.calls and not obj.write_attempted
    no_reply(offered)
    offered.p.borrowed()


def test_send_before_receive_poisons_without_any_qualification_or_write(offered):
    obj = offered.make()
    denied(obj.send)
    denied(obj.receive)
    assert not offered.calls and not obj.write_attempted
    no_reply(offered)


@pytest.mark.parametrize("repeat", ["receive", "send"])
def test_logical_receive_and_send_are_each_one_attempt(offered, repeat):
    obj = offered.make()
    obj.receive()
    if repeat == "receive":
        denied(obj.receive)
        denied(obj.send)
        assert not offered.calls and not obj.write_attempted
        no_reply(offered)
    else:
        obj.send()
        assert json.loads(review_tests.peer_tests.line(offered.p.child))["schema"] == 1
        denied(obj.send)
        assert len(offered.calls) == 1 and obj.write_complete
        no_reply(offered)
    offered.p.borrowed()


@pytest.mark.parametrize("fault", ["closed", "review", "qualifier", "expired"])
def test_lost_binding_after_qualification_never_reaches_first_write(offered, monkeypatch, fault):
    obj = None

    def qualify(review):
        if fault == "closed":
            obj.close()
        elif fault == "review":
            review.close()
        elif fault == "qualifier":
            obj.qualify = lambda _: None
        else:
            monkeypatch.setattr(m.time, "monotonic", lambda: obj.end)

    obj = offered.make(qualify=qualify)
    obj.receive()
    denied(obj.send)
    assert not obj.write_attempted
    no_reply(offered)
    offered.p.borrowed()


def test_partial_nonblocking_writes_finish_one_frame_without_requalifying(offered, monkeypatch):
    obj = offered.make()
    obj.receive()
    real, chunks = socket.socket.send, []

    def short(channel, raw, flags=0):
        chunks.append(bytes(raw))
        if len(chunks) == 1:
            raise BlockingIOError()
        return real(channel, raw[:9], flags)

    monkeypatch.setattr(socket.socket, "send", short)
    obj.send()
    assert len(chunks) > 2 and len(offered.calls) == 1 and obj.write_complete
    assert (
        json.loads(review_tests.peer_tests.line(offered.p.child))["challenge_sha256"] == obj.digest
    )
    no_reply(offered)


def test_lost_return_after_full_write_preserves_dispatch_not_remote_ack(offered, monkeypatch):
    obj = offered.make()
    obj.receive()
    real = socket.socket.send

    def lose(channel, raw, flags=0):
        count = real(channel, raw, flags)
        obj.review.close()
        return count

    monkeypatch.setattr(socket.socket, "send", lose)
    denied(obj.send)
    # These bytes did reach the kernel; do not erase that fact or send again.
    assert obj.write_attempted and obj.write_complete and obj.failed
    assert (
        json.loads(review_tests.peer_tests.line(offered.p.child))["challenge_sha256"] == obj.digest
    )
    denied(obj.send)
    assert len(offered.calls) == 1
    no_reply(offered)
    offered.p.borrowed()
