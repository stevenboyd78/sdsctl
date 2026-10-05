"""Isolated staged peer-library imports, never installed launcher admission.

Only reviewed checkout bytes are executed. Runtime/Engine/platform/input trust
are deliberately not asserted by this fixture or by the resulting source hash.
No scanner, Home Assistant, container or active service is contacted.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import serial

from . import test_supplemental_recording_peer_host_source as bundle

BOOTSTRAP = r"""
import importlib, importlib.abc, importlib.machinery, json, os, signal, socket, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
scope, module = sys.argv[2:4]
prefixes = ("supplemental_", "qualify_supplemental_", "accept_supplemental_")
# Deliberately leave known-good checkout/installed fallback candidates visible.
# Missing staged sources must still fail at our finder instead of using them.
sys.path.extend(json.loads(sys.argv[4]))
class StagedImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.partition(".")[0]
        if top == "org":
            raise ModuleNotFoundError("Optional Jython module absent", name="org")
        if top in sys.stdlib_module_names:
            return None
        if top.startswith(prefixes):
            base = root / "scripts"
        elif top in ("sds200", "serial"):
            base = root / ("src" if top == "sds200" else "dependencies")
        else:
            raise ImportError("Undeclared staged peer dependency")
        search = [str(base)] if path is None else list(path)
        if not all(Path(p).is_relative_to(base) and Path(p).resolve() == Path(p) for p in search):
            raise ImportError("Staged peer search path escaped")
        spec = importlib.machinery.PathFinder.find_spec(fullname, search)
        if spec is None:
            raise ModuleNotFoundError("Missing staged peer module: " + fullname, name=fullname)
        origin = Path(spec.origin)
        if not origin.is_relative_to(base) or origin.resolve(strict=True) != origin:
            raise ImportError("Staged peer source escaped")
        return spec
sys.meta_path.insert(0, StagedImports())
def forbidden(*args, **kwargs):
    raise AssertionError("Peer import attempted active process/network/file operation")
for name in ("connect", "connect_ex", "bind", "listen", "send", "sendall", "sendto", "sendmsg"):
    setattr(socket.socket, name, forbidden)
subprocess.Popen = forbidden
os.fork = os.system = os.open = os.mkdir = os.unlink = os.rename = forbidden
os.kill = signal.pidfd_send_signal = forbidden
inventory = importlib.import_module("supplemental_recording_peer_host_source")
if sys.argv[5] == "preparation": inventory = inventory.PreparationProfile
names = inventory.ROOTS if scope == "roots" else inventory.MODULES
if scope == "one": names = (module,)
for name in sorted(names): importlib.import_module(name)
private = {name for name in sys.modules if name.startswith(prefixes)}
deferred = {"supplemental_recording_api", "supplemental_recording_assembly",
            "supplemental_recording_schedule"}
if scope == "one":
    assert module in private and private <= inventory.MODULES
else:
    assert private == (inventory.MODULES - deferred if scope == "roots" else inventory.MODULES)
for name, value in tuple(sys.modules.items()):
    top = name.partition(".")[0]
    if name in private:
        assert Path(value.__file__) == root / "scripts" / (name + ".py")
    elif top in ("sds200", "serial"):
        base = root / ("src" if top == "sds200" else "dependencies") / top
        assert Path(value.__file__).is_relative_to(base)
