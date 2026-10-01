"""Owned pidfds/real Unix HTTP and durable files; synthetic Engine/NS/listener.

No real listener, image/runtime qualification or installed HA handoff is claimed.
"""

import importlib.util
import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_probe_exec as probes

continuity, ready_tests, engine_tests, transport = (
    probes.continuity,
    probes.ready_tests,
    probes.engine_tests,
    probes.transport,
)
NAME = "supplemental_recording_web_exec"
SPEC = importlib.util.spec_from_file_location(NAME, Path(probes.m.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    family,
    actors,
    calibration,
    ledger,
) = (
    probes.layout,
    probes.tree,
    probes.routing,
    probes.projection,
    probes.binding,
    probes.directory,
    probes.prepared,
    probes.family,
    probes.actors,
    probes.calibration,
    probes.ledger,
)
WEB_ID = "9" * 64


@pytest.fixture
def web_child(actors):
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys;print('ready',flush=True);sys.stdin.read();sys.exit(70)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    fd = os.pidfd_open(child.pid, 0)
    try:
        assert select.select([child.stdout], [], [], 3)[0]
        assert child.stdout.readline() == b"ready\n"
        ticks = int(Path(f"/proc/{child.pid}/stat").read_text().rpartition(") ")[2].split()[19])
        actor = m.namespace.Actor(
            child.pid,
            42,
            os.getpid(),
            ticks,
            actors.values[0].container_id,
            actors.values[0].namespaces,
        )
        actors.lookup[child.pid] = actor
        yield SimpleNamespace(process=child, fd=fd, actor=actor)
    finally:
        # Only this owned harmless fixture child; a stopped test must be resumed
        # before stdin EOF can finish it. Never signal a name/discovered PID.
        if not select.select([fd], [], [], 0)[0]:
            signal.pidfd_send_signal(fd, signal.SIGCONT)
        child.stdin.close()
        child.wait(timeout=3)
        assert child.returncode == 70
        child.stdout.close()
        os.close(fd)


def metadata(command, *, pid=0, running=False, code=None):
    return probes.metadata(command, pid=pid, running=running, code=code)


@contextmanager
def case(
    prepared,
    actors,
    web_child,
    calibration,
    monkeypatch,
    *,
    change_reply=None,
    change_metadata=None,
    lost_at=None,
    after_reply=None,
    sender_credentials=False,
):
    peers, threads, errors = [], [], []
    state = SimpleNamespace(web=None, ready=None, request=None, peer=None)
    (prepared.directory.parent / "web-exec").mkdir(mode=0o700)

    def operator(peer, _request):
        state.operator_peer = peer.dup()
        peers.append(state.operator_peer)
        peer.sendall(
            transport.stream.UPGRADE
            + transport.stream.segment(transport.stream.app(ready_tests.envelope(prepared, actors)))
        )

    def create(peer, request):
        assert request[2] == state.web.command.create_body()
        assert m.dispatch.load_web(state.web.directory, state.web.web_pins).phase == "create_intent"
        peer.sendall(engine_tests.reply({"Id": WEB_ID}, 201))

    def inspect(index, *, running=False, ended=False):
        def response(peer, _request):
            value = metadata(
                state.web.command,
                pid=web_child.process.pid if running or ended else 0,
                running=running,
                code=70 if ended else None,
            )
            if change_metadata:
                change_metadata(index, value, state)
            peer.sendall(engine_tests.reply(value))

        return response

    def web(peer, _request):
        assert m.dispatch.load_web(state.web.directory, state.web.web_pins).phase == "attach_intent"
        state.peer = retained_peer = peer.dup()
        peers.append(retained_peer)
        peer.sendall(transport.stream.UPGRADE)

        def respond():
            try:
                first = retained_peer.recv(4)
                if not first:
                    return
                size = transport.stream.m.wire.HEADER.unpack(
                    first + transport.exact(retained_peer, 4 - len(first))
                )[0]
                state.request = json.loads(transport.exact(retained_peer, size))
                value = {
                    "schema": 1,
                    "kind": "finite-recording-web-listening",
                    "request_sha256": m.hashlib.sha256(m.binding.encode(state.request)).hexdigest(),
                    "pid": web_child.actor.local_pid,
                    "observed_at": time.monotonic(),
                    "deadline": state.request["watchdog"]["deadline"],
                }
                if change_reply:
                    change_reply(value, state)
                retained_peer.sendall(transport.stream.segment(transport.stream.app(value)))
                if after_reply:
                    after_reply(state)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except BaseException as error:
                errors.append(error)

        thread = Thread(target=respond)
        threads.append(thread)
        thread.start()

    handlers = [
        engine_tests.reply({"Id": transport.EXEC}, 201),
        engine_tests.reply(engine_tests.intents.metadata(prepared)),
        operator,
        *[engine_tests.reply(ready_tests.joined.live(prepared, actors))] * 2,
        create,
        inspect(0),
        web,
        inspect(1, running=True),
        inspect(2, running=True),
        inspect(3, ended=True),
    ]
    if lost_at is not None:
        handlers[lost_at] = b""
    try:
        with engine_tests.engine(
            prepared, monkeypatch, handlers, sender_credentials=sender_credentials
        ) as (client, requests):
            client.create()
            client.attach(
                finish_by=prepared.pins.command.ready_by
                + prepared.pins.host.projection.native.contract.maximum_recording_seconds
                + 3
            )
            ready = state.ready = ready_tests.capture(client, calibration)
            try:
                state.web = m.Launch(ready)
                state.requests = requests
                yield state
            finally:
                if state.web is not None:
                    state.web.close()
                ready.close()
    finally:
        for thread in threads:
            thread.join(4)
            assert not thread.is_alive()
        for peer in peers:
            with suppress(OSError):
                peer.close()
        assert not errors, errors


def refused(action, web):
    with pytest.raises(m.UnconfirmedWebExec) as error:
        action()
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)
    assert web.failed
    count = web.execution_id
    with pytest.raises(m.UnconfirmedWebExec):
        web.start()
    assert web.execution_id == count


