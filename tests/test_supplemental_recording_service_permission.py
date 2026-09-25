"""Real local peer credentials, pidfds, clocks and zero-offset domain handles.

Only Docker cgroups are synthetic. Peers are private owned child processes;
no App, scanner, host baseline, recording or service is selected. The child
echo protocol deliberately does not purport to qualify either peer's source.
"""

import importlib.util
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_service_template as template_tests
from . import test_supplemental_recording_time_domain as domain_tests  # noqa: F401

NAME = "supplemental_recording_service_permission"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(template_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

PEER = r"""
import hashlib, json, os, socket, sys, time, uuid
path, mode = sys.argv[1:]
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
    server.bind(path)
    server.listen(1)
    server.settimeout(3)
    print("ready", flush=True)
    with server.accept()[0] as channel:
        channel.settimeout(3)
        if mode == "preemptive":
            channel.sendall(b"PRIVATE preemptive response\n")
        print("connected", flush=True)
        if mode == "clock":
            # Actual child-domain sample for the read-only observer review.
            with open("/proc/sys/kernel/random/boot_id") as source:
                boot = uuid.UUID(source.read().strip()).hex
            info = os.stat("/proc/self/ns/time")
            before = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            boottime = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            after = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            print(json.dumps(dict(boot=boot, namespace=[info.st_dev, info.st_ino],
                before_ns=before, boottime_ns=boottime, after_ns=after)), flush=True)
            sys.stdin.buffer.read()
            sys.exit(0)
        raw = bytearray()
        while b"\n" not in raw:
            try:
                chunk = channel.recv(2048)
            except ConnectionResetError:
                # Closing a receiver with unread unsolicited data resets the
                # private test socket; it is not a peer protocol success.
                sys.exit(0)
            if not chunk:
                sys.exit(0)
            raw.extend(chunk)
        print(bytes(raw).decode().strip(), flush=True)
        digest = hashlib.sha256(bytes(raw[:-1])).hexdigest()
        reply = json.dumps(dict(schema=1,
            kind="finite-recording-preflight-permission-v1",
            challenge_sha256=digest), sort_keys=True,
            separators=(",", ":")).encode() + b"\n"
        if mode == "wrong":
            reply = reply.replace(digest.encode(), b"0" * 64)
        elif mode == "malformed":
            reply = b"PRIVATE invalid data\n"
        elif mode == "extra":
            reply += b"PRIVATE extra\n"
        elif mode == "duplicate":
            reply += reply
        elif mode == "oversize":
            reply = b"X" * 2049
        elif mode == "partial":
            reply = reply[:-1]
        if mode == "eof":
            channel.shutdown(socket.SHUT_WR)
        elif mode != "silent":
            channel.sendall(reply)
        print("sent", flush=True)
        if mode == "late-extra":
            if sys.stdin.buffer.readline() == b"extra\n":
                channel.sendall(b"PRIVATE late extra\n")
                print("extra", flush=True)
        sys.stdin.buffer.read()
"""


def line(child):
    poller = select.poll()
    poller.register(child.stdout.fileno(), select.POLLIN | select.POLLHUP)
    assert poller.poll(3000), "Owned peer did not reach its test barrier"
    return child.stdout.readline()


def denied(action):
    with pytest.raises(m.UnconfirmedPermission) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@pytest.fixture
def peer(request, monkeypatch):
    mode = getattr(request, "param", "correct")
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.domains, "ROOT_UID", os.geteuid())

    def identity(pid, cid):
        # Preserve real PID/start ticks/liveness. Only the Docker scope is fake.
        return m.domains.process.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m.domains.process, "read_identity", identity)
    # UNIX pathname limit is only 108 bytes; pytest's generated root can exceed it.
    with tempfile.TemporaryDirectory(prefix="sds-permission-") as directory:
        address = str(Path(directory) / "peer.sock")
        child = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", PEER, address, mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        witness = timer = domain = None
        objects = []
        try:
            assert line(child) == b"ready\n"
            channel.connect(address)
            channel.setblocking(False)
            assert line(child) == b"connected\n"
            witness = m.domains.process.ProcessWitness(identity(child.pid, "a" * 64))
            target = identity(os.getpid(), "b" * 64)
            timer = m.clock.ClockWitness(m.clock.read())
            domain = m.domains.ZeroDomain(timer.original, witness)
            value = template_tests.value()
            value["plan"]["boot"] = timer.original.boot
            template = m.templates.decode(value)
            args = dict(
                template=template,
                template_sha256=template.sha256,
                baseline_sha256="c" * 64,
                target=target,
                observer=witness,
                domain=domain,
                timer=timer,
                channel=channel,
            )

            def make(**changes):
                result = m.Permission(**(args | changes))
                objects.append(result)
                return result

            def borrowed():
                assert not timer.closed and not domain.closed and not witness.exited()
                for fd in (timer.fd, domain.pidfd, witness.fd, channel.fileno()):
                    os.fstat(fd)

            yield SimpleNamespace(
                **args,
                child=child,
                make=make,
                borrowed=borrowed,
                objects=objects,
            )
        finally:
            for obj in objects:
                obj.close()
            if domain:
                domain.close()
            if timer:
                timer.close()
            if witness:
                witness.close()
            channel.close()
            child.stdin.close()
            child.wait(timeout=4)
            error = child.stderr.read()
            child.stdout.close()
            child.stderr.close()
            assert child.returncode == 0, error.decode(errors="replace")


