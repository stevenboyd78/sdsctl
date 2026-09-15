from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

from sds200.exceptions import SDS200Error
from sds200.home_assistant_lovelace import (
    HOME_ASSISTANT_LOVELACE_MIMIC_CARD_RESOURCE_URL,
    install_home_assistant_lovelace_mimic_card,
)
from sds200.home_assistant_themes import (
    built_in_home_assistant_theme_registry,
    read_built_in_home_assistant_theme_module,
)

ROOT = Path(__file__).resolve().parents[1]


def module(identifier="mimic-sds"):
    return read_built_in_home_assistant_theme_module(
        built_in_home_assistant_theme_registry().require(identifier)
    ).decode()


def test_generated_cards_share_exact_browser_projection_and_session_owner():
    build = runpy.run_path(str(ROOT / "scripts/build_mimic_lovelace.py"))
    for path, expected in build["outputs"]().items():
        assert path.read_text() == expected
    assert (
        "/local/sds200/sds200-mimic-card.js?v=" + hashlib.sha256(module().encode()).hexdigest()
    ) == HOME_ASSISTANT_LOVELACE_MIMIC_CARD_RESOURCE_URL
    for forbidden in (
        "innerHTML",
        "localStorage",
        "sessionStorage",
        "console.",
        "XMLHttpRequest",
        "scanner_host",
        "source_path",
    ):
        assert forbidden not in module()


def test_mimic_installer_atomic_idempotent_and_preserves_unrelated_files(tmp_path):
    target = tmp_path / "www/sds200/sds200-mimic-card.js"
    target.parent.mkdir(parents=True)
    unrelated = target.parent / "keep.txt"
    unrelated.write_text("keep")
    assert install_home_assistant_lovelace_mimic_card(target) == target
    before = target.stat()
    install_home_assistant_lovelace_mimic_card(target)
    assert target.read_text() == module()
    assert target.stat().st_ino == before.st_ino
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert target.stat().st_mode & 0o777 == 0o644
    assert unrelated.read_text() == "keep"


@pytest.mark.parametrize("part", ["www", "sds200", "sds200-mimic-card.js"])
def test_mimic_installer_refuses_symlinks(tmp_path, part):
    target = tmp_path / "www/sds200/sds200-mimic-card.js"
    link = target if part.endswith(".js") else tmp_path / ("www" if part == "www" else "www/sds200")
    link.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    if part.endswith(".js"):
        outside.write_text("keep")
    else:
        outside.mkdir()
    link.symlink_to(outside, target_is_directory=not part.endswith(".js"))
    with pytest.raises(SDS200Error, match="refuses symlinks"):
        install_home_assistant_lovelace_mimic_card(target)


@pytest.mark.parametrize(
    "case",
    [
        "configuration",
        "render_all",
        "freshness",
        "identity",
        "hidden",
        "unmount",
        "late_auth",
        "auth_timeout",
        "invalid_auth",
        "shared_leases",
        "late_refresh",
        "late_context",
        "discovery",
        "discovery_timeout",
        "context_change",
        "invalid_frames",
        "response_bounds",
        "expired_auth",
        "fetch_timeout",
        "instance_options",
    ],
)
def test_mimic_browser_lifecycle(case):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node needed for actual HA card controller tests")
    scenarios = runpy.run_path(str(ROOT / "tests/test_scanner_display_web.py"))["scenarios"]()
    result = subprocess.run(
        [node, str(ROOT / "tests/home_assistant_mimic_lifecycle.cjs")],
        input=json.dumps(
            {
                "script": module(),
                "waterfall": module("waterfall"),
                "scenarios": scenarios,
                "case": case,
            }
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{case} passed"
