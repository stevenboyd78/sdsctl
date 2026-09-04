# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/) as the public API matures.

## [Unreleased]

## [0.29.2] - 2026-09-04

### Changed

- Identify the TUI application as `sdsctl v<app version>` in the top header.
  Keep scanner model and firmware in the Scanner panel and endpoint/target in
  Connection. Short terminals retain a single-line Scanner summary; the
  100-by-30 dashboard gives it a compact full-width panel below Audio or Logs.
- Document stopping a managed console display before changing its font, to
  avoid restoring a saved screen with the previous character/color encoding.

## [0.29.1] - 2026-09-04

### Fixed

- Pack the wide TUI's Live PSI / Controls panel directly below Scanner State,
  alongside a Network Audio panel spanning both rows. Operational Logs now
  begins on its own full-width row even when network audio is available.

### Changed

- Let Keyboard Reference and Operational Logs remain open independently on
  terminals at least 120 columns wide and 32 rows tall. `?` toggles only help
  and `G` toggles only logs; opening the complete reference may require scrolling.
  Smaller terminals retain their space-saving, mutually exclusive drawers.

## [0.29.0] - 2026-09-03

### Added

- Add an Ingress-only connected remote-client inventory in App Diagnostics,
  grouped by client ID with access scopes, service counts, and connection age.
  Keep identities out of shared runtime snapshots and remote-client APIs.
- Add an observe-only `display-client-preflight` command for managed Raspberry
  Pi TUI deployments. It verifies an exact physical console, reports the
  responsive layout selected by its measured geometry, authenticates to the
  remote API, event, and audio services, checks the runtime contract, optionally
  inspects local playback, and returns only redacted evidence.
- Add `tui --managed-display` service-manager semantics and a hardened
  `sdsctl-display@.service` Raspberry Pi console template. Temporary connection
  failures use exit 75 and a delayed restart; permanent profile, TLS,
  authentication, authorization, or service failures use exit 78 and stop.
  Unexpected local failures and an intentional quit also remain stopped. Boot
  ordering keeps late Plymouth and cloud-init status output from overwriting
  the managed TUI without creating a `multi-user.target` ordering cycle, and
  all three standard streams reach the console so Textual's standard-error
  display frames remain visible.
- Add canonical and beginner wiki guidance for a dedicated service account,
  isolated virtual environment, private client files, preflight, interactive
  validation, boot startup, journald, recovery, credential changes, upgrade,
  disablement, exact removal, and default-closed Home Assistant cleanup.

### Changed

- Keep the Diagnostics Connection rows compact and top-aligned like Services,
  alongside the connected-client inventory on wider displays and stacked on
  smaller screens.
- Enable per-connection TCP keepalive and supported user timeouts on both ends
  of remote TLS services so silently broken links enter existing recovery paths
  without treating a healthy quiet scanner as disconnected.
- Make the Textual TUI transport-aware on compact Raspberry Pi displays. Direct
  USB sessions now omit network-audio playback, WAV-recording, saved-playback,
  and audio-control rows and shortcuts instead of spending screen space on
  unavailable features; Ethernet and daemon-backed audio sessions retain them.
- Use a compact two-column layout on short terminals from 100 through 119
  columns. The physical 100-by-30 Raspberry Pi geometry places Connection beside
  Channel Details, gives System / Site / Channel a fixed full-width row, pairs
  Scanner State with Live PSI / Controls, and gives Network Audio the full lower
  row when that service is available. Long hierarchy values are ellipsized so
  they cannot move the lower panels or footer.
- Turn Operational Logs into a bounded `G` drawer on the physical Raspberry Pi
  layout. It starts hidden, replaces Network Audio when opened, shows the newest
  four single-line records across the full width, continues collecting while
  hidden, and is mutually exclusive with the `?` keyboard reference. Larger and
  narrower layouts retain their existing visible bounded log panel.
- Physically validate the refined 100-by-30 dashboard, Operational Logs drawer,
  and mutually exclusive `G`/`?` switching on the managed Raspberry Pi display.
  The fixed dashboard and log drawer remain in the initial viewport; the complete
  Keyboard Reference remains intentionally scrollable at this terminal height.
- Show a named remote-daemon TUI's resolved private-LAN `host:port` as `Target`
  in the Connection panel while leaving direct USB and standalone-host sessions
  unchanged.
- Extend the existing proactive 120-second PSI renewal to direct serial
  transports. Physical SDS100 USB timing showed complete 500 ms pushes expiring
  regularly after about three minutes; renewing before that boundary avoids the
  TUI's stale warning, ten-second outage, and full serial reconnect.
- Distinguish the scanner's own `Rec` state as `Scanner recording` from the
  sdsctl-owned WAV workflow as `Audio recording` in every responsive layout.
- Physically accept the exact candidate on the 100-by-30 Raspberry Pi display
  with both a direct SDS100 USB session and an observe-only, daemon-backed
  SDS200 session through the production Home Assistant App. The USB pass kept
  500 ms PSI continuous across proactive serial renewals without reconnecting;
  the remote pass retained the complete network-audio workflow, finalized a WAV
  and metadata sidecar, and ended with the temporary listener, credential, and
  secret-bearing artifacts removed and the production App restored.

## [0.28.1] - 2026-09-02

### Fixed

- Preserve a real browser's exact same-origin `Origin` header when submitting
  the password-authenticated native HTTPS dashboard login form. The login page
  now uses a same-origin referrer policy instead of converting its own basic
  form POST into an opaque origin that the mandatory CSRF check rejects.

### Security

- Retain the exact HTTPS host and origin checks, self-only form action, secure
  strict-same-site session cookie, no-store responses, HSTS, and denial of
  cross-origin requests while correcting the same-origin browser path.

## [0.28.0] - 2026-09-02

### Added

- Add an explicit TLS 1.3 authenticated remote-daemon service for trusted
  private-LAN clients. Versioned challenge/proof authentication, independently
  revocable mode-`0600` client credentials, exact `observe` or `control`
  scopes, aggregate and per-identity limits, and bounded API, event, accepted-
  PCMU, and shared-Waterfall leases fail closed without exposing secret or
  scanner-private state.
- Add packaged `sdsctl daemon-client` and `sdsctl tui --daemon-client`
  selection through strict named remote profiles. Local Unix-domain sockets
  remain the default; remote profiles validate their CA, expected TLS hostname,
  referenced credential, service capabilities, deadlines, reconnect, and
  ordered resynchronization behavior without plaintext fallback.
- Add a separate `compose.remote.yaml` native-Linux Docker Engine deployment
  for one unprivileged scanner-owning daemon and authenticated private-LAN CLI,
  TUI, or native-dashboard clients. Exact-address preflight, read-only
  configuration and secret mounts, persistent state, scanner RTP UDP 50000,
  explicit daemon-client publication, and complete cleanup remain independent
  of the ordinary local Compose paths.
- Add disabled-by-default advanced Home Assistant App mappings for the
  authenticated daemon-client service on container port 50443 and a password-
  authenticated native HTTPS dashboard on container port 8443. App startup
  verifies enabled options against Supervisor's authoritative network mapping
  and private container address before constructing either listener.
- Add an Ingress-only advanced-access workspace for App-owned server identity,
  native-dashboard password, and multiple named, least-privilege client
  identities. One-time credential reveals and downloads are explicit and
  memory-only; granular revocation and rotation do not expose stored secret
  values or interrupt unrelated identities.

### Changed

- Reuse one transport-neutral client and daemon-owned service boundary across
  local sockets, native hosts, isolated containers, the Home Assistant App,
  Raspberry Pi displays, CLI, TUI, and browser clients. Remote daemon clients
  consume only their authorized status, event, accepted-PCMU audio, typed-
  control, and Waterfall services while recordings remain daemon-owned, all
  without opening another scanner session.
- Serve the advanced Home Assistant native dashboard as an independently
  authenticated exact HTTPS origin without the Ingress context, Home Assistant
  management tab, bridge-key workflow, or Core-integration routes. Ordinary
  Ingress remains Supervisor-peer-only and continues to recover independently
  alongside remote clients after an App restart.
- Document beginner local-only operation and advanced one-daemon/multiple-
  display deployments for native Linux, Docker, Home Assistant, Raspberry Pi,
  CLI, TUI, and browser consumers, including enrollment, certificate trust,
  firewall direction, rotation, revocation, rollback, and cleanup.

### Security

- Keep every remote listener opt-in and reject incomplete, contradictory,
  wildcard, loopback, multicast, reserved, documentation, public, conflicting,
  or unexpected bind and mapping state. Home Assistant's optional TCP mappings
  remain `null` by default, and the App binds only its Supervisor-assigned
  private container address when explicitly enabled.
- Preserve private-LAN-only support. Home Assistant Supervisor publishes an
  enabled mapping across host interfaces, so operators must restrict access
  with host firewall rules. Internet exposure, router port forwarding, trusted
  reverse proxies, wildcard listeners, shared client identities, and copying a
  server private key to clients remain unsupported.

## [0.27.0] - 2026-09-01

### Added

- Add explicit 15-, 30-, and 60-second Waterfall history modes to the web
  dashboard and first-party Home Assistant card. Elapsed history uses accepted
  monotonic receipt time, preserves delivery gaps, and remains capped at 240
  frames.
- Add an optional display-only Waterfall frequency pointer across both spectrum
  and history Canvases. Pointer, touch, and keyboard interaction interpolate the
  validated scanner-reported lower and upper bounds without tuning, holding,
  searching, changing span, or claiming calibrated frequency measurements.

### Changed

- Preserve the Home Assistant card's existing 60-, 120-, and 240-frame
  configurations as the backward-compatible default while new picker-created
  cards start with 30-second history. Pause freezes visible history without
  accumulating a hidden backlog; Clear, stream-generation changes, and teardown
  remove retained history deterministically.

## [0.26.1] - 2026-09-01

### Added

- Add an `all` optional Python runtime extra whose members are the exact union
  of the independently installable `tui`, `web`, `mqtt`, and `playback` extras.
  It intentionally excludes development tools, operating-system packages, Home
  Assistant installation, containers, audio servers, FFmpeg, and other external
  programs.
- Add beginner-oriented wiki guides for first connection, ordinary `sdsctl`
  use, Home Assistant, containers, audio and recordings, Favorites and
  RadioReference, operations and diagnostics, and the Python API.

### Changed

- Replace the 1,278-line package README with a 211-line project landing page
  that helps a beginner select an installation target, install the base or all-
  runtime Python package, make the first scanner connection, and reach the
  appropriate detailed guide. PyPI now renders that same concise account.
- Reorganize reviewed wiki navigation around deployment targets and user goals,
  while retaining detailed version-controlled technical references. Enforce a
  350-line README ceiling and validate documented Python extras against package
  metadata so instructions cannot silently drift from installable targets.

## [0.26.0] - 2026-08-31

### Added

- Add bounded Waterfall timing and status telemetry: successful GWF round-trip
  time, scheduler lag, skipped poll deadlines, low-rate GST refresh attempts and
  failures, and a semantic Waterfall-status revision now travel with live
  daemon records and appear in the authenticated web diagnostics.
- Add one digest-qualified `sds200-cards.js` Home Assistant resource that
  imports the compact Scanner, responsive Display, and authenticated Waterfall
  cards through their exact manifest-declared URLs. Existing individual
  resources remain supported selective-registration paths, duplicate loading is
  idempotent, and the App still never edits Home Assistant resource records.
- Add a bounded exact-byte GW2 research substrate that preserves complete raw
  datagrams and stream boundaries, classifies structural observations without
  inventing FFT semantics, and enforces explicit record-size, count, elapsed-
  time, and inactivity limits. A guarded physical SDS200 firmware 1.26.01 LAN
  probe sent one reviewed `GW2,1,ON` candidate, received exact `ERR\r`, issued
  paired cleanup, and restored the published Home Assistant App with fresh
  text-Waterfall frames.

### Changed

- Retain the physically qualified phase-stable text `PWF`/`GWF` path as the
  authoritative Waterfall data plane. The tested GW2 candidate established no
  binary framing or renderer benefit, so production transports do not guess an
  alternate syntax, negotiate undocumented binary records, or assign
  unsupported magnitude, calibration, byte-order, or element-width semantics.

