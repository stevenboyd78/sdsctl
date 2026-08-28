from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import zlib
from collections import Counter
from pathlib import Path
from struct import pack

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from scripts import generate_web_dashboard_screenshots as screenshots
from sds200.web_dashboard import create_web_dashboard_app

_EXPECTED_THEMES = (
    "system",
    "lcars",
    "matrix",
    "first-responder",
    "amateur-radio",
    "pip-boy-inspired",
)
_EXPECTED_CAPTURES = (
    ("theme-system-1920x1080.png", "system", 1920, 1080, 1, "scanner"),
    ("theme-system-390x844-dpr2.png", "system", 390, 844, 2, "scanner"),
    ("theme-lcars-1920x1080.png", "lcars", 1920, 1080, 1, "scanner"),
    ("theme-matrix-1920x1080.png", "matrix", 1920, 1080, 1, "scanner"),
    ("theme-first-responder-1920x1080.png", "first-responder", 1920, 1080, 1, "scanner"),
    ("theme-amateur-radio-1920x1080.png", "amateur-radio", 1920, 1080, 1, "scanner"),
    ("theme-amateur-radio-1366x768.png", "amateur-radio", 1366, 768, 1, "scanner"),
    ("theme-pip-boy-inspired-1920x1080.png", "pip-boy-inspired", 1920, 1080, 1, "scanner"),
    ("theme-pip-boy-inspired-800x480.png", "pip-boy-inspired", 800, 480, 1, "scanner"),
    ("theme-system-waterfall-1920x1080.png", "system", 1920, 1080, 1, "waterfall"),
    (
        "theme-pip-boy-inspired-waterfall-800x480.png",
        "pip-boy-inspired",
        800,
        480,
        1,
        "waterfall",
    ),
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(data, zlib.crc32(chunk_type)) & 0xFFFFFFFF
    return pack(">I", len(data)) + chunk_type + data + pack(">I", checksum)


def _png(width: int, height: int, *, pixel: bytes = b"\x00\x00\x00") -> bytes:
    header = pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + pixel * width for _row in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def _ready_dom(theme: str = "system", pane: str = "scanner") -> str:
    values = "".join(
        f'<div id="{element_id}">{value}</div>'
        for element_id, value in screenshots._READY_TEXT.items()
        if element_id not in {"last-update", "status-badge"}
    )
    return (
        f'<!doctype html><html data-theme="{theme}" data-workspace-pane="{pane}"><body>'
        '<div id="status-badge" data-state="online">Connected</div>'
        f"{values}"
        f'<time id="last-update" datetime="{screenshots._READY_LAST_UPDATE}">'
        f"{screenshots._READY_TEXT['last-update']}</time>"
        "</body></html>"
    )


def _capture_result(
    capture: screenshots.Capture,
    png_bytes: int,
    *,
    outer_html: str | None = None,
    viewport: dict[str, object] | None = None,
) -> str:
    exact_viewport: dict[str, object] = {
        "width": capture.width,
        "height": capture.height,
        "dpr": capture.device_scale_factor,
        "visualWidth": capture.width,
        "visualHeight": capture.height,
        "visualScale": 1,
        "reducedMotion": True,
    }
    if viewport is not None:
        exact_viewport.update(viewport)
    return json.dumps(
        {
            "outerHTML": outer_html if outer_html is not None else _ready_dom(capture.theme),
            "pngBytes": png_bytes,
            "viewport": exact_viewport,
        }
    )


def _command_value(command: list[str], option: str) -> str:
    return command[command.index(option) + 1]


def test_capture_inventory_covers_every_theme_and_reference_viewport() -> None:
    assert screenshots._THEMES == _EXPECTED_THEMES
    assert (
        tuple(
            (
                capture.filename,
                capture.theme,
                capture.width,
                capture.height,
                capture.device_scale_factor,
                capture.pane,
            )
            for capture in screenshots.CAPTURES
        )
        == _EXPECTED_CAPTURES
    )
    assert len({capture.filename for capture in screenshots.CAPTURES}) == len(screenshots.CAPTURES)
    assert set(capture.theme for capture in screenshots.CAPTURES) == set(_EXPECTED_THEMES)

    full_hd_counts = Counter(
        capture.theme
        for capture in screenshots.CAPTURES
        if capture.pane == "scanner"
        and (capture.width, capture.height, capture.device_scale_factor) == (1920, 1080, 1)
    )
    assert full_hd_counts == Counter({theme: 1 for theme in _EXPECTED_THEMES})

    reference_captures = {
        (
            capture.theme,
            capture.width,
            capture.height,
            capture.device_scale_factor,
        )
        for capture in screenshots.CAPTURES
        if capture.pane == "scanner" and (capture.width, capture.height) != (1920, 1080)
    }
    assert reference_captures == {
        ("system", 390, 844, 2),
        ("pip-boy-inspired", 800, 480, 1),
        ("amateur-radio", 1366, 768, 1),
    }


def test_generator_help_requires_node_24_and_exact_css_viewports() -> None:
    help_text = screenshots._parser().format_help()

    assert "exact-CSS-viewport" in help_text
    assert "Node.js 24 CDP" in help_text
    assert "Node.js 24-or-newer executable" in help_text
    assert "post-readiness settling milliseconds" in help_text
    assert "startup and CDP-operation timeout" in help_text


def test_checked_in_gallery_docs_and_wiki_are_one_exact_contract() -> None:
    assert screenshots._gallery_contract_errors() == ()


def test_demo_theme_route_accepts_only_the_exact_capture_themes() -> None:
    app = screenshots.create_demo_app()
    demo_routes = [
        path
        for route in app.routes
        if (path := getattr(route, "path", "")).startswith("/__demo/theme/")
    ]
    assert demo_routes == ["/__demo/theme/{theme}"]

    with TestClient(app) as client:
        response_bodies = set()
        for theme in _EXPECTED_THEMES:
            response = client.get(f"/__demo/theme/{theme}")

            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            response_bodies.add(response.text)
            assert 'const theme = location.pathname.split("/").at(-1);' in response.text
            assert "if (allowedThemes.includes(theme))" in response.text
            assert 'localStorage.setItem("sdsctl.web.theme", theme);' in response.text
            assert "if (allowedPanes.includes(pane))" in response.text
            assert 'localStorage.setItem("sdsctl.web.pane", pane);' in response.text
            assert 'location.replace("/");' in response.text

        unknown = client.get("/__demo/theme/not-a-built-in-theme")
        unknown_pane = client.get("/__demo/theme/system?pane=not-a-workspace-pane")

    assert response_bodies == {screenshots._DEMO_THEME_SETUP_HTML}
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "unknown demo theme"}
    assert unknown_pane.status_code == 404
    assert unknown_pane.json() == {"detail": "unknown demo pane"}


