"""Finite entry policy with real owned peers/pipes, but NO opened TCP listener.

The server fixture is explicitly synthetic. Installed image/Engine/listener and
independent frozen-process recovery qualification remain separate requirements.
"""

import asyncio
import hashlib
import importlib.util
import os
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

from sds200.exceptions import DaemonUnavailableError

from . import test_supplemental_recording_probe_entry as probe_entry
from .test_supplemental_recording_probe_entry import wire
from .test_supplemental_recording_web_service import m as service
from .test_supplemental_recording_web_service import tree as tree

sys.modules["supplemental_recording_web_service"] = service
PATH = Path(service.__file__).with_name("accept_supplemental_recording_web.py")
SPEC = importlib.util.spec_from_file_location("accept_supplemental_recording_web", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

configured, cached, prepared, staged = (
    probe_entry.configured,
    probe_entry.cached,
    probe_entry.prepared,
    probe_entry.staged,
)
# The launch-plan fixture's filesystem tree is different from the live Unix
# peer tree used above. Alias the latter so pytest can resolve each explicitly.
peer_tree = tree
tree = probe_entry.tree


def argv():
    return [
        "--plan",
        "/case/launch.json",
        "--plan-sha256",
        "a" * 64,
        "--source-sha256",
        "b" * 64,
        "--runtime-root",
        "/installed/sds200",
        "--ready-by",
        str(time.monotonic() + 20),
        "--request-sha256",
        "c" * 64,
    ]


def test_arguments_have_no_listener_auth_or_command_options():
    args = m.arguments(argv())
    assert set(args) == m.FIELDS
    assert (m.HOST, m.PORT) == ("0.0.0.0", 8099)
    assert args["plan"] == "/case/launch.json"


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "extra",
        "listen",
        "auth",
        "command",
        "hash",
        "relative",
        "parent",
        "root",
        "noncanonical",
        "wrong_name",
        "expired",
        "renewed",
        "nan",
        "infinity",
        "nonstring",
        "tuple",
    ],
)
def test_closed_arguments_reject_expansion_or_invalid_originals(fault):
    args = argv()
    if fault == "missing":
        args = args[:-2]
    elif fault == "duplicate":
        args[-2:] = args[:2]
    elif fault in ("extra", "listen", "auth", "command"):
        args += ["--" + fault, "PRIVATE"]
    elif fault == "hash":
        args[3] = "A" * 64
    elif fault in ("relative", "parent", "root", "noncanonical", "wrong_name"):
        args[1] = {
            "relative": "launch.json",
            "parent": "/tmp/../case/launch.json",
            "root": "/",
            "noncanonical": "/case//launch.json",
            "wrong_name": "/case/other.json",
        }[fault]
    elif fault in ("expired", "renewed", "nan", "infinity"):
        args[9] = {
            "expired": "0",
            "renewed": str(time.monotonic() + 900),
            "nan": "nan",
            "infinity": "inf",
        }[fault]
    elif fault == "nonstring":
        args[1] = Path(args[1])
    else:
        args = tuple(args)
    with pytest.raises(ValueError):
        m.arguments(args)


@pytest.fixture
def channel():
    incoming, writer = os.pipe()
    reader, outgoing = os.pipe()
    stream = wire.Stream(incoming, outgoing, role="web")
    os.write(writer, wire.HEADER.pack(2) + b"{}")
    assert stream.receive(deadline=time.monotonic() + 1) == {}

    def close_input():
        nonlocal writer
        os.close(writer)
        writer = -1

    try:
        yield SimpleNamespace(stream=stream, writer=writer, reader=reader, close_input=close_input)
    finally:
        stream.close()
        for fd in (incoming, writer, reader, outgoing):
            with suppress(OSError):
                os.close(fd)


def test_web_role_has_only_one_request_and_reply_and_quiet_original_pipe(channel):
    stream = channel.stream
    m.quiet(stream, time.monotonic() + 1)
    stream.send({"kind": "test"}, deadline=time.monotonic() + 1)
    m.quiet(stream, time.monotonic() + 1)
    assert (stream.reads, stream.writes) == (1, 1)
    with pytest.raises(wire.UnconfirmedStream):
        stream.send({}, deadline=time.monotonic() + 1)


@pytest.mark.parametrize("fault", ["eof", "extra", "expired"])
def test_lost_or_reused_attachment_is_not_a_command_to_resume(channel, fault):
    deadline = time.monotonic() + 1
    if fault == "eof":
        channel.close_input()
    elif fault == "extra":
        os.write(channel.writer, b"x")
    else:
        deadline = time.monotonic() - 1
    with pytest.raises(ValueError):
        m.quiet(channel.stream, deadline)


