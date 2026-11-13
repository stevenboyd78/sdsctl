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


def test_typography_controls_use_one_universal_drawer_layout() -> None:
    css = files("sds200.web_assets").joinpath("theme-typography.css").read_text()
    assert "#native-menu #theme-typography-pickers:not([hidden])" in css
    assert "grid-template-columns: minmax(0, 1fr) !important" in css
    assert "width: 100% !important" in css
    assert "text-align: left !important" in css
    assert "#main-content > .overview" not in css
    assert ".overview #theme-typography-pickers" not in css


def test_hidden_typography_rule_shares_important_display_layer() -> None:
    css = files("sds200.web_assets").joinpath("theme-typography.css").read_text()
    layer = css.split("@layer sdsctl-viewport-contract {", 1)[1]
    assert "#theme-font-picker[hidden] { display: none !important; }" in layer
    assert "#theme-typography-pickers[hidden]" in layer
    assert (
        "#native-menu #theme-typography-pickers > div:not([hidden]) {\n    display: grid !important"
    ) in layer
    # The real-Chrome audit additionally checks computed visibility, drawer
    # reachability/touch sizing, and the unchanged phone geometry limits.
    script = files("sds200.web_assets").joinpath("dashboard.js").read_text()
    assert "function initializeNativeMenu(logout, logoutButton)" in script
    assert 'element("lcars-v2-appearance-pickers")' in script
    assert 'element("theme-typography-pickers")' in script
    assert 'document.createComment("native drawer control position")' in script
    assert "appearance.append(node)" in script
    assert "narrowAppearance" not in script
    assert "function initializeDisplayNavigation(menu)" in script
    lcars = files("sds200.web_assets").joinpath("lcars-v2.css").read_text()
    menu_select = lcars.split("#native-menu #lcars-v2-appearance-pickers select {", 1)[1].split(
        "}", 1
    )[0]
    assert "min-height: 44px !important" in menu_select


def test_typography_drawer_geometry_has_no_viewport_specific_relocation() -> None:
    css = files("sds200.web_assets").joinpath("theme-typography.css").read_text()
    assert "@media (max-width: 65rem)" not in css
    assert "@media (min-width: 44.01rem)" not in css
    assert "#native-menu #theme-typography-pickers > div:not([hidden])" in css
    assert "grid-template-columns: minmax(0, 1fr) !important" in css
    assert "#theme-font-picker[hidden] { display: none !important; }" in css


def test_compact_panes_leave_room_for_taller_bundled_fonts() -> None:
    css = files("sds200.web_assets").joinpath("dashboard-viewport.css").read_text()
    # Generic phone cards retain their established two-row action contract.
    # The LCARS frame recovers its extra row space in the theme-specific layer.
    phone = css.split(".recording-item-details strong {", 1)[1].split(".recording-action {", 1)[0]
    assert "grid-column: 1 !important" in phone
    assert "grid-column: 2 !important" in phone
    assert "grid-row: 1 / 3 !important" in phone
    phone_current = css.split(".scanner-hold-current-value {", 1)[1].split(
        ".scanner-hold-action {", 1
    )[0]
    assert "overflow-wrap: anywhere !important" in phone_current
    assert "white-space: normal !important" in phone_current
    phone_viewport = css.split("@media (max-width: 44rem) and (min-height: 28rem)", 1)[1].split(
        "@media (max-width: 26rem) and (min-height: 28rem)", 1
    )[0]
    assert ".scanner-hold-scope:has(#scanner-hold-department)" in phone_viewport
    assert "grid-template-columns: minmax(0, 1fr) 3.95rem !important" in phone_viewport
    assert "#scanner-hold-department-state" in phone_viewport
    assert "min-width: 3.95rem !important" in phone_viewport
    lcars = files("sds200.web_assets").joinpath("lcars-v2.css").read_text()
    phone_lcars = lcars.split("@media (max-width: 43.99rem) and (min-height: 28rem)", 1)[1].split(
        "/* A secondary Site line", 1
    )[0]
    assert ".recording-list" in phone_lcars
    assert "gap: 0 !important" in phone_lcars
    assert ".recording-item" in phone_lcars
    assert "padding-block: 0 !important" in phone_lcars


def test_short_landscape_layout_compacts_each_real_chrome_root_cause() -> None:
    css = files("sds200.web_assets").joinpath("dashboard-viewport.css").read_text()
    landscape = css.split(
        "@media (min-width: 44.01rem) and (max-height: 38rem) and (min-height: 28rem)", 1
    )[1].split("/* At enlarged text or browser zoom", 1)[0]
    assert "#radio-group-hierarchy .radio-field-list > div" in landscape
    assert "grid-template-columns: minmax(0, 1fr) auto !important" in landscape
    assert "#radio-group-hierarchy .radio-field-list dd" in landscape
    assert "white-space: nowrap !important" in landscape
    assert "grid-template-columns: repeat(4, minmax(0, 1fr)) !important" in landscape
    assert "grid-template-columns: repeat(2, minmax(0, 1fr)) !important" in landscape
    assert "grid-template-rows: repeat(3, minmax(max-content, 1fr)) !important" in landscape
    assert "grid-template-columns: repeat(3, minmax(0, 1fr)) !important" in landscape
    assert "#system-palette-picker" in landscape
    assert "grid-template-columns: auto minmax(7rem, 11rem) !important" in landscape
    system_message = landscape.split(':root[data-theme="system"] #dashboard-message {', 1)[1].split(
        "}", 1
    )[0]
    assert "width: 100% !important" in system_message
    assert "max-width: 100% !important" in system_message
    assert "justify-self: stretch !important" in system_message
    assert "padding: 0.25rem 0.5rem !important" in system_message
    recording_list = landscape.split(".recording-list {", 1)[1].split("}", 1)[0]
    assert "gap: 0 !important" in recording_list
    recording_item = landscape.split(".recording-item {", 1)[1].split("}", 1)[0]
    assert "padding-block: 0 !important" in recording_item
    saved_playback = landscape.split(".saved-playback {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr) auto !important" in saved_playback
    saved_audio = landscape.split(".saved-playback audio {", 1)[1].split("}", 1)[0]
    assert "height: 1.35rem !important" in saved_audio
    saved_controls = landscape.split(".saved-playback .audio-controls {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: nowrap !important" in saved_controls
    assert "padding-block: 0 !important" in landscape
    assert "font-size: 0.6rem !important" in landscape
    assert "line-height: 1.2 !important" in landscape
