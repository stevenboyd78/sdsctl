"""Pure mount/manifest fixtures; no namespace operations or real /media access."""

import hashlib
import importlib.util
import sys
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass, replace
from pathlib import Path

import pytest

from . import test_supplemental_recording_host as host_tests

NAME = "supplemental_recording_projection"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(host_tests.r.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
p, f, c = host_tests.p, host_tests.f, host_tests.c
layout, tree, routing = host_tests.layout, host_tests.tree, host_tests.routing


@pytest.fixture
def projection(routing):
    candidate, collector, _ = routing
    return m.project(candidate, collector.stored)


def container(projection):
    value = host_tests.audio.ot.recovery_tests.container("app_" + p.CANDIDATE, 4)
    value.update(host_tests.fixed.container(projection.layout))
    generation = m.host.generation(value, name="app_" + p.CANDIDATE, image=value["Image"])
    return value, generation


def reencode(stored, **changes):
    baseline = changes.get("baseline", stored.baseline)
    writer = changes.get("writer", stored.writer)
    endpoint = changes.get("endpoint", stored.contract.audio_endpoint_sha256)
    maximum = changes.get("maximum", stored.contract.maximum_recording_seconds)
    return c._decode(
        c.manifest_bytes(baseline, writer, endpoint, maximum_recording_seconds=maximum)
    )


def test_projection_keeps_original_inventory_but_distinct_path_hashes(projection, monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("Projection must not capture files or access either namespace")

    monkeypatch.setattr(c.evidence, "capture_baseline", forbidden)
    monkeypatch.setattr(c.Collector, "pristine", forbidden)
    original = projection.host
    result = m.project(projection.layout, original)
    assert result.host is original
    assert result.native.baseline.root == Path("/media/candidate/recordings")
    assert result.host.baseline.files == result.native.baseline.files
    assert result.host.baseline.root_identity == result.native.baseline.root_identity
    assert result.host.contract.baseline_sha256 == result.native.contract.baseline_sha256
    assert result.host.contract.root_sha256 != result.native.contract.root_sha256
    assert result.host.contract.sha256 != result.native.contract.sha256
    assert result.host.manifest_sha256 != result.native.manifest_sha256
    assert hashlib.sha256(result.native_manifest).hexdigest() == result.native.manifest_sha256
    assert (
        result.check_native_manifest(result.native_manifest, pinned_projection=result.sha256)
        == result.native
    )
    assert result == projection


@pytest.mark.parametrize(
    "fault",
    ["case", "root", "identity", "files", "writer", "endpoint", "duration", "forged_digest"],
)
def test_native_manifest_must_represent_exact_same_original_data(projection, fault):
    native = projection.native
    baseline = native.baseline
    if fault == "case":
        native = reencode(
            native, baseline=replace(baseline, case="bb12345612344abc8abc123456789abc")
        )
    elif fault == "root":
        native = reencode(native, baseline=replace(baseline, root=Path("/media/other")))
    elif fault == "identity":
        identity = list(baseline.root_identity)
        identity[1] += 1
        native = reencode(native, baseline=replace(baseline, root_identity=tuple(identity)))
    elif fault == "files":
        native = reencode(native, baseline=replace(baseline, files=()))
    elif fault == "writer":
        native = reencode(native, writer=replace(native.writer, uid=native.writer.uid + 1))
    elif fault == "endpoint":
        native = reencode(native, endpoint="f" * 64)
    elif fault == "duration":
        native = reencode(native, maximum=179)
    else:
        native = replace(native, manifest_sha256="f" * 64)
    with pytest.raises((p.UnsafeHandoff, c.UnconfirmedProtection)):
        replace(projection, native=native)


@pytest.mark.parametrize("fault", ["wrong_pin", "host_manifest", "changed", "noncanonical", "text"])
def test_manifest_bytes_require_exact_projection_pin(projection, fault):
    raw, pin = projection.native_manifest, projection.sha256
    if fault == "wrong_pin":
        pin = "f" * 64
    elif fault == "host_manifest":
        raw = m._validated(projection.host)
    elif fault == "changed":
        raw = raw.replace(b"old.wav", b"new.wav")
    elif fault == "noncanonical":
        raw += b"\n"
    else:
        raw = raw.decode()
    with pytest.raises(p.UnsafeHandoff):
        projection.check_native_manifest(raw, pinned_projection=pin)


def test_running_container_requires_exact_generation_and_media_mapping(projection):
    value, generation = container(projection)
    assert projection.check_container(value, image=value["Image"], generation=generation) is None


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "read_only",
        "volume",
        "missing",
        "duplicate",
        "ancestor",
        "child",
        "nested_other",
        "relative",
        "traversal",
        "noncanonical",
        "control_character",
        "generation",
        "image",
        "pid",
        "exited",
        "too_many",
    ],
)
def test_unqualified_mount_or_incarnation_refuses_projection(projection, fault):
    value, generation = container(projection)
    image = value["Image"]
    media = value["Mounts"][1]
    if fault == "source":
        media["Source"] = "/mnt/data/supervisor/media/other"
    elif fault == "read_only":
        media["RW"] = False
    elif fault == "volume":
        media["Type"] = "volume"
    elif fault == "missing":
        value["Mounts"].pop()
    elif fault == "duplicate":
        value["Mounts"].append(dict(media))
    elif fault in (
        "ancestor",
        "child",
        "nested_other",
        "relative",
        "traversal",
        "noncanonical",
        "control_character",
    ):
        value["Mounts"].append(
            {
                "Destination": {
                    "ancestor": "/",
                    "child": "/media/candidate/recordings/subdir",
                    "nested_other": "/media/unrelated",
                    "relative": "media",
                    "traversal": "/media/../other",
                    "noncanonical": "/media/./other",
                    "control_character": "/other\npath",
                }[fault]
            }
        )
    elif fault == "generation":
        generation = "f" * 64
    elif fault == "image":
        image = "sha256:" + "f" * 64
    elif fault == "pid":
        value["State"]["Pid"] += 1
    elif fault == "exited":
        value["State"]["Status"] = "exited"
        value["State"]["Running"] = False
    else:
        value["Mounts"] += [{"Destination": f"/other/{i}"} for i in range(31)]
    with pytest.raises(p.UnsafeHandoff):
        projection.check_container(value, image=image, generation=generation)


