# v0.32.0 release scope — operator kiosk and controlled Favorites writes

Status: **Released in v0.32.0 on November 3, 2026**

This record defines the reviewed release boundary and completed publication.
Source, pull-request, image, and private-candidate checks were kept separate
from public artifact and installed-upgrade acceptance.

## Identity and title

- Release version: `0.32.0`.
- Release title: **sdsctl v0.32.0 — Operator kiosk and controlled Favorites
  writes**.
- Accepted source base before release preparation:
  `a1dc36dbceadfb319f2d974e03c8b145dcfdfcff`.
- Release-preparation commit:
  `d240adfb0c8ea1006a41cbdd610695594a15bce0`.
- Final release commit:
  `91032d96d5c89c3e99d0ec3d1f543290616a2f68`.
- Annotated tag object:
  `3a6ba3bf73a12d91298bf4851f3b5356ef82c1d1`.
- The non-draft, non-prerelease GitHub Release was published on November 3,
  2026 after public artifacts and the repository-managed App upgrade passed.

## Included user-visible scope

### Explicit privileged browser-kiosk operator role

- Keep `display` as the backward-compatible default role and existing
  server-enforced observe-only boundary.
- Add `browser-kiosk --role operator` for a deliberately privileged,
  physically controlled station. It opens the ordinary native operator login
  and dashboard, including user-initiated audio, scanner controls, recordings
  and existing management operations.
- Require a distinct role-bound mode-0700 Chromium profile. Refuse cross-role
  reuse and refuse adoption of a legacy display profile by operator mode.
- Preflight the selected role's exact login route without submitting a
  password. The packaged graphical-session user service accepts the role as a
  non-secret setting while keeping passwords out of units, environment files,
  process arguments and logs.
- Retain manual authentication, Chromium sandboxing, autoplay policy,
  certificate validation and normal session expiry. The launcher creates no
  graphical session, changes no Getty, enables no listener and performs no
  automatic login.

### Configurable Mimic-SDS TUI startup

- Add default-off `tui_startup_view = "mimic"` application configuration and
  a temporary `tui --startup-view mimic` override.
- Open the existing read-only Mimic-SDS screen only after the normal Textual
  root application mounts and capability negotiation confirms a display
  source. Do not inject a key event or create another scanner connection,
  credential or daemon identity.
- Preserve `dashboard` as the default. Explicit Mimic startup fails closed when
  the connected daemon cannot provide the required display-frame capability;
  older daemons and direct sessions retain their ordinary interface otherwise.
- Let the packaged managed-display service read the existing versioned
  `config.toml` through its private service-account configuration directory.
  No systemd unit or `ExecStart` edit is required.

### Verified additive-only Favorites FTP write executor

- Include the reviewed library executor for one explicit, already reviewed
  Favorites write plan and one separately referenced writable FTP credential.
- Require two stable complete observations, exact baseline/plan agreement,
  case-insensitive collision checks, a private owner-only host backup and
  manifest, per-target lock, bounded staging readback and catalog-last
  activation.
- Permit only bounded addition of new HPD documents. Existing HPDs cannot be
  replaced or deleted, and the catalog must change when additions are made.
- Automatically restore the exact baseline after any post-mutation failure and
  report verified recovery separately from success. Retain recovery evidence
  when rollback cannot be verified.
- Shipping this API does not authorize a physical scanner write and adds no
  editor, CLI, daemon, Web, Home Assistant or background-sync action. A live
  execution still requires a fresh source/target review, private artifact
  destination and explicit bounded operator-approved plan.

### Presentation and reliability corrections

- Hide Mimic-only layout and alert controls while the ordinary dashboard is
  selected, while preserving each page-local choice.
- Add the two-column surrounding alert rails and visible inactive state across
  WebUI, TUI and Home Assistant. Qualify the configured Modulation label from
  reported signal level rather than presenting an active state at zero or
  unknown reception.
- Keep LCARS segmented navigation rails connected through responsive layout
  transitions without covering content or changing saved theme IDs.
- Correct bounded lifecycle behavior for supplemental acquisition, network
  audio, remote PCM finalization, PCMU idle disconnects, live-audio accept
  shutdown, shared Waterfall timeouts and post-start PSI recovery.
- Preserve an authenticated tty2 recovery console while the managed TUI owns
  tty1. The browser-kiosk user service remains unprivileged and independent of
  virtual-console ownership.

## Compatibility preserved

- Python distribution/import name `sds200` and CLI name `sdsctl`.
- Python 3.11, 3.12, 3.13 and 3.14 support; Python 3.15 is not added by this
  release.
- Home Assistant App name, slug, image identity and the exact nine-field v0.31.0
  options/schema contract. The App version advances only to align the packaged
  runtime and corrected card assets.
- One daemon-owned scanner, PSI, audio, Waterfall and recording service set.
- MQTT identities and controls, persistent recordings, credentials, existing
  port mappings and disabled advanced-service defaults.
- Ordinary authenticated Ingress, manual native-dashboard authentication and
  independently versioned Home Assistant Core integration artifacts.
- Existing display kiosk profile, managed TUI service and rollback
  installations. No upgrade changes a profile role automatically.

## Explicitly deferred or excluded

- Secure unattended browser sign-in, automatic credential submission,
  automatic browser enrollment, cross-machine profile copying, abrupt
  power-loss qualification and alternative browser-engine support.
- Treating an unlocked operator kiosk as observe-only or safe for untrusted
  physical access.
- Physical Favorites writes without a separately approved exact plan; replacing
  or deleting an existing HPD; background synchronization; storage-mode
  switching; or adding a public write action.
- Python 3.15 container-base changes later tracked by Dependabot PRs #323 and
  #324, plus the workflow update later reviewed through PR #325. None were
  included in the immutable v0.32.0 source or publication.
