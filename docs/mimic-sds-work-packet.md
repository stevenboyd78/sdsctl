# Mimic-SDS: profile-driven screens and front-panel controls

Status: offline parser, import state, screen/value foundations, single-owner
observation adapter, synthetic SVG preview and interactive frame preview
implemented locally; no installed user-facing support or release. Source review
baseline: `ec17cf9d4cc3f41c57fd3a647990d5d2322b8719` (v0.30.0 release closure).
This packet adds to the [roadmap](../ROADMAP.md) and
[independent work packets](next-milestone-work-packets.md); it does not assign a
milestone number or release version. Keep the Home Assistant Waterfall-card
auto-height correction first and separate from this feature work.

## Requested experience

Add **Mimic-SDS** as an optional scanner-faithful layout/theme for the TUI and
WebUI, plus an **additional Home Assistant card**. Do not replace the existing
layouts, themes, scanner card, or Waterfall card. Reconstruct the scanner's
documented screen regions, configured field choices, colors, indicators and
context-sensitive soft keys from a validated scanner profile and live data.
This is semantic reconstruction, not a pixel capture of the scanner LCD.

In the Mimic-SDS TUI, put daemon runtime information in a toggleable drawer or
overlay like the existing command palette and keyboard reference. Include
connection health/target, application versions where actually reported, daemon
audio/recording status and operational logs. Keep scanner-owned REC and other
scanner indicators on the mimic screen; do not confuse them with daemon WAV
recording. Preserve a visible disconnection/stale-data indication even when
the drawer is closed. Direct USB must not gain unavailable daemon/audio controls.

Also add the complete front-panel key set requested from page 35 of the supplied
remote-command specification, using shared, authorized controls rather than
renderer-specific raw-command paths.

## Reference evidence and limits

The September 14, 2026 review used these user-supplied references read-only:

- `SDSx00_File_Specification_V1_08.pdf`: `DisplayOption`, `DispOptItems`,
  `DispColors`, layout/color IDs and screen diagrams (PDF pages 31-39, including
  horizontally continued tables), and color/item codes (PDF pages 57-60).
- `SDS200_RemoteCommand_Specification_V1_02.pdf`: `KEY` syntax on page 4,
  PSI/GSI screen information, and the complete key-code table on page 35.
- `SDS200om.pdf`: printed pages 37-41 (PDF pages 43-47), customizing the display,
  fixed versus configurable regions, Simple/Detail, Search/Weather/Tone-Out,
  field-size eligibility and the explicit Icon Area item list.
- `Display Table Layouts.html`: the user's three screen-family grids, including
  option slots, name/primary regions, icons and soft keys. This is a layout
  reference, not executable application code. It intentionally omits Waterfall.
- `profile.cfg`: inspected locally for display records, structure and model
  metadata only. It contains 25 option-group and 46 color-group records covering
  the seven documented modes. The original file is not a public fixture.

The September 15 follow-up also reviewed the supplied Simple/Detail trunk photos,
HTML cell alignment, remote-specification `Property.A_Led` and individual name
holds (pages 18-19), and file-specification Alert Light Color/Pattern (pages 9-10),
channel/talkgroup alert fields (page 17) and Unit ID alerts (page 14). Photos are
visual evidence, not substitutes for live XML or permission to publish private
scanner names/profile content as fixtures.

The [current vendor V2.00 remote specification][remote-v2] also retains the
`KEY,[KEY_CODE],[KEY_MODE]` form. Neither the inspected V1.02 nor V2.00 command
block defines the complete key-mode value set. The existing implementation
qualifies only `F`, `A`, `B`, `C` with a single `P` press for hold gestures.
Do not infer long-press, key-down/up or repeat behavior for new keys.

Resolve source differences explicitly. The owner's manual describes shared
Conventional/Trunk colors and limited Weather/Tone-Out customization, while the
file specification and supplied profile contain distinct IDs/records. Preserve
those records; establish actual model/firmware behavior before aliasing colors
or promising that every stored item is honored by the physical scanner. The
manual also shows hold indicators absent from some simplified layout drawings.
Record such differences in the per-region mapping, not as silently discarded data.

[remote-v2]: https://info.uniden.com/twiki/pub/UnidenMan4/SDS100FirmwareUpdate/SDS_Series_RemoteCommand_Specification_V2_00.pdf

## 1. One read-only scanner-display profile

### Offline parser foundation

`src/sds200/scanner_display_profile.py` now provides the internal, pure
`parse_scanner_display_profile(bytes)` projection. It performs no file access,
network access, profile persistence or scanner commands. Synthetic tests cover
all seven mode-ID mappings, positional fields, malformed inputs, bounded sizes
and display-only output. This is only the first part of delivery slice 1.

The projection includes `TargetModel`, `ProductName` and `FormatVersion` when
present, the four reviewed `DisplayOption` settings, ordered option groups and
stored text/background color pairs. Blank slots and the literal `Empty` token
remain distinct. Unknown syntactically safe option tokens are retained but not
executed or treated as available live fields. Reserved column positions are
honored while parsing, but their values and unrelated records are discarded.
The original file is not retained in the model or its JSON-safe descriptor.

