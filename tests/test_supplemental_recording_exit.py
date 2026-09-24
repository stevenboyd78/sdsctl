"""Separate returned report, clean EOF, owned pidfds and synthetic Engine inspect."""

import importlib.util
import socket
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from . import test_supplemental_recording_reconcile as custody_tests  # noqa: F401
from . import test_supplemental_recording_relay as relay_tests

NAME = "supplemental_recording_exit"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(relay_tests.m.__file__).with_name(NAME + ".py")
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
    files,
) = (
    relay_tests.layout,
    relay_tests.tree,
    relay_tests.routing,
    relay_tests.projection,
    relay_tests.binding,
    relay_tests.directory,
    relay_tests.prepared,
    relay_tests.family,
    relay_tests.actors,
    relay_tests.calibration,
    relay_tests.ledger,
    relay_tests.files,
)
ready_tests = relay_tests.begin.ready_tests
engine_tests = ready_tests.joined.engine


@contextmanager
def finished(prepared, actors, calibration, ledger, files, monkeypatch, *, response=None):
    peers = []
    final = ready_tests.joined.live(prepared, actors)
    final.update(Running=False, ExitCode=0)
    handlers = [engine_tests.reply(ready_tests.joined.live(prepared, actors))] * 2
    handlers.append(engine_tests.reply(final) if response is None else response)
    with ready_tests.joined.ready(
        prepared,
        actors,
        monkeypatch,
        consume=False,
        message=lambda: ready_tests.envelope(prepared, actors),
        finish_extra=prepared.pins.host.projection.native.contract.maximum_recording_seconds + 3,
        tap=peers,
        responses=handlers,
    ) as (client, requests):
        ready = ready_tests.capture(client, calibration)
        try:
            relay_tests.begin.intent(ledger, prepared)
            relay = m.returned.Relay(ledger, ready)
            engine_tests.attached.begun(peers[0])
            case = relay_tests.SimpleNamespace(
                relay=relay, ready=ready, peer=peers[0], requests=requests
            )
            expected, _plan, start = relay_tests.startup(case, files)
            relay_tests.send(case, start)
            relay.started()
            relay_tests.send(case, relay_tests.finish(case, files, expected))
            relay.completed()
            yield case
        finally:
            ready.close()


def envelope(case):
    value = deepcopy(case.relay.envelope)
    value["phase"] = "exited"
    native, watch = value["native"], value["watchdog"]
    value["body"] = {
        "pid": native["pid"],
        "start_ticks": native["start_ticks"],
        "returncode": 0,
        "reaped_at": time.monotonic(),
        "watchdog": {
            "native_pid": native["pid"],
            "native_start_ticks": native["start_ticks"],
            "watchdog_pid": watch["pid"],
            "watchdog_start_ticks": watch["start_ticks"],
            "deadline": watch["deadline"],
            "grace": watch["grace"],
            "returncode": 0,
        },
    }
    return value


def end_family(family):
    family.process.stdin.close()
    family.process.wait(timeout=3)


def delivery(case, family):
    end_family(family)
    relay_tests.send(case, envelope(case))
    case.peer.shutdown(socket.SHUT_WR)


def refused(case):
    with pytest.raises(m.UnconfirmedExit) as error:
        m.collect(case.relay)
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)
    assert case.relay.phase == "unconfirmed" and case.ready.failed
    assert case.ready.client.closed and not case.ready.processes.closed


