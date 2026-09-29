from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.web_dashboard import create_web_dashboard_app
from sds200.web_typography import WEB_TYPOGRAPHY_FONTS, WEB_TYPOGRAPHY_READ_PATHS


def forbidden_client():
    raise AssertionError("typography must not connect to the daemon")


def test_font_integrity_license_and_no_remote_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs/theme-font-sources.json").read_text())
    assert {Path(entry["path"]).name for entry in manifest["files"]} == WEB_TYPOGRAPHY_FONTS
    for entry in manifest["files"]:
        content = (root / entry["path"]).read_bytes()
        assert len(content) == entry["bytes"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        assert manifest["googleFontsRevision"] in entry["source"]
        if entry["path"].endswith(".txt"):
            assert b"SIL OPEN FONT LICENSE Version 1.1" in content
    css = files("sds200.web_assets").joinpath("theme-typography.css").read_text()
    fonts = set(re.findall(r'url\("fonts/([^\"]+)"\)', css))
    assert fonts == {name for name in WEB_TYPOGRAPHY_FONTS if name.endswith(".ttf")}
    assert css.count("font-display: swap") == len(fonts)
    assert "https://" not in css and "http://" not in css
    assert "rapid" not in css.lower() and "ocr" not in css.lower()
    # Legacy built-in themes have ID-specific font rules. Only font roles may
    # override those; sizes and Mimic cell fonts remain layout-owned.
    for role in ("body", "heading", "ui", "identity", "data"):
        assert f"font-family: var(--type-{role}) !important" in css
    assert ".native-summary dd" in css
    assert ".mimic-cell" not in css
    assert '"src/sds200/web_assets/fonts/*-OFL.txt"' in (root / "pyproject.toml").read_text()


def test_typography_routes_are_exact_local_and_do_not_contact_daemon() -> None:
    with TestClient(create_web_dashboard_app(forbidden_client)) as client:
        html = client.get("/").text
        assert 'href="assets/theme-typography.css"' in html
        assert html.index('src="assets/theme-typography.js"') < html.index("</head>")
        for path in WEB_TYPOGRAPHY_READ_PATHS:
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "font-src 'self'" in response.headers["content-security-policy"]
            if path.endswith(".ttf"):
                assert response.headers["content-type"] == "font/ttf"
            elif path.endswith(".txt"):
                assert response.headers["content-type"].startswith("text/plain")
        for name in (
            "unknown.ttf",
            "rapid-response.ttf",
            "ocr-a.ttf",
            "dashboard.js",
            "..%2Fdashboard.js",
        ):
            assert client.get(f"/assets/fonts/{name}").status_code == 404


def test_typography_controller_is_presentation_only_and_handles_preferences() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for browser controller tests")
    script = files("sds200.web_assets").joinpath("theme-typography.js")
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "innerHTML", "eval("):
        assert forbidden not in script.read_text()
    result = subprocess.run(
        [node, str(Path(__file__).with_name("theme_typography.cjs")), str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checks passed" in result.stdout


def test_typography_layout_centers_controls_and_keeps_responsive_fallbacks() -> None:
    css = files("sds200.web_assets").joinpath("theme-typography.css").read_text()
    # These rules must beat the legacy two-column theme overview without
    # replacing its display mode (compact kiosk and zoom own that behavior).
    wide, narrow = css.split("@media (max-width: 65rem)", 1)
    assert ":root[data-theme-typography] #main-content > .overview" in wide
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) !important" in wide
    assert "grid-column: 3 !important; grid-row: 1 !important" in wide
    assert "justify-content: center !important" in wide
    assert "grid-column: 2 !important; grid-row: 1 !important" in wide
    assert "grid-column: 1 / -1 !important; grid-row: 2 !important" in narrow
    assert "grid-template-columns: repeat(2, minmax(0, 1fr)) !important" in narrow
    assert "#native-menu #theme-typography-pickers:not([hidden])" in narrow
    assert "flex-direction: column !important; align-items: stretch !important" in narrow


def test_hidden_typography_rule_shares_important_display_layer() -> None:
    css = files("sds200.web_assets").joinpath("theme-typography.css").read_text()
    layer = css.split("@layer sdsctl-viewport-contract {", 1)[1]
    assert "#theme-font-picker[hidden] { display: none !important; }" in layer
    assert "#theme-typography-pickers[hidden]" in layer
    assert "#theme-typography-pickers > div {\n    display: grid !important" in layer
    # The real-Chrome audit additionally checks computed visibility, menu
    # reachability/touch sizing, and the unchanged phone geometry limits.
    script = files("sds200.web_assets").joinpath("dashboard.js").read_text()
    assert 'relocate(element("theme-typography-pickers"), appearance, true)' in script
    assert 'narrowAppearance = window.matchMedia("(max-width: 65rem)")' in script
    assert 'narrowAppearance.addEventListener("change", update)' in script


def test_compact_landscape_labels_share_selector_row_without_shrinking_controls() -> None:
    css = files("sds200.web_assets").joinpath("theme-typography.css").read_text()
    compact = css.split(
        "@media (min-width: 44.01rem) and (max-width: 65rem) and (max-height: 38rem)", 1
    )[1].split("#native-menu", 1)[0]
    assert ".overview #theme-typography-pickers > div:not([hidden])" in compact
    assert "grid-template-columns: auto minmax(0, 1fr) !important" in compact
    assert "align-items: center !important" in compact
    assert "font-size" not in compact and "min-height" not in compact
    assert "display:" not in compact  # Hidden controls retain their original rule.


def test_compact_panes_leave_room_for_taller_bundled_fonts() -> None:
    css = files("sds200.web_assets").joinpath("dashboard-viewport.css").read_text()
    # In narrow recording rows, actions share BOTH filename/metadata rows.
    # A second, separate action-height row clips with taller bundled fonts.
    phone = css.split(".recording-item-details strong {", 1)[1].split(".recording-action {", 1)[0]
    assert "grid-column: 1 !important" in phone
    assert "grid-row: 1 / 3 !important" in phone
    landscape = css.split(".waterfall-telemetry > div {", 1)[1]
    assert "padding-block: 0.1rem !important" in landscape
