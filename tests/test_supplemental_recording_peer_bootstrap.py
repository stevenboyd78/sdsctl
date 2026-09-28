"""Real three-process private descriptor transport, not installed provenance.

Two disposable peers connect to a fixture-only private pathname. Actual socket
credentials, fd passing, clocks, namespaces and pidfds are checked. Docker
cgroups/identities and independently authenticated declaration provenance remain
synthetic. No scanner/HA/Pi/Engine connection or active App grant is involved.
"""

import importlib.util
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_host_plan as plan_tests
from . import test_supplemental_recording_service_cli_channel as channel_tests

NAME = "supplemental_recording_peer_bootstrap"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(channel_tests.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)

CHILD = r"""
import json, os, socket, sys, time
from pathlib import Path
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]).parent / "src")]
import supplemental_recording_peer_bootstrap as m
m.ROOT_UID = os.geteuid()
m.links.ROOT_UID = os.geteuid()
channel = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
channel.connect(sys.argv[2])
channel.setblocking(False)
print("ready", flush=True)
first = sys.stdin.buffer.readline()
if not first:
    sys.exit(0)
config = json.loads(first)
table = {item["pid"]: item["container_id"] for item in config["identities"]}
def identity(pid, cid):
    assert table[pid] == cid
    return m.links.processes.process_identity(pid, cid, Path(f"/proc/{pid}/stat").read_text(),
        f"0::/system.slice/docker-{cid}.scope\n")
m.links.processes.read_identity = identity
plan = m.links.plans.load_bytes(config["plan"].encode(), config["sha256"])
local = identity(os.getpid(), config["local"])
remote = m.links.processes.ProcessWitness(identity(os.getppid(), config["outer"]))
other = m.links.processes.ProcessWitness(identity(config["peer_pid"], config["peer"]))
clock = m.links.clock.ClockWitness(plan.original_clock)
before = set(os.listdir("/proc/self/fd"))
endpoint = channels = link = None
try:
    endpoint = m.Endpoint(channel, plan, clock, local, remote, other, role=config["role"],
        mode="receive", declaration_sha256=config["declaration"],
        passive_retirement=config.get("passive_retirement", False))
    fault = config.get("fault")
    if fault:
        original_send = endpoint._send
        def send(value, fds=()):
            if value["phase"] == "received":
                if fault == "lost-ack":
                    return m.links.base.checksum(value)
                value = dict(value, offer_sha256="f" * 64)
            if fault == "forwarder" and value["phase"] == "request":
                # Same connected socket, DIFFERENT actual message sender.
                child = os.fork()
                if child == 0:
                    channel.send(m.links.base.encode(value))
                    os._exit(0)
                os.waitpid(child, 0)
                return m.links.base.checksum(value)
            return original_send(value, fds)
        endpoint._send = send
    channels, receipt = endpoint.receive()
    print(json.dumps(dict(received=True, context=receipt.context_sha256,
        offer=receipt.offer_sha256, identities=[list(item) for item in m._sockets(channels)],
        inheritable=[s.get_inheritable() for s in
            (channels.incoming, channels.outgoing)])), flush=True)
    for line in sys.stdin.buffer:
        command = json.loads(line)
        if command["mode"] == "finish":
            break
        if command["mode"] == "retire-passive":
            if command.get("replace_receipt"):
                receipt = m.Receipt(receipt.context_sha256, receipt.offer_sha256)
            endpoint.receive_retirement(receipt)
            print("passive release received", flush=True)
            break
        if command["mode"] == "link":
            endpoint.close()
            link = m.links.Link(channels, plan, clock, other, role=config["role"])
            print("linked", flush=True)
        if command["mode"] == "link-send":
            link._send(command["value"], time.monotonic() + 2)
            print("sent", flush=True)
        if command["mode"] == "link-read":
            value, _ = link._receive(time.monotonic() + 2)
            print(json.dumps(value), flush=True)
        if command["mode"] == "send":
            channels.outgoing.send(command["data"].encode())
            print("sent", flush=True)
        if command["mode"] == "read":
            import select
            assert select.select([channels.incoming], [], [], 2)[0]
            raw, ancillary, flags, _ = channels.incoming.recvmsg(128, socket.CMSG_SPACE(12))
            credentials = m.links.control.returns.CREDENTIALS.unpack(ancillary[0][2])
            print(json.dumps(dict(data=raw.decode(), credentials=list(credentials))), flush=True)
except m.UnconfirmedBootstrap as error:
    print(json.dumps(dict(received=False, error=str(error))), flush=True)
finally:
    if link is not None:
        link.close()
    if channels is not None:
        channels.close()
    if endpoint is not None:
        endpoint.close()
    print(json.dumps(dict(fd_delta=len(set(os.listdir("/proc/self/fd"))) - len(before),
        caller_clock_live=not clock.closed,
        caller_witnesses_live=not remote.exited() and not other.exited())), flush=True)
    # Keep the original bootstrap peer alive until the outer fixture retires it.
    sys.stdin.buffer.readline()
    clock.close()
    remote.close()
    other.close()
    channel.close()
"""


