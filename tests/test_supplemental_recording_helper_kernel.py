"""Pure privilege profile and owned pidfd with explicit synthetic proc routing.

These tests cannot qualify kernel enforcement on an installed host. Live owned
process rejection exercises real proc; accepted profile bytes are test fixtures.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_recording_helper_environment as environment

m = environment.m
CID = "a" * 64
MAP = b"         0          0 4294967295\n"


def status(pid=100):
    return (
        f"Name:\thelper\nState:\tS (sleeping)\nTgid:\t{pid}\nPid:\t{pid}\n"
        f"TracerPid:\t0\nUid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\nNSpid:\t{pid}\n"
        "CapInh:\t0000000000000000\nCapPrm:\t0000000000080004\n"
        "CapEff:\t0000000000080004\nCapBnd:\t0000000000080004\n"
        "CapAmb:\t0000000000000000\nNoNewPrivs:\t1\nSeccomp:\t2\nSeccomp_filters:\t1\n"
        "voluntary_ctxt_switches:\t1\n"
    ).encode("ascii")


def changed(raw, key, value):
    return b"\n".join(
        key.encode() + b":\t" + value if line.startswith(key.encode() + b":") else line
        for line in raw.split(b"\n")
    )


def pin(raw, uid=MAP, gid=MAP):
    return m.helper_kernel_profile(raw, uid, gid, process=m.processes.ProcessIdentity(100, 1, CID))


def denied(call):
    environment.env.runtime.denied(call)


def test_closed_effective_privileges_ignore_only_volatile_nonsecurity_status():
    before = status()
    value = pin(before)
    after = changed(changed(before, "State", b"R (running)"), "voluntary_ctxt_switches", b"199")
    assert value == pin(after)
    assert before == status() and len(value) == 64
    assert value != pin(changed(before, "Seccomp_filters", b"2"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("State", b"T (stopped)"),
        ("State", b"Z (zombie)"),
        ("State", b"D (disk sleep)"),
        ("Pid", b"101"),
        ("Tgid", b"101"),
        ("NSpid", b"100 1"),
        ("NSpid", b"1"),
        ("TracerPid", b"101"),
        ("Uid", b"0 0 1000 0"),
        ("Gid", b"0 0 0 1000"),
        ("CapInh", b"0000000000080004"),
        ("CapAmb", b"0000000000000004"),
        ("CapPrm", b"0000000000080000"),
        ("CapEff", b"0000000000000000"),
        ("CapBnd", b"000001ffffffffff"),
        ("CapEff", b"80004"),
        ("NoNewPrivs", b"0"),
        ("Seccomp", b"0"),
        ("Seccomp", b"1"),
        ("Seccomp_filters", b"0"),
        ("Seccomp_filters", b"65"),
        ("Seccomp_filters", b"01"),
    ],
)
def test_each_required_kernel_privilege_refuses_deviation(key, value):
    denied(lambda: pin(changed(status(), key, value)))


@pytest.mark.parametrize(
    "key",
    [
        "State",
        "Tgid",
        "Pid",
        "TracerPid",
        "Uid",
        "Gid",
        "NSpid",
        "CapInh",
        "CapPrm",
        "CapEff",
        "CapBnd",
        "CapAmb",
        "NoNewPrivs",
        "Seccomp",
        "Seccomp_filters",
    ],
)
@pytest.mark.parametrize("fault", ["missing", "duplicate", "empty"])
def test_required_fields_cannot_be_filtered_or_defaulted(key, fault):
    raw = status()
    line = next(line for line in raw.splitlines() if line.startswith(key.encode() + b":"))
    if fault == "missing":
        raw = raw.replace(line + b"\n", b"")
    elif fault == "duplicate":
        raw += line + b"\n"
    else:
        raw = changed(raw, key, b"")
    denied(lambda: pin(raw))


@pytest.mark.parametrize("value", [None, "text", b"", b"x" * 16385, b"\xff"])
def test_status_is_bounded_ascii_kernel_bytes(value):
    denied(lambda: pin(value))


@pytest.mark.parametrize(
    "value",
    [
        b"0 100000 65536\n",
        b"0 0 4294967295",
        b"0 0 4294967295\n1 1 1\n",
        b"\n0 0 4294967295\n",
        b"00 0 4294967295\n",
        b"x" * 257,
        None,
        "text",
    ],
)
@pytest.mark.parametrize("map_name", ["uid", "gid"])
def test_user_namespace_mapping_cannot_be_remapped_or_partial(value, map_name):
    denied(lambda: pin(status(), **{map_name: value}))


@pytest.fixture
def collector(tmp_path, monkeypatch):
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys;sys.stdin.read()"], stdin=subprocess.PIPE
    )
    witness = None
    actual_open, actual_stat, actual_read = os.open, os.stat, os.read
    state = SimpleNamespace(child=child, fail=None, opens=0, reads=0, fds=[])
    paths = {}
    for name, raw in (("status", status(child.pid)), ("uid_map", MAP), ("gid_map", MAP)):
        target = tmp_path / name
        target.write_bytes(raw)
        paths[f"/proc/{child.pid}/{name}"] = target

    def identity(pid, cid):
        assert (pid, cid) == (child.pid, CID)
        return m.processes.process_identity(
            pid,
            cid,
            Path(f"/proc/{pid}/stat").read_text(),
            f"0::/system.slice/docker-{cid}.scope\n",
        )

    monkeypatch.setattr(m.processes, "read_identity", identity)
    monkeypatch.setattr(m, "ROOT_UID", os.geteuid())

    def open_path(path, *args, **kwargs):
        if path in paths:
            state.opens += 1
            if state.fail == state.opens:
                raise OSError("PRIVATE")
            fd = actual_open(paths[path], *args, **kwargs)
            state.fds.append(fd)
            return fd
        return actual_open(path, *args, **kwargs)

    def stat_path(path, *args, **kwargs):
        return actual_stat(paths.get(path, path), *args, **kwargs)

    def read(fd, size):
        if fd in state.fds:
            state.reads += 1
        return actual_read(fd, size)

    monkeypatch.setattr(m.os, "open", open_path)
    monkeypatch.setattr(m.os, "stat", stat_path)
    monkeypatch.setattr(m.os, "read", read)
    try:
        witness = m.processes.ProcessWitness(identity(child.pid, CID))
        state.witness, state.paths = witness, paths
        state.call = lambda **kwargs: m.collect_helper_kernel(
            witness, deadline=time.monotonic() + 0.8, **kwargs
        )
        state.actual_open, state.actual_stat = actual_open, actual_stat
        yield state
    finally:
        child.stdin.close()
        child.wait(timeout=3)
        if witness:
            witness.close()


def assert_closed(fds):
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_collector_reads_original_files_twice_retaining_caller_pidfd(collector):
    before = time.monotonic()
    result = collector.call()
    assert type(result) is m.HelperKernel and result.process == collector.witness.identity
    assert before <= result.observed_at <= time.monotonic()
    assert collector.opens == 3 and collector.reads == 12
    assert collector.witness.fd >= 0 and not collector.witness.exited()
    assert_closed(collector.fds)


@pytest.mark.parametrize("fail", [1, 2, 3])
def test_partial_open_failure_closes_only_owned_proc_fds(collector, fail):
    collector.fail = fail
    denied(collector.call)
    assert_closed(collector.fds)
    assert collector.witness.fd >= 0


def test_real_owned_unprivileged_process_is_not_accepted_as_helper(collector, monkeypatch):
    monkeypatch.setattr(m.os, "open", collector.actual_open)
    monkeypatch.setattr(m.os, "stat", collector.actual_stat)
    denied(collector.call)
    assert collector.witness.fd >= 0


def test_changed_status_on_second_read_is_not_a_new_baseline(collector, monkeypatch):
    actual = m.os.read
    cycles = []

    def drift(fd, size):
        raw = actual(fd, size)
        if raw.startswith(b"Name:"):
            cycles.append(True)
            if len(cycles) == 2:
                return changed(raw, "Seccomp_filters", b"2")
        return raw

    monkeypatch.setattr(m.os, "read", drift)
    denied(collector.call)
    assert len(cycles) == 2
    assert_closed(collector.fds)


@pytest.mark.parametrize("fault", ["exit", "lost", "file", "time"])
def test_loss_during_proc_read_refuses_and_does_not_rebind(collector, monkeypatch, fault):
    actual = m.os.read
    done = []

    def fail(fd, size):
        raw = actual(fd, size)
        if raw and not done and fd in collector.fds:
            done.append(True)
            if fault == "exit":
                collector.child.stdin.close()
                assert collector.child.wait(timeout=3) == 0
            elif fault == "lost":
                collector.witness.close()
            elif fault == "file":
                next(iter(collector.paths.values())).write_bytes(b"PRIVATE")
            else:
                now = time.monotonic()
                monkeypatch.setattr(time, "monotonic", lambda: now + 2)
        return raw

    monkeypatch.setattr(m.os, "read", fail)
    denied(collector.call)
    assert_closed(collector.fds)


@pytest.mark.parametrize("delta", [-1, 0, 1.5, float("inf"), float("nan")])
def test_no_renewed_or_unbounded_deadline(collector, delta):
    denied(lambda: m.collect_helper_kernel(collector.witness, deadline=time.monotonic() + delta))
    assert not collector.fds
