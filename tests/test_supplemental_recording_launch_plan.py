"""Read-only launch preflight on actual private baseline and accepted profile."""

import hashlib
import importlib.util
import socket
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import test_supplemental_handoff_cached as cached_tests
from . import test_supplemental_recording_construction as construction_tests

NAME = "supplemental_recording_launch_plan"
SPEC = importlib.util.spec_from_file_location(
    NAME, Path(construction_tests.c.__file__).with_name(NAME + ".py")
)
m = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = m
SPEC.loader.exec_module(m)
p = m.protected
tree = construction_tests.tree
configured, cached = cached_tests.configured, cached_tests.cached


@pytest.fixture
def prepared(tree, cached, tmp_path):
    deployment, config, _, _, _ = cached
    root = tmp_path / "launch-input"
    root.mkdir(mode=0o700)
    sockets, receipts, baseline = (tmp_path / name for name in ("sockets", "receipts", "baseline"))
    for directory in (sockets, receipts, baseline):
        directory.mkdir(mode=0o700)
    host = "192.0.2.25"
    specification = m.construction.Specification(
        host, 50536, 554, "127.0.0.1", 50000, sockets, receipts, "Version 1.26.01", 64, 60, 600
    )
    endpoint = m.construction.NetworkAudioTransport(host).endpoint
    stored = p.save_baseline(
        baseline, tree.baseline, tree.writer, hashlib.sha256(endpoint.encode()).hexdigest()
    )
    profile, _ = m.cached.profile_files(deployment, tree.root)
    fields = asdict(specification)
    for key in ("sockets", "receipts"):
        fields[key] = str(fields[key])
    value = {
        "schema": 1,
        "kind": "finite-recording-native-launch-v1",
        "specification": fields,
        "baseline": {
            "directory": str(baseline),
            "sha256": stored.manifest_sha256,
            "contract": asdict(stored.contract),
        },
        "profile": {"deployment": str(deployment), "sha256": profile},
        "generation": "a" * 64,
        "source_sha256": "b" * 64,
        "projection_sha256": "c" * 64,
        "host_plan_sha256": "d" * 64,
    }
    path = root / "launch.json"
    path.write_bytes(p.encode(value))
    path.chmod(0o600)
    return SimpleNamespace(
        path=path, value=value, stored=stored, config=config, tree=tree, spec=specification
    )


def load(prepared, **changes):
    return m.load(
        prepared.path,
        **(
            {
                "expected_sha256": hashlib.sha256(p.encode(prepared.value)).hexdigest(),
                "expected_source_sha256": prepared.value["source_sha256"],
            }
            | changes
        ),
    )


def refused(action):
    with pytest.raises(m.UnconfirmedLaunchPlan) as caught:
        action()
    assert str(caught.value) == m.MESSAGE and "PRIVATE" not in str(caught.value)


