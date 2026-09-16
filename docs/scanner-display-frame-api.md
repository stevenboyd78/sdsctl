# Mimic-SDS shared live frame contract

Status: **local candidate only**, not a published feature or an installed theme.
This is the shared read-only data path for the candidate WebUI, TUI and
additional [Home Assistant card](home-assistant-mimic-card.md). See the
[work packet](mimic-sds-work-packet.md) and
[profile administration guide](scanner-display-profile-import.md) for the
separate profile, deployment and physical-acceptance boundaries.

## One scanner owner

An explicitly configured daemon attaches `DaemonDisplayFrames` to its existing
scanner's connection and complete PSI callbacks. The feed sends **no scanner
commands**, starts no polling thread or connection, and takes no audio or
Waterfall subscription. Normal daemon startup still owns scanner connection and
PSI acquisition. No profile configuration means no display-feed subscriptions.
Only bounded display fields are retained, not raw XML. Scalar state updates and
API reads cannot refresh receipt time. This version does not issue GSI polls.

- Each feed has a new opaque `stream_id`; each connection has a new opaque
  `session_id`. Disconnect clears the current session and observation.
- Reconnect starts in `waiting`, with no old values or indicators. Already
  copied callbacks from a prior session are ignored, even for the same endpoint.
- Equal newly received complete PSI observations refresh the monotonic receipt
  time. Requesting the same frame does not.
- At five seconds without a complete observation, the default feed marks it
  `stale` and clears live values/indicators. This is observation freshness, not
  an assertion that the transport socket has disconnected.
- Unsupported screens (including Waterfall), overlays and ambiguous records
  clear live values instead of combining previous and current screens.
- Startup failure and process shutdown unsubscribe the feed. A closed feed
  cannot be restarted or revived by a late callback.

## Accepted profile and current data remain separate

Every response joins one immutable cached accepted profile with one observation
and one monotonic time. Frame reads never read or import `profile.cfg`. Source
edits have no effect until explicit administrator acceptance and daemon reload.
No file watcher or automatic import is introduced.

The profile lock is acquired before the observation lock. A slow administrator
disk reload may delay a frame request, but does not block incoming PSI callbacks.
An observed endpoint mismatch or failed accepted-state reload invalidates old
data, even if failure and repair happen between frame requests. The next read
clears old values and a subsequent complete PSI is required. A successful
ordinary profile refresh may immediately apply new assignments/colors to the
still-current observation.

`profile_status`, `source_status`, `profile_refresh_pending` and observation
`status` describe different things. Current live data can accompany a last-good
profile whose selected source is missing. Source status reflects the last
explicit inspection, not ongoing filesystem monitoring. Missing accepted
configuration must not be replaced with a made-up scanner profile.

## Read-only interfaces

The daemon operation is `display.frame`, with **no parameters**. Capabilities
advertise it only when configured; otherwise it returns `unsupported_operation`.
Remote observe clients may read it. Scanner-control dispatch cannot turn it
into a write; remote profile reload and administration remain unavailable.

The WebUI route is `GET /api/v1/display-frame`, with no query parameters. Its
response contains the existing `sdsctl.web` version-1 envelope plus `display`.
The daemon operation returns that `display` object directly. It contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | Frame contract version, currently 1 |
| `endpoint_id` | Configured opaque endpoint UUID |
| `stream_id`, `session_id` | Feed and connection identities; session null when disconnected |
| `failure` | Fixed failure category or null; no raw exception text |
| `source_status` | Status from the accepted-profile owner, or null |
| `frames` | Three entries: `preferred`, `simple`, `detail` |

Web responses use `Cache-Control: no-store`. Native display-only and managed
display sessions can read through existing authentication. Unauthenticated
access, non-read methods and administrator actions remain denied. Unsupported
daemon capability is HTTP 503, not a fabricated current screen. The API-index
link describes route existence, not daemon capability or health.

There is no new listener, port, cookie, credential or recording route. Existing
bounded daemon server limits apply. Raw source bytes, unrelated settings, host
paths and raw XML are absent. Source and endpoint UUIDs are identities, not
connection addresses or credentials.

## Frame and region fields

