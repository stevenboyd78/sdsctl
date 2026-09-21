# Mimic-SDS shared live frame contract

Status: **local candidate only**, not a published feature or an installed theme.
This is the shared read-only data path for the candidate WebUI, TUI and
additional [Home Assistant card](home-assistant-mimic-card.md). See the
[work packet](mimic-sds-work-packet.md) and
[profile administration guide](scanner-display-profile-import.md) for the
separate profile, deployment and physical-acceptance boundaries.

## One scanner owner

An explicitly configured daemon attaches `DaemonDisplayFrames` to its existing
scanner's connection and complete PSI callbacks. By default the feed sends **no
scanner commands**, starts no polling thread or connection, and takes no audio or
Waterfall subscription. Normal daemon startup still owns scanner connection and
PSI acquisition. No profile configuration means no display-feed subscriptions.
The internal opt-in quick-key worker described below is not enabled by ordinary
daemon startup. Only bounded display fields are retained, not raw XML. Scalar state updates and
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

### Diagnosing a brief conflicting-data screen

The owner retains only the latest conflict's allowlisted structural description
and a bounded count. On a subsequent display read it can emit one journal warning
per 30 seconds, even if scanning has already recovered. The warning distinguishes
foreign channel records, a known Mode/V_Screen mismatch, duplicate permitted
records, and simultaneous weather frequency sources. Multiple reasons can apply.
Only recognized screen/mode labels and record tag names are included, never
attributes, scanner names, raw XML, profile data or endpoint addresses.

These diagnostics do not change the frame schema or permit conflicting values
to render. Logging runs outside the feed lock and never on the scanner's PSI
callback; a diagnostic sink failure does not fail the frame read. A warning
describes a previously observed conflict, not necessarily the current response.
Its count is the number of conflicting observations since the previous warning,
not the number of browser interruptions. This is diagnostic evidence, not proof
that the physical scanner stopped or that an ambiguity guard should be removed.

One narrowly qualified exception covers observed SDS200 scan transitions:
`Trunk Scan` with `conventional_scan`, and `Scan Mode` with `trunk_scan`.
For those exact pairs, the renderer follows `V_Screen` only when its matching
`ConvFrequency` or `TGID` record is present. It still rejects foreign channel
records, duplicates, missing matching records, and unqualified held/search/other
mode discrepancies. All data comes from the same complete observation; no prior
channel is carried forward and the independent freshness limit is unchanged.

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

### Hardware-aligned presentation and live indicators

The 2026-09-17 hardware comparison supersedes the original reference HTML's
centered Favorites, Site and Frequency rows: all three under-name rows align
left in Simple and Detail. The current reported site/control-channel frequency
remains visible during idle scanning by user preference, even where the physical
LCD omits it. This is an intentional difference, not proof of receiving voice.

`Property.F` supplies the Function indicator: On shows an inverted `F` using the
profile pair; Off leaves the cell blank and black. Missing, invalid or expired
states show a neutral `?`, never an inverted placeholder. Normal-scan AVOID
regions use their own System, Department and ConvFrequency/TGID Avoid attributes,
not Hold or Site Avoid. Off shows dark-gray `AVOID` on black; Avoid shows profile
colors; T-Avoid shows `T-AVOID` in profile colors. Unknown states show `?`, not an
inactive label. The same inactive treatment applies to configured, qualified
PRI, CC, WX, REC, IFX and priority-channel icons. Intentionally Empty/blank profile
slots remain empty; unqualified GPS/SCR/REP, offsets and modulation do not gain
invented toggle behavior. No scanner state is changed by these indicators.

The signal region uses current `Property.Sig` directly. V1.02 p.18 documents
0..4; existing scanner replay fixtures also report 5. This bounded 0..5 input
renders zero to five ascending bars, without an RSSI conversion, dBm calibration,
or a claim of pixel-exact LCD glyphs. Missing/invalid/stale is unknown, not zero.
Function, AVOID and signal data share PSI's qualification/freshness gate; they
do not trigger commands. WebUI, generated HA card, TUI and offline frame preview
share presentation semantics. Profile colors remain authoritative when active.
Very narrow terminal cells show a numeric signal label (for example `S5`)
instead of clipping a five-bar reading to fewer bars. Update the server and
bundled readers/generated HA resource together: the strict decoder validates
canonical alignment, so an old centered-layout reader rejects the new geometry.

The scanner clock is a separate acquisition task from the application header
clock. The GET/parser and passive sample lifecycle described below are available
locally, but **live Day/Time fields are not enabled**. F/S/D rows likewise require
the scoped bank acquisition described below; a current selection alone cannot
reconstruct them. These changes add no automatic DTM or quick-key polling and do
not resolve the remaining AST-only IDs.

### Scanner clock GET groundwork (not automatic polling)

Remote Command Specification V1.02 p.9 documents a read-only `DTM` and a reply
with eight fields: DayLightSaving, year/month/day, hour/minute/second, and RTC
Status (0 invalid, 1 valid). `GetDateTime` / `get_date_time()` parse that GET reply
through the scanner object's existing serialized transport. There is no new SET
clock command, second connection, remote operation or daemon startup hook.

