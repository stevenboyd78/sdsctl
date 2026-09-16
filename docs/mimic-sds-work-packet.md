# Mimic-SDS: profile-driven screens and front-panel controls

Status: offline parser, in-memory and durable manual-import state, screen/value
foundations, single-owner observation adapter, synthetic SVG preview and
interactive frame preview, explicit daemon configuration, local import commands
and disabled-by-default administrator browser Upload/Refresh adapter,
App startup wiring, passive shared live-frame API, optional WebUI presentation
and daemon-backed read-only TUI presentation, plus an additional read-only HA card,
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
selection and acquisition metadata. The separate local-file storage foundation
below now retains accepted manual imports across process restarts; installed
acquisition, per-region completeness and live-data qualification are still
required before the user-facing renderers consume it.
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
tickets cannot be replayed. This module remains **in memory only**; the separate
durable manual-import adapter below reuses its validation/review contract rather
than adding file access to the pure store. Neither module creates background
sync, a CLI/API route, automatic profile selection or a scanner write.

### Durable manual-file import foundation

`src/sds200/scanner_display_profile_storage.py` adds an internal POSIX/Linux
local-file adapter. The local development branch now connects it to explicit
daemon configuration and local administrator commands, described below; the
separate upload adapter does not alter this read-only import contract. Installed
App wiring and Favorites sync remain pending. It requires three explicit inputs:
the selected endpoint UUID, an existing operator-selected source `profile.cfg`,
and a separate private accepted-state directory. It does not discover paths or
create the planned installation paths listed below.

The source file is read-only to this adapter. A service can import an
administrator-managed read-only file without receiving permission to replace it.
The accepted-state directory must be writable by the service account, owned by
that account and mode `0700`; its `accepted-profile.json` is mode `0600`.
`initialize_display_profile_storage` creates exactly one new directory below an
existing parent. It refuses existing directories, including empty or partially
initialized ones, and does not repair permissions or erase failed state.

The lifecycle is explicit:

1. `prepare(binding, acquired_at=...)` validates the bounded source and returns
   a display-only preview, without writing either source or accepted state.
2. The caller reviews that exact preview; a changed source identity needs the
   existing explicit source-change confirmation. `cancel` discards the review.
3. `commit(preview, imported_at=...)` verifies that the reviewed source and
   previous accepted state are unchanged, then atomically replaces one private
   document containing the original accepted bytes, normalized revision, source
   binding and acquisition/import timestamps.
4. A newly constructed adapter's `inspect()` validates/restores that document
   and separately reports whether the source still matches, has changed, is
   invalid, unavailable or unsafe. It never silently imports an external edit.
   With no accepted import, it reports `not_imported` and does not adopt a file.

An invalid/incomplete source edit, missing source, failed refresh or failed write
before replacement leaves the previous accepted import usable. Last accepted
does not mean scanner-synchronized, and a valid parsed profile does not prove
complete field support. Source-copy status must be shown separately in the future
management UI; it does not change the live observation adapter's freshness rules.
Disk inspection is an import/status operation, not work for each rendered frame.

The private document contains a byte-for-byte copy of the accepted source,
including unrelated settings, encoded as base64. **This is not encryption.**
Protect it like the original file, include both configured paths in the operator's
backup plan, and never expose that document via client payloads, diagnostics,
static files or recording downloads. Public previews/snapshots retain only the
existing display projection and opaque provenance; no raw source or path is
added. The original source file is never edited by this adapter.

Filesystem access rejects symlinks at every path component, hard-linked or
nonregular files, loose private permissions, and unsupported platforms rather
than weakening protection. Cooperative process locks and full file-identity
checks prevent simultaneous reviewed imports from silently overwriting each
other. These checks are not a security boundary against root or arbitrary code
running as the same service account.

The atomic writer flushes and checks its temporary file before replacement,
then flushes the directory and verifies the accepted record. A failure after
replacement is `outcome_unconfirmed`, not a successful save or guaranteed rollback.
The review is consumed; reopen/inspect and have an administrator reconcile it
before preparing a new import. Missing/corrupt accepted state fails closed and
is preserved for review, never silently rebuilt from an unreviewed source.
An abrupt process exit may leave a private temporary file; restart does not
purge those artifacts. Ordinary pre-replacement failure cleans only that
transaction's positively identified temporary file.