def test_exact_real_peer_single_permission_and_scope_leave_all_handles_borrowed(peer):
    obj = peer.make()
    origin, deadline = peer.timer.original, obj.deadline
    assert not obj.approved and not obj.requested
    assert obj.wait() is None
    challenge = json.loads(line(peer.child))
    assert line(peer.child) == b"sent\n"
    assert challenge == dict(
        schema=1,
        kind=m.CHALLENGE_KIND,
        case=json.loads(peer.template.raw)["plan"]["case"],
        template_sha256=peer.template.sha256,
        baseline_sha256=peer.baseline_sha256,
        target=asdict(peer.target),
        observer=asdict(peer.observer.identity),
        original_clock=asdict(peer.timer.original)
        | {"namespace": list(peer.timer.original.namespace)},
        domain_sha256=peer.domain.evidence.sha256,
        wait_by=obj.wait_by,
        deadline=obj.deadline,
        nonce=challenge["nonce"],
    )
    assert len(challenge["nonce"]) == 64
    assert m.plans.base.checksum(challenge) == obj.challenge_sha256
    assert obj.approved and not obj.used
    with obj.consume() as guard:
        assert obj.active and obj.used and guard() is None
        assert obj.consume_end <= deadline
        peer.borrowed()
    assert not obj.active and not obj.failed
    assert peer.timer.original is origin and obj.deadline == deadline
    obj.close()
    obj.close()
    peer.borrowed()


@pytest.mark.parametrize(
    "peer", ["wrong", "malformed", "extra", "duplicate", "oversize", "eof"], indirect=True
)
def test_refused_reply_is_sticky_and_never_closes_borrowed_handles(peer):
    obj = peer.make()
    denied(obj.wait)
    assert obj.requested and obj.failed and not obj.approved
    denied(obj.wait)
    denied(lambda: obj.consume().__enter__())
    peer.borrowed()


@pytest.mark.parametrize("peer", ["partial", "silent"], indirect=True)
def test_lost_or_incomplete_reply_cannot_renew_original_cutoff(peer, monkeypatch):
    # A short fixture window exercises real nonblocking timeout without a 13s wait.
    monkeypatch.setattr(m, "WAIT_SECONDS", 0.7)
    monkeypatch.setattr(m, "IO_SECONDS", 0.15)
    obj = peer.make()
    original = obj.deadline
    denied(obj.wait)
    assert obj.failed and not obj.approved and obj.deadline == original
    peer.borrowed()


@pytest.mark.parametrize("peer", ["preemptive"], indirect=True)
def test_unsolicited_reply_is_not_a_challenge_response(peer):
    denied(peer.make)
    peer.borrowed()


@pytest.mark.parametrize("field", ["template", "target", "observer", "domain", "timer", "channel"])
def test_serialized_or_missing_original_object_refused_before_send(peer, field):
    denied(lambda: peer.make(**{field: None}))
    peer.borrowed()


@pytest.mark.parametrize("field", ["template_sha256", "baseline_sha256"])
@pytest.mark.parametrize("value", [None, True, b"a" * 64, "a" * 63, "PRIVATE"])
def test_input_pins_are_closed_exact_digests(peer, field, value):
    denied(lambda: peer.make(**{field: value}))
    peer.borrowed()


