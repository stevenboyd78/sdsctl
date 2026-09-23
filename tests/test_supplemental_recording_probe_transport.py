"""Actual private sockets, synthetic Engine replies; no host/health authentication."""

import copy
import select
import socket
import time
from dataclasses import replace

import pytest

from . import test_supplemental_recording_attachment as transport
from . import test_supplemental_recording_execution as metadata

a, e, framing = transport.m, metadata.m, transport.stream
COMMAND = e.ProbeCommand("/data/finite-case/launch.json", "a" * 64, "b" * 64, 100.25)
REQUEST = {"schema": 1, "kind": "finite-recording-cached-probe", "context": {}}
RESULT = {"schema": 1, "kind": "finite-recording-cached-probe-result", "body": {}}


def probe_metadata():
    value = metadata.metadata()
    value["ProcessConfig"] |= {
        "entrypoint": COMMAND.argv()[0],
        "arguments": list(COMMAND.argv()[1:]),
    }
    return value


def inspect(value, command=COMMAND):
    return e.inspect_probe(
        value, execution_id=metadata.EXEC, container_id=metadata.CONTAINER, command=command
    )


def test_probe_description_has_no_operator_or_general_command_authority():
    argv = COMMAND.argv()
    assert argv[:4] == (e.PYTHON, "-I", "-B", e.PROBE_ENTRY)
    assert argv[4:-2] == metadata.COMMAND.argv()[4:-2]
    assert argv[-2:] == ("--probe-by", "100.25")
    body = COMMAND.create_body()
    assert body == metadata.COMMAND.create_body() | {"Cmd": list(argv)}
    body["Cmd"].append("--PRIVATE")
    assert COMMAND.create_body()["Cmd"] == list(argv)
    assert type(COMMAND) is not e.Command
    with pytest.raises(e.UnconfirmedExecution):
        e.inspect(
            probe_metadata(),
            execution_id=metadata.EXEC,
            container_id=metadata.CONTAINER,
            command=COMMAND,
        )
    with pytest.raises(e.UnconfirmedExecution):
        inspect(metadata.metadata(), metadata.COMMAND)
    with pytest.raises(e.UnconfirmedExecution):
        inspect(metadata.metadata())


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan", "/data/launch.json"),
        ("plan", "/data/x/../x/launch.json"),
        ("plan", "relative"),
        ("plan_sha256", "PRIVATE"),
        ("source_sha256", True),
        ("probe_by", True),
        ("probe_by", float("nan")),
        ("probe_by", -1),
    ],
)
def test_probe_command_keeps_fixed_path_digest_and_deadline_validation(field, value):
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
def test_probe_metadata_keeps_process_states_distinct(running, pid, code, phase):
    value = probe_metadata() | {"Running": running, "Pid": pid, "ExitCode": code}
    before = copy.deepcopy(value)
    observed = inspect(value)
    assert observed == e.State(phase, pid, code) and value == before
    assert not hasattr(observed, "healthy") and not hasattr(observed, "recording")


@pytest.mark.parametrize("fault", ["extra", "missing", "argv", "user", "tty", "pid", "stderr"])
def test_probe_inspection_cannot_ignore_metadata_changes(fault):
    value = probe_metadata()
    if fault == "extra":
        value["PRIVATE"] = 1
    elif fault == "missing":
        del value["ExitCode"]
    elif fault == "argv":
        value["ProcessConfig"]["arguments"][-1] = "100.26"
    elif fault == "user":
        value["ProcessConfig"]["user"] = "root"
    elif fault == "tty":
        value["ProcessConfig"]["tty"] = True
    elif fault == "pid":
        value["Pid"] = True
    else:
        value["OpenStderr"] = False
    with pytest.raises(e.UnconfirmedExecution):
        inspect(value)


def test_decoder_has_explicit_one_reply_budget_without_weakening_default():
    one = framing.m.Decoder(message_limit=1)
    assert one.feed(framing.UPGRADE + framing.segment(framing.app(RESULT))) == [RESULT]
    one.finish()
    default = framing.m.Decoder()
    assert default.feed(framing.UPGRADE + framing.segment(framing.app(RESULT))) == [RESULT]
    with pytest.raises(framing.m.UnconfirmedExecStream):
        default.finish()
    extra = framing.m.Decoder(message_limit=1)
    with pytest.raises(framing.m.UnconfirmedExecStream):
        extra.feed(framing.UPGRADE + framing.segment(framing.app(RESULT) * 2))


@pytest.mark.parametrize("limit", [0, 2, 3, 5, True, 1.0, None, "1"])
def test_other_decoder_contracts_are_not_available(limit):
    with pytest.raises(framing.m.UnconfirmedExecStream):
        framing.m.Decoder(message_limit=limit)