All three presentations share profile revision, provenance, observation sequence
and age. `preferred` uses the profile preference; `simple` and `detail` are local
presentation choices. Neither detects or changes the physical manual toggle.
Search/Close Call, Weather and Tone-Out select their documented family layout
in all three entries. Waterfall remains separate.

Each frame carries observation `status`, `layout_basis`, profile revision/status/
refresh flag, accepted source provenance, `sequence`, `age_seconds`, a canonical
`screen` or null, and `indicators`. Acquisition/import timestamps are not the
scanner clock. Indicators are exact live `alert_led`, `system_hold`,
`department_hold`, `channel_hold`; null means unknown, not Off/released.

Screen regions include canonical identifier, kind, row/column spans, alignment,
name-line allowance and reverse-color flag; profile selection/token and optional
stored text/background pair; and `value_status` plus `text`. Only qualified
`raw_source` values carry text. Missing, unsupported, invalid, blank and explicitly
empty selections remain distinct. Non-current frames cannot leak old text
through the serializer. Matching revision, canonical geometry and region
identities are checked before serialization.

Colors are six hexadecimal digits, not arbitrary CSS. Values are bounded plain
text, not HTML. Renderers must use text nodes, escape terminal control sequences
and validate incoming wire data before using dimensions, colors or tokens.
Output projection is not a substitute for consumer-side validation. The candidate
WebUI validates canonical geometry, bounds, provenance, values and coherence
across all three presentations before creating text nodes or applying RGB colors.

Some fields still carry scanner-native notation.
This API does not claim every value is already formatted exactly like the LCD.
Shared formatting and field availability must be qualified before visual
acceptance; do not infer missing values from a different mode.

The SDS200 `Rssi=-999` telemetry sentinel is unavailable, not a signal-strength
measurement; Mimic shows a placeholder. Integral RSSI values omit a redundant
`.0`, while fractional readings are preserved. This does not infer signal bars
or change the ordinary shared radio state.

The documented `InfoArea1`/`InfoArea2` Text attributes are preserved when sent,
including quick-key status strings and `SITE HOLD`. An explicit empty Text is
blank; a missing record is **data unavailable**. Live SDS200 observations can
omit these nodes while the physical LCD displays F/S/D quick-key rows. The
current selection's `Q_Key` does not describe all 100 bank states. The third
information row has no qualified PSI source yet.

### Quick-key GET groundwork (not automatic polling)

Remote Command Specification V1.02, page 6, provides the separate bank reads:

| GET form | Scope | Result |
| --- | --- | --- |
| `FQK` | Favorites lists | 100 states |
| `SQK,<FAV_QK>` | Systems in one Favorites quick key | Selectors plus 100 states |
| `DQK,<FAV_QK>,<SYS_QK>` | Departments in that Favorites/system scope | Selectors plus 100 states |

States are 0 (does not exist), 1 (disabled), and 2 (enabled). The existing
`get_favorites_quick_keys()` and new read-only `get_system_quick_keys()` /
`get_department_quick_keys()` use the scanner object's existing serialized
command path; calling code must not open a second scanner connection. These
methods are **not yet wired to the Mimic feed** or exposed as new remote commands.
Nothing polls or changes quick keys automatically.

The specification's SQK GET reply unusually includes both FAV_QK and SYS_QK.
The parser preserves that reported SYS_QK separately and requires the documented
field count; its meaning and actual firmware reply must be hardware-qualified
before using it as a display selector. A changed or unassigned scope must not
silently become key 0, a scanner object index, or a previous system's bank.

### Owner quick-key cache groundwork (not enabled)

The local `DaemonQuickKeyCache` component now separates bank freshness from PSI
freshness. It is **not wired to daemon startup, display frames or a worker yet**:
installing this candidate does not start bank reads or display F/S/D rows. It
opens no transport, starts no thread, and retains at most three immutable
100-state banks, without raw packets or exception text.

Only an already-qualified, current conventional/trunk observation supplies the
Favorites and system `Q_Key` scope. Object `Index` values never supply quick
keys. Explicit `None` is unassigned, distinct from key 0; missing, invalid,
duplicate, overlaid and non-scan observations suspend scoped reads. The owner
must renew the connection ticket only after an actual new scanner connection.

The component's initial, hardware-unqualified scheduling limits are:

- One shared five-second demand lease for all consumers. Renewing demand,
  reading a snapshot and handling PSI perform no scanner I/O.
- At most one GET in flight, with at least 500 ms between reads. Successful
  banks become eligible for refresh after two seconds; bank values and PSI
  selection each expire independently after five seconds. Bank age starts at
  request dispatch, not response completion or browser refresh.
- A worker uses `read_quick_keys_if_idle()` on the existing owner. It immediately
  yields when a foreground command holds the command lane. A dispatched GET
  occupies that lane until completion with a 250 ms response budget; this is
  not hard preemption of a transport write or a promise of zero control latency.
- A definite command rejection backs off that bank for 30 seconds. A timeout,
  malformed reply or other uncertain read disables all automatic bank reads
  for that connection, without reconnecting the scanner. These replies have no
  request IDs: retrying on the same connection could mistake a late reply for
  a new read. A changed scope or renewed demand does not lift that restriction.
- Connection, endpoint, selection and demand-generation changes discard late
  results. An old request keeps the shared in-flight slot until it exits; it
  cannot populate a newer session or revive a closed cache. A failed bank read
  does not modify or clear the otherwise-current PSI display frame.

Remaining work is explicit owner-worker integration and hardware qualification
of actual replies, displayed decade/selection and glyph mapping. Those gates
must be met before rendering bank rows. Transport write bounds, foreground
control latency, audio and Waterfall continuity also need qualification before
enabling the reader; synthetic cache tests are not that acceptance.

## Candidate WebUI presentation

The existing Scanner pane offers **Scanner presentation → Mimic-SDS** only when
the configured daemon advertises `display.frame`. The ordinary Dashboard remains
the initial presentation; its existing controls and themes are not replaced.
No configured capability means no Mimic frame requests. This local candidate
does not add a published App-catalog setting or install itself on any display.

Visible **Mimic layout** and **Alert LED treatment** selectors choose profile
preference/Simple/Detail and top-and-bottom strips/surrounding border. These are
per-page presentation choices; reloading the page restores Dashboard, profile
preference and strips. They neither write the scanner nor alter another client
or the imported profile. Outer dashboard themes remain independent of profile
field colors. Only supported COLOR mappings and confirmed individual name holds
are applied; BLACK/WHITE transformations and icon glyphs remain unqualified.

Fields use the canonical seven layouts and alignment, with two-line Simple name
bands and single-line Detail names. Larger screens can grow beyond the reference
field-width baselines. Useful option captions remain, but name bands do not gain
generic field labels. The supplied V1.02 remote-command PDF describes PSI
frequency fields as already unit-bearing `xxxx.xxxxMHz` text. The renderer
preserves source notation and precision, including opaque digits if supplied;
it does not borrow the unrelated Waterfall numeric-frequency conversion. TGID
captions are not repeated when the source already includes `TGID:`.

Only the selected, visible Scanner presentation polls, using one finite request
at a time with a two-second timeout and a 256 KiB response limit. Successful reads
are separated by 250 ms; failures retry after two seconds. This is not a promise
of a higher scanner frame rate. Hiding the page, switching panes/presentation or
signing out aborts the request, stops polling and clears live values. Late results
cannot restart a signed-out renderer. Page-cache restoration can resume an
otherwise active session, while retaining sequence freshness limits.

The browser pins the endpoint for the page lifetime, tracks feed/session and
sequence, rejects backwards sequences, and advances age with a monotonic clock.
A repeated sequence cannot extend its five-second freshness deadline, even after
an error or a hidden view cleared the display. Malformed, oversized, unavailable
or failed responses clear live text and indicators; they do not leave an old
screen looking current. The existing authenticated fetch/sign-out paths are
reused; display-only access remains read-only, with no new session authority.

When an update fails, **Profile, LED and field details** retains the last fixed
failure phase (request/timeout, HTTP status, content type, body read/size,
UTF-8/JSON decoding, frame validation, endpoint identity, sequence, or rendering),
bounded elapsed milliseconds and an interruption count. This remains visible
after recovery so a transient failure can be inspected without repeating it.
It is page-local, cleared on session stop/reload, and contains no exception text,
response body, URL, credentials or scanner/profile values. A timeout's late
response cannot be accepted as a successful update. These diagnostics identify
the failed stage; they do not by themselves establish a network or scanner cause.

