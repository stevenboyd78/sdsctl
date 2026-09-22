"""Read-only tree evidence with local fault fixtures; no host or scanner I/O."""

import hashlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

NAME = "supplemental_handoff_files"
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
f = sys.modules[NAME]


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.py").write_bytes(b"first\n")
    (root / "a.py").chmod(0o644)
    (root / "nested").mkdir()
    (root / "nested" / "b.json").write_bytes(b"{}")
    return root


def test_exact_bytes_metadata_and_no_writes(tree):
    first = f.inventory(tree)
    assert first == f.inventory(tree)
    assert list(first) == ["a.py", "nested/b.json"]
    assert first["a.py"] == {
        "size": 6,
        "sha256": hashlib.sha256(b"first\n").hexdigest(),
        "mode": 0o644,
        "uid": os.getuid(),
        "gid": os.getgid(),
    }
    assert set(tree.iterdir()) == {tree / "a.py", tree / "nested"}
    (tree / "a.py").write_bytes(b"other\n")
    assert f.inventory(tree)["a.py"]["sha256"] != first["a.py"]["sha256"]


def test_empty_file_and_directory(tree):
    (tree / "zero").touch()
    (tree / "empty").mkdir()
    assert f.inventory(tree)["zero"]["sha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("kind", ["file_link", "dir_link", "hardlink", "fifo", "name"])
def test_unsafe_entries_rejected_without_exposing_path(tree, kind):
    private = tree / "private-secret"
    if kind == "file_link":
        private.symlink_to(tree / "a.py")
    elif kind == "dir_link":
        private.symlink_to(tree / "nested", target_is_directory=True)
    elif kind == "hardlink":
        private.hardlink_to(tree / "a.py")
    elif kind == "fifo":
        os.mkfifo(private)
    else:
        (tree / "private\nsecret").touch()
    with pytest.raises(f.UnconfirmedFiles) as caught:
        f.inventory(tree)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("position", ["root", "parent"])
def test_links_in_root_ancestry_rejected(tree, position):
    link = tree.parent / "link"
    link.symlink_to(tree, target_is_directory=True)
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(link if position == "root" else link / "nested")


@pytest.mark.parametrize("path", [Path("/"), Path("relative"), Path("/tmp/../tmp"), "/tmp"])
def test_explicit_non_root_absolute_path_required(path):
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(path)


@pytest.mark.parametrize(
    "limit,value",
    [
        ("MAX_FILES", 1),
        ("MAX_ENTRIES", 1),
        ("MAX_DEPTH", 0),
        ("MAX_FILE_BYTES", 4),
        ("MAX_TOTAL_BYTES", 7),
        ("MAX_SECONDS", -1),
    ],
)
def test_budgets_refuse_partial_inventory(tree, monkeypatch, limit, value):
    monkeypatch.setattr(f, limit, value)
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree)


@pytest.mark.parametrize("change", ["grow", "shrink", "replace", "chmod", "link", "root"])
def test_mutation_during_read_rejected(tree, monkeypatch, change):
    original = f.os.read
    changed = False

    def reading(fd, size):
        nonlocal changed
        content = original(fd, size)
        if not changed and os.fstat(fd).st_ino == (tree / "a.py").stat().st_ino:
            changed = True
            target = tree / "a.py"
            if change == "grow":
                target.write_bytes(b"larger and private")
            elif change == "shrink":
                target.write_bytes(b"x")
            elif change == "replace":
                target.unlink()
                target.write_bytes(b"first\n")
            elif change == "chmod":
                target.chmod(0o600)
            elif change == "link":
                (tree / "alias").hardlink_to(target)
            else:
                tree.rename(tree.parent / "moved")
                tree.mkdir()
        return content

    monkeypatch.setattr(f.os, "read", reading)
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree)


def test_read_failure_sanitized_and_descriptors_closed(tree, monkeypatch):
    before = len(list(Path("/proc/self/fd").iterdir()))

    def failure(*args):
        raise OSError("private-secret-file")

    monkeypatch.setattr(f.os, "read", failure)
    with pytest.raises(f.UnconfirmedFiles, match="^Protected filesystem evidence is unconfirmed.$"):
        f.inventory(tree)
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_timeout_while_reading(tree, monkeypatch):
    ticks = iter([1.0, 1.0, 1.0, 99.0])
    monkeypatch.setattr(f.time, "monotonic", lambda: next(ticks))
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree)


def test_ancestor_stat_failure_closes_new_descriptor(tree, monkeypatch):
    before = len(list(Path("/proc/self/fd").iterdir()))

    def failure(*args):
        raise OSError("private ancestor")

    monkeypatch.setattr(f.os, "fstat", failure)
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree)
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_import_does_not_launch_or_connect():
    source = Path(f.__file__).read_text()
    assert "subprocess" not in source
    assert "import socket" not in source
