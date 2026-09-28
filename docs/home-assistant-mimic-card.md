# Mimic-SDS Home Assistant card (unreleased candidate)

This is **local candidate functionality**, not part of the published 0.30.0
installation instructions. It still needs a paired candidate App deployment and
comparison with the user's scanner in Home Assistant. Do not add candidate App
options to an older published image or replace working dashboard resources with
unreviewed development files.

Mimic-SDS is an **additional, read-only card**, not a replacement for SDS200
Scanner, SDS200 Display or SDS200 Waterfall. It reconstructs the scanner's
documented layout using its imported display profile and existing live PSI data;
it is not a screenshot of the LCD. The two existing MQTT/entity cards do not
change. No optional Core integration, second scanner connection, new port,
dashboard password or MQTT entity is required by the new card.

## Before adding the card

Use a candidate App built with the same frame API and card assets. An
administrator must configure the candidate's optional display-profile source,
initialize its accepted state, and import the scanner's `profile.cfg` using the
[profile administration workflow](scanner-display-profile-import.md).
The card cannot upload a profile, choose a filesystem path or edit scanner
settings. All cards use the accepted profile for that App's scanner endpoint.

The App installs `sds200-mimic-card.js` with the three existing card modules and
the `sds200-cards.js` aggregate. Register the candidate aggregate's **complete
digest-qualified resource URL** as a JavaScript Module. The reviewed deployment
must supply that URL from the packaged manifest/aggregate bytes; do not reuse
the older published aggregate digest. Individual resources remain supported,
but update Waterfall and Mimic together. Reload every open dashboard tab after
updating: an already-running older Waterfall class cannot adopt the new shared
session manager merely because a second module was loaded beside it.

Home Assistant must provide an authenticated App context and exactly one
discoverable, running sds200 App with Ingress enabled. A missing, inaccessible or
ambiguous App fails closed. Stop the other sds200 candidate before switching
between test and installed Apps. No arbitrary host, URL, port, token or Ingress
identifier is accepted in the card YAML.

## Add the card

Choose **Mimic-SDS** in the card picker, or use:

```yaml
type: custom:sds200-mimic-card
title: Mimic-SDS
layout: preferred
led_treatment: strips
density: standard
show_details: true
grid_options:
  rows: auto
  columns: full
```

The visual editor offers the same presentation options:

| Option | Choices | Meaning |
| --- | --- | --- |
| `layout` | `preferred`, `simple`, `detail` | Use the imported preference or choose a local layout. The physical Simple/Detail toggle is not reported. |
| `led_treatment` | `strips`, `border` | Show reported alert color on the top/bottom edges or around the whole scanner screen. |
| `density` | `compact`, `standard`, `tall` | Choose an intrinsic scanner-area height of 280, 360 or 480 pixels for automatic rows. |
| `show_details` | `true`, `false` | Show a closed-by-default disclosure for profile status, field availability and interpretation limits. |
| `title` | Up to 128 printable characters | Card heading. An empty string hides it. |

Choices are independent for each card and are saved in its dashboard
configuration. Both LED treatments use a frame thickness of 3% of that card's
scanner panel's shorter dimension, with the same thickness on all four sides.
The frame scales with the panel, not the browser window; transparent side edges
reserve the same space in strips mode so changing treatment does not move the
fields. The existing default remains `strips` unless `border` is selected.
The imported profile still determines field assignments and
colors. Simple/Detail applies to Conventional and Trunk; Search/Close Call,
Weather and Tone-Out use their documented family layouts. Waterfall remains a
separate card and scanner screen.

Automatic rows use a stable intrinsic height, independent of repeated live
frames. Numeric `grid_options.rows` fills the dashboard's allocated height;
very short allocations scroll inside the card to preserve readable content.
Width grows with the dashboard. Region positions, alignment and name-band
line counts stay fixed; long text is clipped within its own region rather than
pushing neighboring fields down. A wider/full-width card is preferable when
many small option fields need to be readable.

## Live data, colors and connection health

