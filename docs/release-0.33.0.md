# v0.33.0 release scope — cinematic dashboards and responsive session controls

Status: **Targeted for v0.33.0; not yet released**

This record defines the reviewed release boundary before an immutable tag is
created. Passing source, pull-request, browser, image, or package checks does
not prove publication or an installed public upgrade. Update this record with
the exact final commit, tag object, public artifact identities, and installed
acceptance only after those gates complete.

## Identity and title

- Release version: `0.33.0`.
- Release title: **sdsctl v0.33.0 — Cinematic dashboards and responsive
  session controls**.
- Accepted source base before release preparation:
  `4e6182421f30b27367fb2a8de6c196618b3576b3`.
- Release-preparation commit: pending review and commit.
- Final release commit and annotated tag object: pending.
- Latest completed public release remains `v0.32.0` until every publication
  and installed-release gate below passes.

## Included user-visible scope

### Full-HD dashboard composition and scanner state

- Raise the bounded dashboard-shell width so a 1920×1080 kiosk uses the
  available display instead of retaining unnecessary side bars, while smaller
  layouts continue to use the established responsive bounds.
- Replace the primary one-row System/Department/Channel strip with a stable
  2×2 hierarchy: System and Department on row one, Channel and Site on row
  two. The arrangement gives long scanner names more room and uses the
  vertical space consistently.
- Give held System, Department, Site, and Channel fields a visible
  theme-native background/boundary treatment across all built-in themes.
  Labels, values, keyboard focus, and static styling remain authoritative;
  color or animation is never the only held-state indication.
- Replace the passive daemon/scanner status sentence with an operational
  health instrument that distinguishes healthy updating, static-preview or
  attention, offline/unavailable, login-required, and stale-data states.
  Warning states retain explicit text and do not depend only on motion or
  color.

### Cinematic dashboard collection

- Add 22 built-in Cinematic WebUI choices. Six are independently constructed
  Full-HD panoramic environments:
  - Carrier Air Wing;
  - Tactical Field;
  - Wizarding Archive;
  - Galactic Operations;
  - Mission Control; and
  - Cyberpunk Night.
- Give each of the six panoramas its own repository-local artwork, material
  system, instrument treatment, navigation composition, layout language,
  motion grammar, and supplemental held-state response. They are not one
  recolored scene or copied franchise artwork.
- Retain the other 16 Cinematic choices as compact/general-purpose colorways.
  They remain useful on smaller and mobile displays but do not claim the same
  panoramic depth or individually constructed scene system.
- Activate a panorama only when the viewport is at least 1280×720 and has an
  aspect ratio of at least 3:2. Short, narrow, mobile, and portrait viewports
  retain the common compact presentation.
- Preserve each complete static composition under `prefers-reduced-motion`.
  Forced-colors mode removes nonessential scenery while retaining controls,
  hierarchy, focus, health, and held-state meaning.
- Provide a truthful source-review mode only for `file://` checkouts. It is
  restricted to the six allowlisted panoramas, loads repository-local CSS,
  uses fixed sample data, makes no network requests, and is stripped from
  production-served HTML.
- Improve native Theme and Simple/Detail popup contrast and keep Mission
  Control navigation lamps in a dedicated lane so they cannot obscure labels.

### Responsive Display & session drawer

- Add one shared responsive drawer for authenticated operator and display-only
  kiosk sessions.
- Move the existing theme, System palette, LCARS palette, and typography
  controls into the drawer without cloning IDs, state, or event handlers.
- Show role-aware session state and logout, plus display-only navigation where
  the current role permits it. The drawer does not grant a capability or
  weaken the server-enforced role boundary.
- Contain keyboard focus while open, restore focus on close, preserve
  accessible names and state, and keep controls usable on compact viewports,
  under forced colors, and with enlarged text.
- Invalidate the drawer immediately on session expiry, HTTP 401, and managed
  sign-out. Stop stale activity and retries rather than presenting an
  apparently live session after authentication is lost.

### Documentation and validation reliability

