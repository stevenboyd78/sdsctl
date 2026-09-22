"""Strict host evidence and local Unix HTTP fixtures; no HA/scanner connection."""

import importlib.util
import json
import socketserver
import sys
import threading
from copy import deepcopy
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1] / "scripts"
for name in (
    "supplemental_handoff_policy",
    "supplemental_handoff_executor",
    "supplemental_handoff_host",
):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
p = sys.modules["supplemental_handoff_policy"]
e = sys.modules["supplemental_handoff_executor"]
h = sys.modules["supplemental_handoff_host"]
CID, EID, IMAGE = "a" * 64, "b" * 64, "sha256:" + "c" * 64
BOOT, CASE = "dddddddddddd4ddd8ddddddddddddddd", "eeeeeeeeeeee4eee8eeeeeeeeeeeeeee"
COMMAND = h.CONTROL["stopping_normal"]


def container():
    return {
        "Id": CID,
        "Name": "/hassio_cli",
        "Image": IMAGE,
        "State": {
            "Status": "running",
            "Running": True,
            "Paused": False,
            "Restarting": False,
            "Dead": False,
            "OOMKilled": False,
            "Pid": 123,
            "Error": "",
            "StartedAt": "2026-09-22T00:00:00.123456789Z",
        },
    }


def execution(command=COMMAND):
    return {
        "ID": EID,
        "ContainerID": CID,
        "Running": False,
        "ExitCode": 0,
        "Pid": 0,
        "OpenStdin": False,
        "ProcessConfig": {
            "entrypoint": command[0],
            "arguments": list(command[1:]),
            "privileged": False,
            "tty": False,
            "user": "0",
        },
    }


def node(*children, done=True):
    return {"uuid": uuid4().hex, "done": done, "errors": [], "child_jobs": list(children)}


def jobs(*nodes, ignore=None):
    return p.encode(
        {"result": "ok", "data": {"jobs": list(nodes), "ignore_conditions": ignore or []}}
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"false",
        b'{"x":NaN}',
        b'{"x":1,"x":2}',
        b'{"secret":"',
        b"x" * (h.MAX_RESPONSE + 1),
    ],
)
def test_bad_response_is_sanitized(raw):
    with pytest.raises(p.UnsafeHandoff) as caught:
        h.object_json(raw)
    assert "secret" not in str(caught.value)


def test_job_tree_must_be_recursively_idle():
    assert h.supervisor_jobs_idle(jobs())
    assert h.supervisor_jobs_idle(jobs(node(node(node()))))
    assert not h.supervisor_jobs_idle(jobs(node(node(node(done=False)))))
    with pytest.raises(p.UnsafeHandoff):
        h.supervisor_jobs_idle(jobs({"done": "private"}, node(done=False)))


@pytest.mark.parametrize(
    "change",
    [
        lambda n: n.pop("child_jobs"),
        lambda n: n.update(done="true"),
        lambda n: n.update(done=1),
        lambda n: n.update(child_jobs={}),
        lambda n: n.update(errors=None),
        lambda n: n.update(uuid="not-a-uuid"),
        lambda n: n["child_jobs"].append(deepcopy(n)),
    ],
)
def test_uncertain_job_structure_refused(change):
    n = node()
    change(n)
    with pytest.raises(p.UnsafeHandoff):
        h.supervisor_jobs_idle(jobs(n))


def test_job_limits_and_ignored_conditions_refused():
    n = node()
    for _ in range(17):
        n = node(n)
    for raw in (
        jobs(n),
        jobs(*(node() for _ in range(257))),
        jobs(ignore=["healthy"]),
        b'{"result":"error","message":"secret"}',
        b'{"result":"ok","data":{}}',
    ):
        with pytest.raises(p.UnsafeHandoff):
            h.supervisor_jobs_idle(raw)


def frame(data, stream=1):
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


def test_framed_output_is_not_terminal_text_or_stderr():
    assert h.docker_output(frame(b'{"result":') + frame(b'"ok"}')) == b'{"result":"ok"}'
    for raw in (
        b"plain text",
        frame(b"secret", 2),
        b"\1\0\0\0\0\0\0\x40abc",
        frame(b"ok") + b"x",
        b"x" * (h.MAX_RESPONSE + 1),
    ):
        with pytest.raises(p.UnsafeHandoff):
            h.docker_output(raw)


