# Local daemon waterfall stream

Milestone 27.2 provides demand-driven text waterfall data through a private
Unix-domain socket. The daemon remains the only scanner owner. Local consumers
never open another scanner transport and never send `GST`, `PWF`, or `GWF`
directly.

This is the renderer-neutral data plane used by the Milestone 27.4 web
waterfall workspace. The authenticated web adapter creates one validating local
client only while the pane is visible and forwards canonical records through a
same-origin bounded NDJSON response. It does not add scanner tuning, mode
navigation, MQTT FFT state, public TCP access, or binary `GW2` handling.

## Scanner protocol boundary

The implementation follows the official SDS Series Remote Command
Specification V2.00 text forms:

- `GST` retrieves the Waterfall-oriented scanner status;
- `PWF,1,ON` and `PWF,1,OFF` are the type-1 text PWF lifecycle forms; and
- `GWF,1,ON` requests one type-1 text GWF frame on the physically tested
  SDS200, while `GWF,1,OFF` is still issued during cleanup.

`GST` is promoted to a typed response only when its display-form length, line
pairs, and twelve documented trailing fields form one exact specification
shape. Other shapes remain generic lossless packets. Typed GST values remain raw
strings. In particular, frequency units, FFT magnitude, color, and cadence are
not inferred.

PWF records preserve all fields and empty positions. The physically observed
SDS200 acknowledgement is the one-field `PWF,OK` form, but the parser retains
variable future PWF shapes. GWF becomes typed only for exactly 240
comma-separated values. It accepts either 240 packet fields or those same 240
values followed by the specification-defined terminal empty field produced by
the trailing comma on the physical scanner; the lossless source `Packet` retains
that separator. Binary `GW2` is excluded because V2.00 contains contradictory
command details and the Milestone 29.6 bounded physical candidate `GW2,1,ON`
returned exact `ERR\r` on the SDS200 firmware 1.26.01 LAN-control path. The
contradictory `GWF,1,ON` spelling is already the qualified comma-separated text
request. No repeatable binary frame or material renderer benefit exists to
justify changing the current line-oriented transports.

## Shared session lifecycle

The first local waterfall client obtains a shared session lease. Session startup
retrieves one GST checkpoint, starts PWF, waits for its first typed record, then
starts GWF and waits for its first 240-value record. Failure after either start
attempts both stop wires in this order:

1. `GWF,1,OFF`
2. `PWF,1,OFF`

The SDS200 firmware tested in this milestone does not continue sending GWF after
that initial request. While the shared session is running, the daemon runtime
therefore serializes a new `GWF,1,ON` request at a conservative 250 ms interval.
The recurring deadline advances from its previous phase rather than from the
daemon loop's actual wake-up time. A late wake skips expired slots and never
issues a catch-up burst, so the 100 ms daemon loop does not steadily turn the
nominal 250 ms interval into approximately 300 ms. Successful GWF round-trip
time, scheduler lag, and cumulative skipped deadlines are retained as bounded
session telemetry.

After a successful due GWF request, the same command owner refreshes typed GST
status on a separate one-second phase-stable schedule. This lets lower, center,
upper, marker, span, and related Waterfall metadata follow changes made on the
physical scanner without asking a renderer to infer a frequency range. GST
failures are counted and redacted but do not fail the GWF session: consumers
retain the last complete typed status until a later refresh succeeds. A
semantic revision changes only when the Waterfall-oriented status fields change;
the refresh timestamp still advances after an unchanged successful response.

One missed response records redacted poll-failure telemetry without destroying
the session; three consecutive misses transition it to failed. A successful
response resets only the consecutive-failure count. Request attempts, last-
request time, total and consecutive failures, last failure time, redacted last
error, scheduler timing, GST refresh state, and the latest typed status are part
of the immutable checkpoint.

Later clients receive independent bounded queues without sending another PWF
start or creating another recurring poll owner. Closing a client affects only
that lease. The final client departure sends both stop wires. Explicit shutdown,
partial startup, transport interruption, recovery, and cleanup failure are
represented by immutable ordered session transitions and snapshots.

When the owned scanner transport reconnects automatically, the receive callback
only marks the session interrupted. The daemon poll loop performs the blocking
GST/PWF/GWF restoration later under the shared control lock. The runtime never
waits for waterfall-session state while holding its own state lock; that explicit
lock order lets the scanner receive thread publish interleaved PSI before the
awaited GWF response. The same non-blocking control-lock path owns recurring GWF
requests, keeps scanner commands off the receive thread, avoids competing with
browser/API controls, and emits an explicit running or failed session transition.

One slow client drops only its own oldest unread records. Each delivered record
includes that lease's cumulative `responses_dropped` and `overflows` counters.
Scanner receive dispatch and other consumers do not block behind a slow client.