`ScannerDateTime.local_time` is a **naive scanner-local calendar reading**, not
an instant in UTC or the host's local timezone. The DayLightSaving token is
preserved without interpreting its undocumented encoding. An invalid RTC has
no usable `local_time`, even if its numeric fields happen to form a valid date.
An RTC-valid response requires a real calendar date and 00–23/00–59/00–59 time.
The year must have four ASCII digits. Month/day/hour/minute/second accept one or
two ASCII digits, with optional leading zeros. An explicit SDS200 1.26.01 GET
capture returned `DTM,1,2026,9,17,3,38,10,1`: the scanner did not pad its month or
hour. The initial fixed-width parser rejected this response; offline regressions
now replay that exact response through the parser, passive cache and one-owner
qualification path. Calendar/range checks remain
unchanged; whitespace, signs, Unicode digits and overlong components are rejected.
Offline replay is not a second physical test or automatic-polling acceptance.
`DTM,OK` is a SET acknowledgement and is not accepted as a clock reading.

A separate corrected-build qualification on SDS200 firmware 1.26.01 accepted
one existing-owner UDP clock GET and observed two subsequent normal PSI frames,
with no connection or scan-context change. The operator confirmed that the LCD
date/minute matched and scanning stayed normal. This qualifies that bounded
one-shot read only: periodic acquisition, shared-worker pacing, mode/freshness
gates and live Day/Time rendering are not enabled or accepted by this result.

`read_clock_if_idle()` offers one bounded GET on an already-connected scanner,
with no queued wait for a busy command lane. Its default response budget is
250 ms and cannot be raised above 500 ms. This is not a hard transport-write
preemption deadline. It creates no worker, retry or cache, and must not run
inside a PSI callback. Calling code must quarantine uncertain outcomes until an
actual reconnect, because replies have a command code but no request identifier.

The internal `ScannerClockSamples` store is passive: no transport, thread, timer,
polling or I/O in construction/snapshots. It provides:

- endpoint/session-bound tickets, one outstanding read, and a minimum two-second
  reservation interval; a previous session's read occupies its slot until it
  finishes, but cannot populate or quarantine the new session;
- exact samples with freshness measured from dispatch, expiring at five seconds;
  no extrapolation from the workstation clock or substitution of receipt time;
- source-packet revalidation without retaining the raw packet in the sample;
- a 250 ms completion budget, connection quarantine after timeout, malformed or
  uncertain reads, and a 30-second backoff for an in-budget definite rejection;
- invalidation that discards pending values without lifting quarantine, and an
  invalid RTC that immediately replaces any previously valid clock reading.

Only the owning connection lifecycle may begin a new session; it is not a retry
button. The internal opt-in **single shared owner worker** now coordinates clock
and quick-key reads with foreground controls, normal-scan PSI, profile health
and demand. The passive clock store does not enforce those external conditions
itself. There is no independent clock polling loop next to the quick-key worker,
and ordinary daemon startup still does not enable either read.

The one-shot reply/physical comparison gate above is complete for the tested
firmware; it must not be repeated merely to proceed with implementation. Before
live Day/Time display, separately qualify periodic shared-worker behavior,
mode/freshness gates and renderer integration. Stop on an uncertain reply; do
not retry it in the same connection. No DTM SET, FQK/SQK/DQK SET, automatic AST or
scanner-clock timezone assumption is part of that qualification. The scanner's
configured 12/24-hour presentation also needs a verified source before claiming
LCD parity.

### One-shot display GET qualification (internal opt-in only)

`DisplayReadResearchAttempt` is a separate qualification path, not a poller or
source for renderer fields. One immutable policy selects exactly one of `clock`,
`favorites`, `system`, or `department` and pins an SDS200 firmware string. Ordinary
daemon construction leaves it disabled, and no API/CLI operation advertises it.
System Status research and display-read research cannot be enabled together.

An explicitly supplied policy permits one attempt through the existing daemon's
control/lifecycle locks and its directly owned UDP command lane. It reserves an
idle waterfall session without stopping an active one. The attempted sequence is
read-only `MDL`, `VER`, then at most one selected `DTM`, `FQK`, `SQK,<FAV_QK>` or
`DQK,<FAV_QK>,<SYS_QK>` GET. Each response wait is capped at 250 ms; the complete
attempt, including passive PSI observation, is bounded to at most eight seconds.
This is not transport-write preemption. No GSI, SET, KEY, AST, APR, second scanner
connection, automatic retry or follow-on bank sweep is issued by this helper.

The selected GET requires newly observed, unambiguous normal-scan PSI no more
than 1.5 seconds old. System/Department selectors come only from assigned Q_Key
values, never object indices, an unassigned-to-zero fallback or a previous bank.
After validating the reply, two further qualified normal PSI updates are required
for `reply_and_psi_observed`; a reply alone is `reply_only`. A connection change,
mode interruption or relevant bank-scope change invalidates the result even if
the previous state returns. These states are evidence, not claims of physical
screen parity or of which LCD quick-key decade the operator selected.

