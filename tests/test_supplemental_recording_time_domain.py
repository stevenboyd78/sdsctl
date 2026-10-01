"""Real owned process/pidfd/ns descriptors; synthetic Docker cgroup only.

No clocks/offsets/namespaces are modified. Faults substitute bounded kernel-read
results in memory. Distinct container namespaces require separate local Docker
qualification, not these same-domain process fixtures.
"""

import builtins
import io
import os
from dataclasses import replace
from threading import Thread
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_idle_observer as idle

m = idle.m.time_domain
prepared = idle.prepared


def denied(action):
    with pytest.raises(m.UnconfirmedDomain) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


@pytest.fixture
def setup(prepared, monkeypatch):
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    result = m.ZeroDomain(prepared.plan.original_clock, prepared.witness)
    try:
        yield prepared, result
    finally:
        result.close()


@pytest.mark.parametrize(
    "raw",
    [
        b"monotonic 0 0\nboottime 0 0\n",
        b"monotonic           0         0\nboottime            0         0\n",
        b"monotonic\t0\t0\nboottime\t0\t0\n",
    ],
)
def test_exact_zero_clock_pair_only(raw):
    assert m.zero_offsets(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,
        "monotonic 0 0\nboottime 0 0\n",
        b"",
        b"x" * 257,
        b"monotonic 0 0\n",
        b"boottime 0 0\n",
        b"boottime 0 0\nmonotonic 0 0\n",
        b"monotonic 0 0\nmonotonic 0 0\nboottime 0 0\n",
        b"monotonic 0 0\nboottime 0 0\nrealtime 0 0\n",
        b"monotonic 0 0\nboottime 0 0",
        b" monotonic 0 0\nboottime 0 0\n",
        b"monotonic 0 0\r\nboottime 0 0\r\n",
        b"monotonic +0 0\nboottime 0 0\n",
        b"monotonic -0 0\nboottime 0 0\n",
        b"monotonic 00 0\nboottime 0 0\n",
        b"monotonic 0 0.0\nboottime 0 0\n",
        b"monotonic 0 0\nboottime 0 0\n\0",
    ],
)
def test_unknown_kernel_format_is_not_silently_normalized(raw):
    denied(lambda: m.zero_offsets(raw))


@pytest.mark.parametrize("which", ["monotonic", "boottime"])
@pytest.mark.parametrize(
    "seconds, nanoseconds", [("1", "0"), ("-1", "0"), ("0", "1"), ("0", "999999999"), ("0", "-1")]
)
def test_nonzero_offsets_never_qualify(which, seconds, nanoseconds):
    values = dict(monotonic=("0", "0"), boottime=("0", "0"))
    values[which] = (seconds, nanoseconds)
    raw = "".join(f"{name} {values[name][0]} {values[name][1]}\n" for name in values).encode()
    denied(lambda: m.zero_offsets(raw))


def test_actual_live_kernel_descriptors_are_retained_without_entering_namespaces(setup):
    prepared, proof = setup
    before = proof.evidence
    assert proof.refresh() == before
    assert before.init == prepared.identity
    assert before.host_time == prepared.plan.original_clock.namespace
    info = os.stat(f"/proc/{prepared.child.pid}/ns/time")
    assert before.native_time == (info.st_dev, info.st_ino)
    assert len(before.sha256) == 64 and len(proof.handles) == 2
    descriptors = [proof.pidfd, *(fd for fd, _ in proof.handles.values())]
    assert all(os.fstat(fd) for fd in descriptors)
    assert not hasattr(proof, "enter") and not hasattr(proof, "set_clock")
    proof.close()
    proof.close()
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert not prepared.witness.exited()
    denied(proof.refresh)