On short displays, layout/LED qualifications move into **Profile, LED and field
details** to leave room for the complete scanner grid. This disclosure includes
unavailable/unqualified fields and accepted-profile status; it never includes raw
profile bytes or paths. If a window is too small, internal scrolling remains
available instead of overlapping cells or hiding controls.

Synthetic browser and deterministic lifecycle tests do not establish real
scanner LCD formatting, physical Pi acceptance or Firefox/WPE support. The
separate TUI candidate is described below; HA-specific packaging, sizing and
Ingress lifecycle are described in the [card guide](home-assistant-mimic-card.md).

## Candidate TUI presentation

The daemon-backed TUI now offers an optional **M** screen with the same canonical
seven layouts and strict profile/field/value/indicator decoding. It is absent if
the initial daemon hello does not advertise `display.frame`. The ordinary TUI
remains the default. Direct-USB profile wiring and the additional HA card are not
implemented by this consumer. Full keys and layout behavior are in the
[TUI guide](tui.md#unreleased-mimic-sds-candidate).

A lazy worker owns a separate API client using the already selected local socket
or authenticated remote transport. It negotiates each new API session before
reading, does not share the control client's request lock, and never touches
scanner or RTSP transports. API timeout and response limits are capped at two
seconds and 256 KiB; stricter user limits remain honored. Successful reads wait
250 ms before the next request; failed reads wait two seconds. This is client
poll cadence, not a scanner FPS guarantee. The worker is inactive until the view
is opened, and the UI never blocks on a transport read or close.

The Python decoder checks exact keys/types, canonical region order/geometry,
source metadata, printable bounded Unicode, RGB pairs and coherent frame
variants, then returns an immutable detached projection. JavaScript and Python
both count Unicode scalar values for the 256-character text bound. The reader
pins the endpoint, tracks stream/session and sequence, rejects replayed lower
sequences, and retains the five-second monotonic deadline for a repeated sequence
even after a clear or hidden view. Visibility generations discard late reads.
Covering the screen clears live content immediately; the worker closes its
connection after any pending finite read finishes. Only a new active-generation
read can restore data. Quitting closes the worker without restarting it.

Runtime/keyboard help is an opaque, scrollable modal drawer. Palette typing and
drawer actions cannot activate scanner or audio controls. Profile metadata shown
there is explicitly a snapshot from drawer-open time, while local TUI runtime
and logs continue updating. Only reported fields are shown: it does not invent
a daemon-version field or label client recordings as daemon-owned recordings.
Ordinary TUI event updates continue underneath, so returning does not require a
new scanner connection. Managed-display terminal failure/retry remains the
existing outer lifecycle.

Rich renders literal text and profile RGB values, with normal terminal color
quantization and `NO_COLOR` support. No ANSI/markup from the wire is interpreted.
The renderer preserves source frequency notation, useful option captions,
Simple two-line names, Detail one-line names, alignment, and independent holds.
The layout/LED selections are process-local and read-only. Synthetic renderer,
real Textual, real Unix API and packaging tests do not replace physical Pi/Linux
console or actual scanner LCD qualification.

## Shared renderer requirements

1. Select presentation locally; one display must not change another. Preserve
   reference alignment and minimum widths while allowing wider screens to grow.
2. Show observation health and profile/layout uncertainty independently. Discard
   data on stream/session replacement. Failed, stopped or out-of-order fetches
   must not leave cached data looking live. Advance age locally using a
   monotonic clock, not the scanner clock.
3. Apply profile colors and confirmed per-name hold inversion. Avoid generic
   field tags except useful option captions; do not reuse previous screen names.
4. Keep LED strips/border a local choice and use exact `A_Led` color without
   invented blink periods. Favorites pattern/identity matching remains separate.
5. Keep display choices and runtime drawers read-only. Additional front-panel
   controls require separate capability/authorization qualification.

Synthetic tests cover supported families, staleness, delayed callbacks, profile
failure/repair, nonblocking observation during reload, real XML parsing,
lifecycle cleanup and real local Unix-to-WebUI reads. They are not physical
scanner, Pi or Home Assistant visual acceptance.