### Fixed

- Keep the daemon's 250 ms text-GWF schedule anchored to its prior deadline
  instead of adding the interval after each daemon-loop wake-up. Late wakes skip
  expired slots without issuing catch-up bursts, avoiding the approximately
  300 ms drift created by the 100 ms runtime loop. Refresh typed GST metadata at
  a separate bounded cadence so changes to scanner span update the live lower,
  center, upper, and marker frequencies in the web dashboard and Home Assistant
  Waterfall card. A failed GST refresh preserves the last valid scale and does
  not interrupt GWF delivery.
- Normalize the Waterfall card graphical editor's exact string-serialized 60,
  120, and 240-frame history selections back to bounded numeric capacities, and
  accept Home Assistant's host-owned `grid_options` section-layout metadata,
  while continuing to reject malformed or unsupported Waterfall options.
- Clarify that authenticated Waterfall card discovery follows Home Assistant's
  Ingress panel registry: the intended running App must have **Show in sidebar**
  enabled, and directly opening a hidden Local App does not make it a discovery
  candidate. This preserves the existing exactly-one-running-App boundary.

## [0.25.0] - 2026-08-31

### Added

- Add the first-party responsive **SDS200 Waterfall** Home Assistant card over
  the existing authenticated App Ingress and single-owner daemon waterfall
  service. Independent visible cards hold separate demand leases over one shared
  scanner-side session; hidden, removed, and disconnected cards release their
  streams. The bounded Canvas renderer provides compact, standard, and tall
  densities; theme, cyan, green, amber, and monochrome palettes; 60, 120, or 240
  frames of history; relative scale and lifecycle telemetry controls; pause and
  clear actions; bounded reconnect; and a graphical editor without accepting
  URLs, credentials, scanner addresses, or private Ingress identifiers.
- Add a versioned first-party Home Assistant Core integration with one standard
  browsable `media-source://sdsctl/live` item. Home Assistant resolves the item
  to a bounded Core-owned proxy URL and exact `audio/mpeg` type; a private,
  rotatable Core-to-App bridge capability and one shared daemon-owned MP3
  encoder remain hidden from target players, MQTT, entities, diagnostics,
  browser storage, and public network listeners. Explicit digest-confirmed
  install, update, rollback, removal, rollback discard, and key rotation never
  run silently or restart Core automatically.
- Add an authenticated Home Assistant Ingress-only lifecycle workspace for the
  custom integration and private bridge. It is omitted from direct and generic
  web-dashboard sessions, retains exact SHA-256 confirmation and two-step
  destructive-action arming, and keeps restart and reauthentication guidance
  reachable across all built-in themes and supported viewports.

### Changed

- Update the generic Docker Hub workflow's reviewed, immutable action pins to
  `docker/setup-buildx-action` 4.3.0 and `docker/build-push-action` 7.3.0 while
  retaining the existing validation and tag-gated publication boundary.
- Reuse the System web theme's 21 Textual-derived color schemes as bounded
  per-card palette choices in all three first-party Home Assistant cards. The
  compact Scanner card gains a graphical-editor palette field; Display retains
  its Color and two monochrome presets; Waterfall retains its four spectrum
  presets; and every card keeps Home Assistant theme-following or its existing
  default. Palette selection changes presentation only and adds no scanner,
  App, network, credential, or browser-storage behavior.
- Keep the live daemon-event status in the balanced right-hand overview column
  when a non-System web theme hides the System palette selector, preventing the
  compact status from shifting inward or wrapping into a narrow orphaned track.
- Add a System-only web palette selector centered in the dashboard overview.
  Follow-device remains the default; 21 explicit choices mirror Textual's full
  built-in TUI scheme list across System surfaces, scanner presentation,
  semantic status colors, controls, and Waterfall. The independently persisted
  choice is hidden for non-System themes, stays responsive on phone and compact
  displays, preserves WCAG AA through derived web label tokens, and does not
  create new managed-theme packages or alter dashboard behavior.
- Use one compact geometry for buttons inside every web-dashboard workspace
  while preserving selected, action, and disabled state colors. The Ingress-only
  Home Assistant lifecycle pane now owns bounded vertical scrolling, uses denser
  inputs and status spacing, and keeps its Core-restart and reauthentication
  guidance reachable across all built-in themes and supported viewports.

### Fixed

- Register all three first-party Home Assistant cards through manifest-declared,
  SHA-256-qualified resource URLs so direct HTTP and external HTTPS dashboard
  origins load the same reviewed module bytes without retaining a stale Auto
  Display definition in browser caches.
- Replace Home Assistant Ingress-incompatible browser confirmation dialogs with
  visible two-step in-page confirmation for integration removal, rollback-image
  discard, and bridge-key rotation. Retain exact-digest checks and clear armed
  actions whenever their identity changes.
- Close Home Assistant live-audio downstream transport leases after the target
  disconnects or returns to idle. Physical Home Assistant OS 18.2 acceptance on
  Core 2026.8.3 and SDS200 firmware 1.26.01 proved canonical MP3 playback on a
  reachable VLC-TELNET target, normal stop and final-lease cleanup, App restart
  recovery, unchanged single RTSP/RTP ownership, and regression coverage for
  browser audio, recording, controls, waterfall, MQTT Discovery, and all nine
  verification cards. The run also documents that Home Assistant `internal_url`
  must be reachable by the selected media target.

## [0.24.0] - 2026-08-29

### Added

- Close Milestone 28 with private operator acceptance of the complete assisted-
  synchronization boundary. A credentialed `getTrsTalkgroups` refresh against
  public RadioReference system `12042` proved the explicit read-only preview;
  reviewed assisted decisions and a full confirmation token then drove exact
  copied-tree backup, staging, activation, readback, conditional provenance
  publication, lifecycle adoption, and single-use invalidation. Controlled
  copied-tree failure injection proved exact reverse recovery after mutation.
  Reversible physical SDS100 USB qualification on firmware 1.26.01 proved one
  minimal forward record insertion and separately reviewed inverse through the
  production editor and durable writer, including sole-owner and removable-media
  qualification, retained backups and reports, exact preactivation and
  postactivation checks, unmanaged-media preservation, absent versus empty
  provenance handling, byte-identical final Favorites and provenance baselines,
  filesystem synchronization, and safe unmount. A real preserved 118-field
  `F-List` row remains conservatively diagnosed as unvalidated because its extra
  field semantics are not yet evidenced. The constrained Textual layout now
  keeps external fields and structural record decisions reachable and explains
  that an adopted import moved into the aggregate plan rather than disappearing.
- Add Milestone 28.3's guarded execution path for one exact aggregate assisted
  RadioReference plan. A separate assisted review derives a full deterministic
  confirmation token bound to the requested copied-tree or USB target, exact
  refresh/lifecycle evidence, ordered decisions, baseline and intended Favorites
  bytes, baseline and intended provenance (including absent versus empty), and
  blockers. Execution freshly verifies the active lifecycle, editor baseline,
  target, and persisted provenance without rereading RadioReference; delegates a
  Favorites-changing plan exactly once to the existing copied-tree or USB
  executor; supports provenance-only publication without a manufactured storage
  write; independently verifies readback; and adopts the exact result into the
  lifecycle and editor session. Conditional provenance publication reconciles an
  uncertain successful return, reverses a verified Favorites write through the
  same backend when provenance remains at baseline, and otherwise reports typed
  incomplete recovery with primary and recovery artifacts. Separate Textual
  review/token/execution controls serialize attempts, retain terminal evidence,
  consume the refresh after success or failure, and defer close until an active
  non-cancellable storage transaction reaches a terminal state. Synthetic
  copied-tree and USB adapter coverage plus a real copied-tree integration test
  preserve backup, staging, displacement, rollback-manifest, operation-report,
  and exact-readback behavior.
- Add Milestone 28.2's explicit, planning-only RadioReference decisions to the
  local Favorites editor. One retained current refresh can now drive reviewed
  conventional `C-Freq` Name/frequency and trunk `TGID` Name/decimal choices,
  field-local or detached ownership, explicit record detach, provider-removal
  delete versus keep-local, unbound-record ignore, and compatible selected-anchor
  import with a provider-populated exact template and reviewed bindings. A new
  renderer-neutral aggregate planner recomputes one intended Favorites snapshot,
  complete intended provenance, schema/comparison evidence, blockers, and exact
  `FavoritesWritePlan` from the immutable refresh baseline. It supports multiple
  compatible field decisions, rejects duplicates, contradictions, foreign or
  stale evidence, and exactly rebinds provenance after structural changes. The
  Textual panel shows decisions, unresolved supported choices, Favorites and
  provenance change status, and blockers while labeling every result
  `UNEXECUTED`. Refresh replacement, edits, reload, invalidation, and exit close
  retained lifecycle ownership and discard dependent decisions. This milestone
  exposes no executor: planning never writes copied-tree or USB bytes, creates or
  replaces provenance, emits durable reports, or rereads the provider.
- Add Milestone 28.1's optional RadioReference refresh to the local Favorites
  Textual editor. One explicit copied-tree or freshly qualified USB source can
  be paired with non-secret account settings, environment-variable secret
  references, one reviewed observation request/dataset, and one canonical
  provenance path. Opening the editor remains passive. Each button or `Ctrl+G`
  action performs one bounded read only after a fresh local snapshot exactly
  matches the durable editor baseline and no unreviewed edits exist. The
  renderer-neutral controller serializes refreshes, retains only the current
  successful lifecycle for later exact planning, closes failed or replaced
  owners, redacts failures, retains prior evidence on failure or cancellation, and
  invalidates results after editor/source transitions. The read-only TUI shows
  provider/dataset identity, times, revisions, all six record classifications,
  exact local/external targets, and field ownership, values, absence, and
  unmapped evidence. No preview action mutates Favorites bytes or publishes
  provenance; acceptance and write-plan composition remain deferred.
  Operator-approved live qualification against RadioReference system `12042`
  exercised `getTrsTalkgroups` with all category, tag, and decimal filters. The
  production decoder now retains a bounded 65,536-element ceiling beneath its
  independent 4 MiB document limit, preserves live content-free `xsi:nil`
  talkgroup subfleet and slot evidence as `None`, and accepts ID-only nested
  talkgroup tags without weakening the complete top-level tag contract. The
  complete provider-to-preview path returned normalized observations and preview
  records while before/after validation proved the copied Favorites tree
  unchanged and no provenance file created.
- Redesign the web Controls pane around four explicit System, Department, Site,
  and Channel scope cards. Each card keeps its complete current target visible,
  reports `Held`, `Not held`, or `Unavailable` independently of color, and
  provides Previous, desired-state Hold/Release, and Next actions. Scoped
  navigation resolves only authoritative scanner indexes to the existing typed
  `SYS`, `DEPT`, `SITE`, `TGID`, or `CFREQ` daemon operations; the original
  unscoped Previous/Next HTTP routes remain channel aliases. Shared viewport
  protections keep the controls non-scrolling at all four reference sizes and
  preserve the enlarged-text reachability escape across all six themes.
  Physical Home Assistant OS 18.2 acceptance on amd64 against SDS200 firmware
  1.26.01 exercised both navigation directions for all four scopes through an
  isolated App built from the reviewed source commit. Channel Previous changed
  the held target from Orem/Lindon Police 1 to Orem Police Car to Car and Next
  restored it; the temporary Channel hold was then released. System,
  Department, and Site commands completed in both directions while retaining
  their authoritative targets under the existing held configuration. The
  repository App was restored as the connected sole scanner owner afterward.

## [0.23.0] - 2026-08-28

### Added

- Add Milestone 27.4's authenticated, theme-aware Waterfall workspace as the
  sixth responsive web pane. A visible authenticated pane creates bounded
  demand on the daemon's private local waterfall service; hiding it, navigating
  away, disconnecting, or closing the final consumer releases that demand.
  Direct and Home Assistant Ingress sessions receive strict same-origin SSE,
  preserve ordered loss and lifecycle telemetry, and decode exact hexadecimal
  240-value GWF records without exposing the Unix socket or opening another
  scanner transport. Canvas renders a relative, explicitly uncalibrated
  spectrum and rolling history with pause/resume, clear-history, and full-screen
  controls, immediate theme recoloring, valid GST frequency context, raw-source
  preservation, responsive no-scroll geometry, and deterministic
  error/reconnect cleanup. The shared header now uses balanced internal padding,
  centers connection state, and right-aligns theme selection. Binary GW2,
  calibrated RF or FFT claims, tuning, persistent history, Home Assistant/TUI/
  GUI renderers, and public waterfall exposure remain deferred.
