"""Pure plan/retained-manifest joins; no App, filesystem capture or launch."""

import importlib.util
import sys
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from . import test_supplemental_handoff_service as legacy
from . import test_supplemental_recording_bootstrap as bootstrap_tests  # noqa: F401
from . import test_supplemental_recording_clock as clock_tests  # noqa: F401
from . import test_supplemental_recording_idle as idle
from . import test_supplemental_recording_projection as manifests

NAME = "supplemental_recording_host_plan"
SPEC = importlib.util.spec_from_file_location(NAME, Path(legacy.s.__file__).with_name(NAME + ".py"))
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)


def value():
    old = legacy.plan_value()
    normal = next(seal for seal in old["seals"] if seal["slug"] == m.base.NORMAL)
    previous = next(seal for seal in old["seals"] if seal["slug"] == m.base.CANDIDATE)
    candidate = dict(
        version=previous["version"],
        image=previous["image"],
        settings=previous["settings"],
        files={key: previous["files"][key] for key in ("context", "package", "profile")},
        contract=asdict(m.Contract(old["case"], "1" * 64, "2" * 64, "3" * 64, "4" * 64)),
    )
    selected = {
        key: old[key]
        for key in (
            "case",
            "boot",
            "source",
            "firmware",
            "cli_image",
            "cli_generation",
            "core_image",
            "core_generation",
            "core_version",
            "normal_generation",
            "layouts",
            "installed_versions",
            "other_scanner_apps",
        )
    }
    return selected | dict(
        schema=3,
        kind=m.KIND,
        normal=normal,
        candidate=candidate,
        helper=dict(
            image=old["helper_image"], source="b" * 64, interpreter="c" * 64, environment="d" * 64
        ),
        candidate_runtime=dict(
            image=candidate["image"],
            source=candidate["files"]["package"],
            interpreter="e" * 64,
            environment="f" * 64,
        ),
        original_clock=dict(
            boot=old["boot"],
            namespace=[1, 402],
            before_ns=10 * m.clock.NS,
            boottime_ns=30 * m.clock.NS,
            after_ns=10 * m.clock.NS + 1,
        ),
        deadlines=dict(issued_at=30, ready_by=330, stop_by=540, recover_by=1530),
        projection_sha256="5" * 64,
        native_baseline_sha256="6" * 64,
        network=m.ordinary.AUDIO_NETWORK,
    )


def denied(callback):
    with pytest.raises(m.UnconfirmedPlan) as caught:
        callback()
    assert str(caught.value) == m.MESSAGE and caught.value.__suppress_context__


def test_closed_plan_preserves_distinct_seals_and_all_normal_files():
    supplied = value()
    plan = m.decode(supplied)
    assert plan.raw == m.base.encode(supplied)
    assert plan.sha256 == m.base.checksum(supplied)
    assert m.load_bytes(plan.raw, plan.sha256) == plan
    assert type(plan.normal) is m.ordinary.AppSeal
    assert type(plan.normal.files) is m.ordinary.ProtectedFiles
    assert plan.normal.files.recordings == supplied["normal"]["files"]["recordings"]
    assert type(plan.candidate) is m.host.CandidateSeal
    assert type(plan.candidate.files) is m.fixed.StaticFiles
    assert plan.candidate.contract.case_id == plan.case
    assert plan.root == Path("/mnt/data/sdsctl-recording-handoff-" + plan.case)
    assert plan.native_root == Path("/data/sdsctl-recording-" + plan.case)
    assert plan.installed_versions == tuple(sorted(supplied["installed_versions"].items()))
    assert not hasattr(plan, "candidate_generation") and not hasattr(plan, "launch_plan_sha256")


def test_input_mutation_cannot_change_plan_or_original_deadlines():
    supplied = value()
    plan = m.decode(supplied)
    original = (plan.raw, plan.sha256, plan.lease, plan.bootstrap, plan.idle_argv)
    supplied["deadlines"]["stop_by"] += 200
    supplied["layouts"][0]["recordings"] = "/PRIVATE"
    supplied["normal"]["files"]["recordings"] = "0" * 64
    supplied["helper"]["source"] = "0" * 64
    supplied["other_scanner_apps"].clear()
    supplied["installed_versions"].clear()
    assert (plan.raw, plan.sha256, plan.lease, plan.bootstrap, plan.idle_argv) == original


@pytest.mark.parametrize(
    "field", ["projection_sha256", "native_baseline_sha256", "normal_generation", "source"]
)
def test_modified_fields_cannot_keep_original_plan_bytes_or_digest(field):
    plan = m.decode(value())
    changed = "0" * (40 if field == "source" else 64)
    denied(lambda: replace(plan, **{field: changed}))


def test_seal_change_cannot_keep_old_plan_digest():
    plan = m.decode(value())
    denied(lambda: replace(plan, helper=replace(plan.helper, source="0" * 64)))
    denied(lambda: replace(plan, other_scanner_apps=()))


