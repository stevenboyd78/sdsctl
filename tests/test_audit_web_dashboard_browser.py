from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path("scripts/audit_web_dashboard_browser.mjs")
_CAPTURE_SCRIPT = Path("scripts/capture_web_dashboard_screenshot.mjs")
_NODE = shutil.which("node")


def test_browser_startup_diagnostics_contract() -> None:
    if _NODE is None:
        pytest.skip("Node.js is unavailable")
    completed = subprocess.run(
        [_NODE, "--test", "scripts/test_browser_audit_startup.mjs"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""


def test_browser_palette_focus_probe_contract() -> None:
    if _NODE is None:
        pytest.skip("Node.js is unavailable")
    completed = subprocess.run(
        [_NODE, "--test", "scripts/test_browser_audit_focus.mjs"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""


def test_browser_trusted_click_geometry_contract() -> None:
    if _NODE is None:
        pytest.skip("Node.js is unavailable")
    completed = subprocess.run(
        [_NODE, "--test", "scripts/test_browser_audit_click.mjs"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""


def test_browser_lcars_reachability_contract() -> None:
    if _NODE is None:
        pytest.skip("Node.js is unavailable")
    completed = subprocess.run(
        [_NODE, "--test", "scripts/test_browser_audit_lcars.mjs"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""


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
        "all 840 built-in",
        "30 exact panoramic-boundary",
        "writes no PNGs",
        "enlarged-text",
        "pagination-focus",
        "trusted Tab/Shift+Tab",
        "WCAG AA",
        "adaptive-presentation",
        "operator and display-only Display & session drawer",
        "LCARS navigation breakpoint",
        "alternative system-font metrics",
        "DPR-transition",
        "prefixed-URL",
        "Ingress Diagnostics layout",
        "authenticated waterfall card",
        "elapsed-time history",
        "keyboard frequency inspection",
        "two live cards",
        "final-stream cleanup",
        "--waterfall-only",
        "--waterfall-screenshot-dir",
        "--findings-json",
        "--startup-timeout-ms",
        "three sanitized waterfall-card reference PNGs",
        "Node.js 24 or newer",
        "No third-party Node package",
    ):
        assert contract in completed.stdout

    source = _SCRIPT.read_text(encoding="utf-8")
    assert "Object.freeze([703, 704, 705])" in source
    assert "navigation-transition/scanner" in source


def test_browser_audit_list_is_the_complete_cartesian_matrix() -> None:
    completed = _run_node("--list")

    assert completed.returncode == 0
    assert completed.stderr == ""
    lines = completed.stdout.splitlines()
    cases = lines[:-1]
    assert len(cases) == 840
    assert lines[-1] == "Matrix cases: 840; screenshots written: 0"

    themes = {
        "system",
        "lcars",
        "matrix",
        "first-responder",
        "amateur-radio",
        "pip-boy-inspired",
        "cinematic-mission-control",
        "cinematic-cyberpunk-night",
        "cinematic-synthwave",
        "cinematic-cold-war-radar",
        "cinematic-dieselpunk-command",
        "cinematic-steampunk-telegraph",
        "cinematic-retro-arcade",
        "cinematic-y2k-media",
        "cinematic-mecha-operations",
        "cinematic-comic-book",
        "cinematic-wizarding-archive",
        "cinematic-gothic-cathedral",
        "cinematic-fantasy-cartographer",
        "cinematic-desert-empire",
        "cinematic-carrier-air-wing",
        "cinematic-motorsport-pit-wall",
        "cinematic-noir-detective",
        "cinematic-art-deco-radio",
        "cinematic-solarpunk",
        "cinematic-national-parks",
        "cinematic-galactic-operations",
        "cinematic-tactical-field",
    }
    viewports = {
        "viewport=1920x1080@1",
        "viewport=1920x900@1",
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
    assert len(set(cases)) == 840


def test_browser_audit_covers_both_authenticated_native_drawer_roles() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    demo = Path("scripts/generate_web_dashboard_screenshots.py").read_text(
        encoding="utf-8",
    )
    operator_source = source.split("async function auditOperatorDrawer", 1)[1].split(
        "export async function openChrome", 1
    )[0]

    assert "async function auditNativeDrawer" in source
    assert "async function auditOperatorDrawer" in source
    assert "async function auditDisplayKiosk" in source
    assert "role: 'operator'" in source
    assert "role: 'display'" in source
    assert "baseUrl + '/?kiosk=operator'" in source
    assert 'baseUrl + "/?kiosk=display"' in source
    assert "const OPERATOR_DRAWER_VIEWPORTS = Object.freeze([" in source
    assert 'Object.freeze({name: "zoomed", width: 400, height: 240, dpr: 1})' in source
    assert "for (const viewport of OPERATOR_DRAWER_VIEWPORTS)" in operator_source
    assert "overlay must not reflow workspace" in source
    assert "viewport containment, internal scrolling and 44px controls" in source
    assert "inside-to-outside drag must not dismiss" in source
    assert "Sign out reachability" in source
    assert "alertContract" in source
    assert "staleLogoutDisabled" in source
    assert "requestsAtExpiry !== state.requestsAfter" in source
    assert source.index("await auditOperatorDrawer") < source.index("await auditDisplayKiosk")
    assert 'b"kiosk=operator": b"operator"' in demo
    assert 'b"kiosk=display": b"display"' in demo


def test_browser_audit_persists_each_applicable_appearance_control() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    drawer_source = source.split("async function auditNativeDrawer", 1)[1].split(
        "async function auditDisplayNavigation", 1
    )[0]
    display_navigation = source.split("async function auditDisplayNavigation", 1)[1].split(
        "async function auditDisplayKiosk", 1
    )[0]
    open_theme_drawer = display_navigation.split("for (const theme of THEMES)", 1)[1].split(
        "await clickElement(cdp, '[data-kiosk-pane=\"waterfall\"]')", 1
    )[0]
    unique_ids = drawer_source.split("const unique = [", 1)[1].split("];", 1)[0]

    assert "const appearanceScenarios = [" in drawer_source
    for theme, control, dataset in (
        ("system", "system-palette-select", "systemPalette"),
        ("lcars", "lcars-v2-palette-select", "lcarsV2Palette"),
        ("lcars", "lcars-v2-type-select", "lcarsV2Type"),
        ("matrix", "theme-font-select", "themeFont"),
        ("matrix", "theme-typography-select", "themeTypography"),
    ):
        assert f"theme: '{theme}'" in drawer_source
        assert f"id: '{control}', dataset: '{dataset}'" in drawer_source
        assert f"'{control}'" in unique_ids
    assert "appearance persistence across close/reopen" in drawer_source
    assert "appearance persistence across viewport resize" in drawer_source
    assert "await setViewport(cdp, {width: 1366, height: 768, dpr: 1})" in drawer_source
    assert "await setViewport(cdp, {width: 800, height: 480, dpr: 1})" in drawer_source
    assert "document.querySelectorAll('#' + id)" in drawer_source
    assert "for (const appearance of ['light', 'dark', 'forced'])" in open_theme_drawer
    assert "document.querySelector('#native-menu')" in open_theme_drawer
    assert "Compact Display & session drawer" in open_theme_drawer
    assert "drawerFailures.length" in open_theme_drawer


def test_browser_audit_rejects_invalid_arguments_without_opening_chrome() -> None:
    for argument in ("--timeout-ms", "--startup-timeout-ms"):
        completed = _run_node(argument, "0")

        assert completed.returncode == 2
        assert completed.stdout == ""
        assert f"{argument} must be a positive integer" in completed.stderr
        assert "Use --help for usage." in completed.stderr


def test_browser_audit_source_preserves_browser_specific_acceptance_guards() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "non-System live daemon status wraps onto multiple lines" not in source
    click_source = source.split("async function clickElement", 1)[1].split(
        "async function movePointerAway", 1
    )[0]
    cleanup_source = source.split("async function cleanupTrustedClickWitness", 1)[1].split(
        "export async function clickElement", 1
    )[0]
    assert "const TRUSTED_CLICK_MAX_ATTEMPTS = 3" in source
    assert "const TRUSTED_CLICK_MAX_POINTER_MOVES = 3" in source
    assert "elementFromPoint" in click_source
    assert "owned:" in click_source
    assert "attempt <= TRUSTED_CLICK_MAX_ATTEMPTS" in click_source
    assert "pointerMove <= TRUSTED_CLICK_MAX_POINTER_MOVES" in click_source
    assert "after ${TRUSTED_CLICK_MAX_ATTEMPTS} settled attempts" in click_source
    assert "candidate.focused && candidate.owned && candidate.hovered" in click_source
    assert "confirmed.focused && confirmed.owned && confirmed.hovered" in click_source
    assert "sameClickGeometry(candidate, confirmed)" in click_source
    assert "candidate = confirmed" in click_source
    assert "stabilizedPointOwned" in click_source
    assert "dispatchPointOwned" in click_source
    assert "armedPoint.stabilizedPointOwned && armedPoint.witnessCurrent" in click_source
    assert (
        "dispatchPoint.hovered && dispatchPoint.owned && "
        "dispatchPoint.witnessCurrent" in click_source
    )
    assert "sameClickGeometry(point, armedPoint)" in click_source
    assert "sameClickGeometry(armedPoint, dispatchPoint)" in click_source
    assert "trusted-click-witness cleanup before geometry reacquisition or exit" in cleanup_source
    assert "record?.token !== ${witnessToken}" in cleanup_source
    assert "delete globalThis[key]" in cleanup_source
    assert "return {clicked: record.clicked, matched: true}" in cleanup_source
    assert "preserveTrustedClickError" in source
    assert "primary.trustedClickSuppressed" in source
    assert "let attemptAccepted = false" in click_source
    assert "let attemptError = null" in click_source
    assert "cleanupTrustedClickWitness(cdp, attemptWitnessToken)" in click_source
    assert "cleanupTrustedClickWitness(cdp, witnessToken)" in click_source
    assert 'preserveTrustedClickError(attemptError, error, "pre-dispatch")' in click_source
    assert 'preserveTrustedClickError(attemptError, error, "pre-dispatch-cleanup")' in click_source
    assert 'preserveTrustedClickError(dispatchError, error, "press-receipt")' in click_source
    assert 'preserveTrustedClickError(dispatchError, error, "mouse-release")' in click_source
    assert (
        'preserveTrustedClickError(dispatchError, error, "post-dispatch-cleanup")' in click_source
    )
    assert "if (dispatchError !== null) throw dispatchError" in click_source
    assert "element.focus({preventScroll: false})" in click_source
    assert "element.focus({preventScroll: true})" not in click_source
    assert "document.activeElement === element" in click_source
    assert 'element.matches(":active")' in click_source
    assert "pressedPointOwned" in click_source
    assert "centerOwned" in click_source
    assert "clickWitness?.isTrusted" in click_source
    assert "clickWitness?.owned" in click_source
    assert "TRUSTED_CLICK_SAFE_RELEASE_POINT" in click_source
    assert "let releasePoint = TRUSTED_CLICK_SAFE_RELEASE_POINT" in click_source
    assert "if (pressValid) releasePoint = pressReceipt" in click_source
    assert 'button: pressValid ? "left" : "none"' in click_source
    assert "clickCount: pressValid ? 1 : 0" in click_source
    assert click_source.count('type: "mousePressed"') == 1
    assert click_source.count('type: "mouseReleased"') == 1
    acquisition_source = click_source.split("if (\n    point === null", 1)[0]
    assert 'type: "mousePressed"' not in acquisition_source
    assert 'type: "mouseReleased"' not in acquisition_source
    click_scroll_index = click_source.index("element.scrollIntoView")
    click_focus_index = click_source.index("element.focus", click_scroll_index)
    click_focus_verified_index = click_source.index(
        "document.activeElement === element", click_focus_index
    )
    click_settle_index = click_source.index("await frames(cdp)", click_scroll_index)
    click_initial_measure_index = click_source.index("const rect = element.getBoundingClientRect()")
    click_initial_hit_index = click_source.index("document.elementFromPoint")
    click_move_index = click_source.index('type: "mouseMoved"')
    click_hover_settle_index = click_source.index("await frames(cdp)", click_move_index)
    click_confirm_index = click_source.index("const confirmed = await evaluate")
    click_confirm_measure_index = click_source.index(
        "const rect = element.getBoundingClientRect()", click_confirm_index
    )
    click_confirm_hit_index = click_source.index("document.elementFromPoint", click_confirm_index)
    click_confirm_hover_index = click_source.index(
        'hovered: element.matches(":hover")', click_confirm_index
    )
    click_convergence_index = click_source.index(
        "sameClickGeometry(candidate, confirmed)", click_confirm_hover_index
    )
    click_witness_arm_index = click_source.index("const witnessArmed", click_convergence_index)
    click_post_arm_settle_index = click_source.index("await frames(cdp)", click_witness_arm_index)
    click_armed_point_index = click_source.index(
        "const armedPoint = await evaluate", click_post_arm_settle_index
    )
    click_post_arm_owner_index = click_source.index("stabilizedPointOwned", click_armed_point_index)
    click_post_arm_witness_index = click_source.index(
        "armedPoint.witnessCurrent", click_post_arm_owner_index
    )
    click_post_arm_stability_index = click_source.index(
        "sameClickGeometry(point, armedPoint)", click_post_arm_witness_index
    )
    click_final_move_index = click_source.index(
        'type: "mouseMoved"', click_post_arm_stability_index
    )
    click_final_settle_index = click_source.index("await frames(cdp)", click_final_move_index)
    click_dispatch_point_index = click_source.index(
        "const dispatchPoint = await evaluate", click_final_settle_index
    )
    click_dispatch_owner_index = click_source.index(
        "dispatchPointOwned", click_dispatch_point_index
    )
    click_dispatch_focus_index = click_source.index(
        "dispatchPoint.focused", click_dispatch_owner_index
    )
    click_dispatch_witness_index = click_source.index(
        "dispatchPoint.witnessCurrent", click_dispatch_focus_index
    )
    click_dispatch_stability_index = click_source.index(
        "sameClickGeometry(armedPoint, dispatchPoint)", click_dispatch_witness_index
    )
    click_attempt_accepted_index = click_source.index(
        "attemptAccepted = true", click_dispatch_stability_index
    )
    click_pre_dispatch_finally_index = click_source.index(
        "} finally {", click_attempt_accepted_index
    )
    click_disarm_index = click_source.index(
        "cleanupTrustedClickWitness(cdp, attemptWitnessToken)",
        click_pre_dispatch_finally_index,
    )
    click_attempt_error_index = click_source.index(
        "if (attemptError !== null) throw attemptError", click_disarm_index
    )
    click_exhaustion_index = click_source.index(
        "after ${TRUSTED_CLICK_MAX_ATTEMPTS} settled attempts"
    )
    click_dispatch_index = click_source.index('type: "mousePressed"')
    click_press_receipt_index = click_source.index(
        "pressReceipt = await evaluate", click_dispatch_index
    )
    click_release_finally_index = click_source.index("} finally {", click_press_receipt_index)
    click_release_index = click_source.index('type: "mouseReleased"', click_release_finally_index)
    click_post_dispatch_index = click_source.index("await frames(cdp)", click_release_index)
    click_cleanup_finally_index = click_source.index("} finally {", click_post_dispatch_index)
    click_witness_index = click_source.index(
        "cleanupTrustedClickWitness(cdp, witnessToken)", click_cleanup_finally_index
    )
    click_dispatch_error_index = click_source.index(
        "if (dispatchError !== null) throw dispatchError", click_witness_index
    )
    click_witness_validation_index = click_source.index(
        "!pressValid || !clickWitness?.isTrusted", click_dispatch_error_index
    )
    assert (
        click_scroll_index
        < click_focus_index
        < click_focus_verified_index
        < click_settle_index
        < click_initial_measure_index
        < click_initial_hit_index
        < click_move_index
        < click_hover_settle_index
        < click_confirm_index
        < click_confirm_measure_index
        < click_confirm_hit_index
        < click_confirm_hover_index
        < click_convergence_index
        < click_witness_arm_index
        < click_post_arm_settle_index
        < click_armed_point_index
        < click_post_arm_owner_index
        < click_post_arm_witness_index
        < click_post_arm_stability_index
        < click_final_move_index
        < click_final_settle_index
        < click_dispatch_point_index
        < click_dispatch_owner_index
        < click_dispatch_focus_index
        < click_dispatch_witness_index
        < click_dispatch_stability_index
        < click_attempt_accepted_index
        < click_pre_dispatch_finally_index
        < click_disarm_index
        < click_attempt_error_index
        < click_exhaustion_index
        < click_dispatch_index
        < click_press_receipt_index
        < click_release_finally_index
        < click_release_index
        < click_post_dispatch_index
        < click_cleanup_finally_index
        < click_witness_index
        < click_dispatch_error_index
        < click_witness_validation_index
    )

    hover_source = source.split("async function hoverElement", 1)[1].split(
        "async function activatePane", 1
    )[0]
    assert "elementFromPoint" in hover_source
    assert "owned:" in hover_source
    assert "occluded before trusted hover dispatch" in hover_source
    assert "for (let attempt = 1; attempt <= 2; attempt += 1)" in hover_source
    assert "after 2 settled attempts" in hover_source
    scroll_index = hover_source.index("element.scrollIntoView")
    pre_dispatch_settle_index = hover_source.index("await frames(cdp)", scroll_index)
    measure_index = hover_source.index("const rect = element.getBoundingClientRect()")
    dispatch_index = hover_source.index('cdp.send("Input.dispatchMouseEvent"')
    post_dispatch_settle_index = hover_source.index("await frames(cdp)", dispatch_index)
    confirmation_index = hover_source.index('hovered: element.matches(":hover")')
    assert (
        scroll_index
        < pre_dispatch_settle_index
        < measure_index
        < dispatch_index
        < post_dispatch_settle_index
        < confirmation_index
    )

    for contract in (
        "semanticInteractiveSelector",
        "control.tabIndex < 0",
        "auditTrustedTabDirection",
        "auditPhoneRecordingFonts",
        "phone-recording-font",
        "modifiers: reverse ? 8 : 0",
        "minimumTextContrast = 4.5",
        "minimumLargeTextContrast = 3",
        "auditWorkspaceTabHoverContrast",
        "hoverReadability",
        "internal hover contrast count mismatch",
        "explicit workspace-tab hover states",
        "phoneUnknownHoldState",
        "unknown-hold-state",
        "internal phone unknown hold-state count mismatch",
        "controlContext",
        "ingressDiagnosticsLayout",
        "Scanner panel does not fill the Diagnostics workspace",
        "internal Ingress Diagnostics count mismatch",
        "ingressHomeAssistantLayout",
        "Home Assistant integration panel does not fill its workspace",
        "Advanced Home Assistant panel does not fill its workspace",
        "subpanelButtonGeometry",
        "const expectedColumns = centered ? 3 : 2",
        "(contentLeft + contentRight)",
        "desktop typography controls are not centered in their own non-overlapping column",
        "Hidden typography control is rendered",
        "Canonical appearance controls left the native drawer",
        "viewport containment, internal scrolling and 44px controls",
        "export async function runMatrix",
        "Advanced Home Assistant guidance is unreachable at the end of the workspace",
        "System palette controller, document, and selector disagree",
        "System palette selector is exposed for ${expectedTheme}",
        "non-System live daemon status has no readable geometry",
        "non-System live daemon status is outside the overview content box",
        "non-System live daemon status text is clipped by its own box",
        "non-System live daemon status overlaps the appearance controls",
        "switchSystemPalette",
        "systemPaletteChoices.slice(1)",
        "responsive System-palette cases",
        "internal Ingress Home Assistant count mismatch",
        "auditHeroPanoramaBoundaries",
        "panoramic-boundary count mismatch",
        "(width < 1280px), (height < 720px), (aspect-ratio < 3/2)",
        "current ${scope} hold state",
        "presentationGeometryFailures",
        "shellGeometryFailures",
        "responsive shell target",
        "120 * rootFontSize",
        "hierarchyFieldAlignmentFailures",
        "compact Channel field has no separation before Site",
        "radioFieldCardIntersectionFailures",
        "overlap by",
        "headline panoramic scene is not rendered under reduced motion",
        "const reducedMotionViewport = hero",
        "? {width: 1920, height: 1080, dpr: 1}",
        '["simple", "scanning", "scanning", "Now scanning", "simple", "none"]',
        '["detail", "scanning", "scanning", "Now scanning", "detail", "hierarchy"]',
        "`${theme}/forced-colors/${pane}`",
        "auditHomeAssistantWaterfallCard",
        "auditHomeAssistantWaterfallSizing",
        "Waterfall height regression",
        "samples.length < 32",
        "auditWebWaterfallHistoryAndPointer",
        "assertHomeAssistantWaterfallGeometry",
        'history_mode: "duration"',
        "show_pointer: true",
        'pointerType: "touch"',
        'card.status !== "live" || card.history > 0',
        "state.fixture.sessionCreates !== 1",
        "state.fixture.streamsAborted !== state.fixture.streamsStarted",
        "canvas.width > 2048",
        "canvas.height > 1024",
    ):
        assert contract in source


def test_phone_unknown_hold_audit_reuses_the_existing_controls_activation() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    run_matrix = source.split("export async function runMatrix", 1)[1].split(
        "export async function openChrome", 1
    )[0]
    controls_branch = run_matrix.split('} else if (pane === "controls") {', 1)[1].split(
        '} else if (pane === "recordings") {', 1
    )[0]

    assert 'if (viewport.name === "phone") {' in controls_branch
    assert "phoneUnknownHoldCases += 1" in controls_branch
    assert "window.__sdsctlBrowserAudit.phoneUnknownHoldState()" in controls_branch
    assert "`${context}/unknown-hold-state`" in controls_branch
    assert 'await activatePane(cdp, "controls")' not in run_matrix


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
        "context.getImageData(0, 0, canvas.width, canvas.height).data",
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


def test_internal_capture_bridge_hashes_post_capture_computed_font_state() -> None:
    audit_source = _SCRIPT.read_text(encoding="utf-8")
    for contract in (
        "fingerprintComputedFonts",
        'document.querySelectorAll("*")',
        "element.getClientRects().length === 0",
        "getComputedStyle(element)",
        "computedProperties.map((property) => style[property])",
        "[...document.fonts]",
        "fontSetStatus: document.fonts.status",
        "faces.sort((left, right) => {",
        'createHash("sha256")',
        'digest("hex")',
        "computedFontSha256",
    ):
        assert contract in audit_source

    fingerprint_start = audit_source.index("async function fingerprintComputedFonts(cdp)")
    fingerprint_end = audit_source.index("async function waitForDashboard", fingerprint_start)
    fingerprint_source = audit_source[fingerprint_start:fingerprint_end]
    assert fingerprint_source.index('document.querySelectorAll("*")') < fingerprint_source.index(
        "rendered.push("
    )
    assert "rendered.sort" not in fingerprint_source
    assert fingerprint_source.index("const faces = [...document.fonts]") < fingerprint_source.index(
        "faces.sort("
    )
    assert fingerprint_source.index("faces.sort(") < fingerprint_source.index(
        "fontSetStatus: document.fonts.status"
    )

    capture_start = audit_source.index("export async function captureDashboardScreenshot")
    capture_end = audit_source.index("\nasync function run(", capture_start)
    capture_source = audit_source[capture_start:capture_end]
    screenshot_index = capture_source.index(
        "const screenshot = await captureStableScreenshot(cdp);"
    )
    final_dom_index = capture_source.index(
        'const finalOuterHTML = await evaluate(cdp, "document.documentElement.outerHTML");'
    )
    font_fingerprint_index = capture_source.index(
        "const computedFontSha256 = await fingerprintComputedFonts(cdp);"
    )
    png_conversion_index = capture_source.index(
        'const png = Buffer.from(screenshot.data, "base64");'
    )
    assert screenshot_index < final_dom_index
    dom_stability_index = capture_source.index("outerHTML !== finalOuterHTML")
    assert final_dom_index < dom_stability_index
    assert dom_stability_index < font_fingerprint_index
    assert font_fingerprint_index < png_conversion_index

    capture_result_start = capture_source.index("return {", font_fingerprint_index)
    capture_result_end = capture_source.index("};", capture_result_start)
    capture_result = capture_source[capture_result_start:capture_result_end]
    assert "computedFontSha256" in capture_result
    assert "inventory" not in capture_result


def test_internal_capture_bridge_stabilizes_native_select_text_only_for_capture() -> None:
    audit_source = _SCRIPT.read_text(encoding="utf-8")
    capture_start = audit_source.index("export async function captureDashboardScreenshot")
    capture_end = audit_source.index("\nasync function run(", capture_start)
    capture_source = audit_source[capture_start:capture_end]

    smoothing = (
        'document.querySelector("#waterfall-history-policy")\n'
        '            .style.setProperty("-webkit-font-smoothing", "antialiased");'
    )
    assert capture_source.count(smoothing) == 1
    waterfall_branch = capture_source.index('if (pane === "waterfall") {')
    smoothing_index = capture_source.index(smoothing)
    canvas_stability = capture_source.index(
        "waterfallCanvas = await waitForWaterfallCanvasStability(cdp, timeoutMs);"
    )
    screenshot = capture_source.index("const screenshot = await captureStableScreenshot(cdp);")
    assert waterfall_branch < smoothing_index < canvas_stability < screenshot
    assert smoothing not in Path("src/sds200/web_assets/dashboard.js").read_text(encoding="utf-8")
    assert smoothing not in Path("src/sds200/web_assets/dashboard.css").read_text(encoding="utf-8")


def test_internal_capture_bridge_rejects_bad_arguments_without_opening_chrome() -> None:
    completed = _run_capture_node("--not-an-option")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "unknown argument: --not-an-option" in completed.stderr


def test_browser_audit_is_a_ci_and_release_gate() -> None:
    command = (
        "node scripts/audit_web_dashboard_browser.mjs --startup-timeout-ms 90000 --timeout-ms 30000"
    )
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


def test_browser_audit_keeps_chrome_startup_separate_from_operation_timeout() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")

    assert "startupTimeoutMs: DEFAULT_STARTUP_TIMEOUT_MS" in source
    assert "pageWebSocketUrl(\n        remotePort,\n        options.startupTimeoutMs," in source
    assert "CdpClient.connect(websocketUrl, options.timeoutMs)" in source