Private results retain only bounded typed clock/bank values after a complete
pass, plus fixed failure classes and response-field counts for malformed replies.
They contain no raw response, XML, scanner names, host addresses or exception
text. An RTC-invalid clock reply can pass protocol/continuity checks but supplies
no usable time. An unexpected SQK shape is recorded as counts and rejected, not
silently reinterpreted. All attempted/refused/uncertain outcomes consume the
one-shot object; no retry is scheduled.

The developer-only `research_display_read_daemon.py` launcher reuses the private
operator-trigger lifecycle but **does not enable System Status research**. A
fresh private `ready.json` includes PID, process start ticks and `read_kind`.
No research command runs until a deliberate administrator SIGUSR1 signal during
the readiness window. Repeated signals cannot repeat an attempt. Expiry and
shutdown cancel the wait, and existing evidence paths are never adopted or
overwritten. Verify the actual image/process identity before signaling; preserve
failed evidence and do not restart/rearm an uncertain case to try again.

`scripts/stage_mimic_app.py` can prepare that temporary image using paired
`--display-read-research-firmware` and `--display-read-kind` arguments. They are
mutually exclusive with the System Status research option. Staging stays local,
source-pinned, manually started and unmapped by default; it does not install or
start an App. Prepare the corresponding ordinary candidate as well, preserve
private data and existing card resources, and restore ordinary startup after
the bounded qualification. Clock and bank polling/display remain disabled.

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

One operator-triggered existing-owner UDP `FQK` GET on SDS200 firmware 1.26.01
returned exactly 100 valid state fields, all `0`, followed by two qualified normal
PSI updates with no connection or scan-context change. An independent bounded
receive-only observation captured the same reply and 57 normal PSI updates
without decoding errors. The first ten states are consistent with the dashed
F0 row in the operator's baseline photo, and the operator confirmed that physical
scanning stayed normal. This qualifies that bounded read/continuity observation
only: it does not establish populated-bank glyphs, a live LCD decade
selector, System/Department GET behavior, or automatic-polling acceptance.

The specification's SQK GET reply unusually includes both FAV_QK and SYS_QK.
The parser preserves that reported SYS_QK separately and requires the documented
field count; its meaning and actual firmware reply must be hardware-qualified
before using it as a display selector. A changed or unassigned scope must not
silently become key 0, a scanner object index, or a previous system's bank.

A populated System quick-key row on the LCD is not itself an assigned Favorites
selector. Following the bounded Favorites qualification, a receive-only normal
scan sample reported explicit `Q_Key="None"` for both the current MonitorList
and System. The scoped qualification guards correctly refused to select either
SQK or DQK; no request was sent. Page 6 does not define an unassigned-selector
encoding. Qualify those reads using an explicitly assigned scope or separately
established protocol evidence, without changing user settings to create a scope
as an automatic side effect. Global Favorites and clock reads do not require
those assigned selectors.

### Owner quick-key worker integration (internal opt-in only)

The local `DaemonQuickKeyCache` separates supplemental freshness from PSI freshness.
An internal caller can explicitly inject it into `DaemonDisplayFrames` to opt
into one owner worker. Ordinary daemon startup does **not** inject a cache:
installing this candidate starts no bank reads and displays no new F/S/D rows.
There is no CLI flag, App option, remote operation or new display.frame field
for enabling or reading it yet. The cache retains at most three immutable
100-state banks and, when explicitly selected, one exact clock sample, without
raw packets or exception text. No extra scanner transport, audio subscription,
profile watcher or per-client worker is created.

Attachment checks the exact scanner object, endpoint UUID and configured target;
one cache can attach to only one feed. The cache belongs to that scanner owner's
lifetime, not to a browser connection. Do not replace a quarantined cache on the
same live scanner connection to retry it. Connection callbacks create/invalidate
its tickets, and qualified complete PSI supplies selection. Default passive
operation remains unchanged.

For local qualification, constructing that cache with `include_clock=True` and
`allow_scoped_reads=False` selects only global `FQK` and `DTM` GETs. These are
internal constructor policies, not supported configuration keys. Defaults retain
the earlier bank-only behavior (`include_clock=False`, `allow_scoped_reads=True`)
when a developer explicitly injects a cache. No ordinary startup injects one.
Clock opt-in requires the exact same scanner object's `read_clock_if_idle()`;
there is no second scanner object, connection, worker, demand lease or read slot.
An internal `clock_snapshot()` does not perform I/O or renew demand, and neither
it nor the bank snapshot changes the public display-frame contract.

The global-only policy never sends SQK/DQK, even if assigned selectors appear
later. It accepts qualified normal PSI with explicit unassigned selectors, does
not infer zero, and still refuses ambiguous/missing/non-scan observations.
Changing between qualified selections does not invalidate endpoint-global values
or continually reset their schedule. Profile/mode/demand barriers still discard
them. Per-operation global refresh/backoff deadlines survive those barriers;
only a new connection resets them. This policy does not qualify SQK/DQK or infer
which ten-key bank/decade is visible on the physical LCD.