def line(process):
    assert select.select([process.stdout], [], [], 4)[0], "fixture child response timed out"
    raw = process.stdout.readline()
    assert raw, process.stderr.read().decode()
    return raw.decode().strip()


def command(process, value):
    process.stdin.write(json.dumps(value).encode() + b"\n")
    process.stdin.flush()


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    # Short path avoids UNIX_PATH_MAX regardless of pytest's full test name.
    temporary = tempfile.TemporaryDirectory(prefix="sds-peer-")
    path = str(Path(temporary.name) / "peer")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.bind(path)
    listener.listen(2)
    listener.settimeout(4)
    processes, connections, witnesses, endpoints = {}, {}, {}, []
    clocks = []
    bundles = m.links.pair()
    try:
        for role in ("writer", "observer"):
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    CHILD,
                    str(Path(m.__file__).parent),
                    path,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            processes[role] = process
            connection, _ = listener.accept()
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            connection.setblocking(False)
            connections[role] = connection
            assert line(process) == "ready"
        cids = {
            os.getpid(): "9" * 64,
            processes["writer"].pid: "a" * 64,
            processes["observer"].pid: "b" * 64,
        }

        def identity(pid, cid):
            assert cids[pid] == cid
            return m.links.processes.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )

        monkeypatch.setattr(m.links.processes, "read_identity", identity)
        identities = {pid: identity(pid, cid) for pid, cid in cids.items()}
        for role, process in processes.items():
            witnesses[role] = m.links.processes.ProcessWitness(identities[process.pid])
        origin = m.links.clock.read()
        issued = origin.boottime_ns / m.links.clock.NS
        raw = plan_tests.value()
        raw.update(
            boot=origin.boot,
            original_clock=asdict(origin) | {"namespace": list(origin.namespace)},
            deadlines=dict(
                issued_at=issued,
                ready_by=issued + 120,
                stop_by=issued + 400,
                recover_by=issued + 1500,
            ),
        )
        plan = m.links.plans.decode(raw)
        clock = m.links.clock.ClockWitness(plan.original_clock)
        clocks.append(clock)
        configs = {}
        for role, process in processes.items():
            other = "observer" if role == "writer" else "writer"
            configs[role] = dict(
                plan=plan.raw.decode(),
                sha256=plan.sha256,
                role=role,
                declaration="c" * 64,
                identities=[asdict(item) for item in identities.values()],
                local=cids[process.pid],
                outer=cids[os.getpid()],
                peer_pid=processes[other].pid,
                peer=cids[processes[other].pid],
            )

        def endpoint(role):
            other = "observer" if role == "writer" else "writer"
            result = m.Endpoint(
                connections[role],
                plan,
                clock,
                identities[os.getpid()],
                witnesses[role],
                witnesses[other],
                role=role,
                mode="deliver",
                declaration_sha256="c" * 64,
                passive_retirement=configs[role].get("passive_retirement", False),
            )
            endpoints.append(result)
            return result

        yield SimpleNamespace(
            processes=processes,
            connections=connections,
            witnesses=witnesses,
            identities=identities,
            configs=configs,
            endpoint=endpoint,
            bundles=dict(zip(("writer", "observer"), bundles, strict=True)),
            plan=plan,
            clock=clock,
        )
    finally:
        for endpoint in endpoints:
            endpoint.close()
        for process in processes.values():
            process.stdin.close()
        for process in processes.values():
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()  # Only this fixture's own original Popen.
                process.wait(timeout=2)
            process.stdout.close()
            process.stderr.close()
        for witness in witnesses.values():
            witness.close()
        for clock in clocks:
            clock.close()
        for connection in connections.values():
            connection.close()
        for bundle in bundles:
            bundle.close()
        listener.close()
        temporary.cleanup()