- Correct the published v0.32 Home Assistant card resource documentation
  digests on main without moving the immutable v0.32.0 tag or republishing its
  artifacts.
- Harden the Real-Chrome audit's startup readiness, click/hover ownership,
  focus settling, layout-shift recovery, native-select capture, screenshot
  repeatability, and phone sequencing.
- Stabilize timing-sensitive supplemental-recording and clock-card fixtures
  without changing production authorization, scanner ownership, retry policy,
  or runtime behavior.
- Update the reviewed immutable GitHub Actions pins while preserving the
  existing security and publication gates.

## Compatibility preserved

- Python distribution/import name `sds200` and CLI name `sdsctl`.
- Python 3.11, 3.12, 3.13, and 3.14 support. Python 3.15 is not added by this
  release.
- Home Assistant App name, slug, image identity, and exact v0.31.0/v0.32.0
  nine-field options/schema contract. The App version advances only to align
  the packaged runtime, dashboard, card assets, and documentation.
- One daemon-owned scanner, PSI, audio, Waterfall, and recording service set.
- MQTT identities and controls, persistent recordings, credentials, port
  mappings, and disabled advanced-service defaults.
- Ordinary authenticated Ingress, manual native-dashboard authentication,
  server-enforced display/operator roles, and independently versioned Home
  Assistant Core integration artifacts.
- Existing browser profile identities, managed TUI services, and rollback
  installations. An upgrade does not switch a display kiosk to operator or
  create a new profile automatically.

## Explicitly deferred or excluded

- Host-key, device-key, automatic, or unattended kiosk authentication;
  automatic credential submission; password replacement; secure browser
  enrollment; cross-machine profile copying; abrupt-power-loss qualification;
  and alternative browser engines. The v0.32 manual login/session-expiry
  security contract remains in force.
- Automatic theme rotation, per-device rotation schedules, and a selectable
  rotation pool. Static theme choice remains browser-local.
- Automatically enabling browser audio for a display-only kiosk or changing
  autoplay, audio authorization, scanner ownership, or role capabilities.
- Favorites editing overhaul, a browser Favorites editor, background
  synchronization, storage-mode switching, replacing/deleting existing HPDs,
  or adding a public write action. The v0.32 additive-only library executor
  remains the only shipped Favorites-write boundary and publication does not
  authorize a physical write.
- Python 3.15 container bases from closed Dependabot PRs #323 and #324. The
  supplemental runtime inventory, handoff paths, qualification tests,
  classifiers, CI matrix, and release contract remain explicitly qualified
  for Python 3.11 through 3.14; 3.15 requires a dedicated migration.
- General front-panel control, additional keys, firmware/starting contexts,
  held gestures, sequences, retries, or inferred post-acknowledgement state.
- Automatic installation or target deployment. The Home Assistant App and
  reviewed Raspberry Pi stations remain unchanged until the public release is
  independently verified and a target-specific phase is separately
  authorized.

## Evidence accepted before release preparation

- PR #329 corrected the v0.32 Home Assistant resource documentation digests
  at exact head `d8777242516ff4cba87a96c2d55af3c62dcfb253`.
- PR #330 delivered the Full-HD shell correction at exact head
  `0447acf1ec5feb4a41d31a4e7886fae51557c901`.
- PR #332 delivered held-field styling across all built-in themes at exact
  head `3568fe21555e77aee6fa7d4094349b112035fbc6`.
- PR #340 delivered the Cinematic collection, 2×2 hierarchy, health
  instrument, safe local preview, responsive boundaries, documentation, and
  authoritative screenshots at exact head
  `8c6da4d80f846581d6ee7da875814b3d4b7fcc88`.
- PR #342 delivered the responsive Display & session drawer at exact head
  `57e9e24c10a292427b9549ed1bfca41b80da703a`. Its complete Real-Chrome
  audit passed all 840 matrix cases.
- PRs #331, #333–#339, #341, #343, and #344 repaired narrow test/audit
  reliability contracts. Those changes were reviewed and merged separately
  from user-visible behavior.
