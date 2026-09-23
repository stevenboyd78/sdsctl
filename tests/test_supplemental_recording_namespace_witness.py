"""Real owned pidfds/children with explicitly synthetic Docker namespace facts.

No Docker, privileged namespace creation or existing process is used. Pure proc
decoding tests cover namespace/cgroup fields separately; these fixtures qualify
retention, liveness/exit distinction and cleanup, not installed host auth.
"""

import os
import select
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_namespace as namespace
from . import test_supplemental_recording_probe as probe

m = namespace.m
family = probe.family


@pytest.fixture
def mapped(family, monkeypatch):
    init = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys;sys.stdin.read()"], stdin=subprocess.PIPE
    )
    initial = None
    try:
        init_ticks = int(Path(f"/proc/{init.pid}/stat").read_text().rpartition(") ")[2].split()[19])
        identity = m.host_process.ProcessIdentity(init.pid, init_ticks, namespace.CID)
        monkeypatch.setattr(m.host_process, "read_identity", lambda *_: identity)
        initial = m.host_process.ProcessWitness(identity)
        points = [family.expected.guardian, family.expected.native, family.expected.watchdog]
        actors = (
            m.Actor(init.pid, 1, os.getpid(), init_ticks, namespace.CID, namespace.NS),
            *(
                m.Actor(
                    item.pid,
                    index + 2,
                    os.getpid() if index == 0 else points[0].pid,
                    item.start_ticks,
                    namespace.CID,
                    namespace.NS,
                )
                for index, item in enumerate(points)
            ),
        )
        lookup = {actor.host_pid: actor for actor in actors}
        fixture = SimpleNamespace(
            init=init, initial=initial, actors=actors, lookup=lookup, calls=[], hook=None
        )

        def read(pid, cid):
            assert cid == namespace.CID and pid in lookup
            fixture.calls.append(pid)
            if fixture.hook is not None:
                fixture.hook(pid)
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
            m.require(fields[0] in ("R", "S", "I") and int(fields[19]) == lookup[pid].start_ticks)
            return lookup[pid]

        monkeypatch.setattr(m, "read", read)
        monkeypatch.setattr(m.Witness, "_host_domains", staticmethod(lambda: namespace.NS[3:]))
        yield fixture
    finally:
        if initial is not None:
            initial.close()
        init.stdin.close()
        init.wait(timeout=3)
        assert init.returncode == 0


def bind(mapped):
    return m.Witness(mapped.initial, mapped.actors[1].host_pid, namespace.reports(mapped.actors))


def test_actual_owned_process_handles_remain_distinct_and_caller_init_stays_open(mapped):
    before = len(os.listdir("/proc/self/fd"))
    witness = bind(mapped)
    try:
        assert len(os.listdir("/proc/self/fd")) == before + 4
        assert witness.refresh() == mapped.actors
        assert set(witness.handles) == {"init", "guardian", "native", "watchdog"}
        assert all(not witness.exited(role) for role in witness.handles)
        assert witness.handles["init"] != mapped.initial.fd
    finally:
        witness.close()
    assert len(os.listdir("/proc/self/fd")) == before and not mapped.initial.exited()


@pytest.mark.parametrize("role", ["init", "guardian", "native", "watchdog"])
def test_actual_exit_is_not_inferred_from_stale_mapping(mapped, family, role):
    witness = bind(mapped)
    try:
        if role == "init":
            mapped.init.stdin.close()
            mapped.init.wait(timeout=3)
        elif role == "guardian":
            family.process.stdin.close()  # Owned parent drains/reaps both real children.
            family.process.wait(timeout=3)
        else:
            signal.pidfd_send_signal(witness.handles[role], signal.SIGKILL)
            assert select.select([witness.handles[role]], [], [], 3)[0]
        assert witness.exited(role)
        with pytest.raises(m.UnconfirmedNamespace):
            witness.refresh()
        assert witness.failed and witness.exited(role)
        # Child/guardian exit never stands in for our separate container-init fixture.
        assert witness.exited("init") is (role == "init")
    finally:
        witness.close()