def test_demo_inventory_exercises_real_recording_pagination() -> None:
    entries = screenshots.DEMO_RECORDINGS["entries"]

    assert isinstance(entries, list)
    assert len(entries) == 7
    assert screenshots.DEMO_RECORDINGS["total_entries"] == 12
    assert all(entry["playable"] is True for entry in entries)
    assert len({entry["audio"] for entry in entries}) == len(entries)
    assert (len(entries) + 2) // 3 == 3


def test_demo_app_mounts_an_ingress_style_url_prefix() -> None:
    app = screenshots.create_demo_app()

    with TestClient(app) as client:
        shell = client.get("/__demo/prefix/")
        stylesheet = client.get("/__demo/prefix/assets/dashboard.css")
        status = client.get("/__demo/prefix/api/v1/status")

    assert shell.status_code == 200
    assert 'href="assets/dashboard.css"' in shell.text
    assert stylesheet.status_code == 200
    assert status.status_code == 200
    assert status.json()["daemon"]["snapshot"]["scanner_model"] == "SDS200"


def test_demo_clock_is_external_prepaint_only_and_demo_scoped() -> None:
    app = screenshots.create_demo_app()

    with TestClient(app) as client:
        shell = client.get("/")
        clock = client.get(screenshots._DEMO_CLOCK_PATH)
        dashboard_script = client.get("/assets/dashboard.js")
        theme_bootstrap = client.get("/assets/theme-bootstrap.js")
        prefixed_shell = client.get("/__demo/prefix/")
        prefixed_clock = client.get("/__demo/prefix/__demo/fixed-clock.js")

    for response in (shell, prefixed_shell):
        assert response.status_code == 200
        content_security_policy = response.headers["content-security-policy"]
        assert "script-src 'self'" in content_security_policy
        assert "'unsafe-inline'" not in content_security_policy
        assert screenshots._DEMO_CLOCK_SCRIPT_TAG.strip() in response.text
        assert response.text.index(screenshots._DEMO_CLOCK_SCRIPT_TAG.strip()) < (
            response.text.index('src="assets/theme-bootstrap.js"')
        )
        assert response.text.count(screenshots._DEMO_CLOCK_SCRIPT_TAG.strip()) == 1

    for response in (clock, prefixed_clock):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
        assert response.headers["cache-control"] == "no-store"
        assert response.text == screenshots._DEMO_CLOCK_JAVASCRIPT

    production_app = create_web_dashboard_app(screenshots.DemoDaemonApiClient)
    with TestClient(production_app) as client:
        production_shell = client.get("/")
        production_clock = client.get(screenshots._DEMO_CLOCK_PATH)
        production_dashboard_script = client.get("/assets/dashboard.js")
        production_theme_bootstrap = client.get("/assets/theme-bootstrap.js")

    assert screenshots._DEMO_CLOCK_SCRIPT_TAG.strip() not in production_shell.text
    assert production_clock.status_code == 404
    assert shell.text.replace(screenshots._DEMO_CLOCK_SCRIPT_TAG, "") == (production_shell.text)
    assert dashboard_script.content == production_dashboard_script.content
    assert theme_bootstrap.content == production_theme_bootstrap.content


