# Installation

> [!IMPORTANT]
> Choose the target that matches how you want to run `sdsctl`. Home Assistant
> and container users do not need to install the Python package on the host.

## Choose an installation

| Goal | Recommended path |
| --- | --- |
| Run inside Home Assistant OS | Install the [Home Assistant App](Home-Assistant) |
| Use a terminal UI on Linux or Raspberry Pi OS | Install `sds200[tui,playback]` |
| Run a web dashboard or MQTT service on a Linux server | Install `sds200[web,mqtt]` |
| Install every optional Python runtime interface | Install `sds200[all]` |
| Use only the CLI or Python library | Install the base `sds200` package |
| Run a managed container | Follow the [container deployment guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/container-deployment.md) |
| Develop or contribute to `sdsctl` | Install the source checkout with `.[dev,all]` |

Continue with [First connection](First-Connection) after a Python or container
installation. Home Assistant users should finish the App setup first.

## Python requirements

- Python 3.11 or newer
- A Uniden SDS100, SDS150, or SDS200 scanner
- A USB serial connection for any supported model, or a trusted local network
  for native SDS200 Ethernet features
- A working PortAudio runtime for optional local playback

FFmpeg with `libmp3lame` is needed only for the optional Broadcastify adapter.

## Install from PyPI

Install the library and `sdsctl` command:

```bash
python -m pip install sds200
```

Install the optional Textual full-screen interface:

```bash
python -m pip install "sds200[tui]"
```

Install optional local audio playback:

```bash
python -m pip install "sds200[playback]"
```

Install the optional loopback web dashboard:

```bash
python -m pip install "sds200[web]"
```

Install optional daemon MQTT support:

```bash
python -m pip install "sds200[mqtt]"
```

Install every optional Python runtime interface:

```bash
python -m pip install "sds200[all]"
```

The `all` extra is exactly the union of `tui`, `web`, `mqtt`, and `playback`.
It does not install development tools, operating-system packages, Home
Assistant, Docker or Podman, an audio server, or FFmpeg. Individual extras can
still be combined, such as `sds200[tui,playback]` or `sds200[web,mqtt]`.

Verify the installation:

```bash
sdsctl --version
sdsctl --help
```

## Use a virtual environment