@pytest.mark.parametrize("target", ["host", "native"])
@pytest.mark.parametrize("which", ["time", "time_for_children", "user"])
def test_namespace_change_or_child_only_offset_view_refused(setup, monkeypatch, target, which):
    prepared, proof = setup
    pid = os.getpid() if target == "host" else prepared.child.pid
    real = m.os.stat

    def changed(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if path == f"/proc/{pid}/ns/{which}":
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
        return info

    monkeypatch.setattr(m.os, "stat", changed)
    denied(proof.refresh)
    assert proof.failed and not proof.closed
    denied(proof.refresh)


@pytest.mark.parametrize("target", ["host", "native"])
@pytest.mark.parametrize(
    "raw",
    [b"monotonic 1 0\nboottime 0 0\n", b"monotonic 0 0\nboottime -1 0\n", b"PRIVATE", b"x" * 257],
)
def test_changed_or_unreadable_offset_file_poisoned(setup, monkeypatch, target, raw):
    prepared, proof = setup
    pid = os.getpid() if target == "host" else prepared.child.pid
    real = builtins.open

    def changed(path, *args, **kwargs):
        if path == f"/proc/{pid}/timens_offsets":
            return io.BytesIO(raw)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", changed)
    denied(proof.refresh)
    assert proof.failed and not prepared.witness.exited()


def test_exit_cannot_be_reconstructed_as_clock_equivalence(setup):
    prepared, proof = setup
    prepared.child.stdin.close()
    prepared.child.wait(timeout=3)
    assert prepared.witness.exited()
    denied(proof.refresh)
    denied(lambda: m.ZeroDomain(prepared.plan.original_clock, prepared.witness))


@pytest.mark.parametrize(
    "fault", ["clock_namespace", "clock_boot", "clock_suspend", "clock_backwards"]
)
def test_original_helper_clock_cannot_be_replaced(setup, monkeypatch, fault):
    prepared, proof = setup
    observed = m.clock.read()
    if fault == "clock_namespace":
        observed = replace(observed, namespace=(1, 123))
    elif fault == "clock_boot":
        observed = replace(observed, boot="c" * 32)
    elif fault == "clock_suspend":
        observed = replace(observed, boottime_ns=observed.boottime_ns + 10 * m.clock.NS)
    else:
        observed = prepared.plan.original_clock
    monkeypatch.setattr(m.clock, "read", lambda: observed)
    denied(proof.refresh)


@pytest.mark.parametrize("position", [1, 2])
def test_constructor_namespace_stat_failure_closes_all_owned_fds(prepared, monkeypatch, position):
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    real = m.os.fstat
    initial, calls = set(os.listdir("/proc/self/fd")), []

    def fail(fd):
        calls.append(fd)
        if len(calls) == position:
            raise OSError("PRIVATE")
        return real(fd)

    monkeypatch.setattr(m.os, "fstat", fail)
    denied(lambda: m.ZeroDomain(prepared.plan.original_clock, prepared.witness))
    assert set(os.listdir("/proc/self/fd")) == initial
    assert not prepared.witness.exited()


def test_wrong_thread_cannot_reuse_live_domain_proof(setup):
    _, proof = setup
    outcomes = []

    def attempt():
        try:
            proof.refresh()
        except m.UnconfirmedDomain:
            outcomes.append("refused")

    child = Thread(target=attempt)
    child.start()
    child.join(timeout=3)
    assert not child.is_alive() and outcomes == ["refused"] and proof.failed
    denied(proof.refresh)


def test_busy_lock_not_released_by_refused_call(setup):
    _, proof = setup
    assert proof.lock.acquire(blocking=False)
    denied(proof.refresh)
    assert proof.lock.locked() and proof.failed
    proof.lock.release()


def test_root_and_original_live_witness_required(prepared, monkeypatch):
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid() + 1)
    denied(lambda: m.ZeroDomain(prepared.plan.original_clock, prepared.witness))
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    denied(lambda: m.ZeroDomain(prepared.plan.original_clock, prepared.identity))
    denied(lambda: m.ZeroDomain({}, prepared.witness))


def test_init_cannot_alias_observing_helper_or_leak_replaced_descriptor(prepared, monkeypatch):
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(prepared.witness, "identity", replace(prepared.identity, pid=os.getpid()))
    before = set(os.listdir("/proc/self/fd"))
    denied(lambda: m.ZeroDomain(prepared.plan.original_clock, prepared.witness))
    assert set(os.listdir("/proc/self/fd")) == before


def test_idle_explicitly_refreshes_live_domain_without_replacing_original_clock(setup):
    prepared, proof = setup
    observer = prepared.observe(zero_domain=proof)
    assert observer.initial.time_domain_sha256 == proof.evidence.sha256
    assert observer.read().time_domain_sha256 == proof.evidence.sha256
    assert observer.plan.original_clock == proof.original
    assert observer.initial.native.healthy is None and observer.initial.native.recording is None
    observer.close()
    assert proof.refresh() == proof.evidence  # Caller retains domain ownership.


def test_default_idle_still_requires_exact_same_domain(prepared, monkeypatch):
    actor = replace(prepared.actor, namespaces=(*prepared.actor.namespaces[:4], (5, 123)))
    monkeypatch.setattr(idle.m.namespace, "read", lambda *_: actor)
    idle.denied(prepared.observe)


@pytest.mark.parametrize("fault", ["closed", "failed", "serialized", "other_clock", "other_init"])
def test_idle_cannot_use_unconfirmed_or_unrelated_domain_evidence(setup, fault):
    prepared, proof = setup
    selected = proof
    if fault == "closed":
        proof.close()
    elif fault == "failed":
        proof.failed = True
    elif fault == "serialized":
        selected = proof.evidence
    elif fault == "other_clock":
        proof.evidence = replace(proof.evidence, original_clock=m.clock.read())
    else:
        proof.evidence = replace(
            proof.evidence, init=replace(proof.init, start_ticks=proof.init.start_ticks + 1)
        )
    idle.denied(lambda: prepared.observe(zero_domain=selected))


def test_idle_retains_original_domain_digest_and_never_rebaselines(setup):
    prepared, proof = setup
    observer = prepared.observe(zero_domain=proof)
    before = observer.initial
    proof.close()
    idle.denied(observer.read)
    assert observer.failed and observer.initial == before
    assert not prepared.witness.exited()