def test_reaped_return_eof_pidfds_and_exact_engine_exit_all_required(
    prepared, actors, calibration, ledger, files, family, monkeypatch
):
    with finished(prepared, actors, calibration, ledger, files, monkeypatch) as case:
        delivery(case, family)
        result = m.collect(case.relay)
        assert (result.guardian_pid, result.native_pid, result.watchdog_pid) == tuple(
            actor.host_pid for actor in actors.values[1:]
        )
        assert result.completion_sha256 == ledger.state.acknowledgment.completion_sha256
        assert case.relay.phase == "exited" and case.ready.client.attachment.finished
        assert len(case.requests) == 6
        assert case.requests[-1][0] == f"GET /v1.47/exec/{engine_tests.attached.EXEC}/json HTTP/1.1"
        assert case.requests[-1][2] is None
        assert not case.ready.processes.exited("init") and not hasattr(result, "restored")
        assert case.ready.client.closed and not case.ready.processes.closed
        refused(case)  # No second inspect or attachment.
        assert len(case.requests) == 6


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "context",
        "guardian",
        "native",
        "watchdog",
        "extra",
        "body_extra",
        "pid",
        "ticks",
        "code",
        "code_bool",
        "reaped_past",
        "reaped_future",
        "watch_native",
        "watch_ticks",
        "watch_pid",
        "watch_deadline",
        "watch_grace",
        "watch_code",
        "watch_bool",
        "watch_extra",
    ],
)
def test_unqualified_reap_report_never_reaches_engine_or_replaces_handles(
    prepared, actors, calibration, ledger, files, family, monkeypatch, fault
):
    with finished(prepared, actors, calibration, ledger, files, monkeypatch) as case:
        end_family(family)
        value = envelope(case)
        body, watch = value["body"], value["body"]["watchdog"]
        if fault == "schema":
            value["schema"] = True
        elif fault == "context":
            value["context"]["generation"] = "e" * 64
        elif fault in ("guardian", "native", "watchdog"):
            value[fault]["start_ticks"] += 1
        elif fault == "extra":
            value["PRIVATE"] = 0
        elif fault == "body_extra":
            body["PRIVATE"] = 0
        elif fault == "pid":
            body["pid"] += 1
        elif fault == "ticks":
            body["start_ticks"] += 1
        elif fault == "code":
            body["returncode"] = 70
        elif fault == "code_bool":
            body["returncode"] = False
        elif fault == "reaped_past":
            body["reaped_at"] = case.relay.completed_at - 1
        elif fault == "reaped_future":
            body["reaped_at"] += 60
        elif fault == "watch_native":
            watch["native_pid"] += 1
        elif fault == "watch_ticks":
            watch["native_start_ticks"] += 1
        elif fault == "watch_pid":
            watch["watchdog_pid"] += 1
        elif fault == "watch_deadline":
            watch["deadline"] += 1
        elif fault == "watch_grace":
            watch["grace"] += 1
        elif fault == "watch_code":
            watch["returncode"] = 10
        elif fault == "watch_bool":
            watch["returncode"] = False
        else:
            watch["PRIVATE"] = 0
        relay_tests.send(case, value)
        case.peer.shutdown(socket.SHUT_WR)
        refused(case)
        assert len(case.requests) == 5 and ledger.state.acknowledgment is not None
        assert case.ready.processes.exited("native") and not case.ready.processes.exited("init")


@pytest.mark.parametrize("fault", ["alive", "missing", "extra_frame", "truncated", "init_exited"])
def test_eof_or_report_does_not_substitute_for_original_exact_exits(
    prepared, actors, calibration, ledger, files, family, monkeypatch, fault
):
    with finished(prepared, actors, calibration, ledger, files, monkeypatch) as case:
        if fault != "alive":
            end_family(family)
        if fault != "missing":
            relay_tests.send(case, envelope(case))
        if fault == "extra_frame":
            relay_tests.send(case, {"phase": "exited"})
        elif fault == "truncated":
            case.peer.sendall(b"\x01\x00")
        elif fault == "init_exited":
            prepared.process.stdin.close()
            prepared.process.wait(timeout=3)
        case.peer.shutdown(socket.SHUT_WR)
        refused(case)
        assert len(case.requests) == 5
        assert case.ready.processes.exited("init") is (fault == "init_exited")
        if fault == "alive":
            assert not case.ready.processes.exited("native")


@pytest.mark.parametrize(
    "fault",
    [
        "lost",
        "running",
        "pid",
        "code",
        "argv",
        "execution",
        "container",
        "clock_after_inspect",
        "dispatch_after_inspect",
        "ledger_after_inspect",
    ],
)
def test_successful_process_exit_does_not_replace_final_independent_inspection(
    prepared, actors, calibration, ledger, files, family, monkeypatch, fault
):
    observed = ready_tests.joined.live(prepared, actors)
    observed.update(Running=False, ExitCode=0)
    if fault == "running":
        observed.update(Running=True, ExitCode=None)
    elif fault == "pid":
        observed["Pid"] = actors.values[0].host_pid
    elif fault == "code":
        observed["ExitCode"] = 70
    elif fault == "argv":
        observed["ProcessConfig"]["arguments"].append("PRIVATE")
    elif fault == "execution":
        observed["ID"] = "9" * 64
        assert observed["ID"] != engine_tests.attached.EXEC
    elif fault == "container":
        observed["ContainerID"] = "e" * 64

    def response(peer, _request):
        if fault == "lost":
            return
        if fault == "clock_after_inspect":
            calibration.offset += 10
        if fault == "dispatch_after_inspect":
            (prepared.directory / "0002.json").write_bytes(b"PRIVATE")
        if fault == "ledger_after_inspect":
            (ledger.directory / "0003.json").write_bytes(b"PRIVATE")
        peer.sendall(engine_tests.reply(observed))

    with finished(
        prepared, actors, calibration, ledger, files, monkeypatch, response=response
    ) as case:
        delivery(case, family)
        refused(case)
        assert len(case.requests) == 6 and ledger.state.acknowledgment is not None
        assert all(case.ready.processes.exited(role) for role in ("guardian", "native", "watchdog"))
        assert not case.ready.processes.exited("init")


def test_without_completion_no_exit_inspection_or_success(
    prepared, actors, calibration, ledger, monkeypatch
):
    with relay_tests.joined(prepared, actors, calibration, ledger, monkeypatch) as case:
        refused(case)
        assert len(case.requests) == 5 and ledger.state.expected is None
