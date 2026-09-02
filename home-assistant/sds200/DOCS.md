# sds200 Home Assistant App

## Requirements

- Home Assistant OS with Apps/Supervisor support
- LAN-connected Uniden SDS200
- Home Assistant MQTT service
- UDP port `50000` available on the Home Assistant host

## Installation

For normal installation:

1. open **Settings > Apps > App store**;
2. open the top-right three-dot menu and choose **Repositories**;
3. add `https://github.com/stevenboyd78/sdsctl`;
4. select the **sds200** App from the repository;
5. install it, then configure the scanner host.

Published versions use the release image from GHCR. Local `/addons` staging is
only for development builds.

## Configuration

| Option | Required | Default |
| --- | --- | --- |
| `scanner_host` | yes | none |
| `mqtt_topic_prefix` | no | `sdsctl` |
| `recording_directory` | no | `sdsctl/recordings` |

Set `scanner_host` to the SDS200 LAN hostname or IP address.

Set `recording_directory` to a relative path below Home Assistant `/media`. The
default `sdsctl/recordings` resolves to `/media/sdsctl/recordings`. Absolute
paths and traversal components are rejected.

The App automatically obtains the selected MQTT service from Supervisor and
enables Home Assistant MQTT Discovery plus the dedicated Home Assistant control
adapter. The generic daemon MQTT request-envelope command topic remains disabled.

## Network audio

The SDS200 sends RTP audio back to the client over UDP. The App fixes that
destination at UDP `50000` and Supervisor maps host UDP `50000` to the same
container port.

If scanner status works but recordings show zero packets and zero samples, verify
the App Network configuration contains:

```text
SDS200 RTP audio
50000/udp -> 50000
```

Host networking is not required.

## Live scanner audio media source

This App packages, but never automatically installs or activates, a separate
versioned Home Assistant custom integration for
`media-source://sdsctl/live`. Installation, update, rollback, and removal are
explicit digest-confirmed operator actions, and Home Assistant Core must be
restarted separately. The full security, lifecycle, App DNS identity, exact MP3
format, playback, and automation contract is documented in
[`docs/home-assistant-live-audio.md`](../../docs/home-assistant-live-audio.md).

## Web UI

Use **Open Web UI** from the App page.

The dashboard runs through authenticated Home Assistant Ingress and supports:

- live scanner state;
- System, Department, Site, and Channel Hold/release;
- reconnect from Scanner connection plus previous/next channel controls;
- browser audio;
- daemon-owned recording;
- finalized recording inventory and playback; and
- the existing dashboard themes and API documentation.

On desktop, Scanner connection also contains daemon runtime, while Browser audio,
Capture, and Recent recordings each have their own lower dashboard panel.
Responsive layouts preserve those functional groups on narrower screens.

## Recordings

Recordings are stored under `/media/<recording_directory>` and persist across App
stop/start and container replacement. With the default option, the library is
`/media/sdsctl/recordings`.

The App maps Home Assistant media storage read/write so finalized WAV files and
their metadata sidecars can be managed through the Home Assistant media tree,
including Samba or SSH access when those services expose `/media`.

When upgrading from v0.20.0, startup migrates legacy files from
`/data/recordings` into the configured media library before launching the daemon.
The migration is recursive and refuses to overwrite a differing destination
file. Copied files are verified before their legacy sources are removed, so an
interrupted migration can be resumed safely.

Stopping a recording finalizes the WAV before it is added to the recent-recording
inventory.

## MQTT entities

The discovered SDS200 device contains twenty-four components.

State and diagnostic entities:

- Daemon State
- Scanner Connection
- Screen Kind
- System
- Department
- Site
- Channel
- Frequency
- Modulation
- Service Type
- Tone-Out Tone A
- Tone-Out Tone B
- Signal
- RSSI
- Audio
- Recording
- Recording Status

Screen Kind remains available and reports `unknown` when its normalized value is
missing, null, or empty. Site, Frequency, Modulation, Service Type, and configured
Tone-Out Tone A and
Tone B are unavailable when the current scanner mode does not supply a non-empty
value. Their fixed entities recover on the next applicable radio state. A zero
Tone-Out value remains unchanged in the entity and appears as `Detect` in the
bundled cards because zero configures tone-frequency detection.

