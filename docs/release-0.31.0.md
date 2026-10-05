# v0.31.0 release scope — Mimic-SDS displays and installed profiles

Status: **Targeted for v0.31.0; preparation and publication authorized, not yet
released**

This record defines the reviewed release boundary before an immutable tag is
created. Passing source, pull-request, image or private-candidate checks does not
by itself prove publication or a repository-managed installed upgrade. Update
this record with exact public artifact and installed-acceptance identities only
after those gates complete.

## Identity and title

- Release version: `0.31.0`.
- Release title: **sdsctl v0.31.0 — Mimic-SDS displays and installed profiles**.
- Base accepted main before release preparation:
  `2b220923d05d57eb18151d5e89675124aaec08f7` (PR #266).
- Candidate and final release commits: pending controlled preparation PR and
  post-merge verification.
- Publication: pending; no `v0.31.0` tag or artifact is claimed by this record.

## Included user-visible scope

### Mimic-SDS presentations

- Include the read-only Mimic-SDS WebUI, daemon-backed TUI presentation and
  first-party Home Assistant card.
- Preserve the existing SDS200 Scanner, SDS200 Display and SDS200 Waterfall
  cards. Mimic-SDS is additional and uses the same authenticated App/session
  boundary; it creates no second scanner connection, password, mapped port or
  MQTT entity.
- Use one explicitly selected, reviewed and durably accepted display profile.
  Profile source/freshness remains explicit; an imported copy does not imply
  later scanner changes were detected.
- Retain strict frame endpoint, sequence, source and freshness validation.
  Disconnected, stale, ambiguous or rejected input clears current values rather
  than presenting old content as live.

### Installed display-profile administration

- Publish `scanner_display_config` in the Home Assistant App schema with an
  empty default. Empty keeps the complete profile-administration and Mimic-SDS
  installed path disabled.
- A non-empty value names an already provisioned absolute deployment TOML,
  normally `/data/scanner-display-deployment.toml`. Startup does not initialize,
  import, repair or delete profile state.
- The authenticated Ingress administrator keeps configured source separate
  from durable accepted source/revision. It reports source-copy status plus
  acquisition, successful acceptance and inspection times.
- Preview, one-use review, source-change confirmation, atomic per-file writes,
  explicit daemon reload and unconfirmed-result recovery retain their current
  bounded behavior. There is no automatic retry or scanner write.

### Qualified SDS200 Menu operation

- Publish `qualified_sds200_menu_control_enabled` as a separate strict boolean,
  default `false`.
- A true value enables only the previously physically qualified one-press Menu
  operation for an SDS200 reporting exact firmware `Version 1.26.01`.
- Every request still requires direct-network ownership, idle Waterfall, the
  existing owner lock and two fresh matching `Trunk Scan` / `trunk_scan` frames
  before exactly one `KEY,M,P` write.
- The acknowledgement proves command acceptance, not post-acknowledgement Menu
  visibility. There is no retry, held gesture, sequence, alternate starting
  context, other firmware, other key or inferred success.
- The Home Assistant card and daemon TUI remain read-only. Server authorization
  and the exact operator-Web route remain the only production request boundary.

### Other included development

The versioned root changelog is authoritative for the remaining merged changes,
including the LCARS v2 presentation, stable Home Assistant Waterfall sizing,
TUI Waterfall/read-only details/connection metadata, Favorites-profile
acquisition foundations and reliability corrections. Foundations named as
internal, experimental or deferred retain those labels when shipped.

## Compatibility preserved

- Home Assistant App name, slug and GHCR image identity.
- Single daemon scanner/PSI/audio/recording ownership.
- Existing MQTT device identity, topic prefix default and dedicated controls.
- Existing credentials, advanced-service defaults and TCP/UDP port mappings.
- Existing recording directory default and persistent App/media data.
- Ordinary authenticated Ingress and manual native-dashboard login.
- Independently versioned Home Assistant Core integration artifacts and their
  explicit install/restart lifecycle.
- Existing compact, display and Waterfall card types and individual resources.

Upgrading from v0.30.0 introduces two catalog fields only with the matching
v0.31.0 runtime. Their defaults preserve the v0.30.0 behavior: no profile
deployment and no Menu control. Do not publish the catalog ahead of the image or
copy either field into an older installed App schema.

## Explicitly deferred or excluded

- General front-panel control and the other 26 inventory keys.
- Another model, firmware, starting context, held gesture, key sequence, retry,
  replay after uncertainty or post-ack display claim.
- Automatic profile discovery, scanner/Favorites writes, storage-mode switching,
  background import/sync or inference that an accepted copy matches later
  scanner changes.
- Secure unattended browser sign-in, automatic keyring/enrollment deployment,
  abrupt power-loss recovery, cross-build rotation and alternative browser
  engine qualification.
- Publication of private candidate images, profiles, recordings, credentials,
  endpoint identifiers or acceptance backups.
- A redesign of Favorites management. The web-first Favorites UX follow-up is
  a separate post-release work packet; its later CLI/TUI alignment must share
  domain rules rather than duplicate them.

## Evidence already accepted before release preparation

- PR #266 merged exact accepted source
  `eea50dff84d287eb04c717bfdca935acbe2a9c7d` through merge
  `2b220923d05d57eb18151d5e89675124aaec08f7` after complete PR and post-merge
  CI, analysis, generic-image and Home Assistant-image validation.
- The exact private installed candidate
  `0.30.0-mimic-132ad7b0e40b`, image
  `sha256:896175b410bb5a23609ce70641fe125710dbe291e1772cdd4fa770f2071989db`,
  displayed separate configured/accepted sources and durable accepted revision
  `6c53067260ad8ac0b82a0e4d85eb08b0fca90a6f45bf2f4ef6d38bbcf4d54c69`.
  One clean App restart preserved exact profile/source/recording state and
  returned all three current frames. The final source change after that
  acceptance was test-only.
- One bounded physical qualification proved exactly one Menu press under the
  firmware/context boundary above. A later installed Ingress acceptance verified
  the default-off option projection and exactly one available inventory key
  without sending another press.
- Offline Mimic card layout, real-browser lifecycle, TUI/Pi geometry, live frame
  and installed candidate checks are recorded in the linked roadmap/work-packet
  evidence. They remain distinct from public-artifact acceptance.

## Pre-tag gates

Before creating `v0.31.0`, require all of the following on the exact accepted
release commit:

1. Package/import/CLI and Home Assistant App metadata all equal `0.31.0`.
2. Root and App changelogs, README, roadmap, release index and reviewed wiki
   source describe the same included/deferred boundary.
3. Public App options, schema and translations contain both new fields with
   empty/false defaults and the matching runtime/parser support.
4. Release-integrity, version, App packaging/runtime, profile/Menu/Mimic,
   documentation, Ruff, formatting, MyPy, syntax, browser, package/sdist/Twine,
   diff and secret checks pass.
5. Controlled PR and post-merge CI matrices, configured analysis, generic
   multi-platform image and Home Assistant amd64/aarch64 validation pass with
   publication skipped.
6. Exact final main identity, clean state, merge parents, security/dependency
   alert inventory and immutable-tag absence are independently verified.

## Publication and installed-release gates

After the pre-tag gates pass, one annotated immutable `v0.31.0` tag may be
created on the exact reviewed main commit. Do not move or reuse it.

Require independent verification of:

- PyPI wheel and source archive version, contents and SHA-256 identities;
- generic Docker Hub multi-platform index plus linux/amd64 and linux/arm64
  manifests;
- Home Assistant GHCR multi-platform index plus amd64 and aarch64 manifests;
- exact package/App/tag alignment and successful tag workflows; and
- absence of an unintended publication path.

Before creating the GitHub Release, back up the installed App's source/image,
options, Network mappings, App data, profile state, card resources and recording
inventory. Then use the published repository/catalog and image to prove the
documented v0.30.0-to-v0.31.0 upgrade, one scanner owner, Ingress, current frames,
persisted accepted revision/status, intact recordings and disabled defaults.
Exercise rollback/re-upgrade only through the documented recoverable procedure.
Do not send a scanner command, repeat profile import/reload, delete recordings,
change credentials or claim visual/audible success without an applicable human
observer.

If any artifact is unavailable or an installed gate fails, record a partial
publication and withhold the GitHub Release. Only after every required gate
passes may this status change to **Released in v0.31.0** with exact artifact,
merge, tag and acceptance evidence.
