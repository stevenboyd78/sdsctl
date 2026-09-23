"""Actual retained processes, transport and intents; synthetic platform metadata."""

import importlib.util
import os
import select
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_begin as begin

NAME = "supplemental_recording_retained"
SPEC = importlib.util.spec_from_file_location(NAME, Path(begin.m.__file__).with_name(NAME + ".py"))
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
    begin.layout,
    begin.tree,
    begin.routing,
    begin.projection,
    begin.binding,
    begin.directory,
    begin.prepared,
    begin.family,
    begin.actors,
    begin.calibration,
    begin.ledger,
)


explicit_domain = begin.ready_tests.explicit_domain


@contextmanager
def begun(prepared, actors, calibration, ledger, monkeypatch, *, zero_domain=None):
    peers = []
    with begin.ready_tests.attached(prepared, actors, monkeypatch, tap=peers) as (client, requests):
        ready = begin.ready_tests.capture(client, calibration, zero_domain=zero_domain)
        try:
            begin.intent(ledger, prepared)
            binding = begin.m.send_once(ready, ledger)
            message = begin.ready_tests.joined.engine.attached.begun(peers[0])
            assert message["body"]["binding"] == binding.payload()
            yield ready, peers[0], requests
        finally:
            ready.close()


def refused(action, ready):
    with pytest.raises(m.UnconfirmedContinuity) as error:
        action()
    assert str(error.value) == m.MESSAGE and "PRIVATE" not in str(error.value)
    assert ready.failed and ready.client.closed and not ready.processes.closed


def test_post_ready_observation_does_not_renew_any_dispatch_deadline(
    prepared, actors, calibration, ledger, monkeypatch
):
    with begun(prepared, actors, calibration, ledger, monkeypatch) as (ready, peer, requests):
        guard = m.Retained(ready)
        original = (guard.pins, guard.state, guard.ready_by, guard.finish_by, ledger.state)
        assert guard.check() == frozenset()
        with monkeypatch.context() as patch:
            # Explicitly synthetic elapsed-clock advance, not a real host suspend.
            patch.setattr(time, "monotonic", lambda: guard.ready_by + 1)
            assert guard.check() == frozenset()
        assert (guard.pins, guard.state, guard.ready_by, guard.finish_by, ledger.state) == original
        assert len(requests) == 5 and not select.select([peer], [], [], 0)[0]
        assert ledger.state.expected is None
        assert not any(hasattr(guard, method) for method in ("begin", "start", "attach", "restore"))


def test_original_domain_proof_remains_required_after_begin_and_child_exit(
    prepared, actors, calibration, ledger, explicit_domain, monkeypatch
):
    with begun(
        prepared, actors, calibration, ledger, monkeypatch, zero_domain=explicit_domain.proof
    ) as (ready, _peer, requests):
        guard = m.Retained(ready)
        assert guard.zero_domain is explicit_domain.proof
        original = guard.domain_sha256
        signal.pidfd_send_signal(guard.handles["native"], signal.SIGKILL)
        assert select.select([guard.handles["native"]], [], [], 3)[0]
        assert guard.check() == frozenset({"native"})
        assert guard.domain_sha256 == original and len(requests) == 5
        explicit_domain.failed = True
        refused(guard.check, ready)
        assert ready.processes.exited("native") and not ready.processes.exited("init")


@pytest.mark.parametrize("fault", ["ready_proof", "witness_proof", "digest", "clock"])
def test_post_begin_domain_or_original_clock_cannot_be_replaced(
    prepared, actors, calibration, ledger, explicit_domain, monkeypatch, fault
):
    with begun(
        prepared, actors, calibration, ledger, monkeypatch, zero_domain=explicit_domain.proof
    ) as (ready, _peer, _requests):
        guard = m.Retained(ready)
        if fault == "ready_proof":
            ready.zero_domain = None
        elif fault == "witness_proof":
            ready.processes.zero_domain = None
        elif fault == "digest":
            ready.processes.domain_sha256 = "0" * 64
        else:
            proof = explicit_domain.proof
            proof.evidence = replace(proof.evidence, original_clock=m.received.clock.read())
        refused(guard.check, ready)
        assert not ready.processes.exited("init")


@pytest.mark.parametrize("role", ["guardian", "native", "watchdog"])
def test_original_pidfd_exit_can_survive_to_buffered_receipt_without_claiming_success(
    prepared, actors, calibration, ledger, family, monkeypatch, role
):
    with begun(prepared, actors, calibration, ledger, monkeypatch) as (ready, _peer, _requests):
        guard = m.Retained(ready)
        if role == "guardian":
            family.process.stdin.close()
            family.process.wait(timeout=3)  # Owned guardian drains both owned children.
        else:
            signal.pidfd_send_signal(guard.handles[role], signal.SIGKILL)
            assert select.select([guard.handles[role]], [], [], 3)[0]
        exited = guard.check()
        assert role in exited and "init" not in exited
        assert guard.check() == exited
        assert not guard.failed and not ready.processes.failed
        assert ledger.state.expected is ledger.state.acknowledgment is None


