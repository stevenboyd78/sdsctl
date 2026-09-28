"""Read-only native bundle evidence; candidate Python is never imported."""

import ast
import hashlib
import importlib.util
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_files as file_tests
from . import test_supplemental_handoff_policy as policy_tests

NAME = "supplemental_recording_source"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location(NAME, SCRIPTS / (NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def layout(tmp_path):
    runtime, native = tmp_path / "sds200", tmp_path / "native"
    runtime.mkdir(mode=0o755)
    native.mkdir(mode=0o755)
    # Importing even one of these files would fail this test. No sys.path or
    # import mechanism is pointed at either candidate directory.
    for root, names in ((runtime, m.REQUIRED_RUNTIME), (native, m.NATIVE_FILES)):
        for name in names:
            (root / name).write_bytes(b"raise RuntimeError('PRIVATE_CANDIDATE')\n")
            (root / name).chmod(0o644)
    return m.Layout(runtime, native)


def denied(callback):
    with pytest.raises(m.UnconfirmedSource) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE
    assert caught.value.__suppress_context__


def test_read_only_deterministic_closed_evidence(layout):
    assert m.files is file_tests.f
    assert m.checksum is policy_tests.policy.checksum
    before = {p: p.stat() for root in (layout.runtime, layout.native) for p in root.iterdir()}
    first = layout.observe()
    assert first == layout.observe() == layout.verify(first.sha256)
    runtime, native = m.files.inventory(layout.runtime), m.files.inventory(layout.native)
    assert first.sha256 == m.checksum(
        {"schema": 1, "kind": m.KIND, "runtime": runtime, "native": native}
    )
    assert first.runtime_sha256 == m.checksum(runtime)
    assert first.native_sha256 == m.checksum(native)
    assert first.file_count == len(before) == len(m.NATIVE_FILES) + len(m.REQUIRED_RUNTIME)
    assert first.total_bytes == sum(item.st_size for item in before.values())
    for p, stated in before.items():
        assert m.files.identity(p.stat()) == m.files.identity(stated)
    assert set(before) == {p for root in (layout.runtime, layout.native) for p in root.iterdir()}


def test_current_full_inventories_surround_one_read_only_observation(layout, monkeypatch):
    expected, trace = layout.observe(), []
    snapshot = m.Layout._snapshot

    def read(self):
        trace.append("snapshot")
        return snapshot(self)

    def observe():
        trace.append("observe")
        return result

    result = object()
    monkeypatch.setattr(m.Layout, "_snapshot", read)
    end = time.monotonic() + 2
    assert layout.verify_during(expected.sha256, observe, deadline=end) is result
    assert trace == ["snapshot", "observe", "snapshot"]
    assert time.monotonic() < end


@pytest.mark.parametrize("stage", ["before", "during", "exception", "late"])
def test_bracket_never_returns_result_for_drift_or_late_callback(layout, monkeypatch, stage):
    expected = layout.observe()
    calls = []
    real_clock = time.monotonic
    end = real_clock() + 2
    path = layout.runtime / "daemon_runtime.py"
    if stage == "before":
        path.write_bytes(b"PRIVATE_CHANGED")

    def observe():
        calls.append(True)
        if stage == "during":
            path.write_bytes(b"PRIVATE_CHANGED")
        elif stage == "exception":
            raise OSError("PRIVATE observation error")
        elif stage == "late":
            monkeypatch.setattr(m.time, "monotonic", lambda: end)
        return object()

    before = len(os.listdir("/proc/self/fd"))
    denied(lambda: layout.verify_during(expected.sha256, observe, deadline=end))
    assert len(calls) == (stage != "before")
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("end", [None, True, float("nan"), float("inf"), -1])
def test_bracket_rejects_invalid_absolute_deadline_before_callback(layout, end):
    calls = []
    denied(
        lambda: layout.verify_during(layout.observe().sha256, lambda: calls.append(1), deadline=end)
    )
    assert calls == []


@pytest.mark.parametrize(
    "name", ["display.js", "styles.css", "__pycache__/runtime.cpython-314.pyc"]
)
def test_package_assets_and_bytecode_are_not_filtered(layout, name):
    original = layout.observe()
    path = layout.runtime / name
    path.parent.mkdir(mode=0o755, exist_ok=True)
    path.write_bytes(b"first")
    path.chmod(0o644)
    first = layout.observe()
    assert first.file_count == original.file_count + 1
    assert first.native_sha256 == original.native_sha256
    assert first.runtime_sha256 != original.runtime_sha256
    path.write_bytes(b"other")  # Same length is not same source.
    denied(lambda: layout.verify(first.sha256))
    assert layout.observe().runtime_sha256 != first.runtime_sha256


@pytest.mark.parametrize(
    "fault",
    [
        "missing_native",
        "missing_runtime",
        "extra",
        "empty_dir",
        "pycache",
        "symlink",
        "hardlink",
        "fifo",
        "file_directory",
        "root_link",
        "parent_link",
        "group_write",
        "other_write",
        "setuid",
    ],
)
def test_unsafe_or_open_source_graph_refused(layout, fault):
    path = layout.native / "accept_supplemental_recording.py"
    if fault == "missing_native":
        path.unlink()
    elif fault == "missing_runtime":
        (layout.runtime / "daemon_runtime.py").unlink()
    elif fault == "extra":
        (layout.native / "sitecustomize.py").write_bytes(b"PRIVATE")
    elif fault in ("empty_dir", "pycache"):
        (layout.native / ("PRIVATE" if fault == "empty_dir" else "__pycache__")).mkdir()
    elif fault == "symlink":
        path.unlink()
        path.symlink_to(layout.runtime / "__init__.py")
    elif fault == "hardlink":
        path.unlink()
        path.hardlink_to(layout.runtime / "__init__.py")
    elif fault == "fifo":
        path.unlink()
        os.mkfifo(path)
    elif fault == "file_directory":
        path.unlink()
        path.mkdir()
    elif fault in ("root_link", "parent_link"):
        link = layout.native.parent / "PRIVATE_LINK"
        link.symlink_to(layout.native if fault == "root_link" else layout.native.parent)
        layout = replace(layout, native=link if fault == "root_link" else link / "native")
    else:
        path.chmod({"group_write": 0o664, "other_write": 0o646, "setuid": 0o4644}[fault])
    denied(layout.observe)


@pytest.mark.parametrize("location", ["runtime", "native", "nested", "empty"])
@pytest.mark.parametrize("mode", [0o775, 0o757, 0o2755, 0o1755])
def test_source_directories_cannot_allow_replacement_of_read_only_files(layout, location, mode):
    for root in (layout.runtime, layout.native):
        root.chmod(0o755)
    if location in ("runtime", "native"):
        directory = getattr(layout, location)
    else:
        directory = layout.runtime / location
        directory.mkdir(mode=0o755)
        if location == "nested":
            (directory / "component.py").write_bytes(b"PRIVATE_NEVER_IMPORT\n")
            (directory / "component.py").chmod(0o644)
    original = layout.observe()
    directory.chmod(mode)
    before = len(os.listdir("/proc/self/fd"))
    denied(lambda: layout.verify(original.sha256))
    assert len(os.listdir("/proc/self/fd")) == before
    assert directory.stat().st_mode & 0o7777 == mode  # Never repairs observed input.


@pytest.mark.parametrize("stage", ["before", "during"])
def test_directory_policy_is_checked_in_both_sides_of_source_bracket(layout, stage):
    expected, calls = layout.observe(), []
    before = len(os.listdir("/proc/self/fd"))
    if stage == "before":
        layout.native.chmod(0o775)

    def observe():
        calls.append(True)
        layout.native.chmod(0o775)
        return "not admitted"

    denied(lambda: layout.verify_during(expected.sha256, observe, deadline=time.monotonic() + 2))
    assert len(calls) == (stage == "during")
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("field", ["runtime", "native"])
@pytest.mark.parametrize("path", [Path("/"), Path("relative"), Path("/tmp/../tmp"), "/tmp"])
def test_paths_are_explicit_absolute_and_canonical(layout, field, path):
    denied(replace(layout, **{field: path}).observe)


@pytest.mark.parametrize("relation", ["equal", "runtime_inside", "native_inside"])
def test_roots_cannot_overlap(layout, relation):
    if relation == "equal":
        layout = replace(layout, native=layout.runtime)
    elif relation == "runtime_inside":
        layout = replace(layout, runtime=layout.native / "sds200")
    else:
        layout = replace(layout, native=layout.runtime / "native")
    denied(layout.observe)


@pytest.mark.parametrize("value", [None, 0, "PRIVATE", "A" * 64, "0" * 64, "a" * 63])
def test_independently_supplied_expected_pin_is_required(layout, value):
    denied(lambda: layout.verify(value))


@pytest.mark.parametrize("fault", ["content", "metadata", "extra_empty_native_directory"])
def test_between_observation_drift_fails_closed(layout, monkeypatch, fault):
    original, calls = m.Layout._snapshot, []

    def snapshot(self):
        result = original(self)
        if not calls:
            if fault == "content":
                (self.runtime / "daemon_runtime.py").write_bytes(b"PRIVATE_CHANGED")
            elif fault == "metadata":
                (self.native / "accept_supplemental_recording.py").chmod(0o600)
            else:
                (self.native / "PRIVATE").mkdir()
        calls.append(True)
        return result

    monkeypatch.setattr(m.Layout, "_snapshot", snapshot)
    denied(layout.observe)


@pytest.mark.parametrize("bound", ["files", "bytes", "time"])
def test_combined_inventory_and_elapsed_bounds(layout, monkeypatch, bound):
    if bound == "time":
        monkeypatch.setattr(m, "MAX_SECONDS", -1)
    else:
        # Each root independently fits; the combined bundle must still fit.
        name = "MAX_FILES" if bound == "files" else "MAX_TOTAL_BYTES"
        largest = len(m.NATIVE_FILES)
        limit = (
            largest
            if bound == "files"
            else largest * len(b"raise RuntimeError('PRIVATE_CANDIDATE')\n")
        )
        monkeypatch.setattr(m.files, name, limit)
    denied(layout.observe)


def test_fixed_bundle_closes_all_private_imports_including_lazy_entry_import():
    assert len(m.MODULES) == 29
    found = set()
    for module in sorted(m.MODULES):
        tree = ast.parse((SCRIPTS / (module + ".py")).read_text())
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [name.name for name in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ):
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                imports = [node.args[0].value]
            for name in imports:
                if name.startswith(("supplemental_", "accept_supplemental_")):
                    assert name in m.MODULES, (module, name)
                    found.add(name)
    assert "supplemental_recording_control" in found
    assert "supplemental_recording_channel" in found
    assert "supplemental_recording_launch_plan" in found


def test_legacy_five_wrapper_fingerprint_is_not_recording_authority(layout):
    legacy = {
        "runtime": m.files.inventory(layout.runtime),
        "launchers": {
            name: {}
            for name in (
                "accept_supplemental_daemon.py",
                "guard_supplemental_acceptance.py",
                "accept_supplemental_web.py",
            )
        },
        "entries": {"sdsctl-supplemental-acceptance": {}, "sdsctl-supplemental-acceptance-web": {}},
    }
    denied(lambda: layout.verify(m.checksum(legacy)))
    # A plain concatenation of bytes is also not the closed inventory schema.
    plain = hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted(layout.native.iterdir()))
    ).hexdigest()
    denied(lambda: layout.verify(plain))
