"""Real retained listeners join original paired comparisons and peer termination.

The executing children, sockets, pidfds, timers and signals are real and owned.
Image/root/Engine/cgroup/command eligibility remains explicitly synthetic, as in
the original joined fixture. No installed input provenance or active App grant.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_peer_delivery as original

m, lifetime = original.m, original.lifetime
layout, image_umask, supervised = original.layout, original.image_umask, original.supervised
image, configured, pair = original.image, original.configured, original.pair
inputs, custody, short_budget = original.inputs, original.custody, original.short_budget
helper = lifetime.helper
pytestmark = original.pytestmark


@pytest.fixture
def peer_processes(monkeypatch):
    temporary = tempfile.TemporaryDirectory(prefix="sds-listener-join-")
    root = Path(temporary.name)
    paths, children = [], []
    popen = subprocess.Popen
    # Only defer the fixture's first connection until both original witnesses
    # and runtime custody exist. No production child or command is substituted.
    child_code = lifetime.transport.CHILD.replace(
        "channel.connect(sys.argv[2])",
        'print("waiting", flush=True)\nsys.stdin.buffer.readline()\nchannel.connect(sys.argv[2])',
    )
    assert child_code != lifetime.transport.CHILD

    def spawn(argv, **kwargs):
        assert argv == [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"]
        assert len(children) < 2 and kwargs["stdin"] is subprocess.PIPE
        assert kwargs.get("stdout") is kwargs.get("stderr") is None
        assert kwargs.get("bufsize", 0) == 0
        path = root / str(len(children))
        path.mkdir(mode=0o700)
        paths.append(path)
        child = popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                child_code,
                str(Path(m.__file__).parent),
                str(path / m.listeners.NAME),
            ],
            **(kwargs | dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)),
        )
        children.append(child)
        assert lifetime.transport.line(child) == "waiting"
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        yield SimpleNamespace(paths=paths, children=children)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()  # This fixture's original disposable process only.
                child.wait(timeout=2)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
        temporary.cleanup()


@pytest.fixture
def joined(custody, pair, inputs, peer_processes, monkeypatch):
    monkeypatch.setattr(m.listeners, "ROOT_UID", os.geteuid())
    owners, channels = {}, []
    source = None
    try:
        end = min(time.monotonic() + 2, pair.plan.lease["ready_by"])
        for role, path in zip(("writer", "observer"), peer_processes.paths, strict=True):
            owner = m.listeners.Listener(path, pair.witnesses[role], deadline=end)
            owners[role] = owner
            pair.children[role].stdin.write(b"connect\n")
            pair.children[role].stdin.flush()
            assert lifetime.transport.line(pair.children[role]) == "ready"
            channels.append(owner.accept())
        source = lifetime.joined.__wrapped__(custody, pair, inputs, channels, monkeypatch)
        value = next(source)
        value.listeners = owners
        value.listener_end = end
        yield value
    finally:
        if source is not None:
            source.close()
        if custody.armed_watch is not None:
            custody.armed_watch.close()
        for owner in owners.values():
            owner.close()


def call(joined, *, writer=None, observer=None):
    return m.deliver_retained(
        joined.custody,
        joined.watch,
        joined.local,
        joined.listeners["writer"] if writer is None else writer,
        joined.listeners["observer"] if observer is None else observer,
    )


def test_both_retained_listeners_bound_every_runtime_collection_and_descriptor_handoff(
    joined, monkeypatch
):
    ends, checked = [], []
    collect = m.termination.peers.PeerRuntimePair._collect_before
    construct = m.bootstrap.Endpoint.__init__
    recheck = m.listeners.Listener.recheck

    def observe(pair, end):
        ends.append(end)
        return collect(pair, end)

    def endpoint(value, *args, **kwargs):
        ends.append(kwargs["deadline"])
        construct(value, *args, **kwargs)

    def listener(owner):
        checked.append(owner)
        return recheck(owner)

    monkeypatch.setattr(m.termination.peers.PeerRuntimePair, "_collect_before", observe)
    monkeypatch.setattr(m.bootstrap.Endpoint, "__init__", endpoint)
    monkeypatch.setattr(m.listeners.Listener, "recheck", listener)
    original.start(joined)
    result = call(joined)
    peers = original.results(joined)
    assert result.plan_sha256 == joined.pair.plan.sha256
    assert all(value["received"] for value in peers.values())
    assert len(ends) == 4 and set(ends) == {joined.listener_end}
    for owner in joined.listeners.values():
        assert checked.count(owner) >= 6 and not owner.closed
        assert owner.channel.fileno() >= 0 and owner.listener.fileno() == -1
    assert not joined.watch.closed and not joined.watch.finished
    for role in ("writer", "observer"):
        lifetime.transport.command(joined.pair.children[role], dict(mode="link"))
        assert lifetime.transport.line(joined.pair.children[role]) == "linked"


@pytest.mark.parametrize(
    "fault", ["path", "expired", "closed", "socket-wrapper", "wrong-witness", "deadline"]
)
def test_listener_uncertainty_before_first_collection_stops_only_original_peers(
    joined, monkeypatch, fault
):
    owner = joined.listeners["observer"]
    if fault == "path":
        (owner.root / "unrelated").touch()
    elif fault == "expired":
        clock = m.listeners.time

        class Expired:
            @staticmethod
            def monotonic():
                return owner.deadline

        monkeypatch.setattr(m.listeners, "time", Expired)
        assert clock.monotonic() < owner.deadline
    elif fault == "closed":
        owner.close()
    elif fault == "socket-wrapper":
        owner.channel = joined.listeners["writer"].channel
    elif fault == "wrong-witness":
        owner.peer = joined.pair.witnesses["writer"]
    else:
        owner.deadline += 2

    def no_pair():
        pytest.fail("Unconfirmed listener created transferable descriptors")

    monkeypatch.setattr(m.bootstrap.links, "pair", no_pair)
    original.refused(lambda: call(joined))
    assert joined.watch.closed and joined.pair.obj.channel_delivery_attempted
    lifetime.termination.exited(joined.pair)
    assert all(joined.pair.counts[role]["container"] == 4 for role in ("writer", "observer"))


@pytest.mark.parametrize("after_role", ["writer", "observer"])
def test_path_change_between_handoffs_or_at_end_cancels_original_watch(
    joined, monkeypatch, after_role
):
    deliver = m.bootstrap.Endpoint.deliver

    def change(endpoint, channels):
        receipt = deliver(endpoint, channels)
        if endpoint.role == after_role:
            (joined.listeners["writer"].root / "foreign").touch()
        return receipt

    monkeypatch.setattr(m.bootstrap.Endpoint, "deliver", change)
    original.start(joined)
    original.refused(lambda: call(joined))
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)
    assert all(joined.pair.counts[role]["container"] == 6 for role in ("writer", "observer"))


@pytest.mark.parametrize("substitute", ["socket", "same-listener"])
def test_raw_socket_and_one_listener_cannot_substitute_for_two_original_owners(joined, substitute):
    replacement = (
        joined.connections["writer"] if substitute == "socket" else joined.listeners["observer"]
    )
    original.refused(lambda: call(joined, writer=replacement))
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)


def test_listener_drift_after_final_runtime_collection_cannot_return_success(joined, monkeypatch):
    collect = m.termination.peers.PeerRuntimePair._collect_before
    calls = []

    def change(pair, end):
        collect(pair, end)
        calls.append(end)
        if len(calls) == 2:
            (joined.listeners["observer"].root / "foreign").touch()

    monkeypatch.setattr(m.termination.peers.PeerRuntimePair, "_collect_before", change)
    original.start(joined)
    original.refused(lambda: call(joined))
    assert len(calls) == 2 and joined.watch.closed
    lifetime.termination.exited(joined.pair)
    assert all(joined.pair.counts[role]["container"] == 8 for role in ("writer", "observer"))


def test_listener_replay_does_not_rearm_or_cancel_successful_original_watch(joined):
    original.start(joined)
    call(joined)
    original.results(joined)
    original.refused(lambda: call(joined))
    assert not joined.watch.closed and all(not w.exited() for w in joined.pair.witnesses.values())