Synthetic regression tests cover explicit import/review/restart, changed and
invalid sources, provenance, read-only sources, concurrent processes, partial
writes, unsafe paths, file/directory sync failures, and abrupt subprocess exits
on either side of replacement. They also feed a restored profile into the
existing frame adapter/HTML renderer and check private data is absent. This is
not physical power-loss or Home Assistant backup/restore qualification.

### Daemon configuration and local administrator integration

The [local import guide](scanner-display-profile-import.md) documents the new
development-only `scanner-display-profile` init/status/preview/import/reload
commands and `daemon --scanner-display-profile-config PATH`. There is no implicit
manifest discovery, App option addition or initialization on daemon startup.
The bounded manifest explicitly binds endpoint/source UUIDs, exact transport
target, source path and private state directory. Invalid configuration, target
mismatch and overlap with the configured recording directory are refused.

One `DaemonDisplayProfile` supplies a cached immutable projection to the daemon's
existing API owner. Explicit reload reads accepted state, not an unreviewed file
edit. `display.profile` is a conditional read-only operation available to
authorized observe/control clients; responses contain only normalized display
data, opaque provenance, source-copy status and last-check time. It performs no
per-request disk read, scanner command or new connection. An endpoint mismatch
clears the cached projection until a local administrator revalidates it.

`display.profile.reload` is local-only even if mistakenly included in a remote
allowlist. The local command uses the existing private Unix API socket and checks
the selected endpoint identity before notification. There is no general API
upload/import/path parameter or implicit operator-as-admin grant. The separate
private Ingress adapter described below does not extend the general daemon API.
If persistence succeeds but notification fails, the command reports the two
outcomes distinctly and directs the administrator to retry reload, not import.

Existing renderers and event subscriptions do not consume the new projection
yet. No Home Assistant/Pi option, service, profile copy or installed display has
changed as part of this local work.

### Administrator browser Upload/Refresh adapter

