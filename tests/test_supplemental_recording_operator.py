"""Actual isolated fixed operator over private pipes, only synthetic scanner I/O."""

import hashlib
import json
import os
import select
import signal
import subprocess
import time
import tomllib
import wave
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import pytest

from . import test_supplemental_recording_guardian as guard
from . import test_supplemental_recording_wire as framing

tree, configured, cached, prepared, staged = (
    guard.tree,
    guard.configured,
    guard.cached,
    guard.prepared,
    guard.staged,
)
p, w, child = guard.p, framing.m, guard.child
MESSAGE = b"Finite recording operator is unconfirmed; preserve this case and do not replay.\n"


def start(staged, prepared, *, change=None, flags=("-I", "-B"), stdin=subprocess.PIPE):
    args = [
        "--plan",
        str(prepared.path),
        "--plan-sha256",
        hashlib.sha256(p.encode(prepared.value)).hexdigest(),
        "--source-sha256",
        staged.pin,
        "--runtime-root",
        str(staged.layout.runtime),
        "--ready-by",
        str(time.monotonic() + 8),
    ]
    if change is not None:
        args = change(args)
    return subprocess.Popen(
        [
            str(staged.python),
            *flags,
            str(staged.layout.native / "accept_supplemental_recording_operator.py"),
            *args,
        ],
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env={
            "PATH": "/usr/bin:/bin",
            "SDS200_HOST": "PRIVATE_IGNORED",
            "PYTHONPATH": "/PRIVATE_IGNORED",
        },
    )


def finish(process, code):
    if process.stdin is not None:
        process.stdin.close()
        process.stdin = None
    out, err = process.communicate(timeout=6)
    assert process.returncode == code, err.decode()
    assert out == b"" and err == (MESSAGE if code else b"")


@pytest.mark.parametrize(
    "fault",
    [
        "isolated",
        "bytecode",
        "unknown",
        "duplicate",
        "help",
        "bad_pin",
        "wrong_pin",
        "source_pin",
        "runtime_origin",
        "relative_plan",
        "relative_runtime",
        "root",
        "deadline",
        "past",
        "input_device",
    ],
)
def test_fixed_arguments_and_early_failures(staged, prepared, fault):
    guard.prepare_source_pin(staged, prepared)
    flags = ("-B",) if fault == "isolated" else (("-I",) if fault == "bytecode" else ("-I", "-B"))

    def change(args):
        if fault == "unknown":
            return args + ["--PRIVATE", "secret"]
        if fault == "duplicate":
            return ["--source-sha256" if a == "--plan-sha256" else a for a in args]
        if fault == "help":
            return ["--help"]
        options = {
            "bad_pin": ("plan-sha256", "PRIVATE_SECRET"),
            "wrong_pin": ("plan-sha256", "9" * 64),
            "source_pin": ("source-sha256", "9" * 64),
            "runtime_origin": ("runtime-root", str(staged.layout.runtime.parent)),
            "relative_plan": ("plan", "PRIVATE/launch.json"),
            "relative_runtime": ("runtime-root", "PRIVATE"),
            "root": ("runtime-root", "/"),
            "deadline": ("ready-by", "NaN"),
            "past": ("ready-by", "0"),
        }
        return child.changed(args, *options[fault]) if fault in options else args

    process = start(
        staged,
        prepared,
        flags=flags,
        change=change,
        stdin=subprocess.DEVNULL if fault == "input_device" else subprocess.PIPE,
    )
    finish(process, 70)
    assert not (prepared.path.parent / "guardian").exists()
    assert not list(prepared.spec.sockets.iterdir()) and not list(prepared.spec.receipts.iterdir())
    assert p.Collector(prepared.stored).pristine().files.stage == "pristine"


