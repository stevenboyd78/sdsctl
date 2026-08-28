# Capability and field-parity audit

Milestone 26.2 records an evidence-backed physical-scanner-to-application
capability and field-parity audit for `sdsctl`. The repository baseline is merge
commit `eac8e8033527607789451dccf0112b240fa0222e`, and the audit was completed on
August 22, 2026.

This is an inventory and planning boundary. It does not infer undocumented
protocol semantics or convert synthetic fixtures into physical validation. It
does not claim that every modeled command works on every supported model,
firmware, and transport. A field delivered in daemon JSON or MQTT is also not
counted as human-visible unless a renderer actually presents it.

## Method and evidence

The audit traces each capability through the strongest available evidence:

1. a scanner screen, menu, function, protocol command, or application-only
   source;
2. physical observations, protocol documentation, sanitized fixtures, and tests;
3. parser/model preservation and renderer-neutral state or service projection;
4. Rich CLI, terminal monitor, Textual TUI, web dashboard, generic daemon
   API/MQTT, and Home Assistant presentation;
5. semantic controls and their safety boundary; and
6. model, firmware, and transport validation limits.

The primary implementation sources are `src/sds200/models.py`,
`src/sds200/state.py`, `src/sds200/scanner.py`, the renderer and daemon modules,
and their corresponding tests. Physical-validation statements remain bounded by
[supported scanner models](supported-models.md), [the Textual TUI](tui.md), and
[advanced protocol research](advanced-protocol-research.md).

### Evidence codes

| Code | Meaning |
| --- | --- |
| `P200` | Observed during bounded physical SDS200 firmware 1.26.01 validation. |
| `P100` | Covered only by the recorded SDS100 firmware 1.26.01 core USB/GSI validation boundary. |
| `S` | Supported by a reviewed protocol specification or command contract. |
| `F` | Covered by sanitized/synthetic fixtures and automated tests. |
| `I` | Established by implementation inspection; not itself physical evidence. |
| `A` | Application behavior with no direct physical-scanner UI equivalent. |
| `U` | Evidence is insufficient or the relevant semantics remain unknown. |

Physical validation of a screen family does not prove every value that the
screen might emit. Field rows therefore use `F` unless the recorded physical
acceptance explicitly supports the narrower claim.

### Finding classes

| Class | Meaning |
| --- | --- |
| `R1` | Scanner data is modeled or parsed but missing from one or more intended renderers. |
| `R2` | A modeled or parsed capability lacks a safe user-facing semantic control. |
| `R3` | A physical capability lacks sufficient protocol evidence, modeling, lifecycle ownership, or physical validation. |
| `R4` | An application-only capability has no physical-scanner UI equivalent and is not a parity defect. |
| `Covered` | No material gap was found across the currently intended surfaces. |
| `Context` | The value is intentionally retained for routing, identity, or safe controls rather than routine display. |

`R1` does not mean every renderer should display every internal index. The
follow-up decision must still consider operator value, layout, compatibility,
and whether a value is control context rather than presentation data.

## Supported-model and transport baseline

| Model | Encoded boundary | Strongest recorded validation | Audit limit |
| --- | --- | --- | --- |
| SDS100 | USB serial control, GSI/PSI, navigation and hold-key control, optional GSI battery, volume/squelch 0–15 | `P100`: core USB, model, firmware, and GSI on firmware 1.26.01 | No network control/audio and no mode-by-mode physical parity record. |
| SDS150 | USB serial control, GSI/PSI, navigation, GCS charge status, no hold-key control, volume/squelch 0–15 | `S`, `F` only | The complete model remains specification-only until representative hardware is available. |
| SDS200 | USB serial and UDP control, GSI/PSI, navigation and hold-key control, volume 0–29, squelch 0–19, RTSP/RTP audio | `P200`: core USB, Ethernet control, audio, and direct/daemon semantic hold and level mutation on firmware 1.26.01 | USB setter comparison is absent; native UDP level mutation is physically accepted. |