- Add Milestone 27.3's responsive web workspace. Scanner, Controls, Audio,
  Recordings, and Diagnostics now occupy one keyboard-accessible, browser-local
  pane shell that fits without document or active-pane scrolling at normal zoom
  in the 390x844, 800x480, 1366x768, and 1920x1080 reference viewports, while an
  accessibility escape restores scrolling when enlarged text or browser zoom
  needs it. The stable System theme now uses the established scanner-display
  hierarchy with automatic Search/Close Call, Weather, and Tone-Out
  presentations, a persistent Simple or Detail scan fallback, and explicit
  field-group inspection without dropping any of the 35 radio fields. Recording
  inventory is paginated, configured zero Tone-Out tones render as **Detect**,
  and the selected pane is persistent. Add an original, asset-free
  Pip-Boy-inspired declarative CSS package after Amateur Radio, giving the
  built-in picker a deterministic six-theme order. A post-theme structural
  cascade contract keeps valid managed CSS inside the shared viewport shell;
  missing or mutated managed stylesheets now repair the document, metadata,
  picker, and stored selection to System and can be explicitly retried. Theme
  switching, authentication, Home Assistant Ingress, controls, audio,
  recordings, and single-owner daemon/scanner boundaries remain unchanged.
  Physical Home Assistant OS 18.2 acceptance against SDS200 firmware 1.26.01
  exercised every theme and pane, adaptive Search, Close Call, Weather, and
  Tone-Out presentation, all semantic controls and field groups, browser audio,
  recording pagination, saved playback/download, reload persistence, App
  restart, and final sole-owner restoration.
- Add the Milestone 27.2 qualified text-waterfall data plane. Exact typed GST,
  PWF, and 240-value GWF handling feeds one demand-driven radio-owned session
  with deterministic rollback, final-consumer stop, interruption/recovery, and
  isolated bounded subscriber loss telemetry. A private mode-`0600`
  `waterfall.sock` provides a versioned size-bounded JSON Lines stream to local
  daemon consumers, including Home Assistant App process wiring and a
  time-and-record-bounded validating `daemon-client waterfall` diagnostic.
  Binary GW2, FFT interpretation, tuning, public exposure, storage, and product
  rendering remain deferred.

### Changed

- Make browser ordered-event recovery explicit and duplicate-free. A terminal
  EventSource error now closes the failed source, resets its sequence checkpoint,
  and schedules exactly one same-origin stream recreation after two seconds;
  stale callbacks, hidden pages, page teardown, malformed messages, and direct
  restarts cannot create overlapping sources or timers. Status polling remains
  active until the replacement EventSource opens, whose first event supplies a
  fresh authoritative snapshot.
  Physical Home Assistant Ingress acceptance deliberately held the App stopped
  for more than ten seconds, then verified that the unchanged page recovered
  ordered updates without reload; a second normal App restart preserved the same
  document and recovered again. Deterministic gallery capture now holds its demo
  transport in one inert-open state, verifies an unchanged visible status
  message, pins canonical documentation media preferences, requires stable
  Waterfall Canvas pixels and consecutive compositor frames, and
  deterministically re-encodes validated Chrome pixels. Waterfall captures also
  assign the rounded workspace a visually inert, capture-only local paint layer
  so fractional edge tiles remain byte-repeatable across isolated Chrome
  profiles. The authoritative gallery now provides the same Full HD, desktop,
  compact/Raspberry Pi, and DPR2 phone review set for all six themes plus two
  Waterfall references, so all 26 files can expose responsive layout and
  formatting regressions without cross-process PNG encoding creating false
  failures or weakening browser event-stream coverage. The first complete phone
  review exposed long theme names competing with the brand and status;
  phone-width headers now use one compact brand row above a shared connection
  status and theme-selector row,
  with the supporting subtitle and visible selector label removed only from
  that narrow presentation. Every built-in label remains complete while more
  height is returned to the working pane and desktop and compact geometry stay
  unchanged.
- Complete Milestone 27.2.2 audio-lifecycle and release-integrity hardening.
  Fanout and dynamic-router destinations now receive PCM through independent
  bounded workers, so producer paths never wait for destination code; overflow
  drops only the affected sink's oldest complete samples with telemetry, while
  submission failures enter an ordered, redacted quarantine with deterministic
  monotonic bounded exponential backoff. The WAV worker remains the sole writer
  and finalizer after startup, drains and closes exactly once, retains ownership
  after a finite stop timeout, completes finalization when a blocked write later
  returns, and gives a later stop a deterministic outcome. Every external
  workflow action is pinned to a reviewed full commit with a readable version
  comment, both stages of both Python container images share one reviewed
  multi-architecture base digest, Dependabot covers both Dockerfile roots as
  well as Python and GitHub Actions dependencies, and CI and release validation
  enforce the shared measured 86 percent coverage floor. The README project
  status was aligned with the then-current v0.22.0 release metadata and scope.
  Dependency locking remains explicitly deferred to a separately designed
  cross-Python reproducible-build milestone rather than freezing this public
  library's supported ranges to one development environment.
- Treat Broadcastify source and metadata Basic credentials as exposed whenever
  the assigned transport uses ordinary HTTP. Source and metadata transport now
  require an explicit operator acknowledgement, legacy remote-audio profile
  schema version 1 loads safely with future construction blocked, schema version
  2 persists the acknowledgement, and credential-free CLI migration can record
  or revoke saved policy without inventing an unverified TLS endpoint.
- Qualify the Milestone 27.2 data plane on a physical SDS200 running firmware
  1.26.01. Physical GWF records carry 240 values plus the documented trailing
  separator, and `GWF,1,ON` returns one frame rather than enabling a sustained
  push stream. The daemon now issues a serialized get every 250 ms while shared
  demand exists, records each attempt and redacted failure telemetry, tolerates
  fewer than three consecutive misses, and avoids a runtime/session lock-order
  deadlock exposed by interleaved PSI. Bounded single-client, overlapping-client,
  reconnect, daemon-restart, both-stop cleanup, normal-scanning restoration, and
  repository Home Assistant App restoration all completed without observed
  client loss or overflow. Raw hardware captures remain uncommitted.
- Add Milestone 27.1 adaptive scanner screen-profile parity. One fixed read-only
  Home Assistant Screen Kind sensor exposes the existing normalized radio-state
  classification with an `unknown` fallback. The SDS200 Display card gains an
  opt-in Auto layout with a configurable Simple or Detail scan fallback while
  preserving all explicit layouts. The web dashboard applies the same profile
  to activity headings and group priority without hiding any of the 35 shared
  radio fields, and the existing mode-aware terminal presentation is retained.

### Security

- Harden managed-theme validation and installation around a consistent two-stage
  source snapshot. Validation now retains one source-directory identity, opens
  entries descriptor-relatively without following links, charges actual reads to
  one aggregate package byte budget, and parses and hashes only a private
  snapshot. Installation copies those exact validated bytes into a separately
  verified same-filesystem publication stage before a filesystem-qualified
  atomic no-replace rename, rejecting
  directory or file replacement, same-size mutation, truncation, growth,
  membership changes, special files, premature EOF, and stage mutation with
  descriptor-bound cleanup and replacement rollback. Unknown concurrent target
  substitutions, including empty destination directories, are preserved in an
  operator-visible conflict quarantine rather than overwritten or recursively
  deleted. Randomized publication stages persist token, interface, package,
  device, and inode bindings. Independently randomized removal records bind the
  exact target device and inode and retain their observed directory identity
  during the active attempt. Recovery applies artifact-specific complete-record
  and empty pre-record rules; malformed or populated unrecorded stages,
  duplicate or mismatched removal state, and unauthenticated detached-purge
  entries are preserved and block mutation for operator reconciliation. Removal
  writes identity-bound transaction evidence before retention, interrupted rollback
  recovery validates the package identity and schema before promotion, and
  iterative descriptor cleanup handles deep invalid trees without recursive path
  traversal or false success. Previously absent managed roots and interfaces are
  published from retained randomized candidates only after filesystem
  qualification. The lifecycle lock rejects link substitution, configured paths
  remain operator-facing in diagnostics, and documentation states the trusted
  same-account concurrency boundary. Web, Home Assistant, and TUI schemas and
  activation semantics remain unchanged, including both explicit Home Assistant
  executable-code trust gates. Add a durable
  [implementation review disposition ledger](docs/implementation-review-ledger.md)
  that treats the supplied review as untrusted input and records every finding
  as implemented, already resolved, deferred, rejected, or not applicable.
- Bound peer-controlled RTSP response framing to 64 KiB through the header
  terminator and 4 MiB of declared body by default, reject an oversized declared
  length before any additional body receive, close framing, read, and CSeq
  failures deterministically, and keep rejected response contents out of framing
  diagnostics. Numbered UDP XML reconstruction now has 256-fragment,
  10,000-element, 64-level-depth, 4 MiB, and ten-second limits with deterministic
  discard and recovery. After transport framing delivers a line, shared XML
  response assembly additionally bounds lines, source bytes, parsed elements,
  nesting depth, and lifetime; a single watchdog clears idle state, and
  incremental parser callbacks establish structural completion instead of a
  closing-tag suffix.
  Decoded-line callback failures are isolated with redacted telemetry, and STS
  parsing requires the specification-defined display shape and nine reserved
  fields without echoing rejected display content.

## [0.22.0] - 2026-08-24

### Added

- Add Milestone 26.16 explicit managed Home Assistant theme activation. Exact
  operator-approved package and module digests, secure descriptor-relative
  reads, collision-safe atomic deployment, and a strict private activation
  ledger provide guarded activation, reapproval, status, deactivation, and
  active-package removal refusal. Built-ins remain App-owned, resource
  registration remains manual, JavaScript is never executed by Python, and
  package discovery or replacement never changes deployed code automatically.
- Add Milestone 26.15 automatic managed terminal-theme activation. Valid
  managed `tui` package IDs can be selected through `--theme`, configuration,
  or `SDSCTL_THEME` for Rich scanner information and the full-screen Textual
  interface. One immutable startup registry binds complete semantic palettes
  and strictly scoped presentation-only TCSS, isolates malformed packages,
  fails unknown selections before scanner access, preserves plain-text meaning,
  and makes `T` return a managed theme to the exact built-in dark fallback.
- Add Milestone 26.14 automatic managed web-theme activation. Each web process
  combines built-ins with valid managed `web` packages discovered under the
  resolved XDG theme root, adds them to the existing picker and pre-paint
  bootstrap, and serves only the declared CSS through the same-origin theme
  route. Managed stylesheets stay disabled until selected. Delivery reopens the
  exact nonsymlink directory chain, verifies the complete startup package digest
  and directory identity, and fails closed after mutation, replacement, removal,
  or substitution while preserving CSP, no-store, `nosniff`, System fallback,
  authentication, Ingress, and scanner-ownership boundaries.
- Add the Milestone 26.13 managed third-party theme discovery and lifecycle
  foundation. The new `sdsctl themes` command family validates, inventories,
  privately stages, installs or replaces with rollback, and exactly removes
  local unpacked `web`, `home-assistant`, and `tui` packages under the resolved
  XDG configuration root. Malformed manual additions are isolated, built-ins
  cannot be shadowed or removed, symlinks and special or oversized content are
  rejected, concurrent mutation is excluded, and Home Assistant JavaScript
  requires an explicit executable-code trust acknowledgement. Managed packages
  are discoverable; activation remains separately bounded by renderer.
- Add the Milestone 26.12 modular TUI theme packaging foundation. The existing
  dark and light terminal presentations now live under
  `themes/tui/<theme-name>/` with versioned manifests, complete declarative
  semantic palettes, theme-only Textual CSS, and one validated immutable
  registry. Existing `DEFAULT_*` objects, Rich CLI selection, Textual toggling,
  exact role styles, responsive layout, accessible text cues, and scanner
  boundaries remain unchanged.
