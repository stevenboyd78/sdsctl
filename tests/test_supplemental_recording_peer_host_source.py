"""Closed peer library inventory, not installed source or action qualification."""

import ast
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace

import pytest

from . import test_supplemental_recording_service_host_source as service
from . import test_supplemental_recording_service_runtime_expectations as declarations

original = service.original
NAME = "supplemental_recording_peer_host_source"
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
            path.write_bytes(b"raise RuntimeError('OBSERVED_SOURCE_MUST_NOT_EXECUTE')\n")
            path.chmod(0o644)
    return m.Layout(runtime, helper)


def test_explicit_peer_inventory_is_distinct_and_never_runs_observed_code(layout):
    evidence = layout.observe()
    assert layout.verify(evidence.sha256) == evidence
    runtime = m.source.files.inventory(layout.runtime)
    helper = m.source.files.inventory(layout.helper)
    assert evidence.sha256 == m.source.checksum(
        dict(schema=1, kind=m.KIND, runtime=runtime, helper=helper)
    )
    assert evidence.file_count == 103 and len(m.MODULES) == 101
    profiles = [
        m.service.Layout(layout.runtime, layout.helper),
        service.app.m.Layout(layout.runtime, layout.helper),
    ]
    profiles.extend(
        m.source.Layout(layout.runtime, layout.helper, **flags)
        for flags in (
            {},
            {"startup": True},
            {"permission_probe": True},
            {"service_preparation": True},
        )
    )
    for profile in profiles:
        original.denied(profile.observe)
        _, kind = profile._profile()
        foreign = m.source.checksum(dict(schema=1, kind=kind, runtime=runtime, helper=helper))
        original.denied(lambda foreign=foreign: layout.verify(foreign))


def test_older_runtime_expectations_do_not_admit_the_new_source_kind():
    supplied = declarations.value()
    assert supplied["source_kind"] == m.service.KIND
    supplied["source_kind"] = m.KIND
    declarations.denied(lambda: declarations.m.decode(supplied))


def test_exact_static_import_graph_keeps_commands_and_older_profiles_closed():
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
                name
                for name in imports
                if name.startswith(
                    ("supplemental_", "qualify_supplemental_", "accept_supplemental_")
                )
            )
    assert seen == m.MODULES and len(seen) == 101
    assert (
        not {
            "supplemental_recording_permission_probe",
            "supplemental_recording_service_permission",
            "supplemental_recording_service_command",
        }
        & seen
    )
    assert len(m.service.MODULES) == 90 and len(service.app.m.MODULES) == 83
    assert len(m.source.SERVICE_MODULES) == 65


@pytest.mark.parametrize("scope", ["inventory", "roots", "complete"])
@pytest.mark.parametrize("allow_serial", [False, True])
@pytest.mark.parametrize("allow_timerfd", [False, True])
def test_reviewed_peer_imports_are_passive_and_dependencies_stay_explicit(
    scope, allow_serial, allow_timerfd
):
    # Reviewed repository code only. A source observation never imports its files.
    script = r"""
import importlib, importlib.abc, json, os, signal, socket, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / "scripts"), str(root / "src")]
scope, serial, timerfd = sys.argv[2], sys.argv[3] == "True", sys.argv[4] == "True"
if not timerfd:
    for name in ("timerfd_create", "timerfd_settime_ns"):
        if hasattr(os, name): delattr(os, name)
prefixes = ("supplemental_", "qualify_supplemental_", "accept_supplemental_")
dependencies = set()
class MissingDependency(Exception): pass
class ReviewedDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        # Python3.11 copy probes optional Jython: deny it as an absent module.
        if fullname == "org":
            raise ModuleNotFoundError("Optional Jython module is unavailable", name="org")
        top = fullname.partition(".")[0]
        if top in sys.stdlib_module_names or top == "sds200" or top.startswith(prefixes):
            return None
        dependencies.add(top)
        if top == "serial" and serial: return None
        raise MissingDependency(top)
sys.meta_path.insert(0, ReviewedDependencies())
try: importlib.import_module("org")
except ModuleNotFoundError as error:
    assert error.name == "org" and "org" not in sys.modules
else: raise AssertionError("Optional Jython dependency was admitted")
def forbidden(*args, **kwargs):
    raise AssertionError("Peer library import attempted process/network/file activity")
for name in ("connect", "connect_ex", "bind", "listen", "send", "sendall", "sendto", "sendmsg"):
    setattr(socket.socket, name, forbidden)
subprocess.Popen = forbidden
os.fork = os.system = os.open = os.mkdir = os.unlink = os.rename = forbidden
os.kill = signal.pidfd_send_signal = forbidden
bundle = importlib.import_module("supplemental_recording_peer_host_source")
try:
    for name in sorted({"inventory": (), "roots": bundle.ROOTS, "complete": bundle.MODULES}[scope]):
        importlib.import_module(name)
except MissingDependency as error:
    assert scope != "inventory" and not serial and str(error) == "serial"
    print(json.dumps({"missing": "serial"}))
    raise SystemExit(0)
private = {name for name in sys.modules if name.startswith(prefixes)}
if scope == "inventory":
    assert not dependencies
    assert not any(name == "sds200" or name.startswith("sds200.") for name in sys.modules)
    assert private <= bundle.MODULES
else:
    assert dependencies == {"serial"}
    deferred = {
        "supplemental_recording_api", "supplemental_recording_assembly",
        "supplemental_recording_schedule",
    }
    assert private == (bundle.MODULES - deferred if scope == "roots" else bundle.MODULES)
    if not timerfd:
        assert not sys.modules["supplemental_recording_service_deadline"].timerfd_available()
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
            str(allow_timerfd),
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


@pytest.mark.parametrize("flag", ["startup", "permission_probe", "service_preparation"])
@pytest.mark.parametrize("value", [True, 0, 1, None])
def test_no_legacy_selector_or_coercion(layout, flag, value):
    original.denied(replace(layout, **{flag: value}).observe)


def test_no_subclass_can_choose_a_foreign_inventory(layout):
    class Other(m.Layout):
        pass

    original.denied(Other(layout.runtime, layout.helper).observe)


@pytest.mark.parametrize("name", sorted(m.HELPER_FILES - m.service.HELPER_FILES))
def test_each_new_library_is_required_and_included_in_the_digest(layout, name):
    evidence = layout.observe()
    path = layout.helper / name
    raw = path.read_bytes()
    path.write_bytes(raw + b"# changed\n")
    original.denied(lambda: layout.verify(evidence.sha256))
    path.unlink()
    original.denied(layout.observe)


@pytest.mark.parametrize("location", ["runtime", "helper", "nested", "empty"])
def test_no_writable_source_directory(layout, location):
    original.test_source_directories_reject_unsafe_permissions(layout, location, 0o775)


@pytest.mark.parametrize("fault", ["runtime", "helper", "metadata", "empty_dir"])
def test_drift_between_full_reads_is_rejected(layout, monkeypatch, fault):
    original.test_changed_second_observation_refused(layout, monkeypatch, fault)


@pytest.mark.parametrize("bound", ["time", "files", "bytes"])
def test_combined_read_bounds_are_preserved(layout, monkeypatch, bound):
    original.test_combined_observation_bounds(layout, monkeypatch, bound)


@pytest.mark.parametrize("name", ["display.js", "nested/package.dat", "__pycache__/module.pyc"])
def test_no_product_asset_or_bytecode_is_excluded(layout, name):
    original.test_no_product_asset_or_bytecode_exclusion(layout, name)