def test_generation_changes_on_restart_even_with_same_container():
    first = container()
    initial = h.generation(first, name=h.CLI, image=IMAGE)
    for key, value in (("Pid", 456), ("StartedAt", "2026-09-22T00:01:00Z")):
        other = deepcopy(first)
        other["State"][key] = value
        assert h.generation(other, name=h.CLI, image=IMAGE) != initial


@pytest.mark.parametrize(
    "key,value",
    [
        ("Running", False),
        ("Running", 1),
        ("Status", "exited"),
        ("Paused", True),
        ("Restarting", True),
        ("Dead", True),
        ("OOMKilled", True),
        ("Pid", True),
        ("Pid", 0),
        ("StartedAt", "yesterday"),
        ("Error", "private"),
    ],
)
def test_invalid_process_incarnation_refused(key, value):
    data = container()
    data["State"][key] = value
    with pytest.raises(p.UnsafeHandoff):
        h.generation(data, name=h.CLI, image=IMAGE)


@pytest.mark.parametrize(
    "key,value",
    [
        ("ContainerID", "d" * 64),
        ("ID", "d" * 64),
        ("Running", "false"),
        ("ExitCode", False),
        ("OpenStdin", True),
        ("Pid", -1),
    ],
)
def test_execution_identity_and_types_are_checked(key, value):
    data = execution()
    data[key] = value
    with pytest.raises(p.UnsafeHandoff):
        h.execution_state(data, execution_id=EID, container_id=CID, command=COMMAND)


@pytest.mark.parametrize(
    "key,value",
    [
        ("entrypoint", "sh"),
        ("arguments", ["-c", "anything"]),
        ("privileged", True),
        ("tty", True),
        ("user", "1000"),
    ],
)
def test_execution_command_must_match(key, value):
    data = execution()
    data["ProcessConfig"][key] = value
    with pytest.raises(p.UnsafeHandoff):
        h.execution_state(data, execution_id=EID, container_id=CID, command=COMMAND)


@pytest.fixture
def journal(tmp_path):
    directory = tmp_path / "journal"
    directory.mkdir(mode=0o700)
    with p.Journal(directory) as j:
        observation = p.Observation(
            10,
            p.App("1" * 64, "running", "2" * 64, True, False),
            p.App("3" * 64, "stopped"),
            True,
            True,
            True,
        )
        j.append(
            {
                "kind": "prepare",
                "case_id": CASE,
                "boot_id": BOOT,
                "now": 10,
                "observation": asdict(observation),
            }
        )
        j.append({"kind": "request", "boot_id": BOOT, "now": 11})
        yield j


class FakeDocker:
    def __init__(self):
        self.cli, self.exec = container(), execution()
        self.exec["ExitCode"] = None
        self.created, self.started = [], []
        self.create_error = self.start_error = None
        self.on_start = lambda _: None

    def container(self, identity):
        assert identity == h.CLI
        return deepcopy(self.cli)

    def create_execution(self, cid, command):
        self.created.append((cid, command))
        if self.create_error:
            raise self.create_error
        return EID

    def inspect_execution(self, identity):
        assert identity == EID
        return deepcopy(self.exec)

    def start_execution(self, identity):
        self.on_start(identity)
        self.started.append(identity)
        self.exec["ExitCode"] = 0
        if self.start_error:
            raise self.start_error


def dispatch(j, docker, now=lambda: 12.2):
    return h.TrackedDispatch(
        j,
        docker,
        cli_image=IMAGE,
        cli_generation=h.generation(container(), name=h.CLI, image=IMAGE),
        now=now,
    )


def intent(j):
    observation = asdict(j.machine.baseline)
    observation["sampled_at"] = 12
    return j.append({"kind": "observe", "boot_id": BOOT, "now": 12, "observation": observation})


