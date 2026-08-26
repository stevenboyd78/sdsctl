# Roadmap

This document records ordered work planned for `sdsctl`. Listed items are
not available until they appear in a released changelog. Milestone order may
change as hardware validation, protocol research, and user feedback produce new
information.

The broader product direction, architectural constraints, deferred capabilities,
and ideas that are not ready for scheduling are recorded in
[the project vision](docs/project-vision.md).

## Active milestone

### Milestone 27.2.1 — Reviewed network and protocol hardening

Milestone 27.2 is closed with its physically qualified waterfall data plane and
must not be amended with unrelated review work. Milestone 27.2.1 independently
reproduces and closes the bounded network, parser, and credential-handling
findings from the post-milestone implementation review. The review package is
evidence rather than implementation authority, and its unexecuted draft patch
must not be applied wholesale.

Bound RTSP response headers and declared bodies before untrusted growth, close
or reset a failed RTSP exchange deterministically, and cover exact-limit,
boundary-straddling, malformed-length, and over-limit behavior. Bound fragmented
XML assembly by fragment count, aggregate source bytes, child count, and
monotonic lifetime, with deterministic sequence discard and later recovery.

Isolate unexpected UDP application-callback failures so one rejected line
cannot terminate the reader. Record bounded failure telemetry and a redacted
diagnostic without logging packet contents. Reject structurally odd STS display-
field collections without including raw scanner display data in the resulting
error.

Treat Broadcastify source and metadata credentials as cleartext whenever the
configured provider endpoint uses ordinary HTTP. Require explicit operator
acknowledgement of that risk unless a provider-supported TLS source endpoint has
been independently verified. Do not silently wrap a documented plaintext port
in TLS or claim transport confidentiality without provider evidence.

Record the two rejected review recommendations explicitly. The current
descriptor-relative theme copy, stable file-identity checks, private staging,
and source/staged digest comparison already close the reported theme race. RTP
padding must continue to follow RFC 3550, in which the final padding octet gives
the padding count, rather than requiring every padding octet to repeat that
count.

Acceptance must cover RTSP exact and one-byte-over limits, delimiter receive
boundaries, XML aggregate and expiry limits, recovery after every rejection, a
callback exception followed by later valid datagrams, redacted malformed STS
handling, Broadcastify migration and risk-acknowledgement behavior, credential-
free diagnostics, documentation, distribution contents, and the complete
static and test suite. Existing scanner control, PSI, RTSP/RTP audio, daemon
ownership, Home Assistant packaging, and waterfall behavior must not regress.

Do not add a waterfall renderer, dashboard layout change, new theme, new scanner
command, remote provider, or unverified Broadcastify TLS endpoint.

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
- Milestone 27.3: one responsive viewport-owned web workspace, with the stable
  system-adaptive `system` default and fallback theme redesigned around the
  existing scanner-display hierarchy and a modular original Pip-Boy-inspired
  built-in web theme. Every built-in theme uses the shared accessible Scanner,
  Controls, Audio, Recordings, and Diagnostics panes without document or active-
  pane scrolling at 390x844, 800x480, 1366x768, and 1920x1080 reference sizes.
- Milestone 27.4: responsive theme-aware web spectrum and rolling-waterfall
  workspace inside the shared viewport shell, with authenticated bounded demand,
  immediate CSS/Canvas recoloring, relative-data labeling, and loss telemetry.

#### Planned Milestone 27.3 contract

Milestone 27.3 converts the existing web dashboard into one responsive viewport-
owned workspace while preserving its authoritative daemon-client boundary,
authenticated session and Home Assistant Ingress behavior, all existing fields
and controls, browser audio, recording workflows, and theme lifecycle.

Keep `system` as the stable theme ID, picker order zero, browser-local default,
and safe fallback so existing stored selections and managed-theme failure
behavior require no migration. Redesign its presentation around the established
SDS200 Display card hierarchy: one prominent scanner-display pane with Simple,
Detail, Search/Close Call, Weather, and Tone-Out presentations driven by the
existing normalized screen kind and the same configurable scan fallback. Build
that presentation in the web renderer; do not load the Home Assistant custom-
element module or duplicate Home Assistant entity state.

The System presentation remains system-adaptive for light and dark preferences
while using scanner-display proportions, hierarchy, status strip, and mode-aware
field selection. Keep the complete web feature set reachable through one shared
keyboard-accessible pane model rather than permanently hiding it or shrinking it
below readability. Scanner, Controls, Audio, Recordings, and Diagnostics panes
use semantic HTML, explicit labels, predictable focus movement, and browser-
local presentation state. The future Waterfall pane may be reserved structurally
but must not connect to the daemon or render waterfall data in this milestone.

Add one built-in package under `themes/web/pip-boy-inspired/` with stable ID
`pip-boy-inspired`, picker label `Pip-Boy-inspired`, and order 50 after Amateur
Radio. Use an original retro-futurist field-terminal presentation with phosphor-
green or amber semantic tokens, restrained CRT depth, grid or radar
instrumentation, scanline effects, and hardware-console framing. Do not use
`Fallout` in the package ID or copy game logos, character or corporate artwork,
screenshots, sounds, proprietary fonts, copied hardware geometry, or other game
assets. The package remains local declarative CSS under the existing manifest,
CSP, digest, and managed-theme rules.

Apply the same shared viewport shell to System, LCARS-inspired, Matrix-inspired,
First Responder, Amateur Radio, Pip-Boy-inspired, and valid managed web themes.
At normal browser zoom, the document and active pane must fit without horizontal
or vertical scrolling at 390x844, 800x480, 1366x768, and 1920x1080, and scale
cleanly to larger full-screen viewports. Information that cannot fit concurrently
must remain available through explicit pane or page controls rather than being
clipped, reduced below readability, or placed in an unannounced scroll region.
At user text enlargement or browser zoom, accessibility and content reachability
take precedence over the decorative no-scroll composition.