Only an already-qualified, current conventional/trunk observation supplies the
Favorites and system `Q_Key` scope. Object `Index` values never supply quick
keys. Explicit `None` is unassigned, distinct from key 0; missing, invalid,
duplicate, overlaid and non-scan observations suspend scoped reads. The owner
must renew the connection ticket only after an actual new scanner connection.

The component's initial, hardware-unqualified scheduling limits are:

- One shared five-second demand lease for all consumers. Renewing demand,
  reading a snapshot and handling PSI perform no scanner I/O.
  A qualified frame read with an accepted profile renews demand; internal bank
  diagnostics do not. Closing one browser does not cancel another reader's
  demand. After the last reader stops, bank requests cease when the lease expires,
  not necessarily immediately. The single worker remains idle until renewed
  demand or owner shutdown. A stricter display-freshness setting is honored too.
- At most one GET in flight across both kinds, with at least 500 ms from
  completion to the next read. Earliest-due selection gives both global reads
  opportunities to run. Successful banks become eligible for refresh after
  two seconds; clock reservations (including a busy-lane attempt) wait at least
  two seconds before the next clock attempt. Bank values, clock samples and PSI
  selection each expire independently after five seconds. Bank age starts at
  request dispatch, not response completion or browser refresh.
- A worker uses `read_quick_keys_if_idle()` / `read_clock_if_idle()` on the
  existing owner. It immediately yields when a foreground command holds the
  command lane. A dispatched GET
  occupies that lane until completion with a 250 ms response budget; this is
  not hard preemption of a transport write or a promise of zero control latency.
- A definite command rejection backs off only that operation for 30 seconds.
  A timeout, malformed reply or other uncertain read disables **both** clock
  and bank reads and hides their cached values for that connection, without
  reconnecting the scanner. These replies have no
  request IDs: retrying on the same connection could mistake a late reply for
  a new read. A changed scope or renewed demand does not lift that restriction.
- Connection, endpoint, qualifying-observation and demand-generation changes
  discard late results (scoped mode also invalidates changed selections).
  An old request keeps the shared in-flight slot until it exits; it
  cannot populate a newer session or revive a closed cache. A failed bank read
  does not modify or clear the otherwise-current PSI display frame.
- Before each poll, the worker checks cached profile health/invalidation before
  acquiring the scanner command lane. Slow administrator reload does not hold
  the PSI callback lock or that lane. Profile failure/repair requires fresh PSI;
  an older concurrent profile context cannot lower the invalidation barrier.
- Worker startup/runtime faults are isolated from normal frame rendering and
  scanner reconnection. Only fixed failure categories are retained, without raw
  traceback text; the worker does not restart itself after a fault. Closing the
  feed invalidates its cache and waits at most 750 ms, outside the callback lock.
  A transport write that violates its own bound may outlive that wait, but cannot
  commit its result or restart reads. The shared scanner is never force-closed
  by this worker. Unsubscription failures still stop/invalidate the worker.

An internal cache can also receive `read_scope=runtime._supplemental_read_scope`.
This reserves the exact runtime scanner, not another object at the same address.
It attempts control and lifecycle locks nonblockingly, briefly checks runtime
state, and reserves an idle real Waterfall session before yielding to the same
scanner command lane. Runtime state and Waterfall state locks are not held over
GET I/O, so receive-thread PSI and connection callbacks can progress. A busy
lock, inactive PSI/connection, non-running runtime, existing research policy or
unavailable/non-idle Waterfall reservation refuses the read; it never stops
Waterfall or reconnects the scanner to make room. All partial reservations are
released before a refusal is returned.

A refused scope suspends supplemental values and requires new qualified PSI
and display demand before trying again. The cache rechecks its session,
generation and demand after scope entry, discarding a reservation that became
obsolete in between. Unexpected scope faults are quarantined, not treated as
ordinary contention; only the typed Waterfall idle-reservation refusal is busy.
Foreground operations arriving after a GET has reserved the owner retain their
existing busy behavior. Shutdown waits for that in-flight reservation to exit;
there is no new claim of hard transport-write preemption. This coordination hook
is not installed by ordinary daemon startup and does not enable periodic reads.

The separate developer-only `scripts/research_supplemental_daemon.py` launcher
qualifies the combined reader without changing ordinary startup. Its source-pinned
staging option is `--supplemental-research-firmware`, mutually exclusive with the
one-shot clock/Favorites/System/Department and System Status research modes. A
fresh private evidence directory and verified operator `SIGUSR1` are required.
No signal means no extra GETs. Existing startup MDL/VER identity must match SDS200
and the exact firmware pin on direct UDP; the launcher does not add identity
queries. The existing display worker may admit at most six global DTM/FQK read
opportunities during one eight-second window. This is an admission/response-wait
budget, not a claim of preempting a blocked transport write.