def test_execution_bound_durably_before_start_and_never_restarted(journal):
    docker = FakeDocker()
    send = dispatch(journal, docker)
    intent(journal)

    def assert_bound(_):
        assert journal.machine.state.executions == (("stopping_normal", CID, EID),)
        assert json.loads((journal.path / "0003.json").read_bytes())["event"]["execution_id"] == EID

    docker.on_start = assert_bound
    docker.start_error = TimeoutError("lost secret reply")
    with pytest.raises(TimeoutError):
        send(COMMAND, CASE)
    assert docker.started == [EID]
    assert send.executions_idle()  # NOT evidence that the App stopped.
    docker.exec["Running"] = True
    assert not send.executions_idle()
    path = journal.path
    journal.close()
    with p.Journal(path) as recovered:
        reopened = dispatch(recovered, docker)
        with pytest.raises(p.UnsafeHandoff):
            reopened(COMMAND, CASE)
        assert recovered.machine.state.executions == (("stopping_normal", CID, EID),)
    assert len(docker.created) == len(docker.started) == 1


def test_create_reply_lost_cannot_retry_even_without_an_execution_id(journal):
    docker = FakeDocker()
    send = dispatch(journal, docker)
    intent(journal)
    docker.create_error = TimeoutError()
    with pytest.raises(TimeoutError):
        send(COMMAND, CASE)
    for again in (send, dispatch(journal, docker)):
        with pytest.raises(p.UnsafeHandoff):
            again(COMMAND, CASE)
    assert len(docker.created) == 1 and not docker.started


