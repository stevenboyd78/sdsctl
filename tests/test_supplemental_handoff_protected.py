"""Fixed host-path collector routing; actual file safety tested separately."""

import importlib.util
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_files as file_tests
from . import test_supplemental_handoff_observer as observer_tests

NAME = "supplemental_handoff_protected"
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        NAME, Path(__file__).resolve().parents[1] / "scripts" / (NAME + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
f, p = sys.modules[NAME], observer_tests.p


@pytest.fixture
def layout():
    data = Path("/mnt/data/supervisor/apps/data") / p.NORMAL
    media = Path("/mnt/data/supervisor/media")
    return f.ProtectedLayout(
        p.NORMAL,
        Path("/mnt/data/supervisor/apps/local") / p.NORMAL.removeprefix("local_"),
        data,
        media,
        media / "normal/recordings",
        data / "deployment.toml",
        data / "configuration.toml",
        data / "accepted/accepted-profile.json",
        media / "normal/profile/profile.cfg",
        "a" * 64,
    )


@pytest.fixture
def disk(monkeypatch):
    calls = []

    def inventory(path, **kwargs):
        calls.append((path, kwargs))
        return {"fixture": {"sha256": p.checksum(str(path))}}

    def private(path):
        calls.append((path, {}))
        return SimpleNamespace(sha256=p.checksum(str(path)))

    monkeypatch.setattr(f, "inventory", inventory)
    monkeypatch.setattr(f, "private_file", private)
    return calls


def container(layout):
    return {
        "Id": "c" * 64,
        "Name": "/app_" + layout.slug,
        "GraphDriver": {
            "Name": "overlay2",
            "Data": {"ID": "c" * 64, "MergedDir": "/mnt/data/docker/overlay2/abc123/merged"},
        },
        "Mounts": [
            {"Destination": name, "Type": "bind", "Source": str(source), "RW": True}
            for name, source in (("/data", layout.data), ("/media", layout.media))
        ],
    }


def test_stopped_package_uses_separately_verified_immutable_image(layout, disk):
    result = f.collect(layout, None)
    assert result.package == layout.image_package_sha256
    assert len(disk) == 6
    assert disk[-1] == (layout.recordings, {"max_file_bytes": 16 * 1024 * 1024})
    assert {path for path, _ in disk} == {layout.context, layout.recordings, *layout.profile_paths}


def test_running_package_is_rehashed_from_verified_overlay(layout, disk):
    result = f.collect(layout, container(layout))
    assert result.package != layout.image_package_sha256
    assert disk[0] == (Path("/mnt/data/docker/overlay2/abc123/merged") / f.PACKAGE, {})


@pytest.mark.parametrize(
    "destination",
    [
        "/",
        "/usr",
        "/usr/local/lib",
        "/usr/local/lib/python3.14/site-packages/sds200/a.py",
        "/data/accepted",
        "/media/normal",
        "/data",
    ],
)
def test_shadowing_or_duplicate_mount_refused(layout, disk, destination):
    value = container(layout)
    value["Mounts"].append({"Destination": destination})
    with pytest.raises(p.UnsafeHandoff):
        f.collect(layout, value)
    assert disk == []


@pytest.mark.parametrize(
    "fault", ["source", "rw", "type", "missing", "id", "driver", "merged", "name"]
)
def test_wrong_or_unqualified_container_layout_refused(layout, disk, fault):
    value = deepcopy(container(layout))
    if fault in ("source", "rw", "type"):
        value["Mounts"][0][{"source": "Source", "rw": "RW", "type": "Type"}[fault]] = "wrong"
    elif fault == "missing":
        value["Mounts"].pop()
    elif fault == "id":
        value["GraphDriver"]["Data"]["ID"] = "d" * 64
    elif fault == "driver":
        value["GraphDriver"]["Name"] = "other"
    elif fault == "merged":
        value["GraphDriver"]["Data"]["MergedDir"] = "/mnt/data/docker/overlay2/../private"
    else:
        value["Name"] = "/other"
    with pytest.raises(p.UnsafeHandoff):
        f.collect(layout, value)
    assert disk == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("context", Path("/tmp")),
        ("data", Path("/tmp")),
        ("media", Path("/tmp")),
        ("source", Path("relative")),
        ("recordings", Path("/mnt/data/supervisor/media")),
        ("source", Path("/mnt/data/supervisor/media/normal/recordings/profile.cfg")),
    ],
)
def test_layout_cannot_expand_to_arbitrary_host_paths(layout, key, value):
    with pytest.raises(p.UnsafeHandoff):
        replace(layout, **{key: value})


def test_file_error_cannot_return_partial_proof(layout, disk, monkeypatch):
    def fail(_):
        raise file_tests.f.UnconfirmedFiles("unconfirmed")

    monkeypatch.setattr(f, "private_file", fail)
    with pytest.raises(file_tests.f.UnconfirmedFiles):
        f.collect(layout, None)


def test_candidate_fingerprint_includes_all_five_finite_wrappers(monkeypatch):
    calls = []

    def inventory(path):
        calls.append(path)
        if path.name == "sds200":
            return {"runtime.py": {"sha256": "a" * 64}}
        return {
            name: {"sha256": "b" * 64}
            for name in (
                "accept_supplemental_daemon.py",
                "guard_supplemental_acceptance.py",
                "accept_supplemental_web.py",
            )
        }

    def entry(path):
        calls.append(path)
        return file_tests.f.FileEvidence(10, "c" * 64, 0o555, 0, 0)

    monkeypatch.setattr(f, "inventory", inventory)
    monkeypatch.setattr(f, "executable_file", entry)
    digest = f.package_fingerprint(Path("/fixture"), candidate=True)
    assert len(digest) == 64 and len(calls) == 4
    assert calls[-2:] == [Path("/fixture/usr/local/bin") / name for name in f.ENTRIES]


def test_unexpected_launcher_file_or_bytecode_is_refused(monkeypatch):
    monkeypatch.setattr(f, "inventory", lambda _: {"unexpected.pyc": {}})
    with pytest.raises(p.UnsafeHandoff):
        f.package_fingerprint(Path("/fixture"), candidate=True)
