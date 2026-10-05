"""Linux pidfd fixtures never attach to Home Assistant or a scanner process."""

import importlib.util
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

for name in ("supplemental_handoff_policy", "supplemental_handoff_process"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
p = sys.modules["supplemental_handoff_policy"]
w = sys.modules["supplemental_handoff_process"]
CID = "a" * 64
CGROUP = f"0::/system.slice/docker-{CID}.scope\n"


def stat_text(pid=123, state="S", ticks="456", comm="daemon"):
    return f"{pid} ({comm}) " + " ".join([state, *(["0"] * 18), ticks, "1", "2"])


def test_identity_handles_arbitrary_comm_and_correct_start_field():
    for comm in ("daemon", "space here", "odd) ("):
        assert w.process_identity(123, CID, stat_text(comm=comm), CGROUP) == w.ProcessIdentity(
            123, 456, CID
        )


@pytest.mark.parametrize(
    "text,group",
    [
        (stat_text(pid=124), CGROUP),
        (stat_text(state="Z"), CGROUP),
        (stat_text(state="X"), CGROUP),
        (stat_text(ticks="0"), CGROUP),
        (stat_text(ticks="-1"), CGROUP),
        (stat_text(ticks="private"), CGROUP),
        ("too short", CGROUP),
        ("x" * 4097, CGROUP),
        (stat_text(), "0::/\n"),
        (stat_text(), CGROUP.replace("0::/", "0::/../")),
        (stat_text(), CGROUP.replace("0::/", "0::/../../")),
        (stat_text(), CGROUP + "1:extra:/other\n"),
        (stat_text(), CGROUP.replace(CID, "b" * 64)),
        (stat_text(), CGROUP.replace(".scope", ".scope/child")),
    ],
)
def test_uncertain_or_wrong_namespace_identity_refused(text, group):
    with pytest.raises(p.UnsafeHandoff):
        w.process_identity(123, CID, text, group)


@pytest.mark.parametrize(
    "pid,ticks,cid",
    [(True, 1, CID), (1, 1, CID), (0, 1, CID), (123, False, CID), (123, -1, CID), (123, 1, "bad")],
)
def test_identity_types_refused(pid, ticks, cid):
    with pytest.raises(p.UnsafeHandoff):
        w.ProcessIdentity(pid, ticks, cid)


def test_real_child_pidfd_survives_exit_and_reap_without_signals(monkeypatch):
    # stdin EOF lets our own harmless child exit normally; the witness sends no
    # signal, and no scanner/container is involved in this local kernel fixture.
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    expected = w.ProcessIdentity(child.pid, 123, CID)
    monkeypatch.setattr(w, "read_identity", lambda *_: expected)
    try:
        with w.ProcessWitness(expected) as witness:
            assert not witness.exited()
            child.stdin.close()
            child.wait(timeout=3)
            assert witness.exited()
            assert witness.exited()
        with pytest.raises(p.UnsafeHandoff):
            witness.exited()
        with pytest.raises(p.UnsafeHandoff, match="exit remains unconfirmed"):
            w.ProcessWitness(expected)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)


def test_pid_recycled_during_binding_refused_and_fd_closed(monkeypatch):
    expected = w.ProcessIdentity(123, 456, CID)
    values = iter([expected, w.ProcessIdentity(123, 999, CID)])
    monkeypatch.setattr(w, "read_identity", lambda *_: next(values))
    # A harmless real descriptor verifies failure cleanup without touching a PID.
    fd = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(w.os, "pidfd_open", lambda *_: fd)
    with pytest.raises(p.UnsafeHandoff, match="exit remains unconfirmed"):
        w.ProcessWitness(expected)
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.parametrize(
    "events",
    [
        [(3, select.POLLERR)],
        [(3, select.POLLHUP)],
        [(3, select.POLLNVAL)],
        [(99, select.POLLIN)],
        [(3, select.POLLIN), (3, select.POLLIN)],
    ],
)
def test_fd_errors_are_not_process_exit(monkeypatch, events):
    class Poll:
        def register(self, *_):
            pass

        def poll(self, *_):
            return events

    monkeypatch.setattr(w.select, "poll", Poll)
    witness = object.__new__(w.ProcessWitness)
    witness.fd = 3
    with pytest.raises(p.UnsafeHandoff):
        witness.exited()


def test_binding_an_already_exited_process_is_unknown(monkeypatch):
    expected = w.ProcessIdentity(123, 456, CID)
    monkeypatch.setattr(w, "read_identity", lambda *_: expected)
    fd = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(w.os, "pidfd_open", lambda *_: fd)
    monkeypatch.setattr(w.ProcessWitness, "exited", lambda _: True)
    with pytest.raises(p.UnsafeHandoff, match="exit remains unconfirmed"):
        w.ProcessWitness(expected)
    with pytest.raises(OSError):
        os.fstat(fd)