@pytest.mark.parametrize(
    "fault",
    [
        "record",
        "ready_eof",
        "partial_begin",
        "late_begin",
        "native_before_begin",
        "native_after_start",
        "lost_completed",
        "context",
        "phase",
        "schema",
        "body_extra",
        "binding",
        "deadline",
    ],
)
def test_fixed_operator_actual_session(staged, prepared, monkeypatch, fault):
    with monkeypatch.context() as patch:
        patch.setattr(Thread, "start", lambda self: None)
        scanner = child.construction.LoopbackScanner()
    rtsp = child.RtspPeer()
    with TemporaryDirectory(prefix="finite-operator-") as local:
        base = Path(local)
        sockets, receipts, baseline = (base / name for name in ("sockets", "receipts", "baseline"))
        case = prepared.path.parent / "guardian"
        for directory in (sockets, receipts, baseline, case):
            directory.mkdir(mode=0o700)
        target = f"udp://127.0.0.1:{scanner.socket.getsockname()[1]}"
        spec = child.plans.m.construction.Specification(
            "127.0.0.1",
            scanner.socket.getsockname()[1],
            rtsp.port,
            "127.0.0.1",
            0,
            sockets,
            receipts,
            "Version 1.26.01",
            1,
            2,
            10,
        )
        deployment = Path(prepared.value["profile"]["deployment"])
        config = Path(tomllib.loads(deployment.read_text())["profile_config"])
        config.write_text(config.read_text().replace(prepared.config.scanner_target, target))
        mode = base / "writer-mode"
        mode.touch(mode=0o666)
        writer = p.monitor.Writer(os.geteuid(), os.getegid(), mode.stat().st_mode & 0o777)
        endpoint = child.plans.m.construction.NetworkAudioTransport(
            "127.0.0.1", rtsp_port=rtsp.port
        ).endpoint
        stored = p.save_baseline(
            baseline, prepared.tree.baseline, writer, hashlib.sha256(endpoint.encode()).hexdigest()
        )
        prepared.value["specification"] = asdict(spec) | {
            "sockets": str(sockets),
            "receipts": str(receipts),
        }
        prepared.value["baseline"] = dict(
            directory=str(baseline), contract=asdict(stored.contract), sha256=stored.manifest_sha256
        )
        prepared.value["profile"]["sha256"] = child.plans.m.cached.profile_files(
            deployment, prepared.tree.root
        )[0]
        guard.prepare_source_pin(staged, prepared)
        scanner.thread.start()
        rtsp.thread.start()
        process = stream = None
        native_fd = -1
        try:
            process = start(staged, prepared)
            stream = w.Stream(process.stdout.fileno(), process.stdin.fileno(), role="host")
            ready = stream.receive(deadline=time.monotonic() + 12)
            assert ready["schema"] == 1 and ready["kind"] == "finite-recording-operator"
            assert ready["phase"] == "ready" and ready["guardian"]["pid"] == process.pid
            native = ready["native"]
            native_fd = os.pidfd_open(native["pid"])
            assert child.c.returns._identity(native["pid"]) == (process.pid, native["start_ticks"])
            assert not select.select([native_fd], [], [], 0)[0]
            assert not list(receipts.iterdir())
            assert p.Collector(stored).pristine().files.stage == "pristine"
            if fault == "partial_begin":
                os.write(process.stdin.fileno(), w.HEADER.pack(20) + b"{")
            if fault == "late_begin":
                # Real original readiness expiry: never replace it with a fresh
                # deadline just because the host missed its opportunity.
                with pytest.raises(w.UnconfirmedStream):
                    stream.receive(deadline=ready["context"]["ready_by"] + 3)
            if fault == "native_before_begin":
                signal.pidfd_send_signal(native_fd, signal.SIGKILL)
                assert select.select([native_fd], [], [], 3)[0]
            if fault not in ("ready_eof", "partial_begin", "late_begin"):
                now, context = time.monotonic(), ready["context"]
                binding = {
                    key: context[key]
                    for key in ("manifest", "contract", "generation", "projection", "source")
                }
                binding |= {"start_by": now + 8, "finish_by": now + 20}
                request = {
                    "schema": 1,
                    "kind": "finite-recording-operator",
                    "phase": "begin",
                    "context": dict(context),
                    "body": {"binding": binding, "intent_at": now, "intent_sha256": "1" * 64},
                }
                if fault == "context":
                    request["context"]["source"] = "9" * 64
                if fault == "phase":
                    request["phase"] = "finish"
                if fault == "schema":
                    request["schema"] = True
                if fault == "body_extra":
                    request["body"]["recording_start"] = True
                if fault == "binding":
                    binding["generation"] = "9" * 64
                if fault == "deadline":
                    binding["start_by"] = now - 1
                stream.send(request, deadline=time.monotonic() + 2)
            if fault in ("record", "native_after_start", "lost_completed"):
                started = stream.receive(deadline=time.monotonic() + 5)
                assert started["phase"] == "started" and started["context"] == context
                assert json.loads(started["body"]["received"]["raw"])["phase"] == "started"
                if fault == "native_after_start":
                    signal.pidfd_send_signal(native_fd, signal.SIGKILL)
                    assert select.select([native_fd], [], [], 3)[0]
                    with pytest.raises(w.UnconfirmedStream):
                        stream.receive(deadline=time.monotonic() + 5)
                else:
                    for i in range(8):
                        rtsp.packets.sendto(
                            child.construction.make_rtp(
                                bytes(range(160)), sequence=100 + i, timestamp=1000 + i * 160
                            ),
                            rtsp.target,
                        )
                if fault == "lost_completed":
                    # Close every reader after actual start. Finalization may
                    # succeed, but failure to deliver its report remains 70.
                    stream.close()
                    stream = None
                    process.stdout.close()
                    process.stdout = None
            if fault == "record":
                completed = stream.receive(deadline=time.monotonic() + 8)
                assert completed["phase"] == "completed" and completed["native"] == native
                returned = json.loads(completed["body"]["received"]["raw"])
                assert returned["body"]["artifact"]["samples"] == 1280
                exited = stream.receive(deadline=time.monotonic() + 5)
                assert exited["phase"] == "exited" and exited["native"] == native
                assert exited["body"]["pid"] == native["pid"]
                assert exited["body"]["returncode"] == exited["body"]["watchdog"]["returncode"] == 0
                assert select.select([native_fd], [], [], 0)[0]
            if stream is not None:
                stream.close()
            stream = None
            if fault == "lost_completed":
                process.stdin.close()
                process.stdin = None
                out, err = process.communicate(timeout=6)
                assert out is None and err == MESSAGE and process.returncode == 70
                # A good artifact cannot substitute for a lost completion.
                paths = list(stored.baseline.root.glob("*.wav"))
                new = [item for item in paths if item.name.startswith("sdsctl-acceptance-")]
                assert len(new) == 1
                with wave.open(str(new[0]), "rb") as saved:
                    assert saved.getnframes() == 1280
            else:
                finish(process, 0 if fault == "record" else 70)
            assert select.select([native_fd], [], [], 0)[0]
            assert (case / "launch-claimed.json").is_file()
            assert not scanner.reads and not scanner.errors
            current = p.evidence.inventory(stored.baseline.root)
            for name, old in prepared.tree.baseline.files:
                assert current[name] == asdict(old)
            if fault not in ("record", "native_after_start", "lost_completed"):
                assert not list(receipts.iterdir())
                assert p.Collector(stored).pristine().files.stage == "pristine"
        finally:
            if stream is not None:
                stream.close()
            if process is not None and process.poll() is None:
                if process.stdin is not None:
                    process.stdin.close()
                    process.stdin = None
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.send_signal(
                        signal.SIGINT
                    )  # Unwind exact operator; guardian reaps children.
                    process.communicate(timeout=5)
            if native_fd >= 0:
                os.close(native_fd)
            scanner.close()
            rtsp.close()
