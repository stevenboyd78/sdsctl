# Next milestone work packets

This is development groundwork, not installation guidance or a release promise.
It separates work that can proceed independently from work that needs the active
managed-browser recovery path or new hardware evidence. The
[roadmap](../ROADMAP.md) remains the source of milestone order; the
[project vision](project-vision.md) retains unscheduled product ideas. Do not
assign new milestone numbers or release versions from this document.

Initial inspection baseline: `ffb3e101de7014d7513daef49d454c81bdc7c59b`
(`main`, including the merged connected-client age presentation). The TUI time
packet below was updated after [PR #253](https://github.com/stevenboyd78/sdsctl/pull/253)
merged as `6f8c1b2ea606368c793a0136ff38d1463440a991`; unrelated source findings
still name the initial baseline. [PR #250](https://github.com/stevenboyd78/sdsctl/pull/250)
has since merged as `d2bc159c59ba8bec070f2eabb21a8aa4b4e30c91`, including the
reviewed continuation route, idle sign-out correction and LCARS layering fix.
Its [acceptance record](browser-device-continuation-acceptance.md) preserves the
scope of each test. Recheck the implementation baseline before starting a packet;
merging this development does not publish it or qualify unattended production use.

## Dependency map

September 14, 2026 addition: the v0.30.0 release closure is complete. The immediate
maintenance priority is the separately reported Home Assistant Waterfall-card
height issue. The requested [Mimic-SDS work packet](mimic-sds-work-packet.md)
adds profile-driven TUI/WebUI layouts, an additional Home Assistant card, a TUI
runtime drawer and the full documented front-panel key inventory. Keep its
feature slices separate from that maintenance correction and from browser-device
authorization. Older baseline/publication wording elsewhere in this packet is
historical; consult the [release record](release-0.30.0.md) for current status.

| Work packet | Can start without browser continuation? | Safe preparation now | Gate before claiming support |
| --- | --- | --- | --- |
| TUI endpoint identity and connection duration | Yes | Build on the accepted local-date presentation; trace actual endpoint metadata and link ownership | Exact endpoint semantics and both Pi layouts |
| Mimic-SDS and full front-panel controls | Yes for offline foundations | Read-only profile parser, shared slot/color/live-data map and typed key inventory | Profile acquisition, all three renderers and model-specific supervised control acceptance |
| Renderer field parity | Yes | Reconcile the existing audit against current models and synthetic fixtures | Per-field provenance and targeted physical observations |
| TUI waterfall | Candidate implemented over the existing daemon data plane; exact 100x30 small-Pi live visual and guarded-cleanup acceptance passed | Strict relative renderer, bounded history, reconnect, local pause/clear and fake-stream tests | HDMI remains unclaimed; a physical `Q`-triggered exit was not independently observed, although exact offline Q cleanup and live guarded consumer release passed |
| Weather/alert presentation and recording | Partly | Define unknown/unavailable states and sanitized fixture requirements | Genuine alert evidence and reviewed recording lifecycle |
| Advanced protocol/menu/analysis | Partly | Evidence inventory and lossless parser fixtures | Exact command/model/firmware proof before any mutation |
| Audio client and playback follow-ups | Yes | Bounded fanout and failure-isolation test design | One scanner stream and physical audio acceptance |
| Favorites follow-ups | Yes for copied data | Reconcile remaining gaps with the existing editor and provenance workflows | Exact backup, stale-target and restore evidence before writes |
| Alternative kiosk engines/platforms | Manual rendering only | Engine capability matrix and fixture-based visual checks | Engine-specific security, lifecycle and physical acceptance |
| Unattended browser continuation | Builds on merged, unreleased foundations | Preserve scoped continuation/logout acceptance; prepare missing lifecycle gates | Secure unattended keyring, abrupt power loss, cross-build state and exact installed-release acceptance |
| v1.0 quality and release hardening | Planning only | Maintain a coverage/acceptance scope and reproducible release matrix | Separately reviewed release-candidate criteria |

These are work packets, not ten new features to implement simultaneously. Keep
independent changes in separate reviewable branches. Do not mix a renderer-only
follow-up into the browser authorization PR.

## 1. TUI time and endpoint identity

### Source findings

- At the initial baseline, [TUI composition](../src/sds200/tui.py) used a
  time-only header and transition label. PR #253 replaced those with local
  RFC 2822-style dates. `_transition_display` still records a presentation-state
  change, not measured socket uptime; the date must not be relabeled as a
  verified connection start.
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

### Completed time presentation

The user accepted full local dates with a numeric UTC offset and 24-hour time,
for example `Fri, 11 Sep 2026 08:06:34 -0600`, instead of the earlier UTC-only
proposal. The local `SDSCTL` application name/version remains in the header;
scanner model and firmware remain in the Scanner pane. The timezone belongs to
the machine running the TUI, including when that TUI is viewed through SSH.

The header follows the current local clock. `Status since` (or the compact
status label plus timestamp) retains the observed transition's date and offset,
including across a later DST change. Unavailable timestamps are explicit; no
socket start or elapsed connection duration is invented. Both 100x30 and 160x45
bench displays passed visual acceptance, and a user-initiated Home Assistant
App restart passed disconnected/retrying and automatic recovery acceptance.
These are merged development changes, not a claim that a new release is published.

### Remaining implementation slices

1. **Endpoint version:** implemented in the local development candidate through
   optional bounded `application_version` metadata on the existing authenticated
   snapshot. The Connection panel shares its Endpoint row with the daemon build;
   direct scanner sessions stay unchanged. Compatibility, loss/reconnect and
   Pi geometry tests pass. Both bench Pis passed installed, fictional offline
   visual/keyboard checks on September 28, 2026; live endpoint-version acceptance
   remains separate from that presentation evidence.
   See the [TUI candidate details](tui.md#unreleased-connected-daemon-version-follow-up).
2. **Connection duration:** implemented in the unreleased local candidate for
   the TUI-client-to-daemon event stream only. `DaemonTuiRadio` owns the observed
   monotonic start after the first authoritative stream snapshot and replaces it
   only after a fresh reconnect snapshot. The preliminary API snapshot remains
   explicitly unavailable, stream loss clears the value, and scanner-only
   disconnect, stale PSI or a degraded presentation does not reset it. The
   Connection panel shares its existing Daemon row with `Link for HH:MM:SS` or
   `Nd HH:MM:SS`; no `Connected since` wall time is fabricated. Direct scanner
   sessions stay unchanged. Boundary, reconnect, invalid-source and Pi geometry
   tests pass. A live 100x30 pass first exposed a meaningless truncated symbolic
   endpoint fragment. The compact split now omits that redundant token while
   retaining the concrete Target row, and the repaired installed wheel passed
   the user's live visual check with an advancing timer and guarded restoration.
   A guarded restart of the same published App then showed event loss as
   `Link for Unavailable`, exercised the managed waiting screen, and recovered
   from a fresh authoritative snapshot at `00:00:00` before advancing normally.
   The 160x45 physical layout remains separate.

### Test contract

| Case | Required result |
| --- | --- |
| Local midnight, year rollover and non-UTC input | Full correct local date, English weekday/month, 24-hour time and numeric UTC offset |
| 59/60 seconds, 3599/3600 seconds, 86399/86400 seconds | Exact duration boundaries; days do not wrap at 24 hours |
| Multi-day/month-length duration | Elapsed days, not invented fixed-length calendar months or years |
| Wall-clock correction or DST transition | Header follows local time and offset; the recorded status transition remains fixed; any future monotonic duration does not jump |
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

The unreleased TUI waterfall candidate consumes
[daemon waterfall](daemon-waterfall.md); it does not issue competing
`PWF`/`GWF` commands. Its screen owns one existing daemon client only while
visible, validates and normalizes exactly 240 base-16 source strings for
relative presentation, bounds history to 256 rows, resizes locally, and keeps
pause/clear presentation-only. Transport loss clears unconfirmed values before
a fresh checkpoint reconnect; an invalid payload fails closed without retry.
The existing shared-session tests retain zero/one/multiple-consumer and
last-consumer cleanup coverage, while the TUI tests cover one-screen ownership,
bounded shutdown and both Pi geometries. Exact development head `85042e6`
subsequently passed authenticated live 240-bin streaming and user-reported
visual/interaction acceptance on the 100x30 small Pi. Its finite guardian
restored the unchanged prior service, and a post-termination checkpoint proved
that no stale waterfall consumer remained. The candidate was still running when
closeout began, so that evidence does not independently qualify physical
`Q`-triggered termination; the exact Textual regression retains Q/quit cleanup
coverage. The 160x45 HDMI display remains outside the live claim. Rendering at a
faster rate must not be described as a higher scanner acquisition rate. Binary
GW2 remains deferred unless new independently reproducible evidence changes the
recorded conclusion.

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

### Release-readiness sequence after continuation integration

1. **Qualify the merged source.** Verify the exact main commit, its relation to
   the reviewed candidate, full Python and namespace execution gates, browser
   checks, packaging/container validations and open security alerts. Passing
   analysis is not proof that alerts are absent. Keep prior-head and merged-head
   results separate, even when their source trees are identical.
2. **Choose and document release scope.** Separate shipping opt-in experimental
   code from claiming unattended-production support. Do not advertise secure
   keyring, power-loss, alternative-engine or cross-build acceptance without its
   own evidence. The reviewed browser acceptance does not qualify new TUI fields.
   Use the [milestone-to-release index](release-tracking.md) and the release
   guide's scope record to distinguish included, deferred and experimental
   slices. Milestone numbers do not select package versions.
3. **Prepare one new version consistently.** Follow the release guide for
   package/CLI, App catalog, changelogs, README and reviewed wiki source. Never
   reuse 0.29.5 or deploy a private validation wheel as the published artifact.
   Keep the public App catalog compatible with its advertised image, including
   disabled options; do not repeat the experimental-option startup mismatch.
4. **Test new release artifacts.** Build from the exact release candidate, verify
   wheel/sdist contents and clean installation, run image validation and freeze
   the candidate before asking for its scoped physical acceptance. Reuse prior
   evidence only when the tested surface and claim still match; never rerun a
   completed or uncertain browser profile to manufacture another pass.
5. **Publish and accept the installed release in order.** Use the release guide's
   reviewed wiki/tag/artifact/catalog ordering, then verify public package/image
   identities and the actual installed-release behavior before the final GitHub
   Release/Latest promotion. A failed publication is partial publication, not
   permission to move a tag. Retained evidence and unrelated user data are outside
   ordinary branch cleanup.

The next human check must name an exact candidate, fresh test profile if needed,
specific expected observation and restoration plan. Until that check is prepared,
source/documentation/package work can proceed without stopping the bench TUIs or
using scanner/audio/credential operations to fill an unrelated checklist.

## Review handoff

For each completed slice, retain the base and candidate commit, exact changed
surfaces, automated evidence, failed attempts, undeployed status and remaining
human checks. Ask for physical tests only when an exact reproducible candidate
and concise expected result are ready. The Pis are bench systems and may be
used for agreed acceptance work; that does not convert an unattended synthetic
test into a human visual or audio confirmation.
