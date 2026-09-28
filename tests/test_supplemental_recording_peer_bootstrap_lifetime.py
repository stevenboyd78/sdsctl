"""Join original runtime pair, termination and delivered Link in one fixture.

Actual three processes, namespaces, clocks, pidfds, private SEQPACKET sockets,
descriptor delivery and independent termination. Full paired file/environment
reads run, but their image, cgroup, command, root and kernel privilege facts are
synthetic; fixture source is NOT the executing image. No installed provenance,
fixed active entrypoint, authenticated App grant, journal or recovery is claimed.
"""

import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_bootstrap as transport
from . import test_supplemental_recording_peer_termination as termination

m, stop = transport.m, termination.m
layout, image_umask, supervised = (
    termination.layout,
    termination.image_umask,
    termination.supervised,
)
image, configured, pair = termination.image, termination.configured, termination.pair
inputs, custody, short_budget = termination.inputs, termination.custody, termination.short_budget
pytestmark = termination.pytestmark


@pytest.fixture
def peer_processes(monkeypatch):
    # Replace ONLY the two inert, owned test children, never a live process or
    # product launcher. Runtime metadata was explicitly synthetic already.
    temporary = tempfile.TemporaryDirectory(prefix="sds-joined-")
    path = str(Path(temporary.name) / "peer")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    listener.bind(path)
    listener.listen(2)
    listener.settimeout(4)
    original = subprocess.Popen
    connections, children = [], []

    def spawn(argv, **kwargs):
        assert argv == [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"]
        assert len(children) < 2 and kwargs["stdin"] is subprocess.PIPE
        process = original(
            [sys.executable, "-I", "-B", "-c", transport.CHILD, str(Path(m.__file__).parent), path],
            **kwargs,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        children.append(process)
        channel, _ = listener.accept()
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        channel.setblocking(False)
        connections.append(channel)
        assert transport.line(process) == "ready"
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        yield connections
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()  # Only this fixture's original owned Popen.
                child.wait(timeout=2)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
        for channel in connections:
            channel.close()
        listener.close()
        temporary.cleanup()


@pytest.fixture
def helper(peer_processes, supervised, image, configured, monkeypatch, request, short_budget):
    source = termination.helper.__wrapped__(
        supervised, image, configured, monkeypatch, request, short_budget
    )
    try:
        yield next(source)
    finally:
        with pytest.raises(StopIteration):
            next(source)


@pytest.fixture
def joined(custody, pair, inputs, peer_processes, monkeypatch):
    assert m.links.processes is stop.peers.launch.engine.dispatch.process
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    original_identity = m.links.processes.read_identity
    outer = m.links.processes.process_identity(
        os.getpid(),
        "7" * 64,
        Path("/proc/self/stat").read_text(),
        f"0::/system.slice/docker-{'7' * 64}.scope\n",
    )

    def identity(pid, cid):
        if pid == os.getpid():
            assert cid == outer.container_id
            return m.links.processes.process_identity(
                pid,
                cid,
                Path(f"/proc/{pid}/stat").read_text(),
                f"0::/system.slice/docker-{cid}.scope\n",
            )
        return original_identity(pid, cid)

    monkeypatch.setattr(m.links.processes, "read_identity", identity)
    watch = stop.arm(custody, scope=stop.SCOPE)
    bundles = dict(zip(("writer", "observer"), m.links.pair(), strict=True))
    connections = dict(zip(("writer", "observer"), peer_processes, strict=True))
    endpoints = []
    configs = {}
    for role in ("writer", "observer"):
        other = "observer" if role == "writer" else "writer"
        configs[role] = dict(
            plan=pair.plan.raw.decode(),
            sha256=pair.plan.sha256,
            role=role,
            declaration=pair.expectations.sha256,
            identities=[asdict(outer), *(asdict(w.identity) for w in pair.witnesses.values())],
            local=pair.witnesses[role].identity.container_id,
            outer=outer.container_id,
            peer_pid=pair.witnesses[other].identity.pid,
            peer=pair.witnesses[other].identity.container_id,
        )

    def deliver(role, *, fault=None):
        other = "observer" if role == "writer" else "writer"
        endpoint = m.Endpoint(
            connections[role],
            pair.plan,
            inputs[1],
            outer,
            pair.witnesses[role],
            pair.witnesses[other],
            role=role,
            mode="deliver",
            declaration_sha256=pair.expectations.sha256,
        )
        endpoints.append(endpoint)
        transport.command(pair.children[role], configs[role] | ({"fault": fault} if fault else {}))
        receipt = endpoint.deliver(bundles[role])
        result = json.loads(transport.line(pair.children[role]))
        assert result["received"] and result["context"] == receipt.context_sha256
        assert result["offer"] == receipt.offer_sha256
        bundles[role].close()  # No lingering outer duplicate before future grants.
        endpoint.close()
        return receipt

    try:
        yield SimpleNamespace(
            watch=watch,
            deliver=deliver,
            endpoints=endpoints,
            bundles=bundles,
            configs=configs,
            pair=pair,
            custody=custody,
            local=outer,
            connections=connections,
        )
    finally:
        watch.close()  # Cancellation, NOT success, disarm or permission to recover.
        for endpoint in endpoints:
            endpoint.close()
        for bundle in bundles.values():
            bundle.close()


def test_full_pair_then_delivery_then_unchanged_links_share_original_supervised_peers(joined):
    pair = joined.pair
    original = (
        pair.plan,
        pair.expectations,
        pair.qualifiers["writer"],
        pair.qualifiers["observer"],
    )
    assert all(pair.counts[role]["container"] == 4 for role in ("writer", "observer"))
    for role in ("writer", "observer"):
        joined.deliver(role)
    for role in ("writer", "observer"):
        transport.command(pair.children[role], dict(mode="link"))
        assert transport.line(pair.children[role]) == "linked"
    pair.obj()  # Both full reads AGAIN after bootstrap; no prior success cache.
    assert all(pair.counts[role]["container"] == 6 for role in ("writer", "observer"))
    assert all(
        a is b
        for a, b in zip(
            original,
            (pair.plan, pair.expectations, pair.qualifiers["writer"], pair.qualifiers["observer"]),
            strict=True,
        )
    )
    assert joined.watch.identities == tuple(w.identity for w in pair.witnesses.values())
    assert joined.watch.deadline_ns == joined.custody.deadline_ns
    message = dict(schema=1, fixture="joined-original-peer-transport", plan=pair.plan.sha256)
    transport.command(pair.children["writer"], dict(mode="link-send", value=message))
    assert transport.line(pair.children["writer"]) == "sent"
    transport.command(pair.children["observer"], dict(mode="link-read"))
    assert json.loads(transport.line(pair.children["observer"])) == message
    joined.watch.close()
    termination.exited(pair)


@pytest.mark.parametrize("role", ["writer", "observer"])
def test_peer_loss_after_both_deliveries_stops_the_other_original_without_outer_tick(joined, role):
    pair = joined.pair
    for selected in ("writer", "observer"):
        joined.deliver(selected)
    pair.children[role].kill()
    termination.exited(pair)  # Real watcher acts independently; no service tick.
    assert select.select([joined.watch.fd], [], [], 2)[0]
    outcome = joined.watch.finish()
    assert outcome.returncode == 11
    assert outcome.writer is pair.qualifiers["writer"].init
    assert outcome.observer is pair.qualifiers["observer"].init


def test_one_sided_lost_ack_keeps_original_termination_and_no_replacement(joined):
    transport.refused(lambda: joined.deliver("writer", fault="lost-ack"))
    endpoint = joined.endpoints[0]
    assert endpoint.failed and endpoint.used
    assert json.loads(transport.line(joined.pair.children["writer"]))["received"]
    assert not joined.watch.closed and len(joined.endpoints) == 1
    joined.watch.close()
    termination.exited(joined.pair)


def test_runtime_change_after_first_delivery_refuses_second_role_and_cancels_originals(joined):
    joined.deliver("writer")
    joined.pair.containers["observer"]["State"]["Paused"] = True
    with pytest.raises(stop.peers.launch.UnconfirmedHostLaunch):
        joined.pair.obj()
    assert len(joined.endpoints) == 1
    joined.watch.close()
    termination.exited(joined.pair)


def test_original_partner_loss_during_delivery_never_reaches_descriptor_send(joined, monkeypatch):
    original = m.Endpoint._send

    def send(endpoint, value, fds=()):
        if endpoint.mode == "deliver" and value["phase"] == "endpoints":
            signal.pidfd_send_signal(joined.pair.witnesses["observer"].fd, signal.SIGKILL)
            joined.pair.children["observer"].wait(timeout=2)
        return original(endpoint, value, fds)

    monkeypatch.setattr(m.Endpoint, "_send", send)
    transport.refused(lambda: joined.deliver("writer"))
    termination.exited(joined.pair)
    assert len(joined.endpoints) == 1 and joined.endpoints[0].failed
