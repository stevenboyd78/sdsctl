"""Read-only native bundle evidence; candidate Python is never imported."""

import ast
import hashlib
import importlib.util
import os
import sys
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
    runtime.mkdir()
    native.mkdir()
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
    assert first.file_count == len(before) == 24
    assert first.total_bytes == sum(item.st_size for item in before.values())
    for p, stated in before.items():
        assert m.files.identity(p.stat()) == m.files.identity(stated)
    assert set(before) == {p for root in (layout.runtime, layout.native) for p in root.iterdir()}


@pytest.mark.parametrize(
    "name", ["display.js", "styles.css", "__pycache__/runtime.cpython-314.pyc"]
)
def test_package_assets_and_bytecode_are_not_filtered(layout, name):
    original = layout.observe()
    path = layout.runtime / name
    path.parent.mkdir(exist_ok=True)
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