@pytest.mark.parametrize("sender_credentials", [False, True])
def test_original_ready_durable_web_intents_actual_pidfd_and_separate_exit(
    prepared, actors, web_child, calibration, monkeypatch, sender_credentials
):
    with case(
        prepared, actors, web_child, calibration, monkeypatch, sender_credentials=sender_credentials
    ) as c:
        original = c.ready.ready_raw, c.ready.context_raw, c.ready.ready_by, c.ready.watch_deadline
        c.web.start()
        c.web.check()
        assert c.web.listening and not c.web.failed and c.web.actor == web_child.actor
        assert c.web.fd >= 0 and not select.select([c.web.fd], [], [], 0)[0]
        assert c.request == json.loads(c.web.request_raw)
        assert c.request["context"] == json.loads(c.ready.context_raw)
        assert c.request["watchdog"]["deadline"] == c.ready.watch_deadline
        assert len(c.requests) == 10 and not c.ready.client.attachment.begun
        assert m.dispatch.load_web(c.web.directory, c.web.web_pins) == c.web.tip
        assert original == (
            c.ready.ready_raw,
            c.ready.context_raw,
            c.ready.ready_by,
            c.ready.watch_deadline,
        )
        c.web.end()
        assert c.web.fd >= 0 and not select.select([c.web.fd], [], [], 0)[0]
        assert not c.ready.processes.exited("native") and not c.ready.client.endpoint.closed
        web_child.process.stdin.close()
        assert select.select([web_child.fd], [], [], 2)[0]
        result = c.web.observe_exit()
        assert result.host_pid == web_child.process.pid and result.execution_id == WEB_ID
        assert len(c.requests) == 11 and c.web.fd >= 0
        c.web.close()
        assert c.web.fd == -1 and not c.ready.client.endpoint.closed
        assert not any(hasattr(c.web, n) for n in ("begin", "stop", "signal", "restore"))


def test_post_begin_continuity_uses_same_retained_without_deadline_renewal(
    prepared, actors, web_child, calibration, ledger, monkeypatch
):
    with case(prepared, actors, web_child, calibration, monkeypatch) as c:
        c.web.start()
        continuity.begin.intent(ledger, prepared)
        continuity.begin.m.send_once(c.ready, ledger)
        assert transport.begun(c.operator_peer)["phase"] == "begin"
        kept = continuity.m.Retained(c.ready)
        c.web.retain(kept)
        c.web.check()
        assert c.web.original is kept and c.web.watch_deadline == c.ready.watch_deadline
        assert c.web.command.ready_by == c.ready.ready_by
        assert len(c.requests) == 10 and c.ready.client.attachment.reads == 1
        clock = m.time.monotonic
        with monkeypatch.context() as patch:
            patch.setattr(m.time, "monotonic", lambda: max(clock(), c.ready.ready_by + 0.01))
            c.web.check()  # Consumed launch deadline is not a renewed service deadline.
        assert c.web.command.ready_by == c.ready.ready_by


