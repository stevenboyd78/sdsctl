# v0.30.0 release preparation — display improvements and experimental browser foundations

Status: **targeted, not published; artifact and installed-release gates pending**.
This is a release scope, not a new milestone or an installation guide. The
[milestone/release index](release-tracking.md) and [release process](releasing.md)
retain the distinction between integration, acceptance and publication.

## Identity and scope

- Target package/App version: `0.30.0`; proposed title:
  **sdsctl v0.30.0 — Display improvements and experimental browser foundations**.
- Source baseline: `417f9d14283f7afc1a4060ce68956468a071b3e1`, with the reviewed
  documentation policy at `b9ec344305e83d7efb8797d73d719a969c09cdaf` (PR #254).
  Verify that PR's merge and exact-head checks before release closure.
- New release commit, artifact hashes and installed-version receipts: pending.
  Earlier private `0.29.5` wheels are not release artifacts and must not be reused.
- This is a feature release because it adds opt-in experimental commands and
  capabilities, not only fixes to the 0.29 maintenance line.
- The release-preparation branch is not the public Home Assistant catalog.
  Do not merge its changed App version into main before matching images exist.

### Included user-facing changes

| Slice | Included behavior | Acceptance boundary |
| --- | --- | --- |
| [TUI date presentation](../ROADMAP.md#low-priority-tui-usability-follow-up), PR #253 | Local RFC-style dates, numeric offset and 24-hour time in the header and observed status changes | Both Pi layouts previously accepted; new versioned-client smoke remains pending; not socket uptime |
| [Diagnostics connection ages](web-dashboard.md) | `HH:MM:SS` or `Nd HH:MM:SS` for the oldest still-open remote connection | Preserve unavailable/invalid values; installed App smoke pending |
| LCARS layering, PR #250 | Keep scanner field text clear of decorative shapes | Both Pi LCARS observations retained; installed App/theme smoke pending |
| Deferred public API loading, PR #250 | Focused native helpers avoid loading unrelated public APIs until used | Preserve export identity and compatibility; clean-wheel import/native-helper tests required |
| [Experimental browser continuation/sign-out](browser-device-continuation-acceptance.md), PR #250 and its foundations | Explicit experimental preparation/start/maintenance commands, document-bound resume, durable pause and bounded same-origin sign-out | Fresh isolated profiles and the recorded scope only; not automatic adoption or unattended-production qualification |
| [Release tracking](release-tracking.md), PR #254 | Independent milestone/version identifiers and explicit release scope | Documentation, not a new runtime capability |

The versioned changelog keeps the detailed internal foundation history. Its
earlier statements such as "no CLI added" describe individual layers, not a
denial of the later explicitly experimental CLI composition. The top-level
release summary must explain the composed behavior and retain its opt-in limits.

### Deliberately not included as new support

- Remote daemon application-version display and measured connection duration.
- Secure unattended keyring, abrupt power-loss and cross-build browser-state
  qualification; complete unattended production deployment instructions.
- Firefox/WPE managed enrollment, alternative-engine lifecycle guarantees,
  automatic credential replacement or automatic recovery of uncertain state.
- New scanner commands, binary GW2 support, TUI Waterfall, Favorites mutations,
  new audio paths or an expanded v1.0/100-percent-coverage claim.

Existing services, devices and browser profiles must not be enrolled, migrated,
reset or granted new permissions by an ordinary package/App upgrade. Do not
replay retained failed or completed browser profiles to obtain new evidence.

## Home Assistant and publication ordering

The candidate retains the same seven-field public App schema as v0.29.5.
Advanced services remain off with `null` TCP mappings by default. Browser
authority creation and the two experimental options remain separate isolated
lab work; this release does not add them to the repository App catalog.
Retain all existing translations, credentials, recording paths, MQTT identities,
cards and Core-integration versions. Check supplied and omitted defaults against
the built image's strict parser, not just the current source checkout.

The ordinary main-first release recipe would advertise the new App version
before tag-triggered image publication. Use a separately reviewed release-branch
ordering for this preparation; this document does not authorize a tag push or
silently change the general release process:

1. Review/freeze the versioned release branch and its exact full CI/security and
   artifact checks while main still advertises the published v0.29.5 image.
2. Complete isolated candidate acceptance and prepare reviewed wiki/public notes.
   Keep candidate-only install commands away from the public wiki.
3. After release-branch/tag ordering is approved, create the genuine annotated
   tag at that reviewed commit. Verify every Python and container publication
   and artifact identity. A failed path is partial publication, not permission
   to move/reuse the tag.
4. Only after all required images exist, promote the reviewed catalog and
   version/docs changes to main without replacing newer development. Verify the
   merge and publish the matching reviewed wiki pages.
5. Complete exact published-package/App upgrade acceptance before GitHub
   Release/Latest promotion and `Released in` closure. Installation behavior
   must be observed; publication or image availability alone is not acceptance.

Before tagging, also finalize candidate wording and the release date, verify
the staged changelog comparison links against the exact release tag, synchronize public
installation examples, and recheck all modified artifacts/metadata. These
preparation-only labels and old public install pins must not ship as final notes.
The staged `v0.30.0` comparison links do not create a tag or establish publication;
they will resolve only after the separately approved tag exists.

## Validation and physical test plan

Record evidence against the exact candidate commit and artifacts. Earlier green
main checks and physical observations are supporting provenance, not new
versioned-release passes. Preserve the existing Python coverage floor and the
separate no-skip namespace gate; do not add overlapping test counts together.

Required automated gates:

- Release metadata, public App schema/defaults, translations and optional extras.
- Ruff/MyPy, full supported Python CI, namespace jobs, browser suite, real-browser
  audit, screenshot verification, package/container validation and open alerts.
- New wheel/sdist hashes, all packaged runtime/asset bytes, clean base and `all`
  installation, `pip check`, CLI/import versions and focused import behavior.
- Browser workers/native assets remain identical to the qualified graph except
  separately reviewed changes; schema/default startup remains non-mutating when
  experimental features are not configured.

Required scoped human checks, once an exact candidate and restoration plan exist:

| Surface | User observation | Preserve/restore |
| --- | --- | --- |
| Both bench Pi consoles | Correct local header/status dates, model/firmware separation, no clipping on 100x30 and 160x45 | Existing fonts, normal TUI services and independent credentials |
| Managed TUI outage/recovery | Waiting screen and fresh automatic live recovery on an App restart | Normal release/configuration; no credential rotation |
| Home Assistant candidate | Scanner state, human-readable Diagnostics age and LCARS metadata, controls/Waterfall, audio start/stop, recording finalization/play/download and restart persistence | Single scanner owner, recording library, mappings, MQTT and installed configuration |
| Experimental browser, only if required by an actual artifact/claim change | Exact fresh-profile paused/resume/sign-out/stopped-restart sequence | Separate fictional/private fixture; never reuse retained uncertain profiles |

Do not ask for every old browser fault scenario again when the artifact's tested
graph and support claim are unchanged. Any additional test must state what
changed, what evidence is missing and its exact expected observation.