The trigger thread supplies bounded display demand and observes results, but
never schedules a GET itself. It counts incoming DTM/FQK packets without saving
raw data; a successful machine result requires three replies of each kind, fresh
validated cache samples, and two normal PSI updates after the last read. Read
opportunities and observed replies are not a packet-captured outgoing command
count. Physical scanning continuity remains a separate operator acceptance.
Connection/mode changes, runtime contention, uncertain or rejected reads, expiry
and cancellation cannot renew/rearm the window. The worker is gated off after
the trial. Preserve unconfirmed evidence and restore the prepared normal image;
never restart a research image merely to bypass a failed trial. The launcher
does not expose samples in display frames or enable SQK/DQK, AST/APR, SET or KEY.

The bounded shared-reader hardware trial passed on an SDS200 running firmware
1.26.01 on 2026-09-17. One operator-triggered window admitted six read
opportunities in about six seconds, with three incoming DTM and three incoming
FQK replies, fresh validated samples, and two normal PSI updates after the last
read. A separate approximately 30-second receive-only observation recorded 57
normal scanning PSI updates without errors or System Status records. The
operator confirmed that physical scanning stayed normal. These are observed
replies and admission counts, not an outgoing wire-capture count. The trial is
complete and must not be rearmed. The matching normal candidate was restored,
with the temporary launcher and runtime markers absent and ordinary supplemental
acquisition still disabled. This qualifies only this short combined-reader
scanning case, not endurance, controls/audio/Waterfall under load, scoped quick
keys, displayed bank selection, or rendered clock/quick-key fields.

The **scanning-continuity** case is separate from that completed short
trial. `--supplemental-continuity` requires the stager's explicit supplemental
firmware pin and selects a distinct version suffix/read-kind in its manifest.
The research entry passes `--continuity` to the launcher: at most 60 read
opportunities within 64 seconds, requiring 30 validated replies of each kind,
two final normal PSI updates, and no observed inter-PSI gap above two seconds.
It cannot be rearmed, does nothing before the verified operator signal, and
closes on connection/mode changes, contention, cancellation or uncertain reads.
This case is not hardware-accepted and does not qualify controls,
audio, or Waterfall under load. The bounds are admission/response-wait limits,
not hard preemption of a blocked transport write. It adds no public App option.

The first continuity trial on 2026-09-17 stopped safely but was **unconfirmed**.
At about 13.23 seconds it had admitted 13 opportunities, counted six replies of
each kind and 26 normal PSI updates, and closed on `read_unconfirmed`. Its largest
observed inter-PSI gap was about 0.546 seconds; there were no remaining in-flight
reads at the recorded result. A separate 75-second receive-only observation
recorded 142 normal scanning PSI updates without parsing errors or System Status
records, plus seven incoming FQK and six incoming DTM replies. The operator
confirmed that physical scanning stayed normal. That physical pass does not
override the unconfirmed machine result or qualify continuous acquisition.

The extra FQK reply was not counted in the bounded reader result. A late reply
is one possibility, but the saved evidence does not establish its arrival time
or the underlying failure category: this trial retained only the first 12 reply
timestamps, and the launcher reported only the generic stop reason. No outgoing
wire count was established. Preserve this closed case; do not retry or rearm it.
The matching normal candidate was restored and verified: 12 current HTTP frame
samples without errors, research launcher/support/markers absent, protected
configuration/profile/recording inventory and five shared card resources
unchanged, and Home Assistant Core not restarted. Ordinary supplemental reads
remain disabled; neither Pi was changed.

Subsequent **offline-only** diagnostic changes retain the first sanitized cache
fault in the private launcher's `read_failure` report: cache quarantine,
per-bank failure categories, and clock failure/quarantine. A coherent snapshot
is taken before a later scope invalidation can erase per-bank detail. No raw
packet, scanner clock value, bank contents, or endpoint is included. A `timeout`
category alone still does not distinguish network arrival delay from local
completion delay. Tests cover both kinds of read, rejection, timeout, malformed
response, unexpected read error, preserved evidence after invalidation, and a
reply observed before a completion timeout. These changes neither loosen the
250ms budget nor allow retries, start acquisition, or expose new public fields.
Any future timing investigation requires a fresh bounded plan and evidence
case that retains timing for every allowed reply, not another trigger here.

The separate developer-only timing case is selected with
`--supplemental-timing --supplemental-continuity` and the same explicit firmware
pin. Its version suffix is `supplemental-timing` and read kind is
`shared-clock-favorites-timing`. It retains the 60-opportunity/64-second limits,
250ms read/completion budget, two-second PSI gap limit and one-trigger policy.
It is not hardware-qualified by the offline tests.

The temporary launcher delegates the existing traffic trace without changing
the scanner owner, transport, command serialization or normal trace behavior.
It stores at most 512 allowlisted phase/command/monotonic-time records in a
private `timing.json`; overflow stops further admissions and invalidates the
diagnostic. No file I/O is added to a timing callback. The original trace is
restored on success, cancellation or failure. No signal means no extra reads.

