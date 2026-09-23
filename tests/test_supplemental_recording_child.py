"""Exec-isolated private child, real loopback RTSP/RTP and exact watchdog.

This exercises the executable boundary, not installed image/source qualification.
All source files are from the local test checkout, and only synthetic peers run.
"""

import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

import pytest

from . import test_supplemental_recording_control as controls
from . import test_supplemental_recording_launch_plan as plans
from . import test_supplemental_recording_watchdog as watches

SOURCE = Path(plans.m.__file__).with_name("accept_supplemental_recording.py")
SPEC = importlib.util.spec_from_file_location("accept_supplemental_recording", SOURCE)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
c, p = controls.m, plans.p
tree, configured, cached, prepared = plans.tree, plans.configured, plans.cached, plans.prepared
construction = plans.construction_tests


def argv(prepared, channels, gate, *, ready_by=None):
    values = dict(
        plan=str(prepared.path),
        plan_sha256=hashlib.sha256(p.encode(prepared.value)).hexdigest(),
        source_sha256=prepared.value["source_sha256"],
        incoming_fd=channels.incoming.fileno(),
        outgoing_fd=channels.outgoing.fileno(),
        gate_fd=gate,
        parent_pid=os.getpid(),
        parent_ticks=c.returns._identity(os.getpid())[1],
        parent_uid=os.geteuid(),
        parent_gid=os.getegid(),
        ready_by=time.monotonic() + 5 if ready_by is None else ready_by,
    )
    return [
        part
        for name, value in values.items()
        for part in ("--" + name.replace("_", "-"), str(value))
    ]


@contextmanager
def launched(prepared, *, change=None, flags=("-I", "-B"), ready_by=None, extra_fds=()):
    left, right = c.pair()
    read, write = os.pipe()
    args = argv(prepared, right, read, ready_by=ready_by)
    if change:
        args = change(args)
    process = subprocess.Popen(
        [sys.executable, *flags, str(SOURCE), *args],
        pass_fds=(right.incoming.fileno(), right.outgoing.fileno(), read, *extra_fds),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": "/PRIVATE_ignored",
            "SDS200_HOST": "PRIVATE_ignored",
        },
    )
    right.close()
    os.close(read)

    def release(data=b"1"):
        nonlocal write
        assert write >= 0
        fd, write = write, -1
        try:
            if data is not None:
                os.write(fd, data)
        finally:
            os.close(fd)

    try:
        yield process, left, release
    finally:
        if write >= 0:
            os.close(write)
        left.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()


def changed(args, key, value):
    copied = list(args)
    copied[copied.index("--" + key) + 1] = value
    return copied


@pytest.mark.parametrize(
    "fault",
    [
        "isolated",
        "bytecode",
        "unknown",
        "duplicate",
        "help",
        "bad_digest",
        "wrong_digest",
        "wrong_source",
        "relative",
        "deadline",
        "parent_pid",
        "parent_ticks",
        "parent_uid",
        "same_fd",
        "bad_gate",
        "closed_gate",
    ],
)
def test_refusals(prepared, fault):
    flags = ("-I",) if fault == "bytecode" else (("-B",) if fault == "isolated" else ("-I", "-B"))

    def change(args):
        if fault == "unknown":
            return args + ["--PRIVATE", "secret"]
        if fault == "duplicate":
            return ["--source-sha256" if v == "--plan-sha256" else v for v in args]
        if fault == "help":
            return ["--help"]
        replacements = {
            "bad_digest": ("plan-sha256", "PRIVATE_SECRET"),
            "wrong_digest": ("plan-sha256", "9" * 64),
            "wrong_source": ("source-sha256", "9" * 64),
            "relative": ("plan", "PRIVATE/launch.json"),
            "deadline": ("ready-by", "NaN"),
            "parent_pid": ("parent-pid", str(os.getpid() + 1)),
            "parent_ticks": ("parent-ticks", "1"),
            "parent_uid": ("parent-uid", str(os.geteuid() + 1)),
            "same_fd": ("incoming-fd", args[args.index("--outgoing-fd") + 1]),
        }
        return changed(args, *replacements[fault]) if fault in replacements else args

    with launched(prepared, change=change, flags=flags) as (process, _channels, release):
        release(None if fault == "closed_gate" else (b"X" if fault == "bad_gate" else b"1"))
        out, err = process.communicate(timeout=4)
        assert process.returncode == 70 and out == b"" and err == (m.MESSAGE + "\n").encode()
    assert not list(prepared.spec.sockets.iterdir()) and not list(prepared.spec.receipts.iterdir())
    assert p.Collector(prepared.stored).pristine().files.stage == "pristine"


