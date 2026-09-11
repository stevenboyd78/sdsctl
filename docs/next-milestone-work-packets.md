# Next milestone work packets

This is development groundwork, not installation guidance or a release promise.
It separates work that can proceed independently from work that needs the active
managed-browser recovery path or new hardware evidence. The
[roadmap](../ROADMAP.md) remains the source of milestone order; the
[project vision](project-vision.md) retains unscheduled product ideas. Do not
assign new milestone numbers or release versions from this document.

Repository inspection baseline: `ffb3e101de7014d7513daef49d454c81bdc7c59b`
(`main`, including the merged connected-client age presentation). Browser
continuation is separately under review in
[draft PR #250](https://github.com/stevenboyd78/sdsctl/pull/250). Recheck both
baselines before implementing a packet; this is not a claim that the draft is
merged or that its installed recovery path is complete.

## Dependency map

| Work packet | Can start without browser continuation? | Safe preparation now | Gate before claiming support |
| --- | --- | --- | --- |
| TUI time and endpoint identity | Yes | Trace metadata and clock ownership; deterministic formatting, compatibility and layout tests | Exact endpoint semantics and both Pi layouts |
| Renderer field parity | Yes | Reconcile the existing audit against current models and synthetic fixtures | Per-field provenance and targeted physical observations |
| TUI waterfall | Yes, but use the existing daemon data plane | Read-only renderer design, bounded history and fake-stream tests | Subscription cleanup, resizing and scanner-mode acceptance |
| Weather/alert presentation and recording | Partly | Define unknown/unavailable states and sanitized fixture requirements | Genuine alert evidence and reviewed recording lifecycle |
| Advanced protocol/menu/analysis | Partly | Evidence inventory and lossless parser fixtures | Exact command/model/firmware proof before any mutation |
| Audio client and playback follow-ups | Yes | Bounded fanout and failure-isolation test design | One scanner stream and physical audio acceptance |
| Favorites follow-ups | Yes for copied data | Reconcile remaining gaps with the existing editor and provenance workflows | Exact backup, stale-target and restore evidence before writes |
| Alternative kiosk engines/platforms | Manual rendering only | Engine capability matrix and fixture-based visual checks | Engine-specific security, lifecycle and physical acceptance |
| Unattended browser continuation | No: active dependency chain | Complete the current security and failure-state gates | Installed end-to-end, outage, logout and physical acceptance |
| v1.0 quality and release hardening | Planning only | Maintain a coverage/acceptance scope and reproducible release matrix | Separately reviewed release-candidate criteria |

These are work packets, not ten new features to implement simultaneously. Keep
independent changes in separate reviewable branches. Do not mix a renderer-only
follow-up into the browser authorization PR.

## 1. TUI time and endpoint identity

### Source findings

- [TUI composition](../src/sds200/tui.py) uses Textual's header clock and
  `_transition_display`, which records a presentation-state change and formats
  only the time of day. That is not measured socket uptime.
- [Daemon TUI adaptation](../src/sds200/daemon_tui.py) uses `scanner_connected`
  from the authoritative snapshot, but also reports disconnection when its
  event stream is unavailable. That boolean alone cannot establish separate
  client-to-daemon and daemon-to-scanner connection start times.
- [Daemon API capabilities](../src/sds200/daemon_api.py) expose protocol versions;
  those are not application versions. The inspected
  [runtime snapshot](../src/sds200/daemon_runtime.py) also does not expose a
  daemon application version. Do not display either protocol version or the
  local client's `__version__` as the remote daemon version.
- The merged [web diagnostics formatter](../src/sds200/web_assets/dashboard.js)
  already renders connection age as `HH:MM:SS` or `Nd HH:MM:SS`, with invalid
  values shown as unavailable. This packet must not reimplement that completed
  web change or claim it is already released.

### Small implementation slices

1. **Header only:** render an aware UTC wall-clock timestamp in ISO 8601
   24-hour form, such as `2026-09-11T06:59:40Z`, while retaining the local
   application name/version. Verify date rollover and width before changing
   connection-row semantics.
2. **Status timestamps:** retain the actual meaning of each presentation-state
   transition. Prefer explicit `Status since` wording rather than relabeling
   this existing value as a connection start. Keep full UTC dates and avoid
   wrapping long values into adjacent panels.
3. **Endpoint version:** introduce optional, bounded application-version
   metadata on an authenticated daemon response, with compatibility tests for
   old clients and old daemons. Render it only for a remote daemon, with an
   explicit unavailable value when missing. Refresh or invalidate it when the
   selected connection changes; do not retain an old daemon version after an
   upgrade or use a second scanner connection to discover it.
4. **Connection duration, if implemented:** first add an explicit owner for the
   selected link's successful connection/reconnection events. Use monotonic
   time for elapsed duration and aware wall time for `Connected since`.
   Separate unavailable transport, stale scanner state and process uptime.
   Do not reset uptime on a stale/degraded label change, and do not count
   disconnected time as connected time.

### Test contract

| Case | Required result |
| --- | --- |
| UTC midnight, year rollover and non-UTC input | Full correct date/time with `Z`; no ambiguous local timestamp |
| 59/60 seconds, 3599/3600 seconds, 86399/86400 seconds | Exact duration boundaries; days do not wrap at 24 hours |
| Multi-day/month-length duration | Elapsed days, not invented fixed-length calendar months or years |
| Wall-clock correction or DST transition | Header follows current UTC; monotonic duration does not jump |
| Unknown original connection start | Explicit unavailable/observed-state semantics; no fabricated uptime |
| Stale PSI, healthy PSI, scanner reconnect, daemon reconnect | Distinct state transitions; only the selected actual link resets duration |
| Older daemon, mixed application versions, malformed metadata | Compatible connection and safe bounded unavailable/version display |
| Direct USB | No remote endpoint-version field; scanner model/firmware unchanged |
| 100x30 and 160x45, long target/name, logs/help open | No unintended panel shifts or clipped date/version; retain reachable controls |

Start with [TUI tests](../tests/test_tui.py),
[live TUI tests](../tests/test_tui_live.py),
[daemon TUI tests](../tests/test_daemon_tui.py), and the existing daemon protocol
tests. A formatter test is not a physical-display pass.

## 2. Renderer parity and waterfall

The [current renderer work packet](renderer-parity-work-packet.md) traces four
specific TUI omissions and separates them from existing Weather, recording and
remote-waterfall foundations. It supplies fixture and layout acceptance cases;
it does not change those renderers or claim new hardware support.

Use the [capability/field parity audit](capability-field-parity-audit.md) as an
evidence inventory, not a current unchecked to-do list. It contains an explicit
older baseline. For each selected omission, record its current parser/model,
snapshot projection, renderer, test fixture and physical-validation boundary
before adding a field. Candidate examples include talkgroup/unit IDs and
scanner-reported P25 or battery details; do not invent units or status meanings.

A TUI waterfall should consume [daemon waterfall](daemon-waterfall.md), not
issue competing `PWF`/`GWF` commands. Prepare tests for zero consumers, one
consumer, multiple consumers, last-consumer cleanup, resize, pause/clear,
bounded history, changed span and reconnect. Preserve relative, uncalibrated
values and display-only pointer semantics. Rendering at a faster rate must not
be described as a higher scanner acquisition rate. Binary GW2 remains deferred
unless new independently reproducible evidence changes the recorded conclusion.

## 3. Weather, menus and advanced analysis

First distinguish scanner-reported mode, a detected alert, a configured SAME
selection and an application inference. A synthetic Weather screen is not
evidence of real alert detection. Prepare private-data-free fixtures for known,
unknown, absent and repeated fields, plus mode entry/exit and delayed delivery.
Do not automatically start recording or change monitoring behavior from those
fixtures alone. Follow the [advanced protocol evidence](advanced-protocol-research.md)
and the roadmap's command-by-command limits.

For menu/analysis work, preserve raw bytes, ordered/repeated fields, exact
command arguments and model/firmware provenance. A read-only menu projection
does not authorize `MSV`, `MSB`, reboot or mass-storage operations. Keep
disruptive probes and physical scanner writes out of unattended preparation.

## 4. Audio and Favorites

Audio work can prepare deterministic slow-consumer, buffer-bound, cancellation
and sink-failure tests without producing sound. Reuse the daemon's existing
single decoded-PCM fanout. Local decoded-PCM subscriptions and automatic
playback-backend selection are separate packets: neither may interrupt scanner
control when playback is unavailable. Physical sound checks require the user.

Favorites already has extensive lossless editing, external provenance and
assisted-refresh work. Inventory gaps against the
[current editor](favorites-workspace-editor.md) before proposing new storage
machinery. Preparation uses copied/synthetic data only. Keep unknown records
and exact round-trip bytes; establish backups, stale-target refusal and exact
read-back/restore before permitting scanner writes. Do not contact a user's
RadioReference account or mutate its data as a background planning step.

## 5. Kiosk engines, platforms and release gates

Separate a manually signed-in dashboard rendering check from unattended
enrollment. Firefox rendering does not prove Chromium extension/native-host
compatibility; WPE needs its own capability and lifecycle assessment. Keep
manual login as a distinct supported path where appropriate. Never substitute
password injection, disabled TLS verification or an unqualified engine to
avoid the current recovery gates. Refer to the
[kiosk](browser-kiosk.md) and [seat](browser-device-seat.md) acceptance limits,
including the unqualified secure unattended keyring boundary.

Prepare an engine/platform matrix recording exact browser/runtime versions,
architecture, sandbox status, trust method, session type, startup/stop order,
login/logout behavior and known omissions. DNS, private IPv4 and IPv6 need
separate verified-TLS evidence. Do not modify either bench Pi just to fill a
matrix entry that can be checked with an isolated fixture.

Keep 100 percent coverage as the proposed late-project v1.0 hardening gate;
the existing 86 percent shared Python floor remains unchanged. Record Python
and browser measurement scopes separately, with skipped tests and exclusions
visible. Automated coverage does not replace real browser, scanner, Pi,
upgrade, power-loss or recovery acceptance. Release qualification follows
[the release guide](releasing.md); it must name the exact commit, artifacts,
checks and acceptance boundaries rather than infer success from a version tag.

## Review handoff

For each completed slice, retain the base and candidate commit, exact changed
surfaces, automated evidence, failed attempts, undeployed status and remaining
human checks. Ask for physical tests only when an exact reproducible candidate
and concise expected result are ready. The Pis are bench systems and may be
used for agreed acceptance work; that does not convert an unattended synthetic
test into a human visual or audio confirmation.
