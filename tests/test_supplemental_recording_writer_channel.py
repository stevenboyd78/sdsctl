"""Real original Startup, plan files and 3-process descriptor/Link handoff.

Host/Engine reads, cgroups and runtime declaration provenance are synthetic.
The parent is the original writer; the outer child creates/delivers both channel
halves and a separate observer child uses Link. Unlike a reconstructed writer
fixture, the writer keeps the exact clock created AFTER its baseline read.
No installed qualification, App consent, scanner I/O or live services.
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_bootstrap as transport
from . import test_supplemental_recording_service_runtime_expectations as declared
from . import test_supplemental_recording_startup_assembly as assembly

NAME = "supplemental_recording_writer_channel"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(transport.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

assert m.startup is assembly.m and m.expectations is declared.m
(
    layout,
    tree,
    routing,
    projection,
    binding,
    directory,
    prepared,
    joined,
    before_handoff,
    service_case,
) = (
    assembly.layout,
    assembly.tree,
    assembly.routing,
    assembly.projection,
    assembly.binding,
    assembly.directory,
    assembly.prepared,
    assembly.joined,
    assembly.before_handoff,
    assembly.service_case,
)

OUTER = r"""
import json, os, socket, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_peer_bootstrap as m
m.ROOT_UID = os.geteuid()
listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
listener.bind(sys.argv[2])
listener.listen(2)
print("listening", flush=True)
connections = {}
for _ in range(2):
    connection, _ = listener.accept()
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    connection.setblocking(False)
    pid, uid, gid = m.links.control.returns.CREDENTIALS.unpack(
        connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    connections[pid] = connection
listener.close()
print("connected", flush=True)
config = json.loads(sys.stdin.buffer.readline())
table = {item["pid"]: item["container_id"] for item in config["identities"]}
def identity(pid, cid):
    assert table[pid] == cid
    return m.links.processes.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
m.links.processes.read_identity = identity
plan = m.links.plans.load_bytes(config["plan"].encode(), config["sha256"])
clock = m.links.clock.ClockWitness(m.links.clock.read())
local = identity(os.getpid(), table[os.getpid()])
peers = {role: m.links.processes.ProcessWitness(identity(config[role], table[config[role]]))
    for role in ("writer", "observer")}
bundles = m.links.pair()
endpoints = []
try:
    for role, channels in zip(("writer", "observer"), bundles):
        other = "observer" if role == "writer" else "writer"
        endpoint = m.Endpoint(connections[config[role]], plan, clock, local, peers[role],
            peers[other], role=role, mode="deliver", declaration_sha256=config["declaration"])
        endpoints.append(endpoint)
        receipt = endpoint.deliver(channels)
        channels.close()
        endpoint.close()
    print("delivered", flush=True)
    sys.stdin.buffer.readline()
except Exception:
    print("refused", flush=True)
    sys.stdin.buffer.readline()
finally:
    for endpoint in endpoints: endpoint.close()
    for channels in bundles: channels.close()
    for peer in peers.values(): peer.close()
    for connection in connections.values(): connection.close()
    clock.close()
"""


@pytest.fixture
def connection(service_case, monkeypatch):
    s = service_case
    monkeypatch.setattr(m.bootstrap, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.bootstrap.links, "ROOT_UID", os.geteuid())
    temporary = tempfile.TemporaryDirectory(prefix="sds-writer-")
    path = str(Path(temporary.name) / "peer")
    processes, witnesses, channels = [], [], []
    accepted = None

    def spawn(code):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", code, str(Path(m.__file__).parent), path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        processes.append(process)
        return process

    try:
        outer = spawn(OUTER)
        assert transport.line(outer) == "listening"
        observer = spawn(
            transport.CHILD.replace(
                'identity(os.getppid(), config["outer"])',
                'identity(config["outer_pid"], config["outer"])',
            ).replace(
                "m.links.clock.ClockWitness(plan.original_clock)",
                "m.links.clock.ClockWitness(m.links.clock.read())",
            )
        )
        assert transport.line(observer) == "ready"
        accepted = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        accepted.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        accepted.connect(path)
        accepted.setblocking(False)
        assert transport.line(outer) == "connected"
        table = {os.getpid(): "a" * 64, outer.pid: "b" * 64, observer.pid: "c" * 64}

        def identity(pid, cid):
            assert table[pid] == cid
            return m.bootstrap.links.processes.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )

        monkeypatch.setattr(m.bootstrap.links.processes, "read_identity", identity)
        identities = {pid: identity(pid, cid) for pid, cid in table.items()}
        outer_witness = m.bootstrap.links.processes.ProcessWitness(identities[outer.pid])
        witnesses.append(outer_witness)
        observer_witness = m.bootstrap.links.processes.ProcessWitness(identities[observer.pid])
        witnesses.append(observer_witness)
        owner = assembly.accept(s)
        values = declared.value()
        values["template_sha256"] = owner.template.sha256
        values["writer"]["runtime"] = json.loads(owner.template.raw)["plan"]["helper"]
        values["observer"]["runtime"]["source"] = values["writer"]["runtime"]["source"]
        declaration = m.expectations.decode(values)
        expected_sha256 = declaration.sha256
        plan = owner.original.plan
        config = dict(
            plan=plan.raw.decode(),
            sha256=plan.sha256,
            identities=[asdict(item) for item in identities.values()],
            declaration=declaration.sha256,
        )

        def start():
            transport.command(outer, config | dict(writer=os.getpid(), observer=observer.pid))
            transport.command(
                observer,
                config
                | dict(
                    role="observer",
                    local=table[observer.pid],
                    outer=table[outer.pid],
                    outer_pid=outer.pid,
                    peer=table[os.getpid()],
                    peer_pid=os.getpid(),
                ),
            )

        def receive(**kwargs):
            result = m.receive(
                owner,
                declaration,
                expected_sha256,
                accepted,
                identities[os.getpid()],
                outer_witness,
                observer_witness,
                **kwargs,
            )
            channels.append(result[0])
            return result

        yield SimpleNamespace(
            s=s,
            owner=owner,
            plan=plan,
            declaration=declaration,
            start=start,
            receive=receive,
            outer=outer,
            observer=observer,
            channel=accepted,
            local=identities[os.getpid()],
            outer_witness=outer_witness,
            observer_witness=observer_witness,
            channels=channels,
        )
    finally:
        for bundle in channels:
            bundle.close()
        for process in processes:
            if process.poll() is None:
                process.kill()  # Only original disposable subprocesses owned by this fixture.
            process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        for witness in witnesses:
            witness.close()
        if accepted is not None:
            accepted.close()
        temporary.cleanup()


def denied(call):
    with pytest.raises(m.UnconfirmedWriterChannel) as error:
        call()
    assert str(error.value) == m.MESSAGE and error.value.__suppress_context__


def test_original_post_baseline_clock_reaches_delivered_link_then_passive_assembly(connection):
    c = connection
    owner = c.owner
    original, clock, baseline, projected = (
        owner.original,
        owner.clock,
        owner.baseline,
        owner.projected,
    )
    assert len(c.s.clocks) == 2 and c.s.clocks[0].closed and c.s.clocks[1] is clock
    c.start()
    channels, receipt = c.receive()
    assert transport.line(c.outer) == "delivered"
    assert json.loads(transport.line(c.observer))["received"]
    assert owner.original is original and owner.clock is clock and not clock.closed
    assert owner.baseline is baseline and owner.projected is projected
    assert owner.peer_channel_attempted is True and not owner.service_used
    assert len(c.s.clocks) == 2 and len(c.s.cached_calls) == 1
    assert not (c.s.root / "journal").exists() and not (c.s.root / "inbox").exists()
    assert receipt.context_sha256 and receipt.offer_sha256
    link = m.bootstrap.links.Link(channels, c.plan, clock, c.observer_witness, role="writer")
    try:
        transport.command(c.observer, dict(mode="link"))
        assert transport.line(c.observer) == "linked"
        value = dict(schema=1, fixture="original-writer-startup", plan=c.plan.sha256)
        link._send(value, time.monotonic() + 2)
        transport.command(c.observer, dict(mode="link-read"))
        assert json.loads(transport.line(c.observer)) == value
        with owner.idle_service(c.s.docker) as service:
            assert service.original is original and service.clock_witness is clock
            assert not service.used and not service.dispatch.used
            assert len(service.journal.entries) == 1
        assert service.closed and not clock.closed
    finally:
        link.close()
    channels.close()
    assert not owner.closed and not c.outer_witness.exited() and not c.observer_witness.exited()
    assert c.channel.fileno() >= 0


def test_second_intake_cannot_reconstruct_or_replay_on_same_startup(connection):
    c = connection
    c.start()
    channels, _ = c.receive()
    before = len(os.listdir("/proc/self/fd"))
    denied(c.receive)
    assert len(os.listdir("/proc/self/fd")) == before and not c.owner.closed
    assert channels.incoming.fileno() >= 0


@pytest.mark.parametrize("fault", ["pin", "plan", "clock", "baseline", "service", "publication"])
def test_changed_input_refuses_before_transport_and_consumes_one_attempt(
    connection, monkeypatch, fault
):
    c = connection
    if fault == "pin":
        object.__setattr__(
            c.declaration,
            "raw",
            c.declaration.raw.replace(b"recording-observer", b"changed-observer"),
        )
    elif fault == "plan":
        c.owner.original._plan = m.startup.plans.load_bytes(c.plan.raw, c.plan.sha256)
    elif fault == "clock":
        c.owner.clock = m.startup.plans.clock.ClockWitness(c.plan.original_clock)
    elif fault == "baseline":
        c.owner.baseline = replace(c.owner.baseline)
    elif fault == "service":
        c.owner.service_used = True
    else:
        c.owner.app_idle_publication_used = True
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Transport started"))
    denied(c.receive)
    assert c.owner.peer_channel_attempted is True
    denied(c.receive)
    assert not (c.s.root / "journal").exists()
    if fault == "clock":
        c.owner.clock.close()


@pytest.mark.parametrize("deadline", [True, "1", float("nan"), float("inf"), -1])
def test_invalid_or_expired_shared_budget_does_not_start_transport(
    connection, monkeypatch, deadline
):
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Transport started"))
    denied(lambda: connection.receive(deadline=deadline))
    assert connection.owner.peer_channel_attempted is True
    assert not connection.owner.closed


@pytest.mark.parametrize("fault", ["clock", "baseline", "attempt", "expired", "interrupt"])
def test_post_delivery_drift_closes_received_fds_and_never_constructs_journal(
    connection, monkeypatch, fault
):
    c = connection
    receive = m.bootstrap.Endpoint.receive
    received = []

    def changed(endpoint):
        result = receive(endpoint)
        received.append(result[0])
        if fault == "clock":
            c.owner.clock.original = replace(c.owner.clock.original)
        elif fault == "baseline":
            c.owner.baseline = replace(c.owner.baseline)
        elif fault == "attempt":
            c.owner.peer_channel_attempted = False
        elif fault == "expired":
            monkeypatch.setattr(m.time, "monotonic", lambda: endpoint.end)
        return result

    monkeypatch.setattr(m.bootstrap.Endpoint, "receive", changed)
    if fault == "interrupt":
        guard = m.startup.Startup.accepted_input

        def interrupted(owner):
            if received:
                raise KeyboardInterrupt("PRIVATE")
            return guard(owner)

        monkeypatch.setattr(m.startup.Startup, "accepted_input", interrupted)
    c.start()
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            c.receive()
    else:
        denied(c.receive)
    assert len(received) == 1
    assert all(s.fileno() == -1 for s in (received[0].incoming, received[0].outgoing))
    assert (
        c.channel.fileno() >= 0 and not c.outer_witness.exited() and not c.observer_witness.exited()
    )
    assert not (c.s.root / "journal").exists()


def test_initial_startup_read_is_charged_to_original_shared_budget(connection, monkeypatch):
    original = m.startup.Startup.accepted_input
    began = time.monotonic()

    def slow(owner):
        result = original(owner)
        monkeypatch.setattr(m.time, "monotonic", lambda: began + 3)
        return result

    monkeypatch.setattr(m.startup.Startup, "accepted_input", slow)
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Budget renewed"))
    denied(connection.receive)
    assert connection.owner.peer_channel_attempted and not connection.owner.closed


@pytest.mark.parametrize("fault", ["last-binding", "reused-fd", "blocking-fd"])
def test_last_recheck_cannot_return_changed_owner_or_foreign_received_descriptor(
    connection, monkeypatch, fault
):
    c = connection
    original = m.startup.Startup.accepted_input
    actual_receive = m.bootstrap.Endpoint.receive
    received, calls, replacements = [], [], []
    spare = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)

    def remember(endpoint):
        result = actual_receive(endpoint)
        received.append(result[0])
        return result

    def changed(owner):
        result = original(owner)
        calls.append(True)
        if len(calls) == 5:
            if fault == "last-binding":
                owner.baseline = replace(owner.baseline)
            elif fault == "reused-fd":
                fd = received[0].incoming.fileno()
                os.close(fd)
                os.dup2(spare, fd, inheritable=False)
                replacements.append(fd)
            else:
                received[0].incoming.setblocking(True)
        return result

    monkeypatch.setattr(m.bootstrap.Endpoint, "receive", remember)
    monkeypatch.setattr(m.startup.Startup, "accepted_input", changed)
    try:
        c.start()
        denied(c.receive)
        assert len(calls) == 5 and len(received) == 1
        assert received[0].incoming.fileno() == received[0].outgoing.fileno() == -1
        for fd in replacements:
            assert os.fstat(fd) == os.fstat(spare)  # Foreign reused fd stays caller-owned.
        assert not (c.s.root / "journal").exists()
    finally:
        for fd in replacements:
            os.close(fd)
        os.close(spare)


@pytest.mark.parametrize("state", ["unaccepted", "probe-only"])
def test_original_baseline_acceptance_required_without_creating_service(
    service_case, monkeypatch, state
):
    owner = service_case.startup
    if state == "probe-only":
        assembly.accept(service_case, read_host=False)
    monkeypatch.setattr(m.bootstrap, "Endpoint", lambda *a, **k: pytest.fail("Not service input"))
    denied(lambda: m.receive(owner, None, "0" * 64, None, None, None, None))
    assert owner.peer_channel_attempted is True
    assert not (service_case.root / "journal").exists()


def test_outer_loss_refuses_without_adopting_another_sender(connection):
    c = connection
    c.outer.kill()
    c.outer.wait(timeout=3)
    denied(c.receive)
    assert c.owner.peer_channel_attempted is True and not c.owner.clock.closed
    assert not (c.s.root / "journal").exists()
    denied(c.receive)