def refused(action):
    with pytest.raises(m.UnconfirmedBootstrap) as error:
        action()
    assert str(error.value) == m.MESSAGE


def start(case, role):
    endpoint = case.endpoint(role)
    command(case.processes[role], case.configs[role])
    return endpoint


def finish(case, role):
    command(case.processes[role], dict(mode="finish"))
    result = json.loads(line(case.processes[role]))
    assert result == dict(fd_delta=0, caller_clock_live=True, caller_witnesses_live=True)


def test_three_original_processes_deliver_both_directional_channels_and_real_credentials(case):
    for role in ("writer", "observer"):
        endpoint = start(case, role)
        expected = m._sockets(case.bundles[role])
        receipt = endpoint.deliver(case.bundles[role])
        result = json.loads(line(case.processes[role]))
        assert result == dict(
            received=True,
            context=receipt.context_sha256,
            offer=receipt.offer_sha256,
            identities=[list(item) for item in expected],
            inheritable=[False, False],
        )
        assert endpoint.used and not endpoint.failed
        # The child received duplicates, not replacements or guessed fd numbers.
        case.bundles[role].close()
        endpoint.close()
    for sender, receiver in (("writer", "observer"), ("observer", "writer")):
        command(case.processes[sender], dict(mode="send", data=sender))
        assert line(case.processes[sender]) == "sent"
        command(case.processes[receiver], dict(mode="read"))
        assert json.loads(line(case.processes[receiver])) == dict(
            data=sender, credentials=[case.processes[sender].pid, os.geteuid(), os.getegid()]
        )
    for role in ("writer", "observer"):
        finish(case, role)


def passive_release(case, *, replacement=False):
    case.configs["writer"]["passive_retirement"] = True
    endpoint = start(case, "writer")
    receipt = endpoint.deliver(case.bundles["writer"])
    assert json.loads(line(case.processes["writer"]))["received"]
    case.bundles["writer"].close()
    command(case.processes["writer"], dict(mode="retire-passive", replace_receipt=replacement))
    return endpoint, receipt


def test_passive_release_retains_original_endpoint_receipt_and_cutoff(case):
    endpoint, receipt = passive_release(case)
    cutoff, origin, objects = endpoint.end, endpoint.clock.original, endpoint.objects
    assert endpoint.send_retirement(receipt) is None
    assert line(case.processes["writer"]) == "passive release received"
    assert json.loads(line(case.processes["writer"])) == dict(
        fd_delta=0, caller_clock_live=True, caller_witnesses_live=True
    )
    assert endpoint.retirement_attempted and not endpoint.failed
    assert endpoint.end == cutoff and endpoint.objects is objects
    assert endpoint.clock.original is origin and not endpoint.clock.closed
    # A release is one-use, not an invitation to retry or admit any App action.
    refused(lambda: endpoint.send_retirement(receipt))
    assert endpoint.retirement_attempted and endpoint.failed


@pytest.mark.parametrize(
    ("interruption", "cleanup_error", "expected"),
    [
        (KeyboardInterrupt, ValueError, KeyboardInterrupt),
        (SystemExit, ValueError, SystemExit),
        (ValueError, ValueError, m.UnconfirmedBootstrap),
        (ValueError, KeyboardInterrupt, KeyboardInterrupt),
    ],
)
def test_passive_release_keeps_original_interruption_if_cleanup_also_fails(
    case, monkeypatch, interruption, cleanup_error, expected
):
    endpoint, receipt = passive_release(case)
    close = endpoint.close

    def interrupted(*args, **kwargs):
        raise interruption("PRIVATE interruption")

    def closing():
        close()
        raise cleanup_error("PRIVATE cleanup uncertainty")

    monkeypatch.setattr(endpoint, "_wait", interrupted)
    with monkeypatch.context() as cleanup:
        cleanup.setattr(endpoint, "close", closing)
        with pytest.raises(expected) as error:
            endpoint.send_retirement(receipt)
        if expected is m.UnconfirmedBootstrap:
            assert str(error.value) == m.MESSAGE and error.value.__suppress_context__
    assert endpoint.failed and endpoint.closed and not case.clock.closed