Scanner controls:

- System Hold
- Department Hold
- Site Hold
- Channel Hold
- Previous Channel
- Next Channel
- Reconnect Scanner

The four Hold switches are non-optimistic and follow authoritative scanner state.
They are unavailable when the scanner is disconnected or the selected scope does
not currently expose a usable hold state.

Previous Channel and Next Channel are available only for current documented
trunked or conventional channel contexts with a valid SDS200 channel index.
Scanner disconnect and daemon-event resynchronization invalidate the adapter's
cached navigation context until authoritative state is restored.

All seven controls use dedicated QoS 0 non-retained MQTT command topics. The App
does not enable the generic daemon MQTT request-envelope command topic. Every
accepted Home Assistant action receives a fresh internal request ID and is
translated into the existing typed daemon control operation.

Scanner model and firmware are included in device metadata when available.

## Security

The App does not enable `host_network`.

The dashboard port is not published directly to the LAN; Home Assistant Ingress
is the browser access boundary. Only UDP `50000` is published for SDS200 RTP.

The daemon API, event, PCMU, recording-file, and waterfall services remain
private Unix-domain sockets inside the App container.

The current MQTT adapter does not configure TLS. Keep Home Assistant, the MQTT
broker, and the scanner on trusted networks. The seven Home Assistant control
topics are scanner-control inputs even though the generic daemon MQTT command
topic remains disabled, so broker publish permissions for the dedicated control
namespace should be limited to trusted Home Assistant publishers.

## Bundled Lovelace cards

The Home Assistant App installs three first-party SDS200 cards and one
declarative aggregate entry point:

```text
/homeassistant/www/sds200/sds200-card.js
/homeassistant/www/sds200/sds200-display-card.js
/homeassistant/www/sds200/sds200-waterfall-card.js
/homeassistant/www/sds200/sds200-cards.js
```

Home Assistant serves them to the frontend as:

```text
/local/sds200/sds200-card.js?v=beb1c6f22d62655caf4fc541a0cabfa4ed273b8fe22d6b3fe4324f5dc88ab9d8
/local/sds200/sds200-display-card.js?v=b2d47c2b7abd19a92b2ee61b6b3de00362366f8df828d7786c54ae35aa0ada72
/local/sds200/sds200-waterfall-card.js?v=d850fa81b04b1798dc7e7f947737525d3a58538f106202f66384eb4e028e62d8
/local/sds200/sds200-cards.js?v=dffbeaa294773419eab0ce8dec4a32317c421faaba5cd74373b46829b6095cad
```

The three byte-identical modules are independently packaged under
`sds200/themes/home-assistant/compact/` and
`sds200/themes/home-assistant/sds200-display/`, and
`sds200/themes/home-assistant/waterfall/`. The aggregate module is packaged at
`sds200/themes/home-assistant/sds200-cards.js`. Versioned manifests and one
validated immutable built-in registry drive their ordered installation while
preserving the same flat installed filenames and public URLs. The App does not
scan user-writable theme directories or discover third-party packages.

For a new installation, register only the complete aggregate URL in
**Settings > Dashboards > Resources** as a **JavaScript Module**. It imports all
three exact card modules. The three complete individual URLs remain supported
for selective registration and existing installations. The `v` query is the
exact module SHA-256, not a custom version string. To migrate, add and verify
the aggregate resource before explicitly removing the individual resource
records; the App never edits those records. HACS is not required. **SDS200 Scanner** remains the
read-only compact card. **SDS200 Display** adds Simple, Detail, Search/Close
Call, Weather, and Tone-Out layouts with Color, Black on White, and White on
Black palettes. **SDS200 Waterfall** adds a bounded responsive Canvas view of
the authenticated App's relative, uncalibrated waterfall stream. Its live scale
follows valid scanner-reported span changes; the graphical editor normalizes
60, 120, and 240-frame history choices, offers an explicit 15-, 30-, or
60-second alternative, and accepts Home Assistant-owned section layout metadata
without treating it as card configuration. An optional display-only frequency
pointer interpolates the valid live span without sending scanner commands. All
three graphical editors also offer the same 21 System web palettes as
independent, presentation-only per-card choices.

