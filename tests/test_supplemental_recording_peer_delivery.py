"""Original paired runtime/termination/descriptor join; no installed authority.

Same explicitly synthetic image/Engine/command/source eligibility as the joined
fixture. Processes, clocks, credentials, descriptor passing and signals are real.
No live scanner, Docker, Home Assistant, App grant, journal or recovery is used.
"""

import importlib.util
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from . import test_supplemental_recording_peer_bootstrap_lifetime as lifetime
from . import test_supplemental_recording_peer_listener as listener_tests

NAME = "supplemental_recording_peer_delivery"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(lifetime.m.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
assert m.bootstrap is lifetime.m and m.termination is lifetime.stop
assert m.listeners is listener_tests.m
layout, image_umask, supervised = lifetime.layout, lifetime.image_umask, lifetime.supervised
image, configured, pair = lifetime.image, lifetime.configured, lifetime.pair
inputs, custody, short_budget = lifetime.inputs, lifetime.custody, lifetime.short_budget
peer_processes, helper, joined = lifetime.peer_processes, lifetime.helper, lifetime.joined
pytestmark = lifetime.pytestmark


def call(joined, *, watch=None, local=None, writer=None, observer=None):
    return m.deliver(
        joined.custody,
        joined.watch if watch is None else watch,
        joined.local if local is None else local,
        joined.connections["writer"] if writer is None else writer,
        joined.connections["observer"] if observer is None else observer,
    )


def start(joined, **faults):
    for role in ("writer", "observer"):
        extra = {"fault": faults[role]} if role in faults else {}
        lifetime.transport.command(joined.pair.children[role], joined.configs[role] | extra)


def refused(action):
    with pytest.raises(m.UnconfirmedDelivery) as error:
        action()
    assert str(error.value) == m.MESSAGE


def results(joined):
    return {
        role: json.loads(lifetime.transport.line(child))
        for role, child in joined.pair.children.items()
    }


def test_coordinator_delivers_once_brackets_with_both_full_reads_and_retires_outer_fds(
    joined,
    monkeypatch,
):
    created = []
    original = m.bootstrap.links.pair

    def sockets():
        result = original()
        created.extend(result)
        return result

    monkeypatch.setattr(m.bootstrap.links, "pair", sockets)
    start(joined)
    result = call(joined)
    peers = results(joined)
    assert result.plan_sha256 == joined.pair.plan.sha256
    assert result.declaration_sha256 == joined.pair.expectations.sha256
    assert joined.pair.obj.channel_delivery_attempted is True
    assert all(joined.pair.counts[role]["container"] == 8 for role in ("writer", "observer"))
    for role in ("writer", "observer"):
        receipt = getattr(result, role)
        assert peers[role]["received"] and peers[role]["context"] == receipt.context_sha256
        assert peers[role]["offer"] == receipt.offer_sha256
    assert len(created) == 2
    assert all(s.fileno() == -1 for bundle in created for s in (bundle.incoming, bundle.outgoing))
    assert not joined.watch.closed and not joined.watch.finished
    assert not joined.custody.closed and not joined.custody.clock.closed
    assert all(channel.fileno() >= 0 for channel in joined.connections.values())
    # Rejected replay must not adopt/cancel an otherwise still-owned watcher.
    refused(lambda: call(joined))
    assert not joined.watch.closed
    assert all(not witness.exited() for witness in joined.pair.witnesses.values())
    for role in ("writer", "observer"):
        lifetime.transport.command(joined.pair.children[role], dict(mode="link"))
        assert lifetime.transport.line(joined.pair.children[role]) == "linked"


@pytest.mark.parametrize("role", ["writer", "observer"])
@pytest.mark.parametrize("fault", ["lost-ack", "wrong-ack"])
def test_any_unconfirmed_delivery_cancels_both_original_peers_without_retry(joined, role, fault):
    start(joined, **{role: fault})
    refused(lambda: call(joined))
    assert joined.watch.closed and joined.pair.obj.channel_delivery_attempted
    lifetime.termination.exited(joined.pair)
    assert not joined.custody.closed and not joined.custody.clock.closed
    assert all(channel.fileno() >= 0 for channel in joined.connections.values())
    refused(lambda: call(joined))


@pytest.mark.parametrize("role", ["writer", "observer"])
def test_runtime_changed_before_delivery_never_sends_descriptors(joined, monkeypatch, role):
    def no_pair():
        pytest.fail("failed qualification created channel descriptors")

    monkeypatch.setattr(m.bootstrap.links, "pair", no_pair)
    joined.pair.containers[role]["State"]["Paused"] = True
    refused(lambda: call(joined))
    assert joined.pair.obj.failed and joined.watch.closed
    lifetime.termination.exited(joined.pair)


def test_runtime_changed_during_delivery_fails_final_full_comparison(joined, monkeypatch):
    original = m.bootstrap.Endpoint.deliver

    def deliver(endpoint, channels):
        receipt = original(endpoint, channels)
        if endpoint.role == "observer":
            joined.pair.containers["writer"]["State"]["Paused"] = True
        return receipt

    monkeypatch.setattr(m.bootstrap.Endpoint, "deliver", deliver)
    start(joined)
    refused(lambda: call(joined))
    assert joined.pair.obj.failed and joined.watch.closed
    assert all(value["received"] for value in results(joined).values())
    lifetime.termination.exited(joined.pair)


def test_loss_during_delivery_is_not_replaced_or_requalified(joined, monkeypatch):
    original = m.bootstrap.Endpoint._send

    def send(endpoint, value, fds=()):
        if fds:
            signal.pidfd_send_signal(joined.pair.witnesses["observer"].fd, signal.SIGKILL)
            joined.pair.children["observer"].wait(timeout=2)
        return original(endpoint, value, fds)

    monkeypatch.setattr(m.bootstrap.Endpoint, "_send", send)
    start(joined)
    refused(lambda: call(joined))
    assert joined.watch.closed and joined.pair.obj.channel_delivery_attempted
    lifetime.termination.exited(joined.pair)
    assert all(joined.pair.counts[role]["container"] == 6 for role in ("writer", "observer"))


def test_two_roles_cannot_share_one_bootstrap_socket(joined):
    refused(lambda: call(joined, observer=joined.connections["writer"]))
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)