Current bounds are 1 MiB input, 10,000 records, 16 KiB per record, 256 fields per
record, 1,024 bytes per field, 64 items/color pairs per group, and 64 characters
per option token or identifying value. Input must be printable ASCII with tabs
and CRLF/LF/CR line endings. Colors must be exactly six hexadecimal digits.
Known record duplicates and unsupported/malformed IDs/settings are rejected;
no partially parsed result is returned. Records may be reordered without
changing the normalized revision, while slot order and display values matter.

A valid projection requires `DisplayOption` plus at least one option and color
group. This does **not** certify a complete screen: missing groups stay missing,
and the parser does not synthesize defaults or equate display IDs with color IDs.
The revision is a normalized content digest, not scanner identity or evidence
that settings are current. The in-memory binding/refresh foundation below adds
selection and acquisition metadata; durable acquisition, per-region completeness
and live-data qualification are still required before a renderer consumes it.
The descriptor schema is an internal version-1 foundation, not a public API route.

### In-memory binding and refresh foundation

`src/sds200/scanner_display_profile_state.py` implements one selected endpoint's
review lifecycle: begin a refresh, prepare a parsed preview, then explicitly
commit it. Endpoint and source identities are opaque UUIDs supplied by the
integration layer, not model names, paths, passwords or network addresses. This
does not require DNS and does not prevent IP-only or USB endpoint configurations.
The caller must bind those IDs to the actual selected configuration; a manual
file's contents cannot prove that it belongs to the chosen physical scanner.

The envelope records manual import versus Favorites-sync source, acquisition
time and accepted-import time. Aware timestamps are normalized to UTC internally;
future presentation should use the user's local date/time convention. Acquisition
time means when the input was obtained, not when its scanner settings were last
changed. Successful state is `last_imported`, never an assertion of current
scanner synchronization. File metadata still does not establish firmware support.

Snapshots and accepted imports are immutable. Preparing a preview does not
replace the last good import. A source-ID or source-kind change requires explicit
confirmation even if the parsed bytes are equivalent. Only the exact current
preview can be committed once. A later refresh supersedes earlier tickets; slow
successes or failures cannot overwrite newer accepted state. Duplicate concurrent
parses for one ticket are refused, and publication is protected by a lock.
This is a local consistency contract, not server authorization or remote consent.

Malformed input, unavailable acquisition or a source changed during a read can
record a sanitized failure category plus the attempted source and start time.
Failure/cancellation retains the last good import; no defaults are synthesized.
An initial failure leaves the profile unavailable. Cancelled/failed/completed
tickets cannot be replayed. These safeguards currently exist **in memory only**:
there is no disk persistence, background sync, acquisition adapter, CLI/API route,
automatic profile selection, renderer hook or scanner write.

### Remaining acquisition and synchronization contract

Introduce a scanner-display profile type separate from existing sdsctl connection
profiles. Import an explicitly selected `profile.cfg`, or acquire that exact
file as an optional part of an operator-selected Favorites synchronization.

The current copied/USB/FTP Favorites storage adapters operate on Favorites
catalogs and documents; they do not yet provide this scanner-display import.
Add a bounded acquisition contract rather than casually broadening accepted
Favorites paths or assuming `GLT` returns the profile. A USB read requires
already-accessible scanner storage or a separately coordinated storage-mode
workflow. Do not switch modes, interrupt scanning, expose ports, or open a
competing scanner-control session just to refresh a theme.

Requirements:

- Parse only reviewed display and identifying records. Preserve tab-delimited
  empty/reserved columns, CRLF handling, ordered slots and unknown extensions.
  Enforce byte/record/field limits, valid IDs and six-digit color values; reject
  ambiguous duplicates or malformed known records without replacing good state.
- Recognize the legacy container identity: the supplied file has
  `TargetModel=BCDx36HP` and `ProductName=SDS200`. Do not reject that combination
  or infer the active scanner model from the legacy marker alone. Bind an import
  to the selected endpoint with explicit provenance, not just a product name.
- Normalize `DispOptItems`, `DispColors`, and relevant `DisplayOption` values
  into one immutable, versioned descriptor shared with clients. Expose only
  validated display fields; do not distribute the raw profile. Location, owner
  details, search presets and unrelated settings stay private by default.
- Store source kind, selected endpoint identity, capture/import time and revision.
  Show `last imported/synced`, `unavailable`, or `stale/unknown freshness` honestly.
  A later physical or Sentinel setting change is not detected merely because
  PSI continues arriving. Refresh after a successful authorized acquisition;
  preserve the last good descriptor, with an explicit status, if refresh fails.
- Prevent partial updates and cross-scanner profile reuse. Select the source
  explicitly when both a manual import and sync are configured; show a change
  preview on source replacement. Never silently switch sources.
- Render safely with a last-good matching profile or offer an explicit default
  preview/import prompt when no valid profile exists. Do not label defaults as
  scanner-synchronized. Import/refresh never writes scanner settings or Favorites.
