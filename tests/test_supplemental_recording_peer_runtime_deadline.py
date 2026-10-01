"""The original paired deadline must reach nested cooperative inventory reads."""

import os

import pytest

from . import test_supplemental_recording_peer_runtime_pair as original

m = original.m
layout, image_umask, supervised = original.layout, original.image_umask, original.supervised
image, configured, helper, pair = (
    original.image,
    original.configured,
    original.helper,
    original.pair,
)


def test_original_pair_deadline_reaches_source_and_interpreter_inventories(pair, monkeypatch):
    sources, interpreters, ends = [], [], []
    trees, snapshots = [], []
    source_type = m.declarations.source.source.Layout
    original_source = source_type.verify
    original_runtime = m.launch.runtime.Layout.verify_supervised
    original_tree = m.declarations.source.source.files.inventory
    original_snapshot = m.launch.runtime.Layout._snapshot

    def source(self, expected, **kwargs):
        sources.append(kwargs.get("deadline"))
        return original_source(self, expected, **kwargs)

    def runtime(self, expected, timezone, **kwargs):
        interpreters.append(kwargs.get("deadline"))
        return original_runtime(self, expected, timezone, **kwargs)

    def tree(root, **kwargs):
        trees.append(kwargs.get("deadline"))
        return original_tree(root, **kwargs)

    def snapshot(self, deadline, **kwargs):
        snapshots.append(deadline)
        return original_snapshot(self, deadline, **kwargs)

    monkeypatch.setattr(source_type, "verify", source)
    monkeypatch.setattr(m.launch.runtime.Layout, "verify_supervised", runtime)
    monkeypatch.setattr(m.declarations.source.source.files, "inventory", tree)
    monkeypatch.setattr(m.launch.runtime.Layout, "_snapshot", snapshot)
    for qualifier in pair.qualifiers.values():
        collect = qualifier._collect_before

        def capture(deadline, original_collect=collect):
            ends.append(deadline)
            return original_collect(deadline)

        monkeypatch.setattr(qualifier, "_collect_before", capture)
    assert pair.obj() is None
    assert len(ends) == 2 and ends[0] == ends[1]
    assert sources == [ends[0]] * 4
    assert interpreters == [ends[0]] * 2
    assert trees == [ends[0]] * 16
    assert snapshots == [ends[0]] * 4


@pytest.fixture
def source_layout(tmp_path):
    graph = m.declarations.source
    runtime, helper = tmp_path / "source", tmp_path / "helper"
    for root, names in ((runtime, graph.source.REQUIRED_RUNTIME), (helper, graph.HELPER_FILES)):
        root.mkdir(mode=0o755)
        for name in names:
            path = root / name
            path.write_bytes(b"raise RuntimeError('DO_NOT_IMPORT_OBSERVED_SOURCE')\n")
            path.chmod(0o644)
    return graph.Layout(runtime, helper)


@pytest.mark.parametrize("bad", [True, "later", -1, float("nan"), float("inf")])
def test_source_deadline_rejects_invalid_or_expired_input_before_inventory(
    source_layout, monkeypatch, bad
):
    module = m.declarations.source.source
    monkeypatch.setattr(
        module.files, "inventory", lambda *_a, **_k: pytest.fail("Unexpected file read")
    )
    with pytest.raises(module.UnconfirmedSource):
        source_layout.observe(deadline=bad)


@pytest.mark.parametrize("bad", [True, "later", -1, float("nan"), float("inf")])
def test_interpreter_deadline_rejects_before_any_snapshot(supervised, monkeypatch, bad):
    module = m.launch.runtime
    monkeypatch.setattr(
        module.Layout, "_snapshot", lambda *_a, **_k: pytest.fail("Unexpected runtime read")
    )
    with pytest.raises(module.UnconfirmedRuntime):
        supervised.observe_supervised("America/Denver", deadline=bad)


@pytest.mark.parametrize("bad", [True, "later", -1, float("nan"), float("inf")])
def test_tree_deadline_rejects_before_opening_any_descriptor(tmp_path, monkeypatch, bad):
    module = m.declarations.source.source.files
    monkeypatch.setattr(os, "open", lambda *_a, **_k: pytest.fail("Unexpected path open"))
    with pytest.raises(module.UnconfirmedFiles):
        module.inventory(tmp_path, deadline=bad)