## Socket location and permissions

Waterfall socket resolution uses this precedence:

1. an explicit absolute `--waterfall-socket-path`;
2. `$XDG_RUNTIME_DIR/sdsctl/waterfall.sock`; or
3. the resolved user state directory followed by `waterfall.sock`.

The managed parent directory uses mode `0700`; the socket uses mode `0600`.
Active sockets, symlinks, non-socket entries, and filesystem identity changes
receive the same guarded treatment as the other daemon sockets.

The Home Assistant App explicitly places the socket at
`/run/sdsctl/waterfall.sock`. It remains private inside the App container. The
future Ingress consumer will access it through the existing web process rather
than exposing the socket or scanner protocol to the LAN.

## JSON Lines protocol

The socket is `AF_UNIX` plus `SOCK_STREAM`. Each UTF-8 JSON record ends with one
newline and carries:

- `protocol`: `sdsctl.waterfall`;
- `version`: `1`;
- a positive per-client `sequence`;
- an aware ISO 8601 `observed_at` timestamp;
- a stable `kind`; and
- a JSON-object `payload`.

Every connection begins with `session.checkpoint`. Its payload contains the
authoritative shared-session snapshot, latest typed GST metadata, publisher
counts, consumer count, lifecycle timestamps, last failure, GWF poll interval,
request attempts, round-trip and scheduler timing, transient and consecutive
failure counts, GST refresh state, semantic status revision, and redacted last
poll and refresh failures. Later records are `waterfall.pwf`, `waterfall.gwf`,
or `session.transition`.

PWF and GWF payloads include the radio-owned `source_sequence`, raw `values`,
source receive timestamp, cumulative lease loss counters, and an additive
current `session` snapshot. The live snapshot lets existing renderers retain
the initial checkpoint while updated renderers follow later GST frequency-range
changes; consumers that do not recognize it may ignore it under protocol
version 1. The validating client rejects an absent initial checkpoint, repeated
checkpoint, unsupported protocol/version/kind, oversized record, malformed JSON
or UTF-8, naive timestamp, and any per-client sequence gap.

The default encoded-record limit is 64 KiB and the default server limit is eight
concurrent local clients. These are framing and resource bounds, not claims
about scanner cadence or FFT semantics.

## Bounded diagnostic client

The daemon client can validate and print the private stream without opening
scanner hardware:

```console
sdsctl daemon-client waterfall --duration 10 --count 100 --json
```

`--duration` is a wall-clock deadline and `--count` is a record ceiling; the
first boundary reached closes the connection. Closing the final connection
releases demand and sends `GWF,1,OFF` followed by `PWF,1,OFF`. The command
defaults to ten seconds and 100 records. `--waterfall-socket-path` selects an
explicit socket, while `--max-record-bytes` can lower the 64 KiB client framing
limit for negative tests.

## Current validation status

Host-independent tests cover exact commands, strict GST/PWF/GWF parsing including
the terminal separator, first-record confirmation, phase-stable recurring polls,
skipped-deadline behavior, successful round-trip aggregation, isolated low-rate
GST refresh and dynamic frequency-range delivery, transient-miss tolerance and
failure threshold, lock ordering, partial-start rollback,
both-stop cleanup after a failure, shared demand, retry, interruption and
recovery, per-client overflow isolation, canonical JSON, client ordering, socket
mode, multi-client fanout, final-client stop, and socket removal.

Physical Milestone 27.2 qualification completed on August 26, 2026, over the
LAN control transport with an SDS200 running firmware 1.26.01 and manually
placed in its available Waterfall mode. The scanner returned typed GST metadata,
`PWF,OK`, and GWF lines containing 240 lowercase hexadecimal strings plus the
trailing separator. Five bounded direct requests returned one fresh frame each,
establishing the request/response behavior instead of sustained GWF push
behavior. A bounded Milestone 27.4 follow-up capture on August 28, 2026,
reconfirmed that token shape for the web renderer without assigning magnitude
or calibration semantics to the hexadecimal syntax.

A ten-second daemon-client run received 30 GWF frames in order, each exactly 240
values wide, with no client drop or overflow. Overlapping clients shared one
scanner lifecycle and retained independent contiguous sequences. A longer run
spanned scanner reconnect and observed interrupted, starting, and running
transitions before continued 240-value delivery. Daemon restart disconnected the
active client, issued `GWF,1,OFF`, `PWF,1,OFF`, then `PSI,0`, removed every
private socket, and admitted a fresh bounded client after restart. The scanner
was returned to normal scanning, the temporary host daemon was stopped, and the
repository Home Assistant App was restored with connected Ingress state and live
entities. Raw captures contain programming data and remain uncommitted; these
sanitized structural observations are the committed evidence.