- Add the Milestone 26.11 modular Home Assistant theme packaging foundation.
  The existing compact SDS200 Scanner and SDS200 Display Lovelace modules now
  live under `themes/home-assistant/compact/` and
  `themes/home-assistant/sds200-display/` with versioned manifests and one
  validated immutable registry. The registry drives packaged-source reads and
  ordered installation while preserving byte-identical modules, public custom
  elements, `/local/sds200/` URLs, manual resource registration, graphical
  editors, responsive rendering, and every scanner-ownership boundary.
- Add the Milestone 26.10 modular web-theme packaging foundation. System,
  LCARS-inspired, Matrix-inspired, First Responder, and Amateur Radio now live
  under `themes/web/<theme-name>/` as independently validated versioned
  manifest and declarative CSS packages. One immutable registry drives picker
  order, same-origin stylesheets, pre-paint metadata, and safe System fallback
  while preserving public IDs, browser-local selection, visual behavior,
  accessibility, response security, and all scanner-ownership boundaries.
- Add two fixed read-only Home Assistant MQTT Discovery sensors for configured
  Tone-Out Tone A and Tone B values on the existing radio-state topic. Both
  bundled Lovelace cards accept the sensors additively, the Tone-Out display
  layout presents both values, and numeric zero with an optional `Hz` suffix is
  shown as `Detect` without changing the raw entity state. Existing fourteen-
  entity card configurations and all scanner-ownership boundaries remain valid.
  Physical Home Assistant OS 18.2 acceptance with SDS200 firmware 1.26.01
  confirmed zero and nonzero entities, both card presentations, restart
  persistence, and the unchanged single-owner boundary.
- Add Milestone 26.8 exact semantic controls across direct and daemon-backed CLI
  and Textual surfaces. System, Department, Site, and Channel controls now choose
  an explicit scanner-confirmed hold or release state; volume and squelch expose
  specification-backed, model-bounded exact levels and daemon-backed TUI
  adjustments. New versioned local daemon operations share the existing control
  lock and authoritative completion snapshot without adding a scanner owner,
  raw-key passthrough, Home Assistant entity, MQTT topic, or remote transport.
  Physical SDS200 1.26.01 UDP acceptance passed all hold scopes plus reversible
  volume and squelch changes after accepting firmware `VOL,OK` and `SQL,OK`
  acknowledgements and confirming levels with screen-independent scalar getters.
- Add a separate responsive `SDS200 Display` Home Assistant card with Simple,
  Detail, Search/Close Call, Weather, and Tone-Out layouts; Color, Black on
  White, and White on Black palettes; and Card or viewport-bounded 4:3 fit. The
  original compact card configuration remains compatible, both cards remain
  read-only, and no Uniden artwork, branding, or fonts are copied.
- Physical development acceptance on amd64 Home Assistant OS 18.2, Core
  2026.8.3, Supervisor 2026.07.5, Docker 29.6.2, and SDS200 firmware 1.26.01
  confirmed separate delivery of the unchanged compact and responsive display
  cards, all thirty live editor layout/palette/fit combinations, fully visible
  viewport fit at 390x844, 800x480, and 1920x1080, twenty-one discovered
  components, live Ingress state, and recording and card persistence across a
  Local App restart. Tagged repository-managed release acceptance remains a
  separate release gate.
- Add four fixed read-only Home Assistant MQTT Discovery sensors and matching
  optional Lovelace card fields for Site, Frequency, Modulation, and Service
  Type. Existing component identities and controls remain unchanged; each new
  nullable mode-dependent sensor combines daemon and field availability so
  omitted, null, or empty values cannot appear current.
- Add optional finite SDS100 GSI/PSI battery telemetry to the complete shared
  radio snapshot, daemon API/SSE/generic MQTT payloads, and accessible web
  dashboard while preserving literal zero and clearing omitted values. Milestone
  26.5 deliberately keeps SDS150 GCS as unpolled request/response telemetry and
  System Status as a deferred session-owned analysis surface until timestamp,
  staleness, ownership, cancellation, reconnect, and physical-acceptance
  contracts exist.
- Add complete web-dashboard presentation for all 34 renderer-neutral
  `RadioStateSnapshot` fields. Stable accessible groups now expose hierarchy,
  channel identity, RF/service, talkgroup and unit identifiers, receiver levels,
  P25, mute, scanner recording, detected subaudio, Weather/SAME, and Tone-Out
  state while preserving literal zero/false-like values and clearing stale
  mode-specific values at every authoritative snapshot, SSE, polling, and
  reconciliation boundary.
- Add the Milestone 26.3 local interactive Favorites Workspace editor. One
  explicit copied-tree or freshly qualified Linux USB source can be browsed and
  searched with schema diagnostics, exact provenance, and raw record detail;
  supported Name Tag replacement, HPD leaf deletion, and exact-template leaf
  creation remain immutable and in memory with undo/reset/discard. Exact write
  plans require a separate deterministic confirmation, execute only through the
  existing verified copied-tree or USB executor, retain USB artifacts in private
  host state, and surface operation, backup, rollback, report, recovery, and
  exact post-write reload evidence without adding scanner, daemon, web, FTP
  write, synchronization, or background ownership.
- Add the Milestone 26.2 evidence-backed physical-scanner capability and
  interface field-parity audit. It traces all 34 renderer-neutral scanner-state
  fields through Rich CLI, terminal monitor, Textual TUI, web, daemon API/SSE/
  MQTT, and Home Assistant surfaces; separates physical, specification,
  synthetic-fixture, implementation, and application-only evidence; records
  semantic-control and advanced-protocol gaps without expanding support claims;
  and establishes the bounded Milestone 26.3 Favorites Workspace editor handoff.
- Add the Milestone 26.1 authenticated LAN web-dashboard foundation as an
  explicit native-TLS host mode on one private, unique-local, or link-local
  interface. The mode requires one exact HTTPS origin, a password supplied only
  through an environment-variable reference, a browser-trusted certificate and
  unencrypted bounded private key, a freshly salted scrypt password verifier,
  and bounded server-side sessions. It protects every dashboard route, enforces
  the exact origin on login and mutations, performs one admitted password
  derivation at a time outside the application event loop, revokes active SSE
  and audio responses with their session, disables proxy trust, and leaves
  default loopback, generic-container, and Home Assistant Ingress behavior
  unchanged.
- Physical SDS200 firmware 1.26.01 validation on 2026-08-22 exercised two
  independent authenticated HTTPS sessions with simultaneous SSE and browser
  audio streams through `https://192.168.0.40:8443`. Exactly one daemon-owned
  UDP scanner-control socket and one RTSP session remained active. A temporary
  recording survived one session's logout while that session's streams ended;
  the other session remained authorized, continued receiving audio, stopped and
  downloaded the finalized RIFF/WAVE file, and then logged out independently.
  Web shutdown left the daemon healthy, daemon shutdown removed all four private
  sockets, and neither logs nor responses exposed the temporary password.

## [0.21.0] - 2026-08-21

### Added

- Add the complete renderer-neutral Favorites Workspace and verified-storage
  foundation across Milestones 21 and 22: lossless SDS100/SDS200 Favorites
  parsing and hierarchy projection, navigation/search/diagnostics/comparison,
  exact import/export, immutable write planning and record editing, verified
  copied-tree and USB mass-storage write execution, and bounded read-only FTP
  storage. Unknown source material remains preserved, and write paths retain
  stale-target, backup, staging, verification, rollback, and device-safety
  boundaries.

- Add the Milestone 23 external Favorites synchronization foundation with
  documented RadioReference SOAP/WSDL contracts, bounded offline request and
  response handling, source-neutral observations, durable external provenance,
  assisted refresh/accept/detach workflows, exact reviewed conventional and
  talkgroup mappings, explicit structural import templates, provider-removal
  decisions, and the production documented stdlib HTTPS SOAP exchange.
  Synchronization remains user-initiated and assisted rather than automatic,
  scheduled, polling, or background behavior.

- Add the Milestone 24 advanced protocol and analysis foundations: evidence-led
  `GLT,FL`, `FQK`, richer ordered/repeated `ScannerInfo`, `URC`, bounded
  `AST`/`APR` analysis sessions, `PWF`/`GWF`/`GW2` waterfall handling,
  receive-only/menu projection work around `MNU` and `MSI`, and system-status
  plus RF-power analysis foundations. Unsupported or insufficiently evidenced
  protocol forms remain deferred instead of inferred.

- Rename the user-facing repository and product identity from `sds200-python`
  to `sdsctl` while preserving compatibility-sensitive identities: PyPI
  distribution `sds200`, Python import package and source path `sds200` /
  `src/sds200`, the `sdsctl = "sds200.cli:main"` entry point, Home Assistant
  `sds200` compatibility identifiers, and SDS100/SDS150/SDS200 model names.

- Add the Milestone 25 generic container deployment foundation: an unprivileged
  multi-platform daemon image, source-built Docker Compose daemon/client/web
  topology, host-loopback web publication, native-Linux USB passthrough,
  physical Docker acceptance, Docker Desktop network-boundary documentation,
  rootless Podman network and USB paths, alternate Docker Compose provider
  acceptance, persistent USB daemon/sidecar integration, device lifecycle and
  degraded-PSI readiness recovery, and explicit Linux security-policy and remote
  runtime boundaries.

- Add the Milestone 25.18 cross-runtime container compatibility matrix,
  including scanner-independent rootless Podman acceptance through alternate
  Docker Compose v5.5.0 and remote API/Compose paths, plus an explicit
  unsupported boundary for remote client-side USB because Podman rejects
  `--group-add keep-groups` in remote mode. Physical Windows/macOS scanner
  acceptance, `podman-compose`, Docker Desktop USB/IP, and RTSP/RTP remain
  explicit non-claims where they were not validated.

- Add persistent serial-daemon degraded PSI startup and readiness recovery for
  expected scanner-not-ready timeouts and explicit command rejections, including
  the SDS200 post-USB-attach serial/mass-storage selection window. Before the
  first confirmed PSI frame, serial auto-recovery retries on the configured
  `psi_recover_after` cadence without reopening scanner control or restarting
  the daemon; unexpected startup failures remain fatal.
- Add `sdsctl daemon-client health` readiness reporting and use it for the
  generic image healthcheck. Readiness requires a running daemon, connected
  scanner, and confirmed active PSI stream, while `daemon-client status` remains
  a diagnostic query that can succeed for a reachable degraded runtime.
- Distinguish configured PSI restart intent from confirmed PSI stream activity.
  `SDSScanner.psi_active` now becomes true only after a parsed PSI
  `ScannerInfo` frame and clears across disconnect, failed start, reconnect,
  stop, and close boundaries instead of becoming true merely because a PSI
  start is in flight.

- Add pure renderer-neutral Favorites record editing over exact storage
  snapshots with immutable source-provenance targets, stale and ambiguous target
  rejection, evidence-backed Name Tag replacement, conservative HPD leaf
  deletion, and template-backed leaf creation with deterministic hierarchy-safe
  insertion. Untouched source bytes, positional extensions, physical line
  endings, document order, and unknown material remain preserved exactly while
  intended snapshots continue through the existing write-planning and schema
  safety boundary without filesystem, device, backup, staging, or write
  execution.

- Add renderer-neutral immutable Favorites write planning over exact baseline and
  intended storage snapshots, retaining existing comparison and schema evidence,
  deterministic safety blockers, and exact stale-target preconditions without
  adding filesystem access, backup/staging, execution, or storage mutation.

- Add renderer-neutral Favorites import/export round trips over the existing exact storage snapshot, preserving catalog and HPD bytes, document order, duplicate filenames, unknown records, positional extensions, and physical line endings without introducing storage writes or a new archive format.

- Lossless read-only parsing for SDS100/200 Favorites source files preserves
  physical record order, ASCII positional fields, blank and trailing fields,
  per-record line endings, unknown commands, and undocumented extra positions
  without applying hierarchy or scanner-storage semantics.
- Renderer-neutral read-only `.hpd` hierarchy projection distinguishes
  conventional and trunked systems, departments, channels, trunk sites, site
  frequencies, and band-plan records while retaining originating raw records,
  observed supplemental records, and explicitly unclassified source records.
