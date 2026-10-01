"""Recording-aware source routing; no actual /mnt/data or container operation."""

import importlib.util
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_protected as old
from . import test_supplemental_recording_source as closed

NAME = "supplemental_recording_static"
SPEC = importlib.util.spec_from_file_location(NAME, closed.SCRIPTS / (NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
f, p = old.f, old.p
disk = old.disk


@pytest.fixture
def layout():
    data = Path("/mnt/data/supervisor/apps/data") / p.CANDIDATE
    media = Path("/mnt/data/supervisor/media")
    return f.ProtectedLayout(
        p.CANDIDATE,
        Path("/mnt/data/supervisor/apps/local") / p.CANDIDATE.removeprefix("local_"),
        data,
        media,
        media / "candidate/recordings",
        data / "deployment.toml",
        data / "configuration.toml",
        data / "accepted/accepted-profile.json",
        media / "candidate/profile/profile.cfg",
        "a" * 64,
    )


@pytest.fixture
def package(monkeypatch):
    calls = []

    def observe(root):
        calls.append(root)
        return "b" * 64

    monkeypatch.setattr(m, "package_fingerprint", observe)
    return calls


def test_absent_candidate_uses_separately_qualified_image_without_dropping_profile(
    layout, disk, package
):
    result = m.collect(layout, None)
    assert type(result) is f.StaticFiles and result.package == layout.image_package_sha256
    assert {path for path, _ in disk} == {layout.context, *layout.profile_paths}
    assert len(disk) == 5 and not package
    # This is expressly static evidence, not the complete recording collector.
    assert not hasattr(result, "recordings")


def test_running_candidate_uses_new_complete_inventory_not_legacy_wrappers(
    layout, disk, package, monkeypatch
):
    def legacy_forbidden(*_, **__):
        pytest.fail("Recording evidence cannot downgrade to legacy wrapper hashes")

    monkeypatch.setattr(f, "package_fingerprint", legacy_forbidden)
    result = m.collect(layout, old.container(layout))
    assert result.package == "b" * 64 and result.package != layout.image_package_sha256
    assert package == [Path("/mnt/data/docker/overlay2/abc123/merged")]
    assert {path for path, _ in disk} == {layout.context, *layout.profile_paths}


@pytest.mark.parametrize(
    "destination",
    [
        "/",
        "/usr",
        "/usr/local/bin/python3.14",
        "/usr/local/lib/python3.14/site-packages/serial",
        "/usr/local/lib/python3.14/site-packages/sds200/theme.css",
        "/bin",
        "/lib",
        "/lib64",
        "/opt",
        "/opt/sdsctl-supplemental-recording",
        "/opt/sdsctl-supplemental-recording/accept_supplemental_recording_operator.py",
        "/opt/sdsctl-supplemental-acceptance",
        "/media/candidate",
        "/data/accepted",
        "/data",
        "/data/",
        "//data",
        "/tmp/./alias",
        "/tmp/../opt",
        "relative",
    ],
)
def test_unqualified_shadow_or_ambiguous_mount_precedes_every_read(
    layout, disk, package, destination
):
    value = old.container(layout)
    value["Mounts"].append({"Destination": destination})
    with pytest.raises(p.UnsafeHandoff):
        m.collect(layout, value)
    assert not disk and not package


@pytest.mark.parametrize(
    "fault", ["source", "rw", "type", "missing", "id", "driver", "merged", "name", "normal"]
)
def test_wrong_identity_or_mount_never_hashes_candidate(layout, disk, package, fault):
    value = deepcopy(old.container(layout))
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
    elif fault == "normal":
        layout = replace(
            layout,
            slug=p.NORMAL,
            context=Path("/mnt/data/supervisor/apps/local") / p.NORMAL.removeprefix("local_"),
            data=Path("/mnt/data/supervisor/apps/data") / p.NORMAL,
            deployment=layout.media / "candidate/deployment.toml",
            configuration=layout.media / "candidate/configuration.toml",
            accepted=layout.media / "candidate/accepted-profile.json",
        )
    else:
        value["Name"] = "/other"
    with pytest.raises(p.UnsafeHandoff):
        m.collect(layout, value)
    assert not disk and not package


@pytest.mark.parametrize("point", ["source", "profile", "context"])
def test_any_static_failure_produces_no_partial_evidence(layout, disk, package, monkeypatch, point):
    def fail(*_, **__):
        raise RuntimeError("fixture read failed")

    owner, key = {
        "source": (m, "package_fingerprint"),
        "profile": (f, "private_file"),
        "context": (f, "inventory"),
    }[point]
    monkeypatch.setattr(owner, key, fail)
    with pytest.raises(RuntimeError, match="fixture read failed"):
        m.collect(layout, old.container(layout))


def test_actual_fixed_root_bundle_is_read_only_and_distinct_from_old_wrappers(tmp_path):
    runtime, native = tmp_path / f.PACKAGE, tmp_path / m.NATIVE
    for root, names in (
        (runtime, closed.m.REQUIRED_RUNTIME | {"theme.css"}),
        (native, closed.m.NATIVE_FILES),
    ):
        root.mkdir(mode=0o755, parents=True)
        for name in names:
            path = root / name
            path.write_bytes(b"raise RuntimeError('must never import candidate')\n")
            path.chmod(0o644)
    original = m.package_fingerprint(tmp_path)
    assert original == closed.m.Layout(runtime, native).observe().sha256
    assert {
        "accept_supplemental_recording_operator.py",
        "supplemental_recording_wire.py",
    } <= closed.m.NATIVE_FILES
    asset = runtime / "theme.css"
    before = asset.stat()
    assert m.package_fingerprint(tmp_path) == original and asset.stat() == before
    asset.write_bytes(b"changed asset\n")
    assert m.package_fingerprint(tmp_path) != original
    # There is no fallback when any required new helper is missing.
    operator = native / "accept_supplemental_recording_operator.py"
    operator.unlink()
    with pytest.raises(closed.m.UnconfirmedSource):
        m.package_fingerprint(tmp_path)


@pytest.mark.parametrize("root", ["/private", Path("/"), Path("relative"), Path("/tmp/../private")])
def test_invalid_overlay_root_refuses_before_read(root, monkeypatch):
    def forbidden(*_):
        pytest.fail("Invalid source root must not be read")

    monkeypatch.setattr(closed.m.Layout, "observe", forbidden)
    with pytest.raises(p.UnsafeHandoff):
        m.package_fingerprint(root)


@pytest.mark.parametrize("identity", [None, "", "c" * 63, "C" * 64, True])
def test_missing_or_malformed_overlay_identity_precedes_reads(layout, disk, package, identity):
    value = old.container(layout)
    value["Id"] = value["GraphDriver"]["Data"]["ID"] = identity
    with pytest.raises(p.UnsafeHandoff):
        m.collect(layout, value)
    assert not disk and not package
