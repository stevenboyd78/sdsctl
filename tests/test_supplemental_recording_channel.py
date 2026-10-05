"""Kernel-credential child fixtures; no real scanner or installed-container claim."""

import importlib.util
import json
import os
import signal
import socket
import struct
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_protected as fixtures

NAME = "supplemental_recording_channel"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(fixtures.p.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
tree = fixtures.tree


@pytest.fixture
def binding(tree):
    now = time.monotonic()
    return m.Binding(tree.stored, tree.expected.generation, "1" * 64, "2" * 64, now + 3, now + 6)


def plan(binding):
    now = time.monotonic()
    return m.owner.Plan(
        binding.stored.baseline.case,
        binding.generation,
        binding.stored.contract.audio_endpoint_sha256,
        now,
        now + 1,
        now + 1.01,
        now + 2,
    )


def started(binding, expected, selected):
    return {"expected": asdict(expected), "plan": asdict(selected)}


def completed(binding, expected):
    name = m.protected.evidence.filename(expected.case, expected.started_at)
    samples = 800
    stopped = dict(
        status="stopped",
        active=False,
        recording=name,
        metadata=name + ".json",
        started_at=expected.started_at,
        stopped_at="2026-09-23T08:00:01-06:00",
        elapsed_seconds=1.0,
        packets=5,
        samples=samples,
        audio_duration_seconds=0.1,
        reliability={k: 0 for k in m.protected.evidence.RELIABILITY},
        sink={
            k: 1600 if k in ("bytes_written", "bytes_submitted") else 0
            for k in m.protected.evidence.SINK
        },
        completed_recordings=1,
        closed=False,
        error=None,
    )
    artifact = m.protected.evidence.FinalizedRecording(
        expected.case,
        binding.generation,
        "3" * 64,
        "4" * 64,
        samples,
        5,
        0.1,
        len(binding.stored.baseline.files),
    )
    return {"artifact": asdict(artifact), "stopped": stopped}


def frame(binding, phase, body):
    return m.encode(
        {
            "schema": 1,
            "kind": "finite-recording-native-return",
            "binding": binding.payload(),
            "phase": phase,
            "body": body,
            "at": time.monotonic(),
        }
    )


def refused(action):
    with pytest.raises(m.UnconfirmedReturn) as error:
        action()
    assert str(error.value) == m.MESSAGE


@contextmanager
def child(binding, action, *, expected_changes=None):
    receive, send = m.pair()
    wait, go = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            receive.close()
            os.close(go)
            os.read(wait, 1)
            os.close(wait)
            action(send)
            send.close()
            os._exit(0)
        except BaseException as error:
            # Preserve useful child failure locations for a failed parent
            # assertion without dumping private payloads or exception text.
            # A finalized WAV is not proof that this child returned completion.
            frames = []
            current = error
            while current is not None and len(frames) < 64:
                trace = current.__traceback__
                while trace is not None and len(frames) < 64:
                    frames.append(
                        (
                            Path(trace.tb_frame.f_code.co_filename).name,
                            trace.tb_lineno,
                            type(current).__name__,
                        )
                    )
                    trace = trace.tb_next
                current = current.__context__
            os.write(2, (json.dumps({"owned_child_failure_locations": frames}) + "\n").encode())
            os._exit(71)
    os.close(wait)
    send.close()
    reader = None
    pidfd = os.pidfd_open(pid)
    try:
        ticks = m._identity(pid)[1]
        reader = m.Receiver(
            receive,
            binding,
            **(
                {"pid": pid, "start_ticks": ticks, "uid": os.geteuid(), "gid": os.getegid()}
                | (expected_changes or {})
            ),
        )
        os.write(go, b"1")
        os.close(go)
        go = -1
        yield reader
    finally:
        if go >= 0:
            os.close(go)
        if reader is not None:
            reader.close()
        receive.close()
        # Test fixture independently reaps its exact child, including on failed
        # assertions; channel success is never treated as process exit.
        if not m.select.select([pidfd], [], [], 0.3)[0]:
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        if reader is not None:
            reader.fixture_returncode = os.waitstatus_to_exitcode(status)
        os.close(pidfd)


def test_two_returns_identify_actual_child_not_socketpair_creating_parent(binding, tree):
    selected = plan(binding)

    def run(send):
        sender = m.Sender(send, binding)
        sender.started(tree.expected, selected)
        time.sleep(max(0, selected.stop_at - time.monotonic()))
        report = completed(binding, tree.expected)
        sender.completed(
            m.protected.evidence.FinalizedRecording(**report["artifact"]), report["stopped"]
        )

    with child(binding, run) as receiver:
        first = receiver.receive()
        assert first.pid != os.getpid() and first.pid == receiver.pid
        assert receiver.expected == tree.expected
        last = receiver.receive()
        assert receiver.phase == "closed" and last.pid == first.pid
        assert last.sha256 != first.sha256
        assert len(last.sha256) == 64 and json.loads(last.raw)["phase"] == "completed"
        refused(receiver.receive)


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_case",
        "wrong_generation",
        "wrong_endpoint",
        "wrong_manifest",
        "wrong_source",
        "wrong_projection",
        "wrong_phase",
        "future_clock",
        "expired_clock",
        "schema_bool",
        "extra",
        "duplicate",
        "noncanonical",
        "empty",
        "oversized",
        "extra_descriptor",
        "grandchild",
    ],
)
def test_unqualified_start_message_is_consumed_without_a_second_receive(binding, tree, fault):
    selected = plan(binding)

    def run(send):
        value = json.loads(frame(binding, "started", started(binding, tree.expected, selected)))
        if fault in ("wrong_case", "wrong_generation", "wrong_endpoint"):
            key = {
                "wrong_case": "case",
                "wrong_generation": "generation",
                "wrong_endpoint": "audio_endpoint_sha256",
            }[fault]
            value["body"]["expected"][key] = (
                "bb12345612344abc8abc123456789abc" if key == "case" else "f" * 64
            )
        elif fault in ("wrong_manifest", "wrong_source", "wrong_projection"):
            value["binding"][fault.removeprefix("wrong_")] = "f" * 64
        elif fault == "wrong_phase":
            value["phase"] = "completed"
        elif fault in ("future_clock", "expired_clock"):
            value["at"] = (
                binding.start_by + 1 if fault == "expired_clock" else time.monotonic() + 0.5
            )
        elif fault == "schema_bool":
            value["schema"] = True
        elif fault == "extra":
            value["extra"] = "PRIVATE"
        raw = m.encode(value)
        if fault == "duplicate":
            raw = raw[:-1] + b',"schema":1}'
        elif fault == "noncanonical":
            raw += b"\n"
        elif fault == "empty":
            return
        elif fault == "oversized":
            raw += b"x" * m.MAX_BYTES
        elif fault == "extra_descriptor":
            fd = os.open("/dev/null", os.O_RDONLY)
            try:
                send.sendmsg([raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))])
            finally:
                os.close(fd)
            return
        elif fault == "grandchild":
            pid = os.fork()
            if pid == 0:
                send.send(raw)
                os._exit(0)
            os.waitpid(pid, 0)
            return
        send.send(raw)

    with child(binding, run) as receiver:
        before = len(os.listdir("/proc/self/fd"))
        refused(receiver.receive)
        assert len(os.listdir("/proc/self/fd")) == before
        refused(receiver.receive)


