# Roadmap

This document records ordered work planned for `sdsctl`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Active milestone

### Milestone 32.1 — Authenticated remote daemon client/server foundation

Milestone 31.2 is closed through reviewed pull request 218, merge commit
`8ae60aa4a8ed02a4e3f2a6657c4f8216634e52be`, the immutable annotated
`v0.27.0` tag, complete public-artifact verification, repository-managed Home
Assistant OS acceptance, reviewed wiki publication, a normal Latest GitHub
Release, and exact cleanup of its release branch and temporary validation
artifacts. Version 0.27.0 publishes bounded elapsed-time Waterfall history and
an optional display-only frequency pointer while retaining legacy frame-depth
configurations and the relative, uncalibrated, single-owner data contract.

Milestone 32.1 establishes the first supported authenticated network transport
for remote daemon-backed clients. The primary topology is one scanner-owning
daemon, running on an ordinary host, in a generic container, or in the Home
Assistant App, with multiple independently reconnecting thin clients. A
Raspberry Pi or other display may open the existing authenticated HTTPS web
dashboard in a kiosk browser or run a TUI backed by the new remote daemon-client
transport; no display client opens its own scanner control, PSI, Waterfall, or
RTSP/RTP audio session.

Existing private Unix-domain sockets remain the default and continue to serve
same-host clients without network configuration. Every host-facing TCP listener
is disabled unless explicitly enabled, binds one operator-selected private,
unique-local, or link-local address rather than a wildcard, uses authenticated
encryption, and publishes one documented, configurable port. A process inside
an isolated container may bind its container interface only behind one explicit
orchestrator host-port mapping while host networking remains disabled. Reject
anonymous access, plaintext fallback, wildcard host publication, public or
multicast discovery, credentials in URLs, and implicit exposure caused only by
enabling container host networking. Preserve bounded framing, request IDs,
protocol-version negotiation, timeouts, backpressure, redacted failures, and
fail-closed behavior before dispatching any existing daemon operation.

Define least-privilege client authorization at the transport boundary. A
display-only client may observe bounded state, ordered events, diagnostics, and
demand-driven relative Waterfall data without control authority. Explicit
operator capability may additionally use the existing typed semantic controls;
it must not expose unrestricted raw scanner keys, the generic MQTT command
envelope, filesystem paths, Home Assistant tokens, Ingress identifiers, scanner
addresses, recording contents, Favorites bytes, or provider credentials. Client
identities must be independently revocable and safe to provision in mode-0600
configuration or an equivalent platform secret store without logging secret
material.

Create one transport-neutral daemon-client abstraction shared by local and
remote CLI/TUI consumers rather than duplicating scanner or application
semantics. Remote startup must validate the server identity and negotiated
protocol before presenting cached state. Disconnect, stale-event detection,
ordered resynchronization, stream-generation changes, credential rotation, and
server restart must recover without a client restart where safe. Isolate slow,
malformed, unauthenticated, and abruptly disconnected peers with bounded
per-client queues and leases so one client cannot block the daemon or another
display.

Add an advanced Home Assistant App configuration without weakening Ingress.
Authenticated Ingress remains the default dashboard path and its trusted
Supervisor listener is never published as a general LAN port. When an operator
opts in, App metadata and documentation may expose only the dedicated remote
daemon-client port and the existing native authenticated HTTPS dashboard port.
The Home Assistant-only lifecycle workspace remains available solely through
Ingress, even when the native dashboard is reachable directly. Retain the
existing UDP 50000 scanner-to-daemon RTP mapping as an input rather than
misrepresenting it as a client/server port.

Document beginner and advanced deployment recipes for an ordinary Python host,
Docker or Compose, and Home Assistant OS. Include firewall direction, TLS trust
and credential provisioning, port mapping, revocation, service restart,
diagnostics, and a concrete one-daemon/multiple-display example. The documented
Raspberry Pi acceptance topology must cover at least two simultaneous display
clients at the 800 by 480 reference size, including one remote TUI and one kiosk
browser, plus an independent administrative client. Make clear that direct
browser access uses the daemon host's native HTTPS dashboard while the TUI uses
the remote daemon-client transport.

Add deterministic unit, integration, hostile-peer, real-browser, container,
and Home Assistant App coverage for disabled-by-default listeners, exact bind
policy, authentication and revocation, authorization scopes, protocol mismatch,
malformed and oversized frames, replay or duplicate requests, connection and
queue limits, slow consumers, fanout ordering, reconnect, shutdown, and log and
diagnostic redaction. Physical acceptance must prove all concurrent clients
share exactly one scanner owner, one PSI stream, one demand-driven Waterfall
session, and one daemon-owned RTSP/RTP audio input while independent client
disconnects release only their own leases.

The implemented foundation now includes the strict remote configuration and
preflight model, versioned challenge/proof authentication, TLS 1.3 admission,
exact-address bounded TCP listener, per-scope API authorization, and atomic
credential generations. Credential reload constructs a complete replacement
before commit, preserves the last-known-good registry on failure, and closes all
prior-generation sessions on successful rotation or revocation. Authenticated
observation leases now attach to the existing ordered-event, shared Waterfall,
and accepted-PCMU publishers with aggregate and per-identity limits,
source-specific redaction, independent release, slow-consumer isolation, and
generation-linked invalidation. These objects remain explicit construction
boundaries and are not yet wired into a shared remote client transport,
packaged daemon startup, or deployment port metadata.

Trusted reverse-proxy identity, Internet/public exposure, wildcard binding,
third-party identity providers, browser-stored bearer credentials, automatic
LAN discovery, multi-user administration, scanner sharing between multiple
daemons, GUI implementation, and broader scanner-family validation remain
outside Milestone 32.1. The milestone adds no scanner protocol, Favorites write,
RadioReference execution, RF calibration, tuning-from-Waterfall, or Home
Assistant resource-registration authority.

#### Closed Milestone 31.2 — v0.27.0 release and publication closure

Milestone 31.2 synchronized the Python package, import version, and Home
Assistant App at 0.27.0 while retaining the independently packaged Home
Assistant Core integration at 0.1.5. Pull request 218 merged as
`8ae60aa4a8ed02a4e3f2a6657c4f8216634e52be` after complete static, test,
documentation, distribution, clean-install, browser, screenshot, generic
container, and Home Assistant App validation. Reviewed wiki source was published
as commit `4e09aa030bd4e993f80a095c17e0e241206f5839` before tagging.

The genuine annotated `v0.27.0` tag object
`d24897dcd4ea8a43d762a46fb48fe44bbea1ad8e` points to that exact merge commit.
Tag-gated workflows published and verified the Python wheel and source
distribution, the amd64 and arm64 generic Docker image, and the amd64 and
aarch64 Home Assistant App images plus both multi-architecture manifests. The
normal GitHub Release was created from the genuine tag and marked Latest with
the reviewed comparison, alpha-API warning, compatibility notes, and relative,
uncalibrated, inspection-only limits.

Repository-managed Home Assistant acceptance upgraded the published App from
0.26.1 to 0.27.0 on amd64 Home Assistant OS 18.2, Core 2026.8.3, Supervisor
2026.08.0, and Docker 29.6.2 against an SDS200 running firmware 1.26.01.
Operator options and the exact persistent-recording tree were preserved. Ingress
reconnected after Core restart; the 24-component integration contract, three
legacy frame-count cards, duration and pointer behavior, controls, browser
audio, recording finalization, saved playback, live span, and restart recovery
all remained valid. The operator-managed aggregate resource was advanced to the
installed 0.27.0 digest without giving the App resource-registry authority.

Closure retained the repository-managed 0.27.0 App as the sole scanner owner,
removed its exact temporary resource-registry rollback copy, deleted the merged
release branch locally and remotely, and removed the clean temporary wiki,
known-hosts, and release-note files. No Local App, private integration copy,
temporary duration card, source directory, staging worktree, private capture,
credential, or release-validation recording remains.

#### Closed Milestone 31.1 — Duration-based Waterfall history and frequency pointer

Milestone 30.2 is closed through reviewed pull request 213, merge commit
`9e693474e9bbd7c551a302b3c66df450a86011f9`, and the immutable `v0.26.1` tag.
Public PyPI now exposes the reviewed `sds200[all]` dependency union and concise
beginner README; the generic Docker and Home Assistant App multi-platform
images, reviewed wiki, GitHub Release, and proportionate Home Assistant OS
upgrade acceptance are published and verified.

Milestone 31.1 improves inspection of the already-qualified relative Waterfall
without adding another scanner session or changing its authoritative text
`PWF`/`GWF` data plane. Add bounded duration-based history to the web dashboard
and first-party Home Assistant Waterfall card so operators can select a useful
time window even when actual frame cadence varies. Existing 60-, 120-, and
240-frame card configurations remain valid and retain their current meaning;
duration mode is an explicit alternative rather than a silent migration.

History retention must use each accepted frame's ordered capture or receipt
time, prune data outside the selected window, cap both elapsed duration and
frame count, and clear all retained samples on the existing Clear action,
stream-generation changes, invalid sequence transitions, and teardown. Paused
rendering may preserve the bounded visible history but must not create an
unbounded hidden backlog. Reconnect, visibility, and final-consumer cleanup
retain the existing independent demand-lease and one shared scanner-side
Waterfall session contract.

Add an optional display-only frequency pointer over the spectrum and Waterfall
surfaces. Derive its value only by linear interpolation across the current typed
lower and upper span reported by the scanner, using the same geometry as the
rendered bins. The pointer must follow span changes immediately, make its
precision and MHz unit explicit, work with mouse, keyboard, and touch input,
and remain readable without obscuring primary status or controls. When current
span metadata is missing or invalid, hide the frequency value rather than reuse
stale bounds or invent a scale.

Pointer interaction is inspection only. It must not tune, hold, search, change
center frequency or span, send raw scanner keys, retain a browser-visible
frequency history, or claim calibrated power, FFT bins, signal bandwidth, or
emission identity. Waterfall samples remain relative and uncalibrated. No new
high-rate MQTT entities, public endpoints, credentials, scanner addresses, or
Home Assistant resource-registration mutations enter this milestone.

