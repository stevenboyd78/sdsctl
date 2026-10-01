"""Real private sockets with synthetic Engine replies, not launch authority."""

import select
import socket
import time
from dataclasses import replace
from threading import Event, Thread

import pytest

from . import test_supplemental_recording_attachment as transport
from . import test_supplemental_recording_execution as metadata

a, e, framing = transport.m, metadata.m, transport.stream
COMMAND = e.WebCommand("/data/finite-case/launch.json", "a" * 64, "b" * 64, 100.25, "c" * 64)
REQUEST = {"schema": 1, "kind": "finite-recording-web-startup", "context": {}}
RESULT = {"schema": 1, "kind": "finite-recording-web-listening", "request_sha256": "c" * 64}


def web_metadata():
    value = metadata.metadata()
    value["ProcessConfig"] |= {
        "entrypoint": COMMAND.argv()[0],
        "arguments": list(COMMAND.argv()[1:]),
    }
    return value


def inspect(value, command=COMMAND):
    return e.inspect_web(
        value, execution_id=metadata.EXEC, container_id=metadata.CONTAINER, command=command
    )


def test_web_description_keeps_fixed_auth_listener_and_original_bound():
    args = COMMAND.argv()
    assert args[:4] == (e.PYTHON, "-I", "-B", e.WEB_ENTRY)
    assert args[4:-2] == metadata.COMMAND.argv()[4:]
    assert args[-2:] == ("--request-sha256", "c" * 64)
    assert COMMAND.create_body() == metadata.COMMAND.create_body() | {"Cmd": list(args)}
    body = COMMAND.create_body()
    body["Cmd"].append("--arbitrary")
    assert COMMAND.create_body()["Cmd"] == list(args)
    assert set(COMMAND.__dataclass_fields__) == {
        "plan",
        "plan_sha256",
        "source_sha256",
        "ready_by",
        "request_sha256",
    }


@pytest.mark.parametrize("function", [e.inspect, e.inspect_probe])
def test_web_description_cannot_become_operator_or_passive_probe(function):
    with pytest.raises(e.UnconfirmedExecution):
        function(
            web_metadata(),
            execution_id=metadata.EXEC,
            container_id=metadata.CONTAINER,
            command=COMMAND,
        )


@pytest.mark.parametrize(
    "command", [metadata.COMMAND, e.ProbeCommand("/data/x/launch.json", "a" * 64, "b" * 64, 100)]
)
def test_other_descriptions_cannot_become_web(command):
    with pytest.raises(e.UnconfirmedExecution):
        inspect(web_metadata(), command)


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan", "/data/launch.json"),
        ("plan", "/data/x/../x/launch.json"),
        ("plan", "relative"),
        ("plan_sha256", "PRIVATE"),
        ("source_sha256", True),
        ("ready_by", True),
        ("ready_by", float("nan")),
        ("ready_by", -1),
        ("request_sha256", "A" * 64),
        ("request_sha256", True),
        ("request_sha256", "c" * 63),
    ],
)
def test_web_description_keeps_all_original_pin_validation(field, value):
    with pytest.raises(e.UnconfirmedExecution):
        replace(COMMAND, **{field: value}).create_body()


@pytest.mark.parametrize(
    "running,pid,code,phase",
    [
        (False, 0, None, "created"),
        (True, 0, None, "starting"),
        (True, 1234, None, "running"),
        (False, 1234, 0, "not_running"),
        (False, 0, 70, "not_running"),
    ],
)
def test_web_inspection_is_not_listening_health_or_exit_proof(running, pid, code, phase):
    value = web_metadata() | {"Running": running, "Pid": pid, "ExitCode": code}
    result = inspect(value)
    assert result == e.State(phase, pid, code)
    assert not hasattr(result, "listening") and not hasattr(result, "healthy")


@pytest.mark.parametrize("fault", ["request_pin", "ready_by", "entrypoint", "user", "tty", "extra"])
def test_web_metadata_must_match_the_exact_original_exec(fault):
    value = web_metadata()
    if fault in ("request_pin", "ready_by"):
        value["ProcessConfig"]["arguments"][-1 if fault == "request_pin" else -3] = "other"
    elif fault == "entrypoint":
        value["ProcessConfig"]["entrypoint"] = "/bin/sh"
    elif fault == "user":
        value["ProcessConfig"]["user"] = "root"
    elif fault == "tty":
        value["ProcessConfig"]["tty"] = True
    else:
        value["extra"] = "PRIVATE"
    with pytest.raises(e.UnconfirmedExecution):
        inspect(value)


