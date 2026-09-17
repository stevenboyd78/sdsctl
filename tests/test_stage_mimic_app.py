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
    options, _, schema = manifest.partition("options:\n")[2].partition("schema:\n")
    expected = stager.PUBLIC_OPTIONS | {"scanner_display_config"}
    assert keys(schema) == expected
    assert keys(options) == expected - {"scanner_host"}
    assert 'scanner_display_config: ""' in options
    assert 'scanner_display_config: "str?"' in schema
    assert keys(output["translations/en.yaml"].decode()) == expected
    assert output["Dockerfile"] == snapshot["home-assistant/sds200/Dockerfile"]
    assert (
        output["src/sds200/daemon_display_frames.py"]
        == snapshot["src/sds200/daemon_display_frames.py"]
    )
    assert "scanner_display_config" not in before["home-assistant/sds200/config.yaml"].decode()


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