@pytest.mark.parametrize("fault", ["receipt", "nonce", "cutoff", "legacy", "not-delivered"])
def test_passive_release_sender_refuses_replacement_or_unselected_context(case, fault):
    case.configs["writer"]["passive_retirement"] = fault != "legacy"
    endpoint = start(case, "writer")
    if fault == "not-delivered":
        receipt = m.Receipt(endpoint.context_sha256, "f" * 64)
    else:
        receipt = endpoint.deliver(case.bundles["writer"])
        assert json.loads(line(case.processes["writer"]))["received"]
    if fault == "receipt":
        receipt = replace(receipt)
    elif fault == "nonce":
        endpoint.exchange_nonce = "e" * 64
    elif fault == "cutoff":
        endpoint.end += 1
    refused(lambda: endpoint.send_retirement(receipt))
    assert endpoint.failed and endpoint.closed and not case.clock.closed


@pytest.mark.parametrize(
    "fault",
    [
        "scope",
        "offer_sha256",
        "context_sha256",
        "nonce",
        "phase",
        "kind",
        "extra",
        "schema",
        "rights",
        "forwarder",
        "eof",
        "missing",
        "replacement",
    ],
)
def test_passive_release_receiver_rejects_unconfirmed_frame_without_fd_leaks(case, fault):
    endpoint, receipt = passive_release(case, replacement=fault == "replacement")
    value = endpoint._frame(
        "retire-passive-writer",
        endpoint.exchange_nonce,
        offer_sha256=receipt.offer_sha256,
        scope=m.RETIREMENT_SCOPE,
    )
    if fault in value or fault == "extra":
        value[fault] = 1.0 if fault == "schema" else "private-invalid-value"
    channel = case.connections["writer"]
    raw = m.links.base.encode(value)
    if fault == "forwarder":
        descendant = os.fork()
        if descendant == 0:
            try:
                channel.send(raw)
            finally:
                os._exit(0)
        assert os.waitpid(descendant, 0) == (descendant, 0)
    elif fault == "rights":
        channel.sendmsg(
            [raw],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", case.witnesses["writer"].fd))],
        )
    elif fault == "eof":
        channel.shutdown(socket.SHUT_WR)
    elif fault not in ("missing", "replacement"):
        channel.send(raw)
    result = json.loads(line(case.processes["writer"]))
    assert result == dict(received=False, error=m.MESSAGE)
    assert json.loads(line(case.processes["writer"])) == dict(
        fd_delta=0, caller_clock_live=True, caller_witnesses_live=True
    )
    assert not case.clock.closed and not case.witnesses["writer"].exited()
    if fault == "missing":
        # The outer is constructed just before the receiver; neither deadline
        # is renewed for the second phase.
        assert time.monotonic() >= endpoint.end


@pytest.mark.parametrize("field", ["role", "declaration", "passive_retirement"])
def test_context_mismatch_refuses_before_transferring_endpoints(case, field):
    endpoint = case.endpoint("writer")
    case.configs["writer"][field] = (
        True if field == "passive_retirement" else "observer" if field == "role" else "d" * 64
    )
    command(case.processes["writer"], case.configs["writer"])
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert endpoint.failed and endpoint.used
    case.connections["writer"].close()
    assert not json.loads(line(case.processes["writer"]))["received"]
    result = json.loads(line(case.processes["writer"]))
    assert result["fd_delta"] == 0 and result["caller_clock_live"]
    m._sockets(case.bundles["writer"])  # Originals remain borrowed, not closed.


@pytest.mark.parametrize(
    "fault", ["nonce", "kind", "context_sha256", "identities", "extra", "float-identity", "schema"]
)
def test_bad_offer_closes_received_duplicates_without_touching_originals(case, monkeypatch, fault):
    endpoint = start(case, "writer")
    original = endpoint._send

    def send(value, fds=()):
        if value["phase"] == "endpoints":
            value = dict(value)
            if fault == "float-identity":
                value["identities"] = [[float(x) for x in item] for item in value["identities"]]
            else:
                value[fault] = (
                    [] if fault == "identities" else True if fault == "schema" else "d" * 64
                )
        return original(value, fds)

    monkeypatch.setattr(endpoint, "_send", send)
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert not json.loads(line(case.processes["writer"]))["received"]
    assert json.loads(line(case.processes["writer"]))["fd_delta"] == 0
    m._sockets(case.bundles["writer"])