A virtual environment keeps the package and optional dependencies isolated:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "sds200[all]"
```

Activate the environment again before using its `sdsctl` command:

```bash
source .venv/bin/activate
```

## Install from source

```bash
git clone https://github.com/stevenboyd78/sdsctl.git
cd sdsctl

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,all]"
```

Run the project checks before contributing:

```bash
ruff check .
mypy src/sds200
pytest
python scripts/check_docs.py
git diff --check
```

## Linux USB permissions

Some Linux systems do not automatically grant the active user access to the
scanner serial port. The project includes an optional udev rule that uses
systemd-logind `uaccess`, retains a `dialout` fallback, and prevents
ModemManager from probing matching scanners.

Follow the canonical
[Linux udev guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/udev.md)
rather than making scanner devices globally writable.

Inspect stable device paths with:

```bash
ls -l /dev/serial/by-id/
```

## Linux playback requirement

The `playback` extra installs the Python binding, but Linux must also provide
the PortAudio runtime. On Debian or Raspberry Pi OS:

```bash
sudo apt update
sudo apt install libportaudio2
```

List the audio devices visible to `sdsctl`:

```bash
sdsctl audio-devices
```

If playback still fails, use [Troubleshooting](Troubleshooting#local-playback-fails).

## Optional FFmpeg support

Check that FFmpeg exposes the MP3 encoder required by the Broadcastify adapter:

```bash
ffmpeg -version | head -n 1
ffmpeg -hide_banner -encoders 2>/dev/null | grep -F libmp3lame
```

The encoder listing should contain `libmp3lame`.

## First connection

Continue with [First connection](First-Connection) for USB, SDS200 Ethernet,
multiple-scanner selection, expected success output, and safe discovery limits.

## Install the Home Assistant App

Home Assistant OS users should follow [Home Assistant](Home-Assistant). The
published App does not require `pip`, a source checkout, or a Local App under
`/addons`.

## Upgrade to v0.28.1

v0.28.1 publishes Milestones 32.1 through 32.6. It lets one scanner-owning
daemon serve explicitly authenticated private-LAN CLI, TUI, Raspberry Pi,
container, browser, and advanced Home Assistant clients. Each daemon client
receives an independent `observe` or `control` identity for its authorized
status, events, accepted-PCMU audio, Waterfall, and typed controls. Recording
content is not granted by either daemon-client scope; the same daemon continues
to own recordings without opening another scanner connection.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the base package with:

```bash
python -m pip install --upgrade "sds200==0.28.1"
sdsctl --version
```

Install or upgrade every optional Python runtime interface with:

```bash
python -m pip install --upgrade "sds200[all]==0.28.1"
python -m pip check
sdsctl --version
```

Linux local playback still needs a working PortAudio runtime. See
[Audio and recordings](Audio-and-Recordings) for the required operating-system
package and verification procedure.

For the generic container, prefer the exact release image:

```bash
docker pull theboyd78/sdsctl:0.28.1
```

`theboyd78/sdsctl:latest` follows the newest successfully published release,
but the exact version tag is recommended for controlled deployments. The
ordinary repository-root `compose.yaml` and `compose.usb.yaml` paths remain
source-built and local-only. The separate `compose.remote.yaml` topology is
documented in [Containers](Containers) and requires deliberate private-LAN TLS,
identity, address, port, and firewall configuration.

The Home Assistant App version tracks 0.28.1 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, persistent recordings, aggregate and individual card resource
paths, and independently versioned card modules. Upgrade the repository-managed
App only after the matching release images have published.

v0.28.1 also corrects the v0.28.0 native-dashboard login response so an
ordinary browser preserves the exact same-origin value required by the
dashboard's CSRF protection. It does not broaden listener exposure, client
scope, or the supported private-LAN boundary.

**Upgrading does not expose an advanced Home Assistant service.** The
authenticated daemon-client and native HTTPS dashboard mappings remain
disabled and `null` by default. Existing Ingress-only users do not need to
create a server identity, dashboard password, client credential, firewall rule,
or TCP mapping. The App continues to expose only authenticated Ingress and the
existing scanner-to-App RTP input unless an operator deliberately completes the
advanced configuration.

Operators who need one App-owned daemon to serve Raspberry Pi or workstation
clients should follow [Advanced Home Assistant](Advanced-Home-Assistant). That
guide explains Supervisor's host-wide mapping limitation, private-LAN firewall
direction, certificate trust, per-device enrollment, least-privilege scopes,
revocation, rotation, restart recovery, rollback, and cleanup. Do not expose
either service to the Internet, forward it through a router, use a wildcard
listener, share one client identity across displays, or copy the server private
key to a client.

The optional Home Assistant Core integration remains independently versioned
at 0.1.5 and does not need replacement, bridge-key rotation, reauthentication,
Core restart, or Core reload for this release.

## Upgrade to v0.27.0

v0.27.0 publishes Milestones 31.1 and 31.2. It adds bounded 15-, 30-, and
60-second Waterfall history modes to the authenticated web dashboard and the
first-party Home Assistant Waterfall card while retaining compatible 60-,
120-, and 240-frame configurations. It also adds an optional display-only
frequency pointer that interpolates the scanner-reported span for inspection.
The pointer does not tune, hold, search, change scanner span, or turn the
relative and uncalibrated Waterfall into an RF measurement instrument.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the base package with:

```bash
python -m pip install --upgrade "sds200==0.27.0"
sdsctl --version
```

Install or upgrade every optional Python runtime interface with:

```bash
python -m pip install --upgrade "sds200[all]==0.27.0"
python -m pip check
sdsctl --version
```

Linux local playback still needs a working PortAudio runtime. See
[Audio and recordings](Audio-and-Recordings) for the required operating-system
package and verification procedure.

For the generic container, prefer the exact release image:

```bash
docker pull theboyd78/sdsctl:0.27.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release,
but the exact version tag is recommended for controlled deployments.
Repository-root `compose.yaml` and `compose.usb.yaml` remain source-built and do
not switch automatically to the Docker Hub image.

The Home Assistant App version tracks 0.27.0 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, persistent recordings, aggregate and individual card resource
paths, and independently versioned card modules. Upgrade the repository-managed
App only after the matching release images have published. The optional Core
integration remains independently versioned at 0.1.5 and does not need
replacement for this release.

## Upgrade to v0.26.1

v0.26.1 publishes Milestones 30.1 and 30.2: the concise package README,
deployment- and task-oriented beginner documentation, and the `sds200[all]`
optional dependency extra. The `all` extra is the exact union of `tui`, `web`,
`mqtt`, and `playback`; it does not install development tools, operating-system
packages, Home Assistant, containers, audio servers, or external encoders.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the base package with:

```bash
python -m pip install --upgrade "sds200==0.26.1"
sdsctl --version
```

Install or upgrade every optional Python runtime interface with:

```bash
python -m pip install --upgrade "sds200[all]==0.26.1"
python -m pip check
sdsctl --version
```

Linux local playback still needs a working PortAudio runtime. See
[Audio and recordings](Audio-and-Recordings) for the required operating-system
package and verification procedure.

For the generic container, prefer the exact release image:

```bash
docker pull theboyd78/sdsctl:0.26.1
```

`theboyd78/sdsctl:latest` follows the newest successfully published release,
but the exact version tag is recommended for controlled deployments.
Repository-root `compose.yaml` and `compose.usb.yaml` remain source-built and do
not switch automatically to the Docker Hub image.

The Home Assistant App version tracks 0.26.1 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, persistent recordings, and independently versioned card modules.
Upgrade the repository-managed App only after the matching release images have
published. The optional Core integration remains independently versioned at
0.1.5 and does not need replacement for this patch release.

## Upgrade to v0.26.0

v0.26.0 freezes Milestones 29.4 through 29.7: one aggregate first-party Home
Assistant card resource with retained individual compatibility URLs, a phase-
stable 250 ms text-GWF schedule, bounded timing telemetry, independently
refreshed typed GST span data, Home Assistant editor and section-layout
compatibility, and the bounded GW2 research conclusion. The qualified text
`PWF`/`GWF` path remains authoritative; the tested SDS200 firmware 1.26.01 LAN
GW2 candidate returned `ERR\r` and established no binary framing or production
negotiation contract.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the Python package with:

```bash
python -m pip install --upgrade "sds200==0.26.0"
sdsctl --version
```

For the generic container, prefer the exact release image:

```bash
docker pull theboyd78/sdsctl:0.26.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release, but
the exact version tag is recommended for controlled deployments. Repository-root
`compose.yaml` and `compose.usb.yaml` remain source-built and do not switch
automatically to the Docker Hub image.

The Home Assistant App version tracks 0.26.0 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, persistent recordings, and independently versioned card modules.
Upgrade the repository-managed App only after the matching release images have
published. The optional Core integration remains independently versioned at
0.1.5; this release does not require replacing its artifact. Existing
digest-confirmed integration lifecycle, Core restart or reload,
reauthentication, and media-target URL reachability requirements remain
unchanged.

## Upgrade to v0.25.0

v0.25.0 freezes Milestones 29.1 through 29.3: the responsive first-party Home
Assistant Waterfall card, the standard `media-source://sdsctl/live` workflow,
the versioned optional Core integration, authenticated private App transports,
bounded waterfall and playback leases, System palette choices for the web and
all three cards, compact workspace controls, the Ingress-only integration
lifecycle pane, SHA-qualified card resources, and visible two-step destructive
confirmations. The daemon remains the only scanner, waterfall, and RTSP/RTP
owner. Home Assistant media players receive a bounded Home Assistant-owned URL,
not an Ingress identifier, App capability, scanner address, or public stream.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the Python package with:

```bash
python -m pip install --upgrade "sds200==0.25.0"
sdsctl --version
```

For the generic container, prefer the exact release image:

```bash
docker pull theboyd78/sdsctl:0.25.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release, but
the exact version tag is recommended for controlled deployments. Repository-root
`compose.yaml` and `compose.usb.yaml` remain source-built and do not switch
automatically to the Docker Hub image.

The Home Assistant App version tracks 0.25.0 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, persistent recordings, and bundled cards. Upgrade the repository-
managed App only after the matching release images have published. The optional
Core integration remains independently versioned at 0.1.5 and requires explicit
digest-confirmed install or update from the authenticated Ingress workspace,
followed by the documented Core restart or reload and any required
reauthentication. A target player must be able to reach Home Assistant's selected
internal or external URL for live media playback.

## Upgrade to v0.24.0

v0.24.0 freezes Milestones 28.1 through 28.4: one explicit credentialed
RadioReference refresh in the local Favorites Workspace editor, an exact
read-only preview, reviewed assisted field and record decisions, deterministic
write planning, and full-token-confirmed copied-tree or freshly qualified USB
execution with verified readback, conditional provenance publication, and exact
cross-store recovery. Synchronization remains local, explicit, user-initiated,
and one-shot; it is not automatic, scheduled, silent, or exposed through the web
dashboard, Home Assistant, or MQTT. The release also provides complete current
target context and Previous, Hold/Release, and Next controls for System,
Department, Site, and Channel in the responsive web Controls pane.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the Python package with:

```bash
python -m pip install --upgrade "sds200==0.24.0"
sdsctl --version
```

For the generic container, prefer the exact release image:

```bash
docker pull theboyd78/sdsctl:0.24.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release, but
the exact version tag is recommended for controlled deployments. Repository-root
`compose.yaml` and `compose.usb.yaml` remain source-built and do not switch
automatically to the Docker Hub image.