Timing phases have deliberately limited meanings:

| Phase | Observation point | Does not establish |
| --- | --- | --- |
| `scope_enter` | Background read admitted by the runtime guard | A command was transmitted |
| `tx_intent` | Existing radio trace call, before transport write | Successful write or wire delivery |
| `rx_line` | Response line reached the radio, after UDP decoding | Kernel packet-arrival time or successful parsing |
| `parsed_packet` | Typed parsing succeeded and packet callback ran | Delivery to the command's waiting queue |
| `scope_exit` | Runtime read reservation exits | Completed cache validation |
| `cache_complete` | Admitted background poll returned or raised | Successful read; inspect failure categories |
| `context_rejected` | Existing CLOCK selector refused a scanner update | A physical screen change or scanner fault |

In the explicit timing case only, the first refused update also supplies a
private `scan_rejection` summary in the result and `timing.json`. It identifies
non-PSI data, unsupported screen/mode pairing, a missing/multiple System record,
duplicate tags or excluded record types. Only fixed command/screen/mode/tag
labels, bounded counts, booleans and a monotonic timestamp are retained. System
and unknown-duplicate counts saturate at two; record count saturates at 256.
Unknown strings become `other`; XML, free-form attributes, text and scanner names
are never copied. An unrecognized selector rule is reported as
`unclassified_rejection`, not a guessed reason. These explanations do not
authorize reads: the selector remains authoritative.
The first summary cannot be replaced by later updates; snapshots are defensive
copies. Pre-arm refusals remain pre-arm and cannot enable a read.
If structural diagnostics fail, the original guard still closes the window;
only `diagnostic_unavailable` is retained, never exception text.

Private context-diagnostic schema 2 separates label recognition from read
eligibility. It recognizes the exact 14 `Mode` labels in Uniden Remote Command
Specification V1.02 p18, without trimming or case folding. Missing and empty
labels are distinct; unknown labels remain `other`. A separate `mode_class`
identifies documented labels, missing/empty values and unrecognized values.
The corrected conventional read selector uses exact `Scan Mode` / `Scan Hold`
only with `conventional_scan`; trunk modes still require `trunk_scan`.
The previous, undocumented `Conventional Scan` / `Conventional Scan Hold`
aliases are no longer admitted or classified as recognized diagnostic labels.
All other selector guards remain unchanged. Recognizing a label does not bypass
screen pairing, record validation or runtime admission, and the presentation
adapter's cross-family and Close Call exceptions are not reader exceptions.

The report also lists only fixed recognized record tags and the exact documented
`PRI`, `CC`, and `WX` enums from a single `DualWatch` record. Missing or duplicate
records have no selected attribute values; missing, empty and unknown attributes
are not treated as Off. No other attributes are retained. These private facts
can distinguish a known transition from absent/unrecognized data without copying
the presentation adapter's exceptions into the reader. Neither these categories
nor synthetic tests establish that extra reads are safe during such transitions.

`scripts/observe_supplemental_timing.py --observe-75s` is a separate explicit
receive-only observer, not a trigger. It must run in the same acceptance
container/time namespace for monotonic-time comparison. It keeps every incoming
DTM/FQK reply timestamp up to 80 records (the 60 permitted replies plus bounded
late/unexpected traffic), with only command and field count. Userspace receive
times are not kernel/hardware timestamps. It exits at 75 seconds or at its
packet/reply/PSI cap; hitting a cap is incomplete evidence, not silent sampling.
It sends no commands, retains no raw packets, and establishes no outgoing wire
count. Archive both timing files, including late replies, before restoration.

Deterministic fake-transport tests reproduce both delayed arrival and delayed
local publication after parsing: either can time out while producing different
phase orders. These tests validate the diagnostic, not the explanation of the
earlier hardware result. Do not widen deadlines, retry an uncertain read, or
enable ordinary acquisition based on these simulations.

The first timing hardware trial on 2026-09-18 stopped at 42.56 seconds with
`scan_context_changed` after 21 FQK and 21 DTM reads. All 42 complete software
phase sequences were retained without overflow; maximum scope-to-cache times
were 78.139ms for FQK and 68.519ms for DTM. The 75-second passive observer saw
144 trunk/conventional PSI updates, all 42 replies and no decoding errors.
The operator reported normal physical scanning. This is a physical pass but
**not** a completed 60-read qualification, nor an explanation of the earlier
timeout. That source did not retain the rejected update's structure; the coarse
screen kinds cannot establish which stricter selector rule rejected it. The
normal image was restored and the case closed without retry.

The subsequent context-diagnostic trial on the same day stopped after 2.58
seconds and three completed reads. Its first rejected PSI reported `trunk_scan`
with only `mode_mismatch`, one System record, eleven records total and no
duplicate/excluded tags. All reads completed in 40.8-65.1ms without a timeout;
the rejection followed the final completion. The operator noticed no abnormal
physical behavior. The complete 75-second observer saw all three replies and
141 PSI updates without decoding errors. Normal restoration passed, and the
case is closed with machine qualification still unconfirmed.

