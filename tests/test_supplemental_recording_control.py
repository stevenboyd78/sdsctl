"""Actual fork/socket credentials; no installed launcher or physical scanner."""

import importlib.util
import json
import os
import signal
import socket
import struct
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_channel as channel_tests
from . import test_supplemental_recording_launch_plan as launch_tests

NAME = "supplemental_recording_control"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(launch_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
tree = channel_tests.tree
configured = launch_tests.construction_tests.configured
prepared = launch_tests.construction_tests.prepared


@pytest.fixture
def context(prepared):
    plan = m.launch.LaunchPlan(
        prepared.spec,
        prepared.stored,
        prepared.config,
        prepared.generation,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
    )
    return m.Context(plan, time.monotonic() + 2.5)


def binding(context, at=None):
    at = time.monotonic() if at is None else at
    p = context.plan
    return m.returns.Binding(
        p.stored, p.generation, p.projection_sha256, p.source_sha256, at + 8, at + 18
    )


def refused(action):
    with pytest.raises(m.UnconfirmedControl) as error:
        action()
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)


@contextmanager
def child(context, action, *, identity=None):
    left, right = m.pair()
    wait, go = os.pipe()
    parent = dict(
        pid=os.getpid(),
        start_ticks=m.returns._identity(os.getpid())[1],
        uid=os.geteuid(),
        gid=os.getegid(),
    )
    pid = os.fork()
    if pid == 0:
        try:
            left.close()
            os.close(go)
            assert os.read(wait, 1) == b"1"
            os.close(wait)
            action(right, parent)
            right.close()
            os._exit(0)
        except BaseException:
            os._exit(73)
    right.close()
    os.close(wait)
    guardian, pidfd = None, os.pidfd_open(pid)
    try:
        guardian = m.Parent(
            left,
            context,
            **(
                dict(
                    pid=pid,
                    start_ticks=m.returns._identity(pid)[1],
                    uid=os.geteuid(),
                    gid=os.getegid(),
                )
                | (identity or {})
            ),
        )
        os.write(go, b"1")
        os.close(go)
        go = -1
        yield guardian
    finally:
        if go >= 0:
            os.close(go)
        left.close()
        if guardian is not None:
            guardian.close()
        if not m.select.select([pidfd], [], [], 5)[0]:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        if guardian is not None:
            guardian.fixture_returncode = os.waitstatus_to_exitcode(status)
        os.close(pidfd)


def frame(context, phase="ready", body=None):
    return {
        "schema": 1,
        "kind": "finite-recording-control",
        "context": context.payload(),
        "phase": phase,
        "body": {} if body is None else body,
        "at": time.monotonic(),
    }


def wait_for_begin(context, right, parent):
    peer = m.Child(right, context, **parent)
    try:
        ready_at = m._send(peer, "ready", {}, deadline=context.ready_by)
        value, received = m._receive(peer, "begin")
        return m._begin(
            context,
            value["body"],
            ready_at=ready_at,
            message_at=value["at"],
            now=received.received_at,
        )
    finally:
        peer.close()


def test_private_readiness_precedes_one_begin_and_actual_return_receiver(context, prepared):
    def run(right, parent):
        accepted = wait_for_begin(context, right, parent)
        current = replace(
            prepared.tree.expected,
            audio_endpoint_sha256=prepared.stored.contract.audio_endpoint_sha256,
        )
        plan = channel_tests.plan(accepted)
        sender = m.returns.Sender(right.outgoing, accepted)
        sender.started(current, plan)
        time.sleep(max(0, plan.stop_at - time.monotonic()))
        report = channel_tests.completed(accepted, current)
        sender.completed(
            m.launch.protected.evidence.FinalizedRecording(**report["artifact"]), report["stopped"]
        )

    with child(context, run) as guardian:
        ready = guardian.receive_ready()
        assert ready.pid == guardian.pid != os.getpid()
        assert json.loads(ready.raw)["phase"] == "ready"
        assert m.launch.protected.Collector(prepared.stored).pristine().files.stage == "pristine"
        assert not list(prepared.spec.receipts.iterdir())
        intent_at = time.monotonic()
        reader = guardian.begin(
            binding(context, intent_at), intent_at=intent_at, intent_sha256="1" * 64
        )
        try:
            assert reader.pid == ready.pid and guardian.phase == "closed"
            assert json.loads(reader.receive().raw)["phase"] == "started"
            assert json.loads(reader.receive().raw)["phase"] == "completed"
            for endpoint in (guardian.channel.incoming, guardian.channel.outgoing):
                m.returns._socket(endpoint)
                assert not endpoint.get_inheritable()
            refused(
                lambda: guardian.begin(
                    binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64
                )
            )
        finally:
            reader.close()
    assert guardian.fixture_returncode == 0