- Lossless `f_list.cfg` catalog projection exposes ordered Favorites List display
  names and `.hpd` filenames while retaining complete positional `F-List`
  records, metadata, duplicate mappings, and unclassified source records.
- Pure in-memory Favorites workspace binding combines catalog entries with
  explicitly named `.hpd` hierarchy documents using exact filename equality and
  reports missing targets, ambiguous duplicate documents, duplicate catalog
  mappings, and orphan supplied documents without filesystem semantics.
- Immutable read-only Favorites storage snapshots carry exact `f_list.cfg` and
  ordered named document bytes through the existing parser, hierarchy, catalog,
  and workspace projections while rejecting absolute and traversing storage
  filenames without case folding, trimming, repair, or filesystem access.
- Read-only copied-tree Favorites storage loads an offline `favorites_lists`
  directory using an exact regular `f_list.cfg` plus deterministic immediate
  lowercase-`.hpd` regular files, rejects managed symbolic links, preserves exact
  bytes and filenames, and performs no format interpretation or writes.

- Renderer-neutral Favorites navigation projects resolved workspace bindings into
  immutable source-index-addressed trees for Favorites Lists, Conventional and
  Trunk systems, departments, trunk sites, and channels while preserving exact
  names, source provenance, and mixed trunk child ordering.
- Immutable renderer-neutral Favorites queries provide display-name substring
  search, navigation-kind filtering, and inclusive subtree filtering while
  retaining deterministic navigation preorder, exact source names, original node
  identity, duplicate names, and explicit stale-subtree failure.
- Immutable renderer-neutral Favorites schema diagnostics validate required
  metadata, evidence-backed record shapes, supported name tags, and observed
  scanner extensions over preserved workspaces while retaining exact
  document/record/field provenance and warning or informational compatibility for
  unvalidated extensions and unsupported commands.
- Immutable renderer-neutral Favorites comparison and preview aligns exact
  preserved catalog and unambiguous HPD source records by raw bytes, reports
  deterministic add/remove/replace record changes with exact baseline/candidate
  provenance, and surfaces duplicate HPD filenames as explicit ambiguities
  without storage access, normalization, repair, or writes.

## [0.20.2] - 2026-08-10

### Added

- Home Assistant-specific MQTT scanner controls add authoritative System,
  Department, Site, and Channel Hold switches plus Previous Channel, Next
  Channel, and Reconnect Scanner buttons. Dedicated QoS 0 non-retained command
  topics translate into fresh internal typed daemon requests without enabling
  the generic request-envelope MQTT command topic, adding raw scanner commands,
  or creating another scanner owner. Previous/Next reuse the existing bounded
  TGID/CFREQ current-channel resolver and invalidate cached navigation context
  after scanner disconnect or daemon-event resynchronization.
- First-party read-only SDS200 Lovelace card packaging and Home Assistant
  `/local/sds200/sds200-card.js` delivery without HACS or Home Assistant Core
  API access; the card uses Home Assistant's supported state context and built-in
  graphical form editor to select the Discovery entities, and exposes no scanner
  controls.
- Home Assistant App configuration translations provide user-facing names and
  descriptions for scanner host, MQTT topic prefix, and recording directory;
  the recording directory description explicitly identifies Home Assistant
  `/media` as the path root and shows the default resolved location.

## [0.20.1] - 2026-08-10

### Added

- Home Assistant App `recording_directory` option for a media-relative recording
  library under `/media`, defaulting to `sdsctl/recordings`, with a writable
  Supervisor media mapping.

### Changed

- Home Assistant App dashboard layout now groups daemon runtime with scanner
  connection, keeps scanner reconnect with connection state, separates active
  capture from finalized recordings, and gives the recording library a dedicated
  responsive grid area.
- Home Assistant App sidebar presentation now requests the `mdi:radio-tower`
  icon.

### Fixed

- Home Assistant App startup safely migrates legacy v0.20.0 recordings and
  metadata sidecars from `/data/recordings` into the configured media library.
  Migration preflights destination conflicts, never overwrites differing files,
  byte-verifies copied files before removing their sources, and can resume after
  an interrupted partial migration.

## [0.20.0] - 2026-08-09

### Added

- Project-consistent icon and logo assets for Home Assistant App presentation.
- Milestone 20.11 Home Assistant App packaging around the existing single-owner
  daemon and web dashboard, with Supervisor-managed startup, required MQTT service
  discovery, strict `/data/options.json` configuration, private runtime sockets,
  persistent `/data/recordings`, amd64/aarch64 images, release-only GHCR
  publication, and Home Assistant Ingress on port 8099 without creating another
  scanner, PSI, RTSP/RTP, or control owner.
- Home Assistant Ingress path portability for dashboard, API documentation,
  scanner controls, SSE, browser PCMU, and saved-recording requests; an explicit
  Ingress peer guard that admits only the Supervisor proxy; Ingress-compatible
  frame policy; and a browser-audio compatibility fallback when AudioWorklet is
  unavailable in a non-secure Home Assistant browser context.
- A fixed Home Assistant App RTP destination on UDP port 50000 with an explicit
  Supervisor port mapping so SDS200 RTP sent back to the HAOS host reaches the
  container-owned daemon audio socket without enabling host networking. Physical
  HAOS validation with SDS200 firmware 1.26.01 confirmed live scanner state,
  Channel Hold/release, browser audio, recording and WAV playback, recording
  persistence across App restart, clean stop/start lifecycle, and all ten
  Home Assistant MQTT Discovery entities with correct model and firmware metadata.
- Milestone 20.10 optional Home Assistant MQTT device Discovery over the existing
  generic daemon MQTT state contract. Discovery is disabled by default, derives a
  stable device identity from the configured MQTT topic prefix, publishes ten
  read-only daemon/scanner/radio/audio/recording entities after authoritative
  snapshots, and republishes after broker reconnect, event-stream
  resynchronization, or an exact configured Home Assistant birth message. The
  birth subscription uses QoS 0 without changing semantic-command manual
  acknowledgement or request-ID deduplication, and configurations reject a birth
  topic that collides with the command topic. No Home Assistant control topic,
  second scanner owner, PSI stream, or RTSP/RTP session is introduced.
- Milestone 20.9 opt-in daemon MQTT scanner controls through the existing
  semantic daemon API boundary. `commands_enabled` defaults to false; enabled
  workers subscribe only after the authoritative initial snapshot, accept only
  hold/hold-state/next/previous/reconnect operations, reject retained and
  oversized requests, publish non-retained correlated responses, use Paho manual
  acknowledgements, and keep a bounded process-local request-ID response cache so
  immediate QoS redelivery does not repeat non-idempotent controls. The Paho
  callback remains transport-only, the inbound queue is bounded, and the local
  socket server and MQTT worker share one `DaemonReadOnlyApi` dispatcher.
- Milestone 20.8 native daemon MQTT foundation with a strict optional version 1
  `daemon-mqtt.toml` broker manifest, `--mqtt-config` override, a separate
  `sds200[mqtt]` Paho MQTT 2.x extra, environment-referenced password secrets,
  validated reconnect policy, and dependency/configuration preflight before
  scanner construction only when MQTT is configured.
- A failure-isolated daemon MQTT publication worker over the existing authoritative
  event stream. It publishes retained `online`/`offline` availability with a
  retained offline last will, canonical daemon/scanner/radio/audio/recording and
  per-destination state topics, and a non-retained semantic event stream while
  suppressing packet-rate PSI. Sequence gaps force authoritative resubscription,
  broker failures use worker-owned exponential backoff, graceful local failures
  publish offline before disconnect, and MQTT shutdown precedes runtime shutdown
  without opening another scanner or RTSP/RTP session.
- Milestone 20.7 browser-local dashboard themes with a system-adaptive default
  plus immersive LCARS-inspired, Matrix-inspired, First Responder, and Amateur
  Radio environments over the existing shared accessible dashboard structure.
  Desktop-class custom themes use a dense full-screen workstation composition
  with renderer-specific console rails, terminal fields, dispatch/CAD surfaces,
  scanner chassis details, layered depth, reflections, scan effects, and other
  pointer-inert decorative staging. Compact layouts reflow telemetry for smaller
  displays, reduced-motion preferences suppress decorative animation, and no
  state is communicated by color alone. A same-origin CSP-compatible bootstrap
  script restores the saved choice before the stylesheet paints, theme selection
  is persisted only in browser local storage, browser color metadata follows the
  active appearance, and the feature adds no daemon, scanner, API-state, CLI, or
  TUI theme coupling.
- Deterministic web-dashboard documentation captures generated from the real
  packaged dashboard with fictional daemon, scanner, radio, recording, and
  reliability data. The repository helper uses native headless Chrome, isolated
  per-capture browser profiles, bounded capture timeouts, and checked dimensions
  to produce the five 1920x1080 theme references plus a 1366x768 compact-layout
  reference without adding screenshot behavior to the shipped web service.
- Milestone 20.6 loopback browser scanner controls for semantic system,
  department, site, and channel hold/release, previous/next channel navigation,
  and bounded reconnect. The web layer negotiates daemon-advertised
  capabilities, sends explicit hold desired state without raw scanner targets
  or keys, preserves documented snapshot-based selection resolution for channel
  navigation, prevents overlapping browser control mutations, maps stable
  redacted failures, and renders authoritative completion snapshots without
  disturbing ordered SSE reconciliation or daemon scanner ownership.
- Foreground-daemon PSI silence recovery using every successfully parsed PSI
  frame as the liveness signal, with default-on bounded recovery after 10 seconds,
  a 60-second retry cooldown, command-line policy controls, and the existing
  daemon mutation lock so automatic reconnect cannot overlap browser or local
  scanner controls. A busy mutation defers recovery without consuming cooldown.
- Physical SDS200 validation of the daemon PSI watchdog by dropping only inbound
  UDP control/PSI datagrams to the daemon's original local control port. The same
  daemon process detected 10.1 seconds of PSI silence, reopened the scanner
  transport on a new local port, resumed ordered PSI events, and kept independent
  RTSP/RTP audio advancing while the web process remained running.
- Self-hosted interactive Swagger UI and ReDoc at `/api/v1/docs` and
  `/api/v1/redoc`, backed by the existing local OpenAPI schema and version-pinned
  packaged Swagger UI 5.32.11 and ReDoc 2.5.3 assets with upstream license and
  notice material. Documentation pages keep same-origin scripts and connections,
  make no daemon request merely by loading, and use a narrowly scoped
  style-only CSP exception without weakening the normal dashboard policy.
- Milestone 20.5 daemon-owned browser recording workflows over the existing
  decoded-PCM router, with repeatable collision-safe WAV capture, adjacent
  metadata sidecars, bounded newest-first finalized inventory, stable recording
  status/start/stop/list API operations, ordered `recording.state` events, and
  daemon-shutdown finalization without opening a second scanner RTSP/RTP stream.
- A private versioned `recordings.sock` recording-file service with bounded local
  clients, canonical inventory-relative identifiers, component-by-component
  `O_NOFOLLOW` reopening, regular-file and WAV revalidation, exact-length
  streaming, redacted failures, and deterministic process cleanup.
- Browser Record and Stop controls with live elapsed, packet, sample, duration,
  and RTP reliability telemetry; automatic active-recording reconciliation after
  reload or reconnect; recent finalized recordings; and same-origin Play and
  Download actions that use the recording-file service without creating browser
  PCMU or scanner-audio ownership.
- Physical SDS200 browser-recording validation confirmed live daemon-owned
  capture, loss-free observed telemetry, finalized WAV and metadata inventory,
  saved-file playback and download, active recording survival across web-process
  shutdown and restart, and SIGTERM finalization before daemon audio shutdown.
  The shutdown-finalized recording was rediscovered as playable after daemon
  restart, and the full browser acceptance workflow passed.
- Milestone 20.4 explicit same-origin browser audio over the daemon-owned PCMU
  socket with one independent validated client per playback stream, exact v1
  frame forwarding without re-encoding, manual Play and Stop controls,
  AudioWorklet G.711 mu-law decoding, bounded buffering and resampling, daemon
  queue-loss and RTP-loss telemetry, hidden-tab playback continuity, and
  deterministic Stop and page-close cleanup. The milestone also reaps idle
  disconnected daemon event clients and bounds Uvicorn graceful shutdown to two
  seconds so long-lived browser streams cannot make one `SIGINT` wait forever.
