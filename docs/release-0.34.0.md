# v0.34.0 release scope — adaptive dashboards and portable displays

Status: **Targeted for v0.34.0; not yet released**

This record defines the reviewed release boundary before an immutable tag is
created. Passing source, pull-request, browser, image, or package checks does
not prove publication or an installed public upgrade. Record the exact final
commit, tag object, public artifact identities, and installed acceptance only
after those separate gates complete.

## Identity and title

- Release version: `0.34.0`.
- Release title: **sdsctl v0.34.0 — Adaptive dashboards and portable displays**.
- Accepted source base before release preparation:
  `3e274469757a2ca34ed333b44e6335b873fbb102`.
- Release-preparation commit: pending review and commit.
- Final release commit and annotated tag object: pending.
- Latest completed public release remains `v0.33.0` until every publication
  and installed-release gate below passes.

## Included user-visible scope

### Universal Menu and reclaimed dashboard space

- Make the responsive **Menu** consistently available in standalone,
  static-preview, native-dashboard, and Home Assistant Ingress presentations.
  Keep appearance, scanner presentation, status details, typography, and
  role-appropriate session actions together without cloning control IDs or
  browser handlers.
- Retain the compact header health instrument while moving secondary page
  labels and update/protocol details into the drawer. This gives every theme
  more uninterrupted layout space without hiding an unhealthy connection.
- Preserve the same keyboard focus containment, Escape/Enter behavior,
  responsive geometry, expiry invalidation, and post-theme-change readability
  that were physically exercised on the managed display.
- Keep authorization server-enforced. Ingress omits native login/logout
  actions, display-only sessions cannot acquire scanner controls or recording
  authority, and the drawer does not provide unattended authentication.

### Deterministic Matrix rain

- Add a procedural code-rain layer to the Matrix theme with bounded fall speed,
  density, opacity, and trail length. Reduced-motion and forced-colors modes
  retain readable, nonessential-motion-free presentations.
- Sanitize and inject System, Department, Channel, Site, mode, and scanner
  screen text into selected drops. Each emitted live-data string completes one
  top-to-bottom lifetime; a changed value starts a new drop without rewriting
  one already in flight.
- Keep the effect browser-local and network-inert. It adds no remote font,
  image, telemetry, scanner transport, or persistence requirement.

### Live theme telemetry, rotation, and Synthwave

- Add normalized browser-local signal, RSSI, and activity presentation state
  derived from the radio state already delivered to the page. Full is the
  default live-effect level; Subtle reduces responses and Off freezes them.
  Reduced-motion, forced-colors, hidden-page suspension, and unavailable-value
  settling remain independent safeguards.
- Let supported themes consume that state through their own meters, beacons,
  rails, rain, maps, sweeps, horizons, and other native instruments. This adds
  no scanner polling or universal recolored template, and does not make
  presentation telemetry an operational measurement or control surface.
- Rebuild Synthwave Outrun as a distinct full-HD neon-highway panorama with a
  live drive-horizon response while preserving compact/mobile, reduced-motion,
  forced-colors, and deterministic static-preview fallbacks.
- Add optional browser-local rotation across two or more available themes with
  a 5-1440 minute interval and sequential or nonrepeating shuffled order.
  Rotation is off by default, pauses while its settings or page are hidden,
  resets after trusted user activity, restores the saved manual theme when
  disabled, and is not a burn-in guarantee.

### Explicit native-LAN display audio

- Rename the drawer to **Menu**, move Play/Stop and live playback status into
  it, remove the redundant Audio workspace tab, and migrate a saved Audio-pane
  preference to Diagnostics.
- Place browser audio source, packet, queue-overflow, and RTP-loss telemetry in
  its own **Live audio** Diagnostics card alongside separate Scanner and Daemon
  runtime cards.
- Add default-off `--lan-display-audio` support for explicitly configured,
  authenticated native-LAN display identities. Playback remains user-initiated
  and does not enable autoplay, scanner controls, recordings, a second scanner
  owner, or a new listener.
- Let a dedicated display-role kiosk opt into automatic playback only when the
  server audio boundary, a new role-bound private profile, the explicit kiosk
  automatic-audio flag, and the browser-local preference all agree. Stop
  suppresses automatic playback for the current session, terminal failures are
  not retried, and operator kiosks remain manual.