The Home Assistant App version tracks 0.24.0 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, and bundled card custom elements. Upgrade the repository-managed App
through Home Assistant after the matching release images have published. The
local RadioReference-assisted Favorites workflow is not an App or Ingress
feature.

## Upgrade to v0.23.0

v0.23.0 freezes Milestones 27.1 through 27.4: adaptive scanner-screen
presentation, the qualified private text-waterfall service, protocol and audio
lifecycle hardening, the responsive six-pane web workspace, the original
Pip-Boy-inspired web theme, managed-theme source-snapshot hardening, and the
authenticated relative spectrum and rolling-waterfall pane. Waterfall values are
explicitly uncalibrated, the web pane creates demand only while visible, and no
Home Assistant waterfall card, scanner tuning, public waterfall transport, or
persistent waterfall history is included.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the Python package with:

```bash
python -m pip install --upgrade "sds200==0.23.0"
sdsctl --version
```

For the generic container, prefer the exact release image:

```bash
docker pull theboyd78/sdsctl:0.23.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release, but
the exact version tag is recommended for controlled deployments. Repository-root
`compose.yaml` and `compose.usb.yaml` remain source-built and do not switch
automatically to the Docker Hub image.

The Home Assistant App version tracks 0.23.0 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, and bundled card custom elements. Upgrade the repository-managed App
through Home Assistant after the matching release images have published.

## Upgrade to v0.22.0

v0.22.0 freezes Milestones 26.1 through 26.16: authenticated direct-TLS LAN
dashboard access, the capability and field-parity audit, the local interactive
Favorites Workspace editor, complete web snapshot presentation, bounded battery
lifecycle handling, expanded Home Assistant state and responsive scanner-style
cards, exact semantic controls, Tone-Out parity, and modular built-in and
managed themes for web, Home Assistant, and terminal interfaces.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the Python package with:

```bash
python -m pip install --upgrade "sds200==0.22.0"
sdsctl --version
```

For the generic container, prefer the immutable exact release image:

```bash
docker pull theboyd78/sdsctl:0.22.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release, but
the exact version tag is recommended for reproducible deployments.
Repository-root `compose.yaml` and `compose.usb.yaml` remain source-built and do
not switch automatically to the Docker Hub image.

The Home Assistant App version tracks 0.22.0 while preserving its
compatibility-sensitive `sds200` name, slug, GHCR image identity, MQTT entity
identities, and bundled card custom elements. Upgrade the repository-managed App
through Home Assistant after the matching release images have published.

## Upgrade to v0.21.0

v0.21.0 is the first release after v0.20.2 and freezes the completed Milestones
21 through 25 release scope: Favorites Workspace and verified storage workflows,
assisted RadioReference synchronization, advanced protocol and analysis
foundations, the user-facing `sdsctl` repository/product identity, and the
generic Docker/Compose/Podman deployment foundation.

The compatibility-sensitive Python distribution and import package remain
`sds200`, while the command remains `sdsctl`. Upgrade the Python package with:

```bash
python -m pip install --upgrade "sds200==0.21.0"
sdsctl --version
```

The v0.21.0 release is also the first release using the generic Docker Hub
publication path. After the genuine matching release tag has published
successfully, prefer the immutable exact image:

```bash
docker pull theboyd78/sdsctl:0.21.0
```

`theboyd78/sdsctl:latest` follows the newest successfully published release, but
the exact version tag is recommended for reproducible deployments.
Repository-root `compose.yaml` and `compose.usb.yaml` remain source-built and do
not switch automatically to the Docker Hub image.

The Home Assistant App version tracks 0.21.0 for the release while preserving
its compatibility-sensitive `sds200` name, slug, and GHCR image identity. The
single-owner daemon, MQTT Discovery/control, Ingress, browser audio, and
recording architecture remain unchanged by release preparation.

## Upgrade to v0.20.2

v0.20.2 publishes the three Home Assistant slices completed after v0.20.1 while
preserving the existing single-owner daemon architecture.

