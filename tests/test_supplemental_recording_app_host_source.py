"""Closed App helper inventory; never imports candidate-provided source."""

import ast
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace

import pytest

from . import test_supplemental_recording_host_source as original

NAME = "supplemental_recording_app_host_source"
SPEC = importlib.util.spec_from_file_location(NAME, original.native.SCRIPTS / (NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


@pytest.fixture
def layout(tmp_path):
    runtime, helper = tmp_path / "sds200", tmp_path / "helper"
    for root, names in ((runtime, m.source.REQUIRED_RUNTIME), (helper, m.HELPER_FILES)):
        root.mkdir(mode=0o755)
        for name in names:
            path = root / name
            path.write_bytes(b"raise RuntimeError('PRIVATE_SOURCE_MUST_NOT_EXECUTE')\\n")
            path.chmod(0o644)
    return m.Layout(runtime, helper)


def test_distinct_closed_bundle_is_read_only_and_legacy_profiles_refuse_it(layout):
    evidence = layout.observe()
    assert layout.verify(evidence.sha256) == evidence
    runtime, helper = (
        m.source.files.inventory(layout.runtime),
        m.source.files.inventory(layout.helper),
    )
    assert evidence.sha256 == m.source.checksum(
        dict(schema=1, kind=m.KIND, runtime=runtime, helper=helper)
    )
    assert evidence.file_count == 85 and len(m.MODULES) == 83
    for flags in ({}, {"startup": True}, {"permission_probe": True}, {"service_preparation": True}):
        original.denied(
            lambda flags=flags: m.source.Layout(layout.runtime, layout.helper, **flags).observe()
        )
    for kind in (
        m.source.KIND,
        m.source.STARTUP_KIND,
        m.source.PERMISSION_KIND,
        m.source.SERVICE_KIND,
    ):
        foreign = m.source.checksum(dict(schema=1, kind=kind, runtime=runtime, helper=helper))
        original.denied(lambda foreign=foreign: layout.verify(foreign))


@pytest.mark.parametrize("location", ["runtime", "helper", "nested", "empty"])
def test_app_source_does_not_admit_writable_directories(layout, location):
    original.test_source_directories_reject_unsafe_permissions(layout, location, 0o775)


@pytest.mark.parametrize("flag", ["startup", "permission_probe", "service_preparation"])
@pytest.mark.parametrize("value", [True, 0, 1, None])
def test_no_legacy_switch_or_non_boolean_coercion(layout, flag, value):
    original.denied(replace(layout, **{flag: value}).observe)


def test_subclasses_cannot_choose_an_unreviewed_inventory(layout):
    class Other(m.Layout):
        pass

    original.denied(Other(layout.runtime, layout.helper).observe)


def test_exact_static_private_import_closure_includes_qualifiers():
    pending, seen = list(m.ROOTS), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        assert name in m.MODULES
        seen.add(name)
        tree = ast.parse((original.native.SCRIPTS / (name + ".py")).read_text())
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0
                imports = [node.module or ""]
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in ("__import__", "exec", "eval")
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("import_module", "spec_from_file_location")
            pending.extend(
                dep
                for dep in imports
                if dep.startswith(
                    ("supplemental_", "qualify_supplemental_", "accept_supplemental_")
                )
            )
    assert seen == m.MODULES and len(seen) == 83
    assert "supplemental_recording_service_command" not in seen
    assert m.source.Layout.__dataclass_fields__.keys() == {
        "runtime",
        "helper",
        "startup",
        "permission_probe",
        "service_preparation",
    }


@pytest.mark.parametrize("scope", ["inventory", "controller", "complete"])
@pytest.mark.parametrize("allow_serial", [False, True])
def test_local_isolated_import_is_passive_with_explicit_dependency(scope, allow_serial):
    # Reviewed repository code only; inventory observations never run this code.
    script = r"""
import importlib
import importlib.abc
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / "scripts"), str(root / "src")]
prefixes = ("supplemental_", "qualify_supplemental_", "accept_supplemental_")
scope, allow_serial = sys.argv[2], sys.argv[3] == "True"
dependencies = set()
class MissingHelperDependency(Exception):
    pass
class OnlyReviewedDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        # Python3.11 copy probes Jython's optional org.python.core and catches
        # ImportError. Deny it as absent (never allow/load an org dependency),
        # rather than turning an ordinary stdlib probe into our hard failure.
        if fullname == "org":
            raise ModuleNotFoundError("Optional Jython module is unavailable", name="org")
        top = fullname.partition(".")[0]
        if top in sys.stdlib_module_names or top == "sds200" or top.startswith(prefixes):
            return None
        dependencies.add(top)
        if top == "serial" and allow_serial:
            return None
        raise MissingHelperDependency(top)
sys.meta_path.insert(0, OnlyReviewedDependencies())
try:
    importlib.import_module("org")
except ModuleNotFoundError as error:
    assert error.name == "org" and "org" not in sys.modules
else:
    raise AssertionError("Optional Jython dependency was admitted")
def forbidden(*args, **kwargs):
    raise AssertionError("App source import attempted network/process activity")
for name in ("connect", "connect_ex", "bind", "listen", "send", "sendall", "sendto"):
    setattr(socket.socket, name, forbidden)
subprocess.Popen = forbidden
os.fork = os.system = forbidden
bundle = importlib.import_module("supplemental_recording_app_host_source")
try:
    targets = {
        "inventory": (), "controller": bundle.ROOTS, "complete": bundle.MODULES,
    }[scope]
    for name in sorted(targets):
        importlib.import_module(name)
except MissingHelperDependency as error:
    assert scope != "inventory" and not allow_serial and str(error) == "serial"
    print(json.dumps({"missing": "serial"}))
    raise SystemExit(0)
private = {name for name in sys.modules if name.startswith(prefixes)}
if scope == "inventory":
    assert not dependencies
    assert not any(name == "sds200" or name.startswith("sds200.") for name in sys.modules)
    assert private <= bundle.MODULES
else:
    assert dependencies == {"serial"}
    # Native construction deliberately defers these imports until construction.
    deferred = {
        "supplemental_recording_api", "supplemental_recording_assembly",
        "supplemental_recording_schedule",
    }
    assert private == (bundle.MODULES - deferred if scope == "controller" else bundle.MODULES)
for name in private:
    assert Path(sys.modules[name].__file__) == root / "scripts" / (name + ".py")
for name in sys.modules:
    if name == "sds200" or name.startswith("sds200."):
        assert Path(sys.modules[name].__file__).is_relative_to(root / "src/sds200")
print(json.dumps({"scope": scope, "dependencies": sorted(dependencies)}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(original.native.SCRIPTS.parent),
            scope,
            str(allow_serial),
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert not result.stderr
    expected = (
        {"missing": "serial"}
        if scope != "inventory" and not allow_serial
        else {"scope": scope, "dependencies": [] if scope == "inventory" else ["serial"]}
    )
    assert json.loads(result.stdout) == expected


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
def test_app_inventory_inherits_filesystem_refusals(layout, fault):
    original.test_incomplete_or_unsafe_graph_refused(layout, fault)


@pytest.mark.parametrize("fault", ["runtime", "helper", "metadata", "empty_dir"])
def test_app_inventory_rejects_changes_between_complete_reads(layout, monkeypatch, fault):
    original.test_changed_second_observation_refused(layout, monkeypatch, fault)


@pytest.mark.parametrize("bound", ["time", "files", "bytes"])
def test_app_inventory_preserves_combined_bounds(layout, monkeypatch, bound):
    original.test_combined_observation_bounds(layout, monkeypatch, bound)


@pytest.mark.parametrize("name", ["display.js", "nested/package.dat", "__pycache__/module.pyc"])
def test_app_inventory_does_not_exclude_product_assets(layout, name):
    original.test_no_product_asset_or_bytecode_exclusion(layout, name)