def test_one_use_refuses_repeated_delivery_and_keeps_borrowed_owners(case):
    endpoint = start(case, "writer")
    endpoint.deliver(case.bundles["writer"])
    assert json.loads(line(case.processes["writer"]))["received"]
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert not case.clock.closed and not case.witnesses["writer"].exited()
    m._sockets(case.bundles["writer"])
    finish(case, "writer")


def test_expired_original_bootstrap_deadline_never_renews(case, monkeypatch):
    endpoint = case.endpoint("writer")
    original = endpoint.end
    monkeypatch.setattr(m.time, "monotonic", lambda: original)
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert endpoint.end == original and endpoint.failed


def test_replaced_namespace_owner_is_not_adopted(case):
    endpoint = case.endpoint("writer")
    endpoint.clock = object()
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert not case.clock.closed


def test_delivered_endpoints_construct_unchanged_links_without_inherited_fds(case):
    # Actual Link transport/guards, not independent CliCustody or App admission.
    for role in ("writer", "observer"):
        endpoint = start(case, role)
        endpoint.deliver(case.bundles[role])
        assert json.loads(line(case.processes[role]))["received"]
        case.bundles[role].close()
        endpoint.close()
    for role in ("writer", "observer"):
        command(case.processes[role], dict(mode="link"))
        assert line(case.processes[role]) == "linked"
    for sender, receiver in (("writer", "observer"), ("observer", "writer")):
        value = dict(schema=1, fixture=sender, plan_sha256=case.plan.sha256)
        command(case.processes[sender], dict(mode="link-send", value=value))
        assert line(case.processes[sender]) == "sent"
        command(case.processes[receiver], dict(mode="link-read"))
        assert json.loads(line(case.processes[receiver])) == value
    for role in ("writer", "observer"):
        finish(case, role)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "one",
        "extra",
        "truncated",
        "duplicate",
        "reverse",
        "file-first",
        "file-second",
        "stream",
        "blocking",
        "passcred",
    ],
)
def test_malformed_received_handles_close_every_installed_duplicate(case, monkeypatch, fault):
    endpoint = start(case, "writer")
    owned_file = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    streams = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    original = endpoint._send

    def send(value, fds=()):
        if not fds:
            return original(value, fds)
        offered = list(fds)
        if fault == "missing":
            offered = []
        elif fault == "one":
            offered = offered[:1]
        elif fault in ("extra", "truncated"):
            offered += [owned_file] * (1 if fault == "extra" else 32)
        elif fault == "duplicate":
            offered[1] = offered[0]
        elif fault == "reverse":
            offered.reverse()
        elif fault in ("file-first", "file-second"):
            offered[0 if fault == "file-first" else 1] = owned_file
        elif fault == "stream":
            offered[0] = streams[0].fileno()
        elif fault == "blocking":
            case.bundles["writer"].incoming.setblocking(True)
        elif fault == "passcred":
            case.bundles["writer"].outgoing.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        data = m.links.base.encode(value)
        ancillary = [
            (socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack(f"{len(offered)}i", *offered))
        ]
        endpoint._wait(sending=True)
        endpoint.channel.sendmsg([data], ancillary if offered else [])
        return m.links.base.checksum(value)

    try:
        monkeypatch.setattr(endpoint, "_send", send)
        refused(lambda: endpoint.deliver(case.bundles["writer"]))
        assert not json.loads(line(case.processes["writer"]))["received"]
        result = json.loads(line(case.processes["writer"]))
        assert result == dict(fd_delta=0, caller_clock_live=True, caller_witnesses_live=True)
        assert not case.clock.closed
        assert all(
            s.fileno() >= 0
            for s in (case.bundles["writer"].incoming, case.bundles["writer"].outgoing, *streams)
        )
        os.fstat(owned_file)
    finally:
        os.close(owned_file)
        for stream in streams:
            stream.close()


@pytest.mark.parametrize("fault", ["lost-ack", "wrong-ack"])
def test_lost_or_wrong_ack_never_confirms_delivery_or_allows_retry(case, fault):
    case.configs["writer"]["fault"] = fault
    endpoint = start(case, "writer")
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    # Receiving descriptors is not a bilateral commit or Ready/action grant.
    assert json.loads(line(case.processes["writer"]))["received"]
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert endpoint.failed and endpoint.used and endpoint.closed
    m._sockets(case.bundles["writer"])
    finish(case, "writer")