@pytest.mark.parametrize("role", ["init", "guardian", "native", "watchdog"])
@pytest.mark.parametrize("fault", ["unreadable", "moved", "frozen"])
def test_unreadable_changed_or_frozen_actor_never_counts_as_exit(
    prepared, actors, calibration, ledger, monkeypatch, role, fault
):
    with begun(prepared, actors, calibration, ledger, monkeypatch) as (ready, _peer, _requests):
        guard = m.Retained(ready)
        actor = actors.values[m.ROLES.index(role)]
        original = m.namespace.read

        def read(pid, cid):
            if pid == actor.host_pid:
                raise OSError("PRIVATE proc failure")
            return original(pid, cid)

        if fault == "unreadable":
            monkeypatch.setattr(m.namespace, "read", read)
        elif fault == "moved":
            actors.lookup[actor.host_pid] = replace(actor, local_pid=99)
        else:
            signal.pidfd_send_signal(guard.handles[role], signal.SIGSTOP)
            until = time.monotonic() + 1
            while Path(f"/proc/{actor.host_pid}/stat").read_text().rpartition(") ")[2].split()[
                0
            ] not in ("T", "t"):
                assert time.monotonic() < until
        try:
            refused(guard.check, ready)
            assert not ready.processes.exited(role)
            refused(guard.check, ready)
        finally:
            if fault == "frozen":
                signal.pidfd_send_signal(guard.handles[role], signal.SIGCONT)


def test_actual_exit_during_proc_read_is_accepted_only_through_original_handle(
    prepared, actors, calibration, ledger, monkeypatch
):
    with begun(prepared, actors, calibration, ledger, monkeypatch) as (ready, _peer, _requests):
        guard = m.Retained(ready)
        original = m.namespace.read

        def read(pid, cid):
            if pid == actors.values[2].host_pid:
                signal.pidfd_send_signal(guard.handles["native"], signal.SIGKILL)
                assert select.select([guard.handles["native"]], [], [], 3)[0]
                raise FileNotFoundError("PRIVATE exited proc")
            return original(pid, cid)

        monkeypatch.setattr(m.namespace, "read", read)
        assert guard.check() == frozenset({"native"})
        assert ledger.state.expected is None


def test_init_exit_is_never_a_continuity_success(
    prepared, actors, calibration, ledger, monkeypatch
):
    with begun(prepared, actors, calibration, ledger, monkeypatch) as (ready, _peer, _requests):
        guard = m.Retained(ready)
        prepared.process.stdin.close()
        prepared.process.wait(timeout=3)
        refused(guard.check, ready)
        assert ready.processes.exited("init")


@pytest.mark.parametrize(
    "fault",
    [
        "tip",
        "history",
        "extra",
        "directory",
        "poisoned",
        "owner",
        "transport",
        "clock",
        "namespace",
        "ready_pin",
        "endpoint",
        "deadline",
        "descriptor",
        "failed_witness",
    ],
)
def test_retained_original_evidence_cannot_be_replaced_reopened_or_extended(
    prepared, actors, calibration, ledger, monkeypatch, fault
):
    with begun(prepared, actors, calibration, ledger, monkeypatch) as (ready, peer, requests):
        guard = m.Retained(ready)
        with monkeypatch.context() as patch:
            if fault == "tip":
                guard.claim.state = replace(guard.claim.state, sha256="e" * 64)
            elif fault == "history":
                (guard.directory / "0002.json").write_bytes(b"PRIVATE")
            elif fault == "extra":
                (guard.directory / "PRIVATE_extra").touch()
            elif fault == "directory":
                old = guard.directory.with_name("PRIVATE_old_dispatch")
                guard.directory.rename(old)
                guard.directory.mkdir(mode=0o700)
                for entry in old.iterdir():
                    copy = guard.directory / entry.name
                    copy.write_bytes(entry.read_bytes())
                    copy.chmod(0o600)
            elif fault == "poisoned":
                guard.claim.poisoned = True
            elif fault == "owner":
                guard.claim.owner = (os.getpid(), -1)
            elif fault == "transport":
                guard.channel.close()
            elif fault == "clock":
                calibration.offset += 10
            elif fault == "namespace":
                patch.setattr(
                    m.namespace.Witness, "_host_domains", staticmethod(lambda: ((1, 2), (3, 4)))
                )
            elif fault == "ready_pin":
                ready.ready_by += 1
            elif fault == "endpoint":
                guard.endpoint.close()
            elif fault == "deadline":
                patch.setattr(time, "monotonic", lambda: guard.finish_by + 1)
            elif fault == "descriptor":
                guard.processes.handles["native"] = guard.handles["watchdog"]
            else:
                guard.processes.failed = True
            try:
                refused(guard.check, ready)
                assert peer.recv(1) == b"" and len(requests) == 5
                assert ledger.state.expected is None
            finally:
                if fault == "descriptor":
                    guard.processes.handles = dict(guard.handles)


def test_constructor_requires_actual_consumed_begin(prepared, actors, calibration, monkeypatch):
    with begin.ready_tests.attached(prepared, actors, monkeypatch) as (client, _requests):
        ready = begin.ready_tests.capture(client, calibration)
        try:
            refused(lambda: m.Retained(ready), ready)
            assert not client.attachment.begun
        finally:
            ready.close()