Physical Milestone 29.5 qualification on August 31, 2026, used the same SDS200
and firmware through the Home Assistant OS App. Three simultaneous first-party
Waterfall cards shared one scanner session and sustained 4.0, 4.0, and 4.1
frames per second with a displayed frame age of 0.0 seconds. With the scanner
centered at 94.9000 MHz and its span set to 1.44 MHz, all three cards received
the exact typed GST lower, center, and upper values corresponding to 94.1800,
94.9000, and 95.6200 MHz. The scanner display truncates its edge labels to one
decimal place; this accounts for its visible 94.1 through 95.6 range without
altering the more precise protocol values used by clients.

The physical scanner exposed the following span and display-label pairs around
that center frequency:

| Span | Scanner display labels |
| --- | --- |
| 360 kHz | 94.7–95.0 MHz |
| 720 kHz | 94.5–95.2 MHz |
| 1.44 MHz | 94.1–95.6 MHz |
| 2.88 MHz | 93.4–96.3 MHz |
| 5.76 MHz | 92.0–97.7 MHz |
| 8.64 MHz | 90.5–99.2 MHz |
| 17.28 MHz | 86.2–103.5 MHz |

These observations validate direct use of the scanner-reported GST range and
do not require a renderer-maintained span table. The separate one-second GST
refresh and the session snapshot carried by live GWF records let a later span
change replace renderer scale data without restarting the session.

## Milestone 31.1 physical acceptance

Physical Milestone 31.1 acceptance completed on September 1, 2026, from
pull-request head `91ec4f1c6ae6568ead5868b9f5814ada8596592f` through one
deliberately named Local App. The amd64 host ran Home Assistant OS 18.2, Core
2026.8.3, Supervisor 2026.08.0, and Docker 29.6.2; the physical SDS200 ran
firmware 1.26.01. The repository-managed App remained stopped during the test,
so scanner control, PSI, Waterfall polling, and RTSP/RTP audio retained one
application owner.

The authenticated web renderer sustained 4.0 frames per second with a
15-second elapsed-time window. History filled progressively, pruned by receipt
time, froze without a hidden backlog while paused, resumed without catch-up,
and rebuilt progressively after Clear. Its display-only pointer worked on both
canvases with mouse and keyboard input. At the restored 1.44 MHz span, Home and
End resolved the typed bounds as 94.1800 and 95.6200 MHz around the 94.9000 MHz
center; one-bin arrow movement changed the value by approximately 0.0060 MHz.
Pointer interaction did not change scanner frequency, span, mode, or hold
state.

Live scanner changes to 720 kHz and 2.88 MHz replaced the renderer scale without
restarting the stream. Their exact typed bounds resolved to 94.5400 through
95.2600 MHz and 93.4600 through 96.3400 MHz respectively. Returning to 1.44 MHz
restored 94.1800 through 95.6200 MHz, and frame delivery remained 4.0 frames per
second throughout.

Home Assistant loaded the aggregate card module at exact SHA-256
`dffbeaa294773419eab0ce8dec4a32317c421faaba5cd74373b46829b6095cad`.
Three unchanged 60-, 120-, and 240-frame cards and one graphical-editor-created
15-second duration card rendered together at 4.0 frames per second. The editor
defaulted new cards to 30 elapsed seconds and accepted Home Assistant's exact
string-serialized `history_seconds: '15'` selection. Per-card pause and Clear
remained independent; the other three cards continued live without losing
history, and the duration card accumulated no paused backlog.

Hiding the final visible card group for longer than the duration window released
demand while the scanner remained connected. Returning to the view
reauthenticated automatically, began with empty histories, and restored 4.0
frames per second without stale rows. A guarded Local App restart then forced a
new scanner and stream generation; all four cards reconnected without manual
reload, rebuilt fresh histories, and restored the live 94.1800, 94.9000, and
95.6200 MHz scale. A direct request from outside the Home Assistant Ingress
boundary returned HTTP 403 as required.

Intermittent GWF command timeouts remained isolated at
`consecutive_failures=1`; the qualified 4.0-frame-per-second cadence recovered
without bursts, client errors, or a scanner reconnect. This evidence does not
turn relative hexadecimal values into calibrated FFT power and does not expand
the accepted text `PWF`/`GWF` transport boundary.

Closure removed the temporary duration card and restored the published v0.26.1
App as the sole scanner owner. The three retained frame-count cards continued
to render through the exact released aggregate module with SHA-256
`dbbb246abbf82fff9040c2d3a4ccb7f94ef634bf56795c0c356737bb5faac37f`.
The stopped Local App was uninstalled, its exact Home Assistant source and
local staging directories were deleted, and the App catalog was reloaded.
Post-cleanup audit found no installed Milestone 31.1 App or temporary source
while the published App remained started.
