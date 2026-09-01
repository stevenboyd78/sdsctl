# Changelog

## Unreleased

## 0.26.0

- Track the sdsctl v0.26.0 release while preserving the App name, slug, image
  identity, MQTT identities, scanner ownership, persistent recordings, custom-
  integration 0.1.5 artifact, and installed first-party custom elements.
- Keep recurring text-GWF requests on the daemon's phase-stable 250 ms schedule,
  expose bounded round-trip and scheduler telemetry, and refresh typed GST span
  metadata independently without interrupting frames after a failed refresh.
- Follow live scanner span changes in the Waterfall card by applying the
  daemon's current typed GST status carried with bounded GWF records. A missed
  status refresh retains the last valid frequency scale without interrupting
  waterfall delivery.
- Install one digest-qualified `sds200-cards.js` aggregate entry point after the
  three independently packaged first-party card modules. New installations can
  register one resource while existing individual URLs remain compatible; App
  startup does not add, update, or remove Home Assistant resource records.
- Normalize the Waterfall card graphical editor's exact string-serialized 60,
  120, and 240-frame history selections to the existing bounded numeric
  capacities, and accept Home Assistant's host-owned `grid_options`
  section-layout metadata, instead of rejecting valid dashboard selections.
- Retain the qualified text `PWF`/`GWF` Waterfall path after a bounded physical
  SDS200 firmware 1.26.01 LAN research candidate returned `ERR\r` for
  `GW2,1,ON` and established no binary framing or production negotiation
  contract.

## 0.25.0

- Track the sdsctl v0.25.0 release while preserving the App name, slug, image
  identity, MQTT identities, scanner ownership, persistent recordings, and
  installed first-party custom elements.
- Add the 21 System web palettes as per-card graphical-editor choices in the
  compact Scanner, scanner-style Display, and Waterfall cards. Existing Home
  Assistant theme-following and specialized Display/Waterfall presets remain
  compatible, and card palette selection stays presentation-only.
- Keep the live daemon-event status right-aligned and single-line when a
  non-System theme hides the System palette selector instead of leaving the
  message in the selector's narrow center track.
- Add the System web theme's responsive, browser-local palette selector to the
  Ingress dashboard. Follow-device remains the default; the full 21-scheme
  Textual TUI list is available only while System is active and changes
  presentation without exposing App configuration, credentials, or scanner
  control state.
- Add an authenticated Ingress-only Home Assistant workspace for protected HAOS
  installations, where a host Docker CLI is intentionally unavailable. The
  dedicated tab keeps integration lifecycle, Core-restart guidance, and private
  live-audio bridge controls out of read-only Diagnostics and is omitted from
  every non-Ingress dashboard. Compact controls and bounded pane scrolling keep
  the final restart and reauthentication guidance reachable without allowing the
  surrounding dashboard to clip it.
  Exact-digest installation, update, rollback, removal, rollback discard, and
  bridge-key rotation retain their existing atomic and explicit boundaries;
  bridge-key reveal is concealed, `no-store`, and cleared after 60 seconds.
- Package the versioned first-party `sdsctl` Home Assistant custom integration
  and its explicit digest-confirmed install, update, rollback, recoverable
  removal, and permanent rollback-discard commands. Normal App startup never
  writes or activates the integration and never restarts Home Assistant Core.
- Add the private protocol-versioned Core-to-App live-audio service and the
  browsable `media-source://sdsctl/live` item. Home Assistant resolves one
  signed, bounded Core proxy URL with exact `audio/mpeg` type; one-time App
  capabilities and one shared daemon-owned MP3 encoder remain hidden from the
  target, MQTT, entities, diagnostics, and browser storage.
- Install the first-party `/local/sds200/sds200-waterfall-card.js` resource. The
  read-only responsive card uses Home Assistant-authenticated App Ingress to
  render the existing daemon's relative, uncalibrated waterfall stream with
  bounded history, presentation controls, lifecycle telemetry, independent
  per-card demand leases, and deterministic cleanup without another scanner
  connection or high-rate MQTT entities.

## 0.24.0

- Track the sdsctl v0.24.0 release while preserving the App name, slug, image
  identity, MQTT identities, installed card resources, and single-owner
  boundary. The local Favorites editor and its explicit RadioReference workflow
  are not exposed through Home Assistant or MQTT.
- Package the redesigned authenticated Controls pane with complete current
  System, Department, Site, and Channel context, independent held-state text,
  and Previous, desired-state Hold/Release, and Next actions for every scope.
  Existing typed daemon operations, browser audio, recording, Waterfall,
  responsive themes, and both bundled card modules retain their established
  interfaces.

## 0.23.0

- Track the sdsctl v0.23.0 release while preserving the App name, slug, image
  identity, MQTT identities, installed card resources, and single-owner
  boundary.