@pytest.mark.parametrize("field", ["watch", "local"])
def test_unrelated_input_is_refused_without_cancelling_valid_borrowed_watch(joined, field):
    refused(lambda: call(joined, **{field: object()}))
    assert joined.pair.obj.channel_delivery_attempted is False
    assert not joined.watch.closed and all(not w.exited() for w in joined.pair.witnesses.values())


def test_all_comparisons_and_deliveries_share_one_unchanged_cutoff(joined, monkeypatch):
    deadlines = []
    collect = m.termination.peers.PeerRuntimePair._collect_before
    construct = m.bootstrap.Endpoint.__init__

    def check(pair, end):
        deadlines.append(end)
        return collect(pair, end)

    def capture(endpoint, *args, **kwargs):
        deadlines.append(kwargs["deadline"])
        construct(endpoint, *args, **kwargs)
        assert endpoint.end <= kwargs["deadline"]

    monkeypatch.setattr(m.termination.peers.PeerRuntimePair, "_collect_before", check)
    monkeypatch.setattr(m.bootstrap.Endpoint, "__init__", capture)
    start(joined)
    call(joined)
    assert len(deadlines) == 4 and len(set(deadlines)) == 1
    assert deadlines[0] <= joined.pair.plan.lease["ready_by"]


def test_failure_closes_only_original_owned_resources_and_keeps_borrowed_connections(joined):
    before = {
        connection.fileno(): os.fstat(connection.fileno())
        for connection in joined.connections.values()
    }
    start(joined, observer="wrong-ack")
    refused(lambda: call(joined))
    for fd, identity in before.items():
        assert os.fstat(fd) == identity
    assert not joined.custody.clock.closed