def test_plan_is_exact_read_only_preflight_not_an_ordinary_cli(prepared, monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("Preflight must not create network/runtime objects or resolve DNS")

    for name in ("socket", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(m.construction, "construct", forbidden)
    monkeypatch.setattr(m.construction, "NetworkAudioTransport", forbidden)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/PRIVATE_unrelated")
    monkeypatch.setenv("SDS200_HOST", "unrelated.example.test")
    result = load(prepared)
    assert result.specification == prepared.spec
    assert result.stored == prepared.stored and result.configuration == prepared.config
    assert result.generation == "a" * 64 and result.source_sha256 == "b" * 64
    assert not hasattr(result, "start") and not hasattr(result, "daemon_args")
    assert list(prepared.spec.sockets.iterdir()) == list(prepared.spec.receipts.iterdir()) == []
    assert p.Collector(prepared.stored).pristine().files.stage == "pristine"


@pytest.mark.parametrize(
    "field",
    ["destination", "reloader", "mqtt", "remote", "daemon_args", "recording_start", "environment"],
)
def test_extra_top_level_runtime_inputs_are_never_ignored(prepared, field):
    prepared.value[field] = {}
    prepared.path.write_bytes(p.encode(prepared.value))
    refused(lambda: load(prepared))


@pytest.mark.parametrize(
    "fault",
    [
        "digest",
        "source",
        "schema_bool",
        "legacy_kind",
        "noncanonical",
        "duplicate",
        "empty",
        "oversize",
        "file_mode",
        "directory_mode",
        "symlink",
        "hardlink",
        "wrong_basename",
        "spec_extra",
        "spec_missing",
        "generation",
        "projection",
        "host_plan",
        "baseline_hash",
        "contract",
        "profile_hash",
        "wrong_target",
        "occupied_sockets",
        "occupied_receipts",
        "input_overlap",
        "output_overlap",
        "changed_old",
        "new_recording",
    ],
)
def test_unqualified_launch_inputs_refuse_without_output(prepared, fault):
    options = {}
    serialized = True
    if fault in ("digest", "source"):
        options["expected_sha256" if fault == "digest" else "expected_source_sha256"] = "f" * 64
    elif fault == "schema_bool":
        prepared.value["schema"] = True
    elif fault == "legacy_kind":
        prepared.value["kind"] = "explicit-demand-clock-favorites-v1"
    elif fault in ("noncanonical", "duplicate", "empty", "oversize"):
        raw = p.encode(prepared.value)
        prepared.path.write_bytes(
            {
                "noncanonical": raw + b"\n",
                "duplicate": raw[:-1] + b',"schema":1}',
                "empty": b"",
                "oversize": b"x" * (m.MAX_BYTES + 1),
            }[fault]
        )
        serialized = False
    elif fault == "file_mode":
        prepared.path.chmod(0o644)
    elif fault == "directory_mode":
        prepared.path.parent.chmod(0o755)
    elif fault == "symlink":
        target = prepared.path.with_name("original")
        prepared.path.rename(target)
        prepared.path.symlink_to(target)
        serialized = False
    elif fault == "hardlink":
        prepared.path.with_name("alias").hardlink_to(prepared.path)
    elif fault == "wrong_basename":
        target = prepared.path.with_name("other.json")
        prepared.path.rename(target)
        prepared.path = target
    elif fault == "spec_extra":
        prepared.value["specification"]["mqtt"] = {}
    elif fault == "spec_missing":
        del prepared.value["specification"]["host"]
    elif fault in ("generation", "projection", "host_plan"):
        prepared.value["generation" if fault == "generation" else fault + "_sha256"] = "invalid"
    elif fault == "baseline_hash":
        prepared.value["baseline"]["sha256"] = "f" * 64
    elif fault == "contract":
        prepared.value["baseline"]["contract"]["audio_endpoint_sha256"] = "f" * 64
    elif fault == "profile_hash":
        prepared.value["profile"]["sha256"] = "f" * 64
    elif fault == "wrong_target":
        prepared.value["specification"]["host"] = "192.0.2.26"
    elif fault == "occupied_sockets":
        (prepared.spec.sockets / "unrelated").touch()
    elif fault == "occupied_receipts":
        (prepared.spec.receipts / "started.json").touch()
    elif fault == "input_overlap":
        prepared.value["specification"]["sockets"] = str(prepared.path.parent)
    elif fault == "output_overlap":
        prepared.value["specification"]["sockets"] = str(prepared.spec.receipts)
    elif fault == "changed_old":
        (prepared.tree.root / "old.json").write_bytes(b"changed")
    else:
        prepared.tree.wav.write_bytes(b"unconfirmed output")
    if serialized:
        prepared.path.write_bytes(p.encode(prepared.value))
    # Malformed serialization is independently pinned here, so refusal tests
    # its parser too rather than only the outer digest mismatch.
    if fault in ("noncanonical", "duplicate", "empty", "oversize"):
        options["expected_sha256"] = hashlib.sha256(prepared.path.read_bytes()).hexdigest()
    refused(lambda: load(prepared, **options))
    assert not (prepared.spec.receipts / "prepared.json").exists()


def test_late_or_changed_plan_during_preflight_has_no_runtime_side_effect(prepared, monkeypatch):
    original = m._bytes
    calls = 0

    def changed(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            prepared.path.write_bytes(prepared.path.read_bytes() + b"\n")
        return original(*args)

    monkeypatch.setattr(m, "_bytes", changed)
    refused(lambda: load(prepared))
    assert list(prepared.spec.receipts.iterdir()) == []


@pytest.mark.parametrize(
    "fault",
    [
        "source_changed",
        "config_changed",
        "deployment_changed",
        "accepted_missing",
        "late",
        "profile_drift",
    ],
)
def test_all_original_profile_inputs_and_deadline_stay_pinned(prepared, monkeypatch, fault):
    if fault == "source_changed":
        prepared.config.source_path.write_bytes(
            prepared.config.source_path.read_bytes() + b"Other\tPRIVATE\n"
        )
    elif fault == "config_changed":
        deployment = Path(prepared.value["profile"]["deployment"])
        config = deployment.parent / "display.toml"
        config.write_bytes(config.read_bytes() + b"\n# changed\n")
    elif fault == "deployment_changed":
        deployment = Path(prepared.value["profile"]["deployment"])
        deployment.write_bytes(deployment.read_bytes() + b"\n# changed\n")
    elif fault == "accepted_missing":
        # Test-only mutation of one exact private accepted-profile file.
        accepted = list(prepared.config.state_directory.rglob("accepted-profile.json"))
        assert len(accepted) == 1
        accepted[0].unlink()
    else:
        original = m.cached.profile_files
        calls = 0

        def read(*args):
            nonlocal calls
            result = original(*args)
            calls += 1
            if calls == 2:
                if fault == "late":
                    late = time.monotonic() + m.MAX_SECONDS + 1
                    monkeypatch.setattr(m.time, "monotonic", lambda: late)
                else:
                    result = ("f" * 64, result[1])
            return result

        monkeypatch.setattr(m.cached, "profile_files", read)
    refused(lambda: load(prepared))
    assert list(prepared.spec.receipts.iterdir()) == []