- Keep original profile bytes untouched. Derive synthetic fixtures instead of
  committing the user's complete profile, PDF attachments, personal paths or
  location/owner values into public source or diagnostic exports.

## 2. Shared screen descriptor and live-data mapping

The two ID namespaces are different:

| Screen mode | DispLayoutId | ColorLayoutId | Layout family |
| --- | --- | --- | --- |
| Simple Conventional | 1 | 1 | Simple |
| Simple Trunk | 2 | 6 | Simple |
| Detail Conventional | 3 | 2 | Detail |
| Detail Trunk | 4 | 7 | Detail |
| Search / Close Call | 5 | 3 | Search/CC/Weather/Tone-Out |
| Weather | 6 | 4 | Search/CC/Weather/Tone-Out |
| Tone-Out | 7 | 5 | Search/CC/Weather/Tone-Out |

Map every slot by group, layout and documented order; do not zip color IDs
directly to layout IDs. Preserve fixed versus configurable fields, foreground
and background colors, documented reversed F/HOLD/soft-key styling, explicit
empty slots, and the profile's COLOR/BLACK/WHITE selection. Use the actual stored
hex values rather than CSS color-name equivalents.

For each allowed option token, record: profile token, valid region, current
parser/model field, daemon snapshot projection, renderer format, unit evidence,
absence behavior, and physical-validation status. Start with the existing
[renderer parity packet](renderer-parity-work-packet.md); do not assume the
current 35-field WebUI inventory supplies every scanner option.

Use live PSI/GSI and existing authoritative state for current values and mode.
The saved Simple/Detail preference is a fallback setting, not proof of the live
mode after front-panel changes. Identify the actual live selector or visibly
mark the profile-based assumption until it can be verified. Keep raw IDs and
unknown values; clear stale fields on mode/channel changes. Do not infer missing
unit names, RF statistics, USB voltages, battery gauges or icons from unrelated
values. Deliberately empty and configured-but-unavailable are distinct states.

Scanner date/time slots must use verified scanner time or clearly disclose a
host-time substitute. The existing local RFC-style application clock belongs
to application chrome/runtime details, not an invented scanner clock.

### Offline region and raw-value foundation

`src/sds200/scanner_display_layout.py` now describes all seven explicitly
requested modes on a renderer-neutral 30-column, 20-row logical grid. The grid
follows the supplied HTML's region proportions, cross-checked against PDF
pages 31-39; these coordinates are not physical LCD pixels. Each cell belongs
to exactly one region. Huge name regions, option regions, icons, information
areas, spacers and soft keys remain distinct. There are no active controls.

The descriptor maps one-based option and color positions in their separate
namespaces. Simple layouts have eight small option slots; Detail and special
layouts have positions 1, 2, 3, 4, 7, 8 backed by six ordered tokens. Detail
large fields alternate A/B for five rows before C1/C2; special screens use
three A/B rows before C1/C2. Special screens have no huge-option group.
Group sizes must match the reviewed table before values are assigned. A missing,
short or oversized group does not shift later positions or partially populate
it. Unexpected groups are reported without deleting them from the parsed profile.
Blank strings, explicit `Empty`, missing configuration and configured tokens are
different states; a configured token alone does not prove live-data support.

Stored color values and documented F/HOLD/soft-key reversal flags are retained
separately. No COLOR/BLACK/WHITE transformation is invented. All Detail/special
small-field colors remain explicitly unqualified because the printed eight-color
table conflicts with the six-option grid and observed record size. Supplying
eight color pairs does not resolve that ambiguity. Other qualified groups can
be mapped without silently guessing these colors.

`src/sds200/scanner_display_values.py` projects only allowlisted shared snapshot
fields into raw-source values. It requires explicit freshness and an independently
qualified matching operating data family or exact source mode. Defaults, stale
samples, unknown families and mismatches emit no source values. Exact-mode callers
retain their stricter check; supplying both qualifications is rejected. Conventional
and trunk families each support Simple and Detail presentation without asserting
which layout is physically selected. It caches nothing, so a new empty sample cannot
retain an old channel, tone or one half of combined Volume/Squelch. All displayed
data is classified `raw_source`, not a claim of LCD-format parity. Numeric zero
and text prefixes/leading zeroes are preserved; no TGID, unit, color, clock or
radio-graph conversion is applied. Raw frequency and code values must be labelled
as such in the development preview until their display formatting is qualified.

Option membership follows the Huge table on PDF page 57 and Large/Small tables
on page 60, cross-checked against the owner's manual printed pages 39-40. A known
token in the wrong region is not presented. The owner's manual printed page 41
establishes icon membership; active-state/glyph rendering remains unqualified.
Scanner REC uses only shared scanner `recording`, never daemon recording. Battery
voltage, modulation with ambiguous fallback provenance, RSSI bars, scanner date
and time, and other unsupported fields remain unqualified rather than invented.
Text is bounded and rejects terminal controls, Unicode control/format characters
and lone surrogates. Raw text is not markup: renderers must use escaping or
`textContent`, including for configured names containing angle brackets.