class Fixture:
    def __init__(self, server, budget=3):
        self.client, self.peer = socket.socketpair()
        self.peer.settimeout(5)
        self.errors = []
        now = time.monotonic()
        self.attachment = a.WebAttachment(
            self.client, metadata.EXEC, ready_by=now + budget, finish_by=now + budget + 2
        )

        def serve():
            try:
                server(self.peer)
            except BaseException as error:
                self.errors.append(error)
            finally:
                self.peer.close()

        self.thread = Thread(target=serve)
        self.thread.start()

    def __enter__(self):
        return self.attachment

    def __exit__(self, *args):
        self.attachment.close()
        self.thread.join(6)
        assert not self.thread.is_alive() and not self.errors, self.errors
        assert self.client.fileno() == self.peer.fileno() == -1


@pytest.mark.parametrize("chunk", [1, 7, 65536])
def test_one_fragmented_request_reply_then_held_quiet_until_owner_closes(chunk):
    def server(peer):
        transport.request(peer)
        peer.sendall(framing.UPGRADE)
        assert transport.begun(peer) == REQUEST
        raw = framing.segment(framing.app(RESULT))
        for offset in range(0, len(raw), chunk):
            peer.sendall(raw[offset : offset + chunk])
        assert peer.recv(1) == b""

    with Fixture(server) as attached:
        attached.start(deadline=attached.ready_by)
        attached.send_request(REQUEST, deadline=attached.ready_by)
        assert attached.receive(deadline=attached.ready_by) == RESULT
        attached.check_quiet(deadline=attached.finish_by)
        assert not attached.finished and not attached.closed
        assert not hasattr(attached, "healthy") and not hasattr(attached, "exit_code")


@pytest.mark.parametrize(
    "fault", ["early", "wrong_kind", "extra_frame", "partial", "stderr", "empty"]
)
def test_invalid_startup_reply_cannot_establish_listening(fault):
    def server(peer):
        transport.request(peer)
        if fault == "early":
            peer.sendall(framing.UPGRADE + framing.segment(framing.app(RESULT)))
            return
        peer.sendall(framing.UPGRADE)
        assert transport.begun(peer) == REQUEST
        payload = {
            "wrong_kind": framing.segment(
                framing.app({"kind": "finite-recording-cached-probe-result"})
            ),
            "extra_frame": framing.segment(framing.app(RESULT) * 2),
            "partial": framing.segment(framing.app(RESULT)[:-1]),
            "stderr": framing.segment(b"PRIVATE", channel=2),
            "empty": b"",
        }[fault]
        peer.sendall(payload)

    with Fixture(server) as attached:

        def attempt():
            attached.start(deadline=attached.ready_by)
            attached.send_request(REQUEST, deadline=attached.ready_by)
            attached.receive(deadline=attached.ready_by)

        transport.deny(attempt, attached)


@pytest.mark.parametrize(
    "fault", ["extra", "partial_tail", "eof", "late", "resend", "receive_twice", "begin", "finish"]
)
def test_no_second_action_or_unexpected_later_output_is_accepted(fault):
    report_received = Event()

    def server(peer):
        transport.request(peer)
        peer.sendall(framing.UPGRADE)
        assert transport.begun(peer) == REQUEST
        peer.sendall(framing.segment(framing.app(RESULT)))
        assert report_received.wait(2)
        if fault in ("extra", "partial_tail"):
            peer.sendall(framing.segment(framing.app(RESULT)) if fault == "extra" else b"\1")
        elif fault != "eof":
            assert peer.recv(1) == b""

    with Fixture(server) as attached:
        attached.start(deadline=attached.ready_by)
        attached.send_request(REQUEST, deadline=attached.ready_by)
        assert attached.receive(deadline=attached.ready_by) == RESULT
        report_received.set()
        if fault in ("extra", "partial_tail", "eof"):
            assert select.select([attached.channel], [], [], 2)[0]

        def action():
            if fault in ("extra", "partial_tail", "eof"):
                attached.check_quiet(deadline=attached.finish_by)
            elif fault == "late":
                attached.check_quiet(deadline=attached.finish_by + 0.1)
            elif fault == "resend":
                attached.send_request(REQUEST, deadline=attached.ready_by)
            elif fault == "receive_twice":
                attached.receive(deadline=attached.ready_by)
            elif fault == "begin":
                attached.send_begin({"phase": "begin"}, deadline=attached.ready_by)
            else:
                attached.finish(deadline=attached.finish_by)

        transport.deny(action, attached)