@pytest.mark.parametrize("field", ["layouts", "other_scanner_apps", "namespace"])
def test_python_container_types_are_not_silently_json_coerced(field):
    supplied = value()
    if field == "namespace":
        supplied["original_clock"][field] = tuple(supplied["original_clock"][field])
    else:
        supplied[field] = tuple(supplied[field])
    denied(lambda: m.decode(supplied))


def test_conservative_original_lease_matches_real_idle_decoder():
    plan = m.decode(value())
    native = plan.lease
    assert native["clock"] == "CLOCK_MONOTONIC"
    assert native["ready_by"] < plan.deadlines.ready_by - 20
    assert native["stop_by"] < plan.deadlines.stop_by - 20
    assert (
        idle.m.decode(
            m.base.encode(native),
            sha256=plan.lease_sha256,
            path=plan.native_root / "idle/lease.json",
            now=11,
            boot=plan.boot,
        )
        == native
    )
    assert plan.bootstrap == m.bootstrap.Bootstrap(
        plan.sha256,
        plan.candidate_runtime.source,
        plan.lease_sha256,
        plan.deadlines.ready_by,
        plan.deadlines.stop_by,
    )
    assert plan.idle_argv == (
        "/usr/local/bin/python",
        "-I",
        "-B",
        "/opt/sdsctl-supplemental-recording/accept_supplemental_recording_idle.py",
        "--lease",
        str(plan.native_root / "idle/lease.json"),
        "--lease-sha256",
        plan.lease_sha256,
    )


@pytest.mark.parametrize("schema", [1, 2, True, 4, "3", None])
def test_cannot_reinterpret_old_schemas(schema):
    denied(lambda: m.decode(value() | {"schema": schema}))
    old = legacy.plan_value()
    denied(lambda: m.decode(old))
    with pytest.raises(m.base.UnsafeHandoff):
        legacy.s.decode_plan(value())


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("kind", "PRIVATE"),
        ("case", "../case"),
        ("boot", "unknown"),
        ("source", "main"),
        ("firmware", "PRIVATE\n"),
        ("core_version", ""),
        ("cli_image", "image:latest"),
        ("core_image", "sha256:bad"),
        ("cli_generation", None),
        ("core_generation", True),
        ("normal_generation", "bad"),
        ("projection_sha256", "bad"),
        ("native_baseline_sha256", None),
        ("layouts", []),
        ("installed_versions", {}),
        ("other_scanner_apps", []),
        ("network", m.ordinary.READER_NETWORK),
        ("helper", {}),
        ("candidate_runtime", {}),
        ("normal", {}),
        ("candidate", {}),
        ("deadlines", {}),
        ("original_clock", {}),
    ],
)
def test_missing_or_unqualified_inputs_refused(field, replacement):
    denied(lambda: m.decode(value() | {field: replacement}))
    missing = value()
    del missing[field]
    denied(lambda: m.decode(missing))


@pytest.mark.parametrize(
    "extra",
    [
        "command",
        "env",
        "candidate_generation",
        "launch_plan_sha256",
        "allow_recording",
        "allow_restart",
    ],
)
def test_no_caller_command_or_guessed_future_identity(extra):
    denied(lambda: m.decode(value() | {extra: "PRIVATE"}))


@pytest.mark.parametrize("role", ["helper", "candidate_runtime"])
@pytest.mark.parametrize("field", ["image", "source", "interpreter", "environment"])
def test_runtime_bindings_cannot_be_omitted_or_coerced(role, field):
    supplied = value()
    supplied[role][field] = True
    denied(lambda: m.decode(supplied))
    supplied = value()
    del supplied[role][field]
    denied(lambda: m.decode(supplied))


@pytest.mark.parametrize(
    "fault",
    [
        "candidate_image",
        "candidate_source",
        "candidate_recordings_field",
        "normal_missing_recordings",
        "wrong_case",
        "wrong_normal_slug",
        "duplicate_layout",
        "wrong_package",
        "version",
        "shared_profile",
        "shared_recordings",
        "other_recordings_profile",
        "relative_path",
        "double_slash",
        "newline_path",
        "duplicate_owner",
        "missing_other_app",
        "normal_as_other",
    ],
)
def test_cross_bindings_and_unchanged_normal_file_protection(fault):
    v = value()
    normal, candidate = v["layouts"]
    assert normal["slug"] == m.base.NORMAL and candidate["slug"] == m.base.CANDIDATE
    if fault == "candidate_image":
        v["candidate_runtime"]["image"] = "sha256:" + "8" * 64
    elif fault == "candidate_source":
        v["candidate_runtime"]["source"] = "8" * 64
    elif fault == "candidate_recordings_field":
        v["candidate"]["files"]["recordings"] = "8" * 64
    elif fault == "normal_missing_recordings":
        del v["normal"]["files"]["recordings"]
    elif fault == "wrong_case":
        v["candidate"]["contract"]["case_id"] = "bb12345612344abc8abc123456789abc"
    elif fault == "wrong_normal_slug":
        v["normal"]["slug"] = m.base.CANDIDATE
    elif fault == "duplicate_layout":
        v["layouts"][1] = deepcopy(normal)
    elif fault == "wrong_package":
        candidate["image_package_sha256"] = "8" * 64
    elif fault == "version":
        v["installed_versions"][m.base.NORMAL] = "wrong"
    elif fault == "shared_profile":
        candidate["source"] = normal["source"]
    elif fault == "shared_recordings":
        candidate["recordings"] = normal["recordings"] + "/child"
    elif fault == "other_recordings_profile":
        candidate["source"] = normal["recordings"] + "/profile.cfg"
    elif fault == "relative_path":
        candidate["source"] = "relative/profile.cfg"
    elif fault == "double_slash":
        candidate["source"] = "/" + candidate["source"]
    elif fault == "newline_path":
        candidate["source"] += "\n"
    elif fault == "duplicate_owner":
        v["other_scanner_apps"] *= 2
    elif fault == "missing_other_app":
        del v["installed_versions"][v["other_scanner_apps"][0]]
    else:
        v["other_scanner_apps"].append(m.base.NORMAL)
    denied(lambda: m.decode(v))


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("issued_at", 31),
        ("issued_at", True),
        ("ready_by", 30),
        ("ready_by", 631),
        ("ready_by", float("inf")),
        ("stop_by", 512),
        ("stop_by", 811),
        ("recover_by", 1531),
        ("recover_by", 1529),
    ],
)
def test_original_deadlines_cannot_shift_or_shrink_recovery_budget(field, replacement):
    v = value()
    v["deadlines"][field] = replacement
    denied(lambda: m.decode(v))