The [import guide](scanner-display-profile-import.md#browser-uploadrefresh-development-adapter)
now describes a private, disabled-by-default Ingress adapter and browser page.
Only the trusted Supervisor peer plus an explicitly allowlisted user ID can
reach it. Exact HTTPS origin and single-use, user-bound CSRF checks protect
actions. Operator/display cookies confer no administrator access. The existing
application factory needs explicit private configuration; installed App options
and launchers remain unchanged. Native standalone browser administration is not
added; the local CLI remains its administrator workflow.

One bounded, five-minute, administrator-bound review stages uploaded bytes in
memory without saving. The page supports selected-file preview, existing-source
Refresh, normalized descriptor review, separate source-change confirmation,
Accept/Cancel and reload of already accepted state. Paths and identities are
server-selected, filenames ignored, raw content excluded from responses.
Uploads require a separately enabled private managed target with 0700 parent
and 0600 existing file. Read-only source imports still work independently.

Acceptance checks source/state conflicts, replaces a complete managed source
copy, then publishes the single authoritative accepted document. These two
replacements are not claimed to be one atomic transaction. A crash between them
leaves the last accepted profile available and the source copy independently
inspectable; uncertainty is explicit, with no rollback, retry or repair. Daemon
reload failure or a concurrent different revision is reported separately.
Browser restart never posts, and loss of an action response stops further writes
pending review. Synthetic tests cover cancellation during commit and abrupt
process exit as well as the browser UI and administrator boundaries.

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

### Profile authority and accessible durable storage

The user requested that actual scanner field assignments and field colors remain
owned by the uploaded/acquired `profile.cfg`, not duplicated in separate Mimic
color pickers or per-field configuration. The same accepted descriptor supplies
WebUI, TUI and the additional HA card. Consumer-local Simple/Detail and LED
treatment remain presentation preferences; they do not rewrite that profile.
Current field values, holds and active LED state still come from qualified live
scanner data, and alert patterns require the separate Favorites record contract
below. Uploading a display profile does not create missing live-data support.

Use a persistent, administrator-accessible file location consistent with the
installation. These are planned defaults/examples, not currently implemented
upload routes, App options or automatic file migrations:

| Installation | Planned scanner display profile location | Relationship to existing files |
| --- | --- | --- |
| Home Assistant App, default media tree | `/media/sdsctl/profiles/profile.cfg` | Sibling of `/media/sdsctl/recordings`, not a WAV-library item |
| Standalone system service using `/etc/sdsctl/config.toml` | `/etc/sdsctl/profile.cfg` | Beside the daemon's application configuration |
| Standalone user service | `${XDG_CONFIG_HOME:-~/.config}/sdsctl/profile.cfg` | Beside the service account's `config.toml` and daemon manifests |
| Custom/container deployment | Explicitly selected profile path beside the operator's daemon configuration, where suitable | Use the actual persistent host-backed configuration location, not the process working directory |

Home Assistant already maps `/media` read/write for recordings. Reuse that
persistent storage area, with a dedicated profile subdirectory and an explicit
override when another location is wanted. Do not derive a profile path by
blindly taking the parent of an arbitrary recording directory, move it when
recording settings change, or add a new port/share for profile access. Show the
selected path and import status in the App's administrator-facing profile UI.
Samba/SSH access depends on the user's existing share/mount configuration; being
under `/media` does not guarantee a particular file browser exposes it.

For standalone services, "beside the configuration" means the selected daemon
application/manifest configuration directory, not the directory containing the
systemd `.service` unit. Resolve the location explicitly for the service account;
do not select an unrelated interactive user's file from a different home or
silently choose between multiple layered configurations. Root-owned `/etc`
locations can be administrator-managed read-only imports. Upload or automatic
sync needs an explicitly writable managed target; do not grant the daemon root
or broaden write permissions on all of `/etc/sdsctl` to make replacement work.
Allow a separately configured persistent writable profile path when needed.

Durable import requirements:

- Keep the uploaded/copied scanner file byte-for-byte as a data file. Validate
  within the existing parser limits before accepting it; never execute its
  content, honor embedded paths, or use an untrusted upload filename as a target.
- Bind it to the selected scanner endpoint. One daemon-owned accepted profile
  serves its remote consumers; do not require a copy on every Pi or in card YAML.
  Multiple endpoints need explicit independent bindings/paths, not last-upload
  wins against a shared `profile.cfg`.
- Stage a complete upload or Favorites-sync read, then atomically accept its
  file/revision/provenance with recoverable last-good state. A crash, partial copy,
  bad permissions, malformed profile or failed refresh must not replace the
  accepted descriptor with partial/default data. On restart, validate the bound
  file and restore the accepted state/status consistently.
- Provide an explicit Upload/Refresh workflow and a refresh after a successful
  selected Favorites acquisition. A user replacing the file through SSH/Samba
  can request Refresh without restarting Home Assistant Core or the scanner.
  No implicit hot reload of partially written files is promised. Apply an
  accepted revision coherently to the owner and subscribed displays.
- Restrict upload/replacement to administrator/configuration authority. Raw
  profiles may contain location or unrelated scanner settings: retain restricted
  filesystem access, exclude them from diagnostics/public fixtures, do not add
  a public static-file/media download route, and send only the validated display
  projection to clients. Document exposure through any user-managed media share.
- Keep profile and provenance files out of recording inventory, playback/download
  endpoints, recording migration/retention and cleanup. Do not remove them when
  recordings are cleared. Document which persistent paths a backup must include;
  App persistence is not itself proof that every HA backup includes `/media`.

Test upload/restart/refresh and Favorites acquisition against missing, invalid,
partially written, mismatched and externally replaced files; read-only paths;
safe path boundaries; coherent concurrent refresh; and multiple remote clients.
Verify recording operations cannot alter profiles and profile refresh cannot
alter recordings, scanner programming, connection credentials or display-local
presentation choices. The internal durable manual-file adapter implements the
accepted-state portion locally, with explicit standalone manifest/command wiring
and local daemon cache reload. The private upload/Ingress adapter implements
guarded staging locally; App path/configuration wiring, renderer/subscriber
refresh and Favorites acquisition remain future integration work. None of the
planned paths above has been created or activated on live hosts.

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
establishes icon membership. The live-source qualification below adds a bounded
subset of active/absent text indicators, not scanner-pixel glyph reproduction.
Scanner REC uses only scanner `Property.Rec`, never daemon recording. Battery
voltage, RSSI bars, scanner date and time, and other unsupported fields remain
unqualified rather than invented. Modulation uses an explicit selected-node
`Mod` attribute, never the shared snapshot's ambiguous P25-status fallback.
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
unless its source and active/absent semantics are implemented. The live adapter
now provides the supported text labels listed below. In particular, the REC icon
is `REC` when scanner recording is On and blank when Off, not raw On/Off text or
daemon recording status. Exact proprietary glyph/flash parity is not claimed.
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

`PopupScreen`, `PlainText`, replay markers and menu/replay operating states yield
an explicit override state without normal live fields. Ordinary InfoArea records
do not suppress the screen. `OverWrite` is different: specification page 23 says
it replaces the channel-name area. For conventional/trunk scanning, show that
message in the channel region and retain only the other fields from the same
current observation. Normal `ID Scanning...` must not clear the whole screen.
An OverWrite on other screen families remains an explicit override until its
placement is qualified. This is not an implementation of scanner menus, popup
text/buttons, replay or soft-key actions.

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

### September 15 live-field qualification follow-up

The first SDS200 comparison exposed missing source mappings and an incorrect
full-screen interpretation of ordinary `OverWrite` messages. These are local
candidate corrections, **not yet a passed physical retest or published release**.
The [bounded live projection](../src/sds200/scanner_display_live.py) supplements
the shared snapshot only inside the already-qualified observation adapter. It
does not add scanner polling, a second owner, retained raw XML, profile editing,
or fields to unrelated clients' shared snapshots. All values still expire and
clear on invalid, unsupported, disconnected or stale observations.

| Region / configured item | Qualified current source and absence behavior |
| --- | --- |
| Information areas | `InfoArea1.Text` / `InfoArea2.Text`; missing optional record means blank, not a popup. Observed `SITE HOLD` is displayed here. |
| Channel scan message | `OverWrite.Text` replaces only the conventional/trunk channel-name region; other current fields remain. |
| Site hold | `Site.Hold` On/Off independently reverses each configured `SiteName` cell using its profile colors, in WebUI, TUI and the shared HA renderer. Missing/invalid is unknown; stale clears it. |
| Favorites name / system type | `MonitorList.Name` / `System.SystemType`, without a configured/database fallback. |
| Number tags | Current `MonitorList`, `System` and channel `N_Tag`; the literal scanner sentinel `None` becomes `--`, `--`, `---`. Numeric text/leading zeroes are preserved; missing attributes are not interpreted as unassigned tags. |
| Unit ID / unit name | The observed separate `UnitID.U_Id` / `UnitID.Name` record; legacy selected channel `U_Id` is used only when no UnitID node exists. An empty UnitID record clears the prior caller. |
| Digital status / DATA | `Property.P25Status`; `Data` renders `DATA`, `None` is blank, other bounded reported text is preserved. |
| Modulation | Selected screen node's literal `Mod` value (for trunk scanning, `Site.Mod`), including the observed `NFM`; no inference from digital status. |
| Priority / Close Call / weather priority icons | `DualWatch.PRI`, `CC`, `WX`; known active states render `PRI`, `CC`, `WX`; confirmed Off is blank. Text labels do not claim exact scanner glyphs. |
| Scanner recording / IFX / priority channel icons | `Property.Rec`, selected frequency `IFX`, selected channel `P_Ch`; known On renders `REC`, `IFX`, `P`, Off is blank. |
| Volume offset icon | Selected channel `LVL`: zero blank, documented nonzero -3 through +3 rendered as `V-3` through `V+3`. |
| Bottom scanning soft-key labels | `SYSTEM`, `DEPT`, `CHANNEL` for conventional/trunk scan families only; these remain read-only labels, not implemented key actions. |

Unsupported or missing sources are not reported as confirmed Off. GPS, broadcast
screen and repeater-find icons remain unqualified. System ID, RFSS/System Sub ID,
WACN and Site ID were **not present** in the sampled ordinary trunk PSI messages.
The related `SystemStatus` attributes are documented in the Analyze/system-status
section (remote specification page 21), which does not establish their availability
during ordinary scanning. Do not enter Analyze to fill a cosmetic display, reuse
old Analyze values, substitute saved record IDs/indexes, or infer them from NAC.
More source qualification is needed before these fields can match the scanner.

The regression suite distinguishes known inactive icons, unavailable sources and
invalid values; covers empty UnitID changes, duplicate records, full-screen
overlays versus channel overwrite, site-hold On/Off/stale transitions, ordinary
browser context updates, paired Python/JavaScript wire validation and renderer
color inversion. The first passive live sample verifies parsing and projection,
not visual acceptance. Keep the existing freshness thresholds; one observed
clearing cause does not prove every intermittent waiting message is resolved.

When no qualified screen can be drawn, the WebUI, TUI and shared HA renderer
distinguish an inconsistent observation, stale data, disconnection, an active
menu/popup/replay, a new-frame wait and a genuinely unsupported screen. Missing
profile configuration has its own import guidance. These messages clear prior
values; they do not extend the five-second observation lifetime or qualify a
conflicting mode/screen pair. An unavailable layout also must not claim a
documented screen-family basis. Regression tests cover these empty states and
recovery to a newer qualified frame, independently from physical acceptance.

Brief Close Call/scan transitions still require matching live source-record
evidence before any additional adapter exception. The Close Call-only band graph
is a separate deferred capability: documented band enable flags do not establish
bar heights or counter semantics. Do not draw invented bars, infer disabled
bands from missing bars, or reuse scanning values to conceal an unqualified
screen while that source is under investigation.

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
   source differences and a per-option data-availability matrix. The local
   durable manual-import engine, explicit daemon configuration, local admin
   commands and cached read-only API projection are implemented locally.
   The optional App configuration/launcher wiring and administrator-only browser
   Upload/Refresh adapter are also implemented locally, with read-only startup
   preflight and an end-to-end Unix-socket acceptance test. The published catalog
   deliberately does not advertise the candidate option to older images. The
   [shared live-frame API](scanner-display-frame-api.md) now joins the cached
   accepted profile with the existing owner's complete PSI observations,
   including independent presentation choices and stale/reconnect invalidation.
   A [source-pinned Local App staging procedure](mimic-sds-candidate-app.md)
   pairs the candidate schema/runtime without changing that catalog. Local
   launch-plan tests cover enable/restart/disable/re-enable while preserving
   accepted state and recordings. Built-image and live Supervisor qualification
   are separate gates; staging is not installation. Preserve raw-source privacy
   and the distinction between source and accepted state.
2. **Mimic preview and WebUI:** scanner-free fixtures for every family and
   configured color mode; state transitions, empty/unknown fields, popup handling,
   stable sizing and hostile/long input. Reuse existing shared daemon data.
   The optional WebUI presentation is implemented locally, including visible
   layout/LED selectors, canonical bounded decoding, profile/hold colors, demand
   cleanup and monotonic sequence aging. Existing native menu and sign-out flows
   are reused. See the [candidate WebUI guide](scanner-display-frame-api.md#candidate-webui-presentation)
   for defaults, non-persistent per-page choices and unqualified field/color/icon
   limits. No live deployment or physical acceptance is claimed.
3. **TUI and additional card:** reuse the same descriptor and fixture corpus;
   prove the three renderers agree on slot/value/color interpretation. Exercise
   both Pi geometries, limited-color terminals, drawer focus, HA grid/theme
   interactions, multi-card cleanup and compatibility with existing themes.
   The daemon-backed read-only TUI screen is implemented locally: **M** opens,
   **V** selects profile/Simple/Detail, **B** selects LED strips/border and **X/?**
   opens runtime/help. A bounded independent API reader preserves control-client
   responsiveness and clears hidden/stale values. Textual focus/geometry,
   Unicode/color fallback, replay/lifecycle and Unix-API tests are in place.
   Preferences are process-local, not persisted. Existing direct USB stays
   unchanged; standalone profile wiring, actual daemon-version/recording runtime
   additions and physical acceptance remain separate. The additional
   [Mimic-SDS HA card](home-assistant-mimic-card.md) is now implemented locally:
   shared WebUI decoder/drawing core, per-card YAML/editor options, stable auto
   height, fixed-row containment, and a common Ingress session owner with
   Waterfall. Installer/registry/aggregate generation, bounded discovery,
   multi-consumer/late-callback tests and synthetic Chromium sizing checks are
   in place. No installed HA or physical acceptance is claimed.
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
The candidate adds opt-in runtime integration, a WebUI presentation and a
daemon-backed read-only TUI screen and additional HA card, but no
new scanner owner, sync job, credential, port, firmware-support
claim or installed change. Existing surrounding themes remain unchanged.
Existing user interfaces and the published App catalog remain unchanged.