def test_demo_clock_freezes_time_and_default_locale_formatting() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available")

    harness = f"""
{screenshots._DEMO_CLOCK_JAVASCRIPT}
const result = {{
  now: Date.now(),
  currentIso: new Date().toISOString(),
  currentDisplay: new Date().toLocaleString(),
  recordingIso: new Date("2026-08-08T15:12:18-06:00").toISOString(),
  recordingDisplay: new Date("2026-08-08T15:12:18-06:00").toLocaleString(),
}};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [node, "-e", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == (
        '{"now":1786223520000,'
        '"currentIso":"2026-08-08T21:12:00.000Z",'
        '"currentDisplay":"8/8/2026, 3:12:00 PM",'
        '"recordingIso":"2026-08-08T21:12:18.000Z",'
        '"recordingDisplay":"8/8/2026, 3:12:18 PM"}'
    )


def test_capture_readiness_requires_complete_authoritative_demo_dom() -> None:
    capture = screenshots.Capture("ready.png", "system", 8, 6)
    ready = _ready_dom()

    assert screenshots._capture_dom_is_ready(ready, capture)
    assert not screenshots._capture_dom_is_ready(
        ready.replace('data-theme="system"', 'data-theme="matrix"'),
        capture,
    )
    assert not screenshots._capture_dom_is_ready(
        ready.replace('data-state="online"', 'data-state="loading"'),
        capture,
    )
    assert not screenshots._capture_dom_is_ready(
        ready.replace(
            "Daemon and scanner status are available.",
            "Live events are reconnecting; status polling remains active.",
        ),
        capture,
    )
    for expected in screenshots._READY_TEXT.values():
        assert not screenshots._capture_dom_is_ready(
            ready.replace(expected, "not ready", 1),
            capture,
        )


def test_capture_uses_same_frame_dom_readiness_and_reduced_motion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 6, 4)
    profile = tmp_path / "profile"
    output = tmp_path / "output"
    profile.mkdir()
    output.mkdir()
    observed_command: list[str] = []
    staged_destination: Path | None = None

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal staged_destination
        observed_command.extend(command)
        staged_destination = Path(_command_value(command, "--output"))
        content = _png(6, 4)
        staged_destination.write_bytes(content)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_capture_result(capture, len(content)),
        )

    monkeypatch.setattr(screenshots.subprocess, "run", run)

    destination = screenshots._capture(
        chrome="chrome",
        node="node",
        profile_dir=profile,
        base_url="http://127.0.0.1:8000",
        capture=capture,
        output_dir=output,
        virtual_time_ms=2500,
        capture_timeout_seconds=20.0,
    )

    assert destination == output / capture.filename
    assert observed_command[:2] == [
        "node",
        str(screenshots._REPOSITORY_ROOT / screenshots._CAPTURE_HELPER),
    ]
    assert "--window-size" not in observed_command
    assert "--screenshot" not in observed_command
    assert "--dump-dom" not in observed_command
    assert _command_value(observed_command, "--width") == "6"
    assert _command_value(observed_command, "--height") == "4"
    assert _command_value(observed_command, "--dpr") == "1"
    assert _command_value(observed_command, "--pane") == "scanner"
    assert _command_value(observed_command, "--settle-ms") == "2500"
    assert staged_destination is not None
    assert staged_destination.parent == output
    assert staged_destination != destination
    assert staged_destination.suffix == ".png"
    assert _command_value(observed_command, "--output") == str(staged_destination)
    assert destination.read_bytes() == _png(6, 4)
    assert set(output.iterdir()) == {destination}


def test_capture_preserves_existing_destination_when_dom_is_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 6, 4)
    profile = tmp_path / "profile"
    output = tmp_path / "output"
    profile.mkdir()
    output.mkdir()
    destination = output / capture.filename
    original = b"existing authoritative image"
    destination.write_bytes(original)

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        staged = Path(_command_value(command, "--output"))
        content = _png(6, 4)
        staged.write_bytes(content)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_capture_result(
                capture,
                len(content),
                outer_html="<html><body>Loading</body></html>",
            ),
        )

    monkeypatch.setattr(screenshots.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="before the deterministic dashboard state was ready"):
        screenshots._capture(
            chrome="chrome",
            node="node",
            profile_dir=profile,
            base_url="http://127.0.0.1:8000",
            capture=capture,
            output_dir=output,
            virtual_time_ms=2500,
            capture_timeout_seconds=20.0,
        )

    assert destination.read_bytes() == original
    assert set(output.iterdir()) == {destination}


def test_capture_timeout_preserves_existing_destination_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 6, 4)
    profile = tmp_path / "profile"
    output = tmp_path / "output"
    profile.mkdir()
    output.mkdir()
    destination = output / capture.filename
    original = b"existing authoritative image"
    destination.write_bytes(original)

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(_command_value(command, "--output")).write_bytes(_png(6, 4))
        raise subprocess.TimeoutExpired(command, 30.0, output="helper timed out")

    monkeypatch.setattr(screenshots.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="timed out before completing"):
        screenshots._capture(
            chrome="chrome",
            node="node",
            profile_dir=profile,
            base_url="http://127.0.0.1:8000",
            capture=capture,
            output_dir=output,
            virtual_time_ms=2500,
            capture_timeout_seconds=20.0,
        )

    assert destination.read_bytes() == original
    assert set(output.iterdir()) == {destination}


def test_capture_atomically_replaces_symlink_without_touching_its_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 6, 4)
    profile = tmp_path / "profile"
    output = tmp_path / "output"
    profile.mkdir()
    output.mkdir()
    symlink_target = tmp_path / "outside-gallery.png"
    target_content = b"must remain untouched"
    symlink_target.write_bytes(target_content)
    destination = output / capture.filename
    try:
        destination.symlink_to(symlink_target)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        staged = Path(_command_value(command, "--output"))
        content = _png(6, 4)
        staged.write_bytes(content)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_capture_result(capture, len(content)),
        )

    monkeypatch.setattr(screenshots.subprocess, "run", run)

    result = screenshots._capture(
        chrome="chrome",
        node="node",
        profile_dir=profile,
        base_url="http://127.0.0.1:8000",
        capture=capture,
        output_dir=output,
        virtual_time_ms=2500,
        capture_timeout_seconds=20.0,
    )

    assert result == destination
    assert not destination.is_symlink()
    assert destination.read_bytes() == _png(6, 4)
    assert symlink_target.read_bytes() == target_content
    assert set(output.iterdir()) == {destination}


def test_capture_rejects_inexact_cdp_viewport_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 390, 844, 2)
    profile = tmp_path / "profile"
    output = tmp_path / "output"
    profile.mkdir()
    output.mkdir()
    destination = output / capture.filename
    destination.write_bytes(b"existing authoritative image")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        staged = Path(_command_value(command, "--output"))
        content = _png(780, 1688)
        staged.write_bytes(content)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_capture_result(
                capture,
                len(content),
                viewport={"width": 500, "height": 757},
            ),
        )

    monkeypatch.setattr(screenshots.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="exact requested CSS viewport"):
        screenshots._capture(
            chrome="chrome",
            node="node",
            profile_dir=profile,
            base_url="http://127.0.0.1:8000",
            capture=capture,
            output_dir=output,
            virtual_time_ms=2500,
            capture_timeout_seconds=20.0,
        )

    assert destination.read_bytes() == b"existing authoritative image"
    assert set(output.iterdir()) == {destination}


def test_demo_theme_response_rejects_untrusted_script_input() -> None:
    with pytest.raises(HTTPException) as error:
        screenshots._demo_theme_response("system');alert('unexpected")

    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        screenshots._demo_theme_response("system", "waterfall');alert('unexpected")

    assert error.value.status_code == 404


def test_waterfall_capture_readiness_requires_stable_live_frame() -> None:
    capture = screenshots.Capture(
        "waterfall.png",
        "system",
        8,
        6,
        pane="waterfall",
    )
    ready = _ready_dom(pane="waterfall").replace(
        "</body>",
        "".join(
            f'<div id="{element_id}">{value}</div>'
            for element_id, value in screenshots._READY_WATERFALL_TEXT.items()
        )
        + "</body>",
    )

    assert screenshots._capture_dom_is_ready(ready, capture)
    assert not screenshots._capture_dom_is_ready(
        ready.replace(">33<", ">32<", 1),
        capture,
    )


def test_capture_selection_preserves_inventory_order_and_rejects_unknown() -> None:
    requested = (
        "theme-pip-boy-inspired-800x480.png",
        "theme-system-1920x1080.png",
    )

    selected = screenshots._selected_captures(requested)

    assert tuple(capture.filename for capture in selected) == (
        "theme-system-1920x1080.png",
        "theme-pip-boy-inspired-800x480.png",
    )
    with pytest.raises(SystemExit, match="unknown capture filename"):
        screenshots._selected_captures(("theme-unknown-1920x1080.png",))


def test_repeatability_helper_uses_isolated_temporary_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 6, 4)
    profiles = tmp_path / "profiles"
    scratch = tmp_path / "scratch"
    profiles.mkdir()
    scratch.mkdir()

    def capture_once(**arguments: object) -> Path:
        output_dir = arguments["output_dir"]
        assert isinstance(output_dir, Path)
        destination = output_dir / capture.filename
        destination.write_bytes(_png(6, 4))
        return destination

    monkeypatch.setattr(screenshots, "_capture", capture_once)

    digest = screenshots._verify_capture_repeatability(
        chrome="chrome",
        node="node",
        profile_root=profiles,
        scratch_root=scratch,
        base_url="http://127.0.0.1:8000",
        capture=capture,
        capture_index=1,
        virtual_time_ms=2500,
        capture_timeout_seconds=20.0,
    )

    assert digest == screenshots._sha256(scratch / "capture-01-first" / capture.filename)
    assert (scratch / "capture-01-second" / capture.filename).is_file()


def test_repeatability_helper_rejects_different_same_chrome_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 6, 4)
    profiles = tmp_path / "profiles"
    scratch = tmp_path / "scratch"
    profiles.mkdir()
    scratch.mkdir()
    run = 0

    def capture_once(**arguments: object) -> Path:
        nonlocal run
        run += 1
        output_dir = arguments["output_dir"]
        assert isinstance(output_dir, Path)
        destination = output_dir / capture.filename
        pixel = b"\x00\x00\x00" if run == 1 else b"\x01\x00\x00"
        destination.write_bytes(_png(6, 4, pixel=pixel))
        return destination

    monkeypatch.setattr(screenshots, "_capture", capture_once)

    with pytest.raises(RuntimeError, match="produced different PNG bytes"):
        screenshots._verify_capture_repeatability(
            chrome="chrome",
            node="node",
            profile_root=profiles,
            scratch_root=scratch,
            base_url="http://127.0.0.1:8000",
            capture=capture,
            capture_index=1,
            virtual_time_ms=2500,
            capture_timeout_seconds=20.0,
        )


def test_dpr_capture_validation_uses_physical_png_dimensions(
    tmp_path: Path,
) -> None:
    capture = screenshots.Capture(
        "theme-system-test-dpr2.png",
        "system",
        3,
        2,
        2,
    )
    screenshot = tmp_path / capture.filename

    screenshot.write_bytes(_png(6, 4))
    assert screenshots._valid_screenshot(screenshot, capture)

    screenshot.write_bytes(_png(3, 2))
    assert not screenshots._valid_screenshot(screenshot, capture)


def test_png_validation_rejects_truncation_forged_headers_and_bad_crc(
    tmp_path: Path,
) -> None:
    capture = screenshots.Capture("capture.png", "system", 6, 4)
    screenshot = tmp_path / capture.filename
    valid = _png(6, 4)

    screenshot.write_bytes(valid)
    assert screenshots._valid_screenshot(screenshot, capture)

    forged_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + pack(">II", 6, 4)
    screenshot.write_bytes(forged_header)
    assert not screenshots._valid_screenshot(screenshot, capture)

    screenshot.write_bytes(valid[:-1])
    assert not screenshots._valid_screenshot(screenshot, capture)

    corrupted = bytearray(valid)
    corrupted[-1] ^= 0x01
    screenshot.write_bytes(corrupted)
    assert not screenshots._valid_screenshot(screenshot, capture)


def test_gallery_rejects_missing_symlink_directory_and_nonregular_images(
    tmp_path: Path,
) -> None:
    gallery = tmp_path / screenshots._GALLERY_DIRECTORY
    gallery.parent.mkdir(parents=True)
    shutil.copytree(screenshots._REPOSITORY_ROOT / screenshots._GALLERY_DIRECTORY, gallery)
    for relative in (screenshots._GALLERY_GUIDE, screenshots._GALLERY_WIKI):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(screenshots._REPOSITORY_ROOT / relative, destination)

    capture = screenshots.CAPTURES[0]
    screenshot = gallery / capture.filename
    original = screenshot.read_bytes()

    screenshot.unlink()
    errors = screenshots._gallery_contract_errors(tmp_path)
    assert any("missing or inaccessible" in error for error in errors)
    screenshot.write_bytes(original)

    broken_target = tmp_path / "does-not-exist.png"
    try:
        screenshot.unlink()
        screenshot.symlink_to(broken_target)
    except OSError as error:
        pytest.skip(f"symbolic links are unavailable: {error}")
    errors = screenshots._gallery_contract_errors(tmp_path)
    assert any("symbolic link" in error for error in errors)
    screenshot.unlink()

    valid_target = tmp_path / "valid-target.png"
    valid_target.write_bytes(original)
    screenshot.symlink_to(valid_target)
    errors = screenshots._gallery_contract_errors(tmp_path)
    assert any("symbolic link" in error for error in errors)
    screenshot.unlink()

    screenshot.mkdir()
    errors = screenshots._gallery_contract_errors(tmp_path)
    assert any("non-regular entry" in error for error in errors)
    screenshot.rmdir()

    if hasattr(os, "mkfifo"):
        os.mkfifo(screenshot)
        errors = screenshots._gallery_contract_errors(tmp_path)
        assert any("non-regular entry" in error for error in errors)
        screenshot.unlink()


def test_source_distribution_requires_exact_regular_checkout_copies(
    tmp_path: Path,
) -> None:
    root = "sds200-0.23.0"
    source_contents = {
        relative_name: path.read_bytes()
        for relative_name, path in screenshots._required_sdist_sources().items()
    }
    assert "scripts/audit_web_dashboard_browser.mjs" in source_contents
    assert "scripts/capture_web_dashboard_screenshot.mjs" in source_contents

    def write_distribution(
        filename: str,
        members: dict[str, bytes],
        *,
        duplicate: str | None = None,
        nonregular: str | None = None,
    ) -> Path:
        distribution = tmp_path / filename
        with tarfile.open(distribution, mode="w:gz", compresslevel=1) as archive:
            for member, data in sorted(members.items()):
                info = tarfile.TarInfo(f"{root}/{member}")
                if member == nonregular:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "elsewhere"
                    archive.addfile(info)
                else:
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            if duplicate is not None:
                data = members[duplicate]
                info = tarfile.TarInfo(f"{root}/{duplicate}")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return distribution

    complete = write_distribution("sds200-0.23.0.tar.gz", source_contents)
    assert screenshots._verify_source_distribution(complete) == complete

    fake_payloads = {member: member.encode("utf-8") for member in source_contents}
    forged = write_distribution("sds200-0.23.0-forged.tar.gz", fake_payloads)
    with pytest.raises(RuntimeError, match="has size|does not exactly match"):
        screenshots._verify_source_distribution(forged)

    missing = write_distribution(
        "sds200-0.23.0-missing.tar.gz",
        {
            member: content
            for member, content in source_contents.items()
            if member != "docs/assets/web-dashboard/theme-system-1920x1080.png"
        },
    )
    with pytest.raises(RuntimeError, match="missing="):
        screenshots._verify_source_distribution(missing)

    stale_contents = dict(source_contents)
    stale_contents["docs/assets/web-dashboard/stale.png"] = b"stale"
    unexpected = write_distribution(
        "sds200-0.23.0-unexpected.tar.gz",
        stale_contents,
    )
    with pytest.raises(RuntimeError, match="unexpected="):
        screenshots._verify_source_distribution(unexpected)

    duplicate = write_distribution(
        "sds200-0.23.0-duplicate.tar.gz",
        source_contents,
        duplicate="docs/web-dashboard.md",
    )
    with pytest.raises(RuntimeError, match="duplicate required member"):
        screenshots._verify_source_distribution(duplicate)

    nonregular = write_distribution(
        "sds200-0.23.0-nonregular.tar.gz",
        source_contents,
        nonregular="scripts/audit_web_dashboard_browser.mjs",
    )
    with pytest.raises(RuntimeError, match="not a regular file"):
        screenshots._verify_source_distribution(nonregular)

    corrupted_contents = dict(source_contents)
    png_name = "docs/assets/web-dashboard/theme-system-1920x1080.png"
    corrupted_png = bytearray(corrupted_contents[png_name])
    corrupted_png[-1] ^= 0x01
    corrupted_contents[png_name] = bytes(corrupted_png)
    corrupted = write_distribution(
        "sds200-0.23.0-corrupted-png.tar.gz",
        corrupted_contents,
    )
    with pytest.raises(RuntimeError, match="PNG is invalid"):
        screenshots._verify_source_distribution(corrupted)

    mutated_contents = dict(source_contents)
    docs_name = "docs/web-dashboard.md"
    mutated_docs = bytearray(mutated_contents[docs_name])
    mutated_docs[0] ^= 0x01
    mutated_contents[docs_name] = bytes(mutated_docs)
    mutated = write_distribution(
        "sds200-0.23.0-mutated-docs.tar.gz",
        mutated_contents,
    )
    with pytest.raises(RuntimeError, match="does not exactly match"):
        screenshots._verify_source_distribution(mutated)