`ScannerCapabilities` is deliberately coarse. Its model validation status does
not encode per-command, per-mode, firmware, or transport evidence, and RTSP/RTP
audio remains a separate subsystem capability.

### Scanner-screen evidence

| Screen or mode family | Modeled state | Strongest evidence | Remaining limit |
| --- | --- | --- | --- |
| Conventional and trunk scanning | Hierarchy names, indexes and holds; channel identity; RF/service/IDs; levels, signal, P25, mute, and recording | `P200` for observed screen transitions and basic live state; `F` for exact field projection | SDS100 lacks an equivalent field-by-field record; SDS150 is specification-only. |
| Quick Search | Search frequency, modulation, hold, and detected sub-audio | `P200` for Quick Search Hold; `F` for exact field projection | Other model/firmware parity is not established. |
| Close Call | Searching state, receive/hold state, hit name/index/number, RF, signal/RSSI, and detected sub-audio | `P200` for searching and Hold; `F` for `CcHitsChannel` | A physical Close Call hit capture is still absent. |
| Weather | Weather mode, channel name/index/number, RF, hold, and optional SAME value | `P200` for Weather Scan/Hold; `F` for exact fields | Physical SAME alert content is still absent. |
| Tone-Out | Profile name/index/number, RF, hold, and Tone A/Tone B | `P200` for standby/Hold; `F` for exact fields | An actual physical Tone-Out detection remains unvalidated. |
| Unknown/future screen | Raw mode, screen, ordered nodes, attributes, and source XML | `F`, `I` | Preservation is deliberate; semantics are not inferred. |
| Analysis and discovery screens | Lossless or partial records for System Status, Current Activity, LCN Monitor, and some discovery nodes | `S`, `F`, `I` | Shared-state projection, lifecycle ownership, output semantics, and physical coverage remain incomplete. |

## Shared live-state field inventory

`RadioStateSnapshot` is the canonical 35-field renderer-neutral scanner state.
The daemon API, SSE payloads, and generic MQTT `state/radio` publication preserve
the complete mapping. The following presentation legend applies only to this
matrix:

- `R`: human-rendered;
- `J`: emitted as structured JSON/API/MQTT data;
- `U`: consumed for layout or control routing but not normally rendered;
- `C`: a semantic control is exposed; and
- `—`: absent from that surface.

The Rich column means `sdsctl scanner-info`; `sdsctl info` separately reports
volume and squelch. The terminal-monitor column means the continuously updating
`sdsctl monitor` surface. An asterisk means conditional or mode-specific
presentation. Milestone 26.4 makes raw `screen` and `screen_kind` separate web
values so unknown scanner screens remain visible without inferred semantics. In
the Home Assistant column, `R+C* / —` means an
optional discovered switch presents and controls the value while the first-party
Lovelace card remains read-only.