def test_template_pin_and_boot_are_independently_matched(peer):
    denied(lambda: peer.make(template_sha256="f" * 64))
    supplied = json.loads(peer.template.raw)
    supplied["plan"]["boot"] = "f" * 32
    wrong = m.templates.decode(supplied)
    denied(lambda: peer.make(template=wrong, template_sha256=wrong.sha256))
    peer.borrowed()


def test_receiver_must_be_exact_current_process_and_original_peer_not_self(peer):
    denied(lambda: peer.make(target=peer.observer.identity))
    denied(lambda: peer.make(target=replace(peer.target, start_ticks=peer.target.start_ticks + 1)))
    original = peer.observer.identity
    peer.observer.identity = peer.target
    denied(peer.make)
    peer.observer.identity = original
    peer.borrowed()


def test_same_uid_socketpair_does_not_authenticate_different_original_peer(peer):
    left, right = socket.socketpair()
    try:
        left.setblocking(False)
        denied(lambda: peer.make(channel=left))
    finally:
        left.close()
        right.close()
    peer.borrowed()


@pytest.mark.parametrize("fault", ["blocking", "inheritable", "not_root"])
def test_endpoint_and_owner_constraints_are_not_optional(peer, monkeypatch, fault):
    if fault == "blocking":
        peer.channel.setblocking(True)
    elif fault == "inheritable":
        peer.channel.set_inheritable(True)
    else:
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid() + 1)
    denied(peer.make)
    peer.borrowed()


@pytest.mark.parametrize(
    "family, kind",
    [
        (socket.AF_UNIX, socket.SOCK_STREAM),
        (socket.AF_UNIX, socket.SOCK_DGRAM),
        (socket.AF_INET, socket.SOCK_STREAM),
    ],
)
def test_unconnected_or_wrong_family_type_cannot_become_permission(peer, family, kind):
    with socket.socket(family, kind) as channel:
        channel.setblocking(False)
        denied(lambda: peer.make(channel=channel))
    peer.borrowed()


@pytest.mark.parametrize("field", ["template", "target", "observer", "domain", "timer", "channel"])
def test_original_object_substitution_after_construction_refuses(peer, field):
    obj = peer.make()
    original = getattr(obj, field)
    setattr(obj, field, None)
    denied(obj.wait)
    setattr(obj, field, original)
    assert obj.failed and not obj.approved
    peer.borrowed()


def test_mutation_of_frozen_target_does_not_change_original_identity(peer):
    obj = peer.make()
    before = peer.target.start_ticks
    object.__setattr__(peer.target, "start_ticks", before + 1)
    denied(obj.wait)
    object.__setattr__(peer.target, "start_ticks", before)
    peer.borrowed()


@pytest.mark.parametrize("field", ["deadline", "wait_by", "baseline_sha256", "fd", "peer_fd"])
def test_rebound_deadline_input_or_descriptor_refuses(peer, field):
    obj = peer.make()
    setattr(obj, field, "f" * 64 if field == "baseline_sha256" else getattr(obj, field) + 1)
    denied(obj.wait)
    peer.borrowed()


@pytest.mark.parametrize(
    "target, field",
    [("timer", "closed"), ("timer", "failed"), ("domain", "closed"), ("domain", "failed")],
)
def test_retirement_during_final_clock_read_cannot_return_approved(
    peer, monkeypatch, target, field
):
    obj = peer.make()
    real = peer.timer.read

    def retire():
        result = real()
        setattr(getattr(peer, target), field, True)
        return result

    monkeypatch.setattr(peer.timer, "read", retire)
    denied(obj.wait)
    assert obj.failed and not obj.approved
    # Restore only synthetic flags to let the fixture close its actual handles.
    setattr(getattr(peer, target), field, False)
    peer.borrowed()


def test_wait_and_scope_can_each_be_consumed_only_once(peer):
    obj = peer.make()
    obj.wait()
    with obj.consume():
        pass
    denied(lambda: obj.consume().__enter__())
    denied(obj.wait)
    assert obj.used and obj.failed
    peer.borrowed()