def test_gate_rejects_http_without_reading_body_and_only_forwards_lifespan():
    async def run():
        calls, output = [], []

        async def app(scope, receive, send):
            calls.append(scope["type"])

        async def receive():
            pytest.fail("Closed listener gate read request data")

        async def send(value):
            output.append(value)

        gate = m._Gate(app)
        await gate({"type": "http"}, receive, send)
        assert output[0]["status"] == 503 and output[-1]["body"] == b""
        assert dict(output[0]["headers"])[b"cache-control"] == b"no-store"
        await gate({"type": "websocket"}, receive, send)
        assert output[-1] == {"type": "websocket.close", "code": 1008}
        await gate({"type": "lifespan"}, receive, send)
        assert calls == ["lifespan"]
        gate.open = True
        await gate({"type": "http"}, receive, send)
        assert calls == ["lifespan", "http"]
        gate.open = False
        with pytest.raises(ValueError):
            await gate({"type": "unknown"}, receive, send)

    asyncio.run(run())


@pytest.fixture
def runtime(peer_tree, channel, monkeypatch):
    result = SimpleNamespace(
        config=None,
        checks=0,
        fail_at=None,
        running=False,
        stuck=False,
        address=(m.HOST, m.PORT),
        finalizing=False,
    )

    def recheck():
        result.checks += 1
        if result.checks == result.fail_at:
            raise ValueError("Synthetic input drift")

    # The actual actor tuple comes from the owned native peer fixture; this
    # synthetic plan recheck does NOT claim immutable-file/Ready provenance.
    prepared = SimpleNamespace(
        expected=peer_tree.expected,
        sockets=peer_tree.sockets,
        recheck=recheck,
        ready_by=time.monotonic() + 3,
        request_sha256="a" * 64,
    )

    class Listener:
        def __init__(self):
            self.sockets, self.closed = [self], False

        def getsockname(self):
            return result.address

        def close(self):
            self.closed = True

    class Server:
        def __init__(self, config):
            result.config = config
            result.server = self
            self.servers, self.started, self.should_exit = [Listener()], False, False

        async def serve(self):
            result.running = True
            try:
                if not result.stuck:
                    self.started = True
                while not self.should_exit:
                    await asyncio.sleep(0.01)
            finally:
                result.finalizing = True
                result.running = False

    monkeypatch.setattr(uvicorn, "Server", Server)
    result.prepared, result.channel = prepared, channel
    return result


def test_fixed_server_stays_finite_and_closes_peers_before_shutdown(runtime):
    async def run():
        qualified = []
        task = asyncio.create_task(
            m.serve(runtime.prepared, runtime.channel.stream, lambda: qualified.append(True))
        )
        until = time.monotonic() + 2
        while runtime.channel.stream.writes == 0:
            assert time.monotonic() < until and not task.done()
            await asyncio.sleep(0.01)
        assert runtime.checks == 2 and qualified == [True, True]
        config = runtime.config
        assert (config.host, config.port, config.loop, config.http, config.ws) == (
            m.HOST,
            m.PORT,
            "asyncio",
            "h11",
            "none",
        )
        assert config.lifespan == "on" and config.workers == 1 and not config.reload
        assert not config.access_log and not config.proxy_headers and not config.server_header
        assert config.limit_concurrency == config.backlog == 32
        assert config.timeout_graceful_shutdown == 0.5
        assert config.app.open
        runtime.channel.close_input()
        with pytest.raises(ValueError):
            await asyncio.wait_for(task, 2)
        assert not config.app.open and config.app.service._peers._closed
        assert runtime.finalizing and runtime.server.servers[0].closed
        assert runtime.channel.stream.writes == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["recheck", "qualify", "wrong_listener", "startup_expired", "extra_byte", "lost_peer"]
)
def test_drift_before_admission_never_sends_listening_report(runtime, fault):
    def qualify():
        if fault == "qualify" and runtime.checks == 2:
            raise ValueError("Synthetic source drift")

    if fault == "recheck":
        runtime.fail_at = 2
    elif fault == "wrong_listener":
        runtime.address = (m.HOST, 9999)
    elif fault == "startup_expired":
        runtime.stuck = True
        runtime.prepared.ready_by = time.monotonic() + 0.05
    elif fault == "extra_byte":
        os.write(runtime.channel.writer, b"x")
    elif fault == "lost_peer":
        runtime.prepared.sockets = runtime.prepared.sockets.with_name("absent")

    with pytest.raises((ValueError, DaemonUnavailableError)):
        asyncio.run(m.serve(runtime.prepared, runtime.channel.stream, qualify))
    assert runtime.channel.stream.writes == 0
    if runtime.config is not None:
        assert not runtime.config.app.open and runtime.config.app.service._peers._closed