Theme switching must preserve the selected pane, live scanner state, form and
control state, browser audio, recording state, and focus meaning. System
fallback, first-paint selection, managed-theme activation, same-origin delivery,
CSP, `nosniff`, reduced-motion, forced-colors/high-contrast behavior, and state
meaning independent of color must remain intact.

Acceptance must cover every automatic scanner screen transition, Simple and
Detail scan fallback, configured and zero/detect Tone-Out values, all existing
controls and all 35 radio fields, recording pagination, pane navigation by
pointer and keyboard, focus restoration, direct authenticated web sessions,
Home Assistant Ingress prefixing, sign-out and restart, every built-in theme at
every reference viewport, device-pixel-ratio and resize changes, reduced motion,
high contrast, missing and mutated managed-theme fallback, gallery/reference
captures, package contents, documentation, distribution builds, and the complete
static and test suite. Physical acceptance must use the existing SDS200 single-
owner guard and restore the repository Home Assistant App and normal scanning.

Do not add copied manufacturer or game assets, theme JavaScript, remote fonts or
resources, automatic theme downloads, a GUI theme, a new Home Assistant card, a
TUI layout, a waterfall daemon consumer, scanner mode navigation, or theme-owned
scanner controls.

#### Planned Milestone 27.4 contract

Milestone 27.2 is closed with a physically qualified, renderer-neutral text-
waterfall data plane. A LAN-connected SDS200 running firmware 1.26.01 returned
the typed `GST` checkpoint, a one-field `PWF,OK` response, and 240-value `GWF`
frames with the specification-defined trailing separator. On this firmware,
`GWF,1,ON` is a one-frame get rather than sustained publication, so the single
daemon-owned session polls it at a conservative 250 ms interval while demand
exists and tolerates fewer than three consecutive misses.

Time- and record-bounded physical runs validated repeated and overlapping local
clients, ordered isolated fanout, zero observed client loss or overflow,
interleaved PSI, scanner reconnect with interrupted/starting/running recovery,
daemon restart, last-client stop, both cleanup wires, private socket removal,
and return to normal scanning. The repository Home Assistant App was stopped to
preserve one scanner owner during direct branch qualification, then restored and
verified through its authenticated Ingress dashboard and live MQTT entities.
Raw programming and frequency data remain outside the repository.

Milestone 27.4 fills the shared Waterfall pane by integrating that private local
stream into the existing web service without changing the trust boundary. The
web process may connect to `waterfall.sock` only after the current session or
Home Assistant Ingress authentication succeeds, and it exposes waterfall
records only through a same-origin, authenticated, size-bounded streaming route.
A browser must never receive the Unix socket path, open another scanner
transport, or send `GST`, `PWF`, or `GWF` commands. Opening the workspace creates
demand; hiding, navigating away, signing out, disconnecting, or shutting down
releases it deterministically so the last consumer triggers scanner cleanup.

Render the 240-bin frames with a bounded Canvas-based spectrum and
rolling-waterfall surface rather than a 240-cell table. Canvas is the
high-frequency raster boundary, while adjacent semantic HTML exposes connection state,
uncalibrated/relative-data labeling, GST context, frame rate, frame age,
sequence, cumulative queue loss, overflow, poll failures, and session
transitions. Malformed, non-finite, incomplete, oversized, or out-of-order data
must fail closed without freezing the dashboard or retaining stale live state.

The visualization may scale observed numeric values into a clearly labeled
relative display but must not claim calibrated power, dB, signal strength, or
documented FFT magnitude semantics. Preserve the raw 240 strings below that
presentation boundary. Use lower, center, upper, and marker frequency metadata
only when their typed GST fields are structurally valid; otherwise show bin
position without inventing an RF axis. Do not derive scanner tuning or mode
navigation from pointer or touch input.

Use shared semantic visualization tokens with safe base defaults. System and
Pip-Boy-inspired provide deliberate spectrum, grid, marker, history, warning,
and unavailable-state colors; every other built-in and managed theme remains
legible through the defaults. Theme changes recolor both CSS and existing Canvas
history immediately without restarting or duplicating the daemon subscription.
The pane must fit the active viewport up to full screen without document or
panel scrolling at 390x844, 800x480, 1366x768, and 1920x1080.

Include keyboard-accessible pause/resume display, clear-history, and full-screen
controls; reduced-motion, high-contrast, resize, visibility, reconnect, and
empty/error states remain usable. Display pause may freeze rendering but must
not be described as pausing the scanner protocol.

Acceptance must cover authenticated route denial, Ingress prefixing, demand and
last-consumer cleanup, strict stream validation, reconnect and sequence-gap
handling, bounded rolling history, resize and device-pixel-ratio behavior,
theme switching, accessibility status, background-tab cleanup, browser and
daemon restart, responsive viewport references, existing dashboard/audio/
recording regression behavior, documentation, distribution builds, and the
complete static and test suite. Physical acceptance uses the SDS200 single-owner
guard and confirms normal scanner and repository App restoration through the
branch image.

Do not add binary `GW2`, high-rate MQTT entities, public waterfall sockets,
persistent waterfall history, calibrated FFT or RF-power claims, scanner tuning,
Waterfall-mode navigation, automatic scanner-screen switching, a Home Assistant
waterfall card, TUI/GUI rendering, Internet-facing access, or new third-party
JavaScript dependencies. Later milestones may reuse this renderer contract in
other interfaces only after their own lifecycle and performance boundaries are
defined.

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