@pytest.mark.parametrize(
    "fault", ["boot", "namespace", "skew", "reverse", "sample_time", "boolean"]
)
def test_original_clock_domain_is_part_of_plan(fault):
    v = value()
    c = v["original_clock"]
    if fault == "boot":
        c["boot"] = "a" * 32
    elif fault == "namespace":
        c["namespace"] = [1, 0]
    elif fault == "skew":
        c["after_ns"] += 10_000_000
    elif fault == "reverse":
        c["before_ns"] = c["after_ns"] + 1
    elif fault == "sample_time":
        c["boottime_ns"] += 1_000_000
    else:
        c["namespace"][1] = True
    denied(lambda: m.decode(v))


def test_later_clock_check_does_not_renew_any_deadline():
    plan = m.decode(value())
    later = m.clock.Window(
        plan.boot, (1, 402), 11 * m.clock.NS, 31 * m.clock.NS, 11 * m.clock.NS + 1
    )
    before = (plan.bootstrap, plan.lease, plan.deadlines)
    assert plan.check_clock(later) is None
    assert before == (plan.bootstrap, plan.lease, plan.deadlines)
    denied(lambda: plan.check_clock(replace(later, namespace=(1, 403))))
    denied(lambda: plan.check_clock(replace(later, boottime_ns=32 * m.clock.NS)))
    denied(
        lambda: plan.check_clock(
            m.clock.Window(
                plan.boot, (1, 402), 1600 * m.clock.NS, 1620 * m.clock.NS, 1600 * m.clock.NS + 1
            )
        )
    )


@pytest.mark.parametrize("fault", ["pin", "whitespace", "duplicate", "nan", "oversize", "type"])
def test_bytes_require_bounded_canonical_unique_data(fault):
    raw = m.base.encode(value())
    if fault == "whitespace":
        raw += b"\n"
    elif fault == "duplicate":
        raw = b'{"schema":3,' + raw[1:]
    elif fault == "nan":
        raw = raw.replace(b'"issued_at":30', b'"issued_at":NaN')
    elif fault == "oversize":
        raw = b" " * (m.MAX_BYTES + 1)
    sha = m.hashlib.sha256(raw).hexdigest()
    if fault == "pin":
        sha = "0" * 64
    elif fault == "type":
        raw = bytearray(raw)
    denied(lambda: m.load_bytes(raw, sha))


layout, tree, routing, projection = (
    manifests.layout,
    manifests.tree,
    manifests.routing,
    manifests.projection,
)


def test_plan_checks_original_host_native_projection_not_a_current_recapture(
    projection, monkeypatch
):
    v = value()
    v["case"] = projection.host.baseline.case
    v["candidate"]["contract"] = asdict(projection.host.contract)
    v["layouts"][1] = {
        key: str(item) if isinstance(item, Path) else item
        for key, item in asdict(projection.layout).items()
    }
    v["candidate"]["files"]["package"] = projection.layout.image_package_sha256
    v["candidate_runtime"]["source"] = projection.layout.image_package_sha256
    v["projection_sha256"] = projection.sha256
    v["native_baseline_sha256"] = projection.native.manifest_sha256
    plan = m.decode(v)
    monkeypatch.setattr(
        m.projection.recording.evidence, "capture_baseline", lambda *_: pytest.fail("recapture")
    )
    assert plan.check_projection(projection) is None
    for field in ("projection_sha256", "native_baseline_sha256"):
        wrong = m.decode(v | {field: "0" * 64})
        denied(lambda wrong=wrong: wrong.check_projection(projection))
    denied(lambda: plan.check_projection(None))