def test_source_finishing_first_snapshot_late_never_starts_a_second(source_layout, monkeypatch):
    module = m.declarations.source.source
    original_snapshot, calls = module.Layout._snapshot, []
    end = module.time.monotonic() + 1

    def snapshot(self, *, deadline=None):
        calls.append(deadline)
        result = original_snapshot(self, deadline=deadline)
        monkeypatch.setattr(module.time, "monotonic", lambda: end + 0.01)
        return result

    monkeypatch.setattr(module.Layout, "_snapshot", snapshot)
    with pytest.raises(module.UnconfirmedSource):
        source_layout.observe(deadline=end)
    assert calls == [end]


def test_source_expiry_between_product_and_helper_does_not_open_a_second_tree(
    source_layout, monkeypatch
):
    module = m.declarations.source.source
    inventory, calls = module.files.inventory, []
    end = module.time.monotonic() + 1

    def first(root, **kwargs):
        calls.append(root)
        result = inventory(root, **kwargs)
        monkeypatch.setattr(module.time, "monotonic", lambda: end + 0.01)
        return result

    monkeypatch.setattr(module.files, "inventory", first)
    with pytest.raises(module.UnconfirmedSource):
        source_layout.observe(deadline=end)
    assert calls == [source_layout.runtime]


def test_interpreter_finishing_first_snapshot_late_never_starts_a_second(supervised, monkeypatch):
    module = m.launch.runtime
    original_snapshot, calls = module.Layout._snapshot, []
    end = module.time.monotonic() + 1

    def snapshot(self, deadline, **kwargs):
        calls.append(deadline)
        result = original_snapshot(self, deadline, **kwargs)
        monkeypatch.setattr(module.time, "monotonic", lambda: end + 0.01)
        return result

    monkeypatch.setattr(module.Layout, "_snapshot", snapshot)
    with pytest.raises(module.UnconfirmedRuntime):
        supervised.observe_supervised("America/Denver", deadline=end)
    assert calls == [end]


def test_source_future_outer_limit_does_not_extend_original_eight_seconds(
    source_layout, monkeypatch
):
    module = m.declarations.source.source
    original_snapshot, calls = module.Layout._snapshot, []
    expected = source_layout.observe()
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)

    def snapshot(self, *, deadline=None):
        calls.append(deadline)
        return original_snapshot(self, deadline=deadline)

    monkeypatch.setattr(module.Layout, "_snapshot", snapshot)
    assert source_layout.verify(expected.sha256, deadline=10000) == expected
    assert calls == [108.0, 108.0]


def test_interpreter_future_outer_limit_does_not_extend_original_twenty_seconds(
    supervised, monkeypatch
):
    module = m.launch.runtime
    original_snapshot, calls = module.Layout._snapshot, []
    expected = supervised.observe_supervised("America/Denver")
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)

    def snapshot(self, deadline, **kwargs):
        calls.append(deadline)
        return original_snapshot(self, deadline, **kwargs)

    monkeypatch.setattr(module.Layout, "_snapshot", snapshot)
    assert (
        supervised.verify_supervised(expected.sha256, "America/Denver", deadline=10000) == expected
    )
    assert calls == [120.0, 120.0]


@pytest.mark.parametrize("allowance,elapsed", [(1, 1.01), (60, 2.01)])
def test_tree_expiry_during_read_closes_all_owned_descriptors(
    tmp_path, monkeypatch, allowance, elapsed
):
    module = m.declarations.source.source.files
    path = tmp_path / "fixture.py"
    path.write_bytes(b"PRIVATE source bytes\n")
    original_open, original_read = os.open, os.read
    handles = []
    began = module.time.monotonic()
    end = began + allowance

    def opened(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        handles.append(fd)
        return fd

    def read(*args, **kwargs):
        result = original_read(*args, **kwargs)
        if result:
            monkeypatch.setattr(module.time, "monotonic", lambda: began + elapsed)
        return result

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "read", read)
    with pytest.raises(module.UnconfirmedFiles):
        module.inventory(tmp_path, deadline=end)
    assert handles
    for fd in handles:
        with pytest.raises(OSError):
            os.fstat(fd)
