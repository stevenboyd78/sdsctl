"""Executable proof is mandatory for Chromium, not unrelated wrapper ancestors."""
from __future__ import annotations

import io
import os
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from sds200 import browser_device_worker as worker


@pytest.fixture
def proc_tree(tmp_path, monkeypatch):
    nodes = {
        301: dict(comm="chromium", parent=302, executable="chromium", uid=os.geteuid()),
        302: dict(comm="bwrap", parent=1, executable=PermissionError, uid=os.geteuid()),
    }
    reads, parsed = [], []

    class Proc:
        def __init__(self, path):
            self.path = PurePosixPath(path)

        def __truediv__(self, name):
            return Proc(self.path / name)

        def stat(self):
            return SimpleNamespace(st_uid=nodes[int(self.path.parts[2])]["uid"])

        def open(self, mode):
            assert mode == "rb"
            pid = int(self.path.parts[2])
            node = nodes[pid]
            if self.path.name == "cmdline":
                return io.BytesIO(b"fixture\0--user-data-dir=/private/browser\0")
            assert self.path.name == "stat"
            fields = ["S", str(node["parent"])] + ["0"] * 18
            fields[19] = str(node.get("start", 42))
            if node.get("changed"):
                node["start"] = node.get("start", 42) + 1
            return io.BytesIO(f"{pid} ({node['comm']}) {' '.join(fields)}".encode())

        def resolve(self, *, strict):
            assert strict is True and self.path.name == "exe"
            pid = int(self.path.parts[2])
            reads.append(pid)
            value = nodes[pid]["executable"]
            if value is PermissionError:
                raise PermissionError("Fictional cross-namespace executable restriction")
            return Path("/usr/bin") / value

    monkeypatch.setattr(worker, "Path", Proc)
    monkeypatch.setattr(worker.os, "getppid", lambda: 301)
    monkeypatch.setattr(worker, "_selected_directory",
        lambda raw, bundle, origin: parsed.append(raw) or tmp_path)
    return nodes, reads, parsed, tmp_path


@pytest.mark.parametrize("name,comm", [
    ("chromium", "chromium"), ("chromium-browser", "chromium-browse"),
])
def test_unrelated_wrapper_exe_is_never_read_but_chromium_proof_is_required(proc_tree, name, comm):
    nodes, reads, parsed, root = proc_tree
    nodes[301].update(executable=name, comm=comm)
    assert worker._browser_directory(Path("/bundle"), "chrome-extension://fixture/") == root
    assert reads == [301] and len(parsed) == 1


@pytest.mark.parametrize("fault", ["unreadable", "wrong-exe", "wrong-user", "changed", "comm-only"])
def test_candidate_or_stability_failure_never_uses_comm_as_identity(proc_tree, fault):
    nodes, reads, parsed, root = proc_tree
    if fault == "unreadable":
        nodes[301]["executable"] = PermissionError
    elif fault == "wrong-exe":
        nodes[301]["executable"] = "another-program"
    elif fault == "wrong-user":
        nodes[301]["uid"] += 1
    elif fault == "changed":
        nodes[301]["changed"] = True
    else:
        nodes[301]["comm"] = "another-program"
    with pytest.raises((ValueError, PermissionError)):
        worker._browser_directory(Path("/bundle"), "chrome-extension://fixture/")
    assert 302 not in reads
    if fault != "changed":
        assert parsed == []


def test_multiple_proven_browser_ancestors_remain_ambiguous(proc_tree):
    nodes, reads, parsed, root = proc_tree
    nodes[302].update(comm="chromium", executable="chromium")
    with pytest.raises(ValueError):
        worker._browser_directory(Path("/bundle"), "chrome-extension://fixture/")
    assert reads == [301, 302] and len(parsed) == 2
