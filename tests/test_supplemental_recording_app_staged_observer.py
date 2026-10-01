"""Owned observer lifetime from the closed staged source/dependency bundle.

Staging selects trusted checkout bytes, not candidate-supplied executable code.
The interpreter, App/Engine/publication facts and test bootstrap are still local
fixtures, NOT installed runtime qualification or an active entrypoint. No live
scanner, Home Assistant or Docker access is used.
"""

import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import serial

from . import test_supplemental_recording_app_actual_observer as observed
from . import test_supplemental_recording_service_host_source as bundle

bwrap, staged, mapped = observed.bwrap, observed.staged, observed.mapped
layout, image_umask, supervised = observed.layout, observed.image_umask, observed.supervised
image, configured, candidate = observed.image, observed.configured, observed.candidate
app, native, launch_case = observed.app, observed.native, observed.launch_case
driver_case, dispatched = observed.driver_case, observed.dispatched
pytestmark = observed.pytestmark

# This disposable bootstrap is not part of the closed production source graph.
# It removes installed package search paths, rejects undeclared third-party
# imports, and checks every imported private/product/dependency module's origin.
BOOTSTRAP = r"""
import importlib.abc, importlib.machinery, runpy, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path[:] = [str(root / "dependencies")] + [
    p for p in sys.path if not {"site-packages", "dist-packages"} & set(Path(p).parts)
]
prefixes = ("supplemental_", "qualify_supplemental_", "accept_supplemental_")
class ReviewedImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.partition(".")[0]
        if top in sys.stdlib_module_names:
            return None
        if top.startswith(prefixes):
            base = root / "scripts"
        elif top in ("sds200", "serial"):
            base = root / ("src" if top == "sds200" else "dependencies")
        elif top == "_native_observer_process_fixture":
            base = root / "tests"
        else:
            raise ImportError("Undeclared staged observer dependency")
        search = [str(base)] if path is None else list(path)
        assert all(Path(p).is_relative_to(base) for p in search)
        spec = importlib.machinery.PathFinder.find_spec(fullname, search)
        if spec is None:
            raise ModuleNotFoundError("No module named '" + fullname + "'")
        assert spec.origin is not None and Path(spec.origin).is_relative_to(base)
        return spec  # Never delegate a missing staged module to an editable finder.
sys.meta_path.insert(0, ReviewedImports())
runpy.run_path(str(root / "tests/_app_service_observer_fixture.py"), run_name="__main__")
private = set()
for name, module in tuple(sys.modules.items()):
    top = name.partition(".")[0]
    if name.startswith(prefixes):
        assert Path(module.__file__) == root / "scripts" / (name + ".py")
        private.add(name)
    elif top in ("sds200", "serial"):
        expected = root / ("src" if top == "sds200" else "dependencies") / top
        assert Path(module.__file__).is_relative_to(expected)
assert "supplemental_recording_service_cli_channel" in private
assert "serial" in sys.modules
assert all(root.joinpath("scripts", name + ".py").is_file() for name in private)
"""


@pytest.fixture
def observer_bundle(tmp_path):
    root = tmp_path / "observer-bundle"
    repo = Path(__file__).resolve().parents[1]
    scripts, product = root / "scripts", root / "src/sds200"
    scripts.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(repo / "src/sds200", product, ignore=ignore)
    for name in bundle.m.HELPER_FILES:
        shutil.copyfile(repo / "scripts" / name, scripts / name)
    dependency = root / "dependencies/serial"
    shutil.copytree(Path(serial.__file__).parent, dependency, ignore=ignore)
    helpers = root / "tests"
    helpers.mkdir()
    for name in ("_app_service_observer_fixture.py", "_native_observer_process_fixture.py"):
        shutil.copyfile(repo / "tests" / name, helpers / name)
    bootstrap = helpers / "staged_observer.py"
    bootstrap.write_text(BOOTSTRAP)
    # Only newly copied disposable fixture paths are normalized; observed user
    # sources are never chmodded or repaired to obtain an inventory match.
    for path in [root, *root.rglob("*")]:
        assert not path.is_symlink()
        path.chmod(0o755 if path.is_dir() else 0o644)
    source = bundle.m.Layout(product, scripts)
    pin = source.observe().sha256
    files = bundle.m.source.files
    dependency_pin = files.inventory(dependency)
    helpers_pin = files.inventory(helpers)

    def unchanged():
        assert source.verify(pin).sha256 == pin
        assert files.inventory(dependency) == dependency_pin
        assert files.inventory(helpers) == helpers_pin

    yield root, bootstrap, unchanged
    unchanged()
    assert not list(root.rglob("__pycache__"))


@pytest.mark.parametrize("route", ["finalized", "abandoned", "pristine"])
def test_staged_original_observer_has_no_checkout_or_installed_dependency_fallback(
    dispatched, mapped, staged, monkeypatch, observer_bundle, route
):
    from . import test_supplemental_recording_app_actual_pristine as closed

    root, bootstrap, unchanged = observer_bundle
    original = observed.subprocess.Popen
    original_script = str(Path(observed.__file__).with_name("_app_service_observer_fixture.py"))
    launches = []

    def launch(argv, *args, **kwargs):
        if type(argv) is list and len(argv) == 6 and argv[3] == original_script:
            assert argv[1:3] == ["-I", "-B"] and not launches
            unchanged()
            argv = [*argv[:3], str(bootstrap), *argv[4:]]
            launches.append(tuple(argv))
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(observed.subprocess, "Popen", launch)

    @contextmanager
    def staged_observer(s, io, patch):
        with observed.separate_observer(s, io, patch) as observer:
            yield observer
        unchanged()

    if route == "finalized":
        observed.recovery.run_recovery(
            dispatched,
            mapped,
            staged,
            monkeypatch,
            initial=True,
            observer_factory=staged_observer,
        )
    else:
        closed.run_closed(
            dispatched,
            mapped,
            staged,
            monkeypatch,
            preserved=route == "abandoned",
            observer_factory=staged_observer,
        )
    assert len(launches) == 1
    assert Path(launches[0][3]).is_relative_to(root)


@pytest.mark.parametrize(
    ("module", "relative"),
    [
        ("serial", "dependencies/serial"),
        ("sds200", "src/sds200"),
        (
            "supplemental_recording_service_cli_channel",
            "scripts/supplemental_recording_service_cli_channel.py",
        ),
    ],
)
def test_missing_staged_module_cannot_fall_back_to_installed_package_or_checkout(
    observer_bundle, candidate, module, relative
):
    # candidate is the module-wide explicit fixture policy, not a running App.
    # Only a newly copied fixture module/package is moved and then put back.
    # The interpreter has installed packages; isolation must still refuse them.
    root, bootstrap, unchanged = observer_bundle
    assert not Path(serial.__file__).is_relative_to(root)
    dependency, withheld = root / relative, root / "withheld-module"
    dependency.rename(withheld)
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(bootstrap), "0", "1"],
            input=b"",
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 1 and not result.stdout
        assert result.stderr.endswith(
            ("ModuleNotFoundError: No module named '" + module + "'\n").encode()
        )
    finally:
        withheld.rename(dependency)
    unchanged()
