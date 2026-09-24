"""Read-only tree evidence with local fault fixtures; no host or scanner I/O."""

import hashlib
import importlib.util
import os
import sys
from dataclasses import asdict
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


@pytest.mark.parametrize("name", ["ordinary", "é漢字", "space name", "del-\x7f"])
@pytest.mark.parametrize("mode", [0o600, 0o644, 0o755])
@pytest.mark.parametrize("content", [b"", b"unchanged\0bytes\xff"])
def test_inventory_scalar_format_matches_original_dataclass(tree, name, mode, content):
    target = tree / name
    target.write_bytes(content)
    target.chmod(mode)
    info = target.stat()
    expected = asdict(
        f.FileEvidence(
            len(content), hashlib.sha256(content).hexdigest(), mode, info.st_uid, info.st_gid
        )
    )
    assert f.inventory(tree)[name] == expected


def test_inventory_does_not_construct_per_file_dataclass(tree, monkeypatch):
    expected = f.inventory(tree)

    def forbidden(*args, **kwargs):
        raise AssertionError("Inventory needlessly constructed a scalar dataclass")

    monkeypatch.setattr(f, "FileEvidence", forbidden)
    assert f.inventory(tree) == expected


@pytest.mark.parametrize("code", range(1, 32))
def test_every_filesystem_control_character_still_refuses(tree, code):
    (tree / ("private-" + chr(code) + "-name")).write_bytes(b"unused")
    with pytest.raises(f.UnconfirmedFiles, match="^Protected filesystem evidence is unconfirmed.$"):
        f.inventory(tree)


@pytest.mark.parametrize("single", [False, True])
def test_unrelated_sibling_directory_creation_does_not_change_selected_evidence(
    tree, monkeypatch, single
):
    target = tree / "a.py"
    target.chmod(0o600)
    observe = (lambda: f.private_file(target)) if single else (lambda: f.inventory(tree))
    expected = observe()
    original, changed = f.os.read, []
    inode = target.stat().st_ino

    def reading(fd, size):
        value = original(fd, size)
        if value and not changed and os.fstat(fd).st_ino == inode:
            # Siblings are not part of either the selected tree or single file.
            sibling = (tree if single else tree.parent) / "unrelated-directory"
            sibling.mkdir()
            changed.append(True)
        return value

    monkeypatch.setattr(f.os, "read", reading)
    assert observe() == expected
    assert changed == [True]


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("fault", ["mode", "replace", "symlink"])
def test_external_ancestor_identity_still_checked_during_selected_read(
    tree, monkeypatch, single, fault
):
    holder = tree.parent / "holder"
    holder.mkdir()
    tree.rename(holder / "tree")
    tree = holder / "tree"
    target = tree / "a.py"
    target.chmod(0o600)
    original, changed = f.os.read, []
    inode = target.stat().st_ino

    def reading(fd, size):
        value = original(fd, size)
        if value and not changed and os.fstat(fd).st_ino == inode:
            if fault == "mode":
                holder.chmod(0o711)
            else:
                saved = holder.with_name("saved-holder")
                holder.rename(saved)
                if fault == "replace":
                    holder.mkdir()
                else:
                    holder.symlink_to(saved, target_is_directory=True)
            changed.append(True)
        return value

    monkeypatch.setattr(f.os, "read", reading)
    with pytest.raises(f.UnconfirmedFiles):
        f.private_file(target) if single else f.inventory(tree)
    assert changed == [True]


def test_creating_inside_selected_root_still_refuses(tree, monkeypatch):
    original, changed = f.os.read, []
    inode = (tree / "a.py").stat().st_ino

    def reading(fd, size):
        value = original(fd, size)
        if value and not changed and os.fstat(fd).st_ino == inode:
            (tree / "unexpected-directory").mkdir()
            changed.append(True)
        return value

    monkeypatch.setattr(f.os, "read", reading)
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree)
    assert changed == [True]


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


def test_recording_limit_is_explicit_and_total_budget_still_applies(tree, monkeypatch):
    target = tree / "audio.wav"
    target.write_bytes(b"a" * (4 * 1024 * 1024 + 1))
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree)
    assert (
        f.inventory(tree, max_file_bytes=16 * 1024 * 1024)["audio.wav"]["size"]
        == target.stat().st_size
    )
    monkeypatch.setattr(f, "MAX_TOTAL_BYTES", 4 * 1024 * 1024)
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree, max_file_bytes=16 * 1024 * 1024)


@pytest.mark.parametrize("limit", [0, -1, True, 16 * 1024 * 1024 + 1, "4"])
def test_content_limit_cannot_be_unbounded(tree, limit):
    with pytest.raises(f.UnconfirmedFiles):
        f.inventory(tree, max_file_bytes=limit)


def test_single_private_file_reads_no_siblings(tree):
    target = tree / "a.py"
    target.chmod(0o600)
    os.mkfifo(tree / "unrelated")
    assert f.private_file(target).sha256 == hashlib.sha256(b"first\n").hexdigest()


def test_executable_entry_requires_exact_nonwritable_mode(tree):
    target = tree / "a.py"
    with pytest.raises(f.UnconfirmedFiles):
        f.executable_file(target)
    target.chmod(0o555)
    assert f.executable_file(target).mode == 0o555
    with pytest.raises(f.UnconfirmedFiles):
        f.private_file(target)


@pytest.mark.parametrize(
    "fault",
    ["symlink", "ancestor", "hardlink", "fifo", "mode", "empty", "large", "replace", "grow"],
)
def test_private_file_faults_are_refused(tree, monkeypatch, fault):
    target = tree / "a.py"
    target.chmod(0o600)
    if fault == "symlink":
        target.rename(tree / "original")
        target.symlink_to(tree / "original")
    elif fault == "ancestor":
        alias = tree.parent / "alias"
        alias.symlink_to(tree)
        target = alias / target.name
    elif fault == "hardlink":
        (tree / "other").hardlink_to(target)
    elif fault == "fifo":
        target.unlink()
        os.mkfifo(target, 0o600)
    elif fault == "mode":
        target.chmod(0o644)
    elif fault == "empty":
        target.write_bytes(b"")
    elif fault == "large":
        monkeypatch.setattr(f, "MAX_FILE_BYTES", 2)
    else:
        original = f.os.read

        def read(fd, size):
            raw = original(fd, size)
            if fault == "replace":
                target.unlink()
            target.write_bytes(b"different private bytes")
            target.chmod(0o600)
            return raw

        monkeypatch.setattr(f.os, "read", read)
    before = len(list(Path("/proc/self/fd").iterdir()))
    with pytest.raises(f.UnconfirmedFiles, match="^Protected filesystem evidence is unconfirmed.$"):
        f.private_file(target)
    assert len(list(Path("/proc/self/fd").iterdir())) == before
