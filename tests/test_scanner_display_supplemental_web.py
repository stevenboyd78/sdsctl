"""Offline coherent-bundle and actual WebUI polling/controller regressions."""

import json
import shutil
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.scanner_display_supplemental_wire import project_supplemental_web_bundle
from sds200.web_dashboard import create_web_dashboard_app

from .test_scanner_display_supplemental_presentation import capture as capture
from .test_scanner_display_supplemental_presentation import configured as configured
from .test_scanner_display_supplemental_presentation import engine as engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle(capture):
    return project_supplemental_web_bundle(capture, now=capture.captured_at)


def test_bundle_uses_one_cut_without_baking_clock_into_base_frame(bundle, engine):
    feed, _, _, scanner, _ = engine
    before = feed.snapshot()
    for frame in bundle["display"]["frames"].values():
        assert frame["sequence"] == bundle["supplemental"]["psi"]["sequence"]
        assert frame["age_seconds"] == bundle["supplemental"]["psi"]["age_seconds"]
        for region in frame["screen"]["regions"]:
            if region["token"] in ("Day", "Time"):
                assert region["text"] is None
    assert bundle["supplemental"]["clock"]["value"] == "2026-09-17T21:26:59"
    assert feed.snapshot() == before
    assert len(scanner.reads) == 2
    assert "PRIVATE" not in json.dumps(bundle)
    assert len(json.dumps(bundle).encode()) < 256 * 1024


def test_expired_bundle_is_cleared_consistently(capture):
    bundle = project_supplemental_web_bundle(capture, now=capture.captured_at + 5)
    assert bundle["supplemental"]["clock"]["value"] is None
    assert bundle["supplemental"]["favorites"]["value"] is None
    for frame in bundle["display"]["frames"].values():
        assert frame["status"] == "stale"
        assert all(region["text"] is None for region in frame["screen"]["regions"])


@pytest.mark.parametrize(
    "source", [None, "matching", "changed_since_import", "invalid_source", "source_unavailable"]
)
def test_bundle_never_fabricates_a_matching_profile_source(capture, source):
    with pytest.raises(ValueError, match="unchanged profile"):
        project_supplemental_web_bundle(
            replace(capture, source_status=source), now=capture.captured_at
        )


def test_bundle_rejects_pending_profile_in_every_layout(capture):
    candidate = replace(
        capture,
        **{
            name: replace(getattr(capture, name), profile_refresh_pending=True)
            for name in ("preferred", "simple", "detail")
        },
    )
    with pytest.raises(ValueError, match="unchanged profile"):
        project_supplemental_web_bundle(candidate, now=capture.captured_at)


@pytest.mark.parametrize("style", ["preferred", "simple", "detail"])
def test_bundle_rejects_a_frame_from_another_cut(capture, style):
    candidate = replace(
        capture, **{style: replace(getattr(capture, style), sequence=capture.sequence + 1)}
    )
    with pytest.raises(ValueError, match="one current scanner context"):
        project_supplemental_web_bundle(candidate, now=capture.captured_at)


def test_real_browser_controller_with_opt_in_bundle_and_local_expiry(bundle):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node needed for synthetic WebUI controller regression")
    with TestClient(create_web_dashboard_app(lambda: None)) as client:
        script = client.get("/assets/mimic-sds.js").text
        assert client.get("/assets/mimic-supplemental.js").status_code == 404
        assert "mimic-supplemental" not in client.get("/").text
    initial = deepcopy(bundle)
    initial["supplemental"]["psi"]["age_seconds"] = 0
    for frame in initial["display"]["frames"].values():
        frame["age_seconds"] = 0
    result = subprocess.run(
        [node, str(Path(__file__).with_name("scanner_display_supplemental_web.cjs"))],
        input=json.dumps(
            {
                "script": script,
                "auxiliary": (ROOT / "src/sds200/web_assets/mimic-supplemental.js").read_text(),
                "bundle": initial,
            }
        ),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "supplemental WebUI lifecycle passed" in result.stdout
