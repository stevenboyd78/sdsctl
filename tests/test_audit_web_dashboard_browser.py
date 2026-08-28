from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path("scripts/audit_web_dashboard_browser.mjs")
_CAPTURE_SCRIPT = Path("scripts/capture_web_dashboard_screenshot.mjs")
_NODE = shutil.which("node")


def _run_node(*arguments: str) -> subprocess.CompletedProcess[str]:
    if _NODE is None:
        pytest.skip("Node.js is unavailable")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [_NODE, str(_SCRIPT), *arguments],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=10.0,
    )


def _run_capture_node(*arguments: str) -> subprocess.CompletedProcess[str]:
    if _NODE is None:
        pytest.skip("Node.js is unavailable")
    return subprocess.run(
        [_NODE, str(_CAPTURE_SCRIPT), *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=10.0,
    )


def test_browser_audit_help_documents_scope_and_dependencies() -> None:
    completed = _run_node("--help")

    assert completed.returncode == 0
    assert completed.stderr == ""
    for contract in (
        "all 144 built-in",
        "writes no PNGs",
        "enlarged-text",
        "pagination-focus",
        "trusted Tab/Shift+Tab",
        "WCAG AA",
        "adaptive-presentation",
        "DPR-transition",
        "prefixed-URL",
        "Node.js 24 or newer",
        "No third-party Node package",
    ):
        assert contract in completed.stdout


def test_browser_audit_list_is_the_complete_cartesian_matrix() -> None:
    completed = _run_node("--list")

    assert completed.returncode == 0
    assert completed.stderr == ""
    lines = completed.stdout.splitlines()
    cases = lines[:-1]
    assert len(cases) == 144
    assert lines[-1] == "Matrix cases: 144; screenshots written: 0"

    themes = {
        "system",
        "lcars",
        "matrix",
        "first-responder",
        "amateur-radio",
        "pip-boy-inspired",
    }
    viewports = {
        "viewport=1920x1080@1",
        "viewport=1366x768@1",
        "viewport=800x480@1",
        "viewport=390x844@2",
    }
    panes = {
        "pane=scanner",
        "pane=controls",
        "pane=waterfall",
        "pane=audio",
        "pane=recordings",
        "pane=diagnostics",
    }
    assert {
        token.removeprefix("theme=")
        for line in cases
        for token in line.split()
        if token.startswith("theme=")
    } == themes
    assert {
        token for line in cases for token in line.split() if token.startswith("viewport=")
    } == viewports
    assert {token for line in cases for token in line.split() if token.startswith("pane=")} == panes
    assert len(set(cases)) == 144


def test_browser_audit_rejects_invalid_arguments_without_opening_chrome() -> None:
    completed = _run_node("--timeout-ms", "0")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "--timeout-ms must be a positive integer" in completed.stderr
    assert "Use --help for usage." in completed.stderr


def test_browser_audit_source_preserves_browser_specific_acceptance_guards() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")

    for contract in (
        "semanticInteractiveSelector",
        "control.tabIndex < 0",
        "auditTrustedTabDirection",
        "modifiers: reverse ? 8 : 0",
        "minimumTextContrast = 4.5",
        "minimumLargeTextContrast = 3",
        "presentationGeometryFailures",
        "hierarchyFieldAlignmentFailures",
        '["simple", "scanning", "scanning", "Now scanning", "simple", "none"]',
        '["detail", "scanning", "scanning", "Now scanning", "detail", "hierarchy"]',
        "`${theme}/forced-colors/${pane}`",
    ):
        assert contract in source


def test_internal_capture_bridge_documents_and_enforces_exact_cdp_viewports() -> None:
    completed = _run_capture_node("--help")

    assert completed.returncode == 0
    assert completed.stderr == ""
    for contract in (
        "Node.js 24 or newer",
        "exact CSS width, height",
        "Chrome outer-window sizing",
        "Same-directory staging PNG path",
        "authoritative outerHTML",
        "No third-party Node package",
    ):
        assert contract in completed.stdout

    helper_source = _CAPTURE_SCRIPT.read_text(encoding="utf-8")
    audit_source = _SCRIPT.read_text(encoding="utf-8")
    assert "captureDashboardScreenshot" in helper_source
    for contract in (
        "export async function captureDashboardScreenshot",
        'cdp.send("Page.addScriptToEvaluateOnNewDocument"',
        'Object.defineProperty(globalThis, "EventSource"',
        "__sdsctlScreenshotEventSource",
        "__sdsctlScreenshotMessageStability",
        "waitForWaterfallCanvasStability",
        'context.getImageData(0, 0, canvas.width, canvas.height).data',
        'crypto.subtle.digest("SHA-256", pixels)',
        "captureStableScreenshot",
        'cdp.send("Emulation.setDeviceMetricsOverride"',
        'cdp.send("Emulation.setEmulatedMedia"',
        '{name: "prefers-color-scheme", value: "light"}',
        '{name: "forced-colors", value: "none"}',
        '{name: "prefers-contrast", value: "no-preference"}',
        'cdp.send("Page.captureScreenshot"',
        "captureBeyondViewport: false",
        "document.documentElement.outerHTML",
        "outerHTML !== finalOuterHTML",
        "screenshotAttempts: screenshot.attempts",
        "waterfallCanvas",
        'style.transform = "translateZ(0)"',
    ):
        assert contract in audit_source
    assert audit_source.index(
        'cdp.send("Page.addScriptToEvaluateOnNewDocument"'
    ) < audit_source.index("await navigate(cdp, captureUrl")


def test_internal_capture_bridge_rejects_bad_arguments_without_opening_chrome() -> None:
    completed = _run_capture_node("--not-an-option")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "unknown argument: --not-an-option" in completed.stderr


def test_browser_audit_is_a_ci_and_release_gate() -> None:
    command = "node scripts/audit_web_dashboard_browser.mjs --timeout-ms 30000"
    setup_node = "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"

    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert command in ci
    assert command in release
    assert setup_node in ci
    assert setup_node in release
    assert 'node-version: "24"' in ci
    assert 'node-version: "24"' in release
    assert command in Path("docs/releasing.md").read_text(encoding="utf-8")