def test_scope_cannot_begin_before_permission(peer):
    obj = peer.make()
    denied(lambda: obj.consume().__enter__())
    assert not obj.requested and not obj.used and obj.failed
    denied(obj.wait)
    peer.borrowed()


def test_second_wait_poisoned_even_after_valid_first_reply(peer):
    obj = peer.make()
    obj.wait()
    denied(obj.wait)
    denied(lambda: obj.consume().__enter__())
    peer.borrowed()


def test_guard_is_not_authority_outside_active_scope(peer):
    obj = peer.make()
    obj.wait()
    denied(obj.guard)
    peer.borrowed()


def test_nested_scope_cannot_release_outer_lock_or_hide_refusal(peer):
    obj = peer.make()
    obj.wait()
    with pytest.raises(m.UnconfirmedPermission), obj.consume():
        denied(lambda: obj.consume().__enter__())
        assert obj.lock.locked() and obj.failed
    assert not obj.lock.locked() and not obj.active
    peer.borrowed()


@pytest.mark.parametrize("error", [ValueError("PRIVATE"), KeyboardInterrupt(), SystemExit(91)])
def test_scope_failure_and_interrupt_keep_original_handles_and_prevent_retry(peer, error):
    obj = peer.make()
    obj.wait()
    expected = m.UnconfirmedPermission if isinstance(error, Exception) else type(error)
    with pytest.raises(expected) as caught, obj.consume():
        raise error
    if not isinstance(error, Exception):
        assert caught.value is error
    else:
        assert str(caught.value) == m.MESSAGE
    assert obj.failed and obj.used and not obj.lock.locked()
    peer.borrowed()


def test_expiry_after_scope_body_cannot_return_success(peer, monkeypatch):
    obj = peer.make()
    obj.wait()
    with pytest.raises(m.UnconfirmedPermission), obj.consume():
        monkeypatch.setattr(m.time, "monotonic", lambda: obj.consume_end)
    assert obj.failed and obj.used and not obj.active
    peer.borrowed()


def test_wrong_thread_poisoned_without_closing_caller_resources(peer):
    obj = peer.make()
    outcomes = []

    def other():
        try:
            obj.wait()
        except m.UnconfirmedPermission:
            outcomes.append("refused")

    child = Thread(target=other)
    child.start()
    child.join(timeout=3)
    assert not child.is_alive() and outcomes == ["refused"] and obj.failed
    peer.borrowed()


def test_busy_wait_cannot_release_another_scope_lock(peer):
    obj = peer.make()
    assert obj.lock.acquire(blocking=False)
    try:
        denied(obj.wait)
        assert obj.failed and obj.lock.locked()
    finally:
        obj.lock.release()
    peer.borrowed()


def test_wait_interruption_is_preserved_and_original_attempt_is_consumed(peer, monkeypatch):
    obj = peer.make()
    interruption = KeyboardInterrupt()

    def interrupted():
        raise interruption

    monkeypatch.setattr(peer.timer, "read", interrupted)
    with pytest.raises(KeyboardInterrupt) as caught:
        obj.wait()
    assert caught.value is interruption and obj.requested and obj.failed
    assert not obj.lock.locked()
    denied(obj.wait)
    peer.borrowed()


def test_replaced_socket_descriptor_refuses_even_with_same_owner_and_fd_number(peer):
    obj = peer.make()
    original = os.dup(peer.channel.fileno())
    left, right = socket.socketpair()
    try:
        left.setblocking(False)
        os.dup2(left.fileno(), peer.channel.fileno(), inheritable=False)
        denied(obj.wait)
    finally:
        os.dup2(original, peer.channel.fileno(), inheritable=False)
        os.close(original)
        left.close()
        right.close()
    assert obj.failed and not obj.approved
    peer.borrowed()


def test_partial_nonblocking_io_completes_one_frame_without_reissuing_challenge(peer, monkeypatch):
    obj = peer.make()
    send, recv = socket.socket.send, socket.socket.recv
    calls = dict(send=0, recv=0)

    def short_send(channel, raw, flags=0):
        calls["send"] += 1
        if calls["send"] == 1:
            raise BlockingIOError()
        return send(channel, raw[:29], flags)

    def short_recv(channel, count, flags=0):
        if not flags & socket.MSG_PEEK:
            calls["recv"] += 1
            if calls["recv"] == 1:
                raise BlockingIOError()
        return recv(channel, min(11, count), flags)

    monkeypatch.setattr(socket.socket, "send", short_send)
    monkeypatch.setattr(socket.socket, "recv", short_recv)
    obj.wait()
    assert calls["send"] > 2 and calls["recv"] > 2
    challenge = json.loads(line(peer.child))
    assert line(peer.child) == b"sent\n"
    assert m.plans.base.checksum(challenge) == obj.challenge_sha256
    with obj.consume():
        pass
    peer.borrowed()