def test_extra_fd(prepared):
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as extra,
        launched(prepared, extra_fds=(extra.fileno(),)) as (process, _channels, release),
    ):
        release()
        out, err = process.communicate(timeout=3)
        assert process.returncode == 70 and out == b"" and err == (m.MESSAGE + "\n").encode()
    assert not list(prepared.spec.sockets.iterdir()) and not list(prepared.spec.receipts.iterdir())


def test_gate_deadline(prepared):
    # No release: the real child remains at its inherited gate, and the separate
    # watchdog terminates it. A missing permission can never fall through to run.
    with launched(prepared) as (process, _channels, _release):
        ticks = c.returns._identity(process.pid)[1]
        watchdog = watches.m.arm(
            process, start_ticks=ticks, deadline=time.monotonic() + 0.3, grace=0.1
        )
        try:
            out, err = process.communicate(timeout=2)
            assert process.returncode == -15 and out == err == b""
            assert watchdog.wait().returncode == 10
        finally:
            watchdog.close()
    assert not list(prepared.spec.sockets.iterdir()) and not list(prepared.spec.receipts.iterdir())


class RtspPeer:
    """Real TCP replies for only the scanner's documented RTSP sequence."""

    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.1)
        self.packets = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.packets.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.done, self.errors, self.methods, self.target = Event(), [], [], None
        self.thread = Thread(target=self.run)

    def run(self):
        try:
            while not self.done.is_set():
                try:
                    stream, address = self.listener.accept()
                    break
                except TimeoutError:
                    continue
            else:
                return
            assert address[0] == "127.0.0.1"
            with stream:
                stream.settimeout(0.1)
                pending = b""
                while not self.done.is_set():
                    try:
                        data = stream.recv(4096)
                    except TimeoutError:
                        continue
                    if not data:
                        return
                    pending += data
                    assert len(pending) <= 8192
                    if b"\r\n\r\n" not in pending:
                        continue
                    raw, pending = pending.split(b"\r\n\r\n", 1)
                    lines = raw.decode("ascii").split("\r\n")
                    method = lines[0].split()[0]
                    headers = dict(line.split(": ", 1) for line in lines[1:])
                    self.methods.append(method)
                    assert method in (
                        "OPTIONS",
                        "DESCRIBE",
                        "SETUP",
                        "PLAY",
                        "GET_PARAMETER",
                        "TEARDOWN",
                    )
                    body, fields = b"", {"CSeq": headers["CSeq"]}
                    if method == "DESCRIBE":
                        body = b"v=0\r\nm=audio 0 RTP/AVP 0\r\na=control:trackID=1\r\n"
                        fields["Content-Type"] = "application/sdp"
                    elif method == "SETUP":
                        port = int(re.search(r"client_port=(\d+)", headers["Transport"])[1])
                        self.target = ("127.0.0.1", port)
                        fields["Session"] = "finite-loopback"
                        fields["Transport"] = (
                            f"RTP/AVP;unicast;client_port={port};source=127.0.0.1;"
                            f"server_port={self.packets.getsockname()[1]};ssrc=5678"
                        )
                    elif method in ("PLAY", "GET_PARAMETER", "TEARDOWN"):
                        assert headers["Session"] == "finite-loopback"
                        fields["Session"] = "finite-loopback"
                    fields["Content-Length"] = str(len(body))
                    stream.sendall(
                        (
                            "RTSP/1.0 200 OK\r\n"
                            + "".join(f"{key}: {value}\r\n" for key, value in fields.items())
                            + "\r\n"
                        ).encode()
                        + body
                    )
        except BaseException as error:
            self.errors.append(error)

    def close(self):
        self.done.set()
        self.thread.join(1)
        self.listener.close()
        self.packets.close()
        assert not self.thread.is_alive() and not self.errors