def test_forwarded_request_from_different_process_on_original_socket_is_rejected(case):
    case.configs["writer"]["fault"] = "forwarder"
    endpoint = start(case, "writer")
    started = time.monotonic()
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert time.monotonic() - started < 1
    case.connections["writer"].close()
    assert not json.loads(line(case.processes["writer"]))["received"]
    assert json.loads(line(case.processes["writer"]))["fd_delta"] == 0


@pytest.mark.parametrize("role", ["writer", "observer"])
def test_loss_of_either_original_peer_refuses_before_descriptor_send(case, role):
    endpoint = case.endpoint("writer")
    process = case.processes[role]
    process.kill()  # Only the actual original disposable fixture process.
    process.wait(timeout=2)
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert endpoint.failed
    m._sockets(case.bundles["writer"])


@pytest.mark.parametrize("target", ["socket", "namespace", "peer"])
def test_reused_numeric_descriptor_is_not_adopted_or_closed(case, target):
    endpoint = case.endpoint("writer")
    if target == "socket":
        fd = case.connections["writer"].fileno()
    elif target == "namespace":
        fd = endpoint.handles[0][1]
    else:
        fd = case.witnesses["writer"].fd
    original = os.dup(fd)
    foreign = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.dup2(foreign, fd, inheritable=False)
        refused(lambda: endpoint.deliver(case.bundles["writer"]))
        assert m.links._identity(fd) == m.links._identity(foreign)
    finally:
        os.dup2(original, fd, inheritable=False)
        os.close(original)
        os.close(foreign)
        if target == "namespace":
            # Endpoint deliberately did not close the foreign descriptor. The
            # fixture now retires the restored original explicitly.
            os.close(fd)


def test_constructor_retention_failure_closes_every_owned_namespace(case, monkeypatch):
    original = m.links._identity
    before = set(os.listdir("/proc/self/fd"))
    seen = 0

    def identity(fd):
        nonlocal seen
        seen += 1
        if seen == 6:  # Socket + two borrowed pidfds + third namespace handle.
            raise OSError("fixture metadata failure")
        return original(fd)

    monkeypatch.setattr(m.links, "_identity", identity)
    refused(lambda: case.endpoint("writer"))
    assert set(os.listdir("/proc/self/fd")) == before
    assert not case.clock.closed and not case.witnesses["writer"].exited()


def test_original_readiness_cutoff_checked_against_boottime(case, monkeypatch):
    endpoint = case.endpoint("writer")
    sampled = case.clock.read()
    delta = int((case.plan.deadlines.ready_by + 1) * m.links.clock.NS) - sampled.boottime_ns
    advanced = replace(
        sampled,
        before_ns=sampled.before_ns + delta,
        boottime_ns=sampled.boottime_ns + delta,
        after_ns=sampled.after_ns + delta,
    )
    monkeypatch.setattr(case.clock, "read", lambda: advanced)
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert endpoint.failed and not case.clock.closed


@pytest.mark.parametrize("target", ["socket", "writer", "observer"])
def test_changed_inheritance_flags_refuse_without_repair(case, target):
    endpoint = case.endpoint("writer")
    fd = case.connections["writer"].fileno() if target == "socket" else case.witnesses[target].fd
    os.set_inheritable(fd, True)
    try:
        refused(lambda: endpoint.deliver(case.bundles["writer"]))
        assert os.get_inheritable(fd)  # Borrowed owner remains untouched.
    finally:
        os.set_inheritable(fd, False)


@pytest.mark.parametrize("fault", ["oversize", "duplicate-key", "noncanonical", "duplicate", "eof"])
def test_malformed_packet_or_eof_never_leaks_installed_rights(case, monkeypatch, fault):
    endpoint = start(case, "writer")
    original = endpoint._send

    def send(value, fds=()):
        if not fds:
            return original(value, fds)
        raw = m.links.base.encode(value)
        if fault == "oversize":
            raw += b" " * 4096
        elif fault == "duplicate-key":
            raw = raw[:-1] + b',"schema":1}'
        elif fault == "noncanonical":
            raw = b" " + raw
        elif fault == "eof":
            endpoint.channel.shutdown(socket.SHUT_WR)
            return m.links.base.checksum(value)
        endpoint._wait(sending=True)
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("2i", *fds))]
        endpoint.channel.sendmsg([raw], ancillary)
        if fault == "duplicate":
            endpoint.channel.sendmsg([raw], ancillary)
        return m.links.base.checksum(value)

    monkeypatch.setattr(endpoint, "_send", send)
    refused(lambda: endpoint.deliver(case.bundles["writer"]))
    assert not json.loads(line(case.processes["writer"]))["received"]
    assert json.loads(line(case.processes["writer"]))["fd_delta"] == 0