@pytest.mark.parametrize("phase", ["before_consume", "within_consume"])
def test_original_deadline_is_not_extended_by_late_scope_entry(peer, monkeypatch, phase):
    obj = peer.make()
    obj.wait()
    original_deadline = obj.deadline
    if phase == "before_consume":
        monkeypatch.setattr(m.time, "monotonic", lambda: original_deadline)
        denied(lambda: obj.consume().__enter__())
    else:
        # Less than two seconds remain. The available scope shrinks; it does
        # not get a fresh two seconds past the originally captured cutoff.
        monkeypatch.setattr(m.time, "monotonic", lambda: original_deadline - 0.1)
        with obj.consume() as guard:
            assert obj.consume_end == original_deadline
            guard()
    assert obj.deadline == original_deadline
    peer.borrowed()


def test_original_peer_exit_after_reply_cannot_authorize_scope(peer):
    obj = peer.make()
    obj.wait()
    peer.child.stdin.close()
    peer.child.wait(timeout=3)
    assert peer.observer.exited()
    denied(lambda: obj.consume().__enter__())
    assert obj.failed and not peer.timer.closed and not peer.domain.closed
    os.fstat(peer.observer.fd)


def test_close_inside_scope_cannot_be_suppressed_to_report_success(peer):
    obj = peer.make()
    obj.wait()
    with pytest.raises(m.UnconfirmedPermission), obj.consume():
        obj.close()
    assert obj.closed and obj.failed
    peer.borrowed()


@pytest.mark.parametrize("peer", ["late-extra"], indirect=True)
def test_delayed_replay_during_scope_refuses(peer):
    obj = peer.make()
    obj.wait()
    assert json.loads(line(peer.child))["kind"] == m.CHALLENGE_KIND
    assert line(peer.child) == b"sent\n"
    with pytest.raises(m.UnconfirmedPermission), obj.consume() as guard:
        peer.child.stdin.write(b"extra\n")
        assert line(peer.child) == b"extra\n"
        guard()
    assert obj.failed
    peer.borrowed()


@pytest.mark.parametrize("value", [None, True, b"a" * 64, "A" * 64, "PRIVATE", "a" * 63])
def test_response_codec_refuses_invalid_digest_without_io(value):
    denied(lambda: m.permission_bytes(value))


def test_response_codec_is_only_canonical_bytes_not_live_permission():
    raw = m.permission_bytes("a" * 64)
    assert (
        raw
        == m.plans.base.encode(
            dict(
                schema=1,
                kind=m.PERMISSION_KIND,
                challenge_sha256="a" * 64,
            )
        )
        + b"\n"
    )
    assert not isinstance(raw, m.Permission)


def test_receiver_not_silently_added_to_existing_helper_profiles():
    import supplemental_recording_host_source as source

    assert NAME + ".py" not in source.HELPER_FILES
    assert NAME + ".py" not in source.STARTUP_FILES
    assert NAME not in source.ROOTS and NAME not in source.STARTUP_ROOTS


def test_importing_receiver_cannot_open_channel_or_capture_clock():
    script = r"""
import os, socket, subprocess, sys
sys.path.insert(0, sys.argv[1])
def forbidden(*args, **kwargs):
    raise AssertionError("Import attempted runtime activity")
for name in ("connect", "connect_ex", "bind", "listen", "send", "sendall", "sendto"):
    setattr(socket.socket, name, forbidden)
subprocess.Popen = forbidden
os.fork = os.system = os.pidfd_open = forbidden
import supplemental_recording_clock as clock
clock.read = forbidden
import supplemental_recording_service_permission
assert "supplemental_recording_service_startup" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(Path(m.__file__).parent)],
        capture_output=True,
        timeout=5,
        check=True,
    )
    assert not result.stdout and not result.stderr