def test_equal_reconstructed_watch_is_not_the_original_arm_result(joined):
    clone = object.__new__(m.termination.Watch)
    clone.__dict__.update(joined.watch.__dict__)
    refused(lambda: call(joined, watch=clone))
    assert not joined.watch.closed and not clone.closed
    assert joined.custody.armed_watch is joined.watch
    assert joined.pair.obj.channel_delivery_attempted is False


def test_retirement_never_closes_foreign_reused_socket_descriptor(joined, monkeypatch):
    original = m.bootstrap.Endpoint.deliver
    retained = []
    foreign = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)

    def deliver(endpoint, channels):
        receipt = original(endpoint, channels)
        fd = channels.outgoing.fileno()
        retained.append((fd, os.dup(fd)))
        os.dup2(foreign, fd, inheritable=False)
        return receipt

    monkeypatch.setattr(m.bootstrap.Endpoint, "deliver", deliver)
    try:
        start(joined)
        refused(lambda: call(joined))
        assert len(retained) == 1
        assert m.bootstrap.links._identity(retained[0][0]) == m.bootstrap.links._identity(foreign)
        assert joined.watch.closed
        lifetime.termination.exited(joined.pair)
    finally:
        for replaced, duplicate in retained:
            os.close(replaced)
            os.close(duplicate)
        os.close(foreign)


def test_expired_shared_cutoff_prevents_second_collection_and_cancels_both(joined, monkeypatch):
    original = m.bootstrap.Endpoint.deliver

    def deliver(endpoint, channels):
        receipt = original(endpoint, channels)
        if endpoint.role == "writer":
            monkeypatch.setattr(m, "SECONDS", 999)  # Cannot renew an already captured cutoff.
            real_time = m.time

            class ExpiredClock:
                @staticmethod
                def monotonic():
                    return endpoint.end

            monkeypatch.setattr(m, "time", ExpiredClock)
            assert real_time.monotonic() < endpoint.end
        return receipt

    monkeypatch.setattr(m.bootstrap.Endpoint, "deliver", deliver)
    start(joined)
    refused(lambda: call(joined))
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)
    assert all(joined.pair.counts[role]["container"] == 6 for role in ("writer", "observer"))


def test_missing_second_ack_and_cleanup_error_still_retire_other_owned_side(joined, monkeypatch):
    created = []
    original_pair = m.bootstrap.links.pair
    original_close = m.bootstrap.Endpoint.close

    def sockets():
        result = original_pair()
        created.extend(result)
        return result

    def close(endpoint):
        original_close(endpoint)
        if endpoint.role == "observer":
            raise OSError("fixture cleanup error after original handles retired")

    monkeypatch.setattr(m.bootstrap.links, "pair", sockets)
    monkeypatch.setattr(m.bootstrap.Endpoint, "close", close)
    start(joined, observer="wrong-ack")
    refused(lambda: call(joined))
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)
    assert all(
        s.fileno() == -1 for channels in created for s in (channels.incoming, channels.outgoing)
    )


def test_process_interrupt_cancels_originals_then_reraises_not_success(joined, monkeypatch):
    def interrupted(_endpoint, _channels):
        raise KeyboardInterrupt

    monkeypatch.setattr(m.bootstrap.Endpoint, "deliver", interrupted)
    start(joined)
    with pytest.raises(KeyboardInterrupt):
        call(joined)
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)


def test_initial_input_checks_do_not_receive_an_extra_budget(joined, monkeypatch):
    original = joined.custody._guard
    clock = m.time
    stamp = [clock.monotonic()]
    calls = 0

    class InitialClock:
        @staticmethod
        def monotonic():
            return stamp[0]

    def slow_guard():
        nonlocal calls
        original()
        calls += 1
        if calls == 1:
            stamp[0] += 3

    monkeypatch.setattr(m, "time", InitialClock)
    monkeypatch.setattr(joined.custody, "_guard", slow_guard)
    refused(lambda: call(joined))
    assert joined.watch.closed
    lifetime.termination.exited(joined.pair)
    assert all(joined.pair.counts[role]["container"] == 4 for role in ("writer", "observer"))