def test_original_socket_peer_credentials_must_match_original_recipient(case):
    endpoint = case.endpoint("writer")
    # A live different peer is not the owner of this accepted connection.
    endpoint.close()
    refused(
        lambda: m.Endpoint(
            case.connections["writer"],
            case.plan,
            case.clock,
            case.identities[os.getpid()],
            case.witnesses["observer"],
            case.witnesses["writer"],
            role="writer",
            mode="deliver",
            declaration_sha256="c" * 64,
        )
    )
    assert not case.clock.closed and all(not w.exited() for w in case.witnesses.values())


@pytest.mark.parametrize("target", ["plan", "remote", "peer", "local", "role", "declaration"])
def test_original_context_and_owners_cannot_be_replaced_with_equal_objects(case, target):
    endpoint = case.endpoint("writer")
    extra = None
    try:
        if target == "plan":
            endpoint.plan = m.links.plans.load_bytes(case.plan.raw, case.plan.sha256)
        elif target in ("remote", "peer"):
            old = getattr(endpoint, target)
            extra = m.links.processes.ProcessWitness(old.identity, retained_fd=old.fd)
            setattr(endpoint, target, extra)
        elif target == "local":
            endpoint.local = replace(endpoint.local)
        elif target == "role":
            endpoint.role = "observer"
        else:
            endpoint.declaration_sha256 = "d" * 64
        refused(lambda: endpoint.deliver(case.bundles["writer"]))
        assert not case.clock.closed
    finally:
        if extra is not None:
            assert not extra.exited()  # Rejected substitutes remain caller-owned.
            extra.close()


@pytest.mark.parametrize("deadline", [True, "123", float("nan"), float("inf"), -1])
def test_external_cutoff_must_be_finite_native_and_unexpired(case, deadline):
    before = set(os.listdir("/proc/self/fd"))
    refused(
        lambda: m.Endpoint(
            case.connections["writer"],
            case.plan,
            case.clock,
            case.identities[os.getpid()],
            case.witnesses["writer"],
            case.witnesses["observer"],
            role="writer",
            mode="deliver",
            declaration_sha256="c" * 64,
            deadline=deadline,
        )
    )
    assert set(os.listdir("/proc/self/fd")) == before
    assert not case.clock.closed


@pytest.mark.parametrize("allowance", [1, 1000])
def test_external_cutoff_only_narrows_original_handshake_window(case, allowance):
    began = time.monotonic()
    cutoff = began + allowance
    endpoint = m.Endpoint(
        case.connections["writer"],
        case.plan,
        case.clock,
        case.identities[os.getpid()],
        case.witnesses["writer"],
        case.witnesses["observer"],
        role="writer",
        mode="deliver",
        declaration_sha256="c" * 64,
        deadline=cutoff,
    )
    try:
        assert endpoint.end <= min(cutoff, time.monotonic() + 2, case.plan.lease["ready_by"])
        if allowance == 1:
            assert endpoint.end == cutoff
        else:
            assert endpoint.end < cutoff
    finally:
        endpoint.close()


def test_context_construction_is_inside_original_handshake_budget(case, monkeypatch):
    stamp = [time.monotonic()]
    original = m.links.base.encode
    advanced = False

    class Clock:
        @staticmethod
        def monotonic():
            return stamp[0]

    def encode(value):
        nonlocal advanced
        result = original(value)
        if value.get("kind") == m.KIND and not advanced:
            stamp[0] += 3
            advanced = True
        return result

    before = set(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(m, "time", Clock)
    monkeypatch.setattr(m.links.base, "encode", encode)
    refused(lambda: case.endpoint("writer"))
    assert advanced and set(os.listdir("/proc/self/fd")) == before