- Do not expose native-LAN display audio as a Home Assistant App option. The App
  retains its exact nine-field option/schema contract and ordinary Ingress
  authorization behavior.

### Scanner-mode-aware presentation

- Show meaningful channel identity when Weather, Search, or Close Call records
  omit a channel name. Weather and Tone-Out retain their numbered `WX`/`FTO`
  identity; unnamed Search and Close Call activity falls back to the active
  frequency in the dashboard and Home Assistant Display card.
- Let display-only kiosks optionally follow scanner-reported Waterfall mode,
  then return to Scanner after the mode ends. Debounce automatic switching and
  preserve a manual-view override.
- Reuse the existing persistent SSE/NDJSON Waterfall response. Do not add
  browser polling, another scanner transport, or competing scanner ownership.
  The display profile defaults automatic following on; operator sessions keep
  it off unless selected locally.

### Portable service configuration

- Add strict versioned `display-client` and `browser-kiosk` TOML manifests with
  unknown-field rejection, private-file and absolute-path checks, deterministic
  source provenance, exact command review, bounded preflight, and strict
  temporary command-line overrides.
- Export optional thin systemd launchers that invoke those manifests and then
  delegate to the existing validated TUI or browser-kiosk command. The release
  does not silently install, enable, migrate, restart, or deploy a service, and
  the v0.33.0 units remain available for existing installations.
- Keep the browser password and device credentials outside the manifest.
  Browser-device enrollment, credential rotation/revocation, and unattended
  authentication still require their separately accepted server protocol.

### Bounded foundations and qualification records

- Add a process-local, owner-bound Favorites web-workspace foundation with
  opaque workspace/navigation identifiers, exact source revisions, aggregate
  no-store review presentation, bounded lifetime/resources, and path-free
  failures. It adds no route, browser editor, durable draft, target discovery,
  or physical write.
- Add reviewed qualification plans for SDS100/SDS200 firmware 1.27.00 and
  CPython 3.15. Dependency-selection preflight is recorded, but Python 3.11-
  3.14 and scanner firmware 1.26.01 remain the accepted contracts until the
  separate runtime, container, browser, and attended hardware matrices pass.

## Included reliability work

- Preserve a retryable state when transient recording-arm identity locks
  contend while retaining bounded cancellation/deadline behavior and immediate
  terminal refusal.
- Keep supplemental recording helpers inside their private proc mount and PID
  namespace through readiness, cancellation, and cleanup instead of exposing
  host `/proc` state.
- Make documentation-only CI classification fail closed and retain the complete
  Real-Chrome acceptance requirement whenever the changed scope is not proven
  documentation-only.
- Stabilize the Python 3.13 clock, screenshot-boundary, qualified-peer,
  cancellation-clock,
  lost-child, passive-handoff, and operator-expiry fixtures at their actual
  event boundaries. These are test/audit corrections; they do not weaken
  security, ownership, publication, or product-behavior assertions.

## Compatibility and preserved boundaries

- Python remains supported and qualified on 3.11, 3.12, 3.13, and 3.14.
- The Home Assistant App keeps the same name, slug, image identity, nine fields,
  schema/defaults, credentials, ports, recordings, MQTT identities, ordinary
  Ingress behavior, and independently versioned Core integration/cards.
- Existing `0.33.0` App installations require no option migration. Browser
  presentation preferences remain client-local and safe unknown/legacy values
  continue to fall back to supported views.
- The new service manifests and launchers are opt-in. Existing units keep their
  current command lines until an administrator explicitly validates and
  installs a reviewed manifest-backed replacement.
- The daemon remains the sole scanner, recording, and RTP owner. The release
  does not authorize Favorites writes, scanner programming, automatic target
  activation, or a second live scanner connection.
- Native-LAN display audio is explicit and default-off. An ordinary package,
  container, or App upgrade cannot silently enable it or automatic kiosk
  playback.

## Explicitly deferred or excluded

- Unattended browser authentication, host-key enrollment, automatic login,
  credential migration, and private-profile creation remain separate work.
