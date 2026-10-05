"""Real Unix transport, original intents and owned pidfds; synthetic Engine/NS.

No Docker, scanner, image/runtime authentication or real daemon health is claimed.
The responding probe is an owned harmless process and the reply is a fixture.
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

from . import test_supplemental_recording_retained as continuity

ready_tests = continuity.begin.ready_tests
engine_tests = ready_tests.joined.engine
transport = engine_tests.attached
NAME = "supplemental_recording_probe_exec"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(continuity.m.__file__).with_name(NAME + ".py")
)
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
    continuity.layout,
    continuity.tree,
    continuity.routing,
    continuity.projection,
    continuity.binding,
    continuity.directory,
    continuity.prepared,
    continuity.family,
    continuity.actors,
    continuity.calibration,
    continuity.ledger,
)
PROBE_ID = "9" * 64


@pytest.fixture
def probe_child(actors):
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
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
        child.stdin.close()
        child.wait(timeout=3)
        child.stdout.close()
        os.close(fd)


def metadata(command, *, pid=0, running=False, code=None):
    value = engine_tests.intents.execution.metadata()
    argv = command.argv()
    value.update(ID=PROBE_ID, Running=running, Pid=pid, ExitCode=code)
    value["ProcessConfig"].update(entrypoint=argv[0], arguments=list(argv[1:]))
    return value


def report(request, *, healthy=True, recording=False):
    now = time.monotonic()
    return {
        "schema": 1,
        "kind": "finite-recording-cached-probe-result",
        "request_sha256": m.hashlib.sha256(m.binding.encode(request)).hexdigest(),
        "observed_after": now,
        "observed_at": now,
        "body": {
            "profile_sha256": request["context"]["profile"],
            "healthy": healthy,
            "recording": recording,
            "supplemental_advertised": True,
            "peer_pid": request["native"]["pid"],
            "peer_start_ticks": str(request["native"]["start_ticks"]),
        },
    }


@contextmanager
def case(
    prepared,
    actors,
    probe_child,
    calibration,
    monkeypatch,
    *,
    ledger=None,
    change_reply=None,
    change_metadata=None,
    healthy=True,
    recording=False,
    finish_child=True,
    probe_id=PROBE_ID,
    after_request=None,
    lost_at=None,
):
    peers, threads, errors = [], [], []
    state = SimpleNamespace(request=None, sample=None, ready=None, probe=probe_child)

    def operator(peer, _request):
        peers.append(peer.dup())
        peer.sendall(
            transport.stream.UPGRADE
            + transport.stream.segment(transport.stream.app(ready_tests.envelope(prepared, actors)))
        )

    def create(peer, request):
        assert request[2] == state.sample.command.create_body()
        peer.sendall(engine_tests.reply({"Id": probe_id}, 201))

    def inspect(index, *, running=False, ended=False):
        def response(peer, _request):
            value = metadata(
                state.sample.command,
                pid=probe_child.process.pid if running or ended else 0,
                running=running,
                code=0 if ended else None,
            )
            if change_metadata is not None:
                change_metadata(index, value, state)
            peer.sendall(engine_tests.reply(value))

        return response

    def probe(peer, _request):
        retained_peer = peer.dup()
        peers.append(retained_peer)
        peer.sendall(transport.stream.UPGRADE)

        def respond():
            try:
                # A failure before request is allowed to close the consumed attachment.
                first = retained_peer.recv(4)
                if not first:
                    return
                raw = first + transport.exact(retained_peer, 4 - len(first))
                size = transport.stream.m.wire.HEADER.unpack(raw)[0]
                state.request = json.loads(transport.exact(retained_peer, size))
                value = report(state.request, healthy=healthy, recording=recording)
                if change_reply is not None:
                    change_reply(value, state)
                if after_request is not None:
                    after_request(state)
                if finish_child:
                    probe_child.process.stdin.close()
                    assert select.select([probe_child.fd], [], [], 2)[0]
                retained_peer.sendall(transport.stream.segment(transport.stream.app(value)))
            except (BrokenPipeError, ConnectionResetError):
                pass  # Client refusal consumes the socket, not the owned original actors.
            except BaseException as error:
                errors.append(error)
            finally:
                retained_peer.close()

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
        probe,
        inspect(1, running=True),
        inspect(2, running=True),
        inspect(3, ended=True),
    ]
    if lost_at is not None:
        handlers[lost_at] = b""
    try:
        with engine_tests.engine(prepared, monkeypatch, handlers) as (client, requests):
            client.create()
            client.attach(
                finish_by=prepared.pins.command.ready_by
                + prepared.pins.host.projection.native.contract.maximum_recording_seconds
                + 3
            )
            ready = state.ready = ready_tests.capture(client, calibration)
            try:
                if ledger is not None:
                    continuity.begin.intent(ledger, prepared)
                    continuity.begin.m.send_once(ready, ledger)
                    assert transport.begun(peers[0])["phase"] == "begin"
                    original = continuity.m.Retained(ready)
                else:
                    original = ready
                state.sample = m.Sample(original)
                state.requests, state.original = requests, original
                yield state
            finally:
                if state.sample is not None:
                    state.sample.close()
                ready.close()
    finally:
        for peer in peers:
            with suppress(OSError):
                peer.close()
        for thread in threads:
            thread.join(4)
            assert not thread.is_alive()
        assert not errors, errors


def denied(sample):
    with pytest.raises(m.UnconfirmedProbeExec) as error:
        sample.read()
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)
    assert sample.failed
    count = sample.execution_id
    with pytest.raises(m.UnconfirmedProbeExec):
        sample.read()
    assert sample.execution_id == count


@pytest.mark.parametrize("healthy", [False, True])
@pytest.mark.parametrize("recording", [False, True])
@pytest.mark.parametrize("begun", [False, True])
def test_actual_probe_pidfd_and_two_engine_reads_join_only_original_request(
    prepared, actors, probe_child, calibration, ledger, monkeypatch, healthy, recording, begun
):
    with case(
        prepared,
        actors,
        probe_child,
        calibration,
        monkeypatch,
        ledger=ledger if begun else None,
        healthy=healthy,
        recording=recording,
    ) as c:
        original = c.ready.ready_by, c.ready.watch_deadline, c.ready.clock, c.ready.context_raw
        result = c.sample.read()
        assert result == m.observer.NativeState(prepared.pins.generation, healthy, recording)
        assert c.sample.actor == probe_child.actor and c.sample.fd >= 0
        assert select.select([c.sample.fd], [], [], 0)[0]
        assert c.sample.channel.finished and c.sample.channel.closed
        assert not c.ready.processes.exited("init") and not c.ready.processes.exited("native")
        assert c.ready.client.attachment.begun is begun and c.ready.client.attachment.reads == 1
        assert len(c.requests) == 11 and c.sample.execution_id == PROBE_ID
        assert c.request == json.loads(c.sample.request_raw)
        assert c.request["context"] == json.loads(c.ready.context_raw)
        assert c.request["watchdog"]["deadline"] == c.ready.watch_deadline
        assert original == (
            c.ready.ready_by,
            c.ready.watch_deadline,
            c.ready.clock,
            c.ready.context_raw,
        )
        assert not select.select([c.ready.client.attachment.channel], [], [], 0)[0]
        c.sample.close()
        assert c.sample.fd == -1 and not c.ready.client.endpoint.closed
        assert not any(
            hasattr(c.sample, method) for method in ("begin", "stop", "restore", "signal")
        )


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "extra",
        "kind",
        "hash",
        "early",
        "future",
        "reversed",
        "nan",
        "profile",
        "native_pid",
        "native_ticks",
        "advertised",
        "healthy_int",
        "recording_null",
        "body_extra",
    ],
)
def test_reply_is_closed_fresh_and_bound_not_healthy_by_exit_zero(
    prepared, actors, probe_child, calibration, monkeypatch, fault
):
    def change(value, _state):
        if fault == "schema":
            value["schema"] = True
        elif fault == "extra":
            value["PRIVATE"] = 1
        elif fault == "kind":
            value["kind"] = "PRIVATE"
        elif fault == "hash":
            value["request_sha256"] = "0" * 64
        elif fault == "early":
            value["observed_after"] -= 60
        elif fault == "future":
            value["observed_at"] += 60
        elif fault == "reversed":
            value["observed_at"] -= 1
        elif fault == "nan":
            value["observed_after"] = None
        elif fault == "profile":
            value["body"]["profile_sha256"] = "0" * 64
        elif fault == "native_pid":
            value["body"]["peer_pid"] = probe_child.actor.local_pid
        elif fault == "native_ticks":
            value["body"]["peer_start_ticks"] = 1
        elif fault == "advertised":
            value["body"]["supplemental_advertised"] = False
        elif fault == "healthy_int":
            value["body"]["healthy"] = 1
        elif fault == "recording_null":
            value["body"]["recording"] = None
        else:
            value["body"]["PRIVATE"] = True

    with case(prepared, actors, probe_child, calibration, monkeypatch, change_reply=change) as c:
        denied(c.sample)
        assert len(c.requests) == 10 and c.sample.channel.closed
        assert c.sample.fd >= 0 and not c.ready.client.endpoint.closed


@pytest.mark.parametrize("stage", [0, 1, 2, 3])
@pytest.mark.parametrize("fault", ["wrong_id", "argv", "container", "phase", "pid", "extra"])
def test_each_actual_engine_status_is_required_and_never_retried(
    prepared, actors, probe_child, calibration, monkeypatch, stage, fault
):
    def change(index, value, _state):
        if index != stage:
            return
        if fault == "wrong_id":
            value["ID"] = "0" * 64
        elif fault == "argv":
            value["ProcessConfig"]["arguments"].append("PRIVATE")
        elif fault == "container":
            value["ContainerID"] = "0" * 64
        elif fault == "phase":
            value["Running"] = not value["Running"]
        elif fault == "pid":
            value["Pid"] = prepared.pins.init.pid
        else:
            value["PRIVATE"] = True

    with case(prepared, actors, probe_child, calibration, monkeypatch, change_metadata=change) as c:
        denied(c.sample)
        assert len(c.requests) == (7, 9, 10, 11)[stage]
        assert c.ready.client.attachment.begun is False


@pytest.mark.parametrize("fault", ["namespace", "local_pid", "parent", "start_ticks"])
def test_original_probe_process_mapping_is_retained_across_engine_reads(
    prepared, actors, probe_child, calibration, monkeypatch, fault
):
    def change(index, _value, _state):
        if index != 2:
            return
        actor = probe_child.actor
        if fault == "namespace":
            actor = replace(actor, namespaces=(*actor.namespaces[:4], (4, 999)))
        elif fault == "local_pid":
            actor = replace(actor, local_pid=43)
        elif fault == "parent":
            actor = replace(actor, parent=actors.values[1].host_pid)
        else:
            actor = replace(actor, start_ticks=actor.start_ticks + 1)
        actors.lookup[actor.host_pid] = actor

    with case(prepared, actors, probe_child, calibration, monkeypatch, change_metadata=change) as c:
        denied(c.sample)
        assert len(c.requests) == 10 and c.request is None
        assert c.sample.fd >= 0


@pytest.mark.parametrize("begun", [False, True])
@pytest.mark.parametrize("role", ["native", "watchdog"])
def test_exit_of_original_actor_is_not_live_health_even_when_reply_arrives(
    prepared, actors, probe_child, calibration, ledger, monkeypatch, begun, role
):
    def after(state):
        fd = state.ready.processes.handles[role]
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        assert select.select([fd], [], [], 2)[0]

    with case(
        prepared,
        actors,
        probe_child,
        calibration,
        monkeypatch,
        ledger=ledger if begun else None,
        after_request=after,
    ) as c:
        denied(c.sample)
        assert len(c.requests) == 10 and c.sample.fd >= 0
        assert c.ready.processes.exited(role) and not c.ready.processes.exited("init")


@pytest.mark.parametrize(
    "fault", ["ready_raw", "context_raw", "clock", "watch_deadline", "expired"]
)
def test_original_context_and_deadlines_cannot_be_refreshed(
    prepared, actors, probe_child, calibration, monkeypatch, fault
):
    with case(prepared, actors, probe_child, calibration, monkeypatch) as c:
        if fault in ("ready_raw", "context_raw"):
            setattr(c.ready, fault, b"PRIVATE")
        elif fault == "clock":
            c.ready.clock = m.retained.received.clock.read()
        elif fault == "watch_deadline":
            c.ready.watch_deadline += 1
        else:
            c.sample.probe_by = time.monotonic() - 1
        denied(c.sample)
        assert len(c.requests) == 5 and c.sample.execution_id is None


def test_existing_operator_id_is_not_a_new_passive_probe(
    prepared, actors, probe_child, calibration, monkeypatch
):
    with case(
        prepared, actors, probe_child, calibration, monkeypatch, probe_id=transport.EXEC
    ) as c:
        denied(c.sample)
        assert len(c.requests) == 6


def test_eof_and_claimed_engine_exit_are_not_actual_probe_exit(
    prepared, actors, probe_child, calibration, monkeypatch
):
    with case(prepared, actors, probe_child, calibration, monkeypatch, finish_child=False) as c:
        denied(c.sample)
        assert len(c.requests) == 10
        assert not select.select([c.sample.fd], [], [], 0)[0]
        assert c.sample.channel.finished and not c.ready.client.endpoint.closed


@pytest.mark.parametrize("lost_at", [5, 6, 7, 8, 9, 10])
def test_lost_probe_engine_return_never_recreates_or_takes_original_endpoint(
    prepared, actors, probe_child, calibration, monkeypatch, lost_at
):
    with case(prepared, actors, probe_child, calibration, monkeypatch, lost_at=lost_at) as c:
        denied(c.sample)
        assert len(c.requests) == lost_at + 1
        assert not c.ready.client.closed and not c.ready.client.endpoint.closed
        assert not c.ready.processes.exited("native")


@pytest.mark.parametrize("code", [1, 70, None])
def test_real_probe_exit_with_unconfirmed_engine_code_is_not_health(
    prepared, actors, probe_child, calibration, monkeypatch, code
):
    def change(index, value, _state):
        if index == 3:
            value["ExitCode"] = code

    with case(prepared, actors, probe_child, calibration, monkeypatch, change_metadata=change) as c:
        denied(c.sample)
        assert len(c.requests) == 11 and select.select([c.sample.fd], [], [], 0)[0]


def test_success_is_single_use_and_close_preserves_original_readiness(
    prepared, actors, probe_child, calibration, monkeypatch
):
    with case(prepared, actors, probe_child, calibration, monkeypatch) as c:
        assert c.sample.read().healthy is True
        denied(c.sample)
        assert len(c.requests) == 11
        c.sample.close()
        c.ready.check_before_begin()
        assert not c.ready.client.endpoint.closed and not c.ready.processes.closed


def test_explicit_prepare_binds_once_but_does_not_request_health_or_renew_deadline(
    prepared, actors, probe_child, calibration, monkeypatch
):
    with case(prepared, actors, probe_child, calibration, monkeypatch) as c:
        original = c.sample.probe_by, c.sample.command, c.ready.received_at
        assert c.sample.prepare() is None
        assert c.sample.prepared and c.sample.preparation_attempted and not c.sample.used
        assert c.request is None and len(c.requests) == 10
        assert not c.sample.channel.begun and c.sample.channel.reads == 0
        assert not select.select([c.sample.fd], [], [], 0)[0]
        assert c.sample.actor == probe_child.actor and not c.ready.client.attachment.begun
        assert c.sample.read() == m.observer.NativeState(prepared.pins.generation, True, False)
        assert c.request == json.loads(c.sample.request_raw) and len(c.requests) == 11
        assert original == (c.sample.probe_by, c.sample.command, c.ready.received_at)
        denied(c.sample)


@pytest.mark.parametrize(
    "fault", ["second_prepare", "close", "expired", "extended", "command", "actor_exit"]
)
def test_prepared_probe_cannot_replay_renew_or_ignore_lost_original_actor(
    prepared, actors, probe_child, calibration, monkeypatch, fault
):
    with case(prepared, actors, probe_child, calibration, monkeypatch) as c:
        c.sample.prepare()
        original = c.sample.probe_by
        if fault == "second_prepare":
            with pytest.raises(m.UnconfirmedProbeExec):
                c.sample.prepare()
        elif fault == "close":
            c.sample.close()
        elif fault == "expired":
            c.sample.probe_by = time.monotonic() - 1
        elif fault == "extended":
            c.sample.probe_by = original + 0.1
        elif fault == "command":
            c.sample.command = replace(c.sample.command, probe_by=original + 0.1)
        else:
            probe_child.process.stdin.close()
            assert select.select([probe_child.fd], [], [], 2)[0]
        denied(c.sample)
        assert c.request is None and len(c.requests) == 10
        assert not c.ready.client.endpoint.closed and not c.ready.client.attachment.begun


@pytest.mark.parametrize("lost_at", [5, 6, 7, 8, 9])
def test_lost_preparation_result_is_consumed_without_sending_request(
    prepared, actors, probe_child, calibration, monkeypatch, lost_at
):
    with case(prepared, actors, probe_child, calibration, monkeypatch, lost_at=lost_at) as c:
        with pytest.raises(m.UnconfirmedProbeExec):
            c.sample.prepare()
        assert c.sample.preparation_attempted and c.sample.failed and not c.sample.prepared
        denied(c.sample)
        assert c.request is None and len(c.requests) == lost_at + 1
        assert not c.ready.client.closed


def test_retained_probe_after_ready_expiry_keeps_original_watch_deadline(
    prepared, actors, probe_child, calibration, ledger, monkeypatch
):
    with case(prepared, actors, probe_child, calibration, monkeypatch, ledger=ledger) as c:
        c.sample.close()
        now = time.monotonic
        delta = c.ready.ready_by + 0.1 - now()
        # Explicit synthetic elapsed clock; no real sleep/suspend/namespace claim.
        monkeypatch.setattr(time, "monotonic", lambda: now() + delta)
        original = c.ready.ready_by, c.ready.watch_deadline, c.original.state
        c.sample = m.Sample(c.original)
        assert c.sample.read().healthy is True
        assert c.sample.probe_by > c.ready.ready_by
        assert c.sample.probe_by <= c.ready.watch_deadline
        assert original == (c.ready.ready_by, c.ready.watch_deadline, c.original.state)
        assert c.request["watchdog"]["deadline"] == c.ready.watch_deadline


@pytest.mark.parametrize("value", [None, {}, {"phase": "ready"}, "PRIVATE"])
def test_caller_reports_are_not_a_ready_or_retained_witness(value):
    with pytest.raises(m.UnconfirmedProbeExec) as error:
        m.Sample(value)
    assert str(error.value) == m.MESSAGE