class ProbeFixture:
    """Own exact private socket/thread; never substitutes a real Docker peer."""

    def __init__(self, server, budget=3):
        from threading import Thread

        self.client, self.peer = socket.socketpair()
        self.peer.settimeout(5)
        self.errors = []
        self.attachment = a.ProbeAttachment(
            self.client, metadata.EXEC, probe_by=time.monotonic() + budget
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
def test_actual_one_request_reply_and_clean_eof_are_only_framing(chunk):
    def send(peer, data):
        for offset in range(0, len(data), chunk):
            peer.sendall(data[offset : offset + chunk])

    def server(peer):
        transport.request(peer)
        send(peer, framing.UPGRADE)
        assert transport.begun(peer) == REQUEST
        send(peer, framing.segment(framing.app(RESULT)))

    with ProbeFixture(server) as attached:
        attached.start(deadline=attached.finish_by)
        attached.send_request(REQUEST, deadline=attached.finish_by)
        assert attached.receive(deadline=attached.finish_by) == RESULT
        attached.finish(deadline=attached.finish_by)
        assert attached.finished and attached.closed
        assert not hasattr(attached, "healthy") and not hasattr(attached, "exit_code")


def test_reply_queued_after_upgrade_but_before_request_is_refused():
    from threading import Event

    after_upgrade = Event()

    def server(peer):
        transport.request(peer)
        peer.sendall(framing.UPGRADE)
        assert after_upgrade.wait(2)
        peer.sendall(framing.segment(framing.app(RESULT)))

    with ProbeFixture(server) as attached:
        attached.start(deadline=attached.finish_by)
        after_upgrade.set()
        assert select.select([attached.channel], [], [], 2)[0]
        transport.deny(
            lambda: attached.send_request(REQUEST, deadline=attached.finish_by), attached
        )


@pytest.mark.parametrize(
    "fault",
    [
        "early_reply",
        "wrong_kind",
        "extra_frame",
        "stderr",
        "partial",
        "empty",
        "partial_tail",
    ],
)
def test_unknown_or_extra_reply_never_qualifies_a_probe(fault):
    def server(peer):
        transport.request(peer)
        if fault == "early_reply":
            peer.sendall(framing.UPGRADE + framing.segment(framing.app(RESULT)))
            return
        peer.sendall(framing.UPGRADE)
        assert transport.begun(peer) == REQUEST
        payload = {
            "wrong_kind": framing.segment(framing.app({"phase": "ready"})),
            "extra_frame": framing.segment(framing.app(RESULT) * 2),
            "stderr": framing.segment(b"PRIVATE", channel=2),
            "partial": framing.segment(framing.w.HEADER.pack(10) + b"{}"),
            "empty": b"",
            "partial_tail": framing.segment(framing.app(RESULT) + b"\0"),
        }[fault]
        peer.sendall(payload)

    with ProbeFixture(server) as attached:

        def run():
            attached.start(deadline=attached.finish_by)
            attached.send_request(REQUEST, deadline=attached.finish_by)
            attached.receive(deadline=attached.finish_by)
            attached.finish(deadline=attached.finish_by)

        transport.deny(run, attached)


@pytest.mark.parametrize(
    "operation", ["operator_begin", "read_before_request", "extend_deadline", "wrong_request"]
)
def test_unavailable_operations_fail_before_request_and_close_socket(operation):
    def server(peer):
        transport.request(peer)
        peer.sendall(framing.UPGRADE)
        assert peer.recv(1) == b""

    with ProbeFixture(server) as attached:
        attached.start(deadline=attached.finish_by)
        actions = {
            "operator_begin": lambda: attached.send_begin(
                {"phase": "begin"}, deadline=attached.finish_by
            ),
            "read_before_request": lambda: attached.receive(deadline=attached.finish_by),
            "extend_deadline": lambda: attached.send_request(
                REQUEST, deadline=attached.finish_by + 1
            ),
            "wrong_request": lambda: attached.send_request(
                {"phase": "begin"}, deadline=attached.finish_by
            ),
        }
        transport.deny(actions[operation], attached)


@pytest.mark.parametrize("fault", ["second_request", "second_read"])
def test_each_direction_is_consumed_once(fault):
    def server(peer):
        transport.request(peer)
        peer.sendall(framing.UPGRADE)
        assert transport.begun(peer) == REQUEST
        if fault == "second_read":
            peer.sendall(framing.segment(framing.app(RESULT)))
        assert peer.recv(1) == b""

    with ProbeFixture(server) as attached:
        attached.start(deadline=attached.finish_by)
        attached.send_request(REQUEST, deadline=attached.finish_by)
        if fault == "second_read":
            attached.receive(deadline=attached.finish_by)
            transport.deny(lambda: attached.receive(deadline=attached.finish_by), attached)
        else:
            transport.deny(
                lambda: attached.send_request(REQUEST, deadline=attached.finish_by), attached
            )


@pytest.mark.parametrize("deadline", [0, -1, True, float("inf"), float("nan"), None, "5", 10**20])
def test_bad_short_deadline_closes_owned_socket(deadline):
    client, peer = socket.socketpair()
    try:
        with pytest.raises(a.UnconfirmedAttachment):
            a.ProbeAttachment(client, metadata.EXEC, probe_by=deadline)
        assert client.fileno() == -1
    finally:
        client.close()
        peer.close()