@pytest.mark.parametrize("fault", ["generation", "identity", "already_running", "late", "journal"])
def test_post_creation_fault_never_starts_execution(journal, fault, monkeypatch):
    docker = FakeDocker()
    send = dispatch(journal, docker, now=(lambda: 15) if fault == "late" else (lambda: 12.2))
    intent(journal)
    if fault == "generation":
        docker.cli["State"]["Pid"] += 1
    if fault == "identity":
        docker.exec["ContainerID"] = "f" * 64
    if fault == "already_running":
        docker.exec["Running"] = True
    if fault == "journal":
        monkeypatch.setattr(journal, "append", lambda _: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises((p.UnsafeHandoff, OSError)):
        send(COMMAND, CASE)
    assert not docker.started


def test_pending_intent_reopened_before_dispatch_cannot_be_adopted(journal):
    intent(journal)
    docker = FakeDocker()
    with pytest.raises(p.UnsafeHandoff):
        dispatch(journal, docker)(COMMAND, CASE)
    assert not docker.created


def test_duplicate_execution_binding_refused(journal):
    intent(journal)
    event = {
        "kind": "bind_execution",
        "boot_id": BOOT,
        "now": 12.2,
        "container_id": CID,
        "execution_id": EID,
    }
    journal.append(event)
    with pytest.raises(p.UnsafeHandoff):
        journal.append({**event, "now": 12.3})
    assert len(journal.entries) == 4


@pytest.fixture
def http_engine(tmp_path):
    calls, replies = [], []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append((self.command, self.path, json.loads(body) if body else None))
            code, payload = replies.pop(0)
            self.send_response(code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = handle_request

        def log_message(self, *args):
            pass

    path = str(tmp_path / "docker.sock")
    with socketserver.UnixStreamServer(path, Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield h.Docker(path), calls, replies
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_real_unix_http_create_start_inspect_shapes(http_engine):
    docker, calls, replies = http_engine
    replies += [(201, p.encode({"Id": EID})), (200, b""), (200, p.encode(execution()))]
    assert docker.create_execution(CID, COMMAND) == EID
    docker.start_execution(EID)
    assert docker.inspect_execution(EID)["ID"] == EID
    assert [method for method, _, _ in calls] == ["POST", "POST", "GET"]
    assert calls[0][1] == h.API + f"/containers/{CID}/exec"
    assert calls[0][2] == {
        "AttachStdin": False,
        "AttachStdout": False,
        "AttachStderr": False,
        "Privileged": False,
        "User": "0",
        "Tty": False,
        "Cmd": list(COMMAND),
    }
    assert calls[1][2] == {"Detach": True, "Tty": False}


def test_real_unix_http_fixed_inventory_and_image_reads(http_engine):
    docker, calls, replies = http_engine
    image = "sha256:" + "f" * 64
    replies += [(200, b"[]"), (200, p.encode({"Id": image})), (200, b"{}")]
    assert docker.containers() == []
    assert docker.image(image) == {"Id": image}
    assert docker.container(h.CORE) == {}
    assert calls == [
        ("GET", h.API + "/containers/json?all=1", None),
        ("GET", h.API + "/images/" + image + "/json", None),
        ("GET", h.API + "/containers/homeassistant/json", None),
    ]


@pytest.mark.parametrize("value", ["latest", "sha256:bad", "../images/test", None])
def test_image_read_requires_immutable_identity(http_engine, value):
    docker, calls, _ = http_engine
    with pytest.raises(p.UnsafeHandoff):
        docker.image(value)
    assert not calls


@pytest.mark.parametrize("raw", [b"{}", b"[null]", b"[1]", b'[ {"Id":1,"Id":2} ]', b"[NaN]"])
def test_inventory_json_rejects_ambiguous_or_invalid_shapes(http_engine, raw):
    docker, calls, replies = http_engine
    replies.append((200, raw))
    with pytest.raises(p.UnsafeHandoff):
        docker.containers()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,raw",
    [
        (500, b"secret"),
        (301, b""),
        (404, b"not found"),
        (200, b'{"Id":"bad"}'),
        (201, b'{"Id":"bad"}'),
        (201, b"x" * (h.MAX_RESPONSE + 1)),
    ],
)
def test_uncertain_http_response_never_retried(http_engine, status, raw):
    docker, calls, replies = http_engine
    replies.append((status, raw))
    with pytest.raises(p.UnsafeHandoff) as caught:
        docker.create_execution(CID, COMMAND)
    assert len(calls) == 1 and "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "command",
    [
        ("sh", "-c", "true"),
        ("ha", "core", "restart"),
        ("ha", "apps", "rebuild", p.NORMAL, "--raw-json"),
        [*COMMAND],
    ],
)
def test_no_arbitrary_command_api(http_engine, command):
    docker, calls, _ = http_engine
    with pytest.raises(p.UnsafeHandoff):
        docker.create_execution(CID, command)
    assert not calls


@pytest.mark.parametrize(
    "code,running,pid,result",
    [
        (None, False, 0, "created"),
        (None, True, 42, "running"),
        (0, False, 42, "not_running"),
        (1, False, 42, "not_running"),
    ],
)
def test_created_running_and_finished_are_distinct(code, running, pid, result):
    value = execution()
    value.update(ExitCode=code, Running=running, Pid=pid)
    assert h.execution_state(value, execution_id=EID, container_id=CID, command=COMMAND) == result


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.pop("ExitCode"),
        lambda v: v["ProcessConfig"].pop("user"),
        lambda v: v.update(ExitCode=None, Running=False, Pid=99),
    ],
)
def test_incomplete_execution_evidence_refused(change):
    value = execution()
    change(value)
    with pytest.raises(p.UnsafeHandoff):
        h.execution_state(value, execution_id=EID, container_id=CID, command=COMMAND)


@pytest.mark.parametrize(
    "kind", ["prepare", "request", "finished", "invalid_id", "late", "duplicate"]
)
def test_execution_binding_only_once_in_current_pending_phase(journal, kind):
    event = {
        "kind": "bind_execution",
        "boot_id": BOOT,
        "now": 12.2,
        "container_id": CID,
        "execution_id": EID,
    }
    if kind in ("finished", "invalid_id", "late", "duplicate"):
        intent(journal)
    if kind == "finished":
        journal.machine.review("fixture")  # terminal state is rebuilt on append, test directly
        with pytest.raises(p.UnsafeHandoff):
            journal.machine.event(event)
        return
    if kind == "invalid_id":
        event["execution_id"] = "../anything"
    if kind == "late":
        event["now"] = 133
    if kind == "duplicate":
        journal.append(event)
    if kind == "prepare":
        # Separate pure prepared machine: fixture journal is already requested.
        machine = p.Machine(CASE, BOOT, 10, journal.machine.baseline)
        with pytest.raises(p.UnsafeHandoff):
            machine.event(event)
        return
    with pytest.raises(p.UnsafeHandoff):
        journal.append(event)