| Field | Source/evidence | Rich | Monitor | Textual TUI | Web UI | API/SSE/MQTT | HA discovery/card | Finding |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mode` | GSI/PSI; `P200`, `F` | R | R | U/R* | R | J | — | R1: Home Assistant |
| `screen` | GSI/PSI; `P200`, `F` | R | — | U/R* | R | J | — | R1: monitor and Home Assistant |
| `screen_kind` | Derived classification; `F`, `I` | — | — | U | R | J | R* | R1: CLI |
| `system` | GSI/PSI; `P200`, `F` | R | R | R* | R | J | R | Covered on applicable panels |
| `department` | GSI/PSI; `P200`, `F` | R | R | R* | R | J | R | Covered on applicable panels |
| `site` | GSI/PSI; `P200`, `F` | R | R | R* | R | J | R | Covered on applicable panels |
| `system_index` | Node attribute; `F` | — | — | U | R | J | — | Context |
| `system_hold` | Node attribute; `P200`, `F` | — | — | R+C | R+C | J | R+C* / — | R1: CLI |
| `department_index` | Node attribute; `F` | — | — | U | R | J | — | Context |
| `department_hold` | Node attribute; `P200`, `F` | — | — | R+C | R+C | J | R+C* / — | R1: CLI |
| `site_index` | Node attribute; `F` | — | — | U | R | J | — | Context |
| `site_hold` | Node attribute; `P200`, `F` | — | — | R+C | R+C | J | R+C* / — | R1: CLI |
| `channel` | Mode-selected node; `P200`, `F` | R | R | R | R | J | R | Covered |
| `channel_index` | Node attribute; `F` | — | — | U | R | J | U | Context |
| `channel_number` | Mode-selected node; `F` | — | — | R* | R | J | — | R1: CLI and Home Assistant |
| `channel_kind` | Source node tag; `F`, `I` | — | — | U/R* | R | J | U | Context |
| `channel_hold` | Node attribute; `P200`, `F` | — | — | R+C | R+C | J | R+C* / — | R1: CLI |
| `frequency` | Mode-selected node; `P200`, `F` | R | R | R | R | J | R* | Covered when available |
| `modulation` | Mode-selected node; `P200`, `F` | R | R | R | R | J | R* | Covered when available |
| `sub_audio_detected` | Search/Close Call SAD; `F` | — | — | R* | R | J | — | R1: CLI and Home Assistant |
| `tone_out_tone_a` | Tone-Out node; `F` | — | — | R* | R | J | R* | R1: Rich and monitor |
| `tone_out_tone_b` | Tone-Out node; `F` | — | — | R* | R | J | R* | R1: Rich and monitor |
| `weather_mode` | Weather node; `P200`, `F` | — | — | R* | R | J | — | R1: CLI and Home Assistant |
| `weather_same` | Weather node; `F` | — | — | R* | R | J | — | R1: CLI and Home Assistant |
| `service_type` | Mode-selected node; `P200`, `F` | R | R | R* | R | J | R* | Covered when available |
| `talkgroup_id` | GSI/PSI; `F` | — | R | — | R | J | — | R1: Rich, TUI, and Home Assistant |
| `unit_id` | GSI/PSI; `F` | — | R | — | R | J | — | R1: Rich, TUI, and Home Assistant |
| `volume` | GSI/PSI; `P200`, `F` | — | R | R+C | R | J | — | R1: direct and daemon-owned UDP mutation physically accepted; Home Assistant remains |
| `squelch` | GSI/PSI; `P200`, `F` | — | R | R+C | R | J | — | R1: direct and daemon-owned UDP mutation physically accepted; Home Assistant remains |
| `signal` | GSI/PSI; `P200`, `F` | R | R | R | R | J | R | Covered |
| `rssi` | GSI/PSI; `P200`, `F` | R | R | R* | R | J | R | R1: generic TUI panel |
| `battery` | Optional GSI/PSI Property; `S`, `F`; `P100` absence | R | — | — | R | J | — | R1: monitor, TUI, and Home Assistant; raw value only |
| `p25_status` | GSI/PSI; `F` | — | — | — | R | J | — | R1: CLI/TUI and Home Assistant |
| `mute` | GSI/PSI; `P200`, `F` | R | R | R | R | J | — | R1: Home Assistant |
| `recording` | Scanner GSI/PSI flag; `P200`, `F` | R | R | R | R | J | — | R1: Home Assistant; distinct from application recording |

The matrix exposes four high-confidence presentation findings without requiring
new protocol semantics:

- Milestone 26.4 closes the web half of the Search, Close Call, Weather, and
  Tone-Out presentation gap through the existing complete shared state;
- Milestone 26.5 shares optional finite SDS100 battery telemetry on the existing
  PSI/GSI lifecycle without assigning unit, percentage, range, or charging
  semantics;
- Milestone 26.6 adds a fixed compatibility-reviewed Home Assistant core of
  Site, Frequency, Modulation, and Service Type without dynamic discovery or
  new scanner state;
- Milestone 26.9 adds fixed configured Tone-Out Tone A and Tone B sensors plus
  optional compact and display-card fields without new scanner polling or state;
- Rich, monitor, TUI, and Home Assistant gaps remain surface-specific rather
  than losses from the shared state; and
- Home Assistant Discovery intentionally exposes a small stable core even though
  the generic MQTT topic carries the complete snapshot.

Structured CLI output is a separate data surface: daemon-client snapshot/status
JSON and direct event JSON preserve their complete structured payloads. The `R1`
labels above concern human renderers, not loss from those machine-readable paths.

### Renderer-neutral semantic presentation

| Semantic projection | Rich CLI | Textual TUI | Web | Home Assistant | Finding |
| --- | --- | --- | --- | --- | --- |
| Connection, activity, signal band, holds, availability, severity, service, mute, and recording from `ScannerPresentation` | Uses only activity, signal, mute, and recording roles for styling; labels and values remain raw | Renders the broader semantic labels/state | Independently derives a smaller view from raw state | Uses selected raw-state templates | R1: the shared semantic projection is not consumed consistently |

## Modeled data outside shared live state

| Surface | Modeled or preserved data | Current product exposure | Finding |
| --- | --- | --- | --- |
| GCS charge status | Status/code, voltage, capacity, current, temperature, and charging predicate | Dedicated `sdsctl battery`; absent from shared state and SDS150 remains specification-only | R1/R3 |
| System Status | Sixteen exact-string fields including system/site identity, signal, quality, activity, IDs, WACN, NAC, Color, RAN, Area, attenuation, frequencies, and P25 status | Parsed projection only; absent from shared state and every renderer | R1/R3 |
| STS display | Display form, ordered line text/mode pairs, nine reserved fields, and raw packet | Dedicated `sdsctl command STS` rendering; V1.02/V2.00 specification-backed line shape with synthetic valid and malformed parser coverage; no shared-state projection or physical STS acceptance | R1/R3 |
| Unknown GSI/PSI material | Raw XML, ordered/repeated nodes, attributes, `MonitorList`, `Avoid`, `RecSlot`, `LVL`, and `IFX` | Losslessly preserved but not semantically projected | R3 |

Raw preservation prevents silent data loss. It does not make an unknown field
safe for control, comparison semantics, or renderer-specific interpretation.

## Semantic-control parity

| Capability | Direct CLI | Standalone TUI | Daemon-client CLI | Daemon TUI | Web | Home Assistant | Finding |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Hold current system/department/site/channel | Indexed hold plus explicit desired state | Explicit hold/release | Indexed hold plus explicit desired state | Explicit hold/release | Explicit hold/release | Optional discovered hold/release | Covered: Milestone 26.8 direct and daemon-owned physical acceptance complete |
| Previous/next selection | Typed targets | Current channel only | Typed targets | Current channel only | Current channel | Optional current-channel controls | Covered within each renderer's advertised scope |
| Volume/squelch mutation | Exact typed level | Typed bounded control | Exact semantic level | Typed bounded control | — | — | Covered across implemented direct and daemon surfaces; firmware 1.26.01 native-UDP acceptance complete |
| Scanner reconnect | — | Restart owned transport | Request daemon reconnect | Request daemon reconnect | Request daemon reconnect | Optional discovered daemon reconnect | Covered within each renderer's ownership boundary |
| Raw scanner command | Explicit escape hatch | — | — | — | — | — | Intentional boundary; raw access is not semantic parity |
| Daemon WAV recording/status/inventory | Different direct-audio workflow | Client-local recording | — | Client-local PCMU recording | Status/start/stop/list | Status sensors only | R1/R2/R4: daemon-client CLI has no manager operations and TUI recordings have a different owner |
| Scanner-native URC recording | — | — | — | — | — | — | R2/R3: typed foundation exists, physical/lifecycle evidence does not |
| Favorites write execution | — | — | — | — | — | — | R1/R2: verified executors exist; the first interactive surface is Milestone 26.3 |

Browser WAV recording and inventory are application recording, not the scanner's
GSI `Rec` flag or scanner-native `URC` capability. Likewise, Home Assistant's
recording entities report daemon-owned WAV recording rather than scanner-native
recording state. A daemon-backed TUI records its own PCMU client stream; it does
not control `DaemonRecordingManager`.

### Milestone 26.8 physical semantic-control acceptance

On 2026-08-23, an SDS200 running firmware 1.26.01 completed authoritative enter
and release for System, Department, Site, and Channel through both the direct CLI
and a single-owner daemon runtime/API client path. The test restored System and
Site Hold to `On`, Department and Channel Hold to `Off`, and the original Utah
County Simulcast site index `35297`; shutdown removed the daemon socket cleanly.

The same scanner completed reversible native-UDP level changes through both
paths: volume `0` to `1` to `0` and squelch `2` to `3` to `2`. Initial timeouts
identified two compatibility details. Firmware 1.26.01 returns `VOL,OK` and
`SQL,OK` setter acknowledgements, which must remain generic packets rather than
numeric getter responses, and a Menu-tree `GSI` response may omit both levels.
Daemon completion therefore uses the matching scalar getter and merges its value
into the existing snapshot. Every requested level was getter-confirmed, final
direct getters verified the restored values after daemon shutdown, and shutdown
removed the daemon socket cleanly. USB setter comparison remains unperformed but
does not limit the accepted native UDP path.

## Application audio and recording detail

| Application surface | Renderer-neutral detail | Human presentation gap | Class |
| --- | --- | --- | --- |
| Audio session | Lifecycle/timing/output, packet/sample totals, and nine reliability counters | CLI audio paths and the separate `AudioRecordingSession` TUI branch show the most detail; the normal `TuiAudioSession` panel is reduced in both standalone and daemon modes; web/HA do not expose the full set | R1/R4 |
| Daemon recording | Paths, timestamps, timing, packet/sample/reliability/sink counters, completed count, closure, and error | Web omits metadata path, some counters, sink state, completed count, and error; HA exposes only active/status | R1/R4 |
| Recording inventory | Compatibility, sidecar health, frames, sizes, timestamps, issue/attention state, and aggregate diagnostics | Web shows a subset; the TUI library uses a separate compatible-WAV-only `RecordingEntry` rather than this inventory model | R1/R4 |
| Recording sidecar scanner state | Ten fields: mode, hierarchy/channel, RF/service, talkgroup, and unit context | Omits raw screen/classification, all indexes/holds, channel number/kind, special-mode fields, levels, signal/RSSI, P25, mute, and scanner recording | R1/R4 |
| Remote stream metadata | Activity/availability plus system, department, site, channel, frequency, service, talkgroup, and unit context | The bounded published title uses only system, department, and channel-or-frequency; site, service, and IDs remain context | Context/R4 |

These are legitimate application parity questions, but their lack of a scanner
screen equivalent makes the underlying capabilities `R4`, not scanner defects.

## Advanced protocol and analysis inventory

| Capability | Implemented foundation | Product renderer/control | Evidence boundary | Finding |
| --- | --- | --- | --- | --- |
| GLT Favorites retrieval | Lossless root and record preservation | None | `S`, `F`; no semantic Favorites-list projection or physical validation | R2/R3 |
| FQK quick keys | Exactly 100 nonexistent/disabled/enabled states and typed read/write | None | `S`, `F`; no physical write acceptance | R2/R3 |
| URC scanner recording | Typed stopped/recording state and start/stop commands | None | `S`, `F`; no complete physical lifecycle record | R2/R3 |
| MSI/MNU menus | Lossless menu records, selected values, inputs/locations, errors, and indexed menu open | None | `S`, `F`; MSV/MSB mutation and complete menu lifecycle are not evidenced | R2/R3 |
| AST/APR analysis | Bounded starts, pause/resume, and ordered Current Activity/LCN records | None | `S`, `F`; ownership, stop/reconnect, correlation, and full output semantics remain incomplete | R3 |
| GST/PWF/GWF waterfall | Exact typed GST plus variable PWF fields and exactly 240 uninterpreted GWF values, including the physically observed lowercase hexadecimal syntax and terminal separator; one demand-driven radio/daemon-owned, recurring-get session and private bounded local fanout | Authenticated demand-driven web spectrum and rolling-waterfall Canvas with semantic lifecycle/loss telemetry and preserved raw strings; the renderer validates and normalizes base-16 codes per frame for relative presentation only; bounded validating daemon-client diagnostic | `S`, `F`, `P`; SDS200 firmware 1.26.01 LAN qualification covers one-frame GWF gets, overlapping clients, reconnect, restart, cleanup, and normal-mode restoration; the observed syntax does not establish magnitude or calibration, and the web renderer remains explicitly relative and uncalibrated; FFT magnitude/calibration and other model/firmware semantics remain unknown | R3 |
| System Status/RF Power Plot | Typed System Status projection and bounded start parameters | None | `S`, `F`; RF output parsing and richer field semantics remain incomplete | R1/R3 |
| QSH exact-frequency search | No implemented exact-frequency form | None | Exact `FRQ` syntax is unresolved | R3 |
| GW2 binary waterfall | No binary framing implementation | None | Framing and semantics are unresolved | R3 |
| Discovery, Activity Log, Raw Data Output | Sparse tokens or node lookup only | None | Insufficient fixtures, typed projection, lifecycle, and physical evidence | R3 |

Unknown or deferred is deliberately different from unsupported. No renderer work
should begin for an `R3` item by guessing fields, command syntax, lifecycle, or
model applicability.

## Favorites interface inventory

Favorites has a mature renderer-neutral/Python foundation but no current Rich
CLI, terminal monitor, Textual TUI, web, or Home Assistant workflow.

| Capability | Renderer-neutral/Python foundation | CLI | TUI | Web | Home Assistant | Finding |
| --- | --- | --- | --- | --- | --- | --- |
| Storage binding and source provenance | Exact catalog/document snapshots, orphan and ambiguity diagnostics | — | — | — | — | R1 |
| Hierarchy/navigation | Catalog-ordered traversal across all eight navigation kinds | — | — | — | — | R1 |
| Search/filter | Text, kind, and inclusive-subtree query | — | — | — | — | R1 |
| Validation/comparison | Stable schema diagnostics and exact baseline/candidate changes | — | — | — | — | R1 |
| Supported editing | Existing Name Tag replacement, supported HPD leaf deletion, and exact-template leaf creation | — | — | — | — | R1/R2 |
| Write planning | Exact baseline/intended plan, blockers, stale precondition, and no-op detection | — | — | — | — | R1/R2 |
| Verified writes | Copied-tree and qualified USB backup/staging/readback/rollback/report executors | — | — | — | — | R1/R2 |
| FTP ingestion | Bounded exact read-only retrieval on trusted networks/VPNs | — | — | — | — | R1; writable FTP remains unsupported |
| Assisted external synchronization | Explicit provider provenance, mapping, review, conflict decisions, and accepted execution | — | — | — | — | R1/R2; excluded from the initial editor |

Milestone 26.3 addresses the first seven presentation/control gaps without
absorbing FTP writes or an assisted-synchronization UI.

## Application-only capabilities

The following `R4` capabilities have no direct physical-scanner UI equivalent:

- daemon ownership, reconnect policy, client fanout, private API/events/PCMU
  services, semantic MQTT, and Home Assistant Discovery;
- browser authentication, SSE, PCM playback, daemon WAV recording, recording
  inventory/playback/download, and multi-client session revocation;
- Home Assistant App packaging, Ingress, Lovelace delivery, persistent media,
  and dedicated MQTT controls; and
- Favorites copied-tree and read-only FTP ingestion, qualified USB discovery,
  copied-tree/USB verified backup/staging/readback/rollback execution, schema
  diagnostics, comparison, and assisted external synchronization.

These capabilities still require cross-interface quality and safety review, but
absence from the scanner itself is expected.

## Prioritized findings and scheduling

| ID | Follow-up | Class | Scheduling decision |
| --- | --- | --- | --- |
| `A01` | Add the first interactive Favorites Workspace editor over existing browse, edit, plan, and verified-executor contracts | R1/R2 | Completed in Milestone 26.3 without absorbing unrelated parity gaps |
| `A02` | Decide whether battery and System Status need renderer-neutral state/services | R1/R3 | Completed in Milestone 26.5: SDS100 PSI/GSI battery joins shared state; GCS and System Status remain separate pending explicit lifecycle and physical evidence |
| `A03` | Present shared hierarchy, RF, identifier, P25, and special-mode fields in the web dashboard | R1 | Completed in Milestone 26.4 without new scanner semantics |
| `A04` | Evaluate additional stable Home Assistant entities, including site and selected mode-specific values | R1 | Completed in Milestone 26.6 with four fixed read-only sensors and matching optional card fields |
| `A05` | Align explicit hold/release and volume/squelch behavior across direct and daemon-backed CLI/TUI surfaces | R2/R3 | Completed in Milestone 26.8 with direct and daemon-owned native-UDP physical acceptance |
| `A06` | Expose richer audio, recording, inventory, and sidecar diagnostics where operationally useful | R1/R4 | Later application-observability slice |
| `A07` | Complete evidence, lifecycle, and physical validation for advanced protocol surfaces before adding controls | R3 | Blocked on protocol/hardware evidence, not on renderer construction |
| `A08` | Expand per-mode SDS100 validation and perform first SDS150 physical validation | R3 | Hardware-dependent; SDS150 remains deferred |

`A01` through `A04` are complete. The remaining ordering avoids turning a broad
inventory into silent authorization for unrelated runtime or protocol work.

## Milestone 26.5 lifecycle decision

The shared-state decision follows the source lifecycle rather than superficial
field similarity:

- optional finite SDS100 `Property.Battery` joins `RadioStateSnapshot` because
  it arrives with the authoritative GSI/PSI frame; omission clears prior state
  and literal zero remains a value;
- the raw battery float carries no inferred unit, percentage, range, charging
  meaning, or applicability to another model;
- SDS150 `GCS` stays an explicit request/response operation and must not become
  automatic polling or cached daemon state without observation-time, staleness,
  cadence, and physical SDS150 acceptance contracts;
- ordered repeated `SystemStatus` records stay in the lossless `ScannerInfo`
  model and immutable `SystemStatusProjection`; and
- a future System Status service must own the selected site, analysis session,
  APR behavior, timestamps, cancellation, disconnect/reconnect policy, and
  physical model/firmware/transport acceptance before it adds daemon or renderer
  exposure.

This decision does not authorize automatic `AST,SYSTEM_STATUS`, an invented
acknowledgement-to-frame transaction, flattening repeated records, SDS150 support
expansion, Home Assistant entity growth, or `STS`/RF Power Plot work.

## Milestone 26.6 Home Assistant compatibility decision

The Home Assistant expansion is deliberately smaller than the remaining parity
inventory:

- Site completes the stable hierarchy already represented by System,
  Department, and Channel;
- Frequency, Modulation, and Service Type are the broad mode-selected RF/service
  context already shared across direct, TUI, web, API, SSE, and generic MQTT
  surfaces;
- all four components have fixed deterministic IDs and reuse the existing
  retained `state/radio` topic;
- each nullable field combines daemon availability with field availability, so
  omission, null, or empty text marks only that sensor unavailable; and
- the bundled read-only card adds matching optional selectors while old card
  configurations retain their existing rendered structure.

This closes `A04` without adding dynamic components, commands, scanner polling,
state fields, inferred units, or another scanner owner. The other Home Assistant
gaps remain candidates for separate compatibility review rather than an implied
entity backlog.

## Milestone 26.9 Tone-Out compatibility decision

Configured `ToneOutChannel` `ToneA` and `ToneB` values already traverse shared
state and the canonical MQTT radio topic. Two fixed optional sensors now expose
that text with field-level availability, and both bundled cards accept them
without invalidating existing fourteen-entity configurations. The cards present
a numeric zero with an optional `Hz` suffix as `Detect`; entity state remains raw
and nonzero or unrecognized nonempty text is not reinterpreted. These configured
values remain distinct from detected search or Close Call `SAD`.