@pytest.mark.parametrize(
    "fault",
    [
        "early",
        "wrong_samples",
        "wrong_file",
        "wrong_old_count",
        "wrong_digest",
        "lost_completion",
        "active",
        "error",
        "closed",
        "dropped",
    ],
)
def test_completion_must_match_start_and_cannot_be_inferred_from_exit(binding, tree, fault):
    selected = plan(binding)

    def run(send):
        send.send(frame(binding, "started", started(binding, tree.expected, selected)))
        if fault == "lost_completion":
            return
        body = completed(binding, tree.expected)
        if fault != "early":
            time.sleep(max(0, selected.stop_at - time.monotonic()))
        if fault == "wrong_samples":
            body["artifact"]["samples"] += 1
        elif fault == "wrong_file":
            body["stopped"]["recording"] = "PRIVATE_OTHER.wav"
        elif fault == "wrong_old_count":
            body["artifact"]["old_files"] += 1
        elif fault == "wrong_digest":
            body["artifact"]["wav_sha256"] = "not-a-digest"
        elif fault == "active":
            body["stopped"]["active"] = True
        elif fault == "error":
            body["stopped"]["error"] = "PRIVATE"
        elif fault == "closed":
            body["stopped"]["closed"] = True
        elif fault == "dropped":
            body["stopped"]["sink"]["bytes_dropped"] = 1
        send.send(frame(binding, "completed", body))

    with child(binding, run) as receiver:
        receiver.receive()
        refused(receiver.receive)
        refused(receiver.receive)


@pytest.mark.parametrize(
    "key,value", [("uid", 999999), ("gid", 999999), ("start_ticks", 1), ("pid", os.getpid())]
)
def test_wrong_peer_identity_does_not_establish_a_return_channel(binding, tree, key, value):
    def run(send):
        send.send(frame(binding, "started", started(binding, tree.expected, plan(binding))))

    if key in ("uid", "gid"):
        with child(binding, run, expected_changes={key: value}) as receiver:
            refused(receiver.receive)
    else:
        with pytest.raises(m.UnconfirmedReturn), child(binding, run, expected_changes={key: value}):
            pytest.fail("Unexpected identity acceptance")


def test_sender_cannot_retry_after_failed_or_duplicate_send(binding, tree):
    receive, send = m.pair()
    try:
        sender = m.Sender(send, binding)
        receive.close()
        refused(lambda: sender.started(tree.expected, plan(binding)))
        refused(lambda: sender.started(tree.expected, plan(binding)))
    finally:
        receive.close()
        send.close()


@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_only_private_seqpacket_is_accepted(binding, kind):
    left, right = socket.socketpair(socket.AF_UNIX, kind)
    try:
        refused(lambda: m.Sender(right, binding))
    finally:
        left.close()
        right.close()


def test_no_native_return_before_fixed_deadline_consumes_receiver(binding, tree):
    binding = replace(binding, start_by=time.monotonic() + 0.15)

    def run(send):
        time.sleep(0.3)

    with child(binding, run) as receiver:
        refused(receiver.receive)
        refused(receiver.receive)


def test_reply_validation_must_also_finish_before_original_deadline(binding, tree, monkeypatch):
    selected = plan(binding)

    def run(send):
        send.send(frame(binding, "started", started(binding, tree.expected, selected)))

    with child(binding, run) as receiver:
        original = m._decode
        with monkeypatch.context() as patch:

            def delayed(*args):
                value = original(*args)
                patch.setattr(m.time, "monotonic", lambda: binding.finish_by + 1)
                return value

            patch.setattr(m, "_decode", delayed)
            refused(receiver.receive)
        refused(receiver.receive)