- PR #325 updated the monthly GitHub Actions group and aligned the integrity
  contracts. Exact-main CI `43017803993`, CodeQL `43017803363`, Docker
  validation `43017803511`, and Home Assistant App validation
  `43017803648` all succeeded on accepted main
  `4e6182421f30b27367fb2a8de6c196618b3576b3`; publication jobs were
  correctly skipped.
- The exact-main gate included full Python 3.11–3.14 suites, all four
  browser-namespace jobs, documentation links, the 840-case Real-Chrome
  dashboard audit, Mimic card acceptance, Same-Chrome screenshot
  repeatability, and wheel/sdist validation.
- A fresh release audit on November 11, 2026 found zero open main-branch
  code-scanning alerts and zero open pull requests. The two Python 3.15 image
  PRs were documented and closed without merge as deferred migrations.

## Pre-tag gates

Before creating `v0.33.0`, require all of the following on the exact reviewed
release commit:

1. Package/import/CLI and Home Assistant App metadata all equal `0.33.0`.
2. Root and App changelogs, README, roadmap, release index, release record,
   installation guidance, and reviewed wiki source describe the same
   included/deferred boundary.
3. The Home Assistant options, schema, and translations retain the exact
   reviewed nine-field contract; drawer state and theme choice are not App
   options.
4. Release version, documentation, Ruff, MyPy, focused release
   tests, complete serial pytest, browser matrix, screenshot repeatability,
   package/sdist/Twine, App packaging, diff, and secret checks pass from the
   dedicated clean worktree.
5. The normal release-preparation PR and post-merge CI, CodeQL, generic image,
   and Home Assistant image validation pass at exact identities with
   publication skipped.
6. Reviewed wiki source is published only after the preparation merge and
   verified against the merged repository source.
7. One target-specific 1920×1080 Chromium acceptance confirms the expanded
   shell, the Display & session drawer, readable selectors, the 2×2
   hierarchy, held fields, Mission Control compact/full-screen behavior, and
   at least one role-expiry/re-login recovery. This visual test does not
   authorize audio, scanner controls, recording, service changes, or target
   deployment beyond the approved test.
8. Exact final main identity, clean state, merge topology, security-alert
   state, publication configuration, and immutable-tag absence are
   independently verified.

## Publication and installed-release gates

After every pre-tag gate passes, create one annotated immutable `v0.33.0` tag
on the exact reviewed main commit and push it once. Never move or reuse it.

The genuine tag must start and successfully complete:

- Python build, release checks, and trusted PyPI publication;
- generic Docker Hub linux/amd64 and linux/arm64 image publication for
  `0.33.0` and `latest`; and
- Home Assistant GHCR amd64/aarch64 images plus the matching
  multi-architecture index.

Before creating the normal GitHub Release, independently verify the wheel and
source archive versions, contents, and SHA-256 identities; both container
index digests and architecture manifests; the exact package/App/tag alignment;
and a clean installation of the public Python artifact. Validate the
repository App upgrade through its normal public catalog path while preserving
options, credentials, recordings, resources, and one scanner owner.

If any artifact is missing, inconsistent, or failed, record partial
publication and withhold the GitHub Release/Latest promotion. Only after every
required artifact, installed gate, and target-specific visual acceptance
passes may this status change to **Released in v0.33.0** with exact commit,
tag, digest, and acceptance evidence.

## Upgrade and rollback boundary

An ordinary v0.32.0-to-v0.33.0 upgrade retains the Home Assistant nine-field
option contract and all defaults. It does not change a browser role, create or
copy a private profile, select a theme, enable theme rotation or audio, submit
credentials, execute a Favorites plan, enable a listener, or issue a scanner
command.

For Python, container, and App upgrades, pin the exact published version or
verified digest and preserve the prior installation as the rollback input.
For a browser kiosk, stop only that user service, preserve its private
role-bound profile and trust material, update the isolated package, rerun
role-specific preflight and interactive layout/session verification, and
restart only after acceptance. Do not delete credentials, recordings, App
data, browser profiles, resources, or unrelated installations merely to make
rollback succeed.
