# sdsctl

<p align="center">
  <img src="docs/assets/sdsctl-logo.svg" alt="sdsctl logo" width="720">
</p>

[![CI](https://github.com/stevenboyd78/sdsctl/actions/workflows/ci.yml/badge.svg)](https://github.com/stevenboyd78/sdsctl/actions/workflows/ci.yml)
![Python 3.11–3.14](https://img.shields.io/badge/python-3.11--3.14-blue)
![Development status: alpha](https://img.shields.io/badge/status-alpha-orange)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

`sdsctl` is a Python library and toolkit for controlling and monitoring
**Uniden SDS100, SDS150, and SDS200** scanners. It provides USB serial control
for all three models and native Ethernet control and network audio for the
SDS200.

Use it from the command line, a full-screen terminal interface, a responsive
web dashboard, Home Assistant, containers, MQTT, or a typed Python API.

> [!IMPORTANT]
> This project is alpha software. The public API may change before version 1.0.
> It is not affiliated with or endorsed by Uniden.

## Start here

Choose the installation that matches where you want to run `sdsctl`:

| Goal | Recommended path |
| --- | --- |
| Home Assistant OS | [Install the Home Assistant App](https://github.com/stevenboyd78/sdsctl/wiki/Home-Assistant) |
| One Home Assistant App with private-LAN Pi or browser displays | [Advanced Home Assistant access](https://github.com/stevenboyd78/sdsctl/wiki/Advanced-Home-Assistant) |
| Linux or Raspberry Pi terminal workstation | Install `sds200[tui,playback]` |
| Linux web or MQTT server | Install `sds200[web,mqtt]` |
| Every optional Python runtime interface | Install `sds200[all]` |
| Base CLI or Python library only | Install `sds200` |
| Docker or Podman | Follow the [container guide](https://github.com/stevenboyd78/sdsctl/wiki/Containers) |
| One Docker daemon with remote CLI or TUI clients | Follow the [remote container guide](docs/remote-container-deployment.md) |
| Development and contribution | Install `.[dev,all]` from a source checkout |

The [Installation wiki page](https://github.com/stevenboyd78/sdsctl/wiki/Installation)
explains prerequisites, virtual environments, Linux audio packages, Home
Assistant, containers, upgrades, and how to verify each target.

## Install the Python package

Python 3.11 or newer is required.

Install the base CLI and library:

```bash
python -m pip install sds200
```

Install all optional Python runtime interfaces:

```bash
python -m pip install "sds200[all]"
```

The `all` extra is exactly the union of `tui`, `web`, `mqtt`, and `playback`.
It does not install operating-system packages, Home Assistant, Docker or
Podman, audio servers, or FFmpeg. Linux local playback also needs a working
PortAudio runtime; Debian and Raspberry Pi OS users normally install
`libportaudio2`.

Published container users can pull the current exact release image:

```bash
docker pull theboyd78/sdsctl:0.28.1
```

`theboyd78/sdsctl:latest` follows the newest successfully published release.
The repository Compose files remain source-built; see the
[generic container deployment guide](docs/container-deployment.md).

## First connection

Connect a scanner by USB in serial mode, then run:

```bash
sdsctl discover
sdsctl info
sdsctl monitor
```

Stop the monitor with `Ctrl+C`.

For an SDS200 on an authorized local network:

```bash
sdsctl discover --network 192.168.1.0/24 --network-only
sdsctl --host SCANNER_IP info
```

Only probe networks you own or are authorized to scan. The
[First Connection guide](https://github.com/stevenboyd78/sdsctl/wiki/First-Connection)
covers USB permissions, multiple scanners, stable Linux device paths, SDS200
Ethernet, profiles, expected success, and common failures.

## What you can do

- Discover and control supported scanners over USB, plus SDS200 Ethernet
- Monitor structured scanner state and model-aware capabilities
- Use a responsive Textual terminal interface with semantic controls
- Run one daemon-owned scanner, PSI, audio, recording, event, and Waterfall
  runtime for local or explicitly authenticated private-network CLI/TUI clients
- Use a loopback web dashboard or explicit authenticated native-TLS LAN mode
- Publish bounded MQTT state, events, Home Assistant Discovery, and controls
- Play live SDS200 network audio and create finalized WAV recordings
- Run the Home Assistant App with Ingress, MQTT entities, first-party cards,
  browser audio, recordings, and an optional media-source integration
- Edit supported Favorites fields through exact review, backup, confirmation,
  execution, rollback, and readback boundaries
- Review and explicitly adopt supported RadioReference-assisted changes
- Capture and replay sessions for hardware-independent development
- Install and manage modular web, Home Assistant, and TUI themes

The [Using sdsctl guide](https://github.com/stevenboyd78/sdsctl/wiki/Using-sdsctl)
routes each task to its shortest workflow and detailed reference.

## Supported scanners

| Model | USB control | Native Ethernet control | RTSP/RTP audio |
| --- | --- | --- | --- |
| SDS100 | Yes | No | No |
| SDS150 | Yes | No | No |
| SDS200 | Yes | Yes | Yes |

SDS200 USB, Ethernet control, and network audio have been validated on physical
firmware 1.26.01. SDS100 core USB behavior has also been hardware-validated on
firmware 1.26.01. SDS150 support follows the shared SDS-series remote-command
specification and still needs physical-hardware validation. See
[Supported scanner models](docs/supported-models.md) for the exact capability
and validation matrix.

## Interfaces and guides

- [Installation and target selection](https://github.com/stevenboyd78/sdsctl/wiki/Installation)
- [First connection](https://github.com/stevenboyd78/sdsctl/wiki/First-Connection)
- [CLI, TUI, web, daemon, MQTT, controls, and themes](https://github.com/stevenboyd78/sdsctl/wiki/Using-sdsctl)
- [Audio and recordings](https://github.com/stevenboyd78/sdsctl/wiki/Audio-and-Recordings)
- [Home Assistant](https://github.com/stevenboyd78/sdsctl/wiki/Home-Assistant)
- [Advanced Home Assistant access](https://github.com/stevenboyd78/sdsctl/wiki/Advanced-Home-Assistant)
- [Favorites and RadioReference](https://github.com/stevenboyd78/sdsctl/wiki/Favorites-and-RadioReference)
- [Operations and diagnostics](https://github.com/stevenboyd78/sdsctl/wiki/Operations-and-Diagnostics)
- [Python API](https://github.com/stevenboyd78/sdsctl/wiki/Python-API)
- [Troubleshooting](https://github.com/stevenboyd78/sdsctl/wiki/Troubleshooting)

Advanced, version-controlled references remain in the repository:

- [Control transports](docs/transports.md)
- [Layered configuration](docs/configuration.md)
- [Daemon deployment](docs/daemon-deployment.md)
- [Authenticated remote daemon clients](docs/daemon-remote.md)
- [Managed Raspberry Pi TUI display](docs/managed-pi-display.md)
- [Remote daemon container deployment](docs/remote-container-deployment.md)
- [Web dashboard](docs/web-dashboard.md)
- [Network audio](docs/audio.md)
- [Home Assistant App](docs/home-assistant-app.md)
- [Advanced Home Assistant App access](docs/home-assistant-advanced-access.md)
- [Favorites Workspace](docs/favorites-workspace-editor.md)
- [Capability and field-parity audit](docs/capability-field-parity-audit.md)

## Security and safety

The SDS200 network-control protocol is unauthenticated and unencrypted. Keep it
on a trusted LAN or access it through a secured VPN. Do not expose UDP port
`50536` directly to the public Internet.

The default web service is loopback-only. Remote LAN access requires the
documented authenticated native-TLS mode. Home Assistant uses a separate
authenticated Ingress boundary. Reverse-proxy, public, and anonymous exposure
are not supported by implication.

Favorites data, RadioReference credentials and payloads, scanner addresses,
recordings, captures, Home Assistant capabilities, and diagnostics may be
private. Review and sanitize complete artifacts before sharing them.

This project is not a safety-critical or emergency-dispatch system. Do not rely
on it as the sole means of receiving urgent communications. Read
[SECURITY.md](SECURITY.md) for vulnerability reporting and
[the transport limits](docs/transports.md) before deployment.

## Project naming

The product, repository, and executable are named `sdsctl`. The compatible
Python distribution and import package remain named `sds200`. New Python code
should use `SDSScanner`; the historical `SDS200` class name remains an alias.

## Development

```bash
git clone https://github.com/stevenboyd78/sdsctl.git
cd sdsctl
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,all]"
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete development and test
workflow. Hardware-independent tests must not require a physical scanner.

## Project status

Version `0.28.1` is the current published release. It adds authenticated,
encrypted private-LAN daemon clients, remote CLI and TUI profiles, an isolated
container server deployment, and disabled-by-default advanced Home Assistant
access for multiple thin displays while preserving one scanner-owning daemon.
The patch release also corrects native HTTPS dashboard sign-in from ordinary
browsers without weakening its exact-origin protection.
See the
[latest GitHub Release](https://github.com/stevenboyd78/sdsctl/releases/latest),
[CHANGELOG.md](CHANGELOG.md), [ROADMAP.md](ROADMAP.md), and
[project vision](docs/project-vision.md) for released changes, ordered work,
and deferred product direction.

## Acknowledgments

See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

## License

MIT. See [LICENSE](LICENSE).