- Physical SDS200 browser validation with firmware 1.26.01 confirmed audible
  scanner audio in both output channels, zero browser queue and RTP loss during
  the observed run, PCMU cleanup on Stop, continued audio while the dashboard
  was hidden, repeated SSE visibility-cycle cleanup with no retained accepted
  event sockets, and controlled active-SSE web shutdown after one `SIGINT` in
  2.368 seconds with the daemon remaining healthy.
- Milestone 20.3 same-origin Server-Sent Events bridge over the existing ordered
  daemon event socket, authoritative snapshot-first delivery, validated SSE
  identifiers and JSON envelopes, redacted initial failures, deterministic
  event-client cleanup, browser-side incremental updates, automatic reconnect,
  two-second polling fallback, and periodic authoritative reconciliation.
- Milestone 20.2 accessible responsive browser shell with packaged HTML, CSS,
  and JavaScript assets; read-only polling of the existing daemon-status API;
  scanner, activity, PSI, audio, and destination summaries; progressive
  no-JavaScript messaging; light, dark, compact, keyboard-focus, and
  reduced-motion behavior; strict browser response headers; and
  host-independent shell and static-asset regression tests.
- Milestone 20.1 optional web-service foundation with FastAPI and Uvicorn
  packaging, a loopback-only `sdsctl web` command, versioned process-health,
  daemon-status, authoritative-snapshot, and OpenAPI endpoints, redacted daemon
  failures, disabled interactive documentation, and host-independent application,
  listener, CLI, packaging, and regression tests.
- A web dashboard foundation guide covering installation, daemon-client
  architecture, localhost-only binding, the current HTTP contract, security
  boundaries, command options, and explicitly deferred browser-dashboard work.

## [0.19.0] - 2026-08-06

### Added

- Immutable renderer-neutral application configuration values with validated
  reconnect, reliability, presentation, and logging settings plus per-field
  source provenance.
- Versioned system and user TOML loading, supported `SDSCTL_*` environment
  overrides, explicit CLI precedence, deterministic `sdsctl` configuration,
  state, and cache paths, and read-only legacy configuration discovery.
- Renderer-neutral `DaemonRuntime` ownership of scanner control, PSI, one
  RTSP/RTP decoded-PCM fanout, and dynamic destinations, with immutable snapshots,
  ordered transitions, partial-start cleanup, reverse-order shutdown, isolated
  listeners, and redacted failures.
- Additive daemon snapshot identity fields for scanner model and firmware,
  populated by independent nonfatal startup probes and accepted as optional by
  version 1 API and event clients for backward compatibility.
- Public `DaemonSignalController`, `DaemonProcess`, and immutable
  `DaemonProcessResult` contracts for foreground process ownership, SIGINT and
  SIGTERM stop requests, handler restoration, and deterministic cleanup that
  preserves primary failures.
- Validated `SIGHUP` destination-manifest reload using the startup path,
  transactional activation, stop-request priority, redacted failure isolation,
  and committed replacement results that retain post-commit cleanup reporting.
- Foreground `sdsctl daemon` construction of one scanner, PSI, RTSP/RTP audio,
  decoded-PCM router, and `DaemonRuntime` from an explicit SDS200 host or
  network-capable saved profile.
- Versioned `sdsctl.daemon` protocol with backward-compatible snapshot
  operations plus capability-checked hold, next, previous, and reconnect
  controls, strict JSON Lines envelopes, correlation identifiers, capability
  negotiation, and structured redacted errors.
- Private Unix-domain socket resolution through an explicit path,
  `XDG_RUNTIME_DIR`, or the user state directory, with private permissions,
  active-daemon refusal, safe stale-socket replacement, and filesystem identity
  checks.
- Bounded local API client handling with request and response limits, idle
  timeouts, isolated connection workers, server health snapshots, CLI limit
  options, and process lifecycle integration that stops API clients before the
  ownership runtime.
- Public `DaemonApiClient` and explicit `sdsctl daemon-client` status, snapshot,
  hold, next, previous, and reconnect workflows with capability negotiation,
  authoritative result validation, distinct control deadlines, clear absent or
  incompatible daemon diagnostics, and preserved standalone scanner commands.
- Single-owner daemon mutation execution with immediate concurrent-request
  rejection, scanner-acknowledged completion, ordered immutable control results,
  authoritative completion snapshots, and stable redacted control error codes.
- One maximum two-second daemon control budget covering lifecycle-lock waits and
  scanner completion, plus bounded reconnect that is advertised only for the
  directly owned SDS200 UDP control transport.
- Versioned `sdsctl.daemon.events` JSON Lines envelopes with immutable payloads,
  authoritative snapshot checkpoints, global sequence numbers, observation
  timestamps, stable event kinds, and encoded-size enforcement.
- One serialized daemon event stream aggregating runtime lifecycle, scanner
  connection, PSI, radio-state, audio lifecycle, and decoded-PCM destination
  health without publishing packet-rate audio data.
- A separate private `events.sock` Unix-domain endpoint with one bounded
  subscription per admitted client, independent overflow, explicit sequence-gap
  resynchronization, slow-client isolation, deterministic cleanup, and process
  lifecycle integration.
- Public `DaemonEventClient` and `sdsctl daemon-client events` workflows with
  validated envelope, protocol, version, snapshot, framing, size, and sequence
  validation; clear disconnect diagnostics; bounded matching counts; and
  optional client-side event-kind filtering.
- Public `DaemonPcmuClient` with bounded binary-frame reads, strict magic,
  version, header, endpoint, frame, stream-order, and cumulative-loss validation,
  plus immutable delivery, duration, RTP-continuity, and client-loss snapshots.
- Explicit `sdsctl daemon-client audio` playback and WAV-recording workflows that
  consume daemon-owned PCMU without opening scanner hardware or the daemon API,
  reuse existing bounded PCM sinks, support optional duration and output-device
  selection, and report stream, queue-loss, RTP, playback, and output summaries.
- Public `DaemonPcmuAudioTransport` adaptation of daemon-owned PCMU to the
  renderer-neutral audio-stream contract, preserving observation timestamps,
  daemon queue and RTP continuity statistics, bounded lifecycle, and isolated
  receive and callback failures.
- Explicit `sdsctl tui --daemon-client` operation using authoritative daemon
  snapshots, ordered events, safe controls, and daemon-owned PCMU audio without
  opening scanner hardware or a second RTSP/RTP session. Standalone TUI
  ownership remains the default, and closing the TUI leaves the daemon running.
- Foreground daemon options for event socket location, subscriber queue depth,
  concurrent event clients, maximum encoded event size, send timeout, and worker
  shutdown deadline.
- Immutable accepted-PCMU packet publication before decoding, preserving RTP
  sequence, timestamp, SSRC, expected continuity values, missing packet and sample
  estimates, observation time, marker state, and authoritative audio endpoint.
- Independent bounded PCMU subscriptions with global publication ordering,
  drop-oldest queues, cumulative packet, byte, and overflow loss counters,
  immutable health snapshots, subscriber limits, and deterministic close behavior.
- Versioned `sdsctl.daemon.pcmu` binary framing with strict magic, version, flags,
  complete-frame lengths, UTF-8 endpoint encoding, payload and frame bounds, and
  public encode and decode helpers.
- A third private `pcmu.sock` endpoint with one isolated subscription per admitted
  client, bounded send waits and shutdown, excess and disconnected-client
  isolation, server health snapshots, and foreground daemon lifecycle integration.
- Foreground daemon options for PCMU socket location, subscriber queue depth,
  concurrent clients, payload, endpoint, and frame sizes, send timeout, and worker
  shutdown deadline.
- Physical SDS200 validation of foreground startup, live PSI and RTSP/RTP
  reception, private API socket permissions, all read-only operations,
  malformed-request recovery, an independent second client, controlled
  `SIGINT` and systemd-style `SIGTERM` shutdown, socket removal, reverse-order
  cleanup, and successful process exit.
- Physical SDS200 validation of the private local event endpoint with two
  independent snapshot-first clients, excess-client rejection, uninterrupted
  API ping, 76 continuous ordered events from sequence 11 through 86, live PSI
  and radio-state updates, shutdown lifecycle events, 507 received RTP packets,
  162,240 decoded samples, clean `SIGTERM`, and removal of both owned sockets.
- Physical SDS200 validation of simultaneous API, event, and PCMU clients with
  private `0700` directory and `0600` socket permissions, 61 successful API
  pings, 231 continuous ordered events, and two independent PCMU clients that
  each received 1,503 frames and 480,960 payload bytes without sequence gaps,
  queue drops, overflows, RTP loss, timestamp reversal, or mismatched overlapping
  frames. An excess PCMU client was rejected, decoded audio advanced by 1,500
  packets and 480,000 samples, and controlled `SIGTERM` removed all three sockets
  with exit status 0.
- Physical SDS200 validation of capability negotiation and the complete safe
  daemon-control sequence: TGID hold, next, previous, hold release, and bounded
  reconnect. All five scanner-acknowledged operations completed in order, next
  changed the held selection, previous returned to it, hold was restored to
  `Off`, reconnect produced both connection transitions, and API, PSI, event,
  RTSP/RTP, decoded-audio, and PCMU activity remained healthy. The run completed
  16 API pings, 82 ordered events without a gap, and two matching loss-free PCMU
  streams of 410 frames and 131,200 payload bytes each. Controlled `SIGTERM`
  returned exit status 0 and removed all three sockets.
- Added semantic desired-state `scanner.hold_state` control for system,
  department, site, and channel hold/release without changing the compatibility
  indexed `scanner.hold` operation. The daemon performs an authoritative `GSI`
  read before deciding whether a gesture is needed, no-ops when the requested
  state already matches, executes the complete verified gesture under one
  mutation lock, and polls authoritative `GSI` until the target hold field
  converges.
- Physical SDS200 firmware 1.26.01 validation confirmed one `KEY,A,P` toggles
  System Hold, one `KEY,B,P` toggles Department Hold, `KEY,F,P` followed by
  `KEY,B,P` toggles Site Hold, and one `KEY,C,P` toggles Channel Hold in both
  directions. Browser hold controls now send explicit `held: true|false`
  desired state, render held scopes as actionable Release controls, and never
  expose a generic raw `KEY` operation.
- Physical end-to-end Milestone 20.6 web validation exercised semantic
  release/re-hold for all four scopes through the loopback HTTP routes,
  including Channel release across the `4294967295` no-selection interval. A
  real browser then confirmed the Channel Release/Held -> Hold -> Release/Held
  UI cycle and completion messages while daemon/web process IDs remained
  unchanged and PSI plus daemon-owned audio stayed healthy.
- Physical SDS200 validation of `sdsctl daemon-client audio` with simultaneous
  default-device playback and WAV recording through the private PCMU socket.
  The client received 258 consecutive frames from stream sequence 16 through
  273 and 82,560 samples without stream gaps, PCMU queue loss, RTP loss,
  missing samples, timestamp reversal, or callback status. It finalized an
  8 kHz mono signed 16-bit WAV containing 10.320 seconds of audio, preserved
  API health, and completed clean `SIGTERM` removal of all three sockets. The
  bounded local playback queue wrote 159,942 bytes and reported six overflows
  dropping 2,088 PCM bytes without underflow.
- Physical SDS200 validation of `sdsctl tui --daemon-client` using explicit
  API, event, and PCMU sockets. The TUI rendered cleanly, followed live state,
  completed a safe scanner control, automatically started playback, toggled
  playback with `A`, and finalized a 53.120-second 8 kHz mono WAV plus metadata.
  Quitting the TUI left scanner, PSI, RTSP/RTP audio, router, and daemon
  ownership running. Controlled `SIGTERM` then removed all three sockets.

- A daemon deployment and upgrade guide covering preserved naming
  compatibility, dedicated service accounts, systemd supervision, private socket
  access, destination manifests, transactional reload, migration, clean
  installation, upgrades, and rollback.