def test_entry_main_always_reports_fixed_failure_and_no_recording_success(monkeypatch, capfd):
    def fail(argv):
        raise RuntimeError("PRIVATE exception")

    monkeypatch.setattr(m, "run", fail)
    assert m.main([]) == 70
    output = capfd.readouterr()
    assert output.out == "" and output.err == m.MESSAGE + "\n"


@pytest.mark.parametrize("fault", ["extra", "server_exit", "deadline"])
def test_after_listening_no_reuse_or_original_lifetime_extension(runtime, fault):
    if fault == "deadline":
        runtime.prepared.expected = replace(
            runtime.prepared.expected, deadline=time.monotonic() + 0.2
        )

    async def run():
        task = asyncio.create_task(m.serve(runtime.prepared, runtime.channel.stream, lambda: None))
        until = time.monotonic() + 2
        while runtime.channel.stream.writes == 0:
            assert time.monotonic() < until and not task.done()
            await asyncio.sleep(0.005)
        if fault == "extra":
            os.write(runtime.channel.writer, b"second request")
        elif fault == "server_exit":
            runtime.server.should_exit = True
        with pytest.raises((ValueError, DaemonUnavailableError)):
            await asyncio.wait_for(task, 2)
        assert runtime.channel.stream.writes == 1
        assert runtime.server.servers[0].closed and not runtime.config.app.open
        assert runtime.config.app.service._peers._closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "isolated",
        "bytecode",
        "unknown",
        "source_pin",
        "runtime_origin",
        "input_device",
        "request_pin",
        "request_schema",
        "partial",
        "duplicate_json",
        "noncanonical",
        "huge",
    ],
)
def test_actual_isolated_entry_early_refusal_does_not_open_runtime(staged, prepared, fault):
    probe_entry.operator.guard.prepare_source_pin(staged, prepared)
    raw = wire.encode({"schema": 1, "kind": "finite-recording-web-startup"})
    args = [
        "--plan",
        str(prepared.path),
        "--plan-sha256",
        hashlib.sha256(probe_entry.plans.p.encode(prepared.value)).hexdigest(),
        "--source-sha256",
        staged.pin,
        "--runtime-root",
        str(staged.layout.runtime),
        "--ready-by",
        str(time.monotonic() + 6),
        "--request-sha256",
        hashlib.sha256(raw).hexdigest(),
    ]
    flags = ("-B",) if fault == "isolated" else (("-I",) if fault == "bytecode" else ("-I", "-B"))
    if fault == "unknown":
        args += ["--listen", "PRIVATE"]
    elif fault in ("source_pin", "runtime_origin", "request_pin"):
        name, value = {
            "source_pin": ("source-sha256", "9" * 64),
            "runtime_origin": ("runtime-root", str(staged.layout.runtime.parent)),
            "request_pin": ("request-sha256", "9" * 64),
        }[fault]
        args = probe_entry.operator.child.changed(args, name, value)
    data = {
        "partial": b"\0\0",
        "duplicate_json": wire.HEADER.pack(13) + b'{"a":1,"a":2}',
        "noncanonical": wire.HEADER.pack(3) + b"{ }",
        "huge": wire.HEADER.pack(wire.MAX_BYTES + 1),
    }.get(fault, wire.HEADER.pack(len(raw)) + raw)
    process = subprocess.Popen(
        [
            str(staged.python),
            *flags,
            str(staged.layout.native / "accept_supplemental_recording_web.py"),
            *args,
        ],
        stdin=subprocess.DEVNULL if fault == "input_device" else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/PRIVATE_IGNORED"},
    )
    output, error = process.communicate(input=data if process.stdin else None, timeout=8)
    assert process.returncode == 70 and output == b"" and error == (m.MESSAGE + "\n").encode()
    assert not list(prepared.spec.sockets.iterdir()) and not list(prepared.spec.receipts.iterdir())
    assert probe_entry.plans.p.Collector(prepared.stored).pristine().files.stage == "pristine"