def test_normal_layout_and_mismatched_baseline_not_projected(layout, projection):
    with pytest.raises(p.UnsafeHandoff):
        m.project(layout, projection.host)
    changed = replace(projection.layout, recordings=projection.layout.recordings / "other")
    with pytest.raises(p.UnsafeHandoff):
        m.project(changed, projection.host)


def test_projection_revalidates_dataclass_tampering(projection):
    object.__setattr__(projection.native, "manifest_sha256", "f" * 64)
    with pytest.raises(p.UnsafeHandoff):
        _ = projection.native_manifest
    with pytest.raises(p.UnsafeHandoff):
        _ = projection.sha256


def _members(value, path=()):
    if is_dataclass(value):
        for item in fields(value):
            nested = (*path, item.name)
            yield nested
            yield from _members(getattr(value, item.name), nested)
    elif type(value) is tuple:
        for index, item in enumerate(value):
            yield from _members(item, (*path, index))


def _member(value, path):
    for name in path:
        value = value[name] if type(name) is int else getattr(value, name)
    return value


@pytest.mark.parametrize("action", ["hash", "manifest", "container", "revalidate"])
@pytest.mark.parametrize("mutation", ["changed", "deleted"])
def test_every_nested_field_stays_pinned_even_after_frozen_record_bypass(
    projection, action, mutation
):
    value, generation = container(projection)
    checked = 0
    for path in _members(projection):
        changed = deepcopy(projection)
        parent = _member(changed, path[:-1])
        if mutation == "changed":
            object.__setattr__(parent, path[-1], None)
        else:
            object.__delattr__(parent, path[-1])
        with pytest.raises(p.UnsafeHandoff):
            if action == "hash":
                _ = changed.sha256
            elif action == "manifest":
                _ = changed.native_manifest
            elif action == "container":
                changed.check_container(value, image=value["Image"], generation=generation)
            else:
                changed.__post_init__()
        checked += 1
    assert checked > 40