def test_full_executor_dispatch_and_replay_keeps_all_four_execution_ids(journal):
    class MultiDocker(FakeDocker):
        def __init__(self):
            super().__init__()
            self.history = {}

        def create_execution(self, cid, command):
            eid = f"{len(self.created) + 1:064x}"
            value = execution(command)
            value.update(ID=eid, ExitCode=None)
            self.history[eid] = value
            self.created.append((cid, command))
            return eid

        def inspect_execution(self, eid):
            return deepcopy(self.history[eid])

        def start_execution(self, eid):
            self.started.append(eid)
            self.history[eid]["ExitCode"] = 0

    docker = MultiDocker()
    clock = [12.2]
    send = dispatch(journal, docker, now=lambda: clock[0])
    initial = journal.machine.baseline
    queue = []

    def read():
        return queue.pop(0)

    ex = e.Executor(journal, read, send)

    def poll(now, normal, candidate, needs_send):
        clock[0] = now + 0.2
        for stamp in [now, now + 0.1] if needs_send else [now]:
            queue.append(
                e.Sample(
                    BOOT,
                    stamp,
                    p.Observation(stamp, normal, candidate, True, send.executions_idle(), True),
                )
            )
        result = ex.poll()
        if result.outcome == "dispatch_submitted":
            send.reconcile_executions()
        return result

    normal = initial.normal
    stopped = p.App(normal.pin, "stopped")
    candidate = initial.candidate
    running = p.App(candidate.pin, "running", "4" * 64, True, False)
    assert poll(12, normal, candidate, True).outcome == "dispatch_submitted"
    assert poll(13, stopped, candidate, True).outcome == "dispatch_submitted"
    assert poll(14, stopped, running, False).phase == "candidate_running"
    journal.append({"kind": "finish", "now": 15, "boot_id": BOOT})
    assert poll(16, stopped, running, True).outcome == "dispatch_submitted"
    assert poll(17, stopped, candidate, True).outcome == "dispatch_submitted"
    restored = p.App(normal.pin, "running", "5" * 64, True, False)
    assert poll(18, restored, candidate, False).phase == "complete"
    assert len(docker.started) == 4
    history = journal.machine.state.executions
    assert len(history) == 4
    assert len(journal.machine.state.completed_executions) == 4
    assert len(journal.entries) < p.MAX_EVENTS
    path = journal.path
    journal.close()
    with p.Journal(path) as replay:
        assert replay.machine.state.phase == "complete"
        assert replay.machine.state.executions == history
        docker.history.clear()  # Docker has expired its old finished metadata.
        assert dispatch(replay, docker).executions_idle()
    assert len(docker.created) == 4


def test_unknown_execution_on_reconciliation_is_not_idle(journal):
    docker = FakeDocker()
    send = dispatch(journal, docker)
    intent(journal)
    send(COMMAND, CASE)
    docker.exec["ID"] = "f" * 64
    with pytest.raises(p.UnsafeHandoff):
        send.executions_idle()


def test_not_started_or_unobserved_exit_cannot_clear_pending_work(journal):
    docker = FakeDocker()
    send = dispatch(journal, docker)
    intent(journal)
    send(COMMAND, CASE)
    docker.exec["ExitCode"] = None  # Simulate a queued/lost start, not completed.
    assert not send.executions_idle()
    send.reconcile_executions()
    assert journal.machine.state.completed_executions == ()
    docker.exec["ID"] = "f" * 64  # Unknown/lost metadata, never observed finished.
    with pytest.raises(p.UnsafeHandoff):
        send.reconcile_executions()
    assert journal.machine.state.completed_executions == ()


@pytest.mark.parametrize("code", [0, 1, 126, 137])
def test_process_exit_receipt_is_not_app_success(journal, code):
    docker = FakeDocker()
    send = dispatch(journal, docker)
    intent(journal)
    send(COMMAND, CASE)
    docker.exec["ExitCode"] = code
    send.reconcile_executions()
    assert journal.machine.state.completed_executions == ((EID, code),)
    assert journal.machine.state.phase == "stopping_normal"
    before = len(journal.entries)
    send.reconcile_executions()
    assert len(journal.entries) == before  # No duplicate completion journal entry.
    docker.exec.clear()  # Only independently observed exit survives metadata loss.
    assert send.executions_idle()


