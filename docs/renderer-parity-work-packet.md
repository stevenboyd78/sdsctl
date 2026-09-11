# Renderer parity: next implementation packet

This is source-backed development preparation, not a new feature or installation
guide. It supplements the historical
[Milestone 26.2 audit](capability-field-parity-audit.md) and the
[independent work packets](next-milestone-work-packets.md). Inspection baseline:
`ffb3e101de7014d7513daef49d454c81bdc7c59b` on `main`. Recheck the selected
implementation commit before starting. No physical scanner acceptance is added.

## Four concrete TUI omissions

These values already cross the shared state boundary. Adding a renderer does
not require a new scanner command, a second transport, or a new daemon poll.

| Value | Existing source and state | Current TUI / web presentation | Smallest safe follow-up |
| --- | --- | --- | --- |
| Talkgroup ID | `ScannerInfo.talkgroup_id` preserves `TGID` from the selected TGID/conventional/search node as text; snapshot and daemon-TUI decoder retain it | No TUI row; web identity group has `radio-talkgroup-id`; terminal monitor already prints Talkgroup | A bounded raw-text ID row in a reviewed TUI detail layout; do not parse it as an integer or derive it from the channel name |
| Unit ID | `ScannerInfo.unit_id` preserves `U_Id` from the same node families; snapshot and daemon-TUI decoder retain it | No TUI row; web identity group has `radio-unit-id`; terminal monitor already prints Unit ID | Preserve the scanner's exact meaningful text, including prefixes and leading zeroes; absent state clears the row |
| P25 status | `ScannerInfo.p25_status` reads raw `Property.P25Status`; snapshot and daemon-TUI decoder retain it | No TUI row; web identity group has `radio-p25-status` | An explicitly scanner-reported status; no new digital/analog, encryption or reception-quality inference |
| Battery | `ScannerInfo.battery` accepts only an optional finite float from `Property.Battery`; snapshot and daemon-TUI decoder retain zero and reject nonfinite values | No TUI row; web identity group has `radio-battery` | Optional raw telemetry with unavailable handling; do not label it volts, percent, charging state or a gauge without separate evidence |

Source locations:

- [ScannerInfo properties](../src/sds200/models.py) and
  [snapshot conversion](../src/sds200/state.py);
- [daemon-TUI string/float field decoder](../src/sds200/daemon_tui.py);
- [TUI panel composition](../src/sds200/tui.py), especially `_channel_panel`,
  `_state_panel` and `_system_panel`;
- [terminal monitor](../src/sds200/monitor.py);
- [web field targets and profile selection](../src/sds200/web_assets/dashboard.js)
  and [the corresponding HTML fields](../src/sds200/web_assets/dashboard.html).

The web inventory maps all 35 shared fields to unique HTML targets. Adaptive
profiles deliberately select field groups; a mapped field is not necessarily
visible in every current mode or collapsed view. Preserve that distinction when
comparing photographs. This packet does not update the historical audit's Home
Assistant columns: discovery templates and each card need their own current
review before any new entity or card-support claim.

## Completed foundations to reuse

- **Weather and SAME display are not missing:** the TUI already has a Weather
  panel with `Weather mode` and `SAME selection`, and the web has both fields.
  A configured SAME selection or a synthetic Weather Alert fixture does not
  establish an actual detected alert or authorize automatic recording.
- **Scanner and application recordings are distinct:** the TUI state panel uses
  `Scanner recording`; audio surfaces use `Audio recording`. Existing USB
  capability handling omits application-audio controls when unavailable. Do not
  reintroduce those rows while adding unrelated telemetry to the compact layout.
- **Remote waterfall transport already exists:**
  [remote service routing](../src/sds200/daemon_remote_service_server.py) serves
  the existing [waterfall record protocol](../src/sds200/daemon_waterfall_protocol.py)
  through authorized observation leases. The
  [validating waterfall client](../src/sds200/daemon_waterfall_client.py) accepts
  a transport abstraction as well as a local socket. Its older local-only
  descriptive text is not evidence that the remote service is absent.
  A future TUI renderer should consume that stream, not expose another port or
  bypass the shared daemon owner. No TUI waterfall pane exists at this baseline.

## Fixture and layout contract before implementing the four rows

1. Start with copied/synthetic GSI/PSI and daemon snapshots, not a live scanner.
   Give each field a unique sentinel and prove it survives parser, shared
   snapshot, remote decoder and the actual rendered panel. Test the raw field,
   not a matching word that happens to occur in a channel/system name.
2. Cover absent, empty and supplied values; ID `0`/leading zeroes/prefixes; finite
   battery zero; rejected NaN/infinity; and unknown P25 text. Never retain a
   previous channel's ID after an authoritative snapshot omits it.
3. Cover scan-to-search, Weather, Tone-Out, unknown-screen and reconnect
   transitions. Do not carry a field into a mode whose new snapshot lacks it.
4. Treat incoming text as data: include long strings, markup-like punctuation,
   Unicode and line/control characters in safe rendering tests. Decide the
   bounded presentation and clipping policy explicitly; do not silently rewrite
   the canonical scanner snapshot to make a panel fit.
5. At 100x30 and 160x45, compare panel regions before/after updates and with
   logs/help open. More rows must not recreate the earlier downward panel shifts
   or hide controls. Also check the existing narrower fallback layout. Choose
   an inspectable detail area or reviewed row grouping instead of indiscriminately
   adding four always-visible rows.
6. Keep direct USB and remote-daemon cases separate: absence is valid on models
   or modes that do not supply a value. Scanner model and firmware remain in
   the Scanner pane. No remote endpoint version is inferred from these fields.
7. After a frozen candidate passes automated checks, ask for concise visual
   acceptance on both bench Pis. Only separately captured real values can add
   field-specific physical evidence. SDS150 remains specification-only.

## Existing tests and gaps

| Existing tests | What they establish now | Still needed for new TUI rows |
| --- | --- | --- |
| [XML parsing](../tests/test_xml_protocol.py), [shared state](../tests/test_state.py) | Raw property parsing and shared conversion; invalid/nonfinite battery refusal; zero versus absence and clearing | Explicit per-ID/P25 sentinel assertions and rendered transition cases; merely having a value in a fixture is not an assertion |
| [Daemon TUI](../tests/test_daemon_tui.py) | Authoritative snapshots, typed decoding, battery validation and reconnect behavior | Per-field decoder-to-panel checks with populated and cleared values |
| [Web field parity](../tests/test_web_dashboard_field_parity.py) | All 35 fields have unique targets; complete projection and group hooks; zero/false-like formatting | Browser visibility depends on selected group; these source-contract tests are not screenshots or physical evidence |
| [TUI](../tests/test_tui.py) | Current adaptive panels, Weather labels, recording distinction and USB audio omission | New rows, stable dimensions, long/raw values, mode changes and both Pi layouts |
| [Remote observation](../tests/test_daemon_remote_observation.py), [transport](../tests/test_daemon_remote_transport.py), [reconnect](../tests/test_daemon_remote_reconnect.py) | Shared waterfall demand, existing routed record protocol and fresh-checkpoint reconnect | Future TUI consumer lifecycle, rendering/history bounds and physical mode acceptance |

Run these unchanged baselines before editing; report their exact candidate and
scope separately from any newly added tests. This packet itself adds no parser,
renderer, command, capability, telemetry unit, recording trigger or service.