- Broader persistent configuration migration and cleanup beyond the reviewed
  version-one manifests and optional launchers remain follow-ups.
- A web Favorites editor, durable browser draft, target discovery, and any
  physical Favorites write are excluded; only the bounded server-side
  workspace foundation is included.
- Further original/cinematic theme overhauls remain future presentation work.
  This release includes the accepted live effects and Synthwave rebuild, not a
  claim that every planned visual redesign is complete.
- Python 3.15 support and completed SDS100/SDS200 firmware 1.27.00 qualification
  remain separate compatibility projects. This release includes their plans
  and preflight evidence only.
- Automatic deployment to Home Assistant, Raspberry Pi displays, scanners, or
  other live/private hardware is excluded.

## Pre-tag gates

Before creating `v0.34.0`, require all of the following on the exact reviewed
release commit:

1. Package/import/CLI and Home Assistant App metadata all equal `0.34.0`.
2. Root and App changelogs, README, roadmap, release index, release record,
   installation guidance, and reviewed wiki source describe the same
   included/deferred boundary and close the published v0.33.0 record.
3. The App options, schema, and translations retain the exact reviewed
   nine-field contract; Menu state, automatic Waterfall choice, rotation, live
   effects, Matrix settings, native-LAN display audio, and automatic kiosk
   audio are not App options.
4. Release-version, documentation, Ruff, MyPy, focused release, serial pytest,
   browser matrix, screenshot-repeatability, package/sdist/Twine, App
   packaging, service-configuration/Favorites foundations, diff, and secret
   checks pass from the dedicated clean worktree.
5. The normal release-preparation PR and post-merge CI, CodeQL, generic image,
   and Home Assistant image validations pass at exact identities with
   publication skipped.
6. Reviewed wiki source is published only after the preparation merge and
   verified against the merged repository source.
7. Target acceptance confirms Menu open/close/focus behavior, a theme change
   while open, the three-card Diagnostics layout, explicit Play/Stop audio,
   special-mode identity, automatic Waterfall entry/return, theme
   rotation/effect controls, Synthwave geometry, explicit/automatic audio
   boundaries, and ordinary dashboard recovery. Tests must not alter scanner
   programming, recordings, Home Assistant state, production profiles, or
   unrelated services.
8. Exact final main identity, clean state, merge topology, zero open
   main-branch code-scanning alerts, publication configuration, and immutable
   tag absence are independently verified.

## Publication and installed-release gates

After every pre-tag gate passes, create one annotated immutable `v0.34.0` tag
on the exact reviewed main commit and push it once. Never move or reuse it.

The genuine tag must start and successfully complete:

- Python build, release checks, and trusted PyPI publication;
- generic Docker Hub linux/amd64 and linux/arm64 image publication for
  `0.34.0` and `latest`; and
- Home Assistant GHCR amd64/aarch64 images plus the matching
  multi-architecture index.

Before creating the normal GitHub Release, independently verify wheel and
source-archive versions, contents, and SHA-256 identities; both container
indexes and architecture manifests; exact package/App/tag alignment; and a
clean installation of the public Python artifact. Validate the repository App
upgrade through its normal public catalog path while preserving options,
credentials, recordings, resources, and one scanner owner.

If any artifact is missing, inconsistent, or failed, record partial publication
and withhold the GitHub Release/Latest promotion. Only after every required
artifact and installed gate passes may this record change to **Released in
v0.34.0** with exact commit, tag, digest, and acceptance evidence.

## Upgrade and rollback boundary

An ordinary v0.33.0-to-v0.34.0 upgrade preserves App options/defaults and does
not select a theme, enable audio, submit credentials, execute a Favorites plan,
issue a scanner command, or activate a managed target. Browser preferences are
migrated only where documented; unsupported values fall back safely.

For Python, container, and App upgrades, pin the exact published version or
verified digest and preserve the prior installation as rollback input. For a
managed display, stop only its user service, preserve the private profile and
trust material, update the isolated package, rerun role-specific preflight and
interactive Menu/audio/mode verification, and restart only after acceptance.
Do not delete credentials, recordings, App data, browser profiles, resources,
or unrelated installations merely to make rollback succeed.
