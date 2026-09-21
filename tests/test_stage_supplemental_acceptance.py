"""Deterministic, offline build context and generated-entry qualification."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import types
from pathlib import Path
from uuid import UUID

import pytest

from sds200 import home_assistant_app_runtime as native
from sds200.home_assistant_app import HomeAssistantAppAdvancedExposure, HomeAssistantAppOptions
from sds200.home_assistant_app_advanced import default_home_assistant_app_advanced_access_paths

from . import test_stage_mimic_app
from .test_stage_mimic_app import stager as normal

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage_supplemental_acceptance", ROOT / "scripts/stage_supplemental_acceptance.py"
)
assert SPEC is not None and SPEC.loader is not None
stager = importlib.util.module_from_spec(SPEC)
previous = sys.modules.get("stage_mimic_app")
sys.modules["stage_mimic_app"] = normal
try:
    SPEC.loader.exec_module(stager)
finally:
    if previous is None:
        sys.modules.pop("stage_mimic_app")
    else:
        sys.modules["stage_mimic_app"] = previous

REVISION = "a" * 40
CASE = "123456789abc4def8abc123456789abc"
FIRMWARE = "Version 1.26.01"
normal_snapshot = test_stage_mimic_app.snapshot


@pytest.fixture
def snapshot(normal_snapshot):
    result = dict(normal_snapshot)
    for name in [stager.RUNTIME, *("scripts/" + n for n in (*stager.DRIVERS, *stager.LAUNCHERS))]:
        result[name] = (ROOT / name).read_bytes()
    return result


def render(snapshot, **kwargs):
    return stager.render(snapshot, REVISION, case_id=CASE, firmware=FIRMWARE, **kwargs)


def test_separate_deterministic_adapter_leaves_normal_staging_unchanged(snapshot):
    before = dict(snapshot)
    baseline = normal.render(snapshot, REVISION)
    result = render(snapshot)
    assert snapshot == before and result == render(snapshot)
    assert normal.render(snapshot, REVISION) == baseline
    assert not any("research" in name for name in result)
    changed = {name for name in baseline if baseline[name] != result[name]}
    assert changed == {
        stager.RUNTIME,
        "Dockerfile",
        "config.yaml",
        "DOCS.md",
        "candidate-source.json",
    }
    assert set(result) - set(baseline) == {
        *stager.LAUNCHERS,
        "supplemental-daemon-entry.py",
        "supplemental-web-entry.py",
    }
    report = json.loads(result["candidate-source.json"])
    assert report["case_id"] == CASE and report["source_revision"] == REVISION
    assert report["app_slug"] == stager.SLUG and stager.SLUG != normal.SLUG
    assert report["normal_acceptance_slug"] == normal.SLUG
    assert report["restoration_strategy"] == "separate-app-stop-start"
    assert report["expected_firmware"] == FIRMWARE
    assert report["guard_directory"] == f"/data/sdsctl-supplemental-acceptance-{CASE}"
    assert report["guardian_deadline_seconds"] == 684
    assert report["automatic_rearm"] is False and report["restoration_verified"] is False
    assert report["host_restoration_guard_required"] is True
    assert report["explicit_arm_required"] and report["explicit_authenticated_demand_required"]
    assert report["files"] == {
        name: hashlib.sha256(data).hexdigest()
        for name, data in result.items()
        if name != "candidate-source.json"
    }
    manifest = result["config.yaml"].decode()
    assert f'slug: "{stager.SLUG}"' in manifest
    assert 'mqtt_topic_prefix: "sdsctl-supplemental-acceptance"' in manifest
    assert 'recording_directory: "sdsctl-supplemental-acceptance/recordings"' in manifest
    assert f'slug: "{normal.SLUG}"' not in manifest
    assert "boot: manual\n" in manifest and "image:" not in manifest
    assert f'version: "{report["app_version"]}"' in manifest
    assert "-demand-" + CASE in manifest
    for port in ("50000/udp", "50443/tcp", "8443/tcp"):
        assert f"  {port}: null\n" in manifest
    assert result["translations/en.yaml"] == baseline["translations/en.yaml"]
    dockerfile = result["Dockerfile"].decode()
    assert f"COPY --chmod=0555 supplemental-daemon-entry.py {stager.DAEMON_ENTRY}" in dockerfile
    assert f"COPY --chmod=0555 supplemental-web-entry.py {stager.WEB_ENTRY}" in dockerfile
    for name in stager.LAUNCHERS:
        assert result[name] == snapshot["scripts/" + name]
        assert name in dockerfile


@pytest.mark.parametrize(
    "entry,module",
    [
        ("supplemental-daemon-entry.py", "guard_supplemental_acceptance"),
        ("supplemental-web-entry.py", "accept_supplemental_web"),
    ],
)
def test_generated_entries_use_fixed_case_and_pass_arguments_verbatim(
    snapshot, monkeypatch, entry, module
):
    code = render(snapshot)[entry]
    assert b"uuid" not in code and b"os.getpid" not in code and b"/run/" not in code
    calls = []
    replacement = types.ModuleType(module)
    replacement.main = lambda args: calls.append(args) or 17
    monkeypatch.setitem(sys.modules, module, replacement)
    supplied = [
        "--host",
        "192.0.2.1",
        "daemon",
        "--scanner-display-profile-config",
        "/data/profile.toml",
    ]
    monkeypatch.setattr(sys, "argv", [entry, *supplied])
    monkeypatch.setattr(sys, "path", list(sys.path))
    for _ in range(2):
        with pytest.raises(SystemExit) as stopped:
            exec(compile(code, entry, "exec"), {"__name__": "__main__"})
        assert stopped.value.code == 17
    assert calls[0] == calls[1]  # A restart cannot create a new identity.
    if module.startswith("guard"):
        assert calls[0] == [
            "--guard-directory",
            f"/data/sdsctl-supplemental-acceptance-{CASE}",
            "--source-revision",
            REVISION,
            "--expected-firmware",
            FIRMWARE,
            "--ready-timeout",
            "600",
            "--window-seconds",
            "64",
            "--max-read-attempts",
            "60",
            "--",
            *supplied,
        ]
    else:
        assert calls[0] == supplied


def test_shipped_guardian_and_child_refuse_a_second_start(snapshot, tmp_path):
    result = render(snapshot)
    image = tmp_path / "image"
    image.mkdir()
    guard_directory = tmp_path / "persistent-case"
    for name in stager.LAUNCHERS:
        (image / name).write_bytes(result[name])
    entry = image / "entry.py"
    # Relocate only the two image mount points into a disposable local fixture.
    # The generated arguments and both shipped process scripts stay unchanged.
    entry.write_bytes(
        result["supplemental-daemon-entry.py"]
        .replace(stager.IMAGE_SCRIPTS.encode(), str(image).encode())
        .replace(
            f"/data/sdsctl-supplemental-acceptance-{CASE}".encode(), str(guard_directory).encode()
        )
    )
    command = [sys.executable, str(entry), "daemon"]  # Missing profile: no scanner construction.
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    first = subprocess.run(command, env=environment, capture_output=True, timeout=15)
    assert first.returncode == 1
    evidence = {path.name: path.read_bytes() for path in guard_directory.iterdir()}
    assert json.loads(evidence["guard-result.json"])["child_exit_confirmed"] is True
    assert json.loads(evidence["guard-result.json"])["restoration_verified"] is False
    assert not (guard_directory / "daemon-case").exists()
    second = subprocess.run(command, env=environment, capture_output=True, timeout=15)
    assert second.returncode != 0
    assert b"FileExistsError" in second.stderr
    assert {path.name: path.read_bytes() for path in guard_directory.iterdir()} == evidence


def test_all_actual_app_child_builders_keep_arguments_and_media_unchanged(
    snapshot, monkeypatch, tmp_path
):
    staged = types.ModuleType("sds200._staged_supplemental_runtime")
    staged.__package__ = "sds200"
    monkeypatch.setitem(sys.modules, staged.__name__, staged)
    exec(compile(render(snapshot)[stager.RUNTIME], stager.RUNTIME, "exec"), staged.__dict__)
    for runtime in (native, staged):
        monkeypatch.setattr(
            runtime,
            "inspect_home_assistant_app_advanced_access",
            lambda p: types.SimpleNamespace(
                identity_present=True,
                dashboard_password_present=True,
                display_password_present=True,
            ),
        )
        monkeypatch.setattr(
            runtime,
            "load_home_assistant_app_advanced_access_state",
            lambda p: types.SimpleNamespace(
                identity_generation="123456789abc4def8abc123456789abc",
            ),
        )
    options = HomeAssistantAppOptions(
        scanner_host="192.0.2.5",
        native_dashboard_enabled=True,
        advanced_access_server_name="192.168.20.10",
    )
    exposure = HomeAssistantAppAdvancedExposure(
        container_address="172.30.33.7", native_dashboard_host_port=8443
    )
    advanced = default_home_assistant_app_advanced_access_paths(
        root=tmp_path / "data", runtime_directory=tmp_path / "run"
    )
    for name, expected in (
        ("build_home_assistant_daemon_command", stager.DAEMON_ENTRY),
        ("build_home_assistant_web_command", stager.WEB_ENTRY),
        ("build_home_assistant_native_web_command", stager.WEB_ENTRY),
        ("build_home_assistant_media_command", "python3"),
    ):
        commands = []
        for runtime in (native, staged):
            paths = runtime.default_home_assistant_app_runtime_paths()
            args, kwargs = (paths,), {}
            if "daemon_command" in name:
                args = (options, paths)
                kwargs = {"scanner_display_profile_config": Path("/data/profile.toml")}
            elif "native_web" in name:
                args = (options, exposure, paths, advanced)
            elif "web_command" in name:
                kwargs = {"scanner_display_config": Path("/data/profile.toml")}
            commands.append(getattr(runtime, name)(*args, **kwargs))
        assert commands[1][0] == expected
        assert commands[0][1:] == commands[1][1:]
        if "media_command" in name:
            assert commands[0] == commands[1]


@pytest.mark.parametrize(
    "revision,case,firmware",
    [
        ("HEAD", CASE, FIRMWARE),
        ("A" * 40, CASE, FIRMWARE),
        (REVISION, "../escape", FIRMWARE),
        (REVISION, "0" * 32, FIRMWARE),
        (REVISION, CASE.upper(), FIRMWARE),
        (REVISION, CASE, ""),
        (REVISION, CASE, " Version 1"),
        (REVISION, CASE, "v\n"),
        (REVISION, CASE, "x'"),
        (REVISION, CASE, "a" * 65),
    ],
)
def test_bad_pins_refused_before_git_or_render(monkeypatch, revision, case, firmware):
    monkeypatch.setattr(normal, "git", lambda *args: pytest.fail("Git invoked"))
    with pytest.raises(ValueError):
        stager.from_revision(revision, case_id=case, firmware=firmware)
    with pytest.raises(ValueError):
        stager.render({}, revision, case_id=case, firmware=firmware)


@pytest.mark.parametrize(
    "function",
    [
        "build_home_assistant_daemon_command",
        "build_home_assistant_web_command",
        "build_home_assistant_native_web_command",
    ],
)
def test_changed_or_prepatched_boundary_is_refused(snapshot, function):
    source = snapshot[stager.RUNTIME].decode()
    snapshot[stager.RUNTIME] = stager.launcher(source, function, "/other").encode()
    with pytest.raises(ValueError, match="source contract"):
        render(snapshot)
    with pytest.raises(ValueError, match="boundary"):
        stager.launcher(source + f"\ndef {function}(", function, "/unused")


def archive(snapshot):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode="w") as stream:
        for name, data in snapshot.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            stream.addfile(item, io.BytesIO(data))
    return target.getvalue()


def test_archive_is_source_pinned_and_checks_both_staging_drivers(snapshot, monkeypatch):
    calls = []
    monkeypatch.setattr(normal, "git", lambda *args: calls.append(args) or archive(snapshot))
    assert stager.from_revision(REVISION, case_id=CASE, firmware=FIRMWARE) == render(snapshot)
    assert calls[0][:4] == ("archive", "--format=tar", REVISION, "--")
    for name in (*stager.DRIVERS, *stager.LAUNCHERS):
        assert "scripts/" + name in calls[0]
    for name in stager.DRIVERS:
        original = snapshot["scripts/" + name]
        snapshot["scripts/" + name] += b"\n# drift\n"
        with pytest.raises(ValueError, match="staging adapters"):
            stager.from_revision(REVISION, case_id=CASE, firmware=FIRMWARE)
        snapshot["scripts/" + name] = original


@pytest.mark.parametrize(
    "change", ["script", "entry", "manifest", "extra", "missing", "case", "source"]
)
def test_verify_recomputes_exact_inventory_and_keeps_mismatches(snapshot, tmp_path, change):
    result = render(snapshot)
    destination = tmp_path / "candidate"
    normal.stage(destination, result)
    normal.verify(destination, result)
    if change == "missing":
        (destination / stager.LAUNCHERS[0]).unlink()
    elif change == "case":
        result = stager.render(
            snapshot, REVISION, case_id="23456789abcd4def8abc123456789abc", firmware=FIRMWARE
        )
    elif change == "source":
        result = stager.render(snapshot, "b" * 40, case_id=CASE, firmware=FIRMWARE)
    else:
        name = {
            "script": stager.LAUNCHERS[0],
            "entry": "supplemental-daemon-entry.py",
            "manifest": "candidate-source.json",
            "extra": "extra",
        }[change]
        (destination / name).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        normal.verify(destination, result)
    assert destination.exists()


def test_cli_generates_case_only_during_stage_requires_case_for_verify(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(stager, "from_revision", lambda rev, **kw: seen.append(kw) or {"one": b"x"})
    monkeypatch.setattr(normal, "git", lambda *a: REVISION.encode() if a[0] == "rev-parse" else b"")
    args = [
        "--source-revision",
        REVISION,
        "--expected-firmware",
        FIRMWARE,
        "--destination",
        str(tmp_path / "candidate"),
    ]
    stager.main(args)
    assert UUID(hex=seen[0]["case_id"]).version == 4
    with pytest.raises(SystemExit):
        stager.main([*args, "--verify"])
    assert len(seen) == 1
    stager.main([*args, "--verify", "--case-id", seen[0]["case_id"]])
    assert seen[0] == seen[1]
    with pytest.raises(FileExistsError):
        stager.main([*args, "--case-id", seen[0]["case_id"]])


@pytest.mark.parametrize("head,dirty", [("b" * 40, False), (REVISION, True)])
def test_cli_dirty_or_wrong_head_never_stages(monkeypatch, tmp_path, head, dirty):
    monkeypatch.setattr(stager, "from_revision", lambda *a, **kw: {"one": b"x"})
    monkeypatch.setattr(
        normal,
        "git",
        lambda *a: head.encode() if a[0] == "rev-parse" else b"dirty" if dirty else b"",
    )
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="clean current"):
        stager.main(
            [
                "--source-revision",
                REVISION,
                "--expected-firmware",
                FIRMWARE,
                "--destination",
                str(target),
            ]
        )
    assert not target.exists()