### Field capacities and icon choices

The owner's table labels Huge as 22 characters, Large as 16/14 characters and
Short as 5 characters. These describe the scanner baseline, **not hard character
limits for Mimic-SDS**. Per user direction, preserve relative regions and their
minimum intended capacity where space permits, and allow them to grow on larger
displays. Do not truncate source data to 22, 16, 14 or 5 characters, infer token
eligibility from the length of a value, or expand one field independently in a way
that moves its neighbors. The offline HTML grid now expands with its container,
including beyond 1920 pixels; constrained viewports clip/wrap visually within
the layout, with complete bounded text available in the field-details view.
This is not a guarantee that every minimum fits an arbitrarily narrow viewport.
Final TUI adaptation will use terminal cell counts, not CSS pixel assumptions.

The fixed name bands are different from configurable Huge option fields: printed
page 38 shows 24 characters x 2 lines in Simple and 24 x 1 in Detail. The supplied
photos corroborate two-line Simple names and one-line Detail names. Mimic uses
those line counts as layout behavior without imposing a 24-character cap.

Icon placement uses these exact file tokens, with the manual's item descriptions:

| Saved token | Icon-area item |
| --- | --- |
| `PRI` | Priority scan |
| `CC` | Close Call |
| `WxPRI` | Weather priority |
| `REC` | Scanner recording |
| `IFX` | IF exchange |
| `GPS` | GPS |
| `SCR` | Broadcast screen |
| `REP` | Repeater find |
| `LVL` | Volume offset |
| `Modulation` | Modulation |
| `P_Ch` | Priority channel |

The page-41 Repeater Find sample says `REF`; the file specification and the
page-40 sample use `REP`. Accept the saved token `REP`, not an invented `REF`
configuration alias. A permitted icon selection still produces `unqualified`
until its source, active/absent semantics and glyph are implemented. In particular,
do not replace the REC icon with raw On/Off text or daemon recording status.
Known non-icon tokens are invalid placements; unknown tokens remain unknown.
Blank cells in one manual table do not silently remove choices explicitly listed
in the file specification. Weather/Tone-Out share the special-family layout but
do not gain unverified item-customization support from that shared geometry.

These modules are internal foundations, not public APIs or user-facing renderers.
They do not certify a profile's physical scanner identity. The observation adapter
below carries endpoint/provenance and operating-screen qualification through to
the values; actual owner subscription/transport hooks and user-facing rendering
remain unimplemented. Current tests validate source-table positions,
complete nonoverlapping grids, missing/extra groups, unsupported fields, safe
text, numeric bounds, freshness/mode refusal and stateless transitions.

`src/sds200/scanner_display_preview.py` and
`scripts/render_scanner_display_preview.py` provide a **development-only SVG
gallery**, not an installed WebUI/TUI/card theme. Run the script with `PYTHONPATH=src`
and `--output-dir` pointing to a new scratch directory to generate seven offline
screens. The script accepts no real profile path or network endpoint; all sample
profile choices and values are invented. Special-screen samples do not carry
scanning hierarchy or trunk IDs into unrelated modes. Files are created exclusively
so an existing gallery is not overwritten.

Every image states that it is an offline preview, labels the selected mode, and
explains raw values and unavailable/unqualified placeholders. Neutral colors
indicate unqualified color mappings. A region's SVG title records its exact
status, and text is escaped and bounded within its region. The artifact contains
no scripts, external resources, active controls, raw profile bytes or source
paths. Tests cover all seven grids, exact region coverage, XML-safe text and
metadata, invalid colors, exact value matching and non-overwrite behavior.
This preview is only a development aid; a live renderer still requires the
endpoint/provenance, actual mode/freshness and user-facing accessibility contracts.

### Single-owner observation adapter and manual layout choice

`src/sds200/scanner_display_adapter.py` consumes complete parsed PSI/GSI from an
existing scanner owner. It does not open a scanner connection, subscribe to events,
add polling, expose an API, persist data or enable commands. Synthetic tests use
the actual XML parser and shared snapshot conversion rather than a second field
parser. A later owner integration must explicitly start a session on connection,
feed every complete observation (including unchanged PSI), and invalidate the
session on disconnect. Scalar getters and state-change-only events cannot refresh
scanner-display freshness. The caller must serialize ordered owner observations
and use a common monotonic clock; wall-clock source timestamps are not freshness.

Root `ScannerInfo Mode` identifies operating context, while `V_Screen` identifies
the visual screen family (Remote Command Specification V1.02 pages 17-18).
The user confirmed that physical Simple/Detail selection is manually toggled.
The reviewed table does not establish a live Simple/Detail selector, so the
adapter separates **operating data family** from **presentation layout**:

- Exact `conventional_scan` and `trunk_scan` select their corresponding data
  families. Start Simple/Detail from the last imported profile, with layout basis
  `profile_preference_unconfirmed`; do not claim the choice is synchronized.
