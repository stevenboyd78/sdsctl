"""Bounded durable progress; actual temporary files, no scanner/host operations."""

import hashlib
import importlib.util
import json
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from .test_supplemental_recording_protected import load, p, refusal, write
from .test_supplemental_recording_protected import tree as tree

NAME = "supplemental_recording_checkpoints"
SPEC = importlib.util.spec_from_file_location(NAME, Path(p.__file__).with_name(NAME + ".py"))
c = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = c
SPEC.loader.exec_module(c)


@pytest.fixture
def journal(tree):
    path = tree.root.with_name("PRIVATE_PROGRESS")
    path.mkdir(mode=0o700)
    write(tree.wav, b"first")
    return path


def append(tree, journal, previous=None, *, finalizing=False):
    result = tree.collector.active(tree.expected, finalizing=finalizing)
    return c.append_progress(journal, tree.collector, tree.expected, result, previous_tip=previous)


def read(tree, journal, tip):
    return c.load_progress(journal, tree.collector, tree.expected, expected_tip=tip)


def test_published_checkpoint_survives_reload_and_verifies_every_prior_entry(tree, journal):
    first = append(tree, journal)
    first_raw = (journal / "0000.json").read_bytes()
    write(tree.wav, b"second longer")
    second = append(tree, journal, first)
    tree.collector = p.Collector(load(tree))
    previous = read(tree, journal, second)
    assert previous.wav.size_after == 13
    assert first.count == 1 and second.count == 2 and first.sha256 != second.sha256
    assert (journal / "0000.json").read_bytes() == first_raw
    assert stat.S_IMODE((journal / "0001.json").stat().st_mode) == 0o600
    write(tree.wav, b"shrink")
    refusal(lambda: tree.collector.active(tree.expected, previous=previous))


@pytest.mark.parametrize("fault", ["stale", "digest", "count", "absent", "extra", "gap"])
def test_tip_cannot_be_rolled_back_or_inferred_from_present_files(tree, journal, fault):
    first = append(tree, journal)
    second = append(tree, journal, first)
    if fault == "stale":
        second = first
    elif fault == "digest":
        second = replace(second, sha256="f" * 64)
    elif fault == "count":
        second = replace(second, count=3)
    elif fault == "absent":
        second = None
    elif fault == "extra":
        write(journal / "unexpected")
    else:
        (journal / "0000.json").rename(journal / "0002.json")
    refusal(lambda: read(tree, journal, second))


@pytest.mark.parametrize(
    "fault",
    [
        "truncated",
        "empty",
        "mode",
        "link",
        "symlink",
        "fifo",
        "oversized",
        "changed_first",
        "directory_symlink",
    ],
)
def test_actual_unsafe_checkpoint_files_never_yield_progress(tree, journal, fault):
    first = append(tree, journal)
    second = append(tree, journal, first)
    path = journal / "0000.json"
    if fault == "truncated":
        write(path, path.read_bytes()[:-1])
    elif fault == "empty":
        write(path)
    elif fault == "mode":
        path.chmod(0o644)
    elif fault == "link":
        path.with_name("alias").hardlink_to(path)
        path.with_name("alias").rename(journal.parent / "PRIVATE_ALIAS")
    elif fault == "symlink":
        original = journal.parent / "PRIVATE_ORIGINAL"
        path.rename(original)
        path.symlink_to(original)
    elif fault == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif fault == "oversized":
        with path.open("r+b") as output:
            output.truncate(c.MAX_ENTRY_BYTES + 1)
    elif fault == "changed_first":
        raw = json.loads(path.read_bytes())
        raw["previous"] = "f" * 64
        write(path, p.encode(raw))
    else:
        original = journal.with_name("PRIVATE_MOVED")
        journal.rename(original)
        journal.symlink_to(original, target_is_directory=True)
    refusal(lambda: read(tree, journal, second))


@pytest.mark.parametrize(
    "fault",
    [
        "schema_bool",
        "kind",
        "extra",
        "duplicate",
        "noncanonical",
        "previous",
        "generation",
        "count_bool",
    ],
)
def test_rehashed_malformed_chain_is_not_a_valid_checkpoint(tree, journal, fault):
    tip = append(tree, journal)
    path = journal / "0000.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    if fault == "schema_bool":
        value["schema"] = True
    elif fault == "kind":
        value["kind"] = "start_recording"
    elif fault == "extra":
        value["extra"] = None
    elif fault == "duplicate":
        raw = raw[:-1] + b',"schema":1}'
    elif fault == "noncanonical":
        raw = json.dumps(value, indent=2).encode()
    elif fault == "previous":
        value["previous"] = "f" * 64
    elif fault == "generation":
        value["files"]["generation"] = "f" * 64
    else:
        with pytest.raises(ValueError):
            c.Tip(True, tip.sha256)
        return
    if fault not in ("duplicate", "noncanonical"):
        raw = p.encode(value)
    write(path, raw)
    tip = replace(tip, sha256=hashlib.sha256(raw).hexdigest())
    refusal(lambda: read(tree, journal, tip))