- Add the authenticated Home Assistant Ingress path for the demand-driven
  Waterfall pane and private daemon waterfall service. The responsive web
  workspace renders exact hexadecimal GWF records as explicitly relative,
  uncalibrated spectrum and history data without adding MQTT waterfall entities,
  scanner tuning, or a Home Assistant waterfall card.
- Package the responsive six-pane web workspace, the original
  Pip-Boy-inspired built-in web theme, ordered-event recovery, and managed-theme
  source-snapshot hardening. Scanner controls, browser audio, recording,
  finalized playback, and downloads retain their established interfaces.
- Add one fixed read-only Screen Kind MQTT Discovery sensor and an opt-in Auto
  SDS200 Display layout. Auto selects Search/Close Call, Weather, or Tone-Out
  presentation from the normalized scanner state and uses a configured Simple
  or Detail fallback for scanning, missing, unavailable, unknown, or future
  values. Existing explicit layouts and the compact card remain unchanged.

## 0.22.0

- Track the sdsctl v0.22.0 release while preserving the compatibility-sensitive
  Home Assistant App name, slug, image identity, MQTT identities, and bundled
  custom elements. Managed third-party Home Assistant modules remain outside
  the App's automatic installation path and require explicit digest-confirmed
  operator activation.

- Package the unchanged compact SDS200 Scanner and SDS200 Display modules under
  separate `themes/home-assistant/<theme-name>/` directories with versioned
  manifests and a validated built-in registry. Installed filenames,
  `/local/sds200/` resource URLs, manual registration, card configuration, and
  rendering remain unchanged.
- Add fixed read-only configured Tone-Out Tone A and Tone B MQTT Discovery
  sensors plus matching optional selectors in both bundled Lovelace cards.
  Numeric zero tones render as `Detect` while preserving raw entity state.
- Add the separately registered `/local/sds200/sds200-display-card.js` resource
  with five scanner-style layouts, three palettes, a viewport-bounded 4:3 mode,
  and a graphical editor compatible with existing fourteen-entity configurations.
- Add fixed read-only Site, Frequency, Modulation, and Service Type MQTT
  Discovery sensors plus matching optional bundled Lovelace card selectors.
  Existing entity identities and controls remain unchanged, and a nullable
  mode-dependent sensor becomes unavailable when its current field is absent.

## 0.21.0

- Track the sdsctl v0.21.0 release while preserving the compatibility-sensitive
  Home Assistant App name, slug, image identity, and Python distribution name
  as `sds200`.
- Repository and documentation links use the canonical
  `stevenboyd78/sdsctl` project identity. The existing Home Assistant MQTT
  Discovery, scanner-control, Ingress, audio, and recording ownership model
  remains unchanged by release preparation.

## 0.20.2

- Home Assistant MQTT Discovery now includes seven bounded scanner-control
  entities alongside the existing ten state/diagnostic entities: System,
  Department, Site, and Channel Hold switches plus Previous Channel, Next
  Channel, and Reconnect Scanner buttons. The App keeps generic daemon MQTT
  commands disabled; dedicated QoS 0 non-retained control topics translate into
  fresh internal typed daemon requests and preserve the daemon as sole scanner
  owner.
- The App now installs a bundled read-only SDS200 Lovelace card under Home
  Assistant `www`, available as `/local/sds200/sds200-card.js`; users register
  the module once through the normal Home Assistant dashboard Resources page,
  then configure the card through Home Assistant's built-in graphical entity
  selectors.
- Configuration fields now have user-facing names and descriptions, including
  explicit guidance that `recording_directory` is relative to Home Assistant
  `/media` and that the default resolves to `/media/sdsctl/recordings`.

## 0.20.1

- Recordings now use writable Home Assistant media storage, defaulting to
  `/media/sdsctl/recordings`, with a configurable media-relative
  `recording_directory`.
- Existing v0.20.0 files under `/data/recordings`, including metadata sidecars
  and nested library paths, migrate safely without overwriting destination
  conflicts.
- The dashboard groups daemon runtime with scanner connection, moves scanner
  reconnect into that panel, separates active capture from recent recordings,
  and gives the finalized library more vertical room.
- The Home Assistant sidebar panel requests the `mdi:radio-tower` icon.

## 0.20.0

- Project-consistent Home Assistant App icon and logo presentation assets.
- Initial Home Assistant App packaging for the existing SDS200 daemon and web
  dashboard.
- Supervisor MQTT service adaptation with ten read-only MQTT Discovery entities.
- Authenticated Ingress dashboard with live scanner state, controls, browser
  audio, recording, and saved-recording playback.
- Persistent recordings under `/data/recordings`.
- Fixed UDP `50000` publication for inbound SDS200 RTP without host networking.
- amd64 and aarch64 image build/publishing workflow.