@pytest.mark.parametrize("fault", ["no_message", "closed", "bad_receive_uid", "bad_receive_gid"])
def test_missing_ready_or_wrong_child_credentials_consumes_case(context, fault):
    context = replace(context, ready_by=time.monotonic() + 0.3)

    def run(right, parent):
        if fault == "closed":
            return
        if fault != "no_message":
            right.outgoing.send(m.encode(frame(context)))
        right.incoming.recv(1)

    identity = {}
    if fault.startswith("bad_receive_"):
        key = fault.removeprefix("bad_receive_")
        identity[key] = (os.geteuid() if key == "uid" else os.getegid()) + 1
    with child(context, run, identity=identity) as guardian:
        refused(guardian.receive_ready)
        refused(guardian.receive_ready)
        assert guardian.ready is None and guardian.phase == "unconfirmed"


def test_child_rechecks_deadline_after_peer_validation(context, monkeypatch):
    def run(right, parent):
        peer = m.Child(right, context, **parent)
        try:
            m._send(peer, "ready", {}, deadline=context.ready_by)
            right.incoming.recv(1)
        finally:
            peer.close()

    with child(context, run) as guardian:
        original = guardian.live
        calls = []

        def live():
            original()
            calls.append(1)
            if len(calls) == 2:
                monkeypatch.setattr(m.time, "monotonic", lambda: context.ready_by + 1)

        monkeypatch.setattr(guardian, "live", live)
        refused(guardian.receive_ready)
        assert len(calls) == 2 and guardian.ready is None


def test_private_pairs_are_required_in_correct_directions(context):
    left, right = m.pair()
    parent = dict(
        pid=os.getppid(),
        start_ticks=m.returns._identity(os.getppid())[1],
        uid=os.geteuid(),
        gid=os.getegid(),
    )
    try:
        refused(lambda: m.Child(m.Channels(right.outgoing, right.incoming), context, **parent))
        refused(lambda: m.Child(m.Channels(right.incoming, right.incoming), context, **parent))
        right.outgoing.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        refused(lambda: m.Child(right, context, **parent))
    finally:
        left.close()
        right.close()


def test_wrong_trial_type_never_invokes_unknown_methods(context):
    def run(right, parent):
        class Unknown:
            def cancel(self):
                raise AssertionError("Must not call an unvalidated trial")

        peer = m.Child(right, context, **parent)
        try:
            refused(lambda: peer.request_when_ready(Unknown(), None))
            assert peer.phase == "unconfirmed"
        finally:
            peer.close()

    with child(context, run) as guardian:
        refused(guardian.receive_ready)
    assert guardian.fixture_returncode == 0


@pytest.mark.parametrize("fault", ["set_failure", "get_failure", "wrong_signal", "parent_changed"])
def test_parent_death_setting_refuses_unconfirmed_setup_before_runtime(context, monkeypatch, fault):
    def run(right, parent):
        peer = m.Child(right, context, **parent)
        calls = []

        def prctl(op, *args):
            calls.append(op)
            if op == 1:
                return -1 if fault == "set_failure" else 0
            if fault == "parent_changed":
                monkeypatch.setattr(peer, "live", lambda: m.require(False))
            m.ctypes.cast(args[0], m.ctypes.POINTER(m.ctypes.c_int)).contents.value = (
                signal.SIGTERM if fault == "wrong_signal" else signal.SIGKILL
            )
            return -1 if fault == "get_failure" else 0

        class Libc:
            pass

        libc = Libc()
        libc.prctl = prctl
        monkeypatch.setattr(m.ctypes, "CDLL", lambda *_, **__: libc)
        try:
            refused(lambda: m._parent_death(peer))
            assert calls == ([1] if fault == "set_failure" else [1, 2])
        finally:
            peer.close()

    with child(context, run) as guardian:
        refused(guardian.receive_ready)
    assert guardian.fixture_returncode == 0


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "profile",
        "launch",
        "generation",
        "host_plan",
        "manifest",
        "contract",
        "projection",
        "ready_by",
        "phase",
        "extra",
        "schema",
        "body",
        "future",
        "before_bind",
        "noncanonical",
        "duplicate",
        "constant",
        "empty",
        "oversized",
        "descriptor",
        "sibling",
    ],
)
def test_bad_ready_is_consumed_without_begin(context, fault):
    def run(right, parent):
        value = frame(context)
        if fault in value["context"]:
            value["context"][fault] = context.ready_by + 1 if fault == "ready_by" else "9" * 64
        elif fault == "phase":
            value["phase"] = "begin"
        elif fault == "extra":
            value["PRIVATE_EXTRA"] = "secret"
        elif fault == "schema":
            value["schema"] = True
        elif fault == "body":
            value["body"] = {"recording_active": True}
        elif fault in ("future", "before_bind"):
            value["at"] = time.monotonic() + (10 if fault == "future" else -10)
        raw = m.encode(value)
        if fault == "noncanonical":
            raw += b"\n"
        elif fault == "duplicate":
            raw = raw[:-1] + b',"schema":1}'
        elif fault == "constant":
            raw = raw.replace(b'"schema":1', b'"schema":NaN')
        elif fault == "empty":
            raw = b""
        elif fault == "oversized":
            raw = b"x" * (m.MAX_BYTES + 1)
        if fault == "descriptor":
            right.outgoing.sendmsg(
                [raw],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", right.outgoing.fileno()))],
            )
        elif fault == "sibling":
            another = os.fork()
            if another == 0:
                right.outgoing.send(raw)
                os._exit(0)
            os.waitpid(another, 0)
        else:
            right.outgoing.send(raw)
        right.incoming.recv(1)  # Keep the actual child alive until the fixture closes.

    before = len(list(Path("/proc/self/fd").iterdir()))
    with child(context, run) as guardian:
        refused(guardian.receive_ready)
        refused(guardian.receive_ready)
        refused(
            lambda: guardian.begin(
                binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64
            )
        )
    assert len(list(Path("/proc/self/fd").iterdir())) == before


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "projection",
        "generation",
        "manifest",
        "early_intent",
        "future_intent",
        "bad_hash",
        "short_start",
        "short_finish",
        "long_start",
        "long_finish",
    ],
)
def test_bad_begin_consumes_parent_without_dispatch(context, fault):
    def run(right, parent):
        try:
            wait_for_begin(context, right, parent)
        except Exception:
            return
        raise AssertionError("Invalid intent reached child")

    with child(context, run) as guardian:
        guardian.receive_ready()
        at = time.monotonic()
        current, options = binding(context, at), dict(intent_at=at, intent_sha256="1" * 64)
        if fault in ("source", "projection", "generation"):
            current = replace(
                current, **{fault + "_sha256" if fault != "generation" else fault: "9" * 64}
            )
        elif fault == "manifest":
            current = replace(current, stored=replace(current.stored, manifest_sha256="9" * 64))
        elif fault in ("early_intent", "future_intent"):
            options["intent_at"] += -1 if fault == "early_intent" else 1
        elif fault == "bad_hash":
            options["intent_sha256"] = "PRIVATE"
        elif fault in ("short_start", "long_start"):
            current = replace(current, start_by=at + (1 if fault == "short_start" else 11))
        elif fault in ("short_finish", "long_finish"):
            current = replace(current, finish_by=at + (10 if fault == "short_finish" else 181))
        refused(lambda: guardian.begin(current, **options))
        refused(
            lambda: guardian.begin(
                binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64
            )
        )
    assert guardian.fixture_returncode == 0