if scope != "one": assert "serial" in sys.modules
assert "sds200._public" not in sys.modules
print(json.dumps({"scope": scope, "private_modules": len(private)}))
"""


@pytest.fixture(params=["library", "preparation"])
def peer_bundle(tmp_path, request):
    root = tmp_path / "peer-bundle"
    profile = bundle.m.PreparationProfile if request.param == "preparation" else bundle.m
    repo = bundle.original.native.SCRIPTS.parent
    scripts, product = root / "scripts", root / "src/sds200"
    scripts.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(repo / "src/sds200", product, ignore=ignore)
    for name in profile.HELPER_FILES:
        shutil.copyfile(repo / "scripts" / name, scripts / name)
    dependency = root / "dependencies/serial"
    shutil.copytree(Path(serial.__file__).parent, dependency, ignore=ignore)
    # Only these newly copied, owned disposable fixture files are normalized.
    for path in (root, *root.rglob("*")):
        assert not path.is_symlink()
        path.chmod(0o755 if path.is_dir() else 0o644)
    source = profile.Layout(product, scripts)
    pin = source.observe().sha256
    dependency_pin = bundle.m.source.files.inventory(dependency)

    def run(scope="complete", module=""):
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-S",
                "-c",
                BOOTSTRAP,
                str(root),
                scope,
                module,
                json.dumps(
                    [
                        str(repo / "scripts"),
                        str(repo / "src"),
                        str(Path(serial.__file__).parent.parent),
                    ]
                ),
                request.param,
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )

    yield root, run, profile
    assert source.verify(pin).sha256 == pin
    assert bundle.m.source.files.inventory(dependency) == dependency_pin
    assert not list(root.rglob("__pycache__"))


@pytest.mark.parametrize("scope", ["roots", "complete"])
def test_peer_imports_use_only_explicit_staged_product_helper_and_serial(peer_bundle, scope):
    _, run, profile = peer_bundle
    result = run(scope)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert not result.stderr
    assert json.loads(result.stdout) == {
        "scope": scope,
        "private_modules": len(profile.MODULES) - (3 if scope == "roots" else 0),
    }


@pytest.mark.parametrize(
    "relative",
    ["dependencies/serial", "src/sds200"]
    + [
        "scripts/" + name
        for name in sorted(bundle.m.PreparationProfile.HELPER_FILES - bundle.m.service.HELPER_FILES)
    ],
)
def test_missing_staged_peer_module_never_uses_visible_checkout_or_installed_copy(
    peer_bundle, relative
):
    root, run, profile = peer_bundle
    original, withheld = root / relative, root / "withheld"
    if relative.startswith("scripts/") and original.name not in profile.HELPER_FILES:
        assert not original.exists()
        result = run("one", original.stem)
        assert result.returncode == 1 and not result.stdout
        assert b"ModuleNotFoundError: Missing staged peer module:" in result.stderr
        return  # This smaller profile never admitted that module in the first place.
    original.rename(withheld)
    try:
        result = run()
        assert result.returncode == 1 and not result.stdout
        assert b"ModuleNotFoundError: Missing staged peer module:" in result.stderr
    finally:
        withheld.rename(original)


@pytest.mark.parametrize("replacement", ["module", "package"])
def test_staged_peer_import_cannot_follow_symlinks_to_checkout_or_installed_package(
    peer_bundle, replacement
):
    root, run, _ = peer_bundle
    if replacement == "module":
        name = "supplemental_recording_peer_connection.py"
        original = root / "scripts" / name
        outside = bundle.original.native.SCRIPTS / name
    else:
        original = root / "dependencies/serial"
        outside = Path(serial.__file__).parent
    withheld = root / "withheld"
    original.rename(withheld)
    try:
        original.symlink_to(outside, target_is_directory=outside.is_dir())
        result = run()
        assert result.returncode == 1 and not result.stdout
        assert b"ImportError: Staged peer source escaped" in result.stderr
    finally:
        original.unlink()
        withheld.rename(original)


@pytest.mark.parametrize(
    "module",
    [
        "supplemental_recording_service_command",
        "supplemental_recording_service_permission",
        "supplemental_recording_permission_probe",
    ],
)
def test_staged_profile_does_not_implicitly_expand_permission_command_selection(
    peer_bundle, module
):
    _, run, profile = peer_bundle
    result = run("one", module)
    if module in profile.MODULES:
        assert profile is bundle.m.PreparationProfile
        assert result.returncode == 0 and not result.stderr
        assert json.loads(result.stdout)["scope"] == "one"
    else:
        assert result.returncode == 1 and not result.stdout
        assert (
            "ModuleNotFoundError: Missing staged peer module: " + module
        ).encode() in result.stderr