def test_plan_payload_and_canonical_manifest_schema_do_not_gain_private_state(projection):
    assert set(asdict(projection)) == {"layout", "host", "native"}
    expected = m.checksum(
        {
            "kind": "finite-recording-media-projection-v1",
            "slug": projection.layout.slug,
            "host_media": str(m.HOST_MEDIA),
            "native_media": str(m.NATIVE_MEDIA),
            "host_root": str(projection.host.baseline.root),
            "native_root": str(projection.native.baseline.root),
            "host_manifest": projection.host.manifest_sha256,
            "native_manifest": projection.native.manifest_sha256,
            "host_contract": projection.host.contract.sha256,
            "native_contract": projection.native.contract.sha256,
        }
    )
    assert projection.sha256 == expected
    assert c._decode(projection.native_manifest) == projection.native


def test_unchanged_rechecks_do_not_redecode_but_still_validate_new_instances(
    projection, monkeypatch
):
    expected = projection.sha256
    validated, original = [], m._validated

    def tracked(value):
        validated.append(value)
        return original(value)

    monkeypatch.setattr(m, "_validated", tracked)
    value, generation = container(projection)
    for _ in range(3):
        assert projection.sha256 == expected
        projection.check_container(value, image=value["Image"], generation=generation)
    assert validated == []
    copy = replace(projection)
    assert copy is not projection and copy == projection and copy.sha256 == expected
    assert validated == [projection.host, projection.native]
    assert copy.native_manifest == projection.native_manifest
    assert validated == [projection.host, projection.native, copy.native, projection.native]


@pytest.mark.parametrize("field", ["HOST_MEDIA", "NATIVE_MEDIA"])
def test_alias_constants_remain_part_of_the_original_projection(projection, monkeypatch, field):
    monkeypatch.setattr(m, field, Path("/different"))
    with pytest.raises(p.UnsafeHandoff):
        _ = projection.sha256


@pytest.mark.parametrize("fault", ["root_identity", "writer", "duration", "entries", "entry_mode"])
def test_equal_but_differently_typed_nested_data_is_not_adopted(projection, fault):
    if fault == "root_identity":
        obj, field = projection.native.baseline, "root_identity"
        value = tuple(float(item) for item in obj.root_identity)
    elif fault == "writer":
        obj, field = projection.native.writer, "wav_mode"
        value = float(obj.wav_mode)
    elif fault == "duration":
        obj, field = projection.native.contract, "maximum_recording_seconds"
        value = float(obj.maximum_recording_seconds)
    elif fault == "entries":
        obj, field = projection.native.baseline, "files"
        value = list(obj.files)
    else:
        obj, field = projection.native.baseline.files[0][1], "mode"
        value = float(obj.mode)
    object.__setattr__(obj, field, value)
    with pytest.raises(p.UnsafeHandoff):
        _ = projection.sha256


def test_consistently_reencoded_replacement_cannot_reseal_existing_projection(projection):
    replacement = replace(
        projection,
        host=reencode(projection.host, endpoint="e" * 64),
        native=reencode(projection.native, endpoint="e" * 64),
    )
    assert replacement.sha256 != projection.sha256
    object.__setattr__(projection, "host", replacement.host)
    object.__setattr__(projection, "native", replacement.native)
    with pytest.raises(p.UnsafeHandoff):
        projection.__post_init__()
    with pytest.raises(p.UnsafeHandoff):
        _ = projection.sha256


@pytest.mark.parametrize("fault", ["name", "identity", "extra", "missing", "name_subclass"])
def test_tuple_members_and_cardinality_stay_pinned(projection, fault):
    class EqualString(str):
        def __eq__(self, other):
            pytest.fail("Untrusted scalar equality must never run")

    baseline = projection.native.baseline
    if fault == "identity":
        old = baseline.root_identity
        object.__setattr__(baseline, "root_identity", (old[0] + 1, *old[1:]))
    else:
        entries = baseline.files
        if fault == "extra":
            # Length mismatch refuses before examining any appended value.
            entries = (*entries, object())
        elif fault == "missing":
            entries = entries[:-1]
        else:
            name = "different.wav" if fault == "name" else EqualString(entries[0][0])
            entries = ((name, entries[0][1]), *entries[1:])
        object.__setattr__(baseline, "files", entries)
    with pytest.raises(p.UnsafeHandoff):
        _ = projection.sha256
