from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sds200.web_dashboard import create_web_dashboard_app
from sds200.web_themes import built_in_web_theme_registry


def forbidden_client():
    raise AssertionError("theme assets must never connect to the daemon")


def test_lcars_v2_is_separate_and_assets_are_local() -> None:
    registry = built_in_web_theme_registry()
    assert registry.require("lcars").label == "LCARS-inspired"
    assert registry.require("lcars-v2").label == "LCARS v2"
    with TestClient(create_web_dashboard_app(forbidden_client)) as client:
        shell = client.get("/").text
        assert '<html lang="en" data-theme="system"' in shell
        for identifier in ("lcars", "lcars-v2"):
            assert f'<option value="{identifier}">' in shell
        for name, media_type in (
            ("lcars-v2.css", "text/css"), ("lcars-v2.js", "application/javascript"),
            ("fonts/antonio-variable.ttf", "font/ttf"), ("fonts/antonio-OFL.txt", "text/plain"),
            ("themes/lcars-v2/theme.css", "text/css"),
        ):
            response = client.get(f"/assets/{name}")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(media_type)
            assert "font-src 'self'" in response.headers["content-security-policy"]
            assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/assets/fonts/unrecognized.ttf").status_code == 404
        assert 'href="assets/lcars-v2.css"' in shell
        assert 'src="assets/lcars-v2.js"' in shell
        assert shell.index('src="assets/lcars-v2.js"') < shell.index("</head>")


def test_font_is_unmodified_and_license_is_packaged() -> None:
    assets = files("sds200.web_assets")
    font = assets.joinpath("fonts/antonio-variable.ttf").read_bytes()
    blob = b"blob " + str(len(font)).encode("ascii") + b"\0" + font
    assert len(font) == 74104
    assert hashlib.sha1(blob, usedforsecurity=False).hexdigest() == (
        "e30920a139fbe8709cba29163b805b44f0a9876a"
    )
    license_bytes = assets.joinpath("fonts/antonio-OFL.txt").read_bytes()
    license_blob = b"blob " + str(len(license_bytes)).encode("ascii") + b"\0" + license_bytes
    assert hashlib.sha1(license_blob, usedforsecurity=False).hexdigest() == (
        "a218f7904561c3270701d07b60541a2ceefbab82"
    )
    license_text = license_bytes.decode("utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "2013 The Antonio Project Authors" in license_text
    css = assets.joinpath("lcars-v2.css").read_text()
    assert 'url("fonts/antonio-variable.ttf")' in css
    assert "font-display: swap" in css
    assert "https://" not in css and "http://" not in css


def test_lcars_v2_visibility_and_responsive_guards() -> None:
    assets = files("sds200.web_assets")
    css = assets.joinpath("lcars-v2.css").read_text()
    assert '#radio-activity-panel[hidden]' in css
    assert '.workspace-tabs [role="tab"][hidden]' in css
    assert '[data-kiosk-compact="true"] .workspace-tabs' in css
    assert '.workspace-tabs::before' in css and '--lcars-v2-nav-cap: 3.2rem;' in css
    script = assets.joinpath("lcars-v2.js").read_text()
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "innerHTML", "eval("):
        assert forbidden not in script
    for content in (css, script):
        assert "lcars-beta" not in content


def test_lcars_v2_controller_preferences_and_reversible_layout() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for browser-controller tests")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("lcars_v2.cjs")),
         str(files("sds200.web_assets").joinpath("lcars-v2.js"))],
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checks passed" in result.stdout


def test_palette_foreground_and_control_contrasts() -> None:
    css = files("sds200.themes").joinpath("web/lcars-v2/theme.css").read_text()
    variants = {
        name: dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6});", body))
        for name, body in re.findall(r'\[data-lcars-v2-palette="([\w-]+)"\]\s*\{([^}]+)\}', css)
    }
    assert set(variants) == {
        "classic", "nemesis-blue", "lower-decks", "lower-decks-padd", "voyager", "picard",
    }

    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722),
                                                          strict=True))

    for name, palette in variants.items():
        pairs = [(palette[token], "#000000") for token in (
            "--text", "--muted", "--lcars-v2-lilac", "--lcars-v2-heading",
            "--lcars-v2-apricot", "--accent-strong", "--source-nav-alternate",
        )]
        pairs.append((palette.get("--source-blue-ink", "#000000"), palette["--lcars-v2-blue"]))
        for foreground, background in pairs:
            bright, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
            assert (bright + 0.05) / (dark + 0.05) >= 4.5, (name, foreground, background)
        assert not {"--success", "--warning", "--danger"}.intersection(palette)
