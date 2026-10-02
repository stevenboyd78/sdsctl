"""Candidate packaging checks. No Home Assistant/scanner/credentials are used."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage_mimic_app", ROOT / "scripts/stage_mimic_app.py"
)
assert SPEC is not None and SPEC.loader is not None
stager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stager)
REVISION = "a" * 40


@pytest.fixture
def snapshot():
    # Only source-controlled packaging inputs. Never read workstation user files.
    names = ["pyproject.toml", "README.md", "LICENSE"]
    names += [
        "home-assistant/sds200/" + name
        for name in ("Dockerfile", "config.yaml", "translations/en.yaml", "icon.png", "logo.png")
    ]
    names += [
        "src/sds200/" + name
        for name in (
            "daemon_display_frames.py",
            "scanner_display_deployment.py",
            "scanner_display_ingress.py",
            "themes/home-assistant/mimic-sds/sds200-mimic-card.js",
        )
    ]
    return {name: (ROOT / name).read_bytes() for name in names}


def keys(text):
    return set(re.findall(r"^  ([a-z][a-z0-9_]*):", text, re.MULTILINE))


@pytest.mark.parametrize(
    "continuity,timing,transition_wait,bounded_writes",
    [
        (False, False, False, False),
        (True, False, False, False),
        (True, True, True, True),
        (True, True, False, False),
        (True, True, True, False),
    ],
)
def test_supplemental_staging_is_pinned_bounded_and_separate(
    snapshot, continuity, timing, transition_wait, bounded_writes
):
    runtime_name = "src/sds200/home_assistant_app_runtime.py"
    for name in (
        runtime_name,
        "scripts/research_system_status_daemon.py",
        "scripts/research_supplemental_daemon.py",
    ):
        snapshot[name] = (ROOT / name).read_bytes()
    normal = stager.render(snapshot, REVISION)
    research = stager.render(
        snapshot,
        REVISION,
        supplemental_firmware="Version 1.26.01",
        supplemental_continuity=continuity,
        supplemental_timing=timing,
        supplemental_transition_wait=transition_wait,
        supplemental_bounded_writes=bounded_writes,
    )
    assert normal[runtime_name] == snapshot[runtime_name]
    assert not any("research" in name for name in normal)
    suffix = (
        "-supplemental-bounded-write"
        if bounded_writes
        else "-supplemental-transition-wait"
        if transition_wait
        else "-supplemental-timing"
        if timing
        else ("-supplemental-continuity" if continuity else "-supplemental-research")
    )
    assert suffix in research["config.yaml"].decode()
    assert "boot: manual" in research["config.yaml"].decode()
    assert "50000/udp: null" in research["config.yaml"].decode()
    assert b"from research_supplemental_daemon import main" in research["research-entry.py"]
    assert b"--read-kind" not in research["research-entry.py"]
    assert (b"'--continuity'" in research["research-entry.py"]) is continuity
    assert (b"'--timing'" in research["research-entry.py"]) is timing
    assert (b"'--transition-wait'" in research["research-entry.py"]) is transition_wait
    assert (b"'--bounded-writes'" in research["research-entry.py"]) is bounded_writes
    compile(research["research-entry.py"], "research-entry.py", "exec")
    boundary = b"def build_home_assistant_web_command("
    assert (
        research[runtime_name].partition(boundary)[2] == normal[runtime_name].partition(boundary)[2]
    )
    report = json.loads(research["candidate-source.json"])
    assert report["research_read_kind"] == (
        "shared-clock-favorites-bounded-write"
        if bounded_writes
        else "shared-clock-favorites-transition-wait"
        if transition_wait
        else "shared-clock-favorites-timing"
        if timing
        else ("shared-clock-favorites-continuity" if continuity else "shared-clock-favorites")
    )
    assert report["research_max_opportunities"] == (60 if continuity else 6)
    assert report["research_window_seconds"] == (64 if continuity else 8)
    assert report["research_max_psi_gap_seconds"] == (2 if continuity else None)
    assert report["research_timing_event_limit"] == (512 if timing else None)
    assert report["research_automatic_start"] is False
    assert report["research_scan_transition_wait"] is transition_wait
    assert report["research_scan_transition_recovery_psi"] == (2 if transition_wait else None)
    if bounded_writes:
        assert report["research_write_policy"] == "native-posix-nonblocking"
        assert report["research_timing_schema"] == 2
        assert report["research_unobserved_phases"] == ["tx_intent", "rx_line", "rx_rejection"]
    else:
        assert "research_write_policy" not in report
    for name, digest in report["files"].items():
        assert hashlib.sha256(research[name]).hexdigest() == digest


@pytest.mark.parametrize("value", [True, None, 1, "yes"])
def test_continuity_cannot_be_implicitly_enabled_or_unpinned(value):
    with pytest.raises(ValueError, match="Continuity research"):
        stager.render({}, REVISION, supplemental_continuity=value)
    with pytest.raises(ValueError, match="Continuity research"):
        stager.from_revision(REVISION, supplemental_continuity=value)


@pytest.mark.parametrize("value", [True, None, 1, "yes"])
def test_timing_requires_explicit_continuity_and_pin(value):
    with pytest.raises(ValueError, match="Timing research"):
        stager.render({}, REVISION, supplemental_timing=value)
    with pytest.raises(ValueError, match="Timing research"):
        stager.from_revision(REVISION, supplemental_timing=value)


@pytest.mark.parametrize("value", [True, None, 0, 1, "yes"])
def test_transition_wait_requires_exact_policy_before_reading_source(value):
    with pytest.raises(ValueError, match="Transition research"):
        stager.render({}, REVISION, supplemental_transition_wait=value)
    with pytest.raises(ValueError, match="Transition research"):
        stager.from_revision(REVISION, supplemental_transition_wait=value)


@pytest.mark.parametrize("value", [True, None, 0, 1, "yes"])
def test_bounded_writes_require_explicit_transition_case_before_source_read(value):
    with pytest.raises(ValueError, match="Bounded-write research"):
        stager.render({}, REVISION, supplemental_bounded_writes=value)
    with pytest.raises(ValueError, match="Bounded-write research"):
        stager.from_revision(REVISION, supplemental_bounded_writes=value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"research_firmware": "Version 1.26.01"},
        {"display_read_firmware": "Version 1.26.01", "display_read_kind": "clock"},
        {"display_read_kind": "favorites"},
        {
            "front_panel_firmware": "Version 1.26.01",
            "front_panel_key": "M",
            "front_panel_mode": "Trunk Scan",
            "front_panel_screen": "trunk_scan",
        },
    ],
)
def test_supplemental_research_cannot_combine_modes(snapshot, kwargs):
    with pytest.raises(ValueError, match="Only one"):
        stager.render(snapshot, REVISION, supplemental_firmware="Version 1.26.01", **kwargs)


@pytest.mark.parametrize("firmware", ["", " a", "a\n", "a'", "a" * 65])
def test_supplemental_firmware_refused_before_reading_source(firmware):
    with pytest.raises(ValueError, match="firmware pin"):
        stager.render({}, REVISION, supplemental_firmware=firmware)


def test_research_staging_is_explicit_and_only_changes_daemon_launcher(snapshot):
    runtime_name = "src/sds200/home_assistant_app_runtime.py"
    launcher_name = "scripts/research_system_status_daemon.py"
    for name in (runtime_name, launcher_name):
        snapshot[name] = (ROOT / name).read_bytes()
    normal = stager.render(snapshot, REVISION)
    research = stager.render(snapshot, REVISION, research_firmware="Version 1.00.00")
    assert normal[runtime_name] == snapshot[runtime_name]
    assert "research-entry.py" not in normal
    assert "-ast-research" in research["config.yaml"].decode()
    assert "boot: manual" in research["config.yaml"].decode()
    assert research["research_system_status_daemon.py"] == snapshot[launcher_name]
    rewritten = research[runtime_name].decode()
    assert rewritten.count('"/usr/local/bin/sdsctl-system-status-research"') == 1
    web_boundary = "def build_home_assistant_web_command("
    assert (
        rewritten.partition(web_boundary)[2]
        == normal[runtime_name].decode().partition(web_boundary)[2]
    )
    compile(research["research-entry.py"], "research-entry.py", "exec")
    inventory = json.loads(research["candidate-source.json"])
    assert inventory["research_automatic_start"] is False
    for name, digest in inventory["files"].items():
        assert hashlib.sha256(research[name]).hexdigest() == digest
    assert research["translations/en.yaml"] == normal["translations/en.yaml"]


@pytest.mark.parametrize("kind", ["clock", "favorites", "system", "department"])
def test_display_read_staging_pins_one_kind_and_preserves_normal_runtime(snapshot, kind):
    runtime_name = "src/sds200/home_assistant_app_runtime.py"
    for name in (
        runtime_name,
        "scripts/research_system_status_daemon.py",
        "scripts/research_display_read_daemon.py",
    ):
        snapshot[name] = (ROOT / name).read_bytes()
    normal = stager.render(snapshot, REVISION)
    research = stager.render(
        snapshot, REVISION, display_read_firmware="Version 1.26.01", display_read_kind=kind
    )
    assert "research-entry.py" not in normal
    assert normal[runtime_name] == snapshot[runtime_name]
    assert f"-{kind}-research" in research["config.yaml"].decode()
    assert "-ast-research" not in research["config.yaml"].decode()
    assert "boot: manual" in research["config.yaml"].decode()
    assert "50000/udp: null" in research["config.yaml"].decode()
    assert f"'--read-kind', '{kind}'" in research["research-entry.py"].decode()
    rewritten = research[runtime_name].decode()
    assert rewritten.count('"/usr/local/bin/sdsctl-display-read-research"') == 1
    boundary = "def build_home_assistant_web_command("
    assert rewritten.partition(boundary)[2] == normal[runtime_name].decode().partition(boundary)[2]
    for name in (
        "research-entry.py",
        "research_system_status_daemon.py",
        "research_display_read_daemon.py",
    ):
        compile(research[name], name, "exec")
    report = json.loads(research["candidate-source.json"])
    assert report["research_read_kind"] == kind
    assert report["research_automatic_start"] is False
    assert report["purpose"] == "local-mimic-display-read-research-only"
    for name, digest in report["files"].items():
        assert hashlib.sha256(research[name]).hexdigest() == digest


def test_front_panel_staging_pins_one_press_context_and_preserves_normal_runtime(snapshot):
    runtime_name = "src/sds200/home_assistant_app_runtime.py"
    for name in (
        runtime_name,
        "scripts/research_system_status_daemon.py",
        "scripts/research_front_panel_daemon.py",
    ):
        snapshot[name] = (ROOT / name).read_bytes()
    normal = stager.render(snapshot, REVISION)
    research = stager.render(
        snapshot,
        REVISION,
        front_panel_firmware="Version 1.26.01",
        front_panel_key="M",
        front_panel_mode="Trunk Scan",
        front_panel_screen="trunk_scan",
    )
    assert "research-entry.py" not in normal
    assert normal[runtime_name] == snapshot[runtime_name]
    assert "-menu-key-research" in research["config.yaml"].decode()
    assert "boot: manual" in research["config.yaml"].decode()
    assert "50000/udp: null" in research["config.yaml"].decode()
    entry = research["research-entry.py"].decode()
    assert "'--key-code', 'M'" in entry
    assert "'--expected-mode', 'Trunk Scan'" in entry
    assert "'--expected-screen', 'trunk_scan'" in entry
    rewritten = research[runtime_name].decode()
    assert rewritten.count('"/usr/local/bin/sdsctl-front-panel-research"') == 1
    boundary = "def build_home_assistant_web_command("
    assert rewritten.partition(boundary)[2] == normal[runtime_name].decode().partition(boundary)[2]
    for name in (
        "research-entry.py",
        "research_system_status_daemon.py",
        "research_front_panel_daemon.py",
    ):
        compile(research[name], name, "exec")
    report = json.loads(research["candidate-source.json"])
    assert report["purpose"] == "local-mimic-front-panel-research-only"
    assert report["research_firmware_pin"] == "Version 1.26.01"
    assert report["research_key_code"] == "M"
    assert report["research_expected_mode"] == "Trunk Scan"
    assert report["research_expected_screen"] == "trunk_scan"
    assert report["research_automatic_start"] is False
    for name, digest in report["files"].items():
        assert hashlib.sha256(research[name]).hexdigest() == digest


@pytest.mark.parametrize(
    "kwargs",
    [
        {"front_panel_firmware": "Version 1.26.01"},
        {
            "front_panel_firmware": "Version 1.26.01",
            "front_panel_key": "A,P",
            "front_panel_mode": "Trunk Scan",
            "front_panel_screen": "trunk_scan",
        },
        {
            "front_panel_firmware": "Version 1.26.01",
            "front_panel_key": "M",
            "front_panel_mode": "Trunk Scan",
            "front_panel_screen": "private,screen",
        },
    ],
)
def test_front_panel_staging_requires_complete_safe_exact_pins(snapshot, kwargs):
    with pytest.raises(ValueError, match="Front-panel|front-panel"):
        stager.render(snapshot, REVISION, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"display_read_firmware": "Version 1.26.01"},
        {"display_read_kind": "clock"},
        {"display_read_firmware": "bad\n", "display_read_kind": "clock"},
        {"display_read_firmware": "Version 1.26.01", "display_read_kind": "DTM,0"},
        {
            "display_read_firmware": "Version 1.26.01",
            "display_read_kind": "clock",
            "research_firmware": "Version 1.26.01",
        },
    ],
)
def test_read_research_bad_or_combined_modes_fail_before_archive_access(monkeypatch, kwargs):
    monkeypatch.setattr(stager, "git", lambda *args: pytest.fail("Unexpected git read"))
    with pytest.raises(ValueError):
        stager.from_revision(REVISION, **kwargs)


@pytest.mark.parametrize("firmware", ["", "v;echo x", "v\n", " v", "v'", "x" * 65])
def test_research_staging_rejects_invalid_pins(snapshot, firmware):
    with pytest.raises(ValueError, match="firmware pin"):
        stager.render(snapshot, REVISION, research_firmware=firmware)


def test_candidate_is_paired_manual_and_disabled_by_default(snapshot):
    before = dict(snapshot)
    output = stager.render(snapshot, REVISION)
    manifest = output["config.yaml"].decode()
    assert snapshot == before
    assert 'slug: "sds200_mimic_acceptance"' in manifest
    assert 'version: "0.30.0-mimic-aaaaaaaaaaaa"' in manifest
    assert "image:" not in manifest
    assert "boot: manual\n" in manifest and "boot: auto\n" not in manifest
    for port in ("50000/udp", "50443/tcp", "8443/tcp"):
        assert f"  {port}: null\n" in manifest
    assert 'mqtt_topic_prefix: "sdsctl-mimic-acceptance"' in manifest
    assert 'recording_directory: "sdsctl-mimic-acceptance/recordings"' in manifest
    assert "remote_daemon_enabled: false" in manifest
    assert "native_dashboard_enabled: false" in manifest
    assert "qualified_sds200_menu_control_enabled: false" in manifest
    options, _, schema = manifest.partition("options:\n")[2].partition("schema:\n")
    expected = stager.PUBLIC_OPTIONS | stager.ACCEPTANCE_OPTIONS
    assert keys(schema) == expected
    assert keys(options) == expected - {"scanner_host"}
    assert 'scanner_display_config: ""' in options
    assert 'scanner_display_config: "str?"' in schema
    assert "qualified_sds200_menu_control_enabled: bool" in schema
    assert keys(output["translations/en.yaml"].decode()) == expected
    assert output["Dockerfile"] == snapshot["home-assistant/sds200/Dockerfile"]
    assert (
        output["src/sds200/daemon_display_frames.py"]
        == snapshot["src/sds200/daemon_display_frames.py"]
    )
    assert "scanner_display_config" not in before["home-assistant/sds200/config.yaml"].decode()
    assert (
        "qualified_sds200_menu_control_enabled"
        not in before["home-assistant/sds200/config.yaml"].decode()
    )


def test_candidate_inventory_is_deterministic_and_excludes_unrelated_files(snapshot):
    snapshot["Downloads/secret.cfg"] = b"PRIVATE_SENTINEL"
    output = stager.render(snapshot, REVISION)
    assert output == stager.render(snapshot, REVISION)
    report = json.loads(output["candidate-source.json"])
    assert report["source_revision"] == REVISION
    assert report["automatic_start"] is False
    assert report["profile_enabled_by_default"] is False
    assert report["files"] == {
        name: hashlib.sha256(data).hexdigest()
        for name, data in output.items()
        if name != "candidate-source.json"
    }
    assert all(b"PRIVATE_SENTINEL" not in data for data in output.values())
    assert not any(name.startswith("home-assistant/") for name in output)


@pytest.mark.parametrize("change", ["schema", "image", "version", "boot", "missing-runtime"])
def test_changed_source_contract_requires_review(snapshot, change):
    if change == "missing-runtime":
        del snapshot["src/sds200/daemon_display_frames.py"]
    else:
        path = "home-assistant/sds200/config.yaml"
        replacements = {
            "schema": (b"schema:\n", b"schema:\n  unexpected_option: bool\n"),
            "image": (b"ghcr.io/stevenboyd78/sds200-home-assistant", b"other/image"),
            "version": (b'version: "0.30.0"', b'version: "0.30.1"'),
            "boot": (b"boot: auto", b"boot: manual"),
        }
        old, new = replacements[change]
        snapshot[path] = snapshot[path].replace(old, new)
    with pytest.raises(ValueError):
        stager.render(snapshot, REVISION)


@pytest.mark.parametrize("revision", ["HEAD", "main", "v0.30.0", "a" * 39, "A" * 40, "../x"])
def test_revision_must_be_exact_before_git_access(monkeypatch, revision):
    monkeypatch.setattr(stager, "git", lambda *a: pytest.fail("Must reject before git"))
    with pytest.raises(ValueError):
        stager.from_revision(revision)


def test_new_stage_and_verify_are_nonmutating_to_source(snapshot, tmp_path):
    files = stager.render(snapshot, REVISION)
    destination = tmp_path / "candidate"
    stager.stage(destination, files)
    assert destination.stat().st_mode & 0o777 == 0o700
    stager.verify(destination, files)
    with pytest.raises(FileExistsError):
        stager.stage(destination, files)
    stager.verify(destination, files)


@pytest.mark.parametrize(
    "name", ["../outside", "/outside", "src/../../outside", "src//x", "./x", ""]
)
def test_stage_refuses_nonrelative_file_inventory_before_creation(tmp_path, name):
    destination = tmp_path / "candidate"
    with pytest.raises(ValueError):
        stager.stage(destination, {name: b"x"})
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["changed", "extra", "missing", "symlink", "hardlink", "fifo"])
def test_verify_detects_drift_and_preserves_it(tmp_path, kind):
    destination = tmp_path / "candidate"
    files = {"one": b"original", "sub/two": b"unchanged"}
    stager.stage(destination, files)
    one = destination / "one"
    if kind == "changed":
        one.write_bytes(b"modified")
    elif kind == "extra":
        (destination / "extra").write_bytes(b"new")
    elif kind == "missing":
        one.unlink()
    elif kind == "symlink":
        one.unlink()
        one.symlink_to(tmp_path / "outside")
    elif kind == "hardlink":
        os.link(one, tmp_path / "outside")
    else:
        one.unlink()
        os.mkfifo(one)
    with pytest.raises(ValueError):
        stager.verify(destination, files)
    assert (destination / "sub/two").read_bytes() == b"unchanged"
    if kind == "changed":
        assert one.read_bytes() == b"modified"


def test_symlink_parent_cannot_stage_or_verify(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        stager.stage(alias / "candidate", {"one": b"x"})
    stager.stage(real / "candidate", {"one": b"x"})
    with pytest.raises(ValueError):
        stager.verify(alias / "candidate", {"one": b"x"})


@pytest.mark.parametrize("dirty,head", [(True, REVISION), (False, "b" * 40)])
def test_cli_refuses_dirty_or_other_checkout(monkeypatch, tmp_path, dirty, head):
    destination = tmp_path / "candidate"
    monkeypatch.setattr(
        sys, "argv", ["stage", "--source-revision", REVISION, "--destination", str(destination)]
    )
    monkeypatch.setattr(stager, "from_revision", lambda r, **kwargs: {"one": b"x"})
    monkeypatch.setattr(
        stager,
        "git",
        lambda *a: head.encode() if a[0] == "rev-parse" else b"dirty" if dirty else b"",
    )
    with pytest.raises(ValueError):
        stager.main()
    assert not destination.exists()


def test_cli_verify_uses_committed_snapshot_not_self_attested_hashes(monkeypatch, tmp_path):
    destination = tmp_path / "candidate"
    stager.stage(destination, {"one": b"changed", "candidate-source.json": b"{}"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["stage", "--source-revision", REVISION, "--destination", str(destination), "--verify"],
    )
    monkeypatch.setattr(
        stager,
        "from_revision",
        lambda r, **kwargs: {"one": b"committed", "candidate-source.json": b"{}"},
    )
    monkeypatch.setattr(
        stager, "git", lambda *a: pytest.fail("Verification does not require clean HEAD")
    )
    with pytest.raises(ValueError):
        stager.main()