@pytest.mark.parametrize("code", [None, True, -1, 256, "0"])
def test_forged_exit_receipt_refused(journal, code):
    docker = FakeDocker()
    send = dispatch(journal, docker)
    intent(journal)
    send(COMMAND, CASE)
    with pytest.raises(p.UnsafeHandoff):
        journal.append(
            {
                "kind": "execution_completed",
                "boot_id": BOOT,
                "now": 12.3,
                "execution_id": EID,
                "exit_code": code,
            }
        )


def config(**changes):
    value = {
        "slug": p.NORMAL,
        "version": "0.30.0-fixture",
        "state": "started",
        "boot": "manual",
        "protected": True,
        "watchdog": False,
        "auto_update": False,
        "host_network": False,
        "host_pid": False,
        "network": {"50000/udp": None, "50443/tcp": None, "8443/tcp": None},
        "options": {"private_password": "not-real-fixture-secret"},
    }
    value.update(changes)
    return p.encode({"result": "ok", "data": value})


def test_configuration_hash_excludes_status_but_covers_options_and_version():
    first = h.app_configuration(config(), slug=p.NORMAL)
    stopped = h.app_configuration(config(state="stopped"), slug=p.NORMAL)
    assert first.settings_sha256 == stopped.settings_sha256
    assert stopped.supervisor_state == "stopped"  # Not process-exit evidence.
    changed = h.app_configuration(config(options={"private_password": "different"}), slug=p.NORMAL)
    assert first.settings_sha256 != changed.settings_sha256
    changed = h.app_configuration(config(version="0.30.1"), slug=p.NORMAL)
    assert first.settings_sha256 != changed.settings_sha256
    assert "not-real-fixture-secret" not in repr(first)
    assert h.app_configuration(config(state=None), slug=p.NORMAL).supervisor_state == "unknown"


@pytest.mark.parametrize(
    "changes",
    [
        {"slug": p.CANDIDATE},
        {"version": "secret\nvalue"},
        {"version": None},
        {"boot": "auto"},
        {"protected": False},
        {"watchdog": True},
        {"auto_update": True},
        {"host_network": True},
        {"host_pid": True},
        {"network": {}},
        {"network": {"50000/udp": 50000, "50443/tcp": None, "8443/tcp": None}},
        {"options": {}},
        {"options": None},
        {"state": "error"},
    ],
)
def test_configuration_drift_redaction_or_unsafe_policy_refused(changes):
    with pytest.raises(p.UnsafeHandoff):
        h.app_configuration(config(**changes), slug=p.NORMAL)


@pytest.mark.parametrize("slug", [p.NORMAL, p.CANDIDATE])
def test_reader_only_contract_is_not_an_audio_ready_port_policy(slug):
    # Live pre-arm qualification found zero RTP packets with this intentionally
    # unpublished network. Keep both roles fail-closed: an audio-capable case
    # needs its own explicit reviewed contract, not a silent port relaxation.
    assert h.app_configuration(config(slug=slug), slug=slug).slug == slug
    with pytest.raises(p.UnsafeHandoff):
        h.app_configuration(
            config(
                slug=slug,
                network={"50000/udp": 50000, "50443/tcp": None, "8443/tcp": None},
            ),
            slug=slug,
        )


def test_absolute_socket_deadline_cannot_be_extended_by_dripping_headers():
    import socket
    import time

    left, right = socket.socketpair()
    reader = h._DeadlineSocket(fileno=left.detach())
    reader.deadline = time.monotonic() + 0.1

    def drip():
        try:
            for _ in range(30):
                right.sendall(b"x")
                time.sleep(0.02)
        except OSError:
            pass
        finally:
            right.close()

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()
    try:
        with reader.makefile("rb") as stream, pytest.raises(TimeoutError):
            stream.read(30)
    finally:
        reader.close()
        thread.join(timeout=2)
    assert not thread.is_alive()