@pytest.mark.parametrize("role", range(4))
def test_changed_identity_refuses_fresh_claim_but_retains_exit_handles(mapped, role):
    witness = bind(mapped)
    try:
        actor = mapped.actors[role]
        mapped.lookup[actor.host_pid] = replace(actor, local_pid=99)
        with pytest.raises(m.UnconfirmedNamespace):
            witness.refresh()
        assert witness.failed and not witness.closed and len(witness.handles) == 4
        mapped.lookup[actor.host_pid] = actor
        with pytest.raises(m.UnconfirmedNamespace):
            witness.refresh()  # No resurrection by rereading a now-good result.
        assert all(not witness.exited(name) for name in witness.handles)
    finally:
        witness.close()


@pytest.mark.parametrize("position", [1, 2, 3, 4, 5, 6, 7, 8])
def test_failed_bind_closes_each_partial_set_of_retained_descriptors(mapped, position):
    def fail(_pid):
        if len(mapped.calls) == position:
            raise OSError("PRIVATE proc failure")

    mapped.hook = fail
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedNamespace) as caught:
        bind(mapped)
    assert str(caught.value) == m.MESSAGE and len(os.listdir("/proc/self/fd")) == before
    assert not mapped.initial.exited()


def test_missing_reported_child_does_not_search_for_a_substitute(mapped):
    report = namespace.reports(mapped.actors)
    report["native"]["pid"] = 99
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedNamespace):
        m.Witness(mapped.initial, mapped.actors[1].host_pid, report)
    assert len(os.listdir("/proc/self/fd")) == before
    assert set(mapped.calls) <= {item.host_pid for item in mapped.actors}


def test_report_dictionary_is_copied_before_later_caller_changes(mapped):
    report = namespace.reports(mapped.actors)
    witness = m.Witness(mapped.initial, mapped.actors[1].host_pid, report)
    try:
        report["native"]["pid"] = 99
        assert witness.refresh() == mapped.actors
    finally:
        witness.close()


def test_already_exited_init_cannot_mint_a_new_witness(mapped):
    mapped.init.stdin.close()
    mapped.init.wait(timeout=3)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedNamespace):
        bind(mapped)
    assert len(os.listdir("/proc/self/fd")) == before and not mapped.calls


@pytest.mark.parametrize("position", [1, 2, 3])
def test_pidfd_open_failure_does_not_leak_partial_handles(mapped, monkeypatch, position):
    original, calls = m.os.pidfd_open, []

    def opening(pid):
        calls.append(pid)
        if len(calls) == position:
            raise OSError("PRIVATE pidfd failure")
        return original(pid)

    monkeypatch.setattr(m.os, "pidfd_open", opening)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(m.UnconfirmedNamespace):
        bind(mapped)
    assert len(os.listdir("/proc/self/fd")) == before and not mapped.initial.exited()


def test_invalid_retained_descriptor_is_not_exit_and_other_handles_still_close(mapped):
    before = len(os.listdir("/proc/self/fd"))
    witness = bind(mapped)
    os.close(witness.handles["native"])  # Deliberate damage to our own new duplicate.
    with pytest.raises(m.UnconfirmedNamespace):
        witness.exited("native")
    with pytest.raises(m.UnconfirmedNamespace):
        witness.close()
    assert witness.closed and not witness.handles
    assert len(os.listdir("/proc/self/fd")) == before and not mapped.initial.exited()


@pytest.mark.parametrize("role", ["guardian", "native", "watchdog"])
def test_frozen_owned_process_keeps_exit_false_but_refuses_live_claim(mapped, family, role):
    witness = bind(mapped)
    try:
        signal.pidfd_send_signal(witness.handles[role], signal.SIGSTOP)
        pid = mapped.actors[("init", "guardian", "native", "watchdog").index(role)].host_pid
        # Observe actual stopped state before checking, no assumed sleep interval.
        import time

        until = time.monotonic() + 1
        while Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()[0] not in (
            "T",
            "t",
        ):
            assert time.monotonic() < until
        assert not witness.exited(role)
        with pytest.raises(m.UnconfirmedNamespace):
            witness.refresh()
        assert witness.failed and not witness.exited(role)
    finally:
        signal.pidfd_send_signal(witness.handles[role], signal.SIGCONT)
        witness.close()