The Home Assistant Configuration page now has user-facing names and descriptions
for `scanner_host`, `mqtt_topic_prefix`, and `recording_directory`. The recording
directory remains media-relative and the default still resolves to:

```text
/media/sdsctl/recordings
```

The App installs the unchanged compact, responsive display, and authenticated
waterfall card modules plus one aggregate entry point. For a new installation,
register
`/local/sds200/sds200-cards.js?v=dbbb246abbf82fff9040c2d3a4ccb7f94ef634bf56795c0c356737bb5faac37f`
once under **Settings > Dashboards > Resources** as a JavaScript Module. The
three complete digest-qualified individual URLs remain supported for selective
registration and existing installations. Add and verify the aggregate before
explicitly removing old individual resource records; the App never edits those
records. If the App had to
create Home Assistant's `www` directory for the first time, restart Home
Assistant Core once before registering the `/local` resources. Add **SDS200
Scanner**, **SDS200 Display**, or **SDS200 Waterfall** from the picker and use
its graphical editor to
choose the SDS200 Discovery state entities. The display card also selects one of
five layouts, three palettes, and Card or viewport-bounded 4:3 fit.

The waterfall card requires no entities, URL, credentials, App slug, private
Ingress identifier, or scanner address. It discovers exactly one running SDS200
App through Home Assistant, uses authenticated App Ingress, and renders bounded
relative, uncalibrated spectrum history. Stop or uninstall obsolete Local Apps
if the card reports that multiple SDS200 Apps are running.

The MQTT-discovered SDS200 device now contains twenty-one components: fourteen
state/diagnostic components, including optional Site, Frequency, Modulation, and
Service Type sensors, plus System, Department, Site, and Channel Hold switches
and Previous Channel, Next Channel, and Reconnect Scanner buttons. The seven
controls use a dedicated bounded Home Assistant adapter; the App still does not
enable the generic daemon MQTT request-envelope command topic.

Existing v0.20.1 recordings and configuration remain in the same Home Assistant
media/configuration locations. Back up important configuration and recordings
before any upgrade.

See the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md)
for the Lovelace registration, entity, storage, networking, control, and security
contracts.

## Upgrade to v0.20.1

v0.20.1 is a corrective Home Assistant App release. It keeps the distribution
and Python import package named `sds200`, the executable named `sdsctl`, and the
existing single-owner daemon architecture.

Home Assistant recordings now use writable media storage instead of the
App-private legacy recording directory. The default `recording_directory` value
`sdsctl/recordings` resolves to:

```text
/media/sdsctl/recordings
```

When upgrading a v0.20.0 Home Assistant App installation, startup migrates files
from `/data/recordings` into the configured media library before starting the
daemon. Nested paths and metadata sidecars are preserved. Migration preflights
destination conflicts, never overwrites a differing file, verifies copied file
contents before removing the legacy source, and can resume when an identical
destination already exists.

The dashboard also separates active Capture from Recent recordings, moves
Reconnect scanner into Scanner connection, groups daemon runtime with scanner
connection, and gives the finalized recording library its own responsive panel.

Existing standalone scanner profiles, remote-audio profiles, daemon
configuration, and other non-App data are not moved by this Home Assistant
recording migration. Back up important configuration and recordings before any
upgrade.

See the canonical
[Home Assistant App guide](https://github.com/stevenboyd78/sdsctl/blob/main/docs/home-assistant-app.md)
for exact storage, migration, networking, and security behavior.

## Run the SDS200 daemon

The foreground daemon is intended for process-manager ownership. It exposes
private local API, event, PCMU, and finalized-recording sockets and can activate
saved playback, recording, and remote-profile destinations.

```bash
sdsctl --log-level INFO --host SCANNER_IP daemon
sdsctl daemon-client status
sdsctl tui --daemon-client
```

Standalone scanner commands and the standalone TUI remain the default. Daemon
client mode is explicit.

To run the loopback web dashboard in another terminal:

```bash
sdsctl web
```

Open `http://127.0.0.1:8000/` locally. The web service remains a daemon client
and does not open scanner hardware directly.

## Next steps

- Complete [First connection](First-Connection).
- Choose a CLI, TUI, web, daemon, MQTT, control, or theme workflow in
  [Using sdsctl](Using-sdsctl).
- Configure [audio and recordings](Audio-and-Recordings).
- Review [operations and diagnostics](Operations-and-Diagnostics) before
  installing a persistent service.
- Open [Troubleshooting](Troubleshooting) when discovery or startup fails.