Define one renderer-neutral duration and pointer model shared by the web and
Home Assistant implementations, with bounded configuration parsing and safe
fallbacks for older saved card configurations. Add deterministic unit, browser,
graphical-editor, multi-card, resize, visibility, accessibility, reduced-motion,
and screenshot coverage at the established phone, Raspberry Pi, desktop, and
full-screen reference sizes. Run the complete static, test, documentation,
package, generic-container, Home Assistant App, and real-browser validation
appropriate to the changed surfaces, followed by physical SDS200 and Home
Assistant OS acceptance of duration pruning, pause, clear, live span changes,
pointer accuracy, final-card cleanup, and scanner-session recovery.

Physical acceptance completed on September 1, 2026, from pull-request head
`91ec4f1c6ae6568ead5868b9f5814ada8596592f` through one deliberately named
Local App on an amd64 Home Assistant OS 18.2 host running Core 2026.8.3,
Supervisor 2026.08.0, and Docker 29.6.2 against an SDS200 running firmware
1.26.01. The authenticated web renderer sustained 4.0 frames per second while
15-second duration pruning, pause without hidden backlog, clear and progressive
rebuild, pointer and keyboard interaction, and live 720 kHz, 1.44 MHz, and
2.88 MHz span changes all passed without reconnecting or changing scanner
state.

The exact aggregate Home Assistant card module then loaded three unchanged
60-, 120-, and 240-frame cards beside one graphical-editor-created 15-second
duration card. All four shared the existing scanner-side session at 4.0 frames
per second; per-card pause and clear remained independent, final hidden-card
demand released cleanly, and visibility plus guarded App restart recovery
reconnected automatically with empty histories and the restored live scale.
The direct non-Ingress probe remained forbidden. Intermittent single GWF misses
recovered at `consecutive_failures=1` without a catch-up burst or renderer
failure. The implementation and physical gate are therefore complete pending
pull-request review and merge.

Closure removed the temporary duration card, restored the published v0.26.1
App as the sole scanner owner, and registered its exact released aggregate
module digest
`dbbb246abbf82fff9040c2d3a4ccb7f94ef634bf56795c0c356737bb5faac37f`.
The three retained frame-count cards rendered normally. The stopped Local App
was then uninstalled, its exact `/addons/sds200_31_1` source and local staging
directory were deleted, and the App catalog was reloaded. Post-cleanup audit
found no installed Milestone 31.1 App or retained temporary directory while the
published v0.26.1 App remained started.

TUI Waterfall rendering, GUI work, scanner tuning, click-to-hold or click-to-
search behavior, alternative GW2 syntax, binary negotiation, ProScan wire
comparison, FFT calibration, public or anonymous access, weather-alert
recording, automatic RadioReference synchronization, and broader scanner-family
validation remain outside Milestone 31.1.

#### Closed Milestone 30.2 — v0.26.1 installation release and publication closure

Milestone 30.2 synchronized and published version 0.26.1 after complete static,
test, documentation, distribution, clean-install, container, browser, Home
Assistant, and release-contract validation. The release made the reviewed
`sds200[all]` extra and beginner installation experience public, retained the
separately versioned Home Assistant Core integration at 0.1.5, verified every
public artifact from the reviewed release commit, and completed exact Home
Assistant acceptance and cleanup without retaining private validation state.

#### Closed Milestone 30.1 — Installation experience and beginner documentation

Milestone 30.1 replaced the oversized README with a concise project landing
page, reorganized reviewed wiki source around beginner deployment targets and
user goals, added the exact checked `sds200[all]` runtime-extra union, and added
documentation contracts that validate README length, declared extras, and wiki
destinations. It changed no scanner, daemon, web, audio, Favorites, Waterfall,
MQTT, container, or Home Assistant runtime behavior and performed no package,
container, App, tag, release, or wiki publication.

#### Closed Milestone 29.7 — v0.26.0 release and publication closure

Milestone 29.7 synchronized and published version 0.26.0 after complete static,
test, documentation, distribution, container, browser, screenshot, Home
Assistant, and release-contract validation. The release retained the separately
versioned Home Assistant Core integration at 0.1.5, verified all public
artifacts from the reviewed release commit, and completed exact post-acceptance
cleanup without retaining private validation state.

#### Closed Milestones 29.4 through 29.6 — Card loading, cadence, and GW2 research

Milestone 29.4 added one digest-qualified first-party aggregate Home Assistant
card resource while retaining the three individual compatibility URLs and the
existing symlink-refusing, atomic, byte-verifying installation boundary.
Milestone 29.5 removed completion-relative text-GWF drift, added bounded
round-trip and scheduler telemetry, and carried independently refreshed typed
GST range data to the web dashboard and first-party Home Assistant Waterfall
card. Both milestones completed full static, test, package, container, browser,
and physical Home Assistant OS validation before merge.

Milestone 29.6 added a bounded, exact-byte, renderer-neutral GW2 research
substrate and one guarded physical probe on the SDS200 running firmware 1.26.01
over LAN UDP control. Exact `GW2,1,ON` returned `ERR\r` and no binary frame;
paired cleanup and Home Assistant recovery passed. The qualified phase-stable
text `PWF`/`GWF` path remains authoritative unless stronger vendor
documentation or independently reproducible evidence establishes a safe,
materially beneficial binary contract.

#### Closed Milestone 29.3 — v0.25.0 release and publication closure

Milestones 29.1 and 29.2 are closed after completing the responsive Home
Assistant waterfall card, standard Home Assistant media-source live scanner
audio, authenticated App transports, shared daemon-owned demand and playback
leases, web and card palette expansion, lifecycle-interface hardening, physical
Home Assistant OS acceptance, cleanup, reviewed pull-request merges, and exact
post-merge validation on `main`. The companion Docker Buildx and build-push
action updates are also closed through reviewed immutable pins and synchronized
release-integrity tests in pull request 205; the incomplete Dependabot pull
requests 188 and 189 are closed as superseded.

Milestone 29.3 is release closure rather than another runtime feature slice.
Synchronize the Python distribution, import version, and Home Assistant App at
0.25.0. Retain the independently versioned Home Assistant Core integration at
0.1.5 unless a reviewed integration change requires a new artifact version.
Freeze the complete repository and Home Assistant App changelogs with explicit
Milestone 29.1 and 29.2 release notes, exact comparison links, and one coherent
public account of the waterfall, media-source, palette, lifecycle, and
dependency-maintenance boundaries.

Audit the README, release guide, container and Home Assistant documentation,
live-audio guide, web-dashboard and theme guides, project vision, reviewed wiki
source, generated reference screenshots, package metadata, example commands,
and pinned public image references for 0.25.0. Documentation must distinguish
the standard `media-source://sdsctl/live` workflow from browser audio and
finalized recordings, preserve the single SDS200 RTSP/RTP owner, explain Home
Assistant target reachability and `internal_url`, and state the accepted
Home Assistant OS, Core, Supervisor, frontend, Docker, scanner model, and
firmware evidence without publishing private addresses, credentials, capability
material, recordings, Favorites content, provider data, or local identifiers.

Run the complete Ruff, MyPy, multi-version pytest and coverage, documentation,
real-Chrome, screenshot-gallery and repeatability, source and wheel distribution,
Twine, release-integrity, generic Docker/Compose/Podman, Home Assistant App
amd64/aarch64, custom-integration packaging, CodeQL, dependency-graph, and
tag-contract validation. The shared coverage floor remains 86 percent. Release
artifacts must be rebuilt from the reviewed source and must not rely on retained
Milestone 29.1 or 29.2 Local App, integration, share, or browser-cache state.

Pull requests, `main` pushes, and manual workflow dispatches may build and
validate the generic Docker and Home Assistant App images, but may not publish
release tags. Only one genuine matching `v0.25.0` tag on the exact reviewed
release commit may publish the versioned and moving image tags. The Python
distribution and GitHub Release must use the same commit, version, changelog,
and artifact set. Workflow success alone is not release acceptance.

After tag-gated publication, verify the GitHub Release, source archive, wheel,
generic multi-platform Docker image, Home Assistant amd64/aarch64 images,
repository metadata, checksums, and public documentation. Complete a clean
installation or upgrade through the published Home Assistant repository, prove
the App owns the scanner exactly once, and revalidate Ingress, MQTT Discovery,
all first-party cards, browser audio, finalized recordings, controls, waterfall,
and the optional 0.1.5 Core integration lifecycle. Restore the published App as
the sole owner and remove any deliberately named release-validation components.

Automatic or background RadioReference synchronization, silent conflict
resolution, MyRR scraping, undocumented interfaces, new Favorites mappings,
writable FTP, daemon/web/Home Assistant Favorites execution, another scanner
connection, public or anonymous live-audio URLs, arbitrary transcoding profiles,
whole-home synchronization claims, weather-alert recording, TUI waterfall
rendering, GUI implementation, and the external implementation-review message
remain outside this release. No new runtime capability enters Milestone 29.3.

##### Closed Milestone 29.2 acceptance record

