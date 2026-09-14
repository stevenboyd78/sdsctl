# Milestones and releases

A **milestone** describes development and acceptance work. A **release** is a
version someone can install. Their numbers do not need to match. Use this index
to find the relationship, the [roadmap](../ROADMAP.md) for detailed work and
acceptance history, and the [changelog](../CHANGELOG.md) for versioned changes.
The [release policy and checklist](releasing.md#0-choose-release-scope-and-version)
define version selection and the required publication gates.

## Reading the status

- **Planned/deferred:** work or acceptance remains; no release is promised.
- **Merged, unreleased:** source is integrated, but is not yet a published change.
- **Targeted for vX.Y.Z:** included in a reviewed release plan, not yet released.
- **Partial publication:** some artifacts were published, or final release
  acceptance/closure failed. Read the specific limitation before installing.
- **Released in vX.Y.Z:** publication and the applicable release acceptance are
  recorded. This does not broaden the feature's documented support boundary.

Treat implementation, physical acceptance and publication as separate facts.
When only a slice ships, name that slice instead of marking the parent milestone
complete. Existing historical milestone IDs remain unchanged.

## Next release — targeted for v0.30.0

The [release scope and acceptance record](release-0.30.0.md) targets the reviewed
changes below for v0.30.0. This is not a published release. The low-priority endpoint
identity/duration follow-up stays deferred. Earlier private validation wheels
carrying `0.29.5` metadata remain distinct from both public and new candidate
artifacts.

| Work item | Development status | Release/support boundary |
| --- | --- | --- |
| [TUI local date/time presentation](../ROADMAP.md#low-priority-tui-usability-follow-up) | Merged, unreleased; PR #253 | Local RFC-style header/status dates accepted on both Pi geometries; not measured connection uptime |
| [Web Diagnostics remote-client connection ages](../CHANGELOG.md#0300---2026-09-14) | Merged, unreleased | Human-readable elapsed time, preserving the oldest-current-connection meaning |
| [LCARS content layering](../ROADMAP.md#managed-display-enrollment-and-unattended-recovery) | Merged, unreleased; PR #250 | Scoped presentation fix; does not qualify browser enrollment |
| [Recording-event continuity and theme spacing](release-0.30.0.md#private-candidate-acceptance--september-14-2026) | Accepted release candidate; PR #255 | Remote TUIs stay live during recording; corrected dashboard header spacing |
| [Deferred public API loading](../CHANGELOG.md#0300---2026-09-14) | Merged, unreleased; PR #250 | Preserve exports while focused helpers avoid loading unrelated APIs |
| [Managed-browser continuation and sign-out](browser-device-continuation-acceptance.md) | Merged, unreleased; PR #250 | Experimental; scoped acceptance does not qualify secure unattended keyring, abrupt power-loss or cross-build recovery |
| [Remote daemon version and actual connection duration](next-milestone-work-packets.md#1-tui-time-and-endpoint-identity) | Planned follow-ups | No release target; separate metadata/lifecycle and physical-layout gates |

The release scope records included slices, deferred work and support limits.
Each included row is targeted for v0.30.0 but remains unreleased until the
publication gates pass. Never reuse `0.29.5` or relabel private validation
artifacts as a public release.

## Recent milestone-to-release history

This is a selected navigation index starting at v0.25.0, not a replacement for
the full changelog or a claim that every part of a broad milestone shipped.
Unnumbered maintenance and presentation follow-ups intentionally have no new
milestone number.

| Milestone or named slice | Release | Publication and scope notes |
| --- | --- | --- |
| [29.3 release closure](../ROADMAP.md#closed-milestone-293--v0250-release-and-publication-closure) | [v0.25.0](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.25.0) | Released; see the linked closure for exact Home Assistant scope |
| [29.4–29.6 card/cadence/research work; 29.7 closure](../ROADMAP.md#closed-milestone-297--v0260-release-and-publication-closure) | [v0.26.0](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.26.0) | Released; GW2 research did not establish a supported binary acquisition path |
| [30.1 installation experience; 30.2 closure](../ROADMAP.md#closed-milestone-302--v0261-installation-release-and-publication-closure) | [v0.26.1](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.26.1) | Released; beginner documentation and the checked `sds200[all]` extra |
| [31.1 Waterfall history/pointer; 31.2 closure](../ROADMAP.md#closed-milestone-312--v0270-release-and-publication-closure) | [v0.27.0](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.27.0) | Released; duration-based history and display-only frequency inspection |
| [32.5 release candidate](../ROADMAP.md#closed-milestone-325--v0280-release-candidate-publication) | v0.28.0 | Artifacts published; final native-login acceptance failed and GitHub Release was withheld; superseded by v0.28.1 |
| [32.1–32.5 remote-client work; 32.6 correction/closure](../ROADMAP.md#closed-milestone-326--v0281-native-dashboard-login-correction-and-publication-closure) | [v0.28.1](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.28.1) | Released with the native-login correction; earlier immutable artifacts retained |
| [33.1 compact TUI; 33.2 managed display; 33.3 closure](../ROADMAP.md#closed-milestone-333--v0290-release-and-publication-closure) | [v0.29.0](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.29.0) | Released; transport-aware compact TUI and managed Raspberry Pi display |
| [Wide TUI layout follow-up](../ROADMAP.md#closed-v0291--wide-tui-production-display-follow-up) | [v0.29.1](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.29.1) | Released; no new milestone number |
| [TUI application header follow-up](../ROADMAP.md#closed-v0292--tui-application-header-and-production-acceptance) | [v0.29.2](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.29.2) | Released; no new milestone number |
| [34.1 manual-login kiosk and scoped 34.2 HDMI qualification](../ROADMAP.md#closed-milestone-341--display-only-native-browser-kiosk) | [v0.29.3](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.29.3) | Partial publication: containers and GitHub Release published, Python publication failed; not a complete Python release |
| [Manual-login kiosk publication recovery](../ROADMAP.md#closed-milestone-341--display-only-native-browser-kiosk) | [v0.29.4](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.29.4) | Released; does not qualify unattended production browser login |
| [Managed-TUI outage waiting screen](../ROADMAP.md#v0295-maintenance-release) | [v0.29.5](https://github.com/stevenboyd78/sdsctl/releases/tag/v0.29.5) | Released from the maintenance line; newer browser enrollment on main is excluded |

## Keeping the mapping current

For each release, update this index and the corresponding roadmap items from
the same reviewed scope record. Link back to the applicable milestone or named
slice from the versioned changelog/release notes. Record `Released in vX.Y.Z`
only after the release guide's required publication and acceptance gates pass;
until then retain the planned, unreleased or partial-publication status.

If an existing release contains only a foundation, label that foundation and
keep later work separate. Preserve failed-publication notes and original tags
when a subsequent version corrects them. Do not backfill uncertain historical
mappings by matching similar-looking version and milestone numbers.