@pytest.mark.parametrize(
    "fault",
    ["schema", "extra", "kind", "hash", "pid", "pid_bool", "early", "future", "nan", "deadline"],
)
def test_listening_reply_is_closed_fresh_and_bound_to_original_web_pid(
    prepared, actors, web_child, calibration, monkeypatch, fault
):
    def change(value, state):
        if fault == "extra":
            value["PRIVATE"] = 1
        else:
            field, replacement = {
                "schema": ("schema", True),
                "kind": ("kind", "PRIVATE"),
                "hash": ("request_sha256", "0" * 64),
                "pid": ("pid", state.request["native"]["pid"]),
                "pid_bool": ("pid", True),
                "early": ("observed_at", time.monotonic() - 60),
                "future": ("observed_at", time.monotonic() + 60),
                "nan": ("observed_at", None),
                "deadline": ("deadline", state.web.watch_deadline + 1),
            }[fault]
            value[field] = replacement

    with case(prepared, actors, web_child, calibration, monkeypatch, change_reply=change) as c:
        refused(c.web.start, c.web)
        assert c.web.used and c.web.fd >= 0 and not c.ready.client.attachment.begun


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("fault", ["id", "command", "container", "pid_zero", "wrong_phase"])
def test_engine_metadata_cannot_bind_other_exec_or_unstarted_process(
    prepared, actors, web_child, calibration, monkeypatch, index, fault
):
    def change(at, value, _state):
        if at != index:
            return
        if fault == "id":
            value["ID"] = "0" * 64
        elif fault == "command":
            value["ProcessConfig"]["arguments"].append("--PRIVATE")
        elif fault == "container":
            value["ContainerID"] = "0" * 64
        elif fault == "pid_zero":
            value["Pid"] = 1234 if index == 0 else 0
        else:
            value["Running"] = not value["Running"]

    with case(prepared, actors, web_child, calibration, monkeypatch, change_metadata=change) as c:
        refused(c.web.start, c.web)
        assert c.request is None and not c.ready.client.attachment.begun


@pytest.mark.parametrize("lost_at", [5, 6, 7, 8, 9])
def test_lost_create_inspect_or_attach_return_consumes_web_case(
    prepared, actors, web_child, calibration, monkeypatch, lost_at
):
    with case(prepared, actors, web_child, calibration, monkeypatch, lost_at=lost_at) as c:
        refused(c.web.start, c.web)
        assert c.web.used and len(c.requests) == lost_at + 1
        assert m.dispatch.load_web(c.web.directory, c.web.web_pins).phase == (
            "create_intent" if lost_at == 5 else "created" if lost_at == 6 else "attach_intent"
        )
        with pytest.raises(m.UnconfirmedWebExec):
            m.Launch(c.ready)
        assert len(c.requests) == lost_at + 1


@pytest.mark.parametrize(
    "fault",
    [
        "exit",
        "stopped",
        "namespace",
        "start_ticks",
        "parent",
        "web_dir",
        "tip",
        "extra_output",
        "eof",
    ],
)
def test_continuity_failure_closes_web_only_retains_original_exit_handle(
    prepared, actors, web_child, calibration, monkeypatch, fault
):
    with case(prepared, actors, web_child, calibration, monkeypatch) as c:
        c.web.start()
        if fault == "exit":
            web_child.process.stdin.close()
            assert select.select([web_child.fd], [], [], 2)[0]
        elif fault == "stopped":
            signal.pidfd_send_signal(web_child.fd, signal.SIGSTOP)
            deadline = time.monotonic() + 2
            while (
                Path(f"/proc/{web_child.process.pid}/stat")
                .read_text()
                .rpartition(") ")[2]
                .split()[0]
                != "T"
            ):
                assert time.monotonic() < deadline
            # Synthetic mapping otherwise hides state; enforce the same real
            # live-state read used by production namespace.read.
            read = m.namespace.read

            def stopped(pid, container):
                if pid == web_child.process.pid:
                    raise ValueError("PRIVATE stopped actor")
                return read(pid, container)

            monkeypatch.setattr(m.namespace, "read", stopped)
        elif fault in ("namespace", "start_ticks", "parent"):
            current = web_child.actor
            fields = {
                "namespace": {"namespaces": tuple((n, v + 1) for n, v in current.namespaces)},
                "start_ticks": {"start_ticks": current.start_ticks + 1},
                "parent": {"parent": current.parent + 1},
            }[fault]
            actors.lookup[current.host_pid] = replace(current, **fields)
        elif fault == "web_dir":
            old = c.web.directory.with_name("retained_web_original")
            c.web.directory.rename(old)
            c.web.directory.mkdir(mode=0o700)
            for p in old.iterdir():
                new = c.web.directory / p.name
                new.write_bytes(p.read_bytes())
                new.chmod(0o600)
        elif fault == "tip":
            path = c.web.directory / "0002.json"
            value = json.loads(path.read_bytes())
            value["event"]["at"] -= 0.0001
            path.write_bytes(m.binding.encode(value))
        elif fault == "extra_output":
            c.peer.sendall(b"x")
        else:
            c.peer.close()
        refused(c.web.check, c.web)
        assert c.web.fd >= 0 and c.web.channel.closed
        # Replacing the web directory also changes the link count of the
        # fixture Engine socket's shared parent. That stronger original-parent
        # guard correctly poisons the Endpoint too; do not weaken it for tests.
        assert c.ready.client.endpoint.closed is (fault == "web_dir")
        assert not c.ready.processes.exited("native")


