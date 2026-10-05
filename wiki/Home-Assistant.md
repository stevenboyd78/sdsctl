# Home Assistant

The published Home Assistant App is the recommended installation for Home
Assistant OS. It packages the daemon and responsive dashboard, uses
authenticated Ingress, publishes MQTT Discovery, and stores recordings in Home
Assistant media storage. It supports an SDS200 reachable on the local network.

## Prerequisites

- Home Assistant OS with Apps and the MQTT service available
- An SDS200 with a stable local hostname or address
- Host UDP port `50000` available for the scanner's inbound RTP audio

The App does not support SDS100 or SDS150 USB passthrough. Do not install the
Python package or copy a Local App for a normal published installation.

## Install the App

1. Open **Settings > Apps > App store**.
2. Open the top-right menu and choose **Repositories**.
3. Add `https://github.com/stevenboyd78/sdsctl`.
4. Open **sds200** from that repository and choose **Install**.
5. Set `scanner_host` to the SDS200 LAN hostname or address.
6. Leave `recording_directory` at `sdsctl/recordings` unless another directory
   below `/media` is intentional.
7. Start the App and open **Web UI**.

Success means the Ingress dashboard reports **Connected**, scanner state
updates, and the App log has no repeated restart or ownership failure.

## v0.31.0 profile and bounded Menu options

v0.31.0 adds two catalog fields only in its matching image/schema release:

- `scanner_display_config` defaults empty. Set it only to a deliberately
  prepared administrator deployment TOML, normally
  `/data/scanner-display-deployment.toml`. Startup never searches, initializes,
  imports, repairs or changes profile state, and this is not automatic
  Favorites List synchronization.
- `qualified_sds200_menu_control_enabled` defaults off. When explicitly enabled,
  it permits only one physically qualified SDS200 `Version 1.26.01` Menu press
  after fresh `Trunk Scan` / `trunk_scan` evidence. No other key, held gesture,
  sequence, retry or post-acknowledgement state claim is enabled.

Save the existing App options, Network mappings and dashboard resource URL
before upgrading. Do not add these fields to a v0.30.0 or older installed App;
its strict parser expects the older seven-field contract. After the normal
repository-managed v0.31.0 update, first verify startup with the new options at
their defaults and confirm persistent recordings. Only then opt in to a prepared
profile deployment or the exact Menu boundary.

For rollback, turn both new options off, preserve profile state and recordings,
restore the prior reviewed App through Home Assistant's normal backup/repository
workflow, and restore only that release's matching options and aggregate card
resource URL. Do not delete credentials, recordings, profile manifests or
accepted state to make rollback succeed.

## Dashboard cards

The v0.31.0 App packages the compact scanner, scanner-display, Waterfall, and
Mimic-SDS cards. Mimic-SDS is an additional read-only reconstruction of accepted
display-profile layout and live PSI data; it does not replace the other cards,
upload a profile or send scanner commands.
Register the single aggregate JavaScript Module shown by the App's current
documentation or lifecycle screen. The URL is digest-qualified and can change
when a released card artifact changes, so copy the exact current value rather
than retyping an older example.

The individual compatibility resources remain available for selective or
existing installations. Register either the aggregate resource or the needed
individual resources; duplicate registration is unnecessary.

Waterfall and Mimic-SDS use one shared authenticated Ingress session manager and
must be updated together through the matching aggregate resource. The Waterfall
card offers compatible 60-, 120-, and 240-frame history plus an
explicit 15-, 30-, or 60-second mode, with 240 frames as the memory cap in every
case. Its optional frequency pointer works with pointer, touch, or keyboard input
and is a display aid only; it does not tune, hold, search, or change scanner span.

## Browser audio and recordings

Browser audio starts only after an operator action. Recordings are finalized by
the daemon and then become playable and downloadable from the dashboard and
Home Assistant media storage. A browser closing must not interrupt another
client or an active daemon-owned recording.

## Optional Core integration

The separately versioned `sdsctl` Core integration adds one browsable
`media-source://sdsctl/live` item. It does not create an output media-player
entity. The App packages the artifact but never installs, activates, removes,
or restarts Home Assistant Core automatically.

Install, update, rollback, bridge-key rotation, removal, and Core restart are
explicit operator actions. Follow the
[Home Assistant live-audio guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-live-audio.md)
and keep capability material out of screenshots, logs, messages, and source
control.

## Advanced private-LAN clients and displays

The App can optionally expose two independently authenticated private-LAN
services: the encrypted daemon-client protocol for CLI/TUI devices, and a native
HTTPS dashboard for an ordinary browser or kiosk. Both are disabled by default;
neither replaces the recommended authenticated Ingress dashboard.

This supports one Home Assistant-hosted scanner daemon feeding multiple Pi or
workstation displays without creating another scanner, PSI, audio, recording, or
Waterfall owner. Home Assistant publishes enabled App ports host-wide, so this
mode requires an intentional trusted-LAN firewall boundary and must never be
port-forwarded to the Internet.

Follow [Advanced Home Assistant access](Advanced-Home-Assistant) for the
beginner-oriented two-restart setup, one-time credential download, Raspberry Pi
TUI commands, native browser setup, per-client scopes, rotation, revocation, and
safe disable workflow.

## Update or remove

Use the normal App update offered by Home Assistant. Before a controlled
upgrade, confirm that persistent recordings are below the configured `/media`
directory. Removing the App does not automatically remove separately installed
Core integration files or a configured integration entry; clean those through
their documented lifecycle when applicable.

The `/addons` Local App workflow is only for development and physical release
validation. A Local App and the published App must not compete for the scanner.

## Detailed reference

Read the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md)
for architecture, configuration, networking, Ingress, MQTT entities, cards,
local development, upgrades, persistent recordings, acceptance evidence, and
security boundaries. Use [Advanced Home Assistant access](Advanced-Home-Assistant)
for private-LAN clients and displays, and use
[Troubleshooting](Troubleshooting#home-assistant-app-problems)
when the repository, App, audio, MQTT entities, cards, or recordings fail.