Milestone 29.1 is closed with the responsive Home Assistant waterfall card,
authenticated event-stream transport, shared demand leases, deterministic
desktop, wall-display, and phone references, physical Home Assistant OS
acceptance, and post-merge CI on `main`. The live run identified and corrected
Ingress buffering, then restored the published v0.24.0 App as the sole scanner,
waterfall, and audio owner. The detailed redacted evidence remains in
[the Home Assistant App guide](docs/home-assistant-app.md#milestone-291-responsive-waterfall-card-development-acceptance).

Milestone 29.2 makes the daemon-owned live scanner audio available through Home
Assistant's standard media-source and media-player workflow. It adds one
browsable, playable `media-source://sdsctl/live` item; it does not model the
scanner as an output `media_player` entity. Existing finalized WAV recordings
remain available through Home Assistant local media, and existing browser audio
remains a separate explicitly started client.

Follow the current
[Home Assistant media-source platform](https://developers.home-assistant.io/docs/core/platform/media_source/)
contract: the Core-side integration resolves the live item to one playable URL
and its exact MIME type. Home Assistant does not transcode media-source content,
so the App must provide an evidence-selected streaming representation that the
documented target class can consume. Do not label raw PCMU, headerless PCM, or an
indefinite WAV response as a broadly compatible stream without deterministic
container, codec, seek, duration, disconnect, and representative-player
evidence. Unsupported players must fail clearly rather than receive a disguised
or silently downgraded format.

App Ingress remains the authenticated human-browser boundary and must not be
used as a media-device credential. Add a first-party Core-side media-source
bridge and a private App-side live-audio service. The media player receives only
a Home Assistant-owned, bounded-lifetime playback URL; it must not receive an
Ingress identifier, Supervisor token, App-internal hostname, scanner address,
long-lived bearer token, or operator credential. Core-to-App access must use a
least-privilege, rotatable capability with strict origin, path, method, lifetime,
and concurrency checks. Authentication material must remain out of MQTT,
browser storage, entity state, diagnostics, filenames, and error text.

The Core-side integration must be a versioned, reviewable artifact with explicit
install, update, rollback, and removal behavior. The App may stage or offer that
artifact through a documented operator action, but it may not silently write,
activate, or restart a Home Assistant custom integration. Published and Local
App identities must be resolved without hard-coding one repository hash, and a
missing, mismatched, stopped, or incompatible App must produce a bounded
unavailable result rather than scanning the LAN or falling back to a public
listener.

The daemon remains the only SDS200 RTSP/RTP owner and the existing accepted-PCMU
and decoded-PCM routers remain the source of truth. One shared, bounded encoder
or container pipeline per selected representation fans out to independent Home
Assistant playback leases. Starting a second media player must not start a
second scanner audio session or duplicate decode work. Slow consumers may drop
bounded data or disconnect, but may not block RTP reception, browser audio,
recording, another media target, scanner control, PSI, or waterfall delivery.

Each resolved playback owns one short-lived lease with explicit creation,
first-byte, idle, maximum-duration, client-disconnect, target-stop, App-restart,
Core-restart, and final-lease cleanup semantics. Abandoned URLs and half-open
clients must expire server-side without relying solely on a frontend stop event.
Revocation must close only the affected playback; releasing the final live-media
lease must stop its shared encoder demand while leaving daemon audio ownership
available to recordings and other explicit subscribers.

Volume, mute, pause, resume, and stop behavior must respect Home Assistant's
roles. Output volume and mute belong to the selected target media player.
Scanner volume, squelch, and mute remain explicit scanner controls and may not be
changed as a side effect of media playback. Pause may be supported only when the
selected representation and target have bounded semantics; otherwise expose
stop and restart honestly instead of buffering an unbounded live backlog. The
live item is not seekable and must not advertise a finite duration.

Expose low-rate, redacted lifecycle and failure evidence sufficient to diagnose
resolving, waiting for scanner audio, streaming, target disconnect, expiry,
encoder failure, App loss, and compatibility rejection. Do not publish audio
payloads or packet-rate telemetry through MQTT, Home Assistant state, events, or
logs. Existing RTP continuity, per-subscriber loss, and daemon health remain the
authoritative operational evidence.

Add daemon, App transport, Core-integration, authentication, format, concurrent-
consumer, malformed-client, expiry, packaging, upgrade, rollback, and removal
coverage. Use synthetic audio fixtures and redacted metadata in committed tests.
Document the exact supported container, codec, MIME type, latency expectations,
target limitations, network reachability, automation syntax, and the distinction
between live scanner audio and finalized recordings. Keep Core and App version
compatibility explicit and fail closed across mixed-version upgrades.

Physical Home Assistant OS acceptance must browse and resolve the live item,
start and stop it through the standard `media_player.play_media` workflow on at
least one representative reachable target, exercise two concurrent playback
leases when the available target set permits, and prove final-lease cleanup,
App restart recovery, bounded latency, and unchanged single RTSP/RTP ownership.
It must also revalidate browser audio, recording and finalized media, scanner
controls, waterfall clients, MQTT Discovery, existing cards, and persistent
data. Only one deliberately named Local validation App and one explicitly
installed Local integration may exist during the run; published components must
be restored afterward.

The first physical slice on Home Assistant OS 18.2 and Core 2026.8.3 proved
root browsing, canonical artwork, standard `media_player.play_media` resolution,
audible continuous MP3 playback on VLC-TELNET, normal target stop to idle, and
unchanged single scanner/RTSP ownership. It also identified and corrected Core's
valid null root-identifier form. Later slices closed the diagnostics,
final-encoder, App-restart, Ingress lifecycle, and complete regression gates
described below.

The same physical run also replaced the three Home Assistant card registrations
with manifest-declared, SHA-256-qualified resource URLs. Direct HTTP and external
HTTPS dashboard origins then rendered the complete nine-card verification view
without configuration errors, including the previously stale Auto Display card.
This closes the browser-origin cache defect without weakening exact module-byte
validation or exposing private deployment data.

Post-restart diagnostics proved a second playback resolution and redemption with
no rejection or expiry, but retained one active Core proxy lease after the
VLC-TELNET target returned to `idle`. Integration candidate `0.1.5` added a
downstream Home Assistant transport check between bounded MP3 chunks. Physical
HAOS revalidation then streamed audible scanner audio to a remote VLC-TELNET
target, stopped it normally, and reported exactly one issued and redeemed
playback for each of the two accepted media-type values, with zero active,
outstanding, rejected, or expired leases. That closes
the downstream-transport cleanup gate without changing App or scanner ownership.

The revalidation also identified an operator-networking defect rather than a
media-source defect. Sanitized evidence records the unreachable origin as
`https://192.0.2.18` and the reachable HAOS listener as
`http://192.0.2.18:8123`; correcting `internal_url` and restarting Core allowed
the remote target to retrieve the Home Assistant-owned playback URL. No private
App port, capability, Ingress identifier, or scanner address was exposed to the
target.

The same HAOS run exposed that Home Assistant's restricted Ingress frame
suppresses browser-native confirmation dialogs, causing removal, rollback-image
discard, and bridge-key rotation to exit silently before reaching the lifecycle
API. The candidate dashboard replaces those dialogs with a visible two-step
in-page confirmation while retaining the exact SHA-256 requirement and clearing
the armed action whenever that identity changes. Focused lifecycle, dashboard,
JavaScript, and documentation validation pass. Physical Ingress revalidation
then used the two-step action to discard the retained `0.1.3` image before the
deliberate `0.1.5` update. The update retained exact `0.1.4` bytes as the new
rollback image, and no integration symlink or unrelated path was changed.

Final physical regression revalidated audible browser audio; daemon recording,
finalization, saved playback, and download; channel hold, navigation, and
release; live waterfall pause, resume, clear, and recovery; the complete
24-component MQTT Discovery device; and all nine verification cards. Restarting
only the Local App preserved the finalized recording, restored a live waterfall
with zero queue loss, overflows, or poll failures, and allowed another canonical
`audio/mpeg` playback to resolve, play, stop, and clean up normally. Final
diagnostics reported three issued and three redeemed playbacks with zero active,
outstanding, rejected, or expired leases.

Only one VLC-TELNET target was reachable; the second configured target remained
unavailable. The conditional two-target physical exercise therefore remained
environment-limited rather than silently simulated. Automated concurrent-lease
coverage remains authoritative, and this acceptance makes no claim of a
physical two-target synchronization or concurrency run. With that explicit
limit, the Milestone 29.2 implementation and physical acceptance gates are
closed and ready for pull-request review.

Closure cleanup then deleted the Home Assistant config entry, discarded both
managed integration copies, uninstalled the Local validation App, and removed
the three exact Milestone 29.2 share artifacts. The published `sds200` App
version 0.24.0 resumed sole scanner ownership before the one required Core
restart. Post-cleanup readback showed the published Ingress dashboard connected,
all nine verification cards rendering without configuration errors, and all 11
existing WAV recordings preserved. Supervisor retained only uninstalled local
source metadata—version absent and state unknown—not a running or installed
Local App instance.

Automatic or background RadioReference synchronization, silent conflict
resolution, MyRR scraping, undocumented interfaces, new Favorites mappings,
writable FTP, daemon/web/Home Assistant Favorites execution, another scanner
connection, public or anonymous live-audio URLs, arbitrary transcoding profiles,
whole-home synchronization claims, weather-alert recording, TUI waterfall
rendering, and GUI implementation remain deferred. The external implementation-
review message remains separate. Dependency-update pull requests 188 and 189
were later resolved together by the reviewed, fully validated replacement in
pull request 205.


## Deferred hardware validation

### SDS150 physical validation

Physical SDS150 validation is deferred until representative hardware is available.
It does not block v0.16.0.

When hardware becomes available:

- validate model detection and USB serial control;
- validate battery and charge reporting;
- validate navigation and PSI state;
- record tested firmware and transport evidence;
- document any limits that differ from modeled or fixture-tested behavior.

Until then, documentation must describe SDS150 support as implemented or
fixture-tested, not hardware-validated.

## Current and future milestone groups

These milestone groups preserve the current product sequence and intended future
work. Numbering and release assignment may change before an unstarted slice
begins.

### Milestone 20 — Web dashboard, themes, and Home Assistant

- Milestone 20.1 completed the optional FastAPI and Uvicorn service foundation,
  loopback-only `sdsctl web` command, versioned health, daemon-status,
  authoritative-snapshot, and OpenAPI routes, redacted daemon errors, package
  extras, documentation, and host-independent regression coverage.
- Milestone 20.2 completed the first accessible responsive read-only browser
  shell, packaged HTML, CSS, and JavaScript assets, two-second daemon-status
  polling, scanner and runtime summaries, restrictive browser response headers,
  light and dark presentation, compact layouts, reduced-motion behavior, and
  host-independent shell and static-asset tests.
- Milestone 20.3 completed same-origin Server-Sent Events over the existing
  ordered daemon event socket, authoritative snapshot-first delivery, browser
  incremental updates and reconnect behavior, polling fallback, periodic
  reconciliation, redacted failures, and event-client lifecycle tests.
- Milestone 20.4 completed explicit browser playback of daemon-owned PCMU with
  same-origin binary streaming, manual Play and Stop controls, AudioWorklet
  mu-law decoding, bounded buffering and resampling, queue-loss and RTP-loss
  telemetry, hidden-tab playback continuity, deterministic PCMU and SSE client
  cleanup, idle daemon event-client reaping, bounded web-server graceful
  shutdown, and physical SDS200 validation.
- Milestone 20.5 completed daemon-owned browser recording workflows over the
  existing decoded-PCM router, recording status/start/stop/inventory API
  operations, ordered `recording.state` events, a private `recordings.sock`
  finalized-file service, safe inventory-relative WAV playback and download,
  recording survival across browser and web-process disconnects, daemon-shutdown
  finalization, regression coverage, packaging validation, and physical SDS200
  validation.
- Milestone 20.6 completed capability-negotiated browser semantic scanner
  controls, bounded reconnect, stable redacted failures, authoritative
  reconciliation, self-hosted interactive API documentation, regression
  coverage, and physical SDS200 validation.
- Milestone 20.7 completed browser-local system-adaptive, LCARS-inspired,
  Matrix-inspired, First Responder, and Amateur Radio themes over one shared
  accessible dashboard, deterministic documentation captures, packaging
  coverage, and CodeQL hardening.
- Milestone 20.8 completed the native daemon MQTT publication substrate:
  strict optional configuration, optional Paho packaging, retained availability,
  canonical semantic state, non-retained semantic events, PSI suppression,
  worker-owned retry/backoff, and daemon lifecycle ownership.
- Milestone 20.9 completed opt-in semantic MQTT scanner commands through the
  daemon's existing control dispatcher, including bounded transport input,
  correlated responses, retained-command rejection, manual acknowledgement, and
  request-ID deduplication without unrestricted raw scanner keys.
- Milestone 20.10 completed read-only Home Assistant MQTT device Discovery over
  the generic daemon state contract, with ten entities, namespace-derived device
  identity, birth-triggered republication, and no Home Assistant-specific scanner
  owner or command path.
- Milestone 20.11 completed Home Assistant App packaging around the existing
  daemon and web dashboard, Supervisor MQTT service adaptation, Ingress path
  portability and peer enforcement, persistent recordings, fixed UDP 50000 RTP
  publication without host networking, multi-architecture image automation, and
  physical HAOS validation of scanner control, live audio, recording persistence,
  App restart, and all ten MQTT Discovery entities.
- The v0.20.1 corrective release hardened Milestone 20.11 by moving recordings
  into writable Home Assistant `/media`, safely migrating legacy
  `/data/recordings`, improving the dashboard layout, and validating
  repository-managed upgrade and reinstall behavior on physical HAOS.
- Milestone 20.12.1 completed Home Assistant configuration translations and
  post-v0.20.1 roadmap synchronization without changing runtime semantics.
  Repository-managed rendering was physically validated on HAOS in the v0.20.2
  acceptance run.
- Milestone 20.12.2 completed the first-party Lovelace SDS200 card, including
  safe `/local` delivery, Home Assistant's graphical card form, supported state
  subscription, deterministic package validation, and explicit isolation of
  optional card-installation failures. Resource delivery, manual registration,
  picker/editor behavior, and live read-only rendering were physically validated
  on HAOS in the v0.20.2 acceptance run.
- Milestone 20.12.3 completed the deliberate Home Assistant control adapter:
  four authoritative non-optimistic Hold switches plus Previous Channel, Next
  Channel, and Reconnect Scanner buttons over seven dedicated QoS 0 non-retained
  topics. The adapter generates fresh internal daemon request IDs, reuses the
  existing typed semantic controls and bounded current-channel resolver, clears
  navigation context on disconnect/resynchronization, keeps generic daemon MQTT
  commands disabled for the App, and preserves the daemon as sole scanner owner.
  All seven controls, generic-command isolation, and the single-owner boundary
  were physically validated on HAOS in the v0.20.2 acceptance run.
- v0.20.2 completed Milestone 20 release closure with reviewed wiki publication,
  PyPI publication, amd64/aarch64 Home Assistant image publication,
  repository-managed HAOS acceptance, and a normal GitHub Release.
- Milestone 26.1 owns the separate authenticated/TLS LAN web-access boundary.
  Keep a network transport for remote daemon-backed CLI/TUI/GUI clients and any
  optional host-network App variant as later security boundaries. The current
  daemon client interfaces remain private Unix-domain sockets, so host networking
  alone would not expose them remotely.
- The completed Milestone 26.2
  [capability and field-parity audit](docs/capability-field-parity-audit.md)
  records renderer omissions for already-modeled scanner fields, including
  Tone-Out, Weather, Close Call, and Search. Keep later renderer work tied to
  that evidence rather than inventing renderer-local scanner semantics.

### Milestone 21 — Favorites Workspace foundation

- Milestone 21.1 completed the SDS100/200 format foundation: immutable lossless
  positional source records, tolerant preservation of observed extensions and
  unknown commands, separate Conventional and Trunk hierarchy projections,
  `f_list.cfg` catalog projection, a pure exact-filename workspace binder,
  sanitized synthetic fixtures, and explicit isolation from storage and writes.
- Milestone 21.2 completed the first read-only storage boundary: immutable exact
  catalog/document byte snapshots, safe catalog filename validation, and an
  offline copied `favorites_lists` backend with deterministic immediate HPD
  discovery, stable regular-file reads, managed-symlink rejection, and no write,
  live-scanner, credential, FTP, USB, or renderer behavior.
- Milestone 21.3 completed renderer-neutral read-only hierarchy navigation:
  immutable source-index-addressed Favorites List, Conventional/Trunk system,
  department, trunk-site, and channel nodes with explicit parent/child structure,
  exact source names and typed provenance, deterministic catalog ordering, and
  reconstruction of mixed trunk children in raw HPD source order.
- Milestone 21.4 completed renderer-neutral Favorites search and filtering:
  immutable display-name substring queries, navigation-kind filtering, and
  inclusive subtree selection over `FavoritesNavigation`, with query-local
  case-folding, deterministic navigation preorder, original-node identity,
  explicit stale-path failure, and no raw-field search semantics.
- Milestone 21.5 completed renderer-neutral Favorites schema diagnostics:
  immutable workspace validation with stable error/warning/info rules, exact
  catalog/HPD and record/field provenance, required metadata and evidence-backed
  shape checks, supported name-tag validation, scanner-observed extension
  acceptance, and compatibility-preserving treatment of unvalidated fields and
  unsupported commands.
- Milestone 21.6 completed renderer-neutral Favorites comparison and preview:
  immutable exact baseline/candidate workspace comparison, deterministic
  add/remove/replace record changes, exact source provenance, filename-based HPD
  pairing, explicit duplicate-filename ambiguity, byte-aware line-ending
  comparison, and compatibility-preserving treatment of unknown source material.
- Milestone 21.7 completed renderer-neutral Favorites import and export:
  exact `FavoritesStorageSnapshot -> FavoritesWorkspace ->
  FavoritesStorageSnapshot` round trips, public pure reverse projection,
  preserved catalog and HPD source bytes, exact document ordering and duplicate
  filenames, and compatibility-preserving export of unknown commands, positional
  extensions, blank fields, physical line endings, empty files, and unresolved
  workspace diagnostics.
- The renderer-neutral Favorites Workspace foundation is complete through
  Milestone 21.7: lossless data modeling, read-only copied storage, hierarchy
  browsing, search/filtering, schema diagnostics, comparison/preview, and exact
  native snapshot import/export all preserve unknown scanner material.
- Keep fixtures and copied storage images as the normal automated-test substrate;
  physical scanner storage is not required for renderer-neutral planning work.

### Milestone 22 — Verified Favorites writes and storage backends

- Milestone 22.1 completed renderer-neutral immutable Favorites write planning:
  exact baseline and intended storage snapshots remain authoritative; existing
  comparison and schema evidence is retained; unsafe or ambiguous intended
  states produce deterministic blockers; exact snapshot inequality determines
  change state; and exact baseline equality provides the stale-target
  precondition for a future executor without performing any storage mutation.
- Milestone 22.2 completed pure renderer-neutral Favorites record editing:
  immutable exact source-provenance targets, stale and ambiguous target
  rejection, evidence-backed Name Tag replacement, conservative HPD leaf
  deletion, template-backed leaf creation, deterministic hierarchy-safe
  insertion, exact untouched-byte preservation, and intended-snapshot
  construction integrated with the existing write planner.
- Milestone 22.3 completed the first verified storage-mutating workflow against
  an offline copied Favorites tree: exact plan-bound target validation, an
  exclusive operation boundary, mandatory verified complete backup, full-tree
  staging, staged readback and exact intended comparison, a second
  concurrency/stale-baseline check before replacement, deterministic active-tree
  replacement, rollback recovery, and durable operation reporting.
- Milestone 22.4 completed read-only Linux USB mass-storage discovery and target
  qualification for already-mounted scanner storage: immutable mountinfo and
  sysfs evidence, proven USB ancestry, canonical contained Favorites targets,
  explicit read-only/writable state, ambiguity and stale/remount rejection,
  copied-tree read validation, and revalidated explicit-path qualification
  without mounting or mutating media.
- Milestone 22.5 completed the first verified USB mass-storage write executor:
  plan-bound fresh target qualification, host-side exclusive operation state,
  complete verified backup and staging away from scanner media, second exact
  mount/device/tree stale checks before activation, removable-media-specific
  activation without copied-tree sibling assumptions, exact active readback,
  rollback recovery, durable reporting, deterministic failure coverage, and
  guarded reversible physical SDS200 validation.
- Milestone 22.6 completed the first read-only FTP Favorites storage foundation:
  explicit trusted-network configuration, read-only credential references with
  no writable fallback, bounded exact binary retrieval, deterministic safe
  listings, two-pass exact snapshot stability, secret-redacted failures, a
  fakeable transport boundary, and guarded read-only SDS200 validation.
- Keep FTP access limited to trusted local networks or VPNs and preserve
  separate read-only and explicitly resolved writable credential roles.
- Keep every write operation deterministic, recoverable, auditable, and free of
  silent last-writer-wins behavior.
- Milestone 26.3 completed the first local interactive Favorites Workspace editor
  over the existing renderer-neutral foundation. It exposes hierarchy
  browsing, search/filtering, diagnostics, comparison/preview, only the existing
  evidence-backed rename, supported leaf-delete, and exact-template leaf-create
  operations, exact write planning, a separate explicit confirmation, and
  backup/rollback/recovery evidence without bypassing the verified copied-tree
  and USB executors. Arbitrary positional-field editing; structural hierarchy or
  catalog creation, deletion, or reordering beyond the supported record
  operations; FTP writes; RadioReference synchronization UI; live-scanner
  GLT/FQK mutation, web/Ingress exposure, and background synchronization remain
  separate evidence-backed capabilities. USB backup, staging, rollback, and
  reporting state remains in a canonical private host-state directory
  outside scanner media. Supported Name Tag replacement may target existing
  catalog and hierarchy records without authorizing structural mutation.

### Milestone 23 — External Favorites data and synchronization

- Milestone 23.1 completed the renderer-neutral external-data foundation:
  immutable provider/dataset/record identities, timezone-aware observation
  evidence, explicit value-versus-absence semantics, external/local/detached
  field ownership, deterministic add/replace/remove/unchanged/local-only/conflict
  previews, redacted fakeable source reads, explicit detach behavior, and exact
  local record provenance without provider-specific scanner mapping.
- Milestone 23.2 completed the first RadioReference-specific documented-interface
  boundary: secret-reference configuration, user/application credential
  separation, fakeable provider sessions, stable redacted failures,
  traceback-secret hardening, normalized observation validation, and documented
  SOAP/WSDL/licensing research without a production network adapter.
- Milestone 23.3 completed the documented WSDL/provider-record contract boundary:
  direct public WSDL inspection, exact metadata for 19 programming-relevant
  RPC/encoded operations, immutable provider-side conventional/trunked DTOs,
  schema-faithful scalar and array modeling, provider-record runtime hardening,
  and offline validation without a SOAP parser or production network adapter.
- Milestone 23.4 completed the dependency-free offline SOAP response decoder:
  bounded SOAP 1.1 RPC/encoded XML parsing, strict operation/response-type and
  QName validation, schema-faithful scalar decoding, SOAP-ENC arrays and named
  array containers, bounded local reference graphs, stable redacted failures,
  and fully offline regression coverage without live provider access.
- Milestone 23.5 completed the offline SOAP request serializer foundation: exact reviewed
  `authInfo` metadata, dependency-free deterministic SOAP 1.1 RPC/encoded request
  serialization for all 19 reviewed programming operations, exact request-side scalar
  validation, ephemeral secret-bearing request bytes, and fully offline coverage without
  HTTP/TLS transport or live provider access.
- Milestone 23.6 completed the offline observation mapping foundation:
  reviewed conventional-frequency and talkgroup DTOs map into immutable
  source-neutral observations with stable provider record identities, exact
  alpha-tag preservation, whole-Hz frequency conversion, and no provider
  revision, hierarchy, deletion, or scanner-record inference.
- Milestone 23.7 completed the offline SOAP result observation adapter:
  operation-aware mapping for three reviewed conventional-frequency operations
  and `GET_TRUNKED_TALKGROUPS`, exact immutable result validation, supported
  empty-result semantics, duplicate provider-identity rejection, provider-order
  preservation, and decoder-to-observation integration without transport access.
- Milestone 23.8 completed the offline request-plan/session composition
  foundation: immutable secret-free reviewed request plans, fakeable
  operation-aware byte exchange, serializer/decoder/observation composition,
  timezone-aware observation evidence, stable redacted failures, deterministic
  cleanup, and integration through `RadioReferenceSource` without production
  HTTP/TLS transport or retained request/response bytes.
- Milestone 23.9 completed the assisted-import provenance binding foundation:
  explicit normalized field-name to exact local source-field-index bindings,
  external/local ownership, exact-value proof before external ownership,
  source-neutral preview integration, explicit detach continuity, and a real
  RadioReference mapper regression that leaves unbound whole-Hz frequency data
  as provider-only evidence without inferring scanner representation.
- Milestone 23.10 completed the assisted-update name acceptance planning
  foundation: one pure name-only acceptance planner over existing linked
  provenance, schema-aware rename authority, ordinary write planning,
  simultaneous bound-field change rejection, refreshed intended in-memory
  provenance, and no implicit provider-frequency mapping.
- Milestone 23.11 completed name acceptance execution composition: injected
  storage-specific write execution, exact independent post-write snapshot
  readback, provenance promotion only after intended-snapshot equality, opaque
  backend success evidence, and fail-closed completion without duplicating
  copied-tree or USB safety/recovery machinery.
- Milestone 23.12 completed canonical external-provenance serialization and
  fresh-snapshot rebinding: source-neutral linked/detached state, exact local
  target locators with source-record SHA-256 evidence, strict bounded JSON,
  stable redacted errors, and fail-closed moved/changed/ambiguous target
  rejection without choosing a filesystem owner.
- Milestone 23.13 completed provenance filesystem durability and explicit
  loading: one deterministic XDG-backed state path, private current-user-owned
  publication, advisory coordination, exclusive no-follow temporary files,
  exact readback and synchronization, guarded atomic replacement, and explicit
  fresh-snapshot loading with absent-versus-empty semantics.
- Milestone 23.14 completed durable name-acceptance provenance: exact retained
  baseline, observation, and intended-state proof, complete persisted-provenance
  preflight, expected-current conditional publication, exact post-write
  verification, and a distinct failure when verified Favorites mutation is not
  followed by completed provenance publication.
- Milestone 23.15 completed startup provenance restoration lifecycle ownership:
  exactly one fresh Favorites snapshot, exact durable provenance loading and
  rebinding with absent `None` versus present-empty `()` preserved, idempotent
  active start, terminal failed/closed behavior, and redacted failures without
  partial restoration evidence.
- Milestone 23.16 completed assisted-refresh preview session composition:
  exactly one lifecycle snapshot and one source read per explicit refresh,
  immutable exact lifecycle, observation, and preview evidence, preserved
  absent-versus-empty provenance, fresh retryable uncached attempts, and no
  Favorites or provenance mutation.
- Milestone 23.17 completed assisted-refresh name-acceptance planning
  composition: exact selected preview, matched observation and linked lifecycle
  provenance evidence, delegated name-only acceptance planning, strict
  relationship validation, and no execution, publication, or lifecycle mutation.
- Milestone 23.18 completed post-acceptance lifecycle advancement: active
  lifecycle evidence adopts one already-verified durable name acceptance under
  the lifecycle lock with exact path, Favorites baseline, complete provenance
  baseline, and idempotent same-result validation.
- Milestone 23.19 completed assisted-refresh name-acceptance orchestration: one
  lifecycle critical section validates the selected refresh baseline, requires
  exact complete persisted provenance, executes the existing durable acceptance,
  conditionally publishes provenance, and advances lifecycle evidence.
- Milestone 23.20 completed assisted-refresh detach planning: explicit field or
  whole-record detach decisions reuse existing ownership transformations, retain
  exact refresh/baseline evidence, preserve local Favorites bytes, and reject
  unbound, ambiguous, local, already-detached, or no-op selections.
- Milestone 23.21 completed assisted-refresh detach orchestration: exact detach
  plans flow through complete persisted-provenance validation, conditional
  provenance publication, and lifecycle adoption under one critical section
  without changing Favorites bytes or rereading the provider.
- Milestone 23.22 completed the first RadioReference-to-Favorites representability
  seam: immutable source-neutral field mapping evidence plus one reviewed
  conventional `C-Freq` whole-Hz frequency mapping with exact target, provider
  identity, source-field index, and scanner-compatible value validation.
- Milestone 23.23 completed source-neutral arbitrary-field acceptance planning:
  exact mapping consumption, externally owned or previously unbound selected
  fields, collision/conflict rejection, exact positional replacement or no-op
  write planning, and intended in-memory provenance without execution.
- Milestone 23.24 completed arbitrary-field acceptance execution composition:
  exact mapped-field plans flow through injected existing write executors,
  independent exact readback, opaque backend evidence retention, and fail-closed
  durability without another editor or storage backend.
- Milestone 23.25 completed the renderer-neutral assisted-synchronization
  foundation across checkpoints A–F: durable arbitrary-field acceptance and
  lifecycle orchestration; explicit template-and-binding-backed record import;
  explicit provider-removal delete versus keep-local/detach planning; exact
  provenance rebasing after structural changes; the production documented
  RadioReference stdlib HTTPS SOAP exchange and reviewed request/session/source
  chain; reviewed conventional and talkgroup observation mappings; and one
  synchronous renderer-neutral application service exposing typed refresh,
  plan, and execute choices.
- Milestone 23 is renderer-neutral foundation complete. Supported
  synchronization is user-initiated and assisted only—never automatic,
  scheduled, polling, or background behavior. Only exact reviewed mappings are
  supported: conventional `C-Freq` name and whole-Hz frequency, plus `TGID`
  name and decimal talkgroup ID. Structural import requires an explicit scanner
  template and field bindings, and provider removal requires an explicit delete
  or keep-local/detach decision.
- The production RadioReference exchange is implemented and tested with
  deterministic offline fixtures and fake HTTPS connections. No live
  credentialed RadioReference validation or live scanner-hardware validation
  was performed for this milestone.
- Keep credentials and application keys outside exported Favorites data. Keep
  MyRR, scraping, undocumented/private interfaces, broader mappings and scanner
  transformations, renderer wiring, and automatic synchronization out of this
  foundation.

### Milestone 24 — Advanced protocol and analysis modes

Research and fixture work must precede public support for:

- Favorites and hierarchy retrieval such as `GLT`;
- Favorites quick keys such as `FQK`;
- Quick Search control such as `QSH`;
- scanner recording control such as `URC`;
- analysis controls such as `AST` and `APR`;
- waterfall data such as `PWF` and `GWF`;
- menu operations such as `MNU`, `MSI`, `MSV`, and `MSB`;
- a guarded SDS200 reboot-recovery operation based on the reported `MSM,1`
  behavior, which places the scanner in mass-storage mode briefly before reboot;
  require protocol and firmware validation, explicit operator intent, bounded
  outage handling, and post-reboot control, PSI, and RTSP recovery checks before
  exposing it, and do not treat it as ordinary reconnect;
- additional NAC, RAN, color-code, area, activity, and quality details;
- conventional and trunking discovery modes;
- system-status and RF-power plot screens.

Each feature must preserve unknown fields and include captured or synthetic
fixtures before renderer-specific implementation.

Tentative evidence-led slicing is:

- Milestone 24.1: protocol inventory, evidence ledger, and fixture/provenance
  foundation;
- Milestone 24.2: exact `GLT,FL` retrieval and generalized bounded-XML framing;
- Milestone 24.3: completed `FQK` read/control; `QSH` remains deferred pending
  evidence for its exact `FRQ` representation;
- Milestone 24.4: ordered/repeated `ScannerInfo` preservation foundation before
  any evidence-backed richer NAC, RAN, color-code, area, activity, or quality
  modeling;
- Milestone 24.5: `URC` scanner recording control;
- Milestone 24.6: `AST`/`APR` analysis-session foundation;
- Milestone 24.7: `PWF`/`GWF`/`GW2` waterfall session and data work;
- Milestone 24.8: `MNU`/`MSI`/`MSV`/`MSB` menu operations; and
- later discovery, system-status, and RF-power work built on the analysis
  substrate.

Milestone 24.8 began with a receive-only `MSI` bounded-XML model/parser
foundation preserving the exact reviewed `<MSI ...>` root, root attributes,
ordered/repeated descendants, unknown attributes and elements, and raw XML. The
second slice added exact `MSI` retrieval through typed command/radio APIs for
CR-line serial and deterministic replay. The third added only the six indexed
`MNU` rows whose index roles are explicit in both reviewed SDS specifications,
and the fourth added a read-only documented MSI menu projection without
discarding lossless source evidence. The fifth slice promotes exact one-shot
`MSI` retrieval to the shared SDS200 UDP bounded-XML expectation, fragment
reassembly, and retry machinery with deterministic fake datagrams. Fallback
MSI remains blocked, and no `MSV`/`MSB`, unindexed `MNU`, menu lifecycle,
model/firmware applicability, or physical-scanner behavior is inferred.

Milestone 24.1 selected `GLT` as the safest leading implementation candidate
because it is bounded retrieval that complements the Favorites foundation and
requires generalized bounded-XML framing before more stateful work. Milestone
24.2 now implements only the narrow, evidence-backed `GLT,FL` slice. Broader
GLT arguments and hierarchy forms remain deferred until evidence supports their
exact request and response semantics. The reported `MSM,1` reboot behavior
remains separately gated pending stronger independent protocol and firmware
evidence; the officially documented disruptive `POF` operation is adjacent
evidence, not a substitute. Later slice numbering may move as physical evidence
is collected.

### Milestone 25 — Portability, containers, and additional interfaces

- Continue prioritizing Linux and Raspberry Pi operation.
- Add a supported container image and Docker Compose examples for daemon,
  daemon-client, and web-dashboard workflows.
- Make network-connected SDS100, SDS150, and SDS200 operation the primary
  container deployment path.
- Document scanner host addressing, configuration, state, cache, recording, log,
  destination-manifest, and private-socket mounts; health checks; restart policy;
  signal handling; and orderly container shutdown.
- Document Linux USB serial passthrough through stable
  `/dev/serial/by-id/...` paths, narrowly scoped device access, and required host
  group permissions without recommending broadly privileged containers.
- Document host networking versus explicit socket or port exposure and identify
  platform-specific Docker limitations.
- Add host-independent container integration tests, followed by separate physical
  network and USB validation.
- Preserve native systemd deployment as the preferred production option when
  direct host-device, local-audio, or operating-system integration is important.
- Validate Windows and macOS behavior without blocking current releases.
- Consider a future desktop GUI over the same renderer-neutral services.
- Treat the Raspberry Pi 7-inch 800 by 480 display as a compact reference layout,
  not a universal fixed resolution.

### Milestone 26 — Authenticated access and post-v0.21 interface work

- Milestone 26.1 completed explicit authenticated direct-TLS LAN access for the
  existing web dashboard while keeping loopback-only operation as the default.
- The daemon remains the sole scanner owner, and daemon API, event, PCMU, and
  recording services retain their private Unix-domain socket boundary.
- The explicit non-loopback listener authenticates scanner state and controls,
  ordered events, browser audio, recording operations, saved recordings, and
  downloads while keeping credentials out of logs, configuration, URLs, and
  browser-visible errors.
- Native TLS, one exact HTTPS origin, bounded server-side sessions, login
  throttling, origin enforcement, and session-scoped stream revocation establish
  the supported security boundary. Trusted reverse-proxy deployment remains a
  separate future design.
- Home Assistant Ingress retains its established authenticated peer boundary and
  does not inherit the standalone dashboard login flow.
- Host-independent coverage completed browser-session, authorization, hostile
  request, origin/header, SSE, audio, recording, semantic-control, concurrency,
  shutdown, and unchanged-loopback validation.
- Bounded physical SDS200 LAN validation completed with two authenticated
  concurrent sessions while the daemon retained one scanner-control and one
  network-audio ownership path.
- Keep remote daemon-backed CLI/TUI transport, Internet-facing deployment,
  identity-provider integration, and broader authorization roles as separate
  future security boundaries unless later evidence deliberately expands this
  milestone.
- Milestone 26.2 completed the evidence-backed physical-scanner capability and
  interface field-parity audit. Its exact shared-state matrix and categorized
  findings are maintained in
  [the audit artifact](docs/capability-field-parity-audit.md).
- Milestone 26.3 completed the local interactive Favorites Workspace editor
  constrained to the already verified Milestones 21–22 model, planner,
  copied-tree executor, and qualified USB executor boundaries.
- Milestone 26.4 completed the browser-renderer parity slice. It presents
  already-modeled shared hierarchy, RF, service, identifier, receiver-level,
  mute, scanner-recording, P25, and special-mode fields without changing scanner
  protocols, shared-state lifecycle, semantic controls, daemon schemas, Home
  Assistant, authentication, or deployment boundaries.
- Milestone 26.5 completed the battery and System Status lifecycle decision.
  Optional finite SDS100 battery telemetry now follows the authoritative PSI/GSI
  shared-state lifecycle, while SDS150 GCS remains explicit request/response
  telemetry and System Status remains a future explicitly owned analysis-session
  service pending timestamps, staleness, cancellation, reconnect, and physical
  acceptance evidence.
- Milestone 26.6 completed the compatibility-reviewed Home Assistant expansion.
  Four fixed read-only Site, Frequency, Modulation, and Service Type sensors and
  matching optional card fields reuse the canonical radio-state topic, preserve
  every existing identity and control, and fail unavailable when their current
  mode-dependent values are absent.
- Milestone 26.7 completed one additive responsive SDS200 Display Lovelace card
  with five selectable scanner-style layouts, three palettes, Card and bounded
  viewport fit, strict configuration validation, and the same fourteen read-only
  entities. Physical development acceptance on Home Assistant OS exercised all
  thirty layout/palette/fit combinations, the three reference viewports, live
  state, and restart persistence against SDS200 firmware 1.26.01. The existing
  compact card and all Home Assistant component and scanner-ownership boundaries
  remain unchanged.
- Milestone 26.8 completed exact desired-state hold/release and exact bounded
  volume/squelch parity across direct and daemon-backed CLI/TUI paths. SDS200
  firmware 1.26.01 physical acceptance covered all four hold scopes and
  reversible native-UDP volume and squelch changes with authoritative
  completion and restored starting state.
- Milestone 26.9 completed two fixed read-only Home Assistant sensors for
  configured Tone-Out Tone A and Tone B values plus additive compact and SDS200
  Display card rendering. Physical Home Assistant OS acceptance covered zero
  detection presentation, programmed nonzero values, correct optional-field
  availability, App restart persistence, and the unchanged single-owner
  boundary against SDS200 firmware 1.26.01.
- Milestone 26.10 completed the first interface-scoped modular theme boundary.
  Five built-in web themes now use independently packaged versioned manifests
  and stylesheets, one validated immutable registry, registry-derived picker and
  pre-paint metadata, same-origin delivery, unchanged accessible responsive
  rendering, and verified wheel/source-distribution inclusion.
- Milestone 26.11 completed the built-in Home Assistant packaging boundary. The
  compact SDS200 Scanner and SDS200 Display modules now use independent
  versioned packages and one validated immutable registry while retaining
  byte-identical JavaScript, public custom elements, flat installed filenames,
  `/local/sds200/` URLs, manual registration, and App-startup failure isolation.
- Milestone 26.12 completed the built-in terminal packaging boundary. The exact
  dark and light semantic palettes and theme-only Textual CSS now live in
  independently validated `themes/tui/<theme-name>/` packages while preserving
  compatibility objects, Rich CLI selection, Textual toggling, responsive
  layout, and deterministic installed-wheel resource loading.
- Milestone 26.13 completed the managed third-party theme lifecycle. The XDG
  interface-scoped hierarchy now has automatic inventory, schema reuse,
  malformed-entry isolation, guarded install and replacement, exact confirmed
  removal, private atomic staging, rollback and recovery, concurrent mutation
  exclusion, and explicit trust acknowledgement for Home Assistant JavaScript.
- Milestone 26.14 completed automatic managed web-theme activation. Valid
  managed CSS packages join the existing browser picker and pre-paint bootstrap,
  remain disabled until selected, and are served only through a same-origin,
  startup-digest-pinned route that fails closed after package mutation or
  substitution.
- Milestone 26.15 completed automatic managed terminal-theme activation. Valid
  managed semantic palettes and presentation-only TCSS can be selected through
  command-line, environment, or configuration inputs while malformed packages
  remain isolated and the exact built-in dark presentation remains the runtime
  fallback.
- Milestone 26.16 completed explicit managed Home Assistant theme activation.
  Exact operator-approved package and module digests, secure descriptor-relative
  reads, atomic target deployment, and a private activation ledger provide
  guarded activation, reapproval, status, deactivation, and active-package
  removal refusal without executing JavaScript or editing Home Assistant state.
- Milestone 26.17 closes and publishes the accumulated v0.22.0 release after
  complete repository validation, tag-gated Python and multi-architecture image
  publication, public artifact verification, clean-install validation, and
  repository-managed Home Assistant OS acceptance.

### Milestone 27 — Adaptive scanner screens, hardening, and waterfall workspace

- Milestone 27.1: evidence-backed screen-profile parity and opt-in automatic
  renderer selection across web, Home Assistant, and terminal interfaces.
  Physical development acceptance on Home Assistant OS exercised every
  classified screen transition, automatic web and card presentation, restart,
  browser-audio and recording regressions, all twenty-four entities, configured
  Tone-Out values, zero-tone detection presentation, and the unchanged
  single-owner boundary against SDS200 firmware 1.26.01.
- Milestone 27.2: physical SDS200 firmware 1.26.01 waterfall qualification and
  one daemon-owned bounded `PWF`/recurring-`GWF` session with private local
  fanout, transient-poll tolerance, reconnect/restart recovery, deterministic
  cleanup, and normal-scanner/Home Assistant ownership restoration.
- Milestone 27.2.1: independently reproduced network and protocol hardening for
  bounded RTSP and fragmented XML, isolated UDP callbacks, strict redacted STS
  structure, and explicit Broadcastify cleartext-credential acknowledgement.
- Milestone 27.2.2: audio lifecycle and release-integrity hardening, including
  nonblocking per-sink ownership, safe single-owner WAV finalization, current
  version documentation, reviewed workflow and image pins, and a measured
  non-regressive coverage floor.
- Milestone 27.3 completed one responsive viewport-owned web workspace, with the
  stable system-adaptive `system` default and fallback theme redesigned around
  the existing scanner-display hierarchy and a modular original Pip-Boy-inspired
  built-in web theme. Every built-in theme uses the shared accessible Scanner,
  Controls, Audio, Recordings, and Diagnostics panes without document or active-
  pane scrolling at 390x844, 800x480, 1366x768, and 1920x1080 reference sizes.
  Physical development acceptance completed on August 26–27, 2026, through Home
  Assistant Ingress against an SDS200 running firmware 1.26.01. The initial
  isolated Local App used exact merged commit `db2e6c0`; a restart exposed a
  terminal native EventSource retry that left authoritative two-second polling
  active but required a page reload before ordered events resumed. Exact closure
  commit `dca445e` added one tracked duplicate-free two-second stream-recreation
  timer. Source-built Apps exercised all six built-in themes, all five panes,
  both scan fallbacks, Search, Close Call, Weather, and configured and detecting
  Tone-Out presentations, all five field-inspection choices, semantic scanner
  controls, browser audio, daemon recording, pagination, saved playback and
  download, reload persistence, and restart. The same Ingress document recovered
  without reload after a deliberate stopped-App interval longer than ten seconds
  and again after a normal restart. Final cleanup left all four holds Off,
  normal scanning active, both isolated Local Apps installed but stopped, and
  the repository-managed App as the sole daemon, scanner-control, PSI, and
  RTSP/RTP owner.
- Milestone 27.3.1: bounded managed-theme source-snapshot hardening with one
  retained source-directory identity, descriptor-relative no-follow reads, one
  aggregate package byte budget, exact staged-byte hashing, private-stage-only
  parsing and validation, adversarial mutation coverage, unchanged interface
  semantics and Home Assistant executable-code trust gates, and a durable
  implementation-review disposition ledger.
- Milestone 27.4: responsive theme-aware web spectrum and rolling-waterfall
  workspace inside the shared viewport shell, with authenticated bounded demand,
  same-origin direct and Home Assistant Ingress streaming, exact hexadecimal
  240-value validation, immediate CSS/Canvas recoloring, relative and
  uncalibrated labeling, lifecycle controls, loss telemetry, deterministic
  cleanup, and physical branch-image acceptance.
- Milestone 27.5: v0.23.0 release closure for the complete adaptive-screen,
  hardening, responsive-workspace, and authenticated-waterfall sequence, including
  final documentation imagery, public artifact verification, and published Home
  Assistant App acceptance.

### Milestone 28 — Favorites Workspace and assisted RadioReference product integration

#### Milestone 28 complete — v0.24.0 release candidate record

Milestones 28.1 through 28.4 are closed at the pre-tag boundary. They completed
an explicit credentialed RadioReference refresh, exact read-only
preview, reviewed field and record decisions, deterministic assisted write
planning, separately confirmed guarded execution, and release publication.
Private operator acceptance completed on August 29, 2026. A credentialed
`getTrsTalkgroups` refresh against public RadioReference system `12042` proved
the bounded preview, a copied-tree import proved the complete guarded execution
path, and a controlled injected post-mutation failure proved exact reverse
recovery with fail-closed cross-store reconciliation.

Physical acceptance used an SDS100 running firmware 1.26.01 in USB Mass Storage
mode on x86-64 Ubuntu 26.04.1 LTS with Python 3.14.4 and Docker 29.7.2. One
reviewed minimal insertion and its separately reviewed inverse proved the
forward/inverse digest chain closed, restored active provenance through canonical empty
to its original absent state, synchronized the filesystem, and safely unmounted
the removable volume. The preserved 118-field `F-List` record retained its
unvalidated-extra-fields diagnostic while exact comparison proved the catalog
bytes unchanged. This does not claim separate physical SDS200 USB acceptance.

All credentials, provider payloads, local programming values, confirmation
tokens, provenance content, private paths, and private-derived hashes remained
outside the repository and public documentation. Release surfaces were
synchronized for 0.24.0 and retained the shared 86 percent coverage floor. Only
one genuine matching `v0.24.0` tag may publish. Workflow success alone is not
release acceptance. Milestone 29.1 activated only after that
public release closure. The external review message remains separate, and
waiting dependency-update pull requests remain separate unless their reviewed
state changes.

- Milestone 28.1: explicit user-initiated RadioReference refresh and read-only
  preview inside the local Favorites editor. Reuse the existing production HTTPS
  source and renderer-neutral synchronization service; show observation time,
  provenance, additions, changes, removals, unchanged records, conflicts, and
  unmapped evidence without mutating Favorites or publishing provenance.
- Milestone 28.2: exact assisted decisions and synchronization planning for the
  reviewed conventional Name/frequency and talkgroup Name/decimal mappings,
  explicit local/external choices, field or record detach, template-and-binding-
  backed import, provider-removal delete versus keep-local decisions, and an
  invalidation-safe exact write plan without execution.
- Milestone 28.3: verified copied-tree and then guarded USB execution through the
  existing backup, staging, stale-target, readback, rollback, durable-report, and
  conditional-provenance machinery. No execution may silently reread the provider
  or proceed from refresh evidence different from the reviewed plan.
- Milestone 28.4: v0.24.0 release closure after credentialed provider, copied-
  tree, recovery, and reversible physical SDS100/200 USB acceptance.
- Synchronization remains explicit, user-initiated, assisted, and conflict-aware;
  never scheduled, polling, background, or silent last-writer-wins behavior.
  Only reviewed provider interfaces and exact representable mappings are
  supported. MyRR scraping and undocumented/private endpoints remain excluded.
- Real local scanner programming may be used as private uncommitted validation
  evidence. Committed regression fixtures must remain synthetic or sanitized and
  must not expose local names, frequencies, talkgroup or unit identifiers, or
  location data.

### Milestone 29 — Home Assistant waterfall and later interface expansion

- Milestone 29.1: a responsive Home Assistant waterfall card and authenticated
  App transport over the existing single-owner daemon service. Define independent
  visibility demand, final-card cleanup, bounded Canvas performance, Ingress
  authentication, card-editor configuration, multi-card behavior, and HAOS
  acceptance without high-rate MQTT entities or another scanner connection.
- Milestone 29.2: a first-party Home Assistant media source for live scanner
  audio through one Core-side bridge and one private App-side service. Preserve
  daemon RTSP/RTP ownership, use bounded short-lived playback leases, advertise
  only evidence-backed formats, and complete explicit packaging, security,
  compatibility, and HAOS acceptance.
- Milestone 29.3: v0.25.0 release and publication closure for the responsive
  Home Assistant waterfall, media-source live scanner audio, palette and
  lifecycle improvements, and reviewed Docker action maintenance. Freeze the
  feature boundary, synchronize release surfaces, validate every distribution
  and image contract, publish only from one genuine matching tag, verify the
  public artifacts, and complete repository-managed Home Assistant acceptance.
- Milestone 29.4: one digest-qualified aggregate Home Assistant card resource
  that imports the independently packaged compact, display, and waterfall
  modules while preserving their individual compatibility URLs.
- Milestone 29.5: phase-stable text-GWF scheduling, bounded command and scheduler
  telemetry, and low-rate typed GST refresh so every renderer follows the
  scanner's current Waterfall span. Qualify the resulting physical SDS200 frame
  rate before separately researching binary GW2 framing, transport, cadence,
  and renderer value.
- Milestone 29.6: bounded exact-byte binary-GW2 research and one guarded SDS200
  firmware 1.26.01 LAN probe. Preserve the qualified text `PWF`/`GWF` data plane
  after exact `GW2,1,ON` returns `ERR\r` and establishes no binary framing or
  material renderer benefit.
- Milestone 29.7: v0.26.0 release and publication closure for the aggregate Home
  Assistant card loader, phase-stable Waterfall cadence, live scanner-span
  tracking, compatibility fixes, and bounded GW2 research conclusion. Freeze
  the runtime boundary, synchronize release surfaces, validate every artifact,
  publish only from one genuine matching tag, and complete a clean published
  Home Assistant upgrade acceptance.
- Milestone 30.1: concise project landing page, deployment- and task-oriented
  beginner wiki source, an exact checked `sds200[all]` runtime-extra union, and
  documentation contracts for README length, declared extras, and audited
  destinations without changing runtime behavior.
- Milestone 30.2: v0.26.1 release and publication closure that makes the
  reviewed installation experience and `all` extra genuinely available from
  public PyPI, synchronizes release artifacts, validates clean installs, and
  completes proportionate published Home Assistant acceptance.

### Milestone 31 — Waterfall time and frequency inspection

- Milestone 31.1: bounded duration-based history and an optional display-only
  frequency pointer for the web dashboard and first-party Home Assistant
  Waterfall card. Preserve legacy frame-depth configurations, the relative and
  uncalibrated data contract, one shared scanner-side session, and explicit
  separation from scanner tuning or unsupported RF interpretation.
- Weather-alert state and recording, TUI waterfall rendering, and the future GUI
  remain separately bounded follow-up candidates.

## Completed milestone groups

- Milestones 1–10: typed core protocol, transports, discovery, profiles,
  reliability, documentation, packaging, and static quality gates.
- Milestones 11–14: SDS200 RTSP/RTP audio, native PCMU decoding, WAV recording,
  reusable recording sessions, and Textual audio controls.
- Milestone 15: deterministic TUI lifecycle hardening, operational logging,
  automatic stale-PSI recovery, Raspberry Pi fault injection, and v0.15.0.
- Milestone 16.1: decoded-PCM fanout, optional local playback, simultaneous
  playback and recording, sink reliability counters, roadmap enforcement, and
  SDS200 hardware validation.
- Milestone 16.2: immediate TUI live playback, repeatable recordings, a
  newest-first recording library, saved-recording playback, one shared RTSP/RTP
  stream, deferred PortAudio startup, warm mute and resume behavior, and SDS200
  hardware validation.
- Milestone 16.3: service-neutral remote PCM destinations, a
  Broadcastify-compatible Icecast source adapter, an Asterisk Music-on-Hold
  bridge, deterministic reconnect and shutdown validation, physical SDS200
  testing, and assigned production-feed authorization and routing validation.
- Milestone 16.4: renderer-neutral recording metadata, atomic JSON sidecars, and
  optional TUI lifecycle integration.
- Milestone 16.5.0: a bounded operational log panel, preserved file logging,
  descriptive panel titles, wide-layout corrections, and shutdown-safe polling.
- Milestone 16.5.1: renderer-neutral scanner-screen classification, preserved raw
  `Mode` and `V_Screen` values, an unknown-screen fallback, and synthetic GSI/PSI
  fixture and transition coverage.
- Milestone 16.5.2: mode-aware Quick Search and Close Call TUI panels with
  frequency or hit details, modulation, hold state, signal, RSSI, and detected
  `SAD` tone or digital-code reporting.
- Milestone 16.5.3: mode-aware Weather panels with channel number, frequency,
  modulation, monitor or alert state, hold, signal, RSSI, and scanner-reported
  SAME selection.
- Milestone 16.5.4: mode-aware Tone Out panels with profile and channel number,
  monitored frequency, modulation, Tone A and Tone B values, hold state, signal,
  and RSSI.
- Milestone 16.5.5: physical SDS200 firmware 1.26.01 validation of normal,
  Quick Search, Close Call, Weather, and Tone Out GSI/PSI states and live
  transitions, with observed protocol differences and unvalidated limits
  documented.
- Milestone 16.6: v0.16.0 release preparation, full CI and CodeQL validation,
  GitHub and PyPI publication, and clean Python 3.14 installation verification.
- Milestone 16.7: Linux PortAudio runtime diagnostics, local host-API and
  output-device inspection, Raspberry Pi default and explicit-device playback
  validation, v0.16.1 GitHub and PyPI publication, and clean Python 3.14 PyPI
  installation verification.
- Milestones 17.1–17.4: renderer-neutral recording identities, configurable
  recording organization, recursive inventory and artifact classification,
  deterministic retention planning, explicit inventory-bound execution, local CLI
  preview and confirmation workflows, and stale-state and filesystem safety
  validation.
- Milestone 17.5: v0.17.0 release preparation, full CI and CodeQL validation,
  trusted PyPI publication, GitHub release publication, and clean Python 3.14
  installation verification from public PyPI.
- Milestone 18.1: renderer-neutral remote-destination health classification,
  serializable operational snapshots, ordered lifecycle transition events,
  timezone-aware timestamps, listener isolation, and shutdown-safe concurrency.
- Milestone 18.2: immutable saved Broadcastify destination profiles, dedicated
  versioned TOML persistence, environment-variable secret references, strict
  schema validation, deterministic atomic writes, and validated adapter conversion.
- Milestone 18.3: renderer-neutral live stream metadata, deterministic bounded
  titles, newest-value worker publication, duplicate suppression, rate limiting,
  retry and redaction metrics, and a Broadcastify-compatible Icecast metadata
  adapter isolated from PCM delivery.
- Milestone 18.4: public renderer-neutral encoder process contracts, immutable
  command and lifecycle configuration, reusable pipe-backed subprocess ownership,
  bounded interruption and terminate/kill finalization, continuously drained
  diagnostics, and Broadcastify migration without changing its fixed FFmpeg MP3
  profile or Icecast transport behavior.
- Milestone 18.5: renderer-neutral buffered local playback, preserved PortAudio
  compatibility, explicit PipeWire, PulseAudio, and ALSA command adapters,
  reusable dynamic PCM subscriber routing, immutable subscriber health snapshots
  and ordered transitions, isolated lifecycle failures, redacted diagnostics, and
  preserved separation from RTP reception and scanner control.
- Milestone 18.6: v0.18.0 release preparation, full static, test,
  documentation, distribution, clean-install, CI, and CodeQL validation, trusted
  PyPI publication, GitHub release publication, and clean Python 3.14 installation
  verification from public PyPI.
- Milestone 19.1: immutable layered application configuration with fixed
  default, system, user, environment, and CLI precedence; versioned strict TOML
  loading; deterministic `sdsctl` paths; source-aware diagnostics and provenance;
  read-only legacy discovery; CLI integration; and host-independent regression
  coverage.
- Milestone 19.2: compact Raspberry Pi TUI composition with dense borderless
  short-screen panels, concise audio and PSI summaries, an essential-controls
  footer, deterministic responsive and resize coverage, refreshed SVG evidence,
  and physical 800 by 480 Raspberry Pi validation.
- Milestone 19.3: renderer-neutral ownership of scanner control, PSI, one
  RTSP/RTP decoded-PCM fanout, and dynamic destinations; immutable runtime
  snapshots and ordered transitions; serialized startup, reverse-order cleanup,
  concurrent idempotent stop, listener isolation, redacted failures, and
  lifecycle regression coverage.
- Milestone 19.4: foreground `sdsctl daemon` ownership of scanner control, PSI,
  one RTSP/RTP audio session, and one decoded-PCM router; signal-safe SIGINT and
  SIGTERM shutdown, restored handlers, preserved primary failures, documented
  systemd `Type=simple` operation, regression coverage, and physical SDS200
  validation.
- Milestone 19.5: strict versioned read-only local API envelopes, capability
  negotiation, authoritative runtime, scanner, audio, and router snapshots,
  private Unix-domain socket ownership, safe stale-socket handling, bounded and
  isolated clients, deterministic process integration, CLI server limits, and
  host-independent regression coverage.
- Milestone 19.6: immutable versioned daemon event envelopes, authoritative
  snapshot-first subscriptions, one serialized renderer-neutral source
  aggregator, independent bounded queues with explicit sequence-gap recovery, a
  separate private `events.sock` endpoint, bounded clients and encoded event
  sizes, deterministic process lifecycle integration, CLI configuration,
  documentation, regression coverage, and physical SDS200 validation.
- Milestone 19.7: authoritative accepted-PCMU publication before decode,
  immutable RTP continuity metadata, independent bounded per-client queues with
  cumulative local-loss accounting, a strict versioned binary frame protocol,
  a third private `pcmu.sock` endpoint, bounded clients, payloads, frames, waits,
  and shutdown, deterministic daemon lifecycle integration, public decoding
  helpers, documentation, extensive regression coverage, a reusable hardware
  validator, and physical SDS200 validation with simultaneous API, event, and
  dual-PCMU clients.
- Milestone 19.8: capability-checked hold, next, previous, and bounded reconnect
  controls; serialized mutation ownership; scanner-acknowledged completion;
  stable redacted failures; regression coverage; and physical SDS200 validation.
- Milestone 19.9: explicit daemon CLI status, snapshots, safe controls, ordered
  event watching, PCMU playback and WAV recording, protocol compatibility, and
  physical SDS200 validation.
- Milestone 19.10: explicit daemon-backed TUI state, events, controls, playback,
  recording, and saved-recording workflows without opening scanner hardware or
  stopping daemon ownership, plus physical SDS200 validation.
- Milestone 19.11: validated playback, recording, and remote-profile destination
  manifests; deterministic activation resources; transactional replacement;
  failure-isolated reload; daemon lifecycle ownership; `SIGHUP`; regression
  coverage; and physical SDS200 validation.
- Milestone 19.12: compatibility, migration, deployment, and systemd
  documentation; adversarial client and shutdown validation; full Python
  3.11–3.14 CI and CodeQL validation; physical daemon-owned CLI and TUI
  validation; v0.19.0 trusted PyPI and GitHub publication; and clean Python 3.14
  installation verification from public PyPI.
- Milestone 20.1: optional loopback-only FastAPI/Uvicorn web-service foundation,
  versioned health/status/snapshot/OpenAPI routes, redacted failures, package
  extras, documentation, and host-independent regression coverage.
- Milestone 20.2: accessible responsive browser shell, packaged web assets,
  daemon-status polling, scanner/runtime summaries, restrictive response
  headers, light/dark presentation, compact layouts, and accessibility coverage.
- Milestone 20.3: same-origin Server-Sent Events over the ordered daemon event
  service with snapshot-first delivery, incremental updates, reconnect, polling
  fallback, authoritative reconciliation, and lifecycle coverage.
- Milestone 20.4: explicit browser playback over daemon-owned PCMU with
  AudioWorklet decoding, bounded buffering/resampling, loss telemetry,
  deterministic client cleanup, and physical SDS200 validation.
- Milestone 20.5: daemon-owned browser recording, recording API and ordered
  events, private finalized-WAV service, newest-first inventory, safe saved
  playback/download, disconnect survival, shutdown finalization, packaging and
  regression coverage, and physical SDS200 validation.