If the App creates Home Assistant's `www` directory for the first time, restart
Home Assistant Core once before registering the resource so `/local` becomes
available.

The automatic `/local` delivery requires the App to map Home Assistant's
configuration directory read/write. That filesystem permission is broader than
the four installed JavaScript files: the container can technically write elsewhere in the Home
Assistant configuration tree while it is running. The SDS200 installer
deliberately limits its own behavior to creating `www/sds200` when necessary and
creating or replacing only the four files listed above. It does not edit
Home Assistant YAML, `.storage`, dashboards, or resource registration.

Failure to install or update the optional cards is isolated from the scanner
runtime. The App logs a warning and continues starting the daemon and web
dashboard.

The compact and display cards subscribe only to Home Assistant's supported
`states` context. The read-only waterfall card uses Home Assistant's
authenticated frontend context to create and validate an App Ingress session;
it does not open a scanner transport, publish high-rate MQTT data, or accept
URLs, credentials, scanner addresses, or private Ingress identifiers in its
configuration.

After registering the resource, add **SDS200 Scanner** from the Home Assistant
card picker. The card uses Home Assistant's built-in graphical form editor.
Expand **SDS200 entities** and select the entities created by the SDS200 MQTT
Discovery device. Entity selectors are constrained to the expected `sensor` or
`binary_sensor` domain.

YAML configuration remains available as a fallback:

```yaml
type: custom:sds200-card
title: SDS200 Scanner
palette: theme  # Home Assistant theme or one System web palette
entities:
  scanner_connected: binary_sensor.REPLACE_ME
  system: sensor.REPLACE_ME
  department: sensor.REPLACE_ME
  site: sensor.REPLACE_ME
  channel: sensor.REPLACE_ME
  frequency: sensor.REPLACE_ME
  modulation: sensor.REPLACE_ME
  service_type: sensor.REPLACE_ME
  tone_out_tone_a: sensor.REPLACE_ME
  tone_out_tone_b: sensor.REPLACE_ME
  signal: sensor.REPLACE_ME
  rssi: sensor.REPLACE_ME
  audio_running: binary_sensor.REPLACE_ME
  recording_active: binary_sensor.REPLACE_ME
  recording_status: sensor.REPLACE_ME
  daemon_state: sensor.REPLACE_ME
```

Use the actual entity IDs created by the SDS200 MQTT Discovery device. The card
remains deliberately read-only. Scanner controls are separate standard Home
Assistant switch and button entities and do not add a transport to the card.

For the scanner-style presentation, add **SDS200 Display** from the picker,
select the same sixteen display entities, and choose a layout, palette, and fit
mode. To use automatic presentation, also select Screen Kind and choose the
Simple or Detail scanning fallback. The corresponding YAML begins with:

```yaml
type: custom:sds200-display-card
title: SDS200 Display
layout: auto
scan_layout: detail
palette: color
fit: viewport
entities:
  scanner_connected: binary_sensor.REPLACE_ME
  screen_kind: sensor.REPLACE_ME
  system: sensor.REPLACE_ME
  department: sensor.REPLACE_ME
  site: sensor.REPLACE_ME
  channel: sensor.REPLACE_ME
  frequency: sensor.REPLACE_ME
  modulation: sensor.REPLACE_ME
  service_type: sensor.REPLACE_ME
  tone_out_tone_a: sensor.REPLACE_ME
  tone_out_tone_b: sensor.REPLACE_ME
  signal: sensor.REPLACE_ME
  rssi: sensor.REPLACE_ME
  audio_running: binary_sensor.REPLACE_ME
  recording_active: binary_sensor.REPLACE_ME
  recording_status: sensor.REPLACE_ME
  daemon_state: sensor.REPLACE_ME
```

