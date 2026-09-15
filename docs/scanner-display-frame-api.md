# Mimic-SDS shared live frame contract

Status: **local candidate only**, not a published feature or an installed theme.
This is the shared read-only data path for the candidate WebUI presentation and
planned TUI and additional Home Assistant card. See the
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

On short displays, layout/LED qualifications move into **Profile, LED and field
details** to leave room for the complete scanner grid. This disclosure includes
unavailable/unqualified fields and accepted-profile status; it never includes raw
profile bytes or paths. If a window is too small, internal scrolling remains
available instead of overlapping cells or hiding controls.

Synthetic browser and deterministic lifecycle tests do not establish real
scanner LCD formatting, physical Pi acceptance, Firefox/WPE support or additional
HA-card/TUI support. Those require their separate consumers and acceptance.

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