@pytest.mark.parametrize(
    "fault", ["still_live", "engine_running", "engine_zero", "engine_wrong_pid"]
)
def test_socket_close_or_engine_flag_alone_never_proves_web_exit(
    prepared, actors, web_child, calibration, monkeypatch, fault
):
    def change(index, value, _state):
        if index == 3:
            if fault == "engine_running":
                value.update(Running=True, ExitCode=None)
            elif fault == "engine_zero":
                value["ExitCode"] = 0
            elif fault == "engine_wrong_pid":
                value["Pid"] += 1

    with case(prepared, actors, web_child, calibration, monkeypatch, change_metadata=change) as c:
        c.web.start()
        c.web.end()
        if fault != "still_live":
            web_child.process.stdin.close()
            assert select.select([web_child.fd], [], [], 2)[0]
        refused(c.web.observe_exit, c.web)
        assert c.web.fd >= 0 and not c.ready.processes.exited("native")


def test_second_start_and_fresh_object_cannot_dispatch_again(
    prepared, actors, web_child, calibration, monkeypatch
):
    with case(prepared, actors, web_child, calibration, monkeypatch) as c:
        c.web.start()
        with pytest.raises(m.UnconfirmedWebExec):
            m.Launch(c.ready)
        assert len(c.requests) == 10
        refused(c.web.start, c.web)
        assert len(c.requests) == 10


@pytest.mark.parametrize(
    "fault",
    [
        "ready_bytes",
        "context_bytes",
        "deadline",
        "clock",
        "request",
        "command",
        "web_pins",
        "handles",
    ],
)
def test_changed_original_ready_or_request_refuses_before_any_web_dispatch(
    prepared, actors, web_child, calibration, monkeypatch, fault
):
    with case(prepared, actors, web_child, calibration, monkeypatch) as c:
        if fault == "ready_bytes":
            c.ready.ready_raw += b" "
        elif fault == "context_bytes":
            c.ready.context_raw += b" "
        elif fault == "deadline":
            c.ready.ready_by += 1
        elif fault == "clock":
            c.ready.clock = replace(c.ready.clock, after_ns=c.ready.clock.after_ns + 1)
        elif fault == "request":
            c.web.request_raw += b" "
        elif fault == "command":
            c.web.command = replace(c.web.command, ready_by=c.web.command.ready_by + 1)
        elif fault == "web_pins":
            c.web.web_pins = replace(c.web.web_pins, ready_sha256="a" * 64)
        else:
            # Change only the captured comparison, never close an unowned fd.
            c.web.handles = dict(c.web.handles, native=-1)
        refused(c.web.start, c.web)
        assert not c.web.used and len(c.requests) == 5


@pytest.mark.parametrize("stage", ["startup", "live", "retained"])
def test_original_deadlines_are_never_renewed(
    prepared, actors, web_child, calibration, ledger, monkeypatch, stage
):
    with case(prepared, actors, web_child, calibration, monkeypatch) as c:
        if stage != "startup":
            c.web.start()
        if stage == "retained":
            continuity.begin.intent(ledger, prepared)
            continuity.begin.m.send_once(c.ready, ledger)
            assert transport.begun(c.operator_peer)["phase"] == "begin"
            c.web.retain(continuity.m.Retained(c.ready))
        end = c.ready.watch_deadline if stage == "retained" else c.ready.ready_by
        with monkeypatch.context() as patch:
            patch.setattr(m.time, "monotonic", lambda: end + 0.001)
            refused(c.web.start if stage == "startup" else c.web.check, c.web)
        assert len(c.requests) == (5 if stage == "startup" else 10)


def test_post_begin_object_cannot_be_substituted_or_used_twice(
    prepared, actors, web_child, calibration, monkeypatch
):
    with case(prepared, actors, web_child, calibration, monkeypatch) as c:
        c.web.start()
        refused(lambda: c.web.retain(SimpleNamespace(ready=c.ready)), c.web)
        assert c.web.channel.closed and c.web.fd >= 0
        assert not c.ready.client.attachment.begun


def test_metadata_dictionary_is_not_an_authenticated_ready():
    with pytest.raises(m.UnconfirmedWebExec):
        m.Launch({"schema": 1, "kind": "finite-recording-web-startup"})