- An explicit Simple/Detail presentation choice overrides that default for the
  requesting consumer only, with basis `explicit_presentation_choice`. It neither
  changes the imported profile nor sends a scanner keypress. Valid live values
  remain available in both layouts; uncertainty about presentation is not treated
  as loss of valid operating data.
- Exact `custom_search`, `quick_search`, `close_call`, `cc_searching`, `wx_alert`
  and `tone_out` use their documented special-screen family. A Simple/Detail
  choice cannot convert these into scanning screens.
- Known contradictory operating-mode/screen pairs, incompatible channel nodes,
  duplicate source records and the currently ambiguous dual WX frequency sources
  are explicitly refused. Unknown screen IDs, menus, combined search-with-scan,
  discovery/analyze, direct-entry and Waterfall do not silently fall back to scan.

The adapter isolates each screen's allowed source records before the existing
snapshot conversion. Old System/Site records cannot populate Weather, and a new
sample missing a field does not keep the previous value. Only the reviewed value
projection is cached, with bounded text; raw XML, unrelated records, source paths,
credentials, popup content and arbitrary root mode text are not retained.

`PopupScreen`, `OverWrite`, `PlainText`, replay markers and menu/replay operating
states yield an explicit override state without normal live fields. Ordinary
InfoArea records alone do not suppress the screen. This follows the distinction
on specification pages 23-24; it is not an implementation of scanner menus,
popup text/buttons, replay or soft-key controls.

Session tickets are identity-bound, so old, foreign, copied and disconnected
tickets cannot repopulate the frame. Sequence order and receipt timestamps are
checked; repeated observations with new sequence numbers refresh freshness even
when the fields are unchanged. Delayed delivery keeps its original monotonic
receipt age, expires at the explicit stale threshold, and cannot become fresh
through a backwards clock. Reconnect starts without old data. Stale frames may
retain layout geometry but contain no source values.

Frame status, layout basis and import provenance are independent. A last-good
matching import survives pending/failed refresh with that status visible; a
missing import does not invent a default scanner profile. Cross-endpoint bindings
are refused. These are local consistency contracts, not authentication or proof
that a manually selected profile came from the physical scanner.

The development renderer described below now consumes these frames. Actual
single-owner event/transport integration remains a later delivery boundary.
Current tests do not constitute hardware acceptance.

### Offline interactive frame preview

`src/sds200/scanner_display_frame_preview.py` renders qualified
`ScannerDisplayFrame` objects as escaped, scoped HTML fragments. It independently
refuses live values in non-current frames, checks canonical region geometry and
matching imported-profile revisions, and exposes three separate status lines:
sample/connection health, import status, and presentation-selection basis.
Unknown/menu/popup states never silently fall back to current scanning content.
Missing profiles do not invent a default layout; failed refreshes can retain the
last good import with the failure clearly indicated.

Simple/Detail is a local presentation choice, not a scanner control. A manual
physical toggle does not by itself prove that its resulting state is unreported;
the limit here is that the reviewed sources have not established a live
Simple/Detail flag. The default is therefore explicitly labelled an unconfirmed
imported preference. Qualified operating data remains available in either layout.

The gallery uses the actual parser, profile-import lifecycle and observation
adapter with invented data. Thirty-three transition scenarios include conventional
and trunk scanning, special families, missing/failed imports, stale samples,
disconnect/reconnect, overlays, unknown/conflicting screens, literal HTML-like
text, disappearing fields, independent name holds, all eight documented A_Led
values and missing/invalid/stale indicators. Each has profile/Simple/Detail variants. Two
independent preview panels demonstrate that a consumer's choice does not mutate
the other consumer, scanner or imported profile. Controls only select precomputed
frames: no scanner/network access, polling, credentials, profile upload or storage.
The document's CSP denies network resources and permits only its hashed script.

Generate a new local directory using the development environment, then open its
`index.html`. The generator refuses to overwrite an existing file:

```bash
PYTHONPATH=src python scripts/render_scanner_display_frames.py --output-dir /tmp/mimic-frame-preview
node scripts/audit_scanner_display_frames.mjs /tmp/mimic-frame-preview/index.html /tmp/mimic-frame-audit
```

The browser audit requires Node.js 24+ and Chrome (an optional third argument
selects the Chrome executable). It reuses the existing browser-audit protocol
helpers with a new isolated profile; it never controls a user's browser session.
It validates all 396 scenario/style/viewport combinations at 800x480, 1920x1080,
390x844 and 2560x1440, including stable grid heights, region containment/non-overlap,
independent consumers, keyboard selection/focus, enlarged controls and absence of
page network requests. It also checks source-based text alignment, hold inversion,
Simple/Detail name line behavior, expanding field widths and geometry-preserving
LED treatment changes. It writes screenshots and structured evidence to a new
output location, terminates only its own browser and retains that test profile.

