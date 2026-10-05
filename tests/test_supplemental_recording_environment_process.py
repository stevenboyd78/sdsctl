"""Real owned child/pidfd/proc reads; synthetic container cgroup/root identity.

Only private local fake credentials, no Docker/HA/scanner or process discovery.
"""

import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_environment as env

m = env.m
image = env.image
configured = env.configured
CID = "a" * 64


@pytest.fixture
def bound(configured, image, monkeypatch):
    values = dict(value.split("=", 1) for value in configured)
    values.update(HOME="/root", HOSTNAME=env.HOSTNAME, PATH=m.FIXED_EXEC_PATH)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        env=values,
    )

    def identity(pid, cid):
        assert pid == child.pid and cid == CID
        return m.processes.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())
    monkeypatch.setattr(m.processes, "read_identity", identity)
    witness = None
    try:
        witness = m.processes.ProcessWitness(identity(child.pid, CID))
        profile = dict(
            configured=configured,
            configured_sha256=env.config_pin(configured, image),
            image_environment_sha256=m.environment(image),
            timezone=env.TIMEZONE,
            hostname=env.HOSTNAME,
            fixed_exec=True,
        )
        yield SimpleNamespace(child=child, witness=witness, profile=profile)
    finally:
        if witness is not None:
            witness.close()
        if not child.stdin.closed:
            child.stdin.close()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()  # Only this test's owned harmless child.
            child.wait(timeout=3)


def collect(bound, *, deadline=None):
    return m.collect_supervised_process_environment(
        bound.witness,
        deadline=time.monotonic() + 0.8 if deadline is None else deadline,
        **bound.profile,
    )


def denied(bound, callback=None):
    before = len(os.listdir("/proc/self/fd"))
    env.runtime.denied(callback or (lambda: collect(bound)))
    assert len(os.listdir("/proc/self/fd")) == before


def test_two_actual_proc_reads_keep_original_witness_open(bound, capsys):
    before = len(os.listdir("/proc/self/fd"))
    started = time.monotonic()
    result = collect(bound)
    assert result.process == bound.witness.identity
    assert started <= result.observed_at <= time.monotonic()
    with open(f"/proc/{bound.child.pid}/environ", "rb") as stream:
        raw = stream.read(16385)
    assert result.sha256 == m.supervised_process_environment(raw, **bound.profile)
    assert len(os.listdir("/proc/self/fd")) == before
    assert not bound.witness.exited() and bound.child.poll() is None
    assert env.TOKEN not in repr(result) and capsys.readouterr() == ("", "")


@pytest.mark.parametrize("fault", ["closed_witness", "child_exited", "wrong_type", "wrong_uid"])
def test_unbound_or_exited_process_cannot_be_qualified(bound, monkeypatch, fault):
    if fault == "closed_witness":
        bound.witness.close()
    elif fault == "child_exited":
        bound.child.stdin.close()
        bound.child.wait(timeout=3)
        assert bound.witness.exited()
    elif fault == "wrong_type":
        original = bound.witness
        bound.witness = SimpleNamespace(
            fd=original.fd, identity=original.identity, close=lambda: None
        )
        try:
            denied(bound)
        finally:
            bound.witness = original
        return
    else:
        monkeypatch.setattr(m, "ROOT_UID", os.geteuid() + 1)
    denied(bound)


@pytest.mark.parametrize("when", [1, 2, 3])
def test_identity_change_at_any_boundary_refuses(bound, monkeypatch, when):
    original = m.processes.read_identity
    calls = 0

    def changed(*args):
        nonlocal calls
        calls += 1
        value = original(*args)
        return replace(value, start_ticks=value.start_ticks + 1) if calls == when else value

    monkeypatch.setattr(m.processes, "read_identity", changed)
    denied(bound)
    assert calls == when


@pytest.mark.parametrize("fault", ["changed_second_read", "oversize", "read_error"])
def test_environment_read_faults_never_produce_evidence(bound, monkeypatch, fault):
    original = m.os.read
    calls = 0

    def changed(fd, size):
        nonlocal calls
        calls += 1
        if fault == "read_error":
            raise OSError(env.TOKEN)
        if fault == "oversize":
            return b"x" * 16385
        raw = original(fd, size)
        if calls == 3:
            return raw.replace(env.TOKEN.encode(), ("y" * len(env.TOKEN)).encode())
        return raw

    monkeypatch.setattr(m.os, "read", changed)
    denied(bound)


@pytest.mark.parametrize("deadline", [True, "1", float("nan"), float("inf"), -1])
def test_deadline_has_closed_original_absolute_shape(bound, deadline):
    denied(bound, lambda: collect(bound, deadline=deadline))


@pytest.mark.parametrize("offset", [-1, 0, 2])
def test_expired_or_overlong_deadline_cannot_be_renewed(bound, offset):
    denied(bound, lambda: collect(bound, deadline=time.monotonic() + offset))


def test_slow_read_expires_original_deadline(bound, monkeypatch):
    original = m.os.read

    def slow(fd, size):
        raw = original(fd, size)
        time.sleep(0.05)
        return raw

    monkeypatch.setattr(m.os, "read", slow)
    denied(bound, lambda: collect(bound, deadline=time.monotonic() + 0.03))


def test_unknown_or_bad_profile_never_leaks_proc_descriptor(bound):
    bound.profile["configured_sha256"] = "b" * 64
    denied(bound)
    bound.profile["unknown"] = env.TOKEN
    denied(bound)


def test_collection_cannot_invent_or_recapture_a_replaced_witness(bound, monkeypatch):
    original_read = m.os.read
    original = bound.witness.identity

    def changed(fd, size):
        raw = original_read(fd, size)
        bound.witness.identity = replace(original, start_ticks=original.start_ticks + 1)
        return raw

    monkeypatch.setattr(m.os, "read", changed)
    try:
        denied(bound)
    finally:
        bound.witness.identity = original


def test_lost_original_handle_during_read_never_becomes_a_new_witness(bound, monkeypatch):
    original_read = m.os.read
    before = len(os.listdir("/proc/self/fd"))

    def changed(fd, size):
        raw = original_read(fd, size)
        bound.witness.close()
        return raw

    monkeypatch.setattr(m.os, "read", changed)
    env.runtime.denied(lambda: collect(bound))
    # Only the explicitly closed original handle is gone; the proc fd closed too.
    assert len(os.listdir("/proc/self/fd")) == before - 1
    assert bound.witness.fd == -1 and bound.child.poll() is None