Schema 1 classified that Mode as `other`, not its literal value: it could not
distinguish a documented-but-ineligible label from missing/unknown data. Schema 2
addresses that diagnostic limitation only; it cannot retrospectively recover the
value, explain the earlier timeout or qualify read admission during a transition.

The mode-diagnostic trial then identified an exact same-family mismatch:
`conventional_scan` with `Scan Mode`. All four reads completed in 19.0-39.3ms
before that refusal, without a timeout; the operator reported that scanning
appeared to stay normal. The 75-second passive observer saw all four replies
and 140 PSI updates without decoding errors. WX Priority was reported, but no
causal attribution is made. The case is closed, normal restoration passed, and
the 60-read machine qualification remains unconfirmed.

The follow-up label correction replaces the undocumented conventional aliases
with exact `Scan Mode` / `Scan Hold` as listed on p18. It applies to the shared
research selector for all four read kinds; scoped requests still require valid
assigned quick keys, while the continuity trial remains global DTM/FQK only.
No timeout, quota, runtime exclusion, record guard, retry policy or default
acquisition setting changes. Synthetic mixed-family qualification and refusal
tests are not new hardware acceptance. The earlier trunk-mode rejection and
isolated timeout remain unresolved; a new reviewed candidate and fresh operator
readiness are required before another live trial.

The scan-label trial completed 24 reads (12 FQK and 12 DTM) before its first
refusal at 24.85 seconds. The exact pair was `trunk_scan` / `Scan Mode`, with
only `mode_mismatch`, one System and no duplicate/excluded records. Every read
completed before the refusal; no read timeout was recorded. The operator
reported normal physical scanning. Full passive observation saw all 24 replies
and 143 PSI updates without decoding errors. Normal restoration passed. This
is not a completed 60-read qualification or an explanation of older failures.

#### Opt-in transition withholding research

`--transition-wait` is a distinct temporary launcher policy, requiring explicit
continuity and timing. Staging additionally requires the firmware pin and
`--supplemental-transition-wait`. It does not change ordinary runtime files or
the strict read selector. The old research modes retain terminal mismatch
handling. This policy is locally tested, **not hardware qualified**.

Only the exact observed PSI `trunk_scan` / `Scan Mode` pair can withhold reads,
and only with unique System, Department, Site, SiteFrequency, TGID, Property
and DualWatch records and no excluded/foreign channel records or duplicates.
That is permission to wait, never to issue a read on the mismatched update.
Other cross-family pairs, held mismatches, Close Call, unknown labels, missing
core records and unsafe screens remain terminal. Ordinary unchanged handling
of single unknown tags does not make their values diagnostic evidence.

The window must already be armed and have a prior strictly qualified PSI, with
no active poll or in-flight read. Reads remain blocked until two consecutive
strictly qualifying PSI updates arrive. Repeated mismatches reset that recovery
count but cannot renew the deadline: the previous qualified PSI plus two
seconds, inside the original 64-second/60-opportunity window. Existing read
pacing, 250ms budget, runtime reservations and disabled scoped queries remain.
Failure, disconnect, unsafe context, an overlapping read or an expired wait
cannot recover/rearm. A successfully recovered wait does not reset the quota.

Private result/timing metadata separately retain the first withheld observation,
bounded episode/recovery counts and wait/resume timing phases. The first terminal
rejection remains independent and immutable. A wait clears post-read continuity
credit; old samples or old PSI cannot manufacture a success. Timing remains
software observation, not proof of transport delivery. Normal UI/API projection
and acquisition defaults remain unchanged.

### Internal supplemental projection (not yet rendered)

`supplemental_snapshot()` joins the optional cache's banks and clock at one
monotonic cutoff under its existing lock. Its identity ticket and PSI sequence
bind that cut to one owner connection, while each independently acquired reply
retains its own age. This neither performs I/O nor renews display demand.

The candidate `DaemonDisplayFrames.supplemental_values()` method checks the
accepted-profile invalidation barrier, current normal scan, endpoint and exact
session/PSI sequence before projecting a fresh cut. Auxiliary projection faults
quarantine that worker without blanking otherwise-current PSI content. No
transport or command lock is acquired by the projection, and slow profile work
runs outside the PSI callback lock. Ordinary `snapshot()`/`display.frame` and
WebUI/TUI/HA output remain unchanged; ordinary startup still has no such cache.

The immutable candidate values preserve exact naive scanner-local time and the
100 typed global Favorites states. Their ages may increase during projection;
the scanner time never advances by extrapolation. Invalid RTC, expiry, disabled
acquisition and quarantine are distinct from a valid reading. An absent bank
does not become 100 off states. No timezone, DST offset, physical 12/24-hour
setting, F0/S0/D0 decade, glyph, scoped selector, or missing field is invented.
A result is a point-in-time internal value, not authority to retain it across a
profile/session change or enable automatic reads. Rendering these values still
requires the remaining acquisition and presentation qualifications.

