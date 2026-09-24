"""Read-only private host graph; no observed file is imported or installed."""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_source as native

NAME = "supplemental_recording_host_source"
SPEC = importlib.util.spec_from_file_location(NAME, native.SCRIPTS / (NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def layout(tmp_path):
    runtime, helper = tmp_path / "sds200", tmp_path / "helper"
    for root, names in ((runtime, m.REQUIRED_RUNTIME), (helper, m.HELPER_FILES)):
        root.mkdir()
        for name in names:
            path = root / name
            path.write_bytes(b"raise RuntimeError('PRIVATE_UNTRUSTED_SOURCE')\n")
            path.chmod(0o644)
    return m.Layout(runtime, helper)


def denied(callback):
    with pytest.raises(m.UnconfirmedSource) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def test_whole_package_and_closed_helper_inventory_without_candidate_import(layout):
    before = {
        p: m.files.identity(p.stat())
        for root in (layout.runtime, layout.helper)
        for p in root.iterdir()
    }
    result = layout.observe()
    assert layout.verify(result.sha256) == result == layout.observe()
    runtime, helper = m.files.inventory(layout.runtime), m.files.inventory(layout.helper)
    assert result.sha256 == m.checksum(
        {"schema": 1, "kind": m.KIND, "runtime": runtime, "helper": helper}
    )
    assert result.runtime_sha256 == m.checksum(runtime)
    assert result.helper_sha256 == m.checksum(helper)
    assert result.file_count == len(before)
    assert result.total_bytes == sum(p.stat().st_size for p in before)
    assert before == {p: m.files.identity(p.stat()) for p in before}


def test_reviewed_roots_close_the_entire_private_static_import_graph():
    pending, seen, product = list(m.ROOTS), set(), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        assert name in m.MODULES
        seen.add(name)
        tree = ast.parse((native.SCRIPTS / (name + ".py")).read_text())
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, (name, node.lineno)
                imports = [node.module or ""]
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in ("__import__", "exec", "eval"), (name, node.lineno)
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("import_module", "spec_from_file_location"), (
                        name,
                        node.lineno,
                    )
            for dependency in imports:
                if dependency.startswith(("supplemental_", "accept_supplemental_")):
                    pending.append(dependency)
                elif dependency.startswith("sds200"):
                    product.add(dependency)
    assert seen == m.MODULES and len(seen) == 54
    assert product == {
        "sds200.daemon_recording",
        "sds200.scanner_display_configuration",
        "sds200.scanner_display_profile_storage",
        "sds200.scanner_display_upload",
    }
    # That product module imports more product code. The FULL package, not just
    # this one .py file, is pinned by Layout; third-party/stdlib remain separate.
    assert m.MODULES != native.m.MODULES


def test_real_isolated_import_closes_local_graph_without_starting_services():
    # Reviewed local source, not the untrusted inventory fixture. This is an
    # import smoke test in the test interpreter, NOT installed image attestation.
    script = r"""
import importlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
repository = Path(sys.argv[1])
sys.path[:0] = [str(repository / "scripts"), str(repository / "src")]
def forbidden(*args, **kwargs):
    raise AssertionError("Source import attempted network/process activity")
for name in ("connect", "connect_ex", "bind", "listen", "send", "sendall", "sendto"):
    setattr(socket.socket, name, forbidden)
subprocess.Popen = forbidden
os.fork = os.system = forbidden
bundle = importlib.import_module("supplemental_recording_host_source")
for name in sorted(bundle.ROOTS):
    importlib.import_module(name)
private = {name for name in sys.modules if name.startswith("supplemental_")}
assert private == bundle.MODULES
for name in private:
    assert Path(sys.modules[name].__file__) == repository / "scripts" / (name + ".py")
product = [name for name in sys.modules if name == "sds200" or name.startswith("sds200.")]
assert "sds200.daemon_recording" in product
for name in product:
    assert Path(sys.modules[name].__file__).is_relative_to(repository / "src" / "sds200")
print(json.dumps({"private": len(private), "product": len(product)}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(native.SCRIPTS.parent)],
        capture_output=True,
        timeout=5,
        check=True,
    )
    assert not result.stderr
    report = json.loads(result.stdout)
    assert report["private"] == 54 and report["product"] >= 1


@pytest.mark.parametrize(
    "name", ["display.js", "nested/package.dat", "__pycache__/module.cpython-314.pyc"]
)
def test_no_product_asset_or_bytecode_exclusion(layout, name):
    path = layout.runtime / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"before")
    path.chmod(0o644)
    result = layout.observe()
    path.write_bytes(b"change")
    denied(lambda: layout.verify(result.sha256))


@pytest.mark.parametrize(
    "fault",
    [
        "missing_helper",
        "missing_product",
        "extra_file",
        "empty_dir",
        "pycache",
        "symlink",
        "hardlink",
        "fifo",
        "group_write",
        "special_bits",
    ],
)
def test_incomplete_or_unsafe_graph_refused(layout, fault):
    path = layout.helper / "supplemental_recording_host_source.py"
    if fault == "missing_helper":
        path.unlink()
    elif fault == "missing_product":
        (layout.runtime / "daemon_recording.py").unlink()
    elif fault == "extra_file":
        (layout.helper / "sitecustomize.py").write_bytes(b"PRIVATE")
    elif fault in ("empty_dir", "pycache"):
        (layout.helper / ("empty" if fault == "empty_dir" else "__pycache__")).mkdir()
    elif fault in ("symlink", "hardlink", "fifo"):
        path.unlink()
        if fault == "symlink":
            path.symlink_to(layout.runtime / "__init__.py")
        elif fault == "hardlink":
            path.hardlink_to(layout.runtime / "__init__.py")
        else:
            os.mkfifo(path)
    else:
        path.chmod(0o664 if fault == "group_write" else 0o4644)
    denied(layout.observe)


@pytest.mark.parametrize("field", ["runtime", "helper"])
@pytest.mark.parametrize("path", [Path("/"), Path("relative"), Path("/tmp/../tmp"), "/tmp"])
def test_noncanonical_paths_refused(layout, field, path):
    denied(replace(layout, **{field: path}).observe)


@pytest.mark.parametrize("relation", ["equal", "helper_inside", "runtime_inside", "symlink"])
def test_overlapping_or_linked_roots_refused(layout, relation):
    if relation == "equal":
        layout = replace(layout, helper=layout.runtime)
    elif relation == "helper_inside":
        layout = replace(layout, helper=layout.runtime / "helper")
    elif relation == "runtime_inside":
        layout = replace(layout, runtime=layout.helper / "sds200")
    else:
        link = layout.helper.parent / "linked"
        link.symlink_to(layout.helper)
        layout = replace(layout, helper=link)
    denied(layout.observe)


@pytest.mark.parametrize("pin", [None, True, "bad", "a" * 64])
def test_unknown_expected_pin_cannot_qualify_source(layout, pin):
    denied(lambda: layout.verify(pin))


@pytest.mark.parametrize("fault", ["runtime", "helper", "metadata", "empty_dir"])
def test_changed_second_observation_refused(layout, monkeypatch, fault):
    original, called = m.Layout._snapshot, []

    def changed(self):
        value = original(self)
        if not called:
            if fault == "runtime":
                (self.runtime / "daemon_recording.py").write_bytes(b"PRIVATE_CHANGED")
            elif fault == "helper":
                (self.helper / "supplemental_recording_host_source.py").write_bytes(
                    b"PRIVATE_CHANGED"
                )
            elif fault == "metadata":
                (self.helper / "supplemental_recording_host_source.py").chmod(0o600)
            else:
                (self.helper / "empty").mkdir()
        called.append(True)
        return value

    monkeypatch.setattr(m.Layout, "_snapshot", changed)
    denied(layout.observe)


@pytest.mark.parametrize("bound", ["time", "files", "bytes"])
def test_combined_observation_bounds(layout, monkeypatch, bound):
    if bound == "time":
        monkeypatch.setattr(m, "MAX_SECONDS", -1)
    elif bound == "files":
        monkeypatch.setattr(m.files, "MAX_FILES", len(m.HELPER_FILES))
    else:
        size = sum(path.stat().st_size for path in layout.helper.iterdir())
        monkeypatch.setattr(m.files, "MAX_TOTAL_BYTES", size)
    denied(layout.observe)


def test_native_and_legacy_fingerprints_are_not_host_source(layout):
    runtime, helper = m.files.inventory(layout.runtime), m.files.inventory(layout.helper)
    for foreign in (
        {"schema": 1, "kind": native.m.KIND, "runtime": runtime, "native": helper},
        {"runtime": runtime, "launchers": helper, "entries": {}},
        {"schema": 1, "kind": m.KIND, "runtime": {}, "helper": helper},
    ):
        denied(lambda foreign=foreign: layout.verify(m.checksum(foreign)))