Use `auto`, `simple`, `detail`, `search`, `weather`, or `tone_out` for `layout`;
and `simple` or `detail` for `scan_layout` when Auto is active;
`color`, `black_on_white`, `white_on_black`, or one System web palette for
`palette`; and `card` or `viewport` for `fit`. Viewport fit retains a centered
4:3 surface and grows only
to the smaller width- or height-constrained dimension, without internal
scrolling. The original grid is inspired by the information hierarchy on pages
38–39 of the
[SDS200 Owner's Manual](https://www.uniden.info/download/ompdf/SDS200om.pdf)
without copying scanner artwork, branding, or fonts.

Auto maps Search and Close Call to the Search layout, Weather to Weather, and
Tone-Out to Tone-Out. Scanning, missing, unavailable, unknown, and future Screen
Kind values use `scan_layout`. Explicit layouts intentionally ignore Screen Kind.

The compact card includes optional Tone A and Tone B detail rows, and the
`tone_out` display layout presents both configured values. Numeric zero with an
optional `Hz` suffix is displayed as `Detect`; the entity retains the raw
scanner text and other nonempty values are shown unchanged.

For the live spectrum presentation, register the waterfall resource and add
**SDS200 Waterfall** from the picker. It needs no entity configuration. The
graphical editor exposes only bounded presentation options; equivalent YAML is:

```yaml
type: custom:sds200-waterfall-card
title: SDS200 Waterfall
density: standard
palette: theme
history_mode: duration
history_seconds: 30
# history: 120  # Used only with history_mode: frames
show_scale: true
show_telemetry: true
show_pointer: false
start_paused: false
grid_options:  # Optional Home Assistant Sections layout metadata
  rows: auto
  columns: full
```

Home Assistant owns `grid_options`; the card accepts this Sections-dashboard
layout metadata without treating it as a Waterfall transport or presentation
option. The Waterfall-specific choices remain bounded as shown above.

`density` is `compact`, `standard`, or `tall`; `palette` is `theme`, `cyan`,
`green`, `amber`, `monochrome`, or one System web palette. `history_mode` is
`frames` or `duration`. Frame mode uses `history` of 60, 120, or 240 frames;
duration mode uses `history_seconds` of 15, 30, or 60 seconds and is still
capped at 240 frames. Existing configurations without `history_mode` continue
to use frame mode; cards newly added through the picker begin with 30 seconds.
The card requires exactly one running SDS200 App discovered through Home
Assistant.
No running App is unavailable, and multiple running SDS200 Apps fail closed so
the card cannot silently select the wrong scanner owner. Enable **Show in
sidebar** for the intended running App so Home Assistant includes it in the
Ingress panel registry used for discovery. Opening `/app/<slug>` directly is
not a substitute for that panel setting.

Visible card instances hold independent demand leases over the daemon's single
shared scanner-side waterfall session. Hiding, removing, or disconnecting a card
aborts its stream; releasing the final live card stops waterfall demand. Pause
freezes only visual history, remains connected, and accumulates no hidden frame
backlog. Clear, stream-generation changes, and teardown remove retained history.
The optional pointer can be moved with pointer, touch, arrow keys, Home, and End;
Escape clears it. It reports interpolated MHz only while the scanner supplies a
valid lower and upper bound, never sends tuning or hold commands, and is not a
calibration claim. Authentication and transport loss use bounded reconnect
delays, and the card stores no authentication or Ingress material in
configuration or browser storage. The daemon refreshes typed Waterfall status
independently of GWF delivery, so the frequency scale follows scanner span
changes. A missed refresh retains the last complete scale without interrupting
live frames.

## Troubleshooting

### Repository does not appear in the App store

Refresh the browser after adding the repository. If the repository still does
not appear, inspect the Home Assistant Supervisor log for repository or App
configuration errors.

### Local App changes do not appear

Refresh the Home Assistant page and Local Apps repository so Supervisor rereads
the updated `config.yaml`.

### Browser audio stays on Buffering

Start a recording and inspect its packet counter. If recording also stays at
zero, verify UDP `50000` before troubleshooting Ingress.

If recording packets advance but live audio is silent, first verify saved
recording playback plus browser, tab, and system audio output. Live Browser Audio
uses Web Audio, while finalized recordings use the browser's native media
playback path, so a browser audio-service problem can affect only the live path.

### Recordings are not visible through Samba or SSH

The default recording library is `/media/sdsctl/recordings`, not
`/data/recordings`. A custom `recording_directory` is also relative to `/media`.
Confirm the Samba or SSH service being used exposes Home Assistant media storage.

### MQTT Discovery is missing

Confirm the Home Assistant MQTT integration is active and check the App Logs tab
for MQTT service errors.

### Logs

App stdout and stderr are available from the Home Assistant App Logs tab.