This is a developer inspection page, not the final kiosk viewport. Its status
controls and complete 20-row grid intentionally scroll vertically at 800x480;
it does not claim the whole final small-Pi interface fits without scrolling.
Long field text is visually clipped within its canonical region and remains
available in the title and expandable field table. Units/code conversions,
unqualified icons, BLACK/WHITE transforms and ambiguous small-field colors remain
explicitly unresolved. No live route, TUI layout, HA card, profile-acquisition
hook or front-panel control is installed by this preview.

### Photo-informed presentation and alert-light contract

Normal screen content no longer carries generic field tags such as System,
Department, Channel or Option A. Full field identities remain in accessible
labels, titles and the expandable details table. Useful prefixes such as VOL,
SQL, TGID and RSSI remain in their applicable option fields. Raw values and their
qualification status are still explicit in the details; no LCD units/code
formatting is invented by merely removing a generic tag.

Region descriptors preserve the supplied HTML's explicit alignment: top short
options, under-name option rows, Simple A/B fields, icons and soft keys centered;
name bands, Detail/special A/B/C and information fields left-aligned. Simple names
can wrap to two lines; Detail names remain one line. Longer text never changes
the canonical row geometry. The synthetic profile uses invented red/green/blue
name colors to make the photo-described inversion easy to inspect.

Current `System.Hold`, `Department.Hold`, and `ConvFrequency.Hold` or `TGID.Hold`
independently invert their own qualified name-band foreground/background colors.
Site hold must not be treated as department hold. Missing/invalid holds are
unknown, not released. Stale, disconnected, unknown or overridden observations
clear hold indications and live LED color in both adapter and renderer.

Current `Property.A_Led` accepts only Off, Blue, Red, Magenta, Green, Cyan, Yellow
or White. The preview offers full-width top/bottom strips and an all-around
border, outside the field grid, with identical reserved geometry. Missing or
unrecognized A_Led is shown as unavailable, not Off. These RGB accents are
illustrative, not calibrated physical LED measurements, and are separate from
connection-health colors. The preview is static and does not claim blink parity.

The user's scanner menu and file-specification pages 9-10 agree on Alert Pattern:
`On`, `Slow Blink`, `Fast Blink`. Color and pattern are separately saved in
conventional channel and TGID records; Unit ID and other alert sources also have
settings. Therefore display-only `profile.cfg` is not sufficient to recover every
active channel's pattern. Future favorites synchronization needs to preserve
these settings with source/revision and stable record identity, separately from
the display-profile color/layout data. Before enabling animation:

1. Match the current alert to the correct synchronized record and alert source;
   never use only a duplicated name or carry the previous channel's pattern.
2. Establish whether live A_Led reports an instantaneous lit state or a sustained
   alert color, so a local animation does not contradict or double-blink it.
3. Qualify slow/fast period, duty cycle and alert precedence with observation;
   the reviewed pattern table gives names, not numeric timing. Do not claim
   scanner-accurate timing from arbitrary CSS durations.
4. Clear animation on stale/mismatched data or explicit Off, retain unknown as
   unknown, and provide a reduced-motion/static alternative. Until qualified,
   report current color without inventing a pattern or silently mapping unknown
   to steady On.

Handle temporary messages, popups, holds and unknown screens without inventing
screen content or hiding safety-relevant state. Menu/dialog visibility is a
dependency for enabling context-sensitive controls that enter those screens;
the seven display diagrams alone do not specify the whole scanner menu system.

Waterfall remains a separate follow-up: preserve the existing qualified
daemon waterfall stream. The supplied profile includes Waterfall color/settings
records, but neither their presence nor the HTML establishes a faithful
Waterfall-screen layout. Do not reopen GW2 guessing or create another poller.

## 3. Renderer adapters

### Initial live-data availability audit

At the `ec17cf9` runtime baseline, the shared `RadioStateSnapshot` still has 35
fields. A configured option name is not evidence that its value is available.
The following is a development mapping, not a claim of LCD-format parity or a
renderer implementation. Recheck each path when adding the actual projection.

