# Mimic-SDS: profile-driven screens and front-panel controls

Status: offline parser, import state, screen/value foundations and synthetic SVG
preview implemented locally; no user-facing support or release. Source review
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
- `SDS200om.pdf`: printed pages 37-40 (PDF pages 43-46), customizing the display,
  fixed versus configurable regions, Simple/Detail and Search/Weather/Tone-Out.
- `Display Table Layouts.html`: the user's three screen-family grids, including
  option slots, name/primary regions, icons and soft keys. This is a layout
  reference, not executable application code. It intentionally omits Waterfall.
- `profile.cfg`: inspected locally for display records, structure and model
  metadata only. It contains 25 option-group and 46 color-group records covering
  the seven documented modes. The original file is not a public fixture.

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
qualified matching source mode; defaults, stale samples, unknown modes and mode
mismatches emit no source values. It caches nothing, so a new empty sample cannot
retain an old channel, tone or one half of combined Volume/Squelch. All displayed
data is classified `raw_source`, not a claim of LCD-format parity. Numeric zero
and text prefixes/leading zeroes are preserved; no TGID, unit, color, clock or
radio-graph conversion is applied. Raw frequency and code values must be labelled
as such in the development preview until their display formatting is qualified.

Option membership follows the Huge table on PDF page 57 and Large/Small tables
on page 60. A known token in the wrong region is not presented. Icon membership
and glyph behavior are not established by those tables and stay unqualified.
Scanner REC uses only shared scanner `recording`, never daemon recording. Battery
voltage, modulation with ambiguous fallback provenance, RSSI bars, scanner date
and time, and other unsupported fields remain unqualified rather than invented.
Text is bounded and rejects terminal controls, Unicode control/format characters
and lone surrogates. Raw text is not markup: renderers must use escaping or
`textContent`, including for configured names containing angle brackets.

These modules are internal offline foundations, not public APIs or user-facing
renderers. They do not certify a profile's scanner identity or current mode.
The next adapter must carry the selected endpoint/provenance contract through
to the values, establish real mode qualification, and validate a synthetic-data
preview before any live hook. Current tests validate source-table positions,
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