@pytest.mark.parametrize(
    "fault", ["shrink", "replacement", "stage", "publication", "metadata", "temporary"]
)
def test_append_refuses_valid_individual_observations_that_regress(tree, journal, fault):
    if fault in ("publication", "metadata"):
        write(tree.sidecar, b"stable metadata")
    if fault == "temporary":
        write(tree.temporary, b"partial metadata")
    first = append(
        tree, journal, finalizing=fault in ("stage", "publication", "metadata", "temporary")
    )
    if fault == "shrink":
        write(tree.wav)
    elif fault == "replacement":
        tree.wav.rename(tree.wav.with_suffix(".old"))
        write(tree.wav, b"first")
        tree.wav.with_suffix(".old").unlink()
    elif fault == "publication":
        tree.sidecar.unlink()
    elif fault == "metadata":
        write(tree.sidecar, b"other metadata")
    elif fault == "temporary":
        tree.temporary.rename(
            tree.temporary.with_name(tree.temporary.name.replace("abcdefgh", "ijklmnop"))
        )
    refusal(
        lambda: append(
            tree, journal, first, finalizing=fault in ("publication", "metadata", "temporary")
        )
    )
    assert sorted(path.name for path in journal.iterdir()) == ["0000.json"]


@pytest.mark.parametrize("fault", ["write", "fsync_file", "fsync_directory", "late_return"])
def test_uncertain_publication_preserves_evidence_and_cannot_reuse_old_tip(
    tree, journal, monkeypatch, fault
):
    first = append(tree, journal)
    if fault == "write":
        original = os.fdopen

        class Interrupted:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def write(self, raw):
                self.stream.write(raw[:23])
                self.stream.flush()
                raise OSError("PRIVATE interrupted write")

        monkeypatch.setattr(os, "fdopen", lambda *a, **k: Interrupted(original(*a, **k)))
    elif fault == "late_return":
        original = c._read
        calls = 0
        clock = [100.0]
        monkeypatch.setattr(c.time, "monotonic", lambda: clock[0])

        def late(*args):
            nonlocal calls
            result = original(*args)
            calls += 1
            if calls == 2:
                clock[0] = 106.0
            return result

        monkeypatch.setattr(c, "_read", late)
    else:
        original = os.fsync

        def broken(fd):
            original(fd)
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            if directory == (fault == "fsync_directory"):
                raise OSError("PRIVATE fsync failure")

        monkeypatch.setattr(os, "fsync", broken)
    refusal(lambda: append(tree, journal, first))
    monkeypatch.undo()
    raw = (journal / "0001.json").read_bytes()
    refusal(lambda: append(tree, journal, first))
    refusal(lambda: read(tree, journal, first))
    assert (journal / "0001.json").read_bytes() == raw


def test_chain_bound_never_discards_old_checkpoints(tree, journal, monkeypatch):
    monkeypatch.setattr(c, "MAX_ENTRIES", 3)
    tip = None
    for _ in range(3):
        tip = append(tree, journal, tip)
    assert read(tree, journal, tip).wav.size_after == 5
    refusal(lambda: append(tree, journal, tip))
    assert sorted(path.name for path in journal.iterdir()) == [
        "0000.json",
        "0001.json",
        "0002.json",
    ]


def test_private_chain_excludes_recording_root_and_concurrent_writer(tree, journal):
    tip = append(tree, journal)
    with p._private_directory(journal, exclusive=True):
        refusal(lambda: read(tree, journal, tip))
        refusal(lambda: append(tree, journal, tip))
    before = set(tree.root.iterdir())
    refusal(lambda: append(tree, tree.root))
    assert set(tree.root.iterdir()) == before


def test_checkpoint_files_do_not_leak_descriptors_on_repeated_refusal(tree, journal):
    tip = append(tree, journal)
    write(journal / "unexpected")
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(20):
        refusal(lambda: read(tree, journal, tip))
    assert len(os.listdir("/proc/self/fd")) == before