| Scanner option / region | Existing source candidate | Boundary before Mimic-SDS presentation |
| --- | --- | --- |
| System, Department, Channel names | `ScannerInfo` name properties -> shared `system`, `department`, `channel` | Fixed name regions, not arbitrary configured option slots; clear absent names on every authoritative update |
| `SiteName` | `Site.Name` -> `site` | Use the selected live site, not Favorites metadata or a configured fallback name |
| `Frequency` | Selected channel/site/search node `Freq` -> `frequency` text | Preserve source text until its frequency encoding and mode-specific LCD format are qualified; no guessed unit conversion |
| `CTCSS/DCS` | Selected node `SAD` -> `sub_audio_detected` | Contains detected tone/digital-code text; no invented detection when absent, and selection/formatting needs mode evidence |
| `ServiceType` | `ConvFrequency`/`TGID` `SvcType` -> `service_type` text | Qualify code-versus-label formatting rather than assuming every wire value is an LCD label |
| `TGID`, `UnitId` | Selected node `TGID`, `U_Id` -> `talkgroup_id`, `unit_id` text | Preserve zeroes/prefixes; honor profile display-format settings only with a verified conversion contract |
| `Volume`, `Squelch`, `Volume&Squelch` | `Property.VOL`/`SQL` or authoritative scalar getters -> `volume`, `squelch` | Zero is valid; combined display must not retain an old half when the other value disappears |
| `P25Status` | `Property.P25Status` -> `p25_status` | Preserve raw scanner status; do not infer encryption, signal quality or analog/digital state |
| `REC` | `Property.Rec` -> scanner `recording` | Never substitute daemon WAV recording or playback status, including on USB |
| `Rssi` | `Property.Rssi` -> `rssi` | Validate finite telemetry and its units; `Rssi Bar` additionally needs the scanner's scale, not an invented percentage |
| `Modulation` | Node `Mod` -> `modulation`, with an existing P25-status fallback | The shared value does not distinguish direct `Mod` from fallback; exact mimic rendering needs provenance rather than silently calling every fallback a scanner modulation label |
| `BattVoltage` | Shared `battery` is only raw finite `Property.Battery` | No confirmed volts/percent meaning; do not label that value battery voltage without additional evidence |
| `SystemId`, `SysSubID`, `SiteId`, `WACN`, `ATT` | Raw `SystemStatusProjection` has related attributes | Not in the 35-field shared snapshot; select/qualify authoritative records and extend shared transport before rendering, without a second scanner owner |
| `Day`, `Time` | No verified scanner-clock field in shared state | Do not use the application header clock and imply it is scanner time |
| Other configured fields, icons and soft-key labels | Not established by this initial shared-state audit | Keep an explicit unavailable/unsupported state pending per-field source, transport and model/mode qualification; never fill from unrelated runtime data |

The audit follows [shared state](../src/sds200/state.py),
[scanner model projections](../src/sds200/models.py), and the existing
[35-field web parity test](../tests/test_web_dashboard_field_parity.py).
The [renderer parity packet](renderer-parity-work-packet.md) remains useful for
raw-ID, absent-value, mode-transition and safe-text tests. Slot-to-color ordering
ambiguities noted above must be resolved separately; this table does not license
index-zipping the arrays or leaking raw XML/profile data to renderers.

| Surface | Implementation target | Acceptance boundary |
| --- | --- | --- |
| TUI | Optional Mimic-SDS layout with a runtime drawer, focus-safe shortcuts and stable regions | 100x30 and 160x45 bench displays; truecolor, 256-color and Linux-console fallback; long names and drawer open/close |
| WebUI | Optional Mimic-SDS layout/theme, shared field semantics and scoped styles | Responsive Simple/Detail/special screens, browser zoom, keyboards/touch and safe text rendering |
| Additional Home Assistant card | Separate additive custom card consuming the same normalized profile/live state | Visual editor/YAML, multiple independent instances, auto/fixed grid height, external themes, reconnect and resource packaging |

### Per-surface presentation controls

The user accepted the direction of the refined preview and requested different
affordances for the same consumer-local settings. This is the implementation
contract for the future renderers, not a claim that those renderers are installed:

| Surface | Simple/Detail choice | Alert LED treatment |
| --- | --- | --- |
| WebUI | Visible layout drop-down, including Use imported preference, Simple and Detail | Visible adjacent drop-down for Light strips or Border; do not bury it in the diagnostics/runtime drawer |
| TUI | Local presentation action available through the command palette/key reference | One focus-safe key action cycles Light strips and Border; also expose it in the command palette and keyboard reference |
| Additional Home Assistant card | Per-card visual-editor and YAML configuration | Per-card visual-editor and YAML choice of Light strips or Border; no extra live toolbar required |

Use Light strips as the initial treatment, matching the current preview. Remember
explicit choices as local presentation preferences: scoped to the selected
source/display in the browser or TUI, and to each HA card instance through its
configuration. Preference persistence belongs in the real renderer integration;
the offline synthetic gallery deliberately does not write browser storage.
Do not publish configuration keys or assign a TUI key until the corresponding
schema and existing keymap have been checked and implemented.

These controls change only the visual treatment. They never send a scanner key,
change the saved profile's Simple/Detail preference, alter Alert Color/Pattern,
or mutate another display's preferences. They remain available to observe-only
clients because they do not exercise scanner-control authority. LED treatment
does not override the reported color or select a blink pattern. Simple/Detail
applies only to the conventional/trunk layouts; retain but do not apply the
choice during Search/CC/Weather/Tone-Out, and keep Waterfall separate.

Acceptance must cover independent browser views/TUIs/cards, saved preference
restoration, invalid configuration values, special-family transitions, stable
field geometry and preserved current data when changing treatment. For the TUI,
show a brief local result such as "LED: Border", do not capture the shortcut
while editing a text field, and ensure no action leaks through to the scanner.
The local setting must still work when disconnected, without falsely presenting
an active LED. Verify controls remain usable on both Pi sizes and with keyboard
or touch input where applicable.

TUI font sizes and exact LCD pixels cannot be promised on a character-cell
console. Match region order, emphasis and values; use deterministic palette
approximation where truecolor is unavailable. Do not change system fonts to
make a theme appear correct. The WebUI/card can reproduce colors and proportional
regions more closely, while keeping accessible text and input targets.

