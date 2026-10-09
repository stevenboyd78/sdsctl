"""Offline HTML development renderer for qualified Mimic-SDS adapter frames.

Not a dashboard route, installed theme or HA card. No acquisition or network
operations. Gallery interaction selects precomputed synthetic frames only.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from html import escape

from .scanner_display_adapter import (
    DisplayLayoutBasis,
    DisplayObservationStatus,
    ScannerAlertLed,
    ScannerDisplayFrame,
    ScannerDisplayIndicators,
)
from .scanner_display_layout import DisplayRegionKind, scanner_display_layout
from .scanner_display_presentation import (
    INACTIVE_COLOR,
    present_indicator,
    signal_reception_state,
)
from .scanner_display_profile_state import DisplayProfileStatus
from .scanner_display_values import ScannerDisplayValueStatus

_COLOR = re.compile(r"[0-9a-fA-F]{6}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STATES = {
    DisplayObservationStatus.CURRENT: "Current sample",
    DisplayObservationStatus.DISCONNECTED: "Disconnected",
    DisplayObservationStatus.WAITING: "Waiting for a new sample",
    DisplayObservationStatus.STALE: "Stale sample - live values cleared",
    DisplayObservationStatus.UNSUPPORTED_SCREEN: "Unsupported operating screen",
    DisplayObservationStatus.OVERRIDE: "Scanner menu / popup / replay - content not qualified",
    DisplayObservationStatus.AMBIGUOUS_RECORDS: "Conflicting scanner information",
}
_BASES = {
    DisplayLayoutBasis.UNAVAILABLE: "No qualified layout",
    DisplayLayoutBasis.DOCUMENTED_SCREEN: "Documented operating-screen family",
    DisplayLayoutBasis.PROFILE_PREFERENCE_UNCONFIRMED: (
        "Imported preference - physical layout unconfirmed"
    ),
    DisplayLayoutBasis.EXPLICIT_PRESENTATION_CHOICE: (
        "Manual presentation choice - scanner unchanged"
    ),
}
_PROFILES = {
    DisplayProfileStatus.UNAVAILABLE: "Profile unavailable",
    DisplayProfileStatus.LAST_IMPORTED: "Last imported profile - not live synchronization",
    DisplayProfileStatus.REFRESH_FAILED: (
        "Profile refresh failed - last good import retained if available"
    ),
}

# Presentation accents, not calibrated physical LED RGB measurements.
_LED_COLORS = {
    ScannerAlertLed.OFF: INACTIVE_COLOR,
    ScannerAlertLed.BLUE: "0066ff",
    ScannerAlertLed.RED: "ff2424",
    ScannerAlertLed.MAGENTA: "ff30df",
    ScannerAlertLed.GREEN: "38ed4a",
    ScannerAlertLed.CYAN: "28e5ff",
    ScannerAlertLed.YELLOW: "ffe132",
    ScannerAlertLed.WHITE: "ffffff",
}
_CAPTIONS = {
    "Volume": "VOL",
    "Squelch": "SQL",
    "Volume&Squelch": "VOL/SQL",
    "TGID": "TGID",
    "UnitId": "UID",
    "SystemId": "Sys ID",
    "SysSubID": "RFSS ID",
    "SiteId": "Site ID",
    "WACN": "WACN",
    "Rssi": "RSSI",
    "BattVoltage": "Battery",
    "Filter": "Filter",
    "Lcn": "LCN",
    "Noise": "Noise",
    "USB1_vbus": "USB1",
    "USB2_vbus": "USB2",
}


def _caption(region_id: str, token: str | None) -> str | None:
    # Only useful value prefixes, never generic System/Channel/Option A tags.
    # A/B/C fields can need labels; self-describing Frequency/ServiceType/tone
    # fields do not. The top volume/squelch fields also retain their prefixes.
    if token is None:
        return None
    if region_id.startswith(("option_a_", "option_b_", "option_c_")) or (
        region_id.startswith("option_") and token in {"Volume", "Squelch", "Volume&Squelch"}
    ):
        return _CAPTIONS.get(token)
    return None


def render_scanner_display_frame(frame: ScannerDisplayFrame) -> str:
    """Return a scoped fragment; source text is escaped, never interpreted.

    A status gate independently prevents live source values from appearing in
    stale/unsupported frames, even if a caller mistakenly supplies old values.
    A stale frame can retain semantic geometry but not current scanner text.
    """
    status = _STATES[frame.status]
    basis = _BASES[frame.layout_basis]
    profile_status = _PROFILES[frame.profile_status]
    if frame.profile_refresh_pending:
        profile_status += " (refresh pending)"
    screen = frame.screen
    if screen is not None and (
        screen.profile_revision != frame.profile_revision
        or frame.provenance is None
        or frame.profile_status is DisplayProfileStatus.UNAVAILABLE
        or screen.layout != scanner_display_layout(screen.layout.requested_mode)
        or tuple(slot.region for slot in screen.regions) != screen.layout.regions
    ):
        raise ValueError("Frame requires matching imported profile and canonical screen geometry.")
    parts = [
        f'<section class="mimic-frame" data-state="{frame.status.value}" '
        f'data-basis="{frame.layout_basis.value}">',
        f'<div class="state" role="status">{escape(status)}</div>',
        '<div class="provenance">',
        f'<div class="profile-status">{escape(profile_status)}</div>',
        f'<div class="layout-basis">{escape(basis)}</div></div>',
    ]
    if screen is None:
        if frame.values:
            raise ValueError("A frame without a screen must not contain region values.")
        message = (
            "Import a valid display profile to preview this screen."
            if (frame.profile_revision is None)
            else "No current screen content is shown in this state."
        )
        parts.extend([f'<div class="empty-screen">{escape(message)}</div>', "</section>"])
        return "\n".join(parts)
    ids = [value.region_id for value in frame.values]
    if len(ids) != len(set(ids)) or set(ids) != {slot.region.id for slot in screen.regions}:
        raise ValueError("Frame values must match the selected regions exactly once.")
    values = {value.region_id: value for value in frame.values}
    signal = values["signal"]
    reception = signal_reception_state(
        signal.status.value if frame.status is DisplayObservationStatus.CURRENT else "not_current",
        signal.text,
    )
    mode = screen.layout.requested_mode.value
    indicators = (
        frame.indicators
        if frame.status is DisplayObservationStatus.CURRENT
        else ScannerDisplayIndicators()
    )
    led = indicators.alert_led
    if not isinstance(led, ScannerAlertLed):
        led = None
    led_state = "unknown" if led is None else led.value.lower()
    led_label = "Unavailable" if led is None else led.value
    led_color = "3b4654" if led is None else _LED_COLORS[led]
    holds = {
        name: getattr(indicators, f"{name}_hold") for name in ("system", "department", "channel")
    }
    parts.append(f'<div class="screen-caption">{escape(mode.replace("_", " ").title())}</div>')
    parts.append(
        f'<div class="scanner-surround" data-led="{led_state}" style="--alert-color:#{led_color}">'
    )
    parts.append(
        f'<div class="scanner-grid" data-mode="{mode}" aria-label="Synthetic scanner regions">'
    )
    details = []
    for slot in screen.regions:
        region = slot.region
        value = values[region.id]
        state = value.status
        if frame.status is not DisplayObservationStatus.CURRENT and state not in {
            ScannerDisplayValueStatus.EMPTY,
            ScannerDisplayValueStatus.BLANK,
            ScannerDisplayValueStatus.CONFIGURATION_UNAVAILABLE,
        }:
            state = ScannerDisplayValueStatus.NOT_CURRENT
        raw = state is ScannerDisplayValueStatus.RAW_SOURCE
        text = value.text if raw and value.text is not None else "--"
        label = slot.token if slot.token not in (None, "", "Empty") else region.id.replace("_", " ")
        empty = state in {ScannerDisplayValueStatus.EMPTY, ScannerDisplayValueStatus.BLANK}
        foreground, background = "cbd5e1", "18212d"
        indicator = present_indicator(
            region.id,
            slot.token,
            slot.selection.value,
            state.value if frame.status is DisplayObservationStatus.CURRENT else "not_current",
            value.text,
            reception,
        )
        color = slot.stored_color
        held = indicators.site_hold if slot.token == "SiteName" else holds.get(region.id)
        if (
            screen.color_mode == "COLOR"
            and color is not None
            and (_COLOR.fullmatch(color.text) and _COLOR.fullmatch(color.background))
        ):
            foreground, background = color.text, color.background
            reverse = region.reverse_colors and (
                region.id != "function" or (indicator is not None and indicator.state == "on")
            )
            if reverse or held is True:
                foreground, background = background, foreground
        if indicator is not None:
            foreground = indicator.foreground or foreground
            background = indicator.background or background
            text, empty = indicator.text, not indicator.text
        classes = f"cell align-{region.alignment.value}"
        if region.kind is DisplayRegionKind.NAME:
            classes += " name-cell"
            if region.name_lines == 2:
                classes += " two-line"
        title = f"{label}: {state.value}" + (f"; raw source: {value.text}" if raw else "")
        if indicator is not None:
            title += f"; indicator: {indicator.state}"
        hold_state = "on" if held is True else "off" if held is False else "unknown"
        if region.id in holds:
            title += f"; hold: {hold_state}"
        style = (
            f"grid-area:{region.row + 1}/{region.column + 1}/"
            f"span {region.rows}/span {region.columns};"
            f"color:#{foreground};background:#{background}"
        )
        parts.append(
            f'<div class="{classes}" data-region="{region.id}" data-value-status="{state.value}" '
            f'data-hold="{hold_state}" style="{style}" '
            f'title="{escape(title, quote=True)}" aria-label="{escape(title, quote=True)}">'
        )
        if not empty and region.kind is not DisplayRegionKind.SPACER:
            caption = _caption(region.id, slot.token)
            if caption is not None:
                parts.append(f'<span class="field-label">{escape(caption)}:</span>')
            parts.append(f'<strong class="field-value">{escape(text)}</strong>')
            details.append(
                f"<tr><th>{escape(label)}</th><td>{escape(text)}</td><td>{state.value}</td></tr>"
            )
        parts.append("</div>")
    parts.extend(
        [
            "</div></div>",
            f'<p class="alert-status">Scanner alert LED: {led_label}. '
            "Reported color only; blink timing is not reproduced.</p>",
            '<p class="legend">Raw source values only; '
            "no inferred units or scanner clock. Gray indicator labels mean confirmed off; "
            "? means unknown. Signal bars use the reported level, not an RSSI conversion. "
            "-- means unavailable or unqualified. Intentionally empty slots remain blank.</p>",
            f'<details class="field-details"><summary>Field details and mapping limits '
            f"({len(screen.issues)} mapping issues)</summary>",
            "<p>Neutral colors mark unqualified mappings. BLACK/WHITE transforms and ambiguous "
            "small-field colors are not applied. Name bands invert only for a current "
            "reported hold; unknown hold state is not a confirmed release.</p>"
            "<table><thead><tr><th>Field</th><th>Value</th>"
            "<th>Qualification</th></tr></thead><tbody>",
            *details,
            "</tbody></table></details></section>",
        ]
    )
    return "\n".join(parts)


_CSS = """
* { box-sizing:border-box } :root { color-scheme:dark }
body { margin:0; padding:16px; background:#0c121c; color:#edf4fc;
font:15px system-ui,sans-serif }
main { width:100%; margin:auto } h1 { margin:0; font-size:26px }
.intro { color:#b9c7d8; margin:8px 0 14px; line-height:1.45 }
.consumer { padding:12px; border:1px solid #55687f; border-radius:10px; margin-bottom:16px }
.toolbar { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin-bottom:10px }
label { display:grid; gap:4px; min-width:0 }
select,button { font:inherit; padding:7px; max-width:100% }
button,select,summary { cursor:pointer }
:focus-visible { outline:3px solid #ffd166; outline-offset:2px }
.state { padding:8px 12px; background:#163e36; border-left:5px solid #62ddbd; font-weight:700 }
[data-state]:not([data-state=current]) .state { background:#4a252d; border-color:#ffacb4 }
.provenance { display:flex; gap:4px 24px; flex-wrap:wrap; font-size:12px;
padding:8px 0; color:#c6d2e2 }
.screen-caption { margin:0 0 6px; font-size:14px; font-weight:600 }
.scanner-surround { --alert-color:#3b4654; border-style:solid; border-color:transparent;
border-block-width:6px; border-inline-width:2ch; border-block-color:var(--alert-color);
background:#000 }
.consumer[data-led-treatment=border] .scanner-surround { border-color:var(--alert-color) }
.scanner-surround[data-led=unknown] { border-top-style:dashed; border-bottom-style:dashed }
.consumer[data-led-treatment=border] .scanner-surround[data-led=unknown] { border-style:dashed }
.alert-status { font-size:12px; color:#b9c7d8; margin:8px 0 }
.scanner-grid { display:grid; grid-template-columns:repeat(30,minmax(0,1fr));
grid-template-rows:repeat(20,minmax(0,1fr)); height:clamp(460px,54vw,620px);
background:#000; overflow:hidden }
.cell { min-width:0; min-height:0; overflow:hidden; padding:1px 4px;
display:flex; align-items:center; gap:5px; font-family:monospace }
.align-left { justify-content:flex-start; text-align:left }
.align-center { justify-content:center; text-align:center }
.field-label,.field-value { font-size:clamp(12px,1.4vw,19px); line-height:1.15;
text-overflow:ellipsis; overflow:hidden; white-space:nowrap }
.field-label { flex:0 1 auto; font-weight:bold }
.field-value { min-width:0 }
.name-cell .field-value { width:100%; font-size:clamp(24px,3.2vw,36px); font-weight:normal }
.name-cell.two-line .field-value { font-size:clamp(28px,3.6vw,40px);
display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
white-space:normal; overflow-wrap:anywhere }
.empty-screen { display:grid; place-items:center; min-height:460px;
background:#18212d; padding:24px }
.legend,.field-details { font-size:12px; line-height:1.45; color:#b9c7d8 }
table { border-collapse:collapse; table-layout:fixed; width:100%; margin-top:8px }
td,th { text-align:left; border:1px solid #425168; padding:6px; overflow-wrap:anywhere }
.compare > summary { padding:10px; margin-bottom:10px } .scenario-note { font-size:12px }
.supplemental-notes { white-space:pre-wrap; overflow-wrap:anywhere; font-size:13px;
line-height:1.5; color:#c6d2e2; padding:10px; border:1px solid #425168 }
@media(max-width:850px) { body{padding:8px} h1{font-size:21px} .consumer{padding:8px}
.scanner-grid{height:460px} .toolbar{gap:8px} select{font-size:13px} }
"""

_SCRIPT = """
for (const panel of document.querySelectorAll('.consumer')) {
  const scenario = panel.querySelector('.scenario');
  const style = panel.querySelector('.style');
  const target = panel.querySelector('.frame-target');
  const ledStyle = panel.querySelector('.led-style');
  ledStyle.addEventListener('change', () => { panel.dataset.ledTreatment = ledStyle.value; });
  function show() {
    const template = document.getElementById('frame-' + scenario.value + '-' + style.value);
    if (!template) throw new Error('Missing synthetic preview variant');
    target.replaceChildren(template.content.cloneNode(true));
  }
  scenario.addEventListener('change', show);
  style.addEventListener('change', show);
  panel.querySelector('.next').addEventListener('click', () => {
    scenario.selectedIndex = (scenario.selectedIndex + 1) % scenario.options.length;
    show();
  });
  show();
}
document.documentElement.dataset.previewReady = 'true';
"""


def render_scanner_display_gallery(
    scenarios: Mapping[str, Mapping[str, ScannerDisplayFrame]],
    *,
    notes: Mapping[str, str] | None = None,
) -> str:
    """Development document with independent panels and no live connections.

    Each scenario must contain profile/simple/detail variants computed by the
    adapter. Template cloning changes presentation only; CSP blocks networking.
    Labels and keys are bounded developer identifiers, not profile text or URLs.
    Optional scenario notes are bounded, escaped plain text outside the LCD.
    """
    if not 1 <= len(scenarios) <= 64 or any(not _KEY.fullmatch(key) for key in scenarios):
        raise ValueError("Expected bounded synthetic scenario identifiers.")
    if notes is not None and (
        set(notes) - set(scenarios)
        or any(type(note) is not str or len(note) > 20000 for note in notes.values())
    ):
        raise ValueError("Expected bounded plain-text scenario notes.")
    templates = []
    for key, variants in scenarios.items():
        if set(variants) != {"profile", "simple", "detail"}:
            raise ValueError("Each scenario requires the three presentation variants.")
        for style, frame in variants.items():
            note = "" if notes is None else notes.get(key, "")
            templates.append(
                f'<template id="frame-{key}-{style}">'
                f"{render_scanner_display_frame(frame)}"
                + (f'<pre class="supplemental-notes">{escape(note)}</pre>' if note else "")
                + "</template>"
            )
    options = "".join(
        f'<option value="{key}">{escape(key.replace("_", " ").title())}</option>'
        for key in scenarios
    )
    panels = []
    for index in (1, 2):
        panels.append(
            f'<section class="consumer" id="consumer-{index}" data-led-treatment="strips" '
            f'aria-label="Preview display {index}">'
            '<div class="toolbar"><label>Sample scenario<select class="scenario">'
            + options
            + '</select></label><label>Simple / Detail presentation<select class="style">'
            '<option value="profile">Use imported preference</option>'
            '<option value="simple">Simple</option>'
            '<option value="detail">Detail</option></select></label>'
            '<label>LED treatment<select class="led-style">'
            '<option value="strips">Light strips</option><option value="border">Border</option>'
            "</select></label>"
            '<button class="next" type="button">'
            'Next scenario</button></div><div class="frame-target"></div></section>'
        )
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    policy = (
        f"default-src 'none'; script-src 'sha256-{script_hash}'; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    )
    return "\n".join(
        [
            '<!doctype html><html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            f'<meta http-equiv="Content-Security-Policy" content="{escape(policy, quote=True)}">',
            "<title>Mimic-SDS offline frame preview</title>",
            f"<style>{_CSS}</style></head><body><main>",
            "<h1>Mimic-SDS / offline frame preview</h1>",
            '<p class="intro">Invented samples, no scanner connection. '
            "This is a development preview, not an installed theme "
            "or a claim of LCD-format parity. "
            "Controls below select sample frames only. Scanner field widths are a baseline, "
            "not character caps; the layout expands with the available display width.</p>",
            "<noscript>Enable JavaScript to select these offline synthetic frames.</noscript>",
            panels[0],
            '<details class="compare"><summary>Compare a second independent display</summary>',
            panels[1],
            "</details>",
            *templates,
            f"<script>{_SCRIPT}</script>",
            "</main></body></html>",
        ]
    )