Offline tests cover the shared schedule, independent freshness, invalid RTC,
cross-kind quarantine, global rejection backoff through barriers, retired-session
replies, bounded shutdown, real parser callbacks during reads and yielding to a
foreground command through the actual owner lane. They are not periodic hardware
acceptance. A quarantined auxiliary reader leaves a current PSI display intact.

Remaining work is scoped GET qualification, displayed decade/selection and glyph
mapping, followed by reviewed opt-in deployment and frame projection. Completed
one-shot clock/Favorites gates do not establish periodic-read safety. Transport
write bounds, foreground control latency, audio and Waterfall continuity also
need qualification before enabling the reader; synthetic cache tests are not
that acceptance. Day/Time must remain exact scanner-local data, not a workstation
clock, inferred UTC offset or extrapolated timestamp.

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
at a time with a five-second timeout and a 256 KiB response limit. Successful reads
are separated by 250 ms; failures retry after two seconds. This is not a promise
of a higher scanner frame rate. Hiding the page, switching panes/presentation or
signing out aborts the request, stops polling and clears live values. Late results
cannot restart a signed-out renderer. Page-cache restoration can resume an
otherwise active session, while retaining sequence freshness limits.

The request allowance tolerates a slow Home Assistant ingress response; it is
not a longer freshness lease. A separate timer still clears displayed values
at their existing five-second observation deadline, even while a
request is pending. Response age includes time spent waiting for headers and
reading the body, conservatively measured from request start. A response can
arrive before the request timeout yet already be too old to display. This does
not diagnose or fix Home Assistant host responsiveness, and does not change the
TUI or Home Assistant card transport budgets.

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
bounded elapsed milliseconds and an interruption count. Timeouts retain the
phase reached at the deadline, distinguishing waiting for response headers from
reading the response body. The note also records elapsed times to headers,
first body bytes and body completion, plus a bounded byte count. An unreached
stage is explicitly marked **not reached**, rather than reported as zero.
All times are relative to request start; they do not isolate browser connection
queuing, the Home Assistant proxy, network transit or backend processing.
This remains visible
after recovery so a transient failure can be inspected without repeating it.
It is page-local, cleared on session stop/reload, and contains no exception text,
response body, URL, credentials or scanner/profile values. A timeout's late
response cannot be accepted as a successful update. These diagnostics identify
the failed stage; they do not by themselves establish a network or scanner cause.

### Optional request-path timing trace

In **Profile, LED and field details**, **Trace request timing for 2 minutes**
temporarily labels the existing frame reads with random, page-local correlation
IDs. It is off by default, is not saved, creates no extra requests, and stops
automatically after two minutes or when the page becomes inactive/hidden,
changes presentation, or signs out. The same button stops it early. If random
ID generation is unavailable, normal display updates continue without tracing.

The App echoes an admitted ID and writes bounded WARNING-level timing records
labelled `Mimic HTTP trace`, so an explicit trace works with the App's default
logging threshold without enabling general verbose logging. These requested
timing records are not themselves scanner failure warnings:

| Marker | Boundary measured inside the existing App access guards |
| --- | --- |
| `received` | The validated frame request entered the timing middleware |
| `handler` | The synchronous route began, after any worker-pool wait |
| `headers_sent` | ASGI response-start sending returned |
| `body_sent` | ASGI final-body sending returned |
| `interrupted` / `incomplete` | An exception interrupted the route or final-body sending did not finish |

These records contain only the correlation ID, fixed marker, UTC timestamp,
bounded elapsed milliseconds, HTTP status, byte count and dropped-record count.
They never contain request URLs, client addresses, cookies, response bodies,
exception messages, scanner values or raw profile data. IDs grant no access.
Authentication and Ingress guards run first; denied requests are not traced.
Only a single strictly validated header on a parameter-free frame GET is
eligible. Ordinary requests are unchanged, and frame responses are explicitly
non-cacheable through both local/Ingress and native access paths.

Tracing admits at most 512 requests per App process, separated by at least
100 ms. A separate log worker uses a 128-record nonblocking queue and exits
after five idle seconds; it does not perform log I/O in the HTTP request or
handler thread. A full queue, failed sink, rate limit or exhausted budget skips
diagnostics without changing the normal response. Existing logging configuration
and retention apply; a level above WARNING suppresses these records.

The page's last interrupted-update note includes that request's ID and whether
the exact echo was received, allowing an administrator to compare it with App
records. An acknowledgement means the App admitted the trace, not that every
record was retained. **Missing records/acknowledgements do not prove non-arrival**:
headers can be stripped, budgets exhausted, requests unfinished or records lost.
Likewise, **`body_sent` does not prove browser receipt**; ASGI sending can finish
before downstream proxies or the browser consume it. Compare the same request's
boundaries, rather than inferring the cause from unrelated successful probes.
Tracing does not extend request deadlines, observation freshness or authority.

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
