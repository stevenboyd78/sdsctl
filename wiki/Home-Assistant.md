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

## Dashboard cards

The App packages the compact scanner, scanner-display, and Waterfall cards.
Register the single aggregate JavaScript Module shown by the App's current
documentation or lifecycle screen. The URL is digest-qualified and can change
when a released card artifact changes, so copy the exact current value rather
than retyping an older example.

The individual compatibility resources remain available for selective or
existing installations. Register either the aggregate resource or the needed
individual resources; duplicate registration is unnecessary.

The Waterfall card offers compatible 60-, 120-, and 240-frame history plus an
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