The card reuses the WebUI's exact canonical geometry, strict frame decoder,
bounded response reader and safe text-only drawing code. Individual name holds
invert the profile's configured foreground/background pair. No HTML, CSS URL,
raw profile path or executable field value is accepted from a frame.

Visible cards poll the existing read-only `api/v1/display-frame` Ingress route
250 milliseconds after each successful response, with a two-second HTTP timeout.
This is a client refresh cadence, not a scanner FPS guarantee. Ingress discovery
and session calls have eight-second bounds. Waterfall and all Mimic cards on the
page share one Ingress session owner and one renewal timer; each card keeps its
own presentation and freshness state. Authentication uses Home Assistant's
Supervisor context, not saved passwords in the card.

Disconnected, stale, unsupported, ambiguous or rejected data clears the live
values and LED rather than showing an old screen as current. Sequence and
endpoint checks reject replay or unexpected-source data. Repeated frames cannot
extend their original five-second freshness deadline. Hidden/offscreen or
removed cards stop polling, release their session lease and clear live values;
late callbacks cannot repopulate a detached card. The final consumer stops the
renewal timer. Returning to the dashboard automatically requests fresh data.

The details disclosure identifies raw/unavailable fields. Source text is
preserved, including already-formatted frequency strings. Alert color is
reported, but blink timing is not inferred. BLACK/WHITE transforms, exact icon
glyphs and some scanner-specific fields remain unqualified. Neutral fallback
colors or unavailable values are explicit limitations, not a claim that the
physical scanner has those settings. See the
[shared frame contract](scanner-display-frame-api.md).

## Developer validation and remaining acceptance

`scripts/build_mimic_lovelace.py` generates the committed Mimic module from the
shared WebUI core plus the HA adapter. It also inlines the identical page-local
Ingress manager into Waterfall and updates both manifests and the ordered
aggregate digests. `--check` verifies reproducibility without writing files.
The source-code-only contract contains no user profile or captured scanner data.

Run `node scripts/audit_home_assistant_mimic.mjs` for the reproducible real-Chrome
card audit (Node 24+, Chrome/Chromium and the Python development dependencies).
Optional `--python` and `--chrome` paths select the local tools. It opens a new
temporary browser profile and a loopback-only fixture page, using packaged card
bytes, fictional scanner frames and a synthetic HA context. It never connects
to an installed Home Assistant instance or scanner. CI runs this independently
of the general dashboard/Waterfall audit. All 33 frame scenarios in three local
layouts, four viewport/DPR configurations, three densities, both LED treatments,
host-only resizing, short fixed rows, trusted keyboard disclosure/focus and
two-card session cleanup are covered. Repeated updates must not grow auto rows.
The audit writes no screenshots and removes only its temporary browser profile.

### Offline visual acceptance — September 28, 2026

The user reported `Mimic card layout passed` for the packaged candidate at
development commit `89155dd`. The finite loopback-only preview used fictional
frames and a synthetic Home Assistant context. Its checklist covered Simple
and Detail, both 3% LED treatments, full/tablet/phone widths, the three intrinsic
heights, readable fields and the details disclosure; short fixed rows and a
second independent card were optional checks. The overall pass does not assert
that every optional combination was individually exercised.

The preview server was stopped and its listener confirmed closed. No Home
Assistant resources, scanner configuration or Pi services were changed. This
closes the candidate's offline human layout check, not installed Home Assistant,
live-data fidelity, physical-scanner or Firefox/WPE acceptance.

### Remaining acceptance boundaries

Deterministic tests cover configuration, all seven layouts, malformed/oversized
responses, source/sequence aging, late authentication and context callbacks,
visibility, multiple leases, renewal shutdown, installer safety and existing
cards. Synthetic Chromium checks cover narrow/wide intrinsic sizing, fixed
rows, text containment, per-card choices and final-consumer cleanup. These do
not substitute for actual Home Assistant/Pi/scanner acceptance, or establish
Firefox/WPE compatibility. Paired candidate packaging and the real-screen
comparison remain required before release.