@pytest.mark.parametrize("side", ["ready", "begin"])
def test_cancelled_parent_phase_cannot_retry(context, monkeypatch, side):
    def run(right, parent):
        with suppress(Exception):
            wait_for_begin(context, right, parent)

    with child(context, run) as guardian:

        def cancelled(*_, **__):
            raise KeyboardInterrupt

        if side == "ready":
            monkeypatch.setattr(m, "_receive", cancelled)
            with pytest.raises(KeyboardInterrupt):
                guardian.receive_ready()
        else:
            guardian.receive_ready()
            monkeypatch.setattr(m, "_send", cancelled)
            with pytest.raises(KeyboardInterrupt):
                guardian.begin(binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64)
        assert guardian.phase == "unconfirmed"
        refused(guardian.receive_ready)


def test_context_deadlines_and_socket_types_fail_before_runtime(context):
    for deadline in (True, float("nan"), 0, time.monotonic() - 1, time.monotonic() + 601):
        left, right = m.pair()
        try:
            refused(
                lambda left=left, deadline=deadline: m.Child(
                    left,
                    replace(context, ready_by=deadline),
                    pid=os.getppid(),
                    start_ticks=1,
                    uid=os.geteuid(),
                    gid=os.getegid(),
                )
            )
        finally:
            left.close()
            right.close()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wrong:
        refused(
            lambda: m.Child(
                wrong, context, pid=os.getppid(), start_ticks=1, uid=os.geteuid(), gid=os.getegid()
            )
        )


@pytest.mark.parametrize("field", ["pid", "start_ticks", "uid", "gid"])
def test_incorrect_parent_identity_refuses(context, field):
    def run(right, parent):
        parent[field] += 1
        if field in ("pid", "start_ticks"):
            refused(lambda: m.Child(right, context, **parent))
        else:
            peer = m.Child(right, context, **parent)
            try:
                m._send(peer, "ready", {}, deadline=context.ready_by)
                try:
                    m._receive(peer, "begin")
                except Exception:
                    return
                raise AssertionError("Wrong parent credentials accepted")
            finally:
                peer.close()

    with child(context, run) as guardian:
        if field in ("pid", "start_ticks"):
            # Child returns without a readiness frame.
            refused(guardian.receive_ready)
        else:
            guardian.receive_ready()
            reader = guardian.begin(
                binding(context), intent_at=time.monotonic(), intent_sha256="1" * 64
            )
            try:
                channel_tests.refused(reader.receive)
            finally:
                reader.close()
    assert guardian.fixture_returncode == 0
