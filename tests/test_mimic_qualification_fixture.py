from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from html import unescape
from pathlib import Path

from sds200 import (
    FavoritesStorageDocument,
    FavoritesStorageSnapshot,
    parse_favorites_file,
    project_favorites_storage_snapshot,
    validate_favorites_workspace,
)


def _fixture_module() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    return runpy.run_path(str(root / "scripts/render_mimic_qualification_fixture.py"))


def test_generated_favorites_are_crlf_schema_valid_and_fail_safe() -> None:
    fixture = _fixture_module()
    catalog = fixture["build_favorites_catalog_bytes"]()
    hpd = fixture["build_favorites_hpd_bytes"]()

    assert catalog.replace(b"\r\n", b"").find(b"\n") == -1
    assert hpd.replace(b"\r\n", b"").find(b"\n") == -1
    assert catalog.endswith(b"\r\n") and hpd.endswith(b"\r\n")

    catalog_source = parse_favorites_file(catalog)
    entry = next(record for record in catalog_source.records if record.command == "F-List")
    assert entry.field_count == 117
    assert entry.fields[2:6] == ("Off", "Off", "Off", "Off")
    assert set(entry.fields[6:]) == {"Off"}

    workspace = project_favorites_storage_snapshot(
        FavoritesStorageSnapshot(
            catalog_bytes=catalog,
            documents=(FavoritesStorageDocument("f_000001.hpd", hpd),),
        )
    )
    validation = validate_favorites_workspace(workspace)
    assert validation.is_valid is True
    assert validation.diagnostics == ()


def test_favorites_and_display_manifest_cover_intended_boundaries() -> None:
    fixture = _fixture_module()
    hpd = parse_favorites_file(fixture["build_favorites_hpd_bytes"]())
    channels = [record for record in hpd.records if record.command == "C-Freq"]
    assert all(record.field_count == 18 for record in channels)
    assert {record.fields[5] for record in channels} == {"AUTO", "AM", "NFM", "FM"}
    assert {record.fields[13] for record in channels} == {
        "Off",
        "Blue",
        "Red",
        "Magenta",
        "Green",
        "Cyan",
        "Yellow",
        "White",
    }
    assert {record.fields[14] for record in channels} == {"On", "Slow Blink", "Fast Blink"}
    assert not {"WFM", "FMB"} & {record.fields[5] for record in channels}

    manifest = fixture["build_manifest"]()
    cases = {case["id"]: case for case in manifest["display_cases"]}
    scenarios = set(fixture["build_scenarios"]())
    assert set(cases) <= scenarios
    assert len(scenarios) == len(cases) + len(manifest["baseline_transition_cases"])
    assert set(cases).isdisjoint(manifest["baseline_transition_cases"])
    assert {"search_wfm", "search_fmb", "alert_red_inactive", "alert_red_active"} <= set(cases)
    assert {
        "system_motorola",
        "system_edacs",
        "system_scat",
        "system_ltr",
        "system_p25_standard",
        "system_p25_one_frequency",
        "system_p25_x2_tdma",
        "system_mototrbo",
        "system_dmr_one_frequency",
        "system_nxdn",
        "system_nxdn_one_frequency",
    } <= set(cases)
    assert manifest["status"] == "OFFLINE_ONLY_NOT_IMPORT_APPROVED"
    assert manifest["safety"]["scanner_access"] is False
    assert manifest["safety"]["network_access"] is False


def test_cli_writes_reviewable_artifact_and_refuses_overwrite(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "qualification"
    command = [
        sys.executable,
        str(root / "scripts/render_mimic_qualification_fixture.py"),
        "--output-dir",
        str(output),
    ]
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    first = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    written = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert written == {
        "README.md",
        "favorites/f_000001.hpd",
        "favorites/f_list.cfg",
        "index.html",
        "manifest.json",
    }
    assert "default-src 'none'" in unescape(
        (output / "index.html").read_text(encoding="utf-8")
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["display_cases"]) == len(_fixture_module()["build_display_cases"]())
    before = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}

    second = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert second.returncode != 0
    assert before == {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
