"""Duplicate owned live kernel pidfds; no Docker or scanner process access."""

import builtins
import io
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_process as base

w, p = base.w, base.p


@pytest.fixture
def live(monkeypatch):
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    ticks = int(Path(f"/proc/{child.pid}/stat").read_text().rpartition(") ")[2].split()[19])
    expected = w.ProcessIdentity(child.pid, ticks, base.CID)
    # Only cgroup membership is synthetic; real proc start ticks and pidfds.
    monkeypatch.setattr(w, "read_identity", lambda *_: expected)
    original = w.ProcessWitness(expected)
    try:
        yield child, original
    finally:
        original.close()
        child.stdin.close()
        child.wait(timeout=3)
        assert child.returncode == 0


def duplicate(original):
    return w.ProcessWitness(original.identity, retained_fd=original.fd)


def denied(call):
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(p.UnsafeHandoff, match="exit remains unconfirmed"):
        call()
    assert len(os.listdir("/proc/self/fd")) == before


def test_duplicate_never_reopens_pid_survives_original_close_and_real_exit(live, monkeypatch):
    child, original = live
    monkeypatch.setattr(os, "pidfd_open", lambda *_: pytest.fail("numeric PID reopened"))
    with duplicate(original) as copy:
        assert copy.fd != original.fd and not os.get_inheritable(copy.fd)
        assert copy.identity is original.identity and not copy.exited()
        original.close()
        child.stdin.close()
        assert child.wait(timeout=3) == 0
        assert copy.exited()
        denied(lambda: duplicate(copy))  # Dead retained descriptor is not new custody.


@pytest.mark.parametrize("which", ["wrong_process", "non_pidfd", "invalid", "boolean"])
def test_wrong_descriptor_cannot_substitute(live, which):
    _, original = live
    fd = (
        os.pidfd_open(os.getpid())
        if which == "wrong_process"
        else os.open("/dev/null", os.O_RDONLY)
    )
    try:
        supplied = {"invalid": -1, "boolean": True}.get(which, fd)
        denied(lambda: w.ProcessWitness(original.identity, retained_fd=supplied))
        assert os.fstat(fd) and not original.exited()
    finally:
        os.close(fd)


@pytest.mark.parametrize("at", [1, 2])
def test_changed_process_incarnation_rejects_and_preserves_borrowed_handle(live, monkeypatch, at):
    _, original = live
    calls = []

    def changed(*_):
        calls.append(True)
        return (
            replace(original.identity, start_ticks=original.identity.start_ticks + 1)
            if len(calls) == at
            else original.identity
        )

    monkeypatch.setattr(w, "read_identity", changed)
    denied(lambda: duplicate(original))
    assert not original.exited()


@pytest.mark.parametrize("raw", [b"", b"Pid: 1\n", b"Pid: -1\n", b"Pid: 1\nPid: 2\n", b"x" * 4097])
def test_bad_kernel_fdinfo_is_not_an_adoptable_handle(live, monkeypatch, raw):
    _, original = live
    real = builtins.open

    def changed(path, *args, **kwargs):
        return (
            io.BytesIO(raw)
            if str(path).startswith("/proc/self/fdinfo/")
            else real(path, *args, **kwargs)
        )

    monkeypatch.setattr(builtins, "open", changed)
    denied(lambda: duplicate(original))
    assert not original.exited()


@pytest.mark.parametrize("at", [1, 2, 3])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_partial_metadata_failure_closes_only_new_duplicate(live, monkeypatch, at, error):
    _, original = live
    real, calls = os.fstat, []
    before = len(os.listdir("/proc/self/fd"))

    def failed(fd):
        calls.append(fd)
        if len(calls) == at:
            raise error("PRIVATE")
        return real(fd)

    monkeypatch.setattr(os, "fstat", failed)
    with pytest.raises(KeyboardInterrupt if error is KeyboardInterrupt else p.UnsafeHandoff):
        duplicate(original)
    assert len(os.listdir("/proc/self/fd")) == before and not original.exited()