@pytest.mark.parametrize("record", [False, True])
def test_run(prepared, monkeypatch, record):
    with monkeypatch.context() as patch:
        patch.setattr(Thread, "start", lambda self: None)
        scanner = construction.LoopbackScanner()
    rtsp = RtspPeer()
    with TemporaryDirectory(prefix="finite-child-") as local:
        base = Path(local)
        sockets, receipts, baseline = (base / name for name in ("sockets", "receipts", "baseline"))
        for directory in (sockets, receipts, baseline):
            directory.mkdir(mode=0o700)
        target = f"udp://127.0.0.1:{scanner.socket.getsockname()[1]}"
        spec = plans.m.construction.Specification(
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
        configuration = Path(tomllib.loads(deployment.read_text())["profile_config"])
        configuration.write_text(
            configuration.read_text().replace(prepared.config.scanner_target, target)
        )
        mode = base / "writer-mode"
        mode.touch(mode=0o666)
        writer = p.monitor.Writer(os.geteuid(), os.getegid(), mode.stat().st_mode & 0o777)
        endpoint = plans.m.construction.NetworkAudioTransport(
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
        prepared.value["profile"]["sha256"] = plans.m.cached.profile_files(
            deployment, prepared.tree.root
        )[0]
        prepared.path.write_bytes(p.encode(prepared.value))
        loaded = plans.load(prepared)
        context = c.Context(loaded, time.monotonic() + 5)
        guardian = watchdog = None
        try:
            with launched(prepared, ready_by=context.ready_by) as (process, channels, release):
                ticks = c.returns._identity(process.pid)[1]
                guardian = c.Parent(
                    channels,
                    context,
                    pid=process.pid,
                    start_ticks=ticks,
                    uid=os.geteuid(),
                    gid=os.getegid(),
                )
                # Real independent watchdog before any launch-gate release or
                # synthetic server thread starts in this guardian process.
                watchdog = watches.m.arm(
                    process, start_ticks=ticks, deadline=context.ready_by + 20, grace=1
                )
                scanner.thread.start()
                rtsp.thread.start()
                release()
                guardian.receive_ready()
                assert not list(receipts.iterdir())
                assert p.Collector(stored).pristine().files.stage == "pristine"
                if record:
                    reader = guardian.begin(
                        controls.binding(context),
                        intent_at=time.monotonic(),
                        intent_sha256="1" * 64,
                    )
                    try:
                        assert json.loads(reader.receive().raw)["phase"] == "started"
                        for i in range(8):
                            rtsp.packets.sendto(
                                construction.make_rtp(
                                    bytes(range(160)), sequence=100 + i, timestamp=1000 + i * 160
                                ),
                                rtsp.target,
                            )
                        report = json.loads(reader.receive().raw)
                        assert report["body"]["artifact"]["samples"] == 1280
                    finally:
                        reader.close()
                else:
                    channels.outgoing.close()  # Cancel readiness, never begin.
                out, err = process.communicate(timeout=6)
                assert process.returncode == (0 if record else 70), err.decode()
                assert out == b"" and err == (b"" if record else (m.MESSAGE + "\n").encode())
                assert watchdog.wait().returncode == 0
                assert not scanner.reads and not scanner.errors
                assert rtsp.methods[:4] == ["OPTIONS", "DESCRIBE", "SETUP", "PLAY"]
                assert rtsp.methods[-1] == "TEARDOWN"
                if not record:
                    assert not list(receipts.iterdir())
                    assert p.Collector(stored).pristine().files.stage == "pristine"
        finally:
            if guardian is not None:
                guardian.close()
            if watchdog is not None:
                watchdog.close()
            scanner.close()
            rtsp.close()