- Validated the repository's existing GitHub CodeQL default setup for Actions
  and Python against the release pull-request head with no analysis errors.

### Changed

- `sdsctl` now resolves application settings from built-in defaults,
  `/etc/sdsctl/config.toml`, the XDG user configuration file, environment
  variables, and explicit CLI options while preserving existing behavior when
  optional files are absent.
- Kept `--config PATH` dedicated to the legacy scanner connection-profile file;
  no profile or remote-audio configuration is moved or rewritten automatically.
- Raised the default daemon API worker shutdown deadline from two to three
  seconds and added rejection of API server configurations whose shutdown
  deadline cannot outlast the maximum request duration.
- Short TUI layouts now use dense borderless panels, four-line audio and PSI
  health summaries, and a one-line essential-controls footer so the operational
  view fits Raspberry Pi-class displays while tall layouts retain full detail.
- Physically validated the compact TUI on a Raspberry Pi 4 with an 800 by 480
  display at 100 by 30 terminal cells, including live playback, recording,
  library navigation, scrolling, controls, live PSI, and clean shutdown.

### Fixed

- Renew active SDS200 network PSI pushes in place every 120 seconds after
  physical firmware 1.26.01 testing showed otherwise healthy push delivery
  stopping at roughly 184 seconds. Renewal is serialized with scanner command
  transactions, the acknowledgement path remains serialized through the first
  subsequent PSI frame, command and bounded-reconnect timeout budgets are
  preserved, capture wrapping and UDP fallback remain supported, and stale-PSI
  reconnect recovery remains the second-line failure path.
- Added the package author email to the PEP 621 project metadata so future
  distributions expose the expected `Author-email` value.

## [0.18.0] - 2026-08-03

### Added

- Renderer-neutral `RemoteSinkHealth` classification, serializable
  `RemotePcmSinkSnapshot` metrics, ordered `RemotePcmSinkTransition` events,
  timezone-aware lifecycle timestamps, and isolated `on_transition()`
  subscriptions for future CLI, TUI, daemon, and integration consumers.
- Versioned `BroadcastifyDestinationProfile` and `RemoteAudioProfileStore`
  APIs that retain environment-variable secret references, preserve adapter and
  reconnect settings, and convert into validated configuration without storing
  resolved credentials.
- Renderer-neutral `RemoteStreamMetadata`, worker-backed
  `RemoteMetadataPublisher` metrics and retry isolation, and optional
  Broadcastify-compatible Icecast alpha-tag updates synchronized with live
  scanner state without blocking PSI or PCM delivery.
- Public renderer-neutral audio encoder process contracts, immutable command and
  lifecycle settings, reusable pipe-backed subprocess management, bounded
  interruption and finalization, stderr diagnostics, and migration of the fixed
  Broadcastify FFmpeg MP3 profile onto the shared lifecycle.
- A renderer-neutral local playback lifecycle with bounded newest-audio buffering,
  warm mute behavior, underflow and overflow metrics, preserved PortAudio
  compatibility, and explicit PipeWire, PulseAudio, and ALSA command adapters with
  injectable factories and bounded process cleanup.
- Reusable dynamic PCM subscriber routing with immutable per-subscriber health
  snapshots, ordered transition events, lifecycle and submission counters,
  timezone-aware timestamps, redacted failure state, listener isolation, and
  startup, submission, and shutdown failure isolation.

## [0.17.0] - 2026-08-03

### Added

- Renderer-neutral `RecordingIdentity` derivation and portable filename-component
  normalization for future recording organization policies.
- Configurable TUI recording directories organized by ordered scanner, date,
  system, department, site, or channel identity components.
- Renderer-neutral, read-only recording inventory with WAV and metadata-sidecar
  classification, deterministic ordering, issue reporting, and aggregate totals.
- Deterministic non-destructive recording-retention previews with age, managed-unit,
  and aggregate-byte limits plus protected-artifact reporting.
- Explicit plan-bound retention execution with stale-state revalidation,
  path-and-symlink refusal, deterministic WAV-plus-sidecar deletion, and immutable
  partial-failure reporting.
- Local `sdsctl recordings retention` previews with stable JSON, exact plan-bound
  execution tokens, fixed age-policy planning boundaries, and meaningful exit
  statuses for unsatisfied limits or incomplete execution.

## [0.16.1] - 2026-08-02

### Added

- `sdsctl audio-devices` reporting the local PortAudio version, host APIs, default
  output, and output-capable devices without opening a scanner connection.

### Fixed

- Missing Linux PortAudio runtimes now produce an actionable Debian and Raspberry
  Pi OS `sudo apt install libportaudio2` diagnostic instead of the raw
  `PortAudio library not found` import failure.

### Changed

- Documented the Linux system dependency behind the optional `sounddevice`
  playback extra and clarified how PortAudio may be exposed through ALSA,
  PipeWire or PulseAudio compatibility, or JACK.
- Recorded direct PipeWire, PulseAudio, and ALSA adapters as planned Milestone 18
  work and daemon-owned single-session audio fanout as planned Milestone 19 work.

## [0.16.0] - 2026-08-02

### Added

- Transport-independent decoded-PCM fanout sessions with independently buffered
  sink destinations.
- Optional live playback through the local default or selected PortAudio output
  device, including queue, underflow, overflow, and dropped-audio counters.
- A maintained project roadmap covering active, planned, and exploratory work.
- A consolidated project-vision document preserving product direction,
  architectural constraints, security boundaries, hardware-validation policy,
  Favorites Workspace plans, daemon and integration ideas, and advanced protocol
  research.
- Version-controlled GitHub Wiki source with task-oriented home, installation,
  troubleshooting, navigation, and publishing guidance.
- Repeatable TUI recordings with collision-safe local timestamp filenames, a
  newest-first recording library, and saved-recording playback controls.
- Immediate unmuted TUI live playback through the default or selected PortAudio
  device.

- A service-neutral remote PCM destination core with environment-backed secret
  references, bounded worker queues, reconnect backoff, redacted failures, and
  immutable operational snapshots.
- A Broadcastify-compatible Icecast source adapter with a fixed 22.05 kHz,
  16 kbps constant-bit-rate mono MP3 profile, FFmpeg process isolation, static
  source metadata, injected test seams, and interruptible shutdown.
- An Asterisk custom Music-on-Hold bridge with direct 8 kHz signed-linear PCM,
  a bounded nonblocking stdout worker, network-profile support, clean pipe-close
  handling, and orderly `SIGHUP`, `SIGTERM`, and `SIGINT` shutdown.

- Physical SDS200 validation tools and evidence for Broadcastify-compatible local
  Icecast streaming, forced-disconnect recovery, and assigned production-feed
  authorization and routing, including sanitized counters, MP3 profile and signal
  checks, reconnect state, credential exclusion, and orphan-process detection.
- A versioned recording-metadata model with scanner boundary state, audio and
  reliability statistics, deterministic JSON serialization, and collision-safe
  atomic sidecar writes.
- Optional TUI recording sidecars enabled by `--audio-metadata`, including live
  scanner state captured at successful recording start and stop boundaries.
- A bounded, thread-safe TUI operational log panel that is visible by default,
  toggles with `G`, retains records while hidden, and preserves optional file
  logging.
- Descriptive border titles for the standard and wide TUI panels.
- Reproducible native SVG screenshots of the real Textual TUI populated with
  fictional demonstration scanner, recording, audio, and log data.
- A renderer-neutral scanner-screen classifier for normal scanning, Quick
  Search, Close Call, weather, Tone Out, and unknown screens while preserving
  the scanner's raw `Mode` and `V_Screen` values, with synthetic GSI/PSI
  fixtures and transition coverage.
- Mode-aware Quick Search and Close Call TUI panels showing the reported state
  node, frequency or hit name, modulation, hold state, signal, RSSI, and
  detected tone or digital-code value from the scanner's `SAD` attribute.
- Mode-aware Weather TUI panels showing the reported weather channel and number,
  frequency, modulation, monitor or alert mode, hold state, signal, RSSI, and
  SAME selection when supplied by the scanner.
- Mode-aware Tone Out TUI panels showing the reported profile and channel number,
  monitored frequency, modulation, Tone A and Tone B values, hold state, signal,
  and RSSI.
- Physical SDS200 firmware 1.26.01 UDP validation for normal scanning, Quick
  Search, Close Call, Weather, and Tone Out GSI/PSI states and live transitions,
  including hardware-aligned fixtures and documented unobserved protocol
  variants.

### Changed

- Package and CLI version advanced to `0.16.0`.
- Reconciled the roadmap with completed Milestone 16 work, made screen-mode
  foundation the active slice, moved v0.16.0 preparation to Milestone 16.6, and
  deferred SDS150 physical validation until hardware is available.
- Adopted `sdsctl` as the namespace for future configuration, services, state,
  cache, daemon, API, and integration work while preserving existing Python package
  compatibility.
- `sdsctl audio` now supports playback-only, recording-only, or simultaneous
  playback and WAV recording from one SDS200 RTSP/RTP session.
- PCM WAV writes used by the fanout pipeline now run on a dedicated worker instead
  of the RTP receive callback.
- TUI playback and repeatable recording now share one long-lived RTSP/RTP stream;
  playing a saved recording temporarily suspends local live playback without
  interrupting scanner reception or an active WAV recording.
- SDS200 host TUI sessions now expose live playback controls even without
  `--audio-playback`; the flag requests automatic startup after connected live PSI.
- Full-screen TUI sessions now redirect package stderr logging into the in-app
  panel and restore the original stderr handler after shutdown.

### Fixed

- Network control and RTP audio transports now represent an unset local bind
  address with `None`, reject legacy numeric aliases for `0.0.0.0`, and use
  explicit resolver fallbacks so CodeQL can verify that wildcard binds are not
  reachable.

- Prevented the final TUI status detail row from being clipped when the
  operational log panel is hidden in a wide layout.
- Stopped TUI polling timers before widget teardown and suppressed late rendering
  callbacks after shutdown begins.
- Deferred PortAudio startup until the first connected live PSI refresh, preventing
  playback initialization from leaving stale startup panels in wide terminals.
- Live playback toggles now keep a prepared output device warm and muted until TUI
  shutdown, without counting intentional muted callbacks as underflows.

## [0.15.0] - 2026-07-28

### Added

- Deterministic fault-injection coverage for concurrent audio start/stop,
  repeated TUI recording requests, shutdown during audio startup, and scanner
  reconnects while recording.
- Configurable operational logging with explicit levels, optional persistent
  files, logrotate compatibility, and systemd/journald guidance.
- Automatic rate-limited TUI recovery when a connected UDP control transport
  stops delivering PSI updates.

### Changed

- Package and CLI version advanced to `0.15.0`.
- Updated the README project status to describe the v0.14.0 TUI audio,
  reliability, and lifecycle improvements alongside the v0.15.0 operational
  hardening.

### Fixed

- Suppressed background TUI callback dispatch after shutdown begins so in-flight
  audio and scanner-control workers can terminate without callback/join contention.
- Reconnect stale PSI streams automatically after a configurable sustained-stale
  interval while leaving an active SDS200 RTP audio recording uninterrupted.
- Preserved the configured PSI interval after a reconnect timeout so later
  automatic recovery attempts continue restarting the scanner-information stream.

## [0.14.0] - 2026-07-28

### Added

- Reusable `AudioRecordingSession` service with immutable lifecycle and reliability
  snapshots for renderer-independent integrations
- Opt-in SDS200 network-audio recording in the Textual TUI with `R` start/stop
  controls and automatic WAV finalization during shutdown
- Dedicated TUI audio worker so RTSP startup and teardown cannot block scanner
  controls or Textual's event loop
- Live TUI audio panel for elapsed time, output path, packet and sample totals, audio
  duration, and RTP reliability counters
- Local `since HH:MM:SS` transition timestamps for connection, availability, and
  severity states
- Nonblocking TUI reconnect action that preserves the active PSI interval

### Changed

- `sdsctl audio` now delegates recording lifecycle and cleanup to the shared audio
  session service
- Every valid GSI/PSI frame now refreshes state observers while field-change events
  remain limited to actual value changes
- Package and CLI version advanced to `0.14.0`

### Fixed

- Stable channels with repeated unchanged PSI frames no longer age into a false
  stale state
