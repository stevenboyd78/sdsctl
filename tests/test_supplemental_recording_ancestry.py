"""Unrelated directory siblings are outside selected receipt/artifact scopes."""

import os
import time
from contextlib import contextmanager

import pytest

from . import test_supplemental_recording_launch_plan as launch
from . import test_supplemental_recording_owner as owner


@contextmanager
def selected(kind, root):
    if kind == "private":
        with launch.p._private_directory(root, exclusive=True):
            yield
    elif kind == "artifacts":
        with owner.r.opened_root(root, deadline=time.monotonic() + 2):
            yield
    else:
        receipts = owner.o._Receipts(root)
        try:
            yield
            receipts.check()
        finally:
            receipts.close()


@pytest.mark.parametrize("kind", ["private", "artifacts", "receipts"])
@pytest.mark.parametrize("fault", ["sibling", "inside", "mode", "replace", "symlink"])
def test_selected_scope_distinguishes_siblings_from_mutation(tmp_path, kind, fault):
    parent = tmp_path / "holder"
    parent.mkdir(mode=0o700)
    root = parent / "selected"
    root.mkdir(mode=0o700)
    descriptors = len(os.listdir("/proc/self/fd"))

    def observe():
        with selected(kind, root):
            if fault == "sibling":
                (parent / "unrelated").mkdir()
            elif fault == "inside":
                (root / "unexpected").mkdir()
            elif fault == "mode":
                parent.chmod(0o711)
            else:
                saved = parent.with_name("saved-holder")
                parent.rename(saved)
                if fault == "replace":
                    parent.mkdir()
                else:
                    parent.symlink_to(saved, target_is_directory=True)

    if fault == "sibling":
        observe()
    else:
        with pytest.raises(ValueError):
            observe()
    assert len(os.listdir("/proc/self/fd")) == descriptors


@pytest.mark.parametrize("fault", ["sibling", "inside", "mode", "replace", "symlink"])
def test_unlocked_launch_probe_keeps_same_scope_boundary(tmp_path, monkeypatch, fault):
    parent = tmp_path / "holder"
    parent.mkdir(mode=0o700)
    root = parent / "selected"
    root.mkdir(mode=0o700)
    inode = root.stat().st_ino
    original, changed = os.fstat, []

    def statting(fd):
        value = original(fd)
        if value.st_ino == inode and not changed:
            changed.append(True)
            if fault == "sibling":
                (parent / "unrelated").mkdir()
            elif fault == "inside":
                (root / "unexpected").mkdir()
            elif fault == "mode":
                parent.chmod(0o711)
            else:
                saved = parent.with_name("saved-holder")
                parent.rename(saved)
                if fault == "replace":
                    parent.mkdir()
                else:
                    parent.symlink_to(saved, target_is_directory=True)
        return value

    monkeypatch.setattr(os, "fstat", statting)
    descriptors = len(os.listdir("/proc/self/fd"))
    if fault == "sibling":
        launch.m._probe_directory(root)
    else:
        with pytest.raises(ValueError):
            launch.m._probe_directory(root)
    assert changed == [True]
    assert len(os.listdir("/proc/self/fd")) == descriptors