- General front-panel control, additional keys, firmware or starting contexts,
  held gestures, sequences, retries or inferred post-acknowledgement state.
- Automatic target deployment. Publication did not itself authorize a Raspberry
  Pi change; target-specific activation required a separate accepted phase.

## Evidence accepted before release preparation

- Configurable TUI startup merged through PR #270, with later service-account
  startup and recovery-console integration accepted through PRs #316 and #318.
- Mimic selector/alert presentation corrections merged through PRs #271, #272
  and #274 after deterministic browser/TUI/Home Assistant checks.
- Verified additive Favorites FTP writes merged through PR #280 at exact
  implementation commit `5f9fa3f`; automated stateful failure injection covers
  success, stale/collision refusal, rollback and retained recovery evidence.
  Physical write authorization remains deliberately absent.
- The operator role merged through PR #326 from exact head
  `69c9cfb04769cacc73e35d1bcc9582602e258f46` into accepted main
  `a1dc36dbceadfb319f2d974e03c8b145dcfdfcff`.
- PR #326 passed complete Python 3.11–3.14 suites, four browser-namespace jobs,
  documentation/package, CodeQL, generic multi-platform image validation and
  Home Assistant amd64/aarch64 validation. Publication jobs were correctly
  skipped for the pull request.
- Exact-main CI `41352907024`, CodeQL `41352907036`, Docker
  `41352907198` and Home Assistant App `41352907026` all completed
  successfully on `a1dc36dbceadfb319f2d974e03c8b145dcfdfcff`.
- A fresh release audit on November 2, 2026 found no open code-scanning,
  Dependabot-security or secret-scanning alerts. The three open automated
  dependency PRs are not accepted release content and have failed full-suite
  jobs.

## Pre-tag gates

Before creating `v0.32.0`, require all of the following on the exact reviewed
release commit:

1. Package/import/CLI and Home Assistant App metadata all equal `0.32.0`.
2. Root and App changelogs, README, roadmap, release index and reviewed wiki
   source describe the same included/deferred/experimental boundary.
3. The Home Assistant options, schema and translations retain the exact
   reviewed nine-field contract; operator role selection is not an App option.
4. Release version, documentation, Ruff, formatting, MyPy, complete serial
   pytest, browser, screenshot repeatability, package/sdist/Twine, App packaging,
   diff and secret checks pass from the dedicated clean worktree.
5. Normal release-preparation PR and post-merge CI, CodeQL, generic image and
   Home Assistant image validation all pass at exact identities with publication
   skipped.
6. The reviewed wiki source is published only after the preparation merge and
   verified against the merged repository source.
7. Required hardware/live smoke tests are either freshly completed under
   explicit authority or conservatively recorded as still pending. Do not infer
   scanner-control, audio, recording, service-restart or physical acceptance
   authority from automated checks.
8. Exact final main identity, clean state, merge topology, security-alert state,
   repository publication configuration and immutable-tag absence are
   independently verified.

## Publication and installed-release gates

After every pre-tag gate passes, create one annotated immutable `v0.32.0` tag
on the exact reviewed main commit and push it once. Never move or reuse it.

The genuine tag must start and successfully complete:

- Python build, release checks and trusted PyPI publication;
- generic Docker Hub linux/amd64 and linux/arm64 image publication for
  `0.32.0` and `latest`; and
- Home Assistant GHCR amd64/aarch64 images plus the matching multi-architecture
  index.

Before creating the normal GitHub Release, independently verify the wheel and
source archive versions, contents and SHA-256 identities; both container index
digests and architecture manifests; the exact package/App/tag alignment; and a
clean installation of the public Python artifact. Validate the repository App
upgrade through its normal public catalog path while preserving options,
credentials, recordings, resources and one scanner owner. Verify the new kiosk
CLI/profile contract from a public isolated installation without activating a
production station.

If any artifact is missing, inconsistent or failed, record partial publication
and withhold the GitHub Release/Latest promotion. Only after every required
artifact and installed gate passes may this status change to **Released in
v0.32.0** with exact commit, tag, digest and acceptance evidence.

## Upgrade and rollback boundary

An ordinary v0.31.0-to-v0.32.0 upgrade retains the Home Assistant option
contract and its defaults. It does not create an operator profile, change a
display profile's role, select Mimic-SDS startup, execute a Favorites plan,
enable a listener or issue a scanner command.

For Python/container/App upgrades, pin the exact published version or verified
digest and preserve the prior installation as the rollback input. For a kiosk,
stop only that user service, preserve its private role-bound profile and public
trust material, update the isolated package, rerun role-specific preflight and
interactive verification, and restart only after acceptance. For a managed TUI,
preserve the client profile/configuration and use its existing service boundary.
Do not delete credentials, recordings, App data, profiles or unrelated
installations merely to make rollback succeed.

## Published release and installed acceptance

The immutable annotated `v0.32.0` tag peels to the final release commit above.
The PyPI wheel and source distribution, Docker Hub amd64/arm64 version and
latest images, and GHCR Home Assistant App amd64/aarch64 images and aggregate
manifest were independently verified. A clean public Python installation
confirmed the CLI version and packaged operator-kiosk contract.

The public Home Assistant repository App was upgraded normally from 0.31.0 to
0.32.0 with its nine options, mappings, credentials, App data, recordings,
Lovelace resources, and Core identity preserved. The packaged runtime and card
assets, Ingress, passive daemon/scanner/PSI health, and the pre-existing
observe-only client passed the installed gate before the normal GitHub Release
became Latest. Publishing v0.32.0 did not authorize a physical Favorites write
or automatically activate an operator kiosk.