- Textual's footer now renders the command-palette binding as `^p Command Palette`
  without duplicated wording

## [0.13.0] - 2026-07-27

### Added

- Optional Textual 8 full-screen application shell with USB, network, profile,
  and replay launch support
- Renderer-neutral scanner-information presentation shared by the Rich CLI and
  Textual adapters
- Dark/light TUI palette switching, explicit quit binding, and headless shell
  regression tests
- Live PSI subscriptions that marshal radio callback updates safely into the
  Textual event loop
- Connected, degraded, reconnecting, disconnected, and stale-data presentation
  with configurable PSI and freshness intervals
- Deterministic callback unsubscription, PSI shutdown, and replay-driven live-state
  regression coverage
- Serialized background TUI command execution for hold, next, previous, volume,
  and squelch controls without blocking Textual's event loop
- PSI/GSI navigation-index retention with capability-aware channel controls and
  explicit unavailable, success, and failure feedback
- `sdsctl -V` and `sdsctl --version` flags for installed version information
- Deterministic TUI control-worker and replay-command regression coverage
- Automatic compact, standard, and wide TUI layouts for Raspberry Pi and terminal
  displays, including short-screen identity consolidation
- In-app `?` keyboard reference with a compact footer that keeps essential actions
  visible without crowding small terminals
- Project acknowledgment documentation for substantial ChatGPT-assisted development

### Changed

- Package and CLI version advanced to `0.13.0`

## [0.12.0] - 2026-07-27

### Added

- Renderer-independent semantic presentation types for connection, activity,
  signal, hold, availability, and severity states
- Pure snapshot classification with normalized mute, recording, service-type,
  and raw-signal values for future CLI and Textual renderers
- Rich terminal adapter that renders scanner information from semantic presentation
  roles while preserving plain-text output for redirected and captured streams
- Stable semantic theme roles that map scanner presentation states without
  introducing renderer dependencies
- Complete immutable light and dark palettes with generic color and emphasis
  tokens for future Rich and Textual adapters
- Explicit `--color`, `--no-color`, and `--theme` CLI controls with `NO_COLOR`
  and `FORCE_COLOR` environment handling
- Accessibility regression coverage proving semantic scanner information remains
  identical when ANSI styling is disabled or palettes are changed

### Changed

- Package version advanced to 0.12.0

## [0.11.1] - 2026-07-27

### Security

- Avoided wildcard-interface binds for default SDS200 UDP control and RTP audio
  sockets by using operating-system route selection
- Restricted RTP audio ingestion to the source address, server port, and SSRC
  negotiated by the scanner's RTSP `SETUP` response
- Rejected explicit `0.0.0.0` control and RTP bind addresses

### Added

- Typed parsing for scanner RTSP `Transport` response parameters
- Audio reliability counters for unexpected RTP sources and SSRC mismatches

### Changed

- Package version advanced to 0.11.1

## [0.11.0] - 2026-07-27

### Added

- Hardware-validated SDS200 network audio transport using the scanner's strict
  single-port RTSP/RTP negotiation
- Typed RTSP response and SDP handling plus RTP version 2 packet parsing for
  payload type 0 PCMU audio
- Native G.711 mu-law decoding to 8 kHz mono signed 16-bit PCM
- Streaming WAV recording through `sdsctl --host HOST audio`, including duration,
  overwrite, RTP bind, RTSP port, and keepalive options
- Per-session RTP reliability statistics for packet loss, sequence gaps,
  duplicates, late and malformed packets, timestamp discontinuities, receive and
  callback errors, keepalives, and orderly teardown
- Sanitized synthetic PCMU/RTP fixtures and deterministic transport reliability
  regression tests

### Changed

- Network audio remains independent from USB serial and UDP scanner-control
  transports while using the existing `AudioStream` lifecycle and subscriptions
- Package version advanced to 0.11.0

## [0.10.0] - 2026-07-24

### Added

- Opt-in preferred-transport recovery for SDS200 fallback profiles
- Validated `MDL` probes, stability windows, cooldowns, and command-idle promotion guards
- Preferred-recovery diagnostics, counters, timestamps, health history totals, and CLI overrides
- Manual fallback profile creation with simultaneous `--port` and `--host` options
- Persistent preferred-recovery settings in version 4 profile documents

### Changed

- Fallback profiles can now return from a healthy alternate transport to the configured preferred endpoint without reporting a connection interruption
- Continuous PSI updates restart automatically after a preferred transport recovery
- Package version advanced to 0.10.0

## [0.9.0] - 2026-07-24

### Added

- Deterministic `ReplayTransport` for running real parser, radio, and CLI flows from JSON Lines captures
- `RecordingTransport` and `--capture` support for USB, UDP, and fallback sessions
- Repeatable literal redaction for captures before fixtures are shared
- `sdsctl --replay`, replay timing control, and strict command-sequence mismatch errors
- `sdsctl capabilities` with model limits, feature flags, and hardware-validation status
- Typed `HLD`, `NXT`, and `PRV` navigation commands with CLI and Python APIs
- Hardware-derived, sanitized SDS100 replay fixture and replay regression tests

### Changed

- Model capabilities now identify scanner-info, PSI, navigation, and validation status
- README installation instructions now use the published PyPI package
- Trusted Publishing workflow uses current Node 24-based checkout and Python setup actions
- Package version advanced to 0.9.0

## [0.8.2] - 2026-07-24

### Added

- Optional SDS100 battery telemetry through the documented `GSI`/`PSI` `Property.Battery` attribute
- Immediate `CommandRejectedError` handling for generic scanner `ERR` and `NG` replies
- Extended `scanner-info` output for RSSI, optional battery, recording, and mute state
- Opt-in Uniden SDS-series udev rule for desktop ACLs and ModemManager exclusion

### Changed

- Corrected SDS100 capabilities after firmware 1.26.01 hardware testing showed
  that `GCS` returns `ERR`
- Kept SDS150 detailed `GCS` charge status as specification-based and hardware unverified
- Package version advanced to 0.8.2

## [0.8.1] - 2026-07-24

### Changed

- Replaced the model-specific `sds200` executable with the model-neutral `sdsctl` command
- Updated CLI help, shell completion, documentation, support guidance, and tests for `sdsctl`
- Kept the distribution, Python import package, configuration directory, and repository named `sds200`
- Package version advanced to 0.8.1

## [0.8.0] - 2026-07-24

### Added

- USB serial control support for the Uniden SDS100 and SDS150
- Model-neutral `SDSScanner` API while retaining the historical `SDS200` alias
- Scanner capability metadata and model-specific volume and squelch limits
- SDS100/SDS150 `GCS` battery and charge-status parsing and CLI output
- Model-aware USB discovery, selection, profiles, repair, and completions
- SDS150 `SDS150GBT` and Uniden internal model-name normalization
- LF, CR, and CRLF serial response framing for shared SDS-series commands
- Multi-model protocol, profile, discovery, and command regression tests

### Changed

- Network discovery and UDP profiles are explicitly restricted to the SDS200
- Profile documents advance to version 3 and can retain the scanner model
- Documentation distinguishes protocol support from physical-hardware validation
- Package version advanced to 0.8.0

## [0.7.0] - 2026-07-23

### Added

- Configurable exponential reconnect backoff with finite or unlimited attempts
- Structured `RadioEvent` notifications and `events --json` JSON Lines output
- Bounded health history with latency, error-rate, reconnect, and failover summaries
- Health thresholds for healthy, degraded, unhealthy, and disconnected states
- Discovery-based profile repair for stale USB paths and changed network addresses
- Detailed failover telemetry including previous and active endpoints
- Reliability regression tests for backoff, history, events, and profile repair

### Changed

- Serial, UDP, and fallback reconnect loops now share one recovery policy
- `health --history` can include historical metrics in human or JSON output
- Network audio remains deferred and documented as future work
- Package version advanced to 0.7.0

## [0.6.0] - 2026-07-23

### Added

- Discovery-driven serial, network, or fallback profile creation
- Configurable serial/network preference with runtime transport failover
- One-time command retry after a successful failover
- Continuous `health --watch` output and JSON health reports
- Connection, response, state, serial, network, and failover diagnostics
- Independent `AudioTransport`, `AudioStream`, and `AudioChunk` API groundwork

### Changed

- Profile files now use version 2 and can store both control endpoints
- Package version advanced to 0.6.0

## [0.5.3] - 2026-07-23

First planned GitHub prerelease.

### Added

- Reliable active LAN discovery with isolated per-host UDP sockets
- Bounded discovery parallelism and configurable worker count
- USB and network connection profiles stored as TOML
- Network health checks, statistics, diagnostics, and XML retry limits
- User-focused README and project documentation
- Contribution, support, security, and conduct guidance
- GitHub issue forms, pull-request template, and Dependabot configuration
- Package metadata, typed-package marker, build verification, and release checklist

### Changed

- CI now uses Node 24-based GitHub Actions majors
- CI verifies documentation links and built distribution metadata
- LAN discovery uses per-host timeouts and bounded concurrency

### Fixed

- `/24` discovery could miss a scanner because unrelated UDP errors, ARP delays,
  and shared-socket behavior interfered with valid replies
- Network XML handling now supports bare and fragmented `GSI`/`PSI` responses
- Strict MyPy narrowing in network XML decoding

## [0.5.2]

- Continued discovery after transient UDP refusal, reset, host-unreachable, and
  network-unreachable errors.

## [0.5.1]

- Improved discovery timeout placement, batching, and response draining.

## [0.5.0]

- Added LAN discovery, profiles, health checks, UDP counters, diagnostics, and
  bounded XML retries.

## [0.4.2]

- Completed strict typing for bare network XML response handling.

## [0.4.1]

- Added command-aware handling for bare `ScannerInfo` XML over UDP.

## [0.4.0]

- Added native SDS200 UDP control, multi-datagram XML reassembly, and network
  support across the existing command, state, trace, and monitor APIs.

## [0.3.1]

- Correctly handled the SDS200 `PSI` acknowledgment followed by streamed XML.

## [0.3.0]

- Added continuous `PSI` monitoring, state-difference events, live terminal
  display, traffic timestamps, and the public transport abstraction.

## [0.2.4]

- Added Ruff- and MyPy-clean shell completion integration.

## [0.2.3]

- Added Bash and Zsh completion for commands, flags, ports, profiles, and common
  scanner protocol commands.

## [0.2.2]

- Completed strict PySerial factory and write-return typing.

## [0.2.1]

- Fixed a serial-reader shutdown race and added regression coverage.

## [0.2.0]

- Added typed command objects, structured scanner XML, synchronized radio state,
  state events, traffic tracing, and the `scanner-info` command.

## [0.1.2]

- Established a Ruff-, MyPy-, and Pytest-clean transport baseline.

## [0.1.0]

- Added serial discovery, transport, packet framing, core responses, CLI tools,
  examples, tests, and CI.

[Unreleased]: https://github.com/stevenboyd78/sdsctl/compare/v0.29.2...HEAD
[0.29.2]: https://github.com/stevenboyd78/sdsctl/compare/v0.29.1...v0.29.2
[0.29.1]: https://github.com/stevenboyd78/sdsctl/compare/v0.29.0...v0.29.1
[0.29.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.28.1...v0.29.0
[0.28.1]: https://github.com/stevenboyd78/sdsctl/compare/v0.28.0...v0.28.1
[0.28.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.26.1...v0.27.0
[0.26.1]: https://github.com/stevenboyd78/sdsctl/compare/v0.26.0...v0.26.1
[0.26.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.20.2...v0.21.0
[0.20.2]: https://github.com/stevenboyd78/sdsctl/compare/v0.20.1...v0.20.2
[0.20.1]: https://github.com/stevenboyd78/sdsctl/compare/v0.20.0...v0.20.1
[0.20.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.16.1...v0.17.0
[0.16.1]: https://github.com/stevenboyd78/sdsctl/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/stevenboyd78/sdsctl/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/stevenboyd78/sdsctl/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/stevenboyd78/sdsctl/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/stevenboyd78/sdsctl/compare/v0.5.3...v0.6.0
[0.5.3]: https://github.com/stevenboyd78/sdsctl/releases/tag/v0.5.3