The card should reference the configured source, not require a raw profile or
credentials in dashboard YAML. Do not invent a public card type, CLI flag or
configuration key before its contract is implemented. Integrate the new card
into existing aggregate resource, manifest/hash, packaging and lifecycle checks
without duplicate registration or changing existing card identifiers.

## 4. Complete page-35 front-panel key inventory

The request covers all 27 listed codes, not all remote commands in the PDF:

| Code(s) | Front-panel action | Qualification notes |
| --- | --- | --- |
| `M` | Menu | Current menu/dialog state must be visible |
| `F` | Function | Already used in limited qualified hold gestures; other combinations need evidence |
| `L` | Avoid | Context can change scanner settings; not a harmless display action |
| `0` through `9` | Numeric keys | Focus and quick-key/direct-entry context matter |
| `.` | Dot / No | Decimal input versus dialog rejection is context-sensitive |
| `E` | Enter / Yes | May confirm a persistent change |
| `>` / `<` | Rotary right / left | Bounded, ordered steps; no unbounded held-key repeats |
| `^` | Rotary push | Separate from turning or Enter |
| `V` | Volume-knob push / SDS100 backlight | Not the same as setting volume level |
| `Q` | Squelch-knob push | Listed as absent for SDS100; not the same as setting squelch level |
| `Y` | Replay | Scanner playback, not daemon audio recording/playback |
| `A` / `B` / `C` | Soft keys 1 / 2 / 3 | Use current scanner labels/context; do not always label them System/Department/Channel |
| `Z` | ZIP | Location entry can affect scanning |
| `T` | Service Type | Listed as absent for SDS100 |
| `R` | Range | Can change scanning selection |

The attached table labels its columns BCD536HP and SDS100 even though it is
inside the SDS200 specification. Do not advertise every model/key combination
as physically supported from that heading alone. Build a model/firmware and
transport capability matrix; retain every requested code in the implementation
inventory and expose unavailable controls with a reason where appropriate.

Extend typed command validation, the single-owner daemon control transaction,
authenticated API/remote permission checks and renderer affordances together.
Do not simply widen the existing hold-only `PressKey` allowlist and inherit
hold-only authorization for Menu/Enter/Avoid. Keep desired-state hold APIs intact.
Observe-only kiosk/TUI credentials stay read-only at the server, even if someone
manually crafts a request or enables a button client-side.

Serialize key sequences across clients, bound queue/rate/gesture lengths, and
keep function-plus-key gestures atomic where qualified. Do not persist or replay
pending keys after disconnection, automatically retry an ambiguous `KEY,OK`
timeout, or report the intended state as confirmed from an acknowledgement alone.
Validate against a fresh authoritative state and show unavailable/uncertain
results. All command results and input labels must be safely escaped.

Deliver a full on-screen keypad/control drawer for WebUI/card and explicit TUI
bindings or command-palette actions. Editing text or opening a runtime drawer
must not leak keystrokes to the scanner. Review destructive/persistent menu
confirmations separately; adding a control does not authorize unattended scanner
programming, power-off, mass-storage switching or speculative key sequences.

## 5. Delivery slices and tests

1. **Profile and mapping foundation:** bounded read-only parser, synthetic
   fixtures for all seven modes, source/freshness metadata, documented ambiguous
   source differences and a per-option data-availability matrix.
2. **Mimic preview and WebUI:** scanner-free fixtures for every family and
   configured color mode; state transitions, empty/unknown fields, popup handling,
   stable sizing and hostile/long input. Reuse existing shared daemon data.
3. **TUI and additional card:** reuse the same descriptor and fixture corpus;
   prove the three renderers agree on slot/value/color interpretation. Exercise
   both Pi geometries, limited-color terminals, drawer focus, HA grid/theme
   interactions, multi-card cleanup and compatibility with existing themes.
4. **Profile acquisition during Favorites sync:** independently qualify copied,
   already-mounted USB and any supported network path. Test wrong endpoint,
   changed file during read, partial/failed sync, missing profile, source conflict
   and atomic last-good retention. Keep scanner write contracts unchanged.
5. **Front-panel controls:** implement the complete typed inventory with
   server-side capability/permission refusal, state-aware UI, interleaving,
   failure/retry and focus tests. Physically qualify documented actions on SDS100
   USB and SDS200 network sessions in bounded operator-supervised groups.
6. **Acceptance and release:** compare selected actual scanner screens with
   both Pis, WebUI and the new card; change a display setting, re-acquire the
   profile, and verify all consumers update consistently without a new scanner
   owner. Record unsupported data/model cases. Choose a feature release only
   after scope review; do not bundle this into the Waterfall maintenance patch.

Preparation can proceed without hardware. Physical visual/control acceptance
requires the user and must name the exact candidate, mode and expected result.
The offline parser adds no runtime integration hook, scanner command, theme,
card, sync job, credential, port, service, firmware-support claim or installed
change. The existing user interfaces and scanner connection owners are unchanged.
